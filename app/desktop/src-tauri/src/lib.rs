//! BrainHub desktop shell (design §8): the `widget` window, the tray, autostart and the hub
//! supervisor. The hub serves the widget page; this shell only frames it.

pub mod config;
pub mod supervisor;

use std::process::Command;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::Duration;

use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{
    AppHandle, Manager, PhysicalPosition, RunEvent, Runtime, WebviewUrl, WebviewWindowBuilder, Window, WindowEvent, Wry,
};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt};
use tauri_plugin_window_state::StateFlags;

use supervisor::{Notice, Supervisor};
#[cfg(windows)]
use supervisor::CREATE_NO_WINDOW;

#[cfg(test)]
mod shell_source;

const WIDGET: &str = "widget";
const TRAY: &str = "main";
const TICK: Duration = Duration::from_secs(2);

/// Set when the shell set `GDK_BACKEND` itself (D1). Its children (the hub, xdg-open, and the
/// browsers they start) don't inherit it: a browser started from here would run under XWayland too.
static FORCED_GDK_BACKEND: AtomicBool = AtomicBool::new(false);

/// Ruling D1: on Linux the shell runs under XWayland, where KWin honours keep-above, skip-taskbar
/// and a remembered position (a native Wayland window can ask for none of them). This must run
/// before GTK starts, while the process has one thread. A `GDK_BACKEND` from the environment wins.
#[cfg(target_os = "linux")]
fn use_xwayland() {
    if std::env::var_os("GDK_BACKEND").is_none() {
        std::env::set_var("GDK_BACKEND", "x11");
        FORCED_GDK_BACKEND.store(true, Ordering::Relaxed);
    }
}

/// Take the shell's own environment changes back out of a child's environment.
pub(crate) fn child_env(cmd: &mut Command) {
    if FORCED_GDK_BACKEND.load(Ordering::Relaxed) {
        cmd.env_remove("GDK_BACKEND");
    }
}

/// Quit cleanly on SIGTERM (logout) and SIGINT (Ctrl+C), as the tray's Quit does (D1): `exit(0)`
/// leads to `RunEvent::Exit`, where the window-state plugin saves the position and the supervisor
/// stops the hub. Without this a signal ends the shell on the spot, saving nothing. The handler
/// only writes a byte to a pipe; a plain thread reads it and asks the app to exit.
#[cfg(unix)]
fn quit_on_signals(app: AppHandle) -> std::io::Result<()> {
    use std::io::Read;
    use std::os::fd::FromRawFd;
    use std::sync::atomic::AtomicI32;

    const SIGNALS: [libc::c_int; 2] = [libc::SIGTERM, libc::SIGINT];
    static WAKE_FD: AtomicI32 = AtomicI32::new(-1);
    extern "C" fn on_signal(_: libc::c_int) {
        // Async-signal-safe only: one write(2) to a non-blocking pipe, with errno kept for the
        // code the signal interrupted.
        #[cfg(target_os = "linux")]
        let saved = unsafe { *libc::__errno_location() };
        let _ = unsafe { libc::write(WAKE_FD.load(Ordering::Relaxed), b"q".as_ptr().cast(), 1) };
        #[cfg(target_os = "linux")]
        unsafe {
            *libc::__errno_location() = saved
        };
    }

    // Close-on-exec from the start, so the hub and xdg-open never inherit the pipe. Only the write
    // end is non-blocking, so the handler never blocks; the reader thread blocks on the read end.
    let mut fds: [libc::c_int; 2] = [-1; 2];
    #[cfg(target_os = "linux")]
    let piped = unsafe { libc::pipe2(fds.as_mut_ptr(), libc::O_CLOEXEC) } == 0;
    #[cfg(not(target_os = "linux"))]
    let piped = unsafe {
        libc::pipe(fds.as_mut_ptr()) == 0
            && libc::fcntl(fds[0], libc::F_SETFD, libc::FD_CLOEXEC) != -1
            && libc::fcntl(fds[1], libc::F_SETFD, libc::FD_CLOEXEC) != -1
    };
    let made = piped && unsafe { libc::fcntl(fds[1], libc::F_SETFL, libc::O_NONBLOCK) } != -1;
    if !made {
        return Err(std::io::Error::last_os_error());
    }
    WAKE_FD.store(fds[1], Ordering::Relaxed);
    let mut wake = unsafe { std::fs::File::from_raw_fd(fds[0]) };
    // The reader runs before any handler is installed, so a caught signal is never left unread.
    std::thread::spawn(move || {
        let mut byte = [0u8; 1];
        loop {
            match wake.read(&mut byte) {
                Ok(1) => break,
                Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
                _ => return,
            }
        }
        // A second signal ends the shell at once, as it would have without this.
        for sig in SIGNALS {
            unsafe { libc::signal(sig, libc::SIG_DFL) };
        }
        app.exit(0);
    });
    for sig in SIGNALS {
        let mut action: libc::sigaction = unsafe { std::mem::zeroed() };
        action.sa_sigaction = on_signal as extern "C" fn(libc::c_int) as libc::sighandler_t;
        action.sa_flags = libc::SA_RESTART;
        let failed = unsafe {
            libc::sigemptyset(&mut action.sa_mask) != 0 || libc::sigaction(sig, &action, std::ptr::null_mut()) != 0
        };
        if failed {
            return Err(std::io::Error::last_os_error());
        }
    }
    Ok(())
}

#[derive(Clone)]
struct Shared(Arc<Mutex<Supervisor>>);

impl Shared {
    fn lock(&self) -> MutexGuard<'_, Supervisor> {
        self.0.lock().unwrap_or_else(|e| e.into_inner())
    }
}

fn show_widget(app: &AppHandle) {
    if let Some(w) = app.get_webview_window(WIDGET) {
        let _ = w.show();
        let _ = w.set_focus();
    }
}

fn toggle_widget(app: &AppHandle) {
    if let Some(w) = app.get_webview_window(WIDGET) {
        if w.is_visible().unwrap_or(false) {
            let _ = w.hide();
        } else {
            let _ = w.show();
            let _ = w.set_focus();
        }
    }
}

/// The default browser at `url`, via `cmd /C start "" <url>` without a console flash.
#[cfg(windows)]
fn open_browser(url: &str) {
    use std::os::windows::process::CommandExt;
    let mut cmd = Command::new("cmd");
    cmd.args(["/C", "start", "", url]);
    cmd.creation_flags(CREATE_NO_WINDOW);
    let _ = cmd.spawn();
}

/// The default browser at `url`, via `xdg-open`, detached: stdio closed, its own process group (a
/// Ctrl+C meant for the shell doesn't reach a browser it starts), and reaped on a thread.
#[cfg(unix)]
fn open_browser(url: &str) {
    use std::os::unix::process::CommandExt;
    use std::process::Stdio;
    let mut cmd = Command::new("xdg-open");
    cmd.arg(url).stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).process_group(0);
    child_env(&mut cmd);
    if let Ok(mut child) = cmd.spawn() {
        std::thread::spawn(move || child.wait());
    }
}

fn toggle_autostart(app: &AppHandle, item: &CheckMenuItem<Wry>) {
    let auto = app.autolaunch();
    let enabled = auto.is_enabled().unwrap_or(false);
    let _ = if enabled { auto.disable() } else { auto.enable() };
    let _ = item.set_checked(auto.is_enabled().unwrap_or(false));
}

/// Should the watch loop point the widget at the hub's page? Only when the port answers and the
/// window is not already there. Once on the hub's page it stays: the page rides out a hub restart
/// by itself (5 s retry, WebSocket reconnect), and a reload would drop what the owner was typing.
/// `current` is the window's URL when it can be read; it catches a window that is somehow not on
/// the hub page after the first navigation.
pub fn needs_navigate(up: bool, navigated: bool, current: Option<&str>, target: &str) -> bool {
    up && (!navigated || current.is_some_and(|u| !u.starts_with(target)))
}

/// The window position that keeps a `size` window inside the monitor's work area (all physical
/// pixels), or None when it already fits. Growing the widget (expand, a popup) must never push it
/// off the bottom of the screen or under the taskbar.
pub fn clamp_into_work_area(
    pos: (i32, i32),
    size: (u32, u32),
    area_pos: (i32, i32),
    area_size: (u32, u32),
) -> Option<(i32, i32)> {
    let clamp = |p: i32, len: u32, start: i32, span: u32| -> i32 {
        let end = start.saturating_add(span as i32);
        p.min(end.saturating_sub(len as i32)).max(start)
    };
    let x = clamp(pos.0, size.0, area_pos.0, area_size.0);
    let y = clamp(pos.1, size.1, area_pos.1, area_size.1);
    if (x, y) == pos {
        None
    } else {
        Some((x, y))
    }
}

/// Move the widget back inside its monitor's work area if it sticks out (after it was built and
/// whenever it resizes).
fn keep_on_screen<R: Runtime>(window: &Window<R>) {
    let (Ok(pos), Ok(size)) = (window.outer_position(), window.outer_size()) else { return };
    let monitor = match window.current_monitor() {
        Ok(Some(m)) => m,
        _ => match window.primary_monitor() {
            Ok(Some(m)) => m,
            _ => return,
        },
    };
    let area = monitor.work_area();
    if let Some((x, y)) = clamp_into_work_area(
        (pos.x, pos.y),
        (size.width, size.height),
        (area.position.x, area.position.y),
        (area.size.width, area.size.height),
    ) {
        let _ = window.set_position(PhysicalPosition::new(x, y));
    }
}

/// The tray's tooltip, and its menu's first line (disabled) while there is a notice: Linux's tray
/// (AppIndicator) shows no tooltip, so the menu says it there.
struct TrayNotice {
    menu: Menu<Wry>,
    line: MenuItem<Wry>,
    shown: Option<Notice>,
}

impl TrayNotice {
    fn show(&mut self, app: &AppHandle, notice: Option<Notice>) {
        if notice == self.shown {
            return;
        }
        let tooltip = notice.map_or_else(|| "BrainHub".to_string(), |n| format!("BrainHub — {}", n.text()));
        if let Some(tray) = app.tray_by_id(TRAY) {
            let _ = tray.set_tooltip(Some(tooltip));
        }
        match notice {
            Some(n) => {
                let text = n.text();
                let _ = self.line.set_text(format!("{}{}", text[..1].to_uppercase(), &text[1..]));
                if self.shown.is_none() {
                    let _ = self.menu.insert(&self.line, 0);
                }
            }
            None => {
                let _ = self.menu.remove(&self.line);
            }
        }
        self.shown = notice;
    }
}

/// Every 2 s: let the supervisor act, point the widget at the hub once the port answers (see
/// `needs_navigate`: never again after that), and keep "Restart hub" and the tray notice in sync.
/// In runtime mode a start first runs claude-sync and the update step (up to 150 s): that runs
/// here, without the supervisor's lock, so the tray's Restart and Quit never wait for it.
fn watch(app: AppHandle, sup: Shared, port: u16, restart_item: MenuItem<Wry>, mut notice: TrayNotice) {
    std::thread::spawn(move || {
        let target = format!("http://127.0.0.1:{port}/widget");
        let mut navigated = false;
        loop {
            let prep = sup.lock().tick();
            if let Some(prep) = prep {
                // Say what is already known (a widget update from the last step) before the wait.
                let known = sup.lock().notice();
                notice.show(&app, known);
                prep.run();
                sup.lock().start_prepared(prep);
            }
            let (up, owns, now) = {
                let mut s = sup.lock();
                (s.last_up(), s.owns_child(), s.notice())
            };
            notice.show(&app, now);
            if let Some(w) = app.get_webview_window(WIDGET) {
                let current = w.url().ok().map(|u| u.to_string());
                if needs_navigate(up, navigated, current.as_deref(), &target) {
                    if let Ok(url) = target.parse() {
                        if w.navigate(url).is_ok() {
                            navigated = true;
                        }
                    }
                }
            }
            let _ = restart_item.set_enabled(owns);
            std::thread::sleep(TICK);
        }
    });
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    #[cfg(target_os = "linux")]
    use_xwayland();
    let cfg = config::default_path().map(|p| config::load(&p)).unwrap_or_default();
    let port = cfg.port;
    let state = config::state_dir().unwrap_or_else(std::env::temp_dir); // temp: no home folder at all
    let sup = Shared(Arc::new(Mutex::new(Supervisor::new(cfg, state))));
    let sup_for_exit = sup.clone();

    let app = tauri::Builder::default()
        .plugin(tauri_plugin_single_instance::init(|app, _args, _cwd| show_widget(app)))
        .plugin(tauri_plugin_autostart::init(MacosLauncher::LaunchAgent, None))
        .plugin(tauri_plugin_window_state::Builder::default().with_state_flags(StateFlags::POSITION).build())
        .setup(move |app| {
            let widget = WebviewWindowBuilder::new(app, WIDGET, WebviewUrl::App("index.html".into()))
                .title("BrainHub")
                .inner_size(520.0, 44.0)
                // First launch only: window-state replaces this with the remembered position.
                .center()
                .decorations(false)
                .transparent(true)
                .always_on_top(true)
                .skip_taskbar(true)
                .resizable(false)
                .shadow(false)
                // Shown below, once its window type is set.
                .visible(false)
                .build()?;
            // D1: a utility window stays out of KWin's Alt+Tab. The type counts only if it is set
            // before the window is first shown.
            #[cfg(target_os = "linux")]
            if let Ok(window) = widget.gtk_window() {
                use gtk::prelude::GtkWindowExt;
                window.set_type_hint(gtk::gdk::WindowTypeHint::Utility);
            }
            // A remembered (or first-launch) position may leave the window partly off-screen.
            keep_on_screen(&widget.as_ref().window());
            widget.show()?;

            let handle = app.handle();
            #[cfg(unix)]
            if let Err(e) = quit_on_signals(handle.clone()) {
                eprintln!("brainhub-desktop: SIGTERM/SIGINT won't quit cleanly: {e}");
            }
            let open = MenuItem::with_id(handle, "open", "Open BrainHub", true, None::<&str>)?;
            let toggle = MenuItem::with_id(handle, "toggle", "Show/Hide widget", true, None::<&str>)?;
            let autostart_on = handle.autolaunch().is_enabled().unwrap_or(false);
            let login = CheckMenuItem::with_id(handle, "login", "Start at login", true, autostart_on, None::<&str>)?;
            let restart = MenuItem::with_id(handle, "restart", "Restart hub", false, None::<&str>)?;
            let quit = MenuItem::with_id(handle, "quit", "Quit", true, None::<&str>)?;
            let sep = PredefinedMenuItem::separator(handle)?;
            let menu = Menu::with_items(handle, &[&open, &toggle, &login, &restart, &sep, &quit])?;
            // The notice line (no slot, widget update): shown at the top only while it applies.
            let notice_line = MenuItem::with_id(handle, "notice", "", false, None::<&str>)?;

            let sup_menu = sup.clone();
            let login_item = login.clone();
            let mut tray = TrayIconBuilder::with_id(TRAY).tooltip("BrainHub").menu(&menu).on_menu_event(
                move |app, e| match e.id.as_ref() {
                    "open" => open_browser(&format!("http://127.0.0.1:{port}/")),
                    "toggle" => toggle_widget(app),
                    "login" => toggle_autostart(app, &login_item),
                    "restart" => sup_menu.lock().restart(),
                    "quit" => {
                        sup_menu.lock().stop();
                        app.exit(0);
                    }
                    _ => {}
                },
            );
            if let Some(icon) = app.default_window_icon() {
                tray = tray.icon(icon.clone());
            }
            tray.build(app)?;

            watch(handle.clone(), sup.clone(), port, restart, TrayNotice { menu, line: notice_line, shown: None });
            Ok(())
        })
        .on_window_event(|window, event| {
            if window.label() != WIDGET {
                return;
            }
            match event {
                // Alt+F4 on the frameless widget hides it; only the tray's Quit ends the shell.
                WindowEvent::CloseRequested { api, .. } => {
                    api.prevent_close();
                    let _ = window.hide();
                }
                // Expanding grows the window downwards: keep it inside the work area.
                WindowEvent::Resized(_) => keep_on_screen(window),
                _ => {}
            }
        })
        .build(tauri::generate_context!())
        .expect("error while building the BrainHub desktop shell");

    app.run(move |_app, event| {
        if let RunEvent::Exit = event {
            // However the shell ends, never leave a hub it started running behind it.
            sup_for_exit.lock().stop();
        }
    });
}

#[cfg(test)]
mod tests {
    use super::*;

    const T: &str = "http://127.0.0.1:8099/widget";

    #[test]
    fn navigates_once_when_the_hub_answers() {
        assert!(!needs_navigate(false, false, Some("http://tauri.localhost/index.html"), T));
        assert!(needs_navigate(true, false, Some("http://tauri.localhost/index.html"), T));
        assert!(needs_navigate(true, false, None, T));
        // Already on the hub page: a hub restart (down, then up) must not reload it.
        assert!(!needs_navigate(true, true, Some(T), T));
        assert!(!needs_navigate(true, true, Some("http://127.0.0.1:8099/widget#x"), T));
        assert!(!needs_navigate(true, true, None, T));
        // Somehow not on the hub page any more: point it back.
        assert!(needs_navigate(true, true, Some("http://tauri.localhost/index.html"), T));
        assert!(!needs_navigate(false, true, Some("http://tauri.localhost/index.html"), T));
    }

    #[test]
    fn clamp_keeps_the_window_in_the_work_area() {
        // 1920x1080 at 125 %: work area 1920x1020 (taskbar below).
        let (ap, asz) = ((0, 0), (1920, 1020));
        assert_eq!(clamp_into_work_area((700, 400), (650, 300), ap, asz), None);
        // Seen in the e2e: y = 1021 (below the work area), then expanding to 300 px tall.
        assert_eq!(clamp_into_work_area((700, 1021), (650, 55), ap, asz), Some((700, 965)));
        assert_eq!(clamp_into_work_area((700, 900), (650, 300), ap, asz), Some((700, 720)));
        // Off the right and left edges.
        assert_eq!(clamp_into_work_area((1500, 10), (650, 55), ap, asz), Some((1270, 10)));
        assert_eq!(clamp_into_work_area((-40, 10), (650, 55), ap, asz), Some((0, 10)));
        // Taller than the work area: top-aligned.
        assert_eq!(clamp_into_work_area((0, 500), (650, 2000), ap, asz), Some((0, 0)));
        // A second monitor left of the primary, with negative coordinates.
        assert_eq!(clamp_into_work_area((-900, 1000), (650, 300), (-1920, 0), (1920, 1040)), Some((-900, 740)));
    }
}
