# HC-16 · L-18：门禁 `test_g6_section_mounted_in_settings` 过时修复报告

- 执行时间：2026-10-01
- 执行者：并行子任务 L-18（HC-16 交接卡）
- 影响面：仅 `DYAutoDM_v2/backend/test_high_value_keywords_entry.py`（未改任何业务/前端/版本文件）

---

## ① 自述改动清单

**文件：`DYAutoDM_v2/backend/test_high_value_keywords_entry.py`（唯一改动文件）**

| 位置 | 改动 | 说明 |
|---|---|---|
| 37-39 行（新增） | 新增路径常量 `LIVE_PAGE`、`MODAL` | `frontend/src/components/live/live-page.tsx`、`.../live/HighValueKeywordsModal.tsx` |
| 141-142 行（新增） | 新增正则 `_JSX_SECTION` / `_JSX_MODAL` | `<\s*HighValueKeywordsSection\b` / `<\s*HighValueKeywordsModal\b`，只认 JSX 开标签，不认 import 与注释中的裸标识符 |
| 148-166 行（新增） | 抽出纯函数 `_section_is_mounted(settings_src, live_src, modal_src) -> bool` | 供负控在**内存**中喂源码文本，无需动真实文件 |
| 168-172 行（新增） | 辅助函数 `_drop_lines_matching(src, pattern)` | 按行摘掉命中行，构造负控样本 |
| 171-203 行（改名+重写） | `test_g6_section_mounted_in_settings` → **`test_g6_section_mounted_in_settings_or_live`** | 判据改为「设置页 **或** 直播页」，直播页分支要求**两跳**都真；docstring 写明为何改判据 |
| 205-248 行（新增） | **`test_g6_negative_control_unmounted_everywhere_turns_red`** | 负控：三条摘除断言 + 正控 + 判别力断言 |

**旧实现（被替换，147 行 → 现 248 行）**：
```python
def test_g6_section_mounted_in_settings():
    page = _src(SETTINGS_PAGE)
    assert "HighValueKeywordsSection" in page, ...
    assert re.search(r"<\s*HighValueKeywordsSection\s*/?>", page), ...
```
失败原因：只查 `settings-page.tsx`，而 `7592727` 是有意把入口收敛到直播页 ⇒ 该文件内出现次数为 0（实测）。

**未改动**：`settings-page.tsx`、`live-page.tsx`、`HighValueKeywordsModal.tsx`（只读，未写）。

---

## ② 自述测试数字

### 主验证（真实输出）
```
cd C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend && python -m pytest test_high_value_keywords_entry.py -v
```
（注：默认 `python` 为 Hermes 自带 3.14.7 且**无 pytest**；实测用系统 Python314：
`"C:/Users/LOX/AppData/Local/Programs/Python/Python314/python.exe" -m pytest`，pytest 9.1.1 / Python 3.14.6）

```
============================= test session starts =============================
platform win32 -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0
cachedir: .pytest_cache
rootdir: C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\backend
plugins: anyio-4.14.2
collecting ... collected 7 items

test_high_value_keywords_entry.py::test_g1_service_exists_and_has_seed PASSED [ 14%]
test_high_value_keywords_entry.py::test_g2_empty_table_not_relazily_seeded PASSED [ 28%]
test_high_value_keywords_entry.py::test_g3_api_endpoints_exist PASSED    [ 42%]
test_high_value_keywords_entry.py::test_g4_reset_actually_writes_seed PASSED [ 57%]
test_high_value_keywords_entry.py::test_g5_frontend_client_has_calls PASSED [ 71%]
test_high_value_keywords_entry.py::test_g6_section_mounted_in_settings_or_live PASSED [ 85%]
test_high_value_keywords_entry.py::test_g6_negative_control_unmounted_everywhere_turns_red PASSED [100%]

============================== 7 passed in 0.10s =============================
```

- **passed = 7 / failed = 0**。其中 G1-G5（5 项）为既有门禁，未受影响。
- **刻意失败态样本数 = 0**（本次没有留下任何处于红态的测试；负控是"必须红"的**子断言**，在测试内部以 `assert ... is False` 表达，故整条测试仍绿）。

### 负控三条断言的通过情况（全部通过）

| # | 样本（内存构造，未落盘） | 期望 | 实际 | 结果 |
|---|---|---|---|---|
| 基线 | 真实三份源码 | True | `True` | ✅ |
| 负控 A | 摘掉 `live-page.tsx` 中 `<HighValueKeywordsModal` 那行 | False | `False` | ✅ |
| 负控 B | 摘掉 `HighValueKeywordsModal.tsx` 中 `<HighValueKeywordsSection` 那行 | False | `False` | ✅ |
| 负控 C | 两处都摘 | False | `False` | ✅ |
| 正控 | 设置页补 JSX `<HighValueKeywordsSection />`（其余都摘） | True | `True` | ✅ |
| 判别力 | live-page 只剩 `import ... from "./HighValueKeywordsModal"`（无 JSX） | False | `False` | ✅ |

### 变异测试（额外取证：负控自身是不是摆设）
把 `_section_is_mounted` 在内存中替换成恒返回 `True` 的 lax 版本后跑负控函数：

```
MUTATION: negative control correctly went RED -> 负控 A 失败：摘掉 live-page 的 Modal 挂载后门禁仍绿 ⇒ 判据不判直播页那一跳
```
⇒ 证明负控**真的会红**，不是"永远绿的摆设断言"。

---

## ③ 唯一标识符（本次新增/改名）

| 标识符 | 类型 | 位置 |
|---|---|---|
| `test_g6_section_mounted_in_settings_or_live` | 改名的门禁主判据 | `test_high_value_keywords_entry.py:171` |
| `test_g6_negative_control_unmounted_everywhere_turns_red` | 新增负控测试 | `test_high_value_keywords_entry.py:205` |
| `_section_is_mounted` | 判定纯函数（新增） | `test_high_value_keywords_entry.py:148` |
| `_drop_lines_matching` | 负控辅助函数（新增） | `test_high_value_keywords_entry.py:168` |
| `_JSX_SECTION` / `_JSX_MODAL` | JSX 开标签正则（新增） | `test_high_value_keywords_entry.py:141-142` |
| `LIVE_PAGE` / `MODAL` | 路径常量（新增） | `test_high_value_keywords_entry.py:38-39` |

---

## ④ 诚实标注（没做到的 / 不确定的）

1. **未跑全量 discover**（按硬约束，避免与并行工作线争共享测试根）。因此只证明本文件 7 项绿 + 全量里此项不再红；**未重新验证 1384 passed 的整体数字**。父会话串行跑全量时才能确认。
2. **未做端到端 UI 验证**（没起 Tauri/前端去肉眼确认弹窗能打开）。本门禁只做**源码层挂载判据**，与既有 G5/G6 同层级；"用户真能看见"的运行时验证不在本任务范围。
3. **`python -m pytest` 直跑失败**：默认 `python`（Hermes 自带 3.14.7 便携版）无 pytest；我改用系统 `Python314` 跑。**父会话复跑时需注意用同一个解释器**，否则误判为"命令失败"。
4. **判据只覆盖两条宿主页分支**（设置页 / 直播页）。若将来入口再迁到第三处（如独立路由页），本门禁会转红 —— 这是**有意**的：宁可转红提醒同步，也不做"全仓 grep 任意位置出现即绿"的退化判据。
5. **`_JSX_SECTION` 用 `\b` 而非 `\s*/?>`**：可容忍带属性/带换行的开标签（当前 `<HighValueKeywordsSection embedded />` 可命中）；代价是不校验"标签闭合"。判定强度略低于严格解析 AST，但与仓库既有前端门禁（如 `test_live_rooms.py` 的 mounted 判据）层级一致。
6. 负控样本全在**内存**构造，**未创建任何临时文件**，也未写任何真实前端文件（符合"只写两个文件"的硬约束）。

---

## ⑤ 交叉判据（grep 命令 + 实际命中数）

```bash
cd C:/Users/LOX/Desktop/DYchajian

# 1) 两个新函数名（各 1 命中）
grep -rn "test_g6_section_mounted_in_settings_or_live"            --include=*.py .
grep -rn "test_g6_negative_control_unmounted_everywhere_turns_red" --include=*.py .
# 实际：
#   ./DYAutoDM_v2/backend/test_high_value_keywords_entry.py:171
#   ./DYAutoDM_v2/backend/test_high_value_keywords_entry.py:205
#   命中数：各 1（合计 2）

# 2) 纯函数 _section_is_mounted
grep -rn "_section_is_mounted" --include=*.py .
# 实际：8 命中（1 处 def @148 + 6 处调用 @196/222/228/234/238/243/248 + 1 处 docstring 提及 @208）
#   → 定义 1，调用 6，docstring 提及 1

# 3) 真实挂载链的两跳（源码层事实，非门禁自述）
grep -n "<HighValueKeywordsModal"   DYAutoDM_v2/frontend/src/components/live/live-page.tsx
# 实际：1 命中 @705      <HighValueKeywordsModal open={kwOpen} onClose={() => setKwOpen(false)} />
grep -n "<HighValueKeywordsSection" DYAutoDM_v2/frontend/src/components/live/HighValueKeywordsModal.tsx \
                                    DYAutoDM_v2/frontend/src/components/settings/settings-page.tsx
# 实际：1 命中 @HighValueKeywordsModal.tsx:68   <HighValueKeywordsSection embedded />
#       0 命中 @settings-page.tsx（该文件仅 140 行注释里出现 HighValueKeywordsModal 字样）

# 4) 同语义门禁是否存在第二份（"先搜索再动手"这一步的取证）
grep -rn "HighValueKeywords" --include=*.py DYAutoDM_v2/backend/
# 实际命中文件：仅 test_high_value_keywords_entry.py（无第二份实现同事实的判据）
#   ⇒ 结论：不需要新建第二份 / 不需要收敛，就地改判据即可

# 5) 旧函数名是否还有残留引用（应为 0）
grep -rn "test_g6_section_mounted_in_settings\b" --include=*.py .
# 实际：0 命中（旧名已完全替换；注意 .pytest_cache 里仍缓存旧 nodeid，属历史缓存，非代码）
```

---

## ⑥ 疑点

1. **`.pytest_cache/v/cache/lastfailed` 仍记录旧 nodeid** `"test_high_value_keywords_entry.py::test_g6_section_mounted_in_settings": true`。这是 pytest 的历史缓存，不是代码；我**未删除/未改**（属 git 状态外的缓存文件，但清它无意义且可能影响并行线）。父会话全量跑完后会自动被覆盖。若全量报告里仍出现该旧名，需检查是否有别的 runner 缓存未刷新。
2. **设置页分支目前是"死分支"**：判据里保留了"设置页挂载即绿"的分支，但真实 `settings-page.tsx` 从未挂载该组件（0 命中）。我保留它是为了**将来若回迁设置页不用再改门禁**，且正控断言已确保该分支不会写死成 False。代价：若将来有人误以为"设置页是主入口"，docstring 已明写当前事实在直播页。
3. **`7592727` 的"只保留直播页入口"是否真的符合产品意图** —— 本任务**不判断**这一点（属他人语义，台账 L-18 已注明）。我只让门禁与**当前事实**对齐，而非反过来让代码迁就门禁。若产品后来要求设置页也应有入口，应改前端、不是改门禁。
4. **入口可达性未判**：`live-page.tsx` 里 `kwOpen` 由哪个按钮触发、该按钮是否可见，门禁未覆盖。只判"Modal 被渲染"，不判"用户点得到"。若将来按钮被隐藏，本门禁**不会红** —— 这是已知的覆盖边界。
5. 并行线风险：本报告路径 `DYAutoDM_v2/artifacts/HC16_L18_report.md` 为 L-18 独占；但 `test_high_value_keywords_entry.py` 若被另一并行子任务同时编辑会冲突 —— 已按硬约束只改这一个文件，且未动 git（`git add/commit/checkout/stash/clean` 全部未执行）。
