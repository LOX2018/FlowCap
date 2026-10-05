# ADR-023：「更新凭证」按状态分流 + BCC 拉起可重试 + 陈旧锁自愈收口

- **状态**：已实施（2026-09-28）
- **触发**：用户实测报障两条 —— ①「BCC 又异常」；②「更新凭证怎么默认变成短信更新了」
- **范围**：`backend/auto_dm/accounts.py` · `backend/auto_dm/login_remote.py` · `backend/vbrowser.py` ·
  `backend/api/accounts.py` · `frontend/src/api/client.ts` ·
  `frontend/src/components/accounts/{accounts-page,LoginDialog,AccountDrawer}.tsx`
- **门禁**：`backend/test_credential_update_flow.py`（8 项，含 4 条负控）

---

## 1. 设计意图（本该怎样）

1. **凭证更新按账号状态自动分流**（ADR-017 §2.3 已拍板）：
   状态 A 全新账号 → 优先二维码；状态 B 已有登录态仅凭证过期 → 只用验证码。
   用户明确要求：**「应该自动判断，而不是让用户手动选择」**。
2. **失败必须可重试**：任何「拉起失败」都不得变成**不可逆**状态；保护逻辑（退避/熔断）
   必须真的接在触发点上，且**有出口**。
3. **profile 锁的判据必须是该 profile 自己的**：不能拿机器级进程数当「本档案在用」的证据。
4. **命名单一（Canonical Contract）**：同一能力在 UI 上只应有一个入口名。

## 2. 观察到的偏离（实测，2026-09-28 10:0x–10:4x）

| # | 现象 | 证据 |
|---|------|------|
| D1 | 张老师 BCC 拉不起来，且**之后永远拉不起来** | `run_20260928_090932.log:114`「打开指纹浏览器失败 · 拉起浏览器容器失败（BCC 此前懒加载失败（端口未就绪）…）」；`/api/accounts` → `browserDaemonAlive:false` |
| D2 | 陈旧 `parent.lock` 一直残留，Camoufox 带锁启动必撞 BCC-058 | 09:36:14 生成 `_camoufox/parent.lock`；09:37:18 `[BCC-058] Camoufox 内核启动失败…Failed to launch the browser process` |
| D3 | 短信路径第一步 100% 失败 | 09:36:00 `未找到手机号输入框 input[name="normal-input"]` → `ACC-036` |
| D4 | 扫码 Tab 锚点也失效 | 09:36:27 `点「扫码登录」: 未找到` |
| D5 | UI 上并列两颗按钮、无分流 | `刷新凭证`(→/scan) 与 `短信登录`(→/sms-login) 同级；`状态 A/状态 B` 只在 `login_remote.py` 注释里，全仓**零调用点** |

## 3. 根因（RCA —— 断在哪个节点，不是错在哪一行）

### D1 · BCC 拉起被**永久负缓存**
`auto_dm/accounts.py` 原用 `_bcc_lazy_spawned: set[str]`，语义是「已拉起过」，
但**只在 Popen 成功后 add，失败/进程死亡/用户手动处理后都不清理**：

```python
if name in _bcc_lazy_spawned:      # ← 首次失败后，本进程内恒真
    return {"ok": False, "msg": "BCC 此前懒加载失败（端口未就绪）…"}
```

⇒ 首次拉起失败即把该账号**钉死到进程重建为止**，与「后来资源已空闲」的事实完全脱钩。

### D2 · 陈旧锁自愈空转（**两个独立错误叠加**）
`login_remote.heal_stale_profile_lock` 的判据是 `count_browser_processes()` ——
**机器级** camoufox/firefox 总数。只要**任何**账号在跑（本案 8 个），本档案的陈旧锁
**永远**被判为「在用」而不清理。叠加第二个错误：**顺序颠倒**

```python
heal = heal_stale_profile_lock(profile)            # 判锁时残留进程还在 ⇒ 判「在用」
heal["reaped"] = reap_profile_processes(profile)   # 之后才清扫 —— 太晚了
```

⇒ 清扫确实执行了，但锁的判定早已按「在用」返回：
**自愈逻辑从落地起就一次都没生效过**（实测 `parent.lock` 从 09:36:14 留到 10:3x）。

### D5 · 契约漂移
ADR-017 §2.3 的「自动分流」只写进了 `login_remote.py` 的**模块注释**，
代码层零落点 ⇒ 文档描述的能力**从未到达产品**（与 H-30 曾出现的
「`login_remote.py` 是全仓零调用孤儿模块」同型故障，第二次复发）。

## 4. 决策（How）

| 缺陷 | 决策 | 契约 |
|---|---|---|
| D1 | `_bcc_lazy_spawned` → `_bcc_lazy_fail: {name: {ts, count}}` | **仅当**「连续失败 ≥ `DY_BCC_LAZY_FAIL_LIMIT`(3) **且**仍在退避窗 `DY_BCC_LAZY_FAIL_BACKOFF`(20s) 内」才拒绝；端口就绪或窗口过后**自动放行重试**。失败不再不可逆 |
| D2 | 新增 `count_profile_processes()`（命令行含本 profile 绝对路径）；新增 `ensure_profile_released()` 固定「**先清扫 → 再判锁 → 才自愈**」 | 判据不可靠时返回 `-1` ⇒ **保守不删锁**（沿用原安全语义） |
| D2+ | 自愈下沉到 `vbrowser.launch_async`（**唯一启动出口**，12 处调用点共同经过） | 任何启动路径自动受益，不再依赖各调用方自觉排序 |
| D3/D4 | **本轮不修锚点**（抖音 DOM 变动需真机重新以「不预设选择器、按内容特征」取证，见 `references/douyin_page_field_probing.md`） | 已在台账登记为独立待办；本轮以**分流**避免把用户送进已知必败路径 |
| D5 | 新增 `POST /api/accounts/{name}/update-login`；前端收敛为**单一「更新凭证」入口** + 弹层内显式「换用扫码 / 换用短信」 | 自动分流为主，显式覆盖为辅；返回 `msg` **如实标注**实际走的路径；判为短信但缺手机号时**如实上报**，绝不静默改走扫码 |

### 分流的权威判据（不自造）
复用既有 `auto_dm.accounts.uid_identity_verdict()` 的 4 元组
`(state, reason, label, detail)`：
- `state is True` ⇒ 状态 B ⇒ 短信验证码；
- `state is False` ⇒ 已确证失效（`no_credential` / `not_logged_in` / `uid_drift`）⇒ 扫码；
- `state is None`（取不到证据）⇒ **诚实降级为扫码**，不假装知道状态。

## 5. 为什么不合并两条路径
**否**。二维码与验证码是抖音两种**本质不同**的认证流（真浏览器 DOM 交互 vs 短信 RPA），
合并会让「哪条路真的跑通了」变得不可观测。正确做法是：**入口唯一、路径显式、结果如实**。

## 6. 验证（Live Verification）

```
环境：真实数据根 C:\temp\flowcap_design（隔离测试根用于跑测试）
```

| 判据 | 实测结果 |
|---|---|
| 门禁 | `test_credential_update_flow.py` **8 passed**（含 T1/T2b/T3/T4 四条负控） |
| 相关回归 | 11 个相关测试文件 **175 passed**，零回归 |
| 实机 · 陈旧锁自愈 | 张老师 `_camoufox/parent.lock`：自愈前存在 → **已真实清除**（`healed:true, procs:0`） |
| 实机 · 不误删在跑实例 | 小助理 lock 数 1→1、`healed:false`（其 profile 进程数 2 ⇒ 正确判为在用） |
| 实机 · 判据对比 | 张老师 profile 进程数 **0**；小助理 **2**；旧机器级判据 **8**（⇒ 旧判据下张老师锁永不清理，实证 D2） |
| 前端 | `tsc -b` 无 error |
| 遗留探针 | `_tmp_dom_probe_20260926` 的 BCC/recv 已用项目自身 `/quit` **优雅退出**（10060/13636 已释放），真实账号守护未触碰 |

## 7. 影响与风险

- **无 NFR 退化**：`count_profile_processes` 仅在「启动前」与「判锁时」各遍历一次进程表
  （psutil，~16 进程量级）；不在热路径。
- **单 profile 铁律更安全**：锁判据从「机器级」收紧到「本档案级」，误判面显著缩小。
- **保留的已知缺口（诚实标注）**：D3/D4 的 DOM 锚点仍未修复 ⇒ 自动分流到
  **短信**的账号当前仍会在 `fill_phone` 失败；自动分流到**扫码**的账号仍可能在
  出码处失败。本轮修的是「把它们混成一个默认入口」与「失败不可逆」，
  **不是**「两条路都已跑通」。真机端到端仍需你本人在场（收码）。

## 8. 可迁移判据（跨项目）

1. **负缓存必须有出口**：任何「失败 ⇒ 拒绝后续尝试」的状态，都要能因**环境事实变化**
   （端口就绪/超时/显式恢复）而自动失效；否则它是不可逆缺陷，不是优化。
2. **判据的作用域要最小**：问「谁占用它」，就只统计**占用它的那个东西**；机器级总数
   会把别的实例的正常行为算成本实例的故障。
3. **自愈型函数的调用顺序是契约的一部分**：`A 判 → B 清` 与 `B 清 → A 判` 语义完全相反；
   应在函数内固定顺序并只暴露一个入口，别指望每个调用点都排对。
4. **把修复下沉到「唯一出口」**：当同一缺陷有 N 个调用方时，修在共同底层的
   1 处 > 修在 N 处（`launch_async` 的 12 个调用点即此例）。
5. **文档写过的能力要能被机械检出「有没有落点」**：本项目已两次出现
   「契约只在注释里」的漂移，应纳入门禁（本 ADR 的 T5c 即此判据）。

---

## 9. 后续补修（v0.45.75）—— 上线实测暴露的两个**新**缺陷

v0.45.74 部署上线后，实机验证**自己暴露**了两个新问题，均已修复并加门禁：

### 9.1 DSSCC-BCC-002：只做「可重试」会导致**重启风暴**（安全回归）

**现象（实机复现）**：对**持续性失效**账号（张老师 · AUTH-050 身份漂移、
`state=no_credential`）执行一次 `/update-login` 后，该账号的 BCC 被**反复拉起**：
数分钟内累积 8+ 个 camoufox 进程、`parent.lock` 删了又生。

**根因**：D1 把「失败 ⇒ 永久拒绝」改成「可重试」是对的（瞬时故障需自愈），
但**对永久性故障，无上限的可重试 = 无限重启** —— 恰是本项目最忌的风控信号
（历史血案：BCC-025 未接熔断 ⇒ 单日 155 次重启，见知识库 06/07 章）。
另有一条**旁路**：`browser_daemon` 的冷启动走 `vbrowser.launch_async` **直连**，
**不经** `ensure_bcc` 的 spawn 出口 ⇒ 只在 ensure_bcc 加节流仍拦不住守护路径。

**修复（把「不可逆」换成「有速率上限的可重试」）**：
- `_bcc_spawn_hist` + `_BCC_SPAWN_MAX`(4) / `_BCC_SPAWN_WINDOW`(900s) 滑窗，
  做在**唯一 spawn 出口**（`ensure_bcc` 的 Popen 处，记账在真正 spawn 之后）；
- 新增 `login_remote.bcc_launch_allowed(name)`：把同一限额做成**可复用判据**，
  `vbrowser.launch_async`（唯一启动出口，12 调用点）在启动前调用，
  命中即抛 `BCC-081`（**编码先查后用**：BCC-080 已被 `/show` 占用）；
- 退避窗由 20s 提到 120s。

**验证**：门禁 `T6`（滑窗内 6 次调用只放行 4 次 + 负控抬高上限后恢复拉起）、
`T6b`（两条启动路径都必须拦）。实机：张老师事件止息，20s 观察无守护再起。

**可迁移判据**：「可重试」不是安全属性，「**有速率上限**的可重试」才是。
凡把「不可逆拒绝」改为「可重试」，必须同时给出**速率上限**与**旁路覆盖检查**
（问：还有哪条路径能绕过我加的这个闸？）。

### 9.2 DSSCC-BCC-005：进程清扫函数会**杀掉调用者自己**（实测自伤）

**现象**：诊断命令执行到 `ensure_profile_released`（内部调
`vbrowser_camoufox._reap_camoufox_processes`）时，终端**无任何输出即被终止**
（exit 15）。二次复现后定位：**连发起命令的 shell 一起被杀**。

**根因**：`_reap_camoufox_processes` 的 `_victims()` **只按命令行是否含 profile
路径**匹配，与其自身文档「只杀命中该 profile 的 camoufox/firefox 进程」不符 ——
而「命令行里恰好写了这个 profile 路径」对**诊断脚本 / shell 命令**是极常见的事
（本次就是这样），于是调用者被自己列入待杀名单。

**修复**：`_victims()` 补**进程名前置过滤**（`camoufox`/`firefox`），与
`count_profile_processes` 同一判据；`process_iter` 增加 `"name"` 字段。

**验证**：门禁 `T7`（静态断言过滤行存在）。修复前实测「命令含路径 ⇒ 自杀」，
修复后同样的清理命令可正常返回。

**可迁移判据**：**以「命令行文本」为判据的进程操作，必须叠加进程名/可执行体判据** ——
文本匹配会把「恰好提到该路径」的无关进程（含调用者）一并命中；且这类误杀在
Windows 上表现为**无输出的静默死亡**，排查成本极高。

