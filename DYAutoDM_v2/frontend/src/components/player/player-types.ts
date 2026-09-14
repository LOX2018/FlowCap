/**
 * 播放器 —— 类型契约
 *
 * ## 设计来源（照源项目 better-douyin）
 *
 * 对标 `frontend/src/components/player/player-types.ts`（源项目壳源码，权威）。
 * 源项目把播放器做成**独立业务域目录**（15 组件 + 7 hooks + 2 工具文件）：
 *
 * ```
 * components/player/
 *   fullscreen-player.tsx        全屏播放器（主体，62KB）
 *   lazy-fullscreen-player.tsx   懒加载包装
 *   player-media-stage.tsx       媒体舞台（视频/图集切换）
 *   player-playback-bar.tsx      播放条
 *   player-volume-control.tsx    音量
 *   player-speed-menu.tsx        倍速
 *   player-quality-menu.tsx      清晰度
 *   player-share-menu.tsx        分享
 *   player-more-menu.tsx         更多
 *   player-actions.tsx           互动（赞/藏/评）
 *   player-comments.tsx          评论区
 *   player-description.tsx       描述
 *   player-info.tsx              作者信息
 *   player-overlays.tsx          叠加层
 *   player-components.tsx        共用原子
 *   player-types.ts / player-utils.ts / player-utils.ts
 *   use-player-*.ts              （bgm / comments / progress-loop / share /
 *                                  surface-events / action-controls）
 * ```
 *
 * 源项目 `player-types.ts` 实测内容（照抄语义）：
 * ```typescript
 * export type PlayerPanel = "volume" | "rate" | "quality" | "download" | "music" | "share";
 * export type CommentRepliesState = Record<string, {...}>;
 * export type CommentReplyTarget = {...} | null;
 * ```
 *
 * 源项目 `player-utils.ts` 实测常量（照抄）：
 * ```
 * IMAGE_DURATION_SECONDS = 1.5
 * LOAD_MORE_THRESHOLD = 8
 * PLAYER_VIDEO_MAX_AUTO_RETRIES = 1
 * PLAYER_VIDEO_INITIAL_STATUS_DELAY_MS = 450
 * PLAYER_VIDEO_REBUFFER_STATUS_DELAY_MS = 1400
 * PLAYER_VIDEO_LOAD_TIMEOUT_MS = 18000
 * PLAYER_MEDIA_ADVANCE_PRELOAD_TIMEOUT_MS = 1800
 * PLAYER_NEXT_VIDEO_PRELOAD_AHEAD_SECONDS = 10
 * MAX_PRELOADED_MEDIA_NODES = 3
 * PLAYBACK_RATES = [0.5, 0.75, 1, 1.25, 1.5, 2]
 * ```
 *
 * ## 本项目现状（诚实记录）
 *
 * 本项目此前**没有播放器**：`components/platform/platform-page.tsx` 的作品卡片
 * 注释明写「不做播放器，点击只展示描述」。本目录是**从零建**的对应物，
 * 而非迁移既有代码。
 *
 * ## 与源项目的差异
 *
 * · 源项目是 Rust/Tauri 本地代理取流（`media_proxy_cache.rs`）；本项目的媒体
 *   经后端 `services/media_proxy.py`（已建，唯一解密实现 + 缓存）。
 * · 源项目 `CommentRepliesState` 对应其评论子系统；本项目评论能力在
 *   `crawl-page`（采集评论区）与后端 `api/platform.py`，故此处保留类型以便对齐。
 */

/** 播放器面板（照源项目 `PlayerPanel`，含本项目扩展 `download`）。 */
export type PlayerPanel =
  | "volume"
  | "rate"
  | "quality"
  | "download"
  | "music"
  | "share";

/** 媒体类型（本项目后端 `media_request.extract_media` 的 type 值对齐）。 */
export type PlayerMediaType = "video" | "images" | "live_photo";

/** 单条媒体（后端 `extract_media()` 的输出形状）。 */
export interface PlayerMedia {
  type: PlayerMediaType;
  /** 视频/直播回放的播放入口（已选清晰度） */
  url?: string;
  /** 图集（images 时有效） */
  images?: string[];
  /** Live Photo（图片 + 视频对） */
  live_photos?: { image: string; video: string }[];
  /** 封面 */
  cover?: string;
  /** 时长（毫秒，照后端 `duration` 字段） */
  duration?: number;
  /** 作品描述 */
  desc?: string;
  /** 作品 id */
  aweme_id?: string;
}

/** 作者信息（照源项目 `player-info.tsx` 的职责）。 */
export interface PlayerAuthor {
  nickname?: string;
  avatar?: string;
  signature?: string;
  sec_uid?: string;
}

/** 播放状态（照源项目 `PLAYER_VIDEO_*` 状态机语义）。 */
export type PlayerStatus =
  | "idle"
  | "loading"
  | "ready"
  | "playing"
  | "paused"
  | "rebuffering"
  | "error";

/**
 * 评论回复态（照源项目 `CommentRepliesState`，逐字段对齐）。
 * 本项目评论能力尚未接入播放器，先保留契约以便后续对齐。
 */
export type CommentRepliesState = Record<
  string,
  {
    items: { cid?: string; text?: string; nickname?: string }[];
    cursor: number;
    hasMore: boolean;
    loading: boolean;
    error: string;
    total: number;
    loaded: boolean;
  }
>;

/** 评论回复目标（照源项目 `CommentReplyTarget`）。 */
export type CommentReplyTarget = {
  replyId: string;
  replyToReplyId: string;
  nickname: string;
} | null;
