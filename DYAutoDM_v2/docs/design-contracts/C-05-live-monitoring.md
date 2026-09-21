# 设计契约 · C-05 直播解析与监听（link_resolve · core/live_hook）

> 来源：`12_业务域_直播监听.md` + 本轮读源码（2026-09-21）+ `10_上游情报_签名与风控.md`。

## 1. 设计意图

① **解析**直播间 URL → `room_id`；② **监听**直播弹幕流，把公屏消息转为私信目标推给调度。

## 2. 设计契约（DbC）

| 类型 | 内容 |
|---|---|
| **前置条件** P | ① 输入为合法直播间 URL（含短链）；② 解析需带 cookie 跟随重定向（否则被风控页拦）；③ 监听需 `live_id` + 有效 `auth` |
| **后置条件** Q | ① 解析成功返回 `room_id`（且有缓存，相同 URL 命中缓存）；② 主引擎失败时可降级到备用路径（URL 片段 / 浏览器兜底），但**必须记录降级**；③ 弹幕以 `WebcastChatMessage` 的发送者直接推送，不经二次加工 |
| **不变式** I | Ⅰ1 **reflow 两步换发为主线**（借鉴 DouyinLiveRecorder），备用路径仅为降级；Ⅰ2 监听**只消费消息自带的 nickname**，绝不补全（见 C-01 Ⅰ3）；Ⅰ3 弹幕处理必须**线程安全**（旧版同步 websocket-client 的直接调用已改为 `submit`） |

## 3. 签名现状（R8 核对，2026-09-21）

| 项 | 现状 | 判据 |
|---|---|---|
| `reflow/info` | 仍用 **X-Bogus**（`link_resolve.py:117 _xbogus_sign`） | 上游情报：X-Bogus 已被 a_bogus 取代（web 侧）；但 webcast 直播通道是否同步换代**未确认** |
| 直播 WS `signature` | 沿用基座 `DouyinLive` 的 X-Bogus 系签法 | 工作正常即不动（**改前须先有实测证伪**） |

> **判定**：**暂不更换**。理由：① 无实证表明 webcast 通道已弃用 X-Bogus；
> ② 直播链路在线可用；③ 贸然换签属**无据改动**（违反「以事实为主」）。
> 待办：取一次 webcast 真实请求对照，确认上游是否已切 a_bogus 后再定。

## 4. 规范契约

| 字段 | 语义 |
|---|---|
| `room_id` | 直播间房间号（数字） |
| `sec_user_id` | 主播安全 uid（reflow 重定向中抽取） |
| `web_rid` | reflow 返回中的展示号（`data.room.owner.web_rid`） |
| `live_id` | 监听用直播 id |

## 5. NFR

| 指标 | 预算 |
|---|---|
| 解析超时 | `timeout=15s` |
| 心跳 | `start_heartbeat(interval=300)` |
| 快照保留 | `feed_snapshot(limit=120)` |

## 6. 验证方式

```bash
py314 -m unittest test_live            # 解析/换发单测
# 在线验证：给一个真实直播间 URL，确认返回 room_id（Live-Instance Verification）
```

## 7. 已知缺口

- `reflow/info` 签名版本待实测确认（见 §3）。
- 直播昵称解密的权限问题（ENG-018/019）见 `artifacts/交接卡_...直播昵称解密权_v0.44.22.md`。
