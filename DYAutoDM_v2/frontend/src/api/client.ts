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

// ===== 会员体系（v0.37.0）：token 管理 =====
const MEMBER_TOKEN_KEY = "dy_member_token";
export function getMemberToken(): string {
  try {
    return localStorage.getItem(MEMBER_TOKEN_KEY) || "";
  } catch {
    return "";
  }
}
export function setMemberToken(t: string) {
  try {
    if (t) localStorage.setItem(MEMBER_TOKEN_KEY, t);
    else localStorage.removeItem(MEMBER_TOKEN_KEY);
  } catch { /* ignore */ }
}

/** 启动就绪度（免鉴权）：前端 BootSplash 轮询，等 daemons_ready 才放行。 */
// ===== 启动诊断日志（2026-09-08 排查用）：写 localStorage，可从 DevTools 或
//      Rust 侧读取；同时也 console 输出，便于 webview 控制台查看 =====
function _diag(msg: string, data?: unknown) {
  try {
    const line = `[${new Date().toISOString()}] ${msg}` + (data !== undefined ? ` :: ${JSON.stringify(data)}` : "");
    const key = "dy:bootlog";
    const prev = localStorage.getItem(key) || "";
    localStorage.setItem(key, (prev + String.fromCharCode(10) + line).slice(-20000));
    console.log("[BOOT]" + line);
    // 同步写文件（经 Rust 命令），便于脱离 DevTools 直接核查
    try {
      const w = (window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__;
      if (w) {
        import("@tauri-apps/api/core").then(({ invoke }) => {
          invoke("write_boot_log", { text: line }).catch(() => {});
        });
      }
    } catch { /* ignore */ }
  } catch { /* ignore */ }
}
export function getBootLog(): string {
  try { return localStorage.getItem("dy:bootlog") || ""; } catch { return ""; }
}

export async function getReadyGate(): Promise<{ ok: boolean; daemons_ready: boolean; accounts: number }> {
  try {
    _diag("ready.fetch.start", { base: BASE });
    const r = await fetch(`${BASE}/api/ready`, { method: "GET" });
    _diag("ready.fetch.done", { status: r.status, ok: r.ok });
    if (!r.ok) return { ok: false, daemons_ready: false, accounts: 0 };
    const j = (await r.json()) as { ok: boolean; daemons_ready: boolean; accounts: number };
    _diag("ready.json", j);
    return j;
  } catch (e) {
    _diag("ready.fetch.error", { err: String(e) });
    return { ok: false, daemons_ready: false, accounts: 0 };
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  // 首次请求前确保 backend sidecar 已就绪（Tauri 模式自动拉起，浏览器模式 no-op）。
  // 2026-09-08 修复：ensureBackendReady() 在 backend 未就绪时会 throw
  // （waitBackendReady 超时/探测失败），导致首个请求（overview）被 reject 成
  // 「Failed to fetch」，而 ready 恒 false → BootSplash 永不消失。
  // 就绪等待只是优化，失败不应阻断请求本身 —— 降级为直接发起，由 fetch 结果说话。
  try {
    await ensureBackendReady();
  } catch {
    // 忽略就绪等待失败，继续发请求
  }
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const tk = getMemberToken();
  if (tk) headers["X-Member-Token"] = tk;
  const res = await fetch(`${BASE}${path}`, {
    headers,
    ...init,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    // 401 = 会话过期：清除本地 token（UI 层据 state 轮询回到登录页）
    if (res.status === 401 && !path.startsWith("/api/member/")) {
      setMemberToken("");
    }
    throw new Error(`API ${path} 失败 (${res.status}): ${text}`);
  }
  return res.json() as Promise<T>;
}

// ===== 会员 API（v0.37.0）=====

export interface MemberState {
  loggedIn: boolean;
  username?: string;
  memberId?: string;
}

export interface LoginResult {
  ok: boolean;
  token: string;
  memberId: string;
  username: string;
}

export const memberApi = {
  async state(): Promise<MemberState> {
    try {
      return await request<MemberState>("/api/member/state");
    } catch {
      return { loggedIn: false };
    }
  },
  async login(username: string, password: string): Promise<LoginResult> {
    const r = await request<LoginResult>("/api/member/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    setMemberToken(r.token);
    return r;
  },
  async register(username: string, password: string): Promise<{ ok: boolean; memberId: string }> {
    return request("/api/member/register", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
  },
  async logout(): Promise<void> {
    try {
      await request("/api/member/logout", { method: "POST" });
    } finally {
      setMemberToken("");
    }
  },
  async changePassword(oldPassword: string, newPassword: string): Promise<{ ok: boolean }> {
    return request("/api/member/change-password", {
      method: "POST",
      body: JSON.stringify({ oldPassword, newPassword }),
    });
  },
  async list(): Promise<{ members: { memberId: string; username: string; createdAt: string }[] }> {
    return request("/api/member/list");
  },
  async delete(memberId: string, password: string): Promise<{ ok: boolean }> {
    return request("/api/member/delete", {
      method: "POST",
      body: JSON.stringify({ memberId, password }),
    });
  },
}

// ===== 协议类型（待 openapi-typescript 生成替换）=====

export interface BackendStatus {
  ok: boolean;
  running: boolean;
  sent?: number;
  limit?: number;
}

/** 模型提供商预设（/api/ai/providers 返回结构） */
export interface ModelProvider {
  id: string; name: string; base_url: string; api_protocol: string;
  needs_key: boolean; key_hint: string;
  models_chat: string[]; models_vision: string[];
}

/** 模型中心（v0.39.0）：提供商 / 模型 / 避障链路 / 兜底 / 消费方 */
export interface HubProvider {
  id: string; name: string; base_url: string; api_protocol: string;
  api_key?: string;
  key_status?: { ok: boolean; checked_at?: number; detail?: string };
  models_fetched_at?: number;
}

export interface HubModel {
  id: string; provider_id: string; model: string;
  caps: string[]; source: string;
}

export type HubRouteKind = "llm" | "vision" | "sem";

export interface HubConsumerBinding {
  mode: "route" | "fixed";
  route?: HubRouteKind;
  model_id?: string;
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

// ===== IM 通知类型（v0.37.0，2026-09-09）=====

/** 支持的渠道类型 */
export type NotifyKind = "weixin_oc" | "wecom" | "dingtalk" | "lark" | "qqofficial";

/** 渠道中文名与字段说明（供 UI 展示，避免把密钥用途写错） */
export const CHANNEL_META: Record<
  NotifyKind,
  { label: string; fields: { key: string; label: string; secret?: boolean; hint?: string }[]; targetHint: string }
> = {
  weixin_oc: {
    label: "个人微信（iLink）",
    targetHint: "对方微信用户 ID（形如 xxx@im.wechat）",
    fields: [
      { key: "token", label: "Bot Token", secret: true, hint: "扫码登录后由系统保存" },
      { key: "base_url", label: "API 地址", hint: "默认 https://ilinkai.weixin.qq.com" },
      { key: "account_id", label: "Bot ID", hint: "登录成功后自动保存" },
    ],
  },
  wecom: {
    label: "企业微信",
    targetHint: "成员 UserID，多人用 | 分隔（留空=@all）",
    fields: [
      { key: "corpid", label: "企业 ID" },
      { key: "corpsecret", label: "应用 Secret", secret: true },
      { key: "agent_id", label: "AgentId", hint: "整型，应用设置页可查" },
      { key: "webhook", label: "群机器人 Webhook", hint: "选填；填了则走群机器人，无需 corpid" },
    ],
  },
  dingtalk: {
    label: "钉钉",
    targetHint: "员工 staffId（私聊）",
    fields: [
      { key: "client_id", label: "AppKey / Client ID" },
      { key: "client_secret", label: "AppSecret", secret: true },
      { key: "robot_code", label: "RobotCode", hint: "选填，默认同 Client ID" },
      { key: "webhook", label: "自定义机器人 Webhook", hint: "选填" },
      { key: "webhook_secret", label: "加签密钥", secret: true, hint: "选填" },
    ],
  },
  lark: {
    label: "飞书",
    targetHint: "open_id（私聊）或 chat_id（群，oc_ 开头）",
    fields: [
      { key: "app_id", label: "App ID" },
      { key: "app_secret", label: "App Secret", secret: true },
      { key: "webhook", label: "自定义机器人 Webhook", hint: "选填" },
    ],
  },
  qqofficial: {
    label: "QQ 官方机器人",
    targetHint: "openid（私聊）或 group_群号（群）",
    fields: [
      { key: "appid", label: "AppID" },
      { key: "secret", label: "AppSecret", secret: true },
    ],
  },
};

/** 单个渠道配置。id/kind/enabled/default_target 为通用字段，其余按 kind 不同 */
export interface NotifyChannelCfg {
  id?: string;
  kind: NotifyKind | string;
  enabled?: boolean;
  default_target?: string;
  [key: string]: unknown;
}

export interface NotifyConfig {
  enabled?: boolean;
  channels?: NotifyChannelCfg[];
  llm?: { base_url?: string; api_key?: string; model?: string };
}

export interface NotifyChannelStatus {
  id: string;
  kind: string;
  enabled: boolean;
  loaded: boolean;
  missing: string[];
  ready: boolean;
}

export interface NotifyStatus {
  ok?: boolean;
  enabled: boolean;
  channels: NotifyChannelStatus[];
}

/** IM 网关授权条目（v0.38.5） */
export interface GatewayGrant {
  key: string;
  role: "admin" | "operator" | "viewer" | "blocked";
  allow_intents: string[];
  note: string;
  approved_at: number;
  channel_id: string;
  sender_id: string;
}

/** IM 网关待授权条目 */
export interface GatewayPending {
  key: string;
  channel_id: string;
  sender_id: string;
  first_text: string;
  last_text: string;
  first_at: number;
  last_at: number;
  msg_count: number;
}

export interface NotifyTestResult {
  ok: boolean;
  results?: Record<string, { ok: boolean; error?: string }>;
  error?: string;
}

// ===== 统一配置中心（v0.37.1，2026-09-09）=====

/** 单个字段的表单元数据（后端 schema 下发，前端据此自动渲染） */
export interface SettingsFieldSchema {
  label: string;
  type: "int" | "float" | "bool" | "str" | "select";
  default: unknown;
  min?: number;
  max?: number;
  env?: string | null;
  /** 生效方式：hot=立即 / restart_daemon=需重启守护 / restart_backend=需重启后端 */
  apply?: "hot" | "restart_daemon" | "restart_backend";
  hint?: string;
  /** 风控敏感项：UI 需醒目标注且下限保护 */
  risk?: boolean;
  /**
   * 下拉选项（type=select）。
   * 后端 app_config.SECTIONS 目前下发的是**字符串数组**（如 ["native","disguise"]），
   * 早期契约注释写的是 {value,label} 对象数组——两种形状前端都要兼容。
   */
  options?: (string | { value: string; label: string })[] | null;
}

export interface SettingsSectionSchema {
  label: string;
  fields: Record<string, SettingsFieldSchema>;
}

/** 后端下发的完整 schema：{ sectionKey: SettingsSectionSchema } */
export type SettingsSchema = Record<string, SettingsSectionSchema>;

// ===== AI Agent（v0.38.0）=====

export interface AiAgentSummary {
  id: string;
  name: string;
  kind?: string;
  scopes?: string[];
  permissions?: Record<string, boolean>;
  model: string;
  strict_level: string;
  merchant_name: string;
  enabled: boolean;
  kb_count: number;
  updated_at: number;
}

/** 专业知识库（思维导图）条目 */
export interface ProKbItem {
  id: number;
  topic: string;
  category: string;
  content: string;
  summary: string;
  enabled: boolean;
  created_at: number;
  updated_at: number;
  /** v0.40 知识演化字段 */
  importance?: number;
  hits?: number;
  occurrence?: number;
  last_accessed_at?: number | null;
  is_stale?: boolean;
  stale_reason?: string;
  deleted_at?: number | null;
}

/** v0.40 维护扫描报告 */
export interface ProKbScanReport {
  scanned: number;
  cold: ProKbStaleItem[];
  low_value: ProKbStaleItem[];
  duplicate: ProKbDupPair[];
  generated_at: number;
}

export interface ProKbStaleItem {
  id: number;
  topic: string;
  category: string;
  summary: string;
  reason: string;
  detail?: string;
}

export interface ProKbDupPair {
  topic: string;
  keep_id: number;
  drop_id: number;
  keep_summary: string;
  drop_summary: string;
  similarity: number;
  reason: string;
}

export interface ProKbMaintainState {
  enabled: boolean;
  interval_hours: number;
  last_run_at: number | null;
  next_run_at: number | null;
  runs: number;
  last_run_result?: Record<string, unknown> | null;
  errors: string[];
}

export interface ProKbTree {
  topic: string;
  children: { category: string; items: ProKbItem[] }[];
}

/** 对话回复库（命中库）条目 */
export interface ReplyKbItem {
  id: number;
  question: string;
  answer: string;
  source: string; // manual | auto
  enabled: boolean;
  hits: number;
  created_at: number;
}

export interface ConfigTagSummary {
  id: string;
  name: string;
  sections: string[];
  field_count: number;
  updated_at: number;
}

export interface ConfigTag {
  id: string;
  name: string;
  created_at: number;
  updated_at: number;
}

export interface AiAgent {
  id: string;
  name: string;
  config: Record<string, unknown>;
  updated_at: number;
}

// ===== API 客户端 =====

export const api = {
  // ===== 启动就绪度（免鉴权，BootSplash 预对齐轮询用） =====
  getReadyGate,

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

  /**
   * 查看/操作该账号登录态：把常驻 BCC 容器**就地切为有头可见**窗口。
   *
   * 2026-09-12 根治：不再另起浏览器实例（旧实现会与 BCC 抢 profile 锁，
   * 导致 BCC 读到失效页面、凭证回写被幽灵 uid 门禁拦截）。
   * 现在窗口就是 BCC 自己——登录态真实，且保活/凭证回写全程不中断。
   * 看完可用 hideFingerprintBrowser 恢复无头省资源。
   */
  async openFingerprintBrowser(name: string): Promise<{ ok: boolean; msg: string }> {
    return request(`/api/accounts/${encodeURIComponent(name)}/open-browser`, {
      method: "POST",
    });
  },

  /** 恢复该账号 BCC 容器为纯无头（与 openFingerprintBrowser 配对） */
  async hideFingerprintBrowser(name: string): Promise<{ ok: boolean; msg: string }> {
    return request(`/api/accounts/${encodeURIComponent(name)}/hide-browser`, {
      method: "POST",
    });
  },

  /** 保存账号代理配置（写 .env 的 DY_PROXY，按账号 IP 隔离） */
  async saveProxy(
    name: string,
    form: { type: string; host: string; port: string; user: string; pass: string },
  ): Promise<{ ok: boolean; msg: string }> {
    return request(`/api/accounts/${encodeURIComponent(name)}/proxy`, {
      method: "POST",
      body: JSON.stringify(form),
    });
  },

  /** 查询账号代理配置状态（configured/masked/error） */
  async proxyStatus(name: string): Promise<{
    configured: boolean;
    masked: string;
    error: string;
  }> {
    return request(`/api/accounts/${encodeURIComponent(name)}/proxy-status`, {
      method: "GET",
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

  // ===== 直播间配置管理（按直播间号存取，含自动申请连麦） =====

  async listRoomConfigs(): Promise<{ ok: boolean; items: RoomConfig[] }> {
    return request("/api/live/room-configs");
  },

  async saveRoomConfig(cfg: Partial<RoomConfig> & { room_id: string }): Promise<{ ok: boolean; config?: RoomConfig; error?: string }> {
    return request("/api/live/room-configs", {
      method: "POST",
      body: JSON.stringify(cfg),
    });
  },

  async deleteRoomConfig(roomId: string): Promise<{ ok: boolean; deleted?: string; error?: string }> {
    return request(`/api/live/room-configs/${encodeURIComponent(roomId)}`, {
      method: "DELETE",
    });
  },

  async applyRoomConfig(roomId: string): Promise<{ ok: boolean; config?: Record<string, unknown>; error?: string }> {
    return request(`/api/live/room-configs/${encodeURIComponent(roomId)}/apply`, {
      method: "POST",
    });
  },

  /** 申请连麦（接口直调：账号凭证 + msToken/a_bogus 签名，与直播监听同链路）。
   *  roomId 传真实 room_id（解析房间号可得）；成功响应 data.waiting_list_offset = 排队位次 */
  async requestLinkMic(account: string, roomId: string, linkType: "audio" | "video" = "audio"): Promise<{
    ok: boolean; status_code?: number; error?: string;
    data?: { linkmic_id_str?: string; auto_join?: boolean; waiting_list_offset?: number; prompts?: string };
  }> {
    return request("/api/live/linkmic/apply", {
      method: "POST",
      body: JSON.stringify({ account, room_id: roomId, link_type: linkType === "video" ? "1" : "2" }),
    });
  },

  /** 连麦状态：排队人数 + 连线者 + linked 判定 */
  async linkmicStatus(account: string, roomId?: string): Promise<{
    ok: boolean;
    status?: { room_id: string; my_uid: string; waiting_total?: number | null;
               linkers?: { id: string; nickname: string }[]; me_in_linkers?: boolean | null;
               linked?: boolean; check?: Record<string, unknown> };
  }> {
    const q = roomId ? `&room_id=${encodeURIComponent(roomId)}` : "";
    return request(`/api/live/linkmic/status?account=${encodeURIComponent(account)}${q}`);
  },

  /** 退出连麦 */
  async linkmicLeave(account: string, roomId?: string): Promise<{ ok: boolean; data?: Record<string, unknown> }> {
    return request("/api/live/linkmic/leave", {
      method: "POST",
      body: JSON.stringify({ account, room_id: roomId }),
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

  /** 2026-09-06：图片发送（后端直发全链路 ①-⑥）。 */
  async sendImage(
    account: string,
    convId: string,
    imageB64: string,
    filename: string,
  ): Promise<{ ok: boolean; error?: string; info?: Record<string, unknown> }> {
    return request("/api/messages/send_image", {
      method: "POST",
      body: JSON.stringify({ account, conv_id: convId, image_b64: imageB64, filename }),
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

  // ===== 统一配置中心（v0.37.1，2026-09-09）=====
  /**
   * 全量配置 + schema。
   * schema 描述每个字段的 label/type/default/min/max/hint/apply，
   * 前端据此自动生成表单，无需为每个参数手写 UI。
   */
  async getSettings(): Promise<{
    ok: boolean;
    config: Record<string, Record<string, unknown>>;
    schema: SettingsSchema;
  }> {
    return request("/api/settings");
  },

  /** 仅下发表单元数据（首屏用，比全量轻量） */
  async getSettingsSchema(): Promise<{ ok: boolean; schema: SettingsSchema }> {
    return request("/api/settings/schema");
  },

  /**
   * 按 section 保存。
   * @returns restart_required：需要重启才生效的目标（daemon / backend）
   */
  async saveSettings(sections: Record<string, Record<string, unknown>>): Promise<{
    ok: boolean;
    saved_sections: string[];
    restart_required: string[];
    config: Record<string, Record<string, unknown>>;
  }> {
    return request("/api/settings", {
      method: "POST",
      body: JSON.stringify({ sections }),
    });
  },

  // ===== AI Agent 模版 + 账号绑定（v0.38.0）=====
  /**
   * Agent 是**模版**，账号绑定 Agent。
   * 绑定关系在设置页维护（不在 AI 页），改 Agent 一次 → 所有绑定账号同步生效。
   */
  async listAgents(): Promise<{
    ok: boolean;
    agents: AiAgentSummary[];
    bindings: Record<string, string>;
  }> {
    return request("/api/ai/agents");
  },

  async saveAgent(body: {
    id?: string;
    name: string;
    config: Record<string, unknown>;
  }): Promise<{ ok: boolean; agent: AiAgent; agents: AiAgentSummary[] }> {
    return request("/api/ai/agents", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  async deleteAgent(id: string): Promise<{
    ok: boolean;
    unbound_accounts: string[];
  }> {
    return request(`/api/ai/agents/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
  },

  /** 绑定账号到 Agent；agentId 为空串 = 解绑 */
  async bindAgent(account: string, agentId: string): Promise<{
    ok: boolean;
    bindings: Record<string, string>;
  }> {
    return request("/api/ai/bind", {
      method: "POST",
      body: JSON.stringify({ account, agent_id: agentId }),
    });
  },

  // ===== 配置标签（v0.38.2）=====
  /**
   * 标签是**指引**，参数仍由 app_config 按 scope 隔离存储，标签不存副本。
   * 标签管「怎么发」（发送风控/直播监听/捕获策略），与 Agent（管「回什么」）并列。
   */
  async listTags(): Promise<{
    ok: boolean;
    tags: ConfigTagSummary[];
    bindings: Record<string, string>;
  }> {
    return request("/api/settings/tags");
  },

  async saveTag(name: string, id?: string): Promise<{
    ok: boolean;
    tag: ConfigTag;
    tags: ConfigTagSummary[];
  }> {
    return request("/api/settings/tags", {
      method: "POST",
      body: JSON.stringify({ id: id || "", name }),
    });
  },

  async deleteTag(id: string): Promise<{
    ok: boolean;
    unbound_accounts: string[];
  }> {
    return request(`/api/settings/tags/${encodeURIComponent(id)}`, {
      method: "DELETE",
    });
  },

  /** 账号绑定标签；tagId 空串 = 解绑（回落全局） */
  async bindTag(account: string, tagId: string): Promise<{
    ok: boolean;
    bindings: Record<string, string>;
  }> {
    return request("/api/settings/tags/bind", {
      method: "POST",
      body: JSON.stringify({ account, tag_id: tagId }),
    });
  },

  /** 保存某标签的参数（实际写入 app_config 的 scope 存储） */
  async saveScoped(
    scope: string,
    sections: Record<string, Record<string, unknown>>,
  ): Promise<{
    ok: boolean;
    saved_sections: string[];
    config: Record<string, Record<string, unknown>>;
  }> {
    return request("/api/settings/scoped", {
      method: "POST",
      body: JSON.stringify({ scope, sections }),
    });
  },

  /** 读某标签存了哪些参数（不含全局回落） */
  async getScoped(tagId: string): Promise<{
    ok: boolean;
    config: Record<string, Record<string, unknown>>;
  }> {
    return request(`/api/settings/scoped/${encodeURIComponent(tagId)}`);
  },

  /** 清空指定 section 回默认值（不传则全清） */
  async resetSettings(sections?: string[]): Promise<{
    ok: boolean;
    reset_sections: string[];
    config: Record<string, Record<string, unknown>>;
  }> {
    return request("/api/settings/reset", {
      method: "POST",
      body: JSON.stringify({ sections: sections || [] }),
    });
  },

  // ===== IM 通知（v0.37.0，2026-09-09）=====
  /** 各渠道就绪状态 */
  async getNotifyStatus(): Promise<NotifyStatus> {
    return request("/api/notify/status");
  },

  /** 读取通知配置（敏感字段已由后端脱敏为 •••• ） */
  async getNotifyConfig(): Promise<{ ok: boolean; config: NotifyConfig }> {
    return request("/api/notify/config");
  },

  /** 保存通知配置。脱敏占位符（全 • ）不会被回写，保留原值 */
  async saveNotifyConfig(config: NotifyConfig): Promise<{ ok: boolean; status?: NotifyStatus; error?: string }> {
    return request("/api/notify/config", {
      method: "POST",
      body: JSON.stringify(config),
    });
  },

  // ===== IM 网关：授权 / 权限组（v0.38.5）=====
  async gatewayOverview(): Promise<{
    ok: boolean;
    mode: "pairing" | "open";
    grants: Record<string, GatewayGrant>;
    pending: GatewayPending[];
  }> {
    return request("/api/notify/gateway");
  },
  async gatewaySetMode(mode: string): Promise<{ ok: boolean; mode?: string; error?: string }> {
    return request("/api/notify/gateway/mode", {
      method: "POST",
      body: JSON.stringify({ mode }),
    });
  },
  async gatewayApprove(key: string, role: string, note = ""): Promise<{
    ok: boolean;
    grants?: Record<string, GatewayGrant>;
    pending?: GatewayPending[];
    error?: string;
  }> {
    return request("/api/notify/gateway/approve", {
      method: "POST",
      body: JSON.stringify({ key, role, note }),
    });
  },
  async gatewayRevoke(key: string): Promise<{
    ok: boolean;
    grants?: Record<string, GatewayGrant>;
    pending?: GatewayPending[];
  }> {
    return request("/api/notify/gateway/revoke", {
      method: "POST",
      body: JSON.stringify({ key }),
    });
  },
  async gatewayAllow(key: string, intents: string[]): Promise<{ ok: boolean; grant?: unknown; error?: string }> {
    return request("/api/notify/gateway/allow", {
      method: "POST",
      body: JSON.stringify({ key, intents }),
    });
  },

  /** 真发一条测试消息 */
  async testNotify(channelId: string, target: string, text?: string): Promise<NotifyTestResult> {
    return request("/api/notify/test", {
      method: "POST",
      body: JSON.stringify({ channel_id: channelId, target, text: text || "" }),
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

  // ===== 模型中心（v0.39.0：提供商/模型/避障链路/兜底/消费方）=====
  async modelhubOverview(): Promise<{
    ok: boolean;
    providers: HubProvider[];
    models: HubModel[];
    routes: Record<HubRouteKind, { models: string[] }>;
    fallback: { model_id: string };
    consumers: Record<string, HubConsumerBinding>;
    consumers_meta: { id: string; label: string; module: string;
      suggest_route: HubRouteKind }[];
    presets: { id: string; name: string; base_url: string;
      api_protocol: string; needs_key: boolean; key_hint: string }[];
    max_chain: number;
  }> {
    return request("/api/modelhub/overview");
  },
  async modelhubSaveProvider(p: Partial<HubProvider>): Promise<{
    ok: boolean; provider: HubProvider; providers: HubProvider[];
    models: HubModel[];
  }> {
    return request("/api/modelhub/providers", {
      method: "POST",
      body: JSON.stringify(p),
    });
  },
  async modelhubDeleteProvider(pid: string): Promise<{
    ok: boolean; removed_models: number; providers: HubProvider[];
    models: HubModel[];
  }> {
    return request(`/api/modelhub/providers/${encodeURIComponent(pid)}`, {
      method: "DELETE",
    });
  },
  async modelhubTestProvider(pid: string): Promise<{
    ok: boolean;
    result: { ok: boolean; detail?: string; checked_at?: number };
  }> {
    return request(`/api/modelhub/providers/${encodeURIComponent(pid)}/test`, {
      method: "POST",
      body: JSON.stringify({}),
    });
  },
  async modelhubFetchModels(pid: string): Promise<{
    ok: boolean; total: number; added: number; warning?: string;
    providers: HubProvider[]; models: HubModel[];
  }> {
    return request(`/api/modelhub/providers/${encodeURIComponent(pid)}/fetch`, {
      method: "POST",
      body: JSON.stringify({}),
    });
  },
  async modelhubAddModel(providerId: string, model: string,
                         caps: string[] = []): Promise<{
    ok: boolean; model: HubModel;
  }> {
    return request("/api/modelhub/models", {
      method: "POST",
      body: JSON.stringify({ provider_id: providerId, model, caps }),
    });
  },
  async modelhubSetCaps(mid: string, caps: string[]): Promise<{ ok: boolean }> {
    return request(`/api/modelhub/models/${encodeURIComponent(mid)}/caps`, {
      method: "POST",
      body: JSON.stringify({ caps }),
    });
  },
  async modelhubDeleteModel(mid: string): Promise<{
    ok: boolean; providers: HubProvider[]; models: HubModel[];
    routes: Record<HubRouteKind, { models: string[] }>;
    fallback: { model_id: string };
    consumers: Record<string, HubConsumerBinding>;
  }> {
    return request(`/api/modelhub/models/${encodeURIComponent(mid)}`, {
      method: "DELETE",
    });
  },
  async modelhubSaveRoute(kind: HubRouteKind, modelIds: string[]): Promise<{
    ok: boolean; routes: Record<HubRouteKind, { models: string[] }>;
  }> {
    return request(`/api/modelhub/routes/${encodeURIComponent(kind)}`, {
      method: "POST",
      body: JSON.stringify({ model_ids: modelIds }),
    });
  },
  async modelhubSetFallback(modelId: string): Promise<{
    ok: boolean; fallback: { model_id: string };
  }> {
    return request("/api/modelhub/fallback", {
      method: "POST",
      body: JSON.stringify({ model_id: modelId }),
    });
  },
  async modelhubSetConsumer(consumer: string, mode: "route" | "fixed",
                            route = "", modelId = ""): Promise<{
    ok: boolean; consumers: Record<string, HubConsumerBinding>;
  }> {
    return request("/api/modelhub/consumers", {
      method: "POST",
      body: JSON.stringify({ consumer, mode, route, model_id: modelId }),
    });
  },

  // ===== AI 获客自动回复（嵌入 douyin-auto-reply-assistant，2026-09-06）=====
  /**
   * 读 AI 配置。agentId 为空读全局；否则读该 Agent 的（叠加在全局之上）。
   * v0.38.3：AI 页 = Agent 编辑器，顶部选哪个就编辑哪个。
   */
  async aiGetConfig(agentId?: string): Promise<{
    ok: boolean;
    config: Record<string, unknown>;
    scope?: string;
    agent_name?: string;
  }> {
    const q = agentId ? `?agent_id=${encodeURIComponent(agentId)}` : "";
    return request(`/api/ai/config${q}`);
  },
  async aiProviders(): Promise<{ ok: boolean; providers: { id: string; name: string; base_url: string; api_protocol: string; needs_key: boolean; key_hint: string; models_chat: string[]; models_vision: string[] }[] }> {
    return request("/api/ai/providers");
  },
  async aiFreellmModels(): Promise<{ ok: boolean; chat: string[]; vision: string[]; embed: string[]; total: number; error?: string }> {
    return request("/api/ai/providers/freellm_models");
  },
  /** 保存 AI 配置。agentId 为空存全局；否则只存该 Agent。 */
  async aiSaveConfig(
    config: Record<string, unknown>,
    agentId?: string,
  ): Promise<{
    ok: boolean;
    config: Record<string, unknown>;
    running: boolean;
    scope?: string;
  }> {
    return request("/api/ai/config", {
      method: "POST",
      body: JSON.stringify({ config, agent_id: agentId || "" }),
    });
  },
  async aiTest(): Promise<{ ok: boolean; msg: string }> {
    return request("/api/ai/test", { method: "POST" });
  },
  async aiTestVision(): Promise<{ ok: boolean; msg: string }> {
    return request("/api/ai/test_vision", { method: "POST" });
  },
  async aiStatus(): Promise<Record<string, unknown>> {
    return request("/api/ai/status");
  },
  async aiStart(): Promise<{ ok: boolean; running: boolean }> {
    return request("/api/ai/start", { method: "POST" });
  },
  async aiStop(): Promise<{ ok: boolean; running: boolean }> {
    return request("/api/ai/stop", { method: "POST" });
  },
  async aiKbList(): Promise<{ ok: boolean; items: { id: number; question: string; answer: string }[] }> {
    return request("/api/ai/knowledge");
  },
  async aiKbSave(item: { id: number; question: string; answer: string }): Promise<{ ok: boolean; msg?: string }> {
    return request("/api/ai/knowledge", {
      method: "POST",
      body: JSON.stringify(item),
    });
  },
  async aiKbDelete(id: number): Promise<{ ok: boolean; msg?: string }> {
    return request(`/api/ai/knowledge/${id}`, { method: "DELETE" });
  },
  // ---- 对话回复库（命中库 v0.39.0）----
  async aiReplyKbList(): Promise<{ ok: boolean; items: ReplyKbItem[] }> {
    return request("/api/ai/replies");
  },
  async aiReplyKbSave(item: { id?: number; question: string; answer: string; enabled?: boolean }): Promise<{ ok: boolean; error?: string }> {
    return request("/api/ai/replies", {
      method: "POST",
      body: JSON.stringify(item),
    });
  },
  async aiReplyKbDelete(id: number): Promise<{ ok: boolean }> {
    return request(`/api/ai/replies/${id}`, { method: "DELETE" });
  },
  // ---- 专业知识库（思维导图 v0.39.1）----
  async aiProKbList(): Promise<{ ok: boolean; items: ProKbItem[]; tree: ProKbTree[] }> {
    return request("/api/ai/prokb");
  },
  async aiProKbSave(item: { id?: number; topic: string; category: string; content: string; summary: string; enabled?: boolean }): Promise<{ ok: boolean; error?: string }> {
    return request("/api/ai/prokb", { method: "POST", body: JSON.stringify(item) });
  },
  async aiProKbDelete(id: number): Promise<{ ok: boolean }> {
    return request(`/api/ai/prokb/${id}`, { method: "DELETE" });
  },
  // ---- v0.40 表格视图 / 统一树 / 知识维护 ----
  async aiProKbPatch(id: number, patch: Partial<ProKbItem>): Promise<{ ok: boolean; item?: ProKbItem; tree?: ProKbTree[]; error?: string }> {
    return request(`/api/ai/prokb/${id}`, { method: "PATCH", body: JSON.stringify(patch) });
  },
  async aiProKbTopics(): Promise<{ ok: boolean; topics: string[]; categories: string[] }> {
    return request("/api/ai/prokb/topics");
  },
  async aiProKbRenameTopic(oldName: string, newName: string): Promise<{ ok: boolean; renamed: number; items: ProKbItem[]; tree: ProKbTree[] }> {
    return request("/api/ai/prokb/rename_topic", { method: "POST", body: JSON.stringify({ old: oldName, new: newName }) });
  },
  async aiProKbMaintainStatus(): Promise<{ ok: boolean; state: ProKbMaintainState; last_scan: ProKbScanReport | null }> {
    return request("/api/ai/prokb/maintain/status");
  },
  async aiProKbMaintainScan(): Promise<{ ok: boolean; report?: ProKbScanReport; error?: string }> {
    return request("/api/ai/prokb/maintain/scan", { method: "POST" });
  },
  async aiProKbMaintainApply(ids: number[], action = "recycle"): Promise<{ ok: boolean; count?: number; items?: ProKbItem[]; tree?: ProKbTree[] }> {
    return request("/api/ai/prokb/maintain/apply", { method: "POST", body: JSON.stringify({ ids, action }) });
  },
  async aiProKbMaintainMerge(pairs: ProKbDupPair[]): Promise<{ ok: boolean; merged?: number; items?: ProKbItem[]; tree?: ProKbTree[] }> {
    return request("/api/ai/prokb/maintain/merge", { method: "POST", body: JSON.stringify({ items: pairs }) });
  },
  async aiProKbMaintainLearn(account?: string): Promise<{ ok: boolean; scanned?: number; extracted?: number; added?: number; error?: string }> {
    const q = account ? `?account=${encodeURIComponent(account)}` : "";
    return request(`/api/ai/prokb/maintain/learn${q}`, { method: "POST" });
  },
  async aiProKbRecycle(): Promise<{ ok: boolean; items: ProKbItem[]; retention_days: number }> {
    return request("/api/ai/prokb/recycle");
  },
  async aiProKbRecycleRestore(id: number): Promise<{ ok: boolean; items: ProKbItem[]; tree: ProKbTree[] }> {
    return request(`/api/ai/prokb/recycle/${id}/restore`, { method: "POST" });
  },
  async aiProKbRecyclePurge(): Promise<{ ok: boolean; purged: number; items: ProKbItem[] }> {
    return request("/api/ai/prokb/recycle/purge", { method: "POST" });
  },
  async aiReplyKbLearn(account?: string): Promise<{ ok: boolean; scanned?: number; extracted?: number; added?: number; error?: string }> {
    const q = account ? `?account=${encodeURIComponent(account)}` : "";
    return request(`/api/ai/replies/learn${q}`, { method: "POST" });
  },
  async aiSemTest(textA?: string, textB?: string): Promise<{ ok: boolean; score?: number; threshold?: number; msg: string }> {
    return request("/api/ai/semantic/test", {
      method: "POST",
      body: JSON.stringify({ text_a: textA || "价格是多少", text_b: textB || "咋收费的啊" }),
    });
  },
  async aiSemRebuild(): Promise<{ ok: boolean; embedded: number; total: number; msg: string }> {
    return request("/api/ai/semantic/rebuild", { method: "POST" });
  },
  async aiSemCacheStatus(): Promise<{ ok: boolean; total: number; embedded: number; model: string; cached_model: string; stale: boolean }> {
    return request("/api/ai/semantic/cache_status");
  },
  /** 上传文件生成知识库 QA（预览，不入库）。用 XMLHttpRequest 上报进度。 */
  /** 专业库文件导入：上传 → AI 提纯为思维导图条目（不落库）。 */
  async aiProKbImport(
    file: File,
    onProgress?: (pct: number) => void,
  ): Promise<{ ok: boolean; filename: string; items: { topic: string; category: string; content: string; summary: string }[]; chars: number; chunks: number; mode: string; error?: string }> {
    await ensureBackendReady();
    const form = new FormData();
    form.append("file", file);
    const tk = getMemberToken();
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${BASE}/api/ai/prokb/import`);
      if (tk) xhr.setRequestHeader("X-Member-Token", tk);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) {
          onProgress(Math.round((e.loaded / e.total) * 100));
        }
      };
      xhr.onload = () => {
        try {
          const r = JSON.parse(xhr.responseText);
          if (xhr.status >= 200 && xhr.status < 300) resolve(r);
          else reject(new Error(r.detail || `提纯失败 (${xhr.status})`));
        } catch {
          reject(new Error(`提纯失败 (${xhr.status})`));
        }
      };
      xhr.onerror = () => reject(new Error("网络错误"));
      xhr.send(form);
    });
  },

  async aiProKbImportConfirm(
    items: { topic: string; category: string; content: string; summary: string }[],
    replace = false,
  ): Promise<{ ok: boolean; added: number; error?: string }> {
    return request("/api/ai/prokb/import/confirm", {
      method: "POST",
      body: JSON.stringify({ items, replace }),
    });
  },

  async aiKbImport(
    file: File,
    onProgress?: (pct: number) => void,
  ): Promise<{ ok: boolean; filename: string; items: { question: string; answer: string; source?: string }[]; chars: number; chunks: number; mode: string }> {
    await ensureBackendReady();
    const form = new FormData();
    form.append("file", file);
    // v0.37.0 会员门禁：/api/* 全部要求 X-Member-Token（普通 request() 自动带，
    // 这个 XHR 直传必须手动补，否则 401「未登录或会话已过期」）
    const tk = getMemberToken();
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `${BASE}/api/ai/knowledge/import`);
      if (tk) xhr.setRequestHeader("X-Member-Token", tk);
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable && onProgress) {
          onProgress(Math.round((e.loaded / e.total) * 100));
        }
      };
      xhr.onload = () => {
        try {
          const r = JSON.parse(xhr.responseText);
          if (xhr.status >= 200 && xhr.status < 300) resolve(r);
          else reject(new Error(r.detail || `导入失败 (${xhr.status})`));
        } catch (err) {
          reject(new Error(`导入失败 (${xhr.status})`));
        }
      };
      xhr.onerror = () => reject(new Error("网络错误"));
      xhr.send(form);
    });
  },
  async aiKbImportConfirm(
    items: { question: string; answer: string; source?: string }[],
    replace = false,
  ): Promise<{ ok: boolean; added: number; total: number; msg: string }> {
    return request("/api/ai/knowledge/import/confirm", {
      method: "POST",
      body: JSON.stringify({ items, replace }),
    });
  },
  async aiLeads(limit = 200): Promise<{ ok: boolean; items: Record<string, unknown>[] }> {
    return request(`/api/ai/leads?limit=${limit}`);
  },
  async aiLeadStatus(id: number, status: string): Promise<{ ok: boolean }> {
    return request("/api/ai/leads/status", {
      method: "POST",
      body: JSON.stringify({ id, status }),
    });
  },
  async aiBlacklist(): Promise<{ ok: boolean; items: string[] }> {
    return request("/api/ai/blacklist");
  },
  async aiBlacklistAdd(userId: string): Promise<{ ok: boolean; items: string[] }> {
    return request("/api/ai/blacklist", {
      method: "POST",
      body: JSON.stringify({ user_id: userId }),
    });
  },
  async aiBlacklistRemove(userId: string): Promise<{ ok: boolean; items: string[] }> {
    return request(`/api/ai/blacklist?user_id=${encodeURIComponent(userId)}`, {
      method: "DELETE",
    });
  },

  // ===== 数据采集（关键词搜索 + 评论采集 + 评论转私信截流）=====
  async crawlSearch(body: {
    account: string;
    query: string;
    kind: "video" | "user";
    sort_type?: string;
    publish_time?: string;
    filter_duration?: string;
    num?: number;
  }): Promise<{
    ok: boolean;
    items: Record<string, unknown>[];
    total: number;
    detail?: string;
  }> {
    return request("/api/crawl/search", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  async crawlComments(body: {
    account: string;
    aweme_id: string;
    limit?: number;
  }): Promise<{ ok: boolean; items: Record<string, unknown>[]; total: number; detail?: string }> {
    return request("/api/crawl/comments", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  async crawlDm(body: {
    account: string;
    uid: string;
    text: string;
  }): Promise<{ ok: boolean; reason: string; detail?: string }> {
    return request("/api/crawl/dm", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  async crawlBatch(body: {
    account: string;
    aweme_id: string;
    text: string;
    keyword?: string;
    limit?: number;
    max_send?: number;
    interval?: number;
  }): Promise<{
    ok: boolean;
    candidates: number;
    sent_ok: number;
    sent_fail: number;
    rate_limited: number;
    results: { uid: string; nickname: string; ok: boolean; reason: string }[];
    detail?: string;
  }> {
    return request("/api/crawl/batch", {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  async crawlHistory(limit = 50): Promise<{
    ok: boolean;
    items: {
      id: number;
      account: string;
      kind: string;
      keyword: string;
      target: string;
      result_count: number;
      ts: string;
    }[];
  }> {
    return request(`/api/crawl/history?limit=${limit}`);
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

/** 直播间配置（按直播间号管理，含自动申请连麦） */
export interface RoomConfig {
  room_id: string;
  name?: string;
  live_url?: string;
  max_target?: number;
  interval?: number;
  delay?: string;
  force_rescan?: boolean;
  dm_pool?: { text: string; enabled: boolean }[];
  acct?: string | null;
  auto_link_mic?: boolean;
  link_mic_mode?: "audio" | "video";
  updated_at?: number;
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

