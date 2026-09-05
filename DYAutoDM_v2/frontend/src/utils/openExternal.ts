/**
 * 在系统默认浏览器里打开外部链接。
 *
 * ## 为什么不能直接用 <a href target="_blank">
 *
 * Tauri 应用的界面跑在 **WebView** 里，WebView **没有标签页 UI**
 * （不像 Chrome 那样有地址栏/标签栏）。因此在 WebView 中点击
 * `<a target="_blank">` 会**静默失败** —— 不报错，但也不打开浏览器。
 *
 * 这就是「查看原图点了没反应」的根因：链接本身是对的，
 * 但 WebView 不知道该拿它怎么办。
 *
 * ## 正确做法
 *
 * 用 `@tauri-apps/plugin-shell` 的 `open()`，它会调用系统默认浏览器。
 * 需要 `shell:allow-open` 权限（见 src-tauri/capabilities/default.json）。
 *
 * 2026-09-01：用户多次反馈「两处查看原图皆无效」，排查后确认
 * 是缺 open 权限 + 用了 <a target="_blank">，两处叠加导致点击无反应。
 */
export async function openExternal(url: string): Promise<boolean> {
  if (!url) return false;
  try {
    // 动态 import：Web 端（vite dev）没有 tauri 模块，不能静态引入
    const mod: any = await import("@tauri-apps/plugin-shell");
    const open = mod?.open;
    if (typeof open !== "function") {
      // 兜底：非 Tauri 环境（如浏览器预览）走 window.open
      window.open(url, "_blank", "noopener,noreferrer");
      return true;
    }
    await open(url);
    return true;
  } catch (e) {
    // 日志留在控制台，便于排查；同时兜底尝试 window.open
    console.warn("[openExternal] shell.open 失败，回退 window.open:", e);
    try {
      window.open(url, "_blank", "noopener,noreferrer");
      return true;
    } catch {
      return false;
    }
  }
}
