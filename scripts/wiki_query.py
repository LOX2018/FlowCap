"""
LLM Wiki 查询/校验脚本（Windows 中文环境安全版）。

为什么不用 curl：
  在 MSYS bash 里 `curl -d '{"query":"中文"}'` 走 GBK 编码，
  LLM Wiki 返回 `{"error":"Request body must be UTF-8"}`。
  必须用 Python 显式 UTF-8 encode 后再发。

用法:
  python scripts/wiki_query.py "查询词"
  python scripts/wiki_query.py --files wiki
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:19828"
# 项目 UUID 从 /api/v1/projects 拿到，避免中文路径 + 冒号编码问题
PROJECT = "ac8c292b-f819-4421-b0ae-0d6c31329559"
TOKEN = os.environ.get("LLM_WIKI_API_TOKEN", "")


def req(method, path, body=None):
    url = f"{BASE}{path}"
    data = None
    headers = {"Authorization": f"Bearer {TOKEN}"}
    if body is not None:
        # 关键：显式 UTF-8，不要用系统默认编码（Windows 是 GBK）
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    r = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=60) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return {"error": f"HTTP {e.code}", "detail": e.read().decode("utf-8", "replace")}
    except Exception as e:
        return {"error": str(e)}


def search(q, topk=6):
    return req(
        "POST",
        f"/api/v1/projects/{PROJECT}/search",
        {"query": q, "topK": topk, "includeContent": False},
    )


def files(root="wiki"):
    return req("GET", f"/api/v1/projects/{PROJECT}/files?root={root}")


def read_page(path):
    # 中文/空格路径必须 percent-encode，否则 urllib 用 ascii 编码 URL 会报错
    from urllib.parse import quote

    return req(
        "GET",
        f"/api/v1/projects/{PROJECT}/files/content?path={quote(path, safe='/')}",
    )


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return

    if args[0] == "--files":
        d = files(args[1] if len(args) > 1 else "wiki")
        if "error" in d:
            print("ERR:", d)
            return
        for n in d.get("files", []):
            if n.get("isDir"):
                print(f"  [dir] {n['name']}")
            else:
                print(f"  {n['size']:>8,}  {n['name']}")
        return

    if args[0] == "--read":
        # 2026-09-17 修补（OCR 审查 HIGH）：原实现无条件 `args[1]`，
        # 运行 `python scripts/wiki_query.py --read`（不带路径）时抛
        # **IndexError**（未捕获栈）；改为给出可读用法提示。
        if len(args) < 2:
            print("用法: python scripts/wiki_query.py --read <页面路径>", file=sys.stderr)
            print(__doc__)
            return 2
        d = read_page(args[1])
        if "error" in d:
            print("ERR:", d)
            return
        c = d.get("content", "")
        print(c[:3000])
        if len(c) > 3000:
            print(f"... (共 {len(c)} 字符，已截断)")
        return

    q = " ".join(args)
    d = search(q)
    if "error" in d:
        print("ERR:", d)
        return
    print(f"查询: {q}")
    print(f"模式: {d.get('mode')}")
    res = d.get("results") or []
    if not res:
        print("  (无结果)")
    for r in res:
        print(f"  {r.get('score', 0):>9.4f}  {r.get('path', '?')}")


if __name__ == "__main__":
    main()
