# ⚠️ 2026-09-22 已退役（DEPRECATED · RETIRED）—— 保留全文仅供追溯，请勿运行。
#
# 退役理由（H-8 普查实测）：38 项断言中 **8 项红**，性质经逐条核验为两类：
#   · 7 项断言随 v0.43.93 重构**整体过期**（force_rescan 彻底移除 / 只读展示 / restart 端点形态变化）
#   · 1 项为**脚本自身缺陷**：_StubDispatch.apply_runtime 未接收 gen_dm_message 形参 →
#     TypeError，与 scripts/diag/verify_ai_live_wiring.py 的同契约断言（PASS）自相矛盾
# ⇒ 「恒红」使其丧失判据价值；继续保留会掩盖真实回归。
#
# 承接者：直播配置/热更契约现由 backend/scripts/verify_ai_live_wiring.py（37/37 全 PASS，
#         L1 契约 / L2 调度 / L3 判定 / L4 错误码 / L5 前端源码一致性）覆盖。
# 备份：仓库外 `C:	emp\_retired_verify_live_restart_hotswap.py.bak.<时间戳>`
# 注：本文件未被 .gitignore 忽略，删除后可从 git 历史 `git log --follow` 回捞。
from __future__ import annotations
# -*- coding: utf-8 -*-
"""v0.43.34 验收：「直播页配置收敛 + 重启标签热更」——源码级 + 实机桩验证。

## 设计契约（Step1 回归设计理念）

用户 2026-09-15 定调（原话拆解）：

  1. 「直播页面的配置都在配置管理中处理，不再留有手动输入的板块，
      页面只需选择对应配置的标签即可」
  2. 「如果是在直播任务进行中修改，则需要到配置管理中修改后点击重启标签」
     → 明确补充：「重启 = 保存后立即把新配置套到正在运行的监听任务，
        **只修改配置的内容，不中断监听**」

| 层 | 应该做什么 |
|---|---|
| 直播页 | **只读展示 + 选择**：选「直播间配置」（= 标签），无任何手填配置项 |
| 直播间配置管理 | 房间级配置的**唯一可写入口**（含新增/编辑/删除） |
| 「重启」按钮 | 保存 + 把变更热更进**正在运行**的任务：不重建 WS、不重扫凭证、不清队列 |
| 引擎 | `apply_runtime_config` 是热更唯一入口；拿不到 dispatch 必须显式失败 |

## 本脚本断言（不启浏览器 / 不连 BCC / 不碰 profile）

  A. 后端热更入口      apply_runtime_config / DispatchCenter.apply_runtime 存在且语义正确
  B. 不中断监听        热更路径不调用 stop/start/goto，不触碰 live（WS）与 auth
  C. 显式失败           非运行态 / 无 dispatch → ok=False + reason（不假装成功）
  D. 换任务语义         换直播间 / 换账号 → not_applied 显式透出
  E. 端点契约           /restart 存在、写 kv、返回 restart.applied/not_applied
  F. 前端收敛           直播页无配置输入控件；有「选择配置」；配置管理有「重启」
  G. 错误码契约         ENG-013 / ENG-014 / LIVE-021 六段齐全
  H. 实机桩验证（离线） AutoDM.apply_runtime_config 真跑：运行态生效字段、
                        非运行态拒绝、dispatch 未初始化拒绝

跑法：python scripts/verify_live_restart_hotswap.py
     （H 段需真跑 backend 模块，若被调解释器缺依赖，本脚本会自动改用
      Python314 重跑 —— Hermes 会话的 `python` 是自带 venv，没有 pydantic_settings。）
"""
from __future__ import annotations

import ast
import io
import os
import re
import subprocess
import sys

# ── 解释器自愈（§〇·庚：backend 打包/依赖解释器 = Python314）─────────────────
# 必须放在任何 backend import 之前。
_PY314 = r"C:\Users\LOX\AppData\Local\Programs\Python\Python314\python.exe"
if os.environ.get("_VLRH_REEXEC") != "1":
    try:
        import pydantic_settings  # noqa: F401
    except Exception:
        if os.path.exists(_PY314):
            env = dict(os.environ, _VLRH_REEXEC="1")
            sys.exit(subprocess.call([_PY314, os.path.abspath(__file__)], env=env))

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BK = os.path.join(ROOT, "backend")
FE = os.path.join(ROOT, "frontend", "src")
sys.path.insert(0, BK)

PASS: list[str] = []
FAIL: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(f"{name}{(' — ' + detail) if detail else ''}")
    print(("  ✅ " if cond else "  ❌ ") + name + (f" — {detail}" if detail else ""))


def src(rel: str, base: str = BK) -> str:
    with io.open(os.path.join(base, rel), "r", encoding="utf-8") as f:
        return f.read()


def tree(rel: str, base: str = BK) -> ast.Module:
    return ast.parse(src(rel, base))


def func_src(rel: str, name: str, base: str = BK) -> str:
    """取某函数的源码切片。

    用 `ast.get_source_segment` 依 `end_lineno` 精确取体 —— **不要用缩进启发式**：
    多行签名的收尾括号（`    ) -> list[str]:`）本身就顶到 def 同级缩进，
    会让「缩进 ≤ def」的中断条件提前触发（曾因此把函数体截成只剩签名）。
    """
    text = src(rel, base)
    t = ast.parse(text)
    for node in ast.walk(t):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            seg = ast.get_source_segment(text, node)
            return seg or ""
    return ""


def method_names(rel: str, cls: str) -> list[str]:
    t = tree(rel)
    for node in ast.walk(t):
        if isinstance(node, ast.ClassDef) and node.name == cls:
            return [n.name for n in node.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    return []


print("=" * 78)
print("A. 后端热更入口")
print("=" * 78)

adm_methods = method_names("core/auto_dm.py", "AutoDM")
check("AutoDM.apply_runtime_config 存在", "apply_runtime_config" in adm_methods)
disp_methods = method_names("core/dispatch.py", "DispatchCenter")
check("DispatchCenter.apply_runtime 存在", "apply_runtime" in disp_methods)

ar = func_src("core/auto_dm.py", "apply_runtime_config")
check("apply_runtime_config 返回结构化结果（applied/not_applied/reason）",
      all(k in ar for k in ("applied", "not_applied", "reason", "engine_state")))
check("apply_runtime_config 有显式失败分支（ok=False + reason）",
      ar.count('"ok": False') >= 2 and 'reason": f"引擎未运行' in ar)

da = func_src("core/dispatch.py", "apply_runtime")
check("DispatchCenter.apply_runtime 热更四个调度字段",
      all(k in da for k in ("max_target", "interval", "delay_range", "pick_dm_message")))
check("DispatchCenter.apply_runtime 不清队列 / 不重置计数",
      "_queue" not in da and "records" not in da and "count = 0" not in da)
check("DispatchCenter.apply_runtime 返回实际生效字段列表", "applied.append" in da)

print()
print("=" * 78)
print("B. 不中断监听（热更路径不得重建任务）")
print("=" * 78)

forbidden = ("self.stop(", "await self.start(", "self._stop_event.set(",
             "goto(", "rescan_and_rebuild(", "self.live =", "self.auth =")
hit = [f for f in forbidden if f in ar]
check("热更路径不含 stop/start/重建 WS/重扫/换 auth", not hit, f"命中={hit}")

check("热更明确声明不重建监听（docstring 契约）",
      "不重建" in ar and "不重扫" in ar and "不中断监听" in ar)

print()
print("=" * 78)
print("C. 显式失败（禁止假成功）")
print("=" * 78)

check("非 RUNNING/PAUSED 显式拒绝", "EngineState.RUNNING, EngineState.PAUSED" in ar)
check("dispatch 为 None 显式失败（不是只改 adm 字段）",
      "ENG-014" in ar and '"ok": False, "applied": [], "not_applied": not_applied' in ar)
check("拒绝/失败时打错误码日志", "ENG-013" in ar and "ENG-014" in ar)

print()
print("=" * 78)
print("D. 「换任务」语义显式透出（换直播间 / 换账号）")
print("=" * 78)

check("换直播间 → not_applied 含 live_url", 'not_applied.append("live_url")' in ar)
check("换账号 → not_applied 含 acct", 'not_applied.append("acct")' in ar)
check("force_rescan 不参与热更（避免运行中踢回扫码）",
      'not_applied.append("force_rescan")' in ar and "force_fresh" not in ar)

print()
print("=" * 78)
print("E. 端点契约：POST /api/live/room-configs/{room_id}/restart")
print("=" * 78)

lc = src("api/live_config.py")
check("restart 端点已注册", '@router.post("/{room_id}/restart")' in lc)
check("restart 端点接收 Request（拿 app.state.adm）",
      "restart_room_config(room_id: str, request: Request)" in lc)
check("restart 写入 kv config（下次启动也生效）", "_apply_to_task_kv(cfg)" in lc)
check("restart 调用引擎热更入口", "adm.apply_runtime_config(" in lc)
check("restart 透出 restart.applied / not_applied / reason",
      all(k in lc for k in ('"applied":', '"not_applied":', '"reason":')))
check("restart 异常显式失败（LIVE-021，不静默）",
      "LIVE-021" in lc and '"ok": False, "applied": []' in lc)
# 2026-09-17 修补（OCR 审查 HIGH —— 恒真式检查）：
# 原为 `'\'enabled\': bool(' in func_src(...) or "enabled" in lc`
# —— 右操作数 `"enabled" in lc` 对 live_config.py 的**任何版本**都为真
# （文件里必然出现 "enabled" 字样），于是整个 check 恒通过、永不失败，
# 起不到回归保护作用。
# 现改为：只认 `_RuntimeCfg.__init__` 里那行真正的布尔保留写法。
_rt_init = func_src("api/live_config.py", "__init__").replace("\r", "")
check("_RuntimeCfg 保留词库 enabled 位（不压成字符串）",
      '"enabled": bool(' in _rt_init,
      f"未在 _RuntimeCfg.__init__ 找到 '\"enabled\": bool(' 写法")
check("/apply 端点仍保留（供任务中心复用）", '@router.post("/{room_id}/apply")' in lc)

print()
print("=" * 78)
print("F. 前端收敛：直播页只选择，配置管理唯一可写 + 重启按钮")
print("=" * 78)

live = src("components/live/live-page.tsx", FE)
check("直播页引用了配置选择器（Select）",
      'from "@/components/ui/select"' in live and "SelectItem" in live)
check("直播页不再渲染发送上限/间隔/延迟抖动输入框",
      'label="发送上限"' not in live and 'label="间隔（秒）"' not in live
      and 'label="延迟抖动（秒）"' not in live)
check("直播页不再渲染私信词库编辑块（dm-templates）", 'data-od-id="dm-templates"' not in live)
check("直播页不再有「保存词库 / 保存配置」按钮",
      "保存词库" not in live and "保存配置" not in live)
check("直播页无配置类输入控件（上限/间隔/抖动/词库全部移除）",
      all(a not in live for a in (
          'aria-label="每场最多发送私信条数"',
          'aria-label="两条私信之间的间隔秒数"',
          'aria-label="延迟抖动区间"'))
      and "启用模板" not in live
      and live.count("<Input") == 3,  # 直播间 / 发弹幕 / 批量点赞（均为互动，非配置）
      f"Input 数={live.count('<Input')}")
check("直播间输入框为自动解析（无「解析房间号」按钮）",
      "onBlur" in live and 'data-od-id="live-parse"' not in live)
check("直播页配置区为只读展示（含「去配置」指引）", "去配置" in live)

# 2026-09-19：配置管理弹窗 → 整页视图「配置标签」（RoomConfigPage.tsx）
# 参数可写能力必须逐项保留；同时旧弹窗文件不得复活（它是「反复覆盖」的载体）
check("旧弹窗组件 RoomConfigManager.tsx 已移除（防止配置入口回退）",
      not os.path.exists(os.path.join(FE, "components", "live", "RoomConfigManager.tsx")))
rc = src("components/live/RoomConfigPage.tsx", FE)
check("策略编辑页保留全部策略控件（发送上限/间隔/抖动/词库/自动连麦/账号）",
      all(k in rc for k in ("发送上限", "间隔（秒）", "延迟抖动（秒）", "自动申请连麦", "监听账号")))
check("配置标签页有「重启」按钮", "重启" in rc and "restartRoomConfig" in rc)
check("配置标签页调 restartRoomConfig（新 API）", "api.restartRoomConfig" in rc)

# 2026-09-19 最终契约：**没有**目标直播间页 / 无子 tab（用户定调，勿复活）
check("「目标直播间」页不存在（用户要求删除，不得复活）",
      not os.path.exists(os.path.join(FE, "components", "live", "TargetRoomPage.tsx")))
check("直播页无子 tab 状态（subView 不得出现）", "subView" not in live)
check("策略弹窗挂在直播页，「直播间」板块有策略下拉与管理入口",
      "<RoomConfigPage" in live and "选择直播策略" in live and "管理策略" in live)
check("强制重扫已彻底移除（前/后端均无残留）",
      not any(k in (live + rc) for k in ("强制重扫", "force_rescan", "forceRescan")))

cli = src("api/client.ts", FE)
check("client.ts 有 restartRoomConfig",
      "async restartRoomConfig" in cli and "/restart" in cli)

print()
print("=" * 78)
print("G. 错误码六段契约")
print("=" * 78)

sys.path.insert(0, BK)
from errcode import lookup, contract_gaps  # noqa: E402

for code in ("ENG-013", "ENG-014", "LIVE-021"):
    r = lookup(code)
    ok = bool(r and r.get("design") and r.get("contract") and r.get("deviation")
              and r.get("chain") and r.get("root") and r.get("verify"))
    check(f"{code} 六段齐全（design/contract/deviation/chain/root/verify）", ok)

gaps = contract_gaps()
check("新增码不在 contract_gaps.missing", all(
    c not in gaps["missing"] for c in ("ENG-013", "ENG-014", "LIVE-021")))

print()
print("=" * 78)
print("H. 实机桩验证（真跑 AutoDM.apply_runtime_config，不启任何进程/浏览器）")
print("=" * 78)

import asyncio  # noqa: E402

os.environ.setdefault("DY_APP_ROOT", os.path.join(ROOT, "backend"))
try:
    from models.enums import EngineState  # noqa: E402
    from core.auto_dm import AutoDM  # noqa: E402

    class _Cfg:
        def __init__(self, **kw):
            self.live_url = kw.get("live_url", "")
            self.max_target = kw.get("max_target", 3)
            self.interval = kw.get("interval", 60.0)
            self.delay_range = kw.get("delay_range", [40, 65])
            self.force_rescan = kw.get("force_rescan", False)
            self.dm_pool = kw.get("dm_pool", [])
            self.acct = kw.get("acct", None)

    class _StubDispatch:
        """记录 apply_runtime 入参的最小桩（不启动消费循环）。"""
        def __init__(self):
            self.calls = []
            self.max_target = 0
            self.count = 0
            self.reached_limit = False
            self.interval = 0.0
            self.delay_range = (0, 0)
            self.pick_dm_message = None
            self.pending = {}
            self.records = {}
            self._queue = None  # 哨兵位：验证热更没被替换掉

        def set_max_target(self, n):
            self.max_target = int(n)
            if self.count < self.max_target:
                self.reached_limit = False

        def apply_runtime(self, max_target=None, interval=None,
                          delay_range=None, pick_dm_message=None):
            applied = []
            if max_target is not None:
                self.set_max_target(max_target)
                applied.append("max_target")
            if interval is not None:
                self.interval = float(interval)
                applied.append("interval")
            if delay_range is not None:
                lo, hi = int(delay_range[0]), int(delay_range[1])
                self.delay_range = (lo, hi) if lo <= hi else (hi, lo)
                applied.append("delay_range")
            if pick_dm_message is not None:
                self.pick_dm_message = pick_dm_message
                applied.append("dm_pool")
            self.calls.append(applied)
            return applied

    # ① 非运行态：显式拒绝
    a = AutoDM()
    r = a.apply_runtime_config(_Cfg(max_target=9))
    check("H1 非运行态（IDLE）→ ok=False 且不假装生效",
          r["ok"] is False and r["applied"] == [] and "引擎未运行" in r["reason"],
          f"reason={r['reason']!r}")

    # ② 运行态但 dispatch 未初始化：显式失败
    a = AutoDM()
    a.state = EngineState.RUNNING
    a.live_url = "https://live.douyin.com/111"
    a._acct = "acctA"
    r = a.apply_runtime_config(_Cfg(max_target=9))
    check("H2 RUNNING 但无 dispatch → ok=False + ENG-014 语义",
          r["ok"] is False and "未初始化" in r["reason"], f"reason={r['reason']!r}")

    # ③ 正常运行态：调度参数热更，且存活对象（队列/计数）不被替换
    a = AutoDM()
    a.state = EngineState.RUNNING
    a.live_url = "https://live.douyin.com/111"
    a._acct = "acctA"
    a.limit, a.interval, a.delay_range = 3, 60.0, (40, 65)
    a.dm_template = [{"text": "旧文案", "enabled": True}]
    a.dispatch = _StubDispatch()
    a.dispatch.count = 5
    sentinel_queue = object()
    a.dispatch._queue = sentinel_queue
    a.dispatch.records = {"k": "rec"}
    r = a.apply_runtime_config(_Cfg(
        max_target=20, interval=15.0, delay_range=[10, 30],
        dm_pool=[{"text": "新文案A", "enabled": True},
                 {"text": "新文案B", "enabled": False}]))
    check("H3 运行态 → ok=True 且回报名单为真实生效字段",
          r["ok"] is True and set(r["applied"]) == {"max_target", "interval", "delay_range", "dm_pool"},
          f"applied={r['applied']}")
    check("H3b 热更真的落到 dispatch（ado 唯一真源）",
          a.dispatch.max_target == 20 and a.dispatch.interval == 15.0
          and tuple(a.dispatch.delay_range) == (10, 30))
    check("H3c 词库 enabled 位保留（新文案B 仍为停用）",
          a.dm_template == [{"text": "新文案A", "enabled": True},
                            {"text": "新文案B", "enabled": False}])
    check("H3d 未清队列 / 未重置已发计数 / records 未丢",
          a.dispatch._queue is sentinel_queue and a.dispatch.count == 5
          and a.dispatch.records == {"k": "rec"})
    check("H3e 上限调高后 reached_limit 复位（可继续发）",
          a.dispatch.reached_limit is False)
    check("H3f 抽取器已重建（新文案可被抽到）",
          callable(a.pick_dm_message) and a.pick_dm_message() in ("新文案A",))

    # ④ 换直播间 / 换账号 / force_rescan → not_applied 显式透出
    a = AutoDM()
    a.state = EngineState.RUNNING
    a.live_url = "https://live.douyin.com/111"
    a._acct = "acctA"
    a.dispatch = _StubDispatch()
    r = a.apply_runtime_config(_Cfg(
        live_url="https://live.douyin.com/222", acct="acctB",
        force_rescan=True, max_target=7))
    check("H4 换直播间/换账号/重扫 → not_applied 三项齐全",
          set(r["not_applied"]) == {"live_url", "acct", "force_rescan"},
          f"not_applied={r['not_applied']}")
    check("H4b 仍热更了可热更的字段", r["ok"] is True and "max_target" in r["applied"])

    # ⑤ 词库 list[str]（TaskConfig 形态）不丢既有启用位
    a = AutoDM()
    a.state = EngineState.RUNNING
    a.dispatch = _StubDispatch()
    a.dm_template = [{"text": "A", "enabled": False}, {"text": "B", "enabled": True}]
    a.apply_runtime_config(_Cfg(dm_pool=["A", "B"]))
    check("H5 list[str] 词库按既有启用位合并（A 保持停用）",
          a.dm_template == [{"text": "A", "enabled": False}, {"text": "B", "enabled": True}],
          f"{a.dm_template}")

    # ⑥ 纯 asyncio 环境无副作用（证明热更不 await 任何东西）
    async def _noop():
        return True
    asyncio.get_event_loop_policy()
    check("H6 apply_runtime_config 是同步方法（热更即时、不需 await）",
          not asyncio.iscoroutinefunction(AutoDM.apply_runtime_config))

except Exception as e:  # 桩验证本身异常 = 未通过
    import traceback
    traceback.print_exc()
    check("H 实机桩验证可运行", False, f"{type(e).__name__}: {e}")

print()
print("=" * 78)
print(f"结果：PASS={len(PASS)}  FAIL={len(FAIL)}")
print("=" * 78)
if FAIL:
    print("失败项：")
    for f in FAIL:
        print("  - " + f)
    sys.exit(1)
print("全部通过 ✅")
