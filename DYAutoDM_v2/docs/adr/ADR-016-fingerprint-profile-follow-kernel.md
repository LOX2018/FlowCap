# ADR-016：指纹档案层跟随真实浏览器内核（Camoufox/Firefox）

- **状态**：已采纳（Accepted）
- **日期**：2026-09-26
- **版本**：v0.45.11
- **决策者**：LOX（架构决策）· 执行：Hermes Agent
- **关联案例**：`工作记忆/cases/2026-09-26_凭证3小时失效_Camoufox内核与Chrome档案层矛盾_case_v0.45.10.md`
- **前置 ADR**：ADR-015（本人端点登录态失效语义）

---

## 背景（Context）

### 触发事实

用户实测：**项目凭证仅能维持约 3 小时**，而**同一账号在同一台机器上的 Edge 浏览器
Cookie 可稳定半个月**。

该**对照实验**排除了「账号 / cookie / 网络」三类假设，将根因锁定在**环境层**。

### 实测证据（动态取证）

**BCC `/env_audit` 输出**（6 处不一致）：

| 维度 | 项目档案（HTTP 层声明） | 浏览器真值（JS 层） |
|---|---|---|
| **浏览器品牌** | **Chrome 148** | **Firefox 152.0** |
| UA | `…AppleWebKit/537.36…Chrome/148.0.0.0…` | `…(rv:152.0) Gecko/20100101 Firefox/152.0` |
| sec-ch-ua | `"Google Chrome";v="148"` | `navigator.userAgentData = null` |
| screen | `1280x720` | `2560x1440` |
| CPU 核数 | `12` | `8` |
| platform | `windows` | `Win32` |

**出站头实测**（项目真实请求链路抓包）：

```
user-agent:  …Chrome/148.0.0.0 Safari/537.36
sec-ch-ua:   "Not;A=Brand";v="8", "Chromium";v="148", "Google Chrome";v="148"
```

**内核日志**（`logs/browser_daemon_20260926.log`）：

```
[vbrowser] 内核=Camoufox（Firefox，C++ 层指纹注入，无 JS 注入）
[bcc] 内核=Camoufox → 跳过 JS 注入（C++ 层指纹注入，注入反而留下可检痕迹）
```

### 矛盾的形状

```
HTTP 请求（Python requests 构造）   →  声明 Chrome 148
浏览器请求（Camoufox 内核发出）     →  真实 Firefox 152
                                        ↑ 同一账号、同一环境、两套身份
```

抖音服务端**交叉校验**两类请求 → 判定为伪装/工具环境 → **缩短会话信任期**。

---

## 决策（Decision）

### D1. 档案的**浏览器身份字段**必须由内核推导，禁止硬编码

**规则**：`utils/fingerprint.py` 的
`brand` / `ua` / `sec_ch_ua` / `sec_ch_ua_platform` / `sec_ch_ua_mobile`
**不得再硬编码 Chrome**；必须依据**当前生效内核**（`camoufox_enabled()`）分支产出：

| 内核 | brand | UA 形态 | Client Hints |
|---|---|---|---|
| **camoufox** | `Firefox` | `…(rv:NNN.0) Gecko/20100101 Firefox/NNN.0` | **不发 sec-ch-ua 系列**（Gecko 无 CH） |
| chromium | `Chrome` | `…Chrome/NNN.0.0.0 Safari/537.36` | 发 sec-ch-ua 系列 |

**理由**：HTTP 层与 JS 层的浏览器身份**必须同源**。Camoufox 的 C++ 注入本身是
**优质资产**（实测 `webdriver=false` / `tamperedCount=0` / `automationKeys=[]`），
**真正拖后腿的是档案层**。

#### D1·甲：内核定位必须**不依赖 `camoufox` Python 包**（实测修正）

**实测事实（2026-09-26）**：

```
camoufox_enabled(项目配置)      = True          ← 配置要求用 Camoufox
import camoufox.pkgman         → ModuleNotFoundError   ← backend 的 Python 环境无该包
  ⇒ _chrome_exe_path() 返回 ""
  ⇒ kernel_version() 退回兜底常量 "148.0.0.0"（Chrome 版本）
  ⇒ 档案声称 Chrome 148，浏览器实际 Firefox 152      ← 矛盾诞生机制
```

**根因**：`_chrome_exe_path()` 依赖 `camoufox.pkgman.launch_path()`。
而该项目**存在两套 Python 环境**：
- **打包 exe**（`_internal/camoufox/` 已含包）—— BCC 实际运行处
- **开发/源码环境**（backend 的 Python）—— **无 camoufox 包**

**决策**：内核定位改为**文件系统扫描优先**（不依赖任何 Python 包）：

```
内核真实位置（实测）：
  %LOCALAPPDATA%\camoufox\camoufox\Cache\browsers\official\<ver>-<hash>\camoufox.exe
  实测版本目录：152.0.4-beta.30-ea52a02f
  ⇒ 与 JS 层 navigator.userAgent 的 rv:152.0 一致 ✅
```

**规则**：`_chrome_exe_path()` 的解析顺序改为
1. **扫描 Camoufox INSTALL_DIR**（`user_cache_dir("camoufox")/camoufox/Cache/browsers/official/*/camoufox.exe`）
   —— **不 import camoufox**
2. 若 `camoufox` 包**恰好可用**，用 `pkgman.launch_path()` 交叉验证
3. 再走既有 Chromium 回退路径
4. 全部失败 → 兜底常量（但须**记录告警**，不静默）

**理由**：内核定位是**环境事实**，不应因为"某个 Python 解释器没装包"而
静默退化成**错误品牌的版本号** —— 这正是本次缺陷的机制。

### D2. 不发 `sec-ch-ua` 系列（Firefox 内核下）

**规则**：Camoufox 内核下，**移除** `sec-ch-ua` / `sec-ch-ua-mobile` / `sec-ch-ua-platform`
三个头。

**理由**：`navigator.userAgentData === null` 是 Firefox 的**事实**。若 HTTP 层仍发
`sec-ch-ua`，等于**头端与浏览器端直接对立** —— 这是比"完全伪装"更容易被识别的破绽。

### D3. 屏幕/核数等硬件字段**跟随真实内核**，不再随机

**规则**：`screen_width/height`、`cpu_core_num` 等改为**读取内核真值**
（Camoufox 自报 = 真实硬件），而非 `rnd.choice(预设池)`。

**理由**（官方警告原文）：

> Do NOT randomly assign values to these properties. WAFs hash your WebGL fingerprint
> and compare it against a dataset. **Randomly assigning values will lead to detection
> as an unknown device.**

当前 `GEO_PRESETS` × `GPU_PRESETS` × `_CORES_POOL` **三者独立随机** → 必然产出
物理上不合理的组合（如 4070 显卡配 720p 屏幕）。

### D4. 消除所有 Chrome 硬编码残留

**规则**：以下四处必须统一走 D1 的内核感知入口：

| 文件 | 现状 |
|---|---|
| `utils/fingerprint.py` | `brand="Chrome"` + Chrome UA/CH |
| `dy_apis/client_live.py:377` | 硬编码 `Chrome/146.0.0.0` |
| `utils/ab_pure.py:58` | 硬编码 `Chrome/150.0.0.0` |
| `utils/strdata_pure.py:10` | 模板 `vendor:"Google Inc."` 与 `product:"Gecko"` **自相矛盾** |

**理由**：Canonical Contract Law —— 同一语义（浏览器身份）出现多个来源，
必然漂移（当前已漂移出 146/148/150 三个版本号）。

### D5. 保留 Chromium 回退路径

**规则**：`camoufox_enabled()==False` 时，仍产出 Chrome 档案（原行为）。

**理由**：不改动「显式配置优先」原则（用户明确要求环境由配置显式选择）；
且 Chromium 回退是既有能力，不应破坏。

---

## 后果（Consequences）

### 正面

- **根除层间矛盾**：HTTP 层与 JS 层浏览器身份一致 → 消除最致命的自动化特征
- **发挥 Camoufox 优势**：其 C++ 注入（`webdriver=false` 等）才真正生效
- **消除硬件不合理组合**：屏幕/核数跟随真实值
- 统一浏览器身份来源 → 消除 146/148/150 三版本漂移

### 负面 / 代价

- **改动面较大**：涉及 `fingerprint.py`（核心）、`header.py`、`params.py`、`proto.py`、
  `client_live.py`、`ab_pure.py`、`strdata_pure.py`
- **Camoufox 版本号获取**：需从内核取真实 Firefox 版本（实测 `rv:152.0`），
  取不到时须**显式失败**而非猜
- **`strdata_pure` 模板**：Gecko 与 Chromium 的 navigator 字段差异较大
  （如 `vendor` Chrome 为 `"Google Inc."`、Firefox 为 `""`），模板需按内核分支

### 未解决（边界）

- **`userAgentData = null`**：Firefox 无 Client Hints —— 本 ADR 选择"不发"，属**正确姿态**；
  但抖音是否**期望** CH 存在（对 Chrome 用户）属平台侧行为，需**上线观察**
- **WebGL renderer `"or similar"` 后缀**：经上游文档核实，**这是 Camoufox 官方标准格式**
  （官方示例即 `"NVIDIA GeForce GTX 980, or similar"`），**非破绽**，本 ADR 不动

---

## 备选方案（Alternatives Considered）

| 方案 | 否决理由 |
|---|---|
| A. 保持 Chrome 档案，让 Camoufox 伪装成 Chrome | **Gecko 伪 Blink 必留痕**；业界实测 `puppeteer-stealth` 得 **0 分**（`tcoyze/browser-fingerprint-evasion`）|
| B. 关闭 Camoufox，回原生 Chromium | 回到 Playwright/CDP 特征（`navigator.webdriver` 等）；放弃现有优质资产 |
| C. 只改 UA，不动 CH/screen/cores | 治标；剩余 5 处矛盾仍会被交叉校验捕获 |
| **D. 档案跟随真实内核（采纳）** | —— |

---

## 验证要求（Definition of Done）

1. **`/env_audit` 复测**：`BCC-068`（档案 vs 真值不一致）条目 **降为 0**
2. **出站头实测**：Camoufox 内核下 **不含 `sec-ch-ua`**，UA 为 Gecko 形态
3. **JS 层一致性**：`navigator.userAgent` 与出站 `user-agent` **同品牌同版本**
4. **凭证 TTL 观察**：修复后**观察期内**（建议 ≥ 24h）凭证不再 3 小时失效
   —— ⚠️ 此项**需真实时间**，不得以"静态检查通过"替代
5. **回退验证**：`DY_BROWSER_KERNEL=chromium` 时仍产出 Chrome 档案（原行为不破坏）

---

## 参考资料

- 案例档案：见文首「关联案例」
- Camoufox 官方：
  - `webgl_config` 用法与 **"不要随机赋值"** 警告（camoufox.com/python/usage.md）
  - WebGL 官方示例含 `"or similar"` 后缀（camoufox.com/webgl-research）
- 开源对照：
  - `abrahamjuliot/creepjs`（⭐2440，MIT）—— 跨信号矛盾检测
  - `tcoyze/browser-fingerprint-evasion` —— puppeteer-stealth 实测 0 分
  - `rebrowser/rebrowser-bot-detector` —— CDP/webdriver 痕迹
- 项目内建检测器：`services/env_audit.py`（`BCC-064`~`BCC-069`）
