"""凭证失效处置门禁 —— 负控/正控（每条判据都要能变红）。

## 为什么要有这个测试

2026-10-05 用户实测报障：**手动更新凭证后仍持续触发重启风暴，凭证反复失效**。

根因：`core/sender.py::_maybe_auto_recapture` 与 `api/accounts.py::auto-recapture`
会走 `_quit_browser_daemon() → enrich_auth(force=True) → ensure_daemons_for()`，
每次都 **context 重建** = 抖音侧一次全新环境访问（风控面），且**完全绕开
run_keepalive 的熔断**，只受 5 分钟节流。

修复：默认改为只发 Windows 通知、不自动重建；配置项
`general.cred_expire_action`（notify 默认 / auto 回旧行为）控制。

## 判据

| ID | 判据 | 性质 |
|---|---|---|
| N1 | 默认态不自动开浏览器 | 正控 |
| N2 | env=auto 恢复自动（证明开关真生效，不是写死） | **负控** |
| N3 | 通知节流：600s 内同账号同原因只发 1 条 | 正控 |
| N4 | 默认态 sender **不调用** auto_recapture | 正控 |
| N5 | env=auto 时 sender 恢复调用（证明 N4 不是写死） | **负控** |
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# 测试隔离：数据根钉到临时目录（conftest 已兜底，此处显式更稳）
_TMP_ROOT = Path(os.environ.get("TEMP", "/tmp")) / "dy_cred_gate_root"
_TMP_ROOT.mkdir(parents=True, exist_ok=True)
os.environ["DY_APP_ROOT"] = str(_TMP_ROOT)
os.environ["DATA_DIR"] = str(_TMP_ROOT / "data")
os.environ["ACCOUNTS_DIR"] = str(_TMP_ROOT / "accounts")

from services import cred_notify as cn  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env():
    """每条用例前后清掉开关，避免用例间串味。"""
    os.environ.pop("DY_CRED_EXPIRE_ACTION", None)
    cn._last.clear()
    yield
    os.environ.pop("DY_CRED_EXPIRE_ACTION", None)


def test_n1_default_is_notify():
    """N1 正控：默认态不自动开浏览器。"""
    assert cn.should_auto_open_browser() is False, (
        f"默认应为 notify（不开浏览器），实际 action={cn._cred_expire_action()}")


def test_n2_env_auto_restores_old_behavior():
    """N2 负控：env=auto 必须能回到旧行为 —— 证明开关不是写死。"""
    os.environ["DY_CRED_EXPIRE_ACTION"] = "auto"
    assert cn.should_auto_open_browser() is True, (
        "env=auto 时应恢复自动开浏览器（否则说明开关失效/被写死为 False）")


def test_n3_notify_throttled(monkeypatch):
    """N3：600s 内同账号同原因只发 1 条（防刷爆通知栏）。"""
    import utils.win_notify as _wn

    sent = []
    # 埋雷打在**真正被 import 的模块**上（函数体内 from ... import 每次取该模块）
    monkeypatch.setattr(_wn, "notify_windows",
                        lambda title, message, **kw: sent.append(title) or True)
    cn.THROTTLE_SEC = 600.0
    r1 = cn.notify_cred_expired("acctA", "reasonA")
    r2 = cn.notify_cred_expired("acctA", "reasonA")
    assert len(sent) == 1, f"600s 内应只发 1 条，实发 {len(sent)} 条"
    assert r1 is True and r2 is False, f"首次={r1} 二次(应被节流)={r2}"


def test_n4_sender_does_not_auto_recapture_by_default(monkeypatch):
    """N4：默认态 sender 不调用 auto_recapture（风暴根治点）。"""
    import auto_dm.accounts as _acc
    import core.sender as _sender

    fired = []
    monkeypatch.setattr(_acc, "auto_recapture", lambda *a, **kw: fired.append(a))

    class _FakeAuth:
        account_name = "acctA"

    marker = (_sender._RECAP_MARKERS or ["凭证"])[0]
    _sender._maybe_auto_recapture(_FakeAuth(), f"xx{marker}xx")
    assert not fired, f"默认态不应调用 auto_recapture（实调 {len(fired)} 次）"


def test_n5_env_auto_restores_sender_trigger(monkeypatch):
    """N5 负控：env=auto 时 sender 恢复触发 —— 证明 N4 不是写死。"""
    import auto_dm.accounts as _acc
    import core.sender as _sender

    fired = []
    monkeypatch.setattr(_acc, "auto_recapture", lambda *a, **kw: fired.append(a))
    os.environ["DY_CRED_EXPIRE_ACTION"] = "auto"

    class _FakeAuth:
        account_name = "acctA"

    marker = (_sender._RECAP_MARKERS or ["凭证"])[0]
    _sender._maybe_auto_recapture(_FakeAuth(), f"xx{marker}xx")
    assert len(fired) == 1, f"env=auto 时应恢复调用（实调 {len(fired)} 次）"
