"""实测 BCC /capture_userinfo 在不同 wait 下的耗时与截获量。

目的：定位 180s 超时的瓶颈，找到「截获量 / 耗时」的最优平衡点。

背景（2026-08-31）：
  当前调用 wait=90（初始等待 90s）+ 40 轮滚动点击，实测超过 180s 超时，
  导致昵称全部降级为 UID（222/222 全是 peer_name=peer_id）。

  抖音前端首屏就会自发 im/user/info，理论上不需要等 90s。
  本脚本用不同 wait 实测，找出最小可用值。
"""
import json
import time
import urllib.request

PORT = 10074
URL = f"http://127.0.0.1:{PORT}/capture_userinfo"


def call(wait: int, timeout: int = 240):
    body = json.dumps({"wait": wait}).encode()
    req = urllib.request.Request(
        URL, data=body,
        headers={"Content-Type": "application/json"},
        method="POST")
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            j = json.loads(r.read().decode())
        el = time.time() - t0
        data = j.get("data") or {}
        return el, len(data), data
    except Exception as e:
        return time.time() - t0, -1, {"err": f"{type(e).__name__}: {str(e)[:80]}"}


if __name__ == "__main__":
    print("=== BCC /capture_userinfo 速度实测 ===\n")
    for w in (5, 15, 30):
        el, n, data = call(w)
        if n >= 0:
            sample = list(data.items())[:2]
            print(f"wait={w:3d}s -> 耗时 {el:6.1f}s  截获 {n} 个昵称")
            for k, v in sample:
                nn = (v or {}).get("nickname", "")
                print(f"         {k[:22]:22s} -> {nn}")
        else:
            print(f"wait={w:3d}s -> 耗时 {el:6.1f}s  失败: {data.get('err')}")
        print()
