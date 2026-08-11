# coding=utf-8
"""WebView 前端控制台桥接层（1:1 复刻原型 HTML）。

职责：
  - 用 pywebview 打开 web/index.html（本地 HTTP 托管，避免 file:// 的 CORS 限制）；
  - 通过 pywebview 的 JS-Python 桥，把前端 ApiBridge 的调用接回真实后端：
        AutoDM（自动私信控制器）/ DispatchCenter（调度与统计）/
        browser_daemon(9911 凭证守护) / recv_daemon(9912 私信接收守护) / accounts（账号管理）。
  - 不依赖 tkinter；所有与 GUI 控件无关的逻辑直接复用 config / run / core / accounts。

前端约定（见 web/index.html 注入的桥接脚本）：
  window.ApiBridge.* 在 pywebviewready 后由 window.pywebview.api 覆盖。
"""

import os
import sys
import json
import time
import threading
import urllib.parse
import urllib.request
from collections import OrderedDict

# onefile 模式下 PyInstaller 把资源解包到 sys._MEIPASS；开发态用项目根 web/ 目录。
if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
    _BASE = sys._MEIPASS
else:
    _BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WEB_DIR = os.path.join(_BASE, "web")
INDEX_HTML = os.path.join(WEB_DIR, "index.html")
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # DY_Spider_base

# 守护进程端口（与 config / daemon 保持一致）
_BROWSER_DAEMON_PORT = 9911
_RECV_DAEMON_PORT = 9912

from auto_dm import config as C
from auto_dm import accounts
from auto_dm import browser_daemon
from auto_dm import run as _run_mod

try:
    import openpyxl
    from utils import data_util
except Exception:  # 某些环境 import 路径略有差异
    try:
        from auto_dm import utils as data_util
    except Exception:
        data_util = None


# ---------------------------------------------------------------------------
# HTTP 小工具：对本机守护进程做 GET/POST
# ---------------------------------------------------------------------------
def _http_get(url, timeout=3):
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _http_post_json(url, payload, timeout=10):
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ---------------------------------------------------------------------------
# 导出统计为 xlsx（从 gui 的纯逻辑迁移，无 tkinter 依赖）
# ---------------------------------------------------------------------------
def _export_stats_xlsx(adm, live_id=None):
    if not adm or not getattr(adm, "dispatch", None):
        return (False, "尚未运行，无统计数据")
    records = getattr(adm.dispatch, "records", [])
    if not records:
        return (False, "暂无记录")
    if data_util is None or openpyxl is None:
        return (False, "缺少 openpyxl 依赖")
    export_dir = os.path.join(ROOT_DIR, "stats_export")
    data_util.check_and_create_path(export_dir)
    lid = live_id or getattr(C, "LIVE_ID", None) or "unknown"
    path = os.path.join(export_dir, f"stats_{lid}.xlsx")
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "明细"
    cols = ["发言人", "评论内容", "私信情况", "私信文案", "私信时间", "发言时间"]
    ws.append(cols)
    for r in records:
        ws.append([r.get("nickname", ""), r.get("comment", ""), r.get("status", ""),
                   r.get("content", ""), r.get("send_ts", ""), r.get("capture_ts", "")])
    wv = wb.create_sheet("按发言人")
    vcols = ["发言人", "发言次数", "评论内容", "私信情况", "私信文案", "私信时间", "发言时间"]
    wv.append(vcols)
    agg = OrderedDict()
    for r in records:
        agg.setdefault(r.get("nickname", ""), []).append(r)
    for name, rs in agg.items():
        wv.append([
            name, len(rs),
            "\n".join(x.get("comment", "") for x in rs),
            "\n".join(x.get("status", "") for x in rs),
            "\n".join(x.get("content", "") for x in rs),
            "\n".join(x.get("send_ts", "") for x in rs),
            "\n".join(x.get("capture_ts", "") for x in rs),
        ])
    wb.save(path)
    return (True, path)


# ---------------------------------------------------------------------------
# 把 config.py 写回（保留原结构，仅更新指定键）
# ---------------------------------------------------------------------------
def _write_config_file(mapping):
    """把 mapping（键->repr 字符串）写回 config.py，不修改其他内容。"""
    cfg_path = os.path.join(ROOT_DIR, "auto_dm", "config.py")
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception:
        return False
    new_lines = []
    updated = set()
    for line in lines:
        stripped = line.strip()
        matched_key = None
        for k in mapping:
            if stripped.startswith(f"{k} =") or stripped.startswith(f"{k}="):
                matched_key = k
                break
        if matched_key:
            new_lines.append(f"{matched_key} = {mapping[matched_key]}\n")
            updated.add(matched_key)
        else:
            new_lines.append(line)
    # 追加尚未存在的键
    for k in mapping:
        if k not in updated:
            new_lines.append(f"{k} = {mapping[k]}\n")
    try:
        with open(cfg_path, "w", encoding="utf-8") as f:
            f.writelines(new_lines)
        return True
    except Exception:
        return False


def _parse_delay(raw):
    raw = (raw or "").strip()
    if not raw:
        return 0
    for sep in (",", "~", "-"):
        if sep in raw:
            parts = raw.split(sep)
            try:
                lo, hi = float(parts[0]), float(parts[1])
                if lo <= hi:
                    return (int(lo), int(hi))
            except Exception:
                pass
            return 0
    try:
        return float(raw)
    except Exception:
        return 0


# ---------------------------------------------------------------------------
# 主桥接类（pywebview 会把它实例化为 window.pywebview.api）
# ---------------------------------------------------------------------------
class WebBridge:
    def __init__(self):
        self.adm = None
        self._lock = threading.Lock()

    # ---- 只读：概览 -------------------------------------------------------
    def getOverview(self):
        with self._lock:
            adm = self.adm
        if adm is None:
            running = False
            sent = 0
            limit = int(getattr(C, "MAX_TARGET", 9999))
            queue = 0
            status = "未启动"
            paused = False
            room = ""
        else:
            running = adm.is_running()
            sent = adm.sent_count()
            limit = adm.max_target()
            queue = adm.dispatch.queue_size() if adm.dispatch else 0
            status = adm.status
            paused = bool(adm.dispatch and getattr(adm.dispatch, "paused", False))
            room = getattr(adm, "room_title", "") or ""
        # 守护状态
        bd = _http_get(f"http://127.0.0.1:{_BROWSER_DAEMON_PORT}/status")
        rd = _http_get(f"http://127.0.0.1:{_RECV_DAEMON_PORT}/status")
        return {
            "ok": True,
            "running": running,
            "sent": sent,
            "limit": limit,
            "queue": queue,
            "status": status,
            "paused": paused,
            "roomTitle": room,
            "liveUrl": getattr(C, "LIVE_URL", ""),
            "browserDaemon": {
                "alive": bool(bd.get("alive")) if isinstance(bd, dict) else False,
                "loggedIn": bool(bd.get("logged_in")) if isinstance(bd, dict) else False,
                "signReady": bool(bd.get("sign_ready")) if isinstance(bd, dict) else False,
                "uid": bd.get("uid") if isinstance(bd, dict) else None,
                "account": bd.get("account") if isinstance(bd, dict) else None,
            },
            "recvDaemon": {
                "alive": isinstance(rd, dict) and bool(rd.get("ok")),
                "accounts": (rd.get("accounts") if isinstance(rd, dict) else None) or {},
            },
        }

    # ---- 只读：实时统计 ---------------------------------------------------
    def getStats(self):
        with self._lock:
            adm = self.adm
        records = []
        if adm and adm.dispatch:
            records = getattr(adm.dispatch, "records", []) or []
        sent = sum(1 for r in records if r.get("status") not in ("已捕获", "采集(未发)", None, ""))
        # 查阅模式：按 nickname 聚合
        agg = OrderedDict()
        for r in records:
            agg.setdefault(r.get("nickname", ""), []).append(r)
        view = []
        for name, rs in agg.items():
            view.append({
                "nickname": name,
                "count": len(rs),
                "comments": [x.get("comment", "") for x in rs],
                "statuses": [x.get("status", "") for x in rs],
                "contents": [x.get("content", "") for x in rs],
                "sendTimes": [x.get("send_ts", "") for x in rs],
                "captureTimes": [x.get("capture_ts", "") for x in rs],
            })
        return {
            "ok": True,
            "total": len(records),
            "sent": sent,
            "list": [
                {
                    "nickname": r.get("nickname", ""),
                    "comment": r.get("comment", ""),
                    "status": r.get("status", ""),
                    "content": r.get("content", ""),
                    "sendTs": r.get("send_ts", ""),
                    "captureTs": r.get("capture_ts", ""),
                }
                for r in records
            ],
            "view": view,
        }

    # ---- 只读：私信会话（来自 recv_daemon 9912） --------------------------
    def getConversations(self, account=None):
        rd = _http_get(
            f"http://127.0.0.1:{_RECV_DAEMON_PORT}/conversations"
            + (f"?account={urllib.parse.quote(str(account))}" if account else ""))
        if isinstance(rd, dict) and rd.get("ok"):
            return {"ok": True, "conversations": rd.get("conversations", [])}
        return {"ok": False, "conversations": [], "error": rd.get("error", "接收守护未运行")}

    def getConversation(self, account, conv_id):
        rd = _http_get(
            f"http://127.0.0.1:{_RECV_DAEMON_PORT}/conversation"
            f"?account={urllib.parse.quote(str(account))}&conv_id={urllib.parse.quote(str(conv_id))}")
        if isinstance(rd, dict) and rd.get("ok"):
            return {"ok": True, "conversation": rd.get("conversation", {})}
        return {"ok": False, "error": rd.get("error", "接收守护未运行")}

    # ---- 只读：账号管理 ---------------------------------------------------
    def getAccounts(self):
        accs = []
        for name, env_path in accounts.list_accounts():
            st = accounts.account_status(name, force=False, timeout=4)
            accs.append({
                "name": name,
                "isCurrent": name == accounts.current_name(),
                "isMonitor": name == accounts.monitor_name(),
                "isSender": name == accounts.sender_name(),
                "signReady": bool(st.get("sign_ready")),
                "loggedIn": bool(st.get("logged_in")),
                "uid": st.get("uid"),
            })
        bd = _http_get(f"http://127.0.0.1:{_BROWSER_DAEMON_PORT}/status")
        return {
            "ok": True,
            "accounts": accs,
            "current": accounts.current_name(),
            "monitor": accounts.monitor_name(),
            "sender": accounts.sender_name(),
            "browserDaemonAlive": bool(bd.get("alive")) if isinstance(bd, dict) else False,
        }

    # ---- 只读：任务配置（词库 / 策略 / 开关） ----------------------------
    def getTasks(self):
        pool = getattr(C, "DM_MESSAGE_POOL", [getattr(C, "DM_MESSAGE", "")])
        enabled = getattr(C, "DM_MESSAGE_ENABLED", None)
        if enabled is None:
            enabled = [True] * len(pool)
        d = getattr(C, "SEND_DELAY_SEC", 60)
        delay_repr = f"{d[0]},{d[1]}" if isinstance(d, (tuple, list)) and len(d) == 2 else str(d)
        return {
            "ok": True,
            "dmPool": [{"text": t, "enabled": bool(e)} for t, e in zip(pool, enabled)],
            "maxTarget": int(getattr(C, "MAX_TARGET", 9999)),
            "interval": int(getattr(C, "SEND_INTERVAL", 60)),
            "delay": delay_repr,
            "enableDanmaku": bool(getattr(C, "ENABLE_DANMAKU", True)),
            "enableConsole": bool(getattr(C, "ENABLE_CONSOLE", True)),
            "enableSend": bool(getattr(C, "ENABLE_SEND", True)),
            "forceRescan": bool(getattr(C, "FORCE_RESCAN_ON_START", True)),
            "liveUrl": getattr(C, "LIVE_URL", ""),
        }

    # ---- 写：启动 / 暂停 / 继续 / 停止 -----------------------------------
    def start(self, config=None):
        with self._lock:
            if self.adm is not None and self.adm.is_running():
                return {"ok": False, "error": "已在运行中"}
            # 应用前端传入的临时配置（不写盘，仅本次运行生效）
            overrides = {}
            if isinstance(config, dict):
                if "liveUrl" in config:
                    C.LIVE_URL = str(config["liveUrl"]).strip()
                    overrides["LIVE_URL"] = C.LIVE_URL
                if "maxTarget" in config:
                    C.MAX_TARGET = int(config["maxTarget"])
                    overrides["MAX_TARGET"] = C.MAX_TARGET
                if "interval" in config:
                    C.SEND_INTERVAL = int(config["interval"])
                    overrides["SEND_INTERVAL"] = C.SEND_INTERVAL
                if "delay" in config:
                    C.SEND_DELAY_SEC = _parse_delay(config["delay"])
                    overrides["SEND_DELAY_SEC"] = C.SEND_DELAY_SEC
                if "enableDanmaku" in config:
                    C.ENABLE_DANMAKU = bool(config["enableDanmaku"])
                if "enableConsole" in config:
                    C.ENABLE_CONSOLE = bool(config["enableConsole"])
                if "enableSend" in config:
                    C.ENABLE_SEND = bool(config["enableSend"])
                if "dmPool" in config and isinstance(config["dmPool"], list):
                    pool, en = [], []
                    for item in config["dmPool"]:
                        txt = item.get("text", "").strip() if isinstance(item, dict) else str(item).strip()
                        if txt:
                            pool.append(txt)
                            en.append(bool(item.get("enabled", True)) if isinstance(item, dict) else True)
                    if pool:
                        C.DM_MESSAGE_POOL = pool
                        C.DM_MESSAGE_ENABLED = en
                        C.DM_MESSAGE = pool[0]
            self.adm = _run_mod.AutoDM(config_overrides=overrides or None)
            adm = self.adm
        # 在线程中启动（start 内部已起 daemon 线程，这里直接调即可）
        try:
            adm.start()
            return {"ok": True, "status": adm.status}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def pause(self):
        with self._lock:
            adm = self.adm
        if adm is None:
            return {"ok": False, "error": "尚未启动"}
        adm.pause()
        return {"ok": True, "status": adm.status}

    def resume(self):
        with self._lock:
            adm = self.adm
        if adm is None:
            return {"ok": False, "error": "尚未启动"}
        adm.resume()
        return {"ok": True, "status": adm.status}

    def stop(self):
        with self._lock:
            adm = self.adm
        if adm is None:
            return {"ok": False, "error": "尚未启动"}
        adm.stop()
        return {"ok": True, "status": adm.status}

    def setMaxTarget(self, n):
        try:
            n = int(n)
        except Exception:
            return {"ok": False, "error": "非法上限"}
        C.MAX_TARGET = n
        with self._lock:
            adm = self.adm
        if adm is not None and adm.is_running():
            adm.set_max_target(n)
        return {"ok": True, "maxTarget": n}

    # ---- 写：保存配置（写回 config.py，下次启动生效） --------------------
    def saveConfig(self, config=None):
        if not isinstance(config, dict):
            return {"ok": False, "error": "无配置"}
        mapping = {}
        if "liveUrl" in config:
            C.LIVE_URL = str(config["liveUrl"]).strip()
            mapping["LIVE_URL"] = repr(C.LIVE_URL)
        if "maxTarget" in config:
            C.MAX_TARGET = int(config["maxTarget"])
            mapping["MAX_TARGET"] = repr(C.MAX_TARGET)
        if "interval" in config:
            C.SEND_INTERVAL = int(config["interval"])
            mapping["SEND_INTERVAL"] = repr(C.SEND_INTERVAL)
        if "delay" in config:
            C.SEND_DELAY_SEC = _parse_delay(config["delay"])
            mapping["SEND_DELAY_SEC"] = repr(C.SEND_DELAY_SEC)
        if "enableDanmaku" in config:
            C.ENABLE_DANMAKU = bool(config["enableDanmaku"])
            mapping["ENABLE_DANMAKU"] = repr(C.ENABLE_DANMAKU)
        if "enableConsole" in config:
            C.ENABLE_CONSOLE = bool(config["enableConsole"])
            mapping["ENABLE_CONSOLE"] = repr(C.ENABLE_CONSOLE)
        if "enableSend" in config:
            C.ENABLE_SEND = bool(config["enableSend"])
            mapping["ENABLE_SEND"] = repr(C.ENABLE_SEND)
        if "forceRescan" in config:
            C.FORCE_RESCAN_ON_START = bool(config["forceRescan"])
            mapping["FORCE_RESCAN_ON_START"] = repr(C.FORCE_RESCAN_ON_START)
        if "dmPool" in config and isinstance(config["dmPool"], list):
            pool, en = [], []
            for item in config["dmPool"]:
                txt = item.get("text", "").strip() if isinstance(item, dict) else str(item).strip()
                if txt:
                    pool.append(txt)
                    en.append(bool(item.get("enabled", True)) if isinstance(item, dict) else True)
            if pool:
                C.DM_MESSAGE_POOL = pool
                C.DM_MESSAGE_ENABLED = en
                C.DM_MESSAGE = pool[0]
                mapping["DM_MESSAGE_POOL"] = repr(C.DM_MESSAGE_POOL)
                mapping["DM_MESSAGE_ENABLED"] = repr(C.DM_MESSAGE_ENABLED)
                mapping["DM_MESSAGE"] = repr(C.DM_MESSAGE)
        ok = _write_config_file(mapping) if mapping else True
        return {"ok": bool(ok), "written": len(mapping)}

    def saveDmPool(self, rows):
        """rows: [{text, enabled}] 列表，写回词库。"""
        pool, en = [], []
        for item in (rows or []):
            txt = item.get("text", "").strip() if isinstance(item, dict) else str(item).strip()
            if txt:
                pool.append(txt)
                en.append(bool(item.get("enabled", True)) if isinstance(item, dict) else True)
        if not pool:
            return {"ok": False, "error": "词库为空"}
        C.DM_MESSAGE_POOL = pool
        C.DM_MESSAGE_ENABLED = en
        C.DM_MESSAGE = pool[0]
        ok = _write_config_file({
            "DM_MESSAGE_POOL": repr(C.DM_MESSAGE_POOL),
            "DM_MESSAGE_ENABLED": repr(C.DM_MESSAGE_ENABLED),
            "DM_MESSAGE": repr(C.DM_MESSAGE),
        })
        return {"ok": bool(ok), "count": len(pool)}

    # ---- 写：发私信（经 recv_daemon 9912 /send） ------------------------
    def sendDm(self, account, conv_id, text):
        if not (account and conv_id and text):
            return {"ok": False, "error": "缺少 account/conv_id/text"}
        return _http_post_json(
            f"http://127.0.0.1:{_RECV_DAEMON_PORT}/send",
            {"account": account, "conv_id": conv_id, "text": str(text).strip()})

    # ---- 写：导出统计 -----------------------------------------------------
    def exportStats(self):
        with self._lock:
            adm = self.adm
        ok, msg = _export_stats_xlsx(adm)
        return {"ok": bool(ok), "path": msg if ok else "", "error": "" if ok else msg}

    # ---- 账号管理写操作 ---------------------------------------------------
    def setCurrent(self, name):
        try:
            accounts.set_current(name)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def setRoles(self, monitor=None, sender=None):
        try:
            if monitor:
                accounts.set_monitor(monitor)
            if sender:
                accounts.set_sender(sender)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def addAccount(self, name):
        try:
            accounts.add_account(name)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def removeAccount(self, name):
        try:
            accounts.remove_account(name)
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def startBrowserDaemon(self, account=None):
        try:
            acc = account or accounts.current_name()
            exe = sys.executable
            import subprocess
            subprocess.Popen(
                [exe, "--mode", "daemon", "--account", acc],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def stopBrowserDaemon(self):
        try:
            browser_daemon._request_quit()
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def startRecvDaemon(self):
        try:
            exe = sys.executable
            import subprocess
            subprocess.Popen(
                [exe, "--mode", "recv"],
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def stopRecvDaemon(self):
        try:
            _http_post_json(f"http://127.0.0.1:{_RECV_DAEMON_PORT}/quit", {})
            return {"ok": True}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def refreshBrowserDaemon(self, account=None):
        acc = account or accounts.current_name()
        return _http_post_json(
            f"http://127.0.0.1:{_BROWSER_DAEMON_PORT}/refresh?account={urllib.parse.quote(str(acc))}",
            {})


# ---------------------------------------------------------------------------
# 本地 HTTP 托管（避免 file:// 的 CORS / fetch 限制）
# ---------------------------------------------------------------------------
class _WebRequestHandler:
    """极简静态文件服务器，仅服务 web/ 目录。"""

    def __init__(self, handler_base):
        self._base = handler_base

    def __call__(self, *args, **kwargs):
        return _StaticHandler(self._base, *args, **kwargs)


import http.server


class _StaticHandler(http.server.BaseHTTPRequestHandler):
    def __init__(self, base, *args, **kwargs):
        self._base = base
        super().__init__(*args, **kwargs)

    def log_message(self, *a):
        pass

    def do_GET(self):
        from urllib.parse import urlparse
        path = urlparse(self.path).path
        if path in ("", "/"):
            path = "/index.html"
        fp = os.path.normpath(os.path.join(self._base, path.lstrip("/")))
        if not fp.startswith(os.path.normpath(self._base)) or not os.path.isfile(fp):
            self.send_error(404)
            return
        ctype = "text/html"
        if fp.endswith(".js"):
            ctype = "application/javascript"
        elif fp.endswith(".css"):
            ctype = "text/css"
        elif fp.endswith(".json"):
            ctype = "application/json"
        elif fp.endswith((".png", ".jpg", ".jpeg", ".gif")):
            ctype = "image/" + fp.rsplit(".", 1)[1]
        try:
            with open(fp, "rb") as f:
                data = f.read()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        except Exception:
            self.send_error(500)


def launch_web():
    import webview

    bridge = WebBridge()

    # 启动本地静态服务器
    handler = _WebRequestHandler(WEB_DIR)
    server = http.server.HTTPServer(("127.0.0.1", 0), handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()

    url = f"http://127.0.0.1:{port}/index.html"

    # pywebview 窗口（带调试控制台，便于排查）
    webview.create_window(
        "抖音直播间自动私信 · 控制台",
        url,
        js_api=bridge,
        width=1440,
        height=900,
        background_color="#0f1320",
    )
    webview.start(debug=True)


if __name__ == "__main__":
    launch_web()
