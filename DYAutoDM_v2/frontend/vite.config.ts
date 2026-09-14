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
