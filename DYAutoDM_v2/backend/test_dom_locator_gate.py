# coding=utf-8
"""dom_locator 机械门禁（自证有效：正向必命中 + 同名/错域必须被拒）。

设计（对应 ui-element-locating-strategy §7.7）：
  G1 漂移 DOM：id/class/name 全改写后，自适应仍能命中（正向）。
  G2 干扰 DOM：导航栏含同名「登录」，不得误命中真正的提交按钮/输入框（判别力）。
  G3 负控：把「错域同名元素」直接喂给 validate()，必须返回 False（判据有区分度）。

运行：python -m unittest backend.test_dom_locator_gate   （需 scrapling base 包）
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    from auto_dm import dom_locator as dl
except Exception:  # noqa: BLE001
    from backend.auto_dm import dom_locator as dl  # type: ignore

_NEED = unittest.skipUnless(dl.available(), "scrapling 未安装（base 包）")

BASE = """
<html><body>
  <div id="nav"><span>首页</span><span>登录</span><button>登录</button></div>
  <div id="login-full-panel-AbC123"><div id="douyin-login-new-id" class="douyin_login_new_class">
    <div class="tabbar"><span class="C6OZQwMA">扫码登录</span><span class="C6OZQwMA">验证码登录</span></div>
    <div id="douyin_login_comp_scan_code"><div id="animate_qrcode_container"><svg><image/></svg></div></div>
    <div id="douyin_login_comp_mobile_code">
      <input id="normal-input" name="normal-input" type="tel" maxlength="50" placeholder="请输入手机号">
      <input id="button-input" name="button-input" type="tel" maxlength="6" placeholder="请输入验证码">
      <div id="douyin_login_comp_btn_id">登录</div>
    </div>
  </div></div>
</body></html>
"""

DRIFT = """
<html><body>
  <div id="nav"><span>首页</span><span>登录</span><button>登录</button></div>
  <div id="login-full-panel-XyZ"><div class="login-wrap">
    <div class="tb"><span class="x1"> 扫码登录 </span><span class="x1"> 验证码登录 </span></div>
    <div id="z-scan-x"><div id="z-qr-x"><svg><image/></svg></div></div>
    <div id="z-mobile-x">
      <input id="z-phone-x" name="mobile-2" type="tel" maxlength="50" placeholder="请输入手机号">
      <input id="z-code-x" name="grid-verify-y" type="tel" maxlength="6" placeholder="请输入验证码">
      <div id="z-btn-x" class="btn-9f">登录</div>
    </div>
  </div></div>
</body></html>
"""

TRAP = """
<html><body>
  <div id="nav"><span>首页</span><span>登录</span><button>登录</button></div>
  <div class="search"><input name="kw" placeholder="搜索"></div>
  <div class="login-wrap">
    <div class="tb"><span class="k1">扫码登录</span><span class="k1">验证码登录</span></div>
    <input name="m9" type="tel" maxlength="50" placeholder="请输入手机号">
    <input name="c9" type="tel" maxlength="6" placeholder="请输入验证码">
    <div class="btn-x">登录</div>
  </div>
</body></html>
"""


@_NEED
class TestDomLocatorGate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile
        cls.db = os.path.join(tempfile.mkdtemp(prefix="dlgate_"), "e.sqlite")
        cls.loc = dl.AdaptiveLocator(storage_file=cls.db)
        cls.loc.seed(BASE)

    def _locate(self, html, key):
        r = self.loc.locate(html, key)
        return r

    def test_g1_drift_still_hits(self):
        """G1：id/class/name 全漂移后，5 个目标仍全部命中。"""
        missing = []
        for key in ("tab_scan", "tab_sms", "input_phone", "input_code", "btn_submit"):
            r = self._locate(DRIFT, key)
            if not r.ok:
                missing.append(f"{key}:{r.reason}")
        self.assertEqual(missing, [], f"漂移后未命中: {missing}")

    def test_g2_trap_no_false_hit(self):
        """G2：导航栏同名「登录」不得让 submit/phone 误命中。"""
        r_sub = self._locate(TRAP, "btn_submit")
        self.assertTrue(r_sub.ok, "TRAP 页应能定位到真正的提交按钮")
        # 命中的元素必须带按钮类而**不是**导航栏（导航栏 button/span 无 placeholder 等）
        el = r_sub.element
        attrs = dict(getattr(el, "attrib", {}) or {})
        # 真按钮所在容器 class=btn-x；导航栏匿名 button 不满足 scope_text（无「登录」域）
        self.assertNotIn("nav", str(attrs), "不得命中导航栏元素")

        r_phone = self._locate(TRAP, "input_phone")
        self.assertTrue(r_phone.ok)
        got = dict(getattr(r_phone.element, "attrib", {}) or {})
        self.assertEqual(got.get("name"), "m9", f"应命中 m9 而非搜索框: {got}")

    def test_g3_negative_control_validate_rejects(self):
        """G3 负控：错域/错标签的同名元素必须被 validate() 拒绝。"""
        from lxml import html as LH
        doc = LH.fromstring(TRAP)
        nav_btn = doc.xpath('//div[@id="nav"]//button')[0]
        t_sub = {t.key: t for t in dl.LOGIN_TARGETS}["btn_submit"]
        ok, why = dl.validate(nav_btn, t_sub)
        self.assertFalse(ok, f"导航栏按钮竟通过校验（判据失效）: {why}")

        search_input = doc.xpath('//input[@name="kw"]')[0]
        t_phone = {t.key: t for t in dl.LOGIN_TARGETS}["input_phone"]
        ok2, why2 = dl.validate(search_input, t_phone)
        self.assertFalse(ok2, f"搜索框竟通过手机框校验（判据失效）: {why2}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
