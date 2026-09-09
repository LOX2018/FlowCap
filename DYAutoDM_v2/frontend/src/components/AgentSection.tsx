/**
 * AI Agent 模版管理 + 账号绑定（v0.38.0）
 *
 * 设计（用户 2026-09-09 拍板）：
 *   - Agent 是**模版**：含模型/档位/商家名/prompt/知识库/黑名单/兜底话术
 *   - 账号绑定 Agent；改 Agent 一次 → 所有绑定它的账号同步生效
 *   - 绑定关系**在设置页维护**，不在 AI 页（避免两处配置分裂）
 *
 * UI 结构：
 *   ① Agent 列表卡片：新建 / 重命名 / 删除 / 选中编辑
 *   ② Agent 参数编辑：核心字段（商家名、档位、模型、总开关、延迟）
 *      —— 只放「最常改」的，全量参数仍在 AI 页（避免重复建设）
 *   ③ 账号绑定矩阵：每个账号一个下拉，选它用哪个 Agent
 */
import { useState, useEffect, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { PageProps } from "../api/client";
import type { AiAgentSummary } from "../api/client";

const LEVELS = [
  { value: "kb_only", label: "kb_only（仅知识库，AI 不参与）" },
  { value: "rag", label: "rag（AI 只准依据知识库答，默认）" },
  { value: "free", label: "free（纯 prompt 约束）" },
];

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export default function AgentSection(props: PageProps) {
  const { api, ready, push } = props;
  const qc = useQueryClient();

  const [selId, setSelId] = useState<string>("");
  const [draft, setDraft] = useState<Record<string, string>>({});

  const q = useQuery({
    queryKey: ["ai-agents"],
    queryFn: () => api.listAgents(),
    enabled: !!ready,
  });

  const accountsQ = useQuery({
    queryKey: ["accounts"],
    queryFn: () => api.getAccounts(),
    enabled: !!ready,
  });

  const agents = q.data?.agents || [];
  const bindings = q.data?.bindings || {};

  // 账号列表（兼容不同返回结构）
  const acctList: string[] = (() => {
    const raw: unknown = accountsQ.data;
    const arr = Array.isArray(raw) ? raw : ((raw as { accounts?: unknown[] })?.accounts ?? []);
    return (arr as { name?: string }[])
      .map((a) => a?.name)
      .filter((n): n is string => !!n);
  })();

  const sel = agents.find((a: AiAgentSummary) => a.id === selId) || null;

  // 选中 Agent 变化时载入其参数到编辑态
  useEffect(() => {
    if (!sel) {
      setDraft({});
      return;
    }
    setDraft({
      name: sel.name || "",
      merchant_name: sel.merchant_name || "",
      strict_level: sel.strict_level || "rag",
      model: sel.model || "",
      enabled: String(!!sel.enabled),
    });
  }, [sel?.id]);

  const saveMut = useMutation({
    mutationFn: (body: {
      id?: string;
      name: string;
      config: Record<string, unknown>;
    }) => api.saveAgent(body),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["ai-agents"] });
      setSelId(data.agent.id);
      push(`已保存 Agent「${data.agent.name}」`);
    },
    onError: (e) => push(`保存失败：${errMsg(e)}`),
  });

  const delMut = useMutation({
    mutationFn: (id: string) => api.deleteAgent(id),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["ai-agents"] });
      setSelId("");
      const n = data.unbound_accounts?.length || 0;
      push(n > 0 ? `已删除，并解绑 ${n} 个账号` : "已删除");
    },
    onError: (e) => push(`删除失败：${errMsg(e)}`),
  });

  const bindMut = useMutation({
    mutationFn: (v: { account: string; agentId: string }) =>
      api.bindAgent(v.account, v.agentId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ai-agents"] });
    },
    onError: (e) => push(`绑定失败：${errMsg(e)}`),
  });

  const doSave = useCallback(() => {
    const cfg: Record<string, unknown> = {
      merchant_name: draft.merchant_name || "",
      strict_level: draft.strict_level || "rag",
      model: draft.model || "",
      enabled: draft.enabled === "true",
    };
    saveMut.mutate({
      id: selId || undefined,
      name: draft.name || "未命名 Agent",
      config: cfg,
    });
  }, [draft, selId, saveMut]);

  if (q.isLoading) {
    return <div style={{ padding: 20, color: "var(--muted)" }}>加载 Agent…</div>;
  }
  if (q.isError) {
    return (
      <div style={{ padding: 20, color: "var(--danger, #c0392b)" }}>
        Agent 加载失败：{errMsg(q.error)}
      </div>
    );
  }

  return (
    <div>
      <div
        style={{
          fontSize: 12,
          color: "var(--muted)",
          marginBottom: 10,
          lineHeight: 1.6,
        }}
      >
        Agent 是<b>模版</b>：建一个 Agent，让多个账号绑定它 —— 改一次，所有绑定
        账号同步生效。知识库 / 黑名单 / 兜底话术都跟随 Agent，账号不单独持有。
      </div>

      {/* ① Agent 列表 */}
      <Section title="Agent 列表" subtitle={`${agents.length} 个`}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 10 }}>
          {agents.length === 0 && (
            <span style={{ fontSize: 12, color: "var(--muted)" }}>
              暂无 Agent，点「新建」创建
            </span>
          )}
          {agents.map((a: AiAgentSummary) => (
            <button
              key={a.id}
              className={"btn sm" + (selId === a.id ? " accent" : " ghost")}
              onClick={() => setSelId(a.id)}
            >
              {a.name}
              {a.merchant_name ? ` · ${a.merchant_name}` : ""}
            </button>
          ))}
        </div>
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button
            className="btn sm"
            onClick={() => {
              setSelId("");
              setDraft({
                name: "新 Agent",
                merchant_name: "",
                strict_level: "rag",
                model: "",
                enabled: "false",
              });
            }}
          >
            新建
          </button>
          {selId && (
            <button
              className="btn sm"
              onClick={() => {
                if (confirm(`确认删除 Agent「${sel?.name}」？绑定它的账号会自动解绑。`)) {
                  delMut.mutate(selId);
                }
              }}
            >
              删除
            </button>
          )}
        </div>
      </Section>

      {/* ② Agent 参数 */}
      {(selId || draft.name) && (
        <Section title="Agent 参数" subtitle={sel ? sel.name : "新建"}>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
            <Field label="Agent 名称">
              <input
                value={draft.name || ""}
                onChange={(e) => setDraft({ ...draft, name: e.target.value })}
                style={inputStyle}
              />
            </Field>
            <Field label="商家名（注入 prompt，决定 AI 自称与话术）">
              <input
                value={draft.merchant_name || ""}
                onChange={(e) =>
                  setDraft({ ...draft, merchant_name: e.target.value })
                }
                style={inputStyle}
              />
            </Field>
            <Field label="回复档位">
              <select
                value={draft.strict_level || "rag"}
                onChange={(e) =>
                  setDraft({ ...draft, strict_level: e.target.value })
                }
                style={inputStyle}
              >
                {LEVELS.map((l) => (
                  <option key={l.value} value={l.value}>
                    {l.label}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="主模型">
              <input
                value={draft.model || ""}
                onChange={(e) => setDraft({ ...draft, model: e.target.value })}
                style={inputStyle}
              />
            </Field>
            <Field label="启用自动回复">
              <select
                value={draft.enabled || "false"}
                onChange={(e) => setDraft({ ...draft, enabled: e.target.value })}
                style={inputStyle}
              >
                <option value="false">关闭</option>
                <option value="true">开启</option>
              </select>
            </Field>
          </div>
          <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 12 }}>
            <button
              className="btn accent sm"
              onClick={doSave}
              disabled={saveMut.isPending}
            >
              {saveMut.isPending ? "保存中…" : "保存 Agent"}
            </button>
          </div>
          <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 8 }}>
            更完整的参数（语义检索、视觉模型、知识库条目、护栏、延迟）仍在
            <b> AI 页</b> 调整；此处只放按账号差异化的核心项。
          </div>
        </Section>
      )}

      {/* ③ 账号绑定 */}
      <Section title="账号绑定" subtitle={`${acctList.length} 个账号`}>
        {acctList.length === 0 && (
          <div style={{ fontSize: 12, color: "var(--muted)" }}>暂无账号</div>
        )}
        {acctList.map((name) => {
          const bound = bindings[name] || "";
          const boundName = agents.find((a: AiAgentSummary) => a.id === bound)?.name;
          return (
            <div
              key={name}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                padding: "8px 10px",
                background: "var(--surface)",
                borderRadius: 8,
                border: "1px solid var(--border)",
                marginBottom: 8,
              }}
            >
              <span style={{ flex: "0 0 160px", fontSize: 12.5 }}>{name}</span>
              <select
                value={bound}
                onChange={(e) =>
                  bindMut.mutate({ account: name, agentId: e.target.value })
                }
                style={{ ...inputStyle, flex: 1 }}
              >
                <option value="">（未绑定 · 使用全局配置）</option>
                {agents.map((a: AiAgentSummary) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
              <span
                style={{
                  flex: "0 0 auto",
                  fontSize: 11,
                  color: boundName ? "var(--accent)" : "var(--muted)",
                }}
              >
                {boundName || "全局"}
              </span>
            </div>
          );
        })}
      </Section>
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  width: "100%",
  background: "var(--surface-2)",
  border: "1px solid var(--border)",
  borderRadius: 4,
  padding: "4px 6px",
  fontSize: 12,
  color: "var(--text)",
  outline: "none",
};

function Section(props: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      style={{
        background: "var(--panel)",
        border: "1px solid var(--line)",
        borderRadius: 10,
        marginBottom: 10,
        padding: "12px 14px",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 10 }}>
        <span style={{ fontWeight: 700, fontSize: 13.5 }}>{props.title}</span>
        {props.subtitle && (
          <span style={{ fontSize: 12, color: "var(--muted)" }}>{props.subtitle}</span>
        )}
      </div>
      {props.children}
    </div>
  );
}

function Field(props: { label: string; children: React.ReactNode }) {
  return (
    <div style={{ flex: "1 0 220px", minWidth: 0, maxWidth: "100%" }}>
      <div style={{ color: "var(--muted)", fontSize: 11.5, marginBottom: 2 }}>
        {props.label}
      </div>
      {props.children}
    </div>
  );
}
