//! ATLAS desktop shell (design §8): the `widget` window, the `main` window (phase 4.5), the
//! tray, autostart and the hub supervisor. The hub serves both pages; this shell only frames them.

pub mod claude_watch;
pub mod config;
pub mod migrate;
pub mod supervisor;
pub mod voice;

use std::process::Command;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};
use std::time::{Duration, Instant};

use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem, Submenu};
use tauri::tray::TrayIconBuilder;
use tauri::webview::PageLoadEvent;
use tauri::window::Color;
use tauri::{
    AppHandle, LogicalSize, Manager, PhysicalPosition, RunEvent, Runtime, Theme, WebviewUrl, WebviewWindow,
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
    !head.is_empty() && head.bytes().all(|b| b.is_ascii_lowercase()) && tail.is_none_or(tail_ok)
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

/// How long a navigation may go without its window reaching the hub page's URL before the shell
/// tries again (the load never started). A load that started and failed is caught sooner, by
/// `on_page_load` (see `after_load`).
const LOAD_GRACE: Duration = Duration::from_secs(20);

/// Where a hub window (the widget, the main window) stands with its hub page.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Page {
    /// On its starting page, or the last load of the hub page failed (the web view's "could not
    /// connect" page, whose URL is the hub's): go to the hub page once the hub answers.
    Start,
    /// Sent to the hub page at this instant; its load hasn't finished yet.
    Loading(Instant),
    /// The hub page finished loading while the hub answered. It is never reloaded after that: the
    /// page rides out a hub restart by itself (5 s retry, WebSocket reconnect), and a reload would
    /// drop what the owner was typing.
    Loaded,
}

/// What to do with a hub window (`next_step`).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Step {
    /// On the hub page: leave it (a view goes there by its hash).
    OnHub,
    /// Not there yet, but nothing to do now (the hub is down, or a load is under way).
    Wait,
    /// Point it at the hub page.
    Navigate,
}

/// The next step for a window in state `page`, whose URL is `current` (when it can be read), on a
/// hub that answered at the last tick (`up`). `target` is the hub page's URL, or a prefix of every
/// URL that counts as on it. A loaded page whose URL is somehow off the hub's is sent back; so is
/// a load that still isn't on the hub's URL after `LOAD_GRACE`.
pub fn next_step(up: bool, page: Page, now: Instant, current: Option<&str>, target: &str) -> Step {
    let off = current.is_some_and(|u| !u.starts_with(target));
    let go = if up { Step::Navigate } else { Step::Wait };
    match page {
        Page::Start => go,
        Page::Loaded if !off => Step::OnHub,
        Page::Loaded => go,
        Page::Loading(since) if off && now.saturating_duration_since(since) >= LOAD_GRACE => go,
        Page::Loading(_) => Step::Wait,
    }
}

/// The state after a hub page finished loading (`PageLoadEvent::Finished` at the hub's URL) and the
/// hub did or didn't answer right after (`answered`). The web views (WebKitGTK, WebView2) report
/// a failed load as finished, at the URL that failed, so a hub that doesn't answer means the
/// window shows the "could not connect" page: back to `Start`, to load again once the hub is up.
/// After a failure a finished load stays `Start`: it is the error page itself, or a stale event,
/// never one of the shell's navigations (those set `Loading`).
pub fn after_load(page: Page, answered: bool) -> Page {
    match (page, answered) {
        (Page::Start, _) => Page::Start,
        (_, true) => Page::Loaded,
        (_, false) => Page::Start,
    }
}

/// The view in a main-window URL (`http://127.0.0.1:<port>/#/<view>`), if it is a valid one: a
/// load that failed keeps the view it was going to.
pub fn view_in(url: &str, port: u16) -> Option<String> {
    let view = url.strip_prefix(&main_url(port, None))?.strip_prefix("#/")?;
    valid_view(view).then(|| view.to_string())
}

/// A hub window's navigation state. Read and changed on the main thread only (`show_main`, the
/// watch tick's `steer`, `load_settled`), so a requested view can't be lost to a navigation that
/// races it; the lock is there for `Sync`.
struct HubNav {
    page: Page,
    /// A view asked for before the main window was on the hub page: the next navigation takes it,
    /// or (if a load is under way) its hash once that load has finished.
    pending: Option<String>,
}

/// One window the hub serves a page to.
struct HubWindow {
    label: &'static str,
    /// The hub page's URL (the widget), or the prefix of its every view (the main window).
    target: String,
    nav: Mutex<HubNav>,
    /// Bumped by every navigation, so a load result that a newer navigation overtook is dropped.
    /// Outside the lock: `page_finished` reads it from the web view's callback, which must never
    /// wait for a lock the main thread may hold around a navigation.
    loads: AtomicU64,
}

impl HubWindow {
    fn new(label: &'static str, target: String) -> Self {
        let nav = Mutex::new(HubNav { page: Page::Start, pending: None });
        HubWindow { label, target, nav, loads: AtomicU64::new(0) }
    }

    fn lock(&self) -> MutexGuard<'_, HubNav> {
        self.nav.lock().unwrap_or_else(|e| e.into_inner())
    }

    fn step(&self, up: bool, w: &WebviewWindow, nav: &HubNav) -> Step {
        let current = w.url().ok().map(|u| u.to_string());
        next_step(up, nav.page, Instant::now(), current.as_deref(), &self.target)
    }

    /// Point `w` at the hub page (the main window: with the pending view).
    fn navigate(&self, port: u16, w: &WebviewWindow, nav: &mut HubNav) {
        let url = if self.label == MAIN { main_url(port, nav.pending.as_deref()) } else { self.target.clone() };
        if let Ok(url) = url.parse() {
            if w.navigate(url).is_ok() {
                nav.page = Page::Loading(Instant::now());
                nav.pending = None;
                self.loads.fetch_add(1, Ordering::SeqCst);
            }
        }
    }
}

/// Managed state: the hub's port, whether it answered at the last tick, and both hub windows.
struct Hub {
    port: u16,
    up: AtomicBool,
    widget: HubWindow,
    main: HubWindow,
}

impl Hub {
    fn new(port: u16) -> Self {
        Hub {
            port,
            up: AtomicBool::new(false),
            widget: HubWindow::new(WIDGET, format!("http://127.0.0.1:{port}/widget")),
            main: HubWindow::new(MAIN, main_url(port, None)),
        }
    }

    fn window(&self, label: &str) -> Option<&HubWindow> {
        [&self.widget, &self.main].into_iter().find(|w| w.label == label)
    }
}

/// Each watch tick, on the main thread: point the widget and the main window (if it was opened)
/// at their hub pages when `next_step` says so.
fn steer(app: &AppHandle) {
    let Some(hub) = app.try_state::<Hub>() else { return };
    let up = hub.up.load(Ordering::SeqCst);
    for hw in [&hub.widget, &hub.main] {
        if let Some(w) = app.get_webview_window(hw.label) {
            let mut nav = hw.lock();
            if hw.step(up, &w, &nav) == Step::Navigate {
                hw.navigate(hub.port, &w, &mut nav);
            }
        }
    }
}

/// `on_page_load` for both windows (the main thread): a load at the hub page's URL finished. Ask
/// the hub whether it answers (off the main thread), then settle the window's state on the main
/// thread. The page itself is never asked: the main window has no IPC.
fn page_finished(w: &WebviewWindow, url: &tauri::Url) {
    let app = w.app_handle().clone();
    let Some(hub) = app.try_state::<Hub>() else { return };
    let Some(hw) = hub.window(w.label()) else { return };
    let url = url.to_string();
    if !url.starts_with(&hw.target) {
        return; // its starting page
    }
    let (port, label, loads) = (hub.port, hw.label, hw.loads.load(Ordering::SeqCst));
    std::thread::spawn(move || {
        let answered = supervisor::hub_up(port);
        let handle = app.clone();
        let _ = app.run_on_main_thread(move || load_settled(&handle, label, loads, answered, &url));
    });
}

/// The main-thread half of `page_finished`: the new state (`after_load`), the main window's pending
/// view by its hash once its page has loaded, and a failed load's view kept for the next try.
fn load_settled(app: &AppHandle, label: &str, loads: u64, answered: bool, url: &str) {
    let Some(hub) = app.try_state::<Hub>() else { return };
    let Some(hw) = hub.window(label) else { return };
    let mut nav = hw.lock();
    if hw.loads.load(Ordering::SeqCst) != loads {
        return; // a newer navigation: its own load decides
    }
    let was = nav.page;
    nav.page = after_load(was, answered);
    match nav.page {
        Page::Start if was != Page::Start => {
            if label == MAIN && nav.pending.is_none() {
                nav.pending = view_in(url, hub.port);
            }
            eprintln!("atlas-desktop: the {label} window could not load the hub page; again once the hub answers");
        }
        Page::Loaded => {
            if let (Some(view), Some(w)) = (nav.pending.take(), app.get_webview_window(label)) {
                let _ = w.eval(hash_script(&view));
            }
        }
        _ => {}
    }
}

/// The margin `prevent_overflow_with_margin` keeps free below the main window's requested inner
/// size (logical pixels). On Windows tauri adds the title bar itself; on Linux it doesn't, and
/// KWin's title bar and frame are about 30 px, so 40 leaves room for them.
#[cfg(target_os = "linux")]
const OVERFLOW_MARGIN: (f64, f64) = (0.0, 40.0);
#[cfg(not(target_os = "linux"))]
const OVERFLOW_MARGIN: (f64, f64) = (0.0, 0.0);

/// The main window, built on first use (phase 4.5, design §6): decorated, resizable, in the taskbar
/// and Alt+Tab, black from the first frame, hidden until `show_main` shows it. It starts on
/// `main.html` ("STARTING THE HUB…"); the watch tick or `show_main` takes it to the hub page.
/// Its 1440×900 is shrunk to the primary monitor's work area before it is built (tauri works it
/// out from the requested size, scale-aware: the unmapped window can't be measured on Linux); the
/// window-state plugin then restores the remembered position, size and maximised state, which
/// win. Call it on the main thread, outside a sent message (`open_main_later`).
fn ensure_main(app: &AppHandle) -> Option<WebviewWindow> {
    if let Some(w) = app.get_webview_window(MAIN) {
        return Some(w);
    }
    let built = WebviewWindowBuilder::new(app, MAIN, WebviewUrl::App("main.html".into()))
        .title("ATLAS")
        .inner_size(1440.0, 900.0)
        .min_inner_size(900.0, 600.0)
        .prevent_overflow_with_margin(LogicalSize::new(OVERFLOW_MARGIN.0, OVERFLOW_MARGIN.1))
        // First open only: window-state replaces it with the remembered position.
        .center()
        .decorations(true)
        .resizable(true)
        .skip_taskbar(false)
        .theme(Some(Theme::Dark))
        .background_color(Color(0, 0, 0, 255))
        .visible(false)
        .on_page_load(|w, p| {
            if p.event() == PageLoadEvent::Finished {
                page_finished(&w, p.url());
            }
        })
        .build();
    match built {
        Ok(w) => {
            keep_on_screen(&w.as_ref().window());
            Some(w)
        }
        Err(e) => {
            eprintln!("atlas-desktop: could not open the main window: {e}");
            app.get_webview_window(MAIN)
        }
    }
}

/// Open the main window (design §6): build it if needed; on the hub page already, go to `view` by
/// its hash (no reload); otherwise remember `view` and go to the hub page now if the hub answers
/// (else the watch tick does, once it does; a load under way takes it by its hash when it has
/// finished). Then unminimise, show and focus it. A view that isn't `valid_view` is dropped.
/// True when the window is showing. Call it on the main thread (`open_main_later` gets there).
fn show_main(app: &AppHandle, view: Option<String>) -> bool {
    let view = view.filter(|v| valid_view(v));
    let Some(hub) = app.try_state::<Hub>() else { return false };
    let Some(w) = ensure_main(app) else { return false };
    {
        let hw = &hub.main;
        let mut nav = hw.lock();
        match hw.step(hub.up.load(Ordering::SeqCst), &w, &nav) {
            Step::OnHub => {
                if let Some(v) = &view {
                    let _ = w.eval(hash_script(v));
                }
            }
            step => {
                if view.is_some() {
                    nav.pending = view;
                }
                if step == Step::Navigate {
                    hw.navigate(hub.port, &w, &mut nav);
                }
            }
        }
    }
    let _ = w.unminimize();
    let shown = w.show().is_ok();
    let _ = w.set_focus();
    shown
}

/// `show_main` on the main thread, from anywhere (the single-instance callback): building a window
/// and reading its URL both need it. Always from a new thread: `run_on_main_thread` runs the task
/// inline when it is called on the main thread (tauri-runtime-wry's `send_user_message`), and on
/// Windows the single-instance callback runs on the main thread inside the plugin's WM_COPYDATA
/// window procedure, a sent message where WebView2 can't be built. From another thread the task is
/// posted to the event loop and runs after that message has returned.
fn open_main_later(app: &AppHandle, view: Option<String>) {
    let app = app.clone();
    std::thread::spawn(move || {
        let handle = app.clone();
        if let Err(e) = app.run_on_main_thread(move || {
            show_main(&handle, view);
        }) {
            eprintln!("atlas-desktop: could not open the main window: {e}");
        }
    });
}

/// How long `open_main` waits for the main thread (the widget gives up after 3 s anyway).
const OPEN_WAIT: Duration = Duration::from_secs(5);

/// The widget's tiles open the main window at a view: `window.__TAURI__.core.invoke('open_main',
/// {view})`. Granted only to the widget (`allow-open-main` in `capabilities/widget.json`). Async,
/// so it runs on the async runtime, never on the main thread (the Windows deadlock when a command
/// builds a window): `run_on_main_thread` posts `show_main`, and the command waits for its answer.
/// An error when the window could not be shown (no state, the build failed, no answer in time), so
/// the widget falls back to POST /api/open.
#[tauri::command]
async fn open_main(app: AppHandle, view: Option<String>) -> Result<(), String> {
    let (tx, rx) = std::sync::mpsc::channel();
    let handle = app.clone();
    app.run_on_main_thread(move || {
        let _ = tx.send(show_main(&handle, view));
    })
    .map_err(|e| format!("could not reach the main thread: {e}"))?;
    match tauri::async_runtime::spawn_blocking(move || rx.recv_timeout(OPEN_WAIT)).await {
        Ok(Ok(true)) => Ok(()),
        Ok(Ok(false)) => Err("could not show the main window".into()),
        _ => Err("the main window did not answer in time".into()),
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
        let tooltip = notice.map_or_else(|| "ATLAS".to_string(), |n| format!("ATLAS — {}", n.text()));
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
/// their hub pages once the port answers (`steer`, on the main thread: see `next_step`), and keep
/// "Restart hub" and the tray notice in sync. This thread never waits for the main thread: `steer`
/// is posted to it, where `show_main` runs too, so neither can undo the other's navigation.
/// In runtime mode a start first runs claude-sync and the update step (up to 150 s): that runs
/// here, without the supervisor's lock, so the tray's Restart and Quit never wait for it.
fn watch(app: AppHandle, sup: Shared, restart_item: MenuItem<Wry>, mut notice: TrayNotice) {
    std::thread::spawn(move || loop {
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
        if let Some(hub) = app.try_state::<Hub>() {
            hub.up.store(up, Ordering::SeqCst);
        }
        let handle = app.clone();
        let _ = app.run_on_main_thread(move || steer(&handle));
        let _ = restart_item.set_enabled(owns);
        std::thread::sleep(TICK);
    });
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    #[cfg(target_os = "linux")]
    use_xwayland();
    // The cut-over from the pre-rename folders comes first: it decides which desktop.json to read.
    let migration = migrate::run();
    let cfg = migration.fix(migration.config.as_deref().map(config::load).unwrap_or_default());
    let autostart_moved = migration.autostart_removed;
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
            app.manage(Hub::new(port));
            let widget = WebviewWindowBuilder::new(app, WIDGET, WebviewUrl::App("index.html".into()))
                .title("ATLAS")
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
                .on_page_load(|w, p| {
                    if p.event() == PageLoadEvent::Finished {
                        page_finished(&w, p.url());
                    }
                })
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
                eprintln!("atlas-desktop: SIGTERM/SIGINT won't quit cleanly: {e}");
            }
            let open = MenuItem::with_id(handle, "open", "Open ATLAS", true, None::<&str>)?;
            let browser = MenuItem::with_id(handle, "browser", "Open in browser", true, None::<&str>)?;
            let toggle = MenuItem::with_id(handle, "toggle", "Show/Hide widget", true, None::<&str>)?;
            // The migration removed the pre-rename login entry: this shell's own takes its place.
            if autostart_moved {
                if let Err(e) = handle.autolaunch().enable() {
                    eprintln!("atlas-desktop: could not turn start at login on: {e}");
                }
            }
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
            let mut tray = TrayIconBuilder::with_id(TRAY).tooltip("ATLAS").menu(&menu).on_menu_event(
                move |app, e| match e.id.as_ref() {
                    // The menu runs on the main thread, from tauri's own posted event (muda's handler
                    // only forwards it to the event loop), never inside a sent message: show_main can
                    // build the window here.
                    "open" => {
                        show_main(app, None);
                    }
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
                                Ok(status) => eprintln!("atlas-desktop: pause voice: the hub answered {status}"),
                                Err(e) => eprintln!("atlas-desktop: pause voice: {e}"),
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
            watch(handle.clone(), sup.clone(), restart, TrayNotice { menu, line: notice_line, shown: None });
            // Started by the launcher (`--open[=view]`): the main window too, not only the widget.
            // args_os: `std::env::args` panics on an argument that isn't UTF-8.
            let args: Vec<String> = std::env::args_os().map(|a| a.to_string_lossy().into_owned()).collect();
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
        .expect("error while building the ATLAS desktop shell");

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

    fn at(secs: u64) -> (Instant, Instant) {
        let t0 = Instant::now();
        (t0, t0 + Duration::from_secs(secs))
    }

    #[test]
    fn navigates_once_when_the_hub_answers() {
        let (t0, now) = at(1);
        let start = Some("http://tauri.localhost/index.html");
        assert_eq!(next_step(false, Page::Start, now, start, T), Step::Wait);
        assert_eq!(next_step(true, Page::Start, now, start, T), Step::Navigate);
        assert_eq!(next_step(true, Page::Start, now, None, T), Step::Navigate);
        // A load under way: wait for it (its URL may still be the starting page's for a moment).
        assert_eq!(next_step(true, Page::Loading(t0), now, start, T), Step::Wait);
        assert_eq!(next_step(true, Page::Loading(t0), now, Some(T), T), Step::Wait);
        // On the hub page: a hub restart (down, then up) must not reload it.
        assert_eq!(next_step(true, Page::Loaded, now, Some(T), T), Step::OnHub);
        assert_eq!(next_step(false, Page::Loaded, now, Some(T), T), Step::OnHub);
        assert_eq!(next_step(true, Page::Loaded, now, Some("http://127.0.0.1:8099/widget#x"), T), Step::OnHub);
        assert_eq!(next_step(true, Page::Loaded, now, None, T), Step::OnHub);
        // Somehow not on the hub page any more: point it back, once the hub answers.
        assert_eq!(next_step(true, Page::Loaded, now, start, T), Step::Navigate);
        assert_eq!(next_step(false, Page::Loaded, now, start, T), Step::Wait);
    }

    #[test]
    fn a_load_that_never_reaches_the_hub_url_is_tried_again() {
        let start = Some("tauri://localhost/main.html");
        let (t0, late) = at(LOAD_GRACE.as_secs());
        assert_eq!(next_step(true, Page::Loading(t0), late, start, T), Step::Navigate);
        assert_eq!(next_step(false, Page::Loading(t0), late, start, T), Step::Wait);
        // At the hub's URL it is the web view's own business (a failure comes back as Start).
        assert_eq!(next_step(true, Page::Loading(t0), late, Some(T), T), Step::Wait);
        let (t0, early) = at(LOAD_GRACE.as_secs() - 1);
        assert_eq!(next_step(true, Page::Loading(t0), early, start, T), Step::Wait);
        // A clock read before the navigation's own (never negative).
        assert_eq!(next_step(true, Page::Loading(early), t0, start, T), Step::Wait);
    }

    #[test]
    fn a_failed_load_goes_back_to_start() {
        let t0 = Instant::now();
        // The shell's navigation finished while the hub answered: loaded, never reloaded.
        assert_eq!(after_load(Page::Loading(t0), true), Page::Loaded);
        // It finished on the "could not connect" page (the hub didn't answer): try again later.
        assert_eq!(after_load(Page::Loading(t0), false), Page::Start);
        // The page reloaded itself: fine while the hub answers, the error page when it doesn't.
        assert_eq!(after_load(Page::Loaded, true), Page::Loaded);
        assert_eq!(after_load(Page::Loaded, false), Page::Start);
        // After a failure, the error page's own load (or a stale event) never counts as loaded.
        assert_eq!(after_load(Page::Start, true), Page::Start);
        assert_eq!(after_load(Page::Start, false), Page::Start);
        // The cycle: down → Start → navigate once up → Loading → Loaded.
        let page = after_load(Page::Loading(t0), false);
        assert_eq!(next_step(true, page, t0, Some("http://127.0.0.1:8099/"), "http://127.0.0.1:8099/"), Step::Navigate);
    }

    #[test]
    fn failed_loads_keep_their_view() {
        assert_eq!(view_in("http://127.0.0.1:8099/#/agents", 8099), Some("agents".to_string()));
        assert_eq!(view_in("http://127.0.0.1:8099/#/settings/tablet", 8099), Some("settings/tablet".to_string()));
        assert_eq!(view_in("http://127.0.0.1:8099/", 8099), None);
        assert_eq!(view_in("http://127.0.0.1:8099/#/Agents", 8099), None);
        assert_eq!(view_in("http://127.0.0.1:8099/#agents", 8099), None);
        assert_eq!(view_in("http://127.0.0.1:8090/#/agents", 8099), None);
        assert_eq!(view_in("http://127.0.0.1:8099/widget", 8099), None);
    }

    #[test]
    fn main_window_navigates_independently_of_the_widget() {
        let now = Instant::now();
        let m = main_url(8099, None);
        assert_eq!(m, "http://127.0.0.1:8099/");
        // First page: go to the hub once it answers.
        assert_eq!(next_step(false, Page::Start, now, Some("tauri://localhost/main.html"), &m), Step::Wait);
        assert_eq!(next_step(true, Page::Start, now, Some("tauri://localhost/main.html"), &m), Step::Navigate);
        assert_eq!(next_step(true, Page::Start, now, Some("http://tauri.localhost/main.html"), &m), Step::Navigate);
        // A failed load sits at the hub's URL: it is Start, so it loads again.
        assert_eq!(next_step(true, Page::Start, now, Some("http://127.0.0.1:8099/#/agents"), &m), Step::Navigate);
        // On the hub page, at any view: never again (a hub restart must not reload it).
        for u in ["http://127.0.0.1:8099/", "http://127.0.0.1:8099/#/agents", "http://127.0.0.1:8099/#/settings/tablet"]
        {
            assert_eq!(next_step(true, Page::Loaded, now, Some(u), &m), Step::OnHub, "{u}");
        }
        assert_eq!(next_step(true, Page::Loaded, now, None, &m), Step::OnHub);
        // Somehow off it (the starting page, another port): back to the hub.
        assert_eq!(next_step(true, Page::Loaded, now, Some("tauri://localhost/main.html"), &m), Step::Navigate);
        assert_eq!(next_step(true, Page::Loaded, now, Some("http://127.0.0.1:8090/"), &m), Step::Navigate);
        // The widget's own target is unaffected by the main window's.
        assert_eq!(next_step(true, Page::Loaded, now, Some("http://127.0.0.1:8099/widget"), T), Step::OnHub);
        assert_eq!(next_step(true, Page::Loaded, now, Some("http://127.0.0.1:8099/#/today"), T), Step::Navigate);
    }

    #[test]
    fn hub_windows_have_their_own_targets() {
        let hub = Hub::new(8099);
        assert_eq!(hub.window(WIDGET).map(|w| w.target.as_str()), Some("http://127.0.0.1:8099/widget"));
        assert_eq!(hub.window(MAIN).map(|w| w.target.as_str()), Some("http://127.0.0.1:8099/"));
        assert!(hub.window("other").is_none());
        assert_eq!(hub.main.lock().page, Page::Start);
        assert!(!hub.up.load(Ordering::SeqCst));
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
        assert_eq!(wants_main(&["/usr/bin/atlas-desktop"]), None); // autostart: the widget
        assert_eq!(wants_main(&["atlas-desktop", "--open"]), Some(None));
        assert_eq!(wants_main(&["atlas-desktop", "--open=agents"]), Some(Some("agents".to_string())));
        let voice = Some(Some("settings/voice".to_string()));
        assert_eq!(wants_main(&["atlas-desktop", "--open=settings/voice"]), voice);
        // An invalid view, an empty one, look-alikes: the widget, as before.
        assert_eq!(wants_main(&["atlas-desktop", "--open=Agents"]), None);
        assert_eq!(wants_main(&["atlas-desktop", "--open="]), None);
        assert_eq!(wants_main(&["atlas-desktop", "--opened"]), None);
        assert_eq!(wants_main(&["atlas-desktop", "-open"]), None);
        assert_eq!(wants_main(&["atlas-desktop", "open"]), None);
        // The first --open… decides; other arguments don't matter.
        assert_eq!(wants_main(&["x", "--verbose", "--open=today", "--open"]), Some(Some("today".to_string())));
        let owned: Vec<String> = ["x", "--open"].iter().map(|a| a.to_string()).collect();
        assert_eq!(wants_main(&owned), Some(None));
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
