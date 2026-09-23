# -*- coding: utf-8 -*-
"""验收 P1-1：AI 会话回复「上下文注入失效」修复（RED → GREEN）。

缺陷根因（2026-09-23 实机取证）
------------------------------
backend/services/ai_reply.py 的 AutoReplyWorker._build_history 原 SQL 硬筛
`msg_type='text'`。而抖音 WS 实时路径落库的消息 msg_type 是**数字字符串 '7'**
（映射语义见 backend/api/messages.py 的 _front_type，约 156-166 行，只读参考）。
两者永不相等 ⇒ 纯 WS 会话的 history 恒为空列表 ⇒ AI 每次只见当前一句
⇒ 表现「只看最后一条消息、完全不像读过聊天记录」。

本脚本做的事
------------
两个会话，同一张临时 dm_messages 表：
  · CONV_WS    —— 会话内**全部**消息都是 WS 实时路径落库的 msg_type='7'
                  （真实缺陷场景）。修复前必须返回 0 条，修复后 >0 条。
  · CONV_MIXED —— 混合 msg_type：'7' + 'text' + 应排除的 '27'（图片）/
                  '1' / '50010' / '5'（表情）/ '8'（视频）/ 空文本。
                  用于断言白名单语义（只收 text|7，其余一律排除）。

「修复前实现」从 git HEAD 版本（`git show HEAD:./backend/services/ai_reply.py`，
纯只读）抽出旧 _build_history 源码并执行；「修复后实现」取当前工作区
services.ai_reply。两者跑在同一份数据上，脚本自打印修复前后读数。

其它断言：role 映射（them→user / me→assistant）、id 升序、
max_history 生效（不再写死 HISTORY_LIMIT=6）、白名单常量取值。

任何 SKIP 都计入失败（空转 ≠ 通过）。

运行：
  python backend/scripts/verify_history_context_fix.py
"""
from __future__ import annotations

import os
import re
import sqlite3
import subprocess
import sys
import textwrap

SOURCE_REPO = r"C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2"
DESIGN_ROOT = r"C:\temp\dyautodm_design"
BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

if os.path.abspath(DESIGN_ROOT).startswith(os.path.abspath(SOURCE_REPO) + os.sep):
    raise SystemExit("拒绝运行：临时数据根落在源码树内")

os.environ["DY_APP_ROOT"] = DESIGN_ROOT          # 隔离测试环境
sys.path.insert(0, BACKEND)
os.chdir(BACKEND)

PASS, FAIL, SKIP = [], [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}{('  — ' + detail) if detail else ''}")
    return bool(cond)


def skip(name, detail=""):
    SKIP.append(name)
    print(f"  [SKIP] {name}{('  — ' + detail) if detail else ''}")
    return False


ACCOUNT = "acc_test"

# 会话 A：纯 WS 实时路径（msg_type 全是 '7'）—— 真实缺陷场景
CONV_WS = "0:1:10001:20002"
ROWS_WS = [
    ("them", "你好，这个多少钱", "7"),
    ("me", "亲，这款 199 哦", "7"),
    ("them", "能便宜点吗", "7"),
    ("me", "已经是活动价啦", "7"),
    ("them", "那我要一个", "7"),
    # ↓ id 最大 = 当前触发消息，history 必须排除它
    ("them", "包邮吗", "7"),
]
EXPECTED_WS = ["你好，这个多少钱", "亲，这款 199 哦", "能便宜点吗",
               "已经是活动价啦", "那我要一个"]

# 会话 B：混合 msg_type —— 断言白名单语义
CONV_MIXED = "0:1:10001:30003"
ROWS_MIXED = [
    ("them", "补拉的历史文本", "text"),     # 收（白名单）
    ("them", "WS 实时文本", "7"),           # 收（白名单）
    ("them", "[图片]", "27"),               # 排除（image）
    ("them", "", "7"),                      # 排除（空文本）
    ("them", "", "text"),                   # 排除（空文本）
    ("them", "语义未确认A", "1"),           # 排除（语义未确认）
    ("them", "语义未确认B", "50010"),       # 排除（语义未确认）
    ("them", "[表情]", "5"),                # 排除（sticker）
    ("them", "[视频]", "8"),                # 排除（video）
    ("me", "客服回复文本", "7"),            # 收
    # ↓ 当前消息
    ("them", "在吗", "7"),
]
EXPECTED_MIXED = ["补拉的历史文本", "WS 实时文本", "客服回复文本"]


def build_db(path):
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE IF NOT EXISTS dm_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account TEXT, conv_id TEXT, role TEXT, text TEXT,
        msg_type TEXT, ts REAL, msg_id TEXT, extra TEXT)""")
    last = {}
    for conv, rows in ((CONV_WS, ROWS_WS), (CONV_MIXED, ROWS_MIXED)):
        for i, (role, text, mt) in enumerate(rows):
            conn.execute(
                "INSERT INTO dm_messages(account,conv_id,role,text,msg_type,ts)"
                " VALUES(?,?,?,?,?,?)", (ACCOUNT, conv, role, text, mt, 1000 + i))
        last[conv] = conn.execute(
            "SELECT MAX(id) FROM dm_messages WHERE conv_id=?", (conv,)).fetchone()[0]
    conn.commit()
    conn.close()
    return last


def _old_source() -> str:
    """从 git HEAD 取旧实现源码（只读，无 git 写操作）。"""
    try:
        out = subprocess.run(
            ["git", "show", "HEAD:./backend/services/ai_reply.py"],
            cwd=SOURCE_REPO, capture_output=True, timeout=30)
        if out.returncode == 0 and out.stdout:
            return out.stdout.decode("utf-8")
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] git show HEAD 取旧版失败: {e}")
    return ""


def extract_old_method(src: str):
    """从旧源码抠出 `def _build_history` 整段（缩进敏感）并 dedent。"""
    lines = src.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if re.match(r"\s*def _build_history\(", ln):
            start = i
            break
    if start is None:
        return None
    base = len(lines[start]) - len(lines[start].lstrip())
    end = len(lines)
    for j in range(start + 1, len(lines)):
        ln = lines[j]
        if not ln.strip():
            continue
        if len(ln) - len(ln.lstrip()) <= base and not ln.lstrip().startswith("#"):
            end = j
            break
    return textwrap.dedent("\n".join(lines[start:end]))


def main():
    print("=" * 72)
    print("P1-1 验收：AI 会话回复上下文注入（_build_history）")
    print("=" * 72)

    tmpdir = os.path.join(DESIGN_ROOT, "_tmp_verify_history")
    os.makedirs(tmpdir, exist_ok=True)
    db = os.path.join(tmpdir, "verify_history.db")
    if os.path.exists(db):
        os.remove(db)
    last = build_db(db)
    print(f"\n[setup] 临时库 {db}")
    print(f"[setup] 会话A(纯WS,'7') {len(ROWS_WS)} 条，当前消息 id={last[CONV_WS]}")
    print(f"[setup] 会话B(混合) {len(ROWS_MIXED)} 条，当前消息 id={last[CONV_MIXED]}"
          "（7=4,text=3,27/1/50010/5/8 各1，空文本2）")

    import database  # noqa: E402
    real_get_db = database.get_db
    _conn = sqlite3.connect(db, check_same_thread=False)
    _conn.row_factory = sqlite3.Row
    database.get_db = lambda: _conn

    # ---- A. 修复前（git HEAD 旧实现） -------------------------------------
    print("\n--- A. 修复前（git HEAD 旧实现：硬筛 msg_type='text'）---")
    before_count = -1
    before_mixed = -1
    old_body = extract_old_method(_old_source())
    old_fn = None
    if not old_body:
        skip("A0 取到旧实现", "git HEAD 不可用")
    else:
        import logging  # noqa: E402
        ns = {"database": database, "logger": logging.getLogger("old_ai_reply")}
        try:
            exec(compile(old_body, "<old_ai_reply>", "exec"), ns)  # noqa: S102
            old_fn = ns.get("_build_history")
        except SyntaxError as e:
            skip("A0 旧实现可执行", f"抽取体语法错误 {e}")
    if old_fn:
        class _Old:
            HISTORY_LIMIT = 6

        hb = old_fn(_Old(), ACCOUNT, CONV_WS, last[CONV_WS])
        before_count = len(hb)
        print(f"  会话A(纯 WS '7') 修复前返回 {before_count} 条 → {hb}")
        check("A1 修复前纯WS会话返回 0 条（复现缺陷 RED）", before_count == 0,
              f"实测 {before_count} 条")

        hbm = old_fn(_Old(), ACCOUNT, CONV_MIXED, last[CONV_MIXED])
        before_mixed = len(hbm)
        print(f"  会话B(混合) 修复前返回 {before_mixed} 条 → "
              f"{[h['content'] for h in hbm]}（只捞到 'text'，'7' 全丢）")

    # ---- B. 修复后（当前工作区实现） --------------------------------------
    print("\n--- B. 修复后（当前工作区：白名单 text|7）---")
    after_count = -1
    from services import ai_reply  # noqa: E402

    w = ai_reply.AutoReplyWorker.__new__(ai_reply.AutoReplyWorker)
    try:
        ha = w._build_history(ACCOUNT, CONV_WS, last[CONV_WS], {"max_history": 10})
        after_count = len(ha)
        print(f"  会话A(纯 WS '7') 修复后返回 {after_count} 条：")
        for h in ha:
            print(f"      {h['role']:9s} | {h['content']}")
        check("B1 修复后纯WS会话返回 >0 条（GREEN）", after_count > 0,
              f"实测 {after_count} 条")
        check("B2 内容按 id 升序、排除当前消息",
              [h["content"] for h in ha] == EXPECTED_WS,
              f"{[h['content'] for h in ha]}")
        check("B3 role 映射 them→user / me→assistant",
              [h["role"] for h in ha] == ["user", "assistant", "user",
                                          "assistant", "user"],
              f"{[h['role'] for h in ha]}")
        check("B4 返回结构 list[{role,content}] 不变",
              isinstance(ha, list) and all(set(h) == {"role", "content"}
                                           for h in ha))

        hm = w._build_history(ACCOUNT, CONV_MIXED, last[CONV_MIXED],
                              {"max_history": 10})
        got_mixed = [h["content"] for h in hm]
        print(f"  会话B(混合) 修复后返回 {len(hm)} 条 → {got_mixed}")
        check("B5 白名单只收 text|7，排除 27/1/50010/5/8 与空文本",
              got_mixed == EXPECTED_MIXED, f"{got_mixed}")

        h2 = w._build_history(ACCOUNT, CONV_WS, last[CONV_WS], {"max_history": 2})
        check("B6 max_history=2 生效（不再写死 HISTORY_LIMIT=6）",
              len(h2) == 2 and h2[-1]["content"] == "那我要一个",
              f"{len(h2)} 条 {[h['content'] for h in h2]}")

        wl = tuple(getattr(ai_reply, "_HISTORY_TEXT_TYPES", ()) or ())
        check("B7 _HISTORY_TEXT_TYPES == ('text','7')", wl == ("text", "7"), f"{wl}")
    except Exception as e:  # noqa: BLE001
        check("B1 修复后纯WS会话返回 >0 条（GREEN）", False, f"异常 {e!r}")
    finally:
        database.get_db = real_get_db
        try:
            _conn.close()
        except Exception:  # noqa: BLE001
            pass

    # ---- 汇总 -------------------------------------------------------------
    print("\n" + "=" * 72)
    print(f"修复前读数 before_count (纯WS会话) = {before_count}")
    print(f"修复后读数 after_count  (纯WS会话) = {after_count}")
    print(f"  （参考）会话B 混合：修复前 {before_mixed} 条 → 修复后 "
          f"{len(EXPECTED_MIXED)} 条")
    print(f"PASS={len(PASS)}  FAIL={len(FAIL)}  SKIP={len(SKIP)}")
    print("=" * 72)
    if FAIL or SKIP:
        print(f"RESULT: FAIL — 失败项 {FAIL}，跳过项 {SKIP}")
        return 1
    if before_count != 0 or after_count <= 0:
        print("RESULT: FAIL — RED→GREEN 证据不成立")
        return 1
    print(f"RESULT: PASS — RED({before_count}) → GREEN({after_count}) 成立")
    return 0


if __name__ == "__main__":
    sys.exit(main())
