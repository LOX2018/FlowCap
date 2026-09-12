/**
 * 总览页
 *
 * 迁移自: DY_Spider_base/web/pages/overview.js
 * 原版职责: 显示引擎状态、守护状态、已发/上限、实时动态、账号概况
 * 迁移要点:
 *   - 旧版自行 setInterval 轮询 overview → 改用 props.overview（App 已 3s 轮询传入）
 *   - accounts / stats 仍 3s 轮询，改用 React Query useQuery
 *   - React.createElement → JSX
 *   - 删除 TASKS_INIT / feed 等假数据，loading 用 .sk 骨架屏
 *   - r.status 中文字符串比较 → 后端英文枚举 'sent'
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { PageProps, Overview } from "../api/client";
import { Avatar, Pill, KIND_NAME } from "../components/ui";

/** 后端 overview 实际带 liveUrl/status 字段（client.ts 精简类型未覆盖），本地扩展 */
type OverviewExt = Overview & { liveUrl?: string; status?: string };

interface StatsItem {
  captureTs?: string;
  status?: string;
  nickname?: string;
  comment?: string;
  content?: string;
}
interface StatsResp {
  ok?: boolean;
  total: number;
  sent: number;
  list?: StatsItem[];
}
interface Account {
  name: string;
  uid?: string;
  isCurrent?: boolean;
  loggedIn?: boolean;
  signReady?: boolean;
  isMonitor?: boolean;
  isSender?: boolean;
}

interface FeedItem {
  id: number;
  t: string;
  k: string;
  n: string;
  l: number;
  x: string;
}

interface StatCard {
  label: string;
  num: string;
  delta: string;
  color: string;
}

export default function OverviewPage(props: PageProps) {
  const { push, api, overview, ready } = props;
  const [viewMode, setViewMode] = useState<"single" | "grid">("single");
  const [activeAcct, setActiveAcct] = useState("");
  const ov = (overview || ({} as OverviewExt)) as OverviewExt;

  // 账号列表（读取 App 常驻轮询的共享缓存；getAccounts 返回数组，勿再用 old wrapper 解包）
  const accountsQ = useQuery({
    queryKey: ["accounts"],
    queryFn: async (): Promise<Account[]> => {
      const d = (await api.getAccounts()) as unknown as Account[];
      return Array.isArray(d) ? d : [];
    },
    enabled: !!ready,
  });
  const statsQ = useQuery({
    queryKey: ["overview-stats"],
    queryFn: async (): Promise<StatsResp | null> => {
      const d = (await api.getStats()) as unknown as StatsResp;
      return d && d.ok ? d : null;
    },
    refetchInterval: 3000,
    enabled: !!ready,
  });

  const accounts = accountsQ.data || [];
  const stats = statsQ.data || null;

  const realFeed: FeedItem[] = ((stats && stats.list) || [])
    .slice(0, 24)
    .map((r, i) => ({
      id: 100000 + i,
      t: (r.captureTs || "").slice(-8) || "—",
      k: r.status === "sent" ? "msg" : "danmaku",
      n: r.nickname || "未知",
      l: 0,
      x: (r.comment || "") + (r.content ? " → 私信: " + r.content : ""),
    }));

  const statCards: StatCard[] | null = ready
    ? [
        {
          label: "已发私信",
          num: (ov.sent || 0).toLocaleString() + "/" + (ov.limit || 0),
          delta: "待发 " + (ov.queue || 0),
          color: "var(--accent)",
        },
        {
          label: "捕获评论",
          num: (stats ? stats.total : 0).toLocaleString() + " 条",
          delta: "已发 " + (stats ? stats.sent : 0),
          color: "var(--ok)",
        },
        {
          label: "账号",
          num: ov.browserDaemon && ov.browserDaemon.alive ? "凭证就绪" : "凭证离线",
          delta: ov.recvDaemon && ov.recvDaemon.alive ? "私信守护在线" : "私信守护离线",
          color: "var(--warn)",
        },
        {
          label: "引擎状态",
          num: ov.running ? (ov.paused ? "已暂停" : "运行中") : "已停止",
          delta: ov.status || "",
          color: "var(--accent)",
        },
      ]
    : null;

  const taskList = ready ? (
    <div className="run-item">
      <div className="top">
        <span className="nm">自动私信引擎 · {ov.liveUrl || "未配置直播间"}</span>
        <span className="pct" style={{ color: ov.running ? "var(--ok)" : "var(--muted)" }}>
          {ov.running ? (ov.paused ? "已暂停" : "运行中") : "未启动"}
        </span>
      </div>
      <div className="track">
        <div
          className="bar"
          style={{
            width:
              (ov.limit ? Math.min(100, Math.round(((ov.sent || 0) / ov.limit) * 100)) : 0) + "%",
          }}
        />
      </div>
    </div>
  ) : (
    <>
      <div className="run-item sk" style={{ height: 42 }} />
      <div className="run-item sk" style={{ height: 42 }} />
    </>
  );

  const curAcct =
    accounts.find((a) => a.name === activeAcct) ||
    accounts.find((a) => a.isCurrent) ||
    accounts[0] ||
    null;

  const gridCards = accounts.map((acct) => (
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
        <Avatar name={acct.name} h="20" />
        <div style={{ flex: 1 }}>
          <div style={{ fontWeight: 600, fontSize: 14 }}>
            {acct.name}
            {acct.isCurrent ? " · 当前" : ""}
          </div>
          <div className="mono" style={{ fontSize: 11.5, color: "var(--muted)" }}>
            UID: {acct.uid || "—"}
          </div>
        </div>
        <Pill c={acct.loggedIn ? "ok" : "danger"}>
          {acct.loggedIn ? (acct.signReady ? "已登录·签名就绪" : "已登录") : "离线"}
        </Pill>
      </div>
      <div className="grid cols-2" style={{ padding: "10px 16px", gap: 6 }}>
        <div className="card stat" style={{ padding: "6px 8px" }}>
          <span className="label" style={{ fontSize: 10 }}>
            监测角色
          </span>
          <span className="num" style={{ fontSize: 14 }}>
            {acct.isMonitor ? "是" : "否"}
          </span>
        </div>
        <div className="card stat" style={{ padding: "6px 8px" }}>
          <span className="label" style={{ fontSize: 10 }}>
            发送角色
          </span>
          <span className="num" style={{ fontSize: 14 }}>
            {acct.isSender ? "是" : "否"}
          </span>
        </div>
      </div>
      <div style={{ padding: "10px 16px", borderTop: "1px solid var(--border)" }}>
        <div className="head-row" style={{ marginBottom: 6 }}>
          <span style={{ fontSize: 12, color: "var(--muted)" }}>守护状态</span>
          <span className="mono" style={{ fontSize: 12, fontWeight: 600 }}>
            {ready && ov.browserDaemon && ov.browserDaemon.alive ? "凭证守护在线" : "守护离线"}
          </span>
        </div>
        <div className="head-row">
          <span style={{ fontSize: 12, color: "var(--muted)" }}>私信守护</span>
          <span className="mono" style={{ fontSize: 12 }}>
            {ready && ov.recvDaemon && ov.recvDaemon.alive ? "在线" : "离线"}
          </span>
        </div>
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
          查看详情
        </button>
      </div>
    </div>
  ));

  return (
    <div>
      <div className="section-head">
        <div>
          <h2>总览</h2>
          <div className="desc">系统运行状态与账号概况</div>
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
        </div>
      </div>

      {viewMode === "grid" ? (
        <div className="grid cols-2" data-od-id="overview-grid">
          {gridCards.length ? (
            gridCards
          ) : (
            <div className="feed-item" style={{ color: "var(--muted)" }}>
              暂无账号数据
            </div>
          )}
        </div>
      ) : (
        <>
          <div className="card" style={{ marginBottom: 14 }} data-od-id="overview-acct-select">
            <div className="head-row">
              <span style={{ fontSize: 13, fontWeight: 600 }}>当前查看账号</span>
              <div style={{ flex: 1 }} />
              <div className="seg">
                {accounts.map((a) => (
                  <button
                    key={a.name}
                    className={activeAcct === a.name ? "active" : ""}
                    onClick={() => {
                      setActiveAcct(a.name);
                      push("已切换到 " + a.name);
                    }}
                  >
                    <Avatar name={a.name} h="20" sm /> {a.name}
                  </button>
                ))}
              </div>
            </div>
            {curAcct && (
              <div
                className="head-row"
                style={{ marginTop: 10, fontSize: 12, color: "var(--muted)" }}
              >
                {/* 2026-09-10：UID/登录/签名/监测/发送 明细行删除（账号明细在账号管理页看） */}
              </div>
            )}
          </div>

          <div className="grid cols-4" style={{ marginBottom: 14 }}>
            {ready
              ? (statCards || []).map((c, i) => (
                  <div className="card stat" key={i} data-od-id={"stat-" + i}>
                    <span className="label">{c.label}</span>
                    <span className="num">
                      {c.num}
                      <span className="unit"> </span>
                    </span>
                    <div className="spark">
                      <span className="delta" style={{ color: c.color }}>
                        {c.delta}
                      </span>
                    </div>
                  </div>
                ))
              : [0, 1, 2, 3].map((i) => (
                  <div className="card stat sk" key={i} style={{ height: 92 }} />
                ))}
          </div>

          <div className="grid cols-2">
            <div className="card" data-od-id="overview-feed">
              <h3>
                实时动态{" "}
                <span className="demo-tag" style={{ textTransform: "none" }}>
                  {ready ? "实时" : "未连接"}
                </span>
              </h3>
              <div className="feed">
                {realFeed.map((f) => (
                  <div className="feed-item" key={f.id}>
                    <span className="tm">{f.t}</span>
                    <span className={"kind k-" + f.k}>{KIND_NAME[f.k] || f.k}</span>
                    <span className="txt">
                      <b>{f.n}</b>
                      {"\u3000"}
                      {f.x}
                    </span>
                    {f.l ? <span className="lv">Lv.{f.l}</span> : null}
                  </div>
                ))}
                {!realFeed.length && (
                  <div className="feed-item" style={{ color: "var(--muted)" }}>
                    暂无数据
                  </div>
                )}
              </div>
            </div>
            <div className="card" data-od-id="overview-tasks">
              <h3>运行中任务</h3>
              <div className="run-list">{taskList}</div>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
