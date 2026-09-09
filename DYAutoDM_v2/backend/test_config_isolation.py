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
