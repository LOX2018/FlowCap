/**
 * AppShell —— 布局骨架（对标 better-douyin `components/layout/app-shell.tsx`）
 *
 * 结构：
 *   ┌ TopBar（拖拽 + 状态 + 窗口控制）─────────────┐
 *   │ Sidebar │  内容区（懒加载 + 切换动画）      │
 *   └─────────┴──────────────────────────────────┘
 *
 * 差异：蓝本用底部 BottomBar 承载播放器/下载队列；本项目无播放器业务，
 * 改为「内容区 + 可选底栏插槽」，保持结构同源但不引入空壳。
 */
import { Suspense, type ReactNode } from "react";
// 2026-10-02：页面切换已移出 framer-motion（见 <main> 内注释），故不再引入动画运行时。
import { Sidebar, type TabId } from "./sidebar";
import { TopBar } from "./topbar";
import { Skeleton } from "@/components/ui/skeleton";

function ViewFallback() {
  return (
    <div className="space-y-3 p-5">
      <Skeleton className="h-8 w-48" />
      <Skeleton className="h-24 w-full" />
      <Skeleton className="h-24 w-full" />
    </div>
  );
}

export function AppShell({
  tab,
  setTab,
  title,
  connected,
  memberName,
  topRight,
  sidebarFooter,
  children,
}: {
  tab: TabId;
  setTab: (t: TabId) => void;
  title?: string;
  connected?: boolean;
  memberName?: string | null;
  topRight?: ReactNode;
  sidebarFooter?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="flex h-screen w-screen overflow-hidden">
      {/* 背景氛围层：跟随主题/主色的柔光 + 细网格，内容浮于其上 */}
      <div className="app-atmosphere" aria-hidden="true" />
      <Sidebar tab={tab} setTab={setTab} footer={sidebarFooter} />
      <div className="relative z-10 flex min-w-0 flex-1 flex-col">
        <TopBar
          connected={connected}
          memberName={memberName}
          left={
            title ? (
              <h1 className="truncate text-[0.88rem] font-semibold tracking-tight
                             text-[var(--color-text)]">
                {title}
              </h1>
            ) : null
          }
          right={topRight}
        />
        <main className="min-h-0 flex-1 overflow-y-auto">
          {/*
           * 2026-10-02 修（用户实测「切换页面时明显闪烁跳跃」）——**两次根因**：
           *  ① `AnimatePresence mode="wait"` 会先等旧页淡出、再挂载新页 ⇒ 中间一段空档 = 闪。
           *  ② framer-motion 的 `y` 位移产生 transform ⇒ 成为 `position:fixed` 后代的
           *     包含块，页面内浮层在切换期间被困在容器内（见下）。
           * 现**彻底移出 framer-motion**：用 keyed div + 极短 CSS 淡入
           * （opacity .6→1，不产生 transform、无空档、旧页立即卸载）。
           * 页面**宽度已统一**（PageContainer 全站 1180px），故不再有横向跳跃。
           */}
          <div key={tab} className="page-in min-h-full">
            <Suspense fallback={<ViewFallback />}>{children}</Suspense>
          </div>
        </main>
      </div>
    </div>
  );
}

/**
 * 页面容器：统一内边距与最大宽度（各页复用，避免样式漂移）。
 *
 * ## 最大宽度取值依据（2026-09-14 实测修正）
 *
 * 原值 1680px 对用户的 **1536×864** 屏幕而言**大于内容区可用宽**（1536 − 240 侧栏 − 滚动条 ≈ 1286px），
 * 于是 `maxWidth` 形同失效、容器永远占满父级 → `margin-inline:auto` 恒解析为 0
 * → 观感上「内容紧贴侧栏、铺到右边缘，没有居中」。
 *
 * 修正为 **1180px**：在用户的 1536 屏上左右各留出约 53px 的视觉留白，
 * 在大屏上则产生明显的居中留白；同时仍宽于任何表格页所需的列宽（不会挤压列）。
 *
 * ⚠️ 若某个页面内容确实需要更宽（列很多的表格），**显式传 maxWidth** 覆盖，不要改默认值。
 */
export function PageContainer({
  children,
  className = "",
  /**
   * ⚠️ 2026-10-02 起：**不要再按页覆盖 maxWidth**（用户实测「每页内容初始位置不同、
   * 切页横向跳跃」）。原先 kb=1080 / notify=860 与其它页的 1180 不一致 ⇒ 内容块左边缘
   * 逐页偏移。现全站统一 1180px（此参数仅为**遗留兼容**保留，默认即可）。
   */
  maxWidth = "1180px",
}: {
  children: ReactNode;
  className?: string;
  maxWidth?: string;
}) {
  return (
    <div
      className={"mx-auto w-full px-5 py-4 " + className}
      style={{ maxWidth }}
    >
      {children}
    </div>
  );
}

/** 页头：标题 + 说明 + 右侧动作（各页统一）。 */
export function PageHeader({
  title,
  description,
  actions,
}: {
  title: string;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
      <div className="min-w-0">
        <h2 className="text-[1.05rem] font-semibold tracking-tight text-[var(--color-text)]">
          {title}
        </h2>
        {description ? (
          <p className="mt-0.5 text-[0.8rem] text-[var(--color-text-secondary)]">
            {description}
          </p>
        ) : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  );
}
