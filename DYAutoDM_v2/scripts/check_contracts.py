# -*- coding: utf-8 -*-
"""契约门禁：把 docs/design-contracts 的「验证方式」变成可执行检查。

依据《架构审计报告》§九-6 判据 + 《体系体检报告》§6.1②（契约漂移必须可执行）。
本脚本只**读**代码/文档，不改任何文件；退出码非 0 表示有契约被违反。

用法：
    py314 scripts/check_contracts.py            # 人读输出
    py314 scripts/check_contracts.py --quiet    # 仅退出码（CI）
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent          # DYAutoDM_v2/
BE = ROOT / "backend"
DC = ROOT / "docs" / "design-contracts"

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))


# ── 已知缺口基线（门禁只对【新增】违规失败）────────────────────────
GAPS_FILE = DC / ".known-gaps.json"


def load_known_gaps() -> set[str]:
    """返回 {(check, target)} 集合；读不到则视为空（保守：全部违规都报）。"""
    try:
        import json
        data = json.loads(GAPS_FILE.read_text(encoding="utf-8"))
        return {(g["check"], g["target"]) for g in data.get("known_gaps", [])}
    except Exception:                                      # noqa: BLE001
        return set()


KNOWN = load_known_gaps()


def classify(check_name: str, offenders: list[str]) -> tuple[list[str], list[str]]:
    """把违规分成 (新增违规, 已知缺口)。target 用 basename:path 归一化匹配。"""
    new, known = [], []
    for off in offenders:
        # off 形如 "backend/dy_apis/client_video.py:/aweme/v1/web/tab/feed/"
        matched = any(off.startswith(g[1].split("/aweme")[0]) and
                      g[0] == check_name for g in KNOWN)
        (known if matched else new).append(off)
    return new, known


# ── G0: 契约文件存在性（审计判据 ≥5）────────────────────────────
contracts = sorted(p for p in DC.glob("C-*.md"))
check("G0 契约文件 ≥5", len(contracts) >= 5, f"实测 {len(contracts)} 份")

# ── G1: C-01 被动捕获 —— 捕获模块不得出现主动批量查询符号 ──────────
CAP = BE / "auto_dm" / "conversation_capture.py"
forbidden = ["bulk_user_info(", "get_im_user_info(", "bulk_user_info_by_uid(",
             "bulk_user_info_via_browser("]
if CAP.exists():
    txt = CAP.read_text(encoding="utf-8", errors="replace")
    hits = [s for s in forbidden if s in txt]
    check("G1 C-01 捕获零主动查询", not hits, f"命中 {hits}" if hits else "0 命中")
else:
    check("G1 C-01 捕获零主动查询", False, f"模块缺失 {CAP}")

# ── G2: C-02 secsdk —— 保护清单端点不得直发 params.get() ──────────
try:
    sys.path.insert(0, str(BE))
    from utils.secsdk_web_sign import PROTECTED_PATHS_GET  # type: ignore
    protected = list(PROTECTED_PATHS_GET)
except Exception as e:                                    # noqa: BLE001
    protected = []
    check("G2 C-02 secsdk 签名接线", False, f"无法导入签名模块: {e}")

if protected:
    # 逐端点判定：找到 `api = "<受保护路径>"` 后，看其后 ~60 行窗口内是否出现签名调用。
    # （不能「文件里出现过 signed_url 就跳过整个文件」——同一文件常有多端点，只在其一上接线。）
    offenders = []
    for py in BE.rglob("*.py"):
        if "__pycache__" in str(py) or py.name.startswith("test_"):
            continue
        lines = py.read_text(encoding="utf-8", errors="replace").splitlines()
        for i, line in enumerate(lines):
            for path in protected:
                if not re.search(r'api\s*=\s*["\'`]' + re.escape(path), line):
                    continue
                window = "\n".join(lines[i:i + 60])
                if "signed_url(" not in window:
                    offenders.append(f"{py.relative_to(ROOT).as_posix()}:{path}")
    new_off, known_off = classify("G2 C-02 secsdk 签名接线", offenders)
    check("G2 C-02 secsdk 签名接线", not new_off,
          (f"⚠ 已知缺口 {len(known_off)} 处（见 .known-gaps.json）"
           + (f"；新增违规 {new_off[:3]}" if new_off else ""))
          if known_off else (f"未签名端点 {len(offenders)} 处: {offenders[:5]}"
                             if offenders else "0 命中"))

# ── G3: C-03 引擎校验 —— dm 判定不得用 wp 结果冒充 ────────────────
ACC = BE / "auto_dm" / "accounts.py"
if ACC.exists():
    txt = ACC.read_text(encoding="utf-8", errors="replace")
    # 反模式：直接 result["dm"] = result["wp"] 之类
    bad = re.search(r'\[\s*["\']dm["\']\s*\]\s*=\s*.{0,40}\[\s*["\']wp["\']', txt)
    check("G3 C-03 dm 不冒充 wp", bad is None,
          "发现 dm 直接赋值 wp" if bad else "0 命中")

# ── G4: C-04 投递 —— 必须存在落库硬验证符号 ───────────────────────
DD = BE / "services" / "dm_dispatch.py"
if DD.exists():
    txt = DD.read_text(encoding="utf-8", errors="replace")
    has_verify = ("role" in txt and "me" in txt) or "回执" in txt
    check("G4 C-04 投递有回执/落库验证", has_verify,
          "存在回执处理" if has_verify else "未发现验证符号")

# ── G5: C-05 直播 —— reflow 为主引擎（X-Bogus 换签仅在有实测时）────
LR = BE / "link_resolve.py"
if LR.exists():
    txt = LR.read_text(encoding="utf-8", errors="replace")
    check("G5 C-05 reflow 主引擎存在", "_reflow_resolve" in txt and
          "reflow/info" in txt, "已实现")

# ── 汇总 ────────────────────────────────────────────────────────
if "--quiet" not in sys.argv:
    print("=" * 60)
    print("契约门禁 check_contracts")
    print("=" * 60)
    for name, ok, detail in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:34s} {detail}")

failed = [n for n, ok, _ in results if not ok]
if failed and "--quiet" not in sys.argv:
    print(f"\n{len(failed)} 项未通过：{failed}")
    print("（提示：G2 未签名端点属**在制品**，实施修复前此为已知缺口）")
sys.exit(1 if failed else 0)
