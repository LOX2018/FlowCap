// 回归测试：UnifiedConfigSection 的 init 死锁（2026-09-17 OCR HIGH 修复）
//
// 问题：原实现 `if (!q.data || initDone) return;` —— initDone 一旦为 true，
//       本挂载实例内永不再初始化。TagSection.tsx 常驻挂载只换 scope，
//       于是「选标签 A → 选标签 B」后 draft 仍是 A 的值，保存会把 A 写进 B。
//
// 本测试用 jsdom 语义直接验证"初始化判据"的纯逻辑（不引入 React 测试栈）：
// 把组件内的判据抽成等价函数，验证同一 scope 不重复 init、换 scope 必 init。
//
// 运行：cd FlowCap/frontend && npx tsx scripts/_verify_init_key_fix.ts
//   （若未装 tsx，用：node --experimental-strip-types scripts/...ts，Node>=22）

/** 与组件中等价的初始化判据（initKey = `${scope}|${dataUpdatedAt}`）。 */
export function makeInitDecider() {
  let last = "";
  return function shouldInit(scope: string, dataUpdatedAt: number): boolean {
    const key = `${scope}|${dataUpdatedAt}`;
    if (last === key) return false;
    last = key;
    return true;
  };
}

/** 旧实现（布尔锁）——用于对照，证明旧逻辑确实有缺陷。 */
export function makeLegacyDecider() {
  let initDone = false;
  return function shouldInit(_scope: string, _dataUpdatedAt: number): boolean {
    if (initDone) return false;
    initDone = true;
    return true;
  };
}

function assert(cond: boolean, msg: string) {
  if (!cond) {
    console.error(`  ✗ FAIL: ${msg}`);
    process.exitCode = 1;
  } else {
    console.log(`  ✓ ${msg}`);
  }
}

console.log("=== 新实现（initKey 判据）===");
{
  const d = makeInitDecider();
  assert(d("A", 1) === true, "首次（scope=A）应初始化");
  assert(d("A", 1) === false, "同一 scope、同一数据代次：不应重复初始化（避免重置用户编辑）");
  assert(d("B", 2) === true, "**切换到 scope=B：必须重新初始化**（这是原缺陷点）");
  assert(d("B", 2) === false, "B 稳定后不再重复初始化");
  assert(d("A", 3) === true, "切回 A 且数据代次变化：重新初始化");
  assert(d("B", 2) === true, "切回 B（代次回退也算变化）：重新初始化");
}

console.log("\n=== 旧实现（布尔锁）——对照，应暴露缺陷 ===");
{
  const d = makeLegacyDecider();
  assert(d("A", 1) === true, "旧：首次初始化");
  const switched = d("B", 2);
  assert(switched === false, "旧：切到 B **不**初始化 ← 这就是缺陷（draft 仍是 A 的值）");
}

console.log(
  process.exitCode
    ? "\n结论：存在失败项 ✗"
    : "\n结论：全部通过 ✓（新判据修好了 init 死锁；旧判据确认有缺陷）",
);
