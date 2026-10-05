/**
 * TopBar —— 窗口拖拽区 + 运行状态 + 窗口控制（自绘标题栏）
 *
 * 对标 better-douyin `components/layout/window-controls.tsx` 的思路：
 * 顶部留拖拽区；状态徽章集中在右上，不散落各页。
 *
 * ## 2026-10-02 改（用户实测「顶部外壳也没有和主题适配」）
 *
 * 根因：`tauri.conf.json` 未设 `decorations:false` ⇒ Windows **原生标题栏**
 * （深色）叠在应用**自绘顶栏**（跟主题、浅色）之上 ⇒
 *   ① 明暗割裂（深色系统条 + 浅色应用区）；
 *   ② 顶部出现**两组**窗口控制按钮（系统一组 + 本组件的三个按钮）。
 *
 * 修法：`tauri.conf.json` 关掉系统装饰（`decorations: false`），本组件成为
 * **唯一**标题栏 —— 顶栏背景走设计令牌（昼/夜自动跟随），不再由系统着色。
 *
 * ## 2026-10-02 二次改（用户实测「浮窗模式下顶栏被遮住，没法最小化/最大化/关闭」）
 *
 * 窗口控制按钮若留在本组件内，会被 `z` 更高的浮层（弹窗 100 / 全屏视图 120）
 * **盖住** ⇒ 浮窗期间无法操作窗口。故三个按钮**已抽到常驻层**
 * `<WindowControls/>`（App 根部，`z-index: --z-blocker(200)`，恒在浮层之上）。
 * 本组件只保留右侧同宽**占位**，避免状态徽章与常驻按钮重叠。
 *
 * 无边框窗口不再有系统级拖拽条，故顶栏整高都声明 `data-tauri-drag-region`。
 */
import { StatusDot } from "@/components/ui/status-dot";

export function TopBar({
  connected,
  memberName,
  left,
  right,
}: {
  connected?: boolean;
  memberName?: string | null;
  left?: React.ReactNode;
  right?: React.ReactNode;
}) {
  return (
    <header
      data-tauri-drag-region
      className="relative z-30 flex h-[var(--topbar-height)] shrink-0 items-center gap-3
                 border-b border-[var(--color-border)] px-3 select-none
                 bg-[color-mix(in_srgb,var(--color-background-soft)_85%,transparent)]
                 backdrop-blur-2xl"
    >
      {/* 左：页面标题/面包屑 */}
      <div data-tauri-drag-region className="flex min-w-0 flex-1 items-center gap-2">
        {left}
      </div>

      {/* 右：状态 + 自定义动作 + 窗口控制 */}
      <div className="flex shrink-0 items-center gap-2">
        <span
          className="flex items-center gap-1.5 rounded-full border border-[var(--color-border)]
                     px-2.5 py-[3px] text-[0.72rem] text-[var(--color-text-secondary)]"
          title={connected ? "后端连接正常" : "后端未连接"}
        >
          <StatusDot tone={connected ? "ok" : "muted"} pulse={!connected} />
          {connected ? "已连接" : "未连接"}
        </span>

        {memberName ? (
          <span className="rounded-full bg-[var(--color-accent-soft)] px-2.5 py-[3px]
                           text-[0.72rem] font-medium text-[var(--color-accent)]">
            {memberName}
          </span>
        ) : null}

        {right}

        {/* 2026-10-02：窗口控制按钮已抽成**常驻层** <WindowControls/>（App 根部），
            以免被浮层遮住而无法最小化/最大化/关闭。此处只留同宽占位，避免右侧
            状态徽章与常驻按钮重叠。 */}
        <div className="ml-1 w-[120px] shrink-0" aria-hidden="true" />
      </div>
    </header>
  );
}