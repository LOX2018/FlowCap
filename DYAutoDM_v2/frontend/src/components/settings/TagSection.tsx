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
import {
  TAG_MANAGED_SECTIONS,
  TAG_SECTION_LABELS,
  type TagManagedSection,
} from "../../api/client";
import { SetCard, SetCardHead, SetCardBody } from "@/components/page/set-card";
import { errMsg, BTN_PRIMARY, BTN_GHOST } from "./settings-shared";
import { confirmDialog } from "@/components/ui/modal";

/** 绑定下拉统一样式（整账号 / 板块级共用，避免两处各写一份）。
 *
 *  2026-10-02 修（用户实测「整账号下拉通栏、长度不合理、布局崩坏」）：
 *  原为 `flex: 1` ⇒ 在整账号行里被拉成**通栏**，与同卡片内板块级下拉
 *  （`flex: 0 0 auto`）宽度严重不一致，挤掉右侧「生效值」并破坏对齐节奏。
 *  现**默认不伸缩**（auto），宽度由各调用点显式给定；整账号行由 160px 标签
 *  + 自适应下拉 + 生效值组成，与板块级行视觉一致。 */
const selectStyle: React.CSSProperties = {
  flex: "0 0 auto",
  maxWidth: 200,
  background: "var(--color-surface-raised)",
  border: "1px solid var(--color-border)",
  borderRadius: 4,
  padding: "4px 6px",
  fontSize: 12,
  color: "var(--color-text)",
  outline: "none",
};

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
  const secBindings = q.data?.bindings_section || {};
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

  const bindSecMut = useMutation({
    mutationFn: (v: {
      account: string;
      section: TagManagedSection;
      tagId: string;
    }) => api.bindTagSection(v.account, v.section, v.tagId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["config-tags"] }),
    onError: (e) => push(`板块绑定失败：${errMsg(e)}`),
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
        本页<b>只负责标签本身</b>（新建 / 删除 / 账号绑定）。
        标签的<b>参数</b>在各自的原页面里配置：进入「私信发送 / 直播监听 /
        捕获与存储 / 内容采集」，用顶部的<b>「保存到 全局 / 标签」</b>切到目标标签后保存。
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
                className={selId === t.id ? BTN_PRIMARY : BTN_GHOST}
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
                onClick={async () => {
                  if (
                    await confirmDialog({
                      message: `确认删除标签「${sel?.name}」？其参数会被清空，绑定它的账号自动解绑。`,
                      danger: true,
                    })
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

      {/* ② 账号绑定
          2026-10-02（用户要求「配置标签需要做页面划分：配置标签页只负责标签，
          具体的事项依旧由其他原本的页面来配置」）：
          本页**不再承载参数编辑** —— 原先内嵌的 UnifiedConfigSection(scope=标签)
          已移除。改标签参数请到对应原页面（私信发送 / 直播监听 / 捕获与存储 /
          内容采集），用其顶部的「保存到 全局 / 标签」切换栏选择目标标签后保存。 */}
      <SetCard>
        <SetCardHead
          title="账号绑定"
          description={`${acctList.length} 个账号 · 整账号为回落值，板块级优先`}
        />
        <SetCardBody>
          {acctList.length === 0 && (
            <div style={{ fontSize: 12, color: "var(--color-text-muted)" }}>暂无账号</div>
          )}
          {acctList.map((name) => {
            const bound = bindings[name] || "";
            const boundName = tags.find(
              (t: ConfigTagSummary) => t.id === bound,
            )?.name;
            const secMap: Record<string, string> = secBindings[name] || {};
            return (
              <div
                key={name}
                style={{
                  display: "flex",
                  flexDirection: "column",
                  gap: 6,
                  padding: "8px 10px",
                  background: "var(--color-surface-solid)",
                  borderRadius: 8,
                  border: "1px solid var(--color-border)",
                  marginBottom: 8,
                }}
              >
                {/* 整账号绑定：未绑板块时的回落值（原有行为，保持不变） */}
                <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
                  <span style={{ flex: "0 0 108px", fontSize: 12.5, overflow: "hidden",
                                 textOverflow: "ellipsis", whiteSpace: "nowrap" }}
                        title={name}>{name}</span>
                  <select
                    value={bound}
                    onChange={(e) =>
                      bindMut.mutate({ account: name, tagId: e.target.value })
                    }
                    style={{ ...selectStyle, minWidth: 150 }}
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
                      maxWidth: 120,
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                      color: boundName ? "var(--color-accent)" : "var(--color-text-muted)",
                    }}
                    title={boundName || "全局"}
                  >
                    {boundName || "全局"}
                  </span>
                </div>

                {/* 板块级绑定（B-4 第二层）：每个板块可单独指派标签，留空 = 回落整账号 */}
                <div
                  style={{
                    display: "flex",
                    flexWrap: "wrap",
                    gap: 8,
                    marginLeft: 4,
                    paddingLeft: 8,
                    borderLeft: "1px solid var(--color-border)",
                  }}
                >
                  {TAG_MANAGED_SECTIONS.map((sec) => {
                    const v = secMap[sec] || "";
                    const nv = tags.find(
                      (t: ConfigTagSummary) => t.id === v,
                    )?.name;
                    const eff = v || bound;
                    const effName = tags.find(
                      (t: ConfigTagSummary) => t.id === eff,
                    )?.name;
                    return (
                      <label
                        key={sec}
                        style={{
                          display: "flex",
                          alignItems: "center",
                          gap: 5,
                          fontSize: 11.5,
                          cursor: "pointer",
                        }}
                      >
                        <span
                          style={{
                            flex: "0 0 64px",
                            color: v
                              ? "var(--color-accent)"
                              : "var(--color-text-muted)",
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                          }}
                          title={TAG_SECTION_LABELS[sec]}
                        >
                          {TAG_SECTION_LABELS[sec]}
                        </span>
                        <select
                          value={v}
                          onChange={(e) =>
                            bindSecMut.mutate({
                              account: name,
                              section: sec,
                              tagId: e.target.value,
                            })
                          }
                          style={{ ...selectStyle, flex: "0 0 auto", fontSize: 11 }}
                        >
                          <option value="">（跟随整账号）</option>
                          {tags.map((t: ConfigTagSummary) => (
                            <option key={t.id} value={t.id}>
                              {t.name}
                            </option>
                          ))}
                        </select>
                        {/* 生效值：板块未绑时显示整账号回落，一眼看清实际会用哪套参数 */}
                        <span
                          style={{
                            maxWidth: 130,
                            overflow: "hidden",
                            textOverflow: "ellipsis",
                            whiteSpace: "nowrap",
                            color: nv ? "var(--color-accent)" : "var(--color-text-muted)",
                          }}
                          title={nv ? nv : effName ? `跟随：${effName}` : "全局"}
                        >
                          {nv ? nv : effName ? `跟随：${effName}` : "全局"}
                        </span>
                      </label>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </SetCardBody>
      </SetCard>
    </div>
  );
}
