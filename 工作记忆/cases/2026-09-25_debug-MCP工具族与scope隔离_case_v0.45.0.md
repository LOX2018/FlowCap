# 案例：debug 专用 MCP 工具族上线 + scope 隔离（v0.45.0，2026-09-25）

> 性质：**已交付案例**（含未做的诚实标注）
> 关联 ADR：`DYAutoDM_v2/docs/adr/ADR-010-debug-mcp-scope.md`
> 关联台账：H-26（本案例）；上游病根与 H-24（WP 通道静默无效）同型
> 版本：v0.44.73 → **v0.45.0**（minor feature）；提交 `f037997`

---

## 1. 用户诉求（原话）

> 「我发现现有的 debug 模式效率确实太低下了，什么资料，什么情况都要自己查，直接给项目
> 当中做一个 debug 专用的 MCP 吧，这样后续 debug 的时候可以用 MCP 主动告知，到底是
> 哪个环节出了问题，而不是反复的去查资料」

后续补充（本次交付的约束条件）：

> 「风控机制解除，这一点是真的，但开发的时候还是尽量保守策略为主。」
> 「既然这个 MCP 已经规划好了功能的分布，那为什么我在前端页面没有看到关于 MCP 的设置
> 入口，导致我一直都不知道，而且他也没有真正的接入到 Hermes 中」

---

## 2. 设计前的关键发现（推翻了「新建 MCP」这一初始假设）

**侦察结论：项目里已经有一整套 MCP，且能跑。**

| 事实 | 证据 |
|---|---|
| MCP 已存在 | `backend/mcp/`（7 模块 1214 行：config/registry/audit/server/__main__/tools） |
| **机制质量高** | token 世代号（轮换即时失效）/ 审计**绝不落原始参数值** / 只 bind 127.0.0.1 / 写操作双闸 |
| 真实可跑 | stdio `initialize` + `tools/list` 实测返回 **22 个工具** |
| debug 类工具 | **0 个**（22 个里没有任何 probe/debug/trace） |
| 调试语义地基均已存在 | `errcode_data.py` 370 码 × 六段（design/contract/deviation/chain/root/verify）；`services/probe.py` 7 探针 × 四态；`工作记忆/03_采集链路总览.md` 五链 + 9 层 |

⇒ 正确定性：**给既有 MCP 加一个 debug 工具族**，不是新建 MCP。ADR-010 据此选定「方案 A：复用机制 + scope 隔离」。

---

## 3. 根因：三个互相掩盖的缺失

用户问「为什么前端没有入口 / 为什么没接入 Hermes」，逐层取证：

| # | 缺失 | 判据 |
|---|---|---|
| 1 | **发现性** | 前端 `settings-page.tsx` 定义 9 个 tab，**无 MCP**；全前端 `grep mcp` **零引用** |
| 2 | **可达性** | Hermes `config.yaml` 的 `mcp_servers` 只有 llm-wiki + github；且 MCP 设计文档（`logic_replication_plan.md` D4）的客户端清单写的是「Codex / Claude Code / OpenClaw」，**不含 Hermes** |
| 3 | **默认关闭** | `enabled=False`（默认），端口 39144 未监听 |

**为什么会缺前端**（复刻计划的验收缺项）：

- `replication_plan.md` §二「子系统 1：MCP 层」只列 **6 项，全是后端**；
- 对标表只列**两个 Rust 文件**（`mcp.rs` + `douyin-dl.rs`）——源项目的前端 `settings-mcp.tsx` **从未进入复刻清单**；
- **验收标准 §五 #1** 全文是「`python -m backend.mcp serve` 可启动；Bearer 校验生效；只读工具免确认…」—— **全是后端判据，没有一条问「用户能否看见并使用」**。

⇒ **验收标准缺项**：只交付了能力层，丢了呈现层，却判为"复刻完成"。

**系统性含义（比 MCP 本身更值钱）**：这与 H-24（WP 通道"代码在跑、零落库"潜伏 5 天）是**同一个病** ——
**代码存在、能跑、但零产出 / 零可达，且无人知晓**。`Live-Instance Verification Law`（动态优先于静态）
被违反两次，因为验收只覆盖到「代码能跑」，未覆盖「用户能用到」。

---

## 4. 交付内容

### 4.1 新增 `backend/mcp/tools_debug.py`（7 工具）

| 工具 | 级 | 作用 |
|---|---|---|
| `debug_overview` | READ | 一屏总览：版本源齐平 / 账号 / 各链最差状态 / 近 1h 高频码 |
| `debug_chain_status` | READ | 五链逐级状态（复用 7 探针），恒 L0→Ln 顺序 |
| **`debug_why`** | READ | ★**上游优先定位【首个】失败环节** + 上游已过清单 + 下一步 |
| `debug_log_digest` | READ | 近 N 分钟错误码聚合（频次/首末时间/样本）+ 关联契约三段 |
| `debug_explain` | READ | 错误码六段（口径同既有 `lookup_errcode`） |
| `debug_data_health` | READ | 落库质量量化（H-25 口径）+ 来源通道构成 |
| `debug_trigger_capture` | **WRITE** | 【唯一有副作用】受控触发真实「更新会话」（复用既有 refresh 路径） |

### 4.2 依赖序定位（`debug_why` 的判定核心）

层级源自 `03_采集链路总览.md`，且**从 `services/probe.py` 派生**（不另抄一份）：

```
L0 kernel_availability   前置：内核不可用 ⇒ 全链必死
L1 credential_identity   C4 支撑链：「C4 失效会以 C1/C2/C3 静默失效的形式表现
                          —— 这正是过去最常见误判的来源」（文档自陈）
L2 conversation_capture … L6 ai_lead_capture
```

算法：`for layer in L0..L6: if state != healthy: return first_bad=layer`（**命中即停，不向下游找**）。

### 4.3 scope 隔离（保守策略的落点）

- `Tool.scopes` 默认 `("full",)` ⇒ 既有 22 工具**逐字不变**（零回归）。
- debug 工具 `scopes=("debug",)` ⇒ **默认面完全不含**它。
- `--scope=debug` 入口 ⇒ 15 个主动查询平台工具（`search_user` / `user_info_batch` …）**物理不可达**。
- **未知 scope 拒绝启动**（S5）—— 绝不静默回落全量，否则隔离形同虚设。

### 4.4 接线

- `tools.py::register_all()` 追加注册 debug 族（失败不影响既有工具）。
- `__main__.py` 新增 `--scope`（支持 `--scope=x` 与 `--scope x`；未知值 EXIT=2）。
- 订正 2 处 doc-rot：`mcp/__init__.py` 与 `mcp/registry.py` 的「本项目不注册任何主动查询昵称的工具」
  —— 与 `tools.py` 实测注册 22 个（含 `search_user`/`user_info_batch`）矛盾；
  实际 2026-09-14 用户已授权「全解除」（留痕 `docs/BRANCH_RULES_design_better_douyin.md`）。

---

## 5. 验证证据（全部真实执行）

| 判据 | 结果 |
|---|---|
| V1 scope=debug 隔离 | **恰 7 个**；`search_user`/`user_info_batch`/`recommend_feed` **零泄漏** ✅ |
| V2 未知 scope | `--scope=bogus` → 明确报错 + **EXIT=2**（不回落） ✅ |
| V3 **依赖序（核心）** | 凭证与捕获**同时失败** → 报 **`credential_identity`**（非 capture）；且内核失败时压过一切 ✅ |
| V4 数据不足 | 无证据不报 healthy（I1）；无数据/目录缺失 → `unknown`（I2） ✅ |
| V5 参数夹紧 | `since_min=1e9` → 夹到 10080 ✅ |
| **真实调用** | 6 工具全 `ok=True`，打**真实生产库**：总 763 / 白名单 734 / base64 图片 25 条(102,913 字符) / 噪音 58 / msg_id 空 47 / extra 空 39 / 重复 msg_id 0 —— **与手工探测逐项吻合** ✅ |
| 性能 | `debug_data_health` 28ms；`debug_log_digest` 扫 303,766 行 242ms ✅ |
| 门禁 | `test_debug_mcp.py` **19/19**（含 3 条依赖序负控） ✅ |
| 全量回归 | **872 tests OK**（853 → 872） ✅ |
| 版本门禁 | **6 处齐平 0.45.0** ✅ |
| 铁律门禁 | **6/6** ✅ |
| Hermes 接入 | `hermes mcp test dyautodm-debug` → **✓ Connected (1235ms) · ✓ Tools discovered: 7**（EXIT=0） ✅ |

---

## 6. 实施中活体抓出并修复的自身缺陷（2 处）

1. **`version_sync` 恒 False**：`_version_sources()` 用了 `probe._app_root()`（= **数据根**），
   而 6 处版本源在**源码树**。修法：新增 `_project_root()`（`__file__.parents[2]`，并校验
   含 `package.json`/`src-tauri`），提取口径对齐 `scripts/check_version_sync.py` 的 TARGETS。
   ⇒ 修后 6/6 齐平。

2. **6 处 loguru printf 风格**（`logger.debug("... %s", x)`）：被项目门禁
   `test_no_loguru_printf_style` **拦下**（loguru 会静默丢弃参数）。改为 f-string。
   ⇒ **门禁起作用了**，这是它该干的事。

3. 🔴 **接线事故（已澄清，记录以免重犯）**：调试期在 shell 里 `export PYTHONPATH=<backend>`，
   该变量**持续留在会话中**并传给后续 `hermes mcp test` 的**父进程** ⇒ 使 Hermes 自身
   `tools/mcp_tool.py` 的 `import mcp` 解析到**项目的 `backend/mcp/` 包**，
   报 `module 'tools.mcp_tool' has no attribute 'StdioServerParameters'`。
   - **性质**：调试期**父进程环境污染**，**不是** Hermes 缺陷，**也不是** config 问题。
   - **判据**：`unset PYTHONPATH` 后 `hermes mcp test` 立即 `✓ Connected`。
   - **结论**：config 里的 `env.PYTHONPATH` **只作用于子进程**，是**正确且必要**的
     （Hermes 的 stdio 配置无 `cwd` 项）；错误只在于它泄漏进了父 shell。

---

## 7. Hermes 接入方式（最终形态）

```yaml
mcp_servers:
  dyautodm-debug:
    command: C:/Users/LOX/AppData/Local/Programs/Python/Python314/python.exe
    args: [-m, mcp, serve, --scope=debug]
    env:
      PYTHONPATH: C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend
      DY_APP_ROOT: C:/temp/dyautodm_design
      PYTHONIOENCODING: utf-8
    connect_timeout: 60
    enabled: true
```

- 工具将注册为 `mcp_dyautodm_debug_*`（新会话生效，无热重载）。
- **为什么走 stdio 而不是 HTTP**：跳过 Bearer 令牌管理、端口占用、前端设置页三块复杂度与
  暴露面 —— 直接服务「保守策略」基调。前端设置页仍有价值（给人/给其他客户端），
  但**不是** Hermes 接入的前置条件；二者当初被绑成一件，才导致"不做前端 = 整件事没发生"。

---

## 8. 未做（诚实标注）

1. **打包态验证**：当前仅**源码态**验证。打包后 `PYTHONPATH` 需指向 `_internal` 或项目根，
   且 `tools_debug.py` 的函数体内延迟导入需确认被 PyInstaller 收集。
2. **ADR-010 的 V10 实机端到端**：需**真实业务异常**来产生 → 观察 `debug_why` 结论
   与 `POST /api/probe/run` 是否一致（当前只有构造态负控 + 真实数据读数）。
3. **前端 MCP 设置页**：独立事项，可延后。

---

## 9. 可复用判据（教训）

1. **「代码在位」≠「能力可用」** —— 新增能力必须同时过**发现性 / 可达性 / 默认状态**三关，
   并写成可复跑的验收判据（H-24 与本案同型，已两次）。
2. **验收标准必须包含「用户视角」条目**，否则后端达标即被判"完成"，前端与接入永远不会被计为缺口。
3. **新增能力的默认姿态 = 保守**：优先零风控面实现；工具/接口面**按需切分**（scope），
   不因"已授权"就把全量交出去。
4. **调试期环境变量会泄漏并污染父进程**：`export` 是会话级的；验证 Hermes 接入前先 `unset`。
5. **依赖序定位必须机械化**：把"按经验猜排查顺序"变成代码，才能真正消除
   「C4 故障被误判为 C1/C2/C3」这类最高频误判。
