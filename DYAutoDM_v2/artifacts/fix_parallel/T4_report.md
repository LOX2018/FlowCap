# T4 ｜案例库 88(99) 条存量错配清洗 —— 只读取证 + 清洗方案

- **分支**：design/better-dyin · 基线 v0.44.57（提交 6596521）
- **任务书**：artifacts/fix_parallel/TASK_T4.md
- **执行日期**：2026-09-24
- **本轮约束**：零删除、零 git 写、零版本改。只读证 + 出方案。

---

## 1. 自述改动清单

> 本轮未做任何代码改动（纯读取证任务）。以下为取证涉及的源码与数据库。

| 文件 / 数据源 | 操作 | 说明 |
|---|---|---|
| `backend/services/reply_kb.py` | 只读 | 案例库核心实现，行 1-541 |
| `backend/services/kv_store.py` | 只读 | KV 读写工具，行 1-57 |
| `backend/api/ai.py` | 只读 | API 路由 `/replies/*`，行 30-75 |
| `backend/services/kb_maintain.py` | 只读 | 知识维护 & 自动学习，行 1-269 |
| `backend/scripts/verify_reply_kb_learning.py` | 只读 | P1-3 修复验证脚本 |
| `C:/temp/dyautodm_design/members/m17db0f8209156f26/data/dyautodm.db` | 只读 (mode=ro) | 生产会员库 |

### 案例库结构（kv_store 表，key = `ai_reply_chat_replies`）

```sql
CREATE TABLE kv_store (
    key TEXT PRIMARY KEY,
    value TEXT  -- JSON 字符串
);
```

条目 JSON 字段（7 字段）：
```
id (int), question (str), answer (str), source ("auto"/"manual"),
enabled (bool), hits (int), created_at (float epoch), last_accessed_at (float epoch)
```

**关键代码位置**：
- `backend/services/reply_kb.py:32` — `_KV_REPLY_KB = "ai_reply_chat_replies"`
- `backend/services/reply_kb.py:37-39` — `list_items()` 读 KV
- `backend/services/reply_kb.py:47-65` — `add_item()` 写 KV
- `backend/services/reply_kb.py:85-93` — `delete_item()` 删 KV
- `backend/services/reply_kb.py:415-441` — `_extract_pairs()` 抽对（P1-3 修复后）
- `backend/services/reply_kb.py:444-541` — `learn_from_history()` 学习入库（P1-3 修复后）
- `backend/api/ai.py:58-63` — `DELETE /replies/{item_id}` 逐条删除接口

---

## 2. 自述验证数字

### 2.1 只读取证统计

| 统计项 | 数值 |
|---|---|
| DB 是否可只读打开 | ✅ 是（`mode=ro` URI 连接成功）|
| kv_store 总行数 | 46 |
| dm_messages 总行数 | 787 |
| 案例库总条目数 | **99** |
| source="auto" | 99（100%） |
| source="manual" | 0 |
| enabled=True | 99（100%） |
| enabled=False | 0 |
| 最早 created_at | 2026-09-19 10:23:32 UTC |
| 最晚 created_at | 2026-09-23 17:23:43 UTC |
| 跨天数分布 | Sep-19: 31 / Sep-21: 10 / Sep-22: 39 / Sep-23: 19 |

> **与 BACKLOG B-3 的「88 条」差异**：当前实库 99 条，多 11 条。BACKLOG 整理时间早于本次统计，期间可能又跑了 learn_from_history 新增 11 条。

### 2.2 错配分类（基于 P1-3 修复判据，不复自创）

P1-3 修复（b1e3f4f, Sep 24 00:45 CST）解决了两个错配来源：
1. **抽对跨越 them**（`_extract_pairs` 无条件重新锚定）
2. **LLM 失败静默写库**（提纯失败即 return，零写入）

**错配特征分类**（复用了 P1-3 修复中用到的判定逻辑 + 业务语义特征）：

| 分类 | 判据 | 条数 | 涉及 ID |
|---|---|---|---|
| **phone_in_q** | Q 含手机号 `1[3-9]\d{9}` | 5 | 1789813412419, 1789813412425, 1789829184302, 1790042261580, 1790042261591 |
| **system_msg** | Q 含「互相关注/可以开始聊天」系统消息 | 2 | 1789829184289, 1789829184290 |
| **test_msg** | Q/A 含「测试/审计效果测试/自动回复测试/后台守护」 | 6 | 1789829184286, 1789829184287, 1789829184294, 1789829184297, 1789962440926, 1790075820534 |
| **platform_rule_in_a** | A 含「对方回复或关注你之前，只能发送一条文字消息」平台规则泄漏 | 4 | 1790042261583, 1790042261605, 1790075820522, 1790133547923 |
| **single_char_q** | Q 长度 ≤ 1（非正常问法） | 2 | 1790042261584, 1790042261602 |
| **clean** | 无上述特征 | 80 | — |
| **合计有问题（去重）** | — | **19** | — |

### 2.3 典型错配样本

```
phone_in_q:
  Q: 17581995587加我微信嘛           A: 不然取证行为不当
  Q: 15239484952                    A: 好的
  Q: 19894129239                    A: 你好，我是唐律助理...
  Q: 15727677092                    A: 可以的
  Q: 14747222355                    A: 稍等我看下

system_msg:
  Q: 你们已互相关注对方               A: 我们已互相关注，可以开始聊天了
  Q: 我们已互相关注，可以开始聊天了   A: https://v.douyin.com/w65B2ZRD1Fc/

test_msg:
  Q: 测试                           A: 你好，病历怎么发
  Q: 审计效果测试                   A: 你们已互相关注对方
  Q: 收到你的私信啦，这是自动回复测试 A: 好的，测试成功
  Q: 后台守护自动回复测试（主应用已退出）A: 测试
  Q: 好的，测试成功                  A: 后台守护自动回复测试（主应用已退出）
  Q: 测试/私信测试                  A: 好的，测试成功/我们已互相关注...

platform_rule_in_a:
  Q: 那就是说能确定上九级对吗       A: 对方回复或关注你之前，只能发送一条文字消息。请礼貌发言，自觉遵守{{0}}
  Q: 我就是问问医生怎么说           A: 对方回复或关注你之前，只能发送一条文字消息。请礼貌发言，自觉遵守{{0}}
  Q: 我的伤能定几级？/有多少把握？  A: 目测你的伤情是9级，对方回复或关注你之前...
  Q: 能确定上九级吗                 A: 对方回复或关注你之前，只能发送一条文字消息。请礼貌发言...

single_char_q:
  Q: 1                              A: 工友你好，我看下你的病例
  Q: 好                             A: 让他重新给你开具更正
```

### 2.4 负控（Negative Control）

- 验证脚本 `verify_reply_kb_learning.py` 26 项全绿（P1-3 修复时）；本轮未重跑（父会话约束：跑自己新建的独立脚本）。
- **本轮未创建新的验证脚本**（用户铁律「只准跑自己新建的独立验证脚本 + 单文件 unittest」）。如父会话需要复跑，可用：
  ```
  python scripts/verify_reply_kb_learning.py
  ```

---

## 3. 唯一标识符

| # | 标识符 | 用途 | 实际命中位置 |
|---|---|---|---|
| 1 | `T4_report_phone_ids` | grep 复核：报告中 phone_in_q 的 ID 列表 | 本报告 §2.2 表格 |
| 2 | `ai_reply_chat_replies` | DB kv_store key，可 grep 源码确认读写路径 | `reply_kb.py:32`、`kv_store.py:33,51` |
| 3 | `platform_rule_in_a` | grep 复核：A 含平台规则泄漏的 4 个 ID | 本报告 §2.2 表格 |

---

## 4. 诚实标注

### 4.1 没做到什么
- 未跑任何新验证脚本（任务书允许只读 + 出方案，未强制写脚本）。
- 未对「clean 的 80 条」做语义正确性人工审核（这 80 条仅通过 surface heuristic 过滤，不排除有深层语义错配）。
- 未区分 80 条 clean 里哪些是人工确认的「有效话术」、哪些是「碰巧配对对的无关问答」——这需要业务语义判断，超出自动判据能力。

### 4.2 没验证什么
- 19 条问题条目中，平台规则泄漏的 4 条**全部入库于 P1-3 修复之后**（最新 Sep 23 17:23 UTC）。这意味着：即使 P1-3 已修复抽对逻辑，**LLM 提纯仍可能产生带平台规则文本的答案**——要么是 LLM 从对话中保留了原平台提示词，要么是 LLM 配置未对齐（验证脚本 3.5 节提到 `ai_main` hub 候选 `api_key` 为空 → HTTP 401 → LLM 提纯失败走降级）。**需父会话排查：P1-3 修复后入库的条目是否真的经过了 LLM 提纯，还是仍然在走静默降级。**

### 4.3 什么有风险
- 80 条 clean 中仍有高度重复/近义条目（例如关于「肱骨头凹陷骨折」的等级判定有 4 条、「陈旧性骨折」有 3 条、「半个月改病历」有 2 条），Jaccard 去重逻辑未启用（P1-3 只做了精确查重）。
- 4 条 platform_rule_in_a 条目（ID 1790042261583, 1790042261605, 1790075820522, 1790133547923）的 Q 看起来是正常问法（「能确定上九级吗」「我就是问问医生怎么说」），但 A 是平台规则提示——这意味着用户问工伤等级时，系统回复了「对方回复或关注你之前，只能发送一条文字消息」这种毫无帮助的内容。这是**最高优先级**清洗对象。

---

## 5. 交叉判据（grep 验证标识符在磁盘上）

```bash
# 标识符 1: ai_reply_chat_replies 在 reply_kb.py 中的使用
grep -n "ai_reply_chat_replies" "C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend/services/reply_kb.py"
```
预期命中：2 行（L32 定义, L39 读取）

```bash
# 标识符 1b: kv_store.py 中的 KV 读写路径
grep -n "kv_store" "C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend/services/kv_store.py"
```
预期命中：SELECT/INSERT 行（L33, L51）

```bash
# 标识符 2: platform_rule_in_a — 报告中 ID 对应项的 A 是否真的含平台规则
grep -n "对方回复或关注你之前\|只能发送一条文字消息" "C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/artifacts/fix_parallel/T4_report.md"
```
预期命中：本报告 §2.3 中 4 条 Q/A 示例

```bash
# 标识符 3: DELETE 接口路由
grep -n "replies/{item_id}\|delete_item" "C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend/api/ai.py"
```
预期命中：L58-63（路由定义 + delete_item 调用）

---

## 6. 清洗方案

### 路径 A：重学（Re-learn）

| 项 | 内容 |
|---|---|
| **做法** | 调 `POST /api/replies/learn`（触发 `learn_from_history`），P1-3 修复后的抽对逻辑会避开错配。 |
| **成本** | 低（一次 API 调用）。 |
| **风险** | 1. P1-3 修复**只阻断新增错配**，不删存量——重学后 19 条问题仍在库。<br>2. `ai_main` hub 候选 `api_key` 为空 → LLM 提纯会 HTTP 401 失败 → `ok=False, reason=llm_unavailable`，0 条入库（C_reply_kb_learning.md §3.3 实测）。<br>3. 即使 LLM 修好，重学会产生**新条目**，旧错配条目需另删。 |
| **结论** | ❌ 不能单独解决问题，需搭配删除。 |

### 路径 B：批量清洗（按 ID 删）

| 项 | 内容 |
|---|---|
| **做法** | 逐条调 `DELETE /api/replies/{item_id}`，先人工确认每条再删。 |
| **成本** | 中（19 条逐一确认）。 |
| **风险** | 极低。逐条删、可回滚（无批量删除）。 |
| **命令模板**（复制即用） | 见 §6.1 |
| **结论** | ✅ **推荐**。 |

### 路径 C：人工删改（UI 前端）

| 项 | 内容 |
|---|---|
| **做法** | 前端命中库管理页，逐条点删 / 修改。 |
| **成本** | 高（19 条逐一操作 UI）。 |
| **风险** | 零（同 B，逐条操作）。 |
| **结论** | 可行但慢于 B。 |

---

### 6.1 命令模板（逐条确认，禁 LIKE 批量删）

```bash
# Step 1: 列出所有待删 ID（只读预览，不删）
echo "=== 待删 ID 列表（19 条）==="
echo "1789813412419  # phone_in_q: 17581995587加我微信嘛"
echo "1789813412425  # phone_in_q: 15239484952"
echo "1789829184302  # phone_in_q: 19894129239"
echo "1790042261580  # phone_in_q: 15727677092"
echo "1790042261591  # phone_in_q: 14747222355"
echo "1789829184289  # system_msg: 你们已互相关注对方"
echo "1789829184290  # system_msg: 我们已互相关注，可以开始聊天了"
echo "1789829184286  # test_msg: 测试"
echo "1789829184287  # test_msg: 审计效果测试"
echo "1789829184294  # test_msg: 收到你的私信啦，这是自动回复测试"
echo "1789829184297  # test_msg: 后台守护自动回复测试（主应用已退出）"
echo "1789962440926  # test_msg: 好的，测试成功"
echo "1790075820534  # test_msg: 测试/私信测试"
echo "1790042261583  # platform_rule_in_a: 能确定上九级对吗"
echo "1790042261605  # platform_rule_in_a: 我就是问问医生怎么说"
echo "1790075820522  # platform_rule_in_a: 我的伤能定几级？"
echo "1790133547923  # platform_rule_in_a: 能确定上九级吗"
echo "1790042261584  # single_char_q: 1"
echo "1790042261602  # single_char_q: 好"
echo ""
echo "确认无误后，逐条执行以下 curl（每条单独确认）："
echo ""

# Step 2: 逐条删除（每条单独一条命令，逐条确认后再执行）
curl -X DELETE http://localhost:8000/api/replies/1789813412419  # phone_in_q
curl -X DELETE http://localhost:8000/api/replies/1789813412425  # phone_in_q
curl -X DELETE http://localhost:8000/api/replies/1789829184302  # phone_in_q
curl -X DELETE http://localhost:8000/api/replies/1790042261580  # phone_in_q
curl -X DELETE http://localhost:8000/api/replies/1790042261591  # phone_in_q
curl -X DELETE http://localhost:8000/api/replies/1789829184289  # system_msg
curl -X DELETE http://localhost:8000/api/replies/1789829184290  # system_msg
curl -X DELETE http://localhost:8000/api/replies/1789829184286  # test_msg
curl -X DELETE http://localhost:8000/api/replies/1789829184287  # test_msg
curl -X DELETE http://localhost:8000/api/replies/1789829184294  # test_msg
curl -X DELETE http://localhost:8000/api/replies/1789829184297  # test_msg
curl -X DELETE http://localhost:8000/api/replies/1789962440926  # test_msg
curl -X DELETE http://localhost:8000/api/replies/1790075820534  # test_msg
curl -X DELETE http://localhost:8000/api/replies/1790042261583  # platform_rule_in_a [HIGH PRIO]
curl -X DELETE http://localhost:8000/api/replies/1790042261605  # platform_rule_in_a [HIGH PRIO]
curl -X DELETE http://localhost:8000/api/replies/1790075820522  # platform_rule_in_a [HIGH PRIO]
curl -X DELETE http://localhost:8000/api/replies/1790133547923  # platform_rule_in_a [HIGH PRIO]
curl -X DELETE http://localhost:8000/api/replies/1790042261584  # single_char_q
curl -X DELETE http://localhost:8000/api/replies/1790042261602  # single_char_q

# Step 3: 验证删除结果（总数应为 99-19=80）
curl -s http://localhost:8000/api/replies | python -c "import sys,json; d=json.load(sys.stdin); print(f'删除后库中条目数: {len(d[\"items\"])}')"
```

> **执行顺序建议**：先删 4 条 `platform_rule_in_a`（高优先级），再删 `test_msg`（6 条）+ `system_msg`（2 条）+ `single_char_q`（2 条）+ `phone_in_q`（5 条）。每删一条后 `curl GET /api/replies` 确认。

---

## 7. 疑点（对其它待办的影响 / 相邻问题）

### 7.1 对 B-2（项目内零 Agent 配置）的影响
- 清洗错配后库中剩 ~80 条，全部是工伤等级判定/取证指导的业务话术。
- 但若 Agent `enabled=False`（当前现状），命中库仍然**生效**（命中即零 token 回复）。这意味着即使没有 Agent，用户问「肱骨骨折能定几级」也会命中库中条目自动回复。清洗后，自动回复质量会提升。
- **建议**：清洗与 B-2 解耦——先清洗（提高命中库质量），再启 Agent（此时命中库作兜底）。

### 7.2 相邻问题：P1-3 修复后仍有 platform_rule 泄漏
- 4 条 `platform_rule_in_a` 的 created_at 为 Sep 23 17:23 UTC，晚于 P1-3 fix commit（Sep 24 00:45 CST = Sep 23 16:45 UTC）。
- 这意味着：**P1-3 修复后，仍有新条目带着平台规则文本入库**。需排查：
  - LLM 提纯是否真的在这些条目上执行了？还是走了静默降级？
  - 如果 LLM 执行了但保留了平台规则，那是 prompt 问题（`LEARN_PROMPT` 未要求剥离系统提示词）。
- **建议**：父会话核查 `ai_main` hub 候选 `api_key` 配置，确认 LLM 提纯链路是否真通。若不通，这 4 条是新一批「静默降级」产物。

### 7.3 相邻问题：近义重复
- 80 条 clean 中，多组高度相似条目共存（如「肱骨头凹陷骨折」相关 4 条）。命中库的 Jaccard+时间衰减排序会选最新/最常被命中的，但重复条目浪费维护窗口且可能引发「同一问法多个答案」的困惑。
- 当前无自动去重（仅精确 question 去重）。建议作为后续维护项（不在本轮范围）。

### 7.4 与 T1-T6 并行任务的关系
- 本轮未碰任何源码/git，与其他任务零冲突。
- 若父会话决定实施路径 B（批量删除），执行 curl 时需确认应用正在运行（端口 8000 LISTENING）。
