# -*- coding: utf-8 -*-
"""留资场景 · 真实网关照会（2026-09-29）

目的：用**真实网关**验证「客户无明确问题（只回『好/没下来』）」时，
新 prompt 是否真的把回复推向留资，而不是"等消息"。

只读 + 临时隔离根；不发送任何真实私信。

运行：DY_APP_ROOT=<临时目录> python scripts/verify_lead_disposition_real.py
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
BACKEND = HERE.parent / "backend"
sys.path.insert(0, str(BACKEND))
SNAP = Path(sys.argv[1] if len(sys.argv) > 1 else "").resolve()

CASES = [
    # (客户消息, 期望：True=应含索要, False=应含提问/推进)
    ("好", True),
    ("没下来", True),
    ("单位给报了工伤，我下午去人社局问问", True),
    ("我这个情况能评几级", False),      # 有明确问题 → 应先专业回答
]


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="lead_real_"))
    (root / "data").mkdir(parents=True, exist_ok=True)
    os.environ["DY_APP_ROOT"] = str(root)

    from services import ai_reply as A

    cfg = dict(A._DEFAULT_CONFIG)
    if SNAP.exists():
        # 🔴 必须把**全部相关 kv** 搬过来（尤其 `model_hub`）：只搬 ai_reply_config
        # 会让 model_hub 走 v1→v2 兜底迁移，凭空造出一个协议/密钥组合与生产不同的
        # provider（实测导致 401 Invalid API key ⇒ 假失败）。
        c = sqlite3.connect(SNAP / "dyautodm.db")
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT key, value FROM kv_store WHERE key='model_hub' "
            "OR key LIKE 'ai_reply%' OR key LIKE 'ai_pro_kb%'").fetchall()
        import database as _db
        for r in rows:
            _db.set_kv_json(r["key"], json.loads(r["value"]))
        r = c.execute("SELECT value FROM kv_store WHERE key='ai_reply_config'").fetchone()
        if r:
            cfg.update(json.loads(r["value"]) or {})
    cfg.update({"enabled": True, "live_reply_kb_enabled": False})
    if not str(cfg.get("base_url", "")).startswith("http://127.0.0.1:31415"):
        print("网关非本机，跳过（结论未知，非缺陷）")
        return 0

    prompt = A.build_system_prompt(cfg, "（客户消息）")
    print(f"system prompt 含留资铁律: {'留资铁律' in prompt}  长度={len(prompt)}")
    fails = []
    for i, (text, expect_ask) in enumerate(CASES):
        # 🔴 必须走 **_generate_reply**（真实链路，含留资护栏 + 定向重试）：
        # 直接调 AIClient.chat_failover 会绕过护栏 ⇒ 看到的是"未治理"的原始输出
        # （本脚本初版正是这么错的，实机当场暴露）。
        # 每个用例用**独立 conv_id**，避免 max_lead_ask 计数跨用例累积。
        worker = A.AutoReplyWorker.__new__(A.AutoReplyWorker)
        worker.status = dict(getattr(A.AutoReplyWorker, "status", {}) or {})
        reply, source = A.AutoReplyWorker._generate_reply(
            worker, cfg, text, "验证账号", f"probe_{i}", 1000 + i)
        # 🔴 **不要**再对返回结果调 validate_reply：真实发送链在 _generate_reply
        # 之后**不再校验**；二次校验会用 max_reply_len=60 把护栏刚追加的索要句
        # 截掉 ⇒ 造出假失败（本脚本上一版正是这么错的，实机当场暴露）。
        has_ask = bool(reply) and A._has_lead_ask(reply)
        stalled = A._is_lead_stalled(reply) if reply else True
        ok = (has_ask if expect_ask else not stalled)
        print(f"\n  客户：{text!r}\n  回复：{reply!r}  [source={source}]\n"
              f"  → 含索要={has_ask} 判沉降={stalled} 期望{'含索要' if expect_ask else '有推进'} "
              f"{'PASS' if ok else 'FAIL'}")
        if not ok:
            fails.append(text)
    print("\n" + ("全部 PASS" if not fails else f"FAIL={len(fails)}: {fails}"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
