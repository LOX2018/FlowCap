# -*- coding: utf-8 -*-
"""聊天长图 → **PNG**（Pillow 原生绘制，2026-09-17 新增）。

## 为什么不是 BCC / 无头浏览器

用户明确要求「**不通过 BCC** 用别的方式转成 PNG」。三条可选路径：

| 方案 | 新增依赖 | 是否需浏览器 | 结论 |
|---|---|---|---|
| BCC 容器 `page.screenshot()` | 0 | ✅ 需 | ❌ 用户否决（且会引入浏览器调用 = 新运行时行为） |
| Playwright 独立无头 | 有（且要装浏览器） | ✅ 需 | ❌ 重、慢、又回到浏览器依赖 |
| **Pillow 原生绘制（本模块）** | **0**（`pillow` 已在 requirements） | ❌ **不需要** | ✅ **选用** |

优点：**纯离线、无浏览器/无 BCC、无网络**，确定性高，适合服务端直接出图与单测。
代价：排版由我们**自己测量与折行**（Pillow 无 CSS 布局），故本模块把
「测量 / 折行 / 分页 / 气泡定位」显式实现并单测覆盖。

## 契约

· **只读**：仅 SELECT 消息 + 绘制，不写库、不联网；
· 主题照上游 5 套（dark/wechat/light/warm/purple）；
· 中文用系统字体（msyh/msjh/simhei 逐级回退；全缺失时退回 Pillow 内置位图字体，
  仍能出图但无中文，**不报错**）；
· 区间跨度上限 `MAX_SPAN=2000`、画布高度上限 `MAX_HEIGHT=60000`（防超大图 OOM）；
· 文本**不转义问题**（不进 HTML），但会做控制字符清洗（防字体渲染异常）。
"""
from __future__ import annotations

import io
import os
import re
import time
from typing import Any

from loguru import logger
from PIL import Image, ImageDraw, ImageFont

from services.chat_render import THEMES, MAX_SPAN, DEFAULT_WIDTH
# 2026-09-30：时间格式 SSOT（日期分割线 / 时分不再裸切片）
from services.message_time import mt_date, mt_time

# 画布安全上限（Pillow 超大图会吃满内存）
MAX_HEIGHT = 60000
# 字体候选（Windows 优先；macOS/Linux 回退）
_FONT_CANDIDATES = (
    r"C:\Windows\Fonts\msyh.ttc",      # 微软雅黑（简体）
    r"C:\Windows\Fonts\msjh.ttc",      # 微软正黑（繁体）
    r"C:\Windows\Fonts\simhei.ttf",    # 黑体
    r"C:\Windows\Fonts\Deng.ttf",      # 等线
    "/System/Library/Fonts/PingFang.ttc",              # macOS
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",  # Linux
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
)
_CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _clean(s: Any) -> str:
    """清洗控制字符（字体渲染会异常），保留换行/制表。"""
    return _CTRL_RE.sub("", str(s or ""))


def available_font() -> str:
    """返回可用的 TrueType 字体路径；都没有则返回 ""（回退内置字体）。"""
    for p in _FONT_CANDIDATES:
        if os.path.exists(p):
            return p
    return ""


def _font(size: int, *, bold: bool = False):
    """取字体对象；找不到 TrueType 时回退 Pillow 内置（仍可出图）。"""
    p = available_font()
    if p:
        # 粗体优先找同族 bold（msyh → msyhbd）
        if bold:
            for cand in (p.replace("msyh.ttc", "msyhbd.ttc"),
                         p.replace("msjh.ttc", "msjhbd.ttc"),
                         p.replace("Deng.ttf", "Dengb.ttf"),
                         p.replace("arial.ttf", "arialbd.ttf")):
                if cand != p and os.path.exists(cand):
                    p = cand
                    break
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            pass
    try:
        return ImageFont.load_default(size)   # Pillow >= 10.1 支持 size
    except Exception:
        return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_w: float) -> list[str]:
    """按像素宽度折行（保留显式换行；中文按字符断、英文按空格优先）。"""
    out: list[str] = []
    for raw_line in _clean(text).split("\n"):
        if raw_line == "":
            out.append("")
            continue
        cur = ""
        for token in re.findall(r"[A-Za-z0-9_\-./:?=&%#+@]+|\s+|[^\s]", raw_line):
            cand = cur + token
            if draw.textlength(cand, font=font) <= max_w or not cur:
                cur = cand
                continue
            # 超宽：若 token 是长英文串，按字符硬切
            out.append(cur.rstrip())
            cur = token.lstrip()
            while draw.textlength(cur, font=font) > max_w and len(cur) > 1:
                cut = 1
                while cut < len(cur) and draw.textlength(cur[:cut + 1], font=font) <= max_w:
                    cut += 1
                out.append(cur[:cut])
                cur = cur[cut:]
        out.append(cur.rstrip())
    return out or [""]


def _rounded(draw: ImageDraw.ImageDraw, box, radius: int, fill) -> None:
    try:
        draw.rounded_rectangle(box, radius=radius, fill=fill)
    except Exception:
        draw.rectangle(box, fill=fill)


def render_png(account: str, conv_id: str, start_seq: int | None = None,
               end_seq: int | None = None, *, theme: str = "dark",
               title: str = "", subtitle: str = "", width: int = DEFAULT_WIDTH,
               scale: float = 2.0, db_path: str | None = None) -> bytes:
    """把消息区间渲染成 PNG 字节流（**Pillow 原生，无需浏览器**）。

    参数与 `chat_render.render_html` 保持一致（主题/标题/宽度/清晰度），
    便于两个出口互换。`scale` 为像素倍率（2.0 ≈ 高清，等效 CSS scale(2)）。
    """
    from services.chat_render import fetch_range
    th = THEMES.get(str(theme or "dark")) or THEMES["dark"]
    r = fetch_range(account, conv_id, start_seq, end_seq, db_path=db_path)
    msgs = r["messages"]
    conv_name = _clean(r["conv"]["name"])

    S = max(1.0, min(float(scale or 2.0), 4.0))
    W = int(max(240, min(int(width or DEFAULT_WIDTH), 1600)) * S)
    pad = int(14 * S)
    bub_max = int(W * 0.74)
    bub_pad_x, bub_pad_y = int(10 * S), int(7 * S)
    radius = int(10 * S)
    gap = int(9 * S)
    day_gap = int(14 * S)
    head_h = int((56 if (title or subtitle) else 18) * S)
    f_text = _font(int(14 * S))
    f_small = _font(int(11 * S))
    f_title = _font(int(15 * S), bold=True)
    f_day = _font(int(11 * S))

    # 逐条预计算（折行 + 高度），再决定画布总高
    layout: list[dict] = []
    total_h = head_h + pad
    max_text_w = bub_max - bub_pad_x * 2
    for m in msgs:
        items: list[dict] = []
        # 2026-09-30：日期/时分一律走 SSOT 判据（不合契约 ⇒ 空串）。
        # 旧实现在此处直接切 time 的前 10 字符，一旦 time 是 1970 年的
        # 占位值就会插一条 1970 年假分割线（源头已在 SSOT 侧消除）。
        day = mt_date(m["time"])
        if day and (not layout or layout[-1].get("day") != day):
            items.append({"kind": "day", "day": day})
        body = _clean(m["text"])
        if m["recalled"]:
            body = "消息已撤回"
        lines = _wrap(_dummy_draw(), body, f_text, max_text_w)
        extra_note = ""
        if m["transcription"] and not m["recalled"]:
            extra_note = _clean(m["transcription"])
        note_lines = (_wrap(_dummy_draw(), extra_note, f_small, max_text_w)
                      if extra_note else [])
        items.append({"kind": "msg", "dir": m["dir"], "lines": lines,
                      "note_lines": note_lines, "time": mt_time(m["time"])})
        h = 0
        if items and items[0]["kind"] == "day":
            h += day_gap + int(18 * S)
        text_h = len(lines) * int(20 * S)
        note_h = (int(6 * S) + len(note_lines) * int(16 * S)) if note_lines else 0
        bub_h = text_h + note_h + bub_pad_y * 2 + int(14 * S)
        h += bub_h + gap
        total_h += h
        layout.append({"day": day, "items": items, "h": h, "bub_h": bub_h})
    if total_h > MAX_HEIGHT:
        logger.warning(f"[RND-001] " + f"长图高度 {total_h}px 超上限 {MAX_HEIGHT}，"
                       f"已截断（区间跨度 {len(msgs)} 条）")
        total_h = MAX_HEIGHT

    img = Image.new("RGB", (W, total_h), th["bg"])
    d = ImageDraw.Draw(img)

    def _hex(c: str):
        c = c.lstrip("#")
        return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))

    C_FG, C_META = _hex(th["fg"]), _hex(th["meta"])
    C_SELF, C_SELF_FG = _hex(th["self"]), _hex(th["self_fg"])
    C_PEER, C_PEER_FG = _hex(th["peer"]), _hex(th["peer_fg"])
    # 分隔色：把 meta 色提亮/压暗做半透明近似
    C_LINE = tuple(int((a + b) / 2) for a, b in zip(C_META, _hex(th["bg"])))

    y = 0
    if title or subtitle:
        d.text((pad, int(12 * S)), _clean(title) or conv_name, font=f_title, fill=C_FG)
        if subtitle:
            d.text((pad, int(32 * S)), _clean(subtitle), font=f_small, fill=C_META)
        d.line([(0, head_h), (W, head_h)], fill=C_LINE, width=max(1, int(S)))
        y = head_h
    y += pad

    for block in layout:
        if y + block["h"] > total_h:
            break
        for it in block["items"]:
            if it["kind"] == "day":
                tw = d.textlength(it["day"], font=f_day)
                cx = (W - tw) / 2
                _rounded(d, [int(cx - 8 * S), y, int(cx + tw + 8 * S), y + int(18 * S)],
                         int(9 * S), C_LINE)
                d.text((cx, y + int(2 * S)), it["day"], font=f_day, fill=C_META)
                y += day_gap + int(18 * S)
                continue
            lines, note_lines = it["lines"], it["note_lines"]
            text_w = max([d.textlength(x, font=f_text) for x in lines] or [0])
            note_w = max([d.textlength(x, font=f_small) for x in note_lines] or [0])
            bw = int(min(bub_max, max(text_w, note_w) + bub_pad_x * 2))
            bh = block["bub_h"]
            x0 = W - pad - bw if it["dir"] == "out" else pad
            fill = C_SELF if it["dir"] == "out" else C_PEER
            fg = C_SELF_FG if it["dir"] == "out" else C_PEER_FG
            _rounded(d, [x0, y, x0 + bw, y + bh], radius, fill)
            ty = y + bub_pad_y
            for ln in lines:
                d.text((x0 + bub_pad_x, ty), ln, font=f_text, fill=fg)
                ty += int(20 * S)
            if note_lines:
                ty += int(2 * S)
                d.line([(x0 + bub_pad_x, ty), (x0 + bw - bub_pad_x, ty)],
                       fill=fg, width=1)
                ty += int(4 * S)
                for ln in note_lines:
                    d.text((x0 + bub_pad_x, ty), ln, font=f_small, fill=fg)
                    ty += int(16 * S)
            tw = d.textlength(it["time"], font=f_small)
            d.text((x0 + bw - bub_pad_x - tw, y + bh - int(14 * S)), it["time"],
                   font=f_small, fill=fg)
            y += bh + gap
    used_h = min(total_h, max(y + pad, head_h + pad))
    if used_h < total_h:
        img = img.crop((0, 0, W, used_h))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    logger.info(f"[RND-002] " + f"长图 PNG: {img.width}x{img.height} 消息 {len(msgs)} 条 "
                f"主题={theme}")
    return buf.getvalue()


_DUMMY: ImageDraw.ImageDraw | None = None


def _dummy_draw() -> ImageDraw.ImageDraw:
    """复用一个 1x1 画布做**纯测量**（折行时不真正出图）。"""
    global _DUMMY
    if _DUMMY is None:
        _DUMMY = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    return _DUMMY


def render_png_b64(*args, **kwargs) -> str:
    """PNG → data URI（便于前端直接 <img src>）。"""
    import base64
    b = render_png(*args, **kwargs)
    return "data:image/png;base64," + base64.b64encode(b).decode("ascii")
