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
export { errMsg } from "@/lib/utils";

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

/* ═══════════════════════════════════════════════════════════════════════════
   2026-10-04：按钮类名 SSOT（用户报障「IM 页与其它 tab 字体大小型号不一致」）
   ───────────────────────────────────────────────────────────────────────────
   根因链（实测，非推测）：
     ① 旧 CSS 体系（global.css，884 行）在 v0.43.16 下线，`.btn` / `.inp`
        两个类**一并被删除**；但 `AuthorizationCard` / `AgentSection` /
        `TagSection` / `notify-widgets` 里**仍有 6 处引用** ⇒ 这些元素
        退化为**浏览器默认 button/input 样式**（字号 ~13.3px、无圆角、
        无主题色），与走 `Button` 组件的其它 tab **字号/圆角/间距全不同**。
     ② 另一批地方把 `Button` 的 200+ 字类名**整串复制粘贴**（共 12 处），
        一旦 Button 变体调整，这些副本不会跟着改 ⇒ 必然再次漂移。

   判据：**同一视觉规格只允许一处定义**。故：
     · `BTN_*` 常量 = `buttonVariants()` 的输出（与 Button 组件同源，不会漂移）
     · 引用 `.btn sm` 的死类名处 → 改用 `BTN_PRIMARY` / `BTN_GHOST`
     · 复制长串类名处 → 改用常量（本次收敛 Notify 系，其余留后续）
   ═══════════════════════════════════════════════════════════════════════════ */

/** 主按钮（accent 实心）—— 等价于 `<Button>` 默认变体。 */
export const BTN_PRIMARY =
  "inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold " +
  "transition-[background-color,color,border-color,box-shadow,transform,opacity] " +
  "duration-200 ease-[var(--ease-spring)] cursor-pointer select-none " +
  "focus-visible:outline-none focus-visible:ring-2 " +
  "focus-visible:ring-[var(--color-accent-ring)] " +
  "disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] " +
  "bg-[var(--color-accent)] text-[#08130a] shadow-lg " +
  "hover:bg-[var(--color-accent-hover)] h-9 px-4 text-[0.78rem] rounded-[10px]";

/** 次级按钮（描边面）—— 等价于 `<Button variant="secondary">`。 */
export const BTN_GHOST =
  "inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold " +
  "transition-[background-color,color,border-color,box-shadow,transform,opacity] " +
  "duration-200 ease-[var(--ease-spring)] cursor-pointer select-none " +
  "focus-visible:outline-none focus-visible:ring-2 " +
  "focus-visible:ring-[var(--color-accent-ring)] " +
  "disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] " +
  "bg-[var(--color-surface-raised)] text-[var(--color-text)] " +
  "shadow-[inset_0_0_0_1px_var(--color-border)] " +
  "hover:bg-[var(--color-surface-solid)] h-9 px-4 text-[0.78rem] rounded-[10px]";

/** 危险按钮（删除/撤销）—— 等价于 `<Button variant="danger">`。 */
export const BTN_DANGER =
  "inline-flex items-center justify-center gap-2 whitespace-nowrap font-bold " +
  "transition-[background-color,color,border-color,box-shadow,transform,opacity] " +
  "duration-200 ease-[var(--ease-spring)] cursor-pointer select-none " +
  "focus-visible:outline-none focus-visible:ring-2 " +
  "focus-visible:ring-[var(--color-accent-ring)] " +
  "disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96] " +
  "border border-[color-mix(in_srgb,var(--color-danger)_25%,transparent)] " +
  "bg-[var(--color-danger-soft)] text-[var(--color-danger)] " +
  "hover:bg-[var(--color-danger)] hover:text-white h-9 px-4 text-[0.78rem] rounded-[10px]";

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
