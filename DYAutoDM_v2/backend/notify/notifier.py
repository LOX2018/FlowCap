"""通知中枢：事件 → 多渠道推送。

职责
----
1. 统一入口 `emit(event_type, title, body, ...)`，业务代码一行调用。
2. **节流去重**：同一 event_type + dedup_key 在 `throttle_sec` 内只推一次
   （凭证失效这类事件会在重试中反复触发，刷屏等于没有告警）。
3. **按级别路由**：只有级别 >= 渠道配置的 min_level 才推。
4. **失败不影响主流程**：推送永远在后台任务里做，异常全吞。

事件级别：info < warn < critical（critical 无视 min_level 配置，必推）。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from loguru import logger

from . import channels
from .channels import BaseChannel, ChannelResult

LEVELS = {"info": 10, "warn": 20, "critical": 30}


class Notifier:
    def __init__(self) -> None:
        self.channels: dict[str, BaseChannel] = {}
        self.cfg: dict[str, Any] = {}
        self.enabled: bool = False
        self._recent: dict[str, float] = {}   # dedup_key -> last_ts（emit 内已带 500 条上限清理）
        self._queue: asyncio.Queue | None = None
        self._worker: asyncio.Task | None = None

    # ---------------- 生命周期 ----------------
    def configure(self, cfg: dict[str, Any]) -> None:
        """热更新配置（前端保存后立即生效，无需重启）。"""
        self.cfg = cfg or {}
        self.enabled = bool(self.cfg.get("enabled", False))
        old = self.channels
        new: dict[str, BaseChannel] = {}
        for item in self.cfg.get("channels", []) or []:
            kind = str(item.get("kind", "")).strip()
            cid = str(item.get("id") or kind).strip()
            if not kind:
                continue
            try:
                ch = channels.build_channel(kind, item)
                new[cid] = ch
            except Exception as e:  # noqa: BLE001
                logger.error("NTY-010", f"[notify] 渠道 {cid}({kind}) 初始化失败: {e}")
        self.channels = new
        # 关闭被移除的旧渠道
        for cid, ch in old.items():
            if cid not in new:
                asyncio.create_task(_safe_close(ch))
        logger.info(
            f"[notify] 配置更新: enabled={self.enabled} 渠道={list(new)}"
        )

    def start_worker(self) -> None:
        """启动派发 worker。

        注意：worker 只创建一次并常驻。此前版本在 stop() 后重建 queue，
        会导致取消期间入队的事件随旧 queue 一起丢失（实测：节流测试中
        第二条不同 key 的事件未派发）。queue 改为**只初始化一次**并复用。
        """
        if self._queue is None:
            self._queue = asyncio.Queue()
        if self._worker and not self._worker.done():
            return
        self._worker = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._worker:
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass
        for ch in self.channels.values():
            await _safe_close(ch)

    async def _run(self) -> None:
        assert self._queue is not None
        while True:
            item = await self._queue.get()
            try:
                await self._dispatch(**item)
            except asyncio.CancelledError:
                # 被取消时把事件放回队列，避免停机瞬间事件静默丢失
                try:
                    self._queue.put_nowait(item)
                except Exception:  # noqa: BLE001
                    pass
                raise
            except Exception as e:  # noqa: BLE001
                logger.exception("NTY-011", f"[notify] 派发异常: {e}")
            finally:
                self._queue.task_done()

    # ---------------- 对外入口 ----------------
    def emit(
        self,
        event_type: str,
        title: str,
        body: str,
        *,
        level: str = "info",
        dedup_key: str = "",
        throttle_sec: int = 0,
        targets: dict[str, str] | None = None,
    ) -> None:
        """业务侧调用入口（同步，不阻塞）。

        targets: {channel_id: target} —— 指定各渠道的接收目标；
                 为空则用渠道配置里的 default_target。
        """
        if not self.enabled or not self.channels:
            return
        key = dedup_key or f"{event_type}:{title}"
        now = time.time()
        last = self._recent.get(key, 0.0)
        if throttle_sec > 0 and now - last < throttle_sec:
            logger.debug(f"[notify] 节流跳过 {key}（{throttle_sec}s 内已发）")
            return
        self._recent[key] = now
        # 清理过期 key，避免无限增长
        if len(self._recent) > 500:
            cutoff = now - 3600
            self._recent = {k: v for k, v in self._recent.items() if v > cutoff}

        if self._queue is None:
            self.start_worker()
        assert self._queue is not None
        self._queue.put_nowait(
            {
                "event_type": event_type,
                "title": title,
                "body": body,
                "level": level,
                "targets": targets or {},
            }
        )

    # ---------------- 内部派发 ----------------
    async def _dispatch(
        self,
        event_type: str,
        title: str,
        body: str,
        level: str,
        targets: dict[str, str],
    ) -> None:
        lvl = LEVELS.get(level, 10)
        text = f"【DYAutoDM·{title}】\n{body}"
        for cid, ch in list(self.channels.items()):
            if not ch.enabled:
                continue
            min_lvl = LEVELS.get(
                str(self.cfg.get("channels_min_level", {}).get(cid, "info")), 10
            )
            if lvl < min_lvl and lvl < LEVELS["critical"]:
                continue
            target = targets.get(cid) or str(
                self._cfg_of(cid).get("default_target", "")
            )
            if not target:
                logger.debug(f"[notify] 渠道 {cid} 无目标，跳过")
                continue
            r: ChannelResult = await ch.send(target, text)
            if not r.ok:
                logger.warning("NTY-012", f"[notify] {cid} 推送失败: {r.error}")

    def _cfg_of(self, cid: str) -> dict[str, Any]:
        for item in self.cfg.get("channels", []) or []:
            if str(item.get("id") or item.get("kind")) == cid:
                return item
        return {}

    # ---------------- 状态/自检 ----------------
    def status(self) -> dict[str, Any]:
        out = []
        for item in self.cfg.get("channels", []) or []:
            cid = str(item.get("id") or item.get("kind"))
            kind = str(item.get("kind", ""))
            ch = self.channels.get(cid)
            cls = channels._REGISTRY.get(kind)
            missing = cls.missing_keys(item) if cls else ["未知渠道"]
            out.append(
                {
                    "id": cid,
                    "kind": kind,
                    "enabled": bool(item.get("enabled", False)),
                    "loaded": ch is not None,
                    "missing": missing,
                    "ready": bool(item.get("enabled")) and ch is not None and not missing,
                }
            )
        return {"enabled": self.enabled, "channels": out}


async def _safe_close(ch: BaseChannel) -> None:
    try:
        await ch.close()
    except Exception:  # noqa: BLE001
        pass


# 全局单例
notifier = Notifier()
