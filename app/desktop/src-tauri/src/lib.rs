//! BrainHub desktop shell (design §8): the `widget` window, the tray, autostart and the hub
//! supervisor. The hub serves the widget page; this shell only frames it.

pub mod config;
pub mod supervisor;

use std::sync::{Arc, Mutex, MutexGuard};
use std::time::Duration;

use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{
    AppHandle, Manager, PhysicalPosition, RunEvent, Runtime, WebviewUrl, WebviewWindowBuilder, Window, WindowEvent, Wry,
};
use tauri_plugin_autostart::{MacosLauncher, ManagerExt};
use tauri_plugin_window_state::StateFlags;

use supervisor::{Supervisor, CREATE_NO_WINDOW};

const WIDGET: &str = "widget";
const TICK: Duration = Duration::from_secs(2);

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
fn open_browser(url: &str) {
    let mut cmd = std::process::Command::new("cmd");
    cmd.args(["/C", "start", "", url]);
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    let _ = cmd.spawn();
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

/// Every 2 s: let the supervisor act, point the widget at the hub once the port answers (see
/// `needs_navigate`: never again after that), and keep "Restart hub" in sync.
fn watch(app: AppHandle, sup: Shared, port: u16, restart_item: MenuItem<Wry>) {
    std::thread::spawn(move || {
        let target = format!("http://127.0.0.1:{port}/widget");
        let mut navigated = false;
        loop {
            let (up, owns) = {
                let mut s = sup.lock();
                s.tick();
                (s.last_up(), s.owns_child())
            };
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
    let cfg = config::default_path().map(|p| config::load(&p)).unwrap_or_default();
    let port = cfg.port;
    let sup = Shared(Arc::new(Mutex::new(Supervisor::new(cfg))));
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
                .build()?;
            // A remembered (or first-launch) position may leave the window partly off-screen.
            keep_on_screen(&widget.as_ref().window());

            let handle = app.handle();
            let open = MenuItem::with_id(handle, "open", "Open BrainHub", true, None::<&str>)?;
            let toggle = MenuItem::with_id(handle, "toggle", "Show/Hide widget", true, None::<&str>)?;
            let autostart_on = handle.autolaunch().is_enabled().unwrap_or(false);
            let login = CheckMenuItem::with_id(handle, "login", "Start at login", true, autostart_on, None::<&str>)?;
            let restart = MenuItem::with_id(handle, "restart", "Restart hub", false, None::<&str>)?;
            let quit = MenuItem::with_id(handle, "quit", "Quit", true, None::<&str>)?;
            let sep = PredefinedMenuItem::separator(handle)?;
            let menu = Menu::with_items(handle, &[&open, &toggle, &login, &restart, &sep, &quit])?;

            let sup_menu = sup.clone();
            let login_item = login.clone();
            let mut tray = TrayIconBuilder::with_id("main").tooltip("BrainHub").menu(&menu).on_menu_event(
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

            watch(handle.clone(), sup.clone(), port, restart);
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
