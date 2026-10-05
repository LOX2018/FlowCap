/**
 * 受保护媒体的 Blob URL 适配（2026-09-17，B 方案）
 *
 * ## 问题
 *
 * `<img src>` / `<video src>` **无法携带自定义请求头**，而后端媒体端点
 * （`/api/messages/origin_image/...`、`/api/messages/video/...`）受会员门禁
 * 保护 —— 实测无令牌访问一律 **401**。
 *
 * 于是「后端解密好的原图/视频」一旦直接塞进 `src`，浏览器不带令牌，
 * 必然取不到（既有原图显示靠 `onError` 降级到内联缩略图，所以长期静默破损、
 * 没人发现）。**这是本次顺带修掉的既有缺陷。**
 *
 * ## 为什么用 B 方案而不是「把端点加进鉴权豁免」
 *
 * 用户 2026-09-17 拍板：**(B) 前端带令牌 fetch → Blob → objectURL**。
 * · **后端零改动** —— 不动 `_MEMBER_EXEMPT`，不引入「媒体端点无鉴权」这一
 *   安全面变化（豁免清单一旦放宽，任何知道文件名的人都可取走本地媒体）。
 * · 鉴权保持完整：取媒体与取 JSON 走**同一套** `X-Member-Token` 语义。
 * · 代价：每个媒体多一次 fetch（对本机 127.0.0.1 可忽略），且需管理对象 URL 生命周期。
 *
 * ## 契约
 *
 * · 本地 API 地址（`/api/...` 或 `127.0.0.1:<port>/api/...`）→ 带令牌 fetch 成 Blob；
 * · 其它地址（图床 `https://...`、`data:`）→ **原样返回**，零改动零请求；
 * · 同一 src 的**并发请求合并**（列表里同一张图多处出现只取一次）；
 * · 组件卸载 / src 变化时 `revokeObjectURL`，避免内存泄漏；
 * · 失败**不抛给渲染层**：返回 `null`，调用方按既有降级路径处理。
 */
import { useEffect, useState } from "react";
import { fetchAuthedBlob, isLocalApiUrl, toBackendUrl } from "@/api/client";

/** 进程内缓存：src → objectURL（命中即复用，避免重复 fetch 与重复对象）。 */
const urlCache = new Map<string, string>();
/** 负缓存：src → 失败时刻。避免「必然失败」的地址在每次挂载/滚动时重打网络。
 *  2026-09-18 审查修复（#56）：原实现只缓存成功，401/404 会被无限重试
 *  （每次都跑一遍 ensureBackendReady + 网络往返）。 */
const failCache = new Map<string, number>();
const FAIL_TTL_MS = 30_000;        // 30s：短到不影响「修好后重试」
/** 进行中的请求：src → Promise（并发去重）。 */
const inflight = new Map<string, Promise<string | null>>();

/** 载入一个受保护媒体 → objectURL（失败返回 null）。 */
async function load(src: string): Promise<string | null> {
  const cached = urlCache.get(src);
  if (cached) return cached;
  const failedAt = failCache.get(src);
  if (failedAt && Date.now() - failedAt < FAIL_TTL_MS) return null;   // 负缓存命中
  const running = inflight.get(src);
  if (running) return running;

  const p = (async () => {
    try {
      const blob = await fetchAuthedBlob(toBackendUrl(src));
      const url = URL.createObjectURL(blob);
      urlCache.set(src, url);
      return url;
    } catch {
      // 静默降级：调用方按既有 onError / 占位分支处理，不打断聊天渲染。
      // 同时写负缓存（#56）：必然失败的地址（401/404）在 TTL 内不再重打网络。
      failCache.set(src, Date.now());
      return null;
    } finally {
      inflight.delete(src);
    }
  })();
  inflight.set(src, p);
  return p;
}

/**
 * 把「可能是受保护地址」的媒体 src 解析成可直接放进 `<img>/<video>` 的地址。
 *
 * @returns `undefined` = 正在加载（调用方应显示占位）；`null` = 取失败；
 *          `string` = 可用地址（本地 blob: 或外部原地址）。
 * @param bust 手动重试计数（2026-09-26 H-22 idx22）：`>0` 时**先清该 src 的负缓存在读**，
 *             使「点击重试」立即重取，而非被 `FAIL_TTL_MS`（30s）挡在门外。
 */
export function useAuthedMediaUrl(src?: string, bust = 0): string | null | undefined {
  const local = isLocalApiUrl(src);
  // 外部地址（图床 / data URI）无需任何处理：同步即可用，零闪烁
  const initial = (): string | null | undefined => {
    if (!src) return null;
    return local ? urlCache.get(src) ?? undefined : src;
  };
  const [state, setState] = useState<string | null | undefined>(initial);
  // 2026-09-18 审查修复（#60）：`src` 变化时**同步复位**。
  // 仅靠 useEffect 复位会晚一帧 —— 那一帧里渲染的是**上一个 src 的 blob URL**，
  // 表现为「切换消息瞬间闪出上一张图」。React 官方推荐的「渲染期派生状态」写法
  // （state 记录自己跟着哪个 src 算出来的）可消除该窗口。
  const [stateFor, setStateFor] = useState<string | undefined>(src);
  if (stateFor !== src) {
    setStateFor(src);
    setState(initial());
  }

  useEffect(() => {
    if (!src) {
      setState(null);
      return;
    }
    if (!local) {
      setState(src);
      return;
    }
    let alive = true;
    // 手动重试（H-22 idx22）：先清该 src 的负缓存，保证「点击重试」不被 30s TTL 挡住。
    if (bust > 0) {
      failCache.delete(src);
      const stale = urlCache.get(src);
      if (stale) {
        try { URL.revokeObjectURL(stale); } catch { /* ignore */ }
        urlCache.delete(src);
      }
    }
    setState(urlCache.get(src) ?? undefined);
    void load(src).then((u) => {
      if (alive) setState(u);
    });
    return () => {
      // 注意：**不**在此 revoke —— 缓存是进程级共享，同一 src 可能仍被其它
      // 组件使用（列表滚动、弹层同时打开）。对象 URL 在缓存驱逐或应用退出时
      // 由浏览器统一回收；进程内媒体数量有上限（会话消息分页加载）。
      alive = false;
    };
  }, [src, local, bust]);

  return state;
}

/** 清空缓存并回收全部对象 URL（退出登录 / 切换账号时调用，防跨账号串图）。 */
export function clearAuthedMediaCache(): void {
  for (const u of urlCache.values()) {
    try {
      URL.revokeObjectURL(u);
    } catch {
      /* ignore */
    }
  }
  urlCache.clear();
  inflight.clear();
  failCache.clear();
}

/** 缓存统计（供自检 / 调试展示）。 */
export function authedMediaStats(): { cached: number; inflight: number } {
  return { cached: urlCache.size, inflight: inflight.size };
}
