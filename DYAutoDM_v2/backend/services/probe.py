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
        logger.debug(f'[SILENT-00] services.probe: app_version build_vars failed')
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
            logger.debug(f'[SILENT-00] services.probe: app_version package.json read failed')
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
# 时间戳前缀两种形态（2026-09-22 实测补全）：
#   ① 带日期：`2026-09-22 09:18:46.715 | …`（recv_daemon_*.log / browser_daemon_*.log）
#   ② **无日期**：`09:22:12 | INFO | …`（backend 主进程 run 日志 / 控制台格式）
# 只认 ① 会让探针**永远读不到 run 日志**里的捕获事实 —— 而「用户点『更新会话』」
# 这条带浏览器的正路恰好写在 run 日志里 ⇒ 真证据被系统性漏读（实测踩过）。
# ② 的日期由调用方按**文件 mtime** 补齐（见 latest_capture_facts 的 _resolve_ts）。
_RE_TS = re.compile(
    r"^(?:(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?)"
    r"|(?P<hms>\d{2}:\d{2}:\d{2}(?:\.\d+)?))\s*[|]")
_RE_HMS = re.compile(r"^(?P<hms>\d{2}:\d{2}:\d{2}(?:\.\d+)?)\s*[|]")
_RE_FIRSTPACK = re.compile(
    r"\[capture\]\[(?P<acct>[^\]]+)\]\s*首包\s*拉取=(?P<pull>[\d.]+)s\s*解析=(?P<parse>[\d.]+)s"
    r"\s*[（(](?P<bytes>[\d,]+)B\s*->\s*(?P<convs>\d+)\s*会话[）)]"
)
# `[capture][账号] 写库完成：会话 44（含消息 108），昵称命中 uid关联=0 sec_uid关联=0 未命中=44/44 with_browser=0`
#   ↑ 2026-09-21：追加 `with_browser=`。recv_daemon 的 _safe_capture 走
#     with_browser=False（按设计**不抓昵称**，避免抢 profile），其 `uid关联=0`
#     是**预期行为**；探针必须据此区分，否则会把它误判为「昵称链路失效」。
#     旧日志无该字段 → 组为 None（= 未知），按既有口径处理，保持向后兼容。
_RE_WRITE = re.compile(
    r"\[capture\]\[(?P<acct>[^\]]+)\]\s*写库完成：会话\s*(?P<conv>\d+)\s*"
    r"[（(]含消息\s*(?P<msg>\d+)[）)][，,]?\s*昵称命中\s*uid关联=(?P<byuid>\d+)\s*"
    r"sec_uid关联=(?P<bysec>\d+)\s*未命中=(?P<miss>\d+)/(?P<total>\d+)"
    r"(?:\s*with_browser=(?P<wb>[01]))?"
)


def _logs_dir() -> Path:
    return Path(_app_root()) / "logs"


def _parse_ts(s: str) -> float | None:
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).timestamp()
        except Exception:
            logger.debug(f'[SILENT-00] services.probe: _parse_ts strptime failed')
            continue
    return None


def _line_ts(line: str, m: "re.Match | None", file_day) -> float | None:
    """从 `_RE_TS.match(line)` 的结果里取时间戳（带日期 or 无日期两种形态）。

    无日期形态（`09:22:12 | …`）用 **file_day**（文件 mtime 的日期）补齐 ——
    run_*.log 就是这种格式，只认带日期的会让探针系统性漏读那条正路证据。
    """
    if not m:
        return None
    ts = _parse_ts(m.group("ts"))
    if ts is None and m.groupdict().get("hms") and file_day:
        ts = _parse_ts(f"{file_day} {m.group('hms')}")
    return ts


def _file_day_of(p: Path):
    try:
        return datetime.fromtimestamp(p.stat().st_mtime).date()
    except Exception:  # noqa: BLE001
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
                logger.debug(f'[SILENT-00] services.probe: log_candidates per-file stat failed')
                continue
            if mt >= since_ts:
                out.append((mt, p))
    except Exception:
        return []
    out.sort()
    return [p for _, p in out]


def latest_capture_facts(account: str, lookback_sec: float = 86400.0,
                         with_browser: int | None = None) -> dict:
    """从运行日志里提取该账号**最新**一条捕获事实（首包 + 写库完成）。

    :param with_browser: 仅取指定路径的写库事实（1=带浏览器 / 0=不带 /
        None=不限）。用于把「按设计不抓昵称」的捕获与「昵称能力」证据分开 ——
        前者 uid关联=0 属预期，不能用来否定后者（见 probe_conversation_capture）。

    返回 {ts, convs_parsed, n_conv, n_msg, by_uid, by_sec, miss, total,
          with_browser, firstpack_bytes, source}
    找不到 → 空 dict（调用方据此报 failed/unknown，**不得**用 0 冒充）。
    """
    since = _now() - max(60.0, float(lookback_sec))
    facts: dict[str, Any] = {}
    for p in _log_candidates(since):
        _file_day = _file_day_of(p)
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    m = _RE_TS.match(line)
                    if not m:
                        continue
                    ts = _line_ts(line, m, _file_day)
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
                        _wb = m2.groupdict().get("wb")
                        _wbv = None if _wb is None else int(_wb)
                        _new = {
                            "ts": ts,
                            "n_conv": int(m2.group("conv")),
                            "n_msg": int(m2.group("msg")),
                            "by_uid": int(m2.group("byuid")),
                            "by_sec": int(m2.group("bysec")),
                            "miss": int(m2.group("miss")),
                            "total": int(m2.group("total")),
                            "with_browser": _wbv,
                            "source": str(p),
                        }
                        # 时间戳比较而非文件顺序覆盖：优先取**真正最新**的一条，
                        # 「最近一次」才是链路当前状态的证据（同一账号多路径写日志）。
                        # with_browser 过滤：只收指定路径的事实（None=不限）。
                        if with_browser is not None and _wbv != with_browser:
                            continue
                        # 不限路径时按时间取最新；同时间戳优先带浏览器的
                        # （带浏览器证据包含昵称能力信息，信息量更大）。
                        if facts.get("ts") is None or ts > facts["ts"] or (
                                ts == facts.get("ts") and _wbv == 1
                                and facts.get("with_browser") != 1):
                            facts.update(_new)
        except Exception:  # noqa: BLE001
            logger.debug(f'[SILENT-00] services.probe: latest_capture_facts log parse failed')
            continue
    return facts


def latest_capture_facts_filtered(account: str, with_browser: int = 1,
                                  lookback_sec: float = 86400.0) -> dict:
    """只取带（或不带）浏览器路径的写库事实（薄封装，语义更明确）。"""
    return latest_capture_facts(account, lookback_sec=lookback_sec,
                                with_browser=with_browser)


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
        logger.debug(f'[SILENT-00] services.probe: _kv_set store failed')
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
        #
        # ⚠️ 2026-09-21 二次修正（**假失效**，与上面的假健康互为镜像）：
        #    `with_browser=0` 的捕获（recv_daemon._safe_capture 的启动前移捕获）
        #    **按设计不抓昵称**（避免抢 profile），其 uid关联=0 是**预期**，不是失效。
        #    实测：探针读了这条日志 → 报 failed；而手动跑 with_browser=1 得
        #    `uid关联=44 未命中=0/44`（链路完全正常）。判据必须按 with_browser 分流：
        #      · with_browser=1 → 参与昵称关联率判定（唯一有效证据）
        #      · with_browser=0 → 仅用于「新鲜度」判定，**若它比带浏览器那次更新，
        #        不得用它否定昵称能力**（保留带浏览器证据的 fresh_ratio）
        _wb = facts.get("with_browser")
        _fresh_facts = facts
        _nick_evidence = bool(_has_write)
        if _wb == 0:
            # 取最近一条**带浏览器**的写库事实（可能更早，但那是昵称能力的真证据）
            _f2 = latest_capture_facts_filtered(account, with_browser=1,
                                                lookback_sec=max(stale_sec * 4, 86400))
            # 必须要求**真有写库完成**（n_conv 非空）；只有首包的 dict 也是 truthy，
            # 但那种没有昵称证据（实测踩过：仅凭 truthy 判断 → 误当作有证据 → 假健康）。
            if _f2.get("n_conv") is not None:
                _fresh_facts = _f2
            else:
                # 窗口内**只有**「不抓昵称」的捕获 → 昵称能力本次未被验证，
                # 既不能报 healthy，也不能凭它报 failed（那是假失效）。
                _nick_evidence = False
        if _fresh_facts.get("n_conv") is not None:
            _a_total = _fresh_facts.get("total") or 0
            _a_hit = (_fresh_facts.get("by_uid") or 0) + (_fresh_facts.get("by_sec") or 0)
            fresh_ratio = round(_a_hit / _a_total, 4) if _a_total else None
        else:
            fresh_ratio = None
        # 有效关联率 = min(库内历史, 最近一次)；任一失守都不得判健康
        _cands = [x for x in (nick_ratio, fresh_ratio) if x is not None]
        eff_ratio = min(_cands) if _cands else None
        evidence.append(
            f"最近一次捕获自身关联率="
            + (f"{fresh_ratio}（uid关联={_fresh_facts.get('by_uid')} "
               f"sec_uid关联={_fresh_facts.get('by_sec')} / 共{_fresh_facts.get('total')}）"
               if fresh_ratio is not None else "（本次未见写库完成）")
            + f"；库内历史比例={nick_ratio} → 有效判定取 min={eff_ratio}")
        if _wb == 0:
            evidence.append(
                "⚠ 最近一次写库为 `with_browser=0`（recv_daemon 启动前移捕获，"
                "按设计**不抓昵称**）—— 其 uid关联=0 属预期，"
                + ("已改用最近一次**带浏览器**捕获作昵称能力证据。"
                   if _nick_evidence else
                   "窗口内无带浏览器捕获作证 → 昵称能力**本次未定论**。"))
        evidence.append(f"SQLite 落库：会话 {total}，真实昵称 {named}，头像 {avatar}，"
                        f"消息 {m_total}（带 msg_id {m_withid}）")
        evidence.append(f"库文件：{db_evidence()}")

        if not _has_write:
            # 有首包、无写库完成：链路进行到一半 —— 既不能判健康，也不能判能力失效
            state = "unknown"
            reasons.append("日志只到首包、未见「写库完成」——本次捕获未定论"
                           "（长会话补全可能仍在进行，或中途中断）")
        elif not _nick_evidence:
            # 最近一次是「不抓昵称」的路径且窗口内无带浏览器证据：
            # 昵称能力未被验证 —— 不得报 failed（假失效），也不得报 healthy。
            state = "unknown"
            reasons.append(
                "最近一次捕获为 `with_browser=0`（按设计不抓昵称）且窗口内无"
                "带浏览器捕获 → 昵称关联能力本次未定论（不得据此判失效，"
                "也不得凭库内历史判健康）")
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
# 探针 4：send_delivery（真实投递 / 幂等性）
# ─────────────────────────────────────────────────────────────────────────────
def probe_send_delivery(account: str) -> dict:
    """发送投递探针。

    设计契约（`02_效果定义与探针.md` §2.2）：**禁止用「接口返回 200」「UI 弹窗」当投递判据**
    （曾发生「回 OK 但从未投递」）。本探针只认三类**本地硬证据**：
      ① `role='me'` 落库行；② 带 `skey` 的实发要素（图片/语音等）；
      ③ 主动写入的 `[投递验证]` 文本（本项目实测投递用的显式标记）。
    并按唯一索引口径查 (conv_id,msg_id) 重复行（幂等性）。

    ⚠️ 诚实边界：`role='me'` 行只证明「本地落库」，**不等于服务端确认投递**。
    故无校验证据时最多报 degraded，不得报 healthy。
    """
    reasons: list[str] = []
    evidence: list[str] = []
    rows = _db_query(
        "SELECT COUNT(*) AS n, "
        "SUM(CASE WHEN extra LIKE '%skey%' THEN 1 ELSE 0 END) AS sk, "
        "SUM(CASE WHEN text LIKE '%投递验证%' THEN 1 ELSE 0 END) AS ver "
        "FROM dm_messages WHERE account=? AND role='me'", (account,))
    n = int(rows[0]["n"] or 0) if rows else 0
    sk = int(rows[0]["sk"] or 0) if rows else 0
    ver = int(rows[0]["ver"] or 0) if rows else 0

    dup = _db_query(
        "SELECT COUNT(*) AS n FROM (SELECT conv_id, msg_id FROM dm_messages "
        "WHERE account=? AND msg_id IS NOT NULL AND msg_id<>'' "
        "GROUP BY conv_id, msg_id HAVING COUNT(*)>1)", (account,))
    dup_n = int(dup[0]["n"] or 0) if dup else 0

    if n == 0:
        state = "unknown"
        reasons.append("库内无 role='me' 记录（该账号从未发送或数据被清）→ 无法判定")
    elif dup_n > 0:
        state = "failed"
        reasons.append(f"存在 {dup_n} 组 (conv_id,msg_id) 重复发送行 —— 幂等性失守")
    elif ver == 0:
        # 只有落库、无「投递验证」直接证据 —— 不得报 healthy（契约：不采信中间步骤）
        state = "degraded"
        reasons.append(f"有 {n} 条 role='me' 落库，但**无投递验证直接证据**"
                       f"（`[投递验证]` 标记 {ver} 条）"
                       f"→ 只证明「本地落库」，不能证明服务端已投递；"
                       f"skey {sk} 条只是「实发要素」，不作为投递证据")
    else:
        state = "healthy"
        reasons.append(f"发送 {n} 条，含投递验证直接证据 {ver} 条，且无重复行")
    if n:
        evidence.append(f"role='me' 落库 {n} 条；`[投递验证]` {ver} 条；带 skey {sk} 条")
        evidence.append(f"(conv_id,msg_id) 重复组 {dup_n}；库文件 {db_evidence()}")
        evidence.append("判据口径：仅以 DB 落库 + 显式投递验证标记为准，"
                        "**不采信**接口 200 / UI 弹窗 / skey 存在性")

    snapshot = {"score": round(ver / n, 4) if n else 0.0}
    base = _baseline_compare("send_delivery", account, snapshot)
    if state == "healthy" and not evidence:
        state = "unknown"
        reasons.append("无证据不得报 healthy")
    return {
        "capability": "send_delivery",
        "state": state,
        "coverage": None,
        "coverage_note": "真实投递率需「对端收到」的独立证据；本探针以落库+校验标记近似",
        "confidence": "A" if (ver or sk) else "D",
        "measured_at": _iso(),
        "evidence": evidence,
        "metrics": {"sent_db_rows": n, "with_skey": sk, "delivery_verified": ver,
                    "dup_groups": dup_n},
        "baseline_delta": base,
        "reasons": reasons,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 探针 5：live_danmaku（直播弹幕 / 昵称解密权）
# ─────────────────────────────────────────────────────────────────────────────
_RE_DANMAKU = re.compile(r"\[弹幕\]\s*(?P<nick>.+?)\(uid=(?P<uid>\d+)\s+sec_uid=(?P<sec>[^)]*)\)")
# 弹幕行**行内账号字段**（ADR-002 §5.1 前置，v0.44.38 起）：`[弹幕][账号=X] 昵称(uid=… sec_uid=…)`
# —— 兼容「匿名」等任意非 `]` 值。
_RE_DANMAKU_ACCT = re.compile(r"\[弹幕\]\[账号=(?P<acct>[^\]]*)\]")
# 弹幕行的**账号归因**标记（**历史日志回落路径**）：直播引擎每次启动都写一行
# 「使用前端指定账号「X」作为监测账号」，且实测恒**先于**该会话的首条弹幕（6/6 文件）。
# 同一文件可先后出现多个监测账号（实测：一个会话里先起张老师、1 分钟后改起尚进）。
#
# ⚠️ 该回落路径**在真并发下不可靠**（ADR-002 多账号同时监听时两实例交错写同一 logs/，
# 实测形态 `A标记 → B标记 → A弹幕` ⇒ 「最近一条」会把 A 的弹幕记到 B）。故：
#   ① 新日志一律带行内字段（唯一可靠归因，见 core/live_hook.py）；
#   ② 本标记仅用于**读旧日志**；若某文件既有行内字段又有弹幕行，
#      一律以行内字段为准（见下方解析顺序）。
_RE_MONITOR = re.compile(r"使用前端指定账号「(?P<acct>[^」]+)」作为监测账号")


def probe_live_danmaku(account: str) -> dict:
    """直播弹幕探针。

    判据（**ENG-020 卡校正**）：脱敏 ⇔ `uid == 111111` **且** `sec_uid` 为空。
    ⚠️ **不可**用 `desensitized_nickname` / `is_anonymous` / `mystery_man` 当判据
    （实测三组均为非空且等于各自昵称）。

    数据源：本项目运行日志的 `[弹幕] 昵称(uid=… sec_uid=…)` 行（近 N 小时窗口）。

    ⚠️ **账号归因（2026-09-22 修复）**：`[弹幕]` 行**不含账号字段**，而日志是按
    **运行会话**落在文件里、多账号共写同一 `logs/` 目录。修复前本探针把**全部**含弹幕
    的日志无差别计入**每个**被查账号 ⇒ 两个账号返回**完全相同**的条数与比例
    （实测各 11 条 / 同一文件 `run_20260921_170980.log`）。现按「最近一条
    `使用前端指定账号「X」作为监测账号`」标记归因，只统计属于本账号的弹幕；
    窗口内**无标记**的行计入 `unattributed` 并如实上报（不猜给谁）。
    """
    window_h = _cfg_int("capture", "probe_live_window_hours", 24)
    th = _cfg_float("capture", "probe_live_healthy", 0.95)
    th_d = _cfg_float("capture", "probe_live_degraded", 0.50)
    since = _now() - max(1, window_h) * 3600

    total = desens = unattributed = 0
    samples: list[str] = []
    src = None
    for p in _log_candidates(since):
        _fd = _file_day_of(p)
        cur_monitor: "str | None" = None
        try:
            for line in open(p, "r", encoding="utf-8", errors="replace"):
                # 归因标记先于时间窗判定：标记可能早于窗口起点，但同一会话的
                # 弹幕在窗内 —— 若先按 ts 过滤就会丢掉归因。
                mm = _RE_MONITOR.search(line)
                if mm:
                    cur_monitor = mm.group("acct")
                    continue
                m = _RE_TS.match(line)
                if not m:
                    continue
                ts = _line_ts(line, m, _fd)
                if ts is None or ts < since:
                    continue
                d = _RE_DANMAKU.search(line)
                if not d:
                    continue
                # 归因优先级（ADR-002 §5.1）：
                #   ① 行内账号字段 —— 唯一在**真并发**下可靠的归因（本行自证）；
                #   ② 无行内字段时回落「最近一条监测账号标记」—— 只对**历史日志**
                #      有效；并发下会误判，故仅作兼容，不用于新日志。
                da = _RE_DANMAKU_ACCT.search(line)
                line_acct = da.group("acct") if da else None
                owner = line_acct if line_acct is not None else cur_monitor
                if owner is None:
                    unattributed += 1
                    continue
                if owner != account:
                    continue
                total += 1
                is_des = (d.group("uid") == "111111" and not d.group("sec").strip())
                if is_des:
                    desens += 1
                if len(samples) < 3:
                    samples.append(f"uid={d.group('uid')} sec_uid={'空' if not d.group('sec').strip() else '有'}")
                src = p
        except Exception:
            logger.debug(f'[SILENT-00] services.probe: nickname_probe log parse failed')
            continue

    reasons: list[str] = []
    evidence: list[str] = []
    if total == 0:
        ev = [f"近 {window_h}h 运行日志内**无属于本账号**的 `[弹幕]` 记录"
              f"（窗口内本账号未监听直播）"]
        if unattributed:
            ev.append(f"另有 {unattributed} 条弹幕行**无法归因**"
                      f"（前文无「作为监测账号」标记），已排除、不猜给本账号")
        return {
            "capability": "live_danmaku", "state": "unknown", "coverage": None,
            "confidence": "D", "measured_at": _iso(),
            "evidence": ev,
            "metrics": {"window_hours": window_h, "danmaku_lines": 0,
                        "unattributed_lines": unattributed},
            "baseline_delta": None,
            "reasons": [f"近 {window_h}h 内本账号未监听直播 → 无法判定（不得报 healthy）"],
        }
    ratio = round((total - desens) / total, 4)
    evidence.append(f"近 {window_h}h 本账号({account})弹幕 {total} 条，其中脱敏 {desens} 条 "
                    f"（真实率 {(total-desens)/total:.2%}）← {Path(str(src)).name}")
    evidence.append(f"判据：脱敏 ⇔ uid==111111 且 sec_uid 空；样例 {samples}")
    if unattributed:
        evidence.append(f"另有 {unattributed} 条弹幕行无法归因"
                        f"（前文无「作为监测账号」标记），已排除")
    if ratio >= th:
        state = "healthy"
        reasons.append(f"弹幕真实昵称率 {ratio} ≥ {th}（有解密权）")
    elif ratio >= th_d:
        state = "degraded"
        reasons.append(f"弹幕真实昵称率 {ratio} 介于 [{th_d},{th}) —— 部分脱敏")
    else:
        state = "failed"
        reasons.append(f"弹幕真实昵称率 {ratio} < {th_d} —— 昵称被脱敏（无解密权/凭证降权）")
    base = _baseline_compare("live_danmaku", account, {"score": ratio})
    return {
        "capability": "live_danmaku", "state": state, "coverage": ratio,
        "confidence": "A", "measured_at": _iso(), "evidence": evidence,
        "metrics": {"window_hours": window_h, "danmaku_lines": total,
                    "desensitized": desens, "real_ratio": ratio,
                    "unattributed_lines": unattributed},
        "baseline_delta": base, "reasons": reasons,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 探针 6：ai_lead_capture（AI 回复 / 留资捕获）
# ─────────────────────────────────────────────────────────────────────────────
def probe_ai_lead_capture(account: str) -> dict:
    """AI 获客域探针：留资捕获有效性。

    判据：`ai_leads` 表里该账号的留资条目数，以及**联系方式非空率**
    （`contact_value` 为空 = 捕获到线索但没抓到可用联系方式 = 无效留资）。
    另查近窗口 AI 回复活动（日志），无活动则只报留资现状、不报能力健康。
    """
    window_h = _cfg_int("capture", "probe_ai_window_hours", 72)
    rows = _db_query(
        "SELECT COUNT(*) AS n, "
        "SUM(CASE WHEN contact_value IS NOT NULL AND contact_value<>'' THEN 1 ELSE 0 END) AS ok, "
        "SUM(CASE WHEN status='new' THEN 1 ELSE 0 END) AS newn "
        "FROM ai_leads WHERE account=?", (account,))
    n = int(rows[0]["n"] or 0) if rows else 0
    ok = int(rows[0]["ok"] or 0) if rows else 0
    newn = int(rows[0]["newn"] or 0) if rows else 0
    ratio = round(ok / n, 4) if n else None

    # AI 回复活动（日志）
    since = _now() - max(1, window_h) * 3600
    ai_lines = 0
    for p in _log_candidates(since):
        try:
            for line in open(p, "r", encoding="utf-8", errors="replace"):
                m = _RE_TS.match(line)
                if m:
                    ts = _parse_ts(m.group("ts"))
                    if ts and ts >= since and ("AI 回复" in line or "ai_reply" in line
                                               or "留资" in line or "决策链" in line):
                        ai_lines += 1
        except Exception:
            logger.debug(f'[SILENT-00] services.probe: ai_reply_probe log parse failed')
            continue

    reasons: list[str] = []
    evidence: list[str] = []
    if n == 0:
        state = "unknown"
        reasons.append("库内无留资记录 → 无法判定（可能从未触发，非缺陷）")
        evidence.append(f"近 {window_h}h AI 相关日志行 {ai_lines} 条")
    else:
        evidence.append(f"留资 {n} 条（联系方式有效 {ok}，待处理 {newn}）← ai_leads 表")
        evidence.append(f"近 {window_h}h AI 相关日志行 {ai_lines} 条；库文件 {db_evidence()}")
        if ratio is not None and ratio >= 0.9 and ai_lines > 0:
            state = "healthy"
            reasons.append(f"留资 {n} 条，联系方式有效率 {ratio} ≥ 0.9，"
                           f"且窗口内有 AI 活动（{ai_lines} 行）")
        elif ratio is not None and ratio >= 0.9:
            # 留资数据在，但窗口内无 AI 活动 —— 无法确认「当前」能力（诚实：unknown）
            state = "unknown"
            reasons.append(f"留资 {n} 条、有效率 {ratio}，但近 {window_h}h **无 AI 活动**"
                           f"→ 只能证明历史捕获过，无法确认当前能力（不得报 healthy）")
        elif ratio is not None and ratio >= 0.5:
            state = "degraded"
            reasons.append(f"联系方式有效率 {ratio} 介于 [0.5,0.9) —— 部分线索不可用")
        else:
            state = "failed"
            reasons.append(f"联系方式有效率 {ratio} < 0.5 —— 留资捕获基本无效")

    snapshot = {"score": ratio if ratio is not None else 0.0}
    base = _baseline_compare("ai_lead_capture", account, snapshot)
    if state == "healthy" and not evidence:
        state = "unknown"
        reasons.append("无证据不得报 healthy")
    return {
        "capability": "ai_lead_capture", "state": state, "coverage": ratio,
        "confidence": "A", "measured_at": _iso(), "evidence": evidence,
        "metrics": {"leads": n, "leads_with_contact": ok, "contact_ratio": ratio,
                    "leads_new": newn, "ai_log_lines": ai_lines,
                    "window_hours": window_h},
        "baseline_delta": base, "reasons": reasons,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 注册表与统一入口
# ─────────────────────────────────────────────────────────────────────────────
REGISTRY: dict[str, Callable[[str], dict]] = {
    "conversation_capture": probe_conversation_capture,
    "message_integrity": probe_message_integrity,
    "credential_identity": probe_credential_identity,
    "send_delivery": probe_send_delivery,
    "live_danmaku": probe_live_danmaku,
    "ai_lead_capture": probe_ai_lead_capture,
}

# 顺序 = 建议关注优先级（对齐 02_效果定义与探针.md §2 的五大业务域）
CAPABILITY_ORDER = ["conversation_capture", "send_delivery", "credential_identity",
                    "live_danmaku", "ai_lead_capture", "message_integrity"]

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
        logger.debug(f'[SILENT-00] services.probe: target_accounts db query failed')
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


# ═══════════════════════════════════════════════════════════════════════════
# 定时巡检（M1 的「发现『悄悄坏了』」那一环）
#
# 设计契约（02_效果定义与探针.md §3.3 接入点）：
#   ① 探针必须能**定期跑**，否则只能在用户点击时才发现失效（本项目历史失效都是
#      用户先发现的）。② 巡检**只读本地事实**（零网络零浏览器），故可安全高频。
#   ③ 状态与结果持久化到 kv（进程重启不丢），供 `/api/probe/status` 与前端读取。
#
# 为什么独立成模块而非塞进 kb_maintain：kb_maintain 是「知识库学习/陈旧扫描」
# 的 84h 周期任务，语义不同；探针巡检需要更短的周期（默认 15 分钟）。
# 但**调度原语与 kb_maintain 保持一致**（threading.Timer 循环 + kv 状态 + 幂等 start），
# 避免新增第二套调度机制（铁律：禁止新增第 N+1 个决策点）。
# ═══════════════════════════════════════════════════════════════════════════

PATROL_KV_KEY = "capability_probe:patrol:state"
PATROL_DEFAULT_INTERVAL_MIN = 15


def _patrol_enabled() -> bool:
    """是否启用定时巡检（默认启用；探针零风控，可安全常驻）。"""
    v = _cfg("capture", "probe_patrol_enabled", None)
    if v is None:
        return True
    return bool(v)


def _patrol_interval_sec() -> float:
    mins = _cfg_int("capture", "probe_patrol_interval_min", PATROL_DEFAULT_INTERVAL_MIN)
    return max(60.0, float(mins) * 60.0)


def _patrol_first_delay() -> float:
    sec = _cfg_int("capture", "probe_patrol_first_delay_sec", 120)
    return max(30.0, float(sec))


def patrol_state() -> dict:
    """读巡检状态（供 API / 前端；含是否启用、上次结果、下次时间）。"""
    st = _kv_get(PATROL_KV_KEY) or {}
    if not isinstance(st, dict):
        st = {}
    st.setdefault("enabled", _patrol_enabled())
    st.setdefault("interval_min", int(_patrol_interval_sec() // 60))
    st.setdefault("runs", 0)
    st.setdefault("errors", [])
    st["next_run_at"] = getattr(_PATROL_TIMER, "next_at", None) or st.get("next_run_at")
    return st


def run_patrol_once() -> dict:
    """跑一轮巡检并落盘状态（幂等；异常不抛出）。返回本轮汇总。"""
    import traceback as _tb
    t0 = _now()
    summary: dict = {}
    err = ""
    try:
            res = run_probes()
            summary = res.get("summary") or {}

            # ── D4: 契约漂移自动化检测（check_contracts.py 静默巡检） ──
            contract_ok = True
            contract_detail = ""
            try:
                import subprocess as _sp, sys as _sys
                _cc = _sp.run(
                    [_sys.executable, "-m", "scripts.check_contracts", "--quiet"],
                    capture_output=True, text=True, timeout=60,
                    cwd=str(Path(__file__).resolve().parent.parent.parent / "scripts"),
                )
                contract_ok = _cc.returncode == 0
                contract_detail = _cc.stdout.strip()[:200] if not contract_ok else "all pass"
            except Exception as _ce:
                contract_detail = f"check_contracts exec fail: {_ce}"

            # ── 报告 ──
            result_detail = {
                "at": _iso(t0),
                "ts": t0,
                "state": res.get("state"),
                "elapsed": round(_now() - t0, 2),
                "summary": summary,
                "attention": summary.get("attention") or [],
                "app_version": res.get("app_version"),
                "contract_check": {"ok": contract_ok, "detail": contract_detail},
            }
            if not contract_ok:
                result_detail["attention"] = list(result_detail["attention"]) + [f"契约漂移检测告警: {contract_detail}"]
            result = result_detail
    except Exception as e:  # noqa: BLE001
        err = f"{e}"
        result = {"at": _iso(t0), "ts": t0, "state": "unknown",
                  "error": err, "traceback": _tb.format_exc(limit=3)}
    st = _kv_get(PATROL_KV_KEY) or {}
    if not isinstance(st, dict):
        st = {}
    st["runs"] = int(st.get("runs") or 0) + 1
    st["last_run_at"] = result["at"]
    st["last_result"] = result
    if err:
        errs = list(st.get("errors") or [])
        errs.append({"at": result["at"], "error": err})
        st["errors"] = errs[-20:]
    _kv_set(PATROL_KV_KEY, st)
    if err:
        logger.warning(f"[PROBE-006] " + f"[probe] 定时巡检异常: {err}")
    else:
        logger.info(
            f"[probe] 定时巡检完成 state={result.get('state')} "
            f"healthy={summary.get('healthy')} degraded={summary.get('degraded')} "
            f"failed={summary.get('failed')} unknown={summary.get('unknown')}"
            + (f"；需关注 {result.get('attention')}" if result.get("attention") else ""))
    return result


class _PatrolTimer:
    """可重启的守护定时器（幂等 start；stop 后不再排下一轮）。"""

    def __init__(self) -> None:
        self._timer = None
        self.next_at = None
        self.started = False

    def _arm(self, delay: float) -> None:
        import threading
        if self._timer is not None:
            try:
                self._timer.cancel()
            except Exception:
                logger.debug(f'[SILENT-00] services.probe: _arm timer cancel failed')
                pass
        self._timer = threading.Timer(delay, self._tick)
        self._timer.daemon = True
        self._timer.start()
        self.next_at = _now() + delay

    def _tick(self) -> None:
        try:
            run_patrol_once()
        finally:
            if self.started and _patrol_enabled():
                self._arm(_patrol_interval_sec())

    def start(self, first_delay: float | None = None,
              interval: float | None = None) -> dict:
        if self.started:
            return {"ok": True, "already": True, "state": patrol_state()}
        if not _patrol_enabled():
            return {"ok": False, "reason": "disabled", "state": patrol_state()}
        self.started = True
        self._arm(first_delay if first_delay is not None else _patrol_first_delay())
        return {"ok": True, "already": False, "interval_sec": _patrol_interval_sec(),
                "next_at": self.next_at, "state": patrol_state()}

    def stop(self) -> dict:
        self.started = False
        if self._timer is not None:
            try:
                self._timer.cancel()
            except Exception:
                logger.debug(f'[SILENT-00] services.probe: stop timer cancel failed')
                pass
        self.next_at = None
        return {"ok": True, "stopped": True}


_PATROL_TIMER = _PatrolTimer()


def start_patrol(first_delay: float | None = None,
                 interval: float | None = None) -> dict:
    """启动巡检定时器（幂等）。"""
    return _PATROL_TIMER.start(first_delay=first_delay, interval=interval)


def stop_patrol() -> dict:
    return _PATROL_TIMER.stop()
