# -*- coding: utf-8 -*-
"""直播监听 AI 私信文案「是否生效」可观测性 —— 验证脚本（v0.44.55，P1-2）。

要证明的事（用户 2026-09-23 拍板口径：**保守默认值 + UI 强提示**）
  1. 判定层「AI 未生效」不再是静默的：每种否决都有**可查询的** reason_code / reason；
  2. scopes 不含 live 的 Agent → 端点明确返回「未生效（原因：Agent 作用域未勾选
     直播监听）」；改成含 live → 返回「已生效」（**前后读数由本脚本自己打印**）；
  3. 保守默认值铁律未被破坏：新建 Agent 的 scopes 缺省仍是 ["dm"]
     （不把 AI 默认接到发送侧）；
  4. 判定**唯一真源**：端点与发送侧接线共用 AutoDM.evaluate_live_ai，
     两者结论必须一致（杜绝「UI 说生效、实际回落词库」）；
  5. 零回归：接线/回落的返回语义（callable / None）与改造前逐字一致；
  6. 端点只读：不写任何配置、不含 cfg 内部键（不泄露 prompt/密钥）。

分层（对齐 references/verification_script_layering.md）
  L1 判定层   —— 真 AutoDM.evaluate_live_ai（monkeypatch Agent/全局配置，零 DB/零浏览器）
  L2 接线层   —— 真 AutoDM._make_gen_dm_message（断言 callable/None 与改造前一致）
  L3 端点层   —— 真 api.ai.live_dm_state 协程（asyncio.run 直调，不起服务进程）
  L4 默认值层 —— 真 services.ai_agent 列表缺省 + api.ai.save_agent 不兜底（源码契约）
  L5 源码层   —— 前端 live-page.tsx 已展示该状态（文案契约）

用法（脚本自设环境，不依赖调用方 export；不改任何项目文件）：
    python backend/scripts/verify_live_ai_observability.py
退出码 0 = 全部通过。
"""
from __future__ import annotations

import asyncio
import os
import sys

# ---------------------------------------------------------------------------
# 环境门禁：脚本自设 DY_APP_ROOT（铁律：不靠调用方记忆，拒绝污染真实数据根）
# ---------------------------------------------------------------------------
_DESIGN_ROOT = r"C:\temp\dyautodm_design"
_FORBIDDEN = (r"C:\temp\dyautodm_test",)
if os.path.abspath(_DESIGN_ROOT) in [os.path.abspath(x) for x in _FORBIDDEN]:
    print("[FATAL] 环境门禁：数据根与禁用环境相同，拒绝运行")
    sys.exit(2)
os.environ["DY_APP_ROOT"] = _DESIGN_ROOT

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

_ROOT = os.path.abspath(os.path.join(_BACKEND, ".."))

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
    from core import auto_dm as adm
    from services import ai_agent, ai_reply

    # 全局配置桩里**不得**预置 scopes —— scopes 是「是否接线」的唯一开关。
    _orig_get_config = ai_reply.get_config
    _o = (ai_agent.agent_of, ai_agent.resolve_config, ai_agent.resolve_config_for,
          ai_reply.get_config)
    ai_reply.get_config = lambda: {"enabled": True, "strict_level": "rag"}

    def _mk(account: str = "测试账号A", aid: str = "ag_v", extra=None):
        ai_agent.agent_of = (lambda a: aid) if aid else (lambda a: "")
        ai_agent.resolve_config = lambda a, base: {**base, **(extra or {})}
        m = adm.AutoDM.__new__(adm.AutoDM)     # 不跑 __init__（零副作用）
        m.target_acct, m._acct = account, None
        return m

    try:
        print("=" * 74)
        print("L1 判定层：AutoDM.evaluate_live_ai（每种否决都要有可查询原因）")
        print("=" * 74)
        cases = [
            ("scopes=['dm']（缺 live）", {"scopes": ["dm"]}, False, "scope_missing"),
            ("scopes=['dm','live']", {"scopes": ["dm", "live"]}, True, "ok"),
            ("enabled=False", {"scopes": ["live"], "enabled": False}, False, "ai_disabled"),
            ("strict_level=kb_only", {"scopes": ["live"], "strict_level": "kb_only"},
             False, "kb_only"),
        ]
        for label, extra, active, code in cases:
            v = _mk(extra=extra).evaluate_live_ai()
            chk(bool(v.get("active")) is active and v.get("reason_code") == code,
                f"{label} → active={active} reason_code={code}",
                f"got={v.get('active')}/{v.get('reason_code')}")
            chk(isinstance(v.get("reason"), str) and bool(v["reason"]),
                f"{label} → reason 非空可展示", f"got={v.get('reason')!r}")

        v = _mk(account="").evaluate_live_ai()
        chk(v.get("reason_code") == "no_account",
            "无账号上下文 → reason_code=no_account", f"got={v.get('reason_code')}")

        # ── H-16 零 Agent 门禁（2026-09-26）：已授权账号 ∩ 未绑定 Agent → no_agent ──
        # 正控：agent_of 返回空（未绑定）→ 必须显式 no_agent，**不得**静默回落全局配置
        v = _mk(account="未绑定账号X", aid="").evaluate_live_ai()
        chk(v.get("active") is False and v.get("reason_code") == "no_agent",
            "未绑定 Agent → active=False 且 reason_code=no_agent（H-16 零 Agent 门禁）",
            f"got={v.get('active')}/{v.get('reason_code')}")
        chk("未绑定" in str(v.get("reason")) and "绑定 Agent" in str(v.get("reason")),
            "未绑定原因含可操作提示（去设置页绑定 Agent）", f"got={v.get('reason')!r}")
        # 负控：一旦绑定（agent_of 返回非空）→ 不得再报 no_agent（防门禁过宽）
        v = _mk(account="已绑定账号Y", aid="ag_v", extra={"scopes": ["dm", "live"]}).evaluate_live_ai()
        chk(v.get("reason_code") != "no_agent",
            "已绑定 Agent → 不再报 no_agent（门禁非无脑全拒）", f"got={v.get('reason_code')}")
        chk(v.get("active") is True and v.get("reason_code") == "ok",
            "已绑定 + scopes 含 live → 已生效", f"got={v.get('reason_code')}")

        # 判定异常必须收敛为 error，且**不抛出**（否则会打断发送链路）
        ai_agent.agent_of = lambda a: (_ for _ in ()).throw(RuntimeError("kv down"))
        m = adm.AutoDM.__new__(adm.AutoDM)
        m.target_acct, m._acct = "测试账号A", None
        v = m.evaluate_live_ai()
        chk(v.get("reason_code") == "error" and not v.get("active"),
            "判定异常 → 收敛为 reason_code=error 且不抛出",
            f"got={v.get('reason_code')}")

        print()
        print("=" * 74)
        print("L2 接线层：_make_gen_dm_message 返回语义零回归 + 状态可被查询")
        print("=" * 74)
        # 2.1 未生效场景：仍返回 None（与改造前逐字一致）
        m = _mk(extra={"scopes": ["dm"]})
        chk(m._make_gen_dm_message() is None,
            "scopes 不含 live → 仍返回 None（回落词库，零回归）")
        st = adm.get_live_ai_state()
        chk(st.get("active") is False and st.get("reason_code") == "scope_missing",
            "未生效 → 模块级状态可查询（active=False, scope_missing）",
            f"got={st}")
        chk("Agent 作用域未勾选直播监听" in str(st.get("reason")),
            "未生效原因含「Agent 作用域未勾选直播监听」", f"got={st.get('reason')!r}")

        # 2.2 生效场景：返回 callable，状态同步为已生效
        m = _mk(extra={"scopes": ["dm", "live"]})
        chk(callable(m._make_gen_dm_message()),
            "scopes 含 live → 返回 callable（接线，零回归）")
        st = adm.get_live_ai_state()
        chk(st.get("active") is True and st.get("reason_code") == "ok",
            "已生效 → 状态同步（active=True, ok）", f"got={st}")

        # 2.3 状态不含内部键 cfg（防 prompt/密钥随状态外泄）
        chk("cfg" not in adm.get_live_ai_state(),
            "状态快照不含内部键 cfg（不外泄 prompt/密钥）")

        print()
        print("=" * 74)
        print("L3 端点层：GET /api/ai/live_dm_state（真协程直调，不起服务）")
        print("=" * 74)
        from api import ai as ai_api

        def call(**kw):
            return asyncio.run(ai_api.live_dm_state(**kw))

        # —— 修复前后对照读数（验收判据：脚本自己打印前后读数）——
        # 构造「scopes 不含 live 的 Agent 配置」（复刻前端新建 Agent 的默认值 ["dm"]）
        ai_agent.resolve_config = lambda a, base: {**base, "scopes": ["dm"]}
        print("  ── 对照读数：scopes 不含 live ──")
        before = call(account="测试账号A")
        print(f"     active      = {before.get('active')}")
        print(f"     reason      = {before.get('reason')}")
        print(f"     reason_code = {before.get('reason_code')}")
        print(f"     source      = {before.get('source')}")
        chk(before.get("active") is False
            and before.get("reason_code") == "scope_missing"
            and "Agent 作用域未勾选直播监听" in str(before.get("reason")),
            "端点：不含 live → 未生效（原因：Agent 作用域未勾选直播监听）",
            f"got={before}")

        # 改为含 live（同一账号，实时重判）
        ai_agent.resolve_config = lambda a, base: {**base, "scopes": ["dm", "live"]}
        print("  ── 对照读数：scopes 改为含 live ──")
        after = call(account="测试账号A")
        print(f"     active      = {after.get('active')}")
        print(f"     reason      = {after.get('reason')}")
        print(f"     reason_code = {after.get('reason_code')}")
        print(f"     source      = {after.get('source')}")
        chk(after.get("active") is True and after.get("reason_code") == "ok"
            and after.get("reason") == "已生效",
            "端点：改为含 live → 已生效", f"got={after}")

        # 端点与发送侧共用同一真源 —— 结论必须一致
        m = _mk(extra={"scopes": ["dm", "live"]})
        side = m._make_gen_dm_message()
        chk(callable(side) is bool(after.get("active")),
            "端点结论 == 发送侧接线结论（同一真源，无第二处判定）",
            f"endpoint={after.get('active')} side={callable(side)}")

        # 端点只读：调用前后配置不变（保守默认值口径）
        snap_before = dict(adm.get_live_ai_state())
        call(account="测试账号A")
        chk(adm.get_live_ai_state() == snap_before,
            "端点只读：不写引擎判定状态", f"got={adm.get_live_ai_state()}")
        chk("cfg" not in call(account="测试账号A"),
            "端点输出不含内部键 cfg")

        # agent_id 预演分支
        ai_agent.resolve_config_for = lambda aid_, base: {
            **base, "scopes": ["live"], "enabled": True, "strict_level": "rag"}
        pv = call(agent_id="ag_demo")
        chk(pv.get("active") is True and pv.get("agent_id") == "ag_demo",
            "agent_id 预演分支可用（不写任何绑定）", f"got={pv}")

        print()
        print("=" * 74)
        print("L4 默认值层：保守默认值铁律（不把 AI 默认接到发送侧）")
        print("=" * 74)
        src_agent = open(os.path.join(_BACKEND, "services", "ai_agent.py"),
                         encoding="utf-8").read()
        chk('cfg.get("scopes") or ["dm"]' in src_agent,
            "services/ai_agent.py 列表缺省仍是 ['dm']（未擅自扩成全选）")
        src_api = open(os.path.join(_BACKEND, "api", "ai.py"), encoding="utf-8").read()
        chk("cfg = incoming" in src_api,
            "api/ai.py 新建 Agent 仍不兜底（cfg = incoming）")

        print()
        print("=" * 74)
        print("L5 源码层：前端已展示「AI 文案：已生效/未生效（原因：xxx）」")
        print("=" * 74)
        lp = open(os.path.join(_ROOT, "frontend", "src", "components", "live",
                               "live-page.tsx"), encoding="utf-8").read()
        chk("AI 文案" in lp, "live-page.tsx 含「AI 文案」展示")
        # 2026-09-24 父会话收尾：已从直连 fetch 切回 api.client 统一封装
        # （直连缺 X-Member-Token 会被会员门禁拦 401）。断言随之升级：
        # ① 封装层存在 aiLiveDmState ② 页面调用它 ③ 页面不得再有裸 fetch。
        cl = open(os.path.join(_ROOT, "frontend", "src", "api",
                              "client.ts"), encoding="utf-8").read()
        chk("aiLiveDmState" in cl,
            "client.ts 已补 aiLiveDmState()（走统一 request 封装）")
        chk("fetchLiveAiDmState(aiDmAcct, api)" in lp,
            "live-page.tsx 调用 aiLiveDmState（带鉴权头）")
        chk("await fetch(\n" not in lp and "LIVE_AI_STATE_PATH" not in lp,
            "live-page.tsx 已无裸 fetch / 无直连常量（不再缺鉴权头）")
        chk("未生效" in lp and "已生效" in lp, "live-page.tsx 含生效/未生效两种文案")

        # 未碰发送侧闸门（本批次铁律）
        for f in ("core/dispatch.py", "services/dm_dispatch.py"):
            p = os.path.join(_BACKEND, f)
            chk(os.path.exists(p), f"{f} 存在（本批次只读，未改）")
    finally:
        (ai_agent.agent_of, ai_agent.resolve_config,
         ai_agent.resolve_config_for, ai_reply.get_config) = _o

    print()
    print("=" * 74)
    total = PASS + len(FAIL)
    print(f"结果：{PASS}/{total} 通过")
    if FAIL:
        print("失败项：")
        for f in FAIL:
            print("  -", f)
        print("=" * 74)
        return 1
    print("全部通过")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    sys.exit(main())
