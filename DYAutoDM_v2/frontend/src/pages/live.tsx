/**
 * 直播监听页
 *
 * 迁移自: DY_Spider_base/web/pages/live.js
 * 原版职责: 实时弹幕流、热度曲线、发弹幕/点赞、私信模板配置、启动引擎
 * 迁移要点:
 *   - 多个 setInterval -> React Query useQuery
 *     (getTasks 2.5s / getAccounts 5s / getStream 2s)
 *   - 关键 bug 修复（规则 10）: 旧版 r.status === "发送失败" 永不匹配
 *     -> toDmStatus 按后端英文枚举 (captured/sent/fail/skipped) 类型安全比较
 *   - 旧 getLiveStream -> props.api.getStream（规则 11）
 *   - doLike / requestDm / resolveLive 后端暂未实现 -> 提示"功能开发中"
 *   - rows 数据源: 旧版 getStats.list -> 新版 tasksCfg.records（含 status 枚举）
 */
import { Fragment, useState, useEffect, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { PageProps } from "../api/client";
import { Avatar, Dot, Pill, hue, KIND_NAME, tick } from "../components/ui";

type DmStatus = "un" | "wait" | "sent" | "fail";
type PillColor = "ok" | "warn" | "danger" | "accent" | "mute";

const DM_META: Record<DmStatus, [string, PillColor]> = {
  un: ["未私信", "mute"],
  wait: ["待发送", "warn"],
  sent: ["已发送", "ok"],
  fail: ["发送失败", "danger"],
};

interface LiveMsg {
  uid: string;
  nickname: string;
  content: string;
  ts: number;
  status?: string;
}

interface LiveStream {
  alive: boolean;
  room_id?: string | null;
  online_count: number;
  messages: LiveMsg[];
  heat_curve: number[];
  likes?: number;
  listening?: boolean;
  dmRunning?: boolean;
  dmPaused?: boolean;
  roomTitle?: string;
  liveUrl?: string;
}

interface SendRecord {
  key: string;
  uid: string;
  nickname: string;
  sec_uid?: string | null;
  status?: string;
  reason?: string | null;
  captured_at: number;
  send_at?: number | null;
  sent_at?: number | null;
  content?: string | null;
  comment: string;
}

interface TaskConfig {
  live_url?: string;
  max_target?: number;
  keywords?: string[];
  dm_pool?: string[];
  delay_range?: [number, number];
  interval?: number;
}

interface TaskListResponse {
  config?: TaskConfig;
  records?: SendRecord[];
  sent?: number;
  captured?: number;
}

interface RealAcct {
  name: string;
  uid?: string | null;
  status?: string;
  role?: string;
}

interface FeedItem {
  id: number;
  t: string;
  k: string;
  n: string;
  l: number;
  x: string;
}

interface Row {
  id: number;
  time: string;
  name: string;
  lv: number;
  content: string;
  dmStatus: DmStatus;
  dmText: string;
  dmTime: string;
  ts: number;
}

/** 后端英文枚举 status -> 前端 DmStatus（修复旧版中文字符串永不匹配的 bug） */
function toDmStatus(s: string | null | undefined): DmStatus {
  switch ((s || "").toLowerCase()) {
    case "sent":
      return "sent";
    case "fail":
    case "failed":
    case "error":
      return "fail";
    case "captured":
    case "wait":
    case "waiting":
    case "pending":
    case "queued":
      return "wait";
    default:
      return "un";
  }
}

/** 服务器秒级时间戳 -> HH:MM:SS */
function fmtTime(ts: number | null | undefined): string {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  const p = (n: number) => (n < 10 ? "0" : "") + n;
  return p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
}

export default function LivePage(props: PageProps) {
  const { push, ready, goMsg, api } = props;
  const [viewMode, setViewMode] = useState<"single" | "grid">("single");
  const [activeAcct, setActiveAcct] = useState<string | null>(null);
  const [room, setRoom] = useState("");
  const [listening, setListening] = useState(false);
  const [review, setReview] = useState(false);
  const [dmDraft, setDmDraft] = useState("");
  const [myLikes] = useState(0);
  const [burst] = useState(0);
  const [batchN, setBatchN] = useState("10");
  const [dmLimit, setDmLimit] = useState("3");
  const [dmInterval, setDmInterval] = useState("60.0");
  const [dmJitter, setDmJitter] = useState("50,120");
  const [dmTemplates, setDmTemplates] = useState<{ text: string; enabled: boolean }[]>([
    { text: "你好，欢迎留言咨询唐律师工伤，留个方式，唐律下播后帮你分析", enabled: true },
    {
      text: "老乡 看到你在唐律师直播间咨询工伤问题，我是他的助理，你可以留个📞方式，我们帮你看下等级和赔偿 [握手]",
      enabled: true,
    },
    { text: "唐律还在直播，我是助理，可以留个联系方式，唐律下播后帮你分析", enabled: true },
  ]);
  const [forceRescan, setForceRescan] = useState(false);

  const { data: tasksCfg } = useQuery({
    queryKey: ["live-tasks"],
    queryFn: async () => (await api.getTasks()) as TaskListResponse,
    refetchInterval: 2500,
    enabled: !!ready,
  });
  const { data: accounts } = useQuery({
    queryKey: ["live-accounts"],
    queryFn: async () => (await api.getAccounts()) as RealAcct[],
    refetchInterval: 5000,
    enabled: !!ready,
  });
  const { data: streamRaw } = useQuery({
    queryKey: ["live-stream"],
    queryFn: async () => (await api.getStream()) as LiveStream,
    refetchInterval: 2000,
    enabled: !!ready,
  });

  const realAccts: RealAcct[] = useMemo(() => (Array.isArray(accounts) ? accounts : []), [accounts]);
  const ls: LiveStream | null = streamRaw || null;
  const running = !!ls?.alive;
  const online = ls?.online_count ?? 0;
  const roomLikes = ls?.likes ?? 0;
  const messages = useMemo(() => ls?.messages ?? [], [ls]);
  const heat = useMemo(() => ls?.heat_curve ?? [], [ls]);
  const records = useMemo(() => tasksCfg?.records ?? [], [tasksCfg]);

  const feed = useMemo<FeedItem[]>(
    () =>
      messages.map((f, i) => ({
        id: (f.ts || 0) * 1000 + i,
        t: fmtTime(f.ts),
        k: "danmaku",
        n: f.nickname || "未知",
        l: 0,
        x: f.content || "",
      })),
    [messages],
  );

  const rows = useMemo<Row[]>(
    () =>
      records.map((r, i) => ({
        id: 200000 + i,
        time: fmtTime(r.captured_at),
        name: r.nickname || r.uid || "未知",
        lv: 0,
        content: r.comment || r.content || "",
        dmStatus: toDmStatus(r.status),
        dmText: r.content || "",
        dmTime: r.sent_at ? fmtTime(r.sent_at) : "",
        ts: (r.captured_at || 0) * 1000,
      })),
    [records],
  );

  const dedup = useMemo(() => new Set(rows.map((r) => r.name)).size, [rows]);
  const dedupCount = useMemo(
    () => new Set(rows.map((r) => r.name + "||" + r.content)).size,
    [rows],
  );
  const waitCount = useMemo(() => rows.filter((r) => r.dmStatus === "wait").length, [rows]);
  const sentCount = useMemo(() => rows.filter((r) => r.dmStatus === "sent").length, [rows]);

  // Esc 关闭查阅模式
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") setReview(false);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  // 账号在线判定：后端账号对象运行时带 loggedIn 字段（RealAcct 未声明，此处安全读取）
  const isAcctOnline = (a: RealAcct): boolean =>
    (a as RealAcct & { loggedIn?: boolean }).loggedIn === true ||
    ["online", "ok", "logged_in", "logged-in"].includes((a.status || "").toLowerCase());

  const heatChart = (data: number[], w = 640, h = 120) => {
    if (!data || !data.length) {
      return (
        <div
          style={{
            height: h,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "var(--muted)",
            fontSize: 12,
          }}
        >
          等待房间热度数据 · 引擎运行后自动生成
        </div>
      );
    }
    const max = Math.max(...data) * 1.18;
    const x = (i: number) => (i / (data.length - 1)) * w;
    const y = (v: number) => h - 10 - (v / max) * (h - 16);
    const d = data
      .map((v, i) => (i ? "L" : "M") + x(i).toFixed(1) + " " + y(v).toFixed(1))
      .join("");
    return (
      <svg
        width={w}
        height={h}
        viewBox={`0 0 ${w} ${h}`}
        style={{ width: "100%", height: "auto" }}
        role="img"
        aria-label="房间热度曲线"
      >
        {[0.25, 0.5, 0.75].map((g) => (
          <line key={g} className="chart-grid" x1="0" x2={w} y1={h * g} y2={h * g} />
        ))}
        <path className="chart-area" d={`${d} L${w} ${h} L0 ${h} Z`} />
        <path className="chart-line" d={d} />
        <circle
          className="chart-last"
          cx={x(data.length - 1)}
          cy={y(data[data.length - 1])}
          r="3.4"
        />
      </svg>
    );
  };

  const sendDanmaku = () => {
    const text = dmDraft.trim();
    if (!text) return;
    if (!ready) {
      push("未连接后端，无法发送弹幕");
      return;
    }
    api.sendDanmaku(text)
      .then((r) => push(r.ok ? "弹幕已发送 · " + text : "发送失败: " + (r.content || "")))
      .catch((e: unknown) => push("发送异常: " + errMsg(e)));
    setDmDraft("");
  };

  // doLike / requestDm / resolveLive 后端暂未实现
  const doLike = () => push("功能开发中：点赞");
  const doBatch = () => push("功能开发中：批量点赞");
  const sendDm = (r: Row) => push("功能开发中：发送私信 → " + r.name);

  const cfgLiveUrl = tasksCfg?.config?.live_url || "";

  return (
    <div>
      <div className="section-head">
        <div>
          <h2>直播监听</h2>
          <div className="desc">实时弹幕 / 礼物 / 评论采集与私信自动化</div>
        </div>
        <div className="head-row">
          <div className="seg">
            <button
              className={viewMode === "single" ? "active" : ""}
              onClick={() => setViewMode("single")}
            >
              单账户
            </button>
            <button
              className={viewMode === "grid" ? "active" : ""}
              onClick={() => setViewMode("grid")}
            >
              多账户总览
            </button>
          </div>
          <span className="badge-conn">
            <Dot c={running ? "ok" : "warn"} pulse={running} />{" "}
            {ls ? (ls.listening ? "直播引擎监听中" : running ? "直播引擎运行中" : "直播引擎未运行") : "未连接"}
          </span>
          <span className="demo-tag">
            {ready ? (running ? "实时数据" : "等待运行") : "未连接"}
          </span>
          <span className="badge-conn" style={{ marginLeft: 8 }}>
            <Dot c={ls?.dmRunning ? "ok" : "warn"} pulse={!!ls?.dmRunning} />{" "}
            {ls
              ? ls.dmRunning
                ? ls.dmPaused
                  ? "私信引擎已暂停"
                  : "私信引擎发送中"
                : "私信引擎待命"
              : "未连接"}
          </span>
        </div>
      </div>

      {viewMode === "grid" ? (
        <div className="grid cols-2" data-od-id="live-grid">
          {realAccts.slice(0, 2).map((acct) => (
            <div className="card" key={acct.name} style={{ padding: 0, overflow: "hidden" }}>
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  padding: "12px 16px",
                  borderBottom: "1px solid var(--border)",
                  background: "var(--surface-2)",
                }}
              >
                <Avatar name={acct.name} h={hue(acct.name.length)} />
                <div style={{ flex: 1 }}>
                  <div style={{ fontWeight: 600, fontSize: 14 }}>{acct.name}</div>
                  <div className="mono" style={{ fontSize: 11.5, color: "var(--muted)" }}>
                    UID: {acct.uid || "—"}
                  </div>
                </div>
                <Pill c={isAcctOnline(acct) ? "ok" : "danger"}>
                  {isAcctOnline(acct) ? "在线" : "离线"}
                </Pill>
              </div>
              <div style={{ padding: "10px 16px", borderBottom: "1px solid var(--border)" }}>
                <div className="head-row" style={{ marginBottom: 6 }}>
                  <span style={{ fontSize: 12, color: "var(--muted)" }}>直播间</span>
                  <span className="mono" style={{ fontSize: 12, fontWeight: 600 }}>
                    {ls?.roomTitle || ls?.liveUrl || "未解析"}
                  </span>
                </div>
                <div className="head-row">
                  <span style={{ fontSize: 12, color: "var(--muted)" }}>在线人数</span>
                  <span className="mono" style={{ fontSize: 12 }}>
                    {online ? online.toLocaleString() : "—"}
                  </span>
                </div>
              </div>
              <div className="grid cols-2" style={{ padding: "10px 16px", gap: 8 }}>
                <div className="card stat" style={{ padding: "8px 10px" }}>
                  <span className="label" style={{ fontSize: 11 }}>
                    弹幕
                  </span>
                  <span className="num" style={{ fontSize: 18 }}>
                    {rows.length.toLocaleString()}
                  </span>
                </div>
                <div className="card stat" style={{ padding: "8px 10px" }}>
                  <span className="label" style={{ fontSize: 11 }}>
                    已私信
                  </span>
                  <span className="num" style={{ fontSize: 18 }}>
                    {sentCount}
                  </span>
                </div>
              </div>
              <div style={{ padding: "10px 16px", borderTop: "1px solid var(--border)" }}>
                <div className="head-row" style={{ marginBottom: 6 }}>
                  <span style={{ fontSize: 12, fontWeight: 600 }}>
                    房间热度
                  </span>
                  <span className="mono" style={{ fontSize: 12, color: "var(--accent)" }}>
                    {online ? online.toLocaleString() : 0} 人
                  </span>
                </div>
                <div style={{ height: 60 }}>{heatChart(heat.length ? heat : [0], 400, 60)}</div>
              </div>
              <div
                style={{
                  padding: "10px 16px",
                  borderTop: "1px solid var(--border)",
                  maxHeight: 120,
                  overflow: "auto",
                }}
              >
                {feed.slice(0, 5).map((f) => (
                  <div className="feed-item" key={f.id} style={{ padding: "3px 0" }}>
                    <span className="tm" style={{ fontSize: 10 }}>
                      {f.t}
                    </span>
                    <span className={"kind k-" + f.k} style={{ fontSize: 10 }}>
                      {KIND_NAME[f.k] || f.k}
                    </span>
                    <span className="txt" style={{ fontSize: 11 }}>
                      <b>{f.n}</b> {f.x}
                    </span>
                  </div>
                ))}
                {feed.length === 0 && (
                  <div
                    style={{
                      padding: "10px 0",
                      color: "var(--muted)",
                      fontSize: 11,
                      textAlign: "center",
                    }}
                  >
                    暂无实时信息
                  </div>
                )}
              </div>
              <div
                style={{
                  padding: "8px 16px",
                  borderTop: "1px solid var(--border)",
                  display: "flex",
                  gap: 6,
                }}
              >
                <button
                  className="btn sm ghost"
                  style={{ flex: 1 }}
                  onClick={() => {
                    setViewMode("single");
                    setActiveAcct(acct.name);
                    push("已切换到 " + acct.name);
                  }}
                >
                  进入监听
                </button>
                <button
                  className="btn sm ghost"
                  onClick={() => push("已导出 " + acct.name + " 数据")}
                >
                  导出
                </button>
              </div>
            </div>
          ))}
          {realAccts.length === 0 && (
            <div
              className="card"
              style={{ padding: 40, textAlign: "center", color: "var(--muted)" }}
            >
              <div style={{ fontSize: 26, marginBottom: 8 }}>👥</div>
              暂无已授权账号 · 请到「账号管理」添加并完成扫码
            </div>
          )}
        </div>
      ) : (
        <>
          <div className="card" style={{ marginBottom: 14 }} data-od-id="live-acct-select">
            <div className="head-row">
              <span style={{ fontSize: 13, fontWeight: 600 }}>当前监听账号</span>
              <div style={{ flex: 1 }} />
              <div className="seg">
                {realAccts.map((a) => (
                  <button
                    key={a.name}
                    className={activeAcct === a.name ? "active" : ""}
                    onClick={() => {
                      setActiveAcct(a.name);
                      push("已切换到 " + a.name);
                    }}
                  >
                    <Avatar name={a.name} h={hue(a.name.length)} sm /> {a.name}
                  </button>
                ))}
                {realAccts.length === 0 && (
                  <span className="mono" style={{ fontSize: 12, color: "var(--muted)" }}>
                    无已授权账号
                  </span>
                )}
              </div>
            </div>
          </div>

          <div className="card" style={{ marginBottom: 14 }} data-od-id="live-input">
            <div className="searchbar" style={{ marginBottom: 0 }}>
              <input
                className="input"
                style={{ flex: 1, fontFamily: "var(--font-mono)" }}
                value={room}
                onChange={(e) => setRoom(e.target.value)}
                placeholder="直播间 URL 或 room_id"
                aria-label="直播间地址"
              />
              <button
                className="btn ghost"
                data-od-id="live-parse"
                onClick={() => {
                  const u = room.trim();
                  if (!u) return;
                  push("功能开发中：解析房间号 · " + u);
                }}
              >
                解析房间号
              </button>
              {ready && !!cfgLiveUrl && room !== cfgLiveUrl && (
                <button className="btn ghost" onClick={() => setRoom(cfgLiveUrl)}>
                  填入已配置
                </button>
              )}
              {!ready && (
                <button
                  className="btn primary"
                  data-od-id="live-start"
                  onClick={() => {
                    setListening((s) => !s);
                    push(listening ? "已停止监听" : "开始监听 " + room);
                  }}
                >
                  {listening ? "停止监听" : "开始监听"}
                </button>
              )}
              {ready && (
                <>
                  <button
                    className="btn primary"
                    data-od-id="live-start"
                    disabled={running}
                    onClick={() => {
                      const cfg = {
                        liveUrl: room,
                        maxTarget: parseInt(dmLimit, 10) || 9999,
                        interval: parseFloat(dmInterval) || 60,
                        delay: dmJitter,
                        dmPool: dmTemplates
                          .filter((t) => t.text && t.text.trim())
                          .map((t) => ({ text: t.text.trim(), enabled: t.enabled })),
                        forceRescan: forceRescan,
                      };
                      api
                        .start(cfg)
                        .then((r) =>
                          push(r.ok ? "引擎已启动 · " + room : "启动失败: " + (r.state || "")),
                        )
                        .catch((e: unknown) => push("启动异常: " + errMsg(e)));
                    }}
                  >
                    开始自动私信
                  </button>
                  <button
                    className="btn ghost"
                    data-od-id="live-pause"
                    disabled={!running}
                    onClick={() =>
                      api
                        .pause()
                        .then((r) => push(r.ok ? "已暂停" : "暂停失败"))
                        .catch((e: unknown) => push("暂停异常: " + errMsg(e)))
                    }
                  >
                    暂停
                  </button>
                  <button
                    className="btn ghost"
                    data-od-id="live-resume"
                    disabled={!running}
                    onClick={() =>
                      api
                        .resume()
                        .then((r) => push(r.ok ? "已继续" : "继续失败"))
                        .catch((e: unknown) => push("继续异常: " + errMsg(e)))
                    }
                  >
                    继续
                  </button>
                  <button
                    className="btn ghost danger"
                    data-od-id="live-stop"
                    disabled={!running}
                    onClick={() =>
                      api
                        .stop()
                        .then((r) => push(r.ok ? "已停止" : "停止失败"))
                        .catch((e: unknown) => push("停止异常: " + errMsg(e)))
                    }
                  >
                    停止
                  </button>
                </>
              )}
            </div>
            <div style={{ fontSize: 11.5, color: "var(--muted)", marginTop: 6 }}>
              {ready
                ? running
                  ? "引擎运行中（真实监听）"
                  : "引擎未运行 · 配置后点「开始自动私信」"
                : "粘贴直播页/分享短链/用户主页链接，自动识别"}
            </div>
          </div>

          <div className="card" style={{ marginBottom: 14 }} data-od-id="live-auto-dm">
            <h3>自动私信配置</h3>
            <div className="grid cols-3" style={{ marginBottom: 14 }}>
              <div className="field">
                <label>发送上限</label>
                <input
                  className="input"
                  type="number"
                  min="1"
                  max="100"
                  value={dmLimit}
                  onChange={(e) => setDmLimit(e.target.value)}
                  style={{ width: "100%", fontFamily: "var(--font-mono)" }}
                  aria-label="每场最多发送私信条数"
                />
                <span className="hint">每场直播最多发送私信条数</span>
              </div>
              <div className="field">
                <label>间隔（秒）</label>
                <input
                  className="input"
                  type="number"
                  min="1"
                  step="0.1"
                  value={dmInterval}
                  onChange={(e) => setDmInterval(e.target.value)}
                  style={{ width: "100%", fontFamily: "var(--font-mono)" }}
                  aria-label="两条私信之间的间隔秒数"
                />
                <span className="hint">两条私信之间的等待时间</span>
              </div>
              <div className="field">
                <label>延迟抖动（秒）</label>
                <input
                  className="input"
                  value={dmJitter}
                  onChange={(e) => setDmJitter(e.target.value)}
                  style={{ width: "100%", fontFamily: "var(--font-mono)" }}
                  aria-label="延迟抖动区间"
                />
                <span className="hint">格式：50,120 = 随机区间；60 = 固定延迟</span>
              </div>
            </div>

            <label className="head-row" style={{ gap: 8, marginBottom: 12, fontSize: 12 }}>
              <span className="switch" style={{ flex: "none" }}>
                <input
                  type="checkbox"
                  checked={forceRescan}
                  onChange={(e) => setForceRescan(e.target.checked)}
                  aria-label="强制重扫"
                />
                <i />
              </span>
              <span style={{ color: "var(--muted)" }}>
                强制重扫（忽略已处理记录，重新匹配私信目标）
              </span>
            </label>

            <div>
              <div className="head-row" style={{ marginBottom: 10 }}>
                <h3 style={{ marginBottom: 0 }}>私信词库</h3>
                <span style={{ fontSize: 12, color: "var(--muted)" }}>
                  每行一条，勾选 = 启用，发送时随机抽已启用的一条
                </span>
              </div>
              <div
                style={{ display: "flex", flexDirection: "column", gap: 8 }}
                data-od-id="dm-templates"
              >
                {dmTemplates.map((t, i) => (
                  <div key={i} className="head-row" style={{ gap: 10 }}>
                    <span className="switch" style={{ flex: "none" }}>
                      <input
                        type="checkbox"
                        checked={t.enabled}
                        onChange={(e) => {
                          const next = [...dmTemplates];
                          next[i] = { ...next[i], enabled: e.target.checked };
                          setDmTemplates(next);
                        }}
                        aria-label={"启用模板 " + (i + 1)}
                      />
                      <i />
                    </span>
                    <input
                      className="input"
                      style={{ flex: 1 }}
                      value={t.text}
                      onChange={(e) => {
                        const next = [...dmTemplates];
                        next[i] = { ...next[i], text: e.target.value };
                        setDmTemplates(next);
                      }}
                      placeholder="输入私信文案…"
                    />
                  </div>
                ))}
              </div>
              <div className="head-row" style={{ marginTop: 10, gap: 8 }}>
                <button
                  className="btn sm ghost"
                  onClick={() => setDmTemplates(dmTemplates.map((t) => ({ ...t, enabled: true })))}
                >
                  全选
                </button>
                <button
                  className="btn sm ghost"
                  onClick={() =>
                    setDmTemplates(dmTemplates.map((t) => ({ ...t, enabled: false })))
                  }
                >
                  全不选
                </button>
                <button
                  className="btn sm ghost"
                  onClick={() => setDmTemplates([...dmTemplates, { text: "", enabled: true }])}
                >
                  添加一条
                </button>
                <button
                  className="btn sm ghost"
                  onClick={() => setDmTemplates(dmTemplates.filter((t) => t.enabled))}
                >
                  删除选中
                </button>
                <span className="mono" style={{ fontSize: 11.5, color: "var(--muted)" }}>
                  已启用 {dmTemplates.filter((t) => t.enabled).length} / {dmTemplates.length} 条
                </span>
                {ready && (
                  <>
                    <button
                      className="btn sm primary"
                      onClick={() =>
                        api
                          .saveDmPool(
                            dmTemplates.filter((t) => t.text.trim()).map((t) => t.text.trim()),
                          )
                          .then((r) =>
                            push(r.ok ? "词库已保存 · " + r.count + " 条" : "保存失败"),
                          )
                          .catch((e: unknown) => push("保存异常: " + errMsg(e)))
                      }
                    >
                      保存词库
                    </button>
                    <button
                      className="btn sm ghost"
                      onClick={() =>
                        api
                          .saveConfig({
                            liveUrl: room,
                            maxTarget: parseInt(dmLimit, 10),
                            interval: parseFloat(dmInterval),
                            delay: dmJitter,
                            dmPool: dmTemplates,
                          })
                          .then((r) => push(r.ok ? "配置已写回" : "写回失败"))
                          .catch((e: unknown) => push("写回异常: " + errMsg(e)))
                      }
                    >
                      保存配置
                    </button>
                  </>
                )}
              </div>
            </div>
          </div>

          <div className="live-layout" style={{ marginBottom: 14 }}>
            <div className="card" data-od-id="live-feed">
              <h3>
                <span>
                  实时信息流{" "}
                  <span className="mono" style={{ color: "var(--accent)", fontWeight: 400 }}>
                    {KIND_NAME.danmaku} / {KIND_NAME.gift} / {KIND_NAME.enter}
                  </span>
                </span>
                <span className="mono like-total">♥ {roomLikes.toLocaleString()}</span>
              </h3>
              <div className="feed" style={{ maxHeight: 236 }}>
                {feed.map((f) => (
                  <div className="feed-item" key={f.id}>
                    <span className="tm">{f.t}</span>
                    <span className={"kind k-" + f.k}>{KIND_NAME[f.k] || f.k}</span>
                    <span className="txt">
                      <b>{f.n}</b>　{f.x}
                    </span>
                    {f.l > 0 && <span className="lv">Lv.{f.l}</span>}
                  </div>
                ))}
                {feed.length === 0 && (
                  <div
                    style={{
                      padding: "28px 12px",
                      textAlign: "center",
                      color: "var(--muted)",
                      fontSize: 12,
                    }}
                  >
                    暂无实时信息 · 引擎运行后自动展示弹幕 / 礼物 / 进场 / 点赞 / 关注
                  </div>
                )}
              </div>
              <div
                className="head-row"
                style={{ marginTop: 12, paddingTop: 12, borderTop: "1px solid var(--border)" }}
              >
                <input
                  className="input"
                  style={{ flex: 1 }}
                  placeholder="发送弹幕到直播间…"
                  value={dmDraft}
                  onChange={(e) => setDmDraft(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && sendDanmaku()}
                />
                <button className="btn ghost" data-od-id="live-send-danmaku" onClick={sendDanmaku}>
                  发送
                </button>
                <button className="btn ghost like-btn" data-od-id="live-like" onClick={doLike}>
                  ♥ 点赞
                  {myLikes > 0 && <span className="like-cnt">×{myLikes}</span>}
                  {burst > 0 && (
                    <span className="burst" key={myLikes}>
                      +{burst}
                    </span>
                  )}
                </button>
              </div>
              <div className="head-row batch-row" data-od-id="live-batch-like">
                <span className="mono" style={{ fontSize: 12, color: "var(--muted)" }}>
                  批量点赞
                </span>
                <input
                  className="input batch-input"
                  type="number"
                  min="1"
                  max="1000"
                  aria-label="批量点赞数量"
                  value={batchN}
                  onChange={(e) => setBatchN(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && doBatch()}
                />
                <button className="btn ghost" onClick={doBatch}>
                  批量点赞
                </button>
                <span className="mono" style={{ fontSize: 11.5, color: "var(--muted)" }}>
                  每次按输入数量执行，上限 1000
                </span>
              </div>
            </div>
            <div className="card" data-od-id="heat-chart">
              <h3>
                房间热度{" "}
                <span className="mono" style={{ color: "var(--accent)", fontWeight: 400 }}>
                  {heat.length
                    ? heat[heat.length - 1].toLocaleString()
                    : online
                      ? online.toLocaleString()
                      : 0}{" "}
                  人在线
                </span>
              </h3>
              <div className="chart-wrap">{heatChart(heat)}</div>
            </div>
          </div>

          <div className="card" data-od-id="comment-stats">
            <div className="table-tools">
              <div style={{ flex: 1, minWidth: 240 }}>
                <h3 style={{ marginBottom: 0 }}>实时评论统计列表</h3>
              </div>
              <button className="btn ghost" data-od-id="review-open" onClick={() => setReview(true)}>
                进入查阅模式
              </button>
            </div>
            <div className="count-line">
              共 <b>{rows.length}</b> 条弹幕记录 · 去重 <b>{dedupCount}</b> 条 · 实际发言{" "}
              <b>{dedup}</b> 人 · 待发送私信 <b>{waitCount}</b> 条 · 已发送私信{" "}
              <b>{sentCount}</b> 条
            </div>
            <div className="table-scroll">
              <table className="comment-table">
                <thead>
                  <tr>
                    <th>发送时间</th>
                    <th>发言人</th>
                    <th>评论内容</th>
                    <th>私信状态</th>
                    <th>私信文案</th>
                    <th>私信时间</th>
                  </tr>
                </thead>
                <tbody>
                  {rows.length === 0 && (
                    <tr>
                      <td
                        colSpan={6}
                        style={{
                          padding: "30px 12px",
                          textAlign: "center",
                          color: "var(--muted)",
                          fontSize: 13,
                        }}
                      >
                        <div style={{ fontSize: 24, marginBottom: 6 }}>📭</div>
                        暂无评论记录 · 引擎运行后自动捕获
                      </td>
                    </tr>
                  )}
                  {rows.slice(0, 12).map((r) => (
                    <tr key={r.id}>
                      <td className="mono">{r.time}</td>
                      <td>
                        <span className="speaker">
                          <Avatar name={r.name} h={hue(r.name.length)} sm />
                          <span className="nm">{r.name}</span>
                          {r.lv < 99 && <span className="lv">Lv.{r.lv}</span>}
                        </span>
                      </td>
                      <td className="content-cell" title={r.content}>
                        <span className="cmt-text">{r.content}</span>
                      </td>
                      <td>
                        {r.dmStatus === "un" ? (
                          <span className="blank">—</span>
                        ) : (
                          <Pill c={DM_META[r.dmStatus][1]}>{DM_META[r.dmStatus][0]}</Pill>
                        )}
                      </td>
                      <td className="dm-cell">
                        <span className={"dm-text" + (r.dmText ? " has" : "")} title={r.dmText}>
                          {r.dmText || <span className="blank">未发送</span>}
                        </span>
                      </td>
                      <td className="mono">{r.dmTime || <span className="blank">—</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      <AnimatePresence>
        {review && (
          <ReviewMode
            key="review-mode"
            rows={rows}
            onClose={() => setReview(false)}
            push={push}
            sendDm={sendDm}
            goMsg={goMsg}
          />
        )}
      </AnimatePresence>
    </div>
  );
}

/** 统一提取错误信息（catch 变量在 strict 模式下为 unknown） */
function errMsg(e: unknown): string {
  return (e as { message?: string })?.message || String(e);
}

interface ReviewModeProps {
  rows: Row[];
  onClose: () => void;
  push: (msg: string) => void;
  sendDm: (r: Row) => void;
  goMsg?: (name: string, text?: string) => void;
}

function ReviewMode({ rows, onClose, push, sendDm, goMsg }: ReviewModeProps) {
  const [q, setQ] = useState("");
  const [st, setSt] = useState<"all" | DmStatus>("all");
  const [asc, setAsc] = useState(false);
  const [exp, setExp] = useState<number | null>(null);

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
      className="overlay"
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.18 }}
      data-od-id="live-review"
    >
      <div className="overlay-head">
        <button className="btn ghost" data-od-id="review-back" onClick={onClose}>
          ‹ 返回实时流
        </button>
        <h2>评论查阅模式</h2>
        <div style={{ flex: 1 }} />
        <span className="demo-tag">只读 · 实时入库</span>
      </div>
      <div className="overlay-body">
        <div className="table-tools">
          <input
            className="input"
            placeholder="搜索昵称 / 评论内容 / 私信文案…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
          />
          <select
            className="select"
            value={st}
            onChange={(e) => setSt(e.target.value as "all" | DmStatus)}
            aria-label="私信状态筛选"
          >
            <option value="all">全部状态</option>
            <option value="un">未私信</option>
            <option value="wait">待发送</option>
            <option value="sent">已发送</option>
            <option value="fail">发送失败</option>
          </select>
          <button className="btn ghost" onClick={() => setAsc((s) => !s)}>
            {asc ? "时间 ↑" : "时间 ↓"}
          </button>
          <button
            className="btn ghost"
            onClick={() => {
              setSt("all");
              setQ("");
            }}
          >
            重置
          </button>
          <button className="btn primary" data-od-id="review-export" onClick={exportCSV}>
            导出 CSV
          </button>
        </div>
        <div className="count-line">
          共 <b>{filtered.length}</b> 条 ·{" "}
          <span
            className="filter-pills"
            style={{ display: "inline-flex", marginLeft: 10, verticalAlign: "middle" }}
          >
            {pills.map(([id, l, c]) => (
              <button
                key={id}
                className={"fpill" + (st === id ? " active" : "")}
                onClick={() => setSt(id as "all" | DmStatus)}
              >
                {l}
                <span className="c">{c}</span>
              </button>
            ))}
          </span>
        </div>
        <div className="card" style={{ padding: 0 }}>
          <div className="table-scroll">
            <table className="comment-table">
              <thead>
                <tr>
                  <th style={{ width: 30 }} />
                  <th>发送时间</th>
                  <th>发言人</th>
                  <th>评论内容</th>
                  <th>私信状态</th>
                  <th>私信文案</th>
                  <th>私信时间</th>
                  <th style={{ width: 150 }}>操作</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((r) => (
                  <Fragment key={r.id}>
                    <tr>
                      <td>
                        <span className="mono" style={{ color: "var(--muted)" }}>
                          {exp === r.id ? "▾" : "▸"}
                        </span>
                      </td>
                      <td className="mono">{r.time}</td>
                      <td>
                        <span className="speaker">
                          <Avatar name={r.name} h={hue(r.name.length)} sm />
                          <span className="nm">{r.name}</span>
                          {r.lv < 99 && <span className="lv">Lv.{r.lv}</span>}
                        </span>
                      </td>
                      <td className="content-cell">
                        <span className="cmt-text">{r.content}</span>
                      </td>
                      <td>
                        {r.dmStatus === "un" ? (
                          <span className="blank">—</span>
                        ) : (
                          <Pill c={DM_META[r.dmStatus][1]}>{DM_META[r.dmStatus][0]}</Pill>
                        )}
                      </td>
                      <td className="dm-cell">
                        <span className={"dm-text" + (r.dmText ? " has" : "")}>
                          {r.dmText || <span className="blank">未发送</span>}
                        </span>
                      </td>
                      <td className="mono">{r.dmTime || <span className="blank">—</span>}</td>
                      <td>
                        <div className="head-row" style={{ gap: 6 }}>
                          <button
                            className="btn text sm"
                            onClick={() => setExp(exp === r.id ? null : r.id)}
                          >
                            详情
                          </button>
                          <button className="btn text sm" onClick={() => sendDm(r)}>
                            发私信
                          </button>
                          <button
                            className="btn text sm"
                            onClick={() => goMsg?.(r.name, r.dmText || "")}
                          >
                            去私信中心
                          </button>
                        </div>
                      </td>
                    </tr>
                    {exp === r.id && (
                      <tr key={r.id + "-d"}>
                        <td
                          colSpan={8}
                          style={{ padding: "6px 10px 14px", background: "var(--surface-2)" }}
                        >
                          <div className="detail-panel">
                            <h4>发言历史 · {r.name}</h4>
                            <div className="history-list">
                              {rows
                                .filter((x) => x.name === r.name)
                                .slice(0, 5)
                                .map((x, i) => (
                                  <div className="history-item" key={i}>
                                    <span className="tm">{x.time}</span>
                                    <span>{x.content}</span>
                                  </div>
                                ))}
                            </div>
                            <h4>私信内容</h4>
                            <div style={{ fontSize: 13 }}>
                              {r.dmStatus === "un" ? (
                                <span className="blank">尚未对该发言人发送私信</span>
                              ) : (
                                <span>
                                  <Pill c={DM_META[r.dmStatus][1]}>
                                    {DM_META[r.dmStatus][0]}
                                  </Pill>
                                  　{r.dmText || "（文案未填写）"}
                                  {r.dmTime ? "　·　" + r.dmTime : ""}
                                </span>
                              )}
                            </div>
                          </div>
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </motion.div>
  );
}