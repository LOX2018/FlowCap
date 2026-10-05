# 设计契约 · C-07 直播交互（弹幕发送 / 私信模板 / WebSocket 推送）

> 关联 ADR：无独立 ADR；本域由审计项 **A-1/A-5**（假成功端点清查）驱动，
> 与 `docs/design-contracts/C-05-live-monitoring.md`（直播解析与监听）同属「直播」域，
> 但 C-05 只管**读**侧（解析/监听/线程安全），本契约管**写与推送**侧。
> 本文件是本域**四个**端点（`/danmaku` · `/like` · `/dm-template` · `/ws`）的**唯一语义契约**（SSOT）。
> 代码改动若与本文冲突，须先改本文。
> 来源：`backend/api/live.py` 实测（2026-09-28 N4 修复后；**2026-09-30** 增 `/like`
> 并补「写接口默认休眠 + 房间号归一化」两条，见 §2.1 P0/P7 与 §4）+
> `backend/main.py` 鉴权面（`_MEMBER_EXEMPT`）+ `backend/models/live.py` 协议模型。

## 1. 设计意图

本域承载直播间的三类**交互**动作，共同红线是**「不报假成功」**——端点必须如实反映
动作的真实结果，绝不在无依据时返回 `ok=True`：

- **发弹幕（`POST /danmaku`）**：调真实写接口 `DouyinAPI.sendMsgInRoom` 发送；凭证、
  房间号、内容任一不齐备即 fail-closed，**绝不**恒返回 `ok:True`。
- **点赞（`POST /like`）**：调真实写接口 `DouyinAPI.diggLiveRoom`；
  与发弹幕同契约、同「默认休眠」门（2026-09-30 新增）。
- **私信模板落盘（`POST /dm-template`）**：真写 `kv_store` `config` 键并**读回校验**；
  落盘失败或读回不一致一律 `ok=False`。
- **WebSocket 推送（`GET /ws`）**：把新弹幕/热度实时推给前端，替代 2s 轮询；
  该端点自验 token（在 `main.py` 的 `_MEMBER_EXEMPT` 白名单内），
  **当前实现为空转桩**（见 §7）。

三条共同约束：**失败一律 fail-closed**；**返回体须携带可判别的事实字段**
（`ok`/`sent`/`saved`/`reason`），使前端能区分「真成功」与「假成功」。

## 2. 设计契约（DbC）

### 2.1 前置条件（P）

| 编号 | 前置条件 | 违约后果 |
|---|---|---|
| **P0** | **写接口默认休眠**：`/danmaku` 要求 `live.danmaku_enabled=true`、`/like` 要求 `live.like_enabled=true`（**两者 default 均为 False**）；读配置失败亦按未启用处理 | 直接 `ok=False, sent=False`，`reason∈{danmaku_disabled, like_disabled}`，**零出站**（连房间信息都不请求） |
| P1 | `/danmaku` 的 `content` 去空白后非空 | 直接 `ok=False`，`reason=empty_content`，**不发请求** |
| P2 | `/danmaku` / `/like` 能取到 `account`（请求体显式给，或 `acct_core.current_name()` 兜底） | 取不到即 `ok=False`，`reason=no_account`（fail-closed，**不得**静默沿用空账号） |
| P3 | `/danmaku` / `/like` 能取到 `room_id`（请求体显式给，或 kv `config.live_id` 兜底） | 取不到即 `ok=False`，`reason=no_room_id` |
| P4 | `/danmaku` / `/like` 能加载账号凭证（`_auth_for(account)` 返回非 None） | 加载失败 `ok=False`，`reason∈{credential_unavailable, credential_empty}` |
| P5 | `/dm-template` 的 `set_kv_json("config", data)` 与随后 `get_kv_json("config", {})` 均可执行 | 抛异常即 `ok=False`，`reason=persist_failed` / `readback_failed` |
| P6 | `/ws` 连接在 `_MEMBER_EXEMPT` 白名单内（路径前缀 `/api/live/ws`）→ 免 HTTP 会员中间件 | 无（白名单由 `main.py` 维护，属**已知设计**，见 I5） |
| **P7** | **`/like` 的 `count` 为 1..`live.like_max`（默认 1000）的整数** | 越界/非整数即 `ok=False`，`reason=bad_count`，**零出站**（显式拒绝，**不静默夹取**） |
| **P8** | **`POST /resolve` 必须同时落库 `config.live_room_id`（真实 room_id）与 `config.live_id`（web_rid）** | 只落 web_rid ⇒ 写接口拿短号当 room_id 用（上游静默失败）；探测失败**不影响解析结果**（写接口侧仍有 `_live_chat_room_id` 兜底归一化） |

### 2.2 后置条件（Q）

| 编号 | 后置条件 | 可观测证据 |
|---|---|---|
| Q1 | `/danmaku` 真调 `DouyinAPI.sendMsgInRoom(auth, room_id, content)`；仅当上游 `status_code == 0` 才返回 `ok=True, sent=True` | 源码含 `DouyinAPI.sendMsgInRoom`；非 0 分支 `reason=upstream_failed` |
| Q2 | `/danmaku` / `/like` 任一路径失败（异常/上游非 0/凭证缺失/门未开）⇒ `ok=False` 且 `sent=False`，**绝不** `ok=True` | 各处返回字面量 `"sent": False`；`except` 分支 `reason=exception` |
| **Q2b** | `/like` 真调 `DouyinAPI.diggLiveRoom(auth, room_id, str(count))`；仅当上游 `status_code == 0` 才 `ok=True, sent=True` | 源码含 `DouyinAPI.diggLiveRoom`；`reason=upstream_failed` 分支在场 |
| **Q2c** | **写接口的房间号必须是真实 `room_id`**（非 URL 短号 `web_rid`）：两处写端点均经 `DouyinAPI._live_chat_room_id` 归一化；取不到真实值时打 `LIVE-038/039` 并**原样回退**（不得 fail-closed、不得编造） | 源码含 `_live_chat_room_id`；`reason=upstream_failed` 分支在场 |
| Q3 | `/dm-template` 真写：`data["dm_template"] = payload; set_kv_json("config", data)` | 源码含 `set_kv_json("config", data)` 与 `dm_template` |
| Q4 | `/dm-template` 落盘后**读回校验**：`get_kv_json("config", {})` 的 `dm_template.pool` 必须等于本次 `pool`，否则 `ok=False` | `reason=readback_mismatch` 分支在场 |
| Q5 | `/dm-template` 成功才返回 `ok=True, saved=True`；落盘/读回失败一律 `ok=False, saved=False` | `reason=persist_failed`；三处 `"saved": False` |
| Q6 | `/ws` 在 `accept()` 后于 `WebSocketDisconnect` 时静默收敛（不冒泡 500） | `except WebSocketDisconnect: pass` |

### 2.3 不变式（I）

| 编号 | 不变式 | 违反代价 |
|---|---|---|
| I1 | **fail-closed 铁律**：三端点在**任何**异常/缺参路径上都**不得**返回成功标记（`ok=True`/`sent=True`/`saved=True`） | 用户看到「已发送」但实际未发出 ⇒ **假成功**（本契约要消灭的核心缺陷） |
| I2 | 弹幕「成功」= **上游 `status_code == 0`**，非「本地函数没抛异常」 | 网络/风控失败被记成成功，误导运营 |
| I3 | 模板「成功」= **落盘 + 读回一致**，非「函数返回了」 | 模板从不持久化，重启即丢（原缺陷） |
| I4 | 模板写路径是 `kv_store` `config` 键的 `dm_template` 子对象；运行时同步 `settings.dm_pool` 属**尽力而为**，其失败**不**翻转落盘结论 | 运行时同步失败被误判为整体失败 |
| I5 | `/ws` 免 HTTP 会员中间件是**显式设计**（WS 自验 token），不是遗漏；但**空转推送 = 假成功**，属未实现（§7） | 前端连上却没有数据，误认为「监听正常」 |
| I6 | 三端点的返回体是**判别性事实**（含 `ok`/`reason`），前端凭此区分真成功/假成功 | 前端只能靠 HTTP 200 猜结果 |
| **I7** | **写接口（`/danmaku` / `/like`）默认休眠**：`live.danmaku_enabled` / `live.like_enabled` 均为 `False`，不配置就**不外发**。这是用户「显式配置原则」的落地：行为由用户显式选择，不由代码默认替用户决定 | 升级后行为**无声变化**（用户一点就真发），且触风控 |
| **I8** | **写接口的房间号是真实 `room_id`，不是 URL 短号 `web_rid`**：两套标识（`link_resolve` 产 web_rid；写接口要 room_id）不得混用。归一化取不到时**回退原值并留痕**（LIVE-038/039），不得编造 | 上游业务失败且无错误码（静默失败 = 用户看到的「发了没反应」） |

## 3. 规范契约（字段命名 · Canonical Contract Law）

`/danmaku` 返回体字段：

| 字段 | 类型 | 成功时 | 失败时 | 说明 | 禁止的别名 |
|---|---|---|---|---|---|
| `ok` | bool | `true` | `false` | 总成功标记 | `success` / `status` |
| `sent` | bool | `true` | `false` | 是否真的发出 | `delivered` / `done` |
| `reason` | string | 缺省 | 机器码 | 失败原因枚举 | `msg` / `err` |
| `content` | string | 原文 | 原文 | 回显 | `text` |
| `account` | string | 账号 | 账号（可得时） | 发送账号 | `acct` |
| `roomId` | string | 房间号 | 房间号（可得时） | 目标直播间 | `room_id` / `rid` |
| `statusCode` | int | `0` | 上游码 | 上游 `status_code` | `code` / `status_code` |

`reason` 枚举（fail-closed 分型，必须与实现一致）：

| 端点 | 枚举 |
|---|---|
| `/danmaku` | `danmaku_disabled` · `empty_content` · `no_account` · `no_room_id` · `credential_unavailable` · `credential_empty` · `exception` · `upstream_failed` |
| `/like` | `like_disabled` · `bad_count` · `no_account` · `no_room_id` · `credential_unavailable` · `credential_empty` · `exception` · `upstream_failed` |

`/like` 返回体字段（**与 `/danmaku` 刻意同构**，前端只需一套错误呈现逻辑）：

| 字段 | 类型 | 成功时 | 失败时 | 说明 | 禁止的别名 |
|---|---|---|---|---|---|
| `ok` | bool | `true` | `false` | 总成功标记 | `success` / `status` |
| `sent` | bool | `true` | `false` | 是否真的发出 | `delivered` / `done` |
| `reason` | string | 缺省 | 机器码 | 失败原因枚举 | `msg` / `err` |
| `count` | int | 实际次数 | 实际次数 | 回显本次请求次数 | `n` |
| `account` | string | 账号 | 账号（可得时） | 发送账号 | `acct` |
| `roomId` | string | 真实 room_id | 真实 room_id（可得时） | 目标直播间 | `room_id` / `rid` |
| `statusCode` | int | `0` | 上游码 | 上游 `status_code` | `code` / `status_code` |

`/dm-template` 返回体字段：

| 字段 | 类型 | 成功时 | 失败时 | 说明 | 禁止的别名 |
|---|---|---|---|---|---|
| `ok` | bool | `true` | `false` | 总成功标记 | `success` |
| `saved` | bool | `true` | `false` | 是否真落盘且读回一致 | `persisted` / `written` |
| `reason` | string | 缺省 | 机器码 | 失败原因枚举 | `msg` |
| `count` | int | `len(pool)` | 缺省 | 落盘词库条数 | `n` |
| `dmTemplate` | object | payload | 缺省 | 落盘原文回显 | `dm_template` |

`reason` 枚举：`persist_failed` · `readback_mismatch` · `readback_failed`。

落盘 payload 结构（`config.dm_template`）：`{pool: [str], delay_range: [int,int], interval: float, max_target: int}`。

## 4. 配置项规范（`kv_store` `config` 键）

| 键 / 路径 | 类型 | 默认 | 写入点 | 说明 |
|---|---|---|---|---|
| `config.dm_template.pool` | `list[str]` | `[]` | `POST /dm-template` | 私信模板词库 |
| `config.dm_template.delay_range` | `list[int,int]` | `[40, 65]` | `POST /dm-template` | 发送间隔抖动区间（秒） |
| `config.dm_template.interval` | `float` | `0.0` | `POST /dm-template` | 轮次间隔（秒） |
| `config.dm_template.max_target` | `int` | `0` | `POST /dm-template` | 单轮最大目标数 |
| `config.live_id` | `str` | `""` | `POST /resolve` | `/danmaku` `/like` 的 `room_id` 兜底来源。⚠️ **该值是 web_rid（URL 短号）**，写接口需真实 room_id ⇒ 必须经 `DouyinAPI._live_chat_room_id` 归一化（I8） |

> 本域**不新增** `kv_store` 键；但 **2026-09-30 起**本域消费两个统一配置中心字段
> （`app_config_schema` 的 `live` 分区）：`danmaku_enabled`（默认 `False`）与
> `like_enabled`（默认 `False`）—— 即 §2.1 的 P0 显式配置门；另有 `like_max`（默认 `1000`）
> 限定单次点赞上限。三者均为**写接口的安全门**，默认值即「休眠」。

## 5. 语义说明

- **「假成功」定义**：端点在外呼未发生 / 未校验通过的情况下返回 `ok=True`（或 `sent`/`saved` 为真）
  即构成假成功——本契约的 I1 即针对此。修复前 `/danmaku` 与 `/dm-template` 均为恒 `ok:True` 空壳。
- **鉴权面（策略）**：`/api/live/danmaku`、`/api/live/dm-template` 走 `main.py` 的
  `member_auth_middleware`（需 `X-Member-Token`）；`/api/live/ws` 在 `_MEMBER_EXEMPT`
  白名单内，由 WS 层自验 token。**不得**为图省事把 `/danmaku` / `/dm-template` 加入白名单。
- **返回体契约**：见 §3；`reason` 是机器可判的失败分型，前端凭 `ok` + `reason` 决定提示文案，
  **不得**仅凭 HTTP 200 判成功。
- **已知偏离的处置纪律**：本契约守护的符号/常量若被改名（如 `DouyinAPI.sendMsgInRoom` →
  别名、`persist_failed` → 其它字符串）即为契约漂移，**必须**报红。
  任何「把本门禁判据登记进 `docs/design-contracts/.known-gaps.json` 或
  `scripts/check_contracts.py` 的 `INLINE_KNOWN` 来换取通过」的做法，**属禁止的假豁免**——
  本域现有实现（N4 修复后）已满足全部判据，**无**既有缺口需要豁免。

## 6. 机械验证方式（可机械判定）

> 判据形式统一为 `grep -n "<符号>" <仓库相对路径>`；门禁 `scripts/check_contracts.py`
> 的 G15 **解析本节的 grep 行**并断言符号在场（词边界精确匹配，改名即失败）。
> **本域不引用单元测试模块**（避免与 G14 的单测可运行判据耦合）；如需行为级验证，
> 另立测试模块后再在本节登记。

```bash
# 契约守护：danmaku 真调 sendMsgInRoom（而非恒 ok:true 空壳）
grep -n "DouyinAPI.sendMsgInRoom" backend/api/live.py
# 期望：命中 1 处（真实写调用）；删除/改名 ⇒ 红

# 契约守护：danmaku 成功 = 上游 status_code == 0
grep -n "status_code" backend/api/live.py
# 期望：命中（ok = code == 0 判据）；缺此判据 ⇒ 恒 ok:true ⇒ 红

# 契约守护：dm-template 真落盘（写 kv config）
grep -n "set_kv_json" backend/api/live.py
# 期望：命中（`set_kv_json("config", data)`）；不写盘 ⇒ 红
grep -n "dm_template" backend/api/live.py
# 期望：命中（payload 落在 config.dm_template 下）

# 契约守护：dm-template 读回校验（防空转）
grep -n "get_kv_json" backend/api/live.py
# 期望：命中（落盘后读回比对 pool）

# 契约守护：fail-closed —— 失败一律不报成功（各失败分型在场）
grep -n "persist_failed\|readback_mismatch\|readback_failed" backend/api/live.py
# 期望：三处分型齐备；缺任一 ⇒ 失败路径可能静默成功 ⇒ 红
grep -n "empty_content\|no_account\|no_room_id\|credential_unavailable\|credential_empty\|upstream_failed" backend/api/live.py
# 期望：danmaku 各失败分型齐备；缺任一 ⇒ 红

# ── 2026-09-30 新增（写接口默认休眠 + 房间号归一化）────────────────────
# 契约守护：like 真调 diggLiveRoom（而非 push("功能开发中") 空壳）
grep -n "DouyinAPI.diggLiveRoom" backend/api/live.py
# 期望：命中 1 处（真实写调用）；删除/改名 ⇒ 红

# 契约守护：写接口的显式配置门（默认休眠，不配置不外发）
grep -n "danmaku_disabled\|like_disabled" backend/api/live.py
# 期望：两处拒发分型齐备；缺任一 ⇒ 写接口可能无条件外发 ⇒ 红
grep -n "danmaku_enabled\|like_enabled" backend/api/live.py
# 期望：两处配置键齐备（app_config 读取）；缺任一 ⇒ 门形同虚设 ⇒ 红

# 契约守护：写接口房间号归一化（web_rid → 真实 room_id）
grep -n "DouyinAPI._live_chat_room_id" backend/api/live.py
# 期望：命中（/danmaku 与 /like 各 1 处）；缺 ⇒ 拿 URL 短号当 room_id ⇒ 静默失败 ⇒ 红

# 契约守护：归一化入口本体在场（基座唯一实现）
grep -n "_live_chat_room_id" backend/dy_apis/client_live.py
# 期望：命中（定义 + 两处写接口调用）
grep -n "LIVE-038\|LIVE-039" backend/dy_apis/client_live.py
# 期望：命中（探测失败/取不到真实值各一处留痕）；静默回退 ⇒ 红

# 契约守护：点赞次数越界显式拒绝（不静默夹取）
grep -n "bad_count" backend/api/live.py
# 期望：命中（count 越界分型）；缺 ⇒ 越界被静默接受 ⇒红

# ── 2026-09-30 第二轮：权威字段 + 风控可读化 ────────────────────────
# 契约守护：resolve 必须落库 live_room_id（真实 room_id）
grep -n "live_room_id" backend/api/live.py
# 期望：命中（resolve 落库 + _room_id_for 读取）；缺 ⇒ 写接口拿短号 ⇒ 红

# 契约守护：风控形态必须翻成可读异常（不得静默降级为 {}）
grep -n "check_risk_response" backend/dy_apis/client_live.py
# 期望：命中（定义 + 两处写接口接线）；缺 ⇒ 风控不可归因 ⇒ 红

# 契约守护：写接口 referer 与 room 参数同形（不得混用 web_rid/room_id）
grep -n "同形" backend/dy_apis/client_live.py
# 期望：命中（两处注释说明）；缺 ⇒ 同一请求内两处身份矛盾 ⇒ 红
```

## 7. 已知缺口（诚实记录）

- **`/ws` 空转**：`backend/api/live.py` 的 `live_ws` 当前为桩——`accept()` 后仅
  `msg = await ws.receive_text()` 循环，标注 `# TODO: 从 LiveChatHook 订阅消息推送`，
  **不推送任何真实弹幕/热度**。即「连接成功但无数据」，属**未实现的假成功**。
  本契约将其**显式登记**为缺口（I5）；门禁 G15 以符号在场判据守护**已实现的**写/落盘路径，
  不把 `/ws` 桩伪装成已实现。补齐推送后须同步更新 §6 判据。
- **`/dm-template` 读路径未接**：本端点只保证「写进去且能读回原文」；前端模板**回读**
  仍走 `GET /api/tasks/current` 的 `dmPool`（源为 `adm.dm_template` / `settings.dm_pool`），
  **不读**本端点写的 `config.dm_template` 键。读路径打通属后续批次（见 `live.py` docstring）。
- **弹幕真机投递未验证**：`sendMsgInRoom` 的真实投递（服务端是否真的落弹幕）未做真机
  端到端验证；本契约只保证「真调写接口 + 按上游 `status_code` 判成败」。真实投递证据属后续。
  **2026-09-30 补充**：`/like` 的真实投递同样未验（同为写接口 / 触风控，不在本批真发）。
- **写接口默认休眠（2026-09-30 新增，非缺陷而是设计）**：`live.danmaku_enabled` /
  `live.like_enabled` 默认 `False`。因此**默认态下**调用 `/danmaku` / `/like` 会得到
  `reason=danmaku_disabled` / `like_disabled` —— 这是**按设计的默认态**，不是故障。
  排查「发了没反应」时先看这条，再怀疑链路。
- **`diggLiveRoom` 的上游形态偏差（已按直播域统一，未与上游同形）**：上游
  `cv-cat/DouYin_Spider`（截至 head `b17b12ee`，2026-09-27）的 `diggLiveRoom` 仍用
  **主站 Origin** 且**不传** `with_a_bogus` 的 host、**无** `with_bd`。本项目按其自身
  `sendMsgInRoom` 的明文结论（「直播域写接口的 Origin 与 bd-ticket 证书必须按
  `live.douyin.com` 生成，沿用主站 Origin 会得到空响应或业务失败」）把两者统一到直播域形态。
  属**有意偏离上游、对齐上游自述约束**；若上游后续修正该接口，须回头 diff 一次。
- **WS 鉴权细节**：`/api/live/ws` 在 `_MEMBER_EXEMPT` 内、宣称「自验 token」，但
  `live_ws` 当前**未实现**任何 token 校验逻辑——待 §7 第一条补齐推送时一并补验。
