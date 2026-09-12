/**
 * Accent / 氛围光引擎 —— 移植自 zn0wii/satelite-proxy（src/theme/accents.ts）。
 *
 * 改造点（对齐本项目既有变量命名，避免 22 个页面全量改名）：
 *   --primary        → --accent        （本项目一直用 --accent）
 *   --primary-hover  → --accent-hover  （新增）
 *   --primary-muted  → --accent-bg     （本项目已有该语义）
 *   --primary-glow   → --accent-glow   （新增）
 *   --primary-border → --accent-border （新增）
 *   --on-primary     → --accent-ink    （本项目已有该语义）
 *   --glow-rgb / --glow-deep-rgb 保持原名（氛围光专用）
 *
 * 语义保护：--ok / --warn / --danger 不受 accent 影响（源项目同样刻意不动
 * --success），保证「成功/警告/危险」在任何配色下含义稳定。
 */

export type ThemeId = "day" | "aerospace";

/** Accent 预设：一个品牌主色，分别给出亮/暗两套色阶。 */
export interface AccentPreset {
  id: string;
  /** 展示名（色块 title）。 */
  name: string;
  /** 暗色（aerospace）主题基准 hex —— 通常更浅更像马卡龙。 */
  aerospace: string;
  /** 亮色（day）主题基准 hex —— 通常更深以保证对比度。 */
  day: string;
}

/**
 * 马卡龙色系预设。第一项（green）为默认值，其 aerospace 色沿用本项目改造前
 * 的 oklch(76% 0.16 145) ≈ #68cb6e，因此老用户默认观感零变化。
 */
export const ACCENTS: AccentPreset[] = [
  { id: "green", name: "薄荷", aerospace: "#68cb6e", day: "#1f9a72" },
  { id: "blue", name: "天蓝", aerospace: "#6bb6e8", day: "#2e86c8" },
  { id: "purple", name: "香芋", aerospace: "#b19cd9", day: "#8e5bb8" },
  { id: "pink", name: "蜜桃", aerospace: "#f4a6b8", day: "#d65a7e" },
  { id: "orange", name: "奶橙", aerospace: "#f5b97a", day: "#d88a3d" },
  { id: "cyan", name: "湖蓝", aerospace: "#7ad7d7", day: "#2fa9a9" },
];

export const DEFAULT_ACCENT = "green";

export function defaultAccent(): string {
  return DEFAULT_ACCENT;
}

/** 是否为「自定义 hex」色（#rrggbb）。 */
export function isCustomHexAccent(id: string | null | undefined): boolean {
  return !!id && /^#[0-9a-f]{6}$/i.test(id.trim());
}

/** 把存储的 accent id 解析成预设；自定义 hex 解析为「同色双主题」的虚拟预设。 */
export function resolveAccent(id: string | null | undefined): AccentPreset {
  if (typeof id === "string" && isCustomHexAccent(id)) {
    const hex = id.trim().toLowerCase();
    return { id: hex, name: "自定义", aerospace: hex, day: hex };
  }
  return ACCENTS.find((a) => a.id === id) ?? ACCENTS[0];
}

/** 是否为合法 accent（预设 id 或自定义 hex）。 */
export function isValidAccent(id: string | null | undefined): id is string {
  return isCustomHexAccent(id) || (!!id && ACCENTS.some((a) => a.id === id));
}

/** '#rrggbb' → { r, g, b }（0–255）。非法输入返回 null。 */
export function hexToRgb(hex: string): { r: number; g: number; b: number } | null {
  const m = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!m) return null;
  const n = parseInt(m[1], 16);
  return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
}

/** { r, g, b } → '#rrggbb'（小写）。 */
export function rgbToHex(r: number, g: number, b: number): string {
  const c = (v: number) =>
    Math.max(0, Math.min(255, Math.round(v)))
      .toString(16)
      .padStart(2, "0");
  return `#${c(r)}${c(g)}${c(b)}`;
}

/** RGB → HSV。h ∈ [0,360)，s/v ∈ [0,1]。 */
export function rgbToHsv(
  r: number,
  g: number,
  b: number,
): { h: number; s: number; v: number } {
  const rr = r / 255,
    gg = g / 255,
    bb = b / 255;
  const max = Math.max(rr, gg, bb),
    min = Math.min(rr, gg, bb);
  const d = max - min;
  let h = 0;
  if (d > 0) {
    if (max === rr) h = 60 * (((gg - bb) / d) % 6);
    else if (max === gg) h = 60 * ((bb - rr) / d + 2);
    else h = 60 * ((rr - gg) / d + 4);
  }
  if (h < 0) h += 360;
  return { h, s: max === 0 ? 0 : d / max, v: max };
}

/** HSV → RGB。h ∈ [0,360)，s/v ∈ [0,1]。 */
export function hsvToRgb(
  h: number,
  s: number,
  v: number,
): { r: number; g: number; b: number } {
  const c = v * s;
  const hp = (((h % 360) + 360) % 360) / 60;
  const x = c * (1 - Math.abs((hp % 2) - 1));
  let r = 0,
    g = 0,
    b = 0;
  if (hp < 1) [r, g, b] = [c, x, 0];
  else if (hp < 2) [r, g, b] = [x, c, 0];
  else if (hp < 3) [r, g, b] = [0, c, x];
  else if (hp < 4) [r, g, b] = [0, x, c];
  else if (hp < 5) [r, g, b] = [x, 0, c];
  else [r, g, b] = [c, 0, x];
  const m = v - c;
  return {
    r: Math.round((r + m) * 255),
    g: Math.round((g + m) * 255),
    b: Math.round((b + m) * 255),
  };
}

/** 感知亮度（Rec. 709 权重），0–1。 */
function luminanceOf(r: number, g: number, b: number): number {
  return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255;
}

/** 依据亮度决定主色上的前景文字（黑/白），保证所有预设下都清晰。 */
function onColorFor(r: number, g: number, b: number): string {
  return luminanceOf(r, g, b) > 0.6 ? "#0c1210" : "#ffffff";
}

/**
 * 自定义 hex 没有「每主题色阶」（预设才有亮/暗两版），直接用在暗底上可能出现
 * 深色字压深色玻璃。故把**实际生效**的颜色往当前主题的可读亮度带里推 ——
 * 存储的 id 保持原样，预设色由设计师调过、直接跳过此步。
 */
function clampForTheme(
  r: number,
  g: number,
  b: number,
  theme: ThemeId,
): { r: number; g: number; b: number } {
  const step = 0.08;
  if (theme === "day") {
    for (let i = 0; i < 24 && luminanceOf(r, g, b) > 0.6; i++) {
      r *= 1 - step;
      g *= 1 - step;
      b *= 1 - step;
    }
  } else {
    for (let i = 0; i < 24 && luminanceOf(r, g, b) < 0.5; i++) {
      r += (255 - r) * step;
      g += (255 - g) * step;
      b += (255 - b) * step;
    }
  }
  return { r: Math.round(r), g: Math.round(g), b: Math.round(b) };
}

/**
 * 把 accent 写进 :root 的 CSS 变量，UI 整体换肤。
 * 半透明派生量（bg/glow/border）由基准 hex 用 rgba() 现算。
 * 主题或 accent 变化时都要调用。
 */
export function applyAccentToDom(
  accentId: string | null | undefined,
  theme: ThemeId,
): void {
  const preset = resolveAccent(accentId);
  const base = hexToRgb(preset[theme]);
  if (!base) return;
  let { r, g, b } = base;
  if (isCustomHexAccent(accentId)) {
    ({ r, g, b } = clampForTheme(r, g, b, theme));
  }
  const rgb = (a: number) => `rgba(${r}, ${g}, ${b}, ${a})`;

  // hover：向白提亮 ~12%，色块预览够用。
  const t = 0.12;
  const hv = {
    r: Math.round(r + (255 - r) * t),
    g: Math.round(g + (255 - g) * t),
    b: Math.round(b + (255 - b) * t),
  };

  const root = document.documentElement.style;
  root.setProperty("--accent", rgbToHex(r, g, b));
  root.setProperty("--accent-hover", `rgb(${hv.r}, ${hv.g}, ${hv.b})`);
  root.setProperty("--accent-bg", rgb(0.14));
  root.setProperty("--accent-glow", rgb(theme === "day" ? 0.2 : 0.28));
  root.setProperty("--accent-border", rgb(0.35));
  root.setProperty("--accent-border-strong", rgb(theme === "day" ? 0.5 : 0.55));
  root.setProperty("--accent-ink", onColorFor(r, g, b));
}

/** 合法氛围光取值："accent"（跟随 UI 主色）或任一 accent id。 */
export function isValidGlow(id: string | null | undefined): id is string {
  return id === "accent" || isValidAccent(id);
}

/** 解析存储的氛围光 id，非法回落 "accent"（跟随）。 */
export function normalizeGlowId(id: string | null | undefined): string {
  return isValidGlow(id) ? (id as string) : "accent";
}

/**
 * 把 rgb 往黑压到目标感知亮度（混黑对亮度是线性缩放），只压不提 ——
 * 本来就够暗的颜色原样通过。
 */
function deepenToLuminance(
  r: number,
  g: number,
  b: number,
  target: number,
): { r: number; g: number; b: number } {
  const lum = luminanceOf(r, g, b);
  const k = lum > target && lum > 0 ? target / lum : 1;
  return {
    r: Math.round(r * k),
    g: Math.round(g * k),
    b: Math.round(b * k),
  };
}

/**
 * 发布 --glow-rgb / --glow-deep-rgb 给背景氛围光层用（页面大光晕 + 顶栏折射光）。
 * 与 accent 解耦：glowId 可为 "accent"（跟随）、任一预设 id，或自定义 hex。
 *
 * --glow-deep-rgb 是同色相按亮度归一化后的版本（暗 0.22 / 亮 0.50），
 * 让大面积底色无论选什么光色都保持一致的柔和亮度 —— 直接用马卡龙原色会让
 * 暗色模式整体明显发亮。
 */
export function applyGlowToDom(
  glowId: string | null | undefined,
  accentId: string | null | undefined,
  theme: ThemeId,
): void {
  const preset = resolveAccent(glowId && glowId !== "accent" ? glowId : accentId);
  const base = hexToRgb(preset[theme]);
  if (!base) return;
  const deep = deepenToLuminance(
    base.r,
    base.g,
    base.b,
    theme === "day" ? 0.5 : 0.22,
  );
  const root = document.documentElement.style;
  root.setProperty("--glow-rgb", `${base.r}, ${base.g}, ${base.b}`);
  root.setProperty("--glow-deep-rgb", `${deep.r}, ${deep.g}, ${deep.b}`);
}
