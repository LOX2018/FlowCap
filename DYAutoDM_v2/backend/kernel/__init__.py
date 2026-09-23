# -*- coding: utf-8 -*-
"""`kernel` = 全仓**已收敛事实/判据**的单一来源包（2026-09-23 补齐真实包形态）。

为什么要有 `__init__.py`（P2-5 实测）：此前本目录**没有** `__init__.py`，
Python 只在「恰好 import 了 `kernel.truth` 的进程」里把它当隐式 namespace
package 加载 —— 有 `__init__.py` 才是真包、才可被静态工具/打包器一致识别
（PyInstaller 的 hiddenimport 分析、pytest 收集、IDE 跳转都依赖它）。
"""
