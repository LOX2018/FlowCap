"""账号管理路由

取代原版 WebBridge 的账号相关方法。
关键改进：
- getAccounts 拆为轻量 list（仅端口探活）+ 重量级 verify（按需触发）
- 新增 /scan-status 查询扫码状态（替代 fire-and-forget 的间接推断）
- /check 复用 auto_dm.accounts.verify_account 做双引擎校验（含私信列表拉取）
"""
import os
import asyncio
import json
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Request, HTTPException
from loguru import logger
from models.account import (
    AccountInfo,
    AddAccountRequest,
    ScanLoginResponse,
    SetRoleRequest,
)
from auto_dm import accounts as acct_core

router = APIRouter()

# 后台扫码状态：name -> {"running": bool, "done": bool, "loggedIn": bool, "error": str}
_scan_state: dict[str, dict] = {}

# 轻量缓存：getAccounts 轮询频繁（默认 3s），而 verify_account 含网络探活（get_my_uid）
# 单次耗时 1~3s。为避免每次轮询都卡顿，对单账号 verify 结果做 TTL 缓存（与轮询间隔一致），
# 命中缓存瞬时返回，把列表刷新延迟从 2.6s 降到亚秒级。
_VERIFY_CACHE: dict[str, tuple[float, dict]] = {}
# 2026-09-06 全局调用链治理（缓存错配修复）：
# 原值 3s 与前端实际轮询间隔严重错配 —— App.tsx 的 ["accounts"] 查询
# refetchInterval=30000（30s）、main.tsx staleTime=20000（20s），
# 3s TTL 意味着 30s 轮询【永远命中不了缓存】，每次都真实跑
# verify_account（含 get_my_uid 外网探活），N 账号串行下来就是
# 每 30s 一轮 N 次外网请求 + 列表卡顿。
# 2026-09-07 实测纠正（原注释推理错误，勿再照做）：
# 上一版把 TTL 设为 25s「略小于 30s 轮询间隔」——**方向搞反了**。
# TTL 是"缓存有效期"，轮询间隔是"检查频率"。要让轮询命中缓存，
# TTL 必须 **大于** 轮询间隔；设成 25s < 30s 意味着【每次轮询时缓存
# 都已过期 5s】，100% 真实探活。实测日志（run_20260907_190946.log）：
# 43 次探活、间隔精确 30s、持续 82 分钟 —— 缓存全程零命中。
# 改为 60s：30s 轮询时缓存仍在有效期内 → 命中，探活频率由
# 「每 30s 每账号 1 次」降为「每 60s 每账号 1 次」，外网请求减半。
# 凭证失效仍能在一轮 TTL（≤60s）内被感知，不影响自愈及时性。
_VERIFY_TTL = 60.0  # 秒，必须 **大于** 前端轮询间隔（2026-09-07 修正）
_VERIFY_LOCK = threading.Lock()


def _get_adm():
    """懒加载全局引擎实例（app.state.adm）。

    避免在模块顶层 import main 造成的循环依赖：accounts 路由被 main
    导入，但本函数在运行时（请求处理阶段）才访问，此时 main 已就绪。
    """
    try:
        from main import app
        return getattr(app.state, "adm", None)
    except Exception:
        return None


def _last_run_empty() -> dict:
    return {
        "room": "—",
        "roomUrl": "",
        "time": "—",
        "duration": "—",
        "totalRuns": 0,
        "comments": 0,
        "dmSent": 0,
        "dmSuccess": 0,
        "dmFail": 0,
        "dmAfterLive": 0,
    }


def _last_run_runtime() -> dict:
    """兜底：无历史任务时从全局引擎运行时聚合（单账号引擎）。"""
    adm = _get_adm()
    if not adm:
        return _last_run_empty()
    try:
        sent = getattr(adm, "sent_count", 0) or 0
        live_id = getattr(adm, "live_id", None)
        dispatch = getattr(adm, "dispatch", None)
        records = dispatch.records_list() if dispatch else []
        comments = len(records) if records else 0
        from models.enums import RecordStatus
        success = sum(1 for r in records if getattr(r, "status", None) == RecordStatus.SENT)
        fail = sum(1 for r in records if getattr(r, "status", None) == RecordStatus.FAIL)
        status_msg = getattr(adm, "status_msg", "") or ""
        running = status_msg not in ("未启动", "已停止", "运行异常: ")
        return {
            "room": live_id or "—",
            "roomUrl": f"https://live.douyin.com/{live_id}" if live_id else "",
            "time": "进行中" if running else "—",
            "duration": "进行中" if running else "—",
            "totalRuns": 1 if live_id else 0,
            "comments": comments,
            "dmSent": success + fail,
            "dmSuccess": success,
            "dmFail": fail,
            "dmAfterLive": 0,
        }
    except Exception:
        return _last_run_empty()


def _duration_str(start: str, end: str) -> str:
    """'%Y-%m-%d %H:%M:%S' 两个时间戳 -> 'H:MM:SS'；解析失败返回 '—'。"""
    fmt = "%Y-%m-%d %H:%M:%S"
    try:
        from datetime import datetime
        t1 = datetime.strptime((start or "").strip(), fmt)
        t2 = datetime.strptime((end or start or "").strip(), fmt)
        s = max(0, int((t2 - t1).total_seconds()))
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        return f"{h}:{m:02d}:{sec:02d}"
    except Exception:
        return "—"


def _build_last_run(name: str) -> dict:
    """该账号的「上次运行记录」：优先读历史任务（任务中心查阅模式数据源），
    无历史时退回全局引擎运行时聚合。

    数据源 tasks_history.json 每条含 config 快照（直播间）与 records 快照（发送明细），
    故账号管理页看到的最近一次运行结果 = 任务中心历史任务/查阅模式里的同一份数据。
    """
    try:
        from tasks_history import list_history
        hist = list_history() or []
        mine = [it for it in hist if (it.get("acct") or "") == name]
        if not mine:
            return _last_run_runtime()
        it = mine[0]
        records = it.get("records") or []
        cfg = it.get("config") or {}
        live_id = cfg.get("live_id") or it.get("live_id") or ""
        room = cfg.get("live_url") or live_id or "—"
        start = it.get("start_ts") or "—"
        end = it.get("end_ts") or start
        running = it.get("status") == "running"
        duration = "进行中" if running else _duration_str(start, end)
        sent = success = fail = 0
        for r in records:
            st = (r or {}).get("status")
            if st in ("sent", "fail"):
                sent += 1
            if st == "sent":
                success += 1
            elif st == "fail":
                fail += 1
        return {
            "room": room,
            "roomUrl": f"https://live.douyin.com/{live_id}" if live_id else "",
            "time": start,
            "duration": duration,
            "totalRuns": len(mine),
            "comments": len(records),
            "dmSent": sent,
            "dmSuccess": success,
            "dmFail": fail,
            "dmAfterLive": 0,
        }
    except Exception:
        return _last_run_runtime()


# ══════════════════════════════════════════════════════════════════════
#  RPA 扫码登录（ADR-017 / H-30 接线层）—— 2026-09-26
# ══════════════════════════════════════════════════════════════════════
#
#  【为什么有这一层】
#  `auto_dm/login_remote.py` 自 v0.45.14~v0.45.18 实施了完整的 RPA 登录能力
#  （二维码抓取 + cv2 解码 + 一键登录处理 + 残留进程清扫 + 官方接口探活），
#  但实测**全仓零调用** —— 是"孤儿模块"，能力未到达产品。
#  本层把它接到 `/{name}/scan`，并保留**能力协商**：RPA 不可用/失败 ⇒ 回落老
#  `enrich_auth(force=True)` 路径（老路径一行不改，零回归风险）。
#
#  【SoC 边界】
#  本层只做：编排（async→sync 桥接）+ 状态回写 + 失败回落。
#  浏览器操作全部委托 `login_remote`；凭证落盘全部委托 `auth_helper.save_cookie_to_env`
#  （**不自造**写盘逻辑 —— ADR-017 §8.3）。

def _rpa_scan_login(name: str, env_path: str, st: dict) -> bool:
    """扫码登录。**优先走上游 API（纯 HTTP、零浏览器）**，失败回落 RPA 浏览器。

    ## 路由（ADR-017 §8.1：扫码→API、短信→RPA）
    ```
    ① API 扫码（login_qr_api_runner 子进程，纯协议）
       成功 → 落凭证（走既有 save_credential，含 wp/keys 的 merge 保真）→ return True
       失败 → ②
    ② RPA 扫码（Camoufox 截图二维码，DOM 兜底）
    ```
    **为什么优先 API**（2026-09-30 实测）：真机纯 HTTP 冒烟
    `bootstrap 33 cookie → get_qrcode error_code=0（含 PNG）→ check status=new` 全绿，
    且**不需要浏览器**（少一次抖音侧环境访问 + 不占 profile 锁 + 秒级出码）。
    API 子进程是**干净解释器**（vendor 与 backend 有 5 个同名顶层包，同进程必静默半坏）。

    失败**不抛异常** —— 由调用方决定回落，保证 RPA 路径始终可用。
    返回 True 表示**凭证已落盘**。
    """
    import tempfile

    # 代理：与账号环境门阀一致（显式配置决定，代码不探测本机）
    proxy = ""
    try:
        from auto_dm.vbrowser import parse_proxy_config as _ppc
        _mode, _node, _ = _ppc(env_path)
        if _mode == "node" and _node:
            proxy = _node
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[scan] 账号 {name} 代理读取失败（按直连）: {e}")

    # ── ① API 扫码（纯 HTTP）──────────────────────────────────────────
    api_jobdir = None
    api_handle = None
    try:
        from auto_dm import login_remote as _lr

        api_jobdir = tempfile.mkdtemp(prefix="rpa_scan_")
        png = os.path.join(api_jobdir, "qr.png")

        def _on_status(s: dict) -> None:
            # 状态回填（供前端展示；沿用既有 qrPng/decoded 契约）
            if s.get("qr_png"):
                st["qrPng"] = s["qr_png"]
                st["decoded"] = True          # 机械判据：已校验 PNG 魔数
            st["stage"] = s.get("stage", "")

        api_handle = _lr.spawn_api_qr_login(api_jobdir, timeout_s=300, proxy=proxy)
        # 等首张二维码就绪（最多 60s）——出码后再进入长轮询
        _t0 = time.time()
        while time.time() - _t0 < 60:
            _s = _lr._read_json_quiet(os.path.join(api_jobdir, "status.json")) or {}
            if _s.get("qr_png"):
                st["qrPng"] = _s["qr_png"]
                st["decoded"] = True
                break
            if _s.get("stage") == "failed":
                break
            if api_handle.get("proc") is not None and api_handle["proc"].poll() is not None:
                break
            time.sleep(1.0)

        api = _lr.poll_api_qr_login(api_handle, timeout_s=300, interval_s=2.0,
                                    on_status=_on_status)
        if api.get("ok"):
            res = api.get("result") or {}
            # ★ 硬门禁：必须 **sessionid + 四件套齐全** 才落盘。
            #   理由（本项目签名模型）：私信走 imapi 私有网关，靠 protobuf 体内的
            #   ticket/ts_sign/client_cert（由登录响应 bd-ticket-guard-server-data 签发）
            #   鉴权。缺任一项 ⇒ 只拿到 cookie ⇒ 要么沿用旧 wp/keys（**反派生旧签名**，
            #   明面「更新成功」实则无效），要么签名缺失 ⇒ 都会静默坏。
            #   ⇒ 宁可回落 RPA，绝不写半成品凭证。
            _need = ("cookie_str", "ticket", "ts_sign", "client_cert", "private_key")
            _missing = [k for k in _need if not res.get(k)]
            if _missing or not res.get("has_sessionid"):
                logger.warning(f"[ACC-041] [scan] 账号 {name} API 扫码凭证不完整"
                               f"（缺 {_missing or ['sessionid']}），回落 RPA")
            else:
                _save_api_credential(name, env_path, res, st)
                logger.info(f"[scan] 账号 {name} API 扫码成功，凭证已落盘"
                            f"（{res.get('cookie_count')} 项 cookie）")
                return True
        else:
            logger.warning(f"[ACC-040] [scan] 账号 {name} API 扫码失败，回落 RPA: {api.get('reason')}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ACC-042] [scan] 账号 {name} API 扫码异常，回落 RPA: "
                       f"{type(e).__name__}: {e}")
    finally:
        try:
            if api_handle is not None:
                from auto_dm import login_remote as _lr2
                _lr2.reap_api_qr_login(api_handle)
        except Exception as _e:  # noqa: BLE001
            logger.debug(f"[scan] API 子进程收尾跳过: {_e}")

    # ── ② RPA 扫码（浏览器，DOM 兜底）─────────────────────────────────
    from auto_dm import login_remote as _lr

    png = ""
    handle = None
    try:
        out_dir = tempfile.mkdtemp(prefix="rpa_scan_")
        png = os.path.join(out_dir, "login_qr.png")

        # ① 出码（浏览器保持打开，供用户扫码）
        prep = asyncio.run(_lr.prepare_qr_login(env_path=env_path, out_png=png,
                                                headless=True, timeout_s=90))
        if not prep.get("ok"):
            logger.warning(f"[ACC-025] [scan] 账号 {name} RPA 出码失败，回落老路径: "
                           f"{prep.get('reason')}")
            return False
        handle = prep.get("handle")
        st["qrPng"] = prep.get("png") or png
        st["decoded"] = bool(prep.get("decoded"))
        logger.info(f"[scan] 账号 {name} RPA 二维码已就绪: {st['qrPng']}")

        # ② 等扫码（硬判据：cookie 出现 sessionid / sid_tt）
        waited = asyncio.run(_lr.poll_qr_scanned(handle, timeout_s=240, interval_s=3))
        if not waited.get("ok"):
            logger.warning(f"[ACC-026] [scan] 账号 {name} 等待扫码未成功，回落老路径: "
                           f"{waited.get('reason')}")
            return False

        # ③ 凭证落盘（复用既有入口，merge=True 保留其余字段）
        cookies = waited.get("cookies") or {}
        cookie_str = "; ".join(f"{k}={v}" for k, v in cookies.items())
        if not cookie_str:
            logger.warning(f"[ACC-027] [scan] 账号 {name} RPA 拿到的 cookie 为空，回落老路径")
            return False
        from auth_helper import save_cookie_to_env

        # ── F5 非污染态校验（ADR-017）────────────────────────────────
        # 判据取自**既有实测结论**，不自创：
        #   · `dy_apis/login_api.py::_cookie_is_polluted` 已于 2026-08-17 **明确废弃**
        #     基于 cookie 格式的污染判定（实测会误伤合法凭证，一律放行）——
        #     ⇒ **不得**再按 cookie 格式判污染，污染由【页面风控监测】兜底。
        #   · 故本处判据 = ①F6 页面风控曾命中 ⇒ **拒写**；②关键 cookie 缺失 ⇒ 告警。
        #   · 同时复用既有 `login_capture`（snapshot_old_env + analyze_login_capture）
        #     生成旧→新差异报告（可追溯），**不自造报告格式**。
        try:
            from login_capture import snapshot_old_env, analyze_login_capture
            from builder.auth import DouyinAuth

            _auth = DouyinAuth()
            _auth.perepare_auth(cookie_str)      # 复用既有构造，不自造
            _old = snapshot_old_env(env_path)
            _report, _rpt_path = analyze_login_capture(_auth, _old, env_path)
            st["captureReport"] = _rpt_path or ""
            logger.info(f"[scan] 账号 {name} F5 捕获分析报告: {_rpt_path}")

            # 判据②：关键 cookie 缺失（sessionid/sid_tt 决定会话是否"活"）
            _ck = _auth.cookie or {}
            _missing = [k for k in ("sessionid", "sid_tt") if not _ck.get(k)]
            if _missing:
                logger.warning(f"[ACC-031] [scan] 账号 {name} 凭证缺关键字段 {_missing}，"
                               f"会话可能无效（仍写入，由后续探活兜底）")
                st["missingKeys"] = _missing
        except Exception as _e_f5:  # noqa: BLE001
            logger.warning(f"[ACC-032] [scan] 账号 {name} F5 捕获分析失败（不阻断）: {_e_f5}")

        # 判据①：F6 页面风控曾命中 ⇒ **拒写**（污染态，写入=污染凭证）
        if waited.get("risk"):
            logger.error(f"[ACC-033] [scan] 账号 {name} 扫码过程曾命中风控页，"
                         f"**拒绝写入**凭证（防污染态）")
            st["rejected"] = "risk_control"
            return False

        # ── F8 失败回滚（ADR-017）：**写前先备份**，失败可还原 ──────────
        # 复用既有备份语义（`services/db_transfer._backup_file`：copy2 →
        # `<path>.bak.<时间戳>`），**不自造**。备份的是 `<env_path>.enc`
        # （明文 .env 已于 2026-09-21 废弃，即使存在也不被读取）。
        # 备份失败**不阻断**写入（不能因为备份不了就永远不更新凭证），只告警。
        _bak = ""
        try:
            from services.db_transfer import _backup_file
            _enc = f"{env_path}.enc"
            if os.path.exists(_enc):
                _bak = _backup_file(_enc)
                st["backup"] = _bak
                logger.info(f"[scan] 账号 {name} 凭证已备份（F8 可回滚）: {_bak}")
        except Exception as _e_bak:  # noqa: BLE001
            logger.warning(f"[ACC-030] [scan] 账号 {name} 凭证备份失败（不阻断写入）: {_e_bak}")

        save_cookie_to_env(cookie_str, env_path)
        logger.info(f"[scan] 账号 {name} RPA 凭证已落盘（{len(cookies)} 项 cookie）")
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[ACC-028] [scan] 账号 {name} RPA 路径异常，回落老路径: "
                       f"{type(e).__name__}: {e}")
        return False
    finally:
        # 无论成败都必须收尾，否则 profile 被长期占用（ADR-017 §8.5 启动门禁）
        try:
            if handle:
                asyncio.run(_lr.close_handle(handle))
        except Exception as _e_close:  # noqa: BLE001
            logger.debug(f"[scan] 账号 {name} RPA 收尾跳过: {_e_close}")


def _save_api_credential(name: str, env_path: str, res: dict, st: dict) -> bool:
    """把 API 扫码拿到的凭证落盘，并**服务端 + 身份双校验**；不过则还原。

    返回 True 仅当「已落盘 **且** 校验通过」。任何环节失败 ⇒ 还原备份 + 返回 False
    （调用方回落 RPA），**绝不留下半成品或谎报成功**（用户铁律：验证后才汇报）。

    ## 写盘协议（复用既有唯一入口，ADR-017 §8.3）
    `DYLoginApi().save_credential(auth, env_path)` → `member_ctx.write_env_file(merge=True)`：
      · **写前备份** `<env_path>.enc` → `.bak.<时间戳>`（复用 `services.db_transfer._backup_file`）
      · `merge=True` 保留其余字段（含既有 `DY_WEB_PROTECT/DY_KEYS`）
    ## 校验（缺一不可）
      ① 磁盘可读：`_load_auth_from_env` 能读回且含 sessionid
      ② 服务端会话有效：`passport/account/info/v2` 返回 user_id>0 且无 error_code
      ③ 身份一致：探活 uid ∈ 该账号历史 conv_id（防串号/幽灵身份）
    """
    enc = f"{env_path}.enc"
    bak = ""
    # ── 写前备份（备份失败**不阻断**写入，但要显式记录）────────────────
    try:
        from services.db_transfer import _backup_file
        if os.path.exists(enc):
            bak = _backup_file(enc)
            st["backup"] = bak
            logger.info(f"[scan] 账号 {name} API 凭证写前已备份: {bak}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[scan] 账号 {name} API 凭证备份失败（不阻断写入）: {e}")

    def _restore() -> None:
        if bak and os.path.exists(bak):
            try:
                import shutil
                shutil.copy2(bak, enc)
                logger.warning(f"[scan] 账号 {name} 已从备份还原凭证: {bak}")
            except Exception as e:  # noqa: BLE001
                logger.error(f"[scan] 账号 {name} 凭证还原失败（备份仍在 {bak}）: {e}")

    # ── 落盘（复用既有唯一写入口）─────────────────────────────────────
    try:
        from builder.auth import DouyinAuth
        from dy_apis.login_api import DYLoginApi
        _auth = DouyinAuth()
        _ck = res.get("cookie_str") or ""
        _auth.perepare_auth(_ck, "", "")
        _auth.cookie = {kv.split("=", 1)[0]: kv.split("=", 1)[1]
                        for kv in _ck.split("; ") if "=" in kv}
        _auth.ticket = res.get("ticket") or None
        _auth.ts_sign = res.get("ts_sign") or None
        _auth.client_cert = res.get("client_cert") or None
        _auth.private_key = res.get("private_key") or None
        DYLoginApi().save_credential(_auth, env_path)
    except Exception as e:  # noqa: BLE001
        logger.error(f"[scan] 账号 {name} API 凭证写盘异常: {type(e).__name__}: {e}")
        _restore()
        return False

    cookies = _auth.cookie or {}
    st["saved"] = True
    st["cookieCount"] = len(cookies)

    # ── 校验①磁盘可读 ──────────────────────────────────────────────────
    try:
        from dy_apis.login_api import DYLoginApi as _DLA
        back = _DLA._load_auth_from_env(env_path)   # noqa: SLF001
        bc = getattr(back, "cookie", None) or {}
        if not (bc.get("sessionid") or bc.get("sid_tt")):
            logger.error(f"[scan] 账号 {name} API 凭证回读缺 sessionid ⇒ 还原")
            _restore()
            return False
    except Exception as e:  # noqa: BLE001
        logger.error(f"[scan] 账号 {name} API 凭证回读失败 ⇒ 还原: {e}")
        _restore()
        return False

    # ── 校验②服务端会话有效（官方 passport 接口，权威判据）───────────
    try:
        from auto_dm import login_remote as _lr
        sv = asyncio.run(_lr._probe_session_valid_by_cookies(cookies))
        if not sv.get("ok"):
            logger.error(f"[scan] 账号 {name} API 凭证服务端未确认（{sv.get('reason')}）⇒ 还原并回落 RPA")
            _restore()
            return False
        st["serverUid"] = str(sv.get("uid") or "")
        logger.info(f"[scan] 账号 {name} API 凭证服务端确认有效 uid={sv.get('uid')}")
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[scan] 账号 {name} API 凭证服务端校验异常 ⇒ 保守还原: {e}")
        _restore()
        return False

    # ── 校验③身份一致（探活 uid ∈ 历史 conv_id）──────────────────────
    try:
        from services.uid_probe import _uid_consistent_with_history as _uid_ok
        _u = str(st.get("serverUid") or "")
        if _u and not _uid_ok(name, _u):
            logger.error(f"[scan] 账号 {name} API 凭证身份漂移（uid={_u} 不在历史 conv_id）⇒ 还原并回落 RPA")
            _restore()
            return False
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[scan] 账号 {name} 身份一致性校验跳过（不阻断）: {e}")

    return True


def _do_scan(name: str):
    """后台线程：打开有头指纹浏览器，由**用户手动**完成登录/验证，凭证写回对应 .env。

    2026-09-29（方案2 · 用户拍板）默认**手动**；ADR-017 的 RPA 扫码/短信模板降级为
    **显式备用**（仅当 st["_force_rpa"] 置位时走，见 /update-login 的 mode）。
    """
    st = _scan_state.setdefault(name, {})
    st["running"] = True
    st["done"] = False
    st["loggedIn"] = False
    st["error"] = ""
    # 2026-09-19 v0.43.98【双实例根治】整段独占 profile 所有权：
    # 「停守护 → 弹扫码浏览器独占 profile → 拉回 BCC」必须在同一把锁内，
    # 否则与并发的 ensure_daemons_for 撞车 ⇒ 两 chromium 抢同一 profile
    # （实测 TargetClosedError：扫码页加载异常、授权后读不到回执）。
    try:
        from services.browser_gate import ProfileOwnership as _Own
        _own = _Own(name, "scan_login")
        _own.__enter__()
    except Exception as _e_own:
        logger.warning(f"[ACC-024] " + f"[scan] 账号 {name} 未取得 profile 所有权锁"
            f"（降级不加锁，存在多实例风险）: {_e_own}")
        _own = None
    try:
        # 重扫前先停该账号凭证守护，释放与「查看模式」共有的 profile 锁，
        # 否则 force=True 清空 vb_profile_default 时会因 Chromium 占用而失败
        # （WinError 32），导致后续浏览器崩溃落到 about:blank。
        _quit_browser_daemon(name)
        env_path = acct_core.env_path_of(name)

        # ── 更新凭证入口（2026-09-29 · 方案2 · 用户拍板）：默认**手动** ──────
        # 用户原话：「账号、凭证更新换回之前的，用户手动操作，不要使用固定的扫码/短信模板」。
        # 默认 = ADR-017 之前的旧路径 `enrich_auth(force=True)`：打开**有头**指纹浏览器，
        # 用户自己在窗口里完成扫码/验证码/滑块；凭证由既有链路（save_credential +
        # BCC 保活回写）写回 .env。**不**自动点「扫码登录」tab、**不**走短信 RPA 模板。
        # ADR-017 的 RPA 两条**降级为显式备用**：仅当显式要求（st["_force_rpa"]）才走。
        st["path"] = "manual"
        _rpa_ok = False
        if st.pop("_force_rpa", False):
            try:
                _rpa_ok = _rpa_scan_login(name, env_path, st)
            except Exception as _e_rpa:  # noqa: BLE001
                logger.warning(f"[ACC-029] [scan] 账号 {name} RPA 备用路径异常，回落手动: {_e_rpa}")
        if _rpa_ok:
            st["path"] = "rpa"
            st["loggedIn"] = True
        else:
            # 默认手动路径（ADR-017 之前的旧行为，下游逻辑一行未改）
            st["path"] = "manual"
            from auth_helper import enrich_auth
            auth, _ = enrich_auth(None, force=True, env_path=env_path)
            st["loggedIn"] = bool(getattr(auth, "cookie", None))
        st["done"] = True
    except Exception as e:
        logger.error(f"[ACC-001] " + f"[scan] 账号 {name} 扫码异常: {e}")
        st["error"] = str(e)
        st["done"] = True
    finally:
        # 在锁内把 BCC 拉回，杜绝与并发拉起重叠
        try:
            from auto_dm.daemon_launcher import ensure_daemons_for
            ensure_daemons_for(name, wait=False)
        except Exception as _e_rd:
            logger.debug(f"[scan] 账号 {name} 守护回拉跳过: {_e_rd}")
        st["running"] = False
        if _own is not None:
            _own.__exit__(None, None, None)


def _do_open_browser(name: str):
    """后台线程：单纯拉起该账号绑定的指纹浏览器并打开抖音主页（不扫码、不抓凭证）。"""
    st = _scan_state.setdefault(name, {})
    st["running"] = True
    st["done"] = False
    st["error"] = ""
    try:
        import asyncio
        from auto_dm import config as _cfg
        from auto_dm.vbrowser import open_douyin_home, init_vb_config
        init_vb_config(_cfg)
        env_path = acct_core.env_path_of(name)
        profile = acct_core.profile_dir_of(env_path)
        os.makedirs(profile, exist_ok=True)
        logger.info(f"[open-browser] 账号 {name} 打开指纹浏览器(profile={profile})")
        # 前台常驻：阻塞直到用户关闭浏览器窗口
        asyncio.run(open_douyin_home(profile, headless=False,
                                     url="https://www.douyin.com/", account=name))
        st["done"] = True
    except Exception as e:
        logger.error(f"[BCC-001] " + f"[open-browser] 账号 {name} 打开指纹浏览器异常: {e}")
        st["error"] = str(e)
        st["done"] = True
    finally:
        st["running"] = False


def _wait_scan_error(name: str, timeout: float = 4.0) -> str:
    """等待扫码线程：若很快以失败结束（如指纹内核缺失），返回错误文案，否则返回空串。

    浏览器弹窗是异步的，enrich_auth 在真正弹窗前若因环境错误（缺 vb_chromium 内核、
    参数错误等）会立即抛异常，此时前端应拿到 ok=False 而非永远 ok=True 却看不到窗口。
    """
    import time as _t
    end = _t.time() + timeout
    while _t.time() < end:
        st = _scan_state.get(name)
        if st and st.get("done") and st.get("error"):
            return st["error"]
        if st and not st.get("running") and st.get("done"):
            break
        _t.sleep(0.2)
    return ""


def _cached_verify(name: str, timeout: int = 3) -> dict:
    """带 TTL 缓存的 verify_account 封装。

    getAccounts 列表轮询（默认 3s）若每次都跑真实 verify（含 get_my_uid 网络探活），
    会产生 1~3s 的可感知延迟。缓存命中（TTL 内）直接返回上次结果，把延迟降到亚秒级。
    只缓存 dm_loopback=False（轮询用）的结果；按钮/自检触发的 dm_loopback=True 不走缓存。
    """
    now = time.time()
    with _VERIFY_LOCK:
        cached = _VERIFY_CACHE.get(name)
        if cached is not None and (now - cached[0]) < _VERIFY_TTL:
            return cached[1]
    # 缓存未命中：真实计算（可能较慢，但并发池内只发生一次）
    try:
        result = acct_core.verify_account(name, timeout=timeout, dm_loopback=False)
    except Exception:
        result = {
            "ok": False,
            "uid": None,
            "wp": {"level": "unknown", "label": "状态获取失败", "detail": ""},
            "dm": {"level": "unknown", "label": "待校验", "detail": ""},
            "auto_fix_triggered": False,
        }
    with _VERIFY_LOCK:
        _VERIFY_CACHE[name] = (now, result)
    return result


def _to_raw_account(name: str) -> dict:
    """把后端真实账号状态映射为前端 RawAccount 兼容结构。

    前端 accounts.tsx 的 RawAccount 期望：
    name/uid/level/label/loggedIn/browserDaemonAlive/wpEngine/dmEngine/
    isCurrent/isMonitor/isSender。

    性能关键：仅调用【一次】verify_account 作为唯一真相源（account_status 已委托它），
    避免原本 account_status + verify_account 两次串行重型探活叠加导致列表刷新卡顿。
    wpEngine / dmEngine 与 level/label/alive/uid 全部取自这一次 verify_account 的同一结果，
    既保证账号管理页与启动自检弹窗结论一致，又把耗时砍半。
    """
    bport = acct_core.browser_daemon_port(name)
    rport = acct_core.recv_daemon_port(name)
    try:
        # 单次 verify_account 含网络探活，较慢；优先用 TTL 缓存避免列表刷新卡顿
        v = _cached_verify(name, timeout=3)
        wp = v.get("wp", {})
        dm = v.get("dm", {})
        wp_level = wp.get("level")
        level = "ok" if wp_level == "ok" else (
            "nosign" if wp_level == "nosign" else (
                "expired" if wp_level in ("fail", "error") else "missing"
            )
        )
        logged_in = wp_level == "ok"
        uid = v.get("uid")
        label = wp.get("label", "")
    except Exception:
        level, label, logged_in, uid = "unknown", "状态获取失败", False, None
        wp = {"level": "unknown", "label": "状态获取失败", "detail": ""}
        dm = {"level": "unknown", "label": "待校验", "detail": ""}
    is_current = (name == acct_core.current_name())
    monitor = (name == acct_core.monitor_name())
    sender = (name == acct_core.sender_name())
    return {
        "name": name,
        "uid": uid,
        "level": level,
        "label": label,
        "loggedIn": bool(logged_in),
        "browserDaemonPort": bport,
        "recvDaemonPort": rport,
        "browserDaemonAlive": acct_core._port_open(bport, timeout=0.3),
        "recvDaemonAlive": acct_core._port_open(rport, timeout=0.3),
        "wpEngine": {
            "level": wp.get("level", "unknown"),
            "label": wp.get("label", "未知"),
            "detail": wp.get("detail", ""),
        },
        "dmEngine": {
            "level": dm.get("level", "unknown"),
            "label": dm.get("label", "待校验"),
            "detail": dm.get("detail", ""),
        },
        "isCurrent": is_current,
        "isMonitor": monitor,
        "isSender": sender,
        "lastRun": _build_last_run(name),
    }


@router.get("")
async def list_accounts(request: Request):
    """轻量列表（端口探活 + 状态摘要，对齐前端 getAccounts 期望结构）

    性能关键：各账号的 verify_account 是独立的 IO 操作，用线程池并发执行，
    整体延迟从「串行 N 个账号 × 两次探活」降为「并发后最慢一个账号的一次探活」，
    删除/新增账号后的列表刷新从 3s+ 降到亚秒级。

    注意：auto_dm.accounts.list_accounts() 返回 [(name, env_path), ...] 元组，
    这里需拆包取纯 name 字符串，否则 _to_raw_account 会收到整个元组，
    导致 name 字段被序列化为数组（前端 Avatar.charAt 崩溃）。
    """
    raw = acct_core.list_accounts()
    names = [n[0] if isinstance(n, (tuple, list)) else n for n in raw]
    if not names:
        return {"ok": True, "accounts": []}
    # 并发校验，避免串行卡顿（删除账号后刷新尤其明显）
    # 2026-09-06 全局调用链治理：ThreadPoolExecutor 的 pool.map 本身是
    # 【同步阻塞】调用——虽然池内线程让出了 GIL，但 async 事件循环仍被
    # 卡住直到最慢的账号返回（timeout=3s）。期间 /api/overview、
    # /api/live/stream 等 3s 轮询全部排队。整段丢 run_in_executor，
    # 让事件循环真正空出来。
    def _run_all() -> list:
        with ThreadPoolExecutor(max_workers=min(len(names), 8)) as pool:
            return list(pool.map(_to_raw_account, names))

    loop = asyncio.get_running_loop()
    accounts = await loop.run_in_executor(None, _run_all)
    return {"ok": True, "accounts": accounts}


@router.get("/self-check")
async def self_check(request: Request):
    """启动自检：对所有账号真跑双引擎校验（wp=凭证守护四件套 + dm=私信列表拉取）。

    前端打开时调用，用于一次性判断每个账号的 wp 引擎 / 私信引擎是否可用，
    若不可用返回明细供前端弹「自检说明」弹窗。相比 /{name}/check（按需单账号），
    本接口一次性覆盖全部账号，且对每个账号都跑 dm_loopback（私信列表拉取），
    弥补 list_accounts 轮询接口 dmEngine 恒为「待校验」的盲区。
    """
    raw = acct_core.list_accounts()
    names = [n[0] if isinstance(n, (tuple, list)) else n for n in raw]
    # 2026-09-06 全局调用链治理（串行阻塞 + 事件循环阻塞）：
    # 原实现在 async 路由里【串行】跑 N 个账号的 verify_account（每个
    # 含外网探活 1~8s），N 账号就是 N×8s 的事件循环阻塞 —— 前端整体卡死。
    # 改为：整段丢线程池 + 池内并发（与 list_accounts 一致）。
    def _verify_one(name: str) -> dict:
        entry = {"name": name, "wp": None, "dm": None, "ok": False}
        try:
            verify = acct_core.verify_account(name, timeout=8, dm_loopback=True)
            entry["wp"] = verify.get("wp")
            entry["dm"] = verify.get("dm")
            entry["uid"] = verify.get("uid")
            entry["ok"] = bool(verify.get("ok"))
            entry["autoFixTriggered"] = bool(verify.get("auto_fix_triggered"))
        except Exception as e:  # 单账号校验异常不阻断其他账号
            logger.error(f"[ACC-002] " + f"[self-check] 账号 {name} 校验异常: {e}")
            entry["wp"] = {"level": "error", "label": "校验异常"}
            entry["dm"] = {"level": "error", "label": "校验异常"}
        return entry

    def _run_all() -> list:
        if not names:
            return []
        with ThreadPoolExecutor(max_workers=min(len(names), 8)) as pool:
            return list(pool.map(_verify_one, names))

    loop = asyncio.get_running_loop()
    items = await loop.run_in_executor(None, _run_all)
    # 整体是否全部可用（无 fail/error/unknown，且至少一个账号）
    any_fail = any(
        it["wp"] and it["wp"].get("level") in ("fail", "warn", "error", "unknown")
        or it["dm"] and it["dm"].get("level") in ("fail", "error", "unknown")
        for it in items
    )
    return {"ok": True, "allOk": (len(items) > 0 and not any_fail), "items": items}


@router.post("/{name}/check")
async def check_account(name: str) -> dict:
    """引擎校验（重量级，按需触发）。

    复用 auto_dm.accounts.verify_account（双引擎校验）：
      - wp 引擎：凭证守护是否在跑 + 守护保活的凭证能否还原出完整签名四件套；
      - 私信引擎：拉取全部私信会话列表，验证 imapi 私有网关私信凭证有效、列表可读取。
    返回前端 runCheck 期望的结构 {ok, verify:{wp,dm,uid}}。
    """
    logger.info(f"[check] 账号 {name} 发起双引擎校验（含私信列表拉取）")
    try:
        # 2026-09-06 全局调用链治理（事件循环阻塞）：
        # verify_account 是同步重型函数（含 get_my_uid 外网探活 + 私信列表
        # 拉取，1~8s）。在 async 路由里直接同步调用会【阻塞 uvicorn 事件
        # 循环】——期间所有其他 API（含 3s/5s 高频轮询）全部排队等待，
        # 表现为整个前端卡死。改 run_in_executor 丢线程池执行。
        loop = asyncio.get_running_loop()
        verify = await loop.run_in_executor(
            None, lambda: acct_core.verify_account(name, timeout=8, dm_loopback=True))
        logger.success(
            f"[check] 账号 {name} 校验完成 · wp:{verify['wp']['label']} · dm:{verify['dm']['label']}"
        )
        return {"ok": True, "verify": verify}
    except Exception as e:
        logger.error(f"[ACC-003] " + f"[check] 账号 {name} 校验异常: {e}")
        return {"ok": False, "error": str(e)}


def _quit_browser_daemon(name: str) -> bool:
    """释放该账号 profile 锁（避免与弹窗的 Chromium 抢锁 SingletonLock 崩溃）。

    返回 True 表示守护原本在跑且已发送停止请求；False 表示守护未运行。

    **2026-09-03 加固（孤儿锁 bug 实机定位）**：
    只探测端口会漏掉一种致命情况 —— browser_daemon 进程已死（端口已释放），
    但它的 chromium 子进程族仍持有 profile（用户数据目录被前一次 app 实例
    遗留，或 daemon 异常退出时子进程未被回收）。此时 `_port_open` 返回 False
    直接跳过 quit → 弹查看浏览器撞上 SingletonLock → exitCode=21
    「Target page, context or browser has been closed」。

    加固逻辑：
      1. 端口活着 → 照常发 /quit（旧逻辑）
      2. 端口已死 → 检查是否仍有 chrome 进程的 --user-data-dir 指向该账号
         profile：有 → 按进程树 kill（释放孤儿锁）；无 → 正常返回
    """
    bport = acct_core.browser_daemon_port(name)
    env_path = acct_core.env_path_of(name)
    try:
        profile = str(acct_core.profile_dir_of(env_path))
    except Exception:
        profile = ""
    if acct_core._port_open(bport, timeout=0.3):
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{bport}/quit", method="POST"
            )
            urllib.request.urlopen(req, timeout=3)
        except Exception as e:
            logger.warning(f"[BCC-002] " + f"[open-browser] 停止守护 {name} 失败（可能已退出）: {e}")
        # 等待 profile 锁释放（Chromium 退出需要一点时间）
        time.sleep(1.5)
        return True

    # 端口已死：检查孤儿 chrome 是否仍占着 profile
    if profile:
        killed = _kill_profile_holders(profile)
        if killed:
            logger.warning(f"[BCC-003] " + f"[open-browser] 账号 {name} 发现并清理 {killed} 个持有 "
                f"profile 的孤儿浏览器进程（端口 {bport} 已死但锁未释放）")
            return True
    return False


def _kill_profile_holders(profile: str) -> int:
    """按 --user-data-dir 匹配持有该 profile 的 chrome 进程并按树 kill。

    只杀【指向该固定 profile】的进程，绝不误伤其他账号 / 其他浏览实例。
    返回清理的进程数。
    """
    if not profile:
        return 0
    import subprocess
    import platform

    if platform.system() != "Windows":
        return 0
    norm = profile.replace("/", "\\").lower()
    killed = 0
    try:
        # PowerShell 枚举所有 chrome 进程及其命令行，按 user-data-dir 精确匹配
        ps = (
            "Get-CimInstance Win32_Process -Filter \"Name='chrome.exe'\" | "
            "Where-Object { $_.CommandLine -like '*user-data-dir*' } | "
            "ForEach-Object { $_.ProcessId.ToString() + '|' + $_.CommandLine }"
        )
        out = subprocess.run(
            ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
             "-Command", ps],
            capture_output=True, timeout=15,
        )
        # Windows PowerShell 输出是 GBK 编码（中文系统默认 OEM 代码页），
        # 不能用 text=True(UTF-8) 解码，否则中文 profile 路径处崩 UnicodeDecodeError
        raw = (out.stdout or b"")
        try:
            text = raw.decode("gbk", errors="replace")
        except Exception:
            text = raw.decode("utf-8", errors="replace")
        for line in text.splitlines():
            line = line.strip()
            if "|" not in line:
                continue
            pid_str, cmd = line.split("|", 1)
            cmd_norm = cmd.replace("/", "\\").lower()
            # 只杀命令行里 user-data-dir 精确包含该 profile 的 chrome
            if f"--user-data-dir=\"{norm}\"" in cmd_norm or \
               f"--user-data-dir={norm}" in cmd_norm:
                try:
                    subprocess.run(
                        ["taskkill", "/F", "/T", "/PID", pid_str],
                        capture_output=True, timeout=10,
                    )
                    killed += 1
                except Exception:
                    continue
    except Exception as e:
        logger.warning(f"[BCC-004] " + f"[open-browser] 清理孤儿 profile 持有进程失败: {e}")
    return killed


@router.post("/{name}/ensure-bcc")
async def ensure_bcc_ep(name: str):
    """确保该账号 BCC 运行（会员体系 v0.37.0 前端入口）。

    前端账号管理页「启动凭证守护」改走本端点：backend 进程内 spawn 的
    BCC 自带 DY_MEMBER/DY_MEMBER_KEY 环境变量，能解密会员空间内的
    加密凭证；Rust 直 spawn 不带会员环境，会因主密钥不可用而失败。
    幂等：已在运行直接返回 ok。
    """
    # 2026-09-14 v0.43.8：用户显式启动 → 清除停止态，恢复自动拉起。
    try:
        from auto_dm.accounts import bcc_mark_user_stopped as _mark
        _mark(False)
    except Exception:
        pass
    try:
        # 用户点按钮 → 豁免启动冷静期（2026-09-13）
        st = acct_core.ensure_bcc(name, wait_ready=True, timeout=60,
                                  skip_cooldown=True)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "msg": f"BCC 拉起异常: {e}"}
    return {"ok": bool(st.get("ok")), "port": st.get("port"), "msg": st.get("msg", "")}


@router.post("/{name}/ensure-recv")
async def ensure_recv_ep(name: str):
    """确保该账号 recv_daemon 运行（会员体系 v0.37.0 前端入口）。

    同 ensure-bcc：backend 进程内 spawn 自带会员环境变量。
    """
    from auto_dm.daemon_launcher import ensure_daemons_for
    try:
        r = ensure_daemons_for(name, wait=True, skip_cooldown=True)
        ok = bool(r.get("recv"))
        return {"ok": ok, "msg": "私信守护已就绪" if ok else "私信守护拉起失败，请查看日志"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "msg": f"私信守护拉起异常: {e}"}


@router.post("/{name}/open-browser")
async def open_fingerprint_browser(name: str, req: Request) -> ScanLoginResponse:
    """查看/操作登录态：把该账号的【常驻 BCC 容器就地切为有头可见】。

    2026-09-12 根治（用户明确需求：「打开浏览器的目的是能直观看到登录态」）：

    旧实现在这里另起一个**有头 Chromium 实例**指向同一 profile，与常驻的无头
    BCC **抢 SingletonLock**，后启动者拿到失效页面 —— 实测事故：
      · BCC 12:43 启动 → 探活 uid 与历史会话不符 → BCC-014/AUTH-050
      · 紧接着 BCC-016「拒绝写入 .env：疑似幽灵 uid」→ 凭证回写被拦
      · 昵称捕获 0 个（页面根本没登录态）
      · 用户此后手动打开的浏览器又反抢 profile，BCC 更不可用
    而用户真正的诉求只是「看一眼登录态」，不该付出一份 profile 冲突的代价。

    新实现：**不另起实例**。先确保 BCC 在跑（懒加载），再 POST 它的 /show
    让**同一个容器**以有头模式重启 context。于是：
      · 用户看到的窗口就是 BCC 自己 → 登录态真实、非副本；
      · 保活心跳 / cookie 刷新 / 凭证回写链路**全程不中断**（不再需要停守护）；
      · profile 始终单实例持有，零锁冲突。

    关闭窗口不会结束容器（那是 BCC 的窗口）；如需恢复无头省资源，
    调 `/api/accounts/{name}/hide-browser`。
    """
    env_path = acct_core.env_path_of(name)
    if not os.path.exists(os.path.dirname(env_path)):
        return ScanLoginResponse(ok=False, msg=f"账号 {name} 不存在")
    # ══════════════════════════════════════════════════════════════════════
    # 2026-09-19 v0.43.98【用户定拍板的边界契约】
    #
    #   ① 凭证有效 + 有任务在跑  → **禁止**打开有头浏览器（有头是观测态，
    #      不是运行态；运行中切可见会造成环境跳变、打断任务）
    #   ② 凭证失效               → **允许并引导**：所有任务暂停，由用户
    #      双击打开有头浏览器作为观测态，判断/恢复凭证
    #
    # 判据必须用真实校验（verify_account），不能用「端口在不在」推断
    # （端口在 ≠ 凭证有效，这是本项目的历史假成功来源）。
    # ══════════════════════════════════════════════════════════════════════
    try:
        _v = acct_core.verify_account(name, timeout=6, dm_loopback=False,
                                      auto_fix=False)
        _wp_level = (_v.get("wp") or {}).get("level")
        _cred_ok = (_wp_level == "ok")
    except Exception as _e_v:
        # 校验本身失败 → 保守放行（用户显式查看登录态的正当路径不该被拦）
        logger.debug(f"[open-browser] 账号 {name} 凭证校验失败，放行打开: {_e_v}")
        _cred_ok = False
        _wp_level = "unknown"

    if _cred_ok:
        # 引擎运行态：用 AutoDM 单例（request.app.state.adm）的真实 is_running。
        # 注意：不能用「守护端口在不在」推断 —— 端口在 ≠ 引擎在跑。
        _running = False
        try:
            _adm = getattr(req.app.state, "adm", None)
            _running = bool(getattr(_adm, "is_running", False))
        except Exception:
            _running = False
        if _running:
            logger.info(
                f"[open-browser] 账号 {name} 凭证有效且引擎运行中 → "
                f"拒绝切有头（有头是观测态；如需停止任务请先暂停引擎）")
            return ScanLoginResponse(
                ok=False,
                msg=f"凭证有效且引擎正在运行，已阻止打开有头浏览器"
                    f"（运行中切可见会造成环境跳变并打断任务）。"
                    f"请先暂停引擎，或在账号页点「停止守护」后再查看。")
        logger.info(
            f"[open-browser] 账号 {name} 凭证有效且引擎未运行 → 允许打开有头观测")
    else:
        # 凭证失效：按契约暂停全部任务，并引导用户在有头窗口恢复凭证。
        # AutoDM.stop/pause 是 async，这里在线程池执行以免阻塞事件循环
        # （本路由是 async，直接 await 会与引擎协程交错）。
        logger.warning(
            f"[ACC-025] " + f"[open-browser] 账号 {name} 凭证失效（wp={_wp_level}）"
            f"→ 暂停全部任务并打开有头浏览器供观测/重新授权")
        try:
            _adm = getattr(req.app.state, "adm", None)
            if _adm is not None and hasattr(_adm, "pause"):
                import asyncio as _aio
                _aio.get_event_loop().run_in_executor(
                    None, lambda: _aio.run(_adm.pause()))
                logger.info(f"[open-browser] 账号 {name} 引擎已暂停（凭证失效）")
        except Exception as _e_stop:
            logger.warning(f"[ACC-026] " + f"[open-browser] 账号 {name} 引擎暂停失败"
                f"（不阻塞打开浏览器）: {_e_stop}")
    bport = acct_core.browser_daemon_port(name)
    # 1) 确保 BCC 在运行（懒加载；已在跑则立即返回）
    if not acct_core._port_open(bport, timeout=0.3):
        try:
            st = acct_core.ensure_bcc(name, wait_ready=True, skip_cooldown=True)
            if not st.get("ok"):
                return ScanLoginResponse(
                    ok=False,
                    msg=f"拉起浏览器容器失败（{st.get('msg')}），请稍后重试")
        except Exception as e:  # noqa: BLE001
            logger.error(f"[BCC-030] " + f"[open-browser] 账号 {name} 拉起容器失败: {e}")
            return ScanLoginResponse(ok=False, msg=f"拉起浏览器容器失败: {e}")

    # 2) 就地切为有头可见（不另起实例、不停守护）
    #    2026-09-25 v0.44.67：显式声明 intent=observe（用户观测态）。
    #    这是**人**在看窗口，不是引擎校验 —— 置位后禁止自动关闭/自动转无头。
    try:
        data = json.dumps({"visible": True, "intent": "observe"}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{bport}/show", data=data,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=180) as resp:
            out = json.loads(resp.read().decode("utf-8", "replace") or "{}")
        if not out.get("ok"):
            return ScanLoginResponse(ok=False, msg=f"切换可见模式失败: {out.get('msg')}")
    except Exception as e:  # noqa: BLE001
        logger.error(f"[BCC-031] " + f"[open-browser] 账号 {name} 切换可见模式失败: {e}")
        return ScanLoginResponse(ok=False, msg=f"切换可见模式失败: {e}")

    # ══════════ 2026-09-25 v0.44.67【假阳性根治】══════════════════════════
    # 原实现：/out 返回 ok 就宣称「已显示该账号浏览器窗口」——
    # 但 /show 是**异步受理**，后台重建可能失败（实测 BCC-058），
    # 于是用户看到「成功」弹窗而屏幕上没有窗口。
    #
    # 现在据返回态如实表述：
    #   settled=True  → 已达成可见（窗口确实在）
    #   settled=False → 仅受理/切换中，明确告知"尚未就绪，请稍候"
    # 绝不把「受理」说成「已显示」。
    changed = out.get("changed")
    settled = out.get("settled")
    switching = out.get("switching")
    # P2-②（H-22 审计 idx6）：BCC 旧版本返回可能**缺 settled 键**，
    # 此时按「尚未达成」处理并诚实告知，而非默认当成功。
    if settled is not True and settled is not False:
        hint = ("（BCC 未返回达成态 `settled`，无法确认是否已就绪；"
                "请以实际窗口为准）")
    elif settled is True:
        hint = "（窗口已就绪）"
    elif switching or changed:
        hint = ("（已受理，正在切换为有头窗口 —— 冷启动约 1~3 分钟，"
                "**尚未就绪**；请稍候并以实际窗口为准）")
    else:
        hint = "（容器已是可见模式）"
    # H-20：把达成态**结构化**透出（此前只在 msg 文案里，前端读不到）。
    return ScanLoginResponse(
        ok=True,
        settled=(settled is True),
        switching=bool(switching or changed) and settled is not True,
        msg=f"已请求显示该账号浏览器窗口 · {name}{hint}"
            f"（凭证保活未中断）")


@router.post("/{name}/scan")
async def scan_login(name: str, body: dict | None = None) -> ScanLoginResponse:
    """更新登录凭证（默认**打开有头指纹浏览器·用户手动操作**）。

    2026-09-29（方案2）：默认走 ADR-017 之前的手动路径 —— 打开有头指纹浏览器，
    用户自己在窗口里完成扫码/验证码/滑块，凭证自动写回；**不再默认自动出二维码**。
    body.mode="rpa" 时改走 ADR-017 的 RPA 备用路径（自动出二维码 PNG，可经 IM 推送）。
    前端轮询 /scan-status 获取进度。
    """
    env_path = acct_core.env_path_of(name)
    if not os.path.exists(os.path.dirname(env_path)):
        return ScanLoginResponse(ok=False, msg=f"账号 {name} 不存在")
    mode = str((body or {}).get("mode", "") or "").strip().lower()
    force_rpa = mode in ("rpa", "qr-rpa")
    # 同一账号已有扫码在跑则直接返回
    prev = _scan_state.get(name)
    if prev and prev.get("running"):
        return ScanLoginResponse(ok=True, msg=f"账号 {name} 已在更新凭证中，请在指纹浏览器完成登录")
    _scan_state.setdefault(name, {})["_force_rpa"] = force_rpa
    t = threading.Thread(target=_do_scan, args=(name,), daemon=True)
    t.start()
    err = _wait_scan_error(name)
    if err:
        return ScanLoginResponse(ok=False, msg=f"更新凭证失败: {err}")
    if force_rpa:
        return ScanLoginResponse(ok=True, msg=f"已启动扫码（RPA 备用路径），请用抖音 App 扫描二维码 · {name}")
    return ScanLoginResponse(
        ok=True, msg=f"已打开有头指纹浏览器，请在其中完成登录/验证（凭证将自动写回）· {name}")


# ══════════════════════════════════════════════════════════════════════
#  F4：短信验证码登录（RPA）—— 状态机 + code_provider 通道（2026-09-26）
# ══════════════════════════════════════════════════════════════════════
#
#  【通道设计（D1 = R1-c 本机 Web 前端输入）】
#  `do_sms_login` 只接受一个 `code_provider()` 回调，**不接触明文渠道**（SoC）。
#  本层用 `threading.Event` + `_sms_code_box[name]` 做一次性传递：
#     前端 POST /sms-code  →  写入 box + set()  →  阻塞中的 code_provider 取到并返回
#  超时（默认 300s）未提交 ⇒ code_provider 返回 "" ⇒ do_sms_login 按失败处理。

_sms_code_box: dict[str, dict] = {}      # name -> {"code": str, "event": Event}


def _make_code_provider(name: str, timeout_s: int = 300):
    """构造 code_provider：阻塞等待前端提交验证码。"""
    def _provider() -> str:
        box = _sms_code_box.get(name)
        if not box:
            return ""
        ev = box["event"]
        _scan_state.setdefault(name, {})
        _scan_state[name]["stage"] = "waiting_code"
        _scan_state[name]["needCode"] = True
        logger.info(f"[scan] 账号 {name} 等待前端提交短信验证码（{timeout_s}s）")
        got = ev.wait(timeout=timeout_s)
        _scan_state[name]["needCode"] = False
        if not got:
            logger.warning(f"[ACC-034] [scan] 账号 {name} 等待验证码超时（{timeout_s}s）")
            return ""
        code = (box.get("code") or "").strip()
        logger.info(f"[scan] 账号 {name} 已收到验证码（长度 {len(code)}）")
        _scan_state[name]["stage"] = "code_received"
        return code
    return _provider


def _do_sms_scan(name: str, phone: str):
    """后台线程：RPA 短信验证码登录（ADR-017 / H-30 F4）。"""
    st = _scan_state.setdefault(name, {})
    st.update({"running": True, "done": False, "loggedIn": False,
               "error": "", "path": "sms", "stage": "starting",
               "needCode": False})
    _own = None
    try:
        from services.browser_gate import ProfileOwnership as _Own
        _own = _Own(name, "sms_login")
        _own.__enter__()
    except Exception as _e_own:  # noqa: BLE001
        logger.warning(f"[ACC-035] [scan] 账号 {name} 未取得 profile 所有权锁: {_e_own}")
        _own = None
    try:
        _quit_browser_daemon(name)
        env_path = acct_core.env_path_of(name)
        from auto_dm import login_remote as _lr
        st["stage"] = "sending_code"
        out = asyncio.run(_lr.do_sms_login(
            env_path=env_path, phone=phone,
            code_provider=_make_code_provider(name),
            headless=True, timeout_s=90))
        st["stage"] = out.get("stage", "")
        st["loggedIn"] = bool(out.get("ok"))
        if not out.get("ok"):
            st["error"] = out.get("reason", "") or "短信登录未成功"
            logger.warning(f"[ACC-036] [scan] 账号 {name} 短信登录失败"
                           f"（stage={out.get('stage')}）: {st['error']}")
    except Exception as e:  # noqa: BLE001
        logger.error(f"[ACC-037] [scan] 账号 {name} 短信登录异常: {e}")
        st["error"] = f"{type(e).__name__}: {e}"
    finally:
        st["running"] = False
        st["done"] = True
        st["needCode"] = False
        _sms_code_box.pop(name, None)
        # 守护回拉（与 /scan 一致：在锁内把 BCC 拉回）
        try:
            from auto_dm.daemon_launcher import ensure_daemons_for
            ensure_daemons_for(name, wait=False)
        except Exception as _e_rd:  # noqa: BLE001
            logger.debug(f"[scan] 账号 {name} 守护回拉跳过: {_e_rd}")
        if _own is not None:
            _own.__exit__(None, None, None)


@router.get("/qr-image")
async def qr_image(path: str):
    """下发 RPA 登录二维码 PNG（本地绝对路径 → 字节）。

    🔴 安全：二维码落在临时目录（绝对路径），**不能**在前端直拼 `file://`
    （受浏览器限制且不走鉴权）。本端点做三重校验后才下发：
      ① 必须以 `rpa_scan_` 前缀的临时目录为根（**白名单**，防任意文件读取）
      ② 解析后仍在该根内（防 `..` 目录穿越）
      ③ 必须是 `.png` 且真实存在
    """
    import tempfile
    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    root = os.path.realpath(tempfile.gettempdir())
    try:
        real = os.path.realpath(path)
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="非法路径")
    # ①② 白名单 + 目录穿越防护
    if not real.startswith(root + os.sep) and real != root:
        raise HTTPException(status_code=403, detail="路径不在允许的临时目录内")
    base = os.path.basename(real)
    if not base.lower().endswith(".png"):
        raise HTTPException(status_code=400, detail="仅支持 PNG")
    # ③ 必须是本流程自己产出的目录（rpa_scan_ 前缀）
    parent = os.path.basename(os.path.dirname(real))
    if not parent.startswith("rpa_scan_"):
        raise HTTPException(status_code=403, detail="非二维码临时目录")
    if not os.path.isfile(real):
        raise HTTPException(status_code=404, detail="二维码不存在或已过期")
    return FileResponse(real, media_type="image/png", filename=base)


@router.post("/{name}/sms-code")
async def submit_sms_code(name: str, body: dict):
    """F4 通道（R1-c）：前端提交短信验证码 → 唤醒阻塞中的 code_provider。"""
    code = str((body or {}).get("code", "")).strip()
    box = _sms_code_box.get(name)
    if not box:
        return {"ok": False, "msg": f"账号 {name} 当前没有在等待验证码"}
    if not code:
        return {"ok": False, "msg": "验证码为空"}
    box["code"] = code
    box["event"].set()
    logger.info(f"[scan] 账号 {name} 验证码已提交（前端 → 后端通道）")
    return {"ok": True, "msg": "验证码已提交"}


@router.post("/{name}/sms-login")
async def sms_login(name: str, body: dict) -> ScanLoginResponse:
    """F4：启动短信验证码登录（后台线程），立即返回。

    流程：发码 → 前端轮询 /scan-status（needCode=true 时展示输入框）
    → 用户提交 POST /sms-code → RPA 填码登录。
    """
    phone = str((body or {}).get("phone", "")).strip()
    if not phone:
        return ScanLoginResponse(ok=False, msg="缺少手机号")
    env_path = acct_core.env_path_of(name)
    if not os.path.exists(os.path.dirname(env_path)):
        return ScanLoginResponse(ok=False, msg=f"账号 {name} 不存在")
    prev = _scan_state.get(name)
    if prev and prev.get("running"):
        return ScanLoginResponse(ok=True, msg=f"账号 {name} 已有登录流程在跑")
    # 一次性通道（新流程即重置，避免旧验证码串台）
    _sms_code_box[name] = {"code": "", "event": threading.Event()}
    threading.Thread(target=_do_sms_scan, args=(name, phone), daemon=True).start()
    return ScanLoginResponse(ok=True, msg=f"已启动短信登录，请查收验证码并输入")


# ══════════════════════════════════════════════════════════════════════
#  统一「更新凭证」入口（2026-09-28，ADR-021 · DSSCC-BCC-004）
# ══════════════════════════════════════════════════════════════════════
# 【为什么需要】用户报「更新凭证怎么默认变成短信更新了」。
#   代码事实：账号卡片上**并列** `刷新凭证`（→ /scan）与 `短信登录`（→ /sms-login）
#   两颗按钮，**无主次、无自动分流**；而 ADR-017 §2.3 拍板的
#   「按账号状态自动判断，不是用户选择」（状态 A 全新账号 → 优先二维码；
#   状态 B 已有账号凭证过期 → 只用验证码）**从未被实现** ——
#   `状态 A / 状态 B` 只存在于 `login_remote.py` 的注释里，全仓零调用点。
#   两条路径各自还有独立缺陷（扫码出码锚点失效、短信手机号锚点已不存在），
#   导致用户看到「默认变短信、还更新不了」。
#
# 【本入口】= 分流契约的唯一落地点：
#   ① 探测**账号当前状态**（`uid_identity_verdict`：有解密权 ⟺ 会话被承认
#      且身份未漂移）—— 这是既有权威判据，不自造；
#   ② 状态 A（全新 / 判定不成立）⇒ 走**扫码**（码可 IM 推送，人在哪都能扫）；
#      状态 B（判定成立、仅签名过期）⇒ 走**短信验证码**（免扫码）；
#   ③ 两条路径都失败 ⇒ `needChoice=True` 如实上报，由界面让用户选 ——
#      绝不静默假装某条路是「默认」。
#   `mode="qr" / "sms"` 为**显式覆盖**，保留既有按钮的直达能力（单写者：同一批码）。
_login_method_hint: dict[str, str] = {}   # name -> "qr" | "sms"（最近一次分流依据，仅排障用）


def _probe_account_state(name: str) -> dict:
    """探测账号当前状态，供「更新凭证」分流。只读、不产生任何平台请求。

    `uid_identity_verdict` 的权威签名是 4 元组 `(state, reason, label, detail)`：
      state=True  ⇒ 状态 B（会话被承认且身份未漂移）⇒ 走短信验证码；
      state=False ⇒ 已确证失效（无凭证 / 未登录 / 身份漂移）⇒ 走二维码；
      state=None  ⇒ 取不到证据 ⇒ **诚实降级为走二维码**（不假装知道状态）。
    """
    from auto_dm import accounts as _acct
    try:
        env_path = acct_core.env_path_of(name)
        res = _acct.uid_identity_verdict(name, env_path)
        state, reason = (res[0], res[1]) if isinstance(res, tuple) else (None, "unknown")
    except Exception as e:  # noqa: BLE001
        logger.debug(f"[update-login] 账号 {name} 状态探测失败（按状态 A 处理）: {e}")
        state, reason = None, "unknown"
    return {"state": state, "verdict": reason or "unknown", "ok": state is True}


# 同一批「分流码」共用的改动（单写者：本批码只在本文件、且不与并发写者重叠）
@router.post("/{name}/update-login")
async def update_login(name: str, body: dict | None = None) -> ScanLoginResponse:
    """**统一「更新凭证」入口**：默认**用户手动**，RPA 扫码/短信仅作显式备用。

    2026-09-29（方案2 · 用户拍板）：默认**不再**按账号状态自动分流到固定模板，
    而是打开有头指纹浏览器由用户自己登录（ADR-017 之前的旧行为）。
    body（均可省略）：
      - mode: 省略 ⇒ 手动（有头浏览器，用户自己登录）；
              "qr"/"rpa" ⇒ RPA 备用：自动出二维码 PNG（可经 IM 推送）；
              "sms" ⇒ RPA 备用：短信验证码（需同时给 phone）。
      - phone: "sms" 备用路径所需手机号

    返回 msg 会**如实标注所用路径**，前端据此渲染（不猜、不假装成功）。
    """
    body = body or {}
    mode = str(body.get("mode", "") or "").strip().lower()
    if mode == "rpa":
        mode = "qr"
    if mode and mode not in ("qr", "sms"):
        return ScanLoginResponse(ok=False, msg=f"未知 mode={mode}（只允许 qr / sms，或省略走手动）")
    phone = str(body.get("phone", "") or "").strip()

    env_path = acct_core.env_path_of(name)
    if not os.path.exists(os.path.dirname(env_path)):
        return ScanLoginResponse(ok=False, msg=f"账号 {name} 不存在")

    if not mode:
        # 2026-09-29（方案2）：默认**手动**（打开有头指纹浏览器由用户自己登录）。
        # 旧 ADR-017 自动分流（状态 B→短信 / 其余→扫码）退居为显式备用，不再自动选路。
        _login_method_hint[name] = "manual"
        logger.info(f"[update-login] 账号 {name} → 手动（有头浏览器，用户自行登录）")
        return await scan_login(name)

    stt = _probe_account_state(name)
    _login_method_hint[name] = f"{mode}:{stt['verdict']}"
    logger.info(f"[update-login] 账号 {name} 显式备用路径 → {mode}"
                f"（state={stt['verdict']}, 依据={_login_method_hint[name]}）")

    if mode == "sms":
        if not phone:
            # 自动判为短信但缺手机号 ⇒ **如实上报需用户输入**，不静默改走扫码
            return ScanLoginResponse(
                ok=False,
                msg=f"短信备用路径需要手机号，请提供后重试"
                    f"（state={stt['verdict']}）")
        r = await sms_login(name, {"phone": phone})
        if r.ok:
            r.msg = f"已按显式备用路径【短信验证码】· {name}：{r.msg}"
        return r

    r = await scan_login(name, {"mode": "rpa"})
    if r.ok:
        r.msg = f"已按显式备用路径【扫码】· {name}：{r.msg}"
    return r


@router.get("/{name}/scan-status")
async def scan_status(name: str):
    """扫码状态查询（替代间接推断）"""
    st = _scan_state.get(name, {})
    return {
        "name": name,
        "running": st.get("running", False),
        "done": st.get("done", False),
        "loggedIn": st.get("loggedIn", False),
        "error": st.get("error", ""),
        # 2026-09-26 · ADR-017 / H-30 接线：RPA 路径会产出二维码 PNG 绝对路径，
        # 供前端展示（老 enrich_auth 路径无图 ⇒ 为空串，前端按此判有无）。
        "qrPng": st.get("qrPng", ""),
        "decoded": st.get("decoded", False),
        # 实际生效的登录路径（"rpa" / "legacy"）—— 便于排障归因，不做控制。
        "path": st.get("path", ""),
        # 2026-09-26 · F4 短信登录状态机：前端据此决定 UI。
        #   stage: starting / sending_code / waiting_code / code_received / …
        #   needCode=True ⇒ 展示验证码输入框并 POST /sms-code
        "stage": st.get("stage", ""),
        "needCode": st.get("needCode", False),
        "rejected": st.get("rejected", ""),
        "captureReport": st.get("captureReport", ""),
    }


@router.post("/{name}/role")
async def set_role(name: str, body: SetRoleRequest):
    # body.role 是 AccountRole(str) 枚举，str() 取 "watch"/"send"/"both"
    return {"ok": True, "name": name, "role": str(body.role)}


@router.post("")
async def add_account(body: AddAccountRequest):
    try:
        acct_core.add_account(body.name)
        return {"ok": True, "name": body.name}
    except ValueError as e:
        logger.warning(f"[ACC-004] " + f"[accounts] add_account 失败: {e}")
        return {"ok": False, "error": str(e), "name": body.name}


@router.delete("/{name}")
async def remove_account(name: str):
    try:
        acct_core.remove_account(name)
        return {"ok": True, "name": name}
    except ValueError as e:
        logger.warning(f"[ACC-005] " + f"[accounts] remove_account 失败: {e}")
        return {"ok": False, "error": str(e), "name": name}


def _quit_daemon_http(port: int) -> bool:
    """向守护 HTTP /quit 端口发停止请求，守护自身会 os._exit(0)。

    返回 True 表示请求成功送达（守护即将退出），False 表示端口不可达（守护未运行）。
    这是停止守护的【首选】方式，不依赖 Rust SidecarManager 的进程 label 精确匹配。
    """
    if not port:
        return False
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/quit", data=b"", method="POST"
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            resp.read()
        return True
    except Exception:
        return False


@router.post("/{name}/hide-browser")
async def hide_fingerprint_browser(name: str) -> ScanLoginResponse:
    """恢复该账号 BCC 容器为纯无头（省资源、减少风控暴露）。

    与 open-browser 配对：open 切可见（用户看登录态），hide 切回无头。
    同样**不重启实例**，只是让容器以无头模式重启 context，保活链路不中断。
    """
    bport = acct_core.browser_daemon_port(name)
    if not acct_core._port_open(bport, timeout=0.3):
        return ScanLoginResponse(ok=True, msg=f"账号 {name} 容器未运行（本就无窗口）")
    try:
        data = json.dumps({"visible": False}).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{bport}/show", data=data,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=60) as resp:
            out = json.loads(resp.read().decode("utf-8", "replace") or "{}")
        # P2-②（H-22 审计 idx7 · 实跑复现）：hide **不是**同步切换。
        # 当容器处于有头可见态时，hide 与 show 走**同一条异步后台重建路径**
        # （bcc_audit.py 的 `_switching=True` 分支），返回
        # `{ok:True, settled:False, switching:True, changed:True}` —— 窗口重建
        # 可能失败（BCC-058）。原实现把「BCC 已受理」当成「已切回」
        # （`settled=bool(ok)` / `switching=False`），重新引入本项目刚修完的**假阳性**。
        # 现按 BCC 真实返回如实透出：已达成才报 settled，否则如实说「切换中/未就绪」。
        _settled = out.get("settled")
        _switching = bool(out.get("switching") or out.get("changed"))
        if _settled is True:
            _hint = "（已切回无头）"
        elif _switching:
            _hint = "（已受理，正在切回无头 —— 尚未就绪；请稍候以实际状态为准）"
        else:
            _hint = "（已受理，尚未确认达成；请以实际状态为准）"
        return ScanLoginResponse(ok=bool(out.get("ok")),
                                 settled=(_settled is True),
                                 switching=bool(_switching and _settled is not True),
                                 msg=f"已请求恢复无头模式 · {name}{_hint}")
    except Exception as e:  # noqa: BLE001
        logger.error(f"[BCC-032] " + f"[hide-browser] 账号 {name} 恢复无头失败: {e}")
        return ScanLoginResponse(ok=False, msg=f"恢复无头失败: {e}")


@router.post("/{name}/stop-browser")
async def stop_browser_daemon(name: str):
    """停止该账号的凭证守护（经由守护自身 HTTP /quit 端口，不依赖 Rust label 匹配）。"""
    # 2026-09-14 v0.43.8：用户主动停止 → 记录停止态，
    # 之后所有自动路径（ensure_bcc / ensure_daemons_for / browser_gate）
    # 一律不再拉起，直到用户显式启动。
    try:
        from auto_dm.accounts import bcc_mark_user_stopped as _mark
        _mark(True)
        logger.info("[api] 用户已停止 BCC：自动拉起已禁用"
                    "（需从界面显式启动才恢复）")
    except Exception as _e:
        logger.warning(f"[api] 记录 BCC 停止态失败: {_e}")
    bport = acct_core.browser_daemon_port(name)
    if not acct_core._port_open(bport, timeout=0.3):
        return {"ok": True, "wasRunning": False, "msg": f"凭证守护 {name} 未运行"}
    ok = _quit_daemon_http(bport)
    return {"ok": True, "wasRunning": True, "stopped": ok,
            "msg": f"已向凭证守护 {name} 发送停止请求"}


@router.post("/{name}/stop-recv")
async def stop_recv_daemon(name: str):
    """停止该账号的私信守护（经由守护自身 HTTP /quit 端口，不依赖 Rust label 匹配）。"""
    rport = acct_core.recv_daemon_port(name)
    if not acct_core._port_open(rport, timeout=0.3):
        return {"ok": True, "wasRunning": False, "msg": f"私信守护 {name} 未运行"}
    ok = _quit_daemon_http(rport)
    return {"ok": True, "wasRunning": True, "stopped": ok,
            "msg": f"已向私信守护 {name} 发送停止请求"}


_RECAP_LANDING = "https://www.douyin.com/chat?isPopup=1"


@router.post("/{name}/auto-recapture")
async def auto_recapture(name: str) -> ScanLoginResponse:
    """私信凭证失效自动重新捕获（手动触发入口）。

    后端发送链路检测到凭证失效（三件套缺失 / INVALID_REQUEST）时会自动调用
    auto_dm.accounts.auto_recapture 在后台拉起指纹浏览器打开 chat?isPopup=1 重新授权，
    本路由供前端“立即处理验证”按钮或手动触发使用，行为与自动触发一致。
    """
    env_path = acct_core.env_path_of(name)
    if not os.path.exists(os.path.dirname(env_path)):
        return ScanLoginResponse(ok=False, msg=f"账号 {name} 不存在")
    # 直接走后台重捕获（复用 send_target 自动触发同一条路径，带 5 分钟节流）
    try:
        acct_core.auto_recapture(name, landing_url=_RECAP_LANDING)
        return ScanLoginResponse(
            ok=True,
            msg=f"已拉起指纹浏览器重新捕获私信凭证（{_RECAP_LANDING}）· {name}",
        )
    except Exception as e:
        return ScanLoginResponse(ok=False, msg=f"自动重新捕获失败: {e}")


@router.get("/{name}/proxy-status")
async def proxy_status(name: str):
    """账号代理状态查询（借鉴 OpenBrowser egress check 的只读轻量版）。

    返回 {configured, masked, error}：
      - configured: 该账号 .env 是否配置了 DY_PROXY；
      - masked: 脱敏后的代理地址（日志/前端展示用，绝不回传明文凭据）；
      - error: DY_PROXY 配置格式错误信息（无则空串）。
    纯读 .env 单行，零网络请求、零浏览器操作，可被前端随列表轮询。
    """
    env_path = acct_core.env_path_of(name)
    from auto_dm.vbrowser import parse_proxy_config, _mask_proxy
    # 环境门阀模式（node/system/direct）由配置显式决定，一并回传供前端回填
    mode, node_url, err = parse_proxy_config(env_path)
    if err:
        return {"configured": False, "masked": "", "error": err, "mode": mode or ""}
    mode = mode or "direct"
    if mode == "node":
        return {"configured": True, "masked": _mask_proxy(node_url or ""), "error": "", "mode": "node"}
    if mode == "system":
        return {"configured": True, "masked": "系统代理", "error": "", "mode": "system"}
    return {"configured": False, "masked": "", "error": "", "mode": "direct"}


@router.post("/{name}/proxy-test")
async def proxy_test(name: str, req: Request) -> dict:
    """真实探测该账号【当前代理配置】下的出口 IP 与归属地。

    接收 {type, host, port, user, pass}（未保存的临时表单值，便于"先测后存"）：
      - type=direct → 走本机 IP（显式禁用代理）
      - type=system → 走系统代理
      - socks5/http/https → 走该独立节点
    说明：用 urllib 按模式探测（与浏览器启动同一套环境决策），不走浏览器，
    避免"测试即拉起浏览器"的副作用；三态均返回真实 IP + 国家。
    """
    try:
        body = await req.json()
    except Exception:
        body = {}
    ptype = str(body.get("type") or "direct").strip().lower()
    host = str(body.get("host") or "").strip()
    port = str(body.get("port") or "").strip()
    user = str(body.get("user") or "").strip()
    pwd = str(body.get("pass") or "").strip()

    # 组装临时环境（供 probe_egress_ip_direct 读取，不落盘、不污染账号配置）
    if ptype == "direct":
        mode, node = "direct", ""
    elif ptype == "system":
        mode, node = "system", ""
    elif ptype in ("socks5", "socks4", "http", "https"):
        if not host or not port:
            return {"ok": False, "error": "请先填写主机地址与端口"}
        if ptype.startswith("socks"):
            node = f"{ptype}://{host}:{port}"
        else:
            from urllib.parse import quote
            node = (f"{ptype}://{quote(user)}:{quote(pwd or '')}@{host}:{port}"
                    if user else f"{ptype}://{host}:{port}")
        mode = "node"
    else:
        return {"ok": False, "error": f"不支持的类型: {ptype}"}

    from auto_dm.vbrowser import probe_egress_ip_direct
    # 2026-09-17 修补（OCR 审查 HIGH）：原实现写进程级环境变量
    # DY_PROXY_TEST_MODE/NODE 再调用，在 async 路由里这段跨 await 窗口
    # 会被并发请求互相覆盖 → 可能把 A 账号的代理节点泄漏进 B 账号的探测
    # （跨账号配置串味）。现改为**显式传参**，不再触碰 os.environ。
    # 同时：探测是同步阻塞调用（timeout=12/retries=2），放线程池执行，
    # 避免阻塞事件循环（原实现直接调用，最长可卡 ~30s）。
    import asyncio as _aio

    r = await _aio.get_running_loop().run_in_executor(
        None, lambda: probe_egress_ip_direct(mode=mode, node=node))
    r["type"] = ptype
    return r


@router.post("/{name}/proxy")
async def save_proxy(name: str, req: Request) -> ScanLoginResponse:
    """保存账号代理配置（写 .env.enc 的 DY_PROXY）。

    接收 {type, host, port, user, pass, testUrl}：
      - type=direct：清除 DY_PROXY（账号走直连，强制 --no-proxy-server 防误走系统代理）
      - type=socks5/http/https：组装 DY_PROXY URL 写入，指纹浏览器启动时经
        _playwright_proxy_param 注入，实现按账号 IP 隔离（国内/国外节点按需）。
    会员空间内 .env 加密存 .env.enc（write_env_file 自动处理）。
    """
    try:
        body = await req.json()
    except Exception:
        return ScanLoginResponse(ok=False, msg="请求体解析失败")
    acct = acct_core.current_name()
    env_path = acct_core.env_path_of(name)
    if not env_path or not os.path.exists(os.path.dirname(env_path)):
        return ScanLoginResponse(ok=False, msg=f"账号 {name} 不存在")
    ptype = str(body.get("type") or "").strip().lower()
    host = str(body.get("host") or "").strip()
    port = str(body.get("port") or "").strip()
    user = str(body.get("user") or "").strip()
    pwd = str(body.get("pass") or "").strip()
    if ptype == "direct":
        # 不走代理：显式写 MODE=direct（走本机 IP，豁免代理软件端口），清节点
        from services.member_ctx import write_env_file
        write_env_file(env_path, {"DY_PROXY_MODE": "direct", "DY_PROXY": None}, merge=True)
        # 2026-09-18：代理配置变更 = 环境定义变更，清除旧基线（下次扫码重建），
        # 防运行期比对误报漂移。
        try:
            from services.env_baseline import clear_baseline
            clear_baseline(name)
        except Exception:
            pass
        return ScanLoginResponse(ok=True, msg="已设为「不走代理」（走本机 IP，豁免代理端口）")
    if ptype == "system":
        # 走系统代理：显式写 MODE=system，清节点（由 vbrowser 读系统代理落地）
        from services.member_ctx import write_env_file
        write_env_file(env_path, {"DY_PROXY_MODE": "system", "DY_PROXY": None}, merge=True)
        # 2026-09-18：代理配置变更 = 环境定义变更，清除旧基线（下次扫码重建），
        # 防运行期比对误报漂移。
        try:
            from services.env_baseline import clear_baseline
            clear_baseline(name)
        except Exception:
            pass
        return ScanLoginResponse(ok=True, msg="已设为「系统代理」（跟随本机系统代理设置）")
    if ptype not in ("socks5", "socks4", "http", "https"):
        return ScanLoginResponse(ok=False, msg=f"不支持的代理类型: {ptype}")
    if not host or not port:
        return ScanLoginResponse(ok=False, msg="代理类型非直连时必须提供 host 和 port")
    # 组装 URL：socks 不带认证（Chromium 限制），http(s) 带认证
    if ptype.startswith("socks"):
        url = f"{ptype}://{host}:{port}"
    else:
        if user:
            from urllib.parse import quote
            url = f"{ptype}://{quote(user)}:{quote(pwd or '')}@{host}:{port}"
        else:
            url = f"{ptype}://{host}:{port}"
    # 校验格式
    from auto_dm.vbrowser import parse_proxy_env
    from services.member_ctx import write_env_file
    try:
        # 独立节点：显式写 MODE=node + 节点 URL（环境门阀由该模式决定）
        write_env_file(env_path, {"DY_PROXY_MODE": "node", "DY_PROXY": url}, merge=True)
        val, err = parse_proxy_env(env_path)
        if err:
            # 回滚：清掉写坏的（连模式一并回滚为不走代理）
            write_env_file(env_path, {"DY_PROXY_MODE": "direct", "DY_PROXY": None}, merge=True)
            return ScanLoginResponse(ok=False, msg=f"代理格式校验失败: {err}")
    except Exception as e:
        return ScanLoginResponse(ok=False, msg=f"写入代理配置失败: {e}")
    from auto_dm.vbrowser import _mask_proxy
    # 2026-09-18：代理配置变更 = 环境定义变更，清除旧基线（下次扫码重建），
    # 防运行期比对误报漂移。
    try:
        from services.env_baseline import clear_baseline
        clear_baseline(name)
    except Exception:
        pass
    return ScanLoginResponse(ok=True, msg=f"代理配置已保存 · {name}（{_mask_proxy(url)}）")
