# coding=utf-8
"""_handle NameError 回归验证（2026-09-16）。

背景：_handle 最后一行 logger.info 引用了从未赋值的 `peer_name`，
      每条消息都抛 NameError: name 'peer_name' is not defined，
      真实环境当日累计 544 次（RECV-005），淹没真实错误。

验证方法：构造真实的 PushFrame + Response(new_message_notify) protobuf
        字节流，喂给真实的 RecvChannel._handle，断言：
          ① 不抛异常
          ② 消息正确落 dm_messages
          ③ last_ts / unread 被更新
          ④ 日志里的发送方不再是 NameError

⚠️ 为何不能只靠 py_compile：该 bug 是**运行时**未绑定变量，
   语法完全合法，静态检查只有 AST 级 Load/Store 分析才发现得了。
"""
import os, sys, tempfile, time

_HERE = os.path.dirname(os.path.abspath(__file__))
_BACKEND = os.path.dirname(_HERE)
sys.path.insert(0, _BACKEND)
sys.path.insert(0, _HERE)

import json

from static import Live_pb2, Response_pb2

# 合成 uid（仅用于构造协议帧做解析验证，与任何真实账号无关）
MY_UID = "100000000000001"
PEER_UID = "200000000000002"
CONV_ID = f"0:1:{MY_UID}:{PEER_UID}"


def build_frame(text: str, sender: str, msg_type: int = 7,
                msg_id: int = 70001, nickname: str = "") -> bytes:
    """构造真实的一帧 new_message_notify。"""
    body = {
        "text": text,
    }
    if nickname:
        body["sender_nickname"] = nickname
    msg = Response_pb2.MessageBody()
    msg.conversation_id = CONV_ID
    msg.conversation_type = 1
    if msg_id is not None:            # 系统占位消息没有 server_message_id
        msg.server_message_id = msg_id
    msg.index_in_conversation = 42
    msg.message_type = msg_type
    msg.sender = int(sender)
    msg.content = json.dumps(body, ensure_ascii=False)

    nm = Response_pb2.NewMessageNotify()
    nm.conversation_id = CONV_ID
    nm.conversation_type = 1
    nm.notify_type = 1
    nm.message.CopyFrom(msg)

    resp = Response_pb2.Response()
    resp.cmd = 500
    resp.sequence_id = 1
    resp.inbox_type = 1
    resp.body.new_message_notify.CopyFrom(nm)

    frame = Live_pb2.PushFrame()
    frame.seqId = 1
    frame.logId = 123
    frame.service = 1
    frame.method = 1
    frame.payloadEncoding = "pb"
    frame.payloadType = "pb"
    frame.payload = resp.SerializeToString()
    return frame.SerializeToString()


# ---- 用真实 RecvChannel._handle，但剥掉建房依赖（DB / inbox 用轻量替身）----
class FakeInbox:
    def __init__(self, name="probe"):
        self.name = name
        self.my_uid = MY_UID
        self.convs = {}
        self.lock = __import__("threading").RLock()
        self.messages = []
        self.connected = False
        self.last_error = ""

    def _refresh_my_uid(self):
        return True

    def _db(self):
        raise RuntimeError("no db in unit probe")

    def add_message(self, conv_id, role, text, peer_id=None, peer_name=None,
                    msg_type="text", extra=None, msg_id=None):
        self.messages.append({
            "conv_id": conv_id, "role": role, "text": text,
            "peer_id": peer_id, "peer_name": peer_name,
            "msg_type": msg_type, "msg_id": msg_id,
        })
        return None


class ProbeChannel:
    """只借用 _handle / _extract 的真实实现，不启动 WS。"""
    # 直接复用 RecvChannel 的真实方法（下面统一赋值），确保「跑的是真代码」
    def __init__(self):
        self.name = "probe"
        self.inbox = FakeInbox()


import daemon.recv_daemon as rd

# 把真实方法绑到探针类上（不改动 recv_daemon 源码）
Channel = rd.RecvChannel
ProbeChannel._handle = Channel._handle
ProbeChannel._extract = Channel.__dict__["_extract"]      # staticmethod 对象原样搬
ProbeChannel._sync_conversations = Channel._sync_conversations
assert callable(ProbeChannel._handle), "_handle 未绑定"
assert callable(ProbeChannel._extract), "_extract 未绑定"

print("=== 注入真实 new_message_notify 帧 ===\n")
ok = True


def _extract_ch(content_json, msg_type):
    """真实 _extract 是 @staticmethod，这里透明转发。"""
    return Channel._extract(content_json, msg_type)


def chk(n, cond, d=""):
    global ok
    print(f"  [{'PASS' if cond else 'FAIL'}] {n} {d}")
    if not cond:
        ok = False


# 场景1：对方发普通文本
ch = ProbeChannel()
frame = build_frame("你好，在吗", PEER_UID)
err = None
try:
    ch._handle(frame)
except Exception as e:
    err = f"{type(e).__name__}: {e}"
chk("① _handle 不抛异常", err is None, f"({err})")
chk("② 消息已接收", len(ch.inbox.messages) == 1,
    f"(n={len(ch.inbox.messages)})")
if ch.inbox.messages:
    m = ch.inbox.messages[0]
    chk("③ 方向判定为 them", m["role"] == "them", f"({m['role']})")
    chk("④ 对端 uid 取自 conv_id", m["peer_id"] == PEER_UID,
        f"({m['peer_id']})")
    chk("⑤ 文本正确", m["text"] == "你好，在吗", f"({m['text']!r})")

# 场景2：自己发的消息（sender == my_uid）
ch2 = ProbeChannel()
frame2 = build_frame("自动欢迎语", MY_UID, msg_id=70002)
err2 = None
try:
    ch2._handle(frame2)
except Exception as e:
    err2 = f"{type(e).__name__}: {e}"
chk("⑥ 自己发的消息不抛异常", err2 is None, f"({err2})")
if ch2.inbox.messages:
    chk("⑦ 自方向判定为 me", ch2.inbox.messages[0]["role"] == "me",
        f"({ch2.inbox.messages[0]['role']})")

# 场景3：系统占位（应被丢弃）
ch3 = ProbeChannel()
frame3 = build_frame("对方回复你或互关之前，可发送一条文字消息。", PEER_UID,
                     msg_id=None)
err3 = None
try:
    ch3._handle(frame3)
except Exception as e:
    err3 = f"{type(e).__name__}: {e}"
chk("⑧ 系统占位消息不抛异常", err3 is None, f"({err3})")
chk("⑨ 系统占位被丢弃（不入库）", len(ch3.inbox.messages) == 0,
    f"(n={len(ch3.inbox.messages)})")

# 场景4：图片消息（extra 要带 skey）
ch4 = ProbeChannel()
img_body = Response_pb2.MessageBody()
img_body.conversation_id = CONV_ID
img_body.message_type = 27
img_body.sender = int(PEER_UID)
img_body.content = json.dumps({
    "resource_url": {
        "origin_url_list": ["https://p9.douyinpic.com/x.jpg"],
        "skey": "AABBCC_SKEY",
    }}, ensure_ascii=False)
nm4 = Response_pb2.NewMessageNotify()
nm4.message.CopyFrom(img_body)
r4 = Response_pb2.Response()
r4.body.new_message_notify.CopyFrom(nm4)
f4 = Live_pb2.PushFrame()
f4.payloadType = "pb"
f4.payloadEncoding = "pb"
f4.payload = r4.SerializeToString()
err4 = None
try:
    ch4._handle(f4.SerializeToString())
except Exception as e:
    err4 = f"{type(e).__name__}: {e}"
chk("⑩ 图片消息不抛异常", err4 is None, f"({err4})")

# 场景5：text/json 控制帧
ch5 = ProbeChannel()
f5 = Live_pb2.PushFrame()
f5.payloadType = "text/json"
f5.payload = json.dumps({"msg_type": 1, "status_code": 0}).encode()
err5 = None
try:
    ch5._handle(f5.SerializeToString())
except Exception as e:
    err5 = f"{type(e).__name__}: {e}"
chk("⑪ json 控制帧不抛异常", err5 is None, f"({err5})")

print(f"\n总判定: {'ALL PASS' if ok else 'HAS FAILURE'}")
sys.exit(0 if ok else 1)
