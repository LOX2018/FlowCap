"""B4 测试隔离：所有配置类测试共用一套 DY_APP_ROOT 隔离逻辑。

背景（2026-09-09 排查）：
  backend/database.py 在**导入时**根据 DY_APP_ROOT 固化 SQLite 路径，
  而各测试文件在文件顶部设置 os.environ。unittest discover 会把所有
  test_*.py 导入到**同一个进程**，于是第一个导入 database 的文件决定了
  DB 路径 → 测试之间互相看到对方的写入 → 单独跑全绿、整体跑失败。
  且 DB 落在源码目录 backend/data/ 会污染真实配置。

解决：本模块在导入时立刻把 DY_APP_ROOT 指向独立临时目录，
并保证 database 尚未被导入；若已被导入则强制重定位其 DB 路径。
"""

import os
import sys
import tempfile
import unittest

_ROOT = os.path.join(tempfile.gettempdir(), "dyautodm_cfgtest_root")
os.makedirs(_ROOT, exist_ok=True)
os.environ["DY_APP_ROOT"] = _ROOT

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def fresh(section: str | None = None):
    """返回一个干净隔离的 app_config 模块，并可选重置某 section。"""
    for m in [k for k in list(sys.modules) if k in ("database",) or k.startswith("services.")]:
        del sys.modules[m]
    from services import app_config as ac
    if section:
        ac.reset_section(section)
    return ac


class TestConfigIsolation(unittest.TestCase):
    """隔离层自证 —— 本模块既被契约要求运行，就必须真的跑出判据。

    #### 为什么补这一组（2026-09-24 实测）

    本模块原为**纯 helper（0 个用例、32 行）**，但契约
    `C-03-credential-verification.md` §5 把
    `py314 -m unittest test_config_isolation` 列为 C-03 的验证命令。
    于是 G14 报「未通过 backend/test_config_isolation.py」；而 G14 是
    **逐用例登记、禁文件级豁免**的（`check_contracts.py` 注释明写），
    故只能补**真判据**，不能靠豁免绕开。

    这三条断言不是占位符：若隔离失效（DY_APP_ROOT 未设 / 指回源码树 /
    fresh() 坏掉），它们**必然变红**。
    """

    def test_app_root_is_isolated_temp_not_repo(self):
        """DY_APP_ROOT 必须在**临时隔离区内**，且不在源码树内。

        #### 为什么不断言「等于本模块的 _ROOT」（2026-09-24 实测纠正）

        组合运行时（`unittest discover` 单进程导入全部模块），
        `test_capability_probe` 会合法地把根改指到它自己的隔离子目录
        （`<tmp>/dyautodm_cfgtest_root/cap_probe_<pid>`）。此时断言「必须等于
        本模块的 `_ROOT`」会把**合法隔离**判成失败 —— 这正是「顺序相关假失败」
        的同一类缺陷。真不变量应**顺序无关**：

          · 非空（未设 ⇒ 隔离失效）
          · 位于系统临时目录之下（真隔离区，而非生产数据根）
          · 不在源码树内（防污染仓库，GitHub 上 .gitignore 遮蔽了它）
        """
        root = os.environ.get("DY_APP_ROOT", "")
        self.assertTrue(root, "DY_APP_ROOT 未设置 —— 测试隔离失效")
        rp = os.path.abspath(root).lower()
        tmp = os.path.abspath(tempfile.gettempdir()).lower()
        self.assertTrue(rp.startswith(tmp),
                        f"DY_APP_ROOT 不在系统临时隔离区（可能在写生产数据）: {root}")
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertFalse(rp.startswith(os.path.abspath(repo).lower()),
                         f"DY_APP_ROOT 落在源码树内（会污染仓库）: {root}")

    def test_database_resolves_under_isolated_root(self):
        """database 的真实解析结果必须落在隔离根下，绝不落在源码树。

        这是本条的核心：断言的是**真实解析路径**（`database._db_path()`），
        而不是我声明的常量 —— 隔离「说到了」不算，要「真做到」。
        """
        import database
        p = os.path.abspath(str(database._db_path()))
        self.assertTrue(
            p.lower().startswith(os.path.abspath(_ROOT).lower()),
            f"DB 路径未落在隔离根: {p}")
        repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertFalse(
            p.lower().startswith(os.path.abspath(repo).lower()),
            f"DB 落在源码树内（污染仓库）: {p}")

    def test_fresh_returns_usable_app_config(self):
        """fresh() 必须返回可用的 app_config（含 section 重置路径）。"""
        ac = fresh()
        self.assertTrue(hasattr(ac, "get"), "fresh() 未返回可用 app_config")
        self.assertTrue(callable(getattr(ac, "reset_section", None)),
                        "app_config 缺 reset_section（fresh(section=...) 会坏）")

