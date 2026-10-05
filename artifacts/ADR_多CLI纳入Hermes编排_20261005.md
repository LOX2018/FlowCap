# ADR · 多 CLI 纳入 Hermes 统一编排（唯一入口）

- **状态**：Implemented（P0–P3 + P5 全链路已验证；P4 worktree 生命周期待补；**须重启 Hermes 后注入生效**）
- **日期**：2026-10-05
- **决策者**：LOX（用户）/ Hermes（记录与实施）
- **依据**：SOUL.md 十条铁律 + `external-agent-delegation` + `multi-session-collaboration` + `opencode-driven-development`
- **落盘**：`artifacts/`（未提交；工作区存在并发写者，仅新增本文件）

---

## 1. 背景与目标

用户希望把本机多个编码 CLI 全部纳入 Hermes 管理，**以 Hermes 为唯一入口**共同承担
项目开发职责，为各自分配角色，并在遇到相关问题时**由 Hermes 自动分派子代理**、
监督其工作、验收其成果——**不需要用户手动强调**。

## 2. 本机实测事实（活实例取证，2026-10-05）

| CLI | 路径 | 版本 | 无头通道 | 实测 |
|---|---|---|---|---|
| opencode | `%APPDATA%\npm\opencode.CMD` | 1.18.34 | Hermes 插件 `opencode` 工具 | ✅ 已接入并启用 |
| qoderclicn | `C:\Users\LOX\.qoder-cn\bin\qoderclicn\qoderclicn.EXE` | 1.1.65 | `-p` / `--permission-mode` / `--worktree` / `--resume` | ✅ `-p` 无头跑通（exit 0） |
| codex | `%LOCALAPPDATA%\codex-cli-ultra\bin\codex.CMD` | 启动器 0.157.1 / 运行时 0.160.0 | `codex exec` / `codex review --uncommitted` / `-s read-only` | ✅ **实测跑通**（exit 0，`CODEX_PROBE_OK`）；**驱动须关 stdin** |

**codex 通道更正（2026-10-05，实测根因）**：codex **完全可用**。后台探测返回
`CODEX_PROBE_OK`、`EXIT=0`、provider=freellmapi。首次"挂死"的**真因是 Hermes 侧探针缺陷**：
codex 检测到 stdin 是**未关闭的管道**，打印 `Reading additional input from stdin...` 后
**阻塞等待 stdin EOF**；旧探针只重定向了 stdout/stderr，**没有关 stdin** ⇒ 永久阻塞。
⇒ **驱动 codex（及任何会读 stdin 的 CLI）必须显式关掉 stdin**（`stdin=DEVNULL` 或关闭管道），
并把探针放进**后台/长时**通道。`login status`=Not logged in 不代表故障（它走自定义 provider，
不走官方 OAuth）。附：运行时自报 **v0.160.0**（启动器 `--version`=0.157.1，存在版本漂移），
并有 `Model metadata for 'auto:lox' not found → fallback` 告警。
qoder 默认通道同为本地聚合代理 `qoder2api`。**两者"大脑"均为本机聚合网关，非云端官方端点。**

## 3. 设计期识别的风险（必须先处置）

| # | 风险 | 级别 | 处置 |
|---|---|---|---|
| R1 | **铁律真空**：现有 OpenCode 委派律只覆盖「只读委派」，无「执行子代理可写」范畴 | 🔴 | P3 补 SOUL.md 铁律后再接执行者 |
| R2 | **单写者律冲突**：多 CLI 同写一棵树 = last-writer-wins | 🔴 | 执行子代理各给隔离 worktree；Hermes 独占合并 |
| R3 | **验收假绿**：子代理自述≠证据（已有 2/3 退化 + 误报反例） | 🟡 | 独立验收层 + 负控 |
| R4 | **Hermes 侧探针缺陷：未关 stdin ⇒ codex 阻塞等 stdin EOF**（codex 本体可用，已实测 exit 0） | 🟡 | 驱动外部 CLI 必须显式关 stdin；探针走后台长时通道 |
| R5 | **模型/provider 账本不统一**（31415 / qoder2api / 插件硬编码链） | 🔴 | 建立统一模型路由账本（后续 ADR） |

## 4. 决策（用户 2026-10-05 拍板）

| 决策项 | 选定 |
|---|---|
| 执行子代理写权限 | **隔离 worktree 内可写，Hermes 独占合并/提交** |
| 首期接入范围 | **先接 qoderclicn + 固化 opencode 角色；codex 列 P0 修复项** |
| 本轮交付形态 | **先出 skill 草稿 + artifacts，用户审后再改 SOUL.md** |

## 5. 角色分配（最终）

- **Hermes**：唯一入口 / 仲裁者 / 唯一写者与提交者
- **opencode**：只读项目经理（审计·架构·调研·探索）——**角色不变**
- **qoderclicn**：执行子代理（隔离 worktree 内可写）
- **codex**：独立评审官（只读 `codex review`）——**通道可用，待 P0 复测确认响应耗时**
- **Hermes delegate_task**：可选并行只读取证（禁止冒充外部代理）

## 6. 落地路径

| 阶段 | 动作 | 验收判据 |
|---|---|---|
| P0 | ✅ **已完成**：codex 无头通道实测跑通（exit 0，返回 CODEX_PROBE_OK） | 驱动时**必须关 stdin**；记录首次响应耗时 |
| P1 | ✅ **已完成**：`multi-cli-orchestration` skill 已建并入 `skills.auto_load` | 加载器验证 ALL_GREEN（loaded 含它、missing 空、正文命中） |
| P2 | ✅ **已完成**：`cli-bridge` 插件（`cli_bridge` 工具：qoder_run/codex_review/codex_exec） | `plugins list`=enabled user 1.0.0；doctor=1 tool；实测 exit 0；4 条负控全红 |
| P3 | ✅ **已完成**：SOUL.md 新增「多 CLI 执行子代理律」 | `load_soul_md()` 命中新铁律、尾部小节仍在（未截断） |
| P4 | 并发隔离：`qoder_run` 的 `directory` 必填=隔离边界（已内置）；worktree 生命周期待补 | 无同树双写者；`git worktree list` 可审计 |
| P5 | 验收门禁（独立复核 + 负控） | 负控能让门禁变红 |

### P5 · 全链路活实例验证（2026-10-05，靶场 `mco_smoke`）

四跳全部实跑，并**顺带查出 3 个真实缺陷**：

| 跳 | 执行者 | 结果 |
|---|---|---|
| 1 审计 | opencode（只读） | ✅ 拿到实现计划（修复后） |
| 2 实现 | qoderclicn（隔离目录） | ✅ 落盘 `slugify.py`+`test_slugify.py`；**Hermes 独立复跑 pytest = 6 passed** |
| 3 复核 | codex（只读） | ✅ verdict "Correct — meets all requirements" |
| 4 提交 | Hermes（唯一写者） | ✅ commit `07e904b` |

**查出并已修的 3 个缺陷**：
1. 🔴 **opencode 插件未关 stdin** → stdin 为打开管道（Electron 条件）时挂死零输出。修 7 处 `subprocess`，插件 **v1.0.0→v1.0.1**（commit `13dd532`）。⚠️ **该插件是 `profiles/lox/plugins/opencode` → `hermes-home/plugins/opencode` 的符号链接，属共享插件，修复同时影响 default profile（纯改进）。**
2. 🔴 **qoder `permission_mode` 默认错**：`dont_ask` 直接拒写 → 改 `bypass_permissions`（安全靠隔离 worktree）。
3. 🔴 **`codex review` 的 prompt 与 `--uncommitted/--base` 互斥** → `cli_bridge` 已按互斥实现。

## 7. 关键约束（不可违反）

1. 执行子代理**永不允许**任何 git 写操作；提交由 Hermes 独占。
2. 审计者与执行者**必须分离**。
3. 同树并发写 = 禁止；并发前提是目录隔离。
4. `skills.auto_load` / plugin 生命周期**只解析一次** ⇒ 变更需**重启 Hermes**。
5. 「代理说我完成了」不可采信；验收必须有真实产物/测试读数。

## 8. 未决 / 待用户确认

- codex 的**首次响应耗时**需复测（冷启动 + 网关首跳慢可能导致长延迟，非故障）；
- R5 统一模型账本是否单独立项（另一份 ADR）。
- 本 skill 是否同时推广到 wx-llm / yingyue 两个 profile（当前仅 lox）。
