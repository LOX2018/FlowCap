"""通知配置与指令路由。

- /api/notify/status    查看各渠道就绪状态
- /api/notify/config    保存/读取配置
- /api/notify/test      测试推送（真发一条）
- /api/notify/command   IM 指令入口（解析意图 → 执行/待确认）
"""

from __future__ import annotations

import json
import os
from typing import Any

from fastapi import APIRouter
from loguru import logger
from pydantic import BaseModel

from notify import notifier
from notify.cmd_parser import parse_command

router = APIRouter()

# 引擎实例引用（由 main.py 启动时 bind_adm 注入，避免运行时 import 反查）
_BOUND_ADM: Any = None

# 配置文件落盘位置（与账号 .env 同级 data 目录，便于打包后持久化）
def _cfg_path() -> str:
    root = os.environ.get("DY_APP_ROOT") or os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    d = os.path.join(root, "data")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "notify_config.json")


def load_config() -> dict[str, Any]:
    p = _cfg_path()
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:  # noqa: BLE001
            logger.warning("NTY-001", f"[notify] 配置读取失败: {e}")
    return {"enabled": False, "channels": []}


class NotifyConfig(BaseModel):
    enabled: bool = False
    channels: list[dict[str, Any]] = []
    llm: dict[str, Any] = {}


class TestPush(BaseModel):
    channel_id: str = ""
    target: str = ""
    text: str = "DYAutoDM 通知链路测试 ✅"


class CommandIn(BaseModel):
    text: str
    confirmed: bool = False


@router.get("/status")
async def status() -> dict:
    st = notifier.status()
    return {"ok": True, **st}


@router.get("/config")
async def get_config() -> dict:
    cfg = load_config()
    # 敏感字段脱敏后再返回前端
    safe = json.loads(json.dumps(cfg))
    for ch in safe.get("channels", []):
        for k in ("corpsecret", "client_secret", "secret", "app_secret", "token"):
            if k in ch and ch[k]:
                ch[k] = "•" * 8
    return {"ok": True, "config": safe}


@router.post("/config")
async def save_config(body: NotifyConfig) -> dict:
    cfg = body.model_dump()
    # 脱敏值不覆盖：前端回传 •••••••• 时保留原值
    old = load_config()
    old_map = {str(c.get("id") or c.get("kind")): c for c in old.get("channels", [])}
    for ch in cfg.get("channels", []):
        cid = str(ch.get("id") or ch.get("kind"))
        prev = old_map.get(cid, {})
        for k in ("corpsecret", "client_secret", "secret", "app_secret", "token"):
            if ch.get(k) and set(str(ch[k])) == {"•"}:
                ch[k] = prev.get(k, "")
    try:
        with open(_cfg_path(), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}
    notifier.configure(cfg)
    notifier.start_worker()
    return {"ok": True, "status": notifier.status()}


@router.post("/test")
async def test_push(body: TestPush) -> dict:
    """真实推送一条测试消息（用于配置页验证）。"""
    cfg = load_config()
    if not cfg.get("enabled"):
        return {"ok": False, "error": "通知未启用"}
    notifier.configure(cfg)
    targets = {body.channel_id: body.target} if body.channel_id and body.target else {}
    if not targets:
        # 用各渠道 default_target
        for ch in cfg.get("channels", []):
            cid = str(ch.get("id") or ch.get("kind"))
            t = str(ch.get("default_target", ""))
            if t:
                targets[cid] = t
    if not targets:
        return {"ok": False, "error": "无可用目标（请先填 default_target 或指定 target）"}
    results = {}
    for cid, target in targets.items():
        ch = notifier.channels.get(cid)
        if not ch:
            results[cid] = {"ok": False, "error": "渠道未加载"}
            continue
        r = await ch.send(target, body.text)
        results[cid] = {"ok": r.ok, "error": r.error}
    return {"ok": any(v.get("ok") for v in results.values()), "results": results}


@router.post("/command")
async def command(body: CommandIn) -> dict:
    """IM 指令入口：解析 → (需确认则回确认语) / 执行。

    执行动作全部走 DYAutoDM 已有内部函数，不新增抖音请求。
    """
    cfg = load_config()
    parsed = await parse_command(body.text, cfg.get("llm") or {})
    intent = parsed["intent"]

    if parsed["need_confirm"] and not body.confirmed:
        return {"ok": True, "pending": True, **parsed}

    # ---- 执行 ----
    try:
        result = await _execute(intent, parsed["params"])
    except Exception as e:  # noqa: BLE001
        logger.exception("NTY-002", f"[notify] 指令执行失败: {e}")
        return {"ok": False, "intent": intent, "error": str(e)}
    return {"ok": True, "pending": False, "intent": intent, "result": result}


async def _execute(intent: str, params: dict[str, Any]) -> Any:
    """按实测签名调用现有接口（start_engine(request, TaskConfig) 等）。

    所有动作都走 DYAutoDM 已有内部接口，不新增任何抖音请求 —— 遵守
    「昵称唯一来源 = BCC 被动 hook」的风控红线。
    """
    from fastapi import Request

    from core.auto_dm import AutoDM

    # 取引擎实例（main.py: app.state.adm = AutoDM()）
    adm: AutoDM | None = _get_adm()
    if adm is None and intent in ("query_status", "start_task", "stop_task"):
        return {"reply": "引擎实例不可用"}

    if intent == "help":
        return {"reply": "见 confirm_text"}

    if intent == "query_status":
        return {
            "state": str(getattr(adm, "state", "") or ""),
            "status_msg": str(getattr(adm, "status_msg", "") or ""),
            "sent": int(getattr(adm, "sent_count", 0) or 0),
            "failed": int(getattr(adm, "failed_count", 0) or 0),
        }

    if intent == "start_task":
        from api.engine import start_engine
        from models.task import TaskConfig

        cfg = _build_task_config(params, adm)
        return await start_engine(_fake_request(adm), cfg)

    if intent == "stop_task":
        from api.engine import stop_engine

        return await stop_engine(_fake_request(adm))

    if intent == "create_task":
        from api.tasks import save_config as save_task_config
        from models.task import TaskConfig

        cfg = _build_task_config(params, adm)
        if not params:
            return {"reply": "参数不足，未创建任务（至少给直播链接或文案）"}
        return await save_task_config(cfg, _fake_request(adm))

    if intent == "recapture":
        from api.accounts import auto_recapture

        acct = params.get("account") or ""
        if not acct:
            return {"reply": "缺少账号名（例：重新捕获凭证 账号:zhangsan）"}
        return await auto_recapture(acct)

    return {"reply": "未识别指令"}


def _get_adm():
    """取引擎实例。

    优先用 main.py 启动时显式注入的引用（见 notify_api.bind_adm）；
    拿不到才回退 `from main import app` 反查。
    【为什么需要注入】PyInstaller onefile 下模块身份/导入顺序不可靠，
    运行时反查易拿到另一个模块实例（实测真机返回「引擎实例不可用」）。
    """
    if _BOUND_ADM is not None:
        return _BOUND_ADM
    try:
        from main import app

        return getattr(app.state, "adm", None)
    except Exception:  # noqa: BLE001
        return None


def bind_adm(adm) -> None:
    """main.py 启动时调用，把引擎实例显式注入（避免运行时反查）。"""
    global _BOUND_ADM
    _BOUND_ADM = adm


def _fake_request(adm) -> Request:
    """构造最小 Request 供现有路由函数使用（它们签名依赖 request.app.state.adm）。"""
    from fastapi import Request as _Req

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/internal",
        "headers": [],
        "query_string": b"",
        "app": _get_app(),
    }
    req = _Req(scope)
    try:
        req.app.state.adm = adm
    except Exception:  # noqa: BLE001
        pass
    return req


def _get_app():
    try:
        from main import app

        return app
    except Exception:  # noqa: BLE001
        from fastapi import FastAPI

        return FastAPI()


def _build_task_config(params: dict[str, Any], adm) -> Any:
    """把 LLM/规则解析出的 params 映射成 TaskConfig（按实测字段名）。"""
    from models.task import TaskConfig

    data: dict[str, Any] = {}
    if params.get("live_url"):
        data["live_url"] = params["live_url"]
    if params.get("max_target"):
        data["max_target"] = int(params["max_target"])
    if params.get("interval"):
        data["interval"] = float(params["interval"])
    if params.get("dm_text"):
        # TaskConfig 的词库字段是 dm_pool(list[str])
        data["dm_pool"] = [str(params["dm_text"])]
    if params.get("account"):
        data["acct"] = str(params["account"])
    # 若引擎已有配置，未提供的字段沿用现有值，避免被空值清空
    cur = getattr(adm, "config", None) or getattr(adm, "task_config", None)
    if cur is not None:
        for k in ("live_url", "max_target", "interval", "dm_pool", "acct"):
            if k not in data:
                v = getattr(cur, k, None)
                if v is not None:
                    data[k] = v
    return TaskConfig(**data)


def init_notifier() -> None:
    """后端启动时调用：加载配置 + 启动派发 worker。"""
    try:
        cfg = load_config()
        notifier.configure(cfg)
        if cfg.get("enabled"):
            notifier.start_worker()
            logger.info("[notify] 通知模块已启动")
    except Exception as e:  # noqa: BLE001
        logger.warning("NTY-003", f"[notify] 启动失败（不影响主流程）: {e}")
