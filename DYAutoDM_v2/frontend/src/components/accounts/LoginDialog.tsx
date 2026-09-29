/**
 * 登录弹层 —— 扫码二维码 / 短信验证码（ADR-017 / H-30 · F4）
 *
 * ## 为什么独立成文件
 * `accounts-page.tsx` 已 >1000 行；本组件只依赖「状态 + 两个提交回调」，
 * 独立后主页面仅做装配（与 AccountDrawer / ProxyDrawer 一致的分层）。
 *
 * ## 组件来源（**复用现有组件库，不自研**）
 * `@/components/ui/dialog`（Radix 封装）、`input`、`button` —— 全部沿用项目既有件。
 *
 * ## 状态机（来自后端 /scan-status）
 *   stage: starting → sending_code → waiting_code(needCode=true) → code_received → …
 *   needCode=true ⇒ 展示验证码输入框；用户提交 ⇒ POST /sms-code
 *
 * ## 诚实呈现原则（用户铁律）
 * 不得凭 `ok` 宣称成功 —— 一切以后端 `status` 的结构化字段为准：
 *   · `qrPng` 非空才渲染二维码图（老路径无图 ⇒ 不渲染，不给用户假图）
 *   · `rejected === "risk_control"` ⇒ 明示「已拒绝写入（防污染）」
 *   · 失败时展示后端给的 `error` / `stage`，不编造原因
 */
import { useEffect, useState } from "react";
import { Loader2 } from "lucide-react";
import { AuthedImg } from "@/components/ui/authed-img";
import {
  Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";

export interface ScanStatus {
  name?: string;
  running?: boolean;
  done?: boolean;
  loggedIn?: boolean;
  qrPng?: string;
  decoded?: boolean;
  path?: string;
  stage?: string;
  needCode?: boolean;
  rejected?: string;
  captureReport?: string;
  error?: string;
}

/**
 * @param onDone 流程结束（成功或失败）后由父组件关弹层并 refetch
 */
export function LoginDialog({
  name,
  status,
  onClose,
  onToast,
  onDone,
  onSwitchMode,
}: {
  name: string;
  status?: ScanStatus | null;
  onClose: () => void;
  onToast: (msg: string) => void;
  onDone: () => void;
  /** 2026-09-28：显式换路（后端按状态自动分流，此处只处理「我要另一条路」）。 */
  onSwitchMode?: (mode: "qr" | "sms") => void;
}) {
  const [code, setCode] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const st = status ?? {};

  // 流程结束 ⇒ 通知父组件（父组件负责关弹层 + 刷新列表）
  useEffect(() => {
    if (st.done) {
      onDone();
      onToast(
        st.loggedIn
          ? `登录成功 · 凭证已写回 · ${name}`
          : `登录未完成或失败 · ${name}${st.error ? "：" + st.error : ""}`,
      );
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [st.done]);

  const submitCode = async () => {
    const v = code.trim();
    if (!v) {
      onToast("请输入验证码");
      return;
    }
    setSubmitting(true);
    try {
      const { api } = await import("@/api/client");
      const r = await api.submitSmsCode(name, v);
      onToast(r?.ok ? "验证码已提交，等待登录结果…" : `提交失败：${r?.msg || "未知原因"}`);
      if (r?.ok) setCode("");
    } catch (e) {
      onToast(`提交异常：${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setSubmitting(false);
    }
  };

  const stageText: Record<string, string> = {
    starting: "正在启动浏览器…",
    sending_code: "正在发送短信验证码…",
    waiting_code: "验证码已发送，请在下方输入",
    code_received: "验证码已收到，正在登录…",
  };

  return (
    <Dialog open onOpenChange={(o) => !o && onClose()}>
      <DialogContent className="max-w-[420px]">
        <DialogHeader>
          <DialogTitle>{name} · 登录</DialogTitle>
          <DialogDescription>
            {st.path === "manual"
              ? "已打开有头指纹浏览器，请在窗口中手动完成登录（扫码/验证码/滑块）；完成后凭证将自动写回。"
              : st.path === "sms"
                ? (stageText[st.stage ?? ""] ?? "正在处理…")
                : "请用抖音 App 扫描下方二维码"}
          </DialogDescription>
        </DialogHeader>

        {/* 二维码：仅当后端真的产出了图才渲染（不给用户假图） */}
        {st.qrPng ? (
          <div className="flex flex-col items-center gap-2 py-2">
            {/* 本地绝对路径 ⇒ 必须经后端受保护端点取字节 */}
            {/* 2026-09-27 全库审计 FE-1：原为裸 <img src="/api/accounts/qr-image?..."> ——
                `<img src>` **无法携带 `X-Member-Token`**，而该端点不在 _MEMBER_EXEMPT
                内 ⇒ 实测必 401 ⇒ 二维码永久破图（登录流程卡死且无提示）。
                改用项目既有 `AuthedImg`（带令牌 fetch → Blob → objectURL），
                与 message-bubble / message-viewer 同一套受保护媒体方案。 */}
            <AuthedImg
              src={`/api/accounts/qr-image?path=${encodeURIComponent(st.qrPng)}`}
              alt="登录二维码"
              className="h-[220px] w-[220px] rounded-[10px] border border-[var(--color-border)] bg-white"
            />
            {!st.decoded && (
              <p className="text-[0.75rem] text-[var(--color-text-muted)]">
                二维码未能机械解码，请目视确认是否清晰
              </p>
            )}
          </div>
        ) : (
          !st.needCode && (
            <div className="flex items-center justify-center gap-2 py-6 text-[0.85rem] text-[var(--color-text-muted)]">
              <Loader2 className="h-4 w-4 animate-spin" />
              {st.path === "sms"
                ? "正在发送验证码…"
                : st.path === "manual"
                  ? "等待你在指纹浏览器中完成登录…"
                  : "正在生成二维码…"}
            </div>
          )
        )}

        {/* 验证码输入：仅在 needCode 时展示（状态机驱动，不是猜） */}
        {st.needCode && (
          <div className="flex flex-col gap-2 py-1">
            <Input
              value={code}
              onChange={(e) => setCode(e.target.value.replace(/\D/g, "").slice(0, 8))}
              onKeyDown={(e) => e.key === "Enter" && void submitCode()}
              placeholder="请输入短信验证码"
              inputMode="numeric"
              autoFocus
              maxLength={8}
            />
            <Button onClick={() => void submitCode()} disabled={submitting || !code.trim()}>
              {submitting ? "提交中…" : "提交验证码"}
            </Button>
          </div>
        )}

        {/* 路径如实标注 + 显式备用（2026-09-29 · 方案2）：
            默认是**手动**（有头浏览器用户自行登录）；扫码/短信为**显式备用**路径。
            这里把**实际走的路径**说出来（不猜、不假装成功）。 */}
        {onSwitchMode && (
          <div className="flex items-center justify-between gap-2 rounded-[8px]
                          bg-[var(--color-surface-raised)] px-3 py-2
                          text-[0.74rem] text-[var(--color-text-muted)]">
            <span>
              {st.path === "manual"
                ? "当前路径：手动（有头浏览器）"
                : st.path === "sms"
                  ? "当前路径：短信验证码（备用）"
                  : "当前路径：扫码（备用）"}
            </span>
            {st.path === "sms" ? (
              <button
                type="button"
                className="underline hover:text-[var(--color-text)]"
                onClick={() => onSwitchMode("qr")}
              >
                改用扫码备用
              </button>
            ) : (
              <button
                type="button"
                className="underline hover:text-[var(--color-text)]"
                onClick={() => onSwitchMode("sms")}
              >
                改用短信备用
              </button>
            )}
          </div>
        )}

        {/* 污染拒写：显式告知（不留沉默失败） */}
        {st.rejected === "risk_control" && (
          <p className="rounded-[8px] bg-[var(--color-danger-soft,#fee)] px-3 py-2 text-[0.8rem] text-[var(--color-danger,#c33)]">
            检测到风控验证页，已<b>拒绝写入</b>凭证（防污染态）。请在浏览器完成验证后重试。
          </p>
        )}

        {st.error && (
          <p className="text-[0.8rem] text-[var(--color-danger,#c33)]">{st.error}</p>
        )}

        {st.captureReport && (
          <p className="text-[0.72rem] text-[var(--color-text-muted)]">
            凭证分析报告：{st.captureReport}
          </p>
        )}
      </DialogContent>
    </Dialog>
  );
}
