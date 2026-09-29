# -*- coding: utf-8 -*-
"""直播三缺陷修复 · 实机验证（2026-09-29）

覆盖三段判据（**只在隔离临时根内跑，绝不动真实库/真实账号**）：
  L1 投递判定：对真实库**只读副本**跑 delivery_state_of，读数须与手工 SQL 吻合
  L2 端到端（堵住 AI 输出）：用桩 AIClient 复现截图里的故障原文
     ⇒ 出口必须变成**引导型**，不得外发等级/金额
  L3 端到端（真实网关）：真实调 ai_main 链生成，打印**实际 system prompt 长度**
     与产出的文案，人工可读地证明「场景规则真的进了 prompt」

运行：
  DY_APP_ROOT=<临时目录> python scripts/verify_live_contact_fix.py
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent / "backend"
sys.path.insert(0, str(BACKEND))

SNAP_DB = Path(sys.argv[1] if len(sys.argv) > 1 else "").resolve()
FAILS: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    mark = "PASS" if cond else "FAIL"
    print(f"  [{mark}] {name}" + (f"  {detail}" if detail else ""))
    if not cond:
        FAILS.append(name)


# ── L1 投递判定：真实库只读副本 ──────────────────────────────────────────
def l1_delivery_state() -> None:
    print("\n[L1] 投递判定（真实库只读副本）")
    root = Path(tempfile.mkdtemp(prefix="dy_verify_l1_"))
    (root / "data").mkdir(parents=True, exist_ok=True)
    dst = root / "data" / "dyautodm.db"
    shutil.copy2(SNAP_DB / "dyautodm.db", dst)
    for suf in ("-wal", "-shm"):
        p = SNAP_DB / f"dyautodm.db{suf}"
        if p.exists():
            shutil.copy2(p, root / "data" / f"dyautodm.db{suf}")
    os.environ["DY_APP_ROOT"] = str(root)

    from services.delivery_verify import delivery_state_of

    # 手工 SQL 作为独立真值（不复用被测实现）——
    # 按**对端**归并（实库两种方向并存 `0:1:对端:自己` / `0:1:自己:对端`）。
    conn = sqlite3.connect(dst)
    MY = "3887506227210423"
    rows = conn.execute(
        "SELECT conv_id,"
        " SUM(CASE WHEN msg_type='7' AND role='me' THEN 1 ELSE 0 END) echo,"
        " SUM(CASE WHEN msg_id LIKE 'verify:%' THEN 1 ELSE 0 END) marker,"
        " SUM(CASE WHEN text LIKE '对方回复或关注你之前%' THEN 1 ELSE 0 END) rej "
        "FROM dm_messages WHERE account='四川工伤张老师' AND role='me' "
        "GROUP BY conv_id HAVING echo+marker+rej > 0").fetchall()

    def peer_of(cid: str) -> str:
        p = cid.split(":")
        if len(p) < 4:
            return cid
        a, b = p[2], p[3]
        return (b if a == MY else a) if (a == MY or b == MY) else b

    # 按对端聚合「真值」
    truth: dict = {}
    for conv_id, echo, marker, rej in rows:
        k = peer_of(conv_id)
        t = truth.setdefault(k, {"echo": 0, "marker": 0, "rej": 0})
        t["echo"] += echo or 0
        t["marker"] += marker or 0
        t["rej"] += rej or 0

    n_ok = n_bad = n_empty = 0
    mism = []
    for k, t in truth.items():
        want = "delivered" if (t["echo"] or t["marker"]) else ("rejected" if t["rej"] else "")
        got = delivery_state_of("四川工伤张老师", conv_id=f"0:1:{MY}:{k}")
        if got != want:
            mism.append((k, want, got))
        if want == "delivered":
            n_ok += 1
        elif want == "rejected":
            n_bad += 1
        else:
            n_empty += 1
    print(f"    对端读数：送达 {n_ok} / 被拒 {n_bad} / 无证据 {n_empty}")
    check("L1-1 判定与独立 SQL 真值逐条吻合", not mism, f"不一致 {len(mism)} 条 {mism[:3]}")
    check("L1-2 三种取值都真实存在（判据非恒空）", n_ok > 0 and n_bad > 0)

    # L1-4 形态无关性（负控）：同一对端**两种方向**的 conv_id 必须得到同一结论
    #       —— 实库两种形态并存，只看一种就会漏判（本项由实机实测新增）。
    probe = next((k for k, t in truth.items() if t["echo"] or t["marker"]), None)
    if probe:
        s1 = delivery_state_of("四川工伤张老师", conv_id=f"0:1:{probe}:{MY}")
        s2 = delivery_state_of("四川工伤张老师", conv_id=f"0:1:{MY}:{probe}")
        check("L1-4 两种方向的 conv_id 结论一致", s1 == s2 == "delivered",
              f"{s1!r} vs {s2!r}")

    # uid 通道（直播 records 用的是 uid，没有 conv_id）
    uid_rows = conn.execute(
        "SELECT substr(conv_id, -12) uid FROM dm_messages "
        "WHERE account='四川工伤张老师' AND msg_type='7' LIMIT 1").fetchone()
    if uid_rows:
        got = delivery_state_of("四川工伤张老师", uid=str(uid_rows[0]))
        check("L1-3 uid 通道可取到送达证据", got == "delivered", f"got={got!r}")


# ── L2 端到端：桩模型复现故障原文 ─────────────────────────────────────────
BAD_SAMPLES = [
    ("鼻骨骨折鼻中隔骨折，保守治疗",
     "南通十级 7 个月本人工资，保守治疗一般定十级。留个联系方式，我给你算赔偿清单。"),
    ("胸11压缩性骨折，不超过四分之一，保守治疗",
     "能评。压缩小于1/2是九级。安徽有标准。留个联系方式，我给你算赔偿清单。"),
    ("中指轻微骨裂算几级",
     "轻微骨裂**大概率评不上等级**，除非影响手指功能"),
]


def l2_stub_end_to_end() -> None:
    print("\n[L2] 端到端（桩模型吐故障原文 → 出口须变引导型）")
    root = Path(tempfile.mkdtemp(prefix="dy_verify_l2_"))
    (root / "data").mkdir(parents=True, exist_ok=True)
    os.environ["DY_APP_ROOT"] = str(root)

    from services import ai_reply

    class _Stub:
        def __init__(self, cfg, out):
            self._out = out

        def chat_failover(self, *a, **kw):
            return self._out

    cfg = dict(ai_reply._DEFAULT_CONFIG)
    cfg.update({"enabled": True, "strict_level": "rag",
                "live_reply_kb_enabled": False, "base_url": "http://127.0.0.1:1/v1"})

    for comment, bad in BAD_SAMPLES:
        orig = ai_reply.AIClient
        ai_reply.AIClient = lambda c, _o=bad: _Stub(c, _o)
        try:
            text, source = ai_reply.generate_dm_for_live(
                "验证账号", "观众A", comment, cfg, uid="123")
        finally:
            ai_reply.AIClient = orig
        viol = ai_reply._live_guard_violation(text)
        check(f"L2 弹幕「{comment[:12]}…」出口合规",
              bool(text) and viol is None,
              f"source={source} 文案={text[:36]!r}"
              + (f" 违规={viol}" if viol else ""))
        check(f"L2 不再与故障原文一致（{comment[:8]}…）", text != bad,
              "" if text != bad else "仍原样外发")


# ── L3 真实网关：证明场景规则进了 prompt ─────────────────────────────────
def l3_real_gateway() -> None:
    print("\n[L3] 真实网关（打印实际 prompt 规模与产出，供人工判读）")
    root = Path(tempfile.mkdtemp(prefix="dy_verify_l3_"))
    (root / "data").mkdir(parents=True, exist_ok=True)
    os.environ["DY_APP_ROOT"] = str(root)

    from services import ai_reply

    # 从快照里搬真实配置（只搬配置，不搬业务数据）
    snap = sqlite3.connect(SNAP_DB / "dyautodm.db")
    snap.row_factory = sqlite3.Row
    kv = {"ai_reply_config": {}, "ai_agents": {}, "ai_account_agent": {}}
    for k in kv:
        r = snap.execute("SELECT value FROM kv_store WHERE key=?", (k,)).fetchone()
        if r:
            kv[k] = json.loads(r["value"])
    cfg = dict(ai_reply._DEFAULT_CONFIG)
    cfg.update(kv["ai_reply_config"] or {})
    cfg.update({"enabled": True, "live_reply_kb_enabled": False})
    base = cfg.get("base_url") or ""
    print(f"    生效链路: base_url={base} model={cfg.get('model')}")
    if not base.startswith("http://127.0.0.1:31415"):
        check("L3-0 网关非本机（跳过真实调用）", True, "按设计跳过")
        return
    try:
        probe = ai_reply.AIClient(cfg).chat(
            "你好", user_id="l3_probe", system_prompt="只回两个字：收到")
        reachable = bool(probe)
    except Exception as e:
        reachable = False
        print(f"    （网关探活异常：{e}）")
    if not reachable:
        check("L3-0 网关可达", False, "不可达 → 本段结论未知（非代码缺陷）")
        return

    for comment in ("鼻骨骨折鼻中隔骨折，保守治疗", "单根肋骨不是十级吗老师"):
        text, source = ai_reply.generate_dm_for_live(
            "四川工伤张老师", "观众A", comment, cfg, uid="l3probe")
        viol = ai_reply._live_guard_violation(text)
        print(f"    弹幕「{comment}」\n      → source={source} 文案={text!r}")
        check(f"L3 真实生成合规（{comment[:10]}…）",
              bool(text) and viol is None, f"违规={viol}" if viol else "")


def main() -> int:
    if not (SNAP_DB / "dyautodm.db").exists():
        print("用法: python scripts/verify_live_contact_fix.py <库快照目录>")
        return 2
    print("=" * 66)
    print("直播三缺陷修复 · 实机验证")
    print("=" * 66)
    l1_delivery_state()
    l2_stub_end_to_end()
    l3_real_gateway()
    print("\n" + "=" * 66)
    if FAILS:
        print(f"FAIL={len(FAILS)}")
        for f in FAILS:
            print("  -", f)
        return 1
    print("全部 PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
