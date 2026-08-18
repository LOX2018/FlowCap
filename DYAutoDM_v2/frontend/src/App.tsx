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
  const [goReview, setGoReview] = useState<ReviewPayload | null>(null);
  const [goReuse, setGoReuse] = useState<ReusePayload | null>(null);
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
    queryKey: ["task-history"],
    queryFn: async () => {
      const r = (await api.getTaskHistory()) as { ok: boolean; list?: unknown[] };
      return r && r.ok ? r.list || [] : [];
    },
    refetchInterval: 10000,
    enabled: ready,
  });

  // ===== 启动自检：打开时对所有账号跑双引擎校验（wp 凭证守护 + dm 私信列表拉取）=====
  const [selfCheckOpen, setSelfCheckOpen] = useState(false);
  const [selfCheckLoading, setSelfCheckLoading] = useState(true);
  const [selfCheckItems, setScItems] = useState<SelfCheckItem[]>([]);
  const ranSelfCheck = useRef(false);

  useEffect(() => {
    if (!ready || ranSelfCheck.current) return; // 后端已连且只跑一次
    ranSelfCheck.current = true;
    setSelfCheckLoading(true);
    api
      .selfCheck()
      .then((d) => {
        const items = (d.items || []) as SelfCheckItem[];
        const bad = items.filter(
          (it) =>
            (it.wp && ["fail", "error", "unknown"].includes(it.wp.level)) ||
            (it.dm && ["fail", "error", "unknown"].includes(it.dm.level)),
        );
        setScItems(items);
        setSelfCheckLoading(false);
        // 仅当存在异常账号时才弹窗，正常情况静默运行
        if (bad.length > 0) {
          setSelfCheckOpen(true);
        }
      })
      .catch(() => {
        // 自检接口失败不阻塞使用，仅静默关闭
        setSelfCheckLoading(false);
        setSelfCheckOpen(false);
      });
  }, [ready]);

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
  }; 

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
        onClose={() => setSelfCheckOpen(false)}
        push={push}
      />
    </div>
  );
}
