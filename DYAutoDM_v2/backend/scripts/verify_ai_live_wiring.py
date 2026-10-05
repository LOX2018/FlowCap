# -*- coding: utf-8 -*-
"""直播监听私信文案 AI 接线 + dm_pool 契约放宽 —— 验证脚本（v0.44.14）。

设计契约（本次改动要证明的四件事）：
  1. `/api/engine/start` 的请求体必须同时吃得下词库的**两种形态**
     （`list[str]` 旧形态 / `[{text,enabled}]` 直播间配置主形态），
     且对象形态的 `enabled` 位**不得被静默抹掉**；
  2. 派发器文案来源必须「AI 生成优先 → 词库回落」，任一环节失败一律回落，
     **绝不发空文案、绝不中断发送、绝不绕过风控闸门**；
  3. AI 是否接线由「账号绑定 Agent + scopes 含 live + enabled 且非 kb_only」判定，
     任一条不满足 → 不接线（保持改造前行为）；
  4. 新增错误码 SEND-038/039/040 必须带设计契约（铁律：没有契约的报错不允许提交）。

分层（对齐 references/verification_script_layering.md）：
  L1 契约层   —— 真 TaskConfig（纯模型，不启引擎、不碰浏览器）
  L2 调度层   —— 真 DispatchCenter._do_send（monkeypatch 发送出口，绝不真发）
  L3 判定层   —— 真 AutoDM._make_gen_dm_message（monkeypatch Agent/kv，零副作用）
  L4 错误码层 —— 真 errcode.lookup
  L5 前端层   —— 交由 `npx tsc -b`（本脚本只断言源码契约一致性）

用法（脚本自设环境，不依赖调用方 export）：
    python backend/scripts/verify_ai_live_wiring.py
退出码 0 = 全部通过。
"""
from __future__ import annotations

import os
import sys

# ---------------------------------------------------------------------------
# 环境门禁：本脚本只允许在「设备内固定数据根」上跑，且绝不指向别的环境
#   （铁律：脚本自设 DY_APP_ROOT，不靠调用方记忆；拒绝运行而不是靠自觉）
# ---------------------------------------------------------------------------
_DESIGN_ROOT = r"C:\temp\dyautodm_design"
_FORBIDDEN = (r"C:\temp\dyautodm_test",)
_here = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
if os.path.abspath(_DESIGN_ROOT) in [os.path.abspath(x) for x in _FORBIDDEN]:
    print("[FATAL] 环境门禁：数据根与禁用环境相同，拒绝运行")
    sys.exit(2)
os.environ.setdefault("DY_APP_ROOT", _DESIGN_ROOT)
os.environ["DY_APP_ROOT"] = _DESIGN_ROOT

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

FAIL: list[str] = []
PASS = 0


def chk(cond: bool, label: str, detail: str = "") -> None:
    global PASS
    if cond:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL.append(label)
        print(f"  [FAIL] {label}  {detail}")


def main() -> int:
    print("=" * 72)
    print("L1 契约层：TaskConfig.dm_pool 两种形态")
    print("=" * 72)
    from models.task import TaskConfig

    obj_pool = [
        {"text": "你好，我是唐律助理，可以留个联系~方式，唐律下播帮你分析", "enabled": True},
        {"text": "工友你好", "enabled": False},
        {"text": "我看下你的病例", "enabled": True},
    ]
    # 1.1 复现缺陷时的真实载荷必须通过（此前 422）
    try:
        c = TaskConfig(live_url="992931212705", max_target=100, interval=60.0,
                       delay="50,120", dm_pool=obj_pool, acct="测试账号")
        r = c.resolved()
        chk(True, "对象形态载荷不再 422（原缺陷载荷）")
        # 1.2 enabled 位必须保留（不得静默降级）
        chk(isinstance(r.dm_pool[0], dict) and r.dm_pool[1].get("enabled") is False,
            "对象形态保留 enabled=False（不被静默抹掉）",
            f"got={r.dm_pool!r}")
        chk([t.get("text") for t in r.dm_pool] == [t["text"] for t in obj_pool],
            "对象形态文案顺序/内容不失真")
    except Exception as e:  # noqa: BLE001
        chk(False, "对象形态载荷不再 422（原缺陷载荷）", f"{type(e).__name__}: {e}")

    # 1.3 旧形态（list[str]）零回归
    try:
        c2 = TaskConfig(live_url="x", dm_pool=["A", "B"]).resolved()
        chk(c2.dm_pool == ["A", "B"], "list[str] 旧形态零回归", f"got={c2.dm_pool!r}")
    except Exception as e:  # noqa: BLE001
        chk(False, "list[str] 旧形态零回归", str(e))

    # 1.4 别名 dmPool 路径同样吃对象
    try:
        c3 = TaskConfig(live_url="x", dmPool=obj_pool).resolved()
        chk(isinstance(c3.dm_pool[0], dict), "别名 dmPool 进入也保留对象形态")
    except Exception as e:  # noqa: BLE001
        chk(False, "别名 dmPool 进入也保留对象形态", str(e))

    print()
    print("=" * 72)
    print("L2 调度层：文案来源 AI 优先 / 词库回落（monkeypatch 发送出口，绝不真发）")
    print("=" * 72)
    import asyncio
    from core import dispatch as _d

    sent: list[str] = []

    async def _fake_send(auth, target, content):
        sent.append(content)
        return True, "stub"

    _orig_send = _d.send_target_async
    _d.send_target_async = _fake_send
    try:
        class _Auth:
            account_name = ""   # 空 = 不进入 dm_dispatch 分支（本脚本不碰真闸门）

        def _mk():
            return _d.DispatchCenter(auth=_Auth(), max_target=5,
                                     delay_range=(0, 0), interval=0.0,
                                     pick_dm_message=lambda: "词库文案")

        # 注意：_do_send 要求 records 里已有该 key（否则直接 return）——
        # 这是被测代码的正常前置，不是缺陷；探针必须先 _ensure_record，
        # 否则会伪造出「一条都没发」的假失败（本脚本开发中实际踩过）。
        # 2.1 AI 生成非空 → 用 AI 文案
        sent.clear()
        dc = _mk()

        async def _gen_ok(target):
            return "AI 生成的开场白"

        dc.gen_dm_message = _gen_ok
        dc._ensure_record("k1", {"user_id": "1", "nickname": "张三"})
        asyncio.run(dc._do_send("k1", {"user_id": "1", "nickname": "张三"}))
        chk(sent and sent[-1] == "AI 生成的开场白", "AI 生成非空 → 采用 AI 文案",
            f"got={sent!r}")

        # 2.2 AI 返回空 → 回落词库
        sent.clear()
        dc = _mk()

        async def _gen_empty(target):
            return ""

        dc.gen_dm_message = _gen_empty
        dc._ensure_record("k2", {"user_id": "2", "nickname": "李四"})
        asyncio.run(dc._do_send("k2", {"user_id": "2", "nickname": "李四"}))
        chk(sent and sent[-1] == "词库文案", "AI 返回空 → 回落词库",
            f"got={sent!r}")

        # 2.3 AI 抛异常 → 回落词库，且不中断发送
        sent.clear()
        dc = _mk()

        async def _gen_boom(target):
            raise RuntimeError("模拟 AI 侧故障")

        dc.gen_dm_message = _gen_boom
        dc._ensure_record("k3", {"user_id": "3", "nickname": "王五"})
        asyncio.run(dc._do_send("k3", {"user_id": "3", "nickname": "王五"}))
        chk(sent and sent[-1] == "词库文案", "AI 异常 → 回落词库且发送继续",
            f"got={sent!r}")

        # 2.4 同步回调同样被吃下（不要求调用方 async）
        sent.clear()
        dc = _mk()
        dc.gen_dm_message = lambda target: "同步回调文案"
        dc._ensure_record("k4", {"user_id": "4", "nickname": "赵六"})
        asyncio.run(dc._do_send("k4", {"user_id": "4", "nickname": "赵六"}))
        chk(sent and sent[-1] == "同步回调文案", "同步回调可用（不强制 async）",
            f"got={sent!r}")

        # 2.5 gen_dm_message=None（未接线）→ 词库（改造前行为）
        sent.clear()
        dc = _mk()
        dc.gen_dm_message = None
        dc._ensure_record("k5", {"user_id": "5", "nickname": "钱七"})
        asyncio.run(dc._do_send("k5", {"user_id": "5", "nickname": "钱七"}))
        chk(sent and sent[-1] == "词库文案", "未接线（None）→ 词库（零回归）",
            f"got={sent!r}")

        # 2.6 热更接口必须能带上 gen_dm_message
        dc = _mk()
        applied = dc.apply_runtime(gen_dm_message=_gen_ok)
        chk("ai_gen" in applied and dc.gen_dm_message is _gen_ok,
            "apply_runtime 支持热更 gen_dm_message", f"applied={applied!r}")
    finally:
        _d.send_target_async = _orig_send

    print()
    print("=" * 72)
    print("L3 判定层：AutoDM._make_gen_dm_message（monkeypatch，零 DB/零浏览器）")
    print("=" * 72)
    from core import auto_dm as _adm
    from services import ai_agent as _agent
    from services import ai_reply as _air

    _o = (_agent.agent_of, _agent.resolve_config, _agent.resolve_knowledge,
          _air.get_config)
    try:
        # 全局配置桩里**不得**预置 scopes —— scopes 是「是否接线」的唯一开关，
        # 由各场景的 cfg_extra 提供。若在全局就塞 scopes，「未绑定 Agent」场景
        # 也会接线，把「全局也可生效」这条语义测反（本脚本开发中实际踩过）。
        _air.get_config = lambda: {"enabled": True, "strict_level": "rag"}

        def _make(account, aid, cfg_extra):
            _agent.agent_of = lambda a: aid
            _agent.resolve_config = lambda a, base: {**base, **(cfg_extra or {})}
            m = _adm.AutoDM.__new__(_adm.AutoDM)     # 不跑 __init__（避免拉任何资源）
            m.target_acct = account
            m._acct = None
            return m._make_gen_dm_message()

        # 3.1 未绑定 Agent + 全局含 live → 接线（既有「未绑定=零回归」语义）
        chk(callable(_make("账号A", None, {"scopes": ["live"]})),
            "未绑定 Agent + 全局含 live → 接线（零回归语义）")
        # 3.2 未绑定 Agent + 全局不含 live → 不接线
        chk(_make("账号A", None, {"scopes": ["dm"]}) is None,
            "未绑定 Agent + 全局不含 live → 不接线")
        # 3.3 全局未设 scopes → 不接线（防「默认开」漂移）
        chk(_make("账号A", None, {}) is None, "全局未设 scopes → 不接线（默认不启用）")
        # 3.4 绑定 + live + enabled + rag → 接线
        chk(callable(_make("账号A", "ag1",
                           {"scopes": ["live"], "enabled": True,
                            "strict_level": "rag"})),
            "绑定 + scopes 含 live + enabled + rag → 接线")
        # 3.5 绑定但 scopes 不含 live → 不接线（绑定只决定「用哪个 Agent」）
        chk(_make("账号A", "ag1", {"scopes": ["dm"]}) is None,
            "绑定但 scopes 不含 live → 不接线")
        # 3.6 enabled=False → 不接线
        chk(_make("账号A", "ag1", {"scopes": ["live"], "enabled": False}) is None,
            "enabled=False → 不接线")
        # 3.7 kb_only 档位 → 不接线（用户显式选择 AI 不参与，必须尊重）
        chk(_make("账号A", "ag1",
                  {"scopes": ["live"], "enabled": True,
                   "strict_level": "kb_only"}) is None,
            "strict_level=kb_only → 不接线（尊重用户显式选择）")
        # 3.8 无账号上下文 → 不接线
        chk(_make("", "ag1", {"scopes": ["live"], "enabled": True}) is None,
            "无账号上下文 → 不接线")
        # 3.9 判定环节抛异常 → 不接线（且不抛出，否则会打断发送链路）
        _agent.agent_of = lambda a: (_ for _ in ()).throw(RuntimeError("kv down"))
        m = _adm.AutoDM.__new__(_adm.AutoDM)
        m.target_acct, m._acct = "账号A", None
        chk(m._make_gen_dm_message() is None, "接线判定异常 → 静默回落（不抛出）")
    finally:
        (_agent.agent_of, _agent.resolve_config, _agent.resolve_knowledge,
         _air.get_config) = _o

    print()
    print("=" * 72)
    print("L4 错误码层：新增码必须带设计契约")
    print("=" * 72)
    from errcode import lookup
    for code, kw in (("SEND-038", "gen_dm_message"),
                     ("SEND-039", "接线判定"),
                     ("SEND-040", "护栏")):
        info = lookup(code)
        chk(bool(info), f"{code} 已注册")
        if info:
            chk(bool(info.get("design")), f"{code} 有 design（设计契约）", str(info)[:120])
            chk(bool(info.get("contract")), f"{code} 有 contract（不变式）")
            chk(code.split("-")[0] == info.get("domain"), f"{code} 域归属正确")

    print()
    print("=" * 72)
    print("L5 源码契约一致性：前端 -> 后端 字段名/形态")
    print("=" * 72)
    root = os.path.abspath(os.path.join(_BACKEND, ".."))
    fe = open(os.path.join(root, "frontend", "src", "components", "live",
                           "live-shared.tsx"), encoding="utf-8").read()
    chk("dm_pool?: (string | {" in fe, "前端 TaskConfig.dm_pool 已放宽为 string|对象")
    lp = open(os.path.join(root, "frontend", "src", "components", "live",
                           "live-page.tsx"), encoding="utf-8").read()
    chk("dm_pool: selCfg.dm_pool || []" in lp,
        "直播页仍原样透传所选策略词库（发送方契约未变）")
    be = open(os.path.join(_BACKEND, "models", "task.py"), encoding="utf-8").read()
    chk("List[Union[str, dict]]" in be, "后端声明已放宽（ListView Union）")
    dp = open(os.path.join(_BACKEND, "core", "dispatch.py"), encoding="utf-8").read()
    chk("inspect.isawaitable" in dp, "派发器协程感知（async 回调正确 await）")
    chk("SEND-038" in dp, "派发器异常带错误码 SEND-038")

    print()
    print("=" * 72)
    total = PASS + len(FAIL)
    print(f"结果：{PASS}/{total} 通过")
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  -", f)
        print("=" * 72)
        return 1
    print("全部通过")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
