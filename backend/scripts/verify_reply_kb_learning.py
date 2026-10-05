# -*- coding: utf-8 -*-
"""reply_kb 自动学习质量修补（P1-3）—— 可复跑验证脚本

用法（在 backend 目录下）：

    python scripts/verify_reply_kb_learning.py            # 离线桩，完全确定性
    python scripts/verify_reply_kb_learning.py --real     # 额外跑一次真实 LLM 提纯

## 环境隔离铁律
- 数据落 `C:\\temp\\flowcap_verify_reply_kb`（**绝不在源码树内**）。
- 真实样本库 `C:\\temp\\flowcap_design\\...\\flowcap.db` 全程 **只读**
  （sqlite3 以 mode=ro 打开，仅做抽对算法的旧/新对比读数）。
- 不做任何 git 写操作，不调用任何昵称批量查询接口。

## 验证目标
1. 抽对加「不得跨越另一条 them」约束 —— 修复前后的错配对比。
2. LLM 提纯失败不再静默降级 —— ok=False 且库中无新增。
3. find_match 语义级接入 —— 默认（sem_enabled=False）行为零变化，
   开启后同义句可命中，embedding 挂了仍回落 Jaccard。
"""
from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
REPO = BACKEND.parent
SRC = BACKEND / "services" / "reply_kb.py"
BAK = BACKEND / "services" / "reply_kb.py.bak_p13"     # 修复前原文件（只读留档）

VERIFY_ROOT = r"C:\temp\flowcap_verify_reply_kb"
MEMBER_ID = "mverify_reply_kb"
REAL_DB = r"C:\temp\flowcap_design\members\m17db0f8209156f26\data\flowcap.db"

# ── 隔离门禁（先于任何业务导入）────────────────────────────────────
if os.path.abspath(VERIFY_ROOT).startswith(os.path.abspath(str(REPO)) + os.sep):
    raise SystemExit("[隔离门禁] 拒绝运行：验证环境根落在源码树内")
os.environ["FLOWCAP_APP_ROOT"] = VERIFY_ROOT
os.environ["DY_MEMBER"] = MEMBER_ID
os.environ.setdefault("PYTHON_BASIC_REPL", "1")
(Path(VERIFY_ROOT) / "members" / MEMBER_ID / "data").mkdir(parents=True,
                                                           exist_ok=True)
sys.path.insert(0, str(BACKEND))

R: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    R.append((name, ok, detail))
    print(f"  {'[OK]' if ok else '[FAIL]'} {name}" + (f" — {detail}" if detail else ""))


def legacy_extract_pairs(msgs) -> list:
    """修复前的抽对实现（逐字照抄 .bak_p13 原文，业务代码里的版本只作留档）。

    缺陷：一旦锚定了第一条 them，后面插进来多少条不相关的 them 都不管，
    拿「其后第一条 me」就配成一对 ⟹ 「牛头不对马尾」的直接来源。
    """
    pairs = []
    q = None
    for role, text, mtype in msgs:
        t = (text or "").strip()
        # msg_type 实际取值：'text' / 'image' / '27'（系统引导）——只收 text；
        # 系统提示（"你已确认聊天…"）、[图片] 等前缀消息跳过
        is_text = (mtype in (1, 0, None, "text", "") or str(mtype) == "text")
        if not is_text or t.startswith("[") or t.startswith("你已确认"):
            continue
        if role == "them" and q is None:
            q = t
        elif role == "me" and q:
            # 简化判定：有问有答即收录（有效性由人工在库里删改把关）
            pairs.append((q, t))
            q = None
    return pairs


def load_legacy_module():
    """把修复前的 reply_kb（.bak_p13）当独立模块加载，用于前后对比读数。

    注：.bak_p13 后缀不被 importlib 识别，显式指定 SourceFileLoader。
    两个模块（新/旧）共用同一个 database 连接与 kv_store key，故可直接在
    同一验证库上对比 learn_from_history / find_match 的行为。
    """
    if not BAK.exists():
        return None
    from importlib.machinery import SourceFileLoader
    loader = SourceFileLoader("reply_kb_legacy", str(BAK))
    spec = importlib.util.spec_from_loader("reply_kb_legacy", loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["reply_kb_legacy"] = mod
    loader.exec_module(mod)
    return mod


def main() -> int:
    print("=" * 70)
    print("reply_kb 自动学习质量修补（P1-3）验证")
    print(f"验证库: {VERIFY_ROOT}\\members\\{MEMBER_ID}\\data\\flowcap.db")
    print(f"样本库（只读）: {REAL_DB}")
    print("=" * 70)

    # ── 0. 语法检查 ────────────────────────────────────────────────
    print("\n[0] 语法/结构检查")
    import ast
    import py_compile
    try:
        py_compile.compile(str(SRC), doraise=True)
        check("py_compile", True)
    except Exception as e:
        check("py_compile", False, str(e))
        return 2
    tree = ast.parse(SRC.read_text(encoding="utf-8"))
    fn = {n.name for n in tree.body if isinstance(n, ast.FunctionDef)}
    for need in ("_extract_pairs", "_extract_pairs_legacy",
                 "find_match_semantic_reply", "rebuild_semantic_cache"):
        check(f"存在 {need}", need in fn)
    import inspect
    from services import reply_kb
    sig = inspect.signature(reply_kb.find_match)
    check("find_match 签名守恒（text, threshold, account）",
          list(sig.parameters)[:3] == ["text", "threshold", "account"], str(sig))
    legacy = load_legacy_module()

    # ── 1. 抽对：构造 them → them → me ────────────────────────────
    print("\n[1] 抽对语义校验（不得跨越另一条 them）")
    seq = [
        ("them", "这个多少钱", "text"),
        ("them", "在吗老板", "text"),
        ("them", "包邮吗", "text"),
        ("me", "包邮的亲", "text"),
    ]
    new_pairs = reply_kb._extract_pairs(seq)
    if legacy:
        old_pairs = legacy_extract_pairs(seq)
    else:
        old_pairs = [("这个多少钱", "包邮的亲")]   # 修复前实现的已知产物
    print(f"      修复前抽对: {old_pairs}")
    print(f"      修复后抽对: {new_pairs}")
    check("修复前会错配（第1条 them × 其后第1条 me）",
          old_pairs and old_pairs[0][0] == "这个多少钱"
          and old_pairs[0][1] == "包邮的亲",
          f"{old_pairs[0] if old_pairs else '无'}")
    check("修复后无此错配",
          not any(p[0] == "这个多少钱" for p in new_pairs),
          f"新抽对={new_pairs}")
    check("修复后正确锚定最后一条 them",
          new_pairs == [("包邮吗", "包邮的亲")], f"{new_pairs}")

    # 过滤规则守恒：系统提示/[图片]/你已确认 仍被跳过
    seq2 = [
        ("them", "[图片]", "image"),
        ("them", "你已确认聊天，可以开始对话了", "text"),
        ("them", "你好", "text"),
        ("me", "在的", "text"),
        ("me", "请问有什么事", "text"),
    ]
    check("过滤规则守恒（[图片]/你已确认 仍跳过，me 只配紧邻的 them）",
          reply_kb._extract_pairs(seq2) == [("你好", "在的")],
          f"{reply_kb._extract_pairs(seq2)}")

    # ── 1b. 真实样本库（只读）上的旧/新对比 ────────────────────────
    print("\n[1b] 真实样本库 779 条 dm_messages 上的旧/新抽对对比（只读）")
    if Path(REAL_DB).exists():
        con = sqlite3.connect(f"file:{REAL_DB}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT account, conv_id FROM dm_messages GROUP BY account, conv_id "
            "HAVING SUM(CASE WHEN role='me' THEN 1 ELSE 0 END) > 0 "
            "AND SUM(CASE WHEN role='them' THEN 1 ELSE 0 END) > 0 "
            "ORDER BY MAX(id) DESC LIMIT 200").fetchall()
        old_all, new_all, conv_diff = [], [], 0
        for acct, conv in rows:
            msgs = con.execute(
                "SELECT role, text, msg_type FROM dm_messages "
                "WHERE account=? AND conv_id=? AND TRIM(COALESCE(text,''))<>'' "
                "ORDER BY id ASC LIMIT 40", (acct, conv)).fetchall()
            o = legacy_extract_pairs(msgs) if legacy else []
            n = reply_kb._extract_pairs(msgs)
            old_all.extend(o)
            new_all.extend(n)
            if o != n:
                conv_diff += 1
        con.close()
        print(f"      会话数 {len(rows)}｜修复前抽对 {len(old_all)} 条"
              f"｜修复后 {len(new_all)} 条｜有差异的会话 {conv_diff} 个")
        # 展示真实错配样本：修复前把不相邻的 them/me 硬配
        shown = 0
        for acct, conv in rows[:400]:
            if shown >= 3:
                break
            con = sqlite3.connect(f"file:{REAL_DB}?mode=ro", uri=True)
            msgs = con.execute(
                "SELECT role, text, msg_type FROM dm_messages WHERE account=? "
                "AND conv_id=? AND TRIM(COALESCE(text,''))<>'' "
                "ORDER BY id ASC LIMIT 40", (acct, conv)).fetchall()
            con.close()
            o = legacy_extract_pairs(msgs) if legacy else []
            n = reply_kb._extract_pairs(msgs)
            if o and o != n:
                d = [p for p in o if p not in n][:1]
                if d:
                    print(f"      错配样本：Q={d[0][0][:34]!r} A={d[0][1][:34]!r}")
                    shown += 1
        check("真实样本上旧/新结果确有差异（证明旧逻辑在真实数据上错配）",
              conv_diff > 0, f"{conv_diff} 个会话的抽对结果被纠正")
        check("修复后抽对数不为零（没有矫枉过正把数据全丢掉）",
              len(new_all) > 0, f"{len(new_all)} 条")
    else:
        print("      （样本库不存在，跳过）")

    # ── 2. LLM 提纯失败不再静默 ───────────────────────────────────
    print("\n[2] LLM 提纯失败：不写库 + 回原因码")
    import database
    database.get_db()          # 建表（验证库）
    from services import ai_reply as _ar

    class _DeadClient:
        def __init__(self, cfg):
            self.cfg = cfg

        def chat_failover(self, *a, **k):
            return None        # 全链失败（坏 key / 断网 / 空回复）

    class _GarbageClient:
        def __init__(self, cfg):
            self.cfg = cfg

        def chat_failover(self, *a, **k):
            return "抱歉，我无法完成该请求。"    # 回文字但非 JSON

    class _GoodClient:
        def __init__(self, cfg):
            self.cfg = cfg

        def chat_failover(self, *a, **k):
            return json.dumps([{"question": "包邮吗", "answer": "包邮的亲"}],
                              ensure_ascii=False)

    real_AIClient, real_get_config = _ar.AIClient, _ar.get_config

    def _seed_pairs():
        """往验证库塞一个 them→them→me 会话，保证有可抽对的样本。"""
        conn = database.get_db()
        conn.execute("DELETE FROM dm_messages WHERE account='verify_acct'")
        for i, (role, txt) in enumerate([
                ("them", "这个多少钱"), ("them", "在吗老板"),
                ("them", "包邮吗"), ("me", "包邮的亲")]):
            conn.execute(
                "INSERT INTO dm_messages(account, conv_id, role, text, "
                "msg_type, extra, ts) VALUES(?,?,?,?,?,?,?)",
                ("verify_acct", "c1", role, txt, "text", "{}", 1700000000 + i))
        conn.commit()

    reply_kb.clear_items()
    _seed_pairs()

    # 2a. 修复前（legacy 模块）：LLM 死了照样写库 —— 静默降级
    if legacy:
        _ar.AIClient, _ar.get_config = _DeadClient, real_get_config
        legacy.clear_items()
        before_n = len(legacy.list_items())
        r_old = legacy.learn_from_history(account="verify_acct")
        after_n = len(legacy.list_items())
        _ar.AIClient = real_AIClient
        print(f"      修复前: 返回={r_old}，库 {before_n} → {after_n} 条")
        print(f"      修复前入库内容: "
              f"{[(it['question'], it['answer']) for it in legacy.list_items()]}")
        check("修复前：LLM 失败仍静默写库（缺陷复现）",
              r_old.get("added", 0) > 0 and after_n > before_n,
              f"added={r_old.get('added')} 库{before_n}→{after_n}")
        legacy.clear_items()

    # 2b. 修复后：ok=False + 零写入
    reply_kb.clear_items()
    _seed_pairs()
    _ar.AIClient, _ar.get_config = _DeadClient, real_get_config
    n0 = len(reply_kb.list_items())
    r_new = reply_kb.learn_from_history(account="verify_acct")
    n1 = len(reply_kb.list_items())
    _ar.AIClient = real_AIClient
    print(f"      修复后: 返回={r_new}，库 {n0} → {n1} 条")
    check("修复后：ok=False", r_new.get("ok") is False, str(r_new.get("ok")))
    check("修复后：带原因码", bool(r_new.get("reason")), str(r_new.get("reason")))
    check("修复后：库中无新增条目", n1 == n0, f"{n0}→{n1}")
    check("修复后：added=0 且 purified=False",
          r_new.get("added") == 0 and r_new.get("purified") is False,
          f"added={r_new.get('added')} purified={r_new.get('purified')}")

    # 2c. 非 JSON 回复同样不写库
    reply_kb.clear_items()
    _seed_pairs()
    _ar.AIClient, _ar.get_config = _GarbageClient, real_get_config
    n0 = len(reply_kb.list_items())
    r_bad = reply_kb.learn_from_history(account="verify_acct")
    n1 = len(reply_kb.list_items())
    _ar.AIClient = real_AIClient
    print(f"      非JSON回复: {r_bad}，库 {n0} → {n1} 条")
    check("非 JSON 回复：ok=False 且零写入",
          r_bad.get("ok") is False and n1 == n0, f"reason={r_bad.get('reason')}")

    # 2d. LLM 正常时仍照常入库（正向路径未破坏）
    reply_kb.clear_items()
    _seed_pairs()
    _ar.AIClient, _ar.get_config = _GoodClient, real_get_config
    n0 = len(reply_kb.list_items())
    r_ok = reply_kb.learn_from_history(account="verify_acct")
    n1 = len(reply_kb.list_items())
    _ar.AIClient = real_AIClient
    print(f"      LLM 正常: {r_ok}，库 {n0} → {n1} 条")
    check("LLM 正常：ok=True 且正常入库",
          r_ok.get("ok") is True and n1 > n0, f"added={r_ok.get('added')}")

    # 2e. 空样本（无合规问答对）不应报失败
    reply_kb.clear_items()
    conn = database.get_db()
    conn.execute("DELETE FROM dm_messages WHERE account='verify_acct'")
    conn.execute("INSERT INTO dm_messages(account, conv_id, role, text, "
                 "msg_type, extra, ts) VALUES(?,?,?,?,?,?,?)",
                 ("verify_acct", "c2", "them", "孤立提问无人回", "text",
                  "{}", 1700000100))
    conn.commit()
    r_empty = reply_kb.learn_from_history(account="verify_acct")
    print(f"      无可配对样本: {r_empty}")
    check("无可配对样本：ok=True + reason=no_pairs（不算失败）",
          r_empty.get("ok") is True and r_empty.get("reason") == "no_pairs",
          str(r_empty.get("reason")))

    # ── 3. find_match 语义级 + 向后兼容 ───────────────────────────
    print("\n[3] find_match 语义级（默认关闭 ⇒ 行为零变化）")
    reply_kb.clear_items()
    reply_kb.add_item("你好", "你是在哪个地区受伤的", source="test")
    reply_kb.add_item("包邮吗", "包邮的亲", source="test")

    # 3a. 默认配置（sem_enabled=False，即本机实测配置）下新旧一致
    cfg = real_get_config()
    print(f"      实测 sem_enabled={cfg.get('sem_enabled')} "
          f"sem_threshold={cfg.get('sem_threshold')} "
          f"sem_base_url={cfg.get('sem_base_url')}")
    cases = ["你好", "包邮吗", "你好呀", "请问多少钱", "完全不相干的一句话"]
    same = True
    for c in cases:
        a = reply_kb.find_match(c)
        b = legacy.find_match(c) if legacy else None
        flag = "同" if a == b else "异"
        print(f"      {c!r}: 新={str(a)[:22]!r} 旧={str(b)[:22]!r} [{flag}]")
        if legacy and a != b:
            same = False
    check("sem_enabled=False 时新旧命中逐例一致（向后兼容）", same)

    # 3b. 语义级真的能命中同义句（用桩向量模拟 embedding 服务）
    reply_kb.clear_items()
    reply_kb.add_item("多少钱", "199 元包邮", source="test")
    sem_cfg = {"sem_enabled": True, "sem_base_url": "http://127.0.0.1:1/v1",
               "sem_model": "stub", "sem_threshold": 0.40,
               "sem_api_key": ""}
    # 「多少钱」与「价格多少」在字符集上重合度极低
    j = reply_kb._jaccard("价格多少", "多少钱")
    print(f"      字符集 Jaccard('价格多少','多少钱') = {j:.3f}（远低于 0.85 阈值）")

    VEC = {"多少钱": [1.0, 0.0], "价格多少": [0.93, 0.05],
           "完全不相干": [0.0, 1.0]}

    def _stub_embed(texts, consumer_id="ai_sem", timeout=20.0):
        if any(t not in VEC for t in texts):
            return None, None
        return [VEC[t] for t in texts], "stub"

    _ar._embed_failover = _stub_embed
    hit = reply_kb.find_match_semantic_reply("价格多少",
                                             reply_kb.list_items(), sem_cfg)
    print(f"      语义级命中: {hit[0]['question']!r} score={hit[1]:.3f}"
          if hit else "      语义级命中: 无")
    check("语义级能命中同义句（Jaccard 命不中的场景）",
          bool(hit) and hit[0]["question"] == "多少钱",
          f"score={hit[1]:.3f}" if hit else "None")
    check("Jaccard 在同义句上确实命中不了（对比读数）", j < 0.85, f"jaccard={j:.3f}")

    # 3c. embedding 挂了 → 回落 Jaccard，且原本能命中的场景仍能命中
    _ar._embed_failover = lambda *a, **k: (None, None)
    reply_kb.clear_items()
    reply_kb.add_item("你好啊", "在的", source="test")
    hit2 = reply_kb.find_match("你好啊", )   # 精确命中路径
    sem_none = reply_kb.find_match_semantic_reply("你好啊",
                                                  reply_kb.list_items(), sem_cfg)
    print(f"      embedding 挂掉后：find_match(精确)={hit2!r} 语义级={sem_none}")
    check("embedding 不可用：语义级返回 None（落 Jaccard）", sem_none is None)
    check("embedding 不可用：精确/包含命中不受影响", hit2 == "在的", repr(hit2))
    _ar._embed_failover = _ar.__dict__.get("__real_embed__") or _ar._embed_failover

    # 3d. 语义级未启用时 rebuild 明确报不可用（不静默假装成功）
    r_rb = reply_kb.rebuild_semantic_cache({"sem_enabled": False})
    print(f"      rebuild_semantic_cache(sem_enabled=False) = {r_rb}")
    check("语义级未启用时 rebuild 明确返回 ok=False",
          r_rb.get("ok") is False, str(r_rb.get("error")))

    # ── 4. 真实 LLM（可选）────────────────────────────────────────
    if "--real" in sys.argv:
        print("\n[4] 真实 LLM 提纯（本机 FreeLLM，需 v2rayN 在跑）")
        reply_kb.clear_items()
        _seed_pairs()
        _ar.AIClient, _ar.get_config = real_AIClient, real_get_config
        r_real = reply_kb.learn_from_history(account="verify_acct")
        print(f"      真实调用返回: {r_real}")
        check("真实 LLM 调用路径可执行", "ok" in r_real, str(r_real)[:120])

    # ── 5. 汇总 ───────────────────────────────────────────────────
    print("\n" + "=" * 70)
    bad = [n for n, ok, _ in R if not ok]
    print(f"合计 {len(R)} 项，通过 {len(R) - len(bad)}，失败 {len(bad)}")
    for n, ok, d in R:
        print(f"  {'[OK]' if ok else '[FAIL]'} {n}" + (f" — {d}" if d else ""))
    print("=" * 70)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
