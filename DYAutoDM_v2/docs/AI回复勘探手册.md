# AI 回复勘探手册（DYAutoDM_v2）

> 生成：2026-09-24 · 分支 `design/better-douyin` · 基线版本 **v0.44.56**（提交 `b1e3f4f`）
> 性质：**项目专属事实档案**（非通用方法论）。含环境基线、链路骨架、缺陷根因档案、
> 实机记录、待办清单、踩坑留痕、取证命令速查。
> 用途：下次排查 AI 回复相关问题**先读本手册**，再动手 —— 避免重走本轮的弯路。

---

## 0. 适用与读法

| 你想做 | 跳到 |
|---|---|
| 刚接手，想知道环境和链路 | §1 / §2 |
| AI 又不智能了，查根因 | §3（四个缺陷档案） |
| 要真机验证 | §4 + §8 |
| 还有哪些没做 | §5 |
| 改绑定 / 改版本 / 改代码前避坑 | §7（踩坑留痕，最值钱的一节） |

---

## 1. 环境基线（勘探起点，先核对再动手）

| 项 | 值 |
|---|---|
| 项目根 | `C:\Users\LOX\Desktop\DYchajian\DYAutoDM_v2` |
| 后端 / 前端 | `backend/` · `frontend/` |
| 分支 | `design/better-douyin` |
| 当前版本 | **0.44.56** |
| **部署/测试数据根** | **`C:\temp\dyautodm_design`** |
| 会员 ID | `m17db0f8209156f26`（用户名 LOX2018） |
| 会员库 | `C:\temp\dyautodm_design\members\m17db0f8209156f26\data\dyautodm.db` |
| 测试账号 | **尚进工伤小助理**（另有 四川工伤张老师） |
| 构建/部署 | `python scripts/build_all.py`（约 7 分钟，三阶段） |
| 版本门禁 | `python scripts/check_version_sync.py` |

### 1.1 ⚠️ 版本源是 **6 处**，不是 3 处

```
package.json                （根）
frontend/package.json       ← 前端 __APP_VERSION__ 的真实来源
src-tauri/tauri.conf.json
src-tauri/Cargo.toml
src-tauri/Cargo.lock        ← [[package]] name="dyautodm-v2" 段（活的检查项）
backend/_build_version.py
```
改版后必须跑 `check_version_sync.py`，**`Cargo.lock` 是活的判定项**（L-10 已做破坏性验证），
只改前 5 处会门禁失败。

### 1.2 隔离路径铁律（已作废的旧认知）

- ❌ `C:\temp\dyautodm_test` —— **已删除，勿用**
- ✅ `C:\temp\dyautodm_design`
- `deploy.py` 内置门禁：拒绝部署进主分支环境（`FORBIDDEN_ROOTS`）

---

## 2. AI 链路骨架（溯源用）

### 2.1 会话私信回复链路

```
dm_messages 新消息（_tick 轮询 id > last_id）
   → AutoReplyWorker._handle          ai_reply.py:1101
      → Agent 解析 resolve_config     ai_agent.py:243
      → 黑名单 / 留资提取
      → _generate_reply               ai_reply.py:1194
         ① reply_kb.find_match（命中库，零 token）
         ② 档位 kb_only → 兜底话术
         ③ rag/free → build_system_prompt
              ├─ 专业库 pro_kb 语义 TopK(5) ≤6000字
              └─ _build_history（DB 读聊天记录）  ← 缺陷①在此
      → _send_delayed（随机延迟 8~20s）
      → dm_dispatch.submit（统一闸门）
```

### 2.2 直播私信文案链路

```
live_hook 收弹幕 → 构造 target{user_id,nickname,comment}
   → dispatch.submit（去重 + 延迟抖入队）
   → _do_send                          core/dispatch.py:393
        content = gen_dm_message（AI）优先
                  → 空则 pick_dm_message（词库）
                  → 再空则 target["comment"]（弹幕原文）← 缺陷 B-1 在此
   → dm_dispatch.submit_by_uid（闸门）
```

**AI 是否接入的判定层**：`core/auto_dm.py:346 _make_gen_dm_message`，三道否决：
`enabled` 为假 / `strict_level == kb_only` / `"live" not in scopes`。
任一道命中 → 返回 `None` → 回落词库。P1-2 后会把原因写进 `[live-ai]` 日志与
`/api/ai/live_dm_state`。

### 2.3 沉淀池与闸门（红线区）

| 机制 | 位置 | 语义 |
|---|---|---|
| `dm_uid_sink` | `database.py:215` | **(account, peer_uid)** 联合主键，同人只发一次，7 天冷却 |
| `dm_cross_sink` | `database.py:228` | **跨账号共享**，由 `sink_global_scope` 开关（实测 = **true**，冷却 90 天） |
| 闸门 | `dm_dispatch.submit_by_uid` | 陌生人 2 次/分、30 次/天 + 频控降权冷静 |

**实测分布**：`dm_uid_sink` 张老师 26 条 / 小助理 4 条；`dm_cross_sink` 26 条（全为张老师所发）。

---

## 3. 四大缺陷根因档案

### 缺陷① 上下文注入失效 —— AI「只看最后一条」（已修）

| 项 | 内容 |
|---|---|
| 现象 | AI 回复完全不像读过聊天记录，只依据最后一条消息 |
| **根因** | `_build_history` SQL 硬筛 `msg_type='text'`（`ai_reply.py:1323`），而 **WS 实时路径落库是数字串 `'7'`**（`api/messages.py:158-166` 映射表为证）。二者永不相等 → **history 恒空** |
| 实库证据 | 779 条 `dm_messages`：`text=687`、`'7'=88`、`'1'=2`、`'50010'=1`、`'27'=1` |
| 修法 | 白名单 `('_HISTORY_TEXT_TYPES = ("text","7")')`，参数化占位符；`HISTORY_LIMIT=6` 降级为兜底，改读配置 `max_history`（此前为悬空契约） |
| **验证** | 真实会员库：可见历史 **51 → 107 条**（净增 56）；小助理某会话 **0 → 1** |
| 脚本 | `backend/scripts/verify_history_context_fix.py` → 8/8，RED(0)→GREEN(5) |
| 状态 | ✅ 已修（v0.44.56） |

### 缺陷② 直播 AI 静默不生效（已修）

| 项 | 内容 |
|---|---|
| 现象 | 配了私信文本、开了 AI，却仍发固定文本 |
| **根因** | 调度顺序本就正确（AI 优先）。真因是**判定层把 AI 关掉**：`enabled=false` / `kb_only` / `scopes` 不含 live。且失败只写 `logger.warning`，**UI 无感** |
| Agent 默认值陷阱 | `ai_agent.py:75` 列表缺省 `["dm"]`；`AgentSection.tsx:93` 前端新建默认 `["dm"]`；`api/ai.py:506` 新建**不兜底**（`cfg = incoming`） |
| 修法 | 保守不动默认值；新增只读端点 `/api/ai/live_dm_state`；前端展示「AI 文案：已生效/未生效（原因：xxx）」 |
| **验证** | 31/31。真机实测端点返回 `active:false, reason_code:ai_disabled` |
| 状态 | ✅ 已修（v0.44.56） |

### 缺陷③ 直播只按单条弹幕生成（**能力缺口，未修**）

| 项 | 内容 |
|---|---|
| 现象 | AI 文案只依据单条弹幕随机生成，未按人聚合、未结合 Agent 人格 |
| 根因 | `generate_dm_for_live`（`ai_reply.py:1425`）入参就是**单条** comment，且 prompt 写死「第一条开场」 |
| 归属 | **P2（架构变更）**，需先落 ADR + 契约，本轮**明确未做** |
| 状态 | ⬜ 待做 |

### 缺陷④ 案例库 QA「牛头不对马尾」（已修）

| 项 | 内容 |
|---|---|
| 现象 | 提取的命中 QA 对不上，LLM 失败也照常入库 |
| 根因 | ① 抽对为纯规则「首 them + 其后首 me」，中间隔任意条仍配对<br>② LLM 失败 → `except` → `learned=[]` → **静默降级原样截断入库**<br>③ `find_match` 字符集 Jaccard 阈值 0.85，中文同义句天然命中不了 |
| 修法 | ① 加「不得跨越另一条 them」约束 ② 失败返 `ok=false + reason`，**不写库** ③ 语义 embedding 优先（阈值 0.40），Jaccard 降级为辅，向后兼容 |
| **验证** | 错配对 **42 → 0**；真实样本纠正 10 个会话；同义句语义 score 0.999 vs Jaccard 0.400 |
| 脚本 | `backend/scripts/verify_reply_kb_learning.py` → 19 项全绿 |
| 存量 | ⬜ **88 条存量错配未清洗**（P1-3 只阻断新增） |
| 状态 | ✅ 新增已阻断 / ⬜ 存量待清洗 |

---

## 4. 实机验证记录（2026-09-24 · 直播间 293736702286）

| 项 | 结果 |
|---|---|
| 应用 | `DYAutoDM_v2_0.44.56-debug.exe`（手工双击启动） |
| 登录 | LOX2018，00:53:51 成功 |
| 监听 | 引擎 `live_url=293736702286, max_target=4`，弹幕正常捕获 |
| 发送 | **4/4 全部真实投递成功**，DB 落库 `role=me` + `delivery_marker`（带服务端 msg_id） |
| 上限 | 发满 4 条自动停，**无第 5 条**（观察 2 分钟确认） |

**⚠️ 但 4 条内容全是「复读对方弹幕」**：
`那种电影最费钱` / `孙红雷和刘宇宁的铁证…` / `冲击奥斯卡` / `国内是什么时候对好莱坞去魅的？`

原因链（完整闭合）：
```
AI 未启用（enabled=false）→ gen_dm_message = None
    ↓
「测试」策略 dm_pool = [] → pick_dm_message = None
    ↓
两者皆空 → dispatch.py:429 兜底 content = target["comment"]
```

⇒ **本轮未验到 AI 智能性**（两个前提都未满足），但暴露了 B-1 缺陷。

---

## 5. 待办清单

### B-1 🔴 文案来源缺失时「复读弹幕」（**只记录，未改代码**）

- 处置口径（用户 2026-09-24 定）：**没配词库 且 没开 AI → 只监听、禁止私信，页面显著标识**
- 建议修法：**复用既有 `enable_send=False` 机制**（`auto_dm.py:838` ENG-017，走
  `RecordStatus.SKIPPED`，前端可见「已捕获未发」），**不新发明**
- 判定：`gen is None and 词库无 enabled 文案` → 置 `_send_ok=False` + status_msg
- UI：直播页需显著标识（当前 P1-2 只覆盖「AI 文案」状态，未覆盖本场景）

### B-2 🟡 零 Agent 配置

- 实测：`ai_agents` 键不存在、无绑定、`system_prompt` 长度 0、`merchant_name` 空
- **本轮已处置**：生成「工伤赔偿留资助手（唐律）」并绑定小助理（见 §6）

### B-3 🟡 案例库 88 条存量错配

- 例：Q`17581995587加我微信嘛` → A`不然取证行为不当`
- 待「重学 / 批量清洗」入口，或人工删改（UI 已支持删改）

### B-4 🟡 配置中心「标签」过于笼统 + 账号只能绑 Agent

**实测（2026-09-24 读源码，非推断）**：

| 项 | 现状 |
|---|---|
| 标签元数据 | 只有 `{id, name, created_at, updated_at}` —— **无业务分类、无板块归属** |
| 新建占位符 | `如：高频账号 / 保守账号` —— 提示的是**风控强度**，不是业务需求 |
| 受管分区 | `MANAGED_SECTIONS = ("send","live","capture")`（`config_tag.py:45`），`general` 不进标签 |
| 账号绑定 | TagSection 内**只能选标签**；AgentSection 内**只能选 Agent** —— 两个下拉各自独立 |
| 职责分工（既有设计） | Agent 管「回什么」，标签管「怎么发」 |

**问题**：账号绑定被**切成两个互不相干的下拉**，用户要分别去两处配置；
且标签没有业务维度（行业/场景），建出来的都是「高频/保守」这种**风控档位**，
不是业务标签（如「工伤-四川」「车险-华东」）。

**建议（待用户拍板，未动手）**：见 `docs/配置标签体系规划_B-4.md`（完整规划稿）。

**🔴 体系级缺口（实测，本轮最重要发现）**：

| 板块 | 策略实际存在哪 | 受标签指引？ |
|---|---|---|
| 直播监听策略 | `kv: live_room_configs`（`api/live_config.py:74`） | ❌ |
| 直播间管理 | `kv: live_rooms`（ADR-003 房间层） | ❌ |
| 视频采集策略 | **零配置项**（`capture` 27 字段全是捕获/存储，无采集策略） | ❌ |
| 私信 WS 回复 | 走 Agent，不属标签 | ❌ |
| 私信发送风控 | `app_config.send` | ✅ **唯一** |

`config_tag.resolve()` / `resolve_all()` **定义了但无人调用**；
标签体系全局只有 `dm_dispatch.py:120` 一处真实消费点。

**三个待拍板决策**：
- **D1** 直播策略按账号还是**按房间**绑标签（建议按房间：一场一策）
- **D2** 私信 WS 回复标签管到哪层（建议只管频率类，内容仍归 Agent）
- **D3** 采集策略需**新建 schema**（已核实：不是复用 capture）

**已做代码（向后兼容，未接线）**：`config_tag.py` +77 行 ——
`save_tag` 加 `biz_line/scene/sections`；新增 `bind_section/scope_of(section)/effective_bindings`；
`scope_of()` 不传 section 时与改造前逐字一致。
**无 API 路由、无前端入口**（等 D1~D3 拍板）。

### P2（架构变更，需先 ADR）

- 弹幕沉淀池（扩 `dm_uid_sink`，**不新建平行表**）
- 高价值人群标签筛查（方案 A 可配关键词权重表 / B LLM 现判 / C 混合 —— **待用户拍板**）
- 按 peer_uid 聚合该人全部弹幕再生成（缺陷③）
- 上下文隔离约束：**每次生成独立快照，键用 uid 不用昵称，生成完即弃**

---

## 6. 已生成的 Agent

| 项 | 值 |
|---|---|
| agent_id | `ag_88387f9ee4e34dc1` |
| name | 工伤赔偿留资助手（唐律） |
| scopes | `['dm','live','crawl']`（含 live，避开 P1-2 判定坑） |
| merchant_name | 唐律工伤团队（此前为空 → 提示词显示「本店」） |
| strict_level | `rag` |
| system_prompt | 685 字，分步节奏型（问伤情 → 问地区 → 判等级 → 钩子留资） |
| 绑定 | `ai_account_agent = {尚进工伤小助理: ag_88387f9ee4e34dc1}` |
| enabled | **False（保守：先建不启）** |

**Prompt 依据（真实数据抽取，非编造）**：
- `你联系方式多少，我给你算份赔偿清单，到时候按照清单找公司谈一下`
- `留个联系~方式，唐律下播帮你分析`（策略词库原文）
- `这样吧，我给你发点资料，你按照话术上面去和医院沟通`

**验证**：调真实 `ai_agent.list_agents()` + `resolve_config()`，确认 merchant/prompt 生效。

---

## 7. 🔴 踩坑留痕（本手册最值钱的一节）

| # | 坑 | 判据 / 正解 |
|---|---|---|
| 1 | **绑定键名写错** | 真实键是 **`ai_account_agent`**（`ai_agent.py:38`），不是 `ai_agent_bind`。写错**不报错**，Agent 永远解析不到 → 静默失效。改绑定前先 grep `_KV_BINDINGS` |
| 2 | **拿注册表当运行态** | `live_rooms`（持久化）里有旧房间 992931212705，但运行日志 `live_url=293736702286` 才是事实。**以日志/进程为准，不以配置为准** |
| 3 | **GUI 从命令行拉不起来** | `cmd //c start` 起应用零日志、无进程（MSYS 环境传变量给 Windows GUI 失败）。改用**直接起 sidecar** 绕过：`./dyautodm-backend-*.exe --port 8000` 带 `DY_APP_ROOT`+`DY_MEMBER`+`DY_MEMBER_KEY` |
| 4 | **会话 token 会过期** | `_SESSION_TTL`，`.session.json` 超期 → `restore_persisted_session()` 返回 False → 全接口 401。判据：`/api/member/state` 不可信，要查日志有无「登录成功」 |
| 5 | **裸 fetch 丢鉴权头** | 前端直连 `fetch` 缺 `X-Member-Token` → 会员门禁 401。**必须走 `api/client.ts` 的 `request()` 封装** |
| 6 | **WS 落库是数字串 msg_type** | `'7'`=文本，不是 `'text'`。任何按 msg_type 过滤的地方都要用映射表（`api/messages.py:158-166`） |
| 7 | **版本源 6 处含 Cargo.lock** | 见 §1.1 |
| 8 | **patch 工具破坏 Python 缩进** | 深层嵌套多行替换会把 12sp 撑成 24sp → IndentationError。改用 `terminal` 跑字节级脚本；**注意 bytes 字面量不能含中文**（会 SyntaxError），改用 `encode('utf-8')` |
| 9 | **CRLF / LF 混用** | `reply_kb.py` 是 CRLF，`ai_reply.py` 是 LF。子 Agent 曾卡在此处导致零产出 |
| 10 | **子 Agent 报告可能不落盘** | 必须按绝对路径逐个读文件核实，**不采信回执自述** |
| 11 | **跨账号沉淀池会拦住第二个账号** | `sink_global_scope=true`（实测）→ 表现为「发不出」而非「发给错的人」。多账号测试前需知悉 |
| 12 | **调试版构建含测试白名单限制** | sidecar 日志明确警告，禁止对外发布 |

---

## 8. 取证命令速查

```bash
# ① 环境/版本
cd /c/Users/LOX/Desktop/DYchajian/DYAutoDM_v2
python scripts/check_version_sync.py          # 6 处版本齐平
git status --porcelain | wc -l                # 并发门禁

# ② 起后端（绕过 GUI）
cd /c/temp/dyautodm_design
export DY_APP_ROOT="C:\temp\dyautodm_design"
export DY_MEMBER="m17db0f8209156f26"
export DY_MEMBER_KEY="KnjQufpZfou9ZO2fyRN4_cI1J37HWKTRaEikW4JHNrE="
./dyautodm-backend-x86_64-pc-windows-msvc.exe --port 8000

# ③ 查接口（需 token，从 members/.session.json 取，过期需重登）
TK=$(python -c "import json;print(json.load(open(r'C:\temp\dyautodm_design\members\.session.json'))['token'])")
curl -s -H "X-Member-Token: $TK" http://127.0.0.1:8000/api/ai/config
curl -s -H "X-Member-Token: $TK" "http://127.0.0.1:8000/api/ai/live_dm_state?account=尚进工伤小助理"

# ④ 真实数据勘 msg_type 分布
python -c "
import sqlite3
c=sqlite3.connect(r'C:\temp\dyautodm_design\members\m17db0f8209156f26\data\dyautodm.db')
print(list(c.execute('SELECT msg_type,COUNT(*) FROM dm_messages GROUP BY msg_type')))"

# ⑤ 验证脚本（可复跑）
cd backend
python scripts/verify_history_context_fix.py     # 8/8
python scripts/verify_live_ai_observability.py   # 31/31
python scripts/verify_reply_kb_learning.py       # 19 项

# ⑥ 前端类型检查
cd frontend && npx tsc --noEmit
```

---

## 9. 本轮结论（一句话）

**AI「不智能」不是模型问题，是工程问题**：上下文注入链路静默失效 + 零 Agent 人格 +
判定层静默关闭 AI 且无 UI 反馈。三者叠加，模型再强也发挥不出来。
