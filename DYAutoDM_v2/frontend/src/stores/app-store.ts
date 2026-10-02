/**
 * 应用视图状态 —— 照源项目 better-douyin 的 `stores/app-store.ts`
 *
 * ## 设计来源（照源项目）
 *
 * 源项目 better-douyin 前端为 **components-first** 架构，视图切换不用 URL 路由，
 * 而是**单一视图枚举 + zustand store**：
 *
 * ```typescript
 * // 源项目 stores/app-store.ts（逆向/源码实测）
 * type ViewType = "home"|"search"|"user"|"recommended"|"downloads"
 *               | "liked"|"collected"|"notices"|"friends-status"
 *               | "automation"|"settings";
 * currentView: "home",
 * setView: (view: ViewType) => set({ currentView: view }),
 * ```
 *
 * ## 本项目（design/better-douyin 阶段2）
 *
 * - `ViewType` = **源项目 11 视图** 的超集：保留本项目独有视图
 *   （`live` 直播 / `kb` 知识库 / `crawl` 采集 / `platform` 内容 /
 *    `accounts` 账号 / `tasks` 任务 / `notify` 通知 / `logs` 日志），
 *   与源项目的 `home`/`search`/`user`/`recommended`/`downloads`/`liked`/
 *     `collected`/`notices`/`friends-status`/`automation`/`settings` **并列**。
 * - 视图切换行为**完全等价于原 `App.tsx` 的 `useState<TabId>` +
 *   `localStorage["dy:tab"]`**（含持久化键不变，故用户现有选择不丢）。
 *
 * ## 兼容策略（零回归的关键）
 *
 * 迁移期**保留** `TabId`（`components/layout/sidebar.tsx`）作为现有视图的
 * 子集类型，`ViewType` 为其超集。`App.tsx` 改用 store 后，
 * `setTab` 的**对外签名与持久化语义不变**，故：
 *   · 侧栏 / TopBar / 页面间的 `setTab("msg")` 等调用**零改动**
 *   · 子页面从 props 拿到的 `setTab` 类型仍兼容
 */
import { create } from "zustand";
// ★ ADR-034（2026-10-03）：视图集合改为**派生自 sidebar 的 TabId**（单一真源），
//   消除「两份 SSOT 漂移」——旧副本漏了 stats 却被 `setView` 当白名单 ⇒ 点统计回落总览。
import type { TabId } from "@/components/layout/sidebar";

/** 源项目 better-douyin 的视图集合（照抄，供对照与未来对齐）。 */
export const SOURCE_VIEWS = [
  "home", "search", "user", "recommended", "downloads",
  "liked", "collected", "notices", "friends-status",
  "automation", "settings",
] as const;

/**
 * 本项目视图集合 = **sidebar 的 TabId**（单一真源）。
 *
 * 🔴 2026-10-03（ADR-034 缺陷修复）：此前本文件**另立一份**视图枚举
 * （含已撤的 `crawl`、缺新增的 `stats`），而 `readStoredView()` 与
 * `setView()` 都拿 `VIEW_TITLE` 当运行时白名单 ⇒ 点「统计」时 `stats`
 * 不在白名单 ⇒ **被静默回落成 `DEFAULT_VIEW`（总览）**。
 * 实测症状：点击导航「统计」后仍显示总览，统计页永远打不开。
 *
 * 根因不是漏加一个键，而是**同一份「有哪些视图」维护在两处**：
 * `components/layout/sidebar.tsx::TabId`（导航真源）与本文件（旧副本）。
 * 修法不是把 `stats` 补进来（那样下次再加页又会漏），而是**让本文件
 * 直接派生自 TabId** ⇒ 增删导航项时本文件自动跟随，不可能再漂。
 */
export type ViewType = TabId;

/** 视图持久化键（与原 App.tsx 一致，**不可改**，否则用户选择丢失）。 */
export const VIEW_STORAGE_KEY = "dy:tab";

/** 默认视图（与原 App.tsx 一致）。 */
export const DEFAULT_VIEW: ViewType = "overview";

/** 各视图标题（TopBar 左侧显示；原 App.tsx `TITLE_OF` 迁入）。 */
export const VIEW_TITLE: Record<ViewType, string> = {
  overview: "总览",
  msg: "私信",
  live: "直播",
  // ★ ADR-034（2026-10-03）：「采集」不再是独立视图（已并入 platform 的 tab），
  //   故从本表移除（它是 `Record<ViewType,…>` 的穷举键，多一个键即类型错误）。
  platform: "内容",
  kb: "知识库",
  // ★ ADR-034：统计页为独立只读看板（非总览的下游），必须有独立标题，
  //   否则 `TITLE_OF` 会因 `hasOwnProperty` 落空而回落「控制台」。
  stats: "统计",
  accounts: "账号",
  tasks: "任务",
  notify: "通知",
  logs: "日志",
  settings: "设置",
};

/**
 * 从 localStorage 读取初始视图（与原实现行为兼容，但增加白名单校验）。
 *
 * 2026-09-17 修补（OCR 审查 MEDIUM）：原实现 `return v || DEFAULT_VIEW`
 * 直接信任持久值。若 localStorage 被写成 ViewType 之外的脏值
 * （如旧版本残留的 "automation"，或手工改过），App.tsx 会 `as TabId`
 * 断言后进入渲染，`VIEW_TITLE[tab]` 得到 undefined ⇒ **标题空白、
 * 侧栏无选中项，且没有任何入口能切回去（界面卡死）**。
 * 现加运行时白名单：不在 VIEW_TITLE 里的值一律回落默认视图。
 */
export function readStoredView(): ViewType {
  try {
    const v = localStorage.getItem(VIEW_STORAGE_KEY);
    if (v && Object.prototype.hasOwnProperty.call(VIEW_TITLE, v)) {
      return v as ViewType;
    }
    return DEFAULT_VIEW;
  } catch {
    return DEFAULT_VIEW;
  }
}

interface AppViewState {
  currentView: ViewType;
  /** 切换视图（含持久化）。签名与原 `setTab(t: string)` 兼容。 */
  setView: (v: string) => void;
}

/**
 * 视图 store（照源项目 `useAppStore` 的 `currentView` / `setView`）。
 *
 * 用法与原 `useState<TabId>` 等价：
 * ```tsx
 * const { currentView, setView } = useAppViewStore();
 * ```
 */
export const useAppViewStore = create<AppViewState>((set) => ({
  currentView: readStoredView(),
  setView: (v: string) => {
    // 2026-09-17 修补（OCR 审查 MEDIUM）：写入端同样做白名单校验，
    // 避免脏值落盘后在下次启动时触发上面的卡死场景。
    const view: ViewType =
      v && Object.prototype.hasOwnProperty.call(VIEW_TITLE, v)
        ? (v as ViewType)
        : DEFAULT_VIEW;
    set({ currentView: view });
    try {
      localStorage.setItem(VIEW_STORAGE_KEY, view);
    } catch {
      /* ignore（隐私模式等） */
    }
  },
}));
