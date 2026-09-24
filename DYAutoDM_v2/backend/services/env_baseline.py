"""账号环境基线 —— 出口环境一致性校验（v0.43.84 补缺口）

## 设计契约（2026-09-18，补知识库 02 §七「check_egress_ip 未挂载」缺口）

**design**：同一账号的出口环境（出口 IP + 代理模式）必须与「登录时」恒等
（知识库 9.30 铁律：登录环境 == 运行环境）。登录成功时刻 = 基线确立时刻；
运行期低频比对，漂移即告警——让环境跳变在「被强制下线」之前暴露。

**contract**：
1. 基线只在【凭证真判据通过】时记录（scan_login ok=True / 观测态回写 ok=True），
   绝不在凭证存疑时写入（否则把失效环境当基线）。
2. 比对是**低频后台行为**（默认 30 分钟一次），走 probe_egress_ip_direct
   （urllib 按代理模式直连，零浏览器副作用、不碰 chat 页、不抢租约）。
3. **只告警，绝不阻断、绝不重启浏览器**（改环境本身极强风控信号，
   修复方式是「用户重扫/改配置」，不是代码自作主张）。
4. 改代理配置（save_proxy）必须清基线——下次扫码重建，防误报。
5. 校验失败（探测接口超时等）≠ 漂移：探测失败只 debug，绝不告警。

**为什么挂 scan_login / refresh_cookie_to_env 而不是浏览器启动**：
- 每次启动多一次外网请求 = 额外风控暴露（知识库 02 §七原判据，沿用）；
- 环境基线的语义就是「登录时环境」，只有登录成功时刻记录才正确。

kv 键：`env_baseline_<account>` →
    {"ip": str, "mode": str, "country": str, "recorded_at": float, "source": str}
"""

from __future__ import annotations

import time
from typing import Any

from services.kv_store import kv_get, kv_set

# 比对节流：两次比对至少间隔该秒数（keepalive 每 300s 跑一轮，
# 每轮都探测 = 每 5 分钟一次外网请求，太频；30min 与环境跳变的
# 发现窗口平衡，且探测失败静默，不构成规律性信号）。
COMPARE_INTERVAL_SEC = 1800

# kv 键前缀
_KEY_PREFIX = "env_baseline_"


def _key(account: str) -> str:
    return _KEY_PREFIX + str(account or "").strip()


def get_baseline(account: str) -> dict | None:
    """读账号环境基线。无基线或异常 → None。"""
    try:
        data = kv_get(_key(account))
        if isinstance(data, dict) and data.get("ip"):
            return data
    except Exception:
        pass
    return None


def record_baseline(account: str, source: str = "login") -> dict | None:
    """记录/更新账号环境基线（在登录成功/凭证回写成功的时刻调用）。

    探测走 probe_egress_ip_direct（与浏览器启动同一套环境决策）；
    探测失败返回 None（不写基线——宁缺勿错，坏基线比没基线危险）。
    账号未登记（env_path 为空）时退化按 direct 模式探测（仍记录——
    scan_login 刚成功登录但账号索引尚未落盘的场景，基线仍有价值）。
    """
    try:
        from vbrowser import probe_egress_ip_direct  # noqa: PLC0415 延迟导入防循环
        from vbrowser import parse_proxy_config  # noqa: PLC0415

        mode, node_url = None, None
        try:
            from auto_dm import accounts as _acc  # noqa: PLC0415
            env_path = _acc.env_path_of(account)
            if env_path:
                mode, node_url, _err = parse_proxy_config(env_path)
        except Exception:  # noqa: BLE001 索引未就绪：退化 direct，不放弃记录
            mode, node_url = None, None
        mode = mode or "direct"
        r = probe_egress_ip_direct(timeout=10, retries=1, mode=mode, node=node_url)
        if not r or not r.get("ok") or not r.get("ip"):
            return None
        baseline = {
            "ip": str(r.get("ip")),
            "mode": mode,
            "country": str(r.get("country") or ""),
            "recorded_at": time.time(),
            "source": source,
        }
        kv_set(_key(account), baseline)
        return baseline
    except Exception:
        return None


def clear_baseline(account: str) -> None:
    """清除基线（改代理配置/删除账号时调用）。kv_set 不支持删键，写空 dict。"""
    try:
        kv_set(_key(account), {})
    except Exception:
        pass


def compare_baseline(account: str) -> dict:
    """运行期比对：当前出口 IP vs 基线。

    返回 {"checked": bool, "drift": bool, "reason": str, ...}：
      - checked=False：本次未做比对（无基线 / 节流窗口内 / 探测失败）——
        这些都【不是】漂移，调用方不得据此告警；
      - drift=True：出口 IP 与基线不一致 → 调用方应记 BCC-063 告警。
    探测失败（网络抖动/接口超时）按「未比对」处理，绝不误报。
    """
    out: dict[str, Any] = {"checked": False, "drift": False, "reason": "",
                           "baseline_ip": "", "current_ip": ""}
    try:
        base = get_baseline(account)
        if not base:
            out["reason"] = "no_baseline"
            return out
        # 节流：上次比对时间记在基线 dict 内（不另立键，保持单键自包含）
        now = time.time()
        last_cmp = float(base.get("last_compare_at") or 0)
        if now - last_cmp < COMPARE_INTERVAL_SEC:
            out["reason"] = "throttled"
            return out

        from vbrowser import probe_egress_ip_direct  # noqa: PLC0415
        from vbrowser import parse_proxy_config  # noqa: PLC0415

        mode, node_url = None, None
        try:
            from auto_dm import accounts as _acc  # noqa: PLC0415
            env_path = _acc.env_path_of(account)
            if env_path:
                mode, node_url, _err = parse_proxy_config(env_path)
        except Exception:  # noqa: BLE001 索引未就绪：退化 direct，不放弃比对
            mode, node_url = None, None
        mode = mode or "direct"
        r = probe_egress_ip_direct(timeout=10, retries=1, mode=mode, node=node_url)
        if not r or not r.get("ok") or not r.get("ip"):
            # 探测失败 ≠ 漂移：静默等下一轮，绝不告警
            out["reason"] = "probe_failed"
            return out

        cur_ip = str(r.get("ip"))
        # 更新节流时间戳（无论结果）
        base["last_compare_at"] = now
        kv_set(_key(account), base)

        out.update({"checked": True, "baseline_ip": str(base.get("ip")),
                    "current_ip": cur_ip, "mode": mode})
        if cur_ip != str(base.get("ip")):
            out["drift"] = True
            out["reason"] = (f"出口 IP 漂移：基线={base.get('ip')}({base.get('country')}) "
                             f"当前={cur_ip} 模式={mode}")
        else:
            out["reason"] = "consistent"
        return out
    except Exception as e:  # noqa: BLE001
        out["reason"] = f"error: {type(e).__name__}"
        return out


# ===========================================================================
# T5-b（2026-09-24）：两世界可见性探针 —— 防「注入成功却读到空」复发
# ===========================================================================
#
# ## 事故形态（要防的复发）
# `browser_daemon` 的 CAP_*_HOOK_JS 经 `context.add_init_script` 注入，截获前端
# 自发的 `im/user/info` 响应，写进 `window.__CAP_USERINFO__` /
# `window.__CAP_WP_MESSAGE__`（`daemon/browser_daemon_js.py:48-108`）。
# patchright 的 `add_init_script` 走 Route 注入，落点是**页面主世界**；而其
# `evaluate()` 默认 `isolated_context=True` = 在**隔离世界**求值（读取侧现状：
# `bcc_capture.py:212-213`、`browser_daemon.py` 读 hook 处均未传该 kwarg）。
# 一旦注入落进与读取侧不同的世界 → **主世界看得到、读取侧永远读到空**，且
# **不报错**（假成功）——即「注入成功却读到空」。
#
# ## 为什么单验一侧不够（本探针的判据）
# 只验「注入是否执行」（主世界可见）会漏掉本事故；只验「读取侧非空」会把
# 「页面还没加载」误判成故障。⇒ **两条必须同时成立**：
#   ① 主世界能看到 hook 键（注入确实执行）；
#   ② 默认（读取）世界也能读到同一键（跨世界可见）。
# 二者不一致 = red。这是复发时唯一的机械信号（L3 报告 §四.4 门禁条款）。
#
# ## 三态（沿用本模块「未知/降级」纪律，绝不静默通过）
#   pass    —— 两个世界都可见（探针已执行且达标）
#   fail    —— 探针已执行且判定不达标（注入未生效 / 跨世界不可见）
#   unknown —— **未能判定**（未执行 / 无浏览器（离线）/ 后端不适用 / 读取
#              异常 / 返回结构不符）。unknown **永远不等于通过**；调用方不得
#              据此放行。`visibility_gate()` 在无页面时默认即 unknown。
#
# ## 离线替身（诚实标注）
# 真实浏览器在本环境不可用（任务亦禁跑真机）⇒ 本模块只提供**页面无关**的探针
# 入口 `probe_two_world_visibility(page)`：传入任何具备 `evaluate()` 的对象即可。
# 离线验证用**桩对象模拟两世界**（`backend/test_two_world_visibility_gate.py`
# 的 `TwoWorldPageStub`：以是否传 `isolated_context=False` 区分主世界 vs 默认
# 世界）。
# ⚠️ **真机未验证**：对真实 patchright 页面的行为**未在本环境实测**；离线只
# 证明了「判据能对（正控 PASS / 负控 FAIL / 缺证据走 unknown）」。
# ---------------------------------------------------------------------------

VISIBILITY_KEYS: tuple[str, ...] = ("__CAP_USERINFO__", "__CAP_WP_MESSAGE__")

# 探针 JS：在**任意世界**求值，回报该世界里 hook 键是否存在。
# 同一段 JS 分别在主世界与默认（隔离）世界各跑一次，差值即两世界可见性差异。
VISIBILITY_PROBE_JS = r"""
() => {
  const keys = ['__CAP_USERINFO__', '__CAP_WP_MESSAGE__'];
  const seen = {};
  for (const k of keys) {
    try { seen[k] = !!window[k]; } catch (e) { seen[k] = false; }
  }
  return { keys: seen };
}
"""

VIS_STATUS_PASS = "pass"
VIS_STATUS_FAIL = "fail"
VIS_STATUS_UNKNOWN = "unknown"

# 两世界可见性基线的 kv 键前缀（与出口 IP 基线 `env_baseline_` 分键，互不污染）
_VIS_KEY_PREFIX = "env_baseline_vis_"


def _vis_key(account: str) -> str:
    return _VIS_KEY_PREFIX + str(account or "").strip()


def _vis_result(status: str, reason: str, *, leaks=None, main_visible=None,
                default_visible=None, detail: str = "") -> dict:
    return {
        "status": status,
        "reason": reason,
        "ok": status == VIS_STATUS_PASS,
        "checked": status != VIS_STATUS_UNKNOWN,
        "leaks": list(leaks or []),
        "main_visible": list(main_visible or []),
        "default_visible": list(default_visible or []),
        "detail": detail,
    }


def classify_two_world_visibility(main_visible=None, default_visible=None, *,
                                  executed: bool = True,
                                  applicable: bool = True) -> dict:
    """纯判据：把两世界的可见性映射成 pass/fail/unknown 三态（离线可测）。

    main_visible / default_visible —— {CAP 键: bool}（可只含相关键）。
    只吃数据、不碰浏览器，故可同时服务于真实探针与门禁回归。
    """
    if not applicable:
        return _vis_result(
            VIS_STATUS_UNKNOWN, "not_applicable",
            detail="当前内核/后端不适用 JS 注入路径（如 Camoufox 跳过 add_init_script）")
    if not executed:
        return _vis_result(
            VIS_STATUS_UNKNOWN, "not_executed",
            detail="探针未执行（无页面对象 / 离线）—— 不得据此判通过")

    main_on = [k for k in VISIBILITY_KEYS if (main_visible or {}).get(k)]
    default_on = [k for k in VISIBILITY_KEYS if (default_visible or {}).get(k)]
    leaks: list[dict] = []

    if not main_on:
        leaks.append({"code": "BCC-070", "severity": "fatal",
                      "detail": "页面主世界看不到任何 CAP_* hook 键 —— "
                                "init script 未生效（注入失败 / 未执行）"})
    for k in main_on:
        if k not in default_on:
            leaks.append({"code": "BCC-070", "severity": "fatal",
                          "detail": f"{k} 在主世界可见、默认（读取）世界不可见 —— "
                                    f"跨世界不可见，读取侧将永远读到空"})
    for k in default_on:
        if k not in main_on:
            leaks.append({"code": "BCC-070", "severity": "fatal",
                          "detail": f"{k} 在默认（读取）世界可见、主世界不可见 —— "
                                    f"两世界不一致"})

    if leaks:
        return _vis_result(VIS_STATUS_FAIL, "two_world_invisible", leaks=leaks,
                           main_visible=main_on, default_visible=default_on)
    return _vis_result(VIS_STATUS_PASS, "consistent",
                       main_visible=main_on, default_visible=default_on)


def _extract_visibility(raw) -> dict | None:
    """把探针原始返回值规整为 {CAP 键: bool}；结构不符 → None（判 unknown）。"""
    if not isinstance(raw, dict):
        return None
    inner = raw.get("keys")
    if isinstance(inner, dict):
        return {k: bool(inner.get(k)) for k in VISIBILITY_KEYS}
    if any(k in raw for k in VISIBILITY_KEYS):
        return {k: bool(raw.get(k)) for k in VISIBILITY_KEYS}
    return None


def _evaluate_world(page, js: str, *, main_world: bool):
    """在指定 JS 世界里求值。返回 (raw, read_mode)。"""
    if main_world:
        try:
            # patchright 专有 kwarg：isolated_context=False = 主执行世界
            return page.evaluate(js, isolated_context=False), "main(isolated_context=False)"
        except TypeError:
            # 原生 playwright 无该 kwarg（单世界，evaluate 本就在主世界）
            return page.evaluate(js), "default(fallback:isolated_context 不支持)"
    return page.evaluate(js), "default"


def probe_two_world_visibility(page, *, applicable: bool = True) -> dict:
    """对给定页面跑两世界可见性探针（页面无关：只要有 evaluate()）。

    page=None / 不可用 → unknown（**绝不**降级成 pass）。
    """
    if not applicable:
        return classify_two_world_visibility(applicable=False)
    if page is None or not hasattr(page, "evaluate"):
        return classify_two_world_visibility(executed=False)

    try:
        main_raw, main_mode = _evaluate_world(page, VISIBILITY_PROBE_JS, main_world=True)
    except Exception as e:  # noqa: BLE001
        return _vis_result(VIS_STATUS_UNKNOWN, "probe_error",
                           detail=f"主世界读取异常: {type(e).__name__}: {e}")
    try:
        default_raw, _ = _evaluate_world(page, VISIBILITY_PROBE_JS, main_world=False)
    except Exception as e:  # noqa: BLE001
        return _vis_result(VIS_STATUS_UNKNOWN, "probe_error",
                           detail=f"默认世界读取异常: {type(e).__name__}: {e}")

    main_map = _extract_visibility(main_raw)
    default_map = _extract_visibility(default_raw)
    if main_map is None or default_map is None:
        return _vis_result(VIS_STATUS_UNKNOWN, "bad_probe_output",
                           detail="探针返回值结构不符（期望 {keys:{CAP 键: bool}}）")

    res = classify_two_world_visibility(main_map, default_map)
    res["main_read_mode"] = main_mode
    return res


def get_visibility_baseline(account: str) -> dict | None:
    """读账号两世界可见性基线。无基线或异常 → None（≠ 通过）。"""
    try:
        data = kv_get(_vis_key(account))
        if isinstance(data, dict) and data.get("status") in (VIS_STATUS_PASS, VIS_STATUS_FAIL):
            return data
    except Exception:
        pass
    return None


def record_visibility_baseline(account: str, result: dict) -> dict | None:
    """记录两世界可见性基线。

    仅 pass/fail 可落盘；unknown **不落**（无数据不得被当通过）。任何异常 → None。
    """
    if not account or not isinstance(result, dict):
        return None
    if result.get("status") not in (VIS_STATUS_PASS, VIS_STATUS_FAIL):
        return None
    rec = {
        "status": result["status"],
        "reason": str(result.get("reason") or ""),
        "main_visible": list(result.get("main_visible") or []),
        "default_visible": list(result.get("default_visible") or []),
        "main_read_mode": str(result.get("main_read_mode") or ""),
        "recorded_at": time.time(),
        "source": "visibility_probe",
    }
    try:
        kv_set(_vis_key(account), rec)
    except Exception:
        return None
    return rec


def visibility_gate(account: str = "", page=None, *, applicable: bool = True) -> dict:
    """门禁入口：探针 +（可选）落基线，返回三态结果。

    调用方纪律：`status == "pass"` 才放行；`fail` 记 BCC-070 告警；`unknown`
    只降级记录，**不得**当成通过（这正是要防的「静默通过」）。
    """
    res = probe_two_world_visibility(page, applicable=applicable)
    if account and res.get("status") in (VIS_STATUS_PASS, VIS_STATUS_FAIL):
        record_visibility_baseline(account, res)
    return res


def latest_visibility(account: str) -> dict:
    """读最近一次两世界可见性读数。无基线 → unknown/no_baseline（绝不 pass）。"""
    rec = get_visibility_baseline(account)
    if not rec:
        return _vis_result(VIS_STATUS_UNKNOWN, "no_baseline")
    out = _vis_result(rec.get("status"), rec.get("reason") or "recorded",
                      main_visible=rec.get("main_visible"),
                      default_visible=rec.get("default_visible"))
    out["recorded_at"] = rec.get("recorded_at")
    return out
