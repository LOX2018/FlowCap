import { useState, useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  X, RefreshCw, SearchIcon,
} from "lucide-react";
import { PageProps } from "../../api/client";
import { PageContainer, PageHeader } from "@/components/layout/app-shell";
import { Avatar, hue, nowHM } from "../../components/ui";
import { Loader2, Download, Paperclip, ImageIcon, VideoIcon, FileText, Mic } from "lucide-react";
import { ImageDown, FileDown, SquareDashedMousePointer } from "lucide-react";
import {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
} from "@/components/ui/dropdown-menu";
import LeadsSection from "./LeadsSection";
import { Button } from "@/components/ui/button";
import { Input, Textarea } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import {
  Section, Tone, Row, Blank, SegmentedTabs, Toolbar,
} from "@/components/page/kit";
import { cn } from "@/lib/utils";
import {
  MediaInfo, Msg, Conv, Account, RawConversation, ConversationsResp, MessagesApi,
  errMsg, isSystemTip,
  SearchHit, DailyCount,
} from "./message-shared";
import { ImageViewer } from "./message-viewer";
import { MsgBubble } from "./message-bubble";

export default function MessagesPage(props: PageProps) {
  const { push, ready, goDm } = props;
  const a = props.api as unknown as MessagesApi;
  // 当前账号提升到 App 级（由 App 常驻 conversations 轮询驱动，本页只读缓存，避免切页冷拉/双拉）
  const activeAcct = props.msgAcct || "";
  const setActiveAcct = props.setMsgAcct || (() => {});
  const [active, setActive] = useState("");
  const [draft, setDraft] = useState("");
  // 2026-09-05：发送通道选择。默认 ws（私信守护，稳定）；wp 为网页版通道。
  const [sendChannel, setSendChannel] = useState<"ws" | "wp">("ws");
  // 2026-09-14：留资线索子页 —— 原「AI 获客」页按作用域打散归类而来
  // （线索表含 account 列，是按账号产出的资产，且产生于私信对话）。
  const [subPage, setSubPage] = useState<"conv" | "leads">("conv");
  // 2026-09-06：会话搜索 —— 按昵称过滤定位会话（用户要求）
  const [convSearch, setConvSearch] = useState("");
  const [showSearch, setShowSearch] = useState(false);
  // 2026-09-13 用户反馈修复：更新状态提升到 App 级（切页不丢）。
  // 兜底：App 未提供时退回本地 state（保持组件可独立使用）。
  const refreshState = props.refreshState;
  const setRefreshState = props.setRefreshState;
  const refreshing = !!refreshState;
  const setRefreshing = (v: boolean) => {
    if (!setRefreshState) return;
    setRefreshState(v ? { account: activeAcct, startedAt: Date.now() } : null);
  };
  // 图片预览弹层（点击缩略图后展示）
  const [viewer, setViewer] = useState<MediaInfo | null>(null);
  // 附件菜单显示状态
  const [showAttach, setShowAttach] = useState(false);
  // 文件选择
  const fileInputRef = useRef<HTMLInputElement>(null);
  const handlePickFile = (accept: string) => {
    setShowAttach(false);
    if (fileInputRef.current) {
      fileInputRef.current.accept = accept;
      fileInputRef.current.click();
    }
  };
  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    e.target.value = "";
    if (!file) return;
    // 2026-09-06：接入图片发送（后端直发全链路 ①-⑥，走 /api/messages/send_image）
    if (!file.type.startsWith("image/")) {
      push("仅支持图片（视频/文件发送尚未接入）");
      return;
    }
    if (file.size > 20 * 1024 * 1024) {
      push("图片超过 20MB 限制");
      return;
    }
    if (!conv || !conv.conv_id) {
      push("请先选择有效会话");
      return;
    }
    push(`正在发送图片: ${file.name} (${Math.round(file.size / 1024)}KB)…`);
    const reader = new FileReader();
    reader.onload = () => {
      const b64 = (reader.result as string).split(",")[1] || "";
      if (!b64) {
        push("图片读取失败");
        return;
      }
      a.sendImage(activeAcct || "", conv.conv_id || "", b64, file.name)
        .then((r) => {
          if (r && r.ok) {
            push("图片已发送");
          } else {
            push("图片发送失败: " + ((r && (r.msg || r.error)) || ""));
          }
        })
        .catch((err) => push("图片发送异常: " + errMsg(err)));
    };
    reader.onerror = () => push("图片读取失败");
    reader.readAsDataURL(file);
  };

  // React Query 客户端：更新会话后用于失效所有缓存（避免显示已删除的旧数据）
  const qc = useQueryClient();
  // 2026-08-31：更新会话耗时 3~6 分钟，按钮显示实时耗时让用户知道还在跑
  const [refreshElapsed, setRefreshElapsed] = useState(0);
  // 2026-09-17：语音转写进行中标记（对照上游「语音转文字」能力）。
  const [transcribing, setTranscribing] = useState(false);
  // 2026-09-17：长图导出进行中标记
  // （2026-09-29：原 `exportingLab`（导出 ChatLab）随该按钮下线一并移除；
  //   后端端点保留，仅私信中心不再有入口）
  const [exportingImg, setExportingImg] = useState(false);
  /** 2026-09-18（E1）：选区导出 —— selMode=是否处于选区模式；selAnchor/selEnd=首/末条 seq */
  const [selMode, setSelMode] = useState(false);
  const [selAnchor, setSelAnchor] = useState<number | null>(null);
  const [selEnd, setSelEnd] = useState<number | null>(null);
  /** 2026-09-18（E1）：选区导出进行中 */
  const [exportingSel, setExportingSel] = useState<"" | "png" | "html">("");
  /** 2026-09-17（E6）：正在单条转写的 msg_id（空=无；用于该气泡转圈） */
  const [transcribeOne, setTranscribeOne] = useState("");
  // 2026-09-17：会话内检索面板（对照上游 SearchBar：文本 / 日期 / 媒体三模式）。
  const [showDmSearch, setShowDmSearch] = useState(false);
  const [dmQuery, setDmQuery] = useState("");
  const [dmHits, setDmHits] = useState<SearchHit[]>([]);
  const [dmTotal, setDmTotal] = useState(0);
  const [dmBusy, setDmBusy] = useState(false);
  const [dmMedia, setDmMedia] = useState<"" | "image" | "video" | "media">("");
  const [dmDays, setDmDays] = useState<DailyCount[]>([]);
  const [dmShowCal, setDmShowCal] = useState(false);
  /** 跳转请求：{msgId, ts} —— 命中/引用/日历都会设置它 */
  // 2026-09-18 审查修复（HIGH）：`pendingConv` = 该跳转**期望落在哪个会话**。
  // 旧实现切会话后立刻消费 jumpTo，而新会话消息还没到（convMsgs.length 初值 0）
  // → 目标在陈旧 DOM 上查不到 → 两条 setJumpTo(null) 把目标清掉 → 点击「没反应」。
  // 现在 effect 只在「当前会话 == 期望会话」时才尝试定位。
  const [jumpTo, setJumpTo] = useState<{ msgId?: string | null; ts?: number;
                                          text?: string; pendingConv?: string } | null>(null);

  // 2026-08-31：更新会话期间每秒累加耗时，让用户看到任务仍在推进
  useEffect(() => {
    if (!refreshing || !refreshState) {
      setRefreshElapsed(0);
      return;
    }
    // 基于 startedAt 计算：切页回来能接着正确计时（原来切页归零重计）
    const tick = () => setRefreshElapsed(
      Math.max(0, Math.floor((Date.now() - refreshState.startedAt) / 1000)));
    tick();
    const t = setInterval(tick, 1000);
    return () => clearInterval(t);
  }, [refreshing, refreshState]);

  // 账号列表（读取 App 常驻轮询的共享缓存）
  const accountsQ = useQuery({
    queryKey: ["accounts"],
    queryFn: async (): Promise<Account[]> => {
      return (await a.getAccounts()) as unknown as Account[];
    },
    enabled: !!ready,
  });

  // 会话列表：仅在本页挂载时轮询（切走即停，避免 App 级常驻轮询导致日志刷屏 + 渲染崩溃）
  const convsQ = useQuery({
    queryKey: ["msg-convs", activeAcct],
    queryFn: async (): Promise<Conv[]> => {
      const d = (await a.getConversations(activeAcct)) as unknown as ConversationsResp & {
        recvDaemonDown?: boolean;
      };
      const list = (d && d.conversations) || [];
      return list.map((c, i) => ({
        // id 必须用 conv_id（会话唯一标识），不能用数组下标 i：
        // 列表 5s 轮询且按 last_ts 排序，下标会随顺序变化而改变，
        // 导致 active 指向错位 -> 聊天框变空、列表「乱跳」。
        id: c.conv_id || "rc" + i,
        conv_id: c.conv_id,
        acct: activeAcct,
        hue: hue((c.name || "x").length * 2),
        name: c.name || "会话" + i,
        unread: c.unread || 0,
        avatar: c.avatar || "",
        // 2026-09-17（E5）：群聊标识 —— 后端下发 conv_type/is_group；
        // 存量行缺失时按 conv_id 是否纯数字回退（与后端同判据）。
        isGroup: c.is_group ?? (c.conv_type ? c.conv_type === 2
                                 : /^\d+$/.test(String(c.conv_id || ""))),
        msgs: (c.messages || []).map((m, j) => ({
          id: "rm" + i + "_" + j,
          dir: (m.dir || "in") as "in" | "out",
          type: m.type || "text",
          text: m.text || "",
          mt: m.time || nowHM(),
        })),
      }));
    },
    enabled: !!ready && !!activeAcct,
    refetchInterval: 5000,
    staleTime: 10000,
  });

  const realAccts = accountsQ.data || [];
  const allConvs: Conv[] = convsQ.data || [];
  // 2026-09-17（E5）：会话类型筛选（全部 / 单聊 / 群聊）。与昵称搜索**叠加**生效。
  const [convKind, setConvKind] = useState<"all" | "direct" | "group">("all");
  const groupCount = allConvs.filter((c) => c.isGroup).length;
  // 2026-09-06：按昵称搜索过滤（大小写不敏感的包含匹配）
  const shownConvs: Conv[] = allConvs
    .filter((c) => (convKind === "all" ? true
                  : convKind === "group" ? !!c.isGroup
                  : !c.isGroup))
    .filter((c) => !convSearch.trim()
      || (c.name || "").toLowerCase().includes(convSearch.trim().toLowerCase()));

  // 当前选中会话对象（先取出 conv_id，供详情 query 使用）
  const conv: Conv =
    shownConvs.find((c) => c.id === active) ||
    shownConvs[0] || {
      id: "",
      conv_id: "",
      acct: "",
      name: "暂无会话",
      hue: "0",
      unread: 0,
      msgs: [],
    };

  // 会话详情（聊天记录）：必须用独立 query，不能塞进列表缓存。
  // 原因：列表每 5s 轮询（refetchInterval 5000）会用后端新数据整体覆盖
  // ["msg-convs"] 缓存，而列表接口的 messages 恒为 []，
  // 若把消息写入列表缓存，点开后 5 秒内就会被清空（实测现象）。
  const detailQ = useQuery({
    queryKey: ["msg-detail", activeAcct, conv.conv_id],
    queryFn: async (): Promise<Msg[]> => {
      if (!activeAcct || !conv.conv_id) return [];
      const raw = (await a.getConversation(activeAcct, conv.conv_id)) as unknown as {
        ok?: boolean;
        conversation?: RawConversation;
      };
      const list = (raw && raw.conversation && raw.conversation.messages) || [];
      return list
        // 过滤解析噪音：空媒体对象（如 "[未知媒体] {}"）不是真实消息。
        // 后端已修（空对象不入库），此处兜底历史脏数据。
        // 2026-09-04:加过滤 [分享视频] 脏数据（WS 错误解析噪音）。
        .filter((m) => {
          // 2026-09-17 修正（对齐后端 messages.py 的同口径规则）：
          //   旧实现 `!/^\[(未知媒体|分享视频|系统提示)\]/` 把**带内容的真实分享**
          //   也一并滤掉了（`[分享视频] 视频ID x` / 新增的 `[分享商品] 标题`），
          //   而后端明确保留了它们（"带 ID 的 [分享视频] 是真实视频分享，应正常展示"）
          //   → 前后端口径不一致 = 真实分享在前端**永远看不见**。
          //   现改为只滤**裸噪音**：整条就是 `[分享视频]`，或 `[未知媒体] …`。
          //   `[系统提示] …` 交给 MsgBubble 的专用分支渲染（它有 sysM 分支）。
          const t = (m.text || "").trim();
          if (t === "[分享视频]") return false;
          if (/^\[未知媒体\]/.test(t)) return false;
          return true;
        })
        .map((m, j) => ({
        id: m.msg_id ? "mid_" + m.msg_id : "dm" + conv.conv_id + "_" + j,
        dir: (m.dir || (m.role === "me" ? "out" : "in")) as "in" | "out",
        type: m.type || "text",
        text: m.text || "",
        mt: m.time || nowHM(),
        image_url: m.image_url || undefined,  // 2026-09-02：后端解密后的真原图
        thumb_url: m.thumb_url || undefined,  // 2026-09-25（H-25）：契约内缩略图
        // 2026-09-05：来源通道，后端已兜底 'ws'，这里再兜一层
        source: (m.source === "wp" ? "wp" : "ws") as "ws" | "wp",
        // 2026-09-17：语音转写文本 + 引用回复（均由后端透传，前端只渲染）
        voiceText: m.transcription ? String(m.transcription) : undefined,
        reply: m.reply || null,
        recalled: m.recalled === true,
        ts: typeof m.ts === "number" ? m.ts : undefined,
        // 2026-09-18（E1/E2）：后端下发的消息序号（渲染端点同口径），选区/单条存图锚点
        seq: typeof m.seq === "number" ? m.seq : undefined,
        // 2026-09-18 审查修复（HIGH）：透传视频要素（后端 extra.video），
        // 否则 MsgBubble 的封面/时长恒空（点播分支拿不到 poster/duration）。
        video: m.video || null,
      }));
    },
    enabled: !!ready && !!activeAcct && !!conv.conv_id,
    staleTime: 3000,
    // 2026-09-16 实机修复：WS 收消息入库后，聊天记录不刷新。
    // 原因：本 query 只设了 staleTime，**没有 refetchInterval** ——
    //   react-query 的 staleTime 只是「下次机会是否重取」的标记，
    //   不产生定时轮询；没有 refetch 触发源 ⇒ 只有挂载/切会话时拉一次，
    //   之后 WS 新消息落库了，聊天记录也永远停在旧快照。
    // 列表 query 有 refetchInterval:5000 能刷新，唯独详情不会 ⇒
    //   表现为「左侧列表动了、右侧聊天不动」。
    // 修：补 refetchInterval 与列表同频（5s），保证 WS 落库 5s 内可见。
    refetchInterval: 5000,
  });

  // 聊天框渲染优先用详情 query 的结果
  const convMsgs: Msg[] = detailQ.data || conv.msgs || [];
  const curAcct = realAccts.find((x) => x.name === activeAcct) || realAccts[0] || null;

  // 2026-09-17：跳转定位（命中 / 引用 / 日历共用）。
  // 先按 msg_id 精确定位（data-msg-id），退化按 ts 找最近一条；命中后高亮 2.4s。
  useEffect(() => {
    if (!jumpTo) return;
    // 2026-09-18 审查修复（HIGH）：目标会话尚未就位时**不要**消费该跳转
    // （否则在新会话消息到达前就被 setJumpTo(null) 清掉，表现为点击无反应）。
    if (jumpTo.pendingConv && jumpTo.pendingConv !== conv.conv_id) return;
    const root = document.querySelector<HTMLElement>("[data-dm-scroll]");
    if (!root) return;
    let el: HTMLElement | null = null;
    if (jumpTo.msgId) {
      el = root.querySelector<HTMLElement>(`[data-msg-id="${CSS.escape(jumpTo.msgId)}"]`);
    }
    // 仅 ref_msg_id 缺失时才用「同文本最近一条」兜底（#52：refText 此前只用于守卫、从未定位）
    if (!el && jumpTo.text) {
      const want = jumpTo.text.trim().replace(/\s+/g, " ");
      const nodes = Array.from(root.querySelectorAll<HTMLElement>("[data-dm-text]"));
      for (let i = nodes.length - 1; i >= 0; i--) {
        const got = (nodes[i].dataset.dmText || "").trim().replace(/\s+/g, " ");
        if (got && got === want) { el = nodes[i]; break; }
      }
    }
    if (!el && jumpTo.ts) {
      // 按时间戳就近定位（日历「跳到那天第一条」用）
      const nodes = Array.from(root.querySelectorAll<HTMLElement>("[data-msg-ts]"));
      let best: HTMLElement | null = null;
      for (const n of nodes) {
        const v = Number(n.dataset.msgTs || 0);
        if (v >= jumpTo.ts && (!best || v < Number(best.dataset.msgTs || 0))) best = n;
      }
      el = best || nodes[0] || null;
    }
    if (el) {
      el.scrollIntoView({ block: "center", behavior: "smooth" });
      el.classList.add("dm-jump-flash");
      const t = window.setTimeout(() => el?.classList.remove("dm-jump-flash"), 2400);
      setJumpTo(null);
      return () => window.clearTimeout(t);
    }
    // 本会话消息已就位（convMsgs.length > 0）却找不到目标 → 才放弃
    if (convMsgs.length > 0) setJumpTo(null);
  }, [jumpTo, convMsgs.length, conv.conv_id]);

  // 2026-09-17：执行检索（会话内 / 全库）
  const runDmSearch = (opts: { withinConv: boolean }) => {
    if (!activeAcct) return;
    const hasCond = !!dmQuery.trim() || !!dmMedia || !!opts.withinConv;
    if (!hasCond) {
      push("请输入关键词，或选择媒体类型 / 限定当前会话");
      return;
    }
    setDmBusy(true);
    a.searchMessages(activeAcct, {
      q: dmQuery.trim(),
      convId: opts.withinConv ? conv.conv_id : undefined,
      mediaType: dmMedia || undefined,
      pageSize: 50,
    })
      .then((r) => {
        const items = (r?.items || []) as SearchHit[];
        setDmHits(items);
        setDmTotal(Number(r?.total || 0));
        if (!items.length) push("没有匹配的消息");
      })
      .catch((e: unknown) => push("检索失败: " + (e instanceof Error ? e.message : String(e))))
      .finally(() => setDmBusy(false));
  };

  // 2026-09-17：加载逐日消息量（日历）
  const loadCalendar = () => {
    if (!activeAcct || !conv.conv_id) return;
    setDmShowCal(true);
    setDmBusy(true);
    a.conversationDaily(activeAcct, conv.conv_id)
      .then((r) => {
        const d = (r as { days?: DailyCount[] })?.days || [];
        setDmDays(d);
        if (!d.length) push("当前会话暂无聊天记录");
      })
      .catch((e: unknown) => push("日历加载失败: " + (e instanceof Error ? e.message : String(e))))
      .finally(() => setDmBusy(false));
  };

  useEffect(() => {
    if (!goDm) return;
    const { name, text } = goDm;
    const existing = shownConvs.find((c) => c.name === name);
    if (existing) {
      setActiveAcct(existing.acct);
      setActive(existing.id);
      setDraft(text || "");
    } else {
      setDraft(text || "");
    }
    push("已打开 " + name + " 的会话，文案已预填");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [goDm]);

  const send = () => {
    if (!draft.trim()) return;
    // 校验改为看 conv_id 是否有效（id 现为 conv_id，不再是 "rc"+下标）
    if (!conv || !conv.conv_id) {
      push("请先选择有效会话");
      return;
    }
    const convId = conv.conv_id;
    a.sendDm(activeAcct, convId, draft.trim(), sendChannel)
      .then((r) => {
        if (r && r.ok) {
          push("私信已发送");
          setDraft("");
        } else {
          push("发送失败: " + ((r && (r.msg || r.error)) || ""));
        }
      })
      .catch((e) => push("发送异常: " + errMsg(e)));
  };

  const openConv = (id: string) => {
    setActive(id);
    // 2026-09-18 审查修复（HIGH）：切会话必须清空选区锚点。
    // 旧实现保留上一个会话的 selAnchor/selEnd，导出时用 **新会话的 seq 空间**
    // 套旧锚点取值（Math.min/max），会导出完全非预期的区间。
    setSelAnchor(null);
    setSelEnd(null);
  };

  return (
    <PageContainer>
      <PageHeader
        title="私信中心"
        description="手动回复支持 WS 守护 / 网页版双通道发送"
        actions={
          <SegmentedTabs
            value={subPage}
            onChange={(v) => setSubPage(v as "conv" | "leads")}
            items={[
              { value: "conv", label: "会话" },
              { value: "leads", label: "留资线索" },
            ]}
          />
        }
      />

      {subPage === "leads" ? (
        <Section title="留资线索" data-od-id="msg-leads">
          <LeadsSection {...props} />
        </Section>
      ) : (
      <>

      <Section className="mb-3" data-od-id="msg-acct-select">
        <div className="flex flex-wrap items-center gap-3">
          {curAcct ? (
            <>
              <span className="text-[0.82rem] font-semibold text-[var(--color-text)]">
                当前私信账号 · {curAcct.name}
              </span>
              <Tone
                tone={
                  curAcct.loggedIn
                    ? "ok"
                    : curAcct.level === "nosign"
                      ? "warn"
                      : "danger"
                }
              >
                {curAcct.label || "凭证状态未知"}
              </Tone>
            </>
          ) : (
            <span className="text-[0.82rem] text-[var(--color-text-muted)]">
              暂无账号 · 请在「账号」页添加并登录
            </span>
          )}
          <div className="flex-1" />
          {realAccts.length > 0 ? (
            <SegmentedTabs
              value={activeAcct}
              onChange={(name) => {
                setActiveAcct(name);
                setActive("");
                push("已切换到 " + name + " 的私信通道");
              }}
              items={realAccts.map((acct) => ({ value: acct.name, label: acct.name }))}
            />
          ) : (
            <span className="font-mono text-[0.72rem] text-[var(--color-text-muted)]">
              无已授权账号
            </span>
          )}
        </div>
      </Section>

      <div className="grid grid-cols-[3fr_7fr] items-start gap-3.5" data-od-id="messages-panel">
        {/* ── 左：会话列表 ── */}
        <Section
          title="会话列表"
          className="flex h-[calc(100vh-136px)] min-h-[480px] flex-col"
          contentClassName="flex min-h-0 flex-1 flex-col overflow-hidden"
          actions={
            <Toolbar>
              {/* 更新会话：按需触发前移捕获（BCC 拉会话列表 + 会话详情/聊天记录）后写库。
                  与账号页「引擎校验」区分：引擎校验只判守护活性，不跑捕获。 */}
              <Button
                variant="ghost"
                size="sm"
                data-od-id="refresh-conv"
                disabled={refreshing || !activeAcct}
                title={
                  activeAcct
                    ? `重新拉取 ${activeAcct} 的会话列表与聊天记录`
                    : "请先选择账号"
                }
                onClick={() => {
                  if (!activeAcct || refreshing) return;
                  setRefreshing(true);
                  // 更新为长任务：提示停留 12s，否则用户错过结果。
                  // 2026-09-15：去掉内部黑话「BCC」与过时 ETA（旧文案写 3~6 分钟，
                  // 实测已降到 ~1 分钟级）；按钮上已有实时耗时计数，无需再写死预估。
                  push(
                    `正在更新会话 · ${activeAcct} · 拉取会话列表与聊天记录…`,
                    12000,
                  );
                  a.addLog("INFO", `更新会话开始 · ${activeAcct}`).catch(() => {});
                  a.refreshConversations(activeAcct, true)
                    .then((r) => {
                      if (r && r.ok) {
                        push(
                          `更新完成 · ${activeAcct} · 会话 ${r.n_conv} 个（消息 ${r.n_msg} 条）· 耗时 ${r.elapsed}s`,
                          12000,
                        );
                        a.addLog(
                          "SUCCESS",
                          `更新会话完成 · ${activeAcct} · 会话 ${r.n_conv}（消息 ${r.n_msg}）· ${r.elapsed}s`,
                        ).catch(() => {});
                      } else {
                        push("更新失败: " + ((r && r.error) || "未知错误"));
                        a.addLog(
                          "ERROR",
                          `更新会话失败 · ${activeAcct}: ${(r && r.error) || "未知错误"}`,
                        ).catch(() => {});
                      }
                    })
                    .catch((e: unknown) => {
                      const msg = e instanceof Error ? e.message : String(e);
                      push("更新失败: " + msg);
                      a.addLog("ERROR", `更新会话失败 · ${activeAcct}: ${msg}`).catch(() => {});
                    })
                    .finally(() => {
                      setRefreshing(false);
                      // 2026-08-31：更新会话后必须**失效**缓存，而不只是 refetch。
                      // 实测：清空数据库后前端仍显示旧数据（React Query
                      // staleTime 10~20s + WebView2 磁盘缓存），
                      // 用户会看到已删除的污染数据和旧昵称。
                      // 此处 invalidateQueries 强制丢弃旧缓存重新拉取。
                      qc.invalidateQueries().catch(() => {});
                      convsQ.refetch().catch(() => {});
                    });
                }}
              >
                {refreshing ? (
                  <>
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    更新中… {refreshElapsed}s
                  </>
                ) : (
                  <>
                    <RefreshCw className="h-3.5 w-3.5" />
                    更新会话
                  </>
                )}
              </Button>

              <Button
                variant="ghost"
                size="sm"
                data-od-id="search-conv"
                title="按昵称搜索会话"
                onClick={() => {
                  setShowSearch((s) => !s);
                  if (showSearch) setConvSearch(""); // 收起时清空过滤
                }}
              >
                {showSearch ? (
                  <X className="h-3.5 w-3.5" />
                ) : (
                  <>
                    <SearchIcon className="h-3.5 w-3.5" />
                    搜索
                  </>
                )}
              </Button>
            </Toolbar>
          }
        >
          {/* 2026-09-17（E5）：会话类型筛选（全部 / 单聊 / 群聊）。
              纯前端过滤，零外呼；与昵称搜索叠加。群聊数取自后端 conv_type。 */}
          {allConvs.length > 0 && (
            <div className="flex items-center gap-1.5 pb-2 pt-0.5">
              <SegmentedTabs
                value={convKind}
                onChange={(v) => setConvKind(v as "all" | "direct" | "group")}
                items={[
                  { value: "all", label: `全部 ${allConvs.length}` },
                  { value: "direct", label: "单聊" },
                  { value: "group", label: `群聊 ${groupCount}` },
                ]}
              />
            </div>
          )}
          {/* 2026-09-06：会话搜索框（按昵称过滤定位） */}
          {showSearch && (
            <div className="flex items-center gap-2 pb-2">
              <Input
                className="h-[34px] flex-1 text-[0.78rem]"
                autoFocus
                placeholder="输入昵称关键字过滤会话…"
                value={convSearch}
                onChange={(e) => setConvSearch(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") {
                    setConvSearch("");
                    setShowSearch(false);
                  }
                }}
              />
              {convSearch && (
                <span className="whitespace-nowrap font-mono text-[0.72rem] text-[var(--color-text-muted)]">
                  {shownConvs.length}/{allConvs.length} 个
                </span>
              )}
            </div>
          )}
          {/* 2026-09-29：高度自适应 —— 原 `max-h-[calc(100vh-186px)]` 是「猜」
              头部 + 筛选条开销的魔数，与右列 `h-[calc(100vh-136px)]` 互不相关，
              筛选/搜索框条件显示一变就对不齐。现改为 flex 撑满 Card 剩余高度
              （Card 已与右列同高），列表自身滚动 —— 两列底部**结构性**对齐。 */}
          <div className="mt-3 flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto">
            {shownConvs.length === 0 && convSearch.trim() && allConvs.length > 0 && (
              <div className="px-2.5 py-3 text-[0.78rem] text-[var(--color-text-muted)]">
                没有昵称包含「{convSearch.trim()}」的会话
              </div>
            )}
            {shownConvs.length === 0 && !(convSearch.trim() && allConvs.length > 0)
              && (allConvs.length > 0) && (
              <div className="px-2.5 py-3 text-[0.78rem] text-[var(--color-text-muted)]">
                当前筛选（{convKind === "group" ? "仅群聊" : "仅单聊"}）下没有会话
              </div>
            )}
            {shownConvs.length === 0 && allConvs.length === 0 && (
              <div className="px-2.5 py-3 text-[0.78rem] text-[var(--color-text-muted)]">
                暂无会话（接收守护未收到消息）
              </div>
            )}
            {shownConvs.map((c) => (
              <div key={c.id} data-od-id={"conv-" + c.id}>
                <Row active={c.id === active} onClick={() => openConv(c.id)}>
                  <Avatar name={c.name} h={c.hue} src={c.avatar} />
                  <div className="min-w-0 flex-1">
                    <div className="flex justify-between gap-2 text-[0.82rem] font-medium text-[var(--color-text)]">
                      <span className="flex min-w-0 items-center gap-1.5">
                        {/* 2026-09-17（E5）：群聊徽标。后端 conv_type 透传，
                            纯展示、零外呼；单聊不显示徽标以免噪音。 */}
                        {c.isGroup ? (
                          <span
                            data-od-id={"conv-group-badge-" + c.id}
                            className="shrink-0 rounded-[var(--radius-sm)] border
                                       border-[var(--color-border-strong)]
                                       bg-[var(--color-info-soft)] px-1 py-[1px]
                                       font-mono text-[0.62rem] font-normal
                                       text-[var(--color-info)]"
                            title="群聊会话"
                          >
                            群
                          </span>
                        ) : null}
                        <span className="truncate">{c.name}</span>
                      </span>
                      <span className="shrink-0 font-mono text-[0.68rem] font-normal text-[var(--color-text-muted)]">
                        {c.msgs && c.msgs.length ? c.msgs[c.msgs.length - 1].mt : ""}
                      </span>
                    </div>
                    <div className="truncate text-[0.74rem] text-[var(--color-text-muted)]">
                      {c.msgs && c.msgs.length
                        ? c.msgs[c.msgs.length - 1].type === "text"
                          ? c.msgs[c.msgs.length - 1].text
                          : c.msgs[c.msgs.length - 1].dir === "in"
                            ? "收到一条新消息"
                            : "已发送"
                        : ""}
                    </div>
                  </div>
                  {c.unread > 0 && (
                    <Badge variant="accent" className="shrink-0 font-mono">
                      {c.unread}
                    </Badge>
                  )}
                </Row>
              </div>
            ))}
          </div>
        </Section>

        {/* ── 右：对话详情 + 输入区 ── */}
        <Card className="min-w-0 overflow-hidden">
          <div className="flex h-[calc(100vh-136px)] min-h-[480px] flex-col">
            <div className="flex items-center gap-2.5 border-b border-[var(--color-border)] px-3.5 py-3">
              <Avatar name={conv.name} h={conv.hue} sm src={conv.avatar} />
              <span className="font-semibold text-[var(--color-text)]">{conv.name}</span>
              <Toolbar className="flex-1 justify-end">
                <Button
                  variant="ghost"
                  size="sm"
                  data-od-id="voice-transcribe"
                  disabled={transcribing || !activeAcct}
                  title={
                    activeAcct
                      ? `把 ${activeAcct} 未转写的语音消息转成文字（只处理语音，不查昵称）`
                      : "请先选择账号"
                  }
                  onClick={() => {
                    if (!activeAcct || transcribing) return;
                    setTranscribing(true);
                    push(`正在转写语音 · ${activeAcct}…`, 8000);
                    a.transcribeVoice(activeAcct, "")
                      .then((r) => {
                        if (r && r.ok) {
                          push(
                            `语音转写完成 · 成功 ${r.succeeded ?? 0} 条` +
                              (r.skipped ? ` · 跳过 ${r.skipped} 条（缺发送者信息）` : ""),
                            10000,
                          );
                          // 2026-09-18 审查修复（#42/#43）：原先无 key 的
                          // `qc.invalidateQueries()` 会失效**全应用所有 query**
                          // （账号/会话列表/详情…），而此处只有消息详情变了
                          // （本项目会话重拉实测 3~6 分钟）。改为只失效消息详情。
                          qc.invalidateQueries({ queryKey: ["msg-detail"] }).catch(() => {});
                          qc.invalidateQueries({ queryKey: ["msg-convs"] }).catch(() => {});
                        } else {
                          // 失败原因分级提示（不静默）：no-uuid / bcc-unavailable /
                          // no-pending / no-text-returned 都直接告诉用户。
                          push(`语音转写未完成 · ${r?.reason || r?.error || "未知原因"}`);
                        }
                      })
                      .catch((e: unknown) => {
                        push(
                          "语音转写失败: " +
                            (e instanceof Error ? e.message : String(e)),
                        );
                      })
                      .finally(() => setTranscribing(false));
                  }}
                >
                  {transcribing ? (
                    <>
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      转写中…
                    </>
                  ) : (
                    <>
                      <Mic className="h-3.5 w-3.5" />
                      语音转写
                    </>
                  )}
                </Button>
                {/* 2026-09-29：导出三合一 —— 「导出长图 / 选区导出 / 导出会话」
                    合并为一个「导出」总按钮，点击弹出 3 个子选项（用户要求）。
                    三个子项的**行为与 disabled 判据逐条原样保留**，只是换了承载容器：
                      · 导出长图 —— 原 `dm-export-img`：直接渲染整会话 PNG（渲染中禁用）
                      · 选区导出 —— 原 `dm-export-sel`：切进选区模式（再点退出）
                      · 导出会话 —— 原第 6 个按钮（无 data-od-id）：JSON 导出（**后端未接线，
                          见下方 `⚠️` 注释**）
                    稳定锚点：`dm-export` 为总按钮；三个子项保留原 `data-od-id`
                    （`dm-export-img` / `dm-export-sel` / `dm-export-json`），
                    既有自动化与文档按 ID 定位不受影响。 */}
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      variant="ghost"
                      size="sm"
                      data-od-id="dm-export"
                      title="导出当前会话：长图 PNG / 选区区间"
                      disabled={!conv.conv_id || exportingImg}
                    >
                      {exportingImg ? (
                        <><Loader2 className="h-3.5 w-3.5 animate-spin" />导出中…</>
                      ) : (
                        <><Download className="h-3.5 w-3.5" />导出</>
                      )}
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem
                      data-od-id="dm-export-img"
                      disabled={!conv.conv_id || exportingImg}
                      onSelect={async () => {
                        if (!conv.conv_id) return;
                        setExportingImg(true);
                        try {
                          const r = await a.renderChatPng(activeAcct, conv.conv_id, {
                            theme: "dark", scale: 2.0, asBase64: true,
                          });
                          if (r?.ok && r.data_uri) {
                            const el = document.createElement("a");
                            el.href = r.data_uri;
                            el.download = `chat_${conv.conv_id.replace(/[^0-9A-Za-z]/g, "_").slice(0, 40)}.png`;
                            el.click();
                            push(`长图已导出（${Math.round((r.bytes || 0) / 1024)} KB）`);
                          } else {
                            push(`长图导出失败 · ${r?.error || "未知原因"}`);
                          }
                        } catch (e: unknown) {
                          push("长图导出失败: " + (e instanceof Error ? e.message : String(e)));
                        } finally {
                          setExportingImg(false);
                        }
                      }}
                    >
                      <ImageDown className="h-3.5 w-3.5" />
                      导出长图（PNG）
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      data-od-id="dm-export-sel"
                      disabled={!conv.conv_id}
                      onSelect={() => {
                        // 2026-09-18（E1）：进入选区模式后，再点击聊天里**首条**
                        // 与**末条**消息定区间，浮条上导出 PNG/HTML。
                        setSelMode((v) => !v);
                        setSelAnchor(null);
                      }}
                    >
                      <SquareDashedMousePointer className="h-3.5 w-3.5" />
                      {selMode ? "退出选区模式" : "选区导出"}
                    </DropdownMenuItem>
                    {/* 2026-09-29：原「导出会话（JSON）」**已删除**（用户要求）。
                        该按钮自引入起就是空壳 —— 只 `push("已导出该会话为 JSON")`
                        一条 toast，**没有任何真实导出后端**；保留它等于给一个
                        不存在的功能留入口。真要做 JSON 导出时再新接后端端点，
                        而不是把这个桩搬回来。 */}
                  </DropdownMenuContent>
                </DropdownMenu>
                {/* 2026-09-29：原「导出 ChatLab」按钮已下线（用户要求）。
                    后端端点 `POST /api/messages/export/chatlab/download` 与
                    `client.ts` 的 `exportChatlab()` **保留**，由「知识库 · 来源导入」
                    链路继续使用 —— 此处仅移除私信中心的可见入口。 */}
                <Button
                  variant="ghost"
                  size="sm"
                  data-od-id="dm-search"
                  title="在当前会话或全库中检索消息（文本 / 日期 / 媒体）"
                  onClick={() => {
                    setShowDmSearch((s) => !s);
                    if (showDmSearch) {
                      setDmHits([]);
                      setDmTotal(0);
                      setDmShowCal(false);
                    }
                  }}
                >
                  <SearchIcon className="h-3.5 w-3.5" />
                  {showDmSearch ? "关闭检索" : "检索消息"}
                </Button>
              </Toolbar>
            </div>
            {/* 2026-09-17：会话内检索面板（对照上游 SearchBar：文本 / 日期 / 媒体） */}
            {showDmSearch && (
              <div className="mt-2 shrink-0 rounded-[var(--radius-md)] border border-[var(--color-border)]
                              bg-[var(--color-surface)] p-2.5">
                <div className="flex flex-wrap items-center gap-2">
                  <Input
                    className="h-[32px] min-w-[180px] flex-1 text-[0.78rem]"
                    placeholder="关键词（正文 / 引用 / 语音转写）…"
                    value={dmQuery}
                    onChange={(e) => setDmQuery(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter") runDmSearch({ withinConv: true }); }}
                  />
                  <Button size="sm" disabled={dmBusy}
                          onClick={() => runDmSearch({ withinConv: true })}>
                    本会话
                  </Button>
                  <Button size="sm" variant="ghost" disabled={dmBusy}
                          onClick={() => runDmSearch({ withinConv: false })}>
                    全库
                  </Button>
                  <div className="flex items-center gap-1">
                    {([["", "全部"], ["image", "图片"], ["video", "视频"], ["media", "媒体"]] as const)
                      .map(([v, label]) => (
                        <button
                          key={v || "all"}
                          onClick={() => setDmMedia(v)}
                          className={cn(
                            "rounded border px-2 py-0.5 text-[0.7rem]",
                            dmMedia === v
                              ? "border-[var(--color-accent)] bg-[var(--color-accent-soft)]"
                              : "border-[var(--color-border)] bg-transparent text-[var(--color-text-muted)]",
                          )}
                        >
                          {label}
                        </button>
                      ))}
                  </div>
                  <Button size="sm" variant="ghost" disabled={dmBusy || !conv.conv_id}
                          onClick={loadCalendar}>
                    日历
                  </Button>
                  {dmBusy && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
                </div>

                {/* 日历：逐日条数，点某天 → 跳到那天第一条 */}
                {dmShowCal && dmDays.length > 0 && (
                  <div className="mt-2 max-h-[132px] overflow-y-auto border-t border-[var(--color-border)] pt-2">
                    <div className="flex flex-wrap gap-1">
                      {dmDays.map((d) => (
                        <button
                          key={d.date}
                          title={`${d.date}：${d.count} 条`}
                          onClick={() => setJumpTo({ msgId: d.first_msg_id, ts: d.first_ts,
                                                     pendingConv: conv.conv_id })}
                          className="rounded border border-[var(--color-border)] px-1.5 py-0.5
                                     font-mono text-[0.68rem] hover:border-[var(--color-accent)]"
                        >
                          {d.date.slice(5)}
                          <span className="ml-1 text-[var(--color-text-muted)]">{d.count}</span>
                        </button>
                      ))}
                    </div>
                  </div>
                )}

                {/* 检索命中：点一条 → 定位到该消息 */}
                {dmHits.length > 0 && (
                  <div className="mt-2 max-h-[190px] overflow-y-auto border-t border-[var(--color-border)] pt-2">
                    <div className="mb-1 text-[0.7rem] text-[var(--color-text-muted)]">
                      命中 {dmTotal} 条（显示前 {dmHits.length} 条）
                    </div>
                    {dmHits.map((h, i) => (
                      <button
                        key={`${h.conv_id}-${h.msg_id ?? i}`}
                        className="block w-full rounded px-1.5 py-1 text-left hover:bg-[var(--color-surface-raised)]"
                        onClick={() => {
                          // 命中可能在别的会话：先切会话再定位（切会话后由 convMsgs 长度变化触发 effect）
                          // 2026-09-18 审查修复（HIGH）：带上期望会话，effect 等它到位再定位。
                          const target = allConvs.find((c) => c.conv_id === h.conv_id)
                            || shownConvs.find((c) => c.conv_id === h.conv_id);
                          if (target && target.id !== active) setActive(target.id);
                          setJumpTo({ msgId: h.msg_id, ts: h.ts, pendingConv: h.conv_id });
                        }}
                      >
                        <div className="truncate text-[0.76rem]">{h.snippet || h.text}</div>
                        <div className="text-[0.66rem] text-[var(--color-text-muted)]">
                          {h.conv_name} · {h.role === "me" ? "我" : "对方"}
                          {h.media_type ? ` · ${h.media_type}` : ""}
                        </div>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            )}
            <div className="flex min-h-0 flex-1 flex-col gap-2.5 overflow-y-auto p-3.5"
                 data-dm-scroll="1">
              {(() => {
                let lastDate = "";
                const nodes: React.ReactNode[] = [];
                convMsgs.forEach((m) => {
                  // 2026-09-05:日期分割线。mt 格式 YYYY-MM-DD HH:MM:SS,
                  // 提取日期部分,和上一条不同就插入分隔线。
                  const fullDate = (m.mt || "").slice(0, 10);
                  if (fullDate && fullDate !== lastDate) {
                    lastDate = fullDate;
                    nodes.push(
                      <div
                        key={`date-${fullDate}`}
                        className="my-2.5 flex items-center gap-2 text-[0.68rem]
                                   text-[var(--color-text-muted)]"
                      >
                        <span className="h-px flex-1 bg-[var(--color-border)]" />
                        <span className="rounded-full border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-0.5">
                          {fullDate}
                        </span>
                        <span className="h-px flex-1 bg-[var(--color-border)]" />
                      </div>
                    );
                  }
                  const sys = isSystemTip(m.text);
                  // 2026-09-18（E1/E2）：选区模式下点击行 = 定锚点（首条→末条）；
                  // 高亮 = 已在 [anchor, end] 区间内。E2 的单条存图按钮挂在行尾。
                  const inSel = selMode && selAnchor !== null && selEnd !== null
                    && m.seq !== undefined
                    && m.seq >= Math.min(selAnchor, selEnd)
                    && m.seq <= Math.max(selAnchor, selEnd);
                  nodes.push(
                    <div
                      className={cn(
                        "group flex max-w-[78%] items-center gap-2.5",
                        // 旧 `.msg { animation: slidein .3s ease both }` 保留（keyframes 在 global.css）
                        "animate-[slidein_0.3s_ease_both]",
                        sys ? "max-w-[84%] self-center" : m.dir === "out"
                          ? "flex-row-reverse self-end"
                          : "self-start",
                        selMode && "cursor-pointer rounded-[var(--radius-md)]",
                        selMode && inSel &&
                          "outline outline-1 -outline-offset-1 outline-[var(--color-accent)]",
                      )}
                      key={m.id}
                      onClick={selMode ? () => {
                        if (m.seq === undefined) {
                          push("该消息没有序号锚点（可能来自本地缓存）", 5000);
                          return;
                        }
                        if (selAnchor === null) {
                          setSelAnchor(m.seq);
                          push(`已选首条（seq=${m.seq}），再点末条`, 4000);
                        } else {
                          setSelEnd(m.seq);
                          push(`选区 seq=${Math.min(selAnchor, m.seq)}–${Math.max(selAnchor, m.seq)}，请在浮条导出`, 4000);
                        }
                      } : undefined}
                      /* 2026-09-17：跳转锚点 —— 命中/引用/日历定位靠这两个属性 */
                      data-msg-id={m.id.startsWith("mid_") ? m.id.slice(4) : undefined}
                      data-msg-ts={m.ts}
                      /* 2026-09-18 审查修复（#52）：ref_msg_id 缺失时按「同文本最近一条」
                         兜底定位，需要把正文挂到 DOM 上供查询。 */
                      data-dm-text={m.text || undefined}
                    >
                      <MsgBubble
                        m={m}
                        sys={sys}
                        account={activeAcct}
                        onOpenImage={setViewer}
                        onTranscribe={(mid) => {
                          // 2026-09-17（E6）：单条语音转写（用户显式触发）。
                          // 识别在账号登录态（BCC 页面上下文）里做，属外呼 —— 故绝不自动跑。
                          if (!activeAcct || !mid || transcribeOne === mid) return;
                          setTranscribeOne(mid);
                          push("正在转写该条语音…", 6000);
                          a.transcribeVoice(activeAcct, conv.conv_id, 1, mid)
                            .then((r) => {
                              if (r && r.ok) {
                                if (r.succeeded) push("转写完成", 4000);
                                else if (r.reason === "no-pending")
                                  push("该条无需转写（可能已转写或缺要素）", 6000);
                                else push(`未转写：${r.reason || "未知原因"}`, 7000);
                                // 2026-09-18 审查修复（#43）：同上，只失效消息详情。
                                qc.invalidateQueries({ queryKey: ["msg-detail"] }).catch(() => {});
                              } else {
                                push(`转写失败：${(r && r.error) || "未知错误"}`, 8000);
                              }
                            })
                            .catch(() => push("转写请求异常", 8000))
                            .finally(() => setTranscribeOne(""));
                        }}
                        transcribing={!!transcribeOne && transcribeOne ===
                          (m.id.startsWith("mid_") ? m.id.slice(4) : m.id)}
                        onJumpRef={(refId, refText) => {
                          // 2026-09-17：引用块点击 → 定位被引用消息（同会话内）。
                          // 有 ref_msg_id 走精确匹配；缺失时退化为「同文本最近一条」。
                          // 2026-09-18 审查修复（LOW→实修）：此前只判断 refText 存在、
                          // 却从不把它用于定位 → refId 缺失时生成空跳转（立即被清掉，点了没反应）。
                          if (refId || refText)
                            setJumpTo({ msgId: refId || undefined, text: refText || undefined,
                                        pendingConv: conv.conv_id });
                        }}
                      />
                      <span className="shrink-0 self-center font-mono text-[0.66rem] text-[var(--color-text-muted)]">
                        {(m.mt || "").slice(11, 16)}
                      </span>
                      {/* 2026-09-18（E2）：单条存图 —— hover 出现的小按钮。
                          走渲染端点 start_seq=end_seq=本条 seq，只渲染这一条。
                          seq 缺失（本地兜底消息）时不显示，避免导出错条。 */}
                      {m.seq !== undefined && !selMode && (
                        <button
                          type="button"
                          data-od-id={"msg-save-img-" + (m.id.startsWith("mid_") ? m.id.slice(4) : m.id)}
                          title="把这条消息保存为图片"
                          className="shrink-0 self-center rounded p-1 text-[var(--color-text-muted)]
                                     opacity-0 transition hover:text-[var(--color-accent)]
                                     focus:opacity-100 group-hover:opacity-100"
                          onClick={async (ev) => {
                            ev.stopPropagation();
                            if (!conv.conv_id || !activeAcct) return;
                            try {
                              const r = await a.renderChatPng(activeAcct, conv.conv_id, {
                                theme: "dark", scale: 2.0, asBase64: true,
                                startSeq: m.seq, endSeq: m.seq,
                              });
                              if (r?.ok && r.data_uri) {
                                const el = document.createElement("a");
                                el.href = r.data_uri;
                                el.download = `chat_msg_${m.seq}.png`;
                                el.click();
                                push("单条消息图已保存");
                              } else {
                                push(`保存失败 · ${r?.error || "未知原因"}`);
                              }
                            } catch (e: unknown) {
                              push("保存失败: " + (e instanceof Error ? e.message : String(e)));
                            }
                          }}
                        >
                          <ImageDown className="h-3 w-3" />
                        </button>
                      )}
                      {/* 2026-09-05：通道角标，仅在 WP 通道时显示（WS 是默认，不打扰） */}
                      {m.source === "wp" && (
                        <span
                          className="shrink-0 self-center rounded border border-[var(--color-border)]
                                     bg-[var(--color-surface)] px-1.5 py-0.5 font-mono text-[0.6rem]
                                     leading-none text-[var(--color-text-muted)]"
                          title="经抖音网页版通道收发"
                        >
                          网页
                        </span>
                      )}
                    </div>
                  );
                });
                return nodes;
              })()}
              {convMsgs.length === 0 && (
                <Blank className="py-2">暂无消息</Blank>
              )}
            </div>
            {/* 隐藏的文件选择 input */}
            <input
              ref={fileInputRef}
              type="file"
              className="hidden"
              onChange={handleFileChange}
            />
            {/* 2026-09-18（E1）：选区导出浮条 —— selMode 时出现。
                导出走既有渲染端点（start_seq/end_seq 闭区间），
                PNG = base64 直存；HTML = Blob 下载（内容不出机器）。 */}
            {selMode && (
              <div
                data-od-id="dm-sel-bar"
                className="flex flex-wrap items-center gap-2 border-t
                           border-[var(--color-border)] bg-[var(--color-surface-raised)]
                           px-3.5 py-2 text-[0.76rem]"
              >
                <SquareDashedMousePointer className="h-3.5 w-3.5 text-[var(--color-accent)]" />
                {selAnchor === null ? (
                  <span className="text-[var(--color-text-secondary)]">
                    点击聊天里的**首条**消息
                  </span>
                ) : selEnd === null ? (
                  <span className="text-[var(--color-text-secondary)]">
                    已选首条 seq={selAnchor} · 再点<strong>末条</strong>（或点它本身=单条）
                  </span>
                ) : (
                  <span className="font-mono text-[var(--color-text-secondary)]">
                    选区 seq={Math.min(selAnchor, selEnd)}–{Math.max(selAnchor, selEnd)}
                  </span>
                )}
                <div className="flex-1" />
                <Button
                  variant="secondary"
                  size="sm"
                  data-od-id="dm-sel-png"
                  disabled={selAnchor === null || selEnd === null || exportingSel !== ""}
                  onClick={async () => {
                    if (selAnchor === null || selEnd === null || !conv.conv_id) return;
                    setExportingSel("png");
                    try {
                      const r = await a.renderChatPng(activeAcct, conv.conv_id, {
                        theme: "dark", scale: 2.0, asBase64: true,
                        startSeq: Math.min(selAnchor, selEnd),
                        endSeq: Math.max(selAnchor, selEnd),
                      });
                      if (r?.ok && r.data_uri) {
                        const el = document.createElement("a");
                        el.href = r.data_uri;
                        el.download = `chat_sel_${Math.min(selAnchor, selEnd)}-${Math.max(selAnchor, selEnd)}.png`;
                        el.click();
                        push(`选区长图已导出（${Math.round((r.bytes || 0) / 1024)} KB）`);
                      } else {
                        push(`选区导出失败 · ${r?.error || "未知原因"}`);
                      }
                    } catch (e: unknown) {
                      push("选区导出失败: " + (e instanceof Error ? e.message : String(e)));
                    } finally {
                      setExportingSel("");
                    }
                  }}
                >
                  {exportingSel === "png" ? (
                    <><Loader2 className="h-3.5 w-3.5 animate-spin" />导出中…</>
                  ) : (
                    <><ImageDown className="h-3.5 w-3.5" />导出 PNG</>
                  )}
                </Button>
                <Button
                  variant="secondary"
                  size="sm"
                  data-od-id="dm-sel-html"
                  disabled={selAnchor === null || selEnd === null || exportingSel !== ""}
                  onClick={async () => {
                    if (selAnchor === null || selEnd === null || !conv.conv_id) return;
                    setExportingSel("html");
                    try {
                      const r = await a.renderChatHtml(activeAcct, conv.conv_id, {
                        theme: "dark",
                        startSeq: Math.min(selAnchor, selEnd),
                        endSeq: Math.max(selAnchor, selEnd),
                      });
                      if (r?.ok && r.html) {
                        const blob = new Blob([r.html], { type: "text/html;charset=utf-8" });
                        const href = URL.createObjectURL(blob);
                        const el = document.createElement("a");
                        el.href = href;
                        el.download = `chat_sel_${Math.min(selAnchor, selEnd)}-${Math.max(selAnchor, selEnd)}.html`;
                        el.click();
                        setTimeout(() => URL.revokeObjectURL(href), 8000);
                        push(`选区 HTML 已导出（${Math.round(r.html.length / 1024)} KB）`);
                      } else {
                        push(`HTML 导出失败 · ${r?.error || "未知原因"}`);
                      }
                    } catch (e: unknown) {
                      push("HTML 导出失败: " + (e instanceof Error ? e.message : String(e)));
                    } finally {
                      setExportingSel("");
                    }
                  }}
                >
                  {exportingSel === "html" ? (
                    <><Loader2 className="h-3.5 w-3.5 animate-spin" />导出中…</>
                  ) : (
                    <><FileDown className="h-3.5 w-3.5" />导出 HTML</>
                  )}
                </Button>
                <Button
                  variant="ghost"
                  size="sm"
                  data-od-id="dm-sel-cancel"
                  onClick={() => { setSelMode(false); setSelAnchor(null); setSelEnd(null); }}
                >
                  取消
                </Button>
              </div>
            )}
            {/* 2026-09-05：发送通道选择。
                ws = 私信守护 HTTP API（默认，稳定）
                wp = 抖音网页版 chat 页 IM SDK（需浏览器容器就绪） */}
            <div
              className="flex flex-wrap items-center gap-3 border-t border-[var(--color-border)]
                         px-3.5 py-1.5 text-[0.72rem] text-[var(--color-text-muted)]"
            >
              <span className="shrink-0">发送通道</span>
              <SegmentedTabs
                value={sendChannel}
                onChange={setSendChannel}
                items={[
                  { value: "ws", label: "WS 守护" },
                  { value: "wp", label: "网页版" },
                ]}
              />
              {sendChannel === "wp" && (
                <span className="text-[0.68rem] opacity-80">
                  经网页版通道发送
                </span>
              )}
            </div>
            <div
              className="flex flex-col gap-1.5 border-t border-[var(--color-border)] p-3.5"
              data-od-id="composer"
            >
              <div className="flex items-center gap-2.5">
                <Textarea
                  className="min-h-0 flex-1 resize-none"
                  rows={2}
                  placeholder="输入私信内容…"
                  value={draft}
                  onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => setDraft(e.target.value)}
                  onKeyDown={(e: React.KeyboardEvent<HTMLTextAreaElement>) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      send();
                    }
                  }}
                />
                <Button
                  variant="secondary"
                  size="icon"
                  title="添加附件"
                  onClick={() => setShowAttach((v) => !v)}
                >
                  <Paperclip className="h-4 w-4" />
                </Button>
                <Button data-od-id="send-msg" onClick={send}>
                  发送
                </Button>
              </div>
              {showAttach && (
                <div className="flex gap-2 py-1">
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => handlePickFile("image/*")}
                  >
                    <ImageIcon className="h-3.5 w-3.5" />
                    图片
                  </Button>
                  <Button
                    variant="secondary"
                    size="sm"
                    onClick={() => handlePickFile("video/*")}
                  >
                    <VideoIcon className="h-3.5 w-3.5" />
                    视频
                  </Button>
                  <Button variant="secondary" size="sm" onClick={() => handlePickFile("*")}>
                    <FileText className="h-3.5 w-3.5" />
                    文件
                  </Button>
                </div>
              )}
            </div>
          </div>
        </Card>
      </div>
      <ImageViewer media={viewer} onClose={() => setViewer(null)} />
      </>
      )}
    </PageContainer>
  );
}
