/**
 * `McpSection` —— MCP 服务设置卡（2026-09-25）
 *
 * ## 为什么新增（根因，用户两次提出）
 *
 * 用户原话：「我在前端页面没有看到关于 MCP 的设置入口，导致我一直都不知道，
 * 而且他也没有真正的接入到 Hermes 中……我也不知道 MCP 密钥是多少，鉴权根本就无法使用」。
 *
 * 实测根因：后端 `backend/api/mcp.py` 的 **7 个端点本来就完整**（config / token/reveal
 * / token/rotate / restart / tools / audit），但前端 9 个 tab **零引用**（`grep -rn mcp`
 * 为空），`data/mcp_config.json` 从未生成 ⇒ `enabled=False` ⇒ 令牌从未被创建
 * ⇒ 「能力在位但不可得」。本组件补上呈现层。
 *
 * ## 设计契约（三条，必须由 UI 保证）
 *
 * 1. **讲清两种模式的心智模型**（用户痛点的真正解药）
 *    · **stdio**（Hermes / Codex / Claude Code）—— **不需要令牌**，进程边界即鉴权。
 *      用户此前误判「没令牌 ⇒ MCP 用不了」，实际 Hermes 接入走的就是这条，本就可用。
 *    · **本机 HTTP**（127.0.0.1:39144）—— **需要 Bearer 令牌**，由本卡「显示令牌」取得。
 *    两者必须并列陈列，否则用户仍会以为整件事被令牌卡住。
 *
 * 2. **默认关闭且关闭态一眼可见** —— 对齐 `NicknameFallbackSection` 的既有范式，
 *    也是后端 `DEFAULTS.enabled=False` 的语义（最安全的默认）。
 *
 * 3. **写操作按需开启，不因「已授权」就默认放开** —— `allow_write_actions` 默认 false，
 *    且开关旁必须写明后果（否则用户不知道打开意味着 AI 可对真实账号执行写操作）。
 *
 * ## 边界（不做的事）
 *   · 不显示全量令牌，除非用户点「显示令牌」（后端 `token/reveal` 是唯一明文出口，
 *     且属本机 UI 显式动作，不经任何 AI 客户端路径）。
 *   · 不在本组件里手写 fetch —— 一律走 `api.getMcpConfig()` 等，保证会员鉴权头在场。
 */
import { useCallback, useEffect, useState } from "react";
import {
  Plug, RefreshCw, Loader2, Eye, EyeOff, Copy, KeyRound, AlertTriangle,
  TerminalSquare, Globe, ShieldAlert, Trash2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { SetCard, SetCardHead, SetCardBody, SetField } from "@/components/page/set-card";
import { api, type McpStatus, type McpTool } from "@/api/client";

/** 统一的忙碌态标记，避免同一时刻多个按钮并发触发。 */
type Busy = "" | "load" | "save" | "token" | "rotate" | "restart" | "tools";

export default function McpSection(props: { push?: (msg: string, holdMs?: number) => void }) {
  const push = props.push ?? (() => {});

  const [status, setStatus] = useState<McpStatus | null>(null);
  const [tools, setTools] = useState<McpTool[] | null>(null);
  const [busy, setBusy] = useState<Busy>("");
  const [err, setErr] = useState("");
  /** 明文令牌只存在于内存；刷新即消失（降低驻留） */
  const [token, setToken] = useState("");
  /** 用户是否主动要求显示令牌 */
  const [showToken, setShowToken] = useState(false);

  // ---- 表单（本地编辑态，保存才提交；避免每敲一下就写盘）----
  const [enabled, setEnabled] = useState(false);
  const [port, setPort] = useState(39144);
  const [allowWrite, setAllowWrite] = useState(false);
  const [requireConfirm, setRequireConfirm] = useState(true);
  const [retention, setRetention] = useState(200);

  const applyStatus = useCallback((s: McpStatus) => {
    setStatus(s);
    setEnabled(Boolean(s.enabled));
    setPort(Number(s.preferred_port ?? 39144));
    setAllowWrite(Boolean(s.allow_write_actions));
    setRequireConfirm(Boolean(s.require_confirmation));
    setRetention(Number(s.log_retention ?? 200));
  }, []);

  const load = useCallback(async () => {
    setBusy("load");
    setErr("");
    try {
      const r = await api.getMcpConfig();
      if (r.ok && r.data) applyStatus(r.data);
      else setErr(r.message || "读取 MCP 配置失败");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "读取 MCP 配置失败");
    } finally {
      setBusy("");
    }
  }, [applyStatus]);

  useEffect(() => { void load(); }, [load]);

  const save = async () => {
    setBusy("save");
    setErr("");
    try {
      const r = await api.saveMcpConfig({
        enabled,
        preferred_port: port,
        allow_write_actions: allowWrite,
        require_confirmation: requireConfirm,
        log_retention: retention,
      });
      if (r.ok) {
        if (r.data) applyStatus(r.data);
        // 后端语义（api/mcp.py:67）：端口/开关变更需 /restart 才生效 —— 如实提示，不谎称已生效
        push("已保存。端口/开关变更需点「重启服务」才生效");
      } else {
        setErr(r.message || "保存失败");
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : "保存失败");
    } finally {
      setBusy("");
    }
  };

  const restart = async () => {
    setBusy("restart");
    setErr("");
    try {
      const r = await api.restartMcp();
      if (r.data) applyStatus(r.data);
      push(r.message || (r.ok ? "MCP 服务已重启" : "重启失败"));
      if (!r.ok && r.message) setErr(r.message);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "重启失败");
    } finally {
      setBusy("");
    }
  };

  const reveal = async () => {
    setBusy("token");
    setErr("");
    try {
      const r = await api.revealMcpToken();
      if (r.ok && r.token) {
        setToken(r.token);
        setShowToken(true);
        // 取回后刷新状态：reveal 会顺带创建令牌（世代号 +1），脱敏串需同步
        const s = await api.getMcpConfig();
        if (s.ok && s.data) applyStatus(s.data);
      } else {
        setErr(r.message || "取回令牌失败");
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : "取回令牌失败");
    } finally {
      setBusy("");
    }
  };

  const rotate = async () => {
    if (!window.confirm(
      "轮换令牌会让「所有已配置的 AI 客户端」立即失效（需各自更新为新令牌）。确定继续？",
    )) return;
    setBusy("rotate");
    setErr("");
    try {
      const r = await api.rotateMcpToken();
      if (r.data) applyStatus(r.data);
      setToken("");
      setShowToken(false);
      push(r.message || "令牌已轮换");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "轮换失败");
    } finally {
      setBusy("");
    }
  };

  const loadTools = async () => {
    setBusy("tools");
    setErr("");
    try {
      const r = await api.getMcpTools();
      if (r.ok) setTools(r.tools);
      else setErr(r.message || "读取工具清单失败");
    } catch (e) {
      setErr(e instanceof Error ? e.message : "读取工具清单失败");
    } finally {
      setBusy("");
    }
  };

  const copy = (text: string, label: string) => {
    navigator.clipboard
      ?.writeText(text)
      .then(() => push(`${label}已复制到剪贴板`))
      .catch(() => push("复制失败"));
  };

  const httpPort = status?.bound_port ?? port;

  return (
    <SetCard>
      <SetCardHead
        title="MCP 服务（本机工具接口）"
        description="把本项目的读取/操作能力暴露给 AI 客户端。stdio 模式无需令牌；本机 HTTP 模式需要令牌"
        right={
          <span
            data-od-id="mcp-state"
            className={
              "rounded-[var(--radius-sm)] border px-1.5 py-[1px] font-mono text-[0.7rem] " +
              (enabled
                ? "border-[var(--color-accent)] text-[var(--color-accent)]"
                : "border-[var(--color-border-strong)] text-[var(--color-text-secondary)]")
            }
          >
            {!status ? "未读取" : status.enabled ? "已启用" : "已关闭"}
          </span>
        }
      />
      <SetCardBody>
        {/* ---- 两种模式的心智模型（本卡最重要的一段）---- */}
        <div className="mb-3 grid gap-2 sm:grid-cols-2">
          <div className="rounded-[var(--radius-sm)] border border-[var(--color-border)]
                          bg-[var(--color-surface-raised)] px-2.5 py-2">
            <div className="flex items-center gap-1.5 text-[0.76rem] font-medium
                            text-[var(--color-text)]">
              <TerminalSquare className="h-3.5 w-3.5 text-[var(--color-accent)]" />
              stdio 模式 · 无需令牌
            </div>
            <div className="mt-1 text-[0.72rem] leading-relaxed text-[var(--color-text-muted)]">
              给 <strong className="text-[var(--color-text-secondary)]">Hermes / Codex /
              Claude Code</strong> 用。由客户端直接拉起本进程，进程边界即鉴权，
              与下面的令牌无关。Hermes 已接入（scope=debug，7 个调试工具）。
            </div>
          </div>
          <div className="rounded-[var(--radius-sm)] border border-[var(--color-border)]
                          bg-[var(--color-surface-raised)] px-2.5 py-2">
            <div className="flex items-center gap-1.5 text-[0.76rem] font-medium
                            text-[var(--color-text)]">
              <Globe className="h-3.5 w-3.5 text-[var(--color-text-secondary)]" />
              本机 HTTP 模式 · 需要令牌
            </div>
            <div className="mt-1 text-[0.72rem] leading-relaxed text-[var(--color-text-muted)]">
              只监听 <strong className="text-[var(--color-text-secondary)]">127.0.0.1</strong>
              （绝不对外）。请求须带 <code className="font-mono">Authorization: Bearer
              &lt;令牌&gt;</code>。令牌由下方「显示令牌」取得。当前端口：
              <span className="font-mono">{httpPort}</span>
              {status?.running ? "（服务中）" : "（未启动）"}
            </div>
          </div>
        </div>

        {/* ---- 开关与参数 ---- */}
        <div className="space-y-2">
          <SetField label={<span className="text-[0.72rem] text-[var(--color-text-muted)]">启用 MCP</span>}>
            <label className="flex cursor-pointer items-center gap-2 text-[0.78rem]
                              text-[var(--color-text)]">
              <input
                type="checkbox"
                data-od-id="mcp-enabled"
                checked={enabled}
                onChange={(e) => setEnabled(e.target.checked)}
              />
              启用后本机 HTTP 服务才可启动（保存后需点「重启服务」）
            </label>
          </SetField>

          <SetField label={<span className="text-[0.72rem] text-[var(--color-text-muted)]">首选端口</span>}>
            <input
              type="number"
              data-od-id="mcp-port"
              className="h-[30px] w-[120px] rounded-[var(--radius-sm)] border
                         border-[var(--color-border)] bg-[var(--color-surface)] px-2
                         font-mono text-[0.76rem] text-[var(--color-text)] outline-none
                         focus:border-[var(--color-accent)]"
              value={port}
              min={1024}
              max={65535}
              onChange={(e) => setPort(Number(e.target.value) || 0)}
            />
            <span className="ml-2 text-[0.72rem] text-[var(--color-text-muted)]">
              被占用时自动向后探测（+20 范围）
            </span>
          </SetField>

          <SetField label={<span className="text-[0.72rem] text-[var(--color-text-muted)]">写操作</span>}>
            <label className="flex cursor-pointer items-center gap-2 text-[0.78rem]
                              text-[var(--color-text)]">
              <input
                type="checkbox"
                data-od-id="mcp-allow-write"
                checked={allowWrite}
                onChange={(e) => setAllowWrite(e.target.checked)}
              />
              允许 AI 客户端执行写操作（默认关闭）
            </label>
            {allowWrite ? (
              <div className="mt-1 flex items-start gap-1.5 text-[0.72rem]
                              text-[var(--color-warning)]">
                <AlertTriangle className="mt-[2px] h-3.5 w-3.5 shrink-0" />
                <span>
                  打开后 AI 可对「真实账号」执行写操作（如发私信）。
                  建议同时保持下方「写操作需二次确认」开启。
                </span>
              </div>
            ) : null}
          </SetField>

          <SetField label={<span className="text-[0.72rem] text-[var(--color-text-muted)]">写操作二次确认</span>}>
            <label className="flex cursor-pointer items-center gap-2 text-[0.78rem]
                              text-[var(--color-text)]">
              <input
                type="checkbox"
                data-od-id="mcp-require-confirm"
                checked={requireConfirm}
                onChange={(e) => setRequireConfirm(e.target.checked)}
                disabled={!allowWrite}
              />
              写操作需一次性确认票据（120 秒内有效）
              {!allowWrite ? (
                <span className="text-[0.72rem] text-[var(--color-text-muted)]">
                  —— 写操作未开启时此保护无对象
                </span>
              ) : null}
            </label>
          </SetField>

          <SetField label={<span className="text-[0.72rem] text-[var(--color-text-muted)]">审计保留条数</span>}>
            <input
              type="number"
              data-od-id="mcp-retention"
              className="h-[30px] w-[100px] rounded-[var(--radius-sm)] border
                         border-[var(--color-border)] bg-[var(--color-surface)] px-2
                         font-mono text-[0.76rem] text-[var(--color-text)] outline-none
                         focus:border-[var(--color-accent)]"
              value={retention}
              min={1}
              max={100000}
              onChange={(e) => setRetention(Number(e.target.value) || 0)}
            />
          </SetField>
        </div>

        {/* ---- 未保存差异提示（徽章显示已保存态，此处点明差异）---- */}
        {status && enabled !== status.enabled ? (
          <div className="mb-2 text-[0.72rem] text-[var(--color-warning)]">
            当前选择（{enabled ? "启用" : "关闭"}）尚未保存 —— 上方状态徽章显示的是「已保存」的值。
          </div>
        ) : null}

        {/* ---- 操作区 ---- */}
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button
            variant="default"
            size="sm"
            data-od-id="mcp-save"
            disabled={busy !== ""}
            onClick={save}
          >
            {busy === "save" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Plug className="h-3.5 w-3.5" />}
            保存配置
          </Button>
          <Button
            variant="secondary"
            size="sm"
            data-od-id="mcp-restart"
            disabled={busy !== ""}
            title="按当前配置启动/重启本机 HTTP 服务"
            onClick={restart}
          >
            {busy === "restart" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
            重启服务
          </Button>
          <Button
            variant="ghost"
            size="sm"
            data-od-id="mcp-refresh"
            disabled={busy !== ""}
            onClick={load}
          >
            {busy === "load" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
            刷新状态
          </Button>
          <Button
            variant="ghost"
            size="sm"
            data-od-id="mcp-tools"
            disabled={busy !== ""}
            onClick={loadTools}
          >
            {busy === "tools" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Eye className="h-3.5 w-3.5" />}
            查看工具清单
          </Button>
        </div>

        {/* ---- 令牌区 ---- */}
        <div className="mt-3 rounded-[var(--radius-sm)] border border-[var(--color-border)]
                        bg-[var(--color-surface-raised)] px-2.5 py-2">
          <div className="flex flex-wrap items-center gap-2 text-[0.76rem]">
            <KeyRound className="h-3.5 w-3.5 text-[var(--color-text-secondary)]" />
            <span className="text-[var(--color-text)]">HTTP 访问令牌</span>
            <span className="font-mono text-[0.72rem] text-[var(--color-text-muted)]">
              {status?.token_set
                ? `已生成 · ${status.token_masked} · 世代 ${status.token_epoch}`
                : "未生成（点「显示令牌」即创建）"}
            </span>
            <div className="flex-1" />
            <Button
              variant="secondary"
              size="sm"
              data-od-id="mcp-reveal"
              disabled={busy !== ""}
              title="取回明文令牌供复制（仅本机 UI 动作，不经 AI 客户端）"
              onClick={reveal}
            >
              {busy === "token" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : (showToken ? <EyeOff className="h-3.5 w-3.5" /> : <Eye className="h-3.5 w-3.5" />)}
              显示令牌
            </Button>
            <Button
              variant="ghost"
              size="sm"
              data-od-id="mcp-rotate"
              disabled={busy !== ""}
              title="世代号 +1，所有旧令牌立即失效"
              onClick={rotate}
            >
              {busy === "rotate" ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <RefreshCw className="h-3.5 w-3.5" />}
              轮换
            </Button>
          </div>

          {token && showToken ? (
            <div className="mt-2 flex items-center gap-2">
              <input
                readOnly
                data-od-id="mcp-token-value"
                value={token}
                className="h-[30px] min-w-0 flex-1 rounded-[var(--radius-sm)] border
                           border-[var(--color-border)] bg-[var(--color-surface)] px-2
                           font-mono text-[0.72rem] text-[var(--color-text)] outline-none"
                onFocus={(e) => e.currentTarget.select()}
              />
              <Button
                variant="secondary"
                size="sm"
                data-od-id="mcp-token-copy"
                onClick={() => copy(token, "令牌")}
              >
                <Copy className="h-3.5 w-3.5" />复制
              </Button>
            </div>
          ) : null}
          {token ? (
            <div className="mt-1.5 text-[0.7rem] leading-relaxed text-[var(--color-text-muted)]">
              刷新页面后此明文即消失（不落 localStorage）；已生成的令牌仍有效，
              需要时再点「显示令牌」取回。
            </div>
          ) : null}
        </div>

        {/* ---- 工具清单 ---- */}
        {tools ? (
          <div className="mt-3">
            <div className="mb-1.5 flex items-center gap-2 text-[0.76rem]
                            text-[var(--color-text)]">
              <ShieldAlert className="h-3.5 w-3.5 text-[var(--color-text-secondary)]" />
              当前可见工具 <span className="font-mono">{tools.length}</span> 个
              <span className="text-[0.72rem] text-[var(--color-text-muted)]">
                （受 scope 与写开关双重过滤）
              </span>
            </div>
            <div className="max-h-[220px] overflow-auto rounded-[var(--radius-sm)] border
                            border-[var(--color-border)]">
              {tools.map((t) => (
                <div
                  key={t.name}
                  className="flex items-start gap-2 border-b border-[var(--color-border)]
                             px-2.5 py-1.5 last:border-b-0"
                >
                  <span
                    className={
                      "mt-[1px] shrink-0 rounded-[var(--radius-sm)] border px-1 py-[0.5px] " +
                      "font-mono text-[0.66rem] " +
                      (t.level === "write"
                        ? "border-[var(--color-warning)] text-[var(--color-warning)]"
                        : "border-[var(--color-border-strong)] text-[var(--color-text-secondary)]")
                    }
                  >
                    {t.level}
                  </span>
                  <div className="min-w-0">
                    <div className="font-mono text-[0.72rem] text-[var(--color-text)]">
                      {t.name}
                    </div>
                    <div className="text-[0.7rem] leading-relaxed text-[var(--color-text-muted)]">
                      {t.summary}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {/* ---- 错误（原样暴露，不吞）---- */}
        {err ? (
          <div className="mt-2 flex items-start gap-1.5 text-[0.74rem] text-[var(--color-danger)]">
            <Trash2 className="mt-[2px] h-3.5 w-3.5 shrink-0" />
            <span>{err}</span>
          </div>
        ) : null}

        {!enabled && status ? (
          <div className="mt-2 text-[0.72rem] leading-relaxed text-[var(--color-text-muted)]">
            提示：MCP 当前已关闭（默认安全姿态）。关闭时本机 HTTP 服务不提供能力，
            且令牌校验一律拒绝。若只为 Hermes / Codex 这类 stdio 客户端使用，
            可以不开启本开关。
          </div>
        ) : null}
      </SetCardBody>
    </SetCard>
  );
}
