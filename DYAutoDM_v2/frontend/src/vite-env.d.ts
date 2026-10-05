/// <reference types="vite/client" />

/**
 * 应用版本号（构建期由 vite.config.ts 从 package.json 注入）。
 *
 * 2026-08-31：用于在界面左上角显示当前版本。排查「改了代码但界面没变」
 * 这类问题时，第一件事就是确认跑的是哪个版本 —— 之前因为看不到版本号，
 * 反复误判为「前端缓存问题」，浪费了不少来回。
 */
declare const __APP_VERSION__: string;
