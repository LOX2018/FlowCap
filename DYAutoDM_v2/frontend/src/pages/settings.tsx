/**
 * 设置页（v0.38.2 重构）
 *
 * 变更（用户 2026-09-09 要求）：
 *   - 删除「默认配置 / 启动策略 / 独立账号」三个 tab：
 *     前两者与「通用配置」重复，后者与账号管理页权限冲突
 *   - 改为按**功能**拆 tab：通用配置（非业务）/ 私信发送 / 直播监听 /
 *     捕获与存储 / AI 与 Agent / 配置标签
 *   - 每个业务 tab 内的参数都可由「配置标签」按账号差异化覆盖
 *
 * 职责边界：
 *   - Agent 管「回什么」：回复内容（prompt / 知识库 / 话术 / 档位）
 *   - 标签 管「怎么发」：发送风控频率 / 直播监听 / 历史捕获策略
 *   - 账号的状态（UID/角色/守护）归「账号管理」页，不在这里
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { PageProps } from "../api/client";
import UnifiedConfigSection from "../components/UnifiedConfigSection";
import AgentSection from "../components/AgentSection";
import TagSection from "../components/TagSection";

type SectionKey =
  | "general"
  | "send"
  | "live"
  | "capture"
  | "agent"
  | "tag"
  | "notify";

const TABS: { key: SectionKey; label: string; hint: string }[] = [
  { key: "general", label: "通用配置", hint: "前端与系统行为（非业务）" },
  { key: "send", label: "私信发送", hint: "风控频率、闸门、额度" },
  { key: "live", label: "直播监听", hint: "监听节奏与轮询" },
  { key: "capture", label: "捕获与存储", hint: "历史补全、缓存、图片" },
  { key: "agent", label: "AI 与 Agent", hint: "回复内容：回什么" },
  { key: "tag", label: "配置标签", hint: "发送策略：怎么发" },
  { key: "notify", label: "通知与指令", hint: "IM 通知与指令解析的模型" },
];

export default function SettingsPage(props: PageProps) {
  const { ready } = props;
  const [section, setSection] = useState<SectionKey>("general");

  // 仅用于页头展示连接状态
  useQuery({
    queryKey: ["settings-ping"],
    queryFn: () => Promise.resolve(true),
    enabled: !!ready,
  });

  return (
    <div>
      <div className="section-head">
        <div>
          <h2>设置</h2>
          <div className="desc">
            按功能分区管理。业务参数（发送/监听/捕获）可用「配置标签」按账号差异化；
            回复内容用 Agent 管理
          </div>
        </div>
        <span className="demo-tag">{ready ? "已连接" : "未连接"}</span>
      </div>

      <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
        {/* 左侧子导航 */}
        <div className="set-nav">
          {TABS.map((t) => (
            <button
              key={t.key}
              className={
                "set-nav-item" + (section === t.key ? " is-active" : "")
              }
              onClick={() => setSection(t.key)}
              title={t.hint}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* 右侧内容区 */}
        <div
          style={{
            flex: 1,
            minWidth: 0,
            background: "color-mix(in oklch, var(--bg) 55%, transparent)",
            border: "1px solid var(--border)",
            borderRadius: 12,
            padding: 14,
          }}
        >
          {/* 非业务：只用 schema 里的 general 分区 */}
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
          {section === "agent" && <AgentSection {...props} />}
          {section === "tag" && <TagSection {...props} />}
          {section === "notify" && (
            <UnifiedConfigSection {...props} onlySections={["notify"]} />
          )}
        </div>
      </div>
    </div>
  );
}
