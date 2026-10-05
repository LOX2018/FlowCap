# -*- coding: utf-8 -*-
"""防回归：**后端全体 .py 不得出现字典重复键**（含 app_config_schema.py 专项断言）。

背景（2026-09-17 OCR 审查 HIGH）：`services/app_config_schema.py` 的
`SECTIONS["automation"]["fields"]` 曾重复定义 10 个键。Python 字面量对重复键是
**last-wins，且无任何告警**，于是前一份被静默丢弃 —— 维护者改错一份完全看不出来。

## 2026-09-22 扩围（实测取证）

原先本测试的 `TARGET` **硬编码只扫 `services/app_config_schema.py` 一个文件**
⇒ 同一缺陷形态在别处无任何防护。实测后果：`errcode_data.py` 的 `ERRCODES` 里
`"ACC-017"` 被定义两次（行 185 单行记录 / 行 333 六段契约）：

  · dict 后写覆盖 → 生效的那条**缺 meaning**；
  · `lookup()` 的 `c["meaning"]` → `KeyError`；
  · `all_codes()` / `GET /api/errcodes`（**会员门禁豁免端点**）→ 必然 500。

门禁有效性已用「改前备份」自证：同一 `find_dup_keys()` 能在备份上报出
`dict@163: 'ACC-017' 行 [185, 333]`，修复后归零。

因此本测试扩为**全后端扫描**（跳过 __pycache__/build/dist/_internal 等非源码目录）。
新增源码时若复制粘贴出重复键，本地/CI 跑本测试即拦住。

用法：cd DYAutoDM_v2/backend && python test_no_dup_dict_keys.py
"""
import ast
import os
import sys
import unittest
from collections import defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "services", "app_config_schema.py")

#: 非源码目录（构建产物 / 缓存 / 第三方）
SKIP_DIRS = {"__pycache__", "build", "dist", "node_modules", ".git",
             "_internal", ".mypy_cache", ".pytest_cache"}


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


def iter_source_files(root: str):
    for dp, dns, fns in os.walk(root):
        dns[:] = [d for d in dns if d not in SKIP_DIRS]
        for fn in fns:
            if fn.endswith(".py"):
                yield os.path.join(dp, fn)


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

    def test_whole_backend_has_no_duplicate_dict_keys(self):
        """全后端扫描：任一 .py 出现字典重复键即失败。

        这是 2026-09-22 新增（原测试只覆盖 app_config_schema.py，实测因此放过了
        errcode_data.py 的 ACC-017 重复键 → /api/errcodes 500）。
        """
        offenders = []
        scanned = 0
        for p in iter_source_files(HERE):
            scanned += 1
            try:
                dups = find_dup_keys(p)
            except SyntaxError as e:                 # 语法坏文件由别的门禁负责
                self.fail(f"{p} 语法错误，无法扫描: {e}")
            if dups:
                rel = os.path.relpath(p, HERE)
                for dline, keys in sorted(dups.items()):
                    for k, ks in keys.items():
                        offenders.append(f"  {rel} dict@{dline}: '{k}' 行 {ks} "
                                         f"← 后写生效={ks[-1]}")
        self.assertGreater(scanned, 50, "扫描文件数异常（目录被跳过？）")
        if offenders:
            self.fail("发现字典重复键（Python last-wins，前者被静默丢弃）：\n"
                      + "\n".join(offenders))

    def test_all_codes_lookup_never_crashes(self):
        import sys
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from errcode import all_codes, lookup
        codes = all_codes()
        for item in codes:
            c = item['code']
            r = lookup(c)
            self.assertIsNotNone(r, f"lookup('{c}') 返回 None")
            self.assertIn('meaning', r, f"{c} missing meaning")
        # 还验证全量遍历不抛异常
        self.assertGreater(len(codes), 300, f"error code count too low: {len(codes)}")

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
