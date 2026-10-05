import type { Overview } from "../../api/client";

/** 后端 overview 实际带 liveUrl/status 字段（client.ts 精简类型未覆盖），本地扩展 */
export type OverviewExt = Overview & { liveUrl?: string; status?: string };

export interface StatsItem {
  captureTs?: string;
  status?: string;
  nickname?: string;
  comment?: string;
  content?: string;
}
export interface StatsResp {
  ok?: boolean;
  total: number;
  sent: number;
  list?: StatsItem[];
}
export interface Account {
  name: string;
  uid?: string;
  isCurrent?: boolean;
  loggedIn?: boolean;
  signReady?: boolean;
  isMonitor?: boolean;
  isSender?: boolean;
}

export interface FeedItem {
  id: number;
  t: string;
  k: string;
  n: string;
  l: number;
  x: string;
}

/** 运行进度条（旧 `.track`/`.bar`）—— 直接读设计令牌，随主题变化。 */
export function ProgressBar({ percent, active }: { percent: number; active: boolean }) {
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-[var(--color-subtle-bg)]">
      <div
        className="h-full rounded-full transition-[width] duration-[var(--duration-slow)] ease-[var(--ease-spring)]"
        style={{
          width: `${Math.max(0, Math.min(100, percent))}%`,
          background: active ? "var(--color-accent)" : "var(--color-text-muted)",
        }}
      />
    </div>
  );
}
