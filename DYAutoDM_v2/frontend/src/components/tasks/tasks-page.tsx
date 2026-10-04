/**
 * 任务中心页（重设计版 · 对标 better-douyin 设计体系）
 *
 * 职责：任务列表（直播监听私信引擎 + 评论采集）、导出管理、历史任务（查阅/复用）、定时任务
 *
 * ## 2026-10-04：三页签重构（用户拍板）
 *
 * 用户要求「把评论采集和直播间听的运行任务全部接入任务中心，并将定时任务单独用子页面呈现」。
 * 按拍板结果分为三个子页面：
 *   · **运行任务** —— 直播监听 + 评论采集**混排**（此前是「直播一个表格行 + 采集一堆卡片」，
 *     两套形态；现统一为一种列表，账号/类型/状态/进度/操作列一致）
 *   · **历史任务** —— 原历史任务表（查阅/复用），语义未变
 *   · **定时任务** —— 原页尾 `SchedulerSection`，从「页尾区块」升格为独立子页面
 *
 * ## 设计约束（不得违反）
 *   · **不新增任何后端契约**：三个页签分别读既有 `/api/tasks`（经 overview 快照）、
 *     `/api/crawl/tasks`、`/api/tasks/scheduler`；本页只做**呈现层归一**。
 *   · **不删任何逻辑**：分页、查阅模式跳转、复用配置快照、暂停/继续/停止调用全部保留。
 *   · **采集队列是进程内存**（`storage` 字段如实透出）：UI 如实标注「重启应用即清空」，
 *     不把它渲染成持久化历史（后端模块 docstring 的硬约束）。
 *   · **深归一无后端**：采集/直播「每次执行落同一张任务表」属 ADR-035 后端改造，
 *     未落地；本页不改后端、不假装已归一。
 */
import { useCallback, useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Download, Trash2, Play, Pause, Square, RotateCw, ExternalLink, History, Inbox,
  Activity, Radio, Database, CalendarClock,
} from "lucide-react";
import { PageProps, TaskHistoryItem, ReusePayload } from "../../api/client";
import { Avatar } from "../../components/ui";
import { PageContainer, PageHeader } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Section, Tone, Blank, Toolbar, SegmentedTabs } from "@/components/page/kit";
import { type OverviewExt, type ExportStatsResp, type Api, Th, Td, errMsg } from "./tasks-shared";
import SchedulerSection from "./SchedulerSection";

type TaskTab = "running" | "history" | "scheduled";

export default function TasksPage(props: PageProps) {
  const { push, overview, ready, goReuse } = props;
  const api = props.api as Api;
  const ov = (overview || ({} as OverviewExt)) as OverviewExt;
  const setTab = props.setTab;
  const qc = useQueryClient();

  const [view, setView] = useState<TaskTab>("running");

  // 历史任务列表（App 常驻轮询 "task-history"，页面只读共享缓存，切页不重拉）
  const PAGE_SIZE = 50;
  const [historyPage, setHistoryPage] = useState(0);
  const historyQ = useQuery({
    queryKey: ["task-history", historyPage],
    queryFn: async (): Promise<{ list: TaskHistoryItem[]; total: number }> => {
      const r = (await api.getTaskHistory(PAGE_SIZE, historyPage * PAGE_SIZE)) as {
        ok: boolean;
        list?: TaskHistoryItem[];
        total?: number;
      };
      return { list: r && r.ok ? r.list || [] : [], total: r?.total || 0 };
    },
    enabled: !!ready,
  });
  const history = historyQ.data?.list || [];
  const historyTotal = historyQ.data?.total || 0;

  // ★ 2026-10-04：采集任务（与悬浮窗同源 `/api/crawl/tasks`）。
  //   只在**任务页可见时**轮询（该数据是进程内存，不做后台常驻轮询省请求）；
  //   有 running 的才 3s 一刷，否则 10s。
  //   ⚠️ 轮询频率由**已取回的数据**决定（`crawlTasks`），不依赖
  //      `refetchInterval` 的回调入参 —— 不同 react-query 版本签名不同
  //      （v4 传 query、v5 不传），写成回调会静默失效。
  const crawlTasksQ = useQuery({
    queryKey: ["crawl-tasks-page"],
    queryFn: () => api.crawlTasks(),
    enabled: !!ready,
    staleTime: 2_000,
    refetchInterval: 10_000,
  });
  const crawlTasks = crawlTasksQ.data?.tasks || [];
  const hasRunning = crawlTasks.some((t) => t.status === "running");
  // 有在跑的 ⇒ 提高刷新频率（用第二个 query 不优雅，直接改 interval 需稳定引用；
  //   这里用 refetch 定时器显式驱动，语义最清楚）
  useEffect(() => {
    if (!ready || !hasRunning) return;
    const timer = setInterval(() => crawlTasksQ.refetch(), 3_000);
    return () => clearInterval(timer);
  }, [ready, hasRunning]);

  // ★ 2026-10-04（ADR-035）：直播监听任务改为读 `/api/engine/accounts`
  //   （ADR-002 §5.2 的按账号引擎表，**已支持多账号并发监听**）。
  //   此前任务中心只读 overview 的 `app.state.adm`（最近启动的那一个）⇒ 多账号时
  //   只显示 1 个任务。改用引擎表后，「全部」运行中的直播任务都能列出。
  const RUNNING_STATES = ["starting", "running", "paused", "stopping"];
  const engineQ = useQuery({
    queryKey: ["engine-accounts"],
    queryFn: () => api.listEngineAccounts(),
    enabled: !!ready,
    refetchInterval: 10_000,
  });
  const liveTasks = (engineQ.data?.items || []).filter((e) =>
    RUNNING_STATES.includes(e.state)
  );

  const refreshHistory = useCallback(() => {
    setHistoryPage(0);
    qc.invalidateQueries({ queryKey: ["task-history"] });
  }, [qc]);

  // 跳转：运行中任务 → 直播监听页；历史任务 → 查阅模式
  const gotoTask = (item: TaskHistoryItem) => {
    if (!setTab) return;
    if (item.status === "running") {
      push("已跳转到直播监听页（该任务运行中）");
      setTab("live");
      return;
    }
    // ★ 2026-10-04：历史任务「查看结果」改为跳「任务详情」页（带 id ⇒ 页面拉权威全量）
    push("已进入「任务详情」查看运行结果");
    if (props.goDetail) {
      props.goDetail({
        id: typeof item.id === "number" ? item.id : Number(item.id) || undefined,
        acct: item.acct || "",
        liveId: item.live_id || "",
        kind: (item as { kind?: string }).kind || "live",
        status: item.status || "",
        records: item.records || [],
        startTs: item.start_ts,
        endTs: item.end_ts,
        resultCount: item.result_count,
      });
    } else {
      setTab("tasks");
    }
  };

  // 复用：用历史任务保存的配置快照预填直播监听页
  const reuseTask = (item: TaskHistoryItem) => {
    const cfg = (item.config || {}) as ReusePayload & {
      live_url?: string;
      max_target?: number;
      live_id?: string;
      dm_pool?: { text: string; enabled: boolean }[];
    };
    if (!goReuse) {
      push("当前无法复用（缺少复用入口），请手动到直播监听页配置");
      return;
    }
    goReuse({
      room: cfg.live_url || cfg.live_id || "",
      maxTarget: cfg.max_target ?? cfg.maxTarget,
      interval: cfg.interval,
      delay: cfg.delay,
      dmPool: Array.isArray(cfg.dm_pool) ? cfg.dm_pool : cfg.dmPool,
      acct: cfg.acct,
    });
    push(`已复用任务「${item.acct || ""}」配置到直播监听页`);
  };

  const engineTone: "ok" | "warn" | "mute" =
    ov.engineState === "stopping" || ov.engineState === "starting"
      ? "warn"
      : ov.paused
        ? "warn"
        : "ok";
  const engineLabel =
    ov.engineState === "stopping"
      ? "私信收尾中"
      : ov.engineState === "starting"
        ? "启动中…"
        : ov.paused
          ? "已暂停"
          : "运行中";

  // 🔴 P1-1 修复（2026-09-23）：任务中心的引擎控制必须**显式带账号**。
  // 后端四个控制端点的账号走 `?acct=`（见 client.ts 契约注释），不传则退化为
  // 「单任务回落 / 多任务 409」。本页在多任务并发下必须点名账号，否则按钮必 409。
  // 取值优先级：overview.acct（后端 /api/overview 的引擎归属）→ 历史行里的运行中账号。
  const runningAcct =
    (ov.acct || "").trim() ||
    (history.find((h) => h.status === "running")?.acct || "").trim();

  // ── 运行任务页签：直播运行行（多账号）+ 采集运行行 混排 ────────────────
  //   `ov.running` 仅作**单任务回落**（engine 表查询失败时仍能显示最近那个引擎）。
  const liveRunning = liveTasks.length > 0 || (!!ready && !!ov.running);
  const runningCount = liveTasks.length + crawlTasks.length;
  const hasAnyRunning = crawlTasks.some((t) => t.status === "running") || liveRunning;

  return (
    <PageContainer>
      <PageHeader
        title="任务中心"
        actions={
          ready ? (
            <Button
              size="sm"
              onClick={() =>
                api
                  .exportStats()
                  .then((r: ExportStatsResp) =>
                    push(
                      r && r.ok
                        ? "统计已导出 · " + (r.path || "")
                        : "导出失败: " + ((r && r.error) || "")
                    )
                  )
                  .catch((e: unknown) => push("导出异常: " + errMsg(e)))
              }
            >
              <Download className="h-3.5 w-3.5" />导出统计 xlsx
            </Button>
          ) : (
            <Badge variant="outline">未连接</Badge>
          )
        }
      />

      {/* 子页面切换（三页签） */}
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <SegmentedTabs<TaskTab>
          value={view}
          onChange={setView}
          items={[
            {
              value: "running",
              label: (
                <span className="inline-flex items-center gap-1.5">
                  运行任务
                  {runningCount > 0 && (
                    <span className="rounded-full bg-[var(--color-accent-soft)] px-1.5 text-[0.68rem] tabular-nums">
                      {runningCount}
                    </span>
                  )}
                </span>
              ),
              icon: <Activity className="h-3.5 w-3.5" />,
            },
            {
              value: "history",
              label: "历史任务",
              icon: <History className="h-3.5 w-3.5" />,
            },
            {
              value: "scheduled",
              label: "定时任务",
              icon: <CalendarClock className="h-3.5 w-3.5" />,
            },
          ]}
        />
      </div>

      {/* ══════════════ 页签一：运行任务（直播 + 采集 混排） ══════════════ */}
      {view === "running" && (
        <Section
          title="运行任务"
          description="直播监听与评论采集统一列出；采集为后端进程内存，重启应用即清空"
        >
          <Card className="overflow-hidden" data-od-id="running-tasks">
            <div className="overflow-x-auto">
              <table className="w-full table-fixed border-collapse">
                {/* 列宽契约：与「历史任务」表共用网格，保证同名列（账号/状态/操作）
                    落在同一横向位置。 */}
                <colgroup>
                  <col style={{ width: "18%" }} />
                  <col style={{ width: "10%" }} />
                  <col style={{ width: "12%" }} />
                  <col style={{ width: "18%" }} />
                  <col style={{ width: "10%" }} />
                  <col style={{ width: "8%" }} />
                  <col style={{ width: "24%" }} />
                </colgroup>
                <thead>
                  <tr>
                    <Th>创建时间</Th>
                    <Th>账号</Th>
                    <Th>任务类型</Th>
                    <Th>目标</Th>
                    <Th>状态</Th>
                    <Th>进度</Th>
                    <Th className="text-right">操作</Th>
                  </tr>
                </thead>
                <tbody>
                  {!ready ? (
                    [0, 1, 2].map((i) => (
                      <tr key={i}>
                        {Array.from({ length: 7 }).map((_, j) => (
                          <Td key={j}>
                            <div className="h-4 animate-pulse rounded bg-[var(--color-surface-raised)]" />
                          </Td>
                        ))}
                      </tr>
                    ))
                  ) : !hasAnyRunning ? (
                    <tr>
                      <Td colSpan={7}>
                        <Blank>
                          <Inbox className="mb-1 h-4 w-4" />
                          暂无运行中任务
                        </Blank>
                      </Td>
                    </tr>
                  ) : (
                    <>
                      {/* ── 直播监听运行行（多账号，读 /api/engine/accounts） ── */}
                      {liveTasks.map((e) => {
                        const st = e.state;
                        const tone: "ok" | "warn" =
                          st === "starting" || st === "stopping" || st === "paused" ? "warn" : "ok";
                        const label =
                          st === "starting" ? "启动中…"
                          : st === "stopping" ? "私信收尾中"
                          : st === "paused" ? "已暂停" : "运行中";
                        const acctName = e.acct || "匿名";
                        return (
                          <tr key={`live-${acctName}`}>
                            <Td mono>—</Td>
                            <Td>
                              <div className="inline-flex items-center gap-2">
                                <Avatar name={acctName} h="160" sm />
                                <span>{acctName}</span>
                              </div>
                            </Td>
                            <Td>
                              <span className="inline-flex items-center gap-1">
                                <Radio className="h-3 w-3 opacity-70" />直播监听
                              </span>
                            </Td>
                            <Td mono muted>{e.live_url || e.live_id || "—"}</Td>
                            <Td>
                              <Tone tone={tone}>{label}</Tone>
                              {e.status_msg && st === "stopping" && (
                                <div className="mt-0.5 font-mono text-[0.68rem] text-[var(--color-text-muted)]">
                                  {e.status_msg}
                                </div>
                              )}
                            </Td>
                            <Td mono>已发 {e.sent || 0}</Td>
                            <Td>
                              <Toolbar className="justify-end gap-1">
                                <Button
                                  variant="link"
                                  size="sm"
                                  onClick={() => {
                                    setTab?.("live");
                                    push("已跳转到直播监听页（该任务运行中）");
                                  }}
                                >
                                  进入任务
                                </Button>
                                {st === "paused" ? (
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    onClick={() =>
                                      api.resumeEngine(e.acct || undefined)
                                        .then(() => push("已继续"))
                                        .catch((err: unknown) => push("异常: " + errMsg(err)))
                                    }
                                  >
                                    <Play className="h-3 w-3" />继续
                                  </Button>
                                ) : (
                                  <Button
                                    variant="ghost"
                                    size="sm"
                                    onClick={() =>
                                      api.pauseEngine(e.acct || undefined)
                                        .then(() => push("已暂停"))
                                        .catch((err: unknown) => push("异常: " + errMsg(err)))
                                    }
                                  >
                                    <Pause className="h-3 w-3" />暂停
                                  </Button>
                                )}
                                <Button
                                  variant="danger-outline"
                                  size="sm"
                                  onClick={() =>
                                    api.stopEngine(e.acct || undefined)
                                      .then(() => push("已停止"))
                                      .catch((err: unknown) => push("异常: " + errMsg(err)))
                                  }
                                >
                                  <Square className="h-3 w-3" />停止
                                </Button>
                              </Toolbar>
                            </Td>
                          </tr>
                        );
                      })}

                      {/* ── 直播监听（单任务回落：engine 表为空时读 overview 快照） ── */}
                      {liveTasks.length === 0 && liveRunning && (
                        <tr>
                          <Td mono>{ov.status || "—"}</Td>
                          <Td>
                            <div className="inline-flex items-center gap-2">
                              <Avatar name="引擎" h="160" sm />
                              <span>{ov.acct || "自动私信引擎"}</span>
                            </div>
                          </Td>
                          <Td>
                            <span className="inline-flex items-center gap-1">
                              <Radio className="h-3 w-3 opacity-70" />直播监听
                            </span>
                          </Td>
                          <Td mono muted>{ov.liveUrl || "—"}</Td>
                          <Td>
                            <Tone tone={engineTone}>{engineLabel}</Tone>
                            {ov.statusMsg && ov.engineState === "stopping" && (
                              <div className="mt-0.5 font-mono text-[0.68rem] text-[var(--color-text-muted)]">
                                {ov.statusMsg}
                              </div>
                            )}
                          </Td>
                          <Td mono>
                            已发 {ov.sent || 0}/{ov.limit || 0} · 队列 {ov.queue || 0}
                          </Td>
                          <Td>
                            <Toolbar className="justify-end gap-1">
                              <Button
                                variant="link"
                                size="sm"
                                onClick={() => {
                                  setTab?.("live");
                                  push("已跳转到直播监听页（该任务运行中）");
                                }}
                              >
                                进入任务
                              </Button>
                              {ov.engineState === "stopping" ? (
                                <Button
                                  variant="danger-outline"
                                  size="sm"
                                  title="立即终止仍在发送的存量私信"
                                  onClick={() =>
                                    api
                                      .stopEngine(runningAcct || undefined)
                                      .then(() => push("已硬停止，存量私信终止发送"))
                                      .catch((e: unknown) => push("异常: " + errMsg(e)))
                                  }
                                >
                                  <Square className="h-3 w-3" />停止存量
                                </Button>
                              ) : ov.paused ? (
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  onClick={() =>
                                    api
                                      .resumeEngine(runningAcct || undefined)
                                      .then(() => push("已继续"))
                                      .catch((e: unknown) => push("异常: " + errMsg(e)))
                                  }
                                >
                                  <Play className="h-3 w-3" />继续
                                </Button>
                              ) : (
                                <Button
                                  variant="ghost"
                                  size="sm"
                                  onClick={() =>
                                    api
                                      .pauseEngine(runningAcct || undefined)
                                      .then(() => push("已暂停"))
                                      .catch((e: unknown) => push("异常: " + errMsg(e)))
                                  }
                                >
                                  <Pause className="h-3 w-3" />暂停
                                </Button>
                              )}
                              <Button
                                variant="danger-outline"
                                size="sm"
                                onClick={() =>
                                  api
                                    .stopEngine(runningAcct || undefined)
                                    .then(() => push("已停止"))
                                    .catch((e: unknown) => push("异常: " + errMsg(e)))
                                }
                              >
                                <Square className="h-3 w-3" />停止
                              </Button>
                            </Toolbar>
                          </Td>
                        </tr>
                      )}

                      {/* ── 采集运行行（读 /api/crawl/tasks，进程内存） ── */}
                      {crawlTasks.map((t) => {
                        const total = t.total || t.aweme_ids?.length || 0;
                        const done = t.done || 0;
                        const pct =
                          total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
                        const stTone: "ok" | "warn" | "danger" | "mute" =
                          t.status === "running"
                            ? "ok"
                            : t.status === "done"
                              ? "ok"
                              : t.status === "failed"
                                ? "danger"
                                : t.status === "cancelled"
                                  ? "warn"
                                  : "mute";
                        return (
                          <tr key={t.id}>
                            <Td mono className="whitespace-nowrap">
                              {t.created_at
                                ? new Date(t.created_at * 1000).toLocaleString("zh-CN", {
                                    hour12: false,
                                  })
                                : "—"}
                            </Td>
                            <Td>{t.account || "—"}</Td>
                            <Td>
                              <span className="inline-flex items-center gap-1">
                                <Database className="h-3 w-3 opacity-70" />评论采集
                              </span>
                            </Td>
                            <Td mono muted>
                              {total > 0 ? `${total} 个作品` : "—"}
                            </Td>
                            <Td>
                              <Tone tone={stTone}>
                                {t.status === "running"
                                  ? `采集中 · ${t.phase || "queued"}`
                                  : t.status === "done"
                                    ? "已完成"
                                    : t.status === "failed"
                                      ? "失败"
                                      : t.status === "cancelled"
                                        ? "已取消"
                                        : t.status}
                              </Tone>
                              {t.error && (
                                <div className="mt-0.5 font-mono text-[0.68rem] text-[var(--color-danger,#ef4444)]">
                                  {t.error}
                                </div>
                              )}
                            </Td>
                            <Td mono>
                              <div className="flex items-center gap-1.5">
                                <span className="tabular-nums">
                                  {done}/{total}
                                </span>
                                <div className="h-1 w-10 overflow-hidden rounded-full bg-[var(--color-surface-raised)]">
                                  <div
                                    className="h-full bg-[var(--color-accent)]"
                                    style={{ width: `${pct}%` }}
                                  />
                                </div>
                              </div>
                              <div className="mt-0.5 text-[0.68rem] text-[var(--color-text-muted)]">
                                成功 {t.ok_works ?? 0} · 失败 {t.fail_works ?? 0}
                              </div>
                            </Td>
                            <Td>
                              <Toolbar className="justify-end gap-1">
                                {t.status === "running" ? (
                                  <Button
                                    variant="danger-outline"
                                    size="sm"
                                    onClick={() =>
                                      api
                                        .crawlTaskDelete(t.id)
                                        .then(() => {
                                          push("已移除采集任务（采集循环的下一次上报会 404）");
                                          crawlTasksQ.refetch();
                                        })
                                        .catch((e: unknown) => push("异常: " + errMsg(e)))
                                    }
                                  >
                                    <Trash2 className="h-3 w-3" />移除
                                  </Button>
                                ) : (
                                  <span className="text-[0.7rem] text-[var(--color-text-muted)]">
                                    已结束
                                  </span>
                                )}
                              </Toolbar>
                            </Td>
                          </tr>
                        );
                      })}
                    </>
                  )}
                </tbody>
              </table>
            </div>
          </Card>

          {/* 采集清空：只清已结束（后端保证绝不清 running） */}
          {crawlTasks.length > 0 && (
            <div className="mt-2 flex justify-end">
              <Button
                variant="ghost"
                size="sm"
                onClick={() =>
                  api
                    .crawlTasksClear()
                    .then((r) => {
                      push(`已清理已结束采集任务 ${r?.removed ?? 0} 条`);
                      crawlTasksQ.refetch();
                    })
                    .catch((e: unknown) => push("异常: " + errMsg(e)))
                }
              >
                <Trash2 className="h-3.5 w-3.5" />清空已结束采集任务
              </Button>
            </div>
          )}
        </Section>
      )}

      {/* ══════════════ 页签二：历史任务 ══════════════ */}
      {view === "history" && (
        <Section
          title="历史任务"
          description="运行中任务可跳转直播监听页；历史任务可跳转查阅模式看结果（双击行同）"
          actions={
            history.length > 0 ? (
              <Button
                variant="danger-outline"
                size="sm"
                onClick={() =>
                  api.clearTaskHistory().then(() => refreshHistory()).catch(() => {})
                }
              >
                <Trash2 className="h-3.5 w-3.5" />清空
              </Button>
            ) : undefined
          }
        >
          <div className="-mx-4 -mb-4 overflow-x-auto">
            <table className="w-full table-fixed border-collapse">
              {/* 列宽契约：见运行任务表注释 —— 账号/状态/结果条数/操作 四列与运行表同源对齐 */}
              <colgroup>
                <col style={{ width: "19%" }} />
                <col style={{ width: "9%" }} />
                <col style={{ width: "19%" }} />
                <col style={{ width: "18%" }} />
                <col style={{ width: "9%" }} />
                <col style={{ width: "8%" }} />
                <col style={{ width: "18%" }} />
              </colgroup>
              <thead>
                <tr>
                  <Th>开始时间</Th>
                  <Th>账号</Th>
                  <Th>直播间</Th>
                  <Th>状态</Th>
                  <Th>结果条数</Th>
                  <Th>结束时间</Th>
                  <Th className="text-right">操作</Th>
                </tr>
              </thead>
              <tbody>
                {!ready ? (
                  <tr>
                    <Td colSpan={7}>
                      <Blank>未连接</Blank>
                    </Td>
                  </tr>
                ) : history.length === 0 ? (
                  <tr>
                    <Td colSpan={7}>
                      <Blank>
                        <Inbox className="mb-1 h-4 w-4" />
                        暂无历史任务
                      </Blank>
                    </Td>
                  </tr>
                ) : (
                  <>
                    {history.map((h) => (
                      <tr
                        key={h.id}
                        className="cursor-pointer transition-colors duration-[var(--duration-fast)]
                                   hover:bg-[var(--color-surface-raised)]"
                        onDoubleClick={() => gotoTask(h)}
                        title="双击进入任务 / 查看结果查阅模式"
                      >
                        <Td mono className="whitespace-nowrap">
                          {h.start_ts || "—"}
                        </Td>
                        <Td>{h.acct || "—"}</Td>
                        <Td mono muted>{h.live_id || "—"}</Td>
                        <Td>
                          <Tone
                            tone={
                              h.status === "running" || h.status === "finished" ? "ok" : "warn"
                            }
                          >
                            {h.status === "running"
                              ? "运行中"
                              : h.status === "finished"
                                ? "已完成"
                                : "已停止"}
                          </Tone>
                        </Td>
                        <Td mono>{h.result_count || 0}</Td>
                        <Td mono className="whitespace-nowrap">
                          {h.end_ts || "—"}
                        </Td>
                        <Td>
                          <Toolbar className="justify-end gap-1">
                            <Button variant="secondary" size="sm" onClick={() => gotoTask(h)}>
                              <ExternalLink className="h-3 w-3" />
                              {h.status === "running" ? "进入任务" : "查看结果"}
                            </Button>
                            <Button
                              variant="ghost"
                              size="sm"
                              onClick={() => reuseTask(h)}
                              title="复用该任务启动时的配置，重新运行"
                            >
                              <RotateCw className="h-3 w-3" />复用
                            </Button>
                          </Toolbar>
                        </Td>
                      </tr>
                    ))}
                    {(historyPage + 1) * PAGE_SIZE < historyTotal && (
                      <tr>
                        <Td colSpan={7} className="text-center">
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setHistoryPage((p: number) => p + 1)}
                          >
                            <History className="h-3 w-3" />
                            加载更多（已显示 {history.length} / {historyTotal} 条）
                          </Button>
                        </Td>
                      </tr>
                    )}
                  </>
                )}
              </tbody>
            </table>
          </div>
        </Section>
      )}

      {/* ══════════════ 页签三：定时任务（子页面） ══════════════ */}
      {view === "scheduled" && <SchedulerSection {...props} />}
    </PageContainer>
  );
}
