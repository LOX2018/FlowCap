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
import { useAuthedMediaUrl } from "@/lib/authed-media";
import { Loader2 } from "lucide-react";

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

  // ── 受保护媒体地址解析（★ 2026-09-26 根因修复）──────────────────────────
  // 判据（实测）：`<video src>` / `<img src>` **无法携带 `X-Member-Token`**，
  // 而后端媒体端点（`/api/platform/media/stream?...`、`/api/messages/video/...`）
  // 受会员门禁保护 —— 裸地址直连**必然 401**，表现为「后端链路明明 206 可拉，
  // 播放器却一直失败」。
  // 既有成熟方案 `useAuthedMediaUrl`（IM 消息视频已用）：本地 API 地址带令牌
  // fetch 成 Blob 再交给 `<video>`；外部地址（图床/data:）原样返回，零开销。
  // 在此处统一接入 ⇒ 所有经本舞台播放的受保护媒体自动生效，无需各调用方改造。
  // 时序契约：undefined=加载中（保持占位，不置 error）；null=取失败（走重试/降级）；
  //          string=可用（blob: 或外部原地址）。
  const resolvedUrl = useAuthedMediaUrl(media?.type === "video" ? media?.url : undefined);
  const stageMedia = useMemo<PlayerMedia | null>(() => {
    if (!media) return null;
    if (media.type !== "video") return media;
    if (resolvedUrl === undefined) return null;      // 仍在取 Blob → 先不挂载（占位）
    if (resolvedUrl === null) return { ...media, url: "" };  // 取失败 → 触发失败分支
    return { ...media, url: resolvedUrl };
  }, [media, resolvedUrl]);

  const set = (evt: string) => {
    setStatus((cur) => {
      const nx = nextStatus(cur, evt);
      if (nx !== cur) onStatusChange?.(nx);   // 仅状态真变化时通知
      return nx;
    });
  };

  // 内核选择（媒体变化时）——UI 只依赖接口，新增内核无需改本组件
  const kernelName = useMemo(() => (stageMedia ? pickKernel(stageMedia).name : ""), [stageMedia]);

  // ── 视频：挂载内核 / 超时重试（照源项目 PLAYER_VIDEO_* 常量）──
  useEffect(() => {
    const v = videoRef.current;
    setFailed(false);
    if (!v || stageMedia?.type !== "video" || !stageMedia.url) {
      if (stageMedia?.type === "images" || stageMedia?.type === "live_photo") set("canplay");
      return;
    }
    retryRef.current = 0;
    set("load");

    let cancelled = false;
    (async () => {
      try {
        kernelRef.current?.detach();
        const k = pickKernel(stageMedia);
        kernelRef.current = k;
        await k.attach(v, stageMedia);
        if (cancelled) return;
        // 续播（照 YCVideoPlayer "记录播放位置"）
        const pos = getPosition(stageMedia.aweme_id || "");
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
  }, [stageMedia?.url, stageMedia?.type, kernelName]); // eslint-disable-line react-hooks/exhaustive-deps

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
    if (!v || !stageMedia?.aweme_id) return;
    const onLoaded = () => {
      setMeta(stageMedia.aweme_id!, {
        duration: Number.isFinite(v.duration) ? v.duration : undefined,
        cover: stageMedia.cover,
      });
    };
    v.addEventListener("loadedmetadata", onLoaded);
    return () => v.removeEventListener("loadedmetadata", onLoaded);
  }, [stageMedia?.aweme_id, stageMedia?.cover]);

  // 图集轮播（照源项目 IMAGE_DURATION_SECONDS=1.5s）
  useEffect(() => {
    if (stageMedia?.type !== "images" || !stageMedia.images?.length) return;
    setImgIdx(0);
    if (stageMedia.images.length < 2) return;
    const t = window.setInterval(
      () => setImgIdx((i) => (i + 1) % stageMedia.images!.length),
      IMAGE_DURATION_SECONDS * 1000,
    );
    return () => window.clearInterval(t);
  }, [stageMedia?.type, stageMedia?.images?.length]);

  // ── 渲染 ──
  if (!stageMedia) {
    // ★ 2026-09-26：区分「媒体为空」与「受保护地址正在取 Blob」——
    //   本地 API 地址首帧 resolvedUrl===undefined（仍在 fetch），此时显示
    //   加载态而非「无媒体」，避免加载中误报为空。
    const loadingAuth = media?.type === "video" && !!media.url && resolvedUrl === undefined;
    return (
      <div className={`flex items-center justify-center gap-2 bg-black/60 text-xs text-white/50 ${className}`}>
        {loadingAuth ? (
          <>
            <Loader2 className="h-4 w-4 animate-spin" />
            加载中…
          </>
        ) : "无媒体"}
      </div>
    );
  }

  if (stageMedia.type === "video") {
    if (!stageMedia.url || failed) {
      return (
        <div className={`flex flex-col items-center justify-center gap-2 bg-black/60 text-xs text-white/60 ${className}`}>
          <span>{failed ? "视频加载失败" : "无可播放地址"}</span>
          {stageMedia.cover && <img src={stageMedia.cover} alt="" className="max-h-24 opacity-40" />}
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
          poster={stageMedia.cover}
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
            clearPosition(stageMedia.aweme_id || "");
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

  if (stageMedia.type === "images" && stageMedia.images?.length) {
    return (
      <div className={`relative flex items-center justify-center bg-black/60 ${className}`}>
        <img src={stageMedia.images[imgIdx]} alt="" className="max-h-full max-w-full object-contain" />
        <div className="absolute bottom-2 right-2 rounded bg-black/60 px-2 py-0.5 text-[0.65rem] text-white">
          {imgIdx + 1} / {stageMedia.images.length}
        </div>
      </div>
    );
  }

  if (stageMedia.type === "live_photo" && stageMedia.live_photos?.length) {
    const lp = stageMedia.live_photos[imgIdx % stageMedia.live_photos.length];
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
      不支持的媒体类型：{stageMedia.type}
    </div>
  );
}
