# 设计契约 · C-02 内容采集（dy_apis / client_collection · client_video · client_user）

> 来源：R8 核对（2026-09-21）实测 + `工作记忆/10_上游情报_合并.md`（章 `10_上游情报_签名与风控.md`）。
> 本契约把「上游接口约定」固化为可执行判据。

## 1. 设计意图

以账号身份抓取抖音内容（作品详情、作品列表、收藏夹、合集、音乐）供项目业务使用。

## 2. 设计契约（DbC）

| 类型 | 内容 |
|---|---|
| **前置条件** P | ① `auth` 携带有效 cookie（含 `UIFID` / `ttwid` / `msToken`）；② 请求 path 已按签名策略（`a_bogus` + 可选 `x-secsdk-web-signature`）构造 |
| **后置条件** Q | ① 返回 JSON 且 `status_code == 0` 视为成功；② 非 0 或 403 时记录**原始响应**（前 200 字节）供诊断；③ 命中 secsdk 保护清单的端点必须走 `signed_url()` |
| **不变式** I | Ⅰ1 **保护清单端点必须签名**：`is_protected(path)` 为真时禁止 `params=params.get()` 直发（会 403）；Ⅰ2 不得用 `series_id` 参数打普通合集（`is_serial_mix=0` 时须用 `mix_id`）；Ⅰ3 签名对**规范化后 query** 计算，服务端按收到的 query 校验 —— 必须发 `signed_url()` 返回值 |

## 3. 规范契约

| 规范字段 | 说明 |
|---|---|
| `mix_id` | 普通合集 id（来自 `mix_infos[].mix_id`） |
| `series_id` | **仅**短剧（series）用，禁止用于普通合集 |
| `cursor` / `count` | 分页，str 型（接口要求） |

## 4. secsdk 保护名单核对结果（R8 实测，2026-09-21）

**上游 SDK 策略清单（14 个 GET）**：`aweme/detail/`、`aweme/post/`、`aweme/favorite/`、`aweme/listcollection/`、`mix/aweme/`、`tab/feed/`、`mix/list/`、`music/aweme/`、`music/list/`、`mix/detail/`、`mix/listcollection/`、`music/detail/`、`collects/list/`、`collects/video/list/`

**本项目实际接线（实测）**：

| 端点 | 调用点 | 是否走 `signed_url()` |
|---|---|---|
| `mix/aweme/` | `client_collection.py:get_mix_aweme` | ✅ 已接 |
| `aweme/listcollection/` | `client_collection.py` | ❌ 仍 `params.get()` |
| `mix/listcollection/` | `client_collection.py` | ❌ 仍 `params.get()` |
| `collects/list/` | `client_collection.py:228` | ❌ 仍 `params.get()` |
| `aweme/detail/` | `client_video.py:53` | ❌ 仍 `params.get()` |
| `aweme/post/` | `client_user.py:430` | ❌ 仍 `params.get()` |
| `aweme/favorite/` | `client_user.py:154` | ❌ 仍 `params.get()` |

> **结论**：实现（`utils/secsdk_web_sign.py`）完整且实测 5/5 抓包回归，但**接线只覆盖 1/7 已定位调用点**。
> 未接线的端点在上游策略漂移后会**静默 403**（返回 46 字节非 JSON）——这正是 2026-09-21「合集作品取不到」的根因类型。
> ⚠️ 该实现属**他人未提交在制品**，本会话只核对、不擅改。

## 5. 验证方式

```bash
# 契约守护（期望：0 处直发保护端点）
grep -rn "params=params.get()" backend/dy_apis/ | grep -B2 "aweme/v1/web"
# 签名自检
py314 -c "from utils.secsdk_web_sign import is_protected; print(is_protected('/aweme/v1/web/mix/aweme/'))"  # True
```

## 6. 已知缺口

- 7 个已定位调用点中 6 个未接 secsdk 签名（见 §4）。
- `music/aweme/`、`music/*` 等端点本项目暂无调用点，属未使用面。
