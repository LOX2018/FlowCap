/**
 * AI 回复引擎（配置中心子区块）
 *
 * ## 来源与设计意图
 *
 * 本组件是原「AI 获客」页打散归类后的产物（2026-09-14 用户拍板）。
 * 原 AI 页 560 行装了 6 块内容，按**数据作用域**实测后分派：
 *
 *   ┌ 本组件（按 Agent / 全局，走 /api/ai）───────────────┐
 *   │  · Agent 设定（商家名/档位/prompt/留资话术/延迟）     │
 *   │  · 护栏配置（违禁词/兜底池/图片兜底/最大长度）        │
 *   │  · 黑名单（全局共享，kv 存储）                       │
 *   └──────────────────────────────────────────────────┘
 *   · 运行控制  → 总览页（产品级运行状态）
 *   · 留资线索  → 私信页（按账号维度，产生于私信对话）
 *   · 知识库入口 → 删除（知识库页已完整含两个子 tab）
 *
 * ## 为什么这三块合为一区
 *
 * 实测它们都通过 `/api/ai/config` + `/api/ai/blacklist` 读写，且都按
 * Agent 或全局生效 —— 是**同一个引擎的三组参数**，拆成多个 tab 只会让
 * 用户来回跳。
 *
 * ## 风控铁律
 *
 * 只读写 `/api/ai`，**不触发任何捕获/昵称查询**（昵称唯一来源是 BCC 被动 hook）。
 *
 * ## 搬迁保真声明
 *
 * 业务逻辑**逐字搬迁**自 pages/ai.tsx（Agent 草稿隔离、脏标记保存条、
 * 黑名单增删、护栏字段读写），未做任何语义改动。
 */
import { useState, useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical, Plus, Trash2, ShieldCheck, Ban, Target } from "lucide-react";
import { PageProps } from "../../api/client";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from "@/components/ui/select";
import { FormField, Row, Blank, SegmentedTabs, Toolbar } from "@/components/page/kit";
import { SetCard, SetCardHead, SetCardBody } from "@/components/page/set-card";
import { errMsg } from "./settings-shared";

/** Agent config 字段（与后端 `ai_reply._DEFAULT_CONFIG` 对齐）。 */
interface AiConfig {
  enabled: boolean;
  merchant_name: string;
  strict_level: string;
  system_prompt: string;
  lead_confirm: string;
  max_lead_ask: number;
  min_delay: number; max_delay: number;
  fallback_pool: string[];
  fallback_image: string;
  forbidden_words: string[];
  max_reply_len: number;
}

/** 回复档位选项（值与后端约定一致，逐字搬迁自 AI 页）。 */
const STRICT_OPTS = [
  { v: "kb_only", label: "🔒 仅知识库（AI 不参与）" },
  { v: "rag", label: "⚖️ RAG 限定（AI 只准依据知识库）" },
  { v: "free", label: "🕊️ 自由风格" },
];

export default function AiEngineSection(props: PageProps) {
  const { push, api } = props;
  const qc = useQueryClient();

  // v0.38.3：选谁编辑谁；不选（""）编辑全局默认。
  const [agentId, setAgentId] = useState<string>("");

  const agentsQ = useQuery({
    queryKey: ["ai-agents-list"],
    queryFn: () => api.listAgents(),
    staleTime: 30_000,
  });
  const agentList = agentsQ.data?.agents || [];

  const { data: cfg } = useQuery({
    queryKey: ["ai-config", agentId],
    queryFn: async () =>
      (await api.aiGetConfig(agentId || undefined)).config as unknown as AiConfig,
  });

  const [draft, setDraft] = useState<Partial<AiConfig>>({});
  // 切 Agent 时丢弃未保存草稿（否则会把 A 的草稿写到 B）—— 逐字搬迁
  const switchAgent = useCallback((id: string) => {
    setDraft({});
    setAgentId(id);
  }, []);

  const { data: blData } = useQuery({
    queryKey: ["ai-blacklist"],
    queryFn: () => api.aiBlacklist(),
  });

  const c = { ...(cfg || ({} as Partial<AiConfig>)), ...draft } as AiConfig;
  const dirty = Object.keys(draft).length > 0;

  const set = useCallback((k: keyof AiConfig, v: unknown) => {
    setDraft((d) => ({ ...d, [k]: v }));
  }, []);

  const save = useCallback(async () => {
    try {
      await api.aiSaveConfig(draft as Record<string, unknown>, agentId || undefined);
      setDraft({});
      await qc.invalidateQueries({ queryKey: ["ai-config", agentId] });
      await qc.invalidateQueries({ queryKey: ["ai-status"] });
      push(
        agentId
          ? `已保存到 Agent「${agentList.find((a) => a.id === agentId)?.name || agentId}」`
          : "AI 全局配置已保存",
        4000,
      );
    } catch (e) {
      push(`保存失败: ${errMsg(e)}`, 8000);
    }
  }, [draft, api, push, qc, agentId, agentList]);

  const testAi = useCallback(async () => {
    push("正在测试 AI 连接…", 10000);
    try {
      const r = await api.aiTest();
      push(r.ok ? `✅ ${r.msg}` : `❌ ${r.msg}`, 8000);
    } catch (e) {
      push(`测试失败: ${errMsg(e)}`, 8000);
    }
  }, [api, push]);

  // ---- 黑名单（逐字搬迁）----
  const [blInput, setBlInput] = useState("");
  const blAdd = useCallback(async () => {
    if (!blInput.trim()) return;
    try {
      await api.aiBlacklistAdd(blInput.trim());
      setBlInput("");
      await qc.invalidateQueries({ queryKey: ["ai-blacklist"] });
      push("已加入黑名单");
    } catch (e) {
      push(`失败: ${errMsg(e)}`, 6000);
    }
  }, [blInput, api, push, qc]);
  const blDel = useCallback(async (uid: string) => {
    try {
      await api.aiBlacklistRemove(uid);
      await qc.invalidateQueries({ queryKey: ["ai-blacklist"] });
      push("已移出黑名单");
    } catch (e) {
      push(`失败: ${errMsg(e)}`, 6000);
    }
  }, [api, push, qc]);

  return (
    <div>
      {/* ===== Agent 选择器 ===== */}
      <SetCard>
        <SetCardHead
          title="AI 回复引擎"
          description="回复内容与行为参数 · 按 Agent 差异化；黑名单为全局共享"
          right={
            <Badge variant={agentId ? "accent" : "outline"}>
              {agentId ? agentList.find((a) => a.id === agentId)?.name || agentId : "全局默认"}
            </Badge>
          }
        />
        <SetCardBody>
          <div className="mb-2.5 flex flex-wrap items-center gap-2">
            <span className="mr-0.5 text-[0.72rem] text-[var(--color-text-muted)]">编辑目标</span>
            <SegmentedTabs
              value={agentId}
              onChange={switchAgent}
              items={[
                { value: "", label: "全局默认" },
                ...agentList.map((a) => ({ value: a.id, label: a.name })),
              ]}
            />
          </div>

          <div className="mb-3 text-[0.72rem] leading-relaxed text-[var(--color-text-muted)]">
            {agentId ? (
              <>
                正在编辑 Agent「{agentList.find((a) => a.id === agentId)?.name}」的回复内容 —— 影响
                <b className="text-[var(--color-text-secondary)]">绑定了该 Agent 的账号</b>。
              </>
            ) : (
              <>
                正在编辑<b className="text-[var(--color-text-secondary)]">全局默认</b> —— 对未绑定
                Agent 的账号生效。多账号请用 Agent 区分，避免配置串号。
              </>
            )}
          </div>

          {/* ---- Agent 设定 ---- */}
          <div className="mb-1.5 text-[0.78rem] font-semibold text-[var(--color-text)]">
            Agent 设定
            <span className="ml-1.5 text-[0.68rem] font-normal text-[var(--color-text-muted)]">
              获客留资目标 + 专业性档位
            </span>
          </div>

          <FormField label="商家名（注入 Agent 人设）">
            <Input
              value={c.merchant_name || ""}
              onChange={(e) => set("merchant_name", e.target.value)}
              placeholder="例：张老师工伤咨询"
            />
          </FormField>

          <FormField
            label="回复档位"
            hint="🔒 仅知识库：AI 不参与，最安全；⚖️ RAG 限定（推荐）：AI 只准依据知识库回答，出界被护栏拦下；🕊️ 自由：仅靠人设约束"
          >
            <Select
              value={c.strict_level || "rag"}
              onValueChange={(v) => set("strict_level", v)}
            >
              <SelectTrigger><SelectValue /></SelectTrigger>
              <SelectContent>
                {STRICT_OPTS.map((o) => (
                  <SelectItem key={o.v} value={o.v}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FormField>

          <FormField label="Agent prompt（留空用内置获客模板；可用 {merchant} / {max_ask} 占位）">
            <Textarea
              className="min-h-[120px]"
              value={c.system_prompt || ""}
              onChange={(e) => set("system_prompt", e.target.value)}
              placeholder="留空 = 内置获客模板（解答→意向→留资三阶段，只索要手机号，被拒即止）"
            />
          </FormField>

          <div className="flex gap-3">
            <FormField label="留资成功确认话术" className="flex-1">
              <Input
                value={c.lead_confirm || ""}
                onChange={(e) => set("lead_confirm", e.target.value)}
              />
            </FormField>
            <FormField label="最多主动索要次数" className="w-[130px]">
              <Input
                type="number" min={0} max={5}
                value={c.max_lead_ask ?? 2}
                onChange={(e) => set("max_lead_ask", Number(e.target.value))}
              />
            </FormField>
          </div>

          <div className="flex items-end gap-3">
            <FormField label="最小延迟(秒)" className="w-[130px]">
              <Input
                type="number" value={c.min_delay ?? 8}
                onChange={(e) => set("min_delay", Number(e.target.value))}
              />
            </FormField>
            <FormField label="最大延迟(秒)" className="w-[130px]">
              <Input
                type="number" value={c.max_delay ?? 20}
                onChange={(e) => set("max_delay", Number(e.target.value))}
              />
            </FormField>
            <div className="pb-2.5">
              <Button variant="secondary" size="sm" onClick={testAi}>
                <FlaskConical className="h-3.5 w-3.5" />测试 AI 连接
              </Button>
            </div>
          </div>

          {/* ---- 护栏配置 ---- */}
          <div className="mb-1.5 mt-4 border-t border-[var(--color-border)] pt-3.5 text-[0.78rem] font-semibold text-[var(--color-text)]">
            护栏配置
            <span className="ml-1.5 text-[0.68rem] font-normal text-[var(--color-text-muted)]">
              违禁词拦截 / 兜底话术池
            </span>
          </div>

          <div className="mb-2 flex items-center gap-1.5 text-[0.72rem] text-[var(--color-text-secondary)]">
            <ShieldCheck className="h-3.5 w-3.5" />
            回复命中违禁词即丢弃改发兜底
          </div>

          <FormField label="违禁词（逗号分隔）">
            <Input
              value={(c.forbidden_words || []).join("，")}
              onChange={(e) =>
                set("forbidden_words",
                  e.target.value.split(/[,，]/).map((s) => s.trim()).filter(Boolean))
              }
            />
          </FormField>

          <FormField label="兜底话术池（未命中/被拦截时随机发一条；每行一条）">
            <Textarea
              className="min-h-[70px]"
              value={(c.fallback_pool || []).join("\n")}
              onChange={(e) =>
                set("fallback_pool",
                  e.target.value.split("\n").map((s) => s.trim()).filter(Boolean))
              }
            />
          </FormField>

          <FormField label="图片兜底话术（视觉模型未启用/失败时）">
            <Input
              value={c.fallback_image || ""}
              onChange={(e) => set("fallback_image", e.target.value)}
            />
          </FormField>

          <FormField label="回复最大长度（超长自动截到第一句）">
            <Input
              type="number" value={c.max_reply_len ?? 60}
              onChange={(e) => set("max_reply_len", Number(e.target.value))}
            />
          </FormField>

          {/* 保存条（有未保存修改时吸底）—— 逐字搬迁 */}
          {dirty && (
            <div
              className="sticky bottom-3 mt-3 flex items-center gap-3 rounded-[var(--radius-md)]
                         border border-[var(--color-accent)] bg-[var(--color-surface-solid)]
                         px-4 py-2.5 shadow-[var(--shadow-md)]"
            >
              <span className="flex items-center gap-1.5 text-[0.8rem] text-[var(--color-text)]">
                <Target className="h-3.5 w-3.5 text-[var(--color-accent)]" />
                有未保存的修改
              </span>
              <div className="flex-1" />
              <Button onClick={save}>保存全部配置</Button>
              <Button variant="ghost" onClick={() => setDraft({})}>放弃</Button>
            </div>
          )}
        </SetCardBody>
      </SetCard>

      {/* ===== 黑名单（全局共享）===== */}
      <SetCard>
        <SetCardHead
          title="黑名单"
          description="全局共享 · 命中的 uid/昵称不参与 AI 自动回复"
          right={
            <span className="flex items-center gap-2">
              <span className="text-[0.7rem] text-[var(--color-text-muted)]">
                {blData?.items?.length ?? 0} 人
              </span>
              <Ban className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
            </span>
          }
        />
        <SetCardBody>
          <Toolbar className="mb-2">
            <Input
              className="flex-1"
              value={blInput}
              onChange={(e) => setBlInput(e.target.value)}
              placeholder="uid 或昵称，回车添加"
              onKeyDown={(e) => {
                if (e.key === "Enter") blAdd();
              }}
            />
            <Button variant="secondary" size="sm" onClick={blAdd}>
              <Plus className="h-3.5 w-3.5" />添加
            </Button>
          </Toolbar>
          {!(blData?.items || []).length && <Blank>黑名单为空</Blank>}
          <div className="divide-y divide-[var(--color-border)]">
            {(blData?.items || []).map((uid) => (
              <Row key={uid} className="!px-0 py-1.5">
                <span className="min-w-0 flex-1 truncate font-mono text-[0.76rem]
                                 text-[var(--color-text)]">
                  {uid}
                </span>
                <Button variant="ghost" size="sm" onClick={() => blDel(uid)}>
                  <Trash2 className="h-3 w-3" />移除
                </Button>
              </Row>
            ))}
          </div>
        </SetCardBody>
      </SetCard>
    </div>
  );
}
