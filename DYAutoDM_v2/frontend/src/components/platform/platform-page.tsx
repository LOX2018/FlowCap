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
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  LayoutGrid, Search as SearchIcon, Heart, Star, Bell, User, MessageSquare,
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
// 采集 = 内容浏览的「高级模式」（用户 2026-09-15 决策：采集页合并进内容浏览）
import { CrawlPanel } from "./crawl-panel";
import { fmtNum, fmtAgo } from "@/lib/utils";
import type { PageProps } from "@/api/client";
import { FullscreenPlayer } from "@/components/player";
import type { PlayerMedia } from "@/components/player";

/** 作品卡片（封面 + 统计；点击打开播放器）。 */
import { Grid } from "./platform-cards";

export default function PlatformPage(props: PageProps) {
  // ★ 账号来源（2026-09-14 修复）：原用 `props.overview.accounts`，
  //   但 `/api/overview` **不返回 accounts 字段** → 内容页恒显示「还没有账号」，
  //   而私信页（走 `/api/accounts`）却正常。现统一为本项目的账号真源。
  const accountsQ = useQuery({
    queryKey: ["platform-accounts"],
    queryFn: async (): Promise<{ name: string }[]> =>
      (await props.api.getAccounts()) as unknown as { name: string }[],
    enabled: !!props.ready,
    staleTime: 300_000,
  });
  const accounts = (accountsQ.data || []).map((a) => a.name).filter(Boolean);
  const [acct, setAcct] = useState<string>(() => {
    try {
      return localStorage.getItem("platform.acct") || "";
    } catch {
      return "";
    }
  });
  const account = acct || accounts[0] || "";
  const [tab, setTab] = useState("feed");
  const [query, setQuery] = useState("");
  const [searchKind, setSearchKind] = useState<"video" | "user">("video");
  const [userUrl, setUserUrl] = useState("");
  const [submitted, setSubmitted] = useState<{ q: string; kind: "video" | "user" } | null>(null);
  const [worksUrl, setWorksUrl] = useState<string | null>(null);
  // ── 播放器（★ 本分支新增：卡片点击 → 取址 → 播放）──
  const [playerMedia, setPlayerMedia] = useState<PlayerMedia | null>(null);
  const [playerOpen, setPlayerOpen] = useState(false);
  // 选中作品（采集模式复用：点开卡片即设为当前采集目标）
  const [selectedAweme, setSelectedAweme] = useState<string>("");
  // 选中的收藏夹（2026-09-15：点卡片 → 加载该收藏夹内作品）
  const [pickedCollect, setPickedCollect] = useState<{ id: string; name: string } | null>(null);
  const [playerErr, setPlayerErr] = useState<string>("");
  const openAweme = async (it: AwemeItem) => {
    setPlayerErr("");
    setPlayerOpen(true);
    setSelectedAweme(it.aweme_id || "");
    setPlayerMedia({ type: "video", aweme_id: it.aweme_id, cover: it.cover, desc: it.desc });
    try {
      // ★ 回传列表返回的作品对象（实测自带播放地址；详情接口平台侧返空）
      const r = await platformApi.mediaResolve(account, it.aweme_id, "origin",
                                               (it as AwemeItem & { media?: Record<string, unknown> }).media);
      if (!r?.ok) throw new Error("取址失败");
      setPlayerMedia({
        type: r.type, aweme_id: r.aweme_id, url: r.url,
        images: r.images, live_photos: r.live_photos,
        cover: r.cover || it.cover, duration: r.duration, desc: r.desc || it.desc,
      });
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
  const feedQ = useQuery({
    queryKey: ["platform-feed", account],
    queryFn: () => platformApi.feed(account, 20),
    enabled: !!account && tab === "feed",
    staleTime: 120_000,
  });

  const searchQ = useQuery({
    queryKey: ["platform-search", account, submitted?.q, submitted?.kind],
    queryFn: () => platformApi.search(account, submitted!.q, submitted!.kind, 20),
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

  const collectedQ = useQuery({
    queryKey: ["platform-collected", account],
    queryFn: () => platformApi.collected(account),
    enabled: !!account && tab === "collected",
    staleTime: 300_000,
  });

  // 收藏夹内的作品（2026-09-15 补：点收藏夹卡片才请求，避免无用主动请求）
  //
  // 2026-09-17 修补（OCR 审查 CRITICAL）：queryKey 里带了 `pickedCollect?.id`
  // 但 queryFn 恒传 cursor="0"，且**后端接口
  // `/aweme/v1/web/aweme/listcollection/` 只支持 max_cursor 分页、
  // 不支持按收藏夹筛选**（referer 即 favorite_collection 总列表）——
  // 即抖音该接口返回的是「我的收藏」总表，无法按夹区分。
  // 原实现会让用户以为进了某个夹，实际看到的是全部收藏。
  // 现显式把这一限制暴露出来（提示文案），避免误导；queryKey 与请求参数
  // 保持一致（都不含 id），消除「切夹命中缓存却不刷新」的假象。
  const collectItemsQ = useQuery({
    queryKey: ["platform-collect-items", account],
    queryFn: () => platformApi.collectionItems(account, "0", 20),
    enabled: !!account && !!pickedCollect,
    staleTime: 300_000,
  });

  const noticesQ = useQuery({
    queryKey: ["platform-notices", account],
    queryFn: () => platformApi.notices(account, 20),
    enabled: !!account && tab === "notices",
    staleTime: 60_000,
  });

  if (!account) {
    return (
      <PageContainer>
        <PageHeader title="内容浏览" description="推荐流 / 搜索 / 用户作品 / 点赞 / 收藏 / 站内通知" />
        <EmptyState title="还没有账号" description="请先在「账号」页添加并登录一个账号。" />
      </PageContainer>
    );
  }

  const renderQ = (
    q: { isPending: boolean; isError: boolean; error: unknown; data?: { items: unknown[] }; refetch: () => void },
    kind: "video" | "user",
  ) => {
    if (q.isPending) return <LoadingState />;
    if (q.isError) {
      return <ErrorState message={String((q.error as Error)?.message || "请求失败")} onRetry={q.refetch} />;
    }
    return <Grid items={(q.data?.items || []) as (AwemeItem | UserItem)[]} kind={kind}
                 onOpenAweme={kind === "video" ? openAweme : undefined} />;
  };

  return (
    <PageContainer>
      <PageHeader
        title="内容浏览"
        description="对标 better-douyin 的内容面 · 所有请求均由你的操作触发，不做后台轮询"
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

      <Tabs value={tab} onValueChange={setTab}>
        <TabsList>
          <TabsTrigger value="feed"><LayoutGrid className="h-3.5 w-3.5" />推荐流</TabsTrigger>
          <TabsTrigger value="search"><SearchIcon className="h-3.5 w-3.5" />搜索</TabsTrigger>
          <TabsTrigger value="works"><User className="h-3.5 w-3.5" />用户作品</TabsTrigger>
          <TabsTrigger value="liked"><Heart className="h-3.5 w-3.5" />点赞</TabsTrigger>
          <TabsTrigger value="collected"><Star className="h-3.5 w-3.5" />收藏夹</TabsTrigger>
          <TabsTrigger value="notices"><Bell className="h-3.5 w-3.5" />站内通知</TabsTrigger>
          <TabsTrigger value="crawl"><MessageSquare className="h-3.5 w-3.5" />采集</TabsTrigger>
        </TabsList>

        <TabsContent value="feed">
          {renderQ(feedQ, "video")}
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
          </div>
          {submitted
            ? renderQ(searchQ, submitted.kind)
            : <EmptyState title="输入关键词开始搜索" description="回车或点「搜索」。结果自带作者昵称，不会额外查询。" />}
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
          </div>
          {worksUrl
            ? (worksQ.isSuccess && !worksQ.data?.items?.length ? (
                // 2026-09-15：实测「查自己主页」平台返回 sc=0 但 aweme_list 为空，
                // 这不是失败而是限制 —— 给出明确提示而非空白，避免用户误判功能坏了。
                <EmptyState
                  title="未取到作品"
                  description="可能原因：① 填的是自己账号（抖音限制：查自己主页不返回作品，请填他人主页）；② 平台限流（同一账号短时多次查询会临时返回空，稍后重试即可）；③ 该用户无公开作品；④ sec_uid 有误。"
                />
              ) : renderQ(worksQ, "video"))
            : <EmptyState title="填入用户主页链接" description="支持主页 URL 或 sec_uid。作品列表会一次拉全（含翻页）。" />}
        </TabsContent>

        <TabsContent value="liked">
          {likedQ.isSuccess && !likedQ.data?.items?.length ? (
            // 2026-09-15：实测 /aweme/favorite 平台侧返回空响应（0 字节，非 JSON）。
            // 属平台侧限制，明确告知而非只显示空白。
            <EmptyState
              title="未取到点赞列表"
              description="点赞/喜欢列表接口在平台侧常返回空响应（非本项目缺陷）。可改用「推荐流」「搜索」或他人主页查看内容。"
            />
          ) : renderQ(likedQ, "video")}
        </TabsContent>

        <TabsContent value="collected">
          {pickedCollect ? (
            // ── 收藏夹内作品（2026-09-15：原卡片不可点，用户进不去）──
            <div className="space-y-3">
              <div className="flex items-center gap-2">
                <Button size="sm" variant="ghost" onClick={() => setPickedCollect(null)}>
                  ← 返回收藏夹
                </Button>
                <span className="text-[0.8rem] text-[var(--color-text-secondary)]">
                  {pickedCollect.name}
                </span>
              </div>
              {collectItemsQ.isPending ? <LoadingState /> :
                collectItemsQ.isError ? (
                  <ErrorState
                    message={String((collectItemsQ.error as Error)?.message)}
                    onRetry={collectItemsQ.refetch}
                  />
                ) :
                (collectItemsQ.data?.items?.length
                  ? <Grid items={collectItemsQ.data.items} kind="video" onOpenAweme={openAweme} />
                  : <EmptyState title="该收藏夹暂无作品" />)}
            </div>
          ) : (
            collectedQ.isPending ? <LoadingState /> :
            collectedQ.isError ? <ErrorState message={String((collectedQ.error as Error)?.message)} onRetry={collectedQ.refetch} /> :
            (collectedQ.data?.items?.length ? (
              <div className="grid grid-cols-3 gap-3">
                {collectedQ.data.items.map((c) => (
                  <Card
                    key={c.collects_id}
                    className="cursor-pointer transition-colors hover:border-[var(--color-accent)]"
                    onClick={() => setPickedCollect({ id: c.collects_id, name: c.name || "未命名" })}
                  >
                    <CardContent className="p-3">
                      <div className="text-[0.84rem] font-medium text-[var(--color-text)]">{c.name || "未命名"}</div>
                      <div className="mt-1 text-[0.72rem] text-[var(--color-text-secondary)]">
                        {fmtNum(c.count)} 个作品 · 点击查看
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            ) : <EmptyState title="暂无收藏夹" description="该账号没有收藏夹，或收藏夹为空。" />)
          )}
        </TabsContent>

        <TabsContent value="notices">
          {noticesQ.isPending ? <LoadingState /> :
            noticesQ.isError ? <ErrorState message={String((noticesQ.error as Error)?.message)} onRetry={noticesQ.refetch} /> :
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
        <TabsContent value="crawl">
          {/* 采集 = 内容浏览的高级模式：先搜作品 → 选中 → 采评论 → 私信截流 */}
          <div className="space-y-3">
            <div className="text-[0.78rem] text-[var(--color-text-secondary)]">
              在「搜索」或「推荐流」里点开一个作品，即可在此采集它的评论区并批量私信。
            </div>
            {selectedAweme ? (
              <CrawlPanel
                account={account}
                api={props.api as never}
                awemeId={selectedAweme}
                push={props.push}
              />
            ) : (
              <EmptyState
                title="还没有选中作品"
                description="切到「搜索」或「推荐流」，点开任意作品卡片后回到这里。"
              />
            )}
          </div>
        </TabsContent>

      </Tabs>

      {/* 播放器浮层（照源项目 components/player 的独立业务域形态） */}
      {playerOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6"
             onClick={() => setPlayerOpen(false)}>
          <div className="flex h-[70vh] w-full max-w-3xl flex-col overflow-hidden rounded-[var(--radius-lg)] bg-[var(--color-surface)]"
               onClick={(e) => e.stopPropagation()}>
            {playerErr ? (
              <div className="flex flex-1 items-center justify-center text-sm text-[var(--color-text-muted)]">
                取址失败：{playerErr}
              </div>
            ) : (
              <FullscreenPlayer
                media={playerMedia}
                onClose={() => setPlayerOpen(false)}
                onDownload={() => { /* 交由后端 downloader（后续接线） */ }}
              />
            )}
          </div>
        </div>
      )}
    </PageContainer>
  );
}
