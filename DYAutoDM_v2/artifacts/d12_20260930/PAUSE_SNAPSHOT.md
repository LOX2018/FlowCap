# D-12 暂停快照 · 2026-09-30（用户指示「先暂存后续做」）

## 状态一句话
**后端 5 项已落盘（可编译）；前端补丁脚本已就绪但未执行；门禁/回归/实机/升版/归档均未做。**

## 磁盘现状（暂停时刻实测）

改动文件（`git status`，本会话 OWNED 7 个）：

```
 M DYAutoDM_v2/backend/api/tasks.py               (+11/-1)
 M DYAutoDM_v2/backend/core/auto_dm.py            (+40)
 M DYAutoDM_v2/backend/core/dispatch.py           (+33)
 M DYAutoDM_v2/backend/models/task.py             (+5)
 M DYAutoDM_v2/backend/services/ai_agent.py       (+6)
 M DYAutoDM_v2/backend/services/ai_reply.py       (+54)
 M DYAutoDM_v2/backend/services/delivery_verify.py(+24/-10)
```

`py_compile` 全绿（7 个文件）。
**前端零改动**：`git status --porcelain -- DYAutoDM_v2/frontend/` 为空。

另一会话在役文件（**本会话未触碰**，逐字节未动）：
`test_abogus_host_guards / test_config_tag / test_dm_dispatch_config /
 test_m20_search_transport / test_replay_conversation_read / test_send_pacing_and_kind`
（`_build_version.py` 亦为其所改，本会话未动）。

## 恢复步骤（严格按序）

```bash
cd /c/Users/LOX/Desktop/DYchajian/DYAutoDM_v2

# 1) 执行前端补丁（脚本逐字段定位，缺一不写）
python artifacts/_patch_20260930_frontend.py

# 2) 前端类型/构建
cd frontend && npx tsc -b && cd ..

# 3) 新建本批门禁（含负控）后跑
python -m unittest backend.test_live_ai_send_defects -v    # 待建

# 4) 后端全量回归（失败集合须等于基线）
cd backend && python -m unittest discover -s . -p "test_*.py"

# 5) 实机验证 + 升版（提交前重读版本）+ ADR + 案例归档
```

## 待建门禁要点（`backend/test_live_ai_send_defects.py`）

| 判据 | 负控（必须变红） |
|---|---|
| `delivery_state_of(..., after_ts=T)` 只统计 T 之后的证据 | 去掉 `_tclause` ⇒ 历史回声污染即红 |
| `intent_in_scope` 空词表 ⇒ True；命中 ⇒ True；未命中 ⇒ False | 空词表返回 False ⇒ 红 |
| 意向门未配置 ⇒ `intent_verdict is None`（零回归） | 无条件构造 ⇒ 红 |
| `attempted_content` 失败时仍有值 | 只留 `content` ⇒ 红 |
| `displayStatus`：`fail` 优先于 `delivered` | 恢复旧顺序 ⇒ 红 |
| 错误计数与表格 danger 行同源 | 取 `aiSt.errors` ⇒ 红 |

## 已知遗留（诚实标注）

- **未实机验证**：两账号真实库读数已取证（污染面），但修复后的运行时行为未在真机验证。
- **意图门仅子串匹配**：不做 LLM 精判（`judge_high_value` 仍独立存在，未接线到门）。
- **`models/task.py` 源码内混有 7 行历史 LF 行**（HEAD 同样混行；本会话新增行均为 CRLF，
  未造成整文件行尾翻转）。
- 另一会话仍在改 6 个 `test_*.py`；本批提交前须复核其状态（可能已提交，也可能仍在改）。
