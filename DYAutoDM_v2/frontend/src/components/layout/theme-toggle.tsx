/**
 * `ThemeToggleButton` —— 顶栏日夜主题快捷切换（ADR-018 F6 附属）
 *
 * ## 定位（刻意降级为「快捷方式」）
 *
 * 主入口在**配置中心 → 通用配置 → 外观**（`AppearanceSection`）。本按钮只是把
 * 同一次 `setTheme()` 提到顶栏，省去「进设置页 → 找开关」两步 **F6 里唯一有
 * 状态语义的入口仍是设置页**，这里不引入任何新状态、不读后端、不写 localStorage
 * （持久化由 ThemeContext.setTheme → applyThemeToDom → persist 负责）。
 *
 * ## 复用约束
 *
 * 按钮用 `@/components/ui/button`（CVA 变体体系，项目既有），图标用 lucide
 * 的 Sun / Moon；尺寸覆盖成顶栏既有的 7×8 规格，与「最小化/最大化/关闭」同高。
 */
import { Moon, Sun } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useTheme } from "@/theme/ThemeContext";

export function ThemeToggleButton({ className }: { className?: string }) {
  const { theme, setTheme } = useTheme();
  const isDay = theme === "day";

  return (
    <Button
      variant="ghost"
      size="icon-sm"
      onClick={() => setTheme(isDay ? "aerospace" : "day")}
      title={isDay ? "切换到夜间主题" : "切换到日间主题"}
      aria-label={isDay ? "切换到夜间主题" : "切换到日间主题"}
      className={
        "h-7 w-8 rounded-[7px] text-[var(--color-text-secondary)] " +
        "hover:bg-[var(--color-surface-raised)] hover:text-[var(--color-text)] " +
        (className ?? "")
      }
    >
      {isDay ? <Moon className="h-3.5 w-3.5" /> : <Sun className="h-3.5 w-3.5" />}
    </Button>
  );
}
