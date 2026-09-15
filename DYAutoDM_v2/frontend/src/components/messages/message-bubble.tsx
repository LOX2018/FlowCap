import { Play } from "lucide-react";
import { openExternal } from "../../utils/openExternal";
import { cn } from "@/lib/utils";
import { Msg, MediaInfo, renderTextWithEmoji, parseMedia } from "./message-shared";

export function MsgBubble({
  m,
  sys,
  onOpenImage,
}: {
  m: Msg;
  /** 是否为系统提示消息（判定在父层用 isSystemTip，语义不变；仅用于样式） */
  sys?: boolean;
  onOpenImage?: (media: MediaInfo) => void;
}) {
  const t = (m.text || "").trim();

  // 2026-08-31：系统提示。抖音的打招呼卡片/推荐表情包（hint_text / hello_text）
  // 此前被兜底成 "[未知媒体] {...JSON...}"，前端显示成一长串乱码。
  // 后端已改为输出 "[系统提示] 文案"，这里居中弱化展示。
  const sysM = t.match(/^\[系统提示\]\s*(.*)$/);
  if (sysM) {
    return (
      <div
        className="rounded-[var(--radius-md)] border border-dashed
                   border-[var(--color-border)] bg-[var(--color-surface-raised)]
                   px-2.5 py-1 text-center text-[0.72rem] italic
                   text-[var(--color-text-muted)] opacity-85"
      >
        {sysM[1] || "系统提示"}
      </div>
    );
  }

  // 2026-08-31：撤回消息。抖音把已撤回的消息体替换成占位串
  // 「Recall Content Hided」（英文原文，实测 5 条），直接展示会像乱码。
  if (t === "Recall Content Hided" || t === "Recall Content Hidden") {
    return (
      <div
        className="rounded-[var(--radius-md)] border border-dashed
                   border-[var(--color-border)] bg-[var(--color-surface-raised)]
                   px-2.5 py-1 text-center text-[0.72rem] italic
                   text-[var(--color-text-muted)] opacity-85"
      >
        消息已撤回
      </div>
    );
  }

  // 气泡样式：系统提示走弱化样式（旧 `.msg.sys .bubble`），
  // 否则按方向取色（旧 `.bubble` / `.msg.out .bubble`）
  const out = m.dir === "out";
  const bubble = cn(
    "rounded-[var(--radius-md)] border px-3 py-2 text-[0.8125rem] leading-[1.55]",
    sys
      ? "border-dashed border-[var(--color-border)] bg-[var(--color-surface-raised)] text-center text-[0.72rem] text-[var(--color-text-muted)]"
      : out
        ? "border-transparent bg-[var(--color-accent)] text-[#08130a]"
        : "border-[var(--color-border)] bg-[var(--color-surface-raised)] text-[var(--color-text)]",
  );
  // 缩略图气泡：无论方向都用弱底色（旧 `.msg.out .bubble.mediathumb` 去掉了主色底与描边）
  const mediaBubble = cn(
    "rounded-[var(--radius-md)] p-1 bg-[var(--color-surface-raised)]",
    out ? "border border-transparent" : "border border-[var(--color-border)]",
    sys && "border-dashed",
  );

  // 图片 / 表情包：文本形如「[图片] <url>」「[表情包] <url>」或裸 URL。
  //
  // 2026-08-31 修正：原来这里有 `/^\[?图片\]?$/` —— 它会把用户正常发的
  // 「图片」两个字也当成图片消息，解析不出 URL 就渲染成
  // 「[图片]（无图链，需重新捕获）」的脏数据（实测库里有 1 条 text='图片'
  // 触发，用户反馈"脏数据依旧在前端显示"）。
  // 去掉该宽松正则；真正无 URL 的图片消息改为显示成普通文本而非脏提示。
  if (
    m.type === "image" ||
    m.type === "sticker" ||
    /^\[(图片|表情包)\]/.test(t)
  ) {
    const label = /表情包/.test(t) ? "表情包" : "图片";
    const media = parseMedia(m.text);
    const isSticker = media.isSticker;

    // 2026-09-02：情况 0 —— 后端已经把 (skey, origin_url) 解密出真原图,
    // 通过 image_url 字段直接给到前端(<img src> 可用)。
    // 这是用户最关心的「能看到原图」路径,优先级最高:
    //   有 image_url → 直接渲染真原图（替换原来「跳抖音」的链接）
    //   无 image_url → 走情况 1/2 降级到缩略图 / 旧路径
    if (m.image_url) {
      // 用 image_url 替代 media.thumb,这样弹层也是高清图
      const fullMedia: MediaInfo = {
        thumb: m.image_url,
        origin: m.image_url,
        inline: true,
      };
      return (
        <div className={mediaBubble}>
          <img
            src={m.image_url}
            alt={label}
            className={cn(
              "block h-auto w-auto cursor-zoom-in rounded-[var(--radius-sm)]",
              "bg-[var(--color-border)] transition-opacity hover:opacity-90",
              isSticker ? "max-h-[130px] max-w-[100px]" : "max-h-[260px] max-w-[200px]",
            )}
            onClick={() => onOpenImage && onOpenImage(fullMedia)}
            onError={(e) => {
              // 解密文件缺失或图床 404:降级显示缩略图
              const img = e.currentTarget;
              if (media.thumb && img.src !== media.thumb) {
                img.src = media.thumb;
              }
            }}
          />
        </div>
      );
    }

    // 2026-08-31：解析不出任何图链时，不要渲染「无图链，需重新捕获」——
    // 那是给开发者看的调试话术，出现在聊天界面里就是脏数据。
    // 这种情况（历史数据 URL 丢失，或误判的文本）按普通文本展示更合理。
    if (!media.thumb) {
      return <div className={bubble}>{t}</div>;
    }

    // 情况 1：有内联缩略图（inline_pic，标准 WebP base64）
    //   -> 直接 <img> 渲染缩略图，点击弹出查看原图
    if (media.inline) {
      return (
        <div className={mediaBubble}>
          <img
            src={media.thumb}
            alt={label}
            className={cn(
              "block h-auto w-auto cursor-zoom-in rounded-[var(--radius-sm)]",
              "bg-[var(--color-border)] transition-opacity hover:opacity-90",
              isSticker ? "max-h-[130px] max-w-[100px]" : "max-h-[260px] max-w-[200px]",
            )}
            onClick={() => onOpenImage && onOpenImage(media)}
          />
          {/* 2026-09-02 修正：原来跳「douyin.com/chat」是因为原图拿不到。
              现在后端解密后有 image_url 就直接用真原图;只有没 image_url
              的历史消息（库内无 skey）才退到「去抖音看」。
              2026-09-03:表情包不显示「去抖音看原图」链接(贴纸走公开CDN,无需跳转) */}
          {!isSticker && (
            <div className="mt-1.5 text-[0.72rem]">
              <a
                href={media.origin || "https://www.douyin.com/chat"}
                className="text-[var(--color-accent)] underline"
                onClick={(e) => {
                  e.preventDefault();
                  void openExternal(media.origin || "https://www.douyin.com/chat");
                }}
              >
                {media.origin ? "查看原图 ↗" : "去抖音看原图 ↗"}
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
    // 2026-08-31 修正：media.thumb 在这里是抖音私有加密远程链，
    //   真机实测加载失败（3/3 error，0×0），点了打不开。
    //   改为跳转抖音私信页 —— 在抖音里点开图片才能看到真原图
    //   2026-09-03:表情包不显示「去抖音查看」链接(贴纸走公开CDN,无需跳转)
    if (media.thumb) {
      if (isSticker) {
        return <div className={bubble}>[{label}]</div>;
      }
      return (
        <div className={cn(bubble, "flex flex-wrap items-center gap-2 text-[0.78rem]")}>
          <a
            href="https://www.douyin.com/chat"
            className="text-[var(--color-accent)] underline"
            onClick={(e) => {
              e.preventDefault();
              void openExternal("https://www.douyin.com/chat");
            }}
          >
            [{label}] 去抖音查看 ↗
          </a>
        </div>
      );
    }

    return <div className={bubble}>[{label}]（无图链，需重新捕获）</div>;
  }
  if (m.type === "text")
    return <div className={bubble}>{renderTextWithEmoji(m.text || "")}</div>;
  if (m.type === "voice")
    return (
      <div className={bubble}>
        <div className="flex items-center gap-2">
          <span className="flex h-[18px] items-center gap-0.5">
            {[10, 18, 14, 22, 12, 20, 8, 16].map((hgt, i) => (
              <i
                key={i}
                style={{ height: hgt }}
                className="w-[3px] rounded-[2px] bg-current opacity-85"
              />
            ))}
          </span>
          <span className="font-mono text-[0.75rem]">{m.dur}</span>
        </div>
      </div>
    );
  if (m.type === "sticker")
    return (
      <div
        className="grid h-[76px] w-[76px] place-items-center rounded-[var(--radius-md)]
                   bg-[var(--color-accent-soft)] text-[1.15rem]"
      >
        ✨
      </div>
    );
  if (m.type === "image")
    return (
      <div
        className="grid h-[110px] w-[150px] place-items-center rounded-[var(--radius-md)]
                   bg-[linear-gradient(135deg,var(--color-accent-soft),var(--color-info-soft))]
                   text-[0.75rem] text-[var(--color-text-secondary)]"
      >
        图片消息
      </div>
    );
  // 2026-09-16 实机修复：未知类型的兜底分支。
  // 原实现：任何 type 非 text/voice/sticker/image 的消息都落进这里，渲染成
  // 「分享的视频」卡片 —— 但 WS 实时路径的数字 msg_type（如 "7"=文本）曾
  // 经流到这里，title 空白 → 界面显示"分享视频（该信息非真实存在）"。
  // 后端已把数字类型归一化（api/messages.py _front_type），前端再兜底一层：
  // 有 title 才当视频卡片，否则按普通文本渲染，绝不把未知类型变成"分享的视频"。
  if (m.title && !/^\[(未知媒体|分享视频|系统提示)\]/.test(t)) {
    return (
      <div className={bubble}>
        <div className="flex min-w-[220px] items-center gap-2.5">
          <div
            className="relative grid aspect-video w-[84px] shrink-0 place-items-center
                      rounded-[var(--radius-sm)]
                      bg-[linear-gradient(135deg,var(--color-accent-soft),var(--color-info-soft))]"
          >
            <span
              className="grid h-[34px] w-[34px] place-items-center rounded-full
                        border border-[color-mix(in_srgb,white_25%,transparent)] bg-black/55"
            >
              <Play className="ml-0.5 h-3 w-3 text-white" aria-hidden="true" />
            </span>
          </div>
          <div>
            <div className="text-[0.78rem] font-medium leading-relaxed">{m.title}</div>
            <div className="font-mono text-[0.68rem] text-[var(--color-text-muted)]">
              分享的视频
            </div>
          </div>
        </div>
      </div>
    );
  }
  // 未知类型且无 title：按普通文本渲染（不冒充视频卡片）
  return <div className={bubble}>{renderTextWithEmoji(m.text || "")}</div>;
}

