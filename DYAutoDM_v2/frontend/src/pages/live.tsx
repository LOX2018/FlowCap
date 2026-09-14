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
 *   - doLike / requestDm 后端暂未实现 -> 提示"功能开发中"
 *   - resolveLive 已移植：后端 /api/live/resolve 调用 link_resolve.resolve_live_id
 *   - rows 数据源: 旧版 getStats.list -> 新版 tasksCfg.records（含 status 枚举）
 */
import { Fragment, useState, useEffect, useMemo, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { motion, AnimatePresence } from "framer-motion";
import {
  Play, Pause, Square, Heart, Send, Settings2, Mic,
  Search as SearchIcon, Download, ArrowUpDown, Eye,
  LogIn, Users, X,
} from "lucide-react";
import { PageProps, ReusePayload } from "../api/client";
import RoomConfigManager from "../components/RoomConfigManager";
import { Avatar, hue, KIND_NAME, tick } from "../components/ui";
import { PageContainer, PageHeader } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import { StatusDot } from "@/components/ui/status-dot";
import { EmptyState } from "@/components/ui/empty-state";
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from "@/components/ui/select";
import {
  Section, Tone, Blank, SegmentedTabs, Toolbar, FormField,
} from "@/components/page/kit";
import { cn } from "@/lib/utils";

/** AI 自动回复控制卡（直播监听场景的启停入口；参数调整在 AI 页） */
function AiReplyCard(props: { push: (msg: string, holdMs?: number) => void }) {
  const { push } = props;
  const { data: status, refetch } = useQuery({
    queryKey: ["ai-status"],
    queryFn: () => useAiStatus(),
    refetchInterval: 5000,
  });
  const running = !!(status as { running?: boolean } | undefined)?.running;
  const st = (status || {}) as { replied?: number; leads_total?: number; processed?: number; errors?: number };
  const toggle = () => {
    const call = running ? apiCallStop : apiCallStart;
    call()
      .then((r: { ok: boolean }) => {
        push(r.ok ? (running ? "AI 自动回复已停止" : "AI 自动回复已启动") : "操作失败");
        void refetch();
      })
      .catch((e: unknown) => push("操作异常: " + (e instanceof Error ? e.message : String(e))));
  };
  const num = "font-mono tabular-nums text-[var(--color-text)]";
  return (
    <Section
      className="mb-3.5"
      data-od-id="live-ai-reply"
      title="AI 自动回复"
      actions={
        <>
          <Tone tone={running ? "ok" : "mute"}>{running ? "运行中" : "已停止"}</Tone>
          <Button
            variant={running ? "danger-outline" : "secondary"}
            size="sm"
            onClick={toggle}
          >
            {running
              ? <><Square className="h-3.5 w-3.5" />停止 AI 回复</>
              : <><Play className="h-3.5 w-3.5" />开启 AI 回复</>}
          </Button>
        </>
      }
    >
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-[0.76rem]
                      text-[var(--color-text-secondary)]">
        <span>已回复 <b className={num}>{st.replied ?? 0}</b></span>
        <span className="text-[var(--color-text-muted)]">·</span>
        <span>留资 <b className={num}>{st.leads_total ?? 0}</b></span>
        <span className="text-[var(--color-text-muted)]">·</span>
        <span>处理 <b className={num}>{st.processed ?? 0}</b></span>
        <span className="text-[var(--color-text-muted)]">·</span>
        <span>错误 <b className={num}>{st.errors ?? 0}</b></span>
      </div>
    </Section>
  );
}

// 独立于组件外的 api 调用（避免循环依赖；client.ts 的 api 由调用处传入更佳，
// 但此处为最小改动直接引 request 语义 —— 见下方 useAiStatus/apiCall* 实现）
import { api as _api } from "../api/client";
const useAiStatus = () => _api.aiStatus() as Promise<Record<string, unknown>>;
const apiCallStart = () => _api.aiStart();
const apiCallStop = () => _api.aiStop();

type DmStatus = "un" | "wait" | "sent" | "fail";
type PillColor = "ok" | "warn" | "danger" | "accent" | "mute";

const DM_META: Record<DmStatus, [string, PillColor]> = {
  un: ["未私信", "mute"],
  wait: ["待发送", "warn"],
  sent: ["已发送", "ok"],
  fail: ["发送失败", "danger"],
};

/** 解析延迟抖动字符串：'50,120'/'50-120'/'50~120' -> [50,120]；'60' -> [60,60] */
function parseDelayRange(raw: string): number[] {
  const s = (raw || "").trim();
  if (!s) return [40, 65];
  const m = s.split(/[,~\-\s]+/);
  if (m.length >= 2) {
    const lo = parseInt(m[0], 10);
    const hi = parseInt(m[1], 10);
    if (!isNaN(lo) && !isNaN(hi)) {
      return lo > hi ? [hi, lo] : [lo, hi];
    }
  }
  const v = parseInt(s, 10);
  if (!isNaN(v)) return [v, v];
  return [40, 65];
}

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
  engineState?: string;
  statusMsg?: string;
}

interface SendRecord {
  key: string;
  uid: string;
  nickname: string;
  sec_uid?: string | null;
  status?: string;
  reason?: string | null;
  /** 2026-09-08：结构化失败分类（后端 models/task.py 下发） */
  fail_kind?: string | null;
  fail_label?: string | null;
  fail_advice?: string | null;
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
  level?: string; // ok=凭证有效；nosign/expired/missing/unknown 为非有效
  role?: string;
}

/** 账号是否有效（凭证齐全且探活通过）。后端 _to_raw_account 的 level==='ok' 即有效。 */
function acctValid(a: RealAcct): boolean {
  return a.level === "ok";
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
  reason?: string;
  /** 2026-09-08：结构化失败分类（弹窗展示具体原因） */
  failKind?: string | null;
  failLabel?: string | null;
  failAdvice?: string | null;
}

// ============================================================================
// 失败原因结构化说明（2026-09-08 用户要求）
// ----------------------------------------------------------------------------
// 目标：私信发送失败时，弹窗明确告知是「调度堵塞 / 凭证失效 / 账号风控 /
// 频控限流 / 参数错误 / 网络异常」中的哪一类，并给出可操作建议，
// 而不是只显示一行原始报错。
// 后端 models/task.py 已下发 fail_kind / fail_label / fail_advice；
// 老后端未下发时，前端用 localClassifyFail 兜底（保持兼容）。
// ============================================================================
const FAIL_KIND_META: Record<string, { label: string; color: string; advice: string }> = {
  credential: {
    label: "凭证失效",
    color: "var(--color-danger)",
    advice: "该账号私信签名已失效。请到「账号」页面点【重新扫码】重新抓取签名后重试。",
  },
  risk: {
    label: "账号风控",
    color: "var(--color-danger)",
    advice:
      "抖音对该账号的私信行为判定为风控（多见于向陌生用户频繁首发）。建议：降低发送频率、暂停该账号 30 分钟以上，或换账号发送。",
  },
  ratelimit: {
    label: "频控限流",
    color: "var(--color-warning)",
    advice:
      "已达发送频率上限（统一闸门限流）。建议等待冷却结束再发，不要手动连续重发，否则会加重限流。",
  },
  blocked: {
    label: "调度堵塞",
    color: "var(--color-warning)",
    advice:
      "发送队列/调度被占满或排队超时。建议：暂停当前监听任务，等队列消化后再启动；若持续出现请重启后端。",
  },
  param: {
    label: "参数错误",
    color: "var(--color-text-muted)",
    advice: "发送参数不合法（目标 uid 或文案为空/格式错误）。请检查该目标的会话数据是否完整。",
  },
  network: {
    label: "网络异常",
    color: "var(--color-warning)",
    advice: "网络或守护进程不可达。请检查后端与 recv_daemon 是否在运行。",
  },
  other: {
    label: "其他原因",
    color: "var(--color-text-muted)",
    advice: "未能归类的失败。可查看下方原始原因，或到「日志」页查看后端详细日志定位。",
  },
};

/** 后端未下发分类时的本地兜底（规则与后端 core/sender.classify_fail 保持一致） */
function localClassifyFail(reason: string): string {
  const r = (reason || "").trim();
  const low = r.toLowerCase();
  if (!r) return "other";
  // ① 账号级风控必须最先判：KICK 文案常同时含 INVALID_REQUEST，
  //    但按后端 sender.py 注释，KICK 多为账号级反 spam 风控，不是签名失效。
  if (r.toUpperCase().includes("KICK") || r.includes("风控") || low.includes("spam") || r.includes("被限制"))
    return "risk";
  if (
    r.includes("签名三件套缺失") ||
    r.includes("需重新扫码") ||
    r.includes("INVALID_REQUEST") ||
    low.includes("unauthorized") ||
    r.includes("登录态") ||
    (low.includes("cookie") && r.includes("失效"))
  )
    return "credential";
  if (
    low.includes("rate_limited") ||
    r.includes("频繁") ||
    r.includes("冷静期") ||
    r.includes("冷却") ||
    r.includes("上限") ||
    r.includes("限流")
  )
    return "ratelimit";
  if (
    r.includes("堵塞") ||
    r.includes("队列") ||
    low.includes("queue") ||
    r.includes("超时") ||
    low.includes("timeout") ||
    low.includes("busy") ||
    r.includes("调度")
  )
    return "blocked";
  if (
    r.includes("为空") ||
    r.includes("非数字") ||
    (r.includes("缺失") && r.includes("账号")) ||
    (low.includes("uid") && r.includes("解析")) ||
    r.includes("会话整理")
  )
    return "param";
  if (
    r.includes("不可达") ||
    low.includes("connection") ||
    r.includes("连接") ||
    low.includes("network") ||
    r.includes("HTTP 5")
  )
    return "network";
  return "other";
}

/** 取最终展示用的失败说明（优先后端下发，缺失时本地兜底） */
function failInfoOf(row: Row): { kind: string; label: string; color: string; advice: string; raw: string } {
  const raw = String(row.reason || "");
  const kind = row.failKind || localClassifyFail(raw);
  const meta = FAIL_KIND_META[kind] || FAIL_KIND_META.other;
  return {
    kind,
    label: row.failLabel || meta.label,
    color: meta.color,
    advice: row.failAdvice || meta.advice,
    raw,
  };
}

/** 失败原因弹窗 */
function FailReasonModal({
  row,
  onClose,
}: {
  row: Row | null;
  onClose: () => void;
}) {
  if (!row) return null;
  const info = failInfoOf(row);
  return (
    <div
      onClick={onClose}
      className="fixed inset-0 z-[9999] flex items-center justify-center bg-black/45 p-5"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-full max-w-[520px] rounded-[var(--radius-md)]
                   border border-[var(--color-border)] bg-[var(--color-surface-solid)] p-5
                   shadow-[0_12px_40px_rgba(0,0,0,0.35)]"
      >
        <div className="mb-3 flex items-center gap-2.5">
          <span
            className="rounded-full px-2.5 py-[3px] text-[0.75rem] font-semibold text-white"
            style={{ background: info.color }}
          >
            {info.label}
          </span>
          <b className="text-[0.95rem] text-[var(--color-text)]">私信发送失败</b>
        </div>

        <div className="mb-2.5 text-[0.82rem] text-[var(--color-text-muted)]">
          目标：{row.name || "未知"}
        </div>

        <div className="mb-3 rounded-[var(--radius-sm)] bg-[var(--color-accent-soft)] px-3 py-2.5
                        text-[0.82rem] leading-[1.7] text-[var(--color-text)]">
          {info.advice}
        </div>

        {info.raw ? (
          <details className="text-[0.75rem]">
            <summary className="cursor-pointer text-[var(--color-text-muted)]">
              原始原因（点击展开）
            </summary>
            <pre
              className="mt-2 whitespace-pre-wrap break-all rounded-[var(--radius-sm)]
                         bg-[var(--color-background-soft)] p-2.5 text-[0.72rem] leading-relaxed
                         text-[var(--color-text)]"
            >
              {info.raw}
            </pre>
          </details>
        ) : null}

        <div className="mt-4 text-right">
          <Button onClick={onClose}>我知道了</Button>
        </div>
      </div>
    </div>
  );
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

/** 任务容器/历史任务 records（dict 快照）-> 查阅模式 Row 列表 */
function recordsToRows(src: Record<string, unknown>[]): Row[] {
  return src.map((r, i) => ({
    id: 900000 + i,
    time: r.start_ts ? String(r.start_ts).slice(11, 19) : "",
    name: String(r.nickname || r.uid || "未知"),
    lv: 0,
    content: String(r.comment || r.content || ""),
    dmStatus: toDmStatus(String(r.status || "")),
    dmText: String(r.content || ""),
    dmTime: r.send_ts ? String(r.send_ts) : "",
    ts: (Number(r.captured_at) || 0) * 1000,
    reason: String(r.reason || ""),
    failKind: (r.fail_kind as string) || null,
    failLabel: (r.fail_label as string) || null,
    failAdvice: (r.fail_advice as string) || null,
  }));
}

/* ── 表格具名单元（避免每处重复 className，见 tasks.tsx 范式） ── */

function Th({
  children,
  className,
  width,
}: {
  children?: React.ReactNode;
  className?: string;
  width?: number;
}) {
  return (
    <th
      style={width ? { width } : undefined}
      className={cn(
        "whitespace-nowrap border-b border-[var(--color-border)] px-2.5 py-2 text-left",
        "text-[0.7rem] font-semibold tracking-[0.03em] text-[var(--color-text-secondary)]",
        className
      )}
    >
      {children}
    </th>
  );
}

function Td({
  children,
  mono,
  muted,
  className,
  colSpan,
  title,
  style,
}: {
  children?: React.ReactNode;
  mono?: boolean;
  muted?: boolean;
  className?: string;
  colSpan?: number;
  title?: string;
  style?: React.CSSProperties;
}) {
  return (
    <td
      colSpan={colSpan}
      title={title}
      style={style}
      className={cn(
        "border-b border-[var(--color-border)] px-2.5 py-2 align-middle",
        "text-[0.78rem] text-[var(--color-text)]",
        mono && "font-mono tabular-nums",
        muted && "text-[var(--color-text-muted)]",
        className
      )}
    >
      {children}
    </td>
  );
}

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
  // 直播间配置管理弹窗（按直播间号管理配置 + 自动申请连麦）
  const [cfgMgr, setCfgMgr] = useState(false);
  // 申请连麦进行中（防重复点击）
  const [linkMicBusy, setLinkMicBusy] = useState(false);
  // 任务中心「复用」载荷（标记已应用，避免容器回读覆盖用户刚改的字段）
  const reuseRef = useRef<ReusePayload | null>(null);

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

  // 任务容器回读：切换页面后回到直播监听页，用 /api/tasks/current 还原上次任务配置
  useEffect(() => {
    if (!ready) return;
    let alive = true;
    api
      .getCurrentTask()
      .then((t) => {
        if (!alive || !t || !t.ok || !t.has_task) return;
        const cfg = t.config || {};
        if (cfg.live_url && !reuseRef.current) setRoom(String(cfg.live_url));
        if (cfg.max_target != null) setDmLimit(String(cfg.max_target));
        if (cfg.interval != null) setDmInterval(String(cfg.interval));
        if (cfg.delay) setDmJitter(String(cfg.delay));
        if (cfg.force_rescan != null) setForceRescan(Boolean(cfg.force_rescan));
        if (Array.isArray(cfg.dm_pool) && cfg.dm_pool.length) {
          setDmTemplates(
            cfg.dm_pool.map((d) => ({
              text: String((d as { text?: string }).text ?? ""),
              enabled: (d as { enabled?: boolean }).enabled !== false,
            })),
          );
        }
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

  // 任务中心「复用」：预填直播监听页（配置取自历史任务快照）
  useEffect(() => {
    reuseRef.current = reusePayload || null;
    if (!reusePayload) return;
    const c = reusePayload;
    if (c.room) setRoom(c.room);
    if (c.maxTarget != null) setDmLimit(String(c.maxTarget));
    if (c.interval != null) setDmInterval(String(c.interval));
    if (c.delay) setDmJitter(c.delay);
    if (c.forceRescan != null) setForceRescan(c.forceRescan);
    if (Array.isArray(c.dmPool) && c.dmPool.length) {
      setDmTemplates(c.dmPool.map((d) => ({ text: String(d.text ?? ""), enabled: d.enabled !== false })));
    }
    push(
      "已复用历史任务配置到直播监听页，核对后点「开始自动私信」即可重新运行",
    );
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
        dmTime: r.sent_at ? fmtTime(r.sent_at) : "",
        ts: (r.captured_at || 0) * 1000,
        reason: String(r.reason || ""),
        failKind: (r.fail_kind as string) || null,
        failLabel: (r.fail_label as string) || null,
        failAdvice: (r.fail_advice as string) || null,
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

  const cfgLiveUrl = tasksCfg?.config?.live_url || "";

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
      <RoomConfigManager
        open={cfgMgr}
        onClose={() => setCfgMgr(false)}
        currentRoom={room}
        push={push}
        onApply={(cfg) => {
          if (cfg.live_url) setRoom(cfg.live_url);
          else if (cfg.room_id) setRoom(cfg.room_id);
          if (cfg.max_target != null) setDmLimit(String(cfg.max_target));
          if (cfg.interval != null) setDmInterval(String(cfg.interval));
          if (cfg.delay) setDmJitter(cfg.delay);
          if (cfg.force_rescan != null) setForceRescan(cfg.force_rescan);
          if (Array.isArray(cfg.dm_pool) && cfg.dm_pool.length) {
            setDmTemplates(cfg.dm_pool.map((d) => ({
              text: String((d as { text?: string }).text ?? ""),
              enabled: (d as { enabled?: boolean }).enabled !== false,
            })));
          }
          if (cfg.acct && realAccts.some((a) => a.name === cfg.acct)) setActiveAcct(cfg.acct);
        }}
      />

      <PageHeader
        title="直播监听"
        description="实时弹幕 / 礼物 / 评论采集与私信自动化"
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

      {viewMode === "grid" ? (
        <div className="grid grid-cols-1 gap-4 md:grid-cols-2" data-od-id="live-grid">
          {realAccts.slice(0, 2).map((acct) => (
            <Card key={acct.name} className="overflow-hidden">
              <div className="flex items-center gap-2.5 border-b border-[var(--color-border)]
                              bg-[var(--color-surface-raised)] px-4 py-3">
                <Avatar name={acct.name} h={hue(acct.name.length)} />
                <div className="min-w-0 flex-1">
                  <div className="text-[0.88rem] font-semibold text-[var(--color-text)]">
                    {acct.name}
                  </div>
                  <div className="font-mono text-[0.72rem] text-[var(--color-text-muted)]">
                    UID: {acct.uid || "—"}
                  </div>
                </div>
                <Tone tone={isAcctOnline(acct) ? "ok" : "danger"}>
                  {isAcctOnline(acct) ? "在线" : "离线"}
                </Tone>
              </div>

              <div className="space-y-1.5 border-b border-[var(--color-border)] px-4 py-2.5">
                <div className="flex items-center justify-between gap-3">
                  <span className="text-[0.75rem] text-[var(--color-text-muted)]">直播间</span>
                  <span className="truncate font-mono text-[0.75rem] font-semibold
                                   text-[var(--color-text)]">
                    {ls?.roomTitle || ls?.liveUrl || "未解析"}
                  </span>
                </div>
                <div className="flex items-center justify-between gap-3">
                  <span className="text-[0.75rem] text-[var(--color-text-muted)]">在线人数</span>
                  <span className="font-mono text-[0.75rem] text-[var(--color-text)]">
                    {online ? online.toLocaleString() : "—"}
                  </span>
                </div>
              </div>

              <div className="grid grid-cols-2 gap-2 px-4 py-2.5">
                <div className="rounded-[var(--radius-sm)] border border-[var(--color-border)]
                                bg-[var(--color-surface)] px-2.5 py-2">
                  <div className="text-[0.68rem] text-[var(--color-text-muted)]">弹幕</div>
                  <div className="font-mono text-[1.1rem] tabular-nums text-[var(--color-text)]">
                    {rows.length.toLocaleString()}
                  </div>
                </div>
                <div className="rounded-[var(--radius-sm)] border border-[var(--color-border)]
                                bg-[var(--color-surface)] px-2.5 py-2">
                  <div className="text-[0.68rem] text-[var(--color-text-muted)]">已私信</div>
                  <div className="font-mono text-[1.1rem] tabular-nums text-[var(--color-text)]">
                    {sentCount}
                  </div>
                </div>
              </div>

              <div className="border-t border-[var(--color-border)] px-4 py-2.5">
                <div className="mb-1.5 flex items-center justify-between gap-3">
                  <span className="text-[0.75rem] font-semibold text-[var(--color-text)]">
                    房间热度
                  </span>
                  <span className="font-mono text-[0.75rem] text-[var(--color-accent)]">
                    {online ? online.toLocaleString() : 0} 人
                  </span>
                </div>
                <div className="h-[60px]">{heatChart(heat.length ? heat : [0], 400, 60)}</div>
              </div>

              <div className="max-h-[120px] overflow-auto border-t border-[var(--color-border)]
                              px-4 py-2.5">
                {feed.slice(0, 5).map((f) => (
                  <div
                    key={f.id}
                    className="flex items-baseline gap-2.5 rounded-[var(--radius-sm)] px-2 py-[3px]
                               transition-colors hover:bg-[var(--color-surface-raised)]"
                  >
                    <span className="w-[52px] shrink-0 font-mono text-[0.62rem] tabular-nums
                                     text-[var(--color-text-muted)]">
                      {f.t}
                    </span>
                    <span className="w-[34px] shrink-0 font-mono text-[0.62rem]
                                     tracking-[0.04em] text-[var(--color-text-secondary)]">
                      {KIND_NAME[f.k] || f.k}
                    </span>
                    <span className="min-w-0 truncate text-[0.68rem] text-[var(--color-text-secondary)]">
                      <b className="font-semibold text-[var(--color-text)]">{f.n}</b> {f.x}
                    </span>
                  </div>
                ))}
                {feed.length === 0 && (
                  <div className="py-2.5 text-center text-[0.68rem] text-[var(--color-text-muted)]">
                    暂无实时信息
                  </div>
                )}
              </div>

              <div className="flex gap-1.5 border-t border-[var(--color-border)] px-4 py-2">
                <Button
                  variant="ghost"
                  size="sm"
                  className="flex-1"
                  onClick={() => {
                    setViewMode("single");
                    setActiveAcct(acct.name);
                    push("已切换到 " + acct.name);
                  }}
                >
                  进入监听
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  onClick={() => push("已导出 " + acct.name + " 数据")}
                >
                  导出
                </Button>
              </div>
            </Card>
          ))}
          {realAccts.length === 0 && (
            <Card className="md:col-span-2">
              <CardContent className="p-0">
                <EmptyState
                  icon={<Users className="h-6 w-6" />}
                  title="暂无已授权账号"
                  description="请到「账号管理」添加账号并完成扫码授权。"
                />
              </CardContent>
            </Card>
          )}
        </div>
      ) : (
        <>
          <Section
            className="mb-3.5"
            data-od-id="live-acct-select"
            title="当前监听账号"
            description="多账号时需手动选择一个；引擎启动前会校验凭证有效性"
            actions={
              <>
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

          <Section className="mb-3.5" data-od-id="live-input">
            <Toolbar>
              <Input
                className="min-w-[200px] flex-1 font-mono"
                value={room}
                onChange={(e) => setRoom(e.target.value)}
                placeholder="直播间 URL 或 room_id"
                aria-label="直播间地址"
              />
              <Button
                variant="ghost"
                size="sm"
                data-od-id="live-parse"
                onClick={() => {
                  const u = room.trim();
                  if (!u) {
                    setAlert({ title: "请输入直播地址", msg: "请先在上方输入框填写直播间 URL 或 room_id，再点击「解析房间号」。" });
                    return;
                  }
                  api
                    .resolveLive(u)
                    .then((r) => {
                      if (r.ok) {
                        const resolved = r.liveId || r.liveUrl || u;
                        setRoom(resolved);
                        push("解析成功 · 直播间号 " + resolved);
                      } else {
                        push("解析失败: " + (r.error || "未知错误"));
                      }
                    })
                    .catch((e: unknown) => push("解析异常: " + errMsg(e)));
                }}
              >
                <SearchIcon className="h-3.5 w-3.5" />解析房间号
              </Button>
              <Button
                variant="ghost"
                size="sm"
                data-od-id="live-linkmic"
                disabled={linkMicBusy || !activeAcct}
                title="对当前直播间发起连麦申请（经账号浏览器执行）"
                onClick={() => {
                  const rid = extractRoomId(room) || (ls?.room_id ? String(ls.room_id) : "");
                  if (!rid) {
                    setAlert({ title: "请先解析直播间", msg: "请先填写并解析直播间地址，再申请连麦。" });
                    return;
                  }
                  if (!activeAcct) {
                    setAlert({ title: "请先选择账号", msg: "申请连麦需要指定监听账号（申请将以该账号身份发起）。" });
                    return;
                  }
                  if (linkMicBusy) return;
                  setLinkMicBusy(true);
                  push("正在发起连麦申请（接口直调 · " + activeAcct + "）…");
                  api
                    .requestLinkMic(activeAcct, rid, "audio")
                    .then((r) => {
                      if (r.ok) {
                        const q = r.data?.waiting_list_offset;
                        const autoJoin = r.data?.auto_join;
                        push(`连麦申请成功 · 排队第 ${q ?? "?"} 位${autoJoin ? " · 免审批自动通过" : " · 等待主播接受"}`);
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
              <Button
                variant="ghost"
                size="sm"
                data-od-id="live-room-configs"
                title="按直播间号管理配置（含自动申请连麦）"
                onClick={() => setCfgMgr(true)}
              >
                <Settings2 className="h-3.5 w-3.5" />直播间配置管理
              </Button>
              {ready && !!cfgLiveUrl && room !== cfgLiveUrl && (
                <Button variant="ghost" size="sm" onClick={() => setRoom(cfgLiveUrl)}>
                  填入已配置
                </Button>
              )}
              {!ready && (
                <span className="font-mono text-[0.75rem] text-[var(--color-warning)]">
                  backend not connected - cannot listen
                </span>
              )}
              {ready && (
                <>
                  <Button
                    data-od-id="live-start"
                    disabled={engineBusy || (realAccts.length > 1 && !activeAcct)}
                    onClick={() => {
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
                      const cfg = {
                        live_url: room,
                        max_target: parseInt(dmLimit, 10) || 9999,
                        interval: parseFloat(dmInterval) || 60,
                        delay_range: parseDelayRange(dmJitter),
                        dm_pool: dmTemplates
                          .filter((t) => t.enabled && t.text && t.text.trim())
                          .map((t) => t.text.trim()),
                        force_rescan: forceRescan,
                        acct: activeAcct || undefined, // 当前选中的监听账号
                      };
                      api
                        .start(cfg)
                        .then((r) =>
                          push(r.ok ? "引擎已启动 · " + room : "启动失败: " + (r.state || "")),
                        )
                        .catch((e: unknown) => push("启动异常: " + errMsg(e)));
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
                        .pause()
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
                        .resume()
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
                        .stopSoft()
                        .then((r) => push(r.ok ? "已停止监听（存量私信继续发送）" : "停止失败"))
                        .catch((e: unknown) => push("停止异常: " + errMsg(e)))
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

          <AiReplyCard push={push} />

          <Section className="mb-3.5" data-od-id="live-auto-dm" title="自动私信配置">
            <div className="mb-3.5 grid grid-cols-1 gap-3.5 md:grid-cols-3">
              <FormField label="发送上限" hint="每场直播最多发送私信条数">
                <Input
                  type="number"
                  min="1"
                  max="100"
                  value={dmLimit}
                  onChange={(e) => setDmLimit(e.target.value)}
                  className="font-mono"
                  aria-label="每场最多发送私信条数"
                />
              </FormField>
              <FormField label="间隔（秒）" hint="两条私信之间的等待时间">
                <Input
                  type="number"
                  min="1"
                  step="0.1"
                  value={dmInterval}
                  onChange={(e) => setDmInterval(e.target.value)}
                  className="font-mono"
                  aria-label="两条私信之间的间隔秒数"
                />
              </FormField>
              <FormField label="延迟抖动（秒）" hint="格式：50,120 = 随机区间；60 = 固定延迟">
                <Input
                  value={dmJitter}
                  onChange={(e) => setDmJitter(e.target.value)}
                  className="font-mono"
                  aria-label="延迟抖动区间"
                />
              </FormField>
            </div>

            <div className="mb-3 flex items-center gap-2 text-[0.76rem]">
              <Switch
                checked={forceRescan}
                onCheckedChange={setForceRescan}
                aria-label="强制重扫"
              />
              <span className="text-[var(--color-text-muted)]">
                强制重扫（忽略已处理记录，重新匹配私信目标）
              </span>
            </div>

            <div>
              <Toolbar className="mb-2.5">
                <span className="text-[0.88rem] font-semibold text-[var(--color-text)]">
                  私信词库
                </span>
                <span className="text-[0.75rem] text-[var(--color-text-muted)]">
                  每行一条，勾选 = 启用，发送时随机抽已启用的一条
                </span>
              </Toolbar>
              <div className="flex flex-col gap-2" data-od-id="dm-templates">
                {dmTemplates.map((t, i) => (
                  <div key={i} className="flex items-center gap-2.5">
                    <Switch
                      checked={t.enabled}
                      onCheckedChange={(v) => {
                        const next = [...dmTemplates];
                        next[i] = { ...next[i], enabled: v };
                        setDmTemplates(next);
                      }}
                      aria-label={"启用模板 " + (i + 1)}
                    />
                    <Input
                      className="flex-1"
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
              <Toolbar className="mt-2.5">
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => setDmTemplates(dmTemplates.map((t) => ({ ...t, enabled: true })))}
                >
                  全选
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() =>
                    setDmTemplates(dmTemplates.map((t) => ({ ...t, enabled: false })))
                  }
                >
                  全不选
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  onClick={() => setDmTemplates([...dmTemplates, { text: "", enabled: true }])}
                >
                  添加一条
                </Button>
                <Button
                  variant="danger-outline"
                  size="sm"
                  onClick={() => setDmTemplates(dmTemplates.filter((t) => t.enabled))}
                >
                  删除选中
                </Button>
                <span className="font-mono text-[0.72rem] text-[var(--color-text-muted)]">
                  已启用 {dmTemplates.filter((t) => t.enabled).length} / {dmTemplates.length} 条
                </span>
                {ready && (
                  <>
                    <Button
                      size="sm"
                      onClick={() =>
                        api
                          .saveDmPool(
                            dmTemplates
                              .filter((t) => t.text && t.text.trim())
                              .map((t) => ({ text: t.text.trim(), enabled: t.enabled })),
                          )
                          .then((r) =>
                            push(r.ok ? "词库已保存 · " + r.count + " 条" : "保存失败"),
                          )
                          .catch((e: unknown) => push("保存异常: " + errMsg(e)))
                      }
                    >
                      保存词库
                    </Button>
                    <Button
                      variant="secondary"
                      size="sm"
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
                    </Button>
                  </>
                )}
              </Toolbar>
            </div>
          </Section>

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
              <div className="flex max-h-[236px] flex-col gap-0.5 overflow-y-auto">
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
            </Section>
          </div>

          <Section
            data-od-id="comment-stats"
            title="实时评论统计列表"
            actions={
              <Button variant="ghost" size="sm" data-od-id="review-open" onClick={openReview}>
                <Eye className="h-3.5 w-3.5" />进入查阅模式
              </Button>
            }
          >
            <div className="mb-2.5 text-[0.75rem] text-[var(--color-text-muted)]">
              共 <b className="font-mono font-semibold text-[var(--color-text)]">{rows.length}</b> 条弹幕记录 · 去重{" "}
              <b className="font-mono font-semibold text-[var(--color-text)]">{dedupCount}</b> 条 · 实际发言{" "}
              <b className="font-mono font-semibold text-[var(--color-text)]">{dedup}</b> 人 · 待发送私信{" "}
              <b className="font-mono font-semibold text-[var(--color-text)]">{waitCount}</b> 条 · 已发送私信{" "}
              <b className="font-mono font-semibold text-[var(--color-text)]">{sentCount}</b> 条
            </div>
            <div className="-mx-4 -mb-4 overflow-x-auto">
              <table className="w-full border-collapse">
                <thead>
                  <tr>
                    <Th>发送时间</Th>
                    <Th>发言人</Th>
                    <Th>评论内容</Th>
                    <Th>私信状态</Th>
                    <Th>私信文案</Th>
                    <Th>私信时间</Th>
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
                  {rows.slice(0, 12).map((r) => (
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
                        {r.dmStatus === "un" ? (
                          <span className="text-[var(--color-text-muted)]">—</span>
                        ) : (
                          <Tone tone={DM_META[r.dmStatus][1]}>{DM_META[r.dmStatus][0]}</Tone>
                        )}
                      </Td>
                      <Td
                        title={
                          r.dmStatus === "fail" && r.reason
                            ? `${r.dmText}\n失败原因: ${r.reason}`
                            : r.dmText
                        }
                      >
                        <span
                          className={cn(
                            "block truncate",
                            r.dmText
                              ? "text-[var(--color-text)]"
                              : "text-[var(--color-text-muted)]"
                          )}
                        >
                          {r.dmText || "未发送"}
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
function errMsg(e: unknown): string {
  return (e as { message?: string })?.message || String(e);
}

interface ReviewModeProps {
  rows: Row[];
  onClose: () => void;
  push: (msg: string, holdMs?: number) => void;
  sendDm: (r: Row) => void;
  goMsg?: (name: string, text?: string) => void;
}

function ReviewMode({ rows, onClose, push, sendDm, goMsg }: ReviewModeProps) {
  const [q, setQ] = useState("");
  const [st, setSt] = useState<"all" | DmStatus>("all");
  const [asc, setAsc] = useState(false);
  const [exp, setExp] = useState<number | null>(null);
  /** 2026-09-08：私信发送失败详情弹窗（点行内失败提示打开） */
  const [failRow, setFailRow] = useState<Row | null>(null);

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
      className="fixed inset-0 z-[60] flex flex-col bg-[var(--color-background)]"
      initial={{ opacity: 0, y: 14 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0 }}
      transition={{ duration: 0.18 }}
      data-od-id="live-review"
    >
      <div className="flex shrink-0 flex-wrap items-center gap-3 border-b
                      border-[var(--color-border)] bg-[var(--color-surface)] px-5 py-3.5">
        <Button variant="ghost" size="sm" data-od-id="review-back" onClick={onClose}>
          ‹ 返回实时流
        </Button>
        <h2 className="text-[0.95rem] font-semibold text-[var(--color-text)]">评论查阅模式</h2>
        <div className="min-w-0 flex-1" />
        <Badge variant="outline">只读 · 实时入库</Badge>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        <div className="mx-auto w-full max-w-[1680px] px-5 pb-10 pt-4.5">
          <Toolbar className="mb-3">
            <Input
              className="min-w-[200px] flex-1"
              placeholder="搜索昵称 / 评论内容 / 私信文案…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            <Select
              value={st}
              onValueChange={(v) => setSt(v as "all" | DmStatus)}
            >
              <SelectTrigger className="h-8 w-[124px]" aria-label="私信状态筛选">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">全部状态</SelectItem>
                <SelectItem value="un">未私信</SelectItem>
                <SelectItem value="wait">待发送</SelectItem>
                <SelectItem value="sent">已发送</SelectItem>
                <SelectItem value="fail">发送失败</SelectItem>
              </SelectContent>
            </Select>
            <Button variant="ghost" size="sm" onClick={() => setAsc((s) => !s)}>
              <ArrowUpDown className="h-3.5 w-3.5" />
              {asc ? "时间 ↑" : "时间 ↓"}
            </Button>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => {
                setSt("all");
                setQ("");
              }}
            >
              重置
            </Button>
            <Button size="sm" data-od-id="review-export" onClick={exportCSV}>
              <Download className="h-3.5 w-3.5" />导出 CSV
            </Button>
          </Toolbar>

          <div className="mb-2.5 flex flex-wrap items-center gap-2.5 text-[0.75rem]
                          text-[var(--color-text-muted)]">
            <span>
              共 <b className="font-mono font-semibold text-[var(--color-text)]">
                {filtered.length}
              </b> 条
            </span>
            <div className="flex flex-wrap gap-1.5">
              {pills.map(([id, l, c]) => {
                const on = st === id;
                return (
                  <button
                    key={id}
                    type="button"
                    onClick={() => setSt(id as "all" | DmStatus)}
                    className={cn(
                      "inline-flex cursor-pointer items-center gap-1.5 rounded-full border px-2.5 py-1",
                      "text-[0.75rem] transition-colors duration-200",
                      on
                        ? "border-[var(--color-accent)] bg-[var(--color-accent-soft)] " +
                          "font-semibold text-[var(--color-accent)]"
                        : "border-[var(--color-border)] bg-[var(--color-surface)] " +
                          "text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                    )}
                  >
                    {l}
                    <span className="font-mono text-[0.68rem] opacity-80">{c}</span>
                  </button>
                );
              })}
            </div>
          </div>

          <Card className="overflow-hidden p-0">
            <div className="overflow-x-auto">
              <table className="w-full border-collapse">
                <thead>
                  <tr>
                    <Th width={30} />
                    <Th>发送时间</Th>
                    <Th>发言人</Th>
                    <Th>评论内容</Th>
                    <Th>私信状态</Th>
                    <Th>私信文案</Th>
                    <Th>私信时间</Th>
                    <Th width={150}>操作</Th>
                  </tr>
                </thead>
                <tbody>
                  {filtered.map((r) => (
                    <Fragment key={r.id}>
                      <tr className="transition-colors hover:bg-[var(--color-surface-raised)]">
                        <Td>
                          <span className="font-mono text-[var(--color-text-muted)]">
                            {exp === r.id ? "▾" : "▸"}
                          </span>
                        </Td>
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
                        <Td className="max-w-[260px]">
                          <span className="block truncate">{r.content}</span>
                        </Td>
                        <Td>
                          {r.dmStatus === "un" ? (
                            <span className="text-[var(--color-text-muted)]">—</span>
                          ) : (
                            <Tone tone={DM_META[r.dmStatus][1]}>{DM_META[r.dmStatus][0]}</Tone>
                          )}
                        </Td>
                        <Td
                          title={
                            r.dmStatus === "fail" && r.reason
                              ? `${r.dmText}\n失败原因: ${r.reason}`
                              : r.dmText
                          }
                        >
                          <span
                            className={cn(
                              "block truncate",
                              r.dmText
                                ? "text-[var(--color-text)]"
                                : "text-[var(--color-text-muted)]"
                            )}
                          >
                            {r.dmText || "未发送"}
                          </span>
                        </Td>
                        <Td mono className="whitespace-nowrap">
                          {r.dmTime || <span className="text-[var(--color-text-muted)]">—</span>}
                        </Td>
                        <Td>
                          <Toolbar className="gap-1">
                            <Button
                              variant="link"
                              size="sm"
                              onClick={() => setExp(exp === r.id ? null : r.id)}
                            >
                              详情
                            </Button>
                            <Button variant="link" size="sm" onClick={() => sendDm(r)}>
                              发私信
                            </Button>
                            <Button
                              variant="link"
                              size="sm"
                              onClick={() => goMsg?.(r.name, r.dmText || "")}
                            >
                              去私信中心
                            </Button>
                          </Toolbar>
                        </Td>
                      </tr>
                      {exp === r.id && (
                        <tr key={r.id + "-d"}>
                          <Td colSpan={8} className="bg-[var(--color-surface-raised)] pt-1.5 pb-3.5">
                            <div className="my-2.5 rounded-[var(--radius-md)]
                                            border border-[var(--color-border)]
                                            bg-[var(--color-surface)] p-3.5">
                              <h4 className="mb-2 text-[0.72rem] tracking-[0.06em]
                                             text-[var(--color-text-muted)]">
                                发言历史 · {r.name}
                              </h4>
                              <div className="mb-3 flex flex-col gap-1.5">
                                {rows
                                  .filter((x) => x.name === r.name)
                                  .slice(0, 5)
                                  .map((x, i) => (
                                    <div key={i} className="flex items-baseline gap-2.5
                                                             text-[0.78rem]">
                                      <span className="shrink-0 font-mono text-[0.68rem]
                                                       text-[var(--color-text-muted)]">
                                        {x.time}
                                      </span>
                                      <span className="text-[var(--color-text)]">{x.content}</span>
                                    </div>
                                  ))}
                              </div>
                              <h4 className="mb-2 text-[0.72rem] tracking-[0.06em]
                                             text-[var(--color-text-muted)]">
                                私信内容
                              </h4>
                              <div className="text-[0.82rem] text-[var(--color-text)]">
                                {r.dmStatus === "un" ? (
                                  <span className="text-[var(--color-text-muted)]">
                                    尚未对该发言人发送私信
                                  </span>
                                ) : (
                                  <span>
                                    <Tone tone={DM_META[r.dmStatus][1]}>
                                      {DM_META[r.dmStatus][0]}
                                    </Tone>
                                    {"　"}
                                    {r.dmText || "（文案未填写）"}
                                    {r.dmTime ? "　·　" + r.dmTime : ""}
                                  </span>
                                )}
                                {r.dmStatus === "fail" && r.reason && (
                                  <div
                                    onClick={() => setFailRow(r)}
                                    title="点击查看失败原因详情与处理建议"
                                    className="mt-1.5 flex cursor-pointer items-center gap-2
                                               rounded-[var(--radius-sm)] border
                                               border-[color-mix(in_srgb,var(--color-danger)_25%,transparent)]
                                               bg-[var(--color-danger-soft)] px-2.5 py-1.5
                                               text-[0.75rem] text-[var(--color-danger)]"
                                  >
                                    <span
                                      className="shrink-0 rounded-full px-1.5 py-px
                                                 text-[0.68rem] font-semibold text-white"
                                      style={{ background: failInfoOf(r).color }}
                                    >
                                      {failInfoOf(r).label}
                                    </span>
                                    <span className="min-w-0 flex-1 truncate">{r.reason}</span>
                                    <span className="shrink-0 opacity-75">详情 ›</span>
                                  </div>
                                )}
                              </div>
                            </div>
                          </Td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
            {filtered.length === 0 && <Blank>无匹配记录</Blank>}
          </Card>
        </div>
      </div>
      {/* 2026-09-08：私信发送失败原因弹窗（区分调度堵塞/凭证失效/风控等） */}
      <FailReasonModal row={failRow} onClose={() => setFailRow(null)} />
    </motion.div>
  );
}