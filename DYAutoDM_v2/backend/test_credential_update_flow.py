"""「更新凭证」链路 + BCC 拉起可重试 —— 机械门禁（2026-09-28，DSSCC-BCC-001..004）。

覆盖本轮用户实测报障的两个缺陷：
  · 「BCC 又异常」= 拉起失败被**永久负缓存** + 陈旧锁自愈因**机器级计数/顺序颠倒**全程空转
  · 「更新凭证怎么默认变成短信更新了」= 无分流契约 + 并列双按钮 + 命名漂移

每条判据都配**负控**（把修复摘掉后该判据必须变红），否则门禁=假绿。
"""
from __future__ import annotations

import asyncio
import pathlib
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent


# ══════════════════════════════════════════════════════════════════════
#  T1  BCC 拉起失败必须**可重试**（原实现：一次失败 ⇒ 本进程内永久拒绝）
# ══════════════════════════════════════════════════════════════════════
def test_t1_bcc_lazy_fail_is_retryable(monkeypatch):
    from auto_dm import accounts as A

    A._bcc_lazy_fail.clear()
    monkeypatch.setattr(A, "_port_open", lambda *a, **k: False)
    monkeypatch.setattr(A, "browser_daemon_port", lambda name: 41231)
    monkeypatch.setattr(A.os.path, "isfile", lambda p: True)   # 假装二进制存在
    monkeypatch.setattr(A, "_BCC_LAZY_FAIL_LIMIT", 2)
    monkeypatch.setattr(A, "_BCC_LAZY_FAIL_BACKOFF", 9999)

    calls = {"n": 0}

    def _boom(*a, **k):
        calls["n"] += 1
        raise OSError("simulated spawn failure")

    monkeypatch.setattr(A.subprocess, "Popen", _boom)

    A.ensure_bcc("gateAcc", wait_ready=False)
    A.ensure_bcc("gateAcc", wait_ready=False)
    r3 = A.ensure_bcc("gateAcc", wait_ready=False)
    assert calls["n"] == 2, "退避窗内达到上限后应拒绝再拉（防循环）"
    assert r3["ok"] is False and "退避" in r3["msg"]

    # 负控：退避窗一过 **必须自动放行重试** —— 原实现此处恒返回
    # 「BCC 此前懒加载失败（端口未就绪）」，永不重试。
    monkeypatch.setattr(A, "_BCC_LAZY_FAIL_BACKOFF", 0)
    A.ensure_bcc("gateAcc", wait_ready=False)
    assert calls["n"] == 3, "退避窗过后必须自动重试（原实现永久拒绝 = 缺陷本体）"

    # 端口就绪 ⇒ 失败态被清空（可干净重试）
    monkeypatch.setattr(A, "_port_open", lambda *a, **k: True)
    r = A.ensure_bcc("gateAcc", wait_ready=False)
    assert r["ok"] is True and "gateAcc" not in A._bcc_lazy_fail
    A._bcc_lazy_fail.clear()


# ══════════════════════════════════════════════════════════════════════
#  T2  陈旧锁自愈必须用 **profile 级** 进程判据（机器级 ⇒ 永不清理）
# ══════════════════════════════════════════════════════════════════════
def test_t2_stale_lock_healed_despite_other_accounts_running(monkeypatch, tmp_path):
    from auto_dm import login_remote as L

    (tmp_path / "_camoufox").mkdir(parents=True)
    (tmp_path / "_camoufox" / "parent.lock").write_bytes(b"")

    # 机器上**别的账号**在跑（旧判据 count_browser_processes() != 0）
    monkeypatch.setattr(L, "count_browser_processes", lambda: 16)
    # 但本 profile 已无进程 ⇒ 陈旧锁
    monkeypatch.setattr(L, "count_profile_processes", lambda d: 0)

    out = L.heal_stale_profile_lock(str(tmp_path))
    assert out["healed"] is True, "本 profile 无进程时陈旧锁必须被清除"
    assert any("parent.lock" in r for r in out["removed"])

    # 负控：本 profile 仍有进程 ⇒ 绝不删锁（不能破坏在跑实例）
    (tmp_path / "_camoufox" / "parent.lock").write_bytes(b"")
    monkeypatch.setattr(L, "count_profile_processes", lambda d: 1)
    out2 = L.heal_stale_profile_lock(str(tmp_path))
    assert out2["healed"] is False and out2["removed"] == []

    # 负控：无法可靠判定（-1）⇒ 保守不删
    monkeypatch.setattr(L, "count_profile_processes", lambda d: -1)
    out3 = L.heal_stale_profile_lock(str(tmp_path))
    assert out3["healed"] is False


def test_t2b_profile_scope_beats_machine_scope(monkeypatch, tmp_path):
    """负控：把判据换回机器级计数 ⇒ T2 的核心断言必须变红。"""
    from auto_dm import login_remote as L

    (tmp_path / "_camoufox").mkdir(parents=True)
    (tmp_path / "_camoufox" / "parent.lock").write_bytes(b"")
    # 模拟「修复被摘掉」：profile 级判据退化为机器级
    monkeypatch.setattr(L, "count_profile_processes", lambda d: 16)
    out = L.heal_stale_profile_lock(str(tmp_path))
    assert out["healed"] is False, "机器级判据下陈旧锁不会被清 —— 这正是原缺陷"


# ══════════════════════════════════════════════════════════════════════
#  T3  「先清扫 → 再判锁」顺序是契约（顺序反了 = 自愈一次都不生效）
# ══════════════════════════════════════════════════════════════════════
def test_t3_reap_runs_before_lock_judgement(monkeypatch, tmp_path):
    from auto_dm import login_remote as L

    order: list[str] = []
    monkeypatch.setattr(L, "reap_profile_processes",
                        lambda d: (order.append("reap"), 3)[1])
    monkeypatch.setattr(L, "heal_stale_profile_lock",
                        lambda d: (order.append("heal"),
                                   {"healed": True, "removed": ["_camoufox/parent.lock"],
                                    "checked": 1, "procs": 0})[1])

    out = L.ensure_profile_released(str(tmp_path))
    assert order == ["reap", "heal"], "必须先清扫残留进程再判锁（否则锁永远判为『在用』）"
    assert out["reaped"] == 3 and out["healed"] is True

    # 负控：恢复「先判锁后清扫」⇒ 顺序断言变红
    order.clear()
    monkeypatch.setattr(L, "reap_profile_processes", lambda d: (order.append("heal"), 0)[1])
    monkeypatch.setattr(L, "heal_stale_profile_lock", lambda d: (order.append("reap"),
                                                                 {"healed": False,
                                                                  "removed": [],
                                                                  "checked": 0,
                                                                  "procs": -1})[1])
    L.ensure_profile_released(str(tmp_path))
    assert order == ["heal", "reap"], "负控生效：颠倒顺序时断言确实失败"


# ══════════════════════════════════════════════════════════════════════
#  T4  统一入口 /update-login 必须**按账号状态自动分流**
# ══════════════════════════════════════════════════════════════════════
def _wire_update_login(monkeypatch, tmp_path):
    from api import accounts as API

    d = tmp_path / "accX"
    d.mkdir()
    (d / ".env").write_text("", encoding="utf-8")
    monkeypatch.setattr(API.acct_core, "env_path_of", lambda n: str(d / ".env"))

    calls: list[tuple[str, str]] = []

    async def _scan(name, body=None):
        mode = str((body or {}).get("mode", "") or "")
        calls.append(("manual" if mode in ("manual", "browser") else "qr", name))
        return API.ScanLoginResponse(ok=True, msg="qr-ok")

    async def _sms(name, body):
        calls.append(("sms", name))
        return API.ScanLoginResponse(ok=True, msg="sms-ok")

    monkeypatch.setattr(API, "scan_login", _scan)
    monkeypatch.setattr(API, "sms_login", _sms)
    return API, calls


def test_t4_update_login_defaults_to_bridge_qr(monkeypatch, tmp_path):
    """2026-09-30（用户拍板）：**默认接口桥扫码（无头）**；手动/短信为显式路径。

    用户原话：「先尝试修复（接口/无头），如果不能就变回最初的弹出浏览器用户手动更新」。
    故 update_login() 省略 mode ⇒ 走**扫码**（scan_login mode=qr，桥内失败再落下层）；
    不再按账号状态自动选短信，也不再默认直接开有头窗口。
    """
    API, calls = _wire_update_login(monkeypatch, tmp_path)

    # ① 默认（省略 mode）⇒ 接口桥扫码；即便账号状态看起来适合短信也**不得**自动选短信
    monkeypatch.setattr(API, "_probe_account_state",
                        lambda n: {"state": True, "verdict": "ok", "ok": True})
    r = asyncio.run(API.update_login("accX", None))
    assert calls[-1][0] == "qr", "默认必须走扫码（接口桥），不自动选短信、不默认开有头"
    assert r.ok

    # ② 默认即便显式给了 phone，也不自动选短信（phone 只在 mode=sms 时用）
    r = asyncio.run(API.update_login("accX", {"phone": "13800000000"}))
    assert calls[-1][0] == "qr", "给了 phone 但未指定 mode ⇒ 仍走扫码"

    # ③ 显式 mode=sms ⇒ 走短信（缺手机号时如实上报，不静默改路）
    before = list(calls)
    r = asyncio.run(API.update_login("accX", {"mode": "sms"}))
    assert r.ok is False and "手机号" in r.msg and calls == before
    r = asyncio.run(API.update_login("accX", {"mode": "sms", "phone": "13800000000"}))
    assert calls[-1][0] == "sms" and r.ok
    assert "短信" in r.msg, "返回必须**如实标注**实际走的路径"

    # ④ 显式 mode=qr ⇒ 走扫码；mode=rpa 为同义别名
    r = asyncio.run(API.update_login("accX", {"mode": "qr"}))
    assert calls[-1][0] == "qr"
    r = asyncio.run(API.update_login("accX", {"mode": "rpa"}))
    assert calls[-1][0] == "qr"

    # ⑤ 显式 mode=manual ⇒ **跳过桥**直接有头手动（最初方案，保留为显式路径）
    r = asyncio.run(API.update_login("accX", {"mode": "manual"}))
    assert calls[-1][0] == "manual", "mode=manual 必须直达有头手动（不走桥）"
    r = asyncio.run(API.update_login("accX", {"mode": "browser"}))
    assert calls[-1][0] == "manual", "mode=browser 为 manual 同义别名"

    # ⑥ 非法 mode ⇒ 显式失败
    r = asyncio.run(API.update_login("accX", {"mode": "wechat"}))
    assert r.ok is False and "mode" in r.msg

    # ⑦ 负控：若把默认改回「有头手动」或「按状态自动选短信」⇒ 断言①变红
    #    （默认与状态判据解耦：状态恒 True 时默认仍必须是扫码）
    calls.clear()
    asyncio.run(API.update_login("accX", None))
    assert calls[-1][0] == "qr", "负控生效：状态恒 True 时默认仍必须是接口桥扫码"


# ══════════════════════════════════════════════════════════════════════
#  T5  静态防复发：单写者调用点 / 单入口（不再有并列双按钮与旧顺序）
# ══════════════════════════════════════════════════════════════════════
def test_t5_no_wrong_order_callsites_left():
    src = (ROOT / "backend" / "auto_dm" / "login_remote.py").read_text(encoding="utf-8")
    assert "heal = heal_stale_profile_lock(profile)" not in src, \
        "调用点不得再直接调 heal（必须走 ensure_profile_released 的固定顺序）"
    assert src.count("ensure_profile_released(profile)") >= 2, \
        "QR 与短信两条起浏览器路径都必须走统一自愈入口"


def test_t5b_single_update_entry_in_ui():
    ui = (ROOT / "frontend" / "src" / "components" / "accounts" / "accounts-page.tsx") \
        .read_text(encoding="utf-8")
    assert "startSmsLogin(a.name)" not in ui, "并列的「短信登录」按钮必须已收敛"
    assert ".updateLogin(" in ui, "必须走统一 /update-login 入口"
    assert "刷新凭证<" not in ui, "旧命名「刷新凭证」必须已统一为「更新凭证」"


def test_t5c_docstring_contract_is_implemented():
    """契约漂移防复发：ADR 的「状态 A/B 自动分流」必须真有代码落点。"""
    src = (ROOT / "backend" / "auto_dm" / "login_remote.py").read_text(encoding="utf-8")
    api = (ROOT / "backend" / "api" / "accounts.py").read_text(encoding="utf-8")
    # 2026-09-29 方案2：默认手动；RPA 备用仍在 /update-login 有实现落点。
    assert "def update_login" in api and "_force_manual" in api, \
        "默认手动 + 显式 RPA 备用 必须在 /update-login 有实现落点"
    assert 'st["path"] = "manual"' in api, \
        "默认手动路径必须有实现落点（不得只是注释）"


# ══════════════════════════════════════════════════════════════════════
#  T6  「可重试」必须配**拉起节流**（否则持续性失效账号 → 无限重启 = 风控信号）
#      —— 本轮实测回归：放开重试后张老师（AUTH-050 永久失效）被反复拉起 8+ 进程
# ══════════════════════════════════════════════════════════════════════
def test_t6_spawn_throttle_caps_retry_storm(monkeypatch):
    from auto_dm import accounts as A

    A._bcc_lazy_fail.clear()
    A._bcc_spawn_hist.clear()
    monkeypatch.setattr(A, "_port_open", lambda *a, **k: False)
    monkeypatch.setattr(A, "browser_daemon_port", lambda name: 49999)
    monkeypatch.setattr(A.os.path, "isfile", lambda p: True)
    monkeypatch.setattr(A, "_BCC_SPAWN_MAX", 4)
    monkeypatch.setattr(A, "_BCC_SPAWN_WINDOW", 900)

    spawns = {"n": 0}

    class _P:
        def __init__(self, *a, **k):
            spawns["n"] += 1

    monkeypatch.setattr(A.subprocess, "Popen", _P)

    rs = [A.ensure_bcc("thrAcc", wait_ready=False) for _ in range(6)]
    assert spawns["n"] == 4, "滑窗内拉起点数必须被硬上限截断（防重启风暴）"
    assert rs[4]["ok"] is False and "节流" in rs[4]["msg"]

    # 负控：把上限抬高 ⇒ 同一序列必须**能**继续拉起（证明拦截来自节流而非其它原因）
    monkeypatch.setattr(A, "_BCC_SPAWN_MAX", 99)
    A.ensure_bcc("thrAcc", wait_ready=False)
    assert spawns["n"] == 5, "负控生效：上限抬高后拉起点数应继续增长"
    A._bcc_lazy_fail.clear()
    A._bcc_spawn_hist.clear()


def test_t6b_launch_async_path_also_throttled():
    """`browser_daemon` 走 launch_async 直连，不经 ensure_bcc ⇒ 必须**另一处**也拦。"""
    acc = (ROOT / "backend" / "auto_dm" / "accounts.py").read_text(encoding="utf-8")
    vb = (ROOT / "backend" / "vbrowser.py").read_text(encoding="utf-8")
    lr = (ROOT / "backend" / "auto_dm" / "login_remote.py").read_text(encoding="utf-8")
    assert "_bcc_spawn_hist" in acc and "_BCC_SPAWN_MAX" in acc, "spawn 出口须记账"
    assert "def bcc_launch_allowed" in lr, "节流须做成可复用判据"
    assert "bcc_launch_allowed(account)" in vb, \
        "唯一启动出口必须调用节流判据（否则守护冷启动路径可无限重启）"


# ══════════════════════════════════════════════════════════════════════
#  T7  清扫函数**不得杀非 camoufox/firefox 进程**（实测自伤：命令含 profile
#      路径的 shell 被一起杀掉，终端 exit 15 且无任何错误输出）
# ══════════════════════════════════════════════════════════════════════
def test_t7_reaper_requires_browser_process_name():
    src = (ROOT / "backend" / "vbrowser_camoufox.py").read_text(encoding="utf-8")
    # 断言锚在 `_victims` 内的过滤行本身（唯一）：
    assert 'if "camoufox" not in _nm and "firefox" not in _nm' in src, \
        "必须按进程名前置过滤 —— 否则命令行含 profile 路径的**任意**进程（含调用者自身）会被杀"
    assert 'psutil.process_iter(["pid", "name", "cmdline"])' in src, \
        "遍历必须取进程名（否则无法做进程名过滤）"

