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
    """注入 sys.path（幂等，**且保证 VENDOR_DIR 在最前**）。**不 chdir** —— chdir 是全局副作用，危险。

    ⚠️ 只在「**确认本进程没有加载项目同名包**」后才可调用（见 `_import_upstream`）——
    否则把 vendor 插到 `sys.path[0]` 会**遮蔽项目包**，污染宿主进程。

    2026-09-26（H-22 审计 idx3）：原实现只在 `VENDOR_DIR not in sys.path` 时插入 ——
    若调用方已把该目录放进 path 但**不在最前**（或把项目路径排在更前），
    就会解析到**项目**同名包 ⇒ 必须把这个目录移到最前。
    """
    global _loaded
    with _lock:
        if not vendor_available():
            raise RuntimeError(
                f"[AUTH-070] 上游 vendor 目录缺失或结构不符: {VENDOR_DIR}。"
                f"请确认 vendor/douyin_spider_upstream 已就位（见 vendor/README.md）。")
        if _loaded and sys.path and sys.path[0] == VENDOR_DIR:
            return
        if VENDOR_DIR in sys.path:
            sys.path.remove(VENDOR_DIR)
        sys.path.insert(0, VENDOR_DIR)
        _loaded = True


# vendor 与 backend **同名**的顶层包（交集）。任一解析到项目包，
# 上游 login_api.py 的顶层绝对导入（`from builder.auth import ...` 等）就会
# 命中项目实现 ⇒ 静默半坏。故必须**逐个**校验，不能只查 dy_apis。
_SHARED_TOP_PKGS = ("dy_apis", "builder", "utils", "dy_live", "static")


def _loaded_origin(name: str) -> str:
    """`name` **已加载**时的来源路径（未加载 → 空串）。"""
    m = sys.modules.get(name)
    if m is None:
        return ""
    f = str(getattr(m, "__file__", "") or "")
    if f:
        return f
    locs = list(getattr(m, "__path__", None) or [])
    return str(locs[0]) if locs else ""


def _resolvable_origin(name: str) -> str:
    """`name` **将解析到**的位置（已加载 → 取其来源；否则按 sys.path 查）。"""
    o = _loaded_origin(name)
    if o:
        return o
    try:
        import importlib.util as _ilu
        spec = _ilu.find_spec(name)
    except Exception:
        return ""
    if spec is None:
        return ""
    locs = list(getattr(spec, "submodule_search_locations", None) or [])
    if locs:
        return str(locs[0])
    return str(getattr(spec, "origin", "") or "")


def _is_project(origin: str) -> bool:
    return bool(origin) and not os.path.abspath(origin).startswith(VENDOR_DIR)


def _collision_error(bad: list[tuple[str, str, str]]) -> RuntimeError:
    _desc = "；".join(f"`{n}` {st}项目包（{o}）" for n, o, st in bad)
    return RuntimeError(
        f"[AUTH-073] API 备用路径在本进程不可用：{_desc}。上游 vendor 的顶层绝对"
        "导入会命中项目实现（`dy_apis.login_api.DYLoginApi` 无 bootstrap_auth/"
        "get_qrcode/check_qrcode）。同进程无法隔离（vendor 与 backend 有 5 个"
        f"同名顶层包：{'/'.join(_SHARED_TOP_PKGS)}）。请改用 RPA 路径"
        "（auto_dm/login_remote.py），或在**干净子进程**中调用本适配层。")


def _import_upstream():
    """惰性导入上游 DYLoginApi。缺依赖时给出**可操作**的报错。

    ⚠️ **命名空间碰撞前置检查（2026-09-26 H-22 审计 idx3）**
    上游用**顶层绝对导入**（`from dy_apis.login_api import DYLoginApi`、
    `from builder.auth import ...`、`from utils.http_client import ...`），
    而 vendor 与 backend 有 **5 个同名顶层包**（`dy_apis` / `builder` / `utils` /
    `dy_live` / `static`）。只要其中任一来自**项目**包（应用进程必然如此，
    全仓大量模块级导入这些包），上游导入就会命中**项目实现** ——
    而项目 `dy_apis.login_api.DYLoginApi` 没有 `bootstrap_auth` / `get_qrcode` /
    `check_qrcode` ⇒ 调用即 `AttributeError`（**静默半坏**，极难排查）。

    同进程**无干净解**：清 `sys.modules` 会破坏项目自身（项目正在用这些包）；
    唯一正确形态是**子进程隔离**（独立解释器 + clean `sys.path`）。

    处置顺序（**关键：判定必须在 `_ensure_path` 之前**）：
      ① 若任一共享包**已加载**且来自项目 ⇒ 立刻报 AUTH-073（**不 mutate sys.path**）；
      ② 若任一共享包**在当前 `sys.path` 下会解析到项目** ⇒ 同样报 AUTH-073。
         **必须先判后插** —— 否则把 vendor 插到 `sys.path[0]` 会反过来**遮蔽项目包**、
         劫持宿主进程后续所有 `import builder/utils/...`（实测踩到：判定滞后时
         `__import__` 已把 vendor 包灌进 `sys.modules`）。
      ③ 只有确认「本进程不含项目同名包」后，才 `_ensure_path()` 并导入上游。
    """
    # ① 已加载的项目同名包 —— 立即失败，且**不触碰** sys.path
    _loaded_bad = [(n, _loaded_origin(n), "已加载")
                   for n in _SHARED_TOP_PKGS
                   if _is_project(_loaded_origin(n))]
    if _loaded_bad:
        raise _collision_error(_loaded_bad)

    # ② 当前 sys.path 下会解析到项目 ⇒ 也必须拒绝（**判定先于任何 mutate**）
    _resolved_bad = [(n, _resolvable_origin(n), "可解析到")
                     for n in _SHARED_TOP_PKGS
                     if _is_project(_resolvable_origin(n))]
    if _resolved_bad:
        raise _collision_error(_resolved_bad)

    # ③ 确认干净后，才允许改 sys.path 并导入
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

    ## ⚠️ 能力边界（2026-09-26 实测判定 —— 务必先读）
    | 能力 | 状态 | 说明 |
    |---|---|---|
    | `bootstrap()`    | ✅ 可用 | 2.0s，33 项 cookie，P-256 密钥自生成 |
    | `get_qrcode()`   | ✅ 可用 | 0.17s，`error_code=0` |
    | `check_qrcode()` | ✅ 可用 | 实测返回 `status=new`，`error_code=0` |
    | **短信登录**      | ❌ **不可用** | 见下 |

    **短信登录为何不可用**：上游 `send_sms_code()` / `phone_login()` 传
    `strict_dtrait=True`，硬性要求 `x-tt-session-dtrait` **恰好 820 字节**
    （`builder/auth.py:2441` 的长度断言）。该头的设备特征 blob 由混淆 SDK
    `@byted/uc-secure-dtrait-core` 采集，上游仓库**未提供 fixture**，
    须从真机浏览器捕获（`.env` 的 `DY_DTRAIT_BLOB` / `DY_SESSION_DTRAIT`）。

    **⇒ 短信登录请走 RPA 路径**（`auto_dm/login_remote.py`）：
    真浏览器天然生成全部指纹，**不需要 dtrait**。
    本项目既有的 `dy_apis.login_api.login_grab_ticket` 同理（零 dtrait 依赖，已核实）。

    用法::

        api = VendorLoginApi()
        auth = api.bootstrap()               # 准备匿名会话
        qr   = api.get_qrcode(auth)          # {token, url, png_b64, raw}
        # 用户手机扫码…
        st   = api.check_qrcode(auth, qr["token"])   # status: new/scanned/confirmed
    """

    # 短信路径的显式禁用（契约：不静默失败）
    SMS_UNSUPPORTED_REASON = (
        "[AUTH-072] API 路径的短信登录不可用：上游 strict_dtrait=True 要求 "
        "x-tt-session-dtrait 恰好 820 字节，需真机捕获的设备指纹素材（本项目暂无）。"
        "短信登录请改用 RPA 路径 auto_dm.login_remote（真浏览器无需 dtrait）。")

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

    # ── ④ 短信登录（**显式禁用** —— 见类文档「能力边界」）──────────
    def send_sms_code(self, auth, phone: str):
        """❌ 不可用（缺 820 字节 dtrait 素材）。短信登录请走 RPA 路径。"""
        raise NotImplementedError(self.SMS_UNSUPPORTED_REASON)

    def phone_login(self, auth, phone: str, code: str):
        """❌ 不可用（同上）。"""
        raise NotImplementedError(self.SMS_UNSUPPORTED_REASON)

    # ── ⑤ 落凭证（写 .env）────────────────────────────────────────
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
