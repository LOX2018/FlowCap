# coding=utf-8
"""稳态对照实验 v3（2026-09-16 v0.43.36）—— 手写 socket mock 服务端。

为什么不用 `websockets` 库：它**收到 Ping 自动回 Pong**，而 frontier-im 恰恰
不回 Pong —— 用它会得出「OLD 也活 100s」的假阳性（v2 已踩此坑，如实记录）。
必须手写 socket，才能真正复现「Ping 无 Pong」这一充要条件。

服务端行为（严格对齐 frontier-im）：
  A. 收协议层 Ping 帧 → 不回 Pong
  B. 全程零下行帧（业务低峰）

对比：
  OLD: run_forever(ping_interval=20, ping_timeout=10)   ← 修复前
  NEW: WSLink（L0 关协议层 ping + L1 应用层 hb + L2 单循环）
"""
import base64, hashlib, os, socket, struct, sys, threading, time

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
sys.path.insert(0, _BACKEND)
sys.path.insert(0, _HERE)

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
os.environ["DY_WS_HB_INTERVAL"] = "15"
os.environ["DY_WS_BACKOFF_BASE"] = "1"
os.environ["DY_WS_BACKOFF_MAX"] = "2"

from daemon import ws_link as wsl  # noqa: E402
wsl.DEAD_TIMEOUT = 10 ** 9          # 本轮不测看门狗，只看纯保活
from daemon.ws_link import WSLink  # noqa: E402
from websocket import WebSocketApp  # noqa: E402

PORT = 8931
WINDOW = 100
got = {"ping": 0, "hb": 0, "conns": 0}


def parse(b):
    out, i = [], 0
    while i + 2 <= len(b):
        op = b[i] & 0x0F
        ln = b[i + 1] & 0x7F
        masked = bool(b[i + 1] & 0x80)
        off = 2
        if ln == 126:
            ln = struct.unpack(">H", b[i + 2:i + 4])[0]; off = 4
        elif ln == 127:
            ln = struct.unpack(">Q", b[i + 2:i + 10])[0]; off = 10
        key = b""
        if masked:
            key = b[i + off:i + off + 4]; off += 4
        p = b[i + off:i + off + ln]
        if masked:
            p = bytes(c ^ key[j % 4] for j, c in enumerate(p))
        out.append((op, p))
        i += off + ln
    return out


def handle(conn):
    got["conns"] += 1
    try:
        d = conn.recv(8192)
        if not d:
            return
        key = ""
        for line in d.decode(errors="ignore").split("\r\n"):
            if line.lower().startswith("sec-websocket-key:"):
                key = line.split(":", 1)[1].strip()
        if not key:
            return
        acc = base64.b64encode(
            hashlib.sha1((key + GUID).encode()).digest()).decode()
        conn.sendall(
            b"HTTP/1.1 101 Switching Protocols\r\n"
            b"Upgrade: websocket\r\nConnection: Upgrade\r\n"
            b"Sec-WebSocket-Accept: " + acc.encode() + b"\r\n\r\n")
        conn.settimeout(0.3)
        while True:
            try:
                b = conn.recv(8192)
            except socket.timeout:
                continue
            except OSError:
                return
            if not b:
                return
            for op, p in parse(b):
                if op == 0x9:
                    got["ping"] += 1       # ★ 收 Ping 不回 Pong
                elif op == 0x2 and b"hb" in p:
                    got["hb"] += 1
                elif op == 0x8:
                    return
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def serve():
    """阻塞式 accept（已验证可行）；每连接一个线程。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", PORT))
    s.listen(8)
    while True:
        try:
            c, _ = s.accept()
        except OSError:
            return
        threading.Thread(target=handle, args=(c,), daemon=True).start()


threading.Thread(target=serve, daemon=True).start()
time.sleep(1.0)

# preflight：确认服务端真在监听
_s = socket.socket()
try:
    _s.connect(("127.0.0.1", PORT)); _s.close()
    print("  [preflight] mock 服务端已就绪（收 Ping 不回 Pong）")
except Exception as e:
    print(f"  !! mock 服务端未就绪: {e}")
    sys.exit(1)


def run_old():
    st = {"open": False, "err": None}
    app = WebSocketApp(f"ws://127.0.0.1:{PORT}/")
    app.on_open = lambda ws: st.__setitem__("open", True)
    app.on_error = lambda ws, e: (st.__setitem__("open", False),
                                  st.__setitem__("err", e))
    app.on_close = lambda ws, *a: st.__setitem__("open", False)
    threading.Thread(
        target=lambda: app.run_forever(ping_interval=20, ping_timeout=10),
        daemon=True).start()
    t0 = time.time()
    while time.time() - t0 < 5 and not st["open"] and not st["err"]:
        time.sleep(0.1)
    if not st["open"]:
        print(f"  !! OLD 建连失败: {st['err']!r}")
        return 0.0, False
    t0 = time.time()
    while time.time() - t0 < WINDOW and st["open"]:
        time.sleep(0.5)
    life = WINDOW if st["open"] else time.time() - t0
    try:
        app.close()
    except Exception:
        pass
    return life, st["open"]


print("=== OLD: run_forever(ping_interval=20, ping_timeout=10) ===")
old_life, old_alive = run_old()
print(f"  OLD 存活 {old_life:.1f}s（alive={old_alive}）")
time.sleep(1)

print("\n=== NEW: WSLink (L0+L1+L2) ===")
link = WSLink(name="new",
              make_ws=lambda: WebSocketApp(f"ws://127.0.0.1:{PORT}/"),
              on_message=lambda m: None)
link.start()
t0 = time.time()
while time.time() - t0 < 5 and not link.stats["connected"]:
    time.sleep(0.1)
if not link.stats["connected"]:
    print("  !! NEW 建连失败")
    new_life, new_alive, hb = 0.0, False, 0
else:
    t0 = time.time()
    while time.time() - t0 < WINDOW and link.stats["connected"]:
        time.sleep(0.5)
    new_life = time.time() - t0
    new_alive = link.stats["connected"]
    hb = link.stats["hb_sent"]
link.stop()
print(f"  NEW 存活 {new_life:.1f}s（alive={new_alive}）hb_sent={hb}")

print("\n===== 服务端累计 =====")
print(f"  协议层 Ping: {got['ping']}（OLD 段产生；NEW 应为 0 增量）")
print(f"  应用层 hb  : {got['hb']}")

print("\n===== 断言 =====")
ok = True


def chk(n, c, d=""):
    global ok
    print(f"  [{'PASS' if c else 'FAIL'}] {n} {d}")
    if not c:
        ok = False


chk("OLD 在 ~30s 被判死（复现原故障）", (not old_alive) and old_life < 45,
    f"({old_life:.1f}s)")
chk("NEW 存活满 100s", new_alive and new_life >= 99, f"({new_life:.1f}s)")
chk("NEW 心跳持续发出", hb >= 4, f"(hb_sent={hb})")
chk("NEW 心跳被服务端收到", got["hb"] >= 4, f"(srv hb={got['hb']})")
print(f"\n总判定: {'ALL PASS' if ok else 'HAS FAILURE'}")
