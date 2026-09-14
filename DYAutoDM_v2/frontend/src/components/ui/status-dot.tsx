/**
 * StatusDot —— 状态指示点（本项目高频使用：连接/守护/账号状态）
 */
import { cn } from "@/lib/utils";

type Tone = "ok" | "warn" | "danger" | "muted" | "info";

const TONE: Record<Tone, string> = {
  ok: "bg-[var(--color-success)] shadow-[0_0_0_3px_var(--color-success-soft)]",
  warn: "bg-[var(--color-warning)] shadow-[0_0_0_3px_var(--color-warning-soft)]",
  danger: "bg-[var(--color-danger)] shadow-[0_0_0_3px_var(--color-danger-soft)]",
  info: "bg-[var(--color-info)] shadow-[0_0_0_3px_var(--color-info-soft)]",
  muted: "bg-[var(--color-text-muted)]",
};

export function StatusDot({
  tone = "muted",
  pulse = false,
  className,
}: {
  tone?: Tone;
  pulse?: boolean;
  className?: string;
}) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "inline-block h-[7px] w-[7px] shrink-0 rounded-full",
        TONE[tone],
        pulse && "animate-[pulse-dot_1.6s_ease-out_infinite]",
        className
      )}
    />
  );
}
