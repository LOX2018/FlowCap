/**
 * Skeleton —— 对标 better-douyin `components/ui/skeleton.tsx`
 */
import { cn } from "@/lib/utils";

function Skeleton({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "animate-pulse rounded-[var(--radius-sm)] bg-[var(--color-surface-raised)]",
        className
      )}
      {...props}
    />
  );
}

export { Skeleton };
