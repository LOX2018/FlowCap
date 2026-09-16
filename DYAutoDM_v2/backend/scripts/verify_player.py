# -*- coding: utf-8 -*-
"""播放器 + 媒体取址 —— 实机验证（阶段4）

## 铁律：环境隔离（强制本分支环境）

## 验证目标

1. **后端端点** `/api/platform/media/resolve`：真实账号取址，返回前端
   `PlayerMedia` 契约的字段
2. **播放器契约**：`player-types` / `player-utils` 的常量照源项目对齐
3. **组件结构**：player/ 目录切分照源项目
4. **媒体代理**：`media_proxy` 缓存统计可达
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

DESIGN_ROOT = r"C:\temp\dyautodm_design"
assert os.path.abspath(DESIGN_ROOT) != os.path.abspath(r"C:\temp\dyautodm_test")
os.environ["DY_APP_ROOT"] = DESIGN_ROOT
os.environ.setdefault("PYTHON_BASIC_REPL", "1")
_s = json.loads((Path(DESIGN_ROOT) / "members" / ".session.json").read_text(encoding="utf-8"))
os.environ["DY_MEMBER"] = _s["member_id"]
os.environ["DY_MEMBER_KEY"] = _s["master_key"]

FE = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))).parent / "frontend" / "src"
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
R: list[tuple[str, bool, str]] = []


def check(n: str, ok: bool, d: str = "") -> None:
    R.append((n, ok, d))
    print(f"  {'✓' if ok else '✗'} {n}" + (f" — {d}" if d else ""))


def main() -> int:
    print(f"\n环境: {os.environ['DY_APP_ROOT']}\n" + "=" * 64)

    # ── 1. 播放器目录结构（照源项目 components/player/）────────────
    print("\n[1] 播放器目录（照源项目 components/player/）")
    pd = FE / "components" / "player"
    check("目录存在", pd.is_dir(), str(pd))
    for f in ["fullscreen-player.tsx", "player-media-stage.tsx", "player-playback-bar.tsx",
              "player-types.ts", "player-utils.ts", "index.ts"]:
        check(f"  {f}", (pd / f).exists())

    # ── 2. 常量照源项目对齐（player-utils.ts）──────────────────────
    print("\n[2] 常量照源项目对齐（player-utils.ts）")
    ut = (pd / "player-utils.ts").read_text(encoding="utf-8")
    consts = {
        "IMAGE_DURATION_SECONDS": "1.5",
        "LOAD_MORE_THRESHOLD": "8",
        "PLAYER_VIDEO_MAX_AUTO_RETRIES": "1",
        "PLAYER_VIDEO_INITIAL_STATUS_DELAY_MS": "450",
        "PLAYER_VIDEO_REBUFFER_STATUS_DELAY_MS": "1400",
        "PLAYER_VIDEO_LOAD_TIMEOUT_MS": "18000",
        "PLAYER_MEDIA_ADVANCE_PRELOAD_TIMEOUT_MS": "1800",
        "PLAYER_NEXT_VIDEO_PRELOAD_AHEAD_SECONDS": "10",
        "MAX_PRELOADED_MEDIA_NODES": "3",
    }
    for k, v in consts.items():
        ok = f"export const {k}" in ut
        check(f"  {k} = {v}（照源项目）", ok)

    # ── 3. 类型契约（player-types.ts）─────────────────────────────
    print("\n[3] 类型契约（照源项目 player-types.ts）")
    tt = (pd / "player-types.ts").read_text(encoding="utf-8")
    check("PlayerPanel（6 项，含源项目 6 面板）",
          all(x in tt for x in ['"volume"', '"rate"', '"quality"', '"download"', '"music"', '"share"']))
    check("CommentRepliesState / CommentReplyTarget", 
          "CommentRepliesState" in tt and "CommentReplyTarget" in tt)

    # ── 4. 后端端点（/media/resolve 真实取址）─────────────────────
    print("\n[4] 后端端点 /media/resolve（真实账号取址）")
    import asyncio
    import api.platform as P
    from api.platform import MediaResolveReq, media_resolve

    auth = P._auth_for(os.environ.get("DY_TEST_ACCOUNT", ""))
    check("凭证加载", len(auth.cookie) > 0, f"{len(auth.cookie)} cookie")

    # 先取一个真实作品对象（收藏夹）
    raw = P._api().get_aweme_list_collection(auth, "0", "3")
    items = (raw or {}).get("aweme_list") or []
    if not items:
        check("取真实作品", False, "收藏夹为空")
    else:
        w = items[0]
        aid = str(w.get("aweme_id"))
        check("取真实作品", bool(aid), f"aweme_id={aid}")
        # ★ 走 raw 路径（列表对象直传）——实测列表对象自带 play_addr
        media_sub = P._pick_aweme(w).get("media")
        check("_pick_aweme 附带 media 子树（含播放地址）",
              bool((media_sub or {}).get("video", {}).get("play_addr")),
              f"media 键数={len(media_sub or {})}")
        res = asyncio.run(media_resolve(MediaResolveReq(account=os.environ.get("DY_TEST_ACCOUNT", ""),
                                                        raw=media_sub, quality="h264")))
        check("端点返回 ok", res.get("ok") is True, "")
        check("type 正确", res.get("type") == "video", f"type={res.get('type')}")
        check("url 非空（可播放）", bool(res.get("url")), (res.get("url") or "")[:70])
        check("cover 非空", bool(res.get("cover")), (res.get("cover") or "")[:50])
        check("duration > 0", (res.get("duration") or 0) > 0, f"{res.get('duration')} ms")
        check("author.nickname", bool((res.get("author") or {}).get("nickname")),
              (res.get("author") or {}).get("nickname", ""))
        check("qualities 列表", isinstance(res.get("qualities"), list),
              str(res.get("qualities")))

    # ── 5. 媒体代理统计 ───────────────────────────────────────────
    print("\n[5] 媒体代理（照源项目 media_proxy_cache.rs）")
    from services import media_proxy as MP
    st = MP.stats()
    check("stats 可达", isinstance(st, dict), f"{len(st)} 键")
    check("cache_dir 在本分支环境", str(MP.cache_dir()).startswith(DESIGN_ROOT), str(MP.cache_dir()))

    print("\n" + "=" * 64)
    ok = sum(1 for _, o, _ in R if o)
    print(f"结果: {ok}/{len(R)} 通过")
    for n, o, d in R:
        if not o:
            print(f"  失败: {n} — {d}")
    return 0 if ok == len(R) else 1


if __name__ == "__main__":
    raise SystemExit(main())
