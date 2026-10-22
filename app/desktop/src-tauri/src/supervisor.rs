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
    child: Option<Child>,
    started_at: Option<Instant>,
    attempt: u32,
    not_before: Option<Instant>,
    stopped: bool,
    last_up: bool,
}

impl Supervisor {
    pub fn new(cfg: DesktopConfig) -> Self {
        Supervisor { cfg, child: None, started_at: None, attempt: 0, not_before: None, stopped: false, last_up: false }
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

    /// Kill our hub (if any) and start a fresh one now.
    pub fn restart(&mut self) {
        self.kill_child();
        self.stopped = false;
        self.attempt = 0;
        self.not_before = None;
        if self.cfg.can_spawn() {
            self.spawn();
        }
    }

    /// Kill only the hub this supervisor started, and start no more.
    pub fn stop(&mut self) {
        self.stopped = true;
        self.kill_child();
    }

    fn kill_child(&mut self) {
        if let Some(mut c) = self.child.take() {
            let _ = c.kill();
            let _ = c.wait();
            self.log("hub stopped by the desktop shell");
        }
        self.started_at = None;
    }

    /// If our child has exited, forget it and schedule the next start with backoff.
    fn reap(&mut self) {
        let exited = match self.child.as_mut().map(|c| c.try_wait()) {
            Some(Ok(Some(status))) => Some(status.to_string()),
            Some(Err(e)) => Some(format!("unknown status ({e})")),
            _ => None,
        };
        if let Some(status) = exited {
            self.child = None;
            if self.started_at.take().is_some_and(|t| t.elapsed() >= HEALTHY_UPTIME) {
                self.attempt = 0;
            }
            self.schedule_retry(&format!("hub exited: {status}"));
        }
    }

    fn schedule_retry(&mut self, why: &str) {
        let delay = backoff(self.attempt);
        self.attempt = self.attempt.saturating_add(1);
        self.not_before = Some(Instant::now() + delay);
        self.log(&format!("{why}; next start in {} s", delay.as_secs()));
    }

    fn data_dir(&self) -> Option<PathBuf> {
        self.cfg.root.as_ref().map(|r| r.join("data"))
    }

    fn open_log(&self) -> Option<File> {
        let dir = self.data_dir()?;
        std::fs::create_dir_all(&dir).ok()?;
        OpenOptions::new().create(true).append(true).open(dir.join("hub.log")).ok()
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
            self.schedule_retry(&format!("cannot open {}", root.join("data").join("hub.log").display()));
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
        let mut s = Supervisor::new(DesktopConfig { port: free_port(), ..DesktopConfig::default() });
        s.tick();
        assert!(!s.owns_child());
    }

    #[test]
    fn listener_on_port_means_connect_only() {
        let l = TcpListener::bind("127.0.0.1:0").unwrap();
        let root = std::env::temp_dir().join(format!("bh-sup-busy-{}", std::process::id()));
        let mut s = Supervisor::new(cfg(l.local_addr().unwrap().port(), &root));
        s.tick();
        assert!(s.last_up());
        assert!(!s.owns_child());
        let _ = std::fs::remove_dir_all(&root);
    }

    #[cfg(windows)]
    #[test]
    fn spawns_logs_and_stops_own_child() {
        let root = std::env::temp_dir().join(format!("bh-sup-spawn-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        // cmd.exe stands in for python: it starts, and may exit at once on its null stdin.
        let mut c = cfg(free_port(), &root);
        c.python = Some(PathBuf::from("C:\\Windows\\System32\\cmd.exe"));
        let mut s = Supervisor::new(c);
        s.tick();
        assert!(root.join("data").join("hub.log").exists());
        let log = std::fs::read_to_string(root.join("data").join("hub.log")).unwrap();
        assert!(log.contains("-m brainhub --root"), "{log}");
        assert!(log.contains(&format!("--port {}", s.cfg.port)), "{log}");
        s.stop();
        assert!(!s.owns_child());
        s.tick(); // stopped: never starts again
        assert!(!s.owns_child());
        let _ = std::fs::remove_dir_all(&root);
    }

    #[cfg(unix)]
    #[test]
    fn spawns_logs_and_stops_own_child_unix() {
        let root = std::env::temp_dir().join(format!("bh-sup-spawn-unix-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&root);
        // /bin/sh stands in for python: it starts, and may exit at once on its null stdin.
        let mut c = cfg(free_port(), &root);
        c.python = Some(PathBuf::from("/bin/sh"));
        let mut s = Supervisor::new(c);
        s.tick();
        assert!(root.join("data").join("hub.log").exists());
        let log = std::fs::read_to_string(root.join("data").join("hub.log")).unwrap();
        assert!(log.contains("-m brainhub --root"), "{log}");
        assert!(log.contains(&format!("--port {}", s.cfg.port)), "{log}");
        s.stop();
        assert!(!s.owns_child());
        s.tick(); // stopped: never starts again
        assert!(!s.owns_child());
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn failed_start_backs_off() {
        let root = std::env::temp_dir().join(format!("bh-sup-fail-{}", std::process::id()));
        let mut s = Supervisor::new(cfg(free_port(), &root));
        s.tick(); // python-does-not-exist.exe: spawn fails
        assert!(!s.owns_child());
        assert_eq!(s.attempt, 1);
        assert!(s.not_before.is_some());
        s.tick(); // still inside the 1 s backoff: no new attempt
        assert_eq!(s.attempt, 1);
        let log = std::fs::read_to_string(root.join("data").join("hub.log")).unwrap();
        assert!(log.contains("could not start the hub"), "{log}");
        let _ = std::fs::remove_dir_all(&root);
    }

    #[test]
    fn hub_args_pass_the_configured_port() {
        // The hub must listen where the shell looks, whatever its own `port` setting says.
        let args = hub_args(std::path::Path::new(r"C:\bh root"), 8099);
        let args: Vec<String> = args.iter().map(|a| a.to_string_lossy().into_owned()).collect();
        assert_eq!(args, ["-m", "brainhub", "--root", r"C:\bh root", "--port", "8099"]);
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
}
