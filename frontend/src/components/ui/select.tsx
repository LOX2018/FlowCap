/**
 * Select —— 对标 better-douyin（Radix Select 封装）
 */
import * as React from "react";
import * as SelectPrimitive from "@radix-ui/react-select";
import { Check, ChevronDown } from "lucide-react";
import { cn } from "@/lib/utils";

const Select = SelectPrimitive.Root;
const SelectValue = SelectPrimitive.Value;

const SelectTrigger = React.forwardRef<
  React.ElementRef<typeof SelectPrimitive.Trigger>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitive.Trigger>
>(({ className, children, ...props }, ref) => (
  <SelectPrimitive.Trigger
    ref={ref}
    className={cn(
      "flex h-9 w-full items-center justify-between gap-2 rounded-[10px] px-3",
      "bg-[var(--color-surface)] border border-[var(--color-border)]",
      "text-[0.85rem] text-[var(--color-text)] transition-colors duration-200",
      "hover:border-[var(--color-border-strong)]",
      "focus:outline-none focus:border-[var(--color-accent)] focus:ring-2 focus:ring-[var(--color-accent-ring)]",
      "disabled:opacity-50 [&>span]:line-clamp-1",
      className
    )}
    {...props}
  >
    {children}
    <SelectPrimitive.Icon asChild>
      <ChevronDown className="h-4 w-4 opacity-60" />
    </SelectPrimitive.Icon>
  </SelectPrimitive.Trigger>
));
SelectTrigger.displayName = "SelectTrigger";

const SelectContent = React.forwardRef<
  React.ElementRef<typeof SelectPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitive.Content>
>(({ className, children, position = "item-aligned", ...props }, ref) => (
  <SelectPrimitive.Portal>
    <SelectPrimitive.Content
      ref={ref}
      position={position}
      sideOffset={6}
      // 🔴 2026-10-03 修「能点开但选项不显示/错位」（用户实测，仅采集悬浮窗）：
      //   ① 默认 `position="popper"` 依赖 @floating-ui **测量 trigger 尺寸**，
      //      而 trigger 位于 `overflow-y-auto` 滚动容器内（ModalBody）——
      //      Portal 把内容挂到 body 后，测量基准与滚动容器脱节 ⇒ 定位失准。
      //   ② `item-aligned` 直接用 trigger 的**几何盒**定位，不依赖测量，
      //      跨滚动容器稳定。
      //   ③ popper 模式还需 `--radix-select-trigger-width/height` 才能对齐宽度；
      //      未设时宽度回退到内容宽度 ⇒ 「位置对但宽度不对」。
      //   ⚠️ 默认值改动影响**全项目**所有 Select，故保留 `position` 可覆盖。
      className={cn(
        "relative z-[var(--z-popover)] max-h-96 min-w-[var(--radix-select-trigger-width,8rem)] overflow-hidden",
        "rounded-[12px] popover-surface p-1",
        position === "popper" && "translate-y-1",
        className
      )}
      {...props}
    >
      <SelectPrimitive.Viewport
        className="p-0"
        // popper 模式下用 CSS 变量把 viewport 宽度对齐 trigger，避免横向滚动条
        style={position === "popper" ? {
          width: "var(--radix-select-trigger-width)",
          minWidth: "var(--radix-select-trigger-width)",
        } : undefined}
      >
        {children}
      </SelectPrimitive.Viewport>
    </SelectPrimitive.Content>
  </SelectPrimitive.Portal>
));
SelectContent.displayName = "SelectContent";

const SelectItem = React.forwardRef<
  React.ElementRef<typeof SelectPrimitive.Item>,
  React.ComponentPropsWithoutRef<typeof SelectPrimitive.Item>
>(({ className, children, ...props }, ref) => (
  <SelectPrimitive.Item
    ref={ref}
    className={cn(
      "relative flex w-full cursor-pointer select-none items-center",
      "rounded-[8px] py-2 pl-8 pr-3 text-[0.82rem] text-[var(--color-text)]",
      "outline-none transition-colors",
      "focus:bg-[var(--color-surface-raised)]",
      "data-[disabled]:pointer-events-none data-[disabled]:opacity-50",
      className
    )}
    {...props}
  >
    <span className="absolute left-2.5 flex h-4 w-4 items-center justify-center">
      <SelectPrimitive.ItemIndicator>
        <Check className="h-3.5 w-3.5 text-[var(--color-accent)]" />
      </SelectPrimitive.ItemIndicator>
    </span>
    <SelectPrimitive.ItemText>{children}</SelectPrimitive.ItemText>
  </SelectPrimitive.Item>
));
SelectItem.displayName = "SelectItem";

export { Select, SelectValue, SelectTrigger, SelectContent, SelectItem };
