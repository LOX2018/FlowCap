# ADR: 会话身份解析单一真相源 + 通用性去硬编码（v0.43.39）

- **日期**：2026-09-16
- **分支**：design/better-douyin
- **版本**：v0.43.38 → v0.43.39
- **范围**：接收/发送全链路的会话身份解析收敛 + 全项目去除本机具体账号/uid 硬编码

---

## 1. 背景与设计意图

### 1.1 原设计契约（被破坏前）
「从 conv_id `X:Y:uidA:uidB` 解析真实对端 uid、排除本账号、拒绝发给自己」
是一条**单一语义的规则**，本应只有一处实现。

### 1.2 实测偏差
改前该规则在**8 处**独立重写，且判定强度不一致：

| # | 位置 | 实现方式 |
|---|---|---|
| 1-3 | `daemon/recv_daemon.py`（/send、/send_by_uid、/send_image） | `uid_probe.get_uid`（探活链路） |
| 4-5 | `auto_dm/conversation_capture.py`（存量订正、脏数据修复） | 内联 Counter 统计 |
| 6 | `services/dm_dispatch.py::ConvPool._my_uid_of` | 会话池统计 |
| 7 | `daemon/wp_recv.py::_my_uid_of` | `auth.get_uid()`（凭证值） |
| 8 | `services/uid_probe.py::_session_uid_of` | 会话池统计（另一份） |

**后果（真实故障面，非代码风格问题）**：
- 同一账号存在多套 `my_uid` 来源 ⇒ 一处判「对端=自己」拒发、另一处判「可以发」。
- 修复不同步（`conversation_capture` 的「张冠李戴」修复只落在自己那份）。
- 规则变更需人工同步 8 处，漏一处即隐性 bug。

### 1.3 通用性缺陷（本次一并修复）
本软件为**通用产品**，但代码/注释/脚本中大量写死某台机器的账号名与 uid：

- `daemon/browser_daemon_js.py:234`：**功能代码**把某 uid 硬编码为 IndexedDB 兜底库名
  ⇒ 其他用户浏览器不支持 `indexedDB.databases()` 时，永远读不到昵称。
- `scripts/build_sidecar.py`：调试白名单写死两个账号的 uid 映射。
- `scripts/verify_*.py`、`scripts/probe_img_*.py`：写死账号名 / conv_id / 主分支路径。
- 多处 docstring 以本机具体账号叙述（应改为描述**方法**）。

---

## 2. 决策

### 2.1 建单一真相源模块 `backend/services/conv_identity.py`
对外 API：
- `my_uid(account, fresh=False)` — 本账号 uid（权威：会话池统计；回退探活；60s 缓存）
- `peer_uid(conv_id, my)` — 解析对端（纯函数）
- `correct_peer_id(account, conv_id, claimed)` — 订正 + 是否变更
- `self_send_error(account, peer_id)` — 拒绝发给自己（唯一实现）
- `infer_my_uid_from_conv_ids(cids)` — 从任意 conv_id 序列推断（纯函数，供无法访问 DB 的进程用）

**判据（唯一）**：本账号 uid 出现在该账号**每一条** conv_id 的**固定位置**。
- 覆盖度 ≥ 90%，**且**位置稳定（只出现在 idx=2 或 idx=3 之一）。
- 双重判据原因：会话总数很少时，脏数据也能凑出高覆盖。

### 2.2 8 处调用方零条件改调共享实现
保留原方法名做薄包装（避免破坏外部调用），内部委托到 `conv_identity`。

**行为升级**：`recv_daemon` 三处从「探活 uid」升级为「会话池统计 uid」（判定强度对齐）。

### 2.3 去硬编码
- 功能代码：运行时取本账号 uid 传入（`browser_daemon.py` → JS `myUid` 参数）。
- 调试白名单：改从环境变量 `DY_DM_TEST_WHITELIST` 读。
- 验证/探针脚本：账号走 `DY_TEST_ACCOUNT`、路径走 `FLOWCAP_APP_ROOT` / `DY_REPO_ROOT`，缺省明确报错。
- docstring：改为描述方法，不再写具体账号。

---

## 3. 实机验证

### 3.1 验证脚本 `backend/daemon/_verify_conv_identity.py`
**源码零硬编码**（账号走环境变量），16 项断言全 PASS：

```
=== [1] peer_uid 纯函数规则（合成数据）=== 6 PASS
=== [2] infer_my_uid_from_conv_ids 纯函数 === 4 PASS
=== [3] correct_peer_id / self_send_error === 2 PASS
=== [4] 实机段 === 4 PASS（推断 uid 非空 / 对端≠自己 / 拒绝发给自己）
```

关键实机断言：会话池统计正确推断出本账号 uid；对端解析结果恒不等于本账号。

### 3.2 编译
改动涉及的 15 个 `.py` 全部 `py_compile` 通过。

### 3.3 通用性
`grep` 全项目可执行代码：本机具体账号名/uid **零残留**（注释中的历史事故档案亦已通用化）。

---

## 4. 已知边界与后续

1. **`uid_probe.session_uid` 保留自有缓存**（TTL 300s），与 `conv_identity.my_uid`
   的 60s 缓存并存 —— 二者语义不同（前者为兼容旧调用方）。后续可考虑统一。
2. **P1（发送闸门单一裁决点）未做**：`AccountQuota`（陌生人首发）与
   `recv_daemon` 闸门（最小间隔）仍是两层独立计数。需先定「配额权威归属」再动手。
3. **P2（接收侧事件总线）未做**：当前 3 个消费方各自 5s 轮询 DB，压力可接受，暂缓。

---

## 5. 审计要点（供后续 review）

- 新增 `services/conv_identity.py` 必须加入 sidecar 打包的 hidden-import（同 `ws_link`）。
- 任何新增「解析对端 uid / 判定本账号」的代码，**必须先查 `conv_identity` 是否已有**，
  禁止再写第 9 处。
- `_infer_from_conv_pool` 的双重判据（覆盖度 + 位置稳定）不可退化为只看覆盖度。


---

# 附录：发送闸门统一 + 缓存统一（v0.43.40）

## 决策

### A. 发送闸门：调度器直接仲裁（用户 2026-09-16 定调）
改前发送频率有**两个互不感知的仲裁点**：
- `DmDispatcher.AccountQuota`（陌生人首发 / 冷静期 / 权重）— backend 进程
- `recv_daemon._send_gate_acquire`（per-account 最小间隔）— recv_daemon 进程

两者各记各的 ⇒ 理论上可叠加出水。

**改法**：
- `AccountQuota` 新增 `can_send(min_interval)` / `note_sent()` —— 把「最小间隔」
  纳入与陌生人首发**同一把锁**的裁决，成为**唯一仲裁点**。
  （新增 `_last_sent_at` 字段，原该时间戳只在 recv_daemon 进程内维护。）
- `DmDispatcher._send_one()` 出队时先 `can_send()`；被拦且等待 ≤ `SEND_WAIT_MAX`
  （默认 10s）则原地等待重试一次，否则快速失败并归还预占额度。
- `recv_daemon._send_gate_acquire` **降级为物理兜底**：阈值 = 配置值的 50%，
  下限 2s。保留理由：`/send` 等是 HTTP 端点，可能被**绕过调度器直接调用**
  （如 `core/sender.py` 直发、手工 curl、其它进程），无此闸门则完全失控。

### B. 缓存统一
新建 `services/ttl_cache.py::TTLCache`（进程内 TTL 缓存**单一原语**）。

- `conv_identity` 改用它（TTL 60s，可用 `DY_MY_UID_TTL` 覆盖）。
- `uid_probe.session_uid` 改**委托** `conv_identity.my_uid` ——
  消除"同一事实两个新鲜度"（原 300s vs 60s）。`.py` 中 `_valid_session` 已删。
- `uid_probe.invalidate()` **联动清理** `conv_identity` 缓存 ——
  否则出现「uid 已刷新但 session_uid 仍返回旧值」的不一致窗口。

**TTL 取 60s 的理由**（而非 300s）：uid 推断是**零网络**的本地计算，
快照更新无成本，更短 TTL 意味着换号/迁移后更快自愈。

## 验证

`backend/daemon/_verify_send_gate_cache.py`（18 项全 PASS）：
- TTLCache 原语 10 项（set/get/过期/负缓存/prefix失效/prune/snapshot）
- 缓存统一 3 项（无 _valid_session / session_uid==my_uid / invalidate 联动）
- 调度器仲裁 4 项（首次放行 / note_sent 后拦 / 剩余等待>0 / 0 不拦）
- 兜底闸门 1 项（宽于配置值）

回归：`_verify_conv_identity.py` ALL PASS；`scripts/verify_isolation.py` PASS=10 FAIL=0。

## 遗留
- `services/media_proxy.py` / `daemon/browser_daemon.py` 仍各有自维护缓存 ——
  可按 `ttl_cache` 逐步替换（非阻塞，属技术债清理）。
