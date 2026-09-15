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
import { useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Play, Square } from "lucide-react";
import { PageProps } from "../../api/client";
import { Button } from "@/components/ui/button";
import { Tone, Section, Toolbar } from "@/components/page/kit";
import { errMsg } from "@/lib/utils";

interface AiStatus {
  ok: boolean; running: boolean; enabled: boolean; processed: number;
  replied: number; leads: number; leads_total: number; errors: number;
  last_reply: string;
}

export default function AiRuntimeSection(props: PageProps) {
  const { api, push } = props;
  const qc = useQueryClient();

  const { data: status } = useQuery({
    queryKey: ["ai-status"],
    queryFn: () => api.aiStatus() as unknown as Promise<AiStatus>,
    refetchInterval: 5000,
  });

  const running = !!status?.running;
  const enabled = !!status?.enabled;
  const st: Partial<AiStatus> = status || {};

  const toggleRun = useCallback(async () => {
    try {
      if (enabled) await api.aiStop();
      else await api.aiStart();
      await qc.invalidateQueries({ queryKey: ["ai-status"] });
      await qc.invalidateQueries({ queryKey: ["ai-config"] });
      push(enabled ? "AI 自动回复已停止" : "AI 自动回复已启动（全自动监听）", 5000);
    } catch (e) {
      push(`操作失败: ${errMsg(e)}`, 8000);
    }
  }, [enabled, api, push, qc]);

  return (
    <Section
      title="AI 自动回复"
      data-od-id="overview-ai-runtime"
      actions={
        <Tone tone={running ? "ok" : enabled ? "warn" : "mute"}>
          {running ? "运行中" : enabled ? "启动中" : "已停止"}
        </Tone>
      }
    >
      <Toolbar className="mb-2.5">
        <Button variant={running ? "danger" : "default"} onClick={toggleRun}>
          {running
            ? <><Square className="h-4 w-4" />停止监听</>
            : <><Play className="h-4 w-4" />启动监听</>}
        </Button>
        <span className="text-[0.74rem] text-[var(--color-text-secondary)]">
          已处理 <b className="text-[var(--color-text)]">{st.processed ?? 0}</b> · 已回复{" "}
          <b className="text-[var(--color-text)]">{st.replied ?? 0}</b> · 线索{" "}
          <b className="text-[var(--color-text)]">{st.leads_total ?? 0}</b> · 错误{" "}
          <b className="text-[var(--color-text)]">{st.errors ?? 0}</b>
        </span>
      </Toolbar>
      {st.last_reply && (
        <div className="text-[0.72rem] text-[var(--color-text-muted)]">
          最近回复：{st.last_reply}
        </div>
      )}
      <div className="mt-2 text-[0.68rem] text-[var(--color-text-muted)]">
        回复内容与护栏在「配置中心 → AI 回复引擎」；留资线索在「私信」页。
      </div>
    </Section>
  );
}
