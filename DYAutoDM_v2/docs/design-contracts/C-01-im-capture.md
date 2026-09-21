# 设计契约 · C-01 IM 私信捕获（conversation_capture）

> 依《体系体检报告》§6.1②③ 与《架构审计报告》§九-6：把「接口定性」从**注释**变成**可执行契约**。
> 本文件是该模块的**唯一语义契约**（SSOT）。代码改动若与本文冲突，须先改本文（走变更控制）。

## 1. 设计意图

被动捕获抖音 IM 私信（**不主动请求、不批量查询**），落库为可用于 AI 回复与会话展示的结构化记录。

**为什么是被动**：昵称 / 会话等信息的批量主动查询会触发风控（用户红线：「只要没用到后端去批量查询昵称就行」）。
故本模块**只消费前端自发产生的响应**，零主动拉取。

## 2. 设计契约（DbC）

| 类型 | 内容 |
|---|---|
| **前置条件** P | ① 目标账号的常驻浏览器已启动且已登录；② BCC 被动 hook 已挂载 `im/user/info` 等前端自发端点；③ `conversation_id` 来自被动捕获，不做后端推导 |
| **后置条件** Q | ① 每条捕获记录落库且可追溯来源；② `sender_nickname` 仅采用消息自带字段（缺失即为空，**不补全**）；③ 捕获覆盖率进探针（`message_integrity`） |
| **不变式** I | Ⅰ1 **零主动请求**：本模块不得调用任何 `im/user/info` 之外的批量查询；Ⅰ2 `msg_type=7 且 msg_id IS NULL` 属脏数据，必须过滤；Ⅰ3 昵称唯一来源 = BCC 被动 hook，绝不走 `bulk_user_info` / `get_im_user_info` 等主动路径；Ⅰ4 方向判定只能用 `sender UID`（空 → `me`），**不得**用字段位置猜 |

## 3. 规范契约（字段命名 · Canonical Contract Law）

统一 snake_case；**禁止**同一语义多命名（nomenclature drift）。

| 规范字段 | 类型 | 说明 | 禁止的别名 |
|---|---|---|---|
| `conversation_id` | str | 会话唯一 id | `conv_id` / `cid` 混用 |
| `sender_uid` | str | 发送者 uid | `uid` / `user_id` 混用 |
| `sender_nickname` | str | 发送者昵称（被动来源） | `nickname` / `nick` |
| `msg_type` | int | 消息类型 | `type` |
| `msg_id` | str \| null | 消息 id（null 为脏数据） | — |
| `direction` | `me` \| `other` | 方向（由 uid 判定） | `is_me` / `from_me` |

## 4. NFR（性能 / 隔离预算）

| 指标 | 预算 | 依据 |
|---|---|---|
| 捕获延迟 | 前端响应到达后 ≤ 2s 落库 | 被动链路 |
| 主动请求数 | **恒 0** | 风控红线（硬约束，非目标） |
| 内存 | 常驻单实例，不随会话数线性增长 | 长跑稳定性 |

## 5. 验证方式（Live-Instance Verification）

```bash
# 探针（只读本地事实，零网络零浏览器）
py314 -m unittest test_capability_probe        # message_integrity 探针
# 契约守护：本模块不得出现主动批量查询符号
grep -rn "bulk_user_info\|get_im_user_info\|bulk_user_info_by_uid" \
  backend/auto_dm/conversation_capture.py       # 期望：0 命中
```

## 6. 已知缺口（诚实记录）

- 昵称覆盖率依赖前端是否自发请求 `im/user/info`；覆盖率不足时探针报 `degraded`（**不是** failed）。
- 关联归零事故（尚进账号）根因待查，见 `knowledge/cases/`。
