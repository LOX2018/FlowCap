/**
 * 入口 —— 在 React Query 之上再包一层 ThemeProvider（设计风格移植层）。
 *
 * 样式导入顺序很重要：
 *   1. global.css   —— 项目既有样式（885 行，22 个页面依赖）
 *   2. theme-glass.css —— 移植层，靠后导入才能覆盖上面需要换材质的规则
 */
import React from "react";
import ReactDOM from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import App from "./App";
import { ThemeProvider } from "./theme/ThemeContext";
import "./styles/global.css";
import "./styles/theme-glass.css";

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
    <QueryClientProvider client={queryClient}>
      <ThemeProvider>
        <App />
      </ThemeProvider>
    </QueryClientProvider>
  </React.StrictMode>
);
