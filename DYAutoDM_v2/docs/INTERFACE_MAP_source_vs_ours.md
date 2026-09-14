# 接口方案映射表 —— 源项目 better-douyin ↔ 本项目

> **为什么有这份表**：2026-09-15 用户明确要求「**业务接口都更换为源项目的最新方案，
> 而不是闭门造车反复碰壁**」。此前我按自己理解逐个试错（碰壁），缺的就是
> **一份"源项目用什么 → 我该用什么"的权威对照**。
>
> **使用方法**：改任何平台接口前，**先查本表**；本表没有的，先逆向源项目补齐再动手。
> **禁止**凭字段名猜测 / 自己拼路径。
>
> **权威来源**：
> 1. 逆向字符串 `D:\bdy_reverse\extract_dl\all_strings.txt`（源项目二进制，28 条路径）
> 2. 源项目壳可读源码 `frontend/src/lib/`（含 `ai-automation.ts` 等**完整契约**）
> 3. **实机直连验证结果**（本表每条都要求实测，标注 HTTP 码/字节数/条数）

---

## 一、源项目全量接口清单（逆向提取，28 条）

| # | 源项目路径 | 用途 | 我方现状 | 实测状态 |
|---|---|---|---|---|
| 1 | `/aweme/v1/web/tab/feed/` | **推荐流** | ✅ 已改用（2026-09-15） | **200 / 659KB / 6 条** ✅ |
| 2 | `/aweme/v1/web/general/search/stream/` | 搜索（流式） | ❌ 我方用 `search/single/` | 200 / 911KB（**流式 SSE**，需流式解析） |
| 3 | `/aweme/v1/web/discover/search/` | 发现搜索 | — | 待测 |
| 4 | `/aweme/v1/web/user/profile/self/` | **自己资料（含 sec_uid）** | ✅ 已改用（2026-09-15） | **200 / 17954B / 含 sec_uid** ✅ |
| 5 | `/aweme/v1/web/user/profile/other/` | 他人资料 | 我方 `get_user_info` | 待测 |
| 6 | `/aweme/v1/web/aweme/post/` | 用户作品 | ✅ 一致 | 待测 |
| 7 | `/aweme/v1/web/aweme/detail/` | 作品详情 | ✅ 一致 | **0 字节**（平台侧）⚠️ |
| 8 | `/aweme/v1/web/multi/aweme/detail/` | 批量作品详情 | ❌ 未用 | 200 / 301B（待深验） |
| 9 | `/aweme/v1/web/aweme/listcollection/` | 收藏夹作品 | ✅ 一致（**POST**） | 200 / 1 条 ✅（GET 会 404） |
| 10 | `/aweme/v1/web/mix/listcollection/` | 收藏合集 | ✅ 一致 | 200 / 1 个 ✅ |
| 11 | `/aweme/v1/web/series/aweme/` | 合集内作品 | ✅ 一致 | 待测 |
| 12 | `/aweme/v1/web/aweme/favorite/` | **点赞/喜欢列表** | ✅ 一致 | **0 字节**（平台侧）⚠️ |
| 13 | `/aweme/v1/web/aweme/collect/` | 收藏动作 | ✅ 一致 | 0 字节（写操作族）⚠️ |
| 14 | `/aweme/v1/web/commit/item/digg/` | 点赞动作 | ✅ 一致 | 0 字节 ⚠️ |
| 15 | `/aweme/v1/web/commit/follow/user/` | 关注动作 | ✅ 一致 | 0 字节 ⚠️ |
| 16 | `/aweme/v1/web/comment/list/` | 评论列表 | ✅ 一致 | 200 / 正常 ✅ |
| 17 | `/aweme/v1/web/comment/list/reply/` | 评论回复 | ✅ 一致 | 待测 |
| 18 | `/aweme/v1/web/comment/publish` | 发布评论 | ✅ 一致 | 待测 |
| 19 | `/aweme/v1/web/comment/digg` | 评论点赞 | ✅ 一致 | 待测 |
| 20 | `/aweme/v1/web/notice/` | 通知 | ✅ 一致 | 200 / 362B（我方 v2 数据更多） |
| 21 | `/aweme/v1/web/query/user` | **取自己 uid** | ✅ 一致 | 200 / uid=316276709526638 ✅ |
| 22 | `/aweme/v1/web/im/user/info/` | **IM 用户信息（批量）** | ✅ 一致（**POST + sec_user_ids**） | **200 / sc=0** ✅ |
| 23 | `/aweme/v1/web/im/spotlight/relation/` | IM 关系 | ✅ 一致 | 待测 |
| 24 | `/aweme/v1/web/im/user/active/status/` | 在线状态 | ✅ 一致 | 待测 |
| 25 | `/aweme/v1/web/im/upload/config/v2` | IM 上传配置 | — | 待测 |
| 26 | `/aweme/v1/web/aweme/detail/video` | 视频详情 | — | 待测 |
| 27 | `/aweme/v1/web/module/feed/` | ~~老推荐流~~ | ❌ **我方原用**（已替换） | 返回 cards+JSON字符串（脆弱） |
| 28 | `/aweme/v1/web/collects/list/` | ~~老收藏夹列表~~ | ❌ **我方原用** | 待测（建议换 #9） |

---

## 二、已确认的"源项目方案 > 我方原方案"（实测证据）

### ① 推荐流：`tab/feed/` 优于 `module/feed/`

| | 源项目 `tab/feed/` | 我方原 `module/feed/` |
|---|---|---|
| HTTP / 体积 | **200 / 659,465 B** | 200（较小） |
| 结构 | **标准 `aweme_list`** | `cards[].aweme`（**JSON 字符串**，需二次解析） |
| 实测条数 | **6 条**（直接可读 desc/author/aweme_id） | 10 条（需补丁解包） |

⇒ 已替换（`client_video.get_feed`）。

### ② 取自己 sec_uid：`user/profile/self/` 优于 HTML 正则

| | 源项目接口 | 我方原 HTML 正则 |
|---|---|---|
| 结果 | **200 / 17954B / `user.sec_uid`** | **失败**（72KB 响应中 secUid 出现 **0 次**） |
| 原因 | 结构化接口 | 抖音改纯异步渲染，HTML 已无数据 |

⇒ 已替换（`client_user.get_my_sec_uid`）。

### ③ IM 用户查询：只认 `sec_user_ids`（阶段1 实测 7 种组合）

```
POST /aweme/v1/web/im/user/info/  body: sec_user_ids=[...]  → sc=0 ✅
GET  to_user_id      → "url doesn't match" ❌
GET  user_id         → "url doesn't match" ❌
POST user_ids        → sc=5 参数不合法 ❌
```
⇒ **数字 uid 查不了**；会话表 83 个中 38 个无昵称且**无 sec_uid**，
故这 38 个**当前无法主动补全昵称**（需先拿到 sec_uid，如从会话首包 protobuf）。

---

## 三、域名策略（照源项目，2026-09-14 修正）

**www-hj 承载"互动与列表"类（读+写皆有）**，证据（`all_strings.txt` 原文）：
```
https://www-hj.douyin.com/aweme/v1/web/commit/item/digg/     ← 点赞（写）
https://www-hj.douyin.com/aweme/v1/web/commit/follow/user/   ← 关注（写）
https://www-hj.douyin.com/aweme/v1/web/comment/digg          ← 评论点赞（写）
https://www-hj.douyin.com/aweme/v1/web/aweme/collect/        ← 收藏（写）
https://www-hj.douyin.com/aweme/v1/web/comment/list/         ← 评论列表
https://www-hj.douyin.com/aweme/v1/web/comment/list/reply/
https://www-hj.douyin.com/aweme/v1/web/im/user/active/status/
https://www-hj.douyin.com/aweme/v1/web/im/spotlight/relation/
https://www-hj.douyin.com/aweme/v1/web/series/aweme/
https://www-hj.douyin.com/aweme/v1/web/mix/listcollection/
https://www-hj.douyin.com/aweme/v1/web/aweme/favorite/
https://www-hj.douyin.com/aweme/v1/web/aweme/listcollection/
```
`www` 承载：搜索、详情、主页、发布等。
⚠️ 早期我误记"写操作用 www" —— **已被原始字符串推翻**。

---

## 四、工作纪律（本次教训固化）

1. **改接口前先查本表**；不在表中的，先逆向源项目补齐。
2. **禁止**凭字段名猜路径（曾因此用 `module/feed`、`collects/list` 等老接口）。
3. **每条改动必须实测**：记录 HTTP 码 / 字节数 / 条数，写回本表「实测状态」列。
4. **权威优先级**：源项目壳可读源码（`lib/*.ts`）> 逆向字符串 > 自己推测。
   （阶段3 曾因只信逆向粘连字符串而猜错 25 个字段名，实测 `scanned=0` 才发现。）
