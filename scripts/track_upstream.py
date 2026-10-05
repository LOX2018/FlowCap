# -*- coding: utf-8 -*-
"""源项目追踪脚本 —— 检查清单中各上游项目是否有更新。

## 用途
本项目多处「借鉴/迁移/嵌入」外部开源项目（见 docs/upstream_sources.json）。
这些上游一旦变更（接口改名、反检测策略调整、风控对策更新），
本项目的对应实现就可能失效 —— 而**上游的更新说明往往已经写明了根因和解法**。

因此：**排查疑难问题前先跑一次本脚本**，看上游有没有相关更新；
定期（如每周）跑一次，把变更记入知识库。

## 用法
    python scripts/track_upstream.py              # 检查更新（打印报告）
    python scripts/track_upstream.py --update     # 检查并更新基线（接受当前状态）
    python scripts/track_upstream.py --json       # 输出 JSON（供其他工具消费）
    python scripts/track_upstream.py --only id1,id2   # 只查指定项目
    python scripts/track_upstream.py --commits N    # 同时列出最近 N 条提交（默认0=不列）

## 基线
    docs/upstream_baseline.json —— 记录上次检查时的 pushed_at + 最新 commit sha。
    首次运行会自动创建。

## 设计约束
    · 只用 GitHub 公共 API（无需 token，匿名 60 req/hour，足够：本清单 ~10 个项目）
    · 绝不写入项目业务代码，只读写 docs/ 下两个 JSON
    · 中文 Windows 下必须用 Python（不用 curl），路径含空格也不受影响
"""
import argparse
import io
import json
import os
import ssl
import sys
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                       # FlowCap/
DOCS = os.path.join(ROOT, "docs")
SRC = os.path.join(DOCS, "upstream_sources.json")
BASE = os.path.join(DOCS, "upstream_baseline.json")

API = "https://api.github.com"
# 2026-09-17 修补（OCR 审查 HIGH）：原实现**全局关闭** TLS 证书校验
#   CTX.check_hostname = False
#   CTX.verify_mode = ssl.CERT_NONE
# 而本脚本在配置 GITHUB_TOKEN/GH_TOKEN 时会发送 `Authorization: Bearer <token>`，
# 关闭校验等于给中间人留了窃取 GitHub 凭据的通道。
# 现改为**默认开启校验**；仅在显式设置 DY_UPSTREAM_INSECURE=1（自签证书/调试代理）
# 时才降级，并打印告警。
CTX = ssl.create_default_context()
if (os.environ.get("DY_UPSTREAM_INSECURE") or "").strip() == "1":
    CTX.check_hostname = False
    CTX.verify_mode = ssl.CERT_NONE
    print("[!] DY_UPSTREAM_INSECURE=1：已关闭 TLS 校验（仅限调试/自签证书场景）",
          file=sys.stderr)

# 可选：本地代理（GFW 下 GitHub API 可能不通）。优先级：
#   环境变量 DY_UPSTREAM_PROXY > 常见端口探测
PROXY_PORTS = (10808, 7890, 7891, 1080, 3128, 8888)


def _pick_proxy():
    p = (os.environ.get("DY_UPSTREAM_PROXY") or "").strip()
    if p:
        return p
    import socket
    for port in PROXY_PORTS:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.25):
                return "http://127.0.0.1:%d" % port
        except Exception:
            continue
    return None


def _get(url, proxy=None, timeout=20):
    hdrs = {
        "User-Agent": "FlowCap-upstream-tracker/1.0",
        "Accept": "application/vnd.github+json",
    }
    # 可选 token：GITHUB_TOKEN / GH_TOKEN → 限流 60/h → 5000/h（强烈推荐）
    _tok = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    if _tok:
        hdrs["Authorization"] = "Bearer " + _tok
    req = urllib.request.Request(url, headers=hdrs)
    if proxy:
        # 2026-09-17 修复（实机对照实验定位）—— 此处**不得**再调 req.set_proxy()。
        #
        # 事故：本函数原先同时使用 req.set_proxy() 与 ProxyHandler，两者叠加会把
        # 请求写坏：set_proxy 把 URL 改写成绝对形式（selector 变成
        # "http://127.0.0.1:10808/https://api.github.com/..."），GitHub 前置 CDN
        # 视为畸形请求，直接回 **HTTP 400「Bad request」的 HTML**（不是 API 的
        # 404 JSON）。脚本又把 400 误判成「仓库不存在/改名」。
        #
        # 后果被放大为「机制整体失效」：端口 10808 在本机常开，_pick_proxy() 的
        # 自动探测必然命中 → **清单里 11 个项目全部恒定 400** → 基线文件从未写入
        # 过任何 sha（恒为 {"projects": {}}），prev 永远为空 ⇒ 即使请求成功也只会
        # 报「🆕」，**永远判不出「有更新」**。
        #
        # 单变量对照实验（同一 URL、同一请求头，只改代理写法）：
        #   ① set_proxy=开 + ProxyHandler=开（旧写法）→ 400
        #   ② set_proxy=关 + ProxyHandler=开（现写法）→ 200（/repos 与 /commits 都通）
        #   ③ set_proxy=开 + ProxyHandler=关            → 400
        # ⇒ 归因到 set_proxy 这一行；ProxyHandler 本身工作正常，仅保留它。
        handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
        opener = urllib.request.build_opener(
            handler, urllib.request.HTTPSHandler(context=CTX))
    else:
        opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=CTX))
    with opener.open(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def load_json(p, default=None):
    if not os.path.exists(p):
        return default
    try:
        return json.load(io.open(p, encoding="utf-8"))
    except Exception as e:
        print("  [!] 读取 %s 失败: %s" % (os.path.basename(p), e))
        return default


def save_json(p, obj):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    io.open(p, "w", encoding="utf-8").write(
        json.dumps(obj, ensure_ascii=False, indent=2))


def main():
    ap = argparse.ArgumentParser(description="源项目更新追踪")
    ap.add_argument("--update", action="store_true", help="更新基线")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--only", default="", help="只查指定 id（逗号分隔）")
    ap.add_argument("--commits", type=int, default=0, help="列出最近 N 条提交")
    args = ap.parse_args()

    src = load_json(SRC)
    if not src or not src.get("projects"):
        print("❌ 未找到清单: %s" % SRC)
        return 2

    baseline = load_json(BASE) or {"_comment": "上游追踪基线（track_upstream.py 维护）",
                                   "projects": {}}
    base_projs = baseline.setdefault("projects", {})

    only = {x.strip() for x in args.only.split(",") if x.strip()}
    proxy = _pick_proxy()
    _tok_on = bool((os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip())
    if not args.json:
        if proxy:
            print("[i] 使用本地代理: %s" % proxy)
        print("[i] GITHUB_TOKEN: %s" % ("已配置（5000次/小时）" if _tok_on
                                        else "未配置（匿名限流 60次/小时）"))

    results = []
    import time as _tm
    _first = True
    for pj in src["projects"]:
        pid = pj.get("id", "?")
        if only and pid not in only:
            continue
        if not pj.get("track"):
            results.append({"id": pid, "name": pj.get("name"), "status": "skip",
                            "reason": pj.get("note") or "未开启追踪"})
            continue
        repo = pj.get("repo") or ""
        if not repo:
            results.append({"id": pid, "name": pj.get("name"), "status": "skip",
                            "reason": "缺 repo 地址"})
            continue

        item = {"id": pid, "name": pj.get("name"), "repo": repo,
                "kind": pj.get("kind"), "url": pj.get("url")}
        if not _first:
            _tm.sleep(0.6)   # 请求间节流，降低触发匿名限流的概率
        _first = False
        try:
            info = _get("%s/repos/%s" % (API, repo), proxy)
        except urllib.error.HTTPError as e:
            if e.code == 403:
                # 403 多为**匿名 API 限流**（60 req/hour/IP），不是仓库问题。
                # 读 X-RateLimit-Reset 给出可操作提示，并保留上次基线状态。
                reset = e.headers.get("X-RateLimit-Reset") if e.headers else None
                import time as _t
                when = ""
                if reset:
                    try:
                        when = _t.strftime("%H:%M:%S", _t.localtime(int(reset)))
                    except Exception:
                        when = str(reset)
                item.update(status="ratelimit",
                            reason="GitHub API 匿名限流（60次/小时）"
                                   + ("，约 %s 恢复" % when if when else ""))
            elif e.code == 404:
                item.update(status="error",
                            reason="HTTP 404（仓库不存在/改名）——请修正清单 repo")
            else:
                # 2026-09-17：非 404 不得再说「仓库不存在」。
                # 误报实例：代理写法错误时 GitHub 前置 CDN 回 400，脚本却报
                # 「仓库不存在/改名」，把排查引向清单地址（真相在网络层）。
                # 这里带响应体摘要，便于一次分清「网络/代理层」与「仓库层」。
                _hint = ""
                try:
                    _body = e.read().decode("utf-8", "replace")
                    _hint = " | " + " ".join(_body.split())[:80]
                except Exception:
                    pass
                item.update(status="error",
                            reason="HTTP %d（非 404：多为网络/代理层问题）%s"
                                   % (e.code, _hint))
            results.append(item); continue
        except Exception as e:
            item.update(status="error", reason="网络失败: %s" % str(e)[:90])
            results.append(item); continue

        pushed = info.get("pushed_at") or ""
        stars = info.get("stargazers_count")
        defbr = info.get("default_branch") or "main"
        item.update(pushed_at=pushed, stars=stars, default_branch=defbr,
                    archived=bool(info.get("archived")))

        # 最新 commit
        sha = ""
        try:
            cs = _get("%s/repos/%s/commits?per_page=1&sha=%s" % (API, repo, defbr),
                      proxy)
            if cs:
                sha = cs[0].get("sha", "")
                item["head_commit"] = {
                    "sha": sha[:10],
                    "date": (cs[0].get("commit", {}).get("author", {}) or {}).get("date", ""),
                    "msg": (cs[0].get("commit", {}).get("message", "") or "").split("\n")[0][:120],
                }
        except Exception:
            pass

        prev = base_projs.get(pid) or {}
        if not prev:
            item["status"] = "new"
        elif prev.get("head_sha") and sha and prev["head_sha"] != sha:
            item["status"] = "updated"
        elif prev.get("pushed_at") and pushed and prev["pushed_at"] != pushed:
            item["status"] = "updated"
        else:
            item["status"] = "same"

        if args.commits > 0:
            try:
                cs = _get("%s/repos/%s/commits?per_page=%d&sha=%s"
                          % (API, repo, args.commits, defbr), proxy)
                item["recent_commits"] = [
                    {"date": (c.get("commit", {}).get("author", {}) or {}).get("date", "")[:16],
                     "msg": (c.get("commit", {}).get("message", "") or "").split("\n")[0][:110]}
                    for c in cs
                ]
            except Exception:
                pass

        if args.update:
            base_projs[pid] = {
                "name": pj.get("name"), "repo": repo,
                "pushed_at": pushed, "head_sha": sha,
                "checked_at": __import__("time").strftime("%Y-%m-%d %H:%M:%S"),
            }
        results.append(item)

    if args.update:
        baseline["_updated_at"] = __import__("time").strftime("%Y-%m-%d %H:%M:%S")
        save_json(BASE, baseline)

    if args.json:
        print(json.dumps({"results": results,
                          "baseline_updated": args.update},
                         ensure_ascii=False, indent=2))
        return 0

    # ── 人类可读报告 ──
    print("=" * 78)
    print("源项目更新追踪  |  清单: %s" % os.path.basename(SRC))
    print("=" * 78)
    order = {"updated": 0, "new": 1, "same": 2, "skip": 3,
             "ratelimit": 4, "error": 5}
    for it in sorted(results, key=lambda x: order.get(x.get("status"), 9)):
        st = it.get("status")
        mark = {"updated": "🔔 有更新", "new": "🆕 新建基线",
                "same": "✅ 无变化", "skip": "⏭  跳过",
                "ratelimit": "⏳ 限流", "error": "⚠️  失败"}.get(st, "?")
        print("\n[%s] %s  (%s)" % (mark, it.get("name"), it.get("id")))
        if it.get("url"):
            print("    %s" % it["url"])
        if it.get("pushed_at"):
            print("    最后推送: %s   星标: %s" % (it["pushed_at"][:16], it.get("stars")))
        hc = it.get("head_commit")
        if hc:
            print("    最新提交: %s  %s" % (hc.get("sha"), hc.get("msg")))
        if it.get("reason"):
            print("    说明: %s" % it["reason"])
        for c in (it.get("recent_commits") or [])[:8]:
            print("      · %s  %s" % (c["date"], c["msg"]))
        if st == "updated":
            print("    ⚠️ 上游有变更 —— 请对照本项目 landing 点评估影响，")
            print("       并把变更记入知识库（工作记忆/*.md）。")

    upd = [x for x in results if x.get("status") == "updated"]
    rl = [x for x in results if x.get("status") == "ratelimit"]
    print("\n" + "=" * 78)
    print("汇总: 有更新 %d | 无变化 %d | 跳过 %d | 限流 %d | 失败 %d"
          % (len(upd),
             len([x for x in results if x.get("status") == "same"]),
             len([x for x in results if x.get("status") == "skip"]),
             len(rl),
             len([x for x in results if x.get("status") == "error"])))
    if rl:
        print("→ GitHub 匿名 API 限流（60次/小时）。等约 1 小时后重跑；")
        print("  或设 DY_UPSTREAM_PROXY=<本地代理> 换 IP；或配 GITHUB_TOKEN（见文档）")
    if upd and not args.update:
        print("→ 确认无碍后执行 --update 更新基线（否则下次仍会报同一批更新）")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
