# 任务E 报告

## 0. 只读声明
- 实际改动的文件（唯一）：`C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\backend\daemon\bcc_login.py`（+10 行，0 删除）。
- 未做任何 git 写操作（无 add/commit/reset/checkout/stash/clean）；仅用 `git diff` 只读查看。
- 未改版本号（package.json / tauri.conf.json / Cargo.toml / Cargo.lock / _build_version.py 均未触碰）。
- 未启动浏览器/BCC/守护/uvicorn/打包；未触真实数据根；单测用 `export DY_APP_ROOT=$(mktemp -d)` 隔离。
- 报告的复现脚本落在只读工件目录（非代码树）：`artifacts\fix_20260929\repro_e1_breaker_reset.py`（另有修复前快照 `bcc_login.before_E1.py`），运行期临时件在 scratch。

## 1. 结论汇总表
| # | 位置(文件:行) | 判定 | 修法 | 修复前证据 | 修复后证据 |
|---|---|---|---|---|---|
| E-1 | bcc_login.py:583 跳闸块（原 583-588） | 真缺陷 | 跳闸归零 `scan_fail_count = 0` | `SCEN_D_583_TRIPS:[2, 3]` | `[2, 3]`→`[2, 4]` |
| E-1 | bcc_login.py:633 跳闸块（原 633-639） | 真缺陷 | 同上 | `SCEN_C_633_TRIPS:[2, 3]` | `[2, 4]` |
| E-1 | bcc_login.py:685 漂移立即熔断块（原 685-694） | 真缺陷 | 同上 | `SCEN_B_685_TRIPS:[2, 3]` | `[2, 4]` |
| E-1 | bcc_login.py:701 通用跳闸块（原 701-707） | 真缺陷 | 同上 | `SCEN_A_701_TRIPS:[2, 3]` | `[2, 4]` |

四个熔断跳闸点（写 `breaker_until` 的每一处）在 STask 描述里只点了「约 685（漂移）与约 701（通用）」两处，但同一缺陷在 583（uid 漂移后刷新连续失败）与 633（页面重激活连续失败）两处**完全对称存在**，一并修（同文件、同一类计数器重置，不属改判定）。

## 2. 逐条详述

### E-1 熔断跳闸不重置 `scan_fail_count`
**问题代码（原样，701 通用跳闸块为例）：**
```python
                        if scan_fail_count >= SCAN_BREAKER_LIMIT:
                            breaker_until = time.time() + SCAN_BACKOFF_SEC
                            logger.error(
                                f"[BCC-024] [bcc] 探活/scan_login 连续失败 {scan_fail_count} 次，"
                                f"熔断 {SCAN_BACKOFF_SEC // 60} 分钟（防浏览器频繁重启"
                                f"引发风控）。session 疑似服务端已失效，自动登录救不回，"
                                f"请在指纹浏览器重新扫码；期间仅告警不重启浏览器")
```
**为何是问题：** `run_keepalive` 里 `scan_fail_count` 是「连续失败」计数，`SCAN_BREAKER_LIMIT=2`，`SCAN_BACKOFF_SEC=1800`。跳闸只写 `breaker_until` 却**不清零**计数 ⇒ 熔断期满恢复后，计数仍保留跳闸前的残值。
- 正常路径：count=2 时跳闸 ⇒ 期满若 count 仍=2 ⇒ **第一次**新失败 `count+=1=3>=2` **立刻再跳闸** ⇒ 根本没有「熔断期满后重新累计 2 次」的恢复窗，退化为「一失败即熔断」，恢复期过短、可能反复熔断（正是 brief 描述的 LOW 缺陷）。
- 漂移路径（685）更明显：漂移立即熔断时若前面已有 1 次真实失败（count=1）⇒ 期满后单次失败 count=2 立即再跳。与 2026-09-28 刚做的「漂移即时熔断、不再消耗重试次数」修复叠加后，这个残值让 `SCAN_BREAKER_LIMIT` 名存实亡。

**改成什么（四处跳闸块均在 `logger.error(...)` 之后补一行）：**
```python
                            scan_fail_count = 0
```
放在日志**之后**，保证 `[BCC-024] 连续失败 N 次` 仍打印真实跳闸计数 N（不打印 0）。漂移块（685）同理补一行，语义为「跳闸即重新起算」。

**负控如何变红/转绿：** 见 §3，`VERDICT:RESET_MISSING`（红）→ `VERDICT:RESET_OK`（绿）。

**修复前定位改动（只读确认）：** 未改启动/单例/租约任何判定；`_scan_exclusive_set`、`_exec`、租约 prio/TTL、`if self._switching or ...: continue` 等一律未动（见 git diff：仅新增 10 行注释+赋值）。

## 3. 验证命令与结果

复现/回归脚本（行为级，不读私有局部变量；4 个场景分别命中四处跳闸点，用日志行号自证）：
`artifacts\fix_20260929\repro_e1_breaker_reset.py`（另有修复前快照 `bcc_login.before_E1.py`）

```bash
A="C:/Users/LOX/Desktop/DYchajian/artifacts/fix_20260929"
B="C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend"
cd "$B"
export DY_APP_ROOT=$(mktemp -d)
# 修复后（对真实文件跑）
py -3.14 "$A/repro_e1_breaker_reset.py" "$B" "$B/daemon/bcc_login.py"
# 修复前（对快照文件跑，证负控变红）
py -3.14 "$A/repro_e1_breaker_reset.py" "$B" "$A/bcc_login.before_E1.py"
```

**修复前（`bcc_login.before_E1.py`）输出尾部：**
```
SCEN_A_701_TRIPS:[2, 3] SCANS:3 LINES:[703, 703]
SCEN_B_685_TRIPS:[2, 3] SCANS:3 LINES:[686, 703]
SCEN_C_633_TRIPS:[2, 3] SCANS:3 LINES:[635, 635]
SCEN_D_583_TRIPS:[2, 3] SCANS:3 LINES:[585, 585]
VERDICT:RESET_MISSING
```
**修复后输出尾部：**
```
SCEN_A_701_TRIPS:[2, 4] SCANS:4 LINES:[711, 711]
SCEN_B_685_TRIPS:[2, 4] SCANS:4 LINES:[691, 711]
SCEN_C_633_TRIPS:[2, 4] SCANS:4 LINES:[638, 638]
SCEN_D_583_TRIPS:[2, 4] SCANS:4 LINES:[585, 585]
VERDICT:RESET_OK
```
解读：第 2 拍跳闸（count 到 2）；第 3 拍把时钟推过 1800s 熔断期。修复前期满后**单次**失败（第 3 拍）即再跳（`[2,3]`）；修复后必须**再连续 2 次**失败，第 2 次落在第 4 拍（`[2,4]`）。SCANS 由 3→4 同理。四个场景 LINES 分别落在 585/638/691/711，证明四处跳闸点各自被覆盖（非同一路径）。

周期证「跳闸后需连续 2 次失败才再跳」：`[2, 4]` 即期满后第 3 拍 1 次失败不跳、第 4 拍第 2 次失败才跳。

**编译与既有回归：**
```
$ py -3.14 -c "import py_compile,...; py_compile.compile('.../bcc_login.py', doraise=True)"  → COMPILE_OK

$ py -3.14 -m unittest ...（DY_APP_ROOT 隔离）
test_bcc_startup_single_fire.py          Ran 3 tests ... OK
test_bcc_module_identity_guard.py        Ran 4 tests ... OK
test_bcc_kernel_unavailable_breaker.py   Ran 2 tests ... OK
test_bcc_duty_surface_e1.py              Ran 7 tests ... OK
$ py -3.14 -m pytest test_bcc_governance_guards.py -q
..........                                                               [100%]
10 passed in 0.19s
```
（`test_bcc_governance_guards.py` 含 G7/G8：漂移上报 drift 标志、保活循环立即熔断；证明本次改动未破坏 09-28 的漂移修复。文件为 pytest 风格函数式测试，用 unittest 跑显示 NO TESTS RAN，故改用 pytest，10 passed。）

## 4. 未做/存疑/需真机验证（诚实标注）
- **实测**：上表四个场景的复现脚本、编译、既有 BCC 测试套件，均在本机 `py -3.14` + 隔离根实跑过。
- **仅静态判断**：真实 TikTok/抖音保活心跳的端到端时序（真实 `scan_login` 慢/网络异常导致 `fut.result` 超时走 except 分支）未在真机跑（约束禁起浏览器/守护）。本次只改计数器重置，未动判定，因此对真机主要影响是「熔断恢复后需重新累计 2 次失败」这一行为，风险面是**更少**触发熔断，不新增主动平台请求（未引入任何抖音查询）。
- 复现脚本对 `time`/`asyncio` 采用**模块级替换**（`mod.time`、`mod.asyncio`）而非全局 monkeypatch；`scan_login` 用桩，不触租约/浏览器/网络。
- 未发现 E-1 为误报，故未标注误报项。
