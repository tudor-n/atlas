//! BrainHub desktop shell (design §8): the `widget` window, the `main` window (phase 4.5), the
//! tray, autostart and the hub supervisor. The hub serves both pages; this shell only frames them.

pub mod claude_watch;
pub mod config;
pub mod supervisor;
pub mod voice;

use std::process::Command;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::Duration;

use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem, Submenu};
use tauri::tray::TrayIconBuilder;
use tauri::window::Color;
use tauri::{
    AppHandle, Manager, PhysicalPosition, PhysicalSize, RunEvent, Runtime, Theme, WebviewUrl, WebviewWindow,
    WebviewWindowBuilder, Window, WindowEvent, Wry,
};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt};
use tauri_plugin_window_state::{StateFlags, WindowExt};

use supervisor::{Notice, Supervisor};
#[cfg(windows)]
use supervisor::CREATE_NO_WINDOW;

#[cfg(test)]
mod shell_source;

const WIDGET: &str = "widget";
/// The main window's label (phase 4.5). The tray's id is "main" too: a different namespace.
const MAIN: &str = "main";
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

/// A view the main window may be sent to (`#/<view>`): the hand-written equivalent of
/// `^[a-z]+(/[a-z0-9-]+)?$`, so `today`, `usage`, `settings/tablet`, `project/arm-v2`. Anything
/// else (a second slash, upper case, a quote, an empty part) is refused: it never reaches a URL
/// or a script.
pub fn valid_view(view: &str) -> bool {
    let (head, tail) = match view.split_once('/') {
        Some((head, tail)) => (head, Some(tail)),
        None => (view, None),
    };
    let tail_ok =
        |t: &str| !t.is_empty() && t.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-');
    !head.is_empty() && head.bytes().all(|b| b.is_ascii_lowercase()) && tail.map_or(true, tail_ok)
}

/// The hub page for the main window, at `#/<view>` when the view is valid.
pub fn main_url(port: u16, view: Option<&str>) -> String {
    match view.filter(|v| valid_view(v)) {
        Some(v) => format!("http://127.0.0.1:{port}/#/{v}"),
        None => format!("http://127.0.0.1:{port}/"),
    }
}

/// The script that moves a main window already on the hub page to `view` without a reload (the
/// page's hash router does the rest). The value is JSON-encoded, though `valid_view` already
/// keeps it to `[a-z0-9/-]`.
pub fn hash_script(view: &str) -> String {
    let hash = serde_json::Value::String(format!("#/{view}"));
    format!("location.hash = {hash};")
}

/// What a launch's arguments ask of the main window: `--open` → `Some(None)` (open it as it is),
/// `--open=<view>` → `Some(Some(view))` for a valid view; `None` otherwise (an invalid view too),
/// which shows the widget as before. The first `--open…` argument decides; the program name, if
/// present, never matches.
pub fn wants_main<S: AsRef<str>>(args: &[S]) -> Option<Option<String>> {
    for arg in args {
        let arg = arg.as_ref();
        if arg == "--open" {
            return Some(None);
        }
        if let Some(view) = arg.strip_prefix("--open=") {
            return valid_view(view).then(|| Some(view.to_string()));
        }
    }
    None
}

/// The main window's navigation state, shared by `show_main` (the main thread) and the watch
/// thread.
struct MainNav {
    /// Navigated to the hub page at least once; it is never reloaded after that (see
    /// `needs_navigate`).
    navigated: bool,
    /// A view asked for before the window was on the hub page: the first navigation takes it.
    pending: Option<String>,
    /// Bumped on every navigation, so the watch thread can tell that `show_main` navigated
    /// between its reading the URL and taking the lock (it then waits for the next tick).
    epoch: u64,
}

/// Managed state for the main window.
struct MainWindow {
    port: u16,
    /// The hub answered at the last watch tick.
    up: AtomicBool,
    nav: Mutex<MainNav>,
    /// `nav.epoch` mirrored outside the lock, read before the URL.
    epoch: AtomicU64,
}

impl MainWindow {
    fn new(port: u16) -> Self {
        MainWindow {
            port,
            up: AtomicBool::new(false),
            nav: Mutex::new(MainNav { navigated: false, pending: None, epoch: 0 }),
            epoch: AtomicU64::new(0),
        }
    }

    fn lock(&self) -> MutexGuard<'_, MainNav> {
        self.nav.lock().unwrap_or_else(|e| e.into_inner())
    }

    fn target(&self) -> String {
        main_url(self.port, None)
    }

    /// Point `w` at the hub page (with the pending view), under the lock.
    fn navigate(&self, w: &WebviewWindow, nav: &mut MainNav) {
        if let Ok(url) = main_url(self.port, nav.pending.as_deref()).parse() {
            if w.navigate(url).is_ok() {
                nav.navigated = true;
                nav.pending = None;
                nav.epoch += 1;
                self.epoch.store(nav.epoch, Ordering::SeqCst);
            }
        }
    }
}

/// A `size` that fits in the work area (both physical pixels), or None when it already does: the
/// main window's 1440×900 is taller than a 1080p screen at 125 % (864 logical pixels).
pub fn fit_into_work_area(size: (u32, u32), area_size: (u32, u32)) -> Option<(u32, u32)> {
    let fitted = (size.0.min(area_size.0), size.1.min(area_size.1));
    (fitted != size && fitted.0 > 0 && fitted.1 > 0).then_some(fitted)
}

/// The main window, built on first use (phase 4.5, design §6): decorated, resizable, in the taskbar
/// and Alt+Tab, black from the first frame, hidden until `show_main` shows it. It starts on
/// `main.html` ("STARTING THE HUB…"); the watch thread or `show_main` takes it to the hub page.
/// The window-state plugin restores its position, size and maximised state as it is built.
/// Call it on the main thread.
fn ensure_main(app: &AppHandle) -> Option<WebviewWindow> {
    if let Some(w) = app.get_webview_window(MAIN) {
        return Some(w);
    }
    let built = WebviewWindowBuilder::new(app, MAIN, WebviewUrl::App("main.html".into()))
        .title("BrainHub")
        .inner_size(1440.0, 900.0)
        .min_inner_size(900.0, 600.0)
        // First open only: window-state replaces it with the remembered position.
        .center()
        .decorations(true)
        .resizable(true)
        .skip_taskbar(false)
        .theme(Some(Theme::Dark))
        .background_color(Color(0, 0, 0, 255))
        .visible(false)
        .build();
    match built {
        Ok(w) => {
            let window = w.as_ref().window();
            if !w.is_maximized().unwrap_or(false) {
                fit_on_screen(&window);
            }
            Some(w)
        }
        Err(e) => {
            eprintln!("brainhub-desktop: could not open the main window: {e}");
            app.get_webview_window(MAIN)
        }
    }
}

/// Shrink a window that is bigger than its monitor's work area to fit it, then keep it on screen.
fn fit_on_screen<R: Runtime>(window: &Window<R>) {
    let monitor = match window.current_monitor() {
        Ok(Some(m)) => Some(m),
        _ => window.primary_monitor().ok().flatten(),
    };
    if let (Ok(size), Some(monitor)) = (window.outer_size(), monitor) {
        let area = monitor.work_area();
        if let Some((w, h)) = fit_into_work_area((size.width, size.height), (area.size.width, area.size.height)) {
            // outer → inner: take off the frame the window manager adds.
            let inner = window.inner_size().unwrap_or(size);
            let (dw, dh) = (size.width.saturating_sub(inner.width), size.height.saturating_sub(inner.height));
            let _ = window.set_size(PhysicalSize::new(w.saturating_sub(dw), h.saturating_sub(dh)));
        }
    }
    keep_on_screen(window);
}

/// Open the main window (design §6): build it if needed; on the hub page already, go to `view` by
/// its hash (no reload); otherwise remember `view` and go to the hub page now if the hub answers
/// (else the watch thread does, once it does). Then unminimise, show and focus it. A view that
/// isn't `valid_view` is dropped. Call it on the main thread (`open_main_later` gets there).
fn show_main(app: &AppHandle, view: Option<String>) {
    let view = view.filter(|v| valid_view(v));
    let Some(main) = app.try_state::<MainWindow>() else { return };
    let Some(w) = ensure_main(app) else { return };
    let current = w.url().ok().map(|u| u.to_string());
    {
        let mut nav = main.lock();
        let on_hub = !needs_navigate(true, nav.navigated, current.as_deref(), &main.target());
        if on_hub {
            if let Some(v) = &view {
                let _ = w.eval(hash_script(v));
            }
        } else {
            if view.is_some() {
                nav.pending = view;
            }
            if main.up.load(Ordering::SeqCst) {
                main.navigate(&w, &mut nav);
            }
        }
    }
    let _ = w.unminimize();
    let _ = w.show();
    let _ = w.set_focus();
}

/// `show_main` on the main thread, from anywhere (the `open_main` command, the single-instance
/// callback): building a window and reading its URL both need it, and a window built from a
/// command's thread can deadlock on Windows.
fn open_main_later(app: &AppHandle, view: Option<String>) {
    let handle = app.clone();
    if let Err(e) = app.run_on_main_thread(move || show_main(&handle, view)) {
        eprintln!("brainhub-desktop: could not open the main window: {e}");
    }
}

/// The widget's tiles open the main window at a view: `window.__TAURI__.core.invoke('open_main',
/// {view})`. Granted only to the widget (`allow-open-main` in `capabilities/widget.json`). Async,
/// so it never runs on the main thread itself (the Windows deadlock when a command builds a
/// window).
#[tauri::command]
async fn open_main(app: AppHandle, view: Option<String>) {
    open_main_later(&app, view);
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

/// Should the watch loop point a window (the widget, or the main window) at its hub page (`target`
/// is that page's URL, or a prefix of every URL that counts as on it)? Only when the port answers
/// and the window is not already there. Once on the hub's page it stays: the page rides out a hub restart
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

/// The tray's tooltip, and its menu's first line (disabled) while there is a notice, on both OSes:
/// Linux's tray (AppIndicator) shows no tooltip, so the menu says it there too.
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

/// Every 2 s: let the supervisor act, point the widget and the main window (if it was opened) at
/// their hub pages once the port answers (see `needs_navigate`: never again after that), and keep
/// "Restart hub" and the tray notice in sync.
/// In runtime mode a start first runs claude-sync and the update step (up to 150 s): that runs
/// here, without the supervisor's lock, so the tray's Restart and Quit never wait for it.
fn watch(app: AppHandle, sup: Shared, port: u16, restart_item: MenuItem<Wry>, mut notice: TrayNotice) {
    std::thread::spawn(move || {
        let target = format!("http://127.0.0.1:{port}/widget");
        let mut navigated = false;
        let main = app.state::<MainWindow>();
        let main_target = main.target();
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
            main.up.store(up, Ordering::SeqCst);
            if let Some(w) = app.get_webview_window(MAIN) {
                // The URL first (it waits for the main thread), then the lock (`show_main` holds it
                // on the main thread). A navigation in between bumps the epoch: wait a tick.
                let epoch = main.epoch.load(Ordering::SeqCst);
                let current = w.url().ok().map(|u| u.to_string());
                let mut nav = main.lock();
                if nav.epoch == epoch && needs_navigate(up, nav.navigated, current.as_deref(), &main_target) {
                    main.navigate(&w, &mut nav);
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
        // A second launch: `--open[=view]` (the launcher) shows the main window, nothing (autostart,
        // a plain start) the widget, as before.
        .plugin(tauri_plugin_single_instance::init(|app, args, _cwd| match wants_main(&args) {
            Some(view) => open_main_later(app, view),
            None => show_widget(app),
        }))
        .plugin(tauri_plugin_autostart::init(MacosLauncher::LaunchAgent, None))
        // The main window keeps its position, size and maximised state. The widget keeps only its
        // position: it is skipped here and restored by hand in setup (its size follows its page).
        .plugin(
            tauri_plugin_window_state::Builder::default()
                .with_state_flags(StateFlags::POSITION | StateFlags::SIZE | StateFlags::MAXIMIZED)
                .skip_initial_state(WIDGET)
                .build(),
        )
        .invoke_handler(tauri::generate_handler![open_main])
        .setup(move |app| {
            app.manage(MainWindow::new(port));
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
            // Its remembered position only (the plugin skips it: see the builder above).
            let _ = widget.restore_state(StateFlags::POSITION);
            // A remembered (or first-launch) position may leave the window partly off-screen.
            keep_on_screen(&widget.as_ref().window());
            widget.show()?;

            let handle = app.handle();
            #[cfg(unix)]
            if let Err(e) = quit_on_signals(handle.clone()) {
                eprintln!("brainhub-desktop: SIGTERM/SIGINT won't quit cleanly: {e}");
            }
            let open = MenuItem::with_id(handle, "open", "Open BrainHub", true, None::<&str>)?;
            let browser = MenuItem::with_id(handle, "browser", "Open in browser", true, None::<&str>)?;
            let toggle = MenuItem::with_id(handle, "toggle", "Show/Hide widget", true, None::<&str>)?;
            let autostart_on = handle.autolaunch().is_enabled().unwrap_or(false);
            let login = CheckMenuItem::with_id(handle, "login", "Start at login", true, autostart_on, None::<&str>)?;
            let restart = MenuItem::with_id(handle, "restart", "Restart hub", false, None::<&str>)?;
            let quit = MenuItem::with_id(handle, "quit", "Quit", true, None::<&str>)?;
            let sep = PredefinedMenuItem::separator(handle)?;
            // Phase 4: pause the voice (the spec's "pause notifications": it's the one audible channel).
            let voice_items = voice::ITEMS
                .iter()
                .map(|(id, label, _)| MenuItem::with_id(handle, *id, *label, true, None::<&str>))
                .collect::<Result<Vec<_>, _>>()?;
            let voice_refs: Vec<&dyn tauri::menu::IsMenuItem<Wry>> =
                voice_items.iter().map(|i| i as &dyn tauri::menu::IsMenuItem<Wry>).collect();
            let pause_voice = Submenu::with_items(handle, "Pause voice", true, &voice_refs)?;
            let menu =
                Menu::with_items(handle, &[&open, &browser, &toggle, &pause_voice, &login, &restart, &sep, &quit])?;
            // The notice line (no slot, widget update): shown at the top only while it applies.
            let notice_line = MenuItem::with_id(handle, "notice", "", false, None::<&str>)?;

            let sup_menu = sup.clone();
            let login_item = login.clone();
            let mut tray = TrayIconBuilder::with_id(TRAY).tooltip("BrainHub").menu(&menu).on_menu_event(
                move |app, e| match e.id.as_ref() {
                    // The menu runs on the main thread: show_main can build the window here.
                    "open" => show_main(app, None),
                    "browser" => open_browser(&format!("http://127.0.0.1:{port}/")),
                    "toggle" => toggle_widget(app),
                    "login" => toggle_autostart(app, &login_item),
                    "restart" => sup_menu.lock().restart(),
                    "quit" => {
                        sup_menu.lock().stop();
                        app.exit(0);
                    }
                    id => {
                        if let Some(body) = voice::body_for(id) {
                            // Off the menu thread: a slow or stopped hub never freezes the tray.
                            std::thread::spawn(move || match voice::post_pause(port, body) {
                                Ok(status) if (200..300).contains(&status) => {}
                                Ok(status) => eprintln!("brainhub-desktop: pause voice: the hub answered {status}"),
                                Err(e) => eprintln!("brainhub-desktop: pause voice: {e}"),
                            });
                        }
                    }
                },
            );
            if let Some(icon) = app.default_window_icon() {
                tray = tray.icon(icon.clone());
            }
            tray.build(app)?;

            // Here, not before the builder: only the one instance that runs gets this far (the
            // single-instance plugin ends a second launch before setup).
            let claude_watch = {
                let s = sup.lock();
                s.record_shell();
                s.claude_watch()
            };
            if let Some(w) = claude_watch {
                w.spawn();
            }
            watch(handle.clone(), sup.clone(), port, restart, TrayNotice { menu, line: notice_line, shown: None });
            // Started by the launcher (`--open[=view]`): the main window too, not only the widget.
            let args: Vec<String> = std::env::args().collect();
            if let Some(view) = wants_main(&args) {
                show_main(handle, view);
            }
            Ok(())
        })
        .on_window_event(|window, event| {
            if window.label() == MAIN {
                // Closing the main window hides it (Quit is in the tray): the page, its WebSocket
                // and any draft stay, and opening it again is instant.
                if let WindowEvent::CloseRequested { api, .. } = event {
                    api.prevent_close();
                    let _ = window.hide();
                }
                return;
            }
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
    fn main_window_navigates_independently_of_the_widget() {
        let m = main_url(8099, None);
        assert_eq!(m, "http://127.0.0.1:8099/");
        // First page: go to the hub once it answers.
        assert!(!needs_navigate(false, false, Some("tauri://localhost/main.html"), &m));
        assert!(needs_navigate(true, false, Some("tauri://localhost/main.html"), &m));
        assert!(needs_navigate(true, false, Some("http://tauri.localhost/main.html"), &m));
        // On the hub page, at any view: never again (a hub restart must not reload it).
        assert!(!needs_navigate(true, true, Some("http://127.0.0.1:8099/"), &m));
        assert!(!needs_navigate(true, true, Some("http://127.0.0.1:8099/#/agents"), &m));
        assert!(!needs_navigate(true, true, Some("http://127.0.0.1:8099/#/settings/tablet"), &m));
        assert!(!needs_navigate(true, true, None, &m));
        // Somehow off it (the starting page, another port): back to the hub.
        assert!(needs_navigate(true, true, Some("tauri://localhost/main.html"), &m));
        assert!(needs_navigate(true, true, Some("http://127.0.0.1:8090/"), &m));
        // The widget's own target is unaffected by the main window's.
        assert!(!needs_navigate(true, true, Some("http://127.0.0.1:8099/widget"), T));
        assert!(needs_navigate(true, true, Some("http://127.0.0.1:8099/#/today"), T));
    }

    #[test]
    fn valid_views() {
        for v in ["today", "usage", "browse", "settings", "settings/tablet", "project/arm-v2", "project/x", "a/0"] {
            assert!(valid_view(v), "{v}");
        }
        for v in [
            "", "/", "today/", "/today", "Today", "to day", "today/a/b", "project/Arm", "project/a_b",
            "x1", "1x", "project/\"x", "today'", "today;alert(1)", "settings/tablet#x", "settings/tablet\n",
            "t\u{e9}", "project/caf\u{e9}", "-/x",
        ] {
            assert!(!valid_view(v), "{v:?}");
        }
    }

    #[test]
    fn main_urls() {
        assert_eq!(main_url(8090, None), "http://127.0.0.1:8090/");
        assert_eq!(main_url(8090, Some("agents")), "http://127.0.0.1:8090/#/agents");
        assert_eq!(main_url(8090, Some("project/arm-v2")), "http://127.0.0.1:8090/#/project/arm-v2");
        // An invalid view is dropped, never put in the URL.
        assert_eq!(main_url(8090, Some("x/../y")), "http://127.0.0.1:8090/");
        assert_eq!(main_url(8090, Some("")), "http://127.0.0.1:8090/");
    }

    #[test]
    fn hash_scripts_are_json_encoded() {
        assert_eq!(hash_script("agents"), r##"location.hash = "#/agents";"##);
        assert_eq!(hash_script("settings/tablet"), r##"location.hash = "#/settings/tablet";"##);
        // valid_view keeps quotes out anyway; the encoding still escapes them.
        assert_eq!(hash_script("a\"b\\c"), r##"location.hash = "#/a\"b\\c";"##);
        assert_eq!(hash_script("</script>\u{2028}"), "location.hash = \"#/</script>\u{2028}\";");
    }

    #[test]
    fn launch_arguments() {
        let none: [&str; 0] = [];
        assert_eq!(wants_main(&none), None);
        assert_eq!(wants_main(&["/usr/bin/brainhub-desktop"]), None); // autostart: the widget
        assert_eq!(wants_main(&["brainhub-desktop", "--open"]), Some(None));
        assert_eq!(wants_main(&["brainhub-desktop", "--open=agents"]), Some(Some("agents".to_string())));
        let voice = Some(Some("settings/voice".to_string()));
        assert_eq!(wants_main(&["brainhub-desktop", "--open=settings/voice"]), voice);
        // An invalid view, an empty one, look-alikes: the widget, as before.
        assert_eq!(wants_main(&["brainhub-desktop", "--open=Agents"]), None);
        assert_eq!(wants_main(&["brainhub-desktop", "--open="]), None);
        assert_eq!(wants_main(&["brainhub-desktop", "--opened"]), None);
        assert_eq!(wants_main(&["brainhub-desktop", "-open"]), None);
        assert_eq!(wants_main(&["brainhub-desktop", "open"]), None);
        // The first --open… decides; other arguments don't matter.
        assert_eq!(wants_main(&["x", "--verbose", "--open=today", "--open"]), Some(Some("today".to_string())));
        assert_eq!(wants_main(&vec!["x".to_string(), "--open".to_string()]), Some(None));
    }

    #[test]
    fn fit_shrinks_only_what_is_too_big() {
        // 1920x1080 at 125 %: 1440x900 logical is 1800x1125 physical; the work area is 1920x1020.
        assert_eq!(fit_into_work_area((1800, 1125), (1920, 1020)), Some((1800, 1020)));
        assert_eq!(fit_into_work_area((1440, 900), (1920, 1020)), None);
        assert_eq!(fit_into_work_area((2000, 1200), (1920, 1020)), Some((1920, 1020)));
        // An unknown (zero) work area changes nothing.
        assert_eq!(fit_into_work_area((1440, 900), (0, 0)), None);
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
