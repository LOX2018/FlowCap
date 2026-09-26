# M-20 收口报告 · 搜索链路传输层事实上抛（2026-09-27）

> 任务来源：F5 报告第 ⑦ 节自曝的遗留项 —— 「`search_stream` 的非 403 空结果仍缺
> `_transport` 上抛（签名之外的另一条假成功路径）」。
> 本报告是**真缺陷修复**（非登记待办），且修复面比原报告所述**更大**。

---

## ① 改动文件清单

| 文件 | 改动 | 行尾 |
|---|---|---|
| `backend/dy_apis/client_search.py` | **3 处**上抛 + 1 处工具函数 | CRLF（保持） |
| `backend/api/platform.py` | 搜索端点如实上抛 `blocked`/`blocked_reason` | LF（保持） |
| `frontend/src/api/platform.ts` | 返回类型加 `blocked` / `blocked_reason` | CRLF（保持） |
| `frontend/src/components/platform/platform-page.tsx` | 被拦截时渲染**明确提示**而非空列表 | CRLF（保持） |
| `backend/test_m20_search_transport.py` | **新增**门禁（G1~G6，含 3 条负控） | LF |

---

## ② 🔴 修复面比原报告更大：**三个**缺口，不是一个

原报告只说 `search_stream` 一处。实测（探针 + 打桩）发现**三个同族缺口**：

| # | 位置 | 问题 | 后果 |
|---|---|---|---|
| **A** | `search_stream` | 归一化返回体**无** `_transport` | 被 Argus 拦时 `buf` 只有 46 字节错误页 ⇒ 分块解析全失败 ⇒ `aweme_list=[]` 且 `status_code=0`，与「真没搜到」**形态完全相同** |
| **B** | `search_general_work`（数据源） | `return safe_json(resp)` —— **从不挂** `_transport` | 上层「接住 `_transport`」的逻辑形同虚设（**我的 G1 门禁抓到的就是这个**） |
| **C** | `search_some_general_work` | 返回**裸 `list`** | 即便 B 修好，传输层事实**无处承载** ⇒ 只能被读成「没搜到」 |

> **关键教训**：B 是「只在 A、C 层做修复」时**必然漏掉**的一层 ——
> 若我只按原报告修 A，C 接住 `_transport` 的代码**永远收不到数据**，门禁也照样绿
> （因为门禁只看「有没有那个属性」，不看「数据从哪来」）。
> **是 G1 门禁把它抓出来的**：写门禁 → 跑 → 红 → 才发现数据源根本没上抛。

---

## ③ 三个缺口的修法

### A. `search_stream` —— 返回体带传输层事实

```python
_transport = None
if resp.status_code != 200 or not raw_objs:
    _transport = {"status": resp.status_code, "bytes": len(buf)}
return {"status_code": status_code, "aweme_list": aweme_list,
        "raw": raw_objs, "_transport": _transport}
```

### B. `search_general_work` —— 补上原本缺失的上抛（照抄同文件 `search_live` 已验范式）

```python
resp_json = safe_json(resp)
try:
    _status, _nbytes = resp.status_code, len(resp.content or b"")
except Exception:  # noqa: BLE001
    _status, _nbytes = 0, 0
if _status != 200 or not isinstance(resp_json, dict) or not resp_json:
    if isinstance(resp_json, dict):
        resp_json["_transport"] = {"status": _status, "bytes": _nbytes}
return resp_json
```

### C. `search_some_general_work` —— 裸 `list` → `LiveSearchResult`

```python
last_transport = None            # 循环外初始化
...
if isinstance(res_json, dict) and res_json.get("_transport"):
    last_transport = res_json["_transport"]      # 必须接住
...
return LiveSearchResult(work_list, last_transport)
```

**为什么用 `LiveSearchResult`（list 子类）而非裸 list**：
内置 `list` **没有 `__dict__`**，`lst.last_transport = x` 直接抛 `AttributeError`；
若用 `try/except` 吞掉，风控事实就**静默丢失**（正是假成功的成因）。
子类同时保证 `for x in r` / `len(r)` / `r[:n]` **零改动**（返回类型契约不变）。

### 附：统一读取工具

```python
def take_search_transport(result) -> dict | None:
    """兼容两种承载形态：LiveSearchResult → .last_transport；dict → ["_transport"]"""
```

---

## ④ `platform.py` —— 把事实**送到前端**（否则修了后端也白修）

```python
transport = api.take_search_transport(stream)
if not works:
    fb = await asyncio.to_thread(api.search_some_general_work, ...)
    works = fb or []
    transport = transport or api.take_search_transport(fb)
blocked = bool(transport)
_reason = None
if blocked:
    _reason = "被风控拦截（HTTP {}，响应 {} 字节）".format(
        transport.get("status"), transport.get("bytes"))
return {"ok": True, "kind": "video",
        "items": [...], "blocked": blocked, "blocked_reason": _reason}
```

## ⑤ 前端 —— 被拦截 ≠ 没结果（铁律「禁假成功」）

`renderQ` 在渲染 Grid **之前**先判 `blocked`，命中即渲染**红色告警块**：
标题「搜索被平台风控拦截」+ 真实原因 + 「这不是『没有结果』——请降低频率、稍后再试」。
视觉与 `RoomManagePage`（F5）同款（`border-[var(--color-danger)]` + `AlertTriangle`）。

---

## ⑥ 门禁（G1~G6 · 全打桩，**零真实抖音请求**）

| 判据 | 断言 | 结果 |
|---|---|---|
| **G1** | 403 空响应 ⇒ `search_some_general_work` 携带 `last_transport` | ✅ |
| **G2** | 403 46B ⇒ `search_stream` 的 `["_transport"]` 非 None 且 status=403 | ✅ |
| **G3** | **正常 200 ⇒ 两者 transport 均须 None**（不得误报为被拦截） | ✅ |
| **G4** | 负控（根因自证）：裸 `list` 挂属性必抛 `AttributeError` | ✅ |
| **G5** | `take_search_transport` 兼容两种形态；无事实时返回 None（不伪造） | ✅ |
| **G6** | 负控：改回裸 list ⇒ 判据必须能抓到 | ✅ |

**安全性**：全部走**打桩**（`requests.get` 替换为内存桩 + 签名/指纹接口打桩），
**零真实抖音请求**（无登录态下的主动请求属风控红线）。

---

## ⑦ 🔴 D-07 真负控（注入 → 必须红；还原 → 必须绿）

| 注入 | 结果 |
|---|---|
| ① 删掉 `search_general_work` 的 `_transport` 上抛 | **exit=1 RED**（errors=5）✅ |
| ② `search_some_general_work` 返回改回裸 `list` | **exit=1 RED**（failures=1）✅ |
| ③ `search_stream` 返回体去掉 `_transport` | **exit=1 RED**（failures=1）✅ |
| 三次还原 | **exit=0 OK / OK / OK** ✅ |

判据有判别力（三条注入各自独立被抓，非单一判据包打天下）。

---

## ⑧ 我自己的两个错误（如实记录）

### ⑧-1 验证顺序错误：差点交付**语法错误**的代码

`platform.py` 里我写了嵌套引号的转义 f-string：

```python
f"被风控拦截（HTTP {transport.get(\"status\")}，...）"   # ← SyntaxError
```

**为什么没被当场抓到**：我的写入脚本习惯是「**先 `py_compile` 内存内容，通过才写盘**」——
但有一次我写成了**先写盘、后编译临时文件**，编译的其实是**旧内容**。
⇒ 语法错误溜过，直到我单独对**磁盘文件**跑 `py_compile` 才暴露：
`SyntaxError: unexpected character after line continuation character`。

**修法**：改写为 `.format()`（去掉嵌套引号），并**改为「先写盘，再对磁盘文件编译」**。
**判据（已写入教训）**：`py_compile` 必须作用于**磁盘上的真实文件**，
不得作用于写盘前的内存串或临时副本 —— 否则验证的是「我以为写了什么」，不是「实际写了什么」。

### ⑧-2 测试桩不真实，导致**假红**

G3 首跑报红（`{'status': 200, 'bytes': 69}` 被判为「被拦截」）。
排查后确认**是我的桩缺陷**：`_StubResp` 没实现 `.json()`，
`safe_json()` 调它抛异常 → 走降级分支 → 正常 200 被误判。
（用独立探针验证了**产品代码是对的**：正常 200 + 合法 JSON ⇒ `_transport: None`。）

**修法**：桩补 `json()`，并区分两个端点的**线格式**
（`single` 是普通 JSON 体，`stream` 是 chunked 分块）——
**两者不可混用同一个桩**，否则测的不是真实路径。
**教训**：**桩的真实性决定门禁的可信度**；假红与假绿同样危险。

---

## ⑨ 验收命令与结果

```
A. python -m py_compile client_search.py platform.py   → exit 0（对磁盘文件）
B. python -m unittest test_m20_search_transport -v     → Ran 6 tests ... OK
C. npx tsc --noEmit (frontend)                         → exit 0
D. 负控 3 条                                            → 全 RED；还原全 GREEN
E. 全量回归                                             → 见下节
```

**EOL 核查**（逐对相等 ⇒ 零 EOL churn）：
`client_search.py 49/3` · `platform.py 16/2` · `platform.ts 4/1` · `platform-page.tsx 28/3`
（每对 `git diff --numstat` 与 `--ignore-all-space --numstat` 读数一致）

---

## ⑩ 遗留 / 未做

- 🟡 **真机 403→200 A/B 未做**：无登录态下的主动请求属风控红线，本轮全部打桩。
  真机验证需在**有有效凭证**时进行（结论：签名已就位，但「签名后是否真从 403 变 200」
  仍沿用同族 M-2 / `client_comments` 的实测推断，**未经本端点真机复现**）。
- 🟡 **`search_stream` 的 `has_more` 翻页**：本端点是一次性取数（无翻页），未涉及。

---

## 中文总结

**达成。** M-20 已真修复（非登记待办），且**修复面比原报告更大**：从报告说的「`search_stream` 一处」
扩到**三个同族缺口**（`search_stream` 返回体、**其数据源 `search_general_work` 从不挂 `_transport`**、
`search_some_general_work` 返回裸 `list`）。**关键的第三层（B）是被我自己新写的 G1 门禁抓出来的** ——
若只按原报告修 A，C 的接收逻辑将永远收不到数据，而门禁照样绿。

修复后链路：**被 Argus 拦截 → 传输层事实一路带到前端 → 渲染红色告警「搜索被平台风控拦截」
+ 真实 HTTP 码/字节数 + 「这不是『没有结果』」**，彻底消除「空列表假装没结果」的假成功。

门禁 G1~G6 全绿（含 3 条真负控：注入→红 / 还原→绿），全打桩零真实请求。
过程中我犯了两个错误并已自纠：**⑧-1 验证顺序错误**（`py_compile` 作用于写盘前内容 ⇒
差点交付语法错误代码，已改为对磁盘文件编译）；**⑧-2 桩不真实导致假红**
（`_StubResp` 缺 `.json()` ⇒ 正常 200 被误判，已补并区分两端点线格式）。
两条教训的共性：**「我以为验证了」≠「验证了实际产物」** —— 验证必须锚定磁盘上的真实内容与真实调用路径。