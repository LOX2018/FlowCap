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
