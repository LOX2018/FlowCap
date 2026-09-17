import { useState } from "react";
import { useRef } from "react";
import { useEffect } from "react";
import { MediaInfo } from "./message-shared";
import { Button } from "@/components/ui/button";
import { AuthedImg } from "@/components/ui/authed-img";

export function ImageViewer({
  media,
  onClose,
}: {
  media: MediaInfo | null;
  onClose: () => void;
}) {
  // 2026-08-31：缩放/拖拽状态。
  // inline_pic 实测只有 160px 级（最大边 73~356px），弹层里放大看会糊，
  // 用户需要能自己缩放查看细节。滚轮缩放 + 按住拖动。
  const [scale, setScale] = useState(1);
  const [drag, setDrag] = useState({ x: 0, y: 0 });
  const dragging = useRef<{ x: number; y: number } | null>(null);
  // 弹层根节点（供原生 wheel 监听使用；语义与原 `.imgviewer` 选择器一致）
  const rootRef = useRef<HTMLDivElement | null>(null);

  // 换图时重置视图
  useEffect(() => {
    setScale(1);
    setDrag({ x: 0, y: 0 });
  }, [media]);

  // 2026-09-03:原生 wheel 监听阻止事件冒泡到页面背后(合成事件 preventDefault 不够)
  // ⚠️ 必须放在 early return 之前,否则 hooks 数量不一致导致 React 崩溃
  useEffect(() => {
    const stop = (e: Event) => {
      e.preventDefault();
      e.stopPropagation();
    };
    const el = rootRef.current;
    if (el) {
      el.addEventListener("wheel", stop, { passive: false });
      return () => el.removeEventListener("wheel", stop);
    }
  }, []);

  if (!media) return null;

  const onWheel = (e: React.WheelEvent) => {
    e.preventDefault();
    setScale((s) => Math.min(8, Math.max(0.3, s - e.deltaY * 0.0015)));
  };
  const onDown = (e: React.MouseEvent) => {
    dragging.current = { x: e.clientX - drag.x, y: e.clientY - drag.y };
  };
  const onMove = (e: React.MouseEvent) => {
    if (!dragging.current) return;
    setDrag({
      x: e.clientX - dragging.current.x,
      y: e.clientY - dragging.current.y,
    });
  };
  const onUp = () => {
    dragging.current = null;
  };

  return (
    <div
      ref={rootRef}
      className="fixed inset-0 z-[999] flex items-center justify-center bg-black/55 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="flex max-h-[90vh] max-w-[65vw] flex-col overflow-hidden
                   rounded-[var(--radius-md)] border border-[var(--color-border)]
                   bg-[var(--color-surface-solid)] shadow-[var(--shadow-lg)]"
        onClick={(e) => e.stopPropagation()}
      >
        <div
          className="flex items-center justify-between gap-4 border-b
                     border-[var(--color-border)] px-3 py-2 text-[0.78rem]
                     text-[var(--color-text-secondary)]"
        >
          <span className="flex items-center gap-2">
            图片
            {scale !== 1 && (
              <span
                className="ml-2 rounded-full border border-[var(--color-border)]
                           bg-[var(--color-surface)] px-2 py-0.5 font-mono
                           text-[0.7rem] tabular-nums text-[var(--color-text-muted)]"
              >
                {Math.round(scale * 100)}%
              </span>
            )}
          </span>
          <div className="flex items-center gap-1.5">
            <Button
              variant="ghost"
              size="sm"
              className="min-w-[34px] px-2"
              onClick={() => setScale((s) => Math.max(0.3, s / 1.25))}
            >
              −
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="min-w-[34px] px-2"
              onClick={() => setScale(1)}
            >
              重置
            </Button>
            <Button
              variant="ghost"
              size="sm"
              className="min-w-[34px] px-2"
              onClick={() => setScale((s) => Math.min(8, s * 1.25))}
            >
              ＋
            </Button>
            <Button variant="ghost" size="sm" onClick={onClose}>
              关闭
            </Button>
          </div>
        </div>
        <div
          className="flex select-none items-center justify-center overflow-hidden p-3.5"
          onWheel={onWheel}
          onMouseDown={onDown}
          onMouseMove={onMove}
          onMouseUp={onUp}
          onMouseLeave={onUp}
        >
          {/* 2026-08-31 实测：抖音 IM 消息里的 inline_pic 本身就是缩略图
              （典型 160×213），消息体内**不含全尺寸原图**；
              resource_url 的远程链是私有加密，真机实测浏览器加载失败
              （3/3 error，0×0）。因此弹层只能放大显示已有的缩略图。 */}
          {media.inline ? (
            <AuthedImg
              src={media.thumb}
              alt="预览"
              className="h-auto w-auto rounded-[var(--radius-sm)]"
              draggable={false}
              style={{
                transform: `translate(${drag.x}px, ${drag.y}px) scale(${scale})`,
                cursor: dragging.current ? "grabbing" : "grab",
                maxWidth: scale === 1 ? "65%" : "none",
                maxHeight: scale === 1 ? "68vh" : "none",
              }}
            />
          ) : (
            <div
              className="px-5 py-5 text-center text-[0.78rem] leading-[1.7]
                         text-[var(--color-text-secondary)]"
            >
              该图为抖音私有加密格式，无法在内嵌预览中显示。
              <br />
              请在浏览器中打开查看。
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

