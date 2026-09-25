# -*- coding: utf-8 -*-
"""审计红线计数器 —— D-02「20 个产品行为 patch 节点 → 全库审计」的 instrument 层。

## 为何要机械化
声明式判据不会自己运行。上次人工统计时我数出「19 个」，这个数字必须
**可复跑、可核对**，否则每次都要重数一遍且口径易漂移。

## 口径（D-02，用户 2026-09-26 改判：3 → 20）
计数 = 上次全库审计 commit 之后，`type` 为 `feat`/`fix` 且**改产品运行时行为**
的提交。排除：
  - `test` / `docs` / `chore`
  - 虽为 `fix` 但只改验证器本身（门禁脚本、探针判据、守卫测试、版本同步）

用法：
    python scripts/audit_redline_count.py                # 人类可读
    python scripts/audit_redline_count.py --since <sha>  # 指定起点
    python scripts/audit_redline_count.py --json         # 机器可读
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 上次全库审计 commit（D-02 计数起点）
DEFAULT_SINCE = "abd1f29"

#: 审计红线阈值（D-02，用户 2026-09-26 定）
THRESHOLD = 20

#: 只改验证器的 scope —— 命中即不计数
VERIFIER_SCOPES = frozenset({
    "gate", "gates", "test", "tests", "verify", "verifier", "probe",
    "version", "version-sync", "docs", "kb", "gitignore", "guard",
})

#: 只改验证器的 subject 关键词 —— 命中即不计数
VERIFIER_KEYWORDS = (
    "门禁", "判据", "探针", "守卫", "版本同步", "守卫测试",
    "selftest", "gate", "contract check",
)


def git(*args: str) -> str:
    r = subprocess.run(["git", "-C", ROOT, *args],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    return r.stdout


def is_product_behavior(subject: str) -> bool:
    """判定该提交是否计入审计红线。"""
    m = re.match(r"^(feat|fix|test|docs|chore|refactor|perf)(\([^)]*\))?\s*:?\s*(.*)$",
                 subject.strip())
    if not m:
        return False
    typ, scope, rest = m.group(1), (m.group(2) or ""), m.group(3)
    if typ not in ("feat", "fix"):
        return False
    sc = scope.strip("()").lower()
    # scope 命中验证器类 → 不计数
    for s in sc.split(","):
        if s.strip() in VERIFIER_SCOPES:
            return False
    low = rest.lower()
    for kw in VERIFIER_KEYWORDS:
        if kw.lower() in low:
            return False
    return True


def count(since: str) -> dict:
    raw = git("log", f"{since}..HEAD", "--pretty=format:%H%x09%s")
    counted, skipped = [], []
    for line in raw.splitlines():
        if "\t" not in line:
            continue
        sha, subj = line.split("\t", 1)
        (counted if is_product_behavior(subj) else skipped).append(
            {"sha": sha[:7], "subject": subj})
    n = len(counted)
    return {
        "since": since,
        "threshold": THRESHOLD,
        "count": n,
        "remaining": max(0, THRESHOLD - n),
        "triggered": n >= THRESHOLD,
        "counted": counted,
        "skipped_sample": skipped[:8],
        "skipped_total": len(skipped),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=DEFAULT_SINCE)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    r = count(args.since)
    if args.json:
        print(json.dumps(r, ensure_ascii=False, indent=2))
        return 0

    print("=" * 66)
    print("  审计红线计数（D-02 · 阈值 20 个产品行为 patch 节点）")
    print("=" * 66)
    print(f"  计数起点（上次全库审计）: {r['since']}")
    print(f"  已计: {r['count']} / {r['threshold']}   "
          f"剩余: {r['remaining']}")
    print("-" * 66)
    for c in r["counted"][-20:]:
        print(f"    {c['sha']}  {c['subject'][:56]}")
    print("-" * 66)
    print(f"  未计入（验证/文档类）: {r['skipped_total']} 个")
    print("=" * 66)
    if r["triggered"]:
        print("  🔴 红线已触发 → 应启动全库审计 + 功能冻结")
    else:
        print(f"  🟢 未触发（距红线还差 {r['remaining']} 个节点）")
    print("  ⚠️ 另：架构级变更（新 ADR / major）立即触发审计，不受本计数限制")
    print("=" * 66)
    return 1 if r["triggered"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
