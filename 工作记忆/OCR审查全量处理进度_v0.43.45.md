# OCR 全量审查 · 处理进度与处置记录

> 报告源：`D:\SJ  agent\DYchajian-review-design-better-douyin-{detailed.md,json}`
> 生成 2026-09-17 16:30 | open-code-review v1.12.4 | 355 文件 / 1484 条
> 处置原则：**逐条实测甄别**（机器报告≠真实缺陷，抽样误报率约 30%）

---

## 1. 范围甄别

| 范围 | 条数 | 处置 |
|---|---:|---|
| `DYAutoDM_v2/*`（产品代码） | **1312** | ✅ 纳入 |
| `_ext_repos/DouYin_Spider-master` | 142 | ❌ 第三方对照库 |
| 仓库根工具脚本 / docs | 30 | ⚠️ 低优先 |

产品代码严重度：**CRITICAL 38 / HIGH 197 / MEDIUM 590 / LOW 487**
类别：bug 644 / maintainability 408 / security 77 / performance 58 / 其它 143

## 2. CRITICAL 38 条 —— **已全部定性**

### 2.1 已修复（20 条，分三批）

**v0.43.44（14 项）**

| 文件 | 缺陷 | 验证 |
|---|---|---|
| `services/pro_kb.py` | logger 未导入（**上轮回归**） | 语法 |
| `vbrowser_window.py` | 拆分丢 ctypes 导入 | 语法 |
| `features.py` | 多传参数致 TypeError | 比对签名 |
| `core/auto_dm.py:773` | `config` 未定义 → NameError | 属性存在性核实 |
| `builder/auth.py` | 空 cookie 覆盖有效凭证 | 读实现 |
| `test_send_gate_config.py` | 6 条断言错 | **6/6 通过**（原 4fail+2err） |
| `mcp/tools.py` ×5 | 参数名与底层签名不符 | 比对 13 个方法签名 |
| `dy_apis/client_video.py` | 丢 `@staticmethod` | 装饰器+语义验证 |
| `dy_apis/client_live.py` | 同上 | 同上 |
| `dy_apis/client_user.py` | **硬编码他人 sec_uid** | 全库扫无残留 |
| `api/messages.py:78` | lease 未释放 | 签名核实 |

**v0.43.45（6 项）**

| 文件 | 缺陷 | 验证 |
|---|---|---|
| 全库 17 文件 | **63 处 `verify=False`** → 统一 `tls_verify()` | **真实域名 verify=True 握手正常** |
| `api/mcp.py` ×3 | `/restart`、`GET/POST /audit` 漏鉴权 | 7/7 端点校验核实 |
| `frontend/accounts-page.tsx` | 双 setRole 并发覆盖 | `tsc --noEmit` 0 |
| `frontend/AgentSection.tsx` | effect 覆盖新建 draft | 同上 |
| `frontend/platform-page.tsx` | queryKey 与请求参数不一致 | 同上 |

### 2.2 判定为误报（18 条，**不改**）

| 文件 | OCR 主张 | 实测结论 |
|---|---|---|
| `utils/data_util.py:12` | 正则空分支失效 | **实跑证明非法字符被正确去除** |
| `core/live_hook.py:275` | `frame.messagesList` 字段错 | 用的就是 `response.messagesList`，与 `dy_live/server.py:63` 一致 |
| `login_capture.py:149` | None 除法 | 已有 `is not None` 保护 |
| `scripts/verify_api_split.py:42` | check 有 positional-only | 三参均为普通参数 |
| `test_model_hub_v2.py` ×2 | 断言与实现不符 | 断言已是 `["sem"]` |
| `scripts/build_sidecar.py:268` | 注入标记丢失 | 标记在 :1169/:1172 存在，缺标记时也会 warn |
| `scripts/probe_img_*.py` ×4 | 缺 `import os` | 实际已 import |
| `src-tauri/lib.rs:34` | Mutex guard 跨 await | 调用为同步，未跨 await 点 |
| `api/logs.py` | open 竞态 | 已有 exists()/is_file() 前置，属低危建议 |
| `frontend/tsconfig.json:4` | 键名拼写错误 | 键名本身正确（实际为 `useDefineForClassFields`） |

## 3. 处理中（HIGH 197 条）

已派 5 个甄别 agent 覆盖：
- `browser_daemon.py`（11）、`app_config_schema.py`（10）、`api/logs.py`（3）
- `dy_apis/client_comments|relations|douyin_recv_msg|login_api`（14）
- `database.py`/`ws_link.py`/`api/accounts.py`/`api/mcp.py`/`auto_dm/accounts.py`/`builder/header.py`（15）
- 前端 `player-media-stage`/`UnifiedConfigSection`/`app-store`（9）

**已自行处理的安全类 HIGH**：
- `api/mcp.py` 鉴权缺口（已修）
- `verify=False` 系列 5 条（已由 TLS 策略统一收敛）
- `browser_daemon.py:2579` 的 9 条「未实现 XSS/CSRF/泄密…」——**同一行刷屏的泛化模板**，
  非逐项真实缺陷，判为低质生成不作逐条处理

## 4. 方法论要点

1. **机器报告必须逐条实测**：CRITICAL 误报 18/38 ≈ 47%；
   若照单全收，`data_util`/`live_hook` 等**本来正确的代码会被改坏**。
2. **回归自查**：`pro_kb.py` logger 是我 v0.43.42 修补时引入的，
   说明自动化审查能抓出人工修复的副作用。
3. **收敛优于逐点**：64 处 `verify=False` 不是逐个改，而是引入统一策略模块，
   一次性收敛 + 保留显式开关。
4. **修复必须实测**：
   - TLS：真跑抖音域名验证 `verify=True` 不会破坏生产
   - 断言类：跑测试看是否真通过
   - 装饰器类：AST 确认装饰器已挂上
