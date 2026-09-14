# 源项目功能 · 逻辑层复现清单

> **任务**：用户 2026-09-14「**将源项目所用的功能全部在逻辑层复现**」
> **分支**：`design/better-douyin`　|　**基准**：`anYuJia/better-douyin` v1.1.18
> **口径**：**逻辑层**（后端能力 + 接口 + 数据流），UI 层另论。
> **取证**：① `README.md`「功能能力」节（源项目自述）② 基座 `dy_apis/douyin_api.py` 46 方法实测
> ③ `docs/reverse_interface_spec.md`（逆向情报）④ 本项目 `backend/api/*.py` 19 模块现状。

---

## 一、源项目功能全集（README 自述，4 板块 20 项）

### A. 内容获取与浏览（5 项）
| # | 功能 | 逻辑层要素 |
|---|---|---|
| A1 | 首页快捷入口（搜索用户/解析链接/推荐视频/收藏视频）+ 本地统计 | 聚合统计 |
| A2 | 按昵称/抖音号/UID 搜索用户；查看资料/作品/粉丝/关注/获赞 | 搜索 + 资料 + 关系列表 |
| A3 | 解析分享链接/短链/完整URL（单视频、图集、Live Photo） | URL 解析 |
| A4 | 浏览用户作品/推荐/点赞/收藏/收藏合集/合集内作品 | 6 类列表 |
| A5 | 推荐流精选/推荐切换、滚轮浏览、快播、一键/批量下载 | 推荐流 + 批量 |

### B. 下载与本地管理（4 项）
| # | 功能 | 逻辑层要素 |
|---|---|---|
| B1 | 下载单个/用户作品/搜索/推荐/点赞/收藏/合集 | **下载流水线** |
| B2 | 任务队列进度、实时日志、暂停/恢复/取消、失败提示、重复跳过 | 任务状态机 |
| B3 | 按作者/合集归档；下载目录、命名模板、并发数、质量 | 归档策略 |
| B4 | Live Photo 静图/视频；原声/BGM；写入任务与记录 | 媒体变体 |
| B5 | 「我的下载」文件视图/作品视图、搜索、筛选、分页、播放、定位、删除、目录同步 | 本地文件管理 |

### C. 播放、互动与自动化（7 项）
| # | 功能 | 逻辑层要素 |
|---|---|---|
| C1 | 沉浸式播放器（视频/图集切换、进度/音量/倍速/清晰度、自动下一条、失败重试） | 播放器状态 |
| C2 | 播放器内下载/点赞/收藏/分享/看评论/定位评论/生成评论草稿 | 互动动作 |
| C3 | 通知中心（点赞/评论/关注通知、后台刷新、间隔设置、跳转） | 通知拉取 |
| C4 | 好友模块（列表/在线状态/关注列表/私信会话/历史同步/未读/分享卡片） | **IM 私信** |
| C5 | AI 互动（OpenAI Compatible；生成评论/私信回复/内容分析） | **AI** |
| C6 | 自动监控（推荐流/好友私信/通知/评论区/创作者作品更新）+ 关键词/阈值/间隔/单轮上限/日志 | **自动化引擎** |
| C7 | 自动评论/私信草稿、自动点赞、自动收藏、关注后回关、收到分享回传媒体 | 自动动作 |

### D. 桌面与扩展（5 项）
| # | 功能 | 逻辑层要素 |
|---|---|---|
| D1 | 本地保存（Cookie/账号/配置/历史/缓存/文件） | 本地存储 ✅ |
| D2 | 内置登录、手动 Cookie、账号校验、多账号切换/删除 | 账号管理 ✅ |
| D3 | 轻量运行时、原生窗口、侧栏收展、小屏适配 | **UI 层**（非本次） |
| D4 | **本机 HTTP MCP 服务**（Codex/Claude Code/OpenClaw 授权读内容、控下载、显式确认写操作） | **MCP** |
| D5 | 跨平台发行、自动更新、更新代理、checksum、启动完整性 | 发行 |

---

## 二、本项目现状对照（实测）

### 已有（基座 46 方法，可直接接）
```
搜索    search_user / search_some_user / search_general_work / search_some_general_work
        search_video_work / search_some_video_work / search_live / search_some_live
用户    get_user_info / get_user_all_work_info / get_user_work_info
关系    get_user_follower_list / get_user_following_list
        get_some_user_follower_list / get_some_user_following_list
内容    get_feed / get_work_info / get_collect_list / get_user_favorite
评论    get_work_all_comment / get_work_out_comment / get_work_inner_comment
        get_work_all_out_comment / get_work_all_inner_comment
        publish_comment（写）
互动    digg / collect_aweme / remove_collect_aweme / move_collect_aweme（写）
通知    get_notice_list / get_some_notice_list
IM      get_message_by_init / get_conversation_list / get_conversation_list_all
        create_conversation / send_msg / get_im_user_info / get_my_uid / get_my_sec_uid
直播    get_live_info / get_webcast_detail / get_rank_list / get_live_production*
```

### 缺失（需新建）
| 源项目功能 | 现状 | 复现要点 |
|---|---|---|
| **B1~B5 下载子系统**（11 模块） | ❌ 完全没有 | 独立流水线：取址→传输→选质→状态机→收尾 |
| **D4 MCP** | ⚠️ `backend/mcp/` 已有骨架（7 模块） | 需接线到实际工具面 |
| **C6 自动化引擎** | ⚠️ 有 `auto_dm/`（直播私信向） | 需扩到"推荐流/评论/通知/创作者监控" |
| **C1/C2 播放器** | ❌ 无 | 前端为主；逻辑层需**媒体代理** |
| **C7 自动动作** | ⚠️ 部分（直播私信） | 需加 评论/点赞/收藏/回关/回传媒体 |
| **A5 推荐流切换/批量下载** | ⚠️ 有 `get_feed` | 需加精选/推荐切换 + 批量 |
| **收藏合集** | ⚠️ 有 `get_collect_list` | 需加 mix/listcollection 系 |
| **Live Photo** | ❌ 无 | 媒体变体处理 |
| **B5 本地文件管理** | ❌ 无 | 文件视图/目录同步/定位 |

---

## 三、逻辑层复现方案（按依赖排序）

### 阶段 1：媒体代理层（C1/C2 与 B1~B5 的共同依赖）
**对应源项目 `src/media_proxy_cache.rs`**
```
新建 backend/services/media_proxy.py：
  · decrypt_image(cipher, skey)  —— 收敛散落 7 处的 AES-256-GCM 解密为唯一实现
  · 磁盘缓存 + LRU（对应源项目 cacheable / cache_v2）
  · 对外 URL 契约 /api/messages/origin_image/{filename}（已存在，收敛为走 proxy）
  · 分块参数 aes_chunk_size = 524288（源项目实测值，512KB）
```

### 阶段 2：下载子系统（B1~B5）—— 最大缺口
**对应源项目 `src/downloader/` 11 模块**
```
新建 backend/downloader/：
  media_request.py   取址（play_addr / bit_rate / fallback_url）
  quality.py         选质（bit_rate_list 排序）
  media_transfer.py  传输（分块 + 断点续传 + x-amz 签名）
  tasks.py           任务状态机（queued/running/paused/canceled/done/failed）
  control.py         暂停/恢复/取消
  filename.py        命名模板 + 作者/合集归档
  completion.py      收尾（写记录 .downloaded / download_record.json）
  retry.py           重试
  request_policy.py  限流中枢（对应源项目同名模块）
  events.py          事件（进度上报）
  batch.py           批量（用户作品/搜索/推荐/点赞/收藏/合集）
```

### 阶段 3：自动化引擎扩展（C6/C7）
**照源项目 `auto_*` 模型**（已在本分支 `app_config.automation` 建了 12 参数）
```
新建 backend/automation/（或扩 auto_dm/）：
  monitors：feed / friends / notices / comments / creator
  actions ：comment / dm / like / collect / follow_back / return_shared_media
  闸门    ：max_actions_per_run + send_delay_ms + scan_interval_seconds
  过滤    ：match/exclude_keywords + min_digg|comment|play_count
```

### 阶段 4：MCP 接线（D4）
**对应源项目 `src/mcp.rs` + `douyin-dl`（stdio 单入口）**
```
backend/mcp/ 已有 registry/config/audit/server/__main__：
  · 把 157 路由按 READ/WRITE 分级登记进 registry
  · 只读默认；写操作 require_confirmation（confirm=true 单次放行）
  · Token 轮换即时失效；审计脱敏 + log_retention 环形保留
  · python -m backend.mcp serve（stdio）
```

### 阶段 5：内容面补齐（A1~A5）
```
api/platform.py 补充：
  · 收藏合集（mix/listcollection + mix_infos）
  · 合集内作品（series/aweme）
  · 双域名策略（www / www-hj，照逆向情报 §1.1）
  · 推荐流 refresh_index 切换（精选/推荐）
  · 搜索增强（昵称/抖音号/UID 三路）
```

---

## 四、验收判据

| 阶段 | 判据 |
|---|---|
| 1 媒体代理 | 解密实现 `grep _decrypt` 收敛为 **1 处**；缓存目录有文件 |
| 2 下载 | 能真实下载 1 个作品并落盘；任务状态机 6 态可观测 |
| 3 自动化 | 5 类监控可开关；4 类动作受 `max_actions_per_run` 限制 |
| 4 MCP | `python -m backend.mcp serve` 可启动；只读免确认、写被闸 |
| 5 内容面 | 6 类列表可拉取；双域名可切换 |

---

## 五、待用户确认

| # | 问题 | 建议 |
|---|---|---|
| Q1 | **下载子系统**是本项目**原本不做**的业务（主分支明确"不涉及"）。现在照源项目复现，**确认要做吗**？ | 确认（用户已说"全部"） |
| Q2 | 阶段顺序：按依赖 **1→2→3→4→5**？ | 建议按此（媒体代理是下载的前置） |
| Q3 | **UI 层**（播放器/11 项导航）是否同步做，还是先只做逻辑层？ | 先逻辑层（用户本次说"逻辑层"） |

---

## 六、工作量提示（诚实评估）

| 阶段 | 规模 | 说明 |
|---|---|---|
| 1 媒体代理 | 小 | 收敛既有散落实现 |
| 2 下载子系统 | **大** | 源项目 11 模块，本项目零基础 |
| 3 自动化扩展 | 中 | 有 `auto_dm/` 可扩 |
| 4 MCP | 中 | 骨架已有，需接线 |
| 5 内容面 | 小 | 基座方法已有 |

**建议先做 1 + 5**（小、快、可立即验证），再做 4，最后 2 + 3（大块）。
