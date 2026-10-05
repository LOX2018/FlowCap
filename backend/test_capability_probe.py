# -*- coding: utf-8 -*-
"""P2 能力探针（M1/M3/M7）单元测试。

覆盖（对应 工作记忆/02_效果定义与探针.md 的契约）：

1. **三态枚举与排序** —— degraded/failed/unknown/healthy 不得退化为二态；
2. **conversation_capture** —— 无数据→failed；陈旧→failed；覆盖率中间→degraded；
   全绿→healthy；**报 healthy 必须带 evidence**；
3. **message_integrity** —— (conv_id,msg_id) 重复→failed；msg_id 覆盖低→degraded；
4. **credential_identity** —— uid 漂移→failed（AUTH-050 判据）；一致→healthy；
   数据不足→unknown（**绝不冒充 healthy**）；
5. **日志解析正则** —— 用真实日志样例行回归（曾因漏一个「，」导致真 line 不匹配）；
6. **run_probes 汇总** —— worst-state 取最差；attention 列出需关注项；
7. **零网络零浏览器** —— 探针不得 import/调用 BCC、不得发网络请求。

隔离：`test_config_isolation` 先把 FLOWCAP_APP_ROOT 指到临时目录，避免污染真实库。
运行：python -m unittest test_capability_probe -v
"""
import importlib
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 配置类/DB 类测试必须隔离 DB（否则污染源码目录真实库，且单独跑绿整体跑红）
import test_config_isolation as iso  # noqa: E402

_ROOT = os.path.join(iso._ROOT, f"cap_probe_{os.getpid()}")
os.makedirs(_ROOT, exist_ok=True)
# 2026-09-23（HC-10 / M-12）：本模块**不再与其它测试模块共用同一隔离根**。
# 背景：原用 `iso._ROOT`（= `%TEMP%/flowcap_cfgtest_root`）全局共享，任何两个
# 进程同时跑 discover 都会争同一 SQLite 文件 → `database is locked`（实测）。
# 改为**按进程号取子目录** ⇒ 单进程内各模块互不干扰、且两个进程也不互相锁。


def setUpModule():
    """unittest discover 单进程导入全部模块，**后导入者覆盖** FLOWCAP_APP_ROOT。

    因此进入本模块时（所有模块已导入完毕）必须把根**重新钉回**本模块的隔离目录，
    否则探针会去读别的测试模块留下的 `_TMP` 根 → 日志找不到 → 误判。
    """
    os.environ["FLOWCAP_APP_ROOT"] = _ROOT


def _fresh_probe():
    """返回一个干净隔离的 probe 模块（顺带把 FLOWCAP_APP_ROOT 钉回本模块隔离根）。

    M-17 修复（2026-09-28）：原实现 `sys.modules.pop("database"/"services.*")`
    制造**重复模块对象**（重导入得新对象，先前导入者仍持旧对象 ⇒ 两边连不同
    SQLite 文件）。改为身份不变：env 钉根 + `reset_connection()` + reload。
    """
    os.environ["FLOWCAP_APP_ROOT"] = _ROOT
    import database
    database.reset_connection()
    import services.probe as P
    return importlib.reload(P)


def _reset_db():
    """重建隔离的 SQLite 库：确保建表 + 清空相关表（不靠删文件，避免连接句柄残留）。

    M-17 修复（2026-09-28）：原实现 `sys.modules.pop("database")` 后重导入 ⇒
    全局出现第二个 `database` 对象（本模块后续用的是新对象，其它模块仍是旧对象）。
    改为 `reset_connection()` 让**同一个**对象按当前 env 重建连接。
    """
    os.environ["FLOWCAP_APP_ROOT"] = _ROOT
    import database
    database.reset_connection()
    conn = database.get_db()          # 首次调用会执行建表脚本
    # 2026-09-23（M-12）：`ai_leads` 原**不在清理清单** ⇒ 上一个 TestAggregation 用例
    # 种下的 (account,conv_id,contact_type,contact_value) 残留，下一次 INSERT 撞
    # `UNIQUE(account,conv_id,contact_type,contact_value)` → IntegrityError。
    # 与 dm_* 同法逐表清空（表可能尚未建 ⇒ 逐表 try 容错）。
    for tbl in ("dm_conversations", "dm_messages", "kv_store", "ai_leads"):
        try:
            conn.execute(f"DELETE FROM {tbl}")
        except Exception:
            pass
    conn.commit()
    return database


def _seed(conv_rows=(), msg_rows=()):
    """写测试数据。conv_rows: (conv_id, peer_id, peer_name)；msg_rows: (conv_id, msg_id, text)。"""
    import database
    conn = database.get_db()
    for cid, pid, pname in conv_rows:
        conn.execute(
            "INSERT OR IGNORE INTO dm_conversations"
            "(account,conv_id,peer_id,peer_name,short_id,conv_type,last_ts,unread,avatar) "
            "VALUES(?,?,?,?,?,?,?,?,?)",
            ("acc1", cid, pid, pname, None, 1, 0, 0, None))
    for cid, mid, text in msg_rows:
        conn.execute(
            "INSERT OR REPLACE INTO dm_messages"
            "(account,conv_id,role,text,msg_type,extra,ts,msg_id) VALUES(?,?,?,?,?,?,?,?)",
            ("acc1", cid, "them", text, "text", "{}", 1.0, mid))
    conn.commit()


def _plant_log(lines, ts=None):
    """在隔离 root 的 logs/ 下写一份捕获日志（供 conversation_capture 读取事实）。

    ts：把每行的前导时间戳改写成该时间（默认=当前时刻）。
    理由：探针按「日志新鲜度」判陈旧（真实契约），测试若不控时间，
    固定时间戳的样例行会被正确判为 failed —— 那是探针对了、测试错了。
    """
    import re
    import time
    if ts is None:
        ts = time.time()
    stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
    ts_re = re.compile(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}(?:\.\d+)?")
    body = []
    for i, ln in enumerate(lines):
        if ts_re.match(ln):
            ln = ts_re.sub(f"{stamp}.{i:03d}", ln, count=1)
        body.append(ln)
    d = os.path.join(_ROOT, "logs")
    os.makedirs(d, exist_ok=True)
    p = os.path.join(d, "recv_daemon_20260921.log")
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(body) + "\n")
    os.utime(p, (ts, ts))
    return p


# 真实日志样例行（2026-09-21 19:56 实机落盘；曾因正则漏「，」导致不匹配）
REAL_FIRSTPACK = ("2026-09-21 19:56:17.414 | INFO | utils.code_logger:wrapper:72 - "
                  "[capture][尚进工伤小助理] 首包 拉取=0.8s 解析=0.2s （128,704B -> 44 会话）")
REAL_WRITE = ("2026-09-21 19:56:38.341 | INFO | utils.code_logger:wrapper:72 - "
              "[capture][尚进工伤小助理] 写库完成：会话 44（含消息 108），"
              "昵称命中 uid关联=0 sec_uid关联=0 未命中=44/44")
# 2026-09-21 新增字段后的真实行形态（recv_daemon 的 with_browser=False 前移捕获）
REAL_WRITE_W0 = REAL_WRITE + " with_browser=0"
# 手动「更新会话」的等价路径（浏览器抓昵称，实测 44/44）
REAL_WRITE_W1 = (REAL_WRITE.replace("昵称命中 uid关联=0", "昵称命中 uid关联=44")
                 .replace("未命中=44/44", "未命中=0/44") + " with_browser=1")


class TestLogParsing(unittest.TestCase):
    """日志解析正则 —— 真实样例行回归。"""

    def test_firstpack_real_line(self):
        P = _fresh_probe()
        m = P._RE_FIRSTPACK.search(REAL_FIRSTPACK)
        self.assertIsNotNone(m, "首包真实日志行必须能解析")
        self.assertEqual(m.group("acct"), "尚进工伤小助理")
        self.assertEqual(int(m.group("convs")), 44)
        self.assertEqual(int(m.group("bytes").replace(",", "")), 128704)

    def test_write_real_line(self):
        P = _fresh_probe()
        m = P._RE_WRITE.search(REAL_WRITE)
        self.assertIsNotNone(m, "写库完成真实日志行必须能解析（曾因漏「，」失败）")
        self.assertEqual(m.group("acct"), "尚进工伤小助理")
        self.assertEqual(int(m.group("conv")), 44)
        self.assertEqual(int(m.group("msg")), 108)
        self.assertEqual(int(m.group("byuid")), 0)
        self.assertEqual(int(m.group("miss")), 44)
        self.assertEqual(int(m.group("total")), 44)

    def test_timestamp_prefix(self):
        P = _fresh_probe()
        self.assertIsNotNone(P._RE_TS.match(REAL_WRITE))

    def test_no_year_timestamp_resolved_by_file_mtime(self):
        """★ run 日志是**无年份**格式（`09:22:12 | INFO | …`）。
        探针必须能借文件 mtime 的日期补齐时间戳 —— 否则「用户点『更新会话』」
        这条**带浏览器的正路证据**（写进 run 日志）会被系统性漏读（实测踩过）。"""
        P = _fresh_probe()
        import time as _t
        # 造一份 run 风格日志（无年份前缀），内容为带浏览器的写库完成
        d = os.path.join(_ROOT, "logs")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "run_20260922_090923.log")
        body = ("09:22:12 | INFO     | [capture][acc1] 写库完成：会话 44（含消息 108），"
                "昵称命中 uid关联=44 sec_uid关联=0 未命中=0/44 with_browser=1\n")
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
        now = _t.time()
        os.utime(p, (now, now))
        f2 = P.latest_capture_facts("acc1", with_browser=1)
        self.assertEqual(f2.get("n_conv"), 44, "无年份 run 日志必须能被读到")
        self.assertEqual(f2.get("with_browser"), 1)
        self.assertIsNotNone(f2.get("ts"))
        os.remove(p)

    def test_latest_facts_picks_latest(self):
        P = _fresh_probe()
        _plant_log([REAL_FIRSTPACK, REAL_WRITE])
        f = P.latest_capture_facts("尚进工伤小助理")
        self.assertEqual(f.get("n_conv"), 44, "应取到写库完成行")
        self.assertEqual(f.get("total"), 44)


class TestConversationCapture(unittest.TestCase):
    def setUp(self):
        _reset_db()
        self.P = _fresh_probe()

    def test_no_rows_is_failed(self):
        r = self.P.run_probe("conversation_capture", "acc1")
        self.assertEqual(r["state"], "failed")

    def test_unknown_when_only_firstpack(self):
        """只有首包、没有写库完成 → unknown（不得判定能力失效，也不得报健康）。"""
        _seed(conv_rows=[("0:1:me:p1", "p1", "张三")])
        _plant_log([REAL_FIRSTPACK.replace("尚进工伤小助理", "acc1")])
        r = self.P.run_probe("conversation_capture", "acc1")
        self.assertEqual(r["state"], "unknown")
        self.assertTrue(any("未定论" in x for x in r["reasons"]))

    def test_stale_is_failed(self):
        _seed(conv_rows=[(f"0:1:me:p{i}", f"p{i}", f"昵称{i}") for i in range(20)])
        import time
        old = time.time() - 3 * 3600          # 3 小时前 → 触发陈旧
        _plant_log([REAL_FIRSTPACK.replace("尚进工伤小助理", "acc1"),
                    REAL_WRITE.replace("尚进工伤小助理", "acc1")
                    .replace("昵称命中 uid关联=0", "昵称命中 uid关联=20")
                    .replace("未命中=44/44", "未命中=0/20")
                    .replace("会话 44", "会话 20")], ts=old)
        r = self.P.run_probe("conversation_capture", "acc1")
        self.assertEqual(r["state"], "failed")
        self.assertTrue(any("陈旧" in x or "停摆" in x for x in r["reasons"]))

    def test_fresh_link_broken_is_failed(self):
        """★ 真实发现的失效模式：最近一次捕获关联率 0，但库内历史比例 1.0
        → 必须判 failed（不得用库内历史比例冒充健康 = 探针假健康）。"""
        _seed(conv_rows=[(f"0:1:me:p{i}", f"p{i}", f"昵称{i}") for i in range(20)])
        _plant_log([REAL_FIRSTPACK.replace("尚进工伤小助理", "acc1"),
                    REAL_WRITE.replace("尚进工伤小助理", "acc1")])   # uid关联=0 未命中=44/44
        r = self.P.run_probe("conversation_capture", "acc1")
        self.assertEqual(r["state"], "failed")
        self.assertEqual(r["metrics"]["effective_ratio"], 0.0)
        self.assertEqual(r["metrics"]["nickname_ratio"], 1.0)   # 库内历史仍是好的
        self.assertTrue(any("冒充健康" in x for x in r["reasons"]))

    def test_write_real_line_with_browser_field(self):
        """新增 with_browser 字段后仍能解析；旧行（无该字段）保持向后兼容。"""
        P = _fresh_probe()
        m = P._RE_WRITE.search(REAL_WRITE_W0)
        self.assertIsNotNone(m)
        self.assertEqual(m.group("wb"), "0")
        self.assertEqual(int(m.group("byuid")), 0)
        # 浏览器路径（44/44）
        m1 = P._RE_WRITE.search(REAL_WRITE_W1)
        self.assertEqual(m1.group("wb"), "1")
        self.assertEqual(int(m1.group("byuid")), 44)
        # 旧行（无字段）→ 组为 None（向后兼容）
        m0 = P._RE_WRITE.search(REAL_WRITE)
        self.assertIsNotNone(m0)
        self.assertIsNone(m0.group("wb"))

    def test_with_browser_zero_is_not_failed(self):
        """★ 假失效回归：recv_daemon 的 with_browser=0 捕获按设计不抓昵称，
        其 uid关联=0 属**预期**。若无带浏览器证据 → unknown（未定论），
        **绝不得**报 failed（否则每天误报）。"""
        _seed(conv_rows=[(f"0:1:me:p{i}", f"p{i}", f"昵称{i}") for i in range(20)])
        _plant_log([REAL_FIRSTPACK.replace("尚进工伤小助理", "acc1"),
                    REAL_WRITE_W0.replace("尚进工伤小助理", "acc1")])
        r = self.P.run_probe("conversation_capture", "acc1")
        self.assertEqual(r["state"], "unknown",
                         "with_browser=0 且无带浏览器证据 → 必须 unknown，不得 failed")
        self.assertTrue(any("不抓昵称" in x for x in r["reasons"]),
                        "必须说明该次捕获按设计不抓昵称")

    def test_with_browser_zero_uses_browser_evidence(self):
        """★ 关键回归：最近一次是 with_browser=0，但窗口内有带浏览器捕获
        （44/44）→ 必须按**带浏览器那次**判定（healthy），不得被无昵称路径否定。"""
        _seed(conv_rows=[(f"0:1:me:p{i}", f"p{i}", f"昵称{i}") for i in range(20)])
        _plant_log([REAL_FIRSTPACK.replace("尚进工伤小助理", "acc1"),
                    REAL_WRITE_W1.replace("尚进工伤小助理", "acc1"),   # 先：带浏览器 44/44
                    REAL_WRITE_W0.replace("尚进工伤小助理", "acc1")])  # 后：无浏览器
        r = self.P.run_probe("conversation_capture", "acc1")
        self.assertEqual(r["state"], "healthy",
                         "带浏览器证据 44/44 必须胜出，不被 with_browser=0 否定")
        self.assertEqual(r["metrics"]["last_capture_fresh_ratio"], 1.0)
        self.assertTrue(any("不抓昵称" in x for x in r["evidence"]))

    def test_healthy_requires_evidence(self):
        """报 healthy 时 evidence 不得为空（02 §3.2 硬性要求）。"""
        _seed(conv_rows=[(f"0:1:me:p{i}", f"p{i}", f"昵称{i}") for i in range(20)])
        _plant_log([REAL_FIRSTPACK.replace("尚进工伤小助理", "acc1"),
                    REAL_WRITE.replace("尚进工伤小助理", "acc1")
                    .replace("昵称命中 uid关联=0", "昵称命中 uid关联=20")
                    .replace("未命中=44/44", "未命中=0/20")
                    .replace("会话 44", "会话 20")])
        r = self.P.run_probe("conversation_capture", "acc1")
        self.assertEqual(r["state"], "healthy")
        self.assertTrue(r["evidence"], "healthy 必须带证据")
        self.assertEqual(r["confidence"], "A")

    def test_numeric_nickname_is_degraded_or_failed(self):
        """peer_name 存成数字 uid（历史失效模式）→ 不得判 healthy。"""
        _seed(conv_rows=[(f"0:1:me:p{i}", f"p{i}", f"p{i}") for i in range(20)])
        _plant_log([REAL_FIRSTPACK.replace("尚进工伤小助理", "acc1"),
                    REAL_WRITE.replace("尚进工伤小助理", "acc1")])
        r = self.P.run_probe("conversation_capture", "acc1")
        self.assertIn(r["state"], ("degraded", "failed"))
        self.assertLess(r["metrics"]["nickname_ratio"], 0.95)


class TestMessageIntegrity(unittest.TestCase):
    def setUp(self):
        _reset_db()
        self.P = _fresh_probe()

    def test_healthy_when_all_have_msg_id(self):
        _seed(conv_rows=[("0:1:me:p1", "p1", "张三")],
              msg_rows=[("0:1:me:p1", f"m{i}", f"内容{i}") for i in range(10)])
        r = self.P.run_probe("message_integrity", "acc1")
        self.assertEqual(r["state"], "healthy")
        self.assertEqual(r["coverage"], 1.0)

    def test_degraded_low_msg_id_ratio(self):
        _seed(conv_rows=[("0:1:me:p1", "p1", "张三")],
              msg_rows=[("0:1:me:p1", "" if i > 1 else f"m{i}", f"c{i}") for i in range(10)])
        r = self.P.run_probe("message_integrity", "acc1")
        self.assertEqual(r["state"], "degraded")

    def test_no_messages_is_failed(self):
        r = self.P.run_probe("message_integrity", "acc1")
        self.assertEqual(r["state"], "failed")


class TestCredentialIdentity(unittest.TestCase):
    def setUp(self):
        _reset_db()
        self.P = _fresh_probe()

    def test_uid_drift_is_failed(self):
        """AUTH-050：探活/落盘 uid 与历史 conv_id 共同项不一致 → 凭证失效。"""
        _seed(conv_rows=[("0:1:3887506227210423:9999", "9999", "张三")])
        import services.conv_identity as ci
        # 库里历史本号 = 3887506227210423；伪造 env uid 与之不符
        self.assertEqual(ci.infer_my_uid_from_conv_ids(["0:1:3887506227210423:9999"]),
                         "3887506227210423")

    def test_insufficient_data_is_unknown(self):
        """无会话可比对 → unknown，绝不冒充 healthy。"""
        r = self.P.run_probe("credential_identity", "acc1")
        self.assertEqual(r["state"], "unknown")
        self.assertNotEqual(r["state"], "healthy")


class TestAggregation(unittest.TestCase):
    def setUp(self):
        _reset_db()
        self.P = _fresh_probe()

    def test_worst_state_selected(self):
        """汇总取最差态：任一 failed 则总体 failed（不得被 healthy 掩盖）。"""
        _seed(conv_rows=[(f"0:1:me:p{i}", f"p{i}", f"昵称{i}") for i in range(20)])
        res = self.P.run_probes(accounts=["acc1"])
        self.assertEqual(res["state"], "failed")   # conversation_capture 无日志→failed
        self.assertIn("conversation_capture@acc1", res["summary"]["attention"])
        self.assertEqual(res["summary"]["total"], len(self.P.CAPABILITY_ORDER))

    def test_registry_and_order(self):
        # 顺序须与 02_效果定义与探针.md §2 的五大业务域 + message_integrity 对齐
        self.assertEqual(self.P.list_capabilities(),
                         ["conversation_capture", "send_delivery", "credential_identity",
                          "live_danmaku", "ai_lead_capture", "message_integrity"])
        for c in self.P.list_capabilities():
            self.assertIn(c, self.P.REGISTRY)

    def test_all_probes_are_readonly_and_return_contract(self):
        """六个探针都必须在隔离库里安全执行，并返回完整契约字段。"""
        _seed(conv_rows=[(f"0:1:me:p{i}", f"p{i}", f"昵称{i}") for i in range(12)])
        for cap in self.P.CAPABILITY_ORDER:
            r = self.P.run_probe(cap, "acc1")
            for k in ("capability", "state", "coverage", "confidence",
                      "measured_at", "evidence", "metrics", "reasons"):
                self.assertIn(k, r, f"{cap} 缺字段 {k}")
            self.assertIn(r["state"], ("healthy", "degraded", "failed", "unknown"),
                          f"{cap} 不得返回二态以外/未知状态")
            # 2026-09-23（审计 P3-3）：原为
            #   self.assertNotEqual(r["state"], "healthy") if not r["evidence"] else None
            # —— 条件表达式**结果被丢弃**，断言永不执行（AST 实测）。现写成真断言。
            if not r["evidence"]:
                self.assertNotEqual(r["state"], "healthy",
                                    f"{cap} 无证据不得报 healthy")

    def test_send_delivery_needs_delivery_proof(self):
        """发送域：只有 role='me' 落库、无投递验证标记 → 不得报 healthy。"""
        import database
        conn = database.get_db()
        for i in range(5):
            conn.execute("INSERT OR REPLACE INTO dm_messages"
                         "(account,conv_id,role,text,msg_type,extra,ts,msg_id) "
                         "VALUES(?,?,?,?,?,?,?,?)",
                         ("acc1", "0:1:me:p1", "me", f"普通消息{i}", "text",
                          "{}", 1.0, f"m{i}"))
        conn.commit()
        r = self.P.run_probe("send_delivery", "acc1")
        self.assertEqual(r["state"], "degraded")
        self.assertTrue(any("不能证明服务端已投递" in x for x in r["reasons"]))

    def test_live_danmaku_desensitized_is_failed(self):
        """直播域：脱敏判据必须用 uid==111111 且 sec_uid 空（勿用 desensitized_nickname）。

        ⚠️ 2026-09-22 夹具修正：`[弹幕]` 行**不含账号字段**，探针改为按
        「使用前端指定账号「X」作为监测账号」标记归因后，夹具**必须**先写该标记，
        否则归因不到账号 → 只能报 unknown（这正是反向用例
        `..._other_account_not_attributed` 所守护的行为）。
        """
        import time
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        lines = [
            f"{stamp}.000 | INFO | x - [auth] 使用前端指定账号「acc1」作为监测账号",
            f"{stamp}.001 | INFO | x - [弹幕] 豫***(uid=111111 sec_uid=): 在吗",
            f"{stamp}.002 | INFO | x - [弹幕] 小***(uid=111111 sec_uid=): 谢谢",
        ]
        d = os.path.join(_ROOT, "logs")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "run_live_test.log")
        open(p, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        try:
            r = self.P.run_probe("live_danmaku", "acc1")
            self.assertEqual(r["state"], "failed")
            self.assertEqual(r["coverage"], 0.0)
        finally:
            os.remove(p)

    def test_live_danmaku_other_account_not_attributed(self):
        """直播域**账号归因**：他账号的弹幕不得计入本账号（2026-09-22 回归守卫）。

        修复前实测缺陷：探针把**全部**含弹幕的日志无差别计入**每个**被查账号，
        致两个账号返回**完全相同**读数（各 11 条 / 同一文件）。本用例断言：
        本账号无弹幕时必须报 unknown，而不是把他人的弹幕算成自己的 failed。
        """
        import time
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        lines = [
            f"{stamp}.000 | INFO | x - [auth] 使用前端指定账号「other」作为监测账号",
            f"{stamp}.001 | INFO | x - [弹幕] 豫***(uid=111111 sec_uid=): 在吗",
        ]
        d = os.path.join(_ROOT, "logs")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "run_live_test2.log")
        open(p, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        try:
            r = self.P.run_probe("live_danmaku", "acc1")
            self.assertEqual(r["state"], "unknown",
                             "他账号的弹幕不得归因给本账号（否则读数不可归因）")
            self.assertEqual(r["metrics"]["danmaku_lines"], 0)
        finally:
            os.remove(p)

    def _isolate_marker_logs(self) -> str:
        """清掉隔离 logs 里**同类标记**的历史 .log，返回日志目录。

        为什么必须自净化（实测）：本用例的断言是「accA 恰好 1 条」，而探针按
        **文件 mtime** 收集整个 `logs/` 目录。若前一次运行（或同套件的另一轮）
        留下含 `账号=accA` 的同名/异名日志文件，计数会变成 2 条 —— 表现为
        「单跑绿、全量红」的**间歇性**失败（实测复现过一次）。故断言前先把
        含本用例标记的历史文件清掉，让判据只依赖本次写入。
        """
        d = os.path.join(_ROOT, "logs")
        os.makedirs(d, exist_ok=True)
        for f in os.listdir(d):
            if not f.endswith(".log"):
                continue
            fp = os.path.join(d, f)
            try:
                txt = open(fp, "r", encoding="utf-8", errors="replace").read()
            except Exception:
                continue
            if "账号=accA" in txt or "账号=accB" in txt:
                try:
                    os.remove(fp)
                except Exception:
                    pass
        return d

    def test_live_danmaku_inline_account_field_is_authoritative(self):
        """🔴 ADR-002 §5.1 前置回归：**行内账号字段**归因，压过「最近标记」推断。

        真并发形态（ADR-002 多账号同时监听，两实例交错写同一 logs/）：

            使用前端指定账号「accA」作为监测账号     ← A 起
            使用前端指定账号「accB」作为监测账号     ← B 起（1 分钟后）
            [弹幕][账号=accA] 小***(uid=111111 sec_uid=): A 的弹幕
            [弹幕][账号=accB] 豫***(uid=111111 sec_uid=): B 的弹幕

        按旧「最近一条标记」实现：两条弹幕都会归到 **accB** ⇒ accA 读到 0 条
        （假失效）、accB 读到 2 条（含他人弹幕）。行内字段必须纠正这一点。
        """
        import time
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        lines = [
            f"{stamp}.000 | INFO | x - [auth] 使用前端指定账号「accA」作为监测账号",
            f"{stamp}.010 | INFO | x - [auth] 使用前端指定账号「accB」作为监测账号",
            f"{stamp}.020 | INFO | x - [弹幕][账号=accA] 甲***(uid=111111 sec_uid=): A 的",
            f"{stamp}.030 | INFO | x - [弹幕][账号=accB] 乙***(uid=111111 sec_uid=): B 的",
        ]
        d = self._isolate_marker_logs()
        p = os.path.join(d, "run_live_inline.log")
        open(p, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        try:
            ra = self.P.run_probe("live_danmaku", "accA")
            rb = self.P.run_probe("live_danmaku", "accB")
            self.assertEqual(ra["metrics"]["danmaku_lines"], 1,
                             "accA 应恰好读到自己的 1 条（行内字段归因）")
            self.assertEqual(rb["metrics"]["danmaku_lines"], 1,
                             "accB 应恰好读到自己的 1 条，不得吞掉 accA 的")
        finally:
            os.remove(p)

    def test_live_danmaku_legacy_log_without_field_still_works(self):
        """历史日志（无行内字段）仍靠「最近标记」归因 —— 兼容路径不得被删掉。"""
        import time
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        lines = [
            f"{stamp}.000 | INFO | x - [auth] 使用前端指定账号「legacy」作为监测账号",
            f"{stamp}.001 | INFO | x - [弹幕] 豫***(uid=1 sec_uid=ABC): 老日志",
        ]
        d = os.path.join(_ROOT, "logs")
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "run_live_legacy.log")
        open(p, "w", encoding="utf-8").write("\n".join(lines) + "\n")
        try:
            r = self.P.run_probe("live_danmaku", "legacy")
            self.assertEqual(r["metrics"]["danmaku_lines"], 1,
                             "旧日志（无行内字段）必须仍能归因")
            self.assertEqual(r["state"], "healthy")
        finally:
            os.remove(p)

    def test_danmaku_log_line_carries_account_field(self):
        """源码契约：`[弹幕]` 行必须带行内账号字段（ADR-002 §5.1 前置）。"""
        _proj = os.path.dirname(os.path.abspath(__file__))   # backend/
        src = open(os.path.join(_proj, "core", "live_hook.py"),
                   encoding="utf-8", errors="replace").read()
        self.assertIn("[弹幕][账号=", src,
                      "live_hook 的弹幕日志行未带账号字段 —— 多账号并发下读数不可归因")

    def test_live_danmaku_no_activity_is_unknown(self):
        """窗口内无弹幕 → unknown（未监听 ≠ 失效），绝不冒充 healthy。"""
        # 清空隔离 logs，避免受其它测试写入的弹幕行影响
        d = os.path.join(_ROOT, "logs")
        for f in (os.listdir(d) if os.path.isdir(d) else []):
            if f.endswith(".log"):
                try:
                    os.remove(os.path.join(d, f))
                except Exception:
                    pass
        r = self.P.run_probe("live_danmaku", "nonexistent_acct")
        self.assertEqual(r["state"], "unknown")
        self.assertNotEqual(r["state"], "healthy")

    def test_ai_lead_no_activity_is_unknown(self):
        """AI 域：有留资但窗口内无 AI 活动 → unknown（不得报 healthy）。"""
        import database
        conn = database.get_db()
        conn.execute("CREATE TABLE IF NOT EXISTS ai_leads("
                     "id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT, conv_id TEXT,"
                     "peer_name TEXT, contact_type TEXT, contact_value TEXT,"
                     "source_text TEXT, status TEXT, created_at REAL)")
        conn.execute("INSERT INTO ai_leads(account,conv_id,peer_name,contact_type,"
                     "contact_value,status,created_at) VALUES(?,?,?,?,?,?,?)",
                     ("acc1", "0:1:me:p1", "张三", "wechat", "wx123", "new", 1.0))
        conn.commit()
        r = self.P.run_probe("ai_lead_capture", "acc1")
        self.assertEqual(r["state"], "unknown")

    def test_unknown_capability_is_unknown_not_healthy(self):
        r = self.P.run_probe("no_such_capability", "acc1")
        self.assertEqual(r["state"], "unknown")


class TestNoNetworkNoBrowser(unittest.TestCase):
    """零风控边界：探针源码不得触碰网络/浏览器。"""

    def test_source_has_no_forbidden_calls(self):
        import inspect
        P = _fresh_probe()
        src = inspect.getsource(P)
        # 禁止「调用」，而非禁止文档里提到（模块 docstring 会说明边界）
        for bad in ("requests.", "urllib.", "httpx.", "playwright",
                    "browser_gate", "ensure_browser(", "capture_all(",
                    "import capture_all"):
            self.assertNotIn(bad, src,
                             f"探针不得触碰 {bad}（只读本地事实：DB + 本项目日志）")

    def test_declares_readonly_boundary(self):
        """零风控边界必须在模块 docstring 里显式声明（防止后人伸手）。"""
        import inspect
        P = _fresh_probe()
        doc = inspect.getdoc(P) or ""
        self.assertIn("只读本地事实", doc)
        self.assertIn("绝不发起任何网络请求", doc)


class TestPatrolScheduler(unittest.TestCase):
    """定时巡检：幂等 start、tick 真会跑、状态持久化、可停、禁用/异常不炸。"""

    def setUp(self):
        _reset_db()
        self.P = _fresh_probe()

    def tearDown(self):
        try:
            self.P.stop_patrol()
        except Exception:
            pass

    def test_start_is_idempotent(self):
        r1 = self.P.start_patrol(first_delay=30)
        self.assertTrue(r1.get("ok"))
        r2 = self.P.start_patrol(first_delay=30)
        self.assertTrue(r2.get("already"))
        self.P.stop_patrol()

    def test_state_has_contract_keys(self):
        st = self.P.patrol_state()
        for k in ("enabled", "interval_min", "runs", "errors"):
            self.assertIn(k, st)

    def test_tick_really_runs_and_persists(self):
        """真实等一次 tick：证明定时器确实会跑巡检并把结果落 kv。"""
        import time
        # 把首轮延迟压到最小可行值（配置下限 30s，这里直接传参绕过）
        self.P.start_patrol(first_delay=1.0)
        deadline = time.time() + 8
        st = {}
        while time.time() < deadline:
            time.sleep(0.4)
            st = self.P.patrol_state()
            if int(st.get("runs") or 0) >= 1:
                break
        self.assertGreaterEqual(int(st.get("runs") or 0), 1,
                              "定时器未在窗口内跑到第一轮")
        self.assertIn("last_result", st)
        self.assertIn(st["last_result"].get("state"),
                      ("healthy", "degraded", "failed", "unknown"))
        self.P.stop_patrol()

    def test_run_once_is_idempotent_and_increments(self):
        r1 = self.P.run_patrol_once()
        self.assertIn("state", r1)
        n1 = int(self.P.patrol_state().get("runs") or 0)
        self.P.run_patrol_once()
        n2 = int(self.P.patrol_state().get("runs") or 0)
        self.assertEqual(n2, n1 + 1)

    def test_stop_prevents_further_runs(self):
        self.P.start_patrol(first_delay=1.0)
        self.P.stop_patrol()
        self.assertFalse(self.P.PATROL_TIMER_STARTED if hasattr(self.P, "PATROL_TIMER_STARTED") else False)

    def test_disabled_config_blocks_start(self):
        from services import app_config as ac
        ac.save_section("capture", {"probe_patrol_enabled": False})
        self.P = _fresh_probe()
        r = self.P.start_patrol(first_delay=30)
        self.assertFalse(r.get("ok"))
        self.assertEqual(r.get("reason"), "disabled")
        ac.save_section("capture", {"probe_patrol_enabled": True})
        self.P = _fresh_probe()

    def test_run_once_survives_probe_exception(self):
        """巡检内部异常必须被兜住并记录，绝不抛出/中断循环。"""
        def _boom(*a, **k):
            raise RuntimeError("boom")
        orig = self.P.run_probes
        self.P.run_probes = _boom
        try:
            res = self.P.run_patrol_once()
            self.assertEqual(res.get("state"), "unknown")
            self.assertIn("boom", str(res.get("error")))
            self.assertTrue(self.P.patrol_state().get("errors"))
        finally:
            self.P.run_probes = orig


if __name__ == "__main__":
    unittest.main(verbosity=2)
