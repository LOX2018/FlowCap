# -*- coding: utf-8 -*-
"""媒体流地址签名 —— 「签名即授权」的单一真源（2026-10-03，方案 A）。

## 为什么需要它

`/api/platform/media/stream` 端点的**设计意图**是让 `<video src>` 直接指向
（支持 Range、边收边转、可拖进度条）。但会员门禁中间件会拦下**不带
`X-Member-Token`** 的裸请求 → 401；而 `<video>` 无法携带自定义请求头，
前端被迫改走「带令牌 fetch 全量成 Blob」—— 对大文件（实测单作品可达约 400MB）
意味着**必须整片下载完才开始播放**，表现为「一直加载」。

## 契约（两端必须同源）

- `sign()` 生成 / `verify()` 校验：字段顺序与算法**只在本模块定义一次**，
  `main.py`（会员门禁中间件）与 `api/platform.py`（端点）都从这里取，
  禁止任一端自行实现（避免漂移 ⇒ 一半放行一半 403）。
- 签名**绑定 member_id** ⇒ 换会员 / 登出后旧链接自动失效。
- 密钥 = 当前会员 `master_key`（内存会话优先）；读不到时退回静态盐
  （仅本机开发态语义，生产必有会员会话）。
- 过期由调用方在 `exp` 里给定（本模块只按 `now` 判定语义）。
"""
from __future__ import annotations

import hashlib
import hmac

_SIG_LEN = 48
_FALLBACK_SECRET = "dyautodm-media-stream-v1"


def current_member_id() -> str:
    """当前会员 ID（无会话返回空串）。签名绑定它 ⇒ 会话切换即失效。"""
    try:
        from services import member_ctx
        c = member_ctx.current()
        if isinstance(c, dict):
            return str(c.get("member_id") or "")
        if isinstance(c, str) and c:
            s = member_ctx.get_session(c)
            if isinstance(s, dict):
                return str(s.get("member_id") or "")
    except Exception:  # noqa: BLE001 —— 无会员上下文时不阻断判定
        pass
    return ""


def _secret() -> str:
    """签名密钥：会员 master_key 优先，读不到退回静态盐。"""
    try:
        from services import member_ctx
        mk = member_ctx.master_key() or ""
    except Exception:  # noqa: BLE001
        mk = ""
    return mk or _FALLBACK_SECRET


def _digest(member_id: str, aweme_id: str, exp: int) -> str:
    msg = f"{member_id}:{aweme_id}:{exp}".encode()
    return hmac.new(_secret().encode(), msg, hashlib.sha256).hexdigest()[:_SIG_LEN]


def sign(aweme_id: str, exp: int, member_id: str = "") -> str:
    """生成流地址签名。`member_id` 由调用方从当前会员会话取。"""
    return _digest(str(member_id or ""), str(aweme_id or ""), int(exp))


def verify(aweme_id: str, exp: int, member_id: str, sig: str,
           now: int) -> tuple[bool, str]:
    """校验签名与过期。返回 `(是否通过, 失败原因)` —— 原因供日志定位。"""
    if not sig:
        return (False, "sig 缺失")
    if not member_id:
        return (False, "mid 缺失")
    if int(exp) < int(now):
        return (False, "签名已过期")
    want = _digest(str(member_id), str(aweme_id or ""), int(exp))
    if not hmac.compare_digest(want, sig):
        return (False, "签名无效")
    return (True, "")
