"""统一配置中心。

把散落在 7 个承载层（环境变量 / config.py / kv_store / auto_dm/config.py /
功能页）的配置收敛到这里，供设置页统一管理。

设计要点
--------
1. **单一落盘**：SQLite `kv_store["app_config"]`（单 key，按 section 分节）。
   与既有 `kv_store["config"]`（任务配置）并存，互不覆盖。
2. **schema 驱动**：`SECTIONS` 声明每个字段的 label / type / default / min / max /
   options / apply / hint / risk，前端据此渲染，无需为每字段写 UI。
3. **取值优先级**：配置中心值 → 环境变量 → schema 默认值。
4. **apply 三分类**（UI 必须逐字段标注，否则用户改了以为生效）：
   - `hot`            保存后下一次读取即生效
   - `restart_daemon` 需重启对应守护（recv_daemon / browser_daemon）
   - `restart_backend` 需重启 backend（启动参数类）

本模块**不主动改任何消费方**——消费方按 §B 阶段逐个接入（保留模块级兜底常量，
新增读配置函数）。这样接线失败时行为与接线前一致。
"""

from __future__ import annotations

import os
import threading
from typing import Any

from loguru import logger

import database

_KV_KEY = "app_config"
_ENV_PREFIX = "DY_"

_lock = threading.RLock()


# ---------------------------------------------------------------------------
# Schema 定义
# ---------------------------------------------------------------------------
# 字段元字段说明：
#   key     配置键（section 内唯一）
#   label   中文名
#   type    int | float | bool | str | select
#   default 默认值
#   min/max 数值范围（type=int/float 时生效）
#   options 下拉选项（type=select）
#   env     关联的环境变量名（None 表示不从环境读）
#   apply   hot | restart_daemon | restart_backend
#   hint    输入下方提示
#   risk    True 表示风控敏感（UI 加下限保护 + 风险提示）

from .app_config_schema import SECTIONS



# ---------------------------------------------------------------------------
# 读写
# ---------------------------------------------------------------------------

def _load(scope_key: str | None = None) -> dict:
    """读配置。scope_key 为 None 读全局；否则读该 scope（如标签）的配置。

    v0.38.2：标签只是「指引」，参数仍由本模块（原单位）按 scope 隔离存储，
    标签自身不持有任何副本 —— 避免两处存储不一致。
    """
    try:
        from database import get_kv_json
        data = get_kv_json(scope_key or _KV_KEY, {})
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save(data: dict, scope_key: str | None = None) -> bool:
    """落盘配置。**返回是否成功**（2026-09-17 修补）。

    OCR 审查 HIGH：原实现裸 `except: pass`，注释写「落盘失败不影响内存语义，
    消费方仍能读到本次值」—— 但**这句是错的**：本模块的 `get()` 每次都从 DB
    重新 `_load()`，模块内**不存在内存副本**。于是落盘失败时：
      · 接口仍返回 200 + 回读到的 schema 默认值（`get()` 回落默认）；
      · 用户以为保存成功，**重启后配置丢失**，且无任何日志线索。
    这与同文件 `drop_scope` 注释记载的事故同型（曾因 NameError 被静默吞掉，
    表现为「删了标签但参数还在」），故此处按同样标准处理：**不吞异常**。

    改法：记录 error 日志并返回 False，由 `save_section`/`reset_section`
    转成明确错误，避免"假成功"。
    """
    try:
        from database import set_kv_json
        set_kv_json(scope_key or _KV_KEY, data)
        return True
    except Exception as e:  # noqa: BLE001
        logger.error(f"[CFG-011] app_config 落盘失败（本次修改不会持久化，"
                     f"重启后将丢失）: {type(e).__name__}: {e}")
        return False


def section_stored(section: str) -> dict:
    """读该分区**用户实际保存过**的原始值（不含默认值/环境变量）。

    v0.38.3：供一次性迁移判断「统一中心是否配置过该分区」。
    """
    return dict(_load().get(section) or {})


def scope_key(scope: str | None) -> str:
    """scope（如标签 id）→ kv key。None 返回全局 key。"""
    return f"{_KV_KEY}::{scope}" if scope else _KV_KEY


def drop_scope(scope: str) -> bool:
    """删除某 scope 的全部参数（删标签时清理，避免孤儿数据残留）。

    返回是否删除成功。**不吞异常** —— 曾因漏 import database 导致
    NameError 被 except 静默吞掉，表现为「删了标签但参数还在」。
    """
    try:
        conn = database.get_db()
        cur = conn.execute("DELETE FROM kv_store WHERE key=?", (scope_key(scope),))
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        logger.warning(f"[CFG-010] [config] drop_scope 失败: {e}")
        return False


def _field_meta(section: str, key: str) -> dict | None:
    sec = SECTIONS.get(section)
    if not sec:
        return None
    return (sec.get("fields") or {}).get(key)


def _from_env(meta: dict, ftype: str):
    name = meta.get("env")
    if not name:
        return None
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return None
    try:
        if ftype == "bool":
            return str(raw).strip() not in ("0", "false", "False", "no", "")
        if ftype == "int":
            return int(float(raw))
        if ftype == "float":
            return float(raw)
        return str(raw)
    except (TypeError, ValueError):
        return None


def _coerce(value: Any, ftype: str, meta: dict):
    """把输入值强转到字段类型，并做范围/选项校验。越界返回 None（调用方忽略）。"""
    try:
        if ftype == "bool":
            return bool(value) if not isinstance(value, str) else value.strip() not in (
                "0", "false", "False", "no", "")
        if ftype == "int":
            v = int(float(value))
        elif ftype == "float":
            v = float(value)
        else:
            v = str(value)
    except (TypeError, ValueError):
        return None

    if ftype in ("int", "float"):
        lo, hi = meta.get("min"), meta.get("max")
        if lo is not None and v < lo:
            return None
        if hi is not None and v > hi:
            return None
    if ftype == "select":
        opts = meta.get("options") or []
        # 2026-09-17 修补（OCR 审查 HIGH —— select 校验恒判非法）：
        # `options` 是 **dict 列表**（[{"value": "observe", "label": "..."}]），
        # 而 `v` 是裸值字符串。原实现 `v not in opts` 拿字符串去和 dict 比对，
        # **恒为 False**（永远不相等）→ 所有 select 字段的值（含 schema 默认值）
        # 一律被判非法并丢弃。实证：cred_refresh_mode 默认 'observe' 校验失败
        # （test_app_config.test_defaults_pass_their_own_range 长期失败的真实根因）。
        # 现同时兼容两种声明形式：dict 列表（取 value）与裸值列表。
        if opts:
            _allowed = []
            for o in opts:
                if isinstance(o, dict):
                    _allowed.append(o.get("value"))
                else:
                    _allowed.append(o)
            if v not in _allowed:
                return None
    return v


def get(section: str, key: str, default: Any = None,
        scope: str | None = None) -> Any:
    """取一个配置值。优先级：scope（标签）→ 全局 → 环境变量 → schema 默认。

    v0.38.2：scope 为标签 id 时先读该标签的值，未配则回落全局。
    标签只作「指引」，参数仍由本模块按 scope 隔离存储。
    """
    meta = _field_meta(section, key)
    if meta is None:
        return default
    ftype = meta.get("type", "str")

    # ① scope（标签）优先
    if scope:
        sv = (_load(scope_key(scope)).get(section) or {}).get(key)
        if sv is not None:
            cv = _coerce(sv, ftype, meta)
            if cv is not None:
                return cv
    # ② 全局
    stored = (_load().get(section) or {}).get(key)
    if stored is not None:
        v = _coerce(stored, ftype, meta)
        if v is not None:
            return v

    v = _from_env(meta, ftype)
    if v is not None:
        cv = _coerce(v, ftype, meta)
        if cv is not None:
            return cv

    dv = _coerce(meta.get("default"), ftype, meta)
    return dv if dv is not None else default


def get_section(section: str, scope: str | None = None) -> dict:
    """取整节配置（含默认值）。scope 为标签 id 时叠加标签值。"""
    sec = SECTIONS.get(section)
    if not sec:
        return {}
    return {k: get(section, k, scope=scope) for k in (sec.get("fields") or {})}


def get_all() -> dict:
    return {s: get_section(s) for s in SECTIONS}


def save_section(section: str, values: dict, scope: str | None = None) -> dict:
    """保存整节（只认 schema 内字段，越界/非法值直接丢弃）。

    返回最终生效值。scope 为标签 id 时写入该标签的隔离存储。
    """
    if section not in SECTIONS:
        return {}
    fields = SECTIONS[section].get("fields") or {}
    with _lock:
        data = _load(scope_key(scope))
        cur = dict(data.get(section) or {})
        for k, v in (values or {}).items():
            meta = fields.get(k)
            if meta is None:
                continue
            cv = _coerce(v, meta.get("type", "str"), meta)
            if cv is None:
                continue
            cur[k] = cv
        data[section] = cur
        # 2026-09-17 修补（OCR 审查 HIGH）：把落盘失败**显式暴露**出去。
        # 原实现忽略 _save 异常并回读（回落到 schema 默认值）→ 接口 200 +
        # 看似合理的值，用户以为保存成功，重启后配置丢失。
        if not _save(data, scope_key(scope)):
            raise RuntimeError(
                f"配置落盘失败（section={section}）：本次修改不会持久化。"
                f"请检查磁盘空间/数据库可用性后重试。")
    return get_section(section, scope=scope)


def reset_section(section: str, scope: str | None = None) -> dict:
    """清空某节回默认值。scope 为标签 id 时只清该标签的覆盖值。"""
    with _lock:
        data = _load(scope_key(scope))
        data.pop(section, None)
        # 2026-09-17 修补：同 save_section，落盘失败必须显式报错。
        if not _save(data, scope_key(scope)):
            raise RuntimeError(
                f"配置重置落盘失败（section={section}）：本次重置不会持久化。")
    return get_section(section, scope=scope)


def schema() -> dict:
    """下发给前端的表单元数据（含当前默认值，不含已存值）。"""
    out = {}
    for sname, sec in SECTIONS.items():
        out[sname] = {
            "label": sec.get("label", sname),
            "fields": {
                k: {
                    "label": m.get("label", k),
                    "type": m.get("type", "str"),
                    "default": m.get("default"),
                    "min": m.get("min"),
                    "max": m.get("max"),
                    "options": m.get("options"),
                    "apply": m.get("apply", "hot"),
                    "hint": m.get("hint", ""),
                    "risk": bool(m.get("risk", False)),
                }
                for k, m in (sec.get("fields") or {}).items()
            },
        }
    return out


def apply_modes_of(section: str, keys: list[str]) -> list[str]:
    """给定改动字段，返回需要重启的目标列表（去重）。"""
    mods = set()
    for k in keys or []:
        m = _field_meta(section, k)
        if not m:
            continue
        a = m.get("apply", "hot")
        if a == "restart_daemon":
            mods.add("daemon")
        elif a == "restart_backend":
            mods.add("backend")
    return sorted(mods)
