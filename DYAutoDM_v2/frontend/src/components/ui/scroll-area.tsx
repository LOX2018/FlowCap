/**
 * ScrollArea —— 对标 better-douyin（Radix ScrollArea 封装）
 */
import * as React from "react";
import * as ScrollAreaPrimitive from "@radix-ui/react-scroll-area";
import { cn } from "@/lib/utils";

const ScrollArea = React.forwardRef<
  React.ElementRef<typeof ScrollAreaPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof ScrollAreaPrimitive.Root>
>(({ className, children, ...props }, ref) => (
  <ScrollAreaPrimitive.Root
    ref={ref}
    className={cn("relative overflow-hidden", className)}
    {...props}
  >
    <ScrollAreaPrimitive.Viewport className="h-full w-full rounded-[inherit]">
      {children}
    </ScrollAreaPrimitive.Viewport>
    <ScrollAreaPrimitive.Scrollbar
      orientation="vertical"
      // 2026-09-17 修补（OCR 审查 HIGH —— 垂直轨道缺高度约束）：
      // Radix 要求垂直方向 Scrollbar 带 `h-full`，否则轨道无法撑开、
      // Thumb（flex-1）按比例布局失效 → 滚动条不显示/拖不动。
      className="flex h-full w-[6px] touch-none select-none p-[1px] transition-colors"
    >
      <ScrollAreaPrimitive.Thumb className="relative flex-1 rounded-full bg-[rgba(128,128,128,0.2)]" />
    </ScrollAreaPrimitive.Scrollbar>
  </ScrollAreaPrimitive.Root>
));
ScrollArea.displayName = "ScrollArea";

export { ScrollArea };
