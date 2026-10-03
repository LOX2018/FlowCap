/**
 * 能力健康（总览页区块）—— M1 能力探针的 UI 出口
 *
 * ## 设计意图（归类依据）
 *
 * 探针回答的是「**现在这个能力行不行**」（`02_效果定义与探针.md` 的 M1）。
 * 与「AI 运行状态」同层：都是**产品级运行状态**，不是某个页面的局部功能。
 * 故与 `AiRuntimeSection` 并列放总览页 —— 用户打开首页一眼看到五域健康度。
 *
 * ## 风控铁律（不可破坏）
 *
 * 探针**只读本地事实**（SQLite + 本项目运行日志），**零网络零浏览器**。
 * 本组件只调 `/api/probe/status`（读）与 `/api/probe/patrol`（手动跑一轮），
 * 两者都不触发任何捕获/昵称查询/浏览器动作。**不要**在此处加「更新会话」类按钮。
 *
 * ## 三态语义（禁止二态）
 *
 * healthy 确认成功 / degraded 部分成功（带覆盖率）/ failed 确认失败 /
 * unknown **无法判定**（如观测窗内未活动）—— unknown 不得显示成健康。
 */
import { useQuery } from "@tanstack/react-query";
import { ShieldCheck, ShieldAlert, ShieldX, HelpCircle, ArrowUpRight } from "lucide-react";
import { PageProps } from "../../api/client";
import { Section, Tone, Blank, SkeletonRows } from "@/components/page/kit";

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

/** 能力名 → 中文（对齐 02_效果定义与探针.md §2 的业务域口径） */
const CAP_NAME: Record<string, string> = {
  kernel_availability: "指纹内核",
  conversation_capture: "会话捕获",
  send_delivery: "私信发送",
  credential_identity: "凭证身份",
  live_danmaku: "直播弹幕",
  ai_lead_capture: "AI 留资捕获",
  message_integrity: "消息完整度",
};

type ToneKey = "ok" | "warn" | "danger" | "accent" | "mute" | "info";

const STATE_META: Record<ProbeState, { label: string; tone: ToneKey; Icon: typeof ShieldCheck }> = {
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

export default function CapabilityHealthSection(props: PageProps) {
  const { api, ready } = props;

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["probe-status"],
    queryFn: () =>
      api.getProbeStatus() as unknown as Promise<{ ok: boolean; patrol: PatrolStatus }>,
    refetchInterval: 30000,
    enabled: !!ready,
  });

  const patrol: PatrolStatus = (data?.patrol || {}) as PatrolStatus;
  const last: ProbeLastResult = (patrol.last_result || {}) as ProbeLastResult;
  const sum = last.summary || {};
  const st: ProbeState = (last.state as ProbeState) || "unknown";
  const meta = STATE_META[st] || STATE_META.unknown;
  const { Icon } = meta;

  return (
    <Section
      title="能力健康"
      description="只读摘要 · 巡检操作在「配置中心 → 系统」"
      data-od-id="overview-capability-health"
      actions={
        <Tone tone={meta.tone}>
          <Icon className="mr-1 h-3.5 w-3.5" />
          {meta.label}
        </Tone>
      }
    >
      {isLoading ? (
        <SkeletonRows rows={2} />
      ) : isError ? (
        <Blank>
          读取巡检状态失败：{(error as Error)?.message || "后端无响应"}
          <br />
          <span className="text-[0.72rem]">能力状态暂未取到，请确认服务已启动。</span>
        </Blank>
      ) : (
        <div className="space-y-2.5">
          {/* 汇总行：四态计数（纯文本，避免与状态徽章混淆）+ 巡检节奏 */}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.78rem] text-[var(--color-text-secondary)]">
            <span>
              健康 <b className="text-[var(--color-text)]">{sum.healthy ?? 0}</b> · 降级{" "}
              <b className="text-[var(--color-text)]">{sum.degraded ?? 0}</b> · 失效{" "}
              <b className="text-[var(--color-text)]">{sum.failed ?? 0}</b> · 未定论{" "}
              <b className="text-[var(--color-text)]">{sum.unknown ?? 0}</b>
              {sum.total ? `（共 ${sum.total} 项）` : ""}
            </span>
            <span className="text-[var(--color-text-muted)]">
              {patrol.enabled === false
                ? "定时巡检：已关闭"
                : `每 ${patrol.interval_min ?? 15} 分钟 · 下次 ${fmtNext(patrol.next_run_at)}`}
            </span>
          </div>

          {/* 需关注项（degraded/failed 的 能力@账号） */}
          {(last.attention || []).length > 0 ? (
            <div className="flex flex-wrap gap-2" data-od-id="overview-probe-attention">
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

          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.72rem] text-[var(--color-text-muted)]">
            <span>
              上次巡检 {last.at || patrol.last_run_at || "—"}
              {typeof last.elapsed === "number" ? ` · 耗时 ${last.elapsed}s` : ""}
            </span>
            {props.setTab ? (
              <button
                type="button"
                onClick={() => props.setTab!("settings")}
                className="inline-flex items-center gap-1 text-[var(--color-text-secondary)] underline-offset-2 hover:underline"
              >
                去巡检
                <ArrowUpRight className="h-3 w-3" />
              </button>
            ) : null}
          </div>

          {last.error ? (
            <Tone tone="danger">巡检异常：{String(last.error).slice(0, 120)}</Tone>
          ) : null}
        </div>
      )}
    </Section>
  );
}
