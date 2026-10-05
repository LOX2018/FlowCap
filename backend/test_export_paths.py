# -*- coding: utf-8 -*-
"""导出目录分类（2026-10-02 用户要求：按图片/表格等类型划分）。

## 判据（每条都能被刻意构造的违规样本打红）

| ID | 断言 | 负控 |
|---|---|---|
| E1 | 根目录读配置中心 `system.export_dir` | 设成临时目录 ⇒ 根目录必须落那里 |
| E2 | 四个分类目录按需创建、中文命名、互不混用 | 写文件后核对各目录内容 |
| E3 | 旧参数名（`chatlab`）映射到「聊天记录」 | `dir_for("chatlab").name == "聊天记录"` |
| E4 | `safe_file` 放行中文名、拦穿越 | 参照 `chatlab_export._safe_export_file` 的既有教训 |
| E5 | `all_dirs` 逐项兜住单个分类失败 | 不因一个分类建目录失败而整页崩 |
| E6 | `_app_root` 解析失败**抛**，不静默退化到 cwd | 拦下 `auto_dm` 导入 ⇒ 必须抛，且 cwd 下不得建出 `exports` |
| E7 | `chatlab_export._safe_export_file` 与本模块**同一份**判据 | 行为逐例等价 + 源码里不再出现 `relative_to` |
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
for p in (BACKEND, BACKEND.parent):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

# 🟡 R16-B 豁免（2026-10-04 登记）：本文件必须解析 BACKEND.parent 路径（导出路径契约测试），
# 无法隔离到临时根 —— 与 5 个直播部署根测试同类。setdefault → 显式赋值（不改变目标值，
# 仅消除「键已存在时空操作」的假隔离坑）。
os.environ["FLOWCAP_APP_ROOT"] = str(BACKEND.parent)


class _Cfg:
    """配置中心桩：只认 system.export_dir，避免真连数据库。"""

    value = ""

    @staticmethod
    def get(section, key, default=None, scope=None):  # noqa: ANN001
        if section == "system" and key == "export_dir":
            return _Cfg.value
        return default


class TestExportPaths(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        from services import export_paths

        cls.ep = export_paths
        cls.tmp = tempfile.mkdtemp(prefix="export_paths_")

    def setUp(self) -> None:
        # 桩：只替换 `app_config.get`（export_paths.root 内部按模块属性取），
        # setUp/tearDown 严格成对还原 ⇒ 真实配置中心不被污染。
        from services import app_config as real_ac

        self._real_get = real_ac.get
        real_ac.get = _Cfg.get            # type: ignore[assignment]
        _Cfg.value = self.tmp

    def tearDown(self) -> None:
        from services import app_config as real_ac

        real_ac.get = self._real_get      # type: ignore[assignment]
        _Cfg.value = ""

    # ── E1 根目录读配置中心 ────────────────────────────────────────────────
    def test_e1_root_follows_config(self) -> None:
        self.assertEqual(str(self.ep.root()), str(Path(self.tmp)))

    def test_e1_root_raises_on_bad_path_not_silent_fallback(self) -> None:
        """🔴 旧实现 `try/except` 静默回落默认目录 ⇒ 用户在别处找不到文件。
        现必须**抛**（由 API 层转 4xx/5xx 如实上报），不静默换地方。

        ⚠️ 负控的构造要讲究：最初用 `C:\\definitely\\not\\exist\\zzz`，
        在 Windows 上 `mkdir(parents=True)` **会把整条链建出来** → 不抛，
        判据假失败。改为「父路径是一个已存在的**文件**」——
        任何平台上都无法在其下建目录，这才是真正不可创建的输入。
        """
        blocker = Path(self.tmp) / "i_am_a_file.txt"
        blocker.write_text("x", encoding="utf-8")
        _Cfg.value = str(blocker / "sub")        # 父是文件 ⇒ 必失败
        try:
            with self.assertRaises(Exception):
                self.ep.root()
        finally:
            _Cfg.value = self.tmp

    # ── E2 四个分类、中文名、互不混用 ──────────────────────────────────────
    def test_e2_four_categories_created(self) -> None:
        names = {k: self.ep.dir_for(k).name for k in self.ep.CATEGORIES}
        self.assertEqual(names, {
            "backup": "备份", "chat": "聊天记录",
            "table": "表格", "image": "图片",
        })

    def test_e2_files_land_in_own_category(self) -> None:
        (self.ep.dir_for("table") / "a.xlsx").write_text("x")
        (self.ep.dir_for("image") / "b.png").write_text("y")
        (self.ep.dir_for("backup") / "c.json").write_text("{}")
        # 各自的兄弟目录**不得**出现对方文件
        self.assertFalse((self.ep.dir_for("image") / "a.xlsx").exists())
        self.assertFalse((self.ep.dir_for("table") / "b.png").exists())
        self.assertFalse((self.ep.dir_for("backup") / "b.png").exists())
        # 未登记分类回落根目录
        self.assertEqual(self.ep.dir_for("nonexistent").name, Path(self.tmp).name)

    # ── E3 旧参数名兼容 ───────────────────────────────────────────────────
    def test_e3_legacy_alias_chatlab(self) -> None:
        self.assertEqual(self.ep.dir_for("chatlab").name, "聊天记录")

    def test_e3_chatlab_export_delegates_to_ssot(self) -> None:
        """`chatlab_export.default_export_dir` 必须走同一套（同一根目录）。"""
        from services import chatlab_export as ce

        self.assertEqual(str(ce.default_export_dir()), str(self.ep.dir_for("chat")))

    def test_e3_backup_export_dir_delegates_to_ssot(self) -> None:
        from services import backup as bk

        self.assertEqual(str(bk._default_export_dir()), str(self.ep.dir_for("backup")))

    # ── E4 安全取文件 ─────────────────────────────────────────────────────
    def test_e4_safe_file_accepts_chinese_name(self) -> None:
        p = self.ep.dir_for("chat") / "四川工伤-张老师_export.jsonl"
        p.write_text("{}", encoding="utf-8")
        self.assertIsNotNone(self.ep.safe_file("chat", p.name))

    def test_e4_safe_file_blocks_traversal(self) -> None:
        p = self.ep.dir_for("chat") / "ok.jsonl"
        p.write_text("{}", encoding="utf-8")
        for bad in ("../" + p.name, "..\\" + p.name, "a/" + p.name,
                    "", ".", "..", "not-exist.jsonl", p.name + "/../x"):
            self.assertIsNone(self.ep.safe_file("chat", bad), f"{bad!r} 应被拒")

    def test_e4_safe_file_cannot_escape_to_other_category(self) -> None:
        """跨分类取文件必须失败（否则「图片」分类能读到「备份」的内容）。"""
        p = self.ep.dir_for("backup") / "secret.json"
        p.write_text("{}", encoding="utf-8")
        self.assertIsNone(self.ep.safe_file("chat", p.name))

    # ── E5 逐项兜住 ───────────────────────────────────────────────────────
    def test_e5_all_dirs_reports_every_category(self) -> None:
        d = self.ep.all_dirs()
        self.assertEqual(set(d), set(self.ep.CATEGORIES))
        for v in d.values():
            self.assertTrue(v["dir"].endswith(v["name"]), v)

    def test_e5_all_dirs_surfaces_root_failure_instead_of_hiding_it(self) -> None:
        """`all_dirs` 的逐项兜底**不许**把「根目录解析失败」也一起吞掉。

        兜底存在的意义是「一个分类建目录失败，不让整页 500」；
        若根目录本身解析不出来，每项都该如实带 `error` 上报，
        而不是回一份空 `dir` 让前端显示「导出目录为空」。

        ⚠️ 前提：`export_dir` 必须**置空**才会走到 `_app_root()`
        （`root()` 里 `Path(v) if v else _app_root()/exports`），
        沿用 setUp 注入的非空值会直接短路、压根碰不到这段逻辑。
        """
        _Cfg.value = ""
        real = self.ep._app_root
        self.ep._app_root = _boom        # type: ignore[assignment]
        try:
            d = self.ep.all_dirs()
        finally:
            self.ep._app_root = real     # type: ignore[assignment]
            _Cfg.value = self.tmp
        for k, v in d.items():
            self.assertEqual(v["dir"], "", k)
            self.assertIn("_Boom", v["error"], v)

    # ── E6 `_app_root` 不静默退化（2026-10-03 修的 `except: pass`）──────────
    def test_e6_app_root_failure_raises_not_silent_cwd(self) -> None:
        """🔴 旧实现 `except Exception: pass → os.getcwd()`。

        cwd 随**进程启动方式**漂移（桌面快捷方式 / 终端 / 打包 exe 各不相同），
        实测拦下 `auto_dm` 导入后 `root()` 静默返回 `<cwd>/exports` 并把目录
        建了出来 —— 零异常零日志，用户在别处找不到文件。
        现在解析失败必须**抛**（前提同 E5：`export_dir` 置空才会走 `_app_root`）。
        """
        _Cfg.value = ""
        real = self.ep._app_root
        self.ep._app_root = _boom        # type: ignore[assignment]
        try:
            with self.assertRaises(_Boom):
                self.ep.root()
        finally:
            self.ep._app_root = real     # type: ignore[assignment]
            _Cfg.value = self.tmp

    def test_e6_real_app_root_never_returns_cwd(self) -> None:
        """真实解析（不注入 export_dir）时，根目录**不得**等于 cwd。

        这是旧 `except: pass` 兜底链留下的指纹：真跑时若 `_app_root()`
        解析不出东西就会返回 `os.getcwd()`，于是 `<cwd>/exports` 成为事实根。
        """
        _Cfg.value = ""
        try:
            r = self.ep._app_root().resolve()
            self.assertNotEqual(r, Path(os.getcwd()).resolve(),
                                "_app_root() 回落到了 cwd（静默退化）")
            self.assertTrue(r.is_dir(), f"{r} 不存在")
        finally:
            _Cfg.value = self.tmp

    def test_e6_app_root_does_not_import_accounts(self) -> None:
        """取 `app_root` 不该绕道 `auto_dm.accounts`（379 个模块的纯负担）。

        `app_root` 的定义处在 `auto_dm.vbrowser`，`accounts` 只是 re-export
        （`auto_dm/accounts.py:30`）；绕道它还等于给 `except` 造了一个可以悄悄
        吃掉失败的位置。

        ⚠️ 用**子进程**跑（不是在本进程里 `del sys.modules[...]`）：M-17
        门禁禁止测试拆散进程级模块（`test_m17_order_independence`），而这里
        需要的恰恰是「一个干净的模块表」。子进程天然满足，且判据更强。
        """
        code = (
            "import os,sys;"
            f"sys.path[:0]=[{str(BACKEND)!r},{str(BACKEND.parent)!r}];"
            "os.environ.pop('FLOWCAP_APP_ROOT',None);"
            "from services import export_paths;"
            "print('ROOT='+str(export_paths._app_root()));"
            "print('HAS_ACCOUNTS='+str('auto_dm.accounts' in sys.modules));"
            "print('HAS_VBROWSER='+str('auto_dm.vbrowser' in sys.modules));"
        )
        r = subprocess.run([sys.executable, "-c", code], cwd=str(BACKEND),
                           capture_output=True, text=True, timeout=180)
        self.assertEqual(r.returncode, 0, r.stderr)
        out = r.stdout
        self.assertIn("HAS_VBROWSER=True", out, out)
        self.assertIn("HAS_ACCOUNTS=False", out,
                      "取 app_root 不该拉起 auto_dm.accounts：" + out)

    def test_e6_app_root_env_wins_without_any_import(self) -> None:
        """`FLOWCAP_APP_ROOT` 存在即用，且**完全不触发** import（测试/隔离态最常用）。"""
        probe = os.path.join(self.tmp, "probe_root")
        os.makedirs(probe, exist_ok=True)
        code = (
            "import os,sys;"
            f"sys.path[:0]=[{str(BACKEND)!r},{str(BACKEND.parent)!r}];"
            f"os.environ['FLOWCAP_APP_ROOT']={probe!r};"
            "from services import export_paths;"
            "print('ROOT='+str(export_paths._app_root()));"
            "print('ANY_AUTODM='+str([m for m in sys.modules "
            "if m.startswith('auto_dm')]));"
        )
        r = subprocess.run([sys.executable, "-c", code], cwd=str(BACKEND),
                           capture_output=True, text=True, timeout=180)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(f"ROOT={probe}", r.stdout, r.stdout)
        self.assertIn("ANY_AUTODM=[]", r.stdout,
                      "命中环境变量时不该 import 任何 auto_dm 模块：" + r.stdout)

    # ── E7 与 `chatlab_export` 单一判据（无重复实现）────────────────────────
    def test_e7_safe_export_file_delegates_to_ssot(self) -> None:
        """`_safe_export_file` 必须**委派** SSOT，不得再留一份逐行复制的判据。

        🔴 判法说明（这里踩过一次坑）：最初只写「读源码断言没有 `relative_to`」+
        「两入口行为一致」，结果把**旧的复制实现**塞回内存后测试照样绿 ——
        ① 源码断言读的是磁盘文件，改内存里的函数它看不见；
        ② 复制实现在这些输入上行为本就一致 ⇒ 行为断言天然分辨不出。
        真正能打红的判据是**侦测委派本身**：`export_paths.safe_file` 被调用了没有。
        一旦有人把判据内联回去，spy 就不响。
        """
        from services import chatlab_export as ce
        from services import export_paths

        p = self.ep.dir_for("chat") / "四川工伤-张老师_20260917_export.jsonl"
        p.write_text("{}", encoding="utf-8")
        calls: list[tuple] = []
        real = export_paths.safe_file

        def _spy(category, filename):        # noqa: ANN001
            calls.append((category, filename))
            return real(category, filename)

        export_paths.safe_file = _spy        # type: ignore[assignment]
        try:
            self.assertIsNotNone(ce._safe_export_file(p.name))
            self.assertIsNone(ce._safe_export_file("../evil.jsonl"))
        finally:
            export_paths.safe_file = real    # type: ignore[assignment]
        self.assertEqual([f for _c, f in calls],
                         [p.name, "../evil.jsonl"],
                         f"_safe_export_file 未委派 export_paths.safe_file：{calls}")

    def test_e7_safe_export_file_matches_ssot_on_every_input(self) -> None:
        """两入口对同一批输入必须给出同一结果（防两份判据各自演化后分叉）。"""
        from services import chatlab_export as ce

        p = self.ep.dir_for("chat") / "四川工伤-张老师_20260917_export.jsonl"
        p.write_text("{}", encoding="utf-8")
        ok = self.ep.dir_for("chat") / "ok.jsonl"
        ok.write_text("{}", encoding="utf-8")
        for c in [p.name, ok.name, "../" + ok.name, "..\\" + ok.name,
                  "a/" + ok.name, "", ".", "..", "not-exist.jsonl",
                  ok.name + "/../x", "\x00bad", None, "   "]:
            self.assertEqual(ce._safe_export_file(c),
                             self.ep.safe_file("chat", c),
                             f"{c!r} 两入口判据不一致")
        # 跨分类：委派后必然同样越界（旧复制版与分类白名单行为可能分叉）
        secret = self.ep.dir_for("backup") / "secret.json"
        secret.write_text("{}", encoding="utf-8")
        self.assertIsNone(ce._safe_export_file(secret.name))
        src = (BACKEND / "services" / "chatlab_export.py").read_text("utf-8")
        self.assertNotIn("relative_to", src,
                         "chatlab_export 不应再自建包含性判据（应委派 SSOT）")


def _boom() -> Path:
    """`_app_root()` 解析失败的替身（E5/E6 用）。"""
    raise _Boom("模拟应用根解析失败")


class _Boom(RuntimeError):
    pass


if __name__ == "__main__":
    unittest.main(verbosity=2)
