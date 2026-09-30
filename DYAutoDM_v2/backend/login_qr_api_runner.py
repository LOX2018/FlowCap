# -*- coding: utf-8 -*-
"""上游 API 扫码登录 —— **冻结安全的子进程入口**（ADR-017 API 备用路径）。

## 为什么是子进程（不是函数调用）
vendor 与 backend 有 **5 个同名顶层包**（`dy_apis`/`builder`/`utils`/`dy_live`/`static`），
应用进程必然已加载项目版 ⇒ 上游的顶层绝对导入会命中项目实现 ⇒ **静默半坏**。
唯一正确形态 = **干净解释器子进程**（独立 `sys.path`，先注入 vendor）。
（判据：`auto_dm/login_api_vendor.py` 的 AUTH-073 与门禁 G1 已把这条钉死。）

## 为什么单进程 + 文件交接（而不是「取码进程」+「轮询进程」）
票据与 P-256 私钥是**会话内状态**：`bd_ticket_guard_client_data` cookie 绑定了公钥，
二维码确认后服务端据此签发 `ticket/ts_sign/client_cert`。
跨进程重建会话需要序列化私钥与全部 cookie，既脆弱又没必要 ——
**一个进程从头跑到尾**，中途只把「二维码图 / 进度 / 最终凭证」写到磁盘，由主程序轮询。

## 产物协议（jobdir 内）
```
qr.png        二维码图（由上游 base64 解码后落盘；主程序直接取用）
status.json   {"stage": "waiting_scan"|"confirmed"|"failed"|"expired", "token": ..., ...}
result.json   成功时: {"ok":true, "cookie_str":..., "ticket":..., "ts_sign":...,
                        "client_cert":..., "private_key":...}
runner.log    子进程自身日志（排障用）
```

## 用法
    python login_qr_api_runner.py --jobdir <DIR> [--timeout 300] [--proxy URL]
冻结态：`dyautodm-backend.exe --qr-api-runner --jobdir <DIR>`（见 main.py 前置分发）
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import sys
import time
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))


def _resolve_vendor() -> str:
    """定位 vendor 目录（源码态 / 冻结态 / 显式覆盖 三路兜底）。"""
    cands = []
    env = os.environ.get("DY_VENDOR_DIR")
    if env:
        cands.append(env)
    # 冻结态：exe 旁边；源码态：本文件上级
    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    for base in (exe_dir, _HERE, os.path.dirname(_HERE)):
        cands.append(os.path.join(base, "vendor", "douyin_spider_upstream"))
        cands.append(os.path.join(base, "..", "vendor", "douyin_spider_upstream"))
    for c in cands:
        c = os.path.abspath(c)
        if os.path.isfile(os.path.join(c, "dy_apis", "login_api.py")):
            return c
    raise RuntimeError(
        "未找到 vendor/douyin_spider_upstream（已试: " + "; ".join(cands) + "）")


def _write_json(path: str, obj: dict) -> None:
    """原子写 JSON（先 .tmp 再 replace），避免主程序读到半截文件。"""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False)
    os.replace(tmp, path)


def _emit(jobdir: str, stage_value: str, **extra) -> None:
    """写进度。★ 位置参数名不与 extra 里的键撞名（否则 `stage=` 会 TypeError）。"""
    extra.pop("stage", None)      # 进度键名由 stage_value 独占，防调用方重复传入
    _write_json(os.path.join(jobdir, "status.json"),
                {"stage": stage_value, "ts": round(time.time(), 3), **extra})


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobdir", required=True)
    ap.add_argument("--timeout", type=int, default=300,
                    help="等待用户扫码的总超时（秒）")
    ap.add_argument("--proxy", default="",
                    help="HTTP/SOCKS 代理；空=直连（与账号环境门阀一致由调用方决定）")
    args = ap.parse_args()

    jobdir = os.path.abspath(args.jobdir)
    os.makedirs(jobdir, exist_ok=True)
    result_path = os.path.join(jobdir, "result.json")
    qr_path = os.path.join(jobdir, "qr.png")

    def _fail(stage: str, err: str, tb: str = "") -> int:
        _emit(jobdir, "failed", stage=stage, error=err)
        _write_json(result_path, {"ok": False, "stage": stage, "error": err,
                                  "traceback": tb[-1500:]})
        return 1

    # ── 干净解释器：先注入 vendor 路径 + chdir（上游以相对路径读 .env/素材）──
    try:
        vendor = _resolve_vendor()
    except Exception as e:  # noqa: BLE001
        return _fail("resolve_vendor", f"{type(e).__name__}: {e}")
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
    old_cwd = os.getcwd()
    try:
        os.chdir(vendor)
    except Exception:  # noqa: BLE001
        pass

    proxies = None
    if args.proxy:
        proxies = {"http": args.proxy, "https": args.proxy}

    try:
        from dy_apis.login_api import DYLoginApi
    except Exception as e:  # noqa: BLE001
        return _fail("import_vendor", f"{type(e).__name__}: {e}",
                     traceback.format_exc())

    _emit(jobdir, "bootstrapping", vendor=vendor)
    api = DYLoginApi()
    try:
        auth = api.bootstrap_auth(proxies=proxies)
    except Exception as e:  # noqa: BLE001
        return _fail("bootstrap", f"{type(e).__name__}: {e}", traceback.format_exc())

    # ── 取码 ────────────────────────────────────────────────────────────
    _emit(jobdir, "getting_qrcode")
    try:
        res = api.get_qrcode(auth)
    except Exception as e:  # noqa: BLE001
        return _fail("get_qrcode", f"{type(e).__name__}: {e}", traceback.format_exc())
    data = (res or {}).get("data") or {}
    token = data.get("token")
    if not token:
        return _fail("get_qrcode",
                     f"无 token: error_code={data.get('error_code')} "
                     f"desc={data.get('description')!r}")
    # 二维码图：上游给的是 base64（PNG）。机械校验魔数，避免把空壳/文本当真图。
    wrote_png = False
    png_err = ""
    b64 = data.get("qrcode")
    if b64:
        try:
            raw = base64.b64decode(b64)
            if raw[:8] == b"\x89PNG\r\n\x1a\n":
                with open(qr_path, "wb") as f:
                    f.write(raw)
                wrote_png = True
            else:
                png_err = f"非 PNG（magic={raw[:8]!r}）"
        except Exception as e:  # noqa: BLE001
            png_err = f"{type(e).__name__}: {e}"
    _emit(jobdir, "waiting_scan", token=token, has_png=wrote_png,
          qr_png=(qr_path if wrote_png else ""), png_err=png_err,
          qr_index_url=data.get("qrcode_index_url") or "")

    # ── 轮询：等用户扫码 → 确认 → 跟随重定向落 cookie ─────────────────
    deadline = time.time() + max(30, args.timeout)
    scanned = False
    last = {}
    poll = max(2.0, float(getattr(api, "POLL_INTERVAL", 5.2)))
    while time.time() < deadline:
        time.sleep(poll)
        try:
            last = api.check_qrcode(auth, token) or {}
        except Exception as e:  # noqa: BLE001
            # 轮询异常不致命（限频/瞬时抖动）：如实记进状态，继续等
            _emit(jobdir, "waiting_scan", token=token, has_png=wrote_png,
                  qr_png=(qr_path if wrote_png else ""), poll_error=str(e)[:200])
            continue
        d = last.get("data") or {}
        status = d.get("status")
        if status == "scanned" and not scanned:
            scanned = True
            _emit(jobdir, "scanned", token=token, has_png=wrote_png,
                  qr_png=(qr_path if wrote_png else ""))
        if status == "confirmed":
            redirect = d.get("redirect_url")
            try:
                api._follow_login_redirect(auth, redirect)  # noqa: SLF001
            except Exception as e:  # noqa: BLE001
                return _fail("follow_redirect", f"{type(e).__name__}: {e}",
                             traceback.format_exc())
            ck = getattr(auth, "cookie", None) or {}
            cookie_str = "; ".join(f"{k}={v}" for k, v in ck.items())
            payload = {
                "ok": True,
                "stage": "confirmed",
                "cookie_str": cookie_str,
                "cookie_count": len(ck),
                "ticket": getattr(auth, "ticket", "") or "",
                "ts_sign": getattr(auth, "ts_sign", "") or "",
                "client_cert": getattr(auth, "client_cert", "") or "",
                "private_key": getattr(auth, "private_key", "") or "",
                "has_sessionid": bool(ck.get("sessionid") or ck.get("sid_tt")),
            }
            _write_json(result_path, payload)
            _emit(jobdir, "confirmed", token=token, has_png=wrote_png,
                  qr_png=(qr_path if wrote_png else ""),
                  cookie_count=len(ck), has_sessionid=payload["has_sessionid"])
            return 0 if payload["has_sessionid"] else 3
        if status == "expired":
            _emit(jobdir, "expired", token=token, has_png=wrote_png,
                  qr_png=(qr_path if wrote_png else ""))
            return _fail("expired", "二维码已过期（用户未及时扫码）")
    return _fail("timeout", f"等待扫码超时（{args.timeout}s），最后状态="
                            f"{(last.get('data') or {}).get('status')}")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001
        # 顶层兜底：任何未捕获异常也要留下可诊断痕迹（否则主程序只看到「消失了」）
        try:
            jd = None
            for i, a in enumerate(sys.argv):
                if a == "--jobdir" and i + 1 < len(sys.argv):
                    jd = sys.argv[i + 1]
                    break
            if jd:
                _write_json(os.path.join(jd, "result.json"),
                            {"ok": False, "stage": "unhandled",
                             "error": f"{type(e).__name__}: {e}",
                             "traceback": traceback.format_exc()[-1500:]})
                _emit(os.path.abspath(jd), "failed", stage="unhandled",
                      error=f"{type(e).__name__}: {e}")
        except Exception:  # noqa: BLE001
            pass
        sys.exit(4)
