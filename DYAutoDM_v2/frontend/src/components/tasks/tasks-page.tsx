/**
 * 任务中心页（重设计版 · 对标 better-douyin 设计体系）
 *
 * 职责：任务列表（直播监听私信引擎）、导出管理、历史任务（查阅/复用）
 *
 * ## 本次改动（重设计）
 * - 旧 `.table` / `.table-scroll` / `.sk` / 内联 `style={{color:"var(--muted)"}}`
 *   → 令牌化表格（`Th` / `Td` 具名组件）+ Badge/Tone + SkeletonRows
 * - 行操作按钮：`.btn text sm` → `<Button variant="link|ghost|danger-outline">`
 * - **业务逻辑零改动**（分页、查阅模式跳转、复用配置快照、暂停/继续/停止调用全保持）
 */
import { useCallback, useEffect, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Download, Trash2, Play, Pause, Square, RotateCw, ExternalLink, History, Inbox,
} from "lucide-react";
import { PageProps, TaskHistoryItem, ReusePayload } from "../../api/client";
import { Avatar } from "../../components/ui";
import { PageContainer, PageHeader } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Section, Tone, Blank, Toolbar } from "@/components/page/kit";
import { type OverviewExt, type ExportStatsResp, type Api, Th, Td, errMsg } from "./tasks-shared";
import SchedulerSection from "./SchedulerSection";

export default function TasksPage(props: PageProps) {
  const { push, overview, ready, goReuse } = props;
  const api = props.api as Api;
  const ov = (overview || ({} as OverviewExt)) as OverviewExt;
  const setTab = props.setTab;
  const qc = useQueryClient();

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
    push("已跳转到直播监听页查阅模式，查看任务运行结果");
    setTab("live");
    setTimeout(() => {
      if (props.goReview) {
        props.goReview({
          acct: item.acct || "",
          liveId: item.live_id || "",
          records: item.records || [],
          startTs: item.start_ts,
          endTs: item.end_ts,
        });
      }
    }, 150);
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

      {/* 运行中任务 */}
      <Card className="mb-4 overflow-hidden" data-od-id="task-list">
        <div className="overflow-x-auto">
          <table className="w-full table-fixed border-collapse">
            {/* 列宽契约：与「历史任务」表共用网格，保证同名列（账号/状态/结果条数/操作）
                落在同一横向位置。两表列数不同（9 vs 7），故用百分比同源对齐
                —— 改列宽只改这里与下表同名的数值。 */}
            <colgroup>
              <col style={{ width: "19%" }} />
              <col style={{ width: "9%" }} />
              <col style={{ width: "9%" }} />
              <col style={{ width: "10%" }} />
              <col style={{ width: "9%" }} />
              <col style={{ width: "9%" }} />
              <col style={{ width: "9%" }} />
              <col style={{ width: "8%" }} />
              <col style={{ width: "18%" }} />
            </colgroup>
            <thead>
              <tr>
                <Th>创建时间</Th>
                <Th>账号</Th>
                <Th>任务类型</Th>
                <Th>目标</Th>
                <Th>状态</Th>
                <Th>耗时</Th>
                <Th>结果条数</Th>
                <Th>重试</Th>
                <Th className="text-right">操作</Th>
              </tr>
            </thead>
            <tbody>
              {ready && ov.running ? (
                <tr>
                  <Td mono>{ov.status || "—"}</Td>
                  <Td>
                    <div className="inline-flex items-center gap-2">
                      <Avatar name="引擎" h="160" sm />
                      <span>自动私信引擎</span>
                    </div>
                  </Td>
                  <Td>直播监听私信</Td>
                  <Td mono muted>{ov.liveUrl || "—"}</Td>
                  <Td>
                    <Tone tone={engineTone}>{engineLabel}</Tone>
                    {ov.statusMsg && ov.engineState === "stopping" && (
                      <div className="mt-0.5 font-mono text-[0.68rem]
                                      text-[var(--color-text-muted)]">
                        {ov.statusMsg}
                      </div>
                    )}
                  </Td>
                  <Td mono>
                    {ov.sent || 0}/{ov.limit || 0}
                  </Td>
                  <Td mono>{ov.queue || 0}</Td>
                  <Td mono>0</Td>
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
              ) : !ready ? (
                [0, 1, 2].map((i) => (
                  <tr key={i}>
                    {Array.from({ length: 9 }).map((_, j) => (
                      <Td key={j}>
                        <div className="h-4 animate-pulse rounded bg-[var(--color-surface-raised)]" />
                      </Td>
                    ))}
                  </tr>
                ))
              ) : (
                <tr>
                  <Td colSpan={9}>
                    <Blank>暂无运行中任务</Blank>
                  </Td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </Card>

      {/* 采集任务（★ 2026-10-04 接线）
          用户定调：悬浮窗负责「采集哪些、怎么采集」，**任务页负责看结果与进度**。
          此前采集任务只在悬浮窗内显示，任务页读的是另一套（/api/tasks 历史），
          ⇒ 「批量采集没进任务系统、任务页看不到」。
          现把 `/api/crawl/tasks` 接到任务页，与历史任务并列。
          ⚠️ 该数据是**进程级内存**（storage 字段如实标注），重启即丢；
             故只在页面可见时轮询，且如实标注来源，不假装持久化。 */}
      <Section
        title="采集任务"
        description="由采集悬浮窗触发；显示进度与结果（后端进程内存，重启应用即清空）"
      >
        {crawlTasks.length === 0 ? (
          <p className="text-[0.78rem] text-[var(--color-text-muted)]">
            暂无采集任务 —— 在内容总览勾选作品后用采集悬浮窗启动
          </p>
        ) : (
          <div className="space-y-2">
            {crawlTasks.map((t) => {
              const total = t.total || t.aweme_ids?.length || 0;
              const done = t.done || 0;
              const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
              return (
                <div
                  key={t.id}
                  className="rounded-[10px] border border-[var(--color-border)] p-2.5"
                >
                  <div className="flex items-center justify-between text-[0.78rem]">
                    <span className="truncate">
                      {t.account} · {t.phase || "queued"} · {t.status}
                    </span>
                    <span className="font-mono text-[var(--color-text-muted)]">
                      {done}/{total}
                    </span>
                  </div>
                  <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-[var(--color-surface-raised)]">
                    <div
                      className="h-full bg-[var(--color-accent)] transition-[width]"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  <div className="mt-1 text-[0.68rem] text-[var(--color-text-muted)]">
                    成功 {t.ok_works ?? 0} · 失败 {t.fail_works ?? 0}
                    {t.error ? ` · ${t.error}` : ""}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </Section>

      {/* 历史任务 */}
      <Section
        title="历史任务"
        description="运行中任务可跳转直播监听页；历史任务可跳转查阅模式看结果（双击行同）"
        actions={
          history.length > 0 ? (
            <Button
              variant="danger-outline"
              size="sm"
              onClick={() => api.clearTaskHistory().then(() => refreshHistory()).catch(() => {})}
            >
              <Trash2 className="h-3.5 w-3.5" />清空
            </Button>
          ) : undefined
        }
      >
        <div className="-mx-4 -mb-4 overflow-x-auto">
          <table className="w-full table-fixed border-collapse">
            {/* 列宽契约：见上表注释 —— 账号/状态/结果条数/操作 四列与运行中任务表同源对齐 */}
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
                <tr><Td colSpan={7}><Blank>未连接</Blank></Td></tr>
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
                      <Td mono className="whitespace-nowrap">{h.start_ts || "—"}</Td>
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
                      <Td mono className="whitespace-nowrap">{h.end_ts || "—"}</Td>
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

      {/* 定时任务中心（ADR-018 F4）—— 置于页尾：日常主要用上面的运行/历史任务，
          调度中心是低频且默认休眠的功能，不抢主视线 */}
      <SchedulerSection {...props} />
    </PageContainer>
  );
}
