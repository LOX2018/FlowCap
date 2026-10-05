# -*- coding: utf-8 -*-
"""接口层 —— 域模块的最终类绑定（门面模式的接线）

## 为什么需要本模块

阶段1 把单体 `douyin_api.py` 按业务域拆成 9 个 mixin 模块（照源项目
`api/client_*.rs`）。原文件内有 **109 处** `DouyinAPI.xxx(...)` 形式的内部调用，
拆分后这些名字在各自模块的全局作用域内**未定义**。

为避免改动这 109 处（以及 300+ 处外部调用），采用**绑定注入**：
  1. 各域模块声明模块级 `DouyinAPI = None`；
  2. `douyin_api.py` 组装出最终类后调用 `bind_all(DouyinAPI)`；
  3. 本函数把最终类写入**每个域模块的全局名**（含自身）。

于是域模块函数体内 `DouyinAPI.xxx(...)` 在**运行时**解析到最终类，
行为与原单体文件完全一致。
"""
from __future__ import annotations

import importlib
from typing import Any

#: 参与绑定的域模块（与 `douyin_api.py` 的 mixin 顺序一致）
DOMAIN_MODULES = (
    "user", "video", "comments", "collection", "relations",
    "notice", "search", "live", "im",
)


def bind_all(cls: Any) -> None:
    """把最终组装类注入每个域模块的全局名 `DouyinAPI`。

    幂等：重复调用安全（后注入覆盖前值，值相同）。
    """
    for dom in DOMAIN_MODULES:
        mod = importlib.import_module(f"dy_apis.client_{dom}")
        mod.DouyinAPI = cls
