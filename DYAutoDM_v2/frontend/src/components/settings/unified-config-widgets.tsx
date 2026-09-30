/**
 * 统一配置 —— 渲染小件（SectionCard / SchemaField）
 *
 * ## 为什么独立（2026-09-15 大组件打散）
 * 原 `UnifiedConfigSection.tsx`（506 行）内联 SectionCard(54) + SchemaField(149)。
 * 二者是纯渲染件（schema 驱动），抽出后主组件只保留数据加载与保存编排。
 * **纯搬移**——字段映射、控件、保存回调逐字节不变。
 */
import { useCallback, useState } from "react";
import { SetCard, SetCardHead, SetCardBody, SetCardFoot } from "@/components/page/set-card";
import type { SettingsFieldSchema } from "../../api/client";

export type Val = string | number | boolean;

export function SectionCard(props: {
  title: string;
  subtitle?: string;
  dirty: boolean;
  saving: boolean;
  onSave: () => void;
  onReset: () => void;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(!!props.defaultOpen);
  return (
    <SetCard>
      <SetCardHead
        open={open}
        onToggle={() => setOpen(!open)}
        title={
          <>
            {props.title}
            {props.dirty && (
              <span
                title="有未保存的改动"
                className="ml-2 inline-block h-[7px] w-[7px] rounded-full
                           bg-[var(--color-accent)] align-middle"
              />
            )}
          </>
        }
        description={props.subtitle}
      />
      {open && (
        <SetCardBody>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
            {props.children}
          </div>
          <SetCardFoot>
            <button className="inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold transition-[background-color,color,border-color,box-shadow,transform,opacity] duration-200 ease-[var(--ease-spring)] cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-ring)] disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] bg-[var(--color-surface-raised)] text-[var(--color-text)] shadow-[inset_0_0_0_1px_var(--color-border)] hover:bg-[var(--color-surface-solid)] h-9 px-4 text-[0.78rem] rounded-[10px]" onClick={props.onReset} disabled={props.saving}>
              恢复默认
            </button>
            <button
              className="inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold transition-[background-color,color,border-color,box-shadow,transform,opacity] duration-200 ease-[var(--ease-spring)] cursor-pointer select-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-ring)] disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] bg-[var(--color-accent)] text-[#08130a] shadow-lg hover:bg-[var(--color-accent-hover)] h-9 px-4 text-[0.78rem] rounded-[10px]"
              onClick={props.onSave}
              disabled={props.saving || !props.dirty}
            >
              {props.saving ? "保存中…" : "保存此分组"}
            </button>
          </SetCardFoot>
        </SetCardBody>
      )}
    </SetCard>
  );
}

/** 单个字段：按 type 渲染 input / switch / select */
export function SchemaField(props: {
  fieldKey: string;
  schema: SettingsFieldSchema;
  value: Val;
  dirty: boolean;
  onChange: (v: Val) => void;
}) {
  const { schema: s, value } = props;
  const [err, setErr] = useState("");

  const commit = useCallback(
    (raw: string) => {
      if (s.type === "int" || s.type === "float") {
        const n = Number(raw);
        if (raw.trim() === "" || Number.isNaN(n)) {
          setErr("请输入数字");
          return;
        }
        if (s.min != null && n < s.min) {
          setErr(`不得小于 ${s.min}`);
          return;
        }
        if (s.max != null && n > s.max) {
          setErr(`不得大于 ${s.max}`);
          return;
        }
        setErr("");
        props.onChange(s.type === "int" ? Math.round(n) : n);
        return;
      }
      setErr("");
      props.onChange(raw);
    },
    [s, props.onChange],
  );

  const rangeText =
    s.type === "int" || s.type === "float"
      ? `范围 ${s.min ?? "-∞"} ~ ${s.max ?? "+∞"}`
      : "";

  return (
    <div
      className={"set-field" + (s.risk ? " is-risk" : "") + (props.dirty ? " is-dirty" : "")}
      /**
       * ★ 2026-09-30 根因修复（用户报障「配置中心副标题/输入框异常夸张、页面分布不合理」）：
       *  `.set-field` 的 CSS 定义在 v0.43.16「旧 CSS 体系下线」时被删除，本组件仍在
       *  引用该**死类名** ⇒ 字段丢失 `flex: 1 0 220px; min-width: 0`。父容器是
       *  `display:flex; flex-wrap:wrap`，flex 项随即按 **max-content** 定宽；中文**无空格**
       *  ⇒ 一行 hint 就是一个不可断「单词」⇒ max-content = 整条 hint 长度 ⇒ 字段被撑到
       *  极宽、内部 `width:100%` 的 input 同步被撑爆、整页分布崩坏。
       *  此处把关键布局内联化（不再依赖已删的 CSS），并把 hint 限制在字段宽度内换行。
       */
      style={{ flex: "1 1 240px", minWidth: 0, maxWidth: "100%" }}
    >
      <span style={{ color: "var(--color-text-muted)", fontSize: 11.5, display: "flex", gap: 6 }}>
        <span>{s.label}</span>
        {props.dirty && (
          <span style={{ color: "var(--color-accent)" }} title="已修改未保存">●</span>
        )}
        {s.risk && (
          <span style={{ color: "var(--color-warning)" }} title="风控敏感项">⚠</span>
        )}
      </span>

      {s.type === "bool" ? (
        <label className="switch" style={{ marginTop: 2 }}>
          <input
            type="checkbox"
            checked={Boolean(value)}
            onChange={(e) => props.onChange(e.target.checked)}
          />
          <i />
        </label>
      ) : s.options && s.options.length > 0 ? (
        <select
          value={String(value)}
          onChange={(e) => props.onChange(e.target.value)}
          style={{
            width: "100%",
            background: "var(--color-surface-raised)",
            border: "1px solid var(--color-border)",
            borderRadius: 4,
            padding: "4px 6px",
            fontSize: 12,
            color: "var(--color-text)",
            outline: "none",
          }}
        >
          {s.options.map((o) => {
            // 后端 schema 的 options 当前是字符串数组（如 ["native"]），
            // 也兼容 { value, label } 对象数组；两种形状都要能正确渲染出文本。
            const val = typeof o === "string" ? o : o.value;
            const lbl = typeof o === "string" ? o : o.label;
            return (
              <option key={val} value={val}>
                {lbl}
              </option>
            );
          })}
        </select>
      ) : (
        <input
          className="mono"
          type={s.type === "int" || s.type === "float" ? "number" : "text"}
          value={String(value ?? "")}
          min={s.min}
          max={s.max}
          onChange={(e) => {
            // 允许中途空串（用户正在输入），失焦时才校验
            setErr("");
            if (e.target.value === "") {
              props.onChange("");
              return;
            }
            commit(e.target.value);
          }}
          onBlur={(e) => commit(e.target.value)}
          style={{
            width: "100%",
            background: "var(--color-surface-raised)",
            border: `1px solid ${err ? "var(--danger, #c0392b)" : "var(--color-border)"}`,
            borderRadius: 4,
            padding: "4px 6px",
            fontSize: 12,
            color: "var(--color-text)",
            outline: "none",
          }}
        />
      )}

      {err ? (
        <span style={{ fontSize: 11, color: "var(--color-danger)", marginTop: 2 }}>
          {err}
        </span>
      ) : (
        <>
          {s.hint && (
            <span style={{ fontSize: 11, lineHeight: 1.5, overflowWrap: "anywhere", color: "var(--color-text-muted)", marginTop: 2 }}>
              {s.hint}
            </span>
          )}
          {(rangeText || s.apply) && (
            <span style={{ fontSize: 10.5, color: "var(--color-text-muted)", marginTop: 2, opacity: 0.85 }}>
              {[rangeText, s.apply ? APPLY_LABEL[s.apply] || s.apply : ""]
                .filter(Boolean)
                .join(" · ")}
            </span>
          )}
        </>
      )}
    </div>
  );
}

export const APPLY_LABEL: Record<string, string> = {
  hot: "立即生效",
  restart_daemon: "需重启守护进程生效",
  restart_backend: "需重启后端生效",
};
