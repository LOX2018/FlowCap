/**
 * 通用 UI 组件 + 工具函数 —— 1:1 迁移自 DY_Spider_base/web/framework.js。
 * class 命名与旧版完全一致，样式来自 global.css。
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

export const TABS: [string, string][] = [
  ["overview", "总览"],
  ["crawl", "采集"],
  ["live", "直播监听"],
  ["msg", "私信"],
  ["accounts", "账号管理"],
  ["tasks", "任务中心"],
  ["settings", "设置"],
];

// ===== 通用组件 =====

/** 迷你折线图（SVG） */
export function Spark({ pts, color = "var(--accent)" }: { pts: number[]; color?: string }) {
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

/** 彩色标签 */
export function Pill({ c, children }: { c: "ok" | "warn" | "danger" | "accent" | "mute"; children: React.ReactNode }) {
  const map: Record<string, [string, string]> = {
    ok: ["var(--ok)", "var(--ok-bg)"],
    warn: ["var(--warn)", "var(--warn-bg)"],
    danger: ["var(--danger)", "var(--danger-bg)"],
    accent: ["var(--accent)", "var(--accent-bg)"],
    mute: ["var(--muted)", "var(--surface-2)"],
  };
  const [color, bg] = map[c] || map.mute;
  return (
    <span className="pill" style={{ color, background: bg }}>
      <span className="bullet" style={{ background: color }} />
      {children}
    </span>
  );
}

/** 头像（首字母 + 色相背景） */
export function Avatar({ name, h, sm, lg }: { name: string; h: string; sm?: boolean; lg?: boolean }) {
  return (
    <span
      className={"avatar" + (sm ? " sm" : "") + (lg ? " lg" : "")}
      style={{ background: `oklch(55% 0.14 ${h})` }}
    >
      {name.charAt(0)}
    </span>
  );
}

/** 状态点（带 pulse 动画） */
export function Dot({ c, pulse }: { c: string; pulse?: boolean }) {
  return <span className={"dot " + c + (pulse ? " pulse" : "")} />;
}

/** 等宽数字 */
export function Num({ v }: { v: React.ReactNode }) {
  return <span className="mono">{v}</span>;
}
