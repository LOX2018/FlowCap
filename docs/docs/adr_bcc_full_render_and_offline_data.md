# ADR: BCC 完整渲染实测 —— 入口崩溃根因、离线数据边界、租约/假成功两缺陷

> 分支：`design/better-douyin` · 环境：`C:\temp\flowcap_design`（隔离，未碰主分支）
> 日期：2026-09-15 · 方法：实机（起唯一 BCC + 平滑滚动点击 + 渲染前后磁盘对比）
> 状态：**发现待修复**（尚未改码；改码需 +0.01 升版本 + 重打 sidecar）

---

## 一、设计意图（回归设计理念）

| 模块 | 设计契约 | 预期行为 |
|---|---|---|
| `daemon/browser_daemon.py` | 作为 **sidecar 入口**可独立启动（`build_sidecar.py` 的 entry） | 双击/被 spawn 即起 HTTP + 浏览器容器 |
| `capture_userinfo_map` | 平滑滚动 + 逐个点击，触发懒加载渲染全部会话 | 覆盖全部会话（非仅首屏） |
| BCC 租约 | 调用方按用途申请；用户显式动作豁免（PURPOSE_USER） | 长捕获（>180s）不被中途回收 |
| 捕获结果 | 「抓到多少」= 「缓存里就是多少」 | 失败时显式失败，不覆盖既有缓存 |

**假设**：源码与入口脚本的导入方式一致（同一模块）；渲染后数据会进入可读存储。

---

## 二、观察偏差（observed_deviation）

### 偏差 1（阻塞级 · fatal）：BCC 入口模式必崩

```
# 部署产物 C:\temp\flowcap_design\flowcap-browser-daemon-*.exe 实跑：
File "browser_daemon.py", line 327, in <module>
ImportError: attempted relative import with no known parent package
[PYI-12136:ERROR] Failed to execute script 'browser_daemon' due to unhandled exception!
```

`deviation_type`: resource（模块不可用）
`deviation_point`: `backend/daemon/browser_daemon.py:327`
`deviation_from_expectation`: 预期 sidecar 可启动；实际启动即 ImportError，**BCC 完全不可用**。

### 偏差 2（error）：租约 TTL < 捕获时长 → 中途强制回收

```
BCC-048 | capture_userinfo 申请 ttl=300s 超过 prio=1（业务自动）上限 180s，已按上限授予
BCC-046 | capture_userinfo（prio=1 业务自动）租约超时 180s 未释放，强制回收
实测: 昵称捕获完成 83 个，总耗时 265.7s   ← 远大于 180s
```

### 偏差 3（error）：缓存被不完整结果覆盖（假成功结构）

```
第一次: DOM累计昵称 15→26→38→50→61→73→83，最终 83 个（265.7s）
BCC-022: 页面级登录态失效（conv=None rel=None）
第二次: 滚动轮次 1 → DOM累计昵称=15 → 昵称捕获完成：15 个（249.0s，已缓存）
```

后续调用（登录态已失效）只渲染首屏 15 个，却**照单写入缓存**，把 83 个覆盖成 15 个。
「流程走完了」≠「结果正确」。

---

## 三、链路溯源（源头 → 传播 → 终端）

### 偏差 1 链路

```
源头: commit c31253d「注入脚本下沉」——把 JS 常量抽到 daemon/browser_daemon_js.py，
      主文件改用【相对导入】 from .browser_daemon_js import (...)
传播: build_sidecar.py:404 build_one("daemon/browser_daemon.py", "flowcap-browser-daemon")
      → 该文件被当作【PyInstaller 入口脚本】，入口模块 __package__ 为空
终端: 入口执行到 327 行 → ImportError: attempted relative import with no known parent package
```

**该 commit 的验证为何没拦住**：它的「实机验证」只做了
`import browser_daemon_js`（**模块模式**）+ md5/ast 检查，
**没有跑入口模式 / 没有跑 exe**。模块模式（有父包）与入口模式（无父包）导入语义不同。

**旁证**：同仓其它 sidecar 入口（`daemon/recv_daemon.py`、`main.py`）**均无相对导入**，
只有 `browser_daemon.py` 有且仅有一处（327 行）。

### 偏差 2 链路

```
源头: capture_all / BCC 端 capture_userinfo 走 prio=1（业务自动，ttl 上限 180s）
传播: 实测捕获 265.7s > 180s
终端: BCC-046 强制回收 → 后续端点被自己刚拿的租约挡住（知识库 §〇·戊「自我死锁」同族）
```

### 偏差 3 链路

```
源头: BCC-022 页面级登录态失效（observe 模式：不弹窗、等用户激活）
传播: 列表只渲染首屏 15 项，滚动无新增（_stall 判据 3 轮即 break）
终端: capture 仍返回 ok=True + 15 条，且【无条件覆盖缓存】
```

---

## 四、根因分析

| 偏差 | 根因 | 为什么链路在此断裂 |
|---|---|---|
| 1 | **相对导入被用作入口脚本** | PyInstaller 以入口模块身份执行 `__name__=="__main__"`、`__package__` 为空 ⇒ 相对导入无解析基准 |
| 2 | **租约上限按「自动」用途授予**，但该调用是用户触发的长任务 | 用途（purpose）与实际时长不匹配；上限 180s < 实测 265.7s |
| 3 | **缺少「结果有效性」判据**：捕获返回条数远低于历史基线时仍写缓存 | 无「登录态失效 ⇒ 显式失败/不覆盖」的守卫 |

---

## 五、实机验证判据（本次实测数据）

### 5.1 滚动点击渲染 —— **有效**（15 → 83）

```
滚动轮次 1: 新点击=15 DOM累计昵称=15(本屏+15) hook=0 用时=10.4s
滚动轮次 2: 新点击=11 DOM累计昵称=26(本屏+11) hook=0 用时=19.0s
滚动轮次 3: 新点击=12 DOM累计昵称=38(本屏+12) hook=0 用时=28.1s
滚动轮次 4: 新点击=12 DOM累计昵称=50(本屏+12) hook=0 用时=36.4s
滚动轮次 5: 新点击=11 DOM累计昵称=61(本屏+11) hook=0 用时=44.7s
滚动轮次 6: 新点击=12 DOM累计昵称=73(本屏+12) hook=0 用时=55.0s
滚动轮次 7: 新点击=9  DOM累计昵称=82(本屏+9)  hook=0 用时=64.0s
DOM 末屏补充：累计昵称=83
昵称捕获完成：83 个，总耗时 265.7s（已缓存）
```

`hook` 恒为 0，**再次证实** `im/user/info` 被动 hook 已失效（抖音改版），DOM 为唯一有效来源。

### 5.2 渲染后 profile 新增数据（磁盘对比）

| 项 | 渲染前（基线） | 渲染后 |
|---|---|---|
| IndexedDB `https_www.douyin.com` 文件 | `000005.ldb 1.22MB` + `000008.ldb 1.09MB` | `000007.log` **922,823 B（新增）** + `000010.ldb 117KB` |
| 新增文件内 `nickname` 字段 | — | **232** |
| 新增文件内 `sec_uid` key | — | **232** |
| 解出真实昵称 | — | 四川工伤-张老师 / 律保标工伤 / 尚进工伤小助理 / 爱心是你的 / 天性gg / 奋斗 … |

⇒ **完整渲染确实把用户信息写进了 IndexedDB（可离线解出）。**

### 5.3 但聊天记录 —— **渲染后仍为 0**

全 profile 遍历（`Default/**`），四种编码：

| 特征 | 渲染后计数 |
|---|---|
| 会话 ID `0:1:uid:uid` | **0** |
| 消息 JSON `aweType\"` | **0** |
| `content_json` | **0** |
| `msg_id` 字段 | **0** |
| imapi 接口（`get_message_by_init` / `get_by_conversation`）的响应缓存 | **0** |

**原因**：聊天正文走 **WS 长连接**（`wss://frontier-im.douyin.com/ws/v2`）→ 前端收进**内存 store** → 仅渲染到 DOM。浏览器持久化只落 HTTP 响应缓存（且首包是 POST+protobuf，Chrome 默认不缓存 POST）、IndexedDB/LocalStorage（仅用户信息/凭证/token）。**渲染不改变这一点。**

---

## 六、错误码报告（结构化）

```json
{
  "design_intent": {
    "module": "daemon/browser_daemon.py + build_sidecar.py",
    "design_contract": "sidecar 入口脚本必须与模块模式导入语义兼容",
    "expected_behavior": "BCC 被 spawn/双击即正常启动 HTTP + 浏览器容器",
    "assumptions": ["入口与模块使用同一份导入写法"]
  },
  "observed_deviation": {
    "deviation_type": "resource",
    "deviation_point": "backend/daemon/browser_daemon.py:327",
    "deviation_from_expectation": "入口模式 ImportError，BCC 完全不可用"
  },
  "error_code": "BCC-IMPORT-327",
  "severity": "fatal",
  "suggested_actions": [
    {"action_id": "A1-import-compat", "automatic": true},
    {"action_id": "A2-purpose-user-ttl", "automatic": true},
    {"action_id": "A3-guard-incomplete-cache", "automatic": true}
  ]
}
```

---

## 七、建议修复（未执行，待批准）

| # | 动作 | 说明 |
|---|---|---|
| A1 | `browser_daemon.py` 改用**双兼容导入**：`try: from .browser_daemon_js import ... except ImportError: from browser_daemon_js import ...` | 同时满足入口模式与模块模式；不破坏任何既有调用 |
| A2 | `capture_userinfo` 端点的 lease 申请改用 **PURPOSE_USER**（prio=0，ttl≤300s） | 捕获实测 265.7s，180s 上限必然被回收 |
| A3 | 捕获结果**有效性守卫**：返回条数 < 历史基线（如 50%）时，显式失败且**不覆盖缓存** | 消除「15 个覆盖 83 个」的假成功 |

**验证方式**（A1）：`python daemon/browser_daemon.py --account x --port 1` 应能进入
「无 .env / 端口占用」等**业务**错误，而**不再是 ImportError**；再跑一次 exe 准入。

**同步要求**：改 `backend/` ⇒ +0.01 升版本（`package.json` / `frontend/package.json` /
`tauri.conf.json` / `Cargo.toml` 四处）+ 重打 3 个 sidecar（`--onedir --debug-whitelist`）。

---

## 八、本轮"负结论"（防后人重走）

1. **聊天记录不落盘**，完整渲染也不落盘 —— 只走 WS → 内存 → DOM。
2. **`im/user/info` hook 已废**（改版后前端不发该请求），DOM 直读是唯一来源。
3. **`Cache_Data` 里按 URL 搜到的 `im/user/info` 多是前端 JS chunk**，不是响应数据；辨型必须看文件头。
4. **IndexedDB 字符串是 V8 序列化（UTF-16LE）**：按 UTF-8 grep 中文必然 0 命中。
