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
  Users, BookOpen,
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
import { Grid, UserCard } from "./platform-cards";

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
  // 选中的收藏夹（2026-09-21 移除：收藏改为直接列作品，不再有「夹」这一层）
  const [playerErr, setPlayerErr] = useState<string>("");
  const openAweme = async (it: AwemeItem) => {
    setPlayerErr("");
    setPlayerOpen(true);
    setSelectedAweme(it.aweme_id || "");
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
      props.push(ok ? `${label}成功` : `${label}未返回成功（平台侧可能已限流）`);
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
          <TabsTrigger value="mixes"><BookOpen className="h-3.5 w-3.5" />合集</TabsTrigger>
          <TabsTrigger value="relation"><Users className="h-3.5 w-3.5" />粉丝/关注</TabsTrigger>
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
          {/* 2026-09-21 用户反馈重做：不再要求「收藏夹文件夹」——
              多数人收藏就是一堆作品，不专门建文件夹（该账号实测文件夹数 = 0，
              但收藏作品有 19 条）。改为直接列收藏作品。 */}
          <div className="space-y-3">
            <div className="text-[0.78rem] text-[var(--color-text-secondary)]">
              你的收藏作品（直接列出，无需先建文件夹）
            </div>
            {favoriteQ.isPending ? <LoadingState /> :
              favoriteQ.isError ? (
                <ErrorState message={String((favoriteQ.error as Error)?.message)}
                            onRetry={favoriteQ.refetch} />
              ) : (favoriteQ.data?.items?.length
                ? <Grid items={favoriteQ.data.items} kind="video" onOpenAweme={openAweme} />
                : <EmptyState title="暂无收藏"
                    description="该账号没有收藏作品，或平台侧临时返回空（稍后重试）。" />)}
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
                  ? <Grid items={seriesQ.data.items} kind="video" onOpenAweme={openAweme} />
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
        </div>
      )}
    </PageContainer>
  );
}
