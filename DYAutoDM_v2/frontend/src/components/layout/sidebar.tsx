/**
 * Sidebar —— 对标 better-douyin `components/layout/sidebar.tsx`
 *
 * 差异：导航项按**用户使用逻辑**编排为三组（任务 / 资产 / 记录），
 * 而非照抄蓝本的 11 项（本项目无下载/点赞/收藏业务）。
 *
 * ## 分组命名依据（2026-09-14 信息架构改版）
 *
 * 旧分组名（核心/内容/系统）是**系统名词**，用户要自己翻译成"我要做什么"；
 * 且「内容」既是组名又是项名，自相撞车。改为**动词性心智**：
 *   · 任务 —— 我在跑的活（总览/私信/直播/采集）
 *   · 资产 —— 我积累的（账号/内容/知识库）
 *   · 记录 —— 发生过什么（任务/通知/日志）
 *
 * 「配置中心」不再是组内一项，而是**底部独立入口**（全局唯一可写配置入口）。
 */
import { useState } from "react";
import {
  LayoutDashboard, MessageSquare, Radio, Search, BookOpen,
  Users, ListChecks, Bell, ScrollText, Settings, PanelLeftClose,
  PanelLeftOpen, ChevronDown, BarChart3, Layers,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { BrandMark } from "@/components/brand";

export type TabId =
  | "overview" | "msg" | "live" | "live-batch" | "platform" | "kb" | "stats"
  | "accounts" | "tasks" | "notify" | "logs" | "settings";

type NavItem = { id: TabId; label: string; icon: React.ElementType };

type NavGroup = { title: string; items: NavItem[] };

/** 三组编排：任务（我在跑的活）/ 资产（我积累的）/ 记录（发生过什么）。 */
export const NAV_GROUPS: NavGroup[] = [
  {
    title: "任务",
    items: [
      { id: "overview", label: "总览", icon: LayoutDashboard },
      { id: "msg", label: "私信", icon: MessageSquare },
      { id: "live", label: "直播", icon: Radio },
      { id: "live-batch", label: "批量采集", icon: Layers },
      // ★ ADR-034（2026-10-03，方向反转 ADR-033）：「采集」导航项已撤除 ——
      //   采集功能融进「内容」页，成为它的一个二级 tab。
      //   独立采集页会造成「两处都能搜作品/看作品」的双入口心智。
      { id: "platform", label: "内容", icon: Search },
    ],
  },
  {
    title: "资产",
    items: [
      { id: "accounts", label: "账号", icon: Users },
      { id: "stats", label: "统计", icon: BarChart3 },
      { id: "kb", label: "知识库", icon: BookOpen },
    ],
  },
  {
    title: "记录",
    items: [
      { id: "tasks", label: "任务", icon: ListChecks },
      { id: "notify", label: "通知", icon: Bell },
      { id: "logs", label: "日志", icon: ScrollText },
    ],
  },
];

export function Sidebar({
  tab,
  setTab,
  brand = "川流",
  footer,
}: {
  tab: TabId;
  setTab: (t: TabId) => void;
  brand?: string;
  footer?: React.ReactNode;
}) {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem("dy.sidebar.collapsed") === "1";
    } catch {
      return false;
    }
  });

  const toggle = () => {
    const next = !collapsed;
    setCollapsed(next);
    try {
      localStorage.setItem("dy.sidebar.collapsed", next ? "1" : "0");
    } catch {
      /* localStorage 不可用则忽略 */
    }
  };

  return (
    <aside
      className={cn(
        "relative z-20 flex h-full shrink-0 flex-col",
        "border-r border-[var(--color-border)]",
        "bg-[color-mix(in_srgb,var(--color-background-soft)_85%,transparent)] backdrop-blur-2xl",
        "transition-[width] duration-300 ease-[var(--ease-spring)]"
      )}
      style={{ width: collapsed ? "var(--sidebar-collapsed)" : "var(--sidebar-width)" }}
    >
      {/* 品牌区 */}
      {/* px 随收起态缩放：collapsed 宽 41px，方块 28px 固定 ⇒ padding 必须 ≤6px 才不溢出 */}
      <div className={cn("flex h-[52px] shrink-0 items-center gap-2.5", collapsed ? "px-1.5" : "px-3.5")}>
        <span
                  aria-hidden="true"
                  className="shrink-0"
                >
                  <BrandMark size={28} rounded={9} />
                </span>
                {!collapsed && (
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-[0.86rem] font-semibold tracking-tight text-[var(--color-text)]">
                      {brand}
                    </div>
            <div className="truncate text-[0.66rem] uppercase tracking-[0.08em] text-[var(--color-text-muted)]">
              控制台
            </div>
          </div>
        )}
      </div>

      {/* 导航（分组） */}
      <nav className="no-scrollbar flex-1 overflow-y-auto px-2 pb-3" aria-label="主导航">
        {NAV_GROUPS.map((g) => (
          <div key={g.title} className="mb-1.5">
            {!collapsed ? (
              <div className="flex items-center gap-1 px-2.5 pb-1 pt-2.5">
                <ChevronDown className="h-3 w-3 text-[var(--color-text-muted)]" />
                <span className="text-[0.66rem] font-semibold uppercase tracking-[0.1em] text-[var(--color-text-muted)]">
                  {g.title}
                </span>
              </div>
            ) : (
              <div className="mx-auto my-2 h-[1px] w-6 bg-[var(--color-border)]" />
            )}
            {g.items.map((it) => {
              const Icon = it.icon;
              const active = tab === it.id;
              return (
                <button
                  key={it.id}
                  onClick={() => setTab(it.id)}
                  title={collapsed ? it.label : undefined}
                  aria-label={it.label}
                  aria-current={active ? "page" : undefined}
                  className={cn(
                    "group relative flex w-full items-center gap-2.5 rounded-[10px]",
                    "px-2.5 py-2 text-[0.82rem] transition-all duration-200",
                    "ease-[var(--ease-spring)]",
                    active
                      ? "bg-[var(--color-accent-soft)] font-semibold text-[var(--color-accent)]"
                      : "text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-raised)] hover:text-[var(--color-text)]",
                    collapsed && "justify-center px-0"
                  )}
                >
                  {active && (
                    <span
                      aria-hidden="true"
                      className="absolute left-0 top-1/2 h-4 w-[2.5px] -translate-y-1/2
                                 rounded-r-full bg-[var(--color-accent)]"
                    />
                  )}
                  <Icon className="h-[17px] w-[17px] shrink-0" />
                  {!collapsed && <span className="truncate">{it.label}</span>}
                </button>
              );
            })}
          </div>
        ))}
      </nav>

      {/* 底部：配置中心（唯一可写配置入口）+ 状态 + 收起按钮 */}
      <div className="shrink-0 border-t border-[var(--color-border)] p-2">
        {/* 配置中心：独立入口，视觉区别于业务导航项（用户 2026-09-14 拍板 D1） */}
        <button
          onClick={() => setTab("settings")}
          title="配置中心 · 全局唯一可写配置入口"
          aria-label="配置中心"
          aria-current={tab === "settings" ? "page" : undefined}
          className={cn(
            "mb-1.5 flex w-full items-center gap-2.5 rounded-[10px] px-2.5 py-2",
            "text-[0.82rem] font-medium transition-colors duration-200",
            "ease-[var(--ease-spring)]",
            tab === "settings"
              ? "bg-[var(--color-accent-soft)] font-semibold text-[var(--color-accent)]"
              : "text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-raised)] hover:text-[var(--color-text)]",
            "shadow-[inset_0_0_0_1px_var(--color-border)]",
            collapsed && "justify-center px-0"
          )}
        >
          <Settings className="h-[17px] w-[17px] shrink-0" />
          {!collapsed && <span className="truncate">配置中心</span>}
        </button>

        {!collapsed && footer ? <div className="mb-1.5">{footer}</div> : null}
        <button
          onClick={toggle}
          aria-label={collapsed ? "展开侧边栏" : "收起侧边栏"}
          className={cn(
            "flex w-full items-center gap-2 rounded-[9px] px-2.5 py-1.5",
            "text-[0.76rem] text-[var(--color-text-muted)] transition-colors",
            "hover:bg-[var(--color-surface-raised)] hover:text-[var(--color-text)]",
            collapsed && "justify-center px-0"
          )}
        >
          {collapsed ? (
            <PanelLeftOpen className="h-4 w-4" />
          ) : (
            <>
              <PanelLeftClose className="h-4 w-4" />
              <span>收起</span>
            </>
          )}
        </button>
      </div>
    </aside>
  );
}
