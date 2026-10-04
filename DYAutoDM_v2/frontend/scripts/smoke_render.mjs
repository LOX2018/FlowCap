/**
 * 非浏览器渲染冒烟（ADR-033 验收用）
 *
 * ## 为什么不用浏览器
 * 本项目实测「每次启动浏览器都卡死」——浏览器验收不可用。
 * 但「只在 tsc 通过就交付」违反活实例验证律（静态分析 ≠ 真能渲染）。
 *
 * 折中：用 **Vite SSR 加载器**把真实组件渲染成 HTML 字符串。
 * 它是真渲染（React 真的跑了 render、hooks 真的执行），只是宿主是 Node
 * 而不是浏览器 —— 能抓到：组件未定义、hook 误用、props 契约错、渲染期抛错。
 *
 * 抓不到（诚实标注）：布局/CSS 视觉、真实网络请求、点击交互。
 * 那部分留给桌面端实机（用户自己开 app）。
 */
import { createServer } from "vite";
import * as React from "react";
import { renderToString } from "react-dom/server";


/**
 * 取「与组件同一份模块实例」的 react-query。
 *
 * 直接 `ssrLoadModule("@tanstack/react-query")` 会让 Vite 走 externalize →
 * 拿到 CJS 副本，其 context 与组件内 ESM 那套 **不是同一个** ⇒
 * `No QueryClient set`。改为从**项目内**已被组件 import 的模块图取样：
 * 先加载一个真实组件（它的 import 已解析到同一实例），再从它的依赖里取。
 */
/**
 * 取「与组件同一份模块实例」的 react-query。
 *
 * 直接 `ssrLoadModule("@tanstack/react-query")` 会让 Vite 走 externalize →
 * 拿到 CJS 副本，其 context 与组件内 ESM 那套 **不是同一个** ⇒
 * `No QueryClient set`。改为：运行时**临时生成**一个位于 src 下的薄壳
 * re-export 文件（走项目内解析 ⇒ 同一实例），跑完立即删除。
 *
 * 🔴 为什么临时生成而不是常驻一份 shim：常驻文件一旦被误删/未提交，
 *    本门禁会**静默不可重跑**（而它上次报过绿）—— 比直接失败更危险。
 *    自生成 + 自清理 ⇒ 脚本自足，任何时候跑都成立。
 */
const SHIM = "/src/__smoke_rq_shim.tsx";
async function getRQ(server) {
  if (_rqCache.has("x")) return _rqCache.get("x");
  const fs = await import("node:fs");
  const path = await import("node:path");
  const abs = path.join(process.cwd(), "src", "__smoke_rq_shim.tsx");
  fs.writeFileSync(
    abs,
    'export { QueryClient, QueryClientProvider } from "@tanstack/react-query";\n',
    "utf8",
  );
  try {
    const rq = await server.ssrLoadModule(SHIM);
    const out = {
      QueryClient: rq.QueryClient,
      QueryClientProvider: rq.QueryClientProvider,
    };
    _rqCache.set("x", out);
    return out;
  } finally {
    // 注意：必须在 Vite **已解析完模块**后删；文件删除不影响已加载模块
    try { fs.unlinkSync(abs); } catch { /* ignore */ }
  }
}
const _rqCache = new Map();

const results = [];

async function main() {
  const server = await createServer({
    server: { middlewareMode: true },
    appType: "custom",
    logLevel: "error",
  });

  try {
    // ── ① 导航栏：必须含「统计」、必须不含「内容」入口 ──
    const sidebarMod = await server.ssrLoadModule(
      "/src/components/layout/sidebar.tsx",
    );
    const NAV_GROUPS = sidebarMod.NAV_GROUPS;
    const allItems = NAV_GROUPS.flatMap((g) => g.items.map((i) => i.label));
    results.push([
      "导航含「统计」",
      allItems.includes("统计"),
      `实际: ${allItems.join("/")}`,
    ]);
    results.push([
      "导航已去除「内容」",
      !allItems.includes("内容"),
      `实际: ${allItems.join("/")}`,
    ]);
    const statsItem = NAV_GROUPS.flatMap((g) => g.items).find(
      (i) => i.label === "统计",
    );
    results.push(["统计项 id = stats", statsItem?.id === "stats", String(statsItem?.id)]);
    // 资产组原位（用户在 clarify 里拍板：替换「内容」原本的位置）
    const assetGroup = NAV_GROUPS.find((g) => g.title === "资产");
    results.push([
      "统计位于「资产」组",
      !!assetGroup?.items.some((i) => i.id === "stats"),
      `资产组: ${assetGroup?.items.map((i) => i.label).join("/")}`,
    ]);

    // ── ② 统计页：真实渲染（注入最小 props）──
    const statsMod = await server.ssrLoadModule(
      "/src/components/stats/stats-page.tsx",
    );
    const StatsPage = statsMod.default;

    // 最小 PageProps 替身：只提供本页渲染期真正用到的字段
    const fakeProps = {
      ready: true,
      push: () => {},
      api: {
        crawlStats: async () => ({
          ok: true,
          tz: 8,
          days: 7,
          date: "2026-10-03",
          total: { runs: 12, results: 340 },
          today: { runs: 3, results: 88 },
          kinds: { video: { runs: 8, results: 200 }, comment: { runs: 4, results: 140 } },
          top_keywords: [{ keyword: "美食", runs: 5, results: 150, last_ts: 0 }],
          trend: [
            { date: "2026-10-02", runs: 1, results: 10 },
            { date: "2026-10-03", runs: 3, results: 88 },
          ],
          sink: { total: 42, sent: 7 },
          recent: [{ id: 1, account: "a", kind: "video", keyword: "美食", target: "", result_count: 20, ts: "2026-10-03 10:00:00" }],
        }),
        getOverviewFunnel: async () => ({
          ok: true, date: "2026-10-03", tz: 8, is_today: true, latest_day: "2026-10-03",
          crawl: { today_runs: 3, today_results: 88, kinds: {} },
          sink: { today_new: 5, today_sent: 2, total: 42, total_sent: 7, sources: {} },
          messages: { today_theirs: 0, raw_me_rows: 0 },
          dm: { today_sent: 15, rejected: 3 },
          leads: { today: 1, total: 9 },
          accounts: { total: 4, active: 3, credential_ok: 2 },
        }),
      },
    };

    // react-query 需要 Provider
    const { QueryClient, QueryClientProvider, make } = await getRQ(server);
    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });

    const html = renderToString(
      React.createElement(
        QueryClientProvider,
        { client: qc },
        React.createElement(StatsPage, fakeProps),
      ),
    );
    results.push(["统计页渲染成功", html.length > 0, `HTML ${html.length} 字节`]);
    results.push(["统计页有标题「统计」", html.includes("统计"), ""]);
    results.push([
      "统计页标注单位「条」",
      html.includes("条"),
      "",
    ]);
    results.push([
      "统计页标注单位「人」",
      html.includes("人"),
      "",
    ]);
    results.push([
      "总览漏斗区块存在",
      html.includes("总览漏斗"),
      "",
    ]);
    results.push([
      "口径说明存在（禁混算）",
      html.includes("不可相加") || html.includes("口径说明"),
      "",
    ]);

    // ── ③ 采集页：含两个 tab（采集 / 内容浏览）──
    const crawlMod = await server.ssrLoadModule(
      "/src/components/crawl/crawl-page.tsx",
    );
    const CrawlPage = crawlMod.default;
    const crawlProps = {
      ready: true,
      push: () => {},
      api: {
        getAccounts: async () => [{ name: "acct1", loggedIn: true }],
        crawlSearch: async () => ({ items: [], total: 0 }),
        crawlComments: async () => ({ items: [], total: 0 }),
        crawlCommentsBatch: async () => ({ per_work: [], ok_works: 0, works: 0, total_comments: 0 }),
        crawlCommentsBatchCancel: async () => ({}),
        crawlCommentsAnonPreview: async () => ({ per_work: [] }),
        crawlDm: async () => ({ ok: true }),
        crawlDmBatch: async () => ({ results: [], candidates: 0, sent_ok: 0, sent_fail: 0, rate_limited: 0 }),
        getConfig: async () => ({ config: {} }),
      },
    };
    const { QueryClient: QC2, QueryClientProvider: P2 } = await getRQ(server);
    const qc2 = new QC2({ defaultOptions: { queries: { retry: false } } });
    const crawlHtml = renderToString(
      React.createElement(
        P2,
        { client: qc2 },
        React.createElement(CrawlPage, crawlProps),
      ),
    );
    results.push(["采集页渲染成功", crawlHtml.length > 0, `HTML ${crawlHtml.length} 字节`]);
    results.push([
      "采集页含「内容浏览」tab",
      crawlHtml.includes("内容浏览"),
      "",
    ]);
    results.push(["采集页保留搜索框", crawlHtml.includes("搜索视频关键词"), ""]);

    // ── ④ PlatformPage 契约：embedded 时不套 PageContainer ──
    const platMod = await server.ssrLoadModule(
      "/src/components/platform/platform-page.tsx",
    );
    const PlatformPage = platMod.default;
    // embedded 且无账号 → 应只返回 EmptyState（不渲染 PageHeader 标题「内容浏览」）
    const { QueryClient: QC3, QueryClientProvider: P3 } = await getRQ(server);
    const qc3 = new QC3({ defaultOptions: { queries: { retry: false } } });
    const embHtml = renderToString(
      React.createElement(
        P3,
        { client: qc3 },
        React.createElement(PlatformPage, {
          ready: true, push: () => {},
          api: { getAccounts: async () => [] },
          embedded: true, account: "", accounts: [],
        }),
      ),
    );
    results.push([
      "embedded 模式不渲染「内容浏览」页头",
      !embHtml.includes("内容浏览"),
      embHtml.includes("还没有账号") ? "（已降级为无账号提示，符合预期）" : embHtml.slice(0, 80),
    ]);
  } finally {
    await server.close();
  }

  // ── 输出 ──
  let fail = 0;
  for (const [name, ok, note] of results) {
    if (!ok) fail++;
    console.log(`${ok ? "PASS" : "FAIL"}  ${name}${note ? "  — " + note : ""}`);
  }
  console.log(`\n${results.length - fail}/${results.length} passed`);
  if (fail) process.exitCode = 1;
}

main().catch((e) => {
  console.error("SMOKE_ERROR", e?.stack || e);
  process.exitCode = 1;
});
