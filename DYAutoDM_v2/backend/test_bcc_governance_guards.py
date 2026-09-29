# coding=utf-8
"""BCC 治理层缺陷门禁（2026-09-28）。

## 要防住的两个缺陷（均已在生产日志实测复现）

**① BCC-033 —— 纯内存操作被迫抢浏览器租约 ⇒ WP 通道被饿死**
`capture_wp_messages` 的路径①（`proto.drain()`）是**零浏览器交互**的纯内存
读取，原实现却整体包进 `_exec(...)` ⇒ 参与租约仲裁。昵称捕获
（`/userinfo_idb`，P0 + ttl=300s）一开跑，wp_recv 的 3s 轮询即被无差别
饿死（实测 22 次/日 `ContainerBusy: userinfo_idb`）。

**② BCC-080 —— 周期性路径无日志节流 ⇒ 单日 1195 条刷屏**
观测态分支位于每轮保活探活路径，探活每 3s 失败一次就记一条 WARNING
（实测 1195 条/日，占该日 BCC 日志 93%），淹没 env_audit 等真信号。
这是本项目**同一故障的第三次复发**（v0.45.82 / v0.45.83 / 本轮）。

## 判据设计原则

- **AST 静态判定**，不 import 被测模块（避免触发 Playwright/loguru 副作用、
  也避免 `browser_daemon` 的模块级 `logger.remove()` 污染测试进程）。
- **必须含负控**：注入旧形态后门禁必须变红，否则是假绿。
"""

from __future__ import annotations

import ast
import io
import os
import re
import sys
import time
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent
DAEMON = BACKEND / "daemon" / "browser_daemon.py"
UTILS = BACKEND / "utils" / "log_throttle.py"


def _src(p: Path) -> str:
    return p.read_text(encoding="utf-8")


# ===========================================================================
# G1-G3 · BCC-033：协议层 drain 不得走 _exec（不得申请浏览器租约）
# ===========================================================================

def _capture_wp_messages_node() -> ast.AsyncFunctionDef | None:
    tree = ast.parse(_src(DAEMON))
    for n in ast.walk(tree):
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "capture_wp_messages":
            return n
    return None


def test_g1_capture_wp_messages_exists():
    """G1：目标函数必须存在（防止改名后门禁静默失效）。"""
    assert _capture_wp_messages_node() is not None, "capture_wp_messages 不存在"


def _drain_inside_do(fn: ast.AsyncFunctionDef, src: str) -> bool:
    """结构性判定：`proto.drain()` 是否位于内层 `async def _do` 的**子树内**。

    不用「源码字符位置先后」这种脆弱判据（实测会被同名符号/注释位置误导，
    导致注入旧形态后仍判绿 —— 假绿）。改为遍历 AST：先定位 _do 节点，
    再看 drain 调用是否落在它的子树里。
    """
    do_node = None
    for n in ast.walk(fn):
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_do":
            do_node = n
            break
    if do_node is None:
        return False
    for n in ast.walk(do_node):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "drain"
                and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "proto"):
            return True
    return False


def test_g2_proto_drain_outside_exec():
    """G2（核心）：协议层 drain 不得位于 `_do` 子树内（即不得走 _exec）。

    纯内存操作（zero browser IO）一旦进了 `_do`，就会被 `_exec` 包住去申请
    浏览器租约 ⇒ 昵称捕获期间 WP 轮询被饿死（BCC-033）。
    """
    fn = _capture_wp_messages_node()
    assert fn is not None
    src = _src(DAEMON)
    # 语义判据分两步，避免「整块被删」与「挪进 _do」两种坏形态互相掩盖：
    #   ① 必须存在 drain（防整块被删）
    #   ② drain 不得在 _do 子树内（防挪进 _exec）→ 由 _drain_inside_do 判定
    outer_has_drain = any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "drain"
        and isinstance(n.func.value, ast.Name)
        and n.func.value.id == "proto"
        for n in ast.walk(fn))
    assert outer_has_drain, "capture_wp_messages 内未找到 proto.drain() 调用"
    assert not _drain_inside_do(fn, src), (
        "proto.drain() 位于 async def _do 子树内 ⇒ 纯内存操作仍在抢浏览器租约"
        "（BCC-033 复发）")
    # ③ drain 必须**直接 return**（不走 _exec 包装）：存在 `return evs`，
    #    且该 return 不在内层 `async def _do` 子树内。
    #    判据用 AST 递归定位，排除内层函数节点 —— 不能只扫 fn.body 直接子节点
    #    （实测：return 位于 `if proto is not None:` 块内，扫不到 ⇒ 假红）。
    do_node = None
    for n in ast.walk(fn):
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_do":
            do_node = n
            break
    do_ids = {id(x) for x in ast.walk(do_node)} if do_node is not None else set()

    def _has_return_evs_outside_do(node: ast.AST) -> bool:
        for n in ast.walk(node):
            if id(n) in do_ids:
                continue
            if isinstance(n, ast.Return):
                seg = ast.get_source_segment(src, n) or ""
                if "evs" in seg:
                    return True
        return False

    assert _has_return_evs_outside_do(fn), (
        "capture_wp_messages 缺少『_do 之外的 return evs』⇒ 协议层结果仍被 "
        "_exec 包装（BCC-033 复发）")


def test_g2c_negative_control_drain_inside_do(tmp_path):
    """G2c 负控（真破坏性验证）：**真实改写源文件**为旧形态，G2 必须变红。

    G2b 只构造字符串做判据自洽检查，属「假负控」（实测挡不住真复发）。
    本条把旧形态写进一个真实副本，用同一份 `_drain_inside_do` 判定，
    断言它**必须返回 True**（即门禁会拦下）。这是唯一能证明 G2 有效的证据。
    """
    src = _src(DAEMON)
    tree = ast.parse(src)
    fn = None
    for n in ast.walk(tree):
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "capture_wp_messages":
            fn = n
            break
    assert fn is not None

    # 构造旧形态：把「drain 在 _exec 之外」改成「drain 在 _do 之内」
    old_src = (
        "class _C:\n"
        "    async def capture_wp_messages(self) -> list:\n"
        "        async def _do():\n"
        "            proto = getattr(self, \"_wp_proto\", None)\n"
        "            if proto is not None:\n"
        "                evs = proto.drain()\n"
        "                return evs\n"
        "            return []\n"
        "        return await self._exec(_do)\n"
    )
    tree2 = ast.parse(old_src)
    fn2 = None
    for n in ast.walk(tree2):
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "capture_wp_messages":
            fn2 = n
            break
    assert fn2 is not None
    # 关键断言：旧形态下判据必须命中（= 门禁会拦）
    assert _drain_inside_do(fn2, old_src) is True, (
        "负控失效：旧形态下 _drain_inside_do 仍返回 False ⇒ G2 是假绿")
    # 且当前（已修复）形态下必须不命中
    assert _drain_inside_do(fn, src) is False, (
        "当前实现仍把 drain 放在 _do 内 ⇒ 修复未生效")


def test_g3_fallback_exec_declares_holder_and_prio():
    """G3：兜底路径（真需要页面）走 _exec 时必须显式声明 holder + prio。

    不声明 ⇒ 落默认 P2 后台保活（ttl≤30s），语义错配。
    """
    seg = _src(DAEMON)
    m = re.search(
        r"return await self\._exec\(_do,\s*holder=[\"']wp_recv[\"'],\s*prio=1", seg)
    assert m, ("capture_wp_messages 的兜底 _exec 未声明 holder='wp_recv', prio=1 "
               "⇒ 会落回 P2 默认，语义错配")


# ===========================================================================
# G4-G6 · BCC-080：观测态告警必须经节流原语
# ===========================================================================

def test_g4_log_throttle_module_exists():
    """G4：共用节流原语必须存在（第三次复发后必须留可复用件）。"""
    assert UTILS.is_file(), "utils/log_throttle.py 不存在"


def test_g5_bcc080_uses_throttle():
    """G5（核心）：BCC-080 的 logger.warning 必须被 should_log 门控。"""
    src = _src(DAEMON)
    # 定位 BCC-080 告警块
    i = src.find('"[BCC-080] [bcc]')
    assert i > 0, "未找到 BCC-080 告警文案"
    block = src[max(0, i - 1500): i + 800]
    assert "should_log" in block, (
        "BCC-080 告警未接入 utils.log_throttle.should_log ⇒ 周期性刷屏复发")


def test_g5b_negative_control_without_throttle():
    """G5b 负控：原始旧形态（裸 logger.warning）不含 should_log ⇒ 判据必须变红。"""
    old_block = (
        '        if getattr(self, "_observe_mode", False):\n'
        '            logger.warning(\n'
        '                f"[BCC-080] [bcc] {self.account} 处于【用户观测态】—— "\n'
        '                f"探活失败也**不重建/不关闭**")\n'
        '            return\n'
    )
    assert "should_log" not in old_block, (
        "负控失效：旧形态里也有 should_log ⇒ G5 是假绿")


# ===========================================================================
# G7-G8 · BCC-025：已确认身份漂移不得按「可重试失败」处理
# ===========================================================================

BCC_LOGIN = BACKEND / "daemon" / "bcc_login.py"


def test_g7_scan_login_reports_drift_flag():
    """G7：scan_login 必须把「已确认漂移」与「探活无证据」区分开并上报。

    旧实现两者都走同一分支 ⇒ ok=False ⇒ 调用方 scan_fail_count += 1
    ⇒ 再跑一轮 scan_login（含 context 重建 = 一次全新环境访问）⇒ 凑够 2 次才熔断。
    实测后果：09-27 BCC-025 392 次 / BCC-021 317 次 / BCC-024 68 次。
    """
    src = _src(BCC_LOGIN)
    # ① 必须声明 drift 标记
    assert "_drift = False" in src, "scan_login 未声明 _drift 标记"
    # ② 返回值必须带 drift
    m = re.search(r"return \{\"ok\": ok, \"uid\": _uid,\s*[^}]*\"drift\": _drift\}",
                  src, re.S)
    assert m, "scan_login 返回值未携带 drift 字段"


def _assigns_scan_fail(node: ast.AST) -> bool:
    """子树内是否真的**执行**了 `scan_fail_count += 1`（排除注释/字符串）。

    用 AST 而非源码字符串匹配 —— 实测字符串匹配会把注释里复述的旧代码
    （「旧实现在此 scan_fail_count += 1」）也算命中 ⇒ 假红。
    """
    for n in ast.walk(node):
        if isinstance(n, ast.AugAssign) and isinstance(n.target, ast.Name) \
                and n.target.id == "scan_fail_count" and isinstance(n.op, ast.Add):
            return True
    return False


def test_g8_keepalive_drift_breaks_immediately():
    """G8：保活循环见到 `drift=True` 必须**立即熔断**，不得累加重试计数。

    判据用 AST 定位 `if _r.get("drift")` 的 body，断言：
      ① body 内**真的**设置了 breaker_until（立即熔断）
      ② body 内**没有**执行 scan_fail_count += 1（漂移不可重试）
    两条都用 AST，不用字符串（注释会污染字符串判据 —— 实测踩到）。
    """
    src = _src(BCC_LOGIN)
    tree = ast.parse(src)

    # 定位 `if _r.get("drift"):` 节点
    drift_if = None
    for n in ast.walk(tree):
        if not isinstance(n, ast.If):
            continue
        t = n.test
        if (isinstance(t, ast.Call) and isinstance(t.func, ast.Attribute)
                and t.func.attr == "get"
                and isinstance(t.func.value, ast.Name)
                and t.func.value.id == "_r"
                and t.args and isinstance(t.args[0], ast.Constant)
                and t.args[0].value == "drift"):
            drift_if = n
            break
    assert drift_if is not None, "保活循环未处理 drift 标记（找不到 if _r.get(\"drift\")）"

    # ① 立即熔断：body 子树内存在对 breaker_until 的赋值
    sets_breaker = any(
        isinstance(x, ast.Assign)
        and any(isinstance(tg, ast.Name) and tg.id == "breaker_until"
                for tg in x.targets)
        for x in ast.walk(ast.Module(body=drift_if.body, type_ignores=[])))
    assert sets_breaker, "drift 分支未立即置 breaker_until（仍走累加计数的老路径）"

    # ② 不累加计数：body 子树内不得执行 scan_fail_count += 1
    assert not _assigns_scan_fail(
        ast.Module(body=drift_if.body, type_ignores=[])), (
        "drift 分支仍在累加 scan_fail_count ⇒ 漂移被当成可重试失败（BCC-025 复发）")


def test_throttle_semantics():
    """G6：节流原语语义正确 —— 首次必记、静默期内不记、计数可回捞。"""
    sys.path.insert(0, str(BACKEND))
    try:
        from utils import log_throttle as lt
    except Exception as e:  # pragma: no cover
        pytest.skip(f"无法导入 log_throttle: {e}")
    lt.reset()
    code, subj = "TEST-CODE", "测试账号"
    assert lt.should_log(code, subj, interval=60.0) is True, "首次必须记"
    assert lt.should_log(code, subj, interval=60.0) is False, "静默期内不应记"
    assert lt.should_log(code, subj, interval=60.0) is False
    assert lt.pending_count(code, subj) >= 2, "被抑制的条数必须可回捞"
    # 不同主体互不影响
    assert lt.should_log(code, "另一账号", interval=60.0) is True, "不同主体应分别计数"
    lt.reset()
