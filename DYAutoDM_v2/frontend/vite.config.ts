import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Tauri 期望前端 dev server 监听 1420 端口
export default defineConfig({
  plugins: [react()],
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
    // 禁用 vite 自动清空输出目录：避免 IDE safe-delete 批量删除拦截导致打包失败。
    // 清空动作由打包脚本在 tauri build 前用 cmd /c rmdir 完成。
    emptyOutDir: false,
  },
  resolve: {
    alias: {
      "@": "/src",
    },
  },
});
