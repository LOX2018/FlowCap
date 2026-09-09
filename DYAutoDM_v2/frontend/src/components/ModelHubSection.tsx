/**
 * 模型链路中心（v0.38.4）—— 模型配置唯一真源的 UI。
 *
 * 用户拍板（2026-09-09）：把模型配置设定为独立模块，AI 和 IM 通知都直接
 * 对接该模块，复用模块中的提供商（链路），各自只选采用的模型和链路。
 *
 * UI 两块：
 *   ① 模型链路（提供商连接）：新建（预设快速填 / 自定义手填）/ 编辑 / 删除
 *   ② 消费方绑定：AI 主模型 / AI 视觉 / AI 语义 / IM 指令解析，
 *      各自选一条链路 + 一个模型名；未绑定走回落（跟随 AI 主模型/默认链路）
 *
 * 数据面全部走 /api/modelhub/*（services/model_hub.py），与 ai_reply_config
 * 里的旧字段是一次性迁移关系，此处不读写旧字段。
 */
import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  PageProps,
  ModelHubEndpoint,
  ModelProvider,
} from "../api/client";

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

interface Binding {
  endpoint_id: string;
  model: string;
}

const inputStyle: React.CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  background: "var(--surface-2)",
  border: "1px solid var(--border)",
  borderRadius: 4,
  padding: "4px 6px",
  fontSize: 12,
  color: "var(--text)",
  outline: "none",
};

function Block(props: {
  title: string;
  subtitle?: string;
  children: React.ReactNode;
}) {
  return (
    <div className="set-card">
      <div className="set-card-head is-open">
        <span style={{ fontWeight: 700, fontSize: 13.5 }}>{props.title}</span>
        {props.subtitle && (
          <span style={{ fontSize: 12, color: "var(--muted)" }}>
            {props.subtitle}
          </span>
        )}
      </div>
      <div className="set-card-body">{props.children}</div>
    </div>
  );
}

export default function ModelHubSection(props: PageProps) {
  const { api, ready, push } = props;
  const qc = useQueryClient();

  const q = useQuery({
    queryKey: ["modelhub"],
    queryFn: () => api.modelhubOverview(),
    enabled: !!ready,
    staleTime: 10_000,
  });

  const endpoints: ModelHubEndpoint[] = q.data?.endpoints || [];
  const consumersMeta = q.data?.consumers_meta || [];
  const bindings: Record<string, Binding> = q.data?.bindings || {};
  const presets: ModelProvider[] = q.data?.presets || [];

  const refresh = () => qc.invalidateQueries({ queryKey: ["modelhub"] });

  // ---- 链路编辑 ----
  const [epDraft, setEpDraft] = useState<Partial<ModelHubEndpoint> | null>(
    null,
  );

  const saveEpMut = useMutation({
    mutationFn: (ep: Partial<ModelHubEndpoint>) =>
      api.modelhubSaveEndpoint(ep),
    onSuccess: () => {
      push("链路已保存");
      setEpDraft(null);
      refresh();
    },
    onError: (e) => push(`保存失败：${errMsg(e)}`),
  });

  const delEpMut = useMutation({
    mutationFn: (id: string) => api.modelhubDeleteEndpoint(id),
    onSuccess: (r) => {
      const n = r.unbound_consumers?.length || 0;
      push(n ? `链路已删除，${n} 个消费方已回落默认` : "链路已删除");
      refresh();
    },
    onError: (e) => push(`删除失败：${errMsg(e)}`),
  });

  // 预设快速填充：选中预设 → 带出 base_url / 协议（key 需用户自填）
  const pickPreset = (pid: string) => {
    const p = presets.find((x) => x.id === pid);
    if (!p) return;
    setEpDraft({
      ...epDraft,
      name: p.id === "custom" ? "" : p.name,
      base_url: p.base_url,
      api_protocol: p.api_protocol,
    });
  };

  // ---- 消费方绑定：本地编辑态，统一保存 ----
  const [bindDraft, setBindDraft] = useState<
    Record<string, { endpoint_id: string; model: string }>
  >({});
  useEffect(() => {
    if (q.data && Object.keys(bindDraft).length === 0) {
      setBindDraft(JSON.parse(JSON.stringify(bindings)));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q.data]);

  const bindMut = useMutation({
    mutationFn: async () => {
      for (const [cid, b] of Object.entries(bindDraft)) {
        await api.modelhubBind(cid, b.endpoint_id, b.model);
      }
    },
    onSuccess: () => {
      push("模型绑定已保存（立即生效）");
      refresh();
    },
    onError: (e) => push(`保存失败：${errMsg(e)}`),
  });

  const endpointOf = (id: string) =>
    endpoints.find((e) => e.id === id);

  const setBind = (cid: string, patch: Partial<Binding>) => {
    const cur = bindDraft[cid] || { endpoint_id: "", model: "" };
    setBindDraft({ ...bindDraft, [cid]: { ...cur, ...patch } });
  };

  const modelOptionsOf = (ep: ModelHubEndpoint | undefined) => {
    if (!ep) return [] as string[];
    const preset = presets.find(
      (p) =>
        p.base_url &&
        p.base_url.replace(/\/$/, "") === ep.base_url.replace(/\/$/, ""),
    );
    return preset?.models_chat || [];
  };

  if (q.isLoading) {
    return <div style={{ padding: 20, color: "var(--muted)" }}>加载模型链路…</div>;
  }
  if (q.isError) {
    return (
      <div style={{ padding: 20, color: "var(--danger, #c0392b)" }}>
        模型链路加载失败：{errMsg(q.error)}
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
        模型配置的<b>唯一入口</b>：这里维护可用的模型服务（链路），AI
        私信回复、图片理解、语义检索、IM 通知指令解析都来这里选链路和模型，
        不再各自配置连接参数。
      </div>

      {/* ===== ① 模型链路 ===== */}
      <Block title="模型链路" subtitle={`${endpoints.length} 条 · 提供商连接`}>
        {endpoints.length === 0 && (
          <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 8 }}>
            还没有链路，点「新建链路」用预设快速创建
          </div>
        )}
        {endpoints.map((ep) => (
          <div
            key={ep.id}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              padding: "8px 10px",
              background: "var(--surface)",
              borderRadius: 8,
              border: "1px solid var(--border)",
              marginBottom: 8,
              fontSize: 12,
            }}
          >
            <div style={{ flex: "0 0 150px", fontWeight: 600 }}>
              {ep.name}
              <div style={{ fontSize: 10.5, color: "var(--muted)" }}>
                {ep.api_protocol}
              </div>
            </div>
            <div style={{ flex: 1, color: "var(--muted)", wordBreak: "break-all" }}>
              {ep.base_url || "（未填地址）"}
              {ep.api_key ? " · 🔑" : " · 无密钥"}
            </div>
            <button
              className="btn sm"
              onClick={() =>
                setEpDraft({ ...ep, api_key: ep.api_key ? "••••••••" : "" })
              }
            >
              编辑
            </button>
            <button
              className="btn sm danger"
              onClick={() => {
                if (confirm(`删除链路「${ep.name}」？绑定的消费方会自动回落默认链路。`))
                  delEpMut.mutate(ep.id);
              }}
            >
              删除
            </button>
          </div>
        ))}
        <div style={{ display: "flex", justifyContent: "flex-end", gap: 8 }}>
          <button className="btn sm accent" onClick={() => setEpDraft({ name: "", base_url: "", api_protocol: "openai", api_key: "" })}>
            + 新建链路
          </button>
        </div>

        {/* 新建/编辑表单 */}
        {epDraft && (
          <div
            style={{
              border: "1px solid var(--accent)",
              borderRadius: 10,
              padding: "10px 12px",
              marginTop: 8,
              display: "flex",
              flexWrap: "wrap",
              gap: 12,
            }}
          >
            <div style={{ width: "100%" }}>
              <div style={{ fontSize: 11.5, color: "var(--muted)", marginBottom: 4 }}>
                从预设填充（也可直接手填 = 自定义）
              </div>
              <select
                style={inputStyle}
                value={presets.find((p) => p.base_url === epDraft.base_url)?.id || ""}
                onChange={(e) => pickPreset(e.target.value)}
              >
                <option value="">— 选择提供商预设 —</option>
                {presets.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </div>
            <div style={{ flex: 1, minWidth: 160 }}>
              <div style={{ fontSize: 11.5, color: "var(--muted)", marginBottom: 4 }}>
                链路名称
              </div>
              <input
                style={inputStyle}
                value={epDraft.name || ""}
                placeholder="例：本机 FreeLLM / DeepSeek 官方"
                onChange={(e) => setEpDraft({ ...epDraft, name: e.target.value })}
              />
            </div>
            <div style={{ width: 120 }}>
              <div style={{ fontSize: 11.5, color: "var(--muted)", marginBottom: 4 }}>
                协议
              </div>
              <select
                style={inputStyle}
                value={epDraft.api_protocol || "openai"}
                onChange={(e) =>
                  setEpDraft({ ...epDraft, api_protocol: e.target.value })
                }
              >
                <option value="openai">OpenAI 兼容</option>
                <option value="anthropic">Anthropic 兼容</option>
              </select>
            </div>
            <div style={{ flex: 2, minWidth: 220 }}>
              <div style={{ fontSize: 11.5, color: "var(--muted)", marginBottom: 4 }}>
                Base URL（填到 /v1 这一级）
              </div>
              <input
                style={inputStyle}
                value={epDraft.base_url || ""}
                placeholder="http://127.0.0.1:31415/v1"
                onChange={(e) =>
                  setEpDraft({ ...epDraft, base_url: e.target.value })
                }
              />
            </div>
            <div style={{ flex: 1, minWidth: 160 }}>
              <div style={{ fontSize: 11.5, color: "var(--muted)", marginBottom: 4 }}>
                API Key（•••• = 保留原值）
              </div>
              <input
                style={inputStyle}
                value={epDraft.api_key || ""}
                placeholder="本机服务可留空"
                onChange={(e) =>
                  setEpDraft({ ...epDraft, api_key: e.target.value })
                }
              />
            </div>
            <div
              style={{
                width: "100%",
                display: "flex",
                justifyContent: "flex-end",
                gap: 8,
              }}
            >
              <button className="btn sm" onClick={() => setEpDraft(null)}>
                取消
              </button>
              <button
                className="btn sm accent"
                disabled={saveEpMut.isPending || !epDraft.base_url}
                onClick={() => saveEpMut.mutate(epDraft)}
              >
                {saveEpMut.isPending ? "保存中…" : "保存链路"}
              </button>
            </div>
          </div>
        )}
      </Block>

      {/* ===== ② 消费方绑定 ===== */}
      <Block title="消费方绑定" subtitle="各模块只选链路和模型，不配连接参数">
        {consumersMeta.map((c) => {
          const b = bindDraft[c.id] || { endpoint_id: "", model: "" };
          const ep = endpointOf(b.endpoint_id);
          const opts = modelOptionsOf(ep);
          return (
            <div
              key={c.id}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                padding: "8px 10px",
                background: "var(--surface)",
                borderRadius: 8,
                border: "1px solid var(--border)",
                marginBottom: 8,
                flexWrap: "wrap",
              }}
            >
              <span style={{ flex: "0 0 210px", fontSize: 12.5 }}>
                {c.label}
              </span>
              <select
                style={{ ...inputStyle, flex: 1, minWidth: 170 }}
                value={b.endpoint_id}
                onChange={(e) => setBind(c.id, { endpoint_id: e.target.value })}
              >
                <option value="">
                  {c.id === "notify_cmd"
                    ? "（跟随 AI 主模型）"
                    : "（默认链路 = 第一条）"}
                </option>
                {endpoints.map((e2) => (
                  <option key={e2.id} value={e2.id}>
                    {e2.name}
                  </option>
                ))}
              </select>
              {opts.length > 0 ? (
                <select
                  style={{ ...inputStyle, flex: 1, minWidth: 190 }}
                  value={opts.includes(b.model) ? b.model : b.model || opts[0]}
                  onChange={(e) => setBind(c.id, { model: e.target.value })}
                >
                  {!opts.includes(b.model) && b.model && (
                    <option value={b.model}>{b.model}（当前）</option>
                  )}
                  {opts.map((m: string) => (
                    <option key={m} value={m}>
                      {m}
                    </option>
                  ))}
                </select>
              ) : (
                <input
                  style={{ ...inputStyle, flex: 1, minWidth: 190 }}
                  value={b.model}
                  placeholder="模型名（必填）"
                  onChange={(e) => setBind(c.id, { model: e.target.value })}
                />
              )}
              <span
                style={{
                  flex: "0 0 auto",
                  fontSize: 11,
                  color: ep ? "var(--accent)" : "var(--muted)",
                }}
              >
                {ep ? ep.api_protocol : "未绑定"}
              </span>
            </div>
          );
        })}
        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 8 }}>
          <button
            className="btn accent sm"
            disabled={bindMut.isPending}
            onClick={() => bindMut.mutate()}
          >
            {bindMut.isPending ? "保存中…" : "保存绑定"}
          </button>
        </div>
        <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 6, lineHeight: 1.6 }}>
          回落规则：IM 指令解析未绑定时跟随「AI 主模型」；其余消费方未绑定时用
          第一条链路。删链路会自动解绑并回落，不会中断服务。
        </div>
      </Block>
    </div>
  );
}
