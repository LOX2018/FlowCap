# -*- coding: utf-8 -*-
"""防回归：app_config_schema.py 不得出现**字典重复键**。

背景（2026-09-17 OCR 审查 HIGH）：`services/app_config_schema.py` 的
`SECTIONS["automation"]["fields"]` 曾重复定义 10 个键（max_actions_per_run /
send_delay_ms / scan_interval_seconds / require_context / min_digg_count /
min_comment_count / min_play_count / match_keywords / exclude_keywords /
use_global_gate）。Python 字面量对重复键是 **last-wins，且无任何告警**，
于是前一份被静默丢弃 —— 维护者改错一份完全看不出来。

本测试用 AST 静态扫描（不导入模块，避免重依赖），任何重复键即失败。
新增字段时若不小心复制粘贴出重复键，CI/本地跑本测试会立即拦住。

用法：cd DYAutoDM_v2/backend && python test_no_dup_dict_keys.py
"""
import ast
import os
import sys
import unittest
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "services", "app_config_schema.py")


def find_dup_keys(path: str) -> dict:
    """返回 {dict起始行: {重复键: [出现的行号...]}}。"""
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    tree = ast.parse(src)
    out = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Dict):
            continue
        # 只处理「全部键都是字符串常量」的 dict；含 ** 展开则跳过
        if not all(isinstance(k, ast.Constant) and isinstance(k.value, str)
                   for k in node.keys):
            continue
        seen = defaultdict(list)
        for k in node.keys:
            seen[k.value].append(k.lineno)
        dups = {k: v for k, v in seen.items() if len(v) > 1}
        if dups:
            out[node.lineno] = dups
    return out


class TestNoDupDictKeys(unittest.TestCase):
    def test_schema_has_no_duplicate_dict_keys(self):
        self.assertTrue(os.path.isfile(TARGET), f"目标文件不存在: {TARGET}")
        dups = find_dup_keys(TARGET)

        if dups:
            lines = ["发现字典重复键（Python last-wins，前者被静默丢弃）："]
            for dline, keys in sorted(dups.items()):
                lines.append(f"  dict@{dline}:")
                for k, ks in keys.items():
                    lines.append(f"    '{k}' 出现在行 {ks} ← 行 {ks[-1]} 生效")
            self.fail("\n".join(lines))

    def test_automation_authoritative_values(self):
        """automation 节关键字段的权威值必须与 automation_engine.py 的 clamp 一致。

        这保证「删掉重复键」没有改变最终生效值。
        """
        with open(TARGET, encoding="utf-8") as fh:
            src = fh.read()
        tree = ast.parse(src)
        fields = None
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            for k, v in zip(node.keys, node.values):
                if (isinstance(k, ast.Constant) and k.value == "automation"
                        and isinstance(v, ast.Dict)):
                    for kk, vv in zip(v.keys, v.values):
                        if (isinstance(kk, ast.Constant)
                                and kk.value == "fields"
                                and isinstance(vv, ast.Dict)):
                            fields = {}
                            for fk, fv in zip(vv.keys, vv.values):
                                if isinstance(fk, ast.Constant):
                                    try:
                                        fields[fk.value] = ast.literal_eval(fv)
                                    except Exception:
                                        pass
        self.assertIsNotNone(fields, "未找到 SECTIONS['automation']['fields']")

        # 与 backend/auto_dm/automation_engine.py 的 clamp 实测一致
        expect = {
            "scan_interval_seconds": (30, 10, 300),
            "max_actions_per_run": (5, 1, 50),
        }
        for name, (d, lo, hi) in expect.items():
            f = fields.get(name)
            self.assertIsNotNone(f, f"缺少字段 {name}")
            self.assertEqual(f.get("default"), d, f"{name}.default")
            self.assertEqual(f.get("min"), lo, f"{name}.min")
            self.assertEqual(f.get("max"), hi, f"{name}.max")


if __name__ == "__main__":
    unittest.main(verbosity=2)
