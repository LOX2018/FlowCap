# ADR-017 · iLink 微信 BOT 外发额度记账 + 扫码登录单条图片推送

- 状态：已采纳（2026-09-26）
- 作者：LOX + Hermes
- 关联：L-13（额度记账）、L-14（outbound_quota 回归闸门）、L-15（单条图片推送）
- 代码：`backend/notify/channels.py`、`backend/notify/notifier.py`、`backend/auto_dm/login_remote.py`
- 验证：`verify_l15_mock.py`、`verify_l13_mock.py`（mock 端到端，均通过）；真机未跑（缺配置）

---

## 1. 背景 / 决策驱动力

iLink（腾讯官方个人微信 Bot API）实测存在**外发额度上限**：同一 `context_token`
连续推送第 11 条必 `ret=-2 "prepare failed"`，且**不会随时间恢复**，只能靠对方
再发一条入站消息（新 ctx）刷新。官方 `wechatbot.dev/zh/protocol` 明确「回复能力
窗口约 24h、每 10 条外发需一次新入站」，由产品层 `contextWindow.ts` 显式记账，
**无刷新接口**。

此前实现（T-14~T-18 阶段）存在的三类问题：
- 无额度记账 → 第 11 条盲推后静默失败，体验割裂；
- `outbound_quota` 默认值曾被注入为 `-14`（负数 → 额度永远耗尽 → 任何主动推送
  都被 preflight 拦死）—— 需回归闸门；
- 扫码登录二维码推送 = 文本 + 图片 = 2 条，吃掉 1/5 额度，且文本失败会割裂。

---

## 2. 决策

| 项 | 决策 | 理由 |
|---|---|---|
| 额度模型 | `_sent[uid]` 仅对 sendmessage 计数，入站 → 归零 | 匹配官方"每 10 条外发需新入站"；实测 1~10 成功 / 11 失败 |
| 前置拦截 | `preflight()` 查 ctx + 额度，拦在 `send()`/`send_image()` 开头 | 把"必然失败"提前为明确提示，避免盲推 + 无谓重试 |
| 单条图片 | 扫码登录 `emit` 用 `body=""` + `image_caption`，notifier 走单条图片分支 | 省 1 条额度；text+image 混排会被服务端判 `ret=-2 invalid arguments` |
| 回归闸门 | `outbound_quota <= 0` 兜底回 10 | 防负数导致主动推送全灭 |

---

## 3. 后果

- ✅ 额度用尽时给出明确可操作提示（"请对方给 Bot 发一条消息"）而非静默失败。
- ✅ 扫码登录推送从 2 条降到 1 条，额度利用率翻倍。
- ✅ `-14` 类回归被硬闸门拦截。
- ⚠️ 仍依赖"对方先发消息"才能回推（iLink 协议级约束，非缺陷）。
- ⚠️ 真机验证待补：需 `data/notify_config.json` 配置 enabled 微信渠道 + 目标先发消息取 ctx。

---

## 4. 替代方案（已否决）

- 把说明烘焙进二维码 PNG（最省额度但需改图生成逻辑，且与"账号名"动态耦合，放弃）。
- 接受 text+image 混排（实测被 `ret=-2 invalid arguments` 拒，不可行）。
- 不记账、靠服务端 ret=-2 反馈（静默失败，体验差，否决）。

---

## 5. L-14 · iLink `ret=-14` 会话过期完整恢复（2026-09-26 落地，v0.45.25）

**问题**：iLink 长轮询 `getupdates` 在会话过期时返回 `ret=-14`（官方 `wechatbot.dev/zh/protocol`
「消息收发循环」明确定义；AstrBot PR #8196 / issue #6901 同口径）。原实现只 `sleep(5); continue`，
等于**对过期 token 持续打流量 + 永不恢复**（违反 issue #6901「不应空轮询」）。

**决策**：在 `_run_ilink` 内把轮询重构为双层循环（外层 token 生命周期 / 内层 getupdates），
并新增两个独立可测函数：

| 项 | 决策 | 理由 |
|---|---|---|
| 判定 | `_is_session_expired()` 只认 `ret==-14`（int/-14 str），**区别于 L-13 的 `ret=-2`** | -14 是会话级失效须重登；-2 是额度/参数错误可普通重试，误判会误清登录态 |
| 检测点 | 内层循环首轮响应 `ret==-14` → 调 `_handle_session_expired()` 后 `break` 外层 | 不再用过期 token 续轮询 |
| ①通知 | **先**发 critical（此时 bot_token 仍完好，绕过 L-13 preflight 的 ctx 拦截） | 后清 token 则通知自身被拦截（假成功族教训） |
| ②持久化 | `save_config_file` 清空 `token`/`sync_buf`/`context_tokens`/`context_sent_counts` | 重启仍清空，不自动续轮询过期会话 |
| ③重置 | 调用方置 `token=""` `sync_buf=""` 后 break | 内存态同步清空，避免残留引用 |
| ④状态标记 | KV `notify.ilink.session_expired`（`need_rescan=True`） | 设置页展示「需重新扫码」 |
| ⑥自动重推 | break → 外层「无 token → `_ilink_qr_login` 重推二维码」 | 契合 H-30 远程登录；满足 issue #6901「不应 crash」 |

**验证**：`artifacts/verify_l14_session_expired.py`（13 PASS/0 FAIL，临时根隔离）——
① `_is_session_expired` 对 -14 真 / 对 -2·无ret·其它ret 假；② 注入 -14 后 config 落盘
token/sync_buf/ctx 已清空；③ 注入 -14 后 poll_calls==1（不再续轮询）且自动重推二维码拿到新 token。

**完成标准达成**：✅ 停止续轮询 ✅ 持久化清空 ✅ 用户侧 critical 通知 ✅ 扫码后自动恢复。
