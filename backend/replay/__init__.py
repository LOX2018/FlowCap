# -*- coding: utf-8 -*-
"""离线回放层（Offline Replay Layer）· 隔离沙箱。

## 这层为什么存在

`flowcap-dev-guards` 记载的实机验证成本链是：真 BCC → 真 profile → 真抖音 →
串行排队（单 profile / 单端口 / 单数据根三条铁律使然）。
结果是：**每个回归用例都要占用唯一一台"真机"，验证产能恒为 1**。

本层把"**已冻结的真实样本**"变成可无限重放的输入，让
「非首次确认」的验证脱离真机 —— 零 BCC、零网络、零真实数据根，因而**可并发**。

## 与「样本必须本次实拉」铁律的关系（重要，勿误读为违规）

`references/capture_pipeline_repro.md` 的铁律「样本必须本次实拉」**继续有效**，
但其适用范围需精确化：

| 场景 | 正解 | 依据 |
|---|---|---|
| **新增/修订契约**（接口字段变、签名变、域名变） | **必须实拉** —— 因为要确认"世界变了没有" | D6 / R8 / 体系级铁律 2（先分「世界变了」还是「自家写错了」）|
| **已定契约的回归验证**（改动是否破坏既有解析） | **跑回放** —— 世界未变，比的是"自家代码有没有写错" | 测试金字塔：回归不该依赖外部世界 |

⇒ 本层**不允许**被用作"证明抖音侧契约仍然有效"的依据；它只证明
「在已冻结的输入上，本仓代码行为未回归」。**新契约仍须一次实拉确认**，
确认后由 `recorder` 落为新 fixture 并冻结（sha256 进 manifest）。

## 三条硬约束（沙箱自证）

1. `FLOWCAP_APP_ROOT` 只允许指向**独立临时目录**；指向真实数据根或源码树即 `raise`。
2. 样本**不可变**：`loader` 按 manifest 的 sha256 校验，不匹配即拒绝加载。
3. 不 import 任何会打网/开浏览器的模块（由用例侧保证；沙箱只保证环境不指向真机）。
"""

from .sandbox import DesignRootForbidden, Sandbox, activate, active_root
from .loader import FixtureMissing, FixtureTampered, list_fixtures, load_fixture

__all__ = [
    "Sandbox",
    "activate",
    "active_root",
    "DesignRootForbidden",
    "load_fixture",
    "list_fixtures",
    "FixtureMissing",
    "FixtureTampered",
]
