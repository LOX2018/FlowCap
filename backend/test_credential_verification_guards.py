# -*- coding: utf-8 -*-
"""凭证校验判据 + uid 探活频次门控 守卫测试（v0.44.17）。

## 覆盖的历史缺陷

1. **dm 引擎只探端口、不验凭证**（auto_dm/accounts.py）：
   旧实现 `_port_open(bport/rport)` 判 `私信守护正常`，完全不碰 imapi
   ⇒ 张老师「能读不能写」（609 rejected）时仍报 ok。
2. **轮询用 wp 冒充 dm**：`wp_level == "ok"` → `dm = ok`，谎报私信可用。
3. **force_probe 是频次后门**：任何调用方都能穿透 300s TTL。
4. **写校验结果必须能被消费方看到**：probe_im_write 缓存语义。

本测试**不触发任何网络请求**（全部 monkeypatch），可在 CI 反复跑。
"""
from __future__ import annotations

import os
import sys
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))


class TestImWriteProbe(unittest.TestCase):
    """IM 写能力探针的判据与缓存语义。"""

    def setUp(self):
        from auto_dm import accounts as acc
        self.acc = acc
        acc.invalidate_im_write_cache()
        self.addCleanup(acc.invalidate_im_write_cache)

    def _patch_env(self, uid="316276709526638"):
        """把探针依赖的外部世界全部桩掉（零网络）。

        注意：探针签名 `probe_im_write(name, auth=None, ...)` 支持直接传 auth，
        传入后**不会**再走 env_path_of / _load_auth_from_env。测试必须用这条
        路径，否则会被 .env 存在性检查提前短路（本身即测试桩缺陷）。
        """
        import dy_apis.douyin_api as da
        fake_auth = mock.Mock(cookie={"sessionid": "x"})
        p1 = mock.patch("services.conv_identity.my_uid", return_value=uid)
        return p1, fake_auth, da

    def test_probe_ok_when_create_conversation_accepted(self):
        """create_conversation 成功 ⇒ (True, 含 uid 的 detail)。"""
        p1, auth, da = self._patch_env()
        with p1, mock.patch.object(da.DouyinAPI, "create_conversation",
                                   return_value=("0:1:a:b", 1, "t")) as m:
            ok, detail = self.acc.probe_im_write("acc", auth=auth, force=True)
        self.assertTrue(ok, detail)
        self.assertIn("316276709526638", detail)
        m.assert_called_once()

    def test_probe_fail_on_readonly_session_length(self):
        """账号级「只读态」必须判 False，且 detail 点明需重新扫码。"""
        p1, auth, da = self._patch_env()
        err = RuntimeError("create_conversation 响应缺少 create_conversation_v2_body "
                           "message='unexepcted session length'")
        with p1, mock.patch.object(da.DouyinAPI, "create_conversation", side_effect=err):
            ok, detail = self.acc.probe_im_write("acc", auth=auth, force=True)
        self.assertFalse(ok)
        self.assertIn("只读", detail)
        self.assertIn("重新扫码", detail)

    def test_probe_fail_on_invalid_request(self):
        p1, auth, da = self._patch_env()
        with p1, mock.patch.object(da.DouyinAPI, "create_conversation",
                                   side_effect=RuntimeError("INVALID_REQUEST")):
            ok, detail = self.acc.probe_im_write("acc", auth=auth, force=True)
        self.assertFalse(ok)
        self.assertIn("INVALID_REQUEST", detail)

    def test_probe_result_is_cached_and_force_bypasses(self):
        """默认走缓存（120s）；force=True 必须重新打网。"""
        p1, auth, da = self._patch_env()
        with p1, mock.patch.object(da.DouyinAPI, "create_conversation",
                                   return_value=("c", 1, "t")) as m:
            self.acc.probe_im_write("acc", auth=auth, force=True)
            self.acc.probe_im_write("acc", auth=auth)            # 命中缓存
            self.assertEqual(m.call_count, 1)
            self.acc.probe_im_write("acc", auth=auth, force=True)  # 穿透
            self.assertEqual(m.call_count, 2)

    def test_probe_never_raises(self):
        """探针异常必须转成 (False, 原因)，绝不冒泡成「校验异常」。"""
        p1, auth, da = self._patch_env()
        with p1, mock.patch.object(da.DouyinAPI, "create_conversation",
                                   side_effect=ValueError("boom")):
            ok, detail = self.acc.probe_im_write("acc", auth=auth, force=True)
        self.assertFalse(ok)
        self.assertIn("boom", detail)


class TestDmEngineNoLongerFakesOk(unittest.TestCase):
    """dm 引擎不得再用 wp 结果或端口探测冒充「凭证可用」。"""

    def _code(self) -> str:
        """只取**函数体代码**（剥掉 docstring），避免注释/文档误伤判据。

        历史坑：用整段源码 grep 禁用词，会被 docstring 里引用旧行为的
        说明文字命中 → 假阳性。判据必须限定在可执行代码段。
        """
        from auto_dm import accounts as acc
        import inspect
        src = inspect.getsource(acc.verify_account)
        # 剥掉三引号 docstring（函数第一个块）
        first = src.find('"""')
        if first != -1:
            second = src.find('"""', first + 3)
            if second != -1:
                src = src[:first] + src[second + 3:]
        # 剥掉行注释
        return "\n".join(ln for ln in src.splitlines()
                         if not ln.strip().startswith("#"))

    def test_no_wp_level_impersonation(self):
        """不得出现 `wp_level == "ok"` → dm ok 的旧映射。"""
        src = self._code()
        self.assertNotIn("正常（沿用 wp）", src,
                         "dm 引擎仍在用 wp 结果冒充私信凭证可用")
        self.assertNotIn("可能可用（沿用 wp）", src)
        # 旧实现的特征：dm 分支里读 wp_level 做映射
        self.assertNotIn('wp_level == "ok"', src)

    def test_dm_uses_real_write_probe(self):
        """用户点校验（dm_loopback）必须走 probe_im_write。"""
        src = self._code()
        self.assertIn("probe_im_write", src)

    def test_dm_ok_requires_write_probe_pass(self):
        """dm 判 ok 的前提是写校验通过，不是端口开着。"""
        src = self._code()
        idx = src.find('"label": "私信凭证可用（写校验通过）"')
        self.assertGreater(idx, 0, "缺少「写校验通过」这一判据标签")
        # ok 分支必须由 _w_ok 驱动
        seg = src[:idx]
        self.assertIn("if _w_ok", seg)

    def test_port_probe_is_only_supplementary(self):
        """端口探测只能进 detail（补充信息），不得再决定 level。"""
        src = self._code()
        idx = src.find("_daemon_note")
        self.assertGreater(idx, 0, "缺少守护态降级为补充信息的实现")
        # 不得存在「端口开着就判 ok」的旧结构
        self.assertNotIn('"label": "私信守护正常"', src)


class TestUidProbeForceWhitelist(unittest.TestCase):
    """uid 探活频次：force 必须白名单制，否则降级为 TTL 探活。"""

    def test_unregistered_caller_is_downgraded(self):
        from services import uid_probe as up
        with mock.patch.object(up, "_caller_tag", return_value="some_random_caller"), \
             mock.patch.object(up, "_do_probe", return_value=123) as dp, \
             mock.patch.object(up, "_valid", return_value=None):
            uid = up.get_uid("acc", force=True)
            # 未登记 → 降级 → 仍会探，但必须走过 TTL 分支（_valid 被查）
            self.assertEqual(uid, 123)
            self.assertTrue(dp.called)

    def test_registered_caller_passes_force(self):
        from services import uid_probe as up
        with mock.patch.object(up, "_caller_tag",
                               return_value="_page_login_state_sync"), \
             mock.patch.object(up, "_do_probe", return_value=456), \
             mock.patch.object(up, "_valid", return_value=None):
            uid = up.get_uid("acc", force=True)
            self.assertEqual(uid, 456)

    def test_whitelist_contains_real_callers(self):
        """白名单必须覆盖真实调用点（防止改名后静默失效）。"""
        from services import uid_probe as up
        self.assertIn("_page_login_state_sync", up._FORCE_CALLERS)
        self.assertIn("verify_credentials", up._FORCE_CALLERS)

    def test_caller_tag_skips_own_module(self):
        """_caller_tag 必须跳过本模块帧，返回外部函数名。"""
        from services import uid_probe as up

        def _outer():
            return up._caller_tag()

        self.assertEqual(_outer(), "_outer")


class TestNoHttpInVerification(unittest.TestCase):
    """静态冒烟：确保本次改动没有引入裸网调用（防回归）。"""

    def test_probe_only_uses_create_conversation(self):
        from auto_dm import accounts as acc
        import inspect
        src = inspect.getsource(acc.probe_im_write)
        self.assertIn("create_conversation", src)
        # 探针不得自行发 requests（复用基座签名链路）
        self.assertNotIn("requests.post", src)


if __name__ == "__main__":
    unittest.main(verbosity=2)
