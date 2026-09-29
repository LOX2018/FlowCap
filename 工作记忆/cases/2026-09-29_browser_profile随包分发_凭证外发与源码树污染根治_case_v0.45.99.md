# 案例：browser profile 随包分发缺陷 —— 凭证外发 + 源码树污染根治（v0.45.99）

- **日期**：2026-09-29
- **分支**：design（环境 `C:\temp\dyautodm_design`）
- **类型**：安全/架构缺陷（凭证外发） + 源码树卫生
- **ADR**：`docs/adr/ADR-030-no-bundle-browser-profile.md`
- **关联**：ADR-029（构建缓存出树）、L-16

---

## 1. 触发

由「源码树为何膨胀到 42G」这一**体量/数据流**问题追查带入 —— 清 `target/`（40G）后，
发现源码树内另有 3 个 browser profile（~300M）被 `tauri.conf.json` 的 `bundle.resources` 引用。

## 2. 取证（实测，非推断）

| 事实 | 证据 |
|---|---|
| 三者均含**真实抖音登录凭证** | Cookies SQLite 命中 `sessionid` / `sessionid_ss` / `sid_guard` / `sid_tt` / `uid_tt` / `uid_tt_ss` / `passport_csrf_token` |
| 已被 `.gitignore` 忽略（从不入库） | `DYAutoDM_v2/.gitignore:88-89` |
| 但被 `bundle.resources` 打进 MSI | `"../vb_profile_default": "vb_profile_default/"` 等 3 条 |
| 运行时真正的 profile 不在这里 | 单 profile 铁律 → `accounts/<name>/profile`；默认账号 → `app_root()/vb_profile_default`（部署根已有） |
| 引用最早引入 | `29139a6`（打包 v0.3.0），早期「整份已登录 profile 随包开箱即用」做法 |

⇒ **双重违规**：① 源码树污染；② **凭证泄露**（含登录态的 profile 随安装包外发）。

## 3. 根因

**设计意图层缺失**：项目从未成文「凭证不得随包外发」这条约束，因此历次
设计符合性审计（R8 数据契约 / 静默失败 / 红线计数 / 全库审计 / OCR 审计）**不可能发现**——
它们问的是「实现是否符合设计」，而这里错的是**设计假设本身**。

**放大机制**：当日建立的打包门禁 `check_packaging_contract.py` 的 **P4 判据原文是
「三个随附 profile 必须映射为子目录」** —— 它把**错误假设固化为正确判据**，每跑一次门禁
都是在「确认这个错误」。即项目自己 `ADR-019` 所指的「假绿门禁」：**门禁只证合规，不证正确**。

**为何边界之外**：三个 profile **gitignored** → 既不入库，也不进任何以「文件/源码」为抓
手的审计扫描面。

## 4. 处置

| 组件 | 内容 |
|---|---|
| `tauri.conf.json` | `bundle.resources` **移除** 3 条 profile 映射（保留 `binaries/appinternals`） |
| 源码树 | 删除 3 个 profile 冗余副本（部署根那份运行期数据**保留**） |
| `check_packaging_contract.py` **P4 反转** | 判据由「必须打包」→「**不得**随包 profile（含登录凭证）」 |
| `package_installer.py` wix 自证 | 由「profile 落为具名子目录」→「**不得**出现 profile 具名子目录」 |
| `check_iron_rules.py` **R1 扩展** | 源码树**不得**出现 `vb_profile_default/…/pw_profile_dm`/`profiles` |
| `vbrowser.py` | 删除零消费死常量 `SEEDED_PROFILES`（原暗示存在随包模板，属误导） |

**能力确认**：`resolve_profile_dir()` 在无 seed 源时**仅返回** `app_root/<name>`，
浏览器自建空 profile ⇒ **首装无需随包模板**，登录由用户现场扫码。
（`resource_root()` 必须保留 —— 还用于安装态定位 sidecar exe。）

## 5. 实机验证

| # | 判据 | 结果 |
|---|---|---|
| V1 | 源码树无 3 个 profile；R1 PASS | ✅ |
| V2 | 打包契约 P4 = 「无随包 profile」 | ✅ 9/9 PASS |
| V3 | `tauri build` 不破（移除资源后） | ✅ 构建成功，产物落 `C:\temp\dyautodm_build\design\debug\dyautodm-v2.exe` |
| V4 | 部署根 profile 未被误删 | ✅ 保留 |
| V5 | 首装可无 profile 运行 | ✅ 读码 + 机制确认 |
| V6 | R1 真实负控 | ✅ 删除前报红 → 删除后 PASS |

## 6. 教训（可迁移）

1. **审计维度必须包含「数据流向/受信边界」**：设计符合性审计**结构性**发现不了
   「设计假设本身错误」。需要独立的**凭证/敏感数据外发审计**维度。
2. **门禁可能固化错误假设**：门禁建立时若把「现状」当「正确」，它就成了错误的守门人。
   建门禁前必须问：**这条判据的依据是「设计意图」还是「当前实现」？**
3. **gitignored 数据在审计视野之外**：忽略入库 ≠ 安全；反而可能让敏感数据**逃过所有扫描**。
4. **体量问题会牵出安全问题**：本次由「为什么 46G」追到「凭证正在被打包外发」——
   数据流追查是与设计审计互补的独立手段。
