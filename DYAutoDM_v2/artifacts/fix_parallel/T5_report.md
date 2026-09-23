# T5 卷宗：直播侧 AI 上下文隔离 user_id 用昵称塌桶修复

## 1) 自述改动清单

| 文件绝对路径 | 增/删 | 唯一标识符 |
|---|---|---|
| `C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend/services/ai_reply.py` | +1 行（签名加 `uid: str = ""`） | `uid: str = ""` |
| `C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend/services/ai_reply.py` | 改 1 行（user_id 键形） | `user_id=f"live:{account}:{uid or peer_name or 'unknown'}"` |
| `C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend/core/auto_dm.py` | +1 行（调用方透传 uid） | `uid=uid` |
| `C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend/scripts/verify_t5_live_ctx_key.py` | 新建 11 个断言 | `verify_t5_live_ctx_key.py` |

## 2) 自述验证数字

- 独立验证脚本 `backend/scripts/verify_t5_live_ctx_key.py`：**11/11 PASS**
  - `TestSignatureAndKey` (4 tests): uid 参数存在、user_id 使用 uid、调用方透传 uid、向后兼容默认值
  - `TestSessionNoAccumulation` (2 tests): AIClient 实例不共享 session_history、chat_failover 每候选新建实例
  - `TestKeyShapeBeforeAfter` (3 tests): 有 uid → `live:{account}:{uid}`；无 uid → `live:{account}:{peer_name}`；双空 → `live:{account}:unknown`
  - `TestNegativeControl` (2 tests): 同昵称不同 uid → 不同键（修复后）；同昵称同键（修复前，负控标注）
- 刻意失败态负控：`test_same_nickname_same_key_before` 标注旧行为塌桶（同昵称 → 同键），验证修复方向正确
- py_compile 编译通过：`ai_reply.py` ✅、`auto_dm.py` ✅

## 3) 唯一标识符（供父会话 grep 复核）

1. `uid: str = ""` — `services/ai_reply.py:1485`
2. `user_id=f"live:{account}:{uid or peer_name or 'unknown'}"` — `services/ai_reply.py:1537`
3. `uid=uid` — `core/auto_dm.py:560`

## 4) 诚实标注

- **没做到什么**：未修改 `core/dispatch.py` 或 `services/dm_dispatch.py`（红线约束），仅改 `auto_dm.py` 的 `_gen` 内部调用。
- **没验证什么**：未做端到端实机测试（无真实直播间弹幕流），仅做源码契约 + 单元级键形验证。
- **风险**：
  - 若 `target` 同时缺 `user_id` 和 `uid`（即 `uid` 为空字符串），键形退化为 `live:{account}:{peer_name}` —— 这是向后兼容的妥协，但保留了昵称塌桶风险。实际生产中 `live_hook.py` 的弹幕帧通常带 `user_id`（`getattr(u, "id", None)`），但加密昵称场景下 `user_id` 可能为 None。
  - `chat_failover` 的避障链每候选新建 `AIClient(cfg)` 实例，但同一候选的 `chat()` 仍会累积 `session_history` —— 不过因为实例在 `chat_failover` 返回后被丢弃，下次调用 `generate_dm_for_live` 又是全新实例，所以**不累积**。已用实测证明（`test_fresh_instance_per_call`）。

## 5) 交叉判据（grep 命中数）

```bash
grep -n 'uid: str = ""' backend/services/ai_reply.py
# → 1 行命中（ai_reply.py:1485）

grep -n 'user_id=f"live:{account}:{uid or peer_name' backend/services/ai_reply.py
# → 1 行命中（ai_reply.py:1537）

grep -n 'uid=uid' backend/core/auto_dm.py
# → 1 行命中（auto_dm.py:560）
```

## 6) 疑点

- **对其它待办的影响**：
  - T1（dispatch 空 content 兜底）：无影响，本改动不改 dispatch.py。
  - T3/T4/T6：无交叉，本改动仅涉 `generate_dm_for_live` 签名和键形。
- **相邻问题**：
  - `auto_dm.py:559` 中 `peer_name=nick or uid` 仍把昵称作为 peer_name 传给 `generate_dm_for_live`，但 peer_name 仅用于 prompt 文本（`对方昵称：{peer_name}`），不影响上下文键隔离。这是合理的 —— 给 AI 看昵称比看数字 uid 更自然。
  - `live_hook.py` 的 `WebcastChatMessage` 解析中 `user_id = getattr(u, "id", None)` —— 若抖音协议中 `id` 字段在某些场景为 0 或 None，则 uid 为空，键形退化为昵称。这是上游数据质量问题，非本修复范围。
