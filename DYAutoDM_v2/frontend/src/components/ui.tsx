/**
 * 通用 UI 组件 + 工具函数（重设计版 · 无旧 CSS 依赖）
 *
 * ## 迁移背景
 * 原版本 class 命名依赖 `global.css`（`.avatar` / `.pill` / `.bullet` / `.dot` / `.mono`）。
 * 本次重设计后旧 CSS 体系下线，故样式**内联为设计令牌**，导出签名保持不变
 * （6 个页面以 `Avatar` / `hue` / `KIND_NAME` / `tick` 等形式引用，签名不动即零回归）。
 *
 * ## 与 `components/page/kit` 的关系
 * - `Tone`（kit）= 旧 `Pill` 的新形态（同为状态胶囊）；新代码请用 `Tone`
 * - 本文件的 `Pill` 仅保留给尚未迁移的调用点（NotifySection 等），保持兼容
 */
import React from "react";

// ===== 工具函数 =====

/** 格式化时间 HH:MM:SS，off 为毫秒偏移 */
export function tick(off = 0): string {
  const d = new Date(Date.now() + off);
  const p = (n: number) => (n < 10 ? "0" : "") + n;
  return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
}

/** 当前时分 HH:MM */
export function nowHM(): string {
  const d = new Date();
  const p = (n: number) => (n < 10 ? "0" : "") + n;
  return p(d.getHours()) + ":" + p(d.getMinutes());
}

/** 数组随机取一 */
export function pick<T>(arr: T[]): T {
  return arr[Math.floor(Math.random() * arr.length)];
}

/** 头像色相轮转 */
const HUES = ["150", "210", "300", "20", "40", "80", "260", "180"];
export function hue(i: number): string {
  return HUES[i % HUES.length];
}

// ===== 常量 =====

export const KIND_NAME: Record<string, string> = {
  danmaku: "弹幕",
  gift: "礼物",
  enter: "进场",
  follow: "关注",
  like: "点赞",
};

/** @deprecated 导航已由 `components/layout/sidebar.tsx` 接管（含 kb/notify）。仅少数旧调用点仍引用。 */
export const TABS: [string, string][] = [
  ["overview", "总览"],
  ["crawl", "采集"],
  ["live", "直播监听"],
  ["msg", "私信"],
  ["ai", "AI"],
  ["accounts", "账号管理"],
  ["tasks", "任务中心"],
  ["settings", "设置"],
  ["logs", "运行日志"],
];

// ===== 通用组件 =====

/** 迷你折线图（SVG）。颜色走设计令牌，深浅主题自动适配。 */
export function Spark({ pts, color = "var(--color-accent)" }: { pts: number[]; color?: string }) {
  const w = 104,
    h = 30;
  const max = Math.max(...pts),
    min = Math.min(...pts);
  const x = (i: number) => (i / (pts.length - 1)) * w;
  const y = (v: number) => h - 3 - ((v - min) / (max - min || 1)) * (h - 6);
  const d = pts
    .map((v, i) => (i ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1))
    .join("");
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden="true">
      <path d={`${d} L${w} ${h} L0 ${h} Z`} fill={color} opacity=".12" />
      <path d={d} fill="none" stroke={color} strokeWidth="1.6" />
      <circle cx={x(pts.length - 1)} cy={y(pts[pts.length - 1])} r="2.4" fill={color} />
    </svg>
  );
}

/** 彩色标签（旧 `Pill` 形态）。新代码请优先用 `kit` 的 `Tone`。 */
export function Pill({
  c,
  children,
}: {
  c: "ok" | "warn" | "danger" | "accent" | "mute";
  children: React.ReactNode;
}) {
  const map: Record<string, [string, string]> = {
    ok: ["var(--color-success)", "var(--color-success-soft)"],
    warn: ["var(--color-warning)", "var(--color-warning-soft)"],
    danger: ["var(--color-danger)", "var(--color-danger-soft)"],
    accent: ["var(--color-accent)", "var(--color-accent-soft)"],
    mute: ["var(--color-text-secondary)", "var(--color-surface-raised)"],
  };
  const [color, bg] = map[c] || map.mute;
  return (
    <span
      className="inline-flex items-center gap-1 rounded-full px-2.5 py-0.5
                 text-[0.72rem] font-semibold"
      style={{ color, background: bg }}
    >
      <span
        className="inline-block h-[6px] w-[6px] shrink-0 rounded-full"
        style={{ background: color }}
      />
      {children}
    </span>
  );
}

/** 头像（首字母 + 色相背景）。色相值由 `hue()` 提供。 */
export function Avatar({
  name,
  h,
  sm,
  lg,
  src,
}: {
  name: string;
  h: string;
  sm?: boolean;
  lg?: boolean;
  src?: string;
}) {
  const size = sm ? "h-6 w-6 text-[0.68rem]" : lg ? "h-10 w-10 text-[0.95rem]" : "h-8 w-8 text-[0.8rem]";
  return (
    <span
      className={
        "inline-flex shrink-0 items-center justify-center overflow-hidden rounded-full " +
        "font-semibold text-white " + size
      }
      style={{ background: `oklch(55% 0.14 ${h})` }}
    >
      {src ? (
        <img
          src={src}
          alt={name}
          className="h-full w-full rounded-full object-cover"
        />
      ) : (
        name.charAt(0)
      )}
    </span>
  );
}

/** 状态点。旧 `Dot c="ok|warn|danger|mute|pulse"` → 语义映射到令牌色。 */
export function Dot({ c, pulse }: { c: string; pulse?: boolean }) {
  const tone = c.includes("ok")
    ? "var(--color-success)"
    : c.includes("warn")
      ? "var(--color-warning)"
      : c.includes("danger")
        ? "var(--color-danger)"
        : c.includes("accent")
          ? "var(--color-accent)"
          : "var(--color-text-muted)";
  return (
    <span
      aria-hidden="true"
      className={
        "inline-block h-[7px] w-[7px] shrink-0 rounded-full " +
        (pulse ? "animate-[pulse-dot_1.6s_ease-out_infinite]" : "")
      }
      style={{ background: tone }}
    />
  );
}

/** 等宽数字 */
export function Num({ v }: { v: React.ReactNode }) {
  return <span className="font-mono tabular-nums">{v}</span>;
}
