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
import { getMemberToken, setMemberToken } from "./client";
// ★ 2026-09-15 修复：必须复用 `sidecar.ts` 的 BACKEND_BASE
//   （`http://127.0.0.1:8000`）。原实现 fallback 为**空串** ⇒ fetch 走相对路径，
//   Tauri 下解析成 `tauri://localhost/api/...` → 命中 SPA fallback 返回 index.html
//   → JSON 解析报 `Unexpected token '<', "<!doctype "...`（内容页完全不可用）。
import { BACKEND_BASE } from "./sidecar";

const BASE = BACKEND_BASE;

async function post<T>(path: string, body: unknown): Promise<T> {
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  const tk = getMemberToken();
  if (tk) headers["X-Member-Token"] = tk;
  try { headers["X-App-Version"] = String(__APP_VERSION__); } catch { /* ignore */ }
  const res = await fetch(`${BASE}${path}`, {
    method: "POST",
    headers,
    body: JSON.stringify(body ?? {}),
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    // 2026-09-17 修补（OCR 审查 HIGH —— 缺少 401 处理，与 client.ts 不一致）：
    // 所有 /api/* 都要 X-Member-Token，会话过期返回 401。`client.ts` 在 401 时
    // 清 token，使 UI 轮询观察到未登录并回到登录页；本封装原来不清，于是
    // 会话过期后每个内容页请求都只抛普通 Error，而 localStorage 里**陈旧 token
    // 一直在**，界面停留在"已登录"却处处失败。
    if (res.status === 401 && !path.startsWith("/api/member/")) {
      setMemberToken("");
    }
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

/** 流地址票据（`POST /api/platform/media/stream-ticket` 的返回）。 */
export interface StreamTicketDTO {
  ok: boolean;
  /** 本机同源流地址（相对路径，用 mediaStreamUrl() 转绝对） */
  stream_url: string;
  /** 有效期（秒） */
  expires_in: number;
  resolved_via?: string;
  type: "video" | "images" | "live_photo";
  cover: string;
  duration: number;
  desc: string;
  author: { nickname: string; avatar: string; sec_uid: string };
  images: string[];
  live_photos: { image: string; video: string }[];
}

/** 播放器媒体（对齐后端 `POST /api/platform/media/resolve` 的返回） */
export interface PlayerMediaDTO {
  ok: boolean;
  aweme_id: string;
  type: "video" | "images" | "live_photo";
  url: string;
  /** 取址方式（2026-09-21）：direct / head / get-range / passthrough。
   *  `passthrough` 表示未解析出终极地址，可能不可播。 */
  resolved_via?: string;
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

/** 收藏合集（mix）—— 与「收藏夹 collects」是两种不同实体，勿混用。 */
export interface MixItem {
  mix_id: string;
  mix_name: string;
  desc: string;
  cover: string;
  item_total: number;
  play_vv: number;
  update_time: number;
  /**
   * 是否短剧合集（1=短剧，0=普通合集）。
   *
   * 🔴 P1-7（2026-09-23）：普通合集与短剧走**两个不同的上游接口**
   * （`mix_id` vs `series_id`）。后端 `collection/series` 可自动回退，
   * 但若上游返回了该字段，前端**透传**能让分流一次到位、少打一次无效请求。
   */
  is_serial_mix?: number | boolean;
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

export interface CommentFullItem {
  cid: string;
  text: string;
  digg_count: number;
  create_time: number;
  reply_comment_total: number;
  /** 是否含楼中楼（有 total 或已取到回复） */
  has_inner: boolean;
  /** 楼中楼（同结构，不再嵌套） */
  reply_comment: {
    cid: string; text: string; digg_count: number;
    create_time: number; user_nickname: string; user_uid: string;
  }[];
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

  /** ★ 2026-09-21：我的收藏（作品维度）——**不依赖收藏夹文件夹**。
   *  实测该账号文件夹数 0 但收藏作品 19 条，故「收藏」tab 用这个。 */
  favorite: (account: string, sec_id = "", num = 30) =>
    post<{ ok: boolean; items: AwemeItem[]; has_more: boolean; unavailable?: boolean }>(
      "/api/platform/favorite", { account, sec_id, num }),

  /** 收藏夹**文件夹**列表（历史保留：多数账号为 0，仅供按夹浏览）。 */
  collected: (account: string) =>
    post<{ ok: boolean; items: CollectItem[] }>("/api/platform/collected", { account }),

  // 收藏夹内的作品列表（2026-09-15 补：后端 /collection/items 早已实现，
  // 前端此前未接线 → 用户点收藏夹卡片进不去，只看到「N 个作品」）
  collectionItems: (account: string, max_cursor = "0", count = 20) =>
    post<{ ok: boolean; items: AwemeItem[]; has_more: boolean; cursor: number | null }>(
      "/api/platform/collection/items", { account, max_cursor, count }),

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
    post<{ ok: boolean; items: NoticeItem[]; unread: number | null;
           unavailable?: boolean; reason?: string }>(
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

  /**
   * ★ 2026-09-21 新增：换取**本机同源流地址**（方案 B）。
   *
   * 为什么需要：`<video>` 直接指向 CDN（`v26-web.douyinvod.com`）是**跨域
   * 请求**，webview 受 CORS/Referer 策略约束 → 即使后端能取到 200 的真实
   * 地址，播放器仍然失败（实测）。本接口把直链换成
   * `/api/platform/media/stream?...`，由本机后端带正确 headers 转发流，
   * 前端只与本机同源通信 → 无跨域、支持 Range 拖动。
   */
  mediaStreamTicket: (account: string, aweme_id: string, quality = "origin",
                      raw?: Record<string, unknown>) =>
    post<StreamTicketDTO>("/api/platform/media/stream-ticket",
                          raw ? { account, aweme_id, raw, quality }
                              : { account, aweme_id, quality }),

  /** 流地址的**绝对**形式（`<video src>` 需要能被 webview 解析）。 */
  mediaStreamUrl: (streamUrl: string) =>
    streamUrl.startsWith("http") ? streamUrl : `${BASE}${streamUrl}`,

  // ---- 2026-09-21 补齐：后端早已实现、前端此前无入口的端点 ----
  // 判据：后端 api/platform.py 有 20 个路由，前端此前只接了 12 个。

  /** 收藏合集（合集列表，不同于「收藏夹」）。对应 `/collection/mixes`。 */
  collectMixes: (account: string, count = 20, cursor = "0") =>
    post<{ ok: boolean; items: MixItem[]; has_more: boolean; cursor: number | string | null }>(
      "/api/platform/collection/mixes", { account, count, cursor }),

  /**
   * 合集内的作品。对应 `/collection/series`。
   *
   * 🔴 P1-7（2026-09-23）：`mix_id` 才是普通合集的正确参数 —— 短剧（`is_serial_mix=1`）
   * 才用 `series_id`。旧实现把 `pickedMix.id`（= **mix_id**）塞进 `series_id` 字段，
   * 后端又只调短剧接口 → 服务端 `status_code: 5 参数不合法` → 合集**恒空**。
   * 现同时传 `mix_id`（语义正确）与 `series_id`（兼容旧后端把 id 放在该槽的形态），
   * 并透传 `is_serial_mix`（上游有则一次分流到位；无则后端按 sc 回退）。
   */
  collectionSeries: (
    account: string, mix_id: string, count = 20, cursor = "0",
    isSerialMix?: number | boolean,
  ) =>
    post<{
      ok: boolean; items: AwemeItem[]; has_more: boolean;
      /** 实际命中的上游接口（mix=普通合集 / series=短剧），排障用 */
      via?: "mix" | "series"; status_code?: number | null;
    }>("/api/platform/collection/series", {
      account, mix_id, series_id: mix_id, count, cursor,
      is_serial_mix: isSerialMix === undefined ? null : (isSerialMix ? 1 : 0),
    }),

  /** 粉丝 / 关注列表。对应 `/relation/list`（kind: follower | following）。 */
  relationList: (
    account: string, user_id: string, sec_id = "",
    kind: "follower" | "following" = "follower", count = 20,
    max_time = "0",
  ) =>
    post<{ ok: boolean; items: UserItem[]; total: number | null }>(
      "/api/platform/relation/list", { account, user_id, sec_id, kind, count, max_time }),

  /** 作品评论（与采集页 crawlComments 同源；此处按作品 URL 取）。 */
  awemeComments: (account: string, url: string, limit = 20) =>
    post<{ ok: boolean; items: CommentItem[]; has_more: boolean }>(
      "/api/platform/comments", { account, url, limit }),

  /** ★ 2026-09-27（ADR-018 F3）：作品评论采集（一级 + 楼中楼）——**只读**。
   *  与播放器**同时**展示：播放器一打开就并发取评论，不是切 Tab 才加载。
   *  `blocked=true` ⇒ 平台风控拦截（后端如实标注，不返回占位空列表）。 */
  commentsFull: (
    account: string, aweme_id: string, url = "", limit = 50,
    with_inner = true, inner_limit = 5,
  ) =>
    post<{
      ok: boolean; items: CommentFullItem[]; total: number;
      has_more: boolean; blocked?: boolean; reason?: string;
    }>("/api/platform/comments/full", {
      account, aweme_id, url, limit, with_inner, inner_limit }),

  /** ★ 2026-09-27（ADR-018 F3）：评论行「发私信」——**仅手动点击触发**。
   *  后端转调既有 `dm_dispatch.submit_by_uid`（复用限流/去重/投递验证）。
   *  ⚠️ `ok=true` 只是**入队受理**，不是投递成功；前端不得显示「已发送」。 */
  commentDm: (account: string, uid: string, text: string, nickname = "") =>
    post<{ ok: boolean; accepted: boolean; error: string; task_id: string }>(
      "/api/platform/comments/dm", { account, uid, text, nickname }),

  // ---- 写操作（须由用户显式点击触发，不做自动批量） ----
  digg: (account: string, aweme_id: string, action: "1" | "0" = "1") =>
    // ★ 2026-09-26：后端已透传平台原始语义（status_code / status_msg）。
    //   实测 `commit/item/digg` 恒返 status_code=8「用户未登录」——纯 HTTP
    //   通道不具备写权限（需浏览器容器态凭证）。前端据此给出**准确**文案，
    //   不再笼统说「平台侧可能已限流」。
    post<{ ok: boolean; action: string; status_code?: number | null;
           status_msg?: string; raw?: boolean; unverifiable?: boolean }>(
      "/api/platform/action/digg", { account, aweme_id, action }),

  collect: (account: string, aweme_id: string, action: "1" | "0" = "1") =>
    post<{ ok: boolean }>("/api/platform/action/collect", { account, aweme_id, action }),

  // ⛔ 2026-09-26 移除 `follow()`：后端 `/api/platform/action/follow` 已移除。
  //   判据：基座无关注写方法（commit_follow / follow_user 均不存在），端点恒
  //   501；且本前端**无任何组件调用它** ⇒ 是个永远失败的假入口。
  //   将来基座补齐 `commit/follow/user/` 链路时，端点与此处一并加回。
};
