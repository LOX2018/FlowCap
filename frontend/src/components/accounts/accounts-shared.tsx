/**
 * 账号页 —— 共享层（类型 / 工具 / 小组件）
 *
 * ## 为什么独立（2026-09-15 大组件打散）
 *
 * `accounts-page.tsx` 原为 **2217 行单一文件**，内含：
 *   类型定义（8 个 interface）+ 工具函数（3）+ 小组件（5）
 *   + 主页面（1000 行） + AccountReview（504） + ProxyDrawer（254） + AccountDrawer（130）
 *
 * 本模块承载**被多个子组件共用**的部分，使各子组件可独立成文件：
 *   accounts-page.tsx（主页面，仅装配）
 *   AccountReview.tsx / ProxyDrawer.tsx / AccountDrawer.tsx（各自独立）
 *
 * ## 拆分原则
 * **纯搬移**——代码逐字节不变，仅换宿主文件；不改动任何业务逻辑
 * （映射、判据、状态机、文案全部保持）。
 */
import { type ReactNode } from "react";
import { hue } from "../../components/ui";
import { Card } from "@/components/ui/card";
import { Stat, type ToneKey } from "@/components/page/kit";
import { cn } from "@/lib/utils";

// ===== 类型定义 =====

export interface EngineInfo {
  level: string;
  label: string;
  detail: string;
}

export interface RawAccount {
  name: string;
  uid?: string;
  level?: string;
  label?: string;
  loggedIn?: boolean;
  browserDaemonPort?: number;
  recvDaemonPort?: number;
  browserDaemonAlive?: boolean;
  recvDaemonAlive?: boolean;
  wpEngine?: EngineInfo;
  dmEngine?: EngineInfo;
  /** 能力矩阵（按用途分面，`/{name}/check` 返回；与 wpEngine/dmEngine 语义不同）。 */
  capabilities?: Capabilities | null;
  isCurrent?: boolean;
  isMonitor?: boolean;
  isSender?: boolean;
  lastRun?: LastRun;
}

export interface LastRun {
  room: string;
  roomUrl: string;
  time: string;
  duration: string;
  totalRuns: number;
  comments: number;
  dmSent: number;
  dmSuccess: number;
  dmFail: number;
  dmAfterLive: number;
}

export interface FpInfo {
  name: string;
  status: string;
  os: string;
  ip: string;
  resolution: string;
  ua: string;
  proxy: string;
  lang: string;
  webrtc: string;
  timezone: string;
}

export interface FmtAccount {
  id: string;
  name: string;
  uid: string;
  hue: string;
  isValid: boolean;
  tokenValid: boolean;
  tokenExpire: string;
  lvlLabel: string;
  lvl: string;
  browserDaemonPort?: number;
  recvDaemonPort?: number;
  browserDaemonAlive: boolean;
  recvDaemonAlive: boolean;
  wpEngine: EngineInfo;
  dmEngine: EngineInfo;
  /** 能力矩阵（按用途分面）。 */
  capabilities?: Capabilities | null;
  isCurrent: boolean;
  isMonitor: boolean;
  isSender: boolean;
  lastCheck: string;
  lastRun: LastRun;
  fp: FpInfo;
}

export interface CapabilityFace {
  key: string;
  label: string;
  state: "ok" | "fail" | "unknown" | "unset" | "na";
  state_label: string;
  detail?: string;
  hint?: string;
}

/** 用途面（按**功能**汇总：凭证还能用来做什么）。 */
export interface CapabilityPurpose {
  label: string;
  usable: boolean | null;
  state: "ok" | "fail" | "unknown" | "unset";
  state_label: string;
}

/**
 * 能力矩阵（后端 `auto_dm.accounts.capability_matrix`）。
 *
 * 与 `verify` 是**两套语义**，不可互替：
 *   - `verify` = 单一「能不能用」结论（wp/dm 双引擎）；
 *   - `capabilities` = **按用途分面**（同一份 cookie 在不同服务端端点裁决独立，
 *     故会出现「wp=fail 但仍能取直播数据」的形态）。用于避免**无效重扫**。
 */
export interface Capabilities {
  caps: CapabilityFace[];
  verdicts?: Record<string, string>;
  purposes?: Record<string, CapabilityPurpose>;
  actions?: string[];
}

export interface CheckResult {
  ok?: boolean;
  error?: string;
  msg?: string;
  verify?: { wp?: EngineInfo; dm?: EngineInfo; uid?: string; auto_fix_triggered?: boolean };
  capabilities?: Capabilities | null;
}

export interface ReviewRow {
  id: number;
  time: string;
  name: string;
  lv: number;
  content: string;
  dmStatus: string;
  dmText: string;
  dmTime: string;
  ts: number;
}

export interface AcctForm {
  name: string;
  uid: string;
  cookie: string;
}

export interface ProxyForm {
  type: string;
  host: string;
  port: string;
  user: string;
  pass: string;
  testUrl: string;
}

export type PillColor = "ok" | "warn" | "danger" | "accent" | "mute";

/** 统一提取错误信息（修复原版 .catch(() => {}) 静默吞错） */
export { errMsg } from "@/lib/utils";

/** wp/dm 引擎 level → Pill 颜色（旧版 'info'/unknown 落到 mute） */
export function enginePill(level: string): PillColor {
  if (level === "ok") return "ok";
  if (level === "warn") return "warn";
  if (level === "fail" || level === "error") return "danger";
  return "mute";
}

/** 凭证状态 → Tone 色调。此前 accounts-page 与 AccountReview 各写一份逐字符相同的
 *  三元式（2026-10-05 T7 收敛），改文案/判据只需改这一处。 */
export function credentialTone(a: FmtAccount): ToneKey {
  return a.tokenValid ? "ok" : a.lvl === "nosign" ? "warn" : "danger";
}

/** 凭证状态 → 文案（后端标签优先，否则按 tokenValid 兜底）。 */
export function credentialLabel(a: FmtAccount): string {
  return a.lvlLabel || (a.tokenValid ? "凭证有效" : "凭证过期");
}

/**
 * 后端原始账号 → 前端卡片渲染对象。
 * 补齐 lastRun/fp 默认值，避免直接渲染原始对象导致渲染崩溃黑屏（原版"全部校验" bug 根因）。
 */
export function mapAcct(a: RawAccount): FmtAccount {
  const lvl = a.level || (a.loggedIn ? "ok" : "expired");
  const lvlLabel = a.label || (a.loggedIn ? "凭证有效" : "凭证过期");
  const isValid = lvl === "ok";
  return {
    // 稳定键 = 账号名（后端唯一标识：accounts.py 以 name 为键，重名直接 raise「账号已存在」）。
    // 原实现是 "ra" + i —— 列表位置序号，随轮询/刷新/排序变动，勾选集合会错位指向别的账号 ⇒ 误删。
    id: a.name,
    name: a.name,
    uid: a.uid || "—",
    hue: hue(a.name.length * 2),
    isValid,
    tokenValid: isValid,
    tokenExpire: isValid
      ? "有效"
      : lvl === "nosign"
        ? "未扫码"
        : lvl === "missing"
          ? "未配置"
          : "失效",
    lvlLabel,
    lvl,
    browserDaemonPort: a.browserDaemonPort,
    recvDaemonPort: a.recvDaemonPort,
    browserDaemonAlive: !!a.browserDaemonAlive,
    recvDaemonAlive: !!a.recvDaemonAlive,
    wpEngine: a.wpEngine || { level: "unknown", label: "未校验", detail: "" },
    dmEngine: a.dmEngine || { level: "unknown", label: "未校验", detail: "" },
    capabilities: a.capabilities ?? null,
    isCurrent: !!a.isCurrent,
    isMonitor: !!a.isMonitor,
    isSender: !!a.isSender,
    lastCheck: "刚刚",
    lastRun: a.lastRun || {
      room: "—",
      roomUrl: "",
      time: "—",
      duration: "—",
      totalRuns: 0,
      comments: 0,
      dmSent: 0,
      dmSuccess: 0,
      dmFail: 0,
      dmAfterLive: 0,
    },
    fp: {
      name: "浏览器守护 · " + a.name,
      status: "running",
      os: "Windows 11",
      ip: "—",
      resolution: "1920×1080",
      ua: "Chrome",
      proxy: "直连",
      lang: "zh-CN",
      webrtc: "替换",
      timezone: "Asia/Shanghai",
    },
  };
}

/* ── 呈现层具名单元（避免每处重复 className；表格单元与 tasks.tsx 同规格） ── */

/** 表头单元（查阅模式表格用）。 */
export function Th({ children, className }: { children?: ReactNode; className?: string }) {
  return (
    <th
      className={cn(
        "whitespace-nowrap border-b border-[var(--color-border)] px-3 py-2 text-left",
        "text-[0.7rem] font-semibold tracking-[0.03em] text-[var(--color-text-secondary)]",
        className
      )}
    >
      {children}
    </th>
  );
}

/** 表格单元（查阅模式表格用）。 */
export function Td({
  children,
  mono,
  muted,
  className,
  colSpan,
}: {
  children?: ReactNode;
  mono?: boolean;
  muted?: boolean;
  className?: string;
  colSpan?: number;
}) {
  return (
    <td
      colSpan={colSpan}
      className={cn(
        "border-b border-[var(--color-border)] px-3 py-2 align-middle",
        "text-[0.78rem] text-[var(--color-text)]",
        mono && "font-mono tabular-nums",
        muted && "text-[var(--color-text-muted)]",
        className
      )}
    >
      {children}
    </td>
  );
}

/** 顶部统计卡（旧 `.card.stat`；保留 data-od-id 锚点）。 */
export function StatCard({
  anchor,
  label,
  value,
  unit,
}: {
  anchor: string;
  label: ReactNode;
  value: ReactNode;
  unit?: ReactNode;
}) {
  return (
    <Card className="p-4" data-od-id={anchor}>
      <Stat label={label} value={value} unit={unit} />
    </Card>
  );
}

/** 区块小标题（旧 `.acct-section h4` / `.card h3`）。 */
export function SectionLabel({ children }: { children: ReactNode }) {
  return (
    <h4 className="mb-2.5 text-[0.72rem] uppercase tracking-[0.06em] text-[var(--color-text-muted)]">
      {children}
    </h4>
  );
}

/** 抽屉/弹层小标题（旧 `<h3>` 在 card 内）。 */
export function PanelTitle({ children }: { children: ReactNode }) {
  return (
    <h3 className="mb-2.5 text-[0.86rem] font-semibold tracking-tight text-[var(--color-text)]">
      {children}
    </h3>
  );
}
// ===== 主页面 =====

export const DM_META: Record<string, [string, PillColor]> = {
  un: ["未私信", "mute"],
  wait: ["待发送", "warn"],
  sent: ["已发送", "ok"],
  fail: ["发送失败", "danger"],
};
