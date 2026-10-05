# -*- coding: utf-8 -*-
"""测试数据隔离助手（2026-10-04 事故后新增）。

🔴 事故与定论（以下均由实测实验得出，不是推测）
================================================
`test_lead_nickname.py` 的 `DELETE FROM ai_leads` 清空了生产库唯一的真实线索并注入
假数据，而测试显示全绿。逐实验复盘：

  · `vbrowser.app_root()` 的**第一优先级就是读 `DY_APP_ROOT`** —— env 隔离机制本身
    是正确的，`test_config_isolation` 那套设计也没错。
  · 事故链是**两个缺陷叠加**：
      ① 我在同一次 `terminal` 调用里 `export DY_APP_ROOT=<生产路径>` 再跑测试
         —— **这是我的操作失误**，不是测试代码的错；
      ② 测试用 `os.environ.setdefault(...)` —— `setdefault` 不覆盖已存在的值，
         于是 ① 污染了 ② ⇒ 临时根从未生效。
  · `setdefault` 还有第二个坑：**同进程测试串扰**。`unittest discover` 把所有
    `test_*.py` 导入同一进程，前一个测试一旦直接赋值 `DY_APP_ROOT`，后一个测试的
    `setdefault` **永远不生效** ⇒ 两者共享同一 db、互看写入（实验 D 复现）。

🔴 正解：`setdefault` → **显式赋值**
====================================
显式赋值同时解决 ① 和 ②：无论 env 是否被外部污染、是否被前一个测试占用，当前测试
都会把根**钉到自己**的临时目录。这是最小改动，且保持与既有隔离契约完全一致。

用法（本文件是唯一正确入口）::

    from test_isolation import env_isolate
    ROOT, db = env_isolate("my_test")   # ① 必须在 import database 之前
    import database                     # ② 之后才能 import
    ...
    # 用 importlib.reload() 的场景：reload 前再钉一次自己的根
    env_isolate("my_test", root=ROOT)
    importlib.reload(mod)

每个写库测试文件应建**自己**的根，不要复用别人的 `_ROOT`（会踩串扰坑）。

🔴 目录必须先建（实测坑）
==========================
`vbrowser.app_root()` 在 `DY_APP_ROOT` 指向**不存在**目录时只打 warning 然后回退，
实测会落到**源码树** `DYAutoDM_v2/data/`。故本函数内部先 `os.makedirs` 再赋值 ——
顺序反了等于没设。

🔴 为什么不用 monkeypatch `member_ctx.db_path`
==============================================
早期版本曾直接 pin `member_ctx.db_path` / `vbrowser.app_root` 为常量。实测这会让
`test_config_isolation.TestConfigIsolation.test_database_resolves_under_isolated_root`
假失败 —— 那条断言检查的是**真实解析路径**是否落在隔离根下，pin 会让路径来自本测试
而非 env，契约被绕过（`test_lead_nickname + test_config_isolation` 组合跑必红）。
故最终实现只走 env。

🔴 为什么不做「空库断言」
==========================
env 隔离下库落在 `<root>/members/<mid>/data/`，`mid` 由**盘上会话文件**决定（固定值）。
本助手只保证「指向自己的临时根」，**不保证库为空** —— 需要干净表的测试自行在
`setUp` 里 `DELETE FROM`（既有惯例）。
"""
from __future__ import annotations

import os
import tempfile
from typing import Tuple


def env_isolate(tag: str = "test", root: str | None = None) -> Tuple[str, str]:
    """把 `DY_APP_ROOT` **显式**钉到本测试专属临时目录，并重建数据库连接。

    参数
    ----
    tag : 隔离根命名前缀（自动拼 pid，天然唯一）。
    root: 已建好的根目录；给了就直接用，不再新建。

    返回 ``(隔离根目录, 预期数据库路径)``。

    🔴 必须在 `import database` 之前调用：`database.get_db()` 有全局 `_conn`
    单例，`reset_connection()` 只在已有连接时才有意义；若前一个测试已建连且
    未重置，会继续用旧连接（指向旧根）。

    🔴 只保证「指向自己的临时根」，**不保证库为空**（见模块文档
    「为什么不做空库断言」）。需要干净表的测试自行在 `setUp` 里 `DELETE FROM`。
    """
    if root is None:
        root = os.path.join(tempfile.gettempdir(), f"{tag}_{os.getpid()}")
    # 目录必须先存在：vbrowser.app_root() 在 DY_APP_ROOT 指向不存在目录时
    # 只打 warning 然后回退（实测落到源码树），等于白设。
    os.makedirs(os.path.join(root, "data"), exist_ok=True)

    # 显式赋值 —— 不用 setdefault，抗外部污染 + 抗同进程串扰
    os.environ["DY_APP_ROOT"] = root

    import database  # noqa: E402  （必须在上面设 env 之后 import）
    database.reset_connection()

    return root, os.path.join(root, "data", "dyautodm.db")


# 别名：既有调用点用的是 isolate(...)
isolate = env_isolate
