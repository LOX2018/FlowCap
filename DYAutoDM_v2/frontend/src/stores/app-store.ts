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

/** 源项目 better-douyin 的视图集合（照抄，供对照与未来对齐）。 */
export const SOURCE_VIEWS = [
  "home", "search", "user", "recommended", "downloads",
  "liked", "collected", "notices", "friends-status",
  "automation", "settings",
] as const;

/** 本项目当前视图集合（= 源项目 11 视图的**超集**，独有视图并列保留）。 */
export type ViewType =
  // ── 本项目独有（源项目无对应，按「只对齐结构、保留独有能力」契约保留）──
  | "overview"     // 总览
  | "msg"          // 私信
  | "live"         // 直播
  | "crawl"        // 采集
  | "platform"     // 内容
  | "kb"           // 知识库
  | "accounts"     // 账号
  | "tasks"        // 任务
  | "notify"       // 通知
  | "logs"         // 日志
  // ── 与源项目重合 ──
  | "settings";    // 设置／配置中心

/** 视图持久化键（与原 App.tsx 一致，**不可改**，否则用户选择丢失）。 */
export const VIEW_STORAGE_KEY = "dy:tab";

/** 默认视图（与原 App.tsx 一致）。 */
export const DEFAULT_VIEW: ViewType = "overview";

/** 各视图标题（TopBar 左侧显示；原 App.tsx `TITLE_OF` 迁入）。 */
export const VIEW_TITLE: Record<ViewType, string> = {
  overview: "总览",
  msg: "私信",
  live: "直播",
  crawl: "采集",
  platform: "内容",
  kb: "知识库",
  accounts: "账号",
  tasks: "任务",
  notify: "通知",
  logs: "日志",
  settings: "设置",
};

/** 从 localStorage 读取初始视图（与原实现逐字一致）。 */
export function readStoredView(): ViewType {
  try {
    const v = localStorage.getItem(VIEW_STORAGE_KEY) as ViewType | null;
    return v || DEFAULT_VIEW;
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
    const view = v as ViewType;
    set({ currentView: view });
    try {
      localStorage.setItem(VIEW_STORAGE_KEY, view);
    } catch {
      /* ignore（隐私模式等） */
    }
  },
}));
