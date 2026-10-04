/**
 * 任务中心页 SSR 冒烟（2026-10-04 三页签重构验收）
 *
 * ## 为什么不用浏览器
 * 本项目实测「每次启动浏览器都卡死」——浏览器验收不可用。但「tsc 过了就交付」
 * 违反活实例验证律。折中：用 **Vite SSR 加载器**把真实组件渲染成 HTML 字符串
 * —— 真渲染（React 真跑 render、hooks 真执行），能抓组件未定义 / hook 误用 /
 * props 契约错 / 渲染期抛错。
 *
 * ## 抓不到（诚实标注）
 * 布局/CSS 视觉、真实网络请求、**点击切页**（SSR 无 DOM，不能点击）。
 * ⇒ 本脚本用「预置 react-query 缓存」让异步数据在首渲染即可见，从而验证
 *    三个页签的**内容**都能渲染；但「点击页签真的切换」仍留给桌面端实机。
 */
import { createServer } from "vite";
import * as React from "react";
import { renderToString } from "react-dom/server";

const SHIM = "/src/__smoke_rq_shim.tsx";
async function getRQ(server) {
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
    return { QueryClient: rq.QueryClient, QueryClientProvider: rq.QueryClientProvider };
  } finally {
    try { fs.unlinkSync(abs); } catch { /* ignore */ }
  }
}

const results = [];
const push = (name, ok, note = "") => results.push([name, ok, note]);

async function main() {
  const server = await createServer({
    server: { middlewareMode: true },
    appType: "custom",
    logLevel: "error",
  });

  try {
    const { QueryClient, QueryClientProvider } = await getRQ(server);

    // ── ① 任务中心页（默认「运行任务」页签）──
    const mod = await server.ssrLoadModule("/src/components/tasks/tasks-page.tsx");
    const TasksPage = mod.default;

    const liveTask = {
      running: true,
      status: "2026-10-04 14:00:00",
      acct: "工伤小助理",
      liveUrl: "https://live.douyin.com/12345",
      engineState: "running",
      paused: false,
      sent: 12,
      limit: 50,
      queue: 3,
    };
    const crawlTask = {
      id: "ct_1",
      account: "采集账号A",
      aweme_ids: ["a", "b", "c"],
      phase: "collect",
      done: 1,
      total: 3,
      ok_works: 1,
      fail_works: 0,
      created_at: 1759500000,
      updated_at: 1759500000,
      status: "running",
      error: "",
    };
    const historyItem = {
      id: 7,
      acct: "历史账号",
      live_id: "999",
      start_ts: "2026-10-03 09:00:00",
      end_ts: "2026-10-03 09:30:00",
      status: "finished",
      result_count: 20,
      records: [],
      config: {},
    };

    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    // 预置缓存：让 useQuery 在 SSR 首渲染即拿到数据（否则 data=undefined，异步行不渲染）
    qc.setQueryData(["crawl-tasks-page"], {
      ok: true,
      storage: "memory(process-level, lost on restart)",
      count: 1,
      tasks: [crawlTask],
    });
    qc.setQueryData(["task-history", 0], { ok: true, list: [historyItem], total: 1 });
    // 两个直播账号（ADR-002 §5.2 多实例）⇒ 验证任务中心能列出**全部**运行任务
    qc.setQueryData(["engine-accounts"], {
      ok: true,
      items: [
        { acct: "工伤小助理", state: "running", live_url: "https://live.douyin.com/12345", sent: 12 },
        { acct: "张老师", state: "paused", live_url: "https://live.douyin.com/67890", sent: 3 },
      ],
    });

    const props = {
      ready: true,
      push: () => {},
      setTab: () => {},
      goReview: () => {},
      goReuse: () => {},
      overview: liveTask,
      api: {
        exportStats: async () => ({ ok: true, path: "x" }),
        getTaskHistory: async () => ({ ok: true, list: [historyItem], total: 1 }),
        crawlTasks: async () => ({
          ok: true,
          storage: "memory(process-level, lost on restart)",
          count: 1,
          tasks: [crawlTask],
        }),
        listEngineAccounts: async () => ({
          ok: true,
          items: [
            { acct: "工伤小助理", state: "running", live_url: "https://live.douyin.com/12345", sent: 12 },
            { acct: "张老师", state: "paused", live_url: "https://live.douyin.com/67890", sent: 3 },
          ],
        }),
        crawlTaskDelete: async () => ({ ok: true, deleted: true }),
        crawlTasksClear: async () => ({ ok: true, removed: 0, remaining: 0 }),
        stopEngine: async () => ({}),
        pauseEngine: async () => ({}),
        resumeEngine: async () => ({}),
        clearTaskHistory: async () => ({ ok: true }),
      },
    };

    const html = renderToString(
      React.createElement(QueryClientProvider, { client: qc }, React.createElement(TasksPage, props)),
    );

    push("任务中心渲染成功", html.length > 0, `HTML ${html.length} 字节`);
    // 三页签结构
    push("含「运行任务」页签", html.includes("运行任务"), "");
    push("含「历史任务」页签", html.includes("历史任务"), "");
    push("含「定时任务」页签", html.includes("定时任务"), "");
    // 运行任务混排：直播 + 采集
    push("运行任务含「直播监听」行", html.includes("直播监听"), "");
    push("运行任务含「评论采集」行", html.includes("评论采集"), "");
    push("采集进度渲染（采集中）", html.includes("采集中"), "");
    push("直播账号渲染", html.includes("工伤小助理"), "");
    push("采集账号渲染", html.includes("采集账号A"), "");
    // 默认在运行页签 ⇒ 不应出现历史表的空态/「开始时间」列头
    push("默认页签非历史（无「开始时间」列头）", !html.includes("开始时间"), "");
    // 多账号直播任务（ADR-002 §5.2）：两个账号都要出现
    push("多账号直播任务 #1 渲染", html.includes("工伤小助理"), "");
    push("多账号直播任务 #2 渲染", html.includes("张老师"), "");

    // ── ② 定时任务子页面组件独立渲染（休眠态如实呈现）──
    const schedMod = await server.ssrLoadModule("/src/components/tasks/SchedulerSection.tsx");
    const SchedulerSection = schedMod.default;
    const qc2 = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc2.setQueryData(["task-scheduler"], {
      ok: true,
      state: { scheduler_enabled: false, enabled: false, auto_send_enabled: false, task_count: 0 },
      tasks: [],
    });
    const schedHtml = renderToString(
      React.createElement(
        QueryClientProvider,
        { client: qc2 },
        React.createElement(SchedulerSection, {
          ready: true,
          push: () => {},
          api: {
            getScheduler: async () => ({
              ok: true,
              state: { scheduler_enabled: false, enabled: false, auto_send_enabled: false, task_count: 0 },
              tasks: [],
            }),
          },
        }),
      ),
    );
    push("定时任务组件渲染成功", schedHtml.length > 0, `HTML ${schedHtml.length} 字节`);
    push("定时任务含「定时任务中心」标题", schedHtml.includes("定时任务中心"), "");
    push("休眠态如实呈现", schedHtml.includes("休眠"), "");
  } finally {
    await server.close();
  }

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
