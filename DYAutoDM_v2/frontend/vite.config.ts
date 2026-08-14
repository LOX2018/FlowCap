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
  },
  resolve: {
    alias: {
      "@": "/src",
    },
  },
});
