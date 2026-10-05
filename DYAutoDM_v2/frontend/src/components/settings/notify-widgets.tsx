/**
 * 通知设置 —— 本地小组件（Card / Field）
 *
 * ## 为什么独立（2026-09-15 大组件打散）
 *
 * 用户要求：「项目有多数单一大组件、单一大页面，全部划分层级打散后优化」。
 * `NotifySection.tsx`（666 行）内联了 Card（60 行）/ Field（32 行）两个
 * 可复用小组件，抽出后主体更聚焦业务逻辑。
 *
 * ## 2026-10-04 重写：从「第二套设计体系」收敛到全站唯一体系
 *
 * 用户报障：「配置中心的 IM 页面和其他 tab 页面字体大小、型号都不一致」。
 *
 * 根因（实测，非推测）：本文件的 Card/Field 是**旧 CSS 时代的私有实现** ——
 * 内联 style 写死 `borderRadius: 10` / `padding: "12px 14px"` / `fontSize: 12`，
 * 而其它所有 tab 走 `@/components/page/set-card` 的 SetCard 族（Tailwind +
 * 设计令牌：`rounded-[var(--radius-md)]`、`p-3.5`、`text-[0.86rem]`）。
 * 两套体系并存 ⇒ 圆角、内边距、字号三项**全都不同**。
 *
 * 附带修掉一个真实缺陷：`Field` 的 input 用 `className="inp"`，而 `.inp`
 * 在 v0.43.16「旧 CSS 体系下线」时**已被删除** ⇒ 该输入框退化为浏览器默认
 * 样式（无圆角、无边框、无主题色、字号 ~13.3px）。现改用 `@/components/ui/input`。
 *
 * 修法：Card/Field **改为转调** SetCard 族（薄适配层，保留原有 props 契约），
 * 而不是复制一份新样式 —— 复制只会制造第三套体系。
 * ⚠️ 保留项（业务语义，不可被纯样式替换抹掉）：
 *   · Card 的可折叠语义（点击头部切换）+ 受控展开（AuthorizationCard 依赖）
 *   · Field 的 `secret`（密码框）与 `hint`（字段说明）
 */
import { useState } from "react";
import { SetCard, SetCardHead, SetCardBody, SetField } from "@/components/page/set-card";
import { Input } from "@/components/ui/input";

export function Card(props: {
  title: React.ReactNode;
  subtitle?: string;
  defaultOpen?: boolean;
  /** 受控展开态（2026-09-28 补；传入即受控，`defaultOpen` 忽略） */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  footer?: React.ReactNode;
  children: React.ReactNode;
}) {
  const [openState, setOpenState] = useState(!!props.defaultOpen);
  // 2026-09-28：补受控模式 —— 与 page/kit 的 Collapse 同一缺陷同批修。
  // 原只有非受控 defaultOpen（仅在挂载时生效），使「异步数据到达后要求展开」
  // （如 AuthorizationCard 的待审批列表）永远不生效。
  const isControlled = props.open !== undefined;
  const open = isControlled ? !!props.open : openState;
  const setOpen = (next: boolean) => {
    if (!isControlled) setOpenState(next);
    props.onOpenChange?.(next);
  };
  return (
    <SetCard>
      <SetCardHead
        title={props.title}
        description={props.subtitle}
        open={open}
        onToggle={() => setOpen(!open)}
        right={
          // 旧实现在头部右侧显示「展开/收起」文字；SetCardHead 用 Chevron
          // 图标表达同一语义，这里补回文字以保留原有信息密度。
          <span className="whitespace-nowrap text-[0.72rem] text-[var(--color-text-muted)]">
            {open ? "收起" : "展开"}
          </span>
        }
      />
      {open && (
        <SetCardBody>
          {props.children}
          {props.footer && <div className="mt-3">{props.footer}</div>}
        </SetCardBody>
      )}
    </SetCard>
  );
}

export function Field(props: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  secret?: boolean;
  hint?: string;
  placeholder?: string;
}) {
  return (
    <SetField
      label={
        <>
          {props.label}
          {props.secret && (
            <span className="ml-1.5 font-normal text-[var(--color-text-muted)]">
              （敏感）
            </span>
          )}
        </>
      }
      hint={props.hint}
    >
      <Input
        type={props.secret ? "password" : "text"}
        value={props.value}
        placeholder={props.placeholder || ""}
        onChange={(e) => props.onChange(e.target.value)}
      />
    </SetField>
  );
}
