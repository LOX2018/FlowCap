//! Sidecar 进程管理
//!
//! 用 Tauri 的 shell 插件拉起外部二进制：
//! - `flowcap-backend`（PyInstaller 打包的 FastAPI 后端）
//! - `flowcap-browser-daemon`（BCC，浏览器容器：常驻唯一浏览器 context）
//! - `flowcap-recv-daemon`（私信接收守护）
//!
//! 这些二进制由 scripts/build_sidecar.py 打包生成，放在 src-tauri/binaries/ 下。
//! 优先用 Tauri 的 externalBin（`sidecar()`），若未嵌入（发布态直接复制 exe
//! 运行时常见），回退到 exe 同目录的 `binaries/<name>-<triple>.exe` 绝对路径启动，
//! 保证"双击 exe 即用"无需安装。
//!
//! BCC 生命周期（任务 9）：start_browser_daemon 在 spawn 后挂一个 supervisor 任务，
//! 进程意外退出（崩溃）时按指数退避自动重启（默认上限 5 次）；显式 stop
//! （kill_tree 置 stopping=true）不触发重启。spawn 前做端口探测，避免与残留
//! BCC / 其他进程抢端口导致 "address already in use"。

use std::net::TcpStream;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use tauri::AppHandle;
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

/// 当前平台 target triple（与 build_sidecar.py / Tauri externalBin 命名一致）。
fn target_triple() -> &'static str {
    if cfg!(target_os = "windows") {
        "x86_64-pc-windows-msvc"
    } else if cfg!(target_os = "macos") {
        if cfg!(target_arch = "aarch64") {
            "aarch64-apple-darwin"
        } else {
            "x86_64-apple-darwin"
        }
    } else {
        "x86_64-unknown-linux-gnu"
    }
}

/// 解析 sidecar 可执行文件路径（onedir 免解压优先，onefile 回退）：
/// 1) exe 同目录/binaries/<name>-<triple>/<name>-<triple>.exe（onedir 目录模式，免解压秒启）
/// 2) exe 同目录/binaries/<name>-<triple>.exe（onefile 单文件，旧模式）
/// 3) Tauri externalBin（`sidecar()`，开发态 src-tauri/binaries 回退）
fn resolve_sidecar(app: &AppHandle, name: &str) -> Result<PathBuf, String> {
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let dir = exe.parent().ok_or_else(|| "无法获取 exe 所在目录".to_string())?;
    // PyInstaller onedir 部署目录名 = <name>-<triple>（无 .exe），目录内 exe 带 .exe
    let stem = format!("{name}-{}", target_triple());
    let fname = format!("{stem}.exe");
    // ⚠️ 部署位置铁律（2026-09-13 用户要求）：**sidecar 直接放应用根目录**，
    //    不再放 <root>/binaries/ 子目录（曾因两处部署导致版本混乱）。
    //    搜索顺序：根目录优先，再兼容历史 binaries/ 子目录（旧部署不破坏）。
    //    ⚠️ onedir 目录段必须是【不带 .exe 的 stem】，
    //    旧写法两段都 join(fname) 会拼出 "...exe\...exe" 永不命中。
    let candidates = [
        // 1) 【新·标准】根目录 onedir：<root>/<full>/<full>.exe
        dir.join(&stem).join(&fname),
        // 2) 根目录 onefile：<root>/<full>.exe
        dir.join(&fname),
        // 3) 兼容旧部署：binaries/<full>/<full>.exe
        dir.join("binaries").join(&stem).join(&fname),
        // 4) 兼容旧部署：binaries/<full>.exe
        dir.join("binaries").join(&fname),
    ];
    for cand in candidates.iter() {
        if cand.exists() {
            return Ok(cand.clone());
        }
    }
    // 5) 回退：Tauri externalBin（开发态 src-tauri/binaries 回退）
    if app.shell().sidecar(name).is_ok() {
        return Ok(PathBuf::from(name));
    }
    Err(format!(
        "找不到 sidecar {name}：已尝试 根目录/<full>/、根目录/<full>.exe、\
         binaries/<full>/、binaries/<full>.exe 均不存在，且 externalBin 不可用"
    ))
}

/// 探测 127.0.0.1:port 是否已有进程监听（TCP connect，200ms 超时）。
/// spawn BCC 前用，避免与残留 BCC / 其他进程抢端口导致 "address already in use"。
fn port_in_use(port: u16) -> bool {
    let addr = match format!("127.0.0.1:{port}").parse() {
        Ok(a) => a,
        Err(_) => return false,
    };
    TcpStream::connect_timeout(&addr, Duration::from_millis(200)).is_ok()
}

/// 关窗优雅级联（2026-10-03）：向 backend 的免鉴权端点
/// `POST http://127.0.0.1:{backend_port}/api/shutdown/daemons` 发一次请求，
/// backend 级联向所有存活守护（BCC + recv）发 /quit 优雅退出。
///
/// 零新依赖：用 std::net::TcpStream 手写最小 HTTP/1.1 请求（不引 reqwest，
/// 避免给 Tauri 壳增加依赖面）。backend 不可达（未起/已退）时静默返回 false，
/// 调用方继续走 taskkill 兜底即可 —— 优雅级联是「尽力而为」，不阻断关窗。
pub fn request_daemons_graceful_shutdown(backend_port: u16) -> bool {
    use std::io::{Read, Write};
    let addr = format!("127.0.0.1:{backend_port}");
    let parsed = match addr.parse() {
        Ok(a) => a,
        Err(_) => return false,
    };
    let stream = match TcpStream::connect_timeout(&parsed, Duration::from_millis(1500)) {
        Ok(s) => s,
        Err(_) => return false, // backend 不可达：兜底路径接管
    };
    let _ = stream.set_read_timeout(Some(Duration::from_millis(4000)));
    // 单行拼接（避免 CRLF 换行把缩进带进 HTTP 头）。
    let req = format!("POST /api/shutdown/daemons HTTP/1.1\r\nHost: {addr}\r\nContent-Length: 0\r\nConnection: close\r\n\r\n");
    let mut stream = stream;
    if stream.write_all(req.as_bytes()).is_err() {
        return false;
    }
    let mut buf = [0u8; 4096];
    loop {
        match stream.read(&mut buf) {
            Ok(0) => break,
            Ok(_) => continue,
            Err(_) => break,
        }
    }
    true
}

/// 单个 sidecar 的句柄。字段全部 interior-mutable（Arc + Mutex/Atomic），
/// 便于 BCC supervisor 在崩溃重启时替换 child 而无需可变借用——句柄存入
/// AppState.daemons Vec 后仍可被 supervisor 跨任务更新。
pub struct SidecarHandle {
    label: String,
    child: Arc<Mutex<Option<CommandChild>>>,
    alive: Arc<AtomicBool>,
    /// true = 显式停止（kill_tree 触发），supervisor 检测到后不重启。
    stopping: Arc<AtomicBool>,
    /// 累计自动重启次数（仅 BCC supervisor 递增），用于退避上限判定。
    restart_count: Arc<AtomicU32>,
}

impl SidecarHandle {
    pub fn label(&self) -> &str {
        &self.label
    }

    pub fn is_alive(&self) -> bool {
        self.alive.load(Ordering::SeqCst)
    }

    /// 置「显式停止」标记但不杀进程。
    ///
    /// 关窗优雅级联专用（2026-10-03）：BCC 收到 backend 级联的 /quit 后
    /// 会在 ~0.5s 内自退；此时 supervisor 若先于兜底 kill_tree 看到
    /// Terminated，会走「1s 退避后自动重启」路径 —— 把优雅退出当崩溃。
    /// 先置 stopping=true，supervisor 的退避检查（supervise_bcc 第 4/5 步）
    /// 与延迟期复查都会判「显式停止」而不重启。
    pub fn mark_stopping(&self) {
        self.stopping.store(true, Ordering::SeqCst);
    }

    /// 递归终止整个进程树（Windows 下 PyInstaller onefile 的 sidecar 会解压出
    /// `_MEIxxxx` 临时子进程，普通 kill() 只杀外层解压器，真正的 Python 子进程
    /// 会成为孤儿继续占用端口/资源。用 taskkill /F /T 递归杀掉整棵树。）
    ///
    /// 置 stopping=true：BCC supervisor 收到 Terminated 事件后会据此跳过自动重启。
    pub fn kill_tree(&self) -> Result<(), String> {
        self.stopping.store(true, Ordering::SeqCst);
        #[cfg(windows)]
        {
            let pid_opt = self
                .child
                .lock()
                .ok()
                .and_then(|g| g.as_ref().map(|c| c.pid()));
            if let Some(pid) = pid_opt {
                if pid > 0 {
                    let out = std::process::Command::new("taskkill")
                        .args(["/F", "/T", "/PID", &pid.to_string()])
                        .output();
                    match out {
                        Ok(_) => {
                            if let Ok(mut g) = self.child.lock() {
                                *g = None;
                            }
                            self.alive.store(false, Ordering::SeqCst);
                            return Ok(());
                        }
                        Err(e) => {
                            // taskkill 不可用（极罕见），回退普通 kill
                            log::warn!("taskkill 失败，回退普通 kill: {e}");
                        }
                    }
                }
            }
            // 回退：普通 kill（仍可能留下孤儿）
            if let Ok(mut g) = self.child.lock() {
                if let Some(c) = g.take() {
                    let _ = c.kill();
                }
            }
            self.alive.store(false, Ordering::SeqCst);
            Ok(())
        }
        #[cfg(not(windows))]
        {
            if let Ok(mut g) = self.child.lock() {
                if let Some(c) = g.take() {
                    let _ = c.kill();
                }
            }
            self.alive.store(false, Ordering::SeqCst);
            Ok(())
        }
    }

    pub fn kill(self) -> Result<(), String> {
        self.kill_tree()
    }
}

/// Sidecar 启动器
pub struct SidecarManager;

impl SidecarManager {
    /// 启动 Python FastAPI 后端
    pub fn start_python_backend(app: &AppHandle) -> Result<SidecarHandle, String> {
        let path = resolve_sidecar(app, "flowcap-backend")?;
        let use_external = path.to_string_lossy() == "flowcap-backend";
        let builder = if use_external {
            app.shell().sidecar("flowcap-backend")
        } else {
            Ok(app.shell().command(path.to_string_lossy().to_string()))
        };
        let (mut rx, child) = builder
            .map_err(|e| format!("找不到 flowcap-backend sidecar: {e}"))?
            .args(["--port", "8000"])
            .spawn()
            .map_err(|e| format!("启动后端失败: {e}"))?;

        let alive = Arc::new(AtomicBool::new(true));
        let alive_clone = alive.clone();
        let child = Arc::new(Mutex::new(Some(child)));
        let label = "backend".to_string();

        tauri::async_runtime::spawn(async move {
            while let Some(event) = rx.recv().await {
                match event {
                    CommandEvent::Terminated(_) => {
                        alive_clone.store(false, Ordering::SeqCst);
                        log::warn!("后端 sidecar 已退出");
                        break;
                    }
                    CommandEvent::Stdout(bytes) => {
                        log::info!("[backend] {}", String::from_utf8_lossy(&bytes).trim());
                    }
                    CommandEvent::Stderr(bytes) => {
                        log::warn!("[backend] {}", String::from_utf8_lossy(&bytes).trim());
                    }
                    _ => {}
                }
            }
        });

        Ok(SidecarHandle {
            label,
            child,
            alive,
            stopping: Arc::new(AtomicBool::new(false)),
            restart_count: Arc::new(AtomicU32::new(0)),
        })
    }

    /// 启动某账号的 BCC（浏览器容器守护）
    ///
    /// 流程：① 端口探测（占用即拒）→ ② spawn 子进程 → ③ 挂 supervisor 任务
    /// 监听退出事件，意外终止时按指数退避自动重启（上限 MAX_RESTARTS）。
    pub fn start_browser_daemon(
        app: &AppHandle,
        account: &str,
        port: u16,
    ) -> Result<SidecarHandle, String> {
        // ① 端口探测：spawn 前若端口已监听，拒绝启动。
        //    避免双开 / 残留 BCC 抢端口导致 "address already in use"。
        if port_in_use(port) {
            return Err(format!(
                "端口 {port} 已被占用（可能存在残留 BCC 或其他进程）。请先停止该账号的凭证守护；若守护已停止仍占用，请检查并结束占用 {port} 端口的进程后再启动"
            ));
        }
        // ② spawn
        let (child, rx) = spawn_bcc_child(app, account, port)?;
        let label = format!("browser_daemon({account}:{port})");
        let alive = Arc::new(AtomicBool::new(true));
        let stopping = Arc::new(AtomicBool::new(false));
        let restart_count = Arc::new(AtomicU32::new(0));
        let child = Arc::new(Mutex::new(Some(child)));

        // ③ supervisor：意外退出时自动重启，显式停止不重启
        tauri::async_runtime::spawn(supervise_bcc(
            app.clone(),
            account.to_string(),
            port,
            label.clone(),
            child.clone(),
            alive.clone(),
            stopping.clone(),
            restart_count.clone(),
            rx,
        ));

        Ok(SidecarHandle {
            label,
            child,
            alive,
            stopping,
            restart_count,
        })
    }

    /// 启动某账号（可多账号）的 recv_daemon（私信接收守护）
    pub fn start_recv_daemon(
        app: &AppHandle,
        accounts: &[String],
        port: u16,
    ) -> Result<SidecarHandle, String> {
        let accounts_arg = accounts.join(",");
        let path = resolve_sidecar(app, "flowcap-recv-daemon")?;
        let use_external = path.to_string_lossy() == "flowcap-recv-daemon";
        let builder = if use_external {
            app.shell().sidecar("flowcap-recv-daemon")
        } else {
            Ok(app.shell().command(path.to_string_lossy().to_string()))
        };
        let (mut rx, child) = builder
            .map_err(|e| format!("找不到 flowcap-recv-daemon: {e}"))?
            .args([
                "--accounts",
                &accounts_arg,
                "--port",
                &port.to_string(),
            ])
            .spawn()
            .map_err(|e| format!("启动 recv_daemon 失败: {e}"))?;

        let alive = Arc::new(AtomicBool::new(true));
        let alive_clone = alive.clone();
        let child = Arc::new(Mutex::new(Some(child)));
        let label = format!("recv_daemon({accounts_arg}:{port})");

        tauri::async_runtime::spawn(async move {
            while let Some(event) = rx.recv().await {
                if let CommandEvent::Terminated(_) = event {
                    alive_clone.store(false, Ordering::SeqCst);
                    break;
                }
            }
        });

        Ok(SidecarHandle {
            label,
            child,
            alive,
            stopping: Arc::new(AtomicBool::new(false)),
            restart_count: Arc::new(AtomicU32::new(0)),
        })
    }
}

/// spawn BCC 子进程（不做端口探测——崩溃重启时端口应已释放，探测反而会误拒）。
/// 供初次启动 + supervisor 重启共用，避免重复 resolve/builder 代码。
fn spawn_bcc_child(
    app: &AppHandle,
    account: &str,
    port: u16,
) -> Result<
    (
        CommandChild,
        tauri::async_runtime::Receiver<CommandEvent>,
    ),
    String,
> {
    let path = resolve_sidecar(app, "flowcap-browser-daemon")?;
    let use_external = path.to_string_lossy() == "flowcap-browser-daemon";
    let builder = if use_external {
        app.shell().sidecar("flowcap-browser-daemon")
    } else {
        Ok(app.shell().command(path.to_string_lossy().to_string()))
    };
    let (rx, child) = builder
        .map_err(|e| format!("找不到 flowcap-browser-daemon: {e}"))?
        .args(["--account", account, "--port", &port.to_string()])
        .spawn()
        .map_err(|e| format!("启动 BCC 失败: {e}"))?;
    Ok((child, rx))
}

/// BCC 自动重启上限（超过即放弃，避免崩溃循环时无限重启消耗资源）。
const BCC_MAX_RESTARTS: u32 = 5;

/// BCC supervisor：进程意外退出时按指数退避自动重启；显式停止（stopping=true）不重启。
///
/// 退避：1s → 2s → 4s → 8s → 10s（封顶），共 5 次重启机会。
async fn supervise_bcc(
    app: AppHandle,
    account: String,
    port: u16,
    label: String,
    child: Arc<Mutex<Option<CommandChild>>>,
    alive: Arc<AtomicBool>,
    stopping: Arc<AtomicBool>,
    restart_count: Arc<AtomicU32>,
    mut rx: tauri::async_runtime::Receiver<CommandEvent>,
) {
    loop {
        // 1) 监听当前进程事件直到退出
        while let Some(event) = rx.recv().await {
            match event {
                CommandEvent::Terminated(_) => {
                    alive.store(false, Ordering::SeqCst);
                    break;
                }
                CommandEvent::Stdout(bytes) => {
                    log::info!("[bcc:{account}] {}", String::from_utf8_lossy(&bytes).trim());
                }
                CommandEvent::Stderr(bytes) => {
                    log::warn!("[bcc:{account}] {}", String::from_utf8_lossy(&bytes).trim());
                }
                _ => {}
            }
        }
        // 2) 进程已退出，清理 dead child 句柄（taskkill 已杀进程，此处仅清引用）
        if let Ok(mut g) = child.lock() {
            *g = None;
        }
        // 3) 显式停止（kill_tree 置 stopping=true）→ 不重启，supervisor 退出
        if stopping.load(Ordering::SeqCst) {
            log::info!("[bcc] {label} 显式停止，supervisor 退出");
            return;
        }
        // 4) 重启预算 + 指数退避
        let n = restart_count.fetch_add(1, Ordering::SeqCst) + 1;
        if n > BCC_MAX_RESTARTS {
            log::error!(
                "[bcc] {label} 重启次数已达上限 {BCC_MAX_RESTARTS}，放弃自动重启（请手动启动）"
            );
            return;
        }
        let delay_secs = std::cmp::min(10u64, 1u64 << (n - 1)); // 1,2,4,8,10
        log::warn!(
            "[bcc] {label} 进程意外退出，{delay_secs}s 后第 {n}/{BCC_MAX_RESTARTS} 次自动重启"
        );
        tokio::time::sleep(Duration::from_secs(delay_secs)).await;
        // delay 期间用户可能点了停止——再确认一次，避免无谓重启
        if stopping.load(Ordering::SeqCst) {
            log::info!("[bcc] {label} 延迟期间收到停止信号，取消重启");
            return;
        }
        // 5) 重新 spawn（跳过端口探测——崩溃后端口应已释放；若仍占用 spawn 会失败并放弃）
        match spawn_bcc_child(&app, &account, port) {
            Ok((new_child, new_rx)) => {
                // spawn 期间用户也可能点了停止——立即杀新进程，不接入 child Arc
                if stopping.load(Ordering::SeqCst) {
                    log::info!("[bcc] {label} 重启期间收到停止信号，终止新进程");
                    kill_child_tree(new_child);
                    alive.store(false, Ordering::SeqCst);
                    return;
                }
                if let Ok(mut g) = child.lock() {
                    *g = Some(new_child);
                }
                alive.store(true, Ordering::SeqCst);
                log::info!("[bcc] {label} 重启成功");
                rx = new_rx;
                continue; // 回到 loop 顶，监听新进程
            }
            Err(e) => {
                log::error!("[bcc] {label} 重启失败: {e}");
                return;
            }
        }
    }
}

/// 杀掉一个 BCC 子进程树（供 supervisor 重启竞态时清理刚 spawn 的新进程）。
/// Windows 用 taskkill /F /T 递归终止（PyInstaller onefile 的 _MEI 子进程一并杀掉）；
/// 其他平台用 CommandChild::kill。child 由本函数消费。
fn kill_child_tree(child: CommandChild) {
    #[cfg(windows)]
    {
        let pid = child.pid();
        if pid > 0 {
            let out = std::process::Command::new("taskkill")
                .args(["/F", "/T", "/PID", &pid.to_string()])
                .output();
            if out.is_err() {
                log::warn!("[bcc] 重启竞态清理时 taskkill 失败，回退普通 kill");
                drop(child); // 仍尝试 drop（不保证杀进程）
            }
        }
    }
    #[cfg(not(windows))]
    {
        let _ = child.kill();
    }
}
