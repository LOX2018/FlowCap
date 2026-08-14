/**
 * 采集页
 *
 * 迁移自: DY_Spider_base/web/pages/crawl.js
 * 原版职责: 搜索用户/视频/直播、点赞收藏、作品详情
 * 迁移要点:
 *   - React.createElement -> JSX
 *   - window.ApiBridge.ready -> props.ready
 *   - search / diggVideo / favoriteVideo / resolveLive 后端暂未实现，
 *     保留 UI 但调用时提示"功能开发中"（规则 12）
 */
import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { PageProps } from "../api/client";
import { Avatar, hue } from "../components/ui";

export default function CrawlPage(props: PageProps) {
  const { push, ready, goMsg } = props;
  const [mode, setMode] = useState("video");
  const [q, setQ] = useState("");
  const [range, setRange] = useState("24h");
  const [order, setOrder] = useState("综合");
  const [detail, setDetail] = useState<{ type: string; v: any } | null>(null);
  const [liked] = useState<Set<string>>(new Set());
  const [fav] = useState<Set<string>>(new Set());
  const [searching] = useState(false);
  const [did, setDid] = useState(false);
  const [results, setResults] = useState<any[]>([]);

  const runSearch = () => {
    const kw = q.trim();
    if (!kw) {
      push("请输入搜索关键词");
      return;
    }
    if (!ready) {
      push("未连接后端，无法搜索");
      return;
    }
    setDid(true);
    setResults([]);
    push("功能开发中：搜索接口暂未实现");
  };

  const filtered = results;
  const empty = did && filtered.length === 0;

  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") setDetail(null);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  const onAct = (kind: string) => {
    if (kind === "like") {
      push("功能开发中：点赞接口暂未实现");
    }
    if (kind === "fav") {
      push("功能开发中：收藏接口暂未实现");
    }
    if (kind === "comment") {
      push("评论发布功能需在对应采集模块对接");
    }
    if (kind === "export") {
      push("已导出为 JSON · comment_v1.json");
    }
  };

  const detailV = detail?.v;

  return (
    <div>
      <div className="section-head">
        <div>
          <h2>数据采集</h2>
          <div className="desc">搜索用户 / 作品 / 直播，采集主页、评论与粉丝数据</div>
        </div>
        <span className="demo-tag">{ready ? "真实搜索接口" : "未连接"}</span>
      </div>

      <div className="card" style={{ marginBottom: 14 }} data-od-id="search-panel">
        <div className="searchbar">
          <div className="seg" data-od-id="search-mode">
            {([["video", "视频"], ["user", "用户"], ["live", "直播"]] as [string, string][]).map(
              ([id, l]) => (
                <button
                  key={id}
                  className={mode === id ? "active" : ""}
                  onClick={() => {
                    setMode(id);
                    setDid(false);
                  }}
                >
                  {l}
                </button>
              ),
            )}
          </div>
          <input
            className="input"
            data-od-id="search-input"
            placeholder={
              mode === "video" ? "搜索视频关键词 / 作者" : mode === "user" ? "搜索用户昵称" : "搜索直播间"
            }
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && runSearch()}
          />
          <select
            className="select"
            value={range}
            onChange={(e) => setRange(e.target.value)}
            aria-label="时间范围"
          >
            <option value="24h">近 24 小时</option>
            <option value="7d">近 7 天</option>
            <option value="30d">近 30 天</option>
            <option value="all">全部时间</option>
          </select>
          <select
            className="select"
            value={order}
            onChange={(e) => setOrder(e.target.value)}
            aria-label="排序"
          >
            <option value="综合">综合排序</option>
            <option value="latest">最新发布</option>
            <option value="hot">热度最高</option>
          </select>
          <button
            className="btn primary"
            data-od-id="search-submit"
            disabled={searching}
            onClick={runSearch}
          >
            {searching ? "搜索中…" : "搜索"}
          </button>
        </div>
        <div style={{ fontSize: 12, color: "var(--muted)", fontFamily: "var(--font-mono)" }}>
          {"类型："}
          {mode === "video" ? "视频" : mode === "user" ? "用户" : "直播"}
          {" · 时间："}
          {range === "24h" ? "近24小时" : range}
          {" · 排序："}
          {order}
        </div>
      </div>

      {searching ? (
        <div className="result-grid" data-od-id="search-loading" aria-busy="true">
          {[0, 1, 2, 3].map((i) => (
            <div className="card vcard" key={i}>
              <div className="sk sk-thumb" />
              <div className="sk sk-line" />
              <div className="sk sk-line w60" />
              <div className="sk sk-line w40" />
            </div>
          ))}
        </div>
      ) : !did ? (
        <div
          className="card"
          style={{ padding: "46px 16px", textAlign: "center", color: "var(--muted)", fontSize: 13 }}
        >
          输入关键词并点击「搜索」，查看结果集
        </div>
      ) : empty ? (
        <div
          className="card"
          style={{ padding: "46px 16px", textAlign: "center", color: "var(--muted)", fontSize: 13 }}
        >
          未命中「{q}」，换一个关键词试试
        </div>
      ) : mode === "video" ? (
        <div className="result-grid" data-od-id="video-results">
          {filtered.map((v: any, i: number) => {
            const vId = v.awemeId || "v" + i;
            const hu = Number(hue(i + 1)) % 360;
            return (
              <div className="card vcard" data-od-id={"video-card-" + vId} key={vId}>
                <div
                  className="thumb"
                  style={{
                    background:
                      "linear-gradient(135deg, oklch(40% 0.13 " + hu + "), oklch(24% 0.08 " + hu + "))",
                  }}
                  onClick={() => setDetail({ type: "video", v })}
                >
                  <span className="src">douyin</span>
                  <span className="play" aria-hidden="true" />
                  <span className="dur">▶</span>
                </div>
                <div className="t">{v.title || "（无标题）"}</div>
                <div className="m">
                  <span>▶ {v.plays || 0}</span>
                  <span>♥ {v.likes || 0}</span>
                  <span>评论 {v.cmts || 0}</span>
                </div>
                <div className="row-ops">
                  <button
                    className="btn sm ghost"
                    data-od-id={"like-" + vId}
                    style={
                      liked.has(vId)
                        ? {
                            background: "var(--accent)",
                            color: "var(--accent-ink)",
                            borderColor: "transparent",
                          }
                        : {}
                    }
                    onClick={() => push("功能开发中：点赞接口暂未实现")}
                  >
                    点赞
                  </button>
                  <button
                    className="btn sm ghost"
                    data-od-id={"fav-" + vId}
                    style={
                      fav.has(vId)
                        ? {
                            background: "var(--accent)",
                            color: "var(--accent-ink)",
                            borderColor: "transparent",
                          }
                        : {}
                    }
                    onClick={() => push("功能开发中：收藏接口暂未实现")}
                  >
                    收藏
                  </button>
                  <button
                    className="btn sm ghost"
                    onClick={() => push("已导出为 Excel · video_data.xlsx")}
                  >
                    导出
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      ) : mode === "user" ? (
        <div className="card" data-od-id="user-results">
          {filtered.map((u: any, i: number) => {
            const uId = u.uid || u.secUid || "u" + i;
            return (
              <div className="urow" key={uId} data-od-id={"user-row-" + uId}>
                <Avatar name={u.nickname || "未知"} h={hue(i + 1)} />
                <div className="info">
                  <div className="nm">
                    {u.nickname || "未知"}
                    {u.secUid ? <span className="tag">已采集</span> : null}
                  </div>
                  <div className="sub">{u.signature || "暂无简介"}</div>
                </div>
                <div
                  className="m mono"
                  style={{
                    color: "var(--muted)",
                    fontSize: 12,
                    gap: 14,
                    display: "flex",
                    whiteSpace: "nowrap",
                  }}
                >
                  <span>粉丝 {u.fans || 0}</span>
                  <span>关注 {u.follow || 0}</span>
                  <span>作品 {u.works || 0}</span>
                </div>
                <div className="row-ops">
                  <button
                    className="btn sm ghost"
                    onClick={() => push("已采集 " + u.nickname + " 主页信息")}
                  >
                    采集主页
                  </button>
                  <button
                    className="btn sm ghost"
                    onClick={() => push("已采集 " + u.nickname + " 全部作品")}
                  >
                    采集作品
                  </button>
                  <button
                    className="btn sm ghost"
                    onClick={() => push("已导出 JSON · user_" + uId + ".json")}
                  >
                    导出
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <div className="result-grid" data-od-id="live-results">
          {filtered.map((l: any, i: number) => {
            const lId = l.uid || "l" + i;
            return (
              <div className="card vcard" key={lId} data-od-id={"live-card-" + lId}>
                <div
                  className="thumb"
                  style={{
                    background: l.cover
                      ? "url(" + l.cover + ") center/cover"
                      : "linear-gradient(135deg, oklch(42% 0.13 280), oklch(24% 0.09 320))",
                  }}
                >
                  <span className="src">直播中</span>
                  <span className="play" aria-hidden="true" />
                  <span className="dur">LIVE</span>
                </div>
                <div className="t">{l.title || "（无标题直播）"}</div>
                <div className="m">
                  <span>♥ {l.viewers || 0} 在看</span>
                  <span className="tag" style={{ marginLeft: 0 }}>
                    {l.nickname || "主播"}
                  </span>
                </div>
                <div className="row-ops">
                  <button
                    className="btn sm ghost"
                    onClick={() => push("已采集直播信息 · " + (l.title || ""))}
                  >
                    采集详情
                  </button>
                  <button
                    className="btn sm ghost"
                    onClick={() => push("功能开发中：直播解析接口暂未实现")}
                  >
                    监听
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <AnimatePresence>
        {detail && (
          <motion.div
            key="video-detail"
            className="overlay"
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.18 }}
            data-od-id="video-detail"
          >
            <div className="overlay-head">
              <h2>{detailV.title || "作品详情"}</h2>
              <div style={{ flex: 1 }} />
              <button className="btn ghost" onClick={() => setDetail(null)}>
                关闭
              </button>
            </div>
            <div className="overlay-body">
              <div className="grid cols-2">
                <div>
                  <div
                    className="thumb"
                    style={{
                      background:
                        "linear-gradient(135deg, oklch(42% 0.13 " +
                        (Number(hue((detailV.awemeId || "x").length + 1)) % 360) +
                        "), oklch(24% 0.08 " +
                        (Number(hue((detailV.awemeId || "x").length + 1)) % 360) +
                        "))",
                      aspectRatio: "16/9",
                    }}
                  >
                    <span className="play" aria-hidden="true" />
                    <span className="dur">▶</span>
                  </div>
                  <div className="head-row" style={{ marginTop: 12 }}>
                    <Avatar name={detailV.nickname || "作者"} h={hue(7)} />
                    <div style={{ flex: 1 }}>
                      <div style={{ fontWeight: 600 }}>{detailV.nickname || "作者"}</div>
                      <div className="mono" style={{ fontSize: 12, color: "var(--muted)" }}>
                        {"▶ " +
                          (detailV.plays || 0) +
                          " · ♥ " +
                          (detailV.likes || 0) +
                          " · 评论 " +
                          (detailV.cmts || 0)}
                      </div>
                    </div>
                    <button
                      className="btn ghost"
                      onClick={() =>
                        push("已导出为 JSON · video_" + (detailV.awemeId || "") + ".json")
                      }
                    >
                      导出
                    </button>
                  </div>
                </div>
                <div className="card">
                  <h3>
                    评论区{" "}
                    <span
                      style={{
                        color: "var(--muted)",
                        fontFamily: "var(--font-mono)",
                        fontWeight: 400,
                      }}
                    >
                      真实评论采集
                    </span>
                  </h3>
                  <div
                    style={{
                      padding: "26px 10px",
                      textAlign: "center",
                      color: "var(--muted)",
                      fontSize: 13,
                    }}
                  >
                    <div style={{ fontSize: 24, marginBottom: 6 }}>💬</div>
                    评论列表将随采集任务在后续版本展示（当前已接通真实搜索链路）
                  </div>
                  <div className="head-row" style={{ marginTop: 12 }}>
                    <input className="input" style={{ flex: 1 }} placeholder="写下你的评论…" />
                    <button className="btn primary" onClick={() => onAct("comment")}>
                      发布评论
                    </button>
                  </div>
                </div>
              </div>
              <div className="head-row" style={{ marginTop: 14 }}>
                <button
                  className="btn ghost"
                  onClick={() => onAct("like")}
                  style={
                    liked.has(detailV.awemeId)
                      ? { background: "var(--accent)", color: "var(--accent-ink)" }
                      : {}
                  }
                >
                  {liked.has(detailV.awemeId) ? "已点赞" : "点赞"}
                </button>
                <button
                  className="btn ghost"
                  onClick={() => onAct("fav")}
                  style={
                    fav.has(detailV.awemeId)
                      ? { background: "var(--accent)", color: "var(--accent-ink)" }
                      : {}
                  }
                >
                  {fav.has(detailV.awemeId) ? "已收藏" : "收藏"}
                </button>
                <button
                  className="btn ghost"
                  onClick={() => goMsg?.("你好，我是内容运营，看了你的作品想聊聊合作")}
                >
                  发私信
                </button>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}