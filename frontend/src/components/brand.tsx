/**
 * 品牌标识 —— 单一真源（加载页 / 登录页 / 侧栏 / 关于页共用）
 *
 * 2026-10-02：用户要求「应用 logo 使用 D:\下载\川流.png」，且实测反馈
 * 「加载页名称和 logo 没变」—— 原 BootSplash/MemberGate/Sidebar 三处各自
 * 用**绿色方块占位**、文案各写一份（「DY」「抖音数据控制台」），改名时必然漏。
 * 现收敛到本组件：换 logo 只改此文件一处，品牌名同理。
 */
import chuanliuLogo from "@/assets/chuanliu-logo.png";
import { cn } from "@/lib/utils";

export const BRAND_NAME = "川流";

export function BrandMark({
  size = 28,
  rounded = 8,
  className,
}: {
  size?: number;
  rounded?: number;
  className?: string;
}) {
  return (
    <img
      src={chuanliuLogo}
      alt={BRAND_NAME}
      width={size}
      height={size}
      draggable={false}
      className={cn("shrink-0 select-none object-cover", className)}
      style={{ width: size, height: size, borderRadius: rounded }}
    />
  );
}
