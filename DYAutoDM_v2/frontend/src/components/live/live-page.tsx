import { useState, useEffect, useMemo, useRef } from "react";

import { useQuery } from "@tanstack/react-query";

import { AnimatePresence } from "framer-motion";

import {
  Play, Pause, Square, Heart, Send, Settings2, Mic, SearchIcon, Eye, LogIn, Users, X,
} from "lucide-react";

import { PageProps, ReusePayload } from "../../api/client";

import RoomConfigManager from "./RoomConfigManager";

import { Avatar, hue, KIND_NAME } from "../../components/ui";

import { PageContainer, PageHeader } from "@/components/layout/app-shell";

import { Button } from "@/components/ui/button";

import { Input } from "@/components/ui/input";

import { Badge } from "@/components/ui/badge";

import { Card, CardContent } from "@/components/ui/card";

import { Switch } from "@/components/ui/switch";

import { StatusDot } from "@/components/ui/status-dot";

import { EmptyState } from "@/components/ui/empty-state";

import {
  Section, Tone, Blank, SegmentedTabs, Toolbar, FormField,
} from "@/components/page/kit";

import { cn } from "@/lib/utils";

import {
  LiveStream, TaskListResponse, RealAcct, FeedItem, Row, DM_META, parseDelayRange, acctValid, toDmStatus, fmtTime, recordsToRows, Th, Td, AiReplyCard,
} from "./live-shared";

import { ReviewMode, errMsg } from "./LiveReviewMode";

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
