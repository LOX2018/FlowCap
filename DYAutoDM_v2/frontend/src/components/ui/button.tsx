/**
 * Button —— 对标 better-douyin `components/ui/button.tsx`
 *
 * 变体体系（variant × size）由 CVA 声明，与蓝本一致。
 * 差异：主色用本项目绿（--color-accent）。
 */
import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const buttonVariants = cva(
  [
    "inline-flex items-center justify-center gap-2 whitespace-nowrap",
    "text-[0.85rem] font-bold transition-[background-color,color,border-color,box-shadow,transform,opacity]",
    "duration-200 ease-[var(--ease-spring)] cursor-pointer select-none",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent-ring)]",
    "disabled:pointer-events-none disabled:opacity-40 active:scale-[0.96]",
  ].join(" "),
  {
    variants: {
      variant: {
        default:
          "bg-[var(--color-accent)] text-[#08130a] shadow-lg shadow-[color-mix(in_srgb,var(--color-accent)_20%,transparent)] hover:bg-[var(--color-accent-hover)]",
        secondary:
          "bg-[var(--color-surface-raised)] text-[var(--color-text)] shadow-[inset_0_0_0_1px_var(--color-border)] hover:bg-[var(--color-surface-solid)] hover:shadow-[inset_0_0_0_1px_var(--color-border-strong),0_10px_24px_rgba(0,0,0,0.08)]",
        outline:
          "bg-transparent text-[var(--color-text-secondary)] shadow-[inset_0_0_0_1px_var(--color-border)] hover:text-[var(--color-text)] hover:bg-[var(--color-surface-raised)]",
        ghost:
          "text-[var(--color-text-secondary)] hover:text-[var(--color-text)] hover:bg-[var(--color-surface-raised)]",
        danger:
          "bg-[var(--color-danger)] text-white shadow-lg hover:brightness-110",
        "danger-outline":
          "border border-[color-mix(in_srgb,var(--color-danger)_25%,transparent)] bg-[var(--color-danger-soft)] text-[var(--color-danger)] hover:bg-[var(--color-danger)] hover:text-white",
        "success-outline":
          "border border-[color-mix(in_srgb,var(--color-success)_25%,transparent)] bg-[var(--color-success-soft)] text-[var(--color-success)] hover:bg-[var(--color-success)] hover:text-white",
        "info-outline":
          "border border-[color-mix(in_srgb,var(--color-info)_25%,transparent)] bg-[var(--color-info-soft)] text-[var(--color-info)] hover:bg-[var(--color-info)] hover:text-white",
        link: "text-[var(--color-accent)] underline-offset-4 hover:underline",
      },
      size: {
        default: "h-10 px-5 rounded-[12px]",
        sm: "h-9 px-4 text-[0.78rem] rounded-[10px]",
        lg: "h-12 px-8 text-[0.95rem] rounded-[14px]",
        icon: "h-10 w-10 rounded-[12px]",
        "icon-sm": "h-9 w-9 rounded-[10px]",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  }
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return (
      <Comp
        className={cn(buttonVariants({ variant, size, className }))}
        ref={ref}
        {...props}
      />
    );
  }
);
Button.displayName = "Button";

export { Button, buttonVariants };
