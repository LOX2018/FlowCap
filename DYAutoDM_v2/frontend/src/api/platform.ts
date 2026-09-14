/**
 * 平台内容 API 客户端（对标 better-douyin 的 bridge 契约）
 *
 * 对应后端 `/api/platform/*`（backend/api/platform.py）。
 *
 * ## 为什么自带 request 而不复用 client.ts 的
 *
 * `client.ts` 的 `request<T>()` 是**模块私有**（未 export），且 `api` 对象里
 * 每个接口都是具名函数、无通用 `post`。为**零侵入**既有 1698 行 API 层，
 * 本模块自带一个同约定的薄封装（同样的 BASE + X-Member-Token + X-App-Version），
 * 而不是去改 client.ts 导出一个通用方法（那会影响 22 个页面的既有调用）。
 */
import { getMemberToken } from "./client";

const BASE = (() => {
  // 与 client.ts 保持一致：Tauri 侧走 sidecar 注入的地址，否则同源
  try {
    const w = window as unknown as { __BACKEND_BASE__?: string };
    if (w.__BACKEND_BASE__) return w.__BACKEND_BASE__;
  } catch {
    /* ignore */
  }
  return "";
})();

async function post<T>(path: string, body: unknown): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const tk = getMemberToken();
  if (tk) headers["X-Member-Token"] = tk;
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers,
    body: JSON.stringify(body ?? {}),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`API ${path} 失败 (${res.status}): ${text}`);
  }
  return res.json() as Promise<T>;
}

export interface AwemeItem {
  aweme_id: string;
  desc: string;
  create_time: number;
  cover: string;
  author_uid: string;
  author_sec_uid: string;
  author_nickname: string;
  digg_count: number;
  comment_count: number;
  share_count: number;
  play_count: number;
  duration: number;
  /** ★ 播放所需最小字段（后端 _pick_aweme 附带；点击播放时原样回传 media/resolve） */
  media?: Record<string, unknown>;
}

/** 播放器媒体（对齐后端 `POST /api/platform/media/resolve` 的返回） */
export interface PlayerMediaDTO {
  ok: boolean;
  aweme_id: string;
  type: "video" | "images" | "live_photo";
  url: string;
  images: string[];
  live_photos: { image: string; video: string }[];
  cover: string;
  duration: number;
  desc: string;
  author: { nickname: string; avatar: string; sec_uid: string };
  qualities: string[];
}

export interface UserItem {
  uid: string;
  sec_uid: string;
  nickname: string;
  signature: string;
  avatar: string;
  follower_count: number;
  following_count: number;
  aweme_count: number;
  total_favorited: number;
}

export interface CollectItem {
  collects_id: string;
  name: string;
  count: number;
}

export interface NoticeItem {
  notice_id: string;
  type: string;
  content: string;
  create_time: number;
  is_read: boolean;
}

export interface CommentItem {
  cid: string;
  text: string;
  digg_count: number;
  create_time: number;
  reply_comment_total: number;
  user_nickname: string;
  user_uid: string;
  user_sec_uid: string;
}

export const platformApi = {
  feed: (account: string, count = 20) =>
    post<{ ok: boolean; items: AwemeItem[]; has_more: boolean }>(
      "/api/platform/feed", { account, count }),

  userWorks: (account: string, user_url: string, limit = 50) =>
    post<{ ok: boolean; items: AwemeItem[]; total: number }>(
      "/api/platform/user/works", { account, user_url, limit }),

  userInfo: (account: string, user_url: string) =>
    post<{ ok: boolean; user: UserItem }>("/api/platform/user/info", { account, user_url }),

  search: (account: string, query: string, kind: "video" | "user" = "video", num = 20) =>
    post<{ ok: boolean; kind: string; items: (AwemeItem | UserItem)[] }>(
      "/api/platform/search", { account, query, kind, num }),

  collected: (account: string) =>
    post<{ ok: boolean; items: CollectItem[] }>("/api/platform/collected", { account }),

  liked: (account: string, sec_id = "", num = 18) =>
    post<{ ok: boolean; items: AwemeItem[]; has_more: boolean }>(
      "/api/platform/liked", { account, sec_id, num }),

  relation: (
    account: string, user_id: string, sec_id: string,
    kind: "follower" | "following" = "follower", count = 20,
  ) =>
    post<{ ok: boolean; items: UserItem[]; total: number | null }>(
      "/api/platform/relation/list", { account, user_id, sec_id, kind, count }),

  notices: (account: string, count = 10, group = "700") =>
    post<{ ok: boolean; items: NoticeItem[]; unread: number | null }>(
      "/api/platform/notice/list", { account, count, group }),

  comments: (account: string, url: string, limit = 20) =>
    post<{ ok: boolean; items: CommentItem[]; has_more: boolean }>(
      "/api/platform/comments", { account, url, limit }),

  /** 媒体取址（播放器用）。★ 优先传 `raw`（列表返回的作品对象）——
   *  实测列表对象自带播放地址，而详情接口平台侧返回空。 */
  mediaResolve: (account: string, aweme_id: string, quality = "origin",
                 raw?: Record<string, unknown>) =>
    post<PlayerMediaDTO>("/api/platform/media/resolve",
                         raw ? { account, raw, quality } : { account, aweme_id, quality }),

  /** 媒体代理缓存统计（照源项目 media_proxy_cache.rs 语义） */
  mediaStats: () => post<{ ok: boolean } & Record<string, unknown>>(
    "/api/platform/media/stats", {}),

  // ---- 写操作（须由用户显式点击触发，不做自动批量） ----
  digg: (account: string, aweme_id: string, action: "1" | "0" = "1") =>
    post<{ ok: boolean; action: string }>("/api/platform/action/digg", { account, aweme_id, action }),

  collect: (account: string, aweme_id: string, action: "1" | "0" = "1") =>
    post<{ ok: boolean }>("/api/platform/action/collect", { account, aweme_id, action }),

  follow: (account: string, user_id: string, sec_id = "", action: "1" | "0" = "1") =>
    post<{ ok: boolean }>("/api/platform/action/follow", { account, user_id, sec_id, action }),
};
