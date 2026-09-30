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

    def __init__(self, live_id: str, auth_: Any, dispatch: Any, controller: Any = None,
                 session_ok: Optional[bool] = None,
                 verdict_hint: str = "") -> None:
        super().__init__(live_id, auth_)
        self.dispatch = dispatch
        # 所属控制器 AutoDM（用于心跳探活失败时自动触发重新扫码）；可为 None
        self.controller = controller
        # 会话态三态（ENG-018）：True=服务端承认登录 / False=已确认未登录 /
        # None=取不到证据（探测失败或未探测）。**判据由 AutoDM 探测后传入**，
        # LiveChatHook 只消费 —— 避免两个模块各自发明一套「凭证是否可用」。
        self.session_ok: Optional[bool] = session_ok
        # 账号侧解密权结论（人可读摘要，由 AutoDM 传入）。仅用于 LIVE-006 日志
        # **如实显示本会话的账号侧判据**，避免在无证据时把脱敏归因成「凭证异常」。
        self._live_verdict_hint: str = verdict_hint or ""
        # 账号归因（ADR-002 §5.1 前置）：`[弹幕]` 行必须带账号字段，否则多账号并发下
        # 两实例交错写同一 logs/ 时无法归因（探针侧的「最近标记」推断会误判）。
        # 优先取 auth_ 上已有的 account_name（AutoDM 在启动时已 setattr），
        # 兜底用控制器上的目标账号，再兜底空串（调用处渲染为「匿名」）。
        self._account: str = (
            str(getattr(auth_, "account_name", "") or "")
            or str(getattr(controller, "target_acct", "") or "")
            or str(getattr(getattr(controller, "_acct", None), "__str__", lambda: "")()
                   if getattr(controller, "_acct", None) else "")
        )
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
        # ---- 点赞累计（2026-09-30 修：前端「实时信息流」卡头红心恒 0）----
        # 设计契约：房间「累计点赞」有两个可独立取得的来源，必须合并而不是互相覆盖——
        #   ① WS `WebcastLikeMessage`（点对点实时事件）：`count` 是**本次连击增量**，
        #      逐帧累加即得累计值（前端实测：本房间 likes 恒 0，因旧实现只推 feed 未累计）；
        #   ② `RoomStatsMessage` 文案解析（`X万点赞`）：是**绝对值**，可作校准锚。
        # 合并规则：取两者较大值，且**绝不被解析到的 0 回退清零**（0 是「未解析到」不是「无点赞」）。
        self._likes_total: int = 0        # 来自 ① 的累计增量
        self._likes_from_ws: bool = False  # 是否已收到过 WS 点赞事件
        # ---- 贡献榜（2026-09-30 新增，用户授权接入）----
        # 上游接口 `/webcast/ranklist/audience/`（上游 `douyin_api.py:1799
        # get_live_contribution_rank`），需 room_id/anchor_id/sec_uid —— 由 AutoDM
        # 启动时预查的 room_info 提供。非关键路径：拉取失败绝不阻断监听。
        self.contribution_rank: list = []
        self._room_info: dict = {}
        self._rank_reason: str = "idle"   # 最近一次拉取结论（供前端空态如实显示）
        self._rank_thread: Optional[threading.Thread] = None
        self._rank_stop = threading.Event()
        self._rank_interval: int = 60

    # ------------------------------------------------------------------
    # 实时信息流快照（供 API 层 getLiveStream 查询）
    # ------------------------------------------------------------------
    def reset_stream(self) -> None:
        self.feed.clear()
        self.room_stats = {"online": 0, "likes": 0, "total_user": 0, "display": ""}
        self.heat_series.clear()
        # 点赞两个来源同时归零；贡献榜随新会话清空
        self._likes_total = 0
        self._likes_from_ws = False
        self.contribution_rank = []

    def _merge_likes(self, stats_val: int) -> int:
        """合并「点赞累计」两个来源，写回 room_stats["likes"]。

        判据（防「解析到的 0 把真值清零」）：
          · ws_total  = WS 逐帧累加的增量（会话内只增不减）；
                        None 表示本会话尚未收到任何 LikeMessage。
          · stats_val = RoomStats 文案解析出的绝对值（可为 0 = 未解析到）。
        规则：任一为 None/未取得 → 用另一方；两者都有 → 取较大值。
        绝不把 likes 从 >0 降到 0（0 只代表「没解析到」）。
        """
        ws_total = self._likes_total if self._likes_from_ws else None
        if ws_total is None:
            merged = stats_val
        elif stats_val is None or stats_val <= 0:
            merged = ws_total
        else:
            merged = max(ws_total, stats_val)
        cur = int(self.room_stats.get("likes", 0) or 0)
        self.room_stats["likes"] = max(cur, int(merged or 0))
        return self.room_stats["likes"]

    def feed_snapshot(self, limit: int = 120) -> list[dict]:
        """返回最近 limit 条信息流（前端倒序展示）。"""
        lst = list(self.feed)[-limit:]
        return [dict(x) for x in reversed(lst)]

    def room_snapshot(self) -> dict:
        return dict(self.room_stats)

    def heat_snapshot(self) -> list[list]:
        return [list(x) for x in self.heat_series]

    # ------------------------------------------------------------------
    # 贡献榜（2026-09-30 新增；用户授权「通过上游情报编写接口」）
    # ------------------------------------------------------------------
    def set_room_info(self, room_info: Optional[dict]) -> None:
        """快照启动时预查的直播间身份（贡献榜所需的 room_id/anchor_id/sec_uid）。

        仅在有值时覆盖 —— 重连时基类 `on_close → self.start_ws()` 传的 room_info
        为 None，此时必须保留首次的身份，不能被清空。
        """
        if room_info and isinstance(room_info, dict) and room_info.get("room_id"):
            self._room_info = dict(room_info)

    @staticmethod
    def _normalize_rank(data: Any) -> list[dict]:
        """把上游贡献榜 JSON 归一化为 [{rank,uid,nickname,score,score_text,avatar}]。

        ⚠️ 上游 `get_live_contribution_rank` 只 `return response.json()`，**未提供
        解析器**（本项目情报库登记为「未实测」）⇒ 必须防御式解析：容器键与字段均多
        候选兜底，绝不因结构漂移抛错。首跑请核对日志里打印的原始片段再校准键名。
        """
        if not isinstance(data, dict):
            return []
        if data.get("status_code") not in (0, None):
            return []
        d = data.get("data") if isinstance(data.get("data"), dict) else data
        if not isinstance(d, dict):
            return []
        arr = None
        for k in ("ranks", "rank_list", "list", "users", "items", "audience"):
            if isinstance(d.get(k), list):
                arr = d[k]
                break
        if arr is None:
            return []
        out: list[dict] = []
        for it in arr:
            if not isinstance(it, dict):
                continue
            u = it.get("user") if isinstance(it.get("user"), dict) else {}
            nick = (it.get("nickname") or u.get("nickname")
                    or it.get("nick_name") or u.get("nick_name") or "")
            uid = (u.get("id_str") or u.get("id") or it.get("user_id")
                   or it.get("user_id_str") or it.get("uid") or "")
            try:
                score = int(it.get("score") or it.get("value")
                            or it.get("rank_score") or 0)
            except Exception:
                score = 0
            score_text = (it.get("score_str") or it.get("score_text")
                          or it.get("score_display") or it.get("score_blur_text") or "")
            av = it.get("avatar_thumb") or u.get("avatar_thumb") or it.get("avatar") or {}
            avatar = ""
            if isinstance(av, dict):
                url_list = av.get("url_list") or []
                avatar = str(url_list[0]) if url_list else ""
            out.append({
                "uid": str(uid or ""),
                "nickname": str(nick or ""),
                "score": score,
                "score_text": str(score_text or ""),
                "avatar": avatar,
            })
        out.sort(key=lambda x: x["score"], reverse=True)
        for i, r in enumerate(out, 1):
            r["rank"] = i
        return out

    @staticmethod
    def fetch_rank(room_info: dict, auth_: Any) -> list[dict]:
        """调用上游贡献榜接口并归一化；任何异常 → 返回 []（非关键路径，不阻断监听）。"""
        try:
            rid = str((room_info or {}).get("room_id") or "")
            aid = str((room_info or {}).get("anchor_id") or "")
            sec = str((room_info or {}).get("sec_uid") or "")
            if not rid or not aid:
                logger.info("[LIVE-040] [贡献榜] 缺 room_id/anchor_id（匿名进房无 anchor），跳过")
                return []
            if auth_ is None or not getattr(auth_, "cookie", None):
                logger.info("[LIVE-040] [贡献榜] 当前无可用凭证，跳过（贡献榜需登录态）")
                return []
            from dy_apis.douyin_api import DouyinAPI
            data = DouyinAPI.get_rank_list(auth_, rid, aid, sec)
            rows = LiveChatHook._normalize_rank(data)
            if rows:
                logger.info(f"[贡献榜] 解析到 {len(rows)} 条（首条={rows[0].get('nickname')} "
                            f"score={rows[0].get('score')}）")
            elif isinstance(data, dict):
                logger.warning(f"[LIVE-041] [贡献榜] 未解析出条目 status_code="
                               f"{data.get('status_code')} 原始片段: {str(data)[:300]}")
            return rows
        except Exception as e:
            logger.warning(f"[LIVE-042] [贡献榜] 拉取异常（非关键路径，忽略）: {e}")
            return []

    def _rank_loop(self) -> None:
        while not self._rank_stop.is_set():
            try:
                if self._room_info:
                    rows = self.fetch_rank(self._room_info, self.auth_)
                    if rows:
                        self.contribution_rank = rows
                        self._rank_reason = "ok"
                    elif not self.contribution_rank:
                        # 失败/空 **不覆盖**已有非空榜（避免抖动清空）
                        self._rank_reason = "empty"
            except Exception as e:  # noqa: BLE001
                logger.debug(f"[贡献榜] 轮询异常: {e}")
            if self._rank_stop.wait(self._rank_interval):
                break

    def start_rank_poll(self, interval: int = 60) -> None:
        if self._rank_thread and self._rank_thread.is_alive():
            return
        self._rank_interval = interval
        self._rank_stop.clear()
        self._rank_thread = threading.Thread(target=self._rank_loop, daemon=True)
        self._rank_thread.start()

    def stop_rank_poll(self) -> None:
        self._rank_stop.set()
        self._rank_thread = None

    # ------------------------------------------------------------------
    # WS 生命周期覆盖（建连前快照身份 + 启动贡献榜轮询；不改动任何 WS 逻辑）
    # ------------------------------------------------------------------
    def start_ws(self, room_info: Optional[dict] = None) -> None:
        """覆盖基类：建连前记录 room_info 并启动贡献榜轮询，随后完全委托基类。

        基类 `start_ws` 阻塞到 WS 关闭（`on_close` 内的自动重连会再次进入本覆盖），
        故这里是「每轮建连前」的幂等前置步骤。
        """
        try:
            self.set_room_info(room_info)
        except Exception:
            pass
        try:
            self.start_rank_poll()
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[贡献榜] 轮询启动失败: {e}")
        super().start_ws(room_info)

    def on_close(self, ws: Any, close_status_code: Any = None, close_msg: Any = None) -> None:
        """覆盖基类：仅在「真停止」时停贡献榜轮询（重连的空隙不停，与 ENG-015 一致）。"""
        if getattr(self, "_should_stop", False):
            try:
                self.stop_rank_poll()
            except Exception:
                pass
        super().on_close(ws, close_status_code, close_msg)

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
        # 2026-09-30 修：点赞走**合并**（WS 累计 ⊕ 文案绝对值），不再被解析到的 0 清零。
        merged_likes = self._merge_likes(likes)
        self.room_stats["total_user"] = max(online, self.room_stats.get("total_user") or 0)
        self.heat_series.append((time.time(), online, merged_likes))
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
                logger.error(f"[LIVE-003] " + "[心跳] 登录态失效（get_my_uid 无返回，cookie 可能过期/账号被挤下线）。\n"
                    "       自动触发重新扫码以恢复监测账号有效会话，避免弹幕昵称被加密。")
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
        """判定弹幕昵称是否被**脱敏**（服务端下发的隐藏形态）。

        ⚠️ 判据必须与真相一致（2026-09-22 实测修正）：
        原实现含「昵称形如『用户』+ 纯数字即判为加密」这一条 —— 它**会误报真实昵称**。
        实测反例：`run_20260918_170957.log` 的 11:18 健康帧里出现昵称
        `用户5927163527973`，其 `uid=805306322911928`、`sec_uid=MS4wLjABAAAA…` 完整，
        属**真实昵称**（该帧可捕获评论并派发私信）。按旧判据它被判为「加密」，
        从而在**完全正常**的会话里刷出 `LIVE-006` 告警，把人引向「凭证异常」的误判。

        真相判据与本项目其他处一致：**脱敏 ⇔ `uid == 111111` 且 `sec_uid` 为空**
        （`***` 只是该形态的伴随表现，单独出现不足以定性）。因此本函数只保留 `***`
        这一形态信号，「用户+纯数字」**不再**作为脱敏判据；最终定性以帧内 uid/sec_uid 为准
        （见调用处的 `not sec_uid` 分支）。
        """
        if not nickname:
            return True
        return "*" in nickname

    # ------------------------------------------------------------------
    # 凭证可用性判据（ENG-017：直播流连接与凭证解耦）
    # ------------------------------------------------------------------
    # 2026-09-21（ENG-018）：把「凭证可用」升级为**会话态判据**。
    #
    # 原先的判据是「auth 带非空 cookie」——实测这是**代理信号**（本项目
    # §〇·己·3 明令禁止）：张老师账号 cookie 70 个字段齐全，但服务端判其
    # 「未登录」，直播帧下发脱敏数据（`uid=111111` + `威***` + `sec_uid` 空），
    # 与「完全不带 cookie 的真匿名」现象**逐字相同**。判据为真、能力为零。
    #
    # 正确判据 = **主站会话被服务端承认**（`user/profile/self/` 是否返回
    # `status_code=0` 且带 `MS4wLjABAAAA` 段），由 AutoDM 在启动时写入
    # `self.session_ok`。三态（§〇·己·2）：True=已确认登录 / False=已确认
    # 未登录 / None=取不到证据（探测失败或未跑）→ 诚实降级，保留原状态。
    def _has_credential(self) -> bool:
        """当前监听是否持有**被服务端承认的**可用凭证。"""
        if not (self.auth_ is not None and getattr(self.auth_, "cookie_str", "")):
            return False
        sess = getattr(self, "session_ok", None)
        if sess is None:
            # 未探测到结论 → 不擅自改写既有行为（诚实降级），仅在日志留痕
            logger.debug("[LIVE-034] 会话态未知（未探测或探测失败），按「有 cookie」继续")
            return True
        return bool(sess)

    def _anon_live_info(self) -> dict:
        """匿名获取直播间信息（room_id / ttwid / 开播状态）。

        2026-09-21（ENG-017）：**不走 `DouyinAPI.get_live_info`** ——
        该函数签名要求 `auth_` 非空（内部 `cookies=auth_.cookie`），传 None 会
        `AttributeError`；而它是并发写者正在维护的文件，本会话不叠加改动。

        这里自建等价的匿名请求：GET 直播间页面 → 响应里取 ttwid、页面里正则取
        room_id/user_id/status。全程**不带任何账号身份**（ttwid 为服务端
        下发的设备级标识），属匿名观看，不触碰账号风控面。

        实测（本会话）：匿名 GET → HTTP 200、~905KB、含 roomId / ttwid(127)。
        """
        import re as _re
        import requests
        from builder.header import HeaderBuilder
        from utils.dy_util import tls_verify

        url = f"https://live.douyin.com/{self.live_id}"
        sess = requests.Session()
        r = sess.get(url, headers={
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "accept-language": "zh-CN,zh;q=0.9",
            "cache-control": "no-cache",
            "referer": "https://live.douyin.com/?from_nav=1",
            "user-agent": HeaderBuilder.ua,
        }, timeout=20, verify=tls_verify())
        if r.status_code != 200:
            logger.error(f"[LIVE-031] " + f"[live-ws] 匿名进房失败 HTTP {r.status_code}")
            return {}
        ttwid = (sess.cookies.get_dict() or {}).get("ttwid") or ""

        # 页面脚本里取 room_id / user_id / status（与官方解析同源，但匿名）
        room_id = user_id = status = None
        for m in _re.finditer(r'\\"roomId\\":\\"(\d+)\\"', r.text):
            room_id = m.group(1)
            break
        if not room_id:
            m = _re.search(r'"roomId"\s*:\s*"(\d+)"', r.text)
            if m:
                room_id = m.group(1)
        m = _re.search(r'\\"user_unique_id\\":\\"(\d+)\\"', r.text) or \
            _re.search(r'"user_unique_id"\s*:\s*"(\d+)"', r.text)
        if m:
            user_id = m.group(1)
        m = _re.search(r'\\"status\\":(\d+)', r.text)
        if m:
            status = m.group(1)

        if not room_id:
            logger.error(f"[LIVE-032] " + "[live-ws] 匿名进房未解析出 room_id"
                         "（页面结构可能变更 / 直播间不存在）")
            return {}
        return {
            "room_id": room_id,
            "user_id": user_id,
            "ttwid": ttwid,
            "room_status": int(status) if status is not None else None,
            "room_title": "",
        }

    def _anon_cookie(self) -> str:
        """无凭证时用于建立 WS 的匿名 cookie（页面侧下发的 ttwid）。

        ttwid 是**设备级**标识，由服务端在无登录态访问直播间页面时下发，
        不携带任何账号身份 —— 用它建连属匿名观看，不触碰账号风控面。
        取不到时返回空串（此时仍按空 cookie 尝试，由服务端裁决）。
        """
        try:
            info = self._anon_live_info()
            return str((info or {}).get("ttwid") or "")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[LIVE-027] " + f"[live-ws] 匿名 ttwid 获取失败（将按空 cookie 尝试）: {e}")
        return ""

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
            # 成功解出一帧 → 归零连续失败计数
            self._frame_fail_streak = 0
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
                        # 🔴 账号归因必须落在**行内**（ADR-002 §5.1 前置，2026-09-22）。
                        # 为何不能在探针侧靠「最近一条『使用前端指定账号…』标记」推断：
                        # 多账号并发（ADR-002）下两个引擎实例**交错写同一 logs/**，
                        # 实测形态为 `A标记 → B标记 → A弹幕` —— 按「最近一条标记」
                        # 会把 A 的弹幕记到 B 名下。行内字段是唯一在真并发下可靠的归因。
                        logger.info(
                            f"[弹幕][账号={self._account or '匿名'}] "
                            f"{nickname}(uid={user_id} sec_uid={sec_uid}): "
                            f"{target['comment']}"
                        )
                        if self._is_encrypted_nickname(nickname):
                            self._enc_count += 1
                            if not sec_uid:
                                self._enc_no_secuid += 1
                                if self._enc_no_secuid == 1 or self._enc_no_secuid % 20 == 0:
                                    # ⚠️ 归因必须落在**已有取证**的判据上，不得猜账号。
                                    # 实测（2026-09-22）：同一 `uid=111111` + 空 sec_uid
                                    # 有两种来源，仅凭帧内数据不可区分 ——
                                    #   ① 监测账号无解密权（凭证被服务端降权）；
                                    #   ② 该直播间开启「隐藏观众信息」（房间级开关，
                                    #      见 saermart/DouyinLiveWebFetcher issue #98）。
                                    # 因此这里只报**事实 + 两种可能**；「请重新扫码」这一
                                    # 确定性动作交给与账号会话态绑定的 `LIVE-035`。
                                    _v = getattr(self, "_live_verdict_hint", "") or (
                                        "本会话未取到监测账号解密权结论（LIVE-036）")
                                    logger.error(
                                        f"[LIVE-006] [昵称加密] 检测到昵称加密且 sec_uid 为空"
                                        f"（累计 {self._enc_no_secuid} 次）。\n"
                                        f"       本会话账号侧判据：{_v}\n"
                                        f"       该现象有两种可能来源，仅凭帧内数据不可区分：\n"
                                        f"         ① 监测账号无解密权（凭证被服务端降权）—— 处置见 LIVE-035；\n"
                                        f"         ② 该直播间开启「隐藏观众信息」（房间级开关）——\n"
                                        f"            验证法：换一个**已知有解密权**的账号进同一房间，"
                                        f"若同样脱敏即属此类。")
                        if target.get("nickname"):
                            # ADR-007 / C-06（2026-09-24）：先「沉淀」再提交。
                            # 沉淀记录该 UID 的聚合弹幕/关键词分/窗口，供发送闸门
                            # 的窗口延迟与高价值过滤使用（能力默认休眠：窗口/阈值=0）。
                            try:
                                from services.dm_dispatch import get_dispatcher as _gd
                                _gd().uid_sink.mark_seen(
                                    self._account or "", user_id or "",
                                    nickname or "", "live",
                                    target.get("comment") or "")
                            except Exception:
                                logger.debug('[SILENT-00] core.live_hook: mark_seen failed')
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
                        # 2026-09-30 修：累计点赞 —— 旧实现只推 feed，room_stats["likes"]
                        # 从不增长（前端红心恒 0）。`count` 是本次连击增量，逐帧累加即累计值。
                        try:
                            _c = int(getattr(m, "count", 0) or 0)
                            if _c > 0:
                                self._likes_total += _c
                                self._likes_from_ws = True
                                self._merge_likes(0)
                        except Exception:
                            pass
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
            # 2026-09-17 修补（OCR 审查 HIGH —— 弹幕静默丢弃）：
            # 整帧解码（ParseFromString / gzip.decompress / ack 回帧）共用这一个
            # except，任一步异常都会让**该帧全部弹幕消息**丢失，而原先只留一行
            # warning——既无计数也无重试，且 `_heartbeat_loop` 只在 get_my_uid
            # 无返回时才重扫，对「WS 活着但消息全丢」完全无感知。
            # 现增加：连续失败计数；连续 N 次（默认 5）后触发重扫，并升级为 error。
            self._frame_fail_streak = getattr(self, "_frame_fail_streak", 0) + 1
            self._frame_fail_total = getattr(self, "_frame_fail_total", 0) + 1
            _streak = self._frame_fail_streak
            logger.warning(
                f"[LIVE-008] live_hook on_message error"
                f"（连续第 {_streak} 次，累计 {self._frame_fail_total} 次）: {e}")
            if _streak >= 5:
                self._frame_fail_streak = 0
                logger.error(
                    f"[LIVE-009] 弹幕帧连续 {_streak} 次解析失败，"
                    f"疑似凭证/协议异常 → 触发重新扫码以恢复捕获")
                try:
                    if self.controller is not None:
                        self.controller.rescan_and_rebuild()
                    else:
                        logger.warning(
                            "[LIVE-009] 未绑定控制器，无法自动重扫，请手动点【重新扫码】")
                except Exception as e2:  # noqa: BLE001
                    logger.error(f"[LIVE-009] 自动重扫失败（请手动点【重新扫码】）: {e2}")

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
