/**
 * 玻璃胶囊按钮 —— 移植自 zn0wii/satelite-proxy（src/components/GlassButton.tsx）。
 * 与顶栏 / GlassSeg 指示器同一套「磨砂玻璃」材质语言。
 *
 * 渲染真实 <button className="glass-btn">；全局 button 重置规则已排除该类，
 * 不会被覆盖。
 */
import type { ButtonHTMLAttributes, ReactNode } from "react";

type Variant = "plain" | "primary" | "danger";

interface Props extends Omit<ButtonHTMLAttributes<HTMLButtonElement>, "className"> {
  /** 前置图标/字形（emoji 或短文本）。 */
  icon?: ReactNode;
  /** 视觉变体：primary = 主色着色玻璃，danger = 危险色着色。 */
  variant?: Variant;
  /** 只显示图标（无 children），内边距收紧。 */
  iconOnly?: boolean;
  /** 追加在根节点的类名，供局部布局微调。 */
  className?: string;
}

export function GlassButton({
  icon,
  variant = "plain",
  iconOnly = false,
  className,
  children,
  disabled,
  title,
  onClick,
  type = "button",
  ...rest
}: Props) {
  const cls = [
    "glass-btn",
    variant === "plain" ? "" : variant,
    iconOnly ? "icon-only" : "",
    className ?? "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <button
      {...rest}
      type={type}
      className={cls}
      title={title}
      disabled={disabled}
      onClick={onClick}
    >
      {icon ? (
        <span className="glass-btn-icon" aria-hidden>
          {icon}
        </span>
      ) : null}
      {children ? <span className="glass-btn-label">{children}</span> : null}
    </button>
  );
}
