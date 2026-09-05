/**
 * App 根组件 —— 1:1 迁移自 DY_Spider_base/web/app.js。
 *
 * 结构：Header（导航 + 4 状态徽章）+ AnimatePresence 页面切换 + Toast。
 * overview 用 React Query 3s 轮询（替代旧版 setInterval）。
 * Tauri 模式下首次查询会触发 ensureBackendReady 自动拉起 sidecar。
 */
import { useState, useCallback, useRef, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { api, Overview, PageProps, ReviewPayload, ReusePayload } from "./api/client";
import { TABS, Dot } from "./components/ui";
import OverviewPage from "./pages/overview";
import CrawlPage from "./pages/crawl";
import LivePage from "./pages/live";
import MessagesPage from "./pages/messages";
import AccountsPage from "./pages/accounts";
import TasksPage from "./pages/tasks";
import SettingsPage from "./pages/settings";
import LogsPage from "./pages/logs";
import SelfCheckModal, { SelfCheckItem } from "./components/SelfCheckModal";

type TabId = (typeof TABS)[number][0];

/** 导航栏 + 状态徽章（1:1 迁移自 framework.js Header） */
function Header({
  tab,
  setTab,
  overview,
  ready,
}: {
  tab: string;
  setTab: (t: string) => void;
  overview: Overview | null | undefined;
  ready: boolean;
}) {
  const ov = overview || ({} as Partial<Overview>);
  const running = !!ov.running;
  const paused = !!ov.paused;
  const statusColor = !ready ? "mute" : running ? (paused ? "warn" : "ok") : "danger";
  const statusText = !ready ? "未连接" : running ? (paused ? "已暂停" : "运行中") : "已停止";
  const bd = ov.browserDaemon || { alive: false };
  const rd = ov.recvDaemon || { alive: false };

  return (
    <header className="nav">
      <div className="brand">
        <span className="mark" aria-hidden="true" />
        <h1>抖音数据控制台</h1>
        <span className="sub">Douyin Console</span>
        {/* 2026-08-31：显示版本号。
            排查「改了代码但界面没变」时，第一件事就是确认跑的是哪个版本 ——
            之前因为看不到版本号，反复误判为"缓存问题"。 */}
        <span className="appver" title="应用版本">
          v{__APP_VERSION__}
        </span>
      </div>
      <nav className="tabs" aria-label="主导航">
        {TABS.map(([id, label]) => (
          <button
            key={id}
            className={"tab" + (tab === id ? " active" : "")}
            onClick={() => setTab(id)}
          >
            {label}
          </button>
        ))}
      </nav>
      <div className="nav-status">
        <span className="badge-conn">
          <Dot c={statusColor} pulse={running && !paused} /> 引擎 <b>{statusText}</b>
        </span>
        <span className="badge-conn">
          <Dot c={bd.alive ? "ok" : "danger"} pulse={bd.alive} /> 凭证守护{" "}
          <b>{bd.alive ? (bd.signReady ? "已就绪" : "登录中") : "离线"}</b>
        </span>
        <span className="badge-conn">
          <Dot c={rd.alive ? "ok" : "danger"} pulse={rd.alive} /> 私信守护{" "}
          <b>{rd.alive ? "在线" : "离线"}</b>
        </span>
        {ready && (
          <span className="badge-conn">
            <b>
              已发 {ov.sent}/{ov.limit}
              {ov.queue ? ` · 待发 ${ov.queue}` : ""}
            </b>
          </span>
        )}
        {!ready && <span className="demo-tag">未连接</span>}
      </div>
    </header>
  );
}

/** 启动闪屏：双击 exe 后窗口立即出现品牌页，后端引擎就绪（overview 首帧数据到达）
 *  才滑入主界面，把 PyInstaller 后端冷启动的 ~3s 变成有进度的等待，而不是白屏/未连接。 */
function BootSplash() {
  return (
    <div
      style={{
        position: "fixed",
        inset: 0,
        zIndex: 9999,
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 16,
        background: "var(--bg)",
        // 等后端就绪前，主界面藏在闪屏之下
      }}
    >
      <div
        style={{
          width: 64,
          height: 64,
          borderRadius: 16,
          background: "var(--accent)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 30,
          color: "#fff",
          fontWeight: 700,
        }}
      >
        DY
      </div>
      <div style={{ fontSize: 16, fontWeight: 600 }}>抖音数据控制台</div>
      <div className="spinner" style={{ marginTop: 4 }} />
      <div style={{ color: "var(--muted)", fontSize: 12.5 }}>正在唤醒后端引擎并准备数据…</div>
    </div>
  );
}

export default function App() {
  const [tab, setTabState] = useState<TabId>(() => {
    try {
      return (localStorage.getItem("dy:tab") as TabId) || "overview";
    } catch {
      return "overview";
    }
  });
  const [toasts, setToasts] = useState<{ id: number; msg: string }[]>([]);
  const [goDm, setGoDm] = useState<{ name: string; text: string } | null>(null);
  const [goReview, setGoReview] = useState<ReviewPayload | null>(null);
  const [goReuse, setGoReuse] = useState<ReusePayload | null>(null);
  const [msgAcct, setMsgAcct] = useState<string>("");
  const cidRef = useRef(0);

  // overview 3s 轮询（替代旧版 setInterval；Tauri 模式首次触发 ensureBackendReady）
  const { data: overview, isSuccess: ready } = useQuery({
    queryKey: ["overview"],
    queryFn: api.getOverview,
    refetchInterval: 3000,
  });

  // ===== 共享数据常驻轮询（「前端启动拉一次、后端数据一直热着」）=====
  // App 永不卸载，这些查询一直订阅刷缓存；各页面用相同 key 读缓存，
  // 切页不再向后端重拉，消灭 4s/3s 的等待（尤其是 accounts 的重型校验）。
  useQuery({
    queryKey: ["accounts"],
    queryFn: async () => (await api.getAccounts()) as never[],
    refetchInterval: 30000,
    enabled: ready,
  });
  useQuery({
    queryKey: ["live-tasks"],
    queryFn: async () => api.getTasks(),
    refetchInterval: 5000,
    enabled: ready,
  });
  useQuery({
    queryKey: ["live-stream"],
    queryFn: async () => api.getStream(),
    refetchInterval: 3000,
    enabled: ready,
  });
  useQuery({
    queryKey: ["current-task"],
    queryFn: api.getCurrentTask,
    refetchInterval: 5000,
    enabled: ready,
  });
  useQuery({
    queryKey: ["task-history", 0],
    queryFn: async () => {
      const r = (await api.getTaskHistory(50, 0)) as { ok: boolean; list?: unknown[]; total?: number };
      return { list: r && r.ok ? r.list || [] : [], total: r?.total || 0 };
    },
    refetchInterval: 15000,
    enabled: ready,
  });
  // 私信会话列表常驻轮询（提升到 App，与 accounts 同机制）：
  // 切到私信页时该 query 已在内存热着，页面挂载只读缓存、不再冷拉，
  // 同时避免 React.StrictMode 双挂载 + 账号状态变化造成的 1 秒内多次读请求。
  const { data: accountsCache } = useQuery({
    queryKey: ["accounts"],
    queryFn: async () => (await api.getAccounts()) as never[],
    refetchInterval: 30000,
    enabled: ready,
  });
  useEffect(() => {
    if (!msgAcct && accountsCache && (accountsCache as unknown[]).length) {
      setMsgAcct((accountsCache as { name: string }[])[0].name);
    }
  }, [msgAcct, accountsCache]);
  // 私信会话列表轮询已移除：App 级常驻轮询会导致非私信页也每 5s 拉一次会话列表，
  // 产生大量冗余日志；切到私信页时 44 个会话同时渲染还会导致窗口崩溃。
  // 会话列表轮询由 messages.tsx 自行管理（仅在该页挂载时激活）。

  // ===== 启动自检已移除（用户要求：避免干扰日志与自动行为）=====
  // 不再打开时自动跑双引擎校验/弹窗；state 仅保留供 SelfCheckModal 渲染（恒不弹窗）。
  const [selfCheckOpen] = useState(false);
  const [selfCheckLoading] = useState(true);
  const [selfCheckItems] = useState<SelfCheckItem[]>([]);

  useEffect(() => {
    // no-op：启动自检已删除
  }, [ready]);

  const setTab = useCallback((t: string) => {
    setTabState(t as TabId);
    try {
      localStorage.setItem("dy:tab", t);
    } catch {
      /* ignore */
    }
  }, []);

  // 2026-08-31 修复：长任务（更新会话耗时 3~6 分钟）的结果提示原本只显示 2.6s，
  // 用户根本看不到运行结果。改为支持自定义停留时长，重要结果默认停留 12s。
  const push = useCallback((msg: string, holdMs?: number) => {
    const id = ++cidRef.current;
    setToasts((ts) => [...ts.slice(-2), { id, msg }]);
    setTimeout(
      () => setToasts((ts) => ts.filter((x) => x.id !== id)),
      holdMs ?? 2600,
    );
  }, []);

  const goMsg = useCallback((name: string, text?: string) => {
    setGoDm({ name, text: text || "" });
    setTab("msg");
  }, [setTab]);

  const goReviewTrigger = useCallback((payload: ReviewPayload) => {
    setGoReview(payload);
  }, []);

  const goReuseTrigger = useCallback((payload: ReusePayload) => {
    setGoReuse(payload);
    setTab("live");
  }, [setTab]);

  // 页面公共 props（1:1 对应旧版 app.js 传给页面的 props）
  const pageProps: PageProps = {
    push,
    api,
    overview: overview ?? null,
    ready,
    goMsg,
    goDm,
    setTab,
    goReview: goReviewTrigger,
    reviewPayload: goReview,
    goReuse: goReuseTrigger,
    reusePayload: goReuse,
    msgAcct,
    setMsgAcct,
  }; 

  return (
    <div className="app">
      {!ready && <BootSplash />}
      <Header tab={tab} setTab={setTab} overview={overview} ready={ready} />
      <main className="main">
        <AnimatePresence mode="wait">
          <motion.div
            key={tab}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.16 }}
          >
            {tab === "overview" && <OverviewPage {...pageProps} />}
            {tab === "crawl" && <CrawlPage {...pageProps} />}
            {tab === "live" && <LivePage {...pageProps} />}
            {tab === "msg" && <MessagesPage {...pageProps} />}
            {tab === "accounts" && <AccountsPage {...pageProps} />}
            {tab === "tasks" && <TasksPage {...pageProps} />}
            {tab === "settings" && <SettingsPage {...pageProps} />}
            {tab === "logs" && <LogsPage {...pageProps} />}
          </motion.div>
        </AnimatePresence>
      </main>
      <div className="toasts">
        {toasts.map((t) => (
          <div className="toast" key={t.id}>
            {t.msg}
          </div>
        ))}
      </div>
      <SelfCheckModal
        open={selfCheckOpen}
        items={selfCheckItems}
        loading={selfCheckLoading}
        onClose={() => { /* 启动自检已移除，弹窗恒不开启 */ }}
        push={push}
      />
    </div>
  );
}
