/**
 * 设置页子区块组件族 —— 把 `.set-card` 家族（旧 global.css 818-860 行）收敛为具名组件。
 *
 * ## 设计意图
 *
 * 设置页有 5 个子区块组件（`UnifiedConfigSection` / `AgentSection` / `TagSection` /
 * `ModelHubSection` / `NotifySection`），它们**各自复制了同一套 `.set-card` 骨架**：
 *
 *   <div className="set-card">
 *     <div className="set-card-head is-open">…</div>
 *     <div className="set-card-body">…</div>
 *     <div className="set-card-foot">…</div>
 *   </div>
 *
 * 旧 CSS 下线后，这套骨架必须收敛到一处，否则 5 个组件各写一遍新样式。
 *
 * 对标：better-douyin 的 `components/settings/*`。
 */
import * as React from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { Card } from "@/components/ui/card";

/* ═══════════════════════════════════════════════════════════════
   SetCard —— 设置卡片（可折叠，头浅体深的两层结构）
   ═══════════════════════════════════════════════════════════════ */

export function SetCard({
  children,
  className,
  ...rest
}: {
  children: React.ReactNode;
  className?: string;
} & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <Card className={cn("mb-3 overflow-hidden", className)} {...rest}>
      {children}
    </Card>
  );
}

/**
 * 卡片头。`open` 控制展开指示器旋转；`onToggle` 存在时整行可点击。
 */
export function SetCardHead({
  title,
  description,
  right,
  open,
  onToggle,
  className,
}: {
  title: React.ReactNode;
  description?: React.ReactNode;
  /** 标题右侧（状态徽章/按钮等） */
  right?: React.ReactNode;
  /** 展开态（与 onToggle 配合；不传则不显示折叠箭头） */
  open?: boolean;
  onToggle?: () => void;
  className?: string;
}) {
  const clickable = typeof onToggle === "function";
  const inner = (
    <>
      <div className="min-w-0 flex-1">
        <div className="text-[0.86rem] font-semibold tracking-tight text-[var(--color-text)]">
          {title}
        </div>
        {description ? (
          <div className="mt-0.5 text-[0.72rem] leading-relaxed text-[var(--color-text-muted)]">
            {description}
          </div>
        ) : null}
      </div>
      {right ? <div className="flex shrink-0 items-center gap-2">{right}</div> : null}
      {clickable ? (
        <ChevronDown
          className={cn(
            "h-4 w-4 shrink-0 text-[var(--color-text-muted)]",
            "transition-transform duration-[var(--duration-base)] ease-[var(--ease-spring)]",
            open && "rotate-180"
          )}
        />
      ) : null}
    </>
  );

  const base = cn(
    "flex items-center gap-2.5 border-b border-[var(--color-border)] px-3.5 py-2.75",
    className
  );

  if (!clickable) return <div className={base}>{inner}</div>;

  return (
    <button
      type="button"
      aria-expanded={!!open}
      onClick={onToggle}
      className={cn(
        base,
        "w-full cursor-pointer text-left transition-colors duration-[var(--duration-fast)]",
        "hover:bg-[var(--color-surface-raised)]"
      )}
    >
      {inner}
    </button>
  );
}

/** 卡片体：比卡头更深一层，形成「头浅体深」的层次（对标旧 .set-card-body）。 */
export function SetCardBody({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "bg-[color-mix(in_srgb,var(--color-background)_40%,var(--color-surface-solid))] p-3.5",
        className
      )}
    >
      {children}
    </div>
  );
}

/** 卡片脚（操作按钮区）。 */
export function SetCardFoot({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-2 border-t border-[var(--color-border)] px-3.5 py-2.5",
        className
      )}
    >
      {children}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════
   SetField —— 字段气泡（旧 `.set-field`：独立边框 + 背景）
   ═══════════════════════════════════════════════════════════════ */

export function SetField({
  label,
  hint,
  children,
  className,
}: {
  label?: React.ReactNode;
  hint?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <label
      className={cn(
        "mb-2.5 block rounded-[var(--radius-sm)] border border-[var(--color-border)]",
        "bg-[var(--color-surface)] px-3 py-2.5",
        "transition-colors duration-[var(--duration-fast)]",
        "hover:border-[var(--color-border-strong)]",
        "focus-within:border-[var(--color-accent)]",
        className
      )}
    >
      {label ? (
        <span className="mb-1 block text-[0.74rem] font-medium text-[var(--color-text)]">
          {label}
        </span>
      ) : null}
      {children}
      {hint ? (
        <span className="mt-1 block text-[0.68rem] leading-relaxed text-[var(--color-text-muted)]">
          {hint}
        </span>
      ) : null}
    </label>
  );
}
