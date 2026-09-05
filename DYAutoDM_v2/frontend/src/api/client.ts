/**
 * API 客户端
 *
 * 通过 fetch 调用 FastAPI 后端（http://127.0.0.1:8000）。
 * 协议类型由 openapi-typescript 从 OpenAPI schema 自动生成：
 *   npm run gen:api
 *
 * 迁移自原 DY_Spider_base/web/framework.js 的 ApiBridge，
 * 调用方式从 `await ApiBridge.xxx()` 改为 `await api.xxx()`。
 *
 * Tauri 模式下，首次请求前会自动拉起 backend sidecar 并等待就绪；
 * 浏览器开发模式下由开发者手动运行 `py backend/main.py`。
 */
import { ensureBackendReady, BACKEND_BASE } from "./sidecar";

// 后端地址：Tauri 模式与浏览器模式都用 127.0.0.1:8000
const BASE = BACKEND_BASE;

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // 首次请求前确保 backend sidecar 已就绪（Tauri 模式自动拉起，浏览器模式 no-op）
  await ensureBackendReady();
  const res = await fetch(`${BASE}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`API ${path} 失败 (${res.status}): ${text}`);
  }
  return res.json() as Promise<T>;
}

// ===== 协议类型（待 openapi-typescript 生成替换）=====

export interface BackendStatus {
  ok: boolean;
  running: boolean;
  sent?: number;
  limit?: number;
}

export interface Overview {
  running: boolean;
  paused?: boolean;
  sent: number;
  limit: number;
  queue?: number;
  accounts: { name: string; role: string; loggedIn: boolean }[];
  daemons: { browser: boolean; recv: boolean };
  browserDaemon?: { alive: boolean; signReady?: boolean };
  recvDaemon?: { alive: boolean };
  engineState?: string;
  statusMsg?: string;
}

// ===== API 客户端 =====

export const api = {
  // ===== overview =====
  async getBackendStatus(): Promise<BackendStatus> {
    return request("/api/status");
  },

  async getOverview(): Promise<Overview> {
    return request("/api/overview");
  },

  async getStats(): Promise<{ sent: number; captured: number; queue: number }> {
    return request("/api/stats");
  },

  // ===== engine =====
  async start(config: Record<string, unknown>): Promise<{ ok: boolean; state?: string }> {
    return request("/api/engine/start", {
      method: "POST",
      body: JSON.stringify(config),
    });
  },

  async stop(): Promise<{ ok: boolean; state?: string }> {
    return request("/api/engine/stop", { method: "POST" });
  },

  async stopSoft(): Promise<{ ok: boolean; state?: string }> {
    return request("/api/engine/stop-soft", { method: "POST" });
  },

  async pause(): Promise<{ ok: boolean; state?: string }> {
    return request("/api/engine/pause", { method: "POST" });
  },

  async resume(): Promise<{ ok: boolean; state?: string }> {
    return request("/api/engine/resume", { method: "POST" });
  },

  // ===== accounts =====
  async getAccounts(): Promise<unknown[]> {
    const r = await request<{ ok: boolean; accounts: unknown[] }>("/api/accounts");
    return r.accounts || [];
  },

  async checkAccount(name: string): Promise<unknown> {
    return request(`/api/accounts/${encodeURIComponent(name)}/check`, {
      method: "POST",
    });
  },

  /** 启动自检：对所有账号跑双引擎校验（wp 凭证守护 + dm 私信列表拉取），返回可用状态 */
  async selfCheck(): Promise<{
    ok: boolean;
    allOk: boolean;
    items: {
      name: string;
      ok: boolean;
      wp: { level: string; label: string; detail?: string } | null;
      dm: { level: string; label: string; detail?: string } | null;
    }[];
  }> {
    return request("/api/accounts/self-check");
  },

  async scanLogin(name: string): Promise<{ ok: boolean; msg: string }> {
    return request(`/api/accounts/${encodeURIComponent(name)}/scan`, {
      method: "POST",
    });
  },

  /** 私信凭证失效自动重新捕获：打开 chat?isPopup=1 重新授权（替代单纯 scanLogin） */
  async autoRecapture(name: string): Promise<{ ok: boolean; msg: string }> {
    return request(`/api/accounts/${encodeURIComponent(name)}/auto-recapture`, {
      method: "POST",
    });
  },

  /** 打开该账号绑定的指纹浏览器窗口（先停守护释放 profile 锁再弹窗） */
  async openFingerprintBrowser(name: string): Promise<{ ok: boolean; msg: string }> {
    return request(`/api/accounts/${encodeURIComponent(name)}/open-browser`, {
      method: "POST",
    });
  },

  async scanStatus(name: string): Promise<{ name: string; done: boolean; loggedIn: boolean }> {
    return request(`/api/accounts/${encodeURIComponent(name)}/scan-status`);
  },

  async setRole(name: string, role: string): Promise<{ ok: boolean; name: string; role: string }> {
    return request(`/api/accounts/${encodeURIComponent(name)}/role`, {
      method: "POST",
      body: JSON.stringify({ role }),
    });
  },

  async addAccount(name: string): Promise<{ ok: boolean; name: string }> {
    return request("/api/accounts", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
  },

  async removeAccount(name: string): Promise<{ ok: boolean; name: string }> {
    return request(`/api/accounts/${encodeURIComponent(name)}`, {
      method: "DELETE",
    });
  },

  // ===== live =====
  async getStream(): Promise<unknown> {
    return request("/api/live/stream");
  },

  async sendDanmaku(content: string): Promise<{ ok: boolean; content: string }> {
    return request("/api/live/danmaku", {
      method: "POST",
      body: JSON.stringify({ content }),
    });
  },

  async setDmTemplate(template: string): Promise<{ ok: boolean }> {
    return request("/api/live/dm-template", {
      method: "POST",
      body: JSON.stringify({ template }),
    });
  },

  async resolveLive(url: string): Promise<{ ok: boolean; liveId?: string; liveUrl?: string; error?: string }> {
    return request("/api/live/resolve", {
      method: "POST",
      body: JSON.stringify({ url }),
    });
  },

  // ===== messages =====

  /**
   * 更新会话：按需触发前移捕获（经 BCC 拉会话列表 + 会话详情/聊天记录）并写库。
   * 与「引擎校验」区分：引擎校验只判守护活性（轻量），本接口真正跑捕获（重）。
   */
  async refreshConversations(
    account: string,
    withBrowser = true,
  ): Promise<{
    ok: boolean;
    n_conv?: number;
    n_msg?: number;
    elapsed?: number;
    error?: string;
  }> {
    return request(`/api/messages/${encodeURIComponent(account)}/refresh`, {
      method: "POST",
      body: JSON.stringify({ account, with_browser: withBrowser }),
    });
  },

  async getConversations(account: string): Promise<unknown> {
    return request(`/api/messages/conversations?account=${encodeURIComponent(account)}`);
  },

  async getConversation(account: string, convId: string): Promise<unknown> {
    return request(
      `/api/messages/conversation?account=${encodeURIComponent(account)}&conv_id=${encodeURIComponent(convId)}`,
    );
  },

  /** 2026-09-05：channel 指定发送通道。
   *   'ws'=私信守护 HTTP API（默认，稳定）；'wp'=抖音网页版 chat 页 IM SDK。
   */
  async sendDm(
    account: string,
    convId: string,
    text: string,
    channel: "ws" | "wp" = "ws",
  ): Promise<{ ok: boolean }> {
    return request("/api/messages/send", {
      method: "POST",
      body: JSON.stringify({ account, conv_id: convId, text, channel }),
    });
  },

  async requestDm(name: string, comment = ""): Promise<{ ok: boolean; msg?: string }> {
    return request("/api/messages/request", {
      method: "POST",
      body: JSON.stringify({ name, comment }),
    });
  },

  // ===== tasks =====
  async getTasks(): Promise<unknown> {
    return request("/api/tasks");
  },

  /** 当前任务容器快照：直播监听页 / 任务中心回读同一个容器的进程状态 */
  async getCurrentTask(): Promise<CurrentTask> {
    return request("/api/tasks/current");
  },

  async saveTaskConfig(config: Record<string, unknown>): Promise<{ ok: boolean }> {
    return request("/api/tasks/config", {
      method: "POST",
      body: JSON.stringify(config),
    });
  },

  async saveDmPool(
    items: { text: string; enabled: boolean }[] | string[],
  ): Promise<{ ok: boolean; count: number }> {
    return request("/api/tasks/dm-pool", {
      method: "POST",
      body: JSON.stringify(items),
    });
  },

  async exportStats(): Promise<{ ok: boolean; path?: string; error?: string }> {
    return request("/api/tasks/export", { method: "POST" });
  },

  async getTaskHistory(
    limit?: number,
    offset?: number,
  ): Promise<{ ok: boolean; list?: TaskHistoryItem[]; total?: number }> {
    const q =
      limit != null
        ? `/api/tasks/history?limit=${limit}&offset=${offset || 0}`
        : "/api/tasks/history";
    return request(q);
  },

  async clearTaskHistory(): Promise<{ ok: boolean }> {
    return request("/api/tasks/history/clear", { method: "POST" });
  },

  // ===== settings =====
  async getConfig(): Promise<{ ok: boolean; config: Record<string, unknown> }> {
    return request("/api/settings");
  },

  async saveConfig(config: Record<string, unknown>): Promise<{ ok: boolean }> {
    return request("/api/settings", {
      method: "POST",
      body: JSON.stringify(config),
    });
  },

  // ===== logs =====
  async getLogs(
    limit = 500,
    name?: string,
  ): Promise<{ ok: boolean; file: string | null; lines: { ts: string; level: string; text: string }[] }> {
    const q = name ? `/api/logs?limit=${limit}&name=${encodeURIComponent(name)}` : `/api/logs?limit=${limit}`;
    return request(q);
  },

  /** 前端操作写入运行日志 */
  async addLog(level: string, text: string): Promise<{ ok: boolean }> {
    return request("/api/logs/write", {
      method: "POST",
      body: JSON.stringify({ level, text }),
    });
  },

  /** 列出全部启动会话（每个 run_*.log = 一次启动），current 为「本次」会话文件名 */
  async getSessions(): Promise<{ ok: boolean; current: string | null; sessions: { file: string; start: string; size: number; mtime: number }[] }> {
    return request("/api/logs/sessions");
  },

  /** 批量删除历史会话文件（实质删除，current 受保护） */
  async deleteSessions(files: string[]): Promise<{ ok: boolean; deleted: string[]; skipped: string[] }> {
    return request("/api/logs/sessions", {
      method: "DELETE",
      body: JSON.stringify({ files }),
    });
  },
};

// ===== 页面组件统一 Props 类型（1:1 对应旧版 app.js 传给页面的 props）=====

export interface TaskHistoryItem {
  id: number;
  acct: string;
  live_id: string;
  start_ts: string;
  end_ts: string;
  status: "running" | "finished" | "stopped";
  result_count: number;
  records?: Record<string, unknown>[];
  config?: Partial<TaskConfigSnapshot>;
}

/** 任务容器里「当前任务」的配置快照（进入任务 / 复用回读） */
export interface TaskConfigSnapshot {
  live_url: string;
  live_id: string;
  max_target: number;
  interval: number;
  delay: string;
  force_rescan: boolean;
  acct?: string | null;
  dm_pool: { text: string; enabled: boolean }[];
  status_msg: string;
}

/** /api/tasks/current 返回的任务容器 */
export interface CurrentTask {
  ok: boolean;
  has_task: boolean;
  task_id?: number | null;
  engine_state: string;
  status_msg: string;
  config: Partial<TaskConfigSnapshot>;
  live: {
    alive: boolean;
    listening: boolean;
    online: number;
    room_title: string;
    dm_running: boolean;
    dm_paused: boolean;
  };
  counts: { sent: number; captured: number; queue: number; limit: number };
  records: Record<string, unknown>[];
}

/** 任务中心「复用」历史任务 -> 直播监听页预填的载荷 */
export interface ReusePayload {
  room?: string;
  maxTarget?: number;
  interval?: number;
  delay?: string;
  dmPool?: { text: string; enabled: boolean }[];
  forceRescan?: boolean;
  acct?: string | null;
}

/** 历史任务跳转查阅模式的载荷 */
export interface ReviewPayload {
  acct: string;
  liveId: string;
  records: Record<string, unknown>[];
  startTs?: string;
  endTs?: string;
}

export interface PageProps {
  /** toast 提示 */
  push: (msg: string, holdMs?: number) => void;
  /** API 客户端 */
  api: typeof api;
  /** 总览数据（3s 轮询） */
  overview?: Overview | null;
  /** 后端是否已连接 */
  ready?: boolean;
  /** 跳转私信页并预填文本 */
  goMsg?: (name: string, text?: string) => void;
  /** 私信页接收的预填消息 */
  goDm?: { name: string; text: string } | null;
  /** 切换 Tab（任务中心跳转用） */
  setTab?: (tab: string) => void;
  /** 跳转到直播监听页并进入查阅模式查看历史任务结果 */
  goReview?: (payload: ReviewPayload) => void;
  /** 直播监听页收到的查阅模式载荷（由 goReview 设置） */
  reviewPayload?: ReviewPayload | null;
  /** 任务中心「复用」历史任务 -> 预填直播监听页 */
  goReuse?: (payload: ReusePayload) => void;
  /** 直播监听页收到的复用载荷（由 goReuse 设置） */
  reusePayload?: ReusePayload | null;
  /** 私信页当前账号（提升到 App 级，配合常驻 conversations 轮询避免切页冷拉/StrictMode 双拉） */
  msgAcct?: string;
  /** 设置私信页当前账号 */
  setMsgAcct?: (name: string) => void;
}

