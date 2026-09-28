# -*- coding: utf-8 -*-
"""前端启动诊断日志**防渲染风暴**门禁（2026-09-28 · DSSCC-UI-007）。

## 缺陷本体（2026-09-28 部署后巡检发现）

`frontend/src/App.tsx` 的「启动诊断」effect **漏写依赖数组**（以 `});` 结尾）⇒
React **每次渲染都执行**，每次都 `invoke("write_boot_log", ...)`；而
`src-tauri/src/lib.rs` 的 `write_boot_log` 只 append、**从不轮转** ⇒
实测累积 **281,595 行 / 31 MB**（`logs/frontend_boot.log`），
既吃磁盘，又把真实日志淹没在噪声里。

## 两条判据（缺一不可）

1. **调用点**：诊断 effect 必须带依赖数组（否则渲染即写）。
2. **写入端**：`write_boot_log` 必须做**体积有界**处理（否则长期累积无上限）。

## ⚠️ 实测踩到的陷阱（本门禁专门锁住）

修 C1 时**差点引入新缺陷**：依赖数组在**渲染期求值**，而 `memberName` 等
`useState` 声明原在 effect **下方** ⇒ 直接加数组会因 const 的 **TDZ**
抛 `Cannot access before initialization`（白屏级故障）。
故 C2 断言「effect 位于其依赖的状态声明**之后**」。
"""
from __future__ import annotations

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
ROOT = BACKEND.parent
APP = ROOT / "frontend" / "src" / "App.tsx"
LIB = ROOT / "src-tauri" / "src" / "lib.rs"

DEPS = ("memberName", "memberChecked", "prealigned", "ready", "overviewEverOk")


def _code_lines(path: Path) -> list[str]:
    """仅取代码行（剔除 // 与 /// 注释），避免被解释性注释误伤。"""
    out = []
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = ln.strip()
        if s.startswith("//") or s.startswith("///") or s.startswith("*"):
            continue
        out.append(ln)
    return out


def test_c1_render_diag_effect_has_dependency_array() -> None:
    """诊断 effect 必须带依赖数组 —— 否则每次渲染都写一行。"""
    code = _code_lines(APP)
    # 找到含 [render] 的 invoke 所在 effect 的收尾
    idx = next(i for i, ln in enumerate(code) if "[render]" in ln)
    tail = code[idx: idx + 6]
    joined = "\n".join(tail)
    assert re.search(r"\}\s*,\s*\[", joined), (
        "回归锚点：诊断 effect 必须以 `}, [deps])` 收尾。\n"
        "漏依赖数组 ⇒ React 每次渲染都 invoke(write_boot_log) ⇒ 渲染风暴。"
    )
    for d in DEPS:
        assert d in joined, f"依赖数组必须包含状态量 {d}（诊断要记录的就是它的变迁）"


def test_c2_effect_declared_after_its_dependencies() -> None:
    """⚠️ TDZ 判据：依赖数组在渲染期求值，effect 必须**晚于**其依赖的声明。

    实测教训：把带数组的 effect 放在 `const [memberName, ...] = useState()` 之前，
    直接抛 `Cannot access 'memberName' before initialization`（白屏）。
    """
    text = APP.read_text(encoding="utf-8", errors="replace")
    eff = text.index("[render]")
    decls = {
        "memberName": r"const \[\s*memberName\s*,\s*set",
        "memberChecked": r"const \[\s*memberChecked\s*,\s*set",
        "prealigned": r"const \[\s*prealigned\s*,\s*set",
        "overviewEverOk": r"const \[\s*overviewEverOk\s*,\s*set",
        "ready": r"isSuccess:\s*ready\s*\}",   # ← 实测漏掉的正是它（tsc TS2448）
    }
    for d, pat in decls.items():
        m = re.search(pat, text)
        assert m, f"未找到 {d} 的声明"
        assert m.start() < eff, (
            f"TDZ 缺陷：`{d}` 声明在第 {m.start()} 字符，而诊断 effect 引用它在第 {eff} 字符 "
            f"—— effect 必须移到**全部**依赖声明之后，否则渲染期求值即抛 "
            f"`Cannot access '{d}' before initialization`（tsc TS2448）。"
        )


def test_c3_write_boot_log_bounds_file_size() -> None:
    """写入端必须有界：单文件轮转（体积阈值 + 轮转动作）。"""
    code = _code_lines(LIB)
    fn = None
    for i, ln in enumerate(code):
        if "fn write_boot_log" in ln:
            fn = "\n".join(code[i: i + 30])
            break
    assert fn, "未找到 write_boot_log"
    assert re.search(r"\d+\s*\*\s*1024\s*\*\s*1024", fn), \
        "write_boot_log 必须定义字节上限（如 10 * 1024 * 1024）"
    assert "metadata" in fn and ".len()" in fn, "必须读取当前文件大小作为轮转判据"
    assert "rename" in fn and ".1" in fn, "必须把超限文件轮转为 .1 备份（占用有界）"


def test_c4_negative_control_removing_deps_turns_c1_red() -> None:
    """负控：模拟「删掉依赖数组」的回归，证明 C1 的判据会真的变红（非恒真）。"""
    code = _code_lines(APP)
    idx = next(i for i, ln in enumerate(code) if "[render]" in ln)
    win = "\n".join(code[idx: idx + 6])
    assert re.search(r"\}\s*,\s*\[", win), "前置条件：当前应带依赖数组"

    regressed = re.sub(r"\s*\},\s*\[[^\]]*\]\s*\);", "});", win)
    assert regressed != win, "负控构造失败：未能复现「删数组」形态"
    assert not re.search(r"\}\s*,\s*\[", regressed), \
        "负控生效：删掉依赖数组后，C1 的判据（`}, [`）不再命中 ⇒ C1 会变红"
