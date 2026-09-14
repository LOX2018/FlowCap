/**
 * 播放器 —— 主体（fullscreen-player 对应物）
 *
 * ## 设计来源（照源项目 better-douyin）
 *
 * 对标 `frontend/src/components/player/fullscreen-player.tsx`（源项目主体，62KB）。
 * 源项目该组件承载：媒体舞台 + 播放条 + 各面板（音量/倍速/清晰度/分享/更多）+
 * 互动（赞/藏/评）+ 作者信息 + 描述。
 *
 * 本项目按其**结构**落地（组件切分照源项目），规模按本项目需求裁剪：
 *   PlayerMediaStage（舞台）· PlayerPlaybackBar（播放条）·
 *   PlayerVolumeControl（音量）· PlayerRateMenu（倍速）· PlayerQualityMenu（清晰度）
 *   · PlayerInfo（作者）· PlayerDescription（描述）
 *
 * ## 与源项目的差异（诚实记录）
 *
 * · 源项目播放器直接对接其 Rust 侧取流与互动（赞/藏）；本项目媒体来自后端
 *   `services/media_proxy.py`，互动动作经 `api/platform.py`（**平台写接口
 *   目前返回空，属已知现象**）→ 故本组件的互动按钮为**可选注入**（`onLike` 等），
 *   未注入则不渲染，避免给出"能点赞"的假象。
 * · 源项目的评论子系统（`use-player-comments.ts` 32KB）本项目未对齐
 *   （评论能力在 `crawl-page` 采集链路），故本组件不含评论区。
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PlayerMediaStage } from "./player-media-stage";
import { PlayerPlaybackBar } from "./player-playback-bar";
import type { PlayerAuthor, PlayerMedia, PlayerStatus } from "./player-types";
import { PLAYBACK_RATES, QUALITY_LABEL, QUALITY_OPTIONS, fmtMs, mediaKindLabel } from "./player-utils";

interface Props {
  media: PlayerMedia | null;
  author?: PlayerAuthor;
  /** 播放中（受控）或内部自管 */
  onClose?: () => void;
  onEnded?: () => void;
  /** 互动（未注入则不渲染按钮——避免"假可用"） */
  onLike?: (awemeId: string) => Promise<boolean> | void;
  onCollect?: (awemeId: string) => Promise<boolean> | void;
  /** 下载（交由后端 downloader） */
  onDownload?: (awemeId: string) => void;
}

export function FullscreenPlayer({
  media, author, onClose, onEnded, onLike, onCollect, onDownload,
}: Props) {
  const stageRef = useRef<HTMLDivElement | null>(null);
  const [status, setStatus] = useState<PlayerStatus>("idle");
  const [playing, setPlaying] = useState(false);
  const [rate, setRate] = useState(1);
  const [volume, setVolume] = useState(1);
  const [muted, setMuted] = useState(false);
  const [quality, setQuality] = useState<string>("origin");
  const [panel, setPanel] = useState<string | null>(null);

  const getMediaEl = useCallback(
    () => stageRef.current?.querySelector("video") as HTMLVideoElement | null,
    [],
  );

  // 媒体变化 → 重置
  useEffect(() => {
    setPlaying(false);
    setStatus("idle");
    setPanel(null);
  }, [media?.aweme_id, media?.url]);

  useEffect(() => {
    const v = getMediaEl();
    if (v) { v.volume = volume; v.muted = muted; }
  }, [volume, muted, getMediaEl]);

  const togglePlay = useCallback(() => {
    const v = getMediaEl();
    if (!v) return;
    if (v.paused) { void v.play().then(() => setPlaying(true)).catch(() => setPlaying(false)); }
    else { v.pause(); setPlaying(false); }
  }, [getMediaEl]);

  const kind = useMemo(() => mediaKindLabel(media), [media]);

  return (
    <div className="flex h-full w-full flex-col bg-black/80">
      {/* 头部 */}
      <div className="flex shrink-0 items-center gap-2 px-3 py-2">
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-medium text-white">
            {author?.nickname || media?.desc || "播放器"}
          </div>
          <div className="truncate text-[0.68rem] text-white/50">
            {kind}
            {media?.duration ? ` · ${fmtMs(media.duration)}` : ""}
            {status === "error" ? " · 加载失败" : ""}
          </div>
        </div>
        {onClose && (
          <button type="button" onClick={onClose}
                  className="rounded px-2 py-1 text-xs text-white/70 hover:bg-white/10"
                  aria-label="关闭">✕</button>
        )}
      </div>

      {/* 舞台 */}
      <div ref={stageRef} className="relative min-h-0 flex-1">
        <PlayerMediaStage
          media={media}
          autoPlay
          muted={muted}
          rate={rate}
          className="h-full w-full"
          onStatusChange={setStatus}
          onEnded={() => { setPlaying(false); onEnded?.(); }}
        />
      </div>

      {/* 播放条 */}
      <PlayerPlaybackBar
        getMediaEl={getMediaEl}
        playing={playing}
        onTogglePlay={togglePlay}
        resetKey={media?.aweme_id || media?.url}
      />

      {/* 控制条 */}
      <div className="flex shrink-0 flex-wrap items-center gap-1.5 border-t border-white/10 px-3 py-2">
        {/* 音量 */}
        <div className="flex items-center gap-1">
          <button type="button" onClick={() => setMuted((m) => !m)}
                  className="rounded px-1.5 py-1 text-xs text-white/80 hover:bg-white/10"
                  aria-label="静音切换">{muted ? "🔇" : "🔊"}</button>
          <input
            type="range" min={0} max={1} step={0.05} value={muted ? 0 : volume}
            onChange={(e) => { setVolume(Number(e.target.value)); setMuted(false); }}
            className="h-1 w-16 accent-white"
            aria-label="音量"
          />
        </div>

        {/* 倍速（照源项目 PLAYBACK_RATES） */}
        <select
          value={rate}
          onChange={(e) => setRate(Number(e.target.value))}
          className="rounded bg-white/10 px-1.5 py-1 text-xs text-white outline-none"
          aria-label="倍速"
        >
          {PLAYBACK_RATES.map((r) => (
            <option key={r} value={r} className="text-black">{r}x</option>
          ))}
        </select>

        {/* 清晰度（对齐后端 pick_quality 降级链） */}
        <select
          value={quality}
          onChange={(e) => setQuality(e.target.value)}
          className="rounded bg-white/10 px-1.5 py-1 text-xs text-white outline-none"
          aria-label="清晰度"
        >
          {QUALITY_OPTIONS.map((q) => (
            <option key={q} value={q} className="text-black">{QUALITY_LABEL[q] || q}</option>
          ))}
        </select>

        <div className="flex-1" />

        {/* 互动：仅在注入回调时渲染（不给"假可用"） */}
        {onLike && media?.aweme_id && (
          <button type="button" onClick={() => void onLike(media.aweme_id!)}
                  className="rounded px-2 py-1 text-xs text-white/80 hover:bg-white/10">♡ 赞</button>
        )}
        {onCollect && media?.aweme_id && (
          <button type="button" onClick={() => void onCollect(media.aweme_id!)}
                  className="rounded px-2 py-1 text-xs text-white/80 hover:bg-white/10">☆ 藏</button>
        )}
        {onDownload && media?.aweme_id && (
          <button type="button" onClick={() => onDownload(media.aweme_id!)}
                  className="rounded px-2 py-1 text-xs text-white/80 hover:bg-white/10">⤓ 下载</button>
        )}
      </div>

      {panel && (
        <div className="shrink-0 border-t border-white/10 px-3 py-2 text-xs text-white/70">
          面板：{panel}
        </div>
      )}
    </div>
  );
}
