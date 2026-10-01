# -*- coding: utf-8 -*-
"""决定性对照：**带 a_bogus 签名**的轻量接口 vs 匿名 HTML 页（2026-09-30）。

上一版探测 [B] 未签名 ⇒ 0 字节，结论不算数。本版用项目自己的
`Params.with_a_bogus(host=LIVE_HOST)` 签名（与 client_live.get_rank_list 同款），
分别用**匿名**与**凭证**各跑一次，取体积/状态/耗时。

不打印任何凭证内容。
"""
import os
import sys
import time

ROOT = r"C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2"
sys.path.insert(0, os.path.join(ROOT, "backend"))
os.environ.setdefault("DY_APP_ROOT", r"C:\temp\dyautodm_design")

import requests  # noqa: E402
from builder.header import HeaderBuilder, HeaderType  # noqa: E402
from builder.params import Params  # noqa: E402
from utils.dy_util import tls_verify  # noqa: E402

WEB_RID = "992931212705"
LIVE_HOST = "live.douyin.com"
URL = "https://live.douyin.com/webcast/room/web/enter/"


def call(auth, tag):
    h = HeaderBuilder().build(HeaderType.GET)
    h.set_referer(f"https://live.douyin.com/{WEB_RID}")
    p = Params()
    for k, v in (("aid", "6383"), ("app_name", "douyin_web"), ("live_id", "1"),
                 ("device_platform", "web"), ("language", "zh-CN"),
                 ("enter_from", "link_share"), ("cookie_enabled", "true"),
                 ("web_rid", WEB_RID), ("os_name", "Windows"), ("os_version", "10")):
        p.add_param(k, v)
    if auth is not None:
        p.add_param("msToken", getattr(auth, "msToken", "") or "")
    p.with_a_bogus(host=LIVE_HOST)
    t0 = time.time()
    kw = {"headers": h.get(), "params": p.get(), "timeout": 20, "verify": tls_verify()}
    if auth is not None:
        kw["cookies"] = auth.cookie
    r = requests.get(URL, **kw)
    dt = time.time() - t0
    body = r.content
    st = sc = None
    try:
        j = r.json()
        sc = j.get("status_code")
        rooms = ((j.get("data") or {}).get("data") or [])
        if rooms and isinstance(rooms[0], dict):
            st = rooms[0].get("status")
    except Exception:
        pass
    print(f"[{tag}] HTTP={r.status_code} bytes={len(body):>8,} status_code={sc} "
          f"room.status={st} {dt*1000:.0f}ms")
    return len(body), sc, st


def load_cred():
    try:
        from dy_apis.login_api import DYLoginApi
        from auto_dm import accounts as acc
        env = acc.env_path_of("尚进工伤小助理")
        return DYLoginApi._load_auth_from_env(env) if env else None
    except Exception as e:
        print(f"  (凭证加载跳过: {type(e).__name__})")
        return None


if __name__ == "__main__":
    print(f"房间 {WEB_RID} · 接口 /webcast/room/web/enter/（带 a_bogus 签名）")
    n_anon, sc_a, st_a = call(None, "匿名 签名")
    cred = load_cred()
    if cred is not None:
        n_cred, sc_c, st_c = call(cred, "凭证 签名")
    print("---\n判据：status_code=0 且 room.status=2 ⇒ 该路径可用于开播检测")