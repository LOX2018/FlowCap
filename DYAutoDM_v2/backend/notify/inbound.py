# -*- coding: utf-8 -*-
"""IM 入站通道（v0.38.5）：iLink 个人微信 + QQ 官方机器人。

用户拍板（2026-09-09）：第一版入站落 QQ + 个人微信（其余三渠道待后续）。

协议依据（均实测，非凭记忆）：
  - iLink（AstrBotDevs/AstrBot@master weixin_oc_adapter 实测）：
      POST ilink/bot/getupdates  {get_updates_buf} -> {get_updates_buf, msgs[]}
        每条 msg: from_user_id / context_token / item_list[{type,text_item}]
        长轮询 timeout_ms（AstrBot 默认 35s），sync_buf 游标持久化
      扫码登录：GET ilink/bot/get_bot_qrcode?bot_type=3 -> {qrcode, qrcode_img_content}
                GET ilink/bot/get_qrcode_status?qrcode= -> {status, bot_token, ilink_bot_id, baseurl}
                confirmed 时返回 bot_token —— **即免手填 token 的门槛来源**
  - QQ 官方（botpy 1.2.1 实测）：
      botpy.Client(intents=botpy.Intents(public_messages=True))，
      on_c2c_message_create: message.author.user_openid / message.content / message.id
      被动回复：post_c2c_message(openid, msg_id=message.id) —— msg_id 5 分钟有效

所有入站消息一律先过 notify.gateway（pairing 模式默认拦截未授权来源），
通过的才交给 api/notify.py::handle_inbound_command 解析执行。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Optional

from loguru import logger

from . import channels
from .gateway import gateway


class InboundManager:
    """管理两个入站通道的生命周期。configure() 时按渠道配置启停。"""

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task] = []
        self._on_command: Optional[Callable] = None  # 注入 api 层处理器
        self.running = False

    def bind_handler(self, handler: Callable[[str, str, str, dict], Any]) -> None:
        """handler(channel_id, sender_id, text, meta) -> reply_text|None"""
        self._on_command = handler

    # ------------------------------------------------------------------
    def configure(self, cfg: dict[str, Any]) -> None:
        """热更新：停止全部通道 → 按配置重启（enabled 的 weixin_oc / qqofficial）。"""
        self.stop()
        if not cfg.get("enabled"):
            return
        for item in cfg.get("channels", []) or []:
            kind = str(item.get("kind", ""))
            cid = str(item.get("id") or kind)
            if not item.get("enabled"):
                continue
            try:
                if kind == "weixin_oc" and item.get("inbound_enabled", True):
                    t = asyncio.create_task(
                        self._run_ilink(cid, dict(item)),
                        name=f"inbound-ilink-{cid}",
                    )
                    self._tasks.append(t)
                elif kind == "qqofficial" and item.get("inbound_enabled", True):
                    t = asyncio.create_task(
                        self._run_qq(cid, dict(item)),
                        name=f"inbound-qq-{cid}",
                    )
                    self._tasks.append(t)
            except Exception as e:  # noqa: BLE001
                logger.warning("IB-001", f"[inbound] {cid}({kind}) 启动失败: {e}")
        self.running = bool(self._tasks)
        if self.running:
            logger.info(f"[inbound] 入站通道已启动: {len(self._tasks)} 个")

    def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        self._tasks = []
        self.running = False

    # ------------------------------------------------------------------
    async def _dispatch(self, channel_id: str, sender_id: str, text: str,
                        meta: dict[str, Any]) -> Optional[str]:
        """入站消息统一入口：网关 → 处理器。返回应回复的文本。"""
        d = gateway.check(channel_id, sender_id, text)
        if d["action"] == "pending":
            return ("你好，我是 DYAutoDM 助手。你的身份待管理员确认，"
                    "确认后即可使用指令功能，请稍候。")
        if d["action"] == "reject":
            return None  # 拉黑者不回复
        # allow → 交业务处理器（含意图级权限校验）
        if self._on_command is None:
            return None
        meta["gateway_role"] = d.get("role")
        meta["sender_key"] = gateway.sender_key(channel_id, sender_id)
        try:
            return await self._on_command(channel_id, sender_id, text, meta)
        except Exception as e:  # noqa: BLE001
            logger.exception("IB-002", f"[inbound] 处理异常: {e}")
            return "指令处理出错，请稍后再试。"

    # ==================================================================
    # iLink 个人微信：扫码登录 + getupdates 长轮询
    # ==================================================================
    async def _run_ilink(self, cid: str, cfg: dict[str, Any]) -> None:
        ch = channels.build_channel("weixin_oc", cfg)
        base = ch.base_url
        token = ch.token
        sync_buf = str(cfg.get("sync_buf", ""))

        async def _req(method: str, endpoint: str, **kw) -> dict:
            import aiohttp

            headers = {"Content-Type": "application/json"}
            if token:
                headers["AuthorizationType"] = "ilink_bot_token"
                headers["Authorization"] = f"Bearer {token}"
                headers["X-WECHAT-UIN"] = str(int(time.time()))
            timeout = aiohttp.ClientTimeout(
                total=kw.pop("timeout_s", 40) or 40)
            async with aiohttp.ClientSession(timeout=timeout) as s:
                if method == "GET":
                    async with s.get(f"{base}{endpoint}",
                                     params=kw.get("params"), headers=headers) as r:
                        return await r.json(content_type=None)
                async with s.post(f"{base}{endpoint}",
                                  json=kw.get("payload", {}), headers=headers) as r:
                    return await r.json(content_type=None)

        # ---- 无 token 时自动扫码登录（免手填门槛的核心）----
        if not token:
            try:
                logged = await _ilink_qr_login(cid, _req, cfg)
                if logged:
                    token = logged
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning("IB-003", f"[inbound] {cid} iLink 扫码登录失败: {e}")
                return
        if not token:
            return

        logger.info(f"[inbound] {cid} iLink getupdates 长轮询启动")
        while True:
            try:
                data = await _req(
                    "POST", "/ilink/bot/getupdates",
                    payload={"base_info": {"channel_version": "dyautodm"},
                             "get_updates_buf": sync_buf},
                    timeout_s=40,
                )
                if str(data.get("ret", "0")) not in ("0", "None") and data.get("ret") != 0:
                    # 会话过期等错误 → 提示重新扫码
                    logger.warning("IB-004", f"[inbound] {cid} getupdates 错误: {data}")
                    await asyncio.sleep(5)
                    continue
                new_buf = data.get("get_updates_buf")
                if new_buf:
                    sync_buf = str(new_buf)
                    _save_sync_buf(cid, cfg, sync_buf)
                msgs = data.get("msgs") or []
                for msg in msgs:
                    if not isinstance(msg, dict):
                        continue
                    sender = str(msg.get("from_user_id", "")).strip()
                    if not sender:
                        continue
                    ctx = str(msg.get("context_token", "")).strip()
                    if ctx and hasattr(ch, "remember_context"):
                        ch.remember_context(sender, ctx)
                    text = _ilink_text(msg)
                    reply = await self._dispatch(cid, sender, text,
                                                 {"context_token": ctx})
                    if reply and ctx:
                        try:
                            await ch.send(sender, reply)
                        except Exception as e:  # noqa: BLE001
                            logger.warning("IB-005", f"[inbound] {cid} 回复失败: {e}")
            except asyncio.CancelledError:
                raise
            except Exception as e:  # noqa: BLE001
                logger.warning("IB-006", f"[inbound] {cid} 轮询异常: {e}")
                await asyncio.sleep(5)

    # ==================================================================
    # QQ 官方机器人：botpy WebSocket（C2C + 群@）
    # ==================================================================
    async def _run_qq(self, cid: str, cfg: dict[str, Any]) -> None:
        try:
            import botpy
        except ImportError:
            logger.warning("IB-007", "[inbound] qq-botpy 未安装，QQ 入站不可用")
            return
        appid = str(cfg.get("appid", "")).strip()
        secret = str(cfg.get("secret", "")).strip()
        if not appid or not secret:
            logger.warning("IB-008", f"[inbound] {cid} 缺 appid/secret，QQ 入站不启动")
            return

        mgr = self

        class _Client(botpy.Client):
            def __init__(self) -> None:
                intents = botpy.Intents(public_messages=True)
                super().__init__(intents=intents, bot_log=False)

            async def on_ready(self):
                logger.info(f"[inbound] {cid} QQ WS 已连接")

            async def on_c2c_message_create(self, message):  # 私聊
                sender = str(getattr(getattr(message, "author", None),
                                     "user_openid", "") or "")
                text = str(getattr(message, "content", "") or "").strip()
                if not sender or not text:
                    return
                reply = await mgr._dispatch(cid, sender, text,
                                            {"msg_id": getattr(message, "id", "")})
                if reply:
                    try:
                        await message.reply(content=reply[:800])
                    except Exception as e:  # noqa: BLE001
                        # 被动回复 5 分钟窗口外的失败如实记录
                        logger.warning("IB-009", f"[inbound] {cid} QQ 回复失败: {e}")

            async def on_group_at_message_create(self, message):  # 群@机器人
                sender = str(getattr(getattr(message, "author", None),
                                     "member_openid", "") or "")
                text = str(getattr(message, "content", "") or "").strip()
                # 群场景以群为会话主体，sender 记 group_openid 便于整群授权
                group = str(getattr(message, "group_openid", "") or "")
                if not group or not text:
                    return
                reply = await mgr._dispatch(cid, f"group_{group}", text,
                                            {"msg_id": getattr(message, "id", "")})
                if reply:
                    try:
                        await message.reply(content=reply[:800])
                    except Exception as e:  # noqa: BLE001
                        logger.warning("IB-009", f"[inbound] {cid} QQ 群回复失败: {e}")

        client = _Client()
        try:
            await client.start(appid=appid, secret=secret)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning("IB-010", f"[inbound] {cid} QQ WS 断开: {e}")


# ----------------------------------------------------------------------
# iLink 辅助
# ----------------------------------------------------------------------

def _ilink_text(msg: dict[str, Any]) -> str:
    parts = []
    for item in (msg.get("item_list") or []):
        try:
            if int(item.get("type") or 0) == 1:
                parts.append(str(item.get("text_item", {}).get("text", "")))
        except (TypeError, ValueError):
            continue
    return "".join(parts).strip()


def _save_sync_buf(cid: str, cfg: dict[str, Any], buf: str) -> None:
    """sync_buf 游标持久化（写回 notify_config.json 的渠道项）。"""
    try:
        from api.notify import load_config, save_config_file

        cfg_full = load_config()
        for item in cfg_full.get("channels", []) or []:
            if str(item.get("id") or item.get("kind")) == cid:
                item["sync_buf"] = buf
                break
        save_config_file(cfg_full)
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[inbound] sync_buf 保存失败（不致命）: {e}")


async def _ilink_qr_login(cid: str, req, cfg: dict[str, Any]) -> str:
    """扫码登录：拿 qrcode → 轮询 status → confirmed 时返回 bot_token。

    免手填门槛的核心：用户只需在本机 UI 点「扫码登录」并扫一次，
    token/bot_id 自动落配置（对齐 AstrBot login_registration 实测协议）。
    """
    import aiohttp

    # 1) 取二维码
    data = await req("GET", "/ilink/bot/get_bot_qrcode",
                     params={"bot_type": "3"}, timeout_s=20)
    qrcode = str(data.get("qrcode", "")).strip()
    qr_img = str(data.get("qrcode_img_content", "")).strip()
    if not qrcode:
        raise RuntimeError(f"qrcode 响应异常: {data}")
    logger.info(f"[inbound] {cid} iLink 登录二维码已就绪（5 分钟内扫码）")
    # 二维码内容交由设置页展示（img 内容为 URL，前端可直接生成二维码图）
    _notify_qr(cid, qrcode, qr_img)

    # 2) 长轮询扫码状态（最多 5 分钟）
    deadline = time.time() + 300
    while time.time() < deadline:
        st = await req("GET", "/ilink/bot/get_qrcode_status",
                       params={"qrcode": qrcode}, timeout_s=40)
        status = str(st.get("status", "wait")).strip()
        if status == "confirmed":
            token = str(st.get("bot_token", "")).strip()
            account_id = str(st.get("ilink_bot_id", "")).strip()
            base_url = str(st.get("baseurl", "")).strip()
            if not token:
                raise RuntimeError("confirmed 但无 bot_token")
            # 自动落配置（token/account_id/base_url），用户零手填
            _save_login(cid, cfg, token, account_id, base_url)
            logger.info(f"[inbound] {cid} iLink 登录成功，凭证已自动保存")
            return token
        if status == "expired":
            raise RuntimeError("二维码已过期")
        await asyncio.sleep(2)
    raise RuntimeError("扫码超时")


def _notify_qr(cid: str, qrcode: str, qr_img: str) -> None:
    """把待扫码状态写入 kv，供设置页轮询展示。"""
    try:
        from database import set_kv_json

        set_kv_json("notify.ilink.qr", {
            "channel_id": cid,
            "qrcode": qrcode,
            "qr_img": qr_img,
            "started_at": int(time.time()),
        })
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[inbound] qr 状态写入失败: {e}")


def _save_login(cid: str, cfg: dict[str, Any], token: str,
                account_id: str, base_url: str) -> None:
    try:
        from api.notify import load_config, save_config_file

        cfg_full = load_config()
        for item in cfg_full.get("channels", []) or []:
            if str(item.get("id") or item.get("kind")) == cid:
                if token:
                    item["token"] = token
                if account_id:
                    item["account_id"] = account_id
                if base_url:
                    item["base_url"] = base_url
                break
        save_config_file(cfg_full)
    except Exception as e:  # noqa: BLE001
        logger.warning("IB-011", f"[inbound] 登录凭证保存失败: {e}")


# 全局单例
inbound = InboundManager()
