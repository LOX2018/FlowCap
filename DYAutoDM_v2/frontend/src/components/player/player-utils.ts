/**
 * 播放器 —— 工具与常量
 *
 * ## 设计来源（照源项目 better-douyin）
 *
 * 对标 `frontend/src/components/player/player-utils.ts`（源项目壳源码，权威）。
 * 源项目实测常量**逐条照抄**（见文件内注释），仅补本项目需要的辅助函数。
 *
 * ## 为什么常量要照抄
 *
 * 这些数值是源项目**实测调优**的结果（如 `PLAYER_VIDEO_LOAD_TIMEOUT_MS=18000`
 * 是等待视频可播放的上限、`REBUFFER_STATUS_DELAY_MS=1400` 是"卡顿提示延迟"以避免
 * 短暂波动就闪提示）。照抄可复用其调优结论，而非重新试错。
 */

import type { PlayerMedia, PlayerStatus } from "./player-types";

// ── 照源项目 player-utils.ts 的常量（逐条对齐，勿随意改）──
/** 图集每张停留时长（秒） */
export const IMAGE_DURATION_SECONDS = 1.5;
/** 触底加载阈值（剩余 N 条时预加载） */
export const LOAD_MORE_THRESHOLD = 8;
/** 视频自动重试上限 */
export const PLAYER_VIDEO_MAX_AUTO_RETRIES = 1;
/** 首次加载状态提示延迟（毫秒）——避免"秒开"也闪加载态 */
export const PLAYER_VIDEO_INITIAL_STATUS_DELAY_MS = 450;
/** 卡顿（rebuffer）状态提示延迟——避免短暂波动就提示 */
export const PLAYER_VIDEO_REBUFFER_STATUS_DELAY_MS = 1400;
/** 视频可播放等待上限 */
export const PLAYER_VIDEO_LOAD_TIMEOUT_MS = 18000;
/** 切换下一条前的预加载等待 */
export const PLAYER_MEDIA_ADVANCE_PRELOAD_TIMEOUT_MS = 1800;
/** 提前预加载下一条的秒数 */
export const PLAYER_NEXT_VIDEO_PRELOAD_AHEAD_SECONDS = 10;
/** 同时保留的媒体节点上限 */
export const MAX_PRELOADED_MEDIA_NODES = 3;
/** 可选倍速 */
export const PLAYBACK_RATES = [0.5, 0.75, 1, 1.25, 1.5, 2] as const;

// ── 本项目补充 ──
/** 可选清晰度（对齐后端 `media_request.pick_quality` 的降级链命名） */
export const QUALITY_OPTIONS = ["origin", "h264", "h265", "lowbr"] as const;
export type QualityOption = (typeof QUALITY_OPTIONS)[number];

/** 清晰度中文名（UI 展示） */
export const QUALITY_LABEL: Record<string, string> = {
  origin: "原画",
  h264: "高清 H.264",
  h265: "高清 H.265",
  lowbr: "流畅",
};

/** 秒 → mm:ss */
export function fmtDuration(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return "00:00";
  const s = Math.floor(seconds % 60);
  const m = Math.floor(seconds / 60) % 60;
  const h = Math.floor(seconds / 3600);
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${pad(m)}:${pad(s)}`;
}

/** 毫秒 → mm:ss（后端 duration 是毫秒） */
export function fmtMs(ms?: number): string {
  return fmtDuration((ms || 0) / 1000);
}

/** 根据媒体类型给出"是否可播放音频"等判据 */
export function mediaKindLabel(m?: PlayerMedia | null): string {
  if (!m) return "未知";
  if (m.type === "images") return `图集 · ${m.images?.length || 0} 张`;
  if (m.type === "live_photo") return `实况 · ${m.live_photos?.length || 0} 组`;
  return "视频";
}

/**
 * 播放状态机迁移（照源项目的状态语义收敛在一处，避免各处自行判断）。
 *
 * 规则：
 *  · 只有 `ready/playing/paused` 之间可自由切换
 *  · `loading` 进入后只能去 `ready` 或 `error`
 *  · `rebuffering` 可回到 `playing`（恢复）或去 `error`
 */
export function nextStatus(cur: PlayerStatus, evt: string): PlayerStatus {
  switch (evt) {
    case "load":
      return "loading";
    case "canplay":
      return cur === "rebuffering" ? "playing" : "ready";
    case "play":
      return "playing";
    case "pause":
      return cur === "playing" ? "paused" : cur;
    case "waiting":
      return "rebuffering";
    case "error":
      return "error";
    case "reset":
      return "idle";
    default:
      return cur;
  }
}
