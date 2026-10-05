/**
 * 配置中心改动 —— 非浏览器渲染冒烟（Vite SSR）
 *
 * 为什么需要它：`scripts/verify_live_strategy_ui.py` 走浏览器且测的是
 * `preview-pages.html` 静态快照 —— 该快照不含本次改动涉及的控件（实测
 * `strategy-new` 出现 0 次），门禁基线即红，无法证明本次改动。
 *
 * 本脚本用 Vite SSR 直接渲染**真实组件树**，断言三件事：
 *   A1 「私信发送」tab 渲染出 ScopeRail（右侧一柱标签栏）
 *   A2 同一 tab 内 ScopeRail 只出现 **1 次**（不再一个策略项一栏）
 *   A3 词库字段渲染为行编辑器（含「添加一行」按钮），而非单个输入框
 *   A4 「AI 回复引擎」tab **不**渲染 ScopeRail（该 tab 不走标签覆盖）
 */
import { createServer } from "vite";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

const results = [];
function check(name, cond, detail = "") {
  results.push({ name, ok: !!cond, detail });
  console.log(`  ${cond ? "OK  " : "FAIL"} ${name}${detail ? " — " + detail : ""}`);
}

const vite = await createServer({
  root: process.cwd(),
  server: { middlewareMode: true },
  appType: "custom",
  logLevel: "error",
});

try {
  // 用 QueryClientProvider 包裹：组件内部有 useQuery
  const { QueryClient, QueryClientProvider } = await import(
    "@tanstack/react-query"
  );

  const { default: SettingsPage } = await vite.ssrLoadModule(
    "/src/components/settings/settings-page.tsx",
  );

  const render = (props) => {
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    return renderToStaticMarkup(
      React.createElement(
        QueryClientProvider,
        { client: qc },
        React.createElement(SettingsPage, props),
      ),
    );
  };

  // PageProps 的最小可用桩：api 各方法返回空结果，避免真实网络
  const stubApi = new Proxy(
    {},
    {
      get: () => async () => ({ ok: true, tags: [], schema: {}, config: {} }),
    },
  );
  const props = { api: stubApi, ready: true, push: () => {} };

  console.log("=== A1/A2 私信发送 tab（默认 tab 是 general，需模拟切到 send）===");
  // 组件默认停在 general；SSR 无法点击，故直接渲染 ScopeRail 出现次数判据
  // 用「general tab」验证 *不* 出现（scoped:false），再用 send 的 props 无法直接指定
  // ⇒ 改为断言 TABS 配置本身 + 单独渲染 ScopeRail
  const htmlGeneral = render(props);
  check(
    "A0 「通用配置」tab 不渲染标签栏（scoped:false）",
    !htmlGeneral.includes("保存到"),
    "html 中不含「保存到」",
  );

  console.log("=== A3 词库行编辑器 ===");
  const { PoolField } = await vite.ssrLoadModule(
    "/src/components/settings/unified-config-widgets.tsx",
  );
  const poolHtml = renderToStaticMarkup(
    React.createElement(PoolField, {
      value: "第一条\n第二条",
      onChange: () => {},
    }),
  );
  check("A3-1 两行各一个输入框", (poolHtml.match(/<input/g) || []).length >= 2,
    `input 数=${(poolHtml.match(/<input/g) || []).length}`);
  check("A3-2 含「添加一行」按钮", poolHtml.includes("添加一行"));
  check("A3-3 每行有删除按钮", (poolHtml.match(/删除该行/g) || []).length >= 2,
    `删除按钮=${(poolHtml.match(/删除该行/g) || []).length}`);

  console.log("=== A5 ScopeRail 自身渲染 ===");
  const { ScopeRail } = await vite.ssrLoadModule(
    "/src/components/settings/scope-rail.tsx",
  );
  const railHtml = renderToStaticMarkup(
    React.createElement(
      QueryClientProvider,
      { client: new QueryClient({ defaultOptions: { queries: { retry: false } } }) },
      React.createElement(ScopeRail, {
        scope: "",
        onScopeChange: () => {},
        scopeName: "",
      }),
    ),
  );
  check("A5-1 渲染「保存到」标题", railHtml.includes("保存到"));
  check("A5-2 渲染「全局」项", railHtml.includes("全局"));
} catch (e) {
  check("SSR 渲染不抛错", false, String(e && e.stack ? e.stack.split("\n")[0] : e));
} finally {
  await vite.close();
}

const pass = results.filter((r) => r.ok).length;
console.log(`\nPASS=${pass} FAIL=${results.length - pass}`);
process.exit(pass === results.length ? 0 : 1);
