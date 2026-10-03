# coding=utf-8
"""批量采集（live_batch）机械门禁 —— v0.46.25。

## 为什么有这份脚本
本模块的缺陷有一个共同形态：**`hasattr` 探测 + 类型想当然** ⇒ 运行期
静默失效（页面显示「监听中」，实际一条私信都发不出）。这类缺陷
**静态检查与类型检查都抓不到**（`hasattr` 恒 False 不报错、`as` 断言可编译），
只能靠「对真实类型求值 + 断言不变量」来拦。

## 覆盖的缺陷（均为本会话实测坐实）
| 编号 | 缺陷 | 判据 |
|---|---|---|
| LB-01 | 停止后实例不从注册表摘除 ⇒ 重复启动 N 倍膨胀 + **占死并发槽位** | 停止后 total_count==0 且 current_running==0 |
| LB-02 | 同 (房间,账号) 重复启动不幂等 ⇒ 双 WS 抢同房间 | 二次 `_start_instance` 返回 already=True |
| LB-03 | `DispatchCenter()` 无参构造（auth 必填）⇒ 每实例 TypeError | 源码不得出现无参 `DispatchCenter()` |
| LB-04 | `LiveChatHook()` 无参构造（3 个必填） | 源码不得出现无参 `LiveChatHook()` |
| LB-05 | `dispatch.start()` 未 await ⇒ 协程从未调度 | 源码必须 `await inst._dispatch.start()` |
| LB-06 | `records[-50:]` 对 dict 切片 ⇒ TypeError，发送记录永远拿不到 | 对 dict 记录断言能取到末 50 条 |
| LB-07 | `SendRecord.get()` 不存在（pydantic 模型） | 对真实 SendRecord 取字段不抛异常 |
| LB-08 | `stop()` 不存在（真实为 stop_hard/stop_soft） | 源码必须引用 stop_hard |
| LB-09 | `is_connected()` 不存在 ⇒ 断线状态永不更新 | 源码不得探测 is_connected |
| LB-10 | 空账号/未登记账号必须 fail-closed | create_task 两次拒绝 |
| LB-11 | `max_target` 必须是 LiveBatchConfig 真实字段 | dataclasses.fields 含 max_target |

## 运行
    cd DYAutoDM_v2/backend && py test_live_batch_guards.py

## 负控（必须能让门禁变红）
脚本末尾 `self_check_negative_controls()` 会在**内存副本**上注入缺陷并断言
门禁能捕获；它不碰磁盘上的真实文件。
"""
import ast
import dataclasses
import os
import sys
import unittest

# 让总开关在本进程内可用（create/start 的 fail-closed 前置）。
os.environ.setdefault("DY_LIVE_BATCH_ENABLED", "1")

_HERE = os.path.dirname(os.path.abspath(__file__))
_SRC = os.path.join(_HERE, "services", "live_batch.py")


def _strip_comments(src: str) -> str:
    """剥掉注释，**保留代码原样排版**（行级剥离）。

    ⚠️ 为什么不用 `tokenize`：它会把源码重排成空格分隔的 token 流，
    破坏 `await inst._dispatch.start()` 这类跨 token 的字面匹配，
    导致门禁对**正确代码**也报红（实测踩到）。
    ⚠️ 判据纪律：判据的模式串不得写进被审文件的注释里，否则门禁恒红/恒绿皆无意义。
    """
    out = []
    for line in src.splitlines():
        q = None
        cut = len(line)
        for i, ch in enumerate(line):
            if q:
                if ch == q and (i == 0 or line[i - 1] != "\\\\"):
                    q = None
            elif ch in ("'", '"'):
                q = ch
            elif ch == "#":
                cut = i
                break
        out.append(line[:cut])
    return "\n".join(out)


def _read(path=None):
    with open(path or _SRC, encoding="utf-8") as f:
        return f.read()


class TestLiveBatchTypeContracts(unittest.TestCase):
    """LB-03/04/05/08/09：对**真实类型**求值，而不是相信源码写法。"""

    def test_lb03_dispatch_center_requires_auth(self):
        """LB-03: DispatchCenter.__init__ 首参是必填 auth。"""
        import ast as _ast
        tree = _ast.parse(open(os.path.join(_HERE, "core", "dispatch.py"),
                               encoding="utf-8").read())
        for n in _ast.walk(tree):
            if isinstance(n, _ast.ClassDef) and n.name == "DispatchCenter":
                for f in n.body:
                    if isinstance(f, _ast.FunctionDef) and f.name == "__init__":
                        pos = [a.arg for a in f.args.args[1:]]
                        nd = len(f.args.defaults)
                        required = pos[:len(pos) - nd] if nd else pos
                        self.assertIn("auth", required,
                                      "契约变了：DispatchCenter 不再要求 auth —— "
                                      "请同步复核 live_batch 的构造调用")
                        return
        self.fail("未找到 DispatchCenter.__init__")

    def test_lb04_live_hook_requires_three_args(self):
        """LB-04: LiveChatHook.__init__ 必填 (live_id, auth_, dispatch)。"""
        import ast as _ast
        tree = _ast.parse(open(os.path.join(_HERE, "core", "live_hook.py"),
                               encoding="utf-8").read())
        for n in _ast.walk(tree):
            if isinstance(n, _ast.ClassDef) and n.name == "LiveChatHook":
                for f in n.body:
                    if isinstance(f, _ast.FunctionDef) and f.name == "__init__":
                        pos = [a.arg for a in f.args.args[1:]]
                        nd = len(f.args.defaults)
                        required = set(pos[:len(pos) - nd] if nd else pos)
                        for arg in ("live_id", "auth_", "dispatch"):
                            self.assertIn(arg, required,
                                          f"契约变了：LiveChatHook 不再要求 {arg}")
                        return
        self.fail("未找到 LiveChatHook.__init__")

    def test_lb05_dispatch_start_must_be_awaited(self):
        """LB-05: 消费任务靠 create_task 挂当前 loop，必须 await 启动。"""
        src = _read()
        code_only = _strip_comments(src)
        self.assertIn("await inst._dispatch.start()", code_only,
                      "dispatch.start() 未 await ⇒ 消费任务从未被调度")
        # 反证：逐行看——凡出现 `inst._dispatch.start()` 的行，**同一行**必须含 `await`
        #（`await` 与 `inst` 之间有空格，负向后顾匹配不到，故用行内判定）。
        bad = [ln.strip() for ln in code_only.splitlines()
               if "inst._dispatch.start()" in ln and "await" not in ln]
        self.assertEqual(bad, [],
                         f"存在未 await 的 dispatch.start() 调用 ⇒ 协程从未被调度: {bad}")

    def test_lb08_stop_hard_is_the_real_api(self):
        """LB-08: 真实停止方法是 stop_hard/stop_soft，不是 stop()。"""
        import ast as _ast
        tree = _ast.parse(open(os.path.join(_HERE, "core", "dispatch.py"),
                               encoding="utf-8").read())
        methods = {f.name for n in _ast.walk(tree)
                   if isinstance(n, _ast.ClassDef) and n.name == "DispatchCenter"
                   for f in n.body if isinstance(f, _ast.FunctionDef)}
        self.assertIn("stop_hard", methods)
        self.assertNotIn("stop", methods,
                         "契约变了：DispatchCenter 新增了 stop() —— 请复核 live_batch")
        self.assertIn("stop_hard", _strip_comments(_read()),
                      "live_batch 未调用 stop_hard ⇒ 停止实例时调度器仍在跑")

    def test_lb09_no_connection_probe(self):
        """LB-09: 连接状态探测方法在 LiveChatHook 上不存在，探测它恒 False。

        ⚠️ 判据实现纪律（skill 铁律）：**判据的模式串不得出现在被审文件的注释里**，
        否则本门禁永远红/永远绿皆无意义。故先剥注释再断言。
        """
        import re as _re
        code_only = _strip_comments(_read())
        self.assertNotIn("is_connected", code_only,
                         "仍在探测不存在的连接状态方法（恒 False ⇒ 断线永不更新）")


class TestLiveBatchSourceInvariants(unittest.TestCase):
    """LB-03/04：对源码形态的机械断言（无参构造必是错的）。"""

    def test_lb03_no_bare_dispatch_center(self):
        import re as _re
        code_only = _strip_comments(_read())
        self.assertIsNone(_re.search(r"DispatchCenter\(\s*\)", code_only),
                          "存在无参 DispatchCenter() ⇒ 运行期 TypeError")

    def test_lb04_no_bare_live_hook(self):
        import re as _re
        code_only = _strip_comments(_read())
        self.assertIsNone(_re.search(r"LiveChatHook\(\s*\)", code_only),
                          "存在无参 LiveChatHook() ⇒ 运行期 TypeError")


class TestLiveBatchRecordContract(unittest.TestCase):
    """LB-06/07：对**真实 SendRecord + 真实 dict 形态**求值。"""

    def test_lb06_records_is_dict_and_slice_safe(self):
        from models.task import SendRecord
        import time as _t
        recs = {}
        for i in range(60):
            k = f"k{i}"
            recs[k] = SendRecord(
                key=k, uid=str(i), nickname=f"n{i}",
                status="captured", captured_at=_t.time(),
            )
        self.assertIsInstance(recs, dict)
        # live_batch 的取法：list(recs.values())[-50:]
        items = list(recs.values())[-50:]
        self.assertEqual(len(items), 50)
        self.assertEqual(items[-1].nickname, "n59")
        # 反证：旧写法 records[-50:] 在 dict 上抛错（dict 把 slice 当 key → KeyError）
        with self.assertRaises((KeyError, TypeError)):
            _ = recs[-50:]

    def test_lb07_sendrecord_has_no_get(self):
        from models.task import SendRecord
        import time as _t
        r = SendRecord(key="k", uid="1", nickname="n", status="captured",
                       captured_at=_t.time())
        self.assertFalse(hasattr(r, "get"),
                         "SendRecord 新增了 .get() —— 请复核 live_batch 的取字段方式")
        for field in ("key", "nickname", "status", "sent_at"):
            getattr(r, field)   # 属性访问必须可用


class TestLiveBatchConfigFields(unittest.TestCase):
    """LB-11: max_target 必须是真实字段（曾被 getattr 静默回退）。"""

    def test_lb11_max_target_is_real_field(self):
        from services.live_batch import LiveBatchConfig
        names = {f.name for f in dataclasses.fields(LiveBatchConfig)}
        self.assertIn("max_target", names,
                      "LiveBatchConfig 缺 max_target —— 发送上限会静默回退到 3")
        d = LiveBatchConfig(task_id="t", name="n").to_dict()
        self.assertIn("max_target", d)
        self.assertEqual(d["max_target"], 3)


class TestLiveBatchManagerBehaviour(unittest.TestCase):
    """LB-01/02/10：管理器真实行为。"""

    def setUp(self):
        from services.live_batch import get_manager
        self.m = get_manager()
        self._tasks = dict(self.m._tasks)
        self._inst = dict(self.m._instances)
        self._ti = {k: list(v) for k, v in self.m._task_instances.items()}

    def tearDown(self):
        self.m._tasks = self._tasks
        self.m._instances = self._inst
        self.m._task_instances = self._ti

    def _mk(self, tid):
        from services.live_batch import LiveBatchConfig, LiveInstance
        cfg = LiveBatchConfig(task_id=tid, name=tid, rooms=["1"], accounts=["x"],
                              strategy="round_robin", max_concurrent=3, enabled=True)
        self.m._tasks[tid] = cfg
        self.m._task_instances[tid] = []
        return cfg

    def _seed(self, tid, n=2, state="running"):
        from services.live_batch import LiveInstance
        ids = []
        for i in range(n):
            iid = f"{tid}_{i}"
            inst = LiveInstance(instance_id=iid, task_id=tid, room_url="1",
                                account="x", state=state)
            self.m._instances[iid] = inst
            self.m._task_instances[tid].append(iid)
            ids.append(iid)
        return ids

    def test_lb01_stop_clears_registry(self):
        """LB-01: 停止后实例必须从注册表摘除（否则占死并发槽位）。"""
        self._mk("g_lb01")
        self._seed("g_lb01", 2)
        self.m.stop_task("g_lb01")
        st = self.m.get_status("g_lb01")
        self.assertEqual(st["total_count"], 0, "停止后仍残留实例")
        self.assertEqual(st["running_count"], 0)
        self.assertEqual(self.m.get_global_status()["current_running"], 0,
                         "残留 running 记录占死了全局并发槽位")

    def test_lb02_restart_does_not_multiply(self):
        """LB-01 延伸：重启 3 次，实例数不得膨胀。"""
        self._mk("g_lb01b")
        for _ in range(3):
            self._seed("g_lb01b", 2)
            self.m.stop_task("g_lb01b")
        self.assertEqual(self.m.get_status("g_lb01b")["total_count"], 0)

    def test_lb02_duplicate_start_is_idempotent(self):
        """LB-02: 同 (房间,账号) 重复启动必须返回既有实例。"""
        cfg = self._mk("g_lb02")
        self._seed("g_lb02", 1, state="running")
        r = self.m._start_instance("g_lb02", "1", "x", cfg)
        self.assertTrue(r.get("ok"))
        self.assertIs(r.get("already"), True,
                      "重复启动未去重 ⇒ 会开两个 WS 抢同一房间")
        self.assertEqual(len(self.m._task_instances["g_lb02"]), 1)

    def test_lb10_fail_closed_on_bad_accounts(self):
        """LB-10: 空账号 / 未登记账号必须拒绝。"""
        from services.live_batch import LiveBatchConfig
        r1 = self.m.create_task(LiveBatchConfig(
            task_id="g_lb10a", name="a", rooms=["1"], accounts=[], enabled=True))
        self.assertFalse(r1["ok"])
        r2 = self.m.create_task(LiveBatchConfig(
            task_id="g_lb10b", name="b", rooms=["1"],
            accounts=["__不存在的账号__"], enabled=True))
        self.assertFalse(r2["ok"])


class TestLiveBatchTagPool(unittest.TestCase):
    """LB-12：词库复用私信策略标签（用户 2026-10-03 定调）。

    判据要点：① 复用 `app_config` 的 `live` 分区（唯一真源），不自造存储；
    ② **只取标签里显式配过的键** —— `app_config.get()` 未配置时回 schema 默认值
       （实测：标签不存在时 max_target 仍返 3），若直接采用会覆盖用户在任务里
       显式设的值（静默降级）。
    """

    def test_lb12_resolve_returns_empty_for_missing_tag(self):
        from services.live_batch import resolve_tag_send_params
        self.assertEqual(resolve_tag_send_params(""), {})
        self.assertEqual(resolve_tag_send_params("__不存在的标签__"), {})

    def test_lb12_never_returns_schema_defaults(self):
        """未显式配置的键**不得**出现在结果里（否则会覆盖用户显式配置）。"""
        from services.live_batch import resolve_tag_send_params
        r = resolve_tag_send_params("__不存在的标签__")
        for k in ("max_target", "interval", "delay_range", "dm_pool"):
            self.assertNotIn(k, r,
                             f"未配标签却返回了 {k}={r.get(k)!r} ⇒ 会覆盖用户显式配置")

    def test_lb12_pick_filters_disabled_and_dedups(self):
        """pick：跳过 enabled=False、按文本去重、随机抽（禁恒取第 0 条）。"""
        from services.live_batch import _make_pick
        self.assertIsNone(_make_pick([]))
        self.assertIsNone(_make_pick(None))
        pick = _make_pick([
            {"text": "甲", "enabled": True},
            {"text": "乙", "enabled": True},
            {"text": "丙", "enabled": False},
            {"text": "甲", "enabled": True},
        ])
        self.assertIsNotNone(pick)
        seen = {pick() for _ in range(300)}
        self.assertEqual(seen, {"甲", "乙"},
                         f"词库抽取语义错：{seen}（禁用项须排除、重复须去重）")

    def test_lb12_tag_id_is_real_config_field(self):
        import dataclasses
        from services.live_batch import LiveBatchConfig
        names = {f.name for f in dataclasses.fields(LiveBatchConfig)}
        self.assertIn("tag_id", names, "LiveBatchConfig 缺 tag_id")
        self.assertEqual(LiveBatchConfig(task_id="t", name="n").to_dict()["tag_id"], "")

    def test_lb12_dispatch_gets_pick_callback(self):
        """词库必须经 `pick_dm_message` 注入（契约里没有 dm_pool 字段）。"""
        code = _strip_comments(_read())
        self.assertIn("pick_dm_message=", code,
                      "DispatchCenter 构造未注入 pick_dm_message ⇒ 词库永远不生效")


# ===========================================================================
class TestLiveBatchConfigCenterSwitch(unittest.TestCase):
    """LB-13：总开关改由**配置中心**控制（用户 2026-10-03 定调「取消默认休眠的 env 机制」）。

    关键判据：**必须是函数、每次读都走 app_config**。
    若退回模块常量（import 时求值一次），配置中心改值**不生效** ——
    用户以为改了配置、实际行为没变，这正是本项目反复出现的「假成功」。
    """

    def test_lb13_switch_is_function_not_module_constant(self):
        import services.live_batch as m
        for fn in ("batch_enabled", "batch_max_concurrent", "batch_rate_limit_per_min"):
            self.assertTrue(callable(getattr(m, fn, None)),
                            f"{fn} 必须是函数（模块常量在 import 时求值，配置中心改值不生效）")
        # 不得再有裸常量
        # ⚠️ 判据只查**赋值形态**（`NAME = ...`），不能查「字符串里出现」——
        #   env 变量名 `DY_LIVE_BATCH_ENABLED` 合法包含该子串，docstring 里
        #   也会引用它。查「出现」会永远红（实测踩过），那是假门禁。
        code = _strip_comments(_read())
        import re
        for name in ("LIVE_BATCH_ENABLED", "LIVE_BATCH_MAX_CONCURRENT",
                     "LIVE_BATCH_RATE_LIMIT_PER_MIN"):
            self.assertIsNone(
                re.search(rf"^{name}\s*=", code, re.M),
                f"仍存在模块常量 {name} = ... ⇒ 配置中心改值不生效（须改为函数）")

    def test_lb13_default_is_fail_closed(self):
        """未显式配置时必须为 False（fail-closed）。

        优先级链：配置中心 → env(`DY_LIVE_BATCH_ENABLED`) → schema 默认 False。
        本测试进程顶部设了 env=1（供其它用例跑），故这里**临时清掉 env** 才能
        验到真正的默认值 —— 否则测的是 env 回退而非 schema 默认。
        """
        import os
        from services.live_batch import batch_enabled
        from services import app_config as ac
        stored = ac._load().get("general") or {}
        if "batch_enabled" in stored:
            self.skipTest("配置中心已显式设置 batch_enabled（非默认态）")
        bak = os.environ.pop("DY_LIVE_BATCH_ENABLED", None)
        try:
            self.assertFalse(batch_enabled(),
                             "未配置且无 env 时必须是 False（fail-closed）")
        finally:
            if bak is not None:
                os.environ["DY_LIVE_BATCH_ENABLED"] = bak

    def test_lb13_env_still_works_as_fallback(self):
        """env 降级回退必须保留（旧部署不破）—— 用户只要求改控制方式，不是删能力。"""
        import os
        from services.live_batch import batch_enabled
        from services import app_config as ac
        stored = ac._load().get("general") or {}
        if "batch_enabled" in stored:
            self.skipTest("配置中心已显式设置（优先级更高，非默认态）")
        bak = os.environ.get("DY_LIVE_BATCH_ENABLED")
        os.environ["DY_LIVE_BATCH_ENABLED"] = "1"
        try:
            self.assertTrue(batch_enabled(), "env=1 时应放行（回退链第③层）")
        finally:
            if bak is None:
                os.environ.pop("DY_LIVE_BATCH_ENABLED", None)
            else:
                os.environ["DY_LIVE_BATCH_ENABLED"] = bak

    def test_lb13_schema_has_the_three_fields(self):
        from services.app_config_schema import SECTIONS
        f = SECTIONS["general"]["fields"]
        for k in ("batch_enabled", "batch_max_concurrent", "batch_rate_limit_per_min"):
            self.assertIn(k, f, f"schema 缺 {k} ⇒ 配置中心 UI 无该项")
        # 开关必须落在**不受标签管**的语义下：显式不传 scope
        self.assertIn("ac.get(_SECTION, \"batch_enabled\"", _read())


class TestLiveBatchPersistence(unittest.TestCase):
    """LB-14：任务配置落 kv_store（用户 2026-10-03 定调「改成存 kv_store」）。"""

    KEY = "live_batch_tasks"

    def setUp(self):
        from database import get_kv_json
        self._bak = get_kv_json(self.KEY, None)

    def tearDown(self):
        from database import set_kv_json
        set_kv_json(self.KEY, self._bak if self._bak is not None else {})

    def test_lb14_roundtrip_via_store(self):
        """写盘 → 新管理器恢复：全部字段逐字一致。"""
        from services.live_batch import LiveBatchManager, LiveBatchConfig
        from database import get_kv_json
        m = LiveBatchManager.__new__(LiveBatchManager)   # 绕开单例
        m._tasks, m._instances = {}, {}
        m._task_instances, m._managed_ids = {}, set()
        m._state_lock = __import__("threading").RLock()
        cfg = LiveBatchConfig(task_id="rt1", name="往返", rooms=["1", "2"],
                              accounts=["a"], strategy="fixed", max_concurrent=5,
                              enabled=True, interval=77.0, max_target=9,
                              tag_id="tagX")
        m._tasks["rt1"] = cfg
        m._task_instances["rt1"] = []
        m._managed_ids.add("rt1")
        m._save_to_store()

        raw = get_kv_json(self.KEY, {}) or {}
        self.assertIn("rt1", raw, "未落盘")
        self.assertEqual(raw["rt1"]["max_target"], 9)
        self.assertEqual(raw["rt1"]["tag_id"], "tagX")

        # 反序列化
        back = LiveBatchManager._cfg_from_dict("rt1", raw["rt1"])
        self.assertEqual(back.to_dict(), cfg.to_dict(), "往返字段不一致")

    def test_lb14_corrupt_record_does_not_block_others(self):
        """单条损坏**不得**拖垮整批恢复。"""
        from services.live_batch import LiveBatchManager
        good = LiveBatchManager._cfg_from_dict("g", {"name": "好的", "rooms": ["1"],
                                                     "accounts": ["a"]})
        self.assertEqual(good.name, "好的")
        with self.assertRaises(Exception):
            LiveBatchManager._cfg_from_dict("b", "不是 dict")

    def test_lb14_save_keeps_unmanaged_keys(self):
        """🔴 防「以内存覆盖 kv」：kv 里**本进程未管理**的 key 必须原样保留。

        实测坐实的缺陷：原实现只写内存快照，导致任何一次写操作都会
        静默删除恢复时被跳过的脏记录。
        """
        import threading
        from database import get_kv_json, set_kv_json
        from services.live_batch import LiveBatchManager, LiveBatchConfig
        set_kv_json(self.KEY, {"foreign": "别的写入方", "bad": "脏记录"})

        m = LiveBatchManager.__new__(LiveBatchManager)
        m._tasks, m._instances = {}, {}
        m._task_instances = {}
        m._managed_ids = {"t1"}          # t1 曾归本进程管
        m._state_lock = threading.RLock()
        cfg = LiveBatchConfig(task_id="t2", name="本进程新建", rooms=["9"],
                              accounts=["z"])
        m._tasks["t2"] = cfg
        m._managed_ids.add("t2")
        m._save_to_store()

        keys = set((get_kv_json(self.KEY, {}) or {}).keys())
        self.assertIn("foreign", keys, "未管理的 key 被误删")
        self.assertIn("bad", keys, "脏记录被误删（应保留待人工处理）")
        self.assertIn("t2", keys)

    def test_lb14_delete_removes_only_managed(self):
        """删除判据方向：**曾受管 且 已不在内存** ⇒ 只删它自己。"""
        import threading
        from database import get_kv_json, set_kv_json
        from services.live_batch import LiveBatchManager, LiveBatchConfig
        set_kv_json(self.KEY, {"keep": {"name": "留", "rooms": ["1"], "accounts": ["a"]},
                               "gone": {"name": "删", "rooms": ["2"], "accounts": ["b"]}})

        m = LiveBatchManager.__new__(LiveBatchManager)
        m._tasks, m._instances = {}, {}
        m._task_instances = {}
        m._managed_ids = {"keep", "gone"}
        m._state_lock = threading.RLock()
        m._tasks["keep"] = LiveBatchConfig(task_id="keep", name="留",
                                          rooms=["1"], accounts=["a"])
        m._task_instances["keep"] = []
        m._tasks["gone"] = LiveBatchConfig(task_id="gone", name="删",
                                          rooms=["2"], accounts=["b"])
        m._task_instances["gone"] = []

        r = m.delete_task("gone")
        self.assertTrue(r["ok"])
        keys = set((get_kv_json(self.KEY, {}) or {}).keys())
        self.assertNotIn("gone", keys, "已删任务未从 kv 移除（重启会复活）")
        self.assertIn("keep", keys, "误删了另一个任务")



# 负控（Negative Controls）
# ===========================================================================
# 纪律（skill 铁律）：**门禁必须能变红**。先造缺陷证明它会红，再信它报绿。
# 判据与正式门禁**同源复用**，杜绝「负控与门禁两套口径」导致假绿。
# 只在内存副本上变异，**不碰磁盘上的真实文件**。

def _has_bare_dispatch(src: str) -> bool:
    """LB-03 判据（与 TestLiveBatchSourceInvariants 同口径）。"""
    import re
    return bool(re.search(r"DispatchCenter\(\s*\)", _strip_comments(src)))


def _has_bare_hook(src: str) -> bool:
    """LB-04 判据。"""
    import re
    return bool(re.search(r"LiveChatHook\(\s*\)", _strip_comments(src)))


def _has_unawaited_start(src: str) -> bool:
    """LB-05 判据（行内判定：出现调用但同行无 await）。"""
    code = _strip_comments(src)
    return any("inst._dispatch.start()" in ln and "await" not in ln
               for ln in code.splitlines())


def _has_module_const_enabled(src: str) -> bool:
    """LB-13 判据（修正版）：只认**赋值形态**的模块常量。

    ⚠️ 不得查「字符串出现」—— env 名 `DY_LIVE_BATCH_ENABLED` 与 docstring
    都会合法包含该子串，照「出现」判会永远红（假门禁，实测踩过）。
    """
    import re
    return bool(re.search(r"^LIVE_BATCH_ENABLED\s*=", _strip_comments(src), re.M))


def _has_registry_leak(src: str) -> bool:
    """LB-01 判据：停止后未从注册表摘除。"""
    return ("self._instances.pop(instance_id, None)" not in _strip_comments(src)
            or "lst.remove(instance_id)" not in _strip_comments(src))


def self_check_negative_controls():
    """返回 [(负控名, 是否被门禁捕获)]。"""
    src = _read()
    out = []

    def probe(name, mutated, detector):
        caught = detector(mutated)   # 缺陷被注入后，判据应当「发现缺陷」
        out.append((name, caught))

    # NC-1: 无参 DispatchCenter()
    m1 = src.replace("from core.dispatch import DispatchCenter",
                     "from core.dispatch import DispatchCenter\n"
                     "    _BAD = DispatchCenter()", 1)
    assert m1 != src, "NC1 注入失败（锚点不存在）"
    probe("NC1 无参 DispatchCenter()", m1, _has_bare_dispatch)

    # NC-2: 无参 LiveChatHook()
    m2 = src.replace("hook = LiveChatHook(live_id, auth, dispatch)",
                     "hook = LiveChatHook()", 1)
    assert m2 != src, "NC2 注入失败"
    probe("NC2 无参 LiveChatHook()", m2, _has_bare_hook)

    # NC-3: dispatch.start() 去掉 await
    m3 = src.replace("await inst._dispatch.start()", "inst._dispatch.start()", 1)
    assert m3 != src, "NC3 注入失败"
    probe("NC3 start() 未 await", m3, _has_unawaited_start)

    # NC-4: 恢复注册表泄漏（去掉摘除两行）
    m4 = src.replace("            self._instances.pop(instance_id, None)\n", "", 1)
    assert m4 != src, "NC4 注入失败"
    probe("NC4 实例注册表泄漏", m4, _has_registry_leak)

    # NC-5: 去掉 stop_hard（退回不存在的 stop()）
    m5 = src.replace('getattr(inst._dispatch, "stop_hard", None)', "None", 1)
    assert m5 != src, "NC5 注入失败"
    probe("NC5 未调用 stop_hard", m5,
          lambda s: "stop_hard" not in _strip_comments(s))

    # NC-6: 去掉 pick_dm_message 注入（词库永远不生效）
    m6 = src.replace("pick_dm_message=_make_pick(_pool),", "", 1)
    assert m6 != src, "NC6 注入失败"
    probe("NC6 未注入 pick_dm_message", m6,
          lambda s: "pick_dm_message=" not in _strip_comments(s))

    # NC-7: 退回「以内存覆盖 kv」写盘（未管理的 key 会被误删）
    leak2 = """            base = get_kv_json(KEY, {}) or {}
            if not isinstance(base, dict):
                base = {}
"""
    m7 = src.replace(leak2, "            base = {}\n", 1)
    assert m7 != src, "NC7 注入失败"
    probe("NC7 以内存覆盖 kv（误删未管理 key）", m7,
          lambda s: "base = get_kv_json(KEY, {})" not in _strip_comments(s))

    # NC-8: 退回模块常量（配置中心改值不生效）
    m8 = src.replace("def batch_enabled() -> bool:",
                     "LIVE_BATCH_ENABLED = False\ndef batch_enabled() -> bool:", 1)
    assert m8 != src, "NC8 注入失败"
    probe("NC8 存在模块常量", m8, _has_module_const_enabled)

    # ── 正控：未变异的真实源码必须「无缺陷」──
    for nm, det in [("NC1", _has_bare_dispatch), ("NC2", _has_bare_hook),
                    ("NC3", _has_unawaited_start), ("NC4", _has_registry_leak),
                    ("NC5", lambda s: "stop_hard" not in _strip_comments(s)),
                    ("NC6", lambda s: "pick_dm_message=" not in _strip_comments(s)),
                    ("NC7", lambda s: "base = get_kv_json(KEY, {})" not in _strip_comments(s)),
                    ("NC8", _has_module_const_enabled)]:
        if det(src):
            out.append((f"{nm} 正控（真实源码应无缺陷）", False))
        else:
            out.append((f"{nm} 正控", True))

    return out


if __name__ == "__main__":
    if "--negative" in sys.argv:
        print("=== 负控（注入缺陷 → 门禁必须变红）+ 正控 ===")
        ok = True
        for name, caught in self_check_negative_controls():
            tag = "GATE_GOES_RED ✔" if caught else "FAIL_GATE ✘（假门禁！）"
            print(f"  {name:34s} {tag}")
            if not caught:
                ok = False
        print("负控结果:", "PASS（门禁会变红，且真实源码无缺陷）" if ok
              else "FAIL（存在假门禁！）")
        sys.exit(0 if ok else 1)

    unittest.main(verbosity=2)
