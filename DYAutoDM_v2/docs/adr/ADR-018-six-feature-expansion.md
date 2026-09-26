# ADR-018 · 六项功能扩展（标签中心 / 概览增强 / 评论采集 / 定时任务 / 直播搜索 / 日夜主题）

> 状态：**Accepted**（用户 2026-09-27 拍板）→ 实施中
> 分支：`design/better-douyin` · 基线 **v0.45.40**
> 上游情报：本 ADR **先侦察后设计**（vendor `cv-cat/DouYin_Spider` commit `4479ea784b`；
>           追踪脚本实跑 `status=same`，基线未变动）

---

## 1. 背景与硬约束

用户 2026-09-27 提出六项功能需求，并追加硬约束：
> 「所有新增的方案都必须去上游收集情报，不能闭门造车」

**侦察结论（决定性）**：六项中 **4 项上游已有现成能力，且本地已迁移**；
只有「定时任务中心」上游无（DouYin_Spider 是纯 API 库，无调度）⇒ 唯一真正的新基建。

| 需求 | 上游符号 | 本地落点 | 性质 |
|---|---|---|---|
| 评论采集 | `get_work_out_comment` / `get_work_all_comment` / `get_work_inner_comment` | `dy_apis/client_comments.py` L48/110/137/196/221 | 接线+前端 |
| 热门视频 | `search_general_work` / `search_video_work` | `client_search.py:46/214` `client_video.py:111/149` | 接线+前端 |
| 搜索直播间 | `search_live` / `search_some_live` | `client_search.py:249/306` | 接线+前端 |
| 发评论 | `publish_comment` | `client_comments.py:237` | 已有 |
| 发私信 | `send_msg` 系列（vendor）/ BCC 链路（本仓） | `dm_dispatch` | 已有 |
| **定时任务中心** | ❌ 上游无 | 仅 `kb_maintain` 单用途调度器 | **新建** |
| 标签配置中心 | ❌ 上游无（本项目自有概念） | `services/config_tag.py` 274 行**未接线** | 接线 |
| 日夜主题 | 移植自 `satelite-proxy` | `theme/ThemeContext.tsx` 已有双主题 | 加 UI 入口 |

---

## 2. 决策（用户 2026-09-27 拍板）

| # | 决策 | 取值 | 依据 |
|---|---|---|---|
| **D1** | **自动外发默认态** | 🔴 **`enabled=False`（休眠）** | 用户选 A。F3+F4 组合 = 定时自动向陌生人批量发私信，是本项目**迄今最大风控敞口**。与 T3 高价值筛查同款处理：能力建成就绪但休眠，用户要开再开。违反用户已定红线（不主动批量查昵称 / 宁可保守）的成本远高于便利性 |
| **D2** | **ADR 与审计口径** | 六项**一起出 ADR-018**，**统一触发一次全库审计** | 用户选「六项一起」。放弃原「F4 单独出 ADR」建议。⇒ 本 ADR 落地后须跑一次全库审计（含 D-08 存量扫描维度），审计期间功能冻结 |
| **D3** | **实施范围** | **六项全推**，遇需拍板项按 D1/D2 执行 | 用户选「六项全推」 |
| **D4** | **外发闸门（即便用户手动开启）** | 三重：额度 / 间隔 / 时段；**复用既有 `dm_dispatch` 限流，不自造**；每条走 M-5 投递验证钩子，无回执不认成功 | 铁律「不自造轮子」+ 用户「验证后才汇报」 |
| **D5** | **实施顺序** | F6 → F2 → F5 → F1 → F3 → F4 | 零风险先做、最高风险最后；F4 单独升版便于归因 |
| **D6** | **搜索类端点** | 一律走 `signed_url()` | M-2 已闭环：不签名必被 Argus 403（46B `Blocked by ArgusSecurityPlugin`） |
| **D7** | **昵称红线不破** | 评论采集**只取评论自带昵称**，绝不回调 `bulk_user_info` / `get_im_user_info` | 用户风控红线（死代码列表已登记） |

---

## 3. 分项实施规格

### F6 · 日夜主题切换（零风险，先做）
- 现成：`theme/ThemeContext.tsx` 已有 `setTheme` + 持久化 + `applyThemeToDom`（day/aerospace）
- 缺口：无用户可点的入口
- 实施：设置页或顶栏加切换控件，调 `useTheme().setTheme`
- 验收：`tsc --noEmit` exit=0；切换后 `document.documentElement.style.colorScheme` 随之变化

### F2 · 概览页数据组件丰富化
- 现有 3 Section（AI 运行时 / 能力健康 / 共享）+ `overview-page.tsx` 329 行
- 补：今日发送量趋势 / 账号凭证健康 / 直播在线状态 / 任务队列 / 错误码 TOP-N
- **数据全部取自既有探针，零新采集**

### F5 · 直播监听页 · 搜索关键词上架直播间
- `search_live`（`client_search.py:249`）→ 前端「搜索发现」Tab → 一键上架到 `live_rooms`（ADR-003 已有 CRUD）→ 绑定策略
- 走 `signed_url`（D6）

### F1 · 标签配置中心接线
- 后端：`services/config_tag.py` 已实现（`_KV_BIND_SECTION` 板块级绑定 +82 行）
- 缺口：前端 `api/client.ts:1423` 已声明 `ConfigTagSummary` 但**后端无 `/api/settings/tags` 路由**
- 实施：补路由 + 前端接设置页入口
- 开放项：D1 直播按房间绑（建议）/ D2 私信 WS 只管频率（建议）/ D3 采集策略新建 schema

### F3 · 内容页播放 + 评论同时采集 + 支持发私信
- 后端评论采集**已具备**（5 方法）⇒ 只需前端 Tab
- 播放已闭环（v0.45.9）
- 「发私信」按钮 → 复用 `dm_dispatch`，**默认 `enabled=False`**（D1）

### F4 · 定时任务中心（唯一新基建 · 最高风险）
- 新建 `services/task_scheduler.py`：通用 cron/interval 调度 + 任务 CRUD + 执行审计
- **复用 `kb_maintain` 的 `_tick`/`_schedule` 范式**（不自造轮子，铁律）
- 任务类型：① 关键词定时处理 ② 热门视频评论采集 ③ 自动发私信
- 🔴 **默认 `enabled=False`**（D1）；三重闸门（D4）；每条走投递验证

---

## 4. 验收判据

| 层 | 判据 |
|---|---|
| 构建 | `tsc --noEmit` exit=0；`npm run lint` exit=0 |
| 门禁 | `check_contracts.py` G0~G14 全 PASS；`check_iron_rules.py` 通过；`check_version_sync.py` 6 处齐平 |
| 回归 | 全量 unittest 失败集合与**基线逐条一致**（零新增回归） |
| 风控 | 自动外发默认关闭可被机械断言（负控：改成 True 门禁变红） |
| **审计** | D2 ⇒ ADR-018 落地后跑一次**全库审计**（含 D-08 存量扫描维度） |

---

## 5. 未做 / 诚实标注

- F1 的 D1/D2/D3 三个子决策**尚未拍板**，实施时按建议值落地并显式上报
- F3/F4 的**真机端到端**（真实评论采集、真实自动发送）需登录态 + 真实内容，今晚无法验证
- 上游 `astrbot` 等因 GitHub 匿名限流未取到最新状态（非故障，可稍后重试）
