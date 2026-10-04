/**
 * 系统通知（以**应用身份**发出）。
 *
 * ## 为什么需要这个 hook
 *
 * 后端是 Python sidecar：**没有窗口、也没有已注册的 AppUserModelID** ⇒
 * 它自己发的 Windows Toast 只能借用别的 AppID，用户看到的是「终端」发的
 * 通知，而不是本应用发的。
 *
 * 应用身份的通知必须由 Tauri 前端发出 —— `tauri-plugin-notification`
 * 已在 `src-tauri/src/lib.rs:205` 注册，`capabilities/default.json` 已声明
 * `notification:default` 权限。故后端只负责**入队**，本 hook 轮询取走后
 * 以应用身份展示。
 *
 * ## 关键约定
 *
 * 1. **取走即清空**：后端 `/api/notify/pending` 每次返回后清空队列，
 *    故同一条通知不会被重复弹（否则轮询会反复打扰用户）。
 * 2. **权限只请求一次**：`isPermissionGranted()` 为 false 时才
 *    `requestPermission()`，不做无谓弹窗。
 * 3. **非 Tauri 环境静默**：浏览器里（npm run dev）没有 Tauri 插件，
 *    直接跳过，不报错、不阻塞。
 */
import { useEffect, useRef } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api/client";

/** 轮询间隔（ms）。凭证失效不是秒级事件，10s 足够及时且不打扰。 */
const POLL_MS = 10_000;

function inTauri(): boolean {
  return Boolean(
    (window as unknown as { __TAURI_INTERNALS__?: unknown }).__TAURI_INTERNALS__,
  );
}

export function useSystemNotices(enabled: boolean = true) {
  // 已弹过的 id（后端已清空，这里是二次保险：防 React Query 重放导致重弹）
  const shown = useRef<Set<string>>(new Set());

  const { data } = useQuery({
    queryKey: ["pending-notices"],
    queryFn: () => api.getPendingNotices(),
    refetchInterval: POLL_MS,
    enabled: enabled && inTauri(),
  });

  useEffect(() => {
    const items = data?.items ?? [];
    if (!items.length) return;
    let cancelled = false;

    (async () => {
      try {
        const notif = await import("@tauri-apps/plugin-notification");
        let permitted = await notif.isPermissionGranted();
        if (!permitted) {
          const p = await notif.requestPermission();
          permitted = p === "granted";
        }
        if (!permitted || cancelled) return;

        for (const n of items) {
          if (shown.current.has(n.id)) continue;
          shown.current.add(n.id);
          // 以**应用身份**发出（显示应用名与图标，而非终端）
          notif.sendNotification({ title: n.title, body: n.body });
        }
      } catch {
        // 非 Tauri 环境或插件不可用：静默跳过（通知是增强，不是主流程）
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [data]);
}
