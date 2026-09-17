# coding=utf-8
"""AutoDM 主控（重构版）

迁移自 DY_Spider_base/auto_dm/run.py。

重构变化（相对旧版）：
- 5 个标志位（_running/listen_active/hard_stopped/no_new/paused）
  → 单一 EngineState enum
- threading.Thread → asyncio.Task（适配 FastAPI 异步栈）
- 同步业务逻辑（_build_one_auth / _verify_credential / LiveChatHook）
  通过 asyncio.to_thread 包装，不破坏 dy_apis/builder/utils 等已迁移代码
- is_running() 多标志组合 → 单一真源 self.state
- check_room_live 保留（同步，用 to_thread 包装）
- rescan_and_rebuild 保留（同步逻辑 + 状态切换）
"""
from __future__ import annotations

import asyncio
import json
import os
import random
import threading
import time
from typing import Any, Optional

from dotenv import load_dotenv
from loguru import logger

from config import settings
from models.enums import EngineState, RecordStatus
from models.task import TaskConfig
from core.dispatch import DispatchCenter
from core.live_hook import LiveChatHook


def _rec_to_dict(rec) -> dict:
    """把 SendRecord 模型转成可 JSON 序列化的 dict（历史任务 records 快照）。"""
    if rec is None:
        return {}
    if isinstance(rec, dict):
        return dict(rec)
    out = {}
    for k, v in rec.__dict__.items():
        if k.startswith("_"):
            continue
        try:
            json.dumps(v)
            out[k] = v
        except (TypeError, ValueError):
            out[k] = str(v)
    return out


def check_room_live(auth: Any, live_id: str):
    """判断直播间是否在直播。

    返回 (is_live, room_status, room_title, info)。
    迁移自 run.py 的 check_room_live。
    """
    try:
        from dy_apis.douyin_api import DouyinAPI
        info = DouyinAPI.get_live_info(auth, live_id)
        if not info or not isinstance(info, dict):
            logger.warning(f"[ENG-001] " + "[直播间状态] get_live_info 返回空，保守视为已开播以免误阻断监听")
            return True, None, "", None
        status = info.get("room_status")
        title = info.get("room_title", "")
        is_live = str(status) == "2"
        logger.info(
            f"[直播间状态] room_status={status} title={title!r} -> "
            f"{'直播中' if is_live else '未开播/下播'}"
        )
        return is_live, status, title, info
    except Exception as e:
        logger.warning(f"[ENG-002] " + f"[直播间状态] 查询异常（保守视为已开播）: {e}")
        return True, None, "", None


class AutoDM:
    """引擎主控（单例，挂在 app.state.adm）

    状态机（单一真源）：
        IDLE → STARTING → RUNNING ⇄ PAUSED
                                ↘ STOPPING → STOPPED → IDLE

    两条线路（与旧版一致）：
        - 监听线路（WS 弹幕）：state in (STARTING, RUNNING, PAUSED) 时活跃
        - 私信线路（DispatchCenter 队列）：state != IDLE/STOPPED 时活跃
    软停止（stop_hard=False）：监听关闭，存量队列发完 → STOPPED
    硬停止（stop_hard=True）：监听关闭 + 队列清空 → STOPPED
    """

    def __init__(self) -> None:
        self.state: EngineState = EngineState.IDLE
        self.live_url: Optional[str] = None
        self.live_id: Optional[str] = None
        self.limit: int = settings.max_target
        self.sent_count: int = 0
        # 私信词库（前端 Tasks 页配置，运行时由 save_dm_pool 写入）
        self.dm_template: list[dict] = list(getattr(settings, "dm_pool", []) or [])
        self.delay_range: tuple[int, int] = tuple(getattr(settings, "delay_range", [40, 65]))
        self.interval: float = float(getattr(settings, "interval", 60.0))
        self.force_rescan: bool = bool(getattr(settings, "force_rescan", False))
        # captured_count 是只读 property（从 dispatch.records 派生），无需初始化
        self.status_msg: str = "未启动"
        self.room_title: str = ""

        # 凭证
        self.auth: Any = None              # 发送账号 auth
        self.monitor_auth: Any = None      # 监测账号 auth
        self.monitor_env_path: Optional[str] = None
        self.sender_env_path: Optional[str] = None

        # 子系统
        self.dispatch: Optional[DispatchCenter] = None
        self.live: Optional[LiveChatHook] = None

        # 运行任务
        self._task: Optional[asyncio.Task] = None
        self._stop_event = asyncio.Event()

    @property
    def is_running(self) -> bool:
        """单一真源判定（替代旧版 is_running() 的多标志组合）"""
        return self.state in (EngineState.STARTING, EngineState.RUNNING, EngineState.PAUSED, EngineState.STOPPING)

    # ------------------------------------------------------------------
    # 凭证构造（迁移自 run.py _build_one_auth，同步逻辑）
    # ------------------------------------------------------------------
    @staticmethod
    def _credential_age(env_path: str) -> int:
        """返回 .env 距上次写入的秒数；不存在返回 -1。"""
        try:
            if os.path.exists(env_path):
                return int(os.path.getmtime(env_path))
        except Exception:
            pass
        return -1

    def _build_one_auth(self, env_path: str, force_fresh: bool = False, max_age: int = 600) -> Any:
        """为指定 .env 路径构造并补全签名的 auth（同步，迁移自旧版）。

        force_fresh: True 时强制重新扫码
        max_age:     凭证最大允许年龄（秒），超过则强制重扫
        """
        from builder.auth import DouyinAuth
        from auth_helper import enrich_auth
        from vbrowser import app_root

        # 安全加载 .env（优先用传入的 env_path，未指定则退化到默认 .env）
        if not env_path:
            env_path = os.path.join(app_root(), ".env")
        elif not os.path.isabs(env_path):
            env_path = os.path.join(app_root(), env_path)
        if os.path.exists(env_path):
            load_dotenv(env_path, override=True)
        # P3（09 台账 5.3）：进程级 os.environ 是多账号交叉污染源——
        # 本函数只负责构造「这一个 env_path」的 auth，凭证值直接从文件读。
        from dotenv import dotenv_values
        # 会员体系（v0.37.0）：会员空间内走解密视图（.enc）
        _vals = None
        try:
            from services import member_ctx
            if member_ctx.is_member_env(env_path):
                _vals = member_ctx.parse_env_dict(env_path)
        except Exception:
            _vals = None
        if _vals is None:
            _vals = dotenv_values(env_path) if os.path.exists(env_path) else {}
        cookies = _vals.get("DY_COOKIES") or ""

        if force_fresh:
            logger.info(f"[auth] 强制重新扫码（忽略现有凭证）：{env_path}")
        elif max_age and cookies and env_path:
            mtime = self._credential_age(env_path)
            if mtime > 0:
                age = int(time.time()) - mtime
                if age > max_age:
                    logger.warning("AUTH-011", 
                        f"[auth] 凭证年龄={age}s(> {max_age}s)，视为非实时会话，"
                        f"强制重扫以避免弹幕昵称被加密。"
                    )
                    force_fresh = True

        if force_fresh:
            # 强制重扫：扫码前【不清空 .env】，新凭证成功后才覆盖；
            # 浏览器由 vbrowser.launch_async(force=True) 使用【临时 profile】启动，
            # 绝不动该账号持久化 profile，避免与「查看模式」打开的浏览器抢 profile 锁导致
            # 浏览器崩溃落到 about:blank（真实事故根因）。
            from dy_apis.login_api import DYLoginApi
            api = DYLoginApi()
            try:
                auth = asyncio.run(api.get_login_auth(headless=False, env_path=env_path, force=True))
                return auth
            except RuntimeError:
                loop = asyncio.new_event_loop()
                try:
                    auth = loop.run_until_complete(
                        api.get_login_auth(headless=False, env_path=env_path, force=True)
                    )
                    return auth
                finally:
                    loop.close()

        auth = DouyinAuth()
        if cookies:
            auth.perepare_auth(cookies, "", "")
        else:
            logger.warning(f"[AUTH-012] " + "[auth] 未检测到 DY_COOKIES，将自动打开浏览器扫码登录获取。")
        if not (auth.ticket and auth.private_key):
            logger.info(
                "[auth] 未检测到有效私信签名，将弹出浏览器扫码登录窗口，"
                "请在浏览器中完成抖音扫码。"
            )
        auth, _fresh_cookie = enrich_auth(
            auth, cookies_dy=cookies, headless=False,
            user_data_dir="pw_profile_dm", env_path=env_path
        )
        return auth

    def _verify_credential(self, auth: Any) -> bool:
        """轻量校验凭证是否仍有效（cookie + 私信签名）。迁移自旧版。"""
        _has_sign = (
            getattr(auth, "ticket", None)
            and getattr(auth, "client_cert", None)
            and getattr(auth, "private_key", None)
        )
        if not _has_sign:
            logger.error("AUTH-013", 
                "[auth] 私信签名三件套缺失(ticket/client_cert/private_key)。\n"
                "       请删除 .env 中 DY_TICKET/DY_TS_SIGN/DY_CLIENT_CERT/DY_PRIVATE_KEY 四行，\n"
                "       再点该账号【重新扫码】抓取签名后启动。"
            )
            return False
        try:
            from dy_apis.douyin_api import DouyinAPI
            uid = DouyinAPI.get_my_uid(auth)
            if not uid:
                logger.error(f"[AUTH-014] " + "[auth] 无法获取自身 uid（cookie 可能已失效），请对该账号执行【重新扫码】后再启动。")
                return False
            DouyinAPI.create_conversation(auth, int(uid))
            logger.info(f"[auth] 私信签名预检通过（uid={uid}，create_conversation 可被服务端接受）")
            return True
        except Exception as e:
            msg = str(e)
            if "INVALID_REQUEST" in msg or "KICK" in msg:
                logger.error("AUTH-015", 
                    "[auth] 私信签名预检被拒（create_conversation 返回 INVALID_REQUEST/KICK）。\n"
                    "       建议点【重新扫码】重新抓取最新凭证。"
                )
            elif "login" in msg.lower() or "unauthorized" in msg.lower() or "401" in msg:
                logger.error(f"[AUTH-016] " + "[auth] 登录态校验失败（create_conversation 报未登录），cookie 可能已失效。")
            else:
                logger.error(f"[AUTH-017] " + f"[auth] 私信签名预检异常: {msg}")
            return False

    # ------------------------------------------------------------------
    # 引擎控制（async）
    # ------------------------------------------------------------------
    def _normalize_dm_pool(self, pool: Any) -> list[dict]:
        """把词库输入归一到 [{text, enabled}]（两种形态都要吃得下）。

        - `list[str]`（TaskConfig 形态）：用 `self.dm_template` 的既有启用位合并，
          避免前端只回传「已启用文案」时把其余文案的启用状态丢掉。
        - `list[dict]`（RoomConfig 形态）：自带 enabled，原样采用。

        合并后仍为空时退回 settings 词库（怕调用方传空导致无文案可发）。
        """
        existing: dict[str, bool] = {}
        for t in (self.dm_template or []):
            text = t.get("text", "") if isinstance(t, dict) else str(t)
            en = t.get("enabled", True) if isinstance(t, dict) else True
            existing[str(text)] = en
        merged: list[dict] = []
        for t in pool or []:
            if isinstance(t, dict):
                text = str(t.get("text", "") or "").strip()
                en = bool(t.get("enabled", True))
            else:
                text = str(t or "").strip()
                en = existing.get(text, True)
            if text:
                merged.append({"text": text, "enabled": en})
        if not merged:
            for item in getattr(settings, "dm_pool", []) or []:
                text = item.get("text", "") if isinstance(item, dict) else str(item)
                if text:
                    merged.append({"text": str(text), "enabled": True})
        return merged

    def _apply_config(self, config: TaskConfig) -> None:
        """把 TaskConfig 落到运行时字段（任务容器回读 / 监控展示用）。

        dm_pool 只保留文案，启用状态合并自 adm.dm_template（[{text, enabled}]）。
        """
        self.live_url = config.live_url
        self.limit = max(1, int(config.max_target))
        self.interval = float(config.interval)
        self.delay_range = tuple(config.delay_range or [40, 65])
        self.force_rescan = bool(config.force_rescan)
        self._acct = getattr(config, "acct", None)
        self.dm_template = self._normalize_dm_pool(config.dm_pool)
        self.pick_dm_message = self._make_pick_dm_message()

    def _make_pick_dm_message(self):
        """从已启用词库随机抽一条文案（修复旧版恒取 dm_pool[0] 导致文案重复被风控）。"""
        active = []
        for t in (self.dm_template or []):
            if isinstance(t, dict):
                if not t.get("enabled", True):
                    continue
                text = str(t.get("text", "")).strip()
            else:
                text = str(t).strip()
            if text and text not in active:
                active.append(text)
        if not active:
            return None

        def _pick():
            return random.choice(active)

        return _pick

    def _snapshot_config(self) -> dict:
        """当前任务的配置快照（任务中心「进入/复用」回读数据源）。"""
        dr = tuple(self.delay_range or [40, 65])
        return {
            "live_url": self.live_url or "",
            "live_id": self.live_id or "",
            "max_target": int(self.limit),
            "interval": float(self.interval),
            "delay": f"{dr[0]},{dr[1]}" if len(dr) == 2 else str(list(dr)),
            "force_rescan": bool(self.force_rescan),
            "acct": getattr(self, "_acct", None),
            "dm_pool": [
                {"text": t.get("text", ""), "enabled": t.get("enabled", True)}
                for t in (self.dm_template or [])
                if isinstance(t, dict) and t.get("text")
            ],
            "status_msg": self.status_msg,
        }

    def snapshot(self) -> dict:
        """任务容器快照：直播监听页 / 任务中心回读当前任务进程的统一视图。

        聚合引擎状态 + 直播流 + 调度进度 + 发送记录为单个 JSON，前端两页共用。
        """
        live = self.live
        ws_active = bool(
            live
            and getattr(live, "ws", None)
            and not getattr(live, "_should_stop", True)
        )
        records = self.dispatch.records_list() if self.dispatch else []
        sent = sum(1 for r in records if getattr(r, "status", None) == RecordStatus.SENT)
        queue = self.dispatch.queue_size() if self.dispatch else 0
        online = 0
        if live and getattr(live, "room_stats", None):
            try:
                online = int((getattr(live, "room_stats") or {}).get("online", 0) or 0)
            except Exception:
                online = 0
        state = self.state.value
        return {
            "ok": True,
            "has_task": self.is_running or state not in ("idle",),
            "task_id": getattr(self, "_task_history_id", None),
            "engine_state": state,
            "status_msg": self.status_msg,
            "config": self._snapshot_config(),
            "live": {
                "alive": ws_active,
                "listening": ws_active,
                "online": online,
                "room_title": getattr(self, "room_title", ""),
                "dm_running": self.is_running,
                "dm_paused": state == "paused",
            },
            "counts": {
                "sent": sent,
                "captured": len(records),
                "queue": queue,
                "limit": int(self.limit),
            },
            "records": [r if isinstance(r, dict) else _rec_to_dict(r) for r in records],
        }

    async def start(self, config: TaskConfig) -> None:
        """启动引擎"""
        if self.state not in (EngineState.IDLE, EngineState.STOPPED):
            raise RuntimeError(f"当前状态 {self.state.value} 无法启动")
        self.state = EngineState.STARTING
        # 从 live_url 解析出真实直播间号 web_rid（live.douyin.com/<web_rid>）
        try:
            from link_resolve import resolve_live_id
            from auto_dm.accounts import current_name
            self.live_id, _ = resolve_live_id(
                config.live_url, account_name=self._acct or current_name())
        except Exception:
            self.live_id = config.live_url
        self._apply_config(config)
        self.status_msg = "启动中"
        self._stop_event.clear()

        # 记录历史任务（任务中心展示），附配置快照供「复用」回读
        try:
            from auto_dm.accounts import current_name
            acct = self._acct or current_name()
            from tasks_history import start_task
            self._task_history_id = start_task(
                acct, self.live_id or config.live_url, config=self._snapshot_config()
            )
            logger.info(f"[history] 记录历史任务: 账号={acct} live={self.live_id} id={self._task_history_id}")
        except Exception as e:
            logger.warning(f"[ENG-003] " + f"[history] 记录历史任务失败: {e}")
            self._task_history_id = None

        logger.info(f"引擎启动: live_url={config.live_url}, max_target={config.max_target}")
        self._task = asyncio.create_task(self._run(config))
        # 等到状态变成 RUNNING 或失败（避免前端立即查询时还在 STARTING）
        # 注意：不阻塞过久，最长等 60s
        for _ in range(120):
            if self.state != EngineState.STARTING:
                return
            await asyncio.sleep(0.5)
        logger.warning(f"[ENG-004] " + "[引擎] 启动超时（60s 仍在 STARTING），可能等待开播中")

    async def _run(self, config: TaskConfig) -> None:
        """主运行任务（asyncio.to_thread 包装同步逻辑）"""
        try:
            # 1) 构造监测账号 auth（同步，用 to_thread 包装）
            # 优先用前端指定的账号（直播监听页选择），否则用当前账号
            from auto_dm.accounts import current_env_path, env_path_of
            if getattr(config, "acct", None):
                m_env = env_path_of(config.acct)
                logger.info(f"[auth] 使用前端指定账号「{config.acct}」作为监测账号")
            else:
                m_env = self.monitor_env_path or current_env_path()
            self.monitor_auth = await asyncio.to_thread(
                self._build_one_auth, m_env, False, 0
            )
            # P1-B（09 台账 5.3）：把账号名挂到 auth 上，sender 才能经
            # recv_daemon /send_by_uid 直发（发送闸门需 account 定位端口）
            try:
                from auto_dm.accounts import current_name as _cn
                _m_name = getattr(config, "acct", None) or _cn()
                setattr(self.monitor_auth, "account_name", _m_name)
            except Exception:
                pass
            if not getattr(self.monitor_auth, "cookie", None):
                logger.error(f"[AUTH-018] " + "[auth] 监测账号未获取到登录 cookie，无法监听。")
                self.status_msg = "监测登录失败"
                self.state = EngineState.IDLE
                return

            # 2) 构造发送账号 auth（与监测同一 env 时复用）
            s_env = self.sender_env_path or m_env
            if s_env == m_env:
                self.auth = self.monitor_auth
                logger.info("[auth] 发送账号与监测账号共用 .env，复用现场会话凭证")
            else:
                self.auth = await asyncio.to_thread(self._build_one_auth, s_env, False, 0)
                # P1-B：独立发送账号同样标记 account_name
                try:
                    from auto_dm.accounts import name_of_env_path
                    _s_name = name_of_env_path(s_env)
                    if _s_name:
                        setattr(self.auth, "account_name", _s_name)
                except Exception:
                    pass
            if not getattr(self.auth, "cookie", None):
                logger.error(f"[AUTH-019] " + "[auth] 发送账号未获取到登录 cookie，无法发私信。")
                self.status_msg = "发送登录失败"
                self.state = EngineState.IDLE
                return
            if not (getattr(self.auth, "ticket", None) and getattr(self.auth, "private_key", None)):
                logger.error("AUTH-020", 
                    "[auth] 发送账号私信签名缺失（DY_TICKET/DY_PRIVATE_KEY 为空）。"
                    "请在该账号下点重新扫码完成一次登录。"
                )
                self.status_msg = "发送账号需重新扫码"
                self.state = EngineState.IDLE
                return
            if not await asyncio.to_thread(self._verify_credential, self.auth):
                self.status_msg = "发送账号凭证失效"
                self.state = EngineState.IDLE
                return

            # 3) 构造 DispatchCenter（词库随机抽取由 self.pick_dm_message 提供）
            self.dispatch = DispatchCenter(
                auth=self.auth,
                max_target=self.limit,
                delay_range=config.delay_range,
                interval=config.interval,
                enable_send=True,
                pick_dm_message=self.pick_dm_message,
            )
            self.dispatch.on_idle = self._on_dispatch_idle
            await self.dispatch.start()

            # 4) 检查直播间是否开播
            self.status_msg = f"等待开播 {self.live_id}"
            is_live, room_status, room_title, live_info = await asyncio.to_thread(
                check_room_live, self.monitor_auth, self.live_id
            )
            if not is_live:
                logger.warning(f"[ENG-005] " + f"[直播间状态] 未开播，进入轮询等待（每 30s 复查）")
                while not self._stop_event.is_set():
                    try:
                        await asyncio.wait_for(self._stop_event.wait(), timeout=30.0)
                        # stop_event 被 set：退出
                        self.state = EngineState.STOPPED
                        self.status_msg = "已停止"
                        return
                    except asyncio.TimeoutError:
                        pass
                    is_live, room_status, room_title, live_info = await asyncio.to_thread(
                        check_room_live, self.monitor_auth, self.live_id
                    )
                    if is_live:
                        logger.info("[直播间状态] 检测到已开播，开始监听")
                        break
            else:
                logger.info("[直播间状态] 已在直播，直接开始监听")

            if self._stop_event.is_set():
                self.state = EngineState.STOPPED
                self.status_msg = "已停止"
                return

            # 5) 启动 LiveChatHook（WS 监听 + 心跳）
            self.state = EngineState.RUNNING
            self.status_msg = f"监听中 {self.live_id}"
            self.room_title = room_title or ""
            # 2026-09-08：任务启动 IM 汇报
            try:
                from notify import events as _ev

                _ev.task_started(
                    f"直播间：{self.live_id}\n标题：{room_title or '(无)'}\n"
                    f"账号：{acct}"
                )
            except Exception as _e:  # noqa: BLE001
                logger.debug(f"[notify] 任务启动汇报跳过: {_e}")
            self.live = LiveChatHook(self.live_id, self.monitor_auth, self.dispatch, controller=self)
            self.live.room_status = room_status
            # 心跳间隔默认 300s
            self.live.start_heartbeat(interval=300)
            # start_ws 会阻塞直到 WS 关闭（用 to_thread 包装）
            await self.live.start_async(room_info=live_info)
            # WS 退出后停心跳
            if self.live:
                self.live.stop_heartbeat()
        except Exception as e:
            logger.error(f"[ENG-006] " + f"[引擎] 运行异常: {e}")
            self.status_msg = f"运行异常: {e}"
            # 2026-09-08：运行异常 IM 告警
            try:
                from notify import events as _ev

                _ev.task_failed(str(e))
            except Exception as _ne:  # noqa: BLE001
                logger.debug(f"[notify] 任务异常告警跳过: {_ne}")
        finally:
            # 启动失败（登录/凭证等）已在 _run 内把状态置回 IDLE：保留失败信息，任务标为停止
            if self.state == EngineState.IDLE:
                logger.warning(f"[ENG-007] " + f"[引擎] 启动未成功: {self.status_msg}")
                self._finish_history_task("stopped")
            elif not self.dispatch or self.dispatch.queue_size() == 0:
                # 监听线路结束，队列已空：正常收尾
                self.state = EngineState.STOPPED
                self.status_msg = "已停止"
                self._finish_history_task("finished")
            else:
                # 仍有待发私信：保持 STOPPING 直到队列发空
                self.state = EngineState.STOPPING
                self.status_msg = "监听已停止（私信发送中）"

    async def pause(self) -> None:
        if self.state != EngineState.RUNNING:
            return
        self.state = EngineState.PAUSED
        if self.dispatch:
            self.dispatch.pause()
        logger.info("引擎暂停")

    async def resume(self) -> None:
        if self.state != EngineState.PAUSED:
            return
        self.state = EngineState.RUNNING
        if self.dispatch:
            self.dispatch.resume()
        logger.info("引擎恢复")

    async def stop(self, hard: bool = False) -> None:
        """停止引擎

        hard=True:  硬停止，立即清队列（旧版 force_stop_all）
        hard=False: 软停止，存量队列发完才结束（旧版 stop）
        """
        if self.state in (EngineState.STOPPED, EngineState.IDLE):
            return
        self.state = EngineState.STOPPING
        self._stop_event.set()
        logger.info(f"引擎停止 (hard={hard})")

        # 停止 dispatch
        if self.dispatch:
            if hard:
                self.dispatch.stop_hard()
            else:
                self.dispatch.stop_soft()

        # 关闭 WS 监听
        if self.live:
            try:
                self.live._should_stop = True
            except Exception:
                pass
            if getattr(self.live, "ws", None):
                try:
                    self.live.ws.close()
                except Exception:
                    pass
            self.live.stop_heartbeat()

        # 等待主任务结束
        if self._task and not self._task.done():
            try:
                await asyncio.wait_for(self._task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._task.cancel()

        if hard:
            self.state = EngineState.STOPPED
            self.status_msg = "已停止"
            self._finish_history_task("stopped")
        else:
            # 软停止：等存量发完
            if self.dispatch and (self.dispatch.queue_size() > 0 or self.dispatch.pending):
                self.status_msg = "监听已停止（私信发送中）"
                # 后台等待发空
                asyncio.create_task(self._wait_dispatch_done())
            else:
                self.state = EngineState.STOPPED
                self.status_msg = "已停止"
                self._finish_history_task("finished")

    async def _wait_dispatch_done(self) -> None:
        """软停止后等存量私信发完"""
        if not self.dispatch:
            self.state = EngineState.STOPPED
            self.status_msg = "已停止"
            self._finish_history_task("finished")
            return
        await self.dispatch.wait_done()
        self.state = EngineState.STOPPED
        self.status_msg = "已停止"
        self._finish_history_task("finished")

    async def shutdown(self) -> None:
        """后端进程退出钩子：确保历史任务被真实收尾。

        修复根因：此前进程崩溃/被强杀时，历史任务的「运行中」状态无人收尾，
        只能靠 fix_stuck_tasks 在下次读列表时擦除（热修复）。本钩子在优雅退出
        （FastAPI lifespan 的 shutdown）时主动硬停止引擎并落终态，正常退出
        不再产生悬空任务；只有崩溃/强杀才需要 pid 比对兜底（见 tasks_history）。
        """
        try:
            if self.is_running:
                await self.stop(hard=True)
            else:
                # 非运行态（idle/stopped）也可能有未收尾的历史任务遗留在内存
                self._finish_history_task("stopped")
        except Exception as e:
            logger.warning(f"[ENG-008] " + f"[history] 退出收尾历史任务失败（不影响关闭）: {e}")

    def _finish_history_task(self, status: str) -> None:
        """更新当前历史任务为结束状态，记录结果条数与 records 快照。"""
        tid = getattr(self, "_task_history_id", None)
        if not tid:
            return
        try:
            from tasks_history import finish_task
            count = 0
            sent = 0
            records = []
            if self.dispatch:
                try:
                    records = self.dispatch.records_list() or []
                    count = len(records)
                    sent = sum(1 for r in records if getattr(r, "status", None) == RecordStatus.SENT)
                except Exception:
                    pass
            finish_task(tid, status=status, result_count=count,
                        records=[r if isinstance(r, dict) else _rec_to_dict(r) for r in records])
        except Exception as e:
            logger.warning(f"[ENG-009] " + f"[history] 更新历史任务失败: {e}")
        # 日志打印真实计数，避免「日志说发完但实际漏发」的误导
        logger.info(
            f"[引擎] 私信收尾：共捕获 {count} 条，实际成功发送 {sent} 条，"
            f"整体停止（状态={status}）"
        )
        # 2026-09-08：任务结束 IM 汇报（复用上面真实计数，绝不另算一套）
        try:
            from notify import events as _ev

            _ev.task_finished(
                {
                    "sent": sent,
                    "failed": max(count - sent, 0),
                    "total": count,
                    "reason": status,
                }
            )
        except Exception as _e:  # noqa: BLE001
            logger.debug(f"[notify] 任务结束汇报跳过: {_e}")

    async def _on_dispatch_idle(self) -> None:
        """私信延迟队列自然发空后的收尾

        修复（V2 容器）：监听进行中队列短暂变空是正常间隙，绝不能置 STOPPED
        （旧逻辑会在启动后队列首次空时误停引擎，导致页面读回「已停止/等待启动」）。
        仅当监听 WS 已关闭且队列确实发空时才收尾。

        注意：STOPPING 态也收尾（之前软停止后的 `_wait_dispatch_done` 与这里
        都执行 `_finish_history_task`，标零保护，写多次也无害）。若不收尾，
        自然关播（WS 断联）后队列发空的任务会永远处于「运行中」。
        """
        if self.state not in (EngineState.RUNNING, EngineState.STOPPING, EngineState.PAUSED):
            return
        # 监听还活着 -> 队列空只是正常间隙，忽略
        live_active = (
            self.live is not None
            and getattr(self.live, "ws", None) is not None
            and not getattr(self.live, "_should_stop", False)
        )
        if live_active:
            return
        self.state = EngineState.STOPPED
        self.status_msg = "已停止"
        self._finish_history_task("finished")

    # ------------------------------------------------------------------
    # 重新扫码重建（迁移自 rescan_and_rebuild，同步逻辑）
    # ------------------------------------------------------------------
    def rescan_and_rebuild(self, account_name: Optional[str] = None) -> Any:
        """重新扫码指定账号，并立即用新凭证重建受影响的部分。

        注意：本方法在心跳线程中被调用（同步上下文），保留同步实现。
        """
        logger.info(f"[重扫重建] 开始为账号「{account_name}」重新扫码并重建会话…")
        env_path = "monitor.env"  # TODO: 从 AccountService 取
        is_monitor = True  # TODO: 按账号角色判断
        is_sender = True

        # 1) 关闭旧监听 WS
        if self.live:
            try:
                self.live._should_stop = True
            except Exception:
                pass
            if getattr(self.live, "ws", None):
                try:
                    self.live.ws.close()
                except Exception:
                    pass
            self.live.stop_heartbeat()
            self.live = None

        # 2) 重新扫码（由 force_rescan 控制：True 强制重扫，False 复用 .env 凭证）
        # 2026-09-17 修补（OCR 审查 CRITICAL）：原为 `getattr(config, "force_rescan", False)`
        # —— 本方法内**没有 config 这个名字**（参数名是 account_name，也未引用
        # self.config），运行到此必然抛 `NameError: name 'config' is not defined`，
        # 使「重新扫码」链路完全不可用。正确来源是本实例的 self.force_rescan。
        force_fresh = getattr(self, "force_rescan", False)
        auth = self._build_one_auth(env_path, force_fresh=force_fresh, max_age=0)
        if auth is None or not getattr(auth, "cookie", None):
            raise RuntimeError(
                f"账号「{account_name}」扫码未成功拿到有效凭证。"
                f"请确认浏览器已打开并完成抖音扫码，再重试。"
            )

        # 3)+4) 按账号角色重建
        if is_monitor or (not is_sender):
            self.monitor_auth = auth
            self.live = LiveChatHook(self.live_id, self.monitor_auth, self.dispatch, controller=self)
            self.live.start_heartbeat(interval=300)
            threading.Thread(target=self.live.start_ws, daemon=True).start()
            logger.info(f"[重扫重建] 监测账号「{account_name}」监听已用新凭证重建")
        if is_sender or (not is_monitor):
            self.auth = auth
            if self.dispatch:
                self.dispatch.auth = auth
            logger.info(f"[重扫重建] 发送账号「{account_name}」私信签名已刷新")
        logger.info(f"[重扫重建] 完成")
        return auth

    # ------------------------------------------------------------------
    # 运行时调整
    # ------------------------------------------------------------------
    def set_max_target(self, n: int) -> None:
        self.limit = int(n)
        if self.dispatch:
            self.dispatch.set_max_target(n)

    def apply_runtime_config(self, cfg: Any) -> dict:
        """把「变更后的配置内容」补进**正在运行**的监听任务，不中断监听。

        设计契约（用户 2026-09-15 定调）：
          > 点「重启」= 保存后立即把新配置套到正在运行的监听任务，
          > **只修改配置的内容，不中断监听**。

        与 `start()` 的区别（这是本方法存在的唯一理由）：
          - **不重建** LiveChatHook / WS 连接（监听不断）；
          - **不重扫** 凭证（`force_rescan` 不在此生效，避免把运行中任务踢回扫码）；
          - **不清空** 延迟发送队列、不重置去重集合与已发计数；
          - 只热更「调度参数」：发送上限 / 间隔 / 延迟抖动 / 私信词库。

        `live_url`（换直播间）与 `acct`（换监听账号）**不属于热更范围**：
        它们要求重建 auth 与 WS，语义上是「换任务」——显式拒绝并在 `not_applied`
        里说明，由调用方（前端）提示用户走「停止 + 开始」流程。绝不静默忽略。

        返回 `{ok, applied, not_applied, reason, engine_state}`；`ok=False` 表示
        本次一个字段都没生效（引擎未运行 / 参数非法），调用方必须如实呈现。
        """
        state = self.state.value if isinstance(self.state, EngineState) else str(self.state)
        if self.state not in (EngineState.RUNNING, EngineState.PAUSED):
            logger.warning(f"[ENG-013] " + f"[引擎] 热更被拒：引擎未运行（state={state}）")
            return {
                "ok": False, "applied": [], "not_applied": [],
                "reason": f"引擎未运行（当前 {state}），请先「开始自动私信」",
                "engine_state": state,
            }

        not_applied: list[str] = []
        # ① live_url：换直播间 = 换任务，不在热更语义内
        new_url = str(getattr(cfg, "live_url", "") or "").strip()
        cur_url = str(self.live_url or "").strip()
        if new_url and cur_url and new_url != cur_url:
            not_applied.append("live_url")
        # ② acct：换监听账号需要重建 auth + WS
        new_acct = getattr(cfg, "acct", None)
        cur_acct = getattr(self, "_acct", None)
        if new_acct and cur_acct and new_acct != cur_acct:
            not_applied.append("acct")

        # ③ force_rescan：运行期无从生效（凭证早已构造）
        if getattr(cfg, "force_rescan", None) is not None:
            not_applied.append("force_rescan")

        # ④ 调度参数热更
        want_limit = max(1, int(getattr(cfg, "max_target", self.limit) or self.limit))
        want_interval = float(getattr(cfg, "interval", self.interval) or self.interval)
        want_delay = tuple(getattr(cfg, "delay_range", None) or self.delay_range or [40, 65])
        want_pool = self._normalize_dm_pool(getattr(cfg, "dm_pool", None) or [])
        want_limit = min(want_limit, 9999)

        self.limit = want_limit
        self.interval = want_interval
        self.delay_range = want_delay
        self.dm_template = want_pool
        self.pick_dm_message = self._make_pick_dm_message()

        if self.dispatch:
            # 调度器是这些运行时字段的唯一持有者 → 以它回报的字段为准
            applied = self.dispatch.apply_runtime(
                max_target=want_limit,
                interval=want_interval,
                delay_range=(int(want_delay[0]), int(want_delay[1])),
                pick_dm_message=self.pick_dm_message,
            )
        else:
            # 引擎 RUNNING 却无 dispatch：属异常态，显式失败而不是假装热更成功
            logger.warning(f"[ENG-014] " + "[引擎] 热更失败：dispatch 未初始化")
            return {
                "ok": False, "applied": [], "not_applied": not_applied,
                "reason": "发送调度器未初始化（引擎可能刚启动或启动失败）",
                "engine_state": state,
            }

        logger.info(
            f"[引擎] 运行时配置热更（监听未中断）：applied={applied} "
            f"not_applied={not_applied} limit={self.limit} interval={self.interval} "
            f"delay={self.delay_range} 词库={len(self.dm_template)} 条"
        )
        return {
            "ok": True, "applied": applied, "not_applied": not_applied,
            "reason": "", "engine_state": state,
        }

    @property
    def captured_count(self) -> int:
        if self.dispatch:
            return len(self.dispatch.records)
        return 0

    async def shutdown(self) -> None:
        """应用关闭时调用"""
        if self.is_running:
            await self.stop(hard=True)
