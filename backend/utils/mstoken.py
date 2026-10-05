# -*- coding: utf-8 -*-
"""msToken"""

import os
import re
import time

import requests
requests.packages.urllib3.disable_warnings()

from utils.strdata_pure import build_report_body

from utils.fingerprint import get_profile
from utils.tls_policy import tls_verify
_REPORT_URL = "https://mssdk.bytedance.com/web/common?ms_appid=6383"

# 2026-09-17 修补（OCR 审查 HIGH）：按 **ttwid（即账号）** 分别缓存。
#
# 原实现是**单条**模块级缓存 `{"token": ..., "ts": ...}`，不含 ttwid 键。
# 多账号场景下（本项目支持多账号），A 账号拿到的 msToken 会被 B 账号直接命中复用
# ——msToken 与 ttwid/cookie 是配套的，串用会致签名校验失败甚至触发风控。
# 现改为 {ttwid: {"token", "ts"}}，并在超出容量时做简单清理防止无界增长。
_cache: dict = {}
_TTL = 600
_CACHE_MAX = 32


def _cache_get(tw: str):
    """取该 ttwid 的有效缓存 token（过期或不存在返回 None）。"""
    ent = _cache.get(tw)
    if ent and ent.get("token") and (time.time() - ent.get("ts", 0) < _TTL):
        return ent["token"]
    return None


def _cache_put(tw: str, token: str) -> None:
    """写入该 ttwid 的缓存；超容量时清掉最旧的一半（简单 LRU 近似）。"""
    if len(_cache) >= _CACHE_MAX and tw not in _cache:
        oldest = sorted(_cache.items(), key=lambda kv: kv[1].get("ts", 0))
        for k, _ in oldest[: max(1, _CACHE_MAX // 2)]:
            _cache.pop(k, None)
    _cache[tw] = {"token": token, "ts": time.time()}


def _get_ttwid(ttwid: str = None) -> str:
    if ttwid:
        return ttwid
    m = re.search(r"ttwid=([^;]+)", os.getenv("DY_COOKIES") or "")
    return m.group(1) if m else ""


def get_mstoken(ttwid: str = None, proxies: dict = None, use_cache: bool = True) -> str:
    tw = _get_ttwid(ttwid)
    if use_cache:
        # 2026-09-17：缓存按 ttwid 键控（原实现跨账号串用，见 _cache 处说明）。
        hit = _cache_get(tw)
        if hit:
            return hit

    envelope = build_report_body()
    headers = {
        "user-agent": get_profile()["ua"], "accept": "*/*", "accept-language": "zh-CN,zh;q=0.9",
        "content-type": "text/plain;charset=UTF-8",
        "origin": "https://www.douyin.com", "referer": "https://www.douyin.com/",
        "cookie": f"ttwid={tw}" if tw else "",
    }
    try:
        resp = requests.post(_REPORT_URL, data=envelope.encode("utf-8"), headers=headers,
                             verify=tls_verify(), timeout=25, proxies=proxies)
        token = resp.headers.get("x-ms-token", "")
        if not token:
            m = re.search(r"msToken=([^;]+)", resp.headers.get("set-cookie", ""))
            token = m.group(1) if m else ""
        if token:
            _cache_put(tw, token)
        return token
    except Exception:
        return ""
