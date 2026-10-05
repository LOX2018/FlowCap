# -*- coding: utf-8 -*-
"""直播数据链路逐段诊断（进房三步：页面解析 → im/fetch → WS 握手）。

## 为什么有这个工具

「直播流数据都捕获不到」的最上游不是 WS，而是**进房拿 room_id/user_id/ttwid**。
本工具把链路拆成三段分别实测，让失败点**自己说话**，而不是靠推断：

| 段 | 调用 | 失败含义 |
|---|---|---|
| ① `get_live_info` | GET https://live.douyin.com/<live_id> 后正则解析页面 | 页面结构变了 / cookie 失效 / 主播未开播 |
| ② `get_webcast_detail` | GET /webcast/im/fetch/ （a_bogus 签名） | **签名被拒**（a_bogus 的 aid/page_id 用错子域） |
| ③ WS 握手 | wss://webcast100-ws-web-hl.douyin.com/webcast/im/push/v2/ | 签名 room_id 错 / 连接层问题 |

## 关键判据（本工具的差异化能力）

第 ② 步除了跑线上请求，还**离线对比 a_bogus 的 host 参数化能力**：
上游 `builder/params.py: with_a_bogus(data, host=LIVE_HOST)` + `utils/ab_pure.py:
HOST_APP_IDS` 已按子域区分 `(aid, page_id)`（live=6383/7571，www=6383/11881）；
本仓若把 page_id 硬编码成 aid 的值，直播域签名就是**错的**——这是「数据都拉不到」
且**不报任何错**（服务端静默返回空/拒绝）的典型成因。

## 用法

    python scripts/diag/diag_live_pipeline.py [--live-id <房间号或URL数字段>]

不传 --live-id 时，从运行期配置（data/config.json 的 live_url / settings.live_url）
读取；读不到则要求显式传入。

## 环境与安全

- 环境门禁：`DY_APP_ROOT` 必须指向**设计分支**数据根（默认 `C:\\temp\\dyautodm_design`）；
  指向 `C:\\temp\\dyautodm_test`（主分支）时**拒绝运行**。
- 零浏览器、零引擎、零常驻进程；只读 .env（会员空间自动解密）。
- **只打印长度 / 命中与否 / 状态号，绝不打印 cookie、ticket、私钥值。**
- 账号名运行期从磁盘取得，脚本内不写死。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

DESIGN_ROOT = os.path.abspath(os.environ.get("DY_APP_ROOT") or r"C:\temp\dyautodm_design")
_FORBIDDEN = {os.path.abspath(r"C:\temp\dyautodm_test")}

BACKEND = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "backend"))


def _bootstrap() -> dict:
    """环境门禁 + 会员态注入 + sys.path。"""
    if DESIGN_ROOT in _FORBIDDEN:
        sys.exit("[环境门禁] DY_APP_ROOT 指向主分支环境(%s)，拒绝运行" % DESIGN_ROOT)
    os.environ["DY_APP_ROOT"] = DESIGN_ROOT
    sys.path.insert(0, BACKEND)
    os.chdir(BACKEND)

    sess_path = os.path.join(DESIGN_ROOT, "members", ".session.json")
    if not os.path.exists(sess_path):
        sys.exit("[环境] 未找到会员会话 %s" % sess_path)
    with open(sess_path, encoding="utf-8") as f:
        sess = json.load(f)
    os.environ["DY_MEMBER"] = sess["member_id"]
    os.environ["DY_MEMBER_KEY"] = sess["master_key"]
    member_id = sess["member_id"]
    return {
        "member_id": member_id,
        "acc_root": os.path.join(DESIGN_ROOT, "members", member_id, "auto_dm", "accounts"),
        "data_dir": os.path.join(DESIGN_ROOT, "members", member_id, "data"),
    }


def _resolve_live_id(env: dict, explicit: str | None) -> str | None:
    """运行期取直播间号：显式参数 > data/config.json。绝不写死房间号。"""
    if explicit:
        return explicit
    cfg = os.path.join(env["data_dir"], "config.json")
    try:
        with open(cfg, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    raw = data.get("live_url") or (data.get("settings") or {}).get("live_url") or ""
    digits = "".join(ch for ch in str(raw) if ch.isdigit())
    return digits or None


def _probe_abogus_host_support() -> dict:
    """离线检查本仓 a_bogus 是否支持按子域取 (aid, page_id)。

    返回能力画像，不发任何网络请求。
    """
    out = {}
    try:
        import inspect
        from builder import params as P
        sig = inspect.signature(P.Params.with_a_bogus)
        out["with_a_bogus_accepts_host"] = "host" in sig.parameters
    except Exception as e:
        out["with_a_bogus_accepts_host"] = f"检查失败: {type(e).__name__}"
    try:
        import utils.ab_pure as A
        out["ab_pure_has_HOST_APP_IDS"] = hasattr(A, "HOST_APP_IDS")
        if hasattr(A, "HOST_APP_IDS"):
            out["HOST_APP_IDS"] = dict(A.HOST_APP_IDS)
        src = inspect.getsource(A)
        # 判据：L[67..70] 是否写死常量（page_id 位置）
        out["page_id_hardcoded"] = ("le_bytes(page_id, 4)" not in src)
    except Exception as e:
        out["ab_pure_err"] = f"{type(e).__name__}: {e}"
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="直播数据链路逐段诊断（进房 → im/fetch → WS）")
    ap.add_argument("--live-id", default=None, help="直播间号（纯数字）；缺省从 config.json 读")
    ap.add_argument("--account", default=None, help="指定账号名；缺省对所有账号跑第①步，第②③步用第一个可用账号")
    args = ap.parse_args(argv)

    env = _bootstrap()
    print(f"数据根(DY_APP_ROOT) = {DESIGN_ROOT}")

    # ---------- 0. 离线能力画像（不发网络请求） ----------
    print(f"\n{'=' * 74}\n【0】a_bogus 子域参数化能力（离线静态，无网络）")
    caps = _probe_abogus_host_support()
    for k, v in caps.items():
        print(f"  {k} = {v}")
    if caps.get("with_a_bogus_accepts_host") is False or caps.get("page_id_hardcoded"):
        print("  ⚠ 本仓 a_bogus 未实现「按子域取 (aid, page_id)」——直播域签名可能与服务端 expectancy 不符。")

    live_id = _resolve_live_id(env, args.live_id)
    print(f"\n直播间号 = {live_id or '(未取到，需 --live-id)'}")
    if not live_id:
        print("  无法继续（无直播间号）。用法：--live-id <数字>")
        return 2

    from dy_apis.login_api import DYLoginApi
    from dy_apis.douyin_api import DouyinAPI

    if not os.path.isdir(env["acc_root"]):
        sys.exit("[环境] 账号目录不存在：%s" % env["acc_root"])
    names = sorted(a for a in os.listdir(env["acc_root"])
                   if os.path.isdir(os.path.join(env["acc_root"], a)))
    if args.account:
        names = [n for n in names if n == args.account] or [args.account]
    print(f"账号 = {names}")

    loaded = {}
    for acct in names:
        try:
            loaded[acct] = DYLoginApi._load_auth_from_env(os.path.join(env["acc_root"], acct, ".env"))
        except Exception as e:
            print(f"  [{acct}] .env 加载失败：{type(e).__name__}: {str(e)[:120]}")

    if not loaded:
        sys.exit("[环境] 无任何可用账号凭证")

    # ---------- 1. get_live_info（页面解析） ----------
    print(f"\n{'=' * 74}\n【1】get_live_info —— 进房页面解析")
    room_info = None
    info_acct = None
    for acct, auth in loaded.items():
        try:
            info = DouyinAPI.get_live_info(auth, live_id)
        except Exception as e:
            print(f"  [{acct}] 异常：{type(e).__name__}: {str(e)[:160]}")
            continue
        if isinstance(info, dict) and info.get("room_id"):
            room_info, info_acct = info, acct
            print(f"  [{acct}] ✅ 解析成功 room_id={info['room_id']} "
                  f"user_id={info.get('user_id')} status={info.get('room_status')} "
                  f"ttwid_len={len(str(info.get('ttwid') or ''))}")
            break
        print(f"  [{acct}] ❌ 返回空/非 dict（页面未解析到 roomId）")

    if not room_info:
        print("\n【判定】第①步全账号失败 ⇒ 页面结构变更 / 凭证失效 / 主播未开播。")
        print("  下一步：确认该直播间此刻确实在播；若在播且仍失败，需抓页面 HTML 看 script[nonce] 结构。")
        return 1

    auth = loaded[info_acct]

    # ---------- 2. get_webcast_detail（/webcast/im/fetch/，带 a_bogus） ----------
    print(f"\n{'=' * 74}\n【2】get_webcast_detail —— /webcast/im/fetch/（a_bogus 签名）")
    room_id = room_info["room_id"]
    user_id = room_info.get("user_id")
    try:
        raw = DouyinAPI.get_webcast_detail(auth, str(user_id), room_id,
                                           f"https://live.douyin.com/{live_id}")
        print(f"  返回字节数 = {len(raw) if raw is not None else 'None'}")
        import re as _re
        txt = (raw or b"")[:200].decode("utf-8", errors="replace")
        printable = "".join(ch if ch.isprintable() else "." for ch in txt)
        print(f"  前 200 字节(可打印) = {printable[:200]}")
        # 判据：protobuf 响应不该是可读 JSON 错误体
        looks_json_err = raw[:1] in (b"{", b"[") if raw else False
        print(f"  是否疑似 JSON 错误响应 = {looks_json_err}")

        # 交叉验证：im/fetch 的 protobuf 自带 WebcastRoomMessage，是开播状态的
        # **第二数据源**。用它独立校验 get_live_info 解析出的 room_status，
        # 避免「页面正则解析出假 status」被当成事实。
        try:
            import static.Live_pb2 as _Live
            resp = _Live.LiveResponse()
            resp.ParseFromString(raw)
            methods = [m.method for m in resp.messagesList]
            print(f"  protobuf 消息 method 列表 = {methods}")
            for m in resp.messagesList:
                if m.method == "WebcastRoomMessage":
                    # 本仓 Live_pb2 未定义 RoomMessage，按字段号直接读原始 protobuf：
                    # field 2 = common（嵌套），field 3 = status。
                    print(f"  【交叉验证】WebcastRoomMessage payload 长度 = {len(m.payload)}")
                    print(f"    原始字段号 = {sorted({(b >> 3) for b in m.payload[:40]})}")
                    break
            else:
                print("  【交叉验证】响应内无 WebcastRoomMessage（无法独立校验开播状态）")
        except Exception as e:
            print(f"  【交叉验证】protobuf 解析失败：{type(e).__name__}: {str(e)[:140]}")
    except Exception as e:
        print(f"  ❌ 异常：{type(e).__name__}: {str(e)[:200]}")

    # ---------- 3. WS 握手 ----------
    print(f"\n{'=' * 74}\n【3】WS 握手（webcast im/push/v2，仅握手后即断，不长期监听）")
    try:
        from urllib.parse import urlencode
        from builder.header import HeaderBuilder
        from builder.params import Params
        from utils.dy_util import generate_signature
        import static.Live_pb2 as Live_pb2
        import websocket

        frame = Live_pb2.LiveResponse()
        try:
            frame.ParseFromString(raw)
            cursor, internal_ext = str(frame.cursor), frame.internalExt
        except Exception:
            cursor, internal_ext = "", ""
        p = Params()
        (p.add_param('app_name', 'douyin_web')
          .add_param('version_code', '180800')
          .add_param('webcast_sdk_version', '1.0.15')
          .add_param('update_version_code', '1.0.15')
          .add_param('compress', 'gzip')
          .add_param('device_platform', 'web')
          .add_param('cookie_enabled', 'true')
          .add_param('screen_width', '1707')
          .add_param('screen_height', '960')
          .add_param('browser_language', 'zh-CN')
          .add_param('browser_platform', 'Win32')
          .add_param('browser_name', 'Mozilla')
          .add_param('browser_version', HeaderBuilder.ua.split('Mozilla/')[-1])
          .add_param('browser_online', 'true')
          .add_param('tz_name', 'Etc/GMT-8')
          .add_param('cursor', cursor)
          .add_param('internal_ext', internal_ext)
          .add_param('host', 'https://live.douyin.com')
          .add_param('aid', '6383')
          .add_param('live_id', '1')
          .add_param('did_rule', '3')
          .add_param('endpoint', 'live_pc')
          .add_param('support_wrds', '1')
          .add_param('user_unique_id', str(user_id))
          .add_param('im_path', '/webcast/im/fetch/')
          .add_param('identity', 'audience')
          .add_param('need_persist_msg_count', '15')
          .add_param('insert_task_id', '')
          .add_param('live_reason', '')
          .add_param('room_id', room_id)
          .add_param('heartbeatDuration', '0')
          .add_param('signature', generate_signature(room_id, user_id)))
        wss = f"wss://webcast100-ws-web-hl.douyin.com/webcast/im/push/v2/?{urlencode(p.get())}"
        print(f"  room_id={room_id} user_id={user_id}")
        ws = websocket.create_connection(
            wss, timeout=15, origin='https://live.douyin.com',
            cookie=auth.cookie_str,
            header={'User-Agent': HeaderBuilder.ua,
                    'Accept-Language': 'zh-CN,zh;q=0.9',
                    'Cache-Control': 'no-cache', 'Pragma': 'no-cache'},
        )
        # 关键：服务端不发帧的常见原因是「未发心跳」。真实 DouyinLive.on_open 会
        # 起 5s 一次 payloadType='hb' 的 ping 线程；裸连接不发心跳 → 判据不可信。
        print("  WS 握手成功（TCP+HTTP Upgrade 通过）")
        ws.settimeout(8)
        got = 0
        try:
            import threading, time as _t
            hb = Live_pb2.PushFrame()
            hb.payloadType = "hb"
            stop = threading.Event()

            def _ping():
                while not stop.is_set():
                    try:
                        ws.send(hb.SerializeToString(), opcode=0x02)
                    except Exception:
                        return
                    _t.sleep(5)

            threading.Thread(target=_ping, daemon=True).start()
            for _ in range(4):
                try:
                    m = ws.recv()
                except Exception:
                    continue
                if m:
                    got += 1
                    if got == 1:
                        print(f"  收到首帧 {len(m) if isinstance(m,(bytes,bytearray)) else type(m).__name__} 字节")
            stop.set()
        finally:
            try:
                ws.close()
            except Exception:
                pass
        print(f"  发心跳后 32s 内收到帧数 = {got}")
        if got == 0:
            print("  ⚠ 连上且有心跳但零帧 ⇒ 该房间此刻无推流（未开播）或 WS 参数被判无效观众。")
        ws = None
    except Exception as e:
        print(f"  ❌ WS 失败：{type(e).__name__}: {str(e)[:200]}")

    print(f"\n{'=' * 74}\n【判定指引】")
    print("  ① 失败 ⇒ 页面/凭证/未开播；② 失败或返回 JSON 错误体 ⇒ a_bogus 签名被拒（子域 aid/page_id）；")
    print("  ①② 成功而 ③ 失败 ⇒ WS 层（signature/cursor/连接）；三段都成功但前端仍无数据 ⇒ 前端消费层。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
