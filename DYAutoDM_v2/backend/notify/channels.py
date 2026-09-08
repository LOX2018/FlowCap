"""IM 通知渠道统一抽象层（从 AstrBot 提取的可复用组件）

设计原则
--------
**只取「出站推送」这一层**，不搬 AstrBot 的事件系统 / Platform / Star 插件框架。
AstrBot 各平台适配器（weixin_oc / wecom / dingtalk / lark / qqofficial）都重度耦合
其内部的 `Platform` 基类、`AstrBotMessage`、`MessageChain`、事件队列，整文件搬进来
会拖入上千行无关代码。而 DYAutoDM 的需求（汇报 + 告警）**只需要出站**，不需要
入站事件建模 —— 所以这里只按各渠道的**真实 HTTP 协议**重写精简客户端。

协议依据（均实测取自 AstrBotDevs/AstrBot master）：
  - weixin_oc (iLink/ClawBot)  : base https://ilinkai.weixin.qq.com
        登录 GET  ilink/bot/get_bot_qrcode   (bot_type)
        轮询 GET  ilink/bot/get_qrcode_status (qrcode) -> status/bot_token/ilink_bot_id
        收   POST ilink/bot/getupdates        (get_updates_buf)
        发   POST ilink/bot/sendmessage       (msg.to_user_id/context_token/item_list)
  - wecom    : https://qyapi.weixin.qq.com/cgi-bin/  (corpid+corpsecret -> access_token)
  - dingtalk : https://api.dingtalk.com/v1.0/oauth2/accessToken
               POST /v1.0/robot/oToMessages/batchSend  (私聊)
               POST /v1.0/robot/groupMessages/send     (群)
  - lark     : lark-oapi SDK (app_id/app_secret)，im/v1/messages
  - qqofficial: QQ 官方机器人 API（appid/secret -> access_token）

每个渠道实现同一个接口：
    async def send_text(target: str, text: str) -> ChannelResult
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from loguru import logger

__all__ = [
    "ChannelResult",
    "ChannelError",
    "BaseChannel",
    "WeixinOCChannel",
    "WecomChannel",
    "DingtalkChannel",
    "LarkChannel",
    "QQOfficialChannel",
    "build_channel",
]


class ChannelError(Exception):
    """渠道发送失败。"""


@dataclass
class ChannelResult:
    ok: bool
    channel: str
    error: str = ""
    raw: dict[str, Any] = field(default_factory=dict)


class BaseChannel(ABC):
    """渠道基类：统一生命周期 + 重试 + 日志。

    子类只需实现 `_do_send`。
    """

    name: str = "base"

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg or {}
        self.enabled: bool = bool(self.cfg.get("enabled", False))
        self._session: Any = None  # aiohttp.ClientSession 惰性创建
        self._lock = asyncio.Lock()

    # ---------- 子类实现 ----------
    @abstractmethod
    async def _do_send(self, target: str, text: str) -> ChannelResult:
        ...

    # ---------- 公共 ----------
    async def _http(self):
        """惰性 aiohttp session（aiohttp 已在 requirements）。"""
        if self._session is None or self._session.closed:
            import aiohttp

            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            )
        return self._session

    async def send(self, target: str, text: str, retries: int = 2) -> ChannelResult:
        """带重试的发送入口。**永不抛异常** —— 失败以 ChannelResult 返回，
        避免通知失败把主业务（私信调度/引擎）拖挂。"""
        if not self.enabled:
            return ChannelResult(False, self.name, "渠道未启用")
        if not target:
            return ChannelResult(False, self.name, "缺少目标（target）")
        last = ""
        for i in range(retries + 1):
            try:
                r = await self._do_send(target, text)
                if r.ok:
                    return r
                last = r.error
            except Exception as e:  # noqa: BLE001
                last = f"{type(e).__name__}: {e}"
                logger.warning("NTY-006", f"[notify:{self.name}] 发送异常({i}/{retries}): {last}")
            if i < retries:
                await asyncio.sleep(1.5 * (i + 1))
        logger.error("NTY-007", f"[notify:{self.name}] 发送最终失败: {last}")
        return ChannelResult(False, self.name, last)

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    # 配置校验：缺必填项时给出**明确**原因，便于前端配置页提示
    @classmethod
    def missing_keys(cls, cfg: dict[str, Any]) -> list[str]:
        return [k for k in cls.REQUIRED if not str(cfg.get(k, "")).strip()]

    REQUIRED: tuple[str, ...] = ()


# ============================================================================
# 1. 个人微信 —— 腾讯官方 iLink Bot API（ClawBot）
#    来源：astrbot/core/platform/sources/weixin_oc/
# ============================================================================
class WeixinOCChannel(BaseChannel):
    """iLink 出站推送。

    **关键约束（实测）**：iLink 是「被动应答」模型 —— `sendmessage` 必须携带
    `context_token`，而该 token 只在**收到过该用户的消息**后才存在。
    因此：对方必须先给机器人发过一条消息，机器人才能回推。
    这是 iLink 协议本身的限制（AstrBot 日志原话：
    "context token missing for %s, skip send. You should send one message to
    refresh context_token."），不是实现缺陷。
    """

    name = "weixin_oc"
    REQUIRED = ("token",)

    def __init__(self, cfg: dict[str, Any]) -> None:
        super().__init__(cfg)
        self.base_url = str(
            cfg.get("base_url") or "https://ilinkai.weixin.qq.com"
        ).rstrip("/")
        self.token = str(cfg.get("token", "")).strip()
        self.account_id = str(cfg.get("account_id", "")).strip()
        # user_id -> context_token（真实环境应持久化；此处内存态 + 可选落盘）
        self._ctx: dict[str, str] = dict(cfg.get("context_tokens") or {})

    def remember_context(self, user_id: str, context_token: str) -> None:
        """收到入站消息时登记 context_token —— 之后才能对该用户回推。"""
        if user_id and context_token:
            self._ctx[str(user_id)] = str(context_token)

    def has_context(self, user_id: str) -> bool:
        return str(user_id) in self._ctx

    async def _do_send(self, target: str, text: str) -> ChannelResult:
        import uuid

        ctx = self._ctx.get(str(target), "")
        if not ctx:
            return ChannelResult(
                False,
                self.name,
                "缺少 context_token：iLink 需对方先发一条消息后才能回推",
            )
        s = await self._http()
        payload = {
            "base_info": {"channel_version": "dyautodm"},
            "msg": {
                "from_user_id": "",
                "to_user_id": str(target),
                "client_id": uuid.uuid4().hex,
                "message_type": 2,  # 2 = BOT 消息
                "message_state": 2,  # 2 = 终态
                "context_token": ctx,
                "item_list": [{"type": 1, "text_item": {"text": text}}],
            },
        }
        headers = {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Authorization": f"Bearer {self.token}",
            "X-WECHAT-UIN": str(int(time.time())),
        }
        async with s.post(
            f"{self.base_url}/ilink/bot/sendmessage", json=payload, headers=headers
        ) as resp:
            body = await resp.json(content_type=None)
        if resp.status != 200:
            return ChannelResult(
                False, self.name, f"HTTP {resp.status}: {body}", {"body": body}
            )
        # iLink 成功时 ret==0（AstrBot _is_successful_api_payload 同逻辑）
        ret = body.get("ret", -1)
        if ret not in (0, None):
            return ChannelResult(
                False,
                self.name,
                f"iLink err ret={ret} {body.get('errmsg', '')}",
                {"body": body},
            )
        return ChannelResult(True, self.name, raw={"body": body})


# ============================================================================
# 2. 企业微信 —— 自建应用消息
#    来源：astrbot/core/platform/sources/wecom/
# ============================================================================
class WecomChannel(BaseChannel):
    """企业微信自建应用推送（text 消息）。

    target 语义：userid 或群 chatid；多个用 `|` 分隔。
    空 target 且配置了 webhook 时 → 走群机器人 webhook（最简单，无需 corpid）。
    """

    name = "wecom"
    REQUIRED = ()  # corpid 或 webhook 二选一

    def __init__(self, cfg: dict[str, Any]) -> None:
        super().__init__(cfg)
        self.corpid = str(cfg.get("corpid", "")).strip()
        self.corpsecret = str(cfg.get("corpsecret", "")).strip()
        self.agent_id = str(cfg.get("agent_id", "")).strip()
        self.webhook = str(cfg.get("webhook", "")).strip()
        self.api_base = str(
            cfg.get("api_base_url") or "https://qyapi.weixin.qq.com/cgi-bin"
        ).rstrip("/")
        self._token: str = ""
        self._token_expire: float = 0.0

    async def _get_token(self) -> str:
        if self._token and time.time() < self._token_expire:
            return self._token
        s = await self._http()
        async with s.get(
            f"{self.api_base}/gettoken",
            params={"corpid": self.corpid, "corpsecret": self.corpsecret},
        ) as resp:
            body = await resp.json(content_type=None)
        if body.get("errcode") != 0:
            raise ChannelError(f"wecom gettoken: {body}")
        self._token = body["access_token"]
        self._token_expire = time.time() + int(body.get("expires_in", 7200)) - 300
        return self._token

    async def _do_send(self, target: str, text: str) -> ChannelResult:
        s = await self._http()
        # 群机器人 webhook 路径
        if self.webhook and not (self.corpid and self.corpsecret):
            async with s.post(
                self.webhook,
                json={"msgtype": "text", "text": {"content": text}},
            ) as resp:
                body = await resp.json(content_type=None)
            ok = body.get("errcode") == 0
            return ChannelResult(
                ok, self.name, "" if ok else f"webhook: {body}", {"body": body}
            )
        token = await self._get_token()
        # 实测依据：企业微信官方「发送应用消息」文档
        # POST https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token=
        # body: {touser, msgtype, agentid, text:{content}}
        # 成功判据 errcode==0；content 上限 2048 字节（超出会被截断）
        content = text[:2000]  # 预留编码膨胀，避免 2048 字节截断
        payload = {
            "touser": target or "@all",
            "msgtype": "text",
            "agentid": int(self.agent_id) if str(self.agent_id).isdigit() else self.agent_id,
            "text": {"content": content},
            "enable_duplicate_check": 0,
        }
        async with s.post(
            f"{self.api_base}/message/send", params={"access_token": token}, json=payload
        ) as resp:
            body = await resp.json(content_type=None)
        err = body.get("errcode")
        ok = err == 0
        # 部分接收人无效仍算成功（官方：返回 invaliduser 但已投递）
        invalid = body.get("invaliduser") or body.get("unlicenseduser")
        err_msg = "" if ok else f"errcode={err} {body.get('errmsg')}"
        if ok and invalid:
            logger.warning("NTY-008", f"[notify:wecom] 部分接收人无效: {invalid}")
        return ChannelResult(ok, self.name, err_msg, {"body": body, "invalid": invalid})


# ============================================================================
# 3. 钉钉 —— 机器人（支持私聊/群）
#    来源：astrbot/core/platform/sources/dingtalk/
# ============================================================================
class DingtalkChannel(BaseChannel):
    name = "dingtalk"
    REQUIRED = ("client_id", "client_secret")

    def __init__(self, cfg: dict[str, Any]) -> None:
        super().__init__(cfg)
        self.client_id = str(cfg.get("client_id", "")).strip()
        self.client_secret = str(cfg.get("client_secret", "")).strip()
        self.robot_code = str(cfg.get("robot_code", "")).strip() or self.client_id
        self.webhook = str(cfg.get("webhook", "")).strip()
        self.webhook_secret = str(cfg.get("webhook_secret", "")).strip()
        self._token: str = ""
        self._token_expire: float = 0.0

    async def _get_token(self) -> str:
        if self._token and time.time() < self._token_expire:
            return self._token
        s = await self._http()
        async with s.post(
            "https://api.dingtalk.com/v1.0/oauth2/accessToken",
            json={
                "appKey": self.client_id,
                "appSecret": self.client_secret,
            },
        ) as resp:
            body = await resp.json(content_type=None)
        if "accessToken" not in body:
            raise ChannelError(f"dingtalk token: {body}")
        self._token = body["accessToken"]
        self._token_expire = time.time() + int(body.get("expireIn", 7200)) - 300
        return self._token

    async def _do_send(self, target: str, text: str) -> ChannelResult:
        s = await self._http()
        # 自定义机器人 webhook（加签可选）
        if self.webhook:
            url = self.webhook
            if self.webhook_secret:
                import base64
                import hashlib
                import hmac
                import urllib.parse

                ts = str(round(time.time() * 1000))
                sign = urllib.parse.quote_plus(
                    base64.b64encode(
                        hmac.new(
                            self.webhook_secret.encode(),
                            f"{ts}\n{self.webhook_secret}".encode(),
                            hashlib.sha256,
                        ).digest()
                    )
                )
                url = f"{url}&timestamp={ts}&sign={sign}"
            async with s.post(
                url, json={"msgtype": "text", "text": {"content": text}}
            ) as resp:
                body = await resp.json(content_type=None)
            ok = body.get("errcode") == 0
            return ChannelResult(ok, self.name, "" if ok else str(body), {"body": body})
        token = await self._get_token()
        headers = {
            "Content-Type": "application/json",
            "x-acs-dingtalk-access-token": token,
        }
        payload = {
            "robotCode": self.robot_code,
            "userIds": [target],
            "msgKey": "sampleMarkdown",
            "msgParam": '{"title":"DYAutoDM","text":"%s"}' % text.replace('"', "'"),
        }
        async with s.post(
            "https://api.dingtalk.com/v1.0/robot/oToMessages/batchSend",
            headers=headers,
            json=payload,
        ) as resp:
            body = await resp.json(content_type=None)
        ok = resp.status < 400
        return ChannelResult(ok, self.name, "" if ok else str(body), {"body": body})


# ============================================================================
# 4. 飞书 —— 自建应用（lark-oapi）
#    来源：astrbot/core/platform/sources/lark/
# ============================================================================
class LarkChannel(BaseChannel):
    name = "lark"
    REQUIRED = ("app_id", "app_secret")

    def __init__(self, cfg: dict[str, Any]) -> None:
        super().__init__(cfg)
        self.app_id = str(cfg.get("app_id", "")).strip()
        self.app_secret = str(cfg.get("app_secret", "")).strip()
        self.webhook = str(cfg.get("webhook", "")).strip()
        self._token: str = ""
        self._token_expire: float = 0.0

    async def _get_token(self) -> str:
        if self._token and time.time() < self._token_expire:
            return self._token
        s = await self._http()
        async with s.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": self.app_id, "app_secret": self.app_secret},
        ) as resp:
            body = await resp.json(content_type=None)
        if body.get("code") != 0 or "tenant_access_token" not in body:
            raise ChannelError(f"lark token: {body}")
        self._token = body["tenant_access_token"]
        self._token_expire = time.time() + int(body.get("expire", 7200)) - 300
        return self._token

    async def _do_send(self, target: str, text: str) -> ChannelResult:
        s = await self._http()
        if self.webhook:
            async with s.post(
                self.webhook, json={"msg_type": "text", "content": {"text": text}}
            ) as resp:
                body = await resp.json(content_type=None)
            ok = body.get("code") == 0 or body.get("StatusCode") == 0
            return ChannelResult(ok, self.name, "" if ok else str(body), {"body": body})
        token = await self._get_token()
        # 群聊 chat_id 形如 oc_xxx；私聊 open_id 形如 ou_xxx
        id_type = "chat_id" if str(target).startswith("oc_") else "open_id"
        receive_id = target
        if id_type == "chat_id" and "%" in receive_id:
            receive_id = receive_id.split("%")[1]
        async with s.post(
            "https://open.feishu.cn/open-apis/im/v1/messages"
            f"?receive_id_type={id_type}",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {token}",
            },
            json={
                "receive_id": receive_id,
                "msg_type": "text",
                "content": '{"text":"%s"}' % text.replace('"', "'").replace("\n", "\\n"),
            },
        ) as resp:
            body = await resp.json(content_type=None)
        ok = body.get("code") == 0
        return ChannelResult(
            ok, self.name, "" if ok else f"code={body.get('code')} {body.get('msg')}", {"body": body}
        )


# ============================================================================
# 5. QQ 官方机器人
#    来源：astrbot/core/platform/sources/qqofficial/
# ============================================================================
class QQOfficialChannel(BaseChannel):
    name = "qqofficial"
    REQUIRED = ("appid", "secret")

    def __init__(self, cfg: dict[str, Any]) -> None:
        super().__init__(cfg)
        self.appid = str(cfg.get("appid", "")).strip()
        self.secret = str(cfg.get("secret", "")).strip()
        self.sandbox = bool(cfg.get("sandbox", False))
        self._token: str = ""
        self._token_expire: float = 0.0

    @property
    def _api_base(self) -> str:
        return (
            "https://sandbox.api.sgroup.qq.com"
            if self.sandbox
            else "https://api.sgroup.qq.com"
        )

    async def _get_token(self) -> str:
        if self._token and time.time() < self._token_expire:
            return self._token
        s = await self._http()
        async with s.post(
            "https://bots.qq.com/app/getAppAccessToken",
            json={"appId": self.appid, "clientSecret": self.secret},
        ) as resp:
            body = await resp.json(content_type=None)
        if "access_token" not in body:
            raise ChannelError(f"qq token: {body}")
        self._token = body["access_token"]
        self._token_expire = time.time() + int(body.get("expires_in", 7200)) - 300
        return self._token

    async def _do_send(self, target: str, text: str) -> ChannelResult:
        token = await self._get_token()
        s = await self._http()
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"QQBot {token}",
        }
        body: dict[str, Any] = {
            "content": text,
            "msg_type": 0,  # 0=文本
        }
        # group_xxx 视为群，否则按私聊（openid）
        if str(target).startswith("group_"):
            gid = target.split("group_", 1)[1]
            url = f"{self._api_base}/v2/groups/{gid}/messages"
        else:
            url = f"{self._api_base}/v2/users/{target}/messages"
        async with s.post(url, headers=headers, json=body) as resp:
            res = await resp.json(content_type=None)
        ok = resp.status < 400
        return ChannelResult(ok, self.name, "" if ok else str(res), {"body": res})


_REGISTRY: dict[str, type[BaseChannel]] = {
    "weixin_oc": WeixinOCChannel,
    "wecom": WecomChannel,
    "dingtalk": DingtalkChannel,
    "lark": LarkChannel,
    "qqofficial": QQOfficialChannel,
}


def build_channel(kind: str, cfg: dict[str, Any]) -> BaseChannel:
    cls = _REGISTRY.get(kind)
    if cls is None:
        raise ChannelError(
            f"未知渠道 {kind}，可选: {', '.join(_REGISTRY)}"
        )
    return cls(cfg)
