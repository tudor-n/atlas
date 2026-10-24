//! The cut-over from BrainHub, the app's name before the rename to ATLAS (rename spec §2.1, §2.3).
//! It runs first thing at start, before `desktop.json` is read (`run`, called from `lib.rs`).
//!
//! The folder move runs only when no `ATLAS_CONFIG_DIR` / `ATLAS_STATE_DIR` override is set, the
//! new `desktop.json` is absent and the legacy one exists. Then:
//! 1. the legacy state folder is renamed to the new one, then (Linux) the legacy config folder to
//!    the new one; on Windows both are `%LOCALAPPDATA%\BrainHub`, one folder. Each is a single
//!    rename, only when the target is absent or an empty folder, and state goes first, so the new
//!    `desktop.json` existing always means "done";
//! 2. the moved `desktop.json` is rewritten: `state` gets the new state folder when it named the
//!    legacy one; `root`, `live` and `app_dir` are swapped to a sibling with one `BrainHub` path
//!    component replaced by `ATLAS` when they are missing on disk and that sibling exists (the
//!    owner's renamed shared folder);
//! 3. `<state>/migrated.json` records it, and `hub.log` gets one line.
//!
//! Any failure is logged and the shell runs on whichever `desktop.json` is on disk (the legacy one,
//! where it is), with paths that match the disk; every step is idempotent, so a later start
//! finishes the job. Separately, whenever no override is set, the legacy login entry (Linux
//! `~/.config/autostart/BrainHub Desktop.desktop`, Windows the HKCU Run value `BrainHub Desktop`)
//! is removed and the shell turns its own autostart on.
//!
//! The pre-rename names live here only (and in the tests below).

use crate::config::{self, Env};
use serde_json::{Map, Value};
use std::path::{Path, PathBuf};

/// The package folder of a runtime slot built from a commit before the rename (spec §2.3).
pub const LEGACY_PACKAGE: &str = "brainhub";
/// The state folder variable a pre-rename slot's update step, hub and claude-sync read.
pub const LEGACY_STATE_VAR: &str = "BRAINHUB_STATE_DIR";
/// The pre-rename `(windows, xdg)` names of the state and config folders.
const LEGACY_NAMES: (&str, &str) = ("BrainHub", "brainhub");
/// The shared root's folder name, before and after (the owner renames it: spec §3).
const LEGACY_ROOT_NAME: &str = "BrainHub";
const ROOT_NAME: &str = "ATLAS";
/// The pre-rename login entry (tauri-plugin-autostart names it after the old productName).
const LEGACY_AUTOSTART: &str = "BrainHub Desktop";
const OVERRIDES: [&str; 2] = ["ATLAS_CONFIG_DIR", "ATLAS_STATE_DIR"];
const DESKTOP_JSON: &str = "desktop.json";

/// What `run` did.
#[derive(Debug, Default, PartialEq, Eq)]
pub struct Outcome {
    /// The folder move's three conditions held.
    pub ran: bool,
    /// `"<old> -> <new>"` per folder renamed.
    pub moved: Vec<String>,
    /// `"<key>: <old> -> <new>"` per `desktop.json` path rewritten.
    pub repointed: Vec<String>,
    /// Why it stopped early, if it did (logged; the shell runs on the legacy file).
    pub error: Option<String>,
    /// The `desktop.json` to read: the new one, or (after a failure) the legacy one where it is.
    pub config: Option<PathBuf>,
    /// The legacy login entry was there and is gone: the shell turns its own autostart on.
    pub autostart_removed: bool,
    /// When the state move went through but `desktop.json` couldn't be rewritten: its `state`
    /// still names the old folder, which is gone, so the shell uses `(old, new)`'s new one.
    pub state_swap: Option<(PathBuf, PathBuf)>,
    /// No override is set: `fix` also swaps a missing `BrainHub` path to its renamed sibling.
    pub swap_siblings: bool,
}

impl Outcome {
    /// The config as read, with what couldn't be written applied in memory: the state swap (see
    /// `state_swap`) and the renamed-sibling swap of `root`, `live` and `app_dir`.
    pub fn fix(&self, mut cfg: config::DesktopConfig) -> config::DesktopConfig {
        if let (Some((old, new)), Some(state)) = (&self.state_swap, &cfg.state) {
            if state == old && moved_away(old, new) && new.exists() {
                cfg.state = Some(new.clone());
            }
        }
        for path in [&mut cfg.root, &mut cfg.live, &mut cfg.app_dir] {
            if let Some(new) = path.as_deref().filter(|_| self.swap_siblings).and_then(renamed_sibling) {
                *path = Some(new);
            }
        }
        cfg
    }
}

/// The folders involved: `(legacy state, new state, legacy config, new config)`.
struct Dirs {
    old_state: PathBuf,
    new_state: PathBuf,
    old_cfg: PathBuf,
    new_cfg: PathBuf,
}

fn dirs(env: Env) -> Option<Dirs> {
    Some(Dirs {
        old_state: config::os_default_dir(env, "XDG_DATA_HOME", ".local/share", LEGACY_NAMES)?,
        new_state: config::os_default_dir(env, "XDG_DATA_HOME", ".local/share", config::NAMES)?,
        old_cfg: config::os_default_dir(env, "XDG_CONFIG_HOME", ".config", LEGACY_NAMES)?,
        new_cfg: config::os_default_dir(env, "XDG_CONFIG_HOME", ".config", config::NAMES)?,
    })
}

fn overridden(env: Env) -> bool {
    OVERRIDES.iter().any(|v| config::env_path(env, v).is_some())
}

/// The shell's start: the folder move (when due), then the legacy login entry. Logs to hub.log.
pub fn run() -> Outcome {
    let mut out = start_in(&config::real_env);
    match remove_legacy_autostart(&config::real_env) {
        Ok(removed) => out.autostart_removed = removed,
        Err(e) => eprintln!("atlas-desktop: could not remove the old login entry: {e}"),
    }
    out
}

/// Every start (tests pass a fake `env`): the folder move when it is due, then, whenever no
/// override is set, the path repointing of whichever `desktop.json` is in use. The owner may
/// rename the shared folder (or mount the drive) only after the move: the next start follows it.
pub(crate) fn start_in(env: Env) -> Outcome {
    let mut out = migrate_in(env);
    out.swap_siblings = !overridden(env);
    if out.swap_siblings {
        if let (Some(path), Some(d)) = (out.config.clone(), dirs(env)) {
            let before = out.repointed.len();
            let result = if path.is_file() { rewrite(&path, &d, &mut out) } else { Ok(()) };
            let log_dir = if d.new_state.is_dir() { &d.new_state } else { &d.old_state };
            match result {
                Ok(()) if out.repointed.len() > before => crate::supervisor::append_log(
                    &log_dir.join("hub.log"), &format!("repointed {}", list(&out.repointed[before..]))),
                Ok(()) => {}
                Err(e) => crate::supervisor::append_log(&log_dir.join("hub.log"), &e),
            }
        }
    }
    out
}

/// Whether the pre-rename shell or hub still runs: the legacy `desktop.json`'s port answers, or
/// the legacy update step holds its lock. The folders are never moved under a running process.
fn legacy_running(d: &Dirs, legacy_json: &Path) -> Option<String> {
    let port = config::load(legacy_json).port;
    if crate::supervisor::hub_up(port) {
        return Some(format!("a hub answers on port {port}"));
    }
    let lock = d.old_state.join("runtime").join("update.lock");
    if let Ok(file) = std::fs::OpenOptions::new().read(true).write(true).open(&lock) {
        if let Err(std::fs::TryLockError::WouldBlock) = file.try_lock() {
            return Some(format!("its update step holds {}", lock.display()));
        }
        // Taken just now, or not lockable: it is released when `file` is dropped.
    }
    None
}

/// The folder move and the `desktop.json` rewrite, in `env` (tests pass a fake one).
pub(crate) fn migrate_in(env: Env) -> Outcome {
    let mut out = Outcome { config: config::default_path_in(env), ..Outcome::default() };
    let (Some(new_json), Some(d)) = (out.config.clone(), dirs(env)) else { return out };
    let legacy_json = d.old_cfg.join(DESKTOP_JSON);
    if overridden(env) || new_json.exists() || !legacy_json.is_file() {
        return out;
    }
    if let Some(why) = legacy_running(&d, &legacy_json) {
        let msg = format!("not moving the {LEGACY_ROOT_NAME} folders yet: {why}. Quit {LEGACY_ROOT_NAME} first \
                           (tray → Quit), then start ATLAS again");
        crate::supervisor::append_log(&d.old_state.join("hub.log"), &msg);
        out.config = Some(legacy_json);
        out.error = Some(msg);
        return out;
    }
    out.ran = true;
    let result = migrate(&d, &legacy_json, &new_json, &mut out);
    out.config = Some(if new_json.exists() { new_json } else { legacy_json });
    let log_dir = if d.new_state.is_dir() { &d.new_state } else { &d.old_state };
    let line = match &result {
        Ok(()) => format!("migrated from {LEGACY_ROOT_NAME}: moved {}; repointed {}", list(&out.moved),
                          list(&out.repointed)),
        Err(e) => format!("migration from {LEGACY_ROOT_NAME} stopped ({e}); moved {}; running on {}",
                          list(&out.moved), out.config.as_deref().unwrap_or(Path::new("?")).display()),
    };
    crate::supervisor::append_log(&log_dir.join("hub.log"), &line);
    out.error = result.err();
    out
}

fn list(items: &[String]) -> String {
    if items.is_empty() { "nothing".into() } else { items.join(", ") }
}

fn migrate(d: &Dirs, legacy_json: &Path, new_json: &Path, out: &mut Outcome) -> Result<(), String> {
    // 1. State first: until the config folder has moved too, the legacy desktop.json says "not done".
    if move_dir(&d.old_state, &d.new_state)? {
        out.moved.push(format!("{} -> {}", d.old_state.display(), d.new_state.display()));
        // Until the hub has repaired them, the hooks, the wrapper and the status line's
        // passthrough still name the old folder: keep those paths working (the hub removes this
        // once nothing names it: claude_integration.remove_legacy_compat).
        if let Err(e) = leave_compat(&d.old_state, &d.new_state) {
            out.repointed.push(format!("(no compat path at {}: {e})", d.old_state.display()));
        }
    }
    // On Windows the state folder is the config folder: desktop.json moved with it.
    let here = if legacy_json.is_file() { legacy_json.to_path_buf() } else { new_json.to_path_buf() };
    // 2. The rewrite, where the file is now.
    if let Err(e) = rewrite(&here, d, out) {
        out.state_swap = Some((d.old_state.clone(), d.new_state.clone()));
        return Err(e);
    }
    // 3. The config folder (Linux; on Windows it was step 1).
    if d.old_cfg != d.old_state && move_dir(&d.old_cfg, &d.new_cfg)? {
        out.moved.push(format!("{} -> {}", d.old_cfg.display(), d.new_cfg.display()));
    }
    // 4. The record.
    let record = serde_json::json!({
        "from": LEGACY_PACKAGE,
        "at": std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|t| t.as_secs()).unwrap_or(0),
        "moved": out.moved,
        "repointed": out.repointed,
    });
    std::fs::create_dir_all(&d.new_state)
        .and_then(|()| std::fs::write(d.new_state.join("migrated.json"), record.to_string() + "\n"))
        .map_err(|e| format!("cannot write migrated.json: {e}"))
}

/// The marker file of the Windows compat copy (`legacy.COMPAT_MARKER` on the Python side).
const COMPAT_MARKER: &str = "moved-to-atlas.txt";

/// Linux: a symlink at the old state folder to the new one. Windows: the old folder again, with
/// copies of the stable hook files and the passthrough, and a marker (a junction would need a
/// process or a crate; the hook files are all the old paths need).
#[cfg(not(windows))]
fn leave_compat(old: &Path, new: &Path) -> std::io::Result<()> {
    std::os::unix::fs::symlink(new, old)
}

#[cfg(windows)]
fn leave_compat(old: &Path, new: &Path) -> std::io::Result<()> {
    std::fs::create_dir_all(old.join("hook"))?;
    for rel in ["hook/hook_client.py", "hook/request_client.py", "statusline_passthrough.txt"] {
        let src = new.join(rel);
        if src.is_file() {
            std::fs::copy(&src, old.join(rel))?;
        }
    }
    std::fs::write(old.join(COMPAT_MARKER), format!("{} moved to {}\n", old.display(), new.display()))
}

/// Whether `old` is the compat path `leave_compat` left for `new` (so it counts as moved).
fn is_compat(old: &Path, new: &Path) -> bool {
    if cfg!(windows) {
        old.join(COMPAT_MARKER).is_file()
    } else {
        old.is_symlink() && std::fs::read_link(old).is_ok_and(|t| t == new)
    }
}

/// The old folder is gone, or only our compat path is left in its place.
fn moved_away(old: &Path, new: &Path) -> bool {
    !old.exists() || is_compat(old, new)
}

/// Rename `from` to `to` in one rename, only when `to` is absent or an empty folder. Ok(false)
/// when there is nothing to move (no `from`: never had one, or moved by an earlier start, which
/// may have left its compat path there).
fn move_dir(from: &Path, to: &Path) -> Result<bool, String> {
    if from == to || !from.is_dir() || is_compat(from, to) {
        return Ok(false);
    }
    if to.exists() {
        let empty = std::fs::read_dir(to).map(|mut d| d.next().is_none()).unwrap_or(false);
        if !empty {
            return Err(format!("{} already exists and isn't empty; {} was left where it is", to.display(),
                               from.display()));
        }
        std::fs::remove_dir(to).map_err(|e| format!("cannot replace the empty {}: {e}", to.display()))?;
    }
    if let Some(parent) = to.parent() {
        std::fs::create_dir_all(parent).map_err(|e| format!("cannot create {}: {e}", parent.display()))?;
    }
    std::fs::rename(from, to).map_err(|e| format!("cannot move {} to {}: {e}", from.display(), to.display()))?;
    Ok(true)
}

/// Rewrite the paths in `desktop.json` (spec §2.1 step 2), atomically, only when one changes. A
/// file that isn't a JSON object is left as it is (the shell reads it as the defaults anyway).
fn rewrite(path: &Path, d: &Dirs, out: &mut Outcome) -> Result<(), String> {
    let text = std::fs::read_to_string(path).map_err(|e| format!("cannot read {}: {e}", path.display()))?;
    let Ok(Value::Object(mut obj)) = serde_json::from_str::<Value>(text.trim_start_matches('\u{feff}')) else {
        return Ok(());
    };
    let changes = repoint(&mut obj, d);
    if changes.is_empty() {
        return Ok(());
    }
    let body = serde_json::to_string_pretty(&Value::Object(obj)).map_err(|e| e.to_string())? + "\n";
    let tmp = path.with_file_name(format!("{DESKTOP_JSON}.migrate.tmp"));
    std::fs::write(&tmp, body)
        .and_then(|()| std::fs::rename(&tmp, path))
        .map_err(|e| {
            let _ = std::fs::remove_file(&tmp);
            format!("cannot rewrite {}: {e}", path.display())
        })?;
    out.repointed.extend(changes);
    Ok(())
}

fn repoint(obj: &mut Map<String, Value>, d: &Dirs) -> Vec<String> {
    let mut changes = Vec::new();
    let mut set = |obj: &mut Map<String, Value>, key: &str, new: PathBuf| {
        let old = obj.get(key).and_then(Value::as_str).unwrap_or_default().to_owned();
        changes.push(format!("{key}: {old} -> {}", new.display()));
        obj.insert(key.into(), Value::String(new.to_string_lossy().into_owned()));
    };
    if let Some(state) = obj.get("state").and_then(Value::as_str).map(PathBuf::from) {
        if state == d.old_state && moved_away(&d.old_state, &d.new_state) && d.new_state.is_dir() {
            set(obj, "state", d.new_state.clone());
        }
    }
    for key in ["root", "live", "app_dir"] {
        if let Some(new) = obj.get(key).and_then(Value::as_str).and_then(|p| renamed_sibling(Path::new(p))) {
            set(obj, key, new);
        }
    }
    changes
}

/// `path`'s sibling with one `BrainHub` component replaced by `ATLAS`, when `path` is missing on
/// disk and that sibling exists (the first such, from the left); otherwise None.
pub(crate) fn renamed_sibling(path: &Path) -> Option<PathBuf> {
    if path.as_os_str().is_empty() || path.exists() {
        return None;
    }
    let parts: Vec<_> = path.components().collect();
    (0..parts.len()).filter(|&i| parts[i].as_os_str() == LEGACY_ROOT_NAME).find_map(|i| {
        let candidate: PathBuf = parts.iter().enumerate()
            .map(|(j, c)| if j == i { std::ffi::OsStr::new(ROOT_NAME) } else { c.as_os_str() })
            .collect();
        candidate.exists().then_some(candidate)
    })
}

/// Remove the pre-rename login entry when it is there (and, on Linux, ours). True when it removed
/// one. Never with an override set: test runs never touch the real login entries.
#[cfg(not(windows))]
pub(crate) fn remove_legacy_autostart(env: Env) -> std::io::Result<bool> {
    if overridden(env) {
        return Ok(false);
    }
    let Some(home) = config::home(env) else { return Ok(false) };
    // auto-launch's folder: `~/.config/autostart`, whatever XDG_CONFIG_HOME says.
    let entry = home.join(".config").join("autostart").join(format!("{LEGACY_AUTOSTART}.desktop"));
    let Ok(text) = std::fs::read_to_string(&entry) else { return Ok(false) };
    if !text.lines().any(|l| l.trim() == format!("Name={LEGACY_AUTOSTART}")) {
        return Ok(false); // not the one our old shell wrote
    }
    std::fs::remove_file(&entry)?;
    Ok(true)
}

/// Windows: the HKCU Run value, through `reg.exe` (no extra crate), with no console window.
#[cfg(windows)]
pub(crate) fn remove_legacy_autostart(env: Env) -> std::io::Result<bool> {
    use std::os::windows::process::CommandExt;
    use std::process::{Command, Stdio};
    if overridden(env) {
        return Ok(false);
    }
    const RUN: &str = r"HKCU\Software\Microsoft\Windows\CurrentVersion\Run";
    let root = std::env::var_os("SystemRoot").unwrap_or_else(|| "C:\\Windows".into());
    let reg = |args: &[&str]| {
        Command::new(Path::new(&root).join("System32").join("reg.exe"))
            .args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .creation_flags(crate::supervisor::CREATE_NO_WINDOW)
            .status()
    };
    if !reg(&["query", RUN, "/v", LEGACY_AUTOSTART])?.success() {
        return Ok(false);
    }
    let deleted = reg(&["delete", RUN, "/v", LEGACY_AUTOSTART, "/f"])?;
    if !deleted.success() {
        return Err(std::io::Error::other(format!("reg delete: {deleted}")));
    }
    Ok(true)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::ffi::OsString;
    use std::sync::atomic::{AtomicUsize, Ordering};

    fn temp(name: &str) -> PathBuf {
        static N: AtomicUsize = AtomicUsize::new(0);
        let dir = std::env::temp_dir()
            .join(format!("atlas-migrate-{name}-{}-{}", std::process::id(), N.fetch_add(1, Ordering::SeqCst)));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        dir
    }

    /// A fake environment with only a home folder (and the given extras).
    fn env_for(home: &Path, extra: &[(&str, &str)]) -> impl Fn(&str) -> Option<OsString> {
        let mut vars: Vec<(String, OsString)> = vec![
            ("HOME".into(), home.into()),
            ("USERPROFILE".into(), home.into()),
            ("LOCALAPPDATA".into(), home.join("AppData").join("Local").into()),
        ];
        vars.extend(extra.iter().map(|(k, v)| (k.to_string(), OsString::from(v))));
        move |name| vars.iter().find(|(k, _)| k == name).map(|(_, v)| v.clone())
    }

    struct Setup {
        dir: PathBuf,
        home: PathBuf,
        d: Dirs,
    }

    /// A home with the pre-rename folders: the state folder (hub.log, runtime/) and desktop.json in
    /// runtime mode, naming `<dir>/mnt/BrainHub` as root and live copy.
    fn legacy_home(name: &str) -> Setup {
        let dir = temp(name);
        let home = dir.join("home");
        let d = dirs(&env_for(&home, &[])).unwrap();
        std::fs::create_dir_all(d.old_state.join("runtime")).unwrap();
        std::fs::write(d.old_state.join("hub.log"), "old line\n").unwrap();
        std::fs::create_dir_all(&d.old_cfg).unwrap();
        let root = dir.join("mnt").join(LEGACY_ROOT_NAME);
        // Port 1: nothing answers there, so the "old hub still running" check never fires by accident.
        let body = serde_json::json!({"port": 1, "root": root, "live": root, "state": d.old_state});
        std::fs::write(d.old_cfg.join(DESKTOP_JSON), body.to_string()).unwrap();
        Setup { dir, home, d }
    }

    fn json(path: &Path) -> Value {
        serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap()
    }

    #[test]
    fn moves_the_folders_and_rewrites_desktop_json() {
        let s = legacy_home("move");
        std::fs::create_dir_all(s.dir.join("mnt").join(ROOT_NAME)).unwrap(); // the owner renamed the root
        let out = migrate_in(&env_for(&s.home, &[]));
        assert!(out.ran && out.error.is_none(), "{out:?}");
        let new_json = s.d.new_cfg.join(DESKTOP_JSON);
        assert_eq!(out.config.as_deref(), Some(new_json.as_path()));
        assert!(!s.d.old_cfg.exists() || cfg!(windows));
        assert!(s.d.new_state.join("runtime").is_dir());
        // The old state folder's paths keep working until the hub repairs the hooks.
        assert!(is_compat(&s.d.old_state, &s.d.new_state), "the compat path");
        #[cfg(not(windows))]
        assert_eq!(std::fs::read_link(&s.d.old_state).unwrap(), s.d.new_state);
        #[cfg(not(windows))]
        assert!(s.d.old_state.join("hub.log").is_file(), "reachable through it");
        let cfg = json(&new_json);
        let root = s.dir.join("mnt").join(ROOT_NAME);
        assert_eq!(cfg["state"], Value::String(s.d.new_state.to_string_lossy().into()));
        assert_eq!(cfg["root"], Value::String(root.to_string_lossy().into()));
        assert_eq!(cfg["live"], cfg["root"]);
        assert_eq!(cfg["port"], 1);
        assert_eq!(out.repointed.len(), 3, "{out:?}");
        let record = json(&s.d.new_state.join("migrated.json"));
        assert_eq!(record["from"], LEGACY_PACKAGE);
        assert_eq!(record["moved"].as_array().unwrap().len(), if cfg!(windows) { 1 } else { 2 });
        let log = std::fs::read_to_string(s.d.new_state.join("hub.log")).unwrap();
        assert!(log.starts_with("old line\n") && log.contains("migrated from BrainHub: moved "), "{log}");
        // What the shell then reads.
        let read = config::load(&new_json);
        assert_eq!(read.state.as_deref(), Some(s.d.new_state.as_path()));
        assert_eq!(read.root.as_deref(), Some(root.as_path()));
        let _ = std::fs::remove_dir_all(&s.dir);
    }

    #[test]
    fn an_unrenamed_root_keeps_its_old_path() {
        let s = legacy_home("keep-root");
        let old_root = s.dir.join("mnt").join(LEGACY_ROOT_NAME);
        std::fs::create_dir_all(&old_root).unwrap();
        let out = migrate_in(&env_for(&s.home, &[]));
        assert!(out.error.is_none(), "{out:?}");
        let cfg = json(out.config.as_deref().unwrap());
        assert_eq!(cfg["root"], Value::String(old_root.to_string_lossy().into()));
        assert_eq!(out.repointed.len(), 1, "only the state: {out:?}");
        let _ = std::fs::remove_dir_all(&s.dir);
    }

    #[test]
    fn root_sibling_swap() {
        let dir = temp("sibling");
        let old = dir.join(LEGACY_ROOT_NAME);
        let new = dir.join(ROOT_NAME);
        // Missing, and no sibling either: nothing to swap to.
        assert_eq!(renamed_sibling(&old), None);
        std::fs::create_dir_all(new.join("app")).unwrap();
        assert_eq!(renamed_sibling(&old), Some(new.clone()));
        // One component, anywhere in the path (legacy mode's app_dir).
        assert_eq!(renamed_sibling(&old.join("app")), Some(new.join("app")));
        assert_eq!(renamed_sibling(&old.join("nope")), None);
        // A path that exists is never swapped; nor one without the old name.
        std::fs::create_dir_all(&old).unwrap();
        assert_eq!(renamed_sibling(&old), None);
        assert_eq!(renamed_sibling(&dir.join("BrainHubX")), None);
        assert_eq!(renamed_sibling(Path::new("")), None);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn no_run_with_an_override_a_new_file_or_no_legacy_file() {
        for extra in [("ATLAS_CONFIG_DIR", "/tmp/atlas-cfg-x"), ("ATLAS_STATE_DIR", "/tmp/atlas-state-x")] {
            let s = legacy_home("override");
            let out = migrate_in(&env_for(&s.home, &[extra]));
            assert!(!out.ran && out.moved.is_empty(), "{extra:?}");
            assert!(s.d.old_state.is_dir() && s.d.old_cfg.join(DESKTOP_JSON).is_file());
            let _ = std::fs::remove_dir_all(&s.dir);
        }
        // Empty counts as unset (as in config.rs): it runs.
        let s = legacy_home("override-empty");
        assert!(migrate_in(&env_for(&s.home, &[("ATLAS_STATE_DIR", "")])).ran);
        let _ = std::fs::remove_dir_all(&s.dir);
        // The new desktop.json is there already: the legacy folders stay as they are.
        let s = legacy_home("has-new");
        std::fs::create_dir_all(&s.d.new_cfg).unwrap();
        std::fs::write(s.d.new_cfg.join(DESKTOP_JSON), "{}").unwrap();
        let out = migrate_in(&env_for(&s.home, &[]));
        assert!(!out.ran && s.d.old_state.is_dir());
        assert_eq!(out.config, Some(s.d.new_cfg.join(DESKTOP_JSON)));
        let _ = std::fs::remove_dir_all(&s.dir);
        // Nothing legacy at all: nothing happens, nothing is created.
        let dir = temp("fresh");
        let out = migrate_in(&env_for(&dir.join("home"), &[]));
        assert!(!out.ran && !dir.join("home").exists());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn an_existing_target_is_not_clobbered() {
        let s = legacy_home("clobber");
        std::fs::create_dir_all(&s.d.new_state).unwrap();
        std::fs::write(s.d.new_state.join("keep.txt"), "mine").unwrap();
        let out = migrate_in(&env_for(&s.home, &[]));
        assert!(out.ran && out.error.as_deref().unwrap_or_default().contains("isn't empty"), "{out:?}");
        assert_eq!(std::fs::read_to_string(s.d.new_state.join("keep.txt")).unwrap(), "mine");
        assert!(s.d.old_state.join("runtime").is_dir() && out.moved.is_empty());
        // The shell runs on the legacy file, unchanged, which still matches the disk.
        let legacy_json = s.d.old_cfg.join(DESKTOP_JSON);
        assert_eq!(out.config.as_deref(), Some(legacy_json.as_path()));
        assert_eq!(config::load(&legacy_json).state.as_deref(), Some(s.d.old_state.as_path()));
        // The error went to the log in the folder in use (the new one exists, so there).
        let log = std::fs::read_to_string(s.d.new_state.join("hub.log")).unwrap();
        assert!(log.contains("migration from BrainHub stopped"), "{log}");
        let _ = std::fs::remove_dir_all(&s.dir);
    }

    #[test]
    fn an_empty_target_is_replaced() {
        let s = legacy_home("empty-target");
        std::fs::create_dir_all(&s.d.new_state).unwrap();
        let out = migrate_in(&env_for(&s.home, &[]));
        assert!(out.error.is_none(), "{out:?}");
        assert!(s.d.new_state.join("runtime").is_dir());
        let _ = std::fs::remove_dir_all(&s.dir);
    }

    /// Linux: the state folder moves, the config folder can't (its target is in the way). The
    /// legacy desktop.json, where it is, names the moved state folder; a later start finishes.
    #[cfg(not(windows))]
    #[test]
    fn a_failed_second_move_leaves_a_usable_legacy_config_and_a_later_start_finishes() {
        let s = legacy_home("half");
        std::fs::create_dir_all(&s.d.new_cfg).unwrap();
        std::fs::write(s.d.new_cfg.join("other.json"), "{}").unwrap();
        let out = migrate_in(&env_for(&s.home, &[]));
        assert!(out.error.is_some() && out.moved.len() == 1, "{out:?}");
        let legacy_json = s.d.old_cfg.join(DESKTOP_JSON);
        assert_eq!(out.config.as_deref(), Some(legacy_json.as_path()));
        let read = out.fix(config::load(&legacy_json));
        assert_eq!(read.state.as_deref(), Some(s.d.new_state.as_path()));
        assert!(read.state.unwrap().join("runtime").is_dir());
        // The owner clears the way: the next start finishes, and the one after does nothing.
        std::fs::remove_file(s.d.new_cfg.join("other.json")).unwrap();
        let again = migrate_in(&env_for(&s.home, &[]));
        assert!(again.ran && again.error.is_none() && again.moved.len() == 1, "{again:?}");
        assert!(again.repointed.is_empty(), "already rewritten: {again:?}");
        assert_eq!(config::load(&s.d.new_cfg.join(DESKTOP_JSON)).state.as_deref(), Some(s.d.new_state.as_path()));
        let third = migrate_in(&env_for(&s.home, &[]));
        assert!(!third.ran && third.moved.is_empty());
        let _ = std::fs::remove_dir_all(&s.dir);
    }

    /// Writes `desktop.json`'s `root` and `live` (keeping the rest).
    fn set_root(path: &Path, root: &Path) {
        let mut cfg = json(path);
        cfg["root"] = Value::String(root.to_string_lossy().into());
        cfg["live"] = cfg["root"].clone();
        std::fs::write(path, cfg.to_string()).unwrap();
    }

    #[test]
    fn the_root_renamed_after_the_migration_is_followed_at_the_next_start() {
        let s = legacy_home("late-rename");
        let old_root = s.dir.join("mnt").join(LEGACY_ROOT_NAME);
        std::fs::create_dir_all(&old_root).unwrap(); // not renamed yet at the first start
        let first = start_in(&env_for(&s.home, &[]));
        assert!(first.ran && first.error.is_none(), "{first:?}");
        let new_json = s.d.new_cfg.join(DESKTOP_JSON);
        assert_eq!(config::load(&new_json).root.as_deref(), Some(old_root.as_path()));
        let new_root = s.dir.join("mnt").join(ROOT_NAME);
        std::fs::rename(&old_root, &new_root).unwrap(); // the owner's `mv`
        let next = start_in(&env_for(&s.home, &[]));
        assert!(!next.ran && next.repointed.len() == 2, "root and live: {next:?}");
        let read = next.fix(config::load(&new_json));
        assert_eq!(read.root.as_deref(), Some(new_root.as_path()));
        assert_eq!(read.live.as_deref(), Some(new_root.as_path()));
        let log = std::fs::read_to_string(s.d.new_state.join("hub.log")).unwrap();
        assert!(log.contains("repointed root: "), "{log}");
        // And then nothing more.
        assert!(start_in(&env_for(&s.home, &[])).repointed.is_empty());
        let _ = std::fs::remove_dir_all(&s.dir);
    }

    #[test]
    fn a_drive_missing_at_the_first_start_is_followed_once_it_is_there() {
        let s = legacy_home("no-mount");
        let first = start_in(&env_for(&s.home, &[])); // neither root exists: nothing to swap to
        assert!(first.ran && first.error.is_none(), "{first:?}");
        assert_eq!(first.repointed.len(), 1, "only the state: {first:?}");
        let new_root = s.dir.join("mnt").join(ROOT_NAME);
        std::fs::create_dir_all(&new_root).unwrap(); // mounted, already renamed
        let next = start_in(&env_for(&s.home, &[]));
        assert_eq!(config::load(&s.d.new_cfg.join(DESKTOP_JSON)).root.as_deref(), Some(new_root.as_path()));
        assert_eq!(next.repointed.len(), 2, "{next:?}");
        let _ = std::fs::remove_dir_all(&s.dir);
    }

    #[test]
    fn the_legacy_file_in_use_is_repointed_too_and_overrides_never_are() {
        // The state move stopped early (its target is in the way): the legacy desktop.json is in use.
        let s = legacy_home("legacy-in-use");
        std::fs::create_dir_all(&s.d.new_state).unwrap();
        std::fs::write(s.d.new_state.join("keep.txt"), "mine").unwrap();
        let new_root = s.dir.join("mnt").join(ROOT_NAME);
        std::fs::create_dir_all(&new_root).unwrap();
        let out = start_in(&env_for(&s.home, &[]));
        let legacy_json = s.d.old_cfg.join(DESKTOP_JSON);
        assert!(out.error.is_some() && out.config.as_deref() == Some(legacy_json.as_path()), "{out:?}");
        assert_eq!(config::load(&legacy_json).root.as_deref(), Some(new_root.as_path()));
        assert_eq!(config::load(&legacy_json).state.as_deref(), Some(s.d.old_state.as_path()), "not moved");
        // With an override, a file naming a missing BrainHub path is left exactly as it is.
        let cfg_dir = s.dir.join("override-cfg");
        std::fs::create_dir_all(&cfg_dir).unwrap();
        set_root(&legacy_json, &s.dir.join("mnt").join(LEGACY_ROOT_NAME));
        std::fs::copy(&legacy_json, cfg_dir.join(DESKTOP_JSON)).unwrap();
        let before = std::fs::read_to_string(cfg_dir.join(DESKTOP_JSON)).unwrap();
        let cfg_var = cfg_dir.to_string_lossy().into_owned();
        let out = start_in(&env_for(&s.home, &[("ATLAS_CONFIG_DIR", &cfg_var)]));
        assert!(!out.ran && out.repointed.is_empty() && !out.swap_siblings);
        assert_eq!(std::fs::read_to_string(cfg_dir.join(DESKTOP_JSON)).unwrap(), before);
        let read = out.fix(config::load(&cfg_dir.join(DESKTOP_JSON)));
        assert_eq!(read.root.as_deref(), Some(s.dir.join("mnt").join(LEGACY_ROOT_NAME).as_path()));
        let _ = std::fs::remove_dir_all(&s.dir);
    }

    #[test]
    fn fix_swaps_a_missing_root_in_memory() {
        let dir = temp("fix-root");
        std::fs::create_dir_all(dir.join(ROOT_NAME)).unwrap();
        let cfg = config::DesktopConfig { root: Some(dir.join(LEGACY_ROOT_NAME)), ..config::DesktopConfig::default() };
        let on = Outcome { swap_siblings: true, ..Outcome::default() };
        assert_eq!(on.fix(cfg.clone()).root, Some(dir.join(ROOT_NAME)));
        assert_eq!(Outcome::default().fix(cfg.clone()).root, cfg.root);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn nothing_moves_while_the_old_hub_answers() {
        let s = legacy_home("running-port");
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let mut cfg = json(&s.d.old_cfg.join(DESKTOP_JSON));
        cfg["port"] = listener.local_addr().unwrap().port().into();
        std::fs::write(s.d.old_cfg.join(DESKTOP_JSON), cfg.to_string()).unwrap();
        let out = migrate_in(&env_for(&s.home, &[]));
        assert!(!out.ran && out.error.as_deref().unwrap_or_default().contains("Quit BrainHub first"), "{out:?}");
        assert!(s.d.old_state.join("runtime").is_dir() && !s.d.new_state.exists());
        assert_eq!(out.config, Some(s.d.old_cfg.join(DESKTOP_JSON)));
        assert!(std::fs::read_to_string(s.d.old_state.join("hub.log")).unwrap().contains("Quit BrainHub first"));
        drop(listener);
        let _ = std::fs::remove_dir_all(&s.dir);
    }

    #[test]
    fn nothing_moves_while_the_old_update_step_holds_its_lock() {
        let s = legacy_home("running-lock");
        let lock = std::fs::File::create(s.d.old_state.join("runtime").join("update.lock")).unwrap();
        lock.try_lock().unwrap();
        let out = migrate_in(&env_for(&s.home, &[]));
        assert!(!out.ran && out.error.as_deref().unwrap_or_default().contains("update.lock"), "{out:?}");
        assert!(s.d.old_state.join("runtime").is_dir());
        lock.unlock().unwrap();
        assert!(migrate_in(&env_for(&s.home, &[])).ran, "once released, it runs");
        let _ = std::fs::remove_dir_all(&s.dir);
    }

    #[test]
    fn it_is_idempotent() {
        let s = legacy_home("twice");
        assert!(migrate_in(&env_for(&s.home, &[])).error.is_none());
        let before = std::fs::read_to_string(s.d.new_cfg.join(DESKTOP_JSON)).unwrap();
        // The compat path at the old state folder never looks like a legacy install again.
        let out = start_in(&env_for(&s.home, &[]));
        assert!(!out.ran && out.moved.is_empty() && out.repointed.is_empty() && out.error.is_none(), "{out:?}");
        assert_eq!(std::fs::read_to_string(s.d.new_cfg.join(DESKTOP_JSON)).unwrap(), before);
        assert!(is_compat(&s.d.old_state, &s.d.new_state));
        let _ = std::fs::remove_dir_all(&s.dir);
    }

    #[test]
    fn a_state_swap_applies_only_while_the_old_folder_is_gone() {
        let dir = temp("swap");
        let (old, new) = (dir.join("old"), dir.join("new"));
        std::fs::create_dir_all(&new).unwrap();
        let out = Outcome { state_swap: Some((old.clone(), new.clone())), ..Outcome::default() };
        let cfg = config::DesktopConfig { state: Some(old.clone()), ..config::DesktopConfig::default() };
        assert_eq!(out.fix(cfg.clone()).state, Some(new.clone()));
        std::fs::create_dir_all(&old).unwrap();
        assert_eq!(out.fix(cfg).state, Some(old));
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[cfg(not(windows))]
    #[test]
    fn the_legacy_autostart_entry_is_removed_when_ours() {
        let dir = temp("autostart");
        let home = dir.join("home");
        let folder = home.join(".config").join("autostart");
        std::fs::create_dir_all(&folder).unwrap();
        let entry = folder.join(format!("{LEGACY_AUTOSTART}.desktop"));
        let ours = format!("[Desktop Entry]\nType=Application\nName={LEGACY_AUTOSTART}\nExec=/x/brainhub-desktop\n");
        // An override (a test run) never touches it.
        std::fs::write(&entry, &ours).unwrap();
        assert!(!remove_legacy_autostart(&env_for(&home, &[("ATLAS_STATE_DIR", "/tmp/s")])).unwrap());
        assert!(entry.exists());
        // Someone else's file of that name stays.
        std::fs::write(&entry, "[Desktop Entry]\nName=Something else\n").unwrap();
        assert!(!remove_legacy_autostart(&env_for(&home, &[])).unwrap());
        assert!(entry.exists());
        // Ours goes, once.
        std::fs::write(&entry, &ours).unwrap();
        assert!(remove_legacy_autostart(&env_for(&home, &[])).unwrap());
        assert!(!entry.exists());
        assert!(!remove_legacy_autostart(&env_for(&home, &[])).unwrap());
        // A new entry of our own is never touched.
        let new_entry = folder.join("ATLAS.desktop");
        std::fs::write(&new_entry, "[Desktop Entry]\nName=ATLAS\n").unwrap();
        assert!(!remove_legacy_autostart(&env_for(&home, &[])).unwrap());
        assert!(new_entry.exists());
        let _ = std::fs::remove_dir_all(&dir);
    }
}
