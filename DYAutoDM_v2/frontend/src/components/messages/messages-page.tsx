import { useState, useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  X, RefreshCw, Plus, SearchIcon,
} from "lucide-react";
import { PageProps } from "../../api/client";
import { PageContainer, PageHeader } from "@/components/layout/app-shell";
import { Avatar, hue, nowHM } from "../../components/ui";
import { Loader2, Download, Paperclip, ImageIcon, VideoIcon, FileText } from "lucide-react";
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
  MediaInfo, Msg, Conv, Account, RawConversation, ConversationsResp, MessagesApi, errMsg, isSystemTip,
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
  const [showNew, setShowNew] = useState(false);
  const [newName, setNewName] = useState("");
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
            push("图片发送失败: " + ((r && r.error) || ""));
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
  // 2026-09-06：按昵称搜索过滤（大小写不敏感的包含匹配）
  const shownConvs: Conv[] = convSearch.trim()
    ? allConvs.filter((c) =>
        (c.name || "").toLowerCase().includes(convSearch.trim().toLowerCase()),
      )
    : allConvs;

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
          const t = (m.text || "").trim();
          return !/^\[(未知媒体|分享视频|系统提示)\]/.test(t);
        })
        .map((m, j) => ({
        id: m.msg_id ? "mid_" + m.msg_id : "dm" + conv.conv_id + "_" + j,
        dir: (m.dir || (m.role === "me" ? "out" : "in")) as "in" | "out",
        type: m.type || "text",
        text: m.text || "",
        mt: m.time || nowHM(),
        image_url: m.image_url || undefined,  // 2026-09-02：后端解密后的真原图
        // 2026-09-05：来源通道，后端已兜底 'ws'，这里再兜一层
        source: (m.source === "wp" ? "wp" : "ws") as "ws" | "wp",
      }));
    },
    enabled: !!ready && !!activeAcct && !!conv.conv_id,
    staleTime: 3000,
  });

  // 聊天框渲染优先用详情 query 的结果
  const convMsgs: Msg[] = detailQ.data || conv.msgs || [];
  const curAcct = realAccts.find((x) => x.name === activeAcct) || realAccts[0] || null;

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
          push("发送失败: " + ((r && r.error) || ""));
        }
      })
      .catch((e) => push("发送异常: " + errMsg(e)));
  };

  const openConv = (id: string) => {
    setActive(id);
  };

  const createConv = () => {
    const nm = newName.trim();
    if (!nm) {
      push("请输入对方昵称");
      return;
    }
    a.requestDm(nm)
      .then((r) => {
        setShowNew(false);
        setNewName("");
        push(
          r && r.ok
            ? (r.msg || "已加入发送队列") + " · " + nm
            : "发送失败: " + ((r && r.error) || ""),
        );
      })
      .catch((e) => push("失败:异常 " + errMsg(e)));
  };

  return (
    <PageContainer>
      <PageHeader
        title="私信中心"
        description="会话列表 · 聊天记录 · 手动回复（WS 守护 / 网页版双通道发送）"
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
                    ? `经 BCC 重新拉取 ${activeAcct} 的会话列表与聊天记录`
                    : "请先选择账号"
                }
                onClick={() => {
                  if (!activeAcct || refreshing) return;
                  setRefreshing(true);
                  // 长任务（3~6 分钟）：提示停留 12s，否则用户错过结果
                  push(
                    `正在更新会话 · ${activeAcct} · 经 BCC 拉取会话列表与聊天记录…（约需 3~6 分钟）`,
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
              <Button
                variant="ghost"
                size="sm"
                data-od-id="new-conv"
                onClick={() => setShowNew((s) => !s)}
              >
                {showNew ? (
                  "取消"
                ) : (
                  <>
                    <Plus className="h-3.5 w-3.5" />
                    新建会话
                  </>
                )}
              </Button>
            </Toolbar>
          }
        >
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
          <div className="mt-3 flex max-h-[calc(100vh-186px)] flex-col gap-0.5 overflow-y-auto">
            {showNew && (
              <div
                className="flex items-center gap-2 pb-2"
                data-od-id="new-conv-form"
              >
                <Input
                  className="h-[34px] flex-1 text-[0.78rem]"
                  autoFocus
                  placeholder="输入对方昵称，回车创建…"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") createConv();
                    if (e.key === "Escape") setShowNew(false);
                  }}
                />
                <Button variant="secondary" size="sm" onClick={createConv}>
                  创建
                </Button>
              </div>
            )}
            {shownConvs.length === 0 && convSearch.trim() && allConvs.length > 0 && (
              <div className="px-2.5 py-3 text-[0.78rem] text-[var(--color-text-muted)]">
                没有昵称包含「{convSearch.trim()}」的会话
              </div>
            )}
            {shownConvs.length === 0 && !(convSearch.trim() && allConvs.length > 0) && (
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
                      <span className="truncate">{c.name}</span>
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
              <span className="font-mono text-[0.7rem] text-[var(--color-text-muted)]">
                会话 ID {conv.id}
              </span>
              <div className="flex-1" />
              <Button
                variant="ghost"
                size="sm"
                onClick={() => {
                  push("已导出该会话为 JSON");
                }}
              >
                <Download className="h-3.5 w-3.5" />
                导出会话
              </Button>
            </div>
            <div className="flex min-h-0 flex-1 flex-col gap-2.5 overflow-y-auto p-3.5">
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
                  nodes.push(
                    <div
                      className={cn(
                        "flex max-w-[78%] items-center gap-2.5",
                        // 旧 `.msg { animation: slidein .3s ease both }` 保留（keyframes 在 global.css）
                        "animate-[slidein_0.3s_ease_both]",
                        sys ? "max-w-[84%] self-center" : m.dir === "out"
                          ? "flex-row-reverse self-end"
                          : "self-start",
                      )}
                      key={m.id}
                    >
                      <MsgBubble m={m} sys={sys} onOpenImage={setViewer} />
                      <span className="shrink-0 self-center font-mono text-[0.66rem] text-[var(--color-text-muted)]">
                        {(m.mt || "").slice(11, 16)}
                      </span>
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
                  经浏览器容器发送，需 BCC 已就绪
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
