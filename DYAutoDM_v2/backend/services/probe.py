# -*- coding: utf-8 -*-
"""能力探针（Capability Probe, M1 / M3 / M7）—— 「现在这个能力行不行？」

## 为什么需要本模块（设计契约，见 工作记忆/02_效果定义与探针.md）

本项目是**对抗性爬虫**：外部契约持续漂移，「失败是常态」。因此判定成败
不能看「代码是否按老定义执行」，只能看「当前能否真实达成业务效果」（纪律 D1）。

历史代价：2026-09-14「全解除」架构改动后**无人敢回退** —— 因为没有探针，
任何回退都无法验证「业务效果是否还在」。所以：
**不先建 M1，P3 架构重构无法进行。**

## 三态（M3，禁止二态）

| state | 含义 | 判据 |
|---|---|---|
| `healthy` | 确认成功 | 覆盖率 ≥ 阈值 且 证据齐全 |
| `degraded` | 部分成功 | 覆盖率介于两阈值之间（**必须带覆盖率**） |
| `failed` | 确认失败 | 覆盖率 < 下阈值 / 无数据 / 链路报错 |
| `unknown` | 无法判定 | 数据不足（如从未跑过捕获）。**不得冒充 healthy** |

## 零风控原则（本模块的硬边界）

探针**只读本地事实**：SQLite 库 + 本项目自己的运行日志。
**绝不发起任何网络请求、绝不启动/触碰浏览器（BCC）、绝不查询抖音接口**。
需要真实链路的验证由调用方（`POST /api/probe/run`）显式触发 `capture_all`，
那属于既有业务路径，不属本模块。

## 契约（每条探针必须返回）

```json
{
  "capability": "conversation_capture",
  "state": "healthy|degraded|failed|unknown",
  "coverage": 0.0,            // 覆盖率，可为 null（= 需外部观测源，诚实标注）
  "confidence": "A|B|C|D",     // 数据来源最低级（A=真实实例取证）
  "measured_at": "ISO8601",
  "evidence": ["..."],         // 硬证据；报 healthy 时不得为空
  "metrics": {},               // 本能力的实测数字
  "baseline_delta": null,      // 与上次/历史最佳基线的差值（M7）
  "reasons": []                // 判定理由（可观测，不得静默）
}
```
"""
from __future__ import annotations

import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from loguru import logger


# ─────────────────────────────────────────────────────────────────────────────
# 基础工具
# ─────────────────────────────────────────────────────────────────────────────
def _cfg(section: str, key: str, default: Any = None) -> Any:
    """读配置中心；任何异常回落默认值（与 conversation_capture._cfg 同口径）。"""
    try:
        from services.app_config import get as _get
        v = _get(section, key)
        return default if v is None else v
    except Exception:
        return default


def _cfg_float(section: str, key: str, default: float) -> float:
    try:
        return float(_cfg(section, key, default))
    except Exception:
        return default


def _cfg_int(section: str, key: str, default: int) -> int:
    try:
        return int(_cfg(section, key, default))
    except Exception:
        return default


def _now() -> float:
    return time.time()


def _iso(ts: float | None = None) -> str:
    return datetime.fromtimestamp(ts if ts else _now()).isoformat(timespec="seconds")


def _app_root() -> str:
    """应用根。

    **优先读 `DY_APP_ROOT`** —— 它是本项目部署/测试的权威根（sidecar 铁律要求
    必须带它），且保证测试隔离时探针读的是隔离目录，而不是被同进程其它模块
    改过的 vbrowser 缓存路径（unittest discover 同进程导入，实测会互相串）。
    """
    env_root = (os.environ.get("DY_APP_ROOT") or "").strip()
    if env_root:
        return env_root
    try:
        from vbrowser import app_root
        return str(app_root())
    except Exception:
        return os.getcwd()


def app_version() -> str:
    """当前应用版本（要求能反映**运行中**的版本，优先构建期常量）。"""
    v = os.environ.get("DY_APP_VERSION")
    if v:
        return v
    # 构建期写入的 _build_version 模块（打包态随包分发）
    try:
        import _build_version as bv  # type: ignore
        for attr in dir(bv):
            if attr.startswith("_"):
                continue
            val = getattr(bv, attr, None)
            if isinstance(val, str) and re.fullmatch(r"\d+\.\d+\.\d+", val.strip()):
                return val.strip()
    except Exception:
        pass
    # 源码态：项目根 package.json
    for rel in ("package.json", os.path.join("..", "package.json"),
                os.path.join("..", "..", "package.json")):
        try:
            p = Path(_app_root()) / rel
            if p.is_file():
                import json
                txt = p.read_text(encoding="utf-8", errors="replace")
                m = re.search(r'"version"\s*:\s*"([^"]+)"', txt)
                if m:
                    return m.group(1)
        except Exception:
            continue
    return "unknown"


# ─────────────────────────────────────────────────────────────────────────────
# DB 只读访问
# ─────────────────────────────────────────────────────────────────────────────
def _db_query(sql: str, args: tuple = ()) -> list:
    """只读查询；任何异常返回空列表（探针绝不能因读库失败而抛出）。"""
    try:
        from database import get_db
        cur = get_db().execute(sql, args)
        return list(cur.fetchall())
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[PROBE-004] " + f"[probe] 读库失败（视为无数据）: {e}")
        return []


def db_evidence() -> str:
    try:
        from database import _db_path
        return str(_db_path())
    except Exception:
        return "<未知>"


# 昵称是否「真实」：非空、不等于 peer_id、且不是纯数字占位
def _is_real_nickname(peer_name: Any, peer_id: Any) -> bool:
    s = str(peer_name or "").strip()
    if not s:
        return False
    if peer_id is not None and s == str(peer_id):
        return False
    return not s.isdigit()


# ─────────────────────────────────────────────────────────────────────────────
# 运行日志只读访问（捕获链路的最新事实）
# ─────────────────────────────────────────────────────────────────────────────
_RE_LOGIN = re.compile(r"^\[(?P<acct>[^\]]+)\] (?P<kind>.)+")
_RE_TS = re.compile(r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)")
_RE_FIRSTPACK = re.compile(
    r"\[capture\]\[(?P<acct>[^\]]+)\]\s*首包\s*拉取=(?P<pull>[\d.]+)s\s*解析=(?P<parse>[\d.]+)s"
    r"\s*[（(](?P<bytes>[\d,]+)B\s*->\s*(?P<convs>\d+)\s*会话[）)]"
)
# `[capture][账号] 写库完成：会话 44（含消息 108），昵称命中 uid关联=0 sec_uid关联=0 未命中=44/44`
_RE_WRITE = re.compile(
    r"\[capture\]\[(?P<acct>[^\]]+)\]\s*写库完成：会话\s*(?P<conv>\d+)\s*"
    r"[（(]含消息\s*(?P<msg>\d+)[）)][，,]?\s*昵称命中\s*uid关联=(?P<byuid>\d+)\s*"
    r"sec_uid关联=(?P<bysec>\d+)\s*未命中=(?P<miss>\d+)/(?P<total>\d+)"
)


def _logs_dir() -> Path:
    return Path(_app_root()) / "logs"


def _parse_ts(s: str) -> float | None:
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).timestamp()
        except Exception:
            continue
    return None


def _log_candidates(since_ts: float) -> list[Path]:
    """候选日志文件：捕获事实由 backend / recv_daemon / browser_daemon 落盘。

    以**文件 mtime** 为准筛选（不猜文件名格式 —— 部署形态可能不同），
    再按 mtime 升序返回，保证「最后一条」是真最新。
    """
    d = _logs_dir()
    out: list[tuple[float, Path]] = []
    try:
        for p in d.glob("*.log"):
            try:
                mt = p.stat().st_mtime
            except Exception:
                continue
            if mt >= since_ts:
                out.append((mt, p))
    except Exception:
        return []
    out.sort()
    return [p for _, p in out]


def latest_capture_facts(account: str, lookback_sec: float = 86400.0) -> dict:
    """从运行日志里提取该账号**最新**一条捕获事实（首包 + 写库完成）。

    返回 {ts, convs_parsed, n_conv, n_msg, by_uid, by_sec, miss, total,
          firstpack_bytes, source}
    找不到 → 空 dict（调用方据此报 failed/unknown，**不得**用 0 冒充）。
    """
    since = _now() - max(60.0, float(lookback_sec))
    facts: dict[str, Any] = {}
    for p in _log_candidates(since):
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = _RE_TS.match(line)
                    if not m:
                        continue
                    ts = _parse_ts(m.group("ts"))
                    if ts is None or ts < since:
                        continue
                    m1 = _RE_FIRSTPACK.search(line)
                    if m1 and m1.group("acct") == account:
                        facts.update({
                            "ts": ts, "convs_parsed": int(m1.group("convs")),
                            "firstpack_bytes": int(m1.group("bytes").replace(",", "")),
                            "source": str(p),
                        })
                        continue
                    m2 = _RE_WRITE.search(line)
                    if m2 and m2.group("acct") == account:
                        facts.update({
                            "ts": ts,
                            "n_conv": int(m2.group("conv")),
                            "n_msg": int(m2.group("msg")),
                            "by_uid": int(m2.group("byuid")),
                            "by_sec": int(m2.group("bysec")),
                            "miss": int(m2.group("miss")),
                            "total": int(m2.group("total")),
                            "source": str(p),
                        })
        except Exception:  # noqa: BLE001
            continue
    return facts


# ─────────────────────────────────────────────────────────────────────────────
# 基线（M7）
# ─────────────────────────────────────────────────────────────────────────────
_KV_PREV = "capability_probe:prev:%s"
_KV_BEST = "capability_probe:best:%s"


def _kv_get(key: str, default: Any = None) -> Any:
    try:
        from services.kv_store import kv_get
        return kv_get(key, default)
    except Exception:
        return default


def _kv_set(key: str, value: Any) -> None:
    try:
        from services.kv_store import kv_set
        kv_set(key, value)
    except Exception:
        pass


def _baseline_compare(capability: str, account: str,
                      snapshot: dict) -> dict:
    """与「上次」和「历史最佳」两个基线比对，写回新的基线段。

    返回 {prev_delta, best_delta, prev_measured_at}。snapshot 必须含 score（0..1）。
    """
    out: dict[str, Any] = {"prev_delta": None, "best_delta": None,
                           "prev_measured_at": None}
    try:
        score = float(snapshot.get("score")) if snapshot.get("score") is not None else None
        key = f"{capability}:{account}"
        prev = _kv_get(_KV_PREV % key) or {}
        best = _kv_get(_KV_BEST % key) or {}
        if score is not None:
            if isinstance(prev.get("score"), (int, float)):
                out["prev_delta"] = round(score - float(prev["score"]), 4)
                out["prev_measured_at"] = prev.get("measured_at")
            if isinstance(best.get("score"), (int, float)):
                out["best_delta"] = round(score - float(best["score"]), 4)
            _kv_set(_KV_PREV % key, {"score": score, "measured_at": _iso()})
            if not isinstance(best.get("score"), (int, float)) or score > float(best["score"]):
                _kv_set(_KV_BEST % key, {"score": score, "measured_at": _iso()})
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[PROBE-005] " + f"[probe] 基线比对失败（不影响判定）: {e}")
    return out


# ─────────────────────────────────────────────────────────────────────────────
# 探针 1：conversation_capture（会话/昵称捕获）
# ─────────────────────────────────────────────────────────────────────────────
def probe_conversation_capture(account: str) -> dict:
    """会话捕获能力探针。

    设计契约（`02_效果定义与探针.md` §2.1）：
      落库会话数/昵称覆盖率/幂等性/不降级性 —— 判据全部来自**本地事实**
      （SQLite 落库结果 + 捕获链路日志），零网络、零浏览器。

    覆盖率口径（诚实）：真正的「会话覆盖率」分母是**抖音侧实际会话数**，
    只有另一个观测源（DOM 枚举）才有；此处不臆造分母 ——
      · 有外部观测数（调用方传入 expected）→ 给出真实覆盖率；
      · 否则 coverage=null，只按绝对量与昵称覆盖率判档（reasons 里写明）。
    """
    min_convs = _cfg_int("capture", "probe_min_convs", 10)
    th_healthy = _cfg_float("capture", "probe_nickname_healthy", 0.95)
    th_degraded = _cfg_float("capture", "probe_nickname_degraded", 0.50)
    stale_sec = _cfg_int("capture", "probe_capture_stale_sec", 3600)

    now = _now()
    rows = _db_query(
        "SELECT conv_id, peer_name, peer_id, avatar FROM dm_conversations WHERE account=?",
        (account,))
    total = len(rows)
    named = sum(1 for r in rows if _is_real_nickname(r["peer_name"], r["peer_id"]))
    avatar = sum(1 for r in rows if (r["avatar"] or "").strip())
    nick_ratio = round(named / total, 4) if total else None
    # 这两个只在「有事实」分支里赋值，末尾 metrics 无条件引用 —— 必须在函数顶先初始化，
    # 否则 total==0 / 无事实 时 UnboundLocalError（实测被自身 try 兜成 unknown，掩盖了真因）。
    fresh_ratio: float | None = None
    eff_ratio: float | None = None

    # 消息与幂等线索（msg_id 有无 → 唯一索引是否真正生效）
    mrows = _db_query(
        "SELECT COUNT(*) AS n, SUM(CASE WHEN msg_id IS NOT NULL AND msg_id<>'' "
        "THEN 1 ELSE 0 END) AS wid FROM dm_messages WHERE account=?", (account,))
    m_total = int(mrows[0]["n"] or 0) if mrows else 0
    m_withid = int(mrows[0]["wid"] or 0) if mrows else 0

    facts = latest_capture_facts(account, lookback_sec=max(stale_sec * 4, 86400))
    last_ts = facts.get("ts")
    age = round(now - last_ts, 1) if last_ts else None

    reasons: list[str] = []
    evidence: list[str] = []
    state = "unknown"
    coverage = None

    if total == 0:
        state = "failed"
        reasons.append("库内该账号会话数为 0 —— 捕获从未成功落库或库路径不对")
    elif not facts:
        state = "failed"
        reasons.append(
            f"近 {int(max(stale_sec * 4, 86400))}s 运行日志内**查不到该账号的捕获记录**"
            f"（库里有 {total} 条，说明是历史遗留，当前链路未确认工作）")
    else:
        _has_write = facts.get("n_conv") is not None
        # 🔴 2026-09-21 真实发现的失效模式：**最近一次捕获自身的关联率**才反映
        #    链路当前状态；库内比例是**历史快照**（可停在好状态），二者必须同时看。
        #    实测：最近一次捕获 `昵称命中 uid关联=0 未命中=44/44`（链路已失效），
        #    而库内 0.9775（历史值）→ 只看库会报 healthy = **探针假健康**。
        if _has_write:
            _a_total = facts.get("total") or 0
            _a_hit = (facts.get("by_uid") or 0) + (facts.get("by_sec") or 0)
            fresh_ratio = round(_a_hit / _a_total, 4) if _a_total else None
        else:
            fresh_ratio = None
        # 有效关联率 = min(库内历史, 最近一次)；任一失守都不得判健康
        _cands = [x for x in (nick_ratio, fresh_ratio) if x is not None]
        eff_ratio = min(_cands) if _cands else None
        evidence.append(
            f"最近一次捕获自身关联率="
            + (f"{fresh_ratio}（uid关联={facts.get('by_uid')} "
               f"sec_uid关联={facts.get('by_sec')} / 共{facts.get('total')}）"
               if fresh_ratio is not None else "（本次未见写库完成）")
            + f"；库内历史比例={nick_ratio} → 有效判定取 min={eff_ratio}")
        evidence.append(f"SQLite 落库：会话 {total}，真实昵称 {named}，头像 {avatar}，"
                        f"消息 {m_total}（带 msg_id {m_withid}）")
        evidence.append(f"库文件：{db_evidence()}")

        if not _has_write:
            # 有首包、无写库完成：链路进行到一半 —— 既不能判健康，也不能判能力失效
            state = "unknown"
            reasons.append("日志只到首包、未见「写库完成」——本次捕获未定论"
                           "（长会话补全可能仍在进行，或中途中断）")
        elif age is not None and age > stale_sec:
            state = "failed"
            reasons.append(f"最新捕获距今 {age:.0f}s > 陈旧阈值 {stale_sec}s —— 能力可能已停摆")
        elif (facts.get("n_conv") or 0) < min_convs:
            state = "degraded"
            reasons.append(f"本次捕获会话数 {facts.get('n_conv')} < 最低阈值 {min_convs}")
        elif eff_ratio is None or eff_ratio < th_degraded:
            state = "failed"
            reasons.append(
                f"有效昵称关联率 {eff_ratio} < {th_degraded}"
                f"（最近一次={fresh_ratio} / 库内={nick_ratio}）"
                f" —— 昵称关联失效（历史失效模式：peer_name 存成数字 uid）；"
                f"不得用库内历史比例冒充健康")
        elif eff_ratio < th_healthy:
            state = "degraded"
            reasons.append(f"有效昵称关联率 {eff_ratio} 介于 "
                           f"[{th_degraded},{th_healthy})"
                           f"（最近一次={fresh_ratio} / 库内={nick_ratio}）")
        else:
            state = "healthy"
            reasons.append(f"会话 {facts.get('n_conv')}（≥{min_convs}）"
                           f"且有效昵称关联率 {eff_ratio} ≥ {th_healthy}"
                           f"（最近一次={fresh_ratio} / 库内={nick_ratio}）")

    # 不降级性（I2，本项目曾违反：15 个覆盖 83 个）
    # 基线用**有效关联率**（= min(库内, 最近一次)），否则「最近一次崩了」不会体现在基线上
    _snap_score = eff_ratio if eff_ratio is not None else nick_ratio
    snapshot = {"score": _snap_score if _snap_score is not None else 0.0}
    base = _baseline_compare("conversation_capture", account, snapshot)
    if base.get("prev_delta") is not None and base["prev_delta"] < 0:
        reasons.append(f"⚠ 不降级性：昵称覆盖率较上次 {base['prev_delta']:+.4f}（劣化）")
        if state == "healthy":
            state = "degraded"
    if base.get("best_delta") is not None:
        evidence.append(f"基线：较上次 {base['prev_delta']}，较历史最佳 "
                        f"{base['best_delta']}（上次 {base.get('prev_measured_at')}）")

    if state == "healthy" and not evidence:
        # 硬性要求：报 healthy 不得无证据（02 §3.2）
        state = "unknown"
        reasons.append("无证据不得报 healthy")

    return {
        "capability": "conversation_capture",
        "state": state,
        "coverage": coverage,
        "coverage_note": "会话覆盖率分母（抖音侧实际会话数）需外部观测源；"
                         "本探针不臆造分母，仅按绝对量+昵称覆盖率判档",
        "confidence": "A",
        "measured_at": _iso(now),
        "evidence": evidence,
        "metrics": {
            "db_conversations": total,
            "db_real_nicknames": named,
            "db_avatars": avatar,
            "nickname_ratio": nick_ratio,
            "last_capture_fresh_ratio": fresh_ratio,
            "effective_ratio": eff_ratio,
            "db_messages": m_total,
            "db_messages_with_msg_id": m_withid,
            "last_capture_at": _iso(last_ts) if last_ts else None,
            "last_capture_source": Path(str(facts.get("source"))).name if facts.get("source") else None,
            "last_capture_age_sec": age,
            "last_capture_n_conv": facts.get("n_conv"),
            "last_capture_nick_uid_hits": facts.get("by_uid"),
            "last_capture_nick_miss": facts.get("miss"),
            "last_capture_total": facts.get("total"),
            "last_capture_bytes": facts.get("firstpack_bytes"),
            "logs_dir": str(_logs_dir()),
            "thresholds": {"min_convs": min_convs, "nickname_healthy": th_healthy,
                           "nickname_degraded": th_degraded, "stale_sec": stale_sec},
        },
        "baseline_delta": base,
        "reasons": reasons,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 探针 2：message_integrity（消息完整度 / 幂等性）
# ─────────────────────────────────────────────────────────────────────────────
def probe_message_integrity(account: str) -> dict:
    """消息落库完整性探针：msg_id 覆盖率 + 重复行 + 会话-消息关联。

    幂等性判据（无需重跑链路）：`uniq_dmmsg` 是部分唯一索引
    （WHERE msg_id IS NOT NULL）。若存在 (account,conv_id,msg_id) 重复行，
    说明索引缺失或被绕过 —— 这是「重复投递 / 脏数据堆积」的直接证据。
    """
    reasons: list[str] = []
    evidence: list[str] = []
    rows = _db_query(
        "SELECT COUNT(*) AS n, SUM(CASE WHEN msg_id IS NOT NULL AND msg_id<>'' "
        "THEN 1 ELSE 0 END) AS wid, COUNT(DISTINCT conv_id) AS convs "
        "FROM dm_messages WHERE account=?", (account,))
    n = int(rows[0]["n"] or 0) if rows else 0
    wid = int(rows[0]["wid"] or 0) if rows else 0
    convs = int(rows[0]["convs"] or 0) if rows else 0
    id_ratio = round(wid / n, 4) if n else None

    dup = _db_query(
        "SELECT conv_id, msg_id, COUNT(*) AS c FROM dm_messages "
        "WHERE account=? AND msg_id IS NOT NULL AND msg_id<>'' "
        "GROUP BY conv_id, msg_id HAVING c > 1 LIMIT 5", (account,))
    dup_n = len(dup)

    # 会话骨架与消息的关联一致性
    orphan = _db_query(
        "SELECT COUNT(*) AS n FROM dm_conversations c WHERE c.account=? "
        "AND NOT EXISTS (SELECT 1 FROM dm_messages m "
        "WHERE m.account=c.account AND m.conv_id=c.conv_id)", (account,))
    orphan_n = int(orphan[0]["n"] or 0) if orphan else 0

    if n == 0:
        state = "failed"
        reasons.append("该账号消息数为 0")
    elif dup_n > 0:
        state = "failed"
        reasons.append(f"存在 {dup_n} 组 (conv_id,msg_id) 重复行 —— 唯一索引未生效/被绕过")
        evidence.append(f"重复样例：{dup[:3]}")
    elif id_ratio is not None and id_ratio < 0.8:
        state = "degraded"
        reasons.append(f"msg_id 覆盖率 {id_ratio} < 0.8 —— 旧行/回执类消息缺钉；"
                       f"幂等性与去重能力受限")
    else:
        state = "healthy"
        reasons.append(f"消息 {n} 条，msg_id 覆盖 {id_ratio}，无重复行")

    if n:
        evidence.append(f"消息 {n} 条 / 覆盖会话 {convs} / 带 msg_id {wid}（{id_ratio}）")
        evidence.append(f"无消息的会话骨架 {orphan_n} 个；库文件 {db_evidence()}")

    snapshot = {"score": id_ratio if id_ratio is not None else 0.0}
    base = _baseline_compare("message_integrity", account, snapshot)

    if state == "healthy" and not evidence:
        state = "unknown"
        reasons.append("无证据不得报 healthy")

    return {
        "capability": "message_integrity",
        "state": state,
        "coverage": id_ratio,
        "confidence": "A",
        "measured_at": _iso(),
        "evidence": evidence,
        "metrics": {"db_messages": n, "with_msg_id": wid, "msg_id_ratio": id_ratio,
                    "distinct_convs": convs, "dup_groups": dup_n,
                    "conv_without_messages": orphan_n},
        "baseline_delta": base,
        "reasons": reasons,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 探针 3：credential_identity（凭证身份一致性 / UID 漂移）
# ─────────────────────────────────────────────────────────────────────────────
def probe_credential_identity(account: str) -> dict:
    """凭证身份一致性探针（复用既有 AUTH-050 判据，不新发明）。

    铁律（2026-09-13，用户定）：**UID 漂移 = 凭证失效**。
    判据：`.env` 里探活得到的 uid 是否命中该账号历史 `conv_id`（`0:1:a:b`）
    的本号位。本项目曾把「本号 uid」误用 `query/user` 的 uid（与 imapi 会话
    体系可能不同值）→ 订正静默失效，故本探针用 **conv_id 共同项**推断本号。

    ⚠️ 本探针只**读** `.env` 与库，不发起任何探活请求（零风控）。
    因此它判定的是「**落盘凭证的身份是否自洽**」，不是「服务端是否承认」——
    后者需要真实探活（由调用方显式触发），此处不冒充。
    """
    reasons: list[str] = []
    evidence: list[str] = []
    env_uid = ""
    try:
        from auto_dm import accounts as acc
        from dy_apis.login_api import DYLoginApi
        env_path = acc.env_path_of(account)
        auth = DYLoginApi._load_auth_from_env(env_path)
        env_uid = str(auth.get_uid() or "")
        evidence.append(f".env(enc) 落盘 uid={env_uid or '<空>'} ← {env_path}")
    except Exception as e:  # noqa: BLE001
        reasons.append(f"读取凭证失败（无法判定，不冒充 healthy）: {e}")

    ids = [str(r["conv_id"]) for r in
           _db_query("SELECT conv_id FROM dm_conversations WHERE account=?", (account,))]
    my_uid = ""
    try:
        from services.conv_identity import infer_my_uid_from_conv_ids
        my_uid = str(infer_my_uid_from_conv_ids(ids) or "")
    except Exception:
        my_uid = ""
    if my_uid:
        evidence.append(f"库内 conv_id 共同项推断本号 uid={my_uid}（样本 {len(ids)} 条会话）")

    if not ids:
        state, hit = "unknown", None
        reasons.append("库内无会话，缺少可比对的历史 conv_id")
    elif not env_uid or not my_uid:
        state, hit = "unknown", None
        reasons.append("uid 或历史 conv_id 不可得 → 无法判定（不得报 healthy）")
    elif env_uid == my_uid:
        state, hit = "healthy", True
        reasons.append("落盘凭证 uid 与历史 conv_id 一致（无漂移）")
    else:
        state, hit = "failed", False
        reasons.append(f"UID 漂移（AUTH-050）：凭证 uid={env_uid} ≠ 历史本号 uid={my_uid}"
                       f" —— 凭证身份存疑，需重新捕获")

    return {
        "capability": "credential_identity",
        "state": state,
        "coverage": None,
        "confidence": "A",
        "measured_at": _iso(),
        "evidence": evidence,
        "metrics": {"env_uid": env_uid, "history_my_uid": my_uid,
                    "conv_id_samples": len(ids), "uid_match": hit,
                    "note": "只读 .env+库，未发探活请求；判定落盘凭证自洽性"},
        "baseline_delta": None,
        "reasons": reasons,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 注册表与统一入口
# ─────────────────────────────────────────────────────────────────────────────
REGISTRY: dict[str, Callable[[str], dict]] = {
    "conversation_capture": probe_conversation_capture,
    "message_integrity": probe_message_integrity,
    "credential_identity": probe_credential_identity,
}

# 顺序 = 建议关注优先级
CAPABILITY_ORDER = ["conversation_capture", "credential_identity", "message_integrity"]

_STATE_RANK = {"failed": 0, "unknown": 1, "degraded": 2, "healthy": 3}


def list_capabilities() -> list[str]:
    return list(CAPABILITY_ORDER)


def run_probe(capability: str, account: str) -> dict:
    """跑单条探针；异常也返回三态（探针自身绝不抛）。"""
    fn = REGISTRY.get(capability)
    if not fn:
        return {"capability": capability, "account": account,
                "state": "unknown", "coverage": None,
                "confidence": "D", "measured_at": _iso(), "evidence": [],
                "metrics": {}, "baseline_delta": None,
                "reasons": [f"未注册的能力探针：{capability}"]}
    try:
        res = fn(account)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[PROBE-003] " + f"[probe][{capability}][{account}] 探针执行异常: {e}")
        res = {"capability": capability, "state": "unknown", "coverage": None,
               "confidence": "D", "measured_at": _iso(), "evidence": [],
               "metrics": {}, "baseline_delta": None,
               "reasons": [f"探针执行异常：{e}"]}
    if isinstance(res, dict):
        res.setdefault("account", account)
    return res


def list_accounts_for_probe() -> list[str]:
    """探针作用的账号清单：优先库内出现过的账号，其次账号索引。"""
    accts: list[str] = []
    try:
        for r in _db_query("SELECT DISTINCT account FROM dm_conversations "
                           "WHERE account IS NOT NULL AND account<>''"):
            a = str(r["account"])
            if a not in accts:
                accts.append(a)
    except Exception:
        pass
    if accts:
        return accts
    try:
        from auto_dm.accounts import list_accounts
        return [str(a) for a in list_accounts()]
    except Exception:
        return []


def run_probes(accounts: list[str] | None = None,
               capabilities: list[str] | None = None) -> dict:
    """跑一批探针，返回汇总（含总体三态 + 覆盖率口径 + 版本）。"""
    caps = [c for c in (capabilities or CAPABILITY_ORDER) if c in REGISTRY]
    if not caps:
        caps = list(CAPABILITY_ORDER)
    accts = accounts or list_accounts_for_probe()
    results: list[dict] = []
    for a in accts:
        for c in caps:
            results.append(run_probe(c, a))

    worst = "healthy"
    for r in results:
        if _STATE_RANK.get(r.get("state", "unknown"), 1) < _STATE_RANK[worst]:
            worst = r.get("state", "unknown")
    if not results:
        worst = "unknown"

    attention = [f"{r['capability']}@{r.get('account') or ''}"
                 for r in results if r["state"] in ("degraded", "failed")]
    return {
        "ok": True,
        "state": worst,
        "summary": {
            "accounts": accts,
            "capabilities": caps,
            "total": len(results),
            "healthy": sum(1 for r in results if r["state"] == "healthy"),
            "degraded": sum(1 for r in results if r["state"] == "degraded"),
            "failed": sum(1 for r in results if r["state"] == "failed"),
            "unknown": sum(1 for r in results if r["state"] == "unknown"),
            "attention": attention,
        },
        "app_version": app_version(),
        "measured_at": _iso(),
        "results": results,
    }
