# -*- coding: utf-8 -*-
"""a_bogus 子域参数化守卫（2026-09-20 直播数据全链路失效修复固化）。

## 背景（真实缺陷，不是理论风险）

直播流数据**完全拉不到**且不报任何错。根因：a_bogus 签名里内嵌的
`(aid, page_id)` **按子域取不同值**（www=6383/11881，live=6383/7571），
本仓把它们**硬编码成主站值**，导致直播域 `/webcast/*` 请求用错误子域签名。

对齐上游 `cv-cat/DouYin_Spider`：`utils/ab_pure.py: HOST_APP_IDS` +
`builder/params.py: with_a_bogus(host=...)` + `LIVE_HOST` 常量。

## 为什么必须是机械门禁

这个缺陷**不抛异常、不打日志**——服务端静默拒绝，肉眼和运行时都发现不了。
只能靠静态断言把它钉死，否则下次重构必然复发（见 §7.4 自守卫原则）。

## 运行

    cd backend && python -m unittest test_abogus_host_guards -v
"""
from __future__ import annotations

import inspect
import os
import re
import tempfile
import unittest

# M-28（2026-09-29）：本模块是**纯源码静态门禁**（不 import database、不建库），
# 但同进程内 `unittest discover` 会先导入别的模块，它们把 FLOWCAP_APP_ROOT 设成各自
# 的临时根；本模块虽然不读库，其被测静态断言仍会被「进程级根」影响（例如
# `utils/ab_pure.py` 的导入期行为）。为消除顺序相关性，这里**无条件**钉一个
# 一次性临时根，并在每个用例 setUp 里重钉。
# 标识符（父会话 grep 用）：N1_M28_PIN_ROOT
_ROOT = tempfile.mkdtemp(prefix="n1_m28_abogus_")
os.makedirs(_ROOT, exist_ok=True)
os.environ["FLOWCAP_APP_ROOT"] = _ROOT


def _n1_m28_pin_root():
    """N1_M28_PIN_ROOT：执行期把 FLOWCAP_APP_ROOT 重钉回本模块的一次性临时根。"""
    os.environ["FLOWCAP_APP_ROOT"] = _ROOT


_HERE = os.path.dirname(os.path.abspath(__file__))

_AB_PURE = os.path.join(_HERE, "utils", "ab_pure.py")
_PARAMS = os.path.join(_HERE, "builder", "params.py")
_DY_UTIL = os.path.join(_HERE, "utils", "dy_util.py")
_CLIENT_LIVE = os.path.join(_HERE, "dy_apis", "client_live.py")


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


class TestAbogusHostParameterisation(unittest.TestCase):
    """a_bogus 必须能按子域取 (aid, page_id)。"""

    def test_host_app_ids_table_exists(self):
        """G1: ab_pure 必须有 HOST_APP_IDS 子域表，且含 live 的非主站 page_id。"""
        import utils.ab_pure as A
        self.assertTrue(hasattr(A, "HOST_APP_IDS"), "缺少 HOST_APP_IDS 子域表")
        table = A.HOST_APP_IDS
        self.assertIn("live.douyin.com", table)
        self.assertIn("www.douyin.com", table)
        live_aid, live_page = table["live.douyin.com"]
        www_aid, www_page = table["www.douyin.com"]
        self.assertNotEqual(live_page, www_page,
                            "live 与 www 的 page_id 必须不同（否则签名无区分）")

    def test_page_id_not_hardcoded(self):
        """G2: L[67..70] 必须是变量 page_id，不得写死常量。"""
        src = _read(_AB_PURE)
        self.assertNotIn("le_bytes(6383, 4)", src,
                         "L[67..70]/L[71..74] 仍写死 6383 —— page_id 未参数化")
        self.assertIn("le_bytes(page_id, 4)", src)
        self.assertIn("le_bytes(aid, 4)", src)

    def test_sign_query_accepts_host(self):
        """G3: sign_query 必须接受 host 参数。"""
        import utils.ab_pure as A
        sig = inspect.signature(A.ABogusPureSigner.sign_query)
        self.assertIn("host", sig.parameters)

    def test_signatures_differ_per_host(self):
        """G4（核心行为门禁）: 同一 query 用 live / www 签出的 a_bogus 必须不同。"""
        from utils.dy_util import generate_a_bogus
        q = "aid=6383&room_id=7687606083061320475&msToken=x"
        www = generate_a_bogus(q, host="www.douyin.com")
        live = generate_a_bogus(q, host="live.douyin.com")
        self.assertNotEqual(www, live,
                            "live 与 www 签出相同 a_bogus ⇒ 子域参数化失效")

    def test_with_a_bogus_accepts_host(self):
        """G5: Params.with_a_bogus 必须支持 host 传参。"""
        from builder.params import Params
        sig = inspect.signature(Params.with_a_bogus)
        self.assertIn("host", sig.parameters)


class TestLiveDomainUsesLiveHost(unittest.TestCase):
    """直播域所有 a_bogus 调用必须显式传 LIVE_HOST。"""

    def test_live_host_constant_defined(self):
        """G6: client_live 必须定义 LIVE_HOST。"""
        src = _read(_CLIENT_LIVE)
        self.assertIn("LIVE_HOST", src)

    def test_no_unparameterized_call_in_live_domain(self):
        """G7: 直播域内不得残留未传 host 的 with_a_bogus 调用。"""
        src = _read(_CLIENT_LIVE)
        # 只检查 LIVE_HOST 定义之后的正文（定义前的import区不受约束）
        body = src.split("LIVE_HOST = 'live.douyin.com'", 1)[-1]
        calls = re.findall(r"with_a_bogus\(([^)]*)\)", body)
        self.assertTrue(calls, "直播域应至少有一处 with_a_bogus 调用")
        bad = [c for c in calls if "LIVE_HOST" not in c]
        self.assertEqual(bad, [],
                         f"直播域仍有未传 LIVE_HOST 的签名调用: {bad}")


if __name__ == "__main__":
    unittest.main()
