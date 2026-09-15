/**
 * 留资线索（私信页子 tab）
 *
 * ## 来源与设计意图
 *
 * 原「AI 获客」页的第 4 块，2026-09-14 用户拍板「打散归类」后迁到此。
 *
 * **为什么放私信页**（归类依据，非拍脑袋）：
 *   实测 `ai_leads` 表含 `account` 列 + `UNIQUE(account, conv_id, contact_type, contact_value)`
 *   —— 线索是**按账号**维度的产出，且**产生在私信对话里**。
 *   它的出生地就是私信，放回这里 = 用户在聊天的地方管理聊天产生的线索。
 *
 *   同理被派往别处的：
 *     · Agent/护栏/黑名单（按 Agent、全局）→ 配置中心「AI 回复引擎」
 *     · 运行控制（全局运行状态）          → 总览页
 *
 * ## 风控铁律
 * 只读写 `/api/ai/leads*`，不触发捕获/昵称查询。
 *
 * ## 搬迁保真声明
 * 列表渲染、状态流转、CSV 导出 URL **逐字搬迁**自 pages/ai.tsx。
 */
import { useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Download } from "lucide-react";
import { PageProps } from "../../api/client";
import { Button } from "@/components/ui/button";
import { Tone, Row, Blank, Toolbar } from "@/components/page/kit";
import { errMsg } from "@/lib/utils";

interface Lead {
  id: number; account: string; conv_id: string; peer_name: string;
  contact_type: string; contact_value: string; status: string;
  created_at: number; source_text: string;
}

export default function LeadsSection(props: PageProps) {
  const { api, push } = props;
  const qc = useQueryClient();

  const { data: leads } = useQuery({
    queryKey: ["ai-leads"],
    queryFn: () => api.aiLeads(),
  });

  const leadStatus = useCallback(async (id: number, status: string) => {
    try {
      await api.aiLeadStatus(id, status);
      await qc.invalidateQueries({ queryKey: ["ai-leads"] });
    } catch (e) {
      push(`失败: ${errMsg(e)}`, 6000);
    }
  }, [api, push, qc]);

  return (
    <div>
      <div className="mb-2.5 flex items-center gap-2">
        <a
          href="http://127.0.0.1:8000/api/ai/leads/export"
          target="_blank"
          rel="noreferrer"
        >
          <Button variant="secondary" size="sm">
            <Download className="h-3.5 w-3.5" />导出 CSV
          </Button>
        </a>
        <span className="text-[0.72rem] text-[var(--color-text-muted)]">
          共 {leads?.items?.length ?? 0} 条 · 客户在对话中发出手机号/微信号后自动捕获
        </span>
      </div>

      {(leads?.items || []).length === 0 && (
        <Blank>暂无线索。客户在对话中发出手机号/微信号后自动捕获。</Blank>
      )}

      <div className="divide-y divide-[var(--color-border)]">
        {(leads?.items || []).map((raw) => {
          const ld = raw as unknown as Lead;
          return (
            <Row key={ld.id} className="!px-0 py-2">
              <Tone
                tone={
                  ld.status === "new" ? "accent" : ld.status === "followed" ? "ok" : "mute"
                }
              >
                {ld.status === "new" ? "新线索" : ld.status === "followed" ? "已跟进" : "无效"}
              </Tone>
              <span className="font-mono text-[0.78rem] font-semibold text-[var(--color-text)]">
                {ld.contact_value}
              </span>
              <span className="min-w-0 flex-1 truncate text-[0.72rem]
                               text-[var(--color-text-muted)]">
                {ld.contact_type === "phone" ? "手机号" : "微信号"} ·{" "}
                {ld.peer_name || ld.conv_id.slice(0, 12)} · {ld.account}
              </span>
              <Toolbar className="shrink-0 gap-1">
                {ld.status !== "followed" && (
                  <Button variant="secondary" size="sm" onClick={() => leadStatus(ld.id, "followed")}>
                    已跟进
                  </Button>
                )}
                {ld.status !== "invalid" && (
                  <Button variant="ghost" size="sm" onClick={() => leadStatus(ld.id, "invalid")}>
                    无效
                  </Button>
                )}
              </Toolbar>
            </Row>
          );
        })}
      </div>
    </div>
  );
}
