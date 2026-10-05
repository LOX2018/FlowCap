"""定位 dist 里残留的 target:"_blank" 具体位置（前后文）。"""
from pathlib import Path

ROOT = Path(r"C:\Users\LOX\Desktop\DYchajian")
assets = ROOT / "frontend/dist/assets"
fs = sorted(assets.glob("*.js"))

needle = b'target:"_blank"'
for f in fs:
    b = f.read_bytes()
    n = b.count(needle)
    if not n:
        continue
    print(f"\n{'=' * 60}")
    print(f"{f.name}  ({len(b):,} 字节)  命中 {n} 处")
    print("=" * 60)
    idx = 0
    for i in range(n):
        idx = b.find(needle, idx)
        if idx < 0:
            break
        start = max(0, idx - 220)
        end = min(len(b), idx + 90)
        ctx = b[start:end].decode("utf-8", errors="replace")
        print(f"\n--- 命中 {i + 1} @ offset {idx} ---")
        print(ctx)
        idx += 1
