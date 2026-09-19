"""直播策略（发送策略）路由 —— kv ``live_room_configs``。

## 沿革（2026-09-19 用户定调，勿回退）

用户原话：
  「直播策略放到直播监听页面中『直播间』板块的配置标签中，该编辑页面中**只保留
   直播策略**，策略以外的全部删除。不要『监听 / 目标直播间 / 配置标签』tab 切换栏，
   也不需要子 tab 页面。『强制重扫』策略早就废弃了，彻底移除。」

因此本模块的语义 = **一套发送策略**（怎么发），不含任何身份字段：

- **一条策略 = 一套发送参数**，可并存多条、各有名字；直播页在「直播间」板块里选。
- **身份字段全部移出**：直播间号由「直播间」板块输入框直接填；备注名 / 直播链接 /
  强制重扫 **均已删除**（强制重扫属已废弃策略，全仓移除）。
- **监听账号是策略属性**（对陌生目标的发送风控/频次由账号策略决定），保留为可选项；
  留空 = 用页面当前选择的账号。
- **身份**（监听哪个房间）由「直播间」板块的输入框给出，**不进策略**；
  本模块只负责策略本体（**零身份字段**）。

## 策略字段（唯一真源）

``max_target`` 每场私信上限 / ``interval`` 私信间隔(秒) / ``delay`` 延迟抖动 ``"40,80"`` /
``dm_pool`` 私信词库 ``[{text,enabled}]`` / ``acct`` 监听账号(可空) /
``auto_link_mic`` 自动申请连麦 / ``link_mic_mode`` 连麦方式 ``audio|video``

## 存储：SQLite kv_store

key=``live_room_configs``（沿用旧 key，**老数据零迁移**，多余字段读取时忽略）::

  { "<strategy_id>": {
      "id": "lc_1758...", "name": "保守-慢速",
      "max_target": 8, "interval": 300, "delay": "300,600",
      "dm_pool": [{"text": "...", "enabled": true}],
      "acct": "账号名", "auto_link_mic": false, "link_mic_mode": "audio",
      "updated_at": 1690000000
  }, ... }

策略 id（``normalize_strategy_key``）：显式 ``id`` 优先（≤40 字符）；
留空则由服务端生成 ``lc_<epoch_ms>``。

## 对外端点（挂 /api/live/config-tags，兼容别名 /api/live/room-configs）

- GET    ``""``                列出全部策略
- GET    ``/{sid}``            取单条
- POST   ``""``                新建/更新
- DELETE ``/{sid}``            删除
- POST   ``/{sid}/restart``    保存 + 热更到**正在运行**的监听任务（不中断监听）
- POST   ``/{sid}/apply``      写进 kv ``config``（供引擎下次启动读取）
"""
import re
import time

from fastapi import APIRouter, Request
from loguru import logger
from pydantic import BaseModel

from database import get_kv_json, set_kv_json

router = APIRouter()

_KV_KEY = "live_room_configs"

# 允许写入的策略字段白名单（越界丢弃；身份字段已全部移除）
_FIELDS = {
    "id", "name", "max_target", "interval", "delay",
    "dm_pool", "acct", "auto_link_mic", "link_mic_mode",
}

# 策略 id 允许的字符（中文 / 字母 / 数字 / 下划线 / 连字符），上限 40 字符
_KEY_RE = re.compile(r"^[\w\u4e00-\u9fff\-]{1,40}$")


def _load_all() -> dict:
    data = get_kv_json(_KV_KEY, {}) or {}
    return data if isinstance(data, dict) else {}


def _save_all(data: dict) -> None:
    set_kv_json(_KV_KEY, data)


def _normalize_stored(cfg: dict, key: str) -> dict:
    """把**存量旧记录**归一化到当前契约（读取路径统一出口）。

    为什么必须做：v0.43.92 及更早的旧记录是「一房一配置」形态 —— 键是直播间号，
    体内还带 ``room_id`` / ``live_url`` / ``force_rescan`` 等身份与废弃字段。
    若读取时原样返回，前端/调用方会再次看到「策略里带直播间号」，等于把用户
    刚要求删掉的东西又露出来（且 ``room_id`` 键名歧义）。

    归一化只**在读时收敛**，不写回：id 取键名（键才是真源），并补齐缺失的策略键。
    """
    c = dict(cfg or {})
    for junk in ("room_id", "live_url", "force_rescan", "forceRescan", "tag_id"):
        c.pop(junk, None)
    c["id"] = str(key)
    c.setdefault("name", "")
    c.setdefault("max_target", None)
    c.setdefault("interval", None)
    c.setdefault("delay", None)
    c.setdefault("dm_pool", [])
    c.setdefault("acct", None)
    c.setdefault("auto_link_mic", None)
    c.setdefault("link_mic_mode", None)
    return c


def normalize_strategy_key(raw: str) -> str:
    """校验显式策略 id（空串表示「新建，由服务端生成 id」）。"""
    s = str(raw or "").strip()
    if not s:
        return ""
    return s if _KEY_RE.match(s) else ""


def new_strategy_id() -> str:
    return f"lc_{int(time.time() * 1000)}"


class StrategyBody(BaseModel):
    """一条发送策略。**不含任何身份字段**（直播间号/备注名/直播链接/强制重扫）。"""

    id: str = ""
    name: str = ""
    max_target: int | None = None
    interval: float | None = None
    delay: str | None = None
    dm_pool: list | None = None
    acct: str | None = None
    auto_link_mic: bool | None = None
    link_mic_mode: str | None = None   # "audio" | "video"


class StrategyApplyBody(BaseModel):
    """把策略应用到任务时的**上下文**（由调用方给出，不属于策略本身）。"""

    room_id: str = ""      # 直播间号（用于推导 live_url / live_id）
    account: str = ""      # 页面当前选择的监听账号（覆盖策略里的 acct，可空）


@router.get("")
async def list_strategies() -> dict:
    data = _load_all()
    items = [_normalize_stored(v, k) for k, v in data.items()]
    items.sort(key=lambda x: x.get("updated_at", 0), reverse=True)
    return {"ok": True, "items": items}


@router.get("/{sid}")
async def get_strategy(sid: str) -> dict:
    key = str(sid)
    cfg = _load_all().get(key)
    if not cfg:
        return {"ok": False, "error": "未找到该直播策略"}
    return {"ok": True, "config": _normalize_stored(cfg, key)}


@router.post("")
async def save_strategy(body: StrategyBody) -> dict:
    """新建 / 更新一条策略（按 id upsert；id 为空则生成新 id）。"""
    if body.id and not normalize_strategy_key(body.id):
        return {"ok": False, "error": "策略 id 无效（≤40 字，可用中英文/数字/下划线/连字符）"}
    sid = normalize_strategy_key(body.id) or new_strategy_id()
    data = _load_all()
    old = data.get(sid) or {}
    upd = {"id": sid, "updated_at": int(time.time())}
    if body.name and body.name.strip():
        upd["name"] = body.name.strip()
    elif old.get("name"):
        upd["name"] = old["name"]
    else:
        upd["name"] = sid
    for k in ("max_target", "interval", "delay",
              "dm_pool", "acct", "auto_link_mic", "link_mic_mode"):
        v = getattr(body, k)
        if v is not None:
            upd[k] = v
        elif k in old:
            upd[k] = old[k]
    data[sid] = upd
    _save_all(data)
    logger.info(f"[live-strategy] 已保存策略 {sid} name={upd.get('name')}")
    return {"ok": True, "config": upd}


@router.delete("/{sid}")
async def delete_strategy(sid: str) -> dict:
    """删除策略。

    2026-09-19 用户定调：**没有「目标直播间」体系**（不要子 tab / 不要该页），
    策略是自足的（只含发送参数，不含身份）。故删除无任何跨模块解绑副作用。
    """
    data = _load_all()
    key = str(sid)
    if key not in data:
        return {"ok": False, "error": "未找到该直播策略"}
    del data[key]
    _save_all(data)
    logger.info(f"[live-strategy] 已删除策略 {key}")
    return {"ok": True, "deleted": key}


def resolve_live_url(room_id: str | None) -> str:
    """直播间链接只由**直播间号**推导（策略不再自带 live_url）。"""
    rid = str(room_id or "").strip()
    if not rid:
        return ""
    m = re.search(r"live\.douyin\.com/(\d+)", rid)
    if m:
        rid = m.group(1)
    return f"https://live.douyin.com/{rid}" if re.fullmatch(r"\d+", rid) else ""


def resolve_effective_account(cfg: dict, account: str | None) -> str:
    """生效的监听账号：页面当前选择优先，其次策略自带 acct（可空）。"""
    return str(account or "").strip() or str(cfg.get("acct") or "").strip()


def _apply_to_task_kv(cfg: dict, room_id: str | None = None,
                      account: str | None = None) -> dict:
    """把策略写进 kv ``config``（供引擎下次启动读取）。返回写入后的 config。

    ``room_id`` / ``account`` 由调用方（直播间板块 / 目标直播间）给出：
    - 有 room_id 才覆盖 live_url/live_id（否则保持任务里原有的直播间不变）；
    - account 显式给出时覆盖策略的 acct（页面当前选择优先于策略默认）。
    """
    cur = get_kv_json("config", {}) or {}
    cur.update({
        "max_target": cfg.get("max_target"),
        "interval": cfg.get("interval"),
        "delay": cfg.get("delay"),
        "dm_pool": cfg.get("dm_pool") or [],
        "acct": resolve_effective_account(cfg, account) or None,
        "auto_link_mic": bool(cfg.get("auto_link_mic")),
        "link_mic_mode": cfg.get("link_mic_mode") or "audio",
    })
    url = resolve_live_url(room_id)
    if url:
        cur["live_url"] = url
        cur["live_id"] = url.rsplit("/", 1)[-1]
    set_kv_json("config", cur)
    return cur


class _RuntimeCfg:
    """热更用的最小配置载体（只按属性名取值，不含身份字段）。"""

    def __init__(self, cfg: dict, room_id: str | None = None,
                 account: str | None = None) -> None:
        from models.task import _parse_delay

        self.live_url = resolve_live_url(room_id)
        self.max_target = int(cfg.get("max_target") or 3)
        self.interval = float(cfg.get("interval") or 60.0)
        self.delay_range = _parse_delay(str(cfg.get("delay") or ""))
        pool = cfg.get("dm_pool") or []
        self.dm_pool = [
            {"text": str(t.get("text", "")), "enabled": bool(t.get("enabled", True))}
            if isinstance(t, dict) else {"text": str(t), "enabled": True}
            for t in pool
        ]
        self.acct = resolve_effective_account(cfg, account) or None


def restart_core(request: Request, sid: str, room_id: str | None = None,
                 account: str | None = None) -> dict:
    """保存该策略并**热更到正在运行的监听任务**（只改配置内容，不中断监听）。

    设计契约（用户 2026-09-15 定调，2026-09-19 收敛为「纯策略」）：
      任务进行中改策略 → 到策略编辑页改 → 点「重启」→
      只把变更的**发送参数**补进正在运行的监听任务，**不重建 WS、不重扫凭证**。

    三种结果都如实下发（禁止假成功）：
      - ``restart.ok=True``  → 引擎运行中，字段已热更（``applied`` 列出实际生效的）
      - ``restart.ok=False`` → 引擎未运行 → 仅保存，提示点「开始自动私信」后生效
      - ``restart.not_applied`` → 换直播间 / 换监听账号属「换任务」语义，热更不覆盖
    """
    data = _load_all()
    cfg = data.get(str(sid))
    if not cfg:
        return {"ok": False, "error": "未找到该直播策略", "restart": {"ok": False}}

    _apply_to_task_kv(cfg, room_id=room_id, account=account)
    logger.info(f"[live-strategy] 已应用策略 {sid} 到当前任务")

    adm = getattr(request.app.state, "adm", None)
    if adm is None:
        return {
            "ok": True, "config": cfg, "applied_fields": [],
            "restart": {"ok": False, "applied": [], "not_applied": [],
                        "reason": "引擎未初始化（后端刚启动？），策略已保存"},
        }

    try:
        shell = _RuntimeCfg(cfg, room_id=room_id, account=account)
        result = adm.apply_runtime_config(shell)
    except Exception as e:  # 热更异常必须显式失败，绝不静默
        logger.warning(f"[LIVE-021] " + f"[live-strategy] 热更失败 {sid}: {e}")
        result = {"ok": False, "applied": [], "not_applied": [],
                  "reason": f"热更异常: {e}", "engine_state": "unknown"}

    applied_fields = [
        {"max_target": "发送上限", "interval": "间隔", "delay_range": "延迟抖动",
         "dm_pool": "私信词库"}.get(f, f)
        for f in (result.get("applied") or [])
    ]
    not_applied = [
        {"live_url": "直播间链接", "acct": "监听账号"}.get(f, f)
        for f in (result.get("not_applied") or [])
    ]
    logger.info(
        f"[live-strategy] 重启策略 {sid}：applied={applied_fields} "
        f"not_applied={not_applied} engine={result.get('engine_state')}"
    )
    return {
        "ok": True,
        "config": cfg,
        "applied_fields": applied_fields,
        "restart": {
            "ok": bool(result.get("ok")),
            "applied": result.get("applied") or [],
            "not_applied": result.get("not_applied") or [],
            "not_applied_fields": not_applied,
            "reason": result.get("reason") or "",
            "engine_state": result.get("engine_state"),
        },
    }


@router.post("/{sid}/restart")
async def restart_strategy(sid: str, request: Request,
                           body: StrategyApplyBody | None = None) -> dict:
    """保存该策略并热更到运行中的监听任务（`sid` 为策略 id）。"""
    b = body or StrategyApplyBody()
    return restart_core(request, sid, room_id=b.room_id, account=b.account)


@router.post("/{sid}/apply")
async def apply_strategy(sid: str, body: StrategyApplyBody | None = None) -> dict:
    """把某策略应用到当前任务配置（kv "config"），供引擎启动时读取。"""
    data = _load_all()
    cfg = data.get(str(sid))
    if not cfg:
        return {"ok": False, "error": "未找到该直播策略"}
    b = body or StrategyApplyBody()
    cur = _apply_to_task_kv(cfg, room_id=b.room_id, account=b.account)
    logger.info(f"[live-strategy] 已应用策略 {sid} 到当前任务")
    return {"ok": True, "config": cur}

