/**
 * `AppearanceSection` —— 外观设置卡：日夜主题切换（ADR-018 F6）
 *
 * ## 为什么新增（唯一的真实缺口）
 *
 * 主题引擎本身**早已完整**：`src/theme/ThemeContext.tsx` 已提供
 * `ThemeProvider` / `useTheme()` / `setTheme()` / `applyThemeToDom()` 与
 * localStorage 持久化（key `dy.theme`），`main.tsx` 也已用 `<ThemeProvider>`
 * 包裹整棵组件树。缺的只有一件事：**没有任何用户可点击的入口** —— 当前要换
 * 主题只能改代码。本组件补的就是这个入口。
 *
 * ## 设计契约
 *
 * 1. **不改 ThemeContext.tsx** —— 它是移植自上游 satelite-proxy 的资产，
 *    保持原样便于对账溯源；本组件只做**调用方**。
 * 2. **复用既有 ui 组件库**（项目铁律）—— 控件用 `@/components/ui/switch`
 *    （Radix Switch 封装，全项目 6 处在用），卡片骨架用
 *    `components/page/set-card` 的 SetCard 家族（与 McpSection 同源）。
 *    不自研弹层/控件。
 * 3. **当前态一眼可见** —— 开关旁直接写「日间 / 夜间」，不靠用户去推
 *    「checked 到底是亮还是暗」。
 * 4. **只管主题，不管主色/氛围光** —— accent / glow 不在本卡暴露（无 UI 需求），
 *    避免与 ThemeContext 的其它 setter 混成一团。
 */
import { Moon, Sun } from "lucide-react";
import { useTheme } from "@/theme/ThemeContext";
import { Switch } from "@/components/ui/switch";
import { SetCard, SetCardHead, SetCardBody } from "@/components/page/set-card";
import { cn } from "@/lib/utils";

export default function AppearanceSection() {
  const { theme, setTheme } = useTheme();
  /** ThemeId = "day" | "aerospace"；aerospace = 暗色（项目默认）。 */
  const isDay = theme === "day";

  return (
    <SetCard>
      <SetCardHead
        title="外观"
        description="日夜主题切换（点击立即生效，重启后保留）"
        right={
          <span
            className={cn(
              "rounded-full px-2.5 py-[3px] text-[0.72rem] font-medium",
              isDay
                ? "bg-[var(--color-warning-soft)] text-[var(--color-warning)]"
                : "bg-[var(--color-accent-soft)] text-[var(--color-accent)]",
            )}
          >
            {isDay ? "日间" : "夜间"}
          </span>
        }
      />
      <SetCardBody>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="text-[0.8rem] font-medium text-[var(--color-text)]">
              日夜主题
            </div>
            <div className="mt-0.5 text-[0.72rem] leading-relaxed text-[var(--color-text-muted)]">
              开启 = 日间（亮色）；关闭 = 夜间（航天暗色，项目默认）。
              选择写入 localStorage（<code>dy.theme</code>），下次启动自动恢复。
            </div>
          </div>

          <label
            className="flex shrink-0 cursor-pointer items-center gap-2
                       text-[0.74rem] text-[var(--color-text-secondary)]"
          >
            <Moon className={cn("h-3.5 w-3.5", !isDay && "text-[var(--color-accent)]")} />
            <Switch
              checked={isDay}
              onCheckedChange={(checked) => setTheme(checked ? "day" : "aerospace")}
              aria-label="日夜主题切换"
            />
            <Sun className={cn("h-3.5 w-3.5", isDay && "text-[var(--color-accent)]")} />
            <span className="w-[2.5rem] text-[var(--color-text)]">
              {isDay ? "日间" : "夜间"}
            </span>
          </label>
        </div>
      </SetCardBody>
    </SetCard>
  );
}
