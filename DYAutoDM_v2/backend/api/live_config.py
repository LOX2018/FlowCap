"""直播配置标签路由（`live_config_tags` 语义，落在 kv ``live_room_configs``）。

## 沿革（2026-09-19 用户定调，勿回退）

用户原话：「直播监听的配置管理修改只能对现有的配置反复覆盖，没有实现多配置标签的
功能，目标直播间可以管理，但别放到配置管理中，单独加一个目标直播间管理，在该页面
中选择是否绑定配置。」

因此本模块的语义从「按直播间号存取配置」升级为 **配置标签**：
- **一个标签 = 一套监听参数**（发送上限 / 间隔 / 抖动 / 词库 / 强制重扫 / 连麦 /
  监听账号 / 可选直播间链接），标签有**自己的 id 与名字**，可并存多条（多配置标签）。
- **目标直播间**（监听哪个房间）在 `api/target_rooms.py` 维护，通过 `tag_id`
  **引用**某个标签 —— 只选引用，不复制参数（唯一可写入口仍是本模块）。
- 解绑只清目标直播间里的 `tag_id`，**不删除**标签本身；删标签由本模块负责。

## 存储：SQLite kv_store

key=``live_room_configs``（沿用旧 key，**老数据零迁移**）::

  { "<tag_id>": {
      "id": "316276709526638",        # 标签 id（旧数据 = 原 room_id，天然兼容）
      "room_id": "316276709526638",   # ⚠️ 兼容别名，等于 id（旧前端/脚本读它）
      "name": "保守账号-慢速",          # 标签名（默认 = id）
      "live_url": "",                  # 可选；留空则按「目标直播间」推导
      "max_target": 100, "interval": 60, "delay": "50,120",
      "force_rescan": false, "dm_pool": [{"text": "...", "enabled": true}],
      "acct": "账号名",
      "auto_link_mic": false, "link_mic_mode": "audio",
      "updated_at": 1690000000
  }, ... }

标签 id 规则（``normalize_tag_key``）：
  - 纯数字 → 直接用（兼容历史「按直播间号当标签键」的数据）
  - 直播间 URL → 提取其中的数字房间号
  - 其它字符串（如 ``保守-慢速``）→ 原样用作 id（**多配置标签**即靠它实现）
  - 留空 → 前端「新建标签」路径，由服务端生成 ``lc_<epoch>``

## 对外端点

规范路径（前端使用）：``/api/live/config-tags``
兼容别名（旧前端 / 既有验证脚本 / 任务中心复用）：``/api/live/room-configs``
两条前缀挂**同一个 router**，读写同一份 kv，不存在第二份存储。

- GET    ``""``                     列出全部标签
- GET    ``/{tag_id}``              取单个
- POST   ``""``                     新建/更新（按 id upsert；id 空则生成）
- DELETE ``/{tag_id}``              删除标签
- POST   ``/{tag_id}/apply``        写进 kv ``config``（供引擎下次启动读取）
- POST   ``/{tag_id}/restart``      保存 + 热更到**正在运行**的监听任务（不中断监听）
"""
import re
import time

from fastapi import APIRouter, Request
from loguru import logger
from pydantic import BaseModel

from database import get_kv_json, set_kv_json

router = APIRouter()

_KV_KEY = "live_room_configs"

# 允许写入的字段白名单（越界丢弃）
_FIELDS = {
    "id", "room_id", "live_url", "name", "max_target", "interval", "delay",
    "force_rescan", "dm_pool", "acct", "auto_link_mic", "link_mic_mode",
}

# 可热更的字段（与 `AutoDM.apply_runtime_config` 的 applied 名单一致）
_HOT_FIELDS = ("max_target", "interval", "delay_range", "dm_pool")

# 标签 id 允许的字符（中文 / 字母 / 数字 / 下划线 / 连字符），上限 40 字符
_KEY_RE = re.compile(r"^[\w\u4e00-\u9fff\-]{1,40}$")


def _load_all() -> dict:
    data = get_kv_json(_KV_KEY, {}) or {}
    return data if isinstance(data, dict) else {}


def _save_all(data: dict) -> None:
    set_kv_json(_KV_KEY, data)


def normalize_tag_key(raw: str) -> str:
    """把用户输入归一成标签 id（容忍粘贴整个直播间 URL）。

    与旧 ``_norm_room_id`` 的差异：**不再只接受纯数字** —— 任意合法短字符串都
    可以当标签键，这是「多配置标签」的前提（旧实现把非数字输入一律拒绝，
    导致只能对「该直播间的那一条」反复覆盖）。
    """
    s = str(raw or "").strip()
    if not s:
        return ""
    m = re.search(r"live\.douyin\.com/(\d+)", s)
    if m:
        return m.group(1)
    return s if _KEY_RE.match(s) else ""


def new_tag_id() -> str:
    """生成新标签 id（新建标签路径用；不用 room_id，避免与房间强绑定）。"""
    return f"lc_{int(time.time() * 1000)}"


class RoomConfigBody(BaseModel):
    """标签体。``room_id`` 保留为 ``id`` 的兼容别名（旧前端只传 room_id）。"""

    id: str = ""
    room_id: str = ""
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

    def tag_key(self) -> str:
        return str(self.id or self.room_id or "").strip()


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
        return {"ok": False, "error": "未找到该标签"}
    return {"ok": True, "config": cfg}


@router.post("")
async def save_room_config(body: RoomConfigBody) -> dict:
    """新建 / 更新一条配置标签（按 id upsert；id 留空则生成新 id）。"""
    raw_key = body.tag_key()
    tag_id = normalize_tag_key(raw_key) if raw_key else new_tag_id()
    if not tag_id:
        return {"ok": False, "error": "标签键无效（可用纯数字直播间号、直播间 URL，或 40 字以内的名称）"}
    data = _load_all()
    old = data.get(tag_id) or {}
    upd = {"id": tag_id, "room_id": tag_id, "updated_at": int(time.time())}
    if body.name is not None and body.name != "":
        upd["name"] = body.name
    elif old.get("name"):
        upd["name"] = old["name"]
    else:
        upd["name"] = tag_id
    # live_url 可选：显式传值优先，否则沿用旧值（留空表示「按目标直播间推导」）
    if body.live_url:
        upd["live_url"] = body.live_url
    elif old.get("live_url"):
        upd["live_url"] = old["live_url"]
    else:
        upd["live_url"] = ""
    for k in ("max_target", "interval", "delay", "force_rescan",
              "dm_pool", "acct", "auto_link_mic", "link_mic_mode"):
        v = getattr(body, k)
        if v is not None:
            upd[k] = v
        elif k in old:
            upd[k] = old[k]
    data[tag_id] = upd
    _save_all(data)
    logger.info(f"[room-config] 已保存配置标签 {tag_id} name={upd.get('name')} auto_link_mic={upd.get('auto_link_mic')}")
    return {"ok": True, "config": upd}


@router.delete("/{room_id}")
async def delete_room_config(room_id: str) -> dict:
    """删除标签。**引用它的目标直播间由 target_rooms 侧负责解绑**（返回解绑清单）。"""
    data = _load_all()
    rid = str(room_id)
    if rid not in data:
        return {"ok": False, "error": "未找到该标签"}
    del data[rid]
    _save_all(data)
    unbound = _unbind_from_targets(rid)
    logger.info(f"[room-config] 已删除配置标签 {rid}，解绑目标直播间 {len(unbound)} 个")
    return {"ok": True, "deleted": rid, "unbound_rooms": unbound}


def _unbind_from_targets(tag_id: str) -> list[str]:
    """把引用了该标签的目标直播间置为「不绑定」（只清引用，不删目标直播间）。"""
    try:
        from api import target_rooms as tr
    except Exception as e:  # 解绑失败不得吞掉删除结果，但必须留痕
        logger.warning(f"[LIVE-022] 解绑目标直播间失败（标签已删除）: {e}")
        return []
    return tr.unbind_tag(tag_id)


def resolve_live_url(cfg: dict, room_id: str | None = None) -> str:
    """解析该标签生效的直播间链接。

    顺序：标签自带 live_url → 传入的目标直播间号 → 数字型标签 id 推导 → 空
    （空表示「保持任务里原有的直播间不变」，对应「换直播间属换任务」语义）。
    """
    url = str(cfg.get("live_url") or "").strip()
    if url:
        return url
    if room_id:
        return f"https://live.douyin.com/{room_id}"
    tid = str(cfg.get("id") or cfg.get("room_id") or "")
    if re.fullmatch(r"\d+", tid):
        return f"https://live.douyin.com/{tid}"
    return ""


def _apply_to_task_kv(cfg: dict, room_id: str | None = None) -> dict:
    """把标签参数写进 kv ``config``（供引擎下次启动读取）。返回写入后的 config。"""
    cur = get_kv_json("config", {}) or {}
    tags = {
        "max_target": cfg.get("max_target"),
        "interval": cfg.get("interval"),
        "delay": cfg.get("delay"),
        "force_rescan": bool(cfg.get("force_rescan")),
        "dm_pool": cfg.get("dm_pool") or [],
        "acct": cfg.get("acct"),
        "auto_link_mic": bool(cfg.get("auto_link_mic")),
        "link_mic_mode": cfg.get("link_mic_mode") or "audio",
    }
    url = resolve_live_url(cfg, room_id)
    if url:
        rid = room_id or (re.search(r"live\.douyin\.com/(\d+)", url) or [None, ""])[1]
        tags["live_url"] = url
        if rid:
            tags["live_id"] = str(rid)
    cur.update(tags)
    set_kv_json("config", cur)
    return cur


class _RuntimeCfg:
    """热更用的最小配置载体。

    刻意不用 TaskConfig：那份模型会把词库 dict 压成文案字符串，丢掉 enabled；
    热更要保留「哪些文案已启用」。`AutoDM.apply_runtime_config` 只按属性名取值。
    """

    def __init__(self, cfg: dict, room_id: str | None = None) -> None:
        from models.task import _parse_delay

        self.live_url = resolve_live_url(cfg, room_id)
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


def restart_core(request: Request, tag_id: str, room_id: str | None = None) -> dict:
    """保存该标签配置并**热更到正在运行的监听任务**（只改配置内容，不中断监听）。

    设计契约（用户 2026-09-15 定调，2026-09-19 扩展到配置标签语义）：
      任务进行中改配置 → 到「直播配置标签」改 → 点「重启」→
      只把变更的配置内容补进正在运行的监听任务，**不重建 WS、不重扫凭证**。

    与 `/apply` 的差别：`/apply` 只写 kv（影响「下次启动」）；本函数**额外**把
    调度参数热更进当前运行实例，因此正在跑的任务立即按新参数发送。

    三种结果都如实下发（禁止假成功）：
      - `restart.ok=True`  → 引擎运行中，字段已热更（`applied` 列出实际生效的）
      - `restart.ok=False` → 引擎未运行 → 仅保存，提示用户点「开始自动私信」后生效
      - `restart.not_applied` → 直播间链接 / 监听账号属「换任务」语义，热更不覆盖，
        必须显式告知（否则用户以为换了直播间却不生效）

    `room_id`：调用方（目标直播间页）已知监听房间时传入，用于推导 live_url。
    """
    data = _load_all()
    cfg = data.get(str(tag_id))
    if not cfg:
        return {"ok": False, "error": "未找到该配置标签", "restart": {"ok": False}}

    cur = _apply_to_task_kv(cfg, room_id=room_id)
    logger.info(
        f"[room-config] 已应用配置标签 {tag_id} 到当前任务 "
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
        shell = _RuntimeCfg(cfg, room_id=room_id)
        result = adm.apply_runtime_config(shell)
    except Exception as e:  # 热更异常必须显式失败，绝不静默
        logger.warning(f"[LIVE-021] " + f"[room-config] 热更失败 {tag_id}: {e}")
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
        f"[room-config] 重启标签 {tag_id}：applied={applied_fields} "
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


@router.post("/{room_id}/restart")
async def restart_room_config(room_id: str, request: Request) -> dict:
    """保存该标签并热更到运行中的监听任务（`room_id` 实为标签 id，保留路径兼容）。"""
    return restart_core(request, room_id)


@router.post("/{room_id}/apply")
async def apply_room_config(room_id: str) -> dict:
    """把某标签的参数应用到当前任务配置（kv "config"），供引擎启动时读取。"""
    data = _load_all()
    cfg = data.get(str(room_id))
    if not cfg:
        return {"ok": False, "error": "未找到该配置标签"}
    cur = _apply_to_task_kv(cfg)
    logger.info(f"[room-config] 已应用标签 {room_id} 到当前任务 auto_link_mic={cur.get('auto_link_mic')}")
    return {"ok": True, "config": cur}


def get_tag(tag_id: str) -> dict | None:
    """跨模块读单个标签（target_rooms 侧校验绑定目标是否存在）。"""
    return _load_all().get(str(tag_id))
