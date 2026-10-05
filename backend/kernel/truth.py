# coding=utf-8
"""单一事实来源常量与判据阈值（2026-09-22 M-10；2026-09-23 接线，P2-5）。

## 本模块是**真 SSOT**（此前是「假 SSOT」，实测：全仓零消费者）
`NICKNAME_SOURCE` / `NICKNAME_SOURCES_ORDER` 在创建时只被自己引用，且 `kernel/`
缺 `__init__.py`（隐式 namespace package）—— 名为单一来源，实则单点死代码。
现按「要么真正接线、要么删除」处置为**真正接线**（处置理由见下），并把
P2-4 收敛出来的**唯一占位判据阈值**也放进来，由 `services/verdicts.py` 引用。

处置理由（为何不选「声明作废并删除」）：
  `docs/architecture.md`（§8、校准横幅）与
  `docs/design-contracts/C-01-im-capture.md` 已把 `backend/kernel/truth.py` 的
  `NICKNAME_SOURCE` 写成权威出处（3 处）。删除会让这些文档指向不存在的模块，
  反而再造一处漂移；接线则让「文档指向的常量」真的被代码消费。

## 消费者（`scripts/diag/check_kernel_truth_consumers.py` 机械核验）
- ``services/verdicts.py``（同包内 `from kernel.truth import ...`）
- 上述文档按常量名引用
"""

# 昵称来源：IndexedDB <uid>_user（实测 44/44 覆盖率）。
# 抖音改版后前端不再发 im/user/info（滚动全程 hook=0），
# 现行为读浏览器 IndexedDB 的 <uid>_user 存储 → 数字 uid 比对。
NICKNAME_SOURCE = "indexeddb:<uid>_user"

# 昵称来源优先级（降序）：IndexedDB > DOM 回退 > active_batch 主动捕获。
# indexeddb: 浏览器 IndexedDB <uid>_user（首选，覆盖率最高）
# dom: DOM 实时回退（BCC 截获前端自发行为）
# active_batch: 主动批量捕获（兜底方案，有风控风险）
NICKNAME_SOURCES_ORDER = ("indexeddb", "dom", "active_batch")

# ─────────────────────────────────────────────────────────────────────────────
# 占位判据的**唯一阈值**（P2-4：此前 `services/verdicts.py` 同一概念两条判据，
# 对 '12345' 一个判 True、一个判 False —— 现只有这一个常数）
# ─────────────────────────────────────────────────────────────────────────────
# 纯数字值长度 ≥ 本值即视为占位（真实抖音 uid 为 19 位左右）。
# 取 6 是为了**排除短纯数字昵称**（用户可以把昵称设成 "12345"），
# 同时仍能catch 真实 uid：这就是「一个概念一条判据」的取值依据。
UID_MIN_DIGITS = 6
