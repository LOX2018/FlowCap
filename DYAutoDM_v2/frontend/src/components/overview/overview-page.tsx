/**
 * 总览页（重设计版 · 对标 better-douyin 设计体系）
 *
 * ## 设计意图
 *
 * 旧版用 `global.css` 类名拼装（`.section-head` / `.card` / `.stat` / `.head-row`
 * / `.seg` / `.feed` / `.run-item`），问题：
 *   ① 深浅主题要各写一套色值；② 无法继承设计令牌；③ 每页重复手搓同一套骨架。
 *
 * 新版全部走 `components/page/kit`（Section / Stat / Row / KeyValue / Tone /
 * SegmentedTabs / SkeletonRows / Blank）+ `components/ui/*`（Radix+CVA），
 * 颜色/圆角/缓动一律取自 `tokens.css`，主题切换自动生效。
 *
 * ## 不变的（业务契约）
 * - 数据来源：`props.overview`（App 级 3s 轮询）+ `api.getAccounts()` / `api.getStats()`
 * - 视图模式：单账户 / 多账户总览
 * - `data-od-id` 锚点保留（自动化选取用）
 *
 * ═══════════════════════════════════════════════════════════════
 * ## 2026-09-30 重设计：三层信息架构
 * ═══════════════════════════════════════════════════════════════
 *
 * ### 旧版问题（本次重设计的根因）
 *
 * 旧版把 7 个性质的区块塞进同一个 `grid grid-cols-2`，三类结构性缺陷：
 *   ① **层级混淆**（SoC 违规）：控制面（AI 启停按钮）、此刻状态（运行中任务）、
 *      历史记录（任务历史）、诊断（能力健康）被拉到同一视觉层，读不出主线；
 *   ② **栅格锯齿**：内容高度天然不等（任务历史 5 行 vs AI 区块 3 行），
 *      两列栅格让卡片高度互相拉扯 —— 观感上的「乱」大半由此而来；
 *   ③ **多账户视图是信息孤岛**：`viewMode === "grid"` 分支里没有 AI / 能力健康 /
 *      任务历史，切过去这些信息凭空消失。
 *
 * ### 新架构（三层，单骨架）
 *
 *   L0 待处理横幅 —— 只有真有问题才出现（守护离线 / 全部掉线），不占常驻版面
 *   L1 核心指标条 —— 同一时间口径（本次已发 / 捕获评论 / 引擎状态 / 守护在线）
 *   L2 三个语义分区（**纵向铺满**，各自成区，不再等高拉扯）：
 *        · 正在跑  ：账号 + 发送进度 + 直播在线 + AI 自动回复
 *        · 资源健康：账号凭证 + 能力健康
 *        · 最近发生：实时动态 + 任务历史速览
 *
 * ### 已固化的决策（用户 2026-09-30 拍板）
 *   · 保留「单账户 / 多账户总览」双视图切换
 *   · 总览为**纯只读看板**，控制操作全部移出（AI 启停 → 直播页；巡检 → 配置中心）
 *
 * ### 数据口径不变
 * 所有字段与旧版逐字一致 —— 重设计只改**编排**，不改**语义**（禁止为排版改数据）。
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity, Send, Radio, Cpu, Users, ShieldAlert, History } from "lucide-react";
import { PageProps } from "../../api/client";
import { Avatar, KIND_NAME } from "../../components/ui";
import AiRuntimeSection from "@/components/overview/AiRuntimeSection";
import CapabilityHealthSection from "@/components/overview/CapabilityHealthSection";
// ADR-018 F2：新增三张数据卡（全部只读既有端点，零新采集）
import AccountsHealthSection from "@/components/overview/AccountsHealthSection";
import LiveStatusSection from "@/components/overview/LiveStatusSection";
import TaskHistorySection from "@/components/overview/TaskHistorySection";
import { PageContainer, PageHeader } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
  Section, Stat, StatRow, Row, RowText, KeyValue, Tone,
  SkeletonRows, Blank, SegmentedTabs,
} from "@/components/page/kit";
import { type OverviewExt, type StatsResp, type Account, type FeedItem, ProgressBar } from "./overview-shared";

/**
 * L2 分区标题。
 *
 * 2026-09-30：旧版各 Section 自带标题、彼此无视觉分组，7 张卡等高并列 ⇒ 读不出层次。
 * 现用「图标 + 组标题 + 细分隔线」把 L2 切成三个语义分区；
 * 组内仍纵向铺满（不强制等高），以消除旧版两列栅格的锯齿观感。
 */
function GroupLabel({
  icon,
  title,
  hint,
}: {
  icon: React.ReactNode;
  title: string;
  hint?: string;
}) {
  return (
    <div className="mb-2 flex items-center gap-2">
      <span className="flex shrink-0 items-center gap-1.5 text-[0.76rem] font-semibold tracking-[0.03em] text-[var(--color-text-secondary)]">
        {icon}
        {title}
      </span>
      {hint ? (
        <span className="truncate text-[0.7rem] text-[var(--color-text-muted)]">{hint}</span>
      ) : null}
      <span className="h-px min-w-[24px] flex-1 bg-[var(--color-border)]" />
    </div>
  );
}

export default function OverviewPage(props: PageProps) {
  const { push, api, overview, ready } = props;
  const [viewMode, setViewMode] = useState<"single" | "grid">("single");
  const [activeAcct, setActiveAcct] = useState("");
  const ov = (overview || ({} as OverviewExt)) as OverviewExt;

  // 账号列表（读取 App 常驻轮询的共享缓存；getAccounts 返回数组）
  const accountsQ = useQuery({
    queryKey: ["accounts"],
    queryFn: async (): Promise<Account[]> => {
      const d = (await api.getAccounts()) as unknown as Account[];
      return Array.isArray(d) ? d : [];
    },
    enabled: !!ready,
  });
  const statsQ = useQuery({
    queryKey: ["overview-stats"],
    queryFn: async (): Promise<StatsResp | null> => {
      const d = (await api.getStats()) as unknown as StatsResp;
      return d && d.ok ? d : null;
    },
    refetchInterval: 3000,
    enabled: !!ready,
  });

  const accounts = accountsQ.data || [];
  const stats = statsQ.data || null;

  const realFeed: FeedItem[] = ((stats && stats.list) || [])
    .slice(0, 24)
    .map((r, i) => ({
      id: 100000 + i,
      t: (r.captureTs || "").slice(-8) || "—",
      k: r.status === "sent" ? "msg" : "danmaku",
      n: r.nickname || "未知",
      l: 0,
      x: (r.comment || "") + (r.content ? " → 私信: " + r.content : ""),
    }));

  const curAcct =
    accounts.find((a) => a.name === activeAcct) ||
    accounts.find((a) => a.isCurrent) ||
    accounts[0] ||
    null;

  const sentPct = ov.limit ? Math.round(((ov.sent || 0) / ov.limit) * 100) : 0;

  /* ── L0：待处理事项（只有真有问题才出现，不占常驻版面） ──
     判据均为「已确知的异常」，不含 unknown/未校验 —— 未定论 ≠ 有问题。 */
  const attention: string[] = [];
  if (ready) {
    if (ov.browserDaemon && !ov.browserDaemon.alive) {
      attention.push("凭证守护离线 —— 私信与监听将不可用");
    }
    if (ov.recvDaemon && !ov.recvDaemon.alive) {
      attention.push("私信守护离线 —— 新消息不会落库");
    }
    if (accounts.length && accounts.every((a) => !a.loggedIn)) {
      attention.push("所有账号均已掉线 —— 请重新登录");
    }
  }

  /* ── 多账户总览：账号卡片栅格 ── */
  const gridCards = accounts.map((acct) => {
    const online = !!acct.loggedIn;
    return (
      <Card key={acct.name} className="overflow-hidden">
        <div className="flex items-center gap-2.5 border-b border-[var(--color-border)] p-3">
          <Avatar name={acct.name} h="20" />
          <div className="min-w-0 flex-1">
            <div className="truncate text-[0.84rem] font-medium text-[var(--color-text)]">
              {acct.name}
              {acct.isCurrent ? " · 当前" : ""}
            </div>
            <div className="truncate font-mono text-[0.7rem] text-[var(--color-text-muted)]">
              UID: {acct.uid || "—"}
            </div>
          </div>
          <Tone tone={online ? "ok" : "danger"}>
            {online ? (acct.signReady ? "已登录·签名就绪" : "已登录") : "离线"}
          </Tone>
        </div>

        <CardContent className="p-3">
          <KeyValue
            cols={2}
            items={[
              { k: "监测角色", v: acct.isMonitor ? "是" : "否" },
              { k: "发送角色", v: acct.isSender ? "是" : "否" },
            ]}
          />
        </CardContent>

        <div className="space-y-1.5 border-t border-[var(--color-border)] p-3">
          <div className="flex items-center justify-between text-[0.74rem]">
            <span className="text-[var(--color-text-muted)]">凭证守护</span>
            <span className="font-mono text-[var(--color-text)]">
              {ready && ov.browserDaemon && ov.browserDaemon.alive ? "在线" : "离线"}
            </span>
          </div>
          <div className="flex items-center justify-between text-[0.74rem]">
            <span className="text-[var(--color-text-muted)]">私信守护</span>
            <span className="font-mono text-[var(--color-text)]">
              {ready && ov.recvDaemon && ov.recvDaemon.alive ? "在线" : "离线"}
            </span>
          </div>
        </div>

        <div className="border-t border-[var(--color-border)] p-2.5">
          <Button
            variant="ghost"
            size="sm"
            className="w-full"
            onClick={() => {
              setViewMode("single");
              setActiveAcct(acct.name);
              push("已切换到 " + acct.name);
            }}
          >
            查看详情
          </Button>
        </div>
      </Card>
    );
  });

  return (
    <PageContainer>
      <PageHeader
        title="总览"
        description="系统当前状态一览（只读看板 · 操作在各功能页）"
        actions={
          <SegmentedTabs
            value={viewMode}
            onChange={setViewMode}
            items={[
              { value: "single", label: "单账户" },
              { value: "grid", label: "多账户总览" },
            ]}
          />
        }
      />

      {viewMode === "grid" ? (
        <div className="grid grid-cols-2 gap-4" data-od-id="overview-grid">
          {gridCards.length ? gridCards : (
            <div className="col-span-2">
              <Blank>暂无账号数据</Blank>
            </div>
          )}
        </div>
      ) : (
        <div className="space-y-5">
          {/* ══ L0 待处理横幅（只有真有问题才出现，不占常驻版面） ══ */}
          {attention.length ? (
            <div
              className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-[var(--radius-md)] border border-[var(--color-danger-soft)] bg-[var(--color-danger-soft)] px-3.5 py-2.5"
              data-od-id="overview-attention"
            >
              <ShieldAlert className="h-4 w-4 shrink-0 text-[var(--color-danger)]" />
              <span className="min-w-0 flex-1 text-[0.8rem] text-[var(--color-text)]">
                {attention.join(" · ")}
              </span>
              {props.setTab ? (
                <Button size="sm" variant="outline" onClick={() => props.setTab!("accounts")}>
                  去处理
                </Button>
              ) : null}
            </div>
          ) : null}

          {/* ══ L1 核心指标（统一为「此刻 / 本次」口径） ══ */}
          <Section bare data-od-id="overview-stats">
            {ready ? (
              <StatRow cols={4}>
                <Stat
                  label="本次已发私信" icon={<Send className="h-3.5 w-3.5" />}
                  value={`${(ov.sent || 0).toLocaleString()}/${ov.limit || 0}`}
                  delta={`待发 ${ov.queue || 0}`} accent
                />
                <Stat
                  label="捕获评论" icon={<Activity className="h-3.5 w-3.5" />}
                  value={stats ? stats.total.toLocaleString() : "0"}
                  unit="条"
                  delta={`已发 ${stats ? stats.sent : 0}`}
                />
                <Stat
                  label="引擎状态" icon={<Radio className="h-3.5 w-3.5" />}
                  value={ov.running ? (ov.paused ? "已暂停" : "运行中") : "已停止"}
                  delta={ov.status || ""}
                />
                <Stat
                  label="守护在线" icon={<Users className="h-3.5 w-3.5" />}
                  value={
                    (ov.browserDaemon && ov.browserDaemon.alive ? "凭证就绪" : "凭证离线")
                  }
                  delta={ov.recvDaemon && ov.recvDaemon.alive ? "私信在线" : "私信离线"}
                />
              </StatRow>
            ) : (
              <div className="grid grid-cols-4 gap-4">
                {[0, 1, 2, 3].map((i) => (
                  <div key={i} className="h-[92px] animate-pulse rounded-[var(--radius-md)]
                                          bg-[var(--color-surface-raised)]" />
                ))}
              </div>
            )}
          </Section>

          {/* ══ L2-① 正在跑 ══ */}
          <div data-od-id="overview-group-running">
            <GroupLabel
              icon={<Activity className="h-3.5 w-3.5" />}
              title="正在跑"
              hint="当前执行中的任务与引擎"
            />
            <div className="space-y-3">
              {/* 账号切换（归入本分区，不再单独占一个顶层 Section） */}
              <Section
                title="当前查看账号"
                data-od-id="overview-acct-select"
                actions={
                  accounts.length ? (
                    <SegmentedTabs
                      value={curAcct?.name || ""}
                      onChange={(n) => {
                        setActiveAcct(n);
                        push("已切换到 " + n);
                      }}
                      items={accounts.map((a) => ({ value: a.name, label: a.name }))}
                    />
                  ) : (
                    <Badge variant="outline">无账号</Badge>
                  )
                }
              >
                {curAcct ? (
                  <Row active>
                    <Avatar name={curAcct.name} h="20" />
                    <RowText
                      primary={`${curAcct.name}${curAcct.isCurrent ? " · 当前" : ""}`}
                      secondary={`UID: ${curAcct.uid || "—"}`}
                      mono
                    />
                    <Tone tone={curAcct.loggedIn ? "ok" : "danger"}>
                      {curAcct.loggedIn ? (curAcct.signReady ? "签名就绪" : "已登录") : "离线"}
                    </Tone>
                  </Row>
                ) : (
                  <Blank>请先在「账号」页添加并登录账号</Blank>
                )}
              </Section>

              {/* 私信发送进度（原「运行中任务」） */}
              <Section title="私信发送进度" data-od-id="overview-tasks">
                {ready ? (
                  <div className="space-y-4">
                    <div className="space-y-2">
                      <div className="flex items-center justify-between gap-3">
                        <span className="truncate text-[0.82rem] text-[var(--color-text)]">
                          自动私信引擎 · {ov.liveUrl || "未配置直播间"}
                        </span>
                        <Tone tone={ov.running ? (ov.paused ? "warn" : "ok") : "mute"}>
                          {ov.running ? (ov.paused ? "已暂停" : "运行中") : "未启动"}
                        </Tone>
                      </div>
                      <ProgressBar percent={sentPct} active={!!ov.running && !ov.paused} />
                      <div className="flex items-center justify-between font-mono text-[0.68rem]
                                      text-[var(--color-text-muted)]">
                        <span>{(ov.sent || 0).toLocaleString()} / {ov.limit || 0}</span>
                        <span>{sentPct}%</span>
                      </div>
                    </div>
                    {!ov.running && (
                      <div className="flex items-center gap-2 rounded-[var(--radius-sm)]
                                      bg-[var(--color-surface)] px-3 py-2 text-[0.74rem]
                                      text-[var(--color-text-muted)]">
                        <Cpu className="h-3.5 w-3.5" />
                        引擎未启动 —— 可在「直播」页开始监听
                      </div>
                    )}
                  </div>
                ) : (
                  <SkeletonRows rows={2} />
                )}
              </Section>

              <LiveStatusSection {...props} />
              {/* AI 自动回复：2026-09-30 起为**只读**（启停已迁至「直播」页） */}
              <AiRuntimeSection {...props} />
            </div>
          </div>

          {/* ══ L2-② 资源健康 ══ */}
          <div data-od-id="overview-group-health">
            <GroupLabel
              icon={<ShieldAlert className="h-3.5 w-3.5" />}
              title="资源健康"
              hint="账号凭证与各项能力的可用性"
            />
            <div className="space-y-3">
              <AccountsHealthSection {...props} />
              {/* 能力健康：2026-09-30 起为**只读**（巡检已迁至「配置中心 → 系统」） */}
              <CapabilityHealthSection {...props} />
            </div>
          </div>

          {/* ══ L2-③ 最近发生 ══ */}
          <div data-od-id="overview-group-recent">
            <GroupLabel
              icon={<History className="h-3.5 w-3.5" />}
              title="最近发生"
              hint="实时动态与历史任务回顾"
            />
            <div className="space-y-3">
              <Section
                title="实时动态"
                data-od-id="overview-feed"
                actions={
                  <Badge variant={ready ? "success" : "outline"}>
                    {ready ? "实时" : "未连接"}
                  </Badge>
                }
              >
                <div className="max-h-[420px] space-y-1 overflow-y-auto pr-1">
                  {realFeed.map((f) => (
                    <div key={f.id} className="flex items-baseline gap-2.5 py-1">
                      <span className="shrink-0 font-mono text-[0.68rem] text-[var(--color-text-muted)]">
                        {f.t}
                      </span>
                      <Badge
                        variant={f.k === "msg" ? "accent" : "info"}
                        className="shrink-0"
                      >
                        {KIND_NAME[f.k] || f.k}
                      </Badge>
                      <span className="min-w-0 flex-1 truncate text-[0.76rem]
                                       text-[var(--color-text-secondary)]">
                        <b className="text-[var(--color-text)]">{f.n}</b>
                        <span className="text-[var(--color-text-muted)]">　</span>
                        {f.x}
                      </span>
                    </div>
                  ))}
                  {!realFeed.length && <Blank>暂无数据</Blank>}
                </div>
              </Section>

              <TaskHistorySection {...props} />
            </div>
          </div>
        </div>
      )}
    </PageContainer>
  );
}
