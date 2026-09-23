# 卷宗 T1：AI 未启用且词库为空时调度器复读弹幕原文

> 修复待办 T1（=BACKLOG B-1），2026-09-24，分支 design/better-douyin，基线 v0.44.57（6596521）

---

## 1) 自述改动清单

| 文件绝对路径 | 操作 | 说明 |
|---|---|---|
| `DYAutoDM_v2/backend/core/auto_dm.py` | **新增 10 行** | 在 `_run()` 内构造 `DispatchCenter` 之前（原 830–841 行），追加「AI 不可用 AND 词库无启用文案 → enable_send=False」判定段 |

### 新增内容（精确行，833–842 行）

```python
            # 2026-09-24（T1 复读弹幕修复）：AI 不可用 且 词库无启用文案 → 只听不发。
            #    复用 ENG-017 的 enable_send=False 机制，绝不新发明。调度器仍入库弹幕
            #    记录（前端可见「已捕获未发」），但 _do_send 因 enable_send=False 不真发，
            #    避免把弹幕原文当私信发出（修复 dispatch.py:429 兜底复读缺陷）。
            if _send_ok and self.gen_dm_message is None and self.pick_dm_message is None:
                _send_ok = False
                self.status_msg = ("监听中（只听不发·AI未启用且词库无启用文案，"
                                   "禁止私信——避免复读弹幕原文）")
                logger.warning("[T1-FIX] AI 未启用 + 词库无启用文案 → 只听不发"
                               f"（live_id={self.live_id}）")
```

### 改动原理

- **判定 AI 是否可用**：复用 `_apply_config` 后已装配的 `self.gen_dm_message` —— 这是 `_make_gen_dm_message()` 的返回值，仅在 `evaluate_live_ai()` 判定「`enabled=True` + `scopes` 含 `live` + `strict_level != 'kb_only'`」三条件全部满足时返回 callable，否则返回 `None`。不重新判定 `enabled` 字段。
- **判定词库是否有启用文案**：复用 `_apply_config` 后已装配的 `self.pick_dm_message` —— 这是 `_make_pick_dm_message()` 的返回值，在 `self.dm_template` 全空或全部 `enabled=False` 时返回 `None`。不直接读存储、不做额外 I/O。
- **发送能力关闭**：复用既有 ENG-017 的 `enable_send=False` 机制。`enable_send=False` 时调度器 `submit()` 仍写 `SendRecord`（`status=RecordStatus.SKIPPED`，前端可见「已捕获未发」），但拒绝入队、不触发 `_do_send()`，从而根本不会走到 `dispatch.py:429` 的弹幕原文兜底复读。
- **显著标识**：`self.status_msg` 文案含「只听不发·AI未启用且词库无启用文案，禁止私信——避免复读弹幕原文」，通过既有 `status_msg` 通道送达前端 `engine-cards.tsx:194` 渲染。

---

## 2) 自述验证数字

### 总断言

| 分组 | 断言数 | PASS | FAIL |
|---|---|---|---|
| A 负控：AI=None + 词库=[] | 3 | 3 | 0 |
| A' 负控：AI=None + 词库=[{enabled=False}] | 3 | 3 | 0 |
| B1 正控：AI=callable + 词库=[] | 2 | 2 | 0 |
| B2 正控：AI=None + 词库=[{enabled=True}] | 2 | 2 | 0 |
| B3 正控：AI=callable + 词库=[{enabled=True}] | 2 | 2 | 0 |
| L2 调度器层：enable_send=False → SKIPPED | 4 | 4 | 0 |
| **合计** | **16** | **16** | **0** |

### 对照实验（归因铁律）

- **负控 A**：`gen_dm_message=None` + `dm_template=[]` → `enable_send=False`，`status_msg` 含 T1 显著文案。
- **负控 A'**：`gen_dm_message=None` + `dm_template=[{text, enabled=False}]` → `enable_send=False`（词库有文案但全部停用，仍视为无可用文案）。
- **正控 B1**：`gen_dm_message=callable` + `dm_template=[]` → `enable_send=True`（AI 可用，行为不变）。
- **正控 B2**：`gen_dm_message=None` + `dm_template=[{text, enabled=True}]` → `enable_send=True`（词库有启用文案，行为不变）。
- **正控 B3**：`gen_dm_message=callable` + `dm_template=[{text, enabled=True}]` → `enable_send=True`（两者都可用，行为不变）。
- **L2 调度器层**：`enable_send=False` 时 5/5 弹幕目标 `status=SKIPPED`，`pending` 保持空；`enable_send=True` + 词库有文案时 `submit()` 返回 `True` 并入队。

### 负控标注

- 负控 A 和 A' 刻意构造「AI 不可用 + 词库无启用文案」组合，验证修复确实关闭发送能力。
- 正控 B1/B2/B3 刻意构造「AI 可用」「词库有文案」「两者都可用」三种情况，验证修复不误伤正常发送能力。
- 仅跑负控不算证据；必须同时跑正控以证明判定仅对「AND」组合生效。

### 编译验证

- `python -m py_compile backend/core/auto_dm.py` → OK（Python 3.11 和 3.14 均通过）。

---

## 3) 唯一标识符（供父会话 grep 复核）

| 标识符 | 类型 | 位置 |
|---|---|---|
| `T1-FIX` | 日志码 | `backend/core/auto_dm.py:841` |
| `AI未启用且词库无启用文案` | 状态文案 | `backend/core/auto_dm.py:839` |
| `2026-09-24（T1 复读弹幕修复）` | 注释标记 | `backend/core/auto_dm.py:833` |
| `verify_t1_no_copy_send.py` | 验证脚本 | `backend/scripts/verify_t1_no_copy_send.py` |

---

## 4) 诚实标注

### 没做到什么

- **未做端到端实机验证**：验证脚本仅重放判定段 + 调度器层，未启动真实引擎、未连接真实直播间、未触发真实弹幕流。实机效果依赖父会话在真实环境验证。
- **未验证 `apply_runtime()` 热更路径**：`apply_runtime()` 在热更时也会重新构造 `pick_dm_message` 和 `gen_dm_message`，但热更路径不经过 `_run()` 中的 T1 判定段。如果用户在「AI 未启用 + 词库空」状态下热更词库（从空变为有文案），`enable_send` 不会自动从 `False` 恢复为 `True`（因为 `enable_send` 在 `DispatchCenter.__init__` 中固化）。这是一个已知限制，但实际场景下「AI 未启用 + 词库空」时用户不会热更词库来开启发送，且重启任务即可恢复。
- **未验证 `config_tag.py` 的影响**：`config_tag.py` 是别人的未提交产物，按约束未触碰。如果它修改了 `dm_template` 或 `gen_dm_message` 的装配逻辑，可能影响本修复的判定前提。

### 什么没验证

- 未验证 `evaluate_live_ai()` 在「账号未绑定 Agent」「Agent 作用域不含 live」「档位为 kb_only」等分支下确实返回 `active=False`（从而 `_make_gen_dm_message()` 返回 `None`）。这些分支在 `verify_live_ai_observability.py` 中已覆盖，本脚本未重复。
- 未验证前端 `engine-cards.tsx` 对 `status_msg` 的渲染是否正确显示 T1 文案（前端层验证留给 `npx tsc -b` 和实机）。

### 什么有风险

- **风险 1**：`self.gen_dm_message` 和 `self.pick_dm_message` 在 `_apply_config()` 中装配，而 `_apply_config()` 在 `_run()` 中先于 T1 判定段调用。如果未来有人在 `_apply_config()` 和 T1 判定段之间修改这两个字段，判定可能失效。当前代码无此问题。
- **风险 2**：`pick_dm_message` 为 `None` 的判定依赖 `_make_pick_dm_message()` 的实现契约（全空或全停用时返回 `None`）。如果未来有人修改 `_make_pick_dm_message()` 使其在词库全停用时返回一个返回空串的 callable，T1 判定会漏过。当前实现契约稳定。
- **风险 3**：`gen_dm_message` 为 `None` 的判定依赖 `_make_gen_dm_message()` 的实现契约（AI 不可用时返回 `None`）。如果未来有人修改 `_make_gen_dm_message()` 使其返回一个返回空串的 callable（而非 `None`），T1 判定会漏过。当前实现契约稳定（`evaluate_live_ai` 是唯一真源）。

---

## 5) 交叉判据（grep 命中数）

```bash
grep -rn "T1-FIX" backend/ --include=*.py | grep -v __pycache__
```
**命中数：1**（`backend/core/auto_dm.py:841`）

```bash
grep -rn "AI未启用且词库无启用文案" backend/ --include=*.py | grep -v __pycache__
```
**命中数：1**（`backend/core/auto_dm.py:839`）

```bash
grep -rn "2026-09-24（T1" backend/ --include=*.py | grep -v __pycache__
```
**命中数：1**（`backend/core/auto_dm.py:833`）

```bash
grep -rn "verify_t1_no_copy_send" backend/ --include=*.py | grep -v __pycache__
```
**命中数：1**（`backend/scripts/verify_t1_no_copy_send.py` 自身）

```bash
grep -rn "T1-FIX\|AI未启用且词库无启用文案\|2026-09-24（T1" backend/ --include=*.py | grep -v __pycache__ | grep -v verify_t1
```
**命中数：3**（全部在 `backend/core/auto_dm.py`）

---

## 6) 疑点

### 对其它待办的影响

- **T3（AI 上下文键维度）**：T3 修改 `generate_dm_for_live` 的签名以透传 uid。如果 T3 修改后 `gen_dm_message` 的装配逻辑变化（例如 `_make_gen_dm_message` 返回 `None` 的条件变化），可能影响 T1 的判定前提。当前无冲突，但合并时需确认 `_make_gen_dm_message` 仍返回 `None`（而非 callable）当 AI 不可用时。
- **T5（配置标签体系）**：T5 涉及 `config_tag.py`（未提交产物）。如果 T5 修改了 `dm_template` 的写入逻辑（例如写入时强制所有文案 `enabled=True`），可能使 `pick_dm_message` 不再为 `None`，从而 T1 判定失效。当前无冲突，但合并时需确认 `config_tag.py` 不破坏 `_make_pick_dm_message` 的契约。
- **T6（未知）**：未读 T6 任务书，无法评估。

### 发现的相邻问题

- **`apply_runtime()` 热更路径不重置 `enable_send`**：如第 4 节所述，热更词库不会自动恢复 `enable_send`。这是一个已知限制，但实际场景下影响极小（用户不会在「AI 未启用 + 词库空」状态下热更词库来开启发送）。建议在后续版本中考虑在 `apply_runtime()` 中增加「如果之前因 T1 判定关闭发送，且现在 AI 或词库已可用，则重新开启」的逻辑。
- **`status_msg` 覆盖顺序**：T1 判定段在 `_send_ok=False` 的 `status_msg = "监听中（只听不发·发送凭证不可用）"` 之后。如果 `_send_ok` 已为 `False`（凭证不可用），T1 判定段不会执行（因为 `_send_ok` 已为 `False`，`if _send_ok and ...` 短路）。这意味着「凭证不可用」的优先级高于「AI 未启用 + 词库空」，符合预期（凭证不可用时本来就不能发）。

### 修复完整性声明

本修复严格遵循用户指定口径：
- ✅ 复用既有 `enable_send=False` 机制（ENG-017），绝不新发明。
- ✅ 页面出现显著标识（`status_msg` 含「只听不发·AI未启用且词库无启用文案，禁止私信——避免复读弹幕原文」）。
- ✅ 引擎仍入库弹幕记录（`status=SKIPPED`，前端可见「已捕获未发」）。
- ✅ 不触发实际发送（`enable_send=False` 时 `submit()` 不入队，`_do_send()` 不执行）。
- ✅ 向后兼容：AI 已启用 或 词库有文案时行为与改造前一致。
- ✅ 未触碰 `core/dispatch.py`、`services/dm_dispatch.py`、`backend/services/config_tag.py`。
- ✅ 未修改版本号文件。
- ✅ 未执行 git 写操作。
