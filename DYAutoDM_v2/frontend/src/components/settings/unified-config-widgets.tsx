/**
 * 统一配置 —— 渲染小件（SectionCard / SchemaField）
 *
 * ## 为什么独立（2026-09-15 大组件打散）
 * 原 `UnifiedConfigSection.tsx`（506 行）内联 SectionCard(54) + SchemaField(149)。
 * 二者是纯渲染件（schema 驱动），抽出后主组件只保留数据加载与保存编排。
 * **纯搬移**——字段映射、控件、保存回调逐字节不变。
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { SetCard, SetCardHead, SetCardBody, SetCardFoot } from "@/components/page/set-card";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from "@/components/ui/select";
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
                      <Button variant="secondary" onClick={props.onReset} disabled={props.saving}>
                        恢复默认
                      </Button>
                      <Button onClick={props.onSave} disabled={props.saving || !props.dirty}>
                        {props.saving ? "保存中…" : "保存此分组"}
                      </Button>
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
  const { schema: s, value, fieldKey } = props;
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

  // 词库类字段（换行分隔的多行文本）：渲染为「行列表 + 添加/删除」，
  // 而不是单个输入框配一行「每行一条」的注释 —— 见 POOL_FIELD_KEYS。
  // 保留 label 外壳（dirty 圆点 / risk 标记），只换掉控件本体。
  if (POOL_FIELD_KEYS.has(fieldKey) && !s.options?.length) {
    return (
      <div
        className={
          "set-field" + (s.risk ? " is-risk" : "") + (props.dirty ? " is-dirty" : "")
        }
        style={{ flex: "1 1 240px", minWidth: 0, maxWidth: "100%" }}
      >
        <span className="mb-1 flex items-center gap-1.5 text-[0.7rem] text-[var(--color-text-muted)]">
          <span className="truncate">{s.label}</span>
          {props.dirty && (
            <span className="text-[var(--color-accent)]" title="已修改未保存">
              ●
            </span>
          )}
          {s.risk && (
            <span className="text-[var(--color-warning)]" title="风控敏感项">
              ⚠
            </span>
          )}
        </span>
        <PoolField
          value={value as string}
          onChange={(v) => props.onChange(v)}
        />
      </div>
    );
  }

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
      <span className="mb-1 flex items-center gap-1.5 text-[0.7rem] text-[var(--color-text-muted)]">
              <span className="truncate">{s.label}</span>
              {props.dirty && (
                <span className="text-[var(--color-accent)]" title="已修改未保存">●</span>
              )}
              {s.risk && (
                <span className="text-[var(--color-warning)]" title="风控敏感项">⚠</span>
              )}
            </span>

      {s.type === "bool" ? (
        <div className="flex h-9 items-center">
          <Switch checked={Boolean(value)} onCheckedChange={(c) => props.onChange(c)} />
        </div>
      ) : s.options && s.options.length > 0 ? (
        <Select value={String(value)} onValueChange={(v) => props.onChange(v)}>
          <SelectTrigger className="text-[0.78rem]">
            <SelectValue />
          </SelectTrigger>
                <SelectContent>
                  {s.options.map((o) => {
                    // 后端 schema 的 options 当前是字符串数组（如 ["native"]），
                    // 也兼容 { value, label } 对象数组；两种形状都要能正确渲染出文本。
                    const val = typeof o === "string" ? o : o.value;
                    const lbl = typeof o === "string" ? o : o.label;
                    return (
                      <SelectItem key={val} value={val}>
                        {lbl}
                      </SelectItem>
                    );
                  })}
                </SelectContent>
              </Select>
            ) : (
              <Input
                className={
                  "px-2.5 font-mono text-[0.78rem]" +
                  (err ? " border-[var(--color-danger)]" : "")
                }
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

/**
 * 词库类字段 —— 存储为**换行分隔字符串**（与 automation 的 *_keywords 同范式）。
 *
 * 为何不直接给一个 textarea：这些字段的 label 曾标注「（每行一条）」、hint 又写一遍
 * 「每行一条」，但控件是**单个单行输入框** —— 用户要输 5 条文案得删/粘贴 5 次，
 * 而标注本身也纯属多余（控件根本不是一行一条）。
 * 现改为**行列表**：每行独立输入 + 删除，底部「添加一行」。
 *
 * 存储格式不变（仍为 \n 分隔），因此**后端零改动**：
 *   · backend/api/crawl.py 本就把 CR 去掉后按 LF 切分并 trim；
 *   · 后端 `test_crawl_panel_fixes.py` 已覆盖「每行一条 ⇒ 取首条」的确定性语义。
 *
 * ⚠️ 加字段前先确认它是「条目列表」而非「一个长字符串」—— 否则会把语义改坏。
 */
export const POOL_FIELD_KEYS: ReadonlySet<string> = new Set(["danmaku_pool", "dm_pool"]);

/** 行列表编辑器：编辑换行分隔的字符串值。 */
export function PoolField(props: { value: string; onChange: (v: string) => void }) {
  const { value, onChange } = props;
  const [rows, setRows] = useState<string[]>(() => (value ? value.split("\n") : []));
  // ext=false 表示「上一次 value 变化是本地编辑造成的回声」⇒ effect 不重置行，
  // 否则输入中的焦点/光标会丢失。外部变更（切标签 / 恢复默认）走 ext=true 分支。
  const ext = useRef(true);

  useEffect(() => {
    if (!ext.current) return;
    setRows(value ? value.split("\n") : []);
    ext.current = false;
  }, [value]);

  const sync = (next: string[]) => {
    setRows(next);
    ext.current = false;
    onChange(next.join("\n"));
  };

  const add = () => sync([...rows, ""]);
  const del = (i: number) => {
    const next = rows.slice();
    next.splice(i, 1);
    sync(next);
  };

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, width: "100%" }}>
      {rows.length === 0 ? (
        <span style={{ fontSize: 11, color: "var(--color-text-muted)" }}>
          暂无文案，点击下方「添加一行」
        </span>
      ) : (
        rows.map((r, i) => (
          <div key={i} style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span
              style={
                {
                  width: 16,
                  flexShrink: 0,
                  textAlign: "center",
                  fontSize: 10.5,
                  color: "var(--color-text-muted)",
                  opacity: 0.75,
                } as React.CSSProperties
              }
            >
              {i + 1}
            </span>
            <Input
              className="flex-1 px-2.5 font-mono text-[0.78rem]"
              value={r}
              placeholder={`第 ${i + 1} 条`}
              onChange={(e) => {
                const next = rows.slice();
                next[i] = e.target.value;
                sync(next);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  add();
                }
              }}
            />
            <Button
              variant="ghost"
              size="sm"
              title="删除该行"
              aria-label="删除该行"
              onClick={() => del(i)}
              style={{ width: 28, flexShrink: 0, padding: 0 }}
            >
              ✕
            </Button>
          </div>
        ))
      )}
      <div>
        <Button variant="secondary" size="sm" onClick={add} className="text-[0.72rem]">
          + 添加一行
        </Button>
      </div>
    </div>
  );
}
