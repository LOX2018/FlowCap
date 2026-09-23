# 批次记录：ID 生成碰撞 + P2 直播配置/判据失真（P0-2 + P2-4/5/7/8/9/10 + P4）

- 仓库：`C:\Users\LOX\Desktop\DYchajian`，分支 `design/better-douyin`，基线 HEAD `b455192`（v0.44.53）
- 日期：2026-09-23
- 解释器：`%LOCALAPPDATA%\Programs\Python\Python314\python.exe`（3.14.6，项目主解释器）
- 隔离库：`DY_APP_ROOT=%LOCALAPPDATA%\Temp\fixA`（自建；**未触碰** `backend/data`）
- 复现脚本（修复前/后同一份）：`%LOCALAPPDATA%\Temp\fixA\reproA.py`
  - 修复前输出：`%LOCALAPPDATA%\Temp\fixA\reproA_BEFORE.txt`
  - 修复后输出：`%LOCALAPPDATA%\Temp\fixA\reproA_AFTER.txt`
- 统一跑法：

```
cd C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend
export DY_APP_ROOT="$LOCALAPPDATA/Temp/fixA"
"$LOCALAPPDATA/Programs/Python/Python314/python.exe" -m unittest test_live_rooms test_id_uniqueness test_p2_live_guards -v
```

> 注：`reproA.py` 只观测/打印、不 assert，故能在**修复前后各跑一次**并直接对比。起点会删掉自己隔离库的 `data/`，绝不碰 `backend/data`。

---

## P0-2a 房间/策略 ID 用 epoch 毫秒 → 同毫秒静默覆盖

**① 位置**
- `backend/api/live_rooms.py:78` `new_room_id()`（旧：`f"lr_{int(time.time()*1000)}"`）
- `backend/api/live_rooms.py:200`（旧）迁移内联 `sid = f"lc_{int(time.time()*1000)}"`
- 保存路径 upsert：`data[key] = upd`（旧 `live_rooms.py:303`），响应恒 `{"ok": True}`

**② 判定：真（已实测复现，两处）**

**③ 修法**
- `live_rooms.py`：新增模块级 `_id_lock` + `_last_id_ms` 与 `_next_id_ms()`（单调递增 `max(now_ms, last+1)`）；`new_room_id(existing=None)` 改为对**目标 kv 现有键**查重（跨进程兜底）。形态仍是 `lr_<epoch_ms>`，**老数据零迁移**。
- `live_rooms.py` 迁移段：承接策略 id 改走 `live_config.new_strategy_id(cfgs)`（同样带查重），房间键改 `new_room_id(rooms)`。
- `live_config.py:127` `new_strategy_id(existing=None)` 同样加锁单调 + 查重。

**④ 验证命令 + 实测输出**

```
export DY_APP_ROOT="$LOCALAPPDATA/Temp/fixA"
"$LOCALAPPDATA/Programs/Python/Python314/python.exe" "$DY_APP_ROOT/reproA.py"   # 冻结 time.time 到 1790160000.123
```

修复前（`reproA_BEFORE.txt`）：
```
id1 = lr_1790160000123 ok1 = True
id2 = lr_1790160000123 ok2 = True
两个 id 相同? -> True
kv 里房间条数 = 1 （期望 2；=1 即「房A 被静默覆盖」）
list_rooms 条数 = 1
旧公式裸算两个 id: lr_1790160000123 lr_1790160000123

found = 5
响应 migrated_rooms 条数 = 5 ['lr_1790160000123', 'lr_1790160000123', 'lr_1790160000123', 'lr_1790160000123', 'lr_1790160000123']
实际落库房间条数 = 1 （<5 即静默覆盖；响应条数与实际不一致即「谎报条数」）
id 去重后 = 1
```

修复后（`reproA_AFTER.txt`）：
```
id1 = lr_1790160000123 ok1 = True
id2 = lr_1790160000124 ok2 = True
两个 id 相同? -> False
kv 里房间条数 = 2 （期望 2；=1 即「房A 被静默覆盖」）
list_rooms 条数 = 2

found = 5
响应 migrated_rooms 条数 = 5 ['lr_1790160000125', 'lr_1790160000126', 'lr_1790160000127', 'lr_1790160000128', 'lr_1790160000129']
实际落库房间条数 = 5 （<5 即静默覆盖；响应条数与实际不一致即「谎报条数」）
id 去重后 = 5
```

回归测试：`test_id_uniqueness.TestRoomIdUniqueness`、`TestStrategyIdUniqueness`（含负控 `TestNegativeControls.test_legacy_room_id_formula_collides` 内联旧公式断言必碰撞）。

**⑤ 诚实标注**
- 跨进程查重是**对目标 dict 快照**查重（`new_room_id(existing)`），非数据库级原子；两个 sidecar 在同一毫秒对**同一旧快照**取号仍可能撞。真实写入路径是「读-改-写」整个 kv，撞键时后写者覆盖前者 —— 与修复前同风险，但**同一进程内的连续取号（多账号/batch/迁移）已彻底消除**，这正是实测事故形态。未做 DB 级唯一约束（kv 无此能力），故此项标注为**部分保证**。

---

## P0-2b `tasks_history.tid` = 主键 epoch 毫秒 → UNIQUE 冲突被吞成 ENG-003

**① 位置** `backend/tasks_history.py:22`（旧 `tid = int(time.time()*1000)`，主键见 `database.py:156` `id INTEGER PRIMARY KEY`）；异常被吞点 `backend/core/auto_dm.py:535-536`（`except Exception → logger.warning("[ENG-003] ...")`）

**② 判定：真（已实测复现）**

**③ 修法**
- 保持 `int` 主键不变。新增模块级 `_task_id_lock` + `_last_task_id` 与 `_next_task_id(conn)`：
  - 进程内加锁单调：`cand = max(now_ms, last+1)`；
  - **跨进程兜底**：`cand = max(cand, MAX(id)+1)`（本进程内存看不见别的 sidecar 刚写的行）；
- `start_task` 改为「取号 → INSERT → 命中 `sqlite3.IntegrityError` 则重算重试一次」，并修正了参数顺序（原参数打包改动导致列错位，已重写为显式命名局部变量）。

**④ 验证命令 + 实测输出**

```
"$LOCALAPPDATA/Programs/Python/Python314/python.exe" "$DY_APP_ROOT/reproA.py"   # 冻结同一毫秒连开两任务
```

修复前：`第 2 条异常: IntegrityError UNIQUE constraint failed: tasks.id` →`tasks 表实际行数 = 1 （期望 2）`
修复后：
```
第 1 条 tid = 1790160000123
第 2 条 tid = 1790160000124
tasks 表实际行数 = 2 （期望 2）
旧公式裸算两个 tid: 1790160000123 1790160000123 -> 相同? True
旧形态同 id 双插 -> sqlite3.IntegrityError: UNIQUE constraint failed: tasks.id
```

回归测试：`test_id_uniqueness.TestTaskIdUniqueness`（3 条：同毫秒两条都落库、跨进程 `MAX(id)` 兜底、保持 int 主键契约）+ 负控 `test_legacy_task_pk_formula_collides`（临时 sqlite 上重放旧公式，断言必抛 `IntegrityError`）。

**⑤ 诚实标注**
- 跨进程靠 `MAX(id)+1` 兜底，属**读后写**，非原子；两个 sidecar 在同一毫秒都读到同一 `MAX(id)` 时仍可能撞，此时靠 `IntegrityError` 重试一次兜底；若重试仍撞则**显式抛出**（不再静默 —— 调用方 `auto_dm` 仍会吞成 ENG-003 警告，这一层不在我文件内，见下）。故此项对「同一进程内多账号并发」为强保证，「多 sidecar 同毫秒极端并发」为**有重试的弱保证**。

---

## P2-7 显式清空（解绑）失败

**① 位置** `backend/api/live_rooms.py:296`（旧）

**② 判定：真（已实测复现）**。旧式 `str(body.strategy_id or "").strip() or str(old.get("strategy_id") or "")` 把**显式空串**当成「未提交」。

**③ 修法** 引入判据「本次请求带没带这个字段」= pydantic `model_fields_set`：
- 可清空：`strategy_id` / `name` / `live_url`（带了就采信，含空串）；
- 不可清空：`room_id`（身份锚点，空串仍回落旧值）。

**④ 验证命令 + 实测输出**
修复前：`显式置空后 strategy_id = 'lc_shared' -> 解绑成功? False`（`旧表达式对 strategy_id='' 求值 = 'lc_shared'`）
修复后：`显式置空后 strategy_id = '' -> 解绑成功? True`
回归测试：`test_p2_live_guards.TestExplicitClear`（解绑生效 / name 可清空 / **未提交字段仍保留**（反向守卫）/ room_id 不可清空 / 负控旧表达式必失败）。

**⑤ 诚实标注** `save_room` 的语义现在依赖「字段是否出现在请求体」。**未验证真实前端**：前端 `RoomManagePage.tsx:124` 每次都 `saveLiveRoom({...draft, id})`，`draft` 含全部字段，故每次都会发送 `strategy_id`（选「未绑定」时为 `""`）⇒ 本修复满足该 UI。若将来有调用方改为「只发要改的字段」，未发字段会安全回落旧值（不误清空），行为正确。真实浏览器渲染留待前端侧验证（不在本批）。

---

## P2-8 写入侧不校验 `strategy_id` 存在性 → 可写悬空引用

**① 位置** `backend/api/live_rooms.py:296`（旧，写路径无校验）

**② 判定：真（已实测复现）**。旧：`ok = True 落库 strategy_id = 'lc_does_not_exist'`。

**③ 修法** 新增 `_strategy_exists(sid)`；`save_room` 落库前校验，不存在则 `{"ok": False, "error": "直播策略 ... 不存在（禁止写入悬空引用）"}`。空串（未绑定）视为合法，不堵解绑路径。

**④ 验证命令 + 实测输出**
修复后：`ok = False error = '直播策略 lc_does_not_exist 不存在（禁止写入悬空引用）'  库内房间数 = 0`
回归测试：`test_p2_live_guards.TestReferentialIntegrity`（悬空被拒 / 存在的接受 / 空串仍可写）。

**⑤ 诚实标注** 引用完整性现在是**双向**维护（写房间校验 + 删策略解绑）。竞态窗口：策略在「校验通过」与「落库」之间被另一进程删除，仍可能写入悬空引用 —— 未做事务化，如实标注。

---

## P2-10 删除策略先持久化删除再解绑；解绑失败仍 ok=True

**① 位置** `backend/api/live_config.py:216-226`（旧）

**② 判定：真（已实测复现）**。旧响应：`{'ok': True, 'deleted': 'lc_ref', 'unbound': 0, 'warning': '...'}`，且策略已删、房间仍指 `lc_ref` = 悬空引用。

**③ 修法** 调整顺序为「**先解绑 → 再删策略**」：
- 解绑抛异常 → **不删除**该策略，返回 `{"ok": False, "error": "解绑引用房间失败，已中止删除以避免悬空引用: ..."}`（保证「有引用就有策略」，可重试）；
- 解绑成功 → 再落盘删除；删除失败同样 `ok=False`。

**④ 验证命令 + 实测输出**
修复后：
```
delete 响应 = {'ok': False, 'deleted': '', 'unbound': 0, 'error': '解绑引用房间失败，已中止删除以避免悬空引用: 模拟解绑失败'}
策略是否已被删除 = False  房间 strategy_id = 'lc_ref'
=> 悬空引用? False
```
回归测试：`test_p2_live_guards.TestDeleteStrategyOrdering`（解绑失败 → ok=False 且策略仍在 / 正常路径 unbound=1 / 删不存在的策略 ok=False）。
既有契约守卫 `test_live_rooms.TestDeleteStrategyUnbinds`（unbound 计数 2、无悬空引用）保持绿。

**⑤ 诚实标注** 响应新增 `ok=False` 分支。前端是否已有「非 ok 也提示」的处理未逐行核验（`RoomManagePage` 的删除路径不涉及此端点；`delete_strategy` 的调用方在策略页，不在本批范围）。已如实保留 `deleted` 字段为空串以示「未删除」。

---

## P2-4 同一概念两条矛盾判据

**① 位置** `backend/services/verdicts.py:33`（`is_placeholder_name`）与 `:60`（`is_uid_placeholder`）

**② 判定：真（已实测复现）**。修复前：`is_placeholder_name('12345')=True` 而 `is_uid_placeholder('12345')=False`。

**③ 修法** 收敛为**唯一实现** `is_placeholder(value, peer_id, min_digits=UID_MIN_DIGITS, min_len=1)`；`is_placeholder_name` / `is_uid_placeholder` 保留为**同义薄封装**（调用方零改动），阈值来自 `kernel/truth.UID_MIN_DIGITS`（不再各写一套字面量）。

**④ 验证命令 + 实测输出**
修复后：
```
  value='12345'                is_placeholder_name=False  is_uid_placeholder=False
  value='3887506227210423'     is_placeholder_name=True   is_uid_placeholder=True
  value='小张'                   is_placeholder_name=False  is_uid_placeholder=False
  value=''                     is_placeholder_name=True   is_uid_placeholder=True
```
回归测试：`test_p2_live_guards.TestVerdictSingleSource`（对 9 种输入两函数永不分歧 / 阈值来自 SSOT / 短纯数字昵称判真实 / 真实 uid 判占位 / 对端 uid 污染）+ 负控（旧两套规则对 `'12345'` 必分歧）。
消费方回归：`services/probe.py`、`api/messages.py`、`services/nickname_fallback.py` 均经 `is_placeholder_name` / `is_uid_placeholder` 调用；`test_capability_probe`（含 `test_numeric_nickname_is_degraded_or_failed`）在 `tests_regress6.txt` 的 182 测试整跑中通过。

**⑤ 诚实标注** 语义发生了变化：**纯数字但长度 < 6 的值（如 '12345'）现在判「真实」**（旧 `is_placeholder_name` 判占位）。这是有意取舍 ——「用户可以把昵称设成短纯数字」是真实场景，且这是两条判据中门槛更高、更不容易误杀的那一条。**对下游的影响未逐点评估**（如 `probe` 的 nickname_ratio 会因短数字昵称不再算占位而变化）；已知 `test_capability_probe` 全绿，故未见回归。

---

## P2-5 所谓「单一事实来源常量」零消费者 + kernel/ 缺 `__init__.py`

**① 位置** `backend/kernel/truth.py:7,13`；`backend/kernel/`（旧无 `__init__.py`）

**② 判定：真（已实测复现）**
- 全仓对 `NICKNAME_SOURCE` 的引用**只有 truth.py 自身**（`grep` 证据见下），`kernel.truth` 零 import；
- `kernel/__init__.py` 不存在 → `kernel` 是隐式 namespace package（修复前 `kernel.__file__ = None`）。

**③ 修法（处置二选一：**选「真正接线」**）**
- 新增 `backend/kernel/__init__.py`（真包）。
- `kernel/truth.py` 扩展为「常量 + 判据阈值」SSOT，**新增 `UID_MIN_DIGITS`**，由 `services/verdicts.py` 实际 import 消费。
- **选择理由（为何不删）**：`docs/architecture.md`（§8 + 校准横幅）与 `docs/design-contracts/C-01-im-capture.md` 已把 `backend/kernel/truth.py` 的 `NICKNAME_SOURCE` 写成权威出处（3 处）。删除会让这些文档指向不存在的模块，反而再造一处漂移；接线让「文档指向的常量」真的被代码消费。删除需同步改 3 处文档 + 勘察文档，风险大于收益。

**④ 验证命令 + 实测输出**
修复前：
```
kernel.__file__ = None  __path__ = ['...\\backend\\kernel']
（全仓 grep 'NICKNAME_SOURCE'：仅 kernel/truth.py 自身 1 处）
```
修复后：
```
kernel.__file__ = C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\backend\kernel\__init__.py  __path__ = ['...\\backend\\kernel']
NICKNAME_SOURCE = indexeddb:<uid>_user
NICKNAME_SOURCES_ORDER = ('indexeddb', 'dom', 'active_batch')
```
回归测试：`test_p2_live_guards.TestKernelTruthWired`（真包形态 / 常量完整 / **消费者机械核验**：`verdicts.py` 必须出现 `from kernel.truth import` / 文档引用的模块真实存在）。

**⑤ 诚实标注**
- 现在 `NICKNAME_SOURCE` / `NICKNAME_SOURCES_ORDER` 的**唯一真消费者是文档**（按常量名引用），代码消费者只有 `UID_MIN_DIGITS`（经 verdicts）。即：SSOT 已「真包 + 至少一条代码消费链」，但**两个字符串常量本身仍未被代码逻辑读取**（项目里昵称来源是硬编码在 capture/bcc 模块的行为，不是按常量分派）。未把 capture/bcc 改成「按常量分派」（超出本批文件范围且属重构），如实登记为**部分接线**。
- 已知残留不一致（不在我文件内，如实登记）：`backend/errcode_data.py:83` 的 `CAP.intent` 仍写「昵称/头像唯一来源 = BCC 被动 hook 截前端自发 im/user/info」，与 `NICKNAME_SOURCE` 的现行事实相反 —— 属**父会话/其它文件**的活口。

---

## P2-6 昵称占位判据第 5 处仍内联（**不归我，仅登记**）

**① 位置** `backend/daemon/recv_daemon.py`
- `:376` SQL 内联 `... OR peer_name=peer_id OR peer_name=?`（`_nick_pending`/回填路径）
- `:468` `if not _pn or _pn == r["peer_id"]:`（`_load_from_db` 富化判定）
- `:513-520` `COALESCE(... peer_name != '' ...)` 覆盖保护
文档/勘察声称昵称占位判据已收敛 5/5，实际第 5 处（recv_daemon）仍是内联 SQL/比较，**未接线到 `services.verdicts`**。

**② 判定：真（静态可见，非本批可改）**
**③ 修法：无（文件不归我，`recv_daemon.py` 由父会话/其它子 agent 负责）**
**④ 证据**：`grep -n "peer_name=peer_id" daemon/recv_daemon.py` → `376: "     OR peer_name=peer_id OR peer_name=?)"`；`grep -n "is_placeholder\|is_uid_placeholder" daemon/recv_daemon.py` → **0 命中**（未接线）。
**⑤ 诚实标注** 我只做了静态确认（grep），**未运行** recv_daemon 验证其行为，因为那会启动浏览器/WS 达人对端点（超出本批、且文件不归我）。请父会话把它并入 P2-4 的接线收口。

---

## P2-9 三个 `timeout_*` 声明 apply:"hot" 却缺 min/max

**① 位置** `backend/services/app_config_schema.py:55 / :61 / :67`（旧）

**② 判定：真**（`min=None max=None`；`services/app_config.py:169-174` `_coerce` 只在声明了 min/max 时才校验范围 ⇒ 声明缺失 = 无范围保护）

**③ 修法** 补齐边界（SSOT 一致：无论消费方是否自 clamp，schema 声明都必须有边界）：
- `timeout_bcc_http` min 0.5 / max 120.0
- `timeout_fast_probe` min 0.05 / max 10.0
- `timeout_http_req` min 1.0 / max 300.0

**④ 验证命令 + 实测输出**
修复后：
```
  timeout_bcc_http     default=15.0     min=0.5      max=120.0
  timeout_fast_probe   default=0.3      min=0.05     max=10.0
  timeout_http_req     default=30.0     min=1.0      max=300.0
```
回归测试：`test_p2_live_guards.TestTimeoutSchemaBounds`（三项都有 min/max 且默认值在范围内 / `_coerce(-1)` 与超上限被拒、默认值通过 / 全 schema 数值字段边界巡检，`dm.*` 三项遗留显式白名单登记不掩盖）。
既有守卫 `test_app_config` 全绿（含 `test_defaults_pass_their_own_range`）。

**⑤ 诚实标注** 「**消费端未全接线**」这部分我**未改**（消费端散落在 BCC/HTTP 调用点，多不在我文件内）。本批只保证「schema 声明与 SSOT 一致、越界值会被 `_coerce` 丢弃」。消费端是否真读这些键、是否仍 hardcode，未逐点核验 —— **另批**。

---

## P4 死代码：`live_rooms._FIELDS` 白名单未被消费

**① 位置** `backend/api/live_rooms.py:63`（旧 `_FIELDS`）

**② 判定：真（死代码）**

**③ 修法** 真消费：`save_room` 落库前 `upd = {k: v for k, v in upd.items() if k in _FIELDS}`；`id` / `updated_at` 为**服务端托管**字段，白名单过滤后重新写入（否则 `list_rooms` 按 `updated_at` 排序失效、且既有测试要求记录含 `updated_at`）。

**④ 验证命令 + 实测输出**
- 源码证据：`grep "k in _FIELDS" api/live_rooms.py` → 命中。
- 行为证据（`reproA.py` 末段）：把 `'name'` 从 `_FIELDS` 摘掉后保存 → `落库记录含 name? -> False`（即白名单是真门禁；若为死代码则必为 `True`）。
- 回归测试：`test_p2_live_guards.TestWhitelistIsRealGate`。

**⑤ 诚实标注** `_FIELDS` 仍与 `_normalize_room` 的 `setdefault` 集合**在语义上重叠**（读路径补默认、写路径做门禁），未合并成单一数据结构 —— 合并属重构，未做。

---

## 改动文件清单

| 文件 | 变更 |
|---|---|
| `backend/api/live_rooms.py` | P0-2a（房间 id + 迁移 id）、P2-7、P2-8、P4 |
| `backend/api/live_config.py` | P0-2a（策略 id）、P2-10 |
| `backend/tasks_history.py` | P0-2b |
| `backend/services/verdicts.py` | P2-4、P2-5（接线） |
| `backend/services/app_config_schema.py` | P2-9 |
| `backend/kernel/truth.py` | P2-5（+`UID_MIN_DIGITS`） |
| `backend/kernel/__init__.py` | **新增**（P2-5 真包） |
| `backend/test_live_rooms.py` | 既有 2 条用例改为「先建策略再绑定」（P2-8 后悬空写入被拒），不改判据语义 |

新增测试文件：
- `backend/test_id_uniqueness.py`（9 条：房间/策略/任务 id 唯一性 + 2 条负控）
- `backend/test_p2_live_guards.py`（26 条：P2-4/5/7/8/9/10 + P4 + 3 条负控）

**未触碰**（守范围）：`probe.py`、`dm_dispatch.py`、`dispatch.py`、`recv_daemon.py`、`client_im.py`、`api/messages.py`、`api/engine.py`、`api/platform.py`、`api/notify.py`、`engine_registry.py`、`replay/*`、`scripts/*`、前端、版本源文件、`工作记忆/`、`artifacts/UP_*`。未执行 `git add/commit/checkout/stash/clean`。

---

## 完整测试输出尾部（实跑）

主证据（本次批次的三个模块）：
```
$ cd C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend
$ export DY_APP_ROOT="$LOCALAPPDATA/Temp/fixA"
$ "$LOCALAPPDATA/Programs/Python/Python314/python.exe" -m unittest test_live_rooms test_id_uniqueness test_p2_live_guards -v
...
Ran 57 tests in 0.499s

OK
```
（`%LOCALAPPDATA%\Temp\fixA\final_run.txt`）

回归（受我改动影响的既有模块）：
```
$ ... -m unittest test_live_rooms test_app_config test_no_dup_dict_keys test_id_uniqueness test_p2_live_guards -v
Ran 84 tests in 1.522s
OK                                          # tests_regress4.txt

$ ... -m unittest test_live_config_guards test_config_tag test_settings_api test_b4_global_switches test_task_delay_sentinel test_engine_idle_guard test_cross_account_sink -v
Ran 65 tests in 3.104s
OK                                          # tests_regress5.txt

$ ... -m unittest test_live_anon_decouple test_b3_capture_probe test_replay_conversation_read test_dm_dispatch_config -v
Ran 43 tests in 4.965s
OK                                          # tests_regress7.txt

$ ... -m unittest test_upstream_p1 test_upstream_p3 test_upstream_p4 test_upstream_p5 test_live_ai_wiring test_live_anon_decouple -v
Ran 151 tests in 12.292s
OK                                          # tests_regress8.txt

$ ... -m unittest test_live_identity_verdict -v
Ran 19 tests in 0.318s
OK                                          # tests_verdict.txt

$ ... -m unittest test_capability_probe test_upstream_p1 test_upstream_p3 test_upstream_p4 test_upstream_p5 test_live_ai_wiring -v
Ran 182 tests in 25.830s
FAILED (errors=1)   # 唯一 error = 我拼错模块名 test_live_anonym_decouple（不存在），
                    # 已用正确名 test_live_anon_decouple 重跑（见 tests_regress8.txt，151 OK）。
                    # probe 侧 176 条全过（含 test_numeric_nickname_is_degraded_or_failed），
                    # 证明 P2-4 判据变更未回归。
```

**独立环境提示（非我的缺陷）**：`test_capability_probe` 后续单独重跑时，因其隔离库
`%TEMP%\dyautodm_cfgtest_root` 被**另一个 agent 的进程**占用，出现
`[db] conv_type 回填失败: database is locked` 与 30s busy-wait（单测整体卡住超时）。
这是**并发跑测试的共享目录争用**，与本批代码无关；上面的 182-test 整跑（争用发生前）
已含 `test_capability_probe` 全绿。

---

## 诚实总结（未验证部分）

1. **P0-2a 跨进程**：仅对目标 dict 快照查重，非 DB 级原子；多 sidecar 同毫秒对同一旧快照取号仍可能撞（与修复前同风险，未加 DB 约束）。同一进程内串行取号已彻底修好。
2. **P0-2b 跨进程**：`MAX(id)+1` 读后写非原子，靠一次 `IntegrityError` 重试兜底；调用方 `core/auto_dm.py` 仍会把最终异常吞成 `ENG-003` 警告（该文件不归我）。
3. **P2-10 响应形状变化**：新增 `ok=False` 分支；前端对非 ok 呈现未逐行核验。
4. **P2-5**：`NICKNAME_SOURCE` / `NICKNAME_SOURCES_ORDER` 的代码消费者仍无（只有文档按名引用）；代码消费链只到 `UID_MIN_DIGITS`。`errcode_data.py:83` 仍与现行事实相反（不归我）。
5. **P2-9**：消费端接线未核验、未改（另批）。
6. **P2-6**：仅静态登记，未跑 recv_daemon。
7. **真实 UI/浏览器**：本批全部为单测 + 冻结时钟脚本，**未做真机渲染/端到端**验证（不在本批范围）。
