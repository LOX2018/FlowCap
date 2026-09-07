"""私信发送统一调度器 + 会话整理池（2026-09-07 架构重构）。

背景
----
私信是本项目核心能力，但发送链路此前是「多头并发、各自为政」：

  1. **4 个发送源**：手动 /send、AI 自动回复、直播 dispatch（批量）、
     图片 /send_image —— 分别调 recv_daemon 的不同端点。
  2. **已有闸门只管频率**：`recv_daemon._send_gate_acquire` 做了 per-account
     8s 最小间隔，但**不解决**：
       - 同一会话并发多条 → 乱序 / 重复发送；
       - 无优先级 → 批量任务会挤掉用户的手动发送；
       - 会话状态未整理 → peer_id 可能是被污染的"自己" uid
         （见 09 台账第六轮：32/77 会话 peer_id 被写成 my_uid），
         直接拿去 create_conversation 会发错人 / 发给自己。

设计
----
**两级结构**：

    发送源 ──入池──> 会话整理池(ConvPool) ──出队──> 统一调度器(Dispatcher) ──> recv_daemon /send
                          │                              │
                    归一化/去重/校验              串行 + 优先级 + 频率闸门

**会话整理池（ConvPool）职责**：
  - **归一化**：用 conv_id 解析真实 peer_uid（排除自身），修正被污染的
    peer_id（复用第六轮的 my_uid 自愈逻辑）；
  - **去重合并**：同一 (account, conv_id) 的待发消息按序排队，**不并发**；
  - **校验**：peer_uid 无效/是自己的一律拒绝入池（防发给自己的事故）；
  - **实时整理**：每次入池都重解析一次会话（会话可能已被 capture 更新）。

**统一调度器（Dispatcher）职责**：
  - per-account 单线程 worker 串行出队（保证同账号发送顺序 = 入池顺序）；
  - 优先级：手动(0) > AI回复(1) > 批量dispatch(2)；
  - 复用 recv_daemon 的频率闸门（三源一配额，不另起炉灶）；
  - 失败快速返回，不静默堆积。

用法
----
    from services.dm_dispatch import submit, SubmitResult

    r = submit(account="四川工伤张老师", conv_id="0:1:...", text="你好",
               source="manual", priority=0)
    if r.accepted:
        ...  # 已入池，异步发送；用 r.task_id 查状态
    else:
        print(r.error)   # 入池被拒（会话无效/重复/队列满）

配置（环境变量）
----
    DY_DM_QUEUE_MAX      每账号队列上限（默认 200，超限拒绝入池）
    DY_DM_POOL_STRICT    严格模式（默认 1）：peer_uid 无效则拒绝入池
"""
from __future__ import annotations

import os
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional, Tuple

from loguru import logger

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------
QUEUE_MAX = int(os.environ.get("DY_DM_QUEUE_MAX", "200"))
POOL_STRICT = os.environ.get("DY_DM_POOL_STRICT", "1") not in ("0", "false", "False")
# 优先级常量（数字小 = 优先）
PRIO_MANUAL = 0
PRIO_AI = 1
PRIO_BATCH = 2
_PRIO_BY_SOURCE = {"manual": PRIO_MANUAL, "ai": PRIO_AI,
                   "batch": PRIO_BATCH, "dispatch": PRIO_BATCH}
# 同名会话去重窗口：同 (account, conv_id, text) 在该秒数内重复入池视为重复
DEDUP_WINDOW = float(os.environ.get("DY_DM_DEDUP_WINDOW", "5"))

# ===========================================================================
# 【调试版专用】测试账号白名单 —— 正式版不生效、不打包
# ===========================================================================
# 需求（用户 2026-09-07）：测试私信发送时，只能「尚进工伤小助理」与
# 「四川工伤张老师」两个账号互发，绝不得发给任何其他真实会话对象。
#
# ---------------------------------------------------------------------------
# ★ 隔离机制说明（为什么这样设计，改动前务必读）
# ---------------------------------------------------------------------------
# 两个测试账号互为收发方，但**两类来源的"会话性质"不同**，白名单必须
# 让它们都能测到，否则频控/降权永远测不出来：
#
#   ┌──────────────┬──────────────┬────────────┬─────────────────────┐
#   │ 来源          │ 会话性质      │ 发送入口    │ 白名单判定           │
#   ├──────────────┼──────────────┼────────────┼─────────────────────┤
#   │ 视频采集      │ **陌生人首发** │ submit_by_uid │ 必须放行（否则测不到 │
#   │ 直播监听      │ **陌生人首发** │ submit_by_uid │ 2/分钟、30/天限流   │
#   │              │              │            │ 与频控降权/冷静）    │
#   ├──────────────┼──────────────┼────────────┼─────────────────────┤
#   │ 私信中心      │ 熟客沟通      │ submit()   │ 放行（有 conv_id，   │
#   │ AI 自动回复   │ 熟客沟通      │ submit()   │ 不计入首发额度）     │
#   └──────────────┴──────────────┴────────────┴─────────────────────┘
#
# 关键：**首发来源绝不能被"对端不在白名单"挡在限流之前** ——
# 若首发也被白名单按"会话"拦截，就永远走不到 can_stranger_first()，
# 频控降权、冷静期、权重恢复**全部无法测试**。因此：
#   - submit_by_uid（采集/监听）：只校验"账号在白名单 + 目标是对端测试
#     账号"，放行后**正常计入陌生人首发额度**（限流/降权照常生效）；
#   - submit（私信中心/AI）：按 conv_id 解析对端后校验，同样只放行
#     对端测试账号。
#
# 这样调试期既能保证「绝不发给真人」，又能完整压测限流与降权链路。
#
# 实现：白名单 uid 在**打包时由脚本注入**到本文件末尾的注入区
# （见 scripts/build_sidecar.py 的 --test-whitelist 分支）。
# 正式构建不注入 → TEST_WHITELIST_ON 恒为 False → 本段代码物理不执行。
#
# 这里只放"空壳"（正式版状态）；调试构建会被覆盖为真实 uid 集合。
_TEST_WHITELIST: Dict[str, set] = {}
TEST_WHITELIST_ON = False


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class SendTask:
    """一条待发私信（整理池内的标准形态）。"""
    task_id: str
    account: str
    conv_id: str          # 原始会话 id（可能是 0:1:uidA:uidB）
    peer_uid: str         # **整理后的真实对端 uid**（绝不会是自己的 uid）
    text: str
    source: str           # manual / ai / batch / dispatch
    priority: int
    enqueued_at: float = field(default_factory=time.time)
    # 图片发送用（可选）
    image_b64: str = ""
    filename: str = ""
    # ---- 2026-09-07 多账号 key + 来源标注 ----
    account_key: str = ""          # 账号唯一 key（=account），发送时二次核对
    is_stranger_first: bool = False  # 是否「陌生人首发」（无历史会话）
    # 结果回填
    status: str = "pending"   # pending / sending / done / failed
    error: str = ""

    def __lt__(self, other: "SendTask") -> bool:
        """优先级队列排序：先比优先级，再比入池时间（FIFO 保证顺序）。

        2026-09-07：陌生人首发额外降权——同类优先级下排在熟会话之后，
        避免批量陌生首发挤占真实对话（抖音对陌生首发限制最严）。
        """
        if self.priority != other.priority:
            return self.priority < other.priority
        if self.is_stranger_first != other.is_stranger_first:
            return not self.is_stranger_first   # 熟会话优先
        return self.enqueued_at < other.enqueued_at


@dataclass
class SubmitResult:
    accepted: bool
    task_id: str = ""
    error: str = ""
    queue_size: int = 0


# ===========================================================================
# 2026-09-07：per-account 配额 / 权重估算 / 冷静期（多账号 key 隔离）
# ===========================================================================
# 背景（用户要求）：
#   1. 多账号模式下每个账号都要有自己的 key，配额与状态按 key 隔离；
#   2. 直播监听的待发送**多数是给陌生人首发**，抖音对此限制最严：
#      **每分钟 ≤2 次、每天 ≤30 次**；
#   3. 不同账号权重不同（注意：**真实权重是抖音服务端内部值，无公开接口，
#      本地无法直接捕获，只能用可观测信号估算**）；
#   4. 发送回执若提示「频繁」，陌生人首发需**降权 + 强制冷静周期**。
#
# 设计要点：
#   - **零额外网络请求**：陌生判定 = 本地 dm_messages 无历史消息；
#   - 权重**只做本地估算**（成功率 + 频控次数），不声称是平台真实权重；
#   - 所有配额按 account key 独立记账，互不干扰。
# ===========================================================================
# 陌生人首发硬限额（抖音平台侧限制，保守取用户给定值）
STRANGER_PER_MINUTE = int(os.environ.get("DY_STRANGER_PER_MINUTE", "2"))
STRANGER_PER_DAY = int(os.environ.get("DY_STRANGER_PER_DAY", "30"))
# 收到"频繁"回执后的强制冷静期（秒），期间暂停该账号的陌生人首发
COOLDOWN_ON_FREQUENT = float(os.environ.get("DY_DM_COOLDOWN_FREQ", "600"))
# 冷静期递增：连续触发则翻倍（上限 1 小时），避免刚解封又撞墙
COOLDOWN_MAX = float(os.environ.get("DY_DM_COOLDOWN_MAX", "3600"))
# 2026-09-07 补：**权重恢复周期**（用户指出不能"降到底就永久停用"）
# freq_hits 每过 HALFLIFE 秒衰减一半 —— 被频控的账号会随时间自动"刑满释放"，
# 且只要期间表现正常就能逐步回到满权重。设为 0 可关闭衰减（不推荐）。
WEIGHT_RECOVER_HALFLIFE = float(
    os.environ.get("DY_WEIGHT_RECOVER_HALFLIFE", "21600"))   # 默认 6 小时
# 静默恢复：距上次频控超过该秒数且后续发送正常 → 冷静等级自动清零
WEIGHT_FORGIVE_AFTER = float(
    os.environ.get("DY_WEIGHT_FORGIVE_AFTER", "86400"))      # 默认 24 小时


class AccountQuota:
    """单个账号（key）的发送配额、权重估算与冷静期状态。

    每个 account 一份实例，由 DmDispatcher 按 key 持有 —— 账目完全隔离，
    不会出现 A 账号的额度被 B 账号消耗、或 A 被频控连累 B 的情况。
    """

    def __init__(self, key: str) -> None:
        self.key = key
        # 用**可重入锁**：snapshot/日志等路径会在持锁时再调 weight 类方法，
        # 普通 Lock 会死锁（实测 2026-09-07 踩过）。RLock 保证同类调用安全。
        self.lock = threading.RLock()
        # 陌生人首发记账：分钟滑窗 + 当日计数
        self._stranger_minute: list = []      # 最近 60s 内的首发时间戳
        self._stranger_day: list = []         # 当日首发时间戳
        self._day_stamp: str = time.strftime("%Y-%m-%d")
        # 权重估算（纯本地统计，非平台真实权重）
        self.sent_total = 0
        self.sent_ok = 0
        self.freq_hits = 0                    # 被频控次数
        # 2026-09-07 补：权重恢复机制（用户指出"降至 0 后不能永久停用"）
        # freq_hits 带**半衰期**：每过 WEIGHT_RECOVER_HALFLIFE 秒衰减一半，
        # 保证账号被频控后能随时间自动恢复，不会永久判死。
        self.freq_hits_f = 0.0                # 浮点保留（衰减用）
        self.last_freq_at = 0.0               # 上次频控时间戳
        self.last_decay_at = time.time()      # 上次衰减结算时间
        # 冷静期
        self.cooldown_until = 0.0
        self.cooldown_level = 0               # 连续触发次数（用于递增）

    # ---------- 日切 ----------
    def _roll_day(self) -> None:
        today = time.strftime("%Y-%m-%d")
        if today != self._day_stamp:
            self._day_stamp = today
            self._stranger_day.clear()
            self.cooldown_level = 0           # 新的一天重置递增

    # ---------- 权重估算（本地可观测信号，非平台真实权重） ----------
    def _decay_freq(self) -> None:
        """频控计数的时间衰减（半衰期）。调用方需持锁。

        freq_hits_f 每过 WEIGHT_RECOVER_HALFLIFE 秒衰减一半：
          t=0h: 4.0  t=6h: 2.0  t=12h: 1.0  t=18h: 0.5 ...
        低于 0.05 视为完全恢复 → 归零（避免永远留个尾巴）。
        """
        if WEIGHT_RECOVER_HALFLIFE <= 0 or self.freq_hits_f <= 0:
            return
        now = time.time()
        elapsed = now - self.last_decay_at
        if elapsed <= 0:
            return
        halves = elapsed / WEIGHT_RECOVER_HALFLIFE
        self.freq_hits_f *= (0.5 ** halves)
        self.last_decay_at = now
        if self.freq_hits_f < 0.05:
            self.freq_hits_f = 0.0
            self.freq_hits = 0
            self.cooldown_level = 0
            logger.info(
                f"[dm-dispatch][{self.key}] 频控记录已随时间衰减归零，"
                f"权重恢复（刑满释放）")
        else:
            self.freq_hits = int(self.freq_hits_f + 0.5)

    def _weight_unlocked(self) -> float:
        """权重计算（**调用方必须已持锁**，内部不再加锁，避免死锁）。"""
        self._roll_day()
        self._decay_freq()
        if self.sent_total < 5:
            return 1.0
        rate = self.sent_ok / max(self.sent_total, 1)
        penalty = min(self.freq_hits_f * 0.15, 0.6)
        return max(0.3, min(1.0, rate - penalty))

    def weight(self) -> float:
        """本地估算权重 [0.3, 1.0]（线程安全版，对外用）。"""
        with self.lock:
            return self._weight_unlocked()

    def effective_stranger_limit(self, base: int) -> int:
        """按权重缩放陌生人首发限额（权重低 → 额度更低、更保守）。

        线程安全：内部取锁调 _weight_unlocked，不会死锁。
        """
        with self.lock:
            return max(1, int(base * self._weight_unlocked()))

    # ---------- 陌生人首发限流 ----------
    def can_stranger_first(self) -> tuple:
        """是否允许再发一条陌生人首发。返回 (ok, reason)。"""
        now = time.time()
        with self.lock:
            self._roll_day()
            if now < self.cooldown_until:
                left = int(self.cooldown_until - now)
                return False, f"冷静期内（剩余 {left}s），暂停陌生人首发"
            # 分钟窗
            self._stranger_minute = [t for t in self._stranger_minute
                                     if now - t < 60]
            limit_min = max(1, int(STRANGER_PER_MINUTE * self._weight_unlocked()))
            if len(self._stranger_minute) >= limit_min:
                return False, (f"陌生人首发已达分钟上限 "
                               f"{limit_min} 次（权重 {self._weight_unlocked():.2f}）")
            # 当日窗
            limit_day = max(1, int(STRANGER_PER_DAY * self._weight_unlocked()))
            if len(self._stranger_day) >= limit_day:
                return False, (f"陌生人首发已达当日上限 "
                               f"{limit_day} 次（权重 {self._weight_unlocked():.2f}）")
            return True, ""

    def note_stranger_sent(self) -> None:
        """记一次陌生人首发（入池时**预占**）。"""
        now = time.time()
        with self.lock:
            self._stranger_minute.append(now)
            self._stranger_day.append(now)

    def refund_stranger(self) -> None:
        """归还一次预占额度（发送失败时调用：失败不该占额度）。

        只退最近一条，且仅从分钟窗退（当日窗同样退一个），避免误退
        其他任务的额度。额度为 0 时安全空转。
        """
        with self.lock:
            if self._stranger_minute:
                self._stranger_minute.pop()
            if self._stranger_day:
                self._stranger_day.pop()

    # ---------- 回执处理 ----------
    def on_result(self, ok: bool, detail: str = "") -> None:
        """根据发送回执更新统计；遇到"频繁"强制进入冷静期。"""
        with self.lock:
            self.sent_total += 1
            if ok:
                self.sent_ok += 1
                # 成功后缓解递增（连续成功可降冷静等级）
                if self.cooldown_level > 0:
                    self.cooldown_level -= 1
                return
        d = (detail or "").upper()
        # 命中"频繁/频控"类回执（与 douyin_api._classify_send_fail 对齐）
        if any(k in d for k in ("FREQUENT", "RATE", "TOO_", "LIMIT",
                                "SPAM", "FREQUENCY", "频繁", "频控")):
            with self.lock:
                self.freq_hits_f += 1.0       # 浮点计数（供半衰期衰减）
                self.freq_hits = int(self.freq_hits_f + 0.5)
                self.last_freq_at = time.time()
                self.cooldown_level = min(self.cooldown_level + 1, 6)
                # 冷静期随连续触发翻倍：10min -> 20 -> 40 ... 上限 COOLDOWN_MAX
                dur = min(COOLDOWN_ON_FREQUENT * (2 ** (self.cooldown_level - 1)),
                          COOLDOWN_MAX)
                self.cooldown_until = time.time() + dur
                logger.warning(
                    f"[dm-dispatch][{self.key}] 回执命中频控 → 权重降至 "
                    f"{self._weight_unlocked():.2f}，强制冷静 {dur / 60:.0f} 分钟"
                    f"（第 {self.cooldown_level} 次，半衰期 "
                    f"{WEIGHT_RECOVER_HALFLIFE / 3600:.0f}h 后自动恢复）")

    def snapshot(self) -> dict:
        now = time.time()
        with self.lock:
            self._roll_day()
            self._decay_freq()
            # 距"完全恢复"还需多久（按当前 freq_hits_f 与半衰期估算）
            if self.freq_hits_f > 0 and WEIGHT_RECOVER_HALFLIFE > 0:
                import math
                halves = math.log(max(self.freq_hits_f, 1e-9) / 0.05, 2)
                recover_in = int(max(0.0, halves) * WEIGHT_RECOVER_HALFLIFE)
            else:
                recover_in = 0
            return {
                "key": self.key,
                # 注意：已持锁，必须用 _weight_unlocked（再调 weight() 会死锁）
                "weight": round(self._weight_unlocked(), 2),
                "weight_floor": 0.3,          # 硬保底，永不归零
                "sent": f"{self.sent_ok}/{self.sent_total}",
                "freq_hits": round(self.freq_hits_f, 2),
                "recover_in_sec": recover_in,  # 权重完全恢复预计剩余秒
                "stranger_last_min": len([t for t in self._stranger_minute
                                          if now - t < 60]),
                "stranger_today": len(self._stranger_day),
                # 已持锁，直接算（不能调 effective_stranger_limit，会二次取锁死锁）
                "limit_per_min": max(1, int(STRANGER_PER_MINUTE * self._weight_unlocked())),
                "limit_per_day": max(1, int(STRANGER_PER_DAY * self._weight_unlocked())),
                "cooldown_left_sec": max(0, int(self.cooldown_until - now)),
            }


# ===========================================================================
# 2026-09-07：UID 沉淀池（用户要求）
# ---------------------------------------------------------------------------
# 直播监听/视频采集时，**同一 UID 会反复发弹幕**（同一个人刷很多条）。
# 若不做沉淀，会对同一个人重复发送私信 = 骚扰 + 风控。
#
# 沉淀池职责：
#   - 同一 (account, peer_uid) **只保留一次有效记录**，后续弹幕直接丢弃；
#   - 跨来源共享（直播监听 + 视频采集 + 中控台采集走同一张表）；
#   - **DB 持久化**（dm_uid_sink），进程重启不丢——避免今天发了，
#     明天重启后又对同一批人重发一遍；
#   - 冷却期：已发送的 UID 在 COOLDOWN 内不再重复发送（默认 7 天）。
# ===========================================================================
UID_SINK_COOLDOWN = float(
    os.environ.get("DY_UID_SINK_COOLDOWN", str(7 * 86400)))   # 默认 7 天
# 冷却期内是否直接丢弃（True=丢弃；False=放行但记录次数，便于排查）
UID_SINK_STRICT = os.environ.get("DY_UID_SINK_STRICT", "1") not in (
    "0", "false", "False")


class UidSink:
    """UID 沉淀池：同 UID 多次弹幕 → 只保留一次有效发送目标。

    与 core/dispatch 内的 `_already_seen` 的区别：
      - dispatch 那份是**进程内、单次任务**的内存集合，重启/新任务即失效；
      - 本类是**会话池级别的持久化沉淀**，跨任务、跨来源、跨进程重启有效。
    两者叠加：dispatch 拦瞬时重复（快），本池拦跨任务重复（准）。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # 内存一级缓存（避免每条弹幕都查库）：{(account,uid): ts}
        self._cache: Dict[Tuple[str, str], float] = {}
        self._loaded = False

    def _ensure_loaded(self, account: str) -> None:
        """首次使用时把该账号已发送过的 UID 载入内存缓存。"""
        with self._lock:
            if self._loaded:
                return
            try:
                from database import get_db
                conn = get_db()
                rows = conn.execute(
                    "SELECT account, peer_uid, sent_ts FROM dm_uid_sink "
                    "WHERE sent_ts IS NOT NULL").fetchall()
                for r in rows:
                    self._cache[(r["account"], str(r["peer_uid"]))] = \
                        float(r["sent_ts"] or 0)
                self._loaded = True
            except Exception as e:
                logger.debug(f"[uid-sink] 载入失败（降级为纯内存去重）: {e}")
                self._loaded = True

    def should_send(self, account: str, peer_uid: str) -> tuple:
        """该 UID 是否应发送。返回 (ok, reason)。

        判定：
          1. 冷却期内已发送过 → 拒绝（默认 7 天）；
          2. 否则放行（并沉淀"已见过"）。
        """
        if not account or not peer_uid:
            return False, "账号或 UID 为空"
        key = (account, str(peer_uid).strip())
        self._ensure_loaded(account)
        now = time.time()
        with self._lock:
            last = self._cache.get(key)
        if last and (now - last) < UID_SINK_COOLDOWN:
            left = int(UID_SINK_COOLDOWN - (now - last))
            if UID_SINK_STRICT:
                return False, (f"UID 已发送过，冷却期内（剩余 {left // 86400} 天）")
            return True, f"（非严格模式放行，{left // 86400} 天前发过）"
        return True, ""

    def mark_sent(self, account: str, peer_uid: str, nickname: str = "",
                  source: str = "") -> None:
        """标记该 UID 已发送（写入 DB + 内存）。"""
        key = (account, str(peer_uid).strip())
        now = time.time()
        with self._lock:
            self._cache[key] = now
        try:
            from database import get_db
            conn = get_db()
            conn.execute(
                "INSERT INTO dm_uid_sink("
                "account,peer_uid,nickname,source,first_seen_ts,"
                "sent_ts,send_count) VALUES(?,?,?,?,?,?,1) "
                "ON CONFLICT(account,peer_uid) DO UPDATE SET "
                "sent_ts=excluded.sent_ts, "
                "send_count=dm_uid_sink.send_count+1, "
                "nickname=COALESCE(excluded.nickname, dm_uid_sink.nickname)",
                (account, str(peer_uid).strip(), nickname or "", source or "",
                 now, now))
            conn.commit()
        except Exception as e:
            logger.warning(f"[uid-sink] 落库失败（仅内存生效）: {e}")

    def mark_seen(self, account: str, peer_uid: str, nickname: str = "",
                  source: str = "") -> None:
        """仅沉淀（未发送）：同 UID 多次弹幕只记一次，不占用发送额度。"""
        try:
            from database import get_db
            conn = get_db()
            conn.execute(
                "INSERT OR IGNORE INTO dm_uid_sink("
                "account,peer_uid,nickname,source,first_seen_ts) "
                "VALUES(?,?,?,?,?)",
                (account, str(peer_uid).strip(), nickname or "", source or "",
                 time.time()))
            conn.commit()
        except Exception:
            pass

    def stats(self, account: str = "") -> dict:
        try:
            from database import get_db
            conn = get_db()
            if account:
                row = conn.execute(
                    "SELECT COUNT(*) n, SUM(CASE WHEN sent_ts IS NOT NULL "
                    "THEN 1 ELSE 0 END) sent FROM dm_uid_sink WHERE account=?",
                    (account,)).fetchone()
                return {"total": row["n"] or 0, "sent": row["sent"] or 0}
            row = conn.execute(
                "SELECT COUNT(*) n, SUM(CASE WHEN sent_ts IS NOT NULL "
                "THEN 1 ELSE 0 END) sent FROM dm_uid_sink").fetchone()
            return {"total": row["n"] or 0, "sent": row["sent"] or 0}
        except Exception:
            return {"total": len(self._cache), "sent": len(self._cache)}


# ---------------------------------------------------------------------------
# 会话整理池
# ---------------------------------------------------------------------------
class ConvPool:
    """会话整理池：归一化 / 去重 / 校验后再进入调度。

    线程安全。所有发送源必须先经 `prepare()` 整理，才能入队。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # 归一化结果缓存：{(account, conv_id): (ts, peer_uid)}
        self._resolved: Dict[Tuple[str, str], Tuple[float, str]] = {}
        self._resolved_ttl = 60.0
        # 去重：{(account, conv_id, text_md5): ts}
        self._recent: Dict[Tuple[str, str, str], float] = {}

    # ---------- 核心：解析真实对端 uid ----------
    @staticmethod
    def _my_uid_of(account: str) -> str:
        """取本账号 uid（走统一探活调度器，零额外网络）。

        2026-09-07 真机修正：**探活 uid 与私信会话 uid 可能不是同一个**
        （实测「四川工伤张老师」探活=4175297014664416，但 278 条会话里
        本账号 uid 恒为 3887506227210423 —— 两套 uid 体系，也是日志里
        持续报「uid 漂移」的根因）。因此：
          1. 先用会话池统计推断（**权威**：本账号 uid 必然出现在该账号
             的每一个 conv_id 中，出现次数 ≈ 会话数）；
          2. 统计不可用时才回退探活 uid。
        这样"排除自身"才不会误判——否则会把真实对端当成本账号排除掉。
        """
        try:
            from database import get_db
            conn = get_db()
            rows = conn.execute(
                "SELECT conv_id FROM dm_conversations WHERE account=?",
                (account,)).fetchall()
            if rows:
                cnt: Dict[str, int] = {}
                for (cid,) in rows:
                    p = (cid or "").split(":")
                    if len(p) >= 4:
                        cnt[p[2]] = cnt.get(p[2], 0) + 1
                        cnt[p[3]] = cnt.get(p[3], 0) + 1
                n = len(rows)
                # 本账号 uid 出现在【每一个】conv_id 中
                for uid, c in cnt.items():
                    if c >= n * 0.9:
                        return uid
        except Exception:
            pass
        try:
            from services.uid_probe import get_uid
            return str(get_uid(account) or "")
        except Exception:
            return ""

    @staticmethod
    def _peer_from_conv(conv_id: str, my_uid: str) -> Optional[str]:
        """从 conv_id 0:1:uidA:uidB 解析对端 uid（排除自身）。"""
        parts = (conv_id or "").split(":")
        if len(parts) < 4:
            return None
        a, b = parts[2], parts[3]
        if not a or not b or a == b:
            return None          # 自发自收的系统会话，不是真实对端
        if my_uid:
            if a == my_uid:
                return b
            if b == my_uid:
                return a
            return None          # 两边都不是自己 -> 数据异常，不猜
        return b                 # 无 my_uid 兜底（弱保证）

    def resolve(self, account: str, conv_id: str) -> Optional[str]:
        """归一化：返回该会话的真实对端 uid；无法确定返回 None。"""
        key = (account, conv_id or "")
        now = time.time()
        with self._lock:
            hit = self._resolved.get(key)
            if hit and (now - hit[0]) < self._resolved_ttl:
                return hit[1] or None
        my_uid = self._my_uid_of(account)
        peer = self._peer_from_conv(conv_id, my_uid)
        # my_uid 缺失时兜底：从 DB 里该会话已记录的 peer_id 取（可能仍被污染，
        # 但至少不会返回"自己"——下面校验会拦）
        if not peer and not my_uid:
            peer = self._peer_from_db(account, conv_id)
        with self._lock:
            self._resolved[key] = (now, peer or "")
        return peer

    @staticmethod
    def _peer_from_db(account: str, conv_id: str) -> Optional[str]:
        """兜底：从 dm_conversations 读 peer_id（仅当它不是自己的 uid）。"""
        try:
            from database import get_db
            conn = get_db()
            row = conn.execute(
                "SELECT peer_id FROM dm_conversations WHERE account=? AND conv_id=?",
                (account, conv_id)).fetchone()
            if row and row["peer_id"]:
                return str(row["peer_id"])
        except Exception:
            pass
        return None

    def is_duplicate(self, account: str, conv_id: str, text: str) -> bool:
        """同内容在去抖窗口内重复入池 -> True（丢弃）。"""
        import hashlib
        h = hashlib.md5((text or "").encode("utf-8")).hexdigest()
        key = (account, conv_id or "", h)
        now = time.time()
        with self._lock:
            # 顺带清理过期项，防内存无限增长
            for k, ts in list(self._recent.items()):
                if now - ts > DEDUP_WINDOW:
                    self._recent.pop(k, None)
            last = self._recent.get(key)
            if last is not None and (now - last) < DEDUP_WINDOW:
                return True
            self._recent[key] = now
            return False

    def invalidate(self, account: str = "", conv_id: str = "") -> None:
        """会话被 capture 更新后调用，让下次入池重解析。"""
        with self._lock:
            if not account:
                self._resolved.clear()
                return
            for k in list(self._resolved.keys()):
                if k[0] == account and (not conv_id or k[1] == conv_id):
                    self._resolved.pop(k, None)


# ---------------------------------------------------------------------------
# 统一调度器
# ---------------------------------------------------------------------------
class DmDispatcher:
    """per-account 串行 worker + 优先级队列 + 频率闸门。"""

    def __init__(self, pool: Optional[ConvPool] = None) -> None:
        self.pool = pool or ConvPool()
        # 2026-09-07：UID 沉淀池（同 UID 多次弹幕只保留一次）
        self.uid_sink = UidSink()
        self._queues: Dict[str, "queue.PriorityQueue"] = {}
        self._workers: Dict[str, threading.Thread] = {}
        self._tasks: Dict[str, SendTask] = {}      # task_id -> task（查状态用）
        # 2026-09-07：per-account key 配额（账目隔离）
        self._quotas: Dict[str, "AccountQuota"] = {}
        self._reg_lock = threading.Lock()
        self._stop = threading.Event()

    # ---------- 陌生人首发判定（本地，零网络请求） ----------
    @staticmethod
    def _is_stranger_first(account: str, conv_id: str) -> bool:
        """该会话是否「从未往来」=> 陌生人首发。

        判定：**本地 dm_messages 无该会话的任何历史消息**。
        不查抖音接口（零风控），与平台无关，纯本地事实。
        查库失败时保守返回 False（不误判为陌生人而误限流熟会话）。
        """
        if not conv_id:
            return False
        try:
            from database import get_db
            conn = get_db()
            row = conn.execute(
                "SELECT COUNT(*) n FROM dm_messages "
                "WHERE account=? AND conv_id=?",
                (account, conv_id)).fetchone()
            return int(row["n"] if row else 0) == 0
        except Exception:
            return False

    def quota_of(self, account: str) -> "AccountQuota":
        """取（或建）该账号的独立配额。每个 key 一份，互不影响。"""
        with self._reg_lock:
            q = self._quotas.get(account)
            if q is None:
                q = AccountQuota(account)
                self._quotas[account] = q
            return q

    # ---------- 入池 ----------
    def submit(self, account: str, conv_id: str, text: str,
               source: str = "manual", priority: Optional[int] = None,
               image_b64: str = "", filename: str = "") -> SubmitResult:
        """发送源统一入口：整理 -> 校验 -> 入队（异步发送）。"""
        if not account or not (conv_id or image_b64):
            return SubmitResult(False, error="账号或会话缺失")
        if not text and not image_b64:
            return SubmitResult(False, error="发送内容为空")

        # ① 整理：解析真实对端 uid
        peer_uid = self.pool.resolve(account, conv_id)
        if not peer_uid:
            msg = (f"会话整理失败：无法从 conv_id 解析真实对端 uid"
                   f"（conv_id={conv_id}）")
            logger.warning(f"[dm-dispatch] {msg}")
            if POOL_STRICT:
                return SubmitResult(False, error=msg)
            peer_uid = ""     # 非严格模式放行，交给下游兜底

        # ② 校验：绝不允许 peer_uid == 本账号 uid（发给自己事故）
        my_uid = self.pool._my_uid_of(account)
        if my_uid and peer_uid == my_uid:
            msg = f"会话整理拒绝：对端 uid 等于本账号 uid（{peer_uid}），疑似污染"
            logger.error(f"[dm-dispatch] {msg}")
            return SubmitResult(False, error=msg)

        # ②-b 【调试版专用白名单】只允许测试账号之间互发，防误发真人。
        # 生效条件：环境变量 DY_DM_TEST_WHITELIST=1（**仅调试构建设置**）。
        # 正式版不设该变量 → 本段完全不生效，零开销、零行为差异，
        # 且打包脚本不会把它打进正式二进制（见 scripts/build_sidecar.py）。
        # 白名单成员与允许的收发关系在 _TEST_WHITELIST 中维护。
        if TEST_WHITELIST_ON:
            allowed = _TEST_WHITELIST.get(account)
            if allowed is None:
                msg = (f"[测试白名单] 账号「{account}」不在测试白名单内，"
                       f"拒绝发送（调试版只允许多测试账号互发）")
                logger.error(f"[dm-dispatch] {msg}")
                return SubmitResult(False, error=msg)
            if allowed and peer_uid not in allowed:
                msg = (f"[测试白名单] 账号「{account}」仅允许发给 "
                       f"{sorted(allowed)}，本次目标 {peer_uid} 被拒绝")
                logger.error(f"[dm-dispatch] {msg}")
                return SubmitResult(False, error=msg)
            logger.info(f"[dm-dispatch] [测试白名单] 放行：{account} -> {peer_uid}")

        # ③ 去重
        if text and self.pool.is_duplicate(account, conv_id, text):
            logger.debug(f"[dm-dispatch] 重复消息已丢弃: {account}/{conv_id}")
            return SubmitResult(False, error="duplicate")

        # ③-b 【2026-09-07】陌生人首发判定 + 限流（per-account key）
        # 判定依据（**零额外网络请求**）：本地 dm_messages 中该会话无历史
        # 消息 => 从未往来 => 陌生人首发。直播监听的待发送绝大多数属此类，
        # 抖音对其限制最严（每分钟 ≤2、每天 ≤30，且按账号权重缩放）。
        is_stranger = self._is_stranger_first(account, conv_id)
        if is_stranger:
            q = self.quota_of(account)
            ok_q, reason = q.can_stranger_first()
            if not ok_q:
                logger.warning(
                    f"[dm-dispatch][{account}] 陌生人首发被限流: {reason}")
                return SubmitResult(False, error=f"陌生人首发达限: {reason}")
            q.note_stranger_sent()      # 预占额度（失败由 _send_one 归还）

        # ④ 入队
        prio = priority if priority is not None else _PRIO_BY_SOURCE.get(
            source, PRIO_BATCH)
        q = self._queue_of(account)
        if q.qsize() >= QUEUE_MAX:
            return SubmitResult(False, error=f"队列已满（{QUEUE_MAX}），请稍后重试")
        task = SendTask(task_id=uuid.uuid4().hex[:12], account=account,
                        conv_id=conv_id, peer_uid=peer_uid, text=text,
                        source=source, priority=prio,
                        image_b64=image_b64, filename=filename,
                        account_key=account, is_stranger_first=is_stranger)
        with self._reg_lock:
            self._tasks[task.task_id] = task
        q.put(task)
        self._ensure_worker(account)
        logger.info(
            f"[dm-dispatch] 入池 task={task.task_id} 账号={account} "
            f"会话={conv_id[:12]}… peer={peer_uid} 源={source} "
            f"优先级={prio} 队列={q.qsize()}")
        return SubmitResult(True, task_id=task.task_id, queue_size=q.qsize())

    def submit_by_uid(self, account: str, peer_uid: str, text: str,
                      source: str = "dispatch",
                      priority: Optional[int] = None) -> SubmitResult:
        """按对端 uid 直发（**视频采集 / 直播监听专用**）。

        这两个来源的目标**绝大多数是陌生人首发**（从未往来，没有 conv_id），
        所以不能走需要 conv_id 的 `submit()`。此处：
          - conv_id 置空，peer_uid 直接给定（已是真实对端）；
          - **强制标记 is_stranger_first=True**（用户确认：采集/监听基本都是首发），
            从而进入 2/分钟、30/天 的严格限流与降权/冷静体系；
          - 同样做 key 校验（peer_uid 不得等于本账号 uid）。
        """
        if not account or not peer_uid or not text:
            return SubmitResult(False, error="账号/对端 uid/内容缺失")
        peer_uid = str(peer_uid).strip()

        # ① 绝不能发给自己
        my_uid = self.pool._my_uid_of(account)
        if my_uid and peer_uid == my_uid:
            return SubmitResult(False, error=f"拒绝：对端 uid 等于本账号（{my_uid}）")

        # ② 测试白名单（调试版专属，见 submit 内说明）
        if TEST_WHITELIST_ON:
            allowed = _TEST_WHITELIST.get(account)
            if allowed is None:
                msg = f"[测试白名单] 账号「{account}」不在测试白名单，拒绝发送"
                logger.error(f"[dm-dispatch] {msg}")
                return SubmitResult(False, error=msg)
            if allowed and peer_uid not in allowed:
                msg = (f"[测试白名单] 账号「{account}」仅允许发给 "
                       f"{sorted(allowed)}，目标 {peer_uid} 被拒绝")
                logger.error(f"[dm-dispatch] {msg}")
                return SubmitResult(False, error=msg)
            logger.info(f"[dm-dispatch] [测试白名单] 放行(uid直发)："
                        f"{account} -> {peer_uid}")

        # ②-b UID 沉淀池：同一 UID 多次弹幕只保留一次有效目标。
        # 直播/采集场景同一个人会刷很多条弹幕，若无此层会对同一人重复发
        # 私信（骚扰 + 风控）。冷却期默认 7 天，跨任务/跨重启有效。
        ok_sink, sink_reason = self.uid_sink.should_send(account, peer_uid)
        if not ok_sink:
            logger.info(
                f"[dm-dispatch][{account}] UID 沉淀池拦截（同 UID 已发过）: "
                f"{peer_uid} - {sink_reason}")
            return SubmitResult(False, error=f"UID 沉淀池: {sink_reason}")

        # ③ 陌生人首发限流（采集/监听目标默认按陌生人首发计）
        # **预占额度**：入池即记账，不等发送成功才记。否则"入池→发送"之间
        # 的窗口期可以无限入池（检查通过但都还没发送，额度始终为 0），
        # 限流形同虚设。发送失败时由 _send_one 调 quota.refund_stranger() 归还。
        q = self.quota_of(account)
        ok_q, reason = q.can_stranger_first()
        if not ok_q:
            logger.warning(
                f"[dm-dispatch][{account}] 陌生人首发被限流(uid直发): {reason}")
            return SubmitResult(False, error=f"陌生人首发达限: {reason}")
        q.note_stranger_sent()          # 预占

        # ④ 入队（conv_id 留空，发送时按 peer_uid 走 /send_by_uid）
        prio = priority if priority is not None else _PRIO_BY_SOURCE.get(
            source, PRIO_BATCH)
        q_ = self._queue_of(account)
        if q_.qsize() >= QUEUE_MAX:
            return SubmitResult(False, error=f"队列已满（{QUEUE_MAX}）")
        task = SendTask(task_id=uuid.uuid4().hex[:12], account=account,
                        conv_id="", peer_uid=peer_uid, text=text,
                        source=source, priority=prio,
                        account_key=account, is_stranger_first=True)
        with self._reg_lock:
            self._tasks[task.task_id] = task
        q_.put(task)
        self._ensure_worker(account)
        logger.info(
            f"[dm-dispatch] 入池(uid直发) task={task.task_id} 账号={account} "
            f"peer={peer_uid} 源={source} 队列={q_.qsize()}")
        return SubmitResult(True, task_id=task.task_id, queue_size=q_.qsize())

    # ---------- 队列 / worker ----------
    def _queue_of(self, account: str) -> "queue.PriorityQueue":
        with self._reg_lock:
            q = self._queues.get(account)
            if q is None:
                q = queue.PriorityQueue()
                self._queues[account] = q
            return q

    def _ensure_worker(self, account: str) -> None:
        with self._reg_lock:
            w = self._workers.get(account)
            if w and w.is_alive():
                return
            w = threading.Thread(target=self._worker_loop, args=(account,),
                                 daemon=True, name=f"dm-dispatch-{account[:8]}")
            self._workers[account] = w
            w.start()

    def _worker_loop(self, account: str) -> None:
        """per-account 串行出队：保证同账号发送顺序 = 优先级 + 入池顺序。"""
        q = self._queue_of(account)
        logger.info(f"[dm-dispatch] worker 启动：{account}")
        while not self._stop.is_set():
            try:
                task: SendTask = q.get(timeout=1.0)
            except Exception:
                # 空闲超时：队列空且已持续空闲 -> 退出（有新任务时会重建）
                if q.empty():
                    with self._reg_lock:
                        self._workers.pop(account, None)
                    logger.debug(f"[dm-dispatch] worker 空闲退出：{account}")
                    return
                continue
            try:
                self._send_one(task)
            except Exception as e:
                task.status = "failed"
                task.error = str(e)
                logger.error(f"[dm-dispatch] 发送异常 task={task.task_id}: {e}")
            finally:
                q.task_done()
        logger.info(f"[dm-dispatch] worker 退出：{account}")

    def _send_one(self, task: SendTask) -> None:
        """实际发送：转发到 recv_daemon（复用既有双通道 + 频率闸门）。

        2026-09-07 新增：
          - **发送前核对 account_key**（防止任务被错投到别的账号队列）；
          - 发送后把**回执结果**交给 AccountQuota 统计（命中"频繁"则
            该账号陌生人首发进入强制冷静期 + 降权）。
        """
        # ★ 发送前 key 核对：任务所属账号必须与本 worker 的账号一致
        if task.account_key and task.account_key != task.account:
            task.status = "failed"
            task.error = (f"key 核对失败：task.account_key="
                          f"{task.account_key} != account={task.account}")
            logger.error(f"[dm-dispatch] {task.error}，已拒绝发送")
            # 被拒绝=没发出去 → 归还预占额度
            if task.is_stranger_first:
                self.quota_of(task.account).refund_stranger()
            return
        task.status = "sending"
        quota = self.quota_of(task.account)
        try:
            import requests
            from auto_dm import accounts as acct_core
            port = acct_core.recv_daemon_port(task.account)
            if task.conv_id:
                # 有会话 → 走 /send（conv_id 定位对端）
                url = f"http://127.0.0.1:{port}/send"
                payload = {"account": task.account, "conv_id": task.conv_id,
                           "text": task.text}
            else:
                # 无会话（视频采集/直播监听的 uid 直发）→ 走 /send_by_uid
                url = f"http://127.0.0.1:{port}/send_by_uid"
                payload = {"account": task.account, "peer_uid": int(task.peer_uid),
                           "text": task.text}
            r = requests.post(url, json=payload, timeout=60)
            data = r.json() if r.status_code == 200 else {}
            ok = bool(data.get("ok"))
            if ok:
                task.status = "done"
                # 额度已在入池时预占（note_stranger_sent），成功不重复记
                # uid 直发（采集/监听）成功 → 写入沉淀池，防止日后重复打扰
                if task.is_stranger_first and not task.conv_id:
                    self.uid_sink.mark_sent(task.account, task.peer_uid,
                                            source=task.source)
                logger.info(f"[dm-dispatch] 已发送 task={task.task_id} "
                            f"账号={task.account} 会话={(task.conv_id or task.peer_uid)[:12]}…"
                            f"{' [陌生人首发]' if task.is_stranger_first else ''}")
            else:
                task.status = "failed"
                task.error = data.get("error") or data.get("msg") or f"HTTP{r.status_code}"
                # 发送失败 → 归还预占的首发额度（失败的发送不该占额度）
                if task.is_stranger_first:
                    quota.refund_stranger()
                logger.warning(f"[dm-dispatch] 发送失败 task={task.task_id}: "
                               f"{task.error}")
            # 回执交给配额统计（命中频控 -> 降权 + 冷静期）
            quota.on_result(ok, task.error)
        except Exception as e:
            task.status = "failed"
            task.error = str(e)
            if task.is_stranger_first:
                quota.refund_stranger()     # 异常=没发出去，归还额度
            quota.on_result(False, str(e))
            raise

    # ---------- 观测 ----------
    def stats(self) -> dict:
        with self._reg_lock:
            return {
                "queues": {a: q.qsize() for a, q in self._queues.items()},
                "workers": {a: bool(w and w.is_alive())
                            for a, w in self._workers.items()},
                # 2026-09-07：各账号 key 的配额/权重/冷静期快照
                "quotas": {k: v.snapshot() for k, v in self._quotas.items()},
                "resolved_cache": len(self.pool._resolved),
                "queue_max": QUEUE_MAX,
                "stranger_limit": {"per_minute": STRANGER_PER_MINUTE,
                                   "per_day": STRANGER_PER_DAY},
            }

    def task_status(self, task_id: str) -> Optional[dict]:
        with self._reg_lock:
            t = self._tasks.get(task_id)
        if not t:
            return None
        return {"task_id": t.task_id, "account": t.account,
                "conv_id": t.conv_id, "peer_uid": t.peer_uid,
                "source": t.source, "status": t.status, "error": t.error}

    def shutdown(self) -> None:
        self._stop.set()


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------
_dispatcher: Optional[DmDispatcher] = None
_disp_lock = threading.Lock()


def get_dispatcher() -> DmDispatcher:
    global _dispatcher
    with _disp_lock:
        if _dispatcher is None:
            _dispatcher = DmDispatcher()
        return _dispatcher


def submit(account: str, conv_id: str, text: str, source: str = "manual",
           priority: Optional[int] = None, image_b64: str = "",
           filename: str = "") -> SubmitResult:
    """模块级便捷入口（推荐发送源用这个）。"""
    return get_dispatcher().submit(
        account=account, conv_id=conv_id, text=text, source=source,
        priority=priority, image_b64=image_b64, filename=filename)


def invalidate_conv(account: str = "", conv_id: str = "") -> None:
    """会话被更新后调用（让整理池重解析）。"""
    get_dispatcher().pool.invalidate(account, conv_id)


def stop_dispatcher() -> None:
    """进程退出时调用。"""
    global _dispatcher
    with _disp_lock:
        if _dispatcher:
            _dispatcher.shutdown()
            _dispatcher = None


# ===========================================================================
# 【打包注入区 —— 调试版专用，勿手动编辑】
# ---------------------------------------------------------------------------
# scripts/build_sidecar.py 在 **调试构建** 时会把下面两行替换为真实内容：
#
#   _TEST_WHITELIST = {"尚进工伤小助理": {"<四川工伤张老师 uid>"},
#                      "四川工伤张老师": {"<尚进工伤小助理 uid>"}}
#   TEST_WHITELIST_ON = True
#
# 正式构建**不注入** → 保持空壳，白名单逻辑永不执行。
# 标记行（打包脚本据此定位替换点，勿删勿改）：
# ---DM_TEST_WHITELIST_INJECT_START---
# ---DM_TEST_WHITELIST_INJECT_END---
# ===========================================================================
