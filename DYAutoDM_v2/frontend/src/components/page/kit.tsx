/**
 * 页面级组件族 —— 把 11 个页面反复出现的模式收敛为一处。
 *
 * ## 设计意图
 *
 * 旧 `global.css` 用类名（`.section-head` / `.card` / `.stat` / `.acct-row` /
 * `.head-row` / `.bubble` / `.blank` …）拼装页面，导致：
 *   ① 类名分散、每页各写一遍；
 *   ② 无法随设计令牌升级；
 *   ③ 深浅主题要各写一套色值。
 *
 * 本文件把这些模式**收敛为具名组件**，全部走 `tokens.css` 的设计令牌，
 * 因此天然支持深浅主题、统一缓动与圆角，且页面只需组合组件。
 *
 * 对标：better-douyin 的 `components/page/*` + `components/layout/*`。
 */
import * as React from "react";
import { cn } from "@/lib/utils";
import { ChevronDown } from "lucide-react";
import { Card, CardHeader, CardContent } from "@/components/ui/card";
import { Badge, type BadgeProps } from "@/components/ui/badge";

/* ═══════════════════════════════════════════════════════════════
   区块：Section（旧 `.card` + `.section-head`）
   ═══════════════════════════════════════════════════════════════ */

/**
 * 页面区块。旧写法：
 *   <div className="card"><h3>标题</h3>…</div>
 *   <div className="section-head"><h2>标题</h2><span className="desc">…</span></div>
 * 新写法：
 *   <Section title="标题" description="说明" actions={<Button/>}>…</Section>
 */
export function Section({
  title,
  description,
  actions,
  children,
  className,
  bare = false,
  ...rest
}: {
  title?: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  /** bare=true 时不套 Card 外壳（用于自管容器的场景） */
  bare?: boolean;
} & React.HTMLAttributes<HTMLElement>) {
  const head = (title || description || actions) ? (
    <div className="flex w-full flex-wrap items-center justify-between gap-3">
      <div className="min-w-0">
        {title ? (
          <h3 className="text-[0.9rem] font-semibold tracking-tight text-[var(--color-text)]">
            {title}
          </h3>
        ) : null}
        {description ? (
          <p className="mt-0.5 text-[0.78rem] text-[var(--color-text-secondary)]">{description}</p>
        ) : null}
      </div>
      {/* ml-auto：头部 flex-wrap 换行时（标题过长 / 动作过多），justify-between 会把
          独占一行的动作组放回**左端**（实测「账号切换段没靠右」即此因）；
          ml-auto 保证动作组在任何情况下都贴右，且同排时行为不变。 */}
      {actions ? <div className="ml-auto flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  ) : null;

  if (bare) {
    return (
      <section className={cn("min-w-0", className)} {...rest}>
        {head}
        <div className={head ? "mt-3" : ""}>{children}</div>
      </section>
    );
  }

  return (
    <Card className={cn("min-w-0", className)} {...rest}>
      {head ? <CardHeader>{head}</CardHeader> : null}
      <CardContent className={head ? "" : "p-4"}>{children}</CardContent>
    </Card>
  );
}

/* ═══════════════════════════════════════════════════════════════
   统计块：Stat（旧 `.stat` / `.num` / `.label` / `.unit` / `.delta`）
   ═══════════════════════════════════════════════════════════════ */

export function Stat({
  label,
  value,
  unit,
  delta,
  deltaDown,
  accent,
  icon,
  className,
}: {
  label: React.ReactNode;
  value: React.ReactNode;
  unit?: React.ReactNode;
  delta?: React.ReactNode;
  deltaDown?: boolean;
  /** 高亮主色（用于核心指标） */
  accent?: boolean;
  icon?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <div className="flex items-center gap-1.5 text-[0.72rem] tracking-[0.04em]
                      text-[var(--color-text-secondary)]">
        {icon}
        {label}
      </div>
      <div className="flex items-baseline gap-1">
        <span
          className={cn(
            "font-mono text-[1.6rem] font-semibold leading-none tracking-tight tabular-nums",
            accent ? "text-[var(--color-accent)]" : "text-[var(--color-text)]"
          )}
        >
          {value}
        </span>
        {unit ? (
          <span className="text-[0.78rem] text-[var(--color-text-muted)]">{unit}</span>
        ) : null}
        {delta ? (
          <span
            className={cn(
              "ml-1 font-mono text-[0.72rem]",
              deltaDown ? "text-[var(--color-danger)]" : "text-[var(--color-success)]"
            )}
          >
            {delta}
          </span>
        ) : null}
      </div>
    </div>
  );
}

/** 一排统计块（响应式栅格）。 */
export function StatRow({
  children,
  cols = 4,
  className,
}: {
  children: React.ReactNode;
  cols?: 2 | 3 | 4 | 5;
  className?: string;
}) {
  const map = {
    2: "grid-cols-2",
    3: "grid-cols-2 md:grid-cols-3",
    4: "grid-cols-2 md:grid-cols-4",
    5: "grid-cols-2 md:grid-cols-5",
  } as const;
  return <div className={cn("grid gap-4", map[cols], className)}>{children}</div>;
}

/* ═══════════════════════════════════════════════════════════════
   列表行：Row（旧 `.acct-row` / `.head-row`）
   ═══════════════════════════════════════════════════════════════ */

export function Row({
  children,
  className,
  onClick,
  active,
}: {
  children: React.ReactNode;
  className?: string;
  onClick?: () => void;
  active?: boolean;
}) {
  return (
    <div
      onClick={onClick}
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      onKeyDown={
        onClick
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onClick();
              }
            }
          : undefined
      }
      className={cn(
        "flex items-center gap-3 rounded-[var(--radius-sm)] px-3 py-2.5",
        "border border-transparent transition-colors duration-200",
        onClick && "cursor-pointer hover:bg-[var(--color-surface-raised)]",
        active && "border-[var(--color-border)] bg-[var(--color-surface-raised)]",
        className
      )}
    >
      {children}
    </div>
  );
}

/** 行内主副文本（左主右副）。 */
export function RowText({
  primary,
  secondary,
  mono,
  className,
}: {
  primary: React.ReactNode;
  secondary?: React.ReactNode;
  mono?: boolean;
  className?: string;
}) {
  return (
    <div className={cn("min-w-0 flex-1", className)}>
      <div
        className={cn(
          "truncate text-[0.82rem] text-[var(--color-text)]",
          mono && "font-mono tabular-nums"
        )}
      >
        {primary}
      </div>
      {secondary ? (
        <div className="mt-0.5 truncate text-[0.72rem] text-[var(--color-text-muted)]">
          {secondary}
        </div>
      ) : null}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════
   数据网格：KeyValue（旧 `.v` / `.k` / `.field` / `.label`）
   ═══════════════════════════════════════════════════════════════ */

export function KeyValue({
  items,
  cols = 2,
  className,
}: {
  items: { k: React.ReactNode; v: React.ReactNode; mono?: boolean }[];
  cols?: 1 | 2 | 3;
  className?: string;
}) {
  const map = { 1: "grid-cols-1", 2: "grid-cols-2", 3: "grid-cols-3" } as const;
  return (
    <div className={cn("grid gap-x-6 gap-y-2.5", map[cols], className)}>
      {items.map((it, i) => (
        <div key={i} className="min-w-0">
          <div className="text-[0.7rem] tracking-[0.03em] text-[var(--color-text-muted)]">
            {it.k}
          </div>
          <div
            className={cn(
              "mt-0.5 truncate text-[0.8rem] text-[var(--color-text)]",
              it.mono && "font-mono tabular-nums"
            )}
          >
            {it.v || "—"}
          </div>
        </div>
      ))}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════
   状态标签：沿用 Badge，但给业务语义一个便捷映射
   ═══════════════════════════════════════════════════════════════ */

export type ToneKey =
  | "ok" | "warn" | "danger" | "accent" | "mute" | "info";

const TONE_MAP: Record<ToneKey, BadgeProps["variant"]> = {
  ok: "success",
  warn: "warning",
  danger: "danger",
  accent: "accent",
  mute: "outline",
  info: "info",
};

/** 状态胶囊（旧 `.Pill c="ok|warn|danger|accent|mute"`）。 */
export function Tone({ tone = "mute", children }: { tone?: ToneKey; children: React.ReactNode }) {
  return <Badge variant={TONE_MAP[tone]}>{children}</Badge>;
}

/* ═══════════════════════════════════════════════════════════════
   骨架：Skeleton 行（旧 `.sk`）
   ═══════════════════════════════════════════════════════════════ */

export function SkeletonRows({ rows = 4, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn("space-y-2", className)}>
      {Array.from({ length: rows }).map((_, i) => (
        <div
          key={i}
          className="h-10 animate-pulse rounded-[var(--radius-sm)] bg-[var(--color-surface-raised)]"
        />
      ))}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════
   空态占位（旧 `.blank`）
   ═══════════════════════════════════════════════════════════════ */

export function Blank({
  children,
  className,
}: {
  children?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-1 py-10 text-center",
        "text-[0.8rem] text-[var(--color-text-muted)]",
        className
      )}
    >
      {children ?? "暂无内容"}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════
   页签组：SegmentedTabs（替代旧 `.btn.ghost` 手搓切换）
   ═══════════════════════════════════════════════════════════════ */

export function SegmentedTabs<T extends string>({
  value,
  onChange,
  items,
  className,
}: {
  value: T;
  onChange: (v: T) => void;
  items: { value: T; label: React.ReactNode; icon?: React.ReactNode }[];
  className?: string;
}) {
  return (
    <div
      className={cn(
        "inline-flex items-center gap-1 rounded-[var(--radius-md)] p-1",
        "border border-[var(--color-border)] bg-[var(--color-surface)]",
        className
      )}
    >
      {items.map((it) => {
        const on = it.value === value;
        return (
          <button
            key={it.value}
            type="button"
            onClick={() => onChange(it.value)}
            className={cn(
              "inline-flex items-center gap-1.5 rounded-[var(--radius-sm)] px-3 py-1.5",
              "text-[0.78rem] font-medium transition-colors duration-200",
              on
                ? "bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
                : "text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-raised)] hover:text-[var(--color-text)]"
            )}
          >
            {it.icon}
            {it.label}
          </button>
        );
      })}
    </div>
  );
}

/* ═══════════════════════════════════════════════════════════════
   工具栏：Toolbar（旧 `.head-row` + 右侧按钮组）
   ═══════════════════════════════════════════════════════════════ */

export function Toolbar({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-wrap items-center gap-2", className)}>{children}</div>
  );
}

/* ═══════════════════════════════════════════════════════════════
   折叠区块：Collapse（旧各页自写的 Collapsible）
   ═══════════════════════════════════════════════════════════════ */

/**
 * 可折叠卡片。旧实现散落在 notify.tsx / ai.tsx / settings.tsx，各写一遍。
 * 收敛到此处后，三个页面共用同一折叠语义与视觉。
 *
 * ## 2026-09-28 修：补**受控模式**（`open` + `onOpenChange`）
 * 原实现只有 **非受控** `defaultOpen`（`useState(defaultOpen)`），而 `defaultOpen`
 * **只在首次挂载时生效** ⇒ 任何「外部事件要求展开」的场景都失效。
 * 实测缺陷（用户报「对话回复库编辑按钮点了没反应」）：
 * `kb/reply-kb.tsx` 用 `defaultOpen={editId !== null}` 表达「点编辑 → 展开」，
 * 但该折叠块在页面挂载时就已渲染完毕（首次 editId=null ⇒ open=false），
 * 之后点「编辑」只改了 `editId`，`defaultOpen` 的**新值被 React 忽略** ⇒
 * 面板不展开、输入框不出现，用户观感即「点了没反应」。
 * 现补标准受控语义：传 `open` 即为受控（由调用方驱动），不传则保持原非受控行为。
 */
export function Collapse({
  title,
  subtitle,
  defaultOpen = false,
  open: openProp,
  onOpenChange,
  right,
  footer,
  children,
}: {
  title: React.ReactNode;
  subtitle?: React.ReactNode;
  defaultOpen?: boolean;
  /** 受控展开态；传入即为受控模式（`defaultOpen` 忽略） */
  open?: boolean;
  /** 受控模式下的展开态变更回调（用户点标题栏时触发） */
  onOpenChange?: (open: boolean) => void;
  /** 标题栏右侧附加内容（状态点/徽章等） */
  right?: React.ReactNode;
  footer?: React.ReactNode;
  children: React.ReactNode;
}) {
  const [openState, setOpenState] = React.useState(defaultOpen);
  const isControlled = openProp !== undefined;
  const open = isControlled ? !!openProp : openState;
  const setOpen = (next: boolean) => {
    if (!isControlled) setOpenState(next);
    onOpenChange?.(next);
  };
  return (
    <Card className="mb-2.5 overflow-hidden">
      <button
        type="button"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className={cn(
          "flex w-full cursor-pointer items-center gap-2.5 px-3.5 py-3 text-left",
          "transition-colors duration-[var(--duration-fast)] ease-[var(--ease-spring)]",
          "hover:bg-[var(--color-surface-raised)]"
        )}
      >
        <span className="text-[0.86rem] font-semibold text-[var(--color-text)]">{title}</span>
        {subtitle ? (
          <span className="text-[0.72rem] text-[var(--color-text-muted)]">{subtitle}</span>
        ) : null}
        <span className="ml-auto flex shrink-0 items-center gap-2">
          {right}
          <ChevronDown
            className={cn(
              "h-4 w-4 text-[var(--color-text-muted)]",
              "transition-transform duration-[var(--duration-base)] ease-[var(--ease-spring)]",
              open && "rotate-180"
            )}
          />
        </span>
      </button>
      {open && (
        <CardContent className="px-3.5 pb-3.5 pt-0">
          {children}
          {footer ? <div className="mt-3">{footer}</div> : null}
        </CardContent>
      )}
    </Card>
  );
}

/* ═══════════════════════════════════════════════════════════════
   表单域：FormField（label + 控件 + hint）
   ═══════════════════════════════════════════════════════════════ */

export function FormField({
  label,
  hint,
  secret,
  children,
  className,
}: {
  label: React.ReactNode;
  hint?: React.ReactNode;
  secret?: boolean;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <label className={cn("mb-2.5 block", className)}>
      <span className="mb-1 block text-[0.72rem] text-[var(--color-text)]">
        {label}
        {secret ? (
          <span className="ml-1.5 text-[var(--color-text-muted)]">（敏感）</span>
        ) : null}
      </span>
      {children}
      {hint ? (
        <span className="mt-1 block text-[0.68rem] leading-relaxed text-[var(--color-text-muted)]">
          {hint}
        </span>
      ) : null}
    </label>
  );
}
