/**
 * TopBar —— 窗口拖拽区 + 运行状态 + 窗口控制
 *
 * 对标 better-douyin `components/layout/window-controls.tsx` 的思路：
 * 顶部留 36px 供拖拽；状态徽章集中在右上，不散落各页。
 */
import { Minus, Square, X } from "lucide-react";
import { StatusDot } from "@/components/ui/status-dot";

async function tauriWindow() {
  try {
    const m = await import("@tauri-apps/api/window");
    return m.getCurrentWindow();
  } catch {
    return null;
  }
}

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
                 border-b border-[var(--color-border)] px-3 select-none"
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

        <div className="ml-1 flex items-center">
          <button
            aria-label="最小化"
            onClick={async () => (await tauriWindow())?.minimize()}
            className="flex h-7 w-8 items-center justify-center rounded-[7px]
                       text-[var(--color-text-secondary)] transition-colors
                       hover:bg-[var(--color-surface-raised)] hover:text-[var(--color-text)]"
          >
            <Minus className="h-3.5 w-3.5" />
          </button>
          <button
            aria-label="最大化/还原"
            onClick={async () => (await tauriWindow())?.toggleMaximize()}
            className="flex h-7 w-8 items-center justify-center rounded-[7px]
                       text-[var(--color-text-secondary)] transition-colors
                       hover:bg-[var(--color-surface-raised)] hover:text-[var(--color-text)]"
          >
            <Square className="h-3 w-3" />
          </button>
          <button
            aria-label="关闭"
            onClick={async () => (await tauriWindow())?.close()}
            className="flex h-7 w-8 items-center justify-center rounded-[7px]
                       text-[var(--color-text-secondary)] transition-colors
                       hover:bg-[var(--color-danger)] hover:text-white"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>
    </header>
  );
}
