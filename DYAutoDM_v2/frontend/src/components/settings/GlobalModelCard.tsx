/**
 * Agent 设置 —— 全局模型配置卡片
 *
 * ## 为什么独立（2026-09-15 大组件打散）
 * 原 `AgentSection.tsx` 内联此卡片（73 行，自带 useQuery 拉取全局模型配置）。
 * 它有独立的查询与契约，抽为独立文件更清晰。
 * **纯搬移**——查询键、渲染、文案不变。
 */
import { useState } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import type { PageProps } from "../../api/client";
import { Field, errMsg, SectionBlock, inputStyle } from "./settings-shared";

export function GlobalModelCard(props: PageProps) {
  const { api, ready, push } = props;
  const qc = useQueryClient();

  const q = useQuery({
    queryKey: ["ai-config", ""],
    queryFn: () => api.aiGetConfig(),
    enabled: !!ready,
    staleTime: 10_000,
  });
  const cfg = (q.data?.config || {}) as Record<string, unknown>;

  const [draft, setDraft] = useState<Record<string, string | string[]>>({});
  const cur = (k: string) =>
    draft[k] !== undefined ? draft[k] : String(cfg[k] ?? "");
  const set = (k: string, v: string) => setDraft({ ...draft, [k]: v });

  const saveMut = useMutation({
    // patch 语义：只传用户改过的字段（与「AI 回复引擎」一致）
    mutationFn: () => api.aiSaveConfig(draft),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["ai-config"] });
      setDraft({});
      push("全局模型配置已保存（AI 回复引擎同步生效）");
    },
    onError: (e) => push(`保存失败：${errMsg(e)}`),
  });

  return (
    <SectionBlock title="全局模型配置" subtitle="与「AI 回复引擎」同源 · 未绑定 Agent 的账号用这份">
      <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
        <Field label="API 地址（base_url）">
          <input
            value={cur("base_url")}
            onChange={(e) => set("base_url", e.target.value)}
            placeholder="http://127.0.0.1:31415/v1"
            style={inputStyle}
          />
        </Field>
        <Field label="主模型（model）">
          <input
            value={cur("model")}
            onChange={(e) => set("model", e.target.value)}
            placeholder="如 qwen2.5-7b-instruct"
            style={inputStyle}
          />
        </Field>
        <Field label="API Key">
          <input
            type="password"
            value={cur("api_key")}
            onChange={(e) => set("api_key", e.target.value)}
            placeholder="sk-...（本机服务可留空）"
            style={inputStyle}
          />
        </Field>
      </div>
      <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 12 }}>
        <button
          className="inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold transition-[background-color,color,border-color,box-shadow,transform,opacity] duration-200 ease-[var(--ease-spring)] cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-ring)] disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] bg-[var(--color-accent)] text-[#08130a] shadow-lg hover:bg-[var(--color-accent-hover)] h-9 px-4 text-[0.78rem] rounded-[10px]"
          onClick={() => saveMut.mutate()}
          disabled={saveMut.isPending || Object.keys(draft).length === 0}
        >
          {saveMut.isPending ? "保存中…" : "保存模型配置"}
        </button>
      </div>
      <div style={{ fontSize: 11, color: "var(--color-text-muted)", marginTop: 8 }}>
        与「AI 回复引擎」共享同一份全局配置；各 Agent 可单独覆盖主模型。
        IM 通知的指令解析模型在「通知与指令」。
      </div>
    </SectionBlock>
  );
}
