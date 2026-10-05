/**
 * 留资情况板块（★ 2026-10-04 用户要求：任务详情也显示留资）
 *
 * ## 归属逻辑（诚实边界，必须读）
 * `ai_leads` 表**没有 task_id 字段**（只有 account / conv_id），无法把线索
 * 精确归属到「具体哪个任务」。故按 **账号 + 任务时间窗** 近似匹配：只展示
 * `account === acct` 且 `created_at ∈ [任务开始, 任务结束]` 的线索。
 * 代价：同账号在窗口内**其他渠道**（人工私信、窗口重叠的其它任务）产生的
 * 线索也会列出。板块内已明示此边界，**不假装能精确归属**。
 *
 * ## 兜底
 * 无 `start`（内存态任务尚未落库）⇒ 后端不做时间过滤，按账号取全部线索，
 * 并在角标注明「内存态·未按时间筛选」。
 */
import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { PhoneCall } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Section, Tone, Blank } from "@/components/page/kit";
import { Th, Td, fmtTs } from "./tasks-shared";

const STATUS_LABEL: Record<string, string> = {
  new: "新线索",
  followed: "已跟进",
  invalid: "无效",
};
const TYPE_LABEL: Record<string, string> = { phone: "手机号", wechat: "微信号" };
const SOURCE_LABEL: Record<string, string> = {
  realtime: "实时",
  backfill: "历史补全",
};

interface LeadRow {
  id: number;
  account: string;
  peer_name: string;
  contact_type: string;
  contact_value: string;
  status: string;
  created_at: number;
  source?: string;
}

/**
 * 本地时间字符串（`YYYY-MM-DD HH:MM:SS`）→ 毫秒时间戳。
 *
 * 🔴 必须用 `Date.parse(str.replace(" ", "T"))`，与详情页 `durationOf` 同口径：
 * 统一口径优先，避免同一文件里出现两种转换方式导致窗口与耗时互相矛盾。
 *
 * 后端 `created_at` 是**秒级 epoch（系统 localtime）**，`tasks.start_ts` 是
 * **本地时间字符串**；实测服务器 SQLite `strftime('%s','now','localtime')`
 * 与 `time.mktime(time.localtime())` 一致（tzname=中国标准时间），故前端
 * `Date.parse` 得到的毫秒与 `created_at*1000` 在同一口径上可比。
 *
 * ⚠️ 边界：该对齐要求**浏览器时区与服务器一致（CST/UTC+8）**。用户换时区运行
 * 前端时会系统性偏移 —— 届时应改为带时区标记的时间戳传输，而不是在此处加偏移。
 */
function toMs(v?: string): number {
  const s = (v || "").trim();
  if (!s) return 0;
  const ms = Date.parse(s.replace(" ", "T"));
  return isFinite(ms) ? ms : 0;
}

export function TaskLeadsSection(props: {
  acct: string;
  start: string;
  end: string;
  push: (m: string, ms?: number) => void;
  api: {
    aiLeads(limit?: number, startMs?: number, endMs?: number): Promise<
      { ok: boolean; items: Record<string, unknown>[] }
    >;
    aiLeadStatus(id: number, status: string): Promise<{ ok: boolean }>;
  };
}) {
  const startMs = toMs(props.start);
  const endMs = toMs(props.end);

  const { data } = useQuery({
    queryKey: ["task-leads", props.acct, startMs, endMs],
    queryFn: () => props.api.aiLeads(200, startMs, endMs),
    enabled: props.acct.length > 0,
  });

  const items = (data?.items || []) as unknown as LeadRow[];
  const fresh = items.filter((r) => r.account === props.acct);

  return (
    <Section
      title="留资情况"
      description="本任务时间窗内该账号捕获的留资线索"
      actions={
        <div className="flex items-center gap-2">
          <Badge variant="outline">
            <PhoneCall className="mr-1 inline h-3 w-3" />
            {fresh.length} 条
          </Badge>
          {!props.start ? <Badge variant="outline">内存态·未按时间筛选</Badge> : null}
        </div>
      }
    >
      <div className="mb-2 flex items-start gap-1.5 text-[0.7rem] text-[var(--color-text-muted)]">
        <span className="mt-px shrink-0">ℹ️</span>
        <span>
          线索表无 task_id 字段，此处按「账号 + 任务时间窗」近似匹配 —— 同一账号在此窗口内
          其他渠道产生的线索也会列出，不等于确由本任务产生。
        </span>
      </div>
      {fresh.length === 0 ? (
        <Blank>本任务时间窗内该账号没有留资线索</Blank>
      ) : (
        <Card className="overflow-hidden p-0">
          <div className="overflow-x-auto">
            <table className="w-full table-fixed border-collapse">
              <colgroup>
                <col style={{ width: "14%" }} />
                <col style={{ width: "13%" }} />
                <col style={{ width: "9%" }} />
                <col style={{ width: "24%" }} />
                <col style={{ width: "13%" }} />
                <col style={{ width: "13%" }} />
                <col style={{ width: "14%" }} />
              </colgroup>
              <thead>
                <tr>
                  <Th>状态</Th>
                  <Th>客户</Th>
                  <Th>类型</Th>
                  <Th>联系方式</Th>
                  <Th>来源</Th>
                  <Th>会话</Th>
                  <Th>捕获时间</Th>
                </tr>
              </thead>
              <tbody>
                {fresh.map((ld) => (
                  <tr key={ld.id} data-od-id="detail-lead">
                    <Td>
                      <LeadStatusBadge lead={ld} api={props.api} push={props.push} />
                    </Td>
                    <Td className="truncate">{ld.peer_name || "—"}</Td>
                    <Td>
                      <Badge variant="outline">
                        {TYPE_LABEL[ld.contact_type] || ld.contact_type}
                      </Badge>
                    </Td>
                    <Td mono className="truncate">
                      {ld.contact_value}
                    </Td>
                    <Td>
                      <Tone
                        tone={(ld.source || "realtime") === "backfill" ? "warn" : "info"}
                      >
                        {SOURCE_LABEL[ld.source || "realtime"] || ld.source}
                      </Tone>
                    </Td>
                    <Td className="truncate">{ld.account || "—"}</Td>
                    <Td mono>{fmtTs(ld.created_at)}</Td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </Section>
  );
}

/** 线索状态：行内下拉（与私信中心留资卡片同交互）。 */
export function LeadStatusBadge(props: {
  lead: { id: number; status: string };
  api: { aiLeadStatus(id: number, status: string): Promise<{ ok: boolean }> };
  push: (m: string, ms?: number) => void;
}) {
  const qc = useQueryClient();
  const [busy, setBusy] = useState(false);
  const tone: "ok" | "warn" | "danger" | "info" | "mute" =
    props.lead.status === "followed"
      ? "ok"
      : props.lead.status === "invalid"
        ? "warn"
        : "info";

  const on = async (v: string) => {
    if (!v || v === props.lead.status) return;
    setBusy(true);
    try {
      // 2026-10-05：成功后此前既不 invalidate 也不 push ⇒ select 弹回原值，
      // 用户以为没生效。改后刷新列表并给成功反馈。
      await props.api.aiLeadStatus(props.lead.id, v);
      qc.invalidateQueries({ queryKey: ["task-leads"] });
      props.push("线索状态已更新", 3000);
    } catch (e) {
      props.push(`状态更新失败: ${String(e)}`, 6000);
    } finally {
      setBusy(false);
    }
  };

  return (
    <span className="inline-flex items-center gap-1">
      <Tone tone={tone}>{STATUS_LABEL[props.lead.status] || props.lead.status}</Tone>
      <select
        className="rounded border border-[var(--color-border)] bg-[var(--color-bg-2)] px-1 py-0.5 text-[0.7rem] text-[var(--color-text)] disabled:opacity-50"
        value={props.lead.status}
        onChange={(e) => void on(e.target.value)}
        disabled={busy}
      >
        <option value="new">新线索</option>
        <option value="followed">已跟进</option>
        <option value="invalid">无效</option>
      </select>
    </span>
  );
}
