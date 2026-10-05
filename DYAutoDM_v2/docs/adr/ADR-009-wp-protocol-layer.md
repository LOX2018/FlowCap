# ADR-009：WP 通道取数层由 JS 注入改为协议层监听

- **状态**：已实施（v0.44.73，2026-09-25）
- **决策人**：LOX（2026-09-25 在 A/B/C 三方案中拍板 **C**）
- **关联**：案例 `工作记忆/cases/2026-09-25_WP通道静默无效根因确证_Camoufox禁JS注入_case_v0.44.72.md`；台账 H-24；知识库 `05e` §24.15

---

## 1. 背景

`main.py` 自 2026-09-05 起拉起 `wp_recv` 轮询循环，注释声称「与 WS 通道并存、应用层去重」。
其唯一取数来源是 `CAP_WP_MESSAGE_HOOK_JS` —— 一个经 `add_init_script` 注入页面的
脚本，改写 `window.fetch / XMLHttpRequest / WebSocket` 截获 IM 数据。

**2026-09-20 v0.44.0（提交 `6c9b09e`）** 接入 Camoufox 内核时，为解决抖音
「安全风险…已阻止此次访问」弹窗（诱因是 JS 注入痕迹），在 `_launch` 内加入：

```python
if self._backend == "camoufox":
    logger.info("内核=Camoufox → 跳过 JS 注入")
```

该分支**跳过全部 init script**。后果（代码注释已自陈、日志实证）：
WP 通道**静默失效 5 天** —— 生产库 `source='wp'` **0 行**、日志
「取回 WP 私信事件」**0 次**、「跳过 JS 注入」**113 次**。
（昵称 hook 同样被跳过，但另有 `CAP_IDB_USERINFO_JS` + `exec_js` 兜底，
故昵称存活、WP 死亡 ⇒ **定向失效**。）

## 2. 问题（为什么必须决断）

**静默无效**是技术债中最坏的一类：无报错、无告警、注释声称有双保险，实际零产出。
且 AI 上下文白名单 `('text','7','27')` 中没有任何一条来自 WP ⇒
该通道对系统**从未贡献过数据**，却持续消耗轮询与（旧实现下）导航成本。

## 3. 备选方案与取舍

| 方案 | 做法 | 取舍 |
|---|---|---|
| **A 显式下线** | 删除 `run_wp_recv_loop` 拉起 + 订正注释，改「WS 单通道」 | 最诚实、零风险；但**放弃**一条本可恢复的冗余通道，且未来若 WS 侧受限无退路 |
| **B 切回 exe 内核** | 使 `_backend != "camoufox"`，恢复 JS 注入 | 一行开关；但**重新暴露风控弹窗** —— 而禁注入正是为解决它 ⇒ **属倒退** |
| **C 协议层监听（选定）** | 用 patchright `context.on("response")` / `page.on("websocket")+framereceived` 被动取数 | 保住 Camoufox 反检测优势（页面零注入），恢复通道；代价是新增一个模块与一门新踩坑 |

**选定 C 的理由**：它是**传输层替换**而非新增能力 —— 数据同源、契约同形，
且让架构回到设计意图（WP 通道本该是 WS 的冗余），同时**消除** JS 注入痕迹
（旧实现唯一的反检测负债）。

## 4. 决策

1. 新增 `daemon/wp_protocol.py`（`WpProtocolListener`），承载协议层取数；
   **只负责取回原始事件，不解析**（解析/落库仍归 `wp_recv`）—— 保持 SoC。
2. `capture_wp_messages()` 返回契约 `[{kind,url,body,ts}]` **逐字段不变** ⇒
   消费方 `wp_recv.process_events` **零改动**（契约先行，换实现不换接口）。
3. 挂载点放在 `_launch` 的**两条分支之后、`goto(chat)` 之前** ——
   两条分支都要挂（协议层与内核无关），且必须在首屏请求前（否则漏 `get_message_by_init`）。
4. legacy JS hook **保留为兜底**（仅协议层挂载失败时启用），不删除 —— 便于回退与对照。
5. URL 过滤判据与旧 `IMAPI_RE` **逐项对齐**（8 路径特征 + 关键字兜底）⇒
   「换传输层不换过滤口径」。

## 5. 后果

**正向**
- WP 通道恢复，且**零页面注入** ⇒ 反检测面优于旧实现
- 顺带消除 `capture_wp_messages` 每 3s「导航到 /chat」的副作用（ENG-020）
- 修掉一个实测新缺陷：Firefox/juggler 的 WS 文本帧 **latin-1 误解码**（`fix_ws_text`）

**负向 / 约束**
- 新增模块与协议层 API 依赖（patchright 版本升级需回归本模块）
- 协议层回调在**创建 context 的同一 asyncio loop** 内执行 ⇒ 不可跨线程调用
- 打包必须补 `--hidden-import wp_protocol`（函数体内延迟导入，PyInstaller 扫不到）

**未决**
- **真实抖音端到端未验**（需登录态 BCC + 真实客户来消息）；
  判据：出现首条 `source='wp'` 落库行 + 日志「取回 WP 私信事件 N 条（协议层）」

## 6. 验证证据（全部真实执行）

| 层 | 证据 |
|---|---|
| 机制最小复现 | 本机 Camoufox 下 `context.on('response')` 命中且体可读；`page.on('websocket')+framereceived` 收帧 |
| 端到端活体 | 真实 Camoufox + 本地仿真 IM 端点 → 协议层 → **真实 `parse_http_init`/`parse_ws_frame`**：HTTP 文本 ✅、WS 中文 ✅、读后清空 ✅ |
| 机械门禁 | `test_wp_protocol.py` **23/23**（含负控：非 IM URL 不命中；已正确中文不被二次破坏） |
| 全量回归 | **853 tests OK** |
| 版本门禁 | `check_version_sync.py 0.44.73` → **6 处齐平**（含 `src-tauri/Cargo.lock`） |
| 铁律门禁 | `check_iron_rules.py` → **6/6 PASS** |

## 7. 回退路径

1. 令 `WpProtocolListener.attach()` 抛错（或临时把 `_wp_proto` 置 `None`）⇒
   `capture_wp_messages` 自动走 legacy JS hook 分支；但注意该分支在 Camoufox 下恒空。
2. 需要真正回退到「有数据」的旧行为，须同时切内核为 `exe`（方案 B）——
   **附带风控弹窗风险**，仅在紧急排障时使用。
