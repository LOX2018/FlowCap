# MCP Server for LogSeq 项目总结

> 来源：[ergut/mcp-logseq](https://github.com/ergut/mcp-logseq)  
> 协议：MIT License  
> 语言：Python 100%  
> 最新版本：v1.8.0（2026年6月）  
> 贡献者：11 人 | Commits：116 | Releases：14

---

## 一、项目简介

`mcp-logseq` 是一个 **MCP（Model Context Protocol）服务器**，用于将 Claude 等 AI 客户端连接到 LogSeq 知识库。通过该服务器，AI 可以直接读取、创建和管理 LogSeq 页面，并支持可选的**语义向量搜索**和 **DB 模式图谱**操作。

核心价值：让 AI 在不切换上下文的情况下直接操作 LogSeq 数据，实现智能知识管理、自动化内容创建和深度研究分析。

---

## 二、主要功能

### 2.1 核心工具（16 个）

| 工具 | 用途 |
|------|------|
| `list_pages` | 浏览图谱中的所有页面 |
| `get_page_content` | 读取页面内容 |
| `create_page` | 创建新页面（支持结构化块） |
| `update_page` | 修改页面（append/replace 模式） |
| `delete_page` | 删除页面 |
| `delete_block` | 按 UUID 删除块 |
| `update_block` | 按 UUID 编辑块内容 |
| `search` | 跨图谱关键词搜索 |
| `query` | 执行 Logseq DSL 查询 |
| `find_pages_by_property` | 按属性搜索页面 |
| `get_pages_from_namespace` | 列出命名空间下的页面 |
| `get_pages_tree_from_namespace` | 命名空间层级树视图 |
| `rename_page` | 重命名页面并更新引用 |
| `get_page_backlinks` | 查找指向页面的反向链接 |
| `insert_nested_block` | 插入子块/兄弟块 |
| `set_block_properties` | 设置 DB 模式类属性（仅 DB 模式） |

### 2.2 向量搜索工具（3 个，可选）

| 工具 | 用途 |
|------|------|
| `vector_search` | 按语义搜索笔记 |
| `sync_vector_db` | 同步向量数据库与图谱文件 |
| `vector_db_status` | 查看向量数据库健康状态 |

向量搜索支持通过 [Ollama](https://ollama.com/) 进行完全本地嵌入，也可使用 OpenAI 或兼容 OpenAI 的嵌入端点。向量数据库使用 [LanceDB](https://lancedb.com/)，始终保持本地存储。

### 2.3 智能 Markdown 解析（v1.1.0+）

`create_page` 和 `update_page` 工具可自动将 Markdown 转换为 Logseq 原生块结构：

- YAML frontmatter → 页面属性（tags、priority 等）
- 标题层级（`#`、`##`、`###`）→ 分层块
- 嵌套列表 → 正确缩进的块
- 代码块 → 保留为单个块
- 复选框（`- [ ]` → TODO，`- [x]` → DONE）

更新模式：
- **append**（默认）：在已有块后追加新内容
- **replace**：清空页面并替换为新内容

### 2.4 安全重试与大文件写入

- `create_page` 在页面已存在时会明确报错，避免 Logseq 静默创建编号重复页面
- 大文件写入推荐模式：先创建空页面 → 分块 append → 读取验证

---

## 三、配置与部署

### 3.1 前置条件

- LogSeq 已安装并运行
- 启用 HTTP APIs 服务器（Settings → Features）
- 启动 API 服务器（🔌 按钮 → "Start server"）
- 生成 API Token
- 安装 [`uv`](https://docs.astral.sh/uv/) Python 包管理器
- MCP 兼容客户端（Claude Code、Claude Desktop 等）

### 3.2 快速安装（Claude Code）

```bash
claude mcp add mcp-logseq \
  --env LOGSEQ_API_TOKEN=your_token_here \
  --env LOGSEQ_API_URL=http://localhost:12315 \
  -- uv run --with mcp-logseq mcp-logseq
```

### 3.3 Claude Desktop 配置

在 `Settings → Developer → Edit Config` 中添加：

```json
{
  "mcpServers": {
    "mcp-logseq": {
      "command": "uv",
      "args": ["run", "--with", "mcp-logseq", "mcp-logseq"],
      "env": {
        "LOGSEQ_API_TOKEN": "your_token_here",
        "LOGSEQ_API_URL": "http://localhost:12315"
      }
    }
  }
}
```

### 3.4 环境变量

| 变量 | 必需 | 说明 |
|------|------|------|
| `LOGSEQ_API_TOKEN` | ✅ | LogSeq API 令牌 |
| `LOGSEQ_API_URL` | ❌ | 服务器 URL（默认 `http://localhost:12315`） |
| `LOGSEQ_API_CONNECT_TIMEOUT` | ❌ | HTTP 连接超时秒数（默认 3） |
| `LOGSEQ_API_READ_TIMEOUT` | ❌ | HTTP 读取超时秒数（默认 6） |
| `LOGSEQ_DB_MODE` | ❌ | 设为 `true` 启用 DB 模式属性支持（beta） |
| `LOGSEQ_EXCLUDE_TAGS` | ❌ | 逗号分隔的标签，标记的页面对 AI 隐藏 |
| `LOGSEQ_INCLUDE_NAMESPACES` | ❌ | 命名空间白名单 |
| `LOGSEQ_EXCLUDE_NAMESPACES` | ❌ | 命名空间黑名单（优先于白名单） |
| `LOGSEQ_CONFIG_FILE` | ❌ | 共享 JSON 配置文件路径 |
| `MCP_HTTP_AUTH_TOKEN` | HTTP 模式必需 | HTTP 传输的 Bearer 认证令牌 |

### 3.5 HTTP 服务与多实例

默认使用 stdio 传输。也可作为长时运行的 HTTP 服务：

```bash
mcp-logseq --transport http --host 127.0.0.1 --port 12320
```

支持 Bearer 认证、按配置文件隔离的多实例模式、独立 writer 进程和 TLS。非本地回环地址的纯 HTTP 绑定会被拒绝，除非提供 TLS 或传入 `--insecure`。详见 [docs/SERVING.md](https://github.com/ergut/mcp-logseq/blob/main/docs/SERVING.md)。

---

## 四、隐私与访问控制

### 4.1 标签排除

通过 `LOGSEQ_EXCLUDE_TAGS=private,secret` 或配置文件，标记了特定标签的页面将对所有工具完全隐藏（列表、搜索、查询、直接读取均被拒绝）。向量搜索中，排除标签也会自动合并到索引排除列表，私密页面永远不会被嵌入。

### 4.2 命名空间访问控制

- **白名单**（`LOGSEQ_INCLUDE_NAMESPACES`）：仅列出的命名空间及其子页面可见
- **黑名单**（`LOGSEQ_EXCLUDE_NAMESPACES`）：列出的命名空间始终被阻止，优先于白名单
- 匹配规则：基于段（segment-based）、不区分大小写。例如 `work` 匹配 `work` 和 `work/projects`，但不匹配 `workshop`
- 访问控制在**页面级别**强制执行，覆盖所有工具

### 4.3 索引时命名空间范围（向量数据库）

可在配置文件的 `vector` 块中设置 `include_namespaces` / `exclude_namespaces`，在索引时决定哪些命名空间被嵌入到数据库中。此设置为全局配置，需要 `logseq-sync --rebuild` 完全重建索引后生效。

---

## 五、典型应用场景

1. **智能知识管理**：分析项目笔记、创建状态摘要、搜索未完成任务
2. **自动化内容创建**：创建会议记录页面、追加进度更新、生成周回顾页面
3. **智能研究与分析**：对比笔记主题、汇总反馈主题、构建知识关联图谱
4. **语义搜索**：按含义查找笔记（即使未使用相同关键词），支持跨语言搜索
5. **会议与文档工作流**：从会议记录创建任务页面、汇总日志条目、整理规划内容

---

## 六、项目结构

```
mcp-logseq/
├── assets/              # 图片等资源
├── docs/                # 文档（SERVING.md 等）
├── src/mcp_logseq/      # 源代码
├── tests/               # 测试
├── CHANGELOG.md         # 变更日志
├── DEVELOPMENT.md       # 开发指南
├── LICENSE              # MIT 许可证
├── LOGSEQ_API_ARCHITECTURE.md  # LogSeq API 架构说明
├── README.md            # 项目说明
├── ROADMAP.md           # 路线图
├── TESTING.md           # 测试文档
├── VECTOR_SEARCH.md     # 向量搜索设置指南
├── pyproject.toml       # Python 项目配置
└── uv.lock              # uv 依赖锁定
```

---

## 七、故障排除

| 问题 | 解决方案 |
|------|----------|
| `LOGSEQ_API_TOKEN environment variable required` | 在 Settings → Features 启用 HTTP APIs；点击 🔌 启动服务器；生成 Token；检查配置 |
| `spawn uv ENOENT`（Claude Desktop） | 使用 `which uv` 找到完整路径并在配置中使用绝对路径 |
| 连接问题 | 确认 LogSeq 运行中；API 服务器已启动；端口 12315 可访问 |

### 验证命令

测试 LogSeq 连接：
```bash
uv run --with mcp-logseq python -c "
from mcp_logseq.logseq import LogSeq
api = LogSeq(api_key='your_token')
print(f'Connected! Found {len(api.list_pages())} pages')
"
```

使用 MCP Inspector 调试：
```bash
npx @modelcontextprotocol/inspector uv run --with mcp-logseq mcp-logseq
```

---
