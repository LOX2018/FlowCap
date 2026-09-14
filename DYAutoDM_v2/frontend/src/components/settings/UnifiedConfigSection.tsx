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
import { PageProps } from "../../api/client";
import type {
  SettingsSchema,
  SettingsFieldSchema,
} from "../../api/client";
import { SetCard, SetCardHead, SetCardBody, SetCardFoot } from "@/components/page/set-card";

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
      <span style={{ color: "var(--color-text-muted)", fontSize: 11.5, display: "flex", gap: 6 }}>
        <span>{s.label}</span>
        {props.dirty && (
          <span style={{ color: "var(--color-accent)" }} title="已修改未保存">●</span>
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
        <span style={{ fontSize: 11, color: "var(--danger, #c0392b)", marginTop: 2 }}>
          {err}
        </span>
      ) : (
        <>
          {s.hint && (
            <span style={{ fontSize: 11, color: "var(--color-text-muted)", marginTop: 2 }}>
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

export default function UnifiedConfigSection(
  props: PageProps & {
    scope?: string;
    scopeName?: string;
    /** 只渲染指定分区（设置页按功能拆 tab 用）；不传则显示全部 */
    onlySections?: string[];
    /** 隐藏顶部标签切换栏（被 TagSection 内嵌时，外层已决定 scope） */
    hideScopeBar?: boolean;
  },
) {
  const { api, ready, push } = props;
  const qc = useQueryClient();

  // ---- v0.38.3：顶部标签切换栏状态（业务分区才有）----
  // 选「全局」= 编辑全局参数；选某标签 = 编辑该标签的参数。
  // 删除标签只能在「配置标签」页做，这里只切换，不提供删除。
  const isBusiness =
    !props.hideScopeBar &&
    (!props.onlySections || !props.onlySections.includes("general"));
  const [activeTag, setActiveTag] = useState<string>("");

  const tagsQ = useQuery({
    queryKey: ["tag-switcher"],
    queryFn: () => api.listTags(),
    enabled: !!ready && isBusiness,
    staleTime: 30_000,
  });
  const tagList = (tagsQ.data?.tags || []) as {
    id: string; name: string; field_count: number;
  }[];

  // 当前生效的 scope：外部传入优先（标签页内嵌时用），否则用顶部选择
  const scope = props.scope !== undefined ? props.scope : activeTag;

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
      const who = scope ? `标签「${scopeName}」` : "全局";
      if (need.length > 0) {
        push(
          `已保存到${who}：「${schema[sec]?.label || sec}」。` +
            `以下组件需重启才生效：${need.join("、")}`,
        );
      } else {
        push(`已保存到${who}：「${schema[sec]?.label || sec}」，立即生效`);
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
    return <div style={{ padding: 20, color: "var(--color-text-muted)" }}>加载配置…</div>;
  }
  if (q.isError) {
    return (
      <div style={{ padding: 20, color: "var(--danger, #c0392b)" }}>
        配置加载失败：{errMsg(q.error)}
      </div>
    );
  }

  const scopeName = scope
    ? tagList.find((t) => t.id === scope)?.name || props.scopeName || "标签"
    : "全局";

  return (
    <div>
      {/* 顶部标签切换栏：决定下面参数保存到哪里（全局 / 某标签） */}
      {isBusiness && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 6,
            flexWrap: "wrap",
            padding: "8px 10px",
            marginBottom: 12,
            background: "var(--color-surface-solid)",
            border: "1px solid var(--color-border-strong)",
            borderRadius: 10,
          }}
        >
          <span style={{ fontSize: 11.5, color: "var(--color-text-muted)", marginRight: 2 }}>
            保存到
          </span>
          <button
            className={"btn sm" + (scope === "" ? " accent" : " ghost")}
            onClick={() => setActiveTag("")}
          >
            全局
          </button>
          {tagList.map((t) => (
            <button
              key={t.id}
              className={"btn sm" + (scope === t.id ? " accent" : " ghost")}
              onClick={() => setActiveTag(t.id)}
              title={`编辑标签「${t.name}」的参数`}
            >
              {t.name}
            </button>
          ))}
          <div style={{ flex: 1 }} />
          <span
            style={{
              fontSize: 11.5,
              padding: "2px 8px",
              borderRadius: 999,
              background: scope ? "var(--color-accent-soft)" : "var(--color-surface-raised)",
              color: scope ? "var(--color-accent)" : "var(--color-text-muted)",
              border: "1px solid var(--color-border)",
            }}
          >
            当前：{scopeName}
          </span>
        </div>
      )}

      {isBusiness && (
        <div
          style={{
            fontSize: 11.5,
            color: "var(--color-text-muted)",
            marginBottom: 10,
            lineHeight: 1.6,
          }}
        >
          {scope ? (
            <>
              正在编辑标签「{scopeName}」的参数 —— 只影响
              <b>在「配置标签」页绑定了该标签的账号</b>。
              标签的新建与删除在配置中心「配置标签」区。
            </>
          ) : (
            <>
              正在编辑<b>全局</b>参数 —— 对未绑定标签的账号生效。
              若某账号绑定了标签，则以标签值为准。
            </>
          )}
        </div>
      )}

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
