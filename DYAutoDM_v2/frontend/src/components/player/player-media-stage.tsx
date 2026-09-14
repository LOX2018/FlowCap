/**
 * 播放器 —— 媒体舞台（视频 / 图集 / Live Photo）
 *
 * ## 设计来源（照源项目 + YCVideoPlayer 架构）
 *
 * · 对标源项目 `components/player/player-media-stage.tsx`：
 *   "当前该渲染哪种媒体"收敛在舞台，播放条/面板只与舞台交互。
 * · 按 YCVideoPlayer 分层：本组件属 **UI 视图层**，通过 `VideoKernel` 接口
 *   驱动播放，**不直接依赖某种内核**（可自由切换 html5/hls/dash）。
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { PlayerMedia, PlayerStatus } from "./player-types";
import {
  IMAGE_DURATION_SECONDS,
  PLAYER_VIDEO_LOAD_TIMEOUT_MS,
  PLAYER_VIDEO_MAX_AUTO_RETRIES,
  nextStatus,
} from "./player-utils";
import { pickKernel, type VideoKernel } from "./player-kernel";
import { getPosition, clearPosition, setMeta } from "./player-cache";

interface Props {
  media: PlayerMedia | null;
  /** 自动播放 */
  autoPlay?: boolean;
  /** 是否静音（自动播放通常需静音） */
  muted?: boolean;
  /** 播放倍速 */
  rate?: number;
  className?: string;
  onStatusChange?: (s: PlayerStatus) => void;
  onEnded?: () => void;
  /** 播放位置上报（用于续播记忆，节流后调用） */
  onProgress?: (seconds: number) => void;
}

export function PlayerMediaStage({
  media, autoPlay = false, muted = false, rate = 1,
  className = "", onStatusChange, onEnded, onProgress,
}: Props) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const kernelRef = useRef<VideoKernel | null>(null);
  const retryRef = useRef(0);
  const timerRef = useRef<number | null>(null);
  const [status, setStatus] = useState<PlayerStatus>("idle");
  const [imgIdx, setImgIdx] = useState(0);
  const [failed, setFailed] = useState(false);

  const set = (evt: string) => {
    setStatus((cur) => {
      const nx = nextStatus(cur, evt);
      if (nx !== cur) onStatusChange?.(nx);   // 仅状态真变化时通知
      return nx;
    });
  };

  // 内核选择（媒体变化时）——UI 只依赖接口，新增内核无需改本组件
  const kernelName = useMemo(() => (media ? pickKernel(media).name : ""), [media]);

  // ── 视频：挂载内核 / 超时重试（照源项目 PLAYER_VIDEO_* 常量）──
  useEffect(() => {
    const v = videoRef.current;
    setFailed(false);
    if (!v || media?.type !== "video" || !media.url) {
      if (media?.type === "images" || media?.type === "live_photo") set("canplay");
      return;
    }
    retryRef.current = 0;
    set("load");

    let cancelled = false;
    (async () => {
      try {
        kernelRef.current?.detach();
        const k = pickKernel(media);
        kernelRef.current = k;
        await k.attach(v, media);
        if (cancelled) return;
        // 续播（照 YCVideoPlayer "记录播放位置"）
        const pos = getPosition(media.aweme_id || "");
        if (pos > 0 && Number.isFinite(v.duration) && pos < v.duration - 2) {
          try { v.currentTime = pos; } catch { /* ignore */ }
        }
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
        if (autoPlay) {
          try {
            await v.play();
          } catch {
            /* 自动播放被拦截时静默，由用户点击播放 */
          }
        }
      } catch {
        if (!cancelled) {
          set("error");
          setFailed(true);
        }
      }
    })();

    return () => {
      cancelled = true;
      if (timerRef.current) window.clearTimeout(timerRef.current);
      kernelRef.current?.detach();
    };
  }, [media?.url, media?.type, kernelName]); // eslint-disable-line react-hooks/exhaustive-deps

  // 倍速
  useEffect(() => {
    if (videoRef.current) videoRef.current.playbackRate = rate;
  }, [rate]);

  // 静音
  useEffect(() => {
    if (videoRef.current) videoRef.current.muted = muted;
  }, [muted]);

  // 元数据缓存
  useEffect(() => {
    const v = videoRef.current;
    if (!v || !media?.aweme_id) return;
    const onLoaded = () => {
      setMeta(media.aweme_id!, {
        duration: Number.isFinite(v.duration) ? v.duration : undefined,
        cover: media.cover,
      });
    };
    v.addEventListener("loadedmetadata", onLoaded);
    return () => v.removeEventListener("loadedmetadata", onLoaded);
  }, [media?.aweme_id, media?.cover]);

  // 图集轮播（照源项目 IMAGE_DURATION_SECONDS=1.5s）
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
    if (!media.url || failed) {
      return (
        <div className={`flex flex-col items-center justify-center gap-2 bg-black/60 text-xs text-white/60 ${className}`}>
          <span>{failed ? "视频加载失败" : "无可播放地址"}</span>
          {media.cover && <img src={media.cover} alt="" className="max-h-24 opacity-40" />}
        </div>
      );
    }
    // 加载/卡顿提示（照源项目"延迟提示"语义，避免短暂波动就闪提示）
    const showHint = status === "loading" || status === "rebuffering";
    return (
      <div className={`relative ${className}`}>
        <video
          ref={videoRef}
          className="h-full w-full bg-black object-contain"
          poster={media.cover}
          controls={false}
          muted={muted}
          playsInline
          onLoadedData={() => set("canplay")}
          onCanPlay={() => set("canplay")}
          onPlay={() => set("play")}
          onPause={() => set("pause")}
          onWaiting={() => set("waiting")}
          onPlaying={() => set("canplay")}
          onError={() => { set("error"); setFailed(true); }}
          onEnded={() => {
            clearPosition(media.aweme_id || "");
            onEnded?.();
          }}
          onTimeUpdate={(e) => {
            const el = e.currentTarget;
            if (onProgress && Number.isFinite(el.currentTime)) {
              onProgress(el.currentTime);
            }
          }}
        />
        {showHint && (
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
        autoPlay muted loop playsInline
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
