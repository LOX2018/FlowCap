/**
 * 引擎多任务卡片（ADR-002 §5.6）：每卡展示一个账号的引擎状态和操控按钮。
 *
 * 数据源：/api/engine/accounts（EngineRegistry 只读快照）。
 * 账号元信息（名称、头像色）取自 /api/accounts 缓存。
 *
 * 约束：
 * - 不改后端 API / EngineRegistry / 设置页
 * - 单账号场景不崩溃
 */
import { useState, useCallback, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import { Play, Pause, Square, RotateCcw } from "lucide-react";

import { api } from "../../api/client";
import { RealAcct } from "./live-shared";

import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { StatusDot } from "@/components/ui/status-dot";
import { EmptyState } from "@/components/ui/empty-state";
import { hue, Avatar } from "@/components/ui";

/* ── 引擎状态 → 展示色 ── */

const STATE_COLORS: Record<string, { badge: "warning" | "success" | "danger" | "outline"; dot: "warn" | "ok" | "danger" | "muted"; pulse: boolean; label: string }> = {
  starting: { badge: "warning", dot: "warn", pulse: true, label: "启动中" },
  running: { badge: "success", dot: "ok", pulse: true, label: "运行中" },
  paused: { badge: "warning", dot: "warn", pulse: false, label: "已暂停" },
  stopping: { badge: "warning", dot: "warn", pulse: false, label: "收尾中" },
  stopped: { badge: "outline", dot: "muted", pulse: false, label: "已停止" },
  error: { badge: "danger", dot: "danger", pulse: false, label: "异常" },
};

function stateMeta(s: string) {
  return STATE_COLORS[s] || { badge: "outline" as const, dot: "muted" as const, pulse: false, label: s || "空闲" };
}

/* ── 判断操作按钮可见性 ── */

function canStart(s: string) {
  return !s || s === "idle" || s === "stopped";
}
function canPause(s: string) {
  return s === "running";
}
function canResume(s: string) {
  return s === "paused";
}
function canStop(s: string) {
  return ["starting", "running", "paused", "stopping"].includes(s);
}

/* ── Props ── */

interface EngineCardsProps {
  push: (msg: string, holdMs?: number) => void;
}

/* ── 组件 ── */

export default function EngineCards({ push }: EngineCardsProps) {
  // 账号列表（来自 /api/accounts 缓存，含 uid/status 等详情）
  const { data: accountsRaw } = useQuery({
    queryKey: ["accounts"],
    queryFn: async () => (await api.getAccounts()) as RealAcct[],
  });
  const accounts = useMemo(() => (Array.isArray(accountsRaw) ? accountsRaw : []), [accountsRaw]);

  // 引擎状态列表（多账号数据源，3s 轮询）
  const { data: engineRaw, refetch } = useQuery({
    queryKey: ["engine-accounts"],
    queryFn: () => api.listEngineAccounts(),
    refetchInterval: 3000,
  });
  const engineItems = useMemo(() => (engineRaw?.items || []), [engineRaw]);

  // 合并账号元信息到引擎状态
  // 引擎可能只显示「已启动」的账号；未启动的账号不出现
  // 单账号场景：引擎 items 为空时展示一条提示而非空页面
  const cards = useMemo(() => {
    return engineItems.map((e) => {
      const acctMeta = accounts.find((a) => a.name === e.acct);
      return { ...e, meta: acctMeta || null };
    });
  }, [engineItems, accounts]);

  // 操作防抖：防止在请求返回前重复点击
  const [loading, setLoading] = useState<Record<string, string>>({});

  const act = useCallback(
    async (acct: string, action: string, fn: () => Promise<unknown>) => {
      const key = acct + ":" + action;
      if (loading[key]) return;
      setLoading((prev) => ({ ...prev, [key]: action }));
      try {
        const r = (await fn()) as { ok: boolean; state?: string; error?: string };
        if (r.ok) {
          push(`引擎 ${action} 成功 · ${acct}`);
          void refetch();
        } else {
          push(`引擎 ${action} 失败 · ${acct}: ${r.error || r.state || ""}`);
        }
      } catch (e: unknown) {
        push(`引擎 ${action} 异常 · ${acct}: ${e instanceof Error ? e.message : String(e)}`);
      } finally {
        setLoading((prev) => {
          const next = { ...prev };
          delete next[key];
          return next;
        });
      }
    },
    [push, refetch],
  );

  if (cards.length === 0) {
    return (
      <Card>
        <CardContent className="p-6">
          <EmptyState
            icon={<Play className="h-6 w-6" />}
            title="暂无运行中的引擎"
            description="引擎未启动时此处为空。前往「直播监听」页为某个账号启动引擎后，这里会展示对应卡片。"
          />
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
      {cards.map((item) => {
        const meta = stateMeta(item.state);
        const busy = loading[item.acct + ":start"] || loading[item.acct + ":pause"]
          || loading[item.acct + ":resume"] || loading[item.acct + ":stop"];
        return (
          <Card key={item.acct || "__anon__"} className="overflow-hidden">
            {/* ── 头部：账号名 + 状态徽章 ── */}
            <div className="flex items-center gap-2.5 border-b border-[var(--color-border)]
                            bg-[var(--color-surface-raised)] px-4 py-3">
              <Avatar name={item.meta?.name || item.acct} h={hue((item.meta?.name || item.acct).length)} />
              <div className="min-w-0 flex-1">
                <div className="text-[0.88rem] font-semibold text-[var(--color-text)]">
                  {item.meta?.name || item.acct || "(匿名)"}
                </div>
                {item.meta?.uid && (
                  <div className="font-mono text-[0.72rem] text-[var(--color-text-muted)]">
                    UID: {item.meta.uid}
                  </div>
                )}
              </div>
              <Badge variant={meta.badge}>
                <StatusDot tone={meta.dot} pulse={meta.pulse} />
                {meta.label}
              </Badge>
            </div>

            {/* ── 体：房间号 / 已发送 / 状态消息 ── */}
            <div className="space-y-1.5 border-b border-[var(--color-border)] px-4 py-2.5">
              <div className="flex items-center justify-between gap-3">
                <span className="text-[0.75rem] text-[var(--color-text-muted)]">房间号</span>
                <span className="truncate font-mono text-[0.75rem] font-semibold text-[var(--color-text)]">
                  {item.live_id || item.live_url || "—"}
                </span>
              </div>
              <div className="flex items-center justify-between gap-3">
                <span className="text-[0.75rem] text-[var(--color-text-muted)]">已私信</span>
                <span className="font-mono text-[0.75rem] text-[var(--color-text)]">
                  {item.sent ?? 0}
                </span>
              </div>
              {item.status_msg && (
                <div className="flex items-center justify-between gap-3">
                  <span className="text-[0.75rem] text-[var(--color-text-muted)]">状态</span>
                  <span className="truncate text-[0.75rem] text-[var(--color-text-secondary)]">
                    {item.status_msg}
                  </span>
                </div>
              )}
            </div>

            {/* ── 底：操作按钮 ── */}
            <div className="flex gap-1.5 px-4 py-2.5">
              {canStart(item.state) && (
                <Button
                  variant="secondary"
                  size="sm"
                  className="flex-1"
                  disabled={!!busy}
                  onClick={() => act(item.acct, "start", () => api.start({ acct: item.acct }))}
                >
                  {loading[item.acct + ":start"] ? (
                    <span className="inline-block h-3 w-3 animate-spin rounded-full border-2
                                     border-current border-t-transparent" />
                  ) : (
                    <Play className="h-3.5 w-3.5" />
                  )}
                  开始
                </Button>
              )}
              {canPause(item.state) && (
                <Button
                  variant="secondary"
                  size="sm"
                  className="flex-1"
                  disabled={!!busy}
                  onClick={() => act(item.acct, "pause", () => api.pauseEngine(item.acct))}
                >
                  {loading[item.acct + ":pause"] ? (
                    <span className="inline-block h-3 w-3 animate-spin rounded-full border-2
                                     border-current border-t-transparent" />
                  ) : (
                    <Pause className="h-3.5 w-3.5" />
                  )}
                  暂停
                </Button>
              )}
              {canResume(item.state) && (
                <Button
                  variant="secondary"
                  size="sm"
                  className="flex-1"
                  disabled={!!busy}
                  onClick={() => act(item.acct, "resume", () => api.resumeEngine(item.acct))}
                >
                  {loading[item.acct + ":resume"] ? (
                    <span className="inline-block h-3 w-3 animate-spin rounded-full border-2
                                     border-current border-t-transparent" />
                  ) : (
                    <RotateCcw className="h-3.5 w-3.5" />
                  )}
                  继续
                </Button>
              )}
              {canStop(item.state) && (
                <Button
                  variant="danger-outline"
                  size="sm"
                  className="flex-1"
                  disabled={!!busy}
                  onClick={() => act(item.acct, "stop", () => api.stopSoftEngine(item.acct))}
                >
                  {loading[item.acct + ":stop"] ? (
                    <span className="inline-block h-3 w-3 animate-spin rounded-full border-2
                                     border-current border-t-transparent" />
                  ) : (
                    <Square className="h-3.5 w-3.5" />
                  )}
                  停止
                </Button>
              )}
            </div>
          </Card>
        );
      })}
    </div>
  );
}