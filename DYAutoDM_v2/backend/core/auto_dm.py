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
import re
import threading
import time
from typing import Any, Optional

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

    2026-09-21（ENG-017）：auth 为 None 时走**匿名进房**（自建请求），
    不得把 None 直接传给 `DouyinAPI.get_live_info`（其内部 `auth_.cookie`
    会 AttributeError）。开播状态本来就从页面解析得到，与登录态无关。
    """
    try:
        from dy_apis.douyin_api import DouyinAPI
        if auth is not None and getattr(auth, "cookie", None):
            info = DouyinAPI.get_live_info(auth, live_id)
        else:
            # 匿名路径：复用 LiveChatHook 的匿名进房实现（同一份解析逻辑）
            from core.live_hook import LiveChatHook
            _probe = LiveChatHook.__new__(LiveChatHook)
            _probe.live_id = live_id
            _probe.auth_ = None
            info = _probe._anon_live_info()
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
        # captured_count 是只读 property（从 dispatch.records 派生），无需初始化
        self.status_msg: str = "未启动"
        self.room_title: str = ""
        # 2026-09-20：AI 生成回调（直播/采集来源的私信文案）。
        # 由 _apply_config 构建；None 时发送完全走词库（与改造前逐字一致）。
        self.gen_dm_message: Optional[Any] = None
        # 当前任务的目标账号（供 AI 生成时解析 Agent 绑定/作用域）
        self.target_acct: Optional[str] = None
        # 直播监听指定账号（TaskConfig.acct）。__init__ 里先置 None，供 start()
        # 解析 live_url 时安全读取（避免 getattr 默认值掩盖真正的属性缺失）——
        # ENG-021：原实现只在 _apply_config 里赋值，而 start() 解析 live_url 早于它。
        self._acct: Optional[str] = None

        # 凭证
        self.auth: Any = None              # 发送账号 auth
        self.monitor_auth: Any = None      # 监测账号 auth
        self.monitor_env_path: Optional[str] = None
        self.sender_env_path: Optional[str] = None

        # 子系统
        self.dispatch: Optional[DispatchCenter] = None
        self.live: Optional[LiveChatHook] = None
        # 监听线生命周期标记（ENG-019，2026-09-21）
        # 设计契约：「监听线是否活跃」的判据必须在**整条生命周期内**都成立——
        # 从「决定监听」开始，到「WS 收尾」结束。此前判据只覆盖「已连上/在重连」，
        # 于是「LiveChatHook 已创建、start_ws 还没被调度」这一窗口被判成「监听已死」，
        # 第一个队列空回调就把整个任务误收尾（实机：引擎启动 2s 后即 stopped，
        # 而 WS 在 1s 后才连上；前端于是显示「已停止」，正是用户报的现象）。
        self._listen_started = False       # 已决定监听（置活点）
        self._listen_ended = False         # 监听线真的收尾了（置死点）

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
        """返回凭证距上次写入的秒数；不存在返回 -1。

        🔴 2026-09-21：明文 .env 已废弃 —— mtime 取加密文件 <env_path>.enc。
        """
        try:
            from services import member_ctx
            ep = env_path + ".enc"
            if member_ctx.env_exists(env_path) and os.path.exists(ep):
                return int(os.path.getmtime(ep))
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
        # 🔴 2026-09-21：凭证永久加密（明文 .env 已废弃）——
        # 不再 load_dotenv 注入 os.environ；值经 member_ctx 精确读取。
        from services import member_ctx
        _vals = member_ctx.parse_env_dict(env_path)
        cookies = _vals.get("DY_COOKIES") or ""

        if force_fresh:
            logger.info(f"[auth] 强制重新扫码（忽略现有凭证）：{env_path}")
        elif max_age and cookies and env_path:
            mtime = self._credential_age(env_path)
            if mtime > 0:
                age = int(time.time()) - mtime
                if age > max_age:
                    logger.warning(f"[AUTH-011] " + f"[auth] 凭证年龄={age}s(> {max_age}s)，视为非实时会话，"
                        f"强制重扫以避免弹幕昵称被加密。")
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
            logger.error(f"[AUTH-013] " + "[auth] 私信签名三件套缺失(ticket/client_cert/private_key)。\n"
                "       请删除 .env 中 DY_TICKET/DY_TS_SIGN/DY_CLIENT_CERT/DY_PRIVATE_KEY 四行，\n"
                "       再点该账号【重新扫码】抓取签名后启动。")
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
                logger.error(f"[AUTH-015] " + "[auth] 私信签名预检被拒（create_conversation 返回 INVALID_REQUEST/KICK）。\n"
                    "       建议点【重新扫码】重新抓取最新凭证。")
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
        self._acct = getattr(config, "acct", None)
        self.dm_template = self._normalize_dm_pool(config.dm_pool)
        self.pick_dm_message = self._make_pick_dm_message()
        # 2026-09-20：AI 生成回调（按「账号绑定 Agent + scopes 含 live」判定；
        # 未绑定/未启用 → 返回空串 → 调度器回落词库，零回归）。
        self.gen_dm_message = self._make_gen_dm_message()
        if self.dispatch is not None:
            self.dispatch.gen_dm_message = self.gen_dm_message

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

    def _make_gen_dm_message(self):
        """构造「AI 生成私信文案」回调（直播监听 / 视频采集来源）。

        设计契约（用户 2026-09-19 口径）：直播与采集两场景的私信统一走调度器，
        文案来源由 `pick_dm_message`（词库）扩展为「AI 生成优先、词库回落」。
        接线点只有一处（本方法）—— 不允许在 live/crawl 各自实现。
        失败语义：判定环节异常记 `SEND-039`；生成环节异常记 `SEND-040`；
        两者都只回落词库，不中断发送。

        生效条件（全部满足才启用 AI）：
          ① `enabled=True`；
          ② `scopes` 含 `live`；
          ③ `strict_level != "kb_only"`（kb_only = 用户显式选择「AI 完全不参与」，必须尊重）。

        配置来源 = `ai_agent.resolve_config(account, base)`：
          账号绑定了 Agent → 用该 Agent 的配置；**未绑定 → 原样返回全局
          `ai_reply_config`**（这是该方法既有契约，文档原话「未绑定 → 零回归」）。
          所以「是否生效」由 **scopes** 决定，而不是由「有没有绑定」决定 ——
          与 9.29 §AI 接入点收敛一致（账号绑定只决定「用哪个 Agent」）。
        任一条不满足 → 返回 None，发送链路回落词库，与改造前逐字一致。

        线程安全：`_do_send` 在 asyncio 事件循环内调用本回调，回调内只用
        `asyncio.to_thread` 包装（禁止在事件循环内做阻塞网络请求）。
        """
        try:
            from services import ai_agent, ai_reply

            base = ai_reply.get_config()
            account = (self.target_acct or getattr(self, "_acct", None) or "")
            if not account:
                return None
            aid = ai_agent.agent_of(account) or ""
            # 未绑定 Agent 时 resolve_config 原样返回全局配置（零回归语义），
            # 因此这里不能用「未绑定」当否决条件 —— 由下方 scopes 判定。
            cfg = ai_agent.resolve_config(account, base)
            if not cfg.get("enabled"):
                return None
            if str(cfg.get("strict_level", "rag")) == "kb_only":
                return None
            if "live" not in (cfg.get("scopes") or []):
                return None
            logger.info(f"[live-ai] 私信文案已接入 AI 生成（账号={account} "
                        f"agent={aid or '未绑定/用全局'} 档位={cfg.get('strict_level')} "
                        f"scopes={cfg.get('scopes')}）")
        except Exception as e:
            logger.warning(f"[SEND-039] " + f"[调度] AI 文案接线判定失败，回落词库: {e}")
            return None

        async def _gen(target: dict) -> str:
            """按目标生成一条私信文案；任何失败都返回空串（调用方回落词库）。"""
            try:
                uid = str((target or {}).get("user_id")
                          or (target or {}).get("uid") or "").strip()
                nick = str((target or {}).get("nickname") or "").strip()
                comment = str((target or {}).get("comment") or "").strip()
                # 语境：把弹幕/评论作为「对方说的话」，让第一步有内容可回应
                ctx = comment or nick or "（直播间新观众）"
                text, source = await asyncio.to_thread(
                    ai_reply.generate_dm_for_live,
                    account=account, peer_name=nick or uid,
                    comment=ctx, cfg=cfg,
                )
                text = str(text or "").strip()
                if not text:
                    return ""
                logger.info(f"[live-ai] 已生成文案（来源={source} 账号={account} "
                            f"目标={nick or uid}）: {text[:30]!r}")
                return text
            except Exception as e:
                logger.warning(f"[SEND-040] " + f"[调度] AI 文案生成失败，回落词库: {e}")
                return ""

        return _gen

    def _snapshot_config(self) -> dict:
        """当前任务的配置快照（任务中心「进入/复用」回读数据源）。"""
        dr = tuple(self.delay_range or [40, 65])
        return {
            "live_url": self.live_url or "",
            "live_id": self.live_id or "",
            "max_target": int(self.limit),
            "interval": float(self.interval),
            "delay": f"{dr[0]},{dr[1]}" if len(dr) == 2 else str(list(dr)),
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
        # 2026-09-21（ENG-015）：与 `_on_dispatch_idle` 共用同一存活判据。
        # 原判据 `live.ws is not None` 在 WS 握手窗口内恒 False → 前端在
        # 「已开始监听但尚未连上」的 1~2s 内读到 alive=False、误显示「等待启动」。
        ws_active = self._listen_line_active()
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
        # ENG-021（2026-09-22 T-01 实机取证）：原实现在解析失败时**静默**把整段
        # live_url 当作 live_id（`except: self.live_id = config.live_url`）⇒ 进房
        # URL 变成 `live.douyin.com/https://…`（实测 HTTP 404）⇒ 秒停「已停止」，
        # 用户完全无从判断是解析失败还是账号没解密权。实测已复现：粘贴
        # `www.douyin.com/follow/live/992931212705?anchor_id=…` 即触发。
        # 设计契约：直播间号必须是「纯数字 web_rid」；解析不出就**显式失败并透出原因**，
        # 绝不用一个不可能成功的 URL 假装启动（静默兜底 = 故障隐藏层，本项目铁律）。
        _raw_url = (config.live_url or "").strip()
        if not re.fullmatch(r"\d{5,}", _raw_url):
            # 非纯数字输入才需要解析；纯 room_id 走快速路径、零网络。
            try:
                from link_resolve import resolve_live_id
                from auto_dm.accounts import current_name
                _resolved, _ = resolve_live_id(
                    config.live_url, account_name=self._acct or current_name())
            except Exception as _e:  # noqa: BLE001
                _resolved = None
                logger.warning(f"[ENG-021] [engine] live_url 解析异常: "
                               f"{type(_e).__name__}: {_e}")
            if not _resolved or not re.fullmatch(r"\d{5,}", str(_resolved)):
                _detail = (f"无法从直播间链接解析出有效房间号（原始输入：{config.live_url!r}）。"
                           f"请使用直播页链接（live.douyin.com/<房间号> 或 "
                           f"www.douyin.com/follow/live/<房间号>）或直接填写纯房间号。")
                self.status_msg = f"启动失败：{_detail}"
                logger.error(f"[ENG-021] [engine] {_detail}")
                self.state = EngineState.STOPPED
                return
            self.live_id = str(_resolved)
        else:
            self.live_id = _raw_url
        self._apply_config(config)
        self.status_msg = "启动中"
        self._stop_event.clear()
        # 新任务复位监听线标记（ENG-019）：否则上一轮任务的 _listen_ended
        # 会让本轮「已启动未收尾」判据恒不成立 → 监听窗口内被误收尾。
        self._listen_started = False
        self._listen_ended = False

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
            # 2026-09-20：目标账号 = 私信发给谁用的账号（AI 生成时解析 Agent 绑定）
            self.target_acct = (getattr(config, "acct", None)
                                or getattr(self, "_acct", None) or None)
            if not getattr(self.monitor_auth, "cookie", None):
                logger.warning(f"[AUTH-018] " + "[auth] 监测账号未获取到登录 cookie —— "
                    "以【匿名模式】继续监听直播流（弹幕可收；昵称若加密将无法解密）。")
                # ENG-017：不再 return。凭证的真正职责是「昵称解密 + 私信发送」，
                # 而直播流连接本身支持匿名查看（用户明确要求解耦）。
                # 此处降级：monitor_auth 置 None，由 LiveChatHook 走匿名 cookie 建连。

            # 2026-09-21（ENG-018）→ 2026-09-22（H-3 返工）：探测**直播昵称解密权**。
            # 判据不能只看「有 cookie」——实测张老师 cookie 70 个字段齐全（sessionid/
            # sid_tt/ttwid/uid_tt 全在），服务端仍判其「未登录」⇒ 直播帧下发脱敏数据
            # （uid=111111 + 昵称 `威***` + sec_uid 空），与**完全不带 cookie 的真匿名**
            # 现象逐字相同 ⇒「有 cookie」是代理信号，判据为真、能力为零。
            #
            # **H-3 返工（2026-09-22）**：解密权是**合取**判据 —— 必须同时满足
            #   ① 会话被服务端承认（profile/self）
            #   ② 身份未漂移（探活 uid ∈ 历史 conv_id，AUTH-050）
            # 旧实现只用 ① 却以「无解密权」上报，把「会话活性被拒」等同于
            # 「身份漂移 / 无解密权」，文案指向错误动作（用户实测症状：重扫了仍无解密）。
            # 权威判据收敛到 `accounts.uid_identity_verdict()`（它零新增打网：
            # 身份侧读 `uid_probe` 的既有出口，会话侧复用既有缓存）。
            # 三态（§〇·己·2）：True=有解密权 / False=已确认无 / None=取不到证据。
            # 该值一路传给 LiveChatHook（它只消费、不自创判据）。
            self._live_session_ok = None
            self._live_ident_detail = ""
            self._live_ident_reason = ""
            self._live_ident_label = ""
            try:
                def _probe_and_verdict():
                    # 身份侧判据需要「本进程对该账号的 uid 判定」做输入；此处
                    # 触发一次**走统一调度**的探活（缓存命中则零网络；未命中才
                    # 真正打网，且受 300s TTL + 同账号串行锁门控）——不绕过亦不新增频次。
                    # 不这样做，身份侧在会话首启时恒为「取不到证据」，
                    # H-3 要求的「两侧都验」就落不了地。
                    from services import uid_probe as _up
                    try:
                        _up.get_uid(_m_name)
                    except Exception:  # noqa: BLE001
                        pass
                    from auto_dm.accounts import uid_identity_verdict as _uiv
                    return _uiv(_m_name, self.monitor_auth, False)
                _li_ok, _li_reason, _li_label, _li_detail = await asyncio.to_thread(
                    _probe_and_verdict)
                self._live_session_ok = _li_ok          # 三态，不做布尔折叠
                self._live_ident_reason = _li_reason
                self._live_ident_label = _li_label
                self._live_ident_detail = _li_detail
            except Exception as _e:  # noqa: BLE001
                logger.debug(f"[live-identity] 直播解密权探测跳过: {_e}")
            if self._live_session_ok is True:
                logger.info(f"[live-identity] 监测账号{self._live_ident_label}"
                            f" —— {self._live_ident_detail}")
            elif self._live_session_ok is False:
                logger.warning(
                    f"[LIVE-035] [live-identity] 监测账号**{self._live_ident_label}** —— "
                    f"弹幕昵称将被脱敏（uid=111111）。原因：{self._live_ident_detail}")
            else:
                # 取不到证据（None）：既不判有、也不判无 —— 诚实降级并留痕
                logger.warning(
                    f"[LIVE-036] [live-identity] 监测账号解密权{self._live_ident_label}"
                    f" —— 按既有行为继续（不据此降级）。原因：{self._live_ident_detail}")

            # 2) 构造发送账号 auth（与监测同一 env 时复用）
            #    ENG-017：发送线凭证缺失**不阻断监听线** —— 只关掉发送能力。
            _send_ok = True
            s_env = self.sender_env_path or m_env
            if s_env == m_env:
                self.auth = self.monitor_auth
                logger.info("[auth] 发送账号与监测账号共用 .env，复用现场会话凭证")
            else:
                try:
                    self.auth = await asyncio.to_thread(self._build_one_auth, s_env, False, 0)
                except Exception as e:  # noqa: BLE001
                    self.auth = None
                    _send_ok = False
                    logger.warning(f"[AUTH-019] " + f"[auth] 发送账号构造失败，转为只听不发: {e}")
                # P1-B：独立发送账号同样标记 account_name
                try:
                    from auto_dm.accounts import name_of_env_path
                    _s_name = name_of_env_path(s_env)
                    if _s_name and self.auth is not None:
                        setattr(self.auth, "account_name", _s_name)
                except Exception:
                    pass
            if _send_ok and not getattr(self.auth, "cookie", None):
                logger.warning(f"[AUTH-019] " + "[auth] 发送账号未获取到登录 cookie —— "
                    "转为【只听不发】（监听继续，私信能力关闭）。")
                _send_ok = False
            if _send_ok and not (getattr(self.auth, "ticket", None) and getattr(self.auth, "private_key", None)):
                logger.warning(f"[AUTH-020] " + "[auth] 发送账号私信签名缺失（DY_TICKET/DY_PRIVATE_KEY 为空）—— "
                    "转为【只听不发】。请在该账号下点重新扫码恢复发送能力。")
                _send_ok = False
            if _send_ok and not await asyncio.to_thread(self._verify_credential, self.auth):
                logger.warning(f"[AUTH-021] " + "[auth] 发送账号凭证失效 —— 转为【只听不发】"
                    "（监听继续；请重新扫码恢复发送）。")
                _send_ok = False
            self._send_available = _send_ok
            if not _send_ok:
                self.status_msg = "监听中（只听不发·发送凭证不可用）"

            # 3) 构造 DispatchCenter（词库随机抽取由 self.pick_dm_message 提供）
            #    ENG-017：发送不可用时 enable_send=False —— 调度器仍接收并入库
            #    弹幕记录（前端可见「已捕获未发」），但不触发实际发送。
            self.dispatch = DispatchCenter(
                auth=self.auth,
                max_target=self.limit,
                delay_range=config.delay_range,
                interval=config.interval,
                enable_send=_send_ok,   # ENG-017：发送凭证不可用 → 只听不发
                pick_dm_message=self.pick_dm_message,
                gen_dm_message=self.gen_dm_message,
            )
            self.dispatch.on_idle = self._on_dispatch_idle
            await self.dispatch.start()

            # 4) 检查直播间是否开播
            #    ENG-017：check_room_live 只用 auth 发 GET 页面（cookie 只是「带上」），
            #    无凭证时传 None 走匿名请求同样能拿到 room_status/title。
            _live_auth = self.monitor_auth if getattr(self.monitor_auth, "cookie", None) else None
            self.status_msg = f"等待开播 {self.live_id}"
            is_live, room_status, room_title, live_info = await asyncio.to_thread(
                check_room_live, _live_auth, self.live_id
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
                        check_room_live, _live_auth, self.live_id
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
            # 2026-09-21（ENG-018）：无解密权必须**在 UI 上可见**，否则用户只看得到
            # 「监听中」而弹幕昵称全是脱敏值，无从判断是代码坏了还是账号没权限。
            # 2026-09-22（H-3）：文案改用判据产出的**原因标签**，让用户知道该修哪一条
            # （会话活性被拒 → 重新扫码；身份漂移 → 该账号凭证已错位，也需重扫/换号）。
            if getattr(self, "_live_session_ok", None) is False:
                _why = (getattr(self, "_live_ident_label", "")
                        or "无直播昵称解密权")
                self.status_msg = (f"监听中 {self.live_id}（昵称脱敏·{_why}，"
                                   f"请重新扫码）")
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
            # 置活点必须在 `LiveChatHook(...)` **之前**：构造与 on_idle 回调之间
            # 任何一刻被判成「不活跃」都会误收尾（ENG-019）。
            self._listen_started = True
            self._listen_ended = False
            self.live = LiveChatHook(self.live_id, self.monitor_auth, self.dispatch,
                                     controller=self, session_ok=self._live_session_ok,
                                     verdict_hint=(f"{self._live_ident_label}"
                                                   f"（{self._live_ident_detail}）"
                                                   if self._live_ident_detail else ""))
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
            # 监听线到此结束（ENG-019）：此后 on_idle 的收尾判据可以正常生效。
            self._listen_ended = True
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
            except Exception as _e_ws1:
                logger.warning(f"[auto_dm] 关闭WS时设置 _should_stop 异常: {_e_ws1}")
            if getattr(self.live, "ws", None):
                try:
                    self.live.ws.close()
                except Exception as _e_ws2:
                    logger.warning(f"[auto_dm] 关闭WS连接时异常: {_e_ws2}")
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
        #
        # 2026-09-21（ENG-015 根因修复）：这里的「监听是否活着」此前判成
        # `self.live.ws is not None`。而 `ws` 是 `run_forever()` 内部才赋值的，
        # **启动握手窗口内恒为 None** —— 于是本回调在引擎刚进 RUNNING、
        # WS 还没连上的那一两秒里被触发时，会把手握中的监听判成「已死」，
        # 直接把整个任务收尾成 STOPPED。
        # 实机（run_20260921_010907.log）：
        #   01:21:41 已在直播，直接开始监听
        #   01:21:41 [引擎] 私信收尾 … 状态=finished   ← 误收尾
        #   01:21:42 [live-ws] 连接已建立               ← WS 一秒后才连上
        # 后果：弹幕/AI 照常工作，但 engineState 恒为 stopped，
        #       前端徽章与「暂停/继续/停止」控件全部失效（用户报的现象）。
        #
        # 正确判据分两层：
        #   ① `_ws_alive`：WS 已连上，或正处于自动重连中（on_close 未走停止分支）；
        #   ② 回退：尚未置停且 ws 句柄已存在 —— 覆盖握手刚完成、
        #      on_open 尚未被调度的极短窗口，避免反向误判。
        # 另：上方 `self.state not in (RUNNING, STOPPING, PAUSED)` 的守卫已排除
        #     STARTING 态，本处不再重复判 STARTING（避免两处语义漂移）。
        if self._listen_line_active():
            return
        self.state = EngineState.STOPPED
        self.status_msg = "已停止"
        # 收尾已完成：解绑队列空回调（ENG-015 —— 调度器不再自行解绑）
        if self.dispatch is not None:
            self.dispatch.detach_on_idle()
        self._finish_history_task("finished")

    def _listen_line_active(self) -> bool:
        """监听线是否仍在运行（收尾判据的唯一真源）。

        设计契约：`live.ws` 只在 `run_forever()` 内赋值，握手窗口内为 None，
        **不能**单独用作存活判据（ENG-015）。本方法按「已连上 → 正在重连 →
        刚拿到句柄」的顺序判定，任一层成立即视为监听线存活。
        """
        # ① 已决定监听且尚未收尾 —— 覆盖「hook 已创建 / start_ws 尚未被调度」
        #    的窗口。该窗口内 `live.ws` 与 `live._ws_alive` 都还不存在，
        #    旧判据在此必然返回 False（= 把「还没开始」判成「已经死了」）。
        if getattr(self, "_listen_started", False) and not getattr(self, "_listen_ended", False):
            return True
        live = self.live
        if live is None:
            return False
        if getattr(live, "_ws_alive", False):
            return True
        # 回退：已拿到 ws 句柄且尚未被置停（覆盖 on_open 未及调度的窗口）
        return (
            getattr(live, "ws", None) is not None
            and not getattr(live, "_should_stop", False)
        )

    # ------------------------------------------------------------------
    # 重新扫码重建（迁移自 rescan_and_rebuild，同步逻辑）
    # ------------------------------------------------------------------
    def rescan_and_rebuild(self, account_name: Optional[str] = None) -> Any:
        """重新扫码指定账号，并立即用新凭证重建受影响的部分。

        注意：本方法在心跳线程中被调用（同步上下文），保留同步实现。
        """
        logger.info(f"[重扫重建] 开始为账号「{account_name}」重新扫码并重建会话…")
        # 2026-09-17 修补（OCR 审查 HIGH —— 占位符忽略入参，永远重扫"监测账号"）：
        # 原为硬编码 `env_path = "monitor.env"` + `is_monitor = is_sender = True`
        # 两条 TODO 占位 —— `account_name` 参数被**完全忽略**，无论调用方请求
        # 哪个账号，都会去重扫"监测/发送"账号的凭证（core/live_hook.py 的
        # 心跳自愈路径调用它时不传参，此时按「监测账号」语义解析是对的）。
        # 现：① 显式传入 account_name 时按名解析其 .env / 角色；
        #     ② 未传参时保持既有"默认监测+发送"语义（不引入行为回归）。
        from auto_dm import accounts as _acct_mod
        is_monitor = is_sender = True
        env_path = None
        if account_name:
            try:
                env_path = _acct_mod.env_path_of(account_name)
            except Exception:
                env_path = None
            if env_path:
                _mon = _acct_mod.monitor_name()
                _snd = _acct_mod.sender_name()
                # 角色：能确定时才收窄（无名可依则保持"两者皆是"）
                if _mon or _snd:
                    is_monitor = bool(_mon and account_name == _mon)
                    is_sender = bool(_snd and account_name == _snd)
                    if not (is_monitor or is_sender):
                        # 该账号既非监测也非发送 —— 仍需刷新其凭证，按发送账号处理
                        is_sender = True
        if not env_path:
            env_path = _acct_mod.current_env_path() or "monitor.env"
        logger.info(f"[重扫重建] env={env_path} 角色(监测={is_monitor}, 发送={is_sender})")

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

        # 2) 重新扫码（本路径本身即「重新扫码」语义 → 强制重扫）
        # 2026-09-19：「启动前强制重扫」配置项已彻底移除（用户定调：该策略早已废弃）。
        # 本方法只在调用方明确要求重建凭证时进入，故直接 force_fresh=True；
        # 凭证「过龄强制重扫」仍由 _build_one_auth 的 max_age 分支负责，与本项无关。
        auth = self._build_one_auth(env_path, force_fresh=True, max_age=0)
        if auth is None or not getattr(auth, "cookie", None):
            raise RuntimeError(
                f"账号「{account_name}」扫码未成功拿到有效凭证。"
                f"请确认浏览器已打开并完成抖音扫码，再重试。"
            )

        # 3)+4) 按账号角色重建
        if is_monitor or (not is_sender):
            self.monitor_auth = auth
            # 2026-09-21（ENG-018）→ 2026-09-22（H-3 返工）：重扫后必须**重新判定**
            # 解密权 —— 否则会带着旧的三态值继续监听（用户实测症状：重扫了仍未解密、
            # 日志无任何线索）。重扫 = 换凭证 ⇒ **两个判据的旧结论都必须作废**：
            #   ① 会话活性缓存（profile/self 的登录态）
            #   ② 身份判断据（探活 uid vs 历史 conv_id；AUTH-050）
            # 否则会出现「凭证已刷新、身份侧仍拿旧结论」的不一致窗口。
            try:
                from auto_dm.accounts import (
                    uid_identity_verdict as _uiv,
                    invalidate_live_identity_cache as _inval_li,
                )
                from services import uid_probe as _up
                _inval_li(account_name)
                _up.invalidate(account_name)          # 作废 uid 缓存 + 三态结论
                try:
                    _up.get_uid(account_name)         # 统一调度重探（受 TTL/锁门控）
                except Exception:                     # noqa: BLE001
                    pass
                _li_ok, _li_reason, _li_label, _li_detail = _uiv(
                    account_name, auth, force=False)
                self._live_session_ok = _li_ok        # 三态，不折叠
                self._live_ident_reason = _li_reason
                self._live_ident_label = _li_label
                self._live_ident_detail = _li_detail
                logger.info(f"[重扫重建] 直播昵称解密权判定：{_li_label} —— {_li_detail}")
            except Exception as _e:  # noqa: BLE001
                self._live_session_ok = None
                self._live_ident_label = ""
                logger.warning(f"[重扫重建] 解密权复测失败（结论未知，不据此降级）: {_e}")
            self.live = LiveChatHook(self.live_id, self.monitor_auth, self.dispatch,
                                     controller=self, session_ok=self._live_session_ok,
                                     verdict_hint=(f"{self._live_ident_label}"
                                                   f"（{self._live_ident_detail}）"
                                                   if self._live_ident_detail else ""))
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
          - **不重扫** 凭证（避免把运行中任务踢回扫码）；
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

        # ③ 调度参数热更
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
                gen_dm_message=self.gen_dm_message,
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

    # 2026-09-17 修补（OCR 审查 HIGH —— 同名方法重复定义）：
    # 此处原本还有第二个 `async def shutdown`，其函数体（仅 `if is_running:
    # stop(hard=True)`）在 Python 里**静默覆盖**了本文件上方那个带完整
    # 退出收尾逻辑（含非运行态的 `_finish_history_task("stopped")`）的定义，
    # 导致历史任务收尾在优雅退出时永不执行。已删除重复定义，保留上方完整版。
