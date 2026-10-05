# ADR-010：调试能力以 MCP `debug` 工具族暴露（scope 隔离 + 依赖序定位）

- **状态**：📝 设计（待实施，2026-09-25）
- **决策人**：LOX（2026-09-25 拍板：架构落点 **方案 A**「复用既有机制 + `scope` 隔离」；能力边界 **选项 B**「只读观测 + 受控实机触发」）
- **关联**：`backend/mcp/`（既有 MCP，22 工具）· `services/probe.py`（7 探针）· `errcode.py`/`errcode_data.py`（370 码 × 六段）· `工作记忆/03_采集链路总览.md`（链路 SSOT）· 台账 H-24 / H-25
- **前置基调**：用户 2026-09-25 裁定「风控机制解除是真的，但开发时**以保守策略为主**」——本 ADR 全部设计受此约束（见 §6）。

---

## 1. 背景与问题

**用户原话**：
> 「现有的 debug 模式效率确实太低下了，什么资料，什么情况都要自己查，直接给项目当中做一个 debug 专用的 MCP 吧，这样后续 debug 的时候可以用 MCP 主动告知，到底是哪个环节出了问题，而不是反复的去查资料」

### 1.1 现状（实测，非推断）

| 资产 | 实测事实 | 位置 |
|---|---|---|
| MCP 机制 | **已存在且可跑**：stdio `initialize` + `tools/list` 真实返回 **22 个工具** | `backend/mcp/`（7 模块，1214 行） |
| MCP 机制质量 | token 世代号轮换即时失效 / 审计**绝不落原始参数值** / 只 bind 127.0.0.1 / 写操作双闸 | `config.py` `audit.py` `server.py` `registry.py` |
| 工具面 | 22 个；其中 **debug / probe / trace 类 = 0** | `mcp/tools.py` |
| 能力探针 | **7 个**，四态契约（`healthy`/`degraded`/`failed`/`unknown`），**零网络零浏览器** | `services/probe.py:1130` |
| 错误码 | **370 个**，每个带 `design/contract/deviation/chain/root/verify` | `errcode_data.py` |
| 链路 SSOT | **C1~C5 五条业务链** + 9 层通用链路 | `工作记忆/03_采集链路总览.md` |
| 日志 | **316 文件**，可聚合错误码频次（实测 `BCC-070`×51 等） | `backend/logs/` |

### 1.2 三个互相掩盖的缺失（这才是「工具存在却用不上」的完整解释）

| # | 缺失 | 表现 | 实证 |
|---|---|---|---|
| 1 | **发现性** | 用户不知有此功能 | 前端 9 个设置 tab **无 MCP**；全前端 `grep mcp` **零引用** |
| 2 | **可达性** | 用户用不了 | Hermes `mcp_servers` 只有 `llm-wiki`+`github`；MCP 设计文档的客户端清单写的是「Codex / Claude Code / OpenClaw」，**不含 Hermes** |
| 3 | **默认关闭** | 即便知道也调不通 | `enabled=False`（默认），端口 39144 未监听 |

**三者互为掩护**：修任何一条都不足以让它被用上。

### 1.3 为什么必须新建「debug 工具族」，而不是要求人多查文档

`03_采集链路总览.md` 自陈：

> 「C4 失效会以 C1/C2/C3 静默失效的形式表现 —— **这正是过去最常见误判的来源**」

同理，WP 通道「代码在跑、零落库」潜伏 5 天无人知（台账 H-24），也是**缺一个"主动告知哪个环节断了"的机制**。

⇒ 人工排查的顺序**依赖经验**，而经验缺失正是「效率低下」的根因。把**依赖序定位机械化**，是本 ADR 的核心价值，也是调试六步闭环「Step 3 执行链追溯」的机械化落地。

---

## 2. 目标与非目标

### 目标
- G1：任一环节异常时，**一次调用**给出「哪个环节断了 + 证据 + 置信度 + 下一步」。
- G2：**上游优先** —— 按依赖序定位**首个**失败环节，而非报「终端现象」。
- G3：**保守策略** —— 默认零网络、工具面按需切分、凭证不出口。

### 非目标（明确排除，避免范围蔓延）
- ❌ 不修改 MCP 机制层（transport / auth / audit / token / registry 的两级闸门）—— **只复用**。
- ❌ 不做前端 MCP 设置页 —— 独立事项，延后（见 §8「延后项」）。
- ❌ **不新增任何主动查询平台**的路径（延续「昵称绝不主动批量查询」红线，USER.md）。
- ❌ 不替代既有 `POST /api/probe/run` 与探针巡检 —— 复用其实现，不平行造第二套判定。

---

## 3. 决策

### D1：复用既有 MCP 机制，新增 `scope` 维度（**不新建独立进程**）

`backend/mcp/` 的传输/鉴权/审计/票据机制完整且质量高，**不复制**（复制 1200 行 = 双份维护 + 双份漂移面）。

新增：
- `Tool.scopes: tuple[str, ...] = ("full",)` —— 工具归属的作用域。
- 既有 22 个工具 → `scopes=("full",)`（**默认行为逐字不变** ⇒ 零回归）。
- debug 工具族 → `scopes=("debug", "full")`。

### D2：新增 `--scope=debug` 专用入口（**工具面物理隔离**）

```
python -m backend.mcp serve --scope=debug      # stdio，仅暴露 debug 工具
python -m backend.mcp http  --scope=debug      # 本机 HTTP，仅暴露 debug 工具
```

- `describe(scope=...)` 按作用域过滤；`call(name, args, ticket, scope=...)` 校验工具在作用域内，否则拒（`MCP-004`）。
- **不带 `--scope` = 全量**（向后兼容，现状不变）。
- **选 A 的理由**：既有工具面含 **15 个主动查询平台**的工具（`search_user` / `user_info_batch` / `recommend_feed` …）。若让 AI 客户端连"全量入口"，模型就同时握有这些路径 —— 与「保守策略」直接冲突。scope 隔离使其**物理不可达**，而非靠约定。

### D3：debug 工具族（**6 只读 + 1 受控触发**）

| # | 工具 | 级 | 作用 |
|---|---|---|---|
| T1 | `debug_overview()` | READ | 一屏总览：版本+6 源齐平 / 内核 / 账号 / 各链 worst / 近 1h 高频码 |
| T2 | `debug_chain_status(account="")` | READ | 五链逐级状态（复用 7 探针），**按依赖序**返回 |
| T3 | **`debug_why(account, symptom="")`** | READ | ★核心：上游优先，返回**首个失败环节** + 证据 + 下一步 |
| T4 | `debug_log_digest(since_min=60, account="", code="", limit=50)` | READ | 日志错误码聚合（频次/首末时间/样本）+ 关联契约摘要 |
| T5 | `debug_explain(code)` | READ | 错误码六段（口径合并既有 `lookup_errcode`，保留旧名别名） |
| T6 | `debug_data_health(account="")` | READ | 落库质量（H-25 量化口径）+ 来源通道构成 |
| T7 | `debug_trigger_capture(account)` | **TRIGGER** | 【唯一有副作用】受控实机触发 `capture_all` |

### D4：依赖序定位算法（`debug_why` 的判定核心）

层级与依赖（源自 `03_采集链路总览.md` 五链 + 探针前置项）：

```
L0  kernel_availability     前置：内核不可用 ⇒ 全链必死
L1  credential_identity     C4 支撑链：C4 失效 ⇒ C1/C2/C3 静默失效（最常见误判源）
L2  conversation_capture    C1
L3  message_integrity       C1 数据质量
L4  send_delivery           C2
L5  live_danmaku            C3
L6  ai_lead_capture         AI 获客
```

算法：`for layer in [L0..L6]: state = probe(layer); if state != healthy: return first_bad=layer`
⇒ 返回**首个**非 healthy 环节，并附「上游已过」清单（`upstream_ok`），使结论**可复核**而非黑箱。

### D5：零网络默认 + 触发式实机验证（受控）

- T1~T6：**纯本地只读**（SQLite `mode=ro` + 本项目日志），**零网络、零浏览器**，可高频调用。
- T7：唯一例外，**复用既有路径** `POST /api/messages/{account}/refresh`（不新增 imapi 直发点），须过**两道闸门**（见 §6）。

### D6：环节枚举从 SSOT 派生，**禁止硬编码**

- 层级表从 `services/probe.py` 的 `REGISTRY` / `CAPABILITY_ORDER` / `PREP_CAPABILITIES` **派生**，不另抄一份。
- 判定阈值走既有配置（`app_config`），不在新工具内写死。
- 理由：避免又一处 **nomenclature drift**（Canonical Contract Law）。

### D7：Hermes 以 **stdio 直连**（跳过 HTTP / Bearer / 前端）

| 路径 | 传输 | 令牌管理 | 前端页 | 网络暴露面 |
|---|---|---|---|---|
| 蓝本原设计 | 本机 HTTP | 需 Bearer + 轮换 | 需 `settings-mcp.tsx` | 回环端口 |
| **Hermes 直连（选定）** | **stdio**（Hermes 拉起子进程） | **不需要** | **不需要** | **无** |

Hermes 的 `mcp_servers` 原生支持 stdio（`llm-wiki` 即 `command` + `args` 形态）。**一步到位且自动跳过三块复杂度与风险** —— 直接服务 G3（保守策略）。

### D8：订正既有 doc-rot（同一 PR）

`mcp/__init__.py:22` 与 `mcp/registry.py:18` 仍写「**本项目不注册任何主动查询昵称的工具**」，但同一目录 `mcp/tools.py:8` 记录 2026-09-14 用户已授权「全解除」，**实测注册 22 个**（含 `search_user`/`user_info_batch`）。⇒ 两处模块级契约**已过期**（Knowledge Freshness Law：作废规格不得与有效规格并存）。须改为「授权事实 + 保守基调」并述。

---

## 4. 共同返回契约（DbC）

所有 `debug_*` 工具 `data` **必须**含（缺任一项即视为契约违反）：

```json
{
  "state": "healthy|degraded|failed|unknown",
  "evidence": ["<硬证据：命令/表名/行数/日志行>"],
  "confidence": "A|B|C|D",
  "measured_at": "ISO8601",
  "reasons": ["<判定理由，可观测，不得静默>"]
}
```

**不变式（invariants）**
- **I1**：`state=healthy` ⇒ `evidence` **非空**（禁止无证据报健康）。
- **I2**：数据不足 ⇒ `state=unknown`，**不得冒充 healthy**（继承 `probe.py` 契约）。
- **I3**：任何返回值**不得含** cookie / token / `.env` 内容 / 会话正文原文（仅摘要/长度/哈希）。
- **I4**：`confidence` 语义固定 —— `A`=真实实例取证 / `B`=库+日志双源交叉 / `C`=单源本地 / `D`=推断（**D 不得单独支撑结论**）。

**Post-conditions**
- T3 `debug_why`：全 healthy ⇒ `first_bad=null` 且 `state=healthy`；否则 `first_bad` 必为**依赖序最靠前**的非 healthy 环节。
- T2 `debug_chain_status`：返回顺序**恒为** L0→L6（不得因字典序/字母序变化）。
- T4 `debug_log_digest`：日志目录缺失/为空 ⇒ 返回空结果 + `reasons`，**不抛异常**。

---

## 5. 风控与安全约束（受「保守策略」基调）

| # | 约束 | 落实 |
|---|---|---|
| S1 | **工具面最小暴露** | scope 切分；debug 入口**物理**拿不到 15 个主动查询工具 |
| S2 | **不新增主动请求路径** | T1~T6 零网络；T7 **只调用既有** `refresh` 路径 |
| S3 | **触发有副作用 ⇒ 双闸** | T7 复用 WRITE 两道闸（`allow_write_actions=true` + 一次性确认票据）；**不新造旁路** |
| S4 | **凭证永不出口** | 返回值脱敏（I3）；沿用 `audit.py`「绝不落原始参数值」 |
| S5 | **未知即拒绝** | `scope` 取值白名单；未知 scope ⇒ 启动失败并报错（**不静默回落全量** —— 否则隔离形同虚设） |
| S6 | **并发读写安全** | 库读一律 `mode=ro` 快照；不触碰运行中服务的状态 |

> **S5 是关键**：若未知 scope 静默回落「全量」，则"隔离"变成一句空话 —— 这是本 ADR 最容易实现错的一条。

---

## 6. 验收判据（全部可复跑）

| # | 判据 | 命令 / 方式 | 期望 |
|---|---|---|---|
| V1 | **scope 隔离真实生效** | stdio `--scope=debug` → `tools/list` | **恰 7 个**，且**不含** `search_user`/`user_info_batch`/`recommend_feed` |
| V2 | **未知 scope 拒绝启动** | `--scope=bogus` | 非零退出 + 明确报错（**不回落全量**，验 S5） |
| V3 | **依赖序定位正确** | 门禁构造：credential=failed / capture=failed | `debug_why` 返回 `first_bad=credential_identity`（**不是** capture）—— 验 D4/G2 |
| V4 | **数据不足不冒充健康** | 空库 / 新账号 | `state=unknown` + `evidence` 说明缺什么 —— 验 I2 |
| V5 | **脱敏** | 对含会话正文的库跑 T3/T6 | 返回值无正文原文、无 cookie/token 字样 |
| V6 | **全量回归不降** | `python -m unittest discover` | ≥ 853 tests OK，0 fail |
| V7 | **默认行为不变** | 不带 `--scope` → `tools/list` | 仍为 **22 个**（向后兼容，验 D1） |
| V8 | **版本 6 处齐平** | `scripts/check_version_sync.py` | 6 处一致 |
| V9 | **铁律门禁** | `scripts/check_iron_rules.py` | 6/6 PASS |
| V10 | **实机端到端** | Hermes 接入 `--scope=debug` → 对真实账号跑 `debug_overview` + `debug_why` | 返回真实环节结论；与 `POST /api/probe/run` 结果**一致** |

---

## 7. 后果

**正向**
- 调试从「人工按经验猜顺序」变为「**依赖序机械定位首个失败环节**」——止血于最上游。
- 用户**立刻可用**（stdio 直连，无需前端页、无需端口、无需令牌）。
- 被动捕获铁律与零风控边界**不被破坏**（T1~T6 零网络）。
- 顺带消除 3 处既有缺口：Hermes 接入 / doc-rot / 前端发现性（延后项）。

**负向 / 约束**
- 新增一门作用域语义（`scope`）⇒ 需门禁锁住「未知 scope 不回落」。
- 依赖序表与 `probe.py` 的 `REGISTRY` 存在耦合 ⇒ 探针增删时须同步复核（V3 门禁会抓）。
- T7 具备真实副作用（会触发一次真实链路）⇒ 必须始终在双闸之下，**默认关闭**。

**延后项（本 ADR 不做，登记备查）**
- 前端 MCP 设置页（给人用/给其他客户端用；Hermes 接入**不依赖它**）。
- 把 `debug_*` 结论接入前端「探针」页面展示（真机联动）。

---

## 8. 回退路径

1. **入口级**：`--scope=debug` 为新增参数，移除后 `python -m backend.mcp serve` 立即回到全量 22 工具（**机制层零改动 ⇒ 回退零影响**）。
2. **工具级**：从 `register_all()` 摘除 `debug_*` 注册即可；既有 22 工具与 scope 默认值 `("full",)` 不受影响。
3. **T7 专项**：`debug_trigger_capture` 天然处于 `allow_write_actions=false` 之下（默认），无需额外回退动作。

---

## 9. 实施顺序（本 ADR 批准后）

```
① registry.py 加 scope 维度（默认全量，零回归）
② tools_debug.py 新增 debug 工具族（6 READ + 1 TRIGGER）
③ __main__.py 加 --scope（含未知值拒绝）
④ test_debug_mcp.py 门禁（V1~V5）
⑤ 订正 doc-rot（D8）
⑥ 版本 v0.44.73 → v0.45.0（minor feature，6 处齐平）
⑦ Hermes config 接入 stdio --scope=debug（V10 实机）
⑧ 归档：本 ADR + 案例 + 台账
```
