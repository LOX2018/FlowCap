/**
 * AI 获客自动回复页（重设计版 · 对标 better-douyin 设计体系）
 *
 * 结构（可折叠区块）：
 *   1. 运行控制：总开关 + 运行状态
 *   2. Agent 设定：商家名 / 回复档位 / 获客 prompt / 留资确认话术 / 索要上限
 *   3. 知识库：卡片入口 → 专用管理页（kb）
 *   4. 留资线索：列表 + 状态 + CSV 导出
 *   5. 护栏：违禁词 / 兜底话术池
 *   6. 黑名单
 *
 * ## 铁律对齐
 * 本页只读写 /api/ai，**不触发任何捕获/昵称查询**。
 *
 * ## 本次改动（重设计）
 * - 旧的内联 `style={{...}}`（约 40 处）+ `miniBtn`/`inputStyle` 常量
 *   → 统一走 `<Input>/<Textarea>/<Select>/<Button>` + `Collapse`/`FormField`（kit）
 * - `Section` 本地实现 → 复用 `kit.Collapse`（与通知页同一实现）
 * - Pill → `Tone`，`.btn sm accent|ghost` → `<Button variant>` + `SegmentedTabs`
 * - **业务逻辑零改动**（Agent 草稿隔离、脏标记保存条、黑名单/线索操作全保持）
 */
import { useState, useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Play, Square, FlaskConical, Download, Plus, Trash2, BookOpen, MessageSquare,
  ShieldCheck, Ban, Target,
} from "lucide-react";
import { PageProps } from "../api/client";
import { PageContainer, PageHeader } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from "@/components/ui/select";
import {
  Collapse, FormField, Tone, Row, Blank, SegmentedTabs, Toolbar,
} from "@/components/page/kit";

interface AiConfig {
  enabled: boolean;
  api_key: string; base_url: string; model: string;
  max_tokens: number; temperature: number;
  vision_enabled: boolean;
  vision_base_url: string; vision_api_key: string; vision_model: string;
  vision_prompt: string;
  sem_enabled: boolean;
  sem_base_url: string; sem_api_key: string; sem_model: string;
  sem_threshold: number;
  api_protocol: string;
  merchant_name: string;
  strict_level: string;
  system_prompt: string;
  lead_confirm: string;
  max_lead_ask: number;
  knowledge_first: boolean;
  min_delay: number; max_delay: number; max_history: number;
  fallback_pool: string[];
  fallback_image: string;
  forbidden_words: string[];
  max_reply_len: number;
}

interface Lead {
  id: number; account: string; conv_id: string; peer_name: string;
  contact_type: string; contact_value: string; status: string;
  created_at: number; source_text: string;
}
interface AiStatus {
  ok: boolean; running: boolean; enabled: boolean; processed: number;
  replied: number; leads: number; leads_total: number; errors: number;
  last_reply: string;
}

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** 回复档位选项（值与后端约定一致）。 */
const STRICT_OPTS = [
  { v: "kb_only", label: "🔒 仅知识库（AI 不参与）" },
  { v: "rag", label: "⚖️ RAG 限定（AI 只准依据知识库）" },
  { v: "free", label: "🕊️ 自由风格" },
];

export default function AiPage(props: PageProps) {
  const { push, api } = props;
  const qc = useQueryClient();

  // v0.38.3：AI 页 = Agent 编辑器。选谁编辑谁；不选（""）编辑全局默认。
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
  // 切 Agent 时丢弃未保存草稿（否则会把 A 的草稿写到 B）
  const switchAgent = useCallback((id: string) => {
    setDraft({});
    setAgentId(id);
  }, []);

  const { data: status } = useQuery({
    queryKey: ["ai-status"],
    queryFn: () => api.aiStatus() as unknown as Promise<AiStatus>,
    refetchInterval: 5000,
  });
  const { data: kb } = useQuery({
    queryKey: ["ai-kb"],
    queryFn: () => api.aiKbList(),
  });
  const { data: leads } = useQuery({
    queryKey: ["ai-leads"],
    queryFn: () => api.aiLeads(),
  });
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

  const toggleRun = useCallback(async () => {
    try {
      if (c.enabled) await api.aiStop();
      else await api.aiStart();
      await qc.invalidateQueries({ queryKey: ["ai-status"] });
      await qc.invalidateQueries({ queryKey: ["ai-config"] });
      push(c.enabled ? "AI 自动回复已停止" : "AI 自动回复已启动（全自动监听）", 5000);
    } catch (e) {
      push(`操作失败: ${errMsg(e)}`, 8000);
    }
  }, [c.enabled, api, push, qc]);

  const testAi = useCallback(async () => {
    push("正在测试 AI 连接…", 10000);
    try {
      const r = await api.aiTest();
      push(r.ok ? `✅ ${r.msg}` : `❌ ${r.msg}`, 8000);
    } catch (e) {
      push(`测试失败: ${errMsg(e)}`, 8000);
    }
  }, [api, push]);

  // ---- 黑名单 ----
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

  // ---- 线索 ----
  const leadStatus = useCallback(async (id: number, status: string) => {
    try {
      await api.aiLeadStatus(id, status);
      await qc.invalidateQueries({ queryKey: ["ai-leads"] });
    } catch (e) {
      push(`失败: ${errMsg(e)}`, 6000);
    }
  }, [api, push, qc]);

  const running = !!status?.running;
  const st: Partial<AiStatus> = status || {};

  return (
    <PageContainer>
      <PageHeader
        title="AI 获客"
        description="编辑 Agent 的回复内容与行为参数 · 只读写 /api/ai，不触发任何捕获或昵称查询"
        actions={
          <Tone tone={running ? "ok" : c.enabled ? "warn" : "mute"}>
            {running ? "运行中" : c.enabled ? "启动中" : "已停止"}
          </Tone>
        }
      />

      {/* ===== Agent 选择器 ===== */}
      <Card className="mb-3">
        <CardContent className="flex flex-wrap items-center gap-2 p-2.5">
          <span className="mr-0.5 text-[0.72rem] text-[var(--color-text-muted)]">编辑目标</span>
          <SegmentedTabs
            value={agentId}
            onChange={switchAgent}
            items={[
              { value: "", label: "全局默认" },
              ...agentList.map((a) => ({ value: a.id, label: a.name })),
            ]}
          />
          <div className="flex-1" />
          <Badge variant={agentId ? "accent" : "outline"}>
            当前：{agentId ? agentList.find((a) => a.id === agentId)?.name || agentId : "全局默认"}
          </Badge>
        </CardContent>
      </Card>

      <div className="mb-3 text-[0.72rem] leading-relaxed text-[var(--color-text-muted)]">
        {agentId ? (
          <>
            正在编辑 Agent「{agentList.find((a) => a.id === agentId)?.name}」的回复内容 —— 影响
            <b className="text-[var(--color-text-secondary)]">在设置页绑定了该 Agent 的账号</b>。
            Agent 的新建、删除与账号绑定请到「设置 → AI 与 Agent」。
          </>
        ) : (
          <>
            正在编辑<b className="text-[var(--color-text-secondary)]">全局默认</b> —— 对未绑定
            Agent 的账号生效。多账号请用 Agent 区分，避免配置串号。
          </>
        )}
      </div>

      {/* ===== 运行控制 ===== */}
      <Collapse
        title="AI 获客自动回复"
        defaultOpen
        right={
          <Tone tone={running ? "ok" : c.enabled ? "warn" : "mute"}>
            {running ? "运行中" : c.enabled ? "启动中" : "已停止"}
          </Tone>
        }
      >
        <Toolbar className="mb-2.5">
          <Button
            variant={running ? "danger" : "default"}
            onClick={toggleRun}
          >
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
      </Collapse>

      {/* ===== Agent 设定 ===== */}
      <Collapse title="Agent 设定" subtitle="获客留资目标 + 专业性档位">
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

        <div className="text-[0.7rem] leading-relaxed text-[var(--color-text-muted)]">
          模型 / 提供商连接已统一到「设置 → AI 与 Agent → 模型链路中心」，本页只保留回复内容与行为参数。
        </div>
      </Collapse>

      {/* ===== 知识库入口 ===== */}
      <Collapse title="知识库" subtitle="点击进入管理">
        <div className="flex flex-wrap gap-3">
          {([
            ["pro", "🧠 专业知识库",
             "思维导图结构（主题→子分类→正文→总结）：向量模型的前置参考，AI 分析问题时检索条目全文，不直接回复。",
             `${kb?.items?.length ?? 0} 条`, <BookOpen key="1" className="h-4 w-4" />],
            ["reply", "💬 对话回复库",
             "命中库：对方消息符合库内案例 → 零 token 直接自动回复。支持从聊天记录自动学习话术。",
             "点击管理", <MessageSquare key="2" className="h-4 w-4" />],
          ] as const).map(([id, title, desc, badge, icon]) => (
            <button
              key={id}
              type="button"
              onClick={() => props.setTab?.("kb")}
              className="flex min-w-[240px] flex-1 cursor-pointer flex-col gap-1.5 rounded-[var(--radius-md)]
                         border border-[var(--color-border)] bg-[var(--color-surface-solid)]
                         p-3.5 text-left transition-colors duration-[var(--duration-fast)]
                         hover:border-[var(--color-border-strong)] hover:bg-[var(--color-surface-raised)]"
            >
              <span className="flex w-full items-center justify-between gap-2">
                <span className="flex items-center gap-1.5 text-[0.86rem] font-bold
                                 text-[var(--color-text)]">
                  {icon}{title}
                </span>
                <span className="text-[0.7rem] font-normal text-[var(--color-text-muted)]">
                  {badge}
                </span>
              </span>
              <span className="text-[0.72rem] leading-relaxed text-[var(--color-text-muted)]">
                {desc}
              </span>
              <span className="mt-0.5 text-[0.72rem] text-[var(--color-accent)]">进入管理 →</span>
            </button>
          ))}
        </div>
      </Collapse>

      {/* ===== 留资线索 ===== */}
      <Collapse title="留资线索" subtitle={`${leads?.items?.length ?? 0} 条`}>
        <div className="mb-2.5">
          <a
            href="http://127.0.0.1:8000/api/ai/leads/export"
            target="_blank"
            rel="noreferrer"
          >
            <Button variant="secondary" size="sm">
              <Download className="h-3.5 w-3.5" />导出 CSV
            </Button>
          </a>
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
      </Collapse>

      {/* ===== 护栏 ===== */}
      <Collapse
        title="护栏配置"
        subtitle="违禁词拦截 / 兜底话术池"
      >
        <div className="mb-2 flex items-center gap-1.5 text-[0.72rem]
                        text-[var(--color-text-secondary)]">
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
      </Collapse>

      {/* ===== 黑名单 ===== */}
      <Collapse
        title="黑名单"
        subtitle={`${blData?.items?.length ?? 0} 人`}
        right={<Ban className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />}
      >
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
      </Collapse>

      {/* ===== 保存条（有未保存修改时吸底） ===== */}
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

    </PageContainer>
  );
}
