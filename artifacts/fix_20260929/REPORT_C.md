# 任务C 报告 · 后端 连麦（linkmic.py）3 条

## 0. 只读声明

- 我只改了**授权文件**：`C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2\backend\api\linkmic.py`（唯一改动文件，`git status` 仅 `M .../api/linkmic.py`，+25/-4）。
- **未做任何 git 写操作**（无 add/commit/reset/checkout/stash/clean）。
- **未改版本号**（package.json / tauri.conf.json / Cargo.toml / Cargo.lock / _build_version.py 均未触碰）。
- 未启动浏览器/BCC/守护/uvicorn/打包；所有 Python 以 `py -3.14` 运行，测试用隔离根（`DY_APP_ROOT=$(mktemp -d)` / 临时目录），未触碰真实数据根。
- 单测文件因「可写清单只有 linkmic.py」而落在**仓库外**的 scratch 目录（见 §4），未向仓库新增文件。

## 1. 结论汇总表

| # | 位置(文件:行) | 判定 | 修法(一句话) | 修复前证据 | 修复后证据 |
|---|---|---|---|---|---|
| C-1 | `backend/api/linkmic.py:125`（原）`_LINKMIC_DOM_JS` 尾 | 真缺陷（假成功） | `out.ok` 仅在四轮兜底**至少一轮**确认为 `device:`/`confirm:` 时置真，否则返回明确失败与原因 | Node DOM 桩「只点按钮」⇒ 旧脚本 `out.ok=true`；`_run_linkmic_dom` 据 `result.ok` 返回 `{"ok":True}` | 同桩新脚本 `ok=false` 且带 `error`；「确认到确定」⇒ `ok=true` |
| C-2 | `backend/api/linkmic.py:181`（原）`apply_linkmic` | 真缺陷（事件循环阻塞） | 阻塞部分 `await asyncio.to_thread(_run_linkmic_dom, ...)` 下放线程，`_run_linkmic_dom` 由假异步 `async def` 改回同步 `def` | 内联 `await` ⇒ 并发「快请求」被拖到 **0.801s**（=慢任务时长） | `to_thread` ⇒ 快请求 **0.001s** 即被服务，慢任务仍在 0.801s 完成 |
| C-3 | `backend/api/linkmic.py:131`（原）形参 `link_type` | 真缺陷（死参数） | 删除 `_run_linkmic_dom` 的死形参 `link_type: str`，同步更新唯一调用处 | 签名含 `link_type` 且函数体无引用；全仓无仓外调用方 | 签名 `(account, room_url, raw_js=None)`；`link_type` 仍在 `ApplyBody` 供 **API 降级路径** 使用 |

## 2. 逐条详述

### C-1（OCR[20] · MED · 假成功 → 已修）

**代码原文（修复前，`_LINKMIC_DOM_JS` 尾）：**
```js
  for (let k = 0; k < 4; k++) {
    out.steps['dialog_round_' + k] = (() => {
      ... // 选设备 / 点确定，否则 return 'none'
    })();
    await sleep(1500);
  }
  out.ok = true;      // ← 无条件置真
  return out;
```

**为什么是问题：** 脚本无条件 `out.ok = true`。`_run_linkmic_dom` 以 `result.get("ok")` 判成功并返回 `{"ok": True, "via": "dom"}`。当「选设备→点确定」四轮兜底**全部 `none`**（对话框没弹出 / 结构变化 / 未确认）时，端点仍回报**申请成功** —— 即本项目最高频的「假成功」。

**改成什么（现 `linkmic.py:126-135`）：**
```js
  // ③ 只有**确实确认到设备/点了确定**才算成功；四轮全 none 视为失败（禁假成功）
  const rounds = Object.keys(out.steps)
    .filter(k => k.indexOf('dialog_round_') === 0)
    .map(k => out.steps[k]);
  out.confirmed = rounds.some(s => /^(device:|confirm:)/.test(s || ''));
  out.ok = out.confirmed;
  if (!out.confirmed) {
    out.error = '已点击「申请连线」但四轮均未选中麦克风设备/未点确定'
      + '（可能未弹出对话框，或页面结构变化）——申请未确认';
  }
```
`_run_linkmic_dom`（未改）在 `result.ok=false` 时走 `error` 分支返回 `{"ok": False, ...}`，端点随之 `ok=False` —— 失败可见。

**负控如何变红/转绿（Node 最小 DOM 桩，见 §3）：**
- 负控（修复前 `_LINKMIC_DOM_JS` + 「只点按钮」桩）：`{"steps":{"clicked":true,"dialog_round_0..3":"none"},"ok":true}` ⇒ **假成功复现**（旧实现确实会红）。
- 修复后同桩：`{"confirmed":false,"ok":false,"error":"…申请未确认"}` ⇒ **转绿**。
- 对照组「确认到确定」：新旧均 `ok:true`（修复未误伤真成功）。

### C-2（OCR[7] · MED · 事件循环阻塞 → 已修）

**代码原文（修复前）：**
```python
async def _run_linkmic_dom(account: str, room_url: str, link_type: str,
                           raw_js: str | None = None) -> dict:
    ...
    from dy_apis.login_api import _bcc_post, _bcc_alive
    if not _bcc_alive(account):          # 同步 requests.get
        ...
    res = _bcc_post(account, "/linkmic_run", {...}, timeout=120)   # 同步 requests.post, timeout=120
```
调用处：`dom = await _run_linkmic_dom(body.account, room_url, body.link_type, body.raw_js)`

**为什么是问题：** `apply_linkmic` 是 `async def`，但 `_run_linkmic_dom` 内部**无任何 await**（假异步），全程同步阻塞 IO：`_bcc_alive` 走 `requests.get`、`_bcc_post` 走 `requests.post timeout=120`、DOM 流程还要等按钮约 40s。`await` 它等于**冻结整个事件循环数十秒**（连 `/api/live/stream` 的 3s 轮询都排队）。

**改成什么：**
- `_run_linkmic_dom` 改回同步 `def`（去掉假 `async`），并在 docstring 明写「全程同步阻塞，调用方须 `asyncio.to_thread`」。
- 调用处：`dom = await asyncio.to_thread(_run_linkmic_dom, body.account, room_url, body.raw_js)`。返回值契约不变（仍 `dict`）。新增 `import asyncio`。

**负控如何变红/转绿（并发对照，`_run_linkmic_dom` 桩 sleep 0.8s）：**
- 修复前（内联 `await`）：时间线 `dom.start(on-loop)@0.0 → dom.end@0.801 → fast.request@0.801` ⇒ 快请求**被拖到 0.801s**（=慢任务时长）。
- 修复后（`to_thread`）：`dom.start(worker-thread)@0.001 → fast.request@0.001 → dom.end@0.801` ⇒ 快请求 **0.001s** 即被服务，不被拖住。
- 单测断言：修复后快请求延迟 `< _SLOW/2`；负控（内联）延迟 `> _SLOW/2`。

### C-3（OCR[21] · LOW · 死参数 → 已修，删参安全）

**代码原文：** `async def _run_linkmic_dom(account: str, room_url: str, link_type: str, raw_js: str | None = None)` —— 形参 `link_type` 在函数体**从未被引用**。

**为什么是问题：** 死形参误导调用方以为 DOM 路径可传连线类型（实际 DOM 流程不消费它）。

**改成什么：** 删除该形参 ⇒ `def _run_linkmic_dom(account: str, room_url: str, raw_js: str | None = None)`；调用处同步改为 `asyncio.to_thread(_run_linkmic_dom, body.account, room_url, body.raw_js)`。

**契约安全性（`grep` 证据）：**
- 全仓 `_run_linkmic_dom` 仅两处命中：定义处 + `api/linkmic.py:181`（同一文件内唯一调用处），**无仓外调用方**（测试 `test_no_other_caller_of_run_linkmic_dom` 遍历 `backend/**/*.py` 断言）。
- `ApplyBody.link_type`（`linkmic.py:56`）**保留未动** —— 它是 **API 降级路径** `DouyinAPI.linkmicApply(..., link_type=body.link_type)`（`linkmic.py:217`）的入参，删它会破坏接口直调能力。删除的只是 DOM 入口上的死形参，两条路径互不影响。

## 3. 验证命令与结果

环境：`py -3.14`（3.14.6），隔离根 `DY_APP_ROOT=$(mktemp -d)`；Node v26.7.0（跑 DOM 脚本）。

**（a）单测（10 项，含 4 项负控）**
```
$ cd <scratch>/linkmic_c && export DY_APP_ROOT="$(mktemp -d)" && py -3.14 -m unittest -v test_linkmic_task_c
test_endpoint_contract_passes_through_js_failure ... ok
test_fixed_click_only_no_confirm_is_false ... ok
test_fixed_confirm_reports_true ... ok
test_fixed_device_reports_true ... ok
test_negative_control_orig_click_only_reports_true ... ok
test_fixed_offload_keeps_fast_request_fast ... ok
test_negative_control_inline_await_blocks ... ok
test_fixed_signature_has_no_link_type ... ok
test_negative_control_orig_has_link_type ... ok
test_no_other_caller_of_run_linkmic_dom ... ok
----------------------------------------------------------------------
Ran 10 tests in 2.629s

OK
```

**（b）C-2 事件循环对照（时间线）**
```
$ py -3.14 c2_timeline2.py
[thread ] [('dom.start(worker-thread)', 0.001), ('fast.request-served', 0.001), ('dom.end(worker-thread)', 0.801), ('all.done', 0.807)]
[thread ] 快请求延迟 = 0.001s   (慢任务=0.8s)
[inline ] [('dom.start(on-loop)', 0.0), ('dom.end(on-loop)', 0.801), ('fast.request-served', 0.801), ('all.done', 0.801)]
[inline ] 快请求延迟 = 0.801s   (慢任务=0.8s)
```

**（c）C-1 DOM 脚本矩阵（旧 vs 新 × 桩场景）**
```
js_orig  clickonly => {"steps":{"clicked":true,"dialog_round_0..3":"none"},...,"ok":true}      ← 旧：假成功
js_orig  confirm   => {...,"dialog_round_0..3":"confirm:确定","ok":true}
js_fixed clickonly => {...,"confirmed":false,"ok":false,"error":"…申请未确认"}                  ← 新：如实失败
js_fixed confirm   => {...,"confirmed":true,"ok":true}                                          ← 新：真成功仍 ok
js_fixed device    => {...,"confirmed":true,"ok":true}
```

**（d）编译/导入**
```
$ py -3.14 -m py_compile backend/api/linkmic.py  → COMPILE_OK
$ py -3.14 -c "import api.linkmic"               → import OK
  sig: (account: str, room_url: str, raw_js: str | None = None) -> dict
  iscoroutinefunction(_run_linkmic_dom): False   (apply_linkmic: True)
  'asyncio.to_thread' in apply_linkmic: True
```

**（e）改动范围**
```
$ git status --porcelain
 M DYAutoDM_v2/backend/api/linkmic.py
$ git diff --stat backend/api/linkmic.py
 1 file changed, 25 insertions(+), 4 deletions(-)
```

## 4. 未做 / 存疑 / 需真机验证（诚实标注）

- **实测的**：C-1（Node DOM 桩 + Python 端点契约）、C-2（并发时间线对照）、C-3（签名 + 全仓调用方 `grep` 遍历）均为**运行期实测**，含负控变红/转绿。
- **未实测（仅静态/契约判断）**：
  - C-1 的 DOM 桩是**最小模拟**（`querySelectorAll` 返回固定元素），未在真实抖音直播间页验证「申请连线 → 选麦克风 → 确定」真实 DOM 的选择器命中率；真实页面结构变化导致 `none` 时，新逻辑会如实报失败（这正是设计意图），但**真实成功路径的端到端**需真机。
  - C-2 只证明了**事件循环不再被冻**（桩 sleep 模拟阻塞）；未跑真实 BCC `/linkmic_run`（禁止启动 BCC/浏览器）。真实 `requests`/DOM 40s 场景行为等价（同样被 `to_thread` 隔离），但**端到端时延**待真机。
- **设计取舍**：C-3 选择「删死参数」而非保留 —— 依据是全仓无仓外调用方（已遍历取证），且 `link_type` 仍保留在 `ApplyBody` 供 API 路径，删除 DOM 入口形参不破坏任何契约。
- **测试文件位置**：TASK_C 可写清单**仅 `linkmic.py`**，故未向仓库新增测试文件；10 项单测与 C-2 时间线脚本落在 scratch 目录（`…/profiles/lox/cache/scratch/linkmic_c/`）。若父会话希望长期回归覆盖，建议把 `test_linkmic_task_c.py` 正式落到 `backend/test_linkmic_dom_honesty.py`（需父会话授权/纳入提交）。
