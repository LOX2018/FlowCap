# M-9 SEND-006 调度透传缺口调查报告

## 现象

弹幕带真实 uid（如 `3447676528502142`），直播间公屏能正常捕获弹幕，但调度报：

```
[SEND-006] [调度] 私信未入池（账号=xxx 目标=3447676528502142）: 账号/对端 uid/内容缺失
```

私信不发。两账号均复现。

## 数据流追踪

```
live_hook.py:463
  self.dispatch.submit(target)
  │ target = {"user_id": 3447676528502142, "sec_uid": ..., "nickname": ..., "comment": "弹幕文本"}
  │
  ▼
dispatch.py:254  submit(target)
  │ → _dedup_key(target)  → 用 user_id/sec_uid/nickname 构造 key
  │ → 入队，等待延迟窗口
  │
  ▼
dispatch.py:393  _do_send(key, target)
  │
  ├─ 403:  content = ""
  ├─ 406:  if gen_dm_message is not None:   ❌ AI 未启用 → 不触发
  ├─ 419:  if not content and pick_dm_message is not None:   ❌ 词库无启用的模板 → 不触发
  │       content 仍然为 ""
  │
  ├─ 431:  _uid = str(target.get("user_id") or target.get("uid") or "").strip()
  │       ✅ _uid = "3447676528502142"
  ├─ 432:  _acct = getattr(self.auth, "account_name", "") or ""
  │       ✅ _acct = "xxx"
  │
  ├─ 437:  _r = _gd().submit_by_uid(_acct, _uid, content, source="dispatch")
  │                           └────────────────┘ ▲ content="" ← 空串
  │
  ▼
dm_dispatch.py:1081  submit_by_uid(account, peer_uid, text, ...)
  │ peer_uid = "3447676528502142"  ✅ 非空
  │ text     = ""                  ❌ 空串
  │
  ├─ 1093:  if not account or not peer_uid or not text:
  │         └──────────────────────────────┘
  │         text 为空 → 条件成立
  └─ 1094:  return SubmitResult(False, error="账号/对端 uid/内容缺失")
```

### 关键发现

| 节点 | 值 | 状态 |
|------|-----|------|
| live_hook target.user_id | `3447676528502142` | ✅ 存在且非空 |
| dispatch submit 传入 target | 含 user_id | ✅ |
| dispatch _do_send _uid | `"3447676528502142"` | ✅ 正确提取 |
| dispatch _do_send content | `""` | ❌ **空串** |
| submit_by_uid peer_uid | `"3447676528502142"` | ✅ 非空 |
| submit_by_uid text | `""` | ❌ **空串 → 拒绝** |

## 根因

**`dispatch.py` 的 `_do_send()` 方法在 `content` 生成链路中，缺少对 `target["comment"]`（弹幕原文）的回退兜底。**

具体路径（`backend/core/dispatch.py` 第 403-424 行）：

1. `content = ""` ← 初始值
2. `gen_dm_message` 回调：仅在 AI 配置启用（scopes 含 `"live"`、非 `kb_only`）时才非 None → 否则跳过
3. `pick_dm_message` 回调：仅在词库有已启用的模板时才非 None → 否则跳过
4. 两者都跳过 → **`content` 保持空串 `""`**
5. 空串传入 `submit_by_uid` → `if not text` 触发 → `SEND-006 内容缺失`

`target["comment"]` 在 `live_hook.py` 第 425 行已被正确提取并放入 target，且 `_ensure_record` 第 246 行也保存了 comment，但 `_do_send` 从未读取它作为 content 的兜底。

## 修复建议

### 修改文件

`backend/core/dispatch.py`

### 修改位置

`_do_send` 方法内，第 424 行之后（即 `pick_dm_message` 回退完成后），增加一个最终兜底：

```python
# 424 行之后新增：
if not content:
    content = str(target.get("comment") or "").strip()
```

### 为什么这不会破坏现有行为

- `gen_dm_message` 优先（AI 生成）—— 优先级不变
- `pick_dm_message` 次之（词库抽取）—— 优先级不变
- `target["comment"]` 最后兜底（弹幕原文）—— **新增的第三级回退**
- 三个来源都为空时依然会失败（罕见情况），行为合理

### 范围影响

仅直播监听（live_hook 来源）受影响。视频采集（web_probe）进入同一 `_do_send` 路径，但没有 `comment` 字段 → 不影响（空串仍为空，行为不变）。