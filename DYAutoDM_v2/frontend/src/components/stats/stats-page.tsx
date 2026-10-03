/**
 * 统计页（ADR-033，2026-10-03）
 *
 * ## 设计意图
 *
 * 用户在一次信息架构调整里同时拍板三件事：
 *   ① 内容浏览页融合进采集页（消除「两个页面都能看作品」的重复心智）；
 *   ② 导航栏去掉「内容」，原位换成「统计」；
 *   ③ 统计 = **总览漏斗**（全局业务口径）+ **采集专项明细**（采集域口径）。
 *
 * 本组件只负责 ③。它**不负责**采集动作（那在采集页），是**纯只读看板**。
 *
 * ## 🔴 口径铁律（本页第一约束，违反即假数据）
 *
 * 两个数据源的**单位与口径不同**，必须分别标注、不可混算：
 *
 * | 数据源 | 端点 | 口径 | 单位 |
 * |---|---|---|---|
 * | 总览漏斗 | `/api/overview/funnel` | 全局业务（采集+私信+线索+账号） | 条 / 人 混有，各自标注 |
 * | 采集专项 | `/api/crawl/stats` | 仅采集域（crawl_history + sink） | 条（results） / 人（sink） |
 *
 * 具体三条：
 *   · `results` = 采集**返回的条目数**，不是去重人数 —— 标「条」；
 *   · `sink.total/sent` = 沉淀池按 uid 去重的**人数** —— 标「人」；
 *   · 两者**不可相加**、**不可互比**（跨单位运算 = 编造数字）。
 *
 * ## 风控契约
 *
 * 两个端点都是只读本地 SQLite、零网络零浏览器 ⇒ 可安全轮询。
 * 本页**不主动触发任何采集**，刷新按钮只重取统计。
 *
 * ## 「今日无数据」的处理（禁止把 0 伪装成失败）
 *
 * 凌晨看统计时今日往往全 0。处理方式与总览页一致：
 * 提供 `day=latest` 回落到**最近有数据的一天**，并在标题旁显式标注
 * 「展示的是 X 日，今日尚无数据」—— 全 0 不解释会被读成「系统坏了」。
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  Search as SearchIcon, TrendingUp, Users, BarChart3, RefreshCw,
  MessageSquare, Sparkles, Target, Database, Radio, Send, X,
  UserCheck,
} from "lucide-react";
import { PageProps, type CrawlStats, type OverviewFunnel } from "@/api/client";
import { PageContainer, PageHeader } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { Section, Stat, StatRow, Blank } from "@/components/page/kit";
import { LoadingState, ErrorState, EmptyState } from "@/components/ui/empty-state";
import { Badge } from "@/components/ui/badge";
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from "@/components/ui/select";
import { Card, CardContent } from "@/components/ui/card";
import { fmtNumShort } from "@/components/crawl/crawl-shared";

/** kind 的中文名（与后端 crawl_history.kind 取值对齐）。 */
const KIND_LABEL: Record<string, string> = {
  video: "视频搜索",
  user: "用户搜索",
  comment: "评论采集",
  batch: "批量截流",
  unknown: "未知",
};

export type DomainKey =
  | "crawl" | "dm" | "leads" | "accounts" | "messages" | "live";

/**
 * 六个业务域的定义（ADR-035，2026-10-03）。
 *
 * 为什么是这六个：`/api/overview/funnel` 的返回结构就这 6 个顶层业务键
 * （crawl/sink/messages/dm/leads/accounts），再加"直播"域（直播监听是独立业务，
 * 其统计目前来自 tasks 表，funnel 未单列）。缺一个域 = 统计缺一块业务。
 *
 * `read()` 只做**读数**，不做单位混算（口径铁律：条 vs 人 不可相加）。
 */
const DOMAINS: {
  key: DomainKey;
  label: string;
  icon: React.ElementType;
  note: string;
  read: (f: OverviewFunnel) => { primary: string; secondary: string };
}[] = [
  {
    key: "crawl",
    label: "采集",
    icon: SearchIcon,
    note: "每次采集的轮次、类型、目标与条数（可下钻到作品）",
    read: (f) => ({
      primary: String(f.crawl?.today_results ?? 0),
      secondary: `今日 ${f.crawl?.today_runs ?? 0} 次 · 条`,
    }),
  },
  {
    key: "dm",
    label: "私信",
    icon: Send,
    note: "沉淀池新增/已发人数与私信发送结果（被拒原因可查）",
    read: (f) => ({
      primary: String(f.dm?.today_sent ?? 0),
      secondary: `已发 ${f.sink?.total_sent ?? 0}/${f.sink?.total ?? 0} 人 · 人`,
    }),
  },
  {
    key: "leads",
    label: "线索",
    icon: Sparkles,
    note: "AI 判定为高意向的线索数（今日 / 累计）",
    read: (f) => ({
      primary: String(f.leads?.today ?? 0),
      secondary: `累计 ${f.leads?.total ?? 0} · 人`,
    }),
  },
  {
    key: "accounts",
    label: "账号",
    icon: UserCheck,
    note: "账号总数、活跃数与凭证状态（凭证失效会在私信域体现为失败）",
    read: (f) => ({
      primary: `${f.accounts?.active ?? 0}/${f.accounts?.total ?? 0}`,
      secondary: `凭证正常 ${f.accounts?.credential_ok ?? 0} · 个`,
    }),
  },
  {
    key: "messages",
    label: "会话",
    icon: MessageSquare,
    note: "会话消息量（收到 / 我方发出），用于核对采集与私信的落差",
    read: (f) => ({
      primary: String(f.messages?.today_theirs ?? 0),
      secondary: `我方 ${f.messages?.raw_me_rows ?? 0} · 条`,
    }),
  },
  {
    key: "live",
    label: "直播",
    icon: Radio,
    note: "直播监听任务（来自任务表）。若从未跑过真实监听，此处会显示「暂无明细」——那是事实，不是故障。",
    read: (f) => ({
      primary: String(f.crawl?.kinds?.comment ?? 0),
      secondary: "评论采集（直播域）· 条",
    }),
  },
];

export default function StatsPage(props: PageProps) {
  const [days, setDays] = useState(7);
  // 今日无数据时是否回落到「最近有数据的一天」（默认回落，避免全 0 被误读）
  const [fallbackLatest, setFallbackLatest] = useState(true);
  // ★ ADR-035：当前下钻的业务域（null = 未选中）
  const [activeDomain, setActiveDomain] = useState<DomainKey | null>(null);

  // ── ① 采集专项统计 ──
  const crawlQ = useQuery<CrawlStats>({
    queryKey: ["crawl-stats", days],
    queryFn: () => props.api.crawlStats(days),
    enabled: !!props.ready,
    staleTime: 30_000,
  });

  // ── ② 总览漏斗（全局业务口径）──
  const funnelQ = useQuery<OverviewFunnel>({
    queryKey: ["overview-funnel", fallbackLatest],
    queryFn: () =>
      props.api.getOverviewFunnel(fallbackLatest ? "latest" : "today"),
    enabled: !!props.ready,
    staleTime: 30_000,
  });

  const cs = crawlQ.data;
  const fn = funnelQ.data;

  const refreshAll = () => {
    void crawlQ.refetch();
    void funnelQ.refetch();
  };

  /** 趋势最大值（柱状图归一化用；全 0 时避免除零）。 */
  const trendMax = Math.max(1, ...(cs?.trend || []).map((d) => d.results));

  return (
    <PageContainer>
      <PageHeader
        title="统计"
        description="总览漏斗（全局业务） + 采集专项明细 · 只读本地库，零网络零浏览器"
        actions={
          <div className="flex items-center gap-2">
            <Select value={String(days)} onValueChange={(v) => setDays(Number(v))}>
              <SelectTrigger className="h-9 w-[110px]"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="7">近 7 日</SelectItem>
                <SelectItem value="14">近 14 日</SelectItem>
                <SelectItem value="30">近 30 日</SelectItem>
              </SelectContent>
            </Select>
            <Button
              size="sm"
              variant="secondary"
              disabled={crawlQ.isFetching || funnelQ.isFetching}
              onClick={refreshAll}
            >
              <RefreshCw
                className={`h-3.5 w-3.5 ${crawlQ.isFetching || funnelQ.isFetching ? "animate-spin" : ""}`}
              />
              刷新
            </Button>
          </div>
        }
      />

      {/* ══════════ L0 · 全域概览（ADR-035）══════════
          用户 2026-10-03 要求：统计不该只有采集，项目**所有业务域**都要统计。
          `/api/overview/funnel` 已提供 6 个域的聚合，此前本页只用了其中 2 个，
          故看上去「只有采集专项」。此处补齐全部 6 域，且每域可点开看详情（L2）。 */}
      <Section
        className="mb-4"
        title="全域概览"
        description={
          <>
            六个业务域 · 数据来自后端聚合
            {fn && ` · ${fn.date}${fn.is_today ? "" : "（今日无数据，回落到最近有数据的一天）"}`}
          </>
        }
        actions={
          <Badge variant="outline">
            {funnelQ.isFetching ? "读取中…" : fn ? `${fn.date}` : "—"}
          </Badge>
        }
      >
        {funnelQ.isPending ? <LoadingState /> : funnelQ.isError ? (
          <ErrorState
            message={String((funnelQ.error as Error)?.message || "漏斗读取失败")}
            onRetry={refreshAll}
          />
        ) : !fn ? (
          <Blank>暂无数据</Blank>
        ) : (
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
            {/* 每个卡片 = 一个业务域；点击 → 下钻到该域详情（activeDomain） */}
            {DOMAINS.map((d) => {
              const v = d.read(fn);
              const Icon = d.icon;
              const active = activeDomain === d.key;
              return (
                <button
                  key={d.key}
                  type="button"
                  onClick={() => setActiveDomain(active ? null : d.key)}
                  data-od-id={`stats-domain-${d.key}`}
                  className={[
                    "rounded-[var(--radius-md)] border p-3 text-left transition-colors",
                    active
                      ? "border-[var(--color-accent)] bg-[var(--color-surface-raised)]"
                      : "border-[var(--color-border)] hover:border-[var(--color-accent)]",
                  ].join(" ")}
                  title={`查看「${d.label}」明细`}
                >
                  <div className="flex items-center gap-1.5 text-[0.72rem] text-[var(--color-text-muted)]">
                    <Icon className="h-3.5 w-3.5" />
                    {d.label}
                  </div>
                  <div className="mt-1 font-mono text-[1.15rem] text-[var(--color-text)]">
                    {v.primary}
                  </div>
                  <div className="mt-0.5 text-[0.68rem] text-[var(--color-text-muted)]">
                    {v.secondary}
                  </div>
                </button>
              );
            })}
          </div>
        )}
      </Section>

      {/* ══════════ L2 · 域详情（点 L0 卡片后出现）══════════ */}
      {activeDomain && (
        <Section
          className="mb-4"
          title={`${DOMAINS.find((d) => d.key === activeDomain)?.label ?? ""} · 详情`}
          description={
            DOMAINS.find((d) => d.key === activeDomain)?.note ?? ""
          }
          actions={
            <Button size="sm" variant="ghost" onClick={() => setActiveDomain(null)}>
              <X className="h-3.5 w-3.5" />收起
            </Button>
          }
        >
          <DomainDetail domain={activeDomain} props={props} days={days} />
        </Section>
      )}

      {/* ══════════ L1 · 采集专项核心指标 ══════════ */}
      <Section
        className="mb-4"
        title="采集专项"
        description={
          <>
            数据源 <code className="text-[0.72rem]">crawl_history</code> +{" "}
            <code className="text-[0.72rem]">dm_uid_sink</code>
            {cs && !cs.today.runs ? " · 今日尚无采集记录" : ""}
          </>
        }
        actions={
          <Badge variant="outline">
            {cs ? `统计日 ${cs.date}` : "—"}
          </Badge>
        }
      >
        {crawlQ.isPending ? <LoadingState /> : crawlQ.isError ? (
          <ErrorState
            message={String((crawlQ.error as Error)?.message || "统计读取失败")}
            onRetry={() => void crawlQ.refetch()}
          />
        ) : !cs ? (
          <Blank>暂无统计数据</Blank>
        ) : (
          <StatRow cols={4}>
            <Stat
              label="累计采集轮次"
              value={fmtNumShort(cs.total.runs)}
              unit="轮"
              icon={<SearchIcon className="h-3.5 w-3.5" />}
            />
            <Stat
              label="累计采集条目"
              value={fmtNumShort(cs.total.results)}
              unit="条"
              accent
              icon={<Database className="h-3.5 w-3.5" />}
            />
            <Stat
              label="今日采集"
              value={fmtNumShort(cs.today.results)}
              unit="条"
              icon={<TrendingUp className="h-3.5 w-3.5" />}
            />
            {/* ★ 单位铁律：sink 是「人」，与上面「条」严格区分标注 */}
            <Stat
              label="采集沉淀（去重）"
              value={fmtNumShort(cs.sink.total)}
              unit="人"
              icon={<Users className="h-3.5 w-3.5" />}
            />
          </StatRow>
        )}
      </Section>

      {/* ══════════ L2 · 趋势 + 关键词榜 ══════════ */}
      <div className="mb-4 grid gap-4 lg:grid-cols-2">
        <Section title={`近 ${days} 日趋势`} description="按本地日切聚合，缺日补 0">
          {crawlQ.isPending ? <LoadingState /> : !cs?.trend?.length ? (
            <Blank>暂无趋势数据</Blank>
          ) : (
            <div className="space-y-2">
              {cs.trend.map((d) => (
                <div key={d.date} className="flex items-center gap-3">
                  <span className="w-[5.2rem] shrink-0 font-mono text-[0.72rem]
                                   text-[var(--color-text-muted)]">
                    {d.date.slice(5)}
                  </span>
                  <div className="h-4 flex-1 overflow-hidden rounded
                                  bg-[var(--color-surface-raised)]">
                    <div
                      className="h-full rounded bg-[var(--color-accent)] transition-[width] duration-500"
                      style={{ width: `${(d.results / trendMax) * 100}%` }}
                      title={`${d.results} 条 / ${d.runs} 轮`}
                    />
                  </div>
                  <span className="w-[4.5rem] shrink-0 text-right font-mono text-[0.72rem]
                                   tabular-nums text-[var(--color-text-secondary)]">
                    {d.results} 条
                  </span>
                  <span className="w-[3rem] shrink-0 text-right font-mono text-[0.68rem]
                                   text-[var(--color-text-muted)]">
                    {d.runs} 轮
                  </span>
                </div>
              ))}
            </div>
          )}
        </Section>

        <Section
          title="关键词排行"
          description="仅统计关键词非空的视频/用户搜索（评论采集的 target 是作品 ID，不计入）"
        >
          {crawlQ.isPending ? <LoadingState /> : !cs?.top_keywords?.length ? (
            <EmptyState
              title="暂无关键词数据"
              description="搜索过带关键词的视频或用户后，这里会按累计条数排行。"
            />
          ) : (
            <div className="space-y-1.5">
              {cs.top_keywords.map((k, i) => (
                <div key={k.keyword} className="flex items-center gap-2.5">
                  <span className="w-4 shrink-0 text-center font-mono text-[0.7rem]
                                   text-[var(--color-text-muted)]">
                    {i + 1}
                  </span>
                  <span className="min-w-0 flex-1 truncate text-[0.8rem]
                                   text-[var(--color-text)]"
                        title={k.keyword}>
                    {k.keyword}
                  </span>
                  <span className="shrink-0 font-mono text-[0.72rem] tabular-nums
                                   text-[var(--color-text-secondary)]">
                    {fmtNumShort(k.results)} 条
                  </span>
                  <span className="w-[3.2rem] shrink-0 text-right font-mono text-[0.68rem]
                                   text-[var(--color-text-muted)]">
                    {k.runs} 轮
                  </span>
                </div>
              ))}
            </div>
          )}
        </Section>
      </div>

      {/* ══════════ L3 · 总览漏斗（全局业务口径）══════════ */}
      <Section
        className="mb-4"
        title="总览漏斗"
        description="全局业务口径（含私信 / 线索 / 账号），与上面的采集专项口径不同，勿混算"
        actions={
          <div className="flex items-center gap-2">
            {fn && !fn.is_today ? (
              <Badge variant="outline">
                展示 {fn.date}（今日尚无数据）
              </Badge>
            ) : null}
            <Button
              size="sm"
              variant="ghost"
              onClick={() => setFallbackLatest((v) => !v)}
              title="今日无数据时，回落到最近有数据的一天"
            >
              {fallbackLatest ? "回到今日" : "回到最近活跃日"}
            </Button>
          </div>
        }
      >
        {funnelQ.isPending ? <LoadingState /> : funnelQ.isError ? (
          <ErrorState
            message={String((funnelQ.error as Error)?.message || "漏斗读取失败")}
            onRetry={() => void funnelQ.refetch()}
          />
        ) : !fn ? (
          <Blank>暂无漏斗数据</Blank>
        ) : (
          <StatRow cols={5}>
            <Stat
              label="今日采集"
              value={fmtNumShort(fn.crawl.today_results)}
              unit="条"
              icon={<SearchIcon className="h-3.5 w-3.5" />}
            />
            {/* sink.today_new 单位 = 人（按 uid 去重），与「条」区分 */}
            <Stat
              label="今日新增捕获"
              value={fmtNumShort(fn.sink.today_new)}
              unit="人"
              icon={<Users className="h-3.5 w-3.5" />}
            />
            <Stat
              label="今日真实已发"
              value={fmtNumShort(fn.dm.today_sent)}
              unit="条"
              accent
              icon={<MessageSquare className="h-3.5 w-3.5" />}
            />
            <Stat
              label="今日留资线索"
              value={fmtNumShort(fn.leads.today)}
              unit="条"
              icon={<Sparkles className="h-3.5 w-3.5" />}
            />
            <Stat
              label="凭证可用账号"
              value={`${fn.accounts.credential_ok}/${fn.accounts.total}`}
              icon={<Target className="h-3.5 w-3.5" />}
            />
          </StatRow>
        )}
        {/* 口径注解：被平台拒发独立成维，绝不混入「已发」（假成功铁律） */}
        {fn && fn.dm.rejected > 0 ? (
          <p className="mt-3 text-[0.72rem] text-[var(--color-text-muted)]">
            另有 {fn.dm.rejected} 条被平台拒发（未登录 / 风控限制），
            <strong className="text-[var(--color-text-secondary)]">不计入</strong>
            「真实已发」——混入即是假成功。
          </p>
        ) : null}
      </Section>

      {/* ══════════ L4 · 采集类型分布 + 最近记录 ══════════ */}
      <div className="grid gap-4 lg:grid-cols-2">
        <Section title="按采集类型分布" description="全量历史，累计口径">
          {crawlQ.isPending ? <LoadingState /> : !cs || !Object.keys(cs.kinds).length ? (
            <Blank>暂无数据</Blank>
          ) : (
            <div className="space-y-2">
              {Object.entries(cs.kinds)
                .sort((a, b) => b[1].results - a[1].results)
                .map(([k, v]) => (
                  <div key={k} className="flex items-center gap-2.5">
                    <Badge variant="outline" className="w-[5.5rem] justify-center shrink-0">
                      {KIND_LABEL[k] || k}
                    </Badge>
                    <div className="h-3.5 flex-1 overflow-hidden rounded
                                    bg-[var(--color-surface-raised)]">
                      <div
                        className="h-full rounded bg-[var(--color-accent)]"
                        style={{
                          width: `${(v.results / Math.max(1, cs.total.results)) * 100}%`,
                        }}
                      />
                    </div>
                    <span className="w-[5rem] shrink-0 text-right font-mono text-[0.72rem]
                                     tabular-nums text-[var(--color-text-secondary)]">
                      {fmtNumShort(v.results)} 条
                    </span>
                  </div>
                ))}
            </div>
          )}
        </Section>

        <Section title="最近采集记录" description="最近 20 条（摘要，不含完整 payload）">
          {crawlQ.isPending ? <LoadingState /> : !cs?.recent?.length ? (
            <EmptyState
              title="还没有采集记录"
              description="到「采集」页搜一次，这里就会出现记录。"
            />
          ) : (
            <div className="max-h-[16rem] space-y-1 overflow-y-auto">
              {cs.recent.map((r) => (
                <div key={r.id} className="flex items-center gap-2 text-[0.74rem]">
                  <Badge variant="outline" className="shrink-0">
                    {KIND_LABEL[r.kind] || r.kind}
                  </Badge>
                  <span className="min-w-0 flex-1 truncate text-[var(--color-text)]"
                        title={r.keyword || r.target}>
                    {r.keyword || r.target || "—"}
                  </span>
                  <span className="shrink-0 font-mono tabular-nums
                                   text-[var(--color-text-secondary)]">
                    {r.result_count} 条
                  </span>
                  <span className="w-[5.6rem] shrink-0 text-right font-mono text-[0.68rem]
                                   text-[var(--color-text-muted)]">
                    {r.ts.slice(5, 16)}
                  </span>
                </div>
              ))}
            </div>
          )}
        </Section>
      </div>

      {/* 全页口径说明（一次说清，避免两个数据源被混读） */}
      <Card className="mt-4">
        <CardContent className="p-3 text-[0.72rem] leading-relaxed text-[var(--color-text-muted)]">
          <BarChart3 className="mr-1.5 inline h-3.5 w-3.5 align-text-bottom" />
          <strong className="text-[var(--color-text-secondary)]">口径说明：</strong>
          「条」= 采集返回的条目数（不去重）；「人」= 沉淀池按 UID 去重的人数。
          两者单位不同，<strong className="text-[var(--color-text-secondary)]">不可相加或互比</strong>。
          全页数据均来自本地库实时聚合，不触发任何网络请求或浏览器动作。
        </CardContent>
      </Card>
    </PageContainer>
  );
}

/**
 * 业务域详情（ADR-035）—— 统计不止概览，还要**能溯源到每一条**。
 *
 * 设计对标直播监听的「查阅模式」（`components/live/LiveReviewMode.tsx`）：
 * 明细表格 + 每行给出「什么时候 / 对谁 / 做了什么 / 结果多少」，
 * 让用户能从汇总数字一路追到原始记录。
 *
 * ## 诚实边界（禁止假成功）
 * 后端目前只有**采集域**提供独立明细端点（`/api/crawl/history`）。
 * 其余域的明细端点尚未建设，这里**明确告知「暂无明细」**并说明去哪看，
 * 而不是拿概览数字再画一遍图冒充「详情」——那是本项目的假成功红线。
 */
function DomainDetail({
  domain,
  props,
  days,
}: {
  domain: DomainKey;
  props: PageProps;
  days: number;
}) {
  // 采集域：拉真实明细（后端唯一已有的独立明细端点）
  const enabled = domain === "crawl";
  const q = useQuery({
    queryKey: ["stats-domain-detail", domain, days],
    queryFn: async () => {
      if (domain !== "crawl") return null;
      return await props.api.crawlHistory(100);
    },
    enabled: !!props.ready && enabled,
    staleTime: 30_000,
  });

  if (!enabled) {
    return (
      <div className="space-y-2 text-[0.78rem] text-[var(--color-text-muted)]">
        <div>
          该域当前<strong className="text-[var(--color-text-secondary)]">暂无独立明细端点</strong>
          （后端尚未建设），因此这里不重复展示概览数字——那会让人误以为已经能溯源。
        </div>
        <div>
          现阶段可溯源的位置：
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            <li>直播监听 → 页面内「查阅模式」（历史任务快照，逐条可查）</li>
            <li>私信/会话 → 私信页的会话列表与聊天记录</li>
            <li>采集 → 本页「采集」域（下方明细表即为真实明细）</li>
          </ul>
        </div>
      </div>
    );
  }

  if (q.isPending) return <LoadingState />;
  if (q.isError) {
    return (
      <ErrorState
        message={String((q.error as Error)?.message || "明细读取失败")}
        onRetry={() => void q.refetch()}
      />
    );
  }

  const items = (q.data as { items?: Record<string, unknown>[] } | null)?.items || [];
  if (!items.length) {
    return <Blank>近 100 条内无采集记录</Blank>;
  }

  return (
    <div className="overflow-x-auto">
      <table className="w-full text-[0.75rem]">
        <thead>
          <tr className="border-b border-[var(--color-border)] text-left text-[var(--color-text-muted)]">
            <th className="py-1.5 pr-3 font-medium">时间</th>
            <th className="py-1.5 pr-3 font-medium">账号</th>
            <th className="py-1.5 pr-3 font-medium">类型</th>
            <th className="py-1.5 pr-3 font-medium">关键词 / 目标</th>
            <th className="py-1.5 pr-3 text-right font-medium">条数</th>
          </tr>
        </thead>
        <tbody>
          {items.map((it, i) => {
            const kind = String(it.kind ?? "");
            const target = String(it.target ?? "");
            const kw = String(it.keyword ?? "");
            return (
              <tr key={String(it.id ?? i)} className="border-b border-[var(--color-border)] last:border-0">
                <td className="py-1.5 pr-3 font-mono text-[var(--color-text-muted)]">
                  {String(it.ts ?? "")}
                </td>
                <td className="py-1.5 pr-3">{String(it.account || "—")}</td>
                <td className="py-1.5 pr-3">{KIND_LABEL[kind] ?? (kind || "—")}</td>
                <td className="py-1.5 pr-3 font-mono" title={kw || target}>
                  {kw || (target ? `作品 ${target.slice(-10)}` : "—")}
                </td>
                <td className="py-1.5 pr-3 text-right font-mono">
                  {String(it.result_count ?? 0)}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
