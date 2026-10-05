# coding=utf-8
"""私信接收守护进程（重构版）

迁移自 DY_Spider_base/auto_dm/recv_daemon.py。
关键变化：
- http.server → FastAPI（路由更清晰）
- Tauri sidecar 模式：由 Rust SidecarManager 管理生命周期
- 业务逻辑（Conversation / AccountInbox / RecvChannel）完整保留

运行方式（Tauri sidecar）：
    flowcap-recv-daemon --accounts A,B --port P

功能：
  - 每账号一个 RecvChannel 线程，监听 frontier-im WS 长连接
  - 收到私信存入 AccountInbox（持久化到 dm_history.json）
  - 暴露 /status /conversations /conversation /send /quit HTTP 接口
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import sys
import threading
import time
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException
from loguru import logger
from pydantic import BaseModel, Field

# ════════════════════════════════════════════════════════════════════════════
# 🔴 2026-09-23【模块身份归一 —— 防「同进程双份模块」】（审计 P0-4）
#
# 本文件被 PyInstaller 以 **`__main__`** 身份启动
# （`build/flowcap-recv-daemon/*.spec` → Analysis(['…/daemon/recv_daemon.py'])），
# 同时又被**按包名 import**：
#     · `test_send_gate_config.py:26`        `import daemon.recv_daemon as rd`
#     · `daemon/verify_handledispatch.py:111` `import daemon.recv_daemon as rd`
# ⇒ 同一份源码在同一进程内可被加载两次，`sys.modules` 两个键并存：
#     ① `sys.modules["__main__"]`              ← main() 往里写 accounts/port 的那份
#     ② `sys.modules["daemon.recv_daemon"]`    ← 反向 import 触发的那份
# 模块级可变状态（`_state` / `_send_gate_last` / `_last_send_at` …）会**各自分叉**：
# 入口侧写入的账号/端口，反向着读到的是**空** `_state`（账户 `[]`、端口 `0`）。
#
# 定式（与 `daemon/browser_daemon.py:78`、`main.py:74` 完全同源，见 2026-09-23
# 阻断级事故 REG-01 / 提交 `2ce728a`）：凡「以 __main__ 运行、又被同进程按包名
# import」的入口，都必须在**任何反向 import 发生之前**把自身登记为规范模块名。
# 先到先得，故用 `setdefault`：
#   · 以包名正常 import（`import daemon.recv_daemon`）→ 已是自己，不动它；
#   · 以 __main__ 启动 → 补上规范名，消除第二份实例。
# 行为探针与负控见 `backend/test_entry_module_identity_guard.py`。
# ════════════════════════════════════════════════════════════════════════════
sys.modules.setdefault("daemon.recv_daemon", sys.modules[__name__])

# 2026-09-06 全局治理（D：系统死代理隔离，同 main.py）：
# 独立 exe 进程同样被 Windows 注册表系统代理毒害（requests 继承
# getproxies_registry）。本进程 requests 全链路不用代理（DY_PROXY 只进浏览器），
# NO_PROXY=* 禁用环境/注册表代理探测——仅本进程，不动系统设置。
os.environ.setdefault("NO_PROXY", "*")
os.environ.setdefault("no_proxy", "*")

# 无控制台模式下 sys.stdout/stderr 可能为 None
if sys.stdout is not None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception as _e_ver1:
        logger.warning(f"[recv-daemon] 版本探测第1步失败（stdout reconfigure）: {_e_ver1}")
if sys.stderr is not None:
    try:
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception as _e_ver2:
        logger.warning(f"[recv-daemon] 版本探测第2步失败（stderr reconfigure）: {_e_ver2}")

from vbrowser import app_root

# 2026-09-15 骨架昵称即时富化：WS 建立会话骨架后，延迟多少秒再批量拉 IndexedDB
# 回填昵称。节流目的：WS 同步帧高频，若每个新会话都触发一次浏览器 IDB 读取
# （数十秒级 evaluate）会拖垮收消息链路；攒一批一起做，一次读取搞定全部。
# 用户开销感知：骨架出现后至多 IDB_ENRICH_DELAY 秒自动变成真昵称。
IDB_ENRICH_DELAY = float(os.environ.get("DY_IDB_ENRICH_DELAY", "8") or 8)
# BCC 离线时的退避重试间隔（BCC 常晚于 recv_daemon 启动，实测数十秒）
IDB_ENRICH_RETRY = float(os.environ.get("DY_IDB_ENRICH_RETRY", "30") or 30)
# 单次富化的重试上限：超过则交还 capture_all 兜底，避免无限退避刷日志
IDB_ENRICH_MAX_TRIES = int(os.environ.get("DY_IDB_ENRICH_MAX_TRIES", "10") or 10)

_ROOT = app_root()

# 日志同步输出到 stderr（enqueue=True 避免 Windows GBK 控制台中文编码失败中断主线程），
# 这样 Tauri Rust 侧能捕获到守护进程的日志，也会经由 backend 的日志桥接展示到前端「运行日志」。
# 错误码日志补丁：loguru 会把第一个位置参数当格式模板，导致
# logger.warning(f"[BCC-006] " + "描述") 的描述被丢弃（运行日志只剩代码）。
# 此处安装兼容层，让「码 + 描述」正常输出（一处生效，覆盖全项目 345 处调用）。
try:
    from utils.code_logger import install_code_logger_patch as _inst_code_log
    _inst_code_log()
except Exception as _e_code_log:  # 补丁失败绝不阻塞启动
    import sys as _sys_cl
    print(f"[code_logger] 补丁安装失败（不影响运行）: {_e_code_log}", file=_sys_cl.stderr)

logger.remove()
logger.add(
    sys.stderr,
    level="INFO",
    colorize=False,
    enqueue=True,
    format="{time:HH:mm:ss} | {level: <8} | {message}",
)


def _rt_version() -> str:
    """本守护构建版本（读 exe 同级 version.json），供版本一致性校验。"""
    try:
        from _build_version import BUILD_VERSION as _bv
        if _bv:
            return str(_bv)
    except Exception as _e_ver3:
        logger.warning(f"[recv-daemon] 版本探测第3步失败（_build_version）: {_e_ver3}")
    try:
        import json as _json
        base = os.path.dirname(os.path.abspath(
            sys.executable if getattr(sys, "frozen", False) else __file__))
        for rel in ("version.json", os.path.join("..", "version.json")):
            fp = os.path.normpath(os.path.join(base, rel))
            if os.path.isfile(fp):
                with open(fp, encoding="utf-8") as f:
                    v = (_json.load(f) or {}).get("version")
                if v:
                    return str(v)
    except Exception as _e_ver4:
        logger.warning(f"[recv-daemon] 版本探测第4步失败（version.json）: {_e_ver4}")
    return "unknown"


app = FastAPI(title="recv-daemon")

# 全局状态
_state: dict[str, Any] = {
    "accounts": [],
    "port": 0,
    "inboxes": {},   # name -> AccountInbox
    "channels": {},  # name -> RecvChannel
}


# ----------------------------------------------------------------------------
# 会话与收件箱（迁移自旧版 Conversation / AccountInbox）
# ----------------------------------------------------------------------------
class Conversation:
    """单个会话（conversation_id 唯一），保存收发双方标识与历史消息。"""

    def __init__(self, conv_id: str, peer_id: Any = None, peer_name: str | None = None) -> None:
        self.conv_id = conv_id
        self.peer_id = peer_id
        self.peer_name = peer_name or (peer_id or conv_id)
        self.short_id: Any = None
        self.messages: list[dict] = []
        self.unread = 0
        self.last_ts = 0
        self.avatar: str | None = None

    def add(self, role: str, text: str, msg_type: str = "text",
            extra: dict | None = None, ts: float | None = None) -> None:
        ts = ts or time.time()
        self.messages.append({
            "role": role,
            "text": text,
            "msg_type": msg_type,
            "extra": extra or {},
            "ts": ts,
        })
        self.last_ts = ts
        if role == "them":
            self.unread += 1

    def to_dict(self, limit_messages: int = 0) -> dict:
        msgs = self.messages if not limit_messages else self.messages[-limit_messages:]
        return {
            "conv_id": self.conv_id,
            "peer_id": self.peer_id,
            "peer_name": self.peer_name,
            "short_id": self.short_id,
            "unread": self.unread,
            "last_ts": self.last_ts,
            "messages": msgs,
            "avatar": self.avatar or "",
        }


class AccountInbox:
    """一个账号的收件箱：会话与消息持久化到 SQLite（data/flowcap.db）。

    取代旧的 dm_history.json 方案：JSON 并发写入易损坏、无事务、无分页。
    SQLite WAL 模式允许 recv_daemon 独立进程与 backend 共享同一 db 文件。
    """

    def __init__(self, name: str, history_path: str | None = None) -> None:
        self.name = name
        self.lock = threading.RLock()
        self.convs: dict[str, Conversation] = {}  # 内存缓存（WS 实时消息用）
        self.connected = False
        self.last_error = ""
        self._api_pulled = False  # 标记是否已调 get_message_by_init 拉全量会话
        # my_uid：用于从 conv_id 0:1:uid_a:uid_b 提取对端 UID（WS 消息 sender 常为空/自己）
        self.my_uid = None
        # 2026-09-15 骨架昵称即时富化：WS 建立的会话骨架 peer_name 为空（WS 帧
        # 不带昵称，见 Response.proto GetConversationInfoV2Response），前端只能
        # 回退显示裸 uid。这里登记待富化的 conv_id，由后台线程节流批量拉
        # IndexedDB 回填，用户无需「再点一次更新会话」。
        self._nick_pending: set[str] = set()
        self._nick_lock = threading.Lock()
        self._nick_worker: threading.Thread | None = None
        self._refresh_my_uid()
        self._load_from_db()

    # ---- 骨架昵称即时富化（2026-09-15）------------------------------------
    #
    # 设计契约：
    #   ① WS 是**高频路径**（同步帧每次建连/新消息都来），**绝不可内联调 BCC**
    #      （一次 IDB 读取要起浏览器 evaluate，数十秒级）→ 只登记 conv_id，
    #      由独立后台线程节流批量处理。
    #   ② 昵称唯一来源仍是 **BCC 被动持有的 IndexedDB**（零网络请求，不碰风控）。
    #      BCC 不在线 → 静默跳过，留待下次（capture_all 会兜底），不报错不阻塞。
    #   ③ **绝不覆盖已有昵称**：SQL 限定 peer_name 为空/缺失才写，
    #      capture_all 作为权威写入方的结果优先。
    #
    def _mark_nick_pending(self, conv_id: str) -> None:
        """登记一个待富化昵称的会话骨架（幂等，O(1)）。"""
        if not conv_id:
            return
        with self._nick_lock:
            self._nick_pending.add(conv_id)
            need_start = self._nick_worker is None or not self._nick_worker.is_alive()
            if need_start:
                # 节流：等待 IDB_ENRICH_DELAY 秒再跑，让同一批骨架一起处理，
                # 避免每个新会话都触发一次浏览器读取。
                try:
                    self._nick_worker = threading.Timer(
                        IDB_ENRICH_DELAY, self._enrich_nicknames_once)
                    self._nick_worker.daemon = True
                    self._nick_worker.start()
                except Exception:
                    pass

    def _reschedule_enrich(self, batch: list[str], reason: str) -> None:
        """把待办还回去并安排退避重试（BCC 常晚于 recv_daemon 启动）。

        设计要点：
          - **归还待办**：任何拿不到数据的情形都不能静默清空，否则这些骨架
            永远失去富化机会（与「下次再补」的契约矛盾）。
          - **有上限**：连续失败 IDB_ENRICH_MAX_TRIES 次后停止退避，交由
            capture_all（用户点「更新会话」）兜底，避免无限 Timer 刷日志。
        """
        with self._nick_lock:
            self._nick_pending.update(batch)
            self._nick_tries = getattr(self, "_nick_tries", 0) + 1
            tries = self._nick_tries
        if tries >= IDB_ENRICH_MAX_TRIES:
            logger.info(
                f"[recv][{self.name}] 骨架昵称富化重试 {tries} 次仍{reason}，"
                f"停止自动重试（{len(batch)} 个交给「更新会话」兜底）")
            return
        try:
            with self._nick_lock:
                if (self._nick_worker is None
                        or not self._nick_worker.is_alive()):
                    self._nick_worker = threading.Timer(
                        IDB_ENRICH_RETRY, self._enrich_nicknames_once)
                    self._nick_worker.daemon = True
                    self._nick_worker.start()
        except Exception:
            pass
        logger.debug(
            f"[recv][{self.name}] {reason}，{len(batch)} 个骨架昵称"
            f"待后续富化（{IDB_ENRICH_RETRY}s 后第 {tries} 次重试）")

    def _infer_peer_by_common_uid(self, conv_id: str,
                                  batch: list[str] | None = None) -> str | None:
        """my_uid 未就绪时，从 conv_id 集合推断本号 uid，再取对端。

        2026-09-25 H-25 收敛：统计推断**不再在本处自写**，统一调
        `services.conv_identity`（会话身份解析唯一真相源，该模块设计契约
        明令「全项目只允许一处实现」）。本处原先的 Counter 统计与
        `conv_identity.infer_my_uid_from_conv_ids` 完全同义，属重复实现。

        为什么需要：`_refresh_my_uid()` 依赖凭证加载，recv_daemon 刚启动或
        凭证异常时会返回 None（实测 10 个骨架全都因此解析不出 peer，富化静默
        全跳过）。退化推断保证**不依赖凭证也能工作**。
        """
        try:
            from services.conv_identity import (
                infer_my_uid_from_conv_ids as _infer,
                peer_uid as _peer_uid,
            )
        except Exception:
            return None
        my = _infer(list(batch or []) + [conv_id])
        if not my:
            return None
        return _peer_uid(conv_id, my)

    def _enrich_nicknames_once(self) -> None:
        """批量把待富化骨架的昵称从 IndexedDB 回填（一次浏览器读取搞定一批）。"""
        with self._nick_lock:
            batch = list(self._nick_pending)
            self._nick_pending.clear()
        if not batch:
            return
        # my_uid 未就绪会让 _extract_peer_uid 全部返回 None（实测 10 个骨架
        # 因此静默全跳过）→ 富化前先自愈一次（幂等、仅本地读凭证）。
        if not self.my_uid:
            try:
                self._refresh_my_uid()
            except Exception:
                pass
        # 2026-09-25 H-25：凭证仍不可用时，用「会话池统计推断」补本号 uid
        # （权威来源 = services.conv_identity 契约）。否则下方 _extract_peer_uid
        # 无 my_uid 可传，会退化为弱判据返回 uid_b —— 而实测 uid_b 恰是本号自己，
        # 正是「本号昵称/头像被回填到全部会话」的根因。
        if not self.my_uid:
            try:
                from services.conv_identity import (
                    infer_my_uid_from_conv_ids as _infer_my,
                )
                _mu = _infer_my(batch)
                if _mu:
                    self.my_uid = _mu
                    logger.info(
                        f"[recv][{self.name}] 本号 uid 由会话池统计推断补全: {_mu}")
            except Exception:
                pass
        try:
            # BCC 在线才做；不在线**把待办还回去**（否则本次清空=永久丢失，
            # 与「下次再补」的契约矛盾），由后续 WS 建骨架或定时重试兜底。
            from auto_dm import accounts as _acc
            port = _acc.browser_daemon_port(self.name)
            if not _acc._port_open(port, timeout=0.5):
                self._reschedule_enrich(batch, "BCC 离线")
                return
            import requests as _rq
            r = _rq.post(f"http://127.0.0.1:{port}/userinfo_idb",
                         json={}, timeout=60)
            j = (r.json() or {}) if r.status_code == 200 else {}
            users = j.get("users") or {}
            # 任何「拿不到数据」的情形都归还待办并退避重试，绝不静默丢弃：
            # HTTP 非 200 / ok=False / users 为空（容器刚起、页面未就绪都属此类）
            if not users:
                self._reschedule_enrich(batch, "IDB 暂无数据")
                return
            # 以 uid 为键（value.uid 是数字，与 conv_id 推出的 peer_uid 同体系）
            by_uid: dict[str, dict] = {}
            for _u, _v in users.items():
                _uid = (_v or {}).get("uid") or _u
                if _uid:
                    by_uid[str(_uid)] = _v or {}
            # 2026-09-25 H-25：本号昵称 —— 取 IndexedDB 里本号 uid 的那条。
            # 用于把「已被污染成本号昵称」的行纳入可覆盖集合（否则存量污染
            # 永远逃过这一道自愈，因为旧 WHERE 只认 NULL/''/peer_id/本号 uid）。
            _self_nick = ""
            if self.my_uid:
                _self_nick = str(
                    (by_uid.get(str(self.my_uid)) or {}).get("nickname") or ""
                ).strip()
            conn = self._db()
            done = 0
            for cid in batch:
                try:
                    # 优先用 my_uid 排除自己
                    peer = self._extract_peer_uid(cid)
                    if not peer:
                        # my_uid 可能尚未就绪（_refresh_my_uid 依赖凭证，
                        # WS 刚起时常为 None）→ 退化：本账号所有 conv_id 的
                        # 共同项即本号 uid（与 capture_all 的 _auth_uid 同法）。
                        # 取待办集合里出现频次最高且达 90% 的那个 uid。
                        peer = self._infer_peer_by_common_uid(cid, batch)
                    # 2026-09-25 H-25 自证门禁：对端 uid 必须存在且 ≠ 本号 uid。
                    # 这是「昵称/头像绝不用本号身份冒充对端」的最后一道防线
                    # （根因即 uid_b 恰为本号自己 → 回填成本账号）。命中即跳过。
                    if not peer or (self.my_uid and str(peer) == str(self.my_uid)):
                        continue
                    info = by_uid.get(str(peer))
                    if not info or not info.get("nickname"):
                        continue
                    # 2026-09-25 H-25 加固：覆盖集合 = 占位值（NULL/''/peer_id）
                    # + 本号 uid + **本账号昵称**（污染态，可自愈）。真昵称绝不覆盖。
                    cur = conn.execute(
                        "UPDATE dm_conversations SET peer_name=?, avatar=? "
                        "WHERE account=? AND conv_id=? "
                        "AND (peer_name IS NULL OR peer_name='' "
                        "     OR peer_name=peer_id OR peer_name=? "
                        "     OR (?<>'' AND peer_name=?))",
                        (info["nickname"], info.get("avatar") or None,
                         self.name, cid, self.my_uid,
                         _self_nick, _self_nick),
                    )
                    done += (cur.rowcount or 0)
                    # 同步内存缓存，前端下次轮询即可见（不必等库）
                    c = self.convs.get(cid)
                    if c:
                        c.peer_name = info["nickname"]
                        if info.get("avatar"):
                            c.avatar = info["avatar"]
                except Exception:
                    continue
            if done:
                conn.commit()
                with self._nick_lock:
                    self._nick_tries = 0   # 成功即清零，避免累积误触上限
                logger.info(
                    f"[recv][{self.name}] 骨架昵称富化：{done}/{len(batch)} 个"
                    f"（IndexedDB，零网络请求）")
        except Exception as e:
            # 异常（超时/连接重置/容器忙）同样归还待办 —— 否则一次网络抖动
            # 就永久丢掉这批骨架的富化机会，与「下次再补」的契约矛盾。
            self._reschedule_enrich(batch, f"异常：{type(e).__name__}")
            logger.warning(f"[RECV-020] " + f"[recv][{self.name}] 骨架昵称富化失败（不影响收消息）: {e}")

    def _refresh_my_uid(self) -> bool:
        """（重新）读取本机 uid。

        2026-09-06 全局治理（2.1b uid 轮换自愈）：
        原实现只在 AccountInbox.__init__ 里算一次，之后**永不刷新**。
        而抖音存在 uid 轮换（知识库 08 §24.9 实测：query/user 返回新 uid，
        imapi 仍挂老 uid），一旦轮换，方向判定 `sender == my_uid` 就全错
        （自己发的被判成对方发的，反之亦然）。

        改：抽成本方法 + 由 WS 循环定期调用（见 RecvChannel 里的
        MY_UID_REFRESH_INTERVAL），轮换后自动自愈。
        返回是否读取成功。
        """
        try:
            from auto_dm import accounts as _acc
            from dy_apis.login_api import DYLoginApi
            env_path = _acc.env_path_of(self.name)
            if not env_path:
                return False
            _auth = DYLoginApi._load_auth_from_env(env_path)
            new_uid = str(_auth.get_uid()) if _auth else None
            if new_uid and new_uid != self.my_uid:
                if self.my_uid:
                    logger.warning(f"[RECV-001] " + f"[recv][{self.name}] my_uid 发生轮换：{self.my_uid} → {new_uid}"
                        f"（已自动更新方向判定基准）")
                self.my_uid = new_uid
            # 2026-09-17 修补（审查 P0-2 配套）：my_uid 是方向判定的唯一基准，
            # 取不到时 recv_daemon:941 会把所有消息兜底判成 me。原实现静默返回
            # False，运维无从察觉 → 改为显式告警（每条限频，避免刷屏）。
            if not self.my_uid:
                self._warn_my_uid_missing()
            return bool(self.my_uid)
        except Exception as e:
            logger.warning(f"[RECV-002] " + f"[recv][{self.name}] my_uid 刷新异常: {e}")
            return False

    _my_uid_warn_ts = 0.0

    def _warn_my_uid_missing(self) -> None:
        """my_uid 缺失告警（每条 60s 限频）。

        2026-09-17 修补（审查 P0-2 配套）：原 `_refresh_my_uid` 失败静默返回
        False，而 my_uid 是 role 判定的基准 —— 缺失时所有 WS 消息会被兜底判
        成 me，未读数不增长且方向全错，却没有任何日志线索。
        """
        now = time.time()
        if now - getattr(self, "_my_uid_warn_ts", 0.0) < 60.0:
            return
        self._my_uid_warn_ts = now
        logger.warning(f"[RECV-003] " + f"[recv][{self.name}] my_uid 未就绪：方向判定将兜底为 me"
            f"（凭证未就绪或探活失败？），请检查账号登录状态")

    def _extract_peer_uid(self, conv_id: str) -> str | None:
        """从 conv_id 0:1:uid_a:uid_b 提取对端 UID（排除自己）。

        2026-09-25 H-25 根因修复：委托 `services.conv_identity.peer_uid`
        （唯一真相源），并**删除「my_uid 未就绪则 return uid_b」的兜底**。

        为什么必须删（实测根因，136 个会话被污染成本账号昵称/头像）：
          实库 conv_id 形态为 `0:1:<对端uid>:<本号uid>`（本号段在 idx=3，
          136/136 实测）。旧兜底在 my_uid 缺失时返回 **uid_b = 本号自己**
          ⇒ `_enrich_nicknames_once` 用本号 uid 去 IndexedDB 取到**本号昵称/
          头像**并回填 → 会话被污染。且该兜底**永不返回 None**，使更正确的
          `_infer_peer_by_common_uid`（按 90% 覆盖率推断本号、再取对端）
          被整体绕过，两道防线一起失效。

        conv_identity.peer_uid 的行为（本处即契约）：
          - a==b → None（自发自收系统会话）
          - my 已知：a==my 返回 b；b==my 返回 a；都不是 my → None（数据异常不猜）
          - my 未知：返回 b（弱保证；调用方若需强判据应先经
            `_infer_peer_by_common_uid` 推断 my_uid，见 `_enrich_nicknames_once`）
        """
        try:
            from services.conv_identity import peer_uid as _peer_uid
        except Exception:
            return None
        # 2026-09-25 H-25：my_uid 未知时**不猜**。conv_identity.peer_uid 在 my
        # 未知时有「返回 b」的弱语义，而实库 conv_id 形态 `0:1:<对端>:<本号>`
        # 使那个 b **恰是本号自己**（与 2026-09-07 D 方案同因）。调用方应先经
        # `_infer_peer_by_common_uid` / `_refresh_my_uid` 拿到 my_uid 再调用本方法。
        if not self.my_uid:
            return None
        return _peer_uid(conv_id, self.my_uid)

    def _db(self):
        from database import get_db
        return get_db()

    def _load_from_db(self) -> None:
        """启动时从 SQLite 加载会话骨架到内存（消息按需查询，不全量加载）。"""
        try:
            conn = self._db()
            rows = conn.execute(
                "SELECT conv_id,peer_id,peer_name,short_id,last_ts,unread,avatar "
                "FROM dm_conversations WHERE account=? ORDER BY last_ts DESC",
                (self.name,),
            ).fetchall()
            for r in rows:
                c = Conversation(r["conv_id"], r["peer_id"], r["peer_name"])
                c.short_id = r["short_id"]
                c.last_ts = r["last_ts"] or 0
                c.unread = r["unread"] or 0
                c.avatar = r["avatar"] or None
                self.convs[r["conv_id"]] = c
                # 2026-09-15：启动时把「昵称为空/裸 uid」的存量会话也登记富化，
                # 让历史上遗留的空名会话自愈（无需用户再点一次「更新会话」）。
                _pn = r["peer_name"]
                if not _pn or _pn == r["peer_id"]:
                    self._nick_pending.add(r["conv_id"])
            if self.convs:
                logger.info(f"[recv][{self.name}] 已从数据库加载 {len(self.convs)} 个会话")
            # 有存量待富化 → 起一次节流任务（BCC 在线则自动补昵称）
            if self._nick_pending:
                logger.info(
                    f"[recv][{self.name}] 启动存量：{len(self._nick_pending)} 个会话"
                    f"昵称待富化，{IDB_ENRICH_DELAY}s 后自动回填")
            # 有存量待富化 → 起一次节流任务（BCC 在线则自动补昵称）
            if self._nick_pending:
                try:
                    self._nick_worker = threading.Timer(
                        IDB_ENRICH_DELAY, self._enrich_nicknames_once)
                    self._nick_worker.daemon = True
                    self._nick_worker.start()
                except Exception:
                    pass
        except Exception as e:
            logger.warning(f"[RECV-002] " + f"[recv][{self.name}] 数据库加载会话失败: {e}")

    def get_or_create(self, conv_id: str, peer_id: Any = None,
                      peer_name: str | None = None) -> Conversation:
        with self.lock:
            c = self.convs.get(conv_id)
            if c is None:
                c = Conversation(conv_id, peer_id, peer_name)
                self.convs[conv_id] = c
                # 持久化到 SQLite（INSERT OR IGNORE 避免重复）
                try:
                    conn = self._db()
                    conn.execute(
                        "INSERT OR IGNORE INTO dm_conversations(account,conv_id,peer_id,"
                        "peer_name,short_id,last_ts,unread) VALUES(?,?,?,?,?,?,?)",
                        (self.name, conv_id, peer_id, peer_name, None, 0, 0),
                    )
                    conn.commit()
                except Exception:
                    pass
            elif peer_id and not c.peer_id:
                c.peer_id = peer_id
                # P2-6（审计 2026-09-23）：本处昵称占位判据原为**内联** `if peer_name:`
                # （只判空，漏判「数字 UID / 等于对端 uid」），与唯一实现
                # `services.verdicts.is_placeholder_name` 不一致 —— docstring 声称
                # 「收敛 5/5」实为 4/5。此处改为**引用唯一实现**：
                # 仅当新值不是占位时才采纳为昵称；占位值退回对端 uid。
                # 与 `services/nickname_fallback.missing_nickname_convs` 同源判据
                # （数字 peer_name 可能是发送方 uid，peer_id 才是真对端）。
                from services.verdicts import is_placeholder_name as _is_ph
                _nick_ok = bool(peer_name) and not _is_ph(peer_name, peer_id=peer_id)
                if _nick_ok:
                    c.peer_name = peer_name
                _write_name = peer_name if _nick_ok else peer_id
                try:
                    conn = self._db()
                    # 保护已由 capture_all 关联的昵称 / 已有对端 UID：
                    # 仅当数据库现有 peer_name 为空或缺失时才用本次值覆盖；
                    # 若现有已有值（昵称或对端UID），绝不回退成自己或空值。
                    conn.execute(
                        "UPDATE dm_conversations SET peer_id=?,"
                        "peer_name=COALESCE("
                        "  (SELECT CASE WHEN peer_name IS NOT NULL AND peer_name != '' "
                        "THEN peer_name ELSE ? END), ?) "
                        "WHERE account=? AND conv_id=?",
                        (peer_id, _write_name, _write_name,
                         self.name, conv_id),
                    )
                    conn.commit()
                except Exception:
                    pass
            return c

    def list_convs(self) -> list[dict]:
        """返回会话列表（按最后消息时间倒序），含最近若干条消息预览。"""
        with self.lock:
            # 优先用内存缓存（WS 实时更新），但补全 SQLite 中可能多的会话
            try:
                conn = self._db()
                rows = conn.execute(
                    "SELECT conv_id,peer_id,peer_name,short_id,last_ts,unread,avatar "
                    "FROM dm_conversations WHERE account=? ORDER BY last_ts DESC",
                    (self.name,),
                ).fetchall()
                for r in rows:
                    if r["conv_id"] not in self.convs:
                        c = Conversation(r["conv_id"], r["peer_id"], r["peer_name"])
                        c.short_id = r["short_id"]
                        c.last_ts = r["last_ts"] or 0
                        c.unread = r["unread"] or 0
                        c.avatar = r["avatar"] or None
                        self.convs[r["conv_id"]] = c
            except Exception:
                pass
            return [c.to_dict(limit_messages=20) for c in sorted(
                self.convs.values(), key=lambda x: x.last_ts, reverse=True)]

    def get_conv(self, conv_id: str) -> dict | None:
        """返回单个会话详情（从 SQLite 加载完整消息历史）。"""
        with self.lock:
            c = self.convs.get(conv_id)
            peer_id = c.peer_id if c else None
            peer_name = c.peer_name if c else None
            short_id = c.short_id if c else None
            try:
                conn = self._db()
                # 确保会话骨架存在
                if c is None:
                    row = conn.execute(
                        "SELECT conv_id,peer_id,peer_name,short_id,last_ts,unread,avatar "
                        "FROM dm_conversations WHERE account=? AND conv_id=?",
                        (self.name, conv_id),
                    ).fetchone()
                    if not row:
                        return None
                    peer_id = row["peer_id"]
                    peer_name = row["peer_name"]
                    short_id = row["short_id"]
                    avatar = row["avatar"] or ""
                else:
                    # 2026-09-16 实机修复：内存会话命中时 row 从未赋值，
                    # 原来 L580 直接 row["avatar"] 抛 UnboundLocalError
                    # （RECV-003 → 404 会话不存在）。改用内存对象属性。
                    avatar = c.avatar or ""
                # 从 SQLite 加载消息（分页最近 200 条）
                mrows = conn.execute(
                    "SELECT role,text,msg_type,extra,ts FROM dm_messages "
                    "WHERE account=? AND conv_id=? ORDER BY ts DESC LIMIT 200",
                    (self.name, conv_id),
                ).fetchall()
                messages = [
                    {
                        "role": m["role"],
                        "text": m["text"],
                        "msg_type": m["msg_type"],
                        "extra": json.loads(m["extra"] or "{}"),
                        "ts": m["ts"],
                    }
                    for m in reversed(mrows)
                ]
                last_ts = mrows[0]["ts"] if mrows else 0
                return {
                    "conv_id": conv_id,
                    "peer_id": peer_id,
                    "peer_name": peer_name,
                    "short_id": short_id,
                    "unread": 0,
                    "last_ts": last_ts,
                    "messages": messages,
                    "avatar": avatar,
                }
            except Exception as e:
                logger.warning(f"[RECV-003] " + f"[recv][{self.name}] 数据库加载会话详情失败: {e}")
                return None

    def mark_read(self, conv_id: str) -> None:
        with self.lock:
            c = self.convs.get(conv_id)
            if c:
                c.unread = 0
            try:
                conn = self._db()
                conn.execute(
                    "UPDATE dm_conversations SET unread=0 WHERE account=? AND conv_id=?",
                    (self.name, conv_id),
                )
                conn.commit()
            except Exception:
                pass

    def add_message(self, conv_id: str, role: str, text: str,
                    peer_id: Any = None, peer_name: str | None = None,
                    msg_type: str = "text", extra: dict | None = None,
                    msg_id: str | None = None) -> Conversation:
        # 回执类消息（msg_type=50001「对方已读」）不落库：
        # 这类消息无 msg_id（唯一索引管不到去重），WS 每次同步都会重复写入，
        # 实测全库堆积 1938 条，把真实聊天记录挤掉、也让会话 last_ts 被无效刷新。
        # 仅更新会话的 last_ts 感知活跃度，不写 dm_messages。
        if str(msg_type) == "50001":
            return self.get_or_create(
                conv_id,
                peer_id or self._extract_peer_uid(conv_id),
                peer_name,
            )
        # 2026-09-06 全局治理（脏数据过滤前移到写侧）：
        # 此前过滤只在【读侧】SQL（api/messages.py 的 NOT LIKE），脏数据
        # 依然入库且 unread+1 已累加 ⇒ 会话列表显示未读数，点开却是空白/
        # 内容对不上。这里在写库前拦截，与读侧规则保持一致：
        #   - 系统占位提示（陌生会话首次打开，抖音自动塞入）
        #   - [未知媒体] 解析噪音 / [分享视频] 脏数据 / iesdouyin 分享链接
        # 命中则不写 dm_messages、不累加 unread。
        if _is_noise_text(text):
            logger.debug(f"[recv][{self.name}] 写侧拦截脏数据（不入库/不计未读）: "
                         f"{(text or '')[:40]}")
            return self.get_or_create(
                conv_id,
                peer_id or self._extract_peer_uid(conv_id),
                peer_name,
            )
        ts = time.time()
        with self.lock:
            # peer_id 为空/等于自己 → 从 conv_id 提取对端 UID（WS sender 常空/自己）
            if not peer_id or str(peer_id) == self.my_uid:
                peer_id = self._extract_peer_uid(conv_id) or peer_id
            c = self.get_or_create(conv_id, peer_id, peer_name)
            c.add(role, text, msg_type, extra, ts=ts)
            # 持久化到 SQLite（单条消息 + 会话 last_ts 更新）
            try:
                conn = self._db()
                # 2026-09-17 修补（审查 P2-12）：**本地发送与 WS 回声双写**。
                # 原流程：/send 落库不传 msg_id（NULL，ts=本地时刻）→ 随后 WS
                # 回声到达带真实 server_message_id（ts=WS 到达时刻）→ 两者毫秒
                # 时间戳不同，uniq_dmmsg_fallback 不命中，uniq_dmmsg 又因
                # 本地行 msg_id IS NULL 管不到 → 同一条自己发的消息存两行。
                #
                # 现改为「占位回填」：本地发送写入带 `local:` 前缀的占位
                # msg_id；WS 回声到达时若发现同会话同角色同文本的占位行，
                # 则 **UPDATE 该行补上真实 msg_id**，不再 INSERT 新行。
                if msg_id and role == "me":
                    cur = conn.execute(
                        "UPDATE dm_messages SET msg_id=? "
                        "WHERE account=? AND conv_id=? AND role='me' "
                        "AND msg_id LIKE 'local:%' AND text=? AND ?-ts BETWEEN 0 AND 300",
                        (str(msg_id), self.name, conv_id, text, ts),
                    )
                    if cur.rowcount > 0:
                        # 已回填占位行 —— 同步 last_ts 后直接返回，不再插入
                        conn.execute(
                            "UPDATE dm_conversations SET last_ts=? "
                            "WHERE account=? AND conv_id=?",
                            (ts, self.name, conv_id))
                        conn.commit()
                        logger.debug(
                            f"[recv][{self.name}] WS 回声已回填本地占位消息 "
                            f"（conv {conv_id[:8]}…, msg_id={str(msg_id)[:20]}）")
                        return c
                # 2026-09-06 全局并发治理（双通道重复落库）：
                # 裸 INSERT 会绕过 uniq_dmmsg / uniq_dmmsg_fallback 两个唯一
                # 索引而直接抛 IntegrityError（不是去重）。改 OR IGNORE 后
                # WS 回声与 WP 页面轮询写入同一条消息时自动去重，
                # 与 conversation_capture / 首包补全路径行为一致。
                conn.execute(
                    "INSERT OR IGNORE INTO dm_messages("
                    "account,conv_id,role,text,msg_type,msg_code,extra,ts,msg_id)"
                    " VALUES(?,?,?,?,?,?,?,?)",
                    _ws_tuple(self.name, conv_id, role, text, msg_type, extra, ts, msg_id)


                )
                conn.execute(
                    "UPDATE dm_conversations SET last_ts=?,unread=unread+? "
                    "WHERE account=? AND conv_id=?",
                    (ts, 1 if role == "them" else 0, self.name, conv_id),
                )
                # B 机制：运行时收到带 sender_nickname 的新消息，回写会话昵称。
                # 仅在当前 peer_name 为空 / 等于 peer_id(数字) / 等于自己昵称 时覆盖，
                # 避免覆盖首包+im/user/info 已写入的正确昵称。
                # 注意：WS 推送的 sender 常是自己的 UID/昵称（系统会话或自己发出的），
                # 这种 peer_name 不可作为对端昵称，必须排除，否则污染成「自己」。
                if peer_name and peer_name != conv_id and str(peer_name) != str(self.my_uid):
                    conn.execute(
                        "UPDATE dm_conversations SET peer_name=? "
                        "WHERE account=? AND conv_id=? AND "
                        "(peer_name IS NULL OR peer_name='' OR peer_name=? OR peer_name=?)",
                        (peer_name, self.name, conv_id, peer_id, peer_name),
                    )
                conn.commit()
            except Exception as e:
                logger.warning(f"[RECV-004] " + f"[recv][{self.name}] 消息持久化失败: {e}")
        return c


# ----------------------------------------------------------------------------
# 接收通道（迁移自旧版 RecvChannel）
# ----------------------------------------------------------------------------
class RecvChannel(threading.Thread):
    """单个账号的私信接收通道（守护线程）。"""

    def __init__(self, name: str, env_path: str, inbox: AccountInbox,
                 auto_reconnect: bool = True) -> None:
        super().__init__(daemon=True)
        self.name = name
        self.env_path = env_path
        self.inbox = inbox
        self.auto_reconnect = auto_reconnect
        self._stop = threading.Event()
        self._ws: Any = None
        self._auth: Any = None
        # 2026-09-16 v0.43.36：连接生命周期移交 daemon/ws_link.py（L0~L3）。
        # 本类只剩「构建 WebSocketApp」+「协议解析」两个职责。
        # 重连后追赶的节流时间戳（频繁重连时不打爆 HTTP 接口）
        self._last_catchup = 0.0

    def _build_auth(self) -> Any:
        from dy_apis.login_api import DYLoginApi
        auth = DYLoginApi._load_auth_from_env(self.env_path)
        # WS 长连接同样要求实时 cookie（陈旧 cookie 会 KICK/拒绝）
        DYLoginApi.refresh_cookie_from_profile(auth, self.env_path)
        return auth

    def _make_ws(self) -> Any:
        from websocket import WebSocketApp
        from dy_apis.douyin_api import DouyinAPI
        from builder.header import HeaderBuilder
        from builder.params import Params

        # 注：凭证在此加载 —— WSLink 每次重连都会调用本方法，从而自动拿到
        # 最新 cookie（原实现只在首连取一次，重连用陈旧凭证易被 KICK）。

        auth = self._build_auth()
        self._auth = auth
        device_id = DouyinAPI.get_device_id(auth=auth)
        app_key = "e1bd35ec9db7b8d846de66ed140b1ad9"
        fp_id = "9"
        access_key = f"{fp_id + app_key + device_id}f8a69f1719916z"
        access_key = hashlib.md5(access_key.encode("utf-8")).hexdigest()
        params = Params()
        (params
         .add_param("aid", "6383")
         .add_param("device_platform", "douyin_pc")
         .add_param("fpid", fp_id)
         .add_param("device_id", device_id)
         .add_param("token", auth.cookie.get("sessionid", ""))
         .add_param("access_key", access_key))
        url = f"wss://frontier-im.douyin.com/ws/v2?{params.toString()}"

        # 2026-09-16 v0.43.36（L0/L2）：回调不再自行 sleep/重连。
        # 生命周期统一由 daemon/ws_link.py 的 WSLink 单循环掌管：
        #   ① 回调内递归重连 → 每层栈 +1，实测同秒 3 次 CLOSE
        #   ② 回调内 time.sleep 会卡住 websocket-client 的 dispatcher 线程
        # 此处只做「业务侧建连反应」；状态与重连由 WSLink.cb_* 处理。

        def on_open(ws):
            # 2026-09-06（2.1b uid 轮换自愈）：每次建连/重连都刷新一次
            # my_uid —— 放在 on_open 而非定时器，零额外开销且覆盖重连场景。
            # uid 轮换后方向判定（sender == my_uid）自动恢复正确。
            try:
                self.inbox._refresh_my_uid()
            except Exception:
                pass

        def _mk_msg_cb():
            """消息回调（业务侧）。WSLink 会在其外层包裹生命周期钩子。"""
            def _m(ws, message):
                try:
                    self._handle(message)
                except Exception as e:
                    logger.warning(f"[RECV-005] " + f"[recv][{self.name}] 消息解析异常: {e}")
            return _m

        def on_error(ws, error):
            self.inbox.connected = False
            self.inbox.last_error = str(error)

        def on_close(ws, *args):
            self.inbox.connected = False

        ws = WebSocketApp(
            url=url,
            header={
                "Pragma": "no-cache",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6",
                "User-Agent": HeaderBuilder.ua,
                "Cache-Control": "no-cache",
                "Sec-WebSocket-Protocol": "binary, base64, pbbp2",
                "Sec-WebSocket-Extensions": "permessage-deflate; client_max_window_bits",
            },
            cookie=auth.cookie_str,
            # 2026-09-16 v0.43.36：回调绑定权归 WSLink（_bind_callbacks 会包裹
            # 此处传入的回调）。这里只保留**业务侧**逻辑，生命周期不在此处理。
            on_message=_mk_msg_cb(),
            on_error=on_error,
            on_close=on_close,
            on_open=on_open,
        )
        return ws

    # ---- 2026-09-16 v0.43.36：生命周期移交 WSLink，本类不再自行 run_forever ----
    def _make_link(self) -> Any:
        from daemon.ws_link import WSLink

        def _on_connected():
            self.inbox.connected = True
            self.inbox.last_error = ""
            self._catchup_after_reconnect()

        def _on_disconnected(reason: str):
            self.inbox.connected = False
            if reason:
                self.inbox.last_error = reason

        def _wrap_app():
            ws = self._make_ws()
            self._ws = ws
            return ws

        link = WSLink(
            name=self.name,
            make_ws=_wrap_app,
            on_message=self._handle,
            on_connected=_on_connected,
            on_disconnected=_on_disconnected,
        )
        return link

    def _catchup_after_reconnect(self) -> None:
        """L3 追赶：重连后按节流用 HTTP 2043 首包补齐掉线期的会话/消息。

        为什么需要：WS **不重推历史**（知识库 08 §12.2-5 已证），掉线期间的
        消息不会随重连回来。原实现重连后完全不补拉 ⇒ 断得越勤丢得越多。

        去重保证：`_pull_conversations_api` 走 `INSERT OR IGNORE`（命中
        uniq_dmmsg / uniq_dmmsg_fallback），重复补拉无副作用。

        节流：距上次补拉不足 CATCHUP_MIN_INTERVAL 秒则跳过，避免网络抖动期
        高频重连打爆 HTTP 接口、放大风控暴露。
        """
        try:
            from daemon.ws_link import CATCHUP_MIN_INTERVAL
        except Exception:
            CATCHUP_MIN_INTERVAL = 120.0
        now = time.time()
        if now - self._last_catchup < CATCHUP_MIN_INTERVAL:
            logger.debug(
                f"[recv][{self.name}] 追赶补拉节流跳过"
                f"（距上次 {now - self._last_catchup:.0f}s）")
            return
        self._last_catchup = now
        try:
            n = _pull_conversations_api(self.inbox)
            logger.info(
                f"[recv][{self.name}] 重连后追赶补拉完成（会话 {n} 个）")
        except Exception as e:
            logger.warning(f"[RECV-034] " + f"[recv][{self.name}] 重连后追赶补拉失败（不影响收消息）: {e}")

    def run(self) -> None:
        """线程主体：交由 WSLink 单循环驱动（不再递归、不再回调内 sleep）。"""
        self._link = self._make_link()
        self._link.start()
        # 阻塞到 stop()，保持 Thread 语义（join 可用）
        while not self._stop.is_set():
            if self._stop.wait(1.0):
                break

    def stop(self) -> None:
        self._stop.set()
        link = getattr(self, "_link", None)
        if link is not None:
            link.stop()
        try:
            if self._ws:
                self._ws.close()
        except Exception:
            pass

    def _handle(self, message: bytes) -> None:
        """解析 PushFrame -> Response。

        - new_message_notify：新私信消息，按 content 提取可读文本入库。
        - get_conversation_info_list_v2_response_body：连接初始化时抖音下发的
          已有会话列表同步帧；据此建立会话骨架（conversation_id），使守护启动
          即拿到全部历史会话（peer 信息待后续新消息到达时补全）。
        """
        from static import Live_pb2, Response_pb2
        frame = Live_pb2.PushFrame()
        frame.ParseFromString(message)
        if frame.payloadType == "pb":
            resp = Response_pb2.Response()
            resp.ParseFromString(frame.payload)
            # 1) 已有会话列表同步帧（连接初期下发一次）
            sync = resp.body.get_conversation_info_list_v2_response_body
            if sync and getattr(sync, "conversation_info_list", None):
                self._sync_conversations(sync.conversation_info_list)
                # 同步帧本身不携带消息体，直接返回；消息走 new_message_notify
                return
            # 2) 新私信消息
            nm = resp.body.new_message_notify
            if not nm or not nm.message:
                return
            msg = nm.message
            conv_id = msg.conversation_id
            sender = msg.sender
            content = msg.content
            msg_type = msg.message_type
            try:
                content_json = json.loads(content) if content else {}
            except Exception:
                content_json = {}
            # 注：图片消息结构排查用的 WS 原始帧 dump 已移除（2026-08-31）。
            # 排查方法已固化到知识库 08 §21.5，需要时按该节重建即可。
            text, extra = self._extract(content_json, msg_type)
            if text is None:
                return
            # 过滤系统引导消息(如"微信"/"在哪个地区受伤的"快捷回复建议):
            # 这类消息 msg_type=7 且无服务端消息 ID(非真实聊天),不应展示在聊天记录中
            #
            # ⚠️ 2026-09-16 实机抓真因（严重）：原写 getattr(msg, "msg_id", None)，
            #    但 Response.proto 的 MessageBody **根本没有 msg_id 字段**
            #    （字段名是 server_message_id）。getattr 恒返回 None
            #    ⇒ `not None` 恒为真 ⇒ **所有 msg_type=7 的真实文本消息被一律丢弃**。
            #    实测后果：9/13~9/16 三天 WS 推送里 role=them 一条都没有
            #    （1800 条全是本号自己的回声），对方消息只能靠 HTTP 补拉捞回。
            #    改为按真实字段名取 server_message_id。
            _sid = int(getattr(msg, "server_message_id", 0) or 0)
            if int(msg_type) == 7 and not _sid:
                return
            # 2026-09-14 v0.43.11 方案B 修正（三处）：
            #  ① **peer_uid 只认 conv_id**（0:1:uid_a:uid_b，排除本号即对端，100% 可靠）。
            #     原实现把 peer_id=sender 直接写入 —— 而 role=me 时 sender 就是**本号**，
            #     于是把 peer_id 写成了自己（这正是 capture_all 要花大力气「存量订正」
            #     的污染源）。sender 仅在 role=them 时才等于对端。
            #  ② **昵称绝不取裸 sender**：`sender_nickname` 实测恒为 None
            #     （15 天日志 27947 条全 None），故 peer_name 落在 `or sender` 上
            #     → 把数字 UID 当昵称写库。现在昵称取不到就留空，让展示层回落。
            #  ③ 日志降噪：该行原为每条消息 INFO，实测单日 564 行、15 天累计
            #     数万行刷屏。改为 debug，仅在昵称真的拿到时才 INFO。
            _real_peer = None
            _cp = str(conv_id).split(":")
            if len(_cp) == 4:
                _ua, _ub = _cp[2], _cp[3]
                _mu = str(self.inbox.my_uid or "")
                _real_peer = (_ub if _ua == _mu else (_ua if _ub == _mu else None))
            if not _real_peer:
                _real_peer = str(sender) if (sender and str(sender) != str(self.inbox.my_uid)) else None
            # 08 §13.5 铁律：方向只能用 sender UID 判断，不可用消息类型推断。
            # sender == 自己 UID → 我发(me)；否则对方发(them)。
            #
            # 2026-09-17 修补（审查 P0-2）：原写法
            #   role = "me" if sender and str(sender) == str(my_uid) else "them"
            # 在 sender 为空时落 them，与 wp_recv.py:161-165、
            # conversation_capture.py:469-473 的「空 sender → me」判定相反
            # —— 同一条消息走 WS 与走 WP 会得到不同 role，前端方向错乱，
            # 且本号自己的回声被判成对方消息导致未读数虚高。
            # 统一为：sender 为空 → me；有 sender → 按 UID 比对。
            role = "them" if (
                sender
                and self.inbox.my_uid
                and str(sender) != str(self.inbox.my_uid)
            ) else "me"
            _nick = content_json.get("sender_nickname") or ""
            if _nick and str(_nick) == str(self.inbox.my_uid):
                _nick = ""
            logger.debug(
                f"[recv][{self.name}][会话 {conv_id[:8]}…] 新消息: "
                f"peer={_real_peer}, sender={sender}, role={role}, nick={_nick!r}")
            self.inbox.add_message(
                conv_id, role, text, peer_id=_real_peer,
                peer_name=_nick or None, msg_type=str(msg_type), extra=extra,
                # 2026-09-06 双通道去重：写入抖音消息唯一 ID，让 WS / WP
                # 两条通道写入同一条消息时命中 uniq_dmmsg 唯一索引去重。
                # 2026-09-16 实机修正：同 904 行的坑 —— 这里原来也取
                # `msg.msg_id`，而 MessageBody 只有 `server_message_id`。
                # 结果 msg_id 恒为 None ⇒ uniq_dmmsg 唯一索引拿不到真实 ID
                # ⇒ WS 回声与 WP 轮询**永远无法去重**（实测 812 条回声刷屏）。
                # 修正后同一条消息两通道写入可正常命中去重。
                msg_id=str(_sid) if _sid else None,
            )
            logger.info(
                f"[recv][{self.name}][会话 {conv_id[:8]}…] "
                f"{_nick or _real_peer or sender}: {text}")
        elif frame.payloadType == "text/json":
            try:
                logger.debug(f"[recv][{self.name}] json 控制帧: {json.loads(frame.payload)}")
            except Exception:
                pass

    def _sync_conversations(self, conv_list: list) -> None:
        """把 WS 下发的已有会话列表建立成会话骨架并持久化到 SQLite。

        日志策略：同步帧为高频推送（WS 实时），逐会话打印会刷屏。改为静默处理，
        仅在「出现新会话」（n_new>0）时打印一句摘要，符合「轮询补充静默」要求。
        """
        n_new = 0
        with self.inbox.lock:
            try:
                conn = self.inbox._db()
            except Exception:
                conn = None
            for item in conv_list:
                conv_id = getattr(item, "conversation_id", "") or ""
                if not conv_id:
                    continue
                if conv_id in self.inbox.convs:
                    continue
                c = Conversation(conv_id, None, None)
                c.short_id = getattr(item, "conversation_short_id", None) or None
                self.inbox.convs[conv_id] = c
                n_new += 1
                if conn:
                    conn.execute(
                        "INSERT OR IGNORE INTO dm_conversations(account,conv_id,"
                        "peer_id,peer_name,short_id,last_ts,unread) VALUES(?,?,?,?,?,?,?)",
                        (self.inbox.name, conv_id, None, None, c.short_id, 0, 0),
                    )
                # 2026-09-15：登记待富化昵称（后台线程节流批量从 IndexedDB 回填）
                self.inbox._mark_nick_pending(conv_id)
            if conn:
                conn.commit()
        if n_new:
            logger.info(f"[recv][{self.name}] 同步帧新增 {n_new} 个会话（已静默入库）")

    @staticmethod
    def _extract(content_json: dict, msg_type: Any) -> tuple[str | None, dict]:
        """把 content JSON 按消息类型转成可读文本。返回 (text, extra)。"""
        try:
            t = int(msg_type)
        except Exception:
            t = msg_type
        if t == 7:
            text = content_json.get("text", "") or ""
            # 2026-09-06 系统占位提示过滤（用户实测反馈）：
            # 抖音在「未发过消息的陌生会话」首次被打开时，会自动塞入一条
            # msg_type=7 的占位提示，内容形如：
            #   "对方回复你或互关之前，可发送一条文字消息。请礼貌发言，自觉遵守抖音社区规范"
            # sender 来自对方（陌生人），role=them 被当真实消息入库污染聊天记录。
            # 识别特征：文本含固定模板串（"对方回复你或互关之前"/"请礼貌发言"/
            # "自觉遵守" 三选一即中），一律丢弃返回 None 让调用方不入库。
            _sys_templates = (
                "对方回复你或互关之前",
                "可发送一条文字消息",
                "请礼貌发言",
                "自觉遵守",
            )
            if any(tpl in text for tpl in _sys_templates):
                return None, {}
            return text, {}
        elif t == 5:
            # 表情包：url.url_list[0]，也可能在 resource_url 下
            def _pick(d, *keys):
                if not isinstance(d, dict):
                    return ""
                for k in keys:
                    lst = d.get(k)
                    if isinstance(lst, list) and lst:
                        return lst[0]
                return ""

            u = _pick(content_json.get("url"), "url_list") or _pick(
                content_json.get("resource_url"), "origin_url_list", "url_list"
            )
            if u:
                return f"[表情包] {u}", {}
            return "[表情包]", {}
        elif t == 17:
            try:
                return f"[语音] {content_json['resource_url']['url_list'][0]}", {}
            except Exception:
                return "[语音]", {}
        elif t == 27:
            # 图片：优先原图，其次普通图链；都没有才退化成占位（不能丢 URL，
            # 否则前端无法渲染缩略图预览）
            def _pick(d, *keys):
                if not isinstance(d, dict):
                    return ""
                for k in keys:
                    lst = d.get(k)
                    if isinstance(lst, list) and lst:
                        return lst[0]
                return ""

            res = content_json.get("resource_url")
            u = _pick(res, "origin_url_list", "url_list")
            # 2026-09-01：图片解密要素必须落库（08 §三十五 实机证真）。
            # 抖音 IM 图片是 AES-256-GCM 加密，密钥就在 resource_url.skey；
            # 此前恒返回 {} 导致 skey 丢失、原图永远无法解密。
            # 注意 JSON 内 & 被转义成 \u0026，必须还原，否则带签名的 URL 失效。
            extra = {}
            if isinstance(res, dict) and res.get("skey"):
                _origin = _pick(res, "origin_url_list", "large_url_list",
                                "medium_url_list", "thumb_url_list")
                if _origin:
                    extra = {"skey": res["skey"],
                             "origin_url": _origin.replace("\\u0026", "&")}
            # 2026-09-25（H-25 统一落库契约）：远程 URL 与内联 base64
            # **都不进 text**（text 只留语义标签）；缩略图字节改走 extra.thumb，
            # 读侧由后端派生下发。线上实测 origin_url 是加密体（需 skey 解密），
            # 本就不能直接渲染，故移出 text 零展示损失。
            try:
                from auto_dm.conversation_capture import (
                    _extract_thumb_data_uri as _th_ex,
                )

                _th = _th_ex(content_json)
            except Exception:
                _th = ""
            if _th:
                extra = dict(extra or {})
                extra["thumb"] = _th
            # ADR-012 / R8-4：语义标签取自 Schema SSOT（禁硬编码字面量）
            from services.message_schema import LABEL_MEDIA
            return LABEL_MEDIA, extra
        elif t == 8:
            return f"[分享视频] 视频ID {content_json.get('itemId', '')}", {}
        elif t == 50001:
            return f"[对方已读 标号 {content_json.get('read_index', '')}]", {}
        return f"[未知类型{t}] {json.dumps(content_json, ensure_ascii=False)[:200]}", {}

def _msg_tuple(m: dict, account: str, conv_id: str) -> tuple:
    """ADR-012 层 2：init 同步路径的**单一写入出口**（八列同序元组）。

    `extra` 由 `_msg_extra_json` 产出后再经 Schema SSOT 补 `kind`
    （未登记类型自动降级，绝不污染 text）。
    """
    from services.message_schema import MessageRecord
    import json as _json
    try:
        ex = _json.loads(_msg_extra_json(m) or "{}")
        if not isinstance(ex, dict):
            ex = {}
    except Exception:
        ex = {}
    rec = MessageRecord.build(text=m.get("text", ""),
                              msg_type=m.get("msg_type") or "text",
                              extra=ex, role=m.get("role"))
    return rec.tuple(account, conv_id,
                     ts=m.get("ts", 0),
                     msg_id=str(m.get("msg_id")) if m.get("msg_id") else None,
                     role=m.get("role") or "them")


def _ws_tuple(account: str, conv_id: str, role: str, text: str,
              msg_type, extra, ts, msg_id) -> tuple:
    """ADR-012 层 2：WS 实时路径（t==27 等）的**单一写入出口**。

    与 `_msg_tuple` 同理：经 Schema SSOT 归一化后再产出八列同序元组。
    """
    from services.message_schema import MessageRecord
    ex = extra if isinstance(extra, dict) else {}
    rec = MessageRecord.build(text=text or "", msg_type=msg_type or "text",
                              extra=dict(ex), role=role)
    return rec.tuple(account, conv_id, ts=ts or 0,
                     msg_id=str(msg_id) if msg_id else None,
                     role=role or "them")


def _msg_extra_json(m: dict) -> str:
    """把 parse_init_protobuf 单条消息的 extra 要素序列化为 JSON 字符串。

    2026-09-25（H-25 统一落库契约，顺带修一处真缺陷）：
    本文件的 init 同步路径原先把 extra **硬编码为 "{}"** —— 与同文件
    `_extract` 的 t==27 分支、以及 conversation_capture 的三条写路径不一致，
    导致该路径写入的图片消息 **skey/origin_url/thumb 全部丢失**（注释
    亦自陈「首包路径 extra 全空」）。现统一由本函数产出，键位与
    conversation_capture.capture_all 完全同构。
    """
    ex = {}
    if m.get("skey") and m.get("origin_url"):
        ex["skey"] = m["skey"]
        ex["origin_url"] = m["origin_url"]
    if m.get("thumb"):
        ex["thumb"] = m["thumb"]
    if m.get("sender_sec_uid"):
        ex["sender_sec_uid"] = m["sender_sec_uid"]
    if m.get("created_at_us"):
        ex["created_at_us"] = int(m["created_at_us"])
    if isinstance(m.get("reply"), dict) and m["reply"]:
        ex["reply"] = m["reply"]
    if m.get("voice_uri"):
        ex["voice_uri"] = m["voice_uri"]
    if m.get("voice_skey"):
        ex["voice_skey"] = m["voice_skey"]
    if m.get("is_recalled"):
        ex["is_recalled"] = int(m["is_recalled"])
    if m.get("visible") is not None:
        ex["visible"] = int(m["visible"])
    try:
        import json as _json

        return _json.dumps(ex, ensure_ascii=False) if ex else "{}"
    except Exception:
        return "{}"




# ----------------------------------------------------------------------------
# FastAPI 路由
# ----------------------------------------------------------------------------
# 2026-09-06 全局治理：脏数据判定（写侧与读侧共用的唯一规则源）
# 必须与 api/messages.py 读侧 SQL 的 NOT LIKE 规则保持一致，
# 否则会出现「写侧认为正常入库 + 读侧过滤不显示」⇒ 未读数虚高、点开空白。
_NOISE_PATTERNS = (
    "对方回复你或互关之前",   # 陌生会话系统占位提示
    "可发送一条文字消息",
    "请礼貌发言",
    "自觉遵守",
    "[未知媒体]",                          # 解析噪音
    "https://www.iesdouyin.com/share/",    # 群聊分享链接脏数据
)

# 2026-09-16 实机修正：不再拦截「[分享视频] 视频ID x」——
# 知识库 08 §16.4 实测判定：msg_type=8 是**真实视频分享**（对端真分享），
# 应正常展示，不是脏数据。此前 `_NOISE_PATTERNS` 里的裸 `"[分享视频]"` 子串
# 会把带 ID 的真实分享也一并拦掉（设计漂移：过滤了不该滤的）。
# 现在只拦「空分享」——`_extract` 在 itemId 缺失时返回裸 "[分享视频]"，
# 那才是 WS 错误解析噪音；带 ID 的放行。空分享由下方 _is_noise_text 特判。
_NOISE_EMPTY_SHARE = "[分享视频]"



def _is_noise_text(text: str | None) -> bool:
    """是否为应丢弃的脏数据/系统占位提示（不入库、不计未读）。"""
    if not text:
        return False
    # 2026-09-16：空分享特判 —— 裸 "[分享视频]"（无 ID）是解析噪音，
    # 带 ID 的 "[分享视频] 视频ID x" 是真实分享，放行。
    if text.strip() == _NOISE_EMPTY_SHARE:
        return True
    return any(p in text for p in _NOISE_PATTERNS)


def _safe_capture(name):
    """守护启动补一次捕获（首包解析+写库，不抢 profile）。失败不影响守护运行。"""
    try:
        from auto_dm.conversation_capture import capture_all
        capture_all(name, with_browser=False)
    except Exception as e:
        logger.warning(f"[RECV-007] " + f"[recv][{name}] 启动前移捕获失败（忽略）: {e}")


@app.on_event("startup")
async def _startup() -> None:
    logger.info(
        f"recv_daemon 启动 accounts={_state['accounts']} port={_state['port']}"
    )
    # 为每个账号启动 RecvChannel
    from auto_dm import accounts as acc
    # 确保 SQLite 已初始化（recv_daemon 独立进程也打开同一个 db 文件）
    try:
        import database
        database.get_db()
    except Exception as e:
        logger.error(f"[RECV-008] " + f"[recv] 数据库初始化失败: {e}")
    for name in _state["accounts"]:
        try:
            env_path = acc.env_path_of(name)
            inbox = AccountInbox(name)
            _state["inboxes"][name] = inbox
            channel = RecvChannel(name, env_path, inbox, auto_reconnect=True)
            _state["channels"][name] = channel
            channel.start()
            logger.info(f"[recv] 账号 {name} 接收通道已启动")
            # 守护启动顺带补一次前移捕获（首包解析写库，不抢 profile）
            try:
                from auto_dm.conversation_capture import capture_all
                threading.Thread(
                    target=lambda: _safe_capture(name), daemon=True
                ).start()
            except Exception as e:
                logger.warning(f"[RECV-009] " + f"[recv] 启动捕获注册失败: {e}")
        except Exception as e:
            logger.error(f"[RECV-010] " + f"[recv] 账号 {name} 启动失败: {e}")


@app.on_event("shutdown")
async def _shutdown() -> None:
    for ch in _state["channels"].values():
        ch.stop()


@app.get("/status")
async def status() -> dict:
    out = {}
    for name, ib in _state["inboxes"].items():
        # 2026-09-16 v0.43.36：连接健康可观测。稳态不能只报 connected 布尔值，
        # 必须能回答「连了几次 / 心跳发出去没 / 多久没收下行」——
        # 否则「30s 定时自杀」这类问题只能翻日志才能发现。
        ch = _state["channels"].get(name)
        link = getattr(ch, "_link", None)
        ls = dict(getattr(link, "stats", {}) or {}) if link else {}
        out[name] = {
            "connected": ib.connected,
            "error": ib.last_error,
            "conv_count": len(ib.convs),
            "total_unread": sum(c.unread for c in ib.convs.values()),
            "link": {
                "connects": ls.get("connects", 0),
                "disconnects": ls.get("disconnects", 0),
                "hb_sent": ls.get("hb_sent", 0),
                "hb_failed": ls.get("hb_failed", 0),
                "last_rx_age": round(ls.get("last_rx_age", 0.0), 1),
                "backoff_stage": ls.get("backoff_stage", 0),
            },
        }
    return {"ok": True, "accounts": out, "version": _rt_version()}


@app.get("/conversations")
async def conversations(account: str) -> dict:
    ib: AccountInbox | None = _state["inboxes"].get(account)
    if not ib:
        raise HTTPException(404, "账号不存在")
    convs = ib.list_convs()
    # 兜底：守护启动后首次拉取时，不管收件箱有没有 WS 同步帧的骨架，
    # 都强制调 Douyin IM API 拉一次全量真实会话，确保私信中心看到完整列表。
    # 避免 WS 同步帧给了 1 个骨架会话导致 list_convs 非空、跳过 API 拉取。
    if not ib._api_pulled:
        _pull_conversations_api(ib)
        ib._api_pulled = True
        convs = ib.list_convs()
    logger.info(f"[recv][{account}] /conversations 返回 {len(convs)} 个会话")
    for i, c in enumerate(convs):
        logger.info(f"[recv][{account}]   会话#{i}: conv_id={c.get('conv_id')}, peer_name={c.get('peer_name')}, peer_id={c.get('peer_id')}, messages={len(c.get('messages') or [])} 条")
    return {"ok": True, "conversations": convs}


def _pull_conversations_api(ib: AccountInbox) -> int:
    """直接调 Douyin IM get_message_by_init 拉全量会话 + get_im_user_info 解析昵称。

    实测发现：抖音网页 douyin.com/chat 用 get_message_by_init（cmd 2043）加载全部会话，
    然后调 /aweme/v1/web/im/user/info/ REST API 解析每个会话对方的昵称/头像。
    后端此前用的 get_info_list（cmd 610）是按 user_id 查单个会话的，不是"列全部"。
    """
    try:
        from auto_dm import accounts as acc
        from dy_apis.login_api import DYLoginApi
        from dy_apis.douyin_api import DouyinAPI
        env_path = acc.env_path_of(ib.name)
        auth = DYLoginApi._load_auth_from_env(env_path)
        # .env 里保存的 cookie 会过期，导致 imapi 返回 50 字节空响应。
        # 拉取前尝试从账号 profile 读取实时 cookie 刷新凭证（失败则退回原凭证）。
        DYLoginApi.refresh_cookie_from_profile(auth, env_path)
        my_uid = str(auth.get_uid())
    except Exception as e:
        logger.warning(f"[RECV-011] " + f"[recv][{ib.name}] 加载凭证失败: {e}")
        return 0

    # 1) get_message_by_init 拉全量会话（250KB，含全部会话 ID + 消息 + peer uid）
    try:
        raw = DouyinAPI.get_message_by_init(auth)
        if len(raw) < 2000:
            logger.warning(f"[RECV-012] " + f"[recv][{ib.name}] get_message_by_init 返回 {len(raw)} 字节"
                           f"（非全量，疑似凭证失效/限频）：{raw[:80]}")
        # 新版：protobuf 精确解析（field 6 = conversation 数组，消息内嵌 conv_id 链接键）
        from auto_dm.conversation_capture import parse_init_protobuf
        convs = parse_init_protobuf(raw, my_uid)
    except Exception as e:
        logger.warning(f"[RECV-013] " + f"[recv][{ib.name}] get_message_by_init 失败: {e}")
        return 0
    if not convs:
        logger.info(f"[recv][{ib.name}] get_message_by_init 返回 0 个会话")
        return 0

    # 2) 写入会话骨架 + 消息到 SQLite + 内存
    n = 0
    n_msg = 0
    with ib.lock:
        try:
            conn = ib._db()
        except Exception:
            conn = None
        for c in convs:
            conv_id = c["conversation_id"]
            peer_uid = c["peer_uid"]
            sec_uid = c.get("sec_uid")
            if conv_id in ib.convs:
                # 已存在：补全 peer_id（WS 建立的骨架可能没有 peer_id）
                if not ib.convs[conv_id].peer_id and peer_uid:
                    ib.convs[conv_id].peer_id = peer_uid
                    if conn:
                        conn.execute(
                            "UPDATE dm_conversations SET peer_id=? WHERE account=? AND conv_id=?",
                            (peer_uid, ib.name, conv_id),
                        )
                # 补全消息（增量）
                # 2026-09-16 实机修正：原 INSERT 缺 msg_id 列 ⇒ 补拉写入
                # 的消息 msg_id 恒 None ⇒ uniq_dmmsg 唯一索引管不到，
                # 与 WS 通道写入的同一消息重复入库（实测「WS链路修复测试」
                # id=1638 有 msg_id / id=1655 None 两条并存）。
                # 现补上 msg_id 列，让补拉与 WS 命中去重。
                if conn and c.get("messages"):
                    for m in c["messages"]:
                        try:
                            conn.execute(
                                "INSERT OR IGNORE INTO dm_messages("
                                "account,conv_id,role,text,msg_type,msg_code,extra,ts,msg_id)"
                                " VALUES(?,?,?,?,?,?,?,?)",
                                _msg_tuple(m, ib.name, conv_id)


                            )
                            n_msg += 1
                        except Exception:
                            pass
                continue
            # 新会话：建立骨架
            conv = Conversation(conv_id, peer_uid, peer_uid)
            ib.convs[conv_id] = conv
            n += 1
            if conn:
                conn.execute(
                    "INSERT OR IGNORE INTO dm_conversations(account,conv_id,"
                    "peer_id,peer_name,short_id,last_ts,unread,avatar) VALUES(?,?,?,?,?,?,?,?)",
                    (ib.name, conv_id, peer_uid, peer_uid, None, 0, 0, None),
                )
                # 2026-09-15：peer_name 是裸 uid 占位 → 登记富化（IDB 有真昵称则替换）
                ib._mark_nick_pending(conv_id)
                # 写消息（2026-09-16 修正：补 msg_id 列，见上方同款说明）
                for m in c.get("messages", []):
                    try:
                        conn.execute(
                            "INSERT OR IGNORE INTO dm_messages("
                            "account,conv_id,role,text,msg_type,msg_code,extra,ts,msg_id)"
                            " VALUES(?,?,?,?,?,?,?,?)",
                            _msg_tuple(m, ib.name, conv_id)


                        )
                        n_msg += 1
                    except Exception:
                        pass
        if conn:
            conn.commit()
    logger.info(f"[recv][{ib.name}] get_message_by_init 提取 {len(convs)} 个会话，"
                f"新增 {n} 个，写入消息 {n_msg} 条")
    # 昵称/头像补全：不再走 BCC 批量查（风控），改由 verify_account 时
    # 无头浏览器截 im/user/info 写入；运行时 WS 新消息也会带 sender_nickname。
    return n


@app.get("/conversation")
async def conversation(account: str, conv_id: str) -> dict:
    ib: AccountInbox | None = _state["inboxes"].get(account)
    if not ib:
        raise HTTPException(404, "账号不存在")
    conv = ib.get_conv(conv_id)
    if conv is None:
        # 未见过的会话：先 API 拉取骨架再查，保证点开的会话存在
        if not ib._api_pulled:
            _pull_conversations_api(ib)
            ib._api_pulled = True
        conv = ib.get_conv(conv_id)
    if conv is None:
        raise HTTPException(404, "会话不存在")
    ib.mark_read(conv_id)
    return {"ok": True, "conversation": conv}


class SendBody(BaseModel):
    account: str
    conv_id: str
    text: str
    # 2026-09-23（审计 P0-1）：调用方若自行取过服务端消息号可回传，
    # 使投递验证标记在 3.11/3.12 等无宽容解析器的解释器下同样可写。
    # ⚠️ 绝不能把「返回 ok」本身当证据 —— 这里必须是**真的** server_message_id。
    server_message_id: str = ""
    # 2026-09-28：发送来源（"manual" = 用户显式操作）。闸门据此决定是否套用
    # 手动豁免（见 `send.per_minute_manual_exempt`）。缺省 "" ⇒ 按自动路径严格管控。
    source: str = ""


class SendByUidBody(BaseModel):
    account: str
    peer_uid: str | int
    text: str
    server_message_id: str = ""
    source: str = ""


class SendImageBody(BaseModel):
    account: str
    conv_id: str
    # 图片二进制 base64（≤20MB 原始大小）
    #
    # 2026-09-17 修补（OCR 审查 HIGH）：原字段**无长度上限**，注释写的
    # "≤20MB" 只是注释，没有任何强制。本机任意调用方可投递任意大 base64
    # → 全部解码入内存（上游 messages.py 还会再 json.dumps 一份全量拷贝）
    # → 可致 OOM / 事件循环卡顿。
    # 20MB 原始 → base64 约 4/3 ≈ 27MB；留余量取 28_000_000 字符。
    # 解码后再复核真实字节数（防止 padding/空白绕过）。
    image_b64: str = Field(..., max_length=28_000_000)
    filename: str = "image.jpg"
    # 2026-09-28：同 SendBody.source（图片目前恒为用户显式发送）。
    source: str = ""


# ============================================================================
# 发送闸门（2026-09-16 v0.43.40：**降级为物理兜底**）
# ----------------------------------------------------------------------------
# 背景变更：改前本闸门是发送频率的**唯一裁决点**；架构调整后，
# 配额裁决已上移到调度器（services/dm_dispatch.DmDispatcher.AccountQuota
# 的 can_send()，含冷静期 + 最小间隔 + 陌生人首发，单一仲裁）。
#
# 本闸门保留的**唯一理由**：`/send`、`/send_by_uid`、`/send_image` 是 HTTP
# 端点，可能被**绕过调度器直接调用**（如 core/sender.py 的直发路径、
# 手工 curl、其它进程）。若无本闸门，这类路径将完全不受限流保护。
#
# 因此本闸门阈值**显著放宽**（兜底值 = 配置值的一半，且下限 2s）：
#   - 正常路径由调度器把关，本闸门几乎不会触发（不会双重限流）；
#   - 绕过路径仍有一道物理保险，防失控。
#
# 2026-09-28（ADR-021 残留补齐）：本闸门**新增分钟窗**（`send.per_minute_limit`）。
# 与上面「显著放宽」的最小间隔不同，分钟窗取**与调度器完全相同的硬上限**
# （同一配置键作 SSOT）—— 因为它是「每分钟 2~3 条」风控要求的**唯一兜底出口**，
# 且 `api/messages.send_image_dm` 的图片发送**不过调度器**（纯手动路径），
# 若本层不放宽反而会导致「图片绕过分钟窗」。三端点（/send、/send_by_uid、
# /send_image）共用 `_send_gate_minute` + 同一把 `_send_gate_lock` ⇒ 汇总计数、
# 无并发滑窗。
# 手动豁免**由配置显式控制**（`send.per_minute_manual_exempt`，默认 True）——
# 用户 2026-09-28 拍板「调度肯定需要手动开放，并不是直接默认定死」。
# 豁免 = 不拦手动，但手动**仍计入**分钟窗（抬高后续自动发送水位）。
#
# ⚠️ 历史：改前默认 8s 且与调度器各记各的，理论上可叠加出水 —— 已修正。
# ============================================================================
_FALLBACK_MIN_INTERVAL = float(os.environ.get("DY_SEND_MIN_INTERVAL", "8") or 8)
_FALLBACK_MAX_WAIT = float(os.environ.get("DY_SEND_MAX_WAIT", "30") or 30)
_FALLBACK_PER_MINUTE = float(os.environ.get("DY_SEND_PER_MINUTE", "3") or 0)


def _cfg_min_interval() -> float:
    """闸门最小间隔（配置中心优先，失败回落模块级兜底）。

    作为**兜底闸门**，此处再打 5 折并设 2s 下限 —— 正常路径由调度器
    裁决，本闸门只拦「绕过调度器的直发」。
    """
    v = _FALLBACK_MIN_INTERVAL
    try:
        from services.app_config import get
        _v = get("send", "min_interval")
        if _v:
            v = float(_v)
    except Exception:
        pass
    return max(2.0, v * 0.5)


def _cfg_max_wait() -> float:
    """闸门排队等待上限（秒）。兜底闸门不需要长等，固定取配置值。"""
    try:
        from services.app_config import get

        v = get("send", "max_wait")
        if v:
            return float(v)
    except Exception:
        pass
    return _FALLBACK_MAX_WAIT


def _cfg_per_minute() -> float:
    """物理闸门的**每分钟全量发送上限**（配置中心优先，失败回落兜底）。

    2026-09-28（ADR-021 残留补齐）：分钟窗原先只存在于调度器策略层
    （`dm_dispatch.AccountQuota.can_send`），而 `api/messages.send_image_dm`
    **不过调度器**、是纯手动路径，故图片发送**完全不受分钟窗约束**；
    `/send`、`/send_by_uid` 的「绕过调度器直发」亦同。

    本闸门是三个发送端点的**共同物理出口**，把分钟窗下沉至此 = 任何路径
    都无法绕过（与既有 min_interval 同层、同一把 `_send_gate_lock` 串行化）。
    与调度器策略层并列，同配置键 `send.per_minute_limit` 作 SSOT，硬上限一致。
    """
    v = _FALLBACK_PER_MINUTE
    try:
        from services.app_config import get

        _v = get("send", "per_minute_limit")
        if _v is not None:
            v = float(_v)
    except Exception:
        pass
    return v


def _cfg_per_minute_manual_exempt() -> bool:
    """手动发送是否豁免**物理层分钟窗**（`send.per_minute_manual_exempt`）。

    2026-09-28 用户拍板：「调度肯定需要手动开放，并不是直接默认定死」——
    故豁免**由配置显式控制**（默认 True = 放行手动），而非在代码里定死。
    手动发送**仍计入分钟窗**（抬高后续自动发送的水位），但不被它拦下
    —— 与调度器层同一条铁律：「门禁不拦用户显式操作」。
    若账号出现风控升级迹象，可把本项置 False，让手动同受每分钟上限约束。
    """
    v = True
    try:
        from services.app_config import get

        _v = get("send", "per_minute_manual_exempt")
        if _v is not None:
            v = bool(_v)
    except Exception:
        pass
    return v


_send_gate_lock = threading.Lock()
_send_gate_last: dict[str, float] = {}   # account -> 上次放行时间戳
_send_gate_minute: dict[str, list] = {}  # account -> 近 60s 放行时间戳列表


def _source_is_manual(source: str) -> bool:
    """是否用户显式操作（`source=="manual"`；大小写/空白容错）。"""
    return (source or "").strip().lower() == "manual"


def _send_gate_acquire(account: str, source: str = "") -> tuple[bool, float, str]:
    """尝试获取该账号的发送令牌。返回 (ok, 等待秒数, 原因码)。

    原因码（可归因，供端点回给用户）："" | "minute_limit" | "min_interval" | "timeout"。

    忙等实现（轮询 0.2s）：发送频率低（秒级间隔），锁内 sleep 可接受；
    且保证「先到先得」的发出顺序，避免两个源同时发同一会话时乱序。

    2026-09-08：闸门参数改为**每次调用时**从统一配置中心读取（热生效），
    不再是模块级常量——设置页保存后无需重启本 daemon。

    2026-09-28（ADR-021 残留补齐）：新增**每分钟全量发送上限**判定
    （`send.per_minute_limit`）。这是三个发送端点（`/send`、`/send_by_uid`、
    `/send_image`）的**共同物理出口**，任何路径（含不过调度器的图片发送、
    绕过调度器的直发）都无法绕开分钟窗。上限 >0 时，超限即**快速失败**
    （不忙等 —— 等满 60s 只会堆死事件循环），调用方拿到明确原因码后提示用户。

    2026-09-28（用户拍板「手动需要开放，不默认定死」）：`source=="manual"`
    时按 `send.per_minute_manual_exempt`（**默认 True**）决定是否豁免本窗口。
    豁免 = 不被拦，但**仍计入** `_send_gate_minute`（抬高后续自动发送的水位）
    —— 与调度器层同一条铁律「门禁不拦用户显式操作」；**由配置显式控制**，
    账号风控升级时置 False 即可让手动同受约束。
    """
    min_interval = _cfg_min_interval()
    max_wait = _cfg_max_wait()
    per_minute = _cfg_per_minute()
    manual_exempt = (_source_is_manual(source)
                     and _cfg_per_minute_manual_exempt())
    # 边界：>0 但 <1 的分数值若直接 int() 会得 0 ⇒ 恒拦一切。取整下限 1。
    minute_cap = int(per_minute) if per_minute >= 1 else (1 if per_minute > 0 else 0)
    deadline = time.time() + max_wait
    waited = 0.0
    while True:
        with _send_gate_lock:
            now = time.time()
            # 分钟窗（手动豁免时跳过「拦截」判定，但下方记账**照常**）
            if minute_cap > 0 and not manual_exempt:
                window = [t for t in _send_gate_minute.get(account, [])
                          if now - t < 60.0]
                if len(window) >= minute_cap:
                    wait = 60.0 - (now - window[0])
                    _send_gate_minute[account] = window
                    return False, max(0.0, wait), "minute_limit"
            last = _send_gate_last.get(account, 0.0)
            remain = min_interval - (now - last)
            if remain <= 0:
                _send_gate_last[account] = now
                if minute_cap > 0:
                    window = [t for t in _send_gate_minute.get(account, [])
                              if now - t < 60.0]
                    window.append(now)
                    _send_gate_minute[account] = window
                return True, waited, ""
        if now >= deadline:
            return False, waited, "min_interval"
        time.sleep(min(0.2, max(remain, 0.05)))
        waited = time.time() - (deadline - max_wait)


def _gate_error(account: str, reason: str) -> dict:
    """按闸门原因码生成**可归因**的失败返回（供三个发送端点共用）。"""
    pm = _cfg_per_minute()
    if reason == "minute_limit":
        return {"ok": False, "error": "rate_limited", "error_kind": "rate_limited",
                "reason_code": "minute_limit",
                "msg": (f"已达每分钟发送上限 {int(pm)} 条，"
                        f"请稍后再试（分钟级限流）")}
    return {"ok": False, "error": "rate_limited", "error_kind": "rate_limited",
            "reason_code": reason or "min_interval",
            "msg": f"发送过于频繁（≥{_cfg_min_interval():.0f}s/条），请稍后重试"}


def _load_send_auth(account: str, env_path: str):
    """加载发送用 auth 并刷新实时 cookie（/send 与 /send_by_uid 共用）。"""
    from dy_apis.login_api import DYLoginApi
    auth = DYLoginApi._load_auth_from_env(env_path)
    try:
        DYLoginApi.refresh_cookie_from_profile(auth, env_path)
    except Exception as _e:
        logger.warning(f"[RECV-014] " + f"[recv][{account}] 刷新实时 cookie 失败（沿用 .env）: {_e}")
    return auth


def _mark_send_delivery(body, conv_id: str, verdict: dict | None,
                        http_ok: bool = True) -> None:
    """写投递验证标记（**唯一合法的写入点族**：持有服务端响应的那一侧）。

    证据优先级：① 调用方回传的 `body.server_message_id`（跨解释器可用）；
    ② 本进程对响应字节的宽容解析 `delivery_verdict`（3.13+ 真实 prod 可用）。
    两者皆无 ⇒ **不写标记**（宁可探针报 degraded，也不制造假证据）。
    """
    try:
        from services.delivery_verify import mark_delivery_verified as _mk
        hint = str(getattr(body, "server_message_id", "") or "").strip()
        if hint:
            _mk(body.account, conv_id, server_message_id=hint, source="recv_daemon")
        else:
            _mk(body.account, conv_id, verdict=verdict, source="recv_daemon")
    except Exception as _e:  # noqa: BLE001
        logger.debug(f"[delivery-verify] 写标记跳过（不影响发送）: {_e}")


@app.post("/send")
async def send(body: SendBody) -> dict:
    """用该账号的 send_msg 回复（统一发送闸门内，P1-A）。"""
    from dy_apis.douyin_api import DouyinAPI
    from auto_dm import accounts as acc

    ib: AccountInbox | None = _state["inboxes"].get(body.account)
    if not ib:
        return {"ok": False, "error": "账号不存在"}
    peer_id = None
    with ib.lock:
        c = ib.convs.get(body.conv_id)
        if c:
            peer_id = c.peer_id
    # 2026-09-16 v0.43.39：peer_id 订正收敛到 services.conv_identity
    # （改前此处独立重写一遍，用较弱的探活 uid；现与 ConvPool 同源，
    #  用「会话池统计推断」的权威 uid，消除两处判定强度不一致）
    try:
        from services import conv_identity as _cid
        _real, _changed = _cid.correct_peer_id(body.account, body.conv_id, peer_id)
        if _changed:
            logger.warning(f"[RECV-015] " + f"[recv][{body.account}] 会话 peer_id 已订正: "
                f"{peer_id} -> {_real}（conv_id 重解析）")
        peer_id = _real
        _err = _cid.self_send_error(body.account, peer_id)
        if _err:
            return {"ok": False, "error": _err}
    except Exception:
        pass
    if not peer_id:
        return {"ok": False, "error": "无法定位会话对方 uid"}
    env_path = acc.env_path_of(body.account)
    if not env_path:
        return {"ok": False, "error": "账号 .env 路径缺失"}
    # 统一发送闸门：三源（手动/AI/直播 dispatch）一配额
    ok_gate, waited, _reason = _send_gate_acquire(body.account, body.source)
    if not ok_gate:
        logger.warning(f"[RECV-016] " + f"[recv][{body.account}] 发送闸门限流：等待 {waited:.0f}s 仍未放行"
            f"（原因 {_reason}，来源 {body.source or 'auto'}，最小间隔 {_cfg_min_interval()}s/分钟上限 {_cfg_per_minute():.0f}），快速失败")
        return _gate_error(body.account, _reason)
    try:
        auth = _load_send_auth(body.account, env_path)
        conversation_id, conversation_short_id, ticket = DouyinAPI.create_conversation(
            auth, int(peer_id)
        )
        _res = DouyinAPI.send_msg(
            auth, conversation_id, conversation_short_id, ticket, body.text
        )
        ok = _res[0] if isinstance(_res, tuple) else bool(_res)
        detail = (_res[1] if isinstance(_res, tuple) and len(_res) > 1 else "")
        _verdict = _res[2] if isinstance(_res, tuple) and len(_res) > 2 else None
        if ok:
            # 2026-09-17 修补（审查 P2-12 配套）：写入 `local:` 占位 msg_id，
            # 供 WS 回声到达时回填真实 server_message_id（避免同一条消息双写）。
            ib.add_message(body.conv_id, "me", body.text, peer_id=peer_id,
                           msg_id=f"local:{uuid.uuid4().hex[:16]}")
            # 2026-09-23（审计 P0-1）：**此处才持有服务端投递证据** ⇒ 写投递验证标记。
            _mark_send_delivery(body, conversation_id, _verdict)
            logger.info(f"[recv][{body.account}] 已回复会话 {body.conv_id[:8]}…: {body.text}")
            return {"ok": True}
        logger.warning(f"[RECV-017] " + f"[recv][{body.account}] 回复失败原因: {detail}")
        # 2026-09-28：透传结构化失败类型（消费方 dm_dispatch 只认 error_kind，不猜文案）
        _kind = (_verdict or {}).get("error_kind") or ""
        return {"ok": False, "error": detail or "send_msg 返回 False（可能触发私信风控）",
                "error_kind": _kind}
    except Exception as e:
        logger.error(f"[RECV-018] " + f"[recv][{body.account}] 回复失败: {e}")
        return {"ok": False, "error": str(e)}


@app.post("/send_by_uid")
async def send_by_uid(body: SendByUidBody) -> dict:
    """按对端数字 uid 直发私信（P1-A 新增，直播 dispatch 专用入口）。

    与 /send 同闸门、同凭证链（_load_send_auth），区别只在于定位目标的方式：
    /send 用 conv_id 反查 peer_id（需要会话已存在），本端点直接带 peer_uid——
    直播弹幕捕获的数字 user.id 无需建会话即可直发（对齐 core/sender.send_by_uid）。

    成功后同样落库（会话不存在则创建骨架），保证前端列表可见。
    """
    from dy_apis.douyin_api import DouyinAPI
    from auto_dm import accounts as acc

    ib: AccountInbox | None = _state["inboxes"].get(body.account)
    if not ib:
        return {"ok": False, "error": "账号不存在"}
    env_path = acc.env_path_of(body.account)
    if not env_path:
        return {"ok": False, "error": "账号 .env 路径缺失"}
    try:
        peer_id = int(body.peer_uid)
    except Exception:
        return {"ok": False, "error": f"peer_uid 非数字: {body.peer_uid}"}

    # 统一发送闸门：三源一配额（与 /send 同一把锁）
    ok_gate, waited, _reason = _send_gate_acquire(body.account, body.source)
    if not ok_gate:
        logger.warning(f"[RECV-019] " + f"[recv][{body.account}] 发送闸门限流(by_uid)：等待 {waited:.0f}s 未放行（原因 {_reason}，来源 {body.source or 'auto'}）")
        return _gate_error(body.account, _reason)
    try:
        auth = _load_send_auth(body.account, env_path)
        conversation_id, conversation_short_id, ticket = DouyinAPI.create_conversation(
            auth, peer_id
        )
        _res = DouyinAPI.send_msg(
            auth, conversation_id, conversation_short_id, ticket, body.text
        )
        ok = _res[0] if isinstance(_res, tuple) else bool(_res)
        detail = (_res[1] if isinstance(_res, tuple) and len(_res) > 1 else "")
        _verdict = _res[2] if isinstance(_res, tuple) and len(_res) > 2 else None
        if ok:
            # conv_id 骨架：0:1:my_uid:peer_uid（方向判定 / 落库与 WS 侧同构）
            conv_id = f"0:1:{ib.my_uid}:{peer_id}" if ib.my_uid else f"0:1::{peer_id}"
            ib.add_message(conv_id, "me", body.text, peer_id=str(peer_id),
                           # 2026-09-17 修补（审查 P2-12 配套）：local: 占位
                           msg_id=f"local:{uuid.uuid4().hex[:16]}")
            # 2026-09-23（审计 P0-1）：持有服务端投递证据 ⇒ 写投递验证标记
            _mark_send_delivery(body, conv_id, _verdict)
            logger.info(f"[recv][{body.account}] 已直发 uid={peer_id}: {body.text[:40]}")
            return {"ok": True, "conv_id": conv_id}
        logger.warning(f"[RECV-020] " + f"[recv][{body.account}] 直发 uid={peer_id} 失败: {detail}")
        _kind = (_verdict or {}).get("error_kind") or ""
        return {"ok": False, "error": detail or "send_msg 返回 False（可能触发私信风控）",
                "error_kind": _kind}
    except Exception as e:
        logger.error(f"[RECV-021] " + f"[recv][{body.account}] 直发 uid={peer_id} 异常: {e}")
        return {"ok": False, "error": str(e)}


@app.post("/send_image")
async def send_image(body: SendImageBody) -> dict:
    """发送图片私信（后端直发全链路 ①-⑥，不依赖浏览器点击）。

    2026-09-05 方案A落地：dy_apis.image_sender（AWS4 SigV4 + protobuf 27 型）。
    2026-09-06 P1-A：纳入统一发送闸门。
    """
    from auto_dm import accounts as acc

    ib: AccountInbox | None = _state["inboxes"].get(body.account)
    if not ib:
        return {"ok": False, "error": "账号不存在"}
    peer_id = None
    with ib.lock:
        c = ib.convs.get(body.conv_id)
        if c:
            peer_id = c.peer_id
    if not peer_id:
        return {"ok": False, "error": "无法定位会话对方 uid"}
    # 2026-09-16 v0.43.39：会话整理防线收敛到 conv_identity（同 /send）
    try:
        from services import conv_identity as _cid
        _real, _changed = _cid.correct_peer_id(body.account, body.conv_id, peer_id)
        if _changed:
            peer_id = _real
        _err = _cid.self_send_error(body.account, peer_id)
        if _err:
            return {"ok": False, "error": _err}
    except Exception:
        pass
    if not body.image_b64:
        return {"ok": False, "error": "image_b64 为空"}
    env_path = acc.env_path_of(body.account)
    if not env_path:
        return {"ok": False, "error": "账号 .env 路径缺失"}
    # 统一发送闸门（图片同样计入配额；且与文本共用分钟窗 —— 2026-09-28）
    ok_gate, waited, _reason = _send_gate_acquire(body.account, body.source)
    if not ok_gate:
        logger.warning(f"[RECV-024] " + f"[recv][{body.account}] 图片发送闸门限流：等待 {waited:.0f}s 未放行（原因 {_reason}，来源 {body.source or 'auto'}）")
        return _gate_error(body.account, _reason)
    try:
        import base64 as _b64

        image_data = _b64.b64decode(body.image_b64)
        # 2026-09-17 修补（OCR 审查 HIGH）：解码后复核**真实字节数**。
        # 字段级 max_length 只约束 base64 字符串长度，仍可能被 padding/空白
        # 绕过；这里对解码结果做硬上限，超限直接拒绝（不进入后续发送链路）。
        _MAX_IMG_BYTES = 20 * 1024 * 1024
        if len(image_data) > _MAX_IMG_BYTES:
            return {"ok": False,
                    "error": f"图片过大：{len(image_data)} 字节，"
                             f"上限 {_MAX_IMG_BYTES} 字节（20MB）"}
    except Exception as e:
        return {"ok": False, "error": f"image_b64 解码失败: {e}"}
    try:
        auth = _load_send_auth(body.account, env_path)
        from dy_apis.image_sender import send_image

        ok, detail, info = send_image(auth, int(peer_id), image_data,
                                      filename=body.filename or "image.jpg")
        if ok:
            # 落库：与接收侧同构（type=image, extra 含 skey/origin_url），
            # 前端直接复用图片渲染逻辑
            extra = {
                "skey": info.get("skey"),
                "oid": info.get("oid"),
                "md5": info.get("md5"),
                "data_size": info.get("data_size"),
                "width": info.get("width"),
                "height": info.get("height"),
            }
            if info.get("origin_url"):
                extra["origin_url"] = info["origin_url"]
            # 2026-09-17 修补（审查 P2-12 配套）：图片同样写入 `local:` 占位。
            # 注意：图片 text 恒为「[图片]」，回填条件里带了 text 比对，
            # 同会话短时间内连发多张图时可能误回填到第一条占位行 —— 因此
            # 图片改用 **oid 参与占位**，保证一图一坑（oid 由抖音返回，唯一）。
            # ADR-012 / R8-2（方案 B，用户 2026-09-26 决策）：
            # msg_type 统一用注册表里的 "27"，不再使用历史别名 "image"
            # —— 同一语义只保留一个名字（历史库实测 0 条 'image'，无需迁移）。
            from services.message_schema import LABEL_MEDIA as _LABEL_MEDIA
            ib.add_message(body.conv_id, "me", _LABEL_MEDIA, peer_id=peer_id,
                           msg_type="27", extra=extra,
                           msg_id=f"local:img:{info.get('oid') or uuid.uuid4().hex[:16]}")
            logger.info(f"[recv][{body.account}] 图片已发送会话 {body.conv_id[:8]}… "
                        f"oid={info.get('oid', '')[:40]}")
            return {"ok": True, "info": {k: info.get(k) for k in
                                         ("oid", "origin_url", "conversation_id")}}
        logger.warning(f"[RECV-022] " + f"[recv][{body.account}] 图片发送失败: {detail}")
        return {"ok": False, "error": detail or "send_image 返回 False"}
    except Exception as e:
        logger.error(f"[RECV-023] " + f"[recv][{body.account}] 图片发送异常: {e}")
        return {"ok": False, "error": str(e)}


@app.post("/quit")
async def quit_() -> dict:
    for ch in _state["channels"].values():
        ch.stop()
    threading.Timer(0.5, lambda: os._exit(0)).start()
    return {"ok": True}


# ----------------------------------------------------------------------------
# 主入口
# ----------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(description="私信接收守护进程")
    parser.add_argument("--accounts", required=False, help="账号名列表（逗号分隔）")
    # 2026-09-06 全局治理：兼容 daemon_launcher.py:113 历史 bug
    # 传的是单数 --account（应传 --accounts），加 alias 兜住。
    parser.add_argument("--account", required=False, help="单账号（兼容历史参数）")
    parser.add_argument("--port", type=int, required=True, help="HTTP 控制端口")
    args = parser.parse_args()

    accounts_str = args.accounts or args.account or ""

    _state["accounts"] = [a.strip() for a in accounts_str.split(",") if a.strip()]
    _state["port"] = args.port

    # 日志落盘
    try:
        from datetime import datetime
        log_dir = os.path.join(_ROOT, "logs")
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(
            log_dir, f"recv_daemon_{datetime.now().strftime('%Y%m%d')}.log"
        )
        logger.add(
            log_file,
            level="DEBUG",
            encoding="utf-8",
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
            retention="15 days",
        )
    except Exception:
        pass

    # 2026-09-13 抓真因：traceback.print_exc() 输出到 stderr，而 sidecar 的
    # stderr 被桌面端吞掉，导致 CAP-015 等异常的堆栈永远看不到。
    # 这里把 stderr 重定向到独立文件（与日志同目录），抓完即移除。
    try:
        import sys as _sys
        _stderr_log = os.path.join(
            log_dir, f"recv_stderr_{datetime.now().strftime('%Y%m%d')}.log")
        _sf = open(_stderr_log, "a", encoding="utf-8")
        _sys.stderr = _sf
        _sys.stdout = _sf
    except Exception:
        pass

    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="info")


if __name__ == "__main__":
    main()
