# P3-5 · 大组件拆分计划（`browser_daemon.py` 3,757 行）

> 状态：**Planned（待排期，未实施）**　日期：2026-09-22
> 依据：《架构审计报告》第 5 项；`flowcap-dev-guards` §〇·子（禁止新增第 N+1 决策点、先删旧路径）

## 1. 为什么这是「高风险重构」而不是「整理代码」

`BrowserContainer` 是**单例物理资源**（唯一 Playwright context / 唯一 profile 锁）。
拆分若不慎引入**两个所有权视图**，会直接破坏本项目最贵的约束：
「单 profile 铁律 + 租约单一事实源」。历史上「调度器沦为吉祥物」正是
**所有权被多处试探**造成的（三处各自看端口/alive/_lock）。

⇒ 拆分**必须**保持：**一个进程、一个容器实例、一套租约**。只拆**职责边界**，不拆**所有权**。

## 2. 现状结构（实测，3,757 行）

| 职责块 | 行区间（约） | 正交性 |
|---|---|---|
| 租约体系（`_lease_*`） | 190–335 | ✅ 纯逻辑，无浏览器依赖，**可最先抽出** |
| 应用/配置（`_app_version`/`_cred_refresh_mode`） | 403–455 | ✅ 独立 |
| `BrowserContainer` 主体（**3,300+ 行**） | 455–2955 | ❌ 需按下面 §3 再分 |
| FastAPI 路由 + Pydantic 模型 | 2957–3757 | ✅ 已基本独立（但混在同一文件） |

`BrowserContainer` 内已可辨 **8 个正交职责**（按方法簇）：

| # | 职责 | 代表方法 | 行量估算 |
|---|---|---|---|
| 1 | 生命周期/健康 | `start` `_launch` `_ensure_alive` | ~250 |
| 2 | 窗口可见性 | `set_visible` `_wait_window_visible` `_window_really_visible` | ~200 |
| 3 | 执行调度 | `submit` `_exec` | ~110 |
| 4 | 页面/JS | `goto` `evaluate` `exec_js` `_ensure_nav_tab` | ~200 |
| 5 | 私信发送 | `wp_send_text` `capture_wp_messages` | ~180 |
| 6 | **昵称捕获** | `capture_userinfo_map` | ~280 |
| 7 | 登录/凭证 | `scan_login` `refresh_cookie_to_env` `_read_page_sign` `run_keepalive` | ~800 |
| 8 | 环境审计 | `env_audit_snapshot` | ~25 |

## 3. 拆分方案（渐进式，每步可独立验证）

**原则：先抽「零浏览器依赖」的，再抽「有依赖但边界清晰」的，最后才动核心。**

### Step 1（低风险）：抽租约体系 → `daemon/bcc_lease.py`
- 内容：`_lease_reset/_lease_current/_lease_status/_lease_acquire/_lease_renew/_lease_release/_lease_owned_by`（~145 行）
- 为什么安全：纯数据结构 + 内存状态，**不碰浏览器**；`_exec` 只调其公开函数。
- 判据：`scripts/diag/verify_s2_lease.py`（现有 33 项）全绿。
- 附带收益：租约成为**可被其他模块 import 的显式契约**，不再藏在 3,700 行文件深处。

### Step 2（低风险）：抽 FastAPI 路由 → `daemon/bcc_routes.py`
- 内容：`@app.post(...)` 全部端点 + Pydantic 模型（~800 行）
- 依赖注入：路由只调 `_state["container"]` 的公开方法，**不读容器私有属性**（先审计）。
- 判据：`/openapi.json` 端点数不变；实机各端点 200/预期码。

### Step 3（中风险）：抽登录/凭证 → `daemon/bcc_login.py`（mixin）
- 用 **mixin** 而非独立类：`class BrowserContainer(BccLoginMixin, BccCaptureMixin, ...)`，
  保持**单实例单所有权**（这是本项目强约束——不能拆成多个对象各自持 page）。
- 判据：`scan_login` / `refresh_cookie_to_env` 实机跑通（凭证回写 + uid 门禁）。

### Step 4（中风险）：抽昵称捕获 → `daemon/bcc_capture.py`（mixin）
- 内容：`capture_userinfo_map` + `CAP_IDB_USERINFO_JS` 调用面
- ⚠️ 风险：该块正被**探针证据链**依赖，改前后必须跑
  `h2_verify_probe_fix.py` + 一次真·refresh，确认日志字段与关联率不变。

### Step 5（低风险）：环境审计 + 窗口可见性 → 各自 mixin

## 4. 明确不做（避免过度拆分）

- ❌ **不拆进程**：多进程会让「谁持有 profile 锁」重新成为问题（违反单例铁律）。
- ❌ **不拆成多个 `BrowserContainer` 实例**：租约必须单一事实源。
- ❌ **不为拆而拆**：`_launch` / `_ensure_alive` 是**原子语义**（启动与重建不可分），
  强行拆开会让「重建期间状态」有多个定义。

## 5. 验收判据（每步通用）

```bash
# 1. 行数下降但总行数不涨（禁止「旁加一层」）
wc -l backend/daemon/browser_daemon.py
# 2. 单实例判据未破
grep -c "class BrowserContainer" backend/daemon/browser_daemon.py   # 期望 1
# 3. 租约单例自检
py314 scripts/diag/verify_s2_lease.py
# 4. 实机：起应用 → BCC 起来 → 昵称捕获 healthy（h2_verify_probe_fix.py）
# 5. 单测全绿
py314 -m unittest discover -s backend -p "test_*.py"
```

## 6. 为什么现在不实施

1. **需实机验证每一步**（BCC 起不来 = 全系统不可用），属「有用户在旁 + 可回滚」场景。
2. 本轮已完成更紧急的：探针假失效修复 + 重打包部署 + 实机闭环。
3. 拆分本身**不产生业务价值**，只在**下一次需要改这块代码时**才回本 ——
   按「迭代止损律」，应在**有具体改动需求**时顺带拆，而不是为拆而拆。

> **建议排期**：Step 1（租约抽取）可作为**独立低风险任务**先做（1~2 小时，纯搬运）；
> Step 3/4 等到下次必须改登录/捕获逻辑时**顺带**完成。
