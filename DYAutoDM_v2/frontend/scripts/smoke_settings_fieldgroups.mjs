/**
 * 配置中心归位 —— 拆卡机制的非浏览器渲染验证（第二轮）。
 *
 * 第一轮只验证了静态导航（useQuery 异步 ⇒ 卡片内容 SSR 下不渲染）。
 * 本轮**预填 React Query 缓存**，使 `q.data` 同步可用，从而真正渲染出卡片，
 * 直接验证本次新增的 `fieldGroups` 拆卡机制。
 *
 * 判据：
 *  U1 onlySections=[send] + fieldGroups(私信词库) ⇒ 出「私信词库」卡
 *  U2 同上 ⇒ **不**出「弹幕文案库」（该字段不在 send 分区）
 *  U3 onlySections=[send] 无分组 ⇒ 出主卡「私信发送与风控」且含「发送闸门最小间隔」
 *  U4 onlySections=[live] + fieldGroups(弹幕文案库) ⇒ 出「弹幕文案库」卡
 *  U5 同上 ⇒ **不**出「私信词库」（已迁出 live 分区）
 *  U6 onlySections=[live] 无分组 ⇒ 出「监听策略」卡（label 已改）
 */
import { createServer } from "vite";
import * as React from "react";
import { renderToString } from "react-dom/server";

const results = [];

async function shim(server, name, body) {
  const fs = await import("node:fs");
  const path = await import("node:path");
  const abs = path.join(process.cwd(), "src", name);
  fs.writeFileSync(abs, body, "utf8");
  try {
    return await server.ssrLoadModule("/src/" + name);
  } finally {
    try { fs.unlinkSync(abs); } catch { /* ignore */ }
  }
}

const SCHEMA = {
  send: {
    label: "私信发送与风控",
    fields: {
      min_interval: { label: "发送闸门最小间隔（秒）", type: "float", default: 8, min: 8, max: 300, apply: "hot" },
      dm_pool: { label: "私信词库（每行一条）", type: "str", default: "", apply: "hot", hint: "每行一条，随机选用" },
    },
  },
  live: {
    label: "监听策略",
    fields: {
      max_target: { label: "每场私信上限", type: "int", default: 3, min: 1, max: 200, apply: "hot" },
      danmaku_pool: { label: "弹幕文案库（每行一条）", type: "str", default: "", apply: "hot", hint: "每行一条，定时发送" },
    },
  },
};

const CFG = {
  send: { min_interval: 8, dm_pool: "你好\n在吗" },
  live: { max_target: 3, danmaku_pool: "主播好" },
};

async function main() {
  const server = await createServer({ server: { middlewareMode: true }, appType: "custom", logLevel: "error" });
  try {
    const rq = await shim(server, "__smoke_rq2.tsx",
      'export { QueryClient, QueryClientProvider } from "@tanstack/react-query";\n');
    const secMod = await server.ssrLoadModule("/src/components/settings/UnifiedConfigSection.tsx");
    const Section = secMod.default;

    const fakeProps = {
      ready: true, push: () => {}, setTab: () => {},
      api: {
        listTags: async () => ({ tags: [] }),
        getSettings: async () => ({ ok: true, schema: SCHEMA, config: CFG }),
        getScoped: async () => ({ ok: true, config: {} }),
      },
    };

    /** 预填缓存 ⇒ SSR 首帧即有数据（否则渲染「加载配置…」）。 */
    const render = (extra) => {
      const qc = new rq.QueryClient({ defaultOptions: { queries: { retry: false } } });
      qc.setQueryData(["unified-settings", ""], { ok: true, schema: SCHEMA, config: CFG });
      return renderToString(
        React.createElement(rq.QueryClientProvider, { client: qc },
          React.createElement(Section, { ...fakeProps, ...extra })),
      );
    };

    const check = (label, cond, note) => results.push([label, cond, note]);

    // U1/U2/U3：send 分区
    const sendGrouped = render({
      onlySections: ["send"],
      fieldGroups: [{ title: "私信词库", fields: ["dm_pool"] }],
    });
    check("U1 send+分组 ⇒ 出「私信词库」卡", sendGrouped.includes("私信词库"), `HTML ${sendGrouped.length}B`);
    check("U2 send+分组 ⇒ 不出「弹幕文案库」", !sendGrouped.includes("弹幕文案库"), "");
    check("U3 send 主卡仍在（含发送闸门字段）", sendGrouped.includes("发送闸门最小间隔"), "");

    // U4/U5/U6：live 分区
    const liveGrouped = render({
      onlySections: ["live"],
      fieldGroups: [{ title: "弹幕文案库", fields: ["danmaku_pool"] }],
    });
    check("U4 live+分组 ⇒ 出「弹幕文案库」卡", liveGrouped.includes("弹幕文案库"), `HTML ${liveGrouped.length}B`);
    check("U5 live+分组 ⇒ 不出「私信词库」", !liveGrouped.includes("私信词库"), "");
    check("U6 live 主卡 label = 监听策略", liveGrouped.includes("监听策略"), "");

    // 零回归：不传 fieldGroups ⇒ 整分区一张卡（原行为）
    const plain = render({ onlySections: ["send"] });
    check("U7 无 fieldGroups ⇒ 零回归（单卡含两字段）",
      plain.includes("发送闸门最小间隔") && plain.includes("私信词库"), "");
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

main().catch((e) => { console.error("SMOKE_ERROR", e?.stack || e); process.exitCode = 1; });
