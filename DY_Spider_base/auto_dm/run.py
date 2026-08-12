# coding=utf-8
"""自动私信入口（以 DouYin_Spider 为底座融合 DYchajian 业务逻辑）。

启动流程：
  1) 从 .env 读取 DY_COOKIES 构造 auth（底座 common_util）。
  2) 自动从登录浏览器抓 web_protect/keys 补全私信签名（auth_helper）。
  3) 启动直播间 WebSocket 监听（live_hook）：公屏弹幕发言人 -> 私信。
  4) （可选）启动中控台浏览器采集（web_probe）：评论区昵称 -> 私信，作补充来源。

双路来源经 core.DispatchCenter 去重后，统一用 imapi 私有接口直发
（优先消息自带数字 uid，彻底跳过 get_user_info 风控）。

运行：
  cd DouYin_Spider-master
  python -m auto_dm.run
"""

import sys
import os
import asyncio
import threading
from loguru import logger
from dotenv import load_dotenv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from auto_dm.vbrowser import app_root  # 统一应用根：源码态=项目根，打包态=exe 所在目录
from auto_dm import config as C
from auto_dm.core import DispatchCenter
from auto_dm.sender import send_target
from auto_dm.live_hook import LiveChatHook
from auto_dm.auth_helper import enrich_auth


def check_room_live(auth, live_id):
    """判断直播间是否在直播。

    返回 (is_live, room_status, room_title, info)。
      - is_live=True  表示正在直播（room_status == '2' 或 2）；
      - is_live=False 表示未开播 / 下播 / 查不到；
      - 获取异常时保守返回 (True, None, '', None)，避免误判阻断正常监听（仅告警）。
    判定数据源：douyin_api.DouyinAPI.get_live_info 解析直播间 HTML 里的 roomInfo.status
    （2=直播中，4=未开播）。返回的完整 info 会直接传给 start_ws 复用，避免重复查询。
    """
    try:
        from dy_apis.douyin_api import DouyinAPI
        info = DouyinAPI.get_live_info(auth, live_id)
        if not info or not isinstance(info, dict):
            logger.warning("[直播间状态] get_live_info 返回空，保守视为“已开播”以免误阻断监听")
            return True, None, "", None
        status = info.get("room_status")
        title = info.get("room_title", "")
        is_live = str(status) == "2"
        logger.info(f"[直播间状态] room_status={status} title={title!r} -> "
                    f"{'直播中' if is_live else '未开播/下播'}")
        return is_live, status, title, info
    except Exception as e:
        logger.warning(f"[直播间状态] 查询异常（保守视为已开播）: {e}")
        return True, None, "", None


# 日志落盘：命令行启动时也写入本地文件（logs/run_*.log），便于离线回看。
try:
    import os as _os
    from datetime import datetime as _dt
    _log_dir = _os.path.join(app_root(), "logs")
    _os.makedirs(_log_dir, exist_ok=True)
    _log_file = _os.path.join(_log_dir, f"run_{_dt.now().strftime('%Y%m%d_%H%M%S')}.log")
    logger.add(_log_file, level="DEBUG", encoding="utf-8",
               format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} - {message}",
               enqueue=True, retention="30 days")
except Exception:
    pass


def load_dotenv_safe():
    """安全加载 .env：仅读环境变量，不做任何会崩溃的 auth 初始化。"""
    env_path = os.path.join(app_root(), ".env")
    if os.path.exists(env_path):
        load_dotenv(env_path)


# 中控台采集的异步入口在 web_probe，按需 import
if C.ENABLE_WEB_PROBE:
    from auto_dm.web_probe import run_probe


def main():
    # 命令行入口：用 config.py 默认值直接跑
    adm = AutoDM()
    adm.start()
    try:
        while adm.is_running():
            import time
            time.sleep(0.5)
    except KeyboardInterrupt:
        adm.stop()


class AutoDM:
    """可被 GUI / 命令行驱动的自动私信控制器（可启动、可停止）。"""

    def __init__(self, config_overrides=None, monitor_env_path=None, sender_env_path=None):
        # config_overrides: dict，用于 GUI 动态覆盖 config.py 默认值
        if config_overrides:
            for k, v in config_overrides.items():
                setattr(C, k, v)
        # 账号分离：
        #  monitor_env_path —— 监测账号（直播间监听/弹幕，需管理器权限看完整昵称）
        #  sender_env_path  —— 发送账号（私信发送，需私信权限）
        # 均为 None 时各自回退到 accounts 模块的默认选择（再回退根 .env）。
        self.monitor_env_path = monitor_env_path
        self.sender_env_path = sender_env_path
        self.auth = None            # 发送账号 auth（私信用）
        self.monitor_auth = None    # 监测账号 auth（弹幕监听用）
        self.dispatch = None
        self.live = None
        self._thread = None
        self._running = False
        self.status = "未启动"
        self.room_title = ""
        # 监听(WS)是否活跃：与私信发送(dispatch)解耦。主播下播/主动停止监听后置 False，
        # 但 dispatch 延迟队列仍可能在后台发送已捕获的私信（私信是独立线路）。
        self.listen_active = False
        # 中控台采集线程
        self._probe_thread = None
        # 停止事件：用于让中控台采集循环及时退出（停止按钮）
        self._stop_event = threading.Event()

    @staticmethod
    def _credential_age(env_path):
        """返回 .env 距上次写入的秒数；不存在返回 -1。"""
        try:
            import os as _os
            if _os.path.exists(env_path):
                return int(_os.path.getmtime(env_path))
        except Exception:
            pass
        return -1

    def _build_one_auth(self, env_path, force_fresh=False, max_age=600):
        """为指定 .env 路径构造并补全签名（私信签名）的 auth。

        force_fresh: True 时忽略现有凭证，强制重新扫码（GUI 重新扫码按钮）。
        max_age:     监测账号凭证最大允许年龄（秒）。超过则视为“非实时会话”，
                     启动时强制重扫，避免用旧 cookie 建立 WS 导致弹幕昵称被加密。
                     默认 600s（10 分钟）；发送账号不强制（签名齐全即可）。
        """
        from builder.auth import DouyinAuth
        from auto_dm import accounts
        from dotenv import load_dotenv
        load_dotenv_safe()
        # 优先用该账号自己的 DY_COOKIES；空则退回全局根 .env
        if env_path and env_path != os.path.join(app_root(), ".env"):
            load_dotenv(env_path, override=True)
        cookies = os.getenv("DY_COOKIES", "") or ""

        # 监测账号：若凭证过老，强制重扫（根因修复——旧 cookie 建 WS 必加密昵称）
        if force_fresh:
            logger.info(f"[auth] 强制重新扫码（忽略现有凭证）：{env_path}")
        elif max_age and cookies and env_path:
            mtime = self._credential_age(env_path)
            if mtime > 0:
                age = int(__import__("time").time()) - mtime
                if age > max_age:
                    logger.warning(
                        f"[auth] 监测凭证年龄={age}s(> {max_age}s)，视为非实时会话，"
                        f"强制重扫以避免弹幕昵称被加密。")
                    force_fresh = True

        if force_fresh:
            # 强制重扫前，先清除该账号磁盘上的旧凭证，避免任何旧 cookie/签名被复用（双保险）
            from auto_dm import accounts as _accounts
            try:
                if _accounts.clear_credentials_of(env_path):
                    logger.info(f"[auth] 已先清除 {env_path} 的旧凭证，准备重新扫码")
            except Exception:
                pass
            # 直接走扫码登录（含旧→新捕获分析），不使用磁盘旧 cookie
            from dy_apis.login_api import DYLoginApi
            import asyncio
            api = DYLoginApi()
            try:
                auth = asyncio.run(
                    api.get_login_auth(headless=False, env_path=env_path, force=True))
                return auth
            except RuntimeError:
                # 已有事件循环（GUI 线程）时改用新循环
                loop = asyncio.new_event_loop()
                try:
                    auth = loop.run_until_complete(
                        api.get_login_auth(headless=False, env_path=env_path, force=True))
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
                "请在浏览器中完成抖音扫码。")
        auth, _fresh_cookie = enrich_auth(
            auth, cookies_dy=cookies, headless=False,
            user_data_dir="pw_profile_dm", env_path=env_path)
        return auth

    def _verify_credential(self, auth):
        """轻量校验凭证是否仍有效（cookie + 私信签名）。

        仅一次网络往返：直接用 create_conversation 对自身 uid 建会话探测。
        create_conversation 既要求有效 cookie（登录态），也要求有效 web_protect/keys 签名，
        服务端对它返回 401/unauthorized 即登录态失效、返回 INVALID_REQUEST/KICK 即签名失效，
        因此一次调用即可同时覆盖两类校验，无需再单独 get_my_uid 探活（enrich_auth 内部已探过一次，
        此处再探就是第二次冗余往返，是“读取登录状态慢”的成因，已去除）。
        注意：该探测不会向任何人发送消息内容，仅建会话验证签名是否被服务端接受。
        """
        _has_sign = (getattr(auth, "ticket", None) and getattr(auth, "client_cert", None)
                     and getattr(auth, "private_key", None))
        if not _has_sign:
            logger.error(
                "[auth] 私信签名三件套缺失(ticket/client_cert/private_key)。\n"
                "       请删除 .env 中 DY_TICKET/DY_TS_SIGN/DY_CLIENT_CERT/DY_PRIVATE_KEY 四行，\n"
                "       再点该账号【重新扫码】抓取签名后启动。")
            return False
        try:
            from dy_apis.douyin_api import DouyinAPI
            uid = DouyinAPI.get_my_uid(auth)  # 仅取 uid 作为 to_user_id，不再单独判登录态
            if not uid:
                logger.error(
                    "[auth] 无法获取自身 uid（cookie 可能已失效），请对该账号执行【重新扫码】后再启动。")
                return False
            # 对自身 uid 建会话探测：不会向任何人发送消息内容，仅验证签名 + 登录态有效性
            DouyinAPI.create_conversation(auth, int(uid))
            logger.info(f"[auth] 私信签名预检通过（uid={uid}，create_conversation 可被服务端接受）")
            return True
        except Exception as e:
            msg = str(e)
            if "INVALID_REQUEST" in msg or "KICK" in msg:
                logger.error(
                    "[auth] 私信签名预检被拒（create_conversation 返回 INVALID_REQUEST/KICK）。\n"
                    "       说明：web_protect/keys 四件套在首页登录后由 security-sdk 自动生成，与是否打开私信页无关。\n"
                    "       若预检对自身 uid 通过、仅对陌生目标 KICK，多为账号级私信风控/反 spam；\n"
                    "       若对自身也失败，多为 cookie 失效。建议：点【重新扫码】重新抓取最新凭证。")
            elif "login" in msg.lower() or "unauthorized" in msg.lower() or "401" in msg:
                logger.error(
                    "[auth] 登录态校验失败（create_conversation 报未登录），cookie 可能已失效。\n"
                    "       请对该账号执行【重新扫码】后再启动。")
            else:
                logger.error(f"[auth] 私信签名预检异常: {msg}")
            return False

    def _run(self):
        try:
            from auto_dm import accounts
            # 每次启动前复位停止事件（stop() 会 set，避免重启后采集循环立即退出）
            self._stop_event.clear()
            # 监测账号（弹幕监听，需管理器权限看完整昵称）
            m_env = self.monitor_env_path or accounts.monitor_env_path()
            # 发送账号（私信发送，需私信权限）
            s_env = self.sender_env_path or accounts.sender_env_path()

            # 启动强制重扫开关（config.FORCE_RESCAN_ON_START）：True 时完全不读取磁盘旧凭证，
            # 每次启动都重新扫码，杜绝旧 cookie/签名导致的昵称加密与私信 KICK。
            _force = getattr(C, "FORCE_RESCAN_ON_START", False)
            if _force:
                logger.info("[auth] 已启用 FORCE_RESCAN_ON_START：本次启动将强制重新扫码，忽略磁盘旧凭证")
            # 昵称加密修复（续13）：监测账号用现场会话 auth 建 WS，存储过期 cookie 建 WS 会昵称加密。
            # 提速（续38）：未开启强制重扫时，改为 _build_one_auth 内部按凭证新鲜度判断：
            # 磁盘凭证完整且 age<max_age 时走 get_login_auth(force=False) 快速路径
            # （get_my_uid 探活通过即返回，不打开浏览器），仅凭证缺失/过期才重新扫码。
            # GUI【重新扫码】按钮走 rescan_and_rebuild(force_fresh=True) 仍强制重扫。
            # 未开启强制重扫时 max_age=0：完全信任该账号守护进程( browser_daemon )保活的
            # 已登录凭证，不因凭证年龄强制重新扫码（复用守护凭证快速启动）。
            self.monitor_auth = self._build_one_auth(m_env, force_fresh=_force, max_age=0 if not _force else 600)
            if not getattr(self.monitor_auth, "cookie", None):
                logger.error(f"监测账号 [{accounts.monitor_name()}] 未获取到登录 cookie，无法监听。")
                self.status = "监测登录失败"
                self._running = False
                return

            # 发送账号：与监测账号同一 .env 时直接复用监测 auth，避免二次扫码覆盖 web_protect/keys
            # 导致私信 KICK（续15 根因）。不同 env 才单独处理。
            if s_env == m_env:
                self.auth = self.monitor_auth
                logger.info(f"[auth] 发送账号与监测账号共用 .env，复用现场会话凭证（不二次扫码）")
            else:
                # 复用发送账号的已登录凭证（守护保活），不强制重扫（与 FORCE_RESCAN 同步）
                self.auth = self._build_one_auth(s_env, force_fresh=_force, max_age=0 if not _force else 600)
            if not getattr(self.auth, "cookie", None):
                logger.error(f"发送账号 [{accounts.sender_name()}] 未获取到登录 cookie，无法发私信。")
                self.status = "发送登录失败"
                self._running = False
                return
            # 发送账号签名仍缺失（扫码超时/未成功）直接提示，避免 create_conversation 崩
            if not (getattr(self.auth, "ticket", None)
                    and getattr(self.auth, "private_key", None)):
                logger.error(
                    f"[auth] 发送账号 [{accounts.sender_name()}] 私信签名缺失"
                    f"（DY_TICKET/DY_PRIVATE_KEY 为空）。\n"
                    "       请在该账号下点“重新扫码”完成一次登录，凭证会自动写回。")
                self.status = "发送账号需重新扫码"
                self._running = False
                return
            if not self._verify_credential(self.auth):
                self.status = "发送账号凭证失效"
                self._running = False
                return
            self.dispatch = DispatchCenter(self.auth, max_target=C.MAX_TARGET)
            # 延迟队列自然发空时收尾（监听停止后私信线路随之结束）
            self.dispatch.on_idle = self._on_dispatch_idle
            # —— 凭证新鲜度 / cookie 构成诊断（用于分析“为什么旧存储凭证弹幕昵称被加密”）——
            try:
                import os as _os
                from datetime import datetime as _dt
                m_env = self.monitor_env_path or accounts.monitor_env_path()
                age_s = int(_dt.now().timestamp() - _os.path.getmtime(m_env)) if _os.path.exists(m_env) else -1
                ck = getattr(self.monitor_auth, "cookie", {}) or {}
                ck_str = getattr(self.monitor_auth, "cookie_str", "") or ""
                logger.info(
                    f"[凭证诊断] 监测账号={accounts.monitor_name()} | .env 路径={m_env}\n"
                    f"       凭证年龄={age_s}s（约{age_s//60}分钟前扫码/写入）\n"
                    f"       cookie 字段数={len(ck)} 含sessionid={'sessionid' in ck} 含sid_tt={'sid_tt' in ck} "
                    f"含ttwid={'ttwid' in ck} 含msToken={'msToken' in ck}\n"
                    f"       cookie_str 长度={len(ck_str)} 是否由浏览器实时登录得到(非存储重建)需结合上面年龄判断")
            except Exception as _e:
                logger.debug(f"[凭证诊断] 输出失败: {_e}")
            logger.info(f"启动直播间监听 LIVE_ID={C.LIVE_ID} "
                        f"（监测账号={accounts.monitor_name()} / 发送账号={accounts.sender_name()}）...")

            # —— 直播间是否在直播判断（用户需求）：未开播则轮询等待，开播后再启动监听 ——
            # 返回的完整 info 直接复用给 start_ws，避免 start_ws 内部再查一次 get_live_info（加速启动）。
            is_live, room_status, room_title, live_info = check_room_live(self.monitor_auth, C.LIVE_ID)
            if not is_live:
                logger.warning(
                    f"[直播间状态] 当前未开播（room_status={room_status}，title={room_title!r}），"
                    f"进入轮询等待，每 {C.LIVE_POLL_INTERVAL}s 复查，开播后自动开始监听。")
                self.status = f"等待开播 LIVE_ID={C.LIVE_ID}"
                waited = 0
                while not self._stop_event.is_set():
                    self._stop_event.wait(C.LIVE_POLL_INTERVAL)
                    if self._stop_event.is_set():
                        break
                    waited += C.LIVE_POLL_INTERVAL
                    is_live, room_status, room_title, live_info = check_room_live(self.monitor_auth, C.LIVE_ID)
                    if is_live:
                        logger.info(f"[直播间状态] 检测到已开播（等待约 {waited}s），开始监听。")
                        break
                    if waited % (C.LIVE_POLL_INTERVAL * 10) == 0:
                        logger.info(f"[直播间状态] 仍在等待开播…（已等 {waited}s）")
                if self._stop_event.is_set():
                    logger.info("[直播间状态] 等待开播期间收到停止信号，退出。")
                    self._running = False
                    self.status = "已停止"
                    return
            else:
                logger.info(f"[直播间状态] 已在直播，直接开始监听。")

            self.status = f"监听中 LIVE_ID={C.LIVE_ID}"
            self.listen_active = True     # 监听(WS)线路已激活；与私信发送(dispatch)独立
            if C.ENABLE_WEB_PROBE and C.WEB_PROBE_ROOM_URL:
                self._probe_thread = threading.Thread(
                    target=lambda: asyncio.run(
                        run_probe(self.dispatch, C.WEB_PROBE_ROOM_URL,
                                  C.WEB_PROBE_USER_DATA_DIR, C.WEB_PROBE_HEADLESS,
                                  should_stop=self._stop_event.is_set)),
                    daemon=True)
                self._probe_thread.start()
                logger.info("中控台采集已在后台启动")
            # 用监测账号的 auth 监听弹幕（管理器权限账号可见完整昵称）
            self.live = LiveChatHook(C.LIVE_ID, self.monitor_auth, self.dispatch, controller=self)
            self.live.room_status = room_status
            # 先启动运行期登录态心跳（周期探活，失效自动重扫），再起 WS 监听
            self.live.start_heartbeat()
            # 复用 check_room_live 已查到的 live_info，避免 start_ws 内部重复查询（省一次网络往返）
            self.live.start_ws(room_info=live_info)
            # WS 退出（正常停止或被心跳触发重扫关闭）后，停掉心跳线程
            # 守卫：stop() 可能已将 self.live 置 None（主动停止时先调过 stop_heartbeat），避免对 None 调用
            if self.live:
                self.live.stop_heartbeat()
        except Exception as e:
            logger.error(f"运行异常: {e}")
        finally:
            # 监听(WS)线路结束；私信发送(dispatch)是独立线路，若仍有待发延迟私信
            # 则由后台 _loop 继续发完，发空后通过 on_idle(_on_dispatch_idle) 把整体置为已停止。
            self.listen_active = False
            if not self.dispatch or self.dispatch.queue_size() == 0:
                # 无待发私信：监听结束即整体结束
                self._running = False
                self.status = "已停止"
            else:
                # 仍有待发私信：保持“运行中（私信发送中）”直到队列发空
                self.status = "监听已停止（私信发送中）"

    def start(self):
        if self._running:
            logger.warning("已在运行中")
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        """停止监听（软停止）：关闭直播间监听 WebSocket、停止中控台采集。
        但【不清空私信延迟队列】——私信发送是与监听(WS)独立的线路，
        已捕获且尚未到延迟时点的评论会继续按原定时点发出（解决“直播间关闭后已捕获私信不发”）。
        若需彻底丢弃待发私信，调用 force_stop_all()。"""
        self._running = False
        self.listen_active = False
        self._stop_event.set()          # 通知中控台采集循环退出
        self.status = "监听已停止（私信发送中）"
        # 1) 软停止私信调度：停止接收新目标，但保留已入队的延迟私信继续发完
        if self.dispatch:
            self.dispatch.stop_keep_queue()
        # 2) 关闭直播间监听 WebSocket（run_forever 会随即退出，监控停止）
        #    先置 _should_stop，阻止 on_close 里的无条件自动重连（否则 WS 永远关不掉）
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
            try:
                self.live.stop_heartbeat()
            except Exception:
                pass
            self.live = None
        logger.info(
            "已停止监听：直播间监控 + 中控台采集已关闭；"
            "已捕获未发的私信将在延迟后继续发送（私信与监听为独立线路）")

    def force_stop_all(self):
        """彻底停止：关闭监听 + 清空私信延迟队列（丢弃已捕获未发的私信）。
        一般仅在需要立即终止全部活动的场景使用（GUI 默认不暴露，避免误丢待发私信）。"""
        self._running = False
        self.listen_active = False
        self._stop_event.set()
        self.status = "已停止"
        if self.dispatch:
            self.dispatch.stop()        # 硬停止，清空队列
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
            try:
                self.live.stop_heartbeat()
            except Exception:
                pass
            self.live = None
        logger.info("已彻底停止：监听 + 私信发送（含待发队列）均已关闭")

    def _on_dispatch_idle(self):
        """私信延迟队列自然发空后的收尾：私信线路也结束，整体置为已停止。"""
        self._running = False
        if not self.listen_active:
            self.status = "已停止"

    def is_running(self):
        # 任务活跃 = 监听在跑，或私信延迟队列仍在发送（两条线路任一活跃即为运行中）
        if self._running:
            return True
        if self.dispatch and not self.dispatch.hard_stopped and self.dispatch.queue_size() > 0:
            # 软停止(no_new)后仍有存量私信在后台发送中
            return True
        return False

    def rescan_and_rebuild(self, account_name=None):
        """重新扫码指定账号，并立即用新凭证重建受影响的部分（关键修复）。

        逻辑顺序（解决“重扫后昵称仍加密 / 私信仍失败”的卡死问题）：
          1) 先关闭当前直播间监听 WS（旧会话必带旧 cookie，不关则弹幕永远加密）；
          2) 对该账号强制重新扫码（含旧→新捕获分析），拿新鲜 auth；
          3) 若重扫的是【监测账号】：用新 monitor_auth 重建 LiveChatHook 并重启 WS
             —— 这一步是昵称恢复完整的根本，必须第一时间做；
          4) 若重扫的是【发送账号】：更新 self.auth 与 dispatch.auth，私信签名随之刷新；
          5) 重建后若昵称仍加密，说明该账号抖音侧仍脱敏，需结合捕获日志判断。

        注意：本方法应在“停止”状态下调用，避免旧 WS 与新 WS 并发写同一 dispatch。
        """
        from auto_dm import accounts
        if account_name is None:
            account_name = accounts.current_name()
        logger.info(f"[重扫重建] 开始为账号「{account_name}」重新扫码并重建会话…")
        # 确定该账号是监测还是发送（按当前选择判断）
        is_monitor = (account_name == accounts.monitor_name())
        is_sender = (account_name == accounts.sender_name())
        env_path = accounts.env_path_of(account_name)

        # 1) 关闭旧监听 WS（无论重扫哪个账号，先停掉弹幕监听，避免旧会话残留）
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
            try:
                self.live.stop_heartbeat()
            except Exception:
                pass
            self.live = None

        # 2) 强制重新扫码，拿到新鲜 auth（内部含旧→新捕获分析）
        auth = self._build_one_auth(env_path, force_fresh=True, max_age=0)
        if auth is None or not getattr(auth, "cookie", None):
            raise RuntimeError(
                f"账号「{account_name}」扫码未成功拿到有效凭证（auth 为空或 cookie 缺失）。"
                f"请确认浏览器已打开并完成抖音扫码，再重试。")

        # 3)+4) 按账号角色重建
        if is_monitor or (not is_sender):
            # 监测账号（或默认账号）：重建直播间监听 WS，第一时间恢复昵称
            self.monitor_auth = auth
            self.live = LiveChatHook(C.LIVE_ID, self.monitor_auth, self.dispatch, controller=self)
            # 新 hook 也带运行期心跳（先启心跳再起 WS）
            self.live.start_heartbeat()
            threading.Thread(target=self.live.start_ws, daemon=True).start()
            logger.info(f"[重扫重建] 监测账号「{account_name}」监听已用新凭证重建，昵称应恢复正常。")
        if is_sender or (not is_monitor):
            # 发送账号：刷新私信 auth
            self.auth = auth
            if self.dispatch:
                self.dispatch.auth = auth
            logger.info(f"[重扫重建] 发送账号「{account_name}」私信签名已刷新。")
        logger.info(f"[重扫重建] 完成。如弹幕昵称仍加密，请查看 logs/login_capture_*.log 比对旧→新凭证。")
        return auth

    def sent_count(self):
        return self.dispatch.count if self.dispatch else 0

    def max_target(self):
        return self.dispatch.max_target if self.dispatch else C.MAX_TARGET

    def set_max_target(self, n):
        """运行时调整发送上限（GUI 调速即时生效）。"""
        if self.dispatch:
            self.dispatch.set_max_target(n)
        C.MAX_TARGET = int(n)

    def pause(self):
        """暂停：不断浏览器/守护进程，仅停止监听采集与私信发送。"""
        if self.dispatch:
            self.dispatch.pause()
        self.status = "已暂停"

    def resume(self):
        """继续：恢复监听采集与私信发送。"""
        if self.dispatch:
            self.dispatch.resume()
        self.status = "监听中" if self._running else self.status


if __name__ == "__main__":
    main()
