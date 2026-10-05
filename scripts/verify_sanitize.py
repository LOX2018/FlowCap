"""验证 sanitizeDataUri 清洗逻辑：能否让脏数据恢复可渲染。

复刻前端 sanitizeDataUri + parseMedia 的 JS 逻辑到 Python，
对数据库里 43 条真实内联图片做「清洗前 / 清洗后」对比，
确认清洗后 base64 合法且能解出 WebP 尺寸。

前端逻辑：
  1) 在 `[图片] <seg>` 里找 `[原图]` 的粘连位置，切分出 thumb 与 origin
  2) thumb 走 sanitizeDataUri：只保留 [A-Za-z0-9+/=_-]
"""
import base64
import re
import sqlite3
from pathlib import Path

DB = Path(r"C:\temp\flowcap_test\data\flowcap.db")

# 与前端 sanitizeDataUri 一致
_KEEP = re.compile(r"[^A-Za-z0-9+/=_-]")


def sanitize_data_uri(u: str) -> str:
    if not u.startswith("data:"):
        return u
    comma = u.find(",")
    if comma < 0:
        return u
    head = u[: comma + 1]
    rest = _KEEP.sub("", u[comma + 1 :])
    return head + rest


def parse_media(text: str):
    """复刻前端 parseMedia 的关键分支。"""
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
    return thumb, origin


def webp_dims(b: bytes):
    if len(b) < 30 or b[:4] != b"RIFF" or b[8:12] != b"WEBP":
        return None
    fmt = b[12:16]
    try:
        if fmt == b"VP8X":
            return (int.from_bytes(b[24:27], "little") + 1,
                    int.from_bytes(b[27:30], "little") + 1)
        if fmt == b"VP8 ":
            i = b.find(b"\x9d\x01\x2a")
            if i < 0:
                return None
            return (int.from_bytes(b[i + 3:i + 5], "little") & 0x3FFF,
                    int.from_bytes(b[i + 5:i + 7], "little") & 0x3FFF)
        if fmt == b"VP8L":
            bits = int.from_bytes(b[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    except Exception:
        return None
    return None


def main():
    con = sqlite3.connect(str(DB))
    rows = con.execute(
        "SELECT id, text FROM dm_messages WHERE text LIKE '%data:image%'"
    ).fetchall()
    con.close()

    print(f"内联图片消息: {len(rows)} 条\n")

    ok_before = ok_after = 0
    got_origin = 0
    dims_after = []

    for mid, text in rows:
        # --- 清洗前：直接按整段当 base64（旧前端行为）---
        i = text.find("base64,")
        raw_payload = text[i + 7 :] if i >= 0 else ""
        try:
            b0 = base64.b64decode(raw_payload)
            ok_before += 1 if webp_dims(b0) else 0
        except Exception:
            pass

        # --- 清洗后：走前端新逻辑 ---
        thumb, origin = parse_media(text)
        if origin:
            got_origin += 1
        if thumb.startswith("data:"):
            payload = thumb[thumb.find(",") + 1 :]
            try:
                b1 = base64.b64decode(payload + "=" * (-len(payload) % 4))
                d = webp_dims(b1)
                if d:
                    ok_after += 1
                    dims_after.append(d)
            except Exception:
                pass

    print("=" * 58)
    print("清洗前 vs 清洗后")
    print("=" * 58)
    print(f"  可解码出 WebP 尺寸的图片数:")
    print(f"    清洗前: {ok_before:3d} / {len(rows)}")
    print(f"    清洗后: {ok_after:3d} / {len(rows)}")
    print()
    print(f"  成功抠出 [原图] URL: {got_origin} / {len(rows)}")

    if dims_after:
        from collections import Counter
        print(f"\n  清洗后尺寸分布（前 8）:")
        for (w, h), n in Counter(dims_after).most_common(8):
            print(f"    {w:5d} × {h:5d}   {n:3d} 张")

    print()
    if ok_after > ok_before:
        print(f"  >>> 清洗修复了 {ok_after - ok_before} 张图片的渲染")
    elif ok_after == ok_before and ok_after == len(rows):
        print("  >>> 清洗前后都能解码（说明数据本身已经是新版带换行的）")
    else:
        print(f"  >>> 仍有 {len(rows) - ok_after} 张无法解码，需进一步排查")


if __name__ == "__main__":
    main()
