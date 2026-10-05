/**
 * `AuthedVideo` —— IM 视频点播组件（2026-09-17，E3）
 *
 * ## 为什么需要「点播」而不是直接渲染
 *
 * 抖音 IM 视频消息**不自带播放地址**，只有 `tkey`（tos_key）+ `skey`：
 *  ① 用 `tkey` 在**已登录页面上下文**换签名 CDN 地址（`batch_play_info`，外呼）；
 *  ② 下载密文 → CENC 解密 → faststart 重封装。
 * 两步都是**外呼**，若在渲染时自动触发，滚动一遍聊天记录就会对同一视频反复
 * 发请求 —— 既浪费也徒增风控面。故设计成**用户点播**：
 *
 * ```
 * [封面/占位 + ▶ 点击播放]  →  POST /video/resolve  →  blob →  <video controls>
 * ```
 *
 * ## 与 B 方案的关系
 *
 * 解密结果落在本机受保护地址（`/api/messages/video/<name>`，受会员门禁），
 * `<video src>` 无法携带 `X-Member-Token`（实测直连 401），故复用
 * `useAuthedMediaUrl` 带令牌取 Blob 再播。
 *
 * ## 契约
 *
 * · 未点播 → 渲染封面（`poster`，外部直链可直用）或占位块，零外呼；
 * · 点播中 → 转圈 + 「解析中…」；
 * · 成功 → 带 `controls` 的 `<video>`（`playsInline`，适配桌面/触屏）；
 * · 失败 → 展示后端返回的原因（如「换取播放地址失败」）并允许重试，
 *   **不静默吞掉**（用户明确要求可诊断，不要空占位）。
 */
import { useState } from "react";
import { Play, Loader2, TriangleAlert, RotateCcw, Video } from "lucide-react";
import { cn } from "@/lib/utils";
import { api } from "@/api/client";
import { useAuthedMediaUrl } from "@/lib/authed-media";

export interface AuthedVideoProps {
  /** 账号（后端按账号取 BCC 上下文换取播放地址） */
  account: string;
  /** 消息 ID（后端据此读 `extra.video` 要素） */
  msgId: string;
  /** 会话 ID（可选，仅用于后端日志定位） */
  convId?: string;
  /** 封面地址（图床直链可直接渲染；为空则用占位块） */
  poster?: string | null;
  /** 时长（毫秒，展示用） */
  durationMs?: number | null;
  className?: string;
}

function fmtDur(ms?: number | null): string {
  if (!ms || ms <= 0) return "";
  const s = Math.round(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

export function AuthedVideo({
  account,
  msgId,
  convId,
  poster,
  durationMs,
  className,
}: AuthedVideoProps) {
  /** 点播状态：idle 未点播 / loading 解析中 / ready 可播 / error 失败 */
  const [phase, setPhase] = useState<"idle" | "loading" | "ready" | "error">("idle");
  const [videoUrl, setVideoUrl] = useState<string>("");
  const [err, setErr] = useState<string>("");
  /** 播放地址是受保护地址，需带令牌取 Blob */
  const blobUrl = useAuthedMediaUrl(phase === "ready" ? videoUrl : undefined);

  const play = async () => {
    if (phase === "loading") return;
    setPhase("loading");
    setErr("");
    try {
      const r = await api.resolveVideo(account, msgId, { convId });
      if (r?.ok && r.url) {
        setVideoUrl(r.url);
        setPhase("ready");
      } else {
        setErr(r?.error || "视频解析失败");
        setPhase("error");
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : "视频解析异常");
      setPhase("error");
    }
  };

  // 已可播：等待 blob 就绪后交给原生播放器
  if (phase === "ready") {
    if (blobUrl === undefined) {
      return (
        <div className={cn("grid min-h-[120px] min-w-[160px] place-items-center",
                           "rounded-[var(--radius-md)] bg-black/40", className)}>
          <Loader2 className="h-5 w-5 animate-spin text-white/80" />
        </div>
      );
    }
    if (blobUrl === null) {
      return (
        <div className="flex items-center gap-2 rounded-[var(--radius-md)] bg-[var(--color-warning-soft)]
                        px-3 py-2 text-[0.78rem] text-[var(--color-text-secondary)]">
          <TriangleAlert className="h-3.5 w-3.5" />
          视频已解密但取流失败
          <button className="underline" onClick={() => setPhase("idle")}>重试</button>
        </div>
      );
    }
    return (
      <video
        src={blobUrl}
        poster={poster || undefined}
        controls
        autoPlay
        playsInline
        preload="metadata"
        className={cn("max-h-[320px] w-auto max-w-full rounded-[var(--radius-md)]",
                      "bg-black", className)}
      />
    );
  }

  // 未点播 / 解析中 / 失败：封面 + 播放入口（**零外呼**，直到用户点击）
  return (
    <button
      type="button"
      onClick={play}
      disabled={phase === "loading"}
      title={phase === "error" ? err : "点击解析并播放"}
      className={cn(
        "group relative grid h-[120px] w-[170px] place-items-center overflow-hidden",
        "rounded-[var(--radius-md)] bg-black/50 text-white",
        className,
      )}
    >
      {poster ? (
        <img src={poster} alt="" className="absolute inset-0 h-full w-full object-cover opacity-80" />
      ) : null}
      <span className="relative flex flex-col items-center gap-1">
        {phase === "loading" ? (
          <>
            <Loader2 className="h-6 w-6 animate-spin" />
            <span className="text-[0.7rem] opacity-90">解析中…</span>
          </>
        ) : phase === "error" ? (
          <>
            <TriangleAlert className="h-5 w-5 text-[var(--color-warning)]" />
            <span className="max-w-[150px] text-[0.68rem] leading-tight opacity-95">{err}</span>
            <span className="mt-0.5 flex items-center gap-1 text-[0.68rem] underline">
              <RotateCcw className="h-3 w-3" />重试
            </span>
          </>
        ) : (
          <>
            <span className="grid h-9 w-9 place-items-center rounded-full bg-white/25
                             transition group-hover:bg-white/40">
              <Play className="h-4 w-4 translate-x-[1px] fill-current" />
            </span>
            <span className="flex items-center gap-1 text-[0.68rem] opacity-90">
              <Video className="h-3 w-3" />
              {durationMs ? fmtDur(durationMs) : "视频"}
            </span>
          </>
        )}
      </span>
    </button>
  );
}

export default AuthedVideo;
