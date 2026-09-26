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
import base64
import hashlib
import json
import random
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from loguru import logger

def _channel_version() -> str:
    """iLink `base_info.channel_version` —— 官方规范要求填**SDK 版本号**。

    依据：`wechatbot.dev/zh/protocol` §3.2 ——「把 channel_version 设为实际 SDK
    版本号，便于排查兼容问题」；腾讯官方 `@tencent-weixin/openclaw-weixin v1.0.2`
    的 `BASE_INFO = { channel_version: '1.0.0' }` 亦如此（常见值 0.1.0/1.0.0/1.0.2）。
    本项目取自身构建版本（`_build_version.BUILD_VERSION`），异常时回退 "0.0.0"。
    """
    try:
        from _build_version import BUILD_VERSION  # type: ignore
        return str(BUILD_VERSION)
    except Exception:  # noqa: BLE001
        return "0.0.0"


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
        避免通知失败把主业务（私信调度/引擎）拖挂。

        ⚠️ 不可重试的错误会**立即返回**，不做重试等待（2026-09-26 实测）：
        iLink 的 `ret=-2`（ctx 配额用尽 / item_list 非法）属**确定性失败**，
        重试不会成功，只会白等 `1.5 + 3.0 = 4.5s`（实测含网络约 6s）。
        """
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
                if self._is_terminal_error(r):
                    logger.warning(
                        f"[NTY-014] [notify:{self.name}] 确定性失败，跳过重试: {last}"
                    )
                    return r
            except Exception as e:  # noqa: BLE001
                last = f"{type(e).__name__}: {e}"
                logger.warning(f"[NTY-006] " + f"[notify:{self.name}] 发送异常({i}/{retries}): {last}")
            if i < retries:
                await asyncio.sleep(1.5 * (i + 1))
        logger.error(f"[NTY-007] " + f"[notify:{self.name}] 发送最终失败: {last}")
        return ChannelResult(False, self.name, last)

    def _is_terminal_error(self, r: "ChannelResult") -> bool:
        """该失败是否**重试无意义**（确定性错误）。

        默认实现：不判定（保持既有行为）。渠道可覆写。
        判据来源：iLink `ret=-2` 属【确定性失败】（会话上下文不可用/参数非法），
        与网络瞬时故障（值得重试）性质不同。
        """
        return False

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
            "base_info": {"channel_version": _channel_version()},
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
            # X-WECHAT-UIN 必须是 base64(random 32bit)——实测 str(time()) 会被服务端判 ret=-1。
            # 依据：AstrBot weixin_oc_client.py::_build_base_headers（上游权威实现）。
            "X-WECHAT-UIN": base64.b64encode(
                str(random.getrandbits(32)).encode("utf-8")
            ).decode("utf-8"),
        }
        async with s.post(
            f"{self.base_url}/ilink/bot/sendmessage", json=payload, headers=headers
        ) as resp:
            body = await resp.json(content_type=None)
        if resp.status != 200:
            return ChannelResult(
                False, self.name, f"HTTP {resp.status}: {body}", {"body": body}
            )
        # iLink 成功判据（依据 AstrBot `_is_successful_api_payload`，上游权威实现）：
        #   成功响应【不含 ret 字段】（形如 {"message_id": ...}），故 ret/errcode 默认值必须为 0。
        #   早期实现用 body.get("ret", -1) ⇒ 把"无 ret 的成功响应"误判为 ret=-1 失败，
        #   表现为"消息其实已发出，但通道报失败"（实测 2026-09-26）。
        ret = int(body.get("ret", 0) or 0)
        errcode = int(body.get("errcode", 0) or 0)
        if ret != 0 or errcode != 0:
            return ChannelResult(
                False,
                self.name,
                f"iLink err ret={ret} errcode={errcode} {body.get('errmsg', '')}",
                {"body": body},
            )
        return ChannelResult(True, self.name, raw={"body": body})

    # ---------- 图片推送（iLink CDN 三步：getuploadurl → 加密上传 → sendmessage）----------
    # 协议依据：AstrBot weixin_oc_adapter.py::_prepare_media_item
    #           + weixin_oc_client.py::upload_to_cdn（上游权威实现）
    IMAGE_ITEM_TYPE = 2      # item_list 里的图片类型
    IMAGE_UPLOAD_TYPE = 1    # getuploadurl 的 media_type
    CDN_BASE_URL = "https://novac2c.cdn.weixin.qq.com/c2c"

    @staticmethod
    def _pkcs7_pad(data: bytes, block: int = 16) -> bytes:
        pad = block - (len(data) % block)
        return data + bytes([pad]) * pad

    @staticmethod
    def _aes_padded_size(n: int, block: int = 16) -> int:
        return n + (block - (n % block) or block)

    def _headers(self) -> dict[str, str]:
        """iLink 公共请求头（含 X-WECHAT-UIN：base64(random32)，与上游一致）。"""
        return {
            "Content-Type": "application/json",
            "AuthorizationType": "ilink_bot_token",
            "Authorization": f"Bearer {self.token}",
            "X-WECHAT-UIN": base64.b64encode(
                str(random.getrandbits(32)).encode("utf-8")
            ).decode("utf-8"),
        }

    def _is_terminal_error(self, r: ChannelResult) -> bool:
        """iLink 的哪些失败重试无意义（2026-09-26 实测）。

        · `ret=-2 "prepare failed"`   → context_token 配额用尽（等多久都不恢复）
        · `ret=-2 "invalid arguments"`→ item_list 组合非法（如 text+image 混排）
        · `ret=-2` 其它 / `errcode=-14`(SESSION_TIMEOUT) → 同样确定性，需重新登录
        ⇒ 这些都必须**立即返回**：上游 SESSION_TIMEOUT_ERRCODE=-14 亦属此列。
        反面：网络异常（异常分支）仍应重试 —— 那些走不到本方法。
        """
        if r.ok:
            return False
        err = str(r.error or "")
        return "ret=-2" in err or "ret=-14" in err or "SESSION_TIMEOUT" in err.upper()

    async def _upload_media(self, target: str, raw: bytes) -> Optional[dict[str, Any]]:
        """上传字节到 iLink CDN → 返回可直接放进 item_list 的 image_item，失败返回 None。

        三步（上游一致）：① getuploadurl（拿 upload_full_url/upload_param + aeskey 协商）
                          ② POST CDN 密文（响应头 x-encrypted-param 即下载凭证）
        """
        import uuid
        from urllib.parse import quote

        try:
            from Crypto.Cipher import AES  # pycryptodome，AstrBot 同款依赖
        except ImportError:
            logger.error("[NTY-008] [notify:weixin_oc] 缺少 pycryptodome，无法推送图片")
            return None

        raw_size = len(raw)
        raw_md5 = hashlib.md5(raw).hexdigest()
        file_key = uuid.uuid4().hex
        aes_key_hex = uuid.uuid4().bytes.hex()
        ct_size = self._aes_padded_size(raw_size)

        s = await self._http()
        # ① 申请上传地址
        try:
            async with s.post(
                f"{self.base_url}/ilink/bot/getuploadurl",
                json={
                    "filekey": file_key,
                    "media_type": self.IMAGE_UPLOAD_TYPE,
                    "to_user_id": str(target),
                    "rawsize": raw_size,
                    "rawfilemd5": raw_md5,
                    "filesize": ct_size,
                    "no_need_thumb": True,
                    "aeskey": aes_key_hex,
                    "base_info": {"channel_version": _channel_version()},
                },
                headers=self._headers(),
            ) as resp:
                body = await resp.json(content_type=None)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[NTY-008] [notify:weixin_oc] getuploadurl 异常: {e}")
            return None
        if resp.status != 200:
            logger.warning(f"[NTY-008] getuploadurl HTTP {resp.status}: {body}")
            return None
        upload_param = str(body.get("upload_param", "")).strip()
        upload_full_url = str(body.get("upload_full_url", "")).strip()
        if not upload_param and not upload_full_url:
            logger.warning(f"[NTY-008] getuploadurl 未返回上传地址: {body}")
            return None

        # ② 加密上传（AES-128-ECB + PKCS7）
        cdn_url = upload_full_url or (
            f"{self.CDN_BASE_URL}/upload?encrypted_query_param={quote(upload_param)}"
            f"&filekey={quote(file_key)}"
        )
        ciphertext = AES.new(bytes.fromhex(aes_key_hex), AES.MODE_ECB).encrypt(
            self._pkcs7_pad(raw)
        )
        try:
            async with s.post(
                cdn_url,
                data=ciphertext,
                headers={"Content-Type": "application/octet-stream"},
            ) as resp:
                detail = await resp.text()
                eqp = resp.headers.get("x-encrypted-param")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[NTY-008] [notify:weixin_oc] CDN 上传异常: {e}")
            return None
        if resp.status != 200 or not eqp:
            logger.warning(f"[NTY-008] CDN 上传失败 HTTP {resp.status}: {detail[:200]}")
            return None

        return {
            "type": self.IMAGE_ITEM_TYPE,
            "image_item": {
                "media": {
                    "encrypt_query_param": eqp,
                    "aes_key": base64.b64encode(aes_key_hex.encode("utf-8")).decode(
                        "utf-8"
                    ),
                    "encrypt_type": 1,
                },
                "mid_size": ct_size,
            },
        }

    async def send_image(
        self, target: str, image_path: str, caption: str = ""
    ) -> ChannelResult:
        """推送本地图片文件到指定 iLink 用户（target 需已有 context_token）。

        `caption` 非空 → 先单独发一条文本，再发图片。**不可混排在同一个 item_list**
        （实测 2026-09-26：text+image 混排会被服务端判 ret=-2 "invalid arguments"，
        纯 image item_list 才成功）。

        ## ⚠️ context_token 是【有限配额】而非时效（2026-09-26 实测订正）
        实测同一 ctx 连续推送：
          · 第 1~2 次 → 成功
          · 第 3 次起 → `ret=-2 "prepare failed"`
          · 等待 30s / 60s 后重试 → 仍失败（**不会随时间恢复**）
          · 收到对方**新消息**（新 ctx）→ 立即全部恢复成功
        ⇒ 结论：一个 context_token 约可用 2~3 次，**用尽即失效**；
          恢复只能靠对方再发一条消息（新的入站事件携带新 ctx）。
          ⚠️ 注意 `bot_token`（绑定凭证）才是长效的，两者勿混淆。
        """
        if not self.enabled:
            return ChannelResult(False, self.name, "渠道未启用")
        if not target:
            return ChannelResult(False, self.name, "缺少目标（target）")
        ctx = self._ctx.get(str(target), "")
        if not ctx:
            return ChannelResult(
                False, self.name, "缺少 context_token：iLink 需对方先发一条消息后才能回推"
            )
        try:
            raw = Path(image_path).read_bytes()
        except Exception as e:  # noqa: BLE001
            return ChannelResult(False, self.name, f"读取图片失败: {e}")

        # caption 先单独发（不混排——见 docstring 的实测依据）
        if caption:
            cap = await self.send(target, caption)
            if not cap.ok:
                logger.warning(f"[NTY-008] [notify:weixin_oc] caption 发送失败: {cap.error}")

        item = await self._upload_media(target, raw)
        if item is None:
            return ChannelResult(False, self.name, "图片上传 CDN 失败")

        import uuid

        s = await self._http()
        payload = {
            "base_info": {"channel_version": _channel_version()},
            "msg": {
                "from_user_id": "",
                "to_user_id": str(target),
                "client_id": uuid.uuid4().hex,
                "message_type": 2,
                "message_state": 2,
                "context_token": ctx,
                "item_list": [item],
            },
        }
        async with s.post(
            f"{self.base_url}/ilink/bot/sendmessage",
            json=payload,
            headers=self._headers(),
        ) as resp:
            body = await resp.json(content_type=None)
        if resp.status != 200:
            return ChannelResult(
                False, self.name, f"HTTP {resp.status}: {body}", {"body": body}
            )
        ret = int(body.get("ret", 0) or 0)
        errcode = int(body.get("errcode", 0) or 0)
        if ret != 0 or errcode != 0:
            return ChannelResult(
                False,
                self.name,
                f"iLink err ret={ret} errcode={errcode} {body.get('errmsg', '')}",
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
            logger.warning(f"[NTY-008] " + f"[notify:wecom] 部分接收人无效: {invalid}")
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
            # 2026-09-17 修补（OCR 审查 HIGH）：原为手工 `%` 拼接手搓 JSON
            #   '{"title":"DYAutoDM","text":"%s"}' % text.replace('"', "'")
            # 缺陷：① 未做 JSON 转义（反斜杠/控制字符破坏 JSON）；
            #      ② 文本含 `%` 时 `%` 格式化直接抛 ValueError（如"完成率 80%"）；
            #      ③ 把 `"` 换成 `'` 是改写原文而非转义。
            # 现用 json.dumps 正确构造（外层 json=payload 会再序列化一次）。
            "msgParam": json.dumps({"title": "DYAutoDM", "text": text},
                                   ensure_ascii=False),
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
                # 2026-09-17 修补（OCR 审查 HIGH）：同钉钉分支 —— 原为
                #   '{"text":"%s"}' % text.replace('"', "'").replace("\n", "\\n")
                # 仅转义 `"` 与 `\n`；反斜杠、`\r`/`\t`/其它控制字符未转义 →
                # 产生非法 JSON；文本含 `%` 时 `%` 格式化抛错。
                "content": json.dumps({"text": text}, ensure_ascii=False),
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
