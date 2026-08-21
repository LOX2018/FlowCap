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

/** 启动指定账号的浏览器守护进程（BCC，浏览器容器）
 *
 * 复用 dyautodm-browser-daemon exe 名，启动参数 `--account X --port P`，
 * 与 Rust 侧 start_browser_daemon 对齐。仅 spawn sidecar 进程，不等待 BCC
 * 浏览器 context 就绪——如需就绪后再放行调用方，用 startBrowserDaemonReady。
 */
export async function startBrowserDaemon(account: string, port: number): Promise<string> {
  return invoke<string>("start_browser_daemon", { account, port });
}

/** 停止指定账号的浏览器守护进程（BCC）
 *
 * 优先通过后端 HTTP 向守护自身的 /quit 端口发停止请求（守护会自行 os._exit，
 * 不依赖 Rust SidecarManager 的进程 label 精确匹配，避免“停止失败”）。
 * 后端不可达时再退回 Rust kill 兜底。
 */
export async function stopBrowserDaemon(account: string, port: number): Promise<void> {
  try {
    const res = await fetch(`${BACKEND_BASE}/api/accounts/${encodeURIComponent(account)}/stop-browser`, {
      method: "POST",
    });
    if (res.ok) return;
  } catch {
    // 后端不可达，退回 Rust kill
  }
  try {
    return await invoke<void>("stop_browser_daemon", { account, port });
  } catch {
    // ignore
  }
}

// ===== BCC（浏览器容器）就绪探测 =====

/** BCC /status 返回体（前端关心的字段，与 backend/daemon/browser_daemon.py 对齐） */
export interface BccStatus {
  alive: boolean;
  account: string;
  profile?: string;
  uid?: number | string | null;
  last_refresh?: number;
  logged_in?: boolean;
}

/** 查询某账号 BCC 的 /status（BCC 未就绪/端口未监听时返回 null） */
export async function browserDaemonStatus(port: number): Promise<BccStatus | null> {
  if (!port) return null;
  try {
    const res = await fetch(`http://127.0.0.1:${port}/status`, { method: "GET" });
    if (!res.ok) return null;
    return (await res.json()) as BccStatus;
  } catch {
    return null;
  }
}

/**
 * 轮询探测 BCC /status 直到 alive=true 或超时。
 *
 * BCC 启动后需数秒拉起常驻浏览器 context（指纹内核冷启动更慢），
 * 在 alive=false 期间 /cookie /user_info /resolve_url /scan_login 均返回
 * “容器未启动”，调用方（recv-daemon / bulk_user_info / link_resolve）
 * 必须等待就绪后再请求。
 *
 * @param port BCC HTTP 端口（= startBrowserDaemon 传入的 port）
 * @param timeoutMs 总超时（默认 60s，指纹内核冷启动留足时间）
 * @param intervalMs 轮询间隔（默认 500ms）
 */
export async function waitBrowserDaemonReady(
  port: number,
  timeoutMs = 60000,
  intervalMs = 500,
): Promise<BccStatus> {
  const deadline = Date.now() + timeoutMs;
  let last: BccStatus | null = null;
  while (Date.now() < deadline) {
    last = await browserDaemonStatus(port);
    if (last && last.alive) return last;
    await new Promise((r) => setTimeout(r, intervalMs));
  }
  const acct = last && last.account ? last.account : "";
  throw new Error(`BCC 在 ${timeoutMs}ms 内未就绪（port=${port}, account=${acct || "未知"}）`);
}

/**
 * Tauri 模式下：启动某账号 BCC 并等待 /status 就绪（alive=true）。
 * 浏览器模式直接抛错（无 sidecar）。
 *
 * @param account 账号名
 * @param port BCC HTTP 端口
 * @param timeoutMs 就绪探测超时（默认 60s）
 */
export async function startBrowserDaemonReady(
  account: string,
  port: number,
  timeoutMs = 60000,
): Promise<BccStatus> {
  await startBrowserDaemon(account, port); // 拉起 sidecar（幂等：已运行则直接返回）
  return waitBrowserDaemonReady(port, timeoutMs);
}

/** 启动私信接收守护进程（支持多账号） */
export async function startRecvDaemon(accounts: string[], port: number): Promise<string> {
  return invoke<string>("start_recv_daemon", { accounts, port });
}

/** 停止私信接收守护进程
 *
 * 优先通过后端 HTTP 向守护自身的 /quit 端口发停止请求（守护会自行 os._exit，
 * 不依赖 Rust SidecarManager 的进程 label 精确匹配，避免“停止失败”）。
 * 后端不可达时再退回 Rust kill 兜底。
 */
export async function stopRecvDaemon(accounts: string[], port: number): Promise<void> {
  const account = accounts && accounts.length ? accounts[0] : "";
  try {
    const res = await fetch(`${BACKEND_BASE}/api/accounts/${encodeURIComponent(account)}/stop-recv`, {
      method: "POST",
    });
    if (res.ok) return;
  } catch {
    // 后端不可达，退回 Rust kill
  }
  try {
    return await invoke<void>("stop_recv_daemon", { accounts, port });
  } catch {
    // ignore
  }
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
