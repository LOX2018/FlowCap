# F3 报告 —— 内容页「评论采集 + 与播放同时展示 + 评论行发私信」

> ADR-018 F3 · 分支 `design/better-douyin` · 版本 v0.45.40（**未改版本号**）· 2026-09-27

---

## ① 改动文件清单

| # | 文件 | 性质 | 说明 |
|---|---|---|---|
| 1 | `backend/dy_apis/client_comments.py` | 修改 | 补齐 D6 签名（`get_work_out_comment` / `get_work_inner_comment` 两个**实际发请求**的方法） |
| 2 | `backend/api/platform.py` | 修改 | 新增 2 个端点 + 2 个请求模型 + 1 个规范化函数 |
| 3 | `frontend/src/api/platform.ts` | 修改 | 新增 `CommentFullItem` 类型 + `commentsFull()` / `commentDm()` |
| 4 | `frontend/src/components/platform/comment-panel.tsx` | **新增** | 评论面板（复用既有 ui 组件） |
| 5 | `frontend/src/components/platform/platform-page.tsx` | 修改 | 播放器浮层右侧挂载 CommentPanel + 加宽容器 |

**未改动**：`backend/dy_apis/client_search.py`、`api/crawl.py`（复用既有）、版本号文件；**未** git add / commit。

---

## ② 签名核实结论与实际代码片段（判据 C · 硬）

### 核实结论：**原本没走，已补齐**

grep 实证（`backend/dy_apis/client_comments.py` 改动前）：

```
$ grep -n "signed_url\|requests.get\|params=params.get()" backend/dy_apis/client_comments.py
102:        resp = requests.get(f'{DouyinAPI.douyin_url}{api}', headers=headers.get(), cookies=auth.cookie,
103:                            params=params.get(), verify=tls_verify())
189:        resp = requests.get(f'{DouyinAPI.douyin_url}{api}', headers=headers.get(), cookies=auth.cookie,
190:                            params=params.get(), verify=tls_verify())
```

⇒ **`signed_url` 零命中**；两个真正发请求的方法（`get_work_out_comment` L47 /
`get_work_inner_comment` L137）都是「裸 `params=params.get()`」，且用
`DouyinAPI.douyin_url`（www）而非 `domain_for()`（`/aweme/v1/web/comment/list`
在 `client.py:77` 的 `_HJ_PREFIXES` 里，应走 www-hj）。

对照同仓已修端点 `backend/dy_apis/client_collection.py:83`（M-2 已闭环实测 403→200）：

```python
url = params.signed_url(f'{DouyinAPI.domain_for(api)}{api}', auth)
resp = requests.post(url, headers=headers.get(), cookies=auth.cookie,
                     verify=tls_verify(), timeout=15)
```

### 改动后的实际代码片段（照抄该写法）

**① `get_work_out_comment`（`client_comments.py:102-112`，一级评论 `/aweme/v1/web/comment/list/`）**

```python
        params.with_a_bogus()
        # ★ 2026-09-27 修复（ADR-018 F3 / D6）：`/aweme/v1/web/comment/list/`
        #   原实现把 `params.get()` 交给 requests 的 `params=` —— 缺 uifid 与
        #   secsdk 签名 ⇒ 被 Argus 网关拦下返 **HTTP 403（46B，非 JSON）
        #   "Blocked by ArgusSecurityPlugin Uifid Not Found"**，safe_json 降级
        #   `{}` ⇒ 评论恒空（与 M-2 已闭环的 listcollection 同一根因）。
        #   ⇒ 改走 `signed_url()`（带 uifid + `x-secsdk-web-signature`）。
        #   注意：必须发 `signed_url()` 的返回值本身，**不能再把 params 交给
        #   requests**（requests 会二次编码，与签名输入对不上 → 依旧 403）。
        url = params.signed_url(f'{DouyinAPI.domain_for(api)}{api}', auth)
        resp = requests.get(url, headers=headers.get(), cookies=auth.cookie,
                            verify=tls_verify())
```

**② `get_work_inner_comment`（`client_comments.py:197-204`，楼中楼 `/aweme/v1/web/comment/list/reply/`）**

```python
        params.with_a_bogus()
        # ★ 2026-09-27 修复（ADR-018 F3 / D6）：`/aweme/v1/web/comment/list/reply/`
        #   与一级评论同源同因 —— 不签名即被 Argus 403（46B 非 JSON）⇒ 楼中楼恒空。
        #   改走 `signed_url()`，且**不再把 params 交给 requests**（二次编码会让
        #   签名失效）。`domain_for` 照 `client.py` 的 www-hj 双域名策略选域。
        url = params.signed_url(f'{DouyinAPI.domain_for(api)}{api}', auth)
        resp = requests.get(url, headers=headers.get(), cookies=auth.cookie,
                            verify=tls_verify())
```

**③ 调用链（改动前后一致，仅底层发送方式变了，采集逻辑一字未动）**

```
新增端点 /api/platform/comments/full
  └─ get_work_out_comment         ← 翻页循环（带 limit 上限）      ✅ signed_url
     get_work_all_inner_comment
       └─ get_work_inner_comment  ← 楼中楼                        ✅ signed_url
get_work_all_comment / get_work_all_out_comment / publish_comment
  └─ 均为对上面两个方法的编排外层，自身不发请求
```

> `publish_comment`（写接口 `/aweme/v1/web/comment/publish`）本次**未改**：
> 该路径不在 `utils/secsdk_web_sign.PROTECTED_PATHS_GET / _POST` 保护清单内
> （清单见 `secsdk_web_sign.py:61-84`，共 14 条 GET / 6 条 POST，`comment/publish`
> 不在其中），且本项目当前**无生产调用方**。F3 只做只读采集，不动写接口。

### M-2 门禁回归（确保新增签名不触发二次编码）

```
$ cd DYAutoDM_v2/backend && python -m pytest test_m2_secsdk_send_side.py -q
4 passed
```

---

## ③ 新端点路径与返回字段

### `POST /api/platform/comments/full`（**只读**）

位置：`backend/api/platform.py` → `async def comments_full()`（紧随既有 `POST /api/platform/comments`）

> **为什么挂在 `api/platform.py` 而不是新建模块**：用户 2026-09-15 已决策
> 「采集 = 内容浏览的高级模式」，platform.py 已是内容域路由；`crawl.py` 那条
> 链路是**关键词搜索**专用的旧模型。挂在 platform.py 可复用其 `_auth_for()`（凭证校验）
> 与 `_api()`，且零新增模块。

**请求体**（`CommentsFullReq`）

| 字段 | 类型 | 默认 | 说明 |
|---|---|---|---|
| `account` | str | — | 账号名 |
| `aweme_id` | str | — | 作品 ID（缺失 → 400） |
| `url` | str | `""` | 作品 URL；缺省按 aweme_id 拼 `https://www.douyin.com/video/{id}` |
| `limit` | int | 50 | 一级评论上限，钳制 [1, 200] |
| `with_inner` | bool | true | 是否拉楼中楼 |
| `inner_limit` | int | 5 | 单条一级评论保留多少楼中楼，钳制 [0, 20] |

**返回体**

```jsonc
{
  "ok": true,
  "total": 12,
  "has_more": false,
  "blocked": false,            // true ⇒ 被风控拦截，文案在 reason
  "reason": "",                // blocked 时 = "被风控拦截"
  "items": [
    {
      "cid": "7...",                    // 评论 id
      "text": "评论内容（截断 500 字）",
      "digg_count": 12,                 // 点赞数
      "create_time": 1758000000,        // 时间戳（秒）
      "reply_comment_total": 3,         // 楼中楼总数（平台原始值）
      "has_inner": true,                // 是否含楼中楼
      "reply_comment": [                // 楼中楼，同结构（无更深嵌套）
        { "cid": "...", "text": "...", "digg_count": 0,
          "create_time": 0, "user_nickname": "...", "user_uid": "..." }
      ],
      "user_nickname": "评论者昵称",      // ★ 仅取评论自带，绝不补查
      "user_uid": "1234567890",
      "user_sec_uid": "MS4wLjABAAAA..."
    }
  ]
}
```

**失败态如实呈现**
- HTTP 502：采集抛异常 → `评论采集失败: <异常类型>`；
- `blocked=true`：**一条也没取到且复探仍空** ⇒ 标注被风控拦截（前端显示「评论被风控拦截」+ 重试按钮，
  **不显示空列表冒充「暂无评论」**）；
- 取到但列表为空（复探有 `status_code`）⇒ `items: []` + 前端显示「该作品暂无评论」，与拦截态区分。

### `POST /api/platform/comments/dm`（**仅手动触发**）

位置：`backend/api/platform.py` → `async def comment_dm()`

请求 `{account, uid, text, nickname?}`（nickname 仅用于日志）；缺失 uid/text → 400。

返回 `{ok, accepted, error, task_id}`：
- ⚠️ `accepted=true` 只是**入队受理**，不是投递成功（符合 D4「无回执不认成功」），
  后端**不**把它包装成「已发送」，前端 UI 文案为「已受理（等待投递回执确认）」。

---

## ④ 私信入口复用了 dm_dispatch 的哪个符号（判据 E）

**`services/dm_dispatch.py` 的 `DmDispatcher.submit_by_uid`**

| 项 | 值 |
|---|---|
| 被复用方法 | `DmDispatcher.submit_by_uid`（实例方法） |
| **定义位置** | `backend/services/dm_dispatch.py` **第 1133 行** |
| 获取途径 | `services.dm_dispatch.get_dispatcher()`（`dm_dispatch.py` **第 1406 行**） |
| 本端调用点 | `backend/api/platform.py` → `comment_dm()`：`disp.submit_by_uid(req.account, uid, text, "manual")` |

**为什么是 `submit_by_uid` 而不是 `submit`**：`dm_dispatch.py:170-188` 的来源矩阵明写
「视频采集 / 直播监听 = **陌生人首发** → `submit_by_uid`；私信中心 / AI = 熟客 → `submit()`」。
评论作者与我方从未往来、**没有 conv_id**，正是陌生人首发语义，故只能走 `submit_by_uid`。

**本端点未自己实现的东西**（全部由 `submit_by_uid` 提供）：
测试白名单 → 跨账号沉淀池 → per-account UID 沉淀池 → 队列容量检查 →
**陌生人首发限流（2/分钟、30/天）** → 额度预占/归还 → per-account 串行调度 → 双通道降级发送。

---

## ⑤ 前端挂在哪一页、为什么

**挂在 `platform` 页（内容浏览），不是 `crawl` 页。** 依据（读代码，非猜测）：

1. `platform-page.tsx:526-558` 持有**唯一的播放器浮层** `FullscreenPlayer`，
   是唯一持有「正在播放的作品 aweme_id」的页面（`selectedAweme`）；
2. `crawl-page.tsx` 是**关键词搜索**链路（搜作品 → 点评论 → 私信），页面内**没有播放器**
   （`fullscreen-player.tsx:21-22` 注释原文：「源项目的评论子系统本项目未对齐，
   评论能力在 crawl-page 采集链路」）——挂它就得先造播放器；
3. 用户 2026-09-15 已决策「采集 = 内容浏览的高级模式」，采集面板 `crawl-panel.tsx`
   本就被 platform 页引用（`platform-page.tsx:508`），同归一处no分叉。

**具体挂载方式**：播放器浮层右侧 `<aside>` 内的 `<CommentPanel>`；容器 `max-w-3xl → max-w-5xl`
为评论留出侧栏（`md:` 以下自动隐藏，不挤占窄屏）。

**「同时展示」如何保证**：`CommentPanel` 挂载即 `useQuery`（`queryKey: ["platform-comments-full", account, awemeId]`），
`enabled` 只依赖 `account && awemeId` —— **不在任何 `TabsContent` 内**，与播放器
`mediaStreamTicket` 取址**并发**发出，不是切 Tab 才加载。

**复用的既有组件**（无自研弹层/控件）：`@/components/ui/button`、`input`、`badge`、
`empty-state`（`EmptyState` / `LoadingState` / `ErrorState`），数据层 `@tanstack/react-query`
+ `@/api/platform`。

---

## ⑥ A~D 的真实命令与 exit code

### A. 后端 `py_compile`

```
$ cd C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend
$ python -m py_compile api/platform.py dy_apis/client_comments.py
COMPILE_OK_0
```
**exit code = 0**

### B. 前端 `npm run lint` + `npx tsc --noEmit`（cd frontend）

```
$ npx tsc --noEmit
TSC_EXIT=0

$ npm run lint
LINT_EXIT=0
```
**两者 exit code = 0**

**验证汇总（全部重跑过最后一轮）**：

| 项 | 命令（均在 `DYAutoDM_v2/` 下） | 结果 |
|---|---|---|
| A 后端编译 | `cd backend && python -m py_compile api/platform.py dy_apis/client_comments.py` | **exit 0** |
| 签名门禁 | `cd backend && python -m pytest test_m2_secsdk_send_side.py -q` | **4 passed**（exit 0） |
| B 类型检查 | `cd frontend && npx tsc --noEmit` | **exit 0** |
| B lint | `cd frontend && npm run lint` | **exit 0** |
| C 签名接线 | `grep -n "signed_url" backend/dy_apis/client_comments.py` | **2 处**（改动前 0 处） |
| D 昵称红线 | grep 4 符号 × 5 个改动文件 + `git diff -U0` 增量级 | **0 命中** |

### C. 签名判据 —— 见 §②

结论：**原未走签名（grep 零命中），已按 `client_collection.py:83` 同款写法补齐**，
并跑通 M-2 门禁：

```
$ cd DYAutoDM_v2/backend && python -m pytest test_m2_secsdk_send_side.py -q
4 passed
```

### D. 昵称红线（判据 D · 硬）

```
$ cd C:/Users/LOX/Desktop/DYchajian
$ grep -n "bulk_user_info\|get_im_user_info\|bulk_user_info_by_uid\|bulk_user_info_via_browser" \
    DYAutoDM_v2/backend/dy_apis/client_comments.py \
    DYAutoDM_v2/frontend/src/api/platform.ts \
    DYAutoDM_v2/frontend/src/components/platform/comment-panel.tsx \
    DYAutoDM_v2/frontend/src/components/platform/platform-page.tsx
GREP_A_EXIT=1                       # ← 0 命中（exit 1 = 未找到）

$ git diff -U0 -- DYAutoDM_v2/backend/api/platform.py | grep "^+" \
    | grep -c "bulk_user_info\|get_im_user_info"
0                                   # ← platform.py 本次新增行 0 命中
```

**0 命中**。四个红线符号在**本次全部 5 个改动文件中均无引用**；
`git diff -U0 | grep "^+"` 更严格地证明 platform.py **本次新增的行**里一个都没有
（文件里 8 处既有命中全在改动前的文档/旧 fallback 代码块中，非本次引入）。
昵称一律取自评论响应自带 `user.nickname` / `reply_comment[].user.nickname`
（`_map_comment_full()`，两处均已注 `自带，不补查`）。

---

## ⑦ 边界与未做的事（如实说明）

- **未真调抖音接口**：本分支无可用登录态（平台对所有本人端点返 `status_code=8`），
  且主动请求属风控红线 ⇒ **未做实网采集验证**，403→200 的效果依据 M-2 同端点
  （listcollection / favorite / aweme/detail）的实测结论外推，判据为「同一网关、同一成因、
  同一修复方式」。首次真机使用建议先用一个已知有评论的作品验证。
- **`publish_comment` 未改**：写接口，不在 secsdk 保护清单、本项目无生产调用方，F3 不需要。
- **`api/crawl.py` 的既有评论/私信端点未动**（`crawl-page.tsx` 仍在用），两套并存互不干扰。
- 前端 `<aside>` 在 `md` 以下断点隐藏 —— 窄屏不显示评论区，非「加载失败」。

---

## 中文总结

ADR-018 F3 已落地：**四个文件改动 + 一个新增前端组件**。核心工作有三块。

**第一，签名是真缺陷，已补实。** grep 实证 `client_comments.py` 里 `signed_url` **零命中**——
一级评论和楼中楼两个真正发请求的方法走的都是裸 `params=params.get()`，且域名用 www 而非
应有的 www-hj。按 `client.py:77` 的 `_HJ_PREFIXES` 和 M-2 已闭环的 `client_collection.py:83`
同款写法补齐了两处 `params.signed_url(f'{DouyinAPI.domain_for(api)}{api}', auth)`，
并把 `params=` 从 requests 调用里去掉（否则二次编码使签名失效）。M-2 门禁 4 项全过。
`publish_comment` 不在保护清单且无调用方，未动。

**第二，两个薄端点复用既有能力。** `/api/platform/comments/full`（只读采集，一级+楼中楼，
带 limit 上限防无限翻页）和 `/api/platform/comments/dm`（转调
`dm_dispatch.py:1133` 的 `DmDispatcher.submit_by_uid`，陌生人首发语义，限流/去重/投递验证
全部复用，一行自己的发送逻辑都没写）。挂在 platform.py 而非新建模块，因为内容域路由和
`_auth_for()` 凭证校验都在那里。

**第三，评论与播放确实同屏。** 挂在 platform 页播放器浮层右侧（判据是 platform-page 持有唯一
播放器浮层，crawl-page 是纯搜索链路没有播放器），`CommentPanel` 挂载即 useQuery，与播放器
取址并发，不在任何 TabsContent 内。失败态区分「被风控拦截」与「暂无评论」，不拿空列表冒充成功；
私信按钮无 uid 时不渲染；`accepted` 只显示「已受理」，不谎称已送达。昵称全程只用评论自带的
`user.nickname`，四个红线符号在改动文件中 0 命中。

**验证**：后端 `py_compile` exit 0、M-2 签名门禁 4 passed；前端 `npx tsc --noEmit` exit 0、
`npm run lint` exit 0。过程中一度出现 `TagSection.tsx` / `client.ts` 两处类型错误，经
`git status` 确认是本仓其它并行任务正在改的文件（+132 / +164 行），与本任务无关，
现已收敛，全仓两项均恢复到 exit 0。
**未做实网验证**（无登录态 + 主动请求属红线），403→200 的效果是从 M-2 同网关同成因的实测外推的，
首次真机使用建议先拿一个有评论的作品验一次。
