/**
 * 玻璃分段控件 —— 移植自 zn0wii/satelite-proxy（src/components/GlassSeg.tsx）。
 *
 * 选中项由一枚会滑动到目标位置的磨砂玻璃胶囊表示（与顶栏同材质）。
 *
 * 关键细节（源项目踩过的坑，务必保留）：
 *   受控组件的值变化有两条来源 —— 用户点击 / 父级加载或刷新状态。
 *   只有前者才应该「滑动」。持久化状态、轮询更新、选项列表变化都必须
 *   直接落位。故用 committedValueRef 跟踪「已提交」的值来判别，
 *   并在首帧（持久化值可能晚到）期间禁用过渡，否则每次回到页面都会看到
 *   胶囊从兜底项滑向真实保存项。
 */
import type { CSSProperties, ReactNode } from "react";
import { useEffect, useRef, useState } from "react";

interface Option {
  value: string;
  /** ReactNode —— 可在文字旁内嵌状态点等。 */
  label: ReactNode;
}

interface Props {
  value: string;
  options: Option[];
  onChange: (value: string) => void;
  ariaLabel?: string;
  disabled?: boolean;
  /** 按项禁用（如无节点时禁用 TUN）。 */
  disabledValues?: Set<string>;
  /** 按项气泡提示。 */
  titles?: Record<string, string>;
  /** 父级仍在加载初始持久化值时为 false。 */
  ready?: boolean;
}

export function GlassSeg({
  value,
  options,
  onChange,
  ariaLabel,
  disabled,
  disabledValues,
  titles,
  ready = true,
}: Props) {
  const index = Math.max(0, options.findIndex((o) => o.value === value));

  const committedValueRef = useRef(value);
  const committedIndexRef = useRef(index);
  const pendingUserValueRef = useRef<string | null>(null);
  const positionChanged =
    committedValueRef.current !== value || committedIndexRef.current !== index;
  const isUserChange = pendingUserValueRef.current === value;

  // 首帧抑制过渡：持久化值可能远晚于挂载才到。
  const [canAnimate, setCanAnimate] = useState(false);
  useEffect(() => {
    if (!ready) {
      setCanAnimate(false);
      return;
    }
    // 两个 rAF 确保「无过渡的目标状态」已实际绘制，之后才允许用户变更时的滑动。
    let nextRaf = 0;
    const paintRaf = requestAnimationFrame(() => {
      nextRaf = requestAnimationFrame(() => setCanAnimate(true));
    });
    return () => {
      cancelAnimationFrame(paintRaf);
      cancelAnimationFrame(nextRaf);
    };
  }, [ready]);

  useEffect(() => {
    committedValueRef.current = value;
    committedIndexRef.current = index;
    if (pendingUserValueRef.current === value) {
      pendingUserValueRef.current = null;
    }
  }, [index, value]);

  const animateIndicator = canAnimate && (!positionChanged || isUserChange);

  return (
    <div
      className="glass-seg"
      role="group"
      aria-label={ariaLabel}
      style={{ "--count": options.length } as CSSProperties}
    >
      <span
        className={`glass-seg-indicator${animateIndicator ? "" : " no-anim"}`}
        aria-hidden="true"
        style={{ transform: `translateX(${index * 100}%)` }}
      />
      {options.map((o) => {
        const isDisabled = disabled || disabledValues?.has(o.value);
        return (
          <button
            key={o.value}
            type="button"
            className={`glass-seg-btn ${value === o.value ? "active" : ""}`}
            disabled={isDisabled}
            title={titles?.[o.value]}
            onClick={() => {
              pendingUserValueRef.current = o.value;
              onChange(o.value);
            }}
          >
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
