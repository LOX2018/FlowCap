# 任务D 报告

## 0. 只读声明

- 实际改动文件（仅清单内 2 个）：
  1. `DYAutoDM_v2\backend\auto_dm\dom_locator.py`（D-1 / D-2 / D-3 修复）
  2. `DYAutoDM_v2\backend\test_dom_locator_gate.py`（追加 D-1/D-2/D-3 门禁用例）
- **未做任何 git 写操作**（无 add/commit/reset/checkout/stash/clean；仅用 `git status` 只读查看）。
- **未改版本号**（package.json / tauri.conf.json / Cargo.toml / Cargo.lock / _build_version.py 一律未动）。
- `login_remote.py` **只读**：仅 grep/read，未改一个字符（见 D-3 选型）。
- 未启动浏览器/BCC/守护/uvicorn/打包；未触碰真实数据根；单测隔离根用 `DY_APP_ROOT=$(mktemp -d)` + `DY_DOM_ADAPTIVE_STORE`；未发起任何对抖音的主动查询。

## 1. 结论汇总表

| # | 位置(文件:行) | 判定(真缺陷/误报) | 修法 | 修复前证据 | 修复后证据 |
|---|---|---|---|---|---|
| D-1 | `dom_locator.py:137`（改后 129/140/149 + 303）| **真缺陷**（比 brief 描述更严重：不止 `_text_hit`，策略②预筛也漏） | 新增 `_norm_ws()` 用 `''.join(s.split())` 归一**全部** Unicode 空白；`exact` 比较与策略②预筛都改用它 | `_text_hit('登\u00a0录',…)` → `False`；旧公式 `'登\u00a0录'.replace(' ','')=='登录'` → `False`；端到端 NBSP 标签 `locate(tab_scan)` 全部策略失败 | 纯函数 NBSP/全角空格命中 `True`；端到端 `r.ok=True`；负控（还原旧实现）`_text_hit` → `False` |
| D-2 | `dom_locator.py:278`（改后 153/316）| **真缺陷**（低危但确实炸） | 新增 `_css_attr_value()` 转义 `"` / `\` 与控制字符（十六进制 CSS 转义）；域策略拼接前先转义 | `_collect` 产出 `domain ERR SelectorSyntaxError("input[placeholder*=\"a\"b\"]")` | 域策略无 `SelectorSyntaxError`，`domain: ok`；负控（旧裸插值）仍抛 `SelectorSyntaxError` |
| D-3 | `dom_locator.py:351/373`（改后 389/421）+ `login_remote.py:85-97` | **真缺陷（漂移）**，选① | **保留 `click_adaptive`** 并把它作为唯一真鼠标点击实现；把其时序**对齐** login_remote 实测值，使 login_remote 改调后点击行为零变化。附防漂移门禁 | `login_remote._mouse_click_locator` sleep=(0.12,0.06)，`dom_locator.click_adaptive` sleep=()（不一致）；全仓 `click_adaptive` 仅自身文件出现 1 次（无人调用） | 两函数 sleep 序列 `[0.12, 0.06]` 相等；同一 FakePage 上真鼠标事件序列**逐条一致** `[('move',60,40),('down',),('up',)]` |

## 2. 逐条详述

### D-1（OCR[13] · MED · 兜底静默失效）—— 真缺陷，且范围比 brief 更大

**代码原文（修复前 `dom_locator.py:136-138`）：**
```python
    if t.text_mode == "exact":
        return any(own.replace(" ", "") == w.replace(" ", "") for w in t.want_text)
```

**为何是问题**：`replace(" ", "")` 只去 **ASCII 空格 U+0020**。登录页标签常含 NBSP(U+00A0)、全角空格(U+3000) 等 Unicode 空白（如 `登\u00a0录`），`exact` 模式下视觉正确标签被拒 ⇒ **兜底静默 miss**，正是本模块存在的目的却失效。

**改法（新增独立归一函数 + 两处调用）：**
```python
def _norm_ws(s: str) -> str:
    """归一**全部** Unicode 空白（含 NBSP U+00A0、全角空格 U+3000、制表/换行等）。...
    `str.split()` 按任意 Unicode 空白切分，`"".join(...)` 即「去除全部空白」，
    比正则 `\s` 更全（`\s` 在部分引擎不含 NBSP）。"""
    return "".join(s.split())

def _text_hit(own: str, attrs: dict, t: Target) -> bool:
    ...
    if t.text_mode == "exact":
        return any(_norm_ws(own) == _norm_ws(w) for w in t.want_text)
```

> **实测追加发现**：只改 `_text_hit` 仍不够。策略②（自身文本锚点）的**预筛**同样按原始字符串子串匹配：
> ```python
>     own = (el.text or "").strip()
>     if own and any(w in own for w in t.want_text):   # 旧：NBSP 标签连候选都进不来
> ```
> `登\u00a0录` 含 NBSP 时 `'登录' in '登\u00a0录'` 为 `False`，**根本进不到 `validate()`** ⇒ 端到端仍 miss（首次跑门禁 `test_d1_nbsp_exact_hits` 正是卡在这里 FAIL）。故预筛一并改：
> ```python
>     if own and any(_norm_ws(w) in _norm_ws(own) for w in t.want_text):
> ```
> 策略②预筛后仍走 `validate()→_in_scope` 的 scope 判据，**不影响** G2/G3 的判别力（8 项门禁含 G1/G2/G3 全绿）。

**负控如何变红/转绿**：
- 变绿：`_text_hit('扫\u00a0码登录', {}, t)` / `_text_hit('扫\u3000码 登录', {}, t)` → `True`；端到端 `locate(nbsp_html, 'tab_scan').ok=True`。
- 变红：`test_d1_negative_control_old_replace_fails` 断言旧公式 `own.replace(" ","")==w.replace(" ","")` 对 NBSP 返回 `False`；另用 monkeypatch 把 `_norm_ws` 还原成旧 `s.replace(" ","")` 实跑，`_text_hit` 由 `True` 变 `False`（见 §3）。

### D-2（OCR[14] · LOW）—— 真缺陷

**代码原文（修复前 `dom_locator.py:278`）：**
```python
                        for c in _as_list(sel.css(f'{tag}[{k}*="{w}"]')):
```

**为何是问题**：`w`（want_text）直接插值进 CSS 字符串字面量；一旦含 `"` 或 `\`，拼出 `input[placeholder*="a"b"]` 这种**语法非法**选择器，`sel.css()` 抛 `SelectorSyntaxError`，域策略被 try/except 静默吞掉 ⇒ 未来 want_text 含引号时域策略静默失效。

**改法（新增安全构造，惰性转义）：**
```python
def _css_attr_value(v: str) -> str:
    if not any(c == "\\" or c == '"' or ord(c) < 0x20 for c in v):
        return v  # 热路径：常见文本零开销
    out = []
    for ch in v:
        if ch == "\\": out.append("\\\\")
        elif ch == '"': out.append('\\"')
        elif ord(ch) < 0x20: out.append(f"\\{ord(ch):x} ")
        else: out.append(ch)
    return "".join(out)
...
                        for c in _as_list(sel.css(f'{tag}[{k}*="{_css_attr_value(w)}"]')):
```

**负控如何变红/转绿**：`test_d2_negative_control_raw_interp_breaks` 用旧裸选择器 `'input[placeholder*="a"b"]'` 调 `sel.css()`，断言必须抛 `SelectorSyntaxError`（证明原缺陷真实）；修复后 `test_d2_quote_in_want_text_no_crash` 断言 `_collect` 的 attempts 中不含 `SelectorSyntaxError`（`domain: ok`）。

### D-3（OCR[15] · LOW）—— 真缺陷（双路径漂移），**选①「保留并收敛」**

**事实核对（只读 grep）**：`click_adaptive` 仅在本文件出现（`__all__` 导出 + 定义），**全仓无调用点**；`login_remote.py` 另写 `_mouse_click_locator`（`:85-97`），两处 sleep 不一致：
- `login_remote._mouse_click_locator`：`move → sleep(0.12) → down → sleep(0.06) → up`
- 改前 `dom_locator.click_adaptive`：`move → down → up`（**无 sleep**）

**选型与理由：选①（保留 `click_adaptive`，不回退到②移除导出）**
1. brief ②「从 `__all__` 移除」等于**放弃**一处能力，且 `click_adaptive` 是「定位 + 真鼠标点击」的完整闭环，正是 login_remote 需要的；删掉后 login_remote 的 `_mouse_click_locator` 成为唯一实现，漂移根源仍在。
2. ①让「单一实现」成为可能——把 login_remote 的 `_mouse_click_locator` 调用改为本模块 `click_adaptive`，漂移即消除。但 login_remote.py 不在本任务可写清单内，**此处不改**，仅给出应改的那一行 + 建议（下方）。
3. 为让①「行为零变化」，本模块把时序**对齐**到 login_remote 实测值（`0.12/0.06`）。若只保留旧的无 sleep 版本，login_remote 一旦改调，点击行为就会变——那就不是「消除漂移」而是「换一种漂移」。

**本模块改动**：`click_adaptive` 补回 `asyncio.sleep(0.12)` / `asyncio.sleep(0.06)`（并 `import asyncio`），时序与 `login_remote._mouse_click_locator` 完全一致。

**只读给出的「应改动的那一行 + 建议改法」（login_remote.py，本任务不改）**

改哪一行：`login_remote.py:85-97` 函数体改用本模块实现（**保持函数名与调用点不变，零改动面**）：

```python
# 现状（login_remote.py:85-97）
async def _mouse_click_locator(page, loc) -> bool:
    """用**真实鼠标**点某个 locator（沿用项目惯例；JS .click() 在 Semi Design 不可靠）。"""
    try:
        box = await loc.bounding_box()
        if not box:
            return False
        await page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        await asyncio.sleep(0.12)
        await page.mouse.down(); await asyncio.sleep(0.06); await page.mouse.up()
        return True
    except Exception as e:  # noqa: BLE001
        logger.debug("[login_remote] 真实鼠标点击失败: {}", e)
        return False

# 建议改法（①）：保留本函数作为「接受任意 locator（非仅 key）」的薄封装，
# 精确定位走稳定锚点、其鼠标事件交给 dom_locator 的唯一实现：
async def _mouse_click_locator(page, loc) -> bool:
    return await _domloc.click_on_locator(page, loc)   # 需在 dom_locator 暴露该低层函数
```

> 注：调用点 `login_remote.py:606` / `:731` 给的是 `page.locator(xp).first`（**非 key**），而 `click_adaptive(page, key)` 接收的是 **key**。故要让 login_remote 真正复用「同一个鼠标点击实现」，需在 `dom_locator.py` 暴露一个**低层** `click_on_locator(page, loc)`（内容即现 `click_adaptive` 的 356-369 行：取 box→move→0.12→down→0.06→up），`click_adaptive` 内部改为 先 `locate_xpath` 再调 `click_on_locator`；login_remote 改为调 `click_on_locator(page, page.locator(xp).first)`。
> **该少量重构（导出 `click_on_locator`）本任务未做**：①任务只授权改 `dom_locator.py`，但 login_remote 的**调用点**也须同步改，而 login_remote 不可改；只导出新函数、不改调用点无法收敛漂移，反而多一个无调用点导出（重蹈 D-3 覆辙）。故仅把时序对齐 + 在报告给出精确改法，待父会话统一处理 login_remote 时一并落地。**本模块已就绪、行为等价**（下方假页实测）。

**负控/防漂移门禁**：`test_d3_click_timing_matches_login_remote` 用 `inspect.getsource` 提取两函数的 `asyncio.sleep(...)` 序列并断言相等（现为 `[0.12, 0.06]`）。一旦任一侧再改时序，门禁变红。

## 3. 验证命令与结果

解释器：`py -3.14`（3.14.6，scrapling base 包可用）。单测隔离根：`DY_APP_ROOT=$(mktemp -d)` / `DY_DOM_ADAPTIVE_STORE=<tmp>/d.sqlite`。

**① 门禁全量（修复后）**
```
$ cd DYAutoDM_v2 && py -3.14 -m unittest backend.test_dom_locator_gate -v
...
D-1 正向：标签含 NBSP/全角空格时 exact 仍命中（旧 replace 会拒）。 ... ok
D-1 负控：还原旧 replace(' ','') 实现，本用例必须变红。 ... ok
D-2 负控：还原旧裸插值必须抛 SelectorSyntaxError（证明原缺陷真实）。 ... ok
D-2：want_text 含 `"`/`\` 时域策略选择器仍语法合法（旧实现报 SelectorSyntaxError）。 ... ok
D-3：本模块 click_adaptive 与 login_remote._mouse_click_locator 的 ... ok
G1：id/class/name 全漂移后，5 个目标仍全部命中。 ... ok
G2：导航栏同名「登录」不得让 submit/phone 误命中。 ... ok
G3 负控：错域/错标签的同名元素必须被 validate() 拒绝。 ... ok

Ran 8 tests in 0.100s
OK
```

**② D-1 修复前复现 → 修复后转绿（含 monkeypatch 负控实跑）**
```
NEW impl  NBSP hit : True
OLD impl  NBSP hit : False
NEGATIVE CONTROL OK: 旧实现下 D-1 必红
```
修复前端到端（NBSP 标签）复现：`全部策略失败（拒绝返回未通过校验的元素）`；修复后：`r.ok=True`。
修复前 `_collect` 的 `('text', ...)` 预筛直接漏掉 NBSP 标签（`'登录' in '登\u00a0录'` 为 False）。

**③ D-2 修复前复现 → 修复后转绿**
```
修复前: attempts: [('adaptive', 'ok'), ('domain', 'ERR SelectorSyntaxError(\'Invalid CSS selector \\\'input[placeholder*="a"b"]\\\': ...\')')]
修复后: POST-FIX domain attempts: [('adaptive', 'ok'), ('domain', 'ok')]
        POST-FIX SelectorSyntaxError count: 0
        escaped css value: 'a\\"b'  'c\\\\d'
```

**④ D-3 行为等价（假页 harness，绕过真浏览器；沿用 brief 禁止启动浏览器约束）**
```
click_adaptive ok: True events: [('move', 60, 40), ('down',), ('up',)]
login_remote ok : True events: [('move', 60, 40), ('down',), ('up',)]
BEHAVIOR-PRESERVING: True
```
（真实 `asyncio.sleep` 在 0.12/0.06 秒上执行，事件顺序与坐标逐条一致；两函数 sleep 序列门禁断言 `[0.12,0.06]==[0.12,0.06]`。）

**⑤ 语法/导入**：`py -3.14 -m py_compile backend/auto_dm/dom_locator.py backend/test_dom_locator_gate.py` → `COMPILE OK`（`login_remote` 可正常 `import`，D-3 门禁已借此成功 import 它并读到源码）。

**⑥ 只读确认仅动清单内文件**：`git status --porcelain` 中本任务相关仅
` M DYAutoDM_v2/backend/auto_dm/dom_locator.py`
` M DYAutoDM_v2/backend/test_dom_locator_gate.py`
（其余 ` M` 条目为其他子任务/既有改动，非本任务所为；`login_remote.py` 未出现。）

## 4. 未做 / 存疑 / 需真机验证

- **D-3 的 login_remote 调用点收敛未做**（login_remote.py 只读）。已在 §2 给出精确改法：在 dom_locator 暴露低层 `click_on_locator(page, loc)`，把 `login_remote.py:606` / `:731` 两处 `await _mouse_click_locator(page, page.locator(xp).first)` 改调之；本模块时序已对齐，可**行为不变**地接入。此项需父会话在可写 login_remote 时一并落地。
- **D-1「含 NBSP 的真实登录页」为静态/DOM 构造验证**：端到端用的是与 BASE 同构、仅标签内插入 NBSP/全角空格的 HTML 快照；真实抖音登录页当前是否真用 NBSP 标签**需真机快照**才能坐实（本任务禁启浏览器，未取）。修复本身对「含 Unicode 空白标签」是一般化增强，不依赖该假设。
- **D-2 是「未来防御」而非现网触发**：现有 `LOGIN_TARGETS` 的 want_text 均不含 `"`/`\`，故当前无现网故障；修复为防未来 want_text 演进。已用合成目标 + 负控证明原缺陷真实、修复有效。
- **`str.split()` 归一语义**：会同时去掉纯 ASCII 空格、NBSP、全角空格、制表、换行等；对「exact 全等」语义是收紧到「忽略所有空白」，符合模块注释「忽略空白的全等，防短词误收」的原意，无副作用（策略②预筛后仍过 scope 判据）。
- **未做实机点击验证**：D-3 用假 page/mouse 验证事件序列与坐标，未在真实 Camoufox/Playwright 页面点过（禁启浏览器）；真机上「真 mouse 事件」仍受页面坐标/滚动影响，属既有调用方职责，本模块不改该逻辑。
- **`git status` 显示的 `?? DYAutoDM_v2/backend/data/`、`?? artifacts/...` 等未跟踪项非本任务创建**（未验证其来源，仅只读列出，未触碰）。
