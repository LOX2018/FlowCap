# -*- coding: utf-8 -*-
"""进程内 TTL 缓存原语（单一实现，2026-09-16 v0.43.40）。

设计契约
--------
**同一类"带过期时间的进程内键值缓存"全项目只允许一处实现。**

为什么独立成模块（根因回溯）
----------------------------
改前各模块各自维护 dict + TTL，语义分散：
  - services/conv_identity.my_uid      60s
  - services/uid_probe.session_uid     300s（同一件事，TTL 不同）
  - services/uid_probe.get_uid         300s/60s 双 TTL
  - services/media_proxy               自带一套
  - daemon/browser_daemon              自带若干
后果：缓存语义无法推理、TTL 不一致导致「同一事实两个新鲜度」、
清理时机分散（改一处忘记另一处 = 读到陈旧数据）。

本模块只提供**最小原语**，不引入任何框架依赖：
  - `TTLCache[str, V]`：线程安全的 get/set/get_or_set/invalidate/clear
  - 支持"负缓存"（值可为 None，用于失败退避语义）
  - 提供 `snapshot()` 供 /status 观测

注意：**跨进程不共享**。recv_daemon 与 backend 是独立进程，各自持有
自己的缓存实例 —— 这是刻意设计（避免引入 IPC 复杂度），代价是
两进程的新鲜度各自独立；对 uid 推断这类**零网络**的本地计算，
该代价可忽略。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, Hashable, Optional


class TTLCache:
    """线程安全的进程内 TTL 缓存。

    用法::

        c = TTLCache(default_ttl=60.0)
        c.set("k", "v")                      # 用默认 TTL
        c.set("k", "v", ttl=300)             # 指定 TTL
        v = c.get("k")                       # 过期/不存在 -> None
        v = c.get_or_set("k", lambda: 计算(), ttl=60)
        c.invalidate("k")                    # 删除单键
        c.clear()                            # 全清

    负缓存：值可以是 None（如探活失败）。`get` 无法区分「未缓存」与
    「缓存了 None」——需要区分时用 `peek()`，返回 (found, value)。
    """

    __slots__ = ("_d", "_lock", "_default_ttl", "_maxsize")

    def __init__(self, default_ttl: float = 60.0, maxsize: int = 0) -> None:
        """`maxsize=0`（默认）表示不限；>0 时在 set 后按 LRU 淘汰最久未用项。

        2026-09-17 修补（审查 P2-5）：`prune()` 虽已实现（「防长期运行内存
        增长」），但生产路径**从未调用**（仅测试脚本用），过期项只增不减。
        本类作为通用原语对外提供，若被用 uid/会话 ID 等高频 key 实例化即
        内存泄漏。现加 `maxsize` 主动淘汰兜底，`prune()` 仍可手动调用。
        """
        self._d: dict[Hashable, tuple[float, float, Any]] = {}
        self._lock = threading.Lock()
        self._default_ttl = float(default_ttl)
        self._maxsize = int(maxsize or 0)

    def _enforce_maxsize(self) -> None:
        """超出容量时按写入时间（元组首元素）淘汰最旧的项。须持锁调用。"""
        if self._maxsize <= 0 or len(self._d) <= self._maxsize:
            return
        for k in sorted(self._d, key=lambda k: self._d[k][0])[
                : len(self._d) - self._maxsize]:
            self._d.pop(k, None)

    # ---------------------------------------------------------------- 基本读写
    def peek(self, key: Hashable) -> tuple[bool, Any]:
        """返回 (是否命中且未过期, 值)。用于需要区分「未缓存」与「缓存 None」的场景。"""
        with self._lock:
            hit = self._d.get(key)
        if not hit:
            return False, None
        ts, ttl, val = hit
        if ttl > 0 and (time.time() - ts) >= ttl:
            return False, None
        return True, val

    def get(self, key: Hashable, default: Any = None) -> Any:
        found, val = self.peek(key)
        return val if found else default

    def set(self, key: Hashable, value: Any, ttl: Optional[float] = None) -> None:
        t = self._default_ttl if ttl is None else float(ttl)
        with self._lock:
            self._d[key] = (time.time(), t, value)
            # 2026-09-17 修补（审查 P2-5）：写入后主动执行容量上限淘汰，
            # 不依赖外部调用 prune()。
            self._enforce_maxsize()

    def get_or_set(self, key: Hashable, factory: Callable[[], Any],
                   ttl: Optional[float] = None) -> Any:
        """命中即返回；未命中则调 factory() 并缓存（含 None 负缓存）。

        ⚠️ factory 在**锁外**执行 —— 避免慢计算（如打网）阻塞其他键。
        代价：同一键并发未命中时可能重复计算。对幂等的本地计算无所谓；
        对必须去重的场景请调用方自行加锁（如 uid_probe 的 per-account 锁）。
        """
        found, val = self.peek(key)
        if found:
            return val
        val = factory()
        self.set(key, val, ttl)
        return val

    # ---------------------------------------------------------------- 失效
    def invalidate(self, key: Hashable) -> None:
        with self._lock:
            self._d.pop(key, None)

    def invalidate_prefix(self, prefix: str) -> int:
        """删除所有以 prefix 开头的键（键须为 str）。返回删除数。"""
        with self._lock:
            dead = [k for k in self._d
                    if isinstance(k, str) and k.startswith(prefix)]
            for k in dead:
                self._d.pop(k, None)
            return len(dead)

    def clear(self) -> None:
        with self._lock:
            self._d.clear()

    # ---------------------------------------------------------------- 观测
    def prune(self) -> int:
        """清除已过期项，返回清除数（防长期运行内存增长）。"""
        now = time.time()
        with self._lock:
            dead = [k for k, (ts, ttl, _v) in self._d.items()
                    if ttl > 0 and (now - ts) >= ttl]
            for k in dead:
                self._d.pop(k, None)
            return len(dead)

    def snapshot(self) -> dict:
        """观测快照（供 /status 用）：总量 + 未过期数。"""
        now = time.time()
        with self._lock:
            items = list(self._d.values())
        live = sum(1 for ts, ttl, _v in items
                   if ttl <= 0 or (now - ts) < ttl)
        return {"total": len(items), "live": live,
                "default_ttl": self._default_ttl}
