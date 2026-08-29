/**
 * 私信页
 *
 * 迁移自: DY_Spider_base/web/pages/messages.js
 * 原版职责: 会话列表、会话详情、手动回复
 * 迁移要点:
 *   - 旧版 setInterval 轮询 conversations/accounts → 改用 useQuery + refetchInterval
 *   - 旧版 window.ApiBridge.ready → props.ready
 *   - React.createElement → JSX
 *   - 删除 mock 会话假数据（cid 计数器、本地 convs 兜底、"你好，很高兴认识你"）
 *   - .catch(() => {}) → .catch(e => push('失败:' + ...))
 *   - requestDm 不在 client.ts，用本地 interface + as unknown as 转换
 */
import { useState, useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { PageProps } from "../api/client";
import { Avatar, Pill, Dot, hue, nowHM } from "../components/ui";

interface Msg {
  id: string;
  dir: "in" | "out";
  type: string;
  text: string;
  mt: string;
  dur?: string;
  title?: string;
}
interface Conv {
  id: string;
  conv_id?: string;
  acct: string;
  hue: string;
  name: string;
  unread: number;
  avatar?: string;
  msgs: Msg[];
}
interface Account {
  name: string;
  uid?: string;
  loggedIn?: boolean;
  level?: string;
  label?: string;
  recvDaemonPort?: number;
}
interface RawMessage {
  dir?: string;
  /** 后端原生命名：me=我发 / them=对方发（dir 为空时的兜底判据） */
  role?: string;
  type?: string;
  text?: string;
  time?: string;
}
interface RawConversation {
  conv_id?: string;
  name?: string;
  unread?: number;
  messages?: RawMessage[];
  avatar?: string;
}
interface ConversationsResp {
  ok?: boolean;
  conversations?: RawConversation[];
}
interface SendDmResp {
  ok: boolean;
  error?: string;
}
interface RequestDmResp {
  ok: boolean;
  msg?: string;
  error?: string;
}

/** client.ts 未提供 requestDm，本地扩展 */
interface RefreshConvsResp {
  ok: boolean;
  n_conv?: number;
  n_msg?: number;
  elapsed?: number;
  error?: string;
}
interface MessagesApi {
  getConversations(account: string): Promise<unknown>;
  getConversation(account: string, convId: string): Promise<unknown>;
  sendDm(account: string, convId: string, text: string): Promise<SendDmResp>;
  requestDm(name: string): Promise<RequestDmResp>;
  refreshConversations(account: string, withBrowser?: boolean): Promise<RefreshConvsResp>;
  getAccounts(): Promise<unknown>;
  addLog(level: string, text: string): Promise<unknown>;
}

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

function MsgBubble({ m }: { m: Msg }) {
  if (m.type === "text") return <div className="bubble">{m.text}</div>;
  if (m.type === "voice")
    return (
      <div className="bubble">
        <div className="voice">
          <span className="voice-bars">
            {[10, 18, 14, 22, 12, 20, 8, 16].map((hgt, i) => (
              <i key={i} style={{ height: hgt }} />
            ))}
          </span>
          <span className="vdur">{m.dur}</span>
        </div>
      </div>
    );
  if (m.type === "sticker")
    return (
      <div className="sticker" style={{ background: "oklch(70% 0.13 300)" }}>
        ✨
      </div>
    );
  if (m.type === "image")
    return (
      <div
        className="imgtile"
        style={{
          background: "linear-gradient(135deg, oklch(48% 0.13 210), oklch(26% 0.09 250))",
        }}
      >
        图片消息
      </div>
    );
  return (
    <div className="bubble">
      <div className="vshare">
        <div
          className="thumb"
          style={{
            background: "linear-gradient(135deg, oklch(46% 0.13 150), oklch(26% 0.09 190))",
          }}
        >
          <span className="play" aria-hidden="true" />
        </div>
        <div>
          <div className="ti">{m.title}</div>
          <div className="st">分享的视频</div>
        </div>
      </div>
    </div>
  );
}

export default function MessagesPage(props: PageProps) {
  const { push, ready, goDm } = props;
  const a = props.api as unknown as MessagesApi;
  const qc = useQueryClient();
  // 当前账号提升到 App 级（由 App 常驻 conversations 轮询驱动，本页只读缓存，避免切页冷拉/双拉）
  const activeAcct = props.msgAcct || "";
  const setActiveAcct = props.setMsgAcct || (() => {});
  const [active, setActive] = useState("");
  const [draft, setDraft] = useState("");
  const [showNew, setShowNew] = useState(false);
  const [newName, setNewName] = useState("");
  const [refreshing, setRefreshing] = useState(false);

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
        id: "rc" + i,
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
  const shownConvs: Conv[] = convsQ.data || [];
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

  const conv: Conv =
    shownConvs.find((c) => c.id === active) ||
    shownConvs[0] || {
      id: "",
      acct: "",
      name: "暂无会话",
      hue: "0",
      unread: 0,
      msgs: [],
    };

  const send = () => {
    if (!draft.trim()) return;
    if (!conv || !conv.id || conv.id.indexOf("rc") !== 0) {
      push("请先选择有效会话");
      return;
    }
    const convId = conv.conv_id || conv.id;
    a.sendDm(activeAcct, convId, draft.trim())
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
    // 会话列表接口 /conversations 的 messages 恒为空（列表不携带消息体），
    // 聊天记录必须点进会话时再调详情接口 /conversation 拉取并回填到本地缓存。
    const target = shownConvs.find((c) => c.id === id);
    const convId = target?.conv_id || "";
    if (!activeAcct || !convId) return;
    a.getConversation(activeAcct, convId)
      .then((raw) => {
        const d = raw as { ok?: boolean; conversation?: RawConversation };
        const list = (d && d.conversation && d.conversation.messages) || [];
        qc.setQueryData<Conv[]>(["msg-convs", activeAcct], (old) =>
          (old || []).map((c) =>
            c.id === id
              ? {
                  ...c,
                  msgs: list.map((m, j) => ({
                    id: "dm" + id + "_" + j,
                    dir: (m.dir || (m.role === "me" ? "out" : "in")) as "in" | "out",
                    type: m.type || "text",
                    text: m.text || "",
                    mt: m.time || nowHM(),
                  })),
                }
              : c,
          ),
        );
      })
      .catch((e: unknown) => {
        // 详情拉取失败不阻断会话切换，仅写日志；列表轮询会持续重试
        a.addLog("WARN", `会话详情拉取失败 · ${activeAcct}: ${errMsg(e)}`).catch(() => {});
      });
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
    <div>
      <div className="section-head">
        <div>
          <h2>私信中心</h2>
          <div className="desc">WebSocket 实时收发 · 文本 / 表情 / 语音 / 图片 / 视频</div>
        </div>
        <div className="head-row">
          {ready ? (
            <span className="badge-conn">
              <Dot c="ok" pulse />{" "}已连接（真实后端）
            </span>
          ) : (
            <>
              <span className="badge-conn">
                <Dot c="warn" />{" "}未连接
              </span>
              <span className="demo-tag">未连接</span>
            </>
          )}
        </div>
      </div>

      <div className="card" style={{ marginBottom: 12 }} data-od-id="msg-acct-select">
        <div className="head-row">
          {curAcct ? (
            <>
              <Avatar name={curAcct.name} h={hue(curAcct.name.length)} />
              <div style={{ flex: 1 }}>
                <div style={{ fontWeight: 600, fontSize: 13.5 }}>
                  当前私信账号 · {curAcct.name}
                </div>
                <div className="mono" style={{ fontSize: 11.5, color: "var(--muted)" }}>
                  UID {curAcct.uid || "—"} · 会话按账号隔离
                </div>
              </div>
              <Pill
                c={
                  curAcct.loggedIn
                    ? "ok"
                    : curAcct.level === "nosign"
                      ? "warn"
                      : "danger"
                }
              >
                {curAcct.label || "凭证状态未知"}
              </Pill>
            </>
          ) : (
            <div style={{ flex: 1, fontSize: 13.5, color: "var(--muted)" }}>
              暂无账号 · 请在「账号」页添加并登录
            </div>
          )}
          <div className="seg" style={{ marginLeft: 6 }}>
            {realAccts.map((acct) => (
              <button
                key={acct.name}
                className={activeAcct === acct.name ? "active" : ""}
                onClick={() => {
                  setActiveAcct(acct.name);
                  setActive("");
                  push("已切换到 " + acct.name + " 的私信通道");
                }}
              >
                <Avatar name={acct.name} h={hue(acct.name.length)} sm /> {acct.name}
              </button>
            ))}
            {realAccts.length === 0 && (
              <span className="mono" style={{ fontSize: 12, color: "var(--muted)" }}>
                无已授权账号
              </span>
            )}
          </div>
        </div>
      </div>

      <div className="grid cols-3-7" data-od-id="messages-panel">
        <div className="card" style={{ padding: 8 }}>
          <div className="head-row" style={{ padding: "4px 8px 10px" }}>
            <h3 style={{ marginBottom: 0 }}>会话列表</h3>
            <div style={{ flex: 1 }} />
            {/* 更新会话：按需触发前移捕获（BCC 拉会话列表 + 会话详情/聊天记录）后写库。
                与账号页「引擎校验」区分：引擎校验只判守护活性，不跑捕获。 */}
            <button
              className="btn sm ghost"
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
                push(`正在更新会话 · ${activeAcct} · 经 BCC 拉取会话列表与聊天记录…`);
                a.addLog("INFO", `更新会话开始 · ${activeAcct}`).catch(() => {});
                a.refreshConversations(activeAcct, true)
                  .then((r) => {
                    if (r && r.ok) {
                      push(
                        `更新完成 · ${activeAcct} · 会话 ${r.n_conv} 个（消息 ${r.n_msg} 条）· 耗时 ${r.elapsed}s`,
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
                    // 立即刷新会话列表，不必等 5s 轮询
                    convsQ.refetch().catch(() => {});
                  });
              }}
            >
              {refreshing ? "更新中…" : "⟳ 更新会话"}
            </button>
            <button
              className="btn sm ghost"
              data-od-id="new-conv"
              onClick={() => setShowNew((s) => !s)}
            >
              {showNew ? "取消" : "＋ 新建会话"}
            </button>
          </div>
          <div className="conv-list">
            {showNew && (
              <div
                className="head-row"
                style={{ padding: "2px 8px 8px", gap: 8 }}
                data-od-id="new-conv-form"
              >
                <input
                  className="input"
                  style={{ flex: 1, height: 34, fontSize: 12.5 }}
                  autoFocus
                  placeholder="输入对方昵称，回车创建…"
                  value={newName}
                  onChange={(e) => setNewName(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") createConv();
                    if (e.key === "Escape") setShowNew(false);
                  }}
                />
                <button className="btn sm ghost" onClick={createConv}>
                  创建
                </button>
              </div>
            )}
            {shownConvs.length === 0 && (
              <div style={{ padding: "14px 10px", color: "var(--muted)", fontSize: 12.5 }}>
                暂无会话（接收守护未收到消息）
              </div>
            )}
            {shownConvs.map((c) => (
              <button
                className={"conv" + (c.id === active ? " active" : "")}
                data-od-id={"conv-" + c.id}
                key={c.id}
                onClick={() => openConv(c.id)}
              >
                <Avatar name={c.name} h={c.hue} src={c.avatar} />
                <span className="info">
                  <span className="nm">
                    {c.name}
                    <span className="t">
                      {c.msgs && c.msgs.length ? c.msgs[c.msgs.length - 1].mt : ""}
                    </span>
                  </span>
                  <span className="pre">
                    {c.msgs && c.msgs.length
                      ? c.msgs[c.msgs.length - 1].type === "text"
                        ? c.msgs[c.msgs.length - 1].text
                        : c.msgs[c.msgs.length - 1].dir === "in"
                          ? "收到一条新消息"
                          : "已发送"
                      : ""}
                  </span>
                </span>
                {c.unread > 0 && <span className="unread">{c.unread}</span>}
              </button>
            ))}
          </div>
        </div>

        <div className="card thread-wrap" style={{ padding: 0 }}>
          <div className="thread">
            <div className="thread-head">
              <Avatar name={conv.name} h={conv.hue} sm src={conv.avatar} />
              <span className="nm">{conv.name}</span>
              <span className="mono" style={{ fontSize: 11, color: "var(--muted)" }}>
                会话 ID {conv.id}
              </span>
              <div style={{ flex: 1 }} />
              <button
                className="btn sm ghost"
                onClick={() => {
                  push("已导出该会话为 JSON");
                }}
              >
                导出会话
              </button>
            </div>
            <div className="msgs">
              {conv.msgs.map((m) => (
                <div className={"msg " + m.dir} key={m.id}>
                  <MsgBubble m={m} />
                  <span className="mtm">{m.mt}</span>
                </div>
              ))}
              {conv.msgs.length === 0 && (
                <div style={{ color: "var(--muted)", fontSize: 12.5, padding: 8 }}>
                  暂无消息
                </div>
              )}
            </div>
            <div className="composer" data-od-id="composer">
              <textarea
                className="textarea"
                placeholder="输入私信内容…"
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    send();
                  }
                }}
              />
              <button className="btn primary" data-od-id="send-msg" onClick={send}>
                发送
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
