# coding=utf-8
"""账号能力矩阵门禁（2026-10-01）。

## 守什么

「凭证是否有效」在本项目长期是**一个含糊的词**：UI 只报 `wp.level` 单值，
而同一份 cookie 在**不同服务端端点**上的裁决**互相独立**（实测 A/B 双账号：
尚进=主站承认/身份一致/imapi 可写；张老师=主站拒(8)/身份漂移/imapi 拒写，
但**两者都能取到贡献榜**）。单一 verdict 会驱动**无效重扫**——而重扫会触碰
passport 验证，是最强风控信号。

本门禁钉死三件事：
  ① 判据**必须分面**（5 个面各有明确 key），且**不得**把「未知」折成「拒绝」；
  ② 用途推导遵守三态纪律（`unknown` → 「待定」而非「不可用」）；
  ③ 矩阵**复用既有探针**、不新造判据、不外发新请求面。

全确定性：纯函数断言 + 源码级断言，**不触网、不读真凭证**。
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("FLOWCAP_APP_ROOT", r"C:\temp\flowcap_design")

import auto_dm.accounts as acc  # noqa: E402

_BE = os.path.dirname(os.path.abspath(__file__))


class CapabilityMatrixGate(unittest.TestCase):
    """M1-M3：面集合 / 三态纪律 / 用途推导。"""

    # ── M1：面集合与 key 契约 ────────────────────────────────────────────
    def test_m1_faces_are_partitioned(self):
        """M1：5 个面必须各有独立 key（分面的**结构**判据）。"""
        src = open(os.path.join(_BE, "auto_dm", "accounts.py"), encoding="utf-8").read()
        seg = src[src.find("def capability_matrix"):]
        for key in ("static", "session", "identity", "im_write", "live_read"):
            self.assertIn(f'_add("{key}"', seg, f"缺面：{key}")

    # ── M2：三态纪律（unknown ≠ fail）──────────────────────────────────
    def test_m2_unknown_never_collapses_to_fail(self):
        """M2：任一前提面为 unknown 时，用途**不得**判「不可用」（核心反无效重扫判据）。"""
        p = acc.derive_purposes({"session": "ok", "identity": "unknown", "im_write": "ok",
                                 "live_read": "ok", "static": "ok"})
        self.assertEqual(p["danmaku_decrypt"]["state"], "unknown")
        self.assertIsNone(p["danmaku_decrypt"]["usable"])
        self.assertIn("待定", p["danmaku_decrypt"]["state_label"])

    def test_m2b_fail_does_make_unusable(self):
        """M2b（负控）：前提面明确 fail 时**必须**判不可用（防 M2 被扩大成永不判死）。"""
        p = acc.derive_purposes({"session": "fail", "identity": "ok", "im_write": "ok",
                                 "live_read": "ok", "static": "ok"})
        self.assertIs(p["danmaku_decrypt"]["usable"], False)
        self.assertEqual(p["danmaku_decrypt"]["state"], "fail")

    # ── M3：用途语义 ───────────────────────────────────────────────────
    def test_m3_purposes_map_to_right_faces(self):
        """M3：三个用途必须挂到**正确的前提面**（防挂错面导致误判）。"""
        allok = {"static": "ok", "session": "ok", "identity": "ok",
                 "im_write": "ok", "live_read": "ok"}
        p = acc.derive_purposes(allok)
        self.assertTrue(p["danmaku_decrypt"]["usable"])
        self.assertTrue(p["im_write"]["usable"])
        self.assertTrue(p["live_read"]["usable"])
        # imapi 写被拒**只**影响「私信发送」，不得牵连直播数据读
        p2 = acc.derive_purposes({**allok, "im_write": "fail"})
        self.assertIs(p2["im_write"]["usable"], False)
        self.assertTrue(p2["live_read"]["usable"],
                        "写被拒不应牵连直播数据读（实测张老师即此形态：写拒/读可）")

    def test_m3b_live_read_independent_of_main_site_session(self):
        """M3b：实测形态回归 —— 主站会话拒但直播数据读可用（张老师）。"""
        p = acc.derive_purposes({"static": "ok", "session": "fail", "identity": "fail",
                                 "im_write": "fail", "live_read": "ok"})
        self.assertTrue(p["live_read"]["usable"], "直播数据读必须独立于主站会话判定")
        self.assertIs(p["im_write"]["usable"], False)


class LiveRoomRefGate(unittest.TestCase):
    """M6-M8：**生效直播间**唯一真源 + 调用方契约 + unset 语义（2026-10-01 修）。

    ## 守的缺陷（实测，非假设）

    用户在「目标直播间」配好了直播间（持久在 `kv config.live_url`，
    形态为**搜索页 URL**：`…?live_web_rid=697879973787`），但 `settings.live_url`
    在进程内**为空**（两者不同步），且 `api/accounts.py` 的 check 端点
    **不传 live_id** ⇒ `live_read` 面恒走「零网络跳过」判 `unset` ⇒ UI 恒显
    「未配置」⇒ 把所有面板都通的账号误报成缺配，**驱动用户无效重扫**
    （触碰 passport = 最强风控信号）—— 恰是本矩阵的设计初衷要根治的失效模式。
    """

    def test_m6_live_room_ref_prefers_kv_and_parses_search_url(self):
        """M6：真源优先 **kv 持久配置**；形态解析在「无 kv 回退」下单独验。

        用**运行中的真实配置**（只读），不 mock —— 因为本缺陷正是「读错存储层」。
        """
        import database as _db
        import config as _cfg
        _orig_kv, _orig_url = _db.get_kv_json, _cfg.settings.live_url
        _orig_lid = getattr(_cfg.settings, "live_id", "")

        def _kv_empty(key, default=None):
            return {} if key == "config" else _orig_kv(key, default)

        # ── A：无任何持久配置时，函数**只**看显式入参 ⇒ 可纯验形态解析 ──
        try:
            _db.get_kv_json = _kv_empty
            _cfg.settings.live_url = ""
            if hasattr(_cfg.settings, "live_id"):
                _cfg.settings.live_id = ""
            self.assertEqual(
                acc.live_room_ref("https://www.douyin.com/jingxuan/search/x?live_web_rid=123456"),
                "123456", "搜索页形态未解析")
            self.assertEqual(acc.live_room_ref("https://live.douyin.com/7890"), "7890",
                             "直播间页形态未解析")
            self.assertEqual(acc.live_room_ref("697879973787"), "697879973787", "裸房间号未透传")
            self.assertEqual(acc.live_room_ref(""), "", "无配置 + 空入参 ⇒ 空")
            self.assertEqual(acc.live_room_ref("http://example.com/nope"), "",
                             "无配置 + 无房间号的 URL ⇒ 空（不得瞎猜）")
        finally:
            _db.get_kv_json = _orig_kv
            _cfg.settings.live_url = _orig_url
            if hasattr(_cfg.settings, "live_id"):
                _cfg.settings.live_id = _orig_lid

        # ── B：恢复后，真源必须产出**纯数字房间号**（或真的没配时为空）──
        ref = acc.live_room_ref()
        self.assertRegex(ref, r"^(\d*)$", "房间号必须是纯数字（或空）")
        # 显式入参优先于 kv（kv 有值时也不该被覆盖）
        self.assertEqual(acc.live_room_ref("https://live.douyin.com/999"), "999",
                         "显式入参应优先")

    def test_m7_live_read_prefers_kv_over_settings(self):
        """M7（**负控**）：kv 与 settings 都为空 ⇒ **必须**判 `unset`（防「假 ok」）。

        反向也要防：这个修复**不得**把「真没配」变成「假可用」。

        🔴 2026-10-02 环境绑定修复：本用例原先硬编码真实账号
        `尚进工伤小助理`。`capability_matrix` 在 `env_path_of(name)` 为空时
        **提前 return `caps: []`** ⇒ 换台机器 / 换数据根（该账号不存在）就
        `IndexError: list index out of range` —— 报的是「取不到元素」，
        与被测的「live_read 该判 unset」毫无关系。
        且这**违背本文件自己的声明**（头部：「全确定性：不触网、不读真凭证」）。

        现把「账号存在」也纳入桩：`env_path_of` 指向一个**桩 .env 路径**，
        四个探针全部打桩 ⇒ 被测判据（无直播间 ⇒ unset）**零网络**达成。
        """
        import database as _db
        import config as _cfg
        import tempfile
        import os as _os

        _FAKE = "桩账号_门禁专用"
        _stub_env = _os.path.join(tempfile.gettempdir(), "gate_stub_account.env")
        _orig_kv, _orig_url = _db.get_kv_json, _cfg.settings.live_url
        _orig_lid = getattr(_cfg.settings, "live_id", "")
        _orig_env_of = acc.env_path_of
        _orig_creds = acc.credentials_complete
        _orig_sess = acc.live_session_state
        _orig_uidv = acc.uid_identity_verdict
        _orig_imw = acc.probe_im_write
        try:
            def _kv(key, default=None):        # 只清 config 键，其余照常
                return {} if key == "config" else _orig_kv(key, default)
            _db.get_kv_json = _kv
            _cfg.settings.live_url = ""
            if hasattr(_cfg.settings, "live_id"):
                _cfg.settings.live_id = ""

            # 账号「存在」：env_path_of 必须给出路径，否则矩阵提前返回空 caps
            acc.env_path_of = lambda name, *a, **k: (
                _stub_env if name == _FAKE else _orig_env_of(name, *a, **k))
            # 四个探针全部打桩 ⇒ 零网络，且状态固定（不依赖任何真凭证）
            acc.credentials_complete = lambda p, *a, **k: (False, "桩：不完整")
            acc.live_session_state = lambda n, **k: (None, "桩：未知")
            acc.uid_identity_verdict = lambda n, **k: (
                None, "unknown", "桩", "桩：未取证")
            acc.probe_im_write = lambda n, **k: (False, "桩：不可写")

            self.assertEqual(acc.live_room_ref(), "", "无配置时不得解析出房间号")
            m = acc.capability_matrix(_FAKE, force=False, live_id="")
            lr = [c for c in m["caps"] if c["key"] == "live_read"][0]
            self.assertEqual(lr["state"], "unset", "无配置时必须判 unset（不得假 ok）")
            p = m["purposes"]["live_read"]
            self.assertEqual(p["state_label"], "未配置直播间",
                             "label 须具体，不能只写模糊的「未配置」")
        finally:
            _db.get_kv_json = _orig_kv
            _cfg.settings.live_url = _orig_url
            if hasattr(_cfg.settings, "live_id"):
                _cfg.settings.live_id = _orig_lid
            acc.env_path_of = _orig_env_of
            acc.credentials_complete = _orig_creds
            acc.live_session_state = _orig_sess
            acc.uid_identity_verdict = _orig_uidv
            acc.probe_im_write = _orig_imw

    def test_m8_api_check_passes_live_room_ref(self):
        """M8：check 端点**必须显式**传生效直播间（防「调用方漏传」复发）。"""
        src = open(os.path.join(_BE, "api", "accounts.py"), encoding="utf-8").read()
        seg = src[src.find("capability_matrix"):]
        self.assertIn("live_id=_live_ref", seg,
                      "check 端点未传 live_id ⇒ live_read 面会恒判 unset（缺陷复发）")
        self.assertIn("live_room_ref()", src, "未取生效直播间真源")

    def test_m8b_unset_says_not_a_credential_problem(self):
        """M8b：unset 的 hint/actions **必须**写明「不是凭证问题」，防驱动无效重扫。"""
        src = open(os.path.join(_BE, "auto_dm", "accounts.py"), encoding="utf-8").read()
        self.assertIn("不是凭证问题", src, "unset 未明确「不是凭证问题」")
        self.assertIn("勿因此重扫", src, "actions 未劝阻因 unset 而重扫")


class CapabilityMatrixSourceGate(unittest.TestCase):
    """M4-M5：源码级纪律（复用既有探针 / 不新造判据 / 不额外发请求面）。"""

    def test_m4_reuses_existing_probes(self):
        """M4：矩阵必须调用**既有**探针，不得自造等效判据（防判据漂移）。"""
        src = open(os.path.join(_BE, "auto_dm", "accounts.py"), encoding="utf-8").read()
        seg = src[src.find("def capability_matrix"):src.find("def _probe(")]
        for fn in ("credentials_complete", "live_session_state",
                   "uid_identity_verdict", "probe_im_write"):
            self.assertIn(fn, seg, f"未复用既有探针：{fn}")
        # 直播数据读走既有 dy_apis，不自造 HTTP
        self.assertIn("DouyinAPI.get_live_info", seg)
        self.assertIn("DouyinAPI.get_rank_list", seg)
        self.assertNotIn("requests.get", seg, "矩阵内不得自造裸 HTTP 请求")

    def test_m5_identity_uses_state_not_label(self):
        """M5：身份面三态必须按 **state** 判定（曾误用中文 label 比 'ok' ⇒ 可用误报未知）。"""
        src = open(os.path.join(_BE, "auto_dm", "accounts.py"), encoding="utf-8").read()
        seg = src[src.find("def capability_matrix"):src.find("def _probe(")]
        self.assertIn('_ok is True', seg, "身份面未按 state 三态判定")
        self.assertNotIn('_lbl == "ok"', seg, "仍在用 label（中文串）判定三态")

    def test_m5b_rescan_advice_is_not_blanket(self):
        """M5b：建议里**不得**出现「一律重扫」；写被拒时须显式劝阻重扫。"""
        src = open(os.path.join(_BE, "auto_dm", "accounts.py"), encoding="utf-8").read()
        seg = src[src.find("def capability_matrix"):src.find("def _probe(")]
        self.assertIn("不要盲目重扫", seg)
        self.assertIn("优先确认直播间是否开播", seg)


if __name__ == "__main__":
    unittest.main(verbosity=2)