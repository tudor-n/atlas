//! BrainHub desktop shell (design §8): the `widget` window, the tray, autostart and the hub
//! supervisor. The hub serves the widget page; this shell only frames it.

pub mod config;
pub mod supervisor;

use std::sync::{Arc, Mutex, MutexGuard};
use std::time::Duration;

use tauri::menu::{CheckMenuItem, Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder, WindowEvent, Wry};
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

/// Every 2 s: let the supervisor act, then point the widget at the hub whenever the port starts
/// answering (first time, and again after it was down), and keep "Restart hub" in sync.
fn watch(app: AppHandle, sup: Shared, port: u16, restart_item: MenuItem<Wry>) {
    std::thread::spawn(move || {
        let mut was_up = false;
        loop {
            let (up, owns) = {
                let mut s = sup.lock();
                s.tick();
                (s.last_up(), s.owns_child())
            };
            if up && !was_up {
                if let (Some(w), Ok(url)) =
                    (app.get_webview_window(WIDGET), format!("http://127.0.0.1:{port}/widget").parse())
                {
                    let _ = w.navigate(url);
                }
            }
            was_up = up;
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
            WebviewWindowBuilder::new(app, WIDGET, WebviewUrl::App("index.html".into()))
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
            // Alt+F4 on the frameless widget hides it; only the tray's Quit ends the shell.
            if let WindowEvent::CloseRequested { api, .. } = event {
                if window.label() == WIDGET {
                    api.prevent_close();
                    let _ = window.hide();
                }
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
