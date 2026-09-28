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
  // 2026-09-13：每个请求附带本前端版本，后端据此可识别「前端新/后端旧」
  try { headers["X-App-Version"] = String(__APP_VERSION__); } catch { /* ignore */ }
  const tk = getMemberToken();
  if (tk) headers["X-Member-Token"] = tk;
  const res = await fetch(`${BASE}${path}`, {
    // 2026-09-17 修补（OCR 审查 HIGH —— headers 合并顺序颠倒）：
    // 原为 `{ headers, ...init }`：`init.headers` 会**整体覆盖**默认头，
    // 使调用方一旦传 headers 就丢掉 `X-Member-Token`（鉴权）、`X-App-Version`
    // 与 `Content-Type` → 静默 401。
    // 现**显式合并**：先铺调用方 headers（允许追加自定义头），再让默认头
    // 覆盖同名项 —— 既保留扩展能力，又保证鉴权/版本头**永远在场**。
    ...init,
    headers: {
      ...((init?.headers as Record<string, string> | undefined) || {}),
      ...headers,
    },
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

// ===== 受保护媒体的「带令牌取二进制」（2026-09-17，B 方案）=====
//
// 背景：`<img src>` / `<video src>` **无法携带自定义请求头**，而后端媒体端点
// （`/api/messages/origin_image/...`、`/api/messages/video/...`）受会员门禁保护
// —— 实测无令牌访问一律 401。若直接把后端 URL 塞进 src，浏览器不带令牌，
// 必然取不到图/视频（既有原图显示因此有 `onError` 降级，长期静默破损）。
//
// 方案 B（用户 2026-09-17 拍板）：**后端零改动**，前端先带 `X-Member-Token`
// fetch 回 Blob，再用 `URL.createObjectURL` 交给 `<img>/<video>`。
// 这样会员鉴权保持完整，不引入「媒体端点无鉴权」的安全面变化。
// 调用点统一走 `@/lib/authed-media`，不要在组件里手写。

// ===== 引擎控制端点的账号寻址（唯一契约：query `?acct=`）=====
// 🔴 P1-1 修复（2026-09-23）：四个控制端点（stop/stop-soft/pause/resume）的账号
// 走 **query string**，不是 JSON body。理由与取舍见下方 api.stopEngine 的注释。
function engineControlPath(path: string, account?: string): string {
  const a = (account || "").trim();
  return a ? `${path}?acct=${encodeURIComponent(a)}` : path;
}

/** 该地址是否为「需要带令牌获取」的本机后端 API 资源。 */
export function isLocalApiUrl(src?: string): boolean {
  if (!src) return false;
  if (src.startsWith("/api/")) return true;
  return /^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?\/api\//i.test(src);
}

/** 相对路径 → 后端绝对地址；已是绝对地址（含图床外链）则原样返回。 */
export function toBackendUrl(src: string): string {
  if (!src) return src;
  if (src.startsWith("/")) return `${BASE}${src}`;
  return src;
}

/** 带会员令牌取受保护媒体 → Blob（失败抛错，调用方负责降级）。 */
export async function fetchAuthedBlob(pathOrUrl: string): Promise<Blob> {
  try {
    await ensureBackendReady();
  } catch {
    // 就绪等待失败不阻断，由 fetch 结果说话（与 request() 同一约定）
  }
  const headers: Record<string, string> = {};
  const tk = getMemberToken();
  if (tk) headers["X-Member-Token"] = tk;
  const res = await fetch(toBackendUrl(pathOrUrl), { headers });
  if (!res.ok) {
    if (res.status === 401 && !pathOrUrl.startsWith("/api/member/")) {
      setMemberToken("");   // 会话过期：清本地令牌，UI 轮询会回登录页
    }
    throw new Error(`取媒体失败 (${res.status})`);
  }
  return res.blob();
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
  /**
   * 当前引擎实例的账号（后端 `/api/overview` 归属的 `app.state.adm`）。
   *
   * 🔴 P1-1 修复（2026-09-23）：任务中心的控制按钮需要**显式账号**才能在多任务
   * 并发下停对任务（ADR-002 §5.3）。此前 Overview 类型没有 acct 字段，调用点
   * `api.stopEngine()` 只能不带账号 → 多任务时 409。
   * 兼容旧后端：字段缺失时按空串处理（走「单任务回落 / 歧义 409」旧语义）。
   */
  acct?: string;
}

/** /api/engine/accounts 返回的每个账号引擎状态（ADR-002 §5.6，前端多任务卡片数据源） */
export interface EngineAccountStatus {
  acct: string;
  state: string;
  live_url?: string | null;
  live_id?: string | null;
  sent: number;
  status_msg?: string;
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

/** 受标签管理的板块（对应后端 config_tag.MANAGED_SECTIONS，顺序一致）。 */
export const TAG_MANAGED_SECTIONS = [
  "send",
  "live",
  "capture",
] as const;
export type TagManagedSection = (typeof TAG_MANAGED_SECTIONS)[number];

/** 板块中文名（UI 展示用，唯一处定义）。 */
export const TAG_SECTION_LABELS: Record<TagManagedSection, string> = {
  send: "私信发送",
  live: "直播监听",
  capture: "捕获与存储",
};

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
  // ===== 版本一致性（2026-09-13）：前端 vs 后端 sidecar 版本比对 =====
  checkVersionConsistency,

  // ===== overview =====
  async getBackendStatus(): Promise<BackendStatus> {
    return request("/api/status");
  },

  // ===== 能力探针（M1，2026-09-21 P1 收尾）=====
  // 只读本地事实（DB + 本项目日志），零网络零浏览器，可安全高频轮询。
  async getProbeStatus(): Promise<unknown> {
    return request("/api/probe/status");
  },

  async runProbePatrol(): Promise<unknown> {
    return request("/api/probe/patrol", { method: "POST" });
  },

  async getOverview(): Promise<Overview> {
    return request("/api/overview");
  },

  async getStats(): Promise<{ sent: number; captured: number; queue: number }> {
    return request("/api/stats");
  },

  // ===== engine =====
  async start(config: Record<string, unknown>): Promise<{ ok: boolean; state?: string; acct?: string }> {
    return request("/api/engine/start", {
      method: "POST",
      body: JSON.stringify(config),
    });
  },

  /**
   * 按账号控制引擎：`{"ok":true,"state":"...","acct":"..."}` 。
   *
   * ## 账号契约（唯一真源，2026-09-23 定稿）
   *
   * 后端 `api/engine.py` 的四个控制端点签名是 `(request, acct: str = Query(""))` ——
   * 账号走 **query string `?acct=`**，**不是 JSON body**。
   *
   * 🔴 修复（P1-1，实测）：旧实现把账号塞进 JSON body（`body.account`），
   * 而后端只读 query，于是账号**永远丢失** → 多任务并发时被 409
   * 「存在多个进行中的直播任务…请显式指定 acct」拦死，单任务时又静默作用到
   * 错误的账号（违反 ADR-002 §5.3「不猜账号、停对任务」）。
   * 独立最小复现：`body.account` → 409；`?acct=张老师` → 200。
   *
   * - ``account`` 指定账号（省略 = 交由后端按「单任务回落 / 多任务歧义 409」规则解析）。
   * - ADR-002（v0.44.39+）必须走这条，不直接用 `stop()` / `pause()` / `resume()` ——
   *   后者没有 `account` 参数，在并发下会回落「最近一个实例」而停错任务。
   * - ⚠️ **不要再改回 body**：契约两侧同源（本文件 + `api/engine.py`），
   *   `frontend` 侧一致性由契约测试守着（`test_engine_contract_p1.py`）。
   */
  async stopEngine(account?: string): Promise<{ ok: boolean; state?: string; acct?: string }> {
    return request(engineControlPath("/api/engine/stop", account), { method: "POST" });
  },

  async stopSoftEngine(account?: string): Promise<{ ok: boolean; state?: string; acct?: string }> {
    return request(engineControlPath("/api/engine/stop-soft", account), { method: "POST" });
  },

  async pauseEngine(account?: string): Promise<{ ok: boolean; state?: string; acct?: string }> {
    return request(engineControlPath("/api/engine/pause", account), { method: "POST" });
  },

  async resumeEngine(account?: string): Promise<{ ok: boolean; state?: string; acct?: string }> {
    return request(engineControlPath("/api/engine/resume", account), { method: "POST" });
  },

  /**
     * 各账号引擎状态一览（ADR-002 §5.6，前端多任务卡片数据源）。
     * 只读：不创建实例，未启动的账号不出现。
     */
    async listEngineAccounts(): Promise<{ ok: boolean; items: EngineAccountStatus[] }> {
      return request("/api/engine/accounts");
    },

    // ===== accounts =====
    /** 2026-09-17：ChatLab 导出到默认目录并返回下载直链（乙方案）。 */
  async exportChatlab(
    account: string,
    convId: string,
    fmt: "json" | "jsonl" = "jsonl",
  ): Promise<{ ok: boolean; url?: string; filename?: string; messages?: number;
               members?: number; error?: string }> {
    return request("/api/messages/export/chatlab/download", {
      method: "POST",
      body: JSON.stringify({ account, conv_id: convId, fmt }),
    });
  },

  /**
   * 2026-09-18（E8）：聊天记录 → 知识库。
   *
   * `preview=true` → `action=to_kb_preview`，**零写入**，返回抽取出的问答对
   *   `[{question, answer, source_msg_id, ts}]` 供前端勾选（计划铁律：必须预览后再入库）；
   * `preview=false` → `action=to_kb`，把**全量抽取结果**写入 `reply_kb`/`pro_kb`。
   *   注意：后端 to_kb 是「整会话抽取即入库」，无逐条勾选参数 —— 前端勾选预览
   *   用于**人工把关**（看了不对就取消），精细逐条入库走 ReplyKb 页的手动添加。
   */
  async exportToKb(
    account: string,
    convId: string,
    opts: { target?: "reply" | "pro"; maxItems?: number; preview?: boolean } = {},
  ): Promise<{
    ok: boolean;
    pairs?: { question: string; answer: string; source_msg_id: string | null; ts: number }[];
    count?: number;
    added?: number;
    skipped?: number;
    target?: string;
    error?: string;
  }> {
    const action = opts.preview ? "to_kb_preview" : "to_kb";
    return request("/api/messages/export/chatlab", {
      method: "POST",
      body: JSON.stringify({
        account,
        conv_id: convId,
        action,
        target: opts.target ?? "reply",
        max_items: opts.maxItems ?? 50,
      }),
    });
  },

  /** 2026-09-17：长图直出 PNG（Pillow 原生绘制，不经 BCC）。 */
  async renderChatPng(
    account: string,
    convId: string,
    opts: {
      startSeq?: number; endSeq?: number; theme?: string; title?: string;
      subtitle?: string; width?: number; scale?: number; asBase64?: boolean;
    } = {},
  ): Promise<{ ok: boolean; data_uri?: string; bytes?: number; error?: string }> {
    return request("/api/messages/render/png", {
      method: "POST",
      body: JSON.stringify({
        account, conv_id: convId,
        start_seq: opts.startSeq ?? null, end_seq: opts.endSeq ?? null,
        theme: opts.theme ?? "dark", title: opts.title ?? "",
        subtitle: opts.subtitle ?? "", width: opts.width ?? 520,
        scale: opts.scale ?? 2.0, as_base64: opts.asBase64 ?? false,
      }),
    });
  },

  /** 2026-09-17：长图 HTML（自包含，可打印/另存）。 */
  async renderChatHtml(
    account: string,
    convId: string,
    opts: {
      startSeq?: number; endSeq?: number; theme?: string; title?: string;
      subtitle?: string; width?: number; scale?: number;
    } = {},
  ): Promise<{ ok: boolean; html?: string; chars?: number; theme?: string;
               error?: string }> {
    return request("/api/messages/render/html", {
      method: "POST",
      body: JSON.stringify({
        account, conv_id: convId,
        start_seq: opts.startSeq ?? null, end_seq: opts.endSeq ?? null,
        theme: opts.theme ?? "dark", title: opts.title ?? "",
        subtitle: opts.subtitle ?? "", width: opts.width ?? 520,
        scale: opts.scale ?? 2.0,
      }),
    });
  },

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
  async openFingerprintBrowser(
    name: string,
  ): Promise<{
    ok: boolean;
    msg: string;
    /** H-20：True=窗口已就绪；False=仅受理/切换中（冷启动 1~3 分钟）；null=不适用 */
    settled?: boolean | null;
    switching?: boolean | null;
  }> {
    return request(`/api/accounts/${encodeURIComponent(name)}/open-browser`, {
      method: "POST",
    });
  },

  /** 恢复该账号 BCC 容器为纯无头（与 openFingerprintBrowser 配对） */
  async hideFingerprintBrowser(
    name: string,
  ): Promise<{
    ok: boolean;
    msg: string;
    settled?: boolean | null;
    switching?: boolean | null;
  }> {
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

  /** 真实探测该账号代理配置下的出口 IP 与归属地（三态通用，不拉起浏览器） */
  async proxyTest(
    name: string,
    form: { type: string; host: string; port: string; user: string; pass: string },
  ): Promise<{
    ok: boolean;
    ip: string;
    country: string;
    city: string;
    region: string;
    isp: string;
    timezone: string;
    is_proxy?: boolean | null;
    is_datacenter?: boolean | null;
    mode: string;
    via: string;
    error: string;
  }> {
    return request(`/api/accounts/${encodeURIComponent(name)}/proxy-test`, {
      method: "POST",
      body: JSON.stringify(form),
    });
  },

  /** 查询账号代理配置状态（configured/masked/error/mode） */
  async proxyStatus(name: string): Promise<{
    configured: boolean;
    masked: string;
    error: string;
    mode?: string;
  }> {
    return request(`/api/accounts/${encodeURIComponent(name)}/proxy-status`, {
      method: "GET",
    });
  },

  async scanStatus(name: string): Promise<{
    name: string;
    done: boolean;
    loggedIn: boolean;
    qrPng?: string;
    stage?: string;
    needCode?: boolean;
    path?: string;
    rejected?: string;
    error?: string;
  }> {
    return request(`/api/accounts/${encodeURIComponent(name)}/scan-status`);
  },

  /** F4：启动短信验证码登录（ADR-017 / H-30）。 */
  async smsLogin(name: string, phone: string): Promise<{ ok: boolean; msg: string }> {
    return request(`/api/accounts/${encodeURIComponent(name)}/sms-login`, {
      method: "POST",
      body: JSON.stringify({ phone }),
    });
  },

  /** F4：提交短信验证码（R1-c 本机 Web 前端输入通道）。 */
  async submitSmsCode(name: string, code: string): Promise<{ ok: boolean; msg: string }> {
    return request(`/api/accounts/${encodeURIComponent(name)}/sms-code`, {
      method: "POST",
      body: JSON.stringify({ code }),
    });
  },

  /** RPA 二维码是**本地绝对路径**，需经后端受保护端点取字节（不能直拼 file://）。 */
  qrImageUrl(pngPath: string): string {
    return `/api/accounts/qr-image?path=${encodeURIComponent(pngPath)}`;
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

  // ===== 直播间登记表（房间层，ADR-003，kv live_rooms）=====
  // 身份 + 策略引用 + 脱敏开关；**与策略层分离**（写这里不会污染 live_room_configs）

  async listLiveRooms(): Promise<{ ok: boolean; items: LiveRoom[] }> {
    return request("/api/live/rooms");
  },

  async saveLiveRoom(room: Partial<LiveRoom> & { id?: string }): Promise<{
    ok: boolean;
    room?: LiveRoom;
    error?: string;
  }> {
    return request("/api/live/rooms", {
      method: "POST",
      body: JSON.stringify(room),
    });
  },

  async deleteLiveRoom(rid: string): Promise<{
    ok: boolean;
    deleted?: string;
    error?: string;
  }> {
    return request(`/api/live/rooms/${encodeURIComponent(rid)}`, {
      method: "DELETE",
    });
  },

  // ===== F5 搜索发现（ADR-018，只读） =====

  /**
   * 按关键词**只读**搜索直播间（后端 `/api/live/rooms/discover`）。
   *
   * 只做搜索，**上架另走** `saveLiveRoom`（既有 save_room），
   * 因此不存在第二套存储。
   *
   * `blocked=true` 表示被平台风控拦截（Argus 网关非 200）
   * —— 与「真的没搜到」是**两回事**，UI 必须分别呈现（禁止假成功）。
   */
  async discoverLiveRooms(query: string, account?: string, num = 20): Promise<{
    ok: boolean;
    query?: string;
    items?: DiscoveredRoom[];
    blocked?: boolean;
    transport?: { status?: number; bytes?: number } | null;
    error?: string;
  }> {
    return request("/api/live/rooms/discover", {
      method: "POST",
      body: JSON.stringify({ query, account: account || "", num }),
    });
  },

  /** 一次性迁移存量「房间形」旧策略记录 → live_rooms（默认干跑）。 */
  async migrateLiveRooms(dryRun = true): Promise<{
    ok: boolean;
    dry_run?: boolean;
    found?: string[];
    plan?: Record<string, unknown>[];
    migrated_rooms?: string[];
    created_strategies?: string[];
    removed_config_keys?: string[];
    error?: string;
  }> {
    return request("/api/live/rooms/migrate", {
      method: "POST",
      body: JSON.stringify({ dry_run: dryRun, apply: !dryRun }),
    });
  },

  // ===== 直播配置标签（参数唯一可写入口；规范前缀 config-tags，兼容 room-configs） =====

  async listRoomConfigs(): Promise<{ ok: boolean; items: RoomConfig[] }> {
    return request("/api/live/config-tags");
  },

  async saveRoomConfig(cfg: Partial<RoomConfig> & { id?: string }): Promise<{ ok: boolean; config?: RoomConfig; error?: string }> {
    return request("/api/live/config-tags", {
      method: "POST",
      body: JSON.stringify(cfg),
    });
  },

  async deleteRoomConfig(roomId: string): Promise<{
    ok: boolean;
    deleted?: string;
    /** 本次删除**自动解绑**的直播间数量（ADR-003 §3.4，删除策略的引用完整性） */
    unbound?: number;
    warning?: string;
    error?: string;
  }> {
    return request(`/api/live/config-tags/${encodeURIComponent(roomId)}`, {
      method: "DELETE",
    });
  },

  async applyRoomConfig(
    sid: string,
    ctx: { room_id?: string; account?: string } = {},
  ): Promise<{ ok: boolean; config?: Record<string, unknown>; error?: string }> {
    return request(`/api/live/config-tags/${encodeURIComponent(sid)}/apply`, {
      method: "POST",
      body: JSON.stringify({ room_id: ctx.room_id || "", account: ctx.account || "" }),
    });
  },

  /**
   * 「重启标签」：保存该直播间配置 + **热更到正在运行的监听任务**。
   *
   * 后端设计契约（2026-09-15 用户定调）：只把变更的配置内容补进正在运行的
   * 任务，**不中断监听**（不重建 WS、不重扫凭证、不清队列）。
   * 三种结果都会如实下发，前端必须逐条呈现，禁止把 not_applied 当成功：
   *   - restart.ok=true            引擎运行中，applied 列出实际生效的字段
   *   - restart.ok=false           引擎未运行 → 已保存，点「开始自动私信」后生效
   *   - restart.not_applied_fields 换直播间/换账号/强制重扫属「换任务」语义，未生效
   */
  async restartRoomConfig(
    sid: string,
    ctx: { room_id?: string; account?: string } = {},
  ): Promise<{
    ok: boolean;
    config?: RoomConfig;
    applied_fields?: string[];
    error?: string;
    restart?: {
      ok: boolean;
      applied: string[];
      not_applied: string[];
      not_applied_fields?: string[];
      reason?: string;
      engine_state?: string;
    };
  }> {
    return request(`/api/live/config-tags/${encodeURIComponent(sid)}/restart`, {
      method: "POST",
      body: JSON.stringify({ room_id: ctx.room_id || "", account: ctx.account || "" }),
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
  ): Promise<{ ok: boolean; error?: string; msg?: string; reason_code?: string }> {
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
  ): Promise<{ ok: boolean; error?: string; msg?: string; reason_code?: string; info?: Record<string, unknown> }> {
    return request("/api/messages/send_image", {
      method: "POST",
      body: JSON.stringify({ account, conv_id: convId, image_b64: imageB64, filename }),
    });
  },

  /** 2026-09-17：语音消息转写（对照上游 douyin-chat-export v2.0.0）。
   *  识别在账号自己的 BCC 浏览器上下文里发出（带登录态），只处理语音消息。 */
  async transcribeVoice(
    account: string,
    convId = "",
    limit = 30,
    msgId = "",
  ): Promise<{
    ok: boolean;
    requested?: number;
    succeeded?: number;
    skipped?: number;
    reason?: string;
    error?: string;
  }> {
    return request("/api/messages/voice/transcribe", {
      method: "POST",
      body: JSON.stringify({ account, conv_id: convId, limit, msg_id: msgId }),
    });
  },

  /** 2026-09-17：会话内/全库消息检索（只读，对照上游开放 API /api/search）。 */
  async searchMessages(
    account: string,
    opts: {
      q?: string;
      convId?: string;
      startTime?: number;
      endTime?: number;
      mediaType?: "image" | "video" | "media";
      page?: number;
      pageSize?: number;
    } = {},
  ): Promise<{ ok: boolean; items?: unknown[]; total?: number; error?: string }> {
    return request("/api/messages/search", {
      method: "POST",
      body: JSON.stringify({
        account,
        q: opts.q ?? "",
        conv_id: opts.convId ?? null,
        start_time: opts.startTime ?? null,
        end_time: opts.endTime ?? null,
        media_type: opts.mediaType ?? null,
        page: opts.page ?? 1,
        page_size: opts.pageSize ?? 50,
      }),
    });
  },

  /**
   * 2026-09-17：IM 视频取用（点播）——下载密文 → CENC 解密 → faststart。
   *
   * 只需给 `account`+`msgId`：后端自己从 `extra.video` 取要素，缺地址时经
   * BCC 页面上下文用 `tkey` 换签名地址。返回 `{url}` 为本机受保护地址，
   * 前端须用 `useAuthedMediaUrl` / `<AuthedVideo>` 带令牌取 Blob 播放
   * （`<video src>` 无法携带 `X-Member-Token`，直连必然 401）。
   */
  async resolveVideo(
    account: string,
    msgId: string,
    opts: { convId?: string; force?: boolean } = {},
  ): Promise<{ ok: boolean; url?: string; bytes?: number; cached?: boolean;
               decrypted?: boolean; error?: string }> {
    return request("/api/messages/video/resolve", {
      method: "POST",
      body: JSON.stringify({
        account,
        msg_id: msgId,
        conv_id: opts.convId ?? "",
        force: opts.force ?? false,
      }),
    });
  },

  /** 2026-09-17（E11）：昵称兜底状态（**默认关闭**；用于确认关闭态一眼可见）。 */
  nicknameFallbackStatus(): Promise<{
    ok: boolean;
    config?: {
      enabled?: boolean;
      min_interval_sec?: number;
      max_per_run?: number;
      // 2026-09-18 审查修复：后端 `_cfg()` 的键是 `daily_cap`，
      // 原字段名 `max_per_day` 在响应里**根本不存在**（类型撒谎）。
      daily_cap?: number;
    };
    limit_info?: Record<string, unknown>;
    would_allow_now?: boolean;
    gate?: string;
  }> {
    return request("/api/messages/nickname_fallback/status");
  },

  /**
   * 2026-09-17（E11）：执行一次昵称兜底（受开关/间隔/上限三重约束）。
   * `dryRun=true` 只列候选、**不发任何请求** —— 用于先看会查到谁。
   */
  nicknameFallbackRun(
    account: string,
    opts: { limit?: number; dryRun?: boolean } = {},
  ): Promise<{
    ok: boolean;
    reason?: string;
    candidates?: number;
    queried?: number;
    updated?: number;
    skipped?: number;
    limit_info?: Record<string, unknown>;
  }> {
    return request("/api/messages/nickname_fallback/run", {
      method: "POST",
      body: JSON.stringify({
        account,
        limit: opts.limit ?? null,
        dry_run: opts.dryRun ?? false,
      }),
    });
  },

  /** 2026-09-17：会话逐日消息量（日历跳转用，只读）。 */
  async conversationDaily(
    account: string,
    convId: string,
    tz = 8,
  ): Promise<unknown> {
    return request(
      `/api/messages/conversation/daily?account=${encodeURIComponent(account)}` +
        `&conv_id=${encodeURIComponent(convId)}&tz=${tz}`,
    );
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

  // ===== 定时任务中心（ADR-018 F4）=====
  // 🔴 注意：本组**不提供**「开启自动外发」的接口。
  //    自动外发开关只由后端环境变量控制（ADR-018 D1：默认休眠），
  //    前端若提供一键开启，就会成为绕过休眠的暗门 —— 刻意不做。
  async getScheduler(): Promise<{
    ok: boolean;
    state?: Record<string, unknown>;
    tasks?: unknown[];
    error?: string;
  }> {
    return request("/api/tasks/scheduler");
  },
  async schedulerStart(): Promise<{ ok: boolean; reason?: string; error?: string }> {
    return request("/api/tasks/scheduler/start", { method: "POST" });
  },
  async schedulerStop(): Promise<{ ok: boolean; error?: string }> {
    return request("/api/tasks/scheduler/stop", { method: "POST" });
  },
  async schedulerSaveTask(body: {
    id?: string;
    name?: string;
    kind?: string;
    account?: string;
    params?: Record<string, unknown>;
    interval?: number;
    enabled?: boolean;
  }): Promise<{ ok: boolean; task?: unknown; error?: string }> {
    return request("/api/tasks/scheduler/tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
  },
  async schedulerDeleteTask(taskId: string): Promise<{ ok: boolean; error?: string }> {
    return request(`/api/tasks/scheduler/tasks/${encodeURIComponent(taskId)}`, {
      method: "DELETE",
    });
  },
  async schedulerRunTask(taskId: string): Promise<{
    ok: boolean;
    skipped?: boolean;
    reason?: string;
    error?: string;
  }> {
    return request(`/api/tasks/scheduler/tasks/${encodeURIComponent(taskId)}/run`, {
      method: "POST",
    });
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

  // ===== 采集策略（ADR-018 F1-D3，2026-09-27）=====
  /**
   * 采集策略 = 一组可复用的采集参数（**零身份字段**：不存关键词/aweme_id）。
   *
   * 设计对标：直播域的「策略层」（live_room_configs）之于「房间层」（live_rooms）。
   * 采集域此前是纯即时调用、无参数载体 ⇒ 本层补上该缺口。
   */
  async listCrawlPolicies(): Promise<{ ok: boolean; items: CrawlPolicy[]; total: number }> {
    return request("/api/crawl/policies");
  },

  async saveCrawlPolicy(body: Partial<CrawlPolicy>): Promise<{ ok: boolean; id: string; item: CrawlPolicy; error?: string }> {
    return request("/api/crawl/policies", { method: "POST", body: JSON.stringify(body) });
  },

  async deleteCrawlPolicy(pid: string): Promise<{ ok: boolean; deleted: boolean }> {
    return request(`/api/crawl/policies/${encodeURIComponent(pid)}`, { method: "DELETE" });
  },

  /** 解析成可直接喂给 crawl 端点的参数包（**只读，不发采集请求**）。 */
  async resolveCrawlPolicy(pid: string, account = ""): Promise<{
    ok: boolean;
    params: Record<string, unknown>;
    tag_scope: string | null;
  }> {
    const q = account ? `?account=${encodeURIComponent(account)}` : "";
    return request(`/api/crawl/policies/${encodeURIComponent(pid)}/resolve${q}`, { method: "POST" });
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
    /** 板块级绑定 {account: {section: tag_id}}（B-4 第二层，2026-09-27 接线） */
    bindings_section?: Record<string, Record<string, string>>;
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

  /** 板块级绑定（B-4 第二层）：把某账号的**单个板块**绑到标签 */
  async bindTagSection(
    account: string,
    section: TagManagedSection,
    tagId: string,
  ): Promise<{ ok: boolean; bindings_section: Record<string, Record<string, string>> }> {
    return request("/api/settings/tags/bind/section", {
      method: "POST",
      body: JSON.stringify({ account, section, tag_id: tagId }),
    });
  },

  /** 读全部板块级绑定 {account: {section: tag_id}} */
  async listTagSectionBindings(): Promise<{
    ok: boolean;
    bindings_section: Record<string, Record<string, string>>;
    tags: ConfigTagSummary[];
  }> {
    return request("/api/settings/tags/bind/section");
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
  // 2026-09-24（P1-2）：直播私信文案的 AI 生效状态（只读）。
  // 必须走统一 request() 封装：直连 fetch 会丢 X-Member-Token 头，
  // 被 v0.37.0 起的会员门禁拦成 401。
  async aiLiveDmState(params?: { account?: string; agent_id?: string }): Promise<{
    ok: boolean; active: boolean; reason?: string; reason_label?: string;
    source?: string; account?: string; agent_id?: string;
  }> {
    const q = params?.account || params?.agent_id
      ? `?${new URLSearchParams(
          Object.entries(params).filter(([, v]) => v) as [string, string][]
        ).toString()}`
      : "";
    return request(`/api/ai/live_dm_state${q}`);
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

  // ===== MCP 服务（dyautodm-mcp，2026-09-25）=====
  // 【为什么要这块】MCP 的配置面/令牌面此前**只存在于后端** —— 前端 9 个 tab
  // 里没有任何入口，导致：① 用户不知道有 MCP；② 拿不到 HTTP 模式所需的令牌
  // ⇒ 能力「在位但不可得」。本组方法把既有的 7 个后端端点接到 UI 上。
  //
  // 【两种模式的心智模型（必须让用户看清，否则会误判「没令牌就用不了」）】
  //   · stdio（Hermes / Codex / Claude Code）：**不需要令牌**。进程边界即鉴权，
  //     Hermes 侧配置 `--scope=debug` 即可用，与 token 无关。
  //   · 本机 HTTP（127.0.0.1）：**需要 Bearer 令牌**，由下方「显示令牌」取得。
  async getMcpConfig(): Promise<{ ok: boolean; data: McpStatus; message?: string }> {
    return request("/api/mcp");
  },

  async saveMcpConfig(body: Partial<McpConfigBody>): Promise<{ ok: boolean; data?: McpStatus; message?: string }> {
    return request("/api/mcp/config", { method: "POST", body: JSON.stringify(body) });
  },

  /** 【本机 UI 显式动作】取回明文令牌供复制 —— 唯一返回明文的端点。 */
  async revealMcpToken(): Promise<{ ok: boolean; token?: string; token_epoch?: number; message?: string }> {
    return request("/api/mcp/token/reveal", { method: "POST" });
  },

  /** 轮换令牌：世代号 +1 ⇒ 所有旧令牌**立即**失效（已配置的客户端需同步更新）。 */
  async rotateMcpToken(): Promise<{ ok: boolean; data?: McpStatus; message?: string }> {
    return request("/api/mcp/token/rotate", { method: "POST" });
  },

  /** 按当前配置启动/重启本机 HTTP 服务（配置里改了端口/开关要调它才生效）。 */
  async restartMcp(): Promise<{ ok: boolean; data?: McpStatus; message?: string }> {
    return request("/api/mcp/restart", { method: "POST" });
  },

  async getMcpTools(): Promise<{ ok: boolean; tools: McpTool[]; message?: string }> {
    return request("/api/mcp/tools");
  },

  async getMcpAudit(limit = 50): Promise<{ ok: boolean; entries?: McpAuditEntry[]; stats?: Record<string, McpAuditStat>; message?: string }> {
    return request(`/api/mcp/audit?limit=${limit}`);
  },
};

/** MCP 运行态 + 配置（`GET /api/mcp` 的 data 字段，与后端 `_status()` 逐字段对齐）。 */
export interface McpStatus {
  enabled: boolean;
  preferred_port: number;
  allow_write_actions: boolean;
  require_confirmation: boolean;
  log_retention: number;
  /** 是否已生成令牌（布尔，不泄露值） */
  token_set: boolean;
  /** 脱敏展示：前 4 后 4 */
  token_masked: string;
  /** 世代号：轮换即 +1，旧令牌立即失效 */
  token_epoch: number;
  /** 本机 HTTP 服务是否在跑 */
  running: boolean;
  bound_port: number | null;
  /** 当前 scope 下可见工具数 */
  tools: number;
  pending_confirmations: number;
  listen_host: string;
}

/**
 * F5 搜索发现：平台搜索返回的一条直播间（**只读**，来自 `/discover`）。
 *
 * ⚠️ D7 昵称红线：`nickname` **只**取搜索结果自带的值，前端与后端都**不得**
 * 为补齐昵称再去回调 `bulk_user_info` / `get_im_user_info`。取不到就显示「—」。
 */
export interface DiscoveredRoom {
  /** 直播间号（web_rid） */
  room_id: string;
  /** 直播间标题 */
  title: string;
  /** 主播昵称（**仅搜索结果自带**，可能为空） */
  nickname: string;
  sec_uid: string;
  /** 在线人数 */
  online_count: number;
  /** 封面 URL（可能为空） */
  cover: string;
  /** 后端拼好的可点链接（有 room_id 时非空） */
  live_url: string;
}

/** 可写配置字段（`POST /api/mcp/config` 的 body）。 */
export interface McpConfigBody {
  enabled: boolean;
  preferred_port: number;
  allow_write_actions: boolean;
  require_confirmation: boolean;
  log_retention: number;
}

export interface McpTool {
  name: string;
  /** "read" | "write"（后端 READ/WRITE 常量小写） */
  level: string;
  summary: string;
  params: Record<string, string>;
}

export interface McpAuditEntry {
  ts: string;
  tool: string;
  ok: boolean;
  code: string;
  elapsed_ms: number;
  summary: Record<string, unknown>;
}

export interface McpAuditStat {
  calls: number;
  fails: number;
  ms: number;
}

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

/** 直播策略（一条 = 一套发送策略；**只含策略字段**，无身份字段） */
export interface RoomConfig {
  /** 策略 id（服务端生成 lc_<ts>，或用户自定义 ≤40 字） */
  id?: string;
  /** @deprecated 兼容别名，等于 id（旧数据/脚本读它） */
  room_id?: string;
  /** 策略名称（如「保守-慢速」） */
  name?: string;
  max_target?: number;
  interval?: number;
  delay?: string;
  dm_pool?: { text: string; enabled: boolean }[];
  acct?: string | null;
  auto_link_mic?: boolean;
  link_mic_mode?: "audio" | "video";
  updated_at?: number;
}

/**
 * 直播间登记（**房间层**，ADR-003，kv `live_rooms`）。
 *
 * 与 `RoomConfig`（策略层）**物理分离**：本记录存**身份 + 策略引用 + 脱敏开关**，
 * 不含任何发送参数。删除策略时后端自动把引用它的房间 `strategy_id` 置空。
 */
export interface CrawlPolicy {
  id: string;
  name: string;
  /** 采集类型：video | user | comment */
  kind: "video" | "user" | "comment";
  /** 每次上限（后端收敛 1..50） */
  num: number;
  /** 排序：0 综合 / 1 最多点赞 / 2 最新 */
  sort_type: string;
  /** 发布时段：0 不限 / 1 一天内 / 7 一周内 / 180 半年内 */
  publish_time: string;
  /** 时长过滤（秒区间，可空） */
  filter_duration: string;
  /** 搜索范围（可空） */
  search_range: string;
  /** 内容形式：空不筛 / 0 视频 / 1 图文 */
  content_type: string;
  /** 翻页轮数上限（后端收敛 1..100）——防「has_more 恒真且 data 空」死循环 */
  max_rounds: number;
  updated_at?: number;
}

export interface LiveRoom {
  /** 房间记录 id（形如 `lr_<epoch_ms>`） */
  id: string;
  /** 直播间号（真实 web_rid） */
  room_id: string;
  /** 直播间链接 */
  live_url: string;
  /** 备注（用户填） */
  name: string;
  /** 引用的策略 id（live_room_configs 的键）；空 = 未绑定 */
  strategy_id: string;
  /**
   * 房间级标签绑定（ADR-018 F1-D1，2026-09-27）；空 = 未绑（回落账号/板块级）。
   *
   * 优先级：**房间级 > 板块级 > 整账号级**，见后端 `config_tag.scope_of`。
   * 留空即「跟随外层」，不是「不生效」——这一区分决定了用户能否只给单个
   * 直播间单独定风控参数而不影响其它房间。
   */
  tag_id?: string;
  /**
   * 该房间是否允许「检测到脱敏仍继续监听（**仅统计**）」。
   *
   * 语义（ADR-003 §3.3，一级概念两级粒度）：全局脱敏策略 = 默认行为；
   * 本字段 = 该房间的覆盖开关。**不是**「能拿到昵称」——
   * 解密权取决于房间归属（自营有 / 他人默认脱敏，与凭证无关）。
   */
  allow_desensitized: boolean;
  updated_at?: number;
}

/** 任务容器里「当前任务」的配置快照（进入任务 / 复用回读） */
export interface TaskConfigSnapshot {
  live_url: string;
  live_id: string;
  max_target: number;
  interval: number;
  delay: string;
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
  /** 更新会话进行态（2026-09-13 提升到 App 级：切页不丢状态）。
   *  refreshing 原本是 messages.tsx 组件内 state，切页即卸载 → 状态归零，
   *  用户看到「更新中」效果消失。提到 App 级后切页/切回来都保持。 */
  refreshState?: { account: string; startedAt: number } | null;
  setRefreshState?: (s: { account: string; startedAt: number } | null) => void;
}



// ===== 版本一致性校验（2026-09-13）=====
// 用户提出：前后端分别构建部署，可能出现「前端新版本 / 后端旧版本」而无任何察觉。
// 判据：前端 __APP_VERSION__（vite 注入，源=package.json）对比后端 /api/version.backend
//      （构建时由 build_sidecar.py 写入 backend/version.json，随 sidecar 打包）。
export interface VersionCheck {
  frontend: string;
  backend: string;
  match: boolean;
  detail: string;
}

export async function checkVersionConsistency(): Promise<VersionCheck> {
  const fe = String(__APP_VERSION__);
  let be = "unknown";
  let detail = "";
  // 2026-09-17 修复（v0.43.43）：探针必须先等 sidecar 就绪再发。
  // 旧实现裸 fetch：sidecar 冷启动（PyInstaller 解包 + uvicorn 监听）通常 >1.2s，
  // App 挂载后 1.2s 的探针必然 ECONNREFUSED → TypeError: Failed to fetch →
  // backend 误判 "unknown" → 版本门禁阻断启动（全屏遮罩永不消失）。
  // 复用餐的就绪等待（ensureBackendReady：拉 sidecar + 30s 轮询 /api/status）。
  try {
    await ensureBackendReady();
  } catch (e) {
    detail = `后端引擎未就绪（${String(e)}）`;
  }
  if (!detail) {
    try {
      const r = await fetch(`${BASE}/api/version`, { method: "GET" });
      if (r.ok) {
        const j = (await r.json()) as { backend?: string };
        be = String(j.backend || "unknown");
        if (be === "unknown") detail = "/api/version 缺少 backend 字段（sidecar 过旧，版本探针缺失）";
      } else {
        detail = `/api/version 返回 ${r.status}`;
      }
    } catch (e) {
      detail = `无法连接后端: ${String(e)}`;
    }
  }
  const match = be !== "unknown" && be === fe;
  if (!match && !detail) {
    detail = `前端 ${fe} ≠ 后端 ${be}（sidecar 未更新或部署未生效）`;
  }
  _diag("version.check", { frontend: fe, backend: be, match, detail });
  return { frontend: fe, backend: be, match, detail };
}
