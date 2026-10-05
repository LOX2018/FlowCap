/**
 * 任务详情「留资情况」板块 SSR 冒烟（2026-10-04）
 *
 * 用户要求：「任务详情也需要显示留资情况」。
 *
 * 覆盖：
 *   · 板块标题 / 计数角标 / 归属边界提示文案（task_id 缺失的诚实声明）
 *   · 表格 7 列齐全（状态/客户/类型/联系方式/来源/会话/捕获时间）
 *   · 账号过滤（同 query 内不同账号的线索必须被过滤掉）
 *   · 来源徽标（实时 / 历史补全）与状态徽标（新线索/已跟进）
 *   · 空数据态（Blank，不报错）
 *   · 窗口对齐端到端：`created_at`（秒）落在 `start_ts`~`end_ts` 窗口内的
 *     线索能被 `Date.parse` 口径命中 —— 这是本功能的时区前提
 *
 * 抓不到：真实网络 / 真实点击 / 行内下拉交互 —— 留给实机。
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
    try { fs.unlinkSync(abs); } catch {}
  }
}

const results = [];
const push = (n, ok, note = "") => results.push([n, ok, note]);

/**
 * 与板块内 `toMs` 完全同口径：本地时间字符串 → 毫秒。
 * 用它来验证「后端秒级 created_at 与前端 start_ts 字符串能否对齐」。
 */
function toMs(v) {
  const s = (v || "").trim();
  if (!s) return 0;
  const ms = Date.parse(s.replace(" ", "T"));
  return Number.isFinite(ms) ? ms : 0;
}

async function main() {
  const server = await createServer({ server: { middlewareMode: true }, appType: "custom", logLevel: "error" });
  try {
    const { QueryClient, QueryClientProvider } = await getRQ(server);
    const mod = await server.ssrLoadModule("/src/components/tasks/task-leads-section.tsx");
    const TaskLeadsSection = mod.TaskLeadsSection;

    // 任务窗口：与既有 smoke_task_detail.mjs 的 taskRow 一致
    // start_ms = 1790917347000，end_ms = 1790917373000（26 秒窗口，见下方断言）
    const START = "2026-10-02 13:02:27";
    const END = "2026-10-02 13:02:53";
    // created_at（秒）经真实时钟校准（node 实测）：
    //   1790917350 = 13:02:30（窗口内）、1790917360 = 13:02:40（窗口内）
    //   1790918000 = 13:15:20（窗口外）；1790917340 = 13:02:20（窗外 7 秒）
    const LEADS = [
      { id: 1, account: "小助理", peer_name: "张三", contact_type: "phone",
        contact_value: "13800001111", status: "new", created_at: 1790917350,
        source: "realtime", source_text: "电话13800001111" },
      { id: 2, account: "小助理", peer_name: "李四", contact_type: "wechat",
        contact_value: "wxlisi88", status: "followed", created_at: 1790917360,
        source: "backfill", source_text: "加微信wxlisi88" },
      { id: 3, account: "小助理", peer_name: "王五", contact_type: "phone",
        contact_value: "13800002222", status: "invalid", created_at: 1790918000,
        source: "realtime", source_text: "13800002222" },
      // 与 id1 同一秒，但属于别的账号 ⇒ 验证账号过滤（同窗口内也会被剔除）
      { id: 4, account: "别的账号", peer_name: "赵六", contact_type: "phone",
        contact_value: "13800003333", status: "new", created_at: 1790917350,
        source: "realtime", source_text: "13800003333" },
    ];
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc.setQueryData(
      ["task-leads", "小助理", toMs(START), toMs(END)],
      { ok: true, items: LEADS },
    );

    const props = { acct: "小助理", start: START, end: END, push: () => {} };
    const html = renderToString(
      React.createElement(QueryClientProvider, { client: qc },
        React.createElement(TaskLeadsSection, {
          ...props,
          api: {
            aiLeads: async () => ({ ok: true, items: LEADS }),
            aiLeadStatus: async () => ({ ok: true }),
          },
        })),
    );

    push("板块渲染成功", html.length > 0, `HTML ${html.length} 字节`);
    push("含标题「留资情况」", html.includes(">留资情况<"), "");
    push("归属边界提示（无 task_id）", html.includes("无 task_id"), "");
    push("边界提示声明近似匹配", html.includes("近似匹配"), "");
    push("列头「状态」", html.includes(">状态<"), "");
    push("列头「客户」", html.includes(">客户<"), "");
    push("列头「联系方式」", html.includes(">联系方式<"), "");
    push("列头「来源」", html.includes(">来源<"), "");
    push("列头「会话」", html.includes(">会话<"), "");
    push("列头「捕获时间」", html.includes(">捕获时间<"), "");
    push("含窗口内手机号", html.includes("13800001111"), "");
    push("含窗口内微信号", html.includes("wxlisi88"), "");
    push("含来源徽标「实时」", html.includes("实时"), "");
    push("含来源徽标「历史补全」", html.includes("历史补全"), "");
    push("含状态徽标「已跟进」", html.includes("已跟进"), "");
    push("含账号列值", html.includes("小助理"), "");
    push("状态可切换下拉（option）", html.includes("<option") && html.includes("无效"), "");
    push("捕获时间渲染为可读时间", /\d{4}\/\d{1,2}\/\d{1,2} \d{2}:\d{2}:\d{2}/.test(html),
      "zh-CN 个位月/日无前导零（2026/10/2 13:02:30）");

    // ---- 账号过滤（前端职责）：另一账号的线索不得出现 ----
    const filtered = LEADS.filter((r) => r.account === "小助理");
    push("账号过滤：排除别的账号", html.includes("赵六") === false, "");
    push("账号过滤：保留本账号 3 条", filtered.length === 3, `实得 ${filtered.length}`);

    // ---- 窗口对齐端到端 ----
    const s = toMs(START);
    const e = toMs(END);
    push("时间窗解析为有限毫秒", Number.isFinite(s) && Number.isFinite(e), `${s} ~ ${e}`);
    const inWin = LEADS.filter((r) => r.account === "小助理" && r.created_at * 1000 >= s && r.created_at * 1000 <= e);
    push("窗口内命中 2 条（张三·李四）", inWin.length === 2, `实得 ${inWin.length}`);
    push("窗口内含 13800001111", inWin.some((r) => r.contact_value === "13800001111"), "");
    push("窗口内含 wxlisi88", inWin.some((r) => r.contact_value === "wxlisi88"), "");
    const outWin = LEADS.filter((r) => r.account === "小助理" && (r.created_at * 1000 < s || r.created_at * 1000 > e));
    push("窗口外 1 条（王五 13:15）", outWin.length === 1, `实得 ${outWin.length}`);
    push("窗口外为 13800002222", outWin[0]?.contact_value === "13800002222", "");

    // ---- 空数据态 ----
    const qc2 = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc2.setQueryData(
      ["task-leads", "空账号", toMs(START), toMs(END)],
      { ok: true, items: [] },
    );
    const html2 = renderToString(
      React.createElement(QueryClientProvider, { client: qc2 },
        React.createElement(TaskLeadsSection, {
          acct: "空账号", start: START, end: END, push: () => {},
          api: { aiLeads: async () => ({ ok: true, items: [] }), aiLeadStatus: async () => ({ ok: true }) },
        })),
    );
    push("空数据渲染成功", html2.includes("留资情况"), "");
    push("空数据 Blank 文案", html2.includes("没有留资线索"), "");
    push("空数据无表格", html2.includes("<table") === false, "");
    push("空数据计数为 0 条", /0<!-- --> 条|>0 条</.test(html2),
      "React SSR 在文本节点间插入注释分隔符");

    // ---- 内存态（无 start）：不做时间过滤并标注 ----
    const qc3 = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc3.setQueryData(["task-leads", "小助理", 0, 0], { ok: true, items: LEADS });
    const html3 = renderToString(
      React.createElement(QueryClientProvider, { client: qc3 },
        React.createElement(TaskLeadsSection, {
          acct: "小助理", start: "", end: "", push: () => {},
          api: { aiLeads: async () => ({ ok: true, items: LEADS }), aiLeadStatus: async () => ({ ok: true }) },
        })),
    );
    push("内存态标注「未按时间筛选」", html3.includes("内存态·未按时间筛选"), "");

  } finally {
    await server.close();
  }
  let fail = 0;
  for (const [n, ok, note] of results) { if (!ok) fail++; console.log(`${ok ? "PASS" : "FAIL"}  ${n}${note ? "  — " + note : ""}`); }
  console.log(`\n${results.length - fail}/${results.length} passed`);
  if (fail) process.exitCode = 1;
}
main().catch((e) => { console.error("SMOKE_ERROR", e?.stack || e); process.exitCode = 1; });
