# 知识库方案对比：Yuxi vs LLM Wiki + Obsidian

> 分析日期：2026-09-01
> 结论：**不建议更换**。Yuxi 是团队级 Agent 平台，不是个人知识库替代品；在你当前硬件与内容形态下，换过去是净损失。
> 本文中所有"实测"数据均来自本机真实探测，非推测。

---

## 0. 一句话结论

| 你的诉求 | Yuxi 是否满足 | 代价 |
|---|---|---|
| 更轻便 | ❌ 反而重得多 | 需装 Docker + 常驻 8~13 个容器 |
| 一体化处理 | ✅ 确实一体化 | 但一体化的是"Agent 平台"，不是"写作+检索" |
| 知识总结效果差距不大 | ⚠️ 略强（rerank/评估集） | 但你 100% Markdown，其文档解析优势用不上 |

**拆开看：Yuxi 唯一实质领先的是「Rerank + RAG 评估 + 多租户权限」，而前两项可以在现有 LLM Wiki 上补齐，第三项你根本用不到（单人）。**

---

## 1. 硬门槛：你的机器现在跑不了 Yuxi

实测本机环境：

```
docker                    → command not found
Docker Desktop.exe        → 不存在
AppData\Local\Docker      → 不存在
wsl -l -v                 → 未安装任何发行版
内存                      → 14.9 GB（16GB 物理）
CPU                       → AMD Ryzen 5 5500U（6 核 12 线程，笔记本低压，无独显）
D 盘可用                  → 80 GB
```

Yuxi 官方前置要求：Docker + Docker Compose，**仅此一种部署形态**（无 Windows 原生包、无 pip/uv 安装路径）。

`docker-compose.yml` 实测服务清单（13 个服务）：

| 服务 | 镜像 | 用途 |
|---|---|---|
| api / worker / storage-migrator | yuxi-api | 后端 + 异步任务 |
| web | yuxi-web | 前端 |
| sandbox-provisioner | yuxi-sandbox-provisioner | 沙盒 |
| **graph** | neo4j:5.26.29 | 知识图谱 |
| **etcd** | quay.io/coreos/etcd:v3.5.5 | Milvus 依赖 |
| **minio** | minio/minio | 对象存储 |
| **milvus** | milvusdb/milvus:v2.5.6 | 向量库 |
| postgres / redis | postgres:16 / redis:7.4.10 | 业务库 / 队列 |
| mineru-api / paddlex | 本地构建 | PDF 深度解析（**profile: all，且声明 nvidia GPU 设备**） |

### 关键矛盾：lite 模式会关掉你要的功能

Makefile 里 lite 模式启动的是：

```make
up-lite:
	LITE_MODE=true docker compose up -d postgres redis minio api worker web
```

**不含 milvus、neo4j、etcd。**

也就是说：
- 跑 `up-lite` → 没有向量检索、没有知识图谱 → Yuxi 的 RAG 核心能力被关掉，只剩一个聊天壳。
- 跑全量 → 8 个以上常驻容器（Milvus standalone 单独就是内存大户），在 16GB 的 Windows 笔记本上，留给 DYAutoDM（后端 + BCC 浏览器容器 + recv 守护 + Tauri）的余量会非常紧张。而你日常主力是 DYAutoDM，知识库只是辅助。

**且 MinerU / PaddleX 这两个 PDF 解析服务显式声明 `devices: nvidia / capabilities: [gpu]` —— 你这台 5500U 核显机器直接出局。**

---

## 2. 内容形态错配：Yuxi 的王牌你一张都用不上

Yuxi 文档处理栈主打 MinerU + PaddleX + RapidOCR，解决的是**扫描 PDF / 表格 / 图文混排**的解析难题。

实测你的知识库实际构成：

```
raw/sources/  →  9 个文件，151 KB，100% 是 .md
  ├─ 工作记忆\*.md      （7 个，手写 md）
  ├─ 07_BCC浏览器容器业务链.md
  └─ git_history.md

wiki/         →  141 个页面，408 KB，100% 是 .md
  ├─ concepts/   63 页
  ├─ entities/   47 页
  ├─ findings/   24 页
  ├─ sources/    10 页
  └─ queries/     3 页（含 2 篇 deep research 报告）
```

**纯 Markdown 场景里，MinerU/PaddleX/RapidOCR 的价值 = 0。** 你付了 3 个重型解析服务的资源账单，换来的是零收益。

---

## 3. 知识总结效果：实测对比（这是核心问题）

### 3.1 LLM Wiki 当前检索质量实测

直接打本地 API（`127.0.0.1:19828`，项目 UUID `ac8c292b-...`），用**故意不含文档字面关键词**的自然语言提问：

| 提问（语义查询，非关键词） | Top1 | 分数 | Top2 | Top3 | 区分度 |
|---|---|---|---|---|---|
| 会话列表刷不出来怎么排查 | `wiki/08_私信列表与会话详情捕获方案.md` | 49 | concepts/私信列表及会话详情捕获方案 (42) | v2-私信收发链路 (5) | ✅ 断崖 |
| 浏览器崩溃重启的策略 | `queries/research-...指数退避重启策略...` | 70 | concepts/指纹浏览器内核强制策略 (48) | v2-架构基线 (2) | ✅ 断崖 |
| 打包后守护进程起不来的原因 | `concepts/守护进程日志-enqueue.md` | 43 | findings/守护进程-enqueue-规避-gbk-崩溃 (43) | index (13) | ✅ 断崖 |

三条全部命中正确页面，Top1 与噪声项有 5~10 倍分差。**RAG 的核心指标（召回正确性 + 排序区分度）是健康的。**

图谱侧实测：**135 节点 / 181 条边**，已形成实体—概念网络。

### 3.2 一个必须诚实指出的短板

虽然 API 返回 `"mode": "hybrid"`，但**所有结果的 `vectorScore` 字段均为 `null`**。

这意味着：**向量通道大概率未配置或未启用，当前检索实质退化为关键词打分**（文件名精确匹配 ≈200、标题短语 ≈50、词袋个位数）。

那上面三条语义查询为什么还能命中？—— 因为 LLM Wiki 在 ingest 时用 LLM 抽取生成了 63 个 `concepts/` 页面，**页面标题本身就是 LLM 做的语义归纳**，关键词匹配这些"语义化标题"间接实现了语义检索。这是 LLM Wiki 架构的巧妙之处，但也说明：**语义上限被"标题抽取质量"卡住了，一旦提问角度超出已抽取的标题词汇，就会漏召回。**

### 3.3 与 Yuxi 的真实差距

| 能力 | LLM Wiki 现状 | Yuxi | 差距性质 |
|---|---|---|---|
| 关键词检索 | ✅ 强 | ✅ | 持平 |
| 向量语义检索 | ⚠️ 声称 hybrid，向量分缺失 | ✅ BGE-M3 + Milvus | **真实差距** |
| Rerank 重排 | ❌ 无 | ✅ 有 | **真实差距** |
| 检索测试工作台 | ❌ 无 | ✅ 有 | 调试价值 |
| RAG 评估集 | ❌ 无 | ✅ 有（自动生成单跳/多跳 QA） | 团队规模才有价值 |
| 知识图谱 | ✅ 135节点/181边（wikilink） | ✅ Neo4j 实体关系抽取 | 形态不同，各有胜场 |
| 文档解析 | ❌ 弱（md 足够） | ✅ 强（PDF/Office/OCR） | 你用不上 |
| 多租户权限 | ❌ | ✅ | 单人 = 无用 |

**结论：差距集中在"向量 + Rerank"，是真实存在的，但可以通过给 LLM Wiki 配置 embedding 来补齐，而不是需要换掉整个栈。**

---

## 4. 一体化：Yuxi 的"一体化"和你要的"一体化"不是一回事

Yuxi 的一体化 = **知识入库 → Agent 执行 → 沙盒产出 → 团队权限**，一条企业级流水线。

你的一体化需求 = **写完能搜到、搜到能跳转、跳转能编辑**。

你现在这套：
```
手写 md（Obsidian，双链/图谱/快捷键，本地手感）
   ↓ copy 到 raw/sources/ + rescan
LLM Wiki 增量编译 → wiki/（概念页/实体页/双链）
   ↓
Hermes 通过 API 检索，带路径引用回答
```
**两个工具共享同一棵物理树**（vault 根 `D:\文档\Biancheng   CK\DY v2`，LLM Wiki 项目嵌套其内一层）。Obsidian 里能看到 LLM Wiki 生成的 `wiki/` 全部双链 —— 这不是"两个割裂系统"，是"一个文件系统上的两个视图"。

换成 Yuxi 之后会丢掉什么：
- ❌ **Obsidian 的本地编辑手感**（Yuxi 只有 Web 端 md 预览/编辑，无双链图谱、无本地快捷键、无插件生态）
- ❌ **文件永远在自己硬盘上**（Yuxi 的文档进了 MinIO + Milvus + Postgres，导出成本变高）
- ❌ **零运维**（Yuxi 需管容器、卷、备份、升级停机窗口）
- ⚠️ **稳定性**：Yuxi 当前 `v0.7.2.beta2`，仍是 **Beta**；官方明确要求"从 v0.7.1 升级时不能直接 `docker compose up`，需在停机窗口完成备份和迁移"

而 Beta 期的企业平台 + 你主力项目 DYAutoDM 也在高强度开发中，属于**用稳定性换一个你用不到的多租户体系**。

---

## 5. 决策建议

### 5.1 不换 Yuxi。把精力花在修复现有栈的 3 个实测问题

探测中发现的具体问题（都能立刻修）：

**问题①：2 个权威源文件未归档**
```
桌面源 工作记忆/ 共 9 个 md
已归档 raw/sources/工作记忆/ 共 7 个
未归档：06_私信守护与会话列表拉取.md
        07_BCC浏览器容器业务链.md
```

**问题②：`wiki/sources/` 下 7 个重复页污染检索**
```
4-工作记忆--7-00架构与版本--yqf7wv.md
4-工作记忆--7-01打包与部署--iytngm.md
4-工作记忆--8-03私信收发链路--ijpx4i.md
4-工作记忆--9-05直播解析与监听--9rsk4f.md
4-工作记忆--10-02指纹浏览器与凭证--djzx9g.md
4-工作记忆--11-04前端联调与账号管理--1c2fe4s.md
4-工作记忆--15-08私信列表与会话详情捕获方案--rr5uz7.md
```
这是早期重复导入产生的 hash 命名页，与后来的 `工作记忆/0X_*.md` 内容重叠。实测检索中 `4-工作记忆--15-08私信列表与会话详情捕获方案--rr5uz7.md` 拿到 **81 分排第二**，直接抢占了正文页的位置。**这是当前知识库最影响检索质量的单点问题。**

**问题③：正文页与摘要页并存，检索目标分裂**
```
raw/sources/工作记忆/08_私信列表与会话详情捕获方案.md   70,362 字符（正文）
wiki/sources/08_私信列表与会话详情捕获方案.md            2,844 字节（摘要）
wiki/08_私信列表与会话详情捕获方案.md                  110,277 字节（正文页）
```
同一份文档在 `wiki/` 里存在"正文页 + sources 摘要页"两个入口，检索时摘要页会分流命中。

**问题④（根因级）：向量通道未启用**
`mode: hybrid` 但 `vectorScore: null` → 在 LLM Wiki 设置里配置 embedding 模型后，语义检索上限会明显提升。**这一步做完，"与 Yuxi 的总结效果差距"就基本抹平了。**

### 5.2 什么时候应该重新考虑 Yuxi

满足**任意两条**再评估：
1. 需要从 PDF / 扫描件 / 表格批量抽取知识（MinerU 才有用武之地）
2. 需要多人协作 + 按部门分配知识库权限（多租户体系）
3. 硬件升级到 32GB+ 内存 / 有 NVIDIA 独显
4. 需要一个 Agent 能自己查知识库、跑代码沙盒、产出交付文件（Yuxi 的真正强项）
5. 需要量化评估 RAG 效果（召回率 / 答案相关性指标）

目前你一条都不满足。

### 5.3 如果将来真要上，最小化试错路径

不要直接替换，先并行验证：
1. 装 Docker Desktop + WSL2（先确认 16GB 内存能否同时带起 DYAutoDM 和 Milvus）
2. 只投 2~3 篇文档做对照，**用同一个问题分别问 LLM Wiki 和 Yuxi**，比召回
3. 对比不过关就 `docker compose down`，零迁移成本 —— 因为你的知识主体始终在 Obsidian vault 的 md 文件里，**没有被任何系统锁死**

这一点正是当前方案最大、最被低估的价值：**数据主权在文件系统，换系统的成本接近于零。** 换成 Yuxi 反而会失去这个性质。

---

## 6. 附：本次实测的原始依据

| 项目 | 命令 / 来源 | 结果 |
|---|---|---|
| Docker | `docker --version` | command not found |
| Docker Desktop | `ls "C:/Program Files/Docker/Docker/Docker Desktop.exe"` | No such file |
| WSL | `wsl -l -v` | 无发行版 |
| 内存 | `Get-CimInstance Win32_ComputerSystem` | 14,875,303,936 B |
| CPU | `Get-CimInstance Win32_Processor` | AMD Ryzen 5 5500U |
| LLM Wiki | `GET /api/v1/health` | 0.6.11, status=running, authConfigured=true |
| 项目统计 | 文件系统遍历 | wiki 141 页 / sources 9 文件 |
| 图谱 | `GET /api/v1/projects/{uuid}/graph?limit=500` | 135 nodes / 181 edges |
| 检索质量 | `POST /search` × 3 组语义查询 | 3/3 命中，mode=hybrid，vectorScore=null |
| Yuxi 服务清单 | `docker-compose.yml` (16,446 B) | 13 services |
| Yuxi lite 定义 | `Makefile: up-lite` | 6 services，无 milvus/neo4j/etcd |
