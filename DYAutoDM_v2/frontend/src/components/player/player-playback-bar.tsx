/**
 * 播放器 —— 播放条（进度 / 时间 / 播放暂停）
 *
 * ## 设计来源（照源项目 better-douyin）
 *
 * 对标 `frontend/src/components/player/player-playback-bar.tsx`。
 */
import { useEffect, useRef, useState } from "react";
import { fmtDuration } from "./player-utils";

interface Props {
  /** 播放器元素（用于读取/设置 currentTime） */
  getMediaEl: () => HTMLVideoElement | null;
  playing: boolean;
  onTogglePlay: () => void;
  /** 媒体变化时重置进度的信号（如 aweme_id） */
  resetKey?: string;
}

export function PlayerPlaybackBar({ getMediaEl, playing, onTogglePlay, resetKey }: Props) {
  const barRef = useRef<HTMLDivElement | null>(null);
  const [cur, setCur] = useState(0);
  const [dur, setDur] = useState(0);
  const dragging = useRef(false);

  useEffect(() => {
    let raf = 0;
    const tick = () => {
      const v = getMediaEl();
      if (v && !dragging.current) {
        setCur(v.currentTime || 0);
        setDur(Number.isFinite(v.duration) ? v.duration : 0);
      }
      raf = window.requestAnimationFrame(tick);
    };
    raf = window.requestAnimationFrame(tick);
    return () => window.cancelAnimationFrame(raf);
  }, [getMediaEl]);

  useEffect(() => {
    setCur(0);
    setDur(0);
  }, [resetKey]);

  const seek = (clientX: number) => {
    const el = barRef.current;
    const v = getMediaEl();
    if (!el || !v || !dur) return;
    const r = el.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (clientX - r.left) / r.width));
    v.currentTime = ratio * dur;
    setCur(ratio * dur);
  };

  const ratio = dur > 0 ? (cur / dur) * 100 : 0;

  return (
    <div className="flex items-center gap-2 px-3 py-2">
      <button
        type="button"
        onClick={onTogglePlay}
        className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full
                   bg-white/15 text-white hover:bg-white/25"
        aria-label={playing ? "暂停" : "播放"}
      >
        {playing ? "❚❚" : "▶"}
      </button>
      <span className="shrink-0 font-mono text-[0.7rem] tabular-nums text-white/80">
        {fmtDuration(cur)}
      </span>
      <div
        ref={barRef}
        className="group relative h-4 flex-1 cursor-pointer"
        onPointerDown={(e) => {
          dragging.current = true;
          seek(e.clientX);
          (e.target as HTMLElement).setPointerCapture?.(e.pointerId);
        }}
        onPointerMove={(e) => dragging.current && seek(e.clientX)}
        onPointerUp={() => { dragging.current = false; }}
      >
        <div className="absolute top-1/2 h-1 w-full -translate-y-1/2 rounded bg-white/20" />
        <div
          className="absolute top-1/2 h-1 -translate-y-1/2 rounded bg-[var(--accent,#22d3ee)]"
          style={{ width: `${ratio}%` }}
        />
        <div
          className="absolute top-1/2 h-2.5 w-2.5 -translate-x-1/2 -translate-y-1/2 rounded-full
                     bg-white opacity-0 transition-opacity group-hover:opacity-100"
          style={{ left: `${ratio}%` }}
        />
      </div>
      <span className="shrink-0 font-mono text-[0.7rem] tabular-nums text-white/60">
        {fmtDuration(dur)}
      </span>
    </div>
  );
}
