# -*- coding: utf-8 -*-
"""内置 Agent 配置契约审计 + ensure_default_dispatch_agent 返回值修复验证。

背景
----
1) `ai_agent.ensure_default_dispatch_agent()` 类型注解是 `-> dict`，
   但两条 return 路径实际返回 `tuple("ai_agents", <agent>)`。
   当前全部调用方都忽略返回值（暂不炸），但任一方按注解写
   `d = ensure_default_dispatch_agent()` 就会拿到 tuple —— 契约违约。
2) 「内置 Agent 有哪些参数」此前只散落在源码里，没有可执行的契约断言。
   本脚本把参数面固化成断言（白名单键集 / 请求体只发 4 键 / 调度 Agent
   高危权限默认关 / 作用域常量），防止后续改动漂移。

分层（对齐 references/verification_script_layering.md）
  L1 契约层 —— 真 services.ai_reply._DEFAULT_CONFIG 键集（不硬编码副本）
  L2 返回层 —— 真 services.ai_agent.ensure_default_dispatch_agent() 类型
  L3 请求层 —— 真 services.ai_reply.AIClient._chat_openai 组装的 payload（拦截 requests.post）
  L4 源码层 —— 调度 Agent permissions 高危项默认 False（源码契约，不依赖 DB）
  L5 作用域 —— AGENT_SCOPES 常量与 SCOPE_LABELS 一一对应

环境门禁：脚本自设 FLOWCAP_APP_ROOT = C:\\temp\\flowcap_design（design 分支专用，
禁串 test 环境）。不改任何项目文件，只在临时根里读写 kv。

用法：
    python backend/scripts/verify_ai_agent_contract.py
退出码 0 = 全部通过。
"""
from __future__ import annotations

import inspect
import os
import sys

# ---------------------------------------------------------------------------
# 环境门禁（铁律：数据根由脚本内置，不靠调用方 export）
# ---------------------------------------------------------------------------
_DESIGN_ROOT = r"C:\temp\flowcap_design"
_FORBIDDEN = (r"C:\temp\flowcap_test",)
if os.path.abspath(_DESIGN_ROOT) in [os.path.abspath(x) for x in _FORBIDDEN]:
    print("[FATAL] 环境门禁：数据根与禁用环境相同，拒绝运行")
    sys.exit(2)
os.environ["FLOWCAP_APP_ROOT"] = _DESIGN_ROOT

_BACKEND = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
if _BACKEND not in sys.path:
    sys.path.insert(0, _BACKEND)

FAIL: list[str] = []
PASS = 0


def chk(cond: bool, label: str, detail: str = "") -> None:
    global PASS
    if cond:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL.append(label)
        print(f"  [FAIL] {label}  {detail}")


print("=" * 70)
print("内置 Agent 配置契约验证")
print("=" * 70)

from services import ai_agent  # noqa: E402
from services import ai_reply  # noqa: E402

# ---------------------------------------------------------------------------
# L1 契约层：Agent 可配置键集（真源 = ai_reply._DEFAULT_CONFIG）
# ---------------------------------------------------------------------------
print("\n[L1] Agent 可配置键集（真源 _DEFAULT_CONFIG）")

cfg_keys = set(ai_reply._DEFAULT_CONFIG.keys())
# save_agent / save_config 额外允许的两个键
EXTRA_KEYS = {"knowledge_base", "blacklist"}
allowed_keys = cfg_keys | EXTRA_KEYS

print(f"  -- _DEFAULT_CONFIG 键数 = {len(cfg_keys)}")
GROUPS = {
    "主模型(LLM)": ["api_key", "api_protocol", "base_url", "model",
                    "max_tokens", "temperature"],
    "视觉": ["vision_enabled", "vision_base_url", "vision_api_key",
             "vision_model", "vision_prompt"],
    "语义(embedding)": ["sem_enabled", "sem_base_url", "sem_api_key",
                        "sem_model", "sem_threshold"],
    "人格/获客": ["merchant_name", "strict_level", "system_prompt",
                  "lead_confirm", "max_lead_ask"],
    "分类与作用域": ["kind", "scopes"],
    "节奏/兜底": ["min_delay", "max_delay", "max_history", "fallback_pool",
                  "fallback_image", "forbidden_words", "max_reply_len",
                  "enabled", "knowledge_first"],
}
seen: set[str] = set()
for gname, keys in GROUPS.items():
    missing = [k for k in keys if k not in cfg_keys]
    chk(not missing, f"参数组「{gname}」键齐全", f"缺失: {missing}")
    seen.update(keys)
leftover = cfg_keys - seen
chk(not leftover, "_DEFAULT_CONFIG 无未归类键（分组表覆盖完整）",
    f"未归类: {sorted(leftover)}")

# LLM 调优面现状（事实固化，非断言）：只有 max_tokens / temperature
TUNING_KNOBS = {"max_tokens", "temperature"}
ABSENT_KNOBS = {"top_p", "top_k", "presence_penalty", "frequency_penalty",
                "stop", "seed"}
for k in TUNING_KNOBS:
    chk(k in cfg_keys, f"调优旋钮 {k} 存在")
for k in sorted(ABSENT_KNOBS):
    chk(k not in cfg_keys, f"调优旋钮 {k} 当前**未**开放（现状固化）")

# 关键默认值
chk(ai_reply._DEFAULT_CONFIG["max_tokens"] == 1000,
    "默认 max_tokens = 1000", f"实际 {ai_reply._DEFAULT_CONFIG.get('max_tokens')}")
chk(abs(float(ai_reply._DEFAULT_CONFIG["temperature"]) - 0.7) < 1e-9,
    "默认 temperature = 0.7", f"实际 {ai_reply._DEFAULT_CONFIG.get('temperature')}")
chk(ai_reply._DEFAULT_CONFIG["strict_level"] == "rag", "默认 strict_level = rag")
chk(ai_reply._DEFAULT_CONFIG["enabled"] is False,
    "默认 enabled = False（保守：不自动接发送侧）")
chk(ai_reply._DEFAULT_CONFIG["model"] == "glm-5.2", "默认 model = glm-5.2")
chk("微信" in (ai_reply._DEFAULT_CONFIG.get("forbidden_words") or []),
    "forbidden_words 含「微信」（站外引流拦截）")

# ---------------------------------------------------------------------------
# L2 返回层：ensure_default_dispatch_agent 返回值契约
# ---------------------------------------------------------------------------
print("\n[L2] ensure_default_dispatch_agent() 返回值契约")

sig = inspect.signature(ai_agent.ensure_default_dispatch_agent)
ann = sig.return_annotation
ann_name = getattr(ann, "__name__", str(ann))
chk(ann_name == "dict", "注解声明返回 dict", f"实际注解 {ann_name}")

# 真实调用（走临时 kv，不污染会员库）
r1 = ai_agent.ensure_default_dispatch_agent()
chk(isinstance(r1, dict), "首次预置返回 dict", f"实际 type={type(r1).__name__}")
if isinstance(r1, dict):
    chk("id" in r1 or "name" in r1, "返回值含 Agent 标识字段",
        f"keys={sorted(r1.keys())[:8]}")

r2 = ai_agent.ensure_default_dispatch_agent()
chk(isinstance(r2, dict), "幂等二次调用返回 dict", f"实际 type={type(r2).__name__}")
chk(type(r1) is type(r2), "首次/幂等两次返回类型一致",
    f"{type(r1).__name__} vs {type(r2).__name__}")

# get_dispatch_agent 必须返回可下发的 dict
d = ai_agent.get_dispatch_agent()
chk(isinstance(d, dict), "get_dispatch_agent() 返回 dict",
    f"实际 type={type(d).__name__}")
if isinstance(d, dict):
    chk(d.get("id") == ai_agent._DISPATCH_ID, "dispatch id = ag_dispatch_default",
        f"实际 {d.get('id')}")
    chk(d.get("kind") == "dispatch", "kind = dispatch", f"实际 {d.get('kind')}")

# ---------------------------------------------------------------------------
# L3 请求层：实际发给 LLM 的 payload 只含 4 键
# ---------------------------------------------------------------------------
print("\n[L3] LLM 请求体实际键（拦截 requests.post）")

import services.ai_reply as _ar  # noqa: E402

captured: dict = {}
_orig_post = None
try:
    import requests  # noqa: E402

    _orig_post = requests.post

    def _fake_post(url, headers=None, json=None, timeout=None, **kw):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers

        class _R:
            status_code = 200

            def json(self):
                return {"choices": [{"message": {"content": "ok"}}]}
        return _R()

    requests.post = _fake_post

    cfg = dict(ai_reply._DEFAULT_CONFIG)
    cfg["api_key"] = "TEST-KEY-DO-NOT-LOG"
    cli = _ar.AIClient(cfg)
    try:
        cli.chat("你好", "u1", "你是客服", [{"role": "user", "content": "在吗"}])
    except Exception as e:  # noqa: BLE001
        print(f"  -- chat 抛异常（不影响 payload 捕获）: {e}")

    payload = captured.get("json") or {}
    keys = set(payload.keys())
    print(f"  -- 实际 payload 键 = {sorted(keys)}")
    chk(keys == {"model", "messages", "max_tokens", "temperature"},
        "payload 恰好 4 键（model/messages/max_tokens/temperature）",
        f"实际 {sorted(keys)}")
    chk("top_p" not in keys, "payload 不含 top_p（现状固化）")
    chk("stop" not in keys, "payload 不含 stop（现状固化）")
    chk(payload.get("max_tokens") == 1000, "payload max_tokens = 1000")
    chk(abs(float(payload.get("temperature", -1)) - 0.7) < 1e-9,
        "payload temperature = 0.7")
    chk(str(captured.get("url", "")).endswith("/chat/completions"),
        "请求打到 /chat/completions", str(captured.get("url")))
    hdrs = captured.get("headers") or {}
    chk(hdrs.get("Authorization") == "Bearer TEST-KEY-DO-NOT-LOG",
        "api_key 以 Bearer 头下发")
    # 密钥不得出现在 body
    chk("TEST-KEY-DO-NOT-LOG" not in str(payload.get("messages", "")),
        "密钥不出现在 messages body 中")
finally:
    if _orig_post is not None:
        import requests as _rq  # noqa: E402
        _rq.post = _orig_post

# ---------------------------------------------------------------------------
# L4 源码层：调度 Agent 高危权限默认关
# ---------------------------------------------------------------------------
print("\n[L4] 调度 Agent 权限默认值（源码契约，不依赖 DB）")

import re  # noqa: E402

src = inspect.getsource(ai_agent.ensure_default_dispatch_agent)
PERM_TRUE = ["query_status", "create_crawl_task", "create_live_task",
             "stop_task", "update_kb"]
PERM_FALSE = ["create_agent", "recapture"]
for p in PERM_TRUE:
    m = re.search(rf'"{p}":\s*(True|False)', src)
    chk(bool(m) and m.group(1) == "True", f"权限 {p} 默认 True",
        f"实际 {m.group(1) if m else '未找到'}")
for p in PERM_FALSE:
    m = re.search(rf'"{p}":\s*(True|False)', src)
    chk(bool(m) and m.group(1) == "False", f"高危权限 {p} 默认 False",
        f"实际 {m.group(1) if m else '未找到'}")

# ---------------------------------------------------------------------------
# L5 作用域常量
# ---------------------------------------------------------------------------
print("\n[L5] 作用域常量一致性")

chk(set(ai_agent.SCOPE_LABELS.keys()) == set(ai_agent.AGENT_SCOPES),
    "SCOPE_LABELS 与 AGENT_SCOPES 键一一对应",
    f"{sorted(ai_agent.AGENT_SCOPES)} vs {sorted(ai_agent.SCOPE_LABELS)}")
chk(set(ai_reply._DEFAULT_CONFIG["scopes"]) == set(ai_agent.AGENT_SCOPES),
    "默认 scopes 覆盖全部 AGENT_SCOPES")
chk(ai_agent._KV_AGENTS == "ai_agents", "kv 键 = ai_agents")
chk(ai_agent._KV_BINDINGS == "ai_account_agent",
    "绑定键 = ai_account_agent（**不是** ai_agent_bind）",
    f"实际 {ai_agent._KV_BINDINGS}")

# ---------------------------------------------------------------------------
# 汇总
# ---------------------------------------------------------------------------
print("\n" + "=" * 70)
print(f"PASS = {PASS}   FAIL = {len(FAIL)}")
if FAIL:
    for f in FAIL:
        print(f"  ✗ {f}")
    print("=" * 70)
    sys.exit(1)
print("全部通过")
print("=" * 70)
sys.exit(0)
