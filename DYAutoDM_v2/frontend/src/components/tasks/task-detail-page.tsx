/**
 * 任务详情页（★ 2026-10-04，用户定调）
 *
 * 用户原话：「评论查阅模式也需要显示主导航栏，页面升级为『任务详情』丰富页面参数，
 * 并归入任务中心。」
 *
 * ## 设计意图
 *
 * 把原先「全屏浮层、无侧栏、只服务直播」的**查阅模式**，升级为**任务中心下的
 * 独立页面**（走 App 路由 ⇒ **主导航/侧栏可见**），并**丰富参数**：
 *
 *   ① 任务参数区：类型 / 账号 / 目标 / 开始·结束·耗时 / 结果数 / 状态 / 错误码
 *      + 按 kind 展开的类型专属参数（直播=直播间；采集=关键词·类型·目标·条数）；
 *   ② 结果明细：按 kind 分流 —— 直播用 `ReviewMode embedded`（与实时流同款表格）；
 *      采集用评论明细表（cid/nickname/text/digg/ip/ts）。
 *
 * ## 两个入口（都指向本页）
 *   · 直播页「进入查阅模式」→ `goDetail({records, ...})`（**内存态**，运行中任务
 *     records 尚未落库，故直接带 records）；
 *   · 任务中心「查看结果」→ `goDetail({id, ...})`（**历史任务**，页面据 id 拉
 *     `GET /api/tasks/{id}` 取权威全量）。
 *
 * ## 诚实边界
 *   · 任务不存在 / 读取失败 ⇒ 如实报错并给「返回任务中心」，**不伪造空表**；
 *   · 采集明细直接来自任务 `records`（采集落库时写入的条目快照），不另行发请求。
 */
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowLeft, Radio, Database, CalendarClock, ListChecks } from "lucide-react";
import { PageProps, TaskDetailPayload, TaskDetail } from "../../api/client";
import { PageContainer, PageHeader } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Section, Tone, Blank } from "@/components/page/kit";
import { Th, Td } from "./tasks-shared";
import { LoadingState, ErrorState } from "@/components/ui/empty-state";
import { ReviewMode } from "@/components/live/LiveReviewMode";
import { recordsToRows } from "@/components/live/live-shared";
import { TaskLeadsSection } from "./task-leads-section";

const KIND_META: Record<string, { label: string; icon: React.ElementType }> = {
  live: { label: "直播监听", icon: Radio },
  crawl: { label: "评论采集", icon: Database },
  scheduled: { label: "定时任务", icon: CalendarClock },
};

const STATUS_LABEL: Record<string, string> = {
  running: "运行中",
  finished: "已完成",
  stopped: "已停止",
  failed: "失败",
  cancelled: "已取消",
};

function toneOf(status: string): "ok" | "warn" | "danger" | "mute" {
  if (status === "finished") return "ok";
  if (status === "running") return "ok";
  if (status === "failed") return "danger";
  if (status === "stopped" || status === "cancelled") return "warn";
  return "mute";
}

/** 秒级时间戳 / 字符串 → 可读时间。 */
function fmtTs(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "number" && v > 0) {
    // 秒级（<1e12）或毫秒级
    const ms = v < 1e12 ? v * 1000 : v;
    try {
      return new Date(ms).toLocaleString("zh-CN", { hour12: false });
    } catch {
      return String(v);
    }
  }
  return String(v);
}

/** 计算耗时（start/end 字符串或时间戳）。 */
function durationOf(start?: string, end?: string): string {
  const s = Date.parse((start || "").replace(" ", "T"));
  const e = Date.parse((end || "").replace(" ", "T"));
  if (!isFinite(s) || !isFinite(e) || e < s) return "—";
  const sec = Math.round((e - s) / 1000);
  if (sec < 60) return `${sec} 秒`;
  const m = Math.floor(sec / 60);
  const r = sec % 60;
  if (m < 60) return `${m} 分 ${r} 秒`;
  return `${Math.floor(m / 60)} 时 ${m % 60} 分`;
}

/** 单条「参数」格。 */
function Param({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="rounded-[var(--radius-sm)] border border-[var(--color-border)] px-3 py-2">
      <div className="text-[0.68rem] text-[var(--color-text-muted)]">{label}</div>
      <div className="mt-0.5 break-all text-[0.82rem] text-[var(--color-text)]">{children}</div>
    </div>
  );
}

export default function TaskDetailPage(props: PageProps) {
  const payload: TaskDetailPayload | null = props.detailPayload || null;
  const taskId = payload?.id || 0;

  // 有 id ⇒ 拉权威全量（历史任务）；否则用载荷自带（内存态）
  const q = useQuery<{ ok: boolean; task?: TaskDetail; error?: string }>({
    queryKey: ["task-detail", taskId],
    queryFn: () => props.api.getTaskDetail(taskId),
    enabled: !!props.ready && taskId > 0,
    staleTime: 10_000,
  });

  const back = () => props.setTab?.("tasks");

  const t: TaskDetail | undefined = taskId > 0 ? q.data?.task : undefined;
  // 统一视图模型：DB 任务优先，其次载荷
  const vm = useMemo(() => {
    if (t) {
      return {
        kind: t.kind || (t.live_id ? "live" : ""),
        acct: t.acct || "",
        status: t.status || "",
        target: t.live_id || "",
        start: t.start_ts || "",
        end: t.end_ts || "",
        resultCount: t.result_count || 0,
        errorCode: t.error_code || "",
        params: (t.params || {}) as Record<string, unknown>,
        records: (t.records || []) as Record<string, unknown>[],
      };
    }
    const p = payload || ({} as TaskDetailPayload);
    return {
      kind: p.kind || "live",
      acct: p.acct || "",
      status: p.status || "",
      target: p.liveId || "",
      start: p.startTs || "",
      end: p.endTs || "",
      resultCount: p.resultCount ?? (p.records || []).length,
      errorCode: p.errorCode || "",
      params: (p.params || {}) as Record<string, unknown>,
      records: (p.records || []) as Record<string, unknown>[],
    };
  }, [t, payload]);

  const kindMeta = KIND_META[vm.kind] || { label: vm.kind || "未知", icon: ListChecks };
  const KindIcon = kindMeta.icon;

  return (
    <PageContainer>
      <PageHeader
        title="任务详情"
        description="任务参数与结果明细（归入任务中心；主导航可见）"
        actions={
          <Button variant="secondary" size="sm" onClick={back} data-od-id="detail-back">
            <ArrowLeft className="h-3.5 w-3.5" />返回任务中心
          </Button>
        }
      />

      {!payload ? (
        <Blank>未选择任务 —— 请从「任务中心」点「查看结果」，或在直播监听页点「进入查阅模式」</Blank>
      ) : taskId > 0 && q.isPending ? (
        <LoadingState />
      ) : taskId > 0 && q.isError ? (
        <ErrorState
          message={String((q.error as Error)?.message || "任务详情读取失败")}
          onRetry={() => void q.refetch()}
        />
      ) : taskId > 0 && q.data && q.data.ok === false ? (
        <Blank>{q.data.error || "任务不存在"}</Blank>
      ) : (
        <>
          {/* ══════════ ① 任务参数区（丰富参数）══════════ */}
          <Section
            className="mb-4"
            title="任务参数"
            description="本次任务的完整上下文（类型 / 账号 / 目标 / 时间 / 结果 / 错误码）"
            actions={
              <div className="flex items-center gap-2">
                <Badge variant="outline">
                  <KindIcon className="mr-1 inline h-3 w-3" />
                  {kindMeta.label}
                </Badge>
                {vm.status ? (
                  <Tone tone={toneOf(vm.status)}>{STATUS_LABEL[vm.status] || vm.status}</Tone>
                ) : null}
                {taskId > 0 ? <Badge variant="outline">#{taskId}</Badge> : <Badge variant="outline">内存态</Badge>}
              </div>
            }
          >
            <div className="grid grid-cols-2 gap-2.5 md:grid-cols-4">
              <Param label="账号">{vm.acct || "—"}</Param>
              <Param label="目标">
                {vm.kind === "crawl"
                  ? String(vm.params?.keyword || vm.params?.target || vm.target || "—")
                  : vm.target || "—"}
              </Param>
              <Param label="开始时间">{vm.start || "—"}</Param>
              <Param label="结束时间">{vm.end || "—"}</Param>
              <Param label="耗时">{durationOf(vm.start, vm.end)}</Param>
              <Param label="结果条数">{vm.resultCount}</Param>
              <Param label="任务类型">{kindMeta.label}</Param>
              <Param label="错误码">
                {vm.errorCode ? (
                  <span className="text-[var(--color-danger)]">{vm.errorCode}</span>
                ) : (
                  <span className="text-[var(--color-text-muted)]">无</span>
                )}
              </Param>
            </div>

            {/* 类型专属参数 */}
            {vm.kind === "crawl" && (
              <div className="mt-2.5 grid grid-cols-2 gap-2.5 md:grid-cols-4">
                <Param label="关键词">{String(vm.params?.keyword || "—")}</Param>
                <Param label="采集类型">{String(vm.params?.kind || "—")}</Param>
                <Param label="目标作品">{String(vm.params?.target || "—")}</Param>
                <Param label="条数上限">{String(vm.params?.limit ?? vm.resultCount)}</Param>
              </div>
            )}
            {vm.kind === "scheduled" && (
              <div className="mt-2.5 grid grid-cols-2 gap-2.5 md:grid-cols-4">
                <Param label="调度任务 id">{String(vm.params?.sched_id || "—")}</Param>
                <Param label="调度类型">{String(vm.params?.sched_kind || "—")}</Param>
                <Param label="名称">{String(vm.params?.name || "—")}</Param>
              </div>
            )}
          </Section>

          {/* ══════════ ② 结果明细（按 kind 分流）══════════ */}
          {vm.kind === "live" ? (
            <Section
              title="结果明细"
              description="直播监听结果（发送记录 · 与实时流同款表格，可直接查每条的私信状态与失败原因）"
            >
              {vm.records.length === 0 ? (
                <Blank>
                  本次任务没有结果记录
                  {vm.status === "running" ? "（任务仍在运行，结束后写入）" : ""}
                </Blank>
              ) : (
                <ReviewMode
                  embedded
                  rows={recordsToRows(vm.records)}
                  onClose={back}
                  push={props.push}
                  sendDm={() => props.push?.("请在直播监听页对运行中任务发送私信")}
                  goMsg={props.goMsg}
                />
              )}
            </Section>
          ) : vm.kind === "crawl" ? (
            <Section
              title="结果明细"
              description="评论采集结果（评论条目快照：昵称 / 内容 / 点赞 / IP / 时间）"
            >
              {vm.records.length === 0 ? (
                <Blank>本次采集没有结果记录</Blank>
              ) : (
                <Card className="overflow-hidden p-0">
                  <div className="overflow-x-auto">
                    <table className="w-full table-fixed border-collapse">
                      <colgroup>
                        <col style={{ width: "16%" }} />
                        <col style={{ width: "40%" }} />
                        <col style={{ width: "10%" }} />
                        <col style={{ width: "14%" }} />
                        <col style={{ width: "20%" }} />
                      </colgroup>
                      <thead>
                        <tr>
                          <Th>昵称</Th>
                          <Th>评论内容</Th>
                          <Th>点赞</Th>
                          <Th>IP</Th>
                          <Th>时间</Th>
                        </tr>
                      </thead>
                      <tbody>
                        {vm.records.map((r, i) => (
                          <tr key={String(r.cid ?? r.awemeId ?? i)}>
                            <Td>{String(r.nickname || r.nick_name || "—")}</Td>
                            <Td className="truncate" >{String(r.text || r.content || "—")}</Td>
                            <Td mono>{String(r.digg ?? r.digg_count ?? 0)}</Td>
                            <Td mono>{String(r.ip || r.ip_label || "—")}</Td>
                            <Td mono>{fmtTs(r.ts ?? r.create_time)}</Td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </Card>
              )}
            </Section>
          ) : (
            <Section title="结果明细" description="该类型任务暂无明细渲染（仅展示参数）">
              <Blank>该任务类型没有可展示的明细（参数见上）</Blank>
            </Section>
          )}

          {/* ══════════ ③ 留资情况（任务时间窗内该账号）══════════ */}
          {vm.acct ? (
            <TaskLeadsSection
              acct={vm.acct}
              start={vm.start}
              end={vm.end}
              push={props.push}
              api={props.api}
            />
          ) : null}
        </>
      )}
    </PageContainer>
  );
}
