
export interface Msg {
  id: string;
  dir: "in" | "out";
  type: string;
  text: string;
  mt: string;
  dur?: string;
  title?: string;
  /** 2026-09-02：后端解密后的真原图 URL(本地 http://.../api/messages/origin_image/xxx 或图床 https://tucdn...)。
   *  与 media.thumb(消息体缩略图)不同 —— image_url 是原图。
   *  前端 <img src={image_url}> 可直接渲染,无需再去抖音网页。
   */
  image_url?: string;
  /** 2026-09-05：消息来源通道。ws=私信守护(默认) / wp=抖音网页版 chat 页 */
  source?: "ws" | "wp";
  /** 2026-09-17：语音转写文本（后端 extra.transcription 透传）。
   *  有则语音气泡下方展示；失败/未转写为 undefined（不显示占位）。 */
  voiceText?: string;
  /** 2026-09-17：引用回复（protobuf field 18 透传，仅消息自带，不做补查） */
  reply?: MsgReply | null;
}
/** 被引用的那条消息（f18 内嵌 JSON），用于在气泡上方渲染引用区块 */
export interface MsgReply {
  ref_msg_id?: string;
  text?: string;
  nickname?: string;
  sec_uid?: string;
}
export interface Conv {
  id: string;
  conv_id?: string;
  acct: string;
  hue: string;
  name: string;
  unread: number;
  avatar?: string;
  msgs: Msg[];
}
export interface Account {
  name: string;
  uid?: string;
  loggedIn?: boolean;
  level?: string;
  label?: string;
  recvDaemonPort?: number;
}
export interface RawMessage {
  dir?: string;
  /** 后端原生命名：me=我发 / them=对方发（dir 为空时的兜底判据） */
  role?: string;
  type?: string;
  text?: string;
  time?: string;
  /** 抖音消息唯一 ID（protobuf field 3），用于 React key 稳定 */
  msg_id?: string;
  /** 2026-09-02：后端解密后的真原图 URL（本地或图床），有则前端优先用它。 */
  image_url?: string;
  /** 2026-09-05：后端 /conversation 透传的来源通道 */
  source?: string;
  /** 2026-09-17：语音转写文本（后端 extra 透传） */
  transcription?: string;
  /** 2026-09-17：引用回复（protobuf field 18），后端原样透传 */
  reply?: MsgReply | null;
}
export interface RawConversation {
  conv_id?: string;
  name?: string;
  unread?: number;
  messages?: RawMessage[];
  avatar?: string;
}
export interface ConversationsResp {
  ok?: boolean;
  conversations?: RawConversation[];
}
export interface SendDmResp {
  ok: boolean;
  error?: string;
}
export interface RequestDmResp {
  ok: boolean;
  msg?: string;
  error?: string;
}

/** client.ts 未提供 requestDm，本地扩展 */
export interface RefreshConvsResp {
  ok: boolean;
  n_conv?: number;
  n_msg?: number;
  elapsed?: number;
  error?: string;
}
export interface MessagesApi {
  getConversations(account: string): Promise<unknown>;
  getConversation(account: string, convId: string): Promise<unknown>;
  // 2026-09-05：channel 指定发送通道 ws(默认) / wp
  sendDm(
    account: string,
    convId: string,
    text: string,
    channel?: "ws" | "wp",
  ): Promise<SendDmResp>;
  // 2026-09-06：图片发送（后端直发全链路，走 recv_daemon /send_image）
  sendImage(
    account: string,
    convId: string,
    imageB64: string,
    filename: string,
  ): Promise<{ ok: boolean; error?: string; info?: Record<string, unknown> }>;
  requestDm(name: string): Promise<RequestDmResp>;
  refreshConversations(account: string, withBrowser?: boolean): Promise<RefreshConvsResp>;
  /** 2026-09-17：语音转写（BCC 上下文内识别；只处理语音消息） */
  transcribeVoice(
    account: string,
    convId?: string,
    limit?: number,
  ): Promise<{
    ok: boolean;
    requested?: number;
    succeeded?: number;
    skipped?: number;
    reason?: string;
    error?: string;
  }>;
  getAccounts(): Promise<unknown>;
  addLog(level: string, text: string): Promise<unknown>;
}

export { errMsg } from "@/lib/utils";

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
export function isSystemTip(text: string): boolean {
  const t = (text || "").trim();
  if (!t) return false;
  if (SYS_TIPS.some((k) => t.includes(k))) return true;
  return /^(你|对方)(已|正在|已确认)/.test(t);
}

/** 媒体消息的双 URL 结构（2026-08-30 实测落地） */
export type MediaInfo = {
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
export function renderTextWithEmoji(s: string): React.ReactNode[] {
  const parts = String(s || "").split(/(\[[^\[\]]{1,10}\])/g);
  return parts.map((p, i) => {
    const m = p.match(/^\[([^\[\]]{1,10})\]$/);
    // 2026-09-17 修补（OCR 审查 HIGH —— 原型链 key 误命中）：
    // 原为 `EMOJI_MAP[m[1]]` —— EMOJI_MAP 是普通对象字面量，继承 Object.prototype。
    // 文本里出现 `[constructor]` / `[toString]` / `[hasOwnProperty]` 等方括号串时，
    // 取值会命中原型链上的函数（真值）→ 走进 emoji 分支渲染出一个**函数**，
    // React 渲染函数会抛错/告警。
    // 改用 hasOwnProperty 判定「自有键」再取值。
    if (m && Object.prototype.hasOwnProperty.call(EMOJI_MAP, m[1])) {
      return (
        <span
          key={i}
          title={m[1]}
          className="text-[1.25em] leading-none align-[-0.1em] not-italic"
        >
          {EMOJI_MAP[m[1]]}
        </span>
      );
    }
    return <span key={i}>{p}</span>;
  });
}

/**
 * 清洗 data URI，剔除 base64 段里的非法字符。
 *
 * 2026-08-31 实测：数据库里 43/43 条内联图片的 base64 后面**直接粘着**
 * `[原图] https://p26-sign.douyinpic.com/...`（中间没有换行），例如：
 *
 *   [图片] data:image/webp;base64,UklGRkgCAABXRUJ...gnature=JEgRJP%2F2%2B
 *          ↑ 本该有 \n，实际紧挨着
 *
 * 后果：base64 里混入 `\n`、`[`、`原` 等字符 → 浏览器判定 data URI 非法
 * → **图片渲染失败**（实测 27/43 条解码失败）。
 *
 * 后端写入时确实用了 `\n[原图]`，但库里存在无换行的历史数据，
 * 故前端必须容错：这里保留合法的 base64 字符，其余一律剔除。
 */
export function sanitizeDataUri(u: string): string {
  if (!u.startsWith("data:")) return u;
  const comma = u.indexOf(",");
  if (comma < 0) return u;
  const head = u.slice(0, comma + 1); // "data:image/webp;base64,"
  let rest = u.slice(comma + 1);
  // 2026-09-04 修复:base64 里可能混入 URL 编码(%2F→/, %2B→+ 等),
  // 必须先 decodeURIComponent 再剔除非法字符,否则 % 被直接删掉后
  // 剩下的 "2F" 不是合法的 base64,图片渲染失败显示灰圈。
  try {
    rest = decodeURIComponent(rest);
  } catch {
    // 解码失败则保持原样,后面正则兜底
  }
  // 标准 base64 字符集（URL 安全变体 + padding）
  rest = rest.replace(/[^A-Za-z0-9+/=_-]/g, "");
  return head + rest;
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
export function parseMedia(text: string): MediaInfo {
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
      // 2026-08-31：base64 尾部可能粘着 `[原图] <url>`（无换行的历史数据），
      // 需拆开 —— 否则整个 data URI 非法，图片渲染失败。
      const seg = m[2].trim();
      const glue = seg.indexOf("[原图]");
      if (glue > 0) {
        thumb = sanitizeDataUri(seg.slice(0, glue).trim());
        if (!origin) {
          const om = seg.slice(glue).match(/^\[原图\]\s*(https?:\/\/\S+)/);
          if (om) origin = om[1];
        }
      } else {
        thumb = sanitizeDataUri(seg);
      }
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
