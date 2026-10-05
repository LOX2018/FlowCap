# -*- coding: utf-8 -*-
"""NFR 预算度量脚本（全库审计 §2·4「纸面契约」→ 机制建议 B）。

## 为什么需要它

2026-09-26 全库审计发现：5 份设计契约（`docs/design-contracts/C-01..C-06.md`）
**都写了 NFR 预算表，但全仓零性能度量资产** ⇒ 预算既不可验证也不可回归防护，
本质是「纸面契约」。本脚本是把它变成**可跑、可读数、会变红**的最小度量。

## 测什么（契约出处）

| 编号 | 指标 | 预算 | 契约出处 |
|---|---|---|---|
| NFR-06-1 | `UidSink.mark_seen` 写库延迟（单次 INSERT/UPDATE） | ≤ 5ms | C-06-live-lead-sink.md §5 L84 |
| NFR-06-2 | `UidSink.should_send` 内存判定 | ≤ 1ms（**非**零 DB 查：稳态每次 2 次 `kv_store` 读） | C-06-live-lead-sink.md §5 L85 |
| NFR-06-3 | `aggregate_text` 单次追加 | ≤ 10ms（文本拼接 ≤ 2000 字符） | C-06-live-lead-sink.md §5 L86 |

其余 NFR 项依赖真实网络 / 浏览器，**离线测不了** ⇒ 显式列入 `NOT_MEASURABLE`
（见 `NOT_MEASURABLE` 表），**不假装测了**。

## 隔离铁律（不可绕过）

- 用 `tempfile.mkdtemp()` 建临时目录，并把 `DY_APP_ROOT` 指到它 —— 这套样式
  照抄 `backend/test_uid_sink_ext.py` 的模块级隔离说明；
- 度量**前**先 `_assert_sandbox()`：校验 `database._db_path()` 与真实 PRAGMA
  database_list 落在临时目录内，否则**直接退出**（拒跑，而不是先写再说）；
- 生产库 `C:\\temp\\dyautodm_design\\members\\...\\dyautodm.db` **绝不触碰**；
- 跑完 `database.reset_connection()` + `shutil.rmtree` 清理临时目录。

## D-07 负控（`--selftest`）

负控必须做在**注入**上，绝不改产品代码。本脚本对每项注入一条人工慢路径：

- NFR-06-1 / NFR-06-3：monkeypatch `services.high_value_keywords.score_text`
  （`mark_seen` 的真实调用路径内的打分钩子）加 20ms；
- NFR-06-2：monkeypatch `services.dm_dispatch.cfg`（`should_send` 的真实调用
  路径内的配置读取钩子）每次调用加 5ms（单次 `should_send` 走 4 次 ⇒ >1ms）。

断言：注入后该项**必须变红**（P95 与 max 都超预算），还原（finally）后**必须
回到 PASS**。任一条不成立即判负控失败。

用法：
    python scripts/check_nfr_budget.py                 # 度量，超预算 exit 1
    python scripts/check_nfr_budget.py --json          # 机器可读（供 CI）
    python scripts/check_nfr_budget.py --selftest      # D-07 负控（预期 exit 1）
    python scripts/check_nfr_budget.py -n 200 --warmup 20
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../DYAutoDM_v2/scripts
_ROOT = os.path.dirname(_HERE)                              # .../DYAutoDM_v2
_BACKEND = os.path.join(_ROOT, "backend")

# ---------------------------------------------------------------------------
# ① 隔离：必须在导入 backend 任何模块**之前**建好临时根并接管 Well-known env
#    否则 database/vbrowser 会按默认根解析到 <repo>/data 或会员生产库。
# ---------------------------------------------------------------------------
_TMP = tempfile.mkdtemp(prefix="nfr_budget_")
os.makedirs(_TMP, exist_ok=True)
os.environ["DY_APP_ROOT"] = _TMP
# 会员上下文回退会把 get_db() 重定向到盘上会话指向的生产库 —— 必须清空。
os.environ["DY_MEMBER"] = ""
os.environ.pop("DY_MEMBER_KEY", None)
# 清掉所有会影响本次度量口径的 env 覆盖，保证预算对比可复现。
for _k in ("DY_UID_SINK_COOLDOWN", "DY_UID_SINK_STRICT", "DY_UID_SINK_WINDOW",
           "DY_HIGH_VALUE_WINDOW", "DY_HIGH_VALUE_THRESHOLD", "DY_HIGH_VALUE_LLM",
           "DY_AGGREGATE_MAX_CHARS"):
    os.environ.pop(_k, None)

sys.path.insert(0, _BACKEND)

import database  # noqa: E402
from services import dm_dispatch as dd  # noqa: E402
from services import high_value_keywords as hvk  # noqa: E402

try:
    from loguru import logger
    logger.remove()
    logger.add(sys.stderr, level="WARNING")     # 度量期间只留异常可见，避免刷屏
except Exception:  # noqa: BLE001
    pass

#: production DB —— 本脚本的绝对红线，度量前后只做**只读**比对
PROD_DB = r"C:\temp\dyautodm_design\members\m17db0f8209156f26\data\dyautodm.db"

ACCT_MAIN = "nfr_acct_main"
ACCT_AGG = "nfr_acct_agg"
ACCT_SS = "nfr_acct_ss"
_TEXT = "我在工地上班摔了，腰椎骨折能定几级，赔偿大概是多少？ "
_RUN_TAG = str(int(time.time() * 1000) % 10 ** 7)

BUDGETS = {
    "NFR-06-1": 5.0,
    "NFR-06-2": 1.0,
    "NFR-06-3": 10.0,
}


# ---------------------------------------------------------------------------
# ② 隔离守卫
# ---------------------------------------------------------------------------
class SandboxViolation(RuntimeError):
    """度量库的落点不在临时沙箱内 —— 拒跑（比事后道歉安全）。"""


def _assert_sandbox() -> str:
    want = Path(_TMP).resolve()
    p = Path(str(database._db_path())).resolve()
    try:
        p.relative_to(want)
    except ValueError as e:
        raise SandboxViolation(
            f"database._db_path() 落在沙箱外: {p}（沙箱={want}）") from e
    conn = database.get_db()
    actual = Path(str(conn.execute("PRAGMA database_list").fetchone()[2])).resolve()
    try:
        actual.relative_to(want)
    except ValueError as e:
        raise SandboxViolation(
            f"真实连接落在沙箱外: {actual}（沙箱={want}）") from e
    if os.path.abspath(actual) == os.path.abspath(PROD_DB):
        raise SandboxViolation("连接指向生产库 —— 拒绝执行")
    return str(actual)


# ---------------------------------------------------------------------------
# ③ 统计
# ---------------------------------------------------------------------------
def _pct(samples: list[float], q: float) -> float:
    """最近秩百分位（不插值 —— 样本量小，插值反而失真）。"""
    if not samples:
        return 0.0
    s = sorted(samples)
    k = max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))
    return s[k]


def _stats(samples: list[float]) -> dict:
    return {
        "n": len(samples),
        "p50_ms": round(_pct(samples, 0.50), 3),
        "p95_ms": round(_pct(samples, 0.95), 3),
        "max_ms": round(_pct(samples, 1.00), 3),
        "min_ms": round(_pct(samples, 0.00), 3),
    }


def _verdict(st: dict, budget: float) -> str:
    over = st["p95_ms"] > budget or st["max_ms"] > budget
    return "FAIL" if over else "PASS"


# ---------------------------------------------------------------------------
# ④ 慢路径注入（D-07 负控）—— 只 patch 钩子，绝不动产品源码
# ---------------------------------------------------------------------------
@contextmanager
def _inject_score(delay_ms: float):
    """在 mark_seen 的真实调用路径（关键词打分钩子）上注入延迟。"""
    orig = hvk.score_text

    def _slow(text, *a, **k):
        time.sleep(delay_ms / 1000.0)
        return orig(text, *a, **k)

    hvk.score_text = _slow
    try:
        yield
    finally:
        hvk.score_text = orig


@contextmanager
def _inject_cfg(delay_ms: float):
    """在 should_send 的真实调用路径（配置读取钩子）上注入延迟。"""
    orig = dd.cfg

    def _slow(name, account=""):
        time.sleep(delay_ms / 1000.0)
        return orig(name, account)

    dd.cfg = _slow
    try:
        yield
    finally:
        dd.cfg = orig


def _injector_for(code: str, delay_ms: float):
    if code == "NFR-06-2":
        return _inject_cfg(delay_ms)
    return _inject_score(delay_ms)          # NFR-06-1 / NFR-06-3


# ---------------------------------------------------------------------------
# ⑤ 被测对象构造
# ---------------------------------------------------------------------------
def _build_items(n: int):
    """每项一个 callable 序列（第 i 次调用的闭包）。"""
    sink_ins = dd.UidSink()
    sink_ss = dd.UidSink()
    sink_agg = dd.UidSink()
    agg_uid = f"{_RUN_TAG}-agg-1"
    # 聚合追加前先落一行（这一步不计入计时样本）
    sink_agg.mark_seen(ACCT_AGG, agg_uid, nickname="聚合用户",
                       source="live", text=_TEXT)
    # should_send 的样本池（未见过 ⇒ 走放行判定一次完整路径）
    ss_pool = [f"{_RUN_TAG}-ss-{i}" for i in range(max(16, min(64, n // 4 or 16)))]

    def _c_insert(i):
        uid = f"{_RUN_TAG}-ins-{i}"
        return lambda: sink_ins.mark_seen(
            ACCT_MAIN, uid, nickname="弹幕用户", source="live", text=_TEXT)

    def _c_should_send(i):
        uid = ss_pool[i % len(ss_pool)]
        return lambda: sink_ss.should_send(ACCT_SS, uid)

    def _c_agg():
        return lambda: sink_agg.mark_seen(
            ACCT_AGG, agg_uid, nickname="聚合用户", source="live", text=_TEXT)

    return [
        {
            "code": "NFR-06-1",
            "name": "mark_seen 写库延迟（单次 INSERT/UPDATE）",
            "budget_ms": BUDGETS["NFR-06-1"],
            "ref": "docs/design-contracts/C-06-live-lead-sink.md §5 L84",
            "factory": _c_insert,
            "inject_ms": 20.0,
        },
        {
            "code": "NFR-06-2",
            "name": "should_send 内存判定",
            "budget_ms": BUDGETS["NFR-06-2"],
            "ref": "docs/design-contracts/C-06-live-lead-sink.md §5 L85",
            "factory": _c_should_send,
            "inject_ms": 5.0,
        },
        {
            "code": "NFR-06-3",
            "name": "aggregate_text 单次追加",
            "budget_ms": BUDGETS["NFR-06-3"],
            "ref": "docs/design-contracts/C-06-live-lead-sink.md §5 L86",
            "factory": lambda i: _c_agg(),
            "inject_ms": 20.0,
        },
    ]


# ---------------------------------------------------------------------------
# ⑥ should_send 发起的 SQL 数**旁证**：给连接装计数器，实打实读
# ---------------------------------------------------------------------------
def _count_should_send_queries() -> int:
    """统计**稳态**下一次 should_send 调用实际发起的 SQL 语句数。

    不用 monkeypatch：`sqlite3.Connection.execute` 是只读属性（实测
    AttributeError），改用标准手段 `set_trace_callback` —— 它是 sqlite3 自带的
    语句级探针，比替换方法更贴近真实执行。

    口径：契约 C-06 §5 L85 已订正为「**非**零 DB 查：稳态每次 2 次 `kv_store`
    读」（风控参数经 `cfg()` 实时取配置，刻意不缓存）。故本旁证的数字应与
    契约的「2 次」**相符**；若实现或契约任一漂移，此旁证即暴露偏差。
    """
    conn = database.get_db()
    sink = dd.UidSink()
    sink.should_send(ACCT_SS, f"{_RUN_TAG}-probe")     # 预热 _ensure_loaded
    box = {"n": 0}
    stmts: list[str] = []
    conn.set_trace_callback(
        lambda _stmt: (box.__setitem__("n", box["n"] + 1),
                       stmts.append(str(_stmt).strip())))
    try:
        sink.should_send(ACCT_SS, f"{_RUN_TAG}-probe2")
    finally:
        conn.set_trace_callback(None)
    _count_should_send_queries.last_stmts = stmts      # 供报告取证
    return box["n"]


# ---------------------------------------------------------------------------
# ⑦ 诚实清单：离线测不了的 NFR 项
# ---------------------------------------------------------------------------
NOT_MEASURABLE = [
    {
        "code": "NFR-06-4",
        "name": "窗口到期扫描 ≤ 50ms（每 30s 扫一次，命中 ≤1000 行）",
        "ref": "docs/design-contracts/C-06-live-lead-sink.md §5 L87",
        "reason": "全仓**没有**该扫描 routine —— grep window_end_ts 只有 "
                  "mark_seen 写 / should_send 读 / window_remaining 读三处，"
                  "无定时扫描器可计时；索引 idx_uid_sink_window 已在 "
                  "database.py L308 建好，但无调用方。度量它等于自己造实现，"
                  "读数无契约意义 ⇒ 不测。",
    },
    {
        "code": "NFR-01-1",
        "name": "捕获延迟 ≤ 2s 落库",
        "ref": "docs/design-contracts/C-01-im-capture.md §4 L40",
        "reason": "输入是「前端响应到达」，依赖真实浏览器 WS 推送；"
                  "离线无事件源，且本任务禁止启浏览器/联网。",
    },
    {
        "code": "NFR-01-2",
        "name": "主动请求数恒 0",
        "ref": "docs/design-contracts/C-01-im-capture.md §4 L41",
        "reason": "需在真实会话存续期间统计出站请求，依赖浏览器网络栈；"
                  "另有 test_capability_probe 以静态形态覆盖，此处不重复。",
    },
    {
        "code": "NFR-05-1",
        "name": "解析超时 timeout=15s",
        "ref": "docs/design-contracts/C-05-live-monitoring.md §5 L41",
        "reason": "15s 是网络操作的超时**上限**而非预算耗时；"
                  "真实耗时依赖直播间网页/WebSocket，离线不可测。",
    },
    {
        "code": "NFR-03-1",
        "name": "校验耗时 timeout=8s",
        "ref": "docs/design-contracts/C-03-credential-verification.md §4 L40",
        "reason": "同上：8s 是超时上限；真实校验走浏览器 + 外网接口"
                  "（禁联网/禁浏览器），离线不可测。",
    },
    {
        "code": "NFR-06-5",
        "name": "内存缓存 ≤ 10MB（100k UID）",
        "ref": "docs/design-contracts/C-06-live-lead-sink.md §5 L89",
        "reason": "需真实 100k UID 规模才有效；本脚本只用 N 百条样本"
                  "（且刻意保持小规模以便清理），观测值无意义。",
    },
]


# ---------------------------------------------------------------------------
# ⑧ 主流程
# ---------------------------------------------------------------------------
def run_measure(n: int, warmup: int) -> tuple[list[dict], str]:
    db_file = _assert_sandbox()
    rows = []
    for item in _build_items(n):
        factory = item["factory"]
        # 「先预热、再取样本」：隔离首次 PRAGMA/commit 的冷启动抖动，
        # 让 P50/P95 反映的是**稳态**调用开销（与契约「单次调用延迟」同口径）。
        st = _stats(_sample(factory, n, warmup, 0))
        rows.append({
            "code": item["code"],
            "name": item["name"],
            "budget_ms": item["budget_ms"],
            "ref": item["ref"],
            "samples": n,
            "warmup": warmup,
            **st,
            "status": _verdict(st, item["budget_ms"]),
        })
    _n = _count_should_send_queries()
    rows.append({
        "code": "NFR-06-2b",
        "name": "should_send SQL 数旁证（每次调用发起的 SQL 数，契约称稳态 2 次）",
        "budget_ms": None,
        "ref": "docs/design-contracts/C-06-live-lead-sink.md §5 L85",
        "samples": 1, "warmup": 1,
        "db_queries_per_call": _n,
        "observed_sql": getattr(_count_should_send_queries, "last_stmts", []),
        "p50_ms": None, "p95_ms": None, "max_ms": None, "min_ms": None,
        "status": "INFO",
    })
    return rows, db_file


def _sample(factory, n: int, warmup: int, offset: int) -> list[float]:
    """按「先预热、再取样本」的统一口径采样。

    预热必须走**同 factory**（用不重复的 offset 段），否则「冷启动抖动」
    （首次 PRAGMA / Python 分层编译 / 页缓存未热）会混进样本。
    """
    for i in range(warmup):
        factory(offset + i)()
    out = []
    for i in range(n):
        c = factory(offset + warmup + i)
        t0 = time.perf_counter()
        c()
        out.append((time.perf_counter() - t0) * 1000.0)
    return out


def run_selftest(n: int, warmup: int) -> tuple[list[dict], bool]:
    """D-07 负控：注入 → 必须变红；还原 → 必须回绿。"""
    _assert_sandbox()
    report = []
    all_ok = True
    for item in _build_items(n):
        code, budget = item["code"], item["budget_ms"]
        entry = {"code": code, "name": item["name"], "budget_ms": budget,
                 "inject_ms": item["inject_ms"]}

        # (a) 注入后 —— 期待变红
        with _injector_for(code, item["inject_ms"]):
            red = _sample(item["factory"], n, warmup, 0)
        rst = _stats(red)
        turned_red = _verdict(rst, budget) == "FAIL"

        # (b) 还原后（contextmanager 的 finally 已还原）—— 期待回绿
        green = _sample(item["factory"], n, warmup, 10 ** 6)
        gst = _stats(green)
        restored = _verdict(gst, budget) == "PASS"

        entry.update({
            "injected": rst,
            "injected_status": "FAIL" if turned_red else "PASS",
            "turned_red": turned_red,
            "restored": gst,
            "restored_status": "PASS" if restored else "FAIL",
            "restored_ok": restored,
        })
        all_ok = all_ok and turned_red and restored
        report.append(entry)
    return report, all_ok


def _prod_fingerprint() -> dict:
    try:
        st = os.stat(PROD_DB)
        return {"size": st.st_size, "mtime": round(st.st_mtime, 3),
                "md5": _md5(PROD_DB), "exists": True}
    except OSError as e:
        return {"exists": False, "error": f"{type(e).__name__}: {e}"}


def _md5(path: str, chunk: int = 1 << 20) -> str:
    import hashlib
    h = hashlib.md5()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--selftest", action="store_true", help="D-07 负控")
    ap.add_argument("-n", "--iterations", type=int, default=200)
    ap.add_argument("--warmup", type=int, default=20)
    args = ap.parse_args()

    before = _prod_fingerprint()
    exit_code = 0
    payload = {"tool": "check_nfr_budget", "sandbox_root": _TMP}

    try:
        if args.selftest:
            report, ok = run_selftest(args.iterations, args.warmup)
            payload.update({
                "mode": "selftest",
                "items": report,
                "selftest_ok": ok,
                "selftest_turned_red": all(r["turned_red"] for r in report),
                "selftest_restored_ok": all(r["restored_ok"] for r in report),
            })
            if args.json:
                payload["prod_db_before"] = before
                payload["prod_db_after"] = _prod_fingerprint()
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print("=" * 74)
                print("  NFR 预算度量 —— D-07 负控（注入慢路径，验证「真的会变红」）")
                print("=" * 74)
                print(f"  隔离沙箱: {_TMP}")
                for r in report:
                    print("-" * 74)
                    print(f"  {r['code']}  {r['name']}")
                    print(f"    预算 ≤ {r['budget_ms']}ms   注入 +{r['inject_ms']}ms/钩子调用")
                    print(f"    [注入后] P50={r['injected']['p50_ms']}ms "
                          f"P95={r['injected']['p95_ms']}ms "
                          f"max={r['injected']['max_ms']}ms -> "
                          f"{'RED ✔' if r['turned_red'] else '仍为绿 ✘ 负控失效'}")
                    print(f"    [还原后] P50={r['restored']['p50_ms']}ms "
                          f"P95={r['restored']['p95_ms']}ms "
                          f"max={r['restored']['max_ms']}ms -> "
                          f"{'PASS ✔' if r['restored_ok'] else '未回绿 ✘'}")
                print("=" * 74)
                all_red = payload["selftest_turned_red"]
                print(f"  负控结论: 注入后变红 {'是' if all_red else '否'} / "
                      f"还原后回绿 {'是' if payload['selftest_restored_ok'] else '否'}")
                if ok:
                    print("  ✔ D-07 负控成立：超预算项确实会让门禁变红。")
                    print("  ℹ 本模式 exit code = 1 —— 这是**预期**的红色读数，")
                    print("    用于证明「失败态会变红」，不代表产品违反预算。")
                else:
                    print("  ✘ 负控不成立：存在「注入后不变红」或「还原后不回绿」，")
                    print("    该度量不可信 —— 禁止用它做放行判据。")
                print("=" * 74)
            exit_code = 1 if ok else 2
        else:
            rows, db_file = run_measure(args.iterations, args.warmup)
            constrained = [r for r in rows if r["status"] != "INFO"]
            failures = [r for r in constrained if r["status"] == "FAIL"]
            payload.update({
                "mode": "measure",
                "db_used": db_file,
                "iterations": args.iterations,
                "warmup": args.warmup,
                "items": rows,
                "not_measurable": NOT_MEASURABLE,
                "passed": len(constrained) - len(failures),
                "failed": len(failures),
            })
            if args.json:
                payload["prod_db_before"] = before
                payload["prod_db_after"] = _prod_fingerprint()
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            else:
                print("=" * 74)
                print("  NFR 预算度量 —— C-06 §5（NFR 从「纸面契约」到可读数）")
                print("=" * 74)
                print(f"  隔离沙箱: {_TMP}")
                print(f"  度量库  : {db_file}")
                print(f"  样本/项 : {args.iterations}（另有 {args.warmup} 次预热不计入）")
                print("-" * 74)
                head = f"  {'编号':<10} {'P50':>8} {'P95':>8} {'max':>8} {'预算':>7}  结论"
                print(head)
                for r in rows:
                    if r["status"] == "INFO":
                        print(f"  {r['code']:<10} {'-':>8} {'-':>8} {'-':>8} "
                              f"{'-':>7}  ℹ 旁证：稳态单次调用发起 "
                              f"{r['db_queries_per_call']} 次 SQL"
                              f"（契约 §5 L85 称「**非**零 DB 查：稳态每次 2 次」"
                              f" —— 与实测比对见报告 §5）")
                        for s in r.get("observed_sql", [])[:4]:
                            print(f"             SQL> {s[:110]}")
                        continue
                    mark = "PASS" if r["status"] == "PASS" else "FAIL"
                    print(f"  {r['code']:<10} {r['p50_ms']:>8.3f} {r['p95_ms']:>8.3f} "
                          f"{r['max_ms']:>8.3f} {r['budget_ms']:>7.1f}  {mark}  {r['name']}")
                print("-" * 74)
                n_info = sum(1 for r in rows if r["status"] == "INFO")
                print(f"  单位 ms（perf_counter 实测）｜受预算约束项 "
                      f"{len(rows) - n_info} 个：通过 {payload['passed']} / "
                      f"失败 {payload['failed']}；旁证项 {n_info} 个（不计通过/失败）")
                print("\n  NOT_MEASURABLE（离线测不了，不假装测了）:")
                for m in NOT_MEASURABLE:
                    print(f"    [{m['code']}] {m['name']}  <- {m['ref']}")
                    print(f"        原因: {m['reason']}")
                print("=" * 74)
                if failures:
                    print("  ✘ 存在超预算项。")
                else:
                    print("  ✔ 全部在 C-06 §5 预算内。")
                print("=" * 74)
            exit_code = 1 if failures else 0
    except SandboxViolation as e:
        print(f"✘ 隔离守卫触发，拒绝执行: {e}", file=sys.stderr)
        print(json.dumps({"tool": "check_nfr_budget", "sandbox_violation": str(e)},
                         ensure_ascii=False), file=sys.stderr)
        return 3
    finally:
        # 清理：先释放连接（Windows 上持有 db 句柄会挡住 rmtree），再删临时目录。
        try:
            database.reset_connection()
        except Exception:  # noqa: BLE001
            pass
        # Windows 上 SQLite 的句柄/WAL 有时 **滞后释放**（尤其 Py 3.14 的 GC
        # 时机），直接 rmtree 会静默失败并留下空目录 —— 看起来像「清理成功」
        # 实则漏沙箱。故重试到删除成功为止（最多 5 轮），仍失败则**显式打印
        # 残留路径**而不是静默吞掉 —— 沙箱残留会在磁盘上累积。
        _left = None
        for _ in range(5):
            shutil.rmtree(_TMP, ignore_errors=True)
            if not os.path.isdir(_TMP):
                _left = None
                break
            time.sleep(0.05)
        else:
            _left = _TMP
        if _left:
            print(f"⚠ 临时沙箱未能完全清理，请手动删除: {_left}", file=sys.stderr)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
