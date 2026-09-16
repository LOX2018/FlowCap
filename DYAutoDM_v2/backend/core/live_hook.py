# coding=utf-8
"""直播间弹幕来源：包装 DouyinLive，从 WebcastChatMessage 提取发言人直接推给调度中心。

迁移自 DY_Spider_base/auto_dm/live_hook.py。

关键：ChatMessage.user 自带 id(数字uid)/sec_uid/nickname，无需任何查询即可直发，
彻底绕开原 DYchajian 卡死的 get_user_info 风控环节。

重构变化（相对旧版）：
- 业务逻辑完整保留（DouyinLive 基类是同步的，不改）
- 提供 async wrapper `start_async` / `stop_async` 供 AutoDM 在 asyncio 上下文调用
- feed/room_stats/heat_series 数据结构保留（前端实时流的数据源）
- 心跳探活逻辑保留（登录态失效自动触发 rescan_and_rebuild）
- on_message 的弹幕处理逻辑保留（submit 改为线程安全调用）
"""
from __future__ import annotations

import asyncio
import re
import sys
import threading
import time
import gzip
from collections import deque
from typing import Any, Optional

if sys.stdout is not None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

from loguru import logger

import static.Live_pb2 as Live_pb2
from dy_live.server import DouyinLive


class LiveChatHook(DouyinLive):
    """在基类 DouyinLive 的 on_message 基础上，把公屏弹幕转成私信目标推给 dispatch。

    旧版继承 DouyinLive（同步 websocket-client），新版保留这一设计。
    AutoDM 通过 asyncio.to_thread 包装 start_ws 调用。
    """

    def __init__(self, live_id: str, auth_: Any, dispatch: Any, controller: Any = None) -> None:
        super().__init__(live_id, auth_)
        self.dispatch = dispatch
        # 所属控制器 AutoDM（用于心跳探活失败时自动触发重新扫码）；可为 None
        self.controller = controller
        # 开播状态（由 AutoDM 在启动前查询并写入）
        self.room_status: Optional[Any] = None
        # 诊断计数：本会话遇到的加密昵称 / 其中 sec_uid 缺失的数量
        self._enc_count = 0
        self._enc_no_secuid = 0
        # 运行期登录态心跳线程
        self._hb_thread: Optional[threading.Thread] = None
        self._hb_stop = threading.Event()
        # ---- 实时信息流（前端「直播监听」页数据源）----
        # feed: {type: danmaku|gift|enter|like|follow, nickname, content, ts, epoch}
        self.feed: deque = deque(maxlen=500)
        # 房间热度
        self.room_stats = {"online": 0, "likes": 0, "total_user": 0, "display": ""}
        # 热度时间序列：(epoch, online, likes)
        self.heat_series: deque = deque(maxlen=180)

    # ------------------------------------------------------------------
    # 实时信息流快照（供 API 层 getLiveStream 查询）
    # ------------------------------------------------------------------
    def reset_stream(self) -> None:
        self.feed.clear()
        self.room_stats = {"online": 0, "likes": 0, "total_user": 0, "display": ""}
        self.heat_series.clear()

    def feed_snapshot(self, limit: int = 120) -> list[dict]:
        """返回最近 limit 条信息流（前端倒序展示）。"""
        lst = list(self.feed)[-limit:]
        return [dict(x) for x in reversed(lst)]

    def room_snapshot(self) -> dict:
        return dict(self.room_stats)

    def heat_snapshot(self) -> list[list]:
        return [list(x) for x in self.heat_series]

    @staticmethod
    def _extract_num(text: str, keywords: tuple) -> int:
        """在 text 中找关键词后第一个数字，支持 '1.2万' 缩写。"""
        for kw in keywords:
            idx = text.find(kw)
            if idx >= 0:
                m = re.search(r"([\d.]+)\s*(万|k|K)?", text[idx + len(kw):])
                if m:
                    val = float(m.group(1))
                    unit = (m.group(2) or "").lower()
                    if unit == "万":
                        val *= 10000
                    elif unit == "k":
                        val *= 1000
                    return int(val)
        return 0

    @staticmethod
    def _parse_room_numbers(text: str) -> tuple[int, int]:
        """从房间统计文案精确提取 (在线人数, 点赞数)。

        抖音 RoomStatsMessage 的 display* 字段没有独立数值字段，只能从文案解析。
        常见格式（数字与关键词两种顺序都有）：
          - "X万人在线 · Y万点赞"  /  "在线 X人 · 点赞 Y"
          - "X人气值 · Y点赞"      /  "观看人数 X · 赞 Y"
        关键坑：旧 _extract_num 用 "人气" 作在线关键词时，会从 "人气值 · 3.5万点赞"
        里误抓到点赞数当在线（"人气" 在 3.5万 之前）。这里用精确正则，让在线/点赞
        各自只认自己单位，且支持「数字+单位」和「单位+数字」两种顺序，互不污染。
        """
        def _val(*patterns: str) -> int:
            for p in patterns:
                m = re.search(p, text)
                if m:
                    v = float(m.group(1))
                    unit = (m.group(2) or "").lower()
                    if unit == "万":
                        v *= 10000
                    elif unit == "k":
                        v *= 1000
                    return int(v)
            return 0

        # 在线：支持 "X人/在线" 与 "在线X人" 两种顺序；也兼容 "X人气值"
        online = _val(
            r"([\d.]+)\s*(万|k|K)?\s*(人|人在线|在线|观看)",
            r"(?:在线|当前在线|观看人数)\D*?([\d.]+)\s*(万|k|K)?",
            r"([\d.]+)\s*(万|k|K)?\s*人气",
            r"人气值?\D*?([\d.]+)\s*(万|k|K)?",
        )
        # 点赞：支持 "X点赞/赞" 与 "点赞X" 两种顺序
        likes = _val(
            r"([\d.]+)\s*(万|k|K)?\s*(点赞|赞)",
            r"(?:点赞|赞)\D*?([\d.]+)\s*(万|k|K)?",
        )
        return online, likes

    def _push_feed(self, type_: str, nickname: str, content: str, epoch: Optional[float] = None) -> None:
        self.feed.append({
            "type": type_,
            "nickname": nickname or "—",
            "content": content or "",
            "ts": time.strftime("%H:%M:%S"),
            "epoch": epoch if epoch is not None else time.time(),
        })

    def _on_room_stats(self, m: Any) -> None:
        # 抖音 RoomStatsMessage 的在线/点赞数只在 display* 字符串里，没有独立数值字段。
        # 不同直播间文案格式不一，按优先级取第一个非空字段做解析源。
        text = (
            getattr(m, "displayLong", None)
            or getattr(m, "displayMiddle", None)
            or getattr(m, "displayShort", None)
            or ""
        )
        if not text:
            return
        self.room_stats["display"] = text
        online, likes = self._parse_room_numbers(text)
        self.room_stats["online"] = online
        self.room_stats["likes"] = likes
        self.room_stats["total_user"] = max(online, self.room_stats.get("total_user") or 0)
        self.heat_series.append((time.time(), online, likes))
        # 首次（或文案变化时）打印原始文案，便于排查“热度看不到”问题
        if self.room_stats.get("_last_logged") != text:
            self.room_stats["_last_logged"] = text
            logger.info(f"[房间统计] {text}（解析→在线={online} 点赞={likes}）")

    # ------------------------------------------------------------------
    # 心跳探活（登录态失效自动重扫）
    # ------------------------------------------------------------------
    def start_heartbeat(self, interval: int = 300) -> None:
        """启动运行期登录态心跳守护线程（在 start_ws 之前调用）。

        interval: 探活间隔（秒），对应旧版 config.WS_HEARTBEAT_INTERVAL
        """
        if self._hb_thread and self._hb_thread.is_alive():
            return
        self._hb_stop.clear()
        self._hb_interval = interval
        self._hb_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        self._hb_thread.start()
        logger.info(f"[心跳] 登录态心跳已启动，间隔 {interval}s")

    def stop_heartbeat(self) -> None:
        if self._hb_thread:
            self._hb_stop.set()
            self._hb_thread = None

    def _heartbeat_loop(self) -> None:
        """周期探活：用 get_my_uid 校验登录态；失败则自动重扫。"""
        from dy_apis.douyin_api import DouyinAPI
        while not self._hb_stop.is_set():
            if self._hb_stop.wait(self._hb_interval):
                break
            if self._hb_stop.is_set():
                break
            if not self.auth_ or not getattr(self.auth_, "cookie", None):
                continue
            try:
                # 2026-09-07：UID 探活统一由 services.uid_probe 调度。
                # 心跳只【读取】调度结果，不再自己打网（此前每 300s 一次
                # 独立 query/user，与 verify_account 等叠加 = 风控信号）。
                from services.uid_probe import get_uid as _uid_get
                uid = _uid_get(getattr(self.auth_, "_account_name", "") or
                               getattr(self, "account_name", "") or "")
                if uid is None:
                    # 调度器无缓存（账号名未知）→ 退化为直接探活，保证心跳可用
                    from dy_apis.douyin_api import DouyinAPI
                    uid = DouyinAPI.get_my_uid(self.auth_)
            except Exception as e:
                logger.error(f"[LIVE-002] " + f"[心跳] 登录态探活异常（将自动重新扫码）: {e}")
                uid = None
            if not uid:
                logger.error("LIVE-003", 
                    "[心跳] 登录态失效（get_my_uid 无返回，cookie 可能过期/账号被挤下线）。\n"
                    "       自动触发重新扫码以恢复监测账号有效会话，避免弹幕昵称被加密。"
                )
                self._trigger_rescan()
                break
            else:
                logger.debug(f"[心跳] 登录态正常（uid={uid}），下次探活在 {self._hb_interval}s 后")
        logger.info("[心跳] 心跳线程已退出")

    def _trigger_rescan(self) -> None:
        """登录态失效时：关闭当前 WS 并让控制器自动重新扫码重建监听。"""
        try:
            self._should_stop = True
        except Exception:
            pass
        if getattr(self, "ws", None):
            try:
                self.ws.close()
            except Exception:
                pass
        if self.controller and hasattr(self.controller, "rescan_and_rebuild"):
            try:
                self.controller.rescan_and_rebuild()
            except Exception as e:
                logger.error(f"[LIVE-004] " + f"[心跳] 自动重新扫码失败（请手动点【重新扫码】）: {e}")
        else:
            logger.warning(f"[LIVE-005] " + "[心跳] 未绑定控制器，无法自动重扫，请手动重新扫码。")

    @staticmethod
    def _is_encrypted_nickname(nickname: str) -> bool:
        """判定弹幕昵称是否被加密/隐藏。"""
        if not nickname:
            return True
        if "*" in nickname:
            return True
        if re.fullmatch(r"用户\d+", nickname.strip()):
            return True
        return False

    # ------------------------------------------------------------------
    # on_message：protobuf 解析 + 推送 dispatch
    # ------------------------------------------------------------------
    def on_message(self, ws: Any, message: bytes) -> None:
        try:
            frame = Live_pb2.PushFrame()
            frame.ParseFromString(message)
            origin_bytes = gzip.decompress(frame.payload)
            response = Live_pb2.LiveResponse()
            response.ParseFromString(origin_bytes)
            if response.needAck:
                s = Live_pb2.PushFrame()
                s.payloadType = "ack"
                s.payload = response.internalExt.encode("utf-8")
                s.logId = frame.logId
                ws.send(s.SerializeToString(), opcode=0x02)
            for item in response.messagesList:
                try:
                    if item.method == "WebcastGiftMessage":
                        m = Live_pb2.GiftMessage()
                        m.ParseFromString(item.payload)
                        try:
                            gname = m.gift.name or "礼物"
                            gcnt = m.comboCount or 1
                            self._push_feed("gift", m.user.nickname, f"{gname} × {gcnt}")
                        except Exception:
                            pass
                    elif item.method == "WebcastChatMessage":
                        m = Live_pb2.ChatMessage()
                        m.ParseFromString(item.payload)
                        u = m.user
                        nickname = getattr(u, "nickname", None)
                        user_id = getattr(u, "id", None)
                        sec_uid = getattr(u, "sec_uid", None)
                        target = {
                            "user_id": user_id,
                            "sec_uid": sec_uid,
                            "nickname": nickname,
                            "comment": getattr(m, "content", None),
                        }
                        self._push_feed("danmaku", nickname, target.get("comment"))
                        logger.info(
                            f"[弹幕] {nickname}(uid={user_id} sec_uid={sec_uid}): {target['comment']}"
                        )
                        if self._is_encrypted_nickname(nickname):
                            self._enc_count += 1
                            if not sec_uid:
                                self._enc_no_secuid += 1
                                if self._enc_no_secuid == 1 or self._enc_no_secuid % 20 == 0:
                                    logger.error("LIVE-006", 
                                        f"[昵称加密] 检测到昵称加密且 sec_uid 为空（累计 {self._enc_no_secuid} 次）。\n"
                                        f"       这是监测账号凭证/会话异常的典型表现。\n"
                                        f"       请对该监测账号执行【重新扫码】以恢复正常会话。"
                                    )
                        if target.get("nickname"):
                            self.dispatch.submit(target)
                    elif item.method == "WebcastMemberMessage":
                        m = Live_pb2.MemberMessage()
                        m.ParseFromString(item.payload)
                        try:
                            self._push_feed("enter", m.user.nickname, "进入直播间")
                        except Exception:
                            pass
                    elif item.method == "WebcastLikeMessage":
                        m = Live_pb2.LikeMessage()
                        m.ParseFromString(item.payload)
                        try:
                            self._push_feed("like", m.user.nickname, f"点赞 × {m.count}")
                        except Exception:
                            pass
                    elif item.method == "WebcastSocialMessage":
                        m = Live_pb2.SocialMessage()
                        m.ParseFromString(item.payload)
                        if m.action == 1:
                            try:
                                self._push_feed("follow", m.user.nickname, "关注了主播")
                            except Exception:
                                pass
                    elif item.method == "WebcastRoomStatsMessage":
                        m = Live_pb2.RoomStatsMessage()
                        m.ParseFromString(item.payload)
                        try:
                            self._on_room_stats(m)
                        except Exception:
                            pass
                except Exception as e:
                    logger.warning(f"[LIVE-007] " + f"live_hook item error: {e}")
        except Exception as e:
            logger.warning(f"[LIVE-008] " + f"live_hook on_message error: {e}")

    # ------------------------------------------------------------------
    # async wrapper（供 AutoDM 在 asyncio 上下文调用）
    # ------------------------------------------------------------------
    async def start_async(self, room_info: Optional[dict] = None) -> None:
        """异步启动 WS 监听（包装同步的 start_ws）。

        本方法会阻塞直到 WS 关闭（run_forever），所以用 to_thread 包装。
        """
        await asyncio.to_thread(self.start_ws, room_info=room_info)

    async def stop_async(self) -> None:
        """异步停止 WS 监听"""
        self.stop_heartbeat()
        try:
            self._should_stop = True
        except Exception:
            pass
        if getattr(self, "ws", None):
            try:
                self.ws.close()
            except Exception:
                pass
