# WP 私信通道「静默无效」根因确证 —— Camoufox 后端禁 JS 注入

- **案例编号**：AI-061 / H-24
- **日期**：2026-09-25
- **触发**：用户提问「AI 回复读取的聊天记录是本地更新会话拉到的库，还是 WS+WP 读到的」
- **结论等级**：**根因已确证（代码注释自陈 + 日志零反例 + 端到端不可能性证明）**
- **修复状态**：**未修复**（方案已出，待用户拍板；见 §6）

---

## 1. 设计意图（Design Intent）

| 项 | 内容 |
|---|---|
| 模块 | `daemon/wp_recv.py` + `daemon/browser_daemon_js.py:CAP_WP_MESSAGE_HOOK_JS` + `daemon/browser_daemon.py:_launch` |
| 设计契约 | WP 通道 = **与 WS 并存的第二条私信接收通道**；页面级被动 hook 截获 chat 页的 im 相关 HTTP 响应与 WS 帧 → 推入 `window.__CAP_WP_MESSAGE__.events` → 后端每 3s 轮询 BCC `/wp_messages` 取回（读后清空）→ 解析 → 落库 `dm_messages`（`extra.source='wp'`）；与 WS 走 `(account,conv_id,ts±2s,text)` **应用层去重**（`main.py:519` 注释自述，`wp_recv.py:12` 自述） |
| 预期行为 | 客户新私信应至少被 WS/WP 之一落库；两条通道互为冗余，互为补缺 |
| 设计假设 | ① Camoufox 模式下 `add_init_script` 会被执行；② 页面 hook 能在 chat 页改写到 `window.fetch/XMLHttpRequest/WebSocket`；③ 两个 hook 脚本（昵称 `CAP_USERINFO_HOOK_JS` + WP `CAP_WP_MESSAGE_HOOK_JS`）走同一注入循环，可一并生效 |

---

## 2. 观测到的偏差（Observed Deviation）

| 项 | 内容 |
|---|---|
| deviation_type | **data**（数据通路整体缺失，非时序/资源） |
| deviation_point | **`browser_daemon.py:_launch` 的 init script 注入分支** —— 页面 hook 从未注入到 DOM |
| deviation_from_expectation | 期望：WP 通道持续落库 `source='wp'` 行。实际：**生产库 0 行、历史全期 0 行**；`[wp_recv]` 日志仅有「启动轮询」、**零条**「取回 WP 私信事件」 |
| error_code | **WP-061**（本案例新分配；现有 `RECV-024/025` 只覆盖 DB/循环异常，未覆盖「通道静默无产出」） |
| severity | **error**（功能缺失但无数据损坏；WS 通道兜住了实时接收，故未上升为 fatal） |

---

## 3. 执行链追踪（Trace the Execution Chain）

从「库中零 `source='wp'` 行」逆推，逐节点验证：

```
① poll_once() 每 3s POST http://127.0.0.1:{port}/wp_messages
      ↓  ✅ 实证在跑（[wp_recv] 启动行 ×6，两个账号各 3 次）
② bcc_routes.py:517 /wp_messages → container.capture_wp_messages()
      ↓  ⚠️ 实证「有时失败」：BCC-012 ×1462（Target page...closed）、BCC-033 ×2326
      ↓  ⚠️ 但 09-25 12:08 起 BCC 容器成功启动（内核=Camoufox），失败为切换窗口期
③ browser_daemon.py:1260 capture_wp_messages() 读页面数组
      page.evaluate("() => window.__CAP_WP_MESSAGE__ ? window.__CAP_WP_MESSAGE__.events : []")
      ↓  🔴 恒返回 [] —— 数组存在但**永远空**
④ 日志判别点：if evs: logger.info("取回 WP 私信事件 N 条")
      ↓  🔴 全历史 **0 次** ⇒ 恒空，从未取回过非空事件
⑤ parse_ws_frame() / parse_http_init()
      ↓  ⛔ **逻辑上不可能到达**（④ 恒空 ⇒ 无事件可解析）⇒ 排除「解析失败」
⑥ _already_exists() 去重
      ↓  ⛔ **逻辑上不可能到达**（同上）⇒ 排除「被 WS 去重拦截」
⑦ _launch 注入分支 (browser_daemon.py:522)
      if self._backend == "camoufox":  → 跳过全部 init script
      ↓  🔴 **断点在此**：Camoufox 模式下 hook 从未注入
```

**断点定位理由（关键）**：④ 的日志点在**解析之前、读回之后**。它恒不打印 ⇒ 事件数组从源头就是空的 ⇒ ⑤⑥ 两步根本没有输入。这把原登记的三候选**逻辑排除两个**，只剩「hook 未截获」。

---

## 4. 根因分析（RCA）

### 4.1 直接根因（代码自陈，非推断）

`daemon/browser_daemon.py:515-524`：

```python
# 2026-09-20 v0.44.0【Camoufox 模式禁止 JS 注入】
#
# 这两个 hook 通过 add_init_script 改写 window.fetch / XMLHttpRequest /
# WebSocket，属「可被 JS 检查发现」的痕迹 —— 正是抖音在**交互时刻**
# 弹「安全风险…已阻止此次访问」的嫌疑成因（实测：带注入时点「验证码
# 登录」即弹窗）。
#
# Camoufox 的指纹注入在 **C++ 实现层**，本就无需 JS 注入，且**注入会
# 抵消它的反检测优势**。故该模式下跳过全部 init script。
#
# 影响（如实记录）：Camoufox 模式下 WP 私信通道的 JS 劫持不可用，
# 需改用 Playwright 原生 WebSocket 事件（非页面注入，无痕迹）。
# ════════════════════════════════════════════════════════════════════
if self._backend == "camoufox":
    logger.info(f"[bcc] {self.account} 内核=Camoufox → 跳过 JS 注入")
else:
    ...  # add_init_script(CAP_USERINFO_HOOK_JS, CAP_WP_MESSAGE_HOOK_JS)
```

> **代码注释自己写明了这个后果**：「Camoufox 模式下 WP 私信通道的 JS 劫持不可用」。
> 所以本案例不是「未知缺陷」，而是**一个已知副作用未被登记、也未被补偿** ——
> 属**知识库缺口 + 计划未闭环**，不是代码逻辑错误。

### 4.2 因果链（完整）

```
2026-09-20 v0.44.0 接入 Camoufox（提交 6c9b09e），为规避抖音风控弹窗
  「安全风险…已阻止此次访问」，Camoufox 模式跳过全部 init script
        ↓
2026-09-25 12:08:33 首次实机观测到：内核=Camoufox → 跳过 JS 注入
        ↓
CAP_WP_MESSAGE_HOOK_JS 从未注入 → window.__CAP_WP_MESSAGE__ 数组恒空
        ↓
capture_wp_messages() 恒返回 [] → 日志「取回 WP 私信事件」0 次
        ↓
wp_recv.process_events() 恒 return 0 → 生产库 source='wp' 恒 0 行
```

### 4.3 为什么「昵称仍可用」（对照，证明是定向失效而非普遍故障）

| hook | 注入方式 | Camoufox 下 | 结果 |
|---|---|---|---|
| 昵称（`CAP_USERINFO_HOOK_JS`） | `add_init_script` | ❌ 被跳过 | 但昵称改用 **`CAP_IDB_USERINFO_JS` + `exec_js`**（IndexedDB 读取，`bcc_routes.py:379`）⇒ 实测 92 个/78.4s **正常** |
| WP（`CAP_WP_MESSAGE_HOOK_JS`） | `add_init_script` | ❌ 被跳过 | **无替代路径** ⇒ **静默失效** |

⇒ 昵称有 B 计划、WP **没有**。这正是「同一注入循环被禁用，一个功能活着、一个死掉」的原因。

### 4.4 停用时长

- Camoufox 接入提交：`6c9b09e`（v0.44.0，2026-09-20）
- 日志首次出现「跳过 JS 注入」：**2026-09-20 12:41:25**
- 至 2026-09-25 13:58（取证时刻）：**约 5 天**；全期该行 **113 次**（日志按日：20 日 22 / 21 日 36 / 22 日 19 / 23 日 5 / 24 日 18 / 25 日 8）

---

## 5. 实测证据（逐条可复跑）

### 5.1 生产库（权威判据）

```
库：C:\temp\dyautodm_design\members\m17db0f8209156f26\data\dyautodm.db
    （= 沙箱快照 snap_123843/dyautodm.db；1,544,192 B）
```

| 查询 | 结果 |
|---|---|
| `json_extract(extra,'$.source')='wp'` | **0 行** |
| `extra LIKE '%client_msg_id%'`（WP 独有指纹） | **0 行** |
| `msg_type='text'`（首包/更新会话） | **754 行 / 84.0%** |
| `msg_type='7'`（WS 长连接） | **110 行 / 12.2%** |
| 其他（delivery_marker 29 / '1' 3 / '50010' 1 / '27' 1） | 34 行 / 3.8% |

### 5.2 日志（生产根 `C:\temp\dyautodm_design\logs\`，09-14~09-25 全期）

| 检索 | 结果 |
|---|---|
| 「取回 WP 私信事件」 | **0 次**（有事件才打印）⇒ 恒空 |
| 「WP 通道新增 N 条」 | **0 次**（有落库才打印） |
| `[wp_recv]` 日志行 | **仅 6 条**，全部是「启动 WP 通道轮询」 |
| 「内核=Camoufox → 跳过 JS 注入」 | **113 次**（首 09-20 12:41:25，末 09-25 12:20:04） |
| `BCC-012` 读 wp message 失败 | **1462 次**（09-20: 49 / 09-21: 18 / **09-25: 1395**，全部 `Target page...has been closed`，集中在 context 切换窗口） |
| `BCC-033` `/wp_messages` 失败 | **2326 次**（含 `ContainerBusy: 切换可见性中`、`get_cookies`、`BCC-058`） |

### 5.3 反面验证（排除「解析失败」与「去重拦截」）

```
$ grep -rh "取回 WP 私信事件" <生产根>/logs/*.log | wc -l
0
```

该日志点位于 `capture_wp_messages()` 内部、**在 `parse_ws_frame` / `_already_exists` 之前**。
它恒不打印 ⇒ 进入解析阶段的事件数恒为 0 ⇒ 后两步**不可能**是该缺陷成因。
（另注：进程内 `_state["container"]` 与 `wp_recv` 为同一进程，**无跨进程 loguru 缓冲**问题，排除「日志吞掉」。）

### 5.4 活体验证（BCC 可启动，增强诊断强度）

09-25 12:08:33 实机日志：

```
[vbrowser] 内核=Camoufox（Firefox，C++ 层指纹注入，无 JS 注入）
[bcc] 尚进工伤小助理 内核=Camoufox → 跳过 JS 注入（C++ 层指纹注入，注入反而留下可检痕迹）
```

同刻 **BCC 容器工作正常**（昵称捕获 92 个 / 78.4s 成功）。⇒ 失败**只落在依赖 JS 注入的 WP 通道**，
不是 BCC 整体不可用 —— 这是**定向失效**的硬证据。

---

## 6. 处置方案（三条路，待拍板）

| 方案 | 做法 | 优点 | 代价 / 风险 |
|---|---|---|---|
| **A. 显式下线** | 删除 `run_wp_recv_loop` 拉起（`main.py:537-551`）+ 订正 `main.py:519` 与 `wp_recv.py:12` 注释，改为「WS 单通道」 | 最诚实、零风险、消除「以为有双保险」的错觉 | 失去第二条接收通道（当前实际贡献已是 0，故**功能上零损失**） |
| **B. 切换内核为 exe** | 让 `_backend != camoufox`，恢复 JS 注入 | 一行配置开关；WP 链路代码原样可用 | 重新暴露抖音风控弹窗风险 —— **而禁用注入正是为规避它**（2026-09-20 的实测动机）⇒ 属**倒退**，不推荐 |
| **C. 恢复能力（推荐）** | 按**代码注释自己指出的方向**实现：改用 patchright **协议层**监听（`context.on("response")` 截 `imapi.douyin.com` HTTP；`context.on("websocket")` + `frame_received` 收 WS 帧），**零 JS 注入、零页面痕迹** | 既保住 Camoufox 反检测优势，又恢复第二条通道；**技术上已验证可行**（patchright 源码含 `_network.py` / `_browser_context.py` WebSocket 支持） | 中等工作量（新监听模块 + 与现有 `extra.source='wp'` 语义对齐 + 回归） |

**可行性预验证（已做）**：`patchright` / `playwright` 均可导入；`BrowserContext.on` 存在；
其源码模块含 WebSocket 事件支持（`_network.py`、`_browser_context.py`、`_generated.py`）。
⇒ 方案 C 有技术基础，但**尚未实现**（代码库中搜 `page.on("response")` / `frameworkreceived` 均无命中 —— 属**新建**）。

**诚实标注**：本案例**未实现任何修复**，仅完成根因确证与方案论证。选择权在用户。

---

## 7. 副产品：顺带确证的架构事实（回答用户原始提问）

**AI 上下文的数据来源**（`services/ai_reply.py`）：

- **读取侧**：纯本地 SQLite 查询，**零网络**。
  `_build_history()`（`:1593`）`SELECT ... FROM dm_messages WHERE ... AND msg_type IN ('text','7','27')`；
  `_tick()`（`:1264`）本地水位轮询（`SELECT COALESCE(MAX(id),0) FROM dm_messages`，水位存 KV）。
- **写入侧**：三条通道写同一张表 —— 首包 `cmd2043/cmd301`（84.0%）、WS（12.2%）、**WP（0%）**。
- **结论**：WS / WP **都是写入方，不是读取方**；AI 读的是「本地库」，而库里 84% 由「更新会话」首包填充。
  详见 `DYAutoDM_v2/knowledge/cases/ai-context-injection-and-concurrency-audit.md` §3.7。

**视觉模型**：确在链路上（`_describe_image`），门控严格 —— 仅当
`text.startswith("[图片]") or msg_type=='27'`；未配置/失败则固定兜底话术，绝不瞎猜。

---

## 8. 知识库缺口（须一并修补）

| 缺口 | 说明 |
|---|---|
| `工作记忆/05e_前端UI与WP通道.md` | §24 记载了 WP 通道接入与两个 hook bug 的修复，**但未记载 v0.44.0 起 Camoufox 禁注入导致 WP 通道整体失效**；该分册当前会让读者以为 WP 通道可用 ⇒ **doc-rot** |
| `工作记忆/05a_私信捕获与协议台账.md` | 应登记「WP 通道当前状态 = 因内核选择而停用」的契约事实 |
| `main.py:519` / `wp_recv.py:12` 注释 | 声称「与 WS 通道并存、应用层去重」——**名不符实**，须订正 |

---

## 9. 复跑方法（可复现）

```bash
# ① 生产库：WP 通道贡献（应为 0）
python -c "import sqlite3;c=sqlite3.connect('file:C:/temp/dyautodm_design/members/m17db0f8209156f26/data/dyautodm.db?mode=ro',uri=True);print(list(c.execute(\"SELECT COUNT(*) FROM dm_messages WHERE json_extract(extra,'\$.source')='wp'\")))"

# ② 日志：恒空判据（应为 0）
grep -rc "取回 WP 私信事件" "C:/temp/dyautodm_design/logs/"*.log | grep -v ":0$" || echo "0 次 —— 恒空"

# ③ 根因判据（应有命中）
grep -rh "跳过 JS 注入" "C:/temp/dyautodm_design/logs/"*.log | tail -3

# ④ 代码断点
grep -n -A3 'if self._backend == "camoufox"' DYAutoDM_v2/backend/daemon/browser_daemon.py
```

---

## 10. 版本与归档

- **本次未改产品代码** ⇒ **不触发版本递增**（`+0.01` 仅适用「debug-test fix」；本案例为**诊断归档 + 台账登记**）
- **归档**：本文件
- **台账**：`工作记忆/00_交接卡待办台账.md` **H-24** 已更新为「根因确证 + 方案待拍板」
- **关联案例**：`DYAutoDM_v2/knowledge/cases/ai-context-injection-and-concurrency-audit.md` §3.7
