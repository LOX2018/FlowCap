#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""受保护媒体渲染门禁（FE 家族）—— 防「裸 <img> 打受保护端点」复发。

## 为什么需要它（2026-09-27 全库审计 FE-1）

`backend/main.py` 的会员门禁中间件对**全部** `/api/*` 生效（豁免清单
`_MEMBER_EXEMPT` 仅含 member/status/ready/ws/errcodes/version/docs）。

而 `<img src="...">` **无法携带自定义请求头**（`X-Member-Token`）——
⇒ 前端若用**裸 `<img>`** 指向受保护端点，浏览器必然拿到 **401**，
表现为**静默破图**（无报错、无提示，用户只看到空白）。

实测证据（TestClient）：
    GET /api/accounts/qr-image（无令牌） → 401 {"detail":"未登录或会话已过期"}
    GET /api/accounts/qr-image（有令牌） → 200 image/png

历史上真实踩过两次：
  ① `/api/messages/origin_image/...`（靠 onError 降级缩略图掩盖，长期静默破损）
     ⇒ 2026-09-17 引入 `AuthedImg` / `AuthedVideo`（B 方案）；
  ② `/api/accounts/qr-image`（登录二维码）⇒ 2026-09-27 本次修复。

## 判据（R1~R3）

- **R1**：前端不得存在「裸 `<img>` 指向受保护端点」。
  判定：扫描 `frontend/src/**/*.tsx`，找出所有 `<img` 标签，
  取其 `src` 表达式；若表达式里出现**本机 API 路径**（`/api/`）
  且**不是外部变量形态**……——注意：静态扫描无法判断变量值，
  故 R1 只拦**字面量形态**（`src="/api/..."` / `` src={`/api/...`} ``），
  这是**确定性**的、零误报的判据。
- **R2**：**白名单内**的「受保护媒体渲染器」不得出现裸 `<img>`。
  白名单纳入判据 = 该文件的 src **确定**来自本机受保护端点
  （目前仅 `LoginDialog.tsx`：`/api/accounts/qr-image`）。
  ⚠️ 不得因「看起来像媒体组件」就纳入 —— 渲染抖音 CDN 直链的组件
  （如 `player-media-stage.tsx` 的 `images`）纳入即假阳性。
- **R3**：`AuthedImg` 的 `isLocalApiUrl` 契约仍在位
  （若它被改窄成只认 `/api/messages/`，则新端点会静默绕过鉴权）。

## 明确不做（避免误报）

❌ **不拦外部 CDN 地址的裸 `<img>`** —— 抖音封面/头像（`url_list`）是
   `https://...` 外部地址，**本就不需要令牌**，`AuthedImg` 的契约也是
   「外部地址原样直用」。实测确认以下位置**正确**（src 来自抖音 CDN）：
   `crawl-page.tsx:360`（`url_list[-1]`）、`RoomManagePage.tsx:378`
   （`_pick_cover` 断言 `startswith("http")`）、`platform-cards.tsx:17/55`
   （`url_list[0]`）、`message-bubble.tsx:236`（封面色）、
   `player-media-stage.tsx:205/261`（`m.get("images")`，无代理改写）。

用法：
    python scripts/check_media_auth_render.py          # 人读
    python scripts/check_media_auth_render.py --json   # 机器读
退出码：0 = 无阻断；1 = 有阻断。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)                      # FlowCap/
_FE = os.path.join(_ROOT, "frontend", "src")

# 「渲染**受保护媒体**」的组件白名单：这些文件里**不允许**出现裸 <img>/<video>。
#
# ⚠️ 白名单的纳入判据必须是「该文件渲染的 src **确定**来自本机受保护端点」，
#    而不是「它看起来是媒体组件」。首版曾把 player-media-stage.tsx 纳入，
#    但其裸 <img> 渲染的是**抖音 CDN 直链**（后端 media/resolve 的
#    `images: m.get("images")` 无任何本机代理改写，实测 grep 零命中）
#    ⇒ 纳入即**假阳性**（负控实测：该文件永远报红，把真信号淹没）。
#    只有「src 由**本机 /api/ 端点**下发」的文件才纳入。
_PROTECTED_RENDERERS = {
    "components/accounts/LoginDialog.tsx":
        "登录二维码（src 为 /api/accounts/qr-image，受会员门禁保护）",
}

# 裸标签的字面量受保护 src 形态（确定性判据，零误报）
# ⚠️ 匹配范围必须覆盖**多行 + 模板字符串**形态：
#     <img
#       src={`/api/accounts/qr-image?path=...`}
#     />
#   首版正则写成 `<img\b[^>]*?src\s*=\s*(?:"|\{`)\s*/api/`，
#   而 `[^>]*?` 里的 `\s*` 被 `<img` 之后的换行+缩进吃掉仍能过 —— 但**实测漏报**：
#   注入「AuthedImg → 裸 <img>」时门禁仍全绿。故改为「标签块内任意位置出现 /api/ 字面量」。
#   判据锚点：`<img` 到该标签结束（`>`）之间，出现 `/api/`。
_LITERAL_PROTECTED = re.compile(r"<img\b(?:(?!>)[\s\S])*?/api/", re.S)


def _iter_tsx(root: str):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in
                       {"node_modules", "dist", "build", ".vite"}]
        for fn in filenames:
            if fn.endswith((".tsx", ".ts")):
                yield os.path.join(dirpath, fn)


def _strip_tsx_comments(s: str) -> str:
    """剥离 TSX 注释，**等长替换为空格**（保持行号不变）。

    ⚠️ 必须做：首版判据直接扫原文，把我自己写的**说明性注释**
    （注释里引用了旧代码 `<img src="/api/..."`）当成真缺陷报红 —— 假阳性。
    注释被剥离后仍要保留换行，否则行号全错、无法定位。
    """
    out = list(s)
    # {/* ... */} 块注释
    for m in re.finditer(r"\{/\*[\s\S]*?\*/\}", s):
        for i in range(m.start(), m.end()):
            if out[i] != "\n":
                out[i] = " "
    # // 行注释（注意别误伤 URL 里的 //：要求 // 前不是 ':'）
    for m in re.finditer(r"(?<!:)//[^\n]*", "".join(out)):
        for i in range(m.start(), m.end()):
            if out[i] != "\n":
                out[i] = " "
    return "".join(out)


def r1_literal_protected_img(root: str) -> tuple[bool, list[str]]:
    """R1：裸 <img> 字面量指向 /api/（确定性缺陷）。"""
    hits: list[str] = []
    for p in _iter_tsx(os.path.join(root, "frontend", "src")):
        try:
            raw = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        s = _strip_tsx_comments(raw)          # ★ 去注释（防注释污染，保留行号）
        for m in _LITERAL_PROTECTED.finditer(s):
            ln = s[:m.start()].count("\n") + 1
            rel = os.path.relpath(p, root).replace("\\", "/")
            hits.append(f"{rel}:{ln} 裸 <img> 字面量指向 /api/")
    return (len(hits) == 0), hits


def r2_protected_components_use_authed(root: str) -> tuple[bool, list[str]]:
    """R2：已知受保护媒体组件必须用 Authed* 家族，不得裸 <img>。"""
    hits: list[str] = []
    for rel, why in _PROTECTED_RENDERERS.items():
        p = os.path.join(root, "frontend", "src", rel.replace("/", os.sep))
        if not os.path.exists(p):
            continue
        s = open(p, encoding="utf-8", errors="replace").read()
        code = re.sub(r"\{/\*.*?\*/\}", "", s, flags=re.S)      # 去注释（注释里提及不算）
        code = re.sub(r"//[^\n]*", "", code)
        # 找真正参与渲染的裸 <img（排除 AuthedImg 自身标签）
        # ⚠️ 不能只判断「文件里是否出现 AuthedImg 字样」——
        #    注入「把 <AuthedImg 换成 <img」后，import 行里的 `AuthedImg` 仍在，
        #    旧判据会漏报（实测踩到）。必须看**真实渲染标签**：
        #    裸 <img 数 > 0 即报红（受保护渲染器**不允许**出现裸 <img>）。
        bare = [m for m in re.finditer(r"<img\b", code)]
        if bare:
            ln = code[:bare[0].start()].count("\n") + 1
            hits.append(
                f"{rel}:{ln} 受保护媒体渲染器出现裸 <img>（{why}）"
                f" —— 必须改用 AuthedImg（裸标签无法携带 X-Member-Token ⇒ 必 401）")
    return (len(hits) == 0), hits


def r3_islocal_contract(root: str) -> tuple[bool, list[str]]:
    """R3：isLocalApiUrl 契约必须仍覆盖任意 /api/ 前缀。"""
    p = os.path.join(root, "frontend", "src", "api", "client.ts")
    if not os.path.exists(p):
        return False, ["未找到 frontend/src/api/client.ts"]
    s = open(p, encoding="utf-8", errors="replace").read()
    m = re.search(r"export function isLocalApiUrl[\s\S]{0,400}?\n\}", s)
    if not m:
        return False, ["未找到 isLocalApiUrl 定义"]
    body = m.group(0)
    ok = 'startsWith("/api/")' in body
    return ok, ([] if ok else [
        "isLocalApiUrl 不再对任意 /api/ 前缀返回 true "
        "⇒ 新增受保护端点会被静默绕过鉴权（须保持 startsWith(\"/api/\")）"])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--root", default=_ROOT)
    a = ap.parse_args()
    root = a.root

    results = [
        ("R1 无裸 <img> 字面量指向受保护端点", *r1_literal_protected_img(root)),
        ("R2 受保护媒体组件走 Authed* 家族", *r2_protected_components_use_authed(root)),
        ("R3 isLocalApiUrl 契约在位", *r3_islocal_contract(root)),
    ]

    if a.json:
        print(json.dumps({
            "ok": all(ok for _, ok, _ in results),
            "checks": [{"name": n, "ok": ok, "detail": d} for n, ok, d in results],
        }, ensure_ascii=False, indent=2))
    else:
        print("=" * 62)
        print("受保护媒体渲染门禁 —— 裸 <img> 必 401（FE 家族）")
        print("=" * 62)
        print(f"  前端根 : {os.path.join(root, 'frontend', 'src')}")
        print("-" * 62)
        for n, ok, d in results:
            print(f"  [{'PASS' if ok else 'FAIL'}] {n}")
            for x in d:
                print(f"          → {x}")
        bad = [n for n, ok, _ in results if not ok]
        print("-" * 62)
        print(f"  合计: {len(results)} 项，通过 {len(results) - len(bad)}，未通过 {len(bad)}")
    return 1 if any(not ok for _, ok, _ in results) else 0


if __name__ == "__main__":
    sys.exit(main())
