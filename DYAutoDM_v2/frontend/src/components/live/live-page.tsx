import { useState, useEffect, useMemo, useRef, useCallback } from "react";

import { useQuery } from "@tanstack/react-query";

import { AnimatePresence } from "framer-motion";

import {
  Play, Pause, Square, Heart, Send, Settings2, Mic, Eye, LogIn, Users, X,
} from "lucide-react";

import { PageProps, ReusePayload, RoomConfig, LiveRoom } from "../../api/client";

import RoomConfigPage from "./RoomConfigPage";

import RoomManagePage from "./RoomManagePage";

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
  Section, Tone, Blank, SegmentedTabs, Toolbar, KeyValue,
} from "@/components/page/kit";

import { cn } from "@/lib/utils";

import {
  LiveStream, TaskListResponse, RealAcct, FeedItem, Row, DM_META, acctValid, toDmStatus, fmtTime, recordsToRows, Th, Td, AiReplyCard,
} from "./live-shared";

import { ReviewMode, errMsg } from "./LiveReviewMode";
import EngineCards from "./engine-cards";

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
  const [myLikes] = useState(0);
  const [burst] = useState(0);
  const [batchN, setBatchN] = useState("10");
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
              <EngineCards push={push} />
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

          <Section
            className="mb-3.5"
            data-od-id="live-input"
            title="直播间"
            description="选策略决定「怎么发」；直播间与绑定关系在「直播间管理」里维护"
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
                title="对当前直播间发起连麦申请（经账号浏览器执行）"
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

          <AiReplyCard push={push} />

          <Section
            className="mb-3.5"
            data-od-id="live-auto-dm"
            title="生效的自动私信配置"
            description={
              selCfg
                ? `来自策略「${selCfg.name || sidOf(selCfg)}」· 任务进行中修改请点「管理策略」改后点「重启」`
                : "尚未选择主播间配置（上方下拉选择）"
            }
            actions={
              <Button
                variant="ghost"
                size="sm"
                data-od-id="live-cfg-goto"
                onClick={() => setCfgMgr(true)}
              >
                <Settings2 className="h-3.5 w-3.5" />管理策略
              </Button>
            }
          >
            {selCfg ? (
              <div className="flex flex-col gap-3">
                <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
                  <KeyValue
                    cols={1}
                    items={[
                      { k: "发送上限", v: String(selCfg.max_target ?? "—"), mono: true },
                    ]}
                  />
                  <KeyValue
                    cols={1}
                    items={[{ k: "间隔", v: `${selCfg.interval ?? "—"} s`, mono: true }]}
                  />
                  <KeyValue
                    cols={1}
                    items={[{ k: "延迟抖动", v: String(selCfg.delay || "—"), mono: true }]}
                  />
                </div>
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-[0.76rem]
                                text-[var(--color-text-secondary)]">
                  <span>
                    自动申请连麦：
                    <b className="font-mono text-[var(--color-text)]">
                      {selCfg.auto_link_mic
                        ? `开（${selCfg.link_mic_mode === "video" ? "视频" : "语音"}）`
                        : "关"}
                    </b>
                  </span>
                  <span>
                    监听账号：
                    <b className="font-mono text-[var(--color-text)]">
                      {activeAcct || selCfg.acct || "未选择"}
                    </b>
                  </span>
                </div>
                <div>
                  <div className="mb-1.5 text-[0.78rem] font-semibold text-[var(--color-text)]">
                    私信词库
                    <span className="ml-2 font-normal text-[var(--color-text-muted)]">
                      发送时随机抽已启用的一条
                    </span>
                  </div>
                  <div className="flex flex-col gap-1">
                    {(selCfg.dm_pool || []).length === 0 && (
                      <span className="text-[0.75rem] text-[var(--color-text-muted)]">
                        该策略尚未设置词库 · 请点「管理策略」补充
                      </span>
                    )}
                    {(selCfg.dm_pool || []).map((t, i) => (
                      <div
                        key={i}
                        className="flex items-start gap-2 rounded-[var(--radius-sm)]
                                   border border-[var(--color-border)] bg-[var(--color-surface)]
                                   px-2.5 py-1.5 text-[0.78rem]"
                      >
                        <Tone tone={t.enabled ? "ok" : "mute"}>
                          {t.enabled ? "启用" : "停用"}
                        </Tone>
                        <span className="min-w-0 flex-1 break-all text-[var(--color-text-secondary)]">
                          {t.text || "（空）"}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>
              </div>
            ) : (
              <Blank>
                请在上方「直播间」区选择一条已保存的策略，
                或点「管理策略」新建一条。
              </Blank>
            )}
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
