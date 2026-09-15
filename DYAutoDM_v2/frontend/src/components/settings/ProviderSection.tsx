/**
 * 模型链路中心 —— ① 提供商区块
 *
 * ## 为什么独立（2026-09-15 大组件打散）
 *
 * `ModelHubSection.tsx`（637 行）把「提供商」的**状态/变更/UI**全内联在
 * 主组件里（逻辑 49 行 + UI 115 行）。抽出后，表单状态 pvDraft 随组件内聚，
 * 主组件只保留跨区块的公共数据（providers/models/presets）。
 *
 * ## 依赖（显式 props）
 *   providers / presets / api / push / refresh
 * **纯搬移**——mutation 定义、文案、交互逐字节不变。
 */
import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import type { HubProvider, HubModel } from "../../api/client";
import type { PageProps } from "../../api/client";
import { errMsg, SectionBlock, inputStyle } from "./settings-shared";

interface ProviderSectionProps {
  providers: HubProvider[];
  models: HubModel[];
  presets: { id: string; name: string; base_url: string; api_protocol: string }[];
  api: PageProps["api"];
  push: PageProps["push"];
  refresh: () => void;
}

export function ProviderSection({ providers, models, presets, api, push, refresh }: ProviderSectionProps) {
  const [pvDraft, setPvDraft] = useState<Partial<HubProvider> | null>(null);

  const savePvMut = useMutation({
    mutationFn: (p: Partial<HubProvider>) => api.modelhubSaveProvider(p),
    onSuccess: () => {
      push("提供商已保存");
      setPvDraft(null);
      refresh();
    },
    onError: (e) => push(`保存失败：${errMsg(e)}`),
  });

  const delPvMut = useMutation({
    mutationFn: (id: string) => api.modelhubDeleteProvider(id),
    onSuccess: (r) => {
      push(`提供商已删除（清理 ${r.removed_models} 个模型）`);
      refresh();
    },
    onError: (e) => push(`删除失败：${errMsg(e)}`),
  });

  const testPvMut = useMutation({
    mutationFn: (id: string) => api.modelhubTestProvider(id),
    onSuccess: (r) => {
      push(r.result?.ok ? `密钥有效：${r.result.detail || ""}` : `测试失败：${r.result?.detail || ""}`);
      refresh();
    },
    onError: (e) => push(`测试失败：${errMsg(e)}`),
  });

  const fetchMut = useMutation({
    mutationFn: (id: string) => api.modelhubFetchModels(id),
    onSuccess: (r) => {
      push(`拉取完成：共 ${r.total} 个，新增 ${r.added} 个${r.warning ? `（${r.warning}）` : ""}`);
      refresh();
    },
    onError: (e) => push(`拉取失败：${errMsg(e)}`),
  });

  const pickPreset = (pid: string) => {
    const p = presets.find((x) => x.id === pid);
    if (!p) return;
    setPvDraft({
      ...pvDraft,
      name: p.id === "custom" ? "" : p.name,
      base_url: p.base_url,
      api_protocol: p.api_protocol,
    });
  };

  // ===== ① 提供商 =====
  return (
    <SectionBlock title="① 提供商" subtitle={`${providers.length} 个 · 管密钥 / 拉模型 / 测密钥`}>
      {providers.map((p) => {
        const st = p.key_status;
        return (
          <div key={p.id} style={{
            display: "flex", alignItems: "center", gap: 10,
            padding: "8px 10px", background: "var(--color-surface-solid)",
            borderRadius: 8, border: "1px solid var(--color-border)",
            marginBottom: 8, fontSize: 12, flexWrap: "wrap",
          }}>
            <div style={{ flex: "0 0 160px", fontWeight: 600 }}>
              {p.name}
              <div style={{ fontSize: 10.5, color: "var(--color-text-muted)" }}>
                {p.api_protocol}{p.api_key ? " · 🔑" : " · 无密钥"}
              </div>
            </div>
            <div style={{ flex: 1, color: "var(--color-text-muted)", wordBreak: "break-all", minWidth: 140 }}>
              {p.base_url || "（未填地址）"}
              <div style={{ fontSize: 10.5, marginTop: 2 }}>
                {models.filter((m) => m.provider_id === p.id).length} 个模型
                {st?.checked_at ? (
                  <span style={{ color: st.ok ? "var(--color-accent)" : "var(--danger, #c0392b)" }}>
                    {" "}· {st.ok ? "密钥有效" : (st.detail || "密钥无效")}
                  </span>
                ) : null}
              </div>
            </div>
            <button className="inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold transition-[background-color,color,border-color,box-shadow,transform,opacity] duration-200 ease-[var(--ease-spring)] cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-ring)] disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] bg-[var(--color-surface-raised)] text-[var(--color-text)] shadow-[inset_0_0_0_1px_var(--color-border)] hover:bg-[var(--color-surface-solid)] h-9 px-4 text-[0.78rem] rounded-[10px]" disabled={testPvMut.isPending}
              onClick={() => testPvMut.mutate(p.id)}>测密钥</button>
            <button className="inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold transition-[background-color,color,border-color,box-shadow,transform,opacity] duration-200 ease-[var(--ease-spring)] cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-ring)] disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] bg-[var(--color-surface-raised)] text-[var(--color-text)] shadow-[inset_0_0_0_1px_var(--color-border)] hover:bg-[var(--color-surface-solid)] h-9 px-4 text-[0.78rem] rounded-[10px]" disabled={fetchMut.isPending}
              onClick={() => fetchMut.mutate(p.id)}>
              {fetchMut.isPending ? "拉取中…" : "拉取模型"}
            </button>
            <button className="inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold transition-[background-color,color,border-color,box-shadow,transform,opacity] duration-200 ease-[var(--ease-spring)] cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-ring)] disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] bg-[var(--color-surface-raised)] text-[var(--color-text)] shadow-[inset_0_0_0_1px_var(--color-border)] hover:bg-[var(--color-surface-solid)] h-9 px-4 text-[0.78rem] rounded-[10px]"
              onClick={() => setPvDraft({ ...p, api_key: p.api_key ? "••••••••" : "" })}>
              编辑
            </button>
            <button className="inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold transition-[background-color,color,border-color,box-shadow,transform,opacity] duration-200 ease-[var(--ease-spring)] cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-ring)] disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] border border-[color-mix(in_srgb,var(--color-danger)_25%,transparent)] bg-[var(--color-danger-soft)] text-[var(--color-danger)] hover:bg-[var(--color-danger)] hover:text-white h-9 px-4 text-[0.78rem] rounded-[10px]"
              onClick={() => {
                if (confirm(`删除提供商「${p.name}」？其模型与链路绑定会一并清理。`))
                  delPvMut.mutate(p.id);
              }}>
              删除
            </button>
          </div>
        );
      })}
      <div style={{ display: "flex", justifyContent: "flex-end" }}>
        <button className="inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold transition-[background-color,color,border-color,box-shadow,transform,opacity] duration-200 ease-[var(--ease-spring)] cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-ring)] disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] bg-[var(--color-accent)] text-[#08130a] shadow-lg hover:bg-[var(--color-accent-hover)] h-9 px-4 text-[0.78rem] rounded-[10px]"
          onClick={() => setPvDraft({ name: "", base_url: "", api_protocol: "openai", api_key: "" })}>
          + 新建提供商
        </button>
      </div>

      {pvDraft && (
        <div style={{
          border: "1px solid var(--color-accent)", borderRadius: 10,
          padding: "10px 12px", marginTop: 8, display: "flex",
          flexWrap: "wrap", gap: 12,
        }}>
          <div style={{ width: "100%" }}>
            <div style={{ fontSize: 11.5, color: "var(--color-text-muted)", marginBottom: 4 }}>
              从预设填充（也可直接手填 = 自定义）
            </div>
            <select style={inputStyle} value=""
              onChange={(e) => pickPreset(e.target.value)}>
              <option value="">— 选择提供商预设 —</option>
              {presets.map((p) => (
                <option key={p.id} value={p.id}>{p.name}</option>
              ))}
            </select>
          </div>
          <div style={{ flex: 1, minWidth: 160 }}>
            <div style={{ fontSize: 11.5, color: "var(--color-text-muted)", marginBottom: 4 }}>名称</div>
            <input style={inputStyle} value={pvDraft.name || ""}
              placeholder="例：本机 FreeLLM / DeepSeek 官方"
              onChange={(e) => setPvDraft({ ...pvDraft, name: e.target.value })} />
          </div>
          <div style={{ width: 120 }}>
            <div style={{ fontSize: 11.5, color: "var(--color-text-muted)", marginBottom: 4 }}>协议</div>
            <select style={inputStyle} value={pvDraft.api_protocol || "openai"}
              onChange={(e) => setPvDraft({ ...pvDraft, api_protocol: e.target.value })}>
              <option value="openai">OpenAI 兼容</option>
              <option value="anthropic">Anthropic 兼容</option>
            </select>
          </div>
          <div style={{ flex: 2, minWidth: 220 }}>
            <div style={{ fontSize: 11.5, color: "var(--color-text-muted)", marginBottom: 4 }}>
              Base URL（填到 /v1 这一级）
            </div>
            <input style={inputStyle} value={pvDraft.base_url || ""}
              placeholder="http://127.0.0.1:31415/v1"
              onChange={(e) => setPvDraft({ ...pvDraft, base_url: e.target.value })} />
          </div>
          <div style={{ flex: 1, minWidth: 160 }}>
            <div style={{ fontSize: 11.5, color: "var(--color-text-muted)", marginBottom: 4 }}>
              API Key（•••• = 保留原值）
            </div>
            <input style={inputStyle} value={pvDraft.api_key || ""}
              placeholder="本机服务可留空"
              onChange={(e) => setPvDraft({ ...pvDraft, api_key: e.target.value })} />
          </div>
          <div style={{ width: "100%", display: "flex", justifyContent: "flex-end", gap: 8 }}>
            <button className="inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold transition-[background-color,color,border-color,box-shadow,transform,opacity] duration-200 ease-[var(--ease-spring)] cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-ring)] disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] bg-[var(--color-surface-raised)] text-[var(--color-text)] shadow-[inset_0_0_0_1px_var(--color-border)] hover:bg-[var(--color-surface-solid)] h-9 px-4 text-[0.78rem] rounded-[10px]" onClick={() => setPvDraft(null)}>取消</button>
            <button className="inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold transition-[background-color,color,border-color,box-shadow,transform,opacity] duration-200 ease-[var(--ease-spring)] cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-ring)] disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] bg-[var(--color-accent)] text-[#08130a] shadow-lg hover:bg-[var(--color-accent-hover)] h-9 px-4 text-[0.78rem] rounded-[10px]"
              disabled={savePvMut.isPending || !pvDraft.base_url}
              onClick={() => savePvMut.mutate(pvDraft)}>
              {savePvMut.isPending ? "保存中…" : "保存提供商"}
            </button>
          </div>
        </div>
      )}
    </SectionBlock>
  );

}
