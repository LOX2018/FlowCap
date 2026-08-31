"""实测 /api/logs 与 /api/logs/sessions 的返回，定位日志页为何看不到内容。"""
import json
import urllib.request

BASE = "http://127.0.0.1:8000"


def get(p, timeout=25):
    try:
        r = urllib.request.urlopen(BASE + p, timeout=timeout)
        return r.status, r.read().decode("utf-8", "replace")
    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)[:150]}"


st, body = get("/api/logs/sessions")
print("=== /api/logs/sessions ===")
print("HTTP", st)
if st:
    try:
        j = json.loads(body)
        print("  current:", j.get("current"))
        ss = j.get("sessions") or []
        print(f"  会话数: {len(ss)}")
        for s in ss[:5]:
            print(f"    {s['file']}  size={s['size']}")
    except Exception as e:
        print("  解析失败", e, body[:200])
else:
    print(" ", body)

print()
st2, body2 = get("/api/logs?limit=500")
print("=== /api/logs?limit=500 ===")
print("HTTP", st2, "len", len(body2 or ""))
if st2:
    try:
        j = json.loads(body2)
        lines = j.get("lines") or []
        print(f"  行数: {len(lines)}")
        print("  最后 8 行:")
        for ln in lines[-8:]:
            print(f"    [{ln.get('ts')}] {ln.get('level')} | {str(ln.get('text'))[:80]}")
    except Exception as e:
        print("  解析失败", e, body2[:300])
else:
    print(" ", body2)
