/**
 * Card 族 —— 对标 better-douyin `components/ui/card.tsx`
 */
import * as React from "react";
import { cn } from "@/lib/utils";

function Card({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        // 2026-10-02 重设计：材质收敛到 .card-surface（令牌驱动，昼/夜自动适配）。
        // 原实现为「80% 实底 + 极弱阴影」⇒ 浅色主题下是「浅灰底上一块纯白」，
        // 无层次、显廉价。现改为微透明底 + 发丝线 + 顶部内高光 + 有分量投影。
        "card-surface rounded-[var(--radius-md)]",
        "transition-[background-color,border-color,box-shadow,transform,opacity]",
        "duration-[var(--duration-base)] ease-[var(--ease-spring)]",
        className
      )}
      {...props}
    />
  );
}

function CardHeader({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "flex items-center justify-between p-4 border-b border-[var(--color-border)]",
        className
      )}
      {...props}
    />
  );
}

function CardTitle({ className, ...props }: React.HTMLAttributes<HTMLHeadingElement>) {
  return (
    <h3
      className={cn(
        "text-[0.9rem] font-semibold tracking-tight text-[var(--color-text)]",
        className
      )}
      {...props}
    />
  );
}

function CardDescription({ className, ...props }: React.HTMLAttributes<HTMLParagraphElement>) {
  return (
    <p
      className={cn("text-[0.8125rem] text-[var(--color-text-secondary)]", className)}
      {...props}
    />
  );
}

function CardContent({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return <div className={cn("p-4", className)} {...props} />;
}

function CardFooter({ className, ...props }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "flex items-center p-4 pt-0 border-t border-[var(--color-border)]",
        className
      )}
      {...props}
    />
  );
}

export { Card, CardHeader, CardTitle, CardDescription, CardContent, CardFooter };
