/**
 * DropdownMenu —— 基于 Radix `@radix-ui/react-dropdown-menu`（2026-09-29 新增）
 *
 * ## 设计意图
 *
 * 私信中心聊天框头原有 3 个导出入口（导出长图 / 选区导出 / 导出会话），
 * 用户要求合并为一个「导出」总按钮，点击弹出 3 个子选项。
 * 项目此前**无下拉菜单组件**（`components/ui/` 只有 select / dialog / tooltip…），
 * 故按 `select.tsx` 的既有蓝本（Radix 封装 + `glass-premium` + Portal）补一个，
 * **不引入新依赖**（`@radix-ui/react-dropdown-menu` 已在 package.json 依赖中）。
 *
 * ## 契约
 *
 * - 只导出菜单**壳**（Root/Trigger/Content/Item/Separator/Label）；
 *   业务语义（子项文案、onClick）由调用方给 —— 本组件不含任何业务分支。
 * - 风格与 `SelectContent` 对齐：`glass-premium` + `z-50` + Portal，
 *   保证在 Card/Toolbar 内弹出不被 `overflow-hidden` 裁剪。
 */
import * as React from "react";
import * as DropdownMenuPrimitive from "@radix-ui/react-dropdown-menu";
import { cn } from "@/lib/utils";

const DropdownMenu = DropdownMenuPrimitive.Root;
const DropdownMenuTrigger = DropdownMenuPrimitive.Trigger;
const DropdownMenuGroup = DropdownMenuPrimitive.Group;

const DropdownMenuContent = React.forwardRef<
  React.ElementRef<typeof DropdownMenuPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof DropdownMenuPrimitive.Content>
>(({ className, sideOffset = 6, ...props }, ref) => (
  <DropdownMenuPrimitive.Portal>
    <DropdownMenuPrimitive.Content
      ref={ref}
      sideOffset={sideOffset}
      className={cn(
        "z-50 min-w-[10rem] overflow-hidden rounded-[12px] p-1",
        "glass-premium",
        "data-[state=open]:animate-in data-[state=closed]:animate-out",
        className,
      )}
      {...props}
    />
  </DropdownMenuPrimitive.Portal>
));
DropdownMenuContent.displayName = "DropdownMenuContent";

const DropdownMenuItem = React.forwardRef<
  React.ElementRef<typeof DropdownMenuPrimitive.Item>,
  React.ComponentPropsWithoutRef<typeof DropdownMenuPrimitive.Item>
>(({ className, ...props }, ref) => (
  <DropdownMenuPrimitive.Item
    ref={ref}
    className={cn(
      "relative flex cursor-pointer select-none items-center gap-2",
      "rounded-[8px] px-3 py-2 text-[0.8rem] outline-none transition-colors",
      "text-[var(--color-text)]",
      "focus:bg-[var(--color-surface-raised)] focus:text-[var(--color-text)]",
      "data-[disabled]:pointer-events-none data-[disabled]:opacity-40",
      className,
    )}
    {...props}
  />
));
DropdownMenuItem.displayName = "DropdownMenuItem";

const DropdownMenuSeparator = React.forwardRef<
  React.ElementRef<typeof DropdownMenuPrimitive.Separator>,
  React.ComponentPropsWithoutRef<typeof DropdownMenuPrimitive.Separator>
>(({ className, ...props }, ref) => (
  <DropdownMenuPrimitive.Separator
    ref={ref}
    className={cn("-mx-1 my-1 h-px bg-[var(--color-border)]", className)}
    {...props}
  />
));
DropdownMenuSeparator.displayName = "DropdownMenuSeparator";

/** 分组标题（可选；本项目导出菜单暂未使用，保留以备后续分组） */
const DropdownMenuLabel = React.forwardRef<
  React.ElementRef<typeof DropdownMenuPrimitive.Label>,
  React.ComponentPropsWithoutRef<typeof DropdownMenuPrimitive.Label>
>(({ className, ...props }, ref) => (
  <DropdownMenuPrimitive.Label
    ref={ref}
    className={cn(
      "px-3 py-1.5 text-[0.7rem] font-semibold uppercase tracking-wide",
      "text-[var(--color-text-muted)]",
      className,
    )}
    {...props}
  />
));
DropdownMenuLabel.displayName = "DropdownMenuLabel";

export {
  DropdownMenu,
  DropdownMenuTrigger,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuGroup,
  DropdownMenuLabel,
};
