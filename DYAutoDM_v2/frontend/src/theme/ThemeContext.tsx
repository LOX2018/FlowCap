/**
 * 主题上下文 —— 移植自 zn0wii/satelite-proxy（src/theme/ThemeContext.tsx）。
 *
 * 与原版的差异：
 *   1. 原版把偏好同步到后端 settings API；本项目暂不扩后端，改为 **localStorage
 *      单一真相源**（改了立刻生效、重启保留），后续若要跟随后端设置，
 *      只需在下方 read* 里加一次 merge 即可。
 *   2. 默认主题 = "aerospace"（暗色），保证老用户升级后观感不变；
 *      原版默认是 "day"（亮色）。
 *   3. 保留原版的 `glassFrost`（玻璃磨砂开关）与 `data-glass-frost` 属性约定。
 */
import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import type { ThemeId } from "./accents";
import {
  applyAccentToDom,
  applyGlowToDom,
  normalizeGlowId,
  resolveAccent,
} from "./accents";

const THEME_KEY = "dy.theme";
const ACCENT_KEY = "dy.accent";
const GLOW_KEY = "dy.glow";
const FROST_KEY = "dy.glassFrost";

export function normalizeTheme(raw: string | null | undefined): ThemeId {
  const t = (raw ?? "").trim().toLowerCase();
  if (t === "day") return "day";
  return "aerospace";
}

export function readStoredTheme(): ThemeId {
  try {
    return normalizeTheme(localStorage.getItem(THEME_KEY));
  } catch {
    return "aerospace";
  }
}

export function readStoredAccent(): string {
  try {
    return resolveAccent(localStorage.getItem(ACCENT_KEY)).id;
  } catch {
    return "green";
  }
}

export function readStoredGlow(): string {
  try {
    return normalizeGlowId(localStorage.getItem(GLOW_KEY));
  } catch {
    return "accent";
  }
}

export function readStoredFrost(): boolean {
  try {
    return localStorage.getItem(FROST_KEY) !== "false";
  } catch {
    return true;
  }
}

function persist(theme: ThemeId, accent: string, glow: string) {
  try {
    localStorage.setItem(THEME_KEY, theme);
    localStorage.setItem(ACCENT_KEY, accent);
    localStorage.setItem(GLOW_KEY, glow);
  } catch {
    /* ignore */
  }
}

/** 把主题/主色/氛围光一次性写进 DOM（<html data-theme> + CSS 变量）。 */
export function applyThemeToDom(theme: ThemeId, accent: string, glow: string): void {
  document.documentElement.dataset.theme = theme;
  // 驱动原生 <select>/表单控件外观（WebView2 / WKWebView）。
  document.documentElement.style.colorScheme = theme === "day" ? "light" : "dark";
  applyAccentToDom(accent, theme);
  applyGlowToDom(glow, accent, theme);
  persist(theme, accent, glow);
}

/** 切换玻璃磨砂观感（<html data-glass-frost>）。 */
export function applyGlassFrostToDom(frost: boolean): void {
  if (frost) document.documentElement.dataset.glassFrost = "true";
  else delete document.documentElement.dataset.glassFrost;
  try {
    localStorage.setItem(FROST_KEY, frost ? "true" : "false");
  } catch {
    /* ignore */
  }
}

interface ThemeContextValue {
  theme: ThemeId;
  setTheme: (next: ThemeId) => void;
  accent: string;
  setAccent: (next: string) => void;
  glow: string;
  setGlow: (next: string) => void;
  glassFrost: boolean;
  setGlassFrost: (next: boolean) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemeId>(readStoredTheme);
  const [accent, setAccentState] = useState<string>(readStoredAccent);
  const [glow, setGlowState] = useState<string>(readStoredGlow);
  const [glassFrost, setFrostState] = useState<boolean>(readStoredFrost);

  // 首帧就把主题铺到 <html>，避免亮/暗自定义变量闪现（FOUC）。
  useEffect(() => {
    applyThemeToDom(theme, accent, glow);
    applyGlassFrostToDom(glassFrost);
    // 仅在挂载时执行一次；后续变更由各自的 setter 负责写 DOM。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const setTheme = useCallback(
    (next: ThemeId) => {
      setThemeState(next);
      applyThemeToDom(next, accent, glow);
    },
    [accent, glow],
  );

  const setAccent = useCallback(
    (next: string) => {
      const id = resolveAccent(next).id;
      setAccentState(id);
      applyThemeToDom(theme, id, glow);
    },
    [theme, glow],
  );

  const setGlow = useCallback(
    (next: string) => {
      const id = normalizeGlowId(next);
      setGlowState(id);
      applyGlowToDom(id, accent, theme);
      try {
        localStorage.setItem(GLOW_KEY, id);
      } catch {
        /* ignore */
      }
    },
    [accent, theme],
  );

  const setGlassFrost = useCallback((next: boolean) => {
    setFrostState(next);
    applyGlassFrostToDom(next);
  }, []);

  const value = useMemo<ThemeContextValue>(
    () => ({ theme, setTheme, accent, setAccent, glow, setGlow, glassFrost, setGlassFrost }),
    [theme, setTheme, accent, setAccent, glow, setGlow, glassFrost, setGlassFrost],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) {
    throw new Error("useTheme 必须在 <ThemeProvider> 内使用");
  }
  return ctx;
}
