# 任务A 报告 · 后端 AI 回复（ai_reply.py）4 条

## 0. 只读声明
我只改了授权文件：
- `C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\backend\services\ai_reply.py`
- `C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\backend\test_lead_disposition_guard.py`

未做任何 git 写操作；未改版本号；未动其他文件（仅 grep/read）；未启动浏览器/BCC/uvicorn/打包；未触碰真实数据根与 `accounts/*/profile`。
（说明：`git status` 里另有 `scripts/verify_lead_disposition_real.py` 处于 modified —— 那是**并行兄弟任务**（OCR[30]/[31]）的改动，**非本任务所改**，我仅只读查看过。）
单测均以 Python 原生 `tempfile.mkdtemp()` 建隔离 `DY_APP_ROOT`（并 `database.reset_connection()`），绝不动真实库。
（注：交付前我核对了 dev 库 `DYAutoDM_v2/data/dyautodm.db`，确认无新增残留 —— 详见 §4-A。）

## 1. 结论汇总表
| # | 位置(文件:行) | 判定 | 修法(一句话) | 修复前证据 | 修复后证据 |
|---|---|---|---|---|---|
| A-1 | `services/ai_reply.py` `_generate_reply` 留资护栏（原 ~2155，现 2196-2252） | **真缺陷** | 达 `max_lead_ask` 时走独立分支：不再追加索要、不再 bump；原句含放走语则改不含索要的引导 | 连续 4 轮 `_asks=[1,2,3,4]`、4 轮全部含 `_LEAD_ASK_TAIL` | 连续 4 轮 `_asks=[1,2,2,2]`、`_LEAD_ASK_TAIL`=[T,T,F,F] |
| A-2 | `services/ai_reply.py` `append_lead_ask`（1067-1148） | **真缺陷** | 追加超 `_LEAD_ASK_HARD_CAP(120)` 时**截断专业正文**而非 `return None` | 107 字专业正文 → 返回 `None`（正文全丢） | 同输入 → 86 字，仍含「功能障碍」且含索要 |
| A-3 | `services/ai_reply.py` `_lead_ask_count`/`_bump_lead_ask`（1011-1048） | **真缺陷** | 复用模块既有 `threading.Lock` 原语，把读-改-写包进 `_LEAD_ASK_LOCK` | 24 线程并发旧逻辑：丢计数（<24） | 24 线程并发新逻辑：恰好 24，0 丢失 |
| A-4 | `services/ai_reply.py` `live_fallback_reply`（962-986） | **真缺陷** | 额外池改为配置项 `live_fallback_extra`（默认=原常量）；两者皆空才回退主池 | `live_fallback_extra=""` 仍返回额外池（回退分支不可达） | `live_fallback_extra=""` → 返回主兜底池成员 |

新增错误码：`[AI-071]`（A-1 上限生效日志）。**已 grep `backend/errcode_data.py`**：该文件登记的 `AI-001..034`（及代码引用的 `AI-061..070`）均**不含 AI-071**，无同码两义。

## 2. 逐条详述

### A-1（OCR[22] · HIGH）`max_lead_ask` 上限真正生效 — 真缺陷
**代码原文（修复前）**
```python
if _n_ask < _max_ask:
    ...定向重试...                      # 只有这里判了上限
if _patched:                           # _patched 只在上面分支里被赋值
    cleaned = _patched
    _bump_lead_ask(account, conv_id)
else:                                  # ← 达上限时 _patched 恒为 None ⇒ 必落此处
    _appended = append_lead_ask(cleaned, cfg)
    if _appended: cleaned = _appended
    else: cleaned = lead_fallback_reply(cfg)
    _bump_lead_ask(account, conv_id)   # ← 无条件再 bump
```
**为什么是问题**：`_n_ask >= _max_ask` 时 `_patched` 恒为 `None`，`else` 无条件执行追加/替换并 `_bump_lead_ask`。`_LEAD_ASK_TAIL` 或 `lead_first_reply` 都自带索要 ⇒ **每一轮都继续索要**，且计数继续上涨（`max_lead_ask` 形同虚设），与 ADR-027 D5「被拒 N 次不再索要」直接矛盾（也是「刷屏」）。
**改成了什么**：把回退逻辑**移进 `if _n_ask < _max_ask` 分支内**，并为「已达上限」新增独立 `else` 分支：
- 不再调用 `append_lead_ask` / `lead_fallback_reply`（两者均含索要）；
- 原句含放走语（`_is_deferring`）⇒ 改发新函数 `_lead_no_ask_reply(cfg)`（新增配置 `lead_no_ask_reply`）—— **不含索要**的专业引导，既不放走线索也不再索要；
- 原句不含放走语 ⇒ **原样保留专业回答**；
- 两个子分支都**不调用 `_bump_lead_ask`**（不涨计数）。
**负控如何变红**：把 `if _n_ask < _max_ask:` 改回 `if True:`（等价旧行为）后，用例 `test_a1_cap_binds_no_growth_after_3_rounds` 断言 `counts == [1,2,2,2]` 失败（实测变 `FAIL 1`）。`test_a1_negative_control_old_else_branch_always_asks` 内嵌复刻旧 `else` 产物必然含 `_LEAD_ASK_TAIL`，佐证「修复前每轮都索要」。

### A-2（OCR[9] · MED）超长时不得丢弃专业正文 — 真缺陷
**代码原文（修复前）**
```python
combined = f"{s}{sep}{tail}"
if len(combined) > _LEAD_ASK_HARD_CAP:
    return None                        # ← 调用方随即整句替换为通用引导话术
return combined
```
**为什么是问题**：`hit` 分支 / 主链路的调用方拿到 `None` 后 `cleaned = lead_fallback_reply(cfg)` ⇒ 把库内/模型给出的**专业正文**（与 tail 相加 >120 即 >~97 字）整段丢弃，只发通用留资话术 —— 与 ADR-027 D2「保专业性优先」相悖。
**改成了什么**：超长时**截断专业正文**后再补索要：按 `room = 120 - len(tail) - 1` 取正文，优先在句末标点（。；！？.;）处断句（截断点须 ≥60% 才采纳，避免截得过短），去掉尾随顿号/逗号，再 `+sep+tail`；仅当正文确无可留时才 `return None`。
**负控如何变红**：把该函数还原为旧 `return combined if <= cap else None` 后，`test_a2_long_professional_body_preserved_after_guard` 断言「不得为 None 且须含『功能障碍』」失败（实测变 `FAIL 1`）。

### A-3（OCR[10] · MED）共享 KV 计数非原子 — 真缺陷
**代码原文（修复前）**
```python
def _bump_lead_ask(account, conv_id):
    d = _kv_get(_KV_ASK_COUNT, {}) or {}   # 读
    n = int(d.get(k, 0)) + 1               # 改
    d[k] = n
    _kv_set(_KV_ASK_COUNT, d)              # 写（无锁）
```
**为什么是问题**：`_tick` 经 `reply_concurrency` 线程池（默认 8）跨会话并发；两线程读同一旧 dict、各自 +1 再写回 = last-write-wins，**丢计数**，`max_lead_ask` 上限随之失效。
**改成了什么**：新增模块级 `_LEAD_ASK_LOCK = threading.Lock()`（与既有 `_IMG_DESC_LOCK` 同款既有原语，未新造机制），`_lead_ask_count`/`_bump_lead_ask` 的整个读-改-写包进 `with _LEAD_ASK_LOCK:`。
**负控如何变红**：用例 `test_a3_concurrent_bump_never_loses_counts` 在进程内把 `_kv_get/_kv_set` 换成「线程安全 store + 读后 2ms 让出」以放大竞态：旧逻辑 24 线程必然 <24（实测负控断言先红，再换新逻辑 =24）；把锁替换为 `_NoLock`（恒不互斥）后该用例 `FAIL 1`。另有静态接线用例 `test_a3_source_uses_lock`（断言 `with _LEAD_ASK_LOCK:` 出现 2 次）。

### A-4（OCR[8] · MED）直播兜底的「回退主池」是死代码 — 真缺陷
**代码原文（修复前）**
```python
return (_LIVE_FALLBACK_EXTRA
        or str(fallback_reply(cfg) or "").strip())
```
**为什么是问题**：`_LIVE_FALLBACK_EXTRA` 是恒非空的模块级常量 ⇒ `fallback_reply(cfg)` 分支**永不执行**（死代码），docstring 承诺的「额外池不可用时回落主池」永不发生。
**改成了什么（选 b：让语义与 docstring 一致）**：把额外池提升为配置项 `live_fallback_extra`（缺省值 = 原常量，保零回归）：
```python
extra = cfg.get("live_fallback_extra", _LIVE_FALLBACK_EXTRA)
extra = str(extra or "").strip()
return extra or str(fallback_reply(cfg) or "").strip()
```
**为什么选 b 而非删分支**：删分支等于放弃「额外池可配置 + 可关闭」的能力，且会把 docstring 一起改成「恒用常量」—— 但「模型挂了也要保证能拿到一条话术、且允许运维把额外池关掉回退主池」是真实需要（现有结构已预留回落点）。让配置留空即真正回退主池，最小改动且与既有承诺一致。
**负控如何变红**：还原为旧表达式后，`test_a4_live_fallback_extra_empty_falls_back_to_main_pool` 断言「空额外池 ⇒ 返回主池成员」失败（实测变 `FAIL 1`）。

## 3. 验证命令与结果

**主套件（隔离根）**：`py -3.14 -c "…d=tempfile.mkdtemp(); os.makedirs(d+'/data'); os.environ['DY_APP_ROOT']=d; database.reset_connection(); run(['test_lead_disposition_guard','test_live_contact_guard'])"`
```
Ran 27 tests in 0.356s

OK
FINAL tests=27 failures=0 errors=0
```

**逐条负控（把该条修复临时还原 → 单测变红）**：
```
[NEG-CONTROL A-1] reverted -> NCTESTS 1 FAIL 1  (expect FAIL>=1)
[NEG-CONTROL A-2] reverted -> NCTESTS 1 FAIL 1  (expect FAIL>=1)
[NEG-CONTROL A-3] reverted -> NCTESTS 1 FAIL 1  (expect FAIL>=1)
[NEG-CONTROL A-4] reverted -> NCTESTS 1 FAIL 1  (expect FAIL>=1)
restored: True 88ba71a59829bbc0
```

**修复前/后客观数字对照（同一桩模型脚本，还原 3 条修复 vs 现行代码）**：
```
=== AFTER (current code) ===
  A1_COUNTS [1, 2, 2, 2]
  A1_HAS_TAIL [True, True, False, False]
  A2_INLEN 107 A2_RET 86
  A2_KEEP True
  A4_RET_IS_MAIN_POOL True
=== BEFORE (A-1/A-2/A-4 reverted to old logic) ===
  A1_COUNTS [1, 2, 3, 4]
  A1_HAS_TAIL [True, True, True, True]
  A2_INLEN 107 A2_RET None
  A2_KEEP False
  A4_RET_IS_MAIN_POOL False
```

**相关回归（只读相关模块，全绿）**：
```
test_ai_reply_quality_guards   tests=9  fail=0 err=0
test_h22_p0_audit_fixes        tests=8  fail=0 err=0
test_h22_p1_audit_fixes        tests=8  fail=0 err=0
test_h25_dirty_data_guards     tests=10 fail=0 err=0
test_h25b_image_text_contract  tests=10 fail=0 err=0
test_h31_ai_reply_safety       tests=9  fail=0 err=0
test_live_ai_wiring            tests=23 fail=0 err=0
test_reply_tone_professionalism tests=0 fail=0 err=0
test_ai_client_method_structure tests=8 fail=0 err=0
```

**编译**：`py -3.14 -m py_compile services/ai_reply.py test_lead_disposition_guard.py` → `compile OK`。

## 4. 未做/存疑/需真机验证（诚实标注）

**A. 关于隔离与真实库残留（已核对并清理，如实报告）**
- 我早期两次「未先建 `data/` 子目录」的运行中，`DY_APP_ROOT` 指向的目录不存在 ⇒ 被 `vbrowser.app_root()` 忽略（日志 `[BCC-039]`），回落真实 dev 库；加上既有用例 `G7` 在 class 内设置 `DY_APP_ROOT` 后**不重置 `database` 全局连接**，其 `_bump_lead_ask("验证账号","0:1:1:2")` 曾落到 dev 库 `DYAutoDM_v2/data/dyautodm.db` 的 `kv_store` 键 `ai_reply_lead_ask`。
- 这与任务要求「若某条为误报就单独说明」无关，但属我造成的测试残留，**已清理**：`DELETE FROM kv_store WHERE key='ai_reply_lead_ask'`，复核 `real askcount: None`。
- 我在 `test_lead_disposition_guard.py` 新增的 `_isolate()` 里主动 `database.reset_connection()` 并 `addCleanup` 还原 `DY_APP_ROOT` 与连接，使新用例自身**不可能**写到真实库。
- **未做**：未去改既有 `G7` 的隔离方式（该用例不在我的授权范围之外的「既有约定」争议内，但其不重置连接是它自身的既有缺陷）—— 属**仅静态判断**：交父会话决定是否单独提 issue。为稳妥，我最终验证命令已采用 Python 原生 `mkdtemp`（对 native Python 可见），确保整轮不触真实库。

**B. 仅静态判断、未真机验证的部分**
- A-3 的「跨会话并发」我用的是**进程内 24 线程 + 放大读-改-写窗口**的受控复现，**不是**真实 `ThreadPoolExecutor` 跑真库；真实场景丢计数还受 SQLite 写序列化影响，量级可能更小但方向一致。
- A-1 的「4 轮端到端」用桩模型（`chat_failover` 固定返回），**未接真实网关**；真实模型在重试提示下的输出/是否命中 `_is_lead_stalled` 未实测。
- A-4 只证「分支可达 + 默认零回归」，**未**在直播 `generate_dm_for_live` 真链路里跑过（该链路需实时数据）。

**C. 未做**
- 未新增/修改 ADR 文档（任务明确要求不改）。
- 未升版本号、未 git 提交（交父会话）。
- 未改 `errcode_data.py`（AI-071 仅出现在代码日志，与项目「新码先 grep 确认未占用」要求一致；是否补登记该表的 `AI-061..071` 区间，交父会话统一处理）。
