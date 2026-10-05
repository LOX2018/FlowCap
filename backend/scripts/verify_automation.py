# -*- coding: utf-8 -*-
"""自动化引擎 —— 实机验证（阶段3）

## 铁律：环境隔离（强制本分支环境）

## 权威基准

源项目壳 `frontend/src/lib/ai-automation.ts`（完整可读契约，非仅字符串逆向）：
  · 25 个 `auto_*` 字段 + 默认值 + clamp 范围
  · `tokens` / `targetKeywords` / `matchesAutomationText`（过滤算法）
  · `meetsVideoAutomationMetrics`（门槛）
  · `videoAutomationText`（文本拼接口径）
  · `runVideoAutomation`（编排；门槛先于动作）
  · `rememberAutomationKey` + `AI_AUTOMATION_DEDUPE_LIMIT=1000`（去重）
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

DESIGN_ROOT = r"C:\temp\flowcap_design"
_SOURCE_REPO = r"C:\Users\LOX\Desktop\DYchajian"
# 2026-09-20：主分支环境 C:\temp\flowcap_test 已废弃删除（两分支合并为
# design/better-douyin）。隔离门禁改为显式 if + raise，防「数据落进源码树」
# —— 原本用 assert，但 `python -O` 会整体剥离 assert 使守卫失效。
if os.path.abspath(DESIGN_ROOT).startswith(os.path.abspath(_SOURCE_REPO) + os.sep):
    raise SystemExit(
        "拒绝运行：环境根落在源码树内，会污染源码库"
        r"（应使用 C:\temp\flowcap_design）")
os.environ["FLOWCAP_APP_ROOT"] = DESIGN_ROOT
os.environ.setdefault("PYTHON_BASIC_REPL", "1")
_s = json.loads((Path(DESIGN_ROOT) / "members" / ".session.json").read_text(encoding="utf-8"))
os.environ["DY_MEMBER"] = _s["member_id"]
os.environ["DY_MEMBER_KEY"] = _s["master_key"]

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
R: list[tuple[str, bool, str]] = []


def check(n: str, ok: bool, d: str = "") -> None:
    R.append((n, ok, d))
    print(f"  {'✓' if ok else '✗'} {n}" + (f" — {d}" if d else ""))


def main() -> int:
    print(f"\n环境: {os.environ['FLOWCAP_APP_ROOT']}\n" + "=" * 64)

    from services import app_config as AC
    from services import automation_engine as AE

    # ── 1. 字段契约（对标源项目 ai-automation.ts）────────────────────
    print("\n[1] 字段契约（源项目 ai-automation.ts，25 项）")
    fl = (AC.SECTIONS.get("automation") or {}).get("fields") or {}
    SRC = ["monitor_notices", "monitor_friends", "monitor_comments", "monitor_feed",
           "follow_back_on_new_follower", "match_keywords", "exclude_keywords",
           "private_match_keywords", "private_exclude_keywords",
           "comment_match_keywords", "comment_exclude_keywords",
           "like_match_keywords", "like_exclude_keywords",
           "collect_match_keywords", "collect_exclude_keywords",
           "min_digg_count", "min_comment_count", "min_play_count",
           "scan_interval_seconds", "max_actions_per_run",
           "return_shared_media", "return_shared_allow_images",
           "return_shared_allow_videos", "return_shared_max_size_mb",
           "return_shared_max_media_count"]
    miss = [f for f in SRC if f not in fl]
    check("源项目 25 字段全覆盖", not miss, f"{len(fl)} 字段" + (f"，缺 {miss}" if miss else ""))

    # 默认值对齐（源项目 DEFAULT_AI_AUTOMATION）
    def d(k):
        return (fl.get(k) or {}).get("default")
    defaults_ok = (
        d("scan_interval_seconds") == 30 and d("max_actions_per_run") == 5
        and d("return_shared_max_size_mb") == 20 and d("return_shared_max_media_count") == 9
        and d("monitor_notices") is False and d("return_shared_allow_images") is True)
    check("默认值对齐源项目（30/5/20/9/false/true）", defaults_ok,
          f"interval={d('scan_interval_seconds')} batch={d('max_actions_per_run')} "
          f"size={d('return_shared_max_size_mb')} media={d('return_shared_max_media_count')}")

    # ── 2. clamp 范围（照源项目 normalize）──────────────────────────
    print("\n[2] clamp 范围（照源项目 normalizeAiAutomationConfig）")
    check("interval clamp 10~300", AE.clamp_interval(5) == 10 and AE.clamp_interval(9999) == 300,
          f"5→{AE.clamp_interval(5)}, 9999→{AE.clamp_interval(9999)}")
    check("batch clamp 1~50", AE.clamp_batch(0) == 1 and AE.clamp_batch(99) == 50,
          f"0→{AE.clamp_batch(0)}, 99→{AE.clamp_batch(99)}")
    check("send_delay clamp 0~10000",
          AE.clamp_send_delay(-5) == 0 and AE.clamp_send_delay(99999) == 10000)
    check("media_count clamp 1~20", AE.clamp_max_media_count(0) == 1 and AE.clamp_max_media_count(99) == 20)
    norm = AE.normalize_config(None)
    check("normalize 默认值兜底", norm["auto_scan_interval_seconds"] == 30
          and norm["auto_max_actions_per_run"] == 5, f"{len(norm)} 键")

    # ── 3. 过滤算法（照源项目 matchesAutomationText）─────────────────
    print("\n[3] 过滤算法（matchesAutomationText 逐行等价）")
    cfg = {"auto_match_keywords": "工伤,赔偿", "auto_exclude_keywords": "广告",
           "auto_like_match_keywords": "显卡", "auto_like_exclude_keywords": "抽奖"}
    check("tokens 多分隔符", AE.tokens("a, b，c\nd  e") == ["a", "b", "c", "d", "e"],
          str(AE.tokens("a, b，c\nd  e")))
    check("排除词命中 → false", not AE.matches_text("工伤 广告", cfg, "comment"))
    check("包含词命中 → true", AE.matches_text("工伤赔偿指南", cfg, "comment"))
    check("包含词未命中 → false", not AE.matches_text("美食教程", cfg, "comment"))
    check("无包含词 → true（照源项目 includes.length===0 返 true）",
          AE.matches_text("任意内容", {}, "comment"))
    check("动作专属词优先生效（like=显卡）",
          AE.matches_text("显卡评测", cfg, "like") and not AE.matches_text("工伤赔偿", cfg, "like"),
          "like 用 auto_like_* 而非通用")
    check("专属词为空时回落通用词",
          AE.matches_text("工伤赔偿", {"auto_match_keywords": "工伤",
                                       "auto_like_match_keywords": ""}, "like"))
    check("should_automate_text 空文本 → false", not AE.should_automate_text("  ", cfg))

    # ── 4. 门槛（照源项目 meetsVideoAutomationMetrics）──────────────
    print("\n[4] 门槛（meetsVideoAutomationMetrics）")
    mcfg = {"auto_min_digg_count": 100, "auto_min_comment_count": 10, "auto_min_play_count": 1000}
    v_ok = AE.VideoCandidate(aweme_id="1", digg_count=200, comment_count=20, play_count=5000)
    v_low = AE.VideoCandidate(aweme_id="2", digg_count=50, comment_count=20, play_count=5000)
    check("三项达标 → true", AE.meets_metrics(v_ok, mcfg))
    check("点赞不足 → false", not AE.meets_metrics(v_low, mcfg))
    check("评论不足 → false",
          not AE.meets_metrics(AE.VideoCandidate(digg_count=200, comment_count=5, play_count=5000), mcfg))
    check("播放不足 → false",
          not AE.meets_metrics(AE.VideoCandidate(digg_count=200, comment_count=20, play_count=10), mcfg))

    # ── 5. 文本拼接口径（照源项目 videoAutomationText）──────────────
    print("\n[5] 文本拼接口径（videoAutomationText）")
    v_txt = AE.VideoCandidate(aweme_id="7654321", desc="工伤指南", author_nickname="老杨",
                              author_signature="专注工伤", music_title="BGM", music_author="歌手")
    t = v_txt.automation_text()
    check("含 desc/作者/签名/音乐/aweme_id", all(x in t for x in
          ["工伤指南", "老杨", "专注工伤", "BGM", "歌手", "7654321"]), t[:60])

    # ── 6. 去重（照源项目 rememberAutomationKey，上限 1000）──────────
    print("\n[6] 去重（AI_AUTOMATION_DEDUPE_LIMIT=1000）")
    dd = AE.DedupeSet()
    check("首次记住 → True", dd.remember("a1") is True)
    check("重复 → False", dd.remember("a1") is False)
    check("空键 → False", dd.remember("") is False)
    check("上限常量 = 1000", AE.AI_AUTOMATION_DEDUPE_LIMIT == 1000)
    dd2 = AE.DedupeSet(limit=3)
    for k in ["a", "b", "c", "d"]:
        dd2.remember(k)
    check("超限淘汰最早（保留 3）", len(dd2) == 3, f"size={len(dd2)}")

    # ── 7. 编排（照源项目 runVideoAutomation 语义）──────────────────
    print("\n[7] 编排（门槛先于动作 + 每动作独立过滤 + 限量）")
    cands = [AE.VideoCandidate(aweme_id=f"C{i}", desc="工伤赔偿指南",
                               digg_count=999, comment_count=99, play_count=99999)
             for i in range(10)]
    cands.append(AE.VideoCandidate(aweme_id="LOW", desc="工伤", digg_count=0, play_count=0))
    seen: list[str] = []

    def actor(v, a):
        seen.append(f"{v.aweme_id}:{a}")
        return AE.ActionResult(v.aweme_id, a, True)

    cfg2 = {"auto_monitor_feed": True, "auto_like": True, "auto_collect": True,
            "auto_max_actions_per_run": 3,
            # 门槛：使 LOW 候选（全 0 指标）被拦（否则默认 0 阈值会放行）
            "auto_min_digg_count": 100, "auto_min_play_count": 1000}
    eng = AE.AutomationEngine(cfg2, monitors={AE.MonitorSource.FEED: lambda s, n: cands},
                              actor=actor)
    st = eng.run_once(actions=["like"])
    check("限量生效（max=3）", st["acted"] == 3, f"acted={st['acted']} scanned={st['scanned']}")
    check("去重生效（同轮同 id 不重复）", len(eng.history) == 3, f"{len(eng.history)} 条")

    # 门槛独立验证：把 LOW 候选放最前 + 提高上限，确认被 metrics 拦截
    eng_low = AE.AutomationEngine(
        dict(cfg2, auto_max_actions_per_run=50),
        monitors={AE.MonitorSource.FEED: lambda s, n: [cands[-1]] + cands[:-1]},
        actor=actor)
    st_low = eng_low.run_once(actions=["like"])
    check("门槛拦截计入 metrics_out", st_low["metrics_out"] == 1,
          f"metrics_out={st_low['metrics_out']} acted={st_low['acted']}")

    # 每动作独立过滤
    seen.clear()
    cfg3 = {"auto_monitor_feed": True, "auto_like": True, "auto_collect": True,
            "auto_like_match_keywords": "工伤", "auto_collect_match_keywords": "显卡"}
    eng3 = AE.AutomationEngine(cfg3, monitors={AE.MonitorSource.FEED: lambda s, n: cands[:1]},
                               actor=actor)
    eng3.run_once(actions=["like", "collect"])
    check("每动作独立过滤（like 通过 / collect 被拦）",
          seen == ["C0:like"], f"seen={seen}")

    # ── 8. 节流（send_delay_ms）────────────────────────────────────
    print("\n[8] 节流（send_delay_ms，用假 sleep 断言）")
    sleeps: list[float] = []
    eng4 = AE.AutomationEngine(cfg2, monitors={AE.MonitorSource.FEED: lambda s, n: cands[:3]},
                               actor=actor, send_delay_ms=1500,
                               sleep=lambda s: sleeps.append(s))
    eng4.run_once(actions=["like"])
    check("按 send_delay_ms 等待", len(sleeps) == 3 and all(abs(x-1.5) < 1e-6 for x in sleeps),
          f"{len(sleeps)} 次 {set(sleeps)}")

    # ── 9. dry_run 与显式提示 ──────────────────────────────────────
    print("\n[9] dry_run / 未启用显式提示")
    side: list[str] = []
    e5 = AE.AutomationEngine(cfg2, monitors={AE.MonitorSource.FEED: lambda s, n: cands[:2]},
                             actor=lambda v, a: side.append(a))
    st5 = e5.run_once(actions=["like"], dry_run=True)
    check("dry_run 不调动作实现", not side and st5["dry_run"] is True)
    s6 = AE.AutomationEngine({}).run_once()
    check("无监控源 → 显式说明（不静默）", "未启用任何监控源" in s6.get("note", ""), s6.get("note", ""))

    # ── 10. 监控源 4 项 ────────────────────────────────────────────
    print("\n[10] 监控源（照源项目 4 个 auto_monitor_*）")
    check("4 个监控源", len(list(AE.MonitorSource)) == 4,
          " ".join(s.value for s in AE.MonitorSource))

    print("\n" + "=" * 64)
    ok = sum(1 for _, o, _ in R if o)
    print(f"结果: {ok}/{len(R)} 通过")
    for n, o, d in R:
        if not o:
            print(f"  失败: {n} — {d}")
    return 0 if ok == len(R) else 1


if __name__ == "__main__":
    raise SystemExit(main())
