"""直播间配置管理路由（按直播间号 room_id 存取配置）。

2026-09-06 新增。需求：按直播间号设定配置，配置项 = 现有直播任务全部配置
（live_url/发送上限/间隔/延迟抖动/词库/强制重扫/监听账号）+ 新增「自动申请连麦」。

存储：SQLite kv_store key="live_room_configs"
  { "<room_id>": {
      "room_id": "840377749201",
      "live_url": "...",           # 原始链接（可回填输入框）
      "name": "备注名",             # 用户起的备注（默认= room_id）
      "max_target": 100,           # 发送上限
      "interval": 60,              # 间隔（秒）
      "delay": "50,120",           # 延迟抖动
      "force_rescan": false,       # 强制重扫
      "dm_pool": [{"text": "...", "enabled": true}],  # 私信词库
      "acct": "尚进工伤小助理",     # 监听账号
      "auto_link_mic": false,      # ★ 自动申请连麦开关
      "link_mic_mode": "audio",    # 连麦方式: audio=语音 / video=视频
      "updated_at": 1690000000
  }, ... }

对外端点（挂 /api/live/room-configs）
------------------------------------
- GET    /api/live/room-configs            列出全部直播间配置
- GET    /api/live/room-configs/{room_id}  取单个
- POST   /api/live/room-configs            新建/更新（按 room_id upsert）
- DELETE /api/live/room-configs/{room_id}  删除
- POST   /api/live/room-configs/{room_id}/apply    应用到当前任务配置（写 kv "config"）
- POST   /api/live/room-configs/{room_id}/restart  保存 + 热更到**正在运行**的监听任务
                                                  （只改配置内容，不中断监听；见 §restart）
"""
import time

from fastapi import APIRouter, Request
from loguru import logger
from pydantic import BaseModel

from database import get_kv_json, set_kv_json

router = APIRouter()

_KV_KEY = "live_room_configs"

# 允许写入的字段白名单（越界丢弃）
_FIELDS = {
    "room_id", "live_url", "name", "max_target", "interval", "delay",
    "force_rescan", "dm_pool", "acct", "auto_link_mic", "link_mic_mode",
}


def _load_all() -> dict:
    data = get_kv_json(_KV_KEY, {}) or {}
    return data if isinstance(data, dict) else {}


def _save_all(data: dict) -> None:
    set_kv_json(_KV_KEY, data)


def _norm_room_id(raw: str) -> str:
    """从输入中提取纯数字 room_id（容忍粘贴整个 URL）。"""
    s = str(raw or "").strip()
    if not s:
        return ""
    # live.douyin.com/<rid>?... 或纯数字
    import re
    m = re.search(r"live\.douyin\.com/(\d+)", s)
    if m:
        return m.group(1)
    m = re.match(r"^(\d+)$", s)
    return m.group(1) if m else ""


class RoomConfigBody(BaseModel):
    room_id: str            # 直播间号（可接受 URL，后端自动提取）
    name: str = ""
    live_url: str = ""
    max_target: int | None = None
    interval: float | None = None
    delay: str | None = None
    force_rescan: bool | None = None
    dm_pool: list | None = None
    acct: str | None = None
    auto_link_mic: bool | None = None
    link_mic_mode: str | None = None   # "audio" | "video"


@router.get("")
async def list_room_configs() -> dict:
    data = _load_all()
    items = sorted(data.values(), key=lambda x: x.get("updated_at", 0), reverse=True)
    return {"ok": True, "items": items}


@router.get("/{room_id}")
async def get_room_config(room_id: str) -> dict:
    data = _load_all()
    cfg = data.get(str(room_id))
    if not cfg:
        return {"ok": False, "error": "未找到该直播间的配置"}
    return {"ok": True, "config": cfg}


@router.post("")
async def save_room_config(body: RoomConfigBody) -> dict:
    room_id = _norm_room_id(body.room_id)
    if not room_id:
        return {"ok": False, "error": "直播间号无效（需纯数字或直播间 URL）"}
    data = _load_all()
    old = data.get(room_id) or {}
    upd = {"room_id": room_id, "updated_at": int(time.time())}
    if body.name is not None and body.name != "":
        upd["name"] = body.name
    elif old.get("name"):
        upd["name"] = old["name"]
    else:
        upd["name"] = room_id
    if body.live_url:
        upd["live_url"] = body.live_url
    elif old.get("live_url"):
        upd["live_url"] = old["live_url"]
    else:
        upd["live_url"] = f"https://live.douyin.com/{room_id}"
    for k in ("max_target", "interval", "delay", "force_rescan",
              "dm_pool", "acct", "auto_link_mic", "link_mic_mode"):
        v = getattr(body, k)
        if v is not None:
            upd[k] = v
        elif k in old:
            upd[k] = old[k]
    data[room_id] = upd
    _save_all(data)
    logger.info(f"[room-config] 已保存直播间配置 {room_id} auto_link_mic={upd.get('auto_link_mic')}")
    return {"ok": True, "config": upd}


@router.delete("/{room_id}")
async def delete_room_config(room_id: str) -> dict:
    data = _load_all()
    rid = str(room_id)
    if rid not in data:
        return {"ok": False, "error": "未找到该直播间的配置"}
    del data[rid]
    _save_all(data)
    logger.info(f"[room-config] 已删除直播间配置 {rid}")
    return {"ok": True, "deleted": rid}


def _apply_to_task_kv(cfg: dict) -> dict:
    """把某直播间配置写进 kv \"config\"（供引擎下次启动读取）。返回写入后的 config。"""
    cur = get_kv_json("config", {}) or {}
    cur.update({
        "live_url": cfg.get("live_url") or f"https://live.douyin.com/{cfg['room_id']}",
        "live_id": cfg["room_id"],
        "max_target": cfg.get("max_target"),
        "interval": cfg.get("interval"),
        "delay": cfg.get("delay"),
        "force_rescan": bool(cfg.get("force_rescan")),
        "dm_pool": cfg.get("dm_pool") or [],
        "acct": cfg.get("acct"),
        "auto_link_mic": bool(cfg.get("auto_link_mic")),
        "link_mic_mode": cfg.get("link_mic_mode") or "audio",
    })
    set_kv_json("config", cur)
    return cur


class _RuntimeCfg:
    """热更用的最小配置载体。

    刻意不用 TaskConfig：那份模型会把词库 dict 压成文案字符串，丢掉 enabled；
    热更要保留「哪些文案已启用」。`AutoDM.apply_runtime_config` 只按属性名取值。
    """

    def __init__(self, cfg: dict) -> None:
        from models.task import _parse_delay

        self.live_url = cfg.get("live_url") or f"https://live.douyin.com/{cfg.get('room_id', '')}"
        self.max_target = int(cfg.get("max_target") or 3)
        self.interval = float(cfg.get("interval") or 60.0)
        self.delay_range = _parse_delay(str(cfg.get("delay") or ""))
        self.force_rescan = bool(cfg.get("force_rescan"))
        pool = cfg.get("dm_pool") or []
        self.dm_pool = [
            {"text": str(t.get("text", "")), "enabled": bool(t.get("enabled", True))}
            if isinstance(t, dict) else {"text": str(t), "enabled": True}
            for t in pool
        ]
        self.acct = cfg.get("acct") or None


@router.post("/{room_id}/restart")
async def restart_room_config(room_id: str, request: Request) -> dict:
    """保存该直播间配置，并**热更到正在运行的监听任务**（只改配置内容，不中断监听）。

    设计契约（用户 2026-09-15 定调）：
      任务进行中改配置 → 到「直播间配置管理」改 → 点「重启」→
      只把变更的配置内容补进正在运行的监听任务，**不重建 WS、不重扫凭证**。

    与 `/apply` 的差别：`/apply` 只写 kv（影响「下次启动」）；本端点**额外**把
    调度参数热更进当前运行实例，因此正在跑的任务立即按新参数发送。

    三种结果都如实下发（禁止假成功）：
      - `restart.ok=True`  → 引擎运行中，字段已热更（`applied` 列出实际生效的）
      - `restart.ok=False` → 引擎未运行 → 仅保存，提示用户点「开始自动私信」后生效
      - `restart.not_applied` → 直播间链接 / 监听账号属「换任务」语义，热更不覆盖，
        必须显式告知（否则用户以为换了直播间却不生效）
    """
    data = _load_all()
    cfg = data.get(str(room_id))
    if not cfg:
        return {"ok": False, "error": "未找到该直播间的配置", "restart": {"ok": False}}

    cur = _apply_to_task_kv(cfg)
    logger.info(
        f"[room-config] 已应用配置 {room_id} 到当前任务 "
        f"auto_link_mic={cur.get('auto_link_mic')}"
    )

    adm = getattr(request.app.state, "adm", None)
    if adm is None:
        return {
            "ok": True, "config": cfg, "applied_fields": [],
            "restart": {"ok": False, "applied": [], "not_applied": [],
                        "reason": "引擎未初始化（后端刚启动？），配置已保存"},
        }

    try:
        shell = _RuntimeCfg(cfg)
        result = adm.apply_runtime_config(shell)
    except Exception as e:  # 热更异常必须显式失败，绝不静默
        logger.warning("LIVE-021", f"[room-config] 热更失败 {room_id}: {e}")
        result = {"ok": False, "applied": [], "not_applied": [],
                  "reason": f"热更异常: {e}", "engine_state": "unknown"}

    applied_fields = [
        {"max_target": "发送上限", "interval": "间隔", "delay_range": "延迟抖动",
         "dm_pool": "私信词库"}.get(f, f)
        for f in (result.get("applied") or [])
    ]
    not_applied = [
        {"live_url": "直播间链接", "acct": "监听账号",
         "force_rescan": "强制重扫"}.get(f, f)
        for f in (result.get("not_applied") or [])
    ]
    logger.info(
        f"[room-config] 重启标签 {room_id}：applied={applied_fields} "
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


@router.post("/{room_id}/apply")
async def apply_room_config(room_id: str) -> dict:
    """把某直播间的配置应用到当前任务配置（kv "config"），供引擎启动时读取。"""
    data = _load_all()
    cfg = data.get(str(room_id))
    if not cfg:
        return {"ok": False, "error": "未找到该直播间的配置"}
    cur = _apply_to_task_kv(cfg)
    logger.info(f"[room-config] 已应用配置 {room_id} 到当前任务 auto_link_mic={cur.get('auto_link_mic')}")
    return {"ok": True, "config": cur}
