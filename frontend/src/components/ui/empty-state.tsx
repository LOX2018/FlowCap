/**
 * EmptyState / PageState —— 对标 better-douyin `components/common/page-state.tsx`
 */
import type { ReactNode } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "./button";

export function LoadingState({ label = "加载中…", className }: { label?: string; className?: string }) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-3 py-16", className)}>
      <Loader2 className="h-6 w-6 animate-spin text-[var(--color-accent)]" />
      <span className="text-[0.82rem] text-[var(--color-text-secondary)]">{label}</span>
    </div>
  );
}

export function EmptyState({
  icon,
  title,
  description,
  action,
  className,
}: {
  icon?: ReactNode;
  title: string;
  description?: string;
  action?: { label: string; onClick: () => void };
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-3 py-16 text-center", className)}>
      {icon ? <div className="text-[var(--color-text-muted)]">{icon}</div> : null}
      <div className="text-[0.9rem] font-medium text-[var(--color-text)]">{title}</div>
      {description ? (
        <div className="max-w-sm text-[0.8rem] leading-relaxed text-[var(--color-text-secondary)]">
          {description}
        </div>
      ) : null}
      {action ? (
        <Button variant="secondary" size="sm" onClick={action.onClick}>
          {action.label}
        </Button>
      ) : null}
    </div>
  );
}

export function ErrorState({
  message,
  onRetry,
  className,
}: {
  message: string;
  onRetry?: () => void;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-center justify-center gap-3 py-16 text-center", className)}>
      <div className="text-[0.9rem] font-medium text-[var(--color-danger)]">出错了</div>
      <div className="max-w-md text-[0.8rem] leading-relaxed text-[var(--color-text-secondary)]">
        {message}
      </div>
      {onRetry ? (
        <Button variant="secondary" size="sm" onClick={onRetry}>
          重试
        </Button>
      ) : null}
    </div>
  );
}
