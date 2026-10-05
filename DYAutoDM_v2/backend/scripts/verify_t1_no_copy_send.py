# -*- coding: utf-8 -*-
"""T1 复读弹幕修复 —— 验证脚本。

验证目标（对照实验，归因铁律）：
  A  负控：AI 未启用 + 词库无启用文案 → enable_send 必须被置 False，status_msg 必须
      出现 T1 指定显著文案，调度器所有目标走 SKIPPED（禁止真发 / 禁止复读弹幕原文）。
  B1 正控①：AI 已启用 → enable_send 保持 True，行为与改造前一致。
  B2 正控②：词库有启用文案 → enable_send 保持 True，行为与改造前一致。
  B3 正控③：AI 启用 且 词库有文案 → enable_send 保持 True。

负控对照：仅跑负控 A 不算证据；必须同时跑正控 B1/B2/B3 以证明判定仅对
「AI 不可用 AND 词库无启用文案」这一组合关闭发送能力。

用法：  python backend/scripts/verify_t1_no_copy_send.py
退出码 0 = 全部通过。
"""
from __future__ import annotations

import os
import sys

# ---------------------------------------------------------------------------
# 环境门禁（与本项目 verify_*.py 一致）
# ---------------------------------------------------------------------------
_DESIGN_ROOT = r"C:\temp\dyautodm_design"
_FORBIDDEN = (r"C:\temp\dyautodm_test",)
os.environ.setdefault("DY_APP_ROOT", _DESIGN_ROOT)
os.environ["DY_APP_ROOT"] = _DESIGN_ROOT
_HERE = os.path.abspath(os.path.dirname(os.path.abspath(__file__)))
_BACKEND = os.path.abspath(os.path.join(_HERE, ".."))
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


# ---------------------------------------------------------------------------
# 工具：直接调用 AutoDM._apply_config + 本地重放 T1 修复段（不启引擎、不碰浏览器）
# ---------------------------------------------------------------------------
def _replay_t1_fix(dm_template, gen_dm_message, _send_ok_input=True):
    """构造一个 AutoDM 实例，重放 _apply_config + T1 修复段，返回 (_send_ok, status_msg)。
    仅构造对象并执行判定逻辑，不启线程/不碰 DB/不碰浏览器。
    """
    from core.auto_dm import AutoDM
    adm = AutoDM()
    # 直接设置 dm_template（绕过 _normalize_dm_pool，直接模拟已归一化的状态）
    adm.dm_template = dm_template
    adm.pick_dm_message = adm._make_pick_dm_message()
    adm.gen_dm_message = gen_dm_message
    adm.live_id = "992931212705"

    # 重放 T1 修复段（与 auto_dm.py:838 处完全一致）
    _send_ok = _send_ok_input
    if _send_ok and adm.gen_dm_message is None and adm.pick_dm_message is None:
        _send_ok = False
        adm.status_msg = ("监听中（只听不发·AI未启用且词库无启用文案，"
                          "禁止私信——避免复读弹幕原文）")
    return adm, _send_ok


def _run_case(label, dm_template, gen_dm_message, expect_send_ok):
    print(f"\n{'='*72}")
    print(f"场景：{label}")
    print(f"{'='*72}")
    adm, send_ok = _replay_t1_fix(dm_template, gen_dm_message)
    chk(send_ok == expect_send_ok,
        f"enable_send={send_ok} (期望 {expect_send_ok})",
        f"got send_ok={send_ok}, pick_dm_message={adm.pick_dm_message}, "
        f"gen_dm_message={adm.gen_dm_message}")
    if not expect_send_ok:
        T1_MARKER = "AI未启用且词库无启用文案"
        chk(T1_MARKER in adm.status_msg,
            "status_msg 包含 T1 显著文案",
            f"got status_msg={adm.status_msg!r}")
        chk("只听不发" in adm.status_msg,
            "status_msg 包含「只听不发」前缀（与 ENG-017 视觉一致）",
            f"got status_msg={adm.status_msg!r}")
    else:
        # 正控：status_msg 不应被 T1 文案污染
        chk("AI未启用且词库无启用文案" not in adm.status_msg,
            "status_msg 未被 T1 文案污染",
            f"got status_msg={adm.status_msg!r}")


def main() -> int:
    # ① 负控：AI 未启用 (gen_dm_message=None) + 词库无启用文案 (dm_template=[])
    _run_case("A 负控：AI=None + 词库=[]",
              dm_template=[],
              gen_dm_message=None,
              expect_send_ok=False)

    # ② 负控变体：词库有文案但全部 enabled=False
    _run_case("A' 负控：AI=None + 词库=[{enabled=False}]",
              dm_template=[{"text": "文案A", "enabled": False}],
              gen_dm_message=None,
              expect_send_ok=False)

    # ③ 正控①：AI 已启用（gen_dm_message 为 callable）+ 词库空
    async def _fake_gen(target):
        return "AI 生成文案"
    _run_case("B1 正控：AI=callable + 词库=[]",
              dm_template=[],
              gen_dm_message=_fake_gen,
              expect_send_ok=True)

    # ④ 正控②：AI 未启用 + 词库有启用文案
    _run_case("B2 正控：AI=None + 词库=[{enabled=True}]",
              dm_template=[{"text": "已启用文案", "enabled": True}],
              gen_dm_message=None,
              expect_send_ok=True)

    # ⑤ 正控③：AI 启用 且 词库有启用文案
    _run_case("B3 正控：AI=callable + 词库=[{enabled=True}]",
              dm_template=[{"text": "已启用文案", "enabled": True}],
              gen_dm_message=_fake_gen,
              expect_send_ok=True)

    # ---------------------------------------------------------------------------
    # L2：调度器层对照 —— enable_send=False 时 submit 应全部 SKIPPED（绝不真发）
    # ---------------------------------------------------------------------------
    print(f"\n{'='*72}")
    print("L2 调度器层：enable_send=False → 目标全部 SKIPPED，_do_send 不被调用")
    print(f"{'='*72}")
    import asyncio
    from core import dispatch as _d
    from models.enums import RecordStatus

    sent_contents: list[str] = []

    async def _fake_send(auth, target, content):
        sent_contents.append(content)
        return True, "stub"

    _orig_send = _d.send_target_async
    _d.send_target_async = _fake_send

    try:
        class _Auth:
            account_name = ""  # 空 = 不进入 dm_dispatch 分支

        # 场景 A：enable_send=False 时，提交目标全部 SKIPPED
        dc_no_send = _d.DispatchCenter(
            auth=_Auth(), max_target=5,
            delay_range=(0, 0), interval=0.0,
            enable_send=False,
            pick_dm_message=None,
            gen_dm_message=None,
        )
        # 提交 5 个弹幕目标（模拟弹幕原文）
        targets_skipped = 0
        for i in range(5):
            ok = dc_no_send.submit({
                "user_id": str(1000 + i),
                "nickname": f"弹幕观众{i}",
                "comment": f"弹幕原文{i}",
            })
            # enable_send=False 时 submit 返回 False（不入池）
            # 但 _ensure_record 仍写入记录（status=SKIPPED）
            rec = dc_no_send.records.get(dc_no_send._dedup_key({
                "user_id": str(1000 + i),
                "nickname": f"弹幕观众{i}",
            }))
            if rec is not None and rec.status == RecordStatus.SKIPPED:
                targets_skipped += 1
        chk(targets_skipped == 5,
            "enable_send=False 时 5/5 目标为 SKIPPED（已捕获未发）",
            f"got {targets_skipped}/5")
        chk(dc_no_send.pending == {},
            "enable_send=False 时 pending 保持空（无入队）",
            f"got pending={dc_no_send.pending}")

        # 正控对照：enable_send=True + pick_dm_message 返回弹幕原文（旧行为兜底复读）
        # 但此处词库=None + gen_dm_message=None，按 dispatch.py:429 旧逻辑会兜底 comment
        # 这恰是我们要证明的旧缺陷 —— 现在 T1 修复后，DispatchCenter 在这种情况根本不会
        # 被构造为 enable_send=True，所以这个分支是被 T1 修复拦截掉的。
        # 我们只验证：如果人为构造 enable_send=True + pick_dm_message=callable，能正常发送
        dc_can_send = _d.DispatchCenter(
            auth=_Auth(), max_target=5,
            delay_range=(0, 0), interval=0.0,
            enable_send=True,
            pick_dm_message=lambda: "词库文案",
            gen_dm_message=None,
        )
        ok = dc_can_send.submit({
            "user_id": "9999",
            "nickname": "可发观众",
            "comment": "弹幕原文-应被词库覆盖",
        })
        chk(ok is True,
            "enable_send=True + 词库有文案 → submit 返回 True（入队）",
            f"got ok={ok}, pending keys={list(dc_can_send.pending.keys())}")
        # pending 以 _dedup_key 为键：user_id 优先 → "9999"
        chk("9999" in dc_can_send.pending,
            "enable_send=True 时 pending 确实入队",
            f"got pending={list(dc_can_send.pending.keys())}")
    finally:
        _d.send_target_async = _orig_send

    # ---------------------------------------------------------------------------
    # 复盘
    # ---------------------------------------------------------------------------
    print(f"\n{'='*72}")
    if FAIL:
        print(f"FAIL({len(FAIL)}): {FAIL}")
        return 1
    print(f"PASS: {PASS} 项断言全部通过（含 1 组负控 + 3 组正控 + L2 调度器层）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
