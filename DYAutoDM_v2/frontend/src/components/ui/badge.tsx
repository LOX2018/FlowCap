/**
 * Badge —— 对标 better-douyin `components/ui/badge.tsx`
 */
import * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "@/lib/utils";

const badgeVariants = cva(
  "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-[0.72rem] font-semibold transition-colors duration-200",
  {
    variants: {
      variant: {
        default: "bg-[var(--color-subtle-bg)] text-[var(--color-text)]",
        accent: "bg-[var(--color-accent-soft)] text-[var(--color-accent)]",
        success: "bg-[var(--color-success-soft)] text-[var(--color-success)]",
        info: "bg-[var(--color-info-soft)] text-[var(--color-info)]",
        warning: "bg-[var(--color-warning-soft)] text-[var(--color-warning)]",
        danger: "bg-[var(--color-danger-soft)] text-[var(--color-danger)]",
        outline: "border border-[var(--color-border)] text-[var(--color-text-secondary)]",
      },
    },
    defaultVariants: { variant: "default" },
  }
);

export interface BadgeProps
  extends React.HTMLAttributes<HTMLSpanElement>,
    VariantProps<typeof badgeVariants> {}

function Badge({ className, variant, ...props }: BadgeProps) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}

export { Badge, badgeVariants };
