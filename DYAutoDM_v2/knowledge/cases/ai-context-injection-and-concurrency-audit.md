# AI 上下文注入 + 并发承载实测审计（v0.44.67）

> 分支 `design/better-douyin` · 基线 v0.44.67（`599171d`）· 2026-09-25
> 触发：用户问「AI 回复的上下文注入机制到底怎么实现」「有没有用到视觉模型」
> →「能否满足该会话的全文注入」「有图片时如何传递」「并发 5~10 人怎么办」
> 性质：**只读实测审计**（不改业务代码）+ 上游情报核查。结论含**三处对我方既有认知的推翻**。
> 取证方式：**数据库副本沙箱 + 独立进程 + 插桩 `requests.post`**。
> **未写用户运行中的生产库**（应用 `DYAutoDM_v2_0.44.67-debug.exe` 于 12:19:52 启动，
> 期间配置发生过变更 —— 全部读数改用 `cp` 快照副本，见 §0 并发门禁）。

---

## 0. 取证环境与并发门禁（先决）

| 项 | 值 |
|---|---|
| 生产库（**只读**） | `C:\temp\dyautodm_design\members\m17db0f8209156f26\data\dyautodm.db` |
| 快照副本 | `%LOCALAPPDATA%\hermes\profiles\lox\cache\scratch\snap_123843\` |
| 可写沙箱 | `…/scratch/live_125136\root\`（DB 副本，可注入测试数据） |
| 运行实例 | `DYAutoDM_v2_0.44.67-debug.exe` PID 18704（用户手动启动） |
| 后端 | `dyautodm-backend-x86_64-pc-windows-msvc.exe` PID 2888，8000 监听 |
| 插桩 | `requests.post` → 记录 URL / model / 载荷特征 / 状态 / 耗时 |

**并发写者证据（实测）**：同一 `model_hub` 键两次读取内容**不同** ——
12:33 读到 2 模型（sem=`md_5543a34b8789`），12:38 读到 7 模型 + `书生` 提供商。
⇒ 用户运行中的应用在会话中途写入 KV。**处置：此后全部读数改用 `cp` 快照副本，不再直读生产库。**

⚠️ **首次读取的错判已订正**：曾报「提供商 key 未填 / provider=None」——
实为①字段名错误（真名 `provider_id`）②该次读数为非原子读。经快照核实：
**三个提供商 key 全部已填且校验通过**（`freellmapi` / `freellmapi向量` → `{ok:True,'密钥有效'}`；
`书生` → `{ok:True,'拉到 11 个模型'}`）。

---

## 1. 设计意图（Design Intent）

| 项 | 内容 |
|---|---|
| 模块 | `backend/services/ai_reply.py`（`AutoReplyWorker` / `AIClient` / `build_system_prompt`） |
| 设计契约 | ① 每轮注入该会话历史上下文，使 AI 回复符合真实聊天场景；② 图片消息经视觉模型转文本后参与回复；③ 多账号/多会话场景下实时接待 |
| 预期行为 | 同一会话的 AI 每轮所见 = 其上一轮所见 + 本轮新消息；图片信息在该会话内可持续被引用；并发会话互不阻塞 |
| 设计假设 | `id`（DB 自增）顺序 ≡ 消息真实时序；`vision_enabled` 等开关是功能唯一门；单线程串行不影响实时性 |

---

## 2. 观测到的偏差（Observed Deviation）

```json
{
  "design_intent": {
    "module": "services.ai_reply（上下文装配 + 视觉链路 + 并发模型）",
    "design_contract": "所轮所见一致（turn N 所见 = turn N+1 所重放）；图片信息会话内可持续；并发不阻塞",
    "expected_behavior": "AI 每轮见到该会话完整上下文，图片描述可持续引用，多会话并行处理",
    "assumptions": ["id 顺序 ≡ 真实时序", "开关是功能唯一门", "串行不影响实时性"]
  },
  "observed_deviation": {
    "deviation_type": "data",
    "deviation_point": "ai_reply.py:_build_history:1363（排序基准与白名单）/ :1195-1207（图片描述不落库）/ ai_reply.py:_tick:1077（单线程串行）",
    "deviation_from_expectation": "① 上下文按 id 取最近 10 条，实测同会话存在 2 次 ts 时间倒挂 → 注入错序；② 图片描述只在当轮内存，白名单排除 msg_type='27' → 下一轮起图片信息彻底蒸发；③ 单 daemon 线程逐条串行，10 并发实测端点可并行，瓶颈在代码"
  },
  "error_code": "AI-050~AI-053",
  "severity": "error",
  "suggested_actions": [
    {"action_id": "order-by-ts", "automatic": false, "idempotent": true},
    {"action_id": "persist-image-desc", "automatic": false, "idempotent": true},
    {"action_id": "per-session-concurrency", "automatic": false, "idempotent": true},
    {"action_id": "drop-auto-first-candidate", "automatic": false, "idempotent": true}
  ]
}
```

---

## 3. 实测证据（逐条可复跑）

### 3.1 上下文注入的五个截断点（**"全文注入"当前做不到**）

代码实现（`AIClient.chat`，`ai_reply.py:690`）实际组装的 messages 为：

```
[0] system = build_system_prompt(...)     ← 人格 + 《资料》RAG
[1..n]     = session_history[uid]（恒空）+ history_extra（DB 历史）
[末] user   = 当前客户消息
```

**① 条数硬上限（两处口径同源、但都是 10）**

| 位置 | 代码 | 作用 |
|---|---|---|
| `_history_limit()` | `ai_reply.py:1345-1361` | SQL `LIMIT max_history`，clamp `[1,50]` |
| `AIClient.chat` | `ai_reply.py:703` | `history[-int(cfg.get("max_history",10)):]`，**无 clamp** |

⚠️ 同一语义离散在两处，且**仅一处有 clamp** —— 契约缺口（改时须一并收口）。

**② 类型白名单排除多模态**
`_HISTORY_TEXT_TYPES = ("text","7")`（`ai_reply.py:197`）。实库 898 条 → 滤后 864 条，掉 34 条：
`delivery_marker` 29 / `'1'` 3 / `'50010'` 1 / **`'27'`（图片）1**。
对照权威映射 `backend/api/messages.py:158-166`：`7=text, 5=sticker, 17=voice, 27=image, 8=video`
⇒ **图片、表情包、语音、视频全部进不了上下文**。

**③ `'27'` 语义未纳入语义层**（与 ② 伴生；`_front_type` 已定义 `'27'→'image'` 但白名单未采纳）

**④ RAG 阈值形同不筛**
`build_system_prompt` 调 `pro_kb.semantic_topk(text, pro, k=5, threshold=0.0, max_chars=6000)`
（`ai_reply.py:330`）。**阈值 0.0 ⇒ 全部候选通过**。实测查询「工伤十级大概能赔多少钱」，
3 条候选全被注入，**含「无关主题 / 天气」那条**（`picked=3`）。

**⑤ 图片描述不落库**（见 §3.2）

**实测规模对照**（全库 252 会话，白名单口径）：

| 单会话字数 | 可全文注入 | 占比 |
|---|---|---|
| ≤ 2000 | 243/252 | 96% |
| ≤ 4000 | 245/252 | **97%** |
| ≤ 8000 | 247/252 | 98% |
| ≤ 32000 | 252/252 | **100%** |

单会话最大 **29,304 字 / 30 条**；中位 **41 字 / 2 条**。
⇒ 全文注入对 **97% 会话廉价可行**，仅 5 个长会话需兜底（与「全库注入」是两回事）。

### 3.2 🔴 图片链路全链断裂（实测三连）

**代码路径**：`_handle:1195` 判 `text.startswith("[图片]") or msg_type=="27"` → `_describe_image:1288`
→ `origin_image_resolver.resolve`（AES-256-GCM 解密 / HEIC 转码）→
`describe_image_failover:874` → `POST /chat/completions`（OpenAI 多模态载荷）
→ 成功则 `text = f"[客户发来图片] {vision_text}"`（`:1207`）**仅喂本轮 `_generate_reply`**。

**实测证据——同一真实会话 `0:1:99971134155:3887506227210423`（账号「四川工伤张老师」）**：

```
id=10410 me    "工友你好，可以发下你的病例，我看下等级"
id=10428 them  [图片]              ← msg_type='27'，text 只有 '[图片]'（无 URL）
id=10430 me    "在哪个地区受伤的"
id=10432 me    "都是保守治疗吗"
id=10436 them  "山西 保险是厦门的"
```

| 轮次 | 历史里含图片信息？ |
|---|---|
| 图片到达时（10428） | **False**（历史 5 条） |
| 紧接着回复轮（10430） | **False**（历史 5 条） |
| 客户说"山西…"（10436） | **False**（历史 7 条） |

⇒ **客户发了病例照片，之后每一个回合都不知道这张图存在过**；对话从"要看病例"直接跳到"问地区"。

**可行基础（实测，非推断）**：
- 图片 URL `x-expires=1821675600` = **2027-09-23**，仍剩 **363 天**（未过期）
- `skey` + `origin_url` 均持久化在 `extra`（实库原文已取）
- 解密产物已落盘 `data/origin_images/`（实测有实体文件，如 `7682289942861628965_80edb0d6.png` 7868B）
⇒ **历史图片事后补描述可行**。

⚠️ **附带实测订正**：`_describe_image` 里「从 text 兜底取 URL」的正则
`re.match(r"\[图片\]\s+(\S+)", row["text"])` 对本项目真实数据**匹配不到**
（真库 `text` 恰为 `'[图片]'`，URL 只在 `extra`）—— 该兜底分支是死路。

### 3.3 🔴 排序基准错误（本轮新发现，全文注入会放大）

`_build_history` 用 `ORDER BY id DESC`。但 **`id`（DB 自增）≠ `ts`（平台消息时间）**。
实测同一会话 `0:1:99971134155:3887506227210423` 存在 **2 次时间倒挂**：

```
id=10554  ts=09-23 13:31
id=10619  ts=09-14 12:20   ← 比上一条早 9 天
id=10620  ts=09-14 12:20
id=10621  ts=09-14 14:12
```

同一会话、同一查询条件，**两种排序取到不同内容**（实测对照，看"模型认为最近发生的事"= 序列尾部）：

```
按 id  →  … '15234611904' / '好' / [09-14]只能发一条 / [09-14]我是唐律助理 / [09-14]陌生人确认
按 ts  →  '做了劳动能力鉴定了' / '现阶段得把赔偿算清楚…' / '你联系方式多少…' / '15234611904' / '好'
```

⇒ **按 id 会把 9 天前的开场白当作"刚刚发生"**。截断 10 条时尚能侥幸遮住，
**全文注入会把错序完整暴露**。`dm_messages` 已有 `ts REAL` 列，无需改表。

### 3.4 视觉模型：能力可用、消费点被门拦（实测）

**能力层（绕过门直连，证据可达）**：
```
POST http://127.0.0.1:31415/v1/chat/completions
model='auto'  max_tokens=200  has_image_url=True   status=200  6720ms
返回 → "图片显示为纯红色色块，未提供任何与产品咨询、故障或需求相关的信息。"
```
（送 1×1 红色 PNG，**识别正确**）

**消费层（真实业务入口）**：`cfg.vision_enabled = False` ⇒ `_describe_image` 首行即 `return None`
⇒ 图片消息恒走固定兜底（`fallback_reply(cfg,"image")`），**出网 0 次**。

**嵌入模型：真被调用**（3 次真实 HTTP）：
```
[1] 200 1919ms model='llama-nemotron-embed-vl-1b-v2' /v1/embeddings input_len=1
[2] 200  891ms 同上                                   input_len=3
[3] 200  663ms 同上                                   input_len=1
```
路径：`build_system_prompt` → `pro_kb.semantic_topk` → `_embed_failover` → `/embeddings`。
端到端证据：`_LAST_PROMPT_STATS = {'kb_mode':'semantic','pro_kb_chars':300,'picked':3}`
（**走真语义路径，非降级**）。

**🔴 实测抓出的浪费**：`semantic_topk` 第 538 行调 `current_sem_model()`，
而后者内部执行 `_embed_failover([" probe "])` —— **只为问模型名就真发一次 HTTP**。
证据即上表 `input_len=1` 的两条探针请求（1919ms / 663ms），**每次 RAG 组装都多烧一次网络往返**。

### 3.5 🔴 契约缺口：`sem_enabled` 对专业库 RAG 形同虚设

| 消费点 | 是否读 `sem_enabled` |
|---|---|
| `pro_kb.py`（专业库 RAG，`semantic_topk`） | **零处引用** —— 直接走 `resolve_chain_hub('ai_sem')` |
| `ai_reply.py:578`（旧 `_embedRemote` 路径） | ✅ 读 |
| `reply_kb._sem_ready`（回复库语义级） | ✅ 读 |

实测后果：`sem_enabled=False` 时，**回复库语义级被拦（0 次出网，实测确认）**，
但**专业库 RAG 不受影响、照常语义检索**。⇒ 开关语义与实现不一致（Canonical Contract 类缺陷）。

### 3.6 并发承载（实测：端点扛得住，瓶颈在代码）

**端点并发实测**（`http://127.0.0.1:31415/v1`）：

| 测试 | 结果 |
|---|---|
| chat 同发 5 | **5/5 全 200**，墙钟 5.7s（串行约 14s） |
| embed 同发 5 | **5/5 全 200**，墙钟 3.5s（串行约 14s） |
| **chat 同发 10** | **10/10 全 200**，墙钟 **2.3s**（串行估算 12.4s） |

⇒ **端点支持并发，不是瓶颈**。

**🔴 但 10 并发暴露 `auto` 路由漂移（实测硬证据）**

同一次 10 并发，**10 个请求被 4 个不同模型服务**：

| 实际服务模型 | 次数 | 输出质量 |
|---|---|---|
| `ZhipuAI/GLM-5.2` | 3 | ❌ 思考泄漏（`"1. **分析请求：**…"`） |
| `agnes-2.5-flash` | 6 | ✅ 正常 |
| `glm-5.2` | 1 | ❌ 垃圾（`"2"`） |
| `deepseek-v4-flash` | 1 | ❌ 英文（`"We need answer in Chinese…"`） |

**10 条中至少 4 条不可用（约 40%）**。这**实测坐实**了知识库
`工作记忆/13_业务域_AI与通知.md:87` 的既有结论（"chat auto 每次路由到不同模型…"）
—— 并首次给出**并发场景下的**硬证据。

后果对"真实聊天场景"是双杀：
① 体验不一致（同一客服账号，不同客户被不同模型接待）；
② 故障随机（约 40% 触发思考泄漏/英文回复）。
且**开并发会放大**该问题（并行会话各自随机命中）。

**我方并发模型（读码）**：
```
1 个 daemon 线程 "ai-autoreply"（ai_reply.py:1052）
  while not stop:
      _tick()                    :1077   取 LIMIT 20 条 them 消息
          for r in rows:                  ← 逐条串行
              _handle(r, cfg)             ← 内含 LLM 调用（13~25s）
      wait(POLL_INTERVAL = 5.0s)  :1033
```
- **N 个会话 = N × 单次 LLM 延迟**（纯串行）
- 水位在**处理前**推进（`:1101-1102`）⇒ 崩溃则该条丢失、不重试
- 批量跑完前新到消息须等下一轮（+5s）

**延迟账**（用实测单次 13~25s + 发送随机延迟 8~20s）：

| 同时接待 | 最后一名用户获得回复 |
|---|---|
| 1 人 | ~25~45s |
| 5 人 | ~90~145s |
| **10 人** | **~155~270s（2.5~4.5 分钟）** |

---

## 4. 上游情报核查（按"上游情报优先"规则）

**已嵌入上游 `AstrBotDevs/AstrBot`（40,981★，`pushed_at` 2026-09-25 —— 当日仍活跃）自带完整答案**：

| 上游文件 | 提供的能力 |
|---|---|
| `astrbot/core/utils/session_lock.py` | **`session_lock_manager`：per-session `asyncio.Lock`**（跨会话并行 / 同会话串行；含引用计数清理，不泄漏锁；线程安全 + 按事件循环隔离 `WeakKeyDictionary`） |
| `astrbot/core/utils/session_waiter.py` | 会话保持/超时（`keep(timeout)` / `stop()`）、`SessionController.history_chains` |
| `astrbot/core/utils/active_event_registry.py` | `UMO → 活跃事件集合`，可按会话终止在途事件 |
| `astrbot/core/platform/message_session.py` | 会话唯一标识规范 `platform_id:message_type:session_id` |
| `astrbot/core/agent/context/{config,manager,compressor,token_counter,truncator}.py` | 上下文子系统四模块：`max_context_tokens` / `enforce_max_turns` / **按轮截断或 LLM 摘要二选一** |
| `astrbot/core/pipeline/{scheduler.py,stage_order.py,...}` | 阶段化管道调度（waking/whitelist/rate_limit/content_safety/respond…） |

⚠️ **可移植性边界**：`session_lock_manager` 是 `asyncio.Lock` 体系，我方是 `threading.Thread`
⇒ **可移植的是"per-session 串行 + 跨会话并行"的语义，不是代码**。

**同类产品核查**：`pen9un/douyin-chatgpt-bot`（379★，2026-09-10 活跃）——
**`license: null`（无协议）⇒ 按项目规则不可用**，仅可作产品形态参考。

**我方上游 `xyc667/douyin-auto-reply-assistant`**（AI 获客回复直接来源）：12★，
`pushed_at` 2026-06-29，JS 实现，**无并发/会话锁机制** ⇒ 该缺陷非移植遗漏，是我方自建部分。

---

## 5. 结论（三处推翻既有认知）

| # | 原认知 | 实测结论 |
|---|---|---|
| 1 | "不建议全文注入（会撑爆上下文）" | ❌ **对"单会话全文"不成立**：30 条/29,304 字实测 **HTTP 200 送达**（`messages=32`, `content=29,337 字`, 实际路由 glm-5.2，18.7s），**容量装得下**；97% 会话 ≤4000 字。真实瓶颈是 **`max_tokens`**（=1000 时 18.7s 返回 None；=4000 时 12.9s 成功） |
| 2 | "链路/能力没配" | ❌ **链路配得完整**：三提供商 key 全部已填且校验通过；sem 链实测可达（`vectors=OK`, 命中 `llama-nemotron-embed-vl-1b-v2`）。差别只在**各消费点自己的门**（`enabled` / `vision_enabled`） |
| 3 | 并发瓶颈在 LLM 端点 | ❌ **端点 10 并发 10/10 全 200，墙钟 2.3s**；瓶颈在**我方单线程串行**代码，以及 **`auto` 首候选的并发漂移** |

**一句话**：AI"不智能 + 不实时"都不是模型能力问题，而是**上下文装配契约 + 并发模型**两处工程缺陷；
模型与端点本身**实测均可用**。

---

## 6. 处置清单（**已全部实现**，v0.44.68~0.44.72）

> **状态更新（2026-09-25）**：本清单 11 项已**全部闭环**。原始登记为「登记台账，未改代码」，
> 用户裁定「先做低风险两项，再按 ADR 实施」后已按 ADR-008 §4 顺序全部落地并升版。

| ID | 项 | 类型 | 优先级 | 状态 |
|---|---|---|---|---|
| AI-050 | `auto` 首候选在并发下漂移（4 模型 / 40% 不可用） | 配置 + 契约 | 🔴 | ✅ v0.44.68（llm 链改 agnes-2.5-flash→deepseek-v4.1-flash + 机械门禁） |
| AI-051 | 上下文排序改 `ORDER BY COALESCE(ts,0) DESC, id DESC` | 缺陷修复 | 🔴 | ✅ v0.44.68 |
| AI-052 | 图片描述落 KV + 白名单纳入 `'27'` + 组装时还原为会话消息 | 架构变更 | 🔴 | ✅ v0.44.70（独立 KV + 负缓存 + 不重复调视觉） |
| AI-053 | per-session 串行 + 跨会话并行（借 AstrBot 语义） | 架构变更 | 🔴 | ✅ v0.44.71（线程池 + 会话锁 + 在途跳过） |
| AI-054 | `sem_enabled` 对 `pro_kb` 形同虚设（契约漂移） | 契约收口 | 🟡 | ✅ v0.44.72（统一为语义总开关） |
| AI-055 | `semantic_topk` 每次多烧一次 embedding 往返（`current_sem_model` 探针） | 性能 | 🟡 | ✅ v0.44.72（改只读链路配置，实测零往返） |
| AI-056 | RAG `threshold=0.0` 等于不筛相关性 | 质量 | 🟡 | ✅ v0.44.72（改 0.40；实测滤除「无关主题」条目） |
| AI-057 | `_history_limit`（有 clamp）与 `chat()`（无 clamp）双处上限离散 | 契约收口 | 🟡 | ✅ v0.44.69（单一口径：0=全文 / >0=N 条） |
| AI-058 | `_RAG_SUFFIX` 把当前客户消息重复写进 system | 冗余 | 🟢 | ✅ v0.44.72 |
| AI-059 | `build_system_prompt(cfg, kb_items, text)` 的 `kb_items` 是死参数 | 卫生 | 🟢 | ✅ v0.44.72（签名收口） |
| AI-060 | `_describe_image` 的 text-URL 兜底正则对真实数据永不匹配 | 死代码 | 🟢 | ✅ v0.44.72（删除） |

**伴随项**：全文注入 + token 预算（ADR-008 决策 1）✅ v0.44.69 —— 即对「全文注入」质疑的正面回答。

---

## 7. 复跑方法（可复现）

```bash
# 环境：数据库副本 + 独立进程（绝不写生产库）
SB="…/scratch/live_<ts>"; mkdir -p "$SB/root/members/m17db0f8209156f26/data"
cp C:/temp/dyautodm_design/members/m17db0f8209156f26/data/dyautodm.db* "$SB/root/members/m17db0f8209156f26/data/"

PY="C:/Users/LOX/AppData/Local/Programs/Python/Python314/python.exe"
cd C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend
DY_APP_ROOT="$SB/root" DY_MEMBER="m17db0f8209156f26" "$PY" <探针>.py
```

| 探针 | 验证内容 |
|---|---|
| `live_probe.py` | 出网插桩：嵌入/视觉/主 LLM 的真实调用与载荷 |
| `full_ctx_probe2.py` | 单会话全文 29,304 字送达实测（容量 + max_tokens 分离） |
| `conc_stress.py` / `conc10.py` | 端点并发 5/10 + `auto` 路由漂移 |
| `size_probe.py` | 全库会话规模分档（全文注入可行性） |

**取证纪律（本轮教训）**：
① 生产库有运行中应用时**必须用快照副本**；
② 单次读数不算证据（本轮曾因非原子读 + 字段名错误产出错判）；
③ 「函数返回值」不算证据，**只认插桩记录的真实出网请求**；
④ 测"模型可达"必须**绕过业务门**，测"门是否放行"必须**走真实业务路径** —— 两者不可混读。
