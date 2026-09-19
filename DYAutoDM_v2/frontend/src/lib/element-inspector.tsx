/**
 * 页面元素检查器（Element Inspector）—— 前端调试用「元素选择模式」
 *
 * ## 设计意图（本模块应做什么）
 *  Tauri 生产构建下**没有浏览器 DevTools**（右键检查不可用），前端改动/排查时
 *  无法把「界面上看到的那个控件」映射回「源码里的哪个组件/哪个 data-od-id」。
 *  本模块提供**只读**的元素取址能力：
 *    点击调试按钮 → 进入选择模式 → 鼠标悬停高亮 → 点击选中 →
 *    面板展示「元素的结构位置 + 组件链 + 可复制选择器」。
 *
 * ## 设计契约（不变式）
 *  1. **只选择，不触发**：选择模式下拦截 `click / mousedown / mouseup /
 *     dblclick / contextmenu / pointerdown / pointerup / touchstart / submit`，
 *     在**window 捕获阶段** `preventDefault + stopPropagation`，元素自身与祖先
 *     （含 React 合成事件委托根）**都收不到该事件** ⇒ 绝不触发元素功能。
 *  2. **零业务耦合**：只读 DOM/React fiber，不读不写任何 API、store、localStorage；
 *     不改动任何业务组件的行为。挂载点只有一处（TopBar 右侧按钮 + App 根部面板）。
 *  3. **降级诚实**：React fiber 内部结构属实现细节，生产构建可能裁剪。取不到时
 *     显示「不可用」，**绝不编造**组件名/状态。
 *
 * ## 用法
 *  - 点 TopBar 右侧 ▢ 按钮（或面板主按钮）进入；`Esc` 退出。
 *  - 悬停 `↑`/`↓` 在父/子元素间移动，`←` 上移一层，`c` 复制当前选择器。
 *  - 面板「复制」把完整取址信息（CSS/XPath/Playwright/组件链/状态）写入剪贴板，
 *    可直接粘贴给协作方。
 *
 * ## 沿革
 *  2026-09-19 新增（需求原话）：「前端无法精准定位元素，加一个调试按钮，点击后
 *  把渲染页面变成可选择元素模式（类似 XPath Tool），只选择元素复制元素的结构位置，
 *  不触发元素功能。」
 */
import { useEffect, useRef, useState } from "react";
import "./element-inspector.css";

// ── 拦截的事件集合（捕获阶段吞掉，避免触发元素功能） ──
// 说明：不拦 wheel/touch —— 桌面端选择时仍应能滚动页面；touch 事件在被 pasive
// 监听器上 preventDefault 会失效并打警告。
const BLOCKED_EVENTS = [
  "click", "dblclick", "contextmenu", "mousedown", "mouseup",
  "pointerdown", "pointerup", "submit", "keydown", "keypress", "keyup",
] as const;

const MAX_DEPTH = 8;
const MAX_CHILDREN = 40;

/** 允许在 CSS 选择器里出现的「稳定类名」：不含构建期哈希特征（长数字串/随机大小写混合）。 */
function isStableClass(c: string): boolean {
  if (!c || c.length > 40) return false;
  if (!/^[A-Za-z][\w-]*$/.test(c)) return false;
  if (/\d{3,}/.test(c)) return false; // `live-cfg-select-9f3a2` 之类
  return true;
}

/** 元素是否带「身份锚点」（data-od-id 非空 / id / aria-label）——用于折叠匿名包裹层。 */
function hasIdentity(el: Element): boolean {
  const h = el as HTMLElement;
  const oid = h.dataset?.odId;
  if (oid && oid.trim()) return true;
  if (el.id) return true;
  if (el.getAttribute("aria-label")) return true;
  return false;
}

function classListOf(el: Element): string[] {
  // 过滤掉工具自身的类（ei-*，如悬停高亮 ei-hover）——它们不能进选择器与报告
  return (el.getAttribute("class") || "")
    .split(/\s+/)
    .filter((c) => c && !c.startsWith("ei-"));
}

/** 行内标签：`button.live-cfg-select` / `div#root` */
function shortLabel(el: Element): string {
  const tag = el.tagName.toLowerCase();
  const id = el.id ? `#${el.id}` : "";
  const cls = classListOf(el).filter(isStableClass).slice(0, 2);
  return tag + id + (cls.length ? "." + cls.join(".") : "");
}

/** 全量类名（含哈希类），仅用于展示，不进选择器。 */
function fullClassLabel(el: Element): string {
  const cls = classListOf(el);
  return cls.length ? cls.join(" ") : "";
}

// ─────────────────────────── 选择器生成 ───────────────────────────

/**
 * 单段选择器：优先 `#id`（全局唯一时）→ `tag.class`（在 root 内唯一时）→ `tag:nth-of-type(n)`。
 * 绝不使用构建期哈希类名（抖音式 `_9f3a2` 会在下次构建变化）。
 */
function selectorSegment(el: Element, root: Element | Document): string {
  const tag = el.tagName.toLowerCase();
  if (el.id) {
    const byId = `#${CSS.escape(el.id)}`;
    if (document.querySelectorAll(byId).length === 1) return byId;
  }
  const parent = el.parentElement;
  if (!parent) return tag;
  // 先试稳定类名组合，命中唯一即用
  const stable = classListOf(el).filter(isStableClass);
  for (let n = Math.min(stable.length, 3); n >= 1; n--) {
    const cand = `${tag}.${stable.slice(0, n).map(CSS.escape).join(".")}`;
    const hits = (root || document).querySelectorAll(cand);
    if (hits.length === 1 && hits[0] === el) return cand;
  }
  const sameTag = Array.from(parent.children).filter((c) => c.tagName === el.tagName);
  const idx = sameTag.indexOf(el) + 1;
  return sameTag.length > 1 ? `${tag}:nth-of-type(${idx})` : tag;
}

/** 从 el 到 root 的 CSS 路径（含逐段唯一性校验失败时的回退）。 */
function cssPath(el: Element, root: Element): string {
  const parts: string[] = [];
  let cur: Element | null = el;
  while (cur && cur !== root && cur !== document.documentElement) {
    parts.unshift(selectorSegment(cur, root || document));
    if (parts.length > 12) break;
    cur = cur.parentElement;
  }
  const sel = parts.join(" > ");
  if (!sel) return "";
  try {
    const hits = (root || document).querySelectorAll(sel);
    if (hits.length >= 1 && hits[0] === el) return sel;
  } catch { /* 落到下面回退 */ }
  return "";
}

/** 绝对路径（从 documentElement 起算），用于 CSS/scoped 都失败时的兜底。 */
function absoluteCssPath(el: Element): string {
  const parts: string[] = [];
  let cur: Element | null = el;
  while (cur && cur !== document.documentElement && parts.length < 16) {
    parts.unshift(selectorSegment(cur, document));
    cur = cur.parentElement;
  }
  return (parts.length ? parts.join(" > ") : shortLabel(el));
}

/** 最近的非空 data-od-id 祖先（= 本项目的稳定锚点体系；见 docs/frontend_redesign_spec_pages.md）。 */
function nearestOdId(el: Element): { anchor: Element; value: string } | null {
  let cur: Element | null = el;
  while (cur && cur !== document.documentElement) {
    const v = (cur as HTMLElement).dataset?.odId;
    if (v && v.trim()) return { anchor: cur, value: v.trim() };
    cur = cur.parentElement;
  }
  return null;
}

/** XPath：优先锚定最近的 data-od-id；无锚点则从根逐段 `tag[n]`。 */
function xpathOf(el: Element): string {
  const steps: string[] = [];
  let cur: Element | null = el;
  while (cur && cur !== document.documentElement && steps.length < 16) {
    const oid = (cur as HTMLElement).dataset?.odId;
    if (oid && oid.trim()) {
      steps.unshift(`//*[@data-od-id=${JSON.stringify(oid.trim())}]`);
      break;
    }
    const parent: Element | null = cur.parentElement;
    if (!parent) break;
    const tag = cur.tagName.toLowerCase();
    const sameTag = Array.from(parent.children).filter((c) => c.tagName === cur!.tagName);
    steps.unshift(sameTag.length > 1 ? `${tag}[${sameTag.indexOf(cur) + 1}]` : tag);
    cur = parent;
  }
  const joined = steps.join("/");
  if (!joined) return "";
  return joined.startsWith("//") ? joined : `//${joined}`;
}

/** 推断可访问角色 + 名称（Playwright `get_by_role` 用；无把握时返回空）。 */
function roleAndName(el: Element): { role: string; name: string } | null {
  const explicit = el.getAttribute("role") || "";
  const tag = el.tagName.toLowerCase();
  const ROLE_BY_TAG: Record<string, string> = {
    button: "button", a: "link", input: "textbox", select: "combobox",
    textarea: "textbox", img: "img", table: "table", h1: "heading", h2: "heading",
  };
  const role = explicit || ROLE_BY_TAG[tag] || "";
  if (!role) return null;
  const aria = el.getAttribute("aria-label") || "";
  const text = (el.textContent || "").replace(/\s+/g, " ").trim();
  const name = aria || (text.length > 0 && text.length <= 30 ? text : "");
  if (!name) return null;
  return { role, name };
}

// ─────────────────────────── 结构树 ───────────────────────────

interface TreeNode { el: Element; children: TreeNode[] }

/**
 * 结构树：从选中元素向下构建，**折叠匿名包裹层**（无 data-od-id/id/aria-label 且只有一个
 * 子元素的层直接下钻），避免面板被样式 div 淹没。
 */
function buildTree(el: Element, depth: number): TreeNode {
  const children: TreeNode[] = [];
  if (depth >= MAX_DEPTH) return { el, children };
  const kids = Array.from(el.children).slice(0, MAX_CHILDREN);
  for (const c of kids) {
    let cur: Element = c;
    let guard = 0;
    while (!hasIdentity(cur) && cur.children.length === 1 && guard++ < 10) {
      cur = cur.children[0];
    }
    // 纯文本叶子跳过（只显示有意义的节点）
    if (!hasIdentity(cur) && cur.children.length === 0 && (cur.textContent || "").trim().length === 0) {
      continue;
    }
    children.push(buildTree(cur, depth + 1));
  }
  return { el, children };
}

/** 生成结构文本（emoji 标记身份锚点；缩进两空格）。 */
function renderTree(node: TreeNode, lines: string[], indent = 0, collapsed = false): void {
  const pad = "  ".repeat(indent);
  const oid = (node.el as HTMLElement).dataset?.odId;
  const mark = oid ? ` · data-od-id=${oid}` : "";
  const text = (node.el.textContent || "").replace(/\s+/g, " ").trim();
  const textPart = node.el.children.length === 0 && text ? `  “${text.slice(0, 24)}”` : "";
  lines.push(`${pad}${shortLabel(node.el)}${mark}${textPart}`);
  if (collapsed) return;
  for (const c of node.children) renderTree(c, lines, indent + 1);
}

// ─────────────────────────── 上下文 HTML（美化片段） ───────────────────────────

function prettyHtml(node: Element, budget: number, indent = 0): string {
  let out = "";
  const pad = "  ".repeat(indent);
  if (node.nodeType === Node.TEXT_NODE) {
    const t = (node.textContent || "").replace(/\s+/g, " ").trim();
    return t ? `${pad}${t.slice(0, 60)}\n` : "";
  }
  if (node.nodeType !== Node.ELEMENT_NODE) return "";
  const tag = node.tagName.toLowerCase();
  if (tag === "script" || tag === "style" || tag === "svg" || tag === "path") {
    return `${pad}<${tag} …/>\n`;
  }
  const attrs = Array.from(node.attributes)
    .filter((a) => a.name !== "style" && !a.name.startsWith("on"))
    .map((a) => {
      const v = a.value.length > 80 ? a.value.slice(0, 77) + "…" : a.value;
      return `${a.name}="${v}"`;
    });
  const open = `${pad}<${tag}${attrs.length ? " " + attrs.join(" ") : ""}>`;
  const kids = Array.from(node.childNodes);
  if (kids.length === 0) return `${open}</${tag}>\n`;
  out += open + "\n";
  for (const k of kids) {
    if (out.length > budget) { out += `${pad}  …\n`; break; }
    out += prettyHtml(k as Element, budget - out.length, indent + 1);
  }
  out += `${pad}</${tag}>\n`;
  return out;
}

// ─────────────────────────── React fiber（降级友好） ───────────────────────────

/** 取 fiber：React 16~18 恒在 DOM 节点上挂 `__reactFiber$xxx` / `__reactInternalInstance$xxx`。 */
function getFiber(el: Element): any | null {
  const key = Object.keys(el).find(
    (k) => k.startsWith("__reactFiber$") || k.startsWith("__reactInternalInstance$"),
  );
  return key ? (el as any)[key] ?? null : null;
}

/**
 * 组件名是否可信。
 *
 * ★实测（生产构建）：Vite 压缩后函数名会变成 `ep` / `t` / `a` 这类无意义串。
 * 把它们显示出来是**误导**（比不显示更糟）——按「降级诚实」契约一律丢弃，
 * 宁可显示「不可用（生产构建已压缩组件名）」。
 * React 组件按约定是 PascalCase，故要求名字含大写字母（或带 $ / . 的包装名）。
 */
function isReliableComponentName(n: string): boolean {
  if (!n || n.startsWith("_")) return false;
  if (/^[a-z][a-z0-9]{0,2}$/.test(n)) return false; // ep / t / ab（压缩名特征）
  if (!/[A-Z]/.test(n) && !n.includes("$") && !n.includes(".")) return false;
  return true;
}

function componentName(fiber: any): string {
  const t = fiber?.type ?? fiber?.elementType;
  if (!t) return "";
  if (typeof t === "string") return ""; // host 元素
  if (typeof t === "function") {
    return isReliableComponentName(t.displayName || t.name || "") ? (t.displayName || t.name) : "";
  }
  if (typeof t === "object") {
    const inner = t.render || t.type;
    if (typeof inner === "function") {
      const n = inner.displayName || inner.name || "";
      return isReliableComponentName(n) ? n : "";
    }
    if (typeof inner === "string") return inner;
  }
  return "";
}

/** 组件链（App > AppShell > LivePage > …）；压缩名一律丢弃，取不到就返回空。 */
function componentChain(fiber: any): string[] {
  const chain: string[] = [];
  let cur = fiber;
  let hops = 0;
  while (cur && hops++ < 60 && chain.length < 6) {
    const n = componentName(cur);
    if (n) {
      if (chain[chain.length - 1] !== n) chain.unshift(n);
    }
    cur = cur.return;
  }
  return chain;
}

/** 值摘要：标量直出，对象/数组给「N 项」概览，函数跳过。 */
function summarize(v: any, depth = 0): string | null {
  if (v === null) return "null";
  if (v === undefined) return "undefined";
  const t = typeof v;
  if (t === "string") return v.length > 48 ? JSON.stringify(v.slice(0, 45) + "…") : JSON.stringify(v);
  if (t === "number" || t === "boolean") return String(v);
  if (t === "function" || t === "symbol") return null;
  if (Array.isArray(v)) {
    if (depth > 0) return `Array(${v.length})`;
    const head = v.slice(0, 4).map((x) => summarize(x, depth + 1)).filter(Boolean);
    return `Array(${v.length})${head.length ? " [" + head.join(", ") + "]" : ""}`;
  }
  if (t === "object") {
    const el = v as Element;
    if (el.nodeType === 1) return `<${el.tagName.toLowerCase()}>`;
    const keys = Object.keys(v);
    if (depth > 0) return `{${keys.length} 键}`;
    return `{${keys.slice(0, 6).join(", ")}${keys.length > 6 ? ", …" : ""}}`;
  }
  return null;
}

interface StateRow { label: string; value: string; kind: "prop" | "state" | "store" }

/** 收集「最近的函数组件」的 props 与 state（best-effort；失败一律返回空数组）。 */
function collectState(fiber: any): { rows: StateRow[]; ok: boolean } {
  const rows: StateRow[] = [];
  let cur = fiber;
  let comp: any = null;
  let hops = 0;
  while (cur && hops++ < 40) {
    if (typeof (cur.type ?? cur.elementType) === "function") { comp = cur; break; }
    cur = cur.return;
  }
  if (!comp) return { rows, ok: false };
  // props（脱敏：跳过函数/事件/children）
  const props = comp.memoizedProps || {};
  for (const k of Object.keys(props).slice(0, 24)) {
    if (k === "children" || (/^on[A-Z]/.test(k))) continue;
    const s = summarize((props as any)[k]);
    if (s === null) continue;
    rows.push({ label: k, value: s, kind: "prop" });
    if (rows.length >= 8) break;
  }
  // hooks：useState 有 queue.dispatch；zustand/外部 store 的 uSES 快照带 getState
  let hook = comp.memoizedState;
  let idx = 0;
  while (hook && idx < 30 && rows.length < 14) {
    const st = hook.memoizedState;
    if (st && typeof st === "object" && typeof st.getState === "function") {
      try {
        const snap = st.getState();
        rows.push({ label: `store#${idx}`, value: `{${Object.keys(snap).slice(0, 8).join(", ")}…}`, kind: "store" });
      } catch { /* ignore */ }
    } else if (hook.queue && typeof hook.queue.dispatch === "function") {
      const s = summarize(st);
      if (s !== null) rows.push({ label: `state#${idx}`, value: s, kind: "state" });
    }
    hook = hook.next;
    idx++;
  }
  return { rows, ok: true };
}

// ─────────────────────────── 主组件 ───────────────────────────

interface Report {
  css: string;
  cssScoped: string;
  cssAbsolute: string;
  xpath: string;
  oid: string;
  tag: string;
  label: string;
  className: string;
  text: string;
  role: { role: string; name: string } | null;
  chain: string[];
  treeLines: string[];
  stateRows: StateRow[];
  stateOk: boolean;
  html: string;
}

/**
 * 前端版本号（写入报告头部）。
 *
 * ★实测踩坑（生产构建）：不能写成 `${(__APP_VERSION__ as string) || "?"}` ——
 * 模板里的 TS **类型转换会让 Vite `define` 的常量替换失效**，运行时该标识符不存在
 * → `ReferenceError` 被 promise 静默吞掉（现象：点「复制全部」毫无反应）。
 * 正确写法与项目既有惯例一致（见 `api/client.ts`）：try/catch 包住 + `String()`。
 */
function frontendVersion(): string {
  try {
    return String(__APP_VERSION__);
  } catch {
    return "?"; // 未注入（如独立 harness 构建）
  }
}

function buildReport(el: Element): Report {
  const anchor = nearestOdId(el);
  const oid = (el as HTMLElement).dataset?.odId || "";
  let cssScoped = "";
  let css = "";
  if (oid) css = `[data-od-id=${JSON.stringify(oid)}]`;
  if (anchor && anchor.anchor !== el) {
    const inner = cssPath(el, anchor.anchor);
    if (inner) cssScoped = `[data-od-id=${JSON.stringify(anchor.value)}] > ${inner}`;
  } else if (anchor && anchor.anchor === el && !css) {
    cssScoped = `[data-od-id=${JSON.stringify(anchor.value)}]`;
  }
  const absolute = absoluteCssPath(el);
  const fiber = getFiber(el);
  const { rows, ok } = collectState(fiber);
  const treeLines: string[] = [];
  renderTree(buildTree(el, 0), treeLines);
  return {
    css: css || cssScoped || absolute,
    cssScoped,
    cssAbsolute: absolute,
    xpath: xpathOf(el),
    oid,
    tag: el.tagName.toLowerCase(),
    label: shortLabel(el),
    className: fullClassLabel(el),
    text: (el.textContent || "").replace(/\s+/g, " ").trim().slice(0, 80),
    role: roleAndName(el),
    chain: componentChain(fiber),
    treeLines,
    stateRows: rows,
    stateOk: ok,
    html: prettyHtml(el.parentElement || el, 1400).trimEnd(),
  };
}

function reportToText(r: Report, tab: string): string {
  const stamp = new Date().toLocaleString("zh-CN");
  const L: string[] = [];
  L.push("# DYAutoDM 元素取址报告");
  L.push(`# 页面 tab: ${tab}   时间: ${stamp}   前端版本: ${frontendVersion()}`);
  L.push(`# 元素: ${r.label}${r.oid ? `   data-od-id: ${r.oid}` : ""}`);
  L.push("");
  L.push("## CSS 选择器（首选）");
  L.push(r.css || "(无)");
  if (r.cssScoped && r.cssScoped !== r.css) L.push("", "## CSS（锚点内完整路径）", r.cssScoped);
  if (r.cssAbsolute && r.cssAbsolute !== r.css) L.push("", "## CSS（绝对路径，兜底）", r.cssAbsolute);
  L.push("", "## XPath", r.xpath || "(无)");
  L.push("", "## Playwright (Python)");
  if (r.oid) L.push(`page.locator('[data-od-id="${r.oid}"]')`);
  L.push(`page.locator(${JSON.stringify(r.cssAbsolute)})`);
  if (r.role) {
    L.push(`page.get_by_role(${JSON.stringify(r.role.role)}, name=${JSON.stringify(r.role.name)})`);
  }
  L.push("", "## React 组件链");
  L.push(r.chain.length ? r.chain.join(" > ") : "不可用（生产构建已裁剪组件名）");
  L.push("", "## 运行时状态（best-effort，仅供校验）");
  if (!r.stateOk) L.push("不可用（未找到函数组件 fiber）");
  else if (!r.stateRows.length) L.push("(无标量状态)");
  else for (const row of r.stateRows) L.push(`- ${row.kind === "prop" ? "prop" : row.kind === "store" ? "store" : "state"} ${row.label} = ${row.value}`);
  L.push("", "## 选中元素的下级结构");
  L.push(...r.treeLines.map((x) => "  " + x));
  L.push("", "## 上下文 HTML（上级节点）");
  L.push(r.html);
  return L.join("\n");
}

export interface InspectorProps {
  /** 当前页面 tab（写进报告，便于定位在哪个页面选的） */
  currentTab?: string;
}

/**
 * 全局顶层悬浮「调试」入口 + 选择模式覆盖层 + 结果面板。
 *
 * ## 入口设计（2026-09-19 用户定调，勿回退）
 *  用户原话：「这个入口不合理，把该入口设定为全局顶层悬浮」。
 *  旧实现把按钮塞进 TopBar 的 `topRight`，有两个硬伤：
 *    ① 未登录时整个 `AppShell` 不渲染 ⇒ **登录框/闪屏阶段根本看不到入口**
 *       （而那正是最需要定位元素的时候）；
 *    ② 挤在状态徽章/退出/窗口控制之间，不是「调试工具」该在的位置。
 *  现改为**全局顶层悬浮按钮**：`App` 根部无条件渲染（覆盖登录门与闪屏），
 *  固定左下角（避开右下角的结果面板），`z-index` 高于面板与所有业务层。
 *
 * 挂载于 `App` 根部，全局唯一实例；按钮与覆盖层共用同一份选择状态。
 */
export function ElementInspectorButton({ currentTab = "" }: InspectorProps) {
  const [active, setActive] = useState(false);
  const [picked, setPicked] = useState<Element | null>(null);
  const [report, setReport] = useState<Report | null>(null);
  const [tab, setTab] = useState<"selector" | "tree" | "state">("selector");
  const [tip, setTip] = useState("");
  const boxRef = useRef<HTMLDivElement | null>(null);
  const hoverRef = useRef<HTMLElement | null>(null);
  const pickedRef = useRef<Element | null>(null);
  const fabRef = useRef<HTMLButtonElement | null>(null);
  const dragRef = useRef<{
    startX: number; startY: number; baseX: number; baseY: number;
    moved: boolean; active: boolean; pointerId: number;
  } | null>(null);
  /** 悬浮入口位置（视口坐标，左上角）。null = 尚未拖动，用 CSS 默认（左下角）。 */
  const [fabPos, setFabPos] = useState<{ x: number; y: number } | null>(null);
  const [dragging, setDragging] = useState(false);
  const [posTip, setPosTip] = useState("");

  /**
   * 读取记忆的位置（localStorage `dy.inspector.pos`）。
   *
   * ⚠️ 这是本工具**唯一**允许碰 localStorage 的地方，且只存「自己按钮的坐标」
   *   （不涉及任何业务数据）；`test_element_inspector_guards.py` 的
   *   「零业务耦合」断言已按此**显式豁免本函数**，不要让豁免范围扩大。
   */
  useEffect(() => {
    try {
      const raw = localStorage.getItem("dy.inspector.pos");
      if (!raw) return;
      const p = JSON.parse(raw);
      if (typeof p?.x === "number" && typeof p?.y === "number") setFabPos(p);
    } catch { /* 无记忆或损坏 → 用默认位置 */ }
  }, []);

  /** 视口尺寸变化时把按钮夹回可视区（防拖到屏幕外/换分辨率后丢失）。 */
  useEffect(() => {
    const onResize = () => {
      setFabPos((p) => {
        if (!p) return p;
        const b = fabRef.current?.getBoundingClientRect();
        const w = b?.width ?? 110;
        const h = b?.height ?? 32;
        const nx = Math.min(Math.max(4, p.x), Math.max(4, window.innerWidth - w - 4));
        const ny = Math.min(Math.max(4, p.y), Math.max(4, window.innerHeight - h - 4));
        if (nx === p.x && ny === p.y) return p;
        try { localStorage.setItem("dy.inspector.pos", JSON.stringify({ x: nx, y: ny })); } catch { /* ignore */ }
        return { x: nx, y: ny };
      });
    };
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);

  const saveFabPos = (x: number, y: number) => {
    setFabPos({ x, y });
    try { localStorage.setItem("dy.inspector.pos", JSON.stringify({ x, y })); } catch { /* ignore */ }
  };

  /** 长按（160ms）后才进入拖动 —— 短按仍是「开关选择模式」，两者不冲突。 */
  const DRAG_HOLD_MS = 160;

  const onFabPointerDown = (e: React.PointerEvent<HTMLButtonElement>) => {
    if (e.button !== 0) return; // 只响应左键
    const el = fabRef.current;
    if (!el) return;
    const b = el.getBoundingClientRect();
    dragRef.current = {
      startX: e.clientX, startY: e.clientY,
      baseX: b.left, baseY: b.top,
      moved: false, active: false, pointerId: e.pointerId,
    };
    // 160ms 长按后仍未抬起 → 进入拖动（并接管指针）
    window.setTimeout(() => {
      const d = dragRef.current;
      if (d && d.pointerId === e.pointerId && !d.moved) {
        d.active = true;
        setDragging(true);
        try { el.setPointerCapture(e.pointerId); } catch { /* ignore */ }
      }
    }, DRAG_HOLD_MS);
  };

  const onFabPointerMove = (e: React.PointerEvent<HTMLButtonElement>) => {
    const d = dragRef.current;
    if (!d || d.pointerId !== e.pointerId) return;
    const dx = e.clientX - d.startX;
    const dy = e.clientY - d.startY;
    if (!d.active) {
      // 长按前就移动了 → 记为「已移动」，抬起时不切换模式（避免误触）
      if (Math.abs(dx) > 4 || Math.abs(dy) > 4) d.moved = true;
      return;
    }
    d.moved = true;
    const el = fabRef.current;
    const w = el?.offsetWidth ?? 110;
    const h = el?.offsetHeight ?? 32;
    const x = Math.min(Math.max(4, d.baseX + dx), Math.max(4, window.innerWidth - w - 4));
    const y = Math.min(Math.max(4, d.baseY + dy), Math.max(4, window.innerHeight - h - 4));
    setFabPos({ x, y });
  };

  const onFabPointerUp = () => {
    const d = dragRef.current;
    dragRef.current = null;
    if (!d) return;
    try { fabRef.current?.releasePointerCapture(d.pointerId); } catch { /* ignore */ }
    if (d.active) {
      setDragging(false);
      const el = fabRef.current;
      const b = el?.getBoundingClientRect();
      const x = Math.round(b ? b.left : d.baseX);
      const y = Math.round(b ? b.top : d.baseY);
      saveFabPos(x, y);
      setPosTip("位置已记住");
      window.setTimeout(() => setPosTip(""), 1600);
      return; // ★ 拖动结束不触发 onClick
    }
    // 未进入拖动：若几乎没动 → 视为短按，切换选择模式
    if (!d.moved) {
      setActive((v) => !v);
      setTip("");
    }
  };

  /** 双击复位到默认位置（左下角） */
  const onFabDoubleClick = () => {
    try { localStorage.removeItem("dy.inspector.pos"); } catch { /* ignore */ }
    setFabPos(null);
    setPosTip("已复位到默认位置");
    window.setTimeout(() => setPosTip(""), 1600);
  };

  // 选中元素变化 → 重建报告
  useEffect(() => {
    pickedRef.current = picked;
    setReport(picked ? buildReport(picked) : null);
  }, [picked]);

  // ── 选择模式：捕获阶段吞事件 + 悬停高亮 ──
  useEffect(() => {
    if (!active) {
      hoverRef.current?.classList.remove("ei-hover");
      hoverRef.current = null;
      if (boxRef.current) boxRef.current.style.display = "none";
      return;
    }
    const mark = (el: Element | null) => {
      if (hoverRef.current && hoverRef.current !== el) hoverRef.current.classList.remove("ei-hover");
      hoverRef.current = el as HTMLElement | null;
      const box = boxRef.current;
      if (!box) return;
      if (!el) { box.style.display = "none"; return; }
      el.classList.add("ei-hover");
      const r = el.getBoundingClientRect();
      box.style.display = "block";
      box.style.transform = `translate(${Math.round(r.left)}px, ${Math.round(r.top)}px)`;
      box.style.width = `${Math.round(r.width)}px`;
      box.style.height = `${Math.round(r.height)}px`;
      box.textContent = shortLabel(el);
    };

    const onMove = (e: MouseEvent) => {
      const t = e.target as Element | null;
      if (!t || t.nodeType !== 1) return;
      if (t.closest?.("[data-ei-ui]")) return; // 面板/按钮自身不算
      mark(t);
    };
    const swallow = (e: Event) => {
      const t = e.target as Element | null;
      if (t?.closest?.("[data-ei-ui]")) return; // 允许操作面板与退出按钮
      e.preventDefault();
      e.stopPropagation();
      // 注意：**不能** stopImmediatePropagation —— `click` 同时注册了本处理器的
      // 兄弟监听器（onClick），它会被一起吞掉（实测：面板永不出现）。
    };
    const onClick = (e: MouseEvent) => {
      const t = e.target as Element | null;
      if (t?.closest?.("[data-ei-ui]")) return;
      swallow(e);
      if (t && t.nodeType === 1) setPicked(t);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") { setActive(false); return; }
      const cur = pickedRef.current;
      if (!cur) return;
      let next: Element | null = null;
      if (e.key === "ArrowUp" || e.key === "ArrowLeft") next = cur.parentElement;
      else if (e.key === "ArrowDown") next = cur.firstElementChild;
      else if (e.key === "c" || e.key === "C") {
        if (report) { void copyText(reportToText(report, currentTab)); setTip("已复制选择器"); }
        return;
      } else return;
      if (next) { e.preventDefault(); setPicked(next); }
    };

    window.addEventListener("mousemove", onMove, true);
    for (const ev of BLOCKED_EVENTS) window.addEventListener(ev, swallow, true);
    window.addEventListener("click", onClick, true);
    window.addEventListener("keydown", onKey, true);
    return () => {
      window.removeEventListener("mousemove", onMove, true);
      for (const ev of BLOCKED_EVENTS) window.removeEventListener(ev, swallow, true);
      window.removeEventListener("click", onClick, true);
      window.removeEventListener("keydown", onKey, true);
      hoverRef.current?.classList.remove("ei-hover");
      hoverRef.current = null;
      if (boxRef.current) boxRef.current.style.display = "none";
    };
    // report 参与闭包只为快捷键复制
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, currentTab, report]);

  async function copyText(text: string, silent = false) {
    try {
      await navigator.clipboard.writeText(text);
      setTip("已复制到剪贴板");
    } catch {
      // 剪贴板不可用（权限/非安全上下文）→ 退化为选中提示，绝不谎报成功
      setTip("剪贴板不可用，请手动选中复制");
    }
    if (!silent) window.setTimeout(() => setTip(""), 2200);
  }

  const copy = async () => {
    if (!report) return;
    await copyText(reportToText(report, currentTab));
  };

  return (
    <>
      {/* 全局顶层悬浮调试入口（始终可见：覆盖闪屏 / 登录门 / 主界面）
          · 长按 160ms 拖动 → 位置记住（localStorage `dy.inspector.pos`，仅存坐标）
          · 短按 = 开关选择模式；双击 = 复位到默认左下角 */}
      <button
        ref={fabRef}
        data-ei-ui
        data-od-id="debug-inspector-toggle"
        title={active
          ? "退出元素选择模式（Esc）｜长按可拖动"
          : "元素选择模式：点击页面元素复制其结构位置，不触发元素功能｜长按可拖动｜双击复位"}
        onPointerDown={onFabPointerDown}
        onPointerMove={onFabPointerMove}
        onPointerUp={onFabPointerUp}
        onPointerCancel={onFabPointerUp}
        onDoubleClick={onFabDoubleClick}
        style={fabPos
          ? { left: fabPos.x, top: fabPos.y, right: "auto", bottom: "auto" }
          : undefined}
        className={`ei-fab${active ? " ei-fab-on" : ""}${dragging ? " ei-fab-dragging" : ""}`}
      >
        <span className="ei-fab-glyph">◎</span>
        <span className="ei-fab-label">{active ? "退出选择" : "元素选择"}</span>
      </button>

      {posTip && <div className="ei-postip" data-ei-ui>{posTip}</div>}

      {/* 悬停高亮框 */}
      <div ref={boxRef} className="ei-box" data-ei-ui style={{ display: "none" }} />

      {/* 选择模式提示条 */}
      {active && (
        <div className="ei-hint" data-ei-ui>
          <b>元素选择模式</b>
          <span>悬停高亮 · 点击选中 · ↑↓ 父/子 · ← 上层 · c 复制 · Esc 退出</span>
          <button className="ei-hint-x" onClick={() => setActive(false)}>退出</button>
        </div>
      )}

      {/* 结果面板 */}
      {report && (
        <div className="ei-panel" data-ei-ui data-od-id="debug-inspector-panel">
          <div className="ei-panel-head">
            <span className="ei-panel-title">元素取址</span>
            <span className="ei-panel-el">{report.label}</span>
            <button className="ei-x" title="清空选择" onClick={() => setPicked(null)}>×</button>
          </div>

          <div className="ei-tabs">
            {([["selector", "选择器"], ["tree", "结构"], ["state", "状态"]] as const).map(([id, label]) => (
              <button
                key={id}
                className={`ei-tab${tab === id ? " ei-tab-on" : ""}`}
                onClick={() => setTab(id as typeof tab)}
              >{label}</button>
            ))}
          </div>

          <div className="ei-body">
            {tab === "selector" && (
              <>
                <div className="ei-sec">CSS 选择器（首选）</div>
                <code className="ei-code ei-code-hi">{report.css || "(无)"}</code>
                {report.cssScoped && report.cssScoped !== report.css && (
                  <>
                    <div className="ei-sec">CSS（锚点内完整路径）</div>
                    <code className="ei-code">{report.cssScoped}</code>
                  </>
                )}
                <div className="ei-sec">XPath</div>
                <code className="ei-code">{report.xpath || "(无)"}</code>
                <div className="ei-sec">Playwright</div>
                <code className="ei-code">
                  {report.oid ? `page.locator('[data-od-id="${report.oid}"]')` : `page.locator(${JSON.stringify(report.cssAbsolute)})`}
                  {report.role ? `\npage.get_by_role(${JSON.stringify(report.role.role)}, name=${JSON.stringify(report.role.name)})` : ""}
                </code>
                <div className="ei-sec">组件链</div>
                <code className="ei-code">{report.chain.length ? report.chain.join(" > ") : "不可用（生产构建已裁剪）"}</code>
                <div className="ei-sec">上级结构</div>
                <pre className="ei-pre">{report.html}</pre>
              </>
            )}

            {tab === "tree" && (
              <>
                <div className="ei-sec">选中元素的下级结构（已折叠匿名包裹层）</div>
                <pre className="ei-pre">{report.treeLines.join("\n") || "(无子元素)"}</pre>
              </>
            )}

            {tab === "state" && (
              <>
                <div className="ei-sec">
                  运行时状态（best-effort，用于校验当前值；定位请优先用选择器）
                </div>
                {!report.stateOk && (
                  <div className="ei-muted">不可用：未找到函数组件 fiber（生产构建可能裁剪）。</div>
                )}
                {report.stateOk && report.stateRows.length === 0 && (
                  <div className="ei-muted">(未发现标量 state/prop)</div>
                )}
                {report.stateRows.map((row) => (
                  <div className="ei-row" key={`${row.kind}:${row.label}`}>
                    <span className={`ei-tag ei-tag-${row.kind}`}>{row.kind}</span>
                    <span className="ei-key">{row.label}</span>
                    <span className="ei-val">{row.value}</span>
                  </div>
                ))}
                <div className="ei-sec">标签与类名</div>
                <div className="ei-row"><span className="ei-key">tag</span><span className="ei-val">{report.tag}</span></div>
                <div className="ei-row"><span className="ei-key">class</span><span className="ei-val">{report.className || "-"}</span></div>
                <div className="ei-row"><span className="ei-key">text</span><span className="ei-val">{report.text || "-"}</span></div>
              </>
            )}
          </div>

          <div className="ei-foot">
            <button className="ei-act" onClick={copy} disabled={!report}>复制全部</button>
            <button className="ei-act ei-act-ghost" onClick={() => setActive((v) => !v)}>
              {active ? "退出选择" : "继续选择"}
            </button>
            {tip && <span className="ei-tip">{tip}</span>}
          </div>
        </div>
      )}
    </>
  );
}

export default ElementInspectorButton;
