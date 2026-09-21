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
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Activity, Send, MessageSquare, Cpu, Users } from "lucide-react";
import { PageProps } from "../../api/client";
import { Avatar, KIND_NAME } from "../../components/ui";
import AiRuntimeSection from "@/components/overview/AiRuntimeSection";
import CapabilityHealthSection from "@/components/overview/CapabilityHealthSection";
import { PageContainer, PageHeader } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
  Section, Stat, StatRow, Row, RowText, KeyValue, Tone,
  SkeletonRows, Blank, SegmentedTabs,
} from "@/components/page/kit";
import { type OverviewExt, type StatsResp, type Account, type FeedItem, ProgressBar } from "./overview-shared";

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
        description="系统运行状态与账号概况"
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
        <div className="grid gap-4">
          {/* 账号切换 */}
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

          {/* 核心指标 */}
          <Section bare data-od-id="overview-stats">
            {ready ? (
              <StatRow cols={4}>
                <Stat
                  label="已发私信" icon={<Send className="h-3.5 w-3.5" />}
                  value={`${(ov.sent || 0).toLocaleString()}/${ov.limit || 0}`}
                  delta={`待发 ${ov.queue || 0}`} accent
                />
                <Stat
                  label="捕获评论" icon={<MessageSquare className="h-3.5 w-3.5" />}
                  value={stats ? stats.total.toLocaleString() : "0"}
                  unit="条"
                  delta={`已发 ${stats ? stats.sent : 0}`}
                />
                <Stat
                  label="凭证 / 私信守护" icon={<Users className="h-3.5 w-3.5" />}
                  value={
                    (ov.browserDaemon && ov.browserDaemon.alive ? "凭证就绪" : "凭证离线")
                  }
                  delta={ov.recvDaemon && ov.recvDaemon.alive ? "私信在线" : "私信离线"}
                />
                <Stat
                  label="引擎状态" icon={<Cpu className="h-3.5 w-3.5" />}
                  value={ov.running ? (ov.paused ? "已暂停" : "运行中") : "已停止"}
                  delta={ov.status || ""}
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

          <div className="grid grid-cols-2 gap-4">
            {/* 实时动态 */}
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

            {/* 运行中任务 */}
            <Section title="运行中任务" data-od-id="overview-tasks">
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
                      <Activity className="h-3.5 w-3.5" />
                      引擎未启动 —— 可在「直播」页开始监听
                    </div>
                  )}
                </div>
              ) : (
                <SkeletonRows rows={2} />
              )}
            </Section>

            {/* AI 运行状态 —— 原「AI 获客」页的运行控制，按作用域（全局运行状态）
                归类到总览页（2026-09-14 打散归类）。 */}
            <AiRuntimeSection {...props} />

            {/* 能力健康（M1 能力探针）—— 同为「产品级运行状态」，2026-09-21 P1 收尾。
                只读本地事实（DB + 本项目日志），零网络零浏览器。 */}
            <CapabilityHealthSection {...props} />
          </div>
        </div>
      )}
    </PageContainer>
  );
}
