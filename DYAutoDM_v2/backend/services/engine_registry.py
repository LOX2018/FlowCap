"""直播引擎**编排层**：`account → Engine` 表（ADR-002 §5.2）。

## 为什么需要它（规格 = ADR-002，用户 2026-09-22 拍板）

改造前全仓只有**一个**引擎实例 `app.state.adm = AutoDM()`，五个引擎端点共用它，
`EngineState` 是**实例级单一状态机**。实测后果（可复跑）：

> 张老师 `RUNNING` 时对小助理 `POST /api/engine/start`
> → `{"ok":true,"state":"running","already":true}`（**静默假成功**）

⇒ 用户的「两个账号同时盯着各自的直播间」在旧实现下**做不到**，且失败是**无声**的。

本模块把「单例」收敛为**按账号取实例**，并让冲突**显式失败**：

| 场景 | 行为 |
|---|---|
| 不同账号各自启动 | 各自一个 `AutoDM` 实例，互不影响（语义 (A)） |
| **匿名单任务**（无 acct） | 固定键 `anonymous`，全进程最多一个 |
| 同账号重复启动 | 不新建实例，交给 `AutoDM.start()` 的既有状态机（返回 409 由 API 层给出） |
| 请求未给 acct 且已有多账号任务 | **显式失败**（不猜账号），由 API 层返回 409 |

## 设计约束（改本模块时必须维持）

1. **单账号零回归**：单账号/匿名路径仍是「一个 AutoDM」，键的引入不改变其行为。
2. **不得绕过统一发送闸门**：本模块只管**实例归属**，不碰发送链路。
3. **不得为并发新建 BCC**：`AutoDM` 内部仍走既有 `browser_gate` / BCC 单例（一账号一容器）。
4. **停不下来要说出来**：`shutdown_all()` 必须逐个账号 shutdown 并汇总异常，不得静默吞。
"""
from __future__ import annotations

from typing import Optional

from loguru import logger

# 匿名（未指定监听账号）任务的固定键 —— 全进程唯一，语义等价于旧的单例。
ANONYMOUS_KEY = "anonymous"


class EngineRegistry:
    """`account → AutoDM` 表。

    惰性创建：只为**真正被启动过**的账号建实例，不预建（避免为每个账号拉 `AutoDM`
    构造里的 settings 快照，也避免「从未启动的账号」出现在任务列表里）。
    """

    def __init__(self, factory) -> None:
        # factory: () -> AutoDM（注入以便测试替身，并避免本模块 import core.auto_dm 造成环）
        self._factory = factory
        self._engines: dict[str, "object"] = {}

    # ── 取实例 ──────────────────────────────────────────────────────────
    def get(self, account: Optional[str]) -> "object":
        """取/建该账号的引擎实例（**按需创建**，不销毁）。"""
        key = self._key(account)
        eng = self._engines.get(key)
        if eng is None:
            eng = self._factory()
            self._engines[key] = eng
            logger.info(f"[engine-registry] 新建引擎实例 acct={key}")
        return eng

    def peek(self, account: Optional[str]) -> Optional["object"]:
        """只看不建（用于状态查询、通知链路，避免查询把实例「建」出来）。"""
        return self._engines.get(self._key(account))

    def get_or_none(self, account: Optional[str]) -> Optional["object"]:
        """取已存在的实例；不存在返回 None（不创建）。"""
        return self._engines.get(self._key(account))

    # ── 枚举 ────────────────────────────────────────────────────────────
    def keys(self) -> list[str]:
        return list(self._engines.keys())

    def all_running_keys(self) -> list[str]:
        out = []
        for k, e in self._engines.items():
            try:
                if getattr(e, "is_running", False) if isinstance(
                        getattr(e, "is_running", None), bool) else e.is_running():
                    out.append(k)
            except Exception:
                continue
        return out

    def items(self) -> list[tuple[str, "object"]]:
        return list(self._engines.items())

    def busy_keys(self) -> list[str]:
        """有任务在跑（非 IDLE）的账号键 —— 供「未指定 acct」时判定是否歧义。"""
        out = []
        for k, e in self._engines.items():
            try:
                st = getattr(e, "state", None)
                v = getattr(st, "value", None) or (str(st) if st is not None else "")
                if str(v).lower() not in ("idle", "", "none"):
                    out.append(k)
            except Exception:
                continue
        return out

    # ── 生命周期 ────────────────────────────────────────────────────────
    async def shutdown_all(self) -> dict:
        """逐个 shutdown；返回 {acct: ok/err}，异常不吞（ADR-002 §3.1）。"""
        result: dict[str, str] = {}
        for k, e in list(self._engines.items()):
            try:
                await e.shutdown()
                result[k] = "ok"
            except Exception as ex:  # noqa: BLE001
                result[k] = f"err: {ex}"
                logger.warning(f"[engine-registry] shutdown 失败 acct={k}: {ex}")
        return result

    def drop(self, account: Optional[str]) -> None:
        """移除某账号实例（仅供测试/显式清理；正常路径不调用）。"""
        self._engines.pop(self._key(account), None)

    # ── 内部 ────────────────────────────────────────────────────────────
    @staticmethod
    def _key(account: Optional[str]) -> str:
        a = str(account or "").strip()
        return a if a else ANONYMOUS_KEY
