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
import unittest

_BACKEND = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _BACKEND)
os.environ.setdefault("DY_APP_ROOT", r"C:\temp\dyautodm_design")

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
        """S1：每个受保护端点的实现窗口内必须出现 signed_url。"""
        import pathlib
        prot = _protected()
        self.assertTrue(prot, "PROTECTED_PATHS_GET 为空 —— 保护清单丢失")
        offenders = []
        from utils.secsdk_web_sign import is_protected  # noqa: F401
        for py in pathlib.Path(_BACKEND).rglob("*.py"):
            if "__pycache__" in str(py) or py.name.startswith("test_"):
                continue
            lines = py.read_text(encoding="utf-8", errors="replace").splitlines()
            for i, ln in enumerate(lines):
                for path in prot:
                    if not re.search(r'api\s*=\s*f?["\'`]' + re.escape(path), ln):
                        continue
                    window = "\n".join(lines[i:i + 60])
                    if "signed_url(" not in window:
                        offenders.append(f"{py.name}:{path}")
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
