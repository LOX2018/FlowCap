/**
 * 启动自检弹窗（重设计版 · 对标 better-douyin 设计体系）
 *
 * App 打开时调 api.selfCheck() 对所有账号跑双引擎校验（wp 凭证守护 + dm 私信列表拉取），
 * 若任一账号引擎不可用（fail/error/unknown），弹此说明弹窗列出不可用账号 + 原因 +
 * 一键「立即处理验证」（调 api.autoRecapture 打开 chat?isPopup=1 重新授权）。
 *
 * ## 本次改动（重设计）
 * - 旧 `.modal-mask` / `.modal-card` / `.modal-head` / `.modal-body` / `.modal-foot` /
 *   `.self-check-list` / `.sci-*` / `.pill` 等类 → 设计令牌 + Badge/Tone + Button
 * - **业务逻辑零改动**：`engBadge` 等级映射（ok/warn/skip/其余）、`bad` 过滤条件
 *   （fail/error/unknown）、`handleFix` 的 autoRecapture 调用与文案全部原样保留
 */
import { X } from "lucide-react";
import { api } from "../../api/client";
import { Button } from "@/components/ui/button";
import { Tone } from "@/components/page/kit";

export interface SelfCheckItem {
  name: string;
  ok: boolean;
  wp: { level: string; label: string; detail?: string } | null;
  dm: { level: string; label: string; detail?: string } | null;
}

/** 引擎等级 → 展示（判据与文案一字未改）。 */
function engBadge(s: { level: string; label: string } | null): {
  cls: "ok" | "warn" | "danger" | "mute";
  text: string;
} {
  if (!s) return { cls: "mute", text: "未知" };
  const lvl = s.level || "unknown";
  if (lvl === "ok") return { cls: "ok", text: "可用" };
  if (lvl === "warn") return { cls: "warn", text: "可用（警告）" };
  if (lvl === "skip") return { cls: "mute", text: "跳过" };
  return { cls: "danger", text: "不可用" };
}

export default function SelfCheckModal({
  open,
  items,
  loading,
  onClose,
  push,
}: {
  open: boolean;
  items: SelfCheckItem[];
  loading: boolean;
  onClose: () => void;
  push: (m: string, holdMs?: number) => void;
}) {
  if (!open) return null;

  const bad = items.filter(
    (it) =>
      (it.wp && ["fail", "error", "unknown"].includes(it.wp.level)) ||
      (it.dm && ["fail", "error", "unknown"].includes(it.dm.level)),
  );

  const handleFix = (name: string) => {
    push("已发起重新捕获 · " + name + " · 请在弹出的指纹浏览器私信页完成验证");
    api.addLog("INFO", `启动自检·重新捕获 · ${name}`).catch(() => {});
    api
      .autoRecapture(name)
      .then((d) => {
        const r = d as { ok?: boolean; msg?: string; error?: string };
        if (r && r.ok) push(r.msg || "已弹出指纹浏览器（私信页）");
        else if (r && r.error) push("重新捕获失败: " + r.error);
      })
      .catch((e: unknown) => push("重新捕获异常: " + String(e)));
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/55 p-6 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        className="glass-premium flex max-h-[85vh] w-full max-w-[620px] flex-col
                   overflow-hidden rounded-[var(--radius-xl)]"
        onClick={(e) => e.stopPropagation()}
      >
        {/* 头部 */}
        <div className="flex shrink-0 items-center justify-between border-b
                        border-[var(--color-border)] px-4 py-3">
          <h3 className="text-[0.95rem] font-semibold text-[var(--color-text)]">
            私信凭证启动自检
          </h3>
          <Button variant="ghost" size="icon-sm" onClick={onClose} aria-label="关闭">
            <X className="h-4 w-4" />
          </Button>
        </div>

        {loading ? (
          <div className="min-h-0 flex-1 overflow-y-auto p-5">
            <p className="text-[0.8rem] text-[var(--color-text-muted)]">
              正在检测各账号的 wp 引擎与私信引擎…
            </p>
          </div>
        ) : items.length === 0 ? (
          <div className="min-h-0 flex-1 overflow-y-auto p-5">
            <p className="text-[0.8rem] text-[var(--color-text-muted)]">
              未检测到任何账号。请先在「账号」页新增并扫码登录账号。
            </p>
            <div className="mt-4 flex justify-end">
              <Button variant="secondary" onClick={onClose}>知道了</Button>
            </div>
          </div>
        ) : bad.length === 0 ? (
          <div className="min-h-0 flex-1 overflow-y-auto p-5">
            <p className="flex items-start gap-2 text-[0.8rem] text-[var(--color-success)]">
              <span className="mt-1.5 inline-block h-[7px] w-[7px] shrink-0 rounded-full
                               bg-[var(--color-success)]" />
              全部账号的 wp 引擎与私信引擎均可用，可正常使用私信功能。
            </p>
            <div className="mt-4 flex justify-end">
              <Button onClick={onClose}>开始使用</Button>
            </div>
          </div>
        ) : (
          <div className="min-h-0 flex-1 overflow-y-auto p-5">
            <p className="text-[0.8rem] leading-relaxed text-[var(--color-warning)]">
              检测到 {bad.length} 个账号的私信凭证引擎不可用，私信发送/接收可能失败。
              请点击下方按钮在弹出的指纹浏览器私信页完成重新授权。
            </p>

            <ul className="mt-3 space-y-2.5">
              {bad.map((it) => {
                const wp = engBadge(it.wp);
                const dm = engBadge(it.dm);
                return (
                  <li
                    key={it.name}
                    className="rounded-[var(--radius-md)] border border-[var(--color-border)]
                               bg-[var(--color-surface)] p-3"
                  >
                    <div className="flex items-center justify-between gap-3">
                      <b className="min-w-0 truncate text-[0.84rem] text-[var(--color-text)]">
                        {it.name}
                      </b>
                      <Button
                        variant="danger-outline"
                        size="sm"
                        onClick={() => handleFix(it.name)}
                      >
                        立即处理验证
                      </Button>
                    </div>
                    <div className="mt-2 space-y-1.5">
                      <div className="flex items-start gap-2">
                        <Tone tone={wp.cls}>wp 引擎 · {wp.text}</Tone>
                        <span className="min-w-0 flex-1 text-[0.74rem] leading-relaxed
                                         text-[var(--color-text-muted)]">
                          {it.wp?.label || "—"}
                          {it.wp?.detail ? "：" + it.wp.detail : ""}
                        </span>
                      </div>
                      <div className="flex items-start gap-2">
                        <Tone tone={dm.cls}>私信引擎 · {dm.text}</Tone>
                        <span className="min-w-0 flex-1 text-[0.74rem] leading-relaxed
                                         text-[var(--color-text-muted)]">
                          {it.dm?.label || "—"}
                          {it.dm?.detail ? "：" + it.dm.detail : ""}
                        </span>
                      </div>
                    </div>
                  </li>
                );
              })}
            </ul>

            <div className="mt-4 flex justify-end">
              <Button variant="secondary" onClick={onClose}>暂忽略，继续使用</Button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
