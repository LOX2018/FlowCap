/**
 * 设置页（重设计版 · 对标 better-douyin 设计体系）
 *
 * ## 职责边界（设计契约，不得混淆）
 *   - Agent 管「回什么」：回复内容（prompt / 知识库 / 话术 / 档位）
 *   - 标签 管「怎么发」：发送风控频率 / 直播监听 / 历史捕获策略
 *   - 账号状态（UID/角色/守护）归「账号」页，不在这里
 *
 * ## 本次改动（重设计）
 * - 页头/子导航/内容容器全部改走设计令牌与新组件
 * - 旧 `.set-nav` + 内联 `color-mix(in oklch, var(--bg)…)` → 新令牌变量
 * - **7 个业务分区与承载组件零改动**（UnifiedConfigSection / ModelHubSection /
 *   AgentSection / TagSection / NotifySection 原样复用）
 */
import { useState } from "react";
import {
  Settings as SettingsIcon, Send, Radio, Database, Bot, Tags, Bell,
} from "lucide-react";
import { PageProps } from "../api/client";
import UnifiedConfigSection from "../components/UnifiedConfigSection";
import AgentSection from "../components/AgentSection";
import TagSection from "../components/TagSection";
import ModelHubSection from "../components/ModelHubSection";
import NotifySection from "../components/NotifySection";
import { PageContainer, PageHeader } from "@/components/layout/app-shell";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

type SectionKey =
  | "general" | "send" | "live" | "capture" | "agent" | "tag" | "notify";

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
  { key: "agent", label: "AI 与 Agent", hint: "回复内容：回什么", icon: <Bot className="h-3.5 w-3.5" /> },
  { key: "tag", label: "配置标签", hint: "发送策略：怎么发", icon: <Tags className="h-3.5 w-3.5" /> },
  { key: "notify", label: "通知与指令", hint: "IM 通知与指令解析的模型", icon: <Bell className="h-3.5 w-3.5" /> },
];

export default function SettingsPage(props: PageProps) {
  const [section, setSection] = useState<SectionKey>("general");

  return (
    <PageContainer>
      <PageHeader
        title="设置"
        description="按功能分区管理。业务参数（发送/监听/捕获）可用「配置标签」按账号差异化；回复内容用 Agent 管理"
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
            {section === "agent" && (
              <>
                {/* 模型链路中心：AI 与 IM 通知共用的模型唯一真源 */}
                <ModelHubSection {...props} />
                <AgentSection {...props} />
              </>
            )}
            {section === "tag" && <TagSection {...props} />}
            {section === "notify" && <NotifySection {...props} />}
          </CardContent>
        </Card>
      </div>
    </PageContainer>
  );
}
