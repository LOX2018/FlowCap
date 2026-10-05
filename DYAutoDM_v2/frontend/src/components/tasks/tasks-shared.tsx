import type { PageProps, Overview } from "../../api/client";
import { cn } from "@/lib/utils";

export type OverviewExt = Overview & {
  liveUrl?: string;
  status?: string;
  engineState?: string;
  statusMsg?: string;
};

export interface ExportStatsResp {
  ok: boolean;
  path?: string;
  error?: string;
}
export type Api = PageProps["api"] & {
  exportStats: () => Promise<ExportStatsResp>;
};

export { errMsg } from "@/lib/utils";

/** 秒级 / 毫秒级时间戳 → 可读时间（tasks/ 域唯一实现）。
 *  2026-10-05：此前四处各写一份（tasks-page 内联 / task-detail / task-leads / Scheduler），
 *  口径虽同但复制四份，归一到此处。 */
export function fmtTs(v: unknown): string {
  if (v === null || v === undefined || v === "" || v === 0) return "—";
  const n = Number(v);
  if (!isFinite(n) || n <= 0) return String(v);
  const ms = n < 1e12 ? n * 1000 : n;
  try {
    return new Date(ms).toLocaleString("zh-CN", { hour12: false });
  } catch {
    return String(v);
  }
}

/* ── 表格具名单元（避免每处重复 className） ── */

export function Th({ children, className }: { children?: React.ReactNode; className?: string }) {
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

export function Td({
  children,
  mono,
  muted,
  className,
  colSpan,
}: {
  children?: React.ReactNode;
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
        "text-[0.76rem] text-[var(--color-text)]",
        mono && "font-mono tabular-nums",
        muted && "text-[var(--color-text-muted)]",
        className
      )}
    >
      {children}
    </td>
  );
}
