# HC-16 / M-30 报告：红心（真实点赞数）刷新节拍 → 显式可配

- 日期：2026-10-01
- 分支：`design/better-douyin`（git 根 `C:\Users\LOX\Desktop\DYchajian`）
- 文件所有权：本报告子任务只 touched 2 个源码文件 + 本报告文件；未碰版本文件、未碰他人文件、未执行任何 git 写操作。

---

## ① 改动清单

| 文件 | 改动性质 | 说明 |
|---|---|---|
| `DYAutoDM_v2/backend/services/app_config_schema.py` | **新增 14 行**（纯增量） | 在既有 `live` 分区新增字段 `rank_poll_interval_sec` |
| `DYAutoDM_v2/backend/core/live_hook.py` | **新增 63 行 / 改 3 行** | 模块级常量 + `_rank_poll_seconds()` 解析函数；`start_rank_poll` 改为读配置并 clamp；实例初值改指常量 |

### 1.1 schema（`app_config_schema.py`，`live` 分区）

```python
"rank_poll_interval_sec": {
    "label": "红心刷新间隔（秒）",
    "type": "int", "default": 60, "min": 15, "max": 600, "env": None,
    "apply": "hot", "hint": "越小越频繁"
},
```

- **为什么落在这里**：全仓 grep `interval`/`poll` 后，`live` 分区已有同构整数字段（`interval` / `live_poll_interval` / `ws_heartbeat_interval`），是项目现成的配置体系，遵循「不新建第二处配置源」。
- **为什么没有收敛到既有的 `live_poll_interval`**：该键 label 是「**未开播**轮询间隔」（default 30，探房间是否开播），与「已开播后的红心刷新节拍」语义不同、默认值也不同，合并会误伤既有行为 ⟹ 判定为**不同语义**，另立键而非复用。
- `apply: hot` ⇒ 下一次启动监听（`start_ws`）即生效，无需重启。

### 1.2 消费侧（`core/live_hook.py`）

```python
KEY_RANK_POLL_SEC = "rank_poll_interval_sec"
RANK_POLL_DEFAULT = 60     # 与改前硬编码一致 ⇒ 不改配置 = 行为完全不变
RANK_POLL_MIN     = 15     # 下限
RANK_POLL_MAX     = 600    # 上限
```

- 新增 `_rank_poll_seconds()`：**读取顺序 = 策略层 kv `config.live[key]` → `app_config.live[key]` → 默认 60**，与 `services/live_automation.py:45 _cfg()` 完全同构（同源优先级约定）。
- `start_rank_poll(interval: Optional[int] = None)`：`interval` 显式传入时用它的 clamp 值；**不传则走配置路径**。项目唯一调用点 `start_ws`（原 `:390`）不传参 ⇒ 自动走配置。
- 线程循环 `_rank_loop` 的等待语句 `self._rank_stop.wait(self._rank_interval)` **未改动**（GET 节拍值的载体仍是 `_rank_interval`，只是来源从写死改为可配）。

### 1.3 下限保护（写小即抬回）

两道防线：

1. **schema 侧**：`min: 15 / max: 600`（设置页写入时约束）。
2. **运行侧再自守**：`start_rank_poll` 内 `max(15, min(600, sec))`。理由写在代码注释里——节拍直接决定对抖音 `reflow/info` 的出站频次，**低于 15s 会把请求形状推向「高频且绝对规律」**，这是风控敏感面；用户手滑写 1 也不应被忠实执行。上限 600s 是因为再慢已失去分钟级实时的意义（此时别人点赞仍由 `ws_delta` 立即可见）。

> 不合法值（`'abc'`/`None`/缺配置）一律**回落默认 60**，而非「放行」——继承 `_cfg()` 的「读不到即回落，不因配置缺失改变行为」纪律。

---

## ② 验证数字

### 2.1 门禁 `test_live_likes_rank.py`（**必须全绿**）

命令：`cd DYAutoDM_v2/backend && python -m pytest test_live_likes_rank.py -v`
解释器：`C:\Users\LOX\AppData\Local\Programs\Python\Python314\python.exe`（Python 3.14.6 / pytest 9.1.1）

```
platform win32 -- Python 3.14.6, pytest-9.1.1, pluggy-1.6.0
rootdir: C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\backend
plugins: anyio-4.14.2
collecting ... collected 15 items

test_live_likes_rank.py::test_ws_like_accumulates PASSED                          [  6%]
test_live_likes_rank.py::test_stats_zero_does_not_clobber_ws_total PASSED         [ 13%]
test_live_likes_rank.py::test_stats_abs_wins_when_larger PASSED                   [ 20%]
test_live_likes_rank.py::test_stats_only_when_no_ws_events PASSED                 [ 26%]
test_live_likes_rank.py::test_on_room_stats_merges_and_feeds_heat PASSED          [ 33%]
test_live_likes_rank.py::test_reset_stream_clears_like_sources PASSED             [ 40%]
test_live_likes_rank.py::test_normalize_rank_users_shape PASSED                   [ 46%]
test_live_likes_rank.py::test_normalize_rank_alt_shape_and_score_text PASSED      [ 53%]
test_live_likes_rank.py::test_normalize_rank_business_error_returns_empty PASSED  [ 60%]
test_live_likes_rank.py::test_normalize_rank_garbage_never_raises PASSED          [ 66%]
test_live_likes_rank.py::test_resolve_anchor_noop_without_auth PASSED             [ 73%]
test_live_likes_rank.py::test_resolve_anchor_skips_when_already_present PASSED    [ 80%]
test_live_likes_rank.py::test_real_like_is_additive_not_max PASSED                [ 86%]
test_live_likes_rank.py::test_refresh_real_resets_ws_delta_no_double_count PASSED [ 93%]
test_live_likes_rank.py::test_real_zero_does_not_downgrade PASSED                 [100%]

============================= 15 passed in 3.96s ==============================
```

**15 passed / 15 collected，0 failed**（改前基线同为 15 passed，无回归）。仅跑单文件，**未做全量 discover**（避免与并行线争共享测试根）。

### 2.2 R15 文案门禁 `check_schema_copy.py`

命令：`cd C:\Users\LOX\Desktop\DYchajian && python DYAutoDM_v2/scripts/check_schema_copy.py`

```
check_schema_copy  阈值: hint≤10 / label≤18
  [PASS] 全部字段文案合规
EXIT=0
```

新增项自查：`label` = 「红心刷新间隔（秒）」= **9 字**（≤18 ✓）；`hint` = 「越小越频繁」= **5 字**（≤10 ✓）；且不含 `/api`、`BCC` 等 R15-3 开发信息。

**刻意失败态 / 负控**（`--selftest`，证明门禁真的会拦而非恒绿）：

```
=== python DYAutoDM_v2/scripts/check_schema_copy.py --selftest ===
check_schema_copy  阈值: hint≤10 / label≤18
  [FAIL] 命中 2 条：
    - [R15-1] dm.nickname_fallback_enabled hint 39>10: XXXXXXXXXXXXXXXXXXXXXXXXXXXXXX /api/foo…
    - [R15-3] dm.nickname_fallback_enabled hint 含开发信息「/api/」: XXXXXXXXXXXXXXXXXXXXXXXXXXXXXX /api/foo…
  [OK] 负控：注入后确实变红 ✓
EXIT=0
```

### 2.3 `py_compile`（改动的两个 py 文件）

```
python -m py_compile DYAutoDM_v2/backend/core/live_hook.py DYAutoDM_v2/backend/services/app_config_schema.py
→ COMPILE_OK
```

### 2.4 行为自证（临时脚本验证，非新增仓库文件）

| 验证项 | 输入 | 实测结果 | 判定 |
|---|---|---|---|
| **默认 60s 行为不变** | 真实 DB 环境、无任何配置值 | `_rank_poll_seconds()` = **60**；`start_rank_poll()` 不传参 → `_rank_interval` = **60** | ✅ 零回归 |
| 配置生效 | mock `app_config.get` 返回 25 | `_rank_interval` = **25**，且 `get` 调用参数为 `('live','rank_poll_interval_sec',60)` | ✅ 键名/分区/default 全对 |
| 下限保护 | 配置写 8 / 显式传 0 / 传 1 | 一律 **15** | ✅ 抬回下限 |
| 上限保护 | 配置写 900 | **600** | ✅ |
| 脏值回落 | 配置写 `'abc'` / `None` | **60** | ✅ 不放行 |
| 策略层优先 | kv `config.live` = 42 且 app_config = 7 | **42**（kv 优先） | ✅ 与 `_cfg()` 同构 |
| 真·节拍驱动 | `interval=15` 跑真线程 | 首次刷新在 **0.0s**（循环首圈立即执行），17s 内**多余触发 0 次** | ✅ `_rank_interval` 驱动未被破坏 |
| 幂等 | 连续两次 `start_rank_poll()` | 线程对象**未更换** | ✅ 保持原语义 |
| **红心相加语义** | real=976 + ws_delta=5 | **981**（非 max、未降级、未双计） | ✅ 未触碰 |

---

## ③ 唯一标识符

- **新配置键名**：`rank_poll_interval_sec`（section `live`，即 `app_config.live.rank_poll_interval_sec` / 策略层 `config.live["rank_poll_interval_sec"]`）
- **默认值**：`60`（秒）
- **取值包络**：`[15, 600]`，越界 clamp、脏值回落 60
- **代码常量**：`core.live_hook.KEY_RANK_POLL_SEC` / `RANK_POLL_DEFAULT` / `RANK_POLL_MIN` / `RANK_POLL_MAX`
- **UI 文案**：label「红心刷新间隔（秒）」/ hint「越小越频繁」

---

## ④ 诚实标注

1. **`interval` 形参语义有微调**：`start_rank_poll(interval: int = 60)` → `Optional[int] = None`。全仓 grep 后**唯一的调用点就是 `start_ws` 里的 `self.start_rank_poll()`（不传参）**，故外部行为等价；但形参默认值从「60」变成「None（=读配置）」，任何直接调用 `start_rank_poll(60)` 仍得 60，无破坏。
2. **未在 UI/前端改动**：无任何 `.tsx/.ts` 改动。设置页由 schema 驱动自动渲染（`services/app_config.py` 文档即声明「前端据此渲染，无需为每字段写 UI」），本字段应当自动出现，但**我没有起前端/IDE 实机验证 UI 是否真的渲染出这一项**。
3. **未做真机抖音验证**：所有验证均在**离线/不发请求**的前提下完成（mock `fetch_rank` / `_refresh_real_likes`）。没有验证真实 `reflow/info` 在 15s 节拍下的实际风控表现——15s 下限是**保守取值建议，不是实测安全值**。
4. `docs/_tmp_appconfig.py` 里有一份 `live_poll_interval`/`ws_heartbeat_interval` 的**临时副本**，它本身已落后（无本次新增键）。该文件看着是 docs 下的临时脚本、无人 import（grep 无命中），**我没有动它**（非本任务文件，且疑似他人域）。若需一致性，应另行清理。
5. 验证脚本均为临时内联执行，**未落盘进仓库**（除本报告外零新增文件）。
6. 跑测试/探针会在 `backend/core/__pycache__` 等处留下编译缓存（`DY_APP_ROOT` 指向 `C:\temp\dyautodm_design` 的一次性 DB 亦由本任务之外既有惯例产生），本人未 `git add` 任何内容，git 状态未变。

---

## ⑤ 交叉判据

```bash
# ① 新键名全仓落点（排除 vendor / appinternals）
cd C:/Users/LOX/Desktop/DYchajian
grep -rn "rank_poll_interval_sec" --include=*.py --include=*.ts --include=*.tsx --include=*.json . | grep -v vendor | grep -v appinternals
# 命中 3：
#   DYAutoDM_v2/backend/core/live_hook.py:42            KEY_RANK_POLL_SEC 常量
#   DYAutoDM_v2/backend/core/live_hook.py:412           docstring（配置路径说明）
#   DYAutoDM_v2/backend/services/app_config_schema.py:177   schema 声明
# ⇒ 恰好「一处声明 + 一处消费」，无第二份配置源

# ② 节拍载体 _rank_interval 的所有引用
grep -rn "_rank_interval" --include=*.py . | grep -v vendor
# 命中 3：live_hook.py:145(初值=RANK_POLL_DEFAULT) / :385(wait 驱动) / :427(clamp 赋值)
# ⇒ 循环 wait 语句未改，仅赋值来源改为可配

# ③ start_rank_poll 调用方（确认无其他散点调用）
grep -rn "start_rank_poll" --include=*.py . | grep -v vendor
# 命中 5：live_hook.py:142/143(注释) / :406(定义) / :450(唯一调用点 start_ws) / app_config_schema.py:176(消费点注释)

# ④ clamp 常量引用总数
grep -rn "RANK_POLL_" --include=*.py . | grep -v vendor | wc -l    # → 14

# ⑤ 硬编码 60 是否残留（rank 相关行中含 60 的）
grep -n "rank" DYAutoDM_v2/backend/core/live_hook.py | grep -n "60"
# 仅命中 1：:412 docstring 里的「默认 60」字样（非代码）

# ⑥ 确认既有的不同语义键（勿与之混淆）
grep -rn "live_poll_interval\|ws_heartbeat_interval" --include=*.py --include=*.tsx . | grep -v vendor | grep -v appinternals
# 命中 4：app_config_schema.py:158/163 + docs/_tmp_appconfig.py:113/118（临时副本，未动）
# ⇒ live_poll_interval = 「未开播轮询间隔」30s，与本次语义不同，未复用
```

---

## ⑥ 疑点

1. **`live_poll_interval` / `ws_heartbeat_interval` 目前在 backend 无任何消费点**（仅 schema 声明 + docs 临时副本）。这两个键可能已「声明但未接线」——与本次改动无关，但值得单独立项排查是否有历史遗留僵尸配置。
2. **`ws_heartbeat_interval` 与本次键可能有协同**：WS 心跳（默认 300s）与红心刷新（默认 60s）都影响重连/刷新密度。若用户把红心调到 600s、心跳 300s，实际观感会变成「偶发跳变」。是否需要在 UI 加一句关系说明（会撞 R15 hint ≤10 字的限），建议产品侧定夺，**本任务未擅自加**。
3. **15s 下限缺乏实测依据**：这是基于「请求频次/风控形状」的保守工程判断，不是抖音侧实测的安全阈值。若后续拿到实测数据，应在注释里替换依据而非仅调数字。
4. **`apply: hot` 的实际生效时机**：严格说是「下一次启动监听时生效」——已在运行的监听不会中途改节拍（线程已在 `wait(旧值)` 中）。这是既有 hot 字段的一致语义（与 `interval` / `live_poll_interval` 同款），但 UI 上用户可能误解为「立即生效」。文档/UI 是否要更明确，留给 UI 侧。
5. **`_rank_loop` 首圈无延迟**：改小节拍时首秒内必然有一次 `reflow/info` 请求，频繁重启监听会叠加请求。是否给首圈加随机抖动（打散请求形状）值得后续考虑，**本任务未改**（超出 M-30 范围，且改了会破坏「零回归」）。
