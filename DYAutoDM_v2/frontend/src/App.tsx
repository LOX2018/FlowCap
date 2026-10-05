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
import { useState, useCallback, useRef, useEffect, lazy, Suspense } from "react";
import { useQuery } from "@tanstack/react-query";
// framer-motion 的页面切换动画已移入 AppShell，App 层不再直接使用
import { api, PageProps, TaskDetailPayload, ReusePayload } from "./api/client";
// 页面按视图懒加载（2026-09-15 性能优化）：
// 原实现 11 个页面全静态 import → 全部打进主 chunk（782KB），首屏要解析所有页面代码。
// 改为 React.lazy：每个页面独立 chunk，首屏只加载当前视图（overview），
// 其余按需拉取。行为不变（仍由 `tab === x && <Page/>` 条件渲染）。
// ★ ADR-034（2026-10-03）：采集功能融进「内容总览」页（PlatformPage 的一个 tab），
//   故 CrawlPage 不再是顶层路由；它由 PlatformPage 内部 import 挂载，
//   因此这里**移除**原先的 lazy 入口（否则打进主 chunk 白占体积）。
const OverviewPage = lazy(() => import("./components/overview/overview-page"));
const StatsPage = lazy(() => import("./components/stats/stats-page"));
const PlatformPage = lazy(() => import("./components/platform/platform-page"));
const LivePage = lazy(() => import("./components/live/live-page"));
const MessagesPage = lazy(() => import("./components/messages/messages-page"));
const KbPage = lazy(() => import("./components/kb/kb-page"));
const AccountsPage = lazy(() => import("./components/accounts/accounts-page"));
const TasksPage = lazy(() => import("./components/tasks/tasks-page"));
const TaskDetailPage = lazy(() => import("./components/tasks/task-detail-page"));
const SettingsPage = lazy(() => import("./components/settings/settings-page"));
const NotifyPage = lazy(() => import("./components/notify/notify-page"));
const LogsPage = lazy(() => import("./components/logs/logs-page"));
import SelfCheckModal, { SelfCheckItem } from "./components/layout/SelfCheckModal";
import { DialogHost } from "./components/ui/modal";
import { BrandMark, BRAND_NAME } from "./components/brand";
import MemberGate from "./components/layout/MemberGate";
import { AppShell } from "./components/layout/app-shell";
import { WindowControls } from "./components/layout/window-controls";
import { ErrorBoundary } from "./components/layout/ErrorBoundary";
import { type TabId } from "./components/layout/sidebar";
// 视图状态：照源项目 stores/app-store 的 currentView/setView 机制（阶段2）
import { useAppViewStore, VIEW_TITLE, type ViewType } from "./stores/app-store";
import { StatusDot } from "./components/ui/status-dot";
import { memberApi, getMemberToken } from "./api/client";
// 元素选择模式（调试工具：点击页面元素复制其结构位置，不触发功能）
const ElementInspectorButton = lazy(() => import("./lib/element-inspector").then(m => ({ default: m.ElementInspectorButton })));
// ADR-018 F6：顶栏日夜主题快捷切换（主入口仍在配置中心 → 通用配置 → 外观）
import { ThemeToggleButton } from "./components/layout/theme-toggle";

// TabId 唯一真源在 components/layout/sidebar（含导航分组）
// 旧 `type TabId = (typeof TABS)[number][0]` 已废弃 —— TABS 缺 kb/notify 两项。

/** 启动闪屏：双击 exe 后窗口立即出现品牌页，后端引擎就绪（overview 首帧数据到达）
 *  才滑入主界面，把 PyInstaller 后端冷启动的 ~3s 变成有进度的等待，而不是白屏/未连接。 */
/** 各 tab 的页面标题（TopBar 左侧显示）。 */
const TITLE_OF = (t: TabId): string =>
  VIEW_TITLE[t as ViewType] ?? "控制台";

function _verDiag(msg: string) {
  try { console.warn("[VERSION] " + msg); } catch { /* ignore */ }
}

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
        background: "var(--color-background)",
        // 等后端就绪前，主界面藏在闪屏之下
      }}
    >
      <div
        style={{
          width: 64,
          height: 64,
          borderRadius: 16,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          overflow: "hidden",
        }}
      >
        <BrandMark size={64} rounded={16} />
      </div>
      <div style={{ fontSize: 16, fontWeight: 600 }}>{BRAND_NAME}</div>
      <div
        className="mt-1 h-5 w-5 animate-spin rounded-full border-2
                   border-[var(--color-border)] border-t-[var(--color-accent)]"
      />
      <div style={{ color: "var(--color-text-muted)", fontSize: 12.5 }}>正在唤醒后端引擎并准备数据…</div>
    </div>
  );
}

export default function App() {
  // ===== 会员门禁（v0.37.0）：未登录不渲染任何业务 UI =====
  const [memberName, setMemberName] = useState<string | null>(null);
  const [memberChecked, setMemberChecked] = useState(false);
  // 启动预对齐门（2026-09-08 用户要求）：后端+守护全部就绪才放行登录框，
  // 登录后立即能用 —— 消灭「登录了还要等对齐」的体验断层。
  const [prealigned, setPrealigned] = useState(false);
  const [overviewEverOk, setOverviewEverOk] = useState(false);

  // 版本一致性校验（2026-09-13 用户要求）：
  // 前后端分别构建部署，必须显式比对，杜绝「前端新/后端旧」静默不一致。
  // 2026-09-16 铁律升级：版本不一致 → **阻断启动**（非 null 即全屏遮罩，
  // 不渲染 AppShell）。旧实现只塞一条提示字符串，用户仍可继续操作，
  // 导致「前端新 / 后端旧」组合跑出 ModuleNotFoundError 等诡异故障。
  const [verBlock, setVerBlock] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const timers: ReturnType<typeof setTimeout>[] = [];
    // 2026-09-17 修复（v0.43.43）：探针失败后重试，而不是单次失败永久阻断。
    // 失败分两类：
    //   「不可恢复」→ 版本真不一致 / 后端缺 backend 字段（探针缺失）→ 立即阻断，不重试。
    //   「可重试」→ 后端引擎尚未就绪 / 网络瞬断 → 5s 后重探，共 3 轮，
    //     3 轮仍读不到才阻断（此时真值得拦下）。
    const isRetryable = (d: string) =>
      d.includes("未就绪") || d.includes("无法连接后端") || d.includes("返回 5") || d.includes("返回 0");
    const run = async () => {
      try {
        const v = await api.checkVersionConsistency();
        if (!alive) return;
        if (v.match) { _verDiag("ok " + v.backend); return; }
        const why = v.backend === "unknown"
          ? `后端版本无法读取（${v.detail}）`
          : `前端 ${v.frontend} ≠ 后端 ${v.backend} — ${v.detail}`;
        if (isRetryable(v.detail)) {
          _verDiag("RETRYABLE " + why);
          if (timers.length < 3) {
            const t = setTimeout(() => { if (alive) run(); }, 5000);
            timers.push(t);
            return;
          }
        }
        _verDiag("BLOCKED " + why);
        setVerBlock(why);
      } catch (e) {
        _verDiag("PROBE_ERROR " + String(e));
      }
    };
    // 首次延迟 1.2s（让 BootSplash 先渲染），后续重试由上方调度。
    const t0 = setTimeout(run, 1200);
    timers.push(t0);
    return () => { alive = false; timers.forEach(clearTimeout); };
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

  // 视图状态（照源项目 components-first + store 机制；持久化键与原实现一致）
  const tab = useAppViewStore((s) => s.currentView) as TabId;
  const setTabState = useAppViewStore((s) => s.setView);
  const [toasts, setToasts] = useState<{ id: number; msg: string }[]>([]);
  const [goDm, setGoDm] = useState<{ name: string; text: string } | null>(null);
  // ★ 2026-10-04 留资线索「跳转原文」：按 conv_id 精确跳转到指定会话。
  // `key` 用于保证**重复点击同一会话**也能触发（state 值不变则 useEffect 不重跑）。
  const [goConvReq, setGoConvReq] = useState<{ convId: string; key: number } | null>(null);
  const [goDetail, setGoDetail] = useState<TaskDetailPayload | null>(null);
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

  // overview 轮询的日志去重（2026-09-28 · DSSCC-UI-007 第二半）：refetchInterval=3000
  // ⇒ 每 3s 就写一次；未登录时 401 长期持续 ⇒ **[overview.ERROR] 每 3s 一行**，
  // 实测 1 分钟 30 行 = 仍是无界增长（原 [render] 风暴的同类）。
  // 判据：只记**内容变化**（OK 载荷 / 错误文本），相同内容不重复写。
  const lastOvLogRef = useRef<string>("");
  const writeOverviewLog = (tag: "OK" | "ERROR", body: string) => {
    const line = `${tag}:${body}`;
    if (lastOvLogRef.current === line) return; // 内容未变 ⇒ 不写（去重）
    lastOvLogRef.current = line;
    try {
      const w = (window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__;
      if (w) {
        import("@tauri-apps/api/core").then(({ invoke }) => {
          invoke("write_boot_log", { text: `[overview.${tag}] ${body}` }).catch(() => {});
        });
      }
    } catch { /* ignore */ }
  };

  // overview 3s 轮询（替代旧版 setInterval；Tauri 模式首次触发 ensureBackendReady）
  const { data: overview, isSuccess: ready } = useQuery({
    queryKey: ["overview"],
    queryFn: async () => {
      try {
        const r = await api.getOverview();
        // 去重后只记**内容变化**（否则每 3s 写一行 = 无界增长）
        writeOverviewLog("OK", JSON.stringify(r).slice(0, 300));
        return r;
      } catch (e) {
        writeOverviewLog("ERROR", String(e));
        throw e;
      }
    },
    refetchInterval: 3000,
  });

  // 启动诊断：**门状态变化时**写一行（写文件，便于脱离 DevTools 核查）。
  // 🔴 2026-09-28 修（DSSCC-UI-007）：原实现**漏了依赖数组**（`});` 结尾）⇒
  //   React 每次渲染都写一行 —— 实测累积 **281,595 行 / 31 MB**
  //   （`frontend_boot.log`），既吃磁盘又淹没真实日志。诊断的语义本就是
  //   「记录门状态**变迁**」，故按依赖数组只在上述状态量变化时写。
  //   ⚠️ 本 effect 必须**置于全部依赖声明之后**（含 `ready`，它由下方 useQuery
  //   解构而来）：依赖数组在渲染期求值，写在其上方会因 const 的 TDZ 抛
  //   `Cannot access 'ready' before initialization`（tsc TS2448 已实证）。
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
  }, [memberName, memberChecked, prealigned, ready, overviewEverOk]);

  useEffect(() => {
    if (ready && !overviewEverOk) setOverviewEverOk(true);
  }, [ready, overviewEverOk]);

  // ===== 共享数据常驻轮询（「前端启动拉一次、后端数据一直热着」）=====
  // App 永不卸载，这些查询一直订阅刷缓存；各页面用相同 key 读缓存，
  // 切页不再向后端重拉，消灭 4s/3s 的等待（尤其是 accounts 的重型校验）。
  const { data: accountsCache } = useQuery({
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
  useEffect(() => {
    if (!msgAcct && accountsCache && (accountsCache as unknown[]).length) {
      setMsgAcct((accountsCache as { name: string }[])[0].name);
    }
  }, [msgAcct, accountsCache]);

  // ===== 启动自检已移除（用户要求：避免干扰日志与自动行为）=====
  // 不再打开时自动跑双引擎校验/弹窗；state 仅保留供 SelfCheckModal 渲染（恒不弹窗）。
  const [selfCheckOpen] = useState(false);
  const [selfCheckLoading] = useState(true);
  const [selfCheckItems] = useState<SelfCheckItem[]>([]);

  useEffect(() => {
    // no-op：启动自检已删除
  }, [ready]);

  // 切换视图（签名与行为不变：含 localStorage 持久化，已由 store 承担）
  // 2026-10-05：第二参 = 页内二级分区落点（此前只有一级，
  // settings 页恒定落 general ⇒ 总览「去巡检」文案与落点不符）。
  const [settingsSection, setSettingsSection] = useState<string>();
  const setTab = useCallback((t: string, section?: string) => {
    setTabState(t);
    if (t === "settings") setSettingsSection(section);
  }, [setTabState]);

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

  // ★ 2026-10-04 留资线索「跳转原文」：按 conv_id 精确跳转（不按昵称 —— 昵称
  // 可能是裸 UID，goMsg 的 `find(c => c.name === name)` 会匹配不到）。
  const goConv = useCallback((convId: string) => {
    setGoConvReq({ convId, key: Date.now() });
    setTab("msg");
  }, [setTab]);

  const goDetailTrigger = useCallback((payload: TaskDetailPayload) => {
    setGoDetail(payload);
    setTab("taskdetail");
  }, [setTab]);

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
        push(`🔔 收到来自「${p.channel_id}」的新消息，待授权甄别（配置中心 → 通知与指令）`, 8000);
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
    goConv,
    goConvReq,
    setTab,
    initialSection: settingsSection,
    goDetail: goDetailTrigger,
    detailPayload: goDetail,
    goReuse: goReuseTrigger,
    reusePayload: goReuse,
    msgAcct,
    setMsgAcct,
    refreshState,
    setRefreshState,
  }; 

  // ===== 会员门禁 + 启动预对齐门（2026-09-08）：后端就绪后才显示登录框 =====
  // 2026-09-18 修复（循环依赖重大缺陷）：
  //   旧实现 v0.43.44 把「登录框」的渲染门控加了 `|| !ready`，而 `ready` 取自
  //   `/api/overview` 的 isSuccess。问题：**/api/overview 需要登录**（会员门禁 401）
  //   → 形成循环依赖：未登录 → overview 失败无法 ready → 登录框不渲染 → 无法登录。
  //   用户侧：未登录(会话过期/首次装) 的情况下永远卡在 BootSplash
  //   「正在唤醒后端引擎」。
  //   为何 v0.43.44 验证时没暴露：当时会话有效（自动登录），overview 成功
  //   → 看着正常；**只有会话过期**才暴露 —— 典型的「只测顺利路径」假通过。
  //
  //   正解：登录框的「后端就绪」判据应用**免鉴权**的 `prealigned`
  //   （来自 `/api/ready`，其 docstring 明写它就是「前端等 daemons_ready=true 才
  //   显示登录框」的就绪针，且内部已分未登录分支）。
  //   `ready`（overview）仅用于登录**后**的业务 UI 与顶栏状态，不参与登录门控。
  if (!memberName) {
    // prealigned 未完成 → 持续闪屏（**不得引用 ready**：它需要登录）
    const gate = !prealigned
      ? <BootSplash />
      : memberChecked
        ? <MemberGate onLogin={(u) => { setMemberName(u); setPrealigned(true); }} />
        : <BootSplash />;
    // 元素选择模式入口在此**一并渲染** —— 登录门/闪屏阶段也要能定位元素
    // （2026-09-19 用户定调：入口必须是全局顶层悬浮，不得被登录门挡住）。
    return (
      <>
        {gate}
        <Suspense fallback={null}>
          <ElementInspectorButton />
        </Suspense>
        {/* 登录门/闪屏阶段也要能操作窗口（与主界面同一常驻层）。 */}
        <WindowControls />
      </>
    );
  }

  // ===================================================================
  // 渲染 —— 新版外壳（对标 better-douyin）
  //   旧：顶部横向 Tab（TopNav）+ <main className="main">
  //   新：侧栏 Sidebar（三组业务编排）+ TopBar（拖拽/状态/窗口控制）+ AppShell
  //   注意：全部业务页面、props、轮询、toast、会员门禁逻辑**保持不变**，
  //         本次只替换 chrome（外壳）。
  // ===================================================================
  return (
    <>
      {/* 版本不一致 —— 阻断式门禁（2026-09-16 用户铁律：禁止启动）。
          旧实现只挂一条告警条，用户仍可继续操作 ⇒ 「前端新/后端旧」的
          组合照样跑出各种诡异行为（如旧 sidecar 缺新模块直接
          ModuleNotFoundError）。改为：不一致时**全屏遮罩、不渲染
          AppShell**，用户只能看到错误 + 处理办法。 */}
      {verBlock ? (
        <div className="fixed inset-0 z-[var(--z-blocker)] flex items-center justify-center
                        bg-[var(--color-background)] px-6">
          <div className="modal-surface w-full max-w-lg rounded-[var(--radius-lg)] p-6
                          border-[color-mix(in_srgb,var(--color-danger)_45%,transparent)]">
            <div className="mb-3 flex items-center gap-2">
              <span className="text-2xl">⛔</span>
              <h1 className="text-lg font-semibold text-[var(--color-danger)]">
                版本不一致，已阻止启动
              </h1>
            </div>
            <p className="mb-4 font-mono text-[13px] leading-relaxed
                          text-[var(--color-text)]">
              {verBlock}
            </p>
            <div className="rounded-[var(--radius-sm)] border
                            border-[color-mix(in_srgb,var(--color-danger)_25%,transparent)]
                            bg-[var(--color-danger-soft)] p-3">
              <div className="mb-1 text-[11px] font-medium text-[var(--color-danger)]">
                处理办法
              </div>
              <ol className="list-decimal space-y-1 pl-4 font-mono
                             text-[11px] leading-relaxed text-[var(--color-text-secondary)]">
                <li>重新打包 sidecar：
                  <code className="mx-1 text-[var(--color-text)]">
                    python scripts/build_sidecar.py --onedir
                  </code>
                </li>
                <li>把三个 exe 与 _internal 重新部署到应用根目录</li>
                <li>完全退出本程序后重新启动</li>
              </ol>
            </div>
            <button
              onClick={() => void api.checkVersionConsistency().then((v) => {
                if (v.match) {
                  setVerBlock(null);
                } else {
                  setVerBlock(
                    `版本仍不一致：前端 ${v.frontend} / 后端 ${v.backend}`);
                }
              })}
              className="mt-4 w-full rounded-[var(--radius-sm)] border
                         border-[color-mix(in_srgb,var(--color-danger)_35%,transparent)]
                         bg-[var(--color-danger-soft)] px-3 py-2 text-[12px]
                         text-[var(--color-danger)] hover:bg-[var(--color-danger)]
                         hover:text-white transition-colors"
            >
              重新检测
            </button>
          </div>
        </div>
      ) : (
        <AppShell
        tab={tab}
        setTab={setTab}
        title={TITLE_OF(tab)}
        connected={ready}
        memberName={memberName}
        topRight={
          <>
            {/* ADR-018 F6：主题快捷切换（与设置页「外观」卡同源，同一 setTheme） */}
            <ThemeToggleButton />
            {gwPendingCount > 0 && (
              <button
                onClick={() => setTab("notify")}
                title={`${gwPendingCount} 条待授权消息`}
                className="rounded-full bg-[var(--color-warning-soft)] px-2.5 py-[3px]
                           text-[0.72rem] font-semibold text-[var(--color-warning)]"
              >
                待授权 {gwPendingCount}
              </button>
            )}
            <button
              onClick={async () => {
                await memberApi.logout();
                setMemberName(null);
              }}
              title="退出登录"
              className="rounded-full border border-[var(--color-border)] px-2.5 py-[3px]
                         text-[0.72rem] text-[var(--color-text-secondary)]
                         transition-colors hover:border-[var(--color-danger)]
                         hover:text-[var(--color-danger)]"
            >
              退出
            </button>
          </>
        }
        sidebarFooter={
          <div className="flex items-center gap-1.5 px-2.5 text-[0.72rem]
                          text-[var(--color-text-muted)]">
            <StatusDot tone={ready ? "ok" : "muted"} pulse={!ready} />
            <span>{ready ? "引擎就绪" : "等待连接"}</span>
          </div>
        }
      >
        <Suspense
          fallback={
            <div className="flex h-full items-center justify-center text-[0.8rem] text-[var(--color-text-muted)]">
              <span className="mr-2 inline-block h-4 w-4 animate-spin rounded-full border-2
                               border-[var(--color-border)] border-t-[var(--color-accent)]" />
              正在加载页面…
            </div>
          }
        >
          {/* 2026-10-02：页面级错误边界（key=tab ⇒ 切页自动复位）。
              原应用无边界，任一页面渲染抛错会**卸载整树 → 全白空窗**且无日志痕迹。 */}
          <ErrorBoundary key={tab} onReset={() => setTab("overview")}>
          {tab === "overview" && <OverviewPage {...pageProps} />}
          {/* ★ ADR-034（2026-10-03，方向反转 ADR-033）：
              采集功能已融进「内容总览」页（PlatformPage 的最后一个 tab），
              故「采集」不再是独立路由 —— 撤除该分支与 CrawlPage 的 lazy 入口。
              统计页（StatsPage）保持独立路由：纯只读看板，不跳转总览。 */}
          {tab === "stats" && <StatsPage {...pageProps} />}
          {tab === "platform" && <PlatformPage {...pageProps} />}
          {tab === "live" && <LivePage {...pageProps} />}
          {tab === "msg" && <MessagesPage {...pageProps} />}
          {tab === "kb" && <KbPage {...pageProps} />}
          {tab === "accounts" && <AccountsPage {...pageProps} />}
          {tab === "tasks" && <TasksPage {...pageProps} />}
          {tab === "taskdetail" && <TaskDetailPage {...pageProps} />}
          {tab === "settings" && <SettingsPage {...pageProps} />}
          {tab === "notify" && <NotifyPage {...pageProps} />}
          {tab === "logs" && <LogsPage {...pageProps} />}
          </ErrorBoundary>
        </Suspense>
      </AppShell>
      )}

      {/* 全局 toast（保留旧样式类，与新外壳共存） */}
      <div className="pointer-events-none fixed bottom-5 left-1/2 z-[var(--z-toast)] flex -translate-x-1/2
                      flex-col items-center gap-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            className="modal-surface pointer-events-auto rounded-[var(--radius-md)] px-4 py-2
                       text-[0.8rem] text-[var(--color-text)]
                       ring-1 ring-[color-mix(in_srgb,var(--color-accent)_60%,transparent)]
                       shadow-[var(--shadow-md),0_0_18px_-6px_color-mix(in_srgb,var(--color-accent)_45%,transparent)]"
          >
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

      {/* 元素选择模式：全局顶层悬浮入口 + 覆盖层 + 结果面板。
          设计契约：只选择、不触发；不读不写任何业务数据。 */}
      <Suspense fallback={null}>
        <ElementInspectorButton currentTab={tab} />
      </Suspense>

      {/* 指令式确认 / 输入弹窗的单例宿主（取代原生 confirm/prompt，全站统一主题观感）。
          挂载一次，供 confirmDialog()/promptDialog() 投递请求。 */}
      <DialogHost />

      {/* 窗口控制**常驻层**：恒在浮层之上（z-blocker=200），
          保证任何浮窗/全屏视图下都能最小化/最大化/关闭窗口。 */}
      <WindowControls />
    </>
  );
}

