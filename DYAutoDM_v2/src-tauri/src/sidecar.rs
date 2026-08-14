//! Sidecar 进程管理
//!
//! 用 Tauri 的 shell 插件拉起外部二进制：
//! - `dyautodm-backend`（PyInstaller 打包的 FastAPI 后端）
//! - `dyautodm-browser-daemon`（凭证守护）
//! - `dyautodm-recv-daemon`（私信接收守护）
//!
//! 这些二进制由 scripts/build_sidecar.py 打包生成，放在 src-tauri/binaries/ 下。

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use tauri::AppHandle;
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;

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
        let (mut rx, child) = app
            .shell()
            .sidecar("dyautodm-backend")
            .map_err(|e| format!("找不到 dyautodm-backend sidecar: {e}"))?
            .args(["--port", "8000"])
            .spawn()
            .map_err(|e| format!("启动后端失败: {e}"))?;

        let alive = Arc::new(AtomicBool::new(true));
        let alive_clone = alive.clone();
        let label = "backend".to_string();

        // 监听子进程输出
        tauri::async_runtime::spawn(async move {
            use tauri_plugin_shell::process::CommandEvent;
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
        let (mut rx, child) = app
            .shell()
            .sidecar("dyautodm-browser-daemon")
            .map_err(|e| format!("找不到 dyautodm-browser-daemon: {e}"))?
            .args(["--account", account, "--port", &port.to_string()])
            .spawn()
            .map_err(|e| format!("启动 browser_daemon 失败: {e}"))?;

        let alive = Arc::new(AtomicBool::new(true));
        let alive_clone = alive.clone();
        let label = format!("browser_daemon({account}:{port})");

        tauri::async_runtime::spawn(async move {
            use tauri_plugin_shell::process::CommandEvent;
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
        let (mut rx, child) = app
            .shell()
            .sidecar("dyautodm-recv-daemon")
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
            use tauri_plugin_shell::process::CommandEvent;
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
