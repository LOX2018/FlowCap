# -*- coding: utf-8 -*-
"""留资场景 · 真实网关照会（2026-09-29）

目的：用**真实网关**验证「客户无明确问题（只回『好/没下来』）」时，
新 prompt 是否真的把回复推向留资，而不是"等消息"。

只读 + 临时隔离根；不发送任何真实私信。

运行：FLOWCAP_APP_ROOT=<临时目录> py -3.14 scripts/verify_lead_disposition_real.py <库快照目录>

──────────────────────────────────────────────────────────────────────────
I-1 修复（2026-09-29 · OCR[31] · HIGH · 假通过）
  原判据 `ok = (has_ask if expect_ask else not stalled)`，而
  `stalled == not has_ask` ⇒ `not stalled == has_ask` ⇒ **两分支都坍缩为
  `ok = has_ask`**。于是第 4 例「我这个情况能评几级（有明确问题→应先专业回答）」
  从未被真正断言（它只是又验了一遍"含索要"）= 假通过。
  现改为**期望类型**（ASK / ADVANCE）：ADVANCE 用与本脚本内联、与「是否含索要」
  **正交**的 `_has_progress`（专业判断 / 向客户提问 / 引导下一步）判定，
  即使回复不含索要也能通过，且不得因缺索要判失败；并内置**负控自检**
  （既无专业内容也无索要 ⇒ 必须 FAIL）。

I-2 修复（2026-09-29 · OCR[30] · MED）
  无 CLI 参数时 `Path("").resolve()` = CWD（恒存在）⇒ `SNAP.exists()` 守卫失效
  ⇒ `sqlite3.connect(SNAP/"flowcap.db")` **静默新建空库** ⇒ 后续 SELECT 报
  `no such table: kv_store`。现改为像兄弟脚本 `verify_live_contact_fix.py` 一样
  **校验库文件本身存在**（并校验 kv_store 表）后再连接，缺失则明确报错退出。
──────────────────────────────────────────────────────────────────────────
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
DB_FILE = SNAP / "flowcap.db"

# ── 期望类型（I-1：取代布尔 expect_ask，杜绝两分支坍缩）──────────────────
ASK = "ask"          # 必须**含索要**（客户无明确问题 → 回复必须推进留资）
ADVANCE = "advance"  # 必须**有推进**（专业回答/提问/引导）；**不因缺索要判失败**

CASES = [
    ("好", ASK),
    ("没下来", ASK),
    ("单位给报了工伤，我下午去人社局问问", ASK),
    ("我这个情况能评几级", ADVANCE),   # 有明确问题 → 应先专业回答
]

# 与「是否索要」正交的推进信号：专业判断 / 向客户提问 / 引导下一步。
_PROGRESS_HINTS = (
    "鉴定", "认定", "等级", "工伤", "伤残", "赔偿", "工资", "社保",
    "劳动", "合同", "住院", "手术", "骨折", "诊断", "材料", "证据", "病历",
    "部位", "受伤", "恢复", "功能", "标准", "依据", "结论", "建议",
    "补充", "说下", "说说", "告知", "提供", "方便", "城市", "地区",
)
# 「把线索放走」的形态（等对方回头/推后处理）⇒ 不算推进。
_DEFER_HINTS = ("回头", "再说", "再联系", "有消息", "有结果", "问完",
                "等你", "等我", "第一时间通知", "到时候")


def _has_progress(reply: str) -> bool:
    """回复是否**有推进**（专业回答 / 提问 / 引导）—— 与「是否含索要」正交。

    这是 I-1 的关键：`ADVANCE` 用例据此判定，**完全不看** `_has_lead_ask`；
    因此「有明确问题」的用例即使回复不含索要，也能因专业/引导内容通过；
    反之「既无专业内容也无索要」的回复（负控）必须判 False。
    """
    s = (reply or "").strip()
    if not s:
        return False
    if any(h in s for h in _DEFER_HINTS):     # 放走语 ⇒ 没推进
        return False
    if "？" in s or "?" in s:                 # 向客户提问 / 引导
        return True
    if any(h in s for h in _PROGRESS_HINTS):  # 有专业内容
        return True
    return False


def _judge(A, reply: str, expect: str) -> tuple[bool, str]:
    """按**期望类型**判定（I-1：ASK 看索要，ADVANCE 看推进；二者正交）。"""
    has_ask = bool(reply) and A._has_lead_ask(reply)
    stalled = (not reply) or A._is_lead_stalled(reply)
    progress = _has_progress(reply)
    ok = has_ask if expect == ASK else progress
    return ok, f"含索要={has_ask} 判沉降={stalled} 有推进={progress}"


# I-1 负控自检：证明「期望类型」判据不是装饰（含**负控必须 FAIL**）。
# 注意 advance 那条：它**不含索要**却被判 PASS —— 正是为了证明
# ADVANCE 分支不再挟持「含索要」（否则第 4 例仍是假通过）。
_SELFTEST = [
    ("ask-hit（含索要）", "方便留个手机号，我按你当地标准算份清单发你。", ASK, True),
    ("ask-miss（无索要）", "你这个情况要看鉴定结论。", ASK, False),
    ("advance-专业无索要（不得因缺索要判失败）",
     "要看具体伤情，一般骨折影响手指功能的才评得上等级。你在哪个城市？", ADVANCE, True),
    ("advance-放走语（不算推进）", "那你先问问，有消息再说吧。", ADVANCE, False),
    ("负控：既无专业内容也无索要", "嗯嗯好的", ADVANCE, False),
]


def _selftest(A) -> list:
    print("[判据自检 · I-1 负控]")
    bad = []
    for name, reply, expect, want in _SELFTEST:
        ok, detail = _judge(A, reply, expect)
        mark = "PASS" if ok == want else "FAIL"
        if ok != want:
            bad.append(name)
        print(f"  [{mark}] {name}: 期望={expect} want_ok={want} got_ok={ok}  "
              f"{detail}  reply={reply!r}")
    return bad


def main() -> int:
    # I-2：连接前先校验**库文件本身**存在（无参数时 SNAP=CWD 恒存在，
    # 旧逻辑会静默新建空库 ⇒ no such table）。
    if not DB_FILE.exists():
        print("用法: py -3.14 scripts/verify_lead_disposition_real.py <库快照目录>")
        print(f"错误: 未找到库文件 {DB_FILE}"
              f"（无参数时 SNAP=CWD 恒存在会静默建空库 ⇒ 已改为显式校验）")
        return 2

    root = Path(tempfile.mkdtemp(prefix="lead_real_"))
    (root / "data").mkdir(parents=True, exist_ok=True)
    os.environ["FLOWCAP_APP_ROOT"] = str(root)

    from services import ai_reply as A

    # I-1 负控自检（确定性，不依赖网关）
    selftest_fails = _selftest(A)

    cfg = dict(A._DEFAULT_CONFIG)
    # I-2：只读打开 + 先确认 kv_store 表存在，避免 no such table。
    c = sqlite3.connect(f"file:{DB_FILE}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    try:
        if not c.execute("SELECT 1 FROM sqlite_master "
                         "WHERE type='table' AND name='kv_store'").fetchone():
            print(f"错误: {DB_FILE} 中不存在 kv_store 表 —— 不是有效库快照")
            return 2
        # 🔴 必须把**全部相关 kv** 搬过来（尤其 `model_hub`）：只搬 ai_reply_config
        # 会让 model_hub 走 v1→v2 兜底迁移，凭空造出一个协议/密钥组合与生产不同的
        # provider（实测导致 401 Invalid API key ⇒ 假失败）。
        rows = c.execute(
            "SELECT key, value FROM kv_store WHERE key='model_hub' "
            "OR key LIKE 'ai_reply%' OR key LIKE 'ai_pro_kb%'").fetchall()
        import database as _db
        for r in rows:
            _db.set_kv_json(r["key"], json.loads(r["value"]))
        r = c.execute("SELECT value FROM kv_store WHERE key='ai_reply_config'").fetchone()
        if r:
            cfg.update(json.loads(r["value"]) or {})
    finally:
        c.close()
    cfg.update({"enabled": True, "live_reply_kb_enabled": False})
    if not str(cfg.get("base_url", "")).startswith("http://127.0.0.1:31415"):
        print("网关非本机，跳过（结论未知，非缺陷）")
        return 1 if selftest_fails else 0

    prompt = A.build_system_prompt(cfg, "（客户消息）")
    print(f"system prompt 含留资铁律: {'留资铁律' in prompt}  长度={len(prompt)}")
    fails = list(selftest_fails)
    for i, (text, expect) in enumerate(CASES):
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
        ok, detail = _judge(A, reply, expect)
        expect_desc = "含索要" if expect == ASK else "有推进（专业/引导，不要求索要）"
        print(f"\n  客户：{text!r}\n  回复：{reply!r}  [source={source}]\n"
              f"  → {detail} 期望={expect_desc} {'PASS' if ok else 'FAIL'}")
        if not ok:
            fails.append(f"case{i}:{text}")
    print("\n" + ("全部 PASS" if not fails else f"FAIL={len(fails)}: {fails}"))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
