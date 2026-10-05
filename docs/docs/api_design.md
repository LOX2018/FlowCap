# API 设计规范

> 本文说明**命名约定与响应格式**，并按模块给出真实端点清单。
> 端点清单由 AST 解析 `backend/api/*.py` 生成（2026-09-11），共 **157 个端点（156 HTTP + 1 WebSocket）/ 16 个路由模块**。

---

## 一、命名约定

- **URL 全小写，复数名词**：`/api/accounts`、`/api/messages/conversations`
- **动作用 POST 子路径**：`/api/accounts/{name}/check`、`/api/engine/start`
- **查询参数小驼峰**：`?accountId=X`
- **状态用枚举字符串**：`state=captured|sent|fail`，**不用中文**（V1 用中文状态串导致前端 `=== '发送失败'` 永不匹配，见 [`migration_guide.md`](migration_guide.md)）
- **前缀即业务域**：每个路由模块挂一个固定前缀（见下表），不跨域拼路径

---

## 二、响应格式（写接口时务必遵守）

**实际约定是「扁平 dict」，不是 `{"ok":true,"data":{...}}` 包装。**

成功：

```json
{"ok": true, "accounts": [ ... ]}
{"ok": true, "total": 312, "codes": [ ... ]}
```

失败（业务失败返回 200 + `ok:false`，不抛 HTTP 错误）：

```json
{"ok": false, "error": "账号 xxx 不存在"}
{"ok": false, "msg": "私信守护拉起失败，请查看日志"}
```

- 多数接口返回**裸 dict**；少数接口（如 `POST /api/accounts/{name}/scan`）返回 Pydantic 模型（`ScanLoginResponse`），字段为 `ok` / `msg`。
- 前端 `client.ts` 里 `ok:` 出现 134 处、`data:` 出现 0 处 —— 印证扁平约定。
- HTTP 层错误仍用 FastAPI 默认：非 200 时 body 为 `{"detail": "..."}`。

---

## 三、端点清单

### `/api/ai` — 54 个（前缀 `/api/ai`）

| Method | Path |
|---|---|
| GET | `/api/ai/replies` |
| POST | `/api/ai/replies` |
| DELETE | `/api/ai/replies/{item_id}` |
| POST | `/api/ai/replies/learn` |
| GET | `/api/ai/prokb` |
| POST | `/api/ai/prokb` |
| DELETE | `/api/ai/prokb/{item_id}` |
| POST | `/api/ai/prokb/import` |
| POST | `/api/ai/prokb/import/confirm` |
| POST | `/api/ai/prokb/migrate_qa` |
| PATCH | `/api/ai/prokb/{item_id}` |
| GET | `/api/ai/prokb/topics` |
| POST | `/api/ai/prokb/rename_topic` |
| GET | `/api/ai/prokb/maintain/status` |
| POST | `/api/ai/prokb/maintain/scan` |
| POST | `/api/ai/prokb/maintain/apply` |
| POST | `/api/ai/prokb/maintain/merge` |
| POST | `/api/ai/prokb/maintain/learn` |
| POST | `/api/ai/prokb/maintain/run` |
| POST | `/api/ai/prokb/maintain/scheduler` |
| GET | `/api/ai/prokb/recycle` |
| POST | `/api/ai/prokb/recycle/{item_id}/restore` |
| POST | `/api/ai/prokb/recycle/purge` |
| GET | `/api/ai/agents` |
| GET | `/api/ai/dispatch_agent` |
| POST | `/api/ai/dispatch_agent` |
| GET | `/api/ai/agents/{agent_id}` |
| POST | `/api/ai/agents` |
| DELETE | `/api/ai/agents/{agent_id}` |
| POST | `/api/ai/bind` |
| GET | `/api/ai/bind` |
| GET | `/api/ai/config` |
| POST | `/api/ai/config` |
| POST | `/api/ai/test` |
| POST | `/api/ai/test_vision` |
| GET | `/api/ai/knowledge` |
| POST | `/api/ai/knowledge` |
| DELETE | `/api/ai/knowledge/{item_id}` |
| POST | `/api/ai/knowledge/import` |
| POST | `/api/ai/knowledge/import/confirm` |
| POST | `/api/ai/semantic/test` |
| POST | `/api/ai/semantic/rebuild` |
| GET | `/api/ai/semantic/cache_status` |
| GET | `/api/ai/blacklist` |
| POST | `/api/ai/blacklist` |
| DELETE | `/api/ai/blacklist` |
| GET | `/api/ai/providers` |
| GET | `/api/ai/providers/freellm_models` |
| GET | `/api/ai/status` |
| POST | `/api/ai/start` |
| POST | `/api/ai/stop` |
| GET | `/api/ai/leads` |
| POST | `/api/ai/leads/status` |
| GET | `/api/ai/leads/export` |

### `/api/accounts` — 15 个（前缀 `/api/accounts`）

| Method | Path |
|---|---|
| GET | `/api/accounts` |
| GET | `/api/accounts/self-check` |
| POST | `/api/accounts/{name}/check` |
| POST | `/api/accounts/{name}/ensure-bcc` |
| POST | `/api/accounts/{name}/ensure-recv` |
| POST | `/api/accounts/{name}/open-browser` |
| POST | `/api/accounts/{name}/scan` |
| GET | `/api/accounts/{name}/scan-status` |
| POST | `/api/accounts/{name}/role` |
| POST | `/api/accounts` |
| DELETE | `/api/accounts/{name}` |
| POST | `/api/accounts/{name}/stop-browser` |
| POST | `/api/accounts/{name}/stop-recv` |
| POST | `/api/accounts/{name}/auto-recapture` |
| GET | `/api/accounts/{name}/proxy-status` |

### `/api/messages` — 11 个（前缀 `/api/messages`）

| Method | Path |
|---|---|
| GET | `/api/messages/conversations` |
| GET | `/api/messages/conversation` |
| POST | `/api/messages/origin_image/resolve` |
| GET | `/api/messages/origin_image/stats` |
| GET | `/api/messages/origin_image/{filename}` |
| POST | `/api/messages/origin_image/sweep` |
| POST | `/api/messages/request` |
| POST | `/api/messages/send` |
| POST | `/api/messages/send_image` |
| POST | `/api/messages/wp_send` |
| POST | `/api/messages/{account}/refresh` |

### `/api/settings` — 11 个（前缀 `/api/settings`）

| Method | Path |
|---|---|
| GET | `/api/settings` |
| GET | `/api/settings/schema` |
| POST | `/api/settings` |
| GET | `/api/settings/tags` |
| POST | `/api/settings/tags` |
| DELETE | `/api/settings/tags/{tag_id}` |
| POST | `/api/settings/tags/bind` |
| GET | `/api/settings/tags/bind` |
| POST | `/api/settings/scoped` |
| GET | `/api/settings/scoped/{tag_id}` |
| POST | `/api/settings/reset` |

### `/api/model_hub` — 11 个（前缀 `/api/modelhub`）

| Method | Path |
|---|---|
| GET | `/api/modelhub/overview` |
| POST | `/api/modelhub/providers` |
| DELETE | `/api/modelhub/providers/{pid}` |
| POST | `/api/modelhub/providers/{pid}/test` |
| POST | `/api/modelhub/providers/{pid}/fetch` |
| POST | `/api/modelhub/models` |
| POST | `/api/modelhub/models/{mid}/caps` |
| DELETE | `/api/modelhub/models/{mid}` |
| POST | `/api/modelhub/routes/{kind}` |
| POST | `/api/modelhub/fallback` |
| POST | `/api/modelhub/consumers` |

### `/api/notify` — 10 个（前缀 `/api/notify`）

| Method | Path |
|---|---|
| GET | `/api/notify/status` |
| GET | `/api/notify/config` |
| POST | `/api/notify/config` |
| GET | `/api/notify/gateway` |
| POST | `/api/notify/gateway/mode` |
| POST | `/api/notify/gateway/approve` |
| POST | `/api/notify/gateway/revoke` |
| POST | `/api/notify/gateway/allow` |
| POST | `/api/notify/test` |
| POST | `/api/notify/command` |

### `/api/tasks` — 7 个（前缀 `/api/tasks`）

| Method | Path |
|---|---|
| GET | `/api/tasks/history` |
| GET | `/api/tasks/current` |
| POST | `/api/tasks/history/clear` |
| GET | `/api/tasks` |
| POST | `/api/tasks/config` |
| POST | `/api/tasks/dm-pool` |
| POST | `/api/tasks/export` |

### `/api/member` — 7 个（前缀 `/api/member`）

| Method | Path |
|---|---|
| POST | `/api/member/register` |
| POST | `/api/member/login` |
| POST | `/api/member/logout` |
| GET | `/api/member/state` |
| POST | `/api/member/change-password` |
| GET | `/api/member/list` |
| POST | `/api/member/delete` |

### `/api/engine` — 5 个（前缀 `/api/engine`）

| Method | Path |
|---|---|
| POST | `/api/engine/start` |
| POST | `/api/engine/pause` |
| POST | `/api/engine/resume` |
| POST | `/api/engine/stop` |
| POST | `/api/engine/stop-soft` |

### `/api/live` — 5 个（前缀 `/api/live`）

| Method | Path |
|---|---|
| GET | `/api/live/stream` |
| WEBSOCKET | `/api/live/ws` |
| POST | `/api/live/danmaku` |
| POST | `/api/live/dm-template` |
| POST | `/api/live/resolve` |

### `/api/crawl` — 5 个（前缀 `/api/crawl`）

| Method | Path |
|---|---|
| POST | `/api/crawl/search` |
| POST | `/api/crawl/comments` |
| POST | `/api/crawl/dm` |
| POST | `/api/crawl/batch` |
| GET | `/api/crawl/history` |

### `/api/live_config` — 5 个（前缀 `/api/live/room-configs`）

| Method | Path |
|---|---|
| GET | `/api/live/room-configs` |
| GET | `/api/live/room-configs/{room_id}` |
| POST | `/api/live/room-configs` |
| DELETE | `/api/live/room-configs/{room_id}` |
| POST | `/api/live/room-configs/{room_id}/apply` |

### `/api/logs` — 4 个（前缀 `/api/logs`）

| Method | Path |
|---|---|
| POST | `/api/logs/write` |
| GET | `/api/logs/sessions` |
| GET | `/api/logs` |
| DELETE | `/api/logs/sessions` |

### `/api/linkmic` — 3 个（前缀 `/api/live/linkmic`）

| Method | Path |
|---|---|
| POST | `/api/live/linkmic/apply` |
| GET | `/api/live/linkmic/status` |
| POST | `/api/live/linkmic/leave` |

### `/api/overview` — 2 个（前缀 `/api`）

| Method | Path |
|---|---|
| GET | `/api/overview` |
| GET | `/api/stats` |

### `/api/errcodes` — 2 个（前缀 `/api/errcodes`）

| Method | Path |
|---|---|
| GET | `/api/errcodes` |
| GET | `/api/errcodes/{code}` |

---

## 四、WebSocket

`WS /api/live/ws`（`backend/api/live.py`）。

> ⚠️ **当前为骨架**：该端点已注册但尚未接线（源码内 3 处 `TODO`），前端 `live.tsx` 也仍用 5s 轮询，**不是既成的实时推送通道**。文档曾称「已替代轮询」，属过时描述。

---

## 五、守护进程内部 API（独立进程，不暴露给前端）

### browser_daemon（每账号一个，端口 `[10000,11999]`）

| Method | Path | 说明 |
|---|---|---|
| GET | `/status` | 凭证状态 |
| POST | `/refresh` | 强制重扫码 |
| POST | `/quit` | 优雅退出（**停进程一律走此路，绝不强杀**） |

### recv_daemon（每账号一个，端口 `[12000,13999]`）

| Method | Path | 说明 |
|---|---|---|
| GET | `/status` | 连接状态 |
| GET | `/conversations` | 会话列表 |
| GET | `/conversation` | 会话详情 |
| POST | `/send` | 发送私信 |
| POST | `/quit` | 优雅退出 |

---

## 六、报错代码接口

| Method | Path | 说明 |
|---|---|---|
| GET | `/api/errcodes` | 全量代码表，支持 `?domain=BCC`、`?q=漂移` |
| GET | `/api/errcodes/{code}` | 单码详情（含 `file:line`） |

当前共 **312 个代码 / 18 个域**，详见 [`报错代码体系.md`](报错代码体系.md)。
