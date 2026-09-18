/**
 * `AuthedImg` —— 受保护图片的统一渲染组件（2026-09-17，B 方案）
 *
 * 为什么需要它：`<img src>` 无法携带 `X-Member-Token`，而后端
 * `/api/messages/origin_image/...` 受会员门禁保护（实测无令牌 → 401）。
 * 直接 `<img src={image_url}>` 会**静默破图**（旧代码靠 onError 换缩略图掩盖了它）。
 *
 * 本组件把「带令牌取 Blob → objectURL」封装成与 `<img>` 同形的用法：
 * 调用点只需把 `<img src=... onError=...>` 换成 `<AuthedImg src=... fallbackSrc=...>`。
 *
 * 行为契约：
 * · 外部地址（图床 `https://...`、`data:`）→ **原样直用**，零额外请求；
 * · 受保护本地地址 → 加载中渲染占位，成功后渲染 blob，失败则降级 `fallbackSrc`；
 * · `fallbackSrc` 也失败 / 未给 → 渲染一个低调的「图片不可用」占位（不吐调试话术）。
 */
import { useEffect, useState } from "react";
import { ImageOff } from "lucide-react";
import { cn } from "@/lib/utils";
import { useAuthedMediaUrl } from "@/lib/authed-media";
import { isLocalApiUrl } from "@/api/client";

export interface AuthedImgProps
  extends Omit<React.ImgHTMLAttributes<HTMLImageElement>, "src"> {
  /** 目标地址：本地受保护地址或外部直链 */
  src?: string;
  /** 主地址不可用时的降级地址（通常是消息体内联缩略图） */
  fallbackSrc?: string;
  /** 自定义占位（默认渲染极简骨架块） */
  placeholder?: React.ReactNode;
}

export function AuthedImg({
  src,
  fallbackSrc,
  placeholder,
  className,
  alt = "",
  ...rest
}: AuthedImgProps) {
  const resolved = useAuthedMediaUrl(src);
  const [failed, setFailed] = useState(false);
  /** 主地址失败后，是否允许换 fallback 再试一次（仅一次，避免死循环） */
  const [useFallback, setUseFallback] = useState(false);

  // 主地址变化时重置降级状态（切换会话/消息更新）
  useEffect(() => {
    setFailed(false);
    setUseFallback(false);
  }, [src, fallbackSrc]);

  // 主地址解析失败（401/取不到）→ 自动切降级地址
  useEffect(() => {
    if (resolved === null && fallbackSrc) setUseFallback(true);
  }, [resolved, fallbackSrc]);

  const finalSrc = useFallback ? fallbackSrc : (resolved ?? undefined);
  /** 降级地址若仍是受保护地址，同样要走适配（缩略图一般是 data: / 外部，直通） */
  const fallbackResolved = useAuthedMediaUrl(useFallback ? fallbackSrc : undefined);
  const shown = useFallback ? fallbackResolved : resolved;

  // 2026-09-18 审查修复（#61）：占位/失败分支同样要**透传交互属性**。
  // 调用方（message-bubble 的 onClick 打开原图、message-viewer 的 style/draggable）
  // 依赖这些属性；此前占位分支丢 `...rest` → 加载中的多帧窗口内点击无响应。
  const divRest = (() => {
    const { onLoad: _ol, onError: _oe, ...r } = rest;
    return r;
  })();

  if (shown === null || failed) {
    return (
      <div
        {...divRest}
        className={cn(
          "flex items-center justify-center rounded-[var(--radius-sm)]",
          "bg-[var(--color-border)] text-[var(--color-text-muted)]",
          "min-h-[72px] min-w-[72px]",
          className,
        )}
        title="图片不可用"
      >
        <ImageOff className="h-4 w-4 opacity-60" />
      </div>
    );
  }

  if (shown === undefined) {
    return (
      <div
        {...divRest}
        className={cn("animate-pulse rounded-[var(--radius-sm)] bg-[var(--color-border)]",
                      "min-h-[72px] min-w-[72px]", className)}
        aria-busy="true"
      >
        {placeholder}
      </div>
    );
  }

  // 外部直链用的是原地址；本地受保护地址用的是 blob:
  const realSrc = isLocalApiUrl(finalSrc) ? shown : (finalSrc || shown);
  return (
    <img
      {...rest}
      src={realSrc}
      alt={alt}
      className={className}
      onError={(e) => {
        // 2026-09-18 审查修复（#57）：先做**本组件的降级**，再回调调用方。
        // 旧顺序（先 rest.onError）下，调用方的 handler 一旦抛错，
        // setUseFallback/setFailed 永不执行 → 图片永久破图且无占位。
        if (!useFallback && fallbackSrc) {
          setUseFallback(true);      // 破图 → 换缩略图再试一次
        } else {
          setFailed(true);           // 彻底失败 → 占位
        }
        try {
          rest.onError?.(e);
        } catch {
          /* 调用方回调异常不影响本组件的降级状态机 */
        }
      }}
    />
  );
}

export default AuthedImg;
