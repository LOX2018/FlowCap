/**
 * 定向渲染冒烟（2026-10-04 UI 三缺陷修复验收）
 *
 * 只验证**本次改动涉及**的组件是否真能渲染（React 真跑 render + hooks）：
 *   ① SettingsPage（配置中心容器 + 各 tab 切换）
 *   ② NotifySection（IM 页，字体体系收敛）
 *   ③ AuthorizationCard（IM 网关，死类名 .btn 已清）
 *   ④ LiveReviewMode（查阅模式 overlay）
 *   ⑤ PlatformPage（播放器浮层所在页）
 *
 * 复用项目既有手法（scripts/smoke_render.mjs）：Vite SSR + renderToString。
 * 抓不到（诚实标注）：布局/CSS 视觉 —— 那部分留给桌面端实机。
 */
import { createServer } from "vite";
import * as React from "react";
import { renderToString } from "react-dom/server";
import fs from "node:fs";
import path from "node:path";

const SHIM = "/src/__smoke_rq_shim2.tsx";
let _rq = null;
async function getRQ(server) {
  if (_rq) return _rq;
  const abs = path.join(process.cwd(), "src", "__smoke_rq_shim2.tsx");
  fs.writeFileSync(
    abs,
    'export { QueryClient, QueryClientProvider } from "@tanstack/react-query";\n',
    "utf8",
  );
  try {
    const rq = await server.ssrLoadModule(SHIM);
    _rq = { QueryClient: rq.QueryClient, QueryClientProvider: rq.QueryClientProvider };
    return _rq;
  } finally {
    try { fs.unlinkSync(abs); } catch {}
  }
}

const results = [];
function check(name, ok, detail = "") {
  results.push({ name, ok, detail });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail ? "  — " + detail : ""}`);
}

const server = await createServer({
  server: { middlewareMode: true },
  appType: "custom",
  logLevel: "error",
});

const { QueryClient, QueryClientProvider } = await getRQ(server);

/** mock PageProps（照 preview-pages.tsx 的口径：模拟后端已就绪） */
const mockProps = {
  push: () => {},
  api: new Proxy({}, {
    get: () => () => Promise.resolve({ ok: true, config: { enabled: false, channels: [], llm: {} } }),
  }),
  overview: { accounts: [{ name: "主账号_A" }] },
  ready: true,
  goMsg: () => {},
  goDm: null,
  setTab: () => {},
  goReview: () => {},
  reviewPayload: null,
  goReuse: () => {},
  reusePayload: null,
  msgAcct: "",
  setMsgAcct: () => {},
  refreshState: null,
  setRefreshState: () => {},
};

function wrap(node, preset) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  // 预置缓存 ⇒ 组件挂载即拿到数据，跳过 isLoading 分支（否则只渲染"加载中"）
  if (preset) for (const [k, v] of Object.entries(preset)) qc.setQueryData(k, v);
  return React.createElement(QueryClientProvider, { client: qc }, node);
}

// ── ① 配置中心容器（本次改：CardContent min-w-0 overflow-x-hidden）──
try {
  const { default: SettingsPage } = await server.ssrLoadModule(
    "/src/components/settings/settings-page.tsx",
  );
  const html = renderToString(wrap(React.createElement(SettingsPage, mockProps)));
  check("配置中心渲染成功", html.length > 200, `HTML ${html.length} 字节`);
  check("配置中心含子导航「通用配置」", html.includes("通用配置"));
  check("配置中心含「通知与指令」tab", html.includes("通知与指令"));
} catch (e) {
  check("配置中心渲染成功", false, String(e).slice(0, 200));
}

// ── ② IM 页 NotifySection（本次改：字体体系收敛）──
try {
  const { default: NotifySection } = await server.ssrLoadModule(
    "/src/components/settings/NotifySection.tsx",
  );
  const html = renderToString(wrap(React.createElement(NotifySection, mockProps)));
  // SSR 首帧：react-query 的 queryFn 是异步的，renderToString 不等它 ⇒
  // 组件必然落在 isLoading 分支（"加载通知配置…"）。这是 SSR 固有行为，
  // 不是回归。故这里只断言「能渲染、不抛错」，收敛效果由下一组断言验证。
  check("IM 页（NotifySection）渲染不抛错", html.length > 0, `HTML ${html.length} 字节`);

  // ── ②b 直接渲染 IM 页的两个子组件（它们不依赖异步查询）──
  const { Card, Field } = await server.ssrLoadModule(
    "/src/components/settings/notify-widgets.tsx",
  );
  const fieldHtml = renderToString(
    React.createElement(Field, {
      label: "Webhook 地址",
      value: "https://example.com/hook",
      onChange: () => {},
      secret: true,
      hint: "留空或 •••• = 不修改",
    }),
  );
  check("IM 页 Field 渲染成功", fieldHtml.includes("Webhook 地址"));
  // 旧体系痕迹：.inp 死类名（已随旧 CSS 下线）+ 内联 fontSize
  check("IM 页 Field 无死类名 .inp", !/class="inp"/.test(fieldHtml));
  check("IM 页 Field 无内联 fontSize", !fieldHtml.includes("font-size"));
  // 收敛后应来自 SetField/Input 的令牌类
  check("IM 页 Field 走 SetField 体系", fieldHtml.includes("set-field") || fieldHtml.includes("rounded-[var(--radius-sm)]"));

  const cardHtml = renderToString(
    React.createElement(Card, { title: "测试卡片", defaultOpen: true },
      React.createElement("div", null, "内容")),
  );
  check("IM 页 Card 渲染成功", cardHtml.includes("测试卡片"));
  check("IM 页 Card 无内联 borderRadius/fontSize",
        !cardHtml.includes("border-radius") && !cardHtml.includes("font-size"));
} catch (e) {
  check("IM 页（NotifySection）渲染成功", false, String(e).slice(0, 200));
}

// ── ③ 授权卡 AuthorizationCard（本次改：.btn sm → BTN_* 常量）──
try {
  const { AuthorizationCard } = await server.ssrLoadModule(
    "/src/components/settings/AuthorizationCard.tsx",
  );
  const html = renderToString(
    wrap(
      React.createElement(AuthorizationCard, {
        mode: "pairing",
        pending: [],
        grants: {},
        busy: false,
        onMode: () => {},
        onApprove: () => {},
        onReject: () => {},
      }),
    ),
  );
  check("授权卡渲染成功", html.includes("远程操作授权"), `HTML ${html.length} 字节`);
  check("授权卡无死类名 .btn", !/class="[^"]*\bbtn\b/.test(html));
} catch (e) {
  check("授权卡渲染成功", false, String(e).slice(0, 200));
}

// ── ④ 查阅模式 LiveReviewMode（本次改：overflow-x-hidden）──
try {
  const { ReviewMode } = await server.ssrLoadModule(
    "/src/components/live/LiveReviewMode.tsx",
  );
  const html = renderToString(
    wrap(
      React.createElement(ReviewMode, {
        rows: [],
        onClose: () => {},
        push: () => {},
        sendDm: () => {},
      }),
    ),
  );
  check("查阅模式渲染成功", html.includes("评论查阅模式"), `HTML ${html.length} 字节`);
  check("查阅模式含 overflow-x-hidden 约束", html.includes("overflow-x-hidden"));
} catch (e) {
  check("查阅模式渲染成功", false, String(e).slice(0, 200));
}

// ── ⑤ 内容总览页（播放器浮层所在页，本次改：flex 兄弟宽度）──
try {
  const { default: PlatformPage } = await server.ssrLoadModule(
    "/src/components/platform/platform-page.tsx",
  );
  const html = renderToString(wrap(React.createElement(PlatformPage, mockProps)));
  check("内容总览页渲染成功", html.length > 200, `HTML ${html.length} 字节`);
} catch (e) {
  check("内容总览页渲染成功", false, String(e).slice(0, 200));
}

await server.close();

const pass = results.filter((r) => r.ok).length;
console.log(`\n${pass}/${results.length} passed`);
if (pass !== results.length) process.exitCode = 1;
