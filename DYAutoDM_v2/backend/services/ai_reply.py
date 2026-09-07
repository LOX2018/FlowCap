# -*- coding: utf-8 -*-
"""
AI 获客留资自动回复服务（嵌入自 xyc667/douyin-auto-reply-assistant）。

来源与改造说明（2026-09-06 嵌入）：
- 原项目：https://github.com/xyc667/douyin-auto-reply-assistant
  （基于 Douyin_Spider 二开的 AI 自动回复桌面助手）
- 嵌入子集：KnowledgeBase（知识库模糊匹配）+ AIClient（Anthropic 兼容
  /v1/messages 协议）+ 随机延迟，三类核心能力整体移植。
- 获客改造（2026-09-06，用户明确目标）：
  agent 核心目标 = 留资（联系方式捕获入线索表），专业性由知识库定义。
  三档回复档位：kb_only（仅知识库）/ rag（AI 只准依据知识库答）/ free。
- 改造点（适配本仓库架构 + 铁律）：
  1. 存储全走全局 SQLite kv_store / ai_leads 表（database.py）。
  2. 回复只生成文本，发送 100% 复用 /api/messages/send 双通道链路
     （recv_daemon WS 主通道 + BCC WP 兜底），绝不复刻原项目直连协议层。
  3. 昵称/头像零主动查询 —— 纯读 dm_messages / dm_conversations。
  4. 图片理解用独立配置的视觉模型（主 LLM 未必多模态）：
     msg_type='27'（实库证真）图片消息 extra 含 skey/origin_url →
     origin_image_resolver.resolve 解密落盘 → base64 → 视觉模型(OpenAI
     兼容 /chat/completions) → 描述文本并入主回复流程。视觉未配置时
     图片走兜底话术，绝不瞎猜。
  5. AI 出口护栏：长度/违禁词/AI 腔校验，不通过改发兜底话术。
"""
from __future__ import annotations

import json
import random
import re
import threading
import time
import base64
from pathlib import Path
from typing import Optional

import requests
from loguru import logger

import database


# ---------------------------------------------------------------------------
# 表结构：ai_leads（留资线索）。幂等建表，import 后由 ensure_tables 调用。
# ---------------------------------------------------------------------------

def ensure_tables() -> None:
    conn = database.get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS ai_leads (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        account TEXT NOT NULL,
        conv_id TEXT NOT NULL,
        peer_name TEXT,
        contact_type TEXT NOT NULL,      -- phone / wechat
        contact_value TEXT NOT NULL,
        source_text TEXT DEFAULT '',
        status TEXT DEFAULT 'new',       -- new / followed / invalid
        created_at REAL NOT NULL,
        UNIQUE(account, conv_id, contact_type, contact_value)
    );
    CREATE INDEX IF NOT EXISTS idx_aileads_created
        ON ai_leads(created_at DESC);
    """)
    conn.commit()


# ---------------------------------------------------------------------------
# kv_store 读写小工具
# ---------------------------------------------------------------------------

def _kv_get(key: str, default):
    try:
        conn = database.get_db()
        row = conn.execute(
            "SELECT value FROM kv_store WHERE key=?", (key,)
        ).fetchone()
        if row and row[0]:
            return json.loads(row[0])
    except Exception as e:
        logger.warning(f"[ai] kv 读失败 {key}: {e}")
    return default


def _kv_set(key: str, value) -> None:
    conn = database.get_db()
    conn.execute(
        "INSERT INTO kv_store(key, value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, json.dumps(value, ensure_ascii=False)),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# 配置（kv_store: ai_reply_config）
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG = {
    "enabled": False,            # 总开关（全自动监听）
    # ---- 主 LLM（文本）----
    "api_key": "",
    "base_url": "https://api.minimaxi.com/anthropic",   # Anthropic 兼容协议
    "model": "MiniMax-M2.7",
    "max_tokens": 1000,
    "temperature": 0.7,
    # ---- 视觉模型（独立，OpenAI 兼容 /chat/completions）----
    "vision_enabled": False,
    "vision_base_url": "https://ark.cn-beijing.volces.com/api/v3",
    "vision_api_key": "",
    "vision_model": "doubao-1-5-vision-pro-32k-250115",
    "vision_prompt": (
        "客观描述这张图片的内容，重点提取与产品咨询/故障/需求相关的信息，"
        "30字以内，不要评论图片质量。"
    ),
    # ---- Agent 设定（获客留资）----
    "merchant_name": "",         # 商家名（注入 prompt）
    "strict_level": "rag",       # kb_only / rag / free
    "system_prompt": "",         # 获客 prompt 模板（空 = 用内置默认）
    "lead_confirm": "收到～稍后这边联系你哈",   # 留资成功后的确认话术
    "max_lead_ask": 2,           # 单会话最多主动索要联系方式次数
    # ---- 知识库/兜底 ----
    "knowledge_first": True,
    "min_delay": 8,
    "max_delay": 20,
    "max_history": 10,
    "fallback_pool": [
        "嗯嗯稍等哈，我问下马上回你",
        "这个我得确认一下哈，稍等",
        "收到，我问好了发你",
        "稍等哈，我看看",
    ],
    "fallback_image": "图我看到了哈，稍等我看看再回你",
    "forbidden_words": [
        "微信", "vx", "VX", "weixin", "加我",  # 站外引流敏感词
        "保证", "肯定", "一定", "承诺",          # 承诺类
        "作为一个AI", "作为一个ai", "抱歉", "亲~",
    ],
    "max_reply_len": 60,         # 超长截第一句
}

_KV_CONFIG = "ai_reply_config"
_KV_KB = "ai_reply_knowledge_base"
_KV_BL = "ai_reply_blacklist"
_KV_MARKER = "ai_reply_last_msg_id"
_KV_ASK_COUNT = "ai_reply_lead_ask"      # {account:conv_id: count}


def get_config() -> dict:
    cfg = dict(_DEFAULT_CONFIG)
    saved = _kv_get(_KV_CONFIG, {})
    if isinstance(saved, dict):
        cfg.update(saved)
    return cfg


def save_config(cfg: dict) -> dict:
    merged = get_config()
    for k in _DEFAULT_CONFIG:
        if k in cfg:
            merged[k] = cfg[k]
    _kv_set(_KV_CONFIG, merged)
    return merged


# ---------------------------------------------------------------------------
# 内置获客 prompt（用户可覆盖：配置里 system_prompt 非空则优先）
# ---------------------------------------------------------------------------

_DEFAULT_AGENT_PROMPT = """你是「{merchant}」的抖音私信客服。唯一目标：解答客户问题的同时，让有意向的客户留下手机号。

工作方式（分阶段推进）：
1. 解答阶段：只依据《资料》回答，专业、简短、口语化，先建立信任。
2. 意向阶段：客户表现出兴趣（问价格/效果/怎么办理）→ 自然推进留资。
3. 留资阶段：用自然话术索要手机号，例如："这边给你申请个专属优惠，你手机号多少？我备注一下"。已被拒绝 {max_ask} 次就不再索要，安心解答。
4. 留资成功（客户已发号码）：只回确认话术，不再多说。

铁律：
- 《资料》里没有的信息一律不许编；不确定就回"嗯嗯这个我问下稍等哈"。
- 绝不说"加微信"、绝不提其他平台；只索要手机号。
- 回复 5-30 字，口语化像真人，一次只回一句，不排队比句式。
- 不出现价格数字承诺、不承诺时间效果。"""

_RAG_SUFFIX = """

《资料》（你唯一的事实来源，全部来自知识库）：
{kb}

客户消息：{text}"""


def build_system_prompt(cfg: dict, kb_items: list, text: str) -> str:
    merchant = cfg.get("merchant_name") or "本店"
    base = (cfg.get("system_prompt") or "").strip() or _DEFAULT_AGENT_PROMPT
    base = (base.replace("{merchant}", merchant)
                .replace("{max_ask}", str(cfg.get("max_lead_ask", 2))))
    if cfg.get("strict_level") == "rag":
        lines = []
        for it in kb_items[:40]:
            lines.append(f"- 问：{it.get('question','')} → 答：{it.get('answer','')}")
        kb_text = "\n".join(lines) if lines else "（知识库暂无条目）"
        base += _RAG_SUFFIX.format(kb=kb_text, text=text)
    return base


# ---------------------------------------------------------------------------
# KnowledgeBase —— 移植自原项目（存储改 kv_store）
# ---------------------------------------------------------------------------

class KnowledgeBase:
    """知识库：问答对，关键词模糊匹配，命中直接回复不耗 AI 额度。"""

    def __init__(self):
        self.lock = threading.Lock()

    def _load(self) -> list:
        return _kv_get(_KV_KB, [])

    def _save(self, items: list) -> None:
        _kv_set(_KV_KB, items)

    def list_items(self) -> list:
        with self.lock:
            return self._load()

    def add(self, question: str, answer: str) -> dict:
        q, a = (question or "").strip(), (answer or "").strip()
        if not q or not a:
            raise ValueError("问题和答案都不能为空")
        with self.lock:
            items = self._load()
            item = {"id": int(time.time() * 1000), "question": q, "answer": a}
            items.append(item)
            self._save(items)
            return item

    def update(self, item_id: int, question: str, answer: str) -> None:
        q, a = (question or "").strip(), (answer or "").strip()
        if not q or not a:
            raise ValueError("问题和答案都不能为空")
        with self.lock:
            items = self._load()
            for it in items:
                if it.get("id") == item_id:
                    it["question"], it["answer"] = q, a
                    break
            self._save(items)

    def delete(self, item_id: int) -> None:
        with self.lock:
            items = self._load()
            self._save([it for it in items if it.get("id") != item_id])

    def find_match(self, question: str, threshold: float = 0.7) -> Optional[str]:
        qtext = (question or "").lower().strip()
        if not qtext:
            return None
        for item in self._load():
            q = (item.get("question") or "").lower().strip()
            if not q:
                continue
            if q in qtext or qtext in q:
                return item.get("answer", "")
            qw, uw = set(q), set(qtext)
            union = qw | uw
            overlap = (len(qw & uw) / len(union)) if union else 0
            if overlap >= threshold:
                return item.get("answer", "")
        return None


KB = KnowledgeBase()


# ---------------------------------------------------------------------------
# AIClient —— 主 LLM（Anthropic 兼容）+ 视觉模型（OpenAI 兼容）
# ---------------------------------------------------------------------------

class AIClient:
    def __init__(self, cfg: dict):
        self.cfg = cfg
        self.session_history: dict[str, list] = {}
        self.session_lock = threading.Lock()

    def chat(self, message: str, user_id: str = "default",
             system_prompt: str = "", history_extra: Optional[list] = None
             ) -> Optional[str]:
        cfg = self.cfg
        if not cfg.get("api_key"):
            return None
        with self.session_lock:
            history = list(self.session_history.get(user_id, []))
        if history_extra:
            history = history + list(history_extra)
        messages: list = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(history[-int(cfg.get("max_history", 10)):])
        messages.append({"role": "user", "content": message})
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.get('api_key', '')}",
            "anthropic-version": "2023-06-01",
        }
        data = {
            "model": cfg.get("model", ""),
            "messages": messages,
            "max_tokens": int(cfg.get("max_tokens", 1000)),
            "temperature": float(cfg.get("temperature", 0.7)),
        }
        try:
            resp = requests.post(
                f"{str(cfg.get('base_url', '')).rstrip('/')}/v1/messages",
                headers=headers, json=data, timeout=30,
            )
            if resp.status_code != 200:
                logger.warning(f"[ai] AI API {resp.status_code}: {resp.text[:200]}")
                return None
            result = resp.json()
        except Exception as e:
            logger.warning(f"[ai] AI 请求失败: {e}")
            return None
        reply = ""
        content = result.get("content", [])
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "text":
                    reply = (item.get("text") or "").strip()
                    break
        if not reply:
            logger.warning(f"[ai] AI 返回为空: {str(result)[:200]}")
            return None
        with self.session_lock:
            h = self.session_history.setdefault(user_id, [])
            h.append({"role": "user", "content": message})
            h.append({"role": "assistant", "content": reply})
            del h[:-40]
        return reply

    # -- 视觉模型（独立配置，OpenAI 兼容 /chat/completions）---------------

    def describe_image(self, image_b64: str, mime: str = "jpeg") -> Optional[str]:
        cfg = self.cfg
        if not cfg.get("vision_api_key") or not cfg.get("vision_base_url"):
            return None
        data_url = f"data:image/{mime};base64,{image_b64}"
        try:
            resp = requests.post(
                f"{str(cfg['vision_base_url']).rstrip('/')}/chat/completions",
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {cfg['vision_api_key']}",
                },
                json={
                    "model": cfg.get("vision_model", ""),
                    "messages": [{
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": data_url}},
                            {"type": "text", "text": cfg.get("vision_prompt", "描述图片")},
                        ],
                    }],
                    "max_tokens": 200,
                },
                timeout=45,
            )
            if resp.status_code != 200:
                logger.warning(f"[ai] 视觉 API {resp.status_code}: {resp.text[:200]}")
                return None
            r = resp.json()
            return ((r.get("choices") or [{}])[0].get("message") or {}).get(
                "content", "").strip() or None
        except Exception as e:
            logger.warning(f"[ai] 视觉请求失败: {e}")
            return None

    def test_connection(self) -> tuple[bool, str]:
        reply = self.chat("你好", user_id="__api_test__",
                          system_prompt="请用一句话回复")
        if reply:
            return True, reply[:80]
        return False, "AI 连接失败：请检查 API Key / Base URL / 模型名"


# ---------------------------------------------------------------------------
# 留资提取（纯本地正则，零网络请求）
# ---------------------------------------------------------------------------

_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
# 微信号：宽松匹配 wx/wxid/v_/weixin 开头 6-20 位字母数字下划线（用户主动发才算）
_WECHAT_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:wx|weixin|wxid|v_)[A-Za-z0-9_-]{5,19}(?![A-Za-z0-9_-])",
    re.IGNORECASE)


def extract_contacts(text: str) -> list[tuple[str, str]]:
    """从客户消息提取 (contact_type, value)。只认用户主动发出的。"""
    out: list[tuple[str, str]] = []
    if not text:
        return out
    for m in _PHONE_RE.findall(text):
        out.append(("phone", m))
    for m in _WECHAT_RE.findall(text):
        out.append(("wechat", m))
    # 去重保序
    seen, uniq = set(), []
    for t, v in out:
        if (t, v) not in seen:
            seen.add((t, v))
            uniq.append((t, v))
    return uniq


def save_lead(account: str, conv_id: str, peer_name: str,
              ctype: str, cvalue: str, source_text: str) -> bool:
    """写线索表。同会话同联系方式只写一次（UNIQUE 约束兜底）。"""
    try:
        conn = database.get_db()
        cur = conn.execute(
            "INSERT OR IGNORE INTO ai_leads(account, conv_id, peer_name, "
            "contact_type, contact_value, source_text, status, created_at) "
            "VALUES(?,?,?,?,?,?, 'new', ?)",
            (account, conv_id, peer_name or "", ctype, cvalue,
             (source_text or "")[:200], time.time()),
        )
        conn.commit()
        return cur.rowcount > 0
    except Exception as e:
        logger.warning(f"[ai] 线索写入失败: {e}")
        return False


# ---------------------------------------------------------------------------
# 护栏：AI 出口文本校验
# ---------------------------------------------------------------------------

def validate_reply(reply: str, cfg: dict) -> Optional[str]:
    """校验 AI 文本；不通过返回 None（调用方改发兜底话术）。"""
    if not reply or not reply.strip():
        return None
    reply = reply.strip().strip('"“”')
    low = reply.lower()
    for w in cfg.get("forbidden_words", []):
        if w and (w.lower() in low or w in reply):
            logger.info(f"[ai] 护栏拦截（命中违禁词「{w}」）: {reply[:40]}")
            return None
    max_len = int(cfg.get("max_reply_len", 60))
    if len(reply) > max_len:
        # 截第一句（句号/问号/感叹号/换行处）
        m = re.split(r"[。？！?!~\n]", reply)
        first = next((s for s in m if s.strip()), "")
        reply = first.strip() or reply[:max_len]
    return reply


def fallback_reply(cfg: dict, kind: str = "text") -> str:
    pool = cfg.get("fallback_pool", [])
    if kind == "image":
        return cfg.get("fallback_image", "图我看到了哈，稍等我看看再回你")
    return random.choice(pool) if pool else "稍等哈，我看下"


# ---------------------------------------------------------------------------
# 黑名单
# ---------------------------------------------------------------------------

def blacklist_list() -> list:
    return _kv_get(_KV_BL, [])


def blacklist_add(user_id: str) -> None:
    bl = blacklist_list()
    if user_id and user_id not in bl:
        bl.append(user_id)
        _kv_set(_KV_BL, bl)


def blacklist_remove(user_id: str) -> None:
    bl = blacklist_list()
    if user_id in bl:
        bl.remove(user_id)
        _kv_set(_KV_BL, bl)


# ---------------------------------------------------------------------------
# 自动回复监听器
# ---------------------------------------------------------------------------

class AutoReplyWorker:
    """全自动监听：轮询 dm_messages 新入库的 role='them' 消息 →
    留资提取 / 知识库 / AI（RAG）→ 复用 /api/messages/send 发送。

    铁律对齐：
    - 纯读 SQLite，零捕获、零昵称查询；
    - 发送走既有双通道（recv_daemon /send 主 + BCC /wp_send 兜底）；
    - 同一会话同一消息只回一次；同一联系方式只写一条线索。
    """

    POLL_INTERVAL = 5.0
    HISTORY_LIMIT = 6

    def __init__(self):
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self.status = {
            "running": False,
            "last_tick": 0.0,
            "processed": 0,
            "replied": 0,
            "leads": 0,
            "errors": 0,
            "last_reply": "",
        }

    def start(self):
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="ai-autoreply")
        self._thread.start()
        self.status["running"] = True
        logger.info("[ai] 获客自动回复监听已启动（全自动模式）")

    def stop(self):
        self._stop.set()
        self.status["running"] = False
        logger.info("[ai] 获客自动回复监听已停止")

    def _run(self):
        # 启动时刻打水位：只回开启之后的新消息，绝不回历史
        conn = database.get_db()
        row = conn.execute("SELECT COALESCE(MAX(id),0) FROM dm_messages").fetchone()
        if int(_kv_get(_KV_MARKER, 0) or 0) == 0:
            _kv_set(_KV_MARKER, row[0] if row else 0)
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as e:
                self.status["errors"] += 1
                logger.warning(f"[ai] 监听 tick 异常: {e}")
            self._stop.wait(self.POLL_INTERVAL)

    def _tick(self):
        cfg = get_config()
        conn = database.get_db()
        row = conn.execute("SELECT COALESCE(MAX(id),0) FROM dm_messages").fetchone()
        max_id = row[0] if row else 0
        if not cfg.get("enabled"):
            _kv_set(_KV_MARKER, max_id)  # 关闭时推进水位
            self.status["last_tick"] = time.time()
            return
        last_id = int(_kv_get(_KV_MARKER, 0) or 0)
        rows = conn.execute(
            "SELECT m.id, m.account, m.conv_id, m.role, m.text, m.msg_type, "
            "       m.extra, c.peer_id, c.peer_name "
            "FROM dm_messages m LEFT JOIN dm_conversations c "
            "  ON c.account=m.account AND c.conv_id=m.conv_id "
            "WHERE m.id>? AND m.role='them' "
            "  AND TRIM(COALESCE(m.text,''))<>'' "
            "  AND m.text NOT LIKE '[对方已读%' "
            "ORDER BY m.id ASC LIMIT 20",
            (last_id,),
        ).fetchall()
        for r in rows:
            last_id = max(last_id, r["id"])
            _kv_set(_KV_MARKER, last_id)
            try:
                self._handle(r, cfg)
                self.status["processed"] += 1
            except Exception as e:
                self.status["errors"] += 1
                logger.warning(f"[ai] 单条处理异常: {e}")
        self.status["last_tick"] = time.time()

    # -- 单条处理 ----------------------------------------------------------

    def _handle(self, row, cfg: dict):
        account = row["account"]
        conv_id = row["conv_id"]
        text = (row["text"] or "").strip()
        peer_name = row["peer_name"] or row["peer_id"] or "对方"
        key = f"{account}:{conv_id}"

        bl = set(blacklist_list())
        if (row["peer_id"] and row["peer_id"] in bl) or peer_name in bl:
            return

        # 会话维度防重：同会话同文本 60s 内只处理一次（WS+WP 双通道落库去重）
        with self._lock:
            recent_key = f"ai_reply_recent_{key}"
            recent = _kv_get(recent_key, {"last_at": 0, "last_text": ""})
            now = time.time()
            if now - float(recent.get("last_at", 0)) < 60 and \
                    recent.get("last_text") == text:
                return
            self._mark_recent(recent_key, text, now)

        # ---- 0) 留资提取（最高优先级：客户主动留的号原样入库，不过护栏）----
        contacts = extract_contacts(text)
        if contacts:
            n_new = 0
            for ctype, cvalue in contacts:
                if save_lead(account, conv_id, peer_name, ctype, cvalue, text):
                    n_new += 1
                    self.status["leads"] += 1
            if n_new:
                logger.info(f"[ai] 🎯 留资成功 {peer_name}: "
                            f"{[v for _, v in contacts]} → ai_leads")
                # 留资成功 → 回确认话术（不调 AI）
                self._send_delayed(account, conv_id,
                                   cfg.get("lead_confirm", "收到～稍后这边联系你哈"),
                                   cfg, source="留资确认")
                return

        # ---- 1) 图片消息（msg_type='27' 实库证真；text 以 [图片] 开头）----
        is_image = text.startswith("[图片]") or str(row["msg_type"]) == "27"
        vision_text = None
        if is_image:
            vision_text = self._describe_image(account, row, cfg)
            if vision_text is None:
                # 视觉未配置/失败 → 固定兜底，绝不瞎猜
                self._send_delayed(account, conv_id,
                                   fallback_reply(cfg, "image"), cfg,
                                   source="图片兜底")
                return
            # 视觉有描述 → 把描述当作客户消息内容继续走文本流程
            text = f"[客户发来图片] {vision_text}"

        # ---- 2) 生成回复（档位分流）----
        reply, source = self._generate_reply(cfg, text, account, conv_id, row["id"])
        if not reply:
            logger.info(f"[ai] 未生成回复（档位={cfg.get('strict_level')}）: "
                        f"{peer_name}: {text[:40]}")
            return
        self._send_delayed(account, conv_id, reply, cfg, source=source)

    # -- 回复生成（档位 + 护栏 + 兜底）-------------------------------------

    def _generate_reply(self, cfg: dict, text: str, account: str,
                        conv_id: str, before_id: int) -> tuple[Optional[str], str]:
        level = cfg.get("strict_level", "rag")
        kb_hit = KB.find_match(text) if cfg.get("knowledge_first", True) else None
        if kb_hit:
            return kb_hit, "知识库"

        if level == "kb_only":
            # 严格档：AI 完全不参与 → 兜底话术池
            return fallback_reply(cfg), "兜底"

        # rag / free：调 AI
        client = AIClient(cfg)
        kb_items = KB.list_items()
        prompt = build_system_prompt(cfg, kb_items, text)
        history = self._build_history(account, conv_id, before_id)
        raw = client.chat(text, user_id=f"{account}:{conv_id}",
                          system_prompt=prompt, history_extra=history)
        if raw:
            cleaned = validate_reply(raw, cfg)
            if cleaned:
                return cleaned, "AI"
            logger.info(f"[ai] AI 输出被护栏拦截，改发兜底: {raw[:40]}")
        return fallback_reply(cfg), "兜底"

    # -- 图片 → 视觉模型 ---------------------------------------------------

    def _describe_image(self, account: str, row, cfg: dict) -> Optional[str]:
        """解密图片 → base64 → 视觉模型描述。失败返回 None（不瞎猜）。"""
        if not cfg.get("vision_enabled"):
            return None
        try:
            extra = json.loads(row["extra"] or "{}")
        except Exception:
            extra = {}
        skey = extra.get("skey") or ""
        origin_url = extra.get("origin_url") or ""
        # 老数据 extra 为空时，从 text 里的 URL 兜底（skey 缺失则解不了）
        if not origin_url:
            m = re.match(r"\[图片\]\s+(\S+)", row["text"] or "")
            if m:
                origin_url = m.group(1)
        if not skey or not origin_url:
            logger.info("[ai] 图片消息缺 skey/origin_url，无法解密")
            return None
        try:
            from auto_dm import origin_image_resolver
            res = origin_image_resolver.resolve(
                account=account, msg_id=str(row["id"]), skey=skey,
                origin_url=origin_url)
        except Exception as e:
            logger.warning(f"[ai] 图片解密失败: {e}")
            return None
        if not res.get("ok"):
            logger.warning(f"[ai] 图片解密未成功: {res.get('error')}")
            return None
        # 本地文件 → base64（kind=local / inline_base64 都可能）
        img_bytes: Optional[bytes] = None
        mime = "jpeg"
        if res.get("kind") == "local":
            fname = str(res.get("url", "")).rsplit("/", 1)[-1]
            fpath = Path(origin_image_resolver._origin_cache_dir()) / fname
            if fpath.exists():
                img_bytes = fpath.read_bytes()
                mime = res.get("format") or "jpeg"
                if mime == "heic":
                    mime = "jpeg"  # HEIC 已在 resolve 里转码
        elif res.get("kind") == "inline_base64":
            try:
                data_url = res.get("url", "")
                head, b64 = data_url.split(",", 1)
                img_bytes = base64.b64decode(b64)
                mime = "jpeg" if "png" not in head else "png"
            except Exception as e:
                logger.warning(f"[ai] inline base64 解码失败: {e}")
        if not img_bytes:
            return None
        client = AIClient(cfg)
        return client.describe_image(
            base64.b64encode(img_bytes).decode(), mime)

    # -- 上下文 / 发送 -----------------------------------------------------

    def _build_history(self, account: str, conv_id: str, before_id: int) -> list:
        try:
            rows = database.get_db().execute(
                "SELECT role, text FROM dm_messages "
                "WHERE account=? AND conv_id=? AND id<? AND msg_type='text' "
                "  AND TRIM(COALESCE(text,''))<>'' "
                "ORDER BY id DESC LIMIT ?",
                (account, conv_id, before_id, int(self.HISTORY_LIMIT)),
            ).fetchall()
            return [{"role": "assistant" if r["role"] == "me" else "user",
                     "content": r["text"]}
                    for r in reversed(rows)]
        except Exception as e:
            logger.debug(f"[ai] 组装上下文失败: {e}")
            return []

    def _send_delayed(self, account: str, conv_id: str, text: str,
                      cfg: dict, source: str = ""):
        """随机延迟（模拟真人）→ 复用 /send 双通道发送。延迟期间放锁。"""
        lo = float(cfg.get("min_delay", 8))
        hi = float(cfg.get("max_delay", 20))
        delay = random.uniform(min(lo, hi), max(lo, hi))
        logger.info(f"[ai] {source} → {conv_id[:12]}…: {text[:30]}… "
                    f"延迟 {delay:.1f}s")
        # 随机延迟期间不持锁（self._lock 只保护 recent 防重段），
        # 等待中若 stop 被置位则放弃发送
        self._stop.wait(delay)
        if self._stop.is_set():
            return
        if self._send_via_chain(account, conv_id, text):
            self.status["replied"] += 1
            self.status["last_reply"] = f"{source}: {text[:40]}"

    def _send_via_chain(self, account: str, conv_id: str, text: str) -> bool:
        """复用 /api/messages/send 双通道（与前端手动发送同一链路）。"""
        try:
            from api.messages import _recv_url, _bcc_url, _http_post_json
        except Exception as e:
            logger.warning(f"[ai] 无法复用发送链路: {e}")
            return False
        try:
            d = _http_post_json(_recv_url(account, "/send"), {
                "account": account, "conv_id": conv_id, "text": text,
            }, timeout=15.0)
            if d and d.get("ok"):
                logger.info(f"[ai] 已发送 (WS): {text[:30]}")
                return True
        except Exception as e:
            logger.warning(f"[ai] WS 通道失败: {e}")
        try:
            d = _http_post_json(_bcc_url(account, "/wp_send"), {
                "account": account, "conv_id": conv_id, "text": text,
            }, timeout=30.0)
            if d and d.get("ok"):
                logger.info(f"[ai] 已发送 (WP): {text[:30]}")
                return True
            logger.warning(f"[ai] WP 通道失败: {(d or {}).get('error')}")
        except Exception as e:
            logger.warning(f"[ai] WP 通道异常: {e}")
        return False

    def _mark_recent(self, key: str, text: str, now: float) -> None:
        _kv_set(key, {"last_at": now, "last_text": text})


WORKER = AutoReplyWorker()


# ---------------------------------------------------------------------------
# 线索查询 / 导出（API 层用）
# ---------------------------------------------------------------------------

def list_leads(limit: int = 200) -> list:
    rows = database.get_db().execute(
        "SELECT * FROM ai_leads ORDER BY created_at DESC LIMIT ?",
        (int(limit),),
    ).fetchall()
    return [dict(r) for r in rows]


def set_lead_status(lead_id: int, status: str) -> None:
    if status not in ("new", "followed", "invalid"):
        raise ValueError("非法状态")
    conn = database.get_db()
    conn.execute("UPDATE ai_leads SET status=? WHERE id=?", (status, lead_id))
    conn.commit()
