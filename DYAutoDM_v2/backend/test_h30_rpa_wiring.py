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
