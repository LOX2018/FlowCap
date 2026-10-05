"""解析库里 inline_pic 的真实尺寸，回答「抖音到底下发了多大的图」。

关键问题：inline_pic 是否只有 160×213 缩略图？还是存在更大尺寸？
若存在大图，说明原图在消息体里、只是我之前取样偏差；
若全部都是小图，则确认抖音 IM 首包**不下发原图**。

附带检查：base64 里是否混入非 ASCII 字符（会导致前端渲染失败）。
"""
import base64
import re
import sqlite3
from collections import Counter
from pathlib import Path

DB = Path(r"C:\temp\dyautodm_test\data\dyautodm.db")

# 合法的 base64 字符
_B64_RE = re.compile(rb"[^A-Za-z0-9+/=]")


def webp_dims(b: bytes):
    """解析 WebP 尺寸（VP8X / VP8 / VP8L 三种格式）。"""
    if len(b) < 30 or b[:4] != b"RIFF" or b[8:12] != b"WEBP":
        return None
    fmt = b[12:16]
    try:
        if fmt == b"VP8X":           # 扩展格式
            w = int.from_bytes(b[24:27], "little") + 1
            h = int.from_bytes(b[27:30], "little") + 1
            return w, h
        if fmt == b"VP8 ":           # 有损
            i = b.find(b"\x9d\x01\x2a")
            if i < 0:
                return None
            w = int.from_bytes(b[i + 3:i + 5], "little") & 0x3FFF
            h = int.from_bytes(b[i + 5:i + 7], "little") & 0x3FFF
            return w, h
        if fmt == b"VP8L":           # 无损
            bits = int.from_bytes(b[21:25], "little")
            return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
    except Exception:
        return None
    return None


def main():
    con = sqlite3.connect(str(DB))
    rows = list(con.execute(
        "SELECT id, text FROM dm_messages WHERE text LIKE '%data:image%'"))
    con.close()

    print(f"内联图片消息: {len(rows)} 条\n")

    sizes = []
    dirty = []
    decoded_fail = 0

    for mid, text in rows:
        i = text.find("base64,")
        if i < 0:
            continue
        payload = text[i + 7:]
        # 检查脏字符
        bad = _B64_RE.findall(payload.encode("utf-8", "ignore"))
        if bad:
            dirty.append((mid, len(bad), bad[:5]))
        # 清洗后解码
        clean = _B64_RE.sub(b"", payload.encode("utf-8", "ignore"))
        try:
            b = base64.b64decode(clean + b"=" * (-len(clean) % 4))
        except Exception:
            decoded_fail += 1
            continue
        d = webp_dims(b)
        if d:
            sizes.append((*d, len(b)))

    print("=" * 60)
    print("1. inline_pic 尺寸分布")
    print("=" * 60)
    print(f"  成功解析: {len(sizes)} 张（解码失败 {decoded_fail}）\n")
    cnt = Counter((w, h) for w, h, _ in sizes)
    for (w, h), n in cnt.most_common(15):
        print(f"    {w:5d} × {h:5d}   {n:3d} 张")

    if sizes:
        ws = [s[0] for s in sizes]
        hs = [s[1] for s in sizes]
        bs = [s[2] for s in sizes]
        print(f"\n  宽度范围: {min(ws)} ~ {max(ws)}")
        print(f"  高度范围: {min(hs)} ~ {max(hs)}")
        print(f"  体积范围: {min(bs):,} ~ {max(bs):,} 字节")

        big = [s for s in sizes if s[0] > 400 or s[1] > 400]
        print(f"\n  >>> 宽或高 >400px 的: {len(big)}/{len(sizes)}")
        if big:
            print("      样例:", [(w, h, f"{b:,}B") for w, h, b in big[:6]])
        else:
            print("      [结论] inline_pic 全是小图（缩略图级别），")
            print("             抖音 IM 首包**不下发原图**。")

    print()
    print("=" * 60)
    print("2. base64 脏字符检查")
    print("=" * 60)
    if not dirty:
        print("  [OK] 全部为合法 base64 字符")
    else:
        print(f"  [!!] {len(dirty)}/{len(rows)} 条含非 base64 字符:")
        for mid, n, sample in dirty[:8]:
            print(f"      id={mid}: {n} 个脏字符，样例 {sample}")
        print("\n  这会导致前端 <img src='data:image/webp;base64,...'> 渲染失败")


if __name__ == "__main__":
    main()
