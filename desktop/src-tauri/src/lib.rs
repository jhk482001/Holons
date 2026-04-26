use tauri::{
    menu::{Menu, MenuItem, PredefinedMenuItem},
    tray::{MouseButton, MouseButtonState, TrayIconBuilder, TrayIconEvent},
    Emitter, Manager,
};

use std::io::BufRead;
use std::sync::Mutex;

static SIDECAR_PORT: Mutex<Option<u16>> = Mutex::new(None);

#[tauri::command]
fn start_sidecar(app_handle: tauri::AppHandle) -> Result<u16, String> {
    // Check if already running
    if let Ok(guard) = SIDECAR_PORT.lock() {
        if let Some(port) = *guard {
            return Ok(port);
        }
    }

    // Try to find the bundled sidecar binary first (production).
    // Fall back to python3 -m backend.standalone (dev mode).
    let resource_dir = app_handle.path().resource_dir().unwrap_or_default();
    let bundled_sidecar = resource_dir.join("agent-company-backend");

    let child = if bundled_sidecar.exists() {
        // Production: use bundled binary
        std::process::Command::new(&bundled_sidecar)
            .args(["--port", "0"])
            .env("DB_BACKEND", "sqlite")
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .spawn()
            .map_err(|e| format!("Failed to start bundled sidecar: {}", e))?
    } else {
        // Dev mode: find the project root (desktop/src-tauri → ../../)
        // and run python3 -m backend.standalone
        let cwd = std::env::current_dir().unwrap_or_default();
        let project_root = cwd
            .parent() // desktop/
            .and_then(|p| p.parent()) // agent_company/
            .unwrap_or(&cwd);

        std::process::Command::new("python3")
            .args(["-m", "backend.standalone", "--port", "0"])
            .current_dir(project_root)
            .env("DB_BACKEND", "sqlite")
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .spawn()
            .map_err(|e| format!("Failed to start python sidecar: {}", e))?
    };

    // Read stdout line by line to find PORT=XXXX. Tee everything (pre- and
    // post-PORT) to ~/.agent_company/sidecar.log so a user can see why the
    // backend is silent when the app misbehaves.
    let log_path = dirs_home().join(".agent_company").join("sidecar.log");
    if let Some(parent) = log_path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    let mut logfile = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&log_path)
        .ok();

    let stdout = child.stdout.ok_or("No stdout")?;
    let mut reader = std::io::BufReader::new(stdout);
    let mut line_buf = String::new();
    loop {
        line_buf.clear();
        let n = reader
            .read_line(&mut line_buf)
            .map_err(|e| format!("Read error: {}", e))?;
        if n == 0 {
            break; // EOF
        }
        if let Some(f) = logfile.as_mut() {
            use std::io::Write;
            let _ = f.write_all(line_buf.as_bytes());
        }
        let line = line_buf.trim();
        if let Some(rest) = line.strip_prefix("PORT=") {
            let port: u16 = rest.parse().map_err(|e| format!("Invalid port: {}", e))?;
            if let Ok(mut guard) = SIDECAR_PORT.lock() {
                *guard = Some(port);
            }
            // Drain remaining stdout in background, teeing to the log.
            let mut tail_file = logfile;
            std::thread::spawn(move || {
                let mut buf = String::new();
                loop {
                    buf.clear();
                    let n = reader.read_line(&mut buf).unwrap_or(0);
                    if n == 0 {
                        break;
                    }
                    if let Some(f) = tail_file.as_mut() {
                        use std::io::Write;
                        let _ = f.write_all(buf.as_bytes());
                    }
                }
            });
            return Ok(port);
        }
    }
    Err("Sidecar did not report a port".to_string())
}

fn dirs_home() -> std::path::PathBuf {
    if let Ok(h) = std::env::var("HOME") {
        return std::path::PathBuf::from(h);
    }
    std::env::temp_dir()
}

fn sidecar_bin(app_handle: &tauri::AppHandle) -> Option<std::path::PathBuf> {
    let resource_dir = app_handle.path().resource_dir().ok()?;
    let bundled = resource_dir.join("agent-company-backend");
    if bundled.exists() {
        Some(bundled)
    } else {
        None
    }
}

// Apply NSColor.clearColor to the NSWindow background + setOpaque(NO).
// macOS otherwise paints its default dark windowBackgroundColor under
// any non-fully-opaque webview pixels, which is exactly the black ring
// users see around the transparent bust PNGs. Called once at app
// startup; the effect persists for the window's lifetime.
#[cfg(target_os = "macos")]
fn clear_native_background(window: &tauri::WebviewWindow) {
    use cocoa::base::{id, nil, NO};
    use objc::{class, msg_send, sel, sel_impl};
    unsafe {
        let ns_window = match window.ns_window() {
            Ok(p) => p as id,
            Err(e) => {
                log::warn!("ns_window unavailable for background clear: {e}");
                return;
            }
        };
        if ns_window == nil {
            return;
        }
        let clear: id = msg_send![class!(NSColor), clearColor];
        let _: () = msg_send![ns_window, setBackgroundColor: clear];
        let _: () = msg_send![ns_window, setOpaque: NO];
    }
}

// ---------- DB preflight check --------------------------------------------
// Run the sidecar in --preflight mode so it can report whether the user's
// ~/.agent_company/data.db has a schema older than the one this .app
// expects. The desktop UI shows an upgrade + backup prompt when needed.

#[tauri::command]
fn check_db_upgrade(app_handle: tauri::AppHandle) -> Result<serde_json::Value, String> {
    let output = if let Some(bin) = sidecar_bin(&app_handle) {
        std::process::Command::new(&bin)
            .args(["--preflight"])
            .env("DB_BACKEND", "sqlite")
            .env("HOLONS_PREFLIGHT", "1")
            .output()
            .map_err(|e| format!("preflight spawn failed: {}", e))?
    } else {
        // Dev mode: python3 -m backend.standalone --preflight
        let cwd = std::env::current_dir().unwrap_or_default();
        let project_root = cwd
            .parent()
            .and_then(|p| p.parent())
            .unwrap_or(&cwd)
            .to_path_buf();
        std::process::Command::new("python3")
            .args(["-m", "backend.standalone", "--preflight"])
            .current_dir(&project_root)
            .env("DB_BACKEND", "sqlite")
            .env("HOLONS_PREFLIGHT", "1")
            .output()
            .map_err(|e| format!("preflight python spawn failed: {}", e))?
    };

    let stdout = String::from_utf8_lossy(&output.stdout);
    for line in stdout.lines() {
        if let Some(rest) = line.strip_prefix("PREFLIGHT=") {
            return serde_json::from_str::<serde_json::Value>(rest)
                .map_err(|e| format!("preflight json parse failed: {}", e));
        }
    }
    // No PREFLIGHT line — treat as ok (fresh sidecar binary that doesn't
    // yet support preflight; fall back to old behavior).
    Ok(serde_json::json!({
        "status": "ok",
        "mode": "personal",
        "missing_tables": [],
        "note": "preflight unsupported by bundled sidecar",
    }))
}

#[tauri::command]
fn backup_personal_db() -> Result<serde_json::Value, String> {
    let db = dirs_home().join(".agent_company").join("data.db");
    if !db.exists() {
        return Err(format!("db not found at {}", db.display()));
    }
    // Timestamp: YYYYMMDD-HHMMSS using chrono-less formatting to avoid
    // adding a new dep.
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_err(|e| format!("time error: {}", e))?
        .as_secs();
    // crude local format (UTC is fine — this is just a filename tag).
    let dest = db.with_file_name(format!("data.db.backup-{}.db", now));
    std::fs::copy(&db, &dest).map_err(|e| format!("copy failed: {}", e))?;
    let size = std::fs::metadata(&dest).map(|m| m.len()).unwrap_or(0);
    Ok(serde_json::json!({
        "path": dest.display().to_string(),
        "size_bytes": size,
    }))
}

#[tauri::command]
fn set_click_through(window: tauri::WebviewWindow, ignore: bool) {
    let _ = window.set_ignore_cursor_events(ignore);
}

#[tauri::command]
fn focus_window(window: tauri::WebviewWindow) {
    let _ = window.show();
    let _ = window.set_focus();
}

#[tauri::command]
fn open_url(url: String) -> Result<(), String> {
    open::that(&url).map_err(|e| format!("open failed: {}", e))
}

// ---------- dock attention (bounce on macOS, flash taskbar on Windows) -----
// Fired from the frontend whenever a new Lead / agent message arrives while
// the window is not in focus, so the user notices even if the webview has
// been backgrounded and its JS timers have been throttled by Chromium.
#[tauri::command]
fn request_attention(window: tauri::WebviewWindow) {
    let _ = window.request_user_attention(Some(tauri::UserAttentionType::Informational));
}

// Stronger version: bounce until the user clicks the dock icon. Useful
// when a long-running workflow finishes while the user is in another app.
#[tauri::command]
fn request_attention_critical(window: tauri::WebviewWindow) {
    let _ = window.request_user_attention(Some(tauri::UserAttentionType::Critical));
}

// Set the macOS Dock badge label (e.g. "3" for unread items, or empty
// to clear). Tauri 2 doesn't expose NSDockTile directly, so we drop down
// to objc. Pass `None` / empty string to clear.
#[tauri::command]
fn set_dock_badge(label: Option<String>) {
    #[cfg(target_os = "macos")]
    {
        use cocoa::base::{id, nil};
        use cocoa::foundation::NSString;
        use objc::{class, msg_send, sel, sel_impl};
        unsafe {
            let app: id = msg_send![class!(NSApplication), sharedApplication];
            if app == nil { return; }
            let dock_tile: id = msg_send![app, dockTile];
            if dock_tile == nil { return; }
            let text = label.unwrap_or_default();
            if text.is_empty() {
                let _: () = msg_send![dock_tile, setBadgeLabel: nil];
            } else {
                let ns_str = NSString::alloc(nil).init_str(&text);
                let _: () = msg_send![dock_tile, setBadgeLabel: ns_str];
            }
        }
    }
    #[cfg(not(target_os = "macos"))]
    {
        let _ = label;
    }
}

fn show_and_focus(app: &tauri::AppHandle) {
    if let Some(w) = app.get_webview_window("main") {
        let _ = w.show();
        let _ = w.unminimize();
        let _ = w.set_focus();
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_store::Builder::default().build())
        .plugin(
            tauri_plugin_log::Builder::default()
                .level(log::LevelFilter::Info)
                .build(),
        )
        .plugin(tauri_plugin_notification::init())
        .invoke_handler(tauri::generate_handler![
            start_sidecar,
            check_db_upgrade,
            backup_personal_db,
            set_click_through,
            focus_window,
            open_url,
            request_attention,
            request_attention_critical,
            set_dock_badge,
        ])
        .setup(|app| {
            // Build-version display in the tray menu — disabled so it's
            // non-interactive but visible. Lets the user verify which
            // .app they're actually running without opening the UI.
            let version_label = format!("Version: {}", env!("HOLONS_BUILD_VERSION"));
            let version_i = MenuItem::with_id(app, "version", &version_label, false, None::<&str>)?;

            let show_i = MenuItem::with_id(app, "show", "Show Holons", true, None::<&str>)?;

            // Bust size submenu
            let size_small = MenuItem::with_id(app, "size_small", "Small", true, None::<&str>)?;
            let size_medium =
                MenuItem::with_id(app, "size_medium", "Medium ✓", true, None::<&str>)?;
            let size_large = MenuItem::with_id(app, "size_large", "Large", true, None::<&str>)?;
            let size_menu = tauri::menu::Submenu::with_items(
                app,
                "Character size",
                true,
                &[&size_small, &size_medium, &size_large],
            )?;

            let reset_pos_i = MenuItem::with_id(
                app,
                "reset_positions",
                "Reset character positions",
                true,
                None::<&str>,
            )?;
            let connection_i = MenuItem::with_id(
                app,
                "connection_settings",
                "Connection settings…",
                true,
                None::<&str>,
            )?;
            let open_web_i =
                MenuItem::with_id(app, "open_web", "Open web settings", true, None::<&str>)?;

            // Language submenu
            let lang_en = MenuItem::with_id(app, "lang_en", "English", true, None::<&str>)?;
            let lang_zh = MenuItem::with_id(app, "lang_zh_tw", "繁體中文", true, None::<&str>)?;
            let lang_menu =
                tauri::menu::Submenu::with_items(app, "Language", true, &[&lang_en, &lang_zh])?;

            let sep = PredefinedMenuItem::separator(app)?;
            let sep2 = PredefinedMenuItem::separator(app)?;
            // Show-lead-only: compact overlay that hides the full cast
            // and only keeps the Lead bust + its chat. Toggleable from
            // the tray. The frontend listens for "toggle-show-lead-only".
            let lead_only_i = MenuItem::with_id(
                app,
                "toggle_show_lead_only",
                "Show Lead only",
                true,
                None::<&str>,
            )?;
            let quit_i = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;

            let menu = Menu::with_items(
                app,
                &[
                    &version_i,
                    &sep,
                    &show_i,
                    &size_menu,
                    &reset_pos_i,
                    &lang_menu,
                    &lead_only_i,
                    &connection_i,
                    &open_web_i,
                    &sep2,
                    &quit_i,
                ],
            )?;

            let icon = app.default_window_icon().cloned().expect("default icon");

            let _tray = TrayIconBuilder::new()
                .icon(icon)
                .icon_as_template(true)
                .menu(&menu)
                .tooltip("Holons")
                .on_menu_event(move |app, event| match event.id.as_ref() {
                    "show" => {
                        show_and_focus(app);
                    }
                    "size_small" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.emit("set-bust-size", "small");
                        }
                    }
                    "size_medium" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.emit("set-bust-size", "medium");
                        }
                    }
                    "size_large" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.emit("set-bust-size", "large");
                        }
                    }
                    "reset_positions" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.emit("reset-cast-positions", ());
                        }
                    }
                    "lang_en" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.emit("set-lang", "en");
                        }
                    }
                    "lang_zh_tw" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.emit("set-lang", "zh-TW");
                        }
                    }
                    "connection_settings" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.emit("reset-connection", ());
                        }
                    }
                    "open_web" => {
                        if let Some(port) = SIDECAR_PORT.lock().ok().and_then(|g| *g) {
                            let _ = open::that(format!("http://localhost:{}/settings", port));
                        }
                    }
                    "toggle_show_lead_only" => {
                        if let Some(w) = app.get_webview_window("main") {
                            let _ = w.emit("toggle-show-lead-only", ());
                        }
                    }
                    "quit" => {
                        app.exit(0);
                    }
                    _ => {}
                })
                .on_tray_icon_event(|tray, event| {
                    if let TrayIconEvent::Click {
                        button: MouseButton::Left,
                        button_state: MouseButtonState::Up,
                        ..
                    } = event
                    {
                        show_and_focus(tray.app_handle());
                    }
                })
                .build(app)?;

            if let Some(window) = app.get_webview_window("main") {
                let _ = window.maximize();
                // Kill the macOS default NSWindow background so transparent
                // PNGs (the bust composites) don't ring with a dark halo
                // against the system's default window background colour.
                #[cfg(target_os = "macos")]
                clear_native_background(&window);
            }

            Ok(())
        })
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
