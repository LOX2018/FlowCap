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
import os
import threading
import time
from typing import Any, Optional

from dotenv import load_dotenv
from loguru import logger

from config import settings
from models.enums import EngineState
from models.task import TaskConfig
from core.dispatch import DispatchCenter
from core.live_hook import LiveChatHook


def check_room_live(auth: Any, live_id: str):
    """判断直播间是否在直播。

    返回 (is_live, room_status, room_title, info)。
    迁移自 run.py 的 check_room_live。
    """
    try:
        from dy_apis.douyin_api import DouyinAPI
        info = DouyinAPI.get_live_info(auth, live_id)
        if not info or not isinstance(info, dict):
            logger.warning("[直播间状态] get_live_info 返回空，保守视为已开播以免误阻断监听")
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
        logger.warning(f"[直播间状态] 查询异常（保守视为已开播）: {e}")
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
        cookies = os.getenv("DY_COOKIES", "") or ""

        if force_fresh:
            logger.info(f"[auth] 强制重新扫码（忽略现有凭证）：{env_path}")
        elif max_age and cookies and env_path:
            mtime = self._credential_age(env_path)
            if mtime > 0:
                age = int(time.time()) - mtime
                if age > max_age:
                    logger.warning(
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
            logger.warning("[auth] 未检测到 DY_COOKIES，将自动打开浏览器扫码登录获取。")
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
            logger.error(
                "[auth] 私信签名三件套缺失(ticket/client_cert/private_key)。\n"
                "       请删除 .env 中 DY_TICKET/DY_TS_SIGN/DY_CLIENT_CERT/DY_PRIVATE_KEY 四行，\n"
                "       再点该账号【重新扫码】抓取签名后启动。"
            )
            return False
        try:
            from dy_apis.douyin_api import DouyinAPI
            uid = DouyinAPI.get_my_uid(auth)
            if not uid:
                logger.error(
                    "[auth] 无法获取自身 uid（cookie 可能已失效），请对该账号执行【重新扫码】后再启动。"
                )
                return False
            DouyinAPI.create_conversation(auth, int(uid))
            logger.info(f"[auth] 私信签名预检通过（uid={uid}，create_conversation 可被服务端接受）")
            return True
        except Exception as e:
            msg = str(e)
            if "INVALID_REQUEST" in msg or "KICK" in msg:
                logger.error(
                    "[auth] 私信签名预检被拒（create_conversation 返回 INVALID_REQUEST/KICK）。\n"
                    "       建议点【重新扫码】重新抓取最新凭证。"
                )
            elif "login" in msg.lower() or "unauthorized" in msg.lower() or "401" in msg:
                logger.error(
                    "[auth] 登录态校验失败（create_conversation 报未登录），cookie 可能已失效。"
                )
            else:
                logger.error(f"[auth] 私信签名预检异常: {msg}")
            return False

    # ------------------------------------------------------------------
    # 引擎控制（async）
    # ------------------------------------------------------------------
    async def start(self, config: TaskConfig) -> None:
        """启动引擎"""
        if self.state not in (EngineState.IDLE, EngineState.STOPPED):
            raise RuntimeError(f"当前状态 {self.state.value} 无法启动")
        self.state = EngineState.STARTING
        self.live_url = config.live_url
        # 从 live_url 解析出真实直播间号 web_rid（live.douyin.com/<web_rid>）
        try:
            from link_resolve import resolve_live_id
            self.live_id, _ = resolve_live_id(config.live_url)
        except Exception:
            self.live_id = config.live_url
        self.limit = config.max_target
        self.status_msg = "启动中"
        self._stop_event.clear()

        logger.info(f"引擎启动: live_url={config.live_url}, max_target={config.max_target}")
        self._task = asyncio.create_task(self._run(config))
        # 等到状态变成 RUNNING 或失败（避免前端立即查询时还在 STARTING）
        # 注意：不阻塞过久，最长等 60s
        for _ in range(120):
            if self.state != EngineState.STARTING:
                return
            await asyncio.sleep(0.5)
        logger.warning("[引擎] 启动超时（60s 仍在 STARTING），可能等待开播中")

    async def _run(self, config: TaskConfig) -> None:
        """主运行任务（asyncio.to_thread 包装同步逻辑）"""
        try:
            # 1) 构造监测账号 auth（同步，用 to_thread 包装）
            from auto_dm.accounts import current_env_path
            m_env = self.monitor_env_path or current_env_path()
            self.monitor_auth = await asyncio.to_thread(
                self._build_one_auth, m_env, False, 0
            )
            if not getattr(self.monitor_auth, "cookie", None):
                logger.error("[auth] 监测账号未获取到登录 cookie，无法监听。")
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
            if not getattr(self.auth, "cookie", None):
                logger.error("[auth] 发送账号未获取到登录 cookie，无法发私信。")
                self.status_msg = "发送登录失败"
                self.state = EngineState.IDLE
                return
            if not (getattr(self.auth, "ticket", None) and getattr(self.auth, "private_key", None)):
                logger.error(
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

            # 3) 构造 DispatchCenter
            self.dispatch = DispatchCenter(
                auth=self.auth,
                max_target=config.max_target,
                delay_range=config.delay_range,
                interval=config.interval,
                enable_send=True,
                pick_dm_message=(lambda: config.dm_pool[0]) if config.dm_pool else None,
            )
            self.dispatch.on_idle = self._on_dispatch_idle
            await self.dispatch.start()

            # 4) 检查直播间是否开播
            self.status_msg = f"等待开播 {self.live_id}"
            is_live, room_status, room_title, live_info = await asyncio.to_thread(
                check_room_live, self.monitor_auth, self.live_id
            )
            if not is_live:
                logger.warning(f"[直播间状态] 未开播，进入轮询等待（每 30s 复查）")
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
            logger.error(f"[引擎] 运行异常: {e}")
            self.status_msg = f"运行异常: {e}"
        finally:
            # 监听线路结束；私信线路若仍有待发，由 dispatch 继续发完
            if not self.dispatch or self.dispatch.queue_size() == 0:
                self.state = EngineState.STOPPED
                self.status_msg = "已停止"
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
        else:
            # 软停止：等存量发完
            if self.dispatch and (self.dispatch.queue_size() > 0 or self.dispatch.pending):
                self.status_msg = "监听已停止（私信发送中）"
                # 后台等待发空
                asyncio.create_task(self._wait_dispatch_done())
            else:
                self.state = EngineState.STOPPED
                self.status_msg = "已停止"

    async def _wait_dispatch_done(self) -> None:
        """软停止后等存量私信发完"""
        if not self.dispatch:
            self.state = EngineState.STOPPED
            return
        await self.dispatch.wait_done()
        self.state = EngineState.STOPPED
        self.status_msg = "已停止"
        logger.info("[引擎] 存量私信已发完，整体停止")

    async def _on_dispatch_idle(self) -> None:
        """私信延迟队列自然发空后的收尾"""
        if self.state == EngineState.STOPPING:
            self.state = EngineState.STOPPED
            self.status_msg = "已停止"

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
        force_fresh = getattr(config, "force_rescan", False)
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

    @property
    def captured_count(self) -> int:
        if self.dispatch:
            return len(self.dispatch.records)
        return 0

    async def shutdown(self) -> None:
        """应用关闭时调用"""
        if self.is_running:
            await self.stop(hard=True)
