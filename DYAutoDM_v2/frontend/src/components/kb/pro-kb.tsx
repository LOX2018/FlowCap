import { useState } from "react";
import { useRef } from "react";
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
import { cn } from "@/lib/utils";

export type ProItem = {
  id?: number;
  topic: string;
  category: string;
  content: string;
  summary: string;
  enabled?: boolean;
};

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

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
  type RowT = ProKbItem & { _groupStart: boolean; _catStart: boolean };
  const trows: RowT[] = [];
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

  const staleInfo = (id: number): string => {
    if (!scan) return "";
    if (scan.low_value.some((x) => x.id === id)) return "低价值";
    if (scan.cold.some((x) => x.id === id)) return "冷数据";
    if (scan.duplicate.some((x) => x.drop_id === id)) return "重复";
    return "";
  };

  const cellCls =
    "w-full rounded-[6px] border border-[var(--color-border)] bg-[var(--color-surface)] " +
    "px-2 py-1.5 text-[0.76rem] text-[var(--color-text)] outline-none " +
    "focus:border-[var(--color-accent)]";

  return (
    <div>
      <div className="mb-3 text-[0.74rem] leading-relaxed text-[var(--color-text-muted)]">
        思维导图结构：主题 → 子分类 → 正文内容 → 总结（一棵全项目共享的主题树）。作为向量模型的
        前置参考（AI 检索条目<b className="text-[var(--color-text-secondary)]">全文</b>
        生成参考答案），<b className="text-[var(--color-text-secondary)]">不直接回复</b>。
      </div>

      {/* 工具条 */}
      <Toolbar className="mb-3 justify-end">
        <Button
          variant="secondary"
          size="sm"
          onClick={() => {
            setMaintOpen(!maintOpen);
            if (!maintOpen) void loadRecycle();
          }}
        >
          <Wrench className="h-3.5 w-3.5" />知识维护
          {maintOpen ? <ArrowLeft className="h-3 w-3 rotate-90" /> : null}
        </Button>
      </Toolbar>

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

      {/* 文件导入 */}
      <Section className="mb-3.5" title="导入文件（AI 提纯）" actions={
        <Button
          variant="secondary"
          size="sm"
          onClick={() => fileRef.current?.click()}
          disabled={impMut.isPending}
        >
          <FileUp className="h-3.5 w-3.5" />
          {impMut.isPending ? `提纯中… ${impPct}%` : "选择文件"}
        </Button>
      }>
        <div className="text-[0.7rem] leading-relaxed text-[var(--color-text-muted)]">
          支持 txt / md / docx / xlsx / pdf（图片需先配视觉模型）· 自动按「主题 → 子分类 → 正文 → 总结」
          提纯。提纯时会注入现有主题清单，AI 优先复用已有主题（保证全局一棵树），确认后入库。
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

      {/* 表格视图（xlsx 式：行归类 / 列分类 / 单元格可编辑） */}
      <Card className="overflow-hidden">
        <div className="overflow-auto">
          <table className="w-full table-fixed border-collapse text-[0.76rem]">
            <colgroup>
              <col style={{ width: 130 }} />
              <col style={{ width: 110 }} />
              <col style={{ width: 190 }} />
              <col />
              <col style={{ width: 96 }} />
              <col style={{ width: 64 }} />
            </colgroup>
            <thead>
              <tr className="sticky top-0 z-[1] bg-[var(--color-surface-solid)]">
                {["主题", "子分类", "总结", "正文", "状态", "操作"].map((h) => (
                  <th
                    key={h}
                    className="whitespace-nowrap border-b border-[var(--color-border)]
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
                return (
                  <tr key={r.id} className="border-b border-[var(--color-border)]">
                    <td
                      className="p-0"
                      style={{ borderTop: r._groupStart ? "2px solid var(--color-accent)" : undefined }}
                    >
                      {r._groupStart ? (
                        <input
                          className={cn(cellCls, "font-semibold")}
                          defaultValue={r.topic}
                          key={`${r.id}-t`}
                          onBlur={(e) => {
                            const v = e.target.value.trim();
                            if (v && v !== r.topic) patchMut.mutate({ id: r.id, patch: { topic: v } });
                          }}
                        />
                      ) : (
                        <div className="px-2 py-1.5 text-[var(--color-text-muted)]">〃</div>
                      )}
                    </td>
                    <td className="p-0">
                      {r._catStart ? (
                        <input
                          className={cellCls}
                          defaultValue={r.category}
                          key={`${r.id}-c`}
                          onBlur={(e) => {
                            const v = e.target.value.trim();
                            if (v !== r.category) patchMut.mutate({ id: r.id, patch: { category: v } });
                          }}
                        />
                      ) : (
                        <div className="px-2 py-1.5 text-[var(--color-text-muted)]">〃</div>
                      )}
                    </td>
                    <td className="p-0">
                      <input
                        className={cellCls}
                        defaultValue={r.summary}
                        key={`${r.id}-s`}
                        onBlur={(e) => {
                          const v = e.target.value;
                          if (v !== r.summary) patchMut.mutate({ id: r.id, patch: { summary: v } });
                        }}
                      />
                    </td>
                    <td className="p-0">
                      <textarea
                        className={cn(cellCls, "min-h-[34px] resize-y")}
                        defaultValue={r.content}
                        key={`${r.id}-b`}
                        rows={2}
                        onBlur={(e) => {
                          const v = e.target.value;
                          if (v !== r.content) patchMut.mutate({ id: r.id, patch: { content: v } });
                        }}
                      />
                    </td>
                    <td className="px-2.5 py-1.5">
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
                          {typeof (r as ProKbItem & { hits?: number }).hits === "number" &&
                          (r as ProKbItem & { hits?: number }).hits! > 0
                            ? `命中${(r as ProKbItem & { hits?: number }).hits}`
                            : "正常"}
                        </span>
                      )}
                    </td>
                    <td className="whitespace-nowrap px-2 py-1.5">
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
        <div className="border-t border-[var(--color-border)] px-3 py-2 text-[0.66rem]
                        text-[var(--color-text-muted)]">
          单元格直接编辑，失去焦点即保存（改表格 = 改树）。主题列相邻同值自动合并显示（〃）。
        </div>
      </Card>
    </div>
  );
}

// ---------------------------------------------------------------------------
// ② 对话回复库（命中库）—— 零 token 直接回复 + 自动学习
// ---------------------------------------------------------------------------

