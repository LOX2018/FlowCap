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

/// 把前端启动诊断日志写入文件（排查用；写入 exe 同目录 logs/frontend_boot.log）
///
/// 🔴 2026-09-28 修（DSSCC-UI-007，单文件轮转）：原实现只 append、**从不轮转** ——
/// 叠加 App.tsx 诊断 effect 漏依赖数组（每次渲染都写），实测累积 281,595 行 /
/// 31 MB，既吃磁盘又淹没真实日志。现补**单文件轮转**（与后端 loguru
/// `rotation="20 MB"` 同一惯例）：写入前若当前文件已达 `BOOT_LOG_MAX_BYTES`，
/// 先轮转为 `frontend_boot.log.1`（覆盖旧备份），再新建写入。上限 = 2×阈值。
/// 本函数是**唯一写入口**，所有调用点自动受益；轮转失败为 best-effort（不阻断写入）。
#[tauri::command]
fn write_boot_log(text: String) -> Result<(), String> {
    const BOOT_LOG_MAX_BYTES: u64 = 10 * 1024 * 1024; // 10 MB
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let dir = exe
        .parent()
        .ok_or_else(|| "无法获取 exe 目录".to_string())?;
    let logs = dir.join("logs");
    std::fs::create_dir_all(&logs).map_err(|e| e.to_string())?;
    let path = logs.join("frontend_boot.log");

    // 单文件轮转：超上限则挪为 .1（覆盖旧备份），使日志占用有界
    if let Ok(meta) = std::fs::metadata(&path) {
        if meta.len() >= BOOT_LOG_MAX_BYTES {
            let backup = logs.join("frontend_boot.log.1");
            let _ = std::fs::remove_file(&backup);
            let _ = std::fs::rename(&path, &backup);
        }
    }

    use std::io::Write;
    let mut f = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .open(&path)
        .map_err(|e| e.to_string())?;
    writeln!(f, "{}", text).map_err(|e| e.to_string())?;
    Ok(())
}

pub fn run() {
    env_logger::Builder::from_env(env_logger::Env::default().default_filter_or("info")).init();

    tauri::Builder::default()
        // ── 单实例限制（2026-10-02 用户要求）──
        // ⚠️ **必须第一个注册**：本插件靠「OS 级互斥体 + 把 argv 转发给首实例」
        // 工作，若排在其它插件之后，晚于它初始化的插件可能已在首实例里建好资源
        // （窗口 / sidecar 端口），重复启动就会撞端口、弹第二个窗口。
        //
        // 第二个实例的行为：**不静默退出**，而是把参数转发给首实例并聚焦其窗口
        // （用户双击第二次时想的是「把我刚才那窗口叫出来」，不是「啥也没发生」）。
        // 闭包返回 `true` = 本进程让位退出。
        .plugin(tauri_plugin_single_instance::init(|app, _argv, _cwd| {
            log::info!("检测到重复启动：已存在的实例将被激活，本实例退出");
            if let Some(win) = app.get_webview_window("main") {
                // unminimize + show + set_focus 缺一不可：
                // 最小化状态下只 show 不会真正置前。
                let _ = win.unminimize();
                let _ = win.show();
                let _ = win.set_focus();
            }
        }))
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
            // 🔴 2026-10-02：**仅 debug 构建**自动开启浏览器 DevTools（F12）。
            // 用户要求「测试版有 Dev 日志窗口，正式版不变」（A 方案）。
            // `open_devtools()` 在 debug 构建内可用（release 无此 API ⇒ 编译期分流），
            // 故 `#[cfg(debug_assertions)]` 同时满足「仅测试版生效」与「正式版零影响」。
            #[cfg(debug_assertions)]
            {
                use tauri::Manager;
                if let Some(win) = _app.get_webview_window("main") {
                    win.open_devtools();
                    log::info!("DevTools 已开启（debug 构建）");
                } else {
                    log::warn!("未找到 main 窗口，DevTools 未开启");
                }
            }
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
        // 前端窗口关闭时联动关闭所有 sidecar。
        // 顺序（2026-10-03 起，关窗「优雅退出」定调）：
        //   ① 优雅级联 —— 调 backend 免鉴权端点 /api/shutdown/daemons，
        //      backend 级联向所有存活 BCC + recv 发 /quit：BCC 关闭 Chromium
        //      context、清扫 SingletonLock，~0.5s 后自退（优雅，而非强杀残留）；
        //   ② 标记守护为显式停止 —— 防 BCC 自退后 supervisor 误判「崩溃」
        //      走退避重启；
        //   ③ 级联成功则等 1.5s，给 BCC 完成优雅自退（/quit 契约 0.5s）；
        //   ④⑤ taskkill /F /T 兜底 —— 未收信号/未自退的守护与 backend
        //      进程树递归终止（连带 _MEI 子进程，避免孤儿占端口）。
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { .. } = event {
                log::info!("窗口关闭请求：优雅级联 → 等自退 → taskkill 兜底");
                let app = window.app_handle();
                // ① 优雅级联（backend 活着才能调 —— 级联端点在 backend 上）
                let backend_alive = app
                    .state::<AppState>()
                    .backend
                    .lock()
                    .map(|g| g.as_ref().map(|h| h.is_alive()).unwrap_or(false))
                    .unwrap_or(false);
                let cascade_ok = if backend_alive {
                    sidecar::request_daemons_graceful_shutdown(8000)
                } else {
                    false
                };
                log::info!(
                    "关窗级联: 优雅退出请求已发 (backend_alive={backend_alive}, ok={cascade_ok})；taskkill 兜底未来自退者"
                );
                // ② 先置「显式停止」标记（必须在等自退**之前**）：
                //    BCC 收 /quit 后 ~0.5s 自退，supervisor 看到 Terminated
                //    若 stopping=false 会走「1s 退避自动重启」——把优雅退出当崩溃。
                if let Ok(mut guard) = app.state::<AppState>().daemons.lock() {
                    for h in guard.iter() {
                        h.mark_stopping();
                    }
                }
                // ③ 仅级联成功时等待自退（1.5s 覆盖 BCC 的 0.5s 自退契约）。
                //    主线程短暂阻塞可接受：即将关窗，换来的是干净自退而非强杀。
                if cascade_ok {
                    std::thread::sleep(std::time::Duration::from_millis(1500));
                }
                // ④ 兜底：杀后端进程树（递归终止，连带 _MEI 子进程）
                if let Ok(mut state) = app.state::<AppState>().backend.lock() {
                    if let Some(h) = state.take() {
                        let _ = h.kill_tree();
                    }
                }
                // ⑤ 兜底：杀各账号守护进程树（清扫未自退的 BCC/recv）
                if let Ok(mut guard) = app.state::<AppState>().daemons.lock() {
                    let handles: Vec<SidecarHandle> = guard.drain(..).collect();
                    for h in handles {
                        let _ = h.kill_tree();
                    }
                }
                log::info!("后端与守护进程树已全部关闭（优雅级联 + taskkill 兜底）");
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
            write_boot_log,
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}
