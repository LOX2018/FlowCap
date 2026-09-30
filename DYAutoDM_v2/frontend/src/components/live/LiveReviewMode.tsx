import { Fragment, useState, useMemo } from "react";
import { motion } from "framer-motion";
import { Download, ArrowUpDown } from "lucide-react";
import { Avatar, hue, tick } from "../../components/ui";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from "@/components/ui/select";
import {
  Tone, Blank, Toolbar,
} from "@/components/page/kit";
import { cn } from "@/lib/utils";
import {
  Row, DmStatus, DM_META, Th, Td, failInfoOf, FailReasonModal,
  sourceMetaOf, dmTitle, displayStatus, dmFailReason, dmPreviewText,
} from "./live-shared";

export { errMsg } from "@/lib/utils";

interface ReviewModeProps {
  rows: Row[];
  onClose: () => void;
  push: (msg: string, holdMs?: number) => void;
  sendDm: (r: Row) => void;
  goMsg?: (name: string, text?: string) => void;
}

export function ReviewMode({ rows, onClose, push, sendDm, goMsg }: ReviewModeProps) {
  const [q, setQ] = useState("");
  const [st, setSt] = useState<"all" | DmStatus>("all");
  const [asc, setAsc] = useState(false);
  const [exp, setExp] = useState<number | null>(null);
  /** 2026-09-08：私信发送失败详情弹窗（点行内失败提示打开） */
  const [failRow, setFailRow] = useState<Row | null>(null);

  const filtered = useMemo(() => {
    let list = rows.slice();
    if (st !== "all") list = list.filter((r) => r.dmStatus === st);
    if (q.trim()) {
      const kw = q.trim();
      list = list.filter((r) => (r.name + r.content + r.dmText).includes(kw));
    }
    list.sort((a, b) => (asc ? a.ts - b.ts : b.ts - a.ts));
    return list;
  }, [rows, q, st, asc]);

  const cnt = (s: DmStatus): number => rows.filter((r) => r.dmStatus === s).length;

  const exportCSV = () => {
    const head = ["发送时间", "发言人", "用户等级", "评论内容", "私信状态", "私信文案", "私信时间"];
    const body = filtered.map((r) => [
      r.time,
      r.name,
      r.lv,
      r.content,
      DM_META[r.dmStatus][0],
      r.dmText || "",
      r.dmTime || "",
    ]);
    const csv = [head, ...body]
      .map((l) => l.map((c) => '"' + String(c).replace(/"/g, '""') + '"').join(","))
      .join("\n");
    const blob = new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "live_comments_" + tick().replace(/:/g, "") + ".csv";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    push("已导出 CSV · " + a.download);
  };

  const pills: [string, string, number][] = [
    ["all", "全部", rows.length],
    ["un", "未私信", cnt("un")],
    ["wait", "待发送", cnt("wait")],
    ["sent", "已发送", cnt("sent")],
    ["fail", "发送失败", cnt("fail")],
  ];

  return (
    <motion.div
      className="fixed inset-0 z-[60] flex flex-col bg-[var(--color-background)]"
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.18 }}
      data-od-id="live-review"
    >
      <div className="flex shrink-0 flex-wrap items-center gap-3 border-b
                      border-[var(--color-border)] bg-[var(--color-surface)] px-5 py-3.5">
        <Button variant="ghost" size="sm" data-od-id="review-back" onClick={onClose}>
          ‹ 返回实时流
        </Button>
        <h2 className="text-[0.95rem] font-semibold text-[var(--color-text)]">评论查阅模式</h2>
        <div className="min-w-0 flex-1" />
        <Badge variant="outline">只读 · 实时入库</Badge>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        {/* 查阅模式是全屏 overlay，独立于 PageContainer；其内容宽度须与
            PageContainer 的默认 maxWidth 保持一致（否则此页的留白与其它页不一致）。 */}
        <div className="mx-auto w-full max-w-[1180px] px-5 pb-10 pt-4.5">
          <Toolbar className="mb-3">
            <Input
              className="min-w-[200px] flex-1"
              placeholder="搜索昵称 / 评论内容 / 私信文案…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            <Select
              value={st}
              onValueChange={(v) => setSt(v as "all" | DmStatus)}
            >
              <SelectTrigger className="h-8 w-[124px]" aria-label="私信状态筛选">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部状态</SelectItem>
                <SelectItem value="un">未私信</SelectItem>
                <SelectItem value="wait">待发送</SelectItem>
                <SelectItem value="sent">已发送</SelectItem>
                <SelectItem value="fail">发送失败</SelectItem>
              </SelectContent>
            </Select>
            <Button variant="ghost" size="sm" onClick={() => setAsc((s) => !s)}>
              <ArrowUpDown className="h-3.5 w-3.5" />
              {asc ? "时间 ↑" : "时间 ↓"}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setSt("all");
                setQ("");
              }}
            >
              重置
            </Button>
            <Button size="sm" data-od-id="review-export" onClick={exportCSV}>
              <Download className="h-3.5 w-3.5" />导出 CSV
            </Button>
          </Toolbar>

          <div className="mb-2.5 flex flex-wrap items-center gap-2.5 text-[0.75rem]
                          text-[var(--color-text-muted)]">
            <span>
              共 <b className="font-mono font-semibold text-[var(--color-text)]">
                {filtered.length}
              </b> 条
            </span>
            <div className="flex flex-wrap gap-1.5">
              {pills.map(([id, l, c]) => {
                const on = st === id;
                return (
                  <button
                    key={id}
                    type="button"
                    onClick={() => setSt(id as "all" | DmStatus)}
                    className={cn(
                      "inline-flex cursor-pointer items-center gap-1.5 rounded-full border px-2.5 py-1",
                      "text-[0.75rem] transition-colors duration-200",
                      on
                        ? "border-[var(--color-accent)] bg-[var(--color-accent-soft)] " +
                          "font-semibold text-[var(--color-accent)]"
                        : "border-[var(--color-border)] bg-[var(--color-surface)] " +
                          "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                    )}
                  >
                    {l}
                    <span className="font-mono text-[0.68rem] opacity-80">{c}</span>
                  </button>
                );
              })}
            </div>
          </div>

          <Card className="overflow-hidden p-0">
            <div className="overflow-x-auto">
              {/* 2026-09-30：与直播页同款固定列宽（同一「实时评论统计列表」的两个视图，
                  列宽契约不得各写一份）。 */}
              <table className="w-full table-fixed border-collapse">
                <colgroup>
                  <col style={{ width: 30 }} />
                  <col style={{ width: 80 }} />
                  <col style={{ width: 130 }} />
                  <col style={{ width: 200 }} />
                  <col style={{ width: 110 }} />
                  <col style={{ width: 220 }} />
                  <col style={{ width: 80 }} />
                  <col style={{ width: 150 }} />
                </colgroup>
                <thead>
                  <tr>
                    <Th width={30} />
                    <Th width={80}>发送时间</Th>
                    <Th width={130}>发言人</Th>
                    <Th width={200}>评论内容</Th>
                    <Th width={110}>私信状态</Th>
                    <Th width={220}>私信文案</Th>
                    <Th width={80}>私信时间</Th>
                    <Th width={150}>操作</Th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((r) => (
                    <Fragment key={r.id}>
                      <tr className="transition-colors hover:bg-[var(--color-surface-raised)]">
                        <Td>
                          <span className="font-mono text-[var(--color-text-muted)]">
                            {exp === r.id ? "▾" : "▸"}
                          </span>
                        </Td>
                        <Td mono className="whitespace-nowrap">{r.time}</Td>
                        <Td>
                          <span className="inline-flex items-center gap-2">
                            <Avatar name={r.name} h={hue(r.name.length)} sm />
                            <span className="whitespace-nowrap font-medium">{r.name}</span>
                            {r.lv < 99 && (
                              <span className="rounded-[4px] border
                                               border-[color-mix(in_srgb,var(--color-warning)_40%,transparent)]
                                               px-1 font-mono text-[0.66rem]
                                               text-[var(--color-warning)]">
                                Lv.{r.lv}
                              </span>
                            )}
                          </span>
                        </Td>
                        <Td className="max-w-[260px]">
                          <span className="block truncate">{r.content}</span>
                        </Td>
                        <Td>
                          {r.dmStatus === "un" && !r.deliveryState ? (
                            <span className="text-[var(--color-text-muted)]">—</span>
                          ) : (
                            (() => {
                              const [label, tone] = displayStatus(r);
                              return <Tone tone={tone}>{label}</Tone>;
                            })()
                          )}
                        </Td>
                        {/* 2026-09-30：与直播页同一真源（live-shared）——
                            来源徽标 + 前 10 字预览 + title 全文。
                            查阅模式此前复用 `title` 里的 `\n`（JSX 属性中是普通字符，
                            不换行），改为统一调用 dmTitle()。 */}
                        <Td title={dmTitle(r)}>
                          <span className="flex min-w-0 items-center gap-1.5">
                            {(() => {
                              const _src = sourceMetaOf(r.contentSource);
                              return _src ? (
                                <span
                                  className="shrink-0 rounded-[4px] border px-1
                                             font-mono text-[0.66rem]"
                                  style={{
                                    color: _src.color,
                                    borderColor: `color-mix(in srgb, ${_src.color} 40%, transparent)`,
                                  }}
                                  title={`文案来源：${_src.label}`}
                                >
                                  {_src.label}
                                </span>
                              ) : null;
                            })()}
                            <span
                              className={cn(
                                "block min-w-0 flex-1 truncate",
                                r.dmText
                                  ? "text-[var(--color-text)]"
                                  : "text-[var(--color-text-muted)]"
                              )}
                            >
                              {dmFailReason(r)
                                ? <span className="text-[var(--color-danger)]">{dmFailReason(r)}</span>
                                : dmPreviewText(r)}
                            </span>
                          </span>
                        </Td>
                        <Td mono className="whitespace-nowrap">
                          {r.dmTime || <span className="text-[var(--color-text-muted)]">—</span>}
                        </Td>
                        <Td>
                          <Toolbar className="gap-1">
                            <Button
                              variant="link"
                              size="sm"
                              onClick={() => setExp(exp === r.id ? null : r.id)}
                            >
                              详情
                            </Button>
                            <Button variant="link" size="sm" onClick={() => sendDm(r)}>
                              发私信
                            </Button>
                            <Button
                              variant="link"
                              size="sm"
                              onClick={() => goMsg?.(r.name, r.dmText || "")}
                            >
                              去私信中心
                            </Button>
                          </Toolbar>
                        </Td>
                      </tr>
                      {exp === r.id && (
                        <tr key={r.id + "-d"}>
                          <Td colSpan={8} className="bg-[var(--color-surface-raised)] pt-1.5 pb-3.5">
                            <div className="my-2.5 rounded-[var(--radius-md)]
                                            border border-[var(--color-border)]
                                            bg-[var(--color-surface)] p-3.5">
                              <h4 className="mb-2 text-[0.72rem] tracking-[0.06em]
                                             text-[var(--color-text-muted)]">
                                发言历史 · {r.name}
                              </h4>
                              <div className="mb-3 flex flex-col gap-1.5">
                                {rows
                                  .filter((x) => x.name === r.name)
                                  .slice(0, 5)
                                  .map((x, i) => (
                                    <div key={i} className="flex items-baseline gap-2.5
                                                             text-[0.78rem]">
                                      <span className="shrink-0 font-mono text-[0.68rem]
                                                       text-[var(--color-text-muted)]">
                                        {x.time}
                                      </span>
                                      <span className="text-[var(--color-text)]">{x.content}</span>
                                    </div>
                                  ))}
                              </div>
                              <h4 className="mb-2 text-[0.72rem] tracking-[0.06em]
                                             text-[var(--color-text-muted)]">
                                私信内容
                              </h4>
                              <div className="text-[0.82rem] text-[var(--color-text)]">
                                {r.dmStatus === "un" ? (
                                  <span className="text-[var(--color-text-muted)]">
                                    尚未对该发言人发送私信
                                  </span>
                                ) : (
                                  <span>
                                    <Tone tone={DM_META[r.dmStatus][1]}>
                                      {DM_META[r.dmStatus][0]}
                                    </Tone>
                                    {"　"}
                                    {r.dmText || "（文案未填写）"}
                                    {r.dmTime ? "　·　" + r.dmTime : ""}
                                  </span>
                                )}
                                {r.dmStatus === "fail" && r.reason && (
                                  <div
                                    onClick={() => setFailRow(r)}
                                    title="点击查看失败原因详情与处理建议"
                                    className="mt-1.5 flex cursor-pointer items-center gap-2
                                               rounded-[var(--radius-sm)] border
                                               border-[color-mix(in_srgb,var(--color-danger)_25%,transparent)]
                                               bg-[var(--color-danger-soft)] px-2.5 py-1.5
                                               text-[0.75rem] text-[var(--color-danger)]"
                                  >
                                    <span
                                      className="shrink-0 rounded-full px-1.5 py-px
                                                 text-[0.68rem] font-semibold text-white"
                                      style={{ background: failInfoOf(r).color }}
                                    >
                                      {failInfoOf(r).label}
                                    </span>
                                    <span className="min-w-0 flex-1 truncate">{r.reason}</span>
                                    <span className="shrink-0 opacity-75">详情 ›</span>
                                  </div>
                                )}
                              </div>
                            </div>
                          </Td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
            {filtered.length === 0 && <Blank>无匹配记录</Blank>}
          </Card>
        </div>
      </div>
      {/* 2026-09-08：私信发送失败原因弹窗（区分调度堵塞/凭证失效/风控等） */}
      <FailReasonModal row={failRow} onClose={() => setFailRow(null)} />
    </motion.div>
  );
}
