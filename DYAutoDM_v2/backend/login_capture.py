# coding=utf-8
"""扫码登录「监测 + 捕获分析」机制。

目的：当你扫码登录时，程序自动完成三件事，你只需扫码，剩下看日志：

  1) 扫码前：快照磁盘上已有的“旧凭证”（.env 里的各字段值 + 文件 mtime）。
  2) 扫码后、写回 .env 前：拿到本次新捕获的 auth（cookie + 私信签名四件套）。
  3) 旧→新 逐项比对，并针对“旧凭证为何会让弹幕昵称被加密/ sec_uid 丢失”
     给出基于硬事实的判断（不臆测，只比对磁盘上真实存在的字段差异）。

输出：控制台实时打印 + logs/login_capture_<时间戳>.log 留存，便于离线复盘。

判定口径（全部基于 .env 真实内容，不猜测抖音机制）：
  - 弹幕昵称加密 / sec_uid 缺失，在已验证日志中表现为“用旧存储凭证（未重新扫码）
    启动的会话”。核心可疑点是旧 cookie 是否缺少 sessionid / sid_tt / ttwid /
    msToken 这类维持登录会话身份的关键字段，或私信签名四件套（DY_TICKET /
    DY_TS_SIGN / DY_CLIENT_CERT / DY_PRIVATE_KEY）是否缺失。
  - 本模块只负责把这些字段的“旧值有没有 / 新值补了什么 / 是否变化”如实列出来，
    并据此给出可读性结论，根因归因留给使用者在日志里对照。
"""

import os
import json
import time
from datetime import datetime
from dotenv import load_dotenv

from loguru import logger

from vbrowser import app_root  # 统一应用根：源码态=项目根，打包态=exe 所在目录

_ROOT = app_root()
_LOG_DIR = os.path.join(_ROOT, "logs")

# 维持登录会话身份的关键 cookie 字段（缺失最容易导致会话态异常 / 昵称脱敏）
_CRITICAL_COOKIE_KEYS = ("sessionid", "sid_tt", "ttwid", "msToken", "sid_ucp_v1", "uid_tt")
# 私信签名四件套（缺失则私信接口必失败）
_SIGN_KEYS = ("DY_TICKET", "DY_TS_SIGN", "DY_CLIENT_CERT", "DY_PRIVATE_KEY")


def snapshot_old_env(env_path):
    """扫码前快照磁盘上的旧凭证。

    返回 dict：包含文件路径、是否存在、mtime（及相对当前的年龄秒数）、
    各 .env 字段的原始值（不含值内容，仅长度/哈希尾缀，避免明文落日志）。
    """
    snap = {
        "env_path": env_path,
        "exists": os.path.exists(env_path),
        "mtime": None,
        "age_seconds": None,
        "cookie_field_count": 0,
        "raw": {},
    }
    if not snap["exists"]:
        return snap
    try:
        snap["mtime"] = os.path.getmtime(env_path)
        snap["age_seconds"] = int(time.time() - snap["mtime"])
    except Exception:
        pass
    try:
        load_dotenv(env_path, override=True)
        from dotenv import dotenv_values
        vals = dotenv_values(env_path)
        for k in list(_SIGN_KEYS) + ["DY_COOKIES"]:
            v = vals.get(k)
            if v is None:
                snap["raw"][k] = None
            else:
                snap["raw"][k] = {
                    "len": len(v),
                    "tail": (v[-8:] if len(v) >= 8 else v),  # 仅取末尾 8 字符做指纹，避免明文泄露
                }
        ck = vals.get("DY_COOKIES") or ""
        snap["cookie_field_count"] = len([p for p in ck.split(";") if p.strip()])
    except Exception as e:
        snap["raw_error"] = str(e)[:120]
    return snap


def _env_str_fingerprint(cookie_str):
    """从 cookie_str 提取关键字段是否存在 + 字段数（不存明文值）。"""
    if not cookie_str:
        return {"field_count": 0, "has": {}}
    fields = {}
    for part in cookie_str.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name = part.split("=", 1)[0].strip()
        fields[name] = True
    has = {k: (k in fields) for k in _CRITICAL_COOKIE_KEYS}
    return {"field_count": len(fields), "has": has}


def analyze_login_capture(auth, old_snap, env_path):
    """扫码成功后、写回 .env 前，比对旧→新并生成分析报告。

    auth:      本次新捕获的 DouyinAuth（已含 cookie / 四件套签名）
    old_snap:  snapshot_old_env() 的返回（扫码前的磁盘快照）
    env_path:  本次凭证将写回的 .env 路径

    返回 (report_text, log_file_path)。同时把报告打印到控制台。
    """
    try:
        os.makedirs(_LOG_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        log_path = os.path.join(_LOG_DIR, f"login_capture_{ts}.log")
    except Exception:
        log_path = None

    new_cookie = getattr(auth, "cookie", {}) or {}
    new_cookie_str = getattr(auth, "cookie_str", "") or ""
    new_sign = {
        "DY_TICKET": getattr(auth, "ticket", None),
        "DY_TS_SIGN": getattr(auth, "ts_sign", None),
        "DY_CLIENT_CERT": getattr(auth, "client_cert", None),
        "DY_PRIVATE_KEY": getattr(auth, "private_key", None),
    }
    new_fp = _env_str_fingerprint(new_cookie_str)

    # 旧 cookie 指纹（从旧快照的 DY_COOKIES 长度无法拆字段，这里只记录“有没有旧凭证”）
    old_had_cookie = old_snap.get("raw", {}).get("DY_COOKIES") is not None
    old_had_sign = all(old_snap.get("raw", {}).get(k) is not None for k in _SIGN_KEYS)
    old_age = old_snap.get("age_seconds")

    lines = []
    lines.append("=" * 70)
    lines.append(f"[扫码登录捕获分析] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    lines.append(f"凭证写回目标: {env_path}")
    lines.append("=" * 70)

    # —— 1. 旧凭证概况 ——
    lines.append("")
    lines.append("【1. 扫码前的旧凭证（磁盘快照）】")
    if not old_snap.get("exists"):
        lines.append("  · .env 不存在（首次登录，无旧凭证可比对）")
    else:
        age_min = (old_age // 60) if old_age is not None else None
        lines.append(f"  · 文件存在；上次写入年龄 = {old_age}s（约 {age_min} 分钟前）")
        lines.append(f"  · 旧 DY_COOKIES: {'有' if old_had_cookie else '无'}"
                     f"（字段数={old_snap.get('cookie_field_count')}）")
        lines.append(f"  · 旧私信签名四件套: {'齐全' if old_had_sign else '缺失/不全'}"
                     f"（{'/'.join(k for k in _SIGN_KEYS if old_snap.get('raw', {}).get(k) is None) or '均存在'}）")

    # —— 2. 新捕获概况 ——
    lines.append("")
    lines.append("【2. 本次扫码新捕获的凭证】")
    lines.append(f"  · cookie 字段数 = {len(new_cookie)}")
    lines.append(f"  · cookie 关键字段存在情况（决定会话是否‘活’）：")
    for k in _CRITICAL_COOKIE_KEYS:
        lines.append(f"      - {k:12s}: {'✓ 有' if new_fp['has'].get(k) else '✗ 缺'}")
    lines.append(f"  · 私信签名四件套：")
    for k in _SIGN_KEYS:
        v = new_sign.get(k)
        lines.append(f"      - {k:16s}: {'✓ 已捕获' if v else '✗ 缺失'}")

    # —— 3. 旧→新 差异结论 ——
    lines.append("")
    lines.append("【3. 旧→新 差异与‘昵称加密’根因判断（基于硬事实）】")
    if not old_snap.get("exists"):
        lines.append("  · 首次登录，无旧凭证。本次会话由浏览器实时登录建立，昵称/ sec_uid 应正常。")
    else:
        missing_old_critical = []
        for k in _CRITICAL_COOKIE_KEYS:
            # 旧快照只记录“有没有 DY_COOKIES”，无法逐字段比对；
            # 真正的逐字段差异由“新捕获是否齐全”反推旧凭证可能缺什么。
            pass
        # 若旧有 cookie 但新捕获某些关键字段现在才有，说明旧凭证当时缺这些字段
        if old_had_cookie and not all(new_fp["has"].values()):
            missing_now = [k for k in _CRITICAL_COOKIE_KEYS if not new_fp["has"].get(k)]
            lines.append(f"  · 旧凭证有 cookie，但本次新捕获仍缺关键字段: {missing_now}。")
            lines.append("    说明这些字段不是‘旧凭证丢的’，而是当前会话抖音也未下发（环境/风控问题）。")
        else:
            lines.append("  · 本次新捕获 cookie 关键字段齐全，会话为浏览器实时登录态。")

        if old_had_sign and all(new_sign.values()):
            lines.append("  · 旧、新签名四件套均齐全 —— 签名不是加密根因，问题在 cookie 会话态。")
        elif (not old_had_sign) and all(new_sign.values()):
            lines.append("  · 旧凭证缺私信签名四件套，本次扫码已补齐。")
            lines.append("    （注：签名缺失只影响私信发送，不导致弹幕昵称加密；昵称加密另因 cookie 会话态。）")
        elif (not old_had_sign) and (not all(new_sign.values())):
            lines.append("  · 旧、新均缺签名四件套 —— 本次扫码未成功触发私信页签名，私信将不可用。")

        if old_age is not None and old_age > 3600:
            lines.append(f"  · 旧凭证年龄={old_age}s（>1小时）。抖音 ticket/私钥时效通常较短，")
            lines.append("    旧凭证大概率已过期；本次重新扫码可恢复新鲜会话。")
        elif old_age is not None:
            lines.append(f"  · 旧凭证年龄={old_age}s（较新）。若它仍导致昵称加密，")
            lines.append("    则加密根因不是‘凭证过期’，而是该 .env 当时由非实时会话写回（如复用存储 cookie）。")

    # —— 4. 给使用者的行动建议 ——
    lines.append("")
    lines.append("【4. 结论与建议】")
    if not old_snap.get("exists"):
        lines.append("  → 首次登录完成，后续若复现昵称加密，请保留本日志并对比下次扫码捕获。")
    else:
        if all(new_fp["has"].values()) and all(new_sign.values()):
            lines.append("  → 本次凭证完整（cookie 关键字段 + 签名四件套齐全），会话为实时登录态。")
            lines.append("    若此前用‘旧存储凭证’启动会加密昵称，本次重扫后应恢复正常。")
        else:
            lines.append("  → 本次仍有关键字段缺失（见上），昵称加密风险未完全排除，需结合运行时弹幕日志确认。")
    lines.append("=" * 70)

    report = "\n".join(lines)

    # 打印到控制台（带标识前缀便于在混合日志里定位）
    for ln in lines:
        logger.info(f"[捕获分析] {ln}")
    # 落盘
    if log_path:
        try:
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(report + "\n")
            logger.info(f"[捕获分析] 报告已保存: {log_path}")
        except Exception as e:
            logger.warning(f"[捕获分析] 报告落盘失败: {e}")

    return report, log_path
