# ADR-030：browser profile 不再随包分发（消除登录凭证外发与源码树污染）

- **状态**：Accepted
- **日期**：2026-09-29
- **决策者**：LOX（用户拍板）
- **相关**：L-16、ADR-029、铁律 §四（环境隔离）+ §1.1（明文凭证）、`tauri.conf.json`、`check_packaging_contract.py` P4、`check_iron_rules.py` R1

---

## 1. 背景与问题

源码树 `DYAutoDM_v2/{vb_profile_default, vb_profile_dm, pw_profile_dm}`（合计 ~300M）
物理落在源码树内，且被 `src-tauri/tauri.conf.json` 的 `bundle.resources` 引用
（`"../vb_profile_default"` 等）⇒ 每次出正式安装包都会把三者打进 MSI/NSIS。

### 实测取证（2026-09-29，非推断）

| 事实 | 证据 |
|---|---|
| 三者**均含真实抖音登录凭证** | Cookies SQLite 中命中 `sessionid` / `sessionid_ss` / `sid_guard` / `sid_tt` / `uid_tt` / `uid_tt_ss` / `passport_csrf_token` |
| 三者**已被 `.gitignore` 忽略**（从不入库） | `DYAutoDM_v2/.gitignore:88-89` |
| 运行时**不需要**它们随包 | 真正在用的是**按账号隔离**的 `accounts/<name>/profile`（单 profile 铁律）；部署态 `app_root()`=`C:\temp\dyautodm_design` 且**已有** `vb_profile_default`（运行期生成） |

⇒ 现状是**双重违规**：① 源码树污染（违反 §四「源码树零运行期产物」）；
② **凭证泄露**（含登录 cookie 的 profile 被打进分发包外发，违反 §1.1 精神）。

### 为什么原设计会引用它们（历史）

`bundle.resources` 里的 profile 引用自 `29139a6`（打包 v0.3.0）起就存在，属于早期
「把整份已登录 profile 随包分发以便开箱即用」的做法。该做法在**单账号自用**下可运行，
但：① 假设了「分发包只给自己用」；② 与后来的**单 profile 铁律**（每账号独立 profile）
和**资源根/数据根分离**（ADR-029 同批的 `f3c76e9`）方向相反；③ 直接泄露凭证。

---

## 2. 决策

**不打包任何 browser profile；profile 只存在于可写数据根（`app_root()`），由浏览器在
首次运行时自建，登录由用户现场扫码完成。**

### D1 · 剥离随包 profile

- `tauri.conf.json` 的 `bundle.resources` **移除** 3 条 profile 映射。
- 保留 `binaries/appinternals` 映射（sidecar contents，必须随包）。

### D2 · 源码树冗余副本删除

- 删除源码树内 3 个 profile 副本（`.gitignore` 已忽略，全仓仅死引用）。
- **不触碰部署根 `C:\temp\dyautodm_design\vb_profile_default`** —— 那是运行期数据（活）。

### D3 · 首装无需随包模板（能力已在）

`resolve_profile_dir(name)` 在**无 seed 源**时**仅返回** `<app_root>/<name>`，
浏览器随后在空目录上自行初始化 → 首次运行自建、用户扫码即可。
（历史零消费常量 `SEEDED_PROFILES` 已删除，避免暗示「存在随包模板」。）

> `resource_root()` **必须保留** —— 除 profile 种子化外，它还用于在安装态定位
> sidecar 可执行文件（`auto_dm/accounts.py:412`）。

### D4 · 机械门禁（双向防复发）

| 门禁 | 判据 | 方向 |
|---|---|---|
| `check_packaging_contract.py` **P4（反转）** | `bundle.resources` **不得**含任何 profile 键 | 防「重新加回打包」 |
| `check_packaging_contract.py` / `package_installer.py` wix 自证 | WiX 源中**不得**出现 profile 具名子目录 | 防「打包布局回落」 |
| `check_iron_rules.py` **R1（扩展）** | 源码树**不得**出现 `vb_profile_default` / `vb_profile_dm` / `pw_profile_dm` / `profiles` | 防「运行期落回源码树」 |

---

## 3. 备选方案与否决理由

| 方案 | 否决理由 |
|---|---|
| 保留打包，但用**脱敏空壳**替换 | 空壳需把整套反爬指纹脚手架（`_camoufox`/`cert9.db`/`key4.db` 等）脱敏导出，工程量大且需长期维护「不泄漏」保证；收益（开箱即用）对**单账号自用**并不成立 |
| 改从 `assets/` 目录随包（源码树外） | 仍是「随包分发凭证」，未解决泄露 |
| 只删源码树副本，保留 `bundle.resources` 引用 | **构建直接失败**（`resource path ../vb_profile_default doesn't exist`） |

---

## 4. 影响与验证

| # | 判据 | 结果 |
|---|---|---|
| V1 | 源码树不再有 3 个 profile | ✅ 已删；R1 PASS |
| V2 | 打包契约 P4 判「无随包 profile」 | ✅ PASS |
| V3 | `tauri build` 正常（配置合法） | 见 §5 |
| V4 | 部署根 profile 未被误删（活数据） | ✅ 保留 |
| V5 | 首装无 profile 仍可运行（浏览器自建） | 读码确认 `resolve_profile_dir` 返回路径 |

## 5. 验证结果（2026-09-29 实机）

| # | 判据 | 结果 |
|---|---|---|
| V1 | 源码树不再有 3 个 profile | ✅ 已删；铁律门禁 **R1 PASS**（源码树无数据根内容） |
| V2 | 打包契约 P4 判「无随包 profile」 | ✅ **P4 PASS**（9/9） |
| V3 | `tauri build` 正常（移除资源不破坏构建） | ✅ 构建成功（23s），日志原文 `Built application at: C:\temp\dyautodm_build\design\debug\dyautodm-v2.exe` |
| V4 | 部署根 profile 未被误删（活数据） | ✅ `C:\temp\dyautodm_design\vb_profile_default` 保留 |
| V5 | 首装无 profile 仍可运行（浏览器自建） | ✅ 读码确认 `resolve_profile_dir` 无 seed 源时仅返回 `app_root/<name>` |
| V6 | R1 负控（源码树出现 profile ⇒ 变红） | ✅ 删除前 R1 实测报红、删除后转 PASS（真实负控） |

**成效**：源码树移除 ~300M；`dyautodm` 源码树稳态 **1.1G**（较起始 42G）。

## 6. 边界（诚实标注）

- **裸源码运行（未设 `DY_APP_ROOT`）** 时 `app_root()` = 项目根，profile 会落在项目根
  → 这正是 R1 要拦的形态。**design/test 工作流始终显式设 `DY_APP_ROOT`**，故不受影响；
  R1 为兜底。
- 既有测试环境里**已存在**的 `accounts/<name>/profile`（含登录态）**不动** —— 它们是运行期数据。

---

## 7. 附带修复（L-16 同批，2026-09-30）

### 7.1 `link_resolve` 兜底 profile 缺陷（真缺陷，已修）

`_browser_resolve()` 的**直开浏览器兜底路径**调用 `launch_sync(...)` 时**漏传
`user_data_dir`**（只传 `account=`）→ 按单 profile 铁律，`launch_sync` 会 **fail-loud 抛错**
⇒ 该兜底实际**不可用**（真到 BCC `/resolve_url` 失败时才发现）。
**修法**：启动前由 `accounts.profile_dir_of(env_path_of(account_name))` 推导固定 profile；
无账号名则显式 fail-loud 跳过（不回落临时/字面量 profile）。

### 7.2 消除 3 处误导性死参数（同类缺陷温床）

`enrich_auth` / `get_current_auth` / `login_grab_ticket` 的 `user_data_dir="pw_profile_dm"`
形参**从未被使用**（实现恒以 `profile_dir_of(env_path)` 覆盖）—— 死参数既误导读者、又让
「单 profile 铁律」在后续改动中失真。全部移除。

### 7.3 新增门禁 R11（防复发）

`check_iron_rules.py` **R11**：源码中不得出现 `user_data_dir="<遗留名>"`
（`vb_profile_default` / `vb_profile_dm` / `pw_profile_dm`），含 def 默认值。
（本项目已有同类先例：`web_probe.py:96` 记录过 `user_data_dir="pw_profile_probe"` 死参数被移除。）

### 7.4 修复陈旧自检（误导性误报）

`scripts/diag/check_packed_resources.py` 硬编码 `_internal` 目录名，而 contents 目录
v0.45.97 起已改名 `appinternals` ⇒ **每轮构建都误报「未找到 _internal 目录」**。
改为自动探测（`appinternals` 优先 / 兼容 `_internal`）。修后复跑：**260 个资源文件齐备 ✅**。

### 7.5 MSI 实测（回答「不打包 profile 对 MSI 有无影响」）

实机出包 v0.45.99 MSI（250,937,397 B，sha256 `3692586e…`）：
- `✅ ProductVersion = 0.45.99`
- `✅ contents 目录落为具名子目录`
- `✅ 无 $RESOURCE/_up_ 目录`
- **`✅ 无随包 profile（不泄露登录凭证）`** ← 本 ADR 的直接判据
- WiX 源 `main.wxs` 实测：`appinternals` 7358 处、**零 profile 资源**

⇒ **不打包 profile 不改变 MSI 功能**：旧方案里那 3 个 profile 是 Chromium 格式，
而运行时实际用 Camoufox（`_camoufox/`）且按账号隔离 ⇒ 即便打了也用不上，属「含凭证 + 无效」的死重量。
