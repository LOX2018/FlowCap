# F5 报告 —— 直播监听页「搜索关键词 → 发现直播间 → 一键上架」（ADR-018）

- 日期：2026-09-27
- 分支：`design/better-douyin`（未 commit，未 `git add`）
- 版本：v0.45.40（**未改动任何版本文件**）

---

## ① 改动文件清单

| # | 文件 | 改动性质 |
|---|------|---------|
| 1 | `backend/dy_apis/client_search.py` | **D6 签名修复**（`search_live` 改走 `signed_url`）+ 翻页守卫 + 风控事实透传 + 新增 `LiveSearchResult` / `take_live_transport` |
| 2 | `backend/api/live_rooms.py` | 新增只读端点 `POST /api/live/rooms/discover`（`DiscoverReq`、`_auth_for`、`_pick_live`、`_pick_cover`、`_first`） |
| 3 | `frontend/src/api/client.ts` | 新增 `DiscoveredRoom` 类型 + `api.discoverLiveRooms()` |
| 4 | `frontend/src/components/live/RoomManagePage.tsx` | 新增「搜索发现」区块（输入 → 列表 → 每行「上架」）+ `doSearch` / `shelve` |

**未改动**：`package.json` / `tauri.conf.json` / `Cargo.toml` / `_build_version.py`（版本红线）。

---

## ② 签名核实结论（D6）—— 原本**没走** `signed_url`，已修

### 核实过程（grep 实证）

```
$ grep -n "signed_url\|def search_live\|def search_some_live\|def search_general_work" backend/dy_apis/client_search.py
46:    def search_general_work(...)
249:    def search_live(...)
306:    def search_some_live(...)
```

`client_search.py` 全文 **0 处 `signed_url`** —— 即该文件的搜索端点**全都没签名**。
原 `search_live` 的发送侧是：

```python
# 改前（client_search.py:300-303）
params.with_a_bogus()
resp = requests.get(f'{DouyinAPI.douyin_url}{api}', headers=headers.get(), cookies=auth.cookie,
                    params=params.get(), verify=tls_verify())   # ← 未签名，必被 Argus 403
return safe_json(resp)
```

### 关键发现：现有门禁**抓不到**这个端点

`/aweme/v1/web/live/search/` **不在** `utils/secsdk_web_sign.PROTECTED_PATHS_GET`
（该清单 14 条，见 `secsdk_web_sign.py:61-76`，无 live/search）。
因此 `test_m2_secsdk_send_side.py` 的 S1 判据（「受保护端点窗口内须有 signed_url」）
对本端点**永远不生效** —— 它一直是**静默漏网**状态。
（注：该清单是 GET/POST 共用白名单，故未为其改动清单本身，而是按 D6 直接无条件加签。）

### 改后（照抄 `client_video.py:103` / `client_user.py:167` 的已修范式）

```python
# 改后（client_search.py:318-336）
params.with_web_id(auth, refer)
params.add_param("msToken", auth.msToken)
params.with_a_bogus()
# ★ 2026-09-27 修复（ADR-018 D6 / F5 接线前置）
url = params.signed_url(f'{DouyinAPI.domain_for(api)}{api}', auth)
resp = requests.get(url, headers=headers.get(), cookies=auth.cookie,
                    verify=tls_verify())          # ★ 不传 params=，避免二次编码
```

✅ **S2 判据满足**：`signed_url` 之后**没有**再把 `params=params.get()` 交给 requests
（否则 requests 二次编码 → 与签名输入不一致 → 依旧 403）。
程序化核验：

```
signed_url in search_live: True
params= leaked to requests: False
domain_for used: True
```

`features.py:68` 的 `search_live` 包装**无需改动** —— 它只是
`_safe("search_some_live", ...)` 薄封装，签名在更底层的 `search_live` 落实即可覆盖。

---

## ③ 新端点路径与返回字段

**端点**：`POST /api/live/rooms/discover`（挂在既有 `/api/live/rooms` 前缀下，`main.py:789`）

**入参**：

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `query` | str | （必填） | 搜索关键词，空 → 400「请输入搜索关键词」 |
| `account` | str | `""` | 留空 = 取当前选中账号 |
| `num` | int | 20 | 上限钳制到 50 |

**返回**：

```
{
  "ok": true,
  "query": "工伤咨询",
  "blocked": false,                      // ★ true = 被风控拦截（禁止假成功）
  "transport": {"status": 403, "bytes": 46} | null,
  "items": [
    {
      "room_id":      "7381234567",
      "title":        "工伤法律咨询",
      "nickname":     "张律",            // ★ 仅搜索结果自带，D7
      "sec_uid":      "MS4wLjABAAAA_x",
      "online_count": 1203,
      "cover":        "https://...",
      "live_url":     "https://live.douyin.com/7381234567"
    }
  ],
  "error": "被风控拦截（Argus 网关返回 HTTP 403，46 字节）——…"   // 仅 blocked 时出现
}
```

**职责边界（刻意窄）**：`/discover` **只做只读搜索，不写任何 kv**。
上架由前端复用既有 `POST /api/live/rooms`（`save_room`）完成 —— 不另造存储、不另开写路径。

**四态实测**（离线桩，真实执行，非描述）：

| 场景 | ok | blocked | items | 说明 |
|---|---|---|---|---|
| HTTP 200 有结果 | true | false | 1 | 无 `room_id` 的残缺行被丢弃 |
| Argus 403 / 46B | true | **true** | 0 | 报「被风控拦截」，**不**假装没结果 |
| HTTP 200 真空 | true | false | 0 | 正常空态 |
| 空关键词 | — | — | — | **400**「请输入搜索关键词」，与「没搜到」可区分 |

### 过程中发现并修掉的真实缺陷

写入 `last_transport` 时**内置 `list` 没有 `__dict__`**，直接赋值抛 `AttributeError`；
若像初版那样用 `try/except` 吞掉，风控事实会**静默丢失** → 上层只收到空列表 →
前端显示「没搜到」，正好是铁律禁止的**假成功**。
已改为 `LiveSearchResult(list)` 子类承载（`list` 子类 ⇒ 既有 `for/len/切片` 用法零改动）。
这是**测试跑出来的**，不是推演出来的。

---

## ④ 前端入口

**位置**：`frontend/src/components/live/RoomManagePage.tsx` —— 「直播间管理」弹窗**顶部**
新增「搜索发现」区块（`data-od-id="room-discover"`），位于「已登记直播间」列表之上。

**交互**：输入关键词 → 回车或点「搜索」→ 结果列表（封面 / 昵称 / 房间号 / 在线数 / 标题）
→ 每行「上架」按钮 → 调 `api.saveLiveRoom()` 落 `live_rooms` → 刷新列表。
已上架的房间按钮自动置灰显示「已上架」（按 `room_id` 去重）。

**空态 / 失败态（如实呈现）**：

| 状态 | 呈现 |
|---|---|
| 被风控拦截 | 红色告警条 + 后端原文（HTTP 403，46 字节…），**不**显示空列表 |
| 确实 0 条 | `Blank` 组件「未搜索到与「xxx」相关的直播间。」 |
| 未搜索过 | 不显示任何结果区 |
| 昵称缺失 | 显示「（无昵称）」，**绝不**为补齐而回调补查接口 |

---

## ⑤ A / B 真实命令与 exit code

### A. 后端语法检查

```
$ cd DYAutoDM_v2/backend && python -m py_compile dy_apis/client_search.py api/live_rooms.py
→ exit 0
```

### B. 前端

```
$ cd DYAutoDM_v2/frontend && npm run lint
→ exit 0

$ cd DYAutoDM_v2/frontend && npx tsc --noEmit
→ exit 0
```

（`npm run lint` 在本仓库即 `tsc --noEmit`。）

### 附加：既有签名门禁未回归

```
$ cd DYAutoDM_v2/backend && python -m pytest test_m2_secsdk_send_side.py -q
4 passed in 1.62s
```

S1~S4 全绿（含 S3 负控自证：错误用法 `signed_url + params=` 仍会被判据命中）。

---

## ⑥ D7 grep 证据（0 命中）

```
$ grep -n "bulk_user_info\|get_im_user_info" \
    backend/api/live_rooms.py backend/dy_apis/client_search.py \
    frontend/src/components/live/RoomManagePage.tsx
```

命中 2 处，**全部在注释 / docstring 里，且内容正是「禁止调用它们」**：

```
live_rooms.py:61: 只取搜索结果**自带**的 ``nickname``，**绝不**回调 ``bulk_user_info`` /
live_rooms.py:62: ``get_im_user_info`` 之类补查接口。取不到就返回空串，由前端显示「—」。
```

按「是否为可执行代码」过滤（排除 `#` / `*` / `//` 注释行与 docstring 文本）后：

```
live_rooms.py:      CODE hits = 0
client_search.py:   CODE hits = 0
RoomManagePage.tsx: CODE hits = 0
```

**结论：F5 全链路 0 处主动批量查昵称的调用。** 昵称唯一来源是搜索结果自带的
`item.nickname` / `item.author.nickname` / `item.anchor.nickname`，取不到即空串。

---

## 中文总结

F5 已按 ADR-018 落地并通过静态验收。

**最实质的收获是 D6 的前置核实**：`client_search.py` 的搜索端点**从来没走过
`signed_url`**，而且 `/aweme/v1/web/live/search/` 不在 `PROTECTED_PATHS_GET` 清单里，
所以现有的 M-2 签名门禁（S1）**根本抓不到它** —— 它是一个静默漏网的未签名端点。
若不先 grep 而是照「后端已有」的结论直接接前端，上线必然是被 Argus 403 拦死。
现已按 `client_video.py` / `client_user.py` 的已修范式无条件改走 `signed_url`，
并满足 S2（签名后不再传 `params=`，避免二次编码）。

**功能上刻意做了两段拆分**：`/discover` 只做只读搜索，上架复用既有
`save_room` —— 没有第二套存储、没有第二份写路径。「禁止假成功」落实为四种
可区分状态（有结果 / 被风控拦截 / 真空 / 输入错误），Argus 403 会明确报
「被风控拦截」而不是返回空列表。

**过程中测试跑出一个真实缺陷并已修**：风控事实原本靠给 `list` 挂属性透传，
而内置 `list` 没有 `__dict__`，赋值必然抛 `AttributeError`；初版的 `try/except`
会把它**静默吞掉**，导致风控事实丢失、前端退化成「假装没搜到」—— 正好撞上项目
禁止的假成功。改成 `LiveSearchResult(list)` 子类后，`for/len/切片` 等既有用法零改动。

**验收**：后端 `py_compile` exit 0；前端 `npm run lint` 与 `npx tsc --noEmit` 均 exit 0；
既有签名门禁 4 项全绿。D7 全链路 0 处主动查昵称调用（仅存的 2 处命中都在
「禁止调用它们」的注释里）。未 commit、未改版本号、未启动浏览器、未真调抖音接口。

**遗留**：真实联调尚未做（无登录态且属主动请求，按红线未触碰）。
上线前应真机验证一次：签名是否真从 403 变成 200，以及搜索结果的实际字段布局
（`_pick_live` 已兼容扁平 / `author` 包 / `room` 包三种形态，但真实上游形态
只在真机可见，若出现第四种布局需补 `_pick_live` 的兜底分支）。
