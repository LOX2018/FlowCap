/**
 * Sidebar —— 对标 better-douyin `components/layout/sidebar.tsx`
 *
 * 差异：导航项按**本项目业务**编排为三组（核心 / 内容 / 系统），
 * 而非照抄蓝本的 11 项（本项目无下载/点赞/收藏业务）。
 */
import { useState } from "react";
import {
  LayoutDashboard, MessageSquare, Radio, Search, Bot, BookOpen,
  Users, ListChecks, Bell, ScrollText, Settings, PanelLeftClose,
  PanelLeftOpen, ChevronDown,
} from "lucide-react";
import { cn } from "@/lib/utils";

export type TabId =
  | "overview" | "msg" | "live" | "crawl" | "ai" | "kb"
  | "accounts" | "tasks" | "notify" | "logs" | "settings";

type NavItem = { id: TabId; label: string; icon: React.ElementType };

type NavGroup = { title: string; items: NavItem[] };

/** 三组编排：核心（日常高频）/ 内容（获客链路）/ 系统（配置与运维）。 */
export const NAV_GROUPS: NavGroup[] = [
  {
    title: "核心",
    items: [
      { id: "overview", label: "总览", icon: LayoutDashboard },
      { id: "msg", label: "私信", icon: MessageSquare },
      { id: "live", label: "直播", icon: Radio },
    ],
  },
  {
    title: "内容",
    items: [
      { id: "crawl", label: "采集", icon: Search },
      { id: "ai", label: "AI 获客", icon: Bot },
      { id: "kb", label: "知识库", icon: BookOpen },
    ],
  },
  {
    title: "系统",
    items: [
      { id: "accounts", label: "账号", icon: Users },
      { id: "tasks", label: "任务", icon: ListChecks },
      { id: "notify", label: "通知", icon: Bell },
      { id: "logs", label: "日志", icon: ScrollText },
      { id: "settings", label: "设置", icon: Settings },
    ],
  },
];

export function Sidebar({
  tab,
  setTab,
  brand = "DYAutoDM",
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
      <div className="flex h-[52px] shrink-0 items-center gap-2.5 px-3.5">
        <span
          aria-hidden="true"
          className="h-7 w-7 shrink-0 rounded-[9px] bg-[var(--color-accent)]
                     shadow-[0_0_18px_-2px_color-mix(in_srgb,var(--color-accent)_55%,transparent)]"
        />
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

      {/* 底部：状态 + 收起按钮 */}
      <div className="shrink-0 border-t border-[var(--color-border)] p-2">
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
