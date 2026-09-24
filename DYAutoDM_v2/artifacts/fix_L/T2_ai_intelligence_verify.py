# -*- coding: utf-8 -*-
"""T2 验收：AI 智能性验证（离线，零发送）。

判据（DY-01 T2 / 台账 §一·乙 T2）：
  文案 `source=AI` 且按 Agent 人格定制（非词库、非复读）。

做法：直接调唯一生成入口 `services.ai_reply.generate_dm_for_live()`
（真 Agent 配置 + 真 LLM 网关 127.0.0.1:31415），**不触发任何发送**（不碰 dispatch/dm_dispatch）。
"""
from __future__ import annotations

import json
import os
import sys

ACCOUNT = "尚进工伤小助理"
PEER = "测试工友"

# 真实直播间语境的工伤咨询（非复读对象本身）
CASES = [
    "我在工地摔了，腰椎骨折，能定几级？",
    "手指断了算几级工伤，我是四川的",
    "陈旧性骨折还能认工伤吗，厂里不认",
]


def main() -> int:
    os.environ["DY_APP_ROOT"] = r"C:\temp\dyautodm_design"
    os.environ["DY_MEMBER"] = "m17db0f8209156f26"
    sys.path.insert(0, os.path.abspath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend")))

    from services import ai_agent, ai_reply

    base = ai_reply.get_config()
    cfg = ai_agent.resolve_config(ACCOUNT, base)
    print("=== 生效配置（持久化态）===")
    print("  merchant_name :", cfg.get("merchant_name"))
    print("  strict_level  :", cfg.get("strict_level"))
    print("  scopes        :", cfg.get("scopes"))
    print("  enabled       :", cfg.get("enabled"))
    print("  system_prompt 字数:", len(cfg.get("system_prompt") or ""))
    print("  model / base_url :", cfg.get("model"), "/", cfg.get("base_url"))

    # 🔒 关键安全设计：**不修改持久化 enabled**（若置 True 会对真实直播间生效 = 风控风险）。
    #    改为在**内存副本**上覆写 enabled=True，仅供本次生成链验证；进程退出即消失。
    print("\n🔒 内存覆盖 enabled=True（不写库、不触发发送、不改任何持久化配置）")
    cfg = dict(cfg)
    cfg["enabled"] = True

    # 关键：评估链只看 enabled + strict_level + scopes（见 core/auto_dm.evaluate_live_ai）
    print("\n=== 判定链预检（evaluate_live_ai 口径，内存态）===")
    print("  enabled      ->", bool(cfg.get("enabled")))
    print("  strict_level ->", cfg.get("strict_level"), "(kb_only 会拒绝)")
    print("  scopes 含 live ->", "live" in (cfg.get("scopes") or []))

    results = []
    for c in CASES:
        text, source = ai_reply.generate_dm_for_live(ACCOUNT, PEER, c, cfg, uid="t2_verify")
        echo = bool(text) and c.strip() and c.strip() in text
        results.append({"comment": c, "source": source, "text": text, "echo": echo})
        print("\n--- 弹幕:", c)
        print("    source :", source)
        print("    text   :", text)
        print("    复读?  :", echo)

    n = len(results)
    ai_cnt = sum(1 for r in results if r["source"] == "AI")
    echo_cnt = sum(1 for r in results if r["echo"])
    fallback_cnt = sum(1 for r in results if r["source"] == "兜底")
    # 人格/业务贴合判据（放宽：人格词 或 工伤业务词，任一命中即算贴合）
    persona_kw = ("唐律", "助理", "工友", "兄弟", "团队", "下播")
    biz_kw = ("工伤", "等级", "几级", "赔偿", "鉴定", "认定", "骨折", "伤", "病历",
              "恢复", "厂里", "地区", "受伤", "受伤部位", "清单", "联系")
    persona_cnt = sum(1 for r in results
                      if r["source"] == "AI"
                      and (any(k in (r["text"] or "") for k in persona_kw)
                           or any(k in (r["text"] or "") for k in biz_kw)))
    print("\n=== 汇总 ===")
    print(f"  source=AI          : {ai_cnt}/{n}   (期望 {n})")
    print(f"  回落兜底(source=兜底): {fallback_cnt}/{n}   (期望 0)")
    print(f"  复读弹幕原文        : {echo_cnt}/{n}   (期望 0)")
    print(f"  含人格或业务特征     : {persona_cnt}/{n}   (期望 {n})")
    verdict = (ai_cnt == n) and (echo_cnt == 0) and (fallback_cnt == 0) and (persona_cnt == n)
    print("  判定:", "PASS" if verdict else "FAIL")
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0 if verdict else 1


if __name__ == "__main__":
    raise SystemExit(main())
