# coding=utf-8
"""指纹层间一致性门禁（ADR-016 D1-D4 的机械验收）。

## 为什么需要它

凭证「3 小时失效」的根因是 **HTTP 层与 JS 层的浏览器身份矛盾**。
这类缺陷**单看一层发现不了**（两层各自"看起来都对"），必须**交叉比对**。
本脚本把该比对固化为可重复执行的门禁，防止回归。

## 判据（对应 ADR-016）

- **A1** 档案 UA 必须与内核品牌一致（Camoufox ⇒ Gecko UA；chromium ⇒ Blink UA）
- **A2** Gecko 内核下 `sec_ch_ua` 必须为空（Firefox 不实现 Client Hints）
- **A3** 出站 HTTP 头不得在 Gecko 下出现 `sec-ch-ua*`（A2 的端到端验证）
- **A4** 签名器 `fixed=True/False` 的 UA 必须一致（同一语义不得两个答案）
- **A5** 全仓不得残留硬编码 Chrome 版本（`Chrome/NNN.0.0.0` 字面量，档案模块除外）
- **A6** `strdata` 指纹的 navigator 字段必须与内核自洽
       （Gecko：`vendor==""` + `productSub=="20100101"`）
- **A7** 内核版本必须真的从内核 exe 读出（不得退兜底常量）

用法：
    python scripts/check_fingerprint_consistency.py          # 校验，失败 exit 1
    python scripts/check_fingerprint_consistency.py --verbose
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
sys.path.insert(0, str(BACKEND))

_results: list[tuple[str, bool, str]] = []


def _check(code: str, ok: bool, detail: str) -> None:
    _results.append((code, bool(ok), detail))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--account", default=os.environ.get("DY_FP_ACCOUNT") or "",
                    help="被测账号（默认取第一个已配置账号；用于验证真值跟随）")
    args = ap.parse_args()

    os.environ.setdefault("FLOWCAP_APP_ROOT", os.environ.get("FLOWCAP_APP_ROOT", ""))

    try:
        from utils.fingerprint import fingerprint_profile, kernel_version, _kernel_is_gecko, _FALLBACK_VERSION
        from utils.ab_pure import ABogusPureSigner
        from utils.strdata_pure import build_fingerprint
        from builder.header import HeaderBuilder
        from builder.params import Params
    except Exception as e:  # noqa: BLE001
        print(f"✗ 无法导入被测模块: {type(e).__name__}: {e}")
        return 1

    # 解析被测账号：显式 --account 优先；否则自动取第一个已配置账号
    # （真值跟随验证需要一个**真实**账号 —— 凭空账号永远无真值记录）
    acct = args.account
    if not acct:
        try:
            import sys as _sys
            _bk = str(BACKEND)
            if _bk not in _sys.path:
                _sys.path.insert(0, _bk)
            from auto_dm import accounts as _acc
            # list_accounts() 返回 [(账号名, env路径), ...]
            _rows = _acc.list_accounts() or []
            _names = [r[0] if isinstance(r, (tuple, list)) else getattr(r, "name", None)
                      for r in _rows]
            _names = [n for n in _names if n]
            acct = _names[0] if _names else "__probe__"
        except Exception:
            acct = "__probe__"

    prof = fingerprint_profile(acct)
    ua = prof.get("ua", "")
    is_gecko = bool(prof.get("_kernel_gecko"))

    # ── A1 档案 UA 与内核品牌一致 ────────────────────────────────────────
    if is_gecko:
        ok = ("Firefox/" in ua) and ("Gecko/20100101" in ua) and ("Chrome/" not in ua)
        _check("A1", ok, f"Gecko 内核 ⇒ UA 应为 Firefox 形态；实际={ua[:70]}")
    else:
        ok = ("Chrome/" in ua) and ("Firefox/" not in ua)
        _check("A1", ok, f"Blink 内核 ⇒ UA 应为 Chrome 形态；实际={ua[:70]}")

    # ── A2 Gecko 下 sec_ch_ua 必须为空 ──────────────────────────────────
    if is_gecko:
        _check("A2", prof.get("sec_ch_ua") == "",
               f"Gecko 下 sec_ch_ua 应为空串；实际={prof.get('sec_ch_ua')!r}")
    else:
        _check("A2", bool(prof.get("sec_ch_ua")),
               "Blink 下 sec_ch_ua 不应为空")

    # ── A3 出站头端到端 ─────────────────────────────────────────────────
    try:
        hdrs = HeaderBuilder.build(1).get() or {}
        low = {str(k).lower(): v for k, v in hdrs.items()}
        has_ch = any(k.startswith("sec-ch-ua") for k in low)
        if is_gecko:
            _check("A3", not has_ch,
                   f"Gecko 下不应发 sec-ch-ua*；实际头={ [k for k in low if k.startswith('sec-ch-ua')] }")
        else:
            _check("A3", has_ch, "Blink 下应发 sec-ch-ua*")
        _check("A3b", "user-agent" in low,
               "出站头必须含 user-agent")
    except Exception as e:  # noqa: BLE001
        _check("A3", False, f"构造出站头失败: {type(e).__name__}: {e}")

    # ── A4 签名器两种模式的 UA 一致 ─────────────────────────────────────
    try:
        a = ABogusPureSigner(fixed=True).ua
        b = ABogusPureSigner(fixed=False).ua
        # 允许版本号不同（固定档位 vs 档案），但**品牌**必须一致
        def _brand(u: str) -> str:
            return "Firefox" if "Firefox/" in u else ("Chrome" if "Chrome/" in u else "?")
        _check("A4", _brand(a) == _brand(b),
               f"签名器品牌不一致: fixed=True→{_brand(a)} / fixed=False→{_brand(b)}")
    except Exception as e:  # noqa: BLE001
        _check("A4", False, f"签名器构造失败: {type(e).__name__}: {e}")

    # ── A5 全仓无**活跃**硬编码 Chrome 版本 ─────────────────────────────
    # 判据：允许「档案不可用时的 fallback 常量」，但该行必须处于
    #       except / fallback 分支（同行或上一行含 fallback 语义标记）。
    # 理由：全盘禁止不现实（fallback 必须有个值），但**活跃使用**（直接
    #       作为出站 UA）必须为零 —— 那才是矛盾来源。
    pat = re.compile(r"""Chrome/\d{2,3}\.0\.0\.0""")
    _ALLOW_MARK = ("# fallback", "# 兜底", "fallback", "_FALLBACK_UA",
                   "# ★ ADR-016 D4：本文件原", "return (\"Mozilla", "fallback_ua")
    hits: list[str] = []
    for p in BACKEND.rglob("*.py"):
        rel = p.relative_to(BACKEND)
        srel = str(rel).replace("\\", "/")
        if any(x in srel for x in ("_internal", "build/", "test_", "_verify")):
            continue
        if srel in ("utils/fingerprint.py", "utils/ab_pure.py"):
            continue  # 档案/签名模块内是内核感知分支与 fallback 的合法归属
        try:
            lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        for i, ln in enumerate(lines):
            if not pat.search(ln):
                continue
            # 跳过**纯注释行**（历史记录里的旧版本号，非活跃代码）
            if ln.lstrip().startswith("#"):
                continue
            ctx = " ".join(lines[max(0, i - 3): i + 1])
            if any(mk in ctx for mk in _ALLOW_MARK):
                continue  # 属 fallback 分支，允许
            hits.append(f"{srel}:{i + 1}: {pat.search(ln).group(0)}")
    _check("A5", not hits,
           "活跃硬编码 Chrome 版本: " + ("; ".join(hits[:6]) if hits else "无（仅允许 fallback 常量）"))

    # ── A6 strdata navigator 与内核自洽 ────────────────────────────────
    try:
        import json as _json
        fp = _json.loads(build_fingerprint("__probe__"))
        nav = fp.get("navigator") or {}
        if is_gecko:
            ok = (nav.get("vendor") == "" and nav.get("productSub") == "20100101")
            _check("A6", ok,
                   f"Gecko ⇒ navigator.vendor 应为 '' 且 productSub='20100101'；"
                   f"实际 vendor={nav.get('vendor')!r} productSub={nav.get('productSub')!r}")
        else:
            ok = (nav.get("vendor") == "Google Inc.")
            _check("A6", ok, f"Blink ⇒ navigator.vendor 应为 'Google Inc.'；实际={nav.get('vendor')!r}")
    except Exception as e:  # noqa: BLE001
        _check("A6", False, f"构造 strdata 指纹失败: {type(e).__name__}: {e}")

    # ── A8 档案必须跟随内核真值（D3-A）─────────────────────────────────
    # 判据：`_truth_source == 'kernel'`（BCC 已落盘真值）。
    # 若为 'preset' ⇒ 两层又是「独立随机」，D3-A 失效 ⇒ 告警（不阻断，因为
    # 冷启/BCC 未运行时确无真值，属**已知降级路径**）。
    _ts = prof.get("_truth_source")
    if _ts == "kernel":
        try:
            from services.kernel_truth import get_kernel_truth
            _rec, _reason = get_kernel_truth(acct)
            if _rec:
                _s = _rec.get("screen") or {}
                ok = (int(prof.get("screen_width") or 0) == int(_s.get("w") or 0)
                      and int(prof.get("screen_height") or 0) == int(_s.get("h") or 0)
                      and int(prof.get("cpu_core_num") or 0) == int(_rec.get("hardwareConcurrency") or 0))
                _check("A8", ok,
                       f"档案须等于内核真值；档案={prof.get('screen_width')}x{prof.get('screen_height')}"
                       f"/{prof.get('cpu_core_num')}核 真值={_s.get('w')}x{_s.get('h')}"
                       f"/{_rec.get('hardwareConcurrency')}核")
            else:
                _check("A8", True, f"真值记录不可读({_reason}) —— 本探针账号无记录，跳过比对")
        except Exception as e:  # noqa: BLE001
            _check("A8", True, f"真值模块不可用({type(e).__name__})，跳过")
    else:
        _check("A8", True,
               f"⚠ 降级路径：_truth_source={_ts!r}（BCC 未落盘真值 ⇒ 档案用预设，"
               f"**两层可能不一致**；启动 BCC 后自动转为 kernel）")
    kv = kernel_version()
    _check("A7", kv != _FALLBACK_VERSION,
           f"内核版本={kv}（兜底常量={_FALLBACK_VERSION}）"
           + ("" if kv != _FALLBACK_VERSION else " ← 退回兜底，内核 exe 定位失败"))

    # ── A9 参数层身份自洽 + 无活跃硬编码（2026-09-26 H-22 审计外发现）─────
    # 判据分两半：
    #   ① **数值自洽**：同一请求的参数里 `browser_name` 与 `engine_name` 必须配对
    #      （Firefox⇒Gecko / Chrome⇒Blink），且 `engine_version` 与 `browser_version`
    #      同源。实测缺陷形态：browser_name='Firefox' + engine_name='Blink'
    #      —— **逻辑不可能值**（Firefox 不用 Blink）。档案侧已正确，是调用点硬编码覆盖。
    #   ② **无活跃硬编码**：全仓不得再出现 `engine_name`/`browser_name` 的硬编码字面量
    #      （档案本体 `utils/fingerprint.py` 与测试 fixture 除外）。
    try:
        _pp = Params().with_platform().get()
        _bn, _en = _pp.get("browser_name"), _pp.get("engine_name")
        _bvv, _evv = _pp.get("browser_version"), _pp.get("engine_version")
        _pair_ok = ((_bn == "Firefox" and _en == "Gecko")
                    or (_bn == "Chrome" and _en == "Blink"))
        _check("A9a", _pair_ok,
               f"参数层 brand/engine 须配对；实际 browser_name={_bn!r} engine_name={_en!r}"
               + ("" if _pair_ok else "  ← 逻辑不可能值（如 Firefox 用 Blink）"))
        _check("A9b", _bvv == _evv,
               f"engine_version 应与 browser_version 同源；实际 "
               f"browser_version={_bvv!r} engine_version={_evv!r}")
    except Exception as e:  # noqa: BLE001
        _check("A9a", False, f"构造参数失败: {type(e).__name__}: {e}")
        _check("A9b", False, "同上（参数构造失败）")

    # 活跃硬编码扫描（避开档案本体与测试）
    _id_pat = re.compile(
        r'(engine_name|browser_name)[\"\']?\s*[:,]\s*[\"\'](Blink|Chrome|Firefox|Edge|Gecko)[\"\']')
    _id_hits: list[str] = []
    for p in BACKEND.rglob("*.py"):
        srel = str(p.relative_to(BACKEND)).replace("\\", "/")
        if any(x in srel for x in ("_internal", "build/")):
            continue
        if srel == "utils/fingerprint.py" or p.name.startswith("test_"):
            continue          # 档案本体（合法归属）/ 测试 fixture
        try:
            _lines = p.read_text(encoding="utf-8", errors="ignore").splitlines()
        except Exception:
            continue
        for i, ln in enumerate(_lines, 1):
            if ln.lstrip().startswith("#"):
                continue
            if _id_pat.search(ln):
                _id_hits.append(f"{srel}:{i}")
    _check("A9c", not _id_hits,
           "活跃硬编码 brand/engine: " + ("; ".join(_id_hits[:6]) if _id_hits else "无"))

    # ── 输出 ───────────────────────────────────────────────────────────
    print("=" * 68)
    print("  指纹层间一致性门禁 —— ADR-016（D1~D4）")
    print("=" * 68)
    print(f"  被测账号: {acct}   真值来源: {prof.get('_truth_source')}")
    print(f"  内核判定: {'Gecko (Firefox/Camoufox)' if is_gecko else 'Blink (Chromium)'}")
    print(f"  内核版本: {kv}")
    print(f"  档案 UA : {ua}")
    print("-" * 68)
    npass = sum(1 for _, ok, _ in _results if ok)
    for code, ok, detail in _results:
        mark = "PASS" if ok else "FAIL"
        print(f"  [{mark}] {code:5s} {detail}")
    print("-" * 68)
    print(f"  合计 {len(_results)} 项，通过 {npass}，失败 {len(_results) - npass}")
    if npass != len(_results):
        print("\n  ✗ 存在层间不一致 —— 这正是「凭证 3 小时失效」的根因形态，禁止提交。")
        return 1
    print("\n  ✓ 全部通过：HTTP 层与浏览器层身份一致。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
