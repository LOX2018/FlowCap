# 2026-09-26｜M-2 闭环：secsdk 签名剩余缺口（A/B 实测裁决 + 误归因订正）（v0.45.30）

> 关联：**M-2**（secsdk 其余端点接线）· **L-2 G2**（签名接线门禁）· 前序 `16c250c`/v0.45.8（5 端点）
> 案例性质：**争议裁决 + 误归因订正**（两个候选假设之一被 A/B 实测证伪，另一被证实）
> 严重级：🔴 高（`/aweme/v1/web/aweme/detail/` 属**内容页核心链路**，此前 **100% 403**）

---

## 1. 设计意图（Step 1）

| 项 | 内容 |
|---|---|
| **模块** | `dy_apis/client_video.py` → `get_work_info`（`/aweme/v1/web/aweme/detail/`）、`get_feed`（`/aweme/v1/web/tab/feed/`） |
| **设计契约** | secsdk `webSign` 保护清单内的 GET 端点**必须带签名**（`signed_url`），否则被 Argus 网关拦下 |
| **预期行为** | 命中清单的端点返 `200` + 有效 JSON；**不得**把 `params.get()` 交给 requests（二次编码会破坏签名） |
| **设计假设** | `PROTECTED_PATHS_GET` 清单是**权威**的（G2 门禁即据此建立） |

---

## 2. 当前状态偏差（Step 2）

`.known-gaps.json` 存有 **2 条互相矛盾的候选假设**：

| 条目 | 假设 | 状态 |
|---|---|---|
| ① `client_video.py:/aweme/v1/web/tab/feed/` | 「docstring 声称实测 200/659KB 成功，与保护清单**矛盾**。二者必有一误：要么清单过宽、要么该次实测处于特殊 cookie/msToken 状态」 | `open-conflict`（**未裁决**） |
| ② `client_video.py:/aweme/v1/web/aweme/detail/` | 「在保护清单内但未接 `signed_url()`」 | `work-in-progress` |

**另有一处误归因**：`api/platform.py:1142` 注释称作品详情「实测 **HTTP 200 但响应体 0 字节**（平台侧行为，与写操作族同源）」。

---

## 3. 执行链追踪（Step 3）

```
[现象] 内容页「取址」在仅有 aweme_id 时失败（前端被建议改用列表回传的 raw）
   ↓
[入口] api/platform.py:1153 media_resolve → api.get_work_info(auth, url)
   ↓
[实现] dy_apis/client_video.py:53 get_work_info
         → api = "/aweme/v1/web/aweme/detail/"
         → requests.get(base, params=params.get())      ← 无 secsdk 签名
   ↓
[上游] Argus 网关 → 403（46B "Blocked by ArgusSecurityPlugin Uifid Not Found"）
   ↓
[误读] 上游把非 200 体当空读 → 记为「200 但响应体 0 字节」→ 归因给「平台侧行为」
```

---

## 4. 根因分析 + A/B 裁决（Step 4）

### 4.1 假设②：detail 明确成立 ✅

**A/B 实测**（真实账号「四川工伤张老师」，真实作品 id `7680508646496750890`，来自 tab/feed 签名返回）：

| 条件 | status | 字节 | 内容 |
|---|---|---|---|
| **不带签名**（现状） | **403** | 46 | `Blocked by ArgusSecurityPlugin Uifid Not Found` |
| **带签名**（`signed_url`） | **200** | **124,004** | `aweme_detail` **非空**（含真实 `desc` / `video`） |

⇒ **假设②成立**；`api/platform.py` 的「200 空体」是**误归因** —— 根因是**缺签名**，非平台行为。

### 4.2 假设①：清单对该端点**过宽** ⚠️

| 条件 | status | 字节 |
|---|---|---|
| **不带签名** | **200** | 229,138 |
| **带签名** | **200** | 211,847 |

⇒ **两者均成功** —— 即 `tab/feed` **不在**签名保护范围（或平台未启用），**清单过宽**。
`.known-gaps.json` 的「二者必有一误」由此裁决：**错的是清单**（第一次实测 200/659KB 的记录是对的）。

**处置（按用户「保守策略为主」）**：签名**严格占优**（两者皆 200，签名不引入新失败面，且对齐上游清单）
⇒ **补签名**而非收窄清单。避免改上游 `secsdk_web_sign.py` 语义（其清单源自上游，擅自收窄风险更高）。

---

## 5. 实机验证（Step 5）

| 判据 | 读数 |
|---|---|
| `get_work_info` 端到端（修复后） | 返回 dict；`aweme_detail` **非空**；`desc='熊出没之夏日连连看第八集…'`；`video` 键存在 ✅ |
| `get_feed` 端到端（走 tab/feed，补签名后） | `status_code=0`、`aweme_list` **2 条** ✅ |
| 契约门禁 G2 | **0 命中**（修复前「已知缺口 2 处」→ **1 处** → **0 处**）✅ |
| 全量回归 | **935 tests**，12 项失败与干净 HEAD 基线**逐条 diff 完全一致** ⇒ 零新增回归 |

### 新增门禁 `backend/test_m2_secsdk_send_side.py`（4 项）

| 断言 | 内容 |
|---|---|
| S1 | 保护清单内每个端点都必须接 `signed_url` |
| S2 | **用了 `signed_url` 就不得再传 `params=`**（G2 抓不到「二次编码 ⇒ 签名失效」这个隐蔽错用） |
| S3 | **负控自证**：构造「signed_url + params=」→ S2 判据必须命中 |
| S4 | **过宽负控**：正确写法不得被误报 |

**破坏性验证**：向真实文件注入 `params=params.get()` → S2 精确报出 `client_video.py:103` 并 FAILED；
恢复后 4/4 OK。

> 📌 **门禁自身的教训**：S3 首版用跨行正则 `signed_url\([^)]*\)[^\n]*\n?[^\n]*params\s*=`，
> 在真实多行写法下**漏报**（负控首次运行即失败）⇒ 改为**块判定**（同块内两个条件）。
> 教训：负控存在 0 秒就抓到门禁失效 —— 这正是负控的价值。

---

## 6. 清理与归档（Step 6）

- **`.known-gaps.json`**：删除 2 条**已消除**条目（项目纪律：已消除缺口留在基线会使门禁失去准确性）。
  写前已备份 `docs/design-contracts/.known-gaps.json.bak.<ts>`；删除后 G2 仍 PASS（0 命中）。
- **`api/platform.py`**：订正误归因注释（改为「缺秒签 → 403 被误读为空」+ 附 A/B 读数）。

---

## 7. 教训

1. 🔴 **「返回空」不必然是「平台不给」，很可能是「被网关拦下后端层当成空」**：本次「200 但 0 字节」的真身是 403（46B Argus 拦截）。**判据：凡「有响应但无内容」，先取 `status_code` 与**原始字节**，不要只看解析后的 dict。**
2. ✅ **A/B 对照是裁决「清单 vs 实测」矛盾的唯一手段**：同一 cookie、同一时刻、只切换「签名」一个变量 ⇒ 结论无歧义。
3. ✅ **「两者皆成 ⇒ 取保守侧」**：签名不引入新失败面时优先签名，比自己收窄上游清单更安全（对齐上游、少一处自造语义）。
4. 🔴 **门禁必须覆盖「正确 API + 错误用法」这一维**：G2 只查「有没有调 `signed_url`」，抓不到「调了却仍传 `params=`」。**凡是「API 有使用前置条件」的能力，门禁要同时断言「用了」与「用对了」。**
5. ✅ **负控首次运行失败 ≠ 白做** —— 它在 0 秒内证明了判据本身有缺陷（比上线后漏报好得多）。

---

## 8. 归档元数据

| 项 | 值 |
|---|---|
| 版本 | v0.45.29 → **v0.45.30**（debug patch +0.01，六处齐平） |
| 缺陷编号 | **API-063 / M-2**（secsdk 签名剩余缺口） |
| 修复文件 | `backend/dy_apis/client_video.py`（`get_work_info` + `get_feed` 补 `signed_url`）· `backend/api/platform.py`（误归因订正）· `docs/design-contracts/.known-gaps.json`（删 2 条已消除缺口） |
| 新增文件 | `backend/test_m2_secsdk_send_side.py`（4 项含双负控） |
| 关联台账 | M-2（本批闭环）· L-2 G2（门禁从「2 处缺口」→ 0） |
