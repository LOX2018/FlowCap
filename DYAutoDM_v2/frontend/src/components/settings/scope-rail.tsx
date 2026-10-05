/**
 * 配置作用域选择栏（tab 级，竖向一柱）
 *
 * ## 为什么从组件内部提升出来（2026-10-04 用户指令）
 *
 * 原实现在**每个 `UnifiedConfigSection` 实例内部**各渲染一条横向「保存到
 * 全局/标签」切换栏。同一个 tab 挂 2 个实例时（「监听策略」tab = 监听主卡 +
 * 弹幕文案库子卡，另有 HVK 自带一条）⇒ 一页出现 **2~3 条重复的标签栏**，
 * 每个策略项各自选一列，切换一次要点对齐好几处。
 *
 * 现提升为**每个 tab 一柱**：本组件渲染在配置内容右侧，`scope` 由 settings-page
 * 持有并下发给该 tab 下的所有子组件。同一 tab 内只有一个「当前作用域」概念。
 *
 * ## 只在真正支持标签覆盖的 tab 显示
 *
 * 判据与后端 `services/config_tag.MANAGED_SECTIONS` 同源（前端
 * `TAG_MANAGED_SECTIONS`）。**不在**这些 tab 放本栏 —— 在非托管 tab 选标签
 * 只会把参数写进 `app_config::<tag_id>` 而被后端忽略（写入成功但永不生效），
 * 即「看着能选、实际无效」的误导入口（2026-10-02 已修过同类缺陷）。
 *
 * ⚠️ 注意「AI 回复引擎」tab **不在**这个白名单里 —— 虽然它显示的是 live_orchestration
 * 参数，但 ModelHubSection / AiEngineSection 走 Agent 自己的存储，不读标签覆盖。
 * settings-page 据此不为该 tab 渲染本栏。
 *
 * ## 记住上次选择（localStorage）
 *
 * tab 切换会卸载本组件，若 scope 不持久化，每次切回都重置为「全局」——用户可能
 * 在全局作用域里误存本该属于某标签的参数。故记住上次选择，并做两重保护：
 *   ① 标签已被删除 ⇒ 回落「全局」（客户端过滤，后端对未知 id 会明确 404）；
 *   ② 按 `settings.scopebar.v1` 命名，命名空间 `dy:*` 与既有的 MEMBER_TOKEN_KEY 一致。
 */
import { useQuery } from "@tanstack/react-query";
import { Tags } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api } from "../../api/client";

const LS_KEY = "dy:settings.scopebar.v1";

/**
 * 读上次记住的 scope（"" = 无记住值 / 读不到）。
 * settings-page 用它初始化本页 scope 状态 —— 因为 scope 现在挂在**页面层**
 * （跨 tab 共用），若每个 tab 各自读 localStorage 初始化，切 tab 会与已挂载
 * 组件里的当前值打架。
 */
export function readRememberedScope(): string {
  try {
    return localStorage.getItem(LS_KEY) || "";
  } catch {
    return "";
  }
}

/** 记住当前 scope（供 ScopeRail 写入；与上面的读配合）。 */
export function rememberScope(scope: string): void {
  try {
    localStorage.setItem(LS_KEY, scope);
  } catch {
    /* localStorage 不可用（隐私模式）时静默降级：栏仍可用，只是不记住 */
  }
}

export type ScopeBarProps = {
  /** 当前 scope："" = 全局，非空 = 标签 id */
  scope: string;
  onScopeChange: (scope: string) => void;
  /** 标签 id → 名称（可选；缺省时用 label 查询回填） */
  scopeName?: string;
  ready?: boolean;
};

export function ScopeRail({
  scope,
  onScopeChange,
  scopeName: scopeNameProp,
  ready,
}: ScopeBarProps) {
  const tagsQ = useQuery({
    queryKey: ["tag-switcher"],
    queryFn: () => api.listTags(),
    enabled: ready !== false,
    staleTime: 30_000,
  });
  const tagList = (tagsQ.data?.tags || []) as {
    id: string;
    name: string;
  }[];

  // 记住值的**有效性**校验放在 settings-page（scope 的真值所在处）：
  // 列表回来时若 id 已不存在就回落「全局」。本组件只做展示与切换。

  return (
    <aside
      className="shrink-0 self-start sticky top-0"
      style={{
        width: 152,
        display: "flex",
        flexDirection: "column",
        gap: 6,
        padding: 10,
        background: "var(--color-surface-solid)",
        border: "1px solid var(--color-border-strong)",
        borderRadius: 10,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 5,
          fontSize: 11,
          fontWeight: 600,
          color: "var(--color-text-muted)",
          letterSpacing: 0.3,
        }}
      >
        <Tags className="h-3 w-3" />
        保存到
      </div>

      <Button
        variant={scope === "" ? "default" : "ghost"}
        size="sm"
        onClick={() => onScopeChange("")}
        title="编辑全局参数（对未绑定标签的账号生效）"
        style={{ justifyContent: "flex-start", width: "100%", textAlign: "left" }}
      >
        全局
      </Button>

      {tagList.map((t) => (
        <Button
          key={t.id}
          variant={scope === t.id ? "default" : "ghost"}
          size="sm"
          onClick={() => onScopeChange(t.id)}
          title={`编辑标签「${t.name}」的参数`}
          style={{
            justifyContent: "flex-start",
            width: "100%",
            textAlign: "left",
          }}
        >
          <span className="truncate">{t.name}</span>
        </Button>
      ))}

      {tagList.length === 0 && !tagsQ.isLoading && (
        <span style={{ fontSize: 10.5, color: "var(--color-text-muted)", lineHeight: 1.5 }}>
          尚无标签，
          <span className="underline cursor-default" title="见「配置标签」页">
            去配置标签新建
          </span>
        </span>
      )}

      <div style={{ flex: 1 }} />

      <div
        title="当前参数保存到的作用域"
        style={{
          fontSize: 10.5,
          padding: "3px 8px",
          borderRadius: 999,
          textAlign: "center",
          background: scope ? "var(--color-accent-soft)" : "var(--color-surface-raised)",
          color: scope ? "var(--color-accent)" : "var(--color-text-muted)",
          border: "1px solid var(--color-border)",
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
        }}
      >
        {scope === "" ? "全局" : scopeNameProp || "标签"}
      </div>
    </aside>
  );
}
