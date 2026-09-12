/**
 * 玻璃开关 —— 移植自 zn0wii/satelite-proxy（src/components/GlassSwitch.tsx +
 * GlassSwitchControl.tsx，此处合并为自包含实现，保留同名 class 与 props）。
 *
 * 磨砂胶囊轨道 + 滑动磨砂滑块，与顶栏 / GlassSeg 同材质。
 * 关闭时滑块是纯玻璃；开启时染上主色，让「开」仍读作 primary 而不脱离玻璃观感。
 *
 * `capsule`：把标签 + 轨道包进同一枚磨砂胶囊（像一个能切换的 GlassButton）。
 *
 * 首帧 `no-anim`：持久化值晚到时，滑块不能从兜底位置滑向真实位置。
 */
import type { ReactNode } from "react";
import { useEffect, useRef, useState } from "react";

export type GlassSwitchSize = "md" | "sm";

/** 跟踪「首帧是否允许过渡」：ready 后连续两帧才开启动画。 */
function useGlassSwitchThumb(checked: boolean, ready: boolean) {
  const [animate, setAnimate] = useState(false);
  const lastChecked = useRef(checked);

  useEffect(() => {
    if (!ready) {
      setAnimate(false);
      return;
    }
    let b = 0;
    const a = requestAnimationFrame(() => {
      b = requestAnimationFrame(() => setAnimate(true));
    });
    return () => {
      cancelAnimationFrame(a);
      cancelAnimationFrame(b);
    };
  }, [ready]);

  useEffect(() => {
    lastChecked.current = checked;
  }, [checked]);

  return { animate, markUserChange: () => setAnimate(true) };
}

function GlassSwitchTrack({
  checked,
  size = "md",
  animate = true,
}: {
  checked: boolean;
  size?: GlassSwitchSize;
  animate?: boolean;
}) {
  return (
    <span
      className={`glass-switch-track ${size}${checked ? " on" : ""}${
        animate ? "" : " no-anim"
      }`}
      aria-hidden="true"
    >
      <span className="glass-switch-thumb" />
    </span>
  );
}

interface Props {
  checked: boolean;
  onChange: (next: boolean) => void;
  /** 内联标签，渲染在开关旁。 */
  label?: ReactNode;
  /** 无障碍标签 / 提示。 */
  title?: string;
  disabled?: boolean;
  /** 标签 + 开关包进同一枚磨砂胶囊。默认 false（裸轨道）。 */
  capsule?: boolean;
  /** 轨道尺寸：md（默认 40×22）或 sm（32×18）。 */
  size?: GlassSwitchSize;
  /** 父级仍在加载初始持久化值时为 false。 */
  ready?: boolean;
}

export function GlassSwitch({
  checked,
  onChange,
  label,
  title,
  disabled,
  capsule = false,
  size = "md",
  ready = true,
}: Props) {
  const { animate, markUserChange } = useGlassSwitchThumb(checked, ready);

  if (capsule) {
    return (
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        className={`glass-btn glass-switch-capsule${checked ? " on" : ""}${
          animate ? "" : " no-anim"
        }`}
        title={title}
        disabled={disabled}
        onClick={() => {
          markUserChange();
          onChange(!checked);
        }}
      >
        {label != null && <span className="glass-switch-label">{label}</span>}
        <GlassSwitchTrack checked={checked} size={size} animate={animate} />
      </button>
    );
  }

  return (
    <label className={`glass-switch-row${disabled ? " disabled" : ""}`} title={title}>
      {label != null && <span className="glass-switch-label">{label}</span>}
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={title}
        className="glass-switch"
        disabled={disabled}
        onClick={() => {
          markUserChange();
          onChange(!checked);
        }}
      >
        <GlassSwitchTrack checked={checked} size={size} animate={animate} />
      </button>
    </label>
  );
}
