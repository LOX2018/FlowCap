"""采集策略字段拆分门禁：`num` 与 `comment_limit` 语义分离（★ 2026-10-03）。

## 为什么拆（背景与真实缺陷）

`CrawlPolicy.num` 的语义是「**一次搜索取回多少个作品**」，`crawl.py::_page_count`
的 docstring 早已明写「与 num 是**两个量**，不可混用」。

但 2026-10-03 我把批量采集的「每作品评论上限」接到了 `num` 上 —— **违反了这条
既有约定**，造成两个后果：
  ① 用户在策略里调「搜索条数」会**意外改变评论采集量**（两个本该独立的旋钮
     被绑在一起）；
  ② 「调 num=50 只对搜索生效」这一既有认知被打破，构成隐蔽的行为漂移。

⇒ 拆分为两个字段：`num`（搜索条数，**语义冻结**，不迁移旧数据）+
`comment_limit`（评论上限，0=不覆盖）。

## 契约

  G1 `comment_limit` 在**策略模型**与**写入白名单**里都存在（否则存不进去）
  G2 `comment_limit` 范围 0..300，**0 = 不覆盖**（不是"0 条"）
  G3 批量采集读 `comment_limit`，**不读 `num`**（防回退到混淆实现）
  G4 `num` 的收敛范围保持 1..50（**未被拆分改动**）—— 负控自证
  G5 负控：把消费端改回读 `num` ⇒ 门禁必须变红
  G6 显式 `limit` 优先于策略的 `comment_limit`（显式值不被静默覆盖）

## 运行

    cd backend && python -m unittest test_crawl_policy_field_split -v
"""
from __future__ import annotations

import os
import re
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_POLICY_PY = os.path.join(_HERE, "api", "crawl_policy.py")
_CRAWL_PY = os.path.join(_HERE, "api", "crawl.py")


def _read(p: str) -> str:
    with open(p, encoding="utf-8") as f:
        return f.read()


def _mod(name: str):
    import importlib
    import sys
    if _HERE not in sys.path:
        sys.path.insert(0, _HERE)
    return importlib.import_module(f"api.{name}")


class TestPolicyFieldExists(unittest.TestCase):
    """G1/G2：字段存在 + 范围与默认值正确。"""

    def test_g1_model_and_whitelist(self):
        m = _mod("crawl_policy")
        # ⚠️ 类名以源码为准（早期版本这里写成 `CrawlPolicySave` —— 符号是**猜**的，
        #   运行即 AttributeError。教训：不要猜符号，先 grep 再写门禁）。
        fields = getattr(m.PolicyBody, "model_fields", {})
        self.assertIn("comment_limit", fields,
                      "策略保存模型必须有 comment_limit")
        self.assertIn("comment_limit", m._FIELDS,
                      "_FIELDS 是写入侧真源，缺它 ⇒ 存不进库")

    def test_g2_default_zero_means_no_override(self):
        m = _mod("crawl_policy")
        d = getattr(m.PolicyBody, "model_fields", {})["comment_limit"].default
        self.assertEqual(d, 0, "默认 0 = 不覆盖（回落请求体 limit）")

    def test_g2_clamped_to_0_300(self):
        m = _mod("crawl_policy")
        # 负值 → 0；超 300 → 300；正常值原样
        self.assertEqual(m._clamp_int(-5, 0, 300, 0), 0)
        self.assertEqual(m._clamp_int(9999, 0, 300, 0), 300)
        self.assertEqual(m._clamp_int(120, 0, 300, 0), 120)


class TestNumSemanticsFrozen(unittest.TestCase):
    """G4：num 的范围未被拆分改动（防「顺手把 num 也改了」）。"""

    def test_g4_num_range_unchanged(self):
        src = _read(_POLICY_PY)
        # ⚠️ 早期版本用跨行正则匹配，实际源码是**单行**调用 ⇒ 恒不匹配（假红）。
        #   改为宽松匹配 + 断言范围字面量，避免再被排版改动打断。
        self.assertRegex(
            src, r'_clamp_int\(\s*getattr\(body,\s*"num"', 'num="num" 存在')
        # num 的收敛范围必须是 1..50（搜索条数语义），不能被改成 0..300
        m = re.search(
            r'_clamp_int\(\s*getattr\(body,\s*"num".*?,\s*1,\s*50,\s*20\s*\)',
            src, re.S)
        self.assertIsNotNone(m, "num 的收敛范围必须是 1..50（搜索条数语义）")
        # 反向自证：comment_limit 才是 0..300
        m2 = re.search(
            r'_clamp_int\(\s*getattr\(body,\s*"comment_limit".*?,\s*0,\s*300,\s*0\s*\)',
            src, re.S)
        self.assertIsNotNone(m2, "comment_limit 的收敛范围必须是 0..300")


class TestBatchConsumesCommentLimit(unittest.TestCase):
    """G3/G5：批量采集**只**读 comment_limit，绝不读 num。"""

    def _batch_body(self, src: str) -> str:
        m = re.search(
            r"^async def crawl_comments_batch\b.*?(?=^@router\.|^async def |^def |^class )",
            src, re.M | re.S)
        self.assertIsNotNone(m, "找不到 crawl_comments_batch 函数体")
        return m.group(0)

    def test_g3_reads_comment_limit(self):
        src = _read(_CRAWL_PY)
        body = self._batch_body(src)
        self.assertIn('comment_limit', body,
                      "批量采集必须读 comment_limit")
        self.assertRegex(
            body, r'_pol\.get\("comment_limit"\)',
            "策略取值必须用 comment_limit")

    def test_g3_does_not_read_num(self):
        """🔴 核心判据：批量采集里**不得**再出现 `pol.get("num")`。"""
        src = _read(_CRAWL_PY)
        body = self._batch_body(src)
        self.assertNotRegex(
            body, r'_pol\.get\("num"\)',
            "批量采集仍在读 num —— 字段拆分被回退（num 是搜索条数，语义不同）")

    def test_g5_negative_control_would_be_caught(self):
        """负控自证：把 `comment_limit` 换成 `num` 后，上面那条 G3 必须变红。"""
        src = _read(_CRAWL_PY)
        body = self._batch_body(src)
        mutated = body.replace('_pol.get("comment_limit")', '_pol.get("num")')
        # 变异后必须触发 G3 的判据（这是「门禁真的有鉴别力」的证明）
        self.assertNotEqual(body, mutated, "变异未生效：本函数体没有 comment_limit 取值")
        self.assertRegex(mutated, r'_pol\.get\("num"\)',
                         "变异体应命中 G3-does-not-read-num 的判据")
        # 原体不应命中
        self.assertNotRegex(body, r'_pol\.get\("num"\)')


class TestExplicitLimitWins(unittest.TestCase):
    """G6：显式 limit 优先于策略（显式值不被静默覆盖）。"""

    def test_g6_explicit_limit_guard_present(self):
        src = _read(_CRAWL_PY)
        body = re.search(
            r"^async def crawl_comments_batch\b.*?(?=^@router\.|^async def |^def |^class )",
            src, re.M | re.S).group(0)
        self.assertRegex(
            body, r"if\s+p_climit\s*>\s*0\s+and\s+not\s+body\.limit",
            "必须显式判断「请求体没给 limit」才用策略值（显式值优先）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
