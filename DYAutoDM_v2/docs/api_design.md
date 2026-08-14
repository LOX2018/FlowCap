# API 设计规范

## 命名

- URL 全小写，复数名词：`/api/accounts`、`/api/messages/conversations`
- 动作用 POST 子路径：`/api/accounts/{name}/check`、`/api/engine/start`
- 查询参数用小驼峰：`?accountId=X`
- 状态用枚举字符串：`state=captured|sent|fail`（不用中文）

## 响应格式

所有响应都是 Pydantic 模型，不返回裸 dict。

成功：
```json
{"ok": true, "data": {...}}
```

错误（HTTP 状态码 + FastAPI 默认）：
```json
{"detail": "错误描述"}
```

## 端点清单

### 引擎控制 `/api/engine`

| Method | Path | 说明 | 迁移自 |
|---|---|---|---|
| POST | `/start` | 启动引擎 | WebBridge.start |
| POST | `/pause` | 暂停 | WebBridge.pause |
| POST | `/resume` | 恢复 | WebBridge.resume |
| POST | `/stop` | 硬停止 | WebBridge.stop |
| POST | `/stop-soft` | 软停止（存量发完） | (新，原版 stop_keep_queue) |

### 账号 `/api/accounts`

| Method | Path | 说明 | 迁移自 |
|---|---|---|---|
| GET | `` | 轻量列表（端口探活） | WebBridge.getAccounts (拆分) |
| POST | `` | 添加账号 | WebBridge.addAccount |
| DELETE | `/{name}` | 删除账号 | WebBridge.removeAccount |
| POST | `/{name}/check` | 引擎校验（重量级） | WebBridge.checkAccount |
| POST | `/{name}/scan` | 扫码登录 | WebBridge.scanLogin |
| GET | `/{name}/scan-status` | 扫码状态（新增） | (新) |
| POST | `/{name}/role` | 设置角色 | WebBridge.setRoles |

### 直播 `/api/live`

| Method | Path | 说明 | 迁移自 |
|---|---|---|---|
| GET | `/stream` | 直播流快照 | WebBridge.getLiveStream |
| WS | `/ws` | 实时弹幕推送（新增） | (新，替代轮询) |
| POST | `/danmaku` | 发弹幕 | WebBridge.sendDanmaku |
| POST | `/dm-template` | 配置私信模板 | WebBridge.saveDmPool |

### 私信 `/api/messages`

| Method | Path | 说明 | 迁移自 |
|---|---|---|---|
| GET | `/conversations?account=X` | 会话列表 | WebBridge.getConversations |
| GET | `/conversation?account=X&conv_id=Y` | 会话详情 | WebBridge.getConversation |
| POST | `/send` | 手动发送 | WebBridge.sendDm |

### 任务 `/api/tasks`

| Method | Path | 说明 | 迁移自 |
|---|---|---|---|
| GET | `` | 任务配置 + 记录 | WebBridge.getTasks |
| POST | `/config` | 保存配置 | WebBridge.saveConfig |
| POST | `/dm-pool` | 保存私信池 | WebBridge.saveDmPool |

### 总览 `/api`

| Method | Path | 说明 |
|---|---|---|
| GET | `/status` | 后端存活 |
| GET | `/overview` | 总览（引擎+账号+守护） |
| GET | `/stats` | 实时统计 |

### 设置 `/api/settings`

| Method | Path | 说明 |
|---|---|---|
| GET | `` | 读取配置 |
| POST | `` | 保存配置 |

## 守护进程内部 API（不暴露给前端）

### browser_daemon（每账号一个）

| Method | Path | 说明 |
|---|---|---|
| GET | `/status` | 凭证状态 |
| POST | `/refresh` | 强制重扫码 |
| POST | `/quit` | 退出 |

### recv_daemon（每账号一个）

| Method | Path | 说明 |
|---|---|---|
| GET | `/status` | 连接状态 |
| GET | `/conversations` | 会话列表 |
| GET | `/conversation` | 会话详情 |
| POST | `/send` | 发送私信 |
| POST | `/quit` | 退出 |

## WebSocket 推送格式

`/api/live/ws` 推送的消息：

```typescript
type WsEvent =
  | { type: 'message'; data: LiveMessage }
  | { type: 'heat'; data: number }
  | { type: 'status'; data: { alive: boolean } }
```
