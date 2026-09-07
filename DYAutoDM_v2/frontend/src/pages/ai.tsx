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
import { useState, useCallback, useRef } from "react";
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

  const { data: cfg } = useQuery({
    queryKey: ["ai-config"],
    queryFn: async () =>
      (await api.aiGetConfig()).config as unknown as AiConfig,
  });
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
      await api.aiSaveConfig(draft as Record<string, unknown>);
      setDraft({});
      await qc.invalidateQueries({ queryKey: ["ai-config"] });
      await qc.invalidateQueries({ queryKey: ["ai-status"] });
      push("AI 配置已保存", 4000);
    } catch (e) {
      push(`保存失败: ${errMsg(e)}`, 8000);
    }
  }, [draft, api, push, qc]);

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

  const testVision = useCallback(async () => {
    push("正在测试视觉模型…", 10000);
    try {
      const r = await api.aiTestVision();
      push(r.ok ? `✅ ${r.msg}` : `❌ ${r.msg}`, 8000);
    } catch (e) { push(`测试失败: ${errMsg(e)}`, 8000); }
  }, [api, push]);

  // ---- 知识库编辑 ----
  const [kbQ, setKbQ] = useState(""); const [kbA, setKbA] = useState("");
  const [kbEditId, setKbEditId] = useState<number | null>(null);
  const kbSave = useCallback(async () => {
    if (!kbQ.trim() || !kbA.trim()) { push("问题和答案都不能为空"); return; }
    try {
      await api.aiKbSave({ id: kbEditId || 0, question: kbQ, answer: kbA });
      setKbQ(""); setKbA(""); setKbEditId(null);
      await qc.invalidateQueries({ queryKey: ["ai-kb"] });
      push(kbEditId ? "已更新" : "知识库条目已添加");
    } catch (e) { push(`保存失败: ${errMsg(e)}`, 6000); }
  }, [kbQ, kbA, kbEditId, api, push, qc]);
  const kbDel = useCallback(async (id: number) => {
    try {
      await api.aiKbDelete(id);
      await qc.invalidateQueries({ queryKey: ["ai-kb"] });
      push("已删除");
    } catch (e) { push(`删除失败: ${errMsg(e)}`, 6000); }
  }, [api, push, qc]);

  // ---- 文件导入（上传 → AI 生成 QA → 预览 → 确认写入）----
  const fileInputRef = useRef<HTMLInputElement | null>(null);
  const [importing, setImporting] = useState(false);
  const [progress, setProgress] = useState(0);
  const [importError, setImportError] = useState("");
  const [preview, setPreview] = useState<{ question: string; answer: string; source?: string }[]>([]);
  const [previewName, setPreviewName] = useState("");
  const [importMode, setImportMode] = useState("ai");
  const [replaceKb, setReplaceKb] = useState(false);

  const onFilePicked = useCallback(async (f: File | null) => {
    if (!f) return;
    setImporting(true); setProgress(0); setImportError(""); setPreview([]);
    try {
      const r = await api.aiKbImport(f, setProgress);
      if (!r.items || r.items.length === 0) {
        setImportError("未生成任何问答对（文档可能没有可用内容）");
      } else {
        setPreview(r.items);
        setPreviewName(r.filename);
        setImportMode(r.mode);
        push(`解析完成：${r.chars} 字 → ${r.items.length} 条问答对`, 5000);
      }
    } catch (e) {
      setImportError(errMsg(e));
    } finally {
      setImporting(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  }, [api, push]);

  const confirmImport = useCallback(async () => {
    try {
      const r = await api.aiKbImportConfirm(preview, replaceKb);
      setPreview([]);
      setReplaceKb(false);
      await qc.invalidateQueries({ queryKey: ["ai-kb"] });
      push(r.msg || `已导入 ${r.added} 条`, 5000);
    } catch (e) {
      push(`导入失败: ${errMsg(e)}`, 8000);
    }
  }, [preview, replaceKb, api, push, qc]);

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
    <div style={{ padding: "14px 18px", maxWidth: 980 }}>
      {/* ===== 标题 + 运行控制 ===== */}
      <Section
        title="🤖 AI 获客自动回复"
        subtitle="全自动监听新消息 → 知识库/AI 回复 → 留资捕获"
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
        <div style={{ fontSize: 12, color: "var(--muted-foreground)", marginTop: 8, lineHeight: 1.6 }}>
          监听只读本地数据库；回复经 WS 主通道发送、失败自动走 WP 兜底；
          客户发来手机号/微信号自动捕获入线索表。历史消息不会触发回复。
        </div>
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
        </div>
      </Section>

      {/* ===== 模型配置 ===== */}
      <Section title="🧠 模型配置" subtitle={`主 LLM：${c.model || "未配置"}${c.vision_enabled ? ` · 视觉：${c.vision_model}` : ""}`}>
        <Field label="API Key">
          <input style={inputStyle} type="password" value={c.api_key || ""}
                 onChange={(e) => set("api_key", e.target.value)}
                 placeholder="sk-…" />
        </Field>
        <div style={{ display: "flex", gap: 12 }}>
          <div style={{ flex: 1 }}>
            <Field label="Base URL（Anthropic 兼容协议）">
              <input style={inputStyle} value={c.base_url || ""}
                     onChange={(e) => set("base_url", e.target.value)} />
            </Field>
          </div>
          <div style={{ width: 220 }}>
            <Field label="模型名">
              <input style={inputStyle} value={c.model || ""}
                     onChange={(e) => set("model", e.target.value)} />
            </Field>
          </div>
        </div>
        <button onClick={testAi} style={{ ...miniBtn, marginBottom: 14 }}>
          测试 AI 连接
        </button>

        <div style={{ borderTop: "1px dashed var(--border)", paddingTop: 12, marginTop: 4 }}>
          <Field label="视觉模型（独立配置，用于理解客户发来的图片；主 LLM 不必支持多模态）">
            <label style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, cursor: "pointer" }}>
              <input type="checkbox" checked={!!c.vision_enabled}
                     onChange={(e) => set("vision_enabled", e.target.checked)} />
              启用视觉模型（未启用时图片消息回兜底话术，绝不瞎猜）
            </label>
          </Field>
          {c.vision_enabled && (
            <>
              <div style={{ display: "flex", gap: 12 }}>
                <div style={{ flex: 1 }}>
                  <Field label="视觉 Base URL（OpenAI 兼容 /chat/completions）">
                    <input style={inputStyle} value={c.vision_base_url || ""}
                           onChange={(e) => set("vision_base_url", e.target.value)}
                           placeholder="https://ark.cn-beijing.volces.com/api/v3" />
                  </Field>
                </div>
                <div style={{ width: 220 }}>
                  <Field label="视觉模型名">
                    <input style={inputStyle} value={c.vision_model || ""}
                           onChange={(e) => set("vision_model", e.target.value)}
                           placeholder="doubao-1-5-vision-pro-32k-250115" />
                  </Field>
                </div>
              </div>
              <Field label="视觉 API Key">
                <input style={inputStyle} type="password" value={c.vision_api_key || ""}
                       onChange={(e) => set("vision_api_key", e.target.value)} />
              </Field>
              <Field label="视觉提示词">
                <input style={inputStyle} value={c.vision_prompt || ""}
                       onChange={(e) => set("vision_prompt", e.target.value)} />
              </Field>
              <button onClick={testVision} style={{ ...miniBtn, marginBottom: 6 }}>
                测试视觉模型
              </button>
            </>
          )}
        </div>
      </Section>

      {/* ===== 知识库 ===== */}
      <Section title="📚 知识库" subtitle={`${kb?.items?.length ?? 0} 条 · 专业性来源 / RAG 资料`} defaultOpen>
        <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
          <input style={{ ...inputStyle, flex: 1 }} value={kbQ}
                 onChange={(e) => setKbQ(e.target.value)} placeholder="客户问…（例：价格是多少）" />
          <input style={{ ...inputStyle, flex: 1 }} value={kbA}
                 onChange={(e) => setKbA(e.target.value)} placeholder="标准答案…" />
          <button onClick={kbSave} style={miniBtn} disabled={!dirty && !kbQ && !kbA}>
            {kbEditId ? "更新" : "添加"}
          </button>
          {kbEditId !== null && (
            <button onClick={() => { setKbEditId(null); setKbQ(""); setKbA(""); }} style={miniBtn}>
              取消
            </button>
          )}
        </div>

        {/* ---- 文件导入：上传 → AI 生成 QA → 预览确认 ---- */}
        <div style={{
          border: "1px dashed var(--border)", borderRadius: 10,
          padding: "10px 12px", marginBottom: 12, fontSize: 12.5,
        }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
            <input ref={fileInputRef} type="file" accept=".txt,.md,.docx,.xlsx,.xls,.pdf,.png,.jpg,.jpeg,.webp"
                   style={{ display: "none" }} onChange={(e) => onFilePicked(e.target.files?.[0] ?? null)} />
            <button onClick={() => fileInputRef.current?.click()} style={miniBtn} disabled={importing}>
              📄 上传文件自动生成
            </button>
            <span style={{ color: "var(--muted-foreground)" }}>
              支持 docx / xlsx / pdf / txt / md{c.vision_enabled ? " / 图片(OCR)" : "（图片需先启用视觉模型）"}，≤20MB
            </span>
            {importing && <Pill c="accent">{`解析中 ${progress}%`}</Pill>}
          </div>
          {importError && (
            <div style={{ color: "var(--danger)", marginTop: 6 }}>❌ {importError}</div>
          )}
          {preview.length > 0 && (
            <div style={{ marginTop: 8 }}>
              <div style={{ marginBottom: 6, color: "var(--muted-foreground)" }}>
                从「{previewName}」生成 {preview.length} 条（AI 模式：{importMode === "ai" ? "是" : "降级·原文切分"}），可删改后确认：
              </div>
              <div style={{ maxHeight: 240, overflowY: "auto", marginBottom: 8 }}>
                {preview.map((it, i) => (
                  <div key={i} style={{ display: "flex", gap: 6, alignItems: "center",
                                        padding: "4px 0", borderBottom: "1px solid var(--border)" }}>
                    <input style={{ ...inputStyle, flex: 1, padding: "4px 8px", fontSize: 12 }}
                           value={it.question}
                           onChange={(e) => setPreview((p) => p.map((x, j) => j === i ? { ...x, question: e.target.value } : x))} />
                    <input style={{ ...inputStyle, flex: 1, padding: "4px 8px", fontSize: 12 }}
                           value={it.answer}
                           onChange={(e) => setPreview((p) => p.map((x, j) => j === i ? { ...x, answer: e.target.value } : x))} />
                    <button onClick={() => setPreview((p) => p.filter((_, j) => j !== i))}
                            style={{ ...miniBtn, color: "var(--danger)", flexShrink: 0 }}>删</button>
                  </div>
                ))}
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                <button onClick={confirmImport} style={miniBtn}>
                  ✅ 确认导入 {preview.length} 条
                </button>
                <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12, cursor: "pointer" }}>
                  <input type="checkbox" checked={replaceKb}
                         onChange={(e) => setReplaceKb(e.target.checked)} />
                  导入前清空现有知识库
                </label>
                <button onClick={() => { setPreview([]); setImportError(""); }} style={miniBtn}>
                  放弃
                </button>
              </div>
            </div>
          )}
        </div>
        {(kb?.items || []).map((it) => (
          <div key={it.id}
               style={{ display: "flex", gap: 8, alignItems: "center", padding: "7px 0",
                        borderBottom: "1px solid var(--border)", fontSize: 12.5 }}>
            <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
              <b>Q:</b> {it.question}
            </span>
            <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                           color: "var(--muted-foreground)" }}>
              <b>A:</b> {it.answer}
            </span>
            <button onClick={() => { setKbEditId(it.id); setKbQ(it.question); setKbA(it.answer); }}
                    style={miniBtn}>编辑</button>
            <button onClick={() => kbDel(it.id)}
                    style={{ ...miniBtn, color: "var(--danger)" }}>删除</button>
          </div>
        ))}
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
