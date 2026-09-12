/**
 * 亮/暗主题切换胶囊（☼ ☾）—— 移植自 zn0wii/satelite-proxy（src/components/ThemeSwitch.tsx）。
 * 复用 `topnav-theme-switch` / `topnav-theme-btn` 样式。
 */
import { useTheme } from "../theme/ThemeContext";

export function ThemeSwitch() {
  const { theme, setTheme } = useTheme();
  return (
    <div className="topnav-theme-switch" role="group" aria-label="外观">
      <button
        type="button"
        className={`topnav-theme-btn ${theme === "day" ? "active" : ""}`}
        aria-label="亮色模式"
        aria-pressed={theme === "day"}
        title="亮色"
        onClick={() => setTheme("day")}
      >
        ☼
      </button>
      <button
        type="button"
        className={`topnav-theme-btn ${theme === "aerospace" ? "active" : ""}`}
        aria-label="暗色模式"
        aria-pressed={theme === "aerospace"}
        title="暗色"
        onClick={() => setTheme("aerospace")}
      >
        ☾
      </button>
    </div>
  );
}

export default ThemeSwitch;
