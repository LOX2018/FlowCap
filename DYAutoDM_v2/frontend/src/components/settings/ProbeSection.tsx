/**
 * 能力巡检（配置中心 · 系统 分区）
 *
 * ## 为什么存在（迁移依据，2026-09-30）
 *
 * 用户拍板「总览页纯只读看板，控制操作回各自功能页」。
 * 但能力巡检的**唯一**触发点原本是 `overview/CapabilityHealthSection`
 * 的「立即巡检」按钮 —— 全仓 `runProbePatrol()` 仅此一处调用。
 * 若只从总览页摘掉按钮而不补入口，`/api/probe/patrol` 这条能力就**无人可点**
 * （后端端点完好、前端零引用 = 能力在位但不可得）。
 *
 * 故本组件承接该操作入口，总览页保留**只读**的健康态展示。
 *
 * ## 风控契约（继承原组件，不可破坏）
 *
 * 巡检**只读本地事实**（SQLite + 本项目运行日志），**零网络零浏览器**。
 * 本组件只调 `GET /api/probe/status`（读）与 `POST /api/probe/patrol`（跑一轮），
 * 不触发任何捕获 / 昵称查询 / 浏览器动作。
 *
 * ## 三态语义（禁止二态）
 *
 * healthy / degraded（带覆盖率）/ failed / unknown（**无法判定**，不得显示为健康）。
 */
import { useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { RefreshCw, ShieldCheck, ShieldAlert, ShieldX, HelpCircle } from "lucide-react";
import { PageProps } from "../../api/client";
import { Button } from "@/components/ui/button";
import { SetCard, SetCardHead, SetCardBody, SetCardFoot } from "@/components/page/set-card";
import { Tone, KeyValue, Blank } from "@/components/page/kit";
import { errMsg } from "@/lib/utils";

type ProbeState = "healthy" | "degraded" | "failed" | "unknown";

interface ProbeLastResult {
  at?: string;
  state?: ProbeState;
  elapsed?: number;
  attention?: string[];
  summary?: {
    healthy?: number; degraded?: number; failed?: number; unknown?: number; total?: number;
  };
  error?: string;
}

interface PatrolStatus {
  enabled?: boolean;
  interval_min?: number;
  runs?: number;
  last_run_at?: string;
  next_run_at?: number | null;
  last_result?: ProbeLastResult;
  errors?: { at: string; error: string }[];
}

/** 能力名 → 中文（对齐 02_效果定义与探针.md §2 的业务域口径）。 */
const CAP_NAME: Record<string, string> = {
  kernel_availability: "指纹内核",
  conversation_capture: "会话捕获",
  send_delivery: "私信发送",
  credential_identity: "凭证身份",
  live_danmaku: "直播弹幕",
  ai_lead_capture: "AI 留资捕获",
  message_integrity: "消息完整度",
};

const STATE_META: Record<ProbeState, {
  label: string;
  tone: "ok" | "warn" | "danger" | "mute";
  Icon: typeof ShieldCheck;
}> = {
  healthy: { label: "健康", tone: "ok", Icon: ShieldCheck },
  degraded: { label: "降级", tone: "warn", Icon: ShieldAlert },
  failed: { label: "失效", tone: "danger", Icon: ShieldX },
  unknown: { label: "未定论", tone: "mute", Icon: HelpCircle },
};

function fmtNext(ts?: number | null): string {
  if (!ts) return "—";
  const sec = Math.floor(ts - Date.now() / 1000);
  if (sec <= 0) return "即将";
  if (sec < 60) return `${sec}s 后`;
  return `${Math.round(sec / 60)} 分钟后`;
}

export default function ProbeSection(props: PageProps) {
  const { api, push } = props;
  const qc = useQueryClient();

  const { data, isError, error, isFetching } = useQuery({
    queryKey: ["probe-status"],
    queryFn: () =>
      api.getProbeStatus() as unknown as Promise<{ ok: boolean; patrol: PatrolStatus }>,
    refetchInterval: 30000,
    enabled: !!props.ready,
  });

  const patrol: PatrolStatus = (data?.patrol || {}) as PatrolStatus;
  const last: ProbeLastResult = (patrol.last_result || {}) as ProbeLastResult;
  const sum = last.summary || {};
  const st: ProbeState = (last.state as ProbeState) || "unknown";
  const meta = STATE_META[st] || STATE_META.unknown;
  const { Icon } = meta;

  const runNow = useCallback(async () => {
    try {
      await api.runProbePatrol();
      await qc.invalidateQueries({ queryKey: ["probe-status"] });
      push("已跑一轮能力巡检（只读本地事实）", 4000);
    } catch (e) {
      push(`巡检失败: ${errMsg(e)}`, 8000);
    }
  }, [api, push, qc]);

  return (
    <SetCard data-od-id="settings-probe-section">
      <SetCardHead
        title="能力巡检"
        description="只读本地事实（数据库 + 运行日志），零网络零浏览器"
        right={
          <Tone tone={meta.tone}>
            <Icon className="mr-1 h-3.5 w-3.5" />
            {meta.label}
          </Tone>
        }
      />
      <SetCardBody>
        {isError ? (
          <Blank>
            读取巡检状态失败：{errMsg(error)}
            <br />
            <span className="text-[0.72rem]">数据源 GET /api/probe/status —— 请确认后端已启动。</span>
          </Blank>
        ) : (
          <div className="space-y-3">
            <KeyValue
              cols={2}
              items={[
                { k: "健康", v: sum.healthy ?? 0, mono: true },
                { k: "降级", v: sum.degraded ?? 0, mono: true },
                { k: "失效", v: sum.failed ?? 0, mono: true },
                { k: "未定论", v: sum.unknown ?? 0, mono: true },
                {
                  k: "定时巡检",
                  v: patrol.enabled === false
                    ? "已关闭"
                    : `每 ${patrol.interval_min ?? 15} 分钟 · 已跑 ${patrol.runs ?? 0} 轮 · 下次 ${fmtNext(patrol.next_run_at)}`,
                },
                { k: "上次巡检", v: last.at || patrol.last_run_at || "—", mono: true },
              ]}
            />

            {/* 需关注项（degraded/failed 的 能力@账号） */}
            {(last.attention || []).length > 0 ? (
              <div className="flex flex-wrap gap-2" data-od-id="settings-probe-attention">
                {(last.attention || []).map((a) => {
                  const [cap, acct] = String(a).split("@");
                  return (
                    <Tone key={a} tone="danger">
                      <ShieldAlert className="mr-1 h-3 w-3" />
                      {CAP_NAME[cap] || cap}
                      {acct ? ` · ${acct}` : ""}
                    </Tone>
                  );
                })}
              </div>
            ) : null}

            {last.error ? (
              <Tone tone="danger">巡检异常：{String(last.error).slice(0, 120)}</Tone>
            ) : null}

            {!patrol.runs ? (
              <div className="text-[0.72rem] text-[var(--color-text-muted)]">
                尚无巡检记录 —— 后端启动后会自动跑首轮；也可点下方「立即巡检」。
              </div>
            ) : (
              <div className="text-[0.72rem] text-[var(--color-text-muted)]">
                总览页「能力健康」区块展示同一份结果的只读摘要。
              </div>
            )}
          </div>
        )}
      </SetCardBody>
      <SetCardFoot>
        <Button size="sm" variant="outline" onClick={runNow} disabled={isFetching} data-od-id="settings-probe-run-now">
          <RefreshCw className="mr-1 h-3.5 w-3.5" />
          立即巡检
        </Button>
        <span className="text-[0.7rem] text-[var(--color-text-muted)]">
          巡检不触发任何捕获、昵称查询或浏览器动作
        </span>
      </SetCardFoot>
    </SetCard>
  );
}
