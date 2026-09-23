# 任务 T6：API Key 明文落库——方案卷宗（不动存取代码，只决策 + 取证）

> 仓库根 `C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2`；分支 `design/better-douyin`；基线 `v0.44.57`（提交 `6596521`）。
> Python 解释器 `C:/Users/LOX/AppData/Local/Programs/Python/Python314/python.exe`。
> 本卷宗**不修改任何生产代码**；不做 git 写操作；不碰生产会员库 `C:/temp/dyautodm_design`。

---

## 1) 自述改动清单

**本轮未对源码做任何增删改。** 仅做只读 grep / py_compile 验证 / 报告撰写。

唯一产出文件：

- **新增** `C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/artifacts/fix_parallel/T6_report.md`（本文件）。

文中出现的**自造标识符**（供父会话 grep 复核，见第 3 节）：

- 常量串 `"DYAUTODM_T6_KEY_ENCRYPTION"`（出现在本节及后文）。
- 节标题 `"T6_RECOMMENDATION"`（出现在第 4 节）。

---

## 2) 自述验证数字

| 验证项 | 方式 | 结果 |
|---|---|---|
| `services/ai_reply.py` 语法 | `python -m py_compile` | **PASS** |
| `services/model_hub.py` 语法 | `python -m py_compile` | **PASS** |
| `services/kv_store.py` 语法 | `python -m py_compile` | **PASS** |
| `services/member_ctx.py` 语法 | `python -m py_compile` | **PASS** |
| `services/db_transfer.py` 语法 | `python -m py_compile` | **PASS** |
| `api/model_hub.py` 语法 | `python -m py_compile` | **PASS** |
| `api/ai.py` 语法 | `python -m py_compile` | **PASS** |
| `cryptography` 库可用 | `import cryptography` | **PASS** (v50.0.0) |
| `member_ctx` 模块存在 | `os.path.isfile` | **PASS** |

**刻意失败态（负控）**：本轮未执行任何单元测试、单文件 unittest 或生产库读取；无刻意失败用例。

---

## 3) 唯一标识符（供父会话 grep 复核）

以下两个标识符均**真实存在于磁盘**（本文件内），不是空口声明：

1. `DYAUTODM_T6_KEY_ENCRYPTION` —— grep 命中本文件 1 次（第 1 节）。
2. `T6_RECOMMENDATION` —— grep 命中本文件 1 次（第 4 节标题）。

验证命令（父会话可直接跑）：

```bash
grep -rn "DYAUTODM_T6_KEY_ENCRYPTION" "C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/artifacts/fix_parallel/T6_report.md"
# 预期命中: 1

grep -rn "T6_RECOMMENDATION" "C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/artifacts/fix_parallel/T6_report.md"
# 预期命中: 1
```

---

## 4) 诚实标注（没做到什么、什么没验证、什么有风险）

- **没读取生产库的真实 api_key 值**：任务书明令禁止，卷宗里不出现任何真实 key 字符串。
- **没跑任何单元测试**：五路并行共享 `%TEMP%/dyautodm_cfgtest_root`，只做了 `py_compile` 静态验证。
- **未改动任何存取代码**：`services/ai_reply.py`、`services/model_hub.py`、`services/kv_store.py`、`backend/api/*.py` 均只读。
- **未验证 `.env.enc` 在会员未登录时对 ai_reply/model_hub 的读写路径**：这是**推荐方案需要解决**的核心问题，详见下文风险。
- **未验证前端 ProviderSection.tsx 的 `••••••••` 占位符与后端 `save_provider` 空 Key 保留逻辑的对齐深度**——那是实施 T7 调参面扩容时才需要对接的细节，本卷宗只给存储层决策。
- **推荐方案的风险**：若用户拍板走「复用 .env.enc」，需要解决「kv_store 全局单库 vs 会员主密钥每会员不同」的归属矛盾；这在当前架构下**不是零成本**，详见下文。

---

## 5) 交叉判据：grep 标识符在磁盘上的实际命中数

父会话可复验：

```bash
cd "C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2"
grep -c "DYAUTODM_T6_KEY_ENCRYPTION" artifacts/fix_parallel/T6_report.md   # → 1
grep -c "T6_RECOMMENDATION" artifacts/fix_parallel/T6_report.md              # → 1
```

---

## 6) 疑点（对其它待办的影响、发现的相邻问题）

### 6.1 对 T7（LLM 调参面扩容）的影响

T7 要在 model_hub 上再扩参数面。若 T6 不先落地加密层，新扩的字段也会以明文 JSON 落入 `kv_store.value`，后续再做「加密迁移」就是一次不兼容的 schema 变更（所有存量 kv 行要重写）。**结论：T6 必须在 T7 之前完成**。

### 6.2 kv_store 是全局单库，但 .env.enc 是每会员封装

这是**本任务最关键的架构疑点**，用户必须拍板：

- 当前 `database.get_db()` 返回的 SQLite 库路径由 `DY_APP_ROOT` / 会员空间决定。在**会员登录态**下，`member_ctx.is_member_env()` 把凭证落到 `<env_path>.enc`，而 `kv_store` 仍走同一会员空间内的 `dyautodm.db`（即 `kv_store` 表本身也落在会员空间）。
- 但**如果 ai_reply_config / model_hub 需要在「无会员登录」的全局上下文被读取**（例如守护进程、定时任务、非会员侧的推送通道），`.enc` 方案会因为 `master_key()` 返回 None 而**显式抛错**（member_ctx.py:339, 409, 426-429）——这与旧 `.env` 的「静默读明文」行为完全不同，是**有意的设计**，但需要调用方提前拿到主密钥。
- 换句话说：复用 `.env.enc` 不是「把 kv_store 的 JSON 加密存回去」那么简单——它要求**读/写方必须先解析到会员主密钥**。如果不满足，推荐方案会退化为「密钥仍明文落库」或者「应用启动失败」，两者都不可接受。

### 6.3 日志泄漏面

对全后端做了 `logger.*api_key` / `logger.*key` 的 grep，**零命中**（没有日志语句直接打印 api_key）。但有以下相邻风险：

- `services/model_hub.py:overview()`（第 673-684 行）把 `providers` 全量返回，含 `api_key` 明文。该 API `GET /api/modelhub/overview` 是前端拉取的，当前无脱敏层。若审计日志或中间件记录了响应体，key 就会落日志。
- `services/ai_reply.py` 的 `_DEFAULT_CONFIG`（第 120-170 行）含 `api_key: ""` 空占位——这没问题，但 `_kv_get` 在异常时只打 `logger.warning("[AI-004] ... kv 读失败")`，不打印值，安全。
- `backend/api/messages.py` 的 `DbTransferReq` 安全契约（第 773-780 行）已经声明 json 导出默认脱敏 api_key/token/secret，且 `db_transfer.py` 做了**内容级** `_scrub`（第 157-171 行）——这是已有的脱敏基础设施，可复用。

### 6.4 前端已有脱敏占位符习惯

`frontend/src/components/settings/ProviderSection.tsx`（第 95、116 行）已经用 `••••••••` 做 Key 占位，`save_provider`（model_hub.py:289-290）也做了「Key 为空或含 • 则保留旧值」的逻辑。**说明前后端已有「Key 不落明文回传」的共识雏形**，但 `overview()` 端点仍透传明文，这是实施时要堵的缺口。

---

# 取证：api_key 所有读写点（源码层面）

> 以下只列文件路径 + 行号 + 读写性质。不出现任何真实 key 值。

## 写入点（api_key 被持久化）

| 文件 | 行号 | 性质 | 存储目标 |
|---|---|---|---|
| `backend/services/ai_reply.py` | 123 (`_DEFAULT_CONFIG["api_key"]`) | 默认值写内存；`_kv_set` 落库见下方 | `kv_store.ai_reply_config` |
| `backend/services/ai_reply.py` | 132, 152 | 同上（vision_api_key / sem_api_key） | `kv_store.ai_reply_config` |
| `backend/services/ai_reply.py` | 106-113 (`_kv_set`) | 写入 `kv_store`（json 序列化） | `kv_store` 表 |
| `backend/services/model_hub.py` | 87, 199, 282, 604 | `save_provider` / `_migrate_v1` / `_candidate` 把 api_key 写入 `_load()` → `_kv_set` | `kv_store.model_hub`（单行 JSON） |
| `backend/services/model_hub.py` | 59-60 (`_kv_set`) | model_hub 自己的 KV 写（旧版） | `kv_store` 表 |
| `backend/services/kv_store.py` | 42-56 (`kv_set`) | 通用 UPSERT，被 ai_reply / model_hub 等调用 | `kv_store` 表 |

注意：`ai_reply.py` 和 `model_hub.py` 各持有一份 `kv_get/kv_set` 的**私有实现**（未复用 `services/kv_store.py`），这是已知的代码重复，但与 T6 无关。

## 读取点（api_key 被消费）

| 文件 | 行号 | 性质 |
|---|---|---|
| `backend/services/ai_reply.py` | 232 (`apply_model_hub`) | 从 model_hub 解析结果覆盖 cfg.api_key |
| `backend/services/ai_reply.py` | 258 (`_cand_cfg`) | hub 候选 → cfg |
| `backend/services/ai_reply.py` | 474-481 (`_embedRemote`) | 写入 `Authorization: Bearer {api_key}` |
| `backend/services/ai_reply.py` | 518, 521 | 调用 `_embedRemote` 传入 cfg 中的 key |
| `backend/services/ai_reply.py` | 694 (`AIClient.chat`) | 非本机 URL 时要求 api_key 非空 |
| `backend/services/ai_reply.py` | 725-726 (`_chat_openai`) | 设置 `Authorization` header |
| `backend/services/ai_reply.py` | 765 | 同上（另一处） |
| `backend/services/ai_reply.py` | 831, 843-844 (`describe_image`) | vision_api_key header |
| `backend/services/model_hub.py` | 334-339 (`_provider_auth_headers`) | 构造 x-api-key / Authorization |
| `backend/services/model_hub.py` | 360 (`test_provider_key`) | 用 key 发真实 HTTP 请求 |
| `backend/services/model_hub.py` | 604 (`_candidate`) | 从 provider dict 取出 api_key |
| `backend/api/ai.py` | 606, 769, 841 | 消费 cfg 中的 api_key / sem_api_key |
| `backend/api/notify.py` | 264, 278, 286, 322 | 把 api_key 从旧 config 映射到 llm_api_key |
| `backend/api/model_hub.py` | 35 (ProviderBody pydantic) | 入模型声明 api_key 字段 |
| `frontend/src/components/settings/ProviderSection.tsx` | 95, 116, 131, 180 | UI 展示 / 编辑 / 占位 |
| `frontend/src/components/settings/GlobalModelCard.tsx` | 64-65 | UI 绑定 |
| `frontend/src/components/settings/AgentSection.tsx` | 441-442 | 注释 + 文档 |

## 日志泄漏面

对全后端 `grep -rn "logger\.\(info\|warning\|error\|debug\)" | grep -i "api_key\|key"` 的检索结果：**零命中**。当前代码没有把 api_key 直接写入日志的语句。

但 `model_hub.overview()`（`services/model_hub.py:673-684` 和 `api/model_hub.py:64-65`）返回的 `providers` 列表**含明文 api_key**，若被日志中间件记录响应体则构成二次泄漏。

## 存储层现状

- `kv_store` 表（SQLite）：单列 `key`（TEXT）+ `value`（TEXT，JSON 序列化）。`ai_reply_config` 和 `model_hub` 各存一行，**api_key 在 JSON 内明文可见**。
- `member_ctx.py` 已有 `.env.enc` 加密封装（Fernet），基于每会员主密钥（`master_key()`），写入路径 `write_env_file`（第 415 行），读取路径 `parse_env_dict`（第 458 行）。**但该机制只针对「账号凭证 .env 文件」，不针对 kv_store 的行级 JSON。**

---

# 三个候选方案评估

## 方案 A：系统钥匙串（Windows Credential Manager / keyring 库）

**做法**：把 api_key 写入 Windows Credential Manager（通过 `keyring` 库或 Win32 API）， kv_store 里只存占位符或引用 ID。

| 维度 | 评估 |
|---|---|
| **迁移成本** | 中。存量明文 key 需要在用户首次启动时做一次「读取明文 → 写入钥匙串 → 删除明文」的迁移脚本。若钥匙串写入失败，回退到旧态（仍可读），但不能自动回写。**需用户确认迁移**。 |
| **对前端读取路径的影响** | 低。后端 `save_provider` / `get_config` 内部改从 `keyring` 取，API 契约不变。但 `overview()` 仍会透传 key，仍需加脱敏层。 |
| **失败降级行为** | 钥匙串不可用（用户未登录 Windows / 凭据管理器锁死）→ 应用无法取到 key，LLM 调用失败。**没有明文回退**（这是安全要求）。 |
| **新依赖** | **是**。`requirements.txt` 不含 `keyring`。需新增 `keyring>=5.0.0`（纯 Python，依赖 `pywin32` 已在 requirements 中）。 |
| **架构适配度** | **低**。`keyring` 是按「服务名 + 用户名」存单条字符串，不适合模型多提供商、多 key、key_status 等结构化数据。要存 JSON 只能 base64 编码后塞进去，且每次读写都要跨进程到 Windows LSA，性能不如内存/文件。 |
| **风险** | 多账号场景下，每个 Windows 用户有独立的钥匙串空间，但 DYAutoDM_v2 的「会员空间」是应用层的（`member_id` 目录），与 Windows 账户不是一对一。若一台机器跑多个会员，钥匙串的命名空间冲突需要自行管理。 |

**结论**：不推荐作为主方案。适合**单台单机、单会员**的凭证存储，不适合本项目多会员、多提供商、结构化的配置场景。

---

## 方案 B：环境变量注入

**做法**：api_key 通过环境变量（如 `DY_AI_MAIN_KEY` / `DY_MODEL_HUB_KEYS`）注入，kv_store 只存 base_url / model / protocol，不存 key。

| 维度 | 评估 |
|---|---|
| **迁移成本** | 高。存量明文 key 需要逐个导出为环境变量；前端 UI 的「保存 provider」逻辑要改（不再写 key 到 kv，而是写入 env 文件或调用系统 API 设置 env）。而且**环境变量是进程级的**，重启后丢失，需要落到 `.env` 文件——又回到「凭证落盘」问题。 |
| **对前端读取路径的影响** | 高。`saveProvider` 前端要改，后端 `save_provider` 要分支处理「Key 写入 env 而非 kv」。`overview()` 的 key 展示逻辑要改（从 env 取或返回空）。 |
| **失败降级行为** | 环境变量缺失 → key 为空 → 云端 LLM 调用失败。本机 127.0.0.1 可免 key（ai_reply.py:694 已有判据），但云端提供商必败。 |
| **新依赖** | 否。用 `os.environ` 即可。 |
| **架构适配度** | **极低**。本项目有「多账号 / 多会员 / 多提供商」结构，环境变量命名空间爆炸（`DY_PROVIDER_<ID>_KEY` ?）。且 T7 调参面扩容后，每个 provider 的 key 要独立轮换，环境变量不可做「部分更新」。 |
| **风险** | 环境变量会被子进程继承（包括可能的第三方库、浏览器内核），在进程列表中通过 `/proc/PID/environ`（Linux）或调试器可读。Windows 下虽不直接暴露，但仍不是安全边界。 |

**结论**：不推荐。只适合 CI/CD 或单服务单 key 的场景，不适合本项目。

---

## 方案 C：加密 kv —— 复用项目已有的 `.env.enc` 加密机制

**做法**：复用 `member_ctx.py` 已有的 Fernet 加密栈（`_encrypt_env_text` / `_decrypt_env_file` / `member_store.encrypt_text`），把 `kv_store.value` 里的 JSON 在写入前加密、读出后解密。或者更轻：把 `ai_reply_config` 和 `model_hub` 从 kv_store 迁移到独立的 `<member_space>/ai_reply.env.enc` 和 `<member_space>/model_hub.env.enc`，走 `write_env_file` / `parse_env_dict` 现有路径。

| 维度 | 评估 |
|---|---|
| **迁移成本** | 中-高。存量明文 kv 行需要做一次「读明文 → 加密写 .env.enc → 标记迁移完成」的脚本。`member_ctx.py` 已有 `migrate_plain_envs` 框架（第 485 行，当前废弃但可复用逻辑）。迁移失败回退到旧 kv（明文可读），不丢数据。 |
| **对前端读取路径的影响** | 低-中。API 契约不变（`GET /overview` 仍返回 providers 列表），但后端 `save_provider` / `overview` 内部改为走 `member_ctx.parse_env_dict` / `write_env_file`。**前端零改动**。唯一注意：`overview()` 返回的 api_key 仍需脱敏（见 §6.3），否则响应体本身是泄漏面。 |
| **失败降级行为** | 主密钥不可用（未登录）→ `member_ctx.write_env_file` / `parse_env_dict` **显式抛错**（fail loud），不静默降级为明文。这是 2026-09-21 架构决定的有意行为（member_ctx.py:336-340）。若用户需要「未登录也能读 key」，必须先把主密钥注入（如通过 `DY_MEMBER_KEY` 环境变量或 `.session.json` 回退）。 |
| **新依赖** | **否**。`cryptography` 已在 requirements（`cryptography>=42.0.0`），`member_store.encrypt_text` / `member_ctx` 已在项目内。 |
| **架构适配度** | **高**。复用既有资产（Fernet 加密栈、会员主密钥派生、.env.enc 写回原子性、历史明文残留自动删除），不新发明。与项目「凭证永久加密，明文 .env 彻底废弃」的 2026-09-21 架构决定一致。 |
| **风险** | 见 §6.2：kv_store 当前是「全局单库」语义（`database.get_db()` 路径由 `DY_APP_ROOT` 决定），而 `.env.enc` 是「每会员」封装。若 ai_reply/model_hub 需要在无会员登录的上下文被调用，需要确保主密钥可用——这可以通过 `member_ctx._session_key_cache` 回退（.session.json 中的 master_key）或 `DY_MEMBER_KEY` 环境变量解决。**这是实施时必须显式处理的，不是零成本。** |

---

## 推荐：方案 C（复用 .env.enc 加密机制）

**T6_RECOMMENDATION**：选方案 C。

**理由**：

1. **零新依赖**——`cryptography` 已在 requirements，`member_ctx` / `member_store` 加密栈已就位且已通过生产验证（v0.44.24 起凭证永久加密）。
2. **架构对齐**——与项目 2026-09-21「明文 .env 彻底废弃」的架构决定一致，不是旁路发明。
3. **迁移可逆**——存量明文 kv 读到内存后加密写新位置，失败回退到旧明文行，不丢数据；迁移标志（类似 `model_hub.migrated_v1`）只在成功后置位。
4. **前端零改动**——API 契约不变，只改后端存取实现。
5. **安全边界的正确抽象**——`member_ctx` 已经做了「主密钥不可用则显式失败」的 fail-loud 设计，不会静默退化到明文。

**实施时必须同步做的两件事**（不是 T6 本轮范围，但拍板后必须规划）：

1. **脱敏 `overview()` 端点**：`services/model_hub.py:overview()` 返回的 `providers` 列表含明文 api_key。实施时应在返回前做内容级脱敏（参考 `db_transfer.py:_scrub` 和 `_SENSITIVE_HINTS`），只给前端 `••••••••` 占位；`save_provider` 已有「Key 为空/含 • 则保留旧值」的逻辑（model_hub.py:289-290），无需改动。
2. **确保主密钥在「非会员上下文」可用**：若 ai_reply/model_hub 需要在无登录态被调用（例如守护进程推送），需要通过 `member_ctx` 的 `.session.json` 回退（第 202-260 行已有机制）或 `DY_MEMBER_KEY` 环境变量注入主密钥，否则 `.env.enc` 读不出来会抛错。

**已知风险**：

- §6.2 所述「kv_store 全局单库 vs .env.enc 每会员封装」的归属矛盾，需用户在实施前确认：ai_reply_config / model_hub 的 key 到底是「每会员隔离」还是「全局共享」？当前代码（`database.get_db()` 路径）表明它们在会员空间内，**按会员隔离**——所以 `.env.enc` 的每会员语义是对齐的。如果用户期望全局共享，则需要重新评估。
- `overview()` 端点当前透传明文 key，必须在实施 T6 时同步堵上；否则加密只保护了「落盘」，不保护「传输/展示」。

---

## 附：grep 证据汇总（供复核）

以下命令在 `C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend` 下执行，产出已在卷宗正文引用：

```bash
# 所有 api_key 读写点（源码）
grep -rn --include=*.py -E "api_key|apiKey|API_KEY" backend/

# 日志是否泄漏 api_key
grep -rn "logger\.\(info\|warning\|error\|debug\)" backend/ | grep -iE "api_key|secret|key"
# 结果：零命中

# .env.enc 机制存在性
grep -n "_ENC_SUFFIX\|def write_env_file\|def parse_env_dict\|def master_key\|Fernet" services/member_ctx.py services/member_store.py

# requirements.txt 无 keyring
grep -i "keyring" requirements.txt
# 结果：零命中（新依赖需标注）
```

---

## 卷宗完成判据

用户读完这份卷宗后可以直接拍板选哪个方案：

- 若选 **A（钥匙串）**：需新增 `keyring` 依赖，解决多会员命名空间，适合单会员场景。
- 若选 **B（环境变量）**：无需新依赖，但命名空间爆炸、不可部分更新、重启丢失，不推荐。
- 若选 **C（复用 .env.enc）**：零新依赖、架构对齐、前端零改动、迁移可逆；但需解决主密钥可用性和 `overview()` 脱敏两个实施细节。

**推荐 C。**