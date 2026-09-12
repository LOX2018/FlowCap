/**
 * AI 获客自动回复页（嵌入自 xyc667/douyin-auto-reply-assistant，2026-09-06）
 *
 * 结构（可收缩区块，同 settings.tsx 的 Collapsible 范式）：
 *   1. 运行控制：总开关 + 运行状态（已处理/已回复/线索/最近回复）
 *   2. Agent 设定：商家名 / 回复档位 / 获客 prompt / 留资确认话术 / 索要上限
 *   3. 模型配置：主 LLM（Anthropic 兼容）+ 视觉模型（独立，OpenAI 兼容）
 *   4. 知识库：问答对增删改查（专业性来源 / RAG 资料）
 *   5. 留资线索：列表 + 状态 + CSV 导出
 *   6. 护栏：违禁词 / 兜底话术池
 *   7. 黑名单
 *
 * 铁律对齐：本页只读写 /api/ai，不触发任何捕获/昵称查询。
 */
import { useState, useCallback } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { PageProps } from "../api/client";
import { Pill } from "../components/ui";

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

/** 可收缩区块（与 settings.tsx 同范式） */
function Section(props: {
  title: string; subtitle?: string; defaultOpen?: boolean;
  right?: React.ReactNode; children: React.ReactNode;
}) {
  const [open, setState] = useState(!!props.defaultOpen);
  return (
    <div style={{ background: "var(--panel)", borderRadius: 12, marginBottom: 10, overflow: "hidden" }}>
      <button
        onClick={() => setState(!open)}
        style={{
          width: "100%", display: "flex", alignItems: "center", gap: 10,
          padding: "12px 16px", background: "none", border: "none",
          cursor: "pointer", color: "var(--foreground)", textAlign: "left",
        }}
      >
        <span style={{ fontSize: 13.5, fontWeight: 600 }}>{props.title}</span>
        {props.subtitle && (
          <span style={{ fontSize: 12, color: "var(--muted-foreground)" }}>{props.subtitle}</span>
        )}
        <span style={{ marginLeft: "auto", display: "flex", gap: 8, alignItems: "center" }}>
          {props.right}
          <span style={{ fontSize: 11, color: "var(--muted-foreground)" }}>{open ? "▲" : "▼"}</span>
        </span>
      </button>
      {open && <div style={{ padding: "0 16px 14px" }}>{props.children}</div>}
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  width: "100%", boxSizing: "border-box", padding: "7px 10px",
  borderRadius: 8, border: "1px solid var(--border)",
  background: "var(--surface-2)", color: "var(--foreground)", fontSize: 13,
};

function Field(props: { label: string; hint?: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ fontSize: 12, color: "var(--muted-foreground)", marginBottom: 4 }}>
        {props.label}
      </div>
      {props.children}
      {props.hint && (
        <div style={{ fontSize: 11, color: "var(--muted-foreground)", marginTop: 3, opacity: 0.8 }}>
          {props.hint}
        </div>
      )}
    </div>
  );
}

const miniBtn: React.CSSProperties = {
  padding: "5px 12px", borderRadius: 7, cursor: "pointer",
  border: "1px solid var(--border)", background: "var(--surface-2)",
  color: "var(--foreground)", fontSize: 12.5,
};


export default function AiPage(props: PageProps) {
  const { push, api } = props;
  const qc = useQueryClient();

  // v0.38.3：AI 页变成 Agent 编辑器 —— 顶部选哪个 Agent 就编辑哪个。
  // 不选择（" "）时编辑全局默认；Agent 的新建/删除/绑定在设置页。
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
      (await api.aiGetConfig(agentId || undefined))
        .config as unknown as AiConfig,
  });
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

  const [draft, setDraft] = useState<Partial<AiConfig>>({});
  const c = { ...(cfg || ({} as Partial<AiConfig>)), ...draft } as AiConfig;
  const dirty = Object.keys(draft).length > 0;

  const set = useCallback((k: keyof AiConfig, v: unknown) => {
    setDraft((d) => ({ ...d, [k]: v }));
  }, []);

  const save = useCallback(async () => {
    try {
      await api.aiSaveConfig(
        draft as Record<string, unknown>,
        agentId || undefined,
      );
      setDraft({});
      await qc.invalidateQueries({ queryKey: ["ai-config", agentId] });
      await qc.invalidateQueries({ queryKey: ["ai-status"] });
      push(
        agentId
          ? `已保存到 Agent「${
              agentList.find((a) => a.id === agentId)?.name || agentId
            }」`
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
    } catch (e) { push(`测试失败: ${errMsg(e)}`, 8000); }
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
    } catch (e) { push(`失败: ${errMsg(e)}`, 6000); }
  }, [blInput, api, push, qc]);
  const blDel = useCallback(async (uid: string) => {
    try {
      await api.aiBlacklistRemove(uid);
      await qc.invalidateQueries({ queryKey: ["ai-blacklist"] });
      push("已移出黑名单");
    } catch (e) { push(`失败: ${errMsg(e)}`, 6000); }
  }, [api, push, qc]);

  // ---- 线索 ----
  const leadStatus = useCallback(async (id: number, status: string) => {
    try {
      await api.aiLeadStatus(id, status);
      await qc.invalidateQueries({ queryKey: ["ai-leads"] });
    } catch (e) { push(`失败: ${errMsg(e)}`, 6000); }
  }, [api, push, qc]);

  const running = !!status?.running;
  const st: Partial<AiStatus> = status || {};

  return (
    <div>
      {/* ===== v0.38.3：Agent 选择器 ===== */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          flexWrap: "wrap",
          padding: "8px 10px",
          marginBottom: 12,
          background: "var(--panel)",
          border: "1px solid var(--line)",
          borderRadius: 10,
        }}
      >
        <span style={{ fontSize: 11.5, color: "var(--muted)", marginRight: 2 }}>
          编辑目标
        </span>
        <button
          className={"btn sm" + (agentId === "" ? " accent" : " ghost")}
          onClick={() => switchAgent("")}
        >
          全局默认
        </button>
        {agentList.map((a) => (
          <button
            key={a.id}
            className={"btn sm" + (agentId === a.id ? " accent" : " ghost")}
            onClick={() => switchAgent(a.id)}
            title={`编辑 Agent「${a.name}」`}
          >
            {a.name}
          </button>
        ))}
        <div style={{ flex: 1 }} />
        <span
          style={{
            fontSize: 11.5,
            padding: "2px 8px",
            borderRadius: 999,
            background: agentId ? "var(--accent-bg)" : "var(--surface-2)",
            color: agentId ? "var(--accent)" : "var(--muted)",
            border: "1px solid var(--border)",
          }}
        >
          当前：
          {agentId
            ? agentList.find((a) => a.id === agentId)?.name || agentId
            : "全局默认"}
        </span>
      </div>
      <div
        style={{
          fontSize: 11.5,
          color: "var(--muted)",
          marginBottom: 12,
          lineHeight: 1.6,
        }}
      >
        {agentId ? (
          <>
            正在编辑 Agent「
            {agentList.find((a) => a.id === agentId)?.name}」的回复内容 ——
            影响<b>在设置页绑定了该 Agent 的账号</b>。 Agent
            的新建、删除与账号绑定请到「设置 → AI 与 Agent」。
          </>
        ) : (
          <>
            正在编辑<b>全局默认</b> ——
            对未绑定 Agent 的账号生效。多账号请用 Agent 区分，避免配置串号。
          </>
        )}
      </div>

      {/* ===== 标题 + 运行控制 ===== */}
      <Section
        title="🤖 AI 获客自动回复"
        defaultOpen
        right={
          <Pill c={running ? "ok" : c.enabled ? "warn" : "mute"}>
            {running ? "运行中" : c.enabled ? "启动中" : "已停止"}
          </Pill>
        }
      >
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", marginBottom: 10 }}>
          <button
            onClick={toggleRun}
            style={{
              padding: "8px 18px", borderRadius: 8, border: "none", cursor: "pointer",
              fontWeight: 600, fontSize: 13,
              background: running ? "var(--danger)" : "var(--accent)", color: "#fff",
            }}
          >
            {running ? "⏹ 停止监听" : "▶ 启动监听"}
          </button>
          <span style={{ fontSize: 12.5, color: "var(--muted-foreground)" }}>
            已处理 <b>{st.processed ?? 0}</b> · 已回复 <b>{st.replied ?? 0}</b> · 线索{" "}
            <b>{st.leads_total ?? 0}</b> · 错误 <b>{st.errors ?? 0}</b>
          </span>
        </div>
        {st.last_reply && (
          <div style={{ fontSize: 12, color: "var(--muted-foreground)" }}>
            最近回复：{st.last_reply}
          </div>
        )}
      </Section>

      {/* ===== Agent 设定 ===== */}
      <Section title="🎯 Agent 设定" subtitle="获客留资目标 + 专业性档位">
        <Field label="商家名（注入 Agent 人设）">
          <input style={inputStyle} value={c.merchant_name || ""}
                 onChange={(e) => set("merchant_name", e.target.value)}
                 placeholder="例：张老师工伤咨询" />
        </Field>
        <Field label="回复档位" hint="🔒 仅知识库：AI 不参与，最安全；⚖️ RAG限定（推荐）：AI 只准依据知识库回答，出界被护栏拦下；🕊️ 自由：仅靠人设约束">
          <select style={inputStyle} value={c.strict_level || "rag"}
                  onChange={(e) => set("strict_level", e.target.value)}>
            <option value="kb_only">🔒 仅知识库（AI 不参与）</option>
            <option value="rag">⚖️ RAG限定（AI 只准依据知识库）</option>
            <option value="free">🕊️ 自由风格</option>
          </select>
        </Field>
        <Field label="Agent prompt（留空用内置获客模板；可用 {merchant} / {max_ask} 占位）">
          <textarea style={{ ...inputStyle, minHeight: 120, fontFamily: "inherit", resize: "vertical" }}
                    value={c.system_prompt || ""}
                    onChange={(e) => set("system_prompt", e.target.value)}
                    placeholder="留空 = 内置获客模板（解答→意向→留资三阶段，只索要手机号，被拒即止）" />
        </Field>
        <div style={{ display: "flex", gap: 12 }}>
          <div style={{ flex: 1 }}>
            <Field label="留资成功确认话术">
              <input style={inputStyle} value={c.lead_confirm || ""}
                     onChange={(e) => set("lead_confirm", e.target.value)} />
            </Field>
          </div>
          <div style={{ width: 130 }}>
            <Field label="最多主动索要次数">
              <input type="number" style={inputStyle} min={0} max={5}
                     value={c.max_lead_ask ?? 2}
                     onChange={(e) => set("max_lead_ask", Number(e.target.value))} />
            </Field>
          </div>
        </div>
        <div style={{ display: "flex", gap: 12 }}>
          <div style={{ width: 130 }}>
            <Field label="最小延迟(秒)">
              <input type="number" style={inputStyle} value={c.min_delay ?? 8}
                     onChange={(e) => set("min_delay", Number(e.target.value))} />
            </Field>
          </div>
          <div style={{ width: 130 }}>
            <Field label="最大延迟(秒)">
              <input type="number" style={inputStyle} value={c.max_delay ?? 20}
                     onChange={(e) => set("max_delay", Number(e.target.value))} />
            </Field>
          </div>
          <div style={{ display: "flex", alignItems: "flex-end", paddingBottom: 10 }}>
            <button onClick={testAi} style={miniBtn}>测试 AI 连接</button>
          </div>
        </div>
        <div style={{ fontSize: 11.5, color: "var(--muted-foreground)", lineHeight: 1.6 }}>
          模型 / 提供商连接已统一到「设置 → AI 与 Agent →
          模型链路中心」，本页只保留回复内容与行为参数。
        </div>
      </Section>

      {/* v0.38.4：模型配置已迁至「设置 → AI 与 Agent → 模型链路中心」(model_hub)，本页不再重复配置。 */}

      {/* v0.39.0：知识库改为卡片入口 → 专用管理页（kb） */}
      <Section title="📚 知识库" subtitle="点击进入管理">
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          {([
            ["pro", "🧠 专业知识库",
             "思维导图结构（主题→子分类→正文→总结）：向量模型的前置参考，AI 分析问题时检索条目全文，不直接回复。",
             `${kb?.items?.length ?? 0} 条`],
            ["reply", "💬 对话回复库",
             "命中库：对方消息符合库内案例 → 零 token 直接自动回复。支持从聊天记录自动学习话术。",
             "点击管理"],
          ] as const).map(([id, title, desc, badge]) => (
            <button key={id}
                    onClick={() => props.setTab?.("kb")}
                    style={{
                      flex: "1 1 240px", textAlign: "left", cursor: "pointer",
                      background: "var(--card)", border: "1px solid var(--border)",
                      borderRadius: 12, padding: "14px 16px",
                      display: "flex", flexDirection: "column", gap: 6,
                    }}>
              <span style={{ fontSize: 14, fontWeight: 700, display: "flex", justifyContent: "space-between", width: "100%" }}>
                {title}
                <span style={{ fontSize: 11.5, fontWeight: 400, color: "var(--muted-foreground)" }}>{badge}</span>
              </span>
              <span style={{ fontSize: 12, color: "var(--muted-foreground)", lineHeight: 1.6 }}>{desc}</span>
              <span style={{ fontSize: 12, color: "var(--accent)", marginTop: 2 }}>进入管理 →</span>
            </button>
          ))}
        </div>
      </Section>

      {/* ===== 留资线索 ===== */}
      <Section title="🎯 留资线索" subtitle={`${leads?.items?.length ?? 0} 条`}>
        <div style={{ marginBottom: 10 }}>
          <a href="http://127.0.0.1:8000/api/ai/leads/export" target="_blank" rel="noreferrer"
             style={{ textDecoration: "none" }}>
            <button style={miniBtn}>⬇ 导出 CSV</button>
          </a>
        </div>
        {(leads?.items || []).length === 0 && (
          <div style={{ fontSize: 12.5, color: "var(--muted-foreground)" }}>
            暂无线索。客户在对话中发出手机号/微信号后自动捕获。
          </div>
        )}
        {(leads?.items || []).map((raw) => {
          const ld = raw as unknown as Lead;
          return (
            <div key={ld.id}
                 style={{ display: "flex", gap: 10, alignItems: "center", padding: "7px 0",
                          borderBottom: "1px solid var(--border)", fontSize: 12.5 }}>
              <Pill c={ld.status === "new" ? "accent" : ld.status === "followed" ? "ok" : "mute"}>
                {ld.status === "new" ? "新线索" : ld.status === "followed" ? "已跟进" : "无效"}
              </Pill>
              <span style={{ fontWeight: 600 }}>{ld.contact_value}</span>
              <span style={{ color: "var(--muted-foreground)", overflow: "hidden",
                             textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {ld.contact_type === "phone" ? "手机号" : "微信号"} · {ld.peer_name || ld.conv_id.slice(0, 12)} · {ld.account}
              </span>
              <span style={{ marginLeft: "auto", display: "flex", gap: 6, flexShrink: 0 }}>
                {ld.status !== "followed" && (
                  <button onClick={() => leadStatus(ld.id, "followed")} style={miniBtn}>已跟进</button>
                )}
                {ld.status !== "invalid" && (
                  <button onClick={() => leadStatus(ld.id, "invalid")}
                          style={{ ...miniBtn, color: "var(--muted-foreground)" }}>无效</button>
                )}
              </span>
            </div>
          );
        })}
      </Section>

      {/* ===== 护栏 ===== */}
      <Section title="🛡️ 护栏配置" subtitle="违禁词拦截 / 兜底话术池">
        <Field label="违禁词（AI 回复命中即丢弃改发兜底；逗号分隔）">
          <input style={inputStyle}
                 value={(c.forbidden_words || []).join("，")}
                 onChange={(e) => set("forbidden_words",
                   e.target.value.split(/[,，]/).map((s) => s.trim()).filter(Boolean))} />
        </Field>
        <Field label="兜底话术池（未命中/被拦截时随机发一条；每行一条）">
          <textarea style={{ ...inputStyle, minHeight: 70 }}
                    value={(c.fallback_pool || []).join("\n")}
                    onChange={(e) => set("fallback_pool",
                      e.target.value.split("\n").map((s) => s.trim()).filter(Boolean))} />
        </Field>
        <Field label="图片兜底话术（视觉模型未启用/失败时）">
          <input style={inputStyle} value={c.fallback_image || ""}
                 onChange={(e) => set("fallback_image", e.target.value)} />
        </Field>
        <Field label="回复最大长度（超长自动截到第一句）">
          <input type="number" style={inputStyle} value={c.max_reply_len ?? 60}
                 onChange={(e) => set("max_reply_len", Number(e.target.value))} />
        </Field>
      </Section>

      {/* ===== 黑名单 ===== */}
      <Section title="🚫 黑名单" subtitle={`${blData?.items?.length ?? 0} 人`}>
        <div style={{ display: "flex", gap: 8, marginBottom: 8 }}>
          <input style={{ ...inputStyle, flex: 1 }} value={blInput}
                 onChange={(e) => setBlInput(e.target.value)}
                 placeholder="uid 或昵称，回车添加"
                 onKeyDown={(e) => { if (e.key === "Enter") blAdd(); }} />
          <button onClick={blAdd} style={miniBtn}>添加</button>
        </div>
        {(blData?.items || []).map((uid) => (
          <div key={uid} style={{ display: "flex", alignItems: "center", gap: 8, padding: "5px 0", fontSize: 12.5 }}>
            <span style={{ flex: 1 }}>{uid}</span>
            <button onClick={() => blDel(uid)}
                    style={{ ...miniBtn, color: "var(--danger)" }}>移除</button>
          </div>
        ))}
      </Section>

      {/* ===== 保存条 ===== */}
      {dirty && (
        <div style={{
          position: "sticky", bottom: 12, display: "flex", gap: 10, alignItems: "center",
          background: "var(--panel)", border: "1px solid var(--accent)", borderRadius: 12,
          padding: "10px 16px", boxShadow: "0 4px 16px rgba(0,0,0,0.25)",
        }}>
          <span style={{ fontSize: 13 }}>有未保存的修改</span>
          <button onClick={save}
                  style={{ padding: "7px 18px", borderRadius: 8, border: "none", cursor: "pointer",
                           background: "var(--accent)", color: "#fff", fontWeight: 600, marginLeft: "auto" }}>
            保存全部配置
          </button>
          <button onClick={() => setDraft({})} style={miniBtn}>放弃</button>
        </div>
      )}
    </div>
  );
}
