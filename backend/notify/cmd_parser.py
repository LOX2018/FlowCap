"""IM 指令解析：LLM 自然语言 → 结构化任务意图。

用途
----
运营在微信/企微/钉钉/飞书/QQ 里发一句自然语言，例如：
    「给最近评论了 3C 数码的用户发一条私信，内容是新品上架了，间隔 5 秒」
后端解析成结构化 intent，再去调 FlowCap 已有的任务接口。

设计
----
1. **LLM 优先**：复用现有 OpenAI 兼容配置（与 ai_reply.py 同一套
   base_url/api_key/model 约定），走 function-calling / JSON mode 输出。
2. **规则兜底**：LLM 未配置或调用失败时，用正则 + 关键词抽取，
   保证「LLM 挂了也能下发任务」——通知链路不能因 LLM 故障整体失效。
3. **只读/破坏性分级**：start/stop/query 允许自动执行；
   create 任务默认返回待确认（需二次确认），避免误建任务。

**风控红线**：本模块只解析意图、调用 FlowCap 自有接口，
绝不引入任何抖音昵称批量查询（见工作记忆：昵称唯一来源为 BCC 被动 hook）。
"""

from __future__ import annotations

import json
import re
from typing import Any

from loguru import logger

# ---- 意图类型 ----
INTENT_CREATE = "create_task"      # 新建任务（破坏性：需确认）
INTENT_START = "start_task"        # 启动引擎
INTENT_STOP = "stop_task"          # 停止引擎
INTENT_STATUS = "query_status"     # 查询任务/账号状态
INTENT_RECAPTURE = "recapture"     # 凭证重新捕获（破坏性：需确认）
INTENT_HELP = "help"
INTENT_UNKNOWN = "unknown"

# 需要二次确认的意图
DESTRUCTIVE = {INTENT_CREATE, INTENT_RECAPTURE}

# 2026-09-17 安全修补（审查 P1-8）：intent 白名单。
# LLM 输出不可信（可被提示词注入劫持），任何要执行的 intent 必须命中本集合，
# 否则一律降级为 INTENT_UNKNOWN 并不执行。新增意图必须同时登记到
# _ALLOWED_INTENTS、执行分支（api/notify.py 的 _execute）与 gateway 的
# ROLE_INTENTS，三处缺一即不可用（fail-closed）。
_ALLOWED_INTENTS = frozenset({
    INTENT_CREATE,
    INTENT_START,
    INTENT_STOP,
    INTENT_STATUS,
    INTENT_RECAPTURE,
    INTENT_HELP,
    INTENT_UNKNOWN,
})

SYSTEM_PROMPT = """你是 FlowCap 抖音自动化运营助手的指令解析器。
把用户的自然语言指令解析为 JSON，不要输出任何解释文字。

可识别的意图：
- create_task 新建私信任务 {live_url?, keyword?, dm_text?, interval?, max_target?, account?}
- start_task  启动任务引擎 {}
- stop_task   停止任务引擎 {}
- query_status 查询当前任务/账号状态 {}
- recapture   重新捕获某账号凭证 {account?}
- help        请求帮助
- unknown     无法识别

输出严格 JSON 格式：
{"intent":"...","params":{...},"confirm_text":"给用户的确认语句"}

interval/max_target 尽量解析为整数。dm_text 原样保留用户写的文案。
无法确定的字段不要编造，留空字符串。"""


def _rule_parse(text: str) -> dict[str, Any]:
    """规则兜底解析（LLM 不可用时使用）。"""
    t = (text or "").strip()
    low = t.lower()

    if any(k in t for k in ("帮助", "怎么用", "help", "?")):
        return {"intent": INTENT_HELP, "params": {}}

    if re.search(r"(停止|停掉|结束|关闭).{0,4}(任务|引擎)", t):
        return {"intent": INTENT_STOP, "params": {}}
    if re.search(r"(启动|开始|开启|继续).{0,4}(任务|引擎)", t):
        return {"intent": INTENT_START, "params": {}}
    if any(k in t for k in ("状态", "进度", "情况", "怎么样", "查询")):
        return {"intent": INTENT_STATUS, "params": {}}
    if any(k in t for k in ("重捕获", "重新捕获", "重新扫码", "凭证失效")):
        m = re.search(r"(?:账号|account)[:：\s]*([\w\u4e00-\u9fa5-]+)", t)
        return {"intent": INTENT_RECAPTURE, "params": {"account": m.group(1) if m else ""}}

    # 新建任务：抽参数
    params: dict[str, Any] = {}
    m = re.search(r"(?:间隔|每隔|interval)[:：\s]*(\d+)", t)
    if m:
        params["interval"] = int(m.group(1))
    m = re.search(r"(?:数量|上限|最多|max)[:：\s]*(\d+)", t)
    if m:
        params["max_target"] = int(m.group(1))
    m = re.search(r"(https?://[^\s，,]+)", t)
    if m:
        params["live_url"] = m.group(1)
    m = re.search(r"(?:内容|文案|发送|私信)[是为说]?[:：\s]*(.+)", t)
    if m:
        params["dm_text"] = m.group(1).strip()
    m = re.search(r"(?:账号|account)[:：\s]*([\w\u4e00-\u9fa5-]+)", t)
    if m:
        params["account"] = m.group(1)

    if params:
        return {"intent": INTENT_CREATE, "params": params}
    return {"intent": INTENT_UNKNOWN, "params": {}, "confirm_text": "没理解你的意思，回复「帮助」查看用法。"}


async def _llm_parse(text: str, cfg: dict[str, Any]) -> dict[str, Any] | None:
    """调用 OpenAI 兼容 /chat/completions 解析。失败返回 None 由规则兜底。"""
    base = str(cfg.get("base_url", "")).strip().rstrip("/")
    model = str(cfg.get("model", "")).strip()
    if not base or not model:
        return None
    api_key = str(cfg.get("api_key", "")).strip()
    try:
        import aiohttp

        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            "temperature": 0.1,
            "max_tokens": 500,
        }
        # 优先尝试 JSON mode（多数 OpenAI 兼容端支持）
        try:
            payload["response_format"] = {"type": "json_object"}
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            ) as s:
                async with s.post(
                    f"{base}/chat/completions", headers=headers, json=payload
                ) as r:
                    if r.status != 200:
                        raise RuntimeError(await r.text())
                    body = await r.json(content_type=None)
        except Exception:
            async with aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=30)
            ) as s:
                payload.pop("response_format", None)
                async with s.post(
                    f"{base}/chat/completions", headers=headers, json=payload
                ) as r:
                    if r.status != 200:
                        return None
                    body = await r.json(content_type=None)

        content = body["choices"][0]["message"]["content"]
        # 兼容模型额外包 ```json 围栏的情况
        content = re.sub(r"^```(?:json)?|```$", "", content.strip()).strip()
        data = json.loads(content)
        if not isinstance(data, dict) or "intent" not in data:
            return None
        data.setdefault("params", {})
        return data
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[NTY-009] " + f"[cmd-parse] LLM 解析失败，回落规则: {e}")
        return None


async def parse_command(text: str, llm_cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """解析一条 IM 文本指令。

    返回: {intent, params, confirm_text, source: 'llm'|'rule', need_confirm: bool}
    """
    text = (text or "").strip()
    if not text:
        return {
            "intent": INTENT_UNKNOWN,
            "params": {},
            "confirm_text": "空指令",
            "source": "rule",
            "need_confirm": False,
        }

    data = None
    source = "rule"
    if llm_cfg:
        data = await _llm_parse(text, llm_cfg)
        if data:
            source = "llm"

    if not data:
        data = _rule_parse(text)

    intent = str(data.get("intent", INTENT_UNKNOWN)).strip() or INTENT_UNKNOWN
    # 2026-09-17 安全修补（审查 P1-8）：intent 此前**完全信任 LLM 输出**，
    # 未做白名单校验。攻击者只需发一句提示词注入文本，诱导 LLM 返回
    # {"intent":"start_task"}，即可在自己的 admin/operator 权限下直接启停引擎
    # —— start_task / stop_task 不在 DESTRUCTIVE 确认集里，无二次确认即执行。
    # 现改为：非白名单 intent 一律降级为 UNKNOWN（不执行任何动作）并告警。
    if intent not in _ALLOWED_INTENTS:
        logger.warning(f"[NTY-010] " + f"[cmd-parse] 非白名单 intent 已拒绝: {intent!r} "
            f"（允许={sorted(_ALLOWED_INTENTS)}）")
        intent = INTENT_UNKNOWN
        need_confirm = False
        params = {}
        return {
            "intent": intent,
            "params": params,
            "confirm_text": "",
            "source": source,
            "need_confirm": need_confirm,
        }
    # 2026-09-17 修补（OCR 审查 HIGH —— 不可信来源未做类型校验）：
    # `params` 直接来自 **LLM 输出**，`or {}` 只处理 falsy，不保证是 dict；
    # 若模型返回字符串/列表，非空值会一路传到 `params.get(...)` → AttributeError
    # 打崩解析链。强制归一为 dict。
    params = data.get("params")
    if not isinstance(params, dict):
        params = {}
    # 引擎启停同样要求二次确认（原 DESTRUCTIVE 只含 create/recapture）
    need_confirm = intent in DESTRUCTIVE or intent in (INTENT_START, INTENT_STOP)
    confirm_text = str(data.get("confirm_text") or "").strip()
    if not confirm_text:
        confirm_text = _default_confirm(intent, params)

    return {
        "intent": intent,
        "params": params,
        "confirm_text": confirm_text,
        "source": source,
        "need_confirm": need_confirm,
    }


def _default_confirm(intent: str, params: dict[str, Any]) -> str:
    if intent == INTENT_CREATE:
        bits = []
        if params.get("live_url"):
            bits.append(f"直播/来源: {params['live_url']}")
        if params.get("dm_text"):
            bits.append(f"文案: {params['dm_text']}")
        if params.get("interval"):
            bits.append(f"间隔: {params['interval']}s")
        if params.get("max_target"):
            bits.append(f"上限: {params['max_target']}")
        if params.get("account"):
            bits.append(f"账号: {params['account']}")
        return "即将新建任务 → " + ("；".join(bits) if bits else "（参数待补充）") + "\n回复「确认」执行，或回复「取消」。"
    if intent == INTENT_RECAPTURE:
        return f"即将重新捕获账号 {params.get('account') or '(默认)'} 凭证。回复「确认」执行。"
    if intent == INTENT_START:
        return "即将启动任务引擎。"
    if intent == INTENT_STOP:
        return "即将停止任务引擎。"
    if intent == INTENT_STATUS:
        return "正在查询状态…"
    if intent == INTENT_HELP:
        return (
            "可用指令示例：\n"
            "· 新建任务：给最近评论的用户发私信，内容是「新品上架」，间隔 5 秒\n"
            "· 启动任务 / 停止任务\n"
            "· 查询状态\n"
            "· 重新捕获凭证 账号:xxx"
        )
    return "未识别的指令，回复「帮助」查看用法。"
