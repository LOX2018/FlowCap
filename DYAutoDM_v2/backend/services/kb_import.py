# -*- coding: utf-8 -*-
"""AI 知识库文件导入（2026-09-06）。

支持格式：
- .txt / .md        直接文本
- .docx             python-docx（段落+表格）
- .xlsx/.xls        openpyxl（每行按"列名: 值"拼接）
- .pdf              pymupdf（文本层；扫描件无文本层时报错提示）
- .png/.jpg/.jpeg   视觉模型 OCR（vision_api_key 必须已配置）

QA 生成策略：
- 主路线：调主 LLM，把文档内容按 chunk 生成问答对（含商家业务语境）。
- 降级路线：未配 API Key 时，按结构规则机械切分（txt/md 按行、
  xlsx 行即 QA、docx 段落句子拆分），生成"原文片段→原文片段"的弱 QA，
  保证无 Key 也能导入（用户可后续手动编辑）。

上限保护：单文件 20MB；chunk 3000 字；每文件最多生成 60 条 QA。
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

MAX_FILE_MB = 20
CHUNK_CHARS = 3000
MAX_QA_PER_FILE = 60

SUPPORTED_EXT = {".txt", ".md", ".docx", ".xlsx", ".xls", ".pdf",
                 ".png", ".jpg", ".jpeg", ".webp"}


class ImportError_(Exception):
    pass


# ---------------------------------------------------------------------------
# 各格式 → 纯文本
# ---------------------------------------------------------------------------

def _read_txt(path: Path) -> str:
    for enc in ("utf-8", "gbk", "utf-16"):
        try:
            return path.read_text(encoding=enc)
        except (UnicodeDecodeError, UnicodeError):
            continue
    raise ImportError_("无法识别文件编码（试过 utf-8/gbk/utf-16）")


def _read_docx(path: Path) -> str:
    try:
        import docx
    except ImportError:
        raise ImportError_("服务器缺少 python-docx，无法解析 Word 文件")
    try:
        d = docx.Document(str(path))
    except Exception as e:
        raise ImportError_(f"Word 文件解析失败: {e}")
    parts: list[str] = []
    for p in d.paragraphs:
        t = (p.text or "").strip()
        if t:
            parts.append(t)
    for tbl in d.tables:
        for row in tbl.rows:
            cells = [(c.text or "").strip() for c in row.cells]
            if any(cells):
                parts.append(" | ".join(cells))
    return "\n".join(parts)


def _read_xlsx(path: Path) -> str:
    try:
        import openpyxl
    except ImportError:
        raise ImportError_("服务器缺少 openpyxl，无法解析 Excel 文件")
    try:
        wb = openpyxl.load_workbook(str(path), data_only=True, read_only=True)
    except Exception as e:
        raise ImportError_(f"Excel 解析失败: {e}")
    lines: list[str] = []
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        header = [str(c).strip() if c is not None else "" for c in rows[0]]
        lines.append(f"【工作表 {ws.title}】")
        for r in rows[1:]:
            cells = ["" if c is None else str(c).strip() for c in r]
            if not any(cells):
                continue
            kv = [f"{header[i]}: {cells[i]}" for i in range(min(len(header), len(cells)))
                  if header[i] and cells[i]]
            lines.append("; ".join(kv) if kv else " | ".join(cells))
    return "\n".join(lines)


def _read_pdf(path: Path) -> str:
    try:
        import pymupdf
    except ImportError:
        raise ImportError_("服务器缺少 pymupdf，无法解析 PDF")
    try:
        doc = pymupdf.open(str(path))
    except Exception as e:
        raise ImportError_(f"PDF 打开失败: {e}")
    parts: list[str] = []
    for page in doc:
        t = page.get_text("text").strip()
        if t:
            parts.append(t)
    doc.close()
    text = "\n".join(parts).strip()
    if not text:
        raise ImportError_(
            "PDF 没有可提取的文本层（可能是扫描件）。请改用图片+视觉模型，或手动录入。")
    return text


def _read_image(path: Path, cfg: dict) -> str:
    """图片 → 视觉模型 OCR。必须已配置视觉模型。"""
    if not cfg.get("vision_api_key") or not cfg.get("vision_base_url"):
        raise ImportError_(
            "图片导入需要先在「模型配置」里启用并配置视觉模型（Base URL/Key/模型名）")
    from services.ai_reply import AIClient
    data = path.read_bytes()
    mime = "png" if path.suffix.lower() == ".png" else "jpeg"
    client = AIClient(cfg)
    # OCR 用更宽松的提示词（覆盖导入场景，不走聊天用的 vision_prompt）
    ocr_prompt = ("把图片中的所有文字完整转录出来，保持原有行序，"
                  "不要添加任何评论或解释。")
    # describe_image 用的是 cfg.vision_prompt，这里直接内联调 /chat/completions
    import requests as _rq
    data_url = f"data:image/{mime};base64," + __import__("base64").b64encode(data).decode()
    try:
        resp = _rq.post(
            f"{str(cfg['vision_base_url']).rstrip('/')}/chat/completions",
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {cfg['vision_api_key']}"},
            json={
                "model": cfg.get("vision_model", ""),
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_url}},
                        {"type": "text", "text": ocr_prompt},
                    ],
                }],
                "max_tokens": 2000,
            },
            timeout=60,
        )
    except Exception as e:
        raise ImportError_(f"视觉模型请求失败: {e}")
    if resp.status_code != 200:
        raise ImportError_(f"视觉模型返回 {resp.status_code}: {resp.text[:150]}")
    try:
        r = resp.json()
        text = ((r.get("choices") or [{}])[0].get("message") or {}).get("content", "").strip()
    except Exception:
        text = ""
    if not text:
        raise ImportError_("视觉模型未返回文字内容")
    return text


def extract_text(path: Path, cfg: dict) -> str:
    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXT:
        raise ImportError_(f"不支持的格式 {ext}（支持: "
                           f"txt/md/docx/xlsx/pdf/图片）")
    if path.stat().st_size > MAX_FILE_MB * 1024 * 1024:
        raise ImportError_(f"文件超过 {MAX_FILE_MB}MB 上限")
    if ext in (".txt", ".md"):
        return _read_txt(path)
    if ext == ".docx":
        return _read_docx(path)
    if ext in (".xlsx", ".xls"):
        return _read_xlsx(path)
    if ext == ".pdf":
        return _read_pdf(path)
    return _read_image(path, cfg)


# ---------------------------------------------------------------------------
# 文本 → chunks
# ---------------------------------------------------------------------------

def make_chunks(text: str, size: int = CHUNK_CHARS) -> list[str]:
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) <= size:
        return [text] if text else []
    # 优先按空行段落聚合
    paras = text.split("\n\n")
    chunks, buf = [], ""
    for p in paras:
        if len(buf) + len(p) + 2 <= size:
            buf = f"{buf}\n\n{p}" if buf else p
        else:
            if buf:
                chunks.append(buf)
            # 单段超长再硬切
            while len(p) > size:
                chunks.append(p[:size])
                p = p[size:]
            buf = p
    if buf:
        chunks.append(buf)
    return chunks[:20]  # chunk 数上限（防止巨型文档烧 token）


# ---------------------------------------------------------------------------
# chunks → QA 对
# ---------------------------------------------------------------------------

_JSON_RE = re.compile(r"\[.*\]", re.DOTALL)


def _parse_qa_json(raw: str) -> list[dict]:
    """容错解析 LLM 返回的 QA JSON 数组。"""
    m = _JSON_RE.search(raw)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
    except Exception:
        return []
    out = []
    for it in arr if isinstance(arr, list) else []:
        q = (it.get("question") or "").strip()
        a = (it.get("answer") or "").strip()
        if q and a:
            out.append({"question": q, "answer": a})
    return out


def generate_qa_with_ai(cfg: dict, chunk: str, merchant: str) -> list[dict]:
    """调主 LLM 把 chunk 转成 QA 对。失败返回 []（调用方降级）。"""
    from services.ai_reply import AIClient
    prompt = f"""你是「{merchant or '本店'}」的抖音私信客服知识库管理员。把下面的资料整理成问答对，用于自动回复客户。

要求：
- question 用客户真实的口语问法（如"多少钱""怎么办理""能退货吗"）
- answer 直接可发给客户的口语化回复，5-40 字，专业但像真人
- 一条资料可以拆成多个问答对；无关紧要的内容（目录、页码、落款）跳过
- 最多 10 条，输出 JSON 数组: [{{"question":"...","answer":"..."}}]

资料：
{chunk}"""
    client = AIClient(cfg)
    raw = client.chat(prompt, user_id="__kb_import__",
                      system_prompt="只输出 JSON 数组，不要其他内容。")
    if not raw:
        return []
    qa = _parse_qa_json(raw)
    return qa[:10]


def generate_qa_fallback(chunk: str) -> list[dict]:
    """无 Key 降级：机械切分原文生成弱 QA（原文→原文），用户可后续编辑。"""
    qa = []
    # xlsx 式 "问题: 答案" 行；同时支持无表头的 QA 表（两列拼接行拆出第 2 列后）
    for line in chunk.splitlines():
        s = line.strip()
        if s.startswith("【") or s.startswith("问题:") or s.startswith("问题："):
            continue
        # xlsx 两列表被拼成 "问题: X; 答案: Y" —— 拆出真 QA
        m2 = re.match(r"^问题[:：]\s*(.+?);\s*答案[:：]\s*(.+)$", s)
        if m2:
            qa.append({"question": m2.group(1).strip(),
                       "answer": m2.group(2).strip()})
            if len(qa) >= 10:
                return qa
            continue
        m = re.match(r"^(.{2,30}?)[:：]\s*(.{2,80})$", s)
        if m:
            qa.append({"question": m.group(1).strip(),
                       "answer": m.group(2).strip()})
            if len(qa) >= 10:
                return qa
    if qa:
        return qa
    # 纯段落：首句当问题、整段当答案（弱，但可用）
    sentences = re.split(r"[。！？\n]", chunk)
    first = next((s.strip() for s in sentences if len(s.strip()) >= 6), "")
    if first:
        qa.append({"question": first[:30], "answer": chunk[:120].replace("\n", " ")})
    return qa


# ---------------------------------------------------------------------------
# 主入口：文件 → QA 列表（不直接入库，返回给 API 层确认后写入）
# ---------------------------------------------------------------------------

def import_file(path: Path, cfg: dict,
                progress: Optional[Callable[[str], None]] = None) -> dict:
    """解析文件并生成 QA 对。返回 {items, stats}。"""
    def _p(msg: str):
        if progress:
            try:
                progress(msg)
            except Exception:
                pass
        logger.info(f"[ai-kb-import] {msg}")

    text = extract_text(path, cfg)
    _p(f"文本提取完成: {len(text)} 字")
    chunks = make_chunks(text)
    _p(f"切分 {len(chunks)} 个片段，开始生成问答对")
    has_key = bool(cfg.get("api_key"))
    items: list[dict] = []
    for i, chunk in enumerate(chunks):
        if len(items) >= MAX_QA_PER_FILE:
            break
        qa = generate_qa_with_ai(cfg, chunk, cfg.get("merchant_name", "")) \
            if has_key else []
        if not qa:
            qa = generate_qa_fallback(chunk)
        for it in qa:
            it["source"] = path.name
        items.extend(qa)
        _p(f"片段 {i + 1}/{len(chunks)} → 累计 {len(items)} 条")
        if len(items) >= MAX_QA_PER_FILE:
            items = items[:MAX_QA_PER_FILE]
            _p(f"达到单文件上限 {MAX_QA_PER_FILE} 条，截断")
    return {"items": items, "chars": len(text), "chunks": len(chunks),
            "mode": "ai" if has_key else "fallback"}
