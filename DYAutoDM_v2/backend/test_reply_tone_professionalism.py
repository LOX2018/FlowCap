# coding=utf-8
"""话术专业度门禁（2026-09-29 · 用户拍板「话术一定要专业」）。

## 要防住的缺陷（实测）

用户在直播监听会话里看到两条 AI 回复「确认一下」「问一下」，判为
「非常不专业，显得像一个小白」。取证发现**两层成因**：

1. **表层**：那两条根本不是 AI 生成的，而是 `fallback_pool` 的固定文案
   —— 而它恰在 **AI 被护栏拦下 / 模型不可用** 时发给真实客户，
   即「最需要专业度的时刻，客户收到最差话术」。
2. **深层（总根源）**：`_DEFAULT_AGENT_PROMPT` **主动教** AI
   「不确定就回"嗯嗯这个我问下稍等哈"」，并把长度限制为 `5-30 字`
   —— 专业解答根本装不下 ⇒ AI 输出短小 ⇒ 被 `min_reply_len` 判残句
   ⇒ 落到最不专业的兜底池。三层同源。

故本门禁守三条（任一条破即复发）：
  G1 兜底池不得出现「拖延型/口语填充型」话术；
  G2 默认 prompt 不得把拖延话术当**正确行为**教给模型，且不得限长到讲不清专业判断；
  G3 短回复必须有「先重试再兜底」的补救路径（否则客户只看得到兜底话术）。

判据一律读**源码与默认配置**，不依赖运行环境。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parent
AI_REPLY = BACKEND / "services" / "ai_reply.py"


def _src() -> str:
    return AI_REPLY.read_text(encoding="utf-8")


# 拖延型 / 口语填充型话术的特征词（出现在**话术内容**里即违规）
_LAZY_MARKERS = [
    "嗯嗯", "稍等哈", "我问下马上回你", "我得确认一下哈",
    "我问好了发你", "我看看", "哦哦", "收到收到",
]


def _default_config_node() -> ast.Assign:
    tree = ast.parse(_src())
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign):
            for t in n.targets:
                if isinstance(t, ast.Name) and t.id == "_DEFAULT_CONFIG":
                    return n
    raise AssertionError("找不到 _DEFAULT_CONFIG")


def _literal(node: ast.AST):
    try:
        return ast.literal_eval(node)
    except Exception:
        return None


# ===========================================================================
# G1 · 兜底池（default config 的 fallback_pool）
# ===========================================================================

def test_g1_fallback_pool_not_lazy():
    """G1（核心）：默认兜底池不得含拖延型/填充型话术。

    兜底池会在「AI 被护栏拦下 or 模型不可用」时直发客户 —— 是专业度的底线。
    """
    node = _default_config_node()
    cfg = None
    for k, v in zip(node.value.keys, node.value.values):
        kk = _literal(k)
        if kk == "_DEFAULT_CONFIG":
            cfg = _literal(v)
    assert cfg is None  # 顶层 dict，不是嵌套
    d = None
    for k, v in zip(node.value.keys, node.value.values):
        if _literal(k) == "fallback_pool":
            d = _literal(v)
    assert isinstance(d, list) and d, "fallback_pool 缺失或非列表"
    for item in d:
        hit = [m for m in _LAZY_MARKERS if m in str(item)]
        assert not hit, (
            f"兜底话术仍含拖延/填充词 {hit}: {item!r}\n"
            f"（兜底池是专业度底线，必须直接对接咨询意图、引导补充关键信息）")


def test_g1b_fallback_image_not_lazy():
    """G1b：图片兜底同样不得是拖延型。"""
    node = _default_config_node()
    d = None
    for k, v in zip(node.value.keys, node.value.values):
        if _literal(k) == "fallback_image":
            d = _literal(v)
    assert isinstance(d, str) and d, "fallback_image 缺失"
    hit = [m for m in _LAZY_MARKERS if m in d]
    assert not hit, f"图片兜底仍含拖延词 {hit}: {d!r}"


def test_g1c_fallback_pool_is_professional():
    """G1c：兜底话术必须**正向具备**专业要素（不只"没有坏词"）。

    要求至少各含一条「身份声明」或「引导补充关键信息」的表述 —— 防止
    有人改成另一批空洞但无害的话术来绕门禁。
    """
    node = _default_config_node()
    d = None
    for k, v in zip(node.value.keys, node.value.values):
        if _literal(k) == "fallback_pool":
            d = _literal(v)
    joined = " ".join(str(x) for x in d)
    assert ("顾问" in joined or "团队" in joined), "兜底话术未声明专业身份"
    assert any(w in joined for w in ("受伤部位", "诊断", "劳动合同", "社保",
                                     "住院", "城市")), \
        "兜底话术未引导客户补充可判定的关键信息"


# ===========================================================================
# G2 · 默认 prompt
# ===========================================================================

def _default_prompt_text() -> str:
    s = _src()
    m = re.search(r"_DEFAULT_AGENT_PROMPT\s*=\s*\"\"\"(.*?)\"\"\"", s, re.S)
    assert m, "找不到 _DEFAULT_AGENT_PROMPT"
    return m.group(1)


def test_g2_prompt_does_not_teach_lazy_reply():
    """G2（核心）：prompt 不得把拖延话术当**正确行为**教给模型。

    旧文：「《资料》里没有的信息一律不许编；**不确定就回"嗯嗯这个我问下稍等哈"**。」
    允许它作为**反例**出现（如「不得用…搪塞」），但不允许作为指令。
    """
    p = _default_prompt_text()
    assert not re.search(r"不确定就回[\"“]嗯嗯", p), (
        "prompt 仍在教模型「不确定就回拖延话术」（这正是『像小白』的总根源）")
    assert "不得" in p and "稍等" in p, \
        "prompt 未包含「禁止用稍等/我问下当回复内容」的明确指令"


def test_g2b_prompt_min_len_not_too_short():
    """G2b：prompt 的长度区间下限不得过小（专业判断讲不清）。

    旧值「5-30 字」：一个专业结论+依据普遍要 40 字以上 ⇒ 强逼模型说短话
    ⇒ 既像敷衍，又容易被 min_reply_len 判残句。
    """
    p = _default_prompt_text()
    m = re.search(r"(\d+)\s*-\s*(\d+)\s*字", p)
    assert m, "prompt 中未找到长度区间"
    lo, hi = int(m.group(1)), int(m.group(2))
    assert lo >= 15, f"prompt 长度下限 {lo} 过小（<15）⇒ 讲不清专业判断"
    assert hi >= 60, f"prompt 长度上限 {hi} 过小（<60）⇒ 专业解答装不下"


# ===========================================================================
# G3 · 短回复补救路径
# ===========================================================================

def test_g3_short_reply_retry_before_fallback():
    """G3（核心）：短回复必须先**重试**，再回落兜底。

    实测：AI 对简短输入常只吐「我在的」「你好」→ 被 min_reply_len 判残句
    → 直接换兜底 ⇒ 客户**从来看不到 AI 的专业输出**。
    必须在 `validate_reply` 返回 None 且原因为「过短」时追加一次带
    补救提示的重问（probe 记 RETRY/GUARD2）。
    """
    s = _src()
    assert '"[RETRY]"' not in s and '"RETRY"' in s, "未见 RETRY 探针"
    assert "_too_short" in s, "未见短回复判定变量"
    assert "短回复重试成功" in s, "未见重试成功日志"
    assert "GUARD2" in s, "未见二次护栏探针（无法观测补救是否生效）"


def test_g3b_retry_failure_does_not_raise():
    """G3b：重试失败必须静默回落兜底（不得把发送链路打断）。"""
    s = _src()
    i = s.find("_too_short")
    assert i > 0
    blk = s[i: i + 1600]
    assert "except Exception" in blk, "重试未包异常保护 ⇒ 失败会打断发送链路"


# ===========================================================================
# G4 · 错误码唯一性（本轮踩到 AI-066 冲突）
# ===========================================================================

def test_g4_error_codes_unique_in_ai_reply():
    """G4：AI-066 只能属于「本机账号互回拦截」语义（本轮实测踩到冲突）。

    原 AI-066 曾用于「截断后残句」，与本轮新增的互回拦截撞码 ⇒ 同码两义、
    破坏可检索性。此判据防它再被挪作他用。
    """
    s = _src()
    # 取每个 AI-066 出现点后的**一段**（跨行），断言语义归属唯一
    for m in re.finditer(r"\[AI-066\]", s):
        blk = s[m.start(): m.start() + 400]
        assert ("另一个账号" in blk or "本机" in blk), (
            "AI-066 被用于非「本机账号互回」语义，错误码冲突复发: "
            + blk.split("\n")[0].strip()[:90])
    # 反向：截断护栏必须用 AI-068，不得退回 AI-066
    cut_i = s.find("截断后疑似残句")
    assert cut_i > 0, "找不到截断后残句护栏"
    assert "[AI-068]" in s[cut_i - 200: cut_i + 200], \
        "截断护栏未使用 AI-068（可能又退回 AI-066 与互回拦截撞码）"
