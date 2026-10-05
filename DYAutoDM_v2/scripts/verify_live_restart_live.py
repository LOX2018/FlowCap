"""实机验证：对真实运行的后端打「重启标签」链路。

前置：backend/_live_hotswap_probe.py 启动后写入 artifacts/live_hotswap_token.txt。

R1 新建配置  R2 列表可读  R3 改 max_target 落盘
R4 无运行中任务时「重启」→ idle 且如实（applied=[]，不谎报成功）
R5 删除配置
"""
import json
import os
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8077"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOKEN = open(os.path.join(ROOT, "artifacts", "live_hotswap_token.txt")).read().strip()
# room_id 必须是纯数字（后端校验：数字或直播间 URL）
RID = "735100000001"
FAILS = []
CHECKS = []


def req(method, path, body=None):
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("x-member-token", TOKEN)
    if data:
        r.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8") or "{}")


def check(name, cond, extra=""):
    CHECKS.append(name)
    print(("PASS  " if cond else "FAIL  ") + name + ((" | " + str(extra)) if extra else ""))
    if not cond:
        FAILS.append(name)


print("=== 实机：直播配置「重启」链路 (base=%s) ===" % BASE)

# R1 新建
st, r = req("POST", "/api/live/room-configs", {
    "room_id": RID, "name": "实机验证配置", "max_target": 100,
    "interval": 60, "delay": "50,120",
})
check("R1 新建配置 200/ok", st == 200 and r.get("ok"), f"status={st} body={r}")

# R2 列表
st, r = req("GET", "/api/live/room-configs")
items = r.get("items", [])
hit = [c for c in items if c.get("room_id") == RID]
check("R2 列表可读到该配置", st == 200 and len(hit) == 1, f"n={len(items)} hit={len(hit)}")

# R3 改 max_target
st, r = req("POST", "/api/live/room-configs", {
    "room_id": RID, "name": "实机验证配置", "max_target": 7,
    "interval": 60, "delay": "50,120",
})
_, lst = req("GET", "/api/live/room-configs")
now = [c for c in lst.get("items", []) if c.get("room_id") == RID]
check("R3 改 max_target=7 落盘", now and now[0].get("max_target") == 7,
      f"max_target={now[0].get('max_target') if now else None}")

# R4 重启（无运行中任务 → 必须如实返回 idle）
st, r = req("POST", f"/api/live/room-configs/{RID}/restart")
# 分层契约：外层 ok=true 表示**配置已保存**；热更是否生效必须看 restart 子对象。
# 前端正是按 restart.ok 分支提示（见 RoomConfigManager.restart），断言须与之同构。
rs = r.get("restart") or {}
check("R4 无运行中任务：外层 ok=true（配置已保存）",
      st == 200 and r.get("ok") is True, f"body.ok={r.get('ok')}")
check("R5 无运行中任务：restart.ok=false 且 reason 明示 idle（不谎报已热更）",
      rs.get("ok") is False and rs.get("engine_state") == "idle"
      and "未运行" in (rs.get("reason") or ""),
      f"restart={json.dumps(rs, ensure_ascii=False)}")
check("R6 idle 时 applied 为空（不谎报已应用）",
      rs.get("applied") == [] and r.get("applied_fields") == [],
      f"applied={rs.get('applied')} applied_fields={r.get('applied_fields')}")

# R7 非法 room_id 重启 → 如实失败
st, r = req("POST", "/api/live/room-configs/__no_such_room__/restart")
check("R7 不存在的房间重启 → 显式失败（非 200+ok=True）",
      not (st == 200 and r.get("ok") is True), f"status={st} body={r}")

# R8 删除
st, r = req("DELETE", f"/api/live/room-configs/{RID}")
_, lst = req("GET", "/api/live/room-configs")
left = [c for c in lst.get("items", []) if c.get("room_id") == RID]
check("R8 删除后列表消失", not left, f"left={len(left)}")

print(f"\n结果：{len(CHECKS)} 项，失败 {len(FAILS)}")
if FAILS:
    for f in FAILS:
        print("  - " + f)
print("全部通过" if not FAILS else "存在失败")
sys.exit(1 if FAILS else 0)
