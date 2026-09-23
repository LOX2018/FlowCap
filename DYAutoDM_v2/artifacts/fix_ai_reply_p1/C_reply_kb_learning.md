# C｜reply_kb（对话回复库）自动学习质量修补 —— P1-3

- **分支**：design/better-douyin（v0.44.55）
- **日期**：2026-09-23/24
- **唯一改动源**：`backend/services/reply_kb.py`
- **新增验证脚本**：`backend/scripts/verify_reply_kb_learning.py`
- **原文件备份**：`backend/services/reply_kb.py.bak_p13`（只读留档，供前后对比）
- 未改动 `ai_reply.py` / `dm_dispatch.py` / `core/dispatch.py` / `api/ai.py`；无任何 git 写操作；未改动版本源。

---

## 0. 结论速览

| 项 | 状态 | 说明 |
|---|---|---|
| ① 抽对语义校验 | ✅ 已做 | them→them→me 不再错配；真实样本 10/23 会话被纠正 |
| ② LLM 失败不再静默 | ✅ 已做 | 提纯失败即 `ok=False` + 原因码，且**零写入** |
| ③ find_match 接入语义 embedding | ✅ 已做（**默认关闭**） | `sem_enabled=False` 时与修复前逐例一致；开启后同义句可命中；embedding 挂了回落 Jaccard |
| 验证脚本 | ✅ 26/26 通过 | `python scripts/verify_reply_kb_learning.py` |

---

## 1. 改动位置与修法

> 全部改动均通过 **terminal 跑 Python 字节级脚本** 写入（312 → 541 行），未使用 patch 工具做多行嵌套替换。写完后跑 `py_compile` + `ast.parse`（见 §5）。行号为**改后**文件行号。

### 1.1 ① 抽对加语义校验（原约 236-247 行）

**位置**：`learn_from_history` 内联循环 → 抽出为独立函数 `_extract_pairs`（**L415**）；旧实现留档 `_extract_pairs_legacy`（**L394**，仅留档，业务不调用）；过滤条件抽为 `_is_valid_text`（**L378**）。

**修法**：把锚定条件从 `if role == "them" and q is None:` 改为 **无条件重新锚定** `if role == "them": q = t`。

```python
def _extract_pairs(msgs) -> list:
    pairs = []
    q = None
    for role, text, mtype in msgs:
        if not _is_valid_text(text, mtype):
            continue
        t = (text or "").strip()
        if role == "them":
            # 关键修复点：**无条件重新锚定**。只要中间又冒出一条 them，
            # 前一条 them 与其后 me 不构成问答对 —— 旧实现这里写的是
            # `if role == "them" and q is None`，才让隔着 5 条 them 的
            # 两条消息被硬配成一对。
            q = t
            continue
        if role == "me" and q:
            pairs.append((q, t))
            q = None
    return pairs
```

**语义**：一条 them 与其配对的 me 之间**不得跨越另一条 them**；跨越了就丢弃旧 them 并把锚点重新落到新的 them 上。

**过滤规则守恒**：`_is_valid_text` 逐字照搬原条件（msg_type 属 `1/0/None/"text"/""`、`[` 前缀、`你已确认` 前缀），**一字未改**。

### 1.2 ② LLM 提纯失败不再静默（原约 254-262 行）

**位置**：`learn_from_history`（**L444**）的 LLM 提纯段与收尾段。

**修法**：
- 删掉 `if not learned: learned = [{"question": q[:60], "answer": a[:200]} ...]` 的**静默降级原样截断入库**。
- 提纯失败 → **直接 return，一步都不往库里写**，并返回结构化字段：
  `ok / reason / scanned / extracted / added / purified / message`。
- 原因码：`llm_not_configured`（无 base_url）、`llm_unavailable`（全链失败：坏 key/断网/HTTP 错/空回复/思考泄漏被丢弃）、`llm_no_json`（回了文字但无 JSON 数组）、`llm_json_bad`（JSON 解析失败）、`llm_empty_array`、`llm_exception:<msg>`。
- 成功时 `ok=True, reason="", purified=True`。
- 无可配对样本时 `ok=True, reason="no_pairs"` —— **不算失败**（正常空输入，非提纯失败，避免误报）。
- 新增防御：LLM 返回非 dict 元素的数组时跳过该元素。

### 1.3 ③ find_match 接入语义 embedding（原约 109-165 行）

**位置**：`_jaccard` 之后新增一整段语义级 helpers（**L109-281**）；`find_match`（**L283**）在 ① 与 ③ 之间插入第 ② 级。

**新增**：
- `_KV_SEM_PREFIX = "reply_kb_semv_"` / `_KV_SEM_MDL = "reply_kb_sem_model"` —— 缓存命名空间**独立于**专业知识库（`ai_reply_semv_*` / `ai_reply_sem_model`），互不污染。
- `_cosine_reply` —— 本地余弦（不依赖 numpy）。
- `_sem_ready(cfg)` —— **只有 `sem_enabled=True` 且有 `sem_base_url` 才可能活**。本机实测配置 `sem_enabled=False`，默认路径恒为死路。
- `_item_vectors` —— 条目问句向量：缓存齐则零网络直返；缓存不全则调 `ai_reply._embed_failover` 算一批并写缓存。只向量化 `question`（照 ai_reply 实测结论：混入 answer 会稀释语义）。
- `find_match_semantic_reply` —— 返回 `(item, score)`；含**向量空间一致性校验**（缓存模型 ≠ 现用模型则跳过语义级）。
- `rebuild_semantic_cache` —— 强制重算（换模型/批量导入后调）；语义级未启用时**明确返回 `ok=False`**，不假装成功。

**向后兼容红线（已逐条落实到代码）**：
1. 语义级插入位置在「① 精确/包含」**之后**、「③ Jaccard」**之前** —— 只会**新增**命中，**不取消任何既有命中**；Jaccard 分支代码一字未改。
2. `sem_enabled=False` ⇒ `_sem_ready()` 恒 False ⇒ 整个第 ② 级零网络、零副作用，与修复前**逐路径相同**。
3. 语义级任何异常被 `try/except: pass` 吞掉，绝不污染下面的 Jaccard。
4. `find_match` 签名 `(text, threshold=0.85, account="")` **完全未变**（`test_ai_agent.py` 要求含 `account` 形参，自省通过）。
5. 缓存 key 前缀独立，不会与专业知识库互相覆盖。

**为什么这次敢做第 3 项**：因为它不改默认行为。§3.3 的逐例 AB 对比显示，在本机实测配置（`sem_enabled=False`）下新旧命中**完全一致**；语义级只有在用户主动打开 `sem_enabled` 并配好 embedding 后才开始工作。

---

## 2. 修复前证据（脚本实跑读数）

### 2.1 错配：`them → them → me`

```
[1] 抽对语义校验（不得跨越另一条 them）
      修复前抽对: [('这个多少钱', '包邮的亲')]     ← 「多少钱」被配成「包邮」的答案
      修复后抽对: [('包邮吗', '包邮的亲')]
```

### 2.2 真实样本库上的错配（779 条 dm_messages，23 个会话，**只读**）

样本：`C:\temp\dyautodm_design\members\m17db0f8209156f26\data\dyautodm.db`

```
      会话数 23｜修复前抽对 65 条｜修复后 65 条｜有差异的会话 10 个
      错配样本：Q='我肱骨是肱骨头凹陷骨折'  A='就是9级的水平'
      错配样本：Q='跟骨打了7个钢针'        A='目测伤9级'
      错配样本：Q='变成陈旧性骨折了'        A='陈旧性也就意味着是旧伤'
```

**10/23 个会话（43%）的抽对结果被纠正** —— 用户说的「牛头不对马尾」在真实数据上普遍存在，不是边缘情况。

### 2.3 LLM 失败静默降级（同一验证库、同一份种子数据，旧模块实跑）

```
      修复前: 返回={'scanned': 1, 'extracted': 1, 'added': 1}，库 0 → 1 条
      修复前入库内容: [('这个多少钱', '包邮的亲')]
      ← 没经过 LLM，还带错配，前端只看到 added=1
```

### 2.4 同义句命中率（第 3 项）

```
      字符集 Jaccard('价格多少','多少钱') = 0.400（远低于 0.85 阈值）  → 命中不了
```

---

## 3. 修复后证据（同一脚本实跑）

### 3.1 抽对

```
  [OK] 修复后无此错配 — 新抽对=[('包邮吗', '包邮的亲')]
  [OK] 修复后正确锚定最后一条 them — [('包邮吗', '包邮的亲')]
  [OK] 过滤规则守恒（[图片]/你已确认 仍跳过，me 只配紧邻的 them） — [('你好', '在的')]
  [OK] 真实样本上旧/新结果确有差异 — 10 个会话的抽对结果被纠正
  [OK] 修复后抽对数不为零（没有矫枉过正把数据全丢掉） — 65 条
```

抽对总数 65 → 65 不变：**没有被矫枉过正**，正确的紧邻对全部保留，只换掉跨 them 的错配。

### 3.2 LLM 失败不再静默（三种失败模态全覆盖）

```
      修复后: {'ok': False, 'reason': 'llm_unavailable', 'scanned': 1, 'extracted': 1,
               'added': 0, 'purified': False,
               'message': 'LLM 提纯失败，本次未写入任何条目（库未变更）'}，库 0 → 0 条
      非JSON回复: {'ok': False, 'reason': 'llm_no_json', ..., 'added': 0}，库 0 → 0 条
      LLM 正常:   {'ok': True,  'reason': '', 'added': 1, 'purified': True}，库 0 → 1 条
      无可配对样本: {'ok': True, 'reason': 'no_pairs', 'added': 0}
```

### 3.3 **真实** LLM 调用（本机 FreeLLM `127.0.0.1:31415`，非桩）

- **失败态**（现网配置原样，hub `ai_main` 候选 `api_key` 为空 → HTTP 401）：
  `{'ok': False, 'reason': 'llm_unavailable', 'added': 0}`，库中 **0 条**。
- **成功态**（把配置里的 key 补进 hub 候选后跑）：
  `{'ok': True, 'reason': '', 'added': 1, 'purified': True}`，入库
  `[('包邮吗', '包邮的亲亲，全国大部分地区包邮')]` —— 走的是真实 `AIClient.chat_failover` + `LEARN_PROMPT`。

### 3.4 find_match 向后兼容（默认 `sem_enabled=False`）

```
      实测 sem_enabled=False sem_threshold=0.4 sem_base_url=http://127.0.0.1:31415/v1
      '你好': 新='你是在哪个地区受伤的' 旧='你是在哪个地区受伤的' [同]
      '包邮吗': 新='包邮的亲' 旧='包邮的亲' [同]
      '你好呀': 新='你是在哪个地区受伤的' 旧='你是在哪个地区受伤的' [同]
      '请问多少钱': 新='None' 旧='None' [同]
      '完全不相干的一句话': 新='None' 旧='None' [同]
  [OK] sem_enabled=False 时新旧命中逐例一致（向后兼容）
```

### 3.5 语义级能力与回落

```
      字符集 Jaccard('价格多少','多少钱') = 0.400（远低于 0.85 阈值）
      语义级命中: '多少钱' score=0.999          ← 桩向量模拟 embedding 服务
      embedding 挂掉后：find_match(精确)='在的' 语义级=None
      rebuild_semantic_cache(sem_enabled=False) = {'ok': False, 'error': '语义级未启用…'}
```

---

## 4. 语法检查结果

```
$ python -m py_compile services/reply_kb.py      → 通过（无输出即成功）
$ python -c "import ast; ast.parse(...)"         → ast.parse OK
  顶层函数：list_items / save_items / add_item / update_item / delete_item /
            clear_items / _jaccard / _cosine_reply / _sem_ready / _ai_get_config /
            _item_vectors / find_match_semantic_reply / rebuild_semantic_cache /
            find_match / _decay / _bump_hits / _is_valid_text /
            _extract_pairs_legacy / _extract_pairs / learn_from_history
$ 字节检查：crlf 541  lf 541  bad_cr(\r\r\n) 0   ← 行尾保持 CRLF，无污染
$ python scripts/verify_reply_kb_learning.py     → 合计 26 项，通过 26，失败 0（EXIT=0）
```

---

## 5. 诚实标注

1. **第 3 项已做，但默认不生效**：本机实测配置 `sem_enabled=False`，语义级在默认配置下完全不运行，行为与修复前一致。这意味着**尚未在真实开启 `sem_enabled` 的环境下验证语义命中效果** —— 只通过桩向量模拟验证了能力（`score=0.999`）。用户若开启 `sem_enabled`，建议先用验收脚本或本脚本的桩/真实路径确认阈值 0.40 是否符合业务预期，并调 `rebuild_semantic_cache()` 预建缓存。
2. **真实 LLM 提纯的成功态依赖 hub 候选 api_key**：当前 `ai_main` 链候选 `api_key` 为空 → HTTP 401；这是**既有配置问题**，不是本次修复引入。要让「一键学习」真正跑通 LLM 提纯，需解决 hub 候选 key 的注入问题（不在本次可改范围，需父会话处理）。
3. **`_extract_pairs_legacy` 留在源码中仅作留档**，业务路径已完全不走它；保留目的是让验证脚本能做前后对比。若做严格代码清理可删除，但删除后验证脚本的「修复前读数」会退化成硬编码，故建议保留。
4. 备份文件 `backend/services/reply_kb.py.bak_p13` 是本次唯一新增的源外文件，是否保留由父会话决定。

---

## 6. api/ai.py 层需要的适配点（**父会话处理**，本次未改）

`backend\api\ai.py:66` 当前实现：

```python
@router.post("/replies/learn")
async def replies_learn(account: str = "", limit: int = 200):
    from services import reply_kb
    try:
        r = reply_kb.learn_from_history(account=account or "", limit=limit)
        return {"ok": True, **r, "items": reply_kb.list_items()}
    except Exception as e:
        return {"ok": False, "error": str(e)}
```

**问题**：`{"ok": True, **r}` 中 `r["ok"]` 会**覆盖**外层硬编码的 `ok: True`（后者展开时同名键后写覆盖）。结果：`learn_from_history` 返回 `ok=False` 时接口仍回 `ok=True`，失败原因被吞掉，前端依然看不到「本次未走 LLM 提纯」。

**建议改法**：

```python
    try:
        r = reply_kb.learn_from_history(account=account or "", limit=limit)
        # 不再硬编码 ok=True，直接透传 reply_kb 的 ok / reason / message
        return {**r, "items": reply_kb.list_items()}
    except Exception as e:
        return {"ok": False, "error": str(e)}
```

前端提示建议：`ok=False` 时展示 `message`（如「LLM 提纯失败，本次未写入任何条目（库未变更）」），并可按 `reason` 精细化引导：
- `llm_not_configured` → 提示先配置 AI（base_url）
- `llm_unavailable` → 提示检查网络 / 代理（v2rayN）/ API Key
- `llm_no_json` / `llm_json_bad` → 提示模型输出格式异常，可换模型重试

---

## 7. 约束遵守情况

- ✅ **唯一可改文件**：`backend/services/reply_kb.py`（`backend/scripts/verify_reply_kb_learning.py` 为任务强制要求的验证脚本）。
- ✅ **零 git 写操作**：未执行 add / commit / checkout / push 等。
- ✅ **未改版本源**。
- ✅ **未改动** `services/ai_reply.py`、`services/dm_dispatch.py`、`core/dispatch.py`。
- ✅ **改 .py 未用 patch 工具做多行嵌套替换**：全程 terminal 跑 Python 字节级脚本写入；改完跑 `py_compile` 与 `ast.parse`。
- ✅ **未引入任何昵称批量查询**：未调用 `get_im_user_info` / `bulk_user_info` / `user_info_by_uids`。
- ✅ **环境隔离**：验证数据落 `C:\temp\dyautodm_verify_reply_kb`（源码树外）；真实样本库全程 `sqlite3 mode=ro` 只读打开。

---

## 8. 产物清单

| 文件 | 状态 | 说明 |
|---|---|---|
| `backend/services/reply_kb.py` | 已改（312 → 541 行） | 抽对修复 + LLM 失败不静默 + 语义级接入 |
| `backend/services/reply_kb.py.bak_p13` | 新增备份 | 修复前原文件，只读留档 |
| `backend/scripts/verify_reply_kb_learning.py` | 新增 | 可复跑验证脚本，26/26 通过 |
| `artifacts/fix_ai_reply_p1/C_reply_kb_learning.md` | 新增 | 本报告（唯一报告文件） |

**复跑命令**：`cd backend && python scripts/verify_reply_kb_learning.py`（加 `--real` 额外跑一次真实 LLM 提纯）。
