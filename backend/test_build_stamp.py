# -*- coding: utf-8 -*-
"""构建来源戳（build_stamp）回归 —— 正控 + **负控**（2026-10-04）。

## 背景（用户痛点）
「每次 debug 都要重编 Rust + 3 sidecar，是版本号限制吗？不校对版本号如何确认非陈旧？」

答：版本号门禁只证明「前后端**声明**的版本一致」，**不证明**「产物是这份代码构建的」。
本模块补后者：对产物**真实输入源码**算 sha256 来源戳。

## 判据（每条都要能变红）
| 判据 | 说明 |
|---|---|
| B1 戳确定性 | 同一份源码两次计算必须相同（遍历顺序无关） |
| B2 行尾无关 | 纯 CRLF↔LF 变更**不得**改变戳（否则假红） |
| B3 **源码敏感**（负控） | 改动一个**纳入范围**的 .py 字节 ⇒ 戳**必须**变 |
| B4 **范围外不敏感**（负控的反向） | 改 `test_*.py` / `_*.py` ⇒ 戳**不得**变（它们不进产物） |
| B5 无记录 ⇒ 判陈旧 | 无构建记录时 `check()` 必须 ok=False（不能默认放行） |
| B6 记录后 ⇒ 判新鲜 | 写入匹配的记录后 `check()` 必须 ok=True |
| B7 **产物 md5 绑定**（负控） | 产物内容被改 ⇒ `check_artifacts()` 必须 ok=False |
| B8 排除清单生效 | 戳内**不含** `__pycache__` / `build` / `dist` / `_build_version.py` |

## 运行
    cd FlowCap
    python backend/test_build_stamp.py
或
    python -m pytest backend/test_build_stamp.py -q
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_HERE = Path(__file__).resolve().parent          # backend/
_ROOT = _HERE.parent                             # FlowCap/
sys.path.insert(0, str(_ROOT / "scripts"))

import build_stamp as bs  # noqa: E402


class TestBuildStamp(unittest.TestCase):
    """全部用例在**临时沙盒**里跑 —— 绝不触碰真实源码树 / 真实记录文件。"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="build_stamp_test_"))
        self.root = self.tmp / "proj"
        (self.root / "backend").mkdir(parents=True)
        (self.root / "daemon").mkdir(parents=True)
        (self.root / "scripts").mkdir(parents=True)
        (self.root / "frontend" / "src").mkdir(parents=True)
        (self.root / "src-tauri" / "src").mkdir(parents=True)
        # 最小可哈希输入
        (self.root / "backend" / "main.py").write_text("x = 1\n", encoding="utf-8")
        (self.root / "daemon" / "recv_daemon.py").write_text("y = 2\n", encoding="utf-8")
        (self.root / "scripts" / "build_sidecar.py").write_text("z = 3\n", encoding="utf-8")
        (self.root / "frontend" / "src" / "app.tsx").write_text("export const a=1;\n", encoding="utf-8")
        (self.root / "src-tauri" / "src" / "lib.rs").write_text("fn main(){}\n", encoding="utf-8")
        self.rec = self.tmp / ".build_stamp.json"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- B1 确定性 ----
    def test_B1_deterministic(self):
        a, na = bs.compute(self.root, "sidecar")
        b, nb = bs.compute(self.root, "sidecar")
        self.assertEqual(a, b, "同一份源码两次计算必须相同")
        self.assertEqual(na, nb)
        self.assertGreater(na, 0, "至少要纳入 1 个文件")

    # ---- B2 行尾无关 ----
    def test_B2_lineending_insensitive(self):
        before, _ = bs.compute(self.root, "sidecar")
        p = self.root / "backend" / "main.py"
        # 纯行尾变更：内容逐字不变，仅 LF → CRLF（原文件是 "x = 1\n"）
        p.write_bytes(b"x = 1\r\n")
        after, _ = bs.compute(self.root, "sidecar")
        self.assertEqual(before, after,
                         "纯行尾变更不得改变戳（否则一次 patch 就假红）")
        # 再换回 CR 单字符行尾，同样必须相同
        p.write_bytes(b"x = 1\r")
        after2, _ = bs.compute(self.root, "sidecar")
        self.assertEqual(before, after2, "CR 行尾同样应归一化")

    # ---- B3 负控：源码敏感 ----
    def test_B3_negative_control_source_sensitive(self):
        before, _ = bs.compute(self.root, "sidecar")
        p = self.root / "backend" / "main.py"
        p.write_text("x = 999\n", encoding="utf-8")   # 真改内容
        after, _ = bs.compute(self.root, "sidecar")
        self.assertNotEqual(before, after,
                            "负控失败：改了纳入范围的源码，戳却没变 ⇒ 门禁无判别力")

    # ---- B4 负控反向：范围外不敏感 ----
    def test_B4_out_of_scope_insensitive(self):
        before, _ = bs.compute(self.root, "sidecar")
        # 测试文件与一次性脚本都不进产物 ⇒ 改它们不该让 sidecar 判陈旧
        (self.root / "backend" / "test_foo.py").write_text("def t(): pass\n", encoding="utf-8")
        (self.root / "backend" / "_tmp_probe.py").write_text("p = 1\n", encoding="utf-8")
        after, _ = bs.compute(self.root, "sidecar")
        self.assertEqual(before, after,
                         "改 test_*.py / _*.py 不该改变 sidecar 戳（否则假红）")

    # ---- B5 无记录 ⇒ 陈旧（fail-closed）----
    def test_B5_no_record_is_stale(self):
        r = bs.check("sidecar", root=self.root, record_path=self.rec)
        self.assertFalse(r["ok"], "无构建记录必须判陈旧（不得默认放行）")
        self.assertIn("无", r["reason"])

    # ---- B6 记录后 ⇒ 新鲜 ----
    def test_B6_recorded_is_fresh(self):
        stamp, n = bs.compute(self.root, "sidecar")
        bs.write_record("sidecar", stamp, n, "1.2.3", path=self.rec)
        r = bs.check("sidecar", root=self.root, record_path=self.rec)
        self.assertTrue(r["ok"], f"记录与源码一致却判陈旧: {r['reason']}")
        # 负控：改了源码后，同一条记录必须失效
        (self.root / "backend" / "main.py").write_text("x = 42\n", encoding="utf-8")
        r2 = bs.check("sidecar", root=self.root, record_path=self.rec)
        self.assertFalse(r2["ok"], "源码已改，记录必须失效（否则跳过陈旧产物）")

    # ---- B7 负控：产物 md5 绑定 ----
    def test_B7_artifact_md5_binding(self):
        art = self.tmp / "fake-sidecar.exe"
        art.write_bytes(b"ORIGINAL-BINARY")
        bs.record_build("sidecar", [art], version="1.2.3",
                        root=self.root, path=self.rec)
        ok = bs.check_artifacts("sidecar", [art], record_path=self.rec)
        self.assertTrue(ok["ok"], f"产物未变却判不符: {ok['reason']}")
        # 负控：产物被替换 ⇒ 必须报红
        art.write_bytes(b"TAMPERED-BINARY")
        bad = bs.check_artifacts("sidecar", [art], record_path=self.rec)
        self.assertFalse(bad["ok"], "负控失败：产物被替换却没检出 ⇒ md5 绑定无效")
        self.assertIn(art.name, bad["changed"])
        # 负控2：产物被删除 ⇒ 必须报红（不是静默通过）
        art.unlink()
        gone = bs.check_artifacts("sidecar", [art], record_path=self.rec)
        self.assertFalse(gone["ok"], "产物缺失必须报红")
        self.assertIn(art.name, gone["missing"])

    # ---- B8 排除清单生效 ----
    def test_B8_excludes(self):
        # 造出应当被排除的东西
        (self.root / "backend" / "__pycache__").mkdir()
        (self.root / "backend" / "__pycache__" / "main.cpython-314.pyc").write_bytes(b"junk")
        (self.root / "backend" / "build").mkdir()
        (self.root / "backend" / "build" / "junk.py").write_text("q=1\n", encoding="utf-8")
        (self.root / "backend" / "_build_version.py").write_text("BUILD_VERSION='9'\n", encoding="utf-8")
        files = bs._collect(self.root, bs.SIDECAR_GLOBS)
        rels = [p.relative_to(self.root).as_posix() for p in files]
        for bad in ("__pycache__", "/build/", "_build_version.py"):
            self.assertFalse(any(bad in r for r in rels),
                             f"排除清单失效：戳里含 {bad} ⇒ 自指/假红。实际: {rels}")

    # ---- B10 负控：伪造「只写 stamp」的记录必须被产物 md5 拦下 ----
    def test_B10_forged_stamp_record_rejected(self):
        """实测发现的漏洞回归：只验源码戳时，一条只写 stamp 的伪造记录即可骗过门禁。"""
        art = self.tmp / "flowcap-backend-x86_64-pc-windows-msvc.exe"
        art.write_bytes(b"OLD-BINARY-FROM-BEFORE-THE-EDIT")
        stamp, n = bs.compute(self.root, "sidecar")
        # 伪造：stamp 正确，但**不含产物 md5**（模拟手工写记录 / 产物被换掉）
        bs.write_record("sidecar", stamp, n, "1.2.3", path=self.rec)
        # 单验源码戳：会通过（这正是漏洞所在）
        self.assertTrue(bs.check("sidecar", root=self.root, record_path=self.rec)["ok"])
        # 组合校验：必须拒绝（产物无 md5 记录 ⇒ 不能证明是本次构建的）
        r = bs.verify("sidecar", [art], root=self.root, record_path=self.rec)
        self.assertFalse(r["ok"],
                         "负控失败：伪造的 stamp 记录骗过了组合校验 ⇒ 陈旧产物会上线")
        self.assertIs(r["artifacts_ok"], False)

    # ---- B11 正控：真实构建记录（含 md5）⇒ 组合校验通过 ----
    def test_B11_verify_positive(self):
        art = self.tmp / "flowcap-recv-daemon-x86_64-pc-windows-msvc.exe"
        art.write_bytes(b"REAL-BINARY")
        bs.record_build("sidecar", [art], version="1.2.3",
                        root=self.root, path=self.rec)
        r = bs.verify("sidecar", [art], root=self.root, record_path=self.rec)
        self.assertTrue(r["ok"], f"真实记录却判不可跳过: {r['reason']}")
        self.assertIs(r["artifacts_ok"], True)

    # ---- 额外：记录文件读写往返 ----
    def test_B9_record_roundtrip(self):
        bs.write_record("rust", "deadbeef", 7, "0.1.0", path=self.rec)
        data = json.loads(self.rec.read_text(encoding="utf-8"))
        self.assertEqual(data["rust"]["stamp"], "deadbeef")
        self.assertEqual(data["rust"]["files"], 7)
        # 再写 sidecar 不得覆盖 rust（合并语义）
        bs.write_record("sidecar", "cafe", 3, "0.1.0", path=self.rec)
        data2 = json.loads(self.rec.read_text(encoding="utf-8"))
        self.assertIn("rust", data2, "写 sidecar 不得丢掉 rust 记录（合并而非覆盖）")
        self.assertIn("sidecar", data2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
