# 凭证 3 小时失效 —— Camoufox(Firefox) 内核 与 Chromium 档案层 不一致

- **案例编号**：CASE-2026-09-26-CRED-KERNEL-MISMATCH
- **版本**：v0.45.10（诊断时版本；修复见后续）
- **严重级别**：**fatal**（影响全项目功能可用性 —— 凭证是所有业务链路的前置条件）
- **状态**：🔬 **诊断完成，待架构决策**
- **涉及模块**：`utils/fingerprint.py`（HTTP 层档案）、`vbrowser_camoufox.py`（内核选择）、
  `daemon/browser_daemon.py`（BCC）、`utils/strdata_pure.py`（出站头构造）

---

## 一、用户报告（原始问题）

| 现象 | 用户原话 |
|---|---|
| 凭证只能撑 3 小时 | 「现在实测，凭证基本上也就只能管三个小时，连一天都撑不过去」 |
| 对照实验 | 「我本机的一个 edge 浏览器 Cookie 稳定性还是很高的，半个月都是没有问题」 |

**对照实验是本诊断的关键锚点**：同一账号、同一网络、同一机器，
**Edge 半个月稳定 ↔ Camoufox 3 小时失效** ⇒ 差异必在**环境层**，而非 cookie 本身。

---

## 二、实测证据链（全部动态取证）

### 2.1 BCC 实机审计输出（`POST /env_audit`）

```json
{"ok":true,"leaks":[
 {"code":"BCC-068","severity":"warn","detail":"navigator.platform 与档案不一致: 期望 windows / 实际 Win32"},
 {"code":"BCC-068","severity":"warn","detail":"CPU 核心数不一致: 档案=12 浏览器=8"},
 {"code":"BCC-068","severity":"warn","detail":"screen 不一致: 档案 viewport=1280x720 浏览器=2560x1440"},
 {"code":"BCC-068","severity":"warn","detail":"UA 不一致: 档案=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36…
                                                浏览器=Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:152.0) Gecko/20…"},
 {"code":"BCC-069","severity":"warn","detail":"audio 指纹两次渲染不一致（同 context 内应恒定）"},
 {"code":"BCC-069","severity":"info","detail":"navigator.userAgentData 为 null（内核未伪装 Client Hints，已知缺口）"}
],"fatal_count":0,"warn_count":5,"info_count":1}
```

### 2.2 内核选择的日志证据（`logs/browser_daemon_20260926.log`）

```
[vbrowser] 内核=Camoufox（Firefox，C++ 层指纹注入，无 JS 注入）
[camoufox] 复用固定 profile: …\profile\_camoufox
[camoufox] 环境门阀：代理 → http://127.0.0.1:10808
[bcc] 四川工伤张老师 内核=Camoufox → 跳过 JS 注入（C++ 层指纹注入，注入反而留下可检痕迹）
```

### 2.3 关键对照（表）

| 维度 | 项目档案（HTTP 层） | 浏览器真值（JS 层） | 一致性 |
|---|---|---|---|
| **浏览器品牌** | **Chrome 148** | **Firefox 152.0** | 🔴 **矛盾（致命）** |
| **UA** | `…AppleWebKit/537.36…Chrome/148.0.0.0…` | `…(rv:152.0) Gecko/20100101 Firefox/152.0` | 🔴 **矛盾** |
| **sec-ch-ua** | `"Google Chrome";v="148"` | `navigator.userAgentData = null` | 🔴 **矛盾**（Firefox 本无 CH） |
| **screen** | `1280x720`（GEO_PRESETS 随机） | `2560x1440`（真实） | 🔴 **矛盾** |
| **CPU 核数** | `12`（随机池） | `8`（真实） | 🔴 **矛盾** |
| platform | `windows` | `Win32` | 🟡 语义差异 |
| audio 指纹 | — | 两次渲染不一致 | 🟡 环境不稳定迹象 |

---

## 三、根本原因分析（Step 4）

### 3.1 直接根因：**内核（Firefox）与档案（Chrome）选择分离，无一致性约束**

```
utils/fingerprint.py::fingerprint_profile()
  └─ 硬编码 brand = "Chrome"
     ├─ ua         = "…Chrome/{v4} Safari/537.36"      ← 永远 Chrome
     ├─ sec_ch_ua  = '"Google Chrome";v="{maj}"'       ← Chromium 概念
     ├─ sec_ch_ua_platform = '"Windows"'
     └─ screen/cores = rnd.choice(预设池)              ← 与真实无关

vbrowser_camoufox.py::camoufox_enabled()
  └─ 读 DY_BROWSER_KERNEL / cfg → 本项目实际走 Camoufox(Firefox)
```

**两处各自独立**，**没有任何机制**保证"内核 == 档案品牌"。

### 3.2 传播路径

```
① Camoufox 启动（Firefox 152 内核，C++ 层指纹注入 —— 本身设计良好）
② 但出站 HTTP 头由 fingerprint.py 构造 → 声明 Chrome 148 + sec-ch-ua
③ 抖音服务端交叉校验：
     HTTP 头(Chrome) ⊗ JS/navigator(Firefox) ⊗ screen(档案720p vs 真实1440p) ⊗ cores(12 vs 8)
     ⇒ 判定为「伪装/自动化环境」
④ 处置：缩短该 session 信任期 → **频繁作废登录态**
⑤ 用户体感：「凭证只能撑 3 小时」
```

### 3.3 为什么 Edge 能活半个月（对照解释）

- Edge 是**真实 Chromium**：HTTP 头与 JS 层**天然自洽**（UA/Sec-CH-UA 由同一内核产生）
- 无 Playwright / CDP 驱动痕迹
- ⇒ 抖音视为正常用户 ⇒ **登录态按正常 TTL 维持**

**⇒ 结论：问题不在"是否被检测出自动化"，而在"项目自己的两层声明互相矛盾"**
（Camoufox 的 C++ 注入本身是好设计，反倒是**档案层**在拖后腿）。

### 3.4 次生观察（未定论，需后续验证）

| 观察 | 影响 | 待验证 |
|---|---|---|
| `audio 指纹两次渲染不一致` | 同一 context 内应恒定；不一致本身可疑 | 是否 Camoufox 已知行为 |
| `userAgentData = null` | Firefox 无 CH —— 若 HTTP 层却发 sec-ch-ua，则**头端与浏览器端直接对立** | 确认 strdata_pure 是否发出 sec-ch-ua |
| 打包日志 `ERROR: Hidden import 'env_audit' not found` | 可能是 `services.env_audit` 未进 exe（但审计能跑 ⇒ 已通过其它路径进入） | 查 PyInstaller 打包清单 |

---

## 四、修复方向（待用户架构决策）

> ⚠️ 本节属 **architectural change**（Canonical Contract Law）：档案层契约变更须走 ADR + 评审。

| 方案 | 做法 | 优点 | 风险 |
|---|---|---|---|
| **A（建议）** | **档案跟随真实内核**：`fingerprint_profile()` 感知内核（Camoufox ⇒ Firefox 档案：Gecko UA、无 sec-ch-ua、Firefox 特有字段） | 根除矛盾；发挥 Camoufox 反检测优势 | 改动面大（档案+出站头+签名层需同步） |
| B | 让 Camoufox 伪装成 Chrome | 档案不动 | Camoufox 是 Gecko 内核，**伪装 Chrome 必留痕**（业界实测：puppeteer-stealth 0 分） |
| C | 关掉 Camoufox，回原生 Chromium | 档案与内核天然一致 | 回到 Playwright/CDP 特征（`navigator.webdriver` 等） |

**推荐 A**，理由：
- Camoufox 是**专门的反检测 Firefox**，其 C++ 层注入是**当前项目最优资产**，不应放弃
- 矛盾的真正来源是**档案层**（Chrome 硬编码），修它 = 治本
- 与用户护栏一致（用户要求"防风控"，而非"降低能力"）

---

## 五、可迁移判据（写给未来的自己）

1. **"伪装"只要不自洽，就是负资产** —— CreepJS 类工具专门抓"跨信号矛盾"
   （官网原文："Detect and ignore JavaScript tampering (prototype lies)"）。
   自造的伪随机指纹（screen/cores/GPU）若与真实环境或彼此矛盾，**比不伪装更糟**。

2. **对照实验是最有力的根因工具** —— 用户"Edge 半月稳定"的对照，一举排除了
   "账号/cookie/网络"三类假设，把根因锁定在**环境层**。

3. **项目已内建检测器时，先跑它，别自造** —— `services/env_audit.py` 已对应
   rebrowser-bot-detector / CreepJS / liarjs 三套标准，且每小时自动跑；
   **先读它的输出**能省掉大量外部取证。

4. **关注"层间契约"而非单层实现** —— 本缺陷的形态是
   "HTTP 层声明 ⊗ JS 层真值"，两层各自"看起来都对"，**加起来才错**。
   审计这类缺陷要**交叉比对**，不能只看单层。

5. **内核选择与档案必须绑定** —— 若未来再支持多内核（Chromium/Camoufox），
   档案**必须**由内核推导，绝不允许两者独立配置。

---

## 六、关联

- **ADR**：待补（本案例应先出 ADR 再动手）
- **项目内建检测器**：`services/env_audit.py`（错误码 `BCC-064`/`BCC-065`/`BCC-068`/`BCC-069`）
- **开源对照**：
  - `abrahamjuliot/creepjs`（⭐2440，MIT）—— 跨信号矛盾检测
  - `rebrowser/rebrowser-bot-detector` —— CDP/webdriver 痕迹
  - `tcoyze/browser-fingerprint-evasion` —— 实测数据：puppeteer-stealth **0 分**
- **同源案例**：`2026-09-26_内容板块三tab不可用_凭证过期伪装为空数据_case_v0.45.10.md`
  （**下游表现**：凭证失效的 UI 呈现问题；本案例是**上游根因**）
