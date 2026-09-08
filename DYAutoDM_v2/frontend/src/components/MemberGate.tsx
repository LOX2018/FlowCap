/**
 * 会员门禁（v0.37.0）
 *
 * 全应用入口守卫：启动时查询 /api/member/state，
 * 未登录显示登录/注册卡片，登录成功后渲染子应用。
 * 会话过期（401 清 token）后由 App 的 3s 轮询自动回到此门禁。
 */
import { useState, useEffect, useCallback } from "react";
import { memberApi, getMemberToken } from "../api/client";

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
      <div className="member-gate">
        <div className="member-card">
          <div className="member-title">正在检查登录态…</div>
        </div>
      </div>
    );
  }

  return (
    <div className="member-gate">
      <div className="member-card">
        <div className="member-brand">
          <span className="mark" aria-hidden="true" />
          <span className="member-title">抖音数据控制台</span>
        </div>
        <div className="member-sub">会员登录后使用 · 数据本地加密隔离存储</div>

        <div className="member-tabs">
          <button
            className={"tab" + (mode === "login" ? " active" : "")}
            onClick={() => { setMode("login"); setMsg(""); }}
          >
            登录
          </button>
          <button
            className={"tab" + (mode === "register" ? " active" : "")}
            onClick={() => { setMode("register"); setMsg(""); }}
          >
            注册
          </button>
        </div>

        <input
          className="member-input"
          placeholder="用户名"
          value={username}
          maxLength={32}
          onChange={(e) => setUsername(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
        />
        <input
          className="member-input"
          type="password"
          placeholder="口令（至少 6 位）"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit()}
        />
        {mode === "register" && (
          <input
            className="member-input"
            type="password"
            placeholder="确认口令"
            value={password2}
            onChange={(e) => setPassword2(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submit()}
          />
        )}

        {msg && <div className="member-msg">{msg}</div>}

        <button className="btn primary member-btn" disabled={busy} onClick={submit}>
          {busy ? "请稍候…" : mode === "login" ? "登录" : "注册并登录"}
        </button>

        <div className="member-foot">
          每个会员独立数据空间：账号、会话、配置互相隔离；凭证 AES 加密存储。
        </div>
      </div>
    </div>
  );
}
