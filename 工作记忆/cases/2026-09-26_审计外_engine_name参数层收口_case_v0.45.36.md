# 审计外发现：engine_name 参数层硬编码与档案矛盾（H-22 外）

- 日期：2026-09-26
- 版本：`v0.45.35` → `v0.45.36`
- 关联：ADR-016（D4·A 参数层收口）、H-22 审计外新发现
- 性质：一致性 / 身份矛盾（与 idx10 同族，但**不在审计 24 条内**）

---

## 1. 设计意图（Design Intent）

**模块**：抖音出站请求身份参数层（`Params().with_platform()` + 各 `dy_apis/*` 的
`add_param` + 私信图片上传 + 会话有效性探测）。

**设计契约（Canonical Contract Law / ADR-016 D4）**：
- 同一语义（浏览器身份：browser_name / browser_version / engine_name / engine_version /
  os_name / os_version / cpu_core_num / screen_*）**只有唯一来源** `get_profile()` 字典。
- 内核感知：Camoufox（Gecko）⇒ `engine_name="Gecko"`、`browser_name="Firefox"`、
  `engine_version == browser_version`；Chromium 回退 ⇒ Blink/Chrome。

**预期行为**：每个 REST 请求携带的 `engine_name` 必与 `browser_name` 同源
（Firefox⇔Gecko / Chrome⇔Blink），且 `engine_version == browser_version`。

---

## 2. 实测偏差（Observed Deviation）

**偏差类型**：data（身份字段自相矛盾）

**偏差点**：参数层（调用点覆盖档案）。

**实机读数**（`Params().with_platform().get()` 真跑）：
```
browser_name    = 'Firefox'
browser_version = '152.0'
engine_name     = 'Blink'        ← 硬编码，与档案 Gecko 矛盾 ❌
engine_version  = '152.0'
```

**偏离预期**：
- `browser_name='Firefox'` 但 `engine_name='Blink'` —— **逻辑不可能值**（Firefox 不用 Blink）。
- 档案侧 `fingerprint.py` 在 Gecko 下**已正确产出** `engine_name='Gecko'`；
  是**调用点硬编码把它覆盖了**。

**规模（比初判大）**：
| 类别 | 处数 | 文件 |
|---|---|---|
| `add_param("engine_name","Blink")` + 版本字面量 | ~49 处 | `dy_apis/*.py`（11 文件）+ `builder/params.py` |
| 整套 Chrome 139 身份块（含 cpu/screen） | 1 处 | `dy_apis/image_sender.py` |
| 硬编码 `Chrome/131.0.0.0` 探活 UA | 1 处 | `auto_dm/login_remote.py:631` |

---

## 3. 根因（RCA）

- **ADR-016 D1–D4 覆盖盲区**：原 D4 只列 4 处（UA/CH/签名器/模板），**参数层未纳入**。
- **档案已对、调用点错**：`fingerprint.py` 正确产出 Gecko，但 50+ 处调用点用上游抓包时代的
  硬编码值（`'Blink'` / `121~150` 版本号）覆盖了档案。上游 vendor 的 `'Blink'` 在 Chrome 时代
  内部自洽，换 Firefox 后被破坏一致性。
- `image_sender.py` 是一整套 Chrome 139 身份块（比单字段更严重），且为活代码
  （`recv_daemon.py:1889` 私信图片发送）。

---

## 4. 处置（Fix）

**全量统一到档案**（`get_profile()[...]`，零新映射、零硬编码、纯值替换）：
1. `builder/params.py:27`：`engine_name='Blink'` → `get_profile()["engine_name"]`
2. `dy_apis/*.py`（11 文件 ~49 处）：`engine_name="Blink"` 及版本字面量 `121/130/138.0.0.0`
   → 档案；其中 `client_live.py` 还含 `browser_name='Edge'` + `130.0.0.0` 一并收口
3. `dy_apis/image_sender.py`：整套 Chrome 139 身份块（browser/version/engine/os/cpu/screen）
   → `_prof[...]`；补 `from utils.fingerprint import get_profile`
4. `auto_dm/login_remote.py:631`：硬编码 `Chrome/131.0.0.0` 探活 UA
   → `get_profile()["user_agent"]`（避免「UID 来自 Firefox 会话、探活自称 Chrome」误判会话失效）

**防复发门禁**：扩建 `scripts/check_fingerprint_consistency.py` ——
- **A9a**：参数层 `browser_name`⇔`engine_name` 必须配对（Firefox⇔Gecko / Chrome⇔Blink）
- **A9b**：`engine_version` 必须与 `browser_version` 同源
- **A9c**：全仓不得再出现活跃硬编码 brand/engine 字面量（档案本体 + 测试 fixture 除外）
- 含**负控**：临时注入 1 处 `engine_name="Blink"` → A9c 立即变红；还原即恢复

---

## 5. 实机验证（Live Verification）

- 命令：`DY_APP_ROOT=C:\temp\dyautodm_design python -c "Params().with_platform().get()"`
- 环境：design 分支（BCC 未运行，档案走 preset 路径）
- 结果：
  - 改后出站参数：`browser_name='Firefox' engine_name='Gecko'`（自洽 ✅）
  - `browser_version='152.0' == engine_version='152.0'`（同源 ✅）
  - 指纹门禁 12/12 全绿（修复前 11/12，A5 失败；本次连 A5 一并清零）
- 全量回归：待提交后跑（预期零新增，失败集与基线 12 项一致）
- EOL：eng_fix2 脚本曾误伤 params.py（250 行整段重写），已 `git checkout` 还原后单独精修，
  最终 params.py 仅 +4 行（含注释）

---

## 6. 知识归档

- 标准更新：`docs/adr/ADR-016-fingerprint-profile-follow-kernel.md`（D4 表格补全 + D4·A 参数层收口）
- 案例：`工作记忆/cases/2026-09-26_审计外_engine_name参数层收口_case_v0.45.36.md`
- 门禁：`scripts/check_fingerprint_consistency.py`（A9a/A9b/A9c，含负控）
- 台账：`工作记忆/00_交接卡待办台账.md` H-22「审计外新发现」行 → 标记已修复 v0.45.36
