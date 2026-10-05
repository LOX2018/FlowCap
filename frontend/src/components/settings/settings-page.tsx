/**
 * 配置中心（原「设置」页升格）
 *
 * ## 定位（2026-09-14 用户拍板）
 *
 * 侧栏底部独立入口，**全局唯一可写配置入口**。其他页面只保留"选择/引用"（调用口）。
 *
 * ## 职责边界（设计契约，不得混淆）
 *   - 引擎管「回什么」：回复内容（prompt / 知识库 / 话术 / 档位 / 护栏）
 *   - 标签 管「怎么发」：发送风控频率 / 直播监听 / 历史捕获策略
 *   - 账号状态（UID/角色/守护）归「账号」页；Agent 的**选择**也在「账号」页
 *
 * ## 本次改动（AI 页打散归类）
 * 原「AI 获客」页 6 块内容按**数据作用域**实测分派，其中三块落到本页：
 *   · Agent 设定 + 护栏配置 + 黑名单 → 新组件 `AiEngineSection`
 * （运行控制 → 总览页；留资线索 → 私信页；知识库入口卡 → 删除）
 *
 * `AiEngineSection` 与 `AgentSection` 的字段分工（避免两处可写）：
 *   · `AiEngineSection` 管**引擎参数**：商家名 / 档位 / prompt / 留资 / 延迟 / 护栏 / 黑名单
 *   · `AgentSection`    管**模版与绑定**：Agent 列表 / 名称 / 主模型 / 启用 / 作用域 / 账号绑定
 */
import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  // 2026-09-18 审查修复（#54）：`MessageSquare` 原先在下方**重复 import**
  // 同一个模块（lucide-react 被 import 两次）；合并到这一处。
  Settings as SettingsIcon, Send, Radio, Database, Bot, Tags, Bell, Users, MessageSquare,
  Plug, ShieldCheck, ListFilter,
} from "lucide-react";
import { PageProps, api } from "../../api/client";
import UnifiedConfigSection from "./UnifiedConfigSection";
import AgentSection from "./AgentSection";
import ModelHubSection from "./ModelHubSection";
import NotifySection from "./NotifySection";
import TagSection from "./TagSection";
import CrawlPolicySection from "./CrawlPolicySection";
import AiEngineSection from "./AiEngineSection";
import NicknameFallbackSection from "./NicknameFallbackSection";
import HighValueKeywordsSection from "./HighValueKeywordsSection";
import McpSection from "./McpSection";
import { ScopeRail, readRememberedScope, rememberScope } from "./scope-rail";
// ADR-018 F6：日夜主题切换的唯一可写入口（主题引擎本身早已存在，缺的是入口）
import AppearanceSection from "./AppearanceSection";
// 2026-09-30：能力巡检入口从总览页迁来（总览改为纯只读看板）。
import ProbeSection from "./ProbeSection";
import BackupSection from "./BackupSection";
import { PageContainer, PageHeader } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

export type SectionKey =
  | "general" | "send" | "live" | "capture" | "dm"
  | "ai" | "agent" | "tag" | "notify" | "mcp" | "crawlpolicy" | "system";

type TabGroup = {
  title: string;
  items: {
    key: SectionKey;
    label: string;
    hint: string;
    icon: React.ReactNode;
    /**
     * 本 tab 是否有**可编辑的参数分区**（即右侧是否显示「作用域标签栏」）。
     *
     * 判据 = 该 tab 是否渲染了走 `get_effective_config` 覆盖路径的参数：
     * 参数按 scope 拉取、按 scope 保存 ⇒ 选标签才有意义。
     *
     * 只有**有**参数分区的 tab 才放这柱：
     *   · 无参数分区的 tab（通知 / MCP / 系统 / Agent 与绑定 / 配置标签）
     *     选标签不会改变任何行为，放了就是「点了没反应」的入口；
     *   · 「配置标签」tab 是标签的**管理页**（新建/删除），
     *     在这里再放一柱 scope 选择会与列表自身功能打架；
     *   · 「私信列表」tab 的 dm 分区不在 `MANAGED_SECTIONS` 白名单内，
     *     选标签会被后端忽略（写入成功但永不生效）⇒ 不显示；
     *   · 「AI 回复引擎」tab 的两个子组件走各自存储
     *     （`aiSaveConfig` 存 Agent、ModelHub 存模型链路），
     *     **都不经** `get_effective_config` ⇒ 选标签无效 ⇒ 不显示。
     *
     * 放栏的 4 个 tab：私信发送（send）· 监听策略（live + live_orchestration）·
     * 捕获与存储（capture）· 采集策略（高价值关键词权重表，按 全局/标签 存）。
     */
    scoped: boolean;
  }[];
};

/**
 * 分区按用户任务流分四组（2026-10-05 T9）：
 * 此前 12 个分区平铺、无分组，支持标签差异化的 4 个与支持不了的 8 个
 * 在页面上看不出任何区别，用户无法预判「切标签会不会改变这一页的行为」。
 * 分组依据是任务顺序（先发送、再采集、再智能、最后通用），不是后端分区归属。
 *
 * ⚠️ 数据源仍是扁平的 `TABS`（`SectionKey` 联合、`TABS.some`、`TABS.find`
 * 等既有引用点不动）；`TAB_GROUPS` 只是对同一批条目的**顺序与分组视图**，
 * 新增分组时不要复制条目，改这里一处即可。
 */
const TAB_GROUPS: TabGroup[] = [
  {
    title: "发送与风控",
    items: [
      { key: "send", label: "私信发送", hint: "多久发一条、每天发多少", scoped: true, icon: <Send className="h-3.5 w-3.5" /> },
      { key: "tag", label: "配置标签", hint: "给一套参数起个名，按账号套用", scoped: false, icon: <Tags className="h-3.5 w-3.5" /> },
    ],
  },
  {
    title: "采集与监听",
    items: [
      { key: "live", label: "监听策略", hint: "看直播多久刷新一次", scoped: true, icon: <Radio className="h-3.5 w-3.5" /> },
      { key: "capture", label: "捕获与存储", hint: "历史消息补全、缓存多久", scoped: true, icon: <Database className="h-3.5 w-3.5" /> },
      { key: "crawlpolicy", label: "采集策略", hint: "可复用的采集参数，不自动跑", scoped: true, icon: <ListFilter className="h-3.5 w-3.5" /> },
      { key: "dm", label: "私信列表", hint: "昵称查不到时是否主动查", scoped: false, icon: <MessageSquare className="h-3.5 w-3.5" /> },
    ],
  },
  {
    title: "智能与接入",
    items: [
      { key: "ai", label: "AI 回复引擎", hint: "用什么模型、回什么内容", scoped: false, icon: <Bot className="h-3.5 w-3.5" /> },
      { key: "agent", label: "Agent 与绑定", hint: "回复模版、绑到哪个账号", scoped: false, icon: <Users className="h-3.5 w-3.5" /> },
      { key: "notify", label: "通知与指令", hint: "发到哪个群、指令怎么解析", scoped: false, icon: <Bell className="h-3.5 w-3.5" /> },
      // 2026-09-25：补 MCP 入口。此前后端 7 个端点已完整，但前端零引用
      // ⇒ 用户「看不到入口、也不知道令牌」= 能力在位但不可得。
      { key: "mcp", label: "MCP 服务", hint: "让 AI 助手连上本系统", scoped: false, icon: <Plug className="h-3.5 w-3.5" /> },
    ],
  },
  {
    title: "通用",
    items: [
      { key: "general", label: "通用配置", hint: "启动行为、凭据失效时怎么办", scoped: false, icon: <SettingsIcon className="h-3.5 w-3.5" /> },
      // 2026-09-30：系统运维（能力巡检）。总览页改为纯只读看板后，
      // 「立即巡检」的**唯一**入口落在此处 —— 端点此前仅总览页一处调用，
      // 不补入口会让 /api/probe/patrol 变成「在位但不可得」。
      { key: "system", label: "系统", hint: "外观、备份、导出、能力巡检", scoped: false, icon: <ShieldCheck className="h-3.5 w-3.5" /> },
    ],
  },
];

const TABS = TAB_GROUPS.flatMap((g) => g.items);

export default function SettingsPage(props: PageProps) {
  // 2026-10-05：支持外部指定落点分区（总览「去巡检」→ system）。
  // 此前 setTab 只有一级、默认恒落 general ⇒ 按钮文案与落点不符。
  const [section, setSection] = useState<SectionKey>(() => {
    const want = props.initialSection;
    return TABS.some((t) => t.key === want) ? (want as SectionKey) : "general";
  });

  // ---- 配置作用域（标签）—— 2026-10-04 从各子组件内部提升到页面层 ----
  // 原实现每个 UnifiedConfigSection 实例各自持有一条「保存到 全局/标签」栏，
  // 一个 tab 挂 2 个实例就出现 2 条重复栏（「监听策略」= 监听主卡 + 弹幕子卡，
  // 另有 HVK 自带一条），切换一次要对齐好几处。
  // 现在 scope 是本页单一状态，右侧一柱 ScopeRail 是唯一入口，
  // 下发给该 tab 下所有子组件 ⇒ 一个 tab 只有一个「当前作用域」概念。
  // 初始化读 localStorage：切 tab 会卸载 ScopeRail，不记住的话每次切回都
  // 重置为「全局」，用户可能在全局作用域里误存本该属于某标签的参数。
  const [scope, setScope] = useState<string>(() => readRememberedScope());
  const [scopeName, setScopeName] = useState<string>("");

  const tagsQ = useQuery({
    queryKey: ["tag-switcher"],
    queryFn: () => api.listTags(),
    staleTime: 30_000,
  });
  // 标签列表晚于 scope 初始值返回 ⇒ 补一次 scopeName（列表里可能已无该标签）。
  useEffect(() => {
    const list = tagsQ.data?.tags || [];
    const t = list.find((x) => x.id === scope);
    setScopeName(scope ? t?.name || "" : "");
  }, [tagsQ.data, scope]);

  const chooseScope = (id: string) => {
    rememberScope(id);
    setScope(id);
  };

  // 当前 tab 是否**支持**标签覆盖（见 TABS 各 tab 的 `scoped` 注释判据）。
  const supportsScope = TABS.find((t) => t.key === section)?.scoped ?? false;

  // 切到不支持 scope 的 tab 时清空 scope：这些 tab 的组件不会收到 scope，
  // 若残留「上次选的标签」，回到支持 scope 的 tab 又会自动带上它 ——
  // 用户可能在自己刚改了参数的作用域里存错地方。切走即清，回到默认「全局」。
  useEffect(() => {
    if (!supportsScope) setScope("");
  }, [supportsScope]);

  return (
    <PageContainer>
      <PageHeader
        title="配置中心"
        description="业务参数可按账号用「配置标签」差异化"
      />

      <div className="flex items-start gap-4">
        {/* 左侧子导航
            2026-10-05（T9）：改为分组渲染。此前 12 项平铺，支持「按账号」差异化的
            4 个与不支持的 8 个在视觉上无差别；现在组标题 + 行内「按账号」小标
            让用户进入该分区前就知道切标签是否生效。 */}
        <nav className="w-[168px] shrink-0 space-y-0.5 sticky top-0 self-start z-20 max-h-[calc(100vh-120px)] overflow-y-auto overscroll-contain bg-[var(--color-background)] py-1">
          {TAB_GROUPS.map((g) => (
            <div key={g.title} className="mb-1">
              <div className="flex items-center gap-1 px-3 pb-0.5 pt-1.5">
                <span className="text-[0.64rem] font-semibold uppercase tracking-[0.1em] text-[var(--color-text-muted)]">
                  {g.title}
                </span>
              </div>
              {g.items.map((t) => {
                const on = section === t.key;
                return (
                  <button
                    key={t.key}
                    type="button"
                    title={t.hint}
                    onClick={() => setSection(t.key)}
                    className={cn(
                      "flex w-full items-center gap-2 rounded-[var(--radius-sm)] px-3 py-2",
                      "text-left text-[0.8rem] font-medium",
                      "transition-colors duration-[var(--duration-fast)] ease-[var(--ease-spring)]",
                      on
                        ? "bg-[var(--color-accent-soft)] text-[var(--color-accent)]"
                        : "text-[var(--color-text-secondary)] hover:bg-[var(--color-surface-raised)] hover:text-[var(--color-text)]"
                    )}
                  >
                    {t.icon}
                    <span className="min-w-0 truncate">{t.label}</span>
                    {/* 只标在真正支持标签差异化的分区上：未标即表示「这里改的是全局」，
                        与右侧 ScopeRail 是否出现同源（同一 `scoped` 字段），不新增判据。 */}
                    {t.scoped && (
                      <span className="ml-auto shrink-0 rounded-[var(--radius-sm)] bg-[var(--color-surface-raised)] px-1 py-0.5 text-[0.58rem] font-medium text-[var(--color-text-muted)]">
                        按账号
                      </span>
                    )}
                  </button>
                );
              })}
            </div>
          ))}
        </nav>

        {/* 右侧内容区
            ⚠️ 2026-10-04 根因修复（用户报障「每一个页面的显示区宽度不一致」）：
              外层 `min-w-0 flex-1` 只挡住了 **Card 本身**被撑破，挡不住 **Card
              内部**的内容 —— 多个 Section（ModelHub / Agent / Tag / Provider）
              用内联 style 写死 `minWidth: 140 / 170`、`width: 130` 的 flex 项，
              父级是 `nowrap` 时这些最小宽会**累加**并撑出横向溢出 ⇒ 各 tab 的
              实际内容宽度随其撑破程度而不同（观感「宽度不一致」）。
              修法：CardContent 加 `min-w-0` + `overflow-x-hidden`，把溢出**就地
              截断在卡片内**，各 tab 的显示区宽度恒等于 Card 宽度（= 一致）。
              截断而非放任横向滚动：配置表单是纵向阅读，横向滚动条是缺陷不是功能。

            2026-10-04：作用域标签栏（ScopeRail）是 Card 的**兄弟**，不是子节点 ——
              CardContent 的 `overflow-x-hidden` 会把它裁掉。 */}
        <div className="flex min-w-0 flex-1 items-start gap-3">
          <Card className="min-w-0 flex-1">
            <CardContent className="min-w-0 overflow-x-hidden p-3.5">
              {section === "general" && (
                /* 后端 schema 驱动的通用配置（非业务）。
                   2026-10-02 用户要求：「外观（日夜主题）」实为系统页功能，已移至「系统」tab。 */
                <UnifiedConfigSection {...props} onlySections={["general"]} />
              )}
              {section === "send" && (
                /* 私信词库（2026-10-04 用户指令）从 live 分区迁回本分区，
                   并用 fieldGroups 拆成**单独子卡片**：主卡放风控频率/闸门/额度，
                   子卡只放词库。同一实例渲染 ⇒ 共享 scope 与草稿，
                   不会出现两张卡各存各的作用域。 */
                <UnifiedConfigSection
                  {...props}
                  scope={scope}
                  scopeName={scopeName}
                  onlySections={["send"]}
                  fieldGroups={[{ title: "私信词库", fields: ["dm_pool"] }]}
                />
              )}
              {section === "live" && (
                <>
                  {/* 主卡（监听节奏/轮询等）+ 弹幕文案库子卡 —— **同一实例**渲染，
                      共享 scope 与草稿。
                      ⚠️ 2026-10-04 修复：此前误写成「两个实例叠加」（一个渲染整分区、
                      另一个再渲染 danmaku_pool 子卡）⇒ 弹幕文案库在页面上**出现两次**，
                      且两张卡各有独立的 scope 选择，可把同一字段写进不同作用域。 */}
                  <UnifiedConfigSection
                    {...props}
                    scope={scope}
                    scopeName={scopeName}
                    onlySections={["live"]}
                    fieldGroups={[{ title: "弹幕文案库", fields: ["danmaku_pool"] }]}
                  />
                  {/* ADR-002 §5.4 策略中心：与「直播监听」同 tab（后端 schema 驱动，
                      仅需在此白名单登记分区名，无手写表单）。 */}
                  <UnifiedConfigSection
                    {...props}
                    scope={scope}
                    scopeName={scopeName}
                    onlySections={["live_orchestration"]}
                  />
                </>
              )}
              {section === "capture" && (
                <UnifiedConfigSection
                  {...props}
                  scope={scope}
                  scopeName={scopeName}
                  onlySections={["capture"]}
                />
              )}
              {section === "dm" && (
                <>
                  {/* 配置域 dm：昵称兜底开关 + 三重上限（唯一可写处）。
                      不传 scope：dm 不在 MANAGED_SECTIONS 白名单内，标签覆盖不生效。 */}
                  <UnifiedConfigSection {...props} onlySections={["dm"]} />
                  {/* 运维卡：状态 / 候选（零外呼）/ 执行一次（显式触发） */}
                  <NicknameFallbackSection />
                </>
              )}
              {section === "ai" && (
                <>
                  {/* 模型链路中心：提供商 / 模型 / 避障链路 / 消费方绑定（唯一真源） */}
                  <ModelHubSection {...props} />
                  {/* 引擎参数：Agent 设定 + 护栏 + 黑名单（原 AI 页打散归类而来） */}
                  <AiEngineSection {...props} />
                </>
              )}
              {section === "agent" && <AgentSection {...props} />}
              {section === "tag" && <TagSection {...props} />}
              {section === "crawlpolicy" && (
                <>
                  {/* 2026-10-05：本页 scoped:true，但策略集合本身不按作用域存
                      （CrawlPolicySection 只收 push，不接 scope）—— 右侧 ScopeRail
                      实际只驱动下方关键词权重表。此前无说明，用户会以为切标签
                      会切换策略集合。 */}
                  <div style={{ fontSize: 11, color: "var(--color-text-muted)",
                                 marginBottom: 10, lineHeight: 1.6 }}>
                    本 tab 仅下方「高价值关键词权重表」随右侧作用域变化；采集策略集合本身不按作用域保存。
                  </div>
                  <CrawlPolicySection {...props} />
                  {/* 高价值关键词权重表（2026-10-04 用户要求）：从直播页迁入采集策略页。
                      该表由 `api/crawl.py` 消费（`score_text(..., scope)` 做采集过滤），
                      且 `config_tag.py` 称其为「策略的附属」⇒ 归属采集策略更贴切。
                      作用域选择由右侧统一的 ScopeRail 承载 —— 本页只有一柱标签栏。
                      ⚠️ 直播页的入口**保留**：该表同时服务直播发送侧（send 分区的
                      `high_value_score_threshold`），两处入口分属不同业务域，非重复。 */}
                  <HighValueKeywordsSection scope={scope} onScopeChange={chooseScope} />
                </>
              )}
              {section === "notify" && <NotifySection {...props} />}
              {section === "mcp" && <McpSection push={props.push} />}
              {section === "system" && (
                <>
                  {/* 2026-10-02：系统分区（后端 schema）—— 文件导出路径管理。 */}
                  <UnifiedConfigSection {...props} onlySections={["system"]} />
                  {/* 2026-10-02 从「通用配置」迁入：外观（日夜主题）属系统级功能。
                      纯前端偏好，存 localStorage，不经后端 schema。 */}
                  <AppearanceSection />
                  {/* 2026-10-02 用户要求：备份子板块（导出 / 导入，自定义范围）。 */}
                  <BackupSection {...props} />
                  <ProbeSection {...props} />
                </>
              )}
            </CardContent>
          </Card>
          {supportsScope && (
            <ScopeRail
              scope={scope}
              onScopeChange={chooseScope}
              scopeName={scopeName}
            />
          )}
        </div>
      </div>
    </PageContainer>
  );
}
