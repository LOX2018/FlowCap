# -*- coding: utf-8 -*-
"""接口层按域切分 —— 实机验证（阶段1）

## 铁律：环境隔离

本脚本**强制**使用本分支独立环境 `C:\\temp\\flowcap_design`。
（2026-09-20 起两分支已合并为单分支 `design/better-douyin`，
 旧主分支环境 `C:\\temp\\flowcap_test` 已废弃删除，不再作为禁入目标。）

## 验证目标

1. **方法面守恒**：改造前 60 个方法 → 改造后 60 个，零丢失
2. **MRO 正确**：`DouyinAPI` 由 9 个域 mixin 组装
3. **内部引用解析**：`DouyinAPI.xxx(...)` 形式仍能调到 mixin 提供的方法
4. **端到端**：真实账号经门面调各域接口，返回结构正常
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

DESIGN_ROOT = r"C:\temp\flowcap_design"
_SOURCE_REPO = r"C:\Users\LOX\Desktop\DYchajian"
# 2026-09-20：主分支环境 C:\temp\flowcap_test 已废弃删除（两分支合并为
# design/better-douyin）。隔离门禁据此改为防「数据落进源码树」
# —— 铁律：源码目录不得产生 db / profile / data / accounts。
if os.path.abspath(DESIGN_ROOT).startswith(os.path.abspath(_SOURCE_REPO) + os.sep):
    raise SystemExit(
        "拒绝运行：环境根落在源码树内，会污染源码库"
        r"（应使用 C:\temp\flowcap_design）")
os.environ["FLOWCAP_APP_ROOT"] = DESIGN_ROOT
os.environ.setdefault("PYTHON_BASIC_REPL", "1")
_s = json.loads((Path(DESIGN_ROOT) / "members" / ".session.json").read_text(encoding="utf-8"))
os.environ["DY_MEMBER"] = _s["member_id"]
os.environ["DY_MEMBER_KEY"] = _s["master_key"]

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
# 账号名从环境变量读（本软件为通用产品，不硬编码任何真实账号）
ACCOUNT = os.environ.get("DY_TEST_ACCOUNT", "").strip()
if not ACCOUNT:
    raise SystemExit("请先设置 DY_TEST_ACCOUNT=<账号名> 再运行本验证脚本")
R: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    R.append((name, ok, detail))
    print(f"  {'✓' if ok else '✗'} {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print(f"\n环境: {os.environ['FLOWCAP_APP_ROOT']}\n" + "=" * 62)

    # ── 1. 方法面守恒 ──────────────────────────────────────────────
    print("\n[1] 方法面守恒（对比 HEAD 版本）")
    old = subprocess.run(
        ["git", "show", "HEAD:backend/dy_apis/douyin_api.py"],
        capture_output=True, text=True, encoding="utf-8", cwd=os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))).stdout
    t = ast.parse(old)
    c = next(n for n in t.body if isinstance(n, ast.ClassDef) and n.name == "DouyinAPI")
    old_m = {n.name for n in c.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    from dy_apis.douyin_api import DouyinAPI as D
    new_m = {n for n in dir(D) if not n.startswith("__") and callable(getattr(D, n))}
    missing = sorted(old_m - new_m)
    check("方法零丢失", not missing, f"改造前 {len(old_m)} → 改造后 {len(new_m)}" +
          (f"，丢失 {missing}" if missing else ""))

    # ── 2. MRO ────────────────────────────────────────────────────
    print("\n[2] 组装结构（MRO）")
    mro = [x.__name__ for x in D.__mro__]
    mixins = ["UserMixin", "VideoMixin", "CommentsMixin", "CollectionMixin", "RelationsMixin",
              "NoticeMixin", "SearchMixin", "LiveMixin", "ImMixin"]
    check("9 个域 mixin 已组装", all(m in mro for m in mixins), " → ".join(mro[:4]) + " ...")

    # ── 3. 域文件存在 ─────────────────────────────────────────────
    print("\n[3] 域模块（照源项目 api/client_*.rs）")
    ddir = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / "dy_apis"
    doms = ["user", "video", "comments", "collection", "relations", "notice", "search", "live", "im"]
    found = [d for d in doms if (ddir / f"client_{d}.py").exists()]
    check(f"域文件 {len(found)}/9", len(found) == 9, " ".join(found))
    check("基础层 client.py", (ddir / "client.py").exists(),
          "BaseClient（域名/签名/请求）")

    # ── 4. 内部引用解析（109 处 DouyinAPI.xxx 形式）──────────────────
    print("\n[4] 内部引用解析（mixin 方案的关键）")
    import inspect
    src_im = (ddir / "client_im.py").read_text(encoding="utf-8")
    has_internal = "DouyinAPI." in src_im or "DouyinAPI." in (
        ddir / "client_comments.py").read_text(encoding="utf-8")
    check("域模块保留 DouyinAPI.xxx 内部调用形式", has_internal, "（故无需改 109 处引用）")
    # 实际解析测试：get_conversation_list_all 内部调 DouyinAPI.get_conversation_list
    check("内部调用可解析", callable(getattr(D, "get_conversation_list", None)),
          "get_conversation_list_all → DouyinAPI.get_conversation_list")

    # ── 5. 端到端（真实账号）───────────────────────────────────────
    print("\n[5] 端到端（真实账号经门面调各域）")
    import api.platform as P
    auth = P._auth_for(ACCOUNT)
    check("凭证加载", len(auth.cookie) > 0, f"{len(auth.cookie)} cookie")

    # 收藏域
    raw = D.get_aweme_list_collection(auth, "0", "3")
    items = (raw or {}).get("aweme_list") or []
    check("collection 域（收藏夹作品）", len(items) > 0, f"{len(items)} 个作品")

    # 用户域：取自己的 sec_uid 后调我的收藏（避免传空参数导致空响应）
    sec = getattr(auth, "sec_uid", None) or D.get_my_sec_uid(auth)
    check("user 域（get_my_sec_uid）", bool(sec), f"sec_uid={str(sec)[:28]}...")

    # ⚠️ `get_user_favorite`（/aweme/v1/web/aweme/favorite/）实测 HTTP 200 但**响应体 0 字节**。
    #    已单独取证：直接构造原始请求（不经门面）同样 0 字节 ⇒ **平台侧行为，非本次拆分引入**。
    #    同一族现象见 `docs/logic_replication_results.md`（点赞/关注/收藏等写操作统一 0 字节）。
    #    故此处只验"方法可调用 + 请求可达"，不将空响应当作失败。
    if sec:
        try:
            fav = D.get_user_favorite(auth, sec, "0", "5")
            check("user 域（我的收藏·方法可达）", isinstance(fav, dict), f"{len(fav)} 键")
        except Exception as e:  # noqa: BLE001
            # 空响应 → JSONDecodeError，属平台侧已知现象
            known = "JSONDecodeError" in type(e).__name__
            check("user 域（我的收藏·方法可达）", known,
                  f"{type(e).__name__}（空响应=平台侧已知现象）" if known else str(e)[:50])

    # IM 域（会话列表）
    convs = D.get_conversation_list(auth, 0)
    check("im 域（会话列表）", isinstance(convs, list), f"{len(convs) if isinstance(convs,list) else '?'} 个会话")

    print("\n" + "=" * 62)
    ok = sum(1 for _, o, _ in R if o)
    print(f"结果: {ok}/{len(R)} 通过")
    for n, o, d in R:
        if not o:
            print(f"  失败: {n} — {d}")
    return 0 if ok == len(R) else 1


if __name__ == "__main__":
    raise SystemExit(main())
