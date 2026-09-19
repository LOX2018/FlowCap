# -*- coding: utf-8 -*-
"""v0.43.92 验证：直播「配置标签 / 目标直播间」分离（四层）。

用户诉求（2026-09-19 原话）：
  「直播监听的配置管理修改只能对现有的配置反复覆盖，没有实现多配置标签的功能，
   目标直播间可以管理，但别放到配置管理中，单独加一个目标直播间管理，
   在该页面中选择是否绑定配置。」

四层（每层独立判据，缺一层就说明只有一层证据）：
  A 静态契约   —— 源码里该做的结构真的在（无参数写进目标直播间 / 解绑≠删除 …）
  B 真模块     —— 隔离 DB 里真跑 live_config + target_rooms（不启任何服务）
  C 真 HTTP    —— 把两条 router 挂到 TestClient 上真发请求（含 400/404 语义）
  D 前端契约   —— 直播页三子视图、旧弹窗已删、配置标签页为整页

跑法：cd backend && python ../scripts/verify_live_config_tags.py
（不启动 uvicorn、不碰浏览器、不碰会员环境；DY_APP_ROOT 强制指向临时目录）
"""
import ast
import os
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BK = os.path.join(ROOT, "backend")
FE = os.path.join(ROOT, "frontend", "src")

# ── 环境隔离门禁（铁律：验证脚本自设 DY_APP_ROOT，绝不污染用户实例）─────────
_TMP = os.path.join(tempfile.gettempdir(), "dyautodm_lctag_verify")
FORBIDDEN = (r"C:\temp\dyautodm_design", r"C:\temp\dyautodm_test")
if os.path.abspath(_TMP) in [os.path.abspath(x) for x in FORBIDDEN]:
    raise SystemExit("环境门禁失败：临时目录与用户环境重合")
os.environ["DY_APP_ROOT"] = _TMP
shutil.rmtree(_TMP, ignore_errors=True)
os.makedirs(_TMP, exist_ok=True)

sys.path.insert(0, BK)

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


def src(rel: str, base: str = BK) -> str:
    with open(os.path.join(base, rel), "rb") as f:
        return f.read().decode("utf-8")


def tree(rel: str, base: str = BK) -> ast.Module:
    return ast.parse(src(rel, base))


# ══════════════════════════════════════════════════════════════════════════
print("A. 静态契约")
lc = src("api/live_config.py")
tr = src("api/target_rooms.py")
main = src("main.py")
live_fe = src("components/live/live-page.tsx", FE)
rcp = src("components/live/RoomConfigPage.tsx", FE)
trp = src("components/live/TargetRoomPage.tsx", FE)
cli = src("api/client.ts", FE)

check("A1 live_config 标签键支持非数字（多配置标签的前提）",
      "def normalize_tag_key" in lc and "_KEY_RE" in lc,
      "normalize_tag_key/_KEY_RE 缺失")
check("A2 旧「只收纯数字否则拒绝」的归一函数已不存在",
      "def _norm_room_id" not in lc,
      "仍存在只接受数字的 _norm_room_id")
check("A3 标签体含 id 字段 + room_id 兼容别名",
      'id: str = ""' in lc and '"room_id": tag_id' in lc,
      "id/room_id 兼容别名缺失")
check("A4 空键走 new_tag_id 生成（新建标签路径）",
      "def new_tag_id" in lc and "new_tag_id()" in lc)

# —— 职责分离：目标直播间侧不得出现任何参数落盘 ——
tr_params = [k for k in ("max_target", "interval", "delay", "dm_pool",
                         "force_rescan", "auto_link_mic", "link_mic_mode")
             if k in tr]
check("A5 目标直播间侧无任何参数字段落盘", not tr_params, f"命中参数名={tr_params}")
check("A6 目标直播间只存引用 tag_id", '"tag_id": tag_id or None' in tr)
check("A7 绑定目标存在性校验（防悬空引用）",
      "live_config.get_tag(tag_id)" in tr and "不存在" in tr)
check("A8 解绑 ≠ 删除：unbind_tag 只置 None 不删记录",
      "t[\"tag_id\"] = None" in tr and "del data" not in tr.split("def unbind_tag")[1][:1200])
check("A9 目标直播间存储与参数存储分属两个 kv（只经 _KV_KEY 写盘）",
      '_KV_KEY = "live_target_rooms"' in tr
      and 'get_kv_json("live_room_configs")' not in tr
      and 'set_kv_json("live_room_configs"' not in tr,
      "target_rooms 里直接读写了参数 kv")

# —— 反向解绑接线 ——
check("A10 删标签时反向解绑目标直播间",
      "_unbind_from_targets(rid)" in lc and "def _unbind_from_targets" in lc)
check("A11 live_url 可留空（按目标直播间推导，不再强制拼房间号）",
      "def resolve_live_url" in lc and "return \"\"" in lc)

# —— 路由注册（规范前缀 + 兼容别名 + 目标直播间）——
check("A12 规范前缀 /api/live/config-tags 已注册",
      'prefix="/api/live/config-tags"' in main)
check("A13 兼容前缀 /api/live/room-configs 仍注册（老前端/脚本/复用入口不破）",
      'prefix="/api/live/room-configs"' in main)
check("A14 /api/live/target-rooms 已注册",
      'prefix="/api/live/target-rooms"' in main)
check("A15 两条前缀挂同一 router（不存在第二份存储）",
      main.count("live_config_api.router") == 2)

# —— 错误码 ——
import errcode as _ec  # noqa: E402

_d = (getattr(_ec, "CODE_DESIGN", {}) or {}).get("LIVE-022") or {}
_miss = [k for k in ("design", "contract", "deviation", "chain", "root", "verify") if not _d.get(k)]
check("A16 LIVE-022 已注册且六段契约齐全",
      "LIVE-022" in getattr(_ec, "ERRCODES", {}) and not _miss,
      f"缺字段={_miss}")

# —— 前端契约 ——
check("A17 直播页引入两个子视图组件",
      'from "./RoomConfigPage"' in live_fe and 'from "./TargetRoomPage"' in live_fe)
check("A18 旧弹窗组件文件已移除（弹窗 = 反复覆盖的载体）",
      not os.path.exists(os.path.join(FE, "components", "live", "RoomConfigManager.tsx")))
check("A19 直播页三子视图（监听 / 目标直播间 / 配置标签）",
      'useState<"monitor" | "targets" | "configs">' in live_fe and '"目标直播间"' in live_fe)
check("A20 配置标签页是整页（无 modal 外壳、无 onClose）",
      "fixed inset-0 z-50" not in rcp and "onClose" not in rcp and "data-od-id=\"live-config-tags\"" in rcp)
check("A21 配置标签页保留全部可写控件（参数唯一入口）",
      all(k in rcp for k in ("发送上限", "间隔（秒）", "延迟抖动（秒）", "私信词库",
                             "强制重扫", "自动申请连麦")) and "restartRoomConfig" in rcp)
check("A22 目标直播间页有「是否绑定配置」选择 + 解绑项",
      "不绑定配置" in trp and "bindOnly" in trp and "deleteTargetRoom" in trp)
check("A23 目标直播间页无参数输入控件（只读展示 + 跳转）",
      not re.search(r"onChange=\{\(e\) => setDraft\(\{ \.\.\.draft, (max_target|interval|delay|dm_pool)", trp),
      "目标直播间页出现了参数编辑绑定")
check("A24 前端 API 指向新前缀，目标直播间 API 齐备",
      'request("/api/live/config-tags")' in cli
      and 'request("/api/live/target-rooms")' in cli
      and "resolveTargetRoom" in cli
      and "export interface TargetRoom" in cli)

# ══════════════════════════════════════════════════════════════════════════
print("B. 真模块（隔离 DB，不起服务）")
import asyncio  # noqa: E402
import database  # noqa: E402
from api import live_config as LC  # noqa: E402
from api import target_rooms as TR  # noqa: E402

_run = asyncio.run  # 端点/模块级入口都是 async → 统一包装


def reset_kv():
    conn = database.get_db()
    conn.execute("DELETE FROM kv_store")
    conn.commit()


reset_kv()

# B1 多配置标签：数字键 + 中文键并存
r1 = _run(LC.save_room_config(LC.RoomConfigBody(room_id="840377749201", name="标准", max_target=50)))
r2 = _run(LC.save_room_config(LC.RoomConfigBody(room_id="保守-慢速", name="保守", max_target=10)))
check("B1 可并存多条标签（数字键 + 名称键）",
      r1.get("ok") and r2.get("ok") and r1["config"]["id"] == "840377749201"
      and r2["config"]["id"] == "保守-慢速",
      f"{r1} | {r2}")
check("B1b 两条标签互不覆盖", r1["config"]["max_target"] == 50 and r2["config"]["max_target"] == 10)

# B2 同名键再存 = 更新（upsert），不是新增
r3 = _run(LC.save_room_config(LC.RoomConfigBody(room_id="保守-慢速", max_target=12)))
lst = LC._load_all()
check("B2 同键保存为更新而非新增", r3["config"]["max_target"] == 12 and len(lst) == 2, f"n={len(lst)}")
check("B2b 更新时未传的字段沿用旧值（name 保留）", lst["保守-慢速"].get("name") == "保守")

# B3 URL 归一
r4 = _run(LC.save_room_config(LC.RoomConfigBody(room_id="https://live.douyin.com/992931212705", name="URL 键")))
check("B3 直播间 URL 归一为数字键", r4["config"]["id"] == "992931212705")

# B4 空键生成新 id
r5 = _run(LC.save_room_config(LC.RoomConfigBody(room_id="", name="自动 id")))
check("B4 空键生成 lc_ 前缀新 id", str(r5["config"]["id"]).startswith("lc_"))

# B5 live_url 可留空
check("B5 live_url 留空不强制拼房间号",
      LC._load_all()["保守-慢速"].get("live_url") == "")
check("B5b 解析时按目标房间推导 live_url",
      LC.resolve_live_url(LC._load_all()["保守-慢速"], "840377749201")
      == "https://live.douyin.com/840377749201")

# B6 目标直播间：绑定 + 解析
t1 = _run(TR.save_target_room(TR.TargetRoomBody(room_id="840377749201", name="张老师", tag_id="保守-慢速")))
check("B6 目标直播间可绑定标签", t1.get("ok") and t1["target"]["tag_id"] == "保守-慢速")
res = TR.resolve("840377749201")
check("B6b resolve 返回标签参数",
      res["tag"] and res["params"].get("max_target") == 12, f"{res}")

# B7 目标直播间记录里没有参数（职责分离的硬判据）
check("B7 目标直播间记录不含任何参数字段",
      not ({"max_target", "interval", "delay", "dm_pool"} & set(t1["target"].keys())),
      f"keys={sorted(t1['target'].keys())}")

# B8 绑定不存在的标签 → 拒绝
t2 = _run(TR.save_target_room(TR.TargetRoomBody(room_id="777777", tag_id="no-such-tag")))
check("B8 绑定不存在的标签被拒（防悬空引用）", not t2.get("ok"), f"{t2}")

# B9 解绑 ≠ 删除
_run(TR.save_target_room(TR.TargetRoomBody(room_id="840377749201", tag_id=None)))
check("B9 解绑后标签仍存在", LC.get_tag("保守-慢速") is not None)
check("B9b 解绑后目标直播间仍在（只是 tag_id=None）",
      TR._load_all().get("840377749201", {}).get("tag_id") is None)

# B10 反向解绑：删标签 → 目标直播间保留 + 引用清空
_run(TR.save_target_room(TR.TargetRoomBody(room_id="840377749201", tag_id="保守-慢速")))
d = _run(LC.delete_room_config("保守-慢速"))
check("B10 删标签返回已解绑清单", d.get("ok") and d.get("unbound_rooms") == ["840377749201"], f"{d}")
check("B10b 标签已删除", LC.get_tag("保守-慢速") is None)
check("B10c 目标直播间记录保留（未被连带删除）",
      TR._load_all().get("840377749201") is not None)
check("B10d 引用已清空（无悬空引用）",
      TR._load_all()["840377749201"]["tag_id"] is None)

# B11 删目标直播间不删标签
_run(TR.save_target_room(TR.TargetRoomBody(room_id="840377749201", tag_id="840377749201")))
_run(TR.delete_target_room("840377749201"))
check("B11 移除目标直播间后配置标签保留", LC.get_tag("840377749201") is not None)

# B12 重启热更仍走同一份存储（契约未破坏）
check("B12 restart_core 可被端点复用且读同一 kv",
      "def restart_core" in lc and 'restart_core(request, room_id)' in lc)

# ══════════════════════════════════════════════════════════════════════════
print("C. 真 HTTP（TestClient 挂载两条 router，含 400/404 语义）")
try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    reset_kv()
    app = FastAPI()
    app.state.adm = None  # 引擎未初始化 → restart 必须如实回 idle，不谎报
    app.include_router(LC.router, prefix="/api/live/config-tags")
    app.include_router(LC.router, prefix="/api/live/room-configs")
    app.include_router(TR.router, prefix="/api/live/target-rooms")
    c = TestClient(app)

    r = c.post("/api/live/config-tags", json={"room_id": "111222", "name": "标签A", "max_target": 7})
    check("C1 POST 建标签 → 200/ok", r.status_code == 200 and r.json().get("ok"), r.text[:200])
    r = c.post("/api/live/room-configs", json={"room_id": "333444", "name": "标签B"})
    check("C2 兼容前缀可写同一份存储", r.status_code == 200 and r.json().get("ok"), r.text[:200])
    r = c.get("/api/live/config-tags")
    ids = [x["id"] for x in r.json().get("items", [])]
    check("C3 双前缀共读同一份（两条都在）", sorted(ids) == ["111222", "333444"], f"{ids}")
    r = c.get("/api/live/room-configs")
    check("C3b 兼容前缀读到同两条", sorted(x["id"] for x in r.json()["items"]) == ["111222", "333444"])

    r = c.post("/api/live/config-tags", json={"room_id": "!!非法!!"})
    check("C4 非法标签键 → 显式失败（非静默）",
          r.status_code == 200 and r.json().get("ok") is False, r.text[:200])

    r = c.post("/api/live/target-rooms", json={"room_id": "111222", "name": "目标房", "tag_id": "111222"})
    check("C5 POST 目标直播间 → 200/ok", r.status_code == 200 and r.json().get("ok"), r.text[:200])
    r = c.post("/api/live/target-rooms", json={"room_id": "999999", "tag_id": "nope"})
    check("C6 绑定不存在标签 → 显式失败", r.json().get("ok") is False, r.text[:200])
    r = c.get("/api/live/target-rooms/111222/resolve")
    check("C7 resolve 透出绑定标签参数",
          r.status_code == 200 and r.json().get("params", {}).get("max_target") == 7, r.text[:300])
    r = c.post("/api/live/config-tags/111222/restart")
    j = r.json()
    check("C8 restart 引擎未运行 → 外层 ok=True 但 restart.ok=false（不谎报热更）",
          r.status_code == 200 and j.get("ok") and j.get("restart", {}).get("ok") is False,
          r.text[:300])
    r = c.post("/api/live/config-tags/__nope__/restart")
    check("C9 不存在标签 restart → 显式失败", r.json().get("ok") is False, r.text[:200])
    r = c.delete("/api/live/config-tags/111222")
    check("C10 删标签 → 解绑清单透出", r.json().get("unbound_rooms") == ["111222"], r.text[:200])
    r = c.get("/api/live/target-rooms")
    t = r.json()["items"][0]
    check("C10b 目标直播间保留且已解绑",
          t["room_id"] == "111222" and t["tag_id"] is None, f"{t}")
except Exception as e:  # 依赖缺失不应伪装成通过
    check("C0 TestClient 层可运行", False, f"{type(e).__name__}: {e}")

# ══════════════════════════════════════════════════════════════════════════
print(f"\nPASS={PASS} FAIL={FAIL}")
if FAILED:
    print("失败项：")
    for x in FAILED:
        print("  -", x)
print("env_root=", os.environ["DY_APP_ROOT"])
sys.exit(1 if FAIL else 0)
