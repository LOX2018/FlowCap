import { useState } from "react";
import { useRef, useLayoutEffect } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import {
  ArrowLeft, Wrench, FileUp, SearchIcon, RefreshCw, Trash2, RotateCcw,
} from "lucide-react";
import type { ProKbItem, ProKbScanReport, ProKbDupPair } from "../../api/client";
import type { PageProps } from "../../api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
  Row, RowText, Blank, Toolbar, Section,
} from "@/components/page/kit";
import { cn, errMsg } from "@/lib/utils";

export type ProItem = {
  id?: number;
  topic: string;
  category: string;
  content: string;
  summary: string;
  enabled?: boolean;
};

export function ProKb({
  push,
  api,
  qc,
}: {
  push: PageProps["push"];
  api: PageProps["api"];
  qc: ReturnType<typeof useQueryClient>;
}) {
  const list = useQuery({ queryKey: ["ai-pro-kb"], queryFn: api.aiProKbList });
  const [search, setSearch] = useState("");

  const invalidate = () => qc.invalidateQueries({ queryKey: ["ai-pro-kb"] });

  // ---- 单元格直接编辑（改表格 = 改树）----
  const patchMut = useMutation({
    mutationFn: (v: { id: number; patch: Record<string, unknown> }) =>
      api.aiProKbPatch(v.id, v.patch as never),
    onSuccess: (d) => {
      if (!d.ok) push(`保存失败：${d.error}`);
      invalidate();
    },
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
    onError: (e) => {
      push(`提纯失败：${errMsg(e)}`);
      setImpPct(0);
    },
  });

  const confirmMut = useMutation({
    mutationFn: () => api.aiProKbImportConfirm(impPrev as never),
    onSuccess: (d) => {
      if (d.ok) {
        push(`已入库 ${d.added} 条`);
        setImpPrev([]);
        invalidate();
      } else push(`入库失败：${d.error}`);
    },
    onError: (e) => push(`入库失败：${errMsg(e)}`),
  });

  const delMut = useMutation({
    mutationFn: (id: number) => api.aiProKbDelete(id),
    onSuccess: () => {
      push("已移入回收站（30 天内可恢复）");
      invalidate();
    },
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
        // 低价值默认勾选（原策略：低价值才建议清理）
        const p: Record<number, boolean> = {};
        d.report.low_value.forEach((x) => {
          p[x.id] = true;
        });
        setPicked(p);
        push(
          `扫描完成：冷数据 ${d.report.cold.length} · 低价值 ${d.report.low_value.length} · 重复 ${d.report.duplicate.length} 对`,
        );
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
      if (d.ok) {
        push(`已处理 ${d.count} 条 → 回收站`);
        setPicked({});
        setScan(null);
        invalidate();
      } else {
        // 2026-09-17 修补（OCR 审查 HIGH —— 应用层失败被静默吞掉）：
        // 服务端返回 `{ok:false}` 时原来无任何提示，界面停在原状态，
        // 用户以为没点到。与 scanMut/patchMut 的报错行为对齐。
        push(`处理失败：${(d as { error?: string }).error || "未知错误"}`, 8000);
      }
    },
    onError: (e) => push(`处理失败：${errMsg(e)}`),
  });

  const mergeMut = useMutation({
    mutationFn: (pairs: ProKbDupPair[]) => api.aiProKbMaintainMerge(pairs),
    onSuccess: (d) => {
      if (d.ok) {
        push(`已合并 ${d.merged} 组重复`);
        setScan(null);
        invalidate();
      }
    },
    onError: (e) => push(`合并失败：${errMsg(e)}`),
  });

  const loadRecycle = async () => {
    const d = await api.aiProKbRecycle();
    setRecycle(d.items || []);
  };
  const restoreMut = useMutation({
    mutationFn: (id: number) => api.aiProKbRecycleRestore(id),
    onSuccess: (d) => {
      // 2026-09-17 修补（OCR 审查 HIGH —— 无条件报"已恢复"）：
      // 原实现不管 `d.ok` 都提示成功，`ok:false` 时用户被误导（以为恢复成功），
      // 而列表refetch 拿回的是服务端**未变**的数据。
      if (!d.ok) {
        push(`恢复失败：${(d as { error?: string }).error || "未知错误"}`, 8000);
        return;
      }
      push("已恢复");
      setRecycle(d.items || []);
      invalidate();
    },
    onError: (e) => push(`恢复失败：${errMsg(e)}`),
  });

  const items = (list?.data?.items ?? []) as ProKbItem[];
  const tree = list?.data?.tree ?? [];

  // 搜索过滤（前端过滤树）
  const kw = search.trim().toLowerCase();
  const filteredTree = !kw
    ? tree
    : tree
        .map((g) => ({
          ...g,
          children: g.children
            .map((c) => ({
              ...c,
              items: c.items.filter(
                (it) =>
                  (it as ProKbItem & { topic: string }).topic !== undefined &&
                  [it.topic, it.category, it.content, it.summary]
                    .join(" ")
                    .toLowerCase()
                    .includes(kw)
              ),
            }))
            .filter((c) => c.items.length > 0),
        }))
        .filter((g) => g.children.length > 0);

  // 表格行 = 树的展平。同行相邻 equal 值才画「分组边框」，实现 xlsx 式行归类。
  type RowT = ProKbItem & { _groupStart: boolean; _catStart: boolean; _groupSize: number };
  const trows: RowT[] = [];
  filteredTree.forEach((g) => {
    g.children.forEach((c) => {
      c.items.forEach((it, idx) => {
        const prev = trows[trows.length - 1];
        trows.push({
          ...(it as ProKbItem),
          _groupStart: !prev || prev.topic !== it.topic,
          _catStart: idx === 0,
          _groupSize: 1,
        });
      });
    });
  });

  // 同一主题的连续行数：只有「真的成组」（>=2 行）才画主题色分组线。
  // 否则单行组也画线 => 每行都带强调线 = 视觉噪声（2026-09-30 表格可读性修复）。
  const topicCount = new Map<string, number>();
  trows.forEach((r) => topicCount.set(r.topic, (topicCount.get(r.topic) ?? 0) + 1));
  trows.forEach((r) => {
    r._groupSize = topicCount.get(r.topic) ?? 1;
  });

  const staleInfo = (id: number): string => {
    if (!scan) return "";
    if (scan.low_value.some((x) => x.id === id)) return "低价值";
    if (scan.cold.some((x) => x.id === id)) return "冷数据";
    if (scan.duplicate.some((x) => x.drop_id === id)) return "重复";
    return "";
  };

  return (
    <div>
      {/* 2026-09-29：原顶部「思维导图结构：…」说明块已删除（用户要求）——
          该段与下方「导入文件」区重复解释同一套主题树机制，且占据首屏。 */}

      {/* 2026-09-29：原独立「知识维护」工具条已删除 —— 该按钮移入下方
          「导入文件（AI 提纯）」卡片的头部（actions），与「选择文件」并列，
          不再单独占一行右对齐工具条（用户要求）。 */}

      {/* 知识维护面板 */}
      {maintOpen && (
        <Card className="mb-3.5">
          <CardContent className="p-3.5">
            <Toolbar className="mb-2.5">
              <span className="text-[0.84rem] font-bold text-[var(--color-text)]">知识维护</span>
              <span className="min-w-[200px] flex-1 text-[0.7rem] leading-relaxed
                               text-[var(--color-text-muted)]">
                常驻定时器每 {statusQ.data?.state?.interval_hours ?? 84} 小时自动跑一轮
                （自动学习 + 陈旧扫描 + 回收站清理）。扫描
                <b className="text-[var(--color-text-secondary)]">只出报告不动数据</b>，删除必须你确认。
              </span>
              <Button size="sm" onClick={() => scanMut.mutate()} disabled={scanMut.isPending}>
                {scanMut.isPending ? "扫描中…" : "立即扫描"}
              </Button>
            </Toolbar>

            {statusQ.data?.state && (
              <div className="mb-2.5 text-[0.7rem] text-[var(--color-text-muted)]">
                上次运行：
                {statusQ.data.state.last_run_at
                  ? new Date(statusQ.data.state.last_run_at * 1000).toLocaleString()
                  : "尚未运行"}
                {" · "}累计 {statusQ.data.state.runs} 轮
              </div>
            )}

            {/* 扫描报告 */}
            {scan && (
              <div className="border-t border-[var(--color-border)] pt-2.5">
                <div className="mb-2 text-[0.76rem] text-[var(--color-text)]">
                  扫描 {scan.scanned} 条 · 冷数据 {scan.cold.length} · 低价值{" "}
                  {scan.low_value.length} · 重复 {scan.duplicate.length} 对
                </div>

                {scan.low_value.length > 0 && (
                  <div className="mb-2.5">
                    <div className="mb-1.5 text-[0.74rem] font-semibold
                                    text-[var(--color-danger)]">
                      低价值（重要度 &lt; 0.3 且从未命中且超 90 天）— 建议清理
                    </div>
                    {scan.low_value.map((x) => (
                      <label
                        key={x.id}
                        className="flex cursor-pointer items-center gap-2 py-[3px] text-[0.76rem]"
                      >
                        <input
                          type="checkbox"
                          className="h-3.5 w-3.5 accent-[var(--color-accent)]"
                          checked={!!picked[x.id]}
                          onChange={(e) => setPicked({ ...picked, [x.id]: e.target.checked })}
                        />
                        <span className="font-semibold text-[var(--color-text)]">{x.topic}</span>
                        <span className="min-w-0 flex-1 truncate text-[var(--color-text-muted)]">
                          {x.summary || x.category}
                        </span>
                      </label>
                    ))}
                  </div>
                )}

                {scan.cold.length > 0 && (
                  <div className="mb-2.5">
                    <div className="mb-1.5 text-[0.74rem] font-semibold text-[var(--color-text)]">
                      冷数据（从未命中且超 180 天）— 已标记陈旧，可选清理
                    </div>
                    {scan.cold.map((x) => (
                      <label
                        key={x.id}
                        className="flex cursor-pointer items-center gap-2 py-[3px] text-[0.76rem]"
                      >
                        <input
                          type="checkbox"
                          className="h-3.5 w-3.5 accent-[var(--color-accent)]"
                          checked={!!picked[x.id]}
                          onChange={(e) => setPicked({ ...picked, [x.id]: e.target.checked })}
                        />
                        <span className="font-semibold text-[var(--color-text)]">{x.topic}</span>
                        <span className="min-w-0 flex-1 truncate text-[var(--color-text-muted)]">
                          {x.summary || x.category}
                        </span>
                      </label>
                    ))}
                  </div>
                )}

                {scan.duplicate.length > 0 && (
                  <div className="mb-2.5">
                    <div className="mb-1.5 text-[0.74rem] font-semibold text-[var(--color-text)]">
                      重复条目（余弦 ≥ 0.95）— 保留高重要度，另一条合并
                    </div>
                    {scan.duplicate.map((x) => (
                      <div
                        key={`${x.keep_id}-${x.drop_id}`}
                        className="py-[3px] text-[0.76rem] text-[var(--color-text-secondary)]"
                      >
                        保留 <b className="text-[var(--color-text)]">{x.keep_summary}</b>
                        <span className="text-[var(--color-text-muted)]">
                          ，合并 {x.drop_summary}（相似度 {x.similarity.toFixed(3)}）
                        </span>
                      </div>
                    ))}
                    <Button
                      variant="secondary"
                      size="sm"
                      className="mt-1.5"
                      onClick={() => mergeMut.mutate(scan.duplicate)}
                      disabled={mergeMut.isPending}
                    >
                      合并全部重复
                    </Button>
                  </div>
                )}

                <Toolbar className="mt-2.5 border-t border-[var(--color-border)] pt-2.5">
                  <Button
                    variant="danger-outline"
                    size="sm"
                    onClick={() => applyMut.mutate()}
                    disabled={applyMut.isPending || Object.values(picked).every((v) => !v)}
                  >
                    <Trash2 className="h-3.5 w-3.5" />处理选中项 → 回收站
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    onClick={() => {
                      setScan(null);
                      setPicked({});
                    }}
                  >
                    关闭报告
                  </Button>
                </Toolbar>
              </div>
            )}

            {/* 回收站 */}
            <div className="mt-2.5 border-t border-[var(--color-border)] pt-2.5">
              <Toolbar className="mb-1.5">
                <span className="text-[0.76rem] font-semibold text-[var(--color-text)]">
                  回收站（{recycle.length}）
                </span>
                <span className="text-[0.7rem] text-[var(--color-text-muted)]">
                  删除保留 30 天，期间可恢复
                </span>
                <Button variant="ghost" size="sm" onClick={loadRecycle}>
                  <RefreshCw className="h-3 w-3" />刷新
                </Button>
              </Toolbar>
              {recycle.map((it) => (
                <Row key={it.id} className="!px-0 py-1">
                  <RowText primary={it.topic} secondary={it.summary || it.category} />
                  <Button variant="secondary" size="sm" onClick={() => restoreMut.mutate(it.id)}>
                    <RotateCcw className="h-3 w-3" />恢复
                  </Button>
                </Row>
              ))}
              {recycle.length === 0 && (
                <div className="text-[0.7rem] text-[var(--color-text-muted)]">回收站为空</div>
              )}
            </div>
          </CardContent>
        </Card>
      )}

      {/* 文件导入 —— 2026-09-29：「知识维护」按钮移入本卡片头部（actions），
          与「选择文件」并列；内容区只保留**支持的文件类型**一行（用户要求）。
          （此前误把「选择文件」当移动对象搬进内容区，已按用户澄清还原。） */}
      <Section className="mb-3.5" title="导入文件（AI 提纯）" actions={
        <>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setMaintOpen(!maintOpen);
              if (!maintOpen) void loadRecycle();
            }}
          >
            <Wrench className="h-3.5 w-3.5" />知识维护
            {maintOpen ? <ArrowLeft className="h-3 w-3 rotate-90" /> : null}
          </Button>
          <Button
            variant="secondary"
            size="sm"
            onClick={() => fileRef.current?.click()}
            disabled={impMut.isPending}
          >
            <FileUp className="h-3.5 w-3.5" />
            {impMut.isPending ? `提纯中… ${impPct}%` : "选择文件"}
          </Button>
        </>
      }>
        <div className="text-[0.7rem] text-[var(--color-text-muted)]">
          支持 txt / md / docx / xlsx / pdf（图片需先配视觉模型）
        </div>
        <input
          ref={fileRef}
          type="file"
          accept=".txt,.md,.docx,.xlsx,.pdf,.png,.jpg,.jpeg"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) impMut.mutate(f);
            e.target.value = "";
          }}
        />

        {impPrev.length > 0 && (
          <div className="mt-3">
            <div className="mb-2 text-[0.74rem] text-[var(--color-text-muted)]">
              提纯预览：共 {impPrev.length} 条（可编辑后批量入库）
            </div>
            <div className="grid max-h-[340px] gap-2 overflow-auto">
              {impPrev.map((it, i) => (
                <Card key={i}>
                  <CardContent className="p-2.5">
                    <div className="mb-1.5 flex gap-1.5">
                      <Input
                        list="prokb-topics"
                        value={it.topic}
                        onChange={(e) => updPrev(i, { topic: e.target.value })}
                        placeholder="主题"
                        className="flex-1"
                      />
                      <Input
                        value={it.category}
                        onChange={(e) => updPrev(i, { category: e.target.value })}
                        placeholder="子分类"
                        className="flex-1"
                      />
                    </div>
                    <Input
                      value={it.summary}
                      onChange={(e) => updPrev(i, { summary: e.target.value })}
                      placeholder="总结（一句话）"
                      className="mb-1.5"
                    />
                    <Textarea
                      value={it.content}
                      onChange={(e) => updPrev(i, { content: e.target.value })}
                      placeholder="正文内容"
                      rows={3}
                    />
                    <div className="mt-1.5">
                      <Button
                        variant="danger-outline"
                        size="sm"
                        onClick={() => setImpPrev(impPrev.filter((_, k) => k !== i))}
                      >
                        <Trash2 className="h-3 w-3" />移除
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
            <Toolbar className="mt-2.5">
              <Button onClick={() => confirmMut.mutate()} disabled={confirmMut.isPending}>
                确认入库 {impPrev.length} 条
              </Button>
              <Button variant="ghost" onClick={() => setImpPrev([])}>取消</Button>
            </Toolbar>
          </div>
        )}
      </Section>

      {/* 全库主题清单（datalist，导入预览时补全已有主题） */}
      <datalist id="prokb-topics">
        {tree.map((g) => (
          <option key={g.topic} value={g.topic} />
        ))}
      </datalist>

      {/* 搜索 */}
      <Toolbar className="mb-2.5">
        <div className="relative min-w-[220px] flex-1">
          <SearchIcon className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5
                                -translate-y-1/2 text-[var(--color-text-muted)]" />
          <Input
            className="pl-8"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜索主题/分类/内容…"
          />
        </div>
        <Badge variant="outline">共 {items.length} 条 · {tree.length} 个主题</Badge>
      </Toolbar>

      {/* 表格视图（xlsx 式：行归类 / 列分类 / 单元格可编辑）
          2026-09-30 可读性重构（用户：「专业知识库的表格分布太丑了」）
          · 修复前：单元格用 <input> 单行框 —— 主题/子分类/总结超长时横向裁掉且无省略号，
            用户无法区分「本来就短」与「被截断」；表头虽为 6 列却完全没有垂直分隔线。
          · 单元格改为 auto-grow <textarea> + 统一「编辑中才显示框、空闲即无框」外观；
            正文列上限 ~5 行、超出内部滚动；单元格内支持真正的换行。
          · 列宽改为按内容密度分配（正文弹性取得余量），并恢复浅色网格线。 */}
      <Card className="overflow-hidden">
        <div className="overflow-auto">
          <table className="w-full table-fixed border-collapse text-[0.76rem] [--cell-line:var(--color-border)]">
            <colgroup>
              <col style={{ width: 136 }} />
              <col style={{ width: 132 }} />
              <col style={{ width: 240 }} />
              <col />
              <col style={{ width: 84 }} />
              <col style={{ width: 56 }} />
            </colgroup>
            <thead>
              <tr className="sticky top-0 z-[1] bg-[var(--color-surface-solid)]">
                {["主题", "子分类", "总结", "正文", "状态", "操作"].map((h) => (
                  <th
                    key={h}
                    className="whitespace-nowrap border-b border-[var(--cell-line)]
                               px-2.5 py-2 text-left text-[0.7rem] font-semibold
                               text-[var(--color-text-secondary)]"
                  >
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {trows.map((r) => {
                const si = staleInfo(r.id);
                const hits = (r as ProKbItem & { hits?: number }).hits;
                return (
                  <tr
                    key={r.id}
                    className={cn(
                      "border-b border-[var(--cell-line)]",
                      r._groupStart && r._groupSize > 1
                        ? "border-t-2 border-t-[var(--color-accent)]"
                        : ""
                    )}
                  >
                    <td className="align-top">
                      {r._groupStart ? (
                        <AutoGrowCell
                          key={`${r.id}-t`}
                          value={r.topic}
                          className="font-semibold text-[var(--color-text)]"
                          onCommit={(v) => {
                            const t = v.trim();
                            if (t && t !== r.topic) patchMut.mutate({ id: r.id, patch: { topic: t } });
                          }}
                        />
                      ) : (
                        <div className="px-2 py-1.5 text-[var(--color-text-muted)]">〃</div>
                      )}
                    </td>
                    <td className="align-top">
                      {r._catStart ? (
                        <AutoGrowCell
                          key={`${r.id}-c`}
                          value={r.category}
                          onCommit={(v) => {
                            const t = v.trim();
                            if (t !== r.category) patchMut.mutate({ id: r.id, patch: { category: t } });
                          }}
                        />
                      ) : (
                        <div className="px-2 py-1.5 text-[var(--color-text-muted)]">〃</div>
                      )}
                    </td>
                    <td className="align-top">
                      <AutoGrowCell
                        key={`${r.id}-s`}
                        value={r.summary}
                        placeholder="（无总结）"
                        onCommit={(v) => {
                          if (v !== r.summary) patchMut.mutate({ id: r.id, patch: { summary: v } });
                        }}
                      />
                    </td>
                    <td className="align-top">
                      <AutoGrowCell
                        key={`${r.id}-b`}
                        value={r.content}
                        maxRows={5}
                        onCommit={(v) => {
                          if (v !== r.content) patchMut.mutate({ id: r.id, patch: { content: v } });
                        }}
                      />
                    </td>
                    <td className="align-top whitespace-nowrap px-2.5 py-1.5">
                      {si ? (
                        <span
                          className={cn(
                            "text-[0.68rem]",
                            si === "低价值"
                              ? "text-[var(--color-danger)]"
                              : "text-[var(--color-text-muted)]"
                          )}
                        >
                          {si}
                        </span>
                      ) : (
                        <span className="text-[0.68rem] text-[var(--color-text-muted)]">
                          {typeof hits === "number" && hits > 0 ? `命中${hits}` : "正常"}
                        </span>
                      )}
                    </td>
                    <td className="align-top whitespace-nowrap px-1.5 py-1.5">
                      <Button
                        variant="ghost"
                        size="sm"
                        className="h-6 px-1.5 text-[0.68rem] text-[var(--color-danger)]"
                        onClick={() => delMut.mutate(r.id)}
                      >
                        删
                      </Button>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {trows.length === 0 && (
            <Blank>暂无条目。用上方「导入文件」让 AI 提纯入库，或直接点击单元格编辑。</Blank>
          )}
        </div>
        <div className="border-t border-[var(--cell-line)] px-3 py-2 text-[0.66rem]
                        text-[var(--color-text-muted)]">
          单元格直接编辑，失去焦点即保存（改表格 = 改树）。主题列相邻同值自动合并显示（〃），
          成组（同主题 ≥ 2 行）才画分组线。
        </div>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// 表格自适应单元格（2026-09-30 新增）
// ---------------------------------------------------------------------------

/**
 * 表格内可编辑单元格：自动增高，空闲时无框（像表格文本，不像一排表单输入框）。
 *
 * 修复来源：原实现每格一个 <input> —— 单行硬裁 + 无省略号，长中文标题/总结
 * 一被切掉用户就分不清「本来就短」还是「被截断」；表头 6 列却无网格线，
 * 整张表读起来没有列感。改为 textarea 后：① 自动撑高，内容完整可见；
 * ② 单元格内可真的换行；③ 统一「聚焦才显框」的外观，静止时是干净的表格。
 *
 * @param maxRows 超过该行数转为内部滚动（默认 3 行；正文列传 5）。
 *                用「阈值 + scrollHeight 跳变」判断溢出，不做逐像素测量。
 */
function AutoGrowCell({
  value,
  onCommit,
  maxRows = 3,
  placeholder,
  className,
}: {
  value: string;
  onCommit: (v: string) => void;
  maxRows?: number;
  placeholder?: string;
  className?: string;
}) {
  const ref = useRef<HTMLTextAreaElement>(null);
  const [overflowing, setOverflowing] = useState(false);

  // 内容或列宽变化时重算高度；用「多行高度是否超出 maxRows 上限」判断是否溢出。
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    const lh = parseFloat(getComputedStyle(el).lineHeight) || 18;
    const cap = lh * maxRows + 8;
    const over = el.scrollHeight > cap + 1;
    setOverflowing(over);
    el.style.height = `${Math.min(el.scrollHeight, cap)}px`;
  });

  return (
    <textarea
      ref={ref}
      rows={1}
      defaultValue={value}
      placeholder={placeholder}
      onBlur={(e) => {
        onCommit(e.target.value);
        const el = ref.current;
        if (el) el.scrollTop = 0;
      }}
      className={cn(
        "block w-full resize-none rounded-[4px] border border-transparent bg-transparent",
        "px-2 py-1.5 text-[0.76rem] leading-snug text-[var(--color-text)] outline-none",
        "transition-colors placeholder:text-[var(--color-text-muted)]",
        "hover:border-[var(--color-border)] focus:border-[var(--color-accent)]",
        "focus:bg-[var(--color-surface)]",
        overflowing ? "overflow-auto" : "overflow-hidden",
        className
      )}
    />
  );
}

// ---------------------------------------------------------------------------
// ② 对话回复库（命中库）—— 零 token 直接回复 + 自动学习
// ---------------------------------------------------------------------------

