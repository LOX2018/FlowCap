"""模型链路中心 —— 常量 / 预设 / 匹配规则（纯数据）

## 为什么独立（2026-09-15 大单文件打散）

原 `services/model_hub.py`（734 行）把「域常量 + 消费方定义 + 提供商预设 +
能力正则 + guess_caps」与业务逻辑混在一起。按「数据与逻辑分离」抽出本模块。

## 说明
**纯搬移**——ROUTE_KINDS / MAX_CHAIN / CAPS / CONSUMERS / PROVIDER_PRESETS /
_VISION_PAT / _SEM_PAT / guess_caps 逐字节不变。
"""
from __future__ import annotations

import re

ROUTE_KINDS = ("llm", "vision", "sem")
MAX_CHAIN = 6
CAPS = ("llm", "vision", "sem")

# 消费方注册表（id → 说明）。前端据此渲染绑定 UI。
CONSUMERS: list[dict] = [
    {"id": "ai_main", "label": "AI 主模型（私信回复）", "module": "ai",
     "suggest_route": "llm"},
    {"id": "ai_vision", "label": "AI 视觉（图片理解）", "module": "ai",
     "suggest_route": "vision"},
    {"id": "ai_sem", "label": "AI 语义检索（知识库向量化）", "module": "ai",
     "suggest_route": "sem"},
    {"id": "notify_cmd", "label": "IM 通知指令解析", "module": "notify",
     "suggest_route": "llm"},
]

# 提供商预设（新建时快速填充 base_url/协议；静态模型清单仅作拉取失败时的备选）
PROVIDER_PRESETS: list[dict] = [
    {"id": "freellm", "name": "FreeLLM（本机聚合，优先推荐）",
     "base_url": "http://127.0.0.1:31415/v1", "api_protocol": "openai",
     "needs_key": False, "key_hint": "本机部署可留空"},
    {"id": "deepseek", "name": "DeepSeek 深度求索",
     "base_url": "https://api.deepseek.com/v1", "api_protocol": "openai",
     "needs_key": True, "key_hint": "sk-…（platform.deepseek.com）"},
    {"id": "zhipu", "name": "智谱 GLM",
     "base_url": "https://open.bigmodel.cn/api/paas/v4", "api_protocol": "openai",
     "needs_key": True, "key_hint": "…（open.bigmodel.cn）"},
    {"id": "dashscope", "name": "阿里云百炼（通义千问）",
     "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
     "api_protocol": "openai", "needs_key": True, "key_hint": "sk-…（百炼控制台）"},
    {"id": "moonshot", "name": "月之暗面 Kimi",
     "base_url": "https://api.moonshot.cn/v1", "api_protocol": "openai",
     "needs_key": True, "key_hint": "sk-…（platform.moonshot.cn）"},
    {"id": "volces", "name": "火山方舟（豆包）",
     "base_url": "https://ark.cn-beijing.volces.com/api/v3",
     "api_protocol": "openai", "needs_key": True,
     "key_hint": "…（方舟控制台，模型用接入点 ID）"},
    {"id": "minimax", "name": "MiniMax（Anthropic 兼容端点）",
     "base_url": "https://api.minimaxi.com/anthropic",
     "api_protocol": "anthropic", "needs_key": True,
     "key_hint": "eyJ…（MiniMax 开放平台）"},
    {"id": "custom", "name": "自定义（手填 Base URL）",
     "base_url": "", "api_protocol": "openai", "needs_key": True,
     "key_hint": "按服务商要求"},
]


# ---------------------------------------------------------------------------
# 启发式能力分类（拉取模型时预分类，UI 可改）
# ---------------------------------------------------------------------------

_VISION_PAT = re.compile(r"(vl|vision|omni|-v\d|4v|image|multimodal)", re.I)
_SEM_PAT = re.compile(r"(embed|bge|e5|m3e)", re.I)


def guess_caps(model_name: str) -> list[str]:
    n = str(model_name or "")
    if _SEM_PAT.search(n):
        return ["sem"]
    caps = ["llm"]
    if _VISION_PAT.search(n):
        caps.append("vision")
    return caps


# ---------------------------------------------------------------------------
# 存储
# ---------------------------------------------------------------------------

