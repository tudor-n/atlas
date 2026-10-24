//! Starts and restarts the hub (design §8). It only ever stops a hub it started itself.
//!
//! In runtime mode (plan decision P2) every start first runs, outside the supervisor's lock (see
//! `Prep`): `claude-sync` once per shell start, then the update step, both with the current
//! slot's python; then the hub starts from the slot `current.json` names. The hub, the step and
//! claude-sync all get `ATLAS_STATE_DIR` = `desktop.json`'s state folder, which also holds
//! hub.log, so the logs and the hub's view of the runtime never split from the slots (ruling D12).

use crate::claude_watch::{ClaudeSync, ClaudeWatch};
use crate::config::{DesktopConfig, Mode};
use std::fs::{File, OpenOptions};
use std::io::Write;
use std::net::{SocketAddr, TcpStream};
use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

/// `CREATE_NO_WINDOW`: the child gets no console window.
pub const CREATE_NO_WINDOW: u32 = 0x0800_0000;
/// The attempt counter resets after this much uptime of a hub we started.
pub const HEALTHY_UPTIME: Duration = Duration::from_secs(300);
/// The hub's exit code when its data folder is usable again (P8): start it again at once.
pub const EXIT_RESTART: i32 = 75;
/// How long a hub gets to exit after SIGTERM before it is killed (unix).
/// Enough for the hub's own shutdown: 2 s for running requests, then up to 2 s each to stop an
/// ask stream's and the filing worker's claude process groups.
pub const STOP_GRACE: Duration = Duration::from_secs(10);
/// The update step's limit. It keeps to its own 75 s budget (ruling D9); this is the backstop.
pub const UPDATE_STEP_LIMIT: Duration = Duration::from_secs(90);
/// `claude-sync`'s limit, at the first start and in the shell's watch (`claude_watch`).
pub const CLAUDE_SYNC_LIMIT: Duration = Duration::from_secs(60);
/// Unix: how long a timed-out step or claude-sync gets to exit after SIGTERM to its process group
/// (the update step first kills its running git or uv, which run in sessions of their own) before
/// the group gets SIGKILL.
pub const TERM_GRACE: Duration = Duration::from_secs(3);
/// How long a killed process tree gets to be reaped before the supervisor moves on.
const KILL_WAIT: Duration = Duration::from_secs(5);
/// The digest of this shell's own source, baked in by build.rs (empty when it couldn't be
/// computed, which never counts as a difference).
pub const SHELL_SOURCE: &str = env!("ATLAS_SHELL_SOURCE");

/// What the tray says besides "ATLAS".
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Notice {
    /// Runtime mode without a usable slot (`runtime init` never ran): nothing is started.
    NoSlot,
    /// The live copy's shell source differs from this shell's: a rebuild is due.
    ShellUpdate,
}

impl Notice {
    pub fn text(self) -> &'static str {
        match self {
            Notice::NoSlot => "connect only: no runtime slot (run runtime init: see app/desktop/README.md)",
            Notice::ShellUpdate => "widget update available (rebuild: see app/desktop/README.md)",
        }
    }
}

/// A slot's venv python.
pub fn venv_python(slot: &Path) -> PathBuf {
    if cfg!(windows) {
        slot.join("venv").join("Scripts").join("python.exe")
    } else {
        slot.join("venv").join("bin").join("python")
    }
}

fn read_json(path: &Path) -> Option<serde_json::Value> {
    let text = std::fs::read_to_string(path).ok()?;
    serde_json::from_str::<serde_json::Value>(&text).ok().filter(|v| v.is_object())
}

/// The current runtime slot as `(venv python, app folder)`, or None when there is no usable one.
/// As `current_slot` in update_step.py: `<state>/runtime/current.json` names slot a or b and a
/// commit, that slot's `slot.json` holds the same commit (a wiped or half-built slot never
/// counts), and the slot has its `app/` and its venv python.
pub fn current_slot(state: &Path) -> Option<(PathBuf, PathBuf)> {
    let runtime = state.join("runtime");
    let cur = read_json(&runtime.join("current.json"))?;
    let slot = cur.get("slot").and_then(|s| s.as_str()).filter(|s| matches!(*s, "a" | "b"))?;
    let commit = cur.get("commit").and_then(|c| c.as_str())?;
    let dir = runtime.join(format!("slot-{slot}"));
    let record = read_json(&dir.join("slot.json"))?;
    let (app, python) = (dir.join("app"), venv_python(&dir));
    (record.get("commit").and_then(|c| c.as_str()) == Some(commit) && app.is_dir() && python.exists())
        .then_some((python, app))
}

/// Whether `<state>/update.json` names another shell source than this shell's own.
pub fn shell_update_in(state: &Path, own: &str) -> bool {
    let live = read_json(&state.join("update.json"))
        .and_then(|u| u.get("shell_source").and_then(|s| s.as_str()).map(str::to_owned));
    matches!(live, Some(live) if !live.is_empty() && !own.is_empty() && live != own)
}

/// Record this shell's source digest in `<state>/shell.json` (atomically), so the hub can tell
/// Settings whether the live copy holds a newer shell.
fn write_shell_json(state: &Path, own: &str) -> std::io::Result<()> {
    std::fs::create_dir_all(state)?;
    let value = if own.is_empty() { serde_json::Value::Null } else { own.into() };
    let body = serde_json::json!({ "shell_source": value }).to_string() + "\n";
    let (target, tmp) = (state.join("shell.json"), state.join(format!("shell.json.{}.tmp", std::process::id())));
    std::fs::write(&tmp, body)?;
    std::fs::rename(&tmp, &target).inspect_err(|_| {
        let _ = std::fs::remove_file(&tmp);
    })
}

/// Append one shell line to hub.log.
fn append_log(log: &Path, msg: &str) {
    if let Some(dir) = log.parent() {
        let _ = std::fs::create_dir_all(dir);
    }
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).open(log) {
        let secs = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
        let _ = writeln!(f, "[atlas-desktop t={secs}] {msg}");
    }
}

/// Whether our child `pid` has exited, without reaping it (unix): until it is reaped its pid, and
/// so its process group's id, can't be given to another process.
#[cfg(unix)]
fn exited_unreaped(pid: libc::pid_t) -> bool {
    let Ok(id) = libc::id_t::try_from(pid) else { return true };
    let mut info: libc::siginfo_t = unsafe { std::mem::zeroed() };
    let flags = libc::WEXITED | libc::WNOHANG | libc::WNOWAIT;
    // With WNOHANG, si_pid stays 0 while the child still runs.
    unsafe { libc::waitid(libc::P_PID, id, &mut info, flags) != 0 || info.si_pid() != 0 }
}

/// Kill `child` and everything in its tree, then reap it, waiting at most `KILL_WAIT`.
/// - Unix (it was started as a group leader): SIGTERM to its process group, up to `grace` for it
///   to exit, then SIGKILL to the group. The update step needs the SIGTERM: its git and uv run in
///   sessions of their own, out of the group's reach, and its handler kills the running one's
///   tree before it exits (`on_sigterm` in update_step.py).
/// - Windows: `taskkill /T /F`, which follows the tree itself.
fn kill_tree(child: &mut Child, grace: Duration) {
    #[cfg(unix)]
    if let Ok(pid) = libc::pid_t::try_from(child.id()) {
        // Never reaped before the SIGKILL (exited_unreaped only peeks), so no other group can
        // have this id.
        unsafe { libc::killpg(pid, libc::SIGTERM) };
        let deadline = Instant::now() + grace;
        while !exited_unreaped(pid) && Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(20));
        }
        unsafe { libc::killpg(pid, libc::SIGKILL) };
    }
    #[cfg(not(unix))]
    let _ = grace;
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        let root = std::env::var_os("SystemRoot").unwrap_or_else(|| "C:\\Windows".into());
        let _ = Command::new(Path::new(&root).join("System32").join("taskkill.exe"))
            .args(["/T", "/F", "/PID"])
            .arg(child.id().to_string())
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .creation_flags(CREATE_NO_WINDOW)
            .status();
    }
    let _ = child.kill();
    let deadline = Instant::now() + KILL_WAIT;
    while matches!(child.try_wait(), Ok(None)) && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(20));
    }
}

/// Run `cmd` with stdin closed and stdout and stderr appended to `log`, for at most `limit`. On
/// timeout its whole process tree is killed (`kill_tree`, with `grace`). It never opens a window
/// (Windows: CREATE_NO_WINDOW, Ruling 13). Returns how it ended, for the log.
fn run_bounded(mut cmd: Command, log: &Path, limit: Duration, grace: Duration) -> String {
    let out = match OpenOptions::new().create(true).append(true).open(log) {
        Ok(f) => f,
        Err(e) => return format!("not run: cannot open {}: {e}", log.display()),
    };
    let err = match out.try_clone() {
        Ok(f) => f,
        Err(e) => return format!("not run: cannot open the log twice: {e}"),
    };
    cmd.stdin(Stdio::null()).stdout(out).stderr(err);
    crate::child_env(&mut cmd);
    // Its own process group, so a timeout can kill everything it started that stayed in it.
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        cmd.process_group(0);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    let started = Instant::now();
    let mut child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => return format!("could not start: {e}"),
    };
    loop {
        match child.try_wait() {
            Ok(Some(status)) => return format!("{status} after {:.1} s", started.elapsed().as_secs_f32()),
            Ok(None) if started.elapsed() < limit => std::thread::sleep(Duration::from_millis(50)),
            Ok(None) => {
                kill_tree(&mut child, grace);
                return format!("timed out after {} s: killed with its process tree", limit.as_secs_f32());
            }
            Err(e) => {
                kill_tree(&mut child, grace);
                return format!("unknown status ({e}): killed with its process tree");
            }
        }
    }
}

/// The time limits of a runtime start's slow part and of the claude-sync watch (tests shorten them).
#[derive(Debug, Clone, Copy)]
pub struct Limits {
    pub update_step: Duration,
    pub claude_sync: Duration,
    /// Unix: from SIGTERM to SIGKILL when one of them is killed at its limit.
    pub term_grace: Duration,
}

/// Run `<slot python> -m atlas claude-sync [--settings-only]` in the slot's `app/`, bounded, and
/// log the command and how it ended (`why` says what asked for it). The exit code is only logged:
/// a slot without the command, or a missing shared drive, just fails here.
pub(crate) fn run_claude_sync(python: &Path, app: &Path, state: &Path, log: &Path, settings_only: bool, why: &str,
                              limits: Limits) {
    let mut sync = Command::new(python);
    sync.args(["-m", "atlas", "claude-sync"])
        .args(settings_only.then_some("--settings-only"))
        .current_dir(app)
        .env("ATLAS_STATE_DIR", state)
        .env("PYTHONUNBUFFERED", "1")
        .env("PYTHONIOENCODING", "utf-8");
    let flag = if settings_only { " --settings-only" } else { "" };
    append_log(log, &format!("claude-sync ({why}): {} -m atlas claude-sync{flag} (in {})", python.display(),
                             app.display()));
    let how = run_bounded(sync, log, limits.claude_sync, limits.term_grace);
    append_log(log, &format!("claude-sync ({why}): {how}"));
}

/// The slow part of a runtime start: `claude-sync` (first start only) and the update step, with
/// the current slot's python. It can take up to 150 s, so the watch thread runs it without holding
/// the supervisor's lock: the tray's Restart and Quit never wait for it. `Supervisor::start_prepared`
/// then starts the hub from whatever slot the step left current.
#[derive(Debug)]
pub struct Prep {
    python: PathBuf,
    app: PathBuf,
    live: PathBuf,
    state: PathBuf,
    log: PathBuf,
    claude_sync: bool,
    limits: Limits,
    /// Shared with the claude-sync watch: one run at a time, and the watch's mtime and cadence.
    sync: Arc<ClaudeSync>,
    /// The Claude Code settings file the watch polls.
    settings: Option<PathBuf>,
}

impl Prep {
    pub fn run(&self) {
        let log = |msg: &str| append_log(&self.log, msg);
        if self.claude_sync {
            // After a watch run that is still going, if any. The hub starts whatever its outcome.
            let mtime = || crate::claude_watch::mtime(self.settings.as_deref());
            self.sync.run_now(Instant::now, mtime, || {
                run_claude_sync(&self.python, &self.app, &self.state, &self.log, false, "at start", self.limits)
            });
        }
        // Run as a file, isolated (-I -S), like the hook client: it imports nothing from atlas.
        // Its one JSON line goes straight to hub.log.
        let script = self.app.join("atlas").join("update_step.py");
        let mut step = Command::new(&self.python);
        step.args(["-I", "-S"])
            .arg(&script)
            .arg("--live")
            .arg(&self.live)
            .arg("--state")
            .arg(&self.state)
            .current_dir(&self.state)
            .env("ATLAS_STATE_DIR", &self.state);
        log(&format!("update step: {} -I -S {} --live {} --state {}", self.python.display(), script.display(),
                     self.live.display(), self.state.display()));
        let how = run_bounded(step, &self.log, self.limits.update_step, self.limits.term_grace);
        log(&format!("update step: {how}"));
    }
}

/// Delay before restart attempt `attempt`: 1, 2, 4, 8, 16, 32 s, then 60 s.
pub fn backoff(attempt: u32) -> Duration {
    Duration::from_secs(if attempt >= 6 { 60 } else { 1u64 << attempt })
}

/// The hub's arguments: `-m atlas --root <root> --port <port>`. The port is passed explicitly so
/// the hub listens where the shell looks, even if the hub's own `port` setting has changed since
/// `desktop-config` ran.
pub fn hub_args(root: &Path, port: u16) -> Vec<OsString> {
    vec!["-m".into(), "atlas".into(), "--root".into(), root.as_os_str().to_owned(), "--port".into(),
         port.to_string().into()]
}

/// Does anything accept a TCP connection on `127.0.0.1:port` within 500 ms?
pub fn hub_up(port: u16) -> bool {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    TcpStream::connect_timeout(&addr, Duration::from_millis(500)).is_ok()
}

pub struct Supervisor {
    cfg: DesktopConfig,
    /// The per-OS state folder, `desktop.json`'s in runtime mode. `hub.log` lives there, not under
    /// the root: a missing drive can still be diagnosed, and the shell never creates anything on
    /// the root.
    state: PathBuf,
    child: Option<Child>,
    started_at: Option<Instant>,
    attempt: u32,
    not_before: Option<Instant>,
    stopped: bool,
    last_up: bool,
    stop_grace: Duration,
    /// Runtime mode: the start's slow part's time limits.
    limits: Limits,
    /// Runtime mode: `claude-sync` has been run (once per shell start).
    synced: bool,
    /// Runtime mode: no usable slot at the last attempt to start (connect only).
    no_slot: bool,
    /// Runtime mode: the live copy's shell source differs from this shell's (`update.json`).
    shell_update: bool,
    /// claude-sync runs (the first start's and the watch's): one at a time.
    sync: Arc<ClaudeSync>,
    /// The Claude Code settings file the claude-sync watch polls (None: no home folder).
    settings: Option<PathBuf>,
}

impl Supervisor {
    /// `state` is the shell's own state folder (P10). In runtime mode `desktop.json`'s takes its
    /// place, so hub.log sits beside the slots and the `update.json` the hub reads.
    pub fn new(cfg: DesktopConfig, state: PathBuf) -> Self {
        let state = match cfg.mode() {
            Mode::Runtime { state, .. } => state,
            _ => state,
        };
        let mut s = Supervisor {
            cfg,
            state,
            child: None,
            started_at: None,
            attempt: 0,
            not_before: None,
            stopped: false,
            last_up: false,
            stop_grace: STOP_GRACE,
            limits: Limits { update_step: UPDATE_STEP_LIMIT, claude_sync: CLAUDE_SYNC_LIMIT, term_grace: TERM_GRACE },
            synced: false,
            no_slot: false,
            shell_update: false,
            sync: Arc::new(ClaudeSync::new(Instant::now())),
            settings: crate::config::claude_settings_path(),
        };
        if let Mode::Runtime { state, .. } = s.cfg.mode() {
            s.shell_update = shell_update_in(&state, SHELL_SOURCE);
        }
        s
    }

    /// Runtime mode: record this shell's source digest in `<state>/shell.json`. The app's setup
    /// calls it, after the single-instance check, so a second launch (perhaps of another build)
    /// never overwrites the running shell's record.
    pub fn record_shell(&self) {
        if let Mode::Runtime { state, .. } = self.cfg.mode() {
            if let Err(e) = write_shell_json(&state, SHELL_SOURCE) {
                self.log(&format!("cannot write {}: {e}", state.join("shell.json").display()));
            }
        }
    }

    /// Runtime mode: the claude-sync watch (Claude Code's settings file every 5 s, a full sync
    /// every 10 minutes), for its own thread. None in the other modes.
    pub fn claude_watch(&self) -> Option<ClaudeWatch> {
        let Mode::Runtime { state, .. } = self.cfg.mode() else { return None };
        Some(ClaudeWatch {
            sync: self.sync.clone(),
            state,
            log: self.log_path(),
            settings: self.settings.clone(),
            limits: self.limits,
        })
    }

    /// Whether the port answered at the last `tick`.
    pub fn last_up(&self) -> bool {
        self.last_up
    }

    /// What the tray should say besides "ATLAS", if anything.
    pub fn notice(&self) -> Option<Notice> {
        if self.no_slot {
            Some(Notice::NoSlot)
        } else if self.shell_update {
            Some(Notice::ShellUpdate)
        } else {
            None
        }
    }

    /// True while a hub this supervisor started is still running.
    pub fn owns_child(&mut self) -> bool {
        self.reap();
        self.child.is_some()
    }

    /// Called every 2 s: notice a dead child, and start one when nothing answers on the port. In
    /// runtime mode the start's slow part comes back as a `Prep`: the caller runs it without
    /// holding the supervisor's lock, then hands it to `start_prepared`.
    pub fn tick(&mut self) -> Option<Prep> {
        self.reap();
        self.last_up = hub_up(self.cfg.port);
        if self.stopped || !self.cfg.can_spawn() {
            return None;
        }
        if let (Some(_), Some(t)) = (&self.child, self.started_at) {
            if t.elapsed() >= HEALTHY_UPTIME {
                self.attempt = 0;
            }
            return None; // ours is running (perhaps still starting up)
        }
        if self.last_up {
            return None; // someone else's hub: connect only
        }
        if matches!(self.not_before, Some(t) if Instant::now() < t) {
            return None;
        }
        match self.cfg.mode() {
            Mode::Legacy { root, python, app_dir } => {
                self.spawn(python, app_dir, root);
                None
            }
            Mode::Runtime { live, state, .. } => self.prepare(live, state),
            Mode::ConnectOnly => None,
        }
    }

    /// The `Prep` for a runtime start, or None (connect only) when there is no usable slot yet.
    /// That is checked on every tick, so a `runtime init` run while the shell is up is picked up.
    fn prepare(&mut self, live: PathBuf, state: PathBuf) -> Option<Prep> {
        let Some((python, app)) = current_slot(&state) else {
            if !self.no_slot {
                self.no_slot = true;
                self.log(&format!(
                    "no runtime slot in {}: not starting a hub (connect only). Run `python -m atlas runtime init --live {}` once.",
                    state.display(),
                    live.display()
                ));
            }
            return None;
        };
        self.no_slot = false;
        let claude_sync = !std::mem::replace(&mut self.synced, true);
        Some(Prep {
            python,
            app,
            live,
            state,
            log: self.log_path(),
            claude_sync,
            limits: self.limits,
            sync: self.sync.clone(),
            settings: self.settings.clone(),
        })
    }

    /// After `prep` ran: start the hub from the slot the update step left current, unless the
    /// shell was stopped meanwhile or a hub is already there.
    pub fn start_prepared(&mut self, prep: Prep) {
        self.shell_update = shell_update_in(&prep.state, SHELL_SOURCE);
        self.reap();
        if self.stopped || self.child.is_some() || hub_up(self.cfg.port) {
            return;
        }
        let Some(root) = self.cfg.root.clone() else { return };
        match current_slot(&prep.state) {
            Some((python, app)) => {
                self.no_slot = false;
                self.spawn(python, app, root);
            }
            None => {
                self.no_slot = true;
                self.schedule_retry("no usable runtime slot after the update step");
            }
        }
    }

    /// Stop our hub (if any) and start a fresh one: at once in legacy mode; in runtime mode the
    /// watch thread's next tick does it, after the update step, never on the caller's (the
    /// tray's) thread.
    pub fn restart(&mut self) {
        self.kill_child();
        self.stopped = false;
        self.attempt = 0;
        self.not_before = None;
        if let Mode::Legacy { root, python, app_dir } = self.cfg.mode() {
            self.spawn(python, app_dir, root);
        }
    }

    /// Stop only the hub this supervisor started, and start no more.
    pub fn stop(&mut self) {
        self.stopped = true;
        self.kill_child();
    }

    fn kill_child(&mut self) {
        if let Some(mut c) = self.child.take() {
            let how = self.end_child(&mut c);
            self.log(&format!("hub stopped by the desktop shell ({how})"));
        }
        self.started_at = None;
    }

    /// Unix: SIGTERM, so the hub shuts down cleanly (its lock, its agents), then a kill if it is
    /// still running after `stop_grace`. Windows: a kill; the hub's own children are handled by
    /// the hub.
    fn end_child(&self, c: &mut Child) -> String {
        #[cfg(unix)]
        if let Some(status) = self.terminate(c) {
            return format!("SIGTERM, then {status}");
        }
        let killed = c.kill();
        let _ = c.wait();
        match killed {
            Err(e) => format!("could not kill the hub: {e}"),
            Ok(()) if cfg!(unix) => format!("killed: no exit within {:?} of SIGTERM", self.stop_grace),
            Ok(()) => "killed".into(),
        }
    }

    /// Send SIGTERM and wait up to `stop_grace` for the exit. None if it is still running.
    #[cfg(unix)]
    fn terminate(&self, c: &mut Child) -> Option<std::process::ExitStatus> {
        let pid = libc::pid_t::try_from(c.id()).ok()?;
        self.log("stopping the hub: SIGTERM");
        // A child in `self.child` has never been waited for (reap drops it once it has), so this
        // pid is still our child's, even if it has just exited: no other process can have it.
        if unsafe { libc::kill(pid, libc::SIGTERM) } != 0 {
            return None;
        }
        let deadline = Instant::now() + self.stop_grace;
        loop {
            match c.try_wait() {
                Ok(Some(status)) => return Some(status),
                Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(50)),
                _ => return None,
            }
        }
    }

    /// If our child has exited, forget it and schedule the next start: with backoff, or at once
    /// after exit code 75 (P8), which doesn't count as an attempt.
    fn reap(&mut self) {
        let exited = match self.child.as_mut().map(|c| c.try_wait()) {
            Some(Ok(Some(status))) => Some((status.to_string(), status.code())),
            Some(Err(e)) => Some((format!("unknown status ({e})"), None)),
            _ => None,
        };
        if let Some((status, code)) = exited {
            self.child = None;
            if self.started_at.take().is_some_and(|t| t.elapsed() >= HEALTHY_UPTIME) {
                self.attempt = 0;
            }
            if code == Some(EXIT_RESTART) {
                self.not_before = None;
                self.log(&format!("hub exited: {status} (its data folder is usable again); starting it again now"));
            } else {
                self.schedule_retry(&format!("hub exited: {status}"));
            }
        }
    }

    fn schedule_retry(&mut self, why: &str) {
        let delay = backoff(self.attempt);
        self.attempt = self.attempt.saturating_add(1);
        self.not_before = Some(Instant::now() + delay);
        self.log(&format!("{why}; next start in {} s", delay.as_secs()));
    }

    fn log_path(&self) -> PathBuf {
        self.state.join("hub.log")
    }

    fn open_log(&self) -> Option<File> {
        std::fs::create_dir_all(&self.state).ok()?;
        OpenOptions::new().create(true).append(true).open(self.log_path()).ok()
    }

    fn log(&self, msg: &str) {
        append_log(&self.log_path(), msg);
    }

    /// Start `<python> -m atlas --root <root> --port <port>` in `app_dir`: the configured
    /// python and app in legacy mode, the current slot's venv python and `app/` in runtime mode.
    /// Never with `-I` or `-P`: the hub imports `atlas` from its working folder.
    fn spawn(&mut self, python: PathBuf, app_dir: PathBuf, root: PathBuf) {
        let Some(out) = self.open_log() else {
            self.schedule_retry(&format!("cannot open {}", self.log_path().display()));
            return;
        };
        let err = match out.try_clone() {
            Ok(e) => e,
            Err(e) => {
                self.schedule_retry(&format!("cannot open the hub log twice: {e}"));
                return;
            }
        };
        let port = self.cfg.port;
        let mut cmd = Command::new(&python);
        cmd.args(hub_args(&root, port))
            .current_dir(&app_dir)
            .env("PYTHONUNBUFFERED", "1")
            .env("PYTHONIOENCODING", "utf-8")
            .stdin(Stdio::null())
            .stdout(out)
            .stderr(err);
        // Runtime mode: the hub reads update.json, current.json and shell.json (and keeps its held
        // captures and integration state) in the state folder the slots are in.
        if let Mode::Runtime { state, .. } = self.cfg.mode() {
            cmd.env("ATLAS_STATE_DIR", state);
        }
        crate::child_env(&mut cmd);
        // Its own process group: a Ctrl+C in the shell's terminal doesn't reach the hub as well, so
        // the supervisor's SIGTERM is the only stop signal it gets.
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            cmd.process_group(0);
        }
        // python.exe (not pythonw) with CREATE_NO_WINDOW: the hub gets a hidden console, which the
        // git, claude and taskkill processes it starts inherit, so none of them flashes a window
        // (Ruling 13).
        #[cfg(windows)]
        {
            use std::os::windows::process::CommandExt;
            cmd.creation_flags(CREATE_NO_WINDOW);
        }
        self.log(&format!("starting {} -m atlas --root {} --port {port} (in {})", python.display(), root.display(),
                          app_dir.display()));
        match cmd.spawn() {
            Ok(child) => {
                self.child = Some(child);
                self.started_at = Some(Instant::now());
            }
            Err(e) => self.schedule_retry(&format!("could not start the hub: {e}")),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::net::TcpListener;
    use std::sync::{Mutex, MutexGuard};

    #[test]
    fn backoff_sequence() {
        assert_eq!(backoff(0), Duration::from_secs(1));
        assert_eq!(backoff(1), Duration::from_secs(2));
        assert_eq!(backoff(2), Duration::from_secs(4));
        assert_eq!(backoff(5), Duration::from_secs(32));
        assert_eq!(backoff(6), Duration::from_secs(60));
        assert_eq!(backoff(20), Duration::from_secs(60));
        assert_eq!(backoff(u32::MAX), Duration::from_secs(60));
    }

    #[test]
    fn hub_up_false_on_closed_port() {
        let _serial = serial(); // a fork could hold the short-lived listener open (see `serial`)
        let port = TcpListener::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port();
        assert!(!hub_up(port));
    }

    #[test]
    fn hub_up_true_on_listener() {
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        assert!(hub_up(l.local_addr().unwrap().port()));
    }

    #[test]
    fn no_config_never_spawns() {
        let dir = temp("bh-sup-none");
        let mut s = Supervisor::new(DesktopConfig { port: free_port(), ..DesktopConfig::default() }, dir.join("state"));
        s.tick();
        assert!(!s.owns_child());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn listener_on_port_means_connect_only() {
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let dir = temp("bh-sup-busy");
        let mut s = sup(cfg(l.local_addr().unwrap().port(), &dir.join("root")), &dir);
        s.tick();
        assert!(s.last_up());
        assert!(!s.owns_child());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[cfg(windows)]
    #[test]
    fn spawns_logs_and_stops_own_child() {
        let dir = temp("bh-sup-spawn");
        let root = dir.join("root");
        // cmd.exe stands in for python: it starts, and may exit at once on its null stdin.
        let mut c = cfg(free_port(), &root);
        c.python = Some(PathBuf::from("C:\\Windows\\System32\\cmd.exe"));
        let mut s = sup(c, &dir);
        s.tick();
        let log = std::fs::read_to_string(dir.join("state").join("hub.log")).unwrap();
        assert!(log.contains("-m atlas --root"), "{log}");
        assert!(log.contains(&format!("--port {}", s.cfg.port)), "{log}");
        assert!(!root.exists(), "the shell writes nothing under the root");
        s.stop();
        assert!(!s.owns_child());
        s.tick(); // stopped: never starts again
        assert!(!s.owns_child());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[cfg(unix)]
    #[test]
    fn spawns_logs_and_stops_own_child_unix() {
        let _serial = serial();
        let dir = temp("bh-sup-spawn-unix");
        let root = dir.join("root");
        // /bin/true stands in for python: it ignores the hub arguments and exits at once.
        let mut c = cfg(free_port(), &root);
        c.python = Some(PathBuf::from("/bin/true"));
        let mut s = sup(c, &dir);
        s.tick();
        let log = std::fs::read_to_string(dir.join("state").join("hub.log")).unwrap();
        assert!(log.contains("-m atlas --root"), "{log}");
        assert!(log.contains(&format!("--port {}", s.cfg.port)), "{log}");
        assert!(!root.exists(), "the shell writes nothing under the root");
        s.stop();
        assert!(!s.owns_child());
        s.tick(); // stopped: never starts again
        assert!(!s.owns_child());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn failed_start_backs_off() {
        let _serial = serial();
        let dir = temp("bh-sup-fail");
        let root = dir.join("root");
        let mut s = sup(cfg(free_port(), &root), &dir);
        s.tick(); // python-does-not-exist.exe: spawn fails
        assert!(!s.owns_child());
        assert_eq!(s.attempt, 1);
        assert!(s.not_before.is_some());
        s.tick(); // still inside the 1 s backoff: no new attempt
        assert_eq!(s.attempt, 1);
        let log = std::fs::read_to_string(dir.join("state").join("hub.log")).unwrap();
        assert!(log.contains("could not start the hub"), "{log}");
        assert!(!root.exists(), "a missing root is never created for the log");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[cfg(unix)]
    #[test]
    fn exit_75_restarts_without_backoff() {
        let _serial = serial();
        let dir = temp("bh-sup-75");
        let mut c = cfg(free_port(), &dir.join("root"));
        c.python = Some(script(&dir, "exit75.sh", "exit 75"));
        let mut s = sup(c, &dir);
        s.tick(); // starts it; it exits with 75 at once
        wait_exit(&mut s);
        assert_eq!(s.attempt, 0, "exit 75 is not a failed attempt");
        assert!(s.not_before.is_none(), "exit 75 means start again at once");
        s.tick(); // the next tick starts it again
        let log = std::fs::read_to_string(dir.join("state").join("hub.log")).unwrap();
        assert_eq!(log.matches("] starting ").count(), 2, "{log}");
        assert!(log.contains("hub exited: exit status: 75"), "{log}");
        // Any other exit still backs off.
        wait_exit(&mut s);
        s.cfg.python = Some(PathBuf::from("/bin/false"));
        s.tick();
        wait_exit(&mut s);
        assert_eq!(s.attempt, 1);
        assert!(s.not_before.is_some());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[cfg(unix)]
    #[test]
    fn stop_sends_sigterm_first() {
        let _serial = serial();
        let dir = temp("bh-sup-term");
        let (ready, marker) = (dir.join("ready"), dir.join("got-term"));
        // The stand-in hub writes a marker when SIGTERM reaches it, then exits 0 as the hub does.
        let body = format!(
            "trap 'echo term > \"{}\"; exit 0' TERM\necho ready > \"{}\"\nwhile :; do sleep 0.05; done",
            marker.display(),
            ready.display()
        );
        let mut c = cfg(free_port(), &dir.join("root"));
        c.python = Some(script(&dir, "trap-term.sh", &body));
        let mut s = sup(c, &dir);
        s.tick();
        wait_for(&ready); // its trap is set
        let t = Instant::now();
        s.stop();
        assert!(marker.exists(), "SIGTERM came first");
        assert!(t.elapsed() < STOP_GRACE, "it exited on SIGTERM and wasn't killed");
        assert!(!s.owns_child());
        let log = std::fs::read_to_string(dir.join("state").join("hub.log")).unwrap();
        assert!(log.contains("hub stopped by the desktop shell (SIGTERM, then exit status: 0)"), "{log}");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[cfg(unix)]
    #[test]
    fn stop_kills_a_hub_that_ignores_sigterm() {
        let _serial = serial();
        let dir = temp("bh-sup-kill");
        let ready = dir.join("ready");
        let body = format!("trap '' TERM\necho ready > \"{}\"\nwhile :; do sleep 0.05; done", ready.display());
        let mut c = cfg(free_port(), &dir.join("root"));
        c.python = Some(script(&dir, "ignore-term.sh", &body));
        let mut s = sup(c, &dir);
        s.stop_grace = Duration::from_millis(300);
        s.tick();
        wait_for(&ready);
        s.stop();
        assert!(!s.owns_child());
        let log = std::fs::read_to_string(dir.join("state").join("hub.log")).unwrap();
        assert!(log.contains("killed: no exit within 300ms of SIGTERM"), "{log}");
        let _ = std::fs::remove_dir_all(&dir);
    }

    // -- runtime mode ------------------------------------------------------------------------

    /// One tick as the watch thread runs it: a runtime start's slow part outside the lock.
    #[cfg(unix)]
    fn tick_now(s: &mut Supervisor) {
        if let Some(p) = s.tick() {
            p.run();
            s.start_prepared(p);
        }
    }

    /// A runtime config whose state folder is `<dir>/state`, the shell's own (as in real use).
    fn runtime_cfg(port: u16, dir: &Path) -> DesktopConfig {
        DesktopConfig {
            port,
            root: Some(dir.join("root")),
            live: Some(dir.join("live")),
            state: Some(dir.join("state")),
            ..DesktopConfig::default()
        }
    }

    /// Slot `slot` in `state`, holding `commit` (its `slot.json`), with an `app/atlas` folder
    /// and an empty venv python. Returns the slot folder.
    fn make_slot(state: &Path, slot: &str, commit: &str) -> PathBuf {
        let dir = state.join("runtime").join(format!("slot-{slot}"));
        std::fs::create_dir_all(dir.join("app").join("atlas")).unwrap();
        std::fs::write(dir.join("slot.json"), format!(r#"{{"commit": "{commit}", "deps": "d"}}"#)).unwrap();
        let python = venv_python(&dir);
        std::fs::create_dir_all(python.parent().unwrap()).unwrap();
        std::fs::write(&python, "").unwrap();
        dir
    }

    fn make_current(state: &Path, slot: &str, commit: &str) {
        std::fs::write(state.join("runtime").join("current.json"),
                       format!(r#"{{"slot": "{slot}", "commit": "{commit}", "deps": "d"}}"#)).unwrap();
    }

    #[test]
    fn current_slot_needs_a_matching_complete_slot() {
        let dir = temp("bh-sup-slot");
        let state = dir.join("state");
        assert_eq!(current_slot(&state), None);
        let a = make_slot(&state, "a", "c0ffee");
        make_current(&state, "a", "c0ffee");
        assert_eq!(current_slot(&state), Some((venv_python(&a), a.join("app"))));
        // A half-built slot (slot.json names another commit, or none) never counts.
        make_current(&state, "a", "beef");
        assert_eq!(current_slot(&state), None);
        make_current(&state, "c", "c0ffee");
        assert_eq!(current_slot(&state), None);
        make_current(&state, "a", "c0ffee");
        std::fs::remove_file(venv_python(&a)).unwrap();
        assert_eq!(current_slot(&state), None);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn no_slot_means_connect_only() {
        let _serial = serial(); // free_port's listener, then a tick's port check
        let dir = temp("bh-sup-noslot");
        let mut s = sup(runtime_cfg(free_port(), &dir), &dir);
        assert!(s.tick().is_none(), "nothing to prepare without a slot");
        assert!(!s.owns_child());
        assert_eq!(s.notice(), Some(Notice::NoSlot));
        assert!(s.tick().is_none());
        let log = std::fs::read_to_string(dir.join("state").join("hub.log")).unwrap();
        assert_eq!(log.matches("no runtime slot").count(), 1, "said once: {log}");
        assert!(log.contains("runtime init --live"), "{log}");
        assert!(!log.contains("] starting ") && !log.contains("update step"), "{log}");
        assert!(!dir.join("root").exists(), "the shell writes nothing under the root");
        // `runtime init` run while the shell is up: the next tick starts.
        make_slot(&dir.join("state"), "a", "c0ffee");
        make_current(&dir.join("state"), "a", "c0ffee");
        assert!(s.tick().is_some());
        assert_eq!(s.notice(), None);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn restart_in_runtime_mode_leaves_the_start_to_the_watch_thread() {
        let _serial = serial(); // free_port's listener, then a tick's port check
        let dir = temp("bh-sup-rt-restart");
        make_slot(&dir.join("state"), "a", "c0ffee");
        make_current(&dir.join("state"), "a", "c0ffee");
        let mut s = sup(runtime_cfg(free_port(), &dir), &dir);
        s.restart(); // the tray's thread: no update step, no start here
        assert!(!s.owns_child());
        let log = std::fs::read_to_string(dir.join("state").join("hub.log")).unwrap_or_default();
        assert!(!log.contains("update step") && !log.contains("] starting "), "{log}");
        assert!(s.tick().is_some(), "the next tick prepares the start");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn shell_json_written_and_update_noticed() {
        let dir = temp("bh-sup-shell");
        let state = dir.join("state");
        std::fs::create_dir_all(&state).unwrap();
        let update = |src: &str| std::fs::write(state.join("update.json"), format!(r#"{{"shell_source": "{src}"}}"#)).unwrap();
        update(SHELL_SOURCE);
        let s = sup(runtime_cfg(free_port(), &dir), &dir);
        // Only the app's setup writes it (after the single-instance check), never a bare `new`.
        assert!(!state.join("shell.json").exists());
        s.record_shell();
        let shell: serde_json::Value = serde_json::from_str(&std::fs::read_to_string(state.join("shell.json")).unwrap()).unwrap();
        assert_eq!(shell["shell_source"], SHELL_SOURCE);
        assert_eq!(s.notice(), None, "same source: nothing to rebuild");
        update("0123abcd");
        assert_eq!(sup(runtime_cfg(free_port(), &dir), &dir).notice(), Some(Notice::ShellUpdate));
        // An unknown live source (the step couldn't hash it) never claims an update.
        std::fs::write(state.join("update.json"), r#"{"shell_source": null}"#).unwrap();
        assert_eq!(sup(runtime_cfg(free_port(), &dir), &dir).notice(), None);
        assert!(!shell_update_in(&state, ""), "an unknown own source neither");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[cfg(unix)]
    #[test]
    fn runtime_mode_spawn_args() {
        let _serial = serial();
        let dir = temp("bh-sup-runtime");
        let state = dir.join("state");
        let calls = dir.join("calls.txt");
        let a = make_slot(&state, "a", "c0ffee");
        let b = make_slot(&state, "b", "beef");
        make_current(&state, "a", "c0ffee");
        // Slot a's python records each call; as the update step it flips current.json to slot b
        // (as a real refresh would), reports a newer shell source, and prints its JSON line.
        let step = format!(
            "case \"$1\" in -I) printf '%s' '{{\"slot\": \"b\", \"commit\": \"beef\", \"deps\": \"d\"}}' > \"{cur}\"\n\
             printf '%s' '{{\"shell_source\": \"newer\"}}' > \"{upd}\"\n\
             echo '{{\"pulled\": true, \"slot\": \"b\", \"commit\": \"beef\", \"pending\": null}}';; esac",
            cur = state.join("runtime").join("current.json").display(),
            upd = state.join("update.json").display()
        );
        // Each call records its slot, working folder, ATLAS_STATE_DIR and arguments.
        let record =
            |slot: &str| format!("echo \"{slot}|$(pwd)|$ATLAS_STATE_DIR|$*\" >> \"{}\"", calls.display());
        script(&a.join("venv").join("bin"), "python", &format!("{}\n{step}", record("a")));
        script(&b.join("venv").join("bin"), "python", &record("b"));

        let port = free_port();
        // The shell's own state folder (its env or XDG default) differs from desktop.json's: the
        // logs and every child follow desktop.json's.
        let own = dir.join("shell-default");
        let mut s = sup_in(runtime_cfg(port, &dir), &dir, &own);
        tick_now(&mut s);
        wait_exit(&mut s);
        let got = std::fs::read_to_string(&calls).unwrap();
        let (a_app, b_app, root, live) = (a.join("app"), b.join("app"), dir.join("root"), dir.join("live"));
        let st = state.display();
        let expected = [
            format!("a|{}|{st}|-m atlas claude-sync", a_app.display()),
            format!("a|{st}|{st}|-I -S {} --live {} --state {st}",
                    a_app.join("atlas").join("update_step.py").display(), live.display()),
            // The hub runs from the slot the step left current, with no -I: its app folder (the
            // working folder) must stay importable.
            format!("b|{}|{st}|-m atlas --root {} --port {port}", b_app.display(), root.display()),
        ];
        assert_eq!(got.lines().collect::<Vec<_>>(), expected);
        let log = std::fs::read_to_string(state.join("hub.log")).unwrap();
        assert!(log.contains("{\"pulled\": true, \"slot\": \"b\""), "the step's JSON line: {log}");
        assert!(log.contains("update step: exit status: 0"), "{log}");
        assert!(log.contains("claude-sync (at start): exit status: 0"), "{log}");
        assert!(!own.exists(), "nothing goes to the shell's own state folder");
        assert_eq!(s.notice(), Some(Notice::ShellUpdate));

        // The next start runs the update step again (now with slot b's python), but not claude-sync.
        s.not_before = None;
        tick_now(&mut s);
        wait_exit(&mut s);
        let got = std::fs::read_to_string(&calls).unwrap();
        let later: Vec<&str> = got.lines().skip(3).collect();
        assert_eq!(later.len(), 2, "{got}");
        assert!(later[0].starts_with(&format!("b|{st}|{st}|-I -S ")), "{got}");
        assert!(later[1].starts_with(&format!("b|{}|{st}|-m atlas --root", b_app.display())), "{got}");
        assert_eq!(got.matches("claude-sync").count(), 1, "once per shell start: {got}");
        assert!(!root.exists(), "the shell writes nothing under the root");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[cfg(unix)]
    #[test]
    fn legacy_mode_unchanged() {
        let _serial = serial();
        let dir = temp("bh-sup-legacy");
        let calls = dir.join("calls.txt");
        let app = dir.join("app");
        std::fs::create_dir_all(&app).unwrap();
        let mut c = cfg(free_port(), &dir.join("root"));
        c.python = Some(script(&dir, "python", &format!("echo \"$(pwd)|$*\" >> \"{}\"", calls.display())));
        c.app_dir = Some(app.clone());
        let port = c.port;
        let mut s = sup(c, &dir);
        assert!(s.tick().is_none(), "nothing runs outside the lock in legacy mode");
        wait_exit(&mut s);
        let got = std::fs::read_to_string(&calls).unwrap();
        assert_eq!(got.lines().collect::<Vec<_>>(),
                   [format!("{}|-m atlas --root {} --port {port}", app.display(), dir.join("root").display())]);
        let log = std::fs::read_to_string(dir.join("state").join("hub.log")).unwrap();
        assert!(!log.contains("update step") && !log.contains("claude-sync"), "{log}");
        s.record_shell();
        assert!(!dir.join("state").join("shell.json").exists(), "shell.json is for runtime mode");
        assert!(s.claude_watch().is_none(), "the claude-sync watch is for runtime mode");
        assert_eq!(s.notice(), None);
        // Restart hub still starts it at once.
        s.restart();
        wait_exit(&mut s);
        assert_eq!(std::fs::read_to_string(&calls).unwrap().lines().count(), 2);
        let _ = std::fs::remove_dir_all(&dir);
    }

    /// Gone, or a zombie its new parent is about to reap.
    #[cfg(unix)]
    fn pid_gone(pid: libc::pid_t) -> bool {
        if unsafe { libc::kill(pid, 0) } != 0 {
            return true;
        }
        #[cfg(target_os = "linux")]
        if let Ok(stat) = std::fs::read_to_string(format!("/proc/{pid}/stat")) {
            return stat.rsplit(')').next().is_some_and(|rest| rest.trim_start().starts_with('Z'));
        }
        false
    }

    #[cfg(unix)]
    #[test]
    fn update_step_timeout_kills_tree() {
        let _serial = serial();
        let dir = temp("bh-sup-steptimeout");
        let state = dir.join("state");
        let (pid_file, calls) = (dir.join("sleep.pid"), dir.join("calls.txt"));
        let a = make_slot(&state, "a", "c0ffee");
        make_current(&state, "a", "c0ffee");
        // As the real update step: it starts its child in a session of its own (out of reach of
        // a signal to the step's process group), hangs on it, and on SIGTERM kills the child's
        // group and exits (`on_sigterm` in update_step.py).
        let body = format!(
            "echo \"$*\" >> \"{calls}\"\ncase \"$1\" in -I) trap 'kill -s KILL -- -$child; exit 0' TERM\n\
             setsid sleep 30 &\nchild=$!\necho $child > \"{pid}\"\nwait;; esac",
            calls = calls.display(),
            pid = pid_file.display()
        );
        script(&a.join("venv").join("bin"), "python", &body);
        let mut s = sup(runtime_cfg(free_port(), &dir), &dir);
        s.synced = true; // claude-sync isn't what's tested here
        s.limits.update_step = Duration::from_millis(500);
        let t = Instant::now();
        tick_now(&mut s);
        assert!(t.elapsed() < Duration::from_secs(3), "the step was cut off at its limit and ended on SIGTERM");
        let pid: libc::pid_t = std::fs::read_to_string(&pid_file).unwrap().trim().parse().unwrap();
        let deadline = Instant::now() + Duration::from_secs(5);
        while !pid_gone(pid) {
            assert!(Instant::now() < deadline, "the step's child {pid} outlived the timeout");
            std::thread::sleep(Duration::from_millis(20));
        }
        let log = std::fs::read_to_string(state.join("hub.log")).unwrap();
        assert!(log.contains("update step: timed out after 0.5 s: killed with its process tree"), "{log}");
        // The hub still started, from the current slot.
        wait_exit(&mut s);
        let got = std::fs::read_to_string(&calls).unwrap();
        assert!(got.lines().last().unwrap().starts_with("-m atlas --root"), "{got}");
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[cfg(unix)]
    #[test]
    fn timeout_kills_a_step_that_ignores_sigterm() {
        let _serial = serial();
        let dir = temp("bh-sup-stepkill");
        let state = dir.join("state");
        let pid_file = dir.join("sleep.pid");
        let a = make_slot(&state, "a", "c0ffee");
        make_current(&state, "a", "c0ffee");
        // A step that ignores SIGTERM, with a child in its process group (which ignores it too):
        // SIGKILL to the group follows after the grace.
        let body = format!("case \"$1\" in -I) trap '' TERM\nsleep 30 &\necho $! > \"{}\"\nwait;; esac",
                           pid_file.display());
        script(&a.join("venv").join("bin"), "python", &body);
        let mut s = sup(runtime_cfg(free_port(), &dir), &dir);
        s.synced = true;
        s.limits.update_step = Duration::from_millis(500);
        s.limits.term_grace = Duration::from_millis(300);
        let t = Instant::now();
        tick_now(&mut s);
        assert!(t.elapsed() < Duration::from_secs(5), "{:?}", t.elapsed());
        let pid: libc::pid_t = std::fs::read_to_string(&pid_file).unwrap().trim().parse().unwrap();
        let deadline = Instant::now() + Duration::from_secs(5);
        while !pid_gone(pid) {
            assert!(Instant::now() < deadline, "the step's child {pid} outlived the timeout");
            std::thread::sleep(Duration::from_millis(20));
        }
        let log = std::fs::read_to_string(state.join("hub.log")).unwrap();
        assert!(log.contains("update step: timed out after 0.5 s: killed with its process tree"), "{log}");
        wait_exit(&mut s);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[cfg(unix)]
    #[test]
    fn claude_watch_runs_settings_only_on_a_change() {
        let _serial = serial();
        let dir = temp("bh-sup-watch");
        let state = dir.join("state");
        let (calls, settings) = (dir.join("calls.txt"), dir.join("claude").join("settings.json"));
        let a = make_slot(&state, "a", "c0ffee");
        make_current(&state, "a", "c0ffee");
        // As claude-sync, slot a's python records the call and rewrites the settings file.
        let body = format!("echo \"$(pwd)|$ATLAS_STATE_DIR|$*\" >> \"{}\"\nprintf '{{}}' > \"{}\"",
                           calls.display(), settings.display());
        script(&a.join("venv").join("bin"), "python", &body);
        std::fs::create_dir_all(settings.parent().unwrap()).unwrap();
        std::fs::write(&settings, "{}").unwrap();
        // Times set by hand: two writes within one clock tick can share an mtime.
        let set_mtime = |secs: u64| {
            let f = std::fs::File::options().write(true).open(&settings).unwrap();
            f.set_modified(UNIX_EPOCH + Duration::from_secs(secs)).unwrap();
        };
        set_mtime(1_000_000);
        let s = sup(runtime_cfg(free_port(), &dir), &dir);
        let watch = s.claude_watch().expect("runtime mode has the watch");
        assert_eq!(watch.settings.as_deref(), Some(settings.as_path()));
        assert_eq!(watch.poll_once(), None, "the first look only records the mtime");
        set_mtime(2_000_000); // the owner changed a setting
        assert_eq!(watch.poll_once(), Some(crate::claude_watch::Run::SettingsOnly));
        assert_eq!(watch.poll_once(), None, "claude-sync's own write is no change");
        let got = std::fs::read_to_string(&calls).unwrap();
        assert_eq!(got.lines().collect::<Vec<_>>(),
                   [format!("{}|{}|-m atlas claude-sync --settings-only", a.join("app").display(), state.display())]);
        let log = std::fs::read_to_string(state.join("hub.log")).unwrap();
        assert!(log.contains("claude-sync (settings changed): exit status: 0"), "{log}");
        // Without a slot nothing runs, and the change waits for one.
        std::fs::remove_file(state.join("runtime").join("current.json")).unwrap();
        set_mtime(3_000_000);
        assert_eq!(watch.poll_once(), None);
        make_current(&state, "a", "c0ffee");
        assert_eq!(watch.poll_once(), Some(crate::claude_watch::Run::SettingsOnly));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn hub_args_pass_the_configured_port() {
        // The hub must listen where the shell looks, whatever its own `port` setting says.
        let args = hub_args(std::path::Path::new(r"C:\bh root"), 8099);
        let args: Vec<String> = args.iter().map(|a| a.to_string_lossy().into_owned()).collect();
        assert_eq!(args, ["-m", "atlas", "--root", r"C:\bh root", "--port", "8099"]);
    }

    /// Tests that start processes run one at a time: a script written while another test's spawn
    /// forks can be held open in that child, and executing it then fails with ETXTBSY. Tests that
    /// need a port to be closed take it too: a fork holds a short-lived listener (`free_port`) open
    /// in the child until it execs, so a connect to that port would succeed.
    fn serial() -> MutexGuard<'static, ()> {
        static LOCK: Mutex<()> = Mutex::new(());
        LOCK.lock().unwrap_or_else(|e| e.into_inner())
    }

    fn temp(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        dir
    }

    /// A stand-in for python: an executable `sh` script (it gets the hub arguments and ignores them).
    #[cfg(unix)]
    fn script(dir: &Path, name: &str, body: &str) -> PathBuf {
        use std::os::unix::fs::PermissionsExt;
        std::fs::create_dir_all(dir).unwrap();
        let f = dir.join(name);
        std::fs::write(&f, format!("#!/bin/sh\n{body}\n")).unwrap();
        std::fs::set_permissions(&f, std::fs::Permissions::from_mode(0o755)).unwrap();
        f
    }

    #[cfg(unix)]
    fn wait_for(path: &Path) {
        let deadline = Instant::now() + Duration::from_secs(5);
        while !path.exists() {
            assert!(Instant::now() < deadline, "{} never appeared", path.display());
            std::thread::sleep(Duration::from_millis(10));
        }
    }

    /// Wait until our child has exited (and was reaped).
    #[cfg(unix)]
    fn wait_exit(s: &mut Supervisor) {
        let deadline = Instant::now() + Duration::from_secs(5);
        while s.owns_child() {
            assert!(Instant::now() < deadline, "the stand-in hub never exited");
            std::thread::sleep(Duration::from_millis(10));
        }
    }

    fn free_port() -> u16 {
        TcpListener::bind("127.0.0.1:0").unwrap().local_addr().unwrap().port()
    }

    fn cfg(port: u16, root: &std::path::Path) -> DesktopConfig {
        DesktopConfig {
            port,
            root: Some(root.to_path_buf()),
            python: Some(PathBuf::from("python-does-not-exist.exe")),
            app_dir: Some(std::env::temp_dir()),
            ..DesktopConfig::default()
        }
    }

    /// A supervisor whose own state folder (hub.log outside runtime mode) is `<dir>/state`.
    fn sup(c: DesktopConfig, dir: &Path) -> Supervisor {
        sup_in(c, dir, &dir.join("state"))
    }

    /// A supervisor whose own state folder is `own`, watching `<dir>/claude/settings.json` (never
    /// the real one).
    fn sup_in(c: DesktopConfig, dir: &Path, own: &Path) -> Supervisor {
        let mut s = Supervisor::new(c, own.to_path_buf());
        s.settings = Some(dir.join("claude").join("settings.json"));
        s
    }
}
