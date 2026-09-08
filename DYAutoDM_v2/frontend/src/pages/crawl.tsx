/**
 * 采集页（2026-09-07 整合 GitHub cxiniao/- 抖音截流获客系统功能规格）
 *
 * 迁移自: DY_Spider_base/web/pages/crawl.js
 * 整合: cxiniao/- 的「关键词搜视频 -> 拉评论区 -> 评论用户一键私信截流」闭环
 * 链路: 基座 DouyinAPI.search_some_general_work / search_some_user /
 *       get_work_out_comment + V2 core.sender.send_by_uid（统一发送闸门）
 * 风控约束: 采集复用账号 .env 凭证被动签名，昵称/uid 全部取自结果自带字段，
 *       绝不批量查询用户信息（昵称关联风控红线）。
 * 账号数据源: /api/accounts（overview 无 accounts 字段，勿改回 overview）。
 */
import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { PageProps } from "../api/client";
import { Avatar, hue } from "../components/ui";

// 2026-09-07 移除用户搜索：基座 /aweme/v1/web/discover/search 已被抖音 verify_check
// 全面风控，"按关键词搜用户"在 2025-2026 已不可用；前端入口已关闭，仅保留视频搜索 +
// 评论截流完整闭环（视频作者 uid 从评论数据自带字段零成本获取，覆盖原搜人用例）。
export default function CrawlPage(props: PageProps) {
  const { push, ready, api } = props;
  const [q, setQ] = useState("");
  const [order, setOrder] = useState("0"); // 0 综合 / 1 最多点赞 / 2 最新发布
  const [pt, setPt] = useState("0"); // 0 不限 / 1 一天 / 7 一周 / 180 半年
  const [dur, setDur] = useState(""); // '' 不限 / 0-1 / 1-5 / 5-10000
  const [searching, setSearching] = useState(false);
  const [did, setDid] = useState(false);
  const [results, setResults] = useState<any[]>([]);
  // 账号选择：加载 /api/accounts 取 loggedIn 账号（overview 不含 accounts 字段）
  const [accounts, setAccounts] = useState<{ name: string; loggedIn: boolean }[]>([]);
  // 评论抽屉
  const [cmtFor, setCmtFor] = useState<any | null>(null);
  const [cmts, setCmts] = useState<any[]>([]);
  const [cmtLoading, setCmtLoading] = useState(false);
  // 评论私信模板与发送状态（uid -> 'sending' | 'sent' | fail-reason）
  const [dmTpl, setDmTpl] = useState("你好，看到你评论了我的内容，想和你聊聊～");
  const [authorTpl, setAuthorTpl] = useState(
    "你好，刷到你的作品很感兴趣，想和你聊聊合作～",
  );
  const [dmState, setDmState] = useState<Record<string, string>>({});
  // 评论筛选关键词 + 批量发送状态
  const [cmtFilter, setCmtFilter] = useState("");
  // 按关键词过滤后的评论列表（供命中数与批量发送使用）
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

  // 视频作者私信（独立话术 authorTpl，与评论截流话术分离）
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

  // 批量截流：按关键词筛选评论区用户 → 批量私信（走统一闸门，自动限速）
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
      // 刷新单条状态
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

  const fmtNum = (n: any) => {
    const v = Number(n) || 0;
    return v >= 10000 ? (v / 10000).toFixed(1) + "w" : String(v);
  };
  const fmtTs = (ts: any) => {
    const v = Number(ts);
    if (!v) return "";
    try {
      return new Date(v * 1000).toLocaleString("zh-CN", { hour12: false });
    } catch {
      return "";
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
    <div>
      <div className="section-head">
        <div>
          <h2>数据采集 · 评论截流</h2>
          <div className="desc">
            关键词搜视频 → 采集评论区 → 评论用户一键私信截流
          </div>
        </div>
        <span className="demo-tag">{ready ? "后端已连接" : "后端未连接"}</span>
      </div>

      {/* 账号 + 搜索面板 */}
      <div className="card" style={{ marginBottom: 14 }}>
        <div className="searchbar">
          <select
            className="select"
            value={account}
            onChange={(e) => setAccount(e.target.value)}
            aria-label="采集账号"
            style={{ maxWidth: 170 }}
          >
            {accounts.length === 0 && <option value="">（无可登录账号）</option>}
            {accounts.map((a: any) => (
              <option key={a.name} value={a.name}>
                {a.name}
              </option>
            ))}
          </select>
          <input
            className="input"
            placeholder="搜索视频关键词"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && runSearch()}
          />
          <button className="btn primary" disabled={searching} onClick={runSearch}>
            {searching ? "搜索中…" : "搜索"}
          </button>
        </div>
        <div
          style={{
            display: "flex",
            gap: 10,
            marginTop: 10,
              flexWrap: "wrap",
              fontSize: 12,
              color: "var(--muted)",
            }}
          >
            <label>
              排序{" "}
              <select className="select" value={order} onChange={(e) => setOrder(e.target.value)}>
                <option value="0">综合排序</option>
                <option value="1">最多点赞</option>
                <option value="2">最新发布</option>
              </select>
            </label>
            <label>
              发布时间{" "}
              <select className="select" value={pt} onChange={(e) => setPt(e.target.value)}>
                <option value="0">不限</option>
                <option value="1">一天内</option>
                <option value="7">一周内</option>
                <option value="180">半年内</option>
              </select>
            </label>
            <label>
              视频时长{" "}
              <select className="select" value={dur} onChange={(e) => setDur(e.target.value)}>
                <option value="">不限</option>
                <option value="0-1">1分钟内</option>
                <option value="1-5">1-5分钟</option>
                <option value="5-10000">5分钟以上</option>
              </select>
            </label>
          </div>
      </div>

      {searching ? (
        <div className="result-grid">
          {[0, 1, 2, 3].map((i) => (
            <div className="card vcard" key={i}>
              <div className="sk sk-thumb" />
              <div className="sk sk-line" />
              <div className="sk sk-line w60" />
            </div>
          ))}
        </div>
      ) : empty ? (
        <div
          className="card"
          style={{ padding: "46px 16px", textAlign: "center", color: "var(--muted)", fontSize: 13 }}
        >
          {did ? `未命中「${q}」，换一个关键词试试` : "输入关键词并点击「搜索」，查看结果集"}
        </div>
      ) : (
        <>
        <div className="card" style={{ marginBottom: 12, display: "flex", gap: 8, alignItems: "center" }}>
          <span style={{ fontSize: 12, color: "var(--muted)", whiteSpace: "nowrap" }}>作者话术</span>
          <input
            className="input"
            style={{ flex: 1 }}
            value={authorTpl}
            onChange={(e) => setAuthorTpl(e.target.value)}
            placeholder="「私信作者」按钮使用的话术（与评论区话术分离）"
          />
        </div>
        <div className="result-grid">
          {results.map((v: any, i: number) => {
            const vId = v.awemeId || "v" + i;
            const hu = Number(hue(i + 1)) % 360;
            return (
              <div className="card vcard" key={vId}>
                <div
                  className="thumb"
                  style={{
                    background: v.cover
                      ? undefined
                      : "linear-gradient(135deg, oklch(40% 0.13 " +
                        hu +
                        "), oklch(24% 0.08 " +
                        hu +
                        "))",
                    backgroundImage: v.cover ? `url("${v.cover}")` : undefined,
                    backgroundSize: "cover",
                    backgroundPosition: "center",
                  }}
                  onClick={() => openComments(v)}
                  title="点击采集该视频评论区"
                >
                  <span className="src">douyin</span>
                  <span className="play" aria-hidden="true" />
                  <span className="dur">💬 {fmtNum(v.cmts)}</span>
                </div>
                <div className="t">{v.title || "（无标题）"}</div>
                <div className="m">
                  <span>▶ {fmtNum(v.plays)}</span>
                  <span>♥ {fmtNum(v.likes)}</span>
                  <span>{v.nickname || "未知作者"}</span>
                </div>
                <div className="row-ops">
                  <button className="btn sm ghost" onClick={() => openComments(v)}>
                    采评论
                  </button>
                  {v.uid && (
                    <button
                      className="btn sm ghost"
                      onClick={() =>
                        sendAuthorDm(v).then(() => {})
                      }
                    >
                      私信作者
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
        </>
      )}

      {/* 评论区抽屉 */}
      <AnimatePresence>
        {cmtFor && (
          <motion.div
            key="comments"
            className="overlay"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.18 }}
          >
            <div className="overlay-head">
              {cmtFor.cover && (
                <div
                  className="thumb"
                  style={{
                    width: 56,
                    height: 56,
                    backgroundImage: `url("${cmtFor.cover}")`,
                    backgroundSize: "cover",
                    backgroundPosition: "center",
                    borderRadius: 8,
                    flexShrink: 0,
                  }}
                />
              )}
              <h2>评论区 · {cmtFor.title?.slice(0, 24) || cmtFor.awemeId}</h2>
              <div style={{ flex: 1 }} />
              <button className="btn ghost" onClick={() => setCmtFor(null)}>
                关闭
              </button>
            </div>
            <div className="overlay-body">
              <div className="card" style={{ marginBottom: 12 }}>
                <div className="head-row">
                  <span style={{ fontSize: 12, color: "var(--muted)" }}>评论话术</span>
                  <input
                    className="input"
                    style={{ flex: 1 }}
                    value={dmTpl}
                    onChange={(e) => setDmTpl(e.target.value)}
                    placeholder="发给评论用户的话术"
                  />
                </div>
                <div className="head-row" style={{ marginTop: 8 }}>
                  <span style={{ fontSize: 12, color: "var(--muted)" }}>按内容筛选</span>
                  <input
                    className="input"
                    style={{ flex: 1 }}
                    value={cmtFilter}
                    onChange={(e) => setCmtFilter(e.target.value)}
                    placeholder="只对评论含该关键词的用户发（留空=全部）"
                  />
                </div>
                <div
                  style={{
                    fontSize: 12,
                    color: "var(--muted)",
                    marginTop: 6,
                    display: "flex",
                    alignItems: "center",
                    gap: 8,
                  }}
                >
                  <span>
                    命中 {cmtFilter ? filteredCmts.length : cmts.length} 条评论的用户，可单发或批量。
                  </span>
                  <div style={{ flex: 1 }} />
                  <button
                    className="btn sm"
                    disabled={batching || !dmTpl.trim()}
                    onClick={sendBatch}
                  >
                    {batching ? "批量发送中…" : "批量私信全部"}
                  </button>
                </div>
              </div>

              {cmtLoading ? (
                <div
                  className="card"
                  style={{ padding: 30, textAlign: "center", color: "var(--muted)" }}
                >
                  评论采集中…
                </div>
              ) : cmts.length === 0 ? (
                <div
                  className="card"
                  style={{ padding: 30, textAlign: "center", color: "var(--muted)" }}
                >
                  未采到评论
                </div>
              ) : (
                <div className="card">
                  {cmts.map((c: any) => {
                    const st = dmState[c.uid];
                    return (
                      <div
                        key={c.cid}
                        style={{
                          display: "flex",
                          alignItems: "flex-start",
                          gap: 10,
                          padding: "9px 2px",
                          borderBottom: "1px solid var(--border)",
                        }}
                      >
                        <Avatar name={c.nickname || "路人"} h={hue((c.cid || "x").length)} />
                        <div style={{ flex: 1, minWidth: 0 }}>
                          <div style={{ fontWeight: 600, fontSize: 13 }}>
                            {c.nickname || "匿名"}
                            <span
                              className="mono"
                              style={{ marginLeft: 8, fontSize: 11, color: "var(--muted)" }}
                            >
                              {c.ip || ""}
                            </span>
                          </div>
                          <div style={{ fontSize: 13, margin: "3px 0" }}>{c.text}</div>
                          <div className="mono" style={{ fontSize: 11, color: "var(--muted)" }}>
                            ♥ {fmtNum(c.digg)} · {fmtTs(c.ts)}
                          </div>
                        </div>
                        {c.uid && (
                          <button
                            className="btn sm ghost"
                            disabled={st === "sending" || st === "sent"}
                            style={
                              st === "sent"
                                ? { background: "var(--accent)", color: "var(--accent-ink)" }
                                : {}
                            }
                            onClick={() => sendDm(c.uid, c.nickname)}
                          >
                            {st === "sending" ? "发送中…" : st === "sent" ? "已私信" : "私信"}
                          </button>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
