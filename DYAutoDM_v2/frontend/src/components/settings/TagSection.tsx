/**
 * 配置标签管理（v0.38.2）
 *
 * **标签是指引，不是参数副本** —— 标签只存 {id, name}，
 * 参数仍由后端 app_config 按 scope 隔离存储。改标签里的参数 = 改该 scope。
 *
 * 职责边界（与 Agent 并列，不重叠）：
 *   - Agent 管「回什么」：回复内容（prompt / 知识库 / 话术 / 档位）
 *   - 标签 管「怎么发」：发送风控频率 / 直播监听 / 历史捕获策略
 *
 * UI：① 标签列表（新建/删除/选中）② 选中标签的参数（复用 UnifiedConfigSection）
 *      ③ 账号绑定矩阵
 */
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { PageProps } from "../../api/client";
import type { ConfigTagSummary } from "../../api/client";
import UnifiedConfigSection from "./UnifiedConfigSection";
import { SetCard, SetCardHead, SetCardBody } from "@/components/page/set-card";

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export default function TagSection(props: PageProps) {
  const { api, ready, push } = props;
  const qc = useQueryClient();
  const [selId, setSelId] = useState<string>("");
  const [newName, setNewName] = useState("");

  const q = useQuery({
    queryKey: ["config-tags"],
    queryFn: () => api.listTags(),
    enabled: !!ready,
  });

  const accountsQ = useQuery({
    queryKey: ["accounts"],
    queryFn: () => api.getAccounts(),
    enabled: !!ready,
  });

  const tags = q.data?.tags || [];
  const bindings = q.data?.bindings || {};
  const sel = tags.find((t: ConfigTagSummary) => t.id === selId) || null;

  const acctList: string[] = (() => {
    const raw = accountsQ.data as unknown;
    const arr = Array.isArray(raw)
      ? raw
      : ((raw as { accounts?: unknown[] })?.accounts ?? []);
    return (arr as { name?: string }[])
      .map((a) => a?.name)
      .filter((n): n is string => !!n);
  })();

  const createMut = useMutation({
    mutationFn: (name: string) => api.saveTag(name),
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["config-tags"] });
      setSelId(d.tag.id);
      setNewName("");
      push(`已创建标签「${d.tag.name}」`);
    },
    onError: (e) => push(`创建失败：${errMsg(e)}`),
  });

  const delMut = useMutation({
    mutationFn: (id: string) => api.deleteTag(id),
    onSuccess: (d) => {
      qc.invalidateQueries({ queryKey: ["config-tags"] });
      setSelId("");
      const n = d.unbound_accounts?.length || 0;
      push(n > 0 ? `已删除，并解绑 ${n} 个账号` : "已删除");
    },
    onError: (e) => push(`删除失败：${errMsg(e)}`),
  });

  const bindMut = useMutation({
    mutationFn: (v: { account: string; tagId: string }) =>
      api.bindTag(v.account, v.tagId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["config-tags"] }),
    onError: (e) => push(`绑定失败：${errMsg(e)}`),
  });

  if (q.isLoading) {
    return <div style={{ padding: 20, color: "var(--color-text-muted)" }}>加载标签…</div>;
  }
  if (q.isError) {
    return (
      <div style={{ padding: 20, color: "var(--danger, #c0392b)" }}>
        标签加载失败：{errMsg(q.error)}
      </div>
    );
  }

  return (
    <div>
      <div
        style={{
          fontSize: 12,
          color: "var(--color-text-muted)",
          marginBottom: 12,
          lineHeight: 1.6,
        }}
      >
        标签管的是<b>「怎么发」</b>：私信风控频率、直播监听、历史捕获策略。
        回复内容（prompt / 知识库 / 话术）归 Agent 管，两者并列。
        <br />
        标签只是<b>指引</b>，参数仍存在各自的存储里，不做副本 —— 不会两处不一致。
      </div>

      {/* ① 标签列表 */}
      <SetCard>
        <SetCardHead title="标签列表" description={`${tags.length} 个`} />
        <SetCardBody>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginBottom: 10 }}>
            {tags.length === 0 && (
              <span style={{ fontSize: 12, color: "var(--color-text-muted)" }}>
                暂无标签，在下方输入名称后点「新建」创建
              </span>
            )}
            {tags.map((t: ConfigTagSummary) => (
              <button
                key={t.id}
                className={"btn sm" + (selId === t.id ? " accent" : " ghost")}
                onClick={() => setSelId(t.id)}
              >
                {t.name}
                {t.field_count > 0 ? ` · ${t.field_count} 项` : ""}
              </button>
            ))}
          </div>
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <input
              value={newName}
              onChange={(e) => setNewName(e.target.value)}
              placeholder="新标签名称，如：高频账号 / 保守账号"
              style={{
                flex: 1,
                background: "var(--color-surface-raised)",
                border: "1px solid var(--color-border)",
                borderRadius: 6,
                padding: "6px 10px",
                fontSize: 12.5,
                color: "var(--color-text)",
                outline: "none",
              }}
            />
            <button
              className="inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold transition-[background-color,color,border-color,box-shadow,transform,opacity] duration-200 ease-[var(--ease-spring)] cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-ring)] disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] bg-[var(--color-surface-raised)] text-[var(--color-text)] shadow-[inset_0_0_0_1px_var(--color-border)] hover:bg-[var(--color-surface-solid)] h-9 px-4 text-[0.78rem] rounded-[10px]"
              onClick={() => newName.trim() && createMut.mutate(newName.trim())}
              disabled={!newName.trim() || createMut.isPending}
            >
              新建
            </button>
            {selId && (
              <button
                className="inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold transition-[background-color,color,border-color,box-shadow,transform,opacity] duration-200 ease-[var(--ease-spring)] cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-ring)] disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] bg-[var(--color-surface-raised)] text-[var(--color-text)] shadow-[inset_0_0_0_1px_var(--color-border)] hover:bg-[var(--color-surface-solid)] h-9 px-4 text-[0.78rem] rounded-[10px]"
                onClick={() => {
                  if (
                    confirm(
                      `确认删除标签「${sel?.name}」？其参数会被清空，绑定它的账号自动解绑。`,
                    )
                  ) {
                    delMut.mutate(selId);
                  }
                }}
              >
                删除
              </button>
            )}
          </div>
        </SetCardBody>
      </SetCard>

      {/* ② 选中标签的参数（复用统一配置组件，仅切换 scope） */}
      {selId && (
        <div style={{ marginTop: 4 }}>
          <div
            style={{
              fontSize: 12,
              color: "var(--color-text-muted)",
              margin: "6px 0",
            }}
          >
            正在编辑标签「{sel?.name}」的参数 —— 只影响绑定它的账号
          </div>
          <UnifiedConfigSection
            {...props}
            scope={selId}
            scopeName={sel?.name}
            hideScopeBar
          />
        </div>
      )}

      {/* ③ 账号绑定 */}
      <SetCard>
        <SetCardHead title="账号绑定" description={`${acctList.length} 个账号`} />
        <SetCardBody>
          {acctList.length === 0 && (
            <div style={{ fontSize: 12, color: "var(--color-text-muted)" }}>暂无账号</div>
          )}
          {acctList.map((name) => {
            const bound = bindings[name] || "";
            const boundName = tags.find(
              (t: ConfigTagSummary) => t.id === bound,
            )?.name;
            return (
              <div
                key={name}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  padding: "8px 10px",
                  background: "var(--color-surface-solid)",
                  borderRadius: 8,
                  border: "1px solid var(--color-border)",
                  marginBottom: 8,
                }}
              >
                <span style={{ flex: "0 0 160px", fontSize: 12.5 }}>{name}</span>
                <select
                  value={bound}
                  onChange={(e) =>
                    bindMut.mutate({ account: name, tagId: e.target.value })
                  }
                  style={{
                    flex: 1,
                    background: "var(--color-surface-raised)",
                    border: "1px solid var(--color-border)",
                    borderRadius: 4,
                    padding: "4px 6px",
                    fontSize: 12,
                    color: "var(--color-text)",
                    outline: "none",
                  }}
                >
                  <option value="">（未绑定 · 使用全局配置）</option>
                  {tags.map((t: ConfigTagSummary) => (
                    <option key={t.id} value={t.id}>
                      {t.name}
                    </option>
                  ))}
                </select>
                <span
                  style={{
                    flex: "0 0 auto",
                    fontSize: 11,
                    color: boundName ? "var(--color-accent)" : "var(--color-text-muted)",
                  }}
                >
                  {boundName || "全局"}
                </span>
              </div>
            );
          })}
        </SetCardBody>
      </SetCard>
    </div>
  );
}
