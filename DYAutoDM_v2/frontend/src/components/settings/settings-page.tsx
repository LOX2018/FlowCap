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
  Settings as SettingsIcon, Send, Radio, Database, Bot, Tags, Bell, Users,
} from "lucide-react";
import { PageProps } from "../../api/client";
import UnifiedConfigSection from "./UnifiedConfigSection";
import AgentSection from "./AgentSection";
import ModelHubSection from "./ModelHubSection";
import NotifySection from "./NotifySection";
import TagSection from "./TagSection";
import AiEngineSection from "./AiEngineSection";
import { PageContainer, PageHeader } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

type SectionKey =
  | "general" | "send" | "live" | "capture" | "ai" | "agent" | "tag" | "notify";

const TABS: {
  key: SectionKey;
  label: string;
  hint: string;
  icon: React.ReactNode;
}[] = [
  { key: "general", label: "通用配置", hint: "前端与系统行为（非业务）", icon: <SettingsIcon className="h-3.5 w-3.5" /> },
  { key: "send", label: "私信发送", hint: "风控频率、闸门、额度", icon: <Send className="h-3.5 w-3.5" /> },
  { key: "live", label: "直播监听", hint: "监听节奏与轮询", icon: <Radio className="h-3.5 w-3.5" /> },
  { key: "capture", label: "捕获与存储", hint: "历史补全、缓存、图片", icon: <Database className="h-3.5 w-3.5" /> },
  { key: "ai", label: "AI 回复引擎", hint: "模型链路 + 回复内容 / 护栏 / 黑名单", icon: <Bot className="h-3.5 w-3.5" /> },
  { key: "agent", label: "Agent 与绑定", hint: "Agent 模版 + 账号绑定", icon: <Users className="h-3.5 w-3.5" /> },
  { key: "tag", label: "配置标签", hint: "发送策略：怎么发", icon: <Tags className="h-3.5 w-3.5" /> },
  { key: "notify", label: "通知与指令", hint: "IM 通知与指令解析的模型", icon: <Bell className="h-3.5 w-3.5" /> },
];

export default function SettingsPage(props: PageProps) {
  const [section, setSection] = useState<SectionKey>("general");

  return (
    <PageContainer>
      <PageHeader
        title="配置中心"
        description="全局唯一可写配置入口。业务页只做「选择/引用」；业务参数（发送/监听/捕获）可用「配置标签」按账号差异化"
      />

      <div className="flex items-start gap-4">
        {/* 左侧子导航 */}
        <nav className="w-[168px] shrink-0 space-y-0.5">
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

        {/* 右侧内容区 */}
        <Card className="min-w-0 flex-1">
          <CardContent className="p-3.5">
            {section === "general" && (
              <UnifiedConfigSection {...props} onlySections={["general"]} />
            )}
            {section === "send" && (
              <UnifiedConfigSection {...props} onlySections={["send"]} />
            )}
            {section === "live" && (
              <UnifiedConfigSection {...props} onlySections={["live"]} />
            )}
            {section === "capture" && (
              <UnifiedConfigSection {...props} onlySections={["capture"]} />
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
            {section === "notify" && <NotifySection {...props} />}
          </CardContent>
        </Card>
      </div>
    </PageContainer>
  );
}
