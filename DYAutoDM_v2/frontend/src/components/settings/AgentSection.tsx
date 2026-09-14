/**
 * AI Agent 模版管理 + 账号绑定（v0.38.0）
 *
 * 设计（用户 2026-09-09 拍板）：
 *   - Agent 是**模版**：含模型/档位/商家名/prompt/知识库/黑名单/兜底话术
 *   - 账号绑定 Agent；改 Agent 一次 → 所有绑定它的账号同步生效
 *   - 绑定关系在配置中心维护（避免两处配置分裂）
 *
 * UI 结构：
 *   ① Agent 列表卡片：新建 / 重命名 / 删除 / 选中编辑
 *   ② Agent 参数编辑：核心字段（商家名、档位、模型、总开关、延迟）
 *      —— 只放「模版与绑定」；引擎参数（prompt/护栏/黑名单）在同页「AI 回复引擎」
 *   ③ 账号绑定矩阵：每个账号一个下拉，选它用哪个 Agent
 */
import { useState, useEffect, useCallback } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { PageProps } from "../../api/client";
import type { AiAgentSummary } from "../../api/client";
import { SetCard, SetCardHead, SetCardBody, SetField } from "@/components/page/set-card";

const LEVELS = [
  { value: "kb_only", label: "kb_only（仅知识库，AI 不参与）" },
  { value: "rag", label: "rag（AI 只准依据知识库答，默认）" },
  { value: "free", label: "free（纯 prompt 约束）" },
];

/** Agent 作用域（私信 Agent）：AI 智能回复注入哪些模块 */
const AGENT_SCOPES = ["dm", "live", "crawl"] as const;
const SCOPE_LABELS: Record<string, string> = {
  dm: "私信中心",
  live: "直播监听",
  crawl: "视频采集",
};

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export default function AgentSection(props: PageProps) {
  const { api, ready, push } = props;
  const qc = useQueryClient();

  const [selId, setSelId] = useState<string>("");
  const [draft, setDraft] = useState<Record<string, string | string[]>>({});

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
      scopes: sel.scopes || ["dm"],
    } as Record<string, string | string[]>);
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
      scopes: Array.isArray(draft.scopes) ? draft.scopes : (["dm"] as string[]),
    };
    saveMut.mutate({
      id: selId || undefined,
      name: (typeof draft.name === "string" ? draft.name : "") || "未命名 Agent",
      config: cfg,
    });
  }, [draft, selId, saveMut]);

  if (q.isLoading) {
    return <div style={{ padding: 20, color: "var(--color-text-muted)" }}>加载 Agent…</div>;
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
          color: "var(--color-text-muted)",
          marginBottom: 10,
          lineHeight: 1.6,
        }}
      >
        Agent 是<b>模版</b>：建一个 Agent，让多个账号绑定它 —— 改一次，所有绑定
        账号同步生效。知识库 / 黑名单 / 兜底话术都跟随 Agent，账号不单独持有。
      </div>

      {/* ⓪ 全局模型配置（v0.38.3：模型配置在配置中心体现，与「AI 回复引擎」同源） */}
      <GlobalModelCard api={api} ready={ready} push={push} />

      {/* ① Agent 列表 */}
      <Section title="Agent 列表" subtitle={`${agents.length} 个`}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 10 }}>
          {agents.length === 0 && (
            <span style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
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
            className="inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold transition-[background-color,color,border-color,box-shadow,transform,opacity] duration-200 ease-[var(--ease-spring)] cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-ring)] disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] bg-[var(--color-surface-raised)] text-[var(--color-text)] shadow-[inset_0_0_0_1px_var(--color-border)] hover:bg-[var(--color-surface-solid)] h-9 px-4 text-[0.78rem] rounded-[10px]"
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
              className="inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold transition-[background-color,color,border-color,box-shadow,transform,opacity] duration-200 ease-[var(--ease-spring)] cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-ring)] disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] bg-[var(--color-surface-raised)] text-[var(--color-text)] shadow-[inset_0_0_0_1px_var(--color-border)] hover:bg-[var(--color-surface-solid)] h-9 px-4 text-[0.78rem] rounded-[10px]"
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
          {/* 作用域：该 Agent 的 AI 智能回复注入哪些模块 */}
          <Field label="作用域（AI 智能回复应用到哪些模块）">
            <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 12.5 }}>
              {(Array.isArray(draft.scopes) ? draft.scopes : ["dm"]).map((s: string) => (
                <label key={s} style={{ display: "flex", alignItems: "center", gap: 5, cursor: "pointer" }}>
                  <input
                    type="checkbox"
                    checked={true}
                    onChange={() =>
                      setDraft({
                        ...draft,
                        scopes: (Array.isArray(draft.scopes) ? draft.scopes : []).filter(
                          (x: string) => x !== s,
                        ),
                      })
                    }
                  />
                  {SCOPE_LABELS[s] || s}
                </label>
              ))}
              {AGENT_SCOPES.filter(
                (s) => !(Array.isArray(draft.scopes) ? draft.scopes : ["dm"]).includes(s),
              ).map((s: string) => (
                <label key={s} style={{ display: "flex", alignItems: "center", gap: 5, cursor: "pointer" }}>
                  <input
                    type="checkbox"
                    checked={false}
                    onChange={() =>
                      setDraft({ ...draft, scopes: [...(draft.scopes || []), s] })
                    }
                  />
                  {SCOPE_LABELS[s]}
                </label>
              ))}
            </div>
          </Field>
          <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 12 }}>
            <button
              className="inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold transition-[background-color,color,border-color,box-shadow,transform,opacity] duration-200 ease-[var(--ease-spring)] cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-ring)] disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] bg-[var(--color-accent)] text-[#08130a] shadow-lg hover:bg-[var(--color-accent-hover)] h-9 px-4 text-[0.78rem] rounded-[10px]"
              onClick={doSave}
              disabled={saveMut.isPending}
            >
              {saveMut.isPending ? "保存中…" : "保存 Agent"}
            </button>
          </div>
          <div style={{ fontSize: 11, color: "var(--color-text-muted)", marginTop: 8 }}>
            更完整的参数（prompt / 护栏 / 兜底话术 / 黑名单）在同页
            <b>「AI 回复引擎」</b> 调整；此处只放模版与绑定。
          </div>
        </Section>
      )}

      {/* ③ 账号绑定 */}
      <Section title="账号绑定" subtitle={`${acctList.length} 个账号`}>
        {acctList.length === 0 && (
          <div style={{ fontSize: 12, color: "var(--color-text-muted)" }}>暂无账号</div>
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
                background: "var(--color-surface-solid)",
                borderRadius: 8,
                border: "1px solid var(--color-border)",
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
                <option value="">（未绑定 · 不参与 AI 智能回复）</option>
                {agents.map((a: AiAgentSummary) => (
                  <option key={a.id} value={a.id}>
                    {a.name}
                  </option>
                ))}
              </select>
              {/* 作用域显示：绑定后该账号 AI 智能回复的实际生效状态 */}
              {bound ? (
                (() => {
                  const ag = agents.find((a: AiAgentSummary) => a.id === bound);
                  const effOn = !!ag?.enabled;
                  return (
                    <span
                      style={{
                        flex: "0 0 auto",
                        display: "flex",
                        flexDirection: "column",
                        alignItems: "flex-end",
                        gap: 2,
                      }}
                    >
                      <span
                        style={{
                          fontSize: 11,
                          fontWeight: 600,
                          color: effOn ? "var(--color-accent)" : "var(--color-text-muted)",
                        }}
                      >
                        {boundName || "全局"}
                        {effOn ? " · AI回复开启" : " · AI回复关闭"}
                      </span>
                      <span style={{ fontSize: 10.5, color: "var(--color-text-muted)" }}>
                        {ag?.strict_level || "rag"} · {ag?.model || "auto"} ·
                        知识库{ag?.kb_count ?? 0}条
                      </span>
                    </span>
                  );
                })()
              ) : (
                <span
                  style={{
                    flex: "0 0 auto",
                    fontSize: 11,
                    color: "var(--color-text-muted)",
                  }}
                >
                  不在 AI 作用域
                </span>
              )}
            </div>
          );
        })}
      </Section>
    </div>
  );
}

/** 输入框样式 —— 用设计令牌（旧 `var(--color-surface-raised)`/`var(--color-border)` 已随旧 CSS 下线）。 */
const inputStyle: React.CSSProperties = {
  width: "100%",
  background: "var(--color-surface)",
  border: "1px solid var(--color-border)",
  borderRadius: 6,
  padding: "5px 7px",
  fontSize: 12,
  color: "var(--color-text)",
  outline: "none",
};

/** 设置区块（对标旧 `.set-card`，已收敛到 `page/set-card` 组件族）。 */
function Section(props: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <SetCard>
      <SetCardHead title={props.title} description={props.subtitle} />
      <SetCardBody>{props.children}</SetCardBody>
    </SetCard>
  );
}

/** 字段（对标旧 `.set-field`）。 */
function Field(props: { label: string; children: React.ReactNode }) {
  return (
    <SetField
      label={
        <span className="text-[0.72rem] font-normal text-[var(--color-text-muted)]">
          {props.label}
        </span>
      }
    >
      {props.children}
    </SetField>
  );
}

/**
 * 全局模型配置卡片（v0.38.3）。
 *
 * 用户要求「模型的配置应该在设置中体现」——这里与「AI 回复引擎」**同源**读写
 * `/api/ai/config`（全局那份），不是第二份存储。两边任一处改，刷新即见。
 * 语义完全一致：api_key 明文存储、password 框显示；
 * 保存只传**变更字段**（patch），未动过的键绝不发送（避免误清 api_key）。
 */
function GlobalModelCard(props: PageProps) {
  const { api, ready, push } = props;
  const qc = useQueryClient();

  const q = useQuery({
    queryKey: ["ai-config", ""],
    queryFn: () => api.aiGetConfig(),
    enabled: !!ready,
    staleTime: 10_000,
  });
  const cfg = (q.data?.config || {}) as Record<string, unknown>;

  const [draft, setDraft] = useState<Record<string, string | string[]>>({});
  const cur = (k: string) =>
    draft[k] !== undefined ? draft[k] : String(cfg[k] ?? "");
  const set = (k: string, v: string) => setDraft({ ...draft, [k]: v });

  const saveMut = useMutation({
    // patch 语义：只传用户改过的字段（与「AI 回复引擎」一致）
    mutationFn: () => api.aiSaveConfig(draft),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ai-config"] });
      setDraft({});
      push("全局模型配置已保存（AI 回复引擎同步生效）");
    },
    onError: (e) => push(`保存失败：${errMsg(e)}`),
  });

  return (
    <Section title="全局模型配置" subtitle="与「AI 回复引擎」同源 · 未绑定 Agent 的账号用这份">
      <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
        <Field label="API 地址（base_url）">
          <input
            value={cur("base_url")}
            onChange={(e) => set("base_url", e.target.value)}
            placeholder="http://127.0.0.1:31415/v1"
            style={inputStyle}
          />
        </Field>
        <Field label="主模型（model）">
          <input
            value={cur("model")}
            onChange={(e) => set("model", e.target.value)}
            placeholder="如 qwen2.5-7b-instruct"
            style={inputStyle}
          />
        </Field>
        <Field label="API Key">
          <input
            type="password"
            value={cur("api_key")}
            onChange={(e) => set("api_key", e.target.value)}
            placeholder="sk-...（本机服务可留空）"
            style={inputStyle}
          />
        </Field>
      </div>
      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 12 }}>
        <button
          className="inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold transition-[background-color,color,border-color,box-shadow,transform,opacity] duration-200 ease-[var(--ease-spring)] cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-ring)] disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] bg-[var(--color-accent)] text-[#08130a] shadow-lg hover:bg-[var(--color-accent-hover)] h-9 px-4 text-[0.78rem] rounded-[10px]"
          onClick={() => saveMut.mutate()}
          disabled={saveMut.isPending || Object.keys(draft).length === 0}
        >
          {saveMut.isPending ? "保存中…" : "保存模型配置"}
        </button>
      </div>
      <div style={{ fontSize: 11, color: "var(--color-text-muted)", marginTop: 8 }}>
        与「AI 回复引擎」共享同一份全局配置；各 Agent 可单独覆盖主模型。
        IM 通知的指令解析模型在「通知与指令」。
      </div>
    </Section>
  );
}
