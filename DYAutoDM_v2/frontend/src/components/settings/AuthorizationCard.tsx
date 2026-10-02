/**
 * 通知设置 —— 远程操作授权卡片（IM 网关）
 *
 * ## 为什么独立（2026-09-15 大组件打散）
 *
 * `NotifySection.tsx` 原把「授权管理（IM 网关）」这块 181 行的 UI 内联在
 * 主组件的返回里。抽出为受控组件后，主组件只负责数据与状态，卡片只负责渲染。
 *
 * ## 依赖（显式 props，避免闭包隐式耦合）
 *   mode / pending / grants / busy / onMode / onApprove / onReject
 * **纯搬移**——JSX、文案、交互逐字节不变。
 */
import { useEffect, useState } from "react";
import { Dot, Pill } from "../ui";
import { confirmDialog } from "@/components/ui/modal";
import { Card } from "./notify-widgets";
import { ROLE_LABELS, CHANNEL_SHORT } from "./settings-shared";
import type { GatewayGrant, GatewayPending } from "../../api/client";

interface AuthorizationCardProps {
  mode: string;
  pending: GatewayPending[];
  grants: Record<string, GatewayGrant>;
  busy: boolean;
  onMode: (mode: string) => void;
  onApprove: (id: string, role: string) => void;
  onReject: (id: string) => void;   // revoke
}

export function AuthorizationCard(props: AuthorizationCardProps) {
  const { mode: gwMode, pending: pendingList, grants, busy: gwBusy, onMode, onApprove, onReject } = props;
  // 2026-09-28 修：原用 `defaultOpen={pendingList.length > 0}` 表达「有待审就展开」，
  // 但待审列表是**异步**拉取的（挂载时为空）⇒ 数据到达后面板不会展开，
  // 待审项被折叠在下面用户看不见。改为**受控** + 待审出现时自动展开一次
  // （用户手动收起后不再强行展开，尊重其意图）。
  const [open, setOpen] = useState(pendingList.length > 0);
  const [autoExpanded, setAutoExpanded] = useState(false);
  useEffect(() => {
    if (pendingList.length > 0 && !autoExpanded && !open) {
      setOpen(true);
      setAutoExpanded(true);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingList.length]);
  // ===== 授权管理（IM 网关，v0.38.5）=====
  return (
  <Card
    title={
      <>
        <strong>远程操作授权</strong>
        <Dot c={pendingList.length ? "warn" : gwMode === "open" ? "warn" : "ok"} />
        <span style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
          {gwMode === "open"
            ? "开放模式（未拦截）"
            : pendingList.length
              ? `${pendingList.length} 条待授权`
              : "配对模式（拦截未授权）"}
        </span>
        {pendingList.length > 0 && <Pill c="warn">{pendingList.length} 待审</Pill>}
      </>
    }
    open={open}
    onOpenChange={setOpen}
  >
    <div style={{ fontSize: 12, color: "var(--color-text-muted)", lineHeight: 1.6, marginBottom: 10 }}>
      配对模式（推荐）：所有来源可给 bot 发消息，但未授权者的指令一律拦截并回引导语，
      其首条消息会显示在下方待审区，由你甄别后授予权限组。开放模式不拦截（仅测试用）。
    </div>

    <div style={{ display: "flex", gap: 8, marginBottom: 12, alignItems: "center" }}>
      <span style={{ fontSize: 12, color: "var(--color-text-muted)" }}>网关模式</span>
      <button
        className={"btn sm" + (gwMode === "pairing" ? " accent" : "")}
        disabled={gwBusy}
        onClick={() => onMode("pairing")}
      >
        配对（拦截未授权）
      </button>
      <button
        className={"btn sm" + (gwMode === "open" ? " accent" : "")}
        disabled={gwBusy}
        onClick={async () => {
          if (await confirmDialog({ message: "开放模式将允许所有来源直接执行指令（受权限组限制的除外），确认切换？" }))
            onMode("open");
        }}
      >
        开放（不拦截）
      </button>
    </div>

    {/* 待授权 */}
    {pendingList.length > 0 && (
      <div style={{ marginBottom: 14 }}>
        <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 6 }}>
          待授权（{pendingList.length}）
        </div>
        {pendingList.map((p) => (
          <div
            key={p.key}
            style={{
              border: "1px solid var(--warn, #d89614)",
              borderRadius: 10,
              padding: "10px 12px",
              marginBottom: 8,
              background: "color-mix(in oklch, var(--warn, #d89614) 8%, transparent)",
            }}
          >
            <div style={{ fontSize: 12.5, marginBottom: 4 }}>
              <b>{CHANNEL_SHORT[p.channel_id] || p.channel_id}</b>
              <span style={{ color: "var(--color-text-muted)", marginLeft: 8, fontFamily: "monospace", fontSize: 11.5 }}>
                {p.sender_id}
              </span>
              <span style={{ color: "var(--color-text-muted)", marginLeft: 8, fontSize: 11 }}>
                共 {p.msg_count} 条 · 最近 {new Date(p.last_at * 1000).toLocaleTimeString()}
              </span>
            </div>
            {p.last_text && (
              <div style={{ fontSize: 12, color: "var(--color-text)", marginBottom: 8, opacity: 0.85 }}>
                「{p.last_text}」
              </div>
            )}
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              <button
                className="inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold transition-[background-color,color,border-color,box-shadow,transform,opacity] duration-200 ease-[var(--ease-spring)] cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-ring)] disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] bg-[var(--color-accent)] text-[#08130a] shadow-lg hover:bg-[var(--color-accent-hover)] h-9 px-4 text-[0.78rem] rounded-[10px]"
                disabled={gwBusy}
                onClick={() => onApprove(p.key, "admin")}
                title="全授权（可执行一切指令）"
              >
                设为管理员
              </button>
              <button
                className="inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold transition-[background-color,color,border-color,box-shadow,transform,opacity] duration-200 ease-[var(--ease-spring)] cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-ring)] disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] bg-[var(--color-surface-raised)] text-[var(--color-text)] shadow-[inset_0_0_0_1px_var(--color-border)] hover:bg-[var(--color-surface-solid)] h-9 px-4 text-[0.78rem] rounded-[10px]"
                disabled={gwBusy}
                onClick={() => onApprove(p.key, "operator")}
              >
                操作员
              </button>
              <button
                className="inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold transition-[background-color,color,border-color,box-shadow,transform,opacity] duration-200 ease-[var(--ease-spring)] cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-ring)] disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] bg-[var(--color-surface-raised)] text-[var(--color-text)] shadow-[inset_0_0_0_1px_var(--color-border)] hover:bg-[var(--color-surface-solid)] h-9 px-4 text-[0.78rem] rounded-[10px]"
                disabled={gwBusy}
                onClick={() => onApprove(p.key, "viewer")}
              >
                仅查看
              </button>
              <button
                className="inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold transition-[background-color,color,border-color,box-shadow,transform,opacity] duration-200 ease-[var(--ease-spring)] cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-ring)] disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] border border-[color-mix(in_srgb,var(--color-danger)_25%,transparent)] bg-[var(--color-danger-soft)] text-[var(--color-danger)] hover:bg-[var(--color-danger)] hover:text-white h-9 px-4 text-[0.78rem] rounded-[10px]"
                disabled={gwBusy}
                onClick={() => onApprove(p.key, "blocked")}
              >
                拉黑
              </button>
            </div>
          </div>
        ))}
      </div>
    )}

    {/* 已授权列表 */}
    <div>
      <div style={{ fontSize: 12.5, fontWeight: 600, marginBottom: 6 }}>
        已授权（{Object.keys(grants).length}）
      </div>
      {Object.keys(grants).length === 0 && (
        <div style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
          还没有授权任何来源。配对模式下，对方给 bot 发一条消息即可出现在待审区。
        </div>
      )}
      {Object.entries(grants).map(([key, g]) => (
        <div
          key={key}
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            padding: "8px 10px",
            background: "var(--color-surface-solid)",
            border: "1px solid var(--color-border)",
            borderRadius: 8,
            marginBottom: 6,
            fontSize: 12,
            flexWrap: "wrap",
          }}
        >
          <span style={{ flex: "0 0 auto", fontWeight: 600 }}>
            {CHANNEL_SHORT[g.channel_id] || g.channel_id}
          </span>
          <span
            style={{
              flex: 1,
              minWidth: 140,
              fontFamily: "monospace",
              fontSize: 11.5,
              color: "var(--color-text-muted)",
              wordBreak: "break-all",
            }}
          >
            {g.sender_id}
          </span>
          <span
            style={{
              flex: "0 0 auto",
              color: g.role === "admin" ? "var(--color-accent)" : g.role === "blocked" ? "var(--color-danger)" : "var(--color-text-muted)",
            }}
          >
            {ROLE_LABELS[g.role] || g.role}
          </span>
          {g.role !== "admin" && g.role !== "blocked" && (
            <button
              className="inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold transition-[background-color,color,border-color,box-shadow,transform,opacity] duration-200 ease-[var(--ease-spring)] cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-ring)] disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] bg-[var(--color-surface-raised)] text-[var(--color-text)] shadow-[inset_0_0_0_1px_var(--color-border)] hover:bg-[var(--color-surface-solid)] h-9 px-4 text-[0.78rem] rounded-[10px]"
              disabled={gwBusy}
              onClick={() => onApprove(key, "admin")}
              title="提升为管理员"
            >
              升级管理员
            </button>
          )}
          <button
            className="inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold transition-[background-color,color,border-color,box-shadow,transform,opacity] duration-200 ease-[var(--ease-spring)] cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-ring)] disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] bg-[var(--color-surface-raised)] text-[var(--color-text)] shadow-[inset_0_0_0_1px_var(--color-border)] hover:bg-[var(--color-surface-solid)] h-9 px-4 text-[0.78rem] rounded-[10px]"
            disabled={gwBusy}
            onClick={() => onReject(key)}
          >
            撤销
          </button>
        </div>
      ))}
    </div>
  </Card>
  );
}
