# RPA 短信登录三 Bug —— 一键登录 / 启动竞态 / 探活判据（+ AUTH-050 身份漂移）

> 归档日期：2026-09-26 · 版本：v0.45.18 · 分支：design/better-douyin

本案例记录 DYAutoDM_v2 在 **RPA 短信登录路径**（`backend/auto_dm/login_remote.py`）中
发现并修复的 **3 个真实缺陷**，以及一并查清的 **AUTH-050 身份漂移判据** 与 **凭证更新四重验证**。
所有结论均基于 **真机运行证据（Live-Instance Verification）**，非推断。

- **处置对象**：`auto_dm/login_remote.py`（RPA/DOM 登录编排层，ADR-017）
- **相关提交**：`da3146b`（v0.45.17）· `79c7311`（v0.45.18）
- **上游设计文档**：`docs/adr/ADR-017-im-remote-login-credential-update.md`（§8 实施收口）
  · `docs/adr/ADR-017-调研记录-20260926.md`

---

## 环境信息

| 项 | 值 |
|---|---|
| 平台 | Windows 11 |
| Python | 3.14（`C:\Users\LOX\AppData\Local\Programs\Python\Python314\python.exe`）|
| Camoufox | 152.0.4-beta.30 |
| `DY_APP_ROOT` | `C:\temp\dyautodm_design` |
| 会员空间 | `m17db0f8209156f26` |
| 测试账号 | 「尚进工伤小助理」（账号自身 uid = `316276709526638`）|
| 代理 | 系统代理 `http://127.0.0.1:10808` |
| 指纹种子 | 固定 = `98325707` |

---

## 一、缺陷 1：「一键登录」界面未处理

### 1.1 问题现象

P2 实测脚本执行短信登录流程时，三处锚点全部失败：

```
[⑤] 切「验证码登录」tab: False
[⑥] 未找到 input[name="normal-input"]
[⑦] 未找到「获取验证码」
```

⇒ **短信流程 100% 失败**。

### 1.2 根因分析（到代码行为层）

**关键设计假设（错误）**：`login_remote.py` 假定「任何 profile 状态下，登录面板都存在手机号表单
`input[name="normal-input"]` 与「验证码登录」tab」。

**实测推翻该假设**：当账号 profile 里抖音**记住了上次登录账号**时，点「登录」弹出的**不是手机号表单**，
而是 **「一键登录」面板**。P6 取证的 DOM 结构为：

```html
<p class="B_Nj1uaz">尚进工伤小助理</p>            <!-- 显示记住的账号昵称 -->
<div id="douyin_login_comp_btn_id">一键登录</div>   <!-- 主按钮 -->
<div class="cq1UKYpd"><p>登录其他账号</p></div>      <!-- ★ 进入表单的唯一入口 -->
```

**执行链（失败路径）**：

```
goto(LOGIN_URL) → 点「登录」
  → 弹出「一键登录」面板（非表单）
  → click_by_text("验证码登录")  → 无此 tab      → False    ← 现象 ⑤
  → locator('input[name="normal-input"]') → 不存在 → False   ← 现象 ⑥
  → click_by_text("获取验证码")   → 不存在        → False    ← 现象 ⑦
  → 短信流程中断
```

⇒ **根本原因**：表单锚点（`normal-input`）与「验证码登录」tab **在一键登录面板下根本不存在**，
流程未处理该界面分支，直接找锚点必然失败。

### 1.3 修复方案

在 `login_remote.py` **新增** `handle_one_click_login(page, timeout_s=6.0)`
（源码 `login_remote.py:267`）：检测到「一键登录」文本后，点击「**登录其他账号**」进入表单界面。

```python
async def handle_one_click_login(page, timeout_s: float = 6.0) -> dict:
    """处理「一键登录」界面：若出现则点「登录其他账号」进入表单。"""
    out = {"one_click": False, "switched": False}
    # 轮询：等「一键登录」或表单任一出现
    st = await page.evaluate(r"""
(() => {
  const el = document.querySelector('#douyin_login_comp_btn_id');
  const one = !!(el && (el.innerText||'').includes('一键登录'));
  const form = !!document.querySelector('input[name="normal-input"]');
  return {one, form};
})()
""")
    if st.get("form"):
        return out                      # 已是表单，无需处理
    if st.get("one"):
        out["one_click"] = True
        # ★ 进入表单的唯一入口
        if await click_by_text(page, "登录其他账号"):
            out["switched"] = True
            await asyncio.sleep(1.5)
    return out
```

### 1.4 实测证据

| 阶段 | 结果 |
|---|---|
| P2（修复前）| 切 tab `False` / 填号 `False` / 发码 `False` ⇒ 100% 失败 |
| P6（DOM 取证）| 抓到 `#douyin_login_comp_btn_id`（一键登录）+ `<p>登录其他账号</p>` |
| 修复后 | 检测到「一键登录」→ 点「登录其他账号」→ 表单渲染 → 短信流程可继续 |

**涉及提交**：`79c7311`（v0.45.18）。

---

## 二、缺陷 2：启动时序竞态（错误码 BCC-058）

### 2.1 问题现象

启动浏览器报错：

```
BrowserType.launch_persistent_context: Target page, context or browser has been closed
```

实测「连续启动 → 关闭 → **立刻**再启动」必然失败，**约 4 分钟后自动恢复**。

### 2.2 根因分析（到代码行为层）

**根本原因**：Camoufox 的 `ctx_mgr.__aexit__()` / `close_camoufox_context()` 调用后，
**浏览器进程需时间退净**（项目既有文档已记录：「调 `close_camoufox_context()` 后等 60s → 仍是 2 个进程」）。
新实例立刻启动，会撞上尚未退净的旧进程占用**同一 profile** ⇒ 持久化上下文启动失败。

**执行链（失败路径）**：

```
close_handle() → close_camoufox_context()
  → 旧 camoufox/firefox 进程仍在退净中（实测关闭后进程数 = 7）
  → 立刻 launch_persistent_context(user_data_dir=同一 profile)
  → 撞上旧进程占用 profile → TargetClosedError（BCC-058）
  → 超时 180s
```

**判别证据（同一现象在不同时刻表现不同 ⇒ 时序性）**：4 分钟后旧进程退净，同一次启动即可成功
⇒ 证明失败源于**时序竞态**而非配置错误。

### 2.3 修复方案

在 `login_remote.py` 新增 `reap_profile_processes(profile_dir)`（源码 `login_remote.py:149`），
**复用项目既有能力** `vbrowser_camoufox._reap_camoufox_processes`（`vbrowser_camoufox.py:177`）——
该函数只杀**命令行命中本 profile 绝对路径**的进程（含 `-contentproc` 子进程），含防误杀边界
（路径过短拒绝执行）；**绝不按进程名盲杀**。并在 `prepare_qr_login` / `do_sms_login`
**启动前**调用：

```python
def reap_profile_processes(profile_dir: str) -> int:
    """启动前清扫仍占用该 profile 的 Camoufox 残留进程（复用项目既有能力）。"""
    try:
        from vbrowser_camoufox import _reap_camoufox_processes
        n = _reap_camoufox_processes(profile_dir)   # 含安全边界，不盲杀
        if n:
            logger.info("[login_remote] 启动前清扫残留进程 {} 个: {}", n, profile_dir)
        return n
    except Exception as e:  # 任何异常都不阻塞启动
        logger.warning("[login_remote] 残留清扫失败（不阻塞启动）: {}", e)
        return 0
```

**启动门禁判据链**（两处启动路径统一走）：

```python
profile = _acc.profile_dir_of(env_path)
os.makedirs(profile, exist_ok=True)
heal = heal_stale_profile_lock(profile)        # ① 递归查锁 + 无同名进程时安全删锁
heal["reaped"] = reap_profile_processes(profile)  # ② ★ 启动前清扫残留进程
# ③ 仅在前两步之后：
pw, browser, context, backend = await launch_async(
    mode, _cfg, headless=headless, force=False,
    account=acc_name, user_data_dir=profile)
```

### 2.4 实测证据

| 项 | 修复前 | 修复后 |
|---|---|---|
| 关闭后进程数 | **7**（残留）| **0**（1s / 3s / 5s 连续采样均 0）|
| 立刻重启 | 超时 **180s** 失败 | 成功，耗时 **5.8s** |
| 连续 3 轮启动/关闭 | — | **全部成功** |

**涉及提交**：`da3146b`（v0.45.17）与 `79c7311`（v0.45.18）。

> **关联缺陷（`da3146b` 同批修复）**：
> ① 自造的 `close_handle`（用 `browser.close()`）因 `backend == "exe"` 判断错误而**完全空操作**
> ⇒ 改为复用项目既有 `close_camoufox_context()`（`vbrowser_camoufox.py:274`，含残留清扫）。
> ② 陈旧锁查找**只查根目录**导致漏判 —— Camoufox 真实 profile 在 `<profile>/_camoufox/` **子目录**，
> ⇒ `heal_stale_profile_lock` 改为**递归**查找（`find_lock_files`，限深度 3）。

---

## 三、缺陷 3：探活判据误用 UI（差点导致误判）

### 3.1 问题现象

凭证看起来「完全有效」：

| 表面证据 | 实测值 |
|---|---|
| profile cookie 数 | **66 个** |
| 登录标识 | **8/8 齐全**（`sessionid` / `sessionid_ss` / `sid_tt` / `uid_tt` / `uid_tt_ss` / `passport_csrf_token` / `odin_tt` / `sid_guard`）|
| 页面 UI | **显示已登录** |

曾一度据此得出「**不需要短信登录**」的结论。

### 3.2 根因分析（到代码行为层）

**UI 登录态是假象**。抖音官方 passport 接口实测返回：

```http
GET https://www.douyin.com/passport/account/info/v2/
→ {"data":{"description":"会话过期，请重新登录","error_code":13,
           "session_key":"","user_id":0},"message":"error"}
```

⇒ **根本原因**：以 **UI 显示 / 本地 cookie 存在性** 作为凭证有效性判据是**不成立的**。
- 本地 cookie 只是客户端缓存，服务端会话已失效；
- UI「已登录」是页面状态机的乐观呈现，不代表服务端承认该会话。

### 3.3 修复方案

引入**官方接口判据**：判定 `user_id > 0 且 error_code 为空` 才算会话有效，
**不再以 UI / 本地 cookie 存在性为准**。

```python
# 判据（伪代码示意）：官方 passport 接口
#   valid = (data["user_id"] > 0) and (data["error_code"] in (None, 0))
```

### 3.4 实测证据

| 判据来源 | 结果 |
|---|---|
| 本地 cookie（66 个）+ UI 显示已登录 | ❌ **假象** |
| 官方 passport 接口 | ✅ 返回 `error_code=13` / `description=会话过期，请重新登录` / `user_id=0` ⇒ **会话无效** |

**涉及提交**：`79c7311`（v0.45.18）。

> **铁律**：见证凭证有效性的唯一可靠判据是**服务端接口答复**，不是 UI 状态。
> 见「教训」章节第 4 条。

---

## 四、关键概念澄清：AUTH-050「身份漂移」

### 4.1 判据定义

项目用「**探活 uid ∈ 该账号历史 `conv_id`**」判定凭证身份一致性：命中 ⇒ 身份一致；不命中 ⇒ **身份漂移**。

> 🔴 **关键澄清（搞错会误判漂移）**：`conv_id` 格式为 `0:1:<对方uid>:<账号自身uid>`
> ⇒ **`field[3]` 才是账号自己的 uid**（`field[2]` 是**对话对方**的 uid）。

判据实现见 `services/uid_probe.py` 的 `_uid_consistent_with_history`：
「该 uid 必须出现在该账号**至少一条**历史 `conv_id` 中」；无历史会话（新账号）返回 True（不冤枉），
DB 不可用也返回 True（降级放行）。

### 4.2 本次实例

| 项 | 值 |
|---|---|
| 账号自身 uid（`conv_id` field[3]）| `316276709526638` |
| 失效凭证探活 uid | `867973938286267`（**不在历史中**）|

⇒ 失效凭证 **确认漂移**，且与抖音官方「会话过期」判定**一致**（缺陷 3 的官方接口证据）。

### 4.3 修复前后对比

| | 旧凭证（失效）| 新凭证（修复后）|
|---|---|---|
| sessionid | `abc2ce20f304551c…` | `17cac3f531423d7d…` |
| 探活 uid | `867973938286267`（漂移，不在历史）| `316276709526638`（命中历史 field[3]）|
| 官方接口 | `error_code=13`（会话过期）| `error_code=None`（有效）|

**涉及提交**：`79c7311`（v0.45.18）。

---

## 五、凭证写入协议（复用既有能力，禁止自造）

凭证写入统一走既有入口，**不自造写盘逻辑**：

```
auth_helper.save_cookie_to_env(cookie_str, env_path)
  → services/member_ctx.write_env_file(..., merge=True)
```

- **只写加密 `.env.enc`**：明文 `.env` 已于 2026-09-21 废弃，**即使存在也不被读取**；
- `merge=True` **保留其余 7 个字段**：`DY_KEYS` / `DY_PRIVATE_KEY` / `DY_TICKET` / `DY_TS_SIGN`
  / `DY_WEB_PROTECT` / `DY_CLIENT_CERT` / `DY_PROXY_MODE`；
- **写前必须备份旧凭证**：本次实际备份为 `.env.enc.bak.20260926_145722`。

> 任何「自己 `open(...).write`」形态均为**契约违规**。

---

## 六、部署 / 验证状态（四重，全部通过）

| # | 验证维度 | 判据 | 结果 |
|---|---|---|---|
| 1 | **磁盘可读** | `member_ctx.parse_env_dict(env_path)` 成功，8 个字段齐全，`sessionid = 17cac3f5…` | ✅ 通过 |
| 2 | **身份正确** | 从磁盘读出的 cookie 探活 uid = `316276709526638`，命中历史 `conv_id` field[3] | ✅ 通过 |
| 3 | **官方有效** | passport 接口 `user_id=316276709526638`、`error_code=None` | ✅ 通过 |
| 4 | **业务可用** | `get_conversation_list` → **1 条**（真实业务接口调用成功）| ✅ 通过 |

**结论**：四重验证**全部通过**，凭证更新闭环成功（`79c7311`，v0.45.18）。

---

## 七、验收命令

```bash
# ── ① 磁盘可读：8 字段齐全 + sessionid ─────────────────────────────
# （在项目 Python 环境内）
python -c "from services import member_ctx; d = member_ctx.parse_env_dict(ENV_PATH); \
print(len(d), d.get('DY_SESSIONID') or d.get('sessionid'))"
# 期望: 8 17cac3f5...

# ── ② 官方会话判据（passport 接口）────────────────────────────────
# 用磁盘读出的 cookie 请求：
#   GET https://www.douyin.com/passport/account/info/v2/
# 期望: user_id=316276709526638, error_code=None  （非 13/非 0）
# 反例（失效凭证）: error_code=13, description=会话过期，请重新登录, user_id=0

# ── ③ 身份一致性：探活 uid ∈ 历史 conv_id field[3] ───────────────
# services/uid_probe.py 的判据：uid 必须出现在该账号至少一条历史 conv_id 中
# 期望: 命中（316276709526638）

# ── ④ 业务可用：真实业务接口 ─────────────────────────────────────
# get_conversation_list(env_path)
# 期望: 返回 1 条会话

# ── ⑤ 启动竞态：关闭后进程退净 + 立刻重启 ────────────────────────
# 关闭浏览器后采样进程数（1s/3s/5s）:
tasklist | grep -icE 'camoufox|firefox'
# 期望: 0（修复前: 7）—— 非 0 时 reap_profile_processes 会在启动前清扫
```

---

## 八、教训（可复用规则）

1. **判据优先于表象**：判定凭证/会话是否有效，唯一可靠依据是**服务端接口答复**；UI 状态与本地
   cookie 数量只能作为线索，**不得**作为判据（Live-Instance Verification）。
2. **「关闭成功」≠「资源已释放」**：关闭浏览器后进程需时间退净，**启动前必须主动清扫残留**
   （`_reap_camoufox_processes`），否则新实例撞旧进程占同一 profile 即报 BCC-058。
3. **改代码前先查项目既有能力**：关闭浏览器、清进程、写凭证等均有既有实现
   （`close_camoufox_context` / `_reap_camoufox_processes` / `save_cookie_to_env`），
   **复用优于自造** —— 自造关闭逻辑曾因 `backend == "exe"` 判断错误而完全空操作。
4. **定位要递归，不能只看根目录**：Camoufox 真实 profile 在 `<profile>/_camoufox/` 子目录，
   只查根目录会漏判陈旧锁 ⇒ 锁/资源查找必须**递归**。
5. **界面分支要枚举完整**：同一入口（点「登录」）在不同 profile 状态下会呈现不同界面
   （表单 vs「一键登录」），流程必须识别**所有**分支并进入目标界面，否则整条路径静默失败。
6. **约定字段的语义必须查实**：`conv_id` 的 `field[3]` 才是账号自身 uid（`field[2]` 是对端），
   语义弄错会把正常身份误判为漂移 —— 涉及身份判据的字段，先查实再使用。
7. **凭证写盘只走唯一入口**：统一 `save_cookie_to_env` → `write_env_file(..., merge=True)`，
   只写加密 `.env.enc`，写前备份；任何自造写盘形态均为契约违规。

---

## 九、相关提交

| commit | 版本 | 一句话说明 |
|---|---|---|
| `9202c11` | v0.45.14 | IM 远程登录更新凭证 —— RPA/DOM 方案 + 修单 profile 铁律契约漂移 |
| `49c3168` | v0.45.15 | vendor 上游登录模块 + 适配层（ADR-017 API 备用路径）|
| `8dba9b1` | v0.45.16 | API 路径能力边界显式化 —— 扫码可用 / 短信禁用（AUTH-072）|
| `da3146b` | v0.45.17 | 修 RPA 两个真 Bug —— **锁查找漏子目录 + 关闭浏览器完全失效** |
| `79c7311` | v0.45.18 | 修 RPA 短信登录三个真 Bug —— **一键登录 / 残留竞态 / 探活判据** |

> 分支：`design/better-douyin`。本轮所有结论均基于**真机运行证据**；
> **UI 状态不可作判据**（见缺陷 3 与教训第 1 条）。
