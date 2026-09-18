/**
 * `NicknameFallbackSection` —— 昵称兜底运维卡（2026-09-17，E11）
 *
 * ## 设计意图（对照铁律「显式配置原则」+ 昵称风控红线）
 *
 * 昵称的唯一常规来源是 **BCC 被动截获**；本功能只是**兜底**，且属**主动查询**
 * （有风控成本），因此 UI 必须满足三条：
 *
 * 1. **默认关闭且关闭态一眼可见** —— 顶部状态行直接写「已关闭 / 已开启」，
 *    不靠用户去翻开关才能确认；
 * 2. **绝不自动跑** —— 只有用户点「执行一次」才发请求；`dry-run` 只列候选、
 *    零外呼（先看会查到谁，再决定要不要真查）；
 * 3. **失败原因不静默** —— `off` / `interval` / `daily-limit` 等闸门原因原样展示，
 *    用户能分清是「没开」还是「被限速」。
 *
 * 开关与三重上限本身由 `UnifiedConfigSection`（配置域 `dm`）渲染与保存，
 * 本卡只做**状态与执行**，避免同一配置两处可写。
 */
import { useState } from "react";
import { ShieldAlert, Play, Eye, Loader2, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { api } from "@/api/client";
import { cn } from "@/lib/utils";

/** 把后端闸门原因翻译成用户能看懂的话（**不隐藏、不改写语义**）。 */
function gateText(gate?: string, enabled?: boolean): string {
  if (gate === "disabled" || enabled === false) return "已关闭（默认）";
  if (gate === "interval") return "距上次执行未满最小间隔";
  if (gate === "daily-limit") return "已达当日上限";
  if (gate === "ok" || enabled) return "已开启";
  return gate ? `闸门：${gate}` : "状态未知";
}

export default function NicknameFallbackSection() {
  /** 用哪个账号执行：留空则后端按账号必填校验返回 400（提示更明确） */
  const [account, setAccount] = useState("");
  const [busy, setBusy] = useState<"" | "status" | "dry" | "run">("");
  const [status, setStatus] = useState<Awaited<
    ReturnType<typeof api.nicknameFallbackStatus>
  > | null>(null);
  const [last, setLast] = useState<string>("");
  const [err, setErr] = useState<string>("");

  const loadStatus = async () => {
    setBusy("status");
    setErr("");
    try {
      setStatus(await api.nicknameFallbackStatus());
    } catch (e) {
      setErr(e instanceof Error ? e.message : "读取状态失败");
    } finally {
      setBusy("");
    }
  };

  const run = async (dry: boolean) => {
    if (!account.trim()) {
      setErr("请先填写要执行的账号");
      return;
    }
    setBusy(dry ? "dry" : "run");
    setErr("");
    setLast("");
    try {
      const r = await api.nicknameFallbackRun(account.trim(), {
        dryRun: dry,
        limit: 10,
      });
      if (!r.ok) {
        // 闸门拒绝（关闭态/限速）——原因原样展示，不吞
        setLast(`未执行：${r.reason || "未知原因"}`);
      } else if (dry) {
        setLast(`候选 ${r.candidates ?? 0} 个（dry-run：未发任何请求）`);
      } else {
        setLast(
          `候选 ${r.candidates ?? 0} · 已查 ${r.queried ?? 0} · ` +
            `更新 ${r.updated ?? 0} · 跳过 ${r.skipped ?? 0}`,
        );
      }
    } catch (e) {
      setErr(e instanceof Error ? e.message : "执行失败");
    } finally {
      setBusy("");
    }
  };

  const enabled = status?.config?.enabled;

  return (
    <Card className="mt-3.5">
      <CardContent className="space-y-3 p-3.5">
        <div className="flex items-start gap-2">
          <ShieldAlert className="mt-[2px] h-4 w-4 shrink-0 text-[var(--color-warning)]" />
          <div className="min-w-0">
            <div className="text-[0.82rem] font-medium text-[var(--color-text)]">
              昵称兜底（主动查询 · 有风控成本）
            </div>
            <div className="mt-0.5 text-[0.76rem] leading-relaxed text-[var(--color-text-muted)]">
              昵称的常规来源是「被动截获」。本功能仅在**库里没有昵称**时做低频兜底，
              在账号自己的登录态里查询；**绝不覆盖已有昵称，也不会自动执行**。
            </div>
          </div>
        </div>

        {/* 状态行：关闭态一眼可见（默认关闭是安全前提） */}
        <div className="flex flex-wrap items-center gap-2 rounded-[var(--radius-sm)]
                        bg-[var(--color-surface-raised)] px-2.5 py-2 text-[0.76rem]">
          <span
            data-od-id="nickname-fallback-state"
            className={cn(
              "rounded-[var(--radius-sm)] border px-1.5 py-[1px] font-mono text-[0.7rem]",
              enabled
                ? "border-[var(--color-warning)] text-[var(--color-warning)]"
                : "border-[var(--color-border-strong)] text-[var(--color-text-secondary)]",
            )}
          >
            {enabled ? "已开启" : "已关闭"}
          </span>
          <span className="text-[var(--color-text-secondary)]">
            {status ? gateText(status.gate, enabled) : "（未读取状态）"}
          </span>
          {status?.limit_info ? (
            <span className="font-mono text-[0.7rem] text-[var(--color-text-muted)]">
              {Object.entries(status.limit_info)
                .filter(([, v]) => typeof v !== "object")
                .map(([k, v]) => `${k}=${String(v)}`)
                .join(" ")}
            </span>
          ) : null}
          <div className="flex-1" />
          <Button
            variant="ghost"
            size="sm"
            data-od-id="nickname-fallback-refresh"
            disabled={busy === "status"}
            onClick={loadStatus}
          >
            {busy === "status" ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            刷新状态
          </Button>
        </div>

        {/* 执行区：账号 + 候选（零外呼）+ 真执行（有外呼） */}
        <div className="flex flex-wrap items-center gap-2">
          <input
            className="h-[32px] w-[180px] rounded-[var(--radius-sm)] border
                       border-[var(--color-border)] bg-[var(--color-surface)]
                       px-2 text-[0.78rem] text-[var(--color-text)]
                       outline-none focus:border-[var(--color-accent)]"
            placeholder="要执行的账号"
            value={account}
            onChange={(e) => setAccount(e.target.value)}
          />
          <Button
            variant="secondary"
            size="sm"
            data-od-id="nickname-fallback-dry"
            disabled={busy !== ""}
            title="只列出会被查询的会话，不发任何请求"
            onClick={() => run(true)}
          >
            {busy === "dry" ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Eye className="h-3.5 w-3.5" />
            )}
            查看候选（不发请求）
          </Button>
          <Button
            variant="secondary"
            size="sm"
            data-od-id="nickname-fallback-run"
            disabled={busy !== ""}
            title="真正执行一次兜底查询（受间隔/单次/日上限约束）"
            onClick={() => run(false)}
          >
            {busy === "run" ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Play className="h-3.5 w-3.5" />
            )}
            执行一次
          </Button>
        </div>

        {last ? (
          <div className="text-[0.76rem] text-[var(--color-text-secondary)]">{last}</div>
        ) : null}
        {err ? (
          <div className="text-[0.76rem] text-[var(--color-danger)]">{err}</div>
        ) : null}
        {!enabled && status ? (
          <div className="text-[0.74rem] text-[var(--color-text-muted)]">
            提示：开关关闭时，「执行一次」会被后端直接拒绝（上方会显示原因）。
            要开启请用左侧「私信 / 昵称兜底」里的开关。
          </div>
        ) : null}
      </CardContent>
    </Card>
  );
}
