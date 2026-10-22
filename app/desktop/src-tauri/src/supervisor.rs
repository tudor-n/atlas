//! Starts and restarts the hub (design §8). It only ever stops a hub it started itself.

use crate::config::DesktopConfig;
use std::fs::{File, OpenOptions};
use std::io::Write;
use std::net::{SocketAddr, TcpStream};
use std::ffi::OsString;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
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

/// Delay before restart attempt `attempt`: 1, 2, 4, 8, 16, 32 s, then 60 s.
pub fn backoff(attempt: u32) -> Duration {
    Duration::from_secs(if attempt >= 6 { 60 } else { 1u64 << attempt })
}

/// The hub's arguments: `-m brainhub --root <root> --port <port>`. The port is passed explicitly so
/// the hub listens where the shell looks, even if the hub's own `port` setting has changed since
/// `desktop-config` ran.
pub fn hub_args(root: &Path, port: u16) -> Vec<OsString> {
    vec!["-m".into(), "brainhub".into(), "--root".into(), root.as_os_str().to_owned(), "--port".into(),
         port.to_string().into()]
}

/// Does anything accept a TCP connection on `127.0.0.1:port` within 500 ms?
pub fn hub_up(port: u16) -> bool {
    let addr = SocketAddr::from(([127, 0, 0, 1], port));
    TcpStream::connect_timeout(&addr, Duration::from_millis(500)).is_ok()
}

pub struct Supervisor {
    cfg: DesktopConfig,
    /// The per-OS state folder. `hub.log` lives there, not under the root: a missing drive can
    /// still be diagnosed, and the shell never creates anything on the root.
    state: PathBuf,
    child: Option<Child>,
    started_at: Option<Instant>,
    attempt: u32,
    not_before: Option<Instant>,
    stopped: bool,
    last_up: bool,
    stop_grace: Duration,
}

impl Supervisor {
    pub fn new(cfg: DesktopConfig, state: PathBuf) -> Self {
        Supervisor {
            cfg,
            state,
            child: None,
            started_at: None,
            attempt: 0,
            not_before: None,
            stopped: false,
            last_up: false,
            stop_grace: STOP_GRACE,
        }
    }

    /// Whether the port answered at the last `tick`.
    pub fn last_up(&self) -> bool {
        self.last_up
    }

    /// True while a hub this supervisor started is still running.
    pub fn owns_child(&mut self) -> bool {
        self.reap();
        self.child.is_some()
    }

    /// Called every 2 s: notice a dead child, and start one when nothing answers on the port.
    pub fn tick(&mut self) {
        self.reap();
        self.last_up = hub_up(self.cfg.port);
        if self.stopped || !self.cfg.can_spawn() {
            return;
        }
        if let (Some(_), Some(t)) = (&self.child, self.started_at) {
            if t.elapsed() >= HEALTHY_UPTIME {
                self.attempt = 0;
            }
            return; // ours is running (perhaps still starting up)
        }
        if self.last_up {
            return; // someone else's hub: connect only
        }
        if matches!(self.not_before, Some(t) if Instant::now() < t) {
            return;
        }
        self.spawn();
    }

    /// Stop our hub (if any) and start a fresh one now.
    pub fn restart(&mut self) {
        self.kill_child();
        self.stopped = false;
        self.attempt = 0;
        self.not_before = None;
        if self.cfg.can_spawn() {
            self.spawn();
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
        if let Some(mut f) = self.open_log() {
            let secs = SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
            let _ = writeln!(f, "[brainhub-desktop t={secs}] {msg}");
        }
    }

    fn spawn(&mut self) {
        let (Some(root), Some(python), Some(app_dir)) = (&self.cfg.root, &self.cfg.python, &self.cfg.app_dir) else {
            return;
        };
        let (root, python, app_dir) = (root.clone(), python.clone(), app_dir.clone());
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
        self.log(&format!("starting {} -m brainhub --root {} --port {port}", python.display(), root.display()));
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
        assert!(log.contains("-m brainhub --root"), "{log}");
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
        assert!(log.contains("-m brainhub --root"), "{log}");
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

    #[test]
    fn hub_args_pass_the_configured_port() {
        // The hub must listen where the shell looks, whatever its own `port` setting says.
        let args = hub_args(std::path::Path::new(r"C:\bh root"), 8099);
        let args: Vec<String> = args.iter().map(|a| a.to_string_lossy().into_owned()).collect();
        assert_eq!(args, ["-m", "brainhub", "--root", r"C:\bh root", "--port", "8099"]);
    }

    /// Tests that start processes run one at a time: a script written while another test's spawn
    /// forks can be held open in that child, and executing it then fails with ETXTBSY.
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
        }
    }

    /// A supervisor whose state folder (hub.log) is `<dir>/state`.
    fn sup(c: DesktopConfig, dir: &Path) -> Supervisor {
        Supervisor::new(c, dir.join("state"))
    }
}
