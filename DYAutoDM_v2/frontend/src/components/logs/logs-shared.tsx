
export interface LogLine {
  ts: string;
  level: string;
  text: string;
}

export interface Session {
  file: string;
  start: string;
  size: number;
  mtime: number;
}

/** 级别 → 语义令牌（深色/浅色主题自动适配）。 */
export const LEVEL_CLASS: Record<string, string> = {
  INFO: "text-[var(--color-text-muted)]",
  DEBUG: "text-[var(--color-text-muted)]",
  WARNING: "text-[var(--color-warning)]",
  ERROR: "text-[var(--color-danger)]",
  SUCCESS: "text-[var(--color-success)]",
};

/** 将 20260815_123045 格式化为 2026-08-15 12:30:45 */
export function fmtStart(start: string): string {
  if (start.length >= 15) {
    const d = start.slice(0, 8);
    const t = start.slice(9);
    return `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)} ${t.slice(0, 2)}:${t.slice(2, 4)}:${t.slice(4, 6)}`;
  }
  return start;
}

export function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
