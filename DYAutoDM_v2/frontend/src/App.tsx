/**
 * App 根组件 —— 1:1 迁移自 DY_Spider_base/web/app.js。
 *
 * 结构：TopNav（浮动玻璃胶囊导航）+ AnimatePresence 页面切换 + Toast。
 * overview 用 React Query 3s 轮询（替代旧版 setInterval）。
 * Tauri 模式下首次查询会触发 ensureBackendReady 自动拉起 sidecar。
 *
 * 设计风格移植（2026-09-11）：顶栏由通栏 sticky 改为浮动玻璃胶囊 + 滑动指示器，
 * 原内联 Header 已抽成 ./components/TopNav.tsx（移植自 zn0wii/satelite-proxy）。
 */
import { useState, useCallback, useRef, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import { api, PageProps, ReviewPayload, ReusePayload } from "./api/client";
import { TABS } from "./components/ui";
import OverviewPage from "./pages/overview";
import CrawlPage from "./pages/crawl";
import LivePage from "./pages/live";
import MessagesPage from "./pages/messages";
import AiPage from "./pages/ai";
import KbPage from "./pages/kb";
import AccountsPage from "./pages/accounts";
import TasksPage from "./pages/tasks";
import SettingsPage from "./pages/settings";
import NotifyPage from "./pages/notify";
import LogsPage from "./pages/logs";
import SelfCheckModal, { SelfCheckItem } from "./components/SelfCheckModal";
import MemberGate from "./components/MemberGate";
import TopNav from "./components/TopNav";
import { memberApi, getMemberToken } from "./api/client";

type TabId = (typeof TABS)[number][0];

/** 启动闪屏：双击 exe 后窗口立即出现品牌页，后端引擎就绪（overview 首帧数据到达）
 *  才滑入主界面，把 PyInstaller 后端冷启动的 ~3s 变成有进度的等待，而不是白屏/未连接。 */
function _verDiag(msg: string) {
  try { console.warn("[VERSION] " + msg); } catch { /* ignore */ }
}

function BootSplash({ onSkip }: { onSkip?: () => void }) {
  const [showSkip, setShowSkip] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setShowSkip(true), 8000);
    return () => clearTimeout(t);
  }, []);
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
      {showSkip && onSkip && (
        <button className="btn" onClick={onSkip} style={{ marginTop: 8, fontSize: 12.5 }}>
          等待过久？点此直接进入
        </button>
      )}
    </div>
  );
}

export default function App() {
  // ===== 会员门禁（v0.37.0）：未登录不渲染任何业务 UI =====
  // 启动诊断：每次渲染打印门状态（写文件，便于脱离 DevTools 核查）
  useEffect(() => {
    try {
      const w = (window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__;
      if (w) {
        import("@tauri-apps/api/core").then(({ invoke }) => {
          invoke("write_boot_log", {
            text: `[render] memberName=${memberName} memberChecked=${memberChecked} prealigned=${prealigned} ready=${ready} overviewEverOk=${overviewEverOk}`,
          }).catch(() => {});
        });
      }
    } catch { /* ignore */ }
  });
  const [memberName, setMemberName] = useState<string | null>(null);
  const [memberChecked, setMemberChecked] = useState(false);
  // 启动预对齐门（2026-09-08 用户要求）：后端+守护全部就绪才放行登录框，
  // 登录后立即能用 —— 消灭「登录了还要等对齐」的体验断层。
  const [prealigned, setPrealigned] = useState(false);
  const [overviewEverOk, setOverviewEverOk] = useState(false);

  // 版本一致性校验（2026-09-13 用户要求）：
  // 前后端分别构建部署，必须显式比对，杜绝「前端新/后端旧」静默不一致。
  const [verMismatch, setVerMismatch] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const run = async () => {
      try {
        const v = await api.checkVersionConsistency();
        if (!alive) return;
        if (!v.match && v.backend !== "unknown") {
          setVerMismatch(`版本不一致：前端 ${v.frontend} / 后端 ${v.backend} — ${v.detail}`);
          _verDiag("mismatch " + v.detail);
        } else if (v.backend === "unknown") {
          _verDiag("backend version unknown: " + v.detail);
        }
      } catch { /* ignore */ }
    };
    const t = setTimeout(run, 1200);
    return () => { alive = false; clearTimeout(t); };
  }, []);

  // 启动预对齐轮询（1.5s）：等 /api/ready 的 daemons_ready=true
  useEffect(() => {
    let alive = true;
    const poll = async () => {
      if (prealigned || memberName) return; // 已对齐/已登录：停止轮询，杜绝状态回退
      const r = await api.getReadyGate();
      if (!alive) return;
      if (r.daemons_ready) { setPrealigned(true); return; }
      setTimeout(poll, 1500);
    };
    poll();
    return () => { alive = false; };
  }, [prealigned, memberName]);

  // 会话有效性轮询（30s）：token 失效（后端重启/过期）自动回登录页
  // 2026-09-08 修复：原实现无 try/catch —— memberApi.state() 在 backend 刚起、
  // 网络未通时抛错，memberChecked 永远停在 false，主界面/登录框被全屏
  // BootSplash 永久盖住（用户体感「一直显示正在唤醒后端引擎」）。
  // 改为：失败重试（1.5s），最长 20s 兜底放行登录框。
  useEffect(() => {
    let alive = true;
    const started = Date.now();
    const check = async () => {
      try {
        if (!getMemberToken()) {
          if (alive) { setMemberName(null); setMemberChecked(true); }
          return;
        }
        const s = await memberApi.state();
        if (!alive) return;
        if (s.loggedIn && s.username) setMemberName(s.username);
        else { setMemberName(null); setMemberChecked(true); }
      } catch {
        if (!alive) return;
        if (Date.now() - started < 20000) {
          setTimeout(check, 1500);
        } else {
          setMemberName(null);
          setMemberChecked(true);
        }
      }
    };
    check();
    const t = setInterval(check, 30000);
    return () => { alive = false; clearInterval(t); };
  }, []);

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
  // 2026-09-13 用户反馈修复：「更新会话状态不持续，切到其他页面回来就丢」。
  // 原因：refreshing 原本是 messages.tsx 组件内 useState —— 切页即卸载组件，
  // 状态归零，回来显示成「未在更新」。现提升到 App 级（App 全程常驻）：
  // 切页不丢，且 startedAt 让回来时能继续正确计时。
  const [refreshState, setRefreshState] = useState<{
    account: string; startedAt: number;
  } | null>(null);
  const cidRef = useRef(0);

  // overview 3s 轮询（替代旧版 setInterval；Tauri 模式首次触发 ensureBackendReady）
  const { data: overview, isSuccess: ready } = useQuery({
    queryKey: ["overview"],
    queryFn: async () => {
      try {
        const r = await api.getOverview();
        try {
          const w = (window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__;
          if (w) {
            import("@tauri-apps/api/core").then(({ invoke }) => {
              invoke("write_boot_log", { text: "[overview.OK] " + JSON.stringify(r).slice(0, 300) }).catch(() => {});
            });
          }
        } catch { /* ignore */ }
        return r;
      } catch (e) {
        try {
          const w = (window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__;
          if (w) {
            import("@tauri-apps/api/core").then(({ invoke }) => {
              invoke("write_boot_log", { text: `[overview.ERROR] ${String(e)}` }).catch(() => {});
            });
          }
        } catch { /* ignore */ }
        throw e;
      }
    },
    refetchInterval: 3000,
  });

  useEffect(() => {
    if (ready && !overviewEverOk) setOverviewEverOk(true);
  }, [ready, overviewEverOk]);

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

  // ===== IM 网关待授权轮询（v0.38.5）：导航角标 + 新待审 toast 提醒 =====
  const gwQ = useQuery({
    queryKey: ["notify-gateway"],
    queryFn: () => api.gatewayOverview(),
    enabled: ready,
    refetchInterval: 20_000,
  });
  const gwPendingCount = gwQ.data?.mode === "pairing" ? (gwQ.data?.pending || []).length : 0;
  const gwSeen = useRef<Set<string>>(new Set());
  const gwInitialized = useRef(false);
  useEffect(() => {
    const list = gwQ.data?.pending || [];
    if (!gwInitialized.current) {
      gwInitialized.current = true;
      gwSeen.current = new Set(list.map((p) => p.key));
      return;
    }
    for (const p of list) {
      if (!gwSeen.current.has(p.key)) {
        gwSeen.current.add(p.key);
        push(`🔔 收到来自「${p.channel_id}」的新消息，待授权甄别（设置 → 通知与指令）`, 8000);
      }
    }
  }, [gwQ.data, push]);

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
    refreshState,
    setRefreshState,
  }; 

  // ===== 会员门禁 + 启动预对齐门（2026-09-08）：对齐完成才显示登录框 =====
  if (!memberName) {
    // 预对齐未完成才盖闪屏；已完成则必须放行登录框（不再受 ready 影响）
    if (!prealigned) return <BootSplash onSkip={() => { setPrealigned(true); setMemberChecked(true); }} />;
    return memberChecked ? (
      <MemberGate onLogin={(u) => { setMemberName(u); setPrealigned(true); }} />
    ) : (
      <BootSplash onSkip={() => setMemberChecked(true)} />
    );
  }

  return (
    <div className="app">
      {/* 版本不一致告警条（2026-09-13）：前端/后端 sidecar 版本必须一致。
          用户明确要求 —— 不许"前端新版本、后端旧版本"静默存在。 */}
      {verMismatch && (
        <div style={{
          background: "#7f1d1d", color: "#fff", padding: "6px 12px",
          fontSize: 12, fontFamily: "monospace", zIndex: 9999,
          position: "sticky", top: 0,
        }}>
          ⚠️ {verMismatch}（请重新打包部署 sidecar）
        </div>
      )}
      {/* 2026-09-08：登录后不再用全屏闪屏盖住主界面。
          原逻辑 `!ready && <BootSplash />` 在 overview 首帧未到（最多 3s）或查询
          偶发失败时会把主界面整个盖住，用户体感「登录后一直转圈不消失」。
          改为：仅当 overview 从未成功过（首次）才盖；之后主界面直接呈现，
          连接状态由 Header 徽章表达（不阻塞操作）。 */}
      {/* 登录后不再用全屏闪屏阻塞：ready 依赖 overview（需登录态），
          未登录时必然 401 → 闪屏盖住 → 无法登录的死循环。
          连接状态改由 Header 徽章实时表达。 */}
      <TopNav tab={tab} setTab={setTab} ready={ready}
        memberName={memberName} gwPending={gwPendingCount}
        onLogout={async () => { await memberApi.logout(); setMemberName(null); }} />
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
            {tab === "ai" && <AiPage {...pageProps} />}
            {tab === "kb" && <KbPage {...pageProps} />}
            {tab === "accounts" && <AccountsPage {...pageProps} />}
            {tab === "tasks" && <TasksPage {...pageProps} />}
            {tab === "settings" && <SettingsPage {...pageProps} />}
            {tab === "notify" && <NotifyPage {...pageProps} />}
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
