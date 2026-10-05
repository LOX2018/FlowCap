"""复刻前端 parseMedia，找出哪些 [图片] 消息会走到「无图链」兜底分支。

用户反馈：「[图片]（无图链，需重新捕获）」脏数据依旧在前端显示。
但数据库里 [图片] 消息只有两类（内联 43 / 远程链 17），没有"无图链"。

说明是前端 parseMedia 对某些文本解析失败 → thumb 为空 → 走到兜底。
本脚本逐条模拟前端逻辑，定位到底哪些消息解析不出来。
"""
import re
import sqlite3
from pathlib import Path

DB = Path(r"C:\temp\dyautodm_test\data\dyautodm.db")

# ---- 复刻前端 sanitizeDataUri ----
_KEEP = re.compile(r"[^A-Za-z0-9+/=_-]")


def sanitize_data_uri(u: str) -> str:
    if not u.startswith("data:"):
        return u
    comma = u.find(",")
    if comma < 0:
        return u
    head = u[: comma + 1]
    return head + _KEEP.sub("", u[comma + 1:])


# ---- 复刻前端 parseMedia ----
def parse_media(text: str):
    thumb = origin = ""
    for raw in (text or "").split("\n"):
        line = raw.strip()
        if line.startswith("[原图]"):
            origin = re.sub(r"^\[原图\]\s*", "", line).strip()
            continue
        m = re.match(r"^\[(图片|表情包)\]\s*(.+)$", line)
        if m and not thumb:
            seg = m.group(2).strip()
            glue = seg.find("[原图]")
            if glue > 0:
                thumb = sanitize_data_uri(seg[:glue].strip())
                if not origin:
                    om = re.match(r"^\[原图\]\s*(https?://\S+)", seg[glue:])
                    if om:
                        origin = om.group(1)
            else:
                thumb = sanitize_data_uri(seg)
            continue
        if not thumb:
            u = re.search(r"(data:image/[\w+-]+;base64,\S+|https?://\S+)", line)
            if u:
                thumb = u.group(1)
    is_sticker = bool(re.match(r"^\[表情包\]", (text or "").strip()))
    inline = (
        bool(re.match(r"^data:image/", thumb))
        or bool(re.match(r"^https?://i\.ibb\.co/", thumb))
        or bool(re.match(r"^https?://tucdn\.wpon\.cn/", thumb))
        or is_sticker
    )
    return {"thumb": thumb, "origin": origin,
            "isSticker": is_sticker, "inline": inline}


def main():
    con = sqlite3.connect(str(DB))
    rows = con.execute(
        "SELECT id, text FROM dm_messages WHERE text LIKE '[图片]%'"
    ).fetchall()
    con.close()

    print(f"[图片] 消息: {len(rows)} 条\n")

    branch = {"内联渲染": 0, "远程链去抖音": 0, "无图链兜底": 0}
    bad_cases = []

    for mid, text in rows:
        r = parse_media(text)
        if not r["thumb"]:
            branch["无图链兜底"] += 1
            bad_cases.append((mid, text))
        elif r["inline"]:
            branch["内联渲染"] += 1
        else:
            branch["远程链去抖音"] += 1

    print("=" * 58)
    print("前端分支命中统计")
    print("=" * 58)
    for k, v in branch.items():
        print(f"  {k:14s}: {v}")

    if bad_cases:
        print()
        print("=" * 58)
        print(f"走到「无图链」的消息: {len(bad_cases)} 条")
        print("=" * 58)
        for mid, text in bad_cases[:6]:
            print(f"\n  id={mid}  长度={len(text)}")
            print(f"  内容: {text[:150]!r}")
    else:
        print("\n  [OK] 没有消息会走到「无图链」兜底")
        print("\n  那么用户看到的「无图链」可能来自：")
        print("    1) 前端缓存的旧渲染结果（代码已改但没重新加载）")
        print("    2) m.type === 'image' 但 text 不以 [图片] 开头的消息")
        print("    3) 其他页面的渲染路径")

    # 额外检查：type=image 但不以 [图片] 开头
    con = sqlite3.connect(str(DB))
    odd = con.execute(
        "SELECT id, substr(text,1,70) FROM dm_messages "
        "WHERE (type='image' OR type='sticker') "
        "AND text NOT LIKE '[图片]%' AND text NOT LIKE '[表情包]%' LIMIT 8"
    ).fetchall()
    con.close()
    if odd:
        print()
        print("=" * 58)
        print(f"type=image/sticker 但不以 [图片] 开头: {len(odd)} 条")
        print("=" * 58)
        for mid, t in odd[:8]:
            print(f"  id={mid}: {t!r}")


if __name__ == "__main__":
    main()
