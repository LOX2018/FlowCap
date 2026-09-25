# ADR-013 · AI 回复安全提取与 LLM 参数显式化

> 日期：2026-09-26 · 版本：v0.45.7 · 状态：**已实施**（代码 + 门禁 + 数据迁移）
> 缺陷编号：AI-061（截断外发）· AI-062（窗口拍脑袋定值）· AI-063（extra 类型契约）
> 上游：ADR-008（AI 上下文与并发）· ADR-012（消息落库 Schema）

---

## 一、背景与决策摘要

| 项 | 决策 |
|---|---|
| D1 | **被截断的回复 = 失败**，走兜底话术；**绝不外发半截话** |
| D2 | `max_tokens` 默认 1000 → **4000**，并对推理模型按配置系数上浮（默认 1.5×） |
| D3 | `context_window` 改为**显式配置**（`0` = 未配置 → 保守下限 8192 + 告警），不再用 65536 兜底 |
| D4 | `extra` 统一经 `_coerce_extra()` 归一化（dict / str / bytes / None 均可，非法退空 dict） |

---

## 二、实测依据（真实模型 + 真实网关 31415）

```
deepseek-v4.1-flash（推理模型），复杂推理题：
  max_tokens=1000 → finish_reason=length  reasoning=365
  max_tokens=2000 → finish_reason=length  reasoning=1009
  max_tokens=4000 → finish_reason=length  reasoning≈1800+

agnes-2.5-flash（非推理）：
  max_tokens=1000 → 正常（content 750，finish=stop）
```

**结论**：固定 `max_tokens` 对推理模型**必然不够** —— reasoning 先吃掉额度。
而修复前**全仓零处处理 `finish_reason`**（`grep finish_reason` 无命中）
⇒ 半截话被直接发给客户。

⚠️ **方向性纠正**：台账 H-23 原记「max_tokens=1000 → 返回 None」是**当时模型**
的表现；本次在 `agnes-2.5-flash` 上实测 1000 **完全正常**。结论方向对
（推理模型会撑爆），但数字随模型变化 —— 不能拿旧记录当现状。

---

## 三、实施

### D1 · 统一安全提取出口 `_extract_reply()`（模块级，两协议共用）

判据（任一命中即判失败 → `None` → 走兜底）：

1. `finish_reason == "length"` （OpenAI 协议）
2. `stop_reason == "max_tokens"` （Anthropic 协议）
3. content 空（含 reasoning 占用情形，保留 reasoning 尾部兜底的历史行为）
4. 内容像思考过程（`_looks_like_reasoning`）

**为何要 SSOT**：此前两处协议各自内联判定，Anthropic 路径连思考泄漏检测都没有。

### D2 · `_eff_max_tokens()` + `_is_reasoner()`

`max_tokens` 默认 4000；模型名命中 `r1/reasoner/reasoning/think/deepseek-v4/glm-5/o1/o3`
→ × `reasoner_max_tokens_factor`（默认 1.5）。

### D3 · `context_window` 显式化

**实测前提**：`model_hub` 的 models 元数据**只有**
`id / provider_id / model / caps / source`，**不含窗口字段**
⇒ 系统无法自动获取真实窗口。

按用户的「显式配置原则」：**不拍脑袋定值**。
`0` = 未配置 → 保守下限 8192 + **一次告警**（猜大 = 超窗被 provider 拒；猜小 = 少喂上下文，安全方向）。

### D4 · `_coerce_extra()`（ADR-012 补）

`MessageRecord.build()` 原签名只收 `dict`，而 **DB 读路径天然给 JSON 字符串**
⇒ `dict('{"sender_sec_uid": ...}')` 抛 `ValueError`
⇒ **存量标注脚本中断，905 行只标了 545 行**（实测 360 行漏标）。

修：统一归一化，非法 JSON / 非容器 → 空 dict，**绝不抛异常**
（迁移脚本一次崩溃会让整库标注中断）。

**数据修复**：`scripts/migrate_message_kind.py --apply`
→ 补标 360 行（user_text 261 / system_notice 99），**残留 0**
（自动备份 `dyautodm.db.bak.h25k.20260926_032019`）。

---

## 四、门禁（防复发，含负控）

`backend/test_h31_ai_reply_safety.py`（9 项，G1~G9）：
G1 出口存在且模块级 / G2 截断必失败 / G3 Anthropic 同样处理 /
**G4 正常响应不得被误判（防过度拦截）** / G5 走 `_eff_max_tokens` /
G6 推理模型上浮生效 / G7 `context_window` 未配置走下限 /
G8 畸形响应不崩 / G9 思考泄漏检测未被破坏。

**负控实测**：把 `if finish == "length"` 改成 `if False and ...`
→ G2 报红（`AssertionError: '这是一段被截断的回复……' is not None`）；还原 → 9/9。

---

## 五、验收

- 全量 **911 passed / 9 failed**（与基线 h30 **逐条相同**，无新增失败）
- 铁律门禁 12 项 + R8 六项全 PASS
- 版本 6 处齐平 **0.45.7**

---

## 六、遗留（不阻断）

1. 历史行的 `text` 仍残留旧标签（如 `[未知类型15] {}`）—— 迁移只补 `kind`
   不重写 `text`。读侧按 `kind=system_notice` 排除，功能正常。
   若要彻底清理 `text`，属独立数据修复项。
2. `context_window` 当前为 0（未配置）⇒ 上下文预算按 8192 计算。
   需按 `agnes-2.5-flash` / `deepseek-v4.1-flash` 的真实窗口填写。
