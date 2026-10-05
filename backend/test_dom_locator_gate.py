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

    # ── D-1（OCR[13]）：exact 模式必须归一整段 Unicode 空白 ────────────────
    def test_d1_nbsp_exact_hits(self):
        """D-1 正向：标签含 NBSP/全角空格时 exact 仍命中（旧 replace 会拒）。"""
        t = dl.Target("t", "span", ("span",), ("扫码登录",), ("login",),
                      desc="", text_mode="exact")
        # 纯函数级：NBSP(U+00A0) / 全角空格(U+3000) 均须被归一
        self.assertTrue(dl._text_hit("扫码登录", {}, t))
        self.assertTrue(dl._text_hit("扫\u00a0码登录", {}, t), "NBSP 未归一 ⇒ 视觉正确标签被拒")
        self.assertTrue(dl._text_hit("扫\u3000码 登录", {}, t), "全角空格/ASCII 空格未归一")
        # 端到端：真 DOM 内标签为 NBSP 时，locate(tab_scan) 仍应命中
        nbsp_html = BASE.replace("扫码登录", "扫\u00a0码\u3000登录")
        r = self._locate(nbsp_html, "tab_scan")
        self.assertTrue(r.ok, f"NBSP 标签端到端未命中: {r.reason}")

    def test_d1_negative_control_old_replace_fails(self):
        """D-1 负控：还原旧 replace(' ','') 实现，本用例必须变红。"""
        def _old_exact(own, w):
            return own.replace(" ", "") == w.replace(" ", "")

        self.assertFalse(_old_exact("扫\u00a0码登录", "扫码登录"),
                         "旧实现竟命中 NBSP —— 负控失效")
        self.assertFalse(_old_exact("扫\u00a0码\u3000登录", "扫码登录"))

    # ── D-2（OCR[14]）：含双引号/反斜杠的 want_text 不得拼坏 CSS ──────────
    def test_d2_quote_in_want_text_no_crash(self):
        """D-2：want_text 含 `"`/`\\` 时域策略选择器仍语法合法（旧实现报 SelectorSyntaxError）。"""
        t = dl.Target("q", "input", ("input",), ('a"b', "c\\d"), ("login",),
                      ("placeholder",), desc="")
        html = (r'<html><body><div class="login-wrap">'
                r'<input name="z1" placeholder="a&quot;b">'
                r'<input name="z2" placeholder="c\d">'
                r'</div></body></html>')
        res, _by = self.loc._collect(html, t)
        bad = [a for a in res.attempts if isinstance(a[1], str) and "SelectorSyntaxError" in a[1]]
        self.assertEqual(bad, [], f"选择器被拼坏: {bad}")

    def test_d2_negative_control_raw_interp_breaks(self):
        """D-2 负控：还原旧裸插值必须抛 SelectorSyntaxError（证明原缺陷真实）。"""
        from cssselect import SelectorSyntaxError
        old_css = 'input[placeholder*="a"b"]'
        with self.assertRaises(SelectorSyntaxError):
            self.loc._sel("<html><body><input placeholder='x'></body></html>").css(old_css)

    # ── D-3（OCR[15]）：click_adaptive 与 login_remote 真鼠标时序防漂移 ────
    def test_d3_click_timing_matches_login_remote(self):
        """D-3：本模块 click_adaptive 与 login_remote._mouse_click_locator 的
        asyncio.sleep 时序必须一致（防两条点击路径再次漂移）。"""
        import re

        def _sleeps(func):
            try:
                src = __import__("inspect").getsource(func)
            except OSError:  # pragma: no cover
                return None
            return [round(float(x), 3) for x in re.findall(r"asyncio\.sleep\(\s*([0-9.]+)\s*\)", src)]

        here = _sleeps(dl.click_adaptive)
        try:
            from auto_dm import login_remote as lr
        except Exception:  # noqa: BLE001
            from backend.auto_dm import login_remote as lr  # type: ignore
        there = _sleeps(lr._mouse_click_locator)
        self.assertIsNotNone(here)
        self.assertIsNotNone(there)
        self.assertEqual(here, there,
                         f"真鼠标点击时序漂移 dom_locator={here} login_remote={there}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
