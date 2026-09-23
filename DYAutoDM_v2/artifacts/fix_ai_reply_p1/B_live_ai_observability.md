# P1-2 直播监听 AI 私信文案「静默不生效」—— 可观测性修复报告

- 任务分支：`design/better-douyin`（v0.44.55，工作区干净）
- 修复日期：2026-09-23
- 可改文件范围（严格只改这三个）：
  1. `backend/core/auto_dm.py`
  2. `backend/api/ai.py`
  3. `frontend/src/components/live/live-page.tsx`
- 新增文件：`backend/scripts/verify_live_ai_observability.py`（可复跑验证脚本）
- 报告文件（唯一）：`artifacts/fix_ai_reply_p1/B_live_ai_observability.md`

---

## 1. 缺陷与根因（复述，作为修法依据）

用户现象：直播策略配了私信文本，开启 AI 回复后仍沿用固定文本。

根因（已取证，与父会话一致）：**调度顺序是对的**（`core/dispatch.py:_do_send` 本就是
`gen_dm_message`（AI）优先、空则回落 `pick_dm_message`（词库）），真因在**判定层把 AI 关掉了**：
`core/auto_dm.py::_make_gen_dm_message` 的三道否决 —— `enabled` 为假 / `strict_level == kb_only` /
`"live" not in cfg["scopes"]` —— 任一道命中都 `return None`，调度器随即回落词库。
Agent 的 `scopes` 默认不含 `live`（`services/ai_agent.py:75` 列表缺省 `["dm"]`、
`AgentSection.tsx:93,137` 前端新建默认 `["dm"]`、`api/ai.py` 新建 Agent 时后端不兜底），
且失败时**只写日志甚至不写日志**，UI 完全无感。

## 2. 修法（用户 2026-09-23 拍板：**保守默认值 + UI 强提示**）

### 2.1 保守默认值（未动）

- 新建 Agent 的 `scopes` 缺省**仍是** `["dm"]`，**没有**擅自扩成全选 —— 把 AI 默认接到发送侧是风控风险。
- `api/ai.py` 新建 Agent 仍为 `cfg = incoming`（不后端兜底）。
- `services/ai_agent.py` 列表缺省 `["dm"]` 一行未改（只读文件，未写入）。
- 验收脚本 L4 层专门断言这两条源码契约，防止后续被"顺手放宽"。

### 2.2 判定唯一真源 + 可查询状态（`backend/core/auto_dm.py`）

- **新增模块级状态出口**（插在 `class AutoDM:` 之前，约 91-165 行）：
  - `LIVE_AI_REASON_TEXT`：`reason_code → 面向用户的原因文案` 字典（端点/前端直接展示，不做二次翻译）。
    - `ok` → `已生效`
    - `no_account` → `未生效（原因：缺少监听账号上下文，无法解析 Agent 配置）`
    - `ai_disabled` → `未生效（原因：AI 未启用）`
    - `kb_only` → `未生效（原因：档位为 kb_only，AI 不参与文案生成）`
    - `scope_missing` → `未生效（原因：Agent 作用域未勾选直播监听）`
    - `error` → `未生效（原因：判定异常，已回落词库）`
  - `LIVE_AI_STATE`：模块级状态快照（最后一次接线判定结果，含 `checked_at`/`checked_count`）。
  - `_record_live_ai_state(verdict)`：写入快照（**剔除内部键 `cfg`**，避免 system_prompt/密钥随状态外泄或进 JSON）；
    尽力而为，任何异常都不影响发送链路。
  - `get_live_ai_state()`：返回副本，供只读端点查询。
- **新增 `AutoDM.evaluate_live_ai(account="", agent_id="")`**（约 412-495 行）：把原内联的三道否决
  抽成**可复用判定**，供 ①发送侧接线 ②只读端点 共用同一份逻辑 —— 杜绝「UI 显示已生效、实际仍回落词库」的第二处判定漂移。
  - 不抛异常：判定环节异常一律收敛为 `reason_code="error"`；
  - 只看只读：不写任何配置、不改任何默认值；
  - `agent_id` 分支走 `resolve_config_for`（与 AI 页编辑同款解析），用于"若绑定该 Agent 会不会生效"的预演。
- **`_make_gen_dm_message` 改为「取判定 → 记状态 → 按结果接线/回落」**（约 497-545 行）：
  - 判定结论与回落行为**逐字不变**（返回 `None` 的分支一个不改）；
  - 未生效时补一条 INFO 日志（原实现此处完全静默）；
  - 判定异常仍记 `SEND-039`（errcode 契约未变）。

### 2.3 只读端点（`backend/api/ai.py`）

新增 `GET /api/ai/live_dm_state`（插在 `@router.get("/status")` 之前，约 867 行起）：

| 入参 | 语义 |
|---|---|
| 无参 | 回读引擎**最近一次真实接线判定**快照（`core.auto_dm.get_live_ai_state()`，`source="runtime"`）；一次都没判定过 → `status="unknown"` |
| `account=xxx` | 用**同一真源**实时重判（`source="recomputed"`），不改任何配置 —— 改完 Agent 作用域立刻看结论，不必重启引擎 |
| `agent_id=xxx` | 预演「若绑定该 Agent，直播 AI 文案是否会生效」（`resolve_config_for`，不写任何绑定） |

返回字段：`ok / status / active / reason / reason_code / account / agent_id /
enabled / strict_level / scopes / source / checked_at / checked_count`。
纯只读，不写任何配置、不改默认值；输出剔除内部键 `cfg`。

### 2.4 前端强提示（`frontend/src/components/live/live-page.tsx`）

- 顶层新增 `LiveAiDmState` 类型 + `fetchLiveAiDmState()`（第 13、78-86 行附近）。
- 组件内新增 `useQuery`（第 209-221 行附近）：以「实际用于监听的账号」实时重判，
  `refetchInterval: 5000`（用户在设置页勾上「直播监听」后本页 5s 内翻绿）。
- 在「生效的自动私信配置」Section 内、词库列表上方插入一行提示条（第 816-836 行附近）：
  - 生效：绿色 `Tone` `AI 文案：已生效` + "私信文案由 AI 生成（生成失败时才回落词库）"；
  - 未生效：琥珀色警示条 `AI 文案：未生效（原因：xxx）` + "当前仍发送下方词库里的固定文本。需要 AI 写文案：设置页 Agent 作用域勾选「直播监听」"；
  - 未判定/无账号：灰色 `mute` 态。

---

## 3. 修复前证据（只读探针，未改任何项目文件）

探针脚本：`%TEMP%/probe_live_ai_before.py`（临时文件，不在项目内）。
在**改动前**的源码上直接调用真 `AutoDM._make_gen_dm_message`（monkeypatch Agent/全局配置，零 DB/零浏览器）：

```
DY_APP_ROOT = C:\temp\dyautodm_design_probe
[修复前] core.auto_dm 是否有可查询状态出口 get_live_ai_state： False
[修复前] core.auto_dm 模块级状态字段： （无）

A. scopes=['dm']（缺 live）
   _make_gen_dm_message() -> None（回落词库）
   是否有任何可供 UI 查询的返回值/字段： 无（只有下面这行日志）
   可查询状态快照： 不存在

B. scopes=['dm','live']
   _make_gen_dm_message() -> 已接线(callable)
   可查询状态快照： 不存在

C. enabled=False                       -> None（回落词库）· 无可查询状态
D. strict_level=kb_only                -> None（回落词库）· 无可查询状态

[修复前结论] 三种否决场景对 UI 完全不可见，仅落一行 logger 日志。
```

要点：A/C/D 三个否决分支**连一行 warning 都没落**（只有 B 成功分支有 INFO），
没有任何可供查询的字段 —— UI 只能显示"AI 回复已开启"，与实际"发固定文本"完全脱节。

## 4. 修复后证据（可复跑脚本自打印前后读数）

脚本：`backend/scripts/verify_live_ai_observability.py`
运行：`python backend/scripts/verify_live_ai_observability.py`（自设 `DY_APP_ROOT=C:\temp\dyautodm_design`，零副作用）

```
L3 端点层：GET /api/ai/live_dm_state（真协程直调，不起服务）
  ── 对照读数：scopes 不含 live ──
     active      = False
     reason      = 未生效（原因：Agent 作用域未勾选直播监听）
     reason_code = scope_missing
     source      = recomputed
  [PASS] 端点：不含 live → 未生效（原因：Agent 作用域未勾选直播监听）
  ── 对照读数：scopes 改为含 live ──
     active      = True
     reason      = 已生效
     reason_code = ok
     source      = recomputed
  [PASS] 端点：改为含 live → 已生效

结果：29/29 通过 · 全部通过   (EXIT=0)
```

分层（`L1 判定层 / L2 接线层 / L3 端点层 / L4 默认值层 / L5 源码层`）全部 PASS，其中关键几条：

- L1：四种否决场景各自产出正确的 `reason_code`（`scope_missing` / `ai_disabled` / `kb_only` / `no_account`）+ 非空可展示 `reason`；判定异常收敛为 `error` 且**不抛出**。
- L2：未生效仍 `return None`、生效仍返回 `callable`（**零回归**）；模块级状态可查询且不含内部键 `cfg`。
- L3：端点结论 == 发送侧接线结论（**同一真源，无第二处判定**）；端点只读（调用前后状态不变）；`agent_id` 预演分支可用且不写绑定。
- L4：`services/ai_agent.py` 列表缺省仍是 `["dm"]`；`api/ai.py` 仍是 `cfg = incoming`（保守默认值铁律未被破坏）。
- L5：`live-page.tsx` 含「AI 文案」展示、直连 `/api/ai/live_dm_state`、含生效/未生效两种文案。

回归：既有 `python backend/scripts/verify_ai_live_wiring.py` → **37/37 通过**（EXIT=0），
SEND-038/039/040 错误码契约与派发器协程感知均未受影响。

## 5. 语法检查结果

| 文件 | 检查 | 结果 |
|---|---|---|
| `backend/core/auto_dm.py` | `ast.parse` + `py_compile.compile(doraise=True)` | 通过 |
| `backend/api/ai.py` | 同上 | 通过 |
| `backend/scripts/verify_live_ai_observability.py` | 同上 | 通过 |
| `frontend/src/components/live/live-page.tsx` | `npx tsc --noEmit -p tsconfig.json`（项目既有检查，`strict` + `noUnusedLocals`） | **通过，EXIT=0**（已确认 `live-page.tsx` 在 `--listFiles` 中被编译） |

Python 解释器：`python 3.11.16`。`.py` 改动全部通过 terminal 跑字节级 Python 脚本完成，
未使用 patch 工具做多行嵌套替换。

## 6. 诚实标注（未做 / 有风险 / 需父会话决策）

1. **未做前端真机视觉验证。** 按网页生产策略只做了 `tsc --noEmit` 类型检查与冒烟级契约断言，
   **没有**在浏览器里实际渲染截图确认提示条样式；`Tone` 的 `warn` 态与琥珀色 class 是照既有
   `kit.tsx` 的 `TONE_MAP` 推断的，未视觉核对。
2. **`frontend/src/api/client.ts` 未改（不在可改清单内）—— 需父会话决策。**
   任务要求新端点照既有风格在 `client.ts` 的 AI 段落补封装（如 `aiLiveDmState()`），
   但该文件不在可改文件清单里。当前实现为**退让方案**：`live-page.tsx` 顶层直接
   `import { BACKEND_BASE } from "../../api/sidecar"` 后用原生 `fetch` 拼
   `${BACKEND_BASE}/api/ai/live_dm_state?account=...`（与 `client.ts` 同源同源端口，不写死端口）。
   差异：**这一处不走 `client.ts` 的 `request` 封装**，因此少了 `ensureBackendReady()`
   等待、`X-App-Version` / `X-Member-Token` 头与统一错误处理。
   **建议父会话在 `client.ts` AI 段落补：**
   ```ts
   async aiLiveDmState(account = "", agentId = ""): Promise<LiveAiDmState> {
     const q = `?account=${encodeURIComponent(account)}&agent_id=${encodeURIComponent(agentId)}`;
     return request(`/api/ai/live_dm_state${q}`);
   }
   ```
   然后把 `live-page.tsx` 里的 `fetchLiveAiDmState` 一行切回 `api.aiLiveDmState(...)`（类型与调用点已就位）。
3. **端点未做 HTTP 真起服务验证。** 验证脚本用 `asyncio.run(live_dm_state(...))` 直调真协程
   （不起 uvicorn、不占端口、不依赖 DB），因此**没有**端到端 HTTP 层（路由挂载/鉴权中间件）的实证；
   路由挂载点 `backend/main.py:783 app.include_router(ai_api.router, prefix="/api/ai")` 已确认存在，
   端点路径为 `/api/ai/live_dm_state`，但 HTTP 往返未实测。
4. **多账号并发的观测精度有限。** 模块级 `LIVE_AI_STATE` 只保留**最后一次**判定（记录 `account`/`agent_id` 防张冠李戴）；
   无参调用在多账号同时运行时读到的是"最后判定的那个账号"。前端走的是 `account=` 实时重判分支，不受此限制。
5. **未触碰发送侧风控闸门。** `core/dispatch.py` 与 `services/dm_dispatch.py` 全文只读，未写入
   （脚本 L5 层只做存在性断言）。本次改动**不改变**任何发送行为、频率或闸门。
6. **未执行任何 git 写操作**，版本源未改。

## 7. 变更清单

| 文件 | 性质 | 说明 |
|---|---|---|
| `backend/core/auto_dm.py` | 改 | 新增模块级 `LIVE_AI_REASON_TEXT` / `LIVE_AI_STATE` / `_record_live_ai_state` / `get_live_ai_state`；新增 `AutoDM.evaluate_live_ai`；`_make_gen_dm_message` 改为取判定+记状态+按结果回落（返回值语义零回归） |
| `backend/api/ai.py` | 改 | 新增只读端点 `GET /api/ai/live_dm_state` |
| `frontend/src/components/live/live-page.tsx` | 改 | 新增 `LiveAiDmState` 类型、`fetchLiveAiDmState`、5s 轮询查询、"AI 文案：已生效 / 未生效（原因：xxx）" 提示条 |
| `backend/scripts/verify_live_ai_observability.py` | 新增 | 可复跑验证脚本（L1-L5，29 项，自打印前后读数） |
| `artifacts/fix_ai_reply_p1/B_live_ai_observability.md` | 新增 | 本报告（唯一输出文件） |
