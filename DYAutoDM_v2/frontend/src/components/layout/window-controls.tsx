/**
 * WindowControls —— 常驻最顶层的窗口控制（最小化 / 最大化↔还原 / 关闭）
 *
 * ## 为什么必须常驻（2026-10-02 用户实测）
 * 无边框窗口（`decorations:false`）后，窗口控制按钮**只存在于应用自绘顶栏**里，
 * 而顶栏在 `--z-chrome(30)`；任何浮层（弹窗 `100` / 全屏视图 `120` / 遮罩）都会
 * **盖住顶栏** ⇒ 用户在全屏浮层里**无法最小化/最大化/关闭窗口**（只能先退出浮层）。
 *
 * 修法：把这三个按钮抽成**独立常驻层**，`z-index` 取 `--z-blocker(200)`
 * ——高于所有业务浮层（≤120），低于 toast(300)。位置固定在右上角，宽高与顶栏一致，
 * 视觉上仍像「标题栏右侧」，但**永远可点**。
 *
 * 另：`fullscreen`（查阅模式等不透明全屏视图）会在自身 header 右侧预留同宽占位
 * （见 `--wc-w`），避免与按钮重叠。
 */
import { useEffect, useState } from "react";
import { Minus, Square, Copy, X } from "lucide-react";
import { cn } from "@/lib/utils";

async function tauriWindow() {
  try {
    const m = await import("@tauri-apps/api/window");
    return m.getCurrentWindow();
  } catch {
    return null;
  }
}

export function WindowControls({ className }: { className?: string }) {
  const [maximized, setMaximized] = useState(false);

  useEffect(() => {
    let dispose: (() => void) | undefined;
    let alive = true;
    (async () => {
      const w = await tauriWindow();
      if (!w || !alive) return;
      try {
        setMaximized(await w.isMaximized());
        dispose = await w.onResized(async () => {
          if (alive) setMaximized(await w.isMaximized());
        });
      } catch {
        /* 非 Tauri 环境（纯浏览器调试）忽略 */
      }
    })();
    return () => {
      alive = false;
      dispose?.();
    };
  }, []);

  const btn =
    "flex h-[var(--topbar-height)] w-10 items-center justify-center " +
    "text-[var(--color-text-secondary)] transition-colors " +
    "hover:bg-[var(--color-surface-raised)] hover:text-[var(--color-text)]";

  return (
    <div
      className={cn("fixed right-0 top-0 z-[var(--z-blocker)] flex items-center", className)}
      // 这些按钮不能吃拖拽（否则点不动）
      style={{ WebkitAppRegion: "no-drag" } as React.CSSProperties}
    >
      <button
        aria-label="最小化"
        title="最小化"
        onClick={async () => (await tauriWindow())?.minimize()}
        className={btn}
      >
        <Minus className="h-3.5 w-3.5" />
      </button>
      <button
        aria-label={maximized ? "还原" : "最大化"}
        title={maximized ? "还原" : "最大化"}
        onClick={async () => (await tauriWindow())?.toggleMaximize()}
        className={btn}
      >
        {maximized ? <Copy className="h-3 w-3" /> : <Square className="h-3 w-3" />}
      </button>
      <button
        aria-label="关闭"
        title="关闭"
        onClick={async () => (await tauriWindow())?.close()}
        className={cn(btn, "hover:bg-[var(--color-danger)] hover:text-white")}
      >
        <X className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}
