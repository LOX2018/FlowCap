/**
 * 定时任务中心（ADR-018 F4）
 *
 * ## 🔴 最重要的设计约束：如实呈现「休眠态」
 *
 * 用户 2026-09-27 拍板（ADR-018 D1）：**自动外发默认休眠** `enabled=false`。
 * 定时自动向陌生人批量发私信是本项目**迄今最大风控敞口**，出厂即关闭。
 *
 * 因此本组件的第一职责不是「让用户开起来」，而是**让用户一眼知道它现在是关的**：
 *   · 休眠时显示醒目的休眠徽标 + 说明文案，而不是一个可以随手点开的开关
 *   · 后端尚未接线 / 取不到状态时，显示「状态未知」，**绝不显示成可用**
 *   · 任务执行被闸门拒绝时，原样展示后端返回的拒绝原因（如「非允许时段」/「额度上限」）
 *     —— 绝不把「被拦截」包装成「执行成功」
 *
 * 这是项目铁律「禁止假成功」在本功能上的具体化。
 *
 * 开关本身**不在前端控制**（只由后端环境变量决定），前端不提供「一键开启自动外发」
 * —— 那会成为绕过休眠的暗门，与 ADR-018 D1 直接冲突。
 */
import { useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, Play, Square, RefreshCw, ShieldAlert } from "lucide-react";
import type { PageProps } from "../../api/client";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Section, Tone } from "@/components/page/kit";
import { type Api, Th, Td, errMsg } from "./tasks-shared";

/** 调度中心状态（后端 `GET /api/tasks/scheduler` 的 state 字段）。 */
interface SchedulerState {
  enabled?: boolean;
  scheduler_enabled?: boolean;
  auto_send_enabled?: boolean;
  active_hours?: number[];
  task_count?: number;
  tick_count?: number;
  next_run_at?: number | null;
  handler_kinds?: string[];
  running?: boolean;
}

interface SchedulerTask {
  id: string;
  name?: string;
  kind?: string;
  account?: string;
  interval?: number;
  enabled?: boolean;
  run_count?: number;
  fail_count?: number;
  last_run_at?: number;
  needs_send?: boolean;
  last_result?: { ok?: boolean; skipped?: boolean; reason?: string; error?: string } | null;
}

interface SchedulerResp {
  ok?: boolean;
  state?: SchedulerState;
  tasks?: SchedulerTask[];
  error?: string;
}

const KIND_LABEL: Record<string, string> = {
  keyword_process: "关键词处理",
  hot_comment_crawl: "热门视频评论采集",
  auto_dm_send: "自动发私信",
};

function fmtTime(v?: number | null): string {
  if (!v) return "—";
  try {
    return new Date(v * 1000).toLocaleString("zh-CN", { hour12: false });
  } catch {
    return "—";
  }
}

export default function SchedulerSection(props: PageProps) {
  const api = props.api as Api;
  const qc = useQueryClient();

  const q = useQuery<SchedulerResp>({
    queryKey: ["task-scheduler"],
    queryFn: () => (api as unknown as { getScheduler: () => Promise<SchedulerResp> }).getScheduler(),
    enabled: !!props.ready,
  });

  const refresh = useCallback(() => {
    qc.invalidateQueries({ queryKey: ["task-scheduler"] });
  }, [qc]);

  const data = q.data;
  const st: SchedulerState = data?.state || {};
  const tasks: SchedulerTask[] = data?.tasks || [];
  const dormant = st.scheduler_enabled === false;

  const act = useCallback(
    async (fn: () => Promise<{ ok?: boolean; error?: string }>, okMsg: string) => {
      try {
        const r = await fn();
        if (r && r.ok === false) {
          props.push?.(`操作未生效：${r.error || "后端拒绝（可能处于休眠态）"}`);
        } else {
          props.push?.(okMsg);
        }
      } catch (e) {
        props.push?.(`操作失败：${errMsg(e)}`);
      }
      refresh();
    },
    [props, refresh],
  );

  return (
    <Section
      title="定时任务中心"
      description="定时自动处理关键词 / 采集热门视频评论 / 自动发私信。出厂默认休眠，需后端显式开启后才会真正执行。"
      actions={
        <div className="flex items-center gap-2">
          <Button variant="ghost" size="sm" onClick={refresh}>
            <RefreshCw className="h-3.5 w-3.5" />刷新
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() =>
              act(() => (api as never as { schedulerStart: () => Promise<{ ok?: boolean; error?: string }> }).schedulerStart(),
                "已请求启动调度中心（若总开关未开，后端会拒绝）")
            }
          >
            <Play className="h-3.5 w-3.5" />启动
          </Button>
          <Button
            variant="outline"
            size="sm"
            onClick={() =>
              act(() => (api as never as { schedulerStop: () => Promise<{ ok?: boolean; error?: string }> }).schedulerStop(),
                "已停止调度中心")
            }
          >
            <Square className="h-3.5 w-3.5" />停止
          </Button>
        </div>
      }
    >
      {/* 🔴 休眠提示：优先于一切其它内容呈现，避免用户误以为功能在用 */}
      {dormant && (
        <div className="mb-3 flex items-start gap-2 rounded-md border border-[var(--color-warn-border,rgba(234,179,8,0.45))] bg-[var(--color-warn-bg,rgba(234,179,8,0.10))] px-3 py-2">
          <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0 text-[var(--color-warn,#eab308)]" />
          <div className="text-[0.76rem] leading-relaxed">
            <div className="font-semibold text-[var(--color-warn,#eab308)]">
              当前处于休眠态，所有定时任务（含自动发私信）均不会执行
            </div>
            <div className="mt-0.5 text-[var(--color-text-secondary)]">
              这是刻意设计（ADR-018 D1）：自动批量外发风控风险高，出厂默认关闭。
              需后端设置 <code className="font-mono">DY_TASK_SCHEDULER_ENABLED=1</code>（自动外发另需{" "}
              <code className="font-mono">DY_AUTO_SEND_ENABLED=1</code>）并重启后才会生效。
              前端不提供开启入口，避免误开。
            </div>
          </div>
        </div>
      )}

      {q.isError && (
        <div className="mb-3 text-[0.76rem] text-[var(--color-danger,#ef4444)]">
          状态未知：{errMsg(q.error)}
          <span className="ml-1 text-[var(--color-text-muted)]">
            （取不到状态时按「不可用」处理，不显示为可用）
          </span>
        </div>
      )}

      {/* 状态概览 */}
      <div className="mb-3 flex flex-wrap items-center gap-2 text-[0.76rem]">
        {dormant ? (
          <Badge variant="warning">休眠（默认）</Badge>
        ) : st.enabled ? (
          <Badge variant="success">运行中</Badge>
        ) : (
          <Badge variant="outline">已停止</Badge>
        )}
        <span className="text-[var(--color-text-secondary)]">
          自动外发：{st.auto_send_enabled ? "已开启" : "关闭"}
        </span>
        {Array.isArray(st.active_hours) && st.active_hours.length === 2 && (
          <span className="text-[var(--color-text-secondary)]">
            允许时段：{st.active_hours[0]}:00–{st.active_hours[1]}:00
          </span>
        )}
        <span className="text-[var(--color-text-secondary)]">任务数：{st.task_count ?? 0}</span>
        <span className="text-[var(--color-text-secondary)]">轮询次数：{st.tick_count ?? 0}</span>
        <span className="text-[var(--color-text-secondary)]">下次执行：{fmtTime(st.next_run_at)}</span>
      </div>

      {/* 任务列表 */}
      <Card>
        {tasks.length === 0 ? (
          <div className="px-3 py-6 text-center text-[0.76rem] text-[var(--color-text-muted)]">
            暂无定时任务（可通过后端接口添加；本页未来会提供新增表单）
          </div>
        ) : (
          <div className="-mx-4 -mb-4 overflow-x-auto">
            <table className="w-full table-fixed border-collapse">
              <colgroup>
                <col style={{ width: "22%" }} />
                <col style={{ width: "16%" }} />
                <col style={{ width: "14%" }} />
                <col style={{ width: "12%" }} />
                <col style={{ width: "10%" }} />
                <col style={{ width: "26%" }} />
              </colgroup>
              <thead>
                <tr>
                  <Th>任务</Th>
                  <Th>类型</Th>
                  <Th>账号</Th>
                  <Th>间隔</Th>
                  <Th>状态</Th>
                  <Th>最近结果</Th>
                </tr>
              </thead>
              <tbody>
                {tasks.map((t) => (
                  <tr key={t.id} className="hover:bg-[var(--color-bg-hover,rgba(127,127,127,0.08))]">
                    <Td>
                      <div className="flex items-center gap-1.5">
                        <CalendarClock className="h-3.5 w-3.5 shrink-0 opacity-70" />
                        <span className="truncate">{t.name || t.id}</span>
                      </div>
                    </Td>
                    <Td>
                      <span className="flex items-center gap-1">
                        {KIND_LABEL[t.kind || ""] || t.kind || "—"}
                        {t.needs_send && <Badge variant="warning">外发</Badge>}
                      </span>
                    </Td>
                    <Td muted>{t.account || "—"}</Td>
                    <Td mono>{t.interval ? `${Math.round(t.interval / 60)} 分钟` : "—"}</Td>
                    <Td>
                      {t.enabled ? <Tone tone="ok">启用</Tone> : <Tone tone="mute">停用</Tone>}
                    </Td>
                    <Td>
                      {!t.last_result ? (
                        <span className="text-[var(--color-text-muted)]">未执行（{fmtTime(t.last_run_at)}）</span>
                      ) : t.last_result.ok ? (
                        <Tone tone="ok">成功 · {fmtTime(t.last_run_at)}</Tone>
                      ) : t.last_result.skipped ? (
                        // 被闸门拦截：原样展示原因，绝不包装成成功
                        <Tone tone="warn">未执行：{t.last_result.reason || "被闸门拦截"}</Tone>
                      ) : (
                        <Tone tone="danger">失败：{t.last_result.error || "未知错误"}</Tone>
                      )}
                    </Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </Section>
  );
}
