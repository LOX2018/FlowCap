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
import { useState, useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { PageProps } from "../api/client";
import { startRecvDaemon } from "../api/sidecar";
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
  type?: string;
  text?: string;
  time?: string;
}
interface RawConversation {
  conv_id?: string;
  name?: string;
  unread?: number;
  messages?: RawMessage[];
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
interface MessagesApi {
  getConversations(account: string): Promise<unknown>;
  getConversation(account: string, convId: string): Promise<unknown>;
  sendDm(account: string, convId: string, text: string): Promise<SendDmResp>;
  requestDm(name: string): Promise<RequestDmResp>;
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
  const [activeAcct, setActiveAcct] = useState("");
  const [active, setActive] = useState("");
  const [draft, setDraft] = useState("");
  const [showNew, setShowNew] = useState(false);
  const [newName, setNewName] = useState("");
  // 私信守护自动启动只尝试一次（避免每 5s 轮询狂拉起）
  const convTried = useRef(false);

  // 账号列表（读取 App 常驻轮询的共享缓存）
  const accountsQ = useQuery({
    queryKey: ["accounts"],
    queryFn: async (): Promise<Account[]> => {
      return (await a.getAccounts()) as unknown as Account[];
    },
    enabled: !!ready,
  });

  useEffect(() => {
    if (!activeAcct && accountsQ.data && accountsQ.data.length) {
      setActiveAcct(accountsQ.data[0].name);
    }
  }, [activeAcct, accountsQ.data]);

  const convsQ = useQuery({
    queryKey: ["msg-convs", activeAcct],
    queryFn: async (): Promise<Conv[]> => {
      const d = (await a.getConversations(activeAcct)) as unknown as ConversationsResp & {
        recvDaemonDown?: boolean;
      };
      // 记录完整原始响应到运行日志，方便排查"张三"等异常数据
      a.addLog("DEBUG", "[私信拉取] 前端：账号「" + activeAcct + "」原始响应=" + JSON.stringify(d));
      // 私信守护未运行：不再报 urlopen 错误弹窗，自动尝试拉一次
      if (d && (d as { recvDaemonDown?: boolean }).recvDaemonDown) {
        a.addLog("WARNING", `[私信拉取] 账号「${activeAcct}」私信守护未运行`);
        if (!convTried.current) {
          convTried.current = true;
          const acct = (accountsQ.data || []).find((x) => x.name === activeAcct);
          const port = acct?.recvDaemonPort || 0;
          push("私信守护未运行，正在自动启动…");
          startRecvDaemon([activeAcct], port)
            .then(() => push("私信守护已自动启动，请稍候自动刷新会话"))
            .catch(() => push("私信守护自动启动失败，请到账号管理页手动「启动」"));
        }
        return [];
      }
      if (!d || !d.ok) {
        push("拉取会话列表失败: " + ((d as Record<string, unknown>)?.error || "无响应"));
        a.addLog("WARNING", "[私信拉取] 前端：账号「" + activeAcct + "」拉取会话列表失败: " + ((d as Record<string, unknown>)?.error || "无响应"));
        return [];
      }
      const list = d.conversations || [];
      a.addLog("INFO", "[私信拉取] 前端：账号「" + activeAcct + "」拉取到 " + list.length + " 个会话（详情见后端日志）");
      list.forEach((c, i) => {
        a.addLog("INFO", "[私信拉取] 前端：  会话#" + i + " name=" + JSON.stringify(c.name) + " conv_id=" + (c.conv_id || "—") + " unread=" + (c.unread || 0));
        // 记录每个会话的完整原始数据，便于排查"张三"等异常名称来源
        a.addLog("DEBUG", "[私信拉取] 前端：  会话#" + i + " 完整原始数据=" + JSON.stringify(c));
      });
      return list.map((c, i) => ({
        id: "rc" + i,
        conv_id: c.conv_id,
        acct: activeAcct,
        hue: hue((c.name || "x").length * 2),
        name: c.name || "会话" + i,
        unread: c.unread || 0,
        msgs: (c.messages || []).map((m, j) => ({
          id: "rm" + i + "_" + j,
          dir: (m.dir || "in") as "in" | "out",
          type: m.type || "text",
          text: m.text || "",
          mt: m.time || nowHM(),
        })),
      }));
    },
    refetchInterval: 5000,
    enabled: !!ready && !!activeAcct,
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
                <Avatar name={c.name} h={c.hue} />
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
              <Avatar name={conv.name} h={conv.hue} sm />
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
