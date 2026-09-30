import { useState, useEffect, useMemo, useRef, useCallback } from "react";

import { createPortal } from "react-dom";

import { useQuery } from "@tanstack/react-query";

import { AnimatePresence } from "framer-motion";

import {
  Play, Pause, Square, Heart, Send, Settings2, Mic, Eye, LogIn, Users, X, Tags,
} from "lucide-react";

import { PageProps, ReusePayload, RoomConfig, LiveRoom } from "../../api/client";

import RoomConfigPage from "./RoomConfigPage";

import RoomManagePage from "./RoomManagePage";

import HighValueKeywordsModal from "./HighValueKeywordsModal";

import { Avatar, hue, KIND_NAME } from "../../components/ui";

import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from "@/components/ui/select";

import { PageContainer, PageHeader } from "@/components/layout/app-shell";

import { Button } from "@/components/ui/button";

import { Input } from "@/components/ui/input";

import { Badge } from "@/components/ui/badge";

import { StatusDot } from "@/components/ui/status-dot";

import {
  Section, Tone, Blank, SegmentedTabs, Toolbar,
} from "@/components/page/kit";

import { cn } from "@/lib/utils";

import {
  LiveStream, TaskListResponse, RealAcct, FeedItem, Row, RankUser, acctValid, toDmStatus, fmtTime, recordsToRows, Th, Td, displayStatus, isIssue,
  sourceMetaOf, dmTitle, dmFailReason, dmPreviewText,
} from "./live-shared";

import { ReviewMode, errMsg } from "./LiveReviewMode";
import EngineCards from "./engine-cards";
import ContributionRank from "./ContributionRank";

/** 策略唯一键（以 id 为准，兼容旧数据的 room_id） */
const sidOf = (c: RoomConfig): string => String(c.id || c.room_id || "");


export default function LivePage(props: PageProps) {
  const { push, ready, goMsg, api, reviewPayload, reusePayload } = props;
  const [viewMode, setViewMode] = useState<"single" | "grid">("single");
  const [activeAcct, setActiveAcct] = useState<string | null>(null);
  const [room, setRoom] = useState("");
  const [review, setReview] = useState(false);
  const [reviewRows, setReviewRows] = useState<Row[]>([]);
  // 提示弹窗：解析房间号未填地址 / 开启自动私信前核查账号
  const [alert, setAlert] = useState<{ title: string; msg: string } | null>(null);
  const [dmDraft, setDmDraft] = useState("");
  const [myLikes, setMyLikes] = useState(0);
  const [burst, setBurst] = useState(0);
  const [batchN, setBatchN] = useState("10");
  // 2026-09-29（用户要求）：表格只有纵向滚动容器、没有筛选时，长会话很难定位
  // 问题条目。此开关只影响**呈现**（是否过滤行），不改变任何统计口径。
  const [onlyIssues, setOnlyIssues] = useState(false);
  // ── 配置来源（2026-09-15 用户定调：直播页不再手填任何配置）───────────────
  // 页面只做「选择对应配置的标签」；配置内容的编辑与「重启」都在
  // 「直播间配置管理」里（唯一可写入口）。这里只读展示生效配置。
  const [roomCfgs, setRoomCfgs] = useState<RoomConfig[]>([]);
  const [selCfgId, setSelCfgId] = useState<string>("");
  // 直播策略弹窗开关（2026-09-19 用户定调：**不要** tab 切换栏 / 子 tab 页面；
  // 策略编辑以弹窗提供，入口在「直播间」板块的策略下拉旁）
  const [cfgMgr, setCfgMgr] = useState(false);
  /** 「直播间管理」（房间层，ADR-003）：身份 + 策略引用 + 脱敏开关 */
  const [roomMgr, setRoomMgr] = useState(false);
  /** 高价值关键词权重表弹窗（2026-09-29：入口从设置页迁入「直播间」区，与策略同场景） */
  const [kwOpen, setKwOpen] = useState(false);
  // ── portal 宿主（2026-09-29 融合）────────────────────────────────────────
  // 把「AI 自动回复开关」**跨层级搬到**评论统计卡头，但不搬迁其 state / useQuery：
  // 仍由本组件持有，只改变渲染位置（避免把统计卡拆成 props 驱动的展示组件）。
  // 用「回调 ref + state」而非 useRef：宿主节点挂载/卸载会触发重渲染，
  // 从而让 portal 在节点出现的那一刻即被渲染（useRef 赋值不触发渲染）。
  const [kwHost, setKwHost] = useState<HTMLDivElement | null>(null); // 评论统计卡头部 actions 内
  // 申请连麦进行中（防重复点击）
  const [linkMicBusy, setLinkMicBusy] = useState(false);
  // 2026-09-29【防连点】：启动/停止请求进行中锁。
  // 为什么必须独立于 engineBusy：engineBusy 由**后端状态轮询**派生
  // （engineState 来自 `ls?.engineState`，要等下一次轮询才更新），
  // 从点击到状态刷新有 2~5s 窗口 —— 期间按钮不禁用，连点第二下必然
  // 被后端 409 拒绝，弹出的却是「如需换房请先停止」，而**第一次其实已成功**
  // ⇒ 误导用户以为启动失败。实测（run_20260929_110902.log）同一秒内
  // 两次 /start：11:19:21 启动成功 + 11:19:21「已在 starting，拒绝重复启动（409）」。
  const [engineReqBusy, setEngineReqBusy] = useState(false);
  // 任务中心「复用」载荷（标记已应用，避免容器回读覆盖用户刚改的字段）
  const reuseRef = useRef<ReusePayload | null>(null);
  // 🟡 2026-09-30（用户实测）：实时弹幕/信息流**不自动滚动到最新**。
  // 设计契约：只在用户已贴近底部时自动跟随（贴底阈值），用户上滚查看历史时
  // 绝不打扰（看一半被拽到底 = 更差）；离开底部时露出「回到最新」按钮。
  const feedBoxRef = useRef<HTMLDivElement | null>(null);
  const tableBoxRef = useRef<HTMLDivElement | null>(null);
  const tableStickRef = useRef(true);
  const [feedStick, setFeedStick] = useState(true);
  const [tableStick, setTableStick] = useState(true);
  const FEED_STICK_PX = 24;

  const { data: tasksCfg } = useQuery({
    queryKey: ["live-tasks"],
    queryFn: async () => (await api.getTasks()) as TaskListResponse,
    enabled: !!ready,
  });
  const { data: accounts } = useQuery({
    queryKey: ["accounts"],
    queryFn: async () => (await api.getAccounts()) as RealAcct[],
    enabled: !!ready,
  });
  const { data: streamRaw } = useQuery({
    queryKey: ["live-stream"],
    queryFn: async () => (await api.getStream()) as LiveStream,
    enabled: !!ready,
  });



  const realAccts: RealAcct[] = useMemo(() => (Array.isArray(accounts) ? accounts : []), [accounts]);
  // 账号选择默认：只有一个账号时自动选中它（无需手动选）；多个账号时由用户手动选择，
  // 且当前选择若失效（账号被删）则回退到第一个有效账号。
  useEffect(() => {
    if (realAccts.length === 1) {
      setActiveAcct(realAccts[0].name);
    } else if (realAccts.length > 0 && activeAcct === null) {
      // 多账号：默认不自动选，保持用户手动选择；若已有选择且账号还在则保留
    } else if (realAccts.length > 0 && activeAcct !== null) {
      if (!realAccts.some((a) => a.name === activeAcct)) {
        setActiveAcct(null); // 当前选择账号已被删除，清空让用户重选
      }
    }
  }, [realAccts, activeAcct]);
  const ls: LiveStream | null = streamRaw || null;
  // V2 任务容器：引擎进程忙命（含 启动中/等待开播/暂停/收尾）与 WS 是否真的在监听要分开看，
  // 否则切页回来因 WS 未连上就误显示「等待启动」。
  const engineState = ls?.engineState || "idle";
  const engineBusy = ["starting", "running", "paused", "stopping"].includes(engineState);
  const streaming = !!ls?.alive; // WS 真实连接（在直播流上）才算监听中
  const online = ls?.online_count ?? 0;
  const roomLikes = ls?.likes ?? 0;
  const messages = useMemo(() => ls?.messages ?? [], [ls]);
  const heat = useMemo(() => ls?.heat_curve ?? [], [ls]);
  const rank = useMemo<RankUser[]>(() => ls?.contribution_rank ?? [], [ls]);
  const rankReason = ls?.rankReason || "";
  const records = useMemo(() => tasksCfg?.records ?? [], [tasksCfg]);
  const engineLabel =
    engineState === "starting"
      ? "直播引擎启动中…"
      : engineState === "stopping"
        ? "直播引擎收尾中"
        : engineState === "paused"
          ? "直播引擎已暂停"
          : streaming
            ? "直播引擎监听中"
            : engineBusy
              ? "直播引擎等待开播"
              : "直播引擎未运行";

  // 响应任务中心「历史任务跳转查阅模式」：用历史任务 records 快照进入查阅模式
  useEffect(() => {
    if (!reviewPayload) return;
    const src = reviewPayload.records || [];
    setReviewRows(recordsToRows(src));
    setReview(true);
    push(`已进入历史任务「${reviewPayload.acct || ""}」的查阅模式，共 ${src.length} 条结果`);
  }, [reviewPayload, push]);

  // ── 直播间配置（「标签」）：列表来自唯一可写入口「直播间配置管理」 ──────────
  const loadRoomCfgs = useCallback(() => {
    if (!ready) return;
    api
      .listRoomConfigs()
      .then((r) => setRoomCfgs(Array.isArray(r.items) ? r.items : []))
      .catch(() => {});
  }, [ready, api]);

  useEffect(() => {
    loadRoomCfgs();
  }, [loadRoomCfgs]);

  // 选择某条配置：只读到页面（不写库、不起任务）——「选择配置的调用口」
  const pickRoomCfg = (roomId: string) => {
    setSelCfgId(roomId);
    const c = roomCfgs.find((x) => sidOf(x) === roomId);
    if (!c) return;
    if (c.acct && realAccts.some((a) => a.name === c.acct)) setActiveAcct(c.acct);
    push(`已选择直播策略「${c.name || roomId}」· 内容可在「管理策略」中改后点「重启」`);
  };

  const selCfg = useMemo(
    () => roomCfgs.find((c) => sidOf(c) === selCfgId) || null,
    [roomCfgs, selCfgId],
  );

  // ── AI 自动回复开关（2026-09-29：从原顶部「AI 自动回复」卡迁入评论统计卡头部）───
  // 原 `AiReplyCard` 顶端独立成卡，与「评论统计」信息重复；现把开关与计数一起
  // 收敛到 comment-stats 卡头部同一排（用户指定融合落点）。
  const { data: aiStatus, refetch: refetchAi } = useQuery({
    queryKey: ["ai-status"],
    queryFn: () => api.aiStatus(),
    refetchInterval: 5000,
    enabled: !!ready,
  });
  const aiRunning = !!(aiStatus as { running?: boolean } | undefined)?.running;
  const aiSt = (aiStatus || {}) as {
    replied?: number; leads_total?: number; processed?: number; errors?: number;
  };
  const toggleAiReply = () => {
    const call = aiRunning ? api.aiStop : api.aiStart;
    call()
      .then((r: { ok: boolean }) => {
        push(r.ok ? (aiRunning ? "AI 自动回复已停止" : "AI 自动回复已启动") : "操作失败");
        void refetchAi();
      })
      .catch((e: unknown) => push("操作异常: " + errMsg(e)));
  };

  // 任务容器回读：切换页面后回到直播监听页，用 /api/tasks/current 还原当前任务
  useEffect(() => {
    if (!ready) return;
    let alive = true;
    api
      .getCurrentTask()
      .then((t) => {
        if (!alive || !t || !t.ok || !t.has_task) return;
        const cfg = t.config || {};
        // 只回读「直播间」与「监听账号」两个页面级输入；其余配置项已不在页面上
        // （改由「直播间配置管理」唯一维护），不再回填到本地草稿。
        if (cfg.live_url && !reuseRef.current) setRoom(String(cfg.live_url));
        if (cfg.acct && realAccts.some((a) => a.name === cfg.acct)) {
          setActiveAcct(cfg.acct as string);
        }
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready]);

  // 任务中心「复用」：预填直播监听页（只回填直播间与账号；配置以「配置管理」里的为准）
  useEffect(() => {
    reuseRef.current = reusePayload || null;
    if (!reusePayload) return;
    const c = reusePayload;
    if (c.room) setRoom(c.room);
    const matched = roomCfgs.find((x) => sidOf(x) === c.room);
    if (matched) setSelCfgId(sidOf(matched));
    push(
      "已复用历史任务的直播间到直播监听页；发送参数请在「直播间配置管理」中核对该房间的配置",
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reusePayload, push]);

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
        attemptedContent: r.attempted_content || r.content || "",
        dmTime: r.sent_at ? fmtTime(r.sent_at) : "",
        ts: (r.captured_at || 0) * 1000,
        reason: String(r.reason || ""),
        failKind: (r.fail_kind as string) || null,
        failLabel: (r.fail_label as string) || null,
        failAdvice: (r.fail_advice as string) || null,
        // 2026-09-29：后端派生的**真实投递结局**（受理 ≠ 送达）
        deliveryState: (r.delivery_state as "delivered" | "rejected" | "") || "",
      })),
    [records],
  );

  const dedup = useMemo(() => new Set(rows.map((r) => r.name)).size, [rows]);
  const dedupCount = useMemo(
    () => new Set(rows.map((r) => r.name + "||" + r.content)).size,
    [rows],
  );
  const waitCount = useMemo(() => rows.filter((r) => r.dmStatus === "wait").length, [rows]);
  // 2026-09-29（用户拍板「受理 ≠ 送达」）：原实现 `sentCount` 数的是
  // RecordStatus.SENT = **已受理（入池）**，却被标成「已发送私信」显示，
  // 受理数里还有相当一部分被平台拒收 —— 数字与事实相反。
  // 现拆两档：受理（elapsed 交给发送链）与**真实送达**（有投递证据）。
  //
  // 🔴 2026-09-29 口径统一（G-1）：投递结局（delivered/rejected）与受理数必须
  // **同分母**。`delivery_state` 由后端按 uid×会话历史派生，同一 uid 的多条记录
  // （含 un/wait/fail 行）都会带上同一结局；若直接数全部 rows，就会出现
  // `deliveredCount > acceptedCount` ⇒ 送达率 >100%。
  // 现口径：**只在「已受理」（dmStatus==='sent'）行内统计投递结局**，
  // 于是 deliveredCount + rejectedCount ≤ acceptedCount，送达率必落在 0~100%。
  const acceptedRows = useMemo(
    () => rows.filter((r) => r.dmStatus === "sent"), [rows]);
  const acceptedCount = acceptedRows.length;
  const deliveredCount = useMemo(
    () => acceptedRows.filter((r) => r.deliveryState === "delivered").length, [acceptedRows]);
  const rejectedCount = useMemo(
    () => acceptedRows.filter((r) => r.deliveryState === "rejected").length, [acceptedRows]);
  // 空态降级：上游 `delivery_state` 仍可能整列为空（根因在 B 桶修）。
  // 无任何投递证据时**不显示 0 条 / 0%**，改为定性提示，避免误导为「全部未送达」。
  const hasDeliveryEvidence = useMemo(
    () => acceptedRows.some((r) => !!r.deliveryState), [acceptedRows]);
  // G-2/G-3：可见行与「仅看异常」判据都收敛到同一个真源（shared 的 isIssue）。
  const visibleRows = useMemo(
    () => (onlyIssues ? rows.filter(isIssue) : rows), [rows, onlyIssues]);
  // 🔴 2026-09-30（用户实测「发送失败…也没有进入错误统计」）：
  // 原「错误」取 `/api/ai/status` 的 `errors` —— 那是 **AI 会话回复 worker**
  // 的计数，与直播私信发送链路**不通** ⇒ 发送失败永远是 0。
  // 现与表格**同一判据**（displayStatus 的 danger 档 = 发送失败 / 被平台拒绝），
  // 保证「表里看得到几条失败，统计里就是几条」。
  const errorCount = useMemo(
    () => rows.filter((r) => displayStatus(r)[1] === "danger").length, [rows]);

  // Esc 关闭查阅模式
  useEffect(() => {
    const h = (e: KeyboardEvent) => {
      if (e.key === "Escape") setReview(false);
    };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, []);

  // ── 自动滚动跟随（2026-09-30 用户实测：弹幕/信息流不自动滚到最新）──────────
  // 判据：仅当用户**贴底**（距底 ≤ FEED_STICK_PX）才自动跟随；上滚查看历史时不打扰。
  const onFeedScroll = () => {
    const el = feedBoxRef.current;
    if (!el) return;
    setFeedStick(el.scrollHeight - el.scrollTop - el.clientHeight <= FEED_STICK_PX);
  };
  const onTableScroll = () => {
    const el = tableBoxRef.current;
    if (!el) return;
    const near = el.scrollHeight - el.scrollTop - el.clientHeight <= FEED_STICK_PX;
    tableStickRef.current = near;
    setTableStick(near);
  };
  useEffect(() => {
    if (!feedStick) return;
    const el = feedBoxRef.current;
    if (el) el.scrollTop = el.scrollHeight;
    // 依赖「最新一条的 id」而非长度：feed_snapshot 封顶 50 条，满员后 length 不再变，
    // 用长度会漏掉后续每一条新弹幕（正是用户报的「不自动滚到最新」的根因之一）。
  }, [feed[feed.length - 1]?.id, feedStick]);
  useEffect(() => {
    if (!tableStickRef.current) return;
    const el = tableBoxRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [rows[rows.length - 1]?.id]);
  const jumpToLatest = (which: "feed" | "table") => {
    if (which === "feed") {
      setFeedStick(true);
      const el = feedBoxRef.current;
      if (el) el.scrollTop = el.scrollHeight;
    } else {
      tableStickRef.current = true;
      setTableStick(true);
      const el = tableBoxRef.current;
      if (el) el.scrollTop = el.scrollHeight;
    }
  };

  const heatChart = (data: number[], w = 640, h = 120) => {
    if (!data || !data.length) {
      return (
        <div
          className="flex items-center justify-center text-[0.75rem]
                     text-[var(--color-text-muted)]"
          style={{ height: h }}
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
        className="block h-auto w-full"
        role="img"
        aria-label="房间热度曲线"
      >
        {[0.25, 0.5, 0.75].map((g) => (
          <line
            key={g}
            stroke="var(--color-border)"
            strokeWidth={1}
            x1="0"
            x2={w}
            y1={h * g}
            y2={h * g}
          />
        ))}
        <path fill="var(--color-accent)" opacity={0.1} d={`${d} L${w} ${h} L0 ${h} Z`} />
        <path fill="none" stroke="var(--color-accent)" strokeWidth={2} d={d} />
        <circle fill="var(--color-accent)" cx={x(data.length - 1)} cy={y(data[data.length - 1])} r="3.4" />
      </svg>
    );
  };

  /**
   * 发送弹幕（写接口）。
   * 2026-09-30：① 必须带 **account**（否则后端回落「当前账号」，多账号时可能用错身份）；
   * ② `room` 可能是 URL，交给后端 `link_resolve` 无关 —— 后端会用 kv `config.live_id`
   * 兜底并经基座归一化为**真实 room_id**；③ 失败必须如实呈现 `reason`（尤其是
   * `danmaku_disabled` = 用户未在设置里开启该写接口，属**默认休眠态**而非故障）。
   */
  const sendDanmaku = () => {
    const text = dmDraft.trim();
    if (!text) return;
    if (!ready) {
      push("未连接后端，无法发送弹幕");
      return;
    }
    // 只把纯数字 room_id 传给后端（URL 形态由后端 kv 兜底，避免传错形状）
    const rid = extractRoomId(room) || ls?.room_id || null;
    api
      .sendDanmaku(text, activeAcct, rid)
      .then((r) => {
        if (r.ok) {
          push("弹幕已发送 · " + text);
        } else if (r.reason === "danmaku_disabled") {
          push("发送弹幕未启用（默认休眠）：请到「设置 → 直播」开启「发送弹幕」");
        } else {
          push("发送失败: " + (r.error || r.reason || "未知原因"));
        }
      })
      .catch((e: unknown) => push("发送异常: " + errMsg(e)));
    setDmDraft("");
  };

  /**
   * 直播间点赞（写接口，2026-09-30 接真；此前为 `push("功能开发中：点赞")` 空壳）。
   * 后端默认休眠：未在设置里开启 `like_enabled` 时直接拒发（reason=like_disabled），
   * **零出站** —— 前端如实呈现，不谎报成功。
   */
  const likeOnce = (n: number) => {
    if (!ready) {
      push("未连接后端，无法点赞");
      return;
    }
    const rid = extractRoomId(room) || ls?.room_id || null;
    api
      .likeRoom(n, activeAcct, rid)
      .then((r) => {
        if (r.ok) {
          setMyLikes((v) => v + n);
          setBurst((v) => v + n);
          push(`已点赞 ×${n}`);
        } else if (r.reason === "like_disabled") {
          push("点赞未启用（默认休眠）：请到「设置 → 直播」开启「点赞」");
        } else {
          push("点赞失败: " + (r.error || r.reason || "未知原因"));
        }
      })
      .catch((e: unknown) => push("点赞异常: " + errMsg(e)));
  };

  const doLike = () => likeOnce(1);
  const doBatch = () => {
    const n = parseInt(batchN, 10);
    if (!Number.isFinite(n) || n < 1 || n > 1000) {
      push("批量点赞数量须在 1~1000 之间");
      return;
    }
    likeOnce(n);
  };
  const sendDm = (r: Row) => push("功能开发中：发送私信 → " + r.name);

  /** 从输入框提取直播间号（纯数字或 URL 里的 /<digits>），失败返回空串 */
  const extractRoomId = (raw: string): string => {
    const s = (raw || "").trim();
    if (/^\d+$/.test(s)) return s;
    const m = s.match(/live\.douyin\.com\/(\d+)/);
    return m ? m[1] : "";
  };

  // v0.38.5d 遗留未用函数（其他会话），暂注释避免 noUnusedLocals 卡构建
    /** 申请连麦（对当前输入框对应的直播间） */
    // const doApplyLinkMic = () => {
    //   const rid = extractRoomId(room) || (ls?.room_id ? String(ls.room_id) : "");
    //   if (!rid) {
    //     setAlert({ title: "请先解析直播间", msg: "请先在上方填写并解析直播间地址，再申请连麦。" });
    //     return;
    //   }
    //   if (linkMicBusy) return;
    //   setLinkMicBusy(true);
    //   api
    //     .requestLinkMic(rid, "audio")
    //     .then((r) => push(r.ok ? "已发起连麦申请 · 直播间 " + rid + (r.msg ? " · " + r.msg : "") : "申请连麦失败: " + (r.error || "未知错误")))
    //     .catch((e: unknown) => push("申请连麦异常: " + errMsg(e)))
    //     .finally(() => setLinkMicBusy(false));
    // };

  // 进入查阅模式：优先用实时记录；实时无数据时回读任务容器/历史任务 records，
  // 避免「进入查阅模式后一片空白未写入数据」。
  const openReview = () => {
    if (rows.length) {
      setReview(true);
      return;
    }
    api
      .getCurrentTask()
      .then((t) => {
        const recs = t && t.ok && Array.isArray(t.records) ? t.records : [];
        if (recs.length) setReviewRows(recordsToRows(recs));
        setReview(true);
        if (!recs.length) push("暂无发送记录可查阅");
      })
      .catch(() => setReview(true));
  };

  /**
   * AI 自动回复开关块（2026-09-29 融合）——状态 + 开关按钮，
   * 经 portal 渲染进评论统计卡头部 actions。
   * 计数（已回复/留资/错误）已按用户要求并入正文统计行（见 `aiCounts`）。
   */
  const aiReplyActions = (
    <div className="flex items-center gap-2" data-od-id="live-ai-reply-controls">
      <Tone tone={aiRunning ? "ok" : "mute"}>{aiRunning ? "AI 运行中" : "AI 已停止"}</Tone>
      <Button
        variant={aiRunning ? "danger-outline" : "secondary"}
        size="sm"
        data-od-id="ai-reply-toggle"
        onClick={toggleAiReply}
      >
        {aiRunning
          ? <><Square className="h-3.5 w-3.5" />停止 AI 回复</>
          : <><Play className="h-3.5 w-3.5" />开启 AI 回复</>}
      </Button>
    </div>
  );

  /** AI 计数（已回复 / 留资 / 错误）——并入评论统计正文统计行末尾。 */
  const aiCounts = (
    <span className="inline-flex items-center gap-x-1.5" data-od-id="live-ai-counts">
      <span>已回复
        <b className="ml-0.5 font-mono tabular-nums text-[var(--color-text)]">{aiSt.replied ?? 0}</b>
      </span>
      <span>·</span>
      <span>留资
        <b className="ml-0.5 font-mono tabular-nums text-[var(--color-text)]">{aiSt.leads_total ?? 0}</b>
      </span>
      <span>·</span>
      <span title="含「发送失败」与「被平台拒绝」两类，与表格状态列同一判据">
        错误
        <b className="ml-0.5 font-mono tabular-nums text-[var(--color-text)]">{errorCount}</b>
      </span>
    </span>
  );

  return (
    <PageContainer>
      {alert && (
        <div
          className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/55
                     p-5 backdrop-blur-sm"
          onClick={() => setAlert(null)}
        >
          <div
            className="w-full max-w-[460px] rounded-[var(--radius-lg)]
                       border border-[var(--color-border)] bg-[var(--color-surface-solid)]
                       p-5 shadow-[var(--shadow-lg)]"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-3 flex items-center justify-between gap-3">
              <h3 className="text-[0.95rem] font-semibold text-[var(--color-text)]">
                {alert.title}
              </h3>
              <button
                type="button"
                aria-label="关闭"
                className="cursor-pointer rounded-[var(--radius-sm)] p-1
                           text-[var(--color-text-muted)] transition-colors
                           hover:bg-[var(--color-surface-raised)] hover:text-[var(--color-text)]"
                onClick={() => setAlert(null)}
              >
                <X className="h-4 w-4" />
              </button>
            </div>
            <div className="rounded-[var(--radius-sm)] border border-[var(--color-warning-soft)]
                            bg-[var(--color-warning-soft)] px-3 py-2.5 text-[0.82rem]
                            leading-relaxed text-[var(--color-text-secondary)]">
              {alert.msg}
            </div>
            <div className="mt-4 text-right">
              <Button onClick={() => setAlert(null)}>我知道了</Button>
            </div>
          </div>
        </div>
      )}
      <RoomConfigPage
        open={cfgMgr}
        onClose={() => setCfgMgr(false)}
        push={push}
        onChanged={loadRoomCfgs}
        onApply={(cfg) => {
          const sid = String(cfg.id || cfg.room_id || "");
          if (sid) setSelCfgId(sid);
          if (cfg.acct && realAccts.some((a) => a.name === cfg.acct)) setActiveAcct(cfg.acct);
        }}
      />
      <RoomManagePage
        open={roomMgr}
        onClose={() => setRoomMgr(false)}
        push={push}
        onChanged={loadRoomCfgs}
        onPick={(r: LiveRoom) => {
          // 「选用」＝把房间号回填到直播页输入框（房间层只提供身份，不需选策略）
          if (r.room_id) setRoom(r.room_id);
          if (r.strategy_id) setSelCfgId(r.strategy_id);
          push(`已选用直播间「${r.name || r.room_id}」`);
        }}
      />

      <PageHeader
        title="直播监听"
        actions={
          <>
            <SegmentedTabs
              value={viewMode}
              onChange={setViewMode}
              items={[
                { value: "single", label: "单账户", icon: <LogIn className="h-3.5 w-3.5" /> },
                { value: "grid", label: "多账户总览", icon: <Users className="h-3.5 w-3.5" /> },
              ]}
            />
            <Badge variant={streaming ? "success" : engineBusy ? "warning" : "outline"}>
              <StatusDot
                tone={streaming ? "ok" : engineBusy ? "warn" : "muted"}
                pulse={streaming}
              />
              {ls ? engineLabel : "未连接"}
            </Badge>
            <Badge variant="outline">
              {ready
                ? engineBusy
                  ? ls?.statusMsg || (streaming ? "实时监听中" : "引擎运行中")
                  : "等待启动"
                : "未连接"}
            </Badge>
            <Badge variant={ls?.dmRunning ? "accent" : "outline"}>
              <StatusDot
                tone={ls?.dmRunning ? "ok" : "warn"}
                pulse={!!ls?.dmRunning}
              />
              {ls
                ? ls.dmRunning
                  ? ls.dmPaused
                    ? "私信引擎已暂停"
                    : "私信引擎发送中"
                  : "私信引擎待命"
                : "未连接"}
            </Badge>
          </>
        }
      />

      {/* 高价值关键词权重表弹窗（2026-09-29：入口从设置页迁来，见「直播间」区按钮） */}
      <HighValueKeywordsModal open={kwOpen} onClose={() => setKwOpen(false)} />

      {viewMode === "grid" ? (
              <EngineCards push={push} />
            ) : (
              <>
                <Section
                  className="mb-3.5"
                  data-od-id="live-acct-select"
            title="当前监听账号"
            actions={
              <>
                <div className="ml-auto flex flex-wrap items-center justify-end gap-2" data-od-id="live-acct-tabs">
                {realAccts.length > 0 ? (
                  <SegmentedTabs
                    value={activeAcct || ""}
                    onChange={(v) => {
                      setActiveAcct(v);
                      push("已切换到 " + v);
                    }}
                    items={realAccts.map((a) => ({ value: a.name, label: a.name }))}
                  />
                ) : (
                  <span className="font-mono text-[0.75rem] text-[var(--color-text-muted)]">
                    无已授权账号
                  </span>
                )}
                {realAccts.length > 1 && !activeAcct && (
                  <span className="font-mono text-[0.75rem] text-[var(--color-warning)]">
                    有多个账号，请手动选择一个
                  </span>
                )}
                </div>
              </>
            }
          >
            <div className="text-[0.75rem] text-[var(--color-text-muted)]">
              当前选择：
              <span className="font-mono text-[var(--color-text)]">
                {activeAcct || "（未选择）"}
              </span>
            </div>
          </Section>

          <Section
            className="mb-3.5"
            data-od-id="live-input"
            title="直播间"
            actions={
              <>
                <Select value={selCfgId} onValueChange={pickRoomCfg}>
                  <SelectTrigger
                    className="h-8 min-w-[220px]"
                    aria-label="选择直播策略"
                    data-od-id="live-cfg-select"
                  >
                    <SelectValue placeholder="选择直播策略…" />
                  </SelectTrigger>
                  <SelectContent>
                    {roomCfgs.length === 0 && (
                      <SelectItem value="__none__" disabled>
                        暂无策略 · 点右侧「管理策略」新建
                      </SelectItem>
                    )}
                    {roomCfgs.map((c) => (
                      <SelectItem key={sidOf(c)} value={sidOf(c)}>
                        {c.name || sidOf(c)}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <Button
                  variant="ghost"
                  size="sm"
                  data-od-id="live-room-configs"
                  title="管理直播策略（增删改 + 重启，不中断监听）"
                  onClick={() => setCfgMgr(true)}
                >
                  <Settings2 className="h-3.5 w-3.5" />管理策略
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  data-od-id="live-room-registry"
                  title="管理直播间登记（链接解析 / 备注 / 绑定策略 / 脱敏开关）"
                  onClick={() => setRoomMgr(true)}
                >
                  <Settings2 className="h-3.5 w-3.5" />直播间管理
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  data-od-id="live-high-value-keywords"
                  title="编辑高价值关键词权重表（决定高价值窗口，进而影响是否被发送闸门拦下）"
                  onClick={() => setKwOpen(true)}
                >
                  <Tags className="h-3.5 w-3.5" />高价值关键词
                </Button>
              </>
            }
          >
            <Toolbar>
              <Input
                className="min-w-[200px] flex-1 font-mono"
                value={room}
                onChange={(e) => setRoom(e.target.value)}
                onBlur={() => {
                  // 自动解析（无需手动按钮）：填的是 URL/短号时补全为真实房间号
                  const u = room.trim();
                  if (!u || /^\d+$/.test(u)) return;
                  api
                    .resolveLive(u)
                    .then((r) => {
                      if (r.ok && (r.liveId || r.liveUrl)) {
                        const resolved = r.liveId || r.liveUrl || u;
                        setRoom(resolved);
                        push("已自动解析 · 直播间号 " + resolved);
                      }
                    })
                    .catch(() => {});
                }}
                placeholder="直播间 URL 或 room_id（失焦自动解析）"
                aria-label="直播间地址"
              />
              <Button
                variant="ghost"
                size="sm"
                data-od-id="live-linkmic"
                disabled={linkMicBusy || !activeAcct}
                title="对当前直播间发起连麦申请（DOM 页面原生 · 经账号浏览器执行；约需 40s 等按钮出现）"
                onClick={() => {
                  const rid = extractRoomId(room) || (ls?.room_id ? String(ls.room_id) : "");
                  if (!rid) {
                    setAlert({ title: "请先填写直播间", msg: "请先在上方填写直播间 URL 或 room_id，再申请连麦。" });
                    return;
                  }
                  if (!activeAcct) {
                    setAlert({ title: "请先选择账号", msg: "申请连麦需要指定监听账号（申请将以该账号身份发起）。" });
                    return;
                  }
                  if (linkMicBusy) return;
                  setLinkMicBusy(true);
                  const roomUrl = /^https?:\/\//i.test(room.trim())
                    ? room.trim()
                    : `https://live.douyin.com/${rid}`;
                  push("正在发起连麦申请（DOM 页面原生 · " + activeAcct + "）…");
                  api
                    .requestLinkMic(activeAcct, rid, "audio", roomUrl)
                    .then((r) => {
                      if (r.ok) {
                        const q = r.data?.waiting_list_offset;
                        const autoJoin = r.data?.auto_join;
                        const btn = r.data?.buttonText;
                        push(`连麦申请成功 · ${r.via === "dom" ? "已点「" + (btn || "申请连线") + "」" : "接口直调"}${q != null ? " · 排队第 " + q + " 位" : ""}${autoJoin ? " · 免审批自动通过" : " · 等待主播接受"}`);
                      } else {
                        push("申请连麦失败: " + (r.error || r.data?.prompts || r.status_code || "未知错误"));
                      }
                    })
                    .catch((e: unknown) => push("申请连麦异常: " + errMsg(e)))
                    .finally(() => setLinkMicBusy(false));
                }}
              >
                <Mic className="h-3.5 w-3.5" />
                {linkMicBusy ? "申请中…" : "申请连麦"}
              </Button>
              {!ready && (
                <span className="font-mono text-[0.75rem] text-[var(--color-warning)]">
                  backend not connected - cannot listen
                </span>
              )}
              {ready && (
                <>
                  <Button
                    data-od-id="live-start"
                    disabled={engineBusy || engineReqBusy || (realAccts.length > 1 && !activeAcct)}
                    onClick={() => {
                      // 🔴 2026-09-29【防连点 · 必须有】：本锁独立于 engineBusy。
                      // 实测缺陷：engineBusy 由后端状态轮询派生（2~5s 才刷新），
                      // 期间按钮不禁用 ⇒ 连点第二下被 409 拒绝，弹「如需换房」
                      // 而第一次其实已成功 ⇒ 误导为"启动失败"。
                      if (engineReqBusy) return;
                      // 开启自动私信前核查账号情况
                      if (realAccts.length === 0) {
                        setAlert({
                          title: "尚未添加账号",
                          msg: "账号管理中还没有任何账号，请先在「账号管理」页添加账号并完成扫码授权，再开启自动私信。",
                        });
                        return;
                      }
                      if (!realAccts.some(acctValid)) {
                        setAlert({
                          title: "没有有效的账号",
                          msg: "当前所有账号的凭证均无效（未扫码 / 凭证过期 / 风控）。请先在「账号管理」页完成扫码授权，确保至少一个账号凭证有效后再开启自动私信。",
                        });
                        return;
                      }
                      if (realAccts.length > 1 && !activeAcct) {
                        setAlert({
                          title: "请先选择账号",
                          msg: "当前有多个账号，请先在上方「监听账号」下拉框中选择一个有效账号，再开启自动私信。",
                        });
                        return;
                      }
                      // 启动配置全部取自所选「直播间配置」（标签）—— 页面无手填项
                      if (!selCfg) {
                        setAlert({
                          title: "请先选择直播间配置",
                          msg: "直播页不再手填配置。请在上方「选择已保存的直播间配置」里选一条（或用「配置管理」新建一条）后再开始。",
                        });
                        return;
                      }
                      if (!room.trim()) {
                        setAlert({
                          title: "请先填写直播间",
                          msg: "请填写直播间 URL 或 room_id（失焦会自动解析），或直接选择一条已保存的策略。",
                        });
                        return;
                      }
                      const cfg = {
                        live_url: room,
                        max_target: selCfg.max_target ?? 3,
                        interval: selCfg.interval ?? 60,
                        delay: selCfg.delay || "50,120",
                        dm_pool: selCfg.dm_pool || [],
                        acct: activeAcct || selCfg.acct || undefined,
                      };
                      setEngineReqBusy(true);
                      api
                        .start(cfg)
                        .then((r) => {
                          // 2026-09-29：`.then` 只处理成功/失败提示，
                          // 锁在 finally 里统一释放（与连麦按钮同款范式）。
                          if (r.ok) {
                            push("引擎已启动 · " + room);
                          } else {
                            const st = (r as { state?: string; error?: string });
                            push(
                              "启动失败: " +
                                (st.error || st.state ||
                                 "已被拒绝（该账号可能已在监听；如需换房请先点「停止」）"),
                            );
                          }
                        })
                        .catch((e: unknown) => push("启动异常: " + errMsg(e)))
                        .finally(() => setEngineReqBusy(false));
                    }}
                  >
                    <Play className="h-3.5 w-3.5" />开始自动私信
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    data-od-id="live-pause"
                    disabled={engineState !== "running"}
                    onClick={() =>
                      api
                        .pauseEngine()
                        .then((r) => push(r.ok ? "已暂停" : "暂停失败"))
                        .catch((e: unknown) => push("暂停异常: " + errMsg(e)))
                    }
                  >
                    <Pause className="h-3.5 w-3.5" />暂停
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    data-od-id="live-resume"
                    disabled={engineState !== "paused"}
                    onClick={() =>
                      api
                        .resumeEngine()
                        .then((r) => push(r.ok ? "已继续" : "继续失败"))
                        .catch((e: unknown) => push("继续异常: " + errMsg(e)))
                    }
                  >
                    <Play className="h-3.5 w-3.5" />继续
                  </Button>
                  <Button
                    variant="danger-outline"
                    size="sm"
                    data-od-id="live-stop"
                    disabled={!engineBusy}
                    onClick={() =>
                      api
                        .stopSoftEngine()
                        .then((r) => push(r.ok ? "软停止中" : "停止异常"))
                        .catch((e: unknown) => push("软停止异常: " + errMsg(e)))
                    }
                  >
                    <Square className="h-3.5 w-3.5" />停止监听
                  </Button>
                </>
              )}
            </Toolbar>
            <div className="mt-1.5 text-[0.72rem] text-[var(--color-text-muted)]">
              {ready
                ? engineBusy
                  ? "引擎运行中" + (streaming ? "（真实监听）" : `（${ls?.statusMsg || "等待开播"}）`)
                  : "引擎未运行 · 配置后点「开始自动私信」"
                : "未连接后端 · 请先确保后端已启动"}
            </div>
          </Section>

          {/* AI 开关的 portal 落点：渲染进评论统计卡头 actions（kwHost） */}
          {kwHost && createPortal(aiReplyActions, kwHost)}

          <div className="mb-3.5 grid grid-cols-1 gap-3.5 lg:grid-cols-2">
            <Section
              data-od-id="live-feed"
              title="实时信息流"
              description={`${KIND_NAME.danmaku} / ${KIND_NAME.gift} / ${KIND_NAME.enter}`}
              actions={
                <span className="font-mono text-[0.8rem] text-[var(--color-text-secondary)]">
                  <Heart className="mr-1 inline h-3.5 w-3.5 text-[var(--color-danger)]" />
                  {roomLikes.toLocaleString()}
                </span>
              }
            >
              <div
                ref={feedBoxRef}
                onScroll={onFeedScroll}
                className="relative flex max-h-[236px] flex-col gap-0.5 overflow-y-auto overscroll-contain"
              >
                {feed.map((f) => (
                  <div
                    key={f.id}
                    className="flex items-baseline gap-2.5 rounded-[var(--radius-sm)] px-2 py-[7px]
                               transition-colors hover:bg-[var(--color-surface-raised)]"
                  >
                    <span className="w-[52px] shrink-0 font-mono text-[0.72rem] tabular-nums
                                     text-[var(--color-text-muted)]">
                      {f.t}
                    </span>
                    <span className="w-[34px] shrink-0 font-mono text-[0.7rem]
                                     tracking-[0.04em] text-[var(--color-text-secondary)]">
                      {KIND_NAME[f.k] || f.k}
                    </span>
                    <span className="min-w-0 truncate text-[0.82rem] text-[var(--color-text-secondary)]">
                      <b className="font-semibold text-[var(--color-text)]">{f.n}</b>　{f.x}
                    </span>
                    {f.l > 0 && (
                      <span className="shrink-0 font-mono text-[0.7rem] text-[var(--color-warning)]">
                        Lv.{f.l}
                      </span>
                    )}
                  </div>
                ))}
                {feed.length === 0 && (
                  <div className="px-3 py-7 text-center text-[0.75rem] text-[var(--color-text-muted)]">
                    暂无实时信息 · 引擎运行后自动展示弹幕 / 礼物 / 进场 / 点赞 / 关注
                  </div>
                )}
              </div>
              {!feedStick && feed.length > 0 && (
                <div className="mt-1.5 flex justify-center">
                  <Button variant="secondary" size="sm" data-od-id="live-feed-jump" onClick={() => jumpToLatest("feed")}>
                    ↓ 回到最新
                  </Button>
                </div>
              )}

              <Toolbar className="mt-3 border-t border-[var(--color-border)] pt-3">
                <Input
                  className="min-w-[160px] flex-1"
                  placeholder="发送弹幕到直播间…"
                  value={dmDraft}
                  onChange={(e) => setDmDraft(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && sendDanmaku()}
                />
                <Button
                  variant="ghost"
                  size="sm"
                  data-od-id="live-send-danmaku"
                  onClick={sendDanmaku}
                >
                  <Send className="h-3.5 w-3.5" />发送
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  data-od-id="live-like"
                  onClick={doLike}
                >
                  <Heart className="h-3.5 w-3.5" />点赞
                  {myLikes > 0 && (
                    <span className="font-mono text-[0.7rem] text-[var(--color-text-muted)]">
                      ×{myLikes}
                    </span>
                  )}
                  {burst > 0 && (
                    <span key={myLikes} className="font-mono text-[0.7rem]
                                                   text-[var(--color-success)]">
                      +{burst}
                    </span>
                  )}
                </Button>
              </Toolbar>

              <Toolbar className="mt-2" data-od-id="live-batch-like">
                <span className="font-mono text-[0.75rem] text-[var(--color-text-muted)]">
                  批量点赞
                </span>
                <Input
                  className="h-8 w-[100px]"
                  type="number"
                  min="1"
                  max="1000"
                  aria-label="批量点赞数量"
                  value={batchN}
                  onChange={(e) => setBatchN(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && doBatch()}
                />
                <Button variant="ghost" size="sm" onClick={doBatch}>
                  批量点赞
                </Button>
                <span className="font-mono text-[0.72rem] text-[var(--color-text-muted)]">
                  每次按输入数量执行，上限 1000
                </span>
              </Toolbar>
            </Section>

            <Section
              data-od-id="heat-chart"
              title="房间热度"
              description={`${
                heat.length
                  ? heat[heat.length - 1].toLocaleString()
                  : online
                    ? online.toLocaleString()
                    : 0
              } 人在线`}
            >
              <div className="relative [&_svg]:block [&_svg]:w-full">
                {heatChart(heat)}
              </div>
              {/* 贡献榜（2026-09-30）：用户指定**内联**进本卡、热度曲线 svg 之下，
                  不另立卡片。数据由 /api/live/stream 承载。 */}
              <ContributionRank rank={rank} reason={rankReason} />
            </Section>
          </div>

          <Section
            data-od-id="comment-stats"
            title="实时评论统计列表"
            actions={
              <div className="flex items-center gap-2">
                {/* 2026-09-29（用户实测反馈）：全量渲染后长会话难定位问题条目，
                    给一个**只影响呈现**的过滤开关（不参与任何统计口径）。 */}
                <button
                  type="button"
                  data-od-id="live-only-issues"
                  onClick={() => setOnlyIssues((v) => !v)}
                  className={cn(
                    "rounded-[var(--radius-sm)] border px-2 py-1 font-mono text-[0.7rem]",
                    "transition-colors",
                    onlyIssues
                      ? "border-[color-mix(in_srgb,var(--color-danger)_45%,transparent)] text-[var(--color-danger)]"
                      : "border-[var(--color-border)] text-[var(--color-text-muted)]"
                  )}
                  title="只显示「被平台拒绝 / 发送失败」的条目"
                >
                  {onlyIssues ? "仅看异常 ✓" : "仅看异常"}
                </button>
                <Button variant="ghost" size="sm" data-od-id="review-open" onClick={openReview}>
                  <Eye className="h-3.5 w-3.5" />进入查阅模式
                </Button>
                {/* 2026-09-29 融合：AI 自动回复开关的 portal 落点（卡头右侧，与上述控件同排） */}
                <div ref={setKwHost} className="flex items-center" data-od-id="comment-stats-actions" />
              </div>
            }
          >
            <div className="mb-2.5 flex flex-wrap items-center gap-x-2 gap-y-1 text-[0.75rem]
                            text-[var(--color-text-muted)]">
              <span>共 <b className="font-mono font-semibold text-[var(--color-text)]">{rows.length}</b> 条弹幕记录</span>
              <span>·</span>
              <span>去重 <b className="font-mono font-semibold text-[var(--color-text)]">{dedupCount}</b> 条</span>
              <span>·</span>
              <span>实际发言 <b className="font-mono font-semibold text-[var(--color-text)]">{dedup}</b> 人</span>
              <span>·</span>
              <span>待发送 <b className="font-mono font-semibold text-[var(--color-text)]">{waitCount}</b> 条</span>
              <span>·</span>
              {/* 🔴 2026-09-29（用户实测反馈「投递成功不代表传递送达」）：
                  原「已发送私信 N 条」把**受理**（入池）当成**送达**显示。
                  受理数里有相当一部分被平台拒收 ⇒ 数字与事实相反。
                  现拆三档并给出「送达率」，把缺口摆在界面上（可观测优先）。
                  G-1 口径统一：投递档只在**已受理**行内统计（同分母，比率≤100%）。 */}
              <span>已受理 <b className="font-mono font-semibold text-[var(--color-text)]">{acceptedCount}</b> 条</span>
              <span>·</span>
              {hasDeliveryEvidence ? (
                <>
                  <span>已送达 <b className="font-mono font-semibold text-[var(--color-success)]">{deliveredCount}</b> 条</span>
                  {rejectedCount > 0 && (
                    <>
                      <span>·</span>
                      <span title="抖音回执：对方回复或关注你之前，只能发送一条文字消息">
                        被平台拒绝{" "}
                        <b className="font-mono font-semibold text-[var(--color-danger)]">{rejectedCount}</b> 条
                      </span>
                    </>
                  )}
                  <span className="text-[var(--color-text-muted)]">
                    （送达率 {acceptedCount > 0 ? Math.round((deliveredCount / acceptedCount) * 100) : 0}%）
                  </span>
                </>
              ) : (
                // 空态降级：上游 delivery_state 仍可能整列为空（根因在 B 桶修）。
                // 此时**不显示 0 条 / 0%**（会被误读成「全部未送达」），改为定性提示。
                <span
                  className="text-[var(--color-text-muted)]"
                  title="后端尚未派生投递结局（delivery_state 为空）；受理只代表已入池，不等于送达"
                >
                  送达情况暂无法判定 · 暂无投递回执证据
                </span>
              )}
              {/* 2026-09-29（图二「红线放到绿线位置」）：AI 计数并入统计行末尾 */}
             {aiCounts}
            </div>
            <div className="relative -mx-4 -mb-4 flex max-h-[clamp(320px,calc(100vh-560px),620px)]
                            flex-col overflow-hidden">
              <div
                ref={tableBoxRef}
                onScroll={onTableScroll}
                className="min-h-0 flex-1 overflow-auto overscroll-contain"
              >
                {/* 2026-09-30（用户实测「表格突然变宽/发言人列变宽」）：
                    原表格**没有 table-layout**，浏览器按内容自动分配列宽 ——
                    长昵称/长文案会把列挤变形。改为 `table-fixed` + 固定列宽，
                    列宽与内容解耦（结构稳定），溢出由单元格 truncate 兜住。 */}
                <table className="w-full table-fixed border-collapse">
                  <colgroup>
                    <col style={{ width: 88 }} />
                    <col style={{ width: 150 }} />
                    <col style={{ width: 240 }} />
                    <col style={{ width: 120 }} />
                    <col style={{ width: 260 }} />
                    <col style={{ width: 88 }} />
                  </colgroup>
                  <thead className="sticky top-0 z-[1] bg-[var(--color-surface)]">
                    <tr>
                      <Th width={88}>发送时间</Th>                      <Th width={150}>发言人</Th>
                      <Th width={240}>评论内容</Th>
                      <Th width={120}>私信状态</Th>
                      <Th width={260}>私信文案</Th>
                      <Th width={88}>私信时间</Th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.length === 0 && (
                      <tr>
                        <Td colSpan={6}>
                          <Blank>
                            <span className="text-[1.4rem]">📭</span>
                            暂无评论记录 · 引擎运行后自动捕获
                          </Blank>
                        </Td>
                      </tr>
                    )}
                    {/* G-3：有记录、但「仅看异常」筛掉全部 ⇒ 给明确提示行。
                        否则只剩表头，用户分不清是「本次无异常」还是「渲染坏了」。 */}
                    {rows.length > 0 && visibleRows.length === 0 && (
                      <tr>
                        <Td colSpan={6}>
                          <Blank>
                            <span className="text-[1.4rem]">✅</span>
                            当前筛选下无异常记录 · 已受理/待发送的条目被隐藏，点「仅看异常 ✓」可恢复全部
                          </Blank>
                        </Td>
                      </tr>
                    )}
                    {/* 🔴 2026-09-29（用户实测反馈）：「实时弹幕区域有区域限制，只显示 20 条
                        左右，后续的弹幕都不显示，把该区域设为滚轮式」。
                        原实现 `rows.slice(0, 12)` 把表格**硬截断到前 12 行**，而外层只有
                        `overflow-x-auto`（横向）—— 既没有纵向滚动容器，也没有分页，
                        于是第 13 行之后的弹幕在界面上**永远不可见**（诊断层不可观测 =
                        缺陷被掩盖，与 H-20「受理冒充达成」同族）。
                        修法：全部行渲染，交给**纵向滚动容器**承载（`overflow-auto` +
                        `overscroll-contain`），表头 `sticky` 固定；容器高度用 `clamp`
                        在 320~620px 之间随视口自适应，保证页面上仍能同时看到下面的板块。 */}
                    {/* G-2：可见行来自 useMemo(visibleRows)，过滤判据 isIssue
                        与状态列 displayStatus 同源 ⇒ 不会「过筛却渲染成绿色已送达」。 */}
                    {visibleRows
                      .map((r) => (
                    <tr key={r.id} className="transition-colors
                                              hover:bg-[var(--color-surface-raised)]">
                      <Td mono className="whitespace-nowrap">{r.time}</Td>
                      <Td>
                        <span className="inline-flex items-center gap-2">
                          <Avatar name={r.name} h={hue(r.name.length)} sm />
                          <span className="whitespace-nowrap font-medium">{r.name}</span>
                          {r.lv < 99 && (
                            <span className="rounded-[4px] border
                                             border-[color-mix(in_srgb,var(--color-warning)_40%,transparent)]
                                             px-1 font-mono text-[0.66rem]
                                             text-[var(--color-warning)]">
                              Lv.{r.lv}
                            </span>
                          )}
                        </span>
                      </Td>
                      <Td className="max-w-[260px]" title={r.content}>
                        <span className="block truncate">{r.content}</span>
                      </Td>
                      <Td>
                        {/* 2026-09-29：显示档 = 调度状态 ⊕ 真实投递证据
                            （「被平台拒绝」优先于「已受理」） */}
                        {r.dmStatus === "un" && !r.deliveryState ? (
                          <span className="text-[var(--color-text-muted)]">—</span>
                        ) : (
                          (() => {
                            const [label, tone] = displayStatus(r);
                            return <Tone tone={tone}>{label}</Tone>;
                          })()
                        )}
                      </Td>
                      {/* 2026-09-30（用户实测反馈）：
                          ① 「发送内容不需要完整的全部展示，显示前 10 个字就行」→ 正文只渲
                             前 10 字（JS 截断，字数固定；CSS `truncate` 兜住窄列溢出）；
                          ② 「不知道这个文本到底是词库、AI 生成还是兜底文档」→ 前置来源
                             徽标（来自后端 content_source，前端不推断）；
                          ③ 「不要破坏表格结构」→ 列数/列序/行结构逐字不变，仅单元格内部
                             布局调整；全文经 `title` 悬浮可见（信息不丢）。 */}
                      <Td
                        title={dmTitle(r)}
                      >
                        <span className="flex min-w-0 items-center gap-1.5">
                          {(() => {
                            const _src = sourceMetaOf(r.contentSource);
                            return _src ? (
                              <span
                                className="shrink-0 rounded-[4px] border px-1
                                           font-mono text-[0.66rem]"
                                style={{
                                  color: _src.color,
                                  borderColor: `color-mix(in srgb, ${_src.color} 40%, transparent)`,
                                }}
                                title={`文案来源：${_src.label}`}
                              >
                                {_src.label}
                              </span>
                            ) : null;
                          })()}
                          <span
                            className={cn(
                              "block min-w-0 flex-1 truncate",
                              r.dmText
                                ? "text-[var(--color-text)]"
                                : "text-[var(--color-text-muted)]"
                            )}
                          >
                            {/* 2026-09-30：失败行优先显示**可读缘由** —— 用户原话
                                「发送失败，既没有显示失败缘由」。缘由非空即替换预览，
                                全文仍可经 title 悬浮查看。 */}
                            {dmFailReason(r) ? (
                              <span className="block min-w-0 flex-1 truncate
                                               text-[var(--color-danger)]"
                                    title={"失败原因：" + dmFailReason(r)}>
                                {dmFailReason(r)}
                              </span>
                            ) : (
                              <span
                                className={cn(
                                  "block min-w-0 flex-1 truncate",
                                  r.attemptedContent || r.dmText
                                    ? "text-[var(--color-text)]"
                                    : "text-[var(--color-text-muted)]"
                                )}
                              >
                                {dmPreviewText(r)}
                              </span>
                            )}
                          </span>
                        </span>
                      </Td>
                      <Td mono className="whitespace-nowrap">
                        {r.dmTime || <span className="text-[var(--color-text-muted)]">—</span>}
                      </Td>
                    </tr>
                  ))}
                  </tbody>
                </table>
              </div>
              {!tableStick && rows.length > 0 && (
                <div className="pointer-events-none absolute inset-x-0 bottom-2 flex justify-center">
                  <Button
                    variant="secondary"
                    size="sm"
                    className="pointer-events-auto shadow-[var(--shadow-lg)]"
                    data-od-id="live-table-jump"
                    onClick={() => jumpToLatest("table")}
                  >
                    ↓ 回到最新
                  </Button>
                </div>
              )}
            </div>
          </Section>
        </>
      )}

      <AnimatePresence>
        {review && (
          <ReviewMode
            key="review-mode"
            rows={reviewRows.length ? reviewRows : rows}
            onClose={() => {
              setReview(false);
              setReviewRows([]);
            }}
            push={push}
            sendDm={sendDm}
            goMsg={goMsg}
          />
        )}
      </AnimatePresence>
    </PageContainer>
  );
}

/** 统一提取错误信息（catch 变量在 strict 模式下为 unknown） */
