/**
 * 内容浏览页面（对标 better-douyin 的 search / recommended / liked / collected / notices）
 *
 * 一个页面按 tab 承载 6 个子视图，避免为每项加一个导航（导航已 11 项）：
 *   推荐流 / 搜索 / 用户作品 / 点赞 / 收藏夹 / 站内通知
 *
 * ## 风控约束（铁律）
 * 所有请求都是**用户显式动作触发**（切 tab / 点搜索 / 点刷新），
 * **不做后台自动轮询** —— 主动请求越少越安全。
 */
import { useState, useEffect, useRef, useMemo } from "react";
import { useQuery, useQueries } from "@tanstack/react-query";
import {
  LayoutGrid, Search as SearchIcon, Heart, Star, Bell, User, MessageSquare,
  Users, BookOpen, AlertTriangle, RefreshCw,
} from "lucide-react";
import { PageContainer, PageHeader } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";
import { EmptyState, LoadingState, ErrorState } from "@/components/ui/empty-state";
import { platformApi, type AwemeItem, type UserItem, type NoticeItem } from "@/api/platform";
// ★ 2026-10-03：采集已从「独立 tab」改为**悬浮窗 + 各 tab 就地勾选**
//   （用户指令「button[9] 删除」）。原挂载 CrawlPage 的 import 随之移除 ——
//   留着会触发 tsc TS6133（未使用导入）。
// ★ 2026-09-27（ADR-018 F3）：播放 + 评论同时展示；评论行可手动发私信
import { CommentPanel } from "./comment-panel";
import { fmtNum, fmtAgo } from "@/lib/utils";
import type { PageProps } from "@/api/client";
import { FullscreenPlayer } from "@/components/player";
import type { PlayerMedia } from "@/components/player";

/** 作品卡片（封面 + 统计；点击打开播放器）。 */
import { Grid, UserCard } from "./platform-cards";
// ★ 2026-10-03：采集悬浮窗（独立于各 tab，统一入口）
import CrawlFloatingPanel from "@/components/crawl/CrawlFloatingPanel";
// ★ 2026-10-03：从采集页移植的筛选器选项
import { ORDER_OPTS, PT_OPTS, DUR_OPTS } from "@/components/crawl/crawl-shared";

export default function PlatformPage(props: PageProps & {
  /**
   * 嵌入模式（ADR-033，2026-10-03）—— 内容浏览作为「采集页」的一个 tab 存在。
   *
   * 为什么不是复制一份代码到采集页：复制会让两处逻辑各自漂移
   * （播放器取址 / 风控拦截提示 / 互动写接口降级文案…都是踩过坑才写对的）。
   * 改为**同一组件两种宿主**：
   *   · 独立模式（默认）：自带 PageContainer + PageHeader，自带 tab 状态；
   *   · 嵌入模式（`embedded`）：只吐 Tabs 主体，外层宿主管容器与 tab 切换。
   *
   * @param embedded      true = 嵌入采集页（不渲染 PageContainer/PageHeader）
   * @param account      嵌入模式下由宿主提供账号（宿主与采集页共用同一下拉）
   * @param accounts     嵌入模式下由宿主提供账号列表
   * @param onSelectAweme 选中作品时回调宿主（采集页据此填入采集目标）
   */
  embedded?: boolean;
  account?: string;
  accounts?: string[];
  onSelectAweme?: (awemeId: string) => void;
}) {
  const embedded = props.embedded === true;
  // ★ 账号来源（2026-09-14 修复）：原用 `props.overview.accounts`，
  //   但 `/api/overview` **不返回 accounts 字段** → 内容页恒显示「还没有账号」，
  //   而私信页（走 `/api/accounts`）却正常。现统一为本项目的账号真源。
  // ★ ADR-033：嵌入模式下账号由宿主（采集页）提供，本组件不再自己拉 ——
  //   否则采集页与内容 tab 会各持一份账号状态，切 tab 时选择不同步。
  const accountsQ = useQuery({
    queryKey: ["platform-accounts"],
    queryFn: async (): Promise<{ name: string }[]> =>
      (await props.api.getAccounts()) as unknown as { name: string }[],
    enabled: !!props.ready && !embedded,
    staleTime: 300_000,
  });
  const accounts = embedded
    ? (props.accounts || [])
    : (accountsQ.data || []).map((a) => a.name).filter(Boolean);
  const [acct, setAcct] = useState<string>(() => {
    try {
      return localStorage.getItem("platform.acct") || "";
    } catch {
      return "";
    }
  });
  const account = embedded ? (props.account || "") : (acct || accounts[0] || "");
  const [tab, setTab] = useState("feed");
  const [query, setQuery] = useState("");
  const [searchKind, setSearchKind] = useState<"video" | "user">("video");
  const [userUrl, setUserUrl] = useState("");
  const [submitted, setSubmitted] = useState<{ q: string; kind: "video" | "user" } | null>(null);
  const [worksUrl, setWorksUrl] = useState<string | null>(null);
  // ★ 采集悬浮窗状态（2026-10-03）
  const [crawlPanelOpen, setCrawlPanelOpen] = useState(false);
  // ★ 各 tab 勾选的作品 ID 列表
  const [checkedIds, setCheckedIds] = useState<Record<string, boolean>>({});
  // ★ 搜索筛选器（从采集页移植）
  const [order, setOrder] = useState("0");
  const [pt, setPt] = useState("0");
  const [dur, setDur] = useState("");
  // ── 播放器（★ 本分支新增：卡片点击 → 取址 → 播放）──
  const [playerMedia, setPlayerMedia] = useState<PlayerMedia | null>(null);
  const [playerOpen, setPlayerOpen] = useState(false);
  // 选中作品（采集模式复用：点开卡片即设为当前采集目标）
  const [selectedAweme, setSelectedAweme] = useState<string>("");
  // 选中的收藏夹（2026-09-21 移除：收藏改为直接列作品，不再有「夹」这一层）
  const [playerErr, setPlayerErr] = useState<string>("");
  // ★ 采集勾选处理（2026-10-03）
  const handleToggleCheck = (id: string, checked: boolean) => {
    setCheckedIds((prev) => ({ ...prev, [id]: checked }));
  };
  // 获取已勾选的作品 ID 列表
  const getCheckedIds = () => Object.entries(checkedIds).filter(([, v]) => v).map(([k]) => k);

  // ★ 2026-10-03（用户指令）：**标签在页面头部选**，悬浮窗不再重复选标签。
  //   语义：本次采集/私信统一按这个标签取参数（后端 scope_override）。
  const [crawlTag, setCrawlTag] = useState("");

  // ★ 2026-10-03：搜索视频时**同步匿名采集**评论（只采不发私信）。
  //
  // 用户定义：「搜索按钮，搜索时就需要使用匿名凭证同步采集评论，只采集，不私信」。
  // 设计要点：
  //  · **零凭证**（`/api/crawl/comments/anon-preview` 不需要 account）⇒ 不落账号
  //    风控面；这是它相对真凭证采集的核心价值。
  //  · **只采不发**：匿名数据无数字 uid（后端日志原文「仅供预览，不可翻页/无私信
  //    uid」），**不可能**进私信队列 —— 私信只在用户主动开「采集后自动私信」时
  //    由真凭证采集结果驱动。
  //  · 与搜索**并行**发起，不阻塞结果渲染（否则又变成「等渲染完才采」）。
  //  · 失败**如实提示**，不静默（禁假成功）。
  // 搜索结果区的匿名预览读数（供卡片角标显示「预览 N 条」）
  const [anonPreview, setAnonPreview] = useState<Record<string, number>>({});
  const [anonLoading, setAnonLoading] = useState(false);
  // renderQ（普通函数）把「当前结果的作品 id」写到这儿；
  // 组件层的 effect 监听它并发起探针（**Hook 只能在组件顶层用**）。
  const anonLoadedRef = useRef<string[] | null>(null);
  const anonFiredRef = useRef<string>("");
  const runAnonProbe = async (ids: string[]) => {
    const todo = ids.filter(Boolean).slice(0, 12);   // 上限 12：探针不是全量采集
    if (!todo.length) return;
    setAnonLoading(true);
    try {
      const r = await props.api.crawlCommentsAnonPreview({ aweme_ids: todo });
      const m: Record<string, number> = {};
      (r.per_work || []).forEach((w: { aweme_id: string; count?: number; status: string }) => {
        if (w.status === "ok") m[w.aweme_id] = w.count || 0;
      });
      setAnonPreview((prev) => ({ ...prev, ...m }));
      const hit = Object.values(m).filter((n) => n > 0).length;
      props.push(
        `匿名预览完成：${hit}/${todo.length} 个作品有评论（零凭证，不会发私信）`,
        5000,
      );
    } catch (e) {
      props.push(`匿名预览失败：${(e as Error)?.message || e}`, 6000);
    } finally {
      setAnonLoading(false);
    }
  };

  // ★ 2026-10-03：搜索结果就绪 → 发起匿名探针（**effect 驱动**，非渲染期副作用）。
  //
  // 判据说明：
  //  · effect 只在「提交了新搜索」时重跑（依赖 = submitted）；renderQ 在渲染期
  //    把结果 id 写进 anonLoadedRef，随后本 effect 读取并发起探针。
  //  · `anonFiredRef` 存「已探针的结果指纹」⇒ 同一批结果**只探一次**
  //    （否则每次重渲染都会重发，风控面白白放大 + 日志噪音）。
  useEffect(() => {
    const ids = anonLoadedRef.current;
    if (!ids || !ids.length) return;
    const fp = ids.join(",");
    if (anonFiredRef.current === fp) return;
    anonFiredRef.current = fp;
    void runAnonProbe(ids);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [submitted]);

  const openAweme = async (it: AwemeItem) => {
    setPlayerErr("");
    setPlayerOpen(true);
    setSelectedAweme(it.aweme_id || "");
    // ★ ADR-033：嵌入模式下把选中作品回传宿主（采集页据此设为采集目标），
    //   避免「内容 tab 里点开作品、切回采集 tab 还得重新搜一遍」的割裂。
    if (embedded && it.aweme_id) props.onSelectAweme?.(it.aweme_id);
    setPlayerMedia({ type: "video", aweme_id: it.aweme_id, cover: it.cover, desc: it.desc });
    try {
      // ★ 2026-09-21 方案 B：换 **本机同源流地址**，而不是直接用 CDN 直链。
      //   原因（实测）：<video src="https://v26-web.douyinvod.com/..."> 是跨域
      //   请求，webview 受 CORS/Referer 约束 → 后端能取到 200 的真实地址，
      //   播放器仍失败。改由本机后端带正确 headers 转发流，前端同源通信。
      const t = await platformApi.mediaStreamTicket(
        account, it.aweme_id, "origin",
        (it as AwemeItem & { media?: Record<string, unknown> }).media);
      if (!t?.ok || !t.stream_url) throw new Error("取址失败");
      setPlayerMedia({
        type: t.type, aweme_id: it.aweme_id,
        url: platformApi.mediaStreamUrl(t.stream_url),
        images: t.images, live_photos: t.live_photos,
        cover: t.cover || it.cover, duration: t.duration, desc: t.desc || it.desc,
        author: t.author,
        resolved_via: t.resolved_via,
      } as PlayerMedia & { resolved_via?: string });
      if (t.resolved_via === "passthrough") {
        setPlayerErr("未能解析出终极播放地址（该作品可能仅提供会话签名直链，CDN 会拒绝直接请求）");
      }
    } catch (e) {
      setPlayerErr(String((e as Error)?.message || "取址失败"));
    }
  };

  const setAcctPersist = (v: string) => {
    setAcct(v);
    try {
      localStorage.setItem("platform.acct", v);
    } catch {
      /* ignore */
    }
  };

  // 推荐流：仅在该 tab 激活时请求（enabled 控制，避免无用主动请求）
  // ★ 2026-09-30：新增「换一批」——refreshIdx 递增即请求互不重复的新批次。
  // ★ 2026-10-03（用户指令，第二轮）：推荐流**恰好 10 个** + **并行**取批。
  //
  // 用户两点要求：
  //  · 「推荐流只需要 10 个视频卡片就行」—— **精确 10**。原串行补拉实测出 11 个。
  //  · 「能不能并行，串流有点慢」—— 原实现是**串行**补拉（一批到位才拉下一批），
  //    每批一个网络往返（实测每批约 1s）⇒ 凑 10 个要等 3~4 秒。改**并行**。
  //
  // 设计（判据）：
  //  · 用 `useQueries` **并行**拉 `FEED_BATCHES` 个不同 `refresh_index` 的批次；
  //  · 合并时按 `aweme_id` 去重，**累计到 `FEED_TARGET` 即截断**（多余的批次丢弃）⇒ 恰好 10；
  //  · 排序键 = `refresh_index` 升序、批内保持上游顺序 ⇒ 稳定序（避免每次渲染卡片跳动）；
  //  · 上游首批不足 10 ⇒ 再并行追加一段（受 `FEED_MAX_BATCHES` 风控上限约束，只增不减，
  //    旧批数据保留 ⇒ 合并结果单调增长，不会「凑够了又掉回去」）；
  //  · 「换一批」⇒ 起始索引跳过已用段，取全新批次。
  const FEED_TARGET = 30;
  const FEED_BATCHES = 10;        // 每次并行拉多少批（实测每批 2~6 条 ⇒ 6 批通常一次就够 10）
  const FEED_MAX_BATCHES = 20;   // 上游持续不足时的最大批数（风控上限：主动请求要可控）
  const [feedBase, setFeedBase] = useState(2);
  const [feedSpan, setFeedSpan] = useState(FEED_BATCHES);
  const feedIndices: number[] = useMemo(
    () => Array.from({ length: feedSpan }, (_, k) => feedBase + k),
    [feedBase, feedSpan],
  );
  const feedQueries: { data?: { items?: AwemeItem[]; filtered?: number }; isPending: boolean; isFetching: boolean; isError: boolean; error?: unknown; refetch: () => void }[] = useQueries({
    queries: feedIndices.map((ri) => ({
      queryKey: ["platform-feed", account, ri],
      queryFn: () => platformApi.feed(account, 20, ri),
      enabled: !!account && tab === "feed",
      staleTime: 120_000,
    })),
  });
  const feedAnyFetching = feedQueries.some((q) => q.isFetching);

  // 合并：去重 → 按 refresh_index 排序 → **精确截断到 FEED_TARGET**。
  // （不用 useMemo：`feedQueries` 每次渲染都是新数组引用，做依赖会每轮重算；
  //   上限 12 批 × 约 6 条，开销可忽略，直接算更简单也更不易错。）
  const _feedMerged: { ri: number; item: AwemeItem }[] = [];
  const _feedSeen = new Set<string>();
  feedIndices.forEach((ri, k) => {
    const d = feedQueries[k]?.data;
    if (!d?.items?.length) return;
    for (const it of d.items as AwemeItem[]) {
      const id = it?.aweme_id;
      if (!id || _feedSeen.has(id)) continue;
      _feedSeen.add(id);
      _feedMerged.push({ ri, item: it });
    }
  });
  _feedMerged.sort((a, b) => a.ri - b.ri);
  const feedItems: AwemeItem[] = _feedMerged.slice(0, FEED_TARGET).map((x) => x.item);
  const feedFiltered = feedQueries.reduce(
    (n, q) => n + (typeof q.data?.filtered === "number" ? q.data.filtered : 0), 0);
  const feedLoaded = feedQueries.filter((q) => (q.data?.items?.length ?? 0) > 0).length;

  // 上游首批不足 10 ⇒ 并行追加一段。`expandedAtRef` 防同一 feedSpan 反复触发。
  const expandedAtRef = useRef(0);
  useEffect(() => {
    if (!account || tab !== "feed") return;
    if (feedAnyFetching) return;
    if (feedItems.length >= FEED_TARGET) return;
    if (feedSpan >= FEED_MAX_BATCHES) return;
    if (expandedAtRef.current === feedSpan) return;
    expandedAtRef.current = feedSpan;
    setFeedSpan((n) => Math.min(n + FEED_BATCHES, FEED_MAX_BATCHES));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [feedAnyFetching, feedItems.length, feedSpan, account, tab]);

  // renderQ 需要的最小契约（多批任一批未就绪即视为「加载中」）。
  const feedQ = {
    isPending: !!account && tab === "feed" && feedQueries.every((q) => q.isPending),
    isError: feedQueries.length > 0 && feedQueries.every((q) => q.isError),
    error: feedQueries.find((q) => q.isError)?.error,
    data: { items: feedItems as unknown[] },
    refetch: () => { feedQueries.forEach((q) => void q.refetch()); },
  };


  const searchQ = useQuery({
    // ★ 2026-10-04：筛选值必须进 queryKey —— 否则改了筛选**不会重新查询**
    //   （react-query 只在 key 变化时失效），表现为「点了筛选没反应」。
    queryKey: ["platform-search", account, submitted?.q, submitted?.kind,
               order, pt, dur],
    queryFn: () => platformApi.search(
      account, submitted!.q, submitted!.kind, 20,
      // 空值沿用「留空 = 用采集策略」的既有约定（不传空串覆盖策略）
      { sort_type: order || undefined, publish_time: pt || undefined,
        filter_duration: dur || undefined }),
    enabled: !!account && !!submitted?.q,
    staleTime: 120_000,
  });

  const worksQ = useQuery({
    queryKey: ["platform-works", account, worksUrl],
    queryFn: () => platformApi.userWorks(account, worksUrl!, 50),
    enabled: !!account && !!worksUrl,
    staleTime: 300_000,
  });

  const likedQ = useQuery({
    queryKey: ["platform-liked", account],
    queryFn: () => platformApi.liked(account, "", 18),
    enabled: !!account && tab === "liked",
    staleTime: 300_000,
  });

  // 「收藏」——直接列收藏作品（2026-09-21 新端点 `/favorite`，不依赖文件夹）
  const favoriteQ = useQuery({
    queryKey: ["platform-favorite", account],
    queryFn: () => platformApi.favorite(account, "", 30),
    enabled: !!account && tab === "collected",
    staleTime: 300_000,
  });

  // 2026-09-21 补齐：后端已实现、前端此前无入口（合集 / 关注列表 / 互动）
  const [pickedMix, setPickedMix] = useState<{ id: string; name: string } | null>(null);
  const [relationOf, setRelationOf] = useState<{ uid: string; sec: string; name: string } | null>(null);
  const [relationKind, setRelationKind] = useState<"follower" | "following">("follower");
  const [actionBusy, setActionBusy] = useState<string>("");
  // 关注列表查询（2026-09-21 补）
  const relationQ = useQuery({
    queryKey: ["platform-relation", account, relationOf?.uid, relationKind],
    queryFn: () => platformApi.relationList(
      account, relationOf!.uid, relationOf!.sec, relationKind, 20),
    enabled: !!account && !!relationOf?.uid,
    staleTime: 300_000,
  });
  const collectMixesQ = useQuery({
    queryKey: ["platform-mixes", account],
    queryFn: () => platformApi.collectMixes(account, 20),
    enabled: !!account && tab === "mixes",
    staleTime: 300_000,
  });
  // 合集内作品（2026-09-21 补：点合集卡片进入）
  const seriesQ = useQuery({
    queryKey: ["platform-series", account, pickedMix?.id],
    queryFn: () => platformApi.collectionSeries(account, pickedMix!.id, 20),
    enabled: !!account && !!pickedMix?.id,
    staleTime: 300_000,
  });

  // ── 互动（赞 / 藏）：平台写接口实测常返空，失败要明确提示，不假装成功 ──
  // 返回值对齐 `FullscreenPlayer` 的契约：`Promise<boolean>` 表示是否成功。
  const doAction = async (kind: "digg" | "collect", awemeId: string, label: string): Promise<boolean> => {
    if (!awemeId) return false;
    setActionBusy(`${kind}:${awemeId}`);
    try {
      const r = kind === "digg"
        ? await platformApi.digg(account, awemeId, "1")
        : await platformApi.collect(account, awemeId, "1");
      const ok = r?.ok === true;
      // ★ 2026-09-26：按后端透传的**平台原始语义**给文案，不再一律
      //   「平台侧可能已限流」（实测是 status_code=8「用户未登录」——
      //   纯 HTTP 通道不具备写权限，需浏览器容器态凭证，与限流无关）。
      let msg: string;
      if (ok) {
        msg = `${label}成功`;
      } else if (kind === "digg" && "status_code" in (r as object)) {
        const d = r as { status_code?: number | null; status_msg?: string };
        if (d.status_code === 8 || /未登录/.test(d.status_msg || "")) {
          msg = `${label}未生效：当前通道不可写（平台返回「用户未登录」）。`
              + `互动写操作需浏览器容器态凭证，请先在「账号」页启动凭证守护并确认登录态。`;
        } else if (d.status_code === 0) {
          msg = `${label}成功`;
        } else {
          msg = `${label}未生效：平台返回 ${d.status_code ?? "未知"}`
              + `${d.status_msg ? `（${d.status_msg}）` : ""}`;
        }
      } else {
        msg = `${label}未返回成功（平台侧可能已限流）`;
      }
      props.push(msg, ok ? 3000 : 6000);
      return ok;
    } catch (e) {
      props.push(`${label}失败：${String((e as Error)?.message || e).slice(0, 120)}`, 6000);
      return false;
    } finally {
      setActionBusy("");
    }
  };

  const noticesQ = useQuery({
    queryKey: ["platform-notices", account],
    queryFn: () => platformApi.notices(account, 20),
    enabled: !!account && tab === "notices",
    staleTime: 60_000,
  });

  if (!account) {
    // 嵌入模式下不套 PageContainer（宿主已有），只给提示
    const noAcct = (
      <EmptyState title="还没有账号" description="请先在「账号」页添加并登录一个账号。" />
    );
    if (embedded) return noAcct;
    return (
      <PageContainer>
        <PageHeader title="内容浏览" description="推荐流 / 搜索 / 用户作品 / 点赞 / 收藏 / 站内通知" />
        {noAcct}
      </PageContainer>
    );
  }

  const renderQ = (
    q: { isPending: boolean; isError: boolean; error: unknown;
         data?: { items: unknown[]; blocked?: boolean; blocked_reason?: string | null };
         refetch: () => void },
    kind: "video" | "user",
    checkedIds?: Record<string, boolean>,
    onToggleCheck?: (id: string, checked: boolean) => void,
    // ★ 2026-10-03：结果就绪回调（匿名探针用它并行发起，不阻塞渲染）
    opts?: { onLoaded?: (ids: string[]) => void;
            /** ★ 2026-10-03：覆盖渲染数据（推荐流用累积列表，不改 renderQ 既有契约） */
            itemsOverride?: (AwemeItem | UserItem)[] },
  ) => {
    if (q.isPending) return <LoadingState />;
    if (q.isError) {
      return <ErrorState message={String((q.error as Error)?.message || "请求失败")} onRetry={q.refetch} />;
    }
    const items = (opts?.itemsOverride ?? q.data?.items ?? []) as (AwemeItem | UserItem)[];
    // ★ 2026-10-03：结果就绪后触发回调（匿名探针）。
    //
    // 🔴 `renderQ` 是**普通函数**（在组件渲染期被调用），**绝不能**在里面用
    //   Hook（`useRef` 等）—— Hook 数量会随分支变化，违反 Hooks 规则，
    //   轻则警告、重则 React 崩溃。故副作用交给**外层组件的 effect**：
    //   这里只负责把「当前结果的作品 id」写进一个模块级回调注册点，
    //   由下面的 `anonProbeEffect` 统一监听。
    anonLoadedRef.current =
      opts?.onLoaded && items.length
        ? (items as AwemeItem[]).map((it) => it.aweme_id).filter(Boolean)
        : null;
    // ★ 2026-09-27（M-20 · 铁律「禁假成功」）：被风控拦截 ≠ 真的没搜到。
    //   此前一律渲染空 Grid ⇒ 用户以为「没这个关键词的作品」，
    //   实际是被 Argus 拦了。现**优先**如实呈现被拦截事实。
    if (q.data?.blocked) {
      return (
        <div className="flex items-start gap-2 rounded-[var(--radius-sm)]
                        border border-[var(--color-danger)] px-3 py-2.5
                        text-sm text-[var(--color-danger)]"
             data-od-id="platform-search-blocked">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <div className="space-y-1">
            <div className="font-medium">搜索被平台风控拦截</div>
            <div className="text-xs opacity-90">
              {q.data.blocked_reason || "被风控拦截，请稍后重试。"}
            </div>
            <div className="text-xs opacity-75">
              这不是「没有结果」——请降低频率、稍后再试，或在浏览器完成验证。
            </div>
          </div>
        </div>
      );
    }
    return <Grid items={items} kind={kind}
                 onOpenAweme={kind === "video" ? openAweme : undefined}
                 checkedIds={checkedIds}
                 onToggleCheck={onToggleCheck}
                 previewCounts={kind === "video" ? anonPreview : undefined} />;
  };

  // ★ ADR-034：嵌入模式下不渲染 PageContainer / PageHeader / 账号下拉 ——
  //   三者都由宿主提供，否则会出现「一个页面两个标题栏」。
  const header = embedded ? null : (
    <PageHeader
      title="内容总览"
      description="推荐流 / 搜索 / 作品 / 点赞 / 收藏 / 通知"
      actions={
        <div className="flex items-center gap-2">
          <Select value={account} onValueChange={setAcctPersist}>
            <SelectTrigger className="h-9 w-[180px]"><SelectValue /></SelectTrigger>
            <SelectContent>
              {accounts.map((n) => <SelectItem key={n} value={n}>{n}</SelectItem>)}
            </SelectContent>
          </Select>
        </div>
      }
    />
  );

  const body = (
    <>
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="feed"><LayoutGrid className="h-3.5 w-3.5" />推荐流</TabsTrigger>
          <TabsTrigger value="search"><SearchIcon className="h-3.5 w-3.5" />搜索</TabsTrigger>
          <TabsTrigger value="works"><User className="h-3.5 w-3.5" />用户作品</TabsTrigger>
          <TabsTrigger value="liked"><Heart className="h-3.5 w-3.5" />点赞</TabsTrigger>
          <TabsTrigger value="collected"><Star className="h-3.5 w-3.5" />收藏夹</TabsTrigger>
          <TabsTrigger value="mixes"><BookOpen className="h-3.5 w-3.5" />合集</TabsTrigger>
          <TabsTrigger value="relation"><Users className="h-3.5 w-3.5" />粉丝/关注</TabsTrigger>
          <TabsTrigger value="notices"><Bell className="h-3.5 w-3.5" />站内通知</TabsTrigger>
          {/* ★ ADR-034 方向修正（2026-10-03，用户指令「button[9] 删除」）：
              独立「采集」二级 tab 已撤除 —— 采集不再是「一个页面」，
              而是**悬浮窗 + 各 tab 就地勾选**（入口在各 tab 工具条上的「采集」按钮）。
              理由：① 用户要求删除该 tab；② 采集跨 tab（feed/search/works/liked/
              collected 都能勾选），塞进单个 tab 反而割裂；③ 避免 tab 套 tab。 */}
        </TabsList>

        <TabsContent value="feed">
          {/* ★ 2026-09-30：推荐流「换一批」按钮（用户报障：只有 6 个且没有刷新按钮）。
              上游 refresh_index 是换一批旋钮，递增即取新批次。 */}
          <div className="mb-3 flex items-center justify-between">
            <span className="text-[0.78rem] text-[var(--color-text-secondary)]">
              {feedItems.length
                ? `${feedItems.length} 个作品`
                  + (feedItems.length < FEED_TARGET ? `（目标 ${FEED_TARGET}）` : "")
                  + (feedLoaded > 1 ? ` · 并行 ${feedLoaded} 批` : "")
                : ""}
              {feedFiltered > 0 ? `（已过滤 ${feedFiltered} 个不可播条目）` : ""}
            </span>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="secondary"
                disabled={feedAnyFetching}
                onClick={() => {
                  // 换一批：起始索引跳过本次已用过的整段 ⇒ 取全新批次
                  setFeedBase((b) => b + feedSpan);
                  setFeedSpan(FEED_BATCHES);
                  expandedAtRef.current = 0;
                }}
              >
                <RefreshCw className={`h-3.5 w-3.5 ${feedAnyFetching ? "animate-spin" : ""}`} />
                换一批
              </Button>
              {/* ★ 2026-10-03：采集入口 */}
              <Button
                size="sm"
                variant="outline"
                onClick={() => setCrawlPanelOpen(true)}
                title="批量采集推荐流中的作品"
              >
                <MessageSquare className="h-3.5 w-3.5" />
                采集
              </Button>
            </div>
          </div>
          {renderQ(feedQ, "video", checkedIds, handleToggleCheck, { itemsOverride: feedItems })}
        </TabsContent>

        <TabsContent value="search">
          <div className="mb-3 flex items-center gap-2">
            <Select value={searchKind} onValueChange={(v) => setSearchKind(v as "video" | "user")}>
              <SelectTrigger className="h-9 w-[110px]"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="video">视频</SelectItem>
                <SelectItem value="user">用户</SelectItem>
              </SelectContent>
            </Select>
            <Input
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && query.trim()) {
                  setSubmitted({ q: query.trim(), kind: searchKind });
                  setAnonPreview({});   // 新一轮搜索 ⇒ 清掉上一轮预览读数
                }
              }}
              placeholder="输入关键词后回车…"
              className="max-w-md"
            />
            <Button
              size="sm"
              onClick={() => query.trim() && setSubmitted({ q: query.trim(), kind: searchKind })}
            >
              搜索
            </Button>
            {/* ★ 2026-10-03：采集入口 */}
            <Button
              size="sm"
              variant="outline"
              onClick={() => setCrawlPanelOpen(true)}
              title="批量采集搜索结果"
            >
              <MessageSquare className="h-3.5 w-3.5" />
              采集
            </Button>
          </div>
          {/* ★ 2026-10-03：筛选器（从采集页移植） */}
          {searchKind === "video" && (
            <div className="mb-3 flex flex-wrap items-center gap-4 text-[0.74rem] text-[var(--color-text-secondary)]">
              <label className="flex items-center gap-1.5">
                排序
                <Select value={order} onValueChange={setOrder}>
                  <SelectTrigger className="w-[118px]"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {ORDER_OPTS.map((o) => (
                      <SelectItem key={o.v} value={o.v}>{o.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </label>
              <label className="flex items-center gap-1.5">
                发布时间
                <Select value={pt} onValueChange={setPt}>
                  <SelectTrigger className="w-[110px]"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {PT_OPTS.map((o) => (
                      <SelectItem key={o.v} value={o.v}>{o.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </label>
              <label className="flex items-center gap-1.5">
                视频时长
                <Select value={dur || "__all"} onValueChange={(v) => setDur(v === "__all" ? "" : v)}>
                  <SelectTrigger className="w-[120px]"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    {DUR_OPTS.map((o) => (
                      <SelectItem key={o.v || "__all"} value={o.v || "__all"}>{o.label}</SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </label>
            </div>
          )}
          {submitted
            ? renderQ(searchQ, submitted.kind, checkedIds, handleToggleCheck, {
                // ★ 匿名探针：视频搜索结果到达后**并行**发起，不阻塞渲染。
                //   只有 kind==="video" 有评论可采；用户搜索不触发。
                onLoaded: (ids: string[]) => {
                  if (submitted.kind === "video") void runAnonProbe(ids);
                },
              })
            : <EmptyState title="输入关键词开始搜索" description="回车或点「搜索」。结果自带作者昵称，不会额外查询。" />}
          {anonLoading && (
            <p className="mt-2 text-[0.7rem] text-[var(--color-text-muted)]">
              匿名预览采集中…（零凭证，不会发私信）
            </p>
          )}
        </TabsContent>

        <TabsContent value="works">
          <div className="mb-3 flex items-center gap-2">
            <Input
              value={userUrl}
              onChange={(e) => setUserUrl(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter" && userUrl.trim()) setWorksUrl(userUrl.trim()); }}
              placeholder="粘贴用户主页链接或 sec_uid，回车…"
              className="max-w-lg"
            />
            <Button size="sm" onClick={() => userUrl.trim() && setWorksUrl(userUrl.trim())}>
              获取作品
            </Button>
            {/* ★ 2026-10-03：采集入口 */}
            <Button
              size="sm"
              variant="outline"
              onClick={() => setCrawlPanelOpen(true)}
              title="批量采集该用户的作品"
            >
              <MessageSquare className="h-3.5 w-3.5" />
              采集
            </Button>
          </div>
          {worksUrl
            ? (worksQ.isSuccess && !worksQ.data?.items?.length ? (
                // 2026-09-15：实测「查自己主页」平台返回 sc=0 但 aweme_list 为空，
                // 这不是失败而是限制 —— 给出明确提示而非空白，避免用户误判功能坏了。
                <EmptyState
                  title="未取到作品"
                  description="可能原因：① 填的是自己账号（抖音限制：查自己主页不返回作品，请填他人主页）；② 平台限流（同一账号短时多次查询会临时返回空，稍后重试即可）；③ 该用户无公开作品；④ sec_uid 有误。"
                />
              ) : renderQ(worksQ, "video", checkedIds, handleToggleCheck))
            : <EmptyState title="填入用户主页链接" description="支持主页 URL 或 sec_uid。作品列表会一次拉全（含翻页）。" />}
        </TabsContent>

        <TabsContent value="liked">
          {likedQ.isSuccess && !likedQ.data?.items?.length ? (
            // ★ 2026-09-26：优先展示后端给出的**真实原因**（unavailable.reason），
            //   无原因时才是「真的没有点赞作品」。
            <EmptyState
              title={(likedQ.data as { unavailable?: boolean })?.unavailable
                ? "暂时取不到点赞列表" : "暂无点赞作品"}
              description={
                (likedQ.data as { unavailable?: boolean; reason?: string })?.reason
                || "该账号还没有点赞过的作品（接口已正常返回）。"}
            />
          ) : renderQ(likedQ, "video", checkedIds, handleToggleCheck)}
        </TabsContent>

        <TabsContent value="collected">
          {/* 2026-09-21 用户反馈重做：不再要求「收藏夹文件夹」——
              多数人收藏就是一堆作品，不专门建文件夹（该账号实测文件夹数 = 0，
              但收藏作品有 19 条）。改为直接列收藏作品。 */}
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-[0.78rem] text-[var(--color-text-secondary)]">
                你的收藏作品（直接列出，无需先建文件夹）
              </span>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setCrawlPanelOpen(true)}
                title="批量采集收藏作品"
              >
                <MessageSquare className="h-3.5 w-3.5" />
                采集
              </Button>
            </div>
            {favoriteQ.isPending ? <LoadingState /> :
              favoriteQ.isError ? (
                <ErrorState message={String((favoriteQ.error as Error)?.message)}
                            onRetry={favoriteQ.refetch} />
              ) : (favoriteQ.data?.items?.length
                ? <Grid items={favoriteQ.data.items} kind="video" onOpenAweme={openAweme}
                         checkedIds={checkedIds} onToggleCheck={handleToggleCheck} />
                : <EmptyState
                    title={(favoriteQ.data as { unavailable?: boolean })?.unavailable
                      ? "暂时取不到收藏" : "暂无收藏"}
                    description={
                      (favoriteQ.data as { unavailable?: boolean; reason?: string })?.reason
                      || "该账号还没有收藏作品。"} />)}
          </div>
        </TabsContent>

        {/* 合集（mix）—— 与「收藏夹」是两种实体：合集是作者自建的作品集 */}
        <TabsContent value="mixes">
          {pickedMix ? (
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <Button size="sm" variant="ghost" onClick={() => setPickedMix(null)}>
                  ← 返回合集
                </Button>
                <span className="text-[0.8rem] text-[var(--color-text-secondary)]">
                  {pickedMix.name}
                </span>
              </div>
              {seriesQ.isPending ? <LoadingState /> :
                seriesQ.isError ? (
                  <ErrorState message={String((seriesQ.error as Error)?.message)}
                              onRetry={seriesQ.refetch} />
                ) : (seriesQ.data?.items?.length
                  ? <Grid items={seriesQ.data.items} kind="video" onOpenAweme={openAweme}
                           checkedIds={checkedIds} onToggleCheck={handleToggleCheck} />
                  : <EmptyState title="该合集暂无作品" />)}
            </div>
          ) : collectMixesQ.isPending ? <LoadingState /> :
            collectMixesQ.isError ? (
              <ErrorState message={String((collectMixesQ.error as Error)?.message)}
                          onRetry={collectMixesQ.refetch} />
            ) : (collectMixesQ.data?.items?.length ? (
              <div className="grid grid-cols-3 gap-3">
                {collectMixesQ.data.items.map((m) => (
                  <Card key={m.mix_id}
                        className="cursor-pointer transition-colors hover:border-[var(--color-accent)]"
                        onClick={() => setPickedMix({ id: m.mix_id, name: m.mix_name || "未命名" })}>
                    <CardContent className="p-3">
                      <div className="text-[0.84rem] font-medium text-[var(--color-text)]">
                        {m.mix_name || "未命名"}
                      </div>
                      <div className="mt-1 text-[0.72rem] text-[var(--color-text-secondary)]">
                        {fmtNum(m.item_total)} 个作品 · 点击查看
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            ) : <EmptyState title="暂无收藏合集" description="该账号没有收藏合集，或合集为空。" />)}
        </TabsContent>

        {/* 粉丝 / 关注（2026-09-21 补：后端 /relation/list 早已实现，前端无入口） */}
        <TabsContent value="relation">
          <div className="mb-3 flex items-center gap-2">
            <Input
              value={userUrl}
              onChange={(e) => setUserUrl(e.target.value)}
              placeholder="粘贴用户主页链接或 sec_uid，回车查看 TA 的粉丝/关注…"
              className="max-w-lg"
            />
            <Button size="sm" onClick={() => {
              const v = userUrl.trim();
              if (!v) return;
              // sec_uid 可直接用；主页链接则先取用户信息换 sec_uid
              const sec = v.startsWith("MS4w") ? v : "";
              if (sec) {
                setRelationOf({ uid: "", sec, name: "该用户" });
              } else {
                platformApi.userInfo(account, v)
                  .then((r) => setRelationOf({
                    uid: String((r.user as { uid?: string })?.uid || ""),
                    sec: r.user?.sec_uid || "",
                    name: r.user?.nickname || "该用户",
                  }))
                  .catch(() => props.push("获取用户信息失败，请检查链接或 sec_uid", 6000));
              }
            }}>查看</Button>
            {relationOf && (
              <Select value={relationKind}
                      onValueChange={(v) => setRelationKind(v as "follower" | "following")}>
                <SelectTrigger className="h-9 w-[120px]"><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="follower">粉丝</SelectItem>
                  <SelectItem value="following">关注</SelectItem>
                </SelectContent>
              </Select>
            )}
          </div>
          {relationOf ? (
            relationQ.isPending ? <LoadingState /> :
            relationQ.isError ? (
              <ErrorState message={String((relationQ.error as Error)?.message)}
                          onRetry={relationQ.refetch} />
            ) : (relationQ.data?.items?.length
              ? <div className="grid grid-cols-2 gap-2">
                  {relationQ.data.items.map((u, i) => (
                    <UserCard key={u.uid || i} item={u} />
                  ))}
                </div>
              : <EmptyState title="暂无数据"
                    description="平台侧对粉丝/关注列表常返回空（非本项目缺陷），或对非本人账号不开放。" />)
          ) : (
            <EmptyState title="填入用户主页链接或 sec_uid"
              description="注意：平台一般只对本人账号返回关注列表，查他人常为空。" />
          )}
        </TabsContent>

        <TabsContent value="notices">
          {noticesQ.isPending ? <LoadingState /> :
            noticesQ.isError ? <ErrorState message={String((noticesQ.error as Error)?.message)} onRetry={noticesQ.refetch} /> :
            // ★ 2026-09-26 修复：服务端返回 sc=8「用户未登录」（本机实测：
            //   cookie sid_guard 已过期）时，原实现直接落到「暂无通知」，
            //   把「登录过期」伪装成「本来就没通知」——用户无从察觉需重新扫码。
            //   与「点赞 / 收藏」tab 同一降级范式：显式说明真实原因。
            (noticesQ.data as { unavailable?: boolean; reason?: string })?.unavailable ? (
              <EmptyState
                title="暂时取不到站内通知"
                description={(noticesQ.data as { reason?: string })?.reason
                  || "未能确认本人登录态，请到「账号管理」检查该账号是否需要重新扫码。"} />
            ) :
            (noticesQ.data?.items?.length ? (
              <div className="space-y-2">
                {noticesQ.data.items.map((n: NoticeItem) => (
                  <Card key={n.notice_id}>
                    <CardContent className="flex items-start gap-3 p-3">
                      <Badge variant={n.is_read ? "outline" : "accent"}>{n.type || "通知"}</Badge>
                      <div className="min-w-0 flex-1">
                        <div className="text-[0.8rem] leading-relaxed text-[var(--color-text)]">
                          {n.content || "（无内容）"}
                        </div>
                        <div className="mt-1 text-[0.68rem] text-[var(--color-text-muted)]">
                          {n.create_time ? fmtAgo(n.create_time) : ""}
                        </div>
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            ) : <EmptyState title="暂无通知" />)}
        </TabsContent>

      </Tabs>

      {/* ★ 2026-10-03：采集悬浮窗（独立于各 tab，统一入口） */}
      <CrawlFloatingPanel
        open={crawlPanelOpen}
        onClose={() => setCrawlPanelOpen(false)}
        selectedIds={getCheckedIds()}
        currentAccount={account}
        tagId={crawlTag}
        onTagChange={setCrawlTag}
        props={props}
      />

      {/* 播放器浮层（照源项目 components/player 的独立业务域形态） */}
      {playerOpen && (
        <div className="fixed inset-0 z-[var(--z-view)] flex items-center justify-center modal-scrim p-6"
             onClick={() => setPlayerOpen(false)}>
          {/* ★ 2026-09-27（F3）：播放器 + 评论同屏，容器加宽到 max-w-5xl
              ★ 2026-10-02：材质改 card-surface（原 bg-[var(--color-surface)] 是极淡半透明，
                在光晕底上几乎看不见 → 用户实测「不适配主题」）。 */}
          <div className="card-surface flex h-[70vh] w-full max-w-5xl flex-col overflow-hidden rounded-[var(--radius-lg)]"
               onClick={(e) => e.stopPropagation()}>
            {playerErr ? (
              <div className="flex flex-1 items-center justify-center text-sm text-[var(--color-text-muted)]">
                取址失败：{playerErr}
              </div>
            ) : (
              <FullscreenPlayer
                media={playerMedia}
                author={playerMedia?.author}
                onClose={() => setPlayerOpen(false)}
                // ★ 2026-09-21：此前从未注入赞/藏回调 → 按组件契约按钮
                //   **根本不渲染**（"未注入则不渲染，避免假可用"）。
                //   现接上真实写接口；平台侧常返空，失败由 doAction 明确提示。
                onLike={
                  actionBusy.startsWith("digg:")
                    ? undefined
                    : (id) => doAction("digg", id, "点赞")
                }
                onCollect={
                  actionBusy.startsWith("collect:")
                    ? undefined
                    : (id) => doAction("collect", id, "收藏")
                }
                onDownload={() => { /* 交由后端 downloader（后续接线） */ }}
              />
            )}
          </div>
          {/* ★ 2026-09-27（ADR-018 F3）：评论与播放**同屏同时**展示。
              加宽浮层（max-w-3xl → max-w-5xl）为评论留出右侧栏；
              CommentPanel 挂载即取评论，与播放器取址并发，
              不是切 Tab 才加载。
              ★ 2026-09-28：评论区改走**采集页同款一级评论**路径
              （api.crawlComments），不再自己走 comments/full + 串行楼中楼。 */}
          <aside
            className="card-surface ml-4 hidden h-[70vh] w-[22rem] shrink-0 flex-col overflow-hidden rounded-[var(--radius-lg)] p-3 md:flex"
            onClick={(e) => e.stopPropagation()}
          >
            <CommentPanel
              account={account}
              awemeId={selectedAweme}
              api={props.api as never}
              push={props.push}
            />
          </aside>
        </div>
      )}
    </>
  );

  if (embedded) return body;
  return (
    <PageContainer>
      {header}
      {body}
    </PageContainer>
  );
}
