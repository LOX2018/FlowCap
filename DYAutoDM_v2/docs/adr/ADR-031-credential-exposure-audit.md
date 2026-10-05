# ADR-031：凭证外发审计维度（数据是否离开受信边界）

- **状态**：Accepted
- **日期**：2026-09-30
- **决策者**：LOX（用户拍板立项）
- **相关**：ADR-030（profile 不随包）、铁律 §1.1（凭证）、§四（环境隔离）、`scripts/check_credential_exposure.py`、`check_iron_rules.py` R12

---

## 1. 背景：为什么既有审计必然发现不了这类缺陷

2026-09-29 实测：3 个含**真实抖音登录凭证**（`sessionid`/`sid_guard`/`sid_tt`/`uid_tt`）
的 browser profile 被 `tauri.conf.json` 的 `bundle.resources` 打进 MSI 分发物，
长期无人发现。追问「为什么历次审计没发现」，定位到**结构性盲区**：

| 审计类型 | 回答的问题 | 为何看不见本缺陷 |
|---|---|---|
| **设计符合性审计**（历次：数据契约 R8 / 静默失败 / 红线计数 / 全库审计 / OCR 审计） | 「实现**是否符合**设计」 | **错的是设计假设本身**（从未成文写「凭证不得外发」）⇒ 结构性盲区 |
| **以文件/源码为抓手的扫描** | 「仓库里有什么」 | 凭证文件被 `.gitignore` 忽略 ⇒ **逃出扫描面**（忽略入库 ≠ 安全） |
| **打包门禁（当时）** | 「打包布局对不对」 | 其 P4 判据**把错误假设固化成正确**（「profile 必须被打包」）⇒ 反而替缺陷背书 |

⇒ 缺的是一个**不同的问题**：**「这份数据会不会离开受信边界？」** 本 ADR 把它立为独立审计维度。

## 2. 决策：新增「凭证外发审计」维度

新增 `scripts/check_credential_exposure.py`，**双档判据、分级不同**：

| 档 | 判据 | 分级 | 理由 |
|---|---|---|---|
| **A DISTRIBUTED** | 含凭证的路径出现在**分发路径**：tauri `bundle.resources`/`externalBin`；或仓库内任何上传/分发命令（`scp`/`rsync`/`ossutil`/`aws s3`/`gh release`…）携带含凭证路径 | **阻断**（exit 1） | 这是**正在泄露**，进的是分发内容 |
| **B STRAY** | 源码树内散落**凭证类文件/目录**（profile 目录 / `members/` / 账号 `.env`·`.enc` / `cookies.sqlite`·`Cookies`·`cert9.db`·`key4.db`·`Login Data`） | **警告** | 应清理，但**尚未外发** → 不阻断提交（否则门禁会被绕过） |

**`.env` 按内容判定**：只有含凭证类键（`*COOKIE*`/`*TICKET*`/`*TOKEN*`/`*SECRET*`/`*API_KEY*`/
`*PRIVATE_KEY*`/`*PASSWORD*`/`*SKEY*`/`*CREDENTIAL*`）才计为凭证文件 —— 避免把
**纯注释墓碑**（如根的 `.env`，2026-09-06 清空后只剩说明）或**非凭证配置**误报。

**排除项**：`.example`/`.template`/`.sample`/`.dist` 样例、`node_modules`/`.git`/
构建产物/`_ext_repos` 参照库。

## 3. 落地：接进铁律门禁（SSOT + 自动执行）

- **SSOT**：判据住在 `check_credential_exposure.py`，**唯一**。
- **执行点**：`check_iron_rules.py` 新增 **R12**（委托 SSOT，`R12-A` 阻断 / `R12-B` 仅警告）⇒
  随 **pre-commit** 自动跑（提交时执行），不靠人记得跑。
- **自检**：`--selftest` 注入「分发路径携带凭证」形态 ⇒ **R12-A 精确报红**（已实测）。

## 4. 验证（实测，非推断）

| # | 判据 | 结果 |
|---|---|---|
| V1 | A 档正控（当前无外发） | ✅ PASS（0 处） |
| V2 | **A 档负控**：把 `../vb_profile_default` 注回 `bundle.resources` | ✅ **FAIL + exit 1**；移除后复绿 |
| V3 | B 档检出源码树真残留 | ✅ 实测检出 `backend/.env`、`.env.stale_backup_*`（后删） |
| V4 | `.env` 按内容判定（纯注释不误报） | ✅ 根 `.env`（纯注释）不计；`backend/.env`（含 TOKEN）计入 |
| V5 | R12 分级正确（B 不阻断） | ✅ 有 B 残留时 `check_iron_rules` exit=0 |
| V6 | 挂进自动入口 | ✅ pre-commit → check_iron_rules → R12 |

## 5. 边界（诚实标注）

- 覆盖的是**分发路径 + 仓库内上传命令**；对**仓库外的**分发动作（手动拷贝、第三方
  打包工具在仓外执行）无法覆盖 —— 那超出机械判据的可及范围。
- `backend/.env`（含图床 `TUCDN_TOKEN`）**是代码实际使用的配置**（`image_host.py` 经
  `load_dotenv` 读），属**有意保留**的 B 档残留；其 token 非抖音账号凭证，风险等级低于
  账号登录态。若将来迁移到 `app_config`/DB，可从 B 档移除。
- 不覆盖「凭证在**运行期**被外发」（如某代码路径主动上传凭证）—— 那属行为层，
  需运行时出口拦截（见 `mechanical-gate-verification` §三·己）。
