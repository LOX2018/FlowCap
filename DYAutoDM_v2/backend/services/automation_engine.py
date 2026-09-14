# -*- coding: utf-8 -*-
"""自动化引擎 —— 监控（monitor）× 过滤（keyword/metrics）× 动作（action）

## 设计来源（照源项目 better-douyin）

**权威契约**：源项目壳 `frontend/src/lib/ai-automation.ts`（完整可读，
非仅字符串逆向）。本模块逐条对照其字段、默认值、clamp 范围与算法：

```typescript
// 源项目 DEFAULT_AI_AUTOMATION（25 个字段，含默认值）
auto_monitor_notices / friends / comments / feed : false   ← 4 个监控源
auto_follow_back_on_new_follower                  : false
auto_match_keywords / auto_exclude_keywords       : ""      ← 通用过滤词
auto_{private,comment,like,collect}_{match,exclude}_keywords  ← 按动作分设
auto_min_digg_count / comment_count / play_count  : 0       ← 门槛
auto_scan_interval_seconds                        : 30
auto_max_actions_per_run                          : 5
auto_return_shared_media                          : false
auto_return_shared_allow_images / allow_videos    : true
auto_return_shared_max_size_mb                    : 20
auto_return_shared_max_media_count                : 9

// clamp（源项目 normalizeAiAutomationConfig）
clampAutomationInterval : 10..300
clampAutomationBatch    : 1..50
getAiAutoSendDelayMs    : 0..10000
return_shared_max_size_mb   : 1..200
return_shared_max_media_count: 1..20
```

**算法（源项目原文，逐条照搬）**：
```
tokens(s)      = s.split(/[,，\\n\\s]+/).map(trim.toLowerCase).filter(Boolean)
targetKeywords = 动作专属键 || 通用键（fallback）
matchesAutomationText(text, cfg, target):
    if 任一 exclude token 命中 → false
    if 无 include token        → true
    return 任一 include token 命中
meetsVideoAutomationMetrics(video, cfg):
    digg_count < auto_min_digg_count → false
    comment_count < ... → false
    play_count < ...    → false
videoAutomationText(video) = desc + author.nickname + author.signature
                             + music.title + music.author + aweme_id
runVideoAutomation(video, cfg):
    门槛不过 → 直接返回
    auto_like    && !is_liked    && matches(...,"like")    → 点赞
    auto_collect && !is_collected && matches(...,"collect") → 收藏
```

## 与源项目的差异（诚实记录）

· 源项目 `runVideoAutomation` 只做 **like / collect**（视频维度）。
  本项目额外提供 `comment` / `private` / `follow` 的动作位（本项目业务需要），
  但**过滤算法与门槛完全照源项目**。
· 源项目 `auto_monitor_comments` 是第 4 个监控源（通知/好友/评论/推荐流），
  本模块 `MonitorSource` 照抄 4 项。
· 去重：源项目用 `AI_AUTOMATION_DEDUPE_LIMIT = 1000` 的 `Set` 去重
  （`rememberAutomationKey`），本模块 `_seen` 照此实现。
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from loguru import logger

# ─────────────────────────────────────────────────────────────
# 默认值（照源项目 DEFAULT_AI_AUTOMATION，逐字段对齐）
# ─────────────────────────────────────────────────────────────

DEFAULT_AI_AUTOMATION: dict[str, Any] = {
    # 监控源
    "auto_monitor_notices": False,
    "auto_monitor_friends": False,
    "auto_monitor_comments": False,
    "auto_monitor_feed": False,
    "auto_follow_back_on_new_follower": False,
    # 通用过滤词
    "auto_match_keywords": "",
    "auto_exclude_keywords": "",
    # 按动作分设的过滤词
    "auto_private_match_keywords": "",
    "auto_private_exclude_keywords": "",
    "auto_comment_match_keywords": "",
    "auto_comment_exclude_keywords": "",
    "auto_like_match_keywords": "",
    "auto_like_exclude_keywords": "",
    "auto_collect_match_keywords": "",
    "auto_collect_exclude_keywords": "",
    # 门槛
    "auto_min_digg_count": 0,
    "auto_min_comment_count": 0,
    "auto_min_play_count": 0,
    # 节流
    "auto_scan_interval_seconds": 30,
    "auto_max_actions_per_run": 5,
    # 回流共享媒体
    "auto_return_shared_media": False,
    "auto_return_shared_allow_images": True,
    "auto_return_shared_allow_videos": True,
    "auto_return_shared_max_size_mb": 20,
    "auto_return_shared_max_media_count": 9,
}

#: 源项目 `AI_AUTOMATION_DEDUPE_LIMIT`
AI_AUTOMATION_DEDUPE_LIMIT = 1000

#: 动作类型（源项目 `AutomationFilterTarget`：private / comment / like / collect）
#: 本项目额外含 follow（源项目仅有回关布尔位）。
ACTION_TARGETS = ("private", "comment", "like", "collect", "follow")


# ─────────────────────────────────────────────────────────────
# 配置归一化（照源项目 normalizeAiAutomationConfig 的 clamp）
# ─────────────────────────────────────────────────────────────

def _to_int(v: Any, default: int = 0) -> int:
    try:
        n = int(float(v))
        return n
    except (TypeError, ValueError):
        return default


def clamp_interval(v: Any) -> int:
    """照源项目 `clampAutomationInterval`：10..300 秒。"""
    return max(10, min(300, _to_int(v, DEFAULT_AI_AUTOMATION["auto_scan_interval_seconds"])))


def clamp_batch(v: Any) -> int:
    """照源项目 `clampAutomationBatch`：1..50。"""
    return max(1, min(50, _to_int(v, DEFAULT_AI_AUTOMATION["auto_max_actions_per_run"])))


def clamp_send_delay(v: Any) -> int:
    """照源项目 `getAiAutoSendDelayMs`：0..10000 毫秒。"""
    n = _to_int(v, 0)
    return max(0, min(10000, n))


def clamp_max_size_mb(v: Any) -> int:
    """照源项目：1..200 MB。"""
    return max(1, min(200, _to_int(v, 20)))


def clamp_max_media_count(v: Any) -> int:
    """照源项目：1..20。"""
    return max(1, min(20, _to_int(v, 9)))


def normalize_config(cfg: Optional[dict[str, Any]]) -> dict[str, Any]:
    """照源项目 `normalizeAiAutomationConfig`：默认值兜底 + 各 clamp。"""
    c = dict(DEFAULT_AI_AUTOMATION)
    if cfg:
        c.update({k: v for k, v in cfg.items() if v is not None})
    c["auto_scan_interval_seconds"] = clamp_interval(c.get("auto_scan_interval_seconds"))
    c["auto_max_actions_per_run"] = clamp_batch(c.get("auto_max_actions_per_run"))
    c["auto_min_digg_count"] = max(0, _to_int(c.get("auto_min_digg_count"), 0))
    c["auto_min_comment_count"] = max(0, _to_int(c.get("auto_min_comment_count"), 0))
    c["auto_min_play_count"] = max(0, _to_int(c.get("auto_min_play_count"), 0))
    c["auto_return_shared_max_size_mb"] = clamp_max_size_mb(c.get("auto_return_shared_max_size_mb"))
    c["auto_return_shared_max_media_count"] = clamp_max_media_count(
        c.get("auto_return_shared_max_media_count"))
    return c


# ─────────────────────────────────────────────────────────────
# 文本过滤（照源项目 tokens / targetKeywords / matchesAutomationText）
# ─────────────────────────────────────────────────────────────

_TOKEN_SPLIT = re.compile(r"[,，\n\s]+")


def tokens(value: Any) -> list[str]:
    """照源项目 `tokens`：按 `[,，\\n\\s]+` 切分、trim、小写、去空。"""
    return [t.strip().lower()
            for t in _TOKEN_SPLIT.split(str(value or ""))
            if t.strip()]


def target_keywords(cfg: dict[str, Any], target: str) -> tuple[str, str]:
    """照源项目 `targetKeywords`：动作专属键，回落通用键。

    返回 (match, exclude)。
    """
    mk = f"auto_{target}_match_keywords"
    ek = f"auto_{target}_exclude_keywords"
    match = cfg.get(mk) or cfg.get("auto_match_keywords") or ""
    exclude = cfg.get(ek) or cfg.get("auto_exclude_keywords") or ""
    return str(match), str(exclude)


def matches_text(text: str, cfg: dict[str, Any], target: str = "comment") -> bool:
    """照源项目 `matchesAutomationText`（**逐行等价**）。"""
    normalized = (text or "").lower()
    match_s, exclude_s = target_keywords(cfg, target)
    includes = tokens(match_s)
    excludes = tokens(exclude_s)
    if any(t in normalized for t in excludes):
        return False
    if not includes:
        return True
    return any(t in normalized for t in includes)


def should_automate_text(text: str, cfg: dict[str, Any], target: str = "comment") -> bool:
    """照源项目 `shouldAutomateText`：非空 且 通过过滤。"""
    return bool((text or "").strip()) and matches_text(text, cfg, target)


# ─────────────────────────────────────────────────────────────
# 候选（视频维度，照源项目 VideoInfo 的相关字段）
# ─────────────────────────────────────────────────────────────

class MonitorSource(str, Enum):
    """监控源（照源项目 4 个 `auto_monitor_*`）。"""
    NOTICES = "notices"      # auto_monitor_notices
    FRIENDS = "friends"      # auto_monitor_friends
    COMMENTS = "comments"    # auto_monitor_comments
    FEED = "feed"            # auto_monitor_feed


@dataclass
class VideoCandidate:
    """视频候选（照源项目 `VideoInfo` 的自动化相关字段）。"""
    aweme_id: str = ""
    desc: str = ""
    author_nickname: str = ""
    author_signature: str = ""
    music_title: str = ""
    music_author: str = ""
    digg_count: int = 0
    comment_count: int = 0
    play_count: int = 0
    is_liked: bool = False
    is_collected: bool = False
    source: Optional[MonitorSource] = None
    raw: dict[str, Any] = field(default_factory=dict)

    def automation_text(self) -> str:
        """照源项目 `videoAutomationText`：desc + 作者 + 签名 + 音乐 + aweme_id。"""
        return " ".join(x for x in (
            self.desc, self.author_nickname, self.author_signature,
            self.music_title, self.music_author, self.aweme_id,
        ) if x)


@dataclass
class ActionResult:
    """动作结果（照源项目 `runVideoAutomation` 返回的 actions 数组语义）。"""
    aweme_id: str
    action: str
    ok: bool
    reason: str = ""


def meets_metrics(v: VideoCandidate, cfg: dict[str, Any]) -> bool:
    """照源项目 `meetsVideoAutomationMetrics`：三项下限逐条比较。"""
    if (v.digg_count or 0) < cfg.get("auto_min_digg_count", 0):
        return False
    if (v.comment_count or 0) < cfg.get("auto_min_comment_count", 0):
        return False
    if (v.play_count or 0) < cfg.get("auto_min_play_count", 0):
        return False
    return True


def should_automate_video(v: VideoCandidate, cfg: dict[str, Any],
                          target: str = "like") -> bool:
    """照源项目 `shouldAutomateVideo`：门槛 且 文本过滤。"""
    return meets_metrics(v, cfg) and matches_text(v.automation_text(), cfg, target)


# ─────────────────────────────────────────────────────────────
# 去重（照源项目 rememberAutomationKey / AI_AUTOMATION_DEDUPE_LIMIT）
# ─────────────────────────────────────────────────────────────

class DedupeSet:
    """照源项目 `rememberAutomationKey`：Set + 上限 1000，超出淘汰最早。"""

    def __init__(self, limit: int = AI_AUTOMATION_DEDUPE_LIMIT) -> None:
        self.limit = limit
        self._seen: dict[str, None] = {}      # dict 保插入序（淘汰最早）

    def remember(self, key: str) -> bool:
        k = str(key or "").strip()
        if not k or k in self._seen:
            return False
        self._seen[k] = None
        while len(self._seen) > self.limit:
            self._seen.pop(next(iter(self._seen)))
        return True

    def __len__(self) -> int:
        return len(self._seen)


# ─────────────────────────────────────────────────────────────
# 引擎（编排：监控 → 门槛 → 去重 → 动作 → 节流）
# ─────────────────────────────────────────────────────────────

#: 监控源回调：(source, limit) -> list[VideoCandidate]
MonitorFn = Callable[[MonitorSource, int], list[VideoCandidate]]
#: 动作回调：(candidate, action) -> ActionResult
ActionFn = Callable[[VideoCandidate, str], ActionResult]


class AutomationEngine:
    """自动化引擎。

    ## 照源项目的两处关键语义

    1. **门槛先于动作**：`meetsVideoAutomationMetrics` 不过则**直接返回**
       （源项目 `runVideoAutomation` 首行即此判断）。
    2. **每动作独立过滤**：`auto_like` 用 like 的匹配词、`auto_collect` 用 collect 的
       （源项目 `matchesAutomationText(text, config, "like"/"collect")`）。
    """

    def __init__(self, cfg: Optional[dict[str, Any]] = None, *,
                 monitors: Optional[dict[MonitorSource, MonitorFn]] = None,
                 actor: Optional[ActionFn] = None,
                 send_delay_ms: int = 0,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.cfg = normalize_config(cfg)
        self.monitors = monitors or {}
        self.actor = actor
        self.send_delay_ms = clamp_send_delay(send_delay_ms)
        self._sleep = sleep
        self.dedupe = DedupeSet()
        self.history: list[ActionResult] = []

    # ---- 配置读取（命名照源项目）----
    @property
    def max_actions(self) -> int:
        return int(self.cfg["auto_max_actions_per_run"])

    @property
    def scan_interval(self) -> int:
        return int(self.cfg["auto_scan_interval_seconds"])

    @property
    def enabled_sources(self) -> list[MonitorSource]:
        """照源项目 `auto_monitor_*` 布尔位决定启用哪些源。"""
        return [s for s in MonitorSource if self.cfg.get(f"auto_monitor_{s.value}")]

    def enabled_actions(self) -> list[str]:
        """照源项目：`auto_like` / `auto_collect` 布尔位 + 本项目扩展位。"""
        acts = [a for a in ("like", "collect", "comment", "private") if self.cfg.get(f"auto_{a}")]
        if self.cfg.get("auto_follow") or self.cfg.get("auto_follow_back_on_new_follower"):
            acts.append("follow")
        return acts

    # ---- 单轮 ----
    def run_once(self, *, actions: Optional[list[str]] = None,
                 dry_run: bool = False, per_source_limit: int = 50) -> dict[str, Any]:
        t0 = time.time()
        acts = actions if actions is not None else self.enabled_actions()
        sources = self.enabled_sources
        st: dict[str, Any] = {
            "sources": [s.value for s in sources], "actions": list(acts),
            "scanned": 0, "metrics_out": 0, "keyword_out": 0, "deduped": 0,
            "acted": 0, "errors": 0, "dry_run": dry_run,
        }
        if not sources:
            st["note"] = "未启用任何监控源（auto_monitor_*）"
            return st
        if not acts:
            st["note"] = "未启用任何动作（auto_like / auto_collect / …）"
            return st

        # 1) 取候选
        cands: list[VideoCandidate] = []
        for s in sources:
            fn = self.monitors.get(s)
            if not fn:
                continue
            try:
                cands.extend(fn(s, per_source_limit) or [])
            except Exception as e:  # noqa: BLE001
                logger.warning("AUTO-001", f"监控源 {s.value} 取候选失败: {type(e).__name__}")
                st["errors"] += 1
        st["scanned"] = len(cands)

        # 2) 门槛 → 去重 → 按动作过滤 → 执行
        acted = 0
        for v in cands:
            if acted >= self.max_actions:
                break
            # 照源项目：门槛不过直接跳过（不进入关键词判断）
            if not meets_metrics(v, self.cfg):
                st["metrics_out"] += 1
                continue
            # 去重（照源项目 rememberAutomationKey）
            if not self.dedupe.remember(v.aweme_id):
                st["deduped"] += 1
                continue
            text = v.automation_text()
            for a in acts:
                # 每个动作独立过滤（照源项目 matchesAutomationText(..., target)）
                if not matches_text(text, self.cfg, a):
                    st["keyword_out"] += 1
                    continue
                # 照源项目：已点赞/已收藏不重复动作
                if a == "like" and v.is_liked:
                    continue
                if a == "collect" and v.is_collected:
                    continue
                if dry_run:
                    res = ActionResult(v.aweme_id, a, True, "dry_run")
                elif self.actor is None:
                    res = ActionResult(v.aweme_id, a, False, "未注入动作实现")
                else:
                    try:
                        res = self.actor(v, a)
                    except Exception as e:  # noqa: BLE001
                        res = ActionResult(v.aweme_id, a, False, f"{type(e).__name__}: {e}")
                self.history.append(res)
                if res.ok:
                    acted += 1
                else:
                    st["errors"] += 1
                if not dry_run and self.send_delay_ms:
                    self._sleep(self.send_delay_ms / 1000.0)

        st["acted"] = acted
        st["duration_ms"] = int((time.time() - t0) * 1000)
        return st

    def status(self) -> dict[str, Any]:
        return {
            "sources": [s.value for s in self.enabled_sources],
            "actions": self.enabled_actions(),
            "auto_max_actions_per_run": self.max_actions,
            "auto_scan_interval_seconds": self.scan_interval,
            "send_delay_ms": self.send_delay_ms,
            "dedupe_size": len(self.dedupe),
            "history": len(self.history),
        }
