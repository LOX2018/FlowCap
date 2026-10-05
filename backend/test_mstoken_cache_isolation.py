# -*- coding: utf-8 -*-
"""msToken 缓存按账号隔离 —— 回归测试（2026-09-17 OCR 审查 HIGH）。

缺陷：`utils/mstoken.py` 的 `_cache` 原为**单条**模块级 dict（不含 ttwid 键），
多账号下 A 账号的 msToken 会被 B 账号命中复用 —— msToken 与 ttwid/cookie 配套，
串用会致签名校验失败甚至触发风控。

本测试直接加载 `utils/mstoken.py`（独立加载，绕开项目重依赖），
注入假响应验证：
  1. 不同 ttwid 各自取到**各自的** token（旧实现会返回同一个）；
  2. 同 ttwid 命中缓存（不重复打网）；
  3. 缓存有容量上限，不会无界增长。

用法：cd FlowCap/backend && python test_mstoken_cache_isolation.py
"""
import ast
import importlib.util
import os
import sys
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
MOD = os.path.join(HERE, "utils", "mstoken.py")


def _load():
    """加载 mstoken.py 并把 make_requests() 等重依赖替换为最小桩。"""
    with open(MOD, encoding="utf-8") as fh:
        src = fh.read()

    # 伪造 utils.strdata_pure / utils.fingerprint / utils.tls_policy
    pkg = types.ModuleType("utils")
    pkg.__path__ = [os.path.join(HERE, "utils")]
    for name in ("utils.strdata_pure", "utils.fingerprint", "utils.tls_policy"):
        m = types.ModuleType(name)
        if name.endswith("strdata_pure"):
            m.build_report_body = lambda: "ENV"
        elif name.endswith("fingerprint"):
            m.get_profile = lambda: {"ua": "UA"}
        else:
            m.tls_verify = lambda: True
        sys.modules[name] = m
    sys.modules.setdefault("utils", pkg)

    spec = importlib.util.spec_from_file_location("mstoken_under_test", MOD)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class FakeResp:
    def __init__(self, token):
        self.headers = {"x-ms-token": token}


class TestMsTokenCacheIsolation(unittest.TestCase):
    def setUp(self):
        self.mod = _load()

    def _call(self, tw, token):
        with mock.patch.object(self.mod.requests, "post",
                               return_value=FakeResp(token)) as p:
            out = self.mod.get_mstoken(ttwid=tw)
        return out, p.call_count

    def test_different_accounts_get_their_own_token(self):
        """账号 A、B 必须各自取到自己的 token（旧实现会串用同一个）。"""
        ta, _ = self._call("ttwid_A", "TOKEN_A")
        tb, _ = self._call("ttwid_B", "TOKEN_B")
        self.assertEqual(ta, "TOKEN_A")
        self.assertEqual(tb, "TOKEN_B",
                         "B 账号拿到了 A 的缓存 token（跨账号串用）")

    def test_same_account_hits_cache(self):
        """同账号第二次应命中缓存，不再打网。"""
        t1, n1 = self._call("ttwid_A", "TOKEN_A")
        t2, n2 = self._call("ttwid_A", "TOKEN_A2")
        self.assertEqual(n1, 1)
        self.assertEqual(n2, 0, "同账号未命中缓存，重复打网")
        self.assertEqual(t1, t2)

    def test_no_ttwid_still_works_and_is_isolated(self):
        """ttwid 为空（异常路径）也不得与具名账号串用。"""
        t_empty, _ = self._call(None, "TOKEN_EMPTY")
        t_named, _ = self._call("ttwid_A", "TOKEN_A")
        self.assertEqual(t_empty, "TOKEN_EMPTY")
        self.assertEqual(t_named, "TOKEN_A")

    def test_cache_bounded(self):
        """缓存有容量上限，不会随账号数无界增长。"""
        for i in range(self.mod._CACHE_MAX + 20):
            self._call(f"ttwid_{i}", f"TOKEN_{i}")
        self.assertLessEqual(len(self.mod._cache), self.mod._CACHE_MAX,
                             "缓存未受容量约束，存在无界增长")

    def test_old_impl_would_leak(self):
        """对照：复刻旧实现（单条缓存），证明缺陷真实存在。"""
        old = {"token": "", "ts": 0}

        def old_get(tw, token):
            import time as _t
            if old["token"] and (_t.time() - old["ts"] < 600):
                return old["token"]          # ← 不区分账号
            old["token"], old["ts"] = token, _t.time()
            return token

        a = old_get("ttwid_A", "TOKEN_A")
        b = old_get("ttwid_B", "TOKEN_B")
        self.assertEqual(a, "TOKEN_A")
        self.assertEqual(b, "TOKEN_A",
                         "旧实现应把 A 的 token 串给 B（缺陷复现）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
