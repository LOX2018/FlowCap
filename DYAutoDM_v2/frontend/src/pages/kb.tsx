/**
 * 知识库专用管理页（v0.39.0）
 *
 * 从 AI 页卡片入口进入。两个子页：
 * ① 专业知识库 —— 向量模型的前置参考库（RAG《资料》段），用于分析对方
 *    发送的问题、生成参考答案，不直接回复。含文件导入/AI提纯全流程。
 * ② 对话回复库 —— 命中库：对方消息符合库内案例 → 零 token 直接自动回复。
 *    支持从聊天记录自动总结学习（绑定了 Agent 的账号）。
 */
import { useState, useRef } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { PageProps } from "../api/client";
import type { ProKbItem, ProKbScanReport, ProKbDupPair } from "../api/client";

type SubTab = "pro" | "reply";

const inputStyle: React.CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  background: "var(--surface-2)",
  border: "1px solid var(--border)",
  borderRadius: 8,
  padding: "7px 10px",
  fontSize: 13,
  color: "var(--foreground)",
};

const miniBtn: React.CSSProperties = {
  padding: "6px 12px",
  fontSize: 12.5,
  borderRadius: 8,
  border: "1px solid var(--border)",
  background: "var(--surface-2)",
  cursor: "pointer",
  whiteSpace: "nowrap",
};

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export type ProItem = {
  id?: number;
  topic: string;
  category: string;
  content: string;
  summary: string;
  enabled?: boolean;
};

export default function KbPage({ push, api, setTab }: PageProps) {
  const [sub, setSub] = useState<SubTab>("pro");
  const qc = useQueryClient();

  return (
    <div style={{ padding: "18px 22px", maxWidth: 1080, margin: "0 auto" }}>
      {/* 页头 + 返回 */}
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 14 }}>
        <button onClick={() => setTab?.("ai")} style={miniBtn}>← 返回 AI</button>
        <h2 style={{ margin: 0, fontSize: 18 }}>📚 知识库管理</h2>
      </div>

      {/* 子页切换 */}
      <div style={{ display: "flex", gap: 8, marginBottom: 16 }}>
        {([
          ["pro", "🧠 专业知识库", "向量参考 · 分析问题 · 不直接回复"],
          ["reply", "💬 对话回复库", "案例命中 · 零 token 直接回复"],
        ] as const).map(([id, label, hint]) => (
          <button key={id}
                  onClick={() => setSub(id)}
                  style={{
                    ...miniBtn,
                    padding: "8px 16px",
                    border: sub === id ? "1px solid var(--accent)" : "1px solid var(--border)",
                    background: sub === id ? "var(--accent)" : "var(--surface-2)",
                    color: sub === id ? "#fff" : "var(--foreground)",
                    display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 2,
                  }}>
            <span style={{ fontWeight: 600 }}>{label}</span>
            <span style={{ fontSize: 11, opacity: 0.8 }}>{hint}</span>
          </button>
        ))}
      </div>

      {sub === "pro" ? <ProKb push={push} api={api} qc={qc} /> : <ReplyKb push={push} api={api} qc={qc} />}
    </div>
  );
}

// ---------------------------------------------------------------------------
// ① 专业知识库（向量参考库）—— 复用现有 ai_reply_knowledge_base 存储
// ---------------------------------------------------------------------------

function ProKb({ push, api, qc }: {
  push: PageProps["push"]; api: PageProps["api"]; qc: ReturnType<typeof useQueryClient>;
}) {
  // 思维导图条目：主题 → 子分类 → 正文 → 总结
  // 两个视图共用这一棵树：树视图=按主题折叠的编辑器；表格视图=这棵树的行式展开
  const list = useQuery({ queryKey: ["ai-pro-kb"], queryFn: api.aiProKbList });
  const [search, setSearch] = useState("");

  const invalidate = () => qc.invalidateQueries({ queryKey: ["ai-pro-kb"] });

  // ---- 单元格直接编辑（改表格 = 改树）----
  const patchMut = useMutation({
    mutationFn: (v: { id: number; patch: Record<string, unknown> }) => api.aiProKbPatch(v.id, v.patch as never),
    onSuccess: (d) => { if (!d.ok) push(`保存失败：${d.error}`); invalidate(); },
    onError: (e) => push(`保存失败：${errMsg(e)}`),
  });

  // ---- 文件导入（AI 提纯 → 预览 → 批量入库） ----
  const fileRef = useRef<HTMLInputElement>(null);
  const [impPrev, setImpPrev] = useState<ProItem[]>([]);
  const [impPct, setImpPct] = useState(0);

  const updPrev = (i: number, patch: Partial<ProItem>) =>
    setImpPrev(impPrev.map((it, k) => (k === i ? { ...it, ...patch } : it)));

  const impMut = useMutation({
    mutationFn: (f: File) => api.aiProKbImport(f, setImpPct),
    onSuccess: (d) => {
      if (d.ok) {
        setImpPrev((d.items || []) as ProItem[]);
        push(d.items?.length ? `提纯完成，共 ${d.items.length} 条待确认` : "未提取到可用条目");
      } else push(`提纯失败：${d.error}`);
      setImpPct(0);
    },
    onError: (e) => { push(`提纯失败：${errMsg(e)}`); setImpPct(0); },
  });

  const confirmMut = useMutation({
    mutationFn: () => api.aiProKbImportConfirm(impPrev as never),
    onSuccess: (d) => {
      if (d.ok) { push(`已入库 ${d.added} 条`); setImpPrev([]); invalidate(); }
      else push(`入库失败：${d.error}`);
    },
    onError: (e) => push(`入库失败：${errMsg(e)}`),
  });

  const delMut = useMutation({
    mutationFn: (id: number) => api.aiProKbDelete(id),
    onSuccess: () => { push("已移入回收站（30 天内可恢复）"); invalidate(); },
    onError: (e) => push(`删除失败：${errMsg(e)}`),
  });

  // ---- 知识维护（扫描只出报告，删除必须人工确认）----
  const [scan, setScan] = useState<ProKbScanReport | null>(null);
  const [picked, setPicked] = useState<Record<number, boolean>>({});
  const [maintOpen, setMaintOpen] = useState(false);
  const [recycle, setRecycle] = useState<ProKbItem[]>([]);

  const statusQ = useQuery({
    queryKey: ["ai-pro-kb-maint"],
    queryFn: api.aiProKbMaintainStatus,
    enabled: maintOpen,
  });

  const scanMut = useMutation({
    mutationFn: () => api.aiProKbMaintainScan(),
    onSuccess: (d) => {
      if (d.ok && d.report) {
        setScan(d.report);
        // 低价值默认勾选（MalogBot 原策略：低价值才建议清理）
        const p: Record<number, boolean> = {};
        d.report.low_value.forEach((x) => { p[x.id] = true; });
        setPicked(p);
        push(`扫描完成：冷数据 ${d.report.cold.length} · 低价值 ${d.report.low_value.length} · 重复 ${d.report.duplicate.length} 对`);
      } else push(`扫描失败：${d.error}`);
      qc.invalidateQueries({ queryKey: ["ai-pro-kb-maint"] });
    },
    onError: (e) => push(`扫描失败：${errMsg(e)}`),
  });

  const applyMut = useMutation({
    mutationFn: () => {
      const ids = Object.entries(picked).filter(([, v]) => v).map(([k]) => Number(k));
      return api.aiProKbMaintainApply(ids, "recycle");
    },
    onSuccess: (d) => {
      if (d.ok) { push(`已处理 ${d.count} 条 → 回收站`); setPicked({}); setScan(null); invalidate(); }
    },
    onError: (e) => push(`处理失败：${errMsg(e)}`),
  });

  const mergeMut = useMutation({
    mutationFn: (pairs: ProKbDupPair[]) => api.aiProKbMaintainMerge(pairs),
    onSuccess: (d) => {
      if (d.ok) { push(`已合并 ${d.merged} 组重复`); setScan(null); invalidate(); }
    },
    onError: (e) => push(`合并失败：${errMsg(e)}`),
  });

  const loadRecycle = async () => {
    const d = await api.aiProKbRecycle();
    setRecycle(d.items || []);
  };
  const restoreMut = useMutation({
    mutationFn: (id: number) => api.aiProKbRecycleRestore(id),
    onSuccess: (d) => { push("已恢复"); setRecycle(d.items || []); invalidate(); },
    onError: (e) => push(`恢复失败：${errMsg(e)}`),
  });

  const items = (list?.data?.items ?? []) as ProKbItem[];
  const tree = list?.data?.tree ?? [];

  // 搜索过滤（前端过滤树）
  const kw = search.trim().toLowerCase();
  const filteredTree = !kw ? tree : tree
    .map((g) => ({
      ...g,
      children: g.children
        .map((c) => ({
          ...c,
          items: c.items.filter((it) => (it as ProKbItem & { topic: string }).topic !== undefined &&
            [it.topic, it.category, it.content, it.summary]
              .join(" ").toLowerCase().includes(kw)),
        }))
        .filter((c) => c.items.length > 0),
    }))
    .filter((g) => g.children.length > 0);

  // 表格行 = 树的展平。同行相邻 equal 值才画「分组边框」，实现 xlsx 式行归类。
  type Row = ProKbItem & { _groupStart: boolean; _catStart: boolean };
  const trows: Row[] = [];
  filteredTree.forEach((g) => {
    g.children.forEach((c) => {
      c.items.forEach((it, idx) => {
        const prev = trows[trows.length - 1];
        trows.push({
          ...(it as ProKbItem),
          _groupStart: !prev || prev.topic !== it.topic,
          _catStart: idx === 0,
        });
      });
    });
  });

  const staleIds = new Set<number>();
  if (scan) {
    scan.cold.forEach((x) => staleIds.add(x.id));
    scan.low_value.forEach((x) => staleIds.add(x.id));
    scan.duplicate.forEach((x) => staleIds.add(x.drop_id));
  }
  const staleInfo = (id: number): string => {
    if (!scan) return "";
    if (scan.low_value.some((x) => x.id === id)) return "低价值";
    if (scan.cold.some((x) => x.id === id)) return "冷数据";
    if (scan.duplicate.some((x) => x.drop_id === id)) return "重复";
    return "";
  };

  const cellStyle: React.CSSProperties = {
    ...inputStyle,
    padding: "5px 8px",
    fontSize: 12.5,
    borderRadius: 6,
  };

  return (
    <div>
      <div style={{ fontSize: 12.5, color: "var(--muted-foreground)", marginBottom: 12 }}>
        思维导图结构：主题 → 子分类 → 正文内容 → 总结（一棵全项目共享的主题树）。
        作为向量模型的前置参考（AI 检索条目<b>全文</b>生成参考答案），<b>不直接回复</b>。
      </div>

      {/* 工具条 */}
      <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 12, flexWrap: "wrap" }}>
        <button onClick={() => { setMaintOpen(!maintOpen); if (!maintOpen) { void loadRecycle(); } }}
                style={{ ...miniBtn, marginLeft: "auto" }}>
          🧹 知识维护
        </button>
      </div>

      {/* 知识维护面板 */}
      {maintOpen && (
        <div style={{ border: "1px solid var(--border)", borderRadius: 10, padding: "12px 14px", marginBottom: 14,
                      background: "var(--surface-2)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", marginBottom: 10 }}>
            <span style={{ fontSize: 13, fontWeight: 700 }}>知识维护</span>
            <span style={{ fontSize: 11.5, color: "var(--muted-foreground)" }}>
              常驻定时器每 {statusQ.data?.state?.interval_hours ?? 84} 小时自动跑一轮（自动学习 + 陈旧扫描 + 回收站清理）。
              扫描<b>只出报告不动数据</b>，删除必须你确认。
            </span>
            <button onClick={() => scanMut.mutate()} style={miniBtn} disabled={scanMut.isPending}>
              {scanMut.isPending ? "扫描中…" : "立即扫描"}
            </button>
          </div>

          {statusQ.data?.state && (
            <div style={{ fontSize: 11.5, color: "var(--muted-foreground)", marginBottom: 10 }}>
              上次运行：{statusQ.data.state.last_run_at
                ? new Date(statusQ.data.state.last_run_at * 1000).toLocaleString()
                : "尚未运行"}
              {" · "}累计 {statusQ.data.state.runs} 轮
            </div>
          )}

          {/* 扫描报告 */}
          {scan && (
            <div style={{ borderTop: "1px solid var(--border)", paddingTop: 10 }}>
              <div style={{ fontSize: 12.5, marginBottom: 8 }}>
                扫描 {scan.scanned} 条 · 冷数据 {scan.cold.length} · 低价值 {scan.low_value.length} · 重复 {scan.duplicate.length} 对
              </div>

              {scan.low_value.length > 0 && (
                <div style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6, color: "var(--danger)" }}>
                    低价值（重要度 &lt; 0.3 且从未命中且超 90 天）— 建议清理
                  </div>
                  {scan.low_value.map((x) => (
                    <label key={x.id} style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12.5, padding: "3px 0" }}>
                      <input type="checkbox" checked={!!picked[x.id]}
                             onChange={(e) => setPicked({ ...picked, [x.id]: e.target.checked })} />
                      <span style={{ fontWeight: 600 }}>{x.topic}</span>
                      <span style={{ color: "var(--muted-foreground)", flex: 1, overflow: "hidden",
                                     textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {x.summary || x.category}
                      </span>
                    </label>
                  ))}
                </div>
              )}

              {scan.cold.length > 0 && (
                <div style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
                    冷数据（从未命中且超 180 天）— 已标记陈旧，可选清理
                  </div>
                  {scan.cold.map((x) => (
                    <label key={x.id} style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12.5, padding: "3px 0" }}>
                      <input type="checkbox" checked={!!picked[x.id]}
                             onChange={(e) => setPicked({ ...picked, [x.id]: e.target.checked })} />
                      <span style={{ fontWeight: 600 }}>{x.topic}</span>
                      <span style={{ color: "var(--muted-foreground)", flex: 1, overflow: "hidden",
                                     textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {x.summary || x.category}
                      </span>
                    </label>
                  ))}
                </div>
              )}

              {scan.duplicate.length > 0 && (
                <div style={{ marginBottom: 10 }}>
                  <div style={{ fontSize: 12, fontWeight: 600, marginBottom: 6 }}>
                    重复条目（余弦 ≥ 0.95）— 保留高重要度，另一条合并
                  </div>
                  {scan.duplicate.map((x) => (
                    <div key={`${x.keep_id}-${x.drop_id}`} style={{ fontSize: 12.5, padding: "3px 0" }}>
                      保留 <b>{x.keep_summary}</b>
                      <span style={{ color: "var(--muted-foreground)" }}>
                        ，合并 {x.drop_summary}（相似度 {x.similarity.toFixed(3)}）
                      </span>
                    </div>
                  ))}
                  <button onClick={() => mergeMut.mutate(scan.duplicate)} style={{ ...miniBtn, marginTop: 6 }}
                          disabled={mergeMut.isPending}>
                    合并全部重复
                  </button>
                </div>
              )}

              <div style={{ display: "flex", gap: 8, marginTop: 10, borderTop: "1px solid var(--border)", paddingTop: 10 }}>
                <button onClick={() => applyMut.mutate()} style={{ ...miniBtn, color: "var(--danger)" }}
                        disabled={applyMut.isPending || Object.values(picked).every((v) => !v)}>
                  处理选中项 → 回收站
                </button>
                <button onClick={() => { setScan(null); setPicked({}); }} style={miniBtn}>关闭报告</button>
              </div>
            </div>
          )}

          {/* 回收站 */}
          <div style={{ borderTop: "1px solid var(--border)", marginTop: 10, paddingTop: 10 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6 }}>
              <span style={{ fontSize: 12.5, fontWeight: 600 }}>回收站（{recycle.length}）</span>
              <span style={{ fontSize: 11.5, color: "var(--muted-foreground)" }}>删除保留 30 天，期间可恢复</span>
              <button onClick={loadRecycle} style={miniBtn}>刷新</button>
            </div>
            {recycle.map((it) => (
              <div key={it.id} style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 12.5, padding: "3px 0" }}>
                <span style={{ fontWeight: 600 }}>{it.topic}</span>
                <span style={{ color: "var(--muted-foreground)", flex: 1, overflow: "hidden",
                               textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {it.summary || it.category}
                </span>
                <button onClick={() => restoreMut.mutate(it.id)} style={miniBtn}>恢复</button>
              </div>
            ))}
            {recycle.length === 0 && (
              <div style={{ fontSize: 11.5, color: "var(--muted-foreground)" }}>回收站为空</div>
            )}
          </div>
        </div>
      )}

      {/* 文件导入（AI 提纯 → 思维导图条目预览 → 批量入库） */}
      <div style={{ border: "1px solid var(--border)", borderRadius: 10, padding: "12px 14px", marginBottom: 14 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <button onClick={() => fileRef.current?.click()} style={miniBtn} disabled={impMut.isPending}>
            {impMut.isPending ? `提纯中… ${impPct}%` : "📎 导入文件（AI 提纯）"}
          </button>
          <span style={{ fontSize: 11.5, color: "var(--muted-foreground)", flex: 1, minWidth: 200 }}>
            支持 txt / md / docx / xlsx / pdf（图片需先配视觉模型）· 自动按「主题 → 子分类 → 正文 → 总结」提纯。
            提纯时会注入现有主题清单，AI 优先复用已有主题（保证全局一棵树），确认后入库。
          </span>
          <input
            ref={fileRef}
            type="file"
            accept=".txt,.md,.docx,.xlsx,.pdf,.png,.jpg,.jpeg"
            style={{ display: "none" }}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) impMut.mutate(f);
              e.target.value = "";
            }}
          />
        </div>

        {impPrev.length > 0 && (
          <div style={{ marginTop: 12 }}>
            <div style={{ fontSize: 12.5, marginBottom: 8, color: "var(--muted-foreground)" }}>
              提纯预览：共 {impPrev.length} 条（可编辑后批量入库）
            </div>
            <div style={{ display: "grid", gap: 8, maxHeight: 340, overflow: "auto" }}>
              {impPrev.map((it, i) => (
                <div key={i} style={{ border: "1px solid var(--border)", borderRadius: 8, padding: 8 }}>
                  <div style={{ display: "flex", gap: 6, marginBottom: 6 }}>
                    <input list="prokb-topics" value={it.topic} onChange={(e) => updPrev(i, { topic: e.target.value })}
                           placeholder="主题" style={{ ...inputStyle, flex: 1 }} />
                    <input value={it.category} onChange={(e) => updPrev(i, { category: e.target.value })}
                           placeholder="子分类" style={{ ...inputStyle, flex: 1 }} />
                  </div>
                  <input value={it.summary} onChange={(e) => updPrev(i, { summary: e.target.value })}
                         placeholder="总结（一句话）" style={{ ...inputStyle, marginBottom: 6 }} />
                  <textarea value={it.content} onChange={(e) => updPrev(i, { content: e.target.value })}
                            placeholder="正文内容" rows={3}
                            style={{ ...inputStyle, resize: "vertical", fontFamily: "inherit" }} />
                  <div style={{ display: "flex", gap: 8, marginTop: 6 }}>
                    <button onClick={() => setImpPrev(impPrev.filter((_, k) => k !== i))}
                            style={{ ...miniBtn, color: "var(--danger)" }}>移除</button>
                  </div>
                </div>
              ))}
            </div>
            <div style={{ display: "flex", gap: 8, marginTop: 10 }}>
              <button onClick={() => confirmMut.mutate()} style={miniBtn} disabled={confirmMut.isPending}>
                确认入库 {impPrev.length} 条
              </button>
              <button onClick={() => setImpPrev([])} style={miniBtn}>取消</button>
            </div>
          </div>
        )}
      </div>

      {/* 全库主题清单（datalist，导入预览时自动补全已有主题 —— 统一树的关键） */}
      <datalist id="prokb-topics">
        {tree.map((g) => <option key={g.topic} value={g.topic} />)}
      </datalist>

      {/* 搜索 */}
      <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
        <input style={{ ...inputStyle, flex: 1 }} value={search}
               onChange={(e) => setSearch(e.target.value)} placeholder="🔍 搜索主题/分类/内容…" />
        <span style={{ fontSize: 12, color: "var(--muted-foreground)", alignSelf: "center", flexShrink: 0 }}>
          共 {items.length} 条 · {tree.length} 个主题
        </span>
      </div>

      {/* ================= 表格视图（xlsx 式：行归类 / 列分类 / 单元格可编辑）================= */}
      <div style={{ overflow: "auto", border: "1px solid var(--border)", borderRadius: 10 }}>
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12.5, tableLayout: "fixed" }}>
            <colgroup>
              <col style={{ width: 130 }} />
              <col style={{ width: 110 }} />
              <col style={{ width: 190 }} />
              <col />
              <col style={{ width: 96 }} />
              <col style={{ width: 64 }} />
            </colgroup>
            <thead>
              <tr style={{ background: "var(--surface-2)", position: "sticky", top: 0, zIndex: 1 }}>
                {["主题", "子分类", "总结", "正文", "状态", "操作"].map((h) => (
                  <th key={h} style={{ textAlign: "left", padding: "8px 10px", borderBottom: "1px solid var(--border)",
                                       fontWeight: 600, whiteSpace: "nowrap" }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {trows.map((r) => {
                const si = staleInfo(r.id);
                return (
                  <tr key={r.id} style={{ borderBottom: "1px solid var(--border)" }}>
                    <td style={{ padding: 0, borderTop: r._groupStart ? "2px solid var(--accent)" : undefined }}>
                      {r._groupStart
                        ? <input style={{ ...cellStyle, fontWeight: 600 }}
                                 defaultValue={r.topic} key={`${r.id}-t`}
                                 onBlur={(e) => {
                                   const v = e.target.value.trim();
                                   if (v && v !== r.topic) patchMut.mutate({ id: r.id, patch: { topic: v } });
                                 }} />
                        : <div style={{ padding: "5px 8px", color: "var(--muted-foreground)" }}>〃</div>}
                    </td>
                    <td style={{ padding: 0 }}>
                      {r._catStart
                        ? <input style={cellStyle} defaultValue={r.category} key={`${r.id}-c`}
                                 onBlur={(e) => {
                                   const v = e.target.value.trim();
                                   if (v !== r.category) patchMut.mutate({ id: r.id, patch: { category: v } });
                                 }} />
                        : <div style={{ padding: "5px 8px", color: "var(--muted-foreground)" }}>〃</div>}
                    </td>
                    <td style={{ padding: 0 }}>
                      <input style={cellStyle} defaultValue={r.summary} key={`${r.id}-s`}
                             onBlur={(e) => {
                               const v = e.target.value;
                               if (v !== r.summary) patchMut.mutate({ id: r.id, patch: { summary: v } });
                             }} />
                    </td>
                    <td style={{ padding: 0 }}>
                      <textarea style={{ ...cellStyle, resize: "vertical", fontFamily: "inherit", minHeight: 34 }}
                                defaultValue={r.content} key={`${r.id}-b`} rows={2}
                                onBlur={(e) => {
                                  const v = e.target.value;
                                  if (v !== r.content) patchMut.mutate({ id: r.id, patch: { content: v } });
                                }} />
                    </td>
                    <td style={{ padding: "5px 10px" }}>
                      {si
                        ? <span style={{ fontSize: 11, color: si === "低价值" ? "var(--danger)" : "var(--muted-foreground)" }}>{si}</span>
                        : <span style={{ fontSize: 11, color: "var(--muted-foreground)" }}>
                            {typeof r.hits === "number" && r.hits > 0 ? `命中${r.hits}` : "正常"}
                          </span>}
                    </td>
                    <td style={{ padding: "5px 8px", whiteSpace: "nowrap" }}>
                      <button onClick={() => delMut.mutate(r.id)} style={{ ...miniBtn, padding: "2px 7px", fontSize: 11, color: "var(--danger)" }}>
                        删
                      </button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {trows.length === 0 && (
            <div style={{ fontSize: 12.5, color: "var(--muted-foreground)", padding: "12px 14px" }}>
              暂无条目。用上方「导入文件」让 AI 提纯入库，或直接点击单元格编辑。
            </div>
          )}
          <div style={{ fontSize: 11, color: "var(--muted-foreground)", padding: "8px 12px",
                        borderTop: "1px solid var(--border)" }}>
            单元格直接编辑，失去焦点即保存（改表格 = 改树）。主题列相邻同值自动合并显示（〃）。
          </div>
        </div>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ② 对话回复库（命中库）—— 零 token 直接回复 + 自动学习
// ---------------------------------------------------------------------------

function ReplyKb({ push, api, qc }: {
  push: PageProps["push"]; api: PageProps["api"]; qc: ReturnType<typeof useQueryClient>;
}) {
  const list = useQuery({ queryKey: ["ai-reply-kb"], queryFn: api.aiReplyKbList });
  const [q, setQ] = useState("");
  const [a, setA] = useState("");
  const [editId, setEditId] = useState<number | null>(null);
  const [learning, setLearning] = useState(false);

  const invalidate = () => qc.invalidateQueries({ queryKey: ["ai-reply-kb"] });

  const saveMut = useMutation({
    mutationFn: () => api.aiReplyKbSave(editId ? { id: editId, question: q, answer: a } : { question: q, answer: a }),
    onSuccess: (d) => {
      if (d.ok) { push(editId ? "已更新" : "话术已添加"); setEditId(null); setQ(""); setA(""); invalidate(); }
      else push(`保存失败：${d.error}`);
    },
    onError: (e) => push(`保存失败：${errMsg(e)}`),
  });

  const delMut = useMutation({
    mutationFn: (id: number) => api.aiReplyKbDelete(id),
    onSuccess: () => { push("已删除"); invalidate(); },
    onError: (e) => push(`删除失败：${errMsg(e)}`),
  });

  const toggleMut = useMutation({
    mutationFn: (it: { id: number; question: string; answer: string; enabled: boolean }) =>
      api.aiReplyKbSave(it),
    onSuccess: () => invalidate(),
    onError: (e) => push(`操作失败：${errMsg(e)}`),
  });

  const learnMut = useMutation({
    mutationFn: () => api.aiReplyKbLearn(),
    onSuccess: (d) => {
      setLearning(false);
      if (d.ok) push(`学习完成：扫描 ${d.scanned} 会话，提取 ${d.extracted} 对，新增 ${d.added} 条`);
      else push(`学习失败：${d.error}`);
      invalidate();
    },
    onError: (e) => { setLearning(false); push(`学习失败：${errMsg(e)}`); },
  });

  const items = list?.data?.items ?? [];

  return (
    <div>
      <div style={{ fontSize: 12.5, color: "var(--muted-foreground)", marginBottom: 12 }}>
        对方发送的信息符合库内案例时<b>直接自动回复，不消耗 token</b>。优先级最高，
        在专业知识库与 AI 生成之前。
      </div>

      {/* 自动学习 */}
      <div style={{ display: "flex", gap: 10, alignItems: "center", marginBottom: 14,
                    border: "1px dashed var(--border)", borderRadius: 10, padding: "10px 12px", flexWrap: "wrap" }}>
        <button onClick={() => { setLearning(true); learnMut.mutate(); }} style={miniBtn} disabled={learning}>
          {learning ? "⏳ 学习中…" : "🤖 从聊天记录自动学习"}
        </button>
        <span style={{ fontSize: 12, color: "var(--muted-foreground)" }}>
          扫描数据库中绑定 Agent 账号的私信会话（WS 长连接落库记录），总结有效问答对自动入库（source=auto，可逐条删改关）
        </span>
      </div>

      {/* 添加/编辑 */}
      <div style={{ display: "flex", gap: 8, marginBottom: 10 }}>
        <input style={{ ...inputStyle, flex: 1 }} value={q}
               onChange={(e) => setQ(e.target.value)} placeholder="客户问法案例…（例：你们这个啥价格）" />
        <input style={{ ...inputStyle, flex: 1 }} value={a}
               onChange={(e) => setA(e.target.value)} placeholder="命中的自动回复话术…" />
        <button onClick={() => saveMut.mutate()} style={miniBtn} disabled={!q && !a}>
          {editId ? "更新" : "添加"}
        </button>
        {editId !== null && (
          <button onClick={() => { setEditId(null); setQ(""); setA(""); }} style={miniBtn}>取消</button>
        )}
      </div>

      {/* 列表 */}
      {items.map((it) => (
        <div key={it.id}
             style={{ display: "flex", gap: 8, alignItems: "center", padding: "7px 0",
                      borderBottom: "1px solid var(--border)", fontSize: 12.5 }}>
          <input type="checkbox" checked={it.enabled}
                 onChange={() => toggleMut.mutate({ id: it.id, question: it.question, answer: it.answer, enabled: !it.enabled })} />
          <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                         opacity: it.enabled ? 1 : 0.45 }}>
            <b>Q:</b> {it.question}
          </span>
          <span style={{ flex: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap",
                         color: "var(--muted-foreground)", opacity: it.enabled ? 1 : 0.45 }}>
            <b>A:</b> {it.answer}
          </span>
          <span style={{ fontSize: 11, color: it.source === "auto" ? "var(--accent)" : "var(--muted-foreground)", flexShrink: 0 }}>
            {it.source === "auto" ? "自动学" : "手动"} · 命中{it.hits}
          </span>
          <button onClick={() => { setEditId(it.id); setQ(it.question); setA(it.answer); }} style={miniBtn}>编辑</button>
          <button onClick={() => delMut.mutate(it.id)} style={{ ...miniBtn, color: "var(--danger)" }}>删除</button>
        </div>
      ))}
      {items.length === 0 && (
        <div style={{ fontSize: 12.5, color: "var(--muted-foreground)", padding: "12px 0" }}>
          暂无话术。点「从聊天记录自动学习」或手动添加。
        </div>
      )}
    </div>
  );
}
