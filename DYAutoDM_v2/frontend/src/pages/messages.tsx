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

// 抖音下发的系统提示文案（非对话内容），需居中展示为提示气泡而非对话气泡
const SYS_TIPS = [
  "你已确认聊天",
  "对方已确认聊天",
  "你已回复",
  "Recall Content Hided",
  "消息已撤回",
  "对方正在输入",
];

/** 判断是否为系统提示：命中已知文案，或形如「你/对方 已…」的系统动作句 */
function isSystemTip(text: string): boolean {
  const t = (text || "").trim();
  if (!t) return false;
  if (SYS_TIPS.some((k) => t.includes(k))) return true;
  return /^(你|对方)(已|正在|已确认)/.test(t);
}

/** 媒体消息的双 URL 结构（2026-08-30 实测落地） */
type MediaInfo = {
  /** 缩略图：data:image/webp;base64,...（inline_pic，可直接渲染）或远程 URL */
  thumb: string;
  /** 原图远程 URL（抖音私有加密，浏览器不可直接解码，仅用于跳转/后续截获） */
  origin: string;
  /** 缩略图是否为内嵌 data URI（true=可直接 <img> 渲染） */
  inline: boolean;
  /** 是否为表情包（抖音贴纸走公开 CDN，实测可直接渲染） */
  isSticker?: boolean;
};

/**
 * 2026-08-31：抖音文本表情短代码 -> emoji。
 *
 * 实测：抖音 IM 的 `[捂脸]`/`[流泪]` 等是**纯文本短代码**，
 * 嵌在 message.text 里（如 "病例我没有[捂脸]"），**没有独立图片 URL**，
 * 与真正的贴纸表情（stickers 数组，带 static_url）是两回事。
 * 原样显示成 `[捂脸]` 观感差，这里映射成 emoji 提升可读性；
 * 未收录的保持原样，不会丢信息。
 */
const EMOJI_MAP: Record<string, string> = {
  捂脸: "😅", 流泪: "😢", 尬笑: "😅", 泪奔: "😭", 赞: "👍",
  握手: "🤝", 抱抱你: "🤗", 尴尬流汗: "😓", 笑哭: "😂",
  偷笑: "😏", 害羞: "😊", 爱心: "❤️", 玫瑰: "🌹",
  强: "💪", ok: "👌", 耶: "✌️", 生气: "😠", 惊讶: "😮",
  思考: "🤔", 困: "😴", 酷: "😎", 抠鼻: "🤨", 白眼: "🙄",
};

/** 把文本里的 [短代码] 替换为 emoji（未收录的保持原样） */
function renderTextWithEmoji(s: string): React.ReactNode[] {
  const parts = String(s || "").split(/(\[[^\[\]]{1,10}\])/g);
  return parts.map((p, i) => {
    const m = p.match(/^\[([^\[\]]{1,10})\]$/);
    if (m && EMOJI_MAP[m[1]]) {
      return (
        <span key={i} className="emoji" title={m[1]}>
          {EMOJI_MAP[m[1]]}
        </span>
      );
    }
    return <span key={i}>{p}</span>;
  });
}

/**
 * 解析媒体消息文本 -> 缩略图 + 原图双 URL。
 *
 * 后端 `_extract_media_text` 输出形态（按行）：
 *   [图片] data:image/webp;base64,<inline_pic>   ← 有内嵌缩略图时
 *   [原图] https://...                            ← 可选，原图远程地址
 * 或（无 inline_pic 的大图）：
 *   [图片] https://...thumb.image?...
 *   [原图] https://...origin...
 *
 * 实测依据：抖音 IM 图片消息体内嵌 inline_pic（标准 WebP base64，27/35 条可用，
 * 平均 3.5KB），远程 URL（thumb/medium/large/origin 四组）**全部为私有加密格式**
 * （熵 7.999/8.0，无标准图片魔数），浏览器无法解码，只能作跳转引用。
 */
function parseMedia(text: string): MediaInfo {
  const lines = (text || "").split("\n");
  let thumb = "";
  let origin = "";
  for (const raw of lines) {
    const line = raw.trim();
    if (/^\[原图\]/.test(line)) {
      origin = line.replace(/^\[原图\]\s*/, "").trim();
      continue;
    }
    const m = line.match(/^\[(图片|表情包)\]\s*(.+)$/);
    if (m && !thumb) {
      thumb = m[2].trim();
      continue;
    }
    // 裸 URL 兜底
    if (!thumb) {
      const u = line.match(/(data:image\/[\w+-]+;base64,\S+|https?:\/\/\S+)/);
      if (u) thumb = u[1];
    }
  }
  // 表情包标记：抖音贴纸表情走公开 CDN，实测可直接 <img> 渲染
  // （HTTP 200，image/jpeg|png|gif，0.23~0.34s）；
  // 而私信图片的 p*-sign.douyinpic.com 是私有加密，不可内嵌 —— 需区分。
  const isSticker = /^\[表情包\]/.test((text || "").trim());
  return {
    thumb,
    origin,
    isSticker,
    // 可直接 <img> 渲染的情形：
    //   1) data: URI（内联 base64，私信图片缩略图的主要形态）
    //   2) 图床公网链接（i.ibb.co / tucdn.wpon.cn，实测标准 image/webp）
    //   3) 表情包 URL（贴纸走公开 CDN，实测可直连）
    // 其余（抖音图片远程原图）为私有加密，不可内嵌。
    inline:
      /^data:image\//.test(thumb) ||
      /^https?:\/\/i\.ibb\.co\//.test(thumb) ||
      /^https?:\/\/tucdn\.wpon\.cn\//.test(thumb) ||
      isSticker,
  };
}

/** 原图预览弹层：点击缩略图后展示可放大的图 */
function ImageViewer({
  media,
  onClose,
}: {
  media: MediaInfo | null;
  onClose: () => void;
}) {
  if (!media) return null;
  return (
    <div className="imgviewer" onClick={onClose}>
      <div className="ivpanel" onClick={(e) => e.stopPropagation()}>
        <div className="ivhead">
          <span>{media.inline ? "缩略图（点击链接可看原图）" : "图片"}</span>
          <button className="btn sm ghost" onClick={onClose}>
            关闭
          </button>
        </div>
        <div className="ivbody">
          {/* 2026-08-31 实测：抖音 IM 消息里的 inline_pic 本身就是缩略图
              （典型 160×213），消息体内**不含全尺寸原图**；
              resource_url 的远程链是私有加密（熵 7.999），浏览器解不开。
              因此弹层只能放大显示已有的缩略图，真正的原图需 BCC 页面内截获
              （方案 B，尚未实现）。此处如实标注，避免误导用户。 */}
          {media.inline ? (
            <img src={media.thumb} alt="预览" className="ivimg" />
          ) : (
            <div className="ivhint">
              该图为抖音私有加密格式，无法在内嵌预览中显示。
              <br />
              请在浏览器中打开查看。
            </div>
          )}
        </div>
        <div className="ivfoot">
          {media.inline && (
            <span className="ivnote">
              当前为抖音下发的缩略图（消息体内无全尺寸原图）
            </span>
          )}
          {media.origin && (
            <a href={media.origin} target="_blank" rel="noreferrer">
              在抖音网页打开原图 ↗
            </a>
          )}
        </div>
      </div>
    </div>
  );
}

function MsgBubble({
  m,
  onOpenImage,
}: {
  m: Msg;
  onOpenImage?: (media: MediaInfo) => void;
}) {
  const t = (m.text || "").trim();

  // 2026-08-31：系统提示。抖音的打招呼卡片/推荐表情包（hint_text / hello_text）
  // 此前被兜底成 "[未知媒体] {...JSON...}"，前端显示成一长串乱码。
  // 后端已改为输出 "[系统提示] 文案"，这里居中弱化展示。
  const sysM = t.match(/^\[系统提示\]\s*(.*)$/);
  if (sysM) {
    return <div className="bubble recalled">{sysM[1] || "系统提示"}</div>;
  }

  // 2026-08-31：撤回消息。抖音把已撤回的消息体替换成占位串
  // 「Recall Content Hided」（英文原文，实测 5 条），直接展示会像乱码。
  if (t === "Recall Content Hided" || t === "Recall Content Hidden") {
    return <div className="bubble recalled">消息已撤回</div>;
  }

  // 图片 / 表情包：文本形如「[图片] <url>」「[表情包] <url>」或裸 URL。
  // 只要识别出媒体标记 + URL 就渲染缩略图；历史数据只有「图片」二字
  // （URL 在旧版本解析时丢失）则给占位提示，需重新捕获才有图链。
  if (m.type === "image" || m.type === "sticker" || /^\[(图片|表情包)\]/.test(t) || /^\[?图片\]?$/.test(t)) {
    const label = /表情包/.test(t) ? "表情包" : "图片";
    const media = parseMedia(m.text);

    // 情况 1：有内联缩略图（inline_pic，标准 WebP base64）
    //   -> 直接 <img> 渲染缩略图，点击弹出查看原图
    if (media.inline) {
      return (
        <div className="bubble mediathumb">
          <img
            src={media.thumb}
            alt={label}
            className="thumbimg"
            onClick={() => onOpenImage && onOpenImage(media)}
          />
          {media.origin && (
            <div className="thumbbar">
              <a href={media.origin} target="_blank" rel="noreferrer">
                查看原图
              </a>
            </div>
          )}
        </div>
      );
    }

    // 情况 2：只有远程 URL（无 inline_pic 的大图，或历史数据）
    //   实测远程 URL 全部为抖音私有加密格式，浏览器不可解码，
    //   降级为可点击链接，不做 <img> 内嵌（会显示破损图标）。
    //   2026-08-31：原文案「[图片] 点击查看（抖音加密格式，不支持内嵌预览）」
    //   暴露内部实现细节且观感差，用户反馈为「污染数据」。改为简洁人话。
    if (media.thumb) {
      return (
        <div className="bubble medialink">
          <a href={media.thumb} target="_blank" rel="noreferrer" title={media.thumb}>
            [{label}] 在抖音查看
          </a>
        </div>
      );
    }

    return <div className="bubble">[{label}]（无图链，需重新捕获）</div>;
  }
  if (m.type === "text")
    return <div className="bubble">{renderTextWithEmoji(m.text || "")}</div>;
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
  // 当前账号提升到 App 级（由 App 常驻 conversations 轮询驱动，本页只读缓存，避免切页冷拉/双拉）
  const activeAcct = props.msgAcct || "";
  const setActiveAcct = props.setMsgAcct || (() => {});
  const [active, setActive] = useState("");
  const [draft, setDraft] = useState("");
  const [showNew, setShowNew] = useState(false);
  const [newName, setNewName] = useState("");
  const [refreshing, setRefreshing] = useState(false);
  // 图片预览弹层（点击缩略图后展示）
  const [viewer, setViewer] = useState<MediaInfo | null>(null);
  // React Query 客户端：更新会话后用于失效所有缓存（避免显示已删除的旧数据）
  const qc = useQueryClient();
  // 2026-08-31：更新会话耗时 3~6 分钟，按钮显示实时耗时让用户知道还在跑
  const [refreshElapsed, setRefreshElapsed] = useState(0);

  // 2026-08-31：更新会话期间每秒累加耗时，让用户看到任务仍在推进
  useEffect(() => {
    if (!refreshing) {
      setRefreshElapsed(0);
      return;
    }
    const t = setInterval(
      () => setRefreshElapsed((s) => s + 1),
      1000,
    );
    return () => clearInterval(t);
  }, [refreshing]);

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
  const shownConvs: Conv[] = convsQ.data || [];

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
        .filter((m) => !/^\[未知媒体\]/.test((m.text || "").trim()))
        .map((m, j) => ({
        id: "dm" + conv.conv_id + "_" + j,
        dir: (m.dir || (m.role === "me" ? "out" : "in")) as "in" | "out",
        type: m.type || "text",
        text: m.text || "",
        mt: m.time || nowHM(),
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
              {refreshing ? `更新中… ${refreshElapsed}s` : "⟳ 更新会话"}
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
              {convMsgs.map((m) => {
                const sys = isSystemTip(m.text);
                return (
                  <div className={"msg " + (sys ? "sys" : m.dir)} key={m.id}>
                    <MsgBubble m={m} onOpenImage={setViewer} />
                    <span className="mtm">{m.mt}</span>
                  </div>
                );
              })}
              {convMsgs.length === 0 && (
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
      <ImageViewer media={viewer} onClose={() => setViewer(null)} />
    </div>
  );
}
