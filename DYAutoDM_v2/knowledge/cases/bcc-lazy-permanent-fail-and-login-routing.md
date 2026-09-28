# 案例：BCC 拉起被永久负缓存 + 陈旧锁自愈全程空转 + 「更新凭证」契约漂移

- **日期**：2026-09-28
- **报告人**：用户（LOX）实测报障
- **触发原话**：「bcc又异常，而且更新凭证怎么默认变成短信更新了」
- **版本**：v0.45.71 → v0.45.72+（本案例落地的提交）
- **ADR**：`docs/adr/ADR-023-credential-update-routing-and-bcc-lazy-retry.md`
- **门禁**：`backend/test_credential_update_flow.py`

---

## 1. 报案现象

| # | 用户可见现象 |
|---|---|
| 1 | BCC 又异常：张老师账号 `browserDaemonAlive:false`，点「打开浏览器」报「拉起浏览器容器失败（BCC 此前懒加载失败（端口未就绪）…）」 |
| 2 | 「更新凭证」的默认动作看起来变成了短信更新（且短信也更新不了） |

## 2. 排查链（Execution-Chain Traceability）

```
用户点「打开浏览器」/「刷新凭证」
   ↓ HTTP  POST /api/accounts/{name}/open-browser  /  /scan
api/accounts.py  →  acct_core.ensure_bcc()
   ↓
auto_dm/accounts.py  ensure_bcc()
   ├─ _port_open(10042) = False           （BCC 确实死了）
   ├─ name in _bcc_lazy_spawned  ↑↑       ← 断点①「永久负缓存」恒真
   └─ return「BCC 此前懒加载失败（端口未就绪）」
        ↑ 与「残留进程早已清扫、端口早已空闲」的事实**完全脱钩**

另一条线（为什么第一次会失败）：
   ↓
login_remote.prepare_qr_login / do_sms_login
   ├─ heal_stale_profile_lock(profile)         ← 判锁：count_browser_processes()=8 ≠ 0
   │      ⇒ 判「在用」⇒ 不清锁（断点②：判据作用域错误）
   └─ reap_profile_processes(profile)          ← 之后才清扫（断点③：顺序颠倒）
   ↓
launch_async(...) → Camoufox 带陈旧 _camoufox/parent.lock 启动
   ↓
[BCC-058] Failed to launch the browser process      ← 终端现象
```

**关键**：三个断点各自独立，缺任一个都不会走到 BCC-058 —— 这类「多因一果」
必须逐段取证，只盯终端报错会误修。

## 3. 三段定位证据（全部来自运行日志/活体接口，非推断）

| 证据 | 出处 |
|---|---|
| `09:36:13 [BCC-074] 关闭后仍残留 2 个进程…开始清扫` | `browser_daemon_20260928.log` |
| `09:36:14 _camoufox/parent.lock` 生成时间戳 | `stat` 实机 |
| `09:36:36 打开指纹浏览器失败 · 拉起浏览器容器失败（BCC 此前懒加载失败…）` | `run_20260928_090932.log:114` |
| `09:37:18 [BCC-058] Camoufox 内核启动失败…Failed to launch` | `run_20260928_090932.log:126` |
| `09:36:00 短信登录失败于 fill_phone: 未找到手机号输入框 input[name="normal-input"]` | `run_20260928_090932.log:97` |
| `09:36:27 点「扫码登录」: 未找到` | `run_20260928_090932.log:108` |
| 活体：张老师 `browserDaemonAlive:false / level:expired / label:身份漂移（AUTH-050）` | `GET /api/accounts` |
| 活体：旧判据 `count_browser_processes()=8`、本档案 `count_profile_processes()=0` | 实机脚本 |

## 4. 根因收敛

1. **负缓存无出口** —— `_bcc_lazy_spawned` 只在成功时写、任何路径都不清 ⇒ 首次失败不可逆。
2. **判据作用域错误** —— 用机器级进程总数判「本档案是否在用」。
3. **顺序颠倒** —— 「先判锁后清扫」使自愈恒不生效（自落地起 0 次成功）。
4. **契约漂移** —— ADR-017 的「按状态自动分流」只在注释里，无实现落点；
   UI 上并列 `刷新凭证` / `短信登录` 两钮，用户体感即「默认变成短信」。
5. **已知未修（诚实标注）** —— 抖音登录页 DOM 锚点已变
   （`input[name="normal-input"]` 不存在、`扫码登录` tab 找不到）；
   属「外部契约漂移」，需真机按内容特征重新取证，**不在本轮范围**。

## 5. 修复与验证

- 修复见 ADR-023 §4；门禁 8 项（含 4 条负控）全绿。
- 实机验证：张老师陈旧 `parent.lock` **已真实清除**；在跑的小助理锁**未被误删**；
  `tsc -b` 无 error；相关回归 175 passed。
- 遗留：`_tmp_dom_probe_20260926`（09-26 探针）常驻 BCC/recv 已用项目自身 `/quit` 优雅退出。

## 6. 可迁移判据

1. **负缓存要有出口**（超时/环境事实/显式恢复三选一，至少其一）。
2. **判据作用域最小化**：问「谁占用它」只统计占用它的对象；机器级总数会把别人的
   正常行为算成你的故障。
3. **自愈型函数的调用顺序是契约**：顺序反了等于没做，且**不会报错**（最危险）。
4. **修在唯一出口**：同缺陷 N 个调用方时，下沉到共同底层 1 处。
5. **文档写过的能力要被机械检出「有没有落点」**（本项目两次踩：H-30 孤儿模块、
   本次 ADR-017 分隔注释）。门禁应含此类静态断言。
6. **「多因一果」禁止只盯终端报错**：本案三个断点各自都不足以单独造成现象。

## 7. 未闭环项（移交台账）

- [ ] 抖音登录页锚点重建（`normal-input` / `扫码登录` tab / 一键登录面板）—
      需真机；方法：**不预设选择器**，按内容特征 hook + 虚拟列表滚动取证。
- [ ] 短信路径真机端到端（需用户在场收码）。
- [ ] 分流判据 `uid_identity_verdict` 在「有 profile 登录态但无 .env 凭证」边界下的行为复核。
