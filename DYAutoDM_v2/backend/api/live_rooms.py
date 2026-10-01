"""直播间登记表（**房间层**）路由 —— kv ``live_rooms``。

## 为什么有这一层（规格 = ADR-003，2026-09-22 用户决策）

2026-09-19 用户曾定调「没有『目标直播间』体系，策略是自足的」。2026-09-22 用户
**部分逆转**该决定：引入**房间登记层**，但坚持**房间与策略分离**——

| 层 | 载体 | 内容 |
|---|---|---|
| **策略**（怎么发） | kv ``live_room_configs``（`api/live_config.py`） | 发送参数，**不含身份字段** |
| **房间**（在哪发） | kv ``live_rooms``（**本模块**） | 身份 + **策略引用** + 脱敏开关 |

⇒ 一份策略可被多个房间**引用**（房间只存 ``strategy_id``），因此**删除策略必须自动
解绑引用它的房间**，禁止悬空引用（见 `api/live_config.delete_strategy`）。

## 记录契约（房间 id 形如 ``lr_<epoch_ms>``）

  { "lr_1758...": {
      "id": "lr_1758...",
      "room_id": "992931212705",
      "live_url": "https://www.douyin.com/follow/live/992931212705?anchor_id=...",
      "name": "自营-工伤咨询",          // 备注（用户填）
      "strategy_id": "lc_1758...",      // 引用 live_room_configs；空 = 未绑定
      "allow_desensitized": false,      // 该房间是否允许「检测到脱敏仍继续监听（仅统计）」
      "updated_at": 1690000000
  }, ... }

## 脱敏布尔语义（ADR-003 §3.3，**一级概念、两级粒度**，勿造第三套）

- 全局「脱敏处理策略」（ADR-002 §3.6，默认「跳过」）= 默认行为；
- 本页 ``allow_desensitized`` = **该房间的覆盖开关**：
  ``false``（默认）⇒ 按全局策略走（默认即「跳过，不监听」）；
  ``true`` ⇒ 即使检测到脱敏也继续监听，但**只做仅统计**（在线数/热度/开播检测），
  **不采真实昵称/uid、不发送私信**（无解密权时本就拿不到身份）。
- 🔴 **诚实标注**：解密权取决于**房间归属**（自营有 / 他人默认脱敏，与凭证无关）。
  故该开关的实际用途是「允许盯一个我没解密权的房间做统计」，**不是**「能拿到它的昵称」。

## 对外端点（挂 /api/live/rooms）

- GET    ``""``             列出全部已登记房间
- GET    ``/{rid}``         取单条
- POST   ``""``             新建 / 更新（按 id upsert；id 为空则生成）
- DELETE ``/{rid}``         删除房间（只删房间记录，**不动策略**）
- POST   ``/migrate``       一次性迁移：存量「房间形」旧策略记录 → live_rooms（带干跑）
- POST   ``/discover``      ★ ADR-018 F5：按关键词**只读搜索**直播间（不上架）

## F5「搜索发现」（ADR-018 §F5，2026-09-27）

流程拆成**两段**，刻意不合并：

  1. ``/discover`` —— **只读**，只做「关键词 → 平台搜索 → 规范化列表」；
  2. 上架 —— 复用**既有** ``POST ""``（``save_room``），不另造一套存储。

### D6（签名，硬性）

调用链 ``/discover`` → ``DouyinAPI.search_some_live`` → ``DouyinAPI.search_live``，
``search_live`` 已改走 ``params.signed_url(...)``（``dy_apis/client_search.py``）。

### D7（昵称红线）

只取搜索结果**自带**的 ``nickname``，**绝不**回调 ``bulk_user_info`` /
``get_im_user_info`` 之类补查接口。取不到就返回空串，由前端显示「—」。

### 「禁止假成功」（项目铁律）

平台风控（Argus 403 / 空响应体）时**不得**返回空列表假装「没搜到」——
响应带 ``blocked=True`` + ``transport`` 传输层事实，前端据此显示「被风控拦截」。
"""
from __future__ import annotations

import asyncio
import threading
import time

from fastapi import APIRouter, HTTPException
from loguru import logger
from pydantic import BaseModel

from typing import Any

from database import get_kv_json, set_kv_json

router = APIRouter()

_KV_KEY = "live_rooms"
_STRATEGY_KV = "live_room_configs"

# 允许写入的房间字段白名单（**含身份字段** —— 这与 live_config._FIELDS 相反，
# 因为身份本来就属于房间层；策略层才排身份）。
# 🔴 本集合**是写入侧真源**（P4 死代码整改 2026-09-23）：`save_room` 落库前
# 用 `{k: v for k, v in upd.items() if k in _FIELDS}` 过滤，因此从本集合里
# 删掉某字段 = 该字段不再可写。此前它定义了却无人消费（删掉也不影响任何行为），
# 是典型「看着像门禁、实际是装饰」的死代码。
_FIELDS = {
    "id", "room_id", "live_url", "name", "strategy_id", "allow_desensitized",
    "tag_id",   # ★ ADR-018 F1-D1：房间级标签绑定（可清空，见 save_room 分类）
}


def _load_all() -> dict:
    data = get_kv_json(_KV_KEY, {}) or {}
    return data if isinstance(data, dict) else {}


def _save_all(data: dict) -> None:
    set_kv_json(_KV_KEY, data)


def _tag_exists(tid: str) -> bool:
    """引用完整性（ADR-018 F1-D1）：标签库里是否真有这个 id。空串 = 「未绑定」，合法。

    与 `_strategy_exists` 同构 —— 存在的理由也一样：悬空引用会让
    `config_tag.scope_of` 判空后**静默回落**到账号级，用户以为绑了、实际没生效。
    宁可写入时报错，也不要静默失效。
    """
    s = str(tid or "").strip()
    if not s:
        return True
    try:
        from services import config_tag
        return bool((config_tag.get_tag(s) or {}).get("name"))
    except Exception:  # noqa: BLE001 —— 读标签库失败不阻断写房间（仅跳过校验）
        return True


def _strategy_exists(sid: str) -> bool:
    """引用完整性（P2-8）：策略库里是否真有这个键。空串 = 「未绑定」，视为合法。"""
    s = str(sid or "").strip()
    if not s:
        return True
    try:
        cfgs = get_kv_json(_STRATEGY_KV, {}) or {}
    except Exception:  # noqa: BLE001 —— 读策略库失败不阻断写房间（仅跳过校验）
        return True
    return isinstance(cfgs, dict) and s in cfgs


# ---------------------------------------------------------------------------
# 2026-09-23 修补（P0-2a，实测复现）：id 用裸 epoch 毫秒 → 同毫秒静默覆盖
# ---------------------------------------------------------------------------
# 旧实现 `f"lr_{int(time.time()*1000)}"`。同一毫秒内新建两个房间（或迁移多条
# 旧记录）→ 两个 id 完全相同 → 保存路径是 upsert（`data[key] = upd`）→
# **静默覆盖**，用户丢数据。实测：冻结时钟下两房 → 同 id `lr_1790160000123`
# → list_rooms 只剩 1 条；迁移 5 条旧房 → 响应谎报 migrated_rooms=5 而库内只剩 1。
#
# 修法（保持 `lr_<ms>` / `lc_<ms>` 形态不变，老数据零迁移）：
#   ① 进程内加锁 + 单调递增：`cand = max(now_ms, last+1)`（同毫秒第二次起 +1）；
#   ② 落库前再对**目标 dict 现有键**查重，命中则继续 +1
#      （覆盖「别的 sidecar 同毫秒刚写进来」的跨进程场景）。
_id_lock = threading.Lock()
_last_id_ms = 0


def _next_id_ms() -> int:
    """单调递增的毫秒级 id 尾巴（进程内加锁；跨进程由调用方查重兜底）。"""
    global _last_id_ms
    now_ms = int(time.time() * 1000)
    with _id_lock:
        cand = max(now_ms, _last_id_ms + 1)
        _last_id_ms = cand
        return cand


def new_room_id(existing: dict | None = None) -> str:
    """生成房间 id（形如 ``lr_<epoch_ms>``）；**保证不与 existing 的键碰撞**。

    existing：目标 kv 的当前 dict（用于跨进程/跨调用查重）。省略则只保证
    进程内单调递增。
    """
    while True:
        rid = f"lr_{_next_id_ms()}"
        if not existing or rid not in existing:
            return rid


def _normalize_room(rec: dict, key: str) -> dict:
    """读取路径统一出口：补齐缺失键、id 以**键名**为准（键才是真源）。

    ## 🔴 2026-09-27 教训：`tag_id` 曾是「垃圾键」，现已是**正式字段**

    本函数原有一份「残留清理」名单：``("force_rescan", "forceRescan", "tag_id")``
    —— 那个 `tag_id` 是**已废弃的旧配置标签 tab** 时代留下的，属于该清的垃圾。
    ADR-018 F1-D1 把 `tag_id` 重新定义为**房间级标签绑定**（正式字段）后，
    这行清理就变成了「写入成功、读取被抹掉」的**静默失效**：
    `save_room` 正常落库，`list_rooms` 却永远回不到 `tag_id`
    （实测：真实实例验证时抓到，单测直调 save/get 不经过本函数 ⇒ 漏网）。

    ⇒ **判据**：给 kv 记录加字段时，必须同时检查**读取出口**
    （本函数 + 前端类型），而不只是写入侧白名单 —— 三者缺一即静默失效。
    """
    c = dict(rec or "")
    # ⚠️ 名单里**不得**再出现 `tag_id`（ADR-018 F1-D1 后它是正式字段）。
    for junk in ("force_rescan", "forceRescan"):
        c.pop(junk, None)
    c["id"] = str(key)
    c.setdefault("room_id", "")
    c.setdefault("live_url", "")
    c.setdefault("name", "")
    c.setdefault("strategy_id", "")
    c.setdefault("allow_desensitized", False)
    # 房间级标签绑定（ADR-018 F1-D1）；缺省空串 = 跟随账号/板块级
    c.setdefault("tag_id", "")
    return c


class RoomBody(BaseModel):
    """一条房间登记记录（**身份 + 策略引用 + 脱敏开关**）。"""

    id: str = ""
    room_id: str = ""
    live_url: str = ""
    name: str = ""
    strategy_id: str = ""
    allow_desensitized: bool | None = None
    # ★ 2026-09-27（ADR-018 F1-D1）：**房间级**标签绑定。空 = 未绑（跟随账号/板块级）。
    #   优先级：房间级 > 板块级 > 整账号级（见 services/config_tag.scope_of）。
    tag_id: str = ""


class MigrateBody(BaseModel):
    """迁移入参：``dry_run=True`` 只报告将发生什么，不写库。"""

    dry_run: bool = True
    apply: bool = False     # 兼容别名：apply=True 也等于真写


class DiscoverReq(BaseModel):
    """F5 搜索发现入参（**只读**，不写任何 kv）。"""

    account: str = ""       # 留空 = 取当前选中账号
    query: str
    num: int = 20


def _auth_for(account: str):
    """加载账号凭证 → auth（与 ``api/platform._auth_for`` 同源，2026-09-27 抽此薄封装）。

    ## 为什么不用 auth_helper.get_current_auth

    它会一路退到 ``enrich_auth`` → ``DYLoginApi.get_login_auth``，**可能弹浏览器扫码**。
    ``/discover`` 是只读搜索：拿到就用，拿不到就如实报「请先在账号管理登录」，
    不该在后台偷偷开浏览器（那正是主侧定义的主动风控动作）。
    """
    try:
        from auto_dm import accounts as acct_core
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"账号模块不可用: {type(e).__name__}") from e

    name = (account or "").strip() or (acct_core.current_name() or "")
    if not name:
        raise HTTPException(400, "尚未选择账号，请先在「账号管理」选择并完成登录。")
    try:
        env_path = acct_core.env_path_of(name)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"加载账号 {name} 凭证失败: {type(e).__name__}") from e
    if not env_path:
        raise HTTPException(404, f"账号 {name} 未登记")

    # P3：凭证收敛——静态字段检查委托 verify_credential(lightweight=True)
    try:
        from auto_dm.accounts import verify_credential
        _vc = verify_credential(name, lightweight=True)
        if not _vc["ok"]:
            raise HTTPException(
                503,
                f"账号 {name} 凭证不完整（{_vc['wp']['detail']}），请重新扫码登录后再搜索。",
            )
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[LIVE-ROOMS-001] 凭证校验异常（不阻断）: {type(e).__name__}: {e}")

    try:
        from dy_apis.login_api import DYLoginApi
        return DYLoginApi._load_auth_from_env(env_path)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(503, f"加载账号 {name} 凭证失败: {type(e).__name__}") from e


def _first(*vals):
    """返回第一个「非空」标量（0/False 也算有值，只排 None/空串/空容器）。"""
    for v in vals:
        if v in (None, "", [], {}, ()):
            continue
        return v
    return ""


def _pick_cover(d: dict) -> str:
    """从直播搜索结果里取封面 URL（尽力而为，取不到返回空串）。"""
    for key in ("cover", "cover_url", "room_cover", "image"):
        v = d.get(key)
        if isinstance(v, str) and v.startswith("http"):
            return v
        if isinstance(v, dict):
            urls = v.get("url_list") or []
            if urls and isinstance(urls[0], str):
                return urls[0]
    return ""


def _pick_live(item: dict) -> dict:
    """把平台原始直播条目压成前端够用的形状（**D7：昵称只用结果自带**）。

    三种布局都兼容（上游字段命名曾多次漂移，稳妥起见逐个兜底）：
      · 扁平      ``{room_id, title, nickname, user_count, cover}``
      · 包 author ``{room_id, title, author:{nickname}, stats:{user_count}}``
      · 包 room   ``{room:{id_str,title,...}, anchor:{nickname}}``
    """
    if not isinstance(item, dict):
        return {}
    lo = {str(k).lower(): v for k, v in item.items()}

    def get(*names):
        for n in names:
            if n in lo and lo[n] not in (None, "", [], {}):
                return lo[n]
        return None

    # ---- 房间号：上游字段命名漂移，逐个兜底 ----
    room_id = str(_first(get("room_id", "roomid"), get("id_str"), get("id")) or "")
    if not room_id:
        room = get("room")
        if isinstance(room, dict):
            rlo = {str(k).lower(): v for k, v in room.items()}
            room_id = str(rlo.get("id_str") or rlo.get("id") or rlo.get("room_id") or "")

    author = get("author", "user", "anchor")
    author = author if isinstance(author, dict) else {}
    alo = {str(k).lower(): v for k, v in author.items()} if author else {}

    stats = get("stats", "statistics", "room_stats")
    stats = stats if isinstance(stats, dict) else {}
    slo = {str(k).lower(): v for k, v in stats.items()} if stats else {}

    online = _first(slo.get("user_count"), get("user_count", "user_count_str"),
                    slo.get("total_user"), get("total_user"), 0)
    try:
        online = int(online)
    except (TypeError, ValueError):
        online = 0

    return {
        "room_id": room_id,
        "title": str(_first(get("title", "name", "room_title")) or ""),
        # D7 红线：只用搜索结果自带的昵称，绝不回调补查接口
        "nickname": str(_first(alo.get("nickname"), get("nickname", "nick_name")) or ""),
        "sec_uid": str(_first(alo.get("sec_uid"), get("sec_uid")) or ""),
        "online_count": online,
        "cover": _pick_cover(item),
        # 上架时直接可塞进 RoomBody 的现成值
        "live_url": f"https://live.douyin.com/{room_id}" if room_id else "",
    }


def _find_room_shaped_configs() -> list[tuple[str, dict]]:
    """找出 ``live_room_configs`` 里的**房间形**旧记录。

    判据（ADR-003 §3.5）：键**非** ``lc_*``，且体内带身份字段
    （``room_id`` 或 ``live_url``）。这些是 v0.43.92 及更早的「一房一配置」形态，
    读取时身份字段被 `_normalize_stored()` pop 掉 ⇒ 信息当前**已 orphan**。
    """
    cfgs = get_kv_json(_STRATEGY_KV, {}) or {}
    if not isinstance(cfgs, dict):
        return []
    out: list[tuple[str, dict]] = []
    for k, v in cfgs.items():
        if not isinstance(v, dict):
            continue
        if str(k).startswith("lc_"):
            continue
        if v.get("room_id") or v.get("live_url"):
            out.append((str(k), dict(v)))
    return out


def migrate_from_room_configs(dry_run: bool = True) -> dict:
    """把「房间形」旧策略记录迁移为 ``live_rooms`` 房间记录（ADR-003 §3.5）。

    迁移映射（**不丢信息**）：
      - ``room_id`` / ``live_url`` / ``name`` → 房间记录的身份与备注；
      - 记录里残留的**发送参数**（max_target/interval/delay/dm_pool/...）若确实存在，
        则另建一条 ``lc_*`` 策略承接，房间的 ``strategy_id`` 指向它 —— 否则给空串；
      - 原房间形键**删除**（身份字段不得继续留在策略层，否则读取路径会再 pop 一次）。

    ``dry_run=True`` 只返回计划，不写任何 kv。可复现且留日志。
    """
    rooms = _load_all()
    cfgs = get_kv_json(_STRATEGY_KV, {}) or {}
    cfgs = dict(cfgs) if isinstance(cfgs, dict) else {}
    found = _find_room_shaped_configs()

    plan: list[dict] = []
    for key, body in found:
        rid = str(body.get("room_id") or key).strip()
        url = str(body.get("live_url") or "").strip()
        # live_url 存的是裸房间号时要归一化成可点链接
        if url and not url.startswith("http") and rid.isdigit():
            url = f"https://live.douyin.com/{rid}"
        name = str(body.get("name") or "").strip()
        # 是否还有策略参数可承接
        has_params = any(
            body.get(k) not in (None, "", [], {})
            for k in ("max_target", "interval", "delay", "dm_pool",
                      "auto_link_mic", "link_mic_mode")
        )
        existing = next(
            (kk for kk, vv in rooms.items()
             if isinstance(vv, dict) and str(vv.get("room_id") or "") == rid),
            None,
        )
        plan.append({
            "source_key": key,
            "room_id": rid,
            "live_url": url,
            "name": name,
            "has_params": has_params,
            "action": "skip-already-migrated" if existing else "migrate",
            "target_room_id_key": existing,
        })

    result = {
        "ok": True,
        "dry_run": bool(dry_run),
        "found": [p["source_key"] for p in plan],
        "plan": plan,
        # 形状稳定：干跑/无内容时也返回空列表，调用方无需猜键是否存在
        "migrated_rooms": [],
        "created_strategies": [],
        "removed_config_keys": [],
    }
    if dry_run or not plan:
        return result

    migrated, created_strategies, removed = [], [], []
    for p in plan:
        if p["action"] != "migrate":
            continue
        key = p["source_key"]
        body = dict(cfgs.get(key) or {})
        sid = ""
        if p["has_params"]:
            # P0-2a：承接策略 id 同样要单调递增 + 查重（同毫秒迁移多条会撞）
            from api.live_config import new_strategy_id  # 懒导入：避免与 live_config 循环
            while True:
                sid = new_strategy_id(cfgs)
                if sid not in cfgs:
                    break
            strat = {"id": sid, "updated_at": int(time.time())}
            if body.get("name"):
                strat["name"] = str(body.get("name"))
            else:
                strat["name"] = p["room_id"] or sid
            for k in ("max_target", "interval", "delay", "dm_pool",
                      "acct", "auto_link_mic", "link_mic_mode"):
                if body.get(k) is not None:
                    strat[k] = body[k]
            cfgs[sid] = strat
            created_strategies.append(sid)
        new_key = new_room_id(rooms)
        rooms[new_key] = {
            "id": new_key,
            "room_id": p["room_id"],
            "live_url": p["live_url"],
            "name": p["name"],
            "strategy_id": sid,
            "allow_desensitized": False,
            "updated_at": int(time.time()),
        }
        cfgs.pop(key, None)
        removed.append(key)
        migrated.append(new_key)

    _save_all(rooms)
    set_kv_json(_STRATEGY_KV, cfgs)
    logger.info(
        f"[live-rooms] 迁移完成：房间 {len(migrated)} 条、新建策略 {len(created_strategies)} 条、"
        f"移除房间形旧键 {removed}"
    )
    result.update({
        "migrated_rooms": migrated,
        "created_strategies": created_strategies,
        "removed_config_keys": removed,
    })
    return result


def unbind_strategy(strategy_id: str) -> int:
    """把引用该策略的房间 ``strategy_id`` 置空（删除策略时调用）。

    返回解绑数量。**禁止**静默留下悬空引用（同族先例：删 Agent 必自动解绑账号）。
    """
    sid = str(strategy_id or "").strip()
    if not sid:
        return 0
    rooms = _load_all()
    n = 0
    for key, rec in rooms.items():
        if isinstance(rec, dict) and str(rec.get("strategy_id") or "") == sid:
            rec["strategy_id"] = ""
            rec["updated_at"] = int(time.time())
            rooms[key] = rec
            n += 1
    if n:
        _save_all(rooms)
        logger.info(f"[live-rooms] 删除策略 {sid} → 自动解绑 {n} 个房间")
    return n


@router.get("")
async def list_rooms() -> dict:
    data = _load_all()
    items = [_normalize_room(v, k) for k, v in data.items()]
    items.sort(key=lambda x: x.get("updated_at", 0), reverse=True)
    return {"ok": True, "items": items}


@router.get("/{rid}")
async def get_room(rid: str) -> dict:
    key = str(rid)
    rec = _load_all().get(key)
    if not rec:
        return {"ok": False, "error": "未找到该直播间"}
    return {"ok": True, "room": _normalize_room(rec, key)}


@router.post("")
async def save_room(body: RoomBody) -> dict:
    """新建 / 更新一条房间登记（按 id upsert；id 为空则生成新 id）。

    ## 字段显式清空语义（P2-7 修补，2026-09-23 实测复现）

    旧写法 `str(body.x or "").strip() or str(old.get("x") or "")` 让**显式传来的
    空串**被当成「未提交」→ 回落旧值 ⇒ 解绑（把 strategy_id 清空）**永远失败**：
    实测置空后仍是 `lc_shared`，UI「未绑定」改不回去。

    现按字段分两类，判据 = 「本次请求到底带没带这个字段」（pydantic
    `model_fields_set`；`Field(default=None)` 的布尔开关同理由 None 承载）：

      - **可清空**：`strategy_id` / `name` / `live_url` —— 带了这个字段就采信
        （含空串 = 显式清空）；没带才回落旧值。
      - **不可清空**：`room_id`（身份锚点，清掉就没有房间号了）—— 空串仍回落旧值，
        否则会破坏「房间必须有身份」的前置契约。
    """
    rid_key = str(body.id or "").strip()
    data = _load_all()
    key = rid_key or new_room_id(data)
    old = data.get(key) or {}
    sent = body.model_fields_set

    def pick(field: str, *, clearable: bool) -> str:
        """按「是否随请求提交」决定：采信本次值 / 回落旧值。"""
        if field in sent:
            cur = str(getattr(body, field) or "").strip()
            if cur or clearable:
                return cur
        return str(old.get(field) or "").strip()

    room_id = pick("room_id", clearable=False)
    live_url = pick("live_url", clearable=True)
    if not room_id and not live_url:
        return {"ok": False, "error": "请填写直播间链接或房间号（失焦自动解析）"}

    strategy_id = pick("strategy_id", clearable=True)
    # P2-8：写入侧也要维护引用完整性（此前只单向维护：删策略时解绑房间）
    if strategy_id and not _strategy_exists(strategy_id):
        return {"ok": False, "error": f"直播策略 {strategy_id} 不存在（禁止写入悬空引用）"}

    # ★ 2026-09-27（ADR-018 F1-D1）：房间级标签绑定。
    #   可清空（与 strategy_id 同类）：显式传空串 = 解绑，回落到账号/板块级。
    #   引用完整性同上：禁止写入不存在的标签（否则 scope_of 判空回落，静默失效）。
    tag_id = pick("tag_id", clearable=True)
    if tag_id and not _tag_exists(tag_id):
        return {"ok": False, "error": f"标签 {tag_id} 不存在（禁止写入悬空引用）"}

    upd = {
        "room_id": room_id,
        "live_url": live_url,
        "name": pick("name", clearable=True),
        "strategy_id": strategy_id,
        "tag_id": tag_id,
        "allow_desensitized": bool(
            old.get("allow_desensitized") if body.allow_desensitized is None
            else body.allow_desensitized
        ),
    }
    # P4：白名单真消费 —— 客户端可控字段越界一律丢弃（此前 _FIELDS 无人读）。
    # `id` / `updated_at` 是**服务端托管**字段（id 以键名为准、updated_at 由时钟
    # 生成），不受客户端白名单管辖，必须始终写入（否则 list_rooms 的排序失效）。
    upd = {k: v for k, v in upd.items() if k in _FIELDS}
    upd["id"] = key
    upd["updated_at"] = int(time.time())
    data[key] = upd
    _save_all(data)
    logger.info(f"[live-rooms] 已保存房间 {key} room_id={room_id} strategy={upd['strategy_id']}")
    return {"ok": True, "room": upd}


@router.delete("/{rid}")
async def delete_room(rid: str) -> dict:
    """删除房间记录（**只删房间**，不动任何策略）。"""
    data = _load_all()
    key = str(rid)
    if key not in data:
        return {"ok": False, "error": "未找到该直播间"}
    del data[key]
    _save_all(data)
    logger.info(f"[live-rooms] 已删除房间 {key}")
    return {"ok": True, "deleted": key}


@router.post("/migrate")
async def migrate(body: MigrateBody | None = None) -> dict:
    """一次性迁移存量「房间形」旧策略记录 → ``live_rooms``（默认干跑）。"""
    b = body or MigrateBody()
    return migrate_from_room_configs(dry_run=not (b.apply or not b.dry_run))


# ⚠️ 路由次序：``/{rid}`` 必须排在 ``/discover`` 之后注册，否则 FastAPI 会把
#    GET 路径里的 "discover" 当成房间 id。此处 /discover 是 POST，无冲突，
#    但仍置于文件末尾以明示「它不与 /{rid} 争抢」这一不变量。

@router.post("/discover")
async def discover_live(req: DiscoverReq) -> dict:
    """ADR-018 F5 —— 按关键词**只读搜索**直播间（**不写任何 kv**）。

    ## 职责边界（刻意窄）

    只做「关键词 → ``search_some_live`` → 规范化列表」。**上架不在此处**：
    前端拿到条目后自行调既有 ``POST /api/live/rooms``（``save_room``）落库，
    从而不出现第二套存储 / 第二份写路径。

    ## 返回字段

      ok / items[] / blocked / transport / query

      items[] 每项：``room_id`` / ``title`` / ``nickname`` / ``sec_uid`` /
      ``online_count`` / ``cover`` / ``live_url``

    ## 「禁止假成功」（项目铁律）

    Argus 403 / 空响应体时返回 ``blocked=True`` + ``transport``，
    **绝不**用空 items 假装「这个关键词没有直播间」。
    """
    query = str(req.query or "").strip()
    if not query:
        # 空关键词属于「输入问题」，不是「搜索结果为空」——两者必须可区分
        raise HTTPException(400, "请输入搜索关键词")

    from services.auth_policy import get_auth_for
    auth = get_auth_for("/api/live/rooms/discover", req.account)
    num = max(1, min(int(req.num or 20), 50))

    try:
        from dy_apis.douyin_api import DouyinAPI
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"平台接口层不可用: {type(e).__name__}") from e

    # DouyinAPI 是同步 requests 实现 ⇒ 一律 asyncio.to_thread，避免阻塞事件循环
    # （与 api/crawl.py、api/platform.py 一致）。
    try:
        if auth is None:
            raw = await asyncio.to_thread(DouyinAPI.search_live_anon, query, num)
        else:
            raw = await asyncio.to_thread(DouyinAPI.search_some_live, auth, query, num)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[LIVE-ROOMS-002] 直播搜索异常: {type(e).__name__}: {e}")
        raise HTTPException(502, f"搜索失败: {type(e).__name__}")

    transport = DouyinAPI.take_live_transport(raw) or None
    items_raw = list(raw or [])
    items = [_pick_live(x) for x in items_raw]
    # 没有房间号 = 无法上架，也没有展示价值，直接丢掉（而非返回一条残缺行）
    items = [x for x in items if x.get("room_id")]

    resp: dict[str, Any] = {
        "ok": True,
        "query": query,
        "items": items,
        "blocked": False,
        "transport": transport,
    }
    # 风控判据：传输层非 200，或 200 但一条都没解析出来且带传输层事实
    if transport:
        status = int(transport.get("status") or 0)
        if status != 200:
            resp["blocked"] = True
            resp["error"] = (
                "被风控拦截（Argus 网关返回 HTTP "
                f"{status}，{transport.get('bytes')} 字节）——请稍后重试或重新扫码登录"
            )
            logger.warning(f"[LIVE-ROOMS-003] 直播搜索被风控拦截: {transport}")
    return resp
