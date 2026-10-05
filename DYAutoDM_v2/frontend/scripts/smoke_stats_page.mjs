/**
 * 统计页 SSR 冒烟（ADR-035 §3.4 下游，2026-10-04）
 *
 * 真渲染（Vite SSR），验证：
 *   · 统计页渲染成功
 *   · 新增「任务表统计」区存在
 *   · 「直播」域改为读任务表（不再拿采集数据冒充）
 * 抓不到：布局/CSS、点击交互 —— 留给桌面端实机。
 */
import { createServer } from "vite";
import * as React from "react";
import { renderToString } from "react-dom/server";

const SHIM = "/src/__smoke_rq_shim.tsx";
async function getRQ(server) {
  const fs = await import("node:fs");
  const path = await import("node:path");
  const abs = path.join(process.cwd(), "src", "__smoke_rq_shim.tsx");
  fs.writeFileSync(abs, 'export { QueryClient, QueryClientProvider } from "@tanstack/react-query";\n', "utf8");
  try {
    const rq = await server.ssrLoadModule(SHIM);
    return { QueryClient: rq.QueryClient, QueryClientProvider: rq.QueryClientProvider };
  } finally {
    try { fs.unlinkSync(abs); } catch { /* ignore */ }
  }
}

const results = [];
const push = (n, ok, note = "") => results.push([n, ok, note]);

async function main() {
  const server = await createServer({ server: { middlewareMode: true }, appType: "custom", logLevel: "error" });
  try {
    const { QueryClient, QueryClientProvider } = await getRQ(server);
    const mod = await server.ssrLoadModule("/src/components/stats/stats-page.tsx");
    const StatsPage = mod.default;

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc.setQueryData(["crawl-stats", 7], {
      ok: true, tz: 8, days: 7, date: "2026-10-04",
      total: { runs: 12, results: 340 }, today: { runs: 3, results: 88 },
      kinds: { comment: { runs: 4, results: 140 } },
      top_keywords: [], trend: [{ date: "2026-10-04", runs: 3, results: 88 }],
      sink: { total: 42, sent: 7 }, recent: [],
    });
    qc.setQueryData(["overview-funnel", true], {
      ok: true, date: "2026-10-04", tz: 8, is_today: true, latest_day: "2026-10-04",
      crawl: { today_runs: 3, today_results: 88, kinds: {} },
      sink: { today_new: 5, today_sent: 2, total: 42, total_sent: 7, sources: {} },
      messages: { today_theirs: 0, raw_me_rows: 0 },
      dm: { today_sent: 15, rejected: 3 }, leads: { today: 1, total: 9 },
      accounts: { total: 4, active: 3, credential_ok: 2 },
    });
    // 任务表统计：直播 3 次 / 采集 5 次 / 定时 2 次
    qc.setQueryData(["task-stats", 7], {
      ok: true, tz: 8, days: 7, date: "2026-10-04",
      total: { runs: 10, results: 120 }, today: { runs: 4, results: 40 },
      kinds: {
        live: { runs: 3, results: 30 },
        crawl: { runs: 5, results: 80 },
        scheduled: { runs: 2, results: 10 },
      },
      status: { ok: 7, failed: 2, running: 1 },
      trend: [{ date: "2026-10-04", runs: 4, results: 40 }],
      recent: [
        { id: 3, account: "工伤小助理", kind: "live", status: "finished", result_count: 12,
          start_ts: "2026-10-04 10:00:00", end_ts: "2026-10-04 10:30:00", error_code: "", live_id: "735" },
        { id: 2, account: "采集账号A", kind: "crawl", status: "finished", result_count: 8,
          start_ts: "2026-10-04 09:00:00", end_ts: "2026-10-04 09:05:00", error_code: "", live_id: "" },
      ],
    });

    const props = {
      ready: true, push: () => {},
      api: {
        crawlStats: async () => qc.getQueryData(["crawl-stats", 7]),
        getOverviewFunnel: async () => qc.getQueryData(["overview-funnel", true]),
        taskStats: async () => qc.getQueryData(["task-stats", 7]),
        crawlHistory: async () => ({ ok: true, items: [] }),
      },
    };

    const html = renderToString(
      React.createElement(QueryClientProvider, { client: qc }, React.createElement(StatsPage, props)),
    );

    push("统计页渲染成功", html.length > 0, `HTML ${html.length} 字节`);
    push("含标题「统计」", html.includes("统计"), "");
    push("含「任务表统计」区（新增）", html.includes("任务表统计"), "");
    push("含「全域概览」", html.includes("全域概览"), "");
    push("直播域读任务表（显示 3 次监听）", html.includes("累计监听 3 次"), "");
    push("直播域不再拿采集数据冒充", !html.includes("评论采集（直播域）"), "");
    push("任务类型分布含「直播监听」", html.includes("直播监听"), "");
    push("任务类型分布含「定时任务」", html.includes("定时任务"), "");
    push("含任务成功/失败", html.includes("成功 / 失败"), "");
  } finally {
    await server.close();
  }
  let fail = 0;
  for (const [n, ok, note] of results) { if (!ok) fail++; console.log(`${ok ? "PASS" : "FAIL"}  ${n}${note ? "  — " + note : ""}`); }
  console.log(`\n${results.length - fail}/${results.length} passed`);
  if (fail) process.exitCode = 1;
}

main().catch((e) => { console.error("SMOKE_ERROR", e?.stack || e); process.exitCode = 1; });
