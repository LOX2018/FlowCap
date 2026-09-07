# DYAutoDM_v2 性能优化重构方案

> **For Hermes:** 按 Task 顺序逐项实施，每项先测基线再动手（铁律 0：无对照数据不许宣称优化）。
> **适用范围:** 仅测试目录 `C:\temp\dyautodm_test` 验证通过后，才同步正式部署。

**Goal:** 在不触碰风控红线、不重写已验证链路的前提下，把用户可感知的慢点（启动、发图、轮询体验）压到物理下限。

**Architecture:** 纯 Python 生态内优化（async 并发、缓存、PyInstaller onedir），零语言迁移。所有已验证链路（WS/WP 双通道、凭证门禁 1-4、AI 获客）保持行为不变。

**Tech Stack:** Python 3.11 / FastAPI / asyncio / requests→httpx（仅新代码）/ PyInstaller onedir / SQLite WAL（已有）

---

## 0. 实测基线（2026-09-07，优化决策的唯一依据）

| 指标 | 实测值 | 来源 |
|---|---|---|
| 数据库体积 | 1.2 MB（821 消息 / 354 会话，平均文本 303 字节） | sqlite3 直查 `dyautodm.db` |
| 启动耗时 | ~15s（§9.16 并行拉起后；此前 40s） | 知识库 §9.16 |
| 发图端到端 | 2-4s，6 次串行网络请求 | 知识库 §9.11 |
| 会话列表查询 | SQLite 主键级查询，毫秒级 | 1.2MB 库 + 已有索引 `idx_dmmsg_account_conv` |
| 前端轮询 | `/conversations` 每 5s 一次 | `messages.py` 注释 |
| embedding 检索 | 余弦暴力遍历，仅知识库重建时批量跑 | `ai_reply.py:299` |

### 由基线直接得出的否决项（不做，写下来防止将来反复）

- ❌ **数据库优化**（加索引/换引擎/分表）：1.2MB / 821 条，任何查询都是亚毫秒。花在这里的每一分钟都是 §performance-tradeoff 案例里的"错误坐标轴"。
- ❌ **protobuf 解析提速**：只在「更新会话」时批量跑一次，不在热路径。
- ❌ **C++/Rust/Go 重写任何模块**：本方案明确排除（前次会话已论证：瓶颈全在 IO 等待，语言无关）。
- ❌ **图片上床（image host）**：已有定论——本地托管 inline，图床 SSL 超时是净恶化。

---

## Phase 1：用户可感知优化（高收益，先做）

### Task 1.1 建立性能基线脚本

**Objective:** 把"快了/慢了"变成可复现的数字，后续每个 Task 都对照它验收。

**Files:**
- Create: `backend/scripts/perf_baseline.py`

**Steps:**
1. 写脚本 `perf_baseline.py`，输出四项计时（各跑 3 次取中位）：
   - backend 冷启动→8000 端口就绪（读 sidecar 日志时间戳或轮询 `/api/status`）
   - `GET /api/messages/conversations?account=...` 单次耗时
   - `GET /api/messages/conversation?...` 单次耗时
   - `POST /api/messages/send` 文本消息端到端（仅测试账号）
2. 结果追加写入 `C:\temp\dyautodm_test\perf_baseline.json`（带日期），形成历史序列。
3. **验证:** 运行两次，JSON 中两次数据合理（启动 <30s，查询 <100ms）。

```bash
cd C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2
python backend/scripts/perf_baseline.py --out C:/temp/dyautodm_test/perf_baseline.json
```

### Task 1.2 PyInstaller onedir：砍 sidecar 启动解包时间

**Objective:** 三份 sidecar（backend / recv-daemon / browser-daemon）从 onefile 改 onedir，消除每次启动的内存解包（当前 15s 启动的最大剩余开销）。

**Files:**
- Modify: `backend/build_sidecar.py`（`build_one` 增加 `--onedir` 分支）
- Modify: `src-tauri` 的 sidecar 解析逻辑（若 onedir 需要指向目录内 exe）

**Steps:**
1. `build_sidecar.py` 加参数 `build_one(entry, name, mode="onedir")`，PyInstaller 参数追加 `--onedir`。
2. 分别打三份 sidecar，记录体积与首启/二启耗时（onedir 首启还有 Defender 扫描，二启才代表稳态）。
3. 确认 Tauri 主程序 `_resolve_sidecar_binary` 兼容 onedir 目录结构（指向 `name/name.exe`）。
4. 对照 Task 1.1 基线：冷启动目标 **15s → ≤8s**。
5. **验证:** md5 比对部署产物；跑 perf_baseline 两次对比 JSON 序列。
6. **回退开关:** `--onefile` 保留为默认回退路径，onedir 验证不通过即回退（不留半成品）。

**Risks:** onedir 产物是目录不是单文件，Tauri 资源打包方式需确认；若主 exe 内嵌释放逻辑（部署铁律 7）依赖单文件形态，则此 Task 仅用于独立 sidecar，内嵌形态不动。

### Task 1.3 发图链路并发化：①STS / ②Apply 并行

**Objective:** 方案A ①-⑥ 中，① `public_image_config`（STS）与 ② `ApplyUploadInner` 无数据依赖的 prep 阶段尽量并行；端到端 2-4s 里网络串行占大头，能压 0.5-1s 是 0.5-1s。

**Files:**
- Modify: `backend/dy_apis/image_sender.py:send_image`

**Steps:**
1. 通读 ①-⑥ 画出依赖图（①STS→②Apply→③TOS→④Commit→⑥send，其中②的 security_token 依赖①，需实测确认——**先读代码确认真依赖再动手，不得臆断**）。
2. 仅对确认无依赖的请求对用 `concurrent.futures.ThreadPoolExecutor` 并行（requests 阻塞式保持不变，不引入 httpx 重写）。
3. **验证:** DB 落库 `role=me` + extra 含 skey（铁律 0）；perf_baseline 记录 send_image 前后耗时；**风控红线：KICK 账号不测，只测已验证通过的小助理号，一次失败立即停止等下次**（§9.4）。

**Risks:** 请求顺序变化可能触发风控特征。若实测对照有差异，回退串行——2-4s 是物理下限附近，0.5s 收益不值得风控风险时果断放弃。

### Task 1.4 前端轮询降载：5s 轮询 + staleTime 对齐

**Objective:** `/conversations` 每 5s 全量拉 354 会话。虽是毫秒级查询，但每次都渲染 diff。把轮询间隔改为有新消息才加速（利用 recv_daemon 已有的未读变化信号）。

**Files:**
- Modify: `frontend/src/pages/messages.tsx`（轮询 interval）
- Modify: `frontend/src/api/client.ts`（如需轻量 `?summary=1` 端点）

**Steps:**
1. 后端加 `GET /conversations/summary?account=`：只返回 `unread_total + last_ts`（一条 SQL），响应体 <1KB。
2. 前端轮询降频：summary 每 5s；summary 有变化才拉全量 `/conversations`；react-query `staleTime` 3s 保持（铁律：改后需切会话或重启验证，知识库部署铁律 8）。
3. **验证:** DevTools Network 面板观察 60s 窗口内全量请求次数：12 次 → 仅变化时 1-2 次。
4. 版本号 0.34.1 → 0.34.2（前端改动升版本铁律），`npx tauri build --no-bundle`。

---

## Phase 2：稳健性优化（中收益，低风险）

### Task 2.1 `_wait_for_port` 退避优化

**Objective:** `main.py:81` 固定 `time.sleep(0.5)` 轮询改指数退避（0.2s 起，上限 1s），并行拉起后的统一等待更灵敏，启动尾延迟再压 1-2s。

**Files:** Modify: `backend/main.py:81-95`

**Steps:**
1. 改 `_wait_for_port` 为 `delay = min(1.0, 0.2 * 1.5**n)` 循环。
2. **验证:** perf_baseline 启动项对照；`python -m py_compile backend/main.py`（CRLF 仓库，小改动可直接 patch，多行插入必须用脚本法——调试铁律 7）。

### Task 2.2 embedding 结果磁盘缓存

**Objective:** `ai_reply.py` 语义检索每次重建知识库都全量重嵌入。加按内容 md5 的向量缓存（kv_store 表即可），增量重建只嵌入新条目。

**Files:**
- Modify: `backend/services/ai_reply.py:351`（`kb_rebuild_semantic_cache`）

**Steps:**
1. 每条知识库条目算 `md5(text)`，已缓存的 md5 直接复用向量。
2. 缓存存 `kv_store`（key=`sem_vec:{md5}`，value=json 向量）。
3. **验证:** 重建两次，第二次日志显示 `embedded=0 cached=N`；语义检索命中结果与不缓存时一致（抽查 3 条问题对照）。

### Task 2.3 会话列表查询微调（可选，仅在实测 >50ms 时做）

**Objective:** `messages.py:256` 的 `LEFT JOIN GROUP BY` 在 354 会话下应 <10ms。**先 EXPLAIN QUERY PLAN 实测，>50ms 才动手**——否则此 Task 直接关闭。

**Steps:**
1. `EXPLAIN QUERY PLAN` 跑一遍现有 SQL，确认无全表扫描。
2. 慢才做：给 `dm_messages(account, conv_id, msg_type)` 建覆盖索引。
3. **验证:** 对照 perf_baseline conversations 项。

---

## Phase 3：明确不做（ Negative Space，防止范围蔓延）

| 提案 | 不做理由 |
|---|---|
| Go/Rust 重写 recv_daemon | 逻辑仍在迭代期（hook/解析常改），编译周期拖慢逆向节奏；消息量（821 条历史）离千条/秒差 4 个数量级 |
| C++ 图片解密 | `cryptography`/`pillow_heif` 已是 C 实现，Python 编排开销 <1% |
| 数据库分库分表 / ORM 迁移 | 1.2MB。见 §0 否决项 |
| websocket 库更换 | WS 链路已端到端验证，换库=重新踩坑，零收益 |
| uvloop / async 全家桶改造 | API 层 QPS 个位数（单用户），事件循环替换收益不可测 |

---

## 验收总门禁

1. **铁律 0**：任何"优化成功"结论必须有 perf_baseline.json 前后对照数字 + DB 硬验证（发送类）。
2. **部署铁律**：sidecar 重打后必须同步 `C:\temp\dyautodm_test\binaries\` 与测试目录独立副本（铁律 10）；改前端必升版本号 + `tauri build --no-bundle`（不打安装包）。
3. **风控红线**：所有实测只用已验证通过的小助理号；KICK 即停；绝不强杀浏览器进程（部署前 BCC `/quit` graceful）。
4. **知识库同步**：每 Phase 完成后把结论补写进 `工作记忆/*.md`（用户明确期望），并 copy 到 `raw/sources/工作记忆/` + POST rescan。

## 风险与权衡

- **Task 1.2 onedir** 是唯一动部署形态的改动，回退开关必须保留。
- **Task 1.3 并行发图** 是唯一触碰抖音请求时序的改动，收益（~0.5-1s）与风险（时序特征）需对照实验裁决，宁可不赚。
- 其余全部是 Python 内部改动，无外部行为变化。

## 预期总收益

| 指标 | 现状 | 目标 |
|---|---|---|
| 冷启动 | ~15s | ≤8s |
| 发图端到端 | 2-4s | 1.5-3s（视依赖图实测） |
| 60s 轮询全量请求 | 12 次 | 1-2 次 |
| 知识库重建 | 全量重嵌入 | 仅增量 |
