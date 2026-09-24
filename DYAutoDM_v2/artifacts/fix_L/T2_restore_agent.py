# -*- coding: utf-8 -*-
"""T2 恢复：重建「工伤赔偿留资助手（唐律）」Agent 并绑定小助理。

背景（2026-09-24 父会话实测）：
  DY-01 卡 §1 第 8 行 / `AI回复勘探手册.md` §6 / `knowledge/cases/ai-agent-config-contract.md` §5
  均记录 Agent `ag_88387f9ee4e34dc1` 已生成并回读验证通过；但 2026-09-24 全库检索
  （会员库 / 全局库 / 全部 .bak / WAL / dyautodm_design 下所有 .db）**零命中**
  → 该 Agent 已不在库中（库被重置或写入未持久化），须恢复。

恢复口径（按用户 2026-09-24 指令「T2 恢复」）：
  · 复用**原 agent_id** `ag_88387f9ee4e34dc1`（保持卡片/案例史与实库可对应）。
  · 只写**覆盖键**（merchant_name / system_prompt / strict_level / scopes / max_tokens /
    temperature / enabled）——`ai_agent.resolve_config()` 逐键覆盖全局 base，未列键沿用全局，
    与原始记录形态一致。
  · 走**应用自身 API**（`ai_agent.save_agent` / `ai_agent.bind`），不直写 kv_store。
  · 幂等：已存在同 id 则**拒绝覆盖**（避免抹掉用户后续手改）。

⚠️ 诚实标注：原始 685 字 system_prompt 的**逐字原文已不可回捞**（日志/记录文件均无）。
  本脚本按已归档的**设计意图 + 真实话术样本**重建（问伤情→问地区→判断等级→钩子留资），
  属**重建**而非原样还原。原文一旦找到应以其为准覆盖。
"""
from __future__ import annotations

import json
import os
import sys

AGENT_ID = "ag_88387f9ee4e34dc1"
AGENT_NAME = "工伤赔偿留资助手（唐律）"
ACCOUNT = "尚进工伤小助理"

# 依据 knowledge/cases/ai-agent-config-contract.md §5 + AI回复勘探手册.md §6 的三条真实话术重建：
#   · 你联系方式多少，我给你算份赔偿清单，到时候按照清单找公司谈一下
#   · 留个联系~方式，唐律下播帮你分析
#   · 这样吧，我给你发点资料，你按照话术上面去和医院沟通
SYSTEM_PROMPT = (
    "你是「唐律工伤团队」的私人助理，代唐律在抖音私信里接待工伤工友。\n"
    "风格：亲切、专业、不冷场；像熟人一样先关心伤情，再一步步引导留联系方式。\n"
    "节奏（严格分步，不要一口气问完）：\n"
    "1) 先问伤情：确认受伤部位与大致情况（如「是哪个部位受伤的？现在恢复得怎么样？」）。\n"
    "2) 再问地区：确认所在省市与是否已认定工伤（不同地区赔偿标准不同）。\n"
    "3) 初判等级：结合十级/九级等常见等级口径，给出初步判断与依据，但说明最终以鉴定为准。\n"
    "4) 钩子留资：说明可以免费帮忙算一份赔偿清单，请对方留下联系方式——"
    "「你联系方式多少，我给你算份赔偿清单，到时候按照清单找公司谈一下」。\n"
    "5) 跟进：若对方犹豫，用「留个联系~方式，唐律下播帮你分析」邀请；"
    "若对方要资料，用「这样吧，我给你发点资料，你按照话术上面去和医院沟通」承接。\n"
    "边界：不做法律承诺、不报绝对数额、不谈与工伤无关的话题；"
    "始终围绕工伤等级判定、地区差异、取证材料指导、复查沟通四个主题。"
)

CONFIG = {
    "merchant_name": "唐律工伤团队",
    "system_prompt": SYSTEM_PROMPT,
    "strict_level": "rag",
    "scopes": ["dm", "live", "crawl"],
    "max_tokens": 1000,
    "temperature": 0.7,
    "enabled": False,          # 保守：先建不启（沿用原始记录口径）
}


def main() -> int:
    os.environ["DY_APP_ROOT"] = r"C:\temp\dyautodm_design"
    os.environ["DY_MEMBER"] = "m17db0f8209156f26"
    _here = os.path.dirname(os.path.abspath(__file__))          # DYAutoDM_v2/artifacts/fix_L
    _backend = os.path.abspath(os.path.join(_here, "..", "..", "backend"))
    sys.path.insert(0, _backend)

    from services import ai_agent

    before = {a.get("id") for a in ai_agent.list_agents()}
    print("恢复前已有 Agent:", sorted(before) or "(无)")

    if AGENT_ID in before:
        print(f"✗ 已存在 {AGENT_ID} —— 幂等拒绝覆盖（避免抹掉后续手改）")
        return 2

    saved = ai_agent.save_agent(AGENT_ID, AGENT_NAME, dict(CONFIG))
    bind_res = ai_agent.bind(ACCOUNT, AGENT_ID)

    print("\n=== 恢复后回读（应用自身 API，非自述）===")
    agents = {a.get("id"): a for a in ai_agent.list_agents()}
    a = agents.get(AGENT_ID)
    print("list_agents() 含目标 Agent:", bool(a))
    if a:
        cfg = a.get("config") or {}
        print("  id        :", a.get("id"))
        print("  name      :", a.get("name"))
        print("  enabled   :", cfg.get("enabled"))
        print("  merchant  :", cfg.get("merchant_name"))
        print("  strict    :", cfg.get("strict_level"))
        print("  scopes    :", cfg.get("scopes"))
        print("  prompt 字数:", len(cfg.get("system_prompt") or ""))
    print("get_agent 非空:", bool(ai_agent.get_agent(AGENT_ID)))
    print("get_bindings():", json.dumps(ai_agent.get_bindings(), ensure_ascii=False))
    print("agent_of(%s): %s" % (ACCOUNT, ai_agent.agent_of(ACCOUNT)))

    # 解析链回读：确认 Agent 配置真的被 resolve_config 拾取
    from services import ai_reply
    base = ai_reply.get_config()
    merged = ai_agent.resolve_config(ACCOUNT, base)
    print("\n=== resolve_config(%s) 生效回读 ===" % ACCOUNT)
    print("  merchant_name:", merged.get("merchant_name"))
    print("  strict_level :", merged.get("strict_level"))
    print("  scopes       :", merged.get("scopes"))
    print("  prompt 字数  :", len(merged.get("system_prompt") or ""))

    ok = bool(a) and ai_agent.agent_of(ACCOUNT) == AGENT_ID and \
        merged.get("merchant_name") == CONFIG["merchant_name"] and \
        merged.get("system_prompt") == CONFIG["system_prompt"]
    print("\n恢复判定:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
