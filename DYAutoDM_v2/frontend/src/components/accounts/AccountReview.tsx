import { Fragment, useEffect, useMemo, useState } from "react";
import { createPortal } from "react-dom";
import { motion } from "framer-motion";
import {
  ArrowLeft, ChevronDown, ChevronRight, Download, Inbox,
} from "lucide-react";
import { Avatar, TABS, hue, tick } from "../../components/ui";
import { StatusDot } from "@/components/ui/status-dot";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Select, SelectTrigger, SelectValue, SelectContent, SelectItem } from "@/components/ui/select";
import {
  Blank, Section, Stat, StatRow, Tone, Toolbar, SegmentedTabs, KeyValue,
} from "@/components/page/kit";
import { cn } from "@/lib/utils";
import { BrandMark, BRAND_NAME } from "@/components/brand";
import {
  type FmtAccount, type ReviewRow, PanelTitle, DM_META, Th, Td,
} from "./accounts-shared";

export function AccountReview({
  account,
  onClose,
  push,
  goMsg,
}: {
  account: FmtAccount;
  onClose: () => void;
  push: (msg: string, holdMs?: number) => void;
  goMsg?: (name: string, text?: string) => void;
}) {
  const [q, setQ] = useState("");
  const [st, setSt] = useState("all");
  const [asc, setAsc] = useState(false);
  const [exp, setExp] = useState<number | null>(null);

  const rows: ReviewRow[] = [];
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
  const cnt = (s: string) => rows.filter((r) => r.dmStatus === s).length;

  const exportCSV = () => {
    const head = ["发送时间", "发言人", "用户等级", "评论内容", "私信状态", "私信文案", "私信时间"];
    const body = filtered.map((r) => [
      r.time,
      r.name,
      r.lv,
      r.content,
      DM_META[r.dmStatus]?.[0] || "",
      r.dmText || "",
      r.dmTime || "",
    ]);
    const csv = [head, ...body]
      .map((l) => l.map((c) => '"' + String(c).replace(/"/g, '""') + '"').join(","))
      .join("\n");
    const blob = new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = account.name + "_review_" + tick().replace(/:/g, "") + ".csv";
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    push("已导出 CSV · " + a.download);
  };

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);

  const a = account;
  const r = a.lastRun;

  return createPortal(
    <motion.div
      className="fixed inset-0 z-[var(--z-view)] flex flex-col overflow-hidden bg-[var(--color-background)]"
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.18 }}
      data-od-id="acct-review"
    >
      <header
        className="flex flex-wrap items-center gap-5 border-b border-[var(--color-border)]
                   px-5 py-2.5 pr-[128px]"
      >
        <div className="flex items-baseline gap-2.5 whitespace-nowrap">
          <BrandMark size={22} rounded={6} className="shrink-0 self-center" />
          <h1 className="text-[0.94rem] font-semibold tracking-tight text-[var(--color-text)]">
            {BRAND_NAME}
          </h1>
          <span className="font-mono text-[0.68rem] uppercase tracking-[0.06em]
                           text-[var(--color-text-muted)]">
            Chuanliu Console
          </span>
        </div>
        <nav className="flex flex-1 flex-wrap gap-0.5" aria-label="主导航">
          {TABS.map(([id, label]) => (
            <button
              key={id}
              data-od-id={"review-tab-" + id}
              className={cn(
                "whitespace-nowrap rounded-[9px] px-3 py-1.5 text-[0.82rem]",
                "transition-colors duration-[var(--duration-fast)]",
                id === "accounts"
                  ? "bg-[var(--color-accent-soft)] font-medium text-[var(--color-accent)]"
                  : "text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-raised)] hover:text-[var(--color-text)]"
              )}
              onClick={() => {
                if (id !== "accounts") {
                  onClose();
                  setTimeout(() => {
                    (
                      document.querySelector('[data-od-id="tab-' + id + '"]') as HTMLElement | null
                    )?.click();
                  }, 50);
                }
              }}
            >
              {label}
            </button>
          ))}
        </nav>
        <div className="flex items-center gap-2 whitespace-nowrap">
          <Badge variant="outline" className="font-mono">
            <StatusDot tone="ok" pulse />Cookie <b className="font-semibold">有效</b>
          </Badge>
          <Badge variant="outline" className="font-mono">
            <StatusDot tone="ok" pulse />直播 <b className="font-semibold">已连接</b>
          </Badge>
          <Badge variant="accent">查阅模式</Badge>
        </div>
      </header>

      <div
        className="flex flex-wrap items-center gap-3 border-b border-[var(--color-border)]
                   bg-[var(--color-surface)] px-5 py-3.5"
      >
        <Button variant="ghost" onClick={onClose}>
          <ArrowLeft className="h-4 w-4" />返回账号管理
        </Button>
        <Avatar name={a.name} h={a.hue} sm />
        <h2 className="text-[0.94rem] font-semibold text-[var(--color-text)]">
          {a.name} · 查阅模式
        </h2>
        <Tone tone={a.tokenValid ? "ok" : a.lvl === "nosign" ? "warn" : "danger"}>
          {a.lvlLabel || (a.tokenValid ? "凭证有效" : "凭证过期")}
        </Tone>
        <div className="flex-1" />
        <Badge variant="outline">只读 · {a.uid}</Badge>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-10 pt-4.5">
        <StatRow cols={5} className="mb-3.5" data-od-id="review-stats">
          <Card className="p-4">
            <Stat label="捕获评论" value={r.comments.toLocaleString()} unit="条" />
          </Card>
          <Card className="p-4">
            <Stat
              label="发送私信"
              value={<span className="text-[var(--color-accent)]">{r.dmSent}</span>}
              unit="条"
            />
          </Card>
          <Card className="p-4">
            <Stat
              label="下播私信"
              value={<span className="text-[var(--color-warning)]">{r.dmAfterLive}</span>}
              unit="条"
            />
          </Card>
          <Card className="p-4">
            <Stat
              label="成功"
              value={<span className="text-[var(--color-success)]">{r.dmSuccess}</span>}
            />
          </Card>
          <Card className="p-4">
            <Stat
              label="失败"
              value={<span className="text-[var(--color-danger)]">{r.dmFail}</span>}
            />
          </Card>
          <Card className="p-4">
            <Stat
              label="成功率"
              value={
                <span
                  className={
                    r.dmSent > 0 && r.dmSuccess / r.dmSent > 0.9
                      ? "text-[var(--color-success)]"
                      : "text-[var(--color-warning)]"
                  }
                >
                  {r.dmSent > 0 ? Math.round((r.dmSuccess / r.dmSent) * 100) : 0}
                </span>
              }
              unit="%"
            />
          </Card>
        </StatRow>

        <div
          className="grid grid-cols-1 items-start gap-3.5 lg:grid-cols-[1fr_320px]"
          data-od-id="review-content"
        >
          <div>
            <Toolbar className="mb-3">
              <Input
                className="min-w-[180px] flex-1 text-[0.78rem]"
                placeholder="搜索昵称 / 评论内容 / 私信文案…"
                value={q}
                onChange={(e) => setQ(e.target.value)}
              />
              <Select value={st} onValueChange={setSt}>
                <SelectTrigger className="w-[130px] text-[0.78rem]" aria-label="私信状态筛选">
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
              <Button onClick={exportCSV}>
                <Download className="h-3.5 w-3.5" />导出 CSV
              </Button>
            </Toolbar>
            <div className="mb-2.5 flex flex-wrap items-center gap-2.5 text-[0.75rem] text-[var(--color-text-muted)]">
              <span>
                共{" "}
                <b className="font-mono font-semibold text-[var(--color-text)]">
                  {filtered.length}
                </b>{" "}
                条
              </span>
              <SegmentedTabs
                value={st}
                onChange={setSt}
                items={(
                  [
                    ["all", "全部", rows.length],
                    ["un", "未私信", cnt("un")],
                    ["wait", "待发送", cnt("wait")],
                    ["sent", "已发送", cnt("sent")],
                    ["fail", "发送失败", cnt("fail")],
                  ] as [string, string, number][]
                ).map(([id, l, c]) => ({
                  value: id,
                  label: (
                    <>
                      {l}
                      <span className="font-mono text-[0.68rem] text-[var(--color-text-muted)]">
                        {c}
                      </span>
                    </>
                  ),
                }))}
              />
            </div>
            <Card className="overflow-hidden">
              <div className="overflow-x-auto">
                <table className="w-full border-collapse">
                  <thead>
                    <tr>
                      <Th className="w-[30px]" />
                      <Th>发送时间</Th>
                      <Th>发言人</Th>
                      <Th>评论内容</Th>
                      <Th>私信状态</Th>
                      <Th>私信文案</Th>
                      <Th>私信时间</Th>
                      <Th className="w-[120px] text-right">操作</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.length === 0 && (
                      <tr>
                        <Td colSpan={8} className="py-9 text-center">
                          <Blank>
                            <Inbox className="mb-1.5 h-6 w-6" />
                            暂无评论记录 · 等待直播间捕获后自动入库
                          </Blank>
                        </Td>
                      </tr>
                    )}
                    {filtered.map((row) => (
                      <Fragment key={row.id}>
                        <tr
                          className="transition-colors duration-[var(--duration-fast)]
                                     hover:bg-[var(--color-surface-raised)]"
                        >
                          <Td>
                            {exp === row.id ? (
                              <ChevronDown className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
                            ) : (
                              <ChevronRight className="h-3.5 w-3.5 text-[var(--color-text-muted)]" />
                            )}
                          </Td>
                          <Td mono className="whitespace-nowrap">{row.time}</Td>
                          <Td>
                            <span className="inline-flex items-center gap-2">
                              <Avatar name={row.name} h={hue(row.name.length)} sm />
                              <span className="whitespace-nowrap font-medium text-[var(--color-text)]">
                                {row.name}
                              </span>
                              {row.lv < 99 && (
                                <span
                                  className="rounded-[4px] border border-[color-mix(in_srgb,var(--color-warning)_40%,transparent)]
                                             px-1 font-mono text-[0.66rem] text-[var(--color-warning)]"
                                >
                                  Lv.{row.lv}
                                </span>
                              )}
                            </span>
                          </Td>
                          <Td className="max-w-[260px]">
                            <span className="block truncate">{row.content}</span>
                          </Td>
                          <Td>
                            {row.dmStatus === "un" ? (
                              <span className="font-mono text-[var(--color-text-muted)]">—</span>
                            ) : (
                              <Tone tone={DM_META[row.dmStatus]?.[1] || "mute"}>
                                {DM_META[row.dmStatus]?.[0] || ""}
                              </Tone>
                            )}
                          </Td>
                          <Td className="max-w-[180px]">
                            <span
                              className={cn(
                                "block truncate",
                                row.dmText
                                  ? "text-[var(--color-text)]"
                                  : "text-[var(--color-text-muted)]"
                              )}
                            >
                              {row.dmText || (
                                <span className="font-mono text-[var(--color-text-muted)]">
                                  未发送
                                </span>
                              )}
                            </span>
                          </Td>
                          <Td mono className="whitespace-nowrap">
                            {row.dmTime || (
                              <span className="font-mono text-[var(--color-text-muted)]">—</span>
                            )}
                          </Td>
                          <Td>
                            <Toolbar className="justify-end gap-1">
                              <Button
                                variant="link"
                                size="sm"
                                onClick={() => setExp(exp === row.id ? null : row.id)}
                              >
                                详情
                              </Button>
                              <Button
                                variant="link"
                                size="sm"
                                onClick={() => {
                                  goMsg?.(row.name, row.content);
                                  push("已跳转私信中心 · " + row.name);
                                }}
                              >
                                发私信
                              </Button>
                            </Toolbar>
                          </Td>
                        </tr>
                        {exp === row.id && (
                          <tr key={row.id + "-d"}>
                            <Td colSpan={8} className="bg-[var(--color-surface-raised)] px-2.5 pb-3.5 pt-1.5">
                              <Card className="mb-2.5 rounded-[10px] border border-[var(--color-border)] bg-[var(--color-surface-raised)] p-3.5">
                                <h4 className="mb-2 text-[0.75rem] tracking-[0.06em] text-[var(--color-text-muted)]">
                                  评论历史 · {row.name}
                                </h4>
                                <div className="mb-3 flex flex-col gap-1.5">
                                  {rows
                                    .filter((x) => x.name === row.name)
                                    .slice(0, 5)
                                    .map((x, i) => (
                                      <div key={i} className="flex items-baseline gap-2.5 text-[0.78rem]">
                                        <span className="shrink-0 font-mono text-[0.7rem] text-[var(--color-text-muted)]">
                                          {x.time}
                                        </span>
                                        <span>{x.content}</span>
                                      </div>
                                    ))}
                                </div>
                                <h4 className="mb-2 text-[0.75rem] tracking-[0.06em] text-[var(--color-text-muted)]">
                                  私信内容
                                </h4>
                                <div className="text-[0.8rem]">
                                  {row.dmStatus === "un" ? (
                                    <span className="font-mono text-[var(--color-text-muted)]">
                                      尚未对该发言人发送私信
                                    </span>
                                  ) : (
                                    <span>
                                      <Tone tone={DM_META[row.dmStatus]?.[1] || "mute"}>
                                        {DM_META[row.dmStatus]?.[0] || ""}
                                      </Tone>
                                      {"　"}
                                      {row.dmText || "（文案未填写）"}
                                      {row.dmTime ? "　·　" + row.dmTime : ""}
                                    </span>
                                  )}
                                </div>
                              </Card>
                            </Td>
                          </tr>
                        )}
                      </Fragment>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>

          <div className="flex flex-col gap-3.5">
            <Section className="mb-0">
              <PanelTitle>运行信息</PanelTitle>
              <KeyValue
                cols={1}
                items={[
                  {
                    k: "直播间",
                    v: (
                      <span className="text-[var(--color-accent)]">{r.room}</span>
                    ),
                  },
                  { k: "运行时间", v: r.time },
                  { k: "运行时长", v: r.duration },
                  { k: "累计运行", v: r.totalRuns + " 次" },
                ]}
              />
            </Section>
            <Section className="mb-0">
              <PanelTitle>指纹浏览器</PanelTitle>
              <div className="mb-1 flex flex-wrap items-center gap-2.5">
                <StatusDot
                  tone={a.fp.status === "running" ? "ok" : "warn"}
                  pulse={a.fp.status === "running"}
                />
                <span className="text-[0.82rem] font-medium text-[var(--color-text)]">
                  {a.fp.name}
                </span>
                <Tone tone={a.fp.status === "running" ? "ok" : "warn"}>
                  {a.fp.status === "running" ? "运行中" : "已停止"}
                </Tone>
              </div>
              <KeyValue
                cols={1}
                items={[
                  { k: "系统", v: a.fp.os },
                  { k: "代理", mono: true, v: <span className="text-[0.7rem]">{a.fp.proxy}</span> },
                  { k: "分辨率", v: a.fp.resolution },
                  { k: "UA", v: <span className="block truncate text-[0.66rem]">{a.fp.ua}</span> },
                  { k: "WebRTC", v: a.fp.webrtc },
                  { k: "时区", v: a.fp.timezone },
                ]}
              />
            </Section>
            <Section className="mb-0">
              <PanelTitle>操作</PanelTitle>
              <div className="flex flex-col gap-2">
                <Button
                  className="w-full"
                  onClick={() => push("已对全部未私信用户批量发送私信")}
                >
                  批量发送私信
                </Button>
                <Button variant="ghost" className="w-full" onClick={exportCSV}>
                  <Download className="h-3.5 w-3.5" />导出全部数据
                </Button>
                <Button
                  variant="ghost"
                  className="w-full"
                  onClick={() => push("已跳转私信中心 · " + a.name)}
                >
                  进入私信中心
                </Button>
              </div>
            </Section>
          </div>
        </div>
      </div>
    </motion.div>,
    document.body
  );
}
// ===== 代理配置抽屉 =====
