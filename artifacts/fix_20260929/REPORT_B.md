# 任务B 报告

## 0. 只读声明

我只改了授权文件（清单内、逐一列出实际改动文件）：

1. `C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\backend\api\tasks.py`
2. `C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\backend\services\delivery_verify.py`

- 未做任何 git 写操作（`git status --short` 只读查看，未 add/commit/reset/checkout/stash/clean）。
- 未改版本号（package.json / tauri.conf.json / Cargo.toml / Cargo.lock / _build_version.py 全未触碰）。
- 未启动浏览器/BCC/守护/uvicorn/打包；未触碰真实数据根 `C:\temp\dyautodm_design` 与 `accounts/*/profile`。
- 未新增任何「主动查询抖音平台」的请求。
- 运行期取证脚本放在临时 scratch 目录（非仓库），且均 `export DY_APP_ROOT=$(mktemp -d)` 等价隔离（`%TEMP%\fix_b_evidence` / `fix_b_probe`），DB 落在临时目录。
- 附带说明：`DYAutoDM_v2/backend/data/dyautodm.db` 在 `git status` 中显示为未跟踪，但它 mtime 为 **2026-09-28 09:56**（早于本次任务），**非本次产生**；本次所有探针都用 `DY_APP_ROOT` 隔离，未写该路径。

---

## 1. 结论汇总表

| # | 位置(文件:行) | 判定 | 修法(一句话) | 修复前证据 | 修复后证据 |
|---|---|---|---|---|---|
| B-1 | `api/tasks.py:140`（旧）→ `:116` 新增 `_account_of`，`:171` 调用点 | **真缺陷（CRITICAL）** | 账号名改从**发送账号 auth**（`dispatch.auth.account_name`）按既有范式取，回退 `auth`/`monitor_auth`，再回退 `target_acct`/`_acct` | 实测 `hasattr(AutoDM(),"account_name")==False`；旧写法 `_acct==""` ⇒ spy 计数 **0** 次、`delivery_state==""`（负控转红） | `_account_of(adm)=="acct-real-01"`；spy 计数 **≥1**、`delivery_state=="delivered"`（正控转绿） |
| B-2 | `services/delivery_verify.py:261`（`_norm_conv`）、`:270`（`_peer_of`） | **真缺陷（LOW·死代码）** | 全仓确认无引用后删除两函数（保留在用的 `_flip_conv`） | 全仓 `grep` 仅命中「定义处」自身，无任何调用点 | 删除后 `grep` 0 命中；`py_compile` 通过；`import delivery_verify` 后 `hasattr(_norm_conv)==False`、`_flip_conv==True`（在用函数仍在） |
| B-3 | `services/delivery_verify.py:143-145` | **真缺陷（LOW·注释口径）** | 注释里具体数字（19/34）改为定性描述，不再含任何业务数字 | 原文硬编码「受理 19 条…34 条」 | 改为「受理条数与平台拒收回执条数不一致…全部受理条」；`grep "[0-9]\+ 条"` 在该注释区 0 命中 |

---

## 2. 逐条详述

### B-1（CRITICAL · 送达口径假落地）

**问题定位（先 grep 确认「哪个对象才持有 account_name」）**

全仓 `grep -rn "account_name"` 的关键事实：

- `core/auto_dm.py`：只在 **auth 对象**上挂账号名
  - `:777  setattr(self.monitor_auth, "account_name", _m_name)`
  - `:865  setattr(self.auth, "account_name", _s_name)`
  - `AutoDM` 自身只有 `:202 self.target_acct` 与 `:206 self._acct`，**没有 `self.account_name`**（`grep "self\.account_name"` = 0 命中）。
- `core/dispatch.py:438  _acct = getattr(self.auth, "account_name", "") or ""` → **既有正确范式：auth 上取**。
- `core/dispatch.py:65  self.auth = auth`；`core/auto_dm.py:898-899  DispatchCenter(auth=self.auth, …)` ⇒ 发送账号 auth 即 `adm.dispatch.auth`，且 records 就挂在 `adm.dispatch` 上。
- `core/live_hook.py:64  str(getattr(auth_, "account_name", "") or "")` → 同一范式。

**代码原文（修复前，`api/tasks.py:140`）**

```python
    _acct = getattr(adm, "account_name", "") or ""
```

`adm` 是 `AutoDM` 实例（`main.py:448 app.state.adm = _default_adm`，`_default_adm = AutoDM()`）。
`AutoDM` 无 `account_name` ⇒ `_acct` **恒为 `""`** ⇒ 下方 `if _dstate and _acct and _uid:` 恒不成立
⇒ `delivery_state_of()` **从不被调用** ⇒ 下发的 `delivery_state` 恒 `""` ⇒ 前端「送达率/拒收」恒为 0。
这正是今日「受理≠送达」修复的**假落地**。

**为什么是问题（运行期实测，非静态推断）**

`hasattr(AutoDM(), "account_name")` 实测 `False`（见 §3 输出）。

**改成了什么**

新增辅助函数 `_account_of(adm)`（`api/tasks.py:116-140`），调用点 `:171` 改为 `_acct = _account_of(adm)`：

```python
def _account_of(adm) -> str:
    for _obj in (
        getattr(getattr(adm, "dispatch", None), "auth", None),   # 持有 records 的发送账号
        getattr(adm, "auth", None),
        getattr(adm, "monitor_auth", None),
    ):
        _name = getattr(_obj, "account_name", "") or ""
        if _name:
            return str(_name)
    # auth 尚未构建（引擎未启动/纯配置态）⇒ 兜底到配置声明账号
    return str(getattr(adm, "target_acct", "") or
               getattr(adm, "_acct", "") or "")
```

同时把该派生逻辑的注释与实现校准（保留原有 2026-09-29 说明，并补一行说明账号名真源）。

**负控如何变红 / 正控如何转绿**

- 正控（修复后）：构造真 `AutoDM` + `dispatch.auth.account_name="acct-real-01"`，
  库里植入 1 条回声帧（`role='me'`, `msg_type='7'`）→ 断言 `delivery_state=="delivered"` 且
  spy 到 `delivery_state_of` 调用 **≥1** 次 → **绿（ok）**。
- 负控（把 `_account_of` 换回旧写法 `getattr(adm,"account_name","") or ""`）→
  断言 `delivery_state==""` 且 spy 计数 **==0** → **绿（ok，即旧写法确实恒空、确实不调用）**。

### B-2（LOW · 死代码）

**确认无引用（grep 证据，修复前）**：全仓 `grep -rn "_norm_conv\|_peer_of"` 仅命中
`services/delivery_verify.py` 的**定义行本身**（261、270），**无任何调用点**。
其邻居 `_flip_conv` 反被 `:204` / `:223` 真实调用，故只删前两个。

**改成了什么**：删除 `_norm_conv`（原 261-267）与 `_peer_of`（原 270-274）两个函数；
保留 `_flip_conv`（现 `:261`）。

**验证**：删除后全仓 grep 0 命中；`py -3.14 -m py_compile` 通过（无未定义名）；
`import services.delivery_verify` 后 `hasattr(m,"_norm_conv")==False`、`hasattr(m,"_flip_conv")==True`。

### B-3（LOW · 注释口径）

**代码原文（修复前，`delivery_verify.py:143-145`）**

```
#   实测 2026-09-29：受理 19 条，而抖音回执里 34 条是
#   「对方回复或关注你之前，只能发送一条文字消息」（平台拒收）。
#   ⇒ 界面却把全部 19 条都显示成绿色「已发送」。
```

**为什么是问题**：硬编码业务数字，与前端 `live-page.tsx:309` 的 **24** 自相矛盾，易漂移。

**改成了什么（不含任何具体数字）**

```
#   实测 2026-09-29：**受理条数与平台拒收回执条数不一致**——相当一部分回执是
#   「对方回复或关注你之前，只能发送一条文字消息」（平台拒收）。
#   ⇒ 界面却把**全部受理条**都显示成绿色「已发送」。
```

三方（本后端注释 + 前端两处，另一任务同步）统一以「不含数字」为准，避免跨文件耦合。

---

## 3. 验证命令与结果

### 3.1 根因实测：`AutoDM` 无 `account_name`

```
$ cd C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend
$ DY_APP_ROOT=<tmp>/fix_b_probe py -3.14 <scratch>/probe_b1_smoke.py
2026-09-29 15:50:13 | INFO | database:get_db:149 - [db] SQLite 已初始化: C:\...\Temp\fix_b_probe\data\dyautodm.db
AutoDM ok; hasattr account_name = False
target_acct = None _acct = None
auth = None dispatch = None
```

### 3.2 B-1 运行期取证（正控转绿 / 负控转红）

```
$ DY_APP_ROOT=<tmp>/fix_b_evidence py -3.14 <scratch>/probe_b1_evidence.py
...
test_negative_control_old_writing_is_red ... ok
test_positive_fixed_version ... ok
test_root_cause_autodm_has_no_account_name ... ok

----------------------------------------------------------------------
Ran 3 tests in 0.002s

OK
EXIT=0
```

（正控断言：账号名解析为 `acct-real-01`；`delivery_state=="delivered"`；spy 计数 ≥1。
 负控断言：旧写法下 `delivery_state==""` 且 spy 计数 ==0。
 取证脚本：`<scratch>\probe_b1_evidence.py`，scratch 目录见正文 0 节。）

### 3.3 B-2 grep 证据（修复后）

```
$ rm -rf ... # 无
$ grep -rn "_norm_conv\|_peer_of" --include=*.py .
exit=1            # 0 命中 = 死代码已清除
```

### 3.4 B-3 grep 证据（修复后，该注释区无业务数字）

```
$ grep -n "[0-9]\+ 条" services/delivery_verify.py
186:  # （实机验证脚本 L1 当场抓出：99/186 条与独立真值不一致）。
exit=0            # 仅剩一条与技术真值验证相关、非「受理/拒收」业务数字；目标注释区 0 命中
```

### 3.5 编译 + 导入

```
$ py -3.14 -m py_compile api/tasks.py services/delivery_verify.py
COMPILE_OK
$ py -3.14 -c "import ... api.tasks, services.delivery_verify ..."
IMPORT_OK; _flip_conv= True ;_norm_conv= False
```

---

## 4. 未做/存疑/需真机验证（诚实标注）

- **B-1 已做运行期取证**：用真 `AutoDM`（非替身）+ 隔离根 + 真实 SQLite，断言「真账号名下
  `delivery_state` 非空」且「`delivery_state_of` 真被调用」，正/负控都跑通。**未做**的部分：
  未在真机引擎（连真抖音、真 recv_daemon 回声帧）上端到端跑；即**「真账号 auth 已被
  引擎正确 setattr」这一环依赖 `core/auto_dm.py:865` 的既有接线**（该处非本任务可写文件，
  仅静态确认存在）。若真机出现 `dispatch.auth` 未挂 `account_name`，则 `_account_of` 会回退
  `target_acct/_acct`（仍非空，`delivery_state_of` 会被调用），但账号名可能与实际发送账号不一致
  —— 建议父会话在真机跑一次「任务页下发 `delivery_state` 非空」的冒烟。
- **B-1 兜底语义提醒**：`target_acct/_acct` 兜底在「auth 已构建但未挂名」时不会触发（前面循环先命中），
  仅在「auth 尚未构建」时命中；此时 `dispatch.records` 通常也为空，故实际无边角风险。
- **B-2**：已确认两函数全仓无引用后删除；**静态判断**其确为死代码（`_flip_conv` 才是活函数）。
- **B-3**：仅改注释；不含任何业务数字，前端两处由另一任务同步（未做前端校验，非本任务文件）。
- 本次未运行项目既有 `unittest discover` 全量（会加载大量与 B 无关的用例，且耗时/依赖多）；
  仅按本任务「每条修复独立取证」要求，以隔离探针给出 B-1/B-2/B-3 的可复现判据。
