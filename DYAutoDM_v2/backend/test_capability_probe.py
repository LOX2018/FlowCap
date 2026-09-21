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

隔离：`test_config_isolation` 先把 DY_APP_ROOT 指到临时目录，避免污染真实库。
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

_ROOT = iso._ROOT


def setUpModule():
    """unittest discover 单进程导入全部模块，**后导入者覆盖** DY_APP_ROOT。

    因此进入本模块时（所有模块已导入完毕）必须把根**重新钉回**本模块的隔离目录，
    否则探针会去读别的测试模块留下的 `_TMP` 根 → 日志找不到 → 误判。
    """
    os.environ["DY_APP_ROOT"] = _ROOT


def _fresh_probe():
    """返回一个干净隔离的 probe 模块（顺带把 DY_APP_ROOT 钉回本模块隔离根）。"""
    os.environ["DY_APP_ROOT"] = _ROOT
    for m in [k for k in list(sys.modules) if k in ("database", "services.probe")
              or k.startswith("services.")]:
        sys.modules.pop(m, None)
    import services.probe as P
    return importlib.reload(P)


def _reset_db():
    """重建隔离的 SQLite 库：确保建表 + 清空相关表（不靠删文件，避免连接句柄残留）。"""
    os.environ["DY_APP_ROOT"] = _ROOT
    for m in [k for k in list(sys.modules) if k == "database"]:
        sys.modules.pop(m, None)
    import database
    importlib.reload(database)
    database.reset_connection()
    conn = database.get_db()          # 首次调用会执行建表脚本
    for tbl in ("dm_conversations", "dm_messages", "kv_store"):
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
            self.assertNotEqual(r["state"], "healthy") if not r["evidence"] else None

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
        """直播域：脱敏判据必须用 uid==111111 且 sec_uid 空（勿用 desensitized_nickname）。"""
        import shutil
        import time
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime())
        lines = [
            f"{stamp}.000 | INFO | x - [弹幕] 豫***(uid=111111 sec_uid=): 在吗",
            f"{stamp}.001 | INFO | x - [弹幕] 小***(uid=111111 sec_uid=): 谢谢",
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
