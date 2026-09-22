# coding=utf-8
"""单一事实来源常量（判据单一来源化，2026-09-22 M-10）。"""

# 昵称来源：IndexedDB <uid>_user（实测 44/44 覆盖率）。
# 抖音改版后前端不再发 im/user/info（滚动全程 hook=0），
# 现行为读浏览器 IndexedDB 的 <uid>_user 存储 → 数字 uid 比对。
NICKNAME_SOURCE = "indexeddb:<uid>_user"

# 昵称来源优先级（降序）：IndexedDB > DOM 回退 > active_batch 主动捕获。
# indexeddb: 浏览器 IndexedDB <uid>_user（首选，覆盖率最高）
# dom: DOM 实时回退（BCC 截获前端自发行为）
# active_batch: 主动批量捕获（兜底方案，有风控风险）
NICKNAME_SOURCES_ORDER = ("indexeddb", "dom", "active_batch")