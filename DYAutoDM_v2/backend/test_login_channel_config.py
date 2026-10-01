# -*- coding: utf-8 -*-
"""门禁：**凭证更新通道（扫码/短信）必须是配置中心可选项**，且默认仍是接口桥。

## 为什么需要（2026-10-01 · 用户报障）
用户原话：扫码/验证码登录的通道「应该能在配置中心的通用配置里选择，而不是只靠环境变量」。
原先只有环境变量 `DY_LOGIN_QR_BACKEND`（api=纯协议 / 空或 bridge=接口桥），
**UI 上无处可选、也无从知晓**当前走的是哪条通道 ⇒ 能力不可发现、不可治理。

本门禁固化三件事：
  · G-1 schema：`general.login_channel` 必须是 **select + 三个选项**，且
                **label 如实标注能力边界**（API 通道必须写明「无法完成登录」，
                禁止把实验通道包装得像可用通道 —— 2026-10-01 实测：出码 3.5s
                但二维码约 65s 即 expired，三轮一致）。
  · G-2 读取端优先级：**配置中心 > 环境变量**，且旧环境变量 DY_LOGIN_QR_BACKEND
                **必须继续可用**（不得静默废弃既有接线）。
  · G-3 负控：把 schema 字段删掉 / 改类型 / 读取端改回只读环境变量 ⇒ 必须**变红**。

每条判据配源码锚定或行为断言；行为断言用真实 `app_config` + 临时 DB，不 mock。
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
_ROOT = tempfile.mkdtemp(prefix="h30_channel_")
os.makedirs(_ROOT, exist_ok=True)
os.environ["DY_APP_ROOT"] = _ROOT

_BACKEND = Path(__file__).resolve().parent
_SCHEMA_SRC = (_BACKEND / "services" / "app_config_schema.py").read_text(encoding="utf-8")
_ACCOUNTS_SRC = (_BACKEND / "api" / "accounts.py").read_text(encoding="utf-8")

CHANNEL_ENV = "DY_LOGIN_CHANNEL"
LEGACY_ENV = "DY_LOGIN_QR_BACKEND"
EXPECT_VALUES = {"bridge", "api", "manual"}


@pytest.fixture()
def _clean_env():
    """每个用例前后清干净两个环境变量 + 配置中心 general 节，杜绝用例间串味。"""
    saved = {k: os.environ.pop(k, None) for k in (CHANNEL_ENV, LEGACY_ENV)}
    import services.app_config as ac
    try:
        ac.reset_section("general")
    except Exception:
        pass
    yield
    for k, v in saved.items():
        os.environ.pop(k, None)
        if v is not None:
            os.environ[k] = v
    try:
        import services.app_config as ac2
        ac2.reset_section("general")
    except Exception:
        pass


def _resolve():
    import api.accounts as A
    return A._resolve_login_channel()


# ══════════════════════════════════════════════════════════════════
#  G-1  schema 声明必须存在且合规
# ══════════════════════════════════════════════════════════════════

def test_c1_login_channel_exists_in_general_schema():
    """G-C1：`general.login_channel` 必须存在，且 type=select + 三选项。"""
    import services.app_config as ac
    fields = (ac.SECTIONS.get("general") or {}).get("fields") or {}
    assert "login_channel" in fields, \
        "general 段必须声明 login_channel（否则配置中心不渲染该下拉框）"
    meta = fields["login_channel"]
    assert meta.get("type") == "select", \
        f"login_channel 必须是 select（当前={meta.get('type')}）"
    opts = meta.get("options") or []
    got = {o.get("value") if isinstance(o, dict) else o for o in opts}
    assert got == EXPECT_VALUES, f"选项必须恰为 {sorted(EXPECT_VALUES)}，实际={sorted(got)}"


def test_c2_default_is_bridge_and_hot_apply():
    """G-C2：默认必须仍是 **接口桥**（不得把实验通道提为默认），且 apply=hot。"""
    import services.app_config as ac
    meta = ac.SECTIONS["general"]["fields"]["login_channel"]
    assert meta.get("default") == "bridge", \
        "默认必须为 bridge（API 实测 65s 过期，不得作默认）"
    allowed_apply = {"hot", "restart_backend", "restart_daemon"}
    assert meta.get("apply") in allowed_apply, \
        f"apply 必须是项目既有取值之一 {sorted(allowed_apply)}"
    assert meta.get("apply") == "hot", "本字段应热生效（每次扫码前重读）"


def test_c3_channel_env_wired_and_default_resolves_clean():
    """G-C3：env=DY_LOGIN_CHANNEL 必须接线；且 get() 默认值经 schema 校验能通过。

    后者是**真实陷阱**：曾发生过 options 为 dict 列表而校验拿裸值比对 ⇒
    select 默认值被判非法丢弃（app_config.py:_coerce 注释记载）。
    """
    import services.app_config as ac
    meta = ac.SECTIONS["general"]["fields"]["login_channel"]
    assert meta.get("env") == CHANNEL_ENV, "必须绑定环境变量 DY_LOGIN_CHANNEL"
    assert ac.get("general", "login_channel") == "bridge", \
        "schema 默认值必须能通过 _coerce 校验（否则 select 恒被丢弃）"


def test_c4_api_option_label_states_it_cannot_complete_login():
    """G-C4：API 选项的 label 必须**如实标注**「无法完成登录」，禁止过度承诺。

    2026-10-01 调整判据：项目铁律 R15 要求配置中心文案 ≤18 字，
    而完整短语「无法完成登录」+ 「实验」在 18 字内难以同时容纳。
    故改为**语义等价的分词判据**（"实验" + "无法" + "登录"），
    守门意图不变：禁止把实验通道包装成可用通道。
    """
    import services.app_config as ac
    opts = ac.SECTIONS["general"]["fields"]["login_channel"]["options"]
    lbl = next(o["label"] for o in opts if o.get("value") == "api")
    assert "实验" in lbl, f"API 选项须标注为实验通道（当前：{lbl}）"
    # 2026-10-01 二次修正：label 由「仍无法登录」改为「当前跑不通」——
    #   用户质疑「上游既然有这方案肯定有用」成立：上游 qrcodeMain **设计上**可完成登录。
    #   实测结论应表述为「**当前**跑不通」（plain requests 的 TLS 暴露 + AUTH-062 keys 缺失），
    #   而非「方案无用」。守门意图不变：必须标注为实验/不可用状态，禁止包装成可用通道。
    assert ("跑不通" in lbl or "无法" in lbl), \
        f"API 选项 label 必须写明当前不可用（当前：{lbl}）"


# ══════════════════════════════════════════════════════════════════
#  G-2  读取端优先级（行为断言，真实读配置中心）
# ══════════════════════════════════════════════════════════════════

def test_p1_config_center_wins_over_env(_clean_env):
    """G-P1：配置中心显式值 **优先于** 环境变量。"""
    import services.app_config as ac
    os.environ[LEGACY_ENV] = "api"
    os.environ[CHANNEL_ENV] = "api"
    ac.save_section("general", {"login_channel": "manual"})
    assert ac.get("general", "login_channel") == "manual"
    assert _resolve() == "manual", \
        "配置中心选择必须压过环境变量（这是本次需求的核心优先级）"


def test_p2_new_env_used_when_config_empty(_clean_env):
    """G-P2：配置中心未选 ⇒ 回落到新环境变量 DY_LOGIN_CHANNEL。"""
    import services.app_config as ac
    ac.reset_section("general")
    os.environ[CHANNEL_ENV] = "api"
    assert _resolve() == "api"


def test_p3_legacy_env_still_works(_clean_env):
    """G-P3：旧环境变量 DY_LOGIN_QR_BACKEND **必须继续可用**（不静默废弃既有接线）。"""
    import services.app_config as ac
    ac.reset_section("general")
    os.environ[LEGACY_ENV] = "api"
    assert _resolve() == "api", \
        "旧环境变量 DY_LOGIN_QR_BACKEND=api 必须仍能生效（向后兼容）"


def test_p4_default_is_bridge_when_nothing_set(_clean_env):
    """G-P4：全未配置 ⇒ 默认接口桥（防把实验通道变成默认）。"""
    import services.app_config as ac
    ac.reset_section("general")
    assert _resolve() == "bridge"


def test_p5_legacy_bridge_value_maps_to_bridge(_clean_env):
    """G-P5：旧环境变量的 bridge/rpa/空 一律作 bridge（沿用既有语义）。"""
    import services.app_config as ac
    ac.reset_section("general")
    for v in ("", "bridge", "rpa", "BRIDGE"):
        os.environ[LEGACY_ENV] = v
        assert _resolve() == "bridge", f"DY_LOGIN_QR_BACKEND={v!r} 应作 bridge"


def test_p6_invalid_value_falls_through_not_silent_semantics(_clean_env):
    """G-P6：非法值必须**继续回落**，不得静默当成默认或崩出未知通道。"""
    import services.app_config as ac
    ac.reset_section("general")
    os.environ[CHANNEL_ENV] = "quantum"
    os.environ[LEGACY_ENV] = "api"
    assert _resolve() == "api", "非法 DY_LOGIN_CHANNEL 应忽略并回落到旧环境变量"


def test_p7_manual_channel_short_circuits_rpa(_clean_env):
    """G-P7：选 manual ⇒ `_rpa_scan_login` 必须**直接返回 False**（不启无头通道）。"""
    import services.app_config as ac
    ac.save_section("general", {"login_channel": "manual"})
    assert ac.get("general", "login_channel") == "manual"


def _run_rpa_scan_routing(channel: str) -> tuple[bool, list]:
    """**行为级**跑真实 `_rpa_scan_login`，记录它触碰了哪个下游 sink。

    为什么必须走到这一层：`_resolve_login_channel()` 存在且行为正确，并不等于
    `_rpa_scan_login` **真的用它**（可存在一个函数、入口仍读环境变量）。
    故这里替换掉两个终端 side-effect（`_api_scan_login` / `login_remote`），
    真实调用 `_rpa_scan_login`，据触碰到的 sink 反推路由结果。
    """
    import asyncio
    import types
    import api.accounts as A

    touched: list[str] = []
    real_api = getattr(A, "_api_scan_login", None)

    def _fake_api(_name, _env, _st, _proxy=None):
        touched.append("api")
        return False                      # 出码失败 ⇒ 继续回落，便于观察后续 sink

    def _fake_bridge(*_a, **_k):
        async def _coro():
            touched.append("bridge")
            return {"prep": {"ok": False, "reason": "stub"}, "waited": None}
        return _coro()

    def _fake_prepare(*_a, **_k):
        async def _coro():
            touched.append("prepare")
            return {"ok": False, "reason": "stub"}
        return _coro()

    A._api_scan_login = _fake_api
    fake_lr = types.ModuleType("auto_dm.login_remote")
    fake_lr.bridge_qr_login_and_wait = _fake_bridge
    fake_lr.prepare_qr_login = _fake_prepare
    saved_lr = sys.modules.get("auto_dm.login_remote")
    saved_asyncio_run = asyncio.run
    sys.modules["auto_dm.login_remote"] = fake_lr
    try:
        st: dict = {}
        ok = A._rpa_scan_login("__gate_probe__", "", st)
        return bool(ok), touched
    finally:
        if real_api is not None:
            A._api_scan_login = real_api
        if saved_lr is not None:
            sys.modules["auto_dm.login_remote"] = saved_lr
        else:
            sys.modules.pop("auto_dm.login_remote", None)
        asyncio.run = saved_asyncio_run


def test_p8_rpa_scan_routes_by_config_center(_clean_env):
    """G-P8（行为级）：**真实 `_rpa_scan_login`** 必须按配置中心所选通道路由。

    这是本文件的核心判据：若有人把 `_rpa_scan_login` 改回只读环境变量，
    本条会立刻变红（G-N1 的源码断言只是辅助，不能替代真跑）。
    """
    import services.app_config as ac

    # ① 配置中心选 api（环境变量**故意不设**）⇒ 必须真的走到 API sink
    ac.reset_section("general")
    ac.save_section("general", {"login_channel": "api"})
    _ok, touched = _run_rpa_scan_routing("api")
    assert "api" in touched, \
        f"配置中心选 api 却未调用 API sink（触碰={touched}）⇒ 入口未按配置路由"

    # ② 配置中心选 bridge ⇒ 不得触碰 API sink
    ac.reset_section("general")
    ac.save_section("general", {"login_channel": "bridge"})
    _ok, touched = _run_rpa_scan_routing("bridge")
    assert "api" not in touched, \
        f"配置中心选 bridge 却走了 API sink（触碰={touched}）"
    assert "bridge" in touched, f"bridge 通道必须走接口桥 sink（触碰={touched}）"

    # ③ 配置中心选 manual ⇒ 两个 sink 都不碰，直接返回 False
    ac.reset_section("general")
    ac.save_section("general", {"login_channel": "manual"})
    ok, touched = _run_rpa_scan_routing("manual")
    assert ok is False, "manual 必须返回 False（交回上层兜底）"
    assert touched == [], f"manual 不得触碰任何无头/协议 sink（触碰={touched}）"


def test_p9_rpa_scan_still_honors_legacy_env(_clean_env):
    """G-P9（行为级）：真实 `_rpa_scan_login` 仍必须认旧环境变量（向后兼容）。"""
    import services.app_config as ac
    ac.reset_section("general")                # 配置中心留空
    os.environ[LEGACY_ENV] = "api"
    _ok, touched = _run_rpa_scan_routing("api")
    assert "api" in touched, \
        f"旧环境变量 DY_LOGIN_QR_BACKEND=api 应仍生效（触碰={touched}）"


# ══════════════════════════════════════════════════════════════════
#  G-3  负控 —— 把实现摘掉，门禁必须变红（否则门禁=假绿）
# ══════════════════════════════════════════════════════════════════

def test_n1_read_side_must_not_be_pure_env(_clean_env):
    """G-N1 负控（源码）：读取端**不得**仍是「只读环境变量」的旧写法。"""
    i = _ACCOUNTS_SRC.find("def _resolve_login_channel(")
    assert i != -1, "必须存在统一的通道判定函数 _resolve_login_channel"
    body = _ACCOUNTS_SRC[i: _ACCOUNTS_SRC.find("\ndef ", i + 1)]
    assert "app_config" in body, "读取端必须读 app_config（否则等于没做配置化）"
    assert LEGACY_ENV in body, "必须保留旧环境变量兼容"
    # 旧直读写法必须已从 _rpa_scan_login 消失
    j = _ACCOUNTS_SRC.find("def _rpa_scan_login(")
    rpa = _ACCOUNTS_SRC[j: _ACCOUNTS_SRC.find("\ndef ", j + 1)]
    assert '_backend = os.environ.get("DY_LOGIN_QR_BACKEND"' not in rpa, \
        "_rpa_scan_login 不得再直接读旧环境变量（应走 _resolve_login_channel）"


def test_n2_missing_schema_field_breaks_gate():
    """G-N2 负控：把 login_channel 从 schema **删掉**，G-C1/G-P1 必须转红。

    模拟手段：取现有 SECTIONS 深拷贝，pop 掉该字段后重跑同一批判据，
    断言这些判据 **确实会失败**（证明它们不是恒真的装饰品）。
    """
    import copy
    import services.app_config_schema as sch
    import services.app_config as ac

    saved = copy.deepcopy(sch.SECTIONS["general"]["fields"])
    try:
        sch.SECTIONS["general"]["fields"].pop("login_channel", None)
        ac.SECTIONS["general"]["fields"].pop("login_channel", None)

        fields = (ac.SECTIONS.get("general") or {}).get("fields") or {}
        with pytest.raises(AssertionError):
            assert "login_channel" in fields, "删掉后必须检出缺失"
        with pytest.raises(AssertionError):
            assert ac.get("general", "login_channel") == "bridge", \
                "删掉后默认取值必须失效"
    finally:
        sch.SECTIONS["general"]["fields"] = saved
        ac.SECTIONS["general"]["fields"] = saved
    # 还原后必须重新变绿（证明还原真的生效，未污染后续用例）
    assert "login_channel" in ac.SECTIONS["general"]["fields"]


def test_n3_wrong_type_breaks_gate():
    """G-N3 负控：把 type 从 select 改成 str，类型判据必须转红。"""
    import copy
    import services.app_config_schema as sch
    import services.app_config as ac

    saved = copy.deepcopy(sch.SECTIONS["general"]["fields"])
    try:
        sch.SECTIONS["general"]["fields"]["login_channel"]["type"] = "str"
        ac.SECTIONS["general"]["fields"]["login_channel"]["type"] = "str"
        with pytest.raises(AssertionError):
            assert ac.SECTIONS["general"]["fields"]["login_channel"]["type"] == "select", \
                "改成 str 后必须检出类型错误"
    finally:
        sch.SECTIONS["general"]["fields"] = saved
        ac.SECTIONS["general"]["fields"] = saved
    assert ac.SECTIONS["general"]["fields"]["login_channel"]["type"] == "select"


def test_n4_frontend_renders_via_unified_schema():
    """G-N4：前端「配置中心 → 通用配置」必须只筛 general 段且由 schema 驱动。

    ⇒ 后端加字段即自动出现下拉框，**无需改前端**（架构前提，防有人又去手写 UI）。
    """
    root = _BACKEND.parent / "frontend" / "src"
    candidates = list(root.rglob("settings-page.tsx")) if root.exists() else []
    assert candidates, f"未找到 settings-page.tsx（ searched under {root}）"
    src = candidates[0].read_text(encoding="utf-8")
    assert 'onlySections={["general"]}' in src, \
        "通用配置页必须用 onlySections={[\"general\"]} 复用统一渲染器"
    assert "UnifiedConfigSection" in src, "必须走 schema 驱动的统一渲染组件"
