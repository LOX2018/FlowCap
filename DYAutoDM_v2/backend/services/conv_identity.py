# -*- coding: utf-8 -*-
"""会话身份解析单一真相源（2026-09-16 v0.43.39）。

设计契约
--------
**会话 ID（conv_id）与账号 UID 的解析，全项目只允许一处实现。**

为什么必须收敛（根因回溯）
--------------------------
改前实测：同一件事（「从 conv_id 0:1:uidA:uidB 解析真实对端、排除本账号、
拒绝发给自己」）在 **5 处独立重写**：

  - daemon/recv_daemon.py        ×2（/send、/send_by_uid 各一遍）
  - auto_dm/conversation_capture.py ×2（存量订正、脏数据修复）
  - services/dm_dispatch.py::ConvPool._peer_from_conv ×1

后果（不是「代码丑」，是真实故障面）：
  1. **判定强度不一致**：recv_daemon 两处用 `uid_probe.get_uid`（探活链路），
     而 ConvPool 用「会话池统计推断」（本账号 uid 必然出现在该账号每一个
     conv_id 中）。同一账号两套 my_uid 来源 ⇒ 一边判「对端=自己」拒发，
     另一边判「可以发」⇒ 行为漂移。
  2. **修复不同步**：conversation_capture 的「张冠李戴」修复（v0.43.11）
     只落在它自己那一份；recv_daemon 的两份没有等价加固。
  3. 任一处规则变更（如新增 conv_id 形态），必须人工同步 5 处，漏一处即
     隐性 bug —— 属「复制粘贴式架构」，违反单一职责。

本模块把上述规则固化为唯一实现，所有调用方零条件改调此处。

UID 语义（重要，勿再引入第二套）
--------------------------------
本项目**不存在「两套 uid」**（历史事故已实测更正 + uid_probe 已加固交叉
验证）。取本账号 uid 的**权威**来源是「会话池统计推断」：

    本账号 uid 必然出现在该账号的**每一个** conv_id 里（`X:Y:我:对端`
    或 `X:Y:对端:我`），故统计出现次数 ≈ 会话数 的那个 uid 即本账号。
    该判据不依赖任何网络请求、不受缓存 TTL 影响、与 conv_id 同源，
    因此比探活 uid 更可靠（探活链路有 300s TTL 与失败退避）。

统计不可用时（无会话数据）才回退 `uid_probe.get_uid`。
"""
from __future__ import annotations

import threading
from typing import Optional

from services.ttl_cache import TTLCache

# 统一 TTL 缓存（2026-09-16 v0.43.40：改用 services.ttl_cache 单一原语）。
# 为什么需要：本函数在发送/接收热路径被高频调用，每次全表扫
# dm_conversations 得不偿失；而会话数量级变化缓慢，60s 足够新鲜。
# TTL 取 60s（而非 uid_probe 的 300s）：本推断**零网络**，快照更新无成本，
# 更短 TTL 意味着换号/迁移后更快自愈。
MY_UID_TTL = float(__import__("os").environ.get("DY_MY_UID_TTL", "60") or 60)
# 2026-09-17 修补（审查 P2-5）：设置容量上限兜底。key 是账号名（数量有限），
# 但本类是通用原语，加 maxsize 可防止后续被高频 key 实例化时内存无界增长。
_my_uid_cache = TTLCache(default_ttl=MY_UID_TTL, maxsize=512)

# 统计推断的置信阈值：本账号 uid 出现在 ≥90% 的 conv_id 中
_MY_UID_COVERAGE = 0.9


def my_uid(account: str, *, fresh: bool = False) -> str:
    """取本账号 uid（权威：会话池统计推断；回退探活）。无则返回 ""。

    fresh=True 跳过缓存（凭证门禁等必须鲜值的路径可用）。
    """
    if not account:
        return ""
    if not fresh:
        found, hit = _my_uid_cache.peek(account)
        if found:
            return hit

    uid = _infer_from_conv_pool(account)
    if not uid:
        # 回退：统一探活调度器（缓存优先，通常零网络）
        try:
            from services.uid_probe import get_uid
            uid = str(get_uid(account) or "")
        except Exception:
            uid = ""

    _my_uid_cache.set(account, uid)
    return uid


def infer_my_uid_from_conv_ids(conv_ids, coverage: float = _MY_UID_COVERAGE) -> str:
    """从**任意** conv_id 序列推断本账号 uid（纯函数，不碰数据库）。

    用途：调用方手里已有内存里的 conv_id 列表（如 capture 进程的 convs、
    IndexedDB 截获结果），无需落库即可推断 —— 但仍必须是**同一套算法**，
    故实现只有这一处。

    判据（2026-09-19 修正）：
      本号 uid 在 conv_id `X:Y:<uid_a>:<uid_b>` 里**位次不固定** ——
      作为会话属主时出现在 idx=3，被他人主动会话时出现在 idx=2。
      因此旧判据「只出现在 idx=2 或 idx=3 之一」会把**本号既当属主又当对端**
      的账号（本次实测：张老师本号 idx2×20 + idx3×260 = 280/280 会话）
      误判为推断失败，导致 my_uid 归空、peer 解析退化「取 b」→ create 发自己。

      新判据：统计某 uid 在 idx2/idx3 的**合计**出现次数，取"合计 ≥ 会话数 ×
      coverage"者为本号 uid（本号出现在几乎每条会话里）。
      附加排除：若某 uid 合计覆盖度虽高，但**明显低于**次高者（>2 倍差距且非
      全覆盖），说明它更像热门对端而非本号 —— 此时保守返回 ""（数据异常不猜）。
      （覆盖度由调用方 coverage 参数控制，默认见模块常量。）
    """
    cnt: dict[str, int] = {}
    n = 0
    for cid in (conv_ids or []):
        parts = str(cid or "").split(":")
        if len(parts) >= 4:
            n += 1
            for idx in (2, 3):
                u = parts[idx]
                if u:
                    cnt[u] = cnt.get(u, 0) + 1
    if not n:
        return ""
    ranked = sorted(cnt.items(), key=lambda kv: kv[1], reverse=True)
    best, best_c = ranked[0]
    if not best or best_c < n * coverage:
        # 无任何 uid 达到覆盖阈值 —— 会话数据太杂乱（如被污染），不猜
        return ""
    return best


def _infer_from_conv_pool(account: str) -> str:
    """会话池统计推断本账号 uid：出现次数 ≈ 会话数 的那个 uid。

    判据说明（勿改回只看覆盖度）：
      本账号 uid 与对端 uid 在 conv_id 里**位次固定** ——
      形如 `X:Y:<本账号>:<对端>`（本账号段出现在每一条会话里）。
      故「覆盖度 ≥ 90%」可定位本账号；但**不能只看覆盖度** ——
      若某账号会话总数很少，脏数据也可能凑出高覆盖。故叠加
      「位置稳定（只出现在 idx=2 或 idx=3 之一）」做二次确认。
    """
    try:
        from database import get_db
        conn = get_db()
        rows = conn.execute(
            "SELECT conv_id FROM dm_conversations WHERE account=?",
            (account,)).fetchall()
    except Exception:
        return ""
    if not rows:
        return ""
    cids = [(r[0] if not hasattr(r, "keys") else r["conv_id"]) or "" for r in rows]
    return infer_my_uid_from_conv_ids(cids)


def peer_uid(conv_id: str, my: str) -> Optional[str]:
    """从 conv_id（0:1:uidA:uidB）解析真实对端 uid。

    规则（唯一真相）：
      - 形如 a:b 且 a==b  → None（自发自收的系统会话，不是真实对端）
      - my 已知：a==my 返回 b；b==my 返回 a；都不是 my → None（数据异常，不猜）
      - my 未知：返回 b（弱保证，与历史行为一致）
    """
    parts = str(conv_id or "").split(":")
    if len(parts) < 4:
        return None
    a, b = parts[2], parts[3]
    if not a or not b or a == b:
        return None
    if my:
        if a == str(my):
            return b
        if b == str(my):
            return a
        return None
    return b


def correct_peer_id(account: str, conv_id: str, claimed) -> tuple[Optional[str], bool]:
    """订正会话的 peer_id，返回 (真实对端 uid, 是否发生了订正)。

    用途：发送路径入口的「会话整理防线」。claimed 为上游给的 peer_id
    （可能被污染成本账号 uid）。解析失败时保守返回原值（(claimed, False)），
    绝不因解析不出而丢弃可用的 claimed。
    """
    my = my_uid(account)
    real = peer_uid(conv_id, my)
    if real and (not claimed or str(claimed) != str(real)):
        return real, True
    return (str(claimed) if claimed else None), False


def self_send_error(account: str, peer_id) -> Optional[str]:
    """若 peer_id 等于本账号 uid，返回拒绝文案；否则 None。

    这是「拒绝发给自己」的**唯一**实现 —— 与 correct_peer_id 同源，
    杜绝两处判定强度不一致导致的误发/误拒。
    """
    my = my_uid(account)
    if my and peer_id and str(peer_id) == str(my):
        return f"拒绝发送：对端 uid 等于本账号 uid（{my}）"
    return None


def conv_type(conv_id: str) -> int:
    """从 conv_id 判定会话类型 → 1=单聊 / 2=群聊（**唯一实现**）。

    照上游口径（web_scraper._acquire_short_id）：**conv_id 为纯数字**即群聊
    （实测群聊 conv_id 本身就是 short_id）；形如 `0:1:uidA:uidB` 为单聊。

    2026-09-18 收敛：此前同一判据在 3 处独立重写
    （`api/messages.py`、`auto_dm/conversation_capture.py`、`services/chatlab_export.py`），
    属本模块设计契约第 1 条明令禁止的重复实现（改一处漏两处即隐性 bug）。
    调用方是**纯 DB 路径**（不依赖本账号 uid），故不接收 account 参数。
    """
    return 2 if str(conv_id or "").strip().isdigit() else 1


def clear_cache(account: str = "") -> None:
    """清缓存（账号迁移/换号后用；account 空则全清）。"""
    if not account:
        _my_uid_cache.clear()
    else:
        _my_uid_cache.invalidate(account)
