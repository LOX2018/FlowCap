"""采集策略路由 —— kv ``crawl_policies``（ADR-018 F1-D3，2026-09-27 新建）。

## 为什么需要这一层（设计意图）

在本次改造前，采集（`api/crawl.py`）是**纯即时调用** —— 每次请求自带
全部参数（`num` / `sort_type` / `publish_time` / `search_range` …），
没有任何可复用的参数载体。后果：

1. **无法复用**：同一套「保守慢速采集」参数要在前端每次重新填；
2. **无法按账号隔离**：标签体系的板块维度（`send` / `live` / `capture`）
   在 `capture` 板块上**没有可绑定的实体** ⇒ `config_tag` 的 `capture`
   板块形同虚设（`scope_of(account, "capture")` 返回的标签无消费者）；
3. **定时任务中心（F4）无处取值**：F4 的「关键词定时处理」需要一个
   「采集参数集」作为任务载荷。

## 与既有两层的分工（**房间与策略分离**的同构复用）

| 层 | 直播域 | 采集域（本模块） |
|---|---|---|
| **策略**（怎么采） | `live_room_configs` | **`crawl_policies`（本模块）** |
| **实体**（采什么） | `live_rooms`（房间登记） | 采集任务 / 关键词（F4 承载） |

⇒ 本模块**零身份字段**（不存具体关键词、不存 aweme_id），与
`live_config.py` 的策略层**同一条设计约束**。身份由请求或 F4 任务给出。

## 策略字段（唯一真源）

``kind`` 采集类型 ``video|user|comment`` ·
``num`` 每次上限 · ``sort_type`` 排序 ``0综合/1最多点赞/2最新`` ·
``publish_time`` 发布时段 ``0/1/7/180`` ·
``filter_duration`` 时长过滤 · ``search_range`` 搜索范围 ·
``content_type`` 内容形式 ``0不计/1视频/2图文`` ·
``max_rounds`` 翻页轮数上限（防「平台恒返 has_more=1 且 data 为空」死循环）

## 存储：SQLite kv_store

key=``crawl_policies``::

  { "<policy_id>": {
      "id": "cp_1758...", "name": "保守-慢速",
      "kind": "video", "num": 20, "sort_type": "0", "publish_time": "0",
      "filter_duration": "", "search_range": "", "content_type": "",
      "max_rounds": 20,
      "updated_at": 1690000000
  }, ... }

策略 id（``_normalize_policy_key``）：显式 ``id`` 优先（≤40 字符）；
留空则由服务端生成 ``cp_<epoch_ms>``。

## 对外端点（挂 /api/crawl/policies）

- GET    ``""``                列出全部策略
- GET    ``/{pid}``            取单条
- POST   ``""``                新建/更新
- DELETE ``/{pid}``            删除
- POST   ``/{pid}/resolve``    按策略产出「可直接喂给 crawl 端点」的参数包
                               （含标签板块 ``capture`` 的 scope 解析）

## 风控约束（用户已定红线，勿放宽）

本模块**只存参数、不发请求** —— 采集动作仍由用户显式触发（或 F4 定时任务，
其默认态见 ADR-018 D1「自动外发默认休眠」）。本模块**绝不**自动轮询或
后台批量请求。
"""
import re
import threading
import time
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from database import get_kv_json, set_kv_json

router = APIRouter()

_KV_KEY = "crawl_policies"

# 允许写入的字段白名单。**与 live_rooms 同一条设计约束**：本集合是写入侧真源，
# `save_policy` 落库前按其过滤 ⇒ 从本集合删字段 = 该字段不再可写。
_FIELDS = {
    "id", "name", "kind", "num", "comment_limit", "sort_type", "publish_time",
    "filter_duration", "search_range", "content_type", "max_rounds",
    "is_default",
}

# 采集类型白名单（与 api/crawl.py 的端点一一对应）。
_KINDS = ("video", "user", "comment")

# 字段取值白名单 —— 非法值一律**拒绝写入**而非静默改写：
# 静默改写会让用户以为设了 A、实际跑的是 B（本项目定义为「假成功」家族）。
# 枚举字段的默认值（单源）。空串 = 「该字段可留空」⇒ 默认就是空串。
_ENUM_DEFAULTS = {
    "sort_type": "0",
    "publish_time": "0",
    "search_range": "",
    "content_type": "",
}

_ENUMS = {
    "sort_type": {"0", "1", "2"},
    "publish_time": {"0", "1", "7", "180"},
    "search_range": {"", "0", "1", "2", "3"},
    "content_type": {"", "0", "1", "2"},
}

_lock = threading.RLock()


class PolicyBody(BaseModel):
    """一条采集策略（**零身份字段** —— 采什么由任务/请求给出）。"""

    id: str = ""
    name: str = ""
    kind: str = "video"
    # ★ `num` 语义**冻结**为「一次搜索取回多少个作品」（勿与评论条数混用 ——
    #   `_page_count` 的 docstring 已明写「与 num 是两个量，不可混用」）。
    num: int = 20
    # ★ 2026-10-03 拆分新增：每作品的**评论采集上限**（原被 num 兼任，语义混淆）。
    #   0/缺省 = 不覆盖（用请求体的 limit）。
    comment_limit: int = 0
    sort_type: str = "0"
    publish_time: str = "0"
    filter_duration: str = ""
    search_range: str = ""
    content_type: str = ""
    max_rounds: int = 20
    # ★ 2026-09-30：标记「全局默认策略」—— 采集时若账号未绑定采集标签、
    #   也未显式指定 policy_id，则用它（见 api/crawl.py::_resolve_policy_params）。
    is_default: bool = False


def _load_all() -> dict:
    data = get_kv_json(_KV_KEY, {}) or {}
    return data if isinstance(data, dict) else {}


def _save_all(data: dict) -> None:
    set_kv_json(_KV_KEY, data)


def _normalize_policy_key(raw: str, data: dict) -> str:
    """策略键：显式 id 优先（≤40 字符，仅字母数字下划线连字符）；否则生成 cp_<epoch_ms>。"""
    k = str(raw or "").strip()
    if k and len(k) <= 40 and re.fullmatch(r"[A-Za-z0-9_\-]+", k):
        return k
    base = f"cp_{int(time.time() * 1000)}"
    key = base
    n = 1
    while key in data:
        n += 1
        key = f"{base}_{n}"
    return key


def _clamp_int(v, lo: int, hi: int, default: int) -> int:
    """整数收敛到 [lo, hi]；不可解析时取 default（不抛 —— 配置项容错）。"""
    try:
        n = int(v)
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


@router.get("")
async def list_policies() -> dict:
    """列出全部采集策略（按 updated_at 降序）。"""
    data = _load_all()
    items = [v for v in data.values() if isinstance(v, dict)]
    items.sort(key=lambda x: x.get("updated_at", 0), reverse=True)
    return {"ok": True, "items": items, "total": len(items)}


@router.get("/{pid}")
async def get_policy(pid: str) -> dict:
    """取单条策略。"""
    data = _load_all()
    item = data.get(str(pid or "").strip())
    if not isinstance(item, dict):
        raise HTTPException(404, f"采集策略 {pid} 不存在")
    return {"ok": True, "item": item}


@router.post("")
async def save_policy(body: PolicyBody) -> dict:
    """新建 / 更新一条采集策略（按 id upsert；id 为空则生成新 id）。

    ## 字段显式清空语义（**照抄 `live_rooms.save_room` 的已验证范式**）

    「显式传来的空串」必须与「未提交该字段」区分，否则清空永远失败
    （`live_rooms` 曾实测踩到：解绑策略后仍显示旧值）。判据 = pydantic
    `model_fields_set`：

      - **可清空**：`name` / `filter_duration` / `search_range` / `content_type`
        —— 带了这个字段就采信（含空串 = 显式清空）；没带才回落旧值。
      - **不可清空**：`kind`（采集类型的锚点）、`num` / `max_rounds`（数值型，
        由 `_clamp_int` 兜底）—— 空/非法仍回落旧值。

    ## 为什么不静默改写非法枚举

    `_ENUMS` 命中失败一律**返回错误**（不改成默认值）—— 静默改写会让用户
    以为设了 A、实际跑 B，属本项目定义的「假成功」。
    """
    key = str(body.id or "").strip()
    with _lock:
        data = _load_all()
        k = _normalize_policy_key(key, data)
        old = data.get(k) or {}
        sent = body.model_fields_set

        def pick(field: str, *, clearable: bool, default: str = "") -> str:
            """取字段值。

            clearable=True：带了这个字段就采信（含空串 = 显式清空）。
            clearable=False：空串视为「未提供」⇒ 回落 old；old 也空 ⇒ default。
              ★ default 参数是必须的 —— 否则**新建**一条策略时，未提交的
                枚举字段回落成空串，直接撞下面「非法枚举拒绝写入」而误拒。
                （本门禁 G7 实测抓到这个 bug：sort_type 未提交 → 撞校验。）
            """
            if field in sent:
                cur = str(getattr(body, field) or "").strip()
                if cur or clearable:
                    return cur
            prev = str(old.get(field) or "").strip()
            return prev if prev else default

        # ① 枚举校验（非法即拒，不静默改写）
        kind = pick("kind", clearable=False, default="video")
        if kind not in _KINDS:
            return {"ok": False, "error": f"采集类型 {kind!r} 不受支持（可选：{'/'.join(_KINDS)}）"}
        for f, allowed in _ENUMS.items():
            v = pick(f, clearable=True, default=_ENUM_DEFAULTS.get(f, ""))
            if v not in allowed:
                return {"ok": False,
                        "error": f"字段 {f} 取值 {v!r} 非法（可选：{sorted(x for x in allowed if x) or '空'}）"}

        # ② 数值收敛（不可解析回落旧值/默认，绝不抛）
        num = _clamp_int(
            getattr(body, "num", None) if "num" in sent else old.get("num"),
            1, 50, 20)
        max_rounds = _clamp_int(
            getattr(body, "max_rounds", None) if "max_rounds" in sent else old.get("max_rounds"),
            1, 100, 20)
        # ★ 2026-10-03 拆分：`comment_limit`（每作品评论上限）。
        #   范围 0~300，**0 = 不覆盖**（回落请求体的 limit）——
        #   与 `num`（1~50，搜索条数）语义分离，两者不可混用。
        comment_limit = _clamp_int(
            getattr(body, "comment_limit", None) if "comment_limit" in sent
            else old.get("comment_limit"),
            0, 300, 0)

        upd = {
            "id": k,
            "name": pick("name", clearable=True),
            "kind": kind,
            "num": num,
            "comment_limit": comment_limit,
            "sort_type": pick("sort_type", clearable=False, default="0"),
            "publish_time": pick("publish_time", clearable=False, default="0"),
            "filter_duration": pick("filter_duration", clearable=True),
            "search_range": pick("search_range", clearable=True),
            "content_type": pick("content_type", clearable=True),
            "max_rounds": max_rounds,
            # ★ is_default：显式提交才改（未提交则沿用旧值），保持「清空语义」一致
            "is_default": bool(body.is_default) if "is_default" in sent
            else bool(old.get("is_default")),
            "updated_at": time.time(),
        }
        # 「全局默认」必须唯一 —— 置真时把其它策略的标记清掉，
        # 否则 _resolve_policy_params 会按 dict 顺序取到不确定的一条。
        if upd["is_default"]:
            for other_k, other_v in list(data.items()):
                if other_k != k and isinstance(other_v, dict) and other_v.get("is_default"):
                    other_v["is_default"] = False
        data[k] = {f: upd[f] for f in _FIELDS if f in upd}
        # 白名单过滤后 updated_at 不在 _FIELDS 内，需显式补回（它是审计字段，非策略字段）
        data[k]["updated_at"] = upd["updated_at"]
        _save_all(data)
    return {"ok": True, "id": k, "item": data[k]}


@router.delete("/{pid}")
async def delete_policy(pid: str) -> dict:
    """删除策略（幂等：不存在也回 ok）。"""
    key = str(pid or "").strip()
    with _lock:
        data = _load_all()
        existed = key in data
        data.pop(key, None)
        _save_all(data)
    return {"ok": True, "deleted": existed}


@router.post("/{pid}/resolve")
async def resolve_policy(pid: str, account: str = "") -> dict:
    """把策略解析成「可直接喂给 `api/crawl.py` 端点」的参数包（**只读，不发请求**）。

    ## 为什么要 `account` 参数

    F1 的标签体系有 `capture` 板块。解析时按
    `config_tag.scope_of(account, "capture")` 取该账号在采集板块应使用的标签
    scope，并把结果一并返回（`tag_scope`），供调用方在真正采集时带上
    —— **本端点自己不读标签参数、也不发任何采集请求**。

    这样分工的理由：本模块属 api 层，`config_tag` 属 services 层；
    在此处只做**一次 scope 解析**，实际取参数仍由 `dm_dispatch.get` 那样的
    消费方完成（保持单一取参出口）。
    """
    data = _load_all()
    item = data.get(str(pid or "").strip())
    if not isinstance(item, dict):
        raise HTTPException(404, f"采集策略 {pid} 不存在")

    tag_scope: Optional[str] = None
    if str(account or "").strip():
        try:
            from services import config_tag
            tag_scope = config_tag.scope_of(str(account).strip(), "capture")
        except Exception:  # noqa: BLE001 —— 标签解析失败不阻断参数下发
            tag_scope = None

    return {
        "ok": True,
        "params": {
            "kind": item.get("kind") or "video",
            "num": item.get("num", 20),
            "sort_type": item.get("sort_type", "0"),
            "publish_time": item.get("publish_time", "0"),
            "filter_duration": item.get("filter_duration", ""),
            "search_range": item.get("search_range", ""),
            "content_type": item.get("content_type", ""),
            "max_rounds": item.get("max_rounds", 20),
        },
        "tag_scope": tag_scope,
    }
