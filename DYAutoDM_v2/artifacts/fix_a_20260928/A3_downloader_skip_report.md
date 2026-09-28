# A-3 施工报告：批量下载「单作品失败 ⇒ 整批中断」缺陷修复

- 日期：2026-09-28
- 分支：`design/better-douyin`（未切换）
- 仓库根：`C:/Users/LOX/Desktop/DYchajian`
- Python：`C:/Users/LOX/AppData/Local/Programs/Python/Python314/python.exe`（3.14.6，loguru+fastapi 可用；3.11 缺包会假红，故全程用 3.14）
- 结论来源：**全部为真实运行输出**（命令随附）。未核验项已在末尾列明。

---

## 一、缺陷（真实定位）

`backend/downloader/downloader.py` 的 `run_task()` 原实现把**前置准备**放在
`try:` **之外**（原 ~125-136 行）：

```python
if mgr.get(task.task_id) is None:
    mgr.add(task)
media = MR.extract_media(aweme)                    # ← 可抛（aweme 媒体无法解析）
task.media_type = media["type"]
task.quality = quality
mgr.mark(task.task_id, status=TK.RUNNING, ...)     # ← 可抛（mark 目标缺失/异常）
out_dir = TK.archive_dir(base_dir, ...)            # ← 可抛（模板/路径异常）
os.makedirs(out_dir, exist_ok=True)                # ← 可抛（OSError）
try:                                               # ← try 起点过晚
    ...
```

而 `run_batch()` 用 `list(ex.map(_one, awemes))`（原 ~254-255 行）编排：
`_one` 任一异常 ⇒ `ThreadPoolExecutor.map` 的 Future 在 `list()` 物化时抛出
⇒ **整批中断**，其余作品全部不下、调用方收到异常。

**RED 实测已复现**（见下 §三.1 反证日志尾行）：
`RuntimeError: 模拟取址失败` 从 `run_task:127 → ex.map → list(...)` 穿透 `run_batch`。

## 二、修法（只改 `downloader/downloader.py`）

把「每个作品的**完整处理**（含 `extract_media` 与全部前置准备）」整体纳入
同一 `try/except`（`mgr.add` 幂等保证 + `extract_media` + `mark(RUNNING)` +
`archive_dir` + `makedirs` 全部进入 try）：

```python
try:
    if mgr.get(task.task_id) is None:
        mgr.add(task)
    media = MR.extract_media(aweme)
    task.media_type = media["type"]
    task.quality = quality
    mgr.mark(task.task_id, status=TK.RUNNING, media_type=media["type"])

    out_dir = TK.archive_dir(...)
    os.makedirs(out_dir, exist_ok=True)
    files = []
    ...
except Exception as e:   # 既有 except 分支**原样保留**
    is_cancel = "已取消" in str(e)
    mgr.mark(task.task_id,
             status=TK.CANCELED if is_cancel else TK.FAILED,
             error="" if is_cancel else f"{type(e).__name__}: {e}")
    logger.warning(f"[DL-003] " + f"下载{'取消' if is_cancel else '失败'} {task.aweme_id}: {e}")
    return task
```

- 单作品异常 ⇒ 该任务 `FAILED` + 写 `error` + `run_task` 正常返回 ⇒ 其余作品不受影响；
- **取消语义零回归**：`"已取消"` ⇒ `CANCELED` 且 `error=""`（原逻辑未动）；
- 无新增函数/错误码前缀（复用既有 `[DL-003]`）。

`git diff` 仅命中 `DYAutoDM_v2/backend/downloader/downloader.py`（改动=1 处块移动）：
```
 M DYAutoDM_v2/backend/downloader/downloader.py
?? DYAutoDM_v2/backend/test_downloader_batch_skip.py
?? DYAutoDM_v2/artifacts/fix_a_20260928/
```

## 三、负控红/绿证据（真实命令 + 输出）

统一跑法（离线，见 §四）：
```
cd backend
DY_APP_ROOT=%LOCALAPPDATA%/Temp/dl_a3_root <python314> -m unittest test_downloader_batch_skip -v
```

### 1. 负控 RED（修复前，未改 downloader.py）

`test_downloader_batch_skip.py` 先于修复写入并运行，**6 用例 3 error**，
error 位置精确落在「try 之前」的三处注入点：

```
ERROR: test_single_extract_failure_does_not_abort_batch
  ...downloader.py", line 255, in run_batch
    list(ex.map(_one, awemes))
  ...downloader.py", line 248, in _one
    run_task(t, aweme, ...)
  ...downloader.py", line 127, in run_task
    media = MR.extract_media(aweme)
RuntimeError: 模拟取址失败：media 无法解析

ERROR: test_prep_stage_failure_does_not_abort_batch   → OSError: 模拟归档目录准备失败
ERROR: test_run_task_returns_task_on_prep_failure     → RuntimeError: 模拟取址失败：media 无法解析

Ran 6 tests in 0.116s
FAILED (errors=3)
```
完整日志：`artifacts/fix_a_20260928/A3_red_before_fix.log`（退出码 1）。

### 2. 负控 GREEN（修复后）

```
test_cancel_semantics_preserved ... ok
test_download_stage_failure_isolated ... ok
test_no_injection_all_success ... ok
test_prep_stage_failure_does_not_abort_batch ... ok
test_run_task_returns_task_on_prep_failure ... ok
test_single_extract_failure_does_not_abort_batch ... ok

Ran 6 tests in 0.060s
OK
```
完整日志：`artifacts/fix_a_20260928/A3_green_after_fix.log`（退出码 0）。

### 3. 负控计数（5 作品注入 1 个必失败）

含 1 个 `extract_media` 必失败样本，`run_batch` **不抛**，`ok=总数-1`、`failed=1`：

```
{"total": 5, "ok": 4, "failed": 1, "statuses": ["completed","completed","completed","completed","failed"]}
```
（原样落盘：`artifacts/fix_a_20260928/A3_batch_counts.txt`）

---

## 四、测试设计（`backend/test_downloader_batch_skip.py`，新增）

覆盖 6 个用例：

| 用例 | 判据 |
|---|---|
| `test_single_extract_failure_does_not_abort_batch` | 4 作品注入 1 个 `extract_media` 必失败 ⇒ total=4/ok=3/failed=1、调用方不抛、失败任务标 FAILED 且 error 非空 |
| `test_prep_stage_failure_does_not_abort_batch` | `archive_dir` 必失败 ⇒ 同上（证明覆盖的是**完整前置准备**，不止 extract_media） |
| `test_no_injection_all_success` | 还原注入（无失败样本）⇒ 全绿 ok==total，且文件真落盘 |
| `test_download_stage_failure_isolated` | 下载阶段失败（旧 try 已覆盖）⇒ 单作品隔离、零回归 |
| `test_cancel_semantics_preserved` | 取消 ⇒ CANCELED 且 error=="" （既有语义不变） |
| `test_run_task_returns_task_on_prep_failure` | 直接调用 `run_task`：前置准备失败不抛、返回 task、标 FAILED |

**离线保证**：monkeypatch `media_request.extract_media` 与 `downloader.download_file`，
URL 用 `dl.invalid`，fake 下载只写 1 字节本地文件，**全程零网络**。
隔离：模块级建 `DY_APP_ROOT` 临时根（已存在目录，防回落仓库 `data/`），
且每个用例替换进程级单例 `TK._manager` + 独立 `base_dir`，互不干扰（与
`test_uid_sink_ext.py` 同经验）。

---

## 五、核验清单

- ✅ 只改 `downloader/downloader.py`；新增 `test_downloader_batch_skip.py`（越界文件=0，`git status` 佐证）
- ✅ 负控红（修复前 3 error，落点=try 之前）→ 绿（修复后 6 OK）
- ✅ `py_compile downloader/downloader.py test_downloader_batch_skip.py` = COMPILE_OK
- ✅ 重复运行稳定 OK（连跑 2 次均 `OK`）
- ✅ 无 git add / commit / checkout / stash；未改版本文件；未跑全量测试
- ⚠️ 未核验（诚实声明）：
  - **未跑全量测试套件**（铁律禁止），仅本模块 6 用例；
  - 未对**真实抖音 aweme** 跑端到端（离线，符合任务要求）；
  - 未核验该错误码 `[DL-003]` 是否登记于 `errcode_data.py`（`grep "DL-" backend/errcode_data.py` = 0 命中，但这属既有状态、非本次改动引入，未扩展处理）。
