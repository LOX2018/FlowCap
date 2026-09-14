/**
 * 播放器 —— 导出入口（照源项目 `components/player/` 的模块边界）
 *
 * ## 设计来源（照源项目 better-douyin）
 *
 * 对标源项目 `components/player/` 的目录级导出。源项目把播放器做成独立业务域：
 * 外部只从目录入口导入，不深入内部文件。
 *
 * ## 本项目的裁剪（诚实记录）
 *
 * 源项目 player/ 有 15 组件 + 7 hooks（含 62KB 主体、32KB 评论 hook）。
 * 本项目按其**结构**落地了核心链路：
 *   `FullscreenPlayer`（主体）· `PlayerMediaStage`（舞台）· `PlayerPlaybackBar`（播放条）
 *   · `player-types` / `player-utils`（契约与常量，逐条照抄源项目）
 * 未对齐（本项目无对应业务）：评论子系统、BGM、分享、更多菜单、进度预览条。
 */
export { FullscreenPlayer } from "./fullscreen-player";
export { PlayerMediaStage } from "./player-media-stage";
export { PlayerPlaybackBar } from "./player-playback-bar";
export type {
  CommentRepliesState,
  CommentReplyTarget,
  PlayerAuthor,
  PlayerMedia,
  PlayerMediaType,
  PlayerPanel,
  PlayerStatus,
} from "./player-types";
export {
  IMAGE_DURATION_SECONDS,
  MAX_PRELOADED_MEDIA_NODES,
  PLAYBACK_RATES,
  PLAYER_NEXT_VIDEO_PRELOAD_AHEAD_SECONDS,
  PLAYER_VIDEO_INITIAL_STATUS_DELAY_MS,
  PLAYER_VIDEO_LOAD_TIMEOUT_MS,
  PLAYER_VIDEO_MAX_AUTO_RETRIES,
  PLAYER_VIDEO_REBUFFER_STATUS_DELAY_MS,
  QUALITY_LABEL,
  QUALITY_OPTIONS,
  fmtDuration,
  fmtMs,
  mediaKindLabel,
  nextStatus,
} from "./player-utils";
