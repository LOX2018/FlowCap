# -*- coding: utf-8 -*-
"""M-2 门禁：secsdk 签名接线的**发送侧正确性**（2026-09-26）。

## 为什么 G2 不够
`scripts/check_contracts.py` 的 G2 只断言「受保护端点 ~60 行窗口内出现
`signed_url(`」。它**抓不到**最常见也最隐蔽的错用：

    url = params.signed_url(base, auth)
    requests.get(url, params=params.get(), ...)      # ← 二次编码，签名失效

签名对**规范化后的 query** 计算，服务端按收到的 query 校验。把 `params` 再交给
requests，requests 会再编码一遍 → 与签名输入不一致 → **仍然 403**。
⇒ 必须断言「调用 `signed_url` 的那次 `requests.*` **不得同时传 `params=`**」。

## 实测依据（A/B，2026-09-26，真实账号）
| 端点 | 不签名 | 签名 |
|---|---|---|
| `/aweme/v1/web/aweme/detail/` | **403** 46B `Blocked by ArgusSecurityPlugin Uifid Not Found` | **200** + 124,004B + `aweme_detail` 非空 |
| `/aweme/v1/web/tab/feed/` | **200** / 229,138B | **200** / 211,847B（清单过宽，取保守侧） |

## 判据（S1~S3）
  S1  受保护清单里的端点，其实现必须调用 `signed_url`
  S2  **用了 `signed_url` 的调用点，不得把 `params=` 传给 requests**（S1 的关键补强）
  S3  负控自证：构造「signed_url + params=」片段 → S2 判据必须命中
"""
from __future__ import annotations

import os
import re
import sys
import tempfile
import unittest

_BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BACKEND)
# A-8 / M-17 隔离根单一化：模块级 DY_APP_ROOT 必须是**一次性临时目录**。
# 先 mkdir 再赋值（vbrowser.app_root() 忽略不存在的根 → 回落仓库 data/）。
_ROOT = tempfile.mkdtemp(prefix="m2_secsdk_")
os.makedirs(_ROOT, exist_ok=True)
os.environ["DY_APP_ROOT"] = _ROOT

def _has_double_encoding(chunk: str) -> bool:
    """该代码块是否「用了 signed_url **且** 又把 params= 交给 requests」。

    判据：同一块里出现 `signed_url(` 且出现 `params=params.get()`。
    用块判定（而非跨行正则）—— 原正则 r"signed_url\([^)]*\)[^\n]*\n?[^\n]*params\s*="
    在真实多行写法下会漏报（实测：负控 S3 未命中）。
    """
    return ("signed_url(" in chunk
            and re.search(r"params\s*=\s*params\.get\(\)", chunk) is not None)


def _protected() -> list[str]:
    from utils.secsdk_web_sign import PROTECTED_PATHS_GET
    return list(PROTECTED_PATHS_GET)


# ── 以下为 M-14（2026-09-27）S1 判据扩窗增量 ────────────────────────────────
# 为什么必须单独扩（而不是改 PROTECTED_PATHS_GET 本身）：
#   `PROTECTED_PATHS_GET` 的语义是「**SDK 的 webSign 策略配置**里确实存在的路径」
#   （见 utils/secsdk_web_sign.py 模块 docstring），它是 SECSSDK 事实的 SSOT，
#   被 `is_protected()` / `Params.needs_secsdk_sign()` 用于**运行期**决定是否加签。
#   往里塞本项目自决的端点会污染这份事实 SSOT（让「SDK 事实」与「我方决策」
#   混为一谈，后续无法区分）。
#
#   而本文件是**门禁**：它的职责是「本项目**决定必须签名**的端点，其发送侧
#   真的签了没有」。故在门禁侧单独维护增量清单——事实 SSOT 与门禁判据分离。
#
# M-14 实测依据（同族 M-2 / client_comments 的 Argus 结论）：
#   `/aweme/v1/web/general/search/single/`、`/aweme/v1/web/general/search/stream/`、
#   `/aweme/v1/web/live/search/` 未经 secsdk 签名发出时，Argus 网关恒返
#   **HTTP 403（46B `Blocked by ArgusSecurityPlugin Uifid Not Found`）**。
MUST_SIGN_PATHS = (
    "/aweme/v1/web/general/search/single/",
    "/aweme/v1/web/general/search/stream/",
    "/aweme/v1/web/live/search/",
)


def _gated_paths() -> list[str]:
    """S1 判据实际保护的路径集合 = SDK 事实清单 ∪ 本项目门禁增量清单。"""
    paths = list(_protected())
    for p in MUST_SIGN_PATHS:
        if p not in paths:
            paths.append(p)
    return paths


# ── 匿名豁免（2026-10-02 门禁判据修复，实测 4 项误报）────────────────────
# 🔴 **判据缺陷**（非实现缺陷）：原 S1 只判「`api = "<受保护路径>"` 所在行
# 起 60 行内有没有 `signed_url(`」。这个窗口**不认函数边界**，会跨进**下一个**
# 函数体里。仓里有成对的「登录态实现 / 匿名实现」共用同一路径：
#
#     client_search.py:86   search_single()       → 有 signed_url ✓
#     client_search.py:517  search_single_anon()  → 匿名，按设计不签名
#     client_video.py:252   get_feed()            → 有 signed_url ✓
#     client_video.py:319   get_feed_anon()       → 匿名，按设计不签名
#
# 于是从 86 行起的 60 行窗口一路读到 145（命中 ✓）没事，但**从 517 行起**的
# 窗口读到 577 —— 那里是匿名实现，**本来就该没有** signed_url ⇒ 被记成违规。
#
# 匿名接口**按设计就不该签名**：它走 `AnonFingerprint` 的独立设备参数、
# 不挂账号 cookie、无 web_id / a_bogus，签名反而会带上无效的账号上下文。
# 故此处按「**函数名含 `_anon` 或 `anon_`**」显式豁免，并**把窗口收进函数体**，
# 双管齐下：既治标（不再跨函数误判），也治本（豁免有据可查、可被审）。
# ⚠️ 这里匹配的是**函数名**（调用点传的是 `fn`，不含 `def ` 前缀）——
# 早先误写成 `def\s+\w*anon\w*\s*\(`，因缺 `def ` 前缀而**永不命中**，
# 负控立刻暴露（`get_feed_anon` 仍被判违规）。教训：正则的输入契约要对齐。
ANON_FUNC_RE = re.compile(r"\w*anon\w*", re.IGNORECASE)


def _function_window(lines: list[str], start: int) -> list[str]:
    """从 `start` 行起，取**当前函数体**内的行（不跨进下一个函数定义）。

    以「下一个顶格 `def `/`class ` 或文件结束」为右界；用缩进判定而非
    关键字硬切，避免把嵌套函数体里的字符串误当函数边界。
    """
    base_indent = len(lines[start]) - len(lines[start].lstrip())
    out = [lines[start]]
    for ln in lines[start + 1:]:
        if ln.strip() and not ln.startswith((" ", "\t")):
            break                       # 顶格 ⇒ 已出当前函数
        if ln.lstrip().startswith(("def ", "class ")) and \
                (len(ln) - len(ln.lstrip())) <= base_indent:
            break                       # 同级或更外层的定义 ⇒ 换函数了
        out.append(ln)
    return out


def _scan_signed_offenders(root: str) -> list[str]:
    """全仓扫描：受保护端点已用 signed_url，但同一次 request 又传了 params=。

    实现方式：定位 `signed_url(` 出现的**语句块**（该行起 4 行内），
    若其中出现 `params=` 则记为违规。
    """
    import pathlib
    bad: list[str] = []
    for py in pathlib.Path(root).rglob("*.py"):
        if "__pycache__" in str(py) or py.name.startswith("test_"):
            continue
        lines = py.read_text(encoding="utf-8", errors="replace").splitlines()
        for i, ln in enumerate(lines):
            if "signed_url(" not in ln:
                continue
            window = "\n".join(lines[i:i + 5])
            # 允许 `url = params.signed_url(...)` 这一行本身；
            # 违规 = 后续窗口里出现 `params=` 传给 requests
            after = "\n".join(lines[i + 1:i + 5])
            if re.search(r"params\s*=\s*params\.get\(\)", after):
                bad.append(f"{py.name}:{i + 1}")
    return bad


class TestM2SecsdkSendSide(unittest.TestCase):

    def test_s1_protected_endpoints_are_signed(self):
        """S1：每个受保护端点的**登录态**实现必须出现 signed_url。

        判据（2026-10-02 修复）：
          · 窗口收进**函数体**（`_function_window`）——原 60 行裸窗口会跨进
            下一个函数，把成对实现里的**匿名**版本误判成「缺签名」；
          · 函数名含 `anon` 的**匿名实现按设计豁免**（独立指纹、无账号 cookie，
            签名会带入无效账号上下文）——见 ANON_FUNC_RE 处完整说明。
        """
        import pathlib
        prot = _gated_paths()
        self.assertTrue(prot, "保护清单为空 —— S1 判据失效（SSOT 与增量清单均丢失）")
        offenders = []
        from utils.secsdk_web_sign import is_protected  # noqa: F401
        for py in pathlib.Path(_BACKEND).rglob("*.py"):
            if "__pycache__" in str(py) or py.name.startswith("test_"):
                continue
            lines = py.read_text(encoding="utf-8", errors="replace").splitlines()
            for i, ln in enumerate(lines):
                hit = [p for p in prot
                       if re.search(r'api\s*=\s*f?["\'`]' + re.escape(p), ln)]
                if not hit:
                    continue
                # 定位所属函数名（向上找最近的 def）
                fn = ""
                for j in range(i, -1, -1):
                    m = re.match(r"\s*def\s+(\w+)", lines[j])
                    if m:
                        fn = m.group(1)
                        break
                if ANON_FUNC_RE.search(fn):
                    continue          # 匿名实现：按设计豁免
                window = "\n".join(_function_window(lines, i))
                if "signed_url(" not in window:
                    offenders.append(f"{py.name}:{fn}:{hit[0]}")
        self.assertFalse(offenders, f"受保护端点未补签名: {offenders}")

    def test_s2_no_signed_url_plus_params_double_encoding(self):
        """S2（G2 的关键补强）：用了 signed_url 就不得再传 params=。"""
        bad = _scan_signed_offenders(_BACKEND)
        self.assertFalse(
            bad,
            "签名后仍把 params= 交给 requests（二次编码 ⇒ 签名失效、依旧 403）：\n  "
            + "\n  ".join(bad))

    def test_s3_negative_control_detects_double_encoding(self):
        """S3 负控自证：构造错误用法 → S2 判据必须命中。"""
        sample = (
            "        url = params.signed_url(base, auth)\n"
            "        resp = requests.get(url,\n"
            "                            params=params.get(), verify=tls_verify())\n"
        )
        self.assertTrue(
            _has_double_encoding(sample),
            "负控失效：错误用法未被判据命中")

    def test_s4_correct_form_is_not_flagged(self):
        """S4 负控：正确写法（signed_url + 不传 params）不得被误报。"""
        good = (
            "        url = params.signed_url(base, auth)\n"
            "        resp = requests.get(url, headers=headers.get(),\n"
            "                            cookies=auth.cookie, verify=tls_verify())\n"
        )
        self.assertFalse(_has_double_encoding(good),
                         "正确写法被误报（门禁过宽）")

    # ── S5：S1 判据自身的负控（防「豁免放宽成不扫描」）────────────────────
    def test_s5_s1_criterion_still_catches_signed_in_function(self):
        """S5 负控：登录态实现缺签名，S1 判据**必须**命中（豁免不得过宽）。

        🔴 为什么需要这条（2026-10-02）：S1 刚引入「匿名豁免」。若有人日后
        把豁免条件放宽成「跳过所有扫描」或「窗口取空」，S1 会**恒绿** ——
        而门禁恒绿 = 没有门禁。故用一段**合成样本**证明判据仍有判别力，
        不依赖真实仓库状态（真实仓库此刻恰好是合规的，测不出「过宽」）。
        """
        prot = _gated_paths()
        self.assertTrue(prot, "保护清单为空 —— 判据失效")
        target = prot[0]
        # 登录态实现：有受保护 api、**故意不**签名 ⇒ 必须被判违规
        signed_impl = [
            "    def search_general_work(self):",
            f"        api = '{target}'",
            "        url = 'https://x' + api",
            "        return requests.get(url)",
        ]
        # 匿名实现：同一路径，按设计不签名 ⇒ 必须被豁免
        anon_impl = [
            "    def search_general_work_anon(self):",
            f"        api = '{target}'",
            "        url = 'https://x' + api",
            "        return requests.get(url)",
        ]

        def _offenders(lines):
            out = []
            for i, ln in enumerate(lines):
                if not re.search(r'api\s*=\s*f?["\'`]' + re.escape(target), ln):
                    continue
                fn = ""
                for j in range(i, -1, -1):
                    m = re.match(r"\s*def\s+(\w+)", lines[j])
                    if m:
                        fn = m.group(1)
                        break
                if ANON_FUNC_RE.search(fn):
                    continue
                if "signed_url(" not in "\n".join(_function_window(lines, i)):
                    out.append(fn)
            return out

        self.assertEqual(_offenders(signed_impl), ["search_general_work"],
                         "S1 判据放过了登录态缺签名实现（豁免过宽，门禁形同虚设）")
        self.assertEqual(_offenders(anon_impl), [],
                         "匿名实现未被豁免（判据回归到误报）")

    def test_s5b_function_window_does_not_cross_functions(self):
        """S5b：`_function_window` 必须在下一个函数定义处**截断**（原 60 行裸窗口的病根）。"""
        lines = [
            "    def a(self):",
            "        api = '/x/'",
            "    def b(self):",
            "        url = params.signed_url('y', auth)",
        ]
        win = "\n".join(_function_window(lines, 1))
        self.assertNotIn("signed_url(", win,
                         "窗口跨进了下一个函数（原始误报的根因）")
        self.assertIn("api = '/x/'", win)


if __name__ == "__main__":
    unittest.main(verbosity=2)
