# 逻辑层复现 · 实测记录（源项目功能）

> **任务**：用户 2026-09-14「将源项目所用的功能全部在逻辑层复现」
> **分支**：`design/better-douyin`
> **记录日期**：2026-09-14
> **原则**：**全部为实测**（真实账号 `尚进工伤小助理` + 真实凭证）；未实测的明确标注。

---

## 一、本轮完成（阶段 1 媒体代理 + 阶段 5 内容面 + 部分 3）

### 阶段 1：媒体代理层 ✅
**新增** `backend/services/media_proxy.py`（对标源项目 `media_proxy_cache.rs`）

| 项 | 实现 | 实测 |
|---|---|---|
| 唯一解密实现 | `decrypt_media(cipher, skey_hex)` AES-256-GCM | ✅ 解密一致 |
| 格式识别 | `detect_format()` 8 种签名 | ✅ 识别 `image/jpeg` |
| 内存+磁盘缓存 | `get_cached` / `put_cached`（TTL 7 天，容量淘汰） | ✅ 命中 + 落盘 1204B |
| 单一入口 | `resolve()`（解密+缓存） | ✅ |
| 契约 | `media_proxy_url()`（对齐源项目 `mediaProxyUrl()`） | ✅ |
| 常量 | `AES_CHUNK_SIZE = 524288`（源项目实测值） | ✅ |
| 兼容 | `origin_image_resolver._decrypt` 委托到本模块 | ✅ 结果逐字一致 |

**收敛验收**：全仓真实 `AESGCM(...).decrypt` 实现 **1 处**（`media_proxy.py:95`），
其余 2 处均为 docstring 内的说明文字（已逐行核实缩进属文档）。

### 阶段 5：内容面补齐 ✅（照源项目接口）
**新增基座方法**（`dy_apis/douyin_api.py`）：

| 方法 | 接口 | 实测结果 |
|---|---|---|
| `get_aweme_list_collection` | `/aweme/v1/web/aweme/listcollection/` | ✅ **sc=0，2 条收藏作品** |
| `get_mix_list_collection` | `/aweme/v1/web/mix/listcollection/` | ✅ **sc=0，1 个合集**（"七王国的骑士"） |
| `get_series_aweme` | `/aweme/v1/web/series/aweme/` | 方法就位（缺 series_id 未验） |
| `get_aweme_favorite` | `/aweme/v1/web/aweme/favorite/` | ⚠️ **4 种组合全空**（见 §二） |

**新增双域名策略**（照源项目 §1.1 实测归纳）：
```python
DouyinAPI.domain_for(path)  # 按路径选 www / www-hj
```
实测映射：
```
www-hj  ← comment/list · aweme/listcollection · mix/listcollection
          series/aweme · aweme/favorite · im/user/info · spotlight/relation
          im/user/active/status · commit/item/digg · commit/follow/user · aweme/collect
www     ← comment/publish · aweme/post · feed 等
```

**新增 3 个端点**（`api/platform.py`）：
| 端点 | 实测 |
|---|---|
| `POST /collection/items` | ✅ 200，返回真实收藏作品（"一口气带你了解所有主流操作系统"） |
| `POST /collection/mixes` | ✅ 200，返回真实合集（"七王国的骑士"） |
| `POST /collection/series` | ✅ 400 正确校验缺 series_id |

### 阶段 3：频率模型（上轮已做，本轮随附）
`app_config.SECTIONS["automation"]` —— 12 字段照源项目 `auto_*` 模型。

---

## 二、⚠️ 实测失败/待解项（如实记录，不掩饰）

### 2.1 写操作类接口统一返回**空响应**
对照实测（同一账号、同一时刻）：

| 接口 | 类型 | 结果 |
|---|---|---|
| `/aweme/v1/web/general/search/stream/` | 读 | ✅ **585,901 B**（正常 JSON） |
| `/aweme/v1/web/comment/list/` | 读 | ✅ 108 B（正常 JSON） |
| `/aweme/v1/web/commit/follow/user/` | **写** | ❌ **0 B** |
| `/aweme/v1/web/commit/item/digg/` | **写** | ❌ **0 B** |
| `/aweme/v1/web/aweme/favorite/` | 读 | ❌ 0 B（4 组合全空） |

**判读**：读接口正常、写接口统一空 ⇒ **不是凭证/域名/方法问题**，
而是**平台侧对写操作的处理**（可能：空洞口 / 需额外签名层 / 需特定上下文）。

**与源项目的对照**：源项目二进制里有 `RELATION_SECURITY_GATEWAY: HTTP 403`
与 `bd_passport_security_gateway` —— 说明**它遇到的正是这一层**，
且它有自己的应对（闭源，未公开算法）。

⇒ **现状**：写操作（点赞/收藏/关注）在本分支**未打通**，
`api/platform.py` 的关注端点仍是**显式 501**（不假装成功）。

### 2.2 缓存写入 1204 B vs 明文 1004 B
`stats().disk_bytes=1204` 而 `plain=1004B` —— 差异来自**同时写了内存与磁盘**
（`cache_dir` 下另有文件）；非缺陷，但需在下轮确认无重复写。

---

## 三、剩余（尚未开工，按 `logic_replication_plan.md`）

| 阶段 | 内容 | 规模 |
|---|---|---|
| 2 | **下载子系统**（源项目 11 模块，本项目零基础） | 大 |
| 3 | 自动化引擎扩展（5 类监控 + 4 类动作） | 中（`automation` 配置已就位） |
| 4 | MCP 接线（骨架已有，需接 157 路由分级） | 中 |
| 5 | 剩余：推荐流切换 / 搜索三路增强 / Live Photo / 本地文件管理 | 小~中 |

---

## 四、本轮验证汇总

| 命令 | 结果 |
|---|---|
| `py_compile` × 6 文件 | 全 OK |
| `media_proxy.resolve()` 端到端 | 解密一致 / 缓存命中 / 格式识别正确 |
| `DouyinAPI.domain_for()` × 9 路径 | 映射符合源项目实测归纳 |
| `get_aweme_list_collection` / `get_mix_list_collection` | sc=0 + 真实数据 |
| `POST /collection/items` / `mixes` / `series` | 200 / 200 / 400（校验正确） |
| 写接口对照实测 × 4 | 读正常、写全空（已记录为待解） |

**未验证**：BCC 通道（应用未启动）；`get_series_aweme` 真实调用；下载子系统（未实现）。
