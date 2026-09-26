# coding=utf-8
"""login_api_vendor —— 上游（vendored）登录模块的**适配层**（ADR-017 API 备用路径）。

## 定位（SoC）
```
RPA（主路径）: auto_dm/login_remote.py   —— 真浏览器驱动，指纹天然真实
API（备用）  : 本模块                    —— 纯协议模拟，快且轻
```
本模块**只做适配**：把上游 `DYLoginApi` 包装成本项目可用的形式，
**不修改上游任何代码**（vendor 目录只读铁律，见 vendor/README.md）。

## 为什么需要适配层
上游以**相对路径**读 `.env` / 素材文件、且 `sys.path` 需注入；
本项目模块不能这样裸用。适配层负责：
  ① `sys.path` 注入 + `chdir` 到 vendor 目录（上游要求）
  ② 惰性导入（不在 import 期触发上游重依赖；RPA 主路径不受影响）
  ③ 把上游返回的 auth/cookie 转成本项目惯用的 dict 形式
  ④ 统一日志与错误码（沿用本项目 AUTH-0xx 体系）

## 契约（DbC）
Pre:
  P1 vendor 目录存在且含 `dy_apis/login_api.py`
  P2 依赖 `curl_cffi` 已装（缺则给出明确指引，不静默降级）
Post:
  C1 成功返回的 auth 至少含：cookie（dict）、private_key
  C2 失败时抛带错误码的异常，绝不返回半成品
Invariants:
  I1 **只读上游**：绝不写 vendor 目录内任何文件（除上游自己写 .env 的行为外）
  I2 惰性：不在模块导入期引入上游（避免拖慢/污染 RPA 主路径）
"""
from __future__ import annotations

import os
import sys
import threading
from typing import Optional

from loguru import logger

# ── vendor 目录定位（相对本文件：backend/auto_dm/ → ../../vendor/...）────────
_HERE = os.path.dirname(os.path.abspath(__file__))
VENDOR_DIR = os.path.abspath(
    os.path.join(_HERE, "..", "..", "vendor", "douyin_spider_upstream"))

_lock = threading.Lock()
_loaded = False


def vendor_available() -> bool:
    """vendor 目录与入口文件是否就位。"""
    return os.path.isfile(os.path.join(VENDOR_DIR, "dy_apis", "login_api.py"))


def _ensure_path() -> None:
    """注入 sys.path（幂等）。**不 chdir** —— chdir 是全局副作用，危险。

    上游用相对路径读 `.env` 的位置，由我们在调用处通过 `cwd` 参数或
    显式传参解决（见 `bootstrap`）。
    """
    global _loaded
    with _lock:
        if _loaded:
            return
        if not vendor_available():
            raise RuntimeError(
                f"[AUTH-070] 上游 vendor 目录缺失或结构不符: {VENDOR_DIR}。"
                f"请确认 vendor/douyin_spider_upstream 已就位（见 vendor/README.md）。")
        if VENDOR_DIR not in sys.path:
            sys.path.insert(0, VENDOR_DIR)
        _loaded = True


def _import_upstream():
    """惰性导入上游 DYLoginApi。缺依赖时给出**可操作**的报错。"""
    _ensure_path()
    try:
        from dy_apis.login_api import DYLoginApi  # type: ignore
    except ImportError as e:
        msg = str(e)
        if "curl_cffi" in msg:
            raise RuntimeError(
                "[AUTH-071] 上游 API 路径需要 curl_cffi（TLS/HTTP2 指纹冒充），"
                "当前环境未安装。安装：\n"
                f'  "{sys.executable}" -m pip install curl_cffi\n'
                "（注意：不要用裸 `uv pip install`，它会指向 Hermes 自己的 venv。）"
            ) from e
        raise
    return DYLoginApi


class VendorLoginApi:
    """上游登录接口的薄包装（API 备用路径的使用入口）。

    用法::

        api = VendorLoginApi()
        auth = api.bootstrap()               # 准备匿名会话
        qr   = api.get_qrcode(auth)          # {token, url, base64, raw}
        # 用户扫码…
        st   = api.check_qrcode(auth, qr["token"])   # 轮询状态
    """

    def __init__(self) -> None:
        self._api = None

    # ── 内部：确保上游已导入 ────────────────────────────────────────
    @property
    def api(self):
        if self._api is None:
            cls = _import_upstream()
            self._api = cls()
        return self._api

    # ── ① 引导匿名会话 ──────────────────────────────────────────────
    def bootstrap(self, *, strict: bool = False, proxies: Optional[dict] = None) -> dict:
        """准备可用于登录的 auth（自生成密钥 + 拉匿名 cookie）。

        ⚠️ 上游 `bootstrap_auth` 会 `load_dotenv(ENV_FILE)`（相对路径 ".env"）。
        为不污染进程 cwd，本方法**临时切换 cwd** 到 vendor 目录再恢复。
        """
        cls = _import_upstream()
        old_cwd = os.getcwd()
        try:
            os.chdir(VENDOR_DIR)
            auth = cls.bootstrap_auth(strict=strict, proxies=proxies)
        finally:
            os.chdir(old_cwd)
        logger.info("[vendor] bootstrap ok: cookie {} 项，private_key {}",
                    len(getattr(auth, "cookie", {}) or {}),
                    "有" if getattr(auth, "private_key", None) else "无")
        return auth

    # ── ② 取二维码（**唯一已验证可用的上游能力**）───────────────────
    def get_qrcode(self, auth) -> dict:
        """取登录二维码。

        返回统一结构::
            {"ok": bool, "token": str|None, "url": str|None,
             "png_b64": str|None, "error_code": int|None,
             "description": str, "raw": dict}
        """
        res = self.api.get_qrcode(auth)
        data = (res or {}).get("data") or {}
        out = {
            "ok": bool(data.get("token")),
            "token": data.get("token"),
            "url": data.get("qrcode_index_url") or data.get("qrcode_url"),
            "png_b64": data.get("qrcode"),
            "error_code": data.get("error_code"),
            "description": data.get("description") or (res or {}).get("message") or "",
            "raw": res,
        }
        if out["ok"]:
            logger.info("[vendor] get_qrcode ok: token={}…（error_code=0）",
                        str(out["token"])[:24])
        else:
            logger.warning("[vendor] get_qrcode 失败: error_code={} desc={}",
                           out["error_code"], out["description"][:80])
        return out

    # ── ③ 轮询扫码状态 ─────────────────────────────────────────────
    def check_qrcode(self, auth, token: str) -> dict:
        """轮询二维码状态。上游 status: new / scanned / confirmed / expired。"""
        res = self.api.check_qrcode(auth, token)
        data = (res or {}).get("data") or {}
        return {
            "status": data.get("status") or data.get("qr_status"),
            "error_code": data.get("error_code"),
            "description": data.get("description") or "",
            "redirect_url": data.get("redirect_url"),
            "raw": res,
        }

    # ── ④ 落凭证（写 .env）────────────────────────────────────────
    def save_credential(self, auth) -> str:
        """把登录凭证写入上游的 .env（vendor 目录内）。返回路径。"""
        old_cwd = os.getcwd()
        try:
            os.chdir(VENDOR_DIR)
            return self.api.save_credential(auth)
        finally:
            os.chdir(old_cwd)

    # ── 工具：cookie 转 dict（本项目惯用）──────────────────────────
    @staticmethod
    def cookies_of(auth) -> dict:
        return dict(getattr(auth, "cookie", {}) or {})
