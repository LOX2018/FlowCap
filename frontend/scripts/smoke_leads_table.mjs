/**
 * 留资线索表格 SSR 冒烟（2026-10-04）
 *
 * 用户要求：「将私信中心的留置线索用表格形式呈现」。
 * 验证：渲染成 <table>、列齐全、行数据到位、筛选/计数呈现。
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
    const mod = await server.ssrLoadModule("/src/components/messages/LeadsSection.tsx");
    const Leads = mod.default;

    const leadItems = [
      { id: 1, account: "工伤小助理", conv_id: "0:1:11:22", peer_name: "张三",
        contact_type: "phone", contact_value: "13800001111", status: "new",
        created_at: 1759500000, source_text: "我的电话13800001111" },
      { id: 2, account: "张老师", conv_id: "0:1:33:44", peer_name: "李四",
        contact_type: "wechat", contact_value: "lisi_wx", status: "followed",
        created_at: 1759400000, source_text: "加我微信lisi_wx", source: "backfill" },
    ];
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    qc.setQueryData(["ai-leads"], { ok: true, items: leadItems });

    const html = renderToString(
      React.createElement(QueryClientProvider, { client: qc },
        React.createElement(Leads, {
          ready: true, push: () => {},
          api: {
            aiLeads: async () => ({ ok: true, items: leadItems }),
            aiLeadStatus: async () => ({ ok: true }),
          },
        })),
    );

    push("留资线索渲染成功", html.length > 0, `HTML ${html.length} 字节`);
    push("渲染为 <table>", html.includes("<table"), "");
    push("列头「状态」", html.includes(">状态<"), "");
    push("列头「联系方式」", html.includes(">联系方式<"), "");
    push("列头「类型」", html.includes(">类型<"), "");
    push("列头「来源」", html.includes(">来源<"), "");
    push("来源徽标「历史补全」", html.includes("历史补全"), "");
    push("列头「客户」", html.includes(">客户<"), "");
    push("列头「账号」", html.includes(">账号<"), "");
    push("列头「捕获时间」", html.includes("捕获时间"), "");
    push("列头「原文」", html.includes(">原文<"), "");
    push("列头「操作」", html.includes(">操作<"), "");
    push("行数据：手机号", html.includes("13800001111"), "");
    push("行数据：微信号", html.includes("lisi_wx"), "");
    push("行数据：客户名", html.includes("张三"), "");
    push("行数据：账号", html.includes("工伤小助理"), "");
    push("行数据：原文", html.includes("我的电话"), "");
    push("类型徽标「手机号」", html.includes("手机号"), "");
    push("类型徽标「微信号」", html.includes("微信号"), "");
    push("状态「新线索」", html.includes("新线索"), "");
    push("状态「已跟进」", html.includes("已跟进"), "");
    push("计数胶囊（全部/新线索）", html.includes("全部") && html.includes("新线索"), "");
    push("搜索框存在", html.includes("搜索联系方式"), "");
    push("导出 CSV 入口", html.includes("导出 CSV"), "");
  } finally {
    await server.close();
  }
  let fail = 0;
  for (const [n, ok, note] of results) { if (!ok) fail++; console.log(`${ok ? "PASS" : "FAIL"}  ${n}${note ? "  — " + note : ""}`); }
  console.log(`\n${results.length - fail}/${results.length} passed`);
  if (fail) process.exitCode = 1;
}
main().catch((e) => { console.error("SMOKE_ERROR", e?.stack || e); process.exitCode = 1; });
