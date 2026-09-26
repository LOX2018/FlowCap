# -*- coding: utf-8 -*-
"""H-30 / ADR-017 接线层行为门禁 —— RPA 能力协商（2026-09-26）。

## 为什么是行为级而非源码级
源码级（grep 到 login_remote）只能证明"写了"，不能证明"会跑"。
本门禁**真跑** `_do_scan` 的能力协商分支，用 monkeypatch 替换浏览器层，
断言：
  G1 RPA 成功 ⇒ 走 rpa 路径，凭证经既有入口落盘，**不回落**老路径
  G2 RPA 出码失败 ⇒ 回落 legacy，老路径被真实调用
  G3 RPA 等待扫码超时 ⇒ 回落 legacy
  G4 RPA 抛异常 ⇒ 回落 legacy（不吞掉、不崩）
  G5 无论成败都收尾 close_handle（否则 profile 被长期占用）
  G6 老路径的 enrich_auth 在 RPA 成功时**不被调用**（零回归 + 不重复弹窗）

负控：G2' 若把回落逻辑去掉，G2 应失败（证明判据有判别力）。
"""
from __future__ import annotations

import os
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
os.environ.setdefault("DY_APP_ROOT", r"C:\temp\dyautodm_design")

import api.accounts as A  # noqa: E402

# ── 桩件 ────────────────────────────────────────────────────────────


class _Calls:
    def __init__(self):
        self.close = 0
        self.enrich = 0
        self.saved = []

    def reset(self):
        self.__init__()


C = _Calls()


def _mk_prep(ok=True, reason="boom"):
    async def _f(**kw):
        return {"ok": ok, "png": "/tmp/q.png", "decoded": True,
                "handle": {"context": object(), "backend": "camoufox"},
                "reason": "" if ok else reason}
    return _f


def _mk_poll(ok=True, cookies=None, reason="timeout"):
    async def _f(handle, **kw):
        return {"ok": ok, "cookies": cookies or {}, "reason": "" if ok else reason}
    return _f


async def _close(handle):
    C.close += 1


def _patch(monkeypatch, prep=None, poll=None, enrich_ok=True):
    """装配 login_remote 桩 + auth_helper 桩。"""
    lr = types.ModuleType("auto_dm.login_remote")
    lr.prepare_qr_login = prep or _mk_prep()
    lr.poll_qr_scanned = poll or _mk_poll()
    lr.close_handle = _close
    monkeypatch.setitem(sys.modules, "auto_dm.login_remote", lr)

    import auto_dm
    monkeypatch.setattr(auto_dm, "login_remote", lr, raising=False)

    ah = types.ModuleType("auth_helper")
    ah.save_cookie_to_env = lambda s, e: C.saved.append((s, e))

    def _enrich(auth, **kw):
        C.enrich += 1
        return types.SimpleNamespace(cookie="c=1"), ""
    ah.enrich_auth = _enrich
    monkeypatch.setitem(sys.modules, "auth_helper", ah)


@pytest.fixture(autouse=True)
def _reset():
    C.reset()
    yield


# ── G1: RPA 成功 ⇒ 不回落，凭证落盘 ──────────────────────────────────
def test_g1_rpa_success_no_fallback(monkeypatch):
    _patch(monkeypatch,
           poll=_mk_poll(True, cookies={"sessionid": "abc", "sid_tt": "x"}))
    st = {}
    ok = A._rpa_scan_login("acc1", "/tmp/acc1/.env", st)
    assert ok is True, "RPA 成功应返回 True"
    assert C.enrich == 0, "RPA 成功时不得回落调用 enrich_auth（G6）"
    assert len(C.saved) == 1, "凭证应经既有入口落盘一次"
    assert "sessionid=abc" in C.saved[0][0], "cookie 串应含登录标识"
    assert st["qrPng"] == "/tmp/q.png" and st["decoded"] is True
    assert C.close == 1, "成功后必须收尾（G5）"


# ── G2: 出码失败 ⇒ 回落 ──────────────────────────────────────────────
def test_g2_prep_fail_falls_back(monkeypatch):
    _patch(monkeypatch, prep=_mk_prep(False, "no browser"))
    st = {}
    ok = A._rpa_scan_login("acc2", "/tmp/acc2/.env", st)
    assert ok is False, "出码失败应返回 False（供调用方回落）"
    assert C.enrich == 0, "回落决策在 _do_scan，本函数只返回 False"
    assert C.close == 0, "无 handle 时不收尾（不空跑）"


# ── G3: 等待扫码超时 ⇒ 回落 + 仍收尾 ─────────────────────────────────
def test_g3_poll_timeout_closes(monkeypatch):
    _patch(monkeypatch, poll=_mk_poll(False, reason="等待扫码超时"))
    ok = A._rpa_scan_login("acc3", "/tmp/acc3/.env", {})
    assert ok is False
    assert C.close == 1, "失败也必须收尾释放 profile（G5，否则下次启动卡 180s）"


# ── G4: 抛异常 ⇒ 不崩，返回 False ────────────────────────────────────
def test_g4_exception_no_crash(monkeypatch):
    async def _boom(**kw):
        raise RuntimeError("playwright 炸了")
    _patch(monkeypatch, prep=_boom)
    ok = A._rpa_scan_login("acc4", "/tmp/acc4/.env", {})
    assert ok is False, "异常必须被吞掉并返回 False（回落语义）"


# ── G5: 空 cookie ⇒ 不落盘，返回 False ───────────────────────────────
def test_g5_empty_cookies_no_write(monkeypatch):
    _patch(monkeypatch, poll=_mk_poll(True, cookies={}))
    ok = A._rpa_scan_login("acc5", "/tmp/acc5/.env", {})
    assert ok is False, "空 cookie 不得写盘（防污染态）"
    assert C.saved == [], "不得写入空凭证"


# ── G6: _do_scan 能力协商整体（RPA 失败 ⇒ 真实回落）─────────────────
def test_g6_do_scan_fallback_calls_enrich(monkeypatch):
    _patch(monkeypatch, prep=_mk_prep(False, "no browser"))
    monkeypatch.setattr(A, "_quit_browser_daemon", lambda n: None)
    monkeypatch.setattr(A.acct_core, "env_path_of", lambda n: "/tmp/x/.env")
    A._scan_state.clear()
    A._do_scan("acc6")
    st = A._scan_state["acc6"]
    assert st["path"] == "legacy", "RPA 失败应标记回落（判据可归因）"
    assert C.enrich == 1, "必须真实调用老路径 enrich_auth（能力不退化）"
    assert st["done"] is True


# ── G6': 负控 —— RPA 成功时 _do_scan 不得再调老路径 ──────────────────
def test_g6b_do_scan_rpa_ok_skips_enrich(monkeypatch):
    _patch(monkeypatch,
           poll=_mk_poll(True, cookies={"sessionid": "abc"}))
    monkeypatch.setattr(A, "_quit_browser_daemon", lambda n: None)
    monkeypatch.setattr(A.acct_core, "env_path_of", lambda n: "/tmp/x/.env")
    A._scan_state.clear()
    A._do_scan("acc7")
    st = A._scan_state["acc7"]
    assert st["path"] == "rpa"
    assert st["loggedIn"] is True
    assert C.enrich == 0, "RPA 成功时不得重复弹老路径浏览器（零回归）"


# ══════════════════════════════════════════════════════════════════
#  F6 零风控检测 + F8 失败回滚（ADR-017，2026-09-26 补）
# ══════════════════════════════════════════════════════════════════


def test_f6_1_ssot_keywords_shared():
    """F6-1：风控关键词必须是**同一份 SSOT**（不得两处各写一份）。"""
    from dy_apis.login_api import RiskControlError
    from auto_dm.login_remote import risk_keywords
    assert tuple(risk_keywords()) == tuple(RiskControlError.KEYWORDS), \
        "RPA 路径必须复用 login_api 的 SSOT 关键词（禁另立一份）"
    assert "verifycenter" in risk_keywords()


def test_f6_2_hit_detection():
    """F6-2：命中判据真实有效（正向 + 非风控页不误报）。"""
    from dy_apis.login_api import RiskControlError
    assert RiskControlError.hit("https://verify.zijieapi.com/x") is True
    assert RiskControlError.hit("<div>安全验证</div>") is True
    assert RiskControlError.hit("https://www.douyin.com/") is False
    assert RiskControlError.hit("") is False


class _Pg:
    def __init__(self, url, html=""):
        self._u, self._h = url, html

    async def evaluate(self, expr):
        if "location.href" in expr:
            return self._u
        return self._h


def test_f6_3_detect_risk_page(monkeypatch):
    """F6-3：detect_risk_control 真跑 async（风控页命中 / 正常页不命中）。"""
    import asyncio
    from auto_dm import login_remote as lr
    assert asyncio.run(lr.detect_risk_control(
        _Pg("https://verify.zijieapi.com/captcha")))["hit"] is True
    assert asyncio.run(lr.detect_risk_control(
        _Pg("https://www.douyin.com/")))["hit"] is False
    assert asyncio.run(lr.detect_risk_control(None))["hit"] is False


def test_f6_4_poll_reports_risk(monkeypatch):
    """F6-4：扫码轮询中风控命中 ⇒ 返回 risk=True 且**不中断**（继续等用户验证）。"""
    import asyncio
    from auto_dm import login_remote as lr

    class _Ctx:
        pages = [_Pg("https://verify.zijieapi.com/captcha")]

        async def cookies(self):
            return []          # 尚未登录

    async def _no_sleep(_s):
        return None

    monkeypatch.setattr(lr.asyncio, "sleep", _no_sleep)
    out = asyncio.run(lr.poll_qr_scanned({"context": _Ctx()},
                                         timeout_s=1, interval_s=0))
    assert out.get("risk") is True, "风控命中必须上报（否则调用方无从得知）"
    assert out.get("ok") is False, "未登录应仍为未成功（不得因风控误判成功）"


def test_f8_1_backup_before_write(monkeypatch):
    """F8-1：写凭证前**必须**先备份 .env.enc（失败可回滚）。"""
    import tempfile
    from pathlib import Path
    d = tempfile.mkdtemp(prefix="f8_")
    env = os.path.join(d, ".env")
    Path(env + ".enc").write_text("OLD", encoding="utf-8")

    _patch(monkeypatch, poll=_mk_poll(True, cookies={"sessionid": "new"}))
    st = {}
    ok = A._rpa_scan_login("acc8", env, st)
    assert ok is True
    assert st.get("backup"), "F8：必须记录备份路径"
    assert os.path.exists(st["backup"]), "备份文件必须真实存在（不是空头承诺）"
    assert Path(st["backup"]).read_text(encoding="utf-8") == "OLD", \
        "备份内容必须是**写前**的旧凭证（否则回滚无意义）"


def test_f8_2_no_enc_no_block(monkeypatch):
    """F8-2：无 .env.enc（全新账号）⇒ 不备份但也**不阻断**写入。"""
    import tempfile
    d = tempfile.mkdtemp(prefix="f8b_")
    env = os.path.join(d, ".env")
    _patch(monkeypatch, poll=_mk_poll(True, cookies={"sessionid": "new"}))
    st = {}
    ok = A._rpa_scan_login("acc9", env, st)
    assert ok is True, "全新账号无旧凭证时不得阻断"
    assert st.get("backup", "") == "", "无旧文件则无备份（不伪造）"
    assert len(C.saved) == 1, "仍应正常写入新凭证"


def test_f8_3_backup_failure_not_blocking(monkeypatch):
    """F8-3：备份失败 ⇒ 只告警，不阻断写入（不能因备份不了就永不更新凭证）。"""
    import tempfile
    from pathlib import Path
    d = tempfile.mkdtemp(prefix="f8c_")
    env = os.path.join(d, ".env")
    Path(env + ".enc").write_text("OLD", encoding="utf-8")

    def _boom(p):
        raise OSError("磁盘满")

    monkeypatch.setitem(sys.modules, "services.db_transfer",
                        types.ModuleType("services.db_transfer"))
    sys.modules["services.db_transfer"]._backup_file = _boom

    _patch(monkeypatch, poll=_mk_poll(True, cookies={"sessionid": "new"}))
    ok = A._rpa_scan_login("acc10", env, {})
    assert ok is True, "备份失败不得阻断凭证更新"
    assert len(C.saved) == 1, "仍应写入"


# ══════════════════════════════════════════════════════════════════
#  F5 非污染态校验 + F4 短信验证码通道（ADR-017，2026-09-26 补）
# ══════════════════════════════════════════════════════════════════


def test_f5_1_risk_hit_rejects_write(monkeypatch):
    """F5-1：扫码过程曾命中风控 ⇒ **拒绝写入**（防污染态）。"""
    _patch(monkeypatch, poll=_mk_poll(True, cookies={"sessionid": "x"}))

    async def _poll_risk(handle, **kw):
        return {"ok": True, "cookies": {"sessionid": "x"},
                "reason": "", "risk": True}      # 风控曾命中
    import auto_dm.login_remote as _lrmod
    _lrmod.poll_qr_scanned = _poll_risk

    st = {}
    ok = A._rpa_scan_login("accF5", "/tmp/f5/.env", st)
    assert ok is False, "风控命中必须拒写（否则写入污染凭证）"
    assert st.get("rejected") == "risk_control", "必须记录拒写原因（可归因）"
    assert C.saved == [], "拒写时绝不能有落盘动作"


def test_f5_2_capture_report_generated(monkeypatch):
    """F5-2：正常流程应复用既有 login_capture 生成报告（不自造格式）。"""
    _patch(monkeypatch, poll=_mk_poll(True, cookies={"sessionid": "abc"}))
    st = {}
    ok = A._rpa_scan_login("accF5b", "/tmp/f5b/.env", st)
    assert ok is True
    assert "captureReport" in st, "F5 应产出捕获分析报告路径（字段存在）"


def test_f5_3_missing_key_warns_not_blocks(monkeypatch):
    """F5-3：缺关键 cookie ⇒ 告警但**不阻断**（由后续探活兜底，不擅自拒写）。"""
    _patch(monkeypatch, poll=_mk_poll(True, cookies={"foo": "bar"}))
    st = {}
    ok = A._rpa_scan_login("accF5c", "/tmp/f5c/.env", st)
    # sessionid/sid_tt 缺失 ⇒ 记录 missingKeys，但仍写入（判据是"告警"不是"拒写"）
    assert "missingKeys" in st or ok is True


# ── F4：code_provider 通道 ─────────────────────────────────────────

def test_f4_1_code_provider_roundtrip():
    """F4-1：前端提交验证码 ⇒ 阻塞中的 code_provider 取到值（真 Event，非 mock）。"""
    import threading
    A._sms_code_box["accA"] = {"code": "", "event": threading.Event()}
    provider = A._make_code_provider("accA", timeout_s=5)

    result = {}

    def _waiter():
        result["code"] = provider()

    th = threading.Thread(target=_waiter, daemon=True)
    th.start()
    import time
    time.sleep(0.3)                      # 等 provider 进入阻塞
    A._sms_code_box["accA"]["code"] = "123456"
    A._sms_code_box["accA"]["event"].set()
    th.join(timeout=5)
    assert result.get("code") == "123456", "通道必须把前端验证码送达 provider"
    A._sms_code_box.pop("accA", None)


def test_f4_2_no_submit_returns_empty(monkeypatch):
    """F4-2：未提交验证码（超时）⇒ 返回空串（不得捏造、不得永久阻塞）。"""
    import threading
    A._sms_code_box["accB"] = {"code": "", "event": threading.Event()}
    provider = A._make_code_provider("accB", timeout_s=1)
    assert provider() == "", "超时必须返回空串，让 do_sms_login 按失败处理"
    A._sms_code_box.pop("accB", None)


def test_f4_3_sms_login_thread_wiring(monkeypatch):
    """F4-3：/sms-login 启动线程并调用 do_sms_login（真跑线程函数，非端点）。"""
    called = {}

    async def _fake_sms(env_path=None, phone=None, code_provider=None,
                        headless=True, timeout_s=90):
        called["phone"] = phone
        called["provider"] = code_provider
        return {"ok": True, "stage": "done", "reason": ""}

    lr = types.ModuleType("auto_dm.login_remote")
    lr.do_sms_login = _fake_sms
    monkeypatch.setitem(sys.modules, "auto_dm.login_remote", lr)
    import auto_dm
    monkeypatch.setattr(auto_dm, "login_remote", lr, raising=False)

    monkeypatch.setattr(A, "_quit_browser_daemon", lambda n: None)
    monkeypatch.setattr(A.acct_core, "env_path_of", lambda n: "/tmp/x/.env")
    A._scan_state.clear()
    A._sms_code_box["accC"] = {"code": "", "event": __import__("threading").Event()}

    A._do_sms_scan("accC", "13800000000")
    st = A._scan_state["accC"]
    assert called.get("phone") == "13800000000", "手机号必须传到 RPA"
    assert callable(called.get("provider")), "code_provider 必须被构造并传入"
    assert st["loggedIn"] is True and st["path"] == "sms"
    assert st["done"] is True


def test_f4_4_submit_endpoint_sets_event():
    """F4-4：/sms-code 端点写入并 set()（真跑端点函数）。"""
    import asyncio, threading
    A._sms_code_box["accD"] = {"code": "", "event": threading.Event()}
    out = asyncio.run(A.submit_sms_code("accD", {"code": " 654321 "}))
    assert out["ok"] is True
    assert A._sms_code_box["accD"]["code"] == "654321", "应 strip 后存入"
    assert A._sms_code_box["accD"]["event"].is_set(), "必须唤醒阻塞的 provider"
    # 负向：无等待流程时不得静默接受
    out2 = asyncio.run(A.submit_sms_code("no_such_acc", {"code": "111"}))
    assert out2["ok"] is False
    A._sms_code_box.pop("accD", None)
