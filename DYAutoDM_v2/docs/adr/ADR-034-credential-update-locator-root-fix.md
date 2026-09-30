# ADR-034：更新凭证「定位不准」根治 —— 扫码走上游 API（子进程）+ 短信补「一键登录」分流

- **状态**：已实施（2026-09-30）
- **触发**：用户实测报障「凭证更新现在用的是有问题的二维码和短信通道 …… 定位是有问题的，
  没有办法精准找到二维码和短信登录的入口」；并给出两条可选路径：
  「先尝试修复，如果不能就变回最初的弹出浏览器用户手动更新」。
- **范围**：`backend/auto_dm/login_remote.py` · `backend/api/accounts.py` ·
  `backend/login_qr_api_runner.py`（新） · `backend/main.py` · `scripts/build_sidecar.py` ·
  `backend/requirements.txt`（curl_cffi/scrapling 早已登记，无需改）
- **前置**：ADR-017（IM 远程登录）· ADR-023（更新凭证分流）· H-30
- **门禁**：`backend/test_credential_update_flow.py`（11）· `backend/test_h30_rpa_wiring.py`（22）·
  `backend/test_h22_p4_audit_fixes.py`（G1 命名空间碰撞）· `backend/test_dom_locator_gate.py`

---

## 1. 设计意图（本该怎样）

「更新凭证」的两个入口（**上方「更新凭证」** / 凭证校验区**「立即处理验证」**）必须能
**精准到达**抖音登录页的两种认证入口：**二维码**（扫码）与**短信验证码**。

ADR-017 §8.1 早已拍板分工：
```
扫码登录 → API 路径（纯协议，快且轻）
短信登录 → RPA 路径（真浏览器，天然带全部指纹，无需 dtrait）
```

## 2. 观察到的偏离（实测，非推断）

用户实际点击的是 `POST /update-login {mode:"sms"}`（显式备用路径）。运行日志实证：

```
16:19:34 [update-login] 账号 四川工伤张老师 显式备用路径 → sms（state=no_credential）
16:19:51 [login_remote] 短信登录失败于 fill_phone: 手机号填入失败（input[name="normal-input"]）
16:19:51 [ACC-036] [scan] 账号 四川工伤张老师 短信登录失败（stage=fill_phone）
```

| # | 偏离 | 证据 |
|---|---|---|
| D1 | **短信走错界面分支** | 「已登录但凭证过期」的 profile，抖音弹的是**「一键登录」面板**，手机号框 `input[name="normal-input"]` **不存在** ⇒ `fill_phone` 100% 失败 |
| D2 | **处理函数定义了却零调用点** | `login_remote.handle_one_click_login`（:426）**全仓 grep 仅自身**；`do_sms_login` 从不调用它 ⇒ 契约漂移（与 H-30「孤儿模块」同型，第二次复发） |
| D3 | **二维码主锚点失效** | 真机 DOM 快照：`#animate_qrcode_container` 内是 **SVG（13 个 `<image>`）**，无 `<img>`；旧主锚点 `"#animate_qrcode_container img"` **恒命中 0**（`_live/live_report.json` 的 `qr_img=0`）⇒ 只剩慢速枚举兜底 |
| D4 | **扫码 API 路径从未接线** | `login_api_vendor.VendorLoginApi`（`get_qrcode`/`check_qrcode`）**唯一调用点是测试**（`test_h22_p4_audit_fixes.py`）；产品走的是浏览器 DOM 截图 |
| D5 | **tab 锚点不可用** | 两个 tab 的真实节点是 `<span class="C6OZQwMA">`（**哈希类名、无 id**），不可作点击锚点 |

## 3. 根因（RCA —— 断在哪个节点）

- **D1/D2**：`do_sms_login` 的**界面分流契约缺实现落点**。ADR-017/023 写的是「按账号状态自动分流」，
  但落地时只写了注释与一个未被调用的函数 ⇒ 流程从「找手机号框」开始，而当前界面根本没有该控件。
- **D3/D5**：**外部契约漂移**（抖音登录页改版）：二维码由 `<img>` 改 SVG、tab 由具名改哈希类名。
  硬编码锚点必然失效。
- **D4**：**能力已实现但未接线**（同 H-30 型）：更高效的工具（纯 API）早已 vendor 就位，产品未用。

## 4. 决策（How）

### 4.1 扫码：优先**上游 API（纯 HTTP）**，RPA 降为兜底

**形态 = 干净子进程 + 文件交接**（**不是**同进程函数调用）：

```
backend/login_qr_api_runner.py        子进程入口（单进程从头跑到尾）
  bootstrap_auth → get_qrcode → 落 qr.png + status.json
  → 轮询 check_qrcode → scanned → confirmed → _follow_login_redirect
  → 落 result.json（cookie_str + ticket + ts_sign + client_cert + private_key）
冻结态由 `dyautodm-backend --qr-api-runner` **前置分发**复用同一份解释器
```

**为什么必须子进程**：vendor 与 backend 有 **5 个同名顶层包**
（`dy_apis`/`builder`/`utils`/`dy_live`/`static`）。应用进程必然已加载项目版 ⇒
上游顶层绝对导入会命中项目实现 ⇒ **静默半坏**。
这不是推测 —— `login_api_vendor._import_upstream` 的 **AUTH-073** 与门禁
`test_h22_p4_audit_fixes.check_g1`（**断言在应用进程内调用必须抛 AUTH-073**）已把这条钉死。

**为什么单进程而非「取码进程 + 轮询进程」**：票据与 P-256 私钥是**会话内状态**
（`bd_ticket_guard_client_data` cookie 绑定公钥，服务端据此签发四件套）。
跨进程重建需序列化私钥，脆弱且没必要 ⇒ 一个进程跑到底，只把产物落盘。

**前置分发为何必须在任何项目 import 之前**：一旦 `from config import settings` 执行，
`dy_apis` 等就进了 `sys.modules` ⇒ 子进程不再「干净」。故 `main.py` 顶部分发块
**先于**所有项目 import。

### 4.2 短信：接入「一键登录」分流 + 稳定 id 的 tab

- `do_sms_login` 在打开登录页后**第一步**调用 `handle_one_click_login`；
- `handle_one_click_login` 判据重写为**决定性标记**：
  ① 有 `input[name="normal-input"]` ⇒ 已是表单；
  ② 页面出现「**登录其他账号**」⇒ 一键登录面板（**唯一入口**，比旧判据可靠 ——
     旧判据把 `#douyin_login_comp_btn_id` 当「一键登录」按钮，**实测其文本是「登录」**）；
  ③ 都无 ⇒ 如实返回，**绝不猜、不乱点**。
- 点击走「JS `.click()` → 失败则**真实鼠标**（`_mouse_click_by_text`）」双路
  （本项目既有结论：Semi/Firefox 下 JS `.click()` 常不生效）。
- tab 改点**稳定 id 容器**（`#douyin_login_comp_mobile_code`），不再依赖哈希类名。

### 4.3 二维码 RPA 兜底：改「容器栅格化」

`_QR_ANCHORS` 首选改为 `#animate_qrcode_container` **容器本体** ——
Playwright 元素截图会把 SVG 栅格化 ⇒ cv2 仍可解码（旧 `img` 锚点保留在次位）。

### 4.4 凭证写入协议（复用唯一入口 + 写前备份 + 校验不过就还原）

- 写盘**只走** `DYLoginApi().save_credential(auth, env_path)` → `member_ctx.write_env_file(merge=True)`；
- **写前备份** `<env_path>.enc` → `.bak.<时间戳>`；校验不过则**还原**；
- **硬门禁**：必须 **sessionid + 四件套（ticket/ts_sign/client_cert/private_key）齐全**
  才落盘 —— 否则宁可回落 RPA，**绝不写半成品**（缺四件套会反派生旧签名而"假成功"）；
- 落盘后三重校验：① 磁盘可读 ② 服务端 `passport/account/info/v2` 有效
  ③ 探活 uid ∈ 该账号历史 `conv_id`（防串号/幽灵身份）。

### 4.5 环境显式（不探测本机）

API 子进程的代理**只由账号配置显式决定**（`parse_proxy_config` 的 node 模式才注入），
与既有「环境门阀」原则一致，**绝不探测本机**。

## 5. 实机验证（Live Verification）

| 判据 | 命令 / 方式 | 结果 |
|---|---|---|
| API 纯 HTTP 冒烟（真机） | 干净子进程 `bootstrap → get_qrcode → check` | ✅ `bootstrap 33 cookie` / `error_code=0`（含 PNG）/ `status=new` |
| runner 本体（真机） | `python backend/login_qr_api_runner.py --jobdir …` | ✅ `qr.png` **PNG 魔数正确**、`status.json` token |
| 冻结分发入口（源码等价） | `python main.py --qr-api-runner --jobdir …` | ✅ 出码成功（808B PNG） |
| 集成胶水层 | `spawn → poll → reap` 真实子进程 | ✅ G1 出码 / G2 短超时正确收口（不崩不挂）/ G3 回收后进程确已退出 |
| 门禁 | `pytest test_credential_update_flow test_h30_rpa_wiring test_h22_p4_audit_fixes test_dom_locator_gate` | ✅ **47 passed**（11+22+6/…）|

### 本轮实机抓到并修掉的**自造缺陷**（诚实留痕）
`login_qr_api_runner._emit(jobdir, stage, **extra)` 与调用方 `_emit(..., stage=stage)`
**位置参数撞名** ⇒ 失败路径抛 `TypeError: _emit() got multiple values for argument 'stage'`。
被顶层兜底捕获落盘才未静默消失。修法：位置参数改名 `stage_value` + `extra.pop("stage")`。
**判据**：任何「进度/结果落盘」函数都要有**顶层兜底写盘**，否则子进程异常退出只留「消失了」。

## 6. 影响面与风险

- **收益**：扫码**不再依赖 DOM**（抖音改版免疫）且**不占 profile 锁**（纯 HTTP）；
  短信**能到达手机号表单**（原为 100% 失败）。
- **未验证（诚实标注）**：
  - **扫码 → confirmed 全流程**未端到端跑（需用户真机扫码）；
  - **短信全流程**未端到端跑（需用户真机收码 + 在 Web 前端提交）；
  - **冻结态打包**未验证（`--qr-api-runner` 分发 + vendor 收集 + curl_cffi 原生库），
    需一次真实 `build_sidecar` + 部署；
  - 上游 login API 依赖 fpk1（本机无指纹采集）时用项目内 fixture —— 极端风控下可能被要求二次验证。
- **回退**：两条路径都在**既有能力**上做**加性**改动；API 失败自动回落 RPA，
  RPA 失败自动回落「有头浏览器用户手动」（`_do_scan` 原手动分支**一行未改**）。

## 7. 可迁移判据

1. **「能力已实现」≠「已接线」**：凡文档/模块声称的能力，必须机械检出**调用点**
   （本项目已两次踩：H-30 孤儿模块、本次 `handle_one_click_login`）。
2. **同名顶层包 ⇒ 只能子进程隔离**：同进程 `sys.path` 操作无法解决（项目必备这些包），
   唯一正确形态是干净解释器 + 显式注入路径。
3. **改版免疫的定位顺序**：确定性接口/入口 → 稳定 id → 容器栅格化/机械解码 → 文本兜底。
   哈希类名与 `<img>` 锚点都是会静默失效的脆判据。
4. **「界面分支」必须显式枚举**：同一入口在不同账号状态下渲染不同面板；
   直接找控件必然在某个分支 100% 失败。
5. **落盘类子进程必须顶层兜底写盘**：否则失败只剩「进程消失了」。
6. **凭证写盘硬门禁**：四件套不全**绝不落盘** —— 否则会「因缺件而反派生旧签名」造成假成功。
