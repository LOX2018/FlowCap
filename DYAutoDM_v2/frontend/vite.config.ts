import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// 2026-08-31：把 package.json 的 version 注入前端，用于在界面上显示版本号。
// 排查「改了代码但界面没变」时，第一件事就是确认跑的是哪个版本。
import pkg from "./package.json";

// Tauri 期望前端 dev server 监听 1420 端口
export default defineConfig({
  define: {
    __APP_VERSION__: JSON.stringify(pkg.version),
  },
  plugins: [react(), tailwindcss()],
  // Tauri 用相对路径加载资源
  base: "./",
  server: {
    port: 1420,
    strictPort: true,
    // 通过环境变量区分 Tauri 模式
    host: "127.0.0.1",
  },
  build: {
    target: "esnext",
    outDir: "dist",
    sourcemap: false,
    // 2026-09-15 性能优化：把体积大、更新频率低的第三方库分成独立 chunk，
    // 使其 hash 稳定、可长期缓存 —— 业务代码变更时客户端无需重下 React/react-dom。
    // 注意：不要过度切分（每个 chunk 都有 HTTP 开销），只分真正的重量级依赖。
    rollupOptions: {
      output: {
        // 只把「首屏必用且体积大」的 React 生态拆出来缓存。
        // 注意：**不要把 radix 全量预载** —— 实测 vendor-radix 会达 229KB，
        // 而首屏仅用到少数几个，全量预载反而拖慢首帧。radix 交给页面 chunk 按需加载。
        // 只拆 react/react-dom（真正首屏必需、且极少变更）。
        // framer-motion 走完整包（5 处真实 import：accounts-page / AccountDrawer /
        // AccountReview / ProxyDrawer / LiveReviewMode），不再单独成 chunk。
        // 若强拆 vendor-motion 会把整包（~120KB）原样装进去，反而抵消减包。
        manualChunks: {
          "vendor-react": ["react", "react-dom"],
        },
      },
    },
    // 必须 true：否则每次构建生成的新 hash 文件名（index-XXXX.js）会永久堆积，
    // 曾堆到 43 个 / 17MB+，导致「源码改对、编译通过，但 exe 里打的是旧 JS」（08 §三十一）。
    // IDE safe-delete 拦截由 scripts/clean_dist.py 在构建前用 cmd /c rmdir 规避，
    // 不靠关掉 emptyOutDir 来绕。
    emptyOutDir: true,
  },
  resolve: {
    alias: {
      "@": "/src",
    },
  },
});
