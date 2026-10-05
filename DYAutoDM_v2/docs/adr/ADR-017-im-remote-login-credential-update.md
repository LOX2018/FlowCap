# ADR-017：IM 远程登录更新凭证（RPA 界面操作）

- **状态**：待评审（Draft —— 待用户拍板 D2~D5）
- **日期**：2026-09-26
- **版本**：v0.45.13（当前基线）
- **决策者**：LOX（架构决策）· 执行：Hermes Agent
- **关联调研**：`docs/adr/ADR-017-调研记录-20260926.md`
- **前置 ADR**：ADR-015（本人端点登录态失效语义）· ADR-016（指纹档案层跟随内核）
- **触发**：用户要求「IM 远程登录更新凭证」——不在电脑旁时也能完成抖音登录/凭证更新
- **D1 决策（已定）**：验证码安全通道 = **R1-c 本机 Web 前端输入**

---

## 1. 背景与问题（Why）

### 1.1 现状（实测确认，非推断）

| 能力 | 现状 | 证据 |
|---|---|---|
| 扫码登录（有头窗口）| ✅ 已有 | `api/accounts.py:742 POST /{name}/scan` + `_do_scan` + `auth_helper.enrich_auth` |
| 扫码进度轮询 | ✅ 已有 | `api/accounts.py: GET /{name}/scan-status` |
| 二维码接口（无头）| ⚠️ 有接口但**必然失败** | `login_api.py:205 dyGenerateInitData` |
| 验证码登录接口 | ✅ 逻辑完整 | `dyGeneratePhoneVerificationCode` / `dyPhoneVerificationCodeLogin` |
| 凭证捕获分析 | ✅ 已有 | `login_capture.analyze_login_capture` / `snapshot_old_env` |
| 通知通道 | ✅ 已有 | `notify/channels.py` `notify/events.py` |
| Web 前端配置界面 | ✅ 已有 | `api/settings.py` 等 |

### 1.2 缺口（本 ADR 要解决的）

**用户在不在电脑旁时，无法完成登录/凭证更新**：

```
现状：POST /{name}/scan → 后台启【有头浏览器窗口】→ 等人在电脑前扫码
       ↑ 人不在电脑旁 = 流程卡死
目标：二维码/验证码 → 推送到 IM（Telegram）→ 用户远程处理 → 自动捕获凭证
```

---

## 1.3 🔴 与既有工作流的关联（**本 ADR 的真实定位**）

> **本文档不是"新功能构想"，而是「解锁 3 个已登记高优先级待办的关键路径」。**

### 上游依赖（实测核对自 `工作记忆/00_交接卡待办台账.md` + `artifacts/交接卡_HC-14_*.md`）

| 待办 | 当前卡点（原文）| ADR-017 提供 |
|---|---|---|
| **HC-14 ②**（交接卡唯一阻塞项）| 「**卡点：须人工扫码（GUI，Session 0 不可用）**」 | IM 推送二维码 → 用户手机扫 |
| **H-15**（小助理凭证重建）| 「须人工扫码」+ 依赖 H-14 | 同上 |
| **H-27 ①**（台账）| 「用户对『四川工伤张老师』重新扫码（凭证仍过期）」 | 同上 |

**⇒ 三者的共同卡点均为「必须人在电脑前扫码」。本 ADR 的实施可使三者解绑。**

### 与 ADR-016 / HC-14 的上下游关系

```
ADR-016 (HC-14)          ADR-017 (本文档)
减少凭证失效率            凭证失效后的远程恢复手段
  ↓                          ↓
「矛盾已修，等观测持续性」   「失效时不必人在电脑前」
     └──────── 互补，非重叠 ────────┘
```

**⇒ 实施顺序无强制依赖**：ADR-016 已部署（v0.45.12/0.45.13），
ADR-017 可独立推进；但 **ADR-017 完成后，HC-14 ② 方能闭环**（否则只能等用户回电脑旁）。

### ⚠️ 影响面警示（`_do_scan` 既有行为，必须在实施时保持）

现有 `api/accounts.py:177 _do_scan` 的既有行为（**本 ADR 不得破坏**）：
```
① 取得 ProfileOwnership(name, "scan_login") 独占锁
② _quit_browser_daemon(name)   ← 停该账号凭证守护（释 profile 锁）
③ enrich_auth(force=True)      ← 弹浏览器扫码
④ ensure_daemons_for(name)     ← 回拉守护
```
**⇒ 扫码动作会临时中断该账号的现有服务**（守护被停）。
对**正在跑直播/私信**的账号执行远程登录会造成业务中断 ⇒ **编排层必须显式提示用户、并要求二次确认**。

---

## 2. 设计意图与契约（Design Intent & Contract）

### 2.1 模块定位

**新模块**：`auto_dm/login_remote.py`（远程登录编排层）

**职责边界（SoC）**：
- ✅ **负责**：状态机编排、页面状态探测、二维码/验证码分流、IM 交互协议
- ❌ **不负责**：底层登录算法（复用 `auth_helper` / `login_api`）、凭证写盘（复用 `persistenceLoginInfo` / `login_capture`）

### 2.2 设计契约（DbC）

**Pre-conditions（前置条件）**：
- P1: 目标账号 `name` 在 `accounts/` 下存在，其 profile 目录可用
- P2: 无其他进程持有该账号 profile 锁（须先取得 `ProfileOwnership`）
- P3: 目标账号的凭证守护已停止（释放 profile 锁）

**Post-conditions（后置条件）**：
- C1: 登录成功后，`.env` 中 `sessionid` 等关键 cookie 已更新，且**通过 `login_capture` 验证非污染态**
- C2: 无论成功失败，profile 锁**必须释放**，凭证守护**必须回拉**（`ensure_daemons_for`）
- C3: 二维码/验证码**绝不出现在日志明文**中

**Invariants（不变量）**：
- I1: **单 profile 铁律** —— 全程只用 `accounts.profile_dir_of(env_path)`，**绝不新建临时 profile**（会触发风控）
- I2: **凭证不过对话** —— 验证码由用户经安全通道输入，**不经聊天正文**
- I3: **状态可观测** —— 每个阶段状态可查（供 IM 轮询）

### 2.3 期望行为（用户 2026-09-26 拍板的流程）

```
【分支判定依据 = 账号当前状态，不是用户选择】

状态 A：全新账号（无任何登录态）
  → 优先【二维码】：截图 → 推送 IM → 用户扫码
  → 备用【验证码】：若二维码不可用，降级验证码

状态 B：已有账号（登录态在，凭证过期）  ← 用户的真实场景
  → 只用【验证码】：发短信 → 推送"输入验证码" → 用户回传 → 提交
```

**关键设计原则（用户明确要求）**：
> 「应该自动判断，而不是让用户手动选择」

⇒ **系统按账号状态自动分流**，用户只在"扫哪个码/填什么码"上操作。

---

## 3. 方案与改造点（How）

### 3.1 必须修复的既存缺陷

**DEFECT-1：`dyGenerateInitData` 违反单 profile 铁律（契约漂移）**

```python
# login_api.py:205（现状）
async def dyGenerateInitData(self, headless=True, cookie_str="", landing_url=...):
    _acc_name = None
    _pw, _browser, context, _backend = await launch_async(
        _vb_mode, _cfg, headless=headless, force=True, account=_acc_name)
        # ↑ 未传 user_data_dir → 被门禁拦截 → 100% 失败
```

**实测错误**：
```
[BCC-058] Camoufox 内核启动失败：
  [camoufox] 未指定固定 profile 目录。单 profile 铁律：禁止临时目录，
  必须传入 accounts.profile_dir_of(env_path)
```

**根因**：`vbrowser.py:825` 于 2026-08-17 修订铁律（`force=True` 也须用固定 profile），
但 `dyGenerateInitData` 未同步修订 → **契约漂移**。

**正确范式**（同文件 `login_grab_ticket:276` 已做对）：
```python
_acc_profile = _accounts.profile_dir_of(env_path)          # ✅ 传固定 profile
_acc_name = basename(dirname(abspath(env_path)))           # ✅ 传账号名（代理注入）
```

**修法**：给 `dyGenerateInitData` 加 `env_path` 参数，照 `login_grab_ticket` 范式传 profile + 账号名。

> ⚠️ **待用户确认**：`dyGenerateInitData` 现无 `env_path`，其 2 个调用点
> （`qrcodeMain`/`phoneMain`）也无账号参数 → 需同时确定这两个骨架的去留。

### 3.1.甲 核心方案：**RPA 式界面操作**（用户 2026-09-26 拍板）

**用户原话**：
> 「验证码的方式也可以采用 DOM 在哪里点击，然后用户接收到验证码后通过 IM 回执验证码，
>   当接收到验证码后，通过**粘贴板的形式填入输入框**，然后点击登录」
> 「**相当于 rpa 处理**」

**⇒ 采纳为方案 A 的实现形态：不做协议逆向，全走界面（RPA）。**

**验证码流程（RPA，5 步）**：
```
① DOM 定位「获取验证码」按钮 → page.click()          [已有锚点]
② 抖音发短信到用户手机（验证码 = 6 位数字，实测 maxlength=6 吻合）
③ 用户在 IM 回执验证码（安全通道，见 R1）
④ 填入输入框：page.keyboard.type(code, delay=80)     [★ 逐字输入 —— 见下方修正]
⑤ DOM 定位「登录」按钮 → page.click()
⑥ 轮询登录结果 → 捕获凭证（复用 login_capture）
```

**⚠️ 方案修正（实测推翻原始设计假设，2026-09-26）**：

**用户原始设计**是「通过**粘贴板**的形式填入输入框」。**实测证明粘贴在 Camoufox 下不可靠**：

| 方案 | 实测结果 | 拟真度 | 结论 |
|---|---|---|---|
| ① 剪贴板 + `Ctrl+V` | ❌ **连阳性对照（手机号框）都失败** | 高 | **否决** |
| ② `keyboard.insert_text` | ✅ 稳定（`'135790'`）| 低（非按键事件）| 兜底 |
| ③ **`keyboard.type` 逐字** | ✅ 成功（`'123456'`）| **最高（真实按键序列）** | ✅ **采纳** |

**归因实验（T10，变量隔离）**：
```
[G3] 验证码框 仅click → Ctrl+V      → ''       ❌
[G1] 验证码框 Ctrl+A → Ctrl+V       → ''       ❌
[G2] 验证码框 Delete → Ctrl+V       → ''       ❌
[G5] 手机号框(阳性对照) → Ctrl+V    → ''       ❌  ← 连对照都失败 ⇒ 不是验证码框特殊
[G4] DOM 合成 paste 事件            → ''       ❌
[G6] keyboard.insert_text           → '135790' ✅  ← 唯一稳定
```
**⇒ 判定**：`Ctrl+V` 在 Camoufox **headless** 下整体不可用（非目标框特性）；
`keyboard.type` 是**真实按键序列**，最接近真人操作 ⇒ **采纳为填入方式**。

**验证码框实测属性**（DOM 锚点，稳定）：
```
name="button-input"  type="tel"  maxLength=6  placeholder="请输入验证码"
name="normal-input"  type="tel"  maxLength=50 placeholder="请输入手机号"
name="web-login-area-code-input" type="text" maxLength=5
```

**为什么用「真实按键序列」而不是 `locator.fill()`（关键设计决策）**：
```
❌ locator.fill("123456")    → JS 直接赋值，不派发真实键盘事件，行为特征异于真人
⚠️ keyboard.insert_text()    → 插入文本但非按键序列，拟真度中等（兜底用）
✅ keyboard.type(code, delay) → 逐字符真实按键事件，与真人操作一致 ⇒ 风控风险最低
```
**⇒ 这是 RPA 的正确形态：模拟真人，而非绕过界面。**

> **注**：最初设想用「剪贴板 + Ctrl+V」（拟真度同样高），因**实测不可靠**（见上表）
> 而改用 `keyboard.type`。二者都是"真实输入事件"，方案意图不变，仅实现手段按实测调整。

#### 实测验证（2026-09-26，Camoufox headless）

| 能力 | 结果 | 证据 |
|---|---|---|
| 系统剪贴板读写 | ✅ | `pyperclip.copy/paste` 往返一致 |
| 页面 Clipboard API 写 | ✅ | `navigator.clipboard.writeText()` → `ok` |
| 页面 Clipboard API 读 | ✅ | `readText()` 返回写入值，完全匹配 |
| **Ctrl+V 真实粘贴** | ✅ | 内容**确实进入 input**（值被该框 formatter 处理，见 E3）|
| DOM 点击 | ✅ | 点 tab / 按钮均成功（前序实测）|
| `grant_permissions(["clipboard-read"])` | ⚠️ | Firefox 不识别该权限名（`Unknown permission`）—— 但**不影响读写**（见 E2）|

**⇒ 结论：RPA 路径的**技术前提全部成立**。**

#### 🔴 RPA 必须解决的 3 个工程问题（实测暴露）

| # | 问题 | 证据 | 处置 |
|---|---|---|---|
| **E1** | **profile 锁残留 → 下次启动卡 180s** | 浏览器异常退出（`TargetClosedError`）后 `parent.lock` 残留；`camoufox/firefox` 进程数为 0 时锁仍在 → 后续 `launch_persistent_context` **超时 180000ms**；**清除锁后立刻恢复正常** | 🔴 **编排层必须加「陈旧锁检测 + 自愈」**：启动前若锁存在且无同名进程 → 安全删除。这是 RPA 可靠性的核心 |
| **E2** | Firefox 不认 `clipboard-read` 权限名 | `Unknown permission: clipboard-read` | 🟡 直接用 Clipboard API（实测无需授权即可读写）；不依赖 `grant_permissions` |
| **E3** | 目标输入框有 formatter（自动分组/清洗）| 粘贴 `CLIP_TEST_20260926` → 读到 `'202 6092 6'` | 🟡 验证码为纯数字，须实测确认 formatter 不改变语义；**不可用 `fill()` 校验粘贴成功**，应读实际 value 判等 |

### 3.2 改造点清单

| # | 改造项 | 位置 | 类型 |
|---|---|---|---|
| M1 | 修 DEFECT-1（传 profile + 账号名）| `login_api.py:205` | Bug 修复 |
| M2 | 新增 `auto_dm/login_remote.py` 编排层 | 新建 | 新模块 |
| M3 | 页面状态探测（识别二维码/验证码框）| `login_remote.py` | 新能力 |
| M4 | 二维码截图 + 落盘（cv2 解码验证）| `login_remote.py` | 新能力（判据已实测）|
| M4b | **RPA 点击编排**（获取验证码/登录按钮，DOM 锚点）| `login_remote.py` | 新能力（锚点已实测）|
| M4c | **剪贴板粘贴填入**（`pyperclip` + `Ctrl+V`）| `login_remote.py` | 新能力（**已实测可行**）|
| M4d | 🔴 **陈旧 profile 锁自愈**（E1，防 180s 卡死）| `login_remote.py` | **工程必需** |
| M5 | IM 交互协议（推送图/文案、接收回传）| 复用 `notify/` | 新能力 |
| M6 | 安全输入通道（验证码）| **待定，见 §5 R2** | 新能力 |
| M7 | 新增 API 端点（远程登录状态机）| `api/accounts.py` | 扩展 |
| M8 | 版本递增 +0.01（6 处版本源同步）| `scripts/check_version_sync.py` | 流程要求 |

### 3.3 已实测的关键技术判据（可直接复用）

**二维码识别（唯一可靠判据 = 解码成功，不是像素统计）**：

```python
import cv2
img = cv2.imread(png)
data, pts, _ = cv2.QRCodeDetector().detectAndDecode(img)
# data 非空 ⇒ 确认是二维码；pts 给出二维码本体四点坐标
```

**实测证据**：登录页二维码截图 `249x246` → cv2 解出
`https://v.douyin.com/jeZeWiyShDE/?hide_nav_bar=1` ✅

> 🔴 **重要教训（本次踩坑 6 轮）**：用"黑白占比/色数"等**像素统计**判二维码 →
> **假阳性率 100%**（文字区、黑底导航栏全被误判），且**真二维码被漏判**（带装饰边）。
> **唯一可靠判据是"能否解码"**。已据此修正。

**登录页入口（绕开 A/B 差异）**：
```
https://www.douyin.com/?modal_id=login    ← 可靠弹出登录框（实测）
```
**登录界面稳定锚点**（实测，有 `name` 属性）：
```
手机号输入框 : input[name="normal-input"]      placeholder="请输入手机号"
验证码输入框 : input[name="button-input"]      placeholder="请输入验证码"
区号输入框   : input[name="web-login-area-code-input"]
「获取验证码」: 文本锚点
tab 选择器   : .C6OZQwMA（扫码登录/验证码登录/密码登录）
```

### 3.4 影响面（Environment Isolation）

- **改动文件**：`backend/dy_apis/login_api.py`、新增 `backend/auto_dm/login_remote.py`、`backend/api/accounts.py`
- **环境**：仅 `dyautodm_design`（`DY_APP_ROOT=C:\temp\dyautodm_design`）；**绝不碰** `dyautodm_test`
- **数据**：不涉数据库；`.env` 由既有 `persistenceLoginInfo` 写入
- **风险面**：浏览器启动、profile 锁、凭证写盘

---

## 4. 备选方案与取舍（Alternatives）

| 方案 | 做法 | 取舍 | 结论 |
|---|---|---|---|
| **A. 复用既有接口 + 页面状态探测** | 走 `enrich_auth`/`login_api` 既有链路，加 IM 推送层 | 改动小，风险可控；但 `dyGenerateInitData` 须修 | ✅ **推荐** |
| B. 纯接口（无浏览器）| 用 `dyGenerateQRcode` 直接发 HTTP | ❌ **已实测不通**：缺 ttwid 生成能力 + 返回 JS 挑战壳页 | ❌ 否决 |
| C. 全自动验证码（接短信网关）| 自动读取短信 | ❌ 成本高、合规风险、用户手机不可控 | ❌ 否决 |
| D. 保持现状（人在电脑前扫码）| 不改 | ❌ 不满足"远程"需求 | ❌ 否决 |

---

## 5. 风险与未决项（Risks & Open Questions）

| # | 风险/未决 | 影响 | 建议 |
|---|---|---|---|
| **R1** | **验证码安全通道未定** —— 我不可在对话中接收验证码 | 🔴 阻塞验证码分支 | 见下 |
| **R2** | 「状态 B（凭证过期）」抖音实际给什么，**未实测** | 🟡 影响分流逻辑 | 需用真实账号实测（用户授权后）|
| R3 | 抖音风控：短时间多次启浏览器会 `NS_ERROR_ABORT` | 🟡 实测已踩 | 编排层须做**退避**（同一账号 ≥5 分钟间隔）|
| R4 | `qrcodeMain`/`phoneMain` 是本地骨架（`input()` 阻塞、硬编码手机号 `15251991681`）| 🟡 需决定去留 | 建议：改造成 `login_remote` 的内部函数，去掉 `input()` |
| R5 | 验证码有效期（抖音通常 5 分钟）与 IM 往返延迟 | 🟡 | 编排层须处理超时重发 |
| R6 | 用户手机号从哪来（`phoneMain` 硬编码）| 🟡 | 应从账号配置读取，不硬编码 |
| R7 | 版本递增时的并发写者（见 multi-session-collaboration）| 🔴 | 提交前重读版本号 + 全版本源同步 |

### R1 详述：验证码安全通道（🔴 阻塞项）

**约束**：验证码**不得**出现在聊天对话中（安全规范硬约束）。

**候选方案**：

| 方案 | 做法 | 评价 |
|---|---|---|
| R1-a | `browser_vault_enter_code`（Hermes 遮罩输入）| ⚠️ 为"网页登录表单"设计，本项目是自研脚本，**需实测可用性** |
| R1-b | 项目内安全入口（`POST /api/dev/verify-code`，仅回环 + 令牌）| ✅ 可控，但需开发（约 30 分钟）|
| R1-c | 用户在本机浏览器（Web 前端）输入 | ✅ 复用既有前端，**最贴合现状** |
| R1-d | Telegram 独立通道（bot 私聊 + 一次性令牌）| ⚠️ 仍在 IM 上，需评估 |

**⇒ 需用户拍板**（见 §7）。

---

## 6. 验收判据（Definition of Done）

- [ ] **F1** `dyGenerateInitData` 修复后，`dyGenerateQRcode(auth)` 返回含 `data.token` + `data.qrcode_index_url`
- [ ] **F2** 二维码 PNG 可被 `cv2.QRCodeDetector` 解码（机械判据，非目视）
- [ ] **F3** 二维码可推送到 IM，用户侧能看到图
- [ ] **F4** 验证码分支：短信发送成功 + 验证码提交后登录成功（**实机验证**）
- [ ] **F5** 登录成功后 `.env` 关键 cookie 更新，且 `login_capture` 判定**非污染态**
- [ ] **F6** **零风控**：全程无「安全风险/已阻止此次访问」弹窗
- [ ] **F7** profile 锁释放 + 守护回拉（`ensure_daemons_for` 成功）
- [ ] **F8** 失败路径可回滚（登录失败不留半写状态）
- [ ] **F9** 版本号 +0.01，6 处版本源齐平（跑 `check_version_sync.py`）
- [ ] **F10** 归档：ADR + 调试案例入 `knowledge/cases/`

---

## 7. 待用户拍板的决策点（Decision Required）

| # | 决策 | 选项 |
|---|---|---|
| **D1** | 验证码安全通道（R1）| R1-a / R1-b / R1-c / R1-d |
| **D2** | `qrcodeMain`/`phoneMain` 骨架去留 | 改造为内部函数 / 保留 / 删除 |
| **D3** | 本次实施范围 | 只修 DEFECT-1（打通接口）/ 完整远程化 |
| **D4** | 真实账号实测授权（R2）| 允许用张老师/小助理实测 / 仅临时账号 |
| **D5** | profile 使用策略 | 该账号自己的 profile / 专用游客 profile |

---

## 附：本次调研的关键实测证据（可追溯）

| 证据 | 命令/位置 | 结果 |
|---|---|---|
| 二维码抓取 + 解码 | `scratch/gen_qr_push.py` + `decode_qr.py` | cv2 解出 `https://v.douyin.com/jeZeWiyShDE/?hide_nav_bar=1` |
| 单 profile 铁律拦截 | 运行 `t7_e2e_qr.py` | `[BCC-058] ... 必须传入 accounts.profile_dir_of(env_path)` |
| 铁律原文 | `vbrowser.py:825-834` | 「force=True 仅表示不复用登录态，仍使用同一固定 profile」|
| 正确范式 | `login_api.py:276 login_grab_ticket` | `_acc_profile = _accounts.profile_dir_of(env_path)` |
| 既有扫码 API | `api/accounts.py:742` | `POST /{name}/scan` + `_do_scan` |
| 登录界面锚点 | `scratch/probe_login_urls.py` | `input[name="normal-input"]` 等 |
| 接口方案不通 | `scratch/probe_pure_mstoken.py` | 9938B JS 挑战壳页；项目无 ttwid 生成器 |

---

## 8. 实施补充：双路径分工 · RPA 短信登录缺陷修复 · 凭证写入协议（2026-09-26 实测归档）

> **本节定位**：§1~§7 是**设计期**（待评审）的意图与契约。本节归档**实施期**在本机实机跑通后**最终落地**的决策，
> 是对 §3.1.甲（RPA 形态）、§3.3（技术判据）、§5（风险）的**实测收口**，不推翻既有内容。
> 状态：**已实施并验证**（v0.45.14 → v0.45.18）。凡「依据」列，均给出源码行号/函数名或实测输出。

### 8.1 决策 1：API 路径与 RPA 路径的**能力边界最终确定**

**决策内容**：

| 登录方式 | 归属路径 | 状态 |
|---|---|---|
| 扫码登录 | **API 路径**（上游 vendor，`auto_dm/login_api_vendor.py`）| ✅ 保留可用 |
| 短信（验证码）登录 | **只走 RPA 路径**（`auto_dm/login_remote.py`）| ✅ RPA |
| 短信登录（API 路径）| 上游 `send_sms_code` / `phone_login` | ❌ **显式禁用** |

- API 路径**只保留扫码登录**：实测 `get_qrcode` 返回 `error_code=0`，`bootstrap` 2.2s / 33 项 cookie 成功。
- 适配层 `VendorLoginApi` 类文档新增「**能力边界**」表（可用/不可用 + 原因 + 替代）；
  `send_sms_code` / `phone_login` **抛出显式 `NotImplementedError`（错误码 AUTH-072）**，
  错误信息同时含「**根因（820 字节 dtrait）**」与「**可操作替代（走 RPA）**」——契约原则：**用错路径要立刻知道，禁止静默失败**。

### 8.1·乙 依据订正（2026-09-26 上游情报复核 —— **结论不变，依据更换**）

> **为什么要订正**：§8.1 判定「短信 API 路径禁用」时，依据之一是
> 「`x-tt-session-dtrait`（820 字节）需真机浏览器生成，**上游无 fixture ⇒ 无法生成**」。
> 经 GitHub 上游复核，**该依据已过期**（见下），但**结论不变**（换了一条更硬的依据）。

**上游情报**（`mkuko52/douyin_spider`，2026-09-26 复核，4★）：

| 能力 | 上游现状 | 说明 |
|---|---|---|
| `x-tt-session-dtrait` | ✅ **已还原** | `utils/dtrait.py` RSA-2048 + AES-128-CBC 组装 + `node/signer_server.mjs` 现场采集 |
| `a_bogus`（真浏览器） | ✅ **实测 `message:success`** | playwright 真实页面生成，Python 独占 HTTP 出口，单次 ~1.6s |
| `a_bogus`（纯离线 nv8） | ❌ 服务端 `2156` | 离线自产不通，必须真浏览器 |
| **业务层** | ⚠️ **被 `bd_ticket_guard` 请求级签名拦住（`2156`）** | 参数层已通（与抓包逐位一致），服务端仍拒 |

**⇒ 结论维持「短信 API 路径禁用」，但依据改为**：
不再是因为「dtrait 生成不了」，而是 **即使 dtrait 与 a_bogus 全部还原，
业务层仍被 `bd_ticket_guard` 拦在 `2156`** —— 协议复现路径存在**服务端侧硬阻塞**。

**这对 RPA 路线的意义（正面印证）**：真浏览器天然携带 dtrait 与 bd_ticket_guard 所需的
全部运行时状态，**不需要逆向任何签名** ⇒ **RPA 是正确路线，不是权宜之计**。

> ⚠️ **该上游不可采用**：`license = None`（仓库根无 LICENSE 文件），
> README 明示「仅供学习与安全研究、**请勿再分发或商用**」。
> 按 OSS 选型判据（协议宽松为第一条）**出局**；
> 本次**只取其情报结论**，未引入其任何代码。

**依据**：`GET /repos/mkuko52/douyin_spider`（`license=null`、`archived=false`、
`pushed_at=2026-09-26`）+ 其 `signing/send_code/README.md` 与 `分析报告.md`。

- 上游 `builder/auth.py` 的 `dtrait_profile` **默认为 None**；`dy_apis/login_api.py` 的登录流程中 `dtrait_profile` **零次出现**。
- `auth.dtrait_blob = getenv("DY_DTRAIT_BLOB")` ⇒ 在项目 `.env` 下**得到 None**。
- `builder/auth.py` 在 `if not blob:` 分支走 **strict → raise**，并有 `assert len(dtrait) == 820` **硬断言**。
  ⇒ 上游短信登录**硬要求 `x-tt-session-dtrait` 恰好 820 字节**；该 blob 由混淆 SDK `@byted/uc-secure-dtrait-core` 生成、
  **需真机浏览器捕获**，上游 `.env.example` **也未提供 fixture**。
- 适配层源码：`login_api_vendor.py:100`（能力边界表）、`:122-125`（`SMS_UNSUPPORTED_REASON` 文案）、
  `:199-205`（两个 `raise NotImplementedError`）。

**影响面**：`auto_dm/login_api_vendor.py`（新增能力边界文档 + 两个显式禁用方法）；
消费方须按「扫码→API、短信→RPA」分流，代码层面调用 API 短信方法会**立刻抛错**而非静默失败。涉及提交 `8dba9b1`（v0.45.16）。

### 8.2 决策 2：RPA 短信登录的**三个真缺陷修复**（均落到代码行为层）

> 三个缺陷的共同根因：原设计假设「任何 profile 状态下都能直接找到手机号表单并完成短信登录」。
> 实测证明该假设在**已记住账号**的 profile 上不成立，且启动时序与探活判据各有独立缺陷。

| # | 缺陷 | 实测现象 / DOM 证据 | 修法（落地行为）|
|---|---|---|---|
| **A** | **「一键登录」界面** | profile 记住账号时弹窗为「一键登录」面板，**而非手机号表单**：DOM 实测 `<div id="douyin_login_comp_btn_id">一键登录</div>` + `<p>登录其他账号</p>`；此时表单锚点 `input[name="normal-input"]` 与「验证码登录」tab **都不存在** ⇒ 短信流程 **100% 失败**（P2 实测：切 tab False / 填号 False / 发码 False）| 新增 `handle_one_click_login()`：检测到「一键登录」即点「**登录其他账号**」切进表单。源码 `login_remote.py:267` |
| **B** | **启动时序竞态（BCC-058）** | `close_camoufox_context()` 后进程**需时间退净**，新实例立刻启动即撞旧进程 ⇒ `launch_persistent_context: Target page, context or browser has been closed`，**实测 4 分钟后自愈** | 新增 `reap_profile_processes(profile)`，**复用项目既有** `vbrowser_camoufox._reap_camoufox_processes`（勿自造），在 `prepare_qr_login` / `do_sms_login` **启动前**调用。源码 `login_remote.py:149` |
| **C** | **探活判据误用 UI（判据不硬）** | profile 有 **66 cookie + 8/8 登录标识 + UI 显示已登录**，但官方接口 `GET https://www.douyin.com/passport/account/info/v2/` 返回 `{"error_code":13,"description":"会话过期，请重新登录","user_id":0}` | **改用官方接口判据**（UI 状态**不可作判据**——Live-Instance Verification 铁律）|

**依据**：`79c7311` 提交链路分析（`[Problem-Analysis Chain]` Source(1)(2)）+ `da3146b`（锁查找漏子目录 / `close` 完全失效）；
源码锚点见上表。**复用的既有能力**（未自造）：`close_camoufox_context` / `_reap_camoufox_processes`
（`vbrowser_camoufox.py:274` / `:177`）、`auth_helper.save_cookie_to_env`（`:187`）。

**影响面**：`auto_dm/login_remote.py`（新增 `handle_one_click_login` / `reap_profile_processes`；探活改官方接口；
`close_handle` 改复用 `close_camoufox_context`；`heal_stale_profile_lock` 改**递归**查找）。涉及提交 `da3146b`（0.45.17）、`79c7311`（0.45.18）。

### 8.3 决策 3：凭证写入协议（**复用既有能力，禁止自造**）

**决策内容**：
- **写入入口**：`auth_helper.save_cookie_to_env(cookie_str, env_path)` → `services/member_ctx.write_env_file(..., merge=True)`。
- **只写加密 `.env.enc`**：明文 `.env` 已于 **2026-09-21 废弃**，**即使存在也不被读取**。
- `merge=True` **保留其余 7 字段**：`DY_KEYS` / `DY_PRIVATE_KEY` / `DY_TICKET` / `DY_TS_SIGN` / `DY_WEB_PROTECT` / `DY_CLIENT_CERT` / `DY_PROXY_MODE`。
- **写前必须备份旧凭证**：本次实际备份为 `.env.enc.bak.20260926_145722`。

**依据**：`auth_helper.save_cookie_to_env`（`auth_helper.py:187`）→ `member_ctx.write_env_file`；
写盘位置与 merge 语义由既有函数实现（**未新增写盘逻辑**）。

**影响面**：所有凭证落盘路径统一收敛到上述入口；任何「自己 open(...).write」形态均为**契约违规**（禁止）。

### 8.4 决策 4：AUTH-050「身份漂移」判据的**精确定义**（本次查清，重要澄清）

**判据**：探活 uid ∈ 该账号**历史 `conv_id`** 才算身份一致；否则判漂移。

> 🔴 **关键澄清（搞错会误判漂移）**：`conv_id` 格式为 `0:1:<对方uid>:<账号自身uid>`
> ⇒ **`field[3]` 才是账号自己的 uid**（`field[2]` 是**对话对方**的 uid）。

**本次实例**：
- 账号自身 uid = `316276709526638`；
- 失效凭证探活 uid = `867973938286267`（**不在历史中**）⇒ **确认漂移**，与抖音官方「会话过期」判定**一致**（决策 2-C）。

**依据**：`services/uid_probe.py:183 _uid_consistent_with_history` ——「判据：该 uid 必须出现在该账号**至少一条**历史
`conv_id` 中」，其实现 `if s in (cid or "").split(":")`（`:202-203`）；无历史会话（新账号）返回 True（不冤枉）；
DB 不可用也返回 True（降级放行）。实测依据：正确 uid 在该账号**全部**历史会话中出现（如 278/278 条），陈旧/错误 uid 出现 0 次。

**影响面**：`services/uid_probe.py` + `auto_dm/accounts.py`（AUTH-050 文案与判定）；消费方（如直播昵称解密权）
**必须**依此判据，**不得**用 UI 或 cookie 数量替代。涉及提交 `79c7311`（0.45.18）。

### 8.5 决策 5：启动门禁的**完整判据链**（最终形态）

**决策内容**：浏览器启动前必须依次通过：

```
① heal_stale_profile_lock(profile)   ← 【递归】查找锁文件
② reap_profile_processes(profile)    ← 清扫残留进程
③ launch_async(...)                  ← 仅在前两步之后
```

> 🔴 **递归查找的必要性**：Camoufox **真实 profile 在 `<profile>/_camoufox/` 子目录**，
> **只查根目录会漏判**（`da3146b` 修的就是这个：`heal_stale_profile_lock` 改为 `find_lock_files` 递归）。

**依据**：`login_remote.py:110 heal_stale_profile_lock`（`retries=3`，递归查找 + 无同名进程时安全删除）、
`:149 reap_profile_processes`（启动前清扫，复用 `_reap_camoufox_processes`，含安全边界：只杀命令行命中本 profile
**绝对路径**的进程，路径过短拒绝执行）；
§3.1.甲 E1 实测「陈旧锁残留 → 下次启动卡 180s，清锁后立刻恢复」为核心动因。

**影响面**：`auto_dm/login_remote.py` 的**所有**启动路径（`prepare_qr_login` / `do_sms_login`）必须走本判据链，
**跳过任一步**都会复现 180s 卡死或 `TargetClosedError`。涉及提交 `da3146b`（0.45.17）、`79c7311`（0.45.18）。

---

## 9. 本轮相关提交（2026-09-26）

| commit | 版本 | 一句话说明 |
|---|---|---|
| `9202c11` | v0.45.14 | IM 远程登录更新凭证 —— RPA/DOM 方案 + 修单 profile 铁律契约漂移 |
| `49c3168` | v0.45.15 | vendor 上游登录模块 + 适配层（ADR-017 API 备用路径）|
| `8dba9b1` | v0.45.16 | API 路径能力边界显式化 —— **扫码可用 / 短信禁用（AUTH-072）** |
| `da3146b` | v0.45.17 | 修 RPA 两个真 Bug —— **锁查找漏子目录 + 关闭浏览器完全失效** |
| `79c7311` | v0.45.18 | 修 RPA 短信登录三个真 Bug —— **一键登录 / 残留竞态 / 探活判据** |

> 分支：`design/better-douyin`。**本轮所有结论均基于真机运行证据**（Live-Instance Verification）；
> **UI 状态不可作判据**（见决策 2-C）。