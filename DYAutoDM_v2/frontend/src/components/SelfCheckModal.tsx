/**
 * 启动自检弹窗
 *
 * App 打开时调 api.selfCheck() 对所有账号跑双引擎校验（wp 凭证守护 + dm 私信列表拉取），
 * 若任一账号引擎不可用（fail/error/unknown），弹此说明弹窗列出不可用账号 + 原因 +
 * 一键「立即处理验证」（调 api.autoRecapture 打开 chat?isPopup=1 重新授权）。
 *
 * 设计：遮罩 + 居中卡片（非 Tauri dialog），可关闭；关闭后用户仍能使用其他可用账号。
 */
import { api } from "../api/client";

export interface SelfCheckItem {
  name: string;
  ok: boolean;
  wp: { level: string; label: string; detail?: string } | null;
  dm: { level: string; label: string; detail?: string } | null;
}

function engBadge(s: { level: string; label: string } | null) {
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
    <div className="modal-mask" onClick={onClose}>
      <div
        className="modal-card self-check"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="modal-head">
          <h3>私信凭证启动自检</h3>
          <button className="x" onClick={onClose} aria-label="关闭">
            ×
          </button>
        </div>

        {loading ? (
          <div className="modal-body">
            <p className="muted">正在检测各账号的 wp 引擎与私信引擎…</p>
          </div>
        ) : items.length === 0 ? (
          <div className="modal-body">
            <p className="muted">未检测到任何账号。请先在「账号」页新增并扫码登录账号。</p>
            <div className="modal-foot">
              <button className="btn" onClick={onClose}>
                知道了
              </button>
            </div>
          </div>
        ) : bad.length === 0 ? (
          <div className="modal-body">
            <p className="ok-line">
              <span className="dot ok" /> 全部账号的 wp 引擎与私信引擎均可用，可正常使用私信功能。
            </p>
            <div className="modal-foot">
              <button className="btn primary" onClick={onClose}>
                开始使用
              </button>
            </div>
          </div>
        ) : (
          <div className="modal-body">
            <p className="warn-line">
              检测到 {bad.length} 个账号的私信凭证引擎不可用，私信发送/接收可能失败。
              请点击下方按钮在弹出的指纹浏览器私信页完成重新授权。
            </p>
            <ul className="self-check-list">
              {bad.map((it) => {
                const wp = engBadge(it.wp);
                const dm = engBadge(it.dm);
                return (
                  <li key={it.name} className="self-check-item">
                    <div className="sci-head">
                      <b>{it.name}</b>
                      <button
                        className="btn sm danger"
                        onClick={() => handleFix(it.name)}
                      >
                        立即处理验证
                      </button>
                    </div>
                    <div className="sci-rows">
                      <div className="sci-row">
                        <span className={"pill " + wp.cls}>wp 引擎 · {wp.text}</span>
                        <span className="sci-label">
                          {it.wp?.label || "—"}
                          {it.wp?.detail ? "：" + it.wp.detail : ""}
                        </span>
                      </div>
                      <div className="sci-row">
                        <span className={"pill " + dm.cls}>私信引擎 · {dm.text}</span>
                        <span className="sci-label">
                          {it.dm?.label || "—"}
                          {it.dm?.detail ? "：" + it.dm.detail : ""}
                        </span>
                      </div>
                    </div>
                  </li>
                );
              })}
            </ul>
            <div className="modal-foot">
              <button className="btn" onClick={onClose}>
                暂忽略，继续使用
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
