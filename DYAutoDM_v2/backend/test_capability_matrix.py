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
os.environ.setdefault("DY_APP_ROOT", r"C:\temp\dyautodm_design")

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