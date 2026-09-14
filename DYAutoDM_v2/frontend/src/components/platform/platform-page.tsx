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
import { LayoutGrid, Search as SearchIcon, Heart, Star, Bell, User } from "lucide-react";
import { PageContainer, PageHeader } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";
import { EmptyState, LoadingState, ErrorState } from "@/components/ui/empty-state";
import { platformApi, type AwemeItem, type UserItem, type NoticeItem } from "@/api/platform";
import { fmtNum, fmtAgo } from "@/lib/utils";
import type { PageProps } from "@/api/client";
import { FullscreenPlayer } from "@/components/player";
import type { PlayerMedia } from "@/components/player";

/** 作品卡片（封面 + 统计；点击打开播放器）。 */
function AwemeCard({ item, onOpen }: { item: AwemeItem; onOpen?: (it: AwemeItem) => void }) {
  return (
    <Card
      className={`overflow-hidden transition-transform hover:-translate-y-0.5 ${onOpen ? "cursor-pointer" : ""}`}
      onClick={() => onOpen?.(item)}
    >
      <div className="relative aspect-[3/4] w-full overflow-hidden bg-[var(--color-surface)]">
        {item.cover ? (
          <img
            src={item.cover}
            alt={item.desc || item.aweme_id}
            loading="lazy"
            className="h-full w-full object-cover"
          />
        ) : (
          <div className="flex h-full items-center justify-center text-[0.75rem]
                          text-[var(--color-text-muted)]">
            无封面
          </div>
        )}
        <div className="absolute inset-x-0 bottom-0 bg-gradient-to-t from-black/70 to-transparent p-2">
          <div className="flex items-center gap-2 text-[0.68rem] text-white/90">
            <span>♥ {fmtNum(item.digg_count)}</span>
            <span>💬 {fmtNum(item.comment_count)}</span>
          </div>
        </div>
      </div>
      <CardContent className="p-2.5">
        <div className="line-clamp-2 text-[0.75rem] leading-snug text-[var(--color-text)]">
          {item.desc || "（无描述）"}
        </div>
        <div className="mt-1 flex items-center justify-between text-[0.68rem]
                        text-[var(--color-text-muted)]">
          <span className="truncate">{item.author_nickname || "—"}</span>
          <span>{item.create_time ? fmtAgo(item.create_time) : ""}</span>
        </div>
      </CardContent>
    </Card>
  );
}

function UserCard({ item }: { item: UserItem }) {
  return (
    <Card>
      <CardContent className="flex items-center gap-3 p-3">
        {item.avatar ? (
          <img src={item.avatar} alt={item.nickname}
               className="h-11 w-11 shrink-0 rounded-full object-cover" />
        ) : (
          <div className="flex h-11 w-11 shrink-0 items-center justify-center rounded-full
                          bg-[var(--color-surface-raised)]">
            <User className="h-5 w-5 text-[var(--color-text-muted)]" />
          </div>
        )}
        <div className="min-w-0 flex-1">
          <div className="truncate text-[0.84rem] font-medium text-[var(--color-text)]">
            {item.nickname || item.uid || "—"}
          </div>
          <div className="truncate text-[0.72rem] text-[var(--color-text-secondary)]">
            {item.signature || `粉丝 ${fmtNum(item.follower_count)} · 作品 ${fmtNum(item.aweme_count)}`}
          </div>
        </div>
        <Badge variant="outline">{fmtNum(item.follower_count)} 粉</Badge>
      </CardContent>
    </Card>
  );
}

function Grid({ items, kind, onOpenAweme }: {
  items: (AwemeItem | UserItem)[]; kind: "video" | "user";
  onOpenAweme?: (it: AwemeItem) => void;
}) {
  if (!items.length) return <EmptyState title="暂无内容" description="换个条件试试，或稍后重试。" />;
  return (
    <div className="grid grid-cols-5 gap-3">
      {items.map((it, i) =>
        kind === "user"
          ? <UserCard key={(it as UserItem).uid || i} item={it as UserItem} />
          : <AwemeCard key={(it as AwemeItem).aweme_id || i} item={it as AwemeItem}
                       onOpen={onOpenAweme} />
      )}
    </div>
  );
}

export default function PlatformPage(props: PageProps) {
  const accounts = (props.overview?.accounts || []).map((a) => a.name);
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
  const [playerErr, setPlayerErr] = useState<string>("");
  const openAweme = async (it: AwemeItem) => {
    setPlayerErr("");
    setPlayerOpen(true);
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
            ? renderQ(worksQ, "video")
            : <EmptyState title="填入用户主页链接" description="支持主页 URL 或 sec_uid。作品列表会一次拉全（含翻页）。" />}
        </TabsContent>

        <TabsContent value="liked">
          {renderQ(likedQ, "video")}
        </TabsContent>

        <TabsContent value="collected">
          {collectedQ.isPending ? <LoadingState /> :
            collectedQ.isError ? <ErrorState message={String((collectedQ.error as Error)?.message)} onRetry={collectedQ.refetch} /> :
            (collectedQ.data?.items?.length ? (
              <div className="grid grid-cols-3 gap-3">
                {collectedQ.data.items.map((c) => (
                  <Card key={c.collects_id}>
                    <CardContent className="p-3">
                      <div className="text-[0.84rem] font-medium text-[var(--color-text)]">{c.name || "未命名"}</div>
                      <div className="mt-1 text-[0.72rem] text-[var(--color-text-secondary)]">
                        {fmtNum(c.count)} 个作品
                      </div>
                    </CardContent>
                  </Card>
                ))}
              </div>
            ) : <EmptyState title="暂无收藏夹" />)}
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
