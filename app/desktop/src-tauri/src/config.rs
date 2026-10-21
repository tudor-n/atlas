//! `%LOCALAPPDATA%\BrainHub\desktop.json`, written by `python -m brainhub desktop-config`.
//!
//! A missing or invalid file gives the defaults: port 8090 and nothing to start, so the shell
//! only connects to an already running hub.

use serde::Deserialize;
use std::path::{Path, PathBuf};

pub const DEFAULT_PORT: u16 = 8090;

#[derive(Debug, Clone, PartialEq, Eq, Deserialize)]
#[serde(default)]
pub struct DesktopConfig {
    pub port: u16,
    pub root: Option<PathBuf>,
    pub python: Option<PathBuf>,
    pub app_dir: Option<PathBuf>,
}

impl Default for DesktopConfig {
    fn default() -> Self {
        DesktopConfig { port: DEFAULT_PORT, root: None, python: None, app_dir: None }
    }
}

impl DesktopConfig {
    /// The supervisor may start a hub only when the file says how.
    pub fn can_spawn(&self) -> bool {
        self.root.is_some() && self.python.is_some() && self.app_dir.is_some()
    }

    pub fn hub_url(&self, path: &str) -> String {
        format!("http://127.0.0.1:{}{}", self.port, path)
    }
}

/// `%LOCALAPPDATA%\BrainHub\desktop.json`, read from the environment so a test run can point it
/// elsewhere.
pub fn default_path() -> Option<PathBuf> {
    std::env::var_os("LOCALAPPDATA").map(|d| PathBuf::from(d).join("BrainHub").join("desktop.json"))
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
            r#"{"port": 8099, "root": "C:\\bh\\root", "python": "C:\\py\\pythonw.exe", "app_dir": "D:\\BrainHub\\app"}"#,
        );
        let c = load(&f);
        assert_eq!(c.port, 8099);
        assert_eq!(c.root, Some(PathBuf::from("C:\\bh\\root")));
        assert_eq!(c.python, Some(PathBuf::from("C:\\py\\pythonw.exe")));
        assert_eq!(c.app_dir, Some(PathBuf::from("D:\\BrainHub\\app")));
        assert!(c.can_spawn());
        assert_eq!(c.hub_url("/widget"), "http://127.0.0.1:8099/widget");
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
}
