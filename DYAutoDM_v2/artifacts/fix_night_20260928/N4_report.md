# N4 · 消灭直播模块两个「假成功端点」—— 卷宗

- **审计项**：A-1（夜间批次前瞻审计，最高优先级）
- **仓库**：`C:/Users/LOX/Desktop/DYchajian`，分支 `design/better-douyin`（未切换，`git rev-parse --abbrev-ref HEAD` 实测确认）
- **时间**：2026-09-28
- **执行者**：夜间子 agent（N4）

---

## 1. 自述改动清单

| 文件 | 改动 | 行数 |
|---|---|---|
| `DYAutoDM_v2/backend/api/live.py` | 唯一被改动的源码文件：`+165 / -8`（改后 315 行） | 315 |
| `DYAutoDM_v2/backend/test_live_endpoint_honesty.py` | 新建门禁测试（L1–L6，12 用例） | 400 |

`git status --short` 实测只有 `M DYAutoDM_v2/backend/api/live.py` 与
`?? DYAutoDM_v2/backend/test_live_endpoint_honesty.py` 两条属于本任务；
其余未跟踪文件为父会话/night 批其它任务产物，本任务**未触碰**。
未执行任何 `git add / commit / checkout / stash / clean`。

### 1.1 `POST /api/live/danmaku` —— 接真（原：恒 `ok:true` 空壳）

- 新增 `DanmakuSendBody(DanmakuRequest)`：**继承扩展**补 `account`、`room_id`
  两个可选字段。之所以不改 `models/live.py`（该文件不在可写清单内），
  是为守住「只写两个文件」的边界；继承方式同时保证旧调用方
  `{"content": "..."}` 仍通过校验 ⇒ **向后兼容**。
- 新增 `_auth_for(account)`：与 `api/linkmic.py` **同范式** ——
  `accounts.env_path_of(account)` → `common_util.load_env(env_path)`。
- 新增 `_room_id_for(room_id)`：缺省时从 kv `config.live_id` 补齐
  （同 `linkmic._room_ids`），拿不到即 fail-closed。
- **真实调用**：`from dy_apis.douyin_api import DouyinAPI`
  → `DouyinAPI.sendMsgInRoom(auth, room_id, content)`。
- 返回契约（实测 5 条失败路径 + 1 条成功路径，无恒真路径）：
  - 成功：`{"ok": True, "sent": True, "statusCode": 0, "account", "roomId", "data"}`
  - 失败：`ok=False` + `error` + `reason`，reason 取值：
    `empty_content` / `no_account` / `no_room_id` / `credential_unavailable` /
    `credential_empty` / `request_rejected` / `exception` / `upstream_failed`。

### 1.2 `POST /api/live/dm-template` —— 真落盘（原：恒 `ok:true` 空壳）

- 落点复用既有存储：`database.set_kv_json("config", ...)` 的
  `config` 键下的 `dm_template` 子对象 —— 与 `POST /resolve`、
  `POST /api/tasks/save-config` **同一落点**，未新建第二套。
- 同时同步 `settings` 运行时单例（`dm_pool` / `delay_range` /
  `interval` / `max_target`），使引擎侧立即生效。
- **落盘自证**：写回后立即 `get_kv_json("config")` 读回并比对 `pool`，
  不一致即 `ok=False`（`readback_mismatch`）；落盘异常 → `persist_failed`。

### 1.3 保留既有行为

`/stream`、`/resolve`、`/ws` 一行未动。

### 1.4 只登记不改：`@router.websocket("/ws")`

`live.py:87-103`（改后行号 99 附近）accept 后空转、不订阅 LiveChatHook、
不推任何消息；且位于 `main.py:754` `_MEMBER_EXEMPT` **免鉴权白名单**内。
按任务要求本次**只登记不改**。风险：前端连上 WS 后永远收不到弹幕，
且该入口**免鉴权**，属「静默空转 + 免鉴权」叠加，建议后续单开一条处理。

---

## 2. 自述测试数字（含哪些是负控）

**运行命令**（实测）：
```
cd C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend
C:/Users/LOX/AppData/Local/Programs/Python/Python314/python.exe -m unittest test_live_endpoint_honesty -v
```

**结果：`Ran 12 tests ... OK`（12/12 绿）**

| 门禁 | 用例 | 结果 |
|---|---|---|
| L1 | `TestL1SendRaisesMustBeFalse.test_exception_is_not_reported_as_success` | 绿 |
| L2 | `TestL2FailureShapeReflected.test_upstream_status_code_nonzero_is_false` | 绿 |
| L2 | `TestL2FailureShapeReflected.test_success_shape_reports_true_with_evidence`（对照） | 绿 |
| L3 | `TestL3CredentialMissingNoEgress.test_account_not_registered_blocks_call` | 绿 |
| L3 | `TestL3CredentialMissingNoEgress.test_empty_credential_blocks_call` | 绿 |
| L3 | `TestL3CredentialMissingNoEgress.test_missing_room_id_blocks_call` | 绿 |
| L4 | `TestL4DmTemplatePersists.test_written_pool_reads_back_verbatim` | 绿 |
| L4 | `TestL4DmTemplatePersists.test_empty_pool_reads_back_as_empty_list` | 绿 |
| **L5 负控** | `TestL5NegativeControl.test_old_implementation_turns_at_least_3_gates_red` | 绿 |
| **L5 还原复绿** | `TestL5NegativeControl.test_source_restored_and_gates_green_again` | 绿 |
| L6 | `TestL6NoRealNetworkEgress.test_real_requests_get_is_blocked` | 绿 |
| L6 | `TestL6NoRealNetworkEgress.test_stub_counter_is_zero_before_each_gate` | 绿 |

**负控读数（关键数字）**：单独抽出 `_negative_variant()` 实跑，
把两个端点还原成旧的恒 `{"ok": True}` 形态后，L1/L2/L3/L4 四条判据中
**仍成立的条数 = 0 ⇒ 变红条数 = 4/4**（要求 ≥3，达标）。
还原后源码回到修复版，L1 判据**复绿**（`test_source_restored_and_gates_green_again`
断言源码含 `DouyinAPI.sendMsgInRoom` 与 `set_kv_json("config", data)` 且端点拒绝假成功）。

**负控范式**（照本仓 `test_delivery_verify` / `test_b3_capture_probe`）：
读原文件 → 字符串注入旧实现 → `importlib.reload` → 跑判据 →
`finally` 写回原文件 → `reload` 复绿。

**零回归补充**（本任务额外自证，非强制项）：
`test_live_config_guards` 16/16 OK、`test_live_rooms` 22/22 OK。

---

## 3. 唯一标识符

| 标识 | 值 |
|---|---|
| 任务编号 | **N4** |
| 审计项 | **A-1** |
| 端点 | `POST /api/live/danmaku`、`POST /api/live/dm-template` |
| 真实调用 | `dy_apis.douyin_api.DouyinAPI.sendMsgInRoom` |
| 新增日志码 | `[LIVE-002]`（发送异常）、`[LIVE-003]`（发送结果）、`[LIVE-004]`（settings 写入失败）、`[LIVE-005]`（落盘失败） |
| 测试模块 | `test_live_endpoint_honesty`（L1–L6，12 用例） |
| 落盘键 | kv `config` → `dm_template.{pool,delay_range,interval,max_target}` |
| 卷宗路径 | `DYAutoDM_v2/artifacts/fix_night_20260928/N4_report.md` |

---

## 4. 诚实标注

1. **解释器口径（与任务书指定不同，已如实标注）**：任务书要求用
   `python`（3.11.9）。实测 **3.11.9 缺 `fastapi` / `google.protobuf`**
   （`ModuleNotFoundError: No module named 'fastapi'`），11/12 用例 error。
   改用仓库既有口径 `C:/Users/LOX/AppData/Local/Programs/Python/Python314/python.exe`
   （3.14.6，依赖齐备）⇒ 12/12 OK。**两个数字都如实列出，未美化。**
2. **`test_live_ai_observability` 不存在** —— 已在仓内确认无此文件
   （`ls` 报 No such file or directory），按任务书要求**跳过**，该条零回归证据缺失。
   用 `test_live_config_guards`、`test_live_rooms` 替代佐证，但**不等价**于该文件的证明力。
3. **`sendMsgInRoom` 未做真实投递验证**。本任务红线禁止真机发送弹幕，
   故只断言「端点真的调用了它 + 如实反映其结果」；
   **上游是否真能发出弹幕仍未验证**（基座源码注释原文亦载
   「本项目当前无生产调用方；真实投递验证 pending」）。
4. **`/dm-template` 的读路径未接**（任务书允许，仅登记）：
   该端点写入 `config.dm_template`，但前端模板回读走
   `GET /api/tasks/current` 的 `dmPool`（数据源是 `adm.dm_template` /
   `settings.dm_pool`），**不读** `dm_template` 键。
   本次仅保证「写进去且能从存储层读回原文」；运行时 `settings` 已同步，
   故引擎侧可见，但**持久化回读链路与前端展示尚未闭环**。
5. **`account` 缺省兜底**：不带 `account` 时会回退到
   `auto_dm.accounts.current_name()`；仍取不到才 fail-closed。
   这是为向后兼容旧调用方留的口子 —— 若当前恰好有选中账号，
   旧调用方会**真的发出弹幕**（不再是静默空转）。此为有意设计，但需父会话知悉。
6. **未改动任何前端文件**（按约定留给父会话另批处理）。

---

## 5. 交叉判据（grep 命令 + 命中数）

```bash
cd C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend

# ① 真实调用已落位（禁 grep 作门禁，此处仅作交叉核对）
grep -n "DouyinAPI.sendMsgInRoom" api/live.py            # 命中 1
grep -n "set_kv_json(\"config\", data)" api/live.py       # 命中 2（danmaku 域 1 + resolve 既有 1）

# ② 假成功残留扫描：两个端点内是否还有无依据的恒真
grep -n '"ok": True' api/live.py                         # 命中 5
#   5 条逐条核对：L156/L228 为**文档字符串**内引用旧代码（非可执行路径）；
#   L214 = danmaku 真成功（前置 status_code==0）；L273 = dm-template 落盘读回校验通过后；
#   L315 = /resolve 既有（未改动）。⇒ **可执行路径上无一条恒真**。

# ③ 旧 TODO 是否清除
grep -n "TODO" api/live.py                               # 命中 3
#   L99 = /ws 空转（本次**只登记不改**）；L156/L228 = 新增文档字符串中引用旧代码。
#   ⇒ 两个被修端点的 TODO **已清除**。

# ④ 隔离根：不得写 design 根
grep -rn "dyautodm_design" test_live_endpoint_honesty.py  # 命中 1（仅注释声明「绝不写」）
grep -n "iso._ROOT\|DY_APP_ROOT" test_live_endpoint_honesty.py  # 命中 14（隔离根接线，含注释）
#   实测 DB 落在 C:\Users\LOX\AppData\Local\Temp\dyautodm_cfgtest_root\members\...\dyautodm.db

# ⑤ 越界检查
cd C:/Users/LOX/Desktop/DYchajian && git status --short   # 本任务相关仅 2 条（1 M + 1 ??）
cd C:/Users/LOX/Desktop/DYchajian && git diff --stat      # 1 file changed, 165 insertions(+), 8 deletions(-)
```

---

## 6. 疑点

1. **前端 `sendDanmaku()` 大概率不带 `account`**（`client.ts:932`）→
   会走 `current_name()` 兜底。若兜底命中，旧前端行为从「假成功」
   变成「真发弹幕」，属**行为突变**。前端不传 account 时到底该
   fail-closed 还是兜底当前账号，建议父会话在改前端时一并裁决。
2. **`sendMsgInRoom` 的 `status_code==0` 判据**取自 `api/linkmic.py`
   同范式（`code == 0`）。但直播写接口 `safe_json` 的实际成功形态
   **未在本项目内实测过**（无生产调用方），存在「上游成功形态不是
   status_code=0」的风险 ⇒ 真机上可能把成功误判为失败（**fail-closed 方向，安全侧**）。
3. **`/ws` 免鉴权空转**：位于 `main.py:754` `_MEMBER_EXEMPT`，任何能连
   到后端的人都能建连并保持空转 WS 不推送。与本次两条**同类**
   （静默空转），建议单开一条，判据参照 A-1：要么接上 LiveChatHook 订阅，
   要么明确下线与前端断连提示。
4. **`dm_template` 与 `dm_pool` 双键并存**：本端点写 `config.dm_template`，
   而 `POST /api/tasks/save-config` 写 `config.dm_pool`，两者**语义重叠**。
   长期看应合一，否则「模板在直播页保存、在任务页读不到」的割裂会持续。
5. **`test_live_ai_observability` 缺失**使本批次少一条指定的零回归证据；
   若父会话认为必要，需指明其真实路径或确认该文件确不存在。
