/**
 * Input / Textarea / Label —— 对标 better-douyin `components/ui/input.tsx`
 */
import * as React from "react";
import { cn } from "@/lib/utils";

const base =
  "w-full rounded-[10px] bg-[var(--color-surface)] text-[var(--color-text)] " +
  "text-[0.85rem] placeholder:text-[var(--color-text-muted)] " +
  "border border-[var(--color-border)] " +
  "transition-[border-color,box-shadow,background-color] duration-200 " +
  "focus:border-[var(--color-accent)] focus:outline-none " +
  "focus:ring-2 focus:ring-[var(--color-accent-ring)] " +
  "disabled:opacity-50 disabled:cursor-not-allowed";

// 2026-10-02 统一：输入框高度对齐按钮（h-9），全站表单控件同一规格。
const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(
  ({ className, ...props }, ref) => (
    <input ref={ref} className={cn(base, "h-9 px-3", className)} {...props} />
  )
);
Input.displayName = "Input";

const Textarea = React.forwardRef<
  HTMLTextAreaElement,
  React.TextareaHTMLAttributes<HTMLTextAreaElement>
>(({ className, ...props }, ref) => (
  <textarea
    ref={ref}
    className={cn(base, "min-h-[80px] px-3 py-2 resize-y leading-relaxed", className)}
    {...props}
  />
));
Textarea.displayName = "Textarea";

const Label = React.forwardRef<
  HTMLLabelElement,
  React.LabelHTMLAttributes<HTMLLabelElement>
>(({ className, ...props }, ref) => (
  <label
    ref={ref}
    className={cn(
      "text-[0.78rem] font-medium text-[var(--color-text-secondary)] select-none",
      className
    )}
    {...props}
  />
));
Label.displayName = "Label";

export { Input, Textarea, Label };
