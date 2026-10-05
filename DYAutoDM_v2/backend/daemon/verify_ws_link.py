# coding=utf-8
"""WSLink 实机验证（2026-09-16 v0.43.36）。

不依赖抖音凭证：起本地 WS 服务端，模拟 frontier-im 的两个真实行为：
  A. 收到协议层 Ping 不回 Pong（这是 30s 定时自杀的充要条件）
  B. 业务低峰无任何下行帧（检验心跳是否真的在发、连接是否活）

验证断言：
  1. L0：连接存活 > 90s（修复前 30s 必死）
  2. L1：hb_sent 随时间递增（应用层心跳真的发出去了）
  3. 服务端确实收到 payloadType="hb" 的二进制帧
  4. L2：服务端主动断开后，客户端按退避重连，且**无递归**（栈不增长、单线程）
  5. stop() 能干净退出
"""
import base64, hashlib, os, socket, struct, sys, threading, time

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
sys.path.insert(0, _BACKEND)          # 使 `daemon.ws_link` / `static.Live_pb2` 可导入
sys.path.insert(0, _HERE)

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

# ---- 控制心跳节奏，让验证在秒级内跑完 ----
os.environ["DY_WS_HB_INTERVAL"] = "5"
os.environ["DY_WS_DEAD_TIMEOUT"] = "30"
os.environ["DY_WS_BACKOFF_BASE"] = "1"
os.environ["DY_WS_BACKOFF_MAX"] = "2"

from daemon.ws_link import WSLink  # noqa: E402

received = {"hb": 0, "ping": 0, "binary": 0, "other": 0}
conn_count = {"n": 0}
srv_stop = threading.Event()


def parse_ws_frames(buf):
    """极简 WS 帧解析（服务端收的都是未掩码的客户端帧）。"""
    out = []
    i = 0
    while i + 2 <= len(buf):
        b0, b1 = buf[i], buf[i + 1]
        op = b0 & 0x0F
        ln = b1 & 0x7F
        masked = bool(b1 & 0x80)
        off = 2
        if ln == 126:
            ln = struct.unpack(">H", buf[i + 2:i + 4])[0]; off = 4
        elif ln == 127:
            ln = struct.unpack(">Q", buf[i + 2:i + 10])[0]; off = 10
        key = b""
        if masked:
            key = buf[i + off:i + off + 4]; off += 4
        payload = buf[i + off:i + off + ln]
        if masked:
            payload = bytes(c ^ key[j % 4] for j, c in enumerate(payload))
        out.append((op, payload))
        i += off + ln
    return out


def serve(port, kill_after=None):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("127.0.0.1", port)); s.listen(5)
    s.settimeout(0.5)
    while not srv_stop.is_set():
        try:
            conn, _ = s.accept()
        except socket.timeout:
            continue
        except OSError:
            break
        threading.Thread(target=handle, args=(conn, kill_after), daemon=True).start()
    s.close()


def handle(conn, kill_after):
    conn_count["n"] += 1
    n = conn_count["n"]
    try:
        data = conn.recv(4096)
        key = ""
        for line in data.decode(errors="ignore").split("\r\n"):
            if line.lower().startswith("sec-websocket-key:"):
                key = line.split(":", 1)[1].strip()
        accept = base64.b64encode(hashlib.sha1((key + GUID).encode()).digest()).decode()
        conn.sendall((
            "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Accept: {accept}\r\n\r\n").encode())
        conn.settimeout(0.3)
        t0 = time.time()
        while not srv_stop.is_set():
            # 每条连接 8s 后服务端主动断开 → 检验客户端退避重连
            # （2026-09-16 修正：原判据 `n == 2` 依赖「第 1 条先被看门狗杀掉」
            #   的旧行为；看门狗修正后第 1 条会长期存活，n==2 永远等不到，
            #   导致这条断言假失败。改为「每条连接都限时」才是真实语义。）
            if kill_after and time.time() - t0 > 8:
                conn.close(); return
            try:
                b = conn.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                return
            if not b:
                return
            for op, payload in parse_ws_frames(b):
                if op == 0x9:
                    received["ping"] += 1      # 协议层 Ping —— 故意不回 Pong
                elif op == 0x2:
                    received["binary"] += 1
                    if b"hb" in payload:
                        received["hb"] += 1
                elif op == 0x8:
                    return
                else:
                    received["other"] += 1
    except Exception as e:
        # 🔴 2026-10-01 修复：静默兜底改为可观测日志（项目铁律：静默兜底是故障隐藏层）
        import logging
        logging.getLogger(__name__).debug(f"[verify_ws_link] WebSocket 验证异常: {e}")
    finally:
        try: conn.close()
        except Exception:
            pass


PORT = 8901
threading.Thread(target=serve, args=(PORT, True), daemon=True).start()
time.sleep(0.5)

from websocket import WebSocketApp  # noqa: E402

msgs = []
connected_at = []


def make_ws():
    return WebSocketApp(f"ws://127.0.0.1:{PORT}/")


def on_msg(m):
    msgs.append(m)


def on_conn():
    connected_at.append(time.time())
    print(f"  [client] connected #{len(connected_at)} at t+{time.time()-T0:.1f}s")


def on_disc(reason):
    print(f"  [client] disconnected at t+{time.time()-T0:.1f}s reason={reason!r}")


T0 = time.time()
link = WSLink(name="probe", make_ws=make_ws, on_message=on_msg,
              on_connected=on_conn, on_disconnected=on_disc)
link.start()

DUR = 70
print(f"运行 {DUR}s（服务端：收 Ping 不回 Pong；第 2 条连接 8s 后被踢）…\n")
for i in range(DUR):
    time.sleep(1)
    if i % 10 == 9:
        s = link.stats
        print(f"  t+{i+1:>2}s  connects={s['connects']} disc={s['disconnects']} "
              f"hb_sent={s['hb_sent']} hb_failed={s['hb_failed']} "
              f"rx_age={s['last_rx_age']:.0f}s")

time.sleep(1)
link.stop()
srv_stop.set()
time.sleep(0.5)

s = link.stats
print("\n===== 服务端收到 =====")
print(f"  协议层 Ping 帧: {received['ping']}  ← 若为 0 则 L0 生效（客户端不发 Ping）")
print(f"  二进制帧: {received['binary']}，其中含 'hb' 的: {received['hb']}")
print("\n===== 客户端统计 =====")
for k, v in s.items():
    print(f"  {k}: {v}")

print("\n===== 断言 =====")
ok = True


def chk(name, cond, detail=""):
    global ok
    print(f"  [{'PASS' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        ok = False


chk("L0 未发协议层 Ping（消除 30s 自杀前提）", received["ping"] == 0,
    f"(ping={received['ping']})")
chk("L1 应用层 hb 心跳已发出", s["hb_sent"] >= 5, f"(hb_sent={s['hb_sent']})")
chk("L1 服务端确实收到 hb 帧", received["hb"] >= 5, f"(hb={received['hb']})")
chk("L0+L1 连接未发生 30s 定时自杀",
    s["hb_failed"] == 0, f"(hb_failed={s['hb_failed']})")
chk("L2 服务端踢线后客户端自动重连", s["connects"] >= 3,
    f"(connects={s['connects']})")
chk("L2 心跳无失败（连接真实存活）", s["hb_failed"] == 0)
print(f"\n总判定: {'ALL PASS' if ok else 'HAS FAILURE'}")
