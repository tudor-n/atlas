//! `desktop.json`, written by `python -m atlas desktop-config`, and the per-OS folders (P10):
//! the config folder holds `desktop.json`, the state folder `hub.log` (in runtime mode,
//! `desktop.json`'s state folder does). Both follow the rules of `atlas/osdirs.py`, env
//! overrides included. Also Claude Code's settings file, which the shell's claude-sync watch polls.
//!
//! A missing or invalid file gives the defaults: port 8090 and nothing to start, so the shell
//! only connects to an already running hub.
//!
//! Two ways to start a hub (see `desktop_config.py`):
//! - legacy (dev): `{port, root, python, app_dir}`: `<python> -m atlas` in `app_dir`;
//! - runtime (plan decision P2): `{port, root, live, state}`: before each start the update step
//!   refreshes the runtime slots under `<state>/runtime` from the live copy, and the hub runs from
//!   the slot `current.json` names.

use serde::Deserialize;
use std::ffi::OsString;
use std::path::{Path, PathBuf};

pub const DEFAULT_PORT: u16 = 8090;

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(default)]
pub struct DesktopConfig {
    pub port: u16,
    pub root: Option<PathBuf>,
    pub python: Option<PathBuf>,
    pub app_dir: Option<PathBuf>,
    /// Runtime mode: the live copy the update step pulls and builds slots from.
    pub live: Option<PathBuf>,
    /// Runtime mode: the per-OS state folder holding `runtime/` and `update.json`.
    pub state: Option<PathBuf>,
}

impl Default for DesktopConfig {
    fn default() -> Self {
        DesktopConfig { port: DEFAULT_PORT, root: None, python: None, app_dir: None, live: None, state: None }
    }
}

/// How the supervisor starts a hub, from what the file holds.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Mode {
    /// Nothing to start: connect to the port only.
    ConnectOnly,
    Legacy { root: PathBuf, python: PathBuf, app_dir: PathBuf },
    Runtime { root: PathBuf, live: PathBuf, state: PathBuf },
}

impl DesktopConfig {
    /// Runtime mode wins when the file holds both shapes (`desktop-config` writes one or the other).
    pub fn mode(&self) -> Mode {
        match (&self.root, &self.live, &self.state, &self.python, &self.app_dir) {
            (Some(root), Some(live), Some(state), _, _) => {
                Mode::Runtime { root: root.clone(), live: live.clone(), state: state.clone() }
            }
            (Some(root), _, _, Some(python), Some(app_dir)) => {
                Mode::Legacy { root: root.clone(), python: python.clone(), app_dir: app_dir.clone() }
            }
            _ => Mode::ConnectOnly,
        }
    }

    /// The supervisor may start a hub only when the file says how.
    pub fn can_spawn(&self) -> bool {
        self.mode() != Mode::ConnectOnly
    }

    pub fn hub_url(&self, path: &str) -> String {
        format!("http://127.0.0.1:{}{}", self.port, path)
    }
}

/// Reads an environment variable (tests pass a fake environment).
type Env<'a> = &'a dyn Fn(&str) -> Option<OsString>;

fn real_env(name: &str) -> Option<OsString> {
    std::env::var_os(name)
}

/// An unset or empty variable gives no value.
fn env_path(env: Env, name: &str) -> Option<PathBuf> {
    env(name).filter(|v| !v.is_empty()).map(PathBuf::from)
}

#[cfg(windows)]
const HOME_VAR: &str = "USERPROFILE";
#[cfg(not(windows))]
const HOME_VAR: &str = "HOME";

fn home(env: Env) -> Option<PathBuf> {
    env_path(env, HOME_VAR).or_else(std::env::home_dir)
}

/// `$override_var`, else `%LOCALAPPDATA%\ATLAS` on Windows and `${xdg_var:-~/xdg_default}/atlas`
/// elsewhere. As in osdirs.py, an empty or relative XDG value falls back to the default.
fn os_dir(env: Env, override_var: &str, xdg_var: &str, xdg_default: &str) -> Option<PathBuf> {
    if let Some(dir) = env_path(env, override_var) {
        return Some(dir);
    }
    #[cfg(windows)]
    {
        let _ = (xdg_var, xdg_default);
        let local = env_path(env, "LOCALAPPDATA").or_else(|| home(env).map(|h| h.join("AppData").join("Local")))?;
        Some(local.join("ATLAS"))
    }
    #[cfg(not(windows))]
    {
        let base = match env_path(env, xdg_var) {
            Some(dir) if dir.is_absolute() => dir,
            _ => home(env)?.join(xdg_default),
        };
        Some(base.join("atlas"))
    }
}

fn default_path_in(env: Env) -> Option<PathBuf> {
    os_dir(env, "ATLAS_CONFIG_DIR", "XDG_CONFIG_HOME", ".config").map(|d| d.join("desktop.json"))
}

fn state_dir_in(env: Env) -> Option<PathBuf> {
    os_dir(env, "ATLAS_STATE_DIR", "XDG_DATA_HOME", ".local/share")
}

fn claude_settings_in(env: Env) -> Option<PathBuf> {
    let dir = match env_path(env, "CLAUDE_CONFIG_DIR") {
        Some(dir) => dir,
        None => home(env)?.join(".claude"),
    };
    Some(dir.join("settings.json"))
}

/// Claude Code's own settings file, which the shell's claude-sync watch polls (ruling D12):
/// `$CLAUDE_CONFIG_DIR/settings.json` (as Claude Code and `claude_sync.py` read it), else
/// `~/.claude/settings.json` (`%USERPROFILE%\.claude\settings.json` on Windows).
pub fn claude_settings_path() -> Option<PathBuf> {
    claude_settings_in(&real_env)
}

/// `desktop.json` in the config folder: `$ATLAS_CONFIG_DIR`, else
/// `${XDG_CONFIG_HOME:-~/.config}/atlas` on Linux and `%LOCALAPPDATA%\ATLAS` on Windows.
pub fn default_path() -> Option<PathBuf> {
    default_path_in(&real_env)
}

/// The per-OS state folder: `$ATLAS_STATE_DIR`, else `${XDG_DATA_HOME:-~/.local/share}/atlas`
/// on Linux and `%LOCALAPPDATA%\ATLAS` on Windows.
pub fn state_dir() -> Option<PathBuf> {
    state_dir_in(&real_env)
}

pub fn load(path: &Path) -> DesktopConfig {
    std::fs::read_to_string(path)
        .ok()
        .and_then(|text| serde_json::from_str(text.trim_start_matches('\u{feff}')).ok())
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn temp_file(name: &str, body: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("bh-desktop-test-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let f = dir.join(name);
        std::fs::write(&f, body).unwrap();
        f
    }

    #[test]
    fn config_missing_file_defaults() {
        let c = load(Path::new("Z:\\definitely\\not\\here\\desktop.json"));
        assert_eq!(c.port, 8090);
        assert_eq!(c, DesktopConfig::default());
        assert!(!c.can_spawn());
    }

    #[test]
    fn config_parses_file() {
        let f = temp_file(
            "ok.json",
            r#"{"port": 8099, "root": "C:\\bh\\root", "python": "C:\\py\\pythonw.exe", "app_dir": "D:\\ATLAS\\app"}"#,
        );
        let c = load(&f);
        assert_eq!(c.port, 8099);
        assert_eq!(c.root, Some(PathBuf::from("C:\\bh\\root")));
        assert_eq!(c.python, Some(PathBuf::from("C:\\py\\pythonw.exe")));
        assert_eq!(c.app_dir, Some(PathBuf::from("D:\\ATLAS\\app")));
        assert!(c.can_spawn());
        assert_eq!(c.hub_url("/widget"), "http://127.0.0.1:8099/widget");
    }

    #[test]
    fn config_parses_runtime_file() {
        let f = temp_file(
            "runtime.json",
            r#"{"port": 8098, "root": "/mnt/data/ATLAS", "live": "/mnt/data/ATLAS", "state": "/home/u/.local/share/atlas"}"#,
        );
        let c = load(&f);
        assert_eq!(c.port, 8098);
        assert_eq!(c.mode(), Mode::Runtime {
            root: PathBuf::from("/mnt/data/ATLAS"),
            live: PathBuf::from("/mnt/data/ATLAS"),
            state: PathBuf::from("/home/u/.local/share/atlas"),
        });
        assert!(c.can_spawn());
        // Without the root (or the state folder) there is nothing to start.
        assert_eq!(load(&temp_file("rt-noroot.json", r#"{"live": "/l", "state": "/s"}"#)).mode(), Mode::ConnectOnly);
        assert_eq!(load(&temp_file("rt-nostate.json", r#"{"root": "/r", "live": "/l"}"#)).mode(), Mode::ConnectOnly);
    }

    #[test]
    fn config_invalid_file_defaults() {
        assert_eq!(load(&temp_file("bad.json", "{ nope")), DesktopConfig::default());
        assert_eq!(load(&temp_file("badport.json", r#"{"port": 70000}"#)), DesktopConfig::default());
    }

    #[test]
    fn config_partial_file_keeps_defaults() {
        let c = load(&temp_file("partial.json", r#"{"root": "C:\\r"}"#));
        assert_eq!(c.port, 8090);
        assert!(!c.can_spawn());
    }

    /// A fake environment: the real one is shared by every test thread.
    fn env<'a>(vars: &'a [(&'a str, &'a str)]) -> impl Fn(&str) -> Option<OsString> + 'a {
        move |name| vars.iter().find(|(k, _)| *k == name).map(|(_, v)| OsString::from(v))
    }

    #[test]
    fn config_path_honours_env_override() {
        let mut vars = [
            ("ATLAS_CONFIG_DIR", "/tmp/bh-cfg"),
            ("XDG_CONFIG_HOME", "/x/config"),
            ("LOCALAPPDATA", "/x/local"),
            ("HOME", "/home/u"),
            ("USERPROFILE", "/home/u"),
        ];
        assert_eq!(default_path_in(&env(&vars)), Some(Path::new("/tmp/bh-cfg").join("desktop.json")));
        // Empty counts as unset: the OS default applies.
        vars[0].1 = "";
        let fallback = default_path_in(&env(&vars)).unwrap();
        assert!(!fallback.starts_with("/tmp/bh-cfg") && fallback.ends_with("desktop.json"), "{}", fallback.display());
    }

    #[cfg(unix)]
    #[test]
    fn config_path_xdg() {
        let default = Some(PathBuf::from("/home/u/.config/atlas/desktop.json"));
        assert_eq!(default_path_in(&env(&[("HOME", "/home/u")])), default);
        let xdg = [("HOME", "/home/u"), ("XDG_CONFIG_HOME", "/x/config")];
        assert_eq!(default_path_in(&env(&xdg)), Some(PathBuf::from("/x/config/atlas/desktop.json")));
        // Empty or relative XDG values fall back to the default (the XDG spec ignores relative ones).
        for bad in ["", "rel/config"] {
            assert_eq!(default_path_in(&env(&[("HOME", "/home/u"), ("XDG_CONFIG_HOME", bad)])), default);
        }
        // LOCALAPPDATA means nothing here.
        assert_eq!(default_path_in(&env(&[("HOME", "/home/u"), ("LOCALAPPDATA", "/x/local")])), default);
    }

    #[cfg(windows)]
    #[test]
    fn config_path_windows() {
        let local = [("LOCALAPPDATA", r"C:\Users\u\AppData\Local"), ("XDG_CONFIG_HOME", r"C:\x")];
        assert_eq!(default_path_in(&env(&local)), Some(PathBuf::from(r"C:\Users\u\AppData\Local\ATLAS\desktop.json")));
        // Without LOCALAPPDATA: the profile's AppData\Local, as osdirs.py does.
        let profile = [("USERPROFILE", r"C:\Users\u")];
        assert_eq!(default_path_in(&env(&profile)), Some(PathBuf::from(r"C:\Users\u\AppData\Local\ATLAS\desktop.json")));
        assert_eq!(state_dir_in(&env(&profile)), Some(PathBuf::from(r"C:\Users\u\AppData\Local\ATLAS")));
    }

    #[test]
    fn state_dir_env_override() {
        let vars = [("ATLAS_STATE_DIR", "/tmp/bh-state"), ("XDG_DATA_HOME", "/x/data"), ("HOME", "/home/u")];
        assert_eq!(state_dir_in(&env(&vars)), Some(PathBuf::from("/tmp/bh-state")));
        // The config override doesn't move the state folder.
        let vars = [("ATLAS_CONFIG_DIR", "/tmp/bh-cfg"), ("HOME", "/home/u"), ("USERPROFILE", "/home/u")];
        assert_ne!(state_dir_in(&env(&vars)), Some(PathBuf::from("/tmp/bh-cfg")));
    }

    #[test]
    fn claude_settings_path_rules() {
        let vars = [("CLAUDE_CONFIG_DIR", "/tmp/bh-claude"), ("HOME", "/home/u"), ("USERPROFILE", "/home/u")];
        assert_eq!(claude_settings_in(&env(&vars)), Some(Path::new("/tmp/bh-claude").join("settings.json")));
        // Empty counts as unset: the home folder's .claude (HOME on unix, USERPROFILE on Windows).
        let vars = [("CLAUDE_CONFIG_DIR", ""), ("HOME", "/home/u"), ("USERPROFILE", "/home/u")];
        assert_eq!(claude_settings_in(&env(&vars)), Some(Path::new("/home/u").join(".claude").join("settings.json")));
        // The ATLAS overrides don't move it.
        let vars = [("ATLAS_CONFIG_DIR", "/tmp/c"), ("ATLAS_STATE_DIR", "/tmp/s"), ("HOME", "/home/u"),
                    ("USERPROFILE", "/home/u")];
        assert_eq!(claude_settings_in(&env(&vars)), Some(Path::new("/home/u").join(".claude").join("settings.json")));
    }

    #[cfg(unix)]
    #[test]
    fn state_dir_xdg() {
        let default = Some(PathBuf::from("/home/u/.local/share/atlas"));
        assert_eq!(state_dir_in(&env(&[("HOME", "/home/u")])), default);
        assert_eq!(state_dir_in(&env(&[("HOME", "/home/u"), ("XDG_DATA_HOME", "/x/data")])),
                   Some(PathBuf::from("/x/data/atlas")));
        assert_eq!(state_dir_in(&env(&[("HOME", "/home/u"), ("XDG_DATA_HOME", "data")])), default);
    }
}
