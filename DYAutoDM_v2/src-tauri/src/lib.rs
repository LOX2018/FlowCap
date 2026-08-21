//! DYAutoDM Tauri 主逻辑
//!
//! 职责：
//! 1. 创建桌面窗口加载前端
//! 2. 启动 Python FastAPI sidecar（dyautodm-backend 二进制）
//! 3. 暴露 Tauri 命令给前端：sidecar 管理、账号守护进程管理
//! 4. 监听前端 WebSocket 桥接事件（实时推送）

mod sidecar;

use std::sync::Mutex;
use tauri::Manager;

use sidecar::{SidecarHandle, SidecarManager};

/// 全局 SidecarManager（保活 Python 后端 + 各账号守护进程）
struct AppState {
    backend: Mutex<Option<SidecarHandle>>,
    daemons: Mutex<Vec<SidecarHandle>>,
}

#[tauri::command]
async fn backend_status(state: tauri::State<'_, AppState>) -> Result<bool, String> {
    let guard = state.backend.lock().map_err(|e| e.to_string())?;
    Ok(guard.as_ref().map(|h| h.is_alive()).unwrap_or(false))
}

/// 启动 Python FastAPI sidecar（监听 127.0.0.1:8000）
#[tauri::command]
async fn start_backend(
    app: tauri::AppHandle,
    state: tauri::State<'_, AppState>,
) -> Result<String, String> {
    let mut guard = state.backend.lock().map_err(|e| e.to_string())?;
    if guard.as_ref().map(|h| h.is_alive()).unwrap_or(false) {
        return Ok("already running".into());
    }
    let handle = SidecarManager::start_python_backend(&app)?;
    *guard = Some(handle);
    Ok("started".into())
}

/// 停止后端 sidecar（递归杀进程树，避免 PyInstaller onefile 子进程成孤儿）
#[tauri::command]
async fn stop_backend(state: tauri::State<'_, AppState>) -> Result<(), String> {
    let mut guard = state.backend.lock().map_err(|e| e.to_string())?;
    if let Some(h) = guard.take() {
        h.kill_tree().map_err(|e| e.to_string())?;
    }
    Ok(())
}

/// 启动指定账号的凭证守护 sidecar（browser_daemon）
#[tauri::command]
async fn start_browser_daemon(
    app: tauri::AppHandle,
    state: tauri::State<'_, AppState>,
    account: String,
    port: u16,
) -> Result<String, String> {
    let handle = SidecarManager::start_browser_daemon(&app, &account, port)?;
    state
        .daemons
        .lock()
        .map_err(|e| e.to_string())?
        .push(handle);
    Ok(format!("browser_daemon for {} on {}", account, port))
}

/// 启动指定账号的私信接收守护 sidecar（recv_daemon）
#[tauri::command]
async fn start_recv_daemon(
    app: tauri::AppHandle,
    state: tauri::State<'_, AppState>,
    accounts: Vec<String>,
    port: u16,
) -> Result<String, String> {
    let handle = SidecarManager::start_recv_daemon(&app, &accounts, port)?;
    state
        .daemons
        .lock()
        .map_err(|e| e.to_string())?
        .push(handle);
    Ok(format!(
        "recv_daemon for [{}] on {}",
        accounts.join(","),
        port
    ))
}

/// 停止指定账号的凭证守护 sidecar（browser_daemon）
#[tauri::command]
async fn stop_browser_daemon(
    state: tauri::State<'_, AppState>,
    account: String,
    port: u16,
) -> Result<(), String> {
    let label = format!("browser_daemon({account}:{port})");
    let mut guard = state.daemons.lock().map_err(|e| e.to_string())?;
    if let Some(pos) = guard.iter().position(|h| h.label() == label) {
        let h = guard.remove(pos);
        h.kill()?;
        Ok(())
    } else {
        Err(format!("凭证守护 {label} 未运行"))
    }
}

/// 停止指定账号的私信接收守护 sidecar（recv_daemon）
#[tauri::command]
async fn stop_recv_daemon(
    state: tauri::State<'_, AppState>,
    accounts: Vec<String>,
    port: u16,
) -> Result<(), String> {
    let label = format!("recv_daemon({}:{port})", accounts.join(","));
    let mut guard = state.daemons.lock().map_err(|e| e.to_string())?;
    if let Some(pos) = guard.iter().position(|h| h.label() == label) {
        let h = guard.remove(pos);
        h.kill()?;
        Ok(())
    } else {
        Err(format!("私信守护 {label} 未运行"))
    }
}

/// 列出所有正在运行的守护 sidecar
#[tauri::command]
fn list_daemons(state: tauri::State<'_, AppState>) -> Vec<String> {
    state
        .daemons
        .lock()
        .map(|g| {
            g.iter()
                .filter(|h| h.is_alive())
                .map(|h| h.label().to_string())
                .collect()
        })
        .unwrap_or_default()
}

pub fn run() {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_fs::init())
        .plugin(tauri_plugin_notification::init())
        .manage(AppState {
            backend: Mutex::new(None),
            daemons: Mutex::new(Vec::new()),
        })
        .setup(|_app| {
            log::info!("DYAutoDM v2 启动");
            // 开发模式下后端由 uvicorn 单独跑；生产模式自动拉起 sidecar
            #[cfg(not(debug_assertions))]
            {
                use tauri::Manager;
                let app_handle = _app.handle().clone();
                tauri::async_runtime::spawn(async move {
                    let state = app_handle.state::<AppState>();
                    if let Err(e) = start_backend(app_handle.clone(), state).await {
                        log::error!("后端 sidecar 启动失败: {e}");
                    }
                });
            }
            Ok(())
        })
        // 前端窗口关闭时联动关闭所有 sidecar：先关前端后端的子进程树，再关守护，
        // 避免孤儿进程（PyInstaller onefile 解压出的 _MEI 子进程）继续占用端口。
        // 顺序：① 后端 sidecar（kill_tree 递归终止）→ ② 各账号守护（kill_tree）。
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                log::info!("窗口关闭请求：先关闭后端进程树，再关闭守护");
                let app = window.app_handle();
                // ① 优先关闭后端（递归杀进程树，连带 _MEI 子进程一并终止）
                if let Ok(mut state) = app.state::<AppState>().backend.lock() {
                    if let Some(h) = state.take() {
                        let _ = h.kill_tree();
                    }
                }
                // ② 再关闭各账号守护进程树
                if let Ok(mut guard) = app.state::<AppState>().daemons.lock() {
                    let handles: Vec<SidecarHandle> = guard.drain(..).collect();
                    for h in handles {
                        let _ = h.kill_tree();
                    }
                }
                log::info!("后端与守护进程树已全部关闭");
            }
        })
        .invoke_handler(tauri::generate_handler![
            backend_status,
            start_backend,
            stop_backend,
            start_browser_daemon,
            stop_browser_daemon,
            start_recv_daemon,
            stop_recv_daemon,
            list_daemons,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
