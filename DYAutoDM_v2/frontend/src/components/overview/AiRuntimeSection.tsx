/**
 * AI 运行状态（总览页区块）
 *
 * ## 来源与设计意图
 *
 * 原「AI 获客」页的第 1 块「运行控制」，2026-09-14 用户拍板「打散归类」后迁到此。
 *
 * **为什么放总览页**（归类依据）：
 *   它是**产品级运行状态**（AI 引擎启停在不在跑 + 处理统计），不是某个页面的局部功能。
 *   总览页已在展示「运行中任务 / 实时动态 / 当前查看账号」——AI 就绪状态属于**同一层**。
 *   放这里 = 用户打开首页一眼看到 "AI 在不在跑"。
 *
 *   同理被派往别处的：
 *     · Agent/护栏/黑名单（按 Agent、全局）→ 配置中心「AI 回复引擎」
 *     · 留资线索（按账号）                → 私信页
 *
 * ## 风控铁律
 * 只读写 `/api/ai`（status/start/stop），**不触发任何捕获/昵称查询**。
 *
 * ## 搬迁保真声明
 * 启停逻辑、统计字段、最近回复展示、toast 文案与时长 **逐字搬迁**自 pages/ai.tsx。
 */
import { useQuery } from "@tanstack/react-query";
import { ArrowUpRight } from "lucide-react";
import { PageProps } from "../../api/client";
import { Section, Tone, KeyValue, Blank, SkeletonRows } from "@/components/page/kit";

interface AiStatus {
  ok: boolean; running: boolean; enabled: boolean; processed: number;
  replied: number; leads: number; leads_total: number; errors: number;
  last_reply: string;
}

export default function AiRuntimeSection(props: PageProps) {
  const { api, ready } = props;

  const { data, isLoading, isError, error } = useQuery({
    queryKey: ["ai-status"],
    queryFn: () => api.aiStatus() as unknown as Promise<AiStatus>,
    refetchInterval: 5000,
    enabled: !!ready,
  });

  const st: Partial<AiStatus> = data || {};
  const running = !!st.running;
  const enabled = !!st.enabled;

  return (
    <Section
      title="AI 自动回复"
      description="运行态只读展示 · 启停在「直播」页"
      data-od-id="overview-ai-runtime"
      actions={
        <Tone tone={running ? "ok" : enabled ? "warn" : "mute"}>
          {running ? "运行中" : enabled ? "启动中" : "已停止"}
        </Tone>
      }
    >
      {isLoading ? (
        <SkeletonRows rows={2} />
      ) : isError ? (
        <Blank>
          读取 AI 状态失败：{(error as Error)?.message || "后端无响应"}
          <br />
          <span className="text-[0.72rem]">AI 状态暂未取到，请确认服务已启动。</span>
        </Blank>
      ) : (
        <div className="space-y-2.5">
          <KeyValue
            cols={2}
            items={[
              { k: "已处理", v: st.processed ?? 0, mono: true },
              { k: "已回复", v: st.replied ?? 0, mono: true },
              { k: "线索", v: st.leads_total ?? 0, mono: true },
              { k: "错误", v: st.errors ?? 0, mono: true },
            ]}
          />
          {st.last_reply ? (
            <div className="truncate text-[0.72rem] text-[var(--color-text-muted)]">
              最近回复：{st.last_reply}
            </div>
          ) : null}
          <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[0.72rem] text-[var(--color-text-muted)]">
            <span>启停在「直播」页；回复内容与护栏在「配置中心 → AI 回复引擎」</span>
            {props.setTab ? (
              <button
                type="button"
                onClick={() => props.setTab!("live")}
                className="inline-flex items-center gap-1 text-[var(--color-text-secondary)] underline-offset-2 hover:underline"
              >
                去直播页
                <ArrowUpRight className="h-3 w-3" />
              </button>
            ) : null}
          </div>
        </div>
      )}
    </Section>
  );
}
