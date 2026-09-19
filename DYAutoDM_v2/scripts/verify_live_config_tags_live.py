# -*- coding: utf-8 -*-
"""v0.43.92 真机验证（HTTP 打真 backend，三态变更 + 热更语义）。

## 设计契约（被验的「应然」）
- **配置标签可并存多条**：一条标签 = 一套参数；目标直播间**引用**标签，不复制参数。
- **目标直播间独立管理**：只存 room_id / 备注 / tag_id 引用 / enabled，**零参数**。
- **解绑 ≠ 删除**：解绑只清引用；删标签自动反向解绑目标直播间（目标记录保留）。
- **唯一可写入口**：参数只在 `/api/live/config-tags` 写。
- **热更三态如实**：引擎运行中 → `restart.ok=true` 列实际生效字段；未运行 →
  `restart.ok=false` 明说「已保存，点开始后生效」；异常 → 显式失败（LIVE-021）。

## 环境（铁律：design 分支专属环境 + 会员态齐全）
    DY_APP_ROOT = C:\temp\dyautodm_design
    DY_MEMBER / DY_MEMBER_KEY 从该环境的 members/.session.json 读
本脚本**自带环境门禁**：不接受 DY_APP_ROOT 指向主分支环境，也不允许为空。

## 跑法
    python scripts/verify_live_config_tags_live.py
先手工起 backend（见脚本末尾打印的命令），本脚本只发 HTTP 并断言。
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESIGN_ROOT = r"C:\temp\dyautodm_design"
FORBIDDEN = (r"C:\temp\dyautodm_test",)

# ── 环境门禁（铁律 §三·0：绝不污染主分支实例）──────────────────────────────
app_root = os.path.abspath(os.environ.get("DY_APP_ROOT") or "")
if app_root in ("", os.path.abspath(".")):
    raise SystemExit("环境门禁失败：未设置 DY_APP_ROOT（禁止用默认/会话 cwd 跑真机验证）")
if app_root in [os.path.abspath(x) for x in FORBIDDEN]:
    raise SystemExit(f"环境门禁失败：DY_APP_ROOT 指向主分支环境 {app_root}")

BASE = os.environ.get("DY_BACKEND_URL", "http://127.0.0.1:8000")

PASS = FAIL = 0
FAILED: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"  FAIL {name}  {detail}")


def req(method: str, path: str, body=None, token: str = "", timeout: float = 20.0):
    # RFC 3986：请求目标必须是 ASCII —— 中文标签 id 必须先百分号编码。
    # （实测踩坑：裸发中文路径 → uvicorn 写响应头 UnicodeEncodeError → 500；
    #   看代码像后端 bug，实际是客户端的协议层缺陷。）
    path = urllib.parse.quote(path, safe="/?&=:@")
    data = None
    headers = {"Content-Type": "application/json"}
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    if token:
        headers["X-Member-Token"] = token
    r = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(r, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8") or "{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}


# ── 取会话 token（同进程/同盘的 .session.json；backend 启动时已 restore）────
tok = ""
try:
    sj = json.load(open(rf"{DESIGN_ROOT}\members\.session.json", encoding="utf-8"))
    tok = sj.get("token") or ""
except Exception as e:
    print(f"  warn 读 members/.session.json 失败: {e}")

st, _ = req("GET", "/api/version")
if st != 200:
    raise SystemExit(
        f"后端不可达（GET /api/version -> {st}）。请先起 backend：\n"
        f'  cd {os.path.join(ROOT, "backend")}\n'
        f'  DY_APP_ROOT={DESIGN_ROOT} python main.py'
    )
print(f"后端在线：{BASE}  app_root={app_root}  token={'有' if tok else '无'}\n")

TS = int(time.time())
RID_A = str(880000000000 + TS % 100000)   # 目标直播间 A
RID_B = str(881000000000 + TS % 100000)   # 目标直播间 B
TAG_A = "标准-快"           # 名称型标签（证明多标签并存）
TAG_B = f"保守-{TS % 10000}"  # 每次跑用新键，避免与历史数据互相干扰

print("A. 配置标签：多标签并存 + 参数写入")
st, r = req("POST", "/api/live/config-tags",
            {"id": TAG_A, "name": TAG_A, "max_target": 50, "interval": 45,
             "delay": "40,80", "dm_pool": [{"text": f"文案A-{TS}", "enabled": True}]}, tok)
check("A1 建标签A（名称型键）", st == 200 and r.get("ok"), f"{st} {r}")
st, r = req("POST", "/api/live/config-tags",
            {"id": TAG_B, "name": "保守慢速", "max_target": 8, "interval": 300,
             "delay": "300,600", "auto_link_mic": False}, tok)
check("A2 建标签B（并存，互不覆盖）", st == 200 and r.get("ok"), f"{st} {r}")
st, r = req("GET", "/api/live/config-tags", token=tok)
items = {x.get("id"): x for x in (r.get("items") or [])}
check("A3 两条标签同时存在（多配置标签）",
      TAG_A in items and TAG_B in items, f"ids={list(items)[:8]}")
check("A3b 参数各自独立（A=50/B=8）",
      items.get(TAG_A, {}).get("max_target") == 50
      and items.get(TAG_B, {}).get("max_target") == 8,
      f"A={items.get(TAG_A, {}).get('max_target')} B={items.get(TAG_B, {}).get('max_target')}")

print("\nB. 目标直播间：独立管理 + 引用式绑定（零参数）")
st, r = req("POST", "/api/live/target-rooms",
            {"room_id": RID_A, "name": "验证房A", "tag_id": TAG_A}, tok)
check("B1 添加目标直播间并绑定标签A", st == 200 and r.get("ok"), f"{st} {r}")
check("B1b 目标记录不含任何参数字段（只存引用）",
      not ({"max_target", "interval", "delay", "dm_pool"} & set((r.get("target") or {}).keys())),
      f"keys={sorted((r.get('target') or {}).keys())}")
st, r = req("GET", f"/api/live/target-rooms/{RID_A}/resolve", token=tok)
check("B2 resolve 透出绑定标签的参数（引用生效）",
      st == 200 and (r.get("params") or {}).get("max_target") == 50, f"{st} {r}")
st, r = req("POST", "/api/live/target-rooms",
            {"room_id": RID_B, "name": "验证房B", "tag_id": "不存在的标签"}, tok)
check("B3 绑定不存在的标签被拒（防悬空引用）", r.get("ok") is False, f"{st} {r}")
st, r = req("POST", "/api/live/target-rooms",
            {"room_id": RID_A, "tag_id": None}, tok)
check("B4 就地解绑（不删标签）", st == 200 and (r.get("target") or {}).get("tag_id") is None, f"{r}")
st, r = req("GET", "/api/live/config-tags", token=tok)
check("B4b 解绑后标签A 仍在（解绑 ≠ 删除）",
      TAG_A in {x.get("id") for x in (r.get("items") or [])})

print("\nC. 改绑 + 删标签的反向解绑")
st, r = req("POST", "/api/live/target-rooms",
            {"room_id": RID_A, "tag_id": TAG_B}, tok)
check("C1 改绑到标签B", st == 200 and (r.get("target") or {}).get("tag_id") == TAG_B, f"{r}")
st, r = req("DELETE", f"/api/live/config-tags/{TAG_B}", token=tok)
check("C2 删标签B → 返回自动解绑清单", r.get("unbound_rooms") == [RID_A], f"{st} {r}")
st, r = req("GET", "/api/live/target-rooms", token=tok)
tgt = {x.get("room_id"): x for x in (r.get("items") or [])}
check("C3 目标直播间记录保留（未被连带删除）", RID_A in tgt, f"rooms={list(tgt)[:8]}")
check("C3b 引用已清空（无悬空引用）", tgt.get(RID_A, {}).get("tag_id") is None, f"{tgt.get(RID_A)}")
st, r = req("GET", f"/api/live/target-rooms/{RID_A}/resolve", token=tok)
check("C3c 解绑后 resolve 不再给出参数（params 空）", not (r.get("params") or {}), f"{r}")

print("\nD. 热更三态如实（不谎报）")
st, r = req("POST", f"/api/live/config-tags/{TAG_A}/restart", token=tok)
j = r if isinstance(r, dict) else {}
rs = j.get("restart") or {}
check("D1 restart 外层 ok=true（配置已保存）", st == 200 and j.get("ok") is True, f"{st} {j}")
check("D2 restart.ok=false 且 reason 非空（引擎未运行时不假报已热更）",
      rs.get("ok") is False and bool(rs.get("reason")), f"restart={rs}")
check("D2b 未运行时不谎报已生效字段", not (j.get("applied_fields") or []), f"{j.get('applied_fields')}")
st, r = req("POST", "/api/live/config-tags/__no_such__/restart", token=tok)
check("D3 不存在的标签 → 显式失败", r.get("ok") is False, f"{st} {r}")

print("\nE. 兼容前缀（老前端 / 既有脚本 / 任务中心复用入口不破）")
st, r = req("GET", "/api/live/room-configs", token=tok)
check("E1 旧前缀读到同一份存储（含标签A）",
      st == 200 and TAG_A in {x.get("id") for x in (r.get("items") or [])}, f"{st}")
st, r = req("POST", "/api/live/room-configs",
            {"room_id": str(882000000000 + TS % 100000), "name": "兼容写入", "max_target": 3}, tok)
check("E2 旧前缀写入后新前缀可见（同一份 kv）", r.get("ok"), f"{st} {r}")
st, r = req("GET", "/api/live/config-tags", token=tok)
check("E3 新前缀能看到旧前缀写的数据",
      str(882000000000 + TS % 100000) in {x.get("id") for x in (r.get("items") or [])})

print("\nF. 中文标签 id 经百分号编码可端到端（前端 encodeURIComponent 走的就是这条）")
zh_id = f"中文标签-{TS % 10000}"
st, r = req("POST", "/api/live/config-tags",
            {"id": zh_id, "name": "中文名", "max_target": 4}, tok)
check("F0 中文 id 可创建", st == 200 and r.get("ok"), f"{st} {r}")
st, r = req("POST", f"/api/live/config-tags/{zh_id}/restart", token=tok)
check("F0b 中文 id 的 restart 能命中（路径解码正常，非 404/500）",
      st == 200 and r.get("ok") is True, f"{st} {r}")
st, r = req("DELETE", f"/api/live/config-tags/{zh_id}", token=tok)
check("F0c 中文 id 可删除（无 500）", st == 200 and r.get("ok") is True, f"{st} {r}")

print("\nG. 清理（只删本次验证对象，逐条精确匹配）")
for tid in (TAG_A, str(882000000000 + TS % 100000)):
    req("DELETE", f"/api/live/config-tags/{tid}", token=tok)
req("DELETE", f"/api/live/target-rooms/{RID_A}", token=tok)
req("DELETE", f"/api/live/target-rooms/{RID_B}", token=tok)
st, r = req("GET", "/api/live/target-rooms", token=tok)
left = [x.get("room_id") for x in (r.get("items") or []) if str(x.get("room_id", "")).startswith(("880", "881", "882"))]
check("G1 本次验证对象已清理（无残留）", not left, f"left={left}")

print(f"\nPASS={PASS} FAIL={FAIL}")
if FAILED:
    print("失败项：")
    for x in FAILED:
        print("  -", x)
sys.exit(1 if FAIL else 0)
