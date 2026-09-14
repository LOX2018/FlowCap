/**
 * 播放器 —— 边播边缓存（cache）
 *
 * ## 设计来源：YCVideoPlayer 的 VideoCache 模块
 *
 * YCVideoPlayer 架构中的独立一层（"边播边缓存"），与内核、UI 解耦。
 * 其 Android 实现是本地代理 + 文件缓存（VideoCache 库）。
 *
 * ## Web 侧的实现取舍（诚实记录）
 *
 * Web 浏览器**无法**像 Android 那样拦截 `<video>` 的字节流做代理缓存
 * （除非用 Service Worker + Range 请求重建，复杂且对跨域/CDN 有限制）。
 *
 * 故本模块实现**可达的等价能力**：
 *   1. **播放位置记忆**（照 YCVideoPlayer "记录播放位置" + VideoSqlLite 模块）
 *      —— 切走再回来能续播，这是用户最能感知的"缓存"价值。
 *   2. **元数据缓存**（时长/清晰度/封面）避免重复解析。
 *   3. **预取提示**：对下一个候选提前建立连接（dns-prefetch / preconnect），
 *      降低起播延迟（对应"边播边缓存"的准备动作）。
 *
 * 真正的字节级缓存可由后端承担：本项目 `services/media_proxy.py`
 * （唯一解密实现 + 磁盘缓存），与源项目 `media_proxy_cache.rs` 同职责。
 */

const POS_KEY = "dy.player.pos.v1";
const META_KEY = "dy.player.meta.v1";

function readJson<T>(key: string, fallback: T): T {
  try {
    const raw = localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function writeJson(key: string, value: unknown): void {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch {
    /* 隐私模式等忽略 */
  }
}

// ── 1. 播放位置记忆（照 VideoSqlLite 模块）──

type PosMap = Record<string, { t: number; ts: number }>;

/** 读取某作品的播放位置（秒）。 */
export function getPosition(awemeId: string): number {
  if (!awemeId) return 0;
  const m = readJson<PosMap>(POS_KEY, {});
  return m[awemeId]?.t || 0;
}

/** 写入播放位置（节流由调用方控制，避免高频写）。 */
export function setPosition(awemeId: string, seconds: number): void {
  if (!awemeId || !Number.isFinite(seconds) || seconds <= 0) return;
  const m = readJson<PosMap>(POS_KEY, {});
  m[awemeId] = { t: Math.floor(seconds), ts: Date.now() };
  // 只保留最近 200 条，避免无限增长
  const keys = Object.keys(m);
  if (keys.length > 200) {
    keys
      .sort((a, b) => (m[a].ts || 0) - (m[b].ts || 0))
      .slice(0, keys.length - 200)
      .forEach((k) => delete m[k]);
  }
  writeJson(POS_KEY, m);
}

/** 清除某作品的播放位置（播完时调用）。 */
export function clearPosition(awemeId: string): void {
  if (!awemeId) return;
  const m = readJson<PosMap>(POS_KEY, {});
  if (awemeId in m) {
    delete m[awemeId];
    writeJson(POS_KEY, m);
  }
}

// ── 2. 元数据缓存（避免重复解析）──

export interface CachedMeta {
  duration?: number;
  cover?: string;
  qualities?: string[];
  ts: number;
}

export function getMeta(awemeId: string): CachedMeta | null {
  if (!awemeId) return null;
  const m = readJson<Record<string, CachedMeta>>(META_KEY, {});
  return m[awemeId] || null;
}

export function setMeta(awemeId: string, meta: Omit<CachedMeta, "ts">): void {
  if (!awemeId) return;
  const m = readJson<Record<string, CachedMeta>>(META_KEY, {});
  m[awemeId] = { ...meta, ts: Date.now() };
  const keys = Object.keys(m);
  if (keys.length > 300) {
    keys
      .sort((a, b) => (m[a].ts || 0) - (m[b].ts || 0))
      .slice(0, keys.length - 300)
      .forEach((k) => delete m[k]);
  }
  writeJson(META_KEY, m);
}

// ── 3. 预取提示（降低起播延迟）──

/**
 * 对候选 URL 提前建连（对应"边播边缓存"的准备动作）。
 * 幂等：同一 origin 只加一次。
 */
const prefetched = new Set<string>();

export function prefetchUrl(url: string): void {
  if (!url) return;
  try {
    const origin = new URL(url, location.href).origin;
    if (prefetched.has(origin)) return;
    prefetched.add(origin);
    const link = document.createElement("link");
    link.rel = "preconnect";
    link.href = origin;
    link.crossOrigin = "anonymous";
    document.head.appendChild(link);
  } catch {
    /* ignore */
  }
}
