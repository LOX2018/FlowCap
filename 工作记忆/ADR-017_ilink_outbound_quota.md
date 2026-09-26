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
