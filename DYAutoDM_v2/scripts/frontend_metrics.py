"""前端度量脚本（只读）—— 唯一口径 SSOT，供门禁与人工审计共用。

口径定义（可被第二人独立复算）：
  bytes_canonical = 文件字节数 − CRLF 对数        # ≡ git blob 语义，跨机器稳定
  lines           = 以 LF 计的行数（不含末尾空行）
  不使用：工作区原始字节（随 core.autocrlf 变）、Get-Content.Count、Measure-Object -Line

用法：
  python scripts/frontend_metrics.py              # 全量
  python scripts/frontend_metrics.py --page       # 仅页面域
  python scripts/frontend_metrics.py --file <rel> # 单文件
  python scripts/frontend_metrics.py --chunk      # 叠加 dist chunk 体积

退出码：0 正常；2 指定文件不存在。
"""
from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from pathlib import Path

# ── 口径 ──────────────────────────────────────────────────────────────────
def canonical_bytes(p: Path) -> int:
    """LF 规范字节 = 工作区字节 − CRLF 对数。"""
    raw = p.read_bytes()
    return len(raw) - raw.count(b"\r\n")


def line_count(p: Path) -> int:
    """LF 行数（不含末尾换行后的空行）。"""
    raw = p.read_bytes().replace(b"\r\n", b"\n")
    if not raw:
        return 0
    if raw.endswith(b"\n"):
        raw = raw[:-1]
    return raw.count(b"\n") + 1


def crlf_count(p: Path) -> int:
    return p.read_bytes().count(b"\r\n")


# ── 域映射（与 R0 实测一致）────────────────────────────────────────────────
DOMAINS = {
    "accounts": "components/accounts",
    "crawl": "components/crawl",
    "kb": "components/kb",
    "live": "components/live",
    "logs": "components/logs",
    "messages": "components/messages",
    "notify": "components/notify",
    "overview": "components/overview",
    "platform": "components/platform",
    "settings": "components/settings",
    "stats": "components/stats",
    "tasks": "components/tasks",
}
CODE_EXT = (".ts", ".tsx", ".css")


def domain_metrics(src: Path, dom: str) -> dict:
    d = src / DOMAINS[dom]
    if not d.is_dir():
        return {}
    b = n = ln = crlf = 0
    files = sorted(f for f in d.iterdir()
                   if f.is_file() and f.suffix in CODE_EXT)
    for f in files:
        b += canonical_bytes(f)
        ln += line_count(f)
        crlf += crlf_count(f)
        n += 1
    return {"bytes": b, "files": n, "lines": ln, "crlf": crlf}


def chunk_size(fe: Path, dom: str) -> tuple[str, int] | None:
    """匹配 <dom>-page-*.js，取最大。"""
    cands = glob.glob(str(fe / "dist" / "assets" / f"{dom}-page-*.js"))
    if not cands:
        return None
    best = max(cands, key=os.path.getsize)
    return Path(best).name, os.path.getsize(best)


def dist_freshness(fe: Path) -> tuple[bool, str]:
    dist = fe / "dist" / "assets"
    src = fe / "src"
    if not dist.is_dir():
        return False, "dist 不存在"
    df = [p for p in dist.iterdir() if p.is_file()]
    sf = [p for p in glob.glob(str(src / "**/*.*"), recursive=True) if Path(p).is_file()]
    if not df or not sf:
        return False, "无法判定"
    nd = max(p.stat().st_mtime for p in df)
    ns = max(Path(p).stat().st_mtime for p in sf)
    import datetime as dt
    fmt = lambda t: dt.datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M:%S")
    fresh = nd >= ns
    return fresh, f"dist {fmt(nd)} / src {fmt(ns)} → {'新鲜' if fresh else '❌ 陈旧，chunk 数字不可信'}"


def check_chunks(max_b: int) -> int:
    """机器可读判据：输出超限域清单，供门禁直接消费（R17）。

    返回码 0 = 无超限，1 = 有超限。退出码不表达结论细节，细节在 stdout。
    门禁只认这一处的数字，避免在门禁里复制 chunk 口径。
    """
    here = Path(__file__).resolve().parent
    repo = here.parent
    fe = repo / "frontend"
    fresh, why = dist_freshness(fe)
    if not fresh:
        print(f"R17-UNCERTAIN dist不可判定: {why}")
        return 2

    over = []
    for dom in sorted(DOMAINS):
        ck = chunk_size(fe, dom)
        if ck and ck[1] > max_b:
            over.append(f"{dom}={ck[1]:,}B")
    if over:
        print(f"R17-FAIL {len(over)} 域超限: " + ", ".join(over))
        return 1
    print(f"R17-PASS 全部 chunk ≤ {max_b:,} B")
    return 0


# ── 输出 ──────────────────────────────────────────────────────────────────
def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--page", action="store_true", help="仅页面域")
    ap.add_argument("--chunk", action="store_true", help="叠加 dist chunk 体积")
    ap.add_argument("--file", help="单文件相对路径")
    ap.add_argument("--root", help="仓库根（默认：脚本所在目录的上一级，即 scripts/ 的父目录）")
    ap.add_argument("--check", type=int, metavar="MAX_B",
                    help="门禁模式：输出 chunk 超限清单并给退出码（R17 消费）")
    args = ap.parse_args()

    if args.check is not None:
        return check_chunks(args.check)

    here = Path(__file__).resolve().parent
    repo = Path(args.root).resolve() if args.root else here.parent
    fe = repo / "frontend"
    src = fe / "src"

    if args.file:
        p = Path(args.file)
        if not p.exists():
            p = repo / args.file
        if not p.exists():
            print(f"文件不存在: {args.file}", file=sys.stderr)
            return 2
        try:
            label = str(p.relative_to(repo))
        except ValueError:
            label = p.name
        print(label)
        print(f"  bytes_canonical : {canonical_bytes(p):>10,} B")
        print(f"  bytes_workspace : {p.stat().st_size:>10,} B")
        print(f"  crlf            : {crlf_count(p):>10,}")
        print(f"  lines(LF)       : {line_count(p):>10,}")
        return 0

    print(f"# 口径: bytes = 工作区字节 − CRLF对数（≡ git blob）; lines = LF 行数")
    import subprocess
    try:
        head = subprocess.run(["git", "-C", str(repo), "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True).stdout.strip()
    except Exception:
        head = "?"
    print(f"# 基线: {repo} @ {head}")
    fresh, note = dist_freshness(fe)
    print(f"# dist: {note}")
    print()

    header = f"{'域':<11}{'规范B':>10}{'工作区B':>10}{'CRLF':>7}{'文件':>6}{'行':>7}"
    if args.chunk:
        header += f"{'chunkB':>10}{'倍数':>7}  {'chunk文件':<28}"
    print(header)
    print("-" * (len(header) + 6))

    total_b = total_n = total_ln = total_crlf = 0
    for dom in sorted(DOMAINS):
        m = domain_metrics(src, dom)
        if not m:
            continue
        # 工作区原始字节
        d = src / DOMAINS[dom]
        ws = sum(f.stat().st_size for f in d.iterdir()
                 if f.is_file() and f.suffix in CODE_EXT)
        row = (f"{dom:<11}{m['bytes']:>10,}{ws:>10,}{m['crlf']:>7}"
               f"{m['files']:>6}{m['lines']:>7}")
        ck = chunk_size(fe, dom) if args.chunk else None
        if ck:
            ratio = ck[1] / m["bytes"] if m["bytes"] else 0
            row += f"{ck[1]:>10,}{ratio:>7.2f}x  {ck[0][:28]:<28}"
        print(row)
        total_b += m["bytes"]; total_n += m["files"]
        total_ln += m["lines"]; total_crlf += m["crlf"]

    print("-" * (len(header) + 6))
    print(f"{'合计':<11}{total_b:>10,}{'':>10}{'':>7}{total_n:>6}{total_ln:>7}")

    if args.chunk:
        # 阈值灵敏度
        print()
        print("# chunk 阈值灵敏度")
        cs = {}
        for dom in DOMAINS:
            ck = chunk_size(fe, dom)
            if ck:
                cs[dom] = ck[1]
        for thr in (40_000, 50_000, 60_000, 80_000):
            over = sorted(d for d, b in cs.items() if b > thr)
            print(f"  chunk > {thr:>6,} B ⇒ {len(over)}/{len(cs)} 域: {', '.join(over)}")

        if not fresh:
            print()
            print("# ⚠️ dist 陈旧：chunk 数字不可采信，门禁不得据此判定")

    return 0


if __name__ == "__main__":
    sys.exit(main())
