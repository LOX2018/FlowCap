/**
 * 设置区 —— 共享层（消除跨 Section 重复）
 *
 * ## 为什么独立（2026-09-15 大组件打散 + 优化）
 *
 * 用户要求：「项目有多数单一大组件、单一大页面，全部划分层级打散后优化」。
 *
 * 拆分过程中实测发现**真实的跨文件重复代码**（非推测）：
 *   · `errMsg(e: unknown)` 在 6 个 Section 文件里各定义一份，
 *     实现逐字节相同（归一化后唯一实现数 = 1）。
 *   · `Block`（ModelHubSection）与 `Section`（AgentSection）签名、实现
 *     逐字节相同（都用 SetCard/SetCardHead/SetCardBody）。
 *
 * 本模块承载这些**被多个 Section 共用**的小件，各 Section 只引用不重复定义。
 * 提取后行为完全一致（纯去重，不改逻辑）。
 */
import { type ReactNode } from "react";
import { SetCard, SetCardHead, SetCardBody, SetField } from "@/components/page/set-card";

/** 统一错误信息提取（原在 6 个 Section 文件重复定义，实现一致）。 */
export function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/**
 * 通用「标题 + 可选副标题 + 内容」卡片区块。
 * 原 ModelHubSection.Block 与 AgentSection.Section 实现相同，合并为此。
 */
export function SectionBlock(props: {
  title: string;
  subtitle?: string;
  children: ReactNode;
}) {
  return (
    <SetCard>
      <SetCardHead title={props.title} description={props.subtitle} />
      <SetCardBody>{props.children}</SetCardBody>
    </SetCard>
  );
}

export const ROLE_LABELS: Record<string, string> = {
  admin: "管理员（全授权）",
  operator: "操作员（可执行任务）",
  viewer: "查看者（仅查看）",
  blocked: "已拉黑",
  pending: "待授权",
};

export const CHANNEL_SHORT: Record<string, string> = {
  weixin_oc: "微信",
  wecom: "企微",
  dingtalk: "钉钉",
  lark: "飞书",
  qqofficial: "QQ",
};

export const inputStyle: React.CSSProperties = {
  width: "100%",
  boxSizing: "border-box",
  background: "var(--color-surface)",
  border: "1px solid var(--color-border)",
  borderRadius: 4,
  padding: "4px 6px",
  fontSize: 12,
  color: "var(--color-text)",
  outline: "none",
};

export function Field(props: { label: string; children: React.ReactNode }) {
  return (
    <SetField
      label={
        <span className="text-[0.72rem] font-normal text-[var(--color-text-muted)]">
          {props.label}
        </span>
      }
    >
      {props.children}
    </SetField>
  );
}
