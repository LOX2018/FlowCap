# 设计契约 · C-03 凭证回写与校验（auto_dm/accounts · verify_account）

> 来源：`04_身份与会话语义.md`、`06_凭证与签名.md`、本轮实测读源码（2026-09-21）。
> 本契约承载**凭证有效性判据**——历史上「探活一致但不可写」事故的防线。

## 1. 设计意图

判定某账号凭证**当前是否可用**，并在失效时自愈（重捕），使守护进程只在凭证有效时启动。

## 2. 设计契约（DbC）

| 类型 | 内容 |
|---|---|
| **前置条件** P | ① `.env` 存在且可解析；② 校验不得依赖「守护进程是否启动」（引擎校验职责 = 凭证本身是否有效） |
| **后置条件** Q | ① 返回 `{ok, uid, wp:{level,label,detail}, dm:{level,label,detail}, auto_fix_triggered}`；② **dm 三态**（ok/fail/unknown）必须独立给出，**不得用 wp 结果冒充 dm**；③ `dm_loopback=False` 时**不新增网络请求**，只读写探针缓存态 |
| **不变式** I | Ⅰ1 **探活一致 ≠ 可写**：`dm` 判定必须来自**真实写**（`create_conversation` cmd 609，对自身 uid，不投递消息）；Ⅰ2 回写前 `uid` 必须命中历史 `conv_id`（门禁 1.5），否则拒写；Ⅰ3 守护进程状态只作 detail 补充信息，**不得**影响 wp 判定；Ⅰ4 仅 `dm_loopback=True`（用户主动校验 / 启动自检）时才触发 `auto_recapture`，带 5 分钟节流 |

## 3. 规范契约

| 字段 | 取值 | 语义 |
|---|---|---|
| `wp.level` | `ok` / `warn` / `fail` / `unknown` | **wp 引擎**：凭证能否还原完整签名四件套 |
| `dm.level` | `ok` / `fail` / `unknown` | **dm 引擎**：能否拉私信列表 **且** 写操作被接受 |
| `dm.detail` | str | 含守护进程状态等**补充信息**（非判定依据） |

**失效分型（必须区分）**：

| 型 | 现象 | 判据 |
|---|---|---|
| 陈旧失效 | 探活 uid ≠ 历史 `conv_id` | AUTH-050 |
| **只读态** | 探活一致、读可用，但 `create_conversation` 返 `unexepcted session length` | cmd609 服务端原文 |
| 守护未起 | 端口不通 | **不等于**凭证无效 |

## 4. NFR

| 指标 | 预算 |
|---|---|
| 30s 轮询开销 | **零新增网络请求**（只读缓存） |
| 强探活节流 | 自动重捕 ≥ 5 分钟间隔 |
| 校验耗时 | `timeout=8s` |

## 5. 验证方式

```bash
# 单测（含「只读态须被判为 fail 而非 ok」的反向用例）
py314 -m unittest test_config_isolation
# 契约守护：dm 判定不得引用 wp 结果
grep -n "dm.*=.*wp\|wp.*↓.*dm" backend/auto_dm/accounts.py   # 期望：0 逻辑耦合
```

## 6. 已知缺口

- 只读态的**服务端触发条件**未知（为何账号级进入只读）：需 A/B 双账号对照实测，属**遗留项**。
- ENG-020 三项待复验（见 `knowledge/cases/`）。
