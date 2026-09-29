/**
 * 任务历史速览（总览页区块）—— ADR-018 F2
 *
 * ## 设计意图（归类依据）
 *
 * 回答「**最近几轮任务跑了没、跑得怎么样**」—— 产品级运行结果，
 * 与「能力健康」「账号凭证健康」同层，放总览页让用户不必进任务中心就能回顾。
 *
 * 区分职责（避免重复）：
 *   · 本区块：只读历史任务**结果概览**（最近 N 条 + 成功/失败/条数统计）
 *   · 总览页已有「运行中任务」：展示**当前**发送进度（已发/上限/队列）
 *   · 任务中心 tasks-page：全量历史 + 分页 + 查阅/复用操作（本区块不复制）
 *
 * ## 数据来源（ADR-018 决策：零新采集，只用既有端点）
 *
 * - `GET /api/tasks/history?limit=&offset=`（client.ts: `api.getTaskHistory()`）
 *   —— backend `api/tasks.py::@router.get("/history")`，数据源 `tasks_history.py
 *   ::list_history()`（SQLite `tasks` 表，含 config/records 快照）。
 * - 后端失败时返回 `{ok:false, list:[], total:0, error}` —— **不是 HTTP 错误**，
 *   所以除以 `ok` 判定数据是否真的取到，禁止把空 list 当成「没有任务」
 *   （那可能是后端读库失败，属于典型的假成功）。
 *
 * ## 铁律（禁止假成功）
 *
 * - `ok=false` → 显示后端给的 error 原文，不显示「暂无任务」。
 * - 请求异常（HTTP 非 2xx / 断连）→ 显示异常信息，不显示 0。
 * - 只有 `ok=true && list 为空` 才允许显示「暂无历史任务」。
 */
import { useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import { RotateCcw, CheckCircle2, XCircle, Loader2, ListChecks } from "lucide-react";
import { PageProps } from "../../api/client";
import { Button } from "@/components/ui/button";
import { Section, Row, RowText, Tone, SkeletonRows, Blank } from "@/components/page/kit";
import { errMsg } from "@/lib/utils";

/** 历史任务条目（对齐 tasks_history.list_history 的单行结构）。 */
interface HistoryTask {
  id: number;
  acct?: string;
  live_id?: string;
  start_ts?: string;
  end_ts?: string;
  status?: string;
  result_count?: number;
  records?: Record<string, unknown>[];
  config?: { live_url?: string; live_id?: string };
}

interface HistoryResp {
  ok?: boolean;
  list?: HistoryTask[];
  total?: number;
  error?: string;
}

const MAX_SHOW = 5;

/** 任务状态 → { 文案, 色调 }（未知状态如实回显原文，不假装成成功）。 */
const STATUS_META: Record<string, { label: string; tone: "ok" | "warn" | "danger" | "mute" }> = {
  running: { label: "进行中", tone: "warn" },
  finished: { label: "已完成", tone: "ok" },
  stopped: { label: "已停止", tone: "mute" },
};

/** 从 records 快照里数成功/失败（后端 result_count 只给总数，不给成败拆分）。 */
function countOutcome(t: HistoryTask): { success: number; fail: number } {
  const recs = Array.isArray(t.records) ? t.records : [];
  let success = 0;
  let fail = 0;
  for (const r of recs) {
    const st = String((r as { status?: unknown })?.status || "");
    if (st === "sent") success += 1;
    else if (st === "fail") fail += 1;
  }
  return { success, fail };
}

function fmtTime(ts?: string): string {
  if (!ts) return "—";
  const s = String(ts);
  return s.length > 8 ? s.slice(5, 16) : s;
}

export default function TaskHistorySection(props: PageProps) {
  const { api, ready, setTab } = props;

  const { data, isLoading, isError, error, refetch, isFetching } = useQuery({
    queryKey: ["task-history", 0, MAX_SHOW],
    queryFn: async () =>
      (await api.getTaskHistory(MAX_SHOW, 0)) as unknown as HistoryResp,
    refetchInterval: 15000,
    enabled: !!ready,
  });

  const refresh = useCallback(() => {
    void refetch();
  }, [refetch]);

  const ok = data?.ok === true;
  const list = Array.isArray(data?.list) ? data!.list! : [];
  const items = list.slice(0, MAX_SHOW);

  return (
    <Section
      title="任务历史速览"
      description={`最近 ${MAX_SHOW} 条`}
      actions={
        ok && list.length ? (
          <Button size="sm" variant="outline" onClick={refresh} disabled={isFetching}>
            <RotateCcw className="mr-1 h-3.5 w-3.5" />
            刷新
          </Button>
        ) : null
      }
    >
      {isLoading ? (
        <SkeletonRows rows={3} />
      ) : isError ? (
        <Blank>
          读取任务历史失败：{errMsg(error)}
          <br />
          <span className="text-[0.72rem]">数据源 GET /api/tasks/history —— 请确认后端已启动。</span>
        </Blank>
      ) : !ok ? (
        // 后端明确返回 ok=false：读库失败，必须如实暴露，不能显示「暂无任务」
        <Blank>
          后端未能取到任务历史：{data?.error || "未知原因"}
          <br />
          <span className="text-[0.72rem]">
            数据源 GET /api/tasks/history 返回 ok=false —— 不代表没有任务。
          </span>
        </Blank>
      ) : !items.length ? (
        <Blank>暂无历史任务 —— 在「直播」页开始监听并结束后会产生记录。</Blank>
      ) : (
        <div className="space-y-1.5">
          {items.map((t) => {
            const st = STATUS_META[String(t.status || "")] || {
              label: String(t.status || "未知"),
              tone: "mute" as const,
            };
            const { success, fail } = countOutcome(t);
            const room = String(t.config?.live_url || t.live_id || "—");
            return (
              <Row key={t.id}>
                <ListChecks className="h-3.5 w-3.5 shrink-0 text-[var(--color-text-muted)]" />
                <RowText
                  primary={`${t.acct || "未指定账号"} · ${room}`}
                  secondary={`${fmtTime(t.start_ts)} 起 · 结果 ${t.result_count ?? 0} 条`}
                  mono
                />
                <div className="flex shrink-0 flex-wrap items-center gap-1.5">
                  <Tone tone="mute">
                    <CheckCircle2 className="mr-1 h-3 w-3" />
                    {success}
                  </Tone>
                  {fail ? (
                    <Tone tone="danger">
                      <XCircle className="mr-1 h-3 w-3" />
                      {fail}
                    </Tone>
                  ) : null}
                  <Tone tone={st.tone}>
                    {st.label === "进行中" ? (
                      <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                    ) : null}
                    {st.label}
                  </Tone>
                </div>
              </Row>
            );
          })}
          <button
            type="button"
            onClick={() => setTab && setTab("tasks")}
            className="mt-1 text-[0.72rem] text-[var(--color-text-muted)] underline-offset-2 hover:underline"
          >
            查看全部历史（任务中心）→
          </button>
        </div>
      )}
    </Section>
  );
}
