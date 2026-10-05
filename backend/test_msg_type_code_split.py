"""msg_type / msg_code 字段拆分门禁（★ 2026-10-03，P5）。

## 背景

`dm_messages.msg_type` 曾**一列混装两套体系**：
  - 语义名：'text' / 'delivery_marker'
  - 上游数字码：'7' / '27' / '8' / '50001'（抖音原始 msg type）

读侧判据因此分裂 —— 同一列上既有 `== '50001'`（回执过滤）又有
`== 'text'`（默认值），且 `api/messages._front_type` 得再做一次
「数字→语义」归一才能给前端。

本门禁守住拆分后的**列语义单一**与**回填幂等**。

## 判据设计

⚠️ 全部在**内存库**上真跑 `_migrate_schema`，不碰真实会员库。
   SQL 错误、列名错、判据错都会立刻暴露（比 grep 强）。

## 运行

    cd backend && python -m unittest test_msg_type_code_split -v
"""
from __future__ import annotations

import os
import sqlite3
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _legacy_db() -> sqlite3.Connection:
    """复刻**拆分前**的旧库结构（无 msg_code 列，msg_type 混装）。"""
    c = sqlite3.Connection(":memory:")
    c.executescript("""
    CREATE TABLE dm_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account TEXT NOT NULL,
        conv_id TEXT NOT NULL,
        role TEXT NOT NULL,
        text TEXT,
        msg_type TEXT DEFAULT 'text',
        extra TEXT DEFAULT '{}',
        ts REAL NOT NULL
    );
    -- 拆分前的真实形态：语义名与上游码**同列**
    INSERT INTO dm_messages(account,conv_id,role,text,msg_type,ts) VALUES
      ('a','c1','them','你好','text',1790880027.0),
      ('a','c1','them','[图片]','27',  1790880028.0),
      ('a','c1','them','[分享视频]','8', 1790880029.0),
      ('a','c1','me','我发的','7',       1790880030.0),
      ('a','c1','them','[系统提示]','15', 1790880032.0),
      ('a','c1','them','[未知媒体]','delivery_marker',1790880033.0);
    """)
    c.commit()
    return c


def _migrated() -> sqlite3.Connection:
    c = _legacy_db()
    from database import _migrate_schema
    _migrate_schema(c)
    return c


class TestColumnExists(unittest.TestCase):
    def test_msg_code_column_added(self):
        c = _migrated()
        cols = {r[1] for r in c.execute('PRAGMA table_info("dm_messages")')}
        self.assertIn("msg_code", cols, "msg_code 列未创建")

    def test_migrate_is_idempotent(self):
        c = _legacy_db()
        from database import _migrate_schema
        _migrate_schema(c)
        n1 = c.execute("SELECT COUNT(*) FROM dm_messages").fetchone()[0]
        _migrate_schema(c)   # 第二次：列已存在，不应报错也不应改数据
        n2 = c.execute("SELECT COUNT(*) FROM dm_messages").fetchone()[0]
        self.assertEqual(n1, n2)
        # 幂等的关键：第二次不应把已归一的 'text' 再搬一次
        dup = c.execute("SELECT COUNT(*) FROM dm_messages "
                        "WHERE msg_type='text' AND msg_code='text'").fetchone()[0]
        self.assertEqual(dup, 0, "二次迁移把语义名当码搬了（幂等失效）")


class TestBackfill(unittest.TestCase):
    """回填：上游码搬到 msg_code，msg_type 只留语义名。"""

    def test_codes_moved_to_msg_code(self):
        c = _migrated()
        for code in ("7", "27", "8", "15"):
            n = c.execute("SELECT COUNT(*) FROM dm_messages WHERE msg_code=?",
                          (code,)).fetchone()[0]
            self.assertGreaterEqual(n, 1, f"上游码 {code} 未搬到 msg_code")

    def test_code_and_normalize_are_coupled(self):
        """🔴 补盲区：搬码与归一**必须成对**，只坏一半要能被发现。

        实测盲区（2026-10-03）：把「搬码」那句禁掉、只留「归一」时，
        `msg_type` 依然没有数字（`test_msg_type_only_semantic` 仍绿），
        **但上游码被静默丢弃** —— 数据不可回溯，属静默数据损失。

        本判据直接验「每个曾有数字码的行都留了码」，补上该盲区。
        """
        c = _migrated()
        for text, code in (("我发的", "7"), ("[图片]", "27"),
                           ("[分享视频]", "8"), ("[系统提示]", "15")):
            n = c.execute("SELECT COUNT(*) FROM dm_messages "
                          "WHERE text=? AND msg_code=?", (text, code)).fetchone()[0]
            self.assertEqual(n, 1, f"「{text}」的码 {code} 丢失 ⇒ 搬码与归一脱耦")

    def test_no_unbound_local_in_any_branch(self):
        """🔴 防「分支顺序」回归（★ 2026-10-03 实测事故）。

        我曾把 `mt_sem, mt_code = split_type(mt)` 放在 `is_system_text`
        分支**之后**，而该分支自己要用这两个变量 ⇒
        `UnboundLocalError` ⇒ **所有系统提示类消息落库全崩**
        （g2b / g9 两项炸，且只在特定文案触发，极隐蔽）。

        本判据遍历每条分支：凡是返回 `MessageRecord(...)` 的路径，
        其 msg_type/msg_code 位置必须引用 `mt_sem`/`mt_code`（已定义的变量），
        不允许再直接引用裸 `mt`。
        """
        import inspect, re
        from services.message_schema import MessageRecord
        src = inspect.getsource(MessageRecord.build)
        # split_type 的赋值位置（行号）
        m = re.search(r"^\s*mt_sem, mt_code = MessageRecord\.split_type",
                      src, re.M)
        self.assertIsNotNone(m, "build() 里找不到 split_type 调用")
        lines = src.splitlines()
        assign_line = next(i for i, ln in enumerate(lines)
                           if "mt_sem, mt_code = MessageRecord.split_type" in ln)
        # 每个 return 分支：msg_type 位置不得用裸 mt
        for i, ln in enumerate(lines):
            if "return MessageRecord(" not in ln:
                continue
            self.assertNotRegex(
                ln, r"MessageRecord\([^,]+,\s*mt\s*,",
                f"第 {i+1} 行的 return 仍传裸 mt（应传 mt_sem）—— "
                f"未走 P5 拆列分流")
        # 赋值必须在所有 return 之前
        first_return = next(i for i, ln in enumerate(lines)
                            if "return MessageRecord(" in ln)
        self.assertLess(assign_line, first_return,
                        "split_type 调用在 return 之后 ⇒ 早期分支 UnboundLocalError")
        # 行为验证：系统提示文案（走最早分支）必须不崩且两列都有值
        for txt in ("[系统提示] 你已添加对方", "[投递验证] 已送达", "[未知媒体] x"):
            r = MessageRecord.build(text=txt, msg_type="7")
            self.assertTrue(r.msg_type, f"{txt} 分支产出空 msg_type")
            self.assertEqual(r.msg_code, "7", f"{txt} 分支丢了上游码")

    def test_50001_not_in_whitelist_by_design(self):
        """🔴 '50001'（已读回执）**故意不在白名单** —— 它落库前已被拦。

        依据：`recv_daemon.py:705` 在写 dm_messages **之前** return，
        实测真实库 0 行 `msg_type='50001'`。把它加进白名单会与
        `MSG_TYPES` 注册表漂移（门禁 test_whitelist_matches_registry 会红），
        且**没有存量可搬**。若某路径漏拦，该行仍会被读侧兼容过滤兜住。
        """
        c = _migrated()
        n = c.execute("SELECT COUNT(*) FROM dm_messages "
                      "WHERE msg_code='50001' OR msg_type='50001'").fetchone()[0]
        self.assertEqual(n, 0,
                         "fixture 里不该有 50001 行（真实库也没有）"
                         "—— 门禁前提失效，请核对 fixture")

    def test_msg_type_only_semantic(self):
        """🔴 核心：msg_type 列**不得**残留任何纯数字（列语义单一）。"""
        c = _migrated()
        rows = list(c.execute(
            "SELECT DISTINCT msg_type FROM dm_messages "
            "WHERE msg_type GLOB '[0-9]*'"))
        self.assertEqual(rows, [],
                         f"msg_type 仍混着数字码: {[r[0] for r in rows]}")

    def test_semantic_names_preserved(self):
        """语义名**不得**被误搬进 msg_code。"""
        c = _migrated()
        n = c.execute("SELECT COUNT(*) FROM dm_messages "
                      "WHERE msg_code IN ('text','delivery_marker')").fetchone()[0]
        self.assertEqual(n, 0, "语义名被误当成上游码")

    def test_no_row_lost(self):
        """🔴 行数守恒：回填不得丢行。"""
        c = _legacy_db()
        before = c.execute("SELECT COUNT(*) FROM dm_messages").fetchone()[0]
        from database import _migrate_schema
        _migrate_schema(c)
        after = c.execute("SELECT COUNT(*) FROM dm_messages").fetchone()[0]
        self.assertEqual(before, after, f"行数 {before} → {after}，回填丢行")

    def test_unregistered_value_not_touched(self):
        """未登记的值**原样保留**，不猜、不吞（不编造映射）。"""
        c = _legacy_db()
        c.execute("INSERT INTO dm_messages(account,conv_id,role,text,msg_type,ts)"
                  " VALUES('a','c1','them','?','99999',1790880034.0)")
        from database import _migrate_schema
        _migrate_schema(c)
        r = c.execute("SELECT msg_type,msg_code FROM dm_messages "
                      "WHERE text='?'").fetchone()
        self.assertEqual(r[0], "99999", "未登记值被改写（不应猜测）")
        self.assertIsNone(r[1], "未登记值不该被当作上游码")


class TestWhitelistMatchesRegistry(unittest.TestCase):
    """🔴 迁移白名单必须与 `MSG_TYPES` 注册表**一致**（防漂移）。

    `database._migrate_schema` 不能 import services（循环依赖），
    所以用了硬编码白名单 —— 这条判据就是那道防线的守卫。
    """

    def test_whitelist_matches_registry(self):
        from database import _migrate_schema as _ms
        import inspect, re
        src = inspect.getsource(_ms)
        # ★ 2026-10-03：回填改用 CASE WHEN 映射表（避开 R8-6 的
        #   `msg_type IN (多值)` 形态，且搬码+归一原子完成）。
        m = re.search(r'_MAP = \{([^}]*)\}', src, re.S)
        self.assertIsNotNone(m, "找不到 _CODES/_MAP 映射表")
        codes = set(re.findall(r'"([^"]+)"\s*:', m.group(1)))
        if not codes:   # 兼容旧的 _CODES 元组写法
            m2 = re.search(r'_CODES = \(([^)]*)\)', src)
            codes = set(re.findall(r"'([^']+)'", m2.group(1)))
        from services.message_schema import MSG_TYPES
        registry = {k for k in MSG_TYPES if k.isdigit()}
        self.assertEqual(
            codes, registry,
            f"白名单与注册表漂移：迁移={sorted(codes)} 注册表={sorted(registry)}")

    def test_split_type_agrees_with_backfill(self):
        """`MessageRecord.split_type` 的映射必须与迁移回填**一致**。"""
        from services.message_schema import MessageRecord as MR
        from services.message_schema import MSG_TYPES
        backfill = {"7": "text", "27": "image", "8": "video"}
        for code, name in backfill.items():
            got_name, got_code = MR.split_type(code)
            self.assertEqual(got_name, name, f"{code} 映射不一致")
            self.assertEqual(got_code, code)
        # 未登记值：不猜
        # 未登记码：语义列存原值（不猜），但**码仍留档**（审计/回溯需要）
        self.assertEqual(MR.split_type("99999"), ("99999", "99999"))
        # 语义名：原样透传，码为 None
        self.assertEqual(MR.split_type("text"), ("text", None))
        self.assertEqual(MR.split_type(""), ("text", None))


class TestReadSide(unittest.TestCase):
    """读侧：`<> '50001'` 过滤必须同时兼容新旧两列。"""

    def test_filter_sql_runs_on_split_schema(self):
        """🔴 真跑读侧 SQL：拆列后过滤条件必须语法正确且语义正确。"""
        c = _migrated()
        # 新形态：码已分离
        c.execute("UPDATE dm_messages SET msg_code='50001', msg_type='text' "
                  "WHERE msg_type='text' AND text='你好'")
        q = ("SELECT COUNT(*) FROM dm_messages "
             "WHERE account=? AND (msg_code IS NULL OR msg_code <> '50001') "
             "AND (msg_type IS NULL OR msg_type <> '50001')")
        self.assertEqual(c.execute(q, ("a",)).fetchone()[0], 5,
                         "回执行未被过滤掉（6 行中应剩 5）")
        # 旧形态兼容：码还留在 msg_type 里
        c.execute("UPDATE dm_messages SET msg_code=NULL, msg_type='50001' "
                  "WHERE text='你好'")
        self.assertEqual(c.execute(q, ("a",)).fetchone()[0], 5,
                         "旧形态（码在 msg_type）未被过滤 —— 存量库会漏")

    def test_no_bare_numeric_filter_remains(self):
        """源码里不得再有裸的 `msg_type <> '50001'`（未兼容新列）。"""
        import io, glob, re
        bad = []
        for p in glob.glob(os.path.join(_HERE, "**", "*.py"), recursive=True):
            if os.sep + "test_" in p or p.endswith("test_msg_type_code_split.py"):
                continue
            with io.open(p, encoding="utf-8", errors="ignore") as f:
                src = f.read()
            # ⚠️ 必须先**规范化空白**：过滤条件常写成跨行字符串续行
            #   （dm_search.py 就是），按行匹配会误判成「裸过滤」。
            flat = " ".join(src.split())
            for hit in re.finditer(r"msg_type <> '50001'", flat):
                # 回看 200 字符上下文里有没有 msg_code（即是否已兼容新列）
                ctx = flat[max(0, hit.start() - 200):hit.start()]
                if "msg_code" not in ctx:
                    bad.append(os.path.basename(p))
        self.assertEqual(bad, [], f"仍有未兼容 msg_code 的过滤: {bad}")


class TestCaseExpressionForm(unittest.TestCase):
    """🔴 防「CASE 形态混用」静默改坏数据（★ 2026-10-03 实测事故）。

    我把简单式写成了搜索式：`CASE {c}` 展开成 `CASE WHEN '7' THEN '7' ...` ——
    **缺条件** ⇒ SQL 报错/行为异常，回填**静默失败**；更糟的一种表现是
    `msg_code` 存成了**枚举序号**而非原值（`'7'` → `1`）。

    本判据逐条验证映射的正确性，而非只验「跑通」。
    """

    def _migrated_rows(self):
        c = _migrated()
        return c, list(c.execute(
            "SELECT text, msg_type, msg_code FROM dm_messages ORDER BY id"))

    def test_code_column_holds_original_value(self):
        """msg_code 必须是**原值**，绝不是枚举序号。"""
        c, rows = self._migrated_rows()
        for text, mt, code in rows:
            if code is not None:
                self.assertIn(code, {"7", "27", "8", "15"},
                              f"「{text}」的 msg_code={code!r} 不是上游原值"
                              f"（疑似 CASE 形态错导致存了枚举序号）")

    def test_short_code_does_not_shadow_long_one(self):
        """🔴 短码不得吃掉长码（'1' vs '15'、'0' vs '50010'）。

        CASE 简单式**首次命中即返回** ⇒ 映射表必须按 key 长度升序排。
        顺序反了：`'15'` 会先匹配 `'1'` 分支 ⇒ 存成 `1`、名也错。
        """
        c = _legacy_db()
        c.execute("INSERT INTO dm_messages(account,conv_id,role,text,msg_type,ts)"
                  " VALUES('a','c1','them','sys15','15',1790880035.0),"
                  " ('a','c1','them','sys5010','50010',1790880036.0)")
        from database import _migrate_schema
        _migrate_schema(c)
        for text, code in (("sys15", "15"), ("sys5010", "50010")):
            got = c.execute("SELECT msg_code FROM dm_messages WHERE text=?",
                            (text,)).fetchone()[0]
            self.assertEqual(got, code, f"「{text}」的码被短码吃掉（序错）")

    def test_case_uses_simple_form(self):
        """源码里必须是简单式 `CASE msg_type WHEN`（含被判断表达式）。"""
        import inspect, re
        from database import _migrate_schema as _ms
        src = inspect.getsource(_ms)
        m = re.search(r'_UPD = \((.*?)\n\s*\)', src, re.S)
        self.assertIsNotNone(m, "找不到 _UPD 模板")
        tpl = m.group(1)
        self.assertEqual(
            tpl.count("CASE msg_type"), 2,
            "两个 CASE 都必须是简单式 `CASE msg_type WHEN ...`"
            "（写成 `CASE WHEN 'x' THEN` 会缺条件 ⇒ 静默改坏数据）")
        self.assertNotIn("CASE {", tpl,
                         "模板里残留未替换的 {c}/{n} 占位")


class TestColumnCountSsot(unittest.TestCase):
    """🔴 列数 SSOT：`tuple()` 长度必须 == `CREATE TABLE` 实际列数。

    ## 为什么需要（★ 2026-10-03，同类问题连发 3 次）

    拆列后 `tuple()` 从 8 列变 9 列，全库有 3 处硬编码 `assertEqual(len(x), 8)`
    同时变红（`test_h29_message_schema` / `test_message_time_ssot` + 我自己数错）。
    逐个改是「拉锯」；本判据从**根**上锁死：
      ① `tuple()` 返回长度 == `PRAGMA table_info(dm_messages)` 列数；
      ② 全库不得再有硬编码的 `len(tup/got) == 8`。

    这类「同一事实散落多处」正是规范契约律要消灭的对象。
    """

    def test_tuple_len_equals_table_columns(self):
        from database import _init_tables
        c = sqlite3.connect(":memory:")
        _init_tables(c)
        n_cols = len(list(c.execute('PRAGMA table_info("dm_messages")')))
        from services.message_schema import MessageRecord
        rec = MessageRecord.build(text="t", msg_type="7")
        tup = rec.tuple("a", "c1", ts=1.0, msg_id="m", role="them")
        self.assertEqual(
            len(tup), n_cols,
            f"tuple() 产出 {len(tup)} 元素，但表有 {n_cols} 列 ⇒ INSERT 会错位")

    def test_no_hardcoded_8_column_assertion(self):
        """全库不得再有硬编码的 8 列长度断言（列数 SSOT 的漂移源）。"""
        import io, glob, re
        bad = []
        pat = re.compile(r"assertEqual\(\s*len\(\s*\w+\s*\)\s*,\s*8\s*\)")
        for p in glob.glob(os.path.join(_HERE, "test_*.py")):
            # ⚠️ 排除本文件自身：判据里含 `pat` 的字面量与示例文本，
            #   否则会匹配到自己的正则字符串（自指 ⇒ 恒红）。
            if os.path.basename(p) == os.path.basename(__file__):
                continue
            with io.open(p, encoding="utf-8", errors="ignore") as f:
                flat = " ".join(f.read().split())
            for hit in pat.finditer(flat):
                line = flat[max(0, hit.start() - 120):hit.start() + 60]
                if "tuple" not in line and "got" not in line and "tup" not in line:
                    continue   # 非 tuple 相关（如 base64 长度）不属本判据
                bad.append(os.path.basename(p))
        self.assertEqual(bad, [],
                         f"仍有硬编码的 8 列 tuple 断言（会随拆列漂移）: {sorted(set(bad))}")


class TestNegativeControl(unittest.TestCase):
    """负控自证：门禁必须能变红。"""

    def test_red_when_backfill_removed(self):
        """移除回填 ⇒ msg_type 残留数字码 ⇒ 必红。"""
        c = _legacy_db()
        c.execute("ALTER TABLE dm_messages ADD COLUMN msg_code TEXT")
        rows = list(c.execute(
            "SELECT DISTINCT msg_type FROM dm_messages "
            "WHERE msg_type GLOB '[0-9]*'"))
        self.assertNotEqual(rows, [], "负控前提失效：旧库本就没有数字码")

    def test_red_when_registry_drifted(self):
        """映射多一项 ⇒ 与注册表漂移 ⇒ 必红。"""
        import inspect, re
        from database import _migrate_schema as _ms
        from services.message_schema import MSG_TYPES
        src = inspect.getsource(_ms)
        m = re.search(r'_MAP = \{([^}]*)\}', src, re.S)
        self.assertIsNotNone(m, "找不到 _MAP 映射表")
        codes = set(re.findall(r'"([^"]+)"\s*:', m.group(1)))
        registry = {k for k in MSG_TYPES if k.isdigit()}
        # 模拟漂移：映射多一项
        drifted = codes | {"99999"}
        self.assertNotEqual(drifted, registry,
                            "负控前提失效：漂移后竟仍相等")


if __name__ == "__main__":
    unittest.main(verbosity=2)
