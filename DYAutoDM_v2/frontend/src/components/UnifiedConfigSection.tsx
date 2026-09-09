/**
 * 统一配置区块（schema 驱动）
 *
 * 后端 services/app_config 下发每字段的 label/type/default/min/max/hint/apply，
 * 本组件据此**自动生成表单** —— 新增参数只需在后端 schema 加一项，
 * 前端零改动即可显示并可编辑，避免「每加一个参数就要手写一处 UI」的分散问题。
 *
 * 设计要点：
 *   - 按 section 分组，每组一个可收缩卡片 + 「保存此分组」按钮
 *   - 数值字段做 min/max 下限保护（越界值本地即拦，并在 UI 标出范围）
 *   - risk=true 的字段（风控敏感）用醒目样式标注
 *   - apply != hot 的字段保存后提示「需重启 X 生效」
 *   - 改动未保存时字段左侧显示圆点标记，避免用户忘记保存
 */
import { useState, useEffect, useCallback, useMemo } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { PageProps } from "../api/client";
import type {
  SettingsSchema,
  SettingsFieldSchema,
} from "../api/client";

type Val = string | number | boolean;

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

const APPLY_LABEL: Record<string, string> = {
  hot: "立即生效",
  restart_daemon: "需重启守护进程生效",
  restart_backend: "需重启后端生效",
};

/** 可收缩分组卡片 */
function SectionCard(props: {
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
    <div className="set-card">
      <div
        className={"set-card-head" + (open ? " is-open" : "")}
        onClick={() => setOpen(!open)}
      >
        <span
          style={{
            fontSize: 12,
            color: "var(--muted)",
            transition: "transform .15s",
            transform: open ? "rotate(90deg)" : "",
          }}
        >
          ▶
        </span>
        <span style={{ fontWeight: 700, fontSize: 13.5 }}>{props.title}</span>
        {props.dirty && (
          <span
            title="有未保存的改动"
            style={{
              width: 7,
              height: 7,
              borderRadius: "50%",
              background: "var(--accent)",
              display: "inline-block",
            }}
          />
        )}
        {props.subtitle && (
          <span style={{ fontSize: 12, color: "var(--muted)" }}>{props.subtitle}</span>
        )}
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 11, color: "var(--muted)" }}>{open ? "收起" : "展开"}</span>
      </div>
      {open && (
        <div className="set-card-body">
          <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>
            {props.children}
          </div>
          <div className="set-card-foot">
            <button className="btn sm" onClick={props.onReset} disabled={props.saving}>
              恢复默认
            </button>
            <button
              className="btn accent sm"
              onClick={props.onSave}
              disabled={props.saving || !props.dirty}
            >
              {props.saving ? "保存中…" : "保存此分组"}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

/** 单个字段：按 type 渲染 input / switch / select */
function SchemaField(props: {
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
      className={
        "set-field" +
        (s.risk ? " is-risk" : "") +
        (props.dirty ? " is-dirty" : "")
      }
    >
      <span style={{ color: "var(--muted)", fontSize: 11.5, display: "flex", gap: 6 }}>
        <span>{s.label}</span>
        {props.dirty && (
          <span style={{ color: "var(--accent)" }} title="已修改未保存">●</span>
        )}
        {s.risk && (
          <span style={{ color: "var(--warn, #d8962c)" }} title="风控敏感项">⚠</span>
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
            background: "var(--surface-2)",
            border: "1px solid var(--border)",
            borderRadius: 4,
            padding: "4px 6px",
            fontSize: 12,
            color: "var(--text)",
            outline: "none",
          }}
        >
          {s.options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
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
            background: "var(--surface-2)",
            border: `1px solid ${err ? "var(--danger, #c0392b)" : "var(--border)"}`,
            borderRadius: 4,
            padding: "4px 6px",
            fontSize: 12,
            color: "var(--text)",
            outline: "none",
          }}
        />
      )}

      {err ? (
        <span style={{ fontSize: 11, color: "var(--danger, #c0392b)", marginTop: 2 }}>
          {err}
        </span>
      ) : (
        <>
          {s.hint && (
            <span style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>
              {s.hint}
            </span>
          )}
          {(rangeText || s.apply) && (
            <span style={{ fontSize: 10.5, color: "var(--muted)", marginTop: 2, opacity: 0.85 }}>
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

export default function UnifiedConfigSection(
  props: PageProps & {
    scope?: string;
    scopeName?: string;
    /** 只渲染指定分区（设置页按功能拆 tab 用）；不传则显示全部 */
    onlySections?: string[];
  },
) {
  const { api, ready, push } = props;
  const qc = useQueryClient();
  // scope = 标签 id；为空则编辑全局配置。
  // 参数仍由后端 app_config 按 scope 隔离存储，此处只决定读写哪个 scope。
  const scope = props.scope || "";

  const q = useQuery({
    queryKey: ["unified-settings", scope],
    queryFn: async () => {
      const base = await api.getSettings();
      if (!scope) return base;
      // 标签模式：额外拉该标签已存的参数，覆盖进 config
      const sc = await api.getScoped(scope).catch(() => ({ ok: false, config: {} }));
      const cfg = { ...(base.config || {}) } as Record<
        string,
        Record<string, Val>
      >;
      for (const [sec, vals] of Object.entries(sc.config || {})) {
        cfg[sec] = { ...(cfg[sec] || {}), ...(vals as Record<string, Val>) };
      }
      return { ...base, config: cfg };
    },
    enabled: !!ready,
    staleTime: 10_000,
  });

  const schema: SettingsSchema = q.data?.schema || {};
  const serverCfg = (q.data?.config || {}) as Record<string, Record<string, Val>>;

  // 本地编辑态：{ section: { key: value } }
  const [draft, setDraft] = useState<Record<string, Record<string, Val>>>({});
  const [initDone, setInitDone] = useState(false);

  useEffect(() => {
    if (!q.data || initDone) return;
    const d: Record<string, Record<string, Val>> = {};
    for (const [sec, secSchema] of Object.entries(schema)) {
      d[sec] = {};
      for (const [k, f] of Object.entries(secSchema.fields)) {
        const cur = serverCfg?.[sec]?.[k];
        d[sec][k] = cur !== undefined && cur !== null ? cur : (f.default as Val);
      }
    }
    setDraft(d);
    setInitDone(true);
  }, [q.data, schema, serverCfg, initDone]);

  const setVal = useCallback((sec: string, key: string, v: Val) => {
    setDraft((prev) => ({ ...prev, [sec]: { ...prev[sec], [key]: v } }));
  }, []);

  const dirtyOf = useCallback(
    (sec: string) => {
      const cur = serverCfg?.[sec] || {};
      const d = draft[sec] || {};
      return Object.keys(d).some((k) => {
        const a = cur[k];
        const b = d[k];
        if (a === undefined || a === null) {
          return b !== (schema[sec]?.fields?.[k]?.default as Val);
        }
        return String(a) !== String(b);
      });
    },
    [draft, serverCfg, schema],
  );

  const saveMut = useMutation({
    mutationFn: async (sec: string) => {
      const body = { [sec]: draft[sec] || {} };
      if (scope) {
        // 标签模式：saveScoped 不返回 restart_required（标签参数多为 hot）
        const r = await api.saveScoped(scope, body);
        return { ...r, restart_required: [] as string[] };
      }
      return api.saveSettings(body);
    },
    onSuccess: (data, sec) => {
      qc.setQueryData(["unified-settings", scope], (old: unknown) => {
        const o = old as { ok: boolean; schema: SettingsSchema; config: unknown } | undefined;
        return o ? { ...o, config: data.config } : o;
      });
      const need = data.restart_required || [];
      if (need.length > 0) {
        push(
          `已保存「${schema[sec]?.label || sec}」。` +
            `以下组件需重启才生效：${need.join("、")}`,
        );
      } else {
        push(`已保存「${schema[sec]?.label || sec}」，立即生效`);
      }
    },
    onError: (e) => push(`保存失败：${errMsg(e)}`),
  });

  const resetMut = useMutation({
    mutationFn: (sec: string) => api.resetSettings([sec]),
    onSuccess: (data, sec) => {
      qc.setQueryData(["unified-settings", scope], (old: unknown) => {
        const o = old as { ok: boolean; schema: SettingsSchema; config: unknown } | undefined;
        return o ? { ...o, config: data.config } : o;
      });
      setInitDone(false); // 触发重新从服务端拉默认值
      push(`已恢复「${schema[sec]?.label || sec}」为默认值`);
    },
    onError: (e) => push(`恢复失败：${errMsg(e)}`),
  });

  const only = props.onlySections;
  const sections = useMemo(
    () =>
      Object.entries(schema)
        .filter(([key]) => !only || only.includes(key))
        .map(([key, s]) => ({
          key,
          label: s.label,
          fields: Object.entries(s.fields || {}),
        })),
    [schema, only],
  );

  if (q.isLoading) {
    return <div style={{ padding: 20, color: "var(--muted)" }}>加载配置…</div>;
  }
  if (q.isError) {
    return (
      <div style={{ padding: 20, color: "var(--danger, #c0392b)" }}>
        配置加载失败：{errMsg(q.error)}
      </div>
    );
  }

  return (
    <div>
      <div
        style={{
          fontSize: 12,
          color: "var(--muted)",
          marginBottom: 10,
          lineHeight: 1.6,
        }}
      >
        全站通用参数集中在此处管理。带 <b style={{ color: "var(--warn, #d8962c)" }}>⚠</b>{" "}
        的是风控敏感项，下限受保护；改动后请点「保存此分组」。
      </div>

      {sections.map((sec, i) => (
        <SectionCard
          key={sec.key}
          title={sec.label}
          subtitle={`${sec.fields.length} 项`}
          defaultOpen={i === 0}
          dirty={dirtyOf(sec.key)}
          saving={saveMut.isPending}
          onSave={() => saveMut.mutate(sec.key)}
          onReset={() => resetMut.mutate(sec.key)}
        >
          {sec.fields.map(([fk, fs]) => (
            <SchemaField
              key={fk}
              fieldKey={fk}
              schema={fs}
              value={draft[sec.key]?.[fk] ?? (fs.default as Val)}
              dirty={
                String(serverCfg?.[sec.key]?.[fk] ?? fs.default) !==
                String(draft[sec.key]?.[fk] ?? fs.default)
              }
              onChange={(v) => setVal(sec.key, fk, v)}
            />
          ))}
        </SectionCard>
      ))}
    </div>
  );
}
