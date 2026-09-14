/**
 * 入口 —— 在 React Query 之上再包一层 ThemeProvider（设计风格移植层）。
 *
 * ## 样式导入（2026-09-14 重设计后）
 *
 * 只剩两处：
 *   1. tokens.css —— Tailwind 4 + 设计令牌（对标 better-douyin）
 *   2. theme-glass.css 的玻璃工具类已并入 tokens.css（`.glass-premium` 等）
 *
 * 已下线（旧 CSS 体系，迁移完成后不再需要）：
 *   · global.css      —— 884 行手写 CSS（旧类名体系，已全部迁到设计令牌 + 组件族）
 *   · bridge.css      —— 旧变量名 → 新令牌的桥接层（无引用后同步下线）
 *
 * ⚠️ 若日后要回滚，见 `docs/frontend_redesign_report.md` 的迁移记录。
 */
import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { ThemeProvider } from "./theme/ThemeContext";
import "./styles/tokens.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      retry: 1,
      // 共享数据（账号/引擎/直播流）由 App 常驻轮询维护缓存；
      // 页面切换直接读缓存，20s 内不再向后端重拉（消灭切页等待）
      staleTime: 20000,
    },
  },
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <ThemeProvider>
      <QueryClientProvider client={queryClient}>
        <App />
      </QueryClientProvider>
    </ThemeProvider>
  </React.StrictMode>
);
