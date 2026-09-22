# -*- coding: utf-8 -*-
#
# ╔══════════════════════════════════════════════════════════╗
# ║  [退役声明]  此文件已退役                               ║
# ║                                                         ║
# ║  退役原因: 判据写死 v0.43.93，策略体系已重构为          ║
# ║  v0.44.41 ADR-002 §5.4 策略中心，无法在当前产品上跑通   ║
# ║                                                         ║
# ║  替代方案: 参见 ADR-002 §5.4 策略中心测试规范           ║
# ╚══════════════════════════════════════════════════════════╝
#
"""对**部署产物**（:8000）跑真机 HTTP 验证 —— v0.43.93 纯策略契约（已退役）。

判据（全部走真实 HTTP，非静态分析）：
  1. 版本/端点面：0.43.93；config-tags 4 端点；target-rooms **不存在**
  2. 多策略并存：建两条 → 列表两条 → 参数互不覆盖
  3. 策略体**零身份字段**：不返回 room_id / live_url / force_rescan
  4. 中文策略名可作 id（URL 编码往返）—— 用户「多配置标签」诉求的直接判据
  5. restart/apply 携带 {room_id, account} 上下文；respond 不谎报
  6. 删除只删自己（无跨模块解绑副作用）
跑法：python scripts/verify_live_strategy_live.py [port]
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

PORT = sys.argv[1] if len(sys.argv) > 1 else "8000"
BASE = f"http://127.0.0.1:{PORT}"

# 部署产物有会员鉴权门禁：token 从部署环境 .session.json 读取（不硬编码、不打印）
_TOKEN = ""
try:
    import json as _json
    import os as _os
    _root = _os.environ.get("DY_APP_ROOT") or r"C:	emp\dyautodm_design"
    with open(_os.path.join(_root, "members", ".session.json"), encoding="utf-8") as _f:
        _TOKEN = _json.load(_f).get("token") or ""
except Exception as _e:
    print(f"[warn] 未能读取会话 token（{type(_e).__name__}），鉴权接口将返回 401：{_e}")

PASS = FAIL = 0
FAILED = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ok   {name}")
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"  FAIL {name}  {detail}")


def req(path, method="GET", body=None):
    """path 必须是 ASCII —— 调用方负责 quote（RFC 3986）。"""
    url = BASE + path
    data = None if body is None else json.dumps(body).encode("utf-8")
    r = urllib.request.Request(url, data=data, method=method)
    if data:
        r.add_header("Content-Type", "application/json")
    if _TOKEN:
        r.add_header("X-Member-Token", _TOKEN)
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}
    except Exception as e:
        return -1, {"error": f"{type(e).__name__}: {e}"}


def q(s):
    return urllib.parse.quote(str(s), safe="")


print(f"目标：部署产物 {BASE}\n")

# ── 1) 版本与端点面 ────────────────────────────────────────────────────
st, v = req("/api/version")
check("1. 版本 = 0.43.93（部署产物）", st == 200 and v.get("backend") == "0.43.93", f"{st} {v}")
check("1b. frozen=true（确认跑的是打包产物，非源码）", v.get("frozen") is True, str(v))

_oq = urllib.request.Request(BASE + "/openapi.json")
if _TOKEN:
    _oq.add_header("X-Member-Token", _TOKEN)
openapi_raw = urllib.request.urlopen(_oq, timeout=15).read().decode()
paths = sorted(json.loads(openapi_raw).get("paths", {}))
check("2. 无 /api/live/target-rooms（目标直播间体系已移除）",
      not any("target-rooms" in p for p in paths),
      str([p for p in paths if "target-rooms" in p]))
check("2b. config-tags 4 端点齐全",
      len([p for p in paths if p.startswith("/api/live/config-tags")]) == 4,
      str([p for p in paths if "config-tags" in p]))

# ── 2) 多策略并存 + 零身份字段 ─────────────────────────────────────────
st, r1 = req("/api/live/config-tags", "POST",
             {"name": "验证-标准快", "max_target": 42, "interval": 9,
              "delay": "3,7", "auto_link_mic": True,
              "dm_pool": [{"text": "验收探针", "enabled": True}]})
check("3. 新建策略 A → ok", st == 200 and r1.get("ok"), f"{st} {r1}")
st, r2 = req("/api/live/config-tags", "POST",
             {"name": "验证-保守慢", "max_target": 5, "interval": 300})
check("3b. 新建策略 B → ok（多策略并存，摆脱「反复覆盖」）",
      st == 200 and r2.get("ok"), f"{st} {r2}")

sid_a = (r1.get("config") or {}).get("id")
sid_b = (r2.get("config") or {}).get("id")
check("3c. 两条策略 id 不同", bool(sid_a) and bool(sid_b) and sid_a != sid_b,
      f"A={sid_a} B={sid_b}")
check("3d. 参数互不覆盖（A=42 / B=5）",
      (r1.get("config") or {}).get("max_target") == 42
      and (r2.get("config") or {}).get("max_target") == 5)

st, lst = req("/api/live/config-tags")
items = lst.get("items") or []
check("3e. 列表可读到两条", st == 200 and len([x for x in items if x.get("id") in (sid_a, sid_b)]) == 2,
      f"n={len(items)}")

KEY_A = set((r1.get("config") or {}).keys())
forbidden = {"room_id", "live_url", "force_rescan", "forceRescan", "tag_id"}
check("3f. 策略体**零身份字段**（无 room_id/live_url/force_rescan/tag_id）",
      not (forbidden & KEY_A),
      f"命中={sorted(forbidden & KEY_A)}")
check("3g. 策略体含全部发送策略键",
      {"max_target", "interval", "delay", "dm_pool", "auto_link_mic",
       "link_mic_mode", "acct"} <= KEY_A,
      f"keys={sorted(KEY_A)}")

# ── 3) 中文策略名（URL 编码往返，不动内容）────────────────────────────
cn_id = "验证-中文策略名"
st, rc = req(f"/api/live/config-tags/{q(cn_id)}", "POST", {"name": cn_id, "max_target": 7})
check("4. 中文名策略可建（不依赖数字 room_id）", st == 200 and rc.get("ok"), f"{st} {rc}")
st, rg = req(f"/api/live/config-tags/{q(cn_id)}")
check("4b. 中文 id 可读回且参数正确（URL 编码往返）",
      st == 200 and (rg.get("config") or {}).get("max_target") == 7, f"{st} {rg}")

# ── 4) restart / apply 上下文 ─────────────────────────────────────────
st, rr = req(f"/api/live/config-tags/{q(sid_a)}/restart", "POST",
             {"room_id": "840377749201", "account": "验证账号X"})
j = rr or {}
check("5. restart 有响应且不谎报（引擎未跑 ⇒ restart.ok=false）",
      st == 200 and j.get("ok") is True and (j.get("restart") or {}).get("ok") is False,
      f"{st} {str(j)[:200]}")
check("5b. not_applied 不含 force_rescan（已彻底移除）",
      "force_rescan" not in ((j.get("restart") or {}).get("not_applied") or []),
      str(j.get("restart")))

st, ra = req(f"/api/live/config-tags/{q(sid_a)}/apply", "POST",
             {"room_id": "840377749201", "account": "验证账号X"})
cfg = (ra.get("config") or {})
check("5c. apply 用调用方上下文写 live_id/账号（策略自身无身份）",
      st == 200 and cfg.get("live_id") == "840377749201" and cfg.get("acct") == "验证账号X",
      f"{st} {str(cfg)[:220]}")
check("5d. apply 产物不含 force_rescan", "force_rescan" not in cfg, str(cfg)[:220])

# ── 5) 删除：只删自己，无跨模块副作用 ─────────────────────────────────
st, rd = req(f"/api/live/config-tags/{q(sid_b)}", "DELETE")
check("6. 删除策略 B → ok 且无 unbound_rooms 副作用键",
      st == 200 and rd.get("ok") and "unbound_rooms" not in rd, f"{st} {rd}")
st, lst2 = req("/api/live/config-tags")
ids = [x.get("id") for x in (lst2.get("items") or [])]
check("6b. B 已消失、A 仍在（删除精确）",
      sid_b not in ids and sid_a in ids, f"ids={ids}")

# ── 清理：删掉本次探针，绝不留在用户环境 ──────────────────────────────
for sid in (sid_a, cn_id):
    req(f"/api/live/config-tags/{q(sid)}", "DELETE")
st, lst3 = req("/api/live/config-tags")
left = [x.get("id") for x in (lst3.get("items") or [])]
check("7. 探针已全部清理（不留验证残留）",
      not any(x in left for x in (sid_a, sid_b, cn_id)), f"left={left}")

print(f"\nPASS={PASS} FAIL={FAIL}")
if FAILED:
    print("失败项：")
    for x in FAILED:
        print("  -", x)
sys.exit(1 if FAIL else 0)
