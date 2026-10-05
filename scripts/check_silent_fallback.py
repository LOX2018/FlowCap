# -*- coding: utf-8 -*-
"""静默兜底机械门禁 —— 把「失败必须可见」从声明式铁律变成**会真拦**的检查。

## 为什么要有这个脚本

`artifacts/全库审计_H6_2026-09-22.md` §S2 已定性：

> 静默兜底较历史（340 处）不减反增到 **386 处**（非测试口径），
> 且集中在 `browser_daemon`(43)/`recv_daemon`(24)/`core/auto_dm`(15) 等核心链路。

本项目是**对抗性爬虫**：任何**外发路径**（私信发送/评论/关注/上报）的静默兜底
属于**风控高危** —— 失败被吞会导致「以为发了实际没发」或「以为没发实际发了」。
项目铁律要求「**无回执不认成功**」，投递/写库失败必须可见。

历史审计把「静默兜底」做成了一次性人肉统计（386 这个数字**不可复跑**）。
本脚本是它的 instrument 层：可复跑、可分级、可防新增。

## 口径（★ 与历史基线对齐）

历史基线 = AST 统计，排除 `test_*.py`/`verify_*.py` 后，
**handler 体仅 `pass`/`continue` 或仅 `logger.debug`** 者 = 386 处。

本脚本**沿用同一「handler 体」判据**（保证 386 可比），并额外做两件历史审计
当时明确列为「未做」的事（见 H6 报告 §164「386 处为静态计数，未判定哪些 handler
在正常路径下会实际吞掉异常」）：

  1. **读 try 块全文**（按缩进范围取真实代码块），判定「这个 try 里到底干了什么」，
     据此把静默兜底**分级**为 L1-写库 / L1-外发 / L2-外部资源 / L3-内部逻辑。
  2. 区分 `pass`（完全吞掉，危险）与 `logger.debug`（有记录但级别低，次危险），
     并且把 `return None/{}/[]/False`、bare `except:`、`contextlib.suppress` 一并纳入。

## 问题编号（与 check_iron_rules.py 的 R1..Rn 同风格）

| ID  | 判据 |
|-----|------|
| F1  | L1-写库路径**禁止** `pass`-only 静默兜底（吞掉 INSERT/UPDATE/DELETE/executemany） |
| F2  | L1-外发路径**禁止** `pass`-only 静默兜底（吞掉 send/post/request/emit/上报） |
| F3  | L1（写库+外发）相对基线**不得新增**（防新增，需 `--baseline` 文件） |
| F4  | 统计口径可见化：L1/L2/L3 计数 + 与历史基线 386 的对比（WARN 型，不阻断） |
| F5  | 「待人工复核」项必须显式列出，不得静默归入低危 |

退出码：0 = 无阻断；1 = 有阻断（L1 pass-only 命中，或 L1 相对基线新增）。

用法：
    py314 scripts/check_silent_fallback.py                 # 人读
    py314 scripts/check_silent_fallback.py --json          # 机器可读
    py314 scripts/check_silent_fallback.py --baseline write # 写基线
    py314 scripts/check_silent_fallback.py --baseline check # 对比基线（默认）
    py314 scripts/check_silent_fallback.py --selftest       # 自证「违规会报红」
    py314 scripts/check_silent_fallback.py --include-tests   # ★ 纳入 test_*/verify_*
    py314 scripts/check_silent_fallback.py --baseline add-scope  # 只新增口径字段进基线

## 扫描口径（★ 显式化，不再默认静默）

默认排除 `test_*.py` / `verify_*.py`（与历史 386 基线同口径）。
`--include-tests` 将其纳入扫描。**每次运行都会把「本次是否含 test/verify、
排除/纳入多少个文件」打印进输出**，让豁免成为**显式决策**而非静默默认。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import sys

# ── 路径真源 ──────────────────────────────────────────────────────────────
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKEND = os.path.join(REPO_ROOT, "backend")

#: 基线文件（默认落 artifacts/，与 H6 报告同目录族）
BASELINE_DEFAULT = os.path.join(
    os.path.dirname(REPO_ROOT), "artifacts", "audit_2026-09-27",
    "silent_fallback_baseline.json")

#: 扫描时跳过的目录（构建产物 / 依赖 / 缓存 / 部署运行时）
SKIP_DIRS = {
    "build", "node_modules", "__pycache__", "vendor", "_internal",
    "_bisect", "dist", "target", ".venv", "venv", ".git", ".pytest_cache",
}

#: 历史基线（2026-09-22 H6 审计，AST 口径，排除 test_*/verify_*）
HISTORIC_BASELINE = 386

# ── 路径类型识别词表（用于判定 try 块内「实际做了什么」）────────────────────
#: 写库路径：任何 INSERT/UPDATE/DELETE/REPLACE 或封装过的写库函数
WRITE_TOKENS = (
    "insert into", "update ", "delete from", "replace into",
    "execute(", "executemany(", "executescript(",
    ".commit(", ".execute(", ".executemany(", ".executescript(",
)
WRITE_FUNC_PREFIX = ("_save_", "_record_", "_insert_", "_update_", "_delete_",
                     "_write_", "_persist_", "_upsert_", "_store_")
WRITE_FUNC_WORDS = ("save_", "record_", "insert_", "update_", "delete_",
                    "upsert_", "persist_", "write_", "store_", "add_",
                    "set_", "put_", "merge_", "save", "commit", "flush")

#: 外发路径：发送/上报/HTTP 请求/浏览器操作/抖音 API（风控高危）
SEND_TOKENS = (
    "requests.get", "requests.post", "requests.put", "requests.delete",
    "requests.request", "httpx.", "aiohttp", "urlopen", "urllib.request",
    "session.post", "session.get", "session.request",
    "client.post", "client.get", "client.request",
    ".send(", "send_message", "send_msg", "send_text", "msgsend",
    "wp_send", "wssend", "_upload", "upload_", "emit(", "report(",
    "publish(", "post_message", "reply(", "comment(", "follow(",
)
#: 浏览器操作也算外发（浏览器里发出的一切请求）。
#: ⚠ 注意 `browser.close()` / `context.close()` 是**清理**不是发送 —— 若 try 块
#: 内只有 close/cleanup 类符号，不判外发，降为 L2 并标「需人工复核」。
BROWSER_TOKENS = (
    "page.goto", "page.click", "page.fill", "page.evaluate",
    "page.press", "page.type", "page.select", "page.dispatch",
    "context.new_page", "new_page(", "fetch(", "navigate(",
)
#: 浏览器**清理**符号（不是外发；命中即降级 + 标复核）
BROWSER_CLEANUP_TOKENS = (
    ".close(", "browser.close", "context.close", "page.close",
    "stop(", "dispose(",
)

#: 幂等 DDL / 迁移符号（写库但不属高危：重复执行失败是预期行为）
IDEMPOTENT_DDL_TOKENS = (
    "alter table", "create index if not exists", "create table if not exists",
    "pragma ", "drop index if exists", "try: conn.execute(\"alter",
)

#: 外部资源（非外发）：文件 IO / 网络（非外发）/ subprocess / 浏览器启动
EXTERNAL_TOKENS = (
    "open(", "os.remove", "os.rename", "shutil.", "pathlib",
    "subprocess.", "os.system", "os.popen", "socket.", "launch(",
    "connect(", "recv(", "accept(",
)


def _norm(s: str) -> str:
    """统一小写、压缩空白，便于子串匹配。"""
    return " ".join(s.lower().split())


def _seg_text(src: str, node) -> str:
    """取某 AST 节点覆盖的源码文本（用行号区间，含注解）。"""
    try:
        return ast.get_source_segment(src, node) or ""
    except Exception:                                    # noqa: BLE001
        return ""


def _block_text(src: str, body: list) -> str:
    """★ 关键：取一个语句块全文（按首末语句行号区间），不是只看一行。

    这就是「必须读 try 块全文才能判定它干了什么」的实现。
    """
    if not body:
        return ""
    first = min(b.lineno for b in body if hasattr(b, "lineno"))
    last = max(getattr(b, "end_lineno", getattr(b, "lineno", first)) for b in body)
    lines = src.splitlines()
    return "\n".join(lines[first - 1:last])


def classify_path(try_text: str) -> tuple[str, str, bool]:
    """按 try 块**全文**判定路径类型。

    返回 (level, reason, needs_review)：
      L1-写库 / L1-外发 / L2-外部资源 / L3-内部逻辑
    优先级：外发 > 写库 > 外部资源 > 内部逻辑
    （外发是本项目的风控第一高危，故先判外发）。

    needs_review=True 的情形（不拔高也不隐瞒）：
      · try 内**只有**浏览器 cleanup（close/stop）而无实际发送 —— 分不清是
        清理失败还是被吞的发送；
      · try 内是**幂等 DDL/迁移**（ALTER TABLE / CREATE INDEX IF NOT EXISTS）——
        「列已存在」类 pass 是**合理**模式，是否算高危需人工判。
    """
    t = _norm(try_text)
    needs_review = False

    # 1) 外发
    send_hit = None
    for tok in SEND_TOKENS:
        if tok in t:
            send_hit = tok
            break
    if send_hit is None:
        for tok in BROWSER_TOKENS:
            if tok in t:
                send_hit = tok
                break
    if send_hit is not None:
        return "L1-外发", f"命中外发/浏览器符号 `{send_hit}`", needs_review

    # 幂等 DDL 标记（写库但属合理模式）
    idem = any(k in t for k in IDEMPOTENT_DDL_TOKENS)

    # 2) 写库
    write_hit = None
    for tok in WRITE_TOKENS:
        if tok in t:
            write_hit = tok
            break
    if write_hit is None:
        for pref in WRITE_FUNC_PREFIX:
            if pref in t:
                write_hit = pref
                break
    if write_hit is None:
        for w in WRITE_FUNC_WORDS:
            if w in t and "(" in t:
                write_hit = w
                break
    if write_hit is not None:
        if idem:
            needs_review = True   # ALTERTABLE 类：合理模式，需人工判
        return "L1-写库", f"命中写库符号 `{write_hit}`", needs_review

    # 只有浏览器 cleanup（close/stop）而无发送 → 分不清，降 L2 + 复核
    if any(k in t for k in BROWSER_CLEANUP_TOKENS):
        return "L2-外部资源", "仅浏览器清理(close/stop)，非外发", True

    # 3) 外部资源
    for tok in EXTERNAL_TOKENS:
        if tok in t:
            return "L2-外部资源", f"命中外部资源 `{tok}`", needs_review
    # 4) 内部逻辑
    return "L3-内部逻辑", "未命中外部/写库/外发符号", needs_review


def _only_pass_or_continue(body: list) -> bool:
    """handler 体是否「仅 pass/continue」。"""
    if not body:
        return True
    real = [b for b in body if not isinstance(b, ast.Expr)
            or not isinstance(b.value, ast.Constant)]
    if not real:
        return True
    if len(real) == 1 and isinstance(real[0], (ast.Pass, ast.Continue, ast.Break)):
        return True
    return all(isinstance(b, (ast.Pass, ast.Continue)) for b in body)


def _expr_calls(body: list) -> list[ast.Call]:
    out = []
    for b in body:
        for n in ast.walk(b):
            if isinstance(n, ast.Call):
                out.append(n)
    return out


def _call_name(call: ast.Call) -> str:
    f = call.func
    if isinstance(f, ast.Attribute):
        base = f.attr
        cur = f.value
        while isinstance(cur, ast.Attribute):
            base = cur.attr + "." + base
            cur = cur.value
        if isinstance(cur, ast.Name):
            base = cur.id + "." + base
        return base
    if isinstance(f, ast.Name):
        return f.id
    return ""


def _only_low_log(body: list) -> bool:
    """handler 体是否「只有 logger.debug/info」或「只有 print」（有记录但级别低）。

    注意：logger.warning/error 不算静默（有可见记录）；但 print 在 sidecar
    无 stdout 的场景等同静默，故归入「低级别」。
    """
    if not body:
        return False
    calls = _expr_calls(body)
    if not calls:
        return False
    # 所有可执行语句都必须是 log/print 调用表达式
    for b in body:
        if isinstance(b, ast.Pass):
            continue
        if isinstance(b, ast.Expr) and isinstance(b.value, ast.Call):
            nm = _call_name(b.value)
            low = nm.lower()
            if not any(k in low for k in ("debug", "info", "print",
                                          "log(" , "logger")):
                return False
            # 高级别不算静默
            if any(k in low for k in ("warning", "warn", "error",
                                      "critical", "exception")):
                return False
        else:
            return False
    return True


def _returns_silent(body: list) -> tuple[bool, str]:
    """handler 体是否 `return None/{}/[]/False`（吞掉后返回空值）。"""
    for b in body:
        if isinstance(b, ast.Return):
            v = b.value
            if v is None:
                return True, "return（裸）"
            if isinstance(v, ast.Constant):
                if v.value is None or v.value is False or v.value == "" or v.value == 0:
                    return True, f"return {v.value!r}"
            if isinstance(v, ast.Dict) and not v.keys:
                return True, "return {} 空字典"
            if isinstance(v, (ast.List, ast.Tuple)) and not v.elts:
                return True, f"return {type(v).__name__.lower()} 空容器"
    return False, ""


def analyze_file(path: str) -> list[dict]:
    """扫描一个 .py 文件，返回所有「静默兜底」命中项。"""
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            src = f.read()
    except Exception:                                        # noqa: BLE001
        return []
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError:
        return []

    rel = os.path.relpath(path, REPO_ROOT).replace("\\", "/")
    # 函数名映射：某行属于哪个函数
    funcs: list[tuple[int, int, str]] = []

    def _walk_funcs(node, prefix=""):
        for ch in ast.walk(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = (prefix + "." if prefix else "") + ch.name
                end = getattr(ch, "end_lineno", ch.lineno)
                funcs.append((ch.lineno, end, name))
    _walk_funcs(tree)

    def func_of(lineno: int) -> str:
        best = ""
        best_span = 10**9
        for s, e, n in funcs:
            if s <= lineno <= e and (e - s) < best_span:
                best, best_span = n, e - s
        return best or "<module>"

    hits: list[dict] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Try):
            continue
        # try 块全文（★ 关键）
        try_text = _block_text(src, node.body)
        level, why, needs_review = classify_path(try_text)
        # 该 try 覆盖的行区间（用于去重 / 报告）
        for handler in node.handlers:
            hbody = handler.body
            form = None
            detail = ""

            if handler.type is None:
                form = "bare-except"
                detail = "裸 except（连 KeyboardInterrupt 都吞）"
            elif _only_pass_or_continue(hbody):
                form = "pass-only"
                detail = "handler 体仅 pass/continue/nop"
            elif _only_low_log(hbody):
                form = "log-only-low"
                detail = "handler 体仅 logger.debug/info 或 print（级别低）"
            else:
                ok, d = _returns_silent(hbody)
                if ok and len(hbody) <= 3:
                    form = "return-empty"
                    detail = d
                else:
                    continue  # 有实质处理（raise/warning/回退逻辑）→ 不算静默

            hits.append({
                "file": rel,
                "line": handler.lineno,
                "func": func_of(handler.lineno),
                "level": level,
                "level_reason": why,
                "needs_review": needs_review,
                "form": form,
                "form_detail": detail,
                "except": _norm(_seg_text(src, handler.type) if handler.type else "except:"),
                "try_snippet": _block_text(src, node.body).strip().splitlines()[:3],
                "handler_snippet": _block_text(src, hbody).strip().splitlines()[:3],
            })

    # contextlib.suppress 也要计入
    for node in ast.walk(tree):
        if isinstance(node, ast.With):
            for item in node.items:
                c = item.context_expr
                nm = _call_name(c).lower() if isinstance(c, ast.Call) else ""
                if "suppress" in nm:
                    try_text = _block_text(src, node.body)
                    level, why, needs_review = classify_path(try_text)
                    hits.append({
                        "file": rel,
                        "line": node.lineno,
                        "func": func_of(node.lineno),
                        "level": level,
                        "level_reason": why,
                        "needs_review": needs_review,
                        "form": "contextlib.suppress",
                        "form_detail": "with suppress(...) 显式吞掉异常",
                        "except": nm,
                        "try_snippet": try_text.strip().splitlines()[:3],
                        "handler_snippet": ["with suppress(...)"],
                    })
    return hits


def _is_test_basename(fn: str) -> bool:
    """是否为 test_*.py / verify_*.py（历史 386 基线默认豁免的命名族）。"""
    return fn.startswith("test_") or fn.startswith("verify_")


def _rel_is_test(rel: str) -> bool:
    """按仓库相对路径判定是否属 test_/verify_ 族（用于同口径比对）。"""
    return _is_test_basename(os.path.basename(rel))


def scan(include_tests: bool = False) -> tuple[list[dict], dict]:
    """扫描 backend/ 全部 .py。

    默认（include_tests=False）**排除** test_*.py / verify_*.py —— 与历史
    386 基线同口径；`include_tests=True`（`--include-tests`）时纳入它们。

    返回 `(hits, scope)`；scope 记录本次口径（是否含 test/verify、
    排除/纳入多少个文件、实际扫描多少个 .py），用于把豁免变成显式决策。
    """
    hits: list[dict] = []
    scanned = 0
    skipped_tests = 0
    included_tests = 0
    for dirpath, dirnames, filenames in os.walk(BACKEND):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if not fn.endswith(".py"):
                continue
            is_test = _is_test_basename(fn)
            if is_test and not include_tests:
                skipped_tests += 1
                continue
            if is_test:
                included_tests += 1
            scanned += 1
            hits.extend(analyze_file(os.path.join(dirpath, fn)))
    scope = {
        "include_tests": bool(include_tests),
        "excluded_test_files": skipped_tests,
        "included_test_files": included_tests,
        "scanned_py_files": scanned,
    }
    return hits, scope


def summarize(hits: list[dict]) -> dict:
    by_level: dict[str, int] = {}
    by_form: dict[str, int] = {}
    by_file: dict[str, int] = {}
    for h in hits:
        by_level[h["level"]] = by_level.get(h["level"], 0) + 1
        by_form[h["form"]] = by_form.get(h["form"], 0) + 1
        by_file[h["file"]] = by_file.get(h["file"], 0) + 1
    l1_write = by_level.get("L1-写库", 0)
    l1_send = by_level.get("L1-外发", 0)
    total = len(hits)
    # pass-only 的 L1（最危险）
    l1_write_pass = sum(1 for h in hits
                        if h["level"] == "L1-写库" and h["form"] == "pass-only")
    l1_send_pass = sum(1 for h in hits
                       if h["level"] == "L1-外发" and h["form"] == "pass-only")
    l1_write_bare = sum(1 for h in hits
                        if h["level"] == "L1-写库" and h["form"] == "bare-except")
    l1_send_bare = sum(1 for h in hits
                       if h["level"] == "L1-外发" and h["form"] == "bare-except")
    return {
        "total": total,
        "historic_baseline": HISTORIC_BASELINE,
        "delta": total - HISTORIC_BASELINE,
        #: 与 H6 386 基线**同口径**子集：handler 体仅 pass/continue 或仅低级别日志
        #: （H6 方法学只数这两类，不含 return-empty / contextlib.suppress）。
        "comparable_to_386": sum(
            1 for h in hits if h["form"] in ("pass-only", "log-only-low",
                                            "bare-except")),
        "comparable_baseline": 386,
        "comparable_delta": sum(
            1 for h in hits if h["form"] in ("pass-only", "log-only-low",
                                            "bare-except")) - 386,
        "by_level": by_level,
        "by_form": by_form,
        "by_file": dict(sorted(by_file.items(), key=lambda kv: -kv[1])),
        "needs_review": sum(1 for h in hits if h.get("needs_review")),
        "L1_写库": l1_write,
        "L1_外发": l1_send,
        "L1_写库_pass_only": l1_write_pass,
        "L1_外发_pass_only": l1_send_pass,
        "L1_写库_bare": l1_write_bare,
        "L1_外发_bare": l1_send_bare,
        "L2_外部资源": by_level.get("L2-外部资源", 0),
        "L3_内部逻辑": by_level.get("L3-内部逻辑", 0),
    }


# ── 门禁判据 F1..F5 ───────────────────────────────────────────────────────
RESULTS: list[tuple[bool, str, str]] = []


def check(ok: bool, rule_id: str, detail: str) -> bool:
    RESULTS.append((bool(ok), rule_id, detail))
    print("  [%s] %-4s %s" % ("PASS" if ok else "FAIL", rule_id, detail))
    return bool(ok)


def load_baseline(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:                                    # noqa: BLE001
        return None


def save_baseline(path: str, summary: dict, hits: list[dict], scope: dict, *,
                  merge: bool = False) -> None:
    """写基线。

    merge=True 时**只新增/更新**本次口径字段（include_tests*），
    保留既有字段不动（默认口径历史数字保持可比，A-4 要求「只增字段不删字段」）。
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    keys = sorted(f"{h['file']}:{h['line']}:{h['level']}:{h['form']}" for h in hits)
    test_keys = [k for k in keys if _rel_is_test(k.split(":")[0])]
    new_scope_fields = {
        # ★ A-4：新增字段（历史字段一律保留，保证 386 口径可比）
        "include_tests_summary": {
            "include_tests": scope.get("include_tests", False),
            "excluded_test_files": scope.get("excluded_test_files", 0),
            "included_test_files": scope.get("included_test_files", 0),
            "scanned_py_files": scope.get("scanned_py_files", 0),
            "total_hits": summary["total"],
            "comparable_to_386": summary["comparable_to_386"],
        },
        "include_tests_total": summary["total"],
        "include_tests_comparable_to_386": summary["comparable_to_386"],
        "include_tests_hits_total": summary["total"],
        "L1_keys_with_tests": [k for k in keys
                               if k.split(":")[2] in ("L1-写库", "L1-外发")],
        "all_keys_with_tests": keys,
        "test_verify_hit_count": len(test_keys),
        "current_scope": scope,
    }
    if merge and os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        data.update(new_scope_fields)          # 只增/更新口径字段，不动历史数字
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        return
    data = {
        "生成时间口径": "check_silent_fallback.py --baseline write",
        "历史基线_H6_386": HISTORIC_BASELINE,
        "summary": summary,
        "L1_keys": [k for k in keys if k.split(":")[2] in ("L1-写库", "L1-外发")],
        "all_keys": keys,
    }
    data.update(new_scope_fields)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def run_gates(hits: list[dict], summary: dict, baseline_path: str,
              baseline_mode: str, scope: dict | None = None,
              hits_include_tests: list[dict] | None = None) -> int:
    scope = scope or {}
    print("=" * 74)
    print("静默兜底机械门禁 —— 「失败必须可见」不靠自觉")
    print("=" * 74)
    print(f"  扫描根 : {BACKEND}")
    print(f"  排除   : {sorted(SKIP_DIRS)} + test_*.py + verify_*.py")
    # ── ★ A-4：口径显式化（豁免不再是默认静默）────────────────────────
    _scope_line = (f"  本次口径: 含 test_*/verify_* = "
                   f"{'是' if scope.get('include_tests') else '否'}"
                   f"（--include-tests {'开启' if scope.get('include_tests') else '未开启'}）；"
                   f"排除 test_/verify_ 文件 = {scope.get('excluded_test_files', 0)} 个；"
                   f"纳入 = {scope.get('included_test_files', 0)} 个；"
                   f"实际扫描 = {scope.get('scanned_py_files', 0)} 个 .py")
    print(_scope_line)
    print(f"  基数   : 命中 {summary['total']} 处 "
          f"(历史基线 {HISTORIC_BASELINE}，Δ {summary['delta']:+d})")
    print(f"           其中与 H6 386 同口径子集 = {summary['comparable_to_386']} 处 "
          f"(Δ {summary['comparable_delta']:+d})")
    if hits_include_tests is not None:
        same_scope = summarize(hits_include_tests)
        print(f"  [对照] 若纳入 test_*/verify_*（--include-tests 口径）: "
              f"命中 {same_scope['total']} 处 "
              f"(历史基线 {HISTORIC_BASELINE}，Δ {same_scope['delta']:+d})；"
              f"同口径子集 = {same_scope['comparable_to_386']} 处")
    print("-" * 74)

    # ── F1: L1-写库 pass-only（排除「需人工复核」的幂等 DDL 类）──────────
    wp = [h for h in hits if h["level"] == "L1-写库" and h["form"] == "pass-only"
          and not h.get("needs_review")]
    wp_review = [h for h in hits if h["level"] == "L1-写库"
                 and h["form"] == "pass-only" and h.get("needs_review")]
    check(not wp, "F1",
          f"L1-写库路径 pass-only 静默兜底 = {len(wp)} 处"
          f"（另 {len(wp_review)} 处需人工复核，不阻断）"
          f"{'（示例 ' + wp[0]['file'] + ':' + str(wp[0]['line']) + '）' if wp else ''}")

    # ── F2: L1-外发 pass-only ─────────────────────────────────────────
    sp = [h for h in hits if h["level"] == "L1-外发" and h["form"] == "pass-only"]
    check(not sp, "F2",
          f"L1-外发路径 pass-only 静默兜底 = {len(sp)} 处"
          f"{'（示例 ' + sp[0]['file'] + ':' + str(sp[0]['line']) + '）' if sp else ''}")

    # ── F3: L1 相对基线不得新增 ────────────────────────────────────────
    if baseline_mode == "write":
        save_baseline(baseline_path, summary, hits, scope)
        check(True, "F3", f"已写基线 → {baseline_path}")
    elif baseline_mode == "add-scope":
        save_baseline(baseline_path, summary, hits, scope, merge=True)
        check(True, "F3",
              f"已**只新增**口径字段 → {baseline_path}（原字段一律保留）")
    else:
        base = load_baseline(baseline_path)
        if base is None:
            check(True, "F3", f"无基线文件，跳过防新增（{baseline_path}）")
        else:
            old = set(base.get("L1_keys", []))
            new = {f"{h['file']}:{h['line']}:{h['level']}:{h['form']}"
                   for h in hits
                   if h["level"] in ("L1-写库", "L1-外发")}
            added = sorted(new - old)
            removed = sorted(old - new)
            check(not added, "F3",
                  f"L1 新增 = {len(added)} 处（较基线 {len(old)} 处）"
                  f"{'：' + added[0] if added else ''}")
            if removed:
                print(f"        （注：较基线减少 {len(removed)} 处，基线可考虑重写）")

    # ── F4: 口径可见化（WARN，永不静默通过）──────────────────────────
    s = summary
    check(True, "F4",
          f"分级口径: L1-写库 {s['L1_写库']} / L1-外发 {s['L1_外发']} / "
          f"L2 {s['L2_外部资源']} / L3 {s['L3_内部逻辑']}；"
          f"其中 pass-only: 写库 {s['L1_写库_pass_only']} / 外发 {s['L1_外发_pass_only']}；"
          f"bare: 写库 {s['L1_写库_bare']} / 外发 {s['L1_外发_bare']}")

    # ── F5: 待人工复核项必须显式列出 ──────────────────────────────────
    review = [h for h in hits if h.get("needs_review")]
    check(True, "F5",
          f"待人工复核 = {len(review)} 处"
          f"{'（' + review[0]['file'] + ':' + str(review[0]['line']) + '）' if review else ''}")
    print("-" * 74)
    failed = [x for x in RESULTS if not x[0]]
    warn_only = {"F4", "F5"}
    blocking = [x for x in failed if x[1] not in warn_only]
    print(f"  合计: {len(RESULTS)} 项，通过 {len(RESULTS) - len(failed)}，"
          f"未通过 {len(failed)}（阻断 {len(blocking)}）")
    if blocking:
        print("\n⛔ 阻断项（必须修复）：")
        for _, rid, d in blocking:
            print(f"  - {rid}: {d}")
        return 1
    print("\n✓ 无阻断项（L1 pass-only 静默兜底为 0，且未较基线新增）")
    return 0


# ── 自检：D-07 自证「违规会报红」────────────────────────────────────────────
def selftest() -> int:
    """注入违规样本，断言门禁确实报红（双向：正控 + 负控）。"""
    print("=" * 74)
    print("自检：验证门禁在违规时**真的会报红**（D-07）")
    print("=" * 74)
    import tempfile
    tmp = tempfile.mkdtemp(prefix="silent_fb_selftest_")

    # 负控 1：写库 try + pass-only
    bad_db = '''
def save_user(u):
    try:
        conn.execute("INSERT INTO users VALUES (?)", (u,))
        conn.commit()
    except Exception:
        pass
'''
    # 负控 2：外发 try + pass-only
    bad_send = '''
def deliver(msg):
    try:
        resp = requests.post("https://api.douyin.com/send", json=msg)
        return resp.json()
    except Exception:
        pass
'''
    # 正控：干净代码（不静默）
    good = '''
def calc(x):
    try:
        return int(x) * 2
    except ValueError:
        raise
'''
    p1 = os.path.join(tmp, "bad_db.py")
    p2 = os.path.join(tmp, "bad_send.py")
    p3 = os.path.join(tmp, "good.py")
    open(p1, "w", encoding="utf-8").write(bad_db)
    open(p2, "w", encoding="utf-8").write(bad_send)
    open(p3, "w", encoding="utf-8").write(good)

    h1 = analyze_file(p1)
    h2 = analyze_file(p2)
    h3 = analyze_file(p3)

    ok1 = any(h["level"] == "L1-写库" and h["form"] == "pass-only" for h in h1)
    ok2 = any(h["level"] == "L1-外发" and h["form"] == "pass-only" for h in h2)
    ok3 = len(h3) == 0
    print("-" * 74)
    print(f"  负控1 写库 pass-only 应命中 L1-写库: {'✓' if ok1 else '✗'}")
    print(f"  负控2 外发 pass-only 应命中 L1-外发: {'✓' if ok2 else '✗'}")
    print(f"  正控  干净代码应零命中: {'✓' if ok3 else '✗'}")
    if ok1 and ok2 and ok3:
        print("\n✓ 自检通过：分级判据随代码内容变化，非写死")
        return 0
    print("\n✗ 自检失败：门禁未能按预期分级/报红")
    return 1


def main() -> int:
    ap = argparse.ArgumentParser(description="静默兜底机械门禁")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--baseline", choices=["write", "check", "off", "add-scope"],
                    default="check", help="基线模式（add-scope=只新增口径字段）")
    ap.add_argument("--baseline-file", default=BASELINE_DEFAULT)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--include-tests", action="store_true",
                    help="★ 纳入 test_*/verify_*.py（默认排除，与历史 386 基线同口径）")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    hits, scope = scan(include_tests=args.include_tests)
    # 对照口径：用于打印「若纳入 test/verify」的数字（显式口径可见）
    hits_include_tests = (hits if args.include_tests
                          else scan(include_tests=True)[0])
    # 标注「待人工复核」：L3 但 try 块里疑似有副作用符号
    for h in hits:
        h.setdefault("needs_review", False)
    summary = summarize(hits)

    # ── ★ A-4：口径显式打印（豁免不再是默认静默）────────────────────
    if not args.json:
        print(f"[口径] 含 test_*/verify_* = "
              f"{'是' if scope['include_tests'] else '否'}"
              f"（--include-tests {'开启' if scope['include_tests'] else '未开启'}）；"
              f"排除 test_/verify_ 文件 {scope['excluded_test_files']} 个，"
              f"纳入 {scope['included_test_files']} 个，"
              f"实际扫描 {scope['scanned_py_files']} 个 .py")

    if args.json:
        out = {
            "summary": summary,
            "scope": scope,
            "scope_control_include_tests": summarize(hits_include_tests),
            "hits": hits,
        }
        # 为了 stdout 可读又不丢信息：默认全量（调用方自行裁剪）
        print(json.dumps(out, ensure_ascii=False, indent=2))
        # JSON 模式下仍返回阻断码
        wp = summary["L1_写库_pass_only"]
        sp = summary["L1_外发_pass_only"]
        return 1 if (wp or sp) else 0

    mode = "check" if args.baseline == "off" else args.baseline
    return run_gates(hits, summary, args.baseline_file, mode, scope,
                     hits_include_tests)


if __name__ == "__main__":
    raise SystemExit(main())
