# -*- coding: utf-8 -*-
"""数据契约审计（R8）—— 全库审计的第八个持久维度（2026-09-26 新增）。

## 为什么要加这个维度（起因：用户 2026-09-26 指出）

> 「这个项目我至少经历过 4 次全盘审计，都没有发现数据库治理的问题」

实测复盘 3 份历史审计报告，其维度全部是「代码结构」视角：

| 报告 | 维度 | 为何看不见 DB 治理问题 |
|---|---|---|
| 09-21 架构审计 | 入口分裂 / 分层依赖逆行 / 大组件行数 / 死面 | 不看「字段被几种语义混用」 |
| 09-23 一致性审计 | 符合项评分 / 跨模块耦合 / 技术债净增 | 同上 |
| 09-24 累积补丁审计 | 逐条 diff（45 条按 severity 判真假） | 本质是增量审；schema 从没改过，天然不在视野 |

根因：审计缺一个持久的「数据契约」维度，且必须是存量扫描（不看 diff）。
「没变化的危险」永远不会被增量审计发现。

## R8 五条判据（每条都可被历史事故反证）

  R8-1  写入出口收敛：dm_messages 不得有裸写列名元组（ADR-012 层 2）
  R8-2  类型注册表完整：代码里出现的 msg_type 字面量必须已登记
  R8-3  语义字段纯度：text 不得承载 base64 / URL 载荷
  R8-4  语义标签 SSOT：不得在业务文件里硬编码标签字面量
  R8-5  前向兼容：未知类型必须降级（且门禁在位）

用法：
    python scripts/audit_data_contract.py            # 人类可读
    python scripts/audit_data_contract.py --json     # 机器可读（供 CI）
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))          # .../DYAutoDM_v2/scripts
_ROOT = os.path.dirname(_HERE)                              # .../DYAutoDM_v2
_BACKEND = os.path.join(_ROOT, "backend")
sys.path.insert(0, _BACKEND)

#: 承载 dm_messages 写入的文件
WRITE_FILES = (
    "auto_dm/conversation_capture.py",
    "daemon/recv_daemon.py",
    "daemon/wp_recv.py",
    "database.py",
    "services/delivery_verify.py",
)

# R8-1：裸写列名元组（锚定「行首缩进后紧跟 (」）
# 教训（实测踩到两次）：不锚定行首会误命中已修好的 _ws_tuple(self.name, ...)
RAW_TUPLE_PAT = re.compile(
    rb'(?m)^\s*\(\s*(?:self\.name|ib\.name|name|acct)\s*,\s*(?:conv_id|cid)\s*,'
)

# R8-3：text 内不得出现这些载荷形态（bytes 模式；中文用 utf-8 encode）
_IMG = "\u56fe\u7247".encode("utf-8")          # 图片
PAYLOAD_IN_TEXT = (
    re.compile(rb'data:image/[A-Za-z0-9.+-]+;base64,'),
    re.compile(rb'\[' + _IMG + rb'\]\s*https?://'),
)

# R8-4：语义标签硬编码（业务文件内应 import SSOT，不得写字面量）
LABEL_HARDCODE = re.compile(
    rb'return\s+"\[' + _IMG + rb'\]"|=\s+"\[' + _IMG + rb'\]"'
)


def _read(rel: str) -> bytes:
    with open(os.path.join(_BACKEND, rel), "rb") as f:
        return f.read()


def check_r8_1() -> tuple:
    """写入出口收敛：dm_messages 的 INSERT 不得再有裸写列名元组。

    ⚠️ 实测踩到（2026-09-26）：初版正则不区分表，把 `dm_conversations`
    的插入也命中了（4 处假红）。审计工具造假红比漏报更危险 —— 会让人
    不再信任它。故必须**先定位 `INSERT ... INTO dm_messages`，再在其
    后的参数位置**找裸元组。
    """
    hits = []
    ins_pat = re.compile(rb'INSERT\s+OR\s+IGNORE\s+INTO\s+dm_messages'
                         rb'|INSERT\s+INTO\s+dm_messages')
    tup_pat = re.compile(
        rb'(?m)^\s*\(\s*(?:self\.name|ib\.name|name|acct)\s*,\s*(?:conv_id|cid)\s*,')
    for rel in WRITE_FILES:
        try:
            src = _read(rel)
        except OSError:
            continue
        for m in ins_pat.finditer(src):
            # 只扫 INSERT 之后 600 字节窗口（覆盖 SQL + 参数元组）
            window = src[m.start():m.start() + 600]
            for t in tup_pat.finditer(window):
                line = src[:m.start() + t.start()].count(b"\n") + 1
                hits.append(f"{rel}:{line}")
    return (not hits), f"dm_messages 写入出口收敛（{len(WRITE_FILES)} 文件）", hits


def check_r8_2() -> tuple:
    """类型注册表完整：源码里 msg_type 字面量必须已登记。"""
    try:
        from services.message_schema import MSG_TYPES
    except Exception as e:
        return False, f"注册表不可导入：{e}", []
    known = set(MSG_TYPES)
    pat = re.compile(rb'msg_type["\']?\s*[:=]\s*["\']([0-9A-Za-z_]+)["\']')
    found = {}
    for rel in WRITE_FILES + ("services/message_schema.py",):
        try:
            src = _read(rel)
        except OSError:
            continue
        # ⚠️ 实测踩到：schema 注释里提到历史别名 "image" 会被当成未登记字面量。
        # 判据只认**代码**，不认注释 ⇒ 先剥离。
        for m in pat.finditer(_strip_comments(src)):
            v = m.group(1).decode()
            if v in ("msg_type", "text", "role", "kind"):
                continue
            found.setdefault(v, []).append(rel)
    unknown = {k: sorted(set(v)) for k, v in found.items() if k not in known}
    return (not unknown), f"类型注册表覆盖（已登记 {len(known)} 种）", [
        f"未登记 msg_type={k} <- {v}" for k, v in unknown.items()]


def _strip_comments(src: bytes) -> bytes:
    """去掉注释行与 docstring，避免把文档里的示例当成真实载荷（实测踩到）。"""
    out = []
    in_doc = False
    for line in src.split(b"\n"):
        s = line.strip()
        if s.startswith(b'"""') or s.startswith(b"'''"):
            if s.count(b'"""') >= 2 or s.count(b"'''") >= 2:
                out.append(b"")          # 单行 docstring
            else:
                in_doc = not in_doc
                out.append(b"")
            continue
        if in_doc:
            out.append(b"")
            continue
        if s.startswith(b"#"):
            out.append(b"")
            continue
        out.append(line)
    return b"\n".join(out)


def check_r8_3() -> tuple:
    """语义字段纯度：text 不得承载 base64 / 图片 URL 载荷。

    ⚠️ 实测踩到：docstring 里的格式说明（"data:image/webp;base64,<inline_pic>"）
    会被当成真实载荷命中（3 处假红）。故先剥离注释与 docstring。
    """
    hits = []
    for rel in WRITE_FILES:
        try:
            src = _strip_comments(_read(rel))
        except OSError:
            continue
        for pat in PAYLOAD_IN_TEXT:
            for m in pat.finditer(src):
                line_no = src[:m.start()].count(b"\n") + 1
                # 回溯该行：若处于「产出 extra.thumb / data URI 给前端」的函数内，
                # 则它是渲染载荷而非写 text ⇒ 不算污染（_extract_thumb_data_uri）
                ctx = src[max(0, m.start() - 1200):m.start()]
                if b"def _extract_thumb_data_uri" in ctx \
                        or b"thumb_url" in ctx[-400:] \
                        or b"extra[\"thumb\"]" in ctx:
                    continue
                snippet = src[m.start():m.start() + 40].decode("utf-8", "ignore")
                hits.append(f"{rel}:{line_no} {snippet!r}")
    return (not hits), "text 不得承载 base64/图片 URL 载荷", hits


def check_r8_4() -> tuple:
    """语义标签 SSOT：业务文件不得硬编码标签字面量。"""
    hits = []
    for rel in ("auto_dm/conversation_capture.py",
                "daemon/recv_daemon.py", "daemon/wp_recv.py"):
        try:
            src = _read(rel)
        except OSError:
            continue
        for m in LABEL_HARDCODE.finditer(src):
            line = src[:m.start()].count(b"\n") + 1
            hits.append(f"{rel}:{line}")
    return (not hits), "语义标签须取自 Schema SSOT（禁硬编码）", hits


def check_r8_5() -> tuple:
    """前向兼容：未知类型降级机制必须存在且被门禁覆盖。"""
    miss = []
    try:
        from services import message_schema as ms
        r = ms.MessageRecord.build(text="x", msg_type="__nonexistent__")
        if r.kind != "unknown":
            miss.append("未知类型未降级为 unknown")
        if ms.readable(r.text, r.msg_type, r.extra):
            miss.append("未知类型仍被读侧放行")
    except Exception as e:
        miss.append(f"降级机制不可用：{e}")
    gate = os.path.join(_BACKEND, "test_h29_message_schema.py")
    if not os.path.isfile(gate):
        miss.append("缺少门禁 test_h29_message_schema.py")
    return (not miss), "前向兼容（未知类型降级 + 门禁在位）", miss


_R8_6_ALLOWED: dict = {
    "services/ai_reply.py:*": "ADR-008 决策2/P1-1：WS 落 '7'、查询落 'text'，同一 user_text 语义；'27' 为图片走 _build_history 二次筛选",
    "mcp/tools_debug.py:*": "同上口径（落库健康统计与 ai_reply 白名单必须同源一致）",
}
# R8-6 已知豁免：`{"<rel>:<line 或 *>": "<理由>"}`。
#
# 纪律（照本仓 `test_m17_order_independence._ALLOWED_UNLOADS` / G6 `_ALLOWED_ORPHANS`）：
# **只因「确属已登记决策的既定事实」才登记，不是为让门禁变绿**。
# 若一条豁免的理由说不出「哪条 ADR / 哪个决策号」，就不该登记 —— 应去修数据契约。
#
# ⚠️ 登记后条目仍会**以 `[已豁免]` 前缀打印**（不静默），确保审计者看得见已知例外。
#
# 当前两条的由来（2026-10-04 登记）：`msg_type IN ('text','7','27')` 是
# **ADR-008 决策2（2026-09-23）+ P1-1 + 2026-09-25 决策** 的既定事实：
#   · WS 实时路径落库 `'7'`，历史/查询路径落库 `'text'`（同一 `user_text` 语义、两个名字）；
#   · `'27'` 是图片（`media`），SQL 层先收、再由 `_build_history` 按视觉描述二次筛选；
#   · 两处**故意**用显式枚举而非 `message_schema.MSG_TYPES`，因为 `MSG_TYPES` 会把
#     `15/50010/1/0` 等 system_notice 一并收进 —— 白名单优于排除式（宁可少收）。
#   ⇒ 正解是「注册表把 `text`/`7` 合并为同一规范名」，但那要动**写入侧全部落库点**
#     （发送/接收关键路径），风险远大于收益，需单开 ADR。故此处登记为已知例外。


def check_r8_6() -> tuple:
    """R8-6：同一语义不得多名字（本次 4 次审计漏掉的根因）。

    判据：SQL 里出现 `msg_type IN (...)` / `msg_type = 'x' OR msg_type = 'y'`
    这种**多值并列**写法，即「同一语义两个名字」的补丁痕迹，必须报出。

    🔴 2026-10-04 修（审计 P1 假绿，H-36）：原实现有两处盲区 ——
      ① 扫描面是**硬编码文件列表**（WRITE_FILES + 3 个名字），
         `mcp/tools_debug.py` 不在其中 ⇒ 漏 `msg_type IN ('text','7','27')`；
      ② 正则只认**字面量** `IN ('a','b')`，参数化 `IN ({ph})`
         （占位符在别处按白名单元组展开）匹配不到 ⇒ 漏 ai_reply.py:2507。
    修后：扫描面走**全 backend 扫描**；新增参数化判据（`IN ({ph})` +
    同文件 `_*_TYPES = (...)` 白名单元组 ≥2 元素）。
    """
    hits = []
    # msg_type IN ('a','b',...) 含 ≥2 个值
    pat_in = re.compile(
        rb"msg_type\s+IN\s*\(\s*['\"]([^'\"]+)['\"]\s*,\s*['\"]([^'\"]+)['\"]")
    # msg_type = 'a' OR msg_type = 'b'
    pat_or = re.compile(
        rb"msg_type\s*=\s*['\"]([^'\"]+)['\"].{0,40}?OR.{0,40}?"
        rb"msg_type\s*=\s*['\"]([^'\"]+)['\"]", re.I)
    # 参数化形态：`msg_type IN ({ph})` 且同文件存在 ≥2 元素的白名单元组
    pat_ph = re.compile(rb"msg_type\s+IN\s*\(\s*\{\s*\w+\s*\}\s*\)", re.I)
    pat_tuple = re.compile(
        rb"(?m)^\s*(?:_)?[A-Z_]*(?:TYPE|TYPES|KIND|KINDS)\w*\s*=\s*\(([^)]*)\)")

    def _scan(rel: str) -> None:
        try:
            src = _read(rel)
        except OSError:
            return
        for m in pat_in.finditer(src):
            if m.group(1) != m.group(2):
                line = src[:m.start()].count(b"\n") + 1
                hits.append(
                    f"{rel}:{line} 多值并列 IN("
                    f"{m.group(1).decode()},{m.group(2).decode()}...)")
        for m in pat_or.finditer(src):
            if m.group(1) != m.group(2):
                line = src[:m.start()].count(b"\n") + 1
                hits.append(
                    f"{rel}:{line} OR 并列 "
                    f"{m.group(1).decode()}/{m.group(2).decode()}")
        # 参数化：IN ({ph}) + 同文件白名单元组 ≥2 元素
        if pat_ph.search(src):
            for tm in pat_tuple.finditer(src):
                items = [x.strip().strip(b"'\"") for x in tm.group(1).split(b",")
                         if x.strip()]
                if len(items) >= 2:
                    line = src[:tm.start()].count(b"\n") + 1
                    vals = ",".join(x.decode(errors="replace") for x in items[:4])
                    hits.append(
                        f"{rel}:{line} 参数化并列 IN({{ph}}) "
                        f"← 白名单元组 ({vals})")

    def _is_allowed(rel: str, line: int) -> bool:
        return (f"{rel}:{line}" in _R8_6_ALLOWED) or (f"{rel}:*" in _R8_6_ALLOWED)

    # 扫描面：WRITE_FILES + 已知消费方 + **全 backend 其余 .py**（补盲区①）
    seen = set()
    for rel in WRITE_FILES + ("services/dm_search.py", "services/ai_reply.py",
                              "services/dm_dispatch.py"):
        if rel not in seen:
            seen.add(rel)
            _scan(rel)
    for dirpath, _dirs, files in os.walk(_BACKEND):
        if "__pycache__" in dirpath or "_ext_repos" in dirpath:
            continue
        for fn in files:
            if not fn.endswith(".py"):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), _BACKEND)
            rel = rel.replace(os.sep, "/")
            if rel in seen:
                continue
            seen.add(rel)
            _scan(rel)
    # 应用已知豁免：命中的项**从阻断列表移除**，但作为 note 保留可观测性
    notes = []
    real_hits = []
    for h in hits:
        rel, _, tail = h.partition(":")
        try:
            line = int(tail.split(" ", 1)[0])
        except (ValueError, IndexError):
            line = -1
        if _is_allowed(rel, line):
            notes.append(f"[已豁免] {h}")
        else:
            real_hits.append(h)
    return (not real_hits), "同一语义不得多名字（禁 msg_type 多值并列）", (real_hits + notes)


CHECKS = (
    ("R8-1", check_r8_1),
    ("R8-2", check_r8_2),
    ("R8-3", check_r8_3),
    ("R8-4", check_r8_4),
    ("R8-5", check_r8_5),
    ("R8-6", check_r8_6),
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    results = []
    for code, fn in CHECKS:
        ok, desc, evidence = fn()
        results.append({"code": code, "ok": ok, "desc": desc,
                        "evidence": evidence[:10]})

    failed = [r for r in results if not r["ok"]]
    if args.json:
        print(json.dumps({"passed": len(results) - len(failed),
                          "failed": len(failed), "results": results},
                         ensure_ascii=False, indent=2))
        return 1 if failed else 0

    print("=" * 68)
    print("  数据契约审计（R8）—— 全库审计第八个持久维度")
    print("=" * 68)
    for r in results:
        flag = "PASS" if r["ok"] else "FAIL"
        print(f"  [{flag}] {r['code']}  {r['desc']}")
        for e in r["evidence"]:
            print(f"          -> {e}")
    print("-" * 68)
    print(f"  合计 {len(results)} 项：通过 {len(results) - len(failed)}，"
          f"未通过 {len(failed)}")
    print("=" * 68)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
