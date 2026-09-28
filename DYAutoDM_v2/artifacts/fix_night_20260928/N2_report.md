# N2 卷宗 —— T6-b：Model Hub API Key 读侧脱敏

- 任务来源：台账 §一·乙 T6「API Key 明文落库」第一半（T6-b，A 类既有能力改良）
- 分支：`design/better-douyin`（未切换；本子代理未执行任何 git add/commit/checkout/stash/clean）
- 版本源：未改动（v0.45.57 保持）
- 执行时间：2026-09-28 02:17 CST
- 唯一标识符：`T6B-MASK-20260928` / 脱敏函数 `mask_secret` / 指示字段 `api_key_set`

---

## 1. 自述改动清单

| # | 文件 | 位置 | 改动 | 行数 |
|---|------|------|------|------|
| 1 | `backend/services/model_hub.py` | L65-78（新增） | 新增 `mask_secret(token)`：空串→空串；len<=8→全 `*`；否则 前4 + `'*'*(len-8)` + 后4。docstring 明写「与 `mcp/config.py ._mask` 同语义」+ 禁回写警告 | +14 |
| 2 | `backend/services/model_hub.py` | L82-93（新增） | 新增 `_public_provider(p)`：返回**副本**，`api_key` 替换为脱敏串，附加 `api_key_set: bool` | +12 |
| 3 | `backend/services/model_hub.py` | L720-722 | `overview()` 的 providers 由 `[dict(p) ...]` 改为 `[_public_provider(p) ...]`（含 2 行注释） | +3 / -1 |
| 4 | `backend/api/model_hub.py` | L26 | `from services.model_hub import _public_provider` | +1 |
| 5 | `backend/api/model_hub.py` | L66-67 | overview 路由补 docstring（声明已脱敏 + 只透传），**语义未变** | +2 |
| 6 | `backend/api/model_hub.py` | L73-76 | `POST /providers` 回包中单个 `provider` 走 `_public_provider(rec)`（该字段此前也是明文泄漏点）；`rec` 本体仍为存储层原文，写侧未动 | +2 / -1 |
| 7 | `backend/test_model_hub_key_masking.py` | 新建 | K1~K5 门禁（145 行） | +145 |

合计：2 个既有文件 **+34 / -2**（`git diff --stat` 报 40 insertions / 2 deletions 含上下文），1 个新文件 145 行。

**未改动**：`save_provider` / `test_provider` / `fetch_provider_models` / `_candidate` / `resolve_chain` / `_normalize` / `_migrate_v1` 全部保持原样；前端零改动；版本源零改动。

### 为什么不在写侧脱敏（事故面规避）

`services/model_hub.py:319`（原 288）「key 为空/脱敏 → 保留原值」的逻辑读的是 `old.get("api_key")`（**存储层 `_load()` 的原文**），而 overview 输出的是 `_public_provider` 的**副本**。二者完全隔离：

- overview 脱敏串（如 `sk-T****…****1234`）**永不进入** `_save()`；
- 前端编辑后若回传脱敏串，`"•" in key` 判定会保住原值；即便回传的是本实现的 `*` 串，也**不会**走 overview 通道回写（写入只经过 `save_provider(body)`）。
- K4 已实测：写明文 → 再存（空 key 再提交）→ 存储层读回仍是明文原文；`resolve_chain` 候选也是明文。

---

## 2. 自述测试数字（含哪些是负控）

命令（唯一允许跑的测试）：

```
cd C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend && \
  "C:/Users/LOX/AppData/Local/Programs/Python/Python314/python.exe" -m unittest \
  test_model_hub_key_masking test_model_hub_v2 -v
```

**实测结果：`Ran 10 tests` —— `OK`（0 失败 / 0 错误）。**

| 判据 | 用例名 | 结果 | 是否负控 |
|------|--------|------|----------|
| K1 | `test_k1_overview_leaks_no_plaintext_key` | ok | 正判据 |
| K2 | `test_k2_mask_shape_keeps_first_and_last_4` | ok | 正判据 |
| K3 | `test_k3_api_key_set_flag` | ok | 正判据 |
| K4 | `test_k4_write_path_still_plaintext` | ok | 正判据（4 个子断言） |
| K5 | `test_k5_negative_control_removing_mask_turns_k1_red` | ok | **负控**（含复绿读数） |
| — | `test_model_hub_v2` × 5（既有回归） | ok × 5 | 回归组 |

- **正判据 4 条（K1~K4）全部绿**；**负控 1 条（K5）绿（即「摘掉脱敏后 K1 确实变红」这条断言成立）**。
- 既有 `test_model_hub_v2` **5/5 仍绿**（零回归）。
- 负控读数（测试内 `print` 实打输出）：
  - `[K5-NEG] 摘掉脱敏后明文泄漏复现: REAL_KEY in overview() = True`
  - `[K5-RESTORE] 还原后 K1 复绿: REAL_KEY in overview() = False`
  - 还原在 `finally` 中执行，并额外断言 `hub._public_provider is orig`。
- 样本 key：`sk-TESTKEY-AAAABBBBCCCC1234`（28 字符，测试内常量，非任何真实密钥）。
- 隔离根：`tempfile.mkdtemp(prefix="dyautodm_t6b_mask_")` + 每用例重导 `database`/`services.*`。**未写入 `C:/temp/dyautodm_design`**，也未写源码树 `backend/data/`。

> ⚠️ 日志里出现的 DB 路径是 `C:\Users\LOX\AppData\Local\Temp\dyautodm_hubtest_root\...` —— 这是**既有 `test_model_hub_v2.py` 模块顶部**固化的隔离根（它在导入时设置了 `DY_APP_ROOT`），不是本测试写入的目标；本测试模块自身用的是 `mkdtemp` 根。两个测试文件在同一进程内互相覆盖 `DY_APP_ROOT` 是既有范式，非本次引入。

### 解释器偏差（如实标注）

任务书指定 `python`（3.11.9），但本机 `python` = **Python 3.11.9 缺 `loguru`**，两个测试模块都在导入 `database` 时以 `ModuleNotFoundError: No module named 'loguru'` 全数 ERROR（6 errors，含既有 `test_model_hub_v2`——即**与本次改动无关的环境问题**，改动前同样跑不起来）。改用本机 **Python 3.14.6**（`C:/Users/LOX/AppData/Local/Programs/Python/Python314/python.exe`，loguru 已装）后 10/10 绿。项目其余测试亦以 3.14 为运行态（见既往卷宗 `py314` 用法）。

---

## 3. 唯一标识符

| 项 | 值 |
|----|----|
| 任务 ID | `T6B-MASK-20260928`（台账 T6 的第一半 T6-b） |
| 脱敏函数 | `services/model_hub.py:mask_secret` |
| 对外视图函数 | `services/model_hub.py:_public_provider`（私有，仅供 API 层） |
| 指示字段 | `providers[].api_key_set: bool` |
| 门禁文件 | `backend/test_model_hub_key_masking.py` |
| 卷宗 | `DYAutoDM_v2/artifacts/fix_night_20260928/N2_report.md`（本文件） |
| 样本 key | `sk-TESTKEY-AAAABBBBCCCC1234` |

---

## 4. 诚实标注

1. **未跑全量测试**（任务书明禁）。只跑了指定的两个模块，共 10 项。
2. **未做实机 HTTP 验证**：没有起后端调 `GET /api/modelhub/overview` 抓真实响应体；脱敏是**单元层**验证（直接调 `model_hub.overview()`）。API 层只做了 grep 与代码路径推演。
3. **测试解释器与任务书不一致**：`python`(3.11.9) 无 `loguru` 跑不起来，实际用 3.14.6（见 §2）。
4. **只覆盖 `model_hub` 一处**：`ai_reply_config` 的 `api_key` / `vision_api_key` / `sem_api_key` 仍明文，`GET /api/ai/config` 是否脱敏**本次未查、未改**（属 T6-c / 后续范围）。T6 只完成了一半的一半。
5. **存储层仍明文**：T6-b 只是读侧视图脱敏，`kv_store.model_hub` 里 `api_key` 仍是明文（拷库仍泄密钥）。根治靠 T6-c（`.env.enc`）。
6. **`POST /api/modelhub/providers` 的 `provider` 单字段**我顺手也脱敏了 —— 超出任务书「只改 overview」的字面范围，但它是同一缺陷面（明文透传）且前端不消费，已在改动清单列明；如父会话认为越界，删掉第 6 项即可一键回滚（1 行）。
7. **`api_key_set` 是新增字段**：前端 `HubProvider` TS 类型里没有它（可选字段不影响运行时；TS 侧未改，按纪律前端不动）。前端现有逻辑用 `p.api_key` 的真值判断「· 🔑 / · 无密钥」，脱敏串非空 ⇒ 仍显示 🔑，行为一致。
8. **未验证 `mask_secret` 与 `mcp/config.py._mask` 的行为一致性边界**：两者阈值不同（本实现 `<=8`，`_mask` 用 `<=12` 且中段固定 8 个 `*`）。任务书指定 `<=8`，我按任务书实现；这是**有意的差异**，不是笔误。

---

## 5. 交叉判据（grep 命令 + 命中数）

全部在 `C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend` 下执行：

| # | grep 命令 | 命中数 | 读法 |
|---|-----------|--------|------|
| G1 | `grep -rn "mask_secret" --include=*.py .` | **5** | 1 处定义（model_hub.py:65）+ 1 处使用（:91）+ 3 处测试引用。无第二份「同一事实的实现」 |
| G2 | `grep -rn "_public_provider" --include=*.py .` | **9** | 定义 1、overview 1、api 层 2（import+调用）、测试 5。除 overview 与 save_provider 回包外**无其它消费者** |
| G3 | `grep -rn '\[dict(p) for p in data\["providers"\]\]' --include=*.py .` | **0** | 旧的明文直传写法**已彻底消失**（修复前为 1） |
| G4 | `grep -rn "api_key_set" --include=*.py .` | **8** | 定义 1 + docstring 3 + 测试 4。仅本特性使用 |
| G5 | `grep -rn "api_key" api/model_hub.py` | **4** | L36（Pydantic body 入参，必须收明文）、L66-67（docstring）、L73（注释）。**路由体无明文回传** |
| G6 | `grep -rn "modelhubSaveProvider\|r\.provider" --include=*.ts --include=*.tsx ../frontend/src` | **1 / 0** | 前端只有调用点，**没有任何地方读 `response.provider.api_key`** ⇒ 脱敏不破坏 UI |
| G7 | `git diff --stat backend/services/model_hub.py backend/api/model_hub.py` | **2 files changed, 40 insertions(+), 2 deletions(-)** | 改动面最小，可回滚 |
| G8 | `git status --porcelain`（仓库根） | 仅有既有 untracked；**本批的 3 个文件**：2 个 modified + 1 个新增 | 未越界写其它文件；无 git add/commit |

---

## 6. 疑点

1. **T6-c 前的中间态风险**：现在 `api_key` 明文仍在库中，而前端「编辑」按钮用 `••••••••` 占位（`ProviderSection.tsx:116`）。用户点编辑 → 不改 key → 保存，回传 `••••••••`，靠 `model_hub.py:319` 的 `"•" in rec["api_key"]` 保住原值。**这层保护依赖前端占位符恰好是 `•`**；若哪天占位符改成 `*`，而写侧判定不加 `*`，就会把掩码写进库。建议 T6-c 落地时把写侧判定改为「只接受非掩码字符」或显式 `"不变"` 哨兵值 —— **本次未改（超范围）**。
2. **`api/model_hub.py` 的 `save_provider` 回包我顺手脱敏了**（§4.6）。若父会话坚持「overview 只透传、其余零改动」，可回滚该项；但它确实是明文泄漏点，回滚等于留半边门。
3. **日志/响应体审计中间件**：T6_report 提到「若审计日志或中间件记录了响应体，key 会落日志」。本次只堵了响应体来源，**未查是否存在记录响应体的中间件**。建议父会话后续 grep 一次（非本次范围）。
4. **`ai_reply` 侧同类缺陷未处理**：`GET /api/ai/config` 是否也明文透传 `api_key`？本次未查。从 T6_report §读取点看 `api/ai.py:606` 有消费，风险对称。
5. **测试进程内 `DY_APP_ROOT` 互相覆盖**：`test_model_hub_v2` 在模块顶部固化 `dyautodm_hubtest_root`，本测试用 `mkdtemp`。同进程跑时后导入者赢。本次 10/10 绿（两模块各自隔离、互不见对方数据），但这是个**既有的脆弱点**，不是本次引入。
6. **`ResourceWarning: unclosed database`**：跑测时输出若干条 sqlite 连接未关闭告警，来自既有 `test_model_hub_v2` 的 `_reimport()` 重导模式，**非本次引入**。
