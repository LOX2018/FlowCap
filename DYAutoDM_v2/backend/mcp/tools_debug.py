# -*- coding: utf-8 -*-
"""MCP debug 工具族（ADR-010）—— 「到底是哪个环节出了问题？」

## 设计契约

来源：`docs/adr/ADR-010-debug-mcp-scope.md`（拍板：方案 A scope 隔离 + 选项 B 只读+受控触发）

**核心价值**：把「按经验猜排查顺序」变成**依赖序机械定位首个失败环节**。
`工作记忆/03_采集链路总览.md` 自陈：「C4 失效会以 C1/C2/C3 静默失效的形式表现 ——
这正是过去最常见误判的来源」。故 `debug_why` **必须上游优先**。

## 不变式（违反即契约破坏）

- I1：`state=healthy` ⇒ `evidence` 非空（禁止无证据报健康）。
- I2：数据不足 ⇒ `state=unknown`，**不得冒充 healthy**（继承 `services/probe.py`）。
- I3：返回值不得含 cookie / token / `.env` 内容 / 会话正文原文（只给摘要/长度/计数）。
- I4：`confidence`：A=真实实例取证 / B=库+日志双源 / C=单源本地 / D=推断（**D 不单独支撑结论**）。

## 能力边界（保守策略，ADR-010 §5）

T1~T6 **零网络零浏览器**（只读本地 SQLite + 本项目日志），可高频调用。
T7 `debug_trigger_capture` 是**唯一有副作用**的工具，复用既有 refresh 路径
（`ensure_daemons_for(skip_cooldown=True)` + `capture_all`），**不新增 imapi 直发点**；
它天然处于既有 WRITE 双闸之下（`allow_write_actions=false` 默认 ⇒ 物理不可达）。

## 环节枚举 SSOT（D6）

层级与能力名一律从 `services/probe.py` 的 `REGISTRY` / `CAPABILITY_ORDER` /
`PREP_CAPABILITIES` **派生**，本模块**不另抄一份**，避免 nomenclature drift。
"""
from __future__ import annotations

import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from .registry import READ, WRITE, Tool

# ─────────────────────────────────────────────────────────────────────────────
# 派生：依赖层级表（上游优先）
# ─────────────────────────────────────────────────────────────────────────────

# 支撑链必须排在业务链之前：C4（凭证）失效是「C1/C2/C3 静默失效」最常见的误判源。
_SUPPORT_FIRST = ("credential_identity",)

_LAYER_LABEL = {
    "kernel_availability": "内核可用性（前置：不可用则全链必死）",
    "credential_identity": "凭证与身份（C4 支撑链）",
    "conversation_capture": "会话与消息捕获（C1）",
    "message_integrity": "消息数据质量（C1）",
    "send_delivery": "私信投递（C2）",
    "live_danmaku": "直播弹幕监听（C3）",
    "ai_lead_capture": "AI 获客回复（AI）",
}


def _layers() -> list[tuple[str, str]]:
    """[(层级名, 能力名)] —— 恒按依赖序（上游优先）。派生自 probe.py，不硬编码。"""
    from services import probe as P

    prep = [c for c in getattr(P, "PREP_CAPABILITIES", []) or [] if c in P.REGISTRY]
    order = [c for c in getattr(P, "CAPABILITY_ORDER", []) or [] if c in P.REGISTRY]
    support = [c for c in order if c in _SUPPORT_FIRST]
    rest = [c for c in order if c not in _SUPPORT_FIRST]

    out: list[tuple[str, str]] = []
    for c in prep:
        out.append((f"L{len(out)}", c))
    for c in support + rest:
        out.append((f"L{len(out)}", c))
    return out


def _rank(state: str) -> int:
    from services import probe as P
    return P._STATE_RANK.get(state, 1)


# ─────────────────────────────────────────────────────────────────────────────
# 通用：只读数据访问 + 脱敏
# ─────────────────────────────────────────────────────────────────────────────

def _db_query(sql: str, args: tuple = ()) -> list:
    """只读查询，异常返回空（debug 工具绝不因读库失败而抛）。复用 probe 口径。"""
    from services import probe as P
    return P._db_query(sql, args)


def _accounts() -> list[str]:
    from services import probe as P
    return P.list_accounts_for_probe()


def _now() -> float:
    from services import probe as P
    return P._now()


def _iso(ts: float | None = None) -> str:
    from services import probe as P
    return P._iso(ts)


def _app_version() -> str:
    from services import probe as P
    return P.app_version()


def _base(state: str, *, evidence: list[str], confidence: str,
          reasons: list[str], **extra: Any) -> dict[str, Any]:
    """统一返回骨架（ADR-010 §4 共同契约）。"""
    out: dict[str, Any] = {
        "state": state,
        "evidence": list(evidence),
        "confidence": confidence,
        "measured_at": _iso(),
        "reasons": list(reasons),
    }
    out.update(extra)
    # I1：报 healthy 必须带证据，否则降级为 unknown（不得无证据报健康）
    if out["state"] == "healthy" and not out["evidence"]:
        out["state"] = "unknown"
        out["reasons"].append("无证据不得报 healthy（ADR-010 I1）⇒ 降级 unknown")
    return out


def _resolve_account(account: str) -> tuple[str, list[str]]:
    """(实际使用的账号, 说明)。空则自动选唯一账号；多个则返回空 + 说明（不猜）。"""
    a = str(account or "").strip()
    if a:
        return a, []
    accts = _accounts()
    if len(accts) == 1:
        return accts[0], [f"未指定账号，自动选唯一账号：{accts[0]}"]
    if not accts:
        return "", ["无任何已登记账号（库内无会话记录）"]
    return "", [f"存在多个账号（{len(accts)}），请显式指定 account：{accts[:5]}"]


# ─────────────────────────────────────────────────────────────────────────────
# T1：总览
# ─────────────────────────────────────────────────────────────────────────────

# 与 scripts/check_version_sync.py 的 TARGETS 对齐（版本源 SSOT，不另立一份）。
# (标签, 相对源码树的路径, 提取方式)
_VERSION_TARGETS = (
    ("frontend/package.json", "frontend/package.json", "json"),
    ("src-tauri/tauri.conf.json", "src-tauri/tauri.conf.json", "json"),
    ("src-tauri/Cargo.toml", "src-tauri/Cargo.toml", "cargo"),
    ("backend/_build_version.py", "backend/_build_version.py", "py"),
    ("package.json", "package.json", "json"),
    ("src-tauri/Cargo.lock", "src-tauri/Cargo.lock", "cargo_lock"),
)


def _project_root() -> Path | None:
    """源码树根（含 frontend/ 或 src-tauri/）。打包态可能不存在 → None。

    注意：**不用** `probe._app_root()` —— 那是**数据根**（DY_APP_ROOT），
    版本源文件不在那里（活体实测：曾因此恒报"版本源不一致"）。
    """
    try:
        p = Path(__file__).resolve().parents[2]      # backend/mcp/ -> DYAutoDM_v2
        if (p / "package.json").is_file() or (p / "src-tauri").is_dir():
            return p
    except Exception:
        logger.debug("[DBG-001] 源码树根解析失败")
    for env_key in ("DY_SRC_ROOT", "DY_PROJECT_ROOT"):
        v = (os.environ.get(env_key) or "").strip()
        if v and Path(v).is_dir():
            return Path(v)
    return None


def _extract_version(txt: str, kind: str) -> str | None:
    """按类型提取版本串（口径对齐 check_version_sync.py）。"""
    try:
        if kind == "json":
            m = re.search(r'"version"\s*:\s*"([^"]+)"', txt)
            return m.group(1) if m else None
        if kind == "cargo":
            m = re.search(r'^\[package\][\s\S]*?^version\s*=\s*"([^"]+)"', txt, re.M)
            return m.group(1) if m else None
        if kind == "py":
            m = re.search(r'BUILD_VERSION\s*=\s*"([^"]+)"', txt)
            return m.group(1) if m else None
        if kind == "cargo_lock":
            m = re.search(r'name\s*=\s*"dyautodm-v2"\s*\nversion\s*=\s*"([^"]+)"', txt)
            return m.group(1) if m else None
    except Exception:
        logger.debug(f"[DBG-001] 版本提取失败 kind={kind}")
    return None


def _version_sources() -> list[dict[str, Any]]:
    """6 处版本源读回（判齐平）。源码树不存在 → 标注 missing，不抛。"""
    root = _project_root()
    out: list[dict[str, Any]] = []
    for label, rel, kind in _VERSION_TARGETS:
        ver = None
        path = rel
        try:
            if root is not None:
                p = root / rel
                path = str(p)
                if p.is_file():
                    ver = _extract_version(
                        p.read_text(encoding="utf-8", errors="replace"), kind)
        except Exception:
            logger.debug(f"[DBG-001] 版本源读取失败: {rel}")
        out.append({"source": label, "path": path, "version": ver,
                    "present": ver is not None})
    return out


def debug_overview() -> dict[str, Any]:
    """一屏总览：版本 / 账号 / 各链最差状态 / 近 1 小时高频错误码。"""
    ev: list[str] = []
    reasons: list[str] = []

    ver = _app_version()
    ev.append(f"app_version={ver}")

    vs = _version_sources()
    avail = [v for v in vs if v.get("present")]
    present = {v["version"] for v in avail}
    if not avail:
        sync_ok = None          # 打包态/非源码树 ⇒ 无法判定（不冒充齐平，I2）
        reasons.append("未找到源码树版本源（打包态属预期）⇒ 版本齐平无法判定")
    else:
        sync_ok = len(present) == 1 and len(avail) == len(vs)
        if not sync_ok:
            reasons.append(f"版本源不一致/缺失："
                           f"{[(v['source'], v['version'], v.get('present')) for v in vs]}")
    ev.append(f"版本源齐平={sync_ok}（可用 {len(avail)}/{len(vs)}，"
              f"读到 {len(present)} 个不同值）")

    accts = _accounts()
    ev.append(f"账号数={len(accts)}")

    # 各链 worst（复用探针，零网络）
    chain: list[dict[str, Any]] = []
    try:
        from services import probe as P
        for lname, cap in _layers():
            state = "unknown"
            if accts:
                states = [P.run_probe(cap, a).get("state", "unknown") for a in accts]
                state = min(states, key=_rank) if states else "unknown"
            chain.append({"layer": lname, "capability": cap, "state": state})
            if state != "healthy":
                reasons.append(f"{lname} {cap} = {state}")
    except Exception as e:  # noqa: BLE001
        reasons.append(f"探针执行异常：{type(e).__name__}")
    ev.append(f"链路层数={len(chain)}")

    digest = _scan_logs(60, "", "", 5)
    top = (digest or {}).get("top") or []
    ev.append(f"近1h错误码种类={len((digest or {}).get('codes') or {})}")

    state = "healthy"
    if sync_ok is False:
        state = "degraded"
    if any(c["state"] == "failed" for c in chain):
        state = "failed"
    elif any(c["state"] in ("degraded", "unknown") for c in chain) and state != "failed":
        state = "degraded"
    if not accts:
        state = "unknown"
        reasons.append("无账号 ⇒ 无法判定链路（不冒充 healthy）")

    return _base(state, evidence=ev, confidence="B" if accts else "C",
                 reasons=reasons, version=ver, version_sources=vs,
                 version_sync=sync_ok, accounts=accts, chain=chain,
                 top_error_codes=top)


# ─────────────────────────────────────────────────────────────────────────────
# T2：五链逐级状态
# ─────────────────────────────────────────────────────────────────────────────

def debug_chain_status(account: str = "") -> dict[str, Any]:
    """按依赖序返回各环节状态（复用 7 探针；恒 L0→Ln 顺序）。"""
    from services import probe as P

    acct, why = _resolve_account(account)
    if not acct:
        return _base("unknown", evidence=[f"账号未定：{why}"], confidence="C",
                     reasons=why, chain=[])
    ev: list[str] = [f"account={acct}"]
    chain: list[dict[str, Any]] = []
    for lname, cap in _layers():
        r = P.run_probe(cap, acct)
        chain.append({
            "layer": lname, "capability": cap,
            "state": r.get("state", "unknown"),
            "coverage": r.get("coverage"),
            "confidence": r.get("confidence"),
            "evidence": (r.get("evidence") or [])[:3],
            "reasons": (r.get("reasons") or [])[:3],
        })
        ev.append(f"{lname} {cap}={r.get('state')}")

    worst = "healthy"
    for c in chain:
        if _rank(c["state"]) < _rank(worst):
            worst = c["state"]
    bad = [c["capability"] for c in chain if c["state"] != "healthy"]
    reasons = why + ([f"非健康环节：{bad}"] if bad else ["全部环节健康"])
    return _base(worst, evidence=ev, confidence="A", reasons=reasons,
                 account=acct, chain=chain)


# ─────────────────────────────────────────────────────────────────────────────
# T3：★ 核心 —— 上游优先定位首个失败环节
# ─────────────────────────────────────────────────────────────────────────────

def debug_why(account: str = "", symptom: str = "") -> dict[str, Any]:
    """★ 上游优先：返回**依赖序最靠前**的非健康环节（首个失败点）。

    `symptom` 仅作为**线索记录**进入返回，**不参与排序** —— 排序只由依赖序决定，
    这正是为了修正「按症状猜环节」导致的误判（C4 失效伪装成 C1/C2/C3 故障）。
    """
    from services import probe as P

    acct, why = _resolve_account(account)
    if not acct:
        return _base("unknown", evidence=[f"账号未定：{why}"], confidence="C",
                     reasons=why, first_bad=None, layers=[], symptom=symptom or "")

    ev: list[str] = [f"account={acct}"]
    if symptom:
        ev.append(f"symptom(线索，不参与排序)={symptom}")

    layers: list[dict[str, Any]] = []
    upstream_ok: list[str] = []
    first_bad: dict[str, Any] | None = None

    for lname, cap in _layers():
        r = P.run_probe(cap, acct)
        st = r.get("state", "unknown")
        layers.append({"layer": lname, "capability": cap, "state": st,
                       "evidence": (r.get("evidence") or [])[:3],
                       "reasons": (r.get("reasons") or [])[:3]})
        ev.append(f"{lname} {cap}={st}")
        if st == "healthy":
            upstream_ok.append(cap)
            continue
        first_bad = {
            "layer": lname,
            "capability": cap,
            "label": _LAYER_LABEL.get(cap, cap),
            "state": st,
            "upstream_ok": list(upstream_ok),
            "evidence": (r.get("evidence") or [])[:5],
            "reasons": (r.get("reasons") or [])[:5],
            "next_actions": _next_actions(cap, st),
        }
        break  # ← 上游优先：一旦命中即停，不再向下游找

    if first_bad is None:
        reasons = why + [f"依赖序全部健康（{len(layers)} 层）"]
        return _base("healthy", evidence=ev, confidence="A", reasons=reasons,
                     account=acct, symptom=symptom or "", first_bad=None,
                     layers=layers, upstream_ok=upstream_ok)

    reasons = why + [
        f"首个非健康环节 = {first_bad['layer']} {first_bad['label']}"
        f"（上游 {len(upstream_ok)} 层已过；下游未评估 —— 上游优先，避免误判）",
    ]
    conf = "A" if first_bad["state"] in ("failed", "degraded") else "B"
    if first_bad["state"] == "unknown":
        conf = "C"
    return _base(first_bad["state"], evidence=ev, confidence=conf, reasons=reasons,
                 account=acct, symptom=symptom or "", first_bad=first_bad,
                 layers=layers, upstream_ok=upstream_ok)


_NEXT_ACTIONS = {
    "kernel_availability": [
        "核对 Camoufox 内核实物是否存在（缺失即 BCC-070 拦死全部启动路径）",
        "见技能 references/camoufox-kernel-availability.md 的恢复步骤",
    ],
    "credential_identity": [
        "用该账号历史 conv_id 交叉核对探活 uid（0 命中 ⇒ 凭证失效）",
        "陈旧即重新扫码捕获；禁止复用失效凭证直发请求",
    ],
    "conversation_capture": [
        "先确认上游（内核/凭证）已健康 —— 二者不健康时本层失败是**结果**不是原因",
        "再查首包 cmd2043 是否 50B 空壳（uid 轮换特征）",
    ],
    "message_integrity": [
        "查 msg_type 白名单口径与 dirty 行（投递标记/图片占位）",
        "见台账 H-25（库脏数据治理）",
    ],
    "send_delivery": [
        "确认投递验证回执是否落库（role=me + skey）",
        "核对发送闸门与额度，勿以弹窗宣称成功",
    ],
    "live_danmaku": [
        "核对直播间页面结构与 reflow 主线",
        "开播判定走匿名路径（ENG-023）",
    ],
    "ai_lead_capture": [
        "查上下文装配 → 门控链 → 配置接线（先查装配，不先改 prompt）",
    ],
}


def _next_actions(cap: str, state: str) -> list[str]:
    base = list(_NEXT_ACTIONS.get(cap, []))
    if state == "unknown":
        base.insert(0, "数据不足：先触发一次实机链路（debug_trigger_capture）以取得判据")
    return base


# ─────────────────────────────────────────────────────────────────────────────
# T4：日志错误码聚合
# ─────────────────────────────────────────────────────────────────────────────

_RE_CODE = re.compile(r"\[([A-Z]{2,8}-\d{3})\]")
_RE_TS = re.compile(r"(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2}:\d{2})")
_RE_HMS = re.compile(r"\b(\d{2}:\d{2}:\d{2})\b")


def _scan_logs(since_min: int, account: str, code: str, limit: int) -> dict[str, Any] | None:
    """扫本项目运行日志，聚合错误码。目录不存在 → None（区别于「无命中」）。"""
    from services import probe as P

    d = P._logs_dir()
    if not d.is_dir():
        return None
    since = _now() - max(1, int(since_min or 60)) * 60.0

    files: list[Path] = []
    try:
        for p in d.glob("*.log"):
            try:
                if p.stat().st_mtime >= since:
                    files.append(p)
            except OSError:
                continue
    except Exception:
        return None
    files.sort(key=lambda p: p.stat().st_mtime)

    codes: dict[str, int] = {}
    first: dict[str, float] = {}
    last: dict[str, float] = {}
    samples: list[dict[str, Any]] = []
    scanned = 0

    for p in files:
        try:
            file_day = datetime.fromtimestamp(p.stat().st_mtime).date()
        except OSError:
            file_day = None
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    scanned += 1
                    m = _RE_CODE.search(line)
                    if not m:
                        continue
                    c = m.group(1)
                    if code and c != code:
                        continue
                    if account and account not in line:
                        continue
                    ts = None
                    mf = _RE_TS.search(line)
                    if mf:
                        try:
                            ts = datetime.strptime(
                                f"{mf.group(1)} {mf.group(2)}",
                                "%Y-%m-%d %H:%M:%S").timestamp()
                        except ValueError:
                            ts = None
                    if ts is None:
                        mh = _RE_HMS.search(line)
                        if mh and file_day:
                            try:
                                ts = datetime.strptime(
                                    f"{file_day} {mh.group(1)}",
                                    "%Y-%m-%d %H:%M:%S").timestamp()
                            except ValueError:
                                ts = None
                    if ts is not None and ts < since:
                        continue
                    codes[c] = codes.get(c, 0) + 1
                    if ts is not None:
                        if c not in first or ts < first[c]:
                            first[c] = ts
                        if c not in last or ts > last[c]:
                            last[c] = ts
                    if len(samples) < max(1, int(limit or 50)):
                        samples.append({
                            "code": c, "file": p.name, "ts": _iso(ts),
                            # 只给摘要：截断并去掉可能的正文（I3）
                            "line": line.strip()[:180],
                        })
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[DBG-002] 日志扫描失败 {p.name}: {e}")
            continue

    top = sorted(codes.items(), key=lambda kv: (-kv[1], kv[0]))[:10]
    return {
        "files": [p.name for p in files],
        "scanned_lines": scanned,
        "codes": codes,
        "top": [{"code": c, "count": n,
                 "first": _iso(first.get(c)), "last": _iso(last.get(c))}
                for c, n in top],
        "samples": samples,
    }


def debug_log_digest(since_min: int = 60, account: str = "",
                     code: str = "", limit: int = 50) -> dict[str, Any]:
    """聚合近 N 分钟日志里的错误码（频次/首末时间/样本）+ 关联契约摘要。"""
    since_min = max(1, min(int(since_min or 60), 10080))
    account = str(account or "").strip()
    code = str(code or "").strip().upper()
    limit = max(1, min(int(limit or 50), 200))

    digest = _scan_logs(since_min, account, code, limit)
    if digest is None:
        return _base("unknown",
                     evidence=[f"日志目录不存在：{_log_dir_str()}"],
                     confidence="C",
                     reasons=["日志目录不可读 ⇒ 无法判定（不冒充 healthy）"],
                     since_min=since_min, account=account, code=code,
                     top=[], codes={}, samples=[])

    ev = [f"近 {since_min} 分钟扫描 {len(digest['files'])} 个日志文件 / "
          f"{digest['scanned_lines']} 行",
          f"命中错误码 {len(digest['codes'])} 种，共 {sum(digest['codes'].values())} 条"]
    reasons: list[str] = []
    if digest["top"]:
        t = digest["top"][0]
        reasons.append(f"最高频：{t['code']} × {t['count']}（首次 {t['first'] or '未知'}）")
    if not digest["codes"]:
        reasons.append("该窗口内无错误码命中（可能是好事，也可能日志未覆盖该时段）")

    # 关联契约摘要：对 top1 错误码附六段（复用 errcode.lookup，不复制内容）
    contracts = []
    for t in digest["top"][:3]:
        try:
            from errcode import lookup
            r = lookup(t["code"]) or {}
            contracts.append({
                "code": t["code"],
                "domain_name": r.get("domain_name"),
                "design": r.get("design"),
                "deviation": r.get("deviation"),
                "chain": r.get("chain"),
                "root": r.get("root"),
                "verify": r.get("verify"),
            })
        except Exception:
            logger.debug(f"[DBG-003] 契约查询失败 {t['code']}")

    state = "healthy" if not digest["codes"] else "degraded"
    conf = "B" if digest["files"] else "C"
    return _base(state, evidence=ev, confidence=conf, reasons=reasons,
                 since_min=since_min, account=account, code=code,
                 files=digest["files"], scanned_lines=digest["scanned_lines"],
                 codes=digest["codes"], top=digest["top"],
                 samples=digest["samples"], contracts=contracts)


def _log_dir_str() -> str:
    try:
        from services import probe as P
        return str(P._logs_dir())
    except Exception:
        return "<未知>"


# ─────────────────────────────────────────────────────────────────────────────
# T5：错误码六段
# ─────────────────────────────────────────────────────────────────────────────

def debug_explain(code: str) -> dict[str, Any]:
    """查错误码的设计契约（design/contract/deviation/chain/root/verify）。

    口径与既有 `lookup_errcode` 同源（同调 `errcode.lookup`），本工具额外
    标注 state/confidence，保持 debug 族返回契约一致。
    """
    c = str(code or "").strip()
    if not c:
        return _base("unknown", evidence=[], confidence="C",
                     reasons=["code 为必填"], code="")
    try:
        from errcode import lookup
        r = lookup(c) or {}
    except Exception as e:  # noqa: BLE001
        return _base("unknown", evidence=[f"lookup 异常：{type(e).__name__}"],
                     confidence="C", reasons=["查询实现不可用"], code=c)
    if not r:
        return _base("unknown", evidence=[f"未登记的错误码：{c}"], confidence="B",
                     reasons=["错误码未在设计契约库中登记（不等于不存在）"], code=c)
    ev = [f"code={c}", f"来源文件：{r.get('file')}:{r.get('line')}",
          f"是否带契约：{bool(r.get('has_contract'))}"]
    return _base("healthy", evidence=ev, confidence="A",
                 reasons=[f"域={r.get('domain_name') or r.get('domain')}"],
                 code=c, contract=r)


# ─────────────────────────────────────────────────────────────────────────────
# T6：落库数据健康（H-25 量化口径）
# ─────────────────────────────────────────────────────────────────────────────

_NOISE_PATTERNS = (
    ("[投递验证]", "投递验证标记"),
    ("[系统提示]", "系统提示"),
    ("[未知媒体]", "未知媒体"),
    ("[对方已读]", "对方已读"),
    ("[分享视频]", "分享视频"),
)

_WHITELIST = "msg_type IN ('text','7','27') AND TRIM(COALESCE(text,''))<>''"


def debug_data_health(account: str = "") -> dict[str, Any]:
    """落库质量量化：白名单行数 / 噪音行 / base64 图片 / 元数据缺失 / 来源构成。

    口径对齐台账 H-25（张老师库脏数据治理）——同一判据、同一 SQL 口径。
    """
    acct, why = _resolve_account(account)
    if not acct:
        return _base("unknown", evidence=[f"账号未定：{why}"], confidence="C",
                     reasons=why, account="")

    ev: list[str] = [f"account={acct}"]
    reasons: list[str] = []

    def one(sql: str, *a: Any) -> int:
        rows = _db_query(sql, (acct,) + a)
        try:
            v = dict(rows[0]).get("n") if rows else 0
            return int(v or 0)
        except Exception:
            return 0

    total = one("SELECT COUNT(*) AS n FROM dm_messages WHERE account=?")
    wl = one(f"SELECT COUNT(*) AS n FROM dm_messages WHERE account=? AND {_WHITELIST}")
    ev.append(f"总行={total} / AI白名单口径={wl}")

    if total == 0:
        return _base("unknown",
                     evidence=ev + ["该账号在库中无任何消息 ⇒ 无法评估数据质量"],
                     confidence="C", reasons=why + ["无数据（不冒充 healthy）"],
                     account=acct, total=0, whitelist=0)

    noise = 0
    noise_detail: list[dict[str, Any]] = []
    for pat, label in _NOISE_PATTERNS:
        n = one(f"SELECT COUNT(*) AS n FROM dm_messages WHERE account=? "
                f"AND {_WHITELIST} AND text LIKE ?", pat + "%")
        if n:
            noise_detail.append({"pattern": pat, "label": label, "count": n})
        noise += n
    ev.append(f"噪音进白名单={noise} 条")

    b64 = one("SELECT COUNT(*) AS n FROM dm_messages WHERE account=? "
              "AND msg_type='text' AND text LIKE '[图片] data:image%'")
    b64_chars = one("SELECT COALESCE(SUM(LENGTH(text)),0) AS n FROM dm_messages "
                    "WHERE account=? AND msg_type='text' "
                    "AND text LIKE '[图片] data:image%'")
    url_img = one("SELECT COUNT(*) AS n FROM dm_messages WHERE account=? "
                  "AND text LIKE '[图片]%' AND text NOT LIKE '%data:image%'")
    t27 = one("SELECT COUNT(*) AS n FROM dm_messages WHERE account=? AND msg_type='27'")
    ev.append(f"内联base64图片={b64} 条 / {b64_chars} 字符；URL形态={url_img} 条；"
              f"msg_type=27 走描述分支={t27} 条")
    if b64:
        reasons.append(f"🔴 {b64} 条图片以 base64 形式存在于 text 字段，"
                       "而 _build_history 仅对 msg_type='27' 走描述分支 ⇒ "
                       "这些 base64 会**原样进 prompt**")

    wl_chars = one(f"SELECT COALESCE(SUM(LENGTH(text)),0) AS n FROM dm_messages "
                   f"WHERE account=? AND {_WHITELIST}")
    big_chars = one(f"SELECT COALESCE(SUM(LENGTH(text)),0) AS n FROM dm_messages "
                    f"WHERE account=? AND {_WHITELIST} AND LENGTH(text)>=500")
    share = (100.0 * big_chars / wl_chars) if wl_chars else 0.0
    ev.append(f"白名单文本 {wl_chars} 字符，其中长行(>=500) 占 {big_chars} "
              f"（{share:.1f}%）")

    null_id = one("SELECT COUNT(*) AS n FROM dm_messages WHERE account=? "
                  "AND (msg_id IS NULL OR msg_id='')")
    empty_extra = one("SELECT COUNT(*) AS n FROM dm_messages WHERE account=? "
                      "AND (extra IS NULL OR extra='' OR extra='{}')")
    ev.append(f"msg_id 为空={null_id}；extra 为空={empty_extra}")

    dup_groups = one("SELECT COUNT(*) AS n FROM (SELECT msg_id FROM dm_messages "
                     "WHERE account=? AND msg_id IS NOT NULL AND msg_id<>'' "
                     "GROUP BY msg_id HAVING COUNT(*)>1)")
    ev.append(f"重复 msg_id 组数={dup_groups}")

    src_rows = _db_query(
        "SELECT CASE WHEN msg_type='7' THEN 'WS 实时' "
        "WHEN extra LIKE '%skey%' THEN '首包(图片)' "
        "WHEN msg_id LIKE 'local:%' THEN '本地/send 回写' "
        "ELSE '首包/其他 text' END AS src, COUNT(*) AS n "
        f"FROM dm_messages WHERE account=? AND {_WHITELIST} "
        "GROUP BY src ORDER BY n DESC", (acct,))
    sources = [{"source": dict(r).get("src"), "count": int(dict(r).get("n") or 0)}
               for r in src_rows]

    state = "healthy"
    if b64:
        state = "failed"
    elif noise or null_id or empty_extra or url_img:
        state = "degraded"
    if noise:
        reasons.append(f"噪音进白名单 {noise} 条（{noise_detail}）⇒ 会污染 prompt")
    if dup_groups:
        reasons.append(f"重复 msg_id {dup_groups} 组 ⇒ 唯一索引未拦住重复写入")
    if not reasons:
        reasons.append("未发现白名单口径的明显脏数据")

    return _base(state, evidence=ev, confidence="A", reasons=why + reasons,
                 account=acct, total=total, whitelist=wl,
                 whitelist_chars=wl_chars, noise=noise, noise_detail=noise_detail,
                 base64_images=b64, base64_chars=b64_chars,
                 image_url_forms=url_img, msg_type_27=t27,
                 long_row_chars=big_chars, long_row_share=round(share, 1),
                 null_msg_id=null_id, empty_extra=empty_extra,
                 dup_msg_id_groups=dup_groups, sources=sources)


# ─────────────────────────────────────────────────────────────────────────────
# T7：受控实机触发（唯一有副作用）
# ─────────────────────────────────────────────────────────────────────────────

def debug_trigger_capture(account: str) -> dict[str, Any]:
    """【有副作用】触发一次真实「更新会话」捕获。

    复用**既有**路径（`ensure_daemons_for(skip_cooldown=True)` + `capture_all`），
    不新增任何 imapi 直发调用点；拿不到浏览器时**显式失败**（CAP-016 契约）。
    本工具为 WRITE 级 ⇒ 处于既有双闸之下（默认 `allow_write_actions=false` 即不可达）。
    """
    a = str(account or "").strip()
    if not a:
        return _base("unknown", evidence=[], confidence="C",
                     reasons=["account 为必填"], account="")
    ev: list[str] = [f"account={a}"]
    try:
        from auto_dm.daemon_launcher import ensure_daemons_for
        from auto_dm.conversation_capture import capture_all, release_active_lease
    except Exception as e:  # noqa: BLE001
        return _base("failed", evidence=[f"导入失败：{type(e).__name__}"],
                     confidence="C", reasons=["捕获实现不可用"], account=a)

    try:
        launched = ensure_daemons_for(a, skip_cooldown=True)
        if not launched.get("browser"):
            return _base("failed",
                         evidence=ev + [f"BCC 未就绪：{launched.get('msg') or '端口未开'}"],
                         confidence="A",
                         reasons=["浏览器守护未就绪 ⇒ 昵称/头像无法捕获（CAP-016："
                                  "不得静默继续跑出假成功）"],
                         account=a, launched=launched)
        ev.append(f"BCC 就绪={launched.get('browser')}")
        n_conv, n_msg = capture_all(a, with_browser=True)
        ev.append(f"capture_all 完成：n_conv={n_conv} n_msg={n_msg}")
        state = "healthy"
        reasons = [f"真实捕获完成：会话 {n_conv} 个 / 消息 {n_msg} 条"]
        if not n_msg:
            state = "degraded"
            reasons.append("本次未取到新消息（可能确无新消息，也可能上游受限）")
        return _base(state, evidence=ev, confidence="A", reasons=reasons,
                     account=a, n_conv=n_conv, n_msg=n_msg)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[DBG-004] 受控触发捕获失败 {a}: {e}")
        return _base("failed", evidence=ev + [f"异常：{type(e).__name__}: {e}"],
                     confidence="A", reasons=["触发路径抛异常（详见 evidence）"],
                     account=a)
    finally:
        try:
            from auto_dm.conversation_capture import release_active_lease as _rel
            _rel(a)
        except Exception as e:  # noqa: BLE001
            logger.debug(f"[DBG-006] 释放租约失败（由 BCC TTL 回收）: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# 注册
# ─────────────────────────────────────────────────────────────────────────────

DEBUG_TOOLS = (
    Tool(name="debug_overview", level=READ, scopes=("debug",),
         summary="调试总览：版本源齐平 / 账号 / 各链最差状态 / 近1h 高频错误码",
         handler=debug_overview, params={}),
    Tool(name="debug_chain_status", level=READ, scopes=("debug",),
         summary="按依赖序（L0→Ln）返回各环节状态（复用 7 探针，零网络）",
         handler=debug_chain_status,
         params={"account": "账号名，留空=自动选唯一账号"},
         audit_fields=("account",)),
    Tool(name="debug_why", level=READ, scopes=("debug",),
         summary="★上游优先定位【首个】失败环节（避免把 C4 故障误判为 C1/C2/C3）",
         handler=debug_why,
         params={"account": "账号名，留空=自动选唯一账号",
                 "symptom": "观察到的症状（仅作线索记录，不参与排序）"},
         audit_fields=("account",)),
    Tool(name="debug_log_digest", level=READ, scopes=("debug",),
         summary="聚合近 N 分钟日志错误码（频次/首末时间/样本）+ 关联契约摘要",
         handler=debug_log_digest,
         params={"since_min": "回溯分钟数，默认 60",
                 "account": "仅计含该账号名的日志行，留空=不限",
                 "code": "仅看某个错误码，如 BCC-070",
                 "limit": "样本条数上限，默认 50"},
         audit_fields=("account", "code")),
    Tool(name="debug_explain", level=READ, scopes=("debug",),
         summary="查错误码六段（design/contract/deviation/chain/root/verify）",
         handler=debug_explain,
         params={"code": "错误码，如 AUTH-050"},
         audit_fields=("code",)),
    Tool(name="debug_data_health", level=READ, scopes=("debug",),
         summary="落库质量量化（白名单口径/噪音/base64图片/元数据/来源构成）",
         handler=debug_data_health,
         params={"account": "账号名，留空=自动选唯一账号"},
         audit_fields=("account",)),
    Tool(name="debug_trigger_capture", level=WRITE, scopes=("debug",),
         summary="【有副作用】受控触发一次真实「更新会话」捕获（需写闸开启+票据）",
         handler=debug_trigger_capture,
         params={"account": "账号名（必填）"},
         audit_fields=("account",)),
)


def register_debug_all() -> int:
    """注册 debug 工具族（幂等），返回注册数量。"""
    from .registry import register
    for t in DEBUG_TOOLS:
        register(t)
    return len(DEBUG_TOOLS)
