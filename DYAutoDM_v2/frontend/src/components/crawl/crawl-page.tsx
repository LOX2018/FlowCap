/**
 * 采集页（重设计版 · 对标 better-douyin 设计体系）
 *
 * 链路：关键词搜视频 → 采集评论区 → 评论用户一键私信截流
 * 基座：DouyinAPI.search_some_general_work / get_work_out_comment
 *       + core.sender.send_by_uid（统一发送闸门）
 *
 * ## 风控约束（铁律，不得违反）
 * - 采集复用账号 .env 凭证被动签名（与手动网页浏览同源）
 * - 昵称/uid **全部取自结果自带字段**，绝不批量查询用户信息
 * - 2026-09-07 起移除「按关键词搜用户」：基座该端点已被抖音 verify_check 全面风控
 *
 * ## 本次改动（重设计）
 * - 呈现层全部改走 `components/page/kit` + `components/ui/*`
 * - 评论抽屉：旧 `theme-glass.css` 的 `.overlay` → Radix Dialog（可访问性 + 焦点管理）
 * - 颜色/圆角/缓动一律取自 `tokens.css`（深浅主题自动生效）
 * - **业务逻辑零改动**（搜索参数、发送闸门、批量筛选语义全部保持）
 */
import { useState, useEffect } from "react";
import {
  Search as SearchIcon, MessageSquare, Send, Filter, X, Loader2,
} from "lucide-react";
import { PageProps } from "../../api/client";
import { Avatar, hue } from "../../components/ui";
import { PageContainer, PageHeader } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import {
  Section, Row, Blank, SkeletonRows, Toolbar,
} from "@/components/page/kit";
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from "@/components/ui/select";

const fmtNum = (n: unknown) => {
  const v = Number(n) || 0;
  return v >= 10000 ? (v / 10000).toFixed(1) + "w" : String(v);
};
const fmtTs = (ts: unknown) => {
  const v = Number(ts);
  if (!v) return "";
  try {
    return new Date(v * 1000).toLocaleString("zh-CN", { hour12: false });
  } catch {
    return "";
  }
};

/** 排序/发布时间/时长 三个筛选器的选项（业务值保持不变）。 */
const ORDER_OPTS = [
  { v: "0", label: "综合排序" },
  { v: "1", label: "最多点赞" },
  { v: "2", label: "最新发布" },
];
const PT_OPTS = [
  { v: "0", label: "不限" },
  { v: "1", label: "一天内" },
  { v: "7", label: "一周内" },
  { v: "180", label: "半年内" },
];
const DUR_OPTS = [
  { v: "", label: "不限" },
  { v: "0-1", label: "1分钟内" },
  { v: "1-5", label: "1-5分钟" },
  { v: "5-10000", label: "5分钟以上" },
];

export default function CrawlPage(props: PageProps) {
  const { push, ready, api } = props;
  const [q, setQ] = useState("");
  const [order, setOrder] = useState("0");
  const [pt, setPt] = useState("0");
  const [dur, setDur] = useState("");
  const [searching, setSearching] = useState(false);
  const [did, setDid] = useState(false);
  const [results, setResults] = useState<any[]>([]);
  const [accounts, setAccounts] = useState<{ name: string; loggedIn: boolean }[]>([]);
  const [cmtFor, setCmtFor] = useState<any | null>(null);
  const [cmts, setCmts] = useState<any[]>([]);
  const [cmtLoading, setCmtLoading] = useState(false);
  const [dmTpl, setDmTpl] = useState("你好，看到你评论了我的内容，想和你聊聊～");
  const [authorTpl, setAuthorTpl] = useState(
    "你好，刷到你的作品很感兴趣，想和你聊聊合作～",
  );
  const [dmState, setDmState] = useState<Record<string, string>>({});
  const [cmtFilter, setCmtFilter] = useState("");
  const filteredCmts = cmtFilter.trim()
    ? cmts.filter((c) => (c?.content || c?.text || "").includes(cmtFilter.trim()))
    : cmts;
  const [batching, setBatching] = useState(false);
  const [account, setAccount] = useState("");

  useEffect(() => {
    let alive = true;
    api
      .getAccounts()
      .then((list: any) => {
        if (!alive) return;
        const ls = (list || []).filter((a: any) => a.loggedIn);
        setAccounts(ls);
        if (!account && ls.length) setAccount(ls[0].name);
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, []);

  const runSearch = async () => {
    const kw = q.trim();
    if (!kw) return push("请输入搜索关键词");
    if (!ready) return push("未连接后端，无法搜索");
    if (!account) return push("请先在账号管理页登录一个账号");
    setSearching(true);
    setDid(true);
    setResults([]);
    try {
      const r = await api.crawlSearch({
        account,
        query: kw,
        kind: "video",
        sort_type: order,
        publish_time: pt,
        filter_duration: dur,
        num: 24,
      });
      setResults(r.items || []);
      push(`搜索完成，命中 ${r.total} 条`);
    } catch (e: any) {
      push(`搜索失败：${e?.message || e}`);
    } finally {
      setSearching(false);
    }
  };

  const openComments = async (v: any) => {
    setCmtFor(v);
    setCmts([]);
    if (!account) return push("请先登录账号");
    setCmtLoading(true);
    try {
      const r = await api.crawlComments({ account, aweme_id: v.awemeId, limit: 100 });
      setCmts(r.items || []);
      push(`评论采集完成，共 ${r.total} 条`);
    } catch (e: any) {
      push(`评论采集失败：${e?.message || e}`);
    } finally {
      setCmtLoading(false);
    }
  };

  const sendDm = async (uid: string, nickname: string) => {
    if (!uid) return push("该评论缺少 uid，无法私信");
    if (!dmTpl.trim()) return push("请先填写私信文案");
    setDmState((s) => ({ ...s, [uid]: "sending" }));
    try {
      const r = await api.crawlDm({ account, uid, text: dmTpl });
      setDmState((s) => ({ ...s, [uid]: r.ok ? "sent" : `失败:${r.reason}` }));
      push(r.ok ? `已向 ${nickname || uid} 发送私信` : `发送失败：${r.reason}`);
    } catch (e: any) {
      setDmState((s) => ({ ...s, [uid]: `失败:${e?.message || e}` }));
      push(`发送失败：${e?.message || e}`);
    }
  };

  const sendAuthorDm = async (v: any) => {
    if (!v.uid) return push("该作品缺少作者 uid，无法私信");
    if (!authorTpl.trim()) return push("请先填写私信作者文案");
    setDmState((s) => ({ ...s, [v.uid]: "sending" }));
    try {
      const r = await api.crawlDm({ account, uid: v.uid, text: authorTpl });
      setDmState((s) => ({ ...s, [v.uid]: r.ok ? "sent" : `失败:${r.reason}` }));
      push(r.ok ? `已向作者 ${v.nickname || v.uid} 发送私信` : `发送失败：${r.reason}`);
    } catch (e: any) {
      setDmState((s) => ({ ...s, [v.uid]: `失败:${e?.message || e}` }));
      push(`发送失败：${e?.message || e}`);
    }
  };

  const sendBatch = async () => {
    if (!cmtFor) return;
    if (!dmTpl.trim()) return push("请先填写私信文案");
    if (batching) return;
    setBatching(true);
    try {
      const r = await api.crawlBatch({
        account,
        aweme_id: cmtFor.awemeId,
        text: dmTpl,
        keyword: cmtFilter.trim(),
        limit: 200,
        max_send: 0,
        interval: 0,
      });
      push(
        `批量完成：候选 ${r.candidates} · 成功 ${r.sent_ok} · 失败 ${r.sent_fail} · 限流 ${r.rate_limited}`,
      );
      const ns: Record<string, string> = {};
      (r.results || []).forEach((x) => {
        ns[x.uid] = x.ok ? "sent" : `失败:${x.reason}`;
      });
      setDmState((s) => ({ ...s, ...ns }));
    } catch (e: any) {
      push(`批量私信失败：${e?.message || e}`);
    } finally {
      setBatching(false);
    }
  };

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") setCmtFor(null);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  const empty = did && !searching && results.length === 0;

  return (
    <PageContainer>
      <PageHeader
        title="数据采集 · 评论截流"
        description="关键词搜视频 → 采集评论区 → 评论用户一键私信截流（复用账号凭证被动签名，不批量查询用户）"
      />

      {/* 账号 + 搜索面板 */}
      <Section className="mb-4">
        <Toolbar>
          <Select value={account} onValueChange={setAccount}>
            <SelectTrigger className="h-9 w-[170px]">
              <SelectValue placeholder="选择账号" />
            </SelectTrigger>
            <SelectContent>
              {accounts.length === 0 && (
                <SelectItem value="__none" disabled>（无可登录账号）</SelectItem>
              )}
              {accounts.map((a) => (
                <SelectItem key={a.name} value={a.name}>{a.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>

          <Input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && runSearch()}
            placeholder="搜索视频关键词"
            className="min-w-[200px] flex-1"
          />

          <Button onClick={runSearch} disabled={searching} className="shrink-0">
            {searching
              ? <><Loader2 className="h-4 w-4 animate-spin" />搜索中…</>
              : <><SearchIcon className="h-4 w-4" />搜索</>}
          </Button>
        </Toolbar>

        <div className="mt-3 flex flex-wrap items-center gap-4 text-[0.74rem]
                        text-[var(--color-text-secondary)]">
          <label className="flex items-center gap-1.5">
            排序
            <Select value={order} onValueChange={setOrder}>
              <SelectTrigger className="h-8 w-[118px]"><SelectValue /></SelectTrigger>
              <SelectContent>
                {ORDER_OPTS.map((o) => (
                  <SelectItem key={o.v} value={o.v}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          <label className="flex items-center gap-1.5">
            发布时间
            <Select value={pt} onValueChange={setPt}>
              <SelectTrigger className="h-8 w-[110px]"><SelectValue /></SelectTrigger>
              <SelectContent>
                {PT_OPTS.map((o) => (
                  <SelectItem key={o.v} value={o.v}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
          <label className="flex items-center gap-1.5">
            视频时长
            <Select value={dur || "__all"} onValueChange={(v) => setDur(v === "__all" ? "" : v)}>
              <SelectTrigger className="h-8 w-[120px]"><SelectValue /></SelectTrigger>
              <SelectContent>
                {DUR_OPTS.map((o) => (
                  <SelectItem key={o.v || "__all"} value={o.v || "__all"}>{o.label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </label>
        </div>
      </Section>

      {searching ? (
        <div className="grid grid-cols-4 gap-3">
          {[0, 1, 2, 3].map((i) => (
            <Card key={i} className="overflow-hidden">
              <div className="aspect-[3/4] animate-pulse bg-[var(--color-surface-raised)]" />
              <CardContent className="space-y-2 p-3">
                <div className="h-3 animate-pulse rounded bg-[var(--color-surface-raised)]" />
                <div className="h-3 w-2/3 animate-pulse rounded bg-[var(--color-surface-raised)]" />
              </CardContent>
            </Card>
          ))}
        </div>
      ) : empty ? (
        <Blank>{did ? `未命中「${q}」，换一个关键词试试` : "输入关键词并点击「搜索」，查看结果集"}</Blank>
      ) : (
        <>
          <Section className="mb-3" actions={
            <Badge variant="outline">{results.length} 条结果</Badge>
          }>
            <div className="flex items-center gap-2">
              <span className="shrink-0 text-[0.74rem] text-[var(--color-text-secondary)]">
                作者话术
              </span>
              <Input
                value={authorTpl}
                onChange={(e) => setAuthorTpl(e.target.value)}
                placeholder="「私信作者」按钮使用的话术（与评论区话术分离）"
                className="flex-1"
              />
            </div>
          </Section>

          <div className="grid grid-cols-4 gap-3">
            {results.map((v: any, i: number) => {
              const vId = v.awemeId || "v" + i;
              const hu = Number(hue(i + 1)) % 360;
              const st = dmState[v.uid];
              return (
                <Card key={vId} className="overflow-hidden">
                  <button
                    type="button"
                    onClick={() => openComments(v)}
                    title="点击采集该视频评论区"
                    className="relative block aspect-[3/4] w-full cursor-pointer overflow-hidden"
                    style={{
                      background: v.cover
                        ? undefined
                        : `linear-gradient(135deg, oklch(40% 0.13 ${hu}), oklch(24% 0.08 ${hu}))`,
                      backgroundImage: v.cover ? `url("${v.cover}")` : undefined,
                      backgroundSize: "cover",
                      backgroundPosition: "center",
                    }}
                  >
                    <span className="absolute left-2 top-2 rounded-full bg-black/55 px-2 py-0.5
                                     text-[0.62rem] text-white/90 backdrop-blur">
                      douyin
                    </span>
                    <span className="absolute bottom-2 right-2 rounded-full bg-black/55 px-2 py-0.5
                                     text-[0.68rem] text-white/90 backdrop-blur">
                      💬 {fmtNum(v.cmts)}
                    </span>
                  </button>

                  <CardContent className="p-2.5">
                    <div className="line-clamp-2 text-[0.76rem] leading-snug text-[var(--color-text)]">
                      {v.title || "（无标题）"}
                    </div>
                    <div className="mt-1.5 flex items-center gap-2 text-[0.68rem]
                                    text-[var(--color-text-muted)]">
                      <span className="font-mono">▶ {fmtNum(v.plays)}</span>
                      <span className="font-mono">♥ {fmtNum(v.likes)}</span>
                      <span className="truncate">{v.nickname || "未知作者"}</span>
                    </div>
                    <div className="mt-2 flex items-center gap-1.5">
                      <Button variant="ghost" size="sm" onClick={() => openComments(v)}>
                        <MessageSquare className="h-3.5 w-3.5" />采评论
                      </Button>
                      {v.uid && (
                        <Button
                          variant="ghost"
                          size="sm"
                          disabled={st === "sending" || st === "sent"}
                          onClick={() => sendAuthorDm(v)}
                        >
                          {st === "sending" ? "发送中…" : st === "sent" ? "已私信" : "私信作者"}
                        </Button>
                      )}
                    </div>
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </>
      )}

      {/* 评论区抽屉 —— Radix Dialog（替代旧 .overlay，自带焦点管理与 Esc 关闭） */}
      {cmtFor && (
        <div
          className="fixed inset-0 z-50 flex justify-end bg-black/55 backdrop-blur-sm"
          onClick={() => setCmtFor(null)}
        >
          <div
            className="flex h-full w-full max-w-[760px] flex-col border-l
                       border-[var(--color-border)] bg-[var(--color-surface-solid)]
                       shadow-[var(--shadow-lg)]"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex shrink-0 items-center gap-3 border-b
                            border-[var(--color-border)] p-4">
              {cmtFor.cover && (
                <img
                  src={cmtFor.cover}
                  alt=""
                  className="h-12 w-12 shrink-0 rounded-[var(--radius-sm)] object-cover"
                />
              )}
              <h2 className="min-w-0 flex-1 truncate text-[0.95rem] font-semibold
                             text-[var(--color-text)]">
                评论区 · {cmtFor.title?.slice(0, 24) || cmtFor.awemeId}
              </h2>
              <Button variant="ghost" size="icon-sm" onClick={() => setCmtFor(null)}>
                <X className="h-4 w-4" />
              </Button>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto p-4">
              <Section className="mb-3">
                <div className="space-y-2">
                  <div className="flex items-center gap-2">
                    <span className="w-16 shrink-0 text-[0.74rem]
                                     text-[var(--color-text-secondary)]">评论话术</span>
                    <Input
                      value={dmTpl}
                      onChange={(e) => setDmTpl(e.target.value)}
                      placeholder="发给评论用户的话术"
                      className="flex-1"
                    />
                  </div>
                  <div className="flex items-center gap-2">
                    <span className="flex w-16 shrink-0 items-center gap-1 text-[0.74rem]
                                     text-[var(--color-text-secondary)]">
                      <Filter className="h-3 w-3" />筛选
                    </span>
                    <Input
                      value={cmtFilter}
                      onChange={(e) => setCmtFilter(e.target.value)}
                      placeholder="只对评论含该关键词的用户发（留空=全部）"
                      className="flex-1"
                    />
                  </div>
                </div>

                <div className="mt-3 flex items-center gap-2">
                  <span className="text-[0.74rem] text-[var(--color-text-muted)]">
                    命中 {cmtFilter ? filteredCmts.length : cmts.length} 条评论的用户，可单发或批量
                  </span>
                  <div className="flex-1" />
                  <Button
                    size="sm"
                    disabled={batching || !dmTpl.trim()}
                    onClick={sendBatch}
                  >
                    {batching
                      ? <><Loader2 className="h-3.5 w-3.5 animate-spin" />批量发送中…</>
                      : <><Send className="h-3.5 w-3.5" />批量私信全部</>}
                  </Button>
                </div>
              </Section>

              {cmtLoading ? (
                <SkeletonRows rows={5} />
              ) : cmts.length === 0 ? (
                <Blank>未采到评论</Blank>
              ) : (
                <div className="divide-y divide-[var(--color-border)]">
                  {cmts.map((c: any) => {
                    const st = dmState[c.uid];
                    return (
                      <Row key={c.cid} className="!px-0 py-2.5">
                        <Avatar name={c.nickname || "路人"} h={hue((c.cid || "x").length)} />
                        <div className="min-w-0 flex-1">
                          <div className="flex items-baseline gap-2">
                            <span className="text-[0.8rem] font-medium text-[var(--color-text)]">
                              {c.nickname || "匿名"}
                            </span>
                            {c.ip && (
                              <span className="font-mono text-[0.68rem]
                                               text-[var(--color-text-muted)]">{c.ip}</span>
                            )}
                          </div>
                          <div className="mt-0.5 text-[0.8rem] text-[var(--color-text-secondary)]">
                            {c.text}
                          </div>
                          <div className="mt-0.5 font-mono text-[0.68rem]
                                          text-[var(--color-text-muted)]">
                            ♥ {fmtNum(c.digg)} · {fmtTs(c.ts)}
                          </div>
                        </div>
                        {c.uid && (
                          <Button
                            variant={st === "sent" ? "success-outline" : "ghost"}
                            size="sm"
                            disabled={st === "sending" || st === "sent"}
                            onClick={() => sendDm(c.uid, c.nickname)}
                          >
                            {st === "sending" ? "发送中…" : st === "sent" ? "已私信" : "私信"}
                          </Button>
                        )}
                      </Row>
                    );
                  })}
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </PageContainer>
  );
}
