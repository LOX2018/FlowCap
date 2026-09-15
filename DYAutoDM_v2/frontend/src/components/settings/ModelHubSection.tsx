/**
 * 模型中心（v0.39.0）—— 模型配置唯一真源的 UI。
 *
 * 三层结构：
 *   ① 提供商：管密钥（base_url/协议/api_key）、拉取模型、测试密钥有效性
 *   ② 模型：提供商提供的模型统一注册，带能力标签（llm/视觉/语义）
 *      + 三条避障链路（llm/vision/sem，每链最多 6 个模型，按序避障）
 *      + 兜底模型（须同时支持 LLM+视觉，附在 llm/vision 链末尾）
 *   ③ 消费方绑定：AI 主模型 / AI 视觉 / AI 语义 / IM 指令解析，
 *      二选一 —— 选一条避障链路（享避障+兜底）或固定某提供商的模型
 *
 * 数据面全部走 /api/modelhub/*（services/model_hub.py）。
 */
import { useState, useEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { PageProps, HubProvider, HubModel, HubRouteKind } from "../../api/client";
import { errMsg, SectionBlock } from "./settings-shared";

const inputStyle: React.CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  background: "var(--color-surface)",
  border: "1px solid var(--color-border)",
  borderRadius: 4,
  padding: "4px 6px",
  fontSize: 12,
  color: "var(--color-text)",
  outline: "none",
};

const CAP_LABEL: Record<string, string> = {
  llm: "LLM",
  vision: "视觉",
  sem: "语义",
};

const ROUTE_META: { kind: HubRouteKind; label: string; hint: string }[] = [
  { kind: "llm", label: "LLM 链路", hint: "文本回复主链" },
  { kind: "vision", label: "视觉链路", hint: "图片理解" },
  { kind: "sem", label: "语义链路", hint: "知识库向量化" },
];


export default function ModelHubSection(props: PageProps) {
  const { api, ready, push } = props;
  const qc = useQueryClient();

  const q = useQuery({
    queryKey: ["modelhub"],
    queryFn: () => api.modelhubOverview(),
    enabled: !!ready,
    staleTime: 10_000,
  });

  const providers: HubProvider[] = q.data?.providers || [];
  const models: HubModel[] = q.data?.models || [];
  const routes: Record<HubRouteKind, { models: string[] }> = q.data?.routes ||
    { llm: { models: [] }, vision: { models: [] }, sem: { models: [] } };
  const fallbackId = q.data?.fallback?.model_id || "";
  const consumersMeta = q.data?.consumers_meta || [];
  const consumers: Record<string, { mode: "route" | "fixed"; route?: string; model_id?: string }> =
    q.data?.consumers || {};
  const presets = q.data?.presets || [];
  const maxChain = q.data?.max_chain || 6;

  const refresh = () => qc.invalidateQueries({ queryKey: ["modelhub"] });
  const modelById = (id: string) => models.find((m) => m.id === id);
  const providerById = (id: string) => providers.find((p) => p.id === id);
  const modelLabel = (id: string) => {
    const m = modelById(id);
    if (!m) return "（已删除）";
    const p = providerById(m.provider_id);
    return `${m.model} · ${p ? p.name : "?"}`;
  };

  // ==================== ① 提供商 ====================
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

  // ==================== ② 模型 ====================
  const [newModel, setNewModel] = useState<{ providerId: string; name: string }>(
    { providerId: "", name: "" },
  );
  const addModelMut = useMutation({
    mutationFn: () =>
      api.modelhubAddModel(newModel.providerId, newModel.name.trim()),
    onSuccess: () => {
      push("模型已添加");
      setNewModel({ providerId: "", name: "" });
      refresh();
    },
    onError: (e) => push(`添加失败：${errMsg(e)}`),
  });

  const delModelMut = useMutation({
    mutationFn: (mid: string) => api.modelhubDeleteModel(mid),
    onSuccess: () => {
      push("模型已删除（链路/绑定已自动清理）");
      refresh();
    },
    onError: (e) => push(`删除失败：${errMsg(e)}`),
  });

  const setCapsMut = useMutation({
    mutationFn: ({ mid, caps }: { mid: string; caps: string[] }) =>
      api.modelhubSetCaps(mid, caps),
    onSuccess: () => refresh(),
    onError: (e) => push(`能力修改失败：${errMsg(e)}`),
  });

  // ==================== 链路编辑（本地态，统一保存） ====================
  const [routeDraft, setRouteDraft] = useState<Record<HubRouteKind, string[]> | null>(null);
  const [routePick, setRoutePick] = useState<Record<string, string>>({});
  useEffect(() => {
    if (q.data && routeDraft === null) {
      // routes 形如 { llm: { models: [...] }, vision: {...}, sem: {...} }，
      // 而 routeDraft 的类型是 Record<HubRouteKind, string[]>（只存 id 数组）。
      // 直接 JSON 整树克隆会把「对象」塞进 string[] 槽位 → routeDraft[kind].includes 崩溃 → 整页白屏。
      setRouteDraft({
        llm: [...(routes.llm?.models || [])],
        vision: [...(routes.vision?.models || [])],
        sem: [...(routes.sem?.models || [])],
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q.data]);

  const saveRouteMut = useMutation({
    mutationFn: async () => {
      for (const kind of ["llm", "vision", "sem"] as HubRouteKind[]) {
        const ids: string[] = routeDraft
          ? routeDraft[kind] || []
          : routes[kind]?.models || [];
        await api.modelhubSaveRoute(kind, ids);
      }
    },
    onSuccess: () => {
      push("链路已保存（立即生效）");
      setRouteDraft(null);
      refresh();
    },
    onError: (e) => push(`保存失败：${errMsg(e)}`),
  });

  const routeAdd = (kind: HubRouteKind, mid: string) => {
    if (!mid || !routeDraft) return;
    const cur = routeDraft[kind] || [];
    if (cur.includes(mid)) return;
    if (cur.length >= maxChain) {
      push(`每条链路最多 ${maxChain} 个模型`);
      return;
    }
    setRouteDraft({ ...routeDraft, [kind]: [...cur, mid] });
    setRoutePick({ ...routePick, [kind]: "" });
  };
  const routeRemove = (kind: HubRouteKind, idx: number) => {
    if (!routeDraft) return;
    const cur = [...(routeDraft[kind] || [])];
    cur.splice(idx, 1);
    setRouteDraft({ ...routeDraft, [kind]: cur });
  };
  const routeMove = (kind: HubRouteKind, idx: number, dir: -1 | 1) => {
    if (!routeDraft) return;
    const cur = [...(routeDraft[kind] || [])];
    const j = idx + dir;
    if (j < 0 || j >= cur.length) return;
    [cur[idx], cur[j]] = [cur[j], cur[idx]];
    setRouteDraft({ ...routeDraft, [kind]: cur });
  };

  // ==================== 兜底模型 ====================
  const [fbDraft, setFbDraft] = useState<string>("");
  useEffect(() => {
    if (q.data) setFbDraft(fallbackId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q.data]);
  const fbMut = useMutation({
    mutationFn: (mid: string) => api.modelhubSetFallback(mid),
    onSuccess: () => {
      push("兜底模型已保存");
      refresh();
    },
    onError: (e) => push(`设置失败：${errMsg(e)}`),
  });
  const fbEligible = models.filter(
    (m) => m.caps.includes("llm") && m.caps.includes("vision"),
  );

  // ==================== ③ 消费方绑定 ====================
  type BindDraft = Record<string, { mode: "route" | "fixed"; route: string; model_id: string }>;
  const [bindDraft, setBindDraft] = useState<BindDraft | null>(null);
  useEffect(() => {
    if (q.data && bindDraft === null) {
      const d: BindDraft = {};
      for (const c of consumersMeta) {
        const b = consumers[c.id];
        d[c.id] = {
          mode: b?.mode === "fixed" ? "fixed" : "route",
          route: b?.route || c.suggest_route || "llm",
          model_id: b?.model_id || "",
        };
      }
      setBindDraft(d);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q.data]);

  const bindMut = useMutation({
    mutationFn: async () => {
      const d = bindDraft || {};
      for (const [cid, b] of Object.entries(d)) {
        await api.modelhubSetConsumer(cid, b.mode, b.route, b.model_id);
      }
    },
    onSuccess: () => {
      push("消费方绑定已保存（立即生效）");
      refresh();
    },
    onError: (e) => push(`保存失败：${errMsg(e)}`),
  });

  if (q.isLoading) {
    return <div style={{ padding: 20, color: "var(--color-text-muted)" }}>加载模型中心…</div>;
  }
  if (q.isError) {
    return (
      <div style={{ padding: 20, color: "var(--danger, #c0392b)" }}>
        模型中心加载失败：{errMsg(q.error)}
      </div>
    );
  }

  return (
    <div>
      <div style={{ fontSize: 12, color: "var(--color-text-muted)", marginBottom: 10, lineHeight: 1.6 }}>
        模型配置的<b>唯一入口</b>：先添加提供商并配好密钥（可拉取模型、测试密钥），
        再把模型按 <b>LLM / 视觉 / 语义</b> 分类编成避障链路（每链最多 {maxChain} 个，失败自动切下一个），
        可设一个「LLM+视觉」双能力模型作兜底；消费方选一条链路（享避障）或固定某个模型。
      </div>

      {/* ===== ① 提供商 ===== */}
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

      {/* ===== ② 模型 ===== */}
      <SectionBlock title="② 模型" subtitle={`${models.length} 个 · 能力标签决定可进哪条链`}>
        {models.length === 0 && (
          <div style={{ fontSize: 12, color: "var(--color-text-muted)", marginBottom: 8 }}>
            还没有模型：先在提供商上点「拉取模型」，或在下方手动添加。
          </div>
        )}
        {models.map((m) => {
          const p = providerById(m.provider_id);
          const toggleCap = (cap: string) => {
            const caps = m.caps.includes(cap)
              ? m.caps.filter((c) => c !== cap)
              : [...m.caps, cap];
            if (!caps.length) { push("至少保留一个能力标签"); return; }
            setCapsMut.mutate({ mid: m.id, caps });
          };
          return (
            <div key={m.id} style={{
              display: "flex", alignItems: "center", gap: 10,
              padding: "6px 10px", background: "var(--color-surface-solid)",
              borderRadius: 8, border: "1px solid var(--color-border)",
              marginBottom: 6, fontSize: 12,
            }}>
              <div style={{ flex: 1, minWidth: 140 }}>
                <span style={{ fontWeight: 600 }}>{m.model}</span>
                <span style={{ color: "var(--color-text-muted)", marginLeft: 6, fontSize: 11 }}>
                  {p?.name || "?"}{m.source === "fetch" ? " · 已拉取" : ""}
                </span>
              </div>
              <div style={{ display: "flex", gap: 4 }}>
                {["llm", "vision", "sem"].map((cap) => (
                  <button key={cap}
                    onClick={() => toggleCap(cap)}
                    style={{
                      fontSize: 10.5, padding: "2px 8px", borderRadius: 10,
                      border: "1px solid " + (m.caps.includes(cap) ? "var(--color-accent)" : "var(--color-border)"),
                      background: m.caps.includes(cap) ? "var(--color-accent)" : "transparent",
                      color: m.caps.includes(cap) ? "#fff" : "var(--color-text-muted)",
                      cursor: "pointer",
                    }}
                    title="点击切换该能力标签">
                    {CAP_LABEL[cap]}
                  </button>
                ))}
              </div>
              {fallbackId === m.id && (
                <span style={{ fontSize: 10.5, color: "var(--color-accent)" }}>兜底</span>
              )}
              <button className="inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold transition-[background-color,color,border-color,box-shadow,transform,opacity] duration-200 ease-[var(--ease-spring)] cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-ring)] disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] border border-[color-mix(in_srgb,var(--color-danger)_25%,transparent)] bg-[var(--color-danger-soft)] text-[var(--color-danger)] hover:bg-[var(--color-danger)] hover:text-white h-9 px-4 text-[0.78rem] rounded-[10px]"
                onClick={() => {
                  if (confirm(`删除模型「${m.model}」？链路与绑定会自动清理。`))
                    delModelMut.mutate(m.id);
                }}>删</button>
            </div>
          );
        })}
        <div style={{ display: "flex", gap: 8, marginTop: 8, alignItems: "center" }}>
          <select style={{ ...inputStyle, flex: 1 }} value={newModel.providerId}
            onChange={(e) => setNewModel({ ...newModel, providerId: e.target.value })}>
            <option value="">— 选提供商 —</option>
            {providers.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
          <input style={{ ...inputStyle, flex: 2 }} value={newModel.name}
            placeholder="模型名（手动添加；一般用「拉取模型」）"
            onChange={(e) => setNewModel({ ...newModel, name: e.target.value })} />
          <button className="inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold transition-[background-color,color,border-color,box-shadow,transform,opacity] duration-200 ease-[var(--ease-spring)] cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-ring)] disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] bg-[var(--color-accent)] text-[#08130a] shadow-lg hover:bg-[var(--color-accent-hover)] h-9 px-4 text-[0.78rem] rounded-[10px]" disabled={addModelMut.isPending || !newModel.providerId || !newModel.name.trim()}
            onClick={() => addModelMut.mutate()}>添加模型</button>
        </div>
      </SectionBlock>

      {/* ===== ②·乙 避障链路 ===== */}
      <SectionBlock title="②·乙 避障链路" subtitle={`每链最多 ${maxChain} 个模型 · 按序尝试失败自动切换`}>
        {ROUTE_META.map(({ kind, label, hint }) => {
          const ids: string[] = routeDraft ? (routeDraft[kind] || []) : (routes[kind]?.models || []);
          return (
            <div key={kind} style={{
              padding: "8px 10px", background: "var(--color-surface-solid)",
              borderRadius: 8, border: "1px solid var(--color-border)",
              marginBottom: 8, fontSize: 12,
            }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
                <b>{label}</b>
                <span style={{ color: "var(--color-text-muted)", fontSize: 11 }}>{hint}</span>
                <span style={{ color: "var(--color-text-muted)", fontSize: 11, marginLeft: "auto" }}>
                  {ids.length}/{maxChain}
                  {kind !== "sem" && fallbackId && !ids.includes(fallbackId) && modelById(fallbackId)
                    ? " + 兜底模型" : ""}
                </span>
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 6 }}>
                {ids.map((mid, idx) => (
                  <span key={mid} style={{
                    display: "inline-flex", alignItems: "center", gap: 6,
                    background: "var(--color-surface)", border: "1px solid var(--color-border)",
                    borderRadius: 14, padding: "2px 8px", fontSize: 11.5,
                  }}>
                    <b style={{ color: "var(--color-accent)" }}>{idx + 1}</b>
                    {modelLabel(mid)}
                    <span style={{ display: "inline-flex", gap: 2 }}>
                      <button onClick={() => routeMove(kind, idx, -1)}
                        disabled={idx === 0}
                        style={{ cursor: idx === 0 ? "default" : "pointer", border: "none", background: "none", color: "var(--color-text-muted)" }}>↑</button>
                      <button onClick={() => routeMove(kind, idx, 1)}
                        disabled={idx === ids.length - 1}
                        style={{ cursor: idx === ids.length - 1 ? "default" : "pointer", border: "none", background: "none", color: "var(--color-text-muted)" }}>↓</button>
                      <button onClick={() => routeRemove(kind, idx)}
                        style={{ cursor: "pointer", border: "none", background: "none", color: "var(--danger, #c0392b)" }}>×</button>
                    </span>
                  </span>
                ))}
                {ids.length === 0 && (
                  <span style={{ color: "var(--color-text-muted)", fontSize: 11 }}>空链路 = 该能力走旧配置兜底</span>
                )}
              </div>
              <select style={{ ...inputStyle, maxWidth: 320 }} value={routePick[kind] || ""}
                onChange={(e) => routeAdd(kind, e.target.value)}>
                <option value="">+ 添加模型到该链…</option>
                {models.filter((m) => m.caps.includes(kind)).map((m) => (
                  <option key={m.id} value={m.id}>
                    {m.model} · {providerById(m.provider_id)?.name || "?"}
                  </option>
                ))}
              </select>
            </div>
          );
        })}

        <div style={{
          padding: "8px 10px", background: "var(--color-surface-solid)",
          borderRadius: 8, border: "1px dashed var(--color-accent)",
          marginBottom: 8, fontSize: 12,
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <b>🛟 兜底模型</b>
            <span style={{ color: "var(--color-text-muted)", fontSize: 11 }}>
              须同时支持 LLM+视觉；自动附在 LLM 链与视觉链末尾（语义链不用）
            </span>
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 6, alignItems: "center" }}>
            <select style={{ ...inputStyle, flex: 1 }} value={fbDraft}
              onChange={(e) => setFbDraft(e.target.value)}>
              <option value="">（不设兜底）</option>
              {fbEligible.map((m) => (
                <option key={m.id} value={m.id}>
                  {m.model} · {providerById(m.provider_id)?.name || "?"}
                </option>
              ))}
            </select>
            <button className="inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold transition-[background-color,color,border-color,box-shadow,transform,opacity] duration-200 ease-[var(--ease-spring)] cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-ring)] disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] bg-[var(--color-accent)] text-[#08130a] shadow-lg hover:bg-[var(--color-accent-hover)] h-9 px-4 text-[0.78rem] rounded-[10px]" disabled={fbMut.isPending || fbDraft === fallbackId}
              onClick={() => fbMut.mutate(fbDraft)}>保存兜底</button>
          </div>
          {fbEligible.length === 0 && (
            <div style={{ fontSize: 11, color: "var(--color-text-muted)", marginTop: 4 }}>
              还没有「LLM+视觉」双能力模型：在模型列表把某模型同时点亮这两个标签（如 nemotron-omni / qwen-vl / glm-4v 系）。
            </div>
          )}
        </div>

        <div style={{ display: "flex", justifyContent: "flex-end" }}>
          <button className="inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold transition-[background-color,color,border-color,box-shadow,transform,opacity] duration-200 ease-[var(--ease-spring)] cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-ring)] disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] bg-[var(--color-accent)] text-[#08130a] shadow-lg hover:bg-[var(--color-accent-hover)] h-9 px-4 text-[0.78rem] rounded-[10px]" disabled={saveRouteMut.isPending || routeDraft === null}
            onClick={() => saveRouteMut.mutate()}>
            {saveRouteMut.isPending ? "保存中…" : "保存链路"}
          </button>
        </div>
      </SectionBlock>

      {/* ===== ③ 消费方绑定 ===== */}
      <SectionBlock title="③ 消费方绑定" subtitle="选避障链路（推荐）或固定某提供商的模型">
        {consumersMeta.map((c) => {
          const b = (bindDraft || {})[c.id] ||
            { mode: "route" as const, route: c.suggest_route || "llm", model_id: "" };
          const fixedModel = b.model_id ? modelById(b.model_id) : null;
          return (
            <div key={c.id} style={{
              display: "flex", alignItems: "center", gap: 10,
              padding: "8px 10px", background: "var(--color-surface-solid)",
              borderRadius: 8, border: "1px solid var(--color-border)",
              marginBottom: 8, fontSize: 12, flexWrap: "wrap",
            }}>
              <span style={{ flex: "0 0 200px", fontSize: 12.5 }}>{c.label}</span>
              <select style={{ ...inputStyle, width: 130 }}
                value={b.mode}
                onChange={(e) => {
                  const mode = e.target.value as "route" | "fixed";
                  setBindDraft({ ...(bindDraft || {}), [c.id]: { ...b, mode } });
                }}>
                <option value="route">走避障链路</option>
                <option value="fixed">固定模型</option>
              </select>
              {b.mode === "route" ? (
                <select style={{ ...inputStyle, flex: 1, minWidth: 170 }}
                  value={b.route}
                  onChange={(e) =>
                    setBindDraft({ ...(bindDraft || {}), [c.id]: { ...b, route: e.target.value } })}>
                  {ROUTE_META.map(({ kind, label }) => (
                    <option key={kind} value={kind}>{label}</option>
                  ))}
                </select>
              ) : (
                <select style={{ ...inputStyle, flex: 1, minWidth: 170 }}
                  value={b.model_id}
                  onChange={(e) =>
                    setBindDraft({ ...(bindDraft || {}), [c.id]: { ...b, model_id: e.target.value } })}>
                  <option value="">— 选固定模型 —</option>
                  {models.map((m) => (
                    <option key={m.id} value={m.id}>
                      {m.model} · {providerById(m.provider_id)?.name || "?"}
                    </option>
                  ))}
                </select>
              )}
              <span style={{ flex: "0 0 auto", fontSize: 11, color: "var(--color-text-muted)" }}>
                {b.mode === "route"
                  ? `避障+${b.route !== "sem" ? "兜底" : "无兜底"}`
                  : fixedModel ? "不避障" : "未选模型"}
              </span>
            </div>
          );
        })}
        <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 8 }}>
          <button className="inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold transition-[background-color,color,border-color,box-shadow,transform,opacity] duration-200 ease-[var(--ease-spring)] cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-ring)] disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] bg-[var(--color-accent)] text-[#08130a] shadow-lg hover:bg-[var(--color-accent-hover)] h-9 px-4 text-[0.78rem] rounded-[10px]" disabled={bindMut.isPending || bindDraft === null}
            onClick={() => bindMut.mutate()}>
            {bindMut.isPending ? "保存中…" : "保存绑定"}
          </button>
        </div>
        <div style={{ fontSize: 11, color: "var(--color-text-muted)", marginTop: 6, lineHeight: 1.6 }}>
          规则：走链路的消费方按链序避障（LLM/视觉链末尾自动附加兜底模型）；
          固定模型不避障、失效即解绑回落默认 LLM 链；链路为空时该能力回落旧配置。
        </div>
      </SectionBlock>
    </div>
  );
}
