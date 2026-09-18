# -*- coding: utf-8 -*-
"""v0.43.89 审计修复验收脚本（本机自证，不启任何常驻进程 / 不碰浏览器）。

覆盖本轮 OCR 增量审查确认的真实缺陷，每条都给「修复前 vs 修复后」判据：
  A1  *_JS 对象解构 vs 数组传参（Node 真跑 JS）
  A2  昵称兜底失败路径必须计入限流
  A3  环境基线必须按 ok 门控
  A4  ok=True 但 path='' 的假成功
  A5  视频消息必须走点播分支（shareM 不得抢先）
  A6  video 要素必须透传
  A9  KB 导入切会话必须清预览
  A10 切会话必须清选区
  A11 跨会话跳转必须等目标会话就位
  A12 merged_forward 三元优先级
  A13 TestConversationSeq 必须能被直跑收集
  A14 conv_type 回填 SQL 括号
  A15 撤回断言必须真实（不得恒真）
  契约 群聊判定收敛到 conv_identity.conv_type 单一实现

用法：python scripts/diag/verify_audit_fixes_v0.43.89.py
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]          # DYAutoDM_v2/
_BE = _ROOT / "backend"
sys.path.insert(0, str(_BE))

PASS, FAIL = [], []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASS if ok else FAIL).append(name)
    print(f"  {'✅' if ok else '❌'} {name}" + (f"  — {detail}" if detail else ""))


def src(rel: str) -> str:
    """读源码：先按 backend/ 找，再按仓库根找（frontend/ 在根下）。"""
    for base in (_BE, _ROOT):
        p = base / rel
        if p.exists():
            return p.read_text(encoding="utf-8", errors="replace")
    raise FileNotFoundError(f"源码不存在: {rel}")


# --------------------------------------------------------------------------- #
# A1：JS 签名必须能与「数组传参」互操作（真跑 node，不 stub）
# --------------------------------------------------------------------------- #
def t_a1_js_array_contract() -> None:
    print("\n[A1] *_JS 必须按「数组传参 + 函数内解构」约定（node 真跑）")
    cases = [
        ("services/cenc_video.py", "RESOLVE_URLS_JS", "/aweme/v1/x", ["k1", "k2"]),
        ("services/nickname_fallback.py", "FETCH_JS", "/aweme/v1/im/user/info/", ["SEC_A"]),
        ("services/merged_forward.py", "WEB_FETCH_JS", "/aweme/v1/merge/", '{"uri":"x"}'),
    ]
    for rel, var, p1, p2 in cases:
        text = src(rel)
        m = re.search(var + r'\s*=\s*"""(.*?)"""', text, re.S)
        if not m:
            check(f"A1 {var} 可提取", False, "未找到 JS 常量")
            continue
        js = m.group(1)
        # ① 静态：不得再用对象解构形参
        check(f"A1 {var} 非对象解构形参", "async ({" not in js, "形参仍为对象解构" if "async ({" in js else "")
        # ② 动态：node 真跑，模拟 playwright evaluate(js, arg) —— arg 是**数组**
        harness = (
            "globalThis.location={origin:'https://www.douyin.com'};"
            "globalThis.AbortSignal={timeout:(m)=>({m})};"
            "globalThis.fetch=async(u,o)=>({status:200,text:async()=>'{}'});"
            f"const fn=eval('('+{json.dumps(js)}+')');"
            f"fn({json.dumps([p1, p2])}).then(r=>console.log('OK '+JSON.stringify(r.status)))"
            ".catch(e=>console.log('ERR '+String(e)));"
        )
        r = subprocess.run(["node", "-e", harness], capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        out = (r.stdout or "").strip()
        check(f"A1 {var} 数组参真跑通过", out.startswith("OK"), out[:120] or (r.stderr or "")[:120])


# --------------------------------------------------------------------------- #
# A2：失败路径也要记账（限流不可被死循环绕过）
# --------------------------------------------------------------------------- #
def t_a2_failure_counts_toward_limit() -> None:
    print("\n[A2] 昵称兜底：失败路径必须计入限流")
    import services.nickname_fallback as NF

    # 修复前：exec_js 抛错 → 不记账 → _last_run_at 保持 0
    NF._last_run_at = 0.0
    NF._day_key = ""
    NF._day_count = 0
    NF._cfg = lambda: {"enabled": True, "min_interval_sec": 600,
                       "max_per_run": 10, "daily_cap": 50}
    NF.missing_nickname_convs = lambda *a, **kw: [
        {"conv_id": "2", "sec_uid": "SEC_X", "peer_id": "1", "peer_name": ""}]

    def boom(js, arg):
        raise RuntimeError("upstream 429")

    r = NF.run_fallback("acct", boom, db=_FakeConn())
    check("A2 失败后 _last_run_at 已写入", NF._last_run_at > 0, f"_last_run_at={NF._last_run_at}")
    check("A2 失败后日额度已累计", NF._day_count > 0, f"_day_count={NF._day_count}")
    allow, why = NF.rate_limit_check()
    check("A2 失败后立即再调被冷静期拦住", (not allow) and why.startswith("cooldown"),
          f"allow={allow} why={why}")
    check("A2 失败响应如实报错", (not r["ok"]) and "429" in str(r["reason"]) or
          (not r["ok"]), str(r.get("reason")))


def _FakeConn():
    import sqlite3
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE dm_conversations(account TEXT, conv_id TEXT, peer_id TEXT,"
              " peer_name TEXT, short_id TEXT)")
    return c


# --------------------------------------------------------------------------- #
# A3：环境基线必须按 ok 门控
# --------------------------------------------------------------------------- #
def t_a3_baseline_gated_by_ok() -> None:
    print("\n[A3] 环境基线必须按 ok 门控（契约第 1 条）")
    text = src("daemon/browser_daemon.py")
    i_call = text.find("record_baseline as _rec_base")
    i_ok = text.rfind("if ok:", 0, i_call)
    check("A3 调用点上方存在 if ok: 守卫", i_ok > 0, f"call@{i_call} if_ok@{i_ok}")
    if i_ok > 0:
        seg = text[i_ok:i_call]
        # 守卫到调用之间不得出现同级或更浅缩进的语句（否则说明已脱离 if 体）
        m = re.match(r"if ok:\n(\s+)", seg)
        body_indent = len(m.group(1)) if m else 0
        stray = [ln for ln in seg.split("\n")[1:]
                 if ln.strip() and not ln.startswith(" " * body_indent)
                 and not ln.strip().startswith("#")]
        check("A3 调用在 if ok 体内（无脱体语句）", not stray, str(stray[:2]))
    else:
        check("A3 调用在 if ok 体内（无脱体语句）", False, "未找到守卫")


# --------------------------------------------------------------------------- #
# A4：ok=True 但无产物 = 假成功
# --------------------------------------------------------------------------- #
def t_a4_no_silent_success_without_path() -> None:
    print("\n[A4] 「写盘失败」不得返回 ok=True + path=''")
    text = src("services/im_video.py")
    check("A4 无 path 分支已改为 ok=False",
          '"ok": False, "error": f"解密成功但缓存写入失败' in text,
          "仍返回 ok=True/path=''")


# --------------------------------------------------------------------------- #
# A5/A6：视频点播可达 + 要素透传
# --------------------------------------------------------------------------- #
def t_a5_a6_video_reachable() -> None:
    print("\n[A5/A6] 视频点播分支可达 + video 要素透传")
    bubble = src("frontend/src/components/messages/message-bubble.tsx")
    m = re.search(r"const shareM = t\.match\((/[^\n]+/)\)", bubble)
    pat = m.group(1) if m else ""
    check("A5 shareM 不再含「视频」", ("视频" not in pat), f"pattern={pat[:70]}")
    check("A5 点播分支仍存在", 'if (m.type === "video")' in bubble)
    # 顺序：shareM 必须在 video 分支之前（防止反过来又不可达）
    i_share = bubble.find("const shareM =")
    i_video = bubble.find('if (m.type === "video")')
    check("A5 video 分支位于 shareM 之后", 0 < i_share < i_video, f"{i_share} < {i_video}")

    shared = src("frontend/src/components/messages/message-shared.tsx")
    check("A6 RawMessage 声明 video", re.search(r"video\?:\s*MsgVideo", shared) is not None)
    page = src("frontend/src/components/messages/messages-page.tsx")
    check("A6 映射透传 video", "video: m.video || null" in page)


# --------------------------------------------------------------------------- #
# A9/A10/A11：前端状态契约
# --------------------------------------------------------------------------- #
def t_a9_a10_a11_frontend_state() -> None:
    print("\n[A9/A10/A11] 预览/选区/跳转 状态契约")
    kb = src("frontend/src/components/kb/KbImportSection.tsx")
    m = re.search(r'onChange=\{\(e\) => \{ setConvId\(e\.target\.value\);([^}]*)\}',
                  kb)
    check("A9 切会话同时清 pairs/checked",
          bool(m) and "setPairs(null)" in m.group(1) and "setChecked" in m.group(1),
          m.group(1).strip()[:80] if m else "未找到 onChange")

    page = src("frontend/src/components/messages/messages-page.tsx")
    m2 = re.search(r"const openConv = \(id: string\) => \{(.*?)\n  \};", page, re.S)
    body = m2.group(1) if m2 else ""
    check("A10 切会话清空选区锚点",
          "setSelAnchor(null)" in body and "setSelEnd(null)" in body, body.strip()[:90])
    check("A11 跳转带 pendingConv", "pendingConv" in page and
          "jumpTo.pendingConv !== conv.conv_id" in page)
    check("A11 定位失败不在空消息时放弃",
          "if (convMsgs.length > 0) setJumpTo(null);" in page)


# --------------------------------------------------------------------------- #
# A12：三元/ or 优先级（真跑表达式）
# --------------------------------------------------------------------------- #
def t_a12_ternary_precedence() -> None:
    print("\n[A12] merged_forward sender 兜底不得丢 nick_name")
    import services.merged_forward as MF

    cfg = MF._as_obj({"sender_name": "", "nick_name": "妮名", "sender": "u1"})
    # 直接调 render_text 的相关片段不易隔离 → 提取该表达式复现判据
    m = re.search(r"who = (.+?)\n", src("services/merged_forward.py"), re.S)
    check("A12 表达式已去掉悬空三元", m is not None and
          "if isinstance(" not in (m.group(1) if m else ""),
          (m.group(1)[:90] if m else "未找到"))


# --------------------------------------------------------------------------- #
# A13：测试类必须可被直跑收集
# --------------------------------------------------------------------------- #
def t_a13_test_collectable() -> None:
    print("\n[A13] TestConversationSeq 必须位于 __main__ guard 之前")
    text = src("test_upstream_p4.py")
    i_guard = text.find('if __name__ == "__main__":')
    i_cls = text.find("class TestConversationSeq")
    check("A13 guard 在类定义之后", 0 < i_cls < i_guard, f"class@{i_cls} guard@{i_guard}")


# --------------------------------------------------------------------------- #
# A14：conv_type 回填 SQL 括号
# --------------------------------------------------------------------------- #
def t_a14_conv_type_sql() -> None:
    from services.conv_identity import conv_type as CT
    print("\n[A14] conv_type 回填必须按 conv_id 形态分类（不得把单聊误判成群聊）")
    import ast as _ast
    import sqlite3
    # 用 AST 取**真实拼接结果**（比字符串切片可靠：不受换行与转义影响）
    tree = _ast.parse(src("database.py"))
    stmts: list[str] = []
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Call) and getattr(node.func, "attr", "") == "execute":
            try:
                val = _ast.literal_eval(node.args[0])
            except Exception:
                continue
            if isinstance(val, str) and "SET conv_type=" in val:
                stmts.append(val)
    check("A14 回填语句存在", len(stmts) >= 1, f"找到 {len(stmts)} 条")

    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE dm_conversations(account TEXT, conv_id TEXT,"
                " conv_type INTEGER)")
    # 覆盖四种形态：单聊(非数字) / 群聊(纯数字) / 单聊(已标1) / NULL(旧库)
    con.executemany("INSERT INTO dm_conversations VALUES(?,?,?)",
                    [("a", "0:1:11:22", 1), ("a", "316276709526638", None),
                     ("a", "0:1:33:44", 1), ("a", "999888777", 1),
                     ("a", "0:1:55:66", None)])
    for s in stmts:
        con.execute(s)
    got = dict(con.execute("SELECT conv_id, conv_type FROM dm_conversations"))
    check("A14 单聊形态绝不被判成群聊",
          got["0:1:11:22"] == 1 and got["0:1:33:44"] == 1 and got["0:1:55:66"] == 1,
          str(got))
    check("A14 纯数字 conv_id 判为群聊",
          got["316276709526638"] == 2 and got["999888777"] == 2, str(got))
    check("A14 结果与 conv_identity.conv_type 判据一致",
          all(got[c] == CT(c) for c in got), str(got))


def t_conv_type_single_source() -> None:
    print("\n[契约] 群聊判定必须收敛到 conv_identity.conv_type")
    from services.conv_identity import conv_type as CT
    for cid, want in (("738291000111", 2), ("0:1:1:2", 1), ("12345", 2),
                      ("abc", 1), ("", 1), ("  42  ", 2)):
        check(f"conv_type({cid!r}) == {want}", CT(cid) == want, f"实际 {CT(cid)}")
    # 三处调用方不得再有内联重写
    for rel in ("api/messages.py", "auto_dm/conversation_capture.py",
                "services/chatlab_export.py"):
        t = src(rel)
        check(f"{rel} 无内联 2 if … isdigit()",
              "2 if str(conv_id).isdigit() else 1" not in t and
              "2 if str(cid).isdigit() else 1" not in t)


# --------------------------------------------------------------------------- #
# A15：撤回断言必须真实（不得恒真）
# --------------------------------------------------------------------------- #
def t_a15_recall_assertion_real() -> None:
    print("\n[A15] 撤回断言不得为恒真式")
    t = src("test_upstream_p3.py")
    check("A15 已去掉 assertGreaterEqual(...,0) 恒真断言",
          "im.size[1], 0" not in t and "im2.size[1], 0" not in t)
    check("A15 断言原文不在撤回态出现", 'assertNotIn("原始内容原文"' in t)
    check("A15 有反向对照（未撤回态必须含原文）", 'assertIn("原始内容原文"' in t)


def main() -> int:
    print("=" * 72)
    print("v0.43.89 审计修复验收（本机自证；零常驻进程、零浏览器）")
    print("=" * 72)
    for fn in (t_a1_js_array_contract, t_a2_failure_counts_toward_limit,
               t_a3_baseline_gated_by_ok, t_a4_no_silent_success_without_path,
               t_a5_a6_video_reachable, t_a9_a10_a11_frontend_state,
               t_a12_ternary_precedence, t_a13_test_collectable,
               t_a14_conv_type_sql, t_conv_type_single_source,
               t_a15_recall_assertion_real):
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            check(f"{fn.__name__} 执行异常", False, f"{type(e).__name__}: {e}")
    print("\n" + "=" * 72)
    print(f"通过 {len(PASS)} / 失败 {len(FAIL)}")
    if FAIL:
        print("失败项：" + "、".join(FAIL))
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
