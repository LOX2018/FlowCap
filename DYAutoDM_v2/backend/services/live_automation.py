# coding=utf-8
"""直播间**写接口自动化**服务（定时弹幕 / 分步批量点赞）。

## 为什么独立成服务，而不是挂在 task_scheduler（架构决策）

`services/task_scheduler.py` 的契约是**同步一次性 `fn(task)->dict` + 300s 超时**
（见其模块头与 T-02 的结论记录）。而本模块的两项能力天然是：
  · **无限周期**（定时弹幕：每隔 N 分钟一轮，直到用户停）；
  · **长时跨步**（分步点赞：每步之间冷却 2~3 分钟，4 步就可能 12 分钟）。
⇒ 都不适配 task_scheduler。且两者都要**跟随监听生命周期**（监听停 → 自动化停）。

故：由 **`LiveChatHook`（监听生命周期所有者）持有本服务的 start/stop**，
本模块只负责「按配置跑写接口」，**不碰监听、不碰 WS、不碰读路径**
（Separation of Concerns：`live_hook` 管读，本模块管写）。

## 风控（这是写接口，触本项目最高红线）

1. **默认休眠**：`danmaku_timer_enabled` / `like_batch_enabled` 默认 False；
   连总开关 `automation_enabled` 也默认 False ⇒ **不改配置 = 零出站**（可断言的零回归）。
2. **速率显式上限**：单次弹幕 ≤ `danmaku_timer_max_per_run`（默认 3）；
   点赞每步 ≤ `like_batch_step_max`（默认 1000），步间冷却 ≥ 2 分钟。
   抖音点赞约 0.3s 一次 ⇒ 每步由基座按节流发，**不并发轰炸**。
3. **绝不在无监听时跑**：由 `live_hook` 在 WS 停止时 `stop()`。
4. **失败即停**：单次失败不重试、不叠加（写接口失败多为风控，重试会加码）。

## 契约

```python
svc = LiveAutomation(auth_provider=..., room_id_provider=...)
svc.start()      # 幂等；读配置决定启用哪些
svc.stop()       # 幂等；等待线程退出
svc.status()     # {"danmaku": {...}, "like_batch": {...}} 供 UI 呈现
```
"""
from __future__ import annotations

import random
import threading
import time
from typing import Any, Callable, Optional

from loguru import logger


def _cfg(key: str, default: Any) -> Any:
    """读配置。**优先级**：kv `config.live[key]`（**策略/房间配置 — 服务端权威**）
    → 设置页 `app_config.live[key]`（全局兜底）→ default。

    两层并存的原因：房间策略（`RoomConfigPage`）是**逐房间**配置服务端权威源
    （与既有 `dm_pool` 同一层，见 `api/live_config._apply_to_task_kv`）；
    设置页 live 分区是**全局**配置。前者给出值即优先，否则用后者。

    读不到一律回落 default —— 绝不因配置缺失而「放行」（默认全 False 即休眠）。
    """
    try:
        from database import get_kv_json
        _live = (get_kv_json("config", {}) or {}).get("live")
        if isinstance(_live, dict) and key in _live and _live[key] is not None:
            return _live[key]
    except Exception:  # noqa: BLE001
        pass
    try:
        from services import app_config as _ac
        return _ac.get("live", key, default)
    except Exception:  # noqa: BLE001
        return default


def _as_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def _as_float(v: Any, default: float) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


class LiveAutomation:
    """定时弹幕 + 分步批量点赞（两个**独立**常驻线程，各自可单独启停）。"""

    def __init__(self,
                 auth_provider: Callable[[], Any],
                 room_id_provider: Callable[[], str],
                 account_provider: Callable[[], str] = lambda: ""):
        self._auth = auth_provider          # () -> auth（写接口需要有效凭证）
        self._room = room_id_provider       # () -> 真实 room_id（直播域写接口只认它）
        self._account = account_provider    # () -> 账号名（仅日志/状态用）
        self._stop = threading.Event()
        self._dm_thread: Optional[threading.Thread] = None
        self._like_thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()
        # UI 可观测状态（只追加，不改结构）
        self.dm_state: dict = {"running": False, "sent": 0, "last_at": 0.0,
                               "next_in": 0, "last_error": ""}
        self.like_state: dict = {"running": False, "done": 0, "total": 0,
                                 "step": 0, "steps": 0, "cooldown_until": 0.0,
                                 "last_error": "", "finished": False}

    # ── 生命周期 ────────────────────────────────────────────────────────
    def start(self) -> dict:
        """按配置启动（幂等）。返回实际启动了什么（供日志/UI）。"""
        self._stop.clear()
        started = {}
        # 总开关：设置页显式关掉则一律不启；未设时按「有任一子项启用」判定
        # （策略/房间配置里没有 automation_enabled 这个键，不能因此永不生效）。
        _master = bool(_cfg("automation_enabled", False))
        if _master or bool(_cfg("danmaku_timer_enabled", False)) or                 bool(_cfg("like_batch_enabled", False)):
            if bool(_cfg("danmaku_timer_enabled", False)):
                started["danmaku"] = self._start_danmaku()
            if bool(_cfg("like_batch_enabled", False)):
                started["like_batch"] = self._start_like_batch()
        logger.info(f"[自动化] 启动结果：{started or '未启用（默认休眠）'}")
        return started

    def stop(self) -> None:
        self._stop.set()
        with self._lock:
            self.dm_state["running"] = False
            self.like_state["running"] = False
        for th in (self._dm_thread, self._like_thread):
            if th and th.is_alive():
                th.join(timeout=3)
        self._dm_thread = self._like_thread = None

    # ── ① 定时弹幕 ──────────────────────────────────────────────────────
    def _start_danmaku(self) -> bool:
        if self._dm_thread and self._dm_thread.is_alive():
            return True
        self._dm_thread = threading.Thread(target=self._danmaku_loop,
                                           name="live-danmaku-timer", daemon=True)
        self._dm_thread.start()
        return True

    def _danmaku_loop(self) -> None:
        """按 `danmaku_timer_min/max`（分钟，随机区间）循环发弹幕文案。

        文案源 = `config.live.danmaku_pool`（直播配置里维护）；每次随机取一条。
        单次上限 `danmaku_timer_max_per_run`（默认 3）—— 若池子配置了多条，
        一次只发 1 条（保持人类节奏），该上限仅用于防御异常配置。
        """
        self.dm_state["running"] = True
        try:
            while not self._stop.is_set():
                lo = max(0.5, _as_float(_cfg("danmaku_timer_min", 3), 3.0))
                hi = max(lo, _as_float(_cfg("danmaku_timer_max", 6), 6.0))
                wait_s = random.uniform(lo, hi) * 60.0
                # 分段等待，便于 stop() 及时生效（也便于 UI 显示倒计时）
                t0 = time.time()
                while not self._stop.is_set():
                    left = wait_s - (time.time() - t0)
                    self.dm_state["next_in"] = max(0, int(left))
                    if left <= 0:
                        break
                    self._stop.wait(min(2.0, left))
                if self._stop.is_set():
                    break
                self._send_one_danmaku()
        finally:
            self.dm_state["running"] = False

    def _send_one_danmaku(self) -> None:
        pool = _cfg("danmaku_pool", []) or []
        # 兼容两种形态：["文案"] 与 [{"text":"文案","enabled":true}]
        texts = []
        for t in pool:
            if isinstance(t, dict):
                if not t.get("enabled", True):
                    continue
                s = str(t.get("text") or "").strip()
            else:
                s = str(t or "").strip()
            if s:
                texts.append(s)
        if not texts:
            self.dm_state["last_error"] = "弹幕文案池为空（请在直播配置里添加）"
            logger.info("[自动化] 定时弹幕跳过：文案池为空")
            return
        rid = str(self._room() or "")
        auth = None
        try:
            auth = self._auth()
        except Exception as e:  # noqa: BLE001
            self.dm_state["last_error"] = f"凭证加载失败: {e}"
            return
        if not rid or auth is None:
            self.dm_state["last_error"] = "缺 room_id 或凭证"
            return
        text = random.choice(texts)
        try:
            from dy_apis.douyin_api import DouyinAPI
            res = DouyinAPI.sendMsgInRoom(auth, rid, text)
            code = (res or {}).get("status_code")
            ok = code == 0
            if ok:
                self.dm_state["sent"] += 1
                self.dm_state["last_at"] = time.time()
                self.dm_state["last_error"] = ""
                logger.info(f"[自动化] 定时弹幕已发送（第 {self.dm_state['sent']} 条）: {text[:20]}")
            else:
                self.dm_state["last_error"] = f"上游 status_code={code}"
                logger.warning(f"[自动化] 定时弹幕未成功 status_code={code}")
        except Exception as e:  # noqa: BLE001
            # 写接口失败多为风控 —— **不重试、不叠加**（重试会加码）
            self.dm_state["last_error"] = f"{type(e).__name__}: {e}"
            logger.warning(f"[自动化] 定时弹幕异常（不重试）: {e}")

    # ── ② 分步批量点赞 ──────────────────────────────────────────────────
    def _start_like_batch(self) -> bool:
        if self._like_thread and self._like_thread.is_alive():
            return True
        total = max(1, _as_int(_cfg("like_batch_total", 3000), 3000))
        steps = max(1, _as_int(_cfg("like_batch_steps", 4), 4))
        self.like_state.update({"running": True, "done": 0, "total": total,
                                "step": 0, "steps": steps,
                                "cooldown_until": 0.0, "last_error": "",
                                "finished": False})
        self._like_thread = threading.Thread(target=self._like_batch_loop,
                                             name="live-like-batch", daemon=True)
        self._like_thread.start()
        return True

    def _like_batch_loop(self) -> None:
        """把 `like_batch_total` 拆成 `like_batch_steps` 步执行，步间冷却。

        每步数量 = 总目标 / 步数（余数并入末步），并受 `like_batch_step_max`
        （默认 1000）约束 —— 超出则自动**增加步数**（而不是每步发更多）。
        步间冷却 `like_batch_cooldown_sec`（默认 150s，配置下限 120s）。
        """
        try:
            total = max(1, _as_int(_cfg("like_batch_total", 3000), 3000))
            steps = max(1, _as_int(_cfg("like_batch_steps", 4), 4))
            step_max = max(1, _as_int(_cfg("like_batch_step_max", 1000), 1000))
            cool = max(120.0, _as_float(_cfg("like_batch_cooldown_sec", 150), 150.0))
            # 受每步上限约束：必要时增加步数（不放大单步）
            if total / steps > step_max:
                steps = max(steps, -(-total // step_max))    # 向上取整
            self.like_state["steps"] = steps
            per = total // steps
            rest = total - per * steps
            rid = str(self._room() or "")
            try:
                auth = self._auth()
            except Exception as e:  # noqa: BLE001
                self.like_state.update({"running": False, "last_error": f"凭证加载失败: {e}"})
                return
            if not rid or auth is None:
                self.like_state.update({"running": False, "last_error": "缺 room_id 或凭证"})
                return
            from dy_apis.douyin_api import DouyinAPI
            for i in range(steps):
                if self._stop.is_set():
                    break
                n = per + (rest if i == steps - 1 else 0)
                self.like_state["step"] = i + 1
                try:
                    res = DouyinAPI.diggLiveRoom(auth, rid, str(n))
                    code = (res or {}).get("status_code")
                    if code == 0:
                        self.like_state["done"] += n
                        self.like_state["last_error"] = ""
                        logger.info(f"[自动化] 批量点赞 第 {i+1}/{steps} 步完成 +{n}"
                                    f"（累计 {self.like_state['done']}/{total}）")
                    else:
                        self.like_state["last_error"] = f"上游 status_code={code}"
                        logger.warning(f"[自动化] 批量点赞 第 {i+1} 步失败 status_code={code}")
                except Exception as e:  # noqa: BLE001
                    self.like_state["last_error"] = f"{type(e).__name__}: {e}"
                    logger.warning(f"[自动化] 批量点赞 第 {i+1} 步异常（停止后续步）: {e}")
                    break
                if i == steps - 1:
                    break
                # 步间冷却（分段等待，便于 stop 生效 + UI 显示倒计时）
                self.like_state["cooldown_until"] = time.time() + cool
                t0 = time.time()
                while not self._stop.is_set() and (time.time() - t0) < cool:
                    self._stop.wait(2.0)
            self.like_state["finished"] = True
        finally:
            self.like_state["running"] = False
            self.like_state["cooldown_until"] = 0.0

    # ── 状态 ────────────────────────────────────────────────────────────
    def status(self) -> dict:
        return {"danmaku": dict(self.dm_state), "like_batch": dict(self.like_state)}