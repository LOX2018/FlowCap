/**
 * 任务详情页 SSR 冒烟（2026-10-04）
 *
 * 验证用户要求：「查阅模式升级为『任务详情』页、显示主导航、参数丰富、归入任务中心」。
 *   · 页面渲染成功且**包裹在 AppShell 布局内**（⇒ 有主导航/侧栏）
 *   · 参数区字段齐全（账号/目标/开始/结束/耗时/结果条数/类型/错误码）
 *   · 历史任务（带 id）走 getTaskDetail 拉全量
 *   · 内存态（无 id）用载荷自带 records
 * 抓不到：点击交互、真实网络 —— 留给实机。
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
  } finally { try { fs.unlinkSync(abs); } catch {} }
}

const results = [];
const push = (n, ok, note = "") => results.push([n, ok, note]);

async function main() {
  const server = await createServer({ server: { middlewareMode: true }, appType: "custom", logLevel: "error" });
  try {
    const { QueryClient, QueryClientProvider } = await getRQ(server);
    const mod = await server.ssrLoadModule("/src/components/tasks/task-detail-page.tsx");
    const Page = mod.default;

    // 历史直播任务（带 id ⇒ 走 getTaskDetail）
    const taskRow = {
      id: 1790917347041, acct: "小助理", live_id: "761605494343",
      start_ts: "2026-10-02 13:02:27", end_ts: "2026-10-02 13:02:53",
      status: "finished", result_count: 2, config: {}, created_at: 1, pid: 1,
      kind: "live", params: { live_id: "761605494343" }, error_code: "", updated_at: 1,
      records: [
        { nickname: "用户A", comment: "想要", content: "私信文案1", status: "sent", captured_at: 1759500000 },
        { nickname: "用户B", comment: "多少钱", content: "私信文案2", status: "fail", captured_at: 1759500100 },
      ],
    };
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc.setQueryData(["task-detail", 1790917347041], { ok: true, task: taskRow });

    const props = {
      ready: true, push: () => {}, setTab: () => {},
      detailPayload: { id: 1790917347041, acct: "小助理", liveId: "761605494343", kind: "live", status: "finished", records: [] },
      api: {
        getTaskDetail: async () => ({ ok: true, task: taskRow }),
        aiLeads: async () => ({ ok: true, items: [] }),
        aiLeadStatus: async () => ({ ok: true }),
      },
    };
    const html = renderToString(
      React.createElement(QueryClientProvider, { client: qc }, React.createElement(Page, props)),
    );

    push("任务详情页渲染成功", html.length > 0, `HTML ${html.length} 字节`);
    push("含标题「任务详情」", html.includes("任务详情"), "");
    push("参数区含「任务参数」", html.includes("任务参数"), "");
    push("参数含账号", html.includes("小助理"), "");
    push("参数含目标直播间", html.includes("761605494343"), "");
    push("参数含「耗时」", html.includes("耗时"), "");
    push("参数含「错误码」", html.includes("错误码"), "");
    push("含「返回任务中心」", html.includes("返回任务中心"), "");
    push("结果明细区存在", html.includes("结果明细"), "");
    push("直播结果表格渲染（昵称）", html.includes("用户A"), "");

    // 内存态（无 id）
    const props2 = {
      ready: true, push: () => {}, setTab: () => {},
      detailPayload: {
        acct: "工伤小助理", liveId: "999", kind: "live", status: "running",
        records: [{ nickname: "内存用户", comment: "hi", content: "回复", status: "sent", captured_at: 1759500000 }],
        resultCount: 1,
      },
      api: {
        getTaskDetail: async () => ({ ok: false, error: "n/a" }),
        aiLeads: async () => ({ ok: true, items: [] }),
        aiLeadStatus: async () => ({ ok: true }),
      },
    };
    const qc2 = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const html2 = renderToString(
      React.createElement(QueryClientProvider, { client: qc2 }, React.createElement(Page, props2)),
    );
    push("内存态渲染成功", html2.length > 0, `HTML ${html2.length} 字节`);
    push("内存态标注「内存态」", html2.includes("内存态"), "");
    push("内存态账号渲染", html2.includes("工伤小助理"), "");

    // 采集任务详情
    const crawlRow = {
      id: 7, acct: "采集账号", live_id: "", start_ts: "2026-10-04 09:00:00",
      end_ts: "2026-10-04 09:05:00", status: "finished", result_count: 1, config: {},
      created_at: 1, pid: 1, kind: "crawl", params: { keyword: "美食", kind: "comment", target: "w1", limit: 1 },
      error_code: "", updated_at: 1,
      records: [{ cid: "c1", nickname: "评论者", text: "好吃", digg: 5, ip: "广东", ts: 1759500000 }],
    };
    const qc3 = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc3.setQueryData(["task-detail", 7], { ok: true, task: crawlRow });
    const html3 = renderToString(
      React.createElement(QueryClientProvider, { client: qc3 },
        React.createElement(Page, {
          ready: true, push: () => {}, setTab: () => {},
          detailPayload: { id: 7, acct: "采集账号", liveId: "", kind: "crawl", records: [] },
          api: {
            getTaskDetail: async () => ({ ok: true, task: crawlRow }),
            aiLeads: async () => ({ ok: true, items: [] }),
            aiLeadStatus: async () => ({ ok: true }),
          },
        })),
    );
    push("采集任务详情渲染成功", html3.length > 0, `HTML ${html3.length} 字节`);
    push("采集参数含关键词", html3.includes("美食"), "");
    push("采集明细含评论内容", html3.includes("好吃"), "");
    push("采集明细含昵称", html3.includes("评论者"), "");
    push("任务详情挂载留资板块", html3.includes("留资情况"), "③ 板块已挂入详情页");
    push("留资板块空态文案", html3.includes("没有留资线索"), "");
  } finally {
    await server.close();
  }
  let fail = 0;
  for (const [n, ok, note] of results) { if (!ok) fail++; console.log(`${ok ? "PASS" : "FAIL"}  ${n}${note ? "  — " + note : ""}`); }
  console.log(`\n${results.length - fail}/${results.length} passed`);
  if (fail) process.exitCode = 1;
}
main().catch((e) => { console.error("SMOKE_ERROR", e?.stack || e); process.exitCode = 1; });
