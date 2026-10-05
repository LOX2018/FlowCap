# ADR-006 · 凭证永久加密：明文 `.env` 彻底废弃（v0.44.24）

> 日期：2026-09-21　分支：`design/better-douyin`　状态：**已实施**
> 决策人：LOX（用户拍板）；执行：Hermes

## 背景

会员体系（v0.37.0 起）引入 `.env` 的 Fernet 整文件加密封装（`.env.enc`），
但一直保留**明文 `.env` 兼容分支**（读写两端），用于「非会员空间 / 调试态」与
「旧数据一次性迁移」。随着会员体系被确认为**唯一确定形态**，该兼容分支已无存在必要，
且构成**实际风险**。

## 决策

**凭证永久加密，明文 `.env` 彻底废弃。** `services/member_ctx` 成为凭证读写的**唯一入口**，
只认 `<env_path>.enc`；任何「写成明文 / 按明文读」的路径一律**显式失败**。

## 理由（问题链）

1. **静默明文落盘漏洞**：`save_credential` 存在一条**绕过 `DY_ALLOW_PLAINTEXT_ENV` 守卫**的
   路径 —— `is_member_env()` 为假时（无会员上下文 + 尚无 `.enc`，如子进程/直接跑 sidecar）
   直接 `set_key` 写明文凭证（含 DY_COOKIES / 私钥）。明文落盘等同凭证失守、且无告警。
2. **明文分支造成恒假判据**：多处用 `os.path.exists(env_path)` 判凭证是否存在 ——
   会员空间内明文文件已不存在（只有 `.enc`）⇒ **恒 False**（`auth_helper`、`login_capture`、
   `credentials_complete`、`_read_status`、`BCC.status.logged_in` 等）。是「假失败」的滋生地。
3. **零迁移成本窗口**：实测磁盘 members 空间明文 `.env` **0 个**、`.env.enc` 2 个，
   环境根无游离明文 ⇒ 此刻废弃最干净。

## 变更

| 模块 | 变更 |
|---|---|
| `services/member_ctx` | `write_env_file` 删明文分支（主密钥不可用即抛 `RuntimeError`+`MEM-007`）；`parse_env_dict/text` 只读 `.enc`；新增 `env_exists()`；`is_member_env` 语义收敛为「加密凭证是否存在」；`migrate_plain_envs` 废弃零操作 |
| `dy_apis/login_api` | `save_credential` 统一走 `member_ctx.write_env_file`，**删除 `DY_ALLOW_PLAINTEXT_ENV` 逃生口**（失败报 `AUTH-054`）；`_load_auth_from_env` 只经 `member_ctx` 读 |
| `auto_dm/accounts` | `_read_status` / `credentials_complete` / `_probe_uid` 统一 `member_ctx` + `env_exists`；`create_account` 不再预创建空明文；`_strip_credential_lines` / `clear_credentials_of` 改为操作 `.enc` |
| `auth_helper` | `save_cookie_to_env` → `member_ctx.write_env_file`；`enrich_auth` 不再 `load_dotenv` 注入 os.environ；`get_current_auth` 用 `env_exists` |
| `utils/common_util` | `load_env(env_path)` 改经 `member_ctx` 精确读（不再 `load_dotenv` 污染 os.environ） |
| `core/auto_dm` | `_credential_age` 取 `.enc` mtime；`_build_one_auth` 统一 `member_ctx` |
| `vbrowser` | 代理读取统一 `member_ctx`（去掉明文逐行读） |
| `login_capture` | `snapshot_old_env` 只认 `.enc`（**修掉「首次登录」误报**） |
| `daemon/browser_daemon` | `status.logged_in` 用 `env_exists` |

## 语义后果（有意为之）

- **未登录时不允许任何凭证读写**（主密钥不可用 → 显式失败）。这是「凭证永久加密」的必然推论。
- **磁盘上若出现明文 `.env`，系统拒绝读取**（不再静默使用未加密凭证）——正是本 ADR 想要的语义。
- 旧的一条 `AUTH-041` 编号已被「批量查昵称」占用（历史撞车），故新增 `AUTH-054`（不撞车）。

## 新错误码（六段契约）

- `AUTH-054` — 凭证加密写盘失败，已拒绝明文降级（凭证未写入）
- `MEM-007` — 拒绝写入/读取凭证：主密钥不可用（明文 .env 已废弃）

## 验证

- 守卫测试 `backend/test_plaintext_env_deprecation.py`（6 项，含「不读明文 / 不写明文 / env_exists 只认 .enc / 迁移零操作 / 全仓无 set_key」），**反向验证**（恢复明文分支 → 红，还原 → 绿）。
- 全量回归 460 项 = 基线 443 + 11（自锁）+ 6（本 ADR）；唯一失败 `test_member_smoke` 为**存量**
  （stash 本批改动后在同名同行同异常失败）。
- 全仓明文路径终扫：`set_key` / `dotenv_values(env_path)` / `load_dotenv(env_path)` 0 命中（仅注释）。
