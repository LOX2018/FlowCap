//! Sidecar 进程管理
//!
//! 用 Tauri 的 shell 插件拉起外部二进制：
//! - `dyautodm-backend`（PyInstaller 打包的 FastAPI 后端）
//! - `dyautodm-browser-daemon`（凭证守护）
//! - `dyautodm-recv-daemon`（私信接收守护）
//!
//! 这些二进制由 scripts/build_sidecar.py 打包生成，放在 src-tauri/binaries/ 下。
//! 优先用 Tauri 的 externalBin（`sidecar()`），若未嵌入（发布态直接复制 exe
//! 运行时常见），回退到 exe 同目录的 `binaries/<name>-<triple>.exe` 绝对路径启动，
//! 保证"双击 exe 即用"无需安装。

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use tauri::AppHandle;
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::process::CommandEvent;
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

/// 解析 sidecar 可执行文件路径：
/// 优先用 exe 同目录的 `binaries/<name>-<triple>.exe`（发布态直接复制 exe 场景，最可靠）；
/// 若不存在则回退到 Tauri externalBin（`sidecar()`，处理开发态 src-tauri/binaries 回退）。
fn resolve_sidecar(app: &AppHandle, name: &str) -> Result<PathBuf, String> {
    // 1) 优先：exe 同目录/binaries/<name>-<triple>.exe（确定存在则直接用绝对路径）
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let dir = exe.parent().ok_or_else(|| "无法获取 exe 所在目录".to_string())?;
    let fname = format!("{name}-{}.exe", target_triple());
    let cand = dir.join("binaries").join(&fname);
    if cand.exists() {
        return Ok(cand);
    }
    // 2) 回退：Tauri externalBin（开发态 src-tauri/binaries 回退）
    if app.shell().sidecar(name).is_ok() {
        return Ok(PathBuf::from(name));
    }
    Err(format!(
        "找不到 sidecar {name}：{} 不存在，且 externalBin 不可用",
        cand.display()
    ))
}

/// 单个 sidecar 的句柄（可查询存活、可 kill）
pub struct SidecarHandle {
    label: String,
    child: Option<CommandChild>,
    alive: Arc<AtomicBool>,
}

impl SidecarHandle {
    pub fn label(&self) -> &str {
        &self.label
    }

    pub fn is_alive(&self) -> bool {
        self.alive.load(Ordering::SeqCst)
    }

    /// 递归终止整个进程树（Windows 下 PyInstaller onefile 的 sidecar 会解压出
    /// `_MEIxxxx` 临时子进程，普通 kill() 只杀外层解压器，真正的 Python 子进程
    /// 会成为孤儿继续占用端口/资源。用 taskkill /F /T 递归杀掉整棵树。）
    pub fn kill_tree(&mut self) -> Result<(), String> {
        #[cfg(windows)]
        {
            if let Some(child) = &self.child {
                let pid = child.pid();
                if pid > 0 {
                    let out = std::process::Command::new("taskkill")
                        .args(["/F", "/T", "/PID", &pid.to_string()])
                        .output();
                    match out {
                        Ok(_) => {
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
            if let Some(mut child) = self.child.take() {
                child.kill().map_err(|e| e.to_string())?;
            }
            self.alive.store(false, Ordering::SeqCst);
            Ok(())
        }
        #[cfg(not(windows))]
        {
            if let Some(mut child) = self.child.take() {
                child.kill().map_err(|e| e.to_string())?;
            }
            self.alive.store(false, Ordering::SeqCst);
            Ok(())
        }
    }

    pub fn kill(mut self) -> Result<(), String> {
        if let Some(child) = self.child.take() {
            child.kill().map_err(|e| e.to_string())?;
        }
        self.alive.store(false, Ordering::SeqCst);
        Ok(())
    }
}

/// Sidecar 启动器
pub struct SidecarManager;

impl SidecarManager {
    /// 启动 Python FastAPI 后端
    pub fn start_python_backend(app: &AppHandle) -> Result<SidecarHandle, String> {
        let path = resolve_sidecar(app, "dyautodm-backend")?;
        let use_external = path.to_string_lossy() == "dyautodm-backend";
        let builder = if use_external {
            app.shell().sidecar("dyautodm-backend")
        } else {
            Ok(app.shell().command(path.to_string_lossy().to_string()))
        };
        let (mut rx, child) = builder
            .map_err(|e| format!("找不到 dyautodm-backend sidecar: {e}"))?
            .args(["--port", "8000"])
            .spawn()
            .map_err(|e| format!("启动后端失败: {e}"))?;

        let alive = Arc::new(AtomicBool::new(true));
        let alive_clone = alive.clone();
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
            child: Some(child),
            alive,
        })
    }

    /// 启动某账号的 browser_daemon（凭证守护）
    pub fn start_browser_daemon(
        app: &AppHandle,
        account: &str,
        port: u16,
    ) -> Result<SidecarHandle, String> {
        let path = resolve_sidecar(app, "dyautodm-browser-daemon")?;
        let use_external = path.to_string_lossy() == "dyautodm-browser-daemon";
        let builder = if use_external {
            app.shell().sidecar("dyautodm-browser-daemon")
        } else {
            Ok(app.shell().command(path.to_string_lossy().to_string()))
        };
        let (mut rx, child) = builder
            .map_err(|e| format!("找不到 dyautodm-browser-daemon: {e}"))?
            .args(["--account", account, "--port", &port.to_string()])
            .spawn()
            .map_err(|e| format!("启动 browser_daemon 失败: {e}"))?;

        let alive = Arc::new(AtomicBool::new(true));
        let alive_clone = alive.clone();
        let label = format!("browser_daemon({account}:{port})");

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
            child: Some(child),
            alive,
        })
    }

    /// 启动某账号（可多账号）的 recv_daemon（私信接收守护）
    pub fn start_recv_daemon(
        app: &AppHandle,
        accounts: &[String],
        port: u16,
    ) -> Result<SidecarHandle, String> {
        let accounts_arg = accounts.join(",");
        let path = resolve_sidecar(app, "dyautodm-recv-daemon")?;
        let use_external = path.to_string_lossy() == "dyautodm-recv-daemon";
        let builder = if use_external {
            app.shell().sidecar("dyautodm-recv-daemon")
        } else {
            Ok(app.shell().command(path.to_string_lossy().to_string()))
        };
        let (mut rx, child) = builder
            .map_err(|e| format!("找不到 dyautodm-recv-daemon: {e}"))?
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
            child: Some(child),
            alive,
        })
    }
}
