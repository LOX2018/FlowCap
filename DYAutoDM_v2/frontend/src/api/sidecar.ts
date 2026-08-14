/**
 * Tauri sidecar 桥接层
 *
 * 职责：
 * 1. 检测当前是否运行在 Tauri 桌面环境（而非纯浏览器）
 * 2. 通过 invoke 调用 Rust 侧命令，拉起 / 停止 Python sidecar 进程
 * 3. 启动后轮询 backend HTTP 健康端点，直到 sidecar 就绪再放行前端请求
 *
 * 在纯浏览器开发模式（vite dev server）下，所有 sidecar 操作降级为 no-op，
 * 由开发者手动运行 `py backend/main.py` 提供 API。
 */

// Tauri 2 的 invoke 从 core 子模块导入
// 动态导入避免在非 Tauri 环境（纯浏览器/测试）下因包缺失而崩溃
let _invoke: ((cmd: string, args?: Record<string, unknown>) => Promise<unknown>) | null = null;
let _tauriChecked = false;
let _isTauri = false;

async function ensureTauri(): Promise<boolean> {
  if (_tauriChecked) return _isTauri;
  _tauriChecked = true;
  // Tauri 2 注入全局 __TAURI_INTERNALS__
  if (typeof window !== "undefined" && (window as any).__TAURI_INTERNALS__) {
    try {
      const mod = await import("@tauri-apps/api/core");
      _invoke = mod.invoke;
      _isTauri = true;
    } catch {
      _isTauri = false;
    }
  }
  return _isTauri;
}

/** 是否处于 Tauri 桌面环境 */
export async function isTauri(): Promise<boolean> {
  return ensureTauri();
}

/** backend 固定监听端口（与 Rust 侧 start_backend 的 --port 8000 对齐） */
export const BACKEND_PORT = 8000;
export const BACKEND_BASE = `http://127.0.0.1:${BACKEND_PORT}`;

/** 调用 Rust 命令的统一封装（非 Tauri 环境直接抛错） */
async function invoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const ok = await ensureTauri();
  if (!ok || !_invoke) {
    throw new Error(`非 Tauri 环境，无法调用 ${cmd}（请手动启动后端）`);
  }
  return _invoke(cmd, args) as Promise<T>;
}

// ===== sidecar 生命周期 =====

/** 启动 backend sidecar（幂等：已运行则直接返回） */
export async function startBackend(): Promise<string> {
  return invoke<string>("start_backend");
}

/** 停止 backend sidecar */
export async function stopBackend(): Promise<void> {
  return invoke<void>("stop_backend");
}

/** 查询 backend sidecar 进程是否存活 */
export async function backendAlive(): Promise<boolean> {
  return invoke<boolean>("backend_status");
}

/** 启动指定账号的凭证守护进程 */
export async function startBrowserDaemon(account: string, port: number): Promise<string> {
  return invoke<string>("start_browser_daemon", { account, port });
}

/** 启动私信接收守护进程（支持多账号） */
export async function startRecvDaemon(accounts: string[], port: number): Promise<string> {
  return invoke<string>("start_recv_daemon", { accounts, port });
}

/** 列出所有存活守护进程标签 */
export async function listDaemons(): Promise<string[]> {
  try {
    return await invoke<string[]>("list_daemons");
  } catch {
    return [];
  }
}

// ===== 就绪探测 =====

/**
 * 轮询探测 backend HTTP /api/status 直到就绪或超时。
 *
 * Tauri 模式下 sidecar 进程拉起后仍需数秒完成 uvicorn 启动，
 * 前端首次请求前必须等待。纯浏览器模式直接 resolve（由开发者保证后端已起）。
 *
 * @param timeoutMs 总超时（默认 30s）
 * @param intervalMs 轮询间隔（默认 400ms）
 */
export async function waitBackendReady(
  timeoutMs = 30000,
  intervalMs = 400,
): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${BACKEND_BASE}/api/status`, { method: "GET" });
      if (res.ok) return;
    } catch {
      // 尚未就绪，继续轮询
    }
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  throw new Error(`backend 在 ${timeoutMs}ms 内未就绪`);
}

/**
 * Tauri 模式下确保 backend 已启动且就绪；浏览器模式直接返回。
 * 供 client.ts 首次请求前调用。
 */
let _readyPromise: Promise<void> | null = null;
export function ensureBackendReady(): Promise<void> {
  if (_readyPromise) return _readyPromise;
  _readyPromise = (async () => {
    const tauri = await isTauri();
    if (!tauri) return; // 浏览器模式：开发者自管后端
    try {
      await startBackend();
    } catch (e) {
      // 已在运行也会返回 started，此处忽略启动错误继续探测
      console.warn("[sidecar] startBackend 返回:", e);
    }
    await waitBackendReady();
  })().catch((e) => {
    _readyPromise = null; // 失败后允许重试
    throw e;
  });
  return _readyPromise;
}
