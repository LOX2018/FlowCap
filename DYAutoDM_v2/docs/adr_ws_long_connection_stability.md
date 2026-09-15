# ADR: WS 长连接稳态治理（L0–L3）

- 日期：2026-09-16
- 版本：v0.43.35 → v0.43.36
- 分支：design/better-douyin
- 影响模块：`backend/daemon/recv_daemon.py`、`backend/daemon/ws_link.py`（新增）

## 1. 背景与设计契约

`recv_daemon` 每账号维护一条 `wss://frontier-im.douyin.com/ws/v2` 长连接。

**设计契约**：该连接应 7×24 常驻，仅在凭证失效/网络异常时断连，且断连后
能自动恢复、恢复后补齐掉线期消息。

**实测违约**（design 分支 `logs/recv_daemon_20260915.log`）：
26 次建连 / 9 次关闭，断连间隔**恒定 OPEN +30.0s**。

## 2. 根因（三层溯源，至源码级）

| 层 | 事实 | 证据 |
|---|---|---|
| 终端 | `RECV-006 Connection to remote host was lost` | 日志 |
| 中间 | websocket-client 抛 `WebSocketTimeoutException("ping/pong timed out")` | 本地 mock 复现 |
| 源头 | frontier-im **不回 Pong** → `last_pong_tm` 恒 0 → `_app.py:507` 的 `last_pong_tm - last_ping_tm < 0` 恒成立 → 20+10=30s 必然自杀 | `websocket/_app.py:502-522` |

**实机复现**（本地 mock 服务端收 Ping 不回 Pong，同参数 `ping_interval=20/ping_timeout=10`）：
```
t+  0.0s  OPEN
t+ 30.0s  ERR:WebSocketTimeoutException:ping/pong timed out
```
与抖音日志 `OPEN +30s` 精确吻合 → 充要原因成立。

### 2.1 重要更正：9/07 的修复是错误修复

`工作记忆/09_全局治理冲突台账.md` §第五轮记录「WS 每 ~30s 被服务端掐 →
加 `ping_interval=20/ping_timeout=10` 修复」。**该结论需撤回**：

| | 修复前 | 修复后 |
|---|---|---|
| 机理 | 服务端掐 30s 空闲 | **客户端 30s 定时自杀** |
| 现象 | 30s 断 | 30s 断 |

现象相同、机理已变，属**功能漂移**。注释中「实测不加时每 ~30s 掐一次」
记录的是修复前观测，已随代码移除。

## 3. 决策

新建 `backend/daemon/ws_link.py`，把连接生命周期从 `RecvChannel` 剥离。
`RecvChannel` 只保留「构建 WebSocketApp」+「协议解析」两个职责。

| 层 | 措施 |
|---|---|
| **L0** | 不再传 `ping_interval/ping_timeout`，消除协议层 Ping 自杀 |
| **L1** | 应用层心跳：`PushFrame(payloadType="hb")` 二进制帧，每 15s（对齐 `dy_live/server.py:34-47` 已验证做法） |
| **L2** | 单循环重连 + 指数退避(base 2s, cap 60s) + ±20% jitter；在线满 120s 重置退避。**回调只置状态，绝不重连** |
| **L3** | 重连后按节流（默认 120s）用 HTTP 2043 首包补拉，`INSERT OR IGNORE` 去重 |

配套：
- **看门狗**：120s 无下行帧判半开，主动重连（防 NAT/防火墙静默丢连接）
- **可观测性**：`/status` 增 `link{connects,disconnects,hb_sent,hb_failed,last_rx_age,backoff_stage}`
- **凭证自愈**：`_make_ws` 在每次重连都重新加载凭证（原实现只在首连取一次）

### 关键设计点：回调绑定权归 WSLink

初版让 `make_ws` 自行绑 `cb_open`，实机验证出现 **connects=0 但连接正常** 的
静默故障（漏绑导致退避永不重置）。改为 `WSLink._bind_callbacks(ws)` 主动注入，
并**包裹**（而非覆盖）调用方已有的业务回调。

## 4. 实机验证

### 4.1 单元验证 `backend/daemon/_verify_ws_link.py`
mock 服务端：收 Ping 不回 Pong；第 2 条连接 8s 后被踢。
```
[PASS] L0 未发协议层 Ping（ping=0）
[PASS] L1 应用层 hb 心跳已发出（hb_sent=6）
[PASS] L1 服务端确实收到 hb 帧（hb=6）
[PASS] L0+L1 连接未发生 30s 定时自杀（hb_failed=0）
[PASS] L2 服务端踢线后客户端自动重连（connects=3）
[PASS] L2 心跳无失败
总判定: ALL PASS
```

### 4.2 对照实验 `backend/daemon/_verify_ab.py`
同一 mock 服务端，OLD vs NEW：
```
OLD 存活 30.0s（alive=False）   ← 复现原故障
NEW 存活 100.1s（alive=True）hb_sent=6
总判定: ALL PASS
```

> **踩坑记录**：v2 曾用 `websockets` 库搭 mock，但它**收到 Ping 自动回 Pong**，
> 得出「OLD 也活 100s」的假阳性。必须用手写 socket 才能复现 Ping 无 Pong。

### 4.3 追赶节流
5 次触发、间隔 1s、`CATCHUP_MIN_INTERVAL=2` → 实际执行 3 次（t=0/2/4），符合预期。

## 5. 待实测项（阻塞，如实记录） → **已实测通过（2026-09-16 当晚）**

L1 的 `hb` 帧**是否被私信 frontier-im 接受** —— 已通过真实环境确认：

- **结果**：真实抖音账号（尚进工伤小助理）连接 600s 零断连
  （`connects 1→1`），`hb_sent 2→41` 持续递增、`hb_failed=0`。
  对比修复前同环境日志：26 次建连 / 9 次关闭、恒定 `OPEN+30s`。
- **顺带验证**：`rx_age` 最高达 **481s**（业务低峰长期零下行）而连接完全
  健康 —— 证实「低峰零下行」是正常业务特征，看门狗若只看下行必误杀
  （这正是 hotfix2 引入 TCP peek 判据的原因，见 commit `4bd2c37`）。

**退路未启用**：`hb` 直接被服务端接受，无需退化为 cmd 610 上行帧。

验证命令（已固化）：
```bash
python <design>/_probe_ws_live.py 12726 600
# 断言: connected=True / connects 不增长 / hb_sent 递增 / hb_failed=0
```

## 6. 可调参数（环境变量，不写死）

| 变量 | 默认 | 说明 |
|---|---|---|
| `DY_WS_HB_INTERVAL` | 15 | 应用层心跳间隔(s) |
| `DY_WS_HB_MODE` | hb | hb=发心跳 / off=不发 |
| `DY_WS_DEAD_TIMEOUT` | 120 | 无下行判半开(s) |
| `DY_WS_BACKOFF_BASE` | 2 | 退避基数(s) |
| `DY_WS_BACKOFF_MAX` | 60 | 退避上限(s) |
| `DY_WS_BACKOFF_RESET_AFTER` | 120 | 在线多久重置退避(s) |
| `DY_WS_CATCHUP_MIN_INTERVAL` | 120 | 追赶补拉节流(s) |
