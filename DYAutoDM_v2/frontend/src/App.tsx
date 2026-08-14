/**
 * App 根组件 —— 1:1 迁移自 DY_Spider_base/web/app.js。
 *
 * 结构：Header（导航 + 4 状态徽章）+ AnimatePresence 页面切换 + Toast。
 * overview 用 React Query 3s 轮询（替代旧版 setInterval）。
 * Tauri 模式下首次查询会触发 ensureBackendReady 自动拉起 sidecar。
 */
import { useState, useCallback, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { api, Overview, PageProps } from "./api/client";
import { TABS, Dot } from "./components/ui";
import OverviewPage from "./pages/overview";
import CrawlPage from "./pages/crawl";
import LivePage from "./pages/live";
import MessagesPage from "./pages/messages";
import AccountsPage from "./pages/accounts";
import TasksPage from "./pages/tasks";
import SettingsPage from "./pages/settings";

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
  const cidRef = useRef(0);

  // overview 3s 轮询（替代旧版 setInterval；Tauri 模式首次触发 ensureBackendReady）
  const { data: overview, isSuccess: ready } = useQuery({
    queryKey: ["overview"],
    queryFn: api.getOverview,
    refetchInterval: 3000,
  });

  const setTab = useCallback((t: string) => {
    setTabState(t as TabId);
    try {
      localStorage.setItem("dy:tab", t);
    } catch {
      /* ignore */
    }
  }, []);

  const push = useCallback((msg: string) => {
    const id = ++cidRef.current;
    setToasts((ts) => [...ts.slice(-2), { id, msg }]);
    setTimeout(() => setToasts((ts) => ts.filter((x) => x.id !== id)), 2600);
  }, []);

  const goMsg = useCallback((name: string, text?: string) => {
    setGoDm({ name, text: text || "" });
    setTab("msg");
  }, [setTab]);

  // 页面公共 props（1:1 对应旧版 app.js 传给页面的 props）
  const pageProps: PageProps = { push, api, overview: overview ?? null, ready, goMsg, goDm };

  return (
    <div className="app">
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
    </div>
  );
}
