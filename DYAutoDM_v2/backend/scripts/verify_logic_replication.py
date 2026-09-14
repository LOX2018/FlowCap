# -*- coding: utf-8 -*-
"""本分支（design/better-douyin）逻辑层复现 —— 实机验证脚本

## 铁律：环境隔离

本脚本**强制**使用本分支的独立部署目录，绝不触碰主分支环境
（`C:\\temp\\dyautodm_test`）。若环境变量指向主分支，脚本**拒绝运行**。

用法（在 backend 目录下）：
    python scripts/verify_logic_replication.py

环境（由脚本自行设置，无需手工 export）：
    DY_APP_ROOT = C:\\temp\\dyautodm_design     ← 本分支独立环境
    DY_MEMBER   = <本分支会员 id>
    DY_MEMBER_KEY = <从 .session.json 读>
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

# ── 1. 环境隔离门禁（先于任何业务导入）──────────────────────────────
DESIGN_ROOT = r"C:\temp\dyautodm_design"
FORBIDDEN = (r"C:\temp\dyautodm_test",)      # 主分支环境，绝不使用

_root = os.path.abspath(DESIGN_ROOT)
for bad in FORBIDDEN:
    if os.path.abspath(bad) == _root:
        raise SystemExit(f"[隔离门禁] 拒绝运行：目标环境指向主分支 {bad}")

os.environ["DY_APP_ROOT"] = DESIGN_ROOT
os.environ.setdefault("PYTHON_BASIC_REPL", "1")

_sess = Path(DESIGN_ROOT) / "members" / ".session.json"
if not _sess.exists():
    raise SystemExit(f"[隔离门禁] 本分支环境缺少会员会话: {_sess}")
_s = json.loads(_sess.read_text(encoding="utf-8"))
os.environ["DY_MEMBER"] = _s["member_id"]
os.environ["DY_MEMBER_KEY"] = _s["master_key"]

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

ACCOUNT = "尚进工伤小助理"
OUT = Path(DESIGN_ROOT) / "_logic_verify"      # 产物只落本分支环境
RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  {'✓' if ok else '✗'} {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print(f"\n环境: {os.environ['DY_APP_ROOT']}  member={os.environ['DY_MEMBER']}")
    print("=" * 62)

    # ── 阶段 1：媒体代理 ────────────────────────────────────────────
    print("\n[阶段1] 媒体代理（services/media_proxy.py）")
    from services import media_proxy as MP
    check("缓存目录在本分支环境内", MP.cache_dir().startswith(DESIGN_ROOT), MP.cache_dir())

    # ── 阶段 5：双域名 + 内容面 ─────────────────────────────────────
    print("\n[阶段5] 双域名 / 内容面（dy_apis/douyin_api.py）")
    from dy_apis.douyin_api import DouyinAPI
    hj = DouyinAPI.domain_for("/aweme/v1/web/aweme/listcollection/")
    check("列表类走 www-hj 域名", "www-hj.douyin.com" in hj, hj)
    dg = DouyinAPI.domain_for("/aweme/v1/web/commit/item/digg/")
    check("互动写操作亦走 www-hj（逆向证据）", "www-hj.douyin.com" in dg, dg)
    sr = DouyinAPI.domain_for("/aweme/v1/web/search/item/")
    check("搜索/详情走主域名 www", sr == "https://www.douyin.com", sr)

    import api.platform as P
    auth = P._auth_for(ACCOUNT)
    check("凭证加载（本分支环境）", len(auth.cookie) > 0, f"{len(auth.cookie)} cookie")

    api = P._api()
    raw = api.get_aweme_list_collection(auth, "0", "3")
    items = (raw or {}).get("aweme_list") or []       # 基座返回 dict（实测）
    check("收藏夹作品接口", isinstance(items, list) and len(items) > 0, f"{len(items)} 个作品")

    rawm = api.get_mix_list_collection(auth, "20", "0")
    mixes = (rawm or {}).get("mix_infos") or []       # 基座返回 dict（实测）
    check("收藏合集接口", isinstance(mixes, list) and len(mixes) > 0, f"{len(mixes)} 个合集")

    # ── 阶段 2：下载子系统 ──────────────────────────────────────────
    print("\n[阶段2] 下载子系统（backend/downloader/）")
    from downloader import media_request as MR, run_task, tasks as TK
    if not items:
        check("下载端到端", False, "无可用作品数据（跳过）")
    else:
        aweme = items[0]
        summary = MR.extract_media(aweme)
        check("媒体提取", summary["type"] == "video",
              f"type={summary['type']} qualities={summary.get('video_qualities')}")

        t = TK.DownloadTask(task_id="verify1", aweme_id=str(aweme.get("aweme_id")),
                            desc=(aweme.get("desc") or "")[:60],
                            nickname=(aweme.get("author") or {}).get("nickname") or "verify")
        out = run_task(t, aweme, base_dir=str(OUT), quality="h264")
        check("下载状态流转", out.status == TK.COMPLETED, f"status={out.status}")
        check("文件落盘", out.done_files == out.total_files and out.done_files > 0,
              f"{out.done_files}/{out.total_files}, {out.bytes_done} bytes")
        if out.save_path:
            check("产物落在本分支环境内", out.save_path.startswith(DESIGN_ROOT), out.save_path)

    # ── 阶段 4：MCP 工具面 ──────────────────────────────────────────
    print("\n[阶段4] MCP 工具面（backend/mcp/）")
    from mcp import registry, tools as mtools
    n = mtools.register_all()
    check("工具注册数", n >= 22, f"{n} 个")
    rd = sum(1 for t in registry.all_tools() if t.level == "read")
    wr = sum(1 for t in registry.all_tools() if t.level != "read")
    check("读写分级", rd > wr, f"read={rd} write={wr}")

    # ── 汇总 ────────────────────────────────────────────────────────
    print("\n" + "=" * 62)
    ok = sum(1 for _, o, _ in RESULTS if o)
    print(f"结果: {ok}/{len(RESULTS)} 通过")
    for name, o, d in RESULTS:
        if not o:
            print(f"  失败: {name} — {d}")
    return 0 if ok == len(RESULTS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
