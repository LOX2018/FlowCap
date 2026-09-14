# -*- coding: utf-8 -*-
"""v0.43.11 A+B 验收：会话昵称↔peer_uid 关联（DOM 无 uid 的根治）。

背景（实机确证）：
  · 抖音改版后昵称只在**会话列表 DOM**（项上仅 data-e2e/class/title/img，
    **无任何 uid 属性**；点击会话也不改 URL）。
  · peer_uid 只在**首包 protobuf**。两端无共同 id ⇒ 位置/顺序对齐必然错位。
  · 曾用 `convs.index(_c)` 做「位置猜测」，而 `_c` 是循环泄漏变量恒为最后一个
    元素 → 80 条会话的 peer_name 全被写成同一个「傲雪」（张冠李戴）。
  · WS 的 `sender_nickname` 实测恒 None（15 天 27947 条全 None）⇒ 原「B 方案」
    按原名设想（用 sender_nickname 回填）**不可行**，须改用 sender(uid) + conv_id。

方案：
  A · 文本桥：首包每个会话自带 messages（权威、带 uid），DOM 每项 desc 就是
      「该会话最后一条消息」→ 用文本精确匹配得 uid。**只在唯一命中时**建立映射；
      歧义一律放弃（宁可不显示，绝不张冠李戴）。
  B · recv_daemon：peer_uid 只认 conv_id（排除本号），昵称取不到就留空
      （绝不把裸 sender 当昵称）。

跑法： python scripts/verify_nickname_link.py     （纯离线，不碰浏览器/DB）
"""
from __future__ import annotations

import ast
import io
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BK = os.path.join(ROOT, "backend")
sys.path.insert(0, BK)

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail else ""))


def src(rel: str) -> str:
    with io.open(os.path.join(BK, rel), "r", encoding="utf-8") as f:
        return f.read()


print("=" * 74)
print("① 静态：位置猜测已彻底移除，且无循环变量泄漏")
print("=" * 74)
cc = src("auto_dm/conversation_capture.py")
# 只算「可执行代码」里的位置猜测（注释里提到旧实现不算）
_cc_code = "\n".join(
    ln for ln in cc.splitlines() if not ln.strip().startswith("#")
)
check("①1 不再出现 convs.index(...) 位置猜测（排除注释）",
      "convs.index(" not in _cc_code)
bd = src("daemon/browser_daemon.py")
check("①2 DOM 抓取新增 desc 字段（文本桥的唯一来源）",
      "desc: desc" in bd and "ConversationItemDescleft" in bd)
# 🔴 本轮漏网 bug：JS 抓了 desc，但组装 _dom_seen 时没带上 → 后端拿不到匹配键。
# 断言「两端都收 desc」：JS push 处 + _dom_seen 组装处（2 处）。
_n_desc = bd.count('"desc": (_dit or {}).get("desc")') + bd.count('"desc": (_it or {}).get("desc")')
check("①2b desc 确实被组装进 _dom_seen（不止 JS 抓、还要传回来）",
      _n_desc == 2, f"命中 {_n_desc} 处（期望 2：主循环 + 末屏补充）")
_js = bd.split("CAP_DOM_SWEEP_JS", 1)[1].split('"""')[1]
_js_code = "\n".join(ln for ln in _js.splitlines() if "//" not in ln)
check("①3 DOM 抓取不读 uid（保持零主动请求）",
      "uid" not in _js_code, "uid 仅允许出现在注释里")

print()
print("=" * 74)
print("② 文本桥不变式（离线仿真，用真实结构的样本数据）")
print("=" * 74)


def build_bridge(convs, userinfo_by_nick):
    """复刻 conversation_capture 里的桥接逻辑（与源码逐行同构）。"""
    _text_to_uids: dict[str, set] = {}
    for c in convs:
        pu = c.get("peer_uid")
        if not pu:
            continue
        for m in (c.get("messages") or []):
            tx = (m.get("text") or "").replace("\n", " ").strip()
            if len(tx) < 4:
                continue
            _text_to_uids.setdefault(tx[:80], set()).add(str(pu))
    by_uid, bridged, ambig = {}, 0, 0
    for nk, vv in list(userinfo_by_nick.items()):
        desc = (vv.get("desc") or "").replace("\n", " ").strip()
        if not desc or len(desc) < 4:
            continue
        cands = _text_to_uids.get(desc[:80]) or set()
        if not cands:
            for tk, tus in _text_to_uids.items():
                if tk[:24] and (tk[:24] in desc or desc[:24] in tk):
                    cands = set(cands) | tus
        if len(cands) == 1:
            u = next(iter(cands))
            if u not in by_uid:
                by_uid[u] = {"nickname": nk, "avatar": vv.get("avatar") or "", "uid": u}
                bridged += 1
        elif len(cands) > 1:
            ambig += 1
    return by_uid, bridged, ambig


# 场景1：唯一命中 → 建立映射（含 DOM desc 被截断的情形）
convs = [
    {"peer_uid": "1001", "messages": [{"text": "你好，看到你评论了我的内容，想和你聊聊～"}]},
    {"peer_uid": "1002", "messages": [{"text": "10级能赔多少钱，麻烦老师看下"}]},
]
nick = {"爱心是你的": {"desc": "你好，看到你评论了我的内容，想和你聊聊～", "avatar": "a1"},
        "张老师": {"desc": "10级能赔多少钱，麻烦老师看下", "avatar": "a2"}}
by_uid, b, a = build_bridge(convs, nick)
check("②1 唯一命中 → 精确建立 2 条 uid 映射", b == 2 and set(by_uid) == {"1001", "1002"},
      f"bridged={b} uids={sorted(by_uid)}")
check("②2 映射的 uid 与昵称**未错位**",
      by_uid["1001"]["nickname"] == "爱心是你的" and by_uid["1002"]["nickname"] == "张老师")

# 场景2：群发同一句话 → 歧义，必须放弃（这是「傲雪」事故的防线）
convs2 = [
    {"peer_uid": "2001", "messages": [{"text": "你好，欢迎留言咨询唐律师工伤，留个方式"}]},
    {"peer_uid": "2002", "messages": [{"text": "你好，欢迎留言咨询唐律师工伤，留个方式"}]},
]
nick2 = {"某客户A": {"desc": "你好，欢迎留言咨询唐律师工伤，留个方式", "avatar": ""},
         "某客户B": {"desc": "你好，欢迎留言咨询唐律师工伤，留个方式", "avatar": ""}}
by2, b2, a2 = build_bridge(convs2, nick2)
check("②3 歧义（群发同文本）→ 一条都不写", b2 == 0 and by2 == {},
      f"bridged={b2} ambig={a2}")
check("②4 歧义被显式计数（可观测）", a2 == 2, f"ambig={a2}")

# 场景3：无匹配（被系统提示覆盖的 desc）→ 放弃
nick3 = {"路人甲": {"desc": "对方回复你或互关之前，可发送一条文字消息...", "avatar": ""}}
by3, b3, _ = build_bridge(convs, nick3)
check("②5 无匹配（系统提示覆盖）→ 放弃，不猜", b3 == 0)

# 场景4：总不变式 —— 任何情况下都不得出现「多个 uid 共享同一昵称」
nick4 = {f"用户{i}": {"desc": f"独特消息内容_{i}_abcdefgh", "avatar": ""} for i in range(6)}
convs4 = [{"peer_uid": f"9{i:03d}", "messages": [{"text": f"独特消息内容_{i}_abcdefgh"}]}
          for i in range(6)]
by4, b4, _ = build_bridge(convs4, nick4)
names = [v["nickname"] for v in by4.values()]
check("②6 不变式：昵称不重复（无张冠李戴）",
      len(names) == len(set(names)) == 6, f"n={len(names)} uniq={len(set(names))}")

print()
print("=" * 74)
print("③ B 方案：peer_uid 只认 conv_id，昵称绝不取裸 sender")
print("=" * 74)
rd = src("daemon/recv_daemon.py")
seg = rd.split("_real_peer = None")[1][:1600] if "_real_peer = None" in rd else ""
check("③1 peer_uid 由 conv_id 推导（排除本号）",
      "len(_cp) == 4" in seg and "_mu" in seg)
check("③2 不再把裸 sender 当昵称（无 `or sender`）",
      "sender_nickname\") or sender" not in rd)
check("③3 昵称取不到就留空（绝不写数字 UID）",
      "peer_name=_nick or None" in rd)
check("③4 方向判定（08 §13.5 铁律）仍在，未被改动破坏",
      'role = "me" if sender and str(sender) == str(self.inbox.my_uid) else "them"' in rd)
check("③5 高频日志已降噪为 debug（原先每消息一条 INFO）",
      "logger.debug(" in seg and "新消息: " in seg)

print()
print("=" * 74)
print("④ 回归：写库侧不再把可能错误的昵称写入")
print("=" * 74)
check("④1 存量订正分支保留真实 UID 占位（uid 无匹配时不猜昵称）",
      '_newname = _ui.get("nickname") or _real' in cc)
check("④2 桥接结果只喂给 _userinfo_by_uid（精确路径），不影响 _by_uid 统计语义",
      "_userinfo_by_uid[_uid_hit]" in cc)

print()
print("=" * 74)
print(f"结果：PASS={len(PASS)}  FAIL={len(FAIL)}")
print("=" * 74)
if FAIL:
    for f in FAIL:
        print("  ❌ " + f)
    sys.exit(1)
print("全部通过 ✅")
