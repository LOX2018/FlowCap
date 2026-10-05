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
 * ## 呈现（★ 2026-10-04：由列表改为**表格**）
 *
 * 用户要求「用表格形式呈现」。列为：状态 / 联系方式 / 类型 / 客户 / 账号 /
 * 捕获时间 / 原文 / 操作。新增可排序（按捕获时间）与类型·状态筛选，便于线索多时定位。
 *
 * ## 风控铁律
 * 只读写 `/api/ai/leads*`，不触发捕获/昵称查询。
 */
import { useCallback, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, ArrowUpDown, CornerDownRight, ChevronDown } from "lucide-react";
import { PageProps } from "../../api/client";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Tone, Blank, Toolbar } from "@/components/page/kit";
import { Th, Td } from "./message-shared";
import {
  DropdownMenu, DropdownMenuTrigger, DropdownMenuContent, DropdownMenuItem,
} from "@/components/ui/dropdown-menu";
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from "@/components/ui/select";
import { errMsg } from "@/lib/utils";

interface Lead {
  id: number; account: string; peer_name: string;
  contact_type: string; contact_value: string; status: string;
  created_at: number; source_text: string;
  /** realtime = 实时（WS/网页经 AI）；backfill = 历史补全（「更新会话」抓到） */
  source?: string;
  /** 会话 ID（★ 2026-10-04「跳转原文」用：goConv 据此精确打开该客户对话）。 */
  conv_id?: string;
}

const STATUS_LABEL: Record<string, string> = {
  new: "新线索", followed: "已跟进", invalid: "无效",
};
const TYPE_LABEL: Record<string, string> = { phone: "手机号", wechat: "微信号" };
/** 来源：实时 vs 历史补全（★ 2026-10-04 用户要求「历史补全也提，但标记非实时」）。 */
const SOURCE_LABEL: Record<string, string> = {
  realtime: "实时",
  backfill: "历史补全",
};

function fmtTime(v: number): string {
  if (!v) return "—";
  try {
    return new Date(v * 1000).toLocaleString("zh-CN", { hour12: false });
  } catch {
    return "—";
  }
}

export default function LeadsSection(props: PageProps) {
  const { api, push, goConv } = props;
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [st, setSt] = useState<string>("all");
  const [ty, setTy] = useState<string>("all");
  const [asc, setAsc] = useState(false);

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

  const items = (leads?.items || []) as unknown as Lead[];

  const filtered = useMemo(() => {
    let list = items.slice();
    if (st !== "all") list = list.filter((r) => r.status === st);
    if (ty !== "all") list = list.filter((r) => r.contact_type === ty);
    if (q.trim()) {
      const kw = q.trim().toLowerCase();
      list = list.filter((r) =>
        (r.contact_value + r.peer_name + r.account + r.source_text)
          .toLowerCase()
          .includes(kw)
      );
    }
    list.sort((a, b) => (asc ? a.created_at - b.created_at : b.created_at - a.created_at));
    return list;
  }, [items, q, st, ty, asc]);

  const cnt = (s: string) => items.filter((r) => r.status === s).length;

  const pills: [string, string, number][] = [
    ["all", "全部", items.length],
    ["new", "新线索", cnt("new")],
    ["followed", "已跟进", cnt("followed")],
    ["invalid", "无效", cnt("invalid")],
  ];

  return (
    <div>
      <Toolbar className="mb-3">
        <input
          className="min-w-[200px] flex-1 rounded-[var(--radius-sm)] border
                     border-[var(--color-border)] bg-[var(--color-surface)] px-3 py-1.5
                     text-[0.78rem] text-[var(--color-text)] outline-none"
          placeholder="搜索联系方式 / 客户 / 账号 / 原文…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
        />
        <Select value={ty} onValueChange={setTy}>
          <SelectTrigger className="w-[120px]" aria-label="联系方式类型筛选">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value="all">全部类型</SelectItem>
            <SelectItem value="phone">手机号</SelectItem>
            <SelectItem value="wechat">微信号</SelectItem>
          </SelectContent>
        </Select>
        <Button variant="ghost" size="sm" onClick={() => setAsc((v) => !v)}>
          <ArrowUpDown className="h-3.5 w-3.5" />
          {asc ? "时间 ↑" : "时间 ↓"}
        </Button>
        <a
          href="http://127.0.0.1:8000/api/ai/leads/export"
          target="_blank"
          rel="noreferrer"
        >
          <Button variant="secondary" size="sm">
            <Download className="h-3.5 w-3.5" />导出 CSV
          </Button>
        </a>
      </Toolbar>

      <div className="mb-2.5 flex flex-wrap items-center gap-2.5 text-[0.75rem]
                      text-[var(--color-text-muted)]">
        <span>
          共 <b className="font-mono font-semibold text-[var(--color-text)]">{filtered.length}</b> 条
          <span className="ml-1.5">· 客户在对话中发出手机号/微信号后自动捕获</span>
        </span>
        <div className="flex flex-wrap gap-1.5">
          {pills.map(([id, label, c]) => {
            const on = st === id;
            return (
              <button
                key={id}
                type="button"
                onClick={() => setSt(id)}
                className={[
                  "inline-flex cursor-pointer items-center gap-1.5 rounded-full border px-2.5 py-1",
                  "text-[0.75rem] transition-colors duration-200",
                  on
                    ? "border-[var(--color-accent)] bg-[var(--color-accent-soft)] font-semibold text-[var(--color-accent)]"
                    : "border-[var(--color-border)] bg-[var(--color-surface)] text-[var(--color-text-muted)] hover:text-[var(--color-text)]",
                ].join(" ")}
              >
                {label}
                <span className="font-mono text-[0.68rem] opacity-80">{c}</span>
              </button>
            );
          })}
        </div>
      </div>

      <Card className="overflow-hidden p-0" data-od-id="msg-leads-table">
        {items.length === 0 ? (
          <Blank>暂无线索。客户在对话中发出手机号/微信号后自动捕获。</Blank>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full table-fixed border-collapse">
              <colgroup>
                {/* ★ 2026-10-04 列宽优化：联系方式定长（手机号11位/微信号）；
                    客户/账号各留弹性空间；原文给最大弹性（用户主要读的内容）；
                    状态/类型/来源三个枚举列压到最小；时间固定防换行。
                    合计 100%。 */}
                <col style={{ width: "8%" }} />      /* 状态 */
                <col style={{ width: "13%" }} />     /* 联系方式（11 位手机号） */
                <col style={{ width: "7%" }} />      /* 类型 */
                <col style={{ width: "9%" }} />      /* 来源（历史补全最长） */
                <col style={{ width: "13%" }} />     /* 客户（昵称，通常 2-8 字） */
                <col style={{ width: "10%" }} />     /* 账号 */
                <col style={{ width: "12%" }} />     /* 捕获时间（不换行） */
                <col style={{ width: "21%" }} />     /* 原文（主内容，最大弹性） */
                <col style={{ width: "7%" }} />      /* 操作（下拉触发器） */
              </colgroup>
              <thead>
                <tr>
                  <Th>状态</Th>
                  <Th>联系方式</Th>
                  <Th>类型</Th>
                  <Th>来源</Th>
                  <Th>客户</Th>
                  <Th>账号</Th>
                  <Th>捕获时间</Th>
                  <Th>原文</Th>
                  <Th className="text-right">操作</Th>
                </tr>
              </thead>
              <tbody>
                {filtered.length === 0 ? (
                  <tr>
                    <Td colSpan={9}>
                      <Blank>无匹配线索</Blank>
                    </Td>
                  </tr>
                ) : (
                  filtered.map((ld) => (
                    <tr key={ld.id} className="hover:bg-[var(--color-surface-raised)]">
                      <Td>
                        <Tone tone={ld.status === "new" ? "accent" : ld.status === "followed" ? "ok" : "mute"}>
                          {STATUS_LABEL[ld.status] || ld.status}
                        </Tone>
                      </Td>
                      <Td mono className="font-semibold">{ld.contact_value}</Td>
                      <Td>
                        <Badge variant="outline">{TYPE_LABEL[ld.contact_type] || ld.contact_type}</Badge>
                      </Td>
                      <Td>
                        <Tone tone={(ld.source || "realtime") === "backfill" ? "warn" : "info"}>
                          {SOURCE_LABEL[ld.source || "realtime"] || ld.source}
                        </Tone>
                      </Td>
                      {/* ★ 2026-10-04 用户指令：客户列从 UID 改为**抖音昵称**。
                          昵称由后端 `list_leads` 出参侧规范化（回查
                          dm_conversations.peer_name）；查不到时保留原值并在
                          hover 提示中说明 —— 不猜、不填假名。 */}
                      <Td>
                        <span
                          className="block truncate"
                          title={/\d{8,}/.test(ld.peer_name || "")
                            ? `昵称未同步：${ld.peer_name}\n（对该客户会话执行「更新会话」后可显示昵称）`
                            : (ld.peer_name || ld.conv_id)}
                        >
                          {ld.peer_name || "—"}
                        </span>
                      </Td>
                      <Td muted className="truncate">{ld.account || "—"}</Td>
                      <Td mono className="whitespace-nowrap">{fmtTime(ld.created_at)}</Td>
                      {/* ★ 2026-10-04 用户指令：原文列改为「跳转原文」入口。
                          原先截断显示（「13037765888微…」）读不全 ⇒ 改成跳转
                          到该客户对话的入口；完整原文仍可通过 hover 悬浮提示
                          与对话原文查看。 */}
                      <Td>
                        <button
                          type="button"
                          disabled={!ld.conv_id}
                          title={ld.source_text || "（该线索无原文）"}
                          className="inline-flex items-center gap-1 whitespace-nowrap rounded-[6px]
                                     px-1.5 py-0.5 text-[var(--color-accent)] transition-colors
                                     hover:bg-[var(--color-surface-raised)]
                                     disabled:opacity-40 disabled:cursor-default disabled:hover:bg-transparent"
                          onClick={() => ld.conv_id && goConv?.(ld.conv_id)}
                        >
                          <CornerDownRight className="h-3.5 w-3.5 shrink-0" />
                          跳转原文
                        </button>
                      </Td>
                      {/* ★ 2026-10-04 用户指令：操作列改为下拉框。
                          原「已跟进 / 无效」两按钮横向铺满、且按当前状态**隐藏**
                          选项 ⇒ 看不出该线索现在处于什么状态。改为下拉后：
                          · 列宽恒定（不再随状态变化伸缩）；
                          · 两项常驻，当前状态打勾，一眼可判。 */}
                      <Td>
                        <DropdownMenu>
                          <DropdownMenuTrigger asChild>
                            <Button variant="ghost" size="sm" className="w-full justify-between">
                              操作
                              <ChevronDown className="h-3.5 w-3.5" />
                            </Button>
                          </DropdownMenuTrigger>
                          <DropdownMenuContent align="end" className="w-36">
                            <DropdownMenuItem onClick={() => leadStatus(ld.id, "followed")}>
                              {ld.status === "followed" ? "✓ 已跟进" : "标记为已跟进"}
                            </DropdownMenuItem>
                            <DropdownMenuItem onClick={() => leadStatus(ld.id, "invalid")}>
                              {ld.status === "invalid" ? "✓ 已标记无效" : "标记为无效"}
                            </DropdownMenuItem>
                          </DropdownMenuContent>
                        </DropdownMenu>
                      </Td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
      </Card>
    </div>
  );
}
