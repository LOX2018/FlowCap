/**
 * 门禁：配置中心不得**重复渲染同一分区**（2026-10-04 实测缺陷固化）。
 *
 * ## 缺陷（真实发生过，本门禁因它而建）
 * `settings-page.tsx` 的「监听策略」tab 曾写成**两个实例叠加**：
 *   `<UnifiedConfigSection onlySections={["live"]} />`            ← 渲染整分区
 *   `<UnifiedConfigSection onlySections={["live"]} fieldGroups={[…danmaku_pool]} />`
 * ⇒ `danmaku_pool` 在页面上**出现两次**，且两张卡各有独立的「保存到 全局/标签」，
 *    可把同一字段写进不同作用域（配置串位）。
 *
 * ## 为什么不靠「渲染后数次数」
 * SSR 下 `useQuery` 是异步的，且 `SettingsPage` 的当前 tab 是**内部 state**
 * （无 prop 可注入）⇒ 单测无法切到「监听策略」tab 去数「弹幕文案库」出现几次。
 * 故本门禁改为**结构性不变式**：同一 section 只允许被一个实例渲染。
 * （`fieldGroups` 的语义就是「一个实例内拆卡」，多实例叠加必然重复。）
 *
 * ## 判据
 * 对 `settings-page.tsx` 中每个 `<UnifiedConfigSection …>` 实例，取它的
 * `onlySections` 列表；统计每个 section 被多少个实例渲染 ⇒ 必须 ≤ 1。
 *
 * 负控（自证会红）：把「两实例叠加」的形态喂给同一判定函数 ⇒ 必须报错。
 */
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const __dirname = dirname(fileURLToPath(import.meta.url));
const SRC = join(__dirname, "..", "src", "components", "settings", "settings-page.tsx");

/**
 * 从源码里抽出每个 `<UnifiedConfigSection …>` 实例的 onlySections。
 * 用「自 `<UnifiedConfigSection` 起，到下一个 `/>` 或 `>`」的片段，避免跨实例误配。
 */
export function extractInstances(src) {
  const out = [];
  const re = /<UnifiedConfigSection\b/g;
  let m;
  while ((m = re.exec(src)) !== null) {
    const start = m.index;
    // 片段终点：该实例自身的结束（`/>` 或 `>`）
    const rest = src.slice(start);
    const endRel = rest.search(/\/>|>/);
    if (endRel < 0) continue;
    const frag = rest.slice(0, endRel + 2);
    // 只取本片段里的 onlySections
    const os = /onlySections=\{\[([^\]]*)\]\}/.exec(frag);
    const sections = os
      ? os[1].split(",").map((s) => s.trim().replace(/^["']|["']$/g, "")).filter(Boolean)
      : [];
    const hasGroups = /fieldGroups=\{/.test(frag);
    out.push({ sections, hasGroups, frag: frag.slice(0, 80) });
  }
  return out;
}

/** 判定：同一 section 是否被多个实例渲染。返回冲突列表（空 = 通过）。 */
export function findDuplicates(instances) {
  const count = new Map();
  for (const inst of instances) {
    for (const s of inst.sections) {
      count.set(s, (count.get(s) || 0) + 1);
    }
  }
  return [...count.entries()].filter(([, n]) => n > 1).map(([s, n]) => ({ section: s, times: n }));
}

const results = [];
const check = (name, ok, note = "") => results.push([name, ok, note]);

// ── 正控：真实源码必须无重复 ──
const src = readFileSync(SRC, "utf8");
const inst = extractInstances(src);
check("解析到 UnifiedConfigSection 实例", inst.length > 0, `${inst.length} 个实例`);
const dup = findDuplicates(inst);
check(
  "真实源码：同一 section 未被重复渲染",
  dup.length === 0,
  dup.length ? `冲突: ${dup.map((d) => `${d.section}×${d.times}`).join(", ")}` : `sections=${[...new Set(inst.flatMap((i) => i.sections))].join("/")}`,
);

// ── 负控 A：复现原始缺陷形态（两实例叠加同一 section）⇒ 必须判红 ──
const BUGGY = `
  <UnifiedConfigSection {...props} onlySections={["live"]} />
  <UnifiedConfigSection {...props} onlySections={["live"]} fieldGroups={[{ title: "弹幕文案库", fields: ["danmaku_pool"] }]} />
`;
const dA = findDuplicates(extractInstances(BUGGY));
check("负控 A：两实例叠加同一 section ⇒ 判红", dA.length > 0, `检出 ${JSON.stringify(dA)}`);

// ── 负控 B：不同 section 各一个实例 ⇒ 必须判绿（防「一律报红」）──
const OK = `
  <UnifiedConfigSection {...props} onlySections={["live"]} fieldGroups={[{ title: "弹幕文案库", fields: ["danmaku_pool"] }]} />
  <UnifiedConfigSection {...props} onlySections={["live_orchestration"]} />
`;
const dB = findDuplicates(extractInstances(OK));
check("负控 B：不同 section 各一实例 ⇒ 判绿", dB.length === 0, JSON.stringify(dB));

// ── 负控 C：字段分组必须落在**单实例**内（防再写成两实例）──
const C = `
  <UnifiedConfigSection {...props} onlySections={["send"]} fieldGroups={[{ title: "私信词库", fields: ["dm_pool"] }]} />
`;
const instC = extractInstances(C);
check(
  "负控 C：单实例 + fieldGroups ⇒ 判绿且识别到分组",
  findDuplicates(instC).length === 0 && instC[0]?.hasGroups === true,
  JSON.stringify(instC.map((i) => ({ s: i.sections, g: i.hasGroups }))),
);

let fail = 0;
for (const [name, ok, note] of results) {
  if (!ok) fail++;
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${note ? "  — " + note : ""}`);
}
console.log(`\n${results.length - fail}/${results.length} passed`);
if (fail) process.exitCode = 1;
