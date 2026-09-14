/**
 * 会员门禁（重设计版 · 对标 better-douyin 设计体系）
 *
 * 全应用入口守卫：启动时查询 /api/member/state，
 * 未登录显示登录/注册卡片，登录成功后渲染子应用。
 * 会话过期（401 清 token）后由 App 的 3s 轮询自动回到此门禁。
 *
 * ## 本次改动（重设计）
 * - 旧 `.member-gate` / `.member-card` / `.member-tabs` / `.tab` / `.member-input` /
 *   `.member-btn` 等类（依赖 global.css）→ 设计令牌 + 玻璃拟态
 * - 旧的 `input` → `<Input>`，`button` → `<Button>`
 * - **业务逻辑零改动**：登录态检查、submit 校验顺序、错误文案提取正则
 *   （`\{"detail":"([^"]+)"\}`）全部原样保留
 */
import { useState, useEffect, useCallback } from "react";
import { memberApi, getMemberToken } from "../api/client";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export default function MemberGate({ onLogin }: { onLogin: (username: string) => void }) {
  const [checking, setChecking] = useState(true);
  const [mode, setMode] = useState<"login" | "register">("login");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [password2, setPassword2] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState("");

  // 启动时：有 token 则校验有效性，无效自动落登录页
  useEffect(() => {
    (async () => {
      if (!getMemberToken()) {
        setChecking(false);
        return;
      }
      const s = await memberApi.state();
      if (s.loggedIn && s.username) {
        onLogin(s.username);
      } else {
        setChecking(false);
      }
    })();
  }, [onLogin]);

  const submit = useCallback(async () => {
    setMsg("");
    if (!username.trim() || !password) {
      setMsg("请输入用户名和口令");
      return;
    }
    if (mode === "register" && password !== password2) {
      setMsg("两次输入的口令不一致");
      return;
    }
    setBusy(true);
    try {
      if (mode === "login") {
        await memberApi.login(username.trim(), password);
        onLogin(username.trim());
      } else {
        await memberApi.register(username.trim(), password);
        // 注册成功直接登录
        await memberApi.login(username.trim(), password);
        onLogin(username.trim());
      }
    } catch (e) {
      const raw = (e as Error).message || "";
      // 后端 HTTPException 返回 {"detail":"..."}，提取可读文案
      const m = raw.match(/\{"detail":"([^"]+)"\}/);
      setMsg(m ? m[1] : raw.slice(0, 120) || "操作失败");
    } finally {
      setBusy(false);
    }
  }, [mode, username, password, password2, onLogin]);

  if (checking) {
    return (
      <div className="flex min-h-screen items-center justify-center bg-[var(--color-background)]">
        <div className="glass-premium rounded-[var(--radius-xl)] px-8 py-6
                        text-[0.86rem] text-[var(--color-text-secondary)]">
          正在检查登录态…
        </div>
      </div>
    );
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--color-background)] p-6">
      <div className="glass-premium w-full max-w-[400px] rounded-[var(--radius-xl)] p-7">
        {/* 品牌 */}
        <div className="mb-6 flex items-center justify-center gap-2.5">
          <span
            aria-hidden="true"
            className="h-7 w-7 rounded-[var(--radius-sm)] bg-[var(--color-accent)]
                       shadow-[var(--shadow-glow)]"
          />
          <span className="text-[1.05rem] font-semibold tracking-tight text-[var(--color-text)]">
            抖音数据控制台
          </span>
        </div>
        {/* 2026-09-10：功能说明文案移至开放说明文档（项目说明.md），前端不展示 */}

        {/* 登录/注册切换 */}
        <div className="mb-5 inline-flex w-full items-center gap-1 rounded-[var(--radius-md)]
                        border border-[var(--color-border)] bg-[var(--color-surface)] p-1">
          {(["login", "register"] as const).map((mk) => (
            <button
              key={mk}
              type="button"
              onClick={() => {
                setMode(mk);
                setMsg("");
              }}
              className={cn(
                "flex-1 cursor-pointer rounded-[var(--radius-sm)] py-1.5 text-[0.8rem] font-medium",
                "transition-colors duration-[var(--duration-fast)] ease-[var(--ease-spring)]",
                mode === mk
                  ? "bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
                  : "text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-raised)]"
              )}
            >
              {mk === "login" ? "登录" : "注册"}
            </button>
          ))}
        </div>

        <div className="space-y-3">
          <Input
            placeholder="用户名"
            value={username}
            maxLength={32}
            onChange={(e) => setUsername(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
          />
          <Input
            type="password"
            placeholder="口令（至少 6 位）"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
          />
          {mode === "register" && (
            <Input
              type="password"
              placeholder="确认口令"
              value={password2}
              onChange={(e) => setPassword2(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && submit()}
            />
          )}
        </div>

        {msg && (
          <div className="mt-3 rounded-[var(--radius-sm)] bg-[var(--color-danger-soft)] px-3 py-2
                          text-[0.76rem] text-[var(--color-danger)]">
            {msg}
          </div>
        )}

        <Button className="mt-5 w-full" disabled={busy} onClick={submit}>
          {busy ? "请稍候…" : mode === "login" ? "登录" : "注册并登录"}
        </Button>

        {/* 2026-09-10：底部功能说明文案移至开放说明文档（项目说明.md），前端不展示 */}
      </div>
    </div>
  );
}
