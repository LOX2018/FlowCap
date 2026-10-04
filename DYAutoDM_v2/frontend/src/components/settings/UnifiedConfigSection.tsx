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
import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { PageProps } from "../../api/client";
import type {
  SettingsSchema,
  SettingsFieldSchema,
} from "../../api/client";
import { Button } from "@/components/ui/button";
import { errMsg } from "./settings-shared";
import { type Val, SectionCard, SchemaField } from "./unified-config-widgets";
import { TAG_MANAGED_SECTIONS } from "../../api/client";



/** 可收缩分组卡片 */

/** 单个字段：按 type 渲染 input / switch / select */

export default function UnifiedConfigSection(
  props: PageProps & {
    scope?: string;
    scopeName?: string;
    /** 只渲染指定分区（设置页按功能拆 tab 用）；不传则显示全部 */
    onlySections?: string[];
    /**
     * 字段分组（2026-10-04：把某些字段拆成「单独子卡片」）。
     * 在同一实例内渲染 —— 共享 scope / draft / 保存按钮语义，
     * 因此标签切换栏只有一条、不会出现「两张卡各存各的 scope」的脱节。
     * 未列入任何分组的字段渲染在主卡里。
     */
    fieldGroups?: Array<{ title: string; fields: string[] }>;
    /** 隐藏顶部标签切换栏（被 TagSection 内嵌时，外层已决定 scope） */
    hideScopeBar?: boolean;
  },
) {
  const { api, ready, push } = props;
  const qc = useQueryClient();

  // ---- v0.38.3：顶部标签切换栏状态（**仅标签管辖的板块**才有）----
  // 选「全局」= 编辑全局参数；选某标签 = 编辑该标签的参数。
  // 删除标签只能在「配置标签」页做，这里只切换，不提供删除。
  //
  // ⚠️ 2026-10-02 根因修复（用户报障「系统里面不需要配置标签」）：
  // 原判据是 `!hideScopeBar && (!onlySections || !onlySections.includes("general"))`
  // —— 实质等于「**只要不是通用配置就显示**」⇒ 系统 / 私信 / 通知 / AI 等
  // 与标签毫无关系的 tab 也弹出了「保存到 全局/标签」栏，且该栏能点、
  // 点了会把这些**非托管分区**的参数写进 `app_config::<tag_id>`（后端不认
  // 托管校验）⇒ 既是无意义入口，又是**参数写到错误作用域**的隐患。
  //
  // 现改为**白名单判据**：与后端 `services/config_tag.MANAGED_SECTIONS` 同源
  // （前端常量 `TAG_MANAGED_SECTIONS`），只有 send/live/capture/crawl/
  // live_orchestration 五个板块才认标签作用域。
  const only = props.onlySections;
  const isBusiness =
    !props.hideScopeBar &&
    (only ? only.some((s) => (TAG_MANAGED_SECTIONS as readonly string[]).includes(s))
          : true);
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
  // 2026-09-17 修补（OCR 审查 HIGH —— init 死锁）：原实现用一个布尔 state
  // `initDone` 作"只初始化一次"的锁，一旦置 true 便在本挂载实例内永不再初始化。
  // 而 TagSection.tsx 是「常驻挂载 + 只换 scope prop」，于是：
  //   选标签 A → draft 初始化为 A → 再选标签 B → q.data 已换但 initDone 仍 true
  //   → draft 保持 A 的值 → 点保存会把 **A 的配置写进 B**。
  // 现改为「数据来源 key」（scope + 数据代次）判定，见下方 effect。
  const initKeyRef = useRef<string>("");
  const initKey = `${scope}|${q.dataUpdatedAt}`;
  useEffect(() => {
    if (!q.data) return;
    if (initKeyRef.current === initKey) return;
    const d: Record<string, Record<string, Val>> = {};
    for (const [sec, secSchema] of Object.entries(schema)) {
      d[sec] = {};
      for (const [k, f] of Object.entries(secSchema.fields)) {
        const cur = serverCfg?.[sec]?.[k];
        d[sec][k] = cur !== undefined && cur !== null ? cur : (f.default as Val);
      }
    }
    initKeyRef.current = initKey;
    setDraft(d);
  }, [q.data, q.dataUpdatedAt, schema, serverCfg, initKey, scope]);

  const setVal = useCallback((sec: string, key: string, v: Val) => {
    setDraft((prev) => ({ ...prev, [sec]: { ...prev[sec], [key]: v } }));
  }, []);

  /** 字段级 dirty（子卡片用）：只看指定字段，避免同分区其他字段变动点亮子卡。 */
  const dirtyOfFields = useCallback(
    (sec: string, fieldKeys: string[]) => {
      const cur = serverCfg?.[sec] || {};
      const d = draft[sec] || {};
      return fieldKeys.some((k) => {
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
    // 2026-09-17 修补（OCR 审查 HIGH）：scope 改为**随参数传递**。
    // 原实现 onSuccess 里读渲染闭包中的 `scope`；在「保存 → 立刻切换
    // 标签/全局」的连点场景下，闭包可能是切换后的新值（或旧值），
    // 导致 setQueryData 把结果写进**错的缓存键**，造成配置串位。
    // 现在把提交那一刻的 scope 随 mutation 一起传下去，onSuccess 用 args。
    mutationFn: async (args: { sec: string; scope: string }) => {
      const { sec, scope: sc } = args;
      const body = { [sec]: draft[sec] || {} };
      if (sc) {
        // 标签模式：saveScoped 不返回 restart_required（标签参数多为 hot）
        const r = await api.saveScoped(sc, body);
        return { ...r, restart_required: [] as string[] };
      }
      return api.saveSettings(body);
    },
    onSuccess: (data, args) => {
      const { sec, scope: sc } = args;
      qc.setQueryData(["unified-settings", sc], (old: unknown) => {
        const o = old as { ok: boolean; schema: SettingsSchema; config: unknown } | undefined;
        return o ? { ...o, config: data.config } : o;
      });
      const need = data.restart_required || [];
      const who = sc ? `标签「${scopeName}」` : "全局";
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
    // 2026-10-04 修复（拆子卡片的作用域错配，真缺陷）：
    //   原实现 `resetMut.mutate(unit.sec)` 只传 section ⇒ 后端 `data.pop(section)`
    //   清掉**整个分区**。拆卡前「一张卡=一个分区」所以正确；拆卡后子卡与主卡
    //   共享同一 section ⇒ 在「私信词库」子卡点「恢复默认」会连带清空整个 send
    //   分区（全部风控/闸门/额度）。
    //   现在传 `fields` = **本卡渲染的字段**，只清本卡作用域。
    //   同时补 `scope` —— 原实现漏传，选中标签时会误重置**全局**（与保存路径不对称）。
    mutationFn: (args: { sec: string; fields: string[]; scope: string }) =>
      api.resetSettings([args.sec], { scope: args.scope, fields: args.fields }),
    onSuccess: (data, args) => {
      const { sec, scope: sc } = args;
      qc.setQueryData(["unified-settings", sc], (old: unknown) => {
        const o = old as { ok: boolean; schema: SettingsSchema; config: unknown } | undefined;
        return o ? { ...o, config: data.config } : o;
      });
      // 2026-09-17：原为 setInitDone(false)。改用 initKey 机制后，
      // 重置 initKeyRef 即等价于"下次 effect 重新初始化"。
      initKeyRef.current = "";
      push(`已恢复「${schema[sec]?.label || sec}」为默认值`);
    },
    onError: (e) => push(`恢复失败：${errMsg(e)}`),
  });

  // 分区 → 渲染单元列表。每项自带 `title` 与 `fields`：
  //   · 未配 fieldGroups ⇒ 整分区一张卡（原行为，零回归）；
  //   · 配了 ⇒ 每个分组一张子卡 + 剩余字段一张主卡（同一实例内，共享 scope/draft）。
  const sections = useMemo(() => {
    const groups = props.fieldGroups || [];
    const grouped = new Set(groups.flatMap((g) => g.fields));
    const out: Array<{
      cardKey: string;
      sec: string;
      title: string;
      fields: Array<[string, SettingsFieldSchema]>;
    }> = [];
    for (const [key, s] of Object.entries(schema)) {
      if (only && !only.includes(key)) continue;
      const all = Object.entries(s.fields || {}) as Array<[string, SettingsFieldSchema]>;
      if (!groups.length) {
        out.push({ cardKey: key, sec: key, title: s.label, fields: all });
        continue;
      }
      // 主卡：未被任何分组接管的字段（先渲染，保证「主配置在上、子卡在下」）
      const rest = all.filter(([fk]) => !grouped.has(fk));
      if (rest.length) {
        out.push({ cardKey: key, sec: key, title: s.label, fields: rest });
      }
      // 子卡片（按 fieldGroups 顺序）
      for (const g of groups) {
        const fs = all.filter(([fk]) => g.fields.includes(fk));
        if (fs.length) {
          out.push({ cardKey: `${key}:${g.title}`, sec: key, title: g.title, fields: fs });
        }
      }
    }
    return out;
  }, [schema, only, props.fieldGroups]);

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
                    <Button
                      variant={scope === "" ? "default" : "ghost"}
                      size="sm"
                      onClick={() => setActiveTag("")}
                    >
                      全局
                    </Button>
                    {tagList.map((t) => (
                      <Button
                        key={t.id}
                        variant={scope === t.id ? "default" : "ghost"}
                        size="sm"
                        onClick={() => setActiveTag(t.id)}
                        title={`编辑标签「${t.name}」的参数`}
                      >
                        {t.name}
                      </Button>
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
            <>参数保存到标签「{scopeName}」（仅影响绑定该标签的账号）。</>
          ) : (
            <>参数保存到全局（对未绑定标签的账号生效）。</>
          )}
        </div>
      )}

      {sections.map((unit, i) => (
        <SectionCard
          key={unit.cardKey}
          title={unit.title}
          subtitle={`${unit.fields.length} 项`}
          defaultOpen={props.onlySections ? i === 0 : true}
          dirty={dirtyOfFields(unit.sec, unit.fields.map(([fk]) => fk))}
          saving={saveMut.isPending}
          onSave={() => saveMut.mutate({ sec: unit.sec, scope: scope || "" })}
          onReset={() =>
            resetMut.mutate({
              sec: unit.sec,
              // 只清**本卡渲染的字段**：子卡与主卡共享同一 section，
              // 不限定范围会连带清空同分区其他字段（2026-10-04 修复）。
              fields: unit.fields.map(([fk]) => fk),
              scope: scope || "",
            })
          }
        >
          {unit.fields.map(([fk, fs]) => (
            <SchemaField
              key={fk}
              fieldKey={fk}
              schema={fs}
              value={draft[unit.sec]?.[fk] ?? (fs.default as Val)}
              dirty={
                String(serverCfg?.[unit.sec]?.[fk] ?? fs.default) !==
                String(draft[unit.sec]?.[fk] ?? fs.default)
              }
              onChange={(v) => setVal(unit.sec, fk, v)}
            />
          ))}
          {/* 子卡片提示：本卡是某分区的一部分，保存作用于整个分区。 */}
          {props.fieldGroups?.some((g) => g.title === unit.title) && (
            <div className="mt-1 text-[0.7rem] text-[var(--color-text-muted)]">
              本卡与「{schema[unit.sec]?.label || unit.sec}」其余项同属一个分区，保存会一并提交。
            </div>
          )}
        </SectionCard>
      ))}
    </div>
  );
}
