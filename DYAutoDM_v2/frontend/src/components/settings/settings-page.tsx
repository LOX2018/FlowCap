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
import { useState } from "react";
import {
  // 2026-09-18 审查修复（#54）：`MessageSquare` 原先在下方**重复 import**
  // 同一个模块（lucide-react 被 import 两次）；合并到这一处。
  Settings as SettingsIcon, Send, Radio, Database, Bot, Tags, Bell, Users, MessageSquare,
  Plug, ShieldCheck,
} from "lucide-react";
import { PageProps } from "../../api/client";
import UnifiedConfigSection from "./UnifiedConfigSection";
import AgentSection from "./AgentSection";
import ModelHubSection from "./ModelHubSection";
import NotifySection from "./NotifySection";
import TagSection from "./TagSection";
import CrawlPolicySection from "./CrawlPolicySection";
import AiEngineSection from "./AiEngineSection";
import NicknameFallbackSection from "./NicknameFallbackSection";
import McpSection from "./McpSection";
// ADR-018 F6：日夜主题切换的唯一可写入口（主题引擎本身早已存在，缺的是入口）
import AppearanceSection from "./AppearanceSection";
// 2026-09-30：能力巡检入口从总览页迁来（总览改为纯只读看板）。
import ProbeSection from "./ProbeSection";
import BackupSection from "./BackupSection";
import { PageContainer, PageHeader } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

type SectionKey =
  | "general" | "send" | "live" | "capture" | "dm"
  | "ai" | "agent" | "tag" | "notify" | "mcp" | "crawlpolicy" | "system";

const TABS: {
  key: SectionKey;
  label: string;
  hint: string;
  icon: React.ReactNode;
}[] = [
  { key: "general", label: "通用配置", hint: "前端行为（非业务）", icon: <SettingsIcon className="h-3.5 w-3.5" /> },
  { key: "send", label: "私信发送", hint: "风控频率、闸门、额度", icon: <Send className="h-3.5 w-3.5" /> },
  { key: "live", label: "直播监听", hint: "监听节奏与轮询", icon: <Radio className="h-3.5 w-3.5" /> },
  { key: "capture", label: "捕获与存储", hint: "历史补全、缓存、图片", icon: <Database className="h-3.5 w-3.5" /> },
  { key: "dm", label: "私信 / 昵称兜底", hint: "昵称兜底（默认关，主动查询有风控成本）", icon: <MessageSquare className="h-3.5 w-3.5" /> },
  { key: "ai", label: "AI 回复引擎", hint: "模型链路 + 回复内容 / 护栏 / 黑名单", icon: <Bot className="h-3.5 w-3.5" /> },
  { key: "agent", label: "Agent 与绑定", hint: "Agent 模版 + 账号绑定", icon: <Users className="h-3.5 w-3.5" /> },
  { key: "tag", label: "配置标签", hint: "发送策略：怎么发", icon: <Tags className="h-3.5 w-3.5" /> },
  // 2026-09-27（ADR-018 F1-D3）：采集策略层。与「配置标签」互补 ——
  // 标签管「用哪套参数」，本项管「采集参数本身」。
  { key: "crawlpolicy", label: "采集策略", hint: "可复用的采集参数（只存参数，不自动采集）", icon: <Database className="h-3.5 w-3.5" /> },
  { key: "notify", label: "通知与指令", hint: "IM 通知与指令解析的模型", icon: <Bell className="h-3.5 w-3.5" /> },
  // 2026-09-25：补 MCP 入口。此前后端 7 个端点已完整，但前端零引用
  // ⇒ 用户「看不到入口、也不知道令牌」= 能力在位但不可得。
  { key: "mcp", label: "MCP 服务", hint: "AI 客户端接入（stdio 免令牌 / 本机 HTTP 需令牌）", icon: <Plug className="h-3.5 w-3.5" /> },
  // 2026-09-30：系统运维（能力巡检）。总览页改为纯只读看板后，
  // 「立即巡检」的**唯一**入口落在此处 —— 端点此前仅总览页一处调用，
  // 不补入口会让 /api/probe/patrol 变成「在位但不可得」。
  { key: "system", label: "系统", hint: "外观主题 · 备份 · 导出路径 · 能力巡检", icon: <ShieldCheck className="h-3.5 w-3.5" /> },
];

export default function SettingsPage(props: PageProps) {
  const [section, setSection] = useState<SectionKey>("general");

  return (
    <PageContainer>
      <PageHeader
        title="配置中心"
        description="业务参数可按账号用「配置标签」差异化"
      />

      <div className="flex items-start gap-4">
        {/* 左侧子导航 */}
        {/* 独立固定：main 是滚动容器，nav 用 sticky 留在流内（保留 168px 占位与 gap-4，
            无需 fixed 的宽度补偿）。top-0 贴 main 顶边；self-start 防止被拉伸导致无吸附余量；
            max-h/overflow 使导航项超高时内部滚动且不把滚动链传导回 main。 */}
        <nav className="w-[168px] shrink-0 space-y-0.5 sticky top-0 self-start z-20 max-h-[calc(100vh-120px)] overflow-y-auto overscroll-contain bg-[var(--color-background)] py-1">
          {TABS.map((t) => {
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
                {t.label}
              </button>
            );
          })}
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
              截断而非放任横向滚动：配置表单是纵向阅读，横向滚动条是缺陷不是功能。 */}
        <Card className="min-w-0 flex-1">
          <CardContent className="min-w-0 overflow-x-hidden p-3.5">
            {section === "general" && (
              /* 后端 schema 驱动的通用配置（非业务）。
                 2026-10-02 用户要求：「外观（日夜主题）」实为系统页功能，已移至「系统」tab。 */
              <UnifiedConfigSection {...props} onlySections={["general"]} />
            )}
            {section === "send" && (
              <UnifiedConfigSection {...props} onlySections={["send"]} />
            )}
            {section === "live" && (
              <>
                <UnifiedConfigSection {...props} onlySections={["live"]} />
                {/* ADR-002 §5.4 策略中心：与「直播监听」同 tab（后端 schema 驱动，
                    仅需在此白名单登记分区名，无手写表单）。 */}
                <UnifiedConfigSection {...props} onlySections={["live_orchestration"]} />
                {/* 高价值关键词权重表（2026-09-29）：唯一编辑入口已迁到「直播监听」页
                    （`live-high-value-keywords` 按钮 → `HighValueKeywordsModal`）。
                    此处**不再挂载**，避免同一张表两处入口（SSOT / 用户要求）。 */}
              </>
            )}
            {section === "capture" && (
              <UnifiedConfigSection {...props} onlySections={["capture"]} />
            )}
            {section === "dm" && (
              <>
                {/* 配置域 dm：昵称兜底开关 + 三重上限（唯一可写处） */}
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
            {section === "crawlpolicy" && <CrawlPolicySection {...props} />}
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
      </div>
    </PageContainer>
  );
}
