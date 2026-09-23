# P1-1 修复报告：AI 会话回复上下文注入失效（`_build_history`）

- 任务：修复 AI 会话回复上下文注入失效（"只看最后一条消息 / 完全不像读过聊天记录"）
- 日期：2026-09-23
- 分支：design/better-douyin（当前 v0.44.55）
- **唯一改动源码文件**：`backend/services/ai_reply.py`
- 新增脚本：`backend/scripts/verify_history_context_fix.py`（可复跑验收）
- 版本源（package.json / tauri.conf.json / Cargo.toml / _build_version.py）**一律未改**，版本递增留给父会话。
- git **全程只读**（仅 `git show` / `git status` / `git diff`），无任何 add/commit/checkout/stash/clean。

---

## 1. 改动位置与修法

### 1.1 `backend/services/ai_reply.py:178-197` —— 新增白名单常量 + 语义注释（注释段 178-196，常量 197）

```python
# 权威映射来源：backend/api/messages.py 的 _front_type（约 156-166 行，**只读参考**）：
#     "7"     -> text      （抖音 WS 实时路径落库的文本消息）
#     "5"     -> sticker
#     "17"    -> voice
#     "27"    -> image
#     "8"     -> video
#     "50001" -> read_receipt
_HISTORY_TEXT_TYPES: tuple = ("text", "7")
```

映射语义抄自 **`backend/api/messages.py` 的 `_front_type` 函数（第 156-166 行）**，该文件按约束**只读未改**。实库分布（779 条 `dm_messages`）也一并写进注释作为订书钉。

### 1.2 `backend/services/ai_reply.py:1028-1032` —— `HISTORY_LIMIT` 降级为兜底值（附注释）

```python
POLL_INTERVAL = 5.0
# 上下文条数的**兜底**上限：仅当配置 max_history 缺失/非法时生效。
# 正式取值走 _history_limit() —— 读配置 max_history
HISTORY_LIMIT = 6
```

常量本身**没有删除**（保持向后兼容，任何外部引用不破），只把它从"实际取值"降为"兜底值"。

### 1.3 `backend/services/ai_reply.py:1344-1361` —— 新增 `_history_limit()`（读配置项 `max_history`）

最小侵入接法：新增 **classmethod**，不动 `_generate_reply` 的外部契约。

- `cfg` 为 `None` 时自行 `get_config()` —— 因此**旧调用签名 `_build_history(account, conv_id, before_id)` 依然可用**，行为只是"取默认 10 而非写死 6"（这正是修复悬空契约的目的）。
- 非法值（None / 非数字）回落 `HISTORY_LIMIT=6`，并 `max(1, min(n, 50))` 夹逼，防误配把整段会话灌进 prompt。

### 1.4 `backend/services/ai_reply.py:1363-1392` —— `_build_history` 本体（核心修复）

```python
def _build_history(self, account: str, conv_id: str, before_id: int,
                   cfg: dict | None = None) -> list:
    limit = self._history_limit(cfg)
    ph = ",".join(["?"] * len(_HISTORY_TEXT_TYPES))
    sql = (
        "SELECT role, text FROM dm_messages "
        "WHERE account=? AND conv_id=? AND id<? "
        f"  AND msg_type IN ({ph}) "          # ← 原为 msg_type='text'
        "  AND TRIM(COALESCE(text,''))<>'' "
        "ORDER BY id DESC LIMIT ?"
    )
    rows = database.get_db().execute(
        sql, (account, conv_id, before_id, *_HISTORY_TEXT_TYPES, limit),
    ).fetchall()
```

- SQL 由 `msg_type='text'` → **`msg_type IN ('text','7')`（参数化占位符，白名单）**。
- `?` 占位符由 `_HISTORY_TEXT_TYPES` 长度生成，加白名单项不用改 SQL 字符串。
- **返回结构完全不变**：`list[{"role","content"}]`，`role: me→assistant / them→user`，`reversed(rows)` 保证 id 升序。
- 签名新增的是**带默认值的末尾参数 `cfg=None`**，既有调用方零改动、零破签名。

### 1.5 `backend/services/ai_reply.py:1261` —— 调用方传入 cfg

```python
history = self._build_history(account, conv_id, before_id, cfg)
```

`_generate_reply` 的签名、返回行为**完全未变**（仍 `(Optional[str], str)`）；仅把已持有的 `cfg` 透传下去，使 `max_history` 真正生效。

---

## 2. 修复前证据读数（RED）

### 2.1 隔离库 msg_type 分布（实库，只读查询）

库：`C:\temp\dyautodm_design\members\m17db0f8209156f26\data\dyautodm.db`，`dm_messages` 共 **779 条**。

| msg_type | 条数 | 是否进上下文（修复前 / 修复后） |
|---|---|---|
| `text` | 687 | ✅ / ✅ |
| `'7'`（WS 实时文本） | 88 | ❌ / ✅ |
| `'1'` | 2 | ❌ / ❌（语义未确认，白名单排除） |
| `'50010'` | 1 | ❌ / ❌（语义未确认，白名单排除） |
| `'27'`（image） | 1 | ❌ / ❌ |

SQL 层面围栏对比：

```
old-eligible (msg_type='text' 且有文本)        = 687
new-eligible (msg_type IN ('text','7') 且有文本) = 775
```

### 2.2 真实库上 `history` 恒为 0 的硬证据

取"最近历史**全部**为 `msg_type='7'`"的会话（共 2 个）—— 这类会话修复前 **100% 不可注入上下文**：

```
conv=0:1:2962541468189539:388750622721042…  before_id=10440  (可取历史 1 条，全 '7')
   修复前 0 条 []
   修复后 1 条 ['你好，我是唐律助理，可以留个联系~方式，唐律下播']
conv=0:1:97039631021:3887506227210423  before_id=10535  (可取历史 1 条，全 '7')
   修复前 0 条 []
   修复后 1 条 ['工友你好']

=== 真实库硬证据合计：修复前 0 条 / 修复后 2 条 ===
```

参考：另两个"最新消息为 `msg_type='7`"会话的混合场景下，修复前 6 条 → 修复后 10 条（受 `HISTORY_LIMIT=6` → `max_history=10` 影响），合计 12 → 20 条。

### 2.3 验收脚本 A 段（构造纯 WS 历史）

```
会话A(纯 WS '7') 6 条，当前消息 id=6
  修复前返回 0 条 → []
  [PASS] A1 修复前纯WS会话返回 0 条（复现缺陷 RED）  — 实测 0 条
  会话B(混合) 修复前返回 1 条 → ['补拉的历史文本']（只捞到 'text'，'7' 全丢）
```

> ⚠️ 诚实标注：最初我把数据设计成混合 msg_type，**"修复前"读数落到 1 而非 0**（那 1 条来自 `msg_type='text'` 的补拉消息）。这不影响根因成立，但**不足以构成 RED**。已改为把"纯 WS 会话"作为主判据：真实触发 AI 回复的最新 WS 消息所在会话，其历史在纯 WS 期内全部是 `'7'` ⇒ 修复前**确为 0 条**。

---

## 3. 修复后证据读数（GREEN）

`python backend/scripts/verify_history_context_fix.py` 完整输出（exit=0）：

```
[setup] 会话A(纯WS,'7') 6 条，当前消息 id=6
[setup] 会话B(混合) 11 条，当前消息 id=17（7=4,text=3,27/1/50010/5/8 各1，空文本2）

--- A. 修复前（git HEAD 旧实现：硬筛 msg_type='text'）---
  会话A(纯 WS '7') 修复前返回 0 条 → []
  [PASS] A1 修复前纯WS会话返回 0 条（复现缺陷 RED）  — 实测 0 条
  会话B(混合) 修复前返回 1 条 → ['补拉的历史文本']

--- B. 修复后（当前工作区：白名单 text|7）---
  会话A(纯 WS '7') 修复后返回 5 条：
      user      | 你好，这个多少钱
      assistant | 亲，这款 199 哦
      user      | 能便宜点吗
      assistant | 已经是活动价啦
      user      | 那我要一个
  [PASS] B1 修复后纯WS会话返回 >0 条（GREEN）  — 实测 5 条
  [PASS] B2 内容按 id 升序、排除当前消息
  [PASS] B3 role 映射 them→user / me→assistant
  [PASS] B4 返回结构 list[{role,content}] 不变
  会话B(混合) 修复后返回 3 条 → ['补拉的历史文本', 'WS 实时文本', '客服回复文本']
  [PASS] B5 白名单只收 text|7，排除 27/1/50010/5/8 与空文本
  [PASS] B6 max_history=2 生效（不再写死 HISTORY_LIMIT=6）  — 2 条
  [PASS] B7 _HISTORY_TEXT_TYPES == ('text','7')

修复前读数 before_count (纯WS会话) = 0
修复后读数 after_count  (纯WS会话) = 5
PASS=8  FAIL=0  SKIP=0
RESULT: PASS — RED(0) → GREEN(5) 成立
```

脚本设计要点（可复跑）：

- 数据落在 **`C:\temp\dyautodm_design\_tmp_verify_history\verify_history.db`**（隔离环境内），脚本首部有"数据根不得落在源码树内"的守卫，并有 `SystemExit` 拒绝分支。
- 「修复前实现」**不是手抄**，而是 `git show HEAD:./backend/services/ai_reply.py`（纯只读）抽出旧 `def _build_history` 源码后 `exec` —— 引用不会被我"改后即失真"。
- **任何 SKIP 都计入 FAIL**（有跳过即 exit≠0），不把空转当通过。

---

## 4. 语法检查结果

```
$ python -c "import ast,py_compile; s=open('backend/services/ai_reply.py','rb').read(); ast.parse(s.decode('utf-8')); py_compile.compile(p,doraise=True)"
ast.parse OK
py_compile OK

$ python -m py_compile backend/services/ai_reply.py backend/scripts/verify_history_context_fix.py
COMPILE_OK
```

AST 提取 `AutoReplyWorker` 方法表（证明未破结构）：

```
__init__ 1034 / start 1048 / stop 1058 / _run 1063 / _tick 1077 / _handle 1126
_generate_reply 1219 / _describe_image 1288 / _history_limit 1345
_build_history 1363 / _send_delayed 1394 / _send_via_chain 1411 / _mark_recent 1473
```

注：修改全程用 **Python 字节级替换**（`read_bytes()` → `replace()` → `write_bytes()`），未使用 patch 工具做多行嵌套替换；每步替换后即跑 `ast.parse` + `py_compile`。

工作区状态（`git status --porcelain`）：

```
 M DYAutoDM_v2/backend/services/ai_reply.py          ← 唯一改动源码
?? DYAutoDM_v2/backend/scripts/verify_history_context_fix.py
?? DYAutoDM_v2/.hermes/plans/2026-09-23_ai-reply-intelligence-rework.md  ← 非本任务产出
```

---

## 5. 诚实标注：哪些是代码事实，哪些是推断

### ✅ 代码 / 实机事实（有读数支撑）

1. 旧 SQL 硬筛 `msg_type='text'`（git HEAD `backend/services/ai_reply.py:1323`，读出原文）。
2. WS 实时路径落库 msg_type 为 `'7'`：实库分布 779 条中 `'7'=88`，且 line 19-22 的模块注释已自证 `'27'` 为图片。
3. 隔离会员库 msg_type 分布、两库计数 687 / 775，均为只读 SQL 实测。
4. 真实库 2 个"历史全为 `'7'`"会话，修复前 `_build_history` 返回 **0 条**、修复后返回 **1 条**，为脚本实跑读数。
5. 脚本 RED→GREEN：纯 WS 会话 0 → 5，PASS=8 FAIL=0 SKIP=0，exit=0。
6. `max_history: 10` 在改动前**无任何代码读取**（全仓搜索仅命中 `_DEFAULT_CONFIG` 定义 + 第 703 行 `AIClient` 内部一处**同名不同源**用法，与 worker 的 `_build_history` 无关）——这是本次搜索得到的事实。
7. `ast.parse` / `py_compile` 均通过。

### ⚠️ 推断（未经实机验证，需后续确认）

1. **本次 fix 是否真能让端到端 AI 回复"显得读过聊天记录"** —— 只验证到 `_build_history` 返回值正确。`history_extra` 怎么被 `AIClient.chat_failover` 拼进请求（第 703 行那段 `history[-cfg["max_history"]:]` 会**再截一次**，可能与 `_history_limit` 叠加两次截断）未做端到端验证，**AI 服务当前 `enabled=False`，未实跑过一次真实 AI 回复**。
2. `'1'` 与 `'50010'` 的语义 —— **未确认**。按约束采用白名单保守处理；若后续确认 `'1'` 也是文本，把它加入 `_HISTORY_TEXT_TYPES` 即可（SQL 占位符自动适配）。
3. `max(1, min(n, 50))` 的 50 上界是我自定的防御值，非需求。
4. `HISTORY_LIMIT=6` 保留为兜底 —— 我未找到它在本仓其它位置的引用，因此"保留可避免破坏外部引用"是保守推断（实测：全仓除本次改动处外无其它引用，见 `search_files` 结果仅有定义行与 `_build_history` 内使用）。
5. 总资产 changed line 数 = 新增约 59 行 + 修改 2 行作用域内，`_generate_reply` 行为我认为未变，但**未跑回归测试**（项目无针对 worker 的单元aily 测试）。

---

## 6. 后续建议（不在本次改动范围）

- P2：第 703 行 `AIClient` 内 `history[-cfg["max_history"]:]` 与 `_history_limit` 双重截断，可能让 `max_history` 生效两次；建议统一由 `_build_history` 负责截断。
- P2：确认 `'1'` / `'50010'` 语义后再决定白名单是否扩容。
- 端到端：`enabled=True` 下一个 WS 会话实跑一轮，验证 AI 回复文案是否真的引用了历史内容。
