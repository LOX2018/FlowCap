/**
 * 通知设置 —— 本地小组件（Card / Field）
 *
 * ## 为什么独立（2026-09-15 大组件打散）
 *
 * 用户要求：「项目有多数单一大组件、单一大页面，全部划分层级打散后优化」。
 * `NotifySection.tsx`（666 行）内联了 Card（60 行）/ Field（32 行）两个
 * 可复用小组件，抽出后主体更聚焦业务逻辑。
 *
 * ## 说明
 * **纯搬移**——实现逐字节不变（含其内联样式与展开交互）。
 */
import { useState } from "react";

export function Card(props: {
  title: React.ReactNode;
  subtitle?: string;
  defaultOpen?: boolean;
  footer?: React.ReactNode;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(!!props.defaultOpen);
  return (
    <div
      style={{
        background: "var(--color-surface-solid)",
        borderRadius: 10,
        marginBottom: 10,
        overflow: "hidden",
        border: "1px solid var(--color-border)",
      }}
    >
      <div
        onClick={() => setOpen(!open)}
        style={{
          padding: "12px 14px",
          cursor: "pointer",
          display: "flex",
          alignItems: "center",
          justifyContent: "space-between",
          gap: 10,
        }}
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            flex: 1,
            minWidth: 0,
          }}
        >
          {props.title}
        </div>
        <span style={{ color: "var(--color-text-muted)", fontSize: 12 }}>
          {open ? "收起" : "展开"}
        </span>
      </div>
      {open && (
        <div style={{ padding: "0 14px 12px" }}>
          {props.subtitle && (
            <div
              style={{ fontSize: 12, color: "var(--color-text-muted)", marginBottom: 10 }}
            >
              {props.subtitle}
            </div>
          )}
          {props.children}
          {props.footer && <div style={{ marginTop: 12 }}>{props.footer}</div>}
        </div>
      )}
    </div>
  );
}

export function Field(props: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  secret?: boolean;
  hint?: string;
  placeholder?: string;
}) {
  return (
    <label style={{ display: "block", marginBottom: 10 }}>
      <div style={{ fontSize: 12, marginBottom: 4, color: "var(--color-text)" }}>
        {props.label}
        {props.secret && (
          <span style={{ color: "var(--color-text-muted)", marginLeft: 6 }}>（敏感）</span>
        )}
      </div>
      <input
        className="inp"
        type={props.secret ? "password" : "text"}
        value={props.value}
        placeholder={props.placeholder || ""}
        onChange={(e) => props.onChange(e.target.value)}
        style={{ width: "100%" }}
      />
      {props.hint && (
        <div style={{ fontSize: 11, color: "var(--color-text-muted)", marginTop: 3 }}>
          {props.hint}
        </div>
      )}
    </label>
  );
}
