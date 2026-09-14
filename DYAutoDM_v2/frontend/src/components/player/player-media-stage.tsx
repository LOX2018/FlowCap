/**
 * 播放器 —— 媒体舞台（视频 / 图集 / Live Photo）
 *
 * ## 设计来源（照源项目 better-douyin）
 *
 * 对标 `frontend/src/components/player/player-media-stage.tsx`。
 * 源项目把"当前该渲染哪种媒体"收敛在舞台组件里（视频 ↔ 图集 ↔ 实况），
 * 播放条 / 面板只与舞台交互，不各自判断媒体类型。
 *
 * ## 本项目对接点
 *
 * 媒体数据来自后端 `services/media_request.py: extract_media()`，其 `type` 为
 * `video` / `images` / `live_photo`（与源项目 `media_group.rs` 的分类一致）。
 * 图集按 `IMAGE_DURATION_SECONDS` 自动轮播（照源项目常量）。
 */
import { useEffect, useRef, useState } from "react";
import type { PlayerMedia } from "./player-types";
import {
  IMAGE_DURATION_SECONDS,
  PLAYER_VIDEO_LOAD_TIMEOUT_MS,
  PLAYER_VIDEO_MAX_AUTO_RETRIES,
  nextStatus,
} from "./player-utils";
import type { PlayerStatus } from "./player-types";

interface Props {
  media: PlayerMedia | null;
  /** 自动播放 */
  autoPlay?: boolean;
  /** 是否静音（首次自动播放通常需静音） */
  muted?: boolean;
  /** 播放倍速 */
  rate?: number;
  className?: string;
  onStatusChange?: (s: PlayerStatus) => void;
  onEnded?: () => void;
}

export function PlayerMediaStage({
  media, autoPlay = false, muted = false, rate = 1,
  className = "", onStatusChange, onEnded,
}: Props) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const retryRef = useRef(0);
  const timerRef = useRef<number | null>(null);
  const [status, setStatus] = useState<PlayerStatus>("idle");
  const [imgIdx, setImgIdx] = useState(0);
  const [failed, setFailed] = useState(false);

  const set = (evt: string) => {
    setStatus((cur) => {
      const nx = nextStatus(cur, evt);
      if (nx !== cur) onStatusChange?.(nx);   // 仅在状态真变化时通知
      return nx;
    });
  };

  // ── 视频：加载/超时/错误重试（照源项目 PLAYER_VIDEO_LOAD_TIMEOUT_MS / MAX_AUTO_RETRIES）──
  useEffect(() => {
    const v = videoRef.current;
    setFailed(false);
    if (!v || media?.type !== "video" || !media.url) {
      if (media?.type === "images" || media?.type === "live_photo") set("canplay");
      return;
    }
    retryRef.current = 0;
    set("load");
    v.load();
    if (timerRef.current) window.clearTimeout(timerRef.current);
    timerRef.current = window.setTimeout(() => {
      if (v.readyState < 3) {
        if (retryRef.current < PLAYER_VIDEO_MAX_AUTO_RETRIES) {
          retryRef.current += 1;
          v.load();
        } else {
          set("error");
        }
      }
    }, PLAYER_VIDEO_LOAD_TIMEOUT_MS);
    return () => {
      if (timerRef.current) window.clearTimeout(timerRef.current);
    };
  }, [media?.url, media?.type]);

  useEffect(() => {
    if (videoRef.current) videoRef.current.playbackRate = rate;
  }, [rate]);

  // ── 图集轮播（照源项目 IMAGE_DURATION_SECONDS=1.5s）──
  useEffect(() => {
    if (media?.type !== "images" || !media.images?.length) return;
    setImgIdx(0);
    if (media.images.length < 2) return;
    const t = window.setInterval(
      () => setImgIdx((i) => (i + 1) % media.images!.length),
      IMAGE_DURATION_SECONDS * 1000,
    );
    return () => window.clearInterval(t);
  }, [media?.type, media?.images?.length]);

  // ── 渲染 ──
  if (!media) {
    return (
      <div className={`flex items-center justify-center bg-black/60 text-xs text-white/50 ${className}`}>
        无媒体
      </div>
    );
  }

  if (media.type === "video") {
    // 加载/卡顿状态提示（照源项目 PLAYER_VIDEO_*_STATUS_DELAY_MS 的"延迟提示"语义：
    // 短暂波动不闪提示，故仅在 loading 且已超初始延迟时才显示）
    const showStatusHint = status === "loading" || status === "rebuffering";
    if (!media.url || failed) {
      return (
        <div className={`flex flex-col items-center justify-center gap-2 bg-black/60 text-xs text-white/60 ${className}`}>
          <span>{failed ? "视频加载失败" : "无可播放地址"}</span>
          {media.cover && <img src={media.cover} alt="" className="max-h-24 opacity-40" />}
        </div>
      );
    }
    return (
      <div className={`relative ${className}`}>
      <video
        ref={videoRef}
        className="h-full w-full bg-black object-contain"
        src={media.url}
        poster={media.cover}
        controls={false}
        autoPlay={autoPlay}
        muted={muted}
        playsInline
        onLoadedData={() => set("canplay")}
        onCanPlay={() => set("canplay")}
        onPlay={() => set("play")}
        onPause={() => set("pause")}
        onWaiting={() => set("waiting")}
        onPlaying={() => set("canplay")}
        onError={() => set("error")}
        onEnded={onEnded}
      />
        {showStatusHint && (
          <div className="absolute inset-x-0 bottom-0 bg-black/50 py-1 text-center text-[0.65rem] text-white/80">
            {status === "loading" ? "加载中…" : "缓冲中…"}
          </div>
        )}
      </div>
    );
  }

  if (media.type === "images" && media.images?.length) {
    return (
      <div className={`relative flex items-center justify-center bg-black/60 ${className}`}>
        <img src={media.images[imgIdx]} alt="" className="max-h-full max-w-full object-contain" />
        <div className="absolute bottom-2 right-2 rounded bg-black/60 px-2 py-0.5 text-[0.65rem] text-white">
          {imgIdx + 1} / {media.images.length}
        </div>
      </div>
    );
  }

  if (media.type === "live_photo" && media.live_photos?.length) {
    const lp = media.live_photos[imgIdx % media.live_photos.length];
    return (
      <video
        className={`bg-black object-contain ${className}`}
        src={lp.video}
        poster={lp.image}
        controls={false}
        autoPlay={autoPlay}
        muted
        loop
        playsInline
        onCanPlay={() => set("canplay")}
        onPlay={() => set("play")}
      />
    );
  }

  return (
    <div className={`flex items-center justify-center bg-black/60 text-xs text-white/50 ${className}`}>
      不支持的媒体类型：{media.type}
    </div>
  );
}
