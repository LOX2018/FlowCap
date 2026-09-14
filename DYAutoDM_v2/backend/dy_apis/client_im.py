# -*- coding: utf-8 -*-
"""平台接口层 —— 私信 IM域（mixin）

## 设计来源（照源项目 better-douyin）

对标 `src-tauri/src/api/client_im*.rs（4 个）`（方案 B 逆向情报）。

## 迁移说明（本分支 design/better-douyin，阶段1）

本模块由 `douyin_api.py` 的 `DouyinAPI` **机械拆分**而来（ast 精确取体，
非重写）：方法体逐字节保持原样，仅换宿主。

**内部调用保持 `DouyinAPI.xxx` 形式不变**——最终类仍是 `DouyinAPI`
（由 `dy_apis/douyin_api.py` 组装全部 mixin），故 109 处内部引用与
300+ 处外部调用**零改动**。

## 本域方法

```
  create_conversation
  get_conversation_list
  get_conversation_list_all
  get_message_by_init
  parse_init_conversations
  send_msg
  _classify_send_fail
  get_im_user_info
```
"""
from __future__ import annotations

# 本域所需的导入（与原 `douyin_api.py` 头部一致，避免循环依赖）
import json
import random
import re
import time
import urllib
import uuid

import requests
requests.packages.urllib3.disable_warnings()
from bs4 import BeautifulSoup
from loguru import logger
from google.protobuf.json_format import MessageToDict as _message_to_dict


def protobuf_to_dict(message):
    return _message_to_dict(message, preserving_proto_field_name=True)

import static.Response_pb2 as ResponseProto
from builder.header import HeaderBuilder, HeaderType
from builder.params import Params
from builder.proto import ProtoBuilder
from utils.fingerprint import get_profile
from utils.dy_util import splice_url, generate_a_bogus, generate_msToken, trans_cookies, generate_a_bogus_pure



#: 组装后的最终类（由 `dy_apis/_bindings.py: bind_all()` 注入本模块全局）。
#: 域模块内保留原文件的 `DouyinAPI.xxx(...)` 内部调用形式（共 109 处），
#: 靠此全局名解析到最终类 —— 从而**无需改动任何内部调用**（门面模式的关键）。
DouyinAPI = None  # type: ignore[assignment]


class ImMixin:
    """私信 IM域接口（来自 DouyinAPI）。"""

    @staticmethod
    def create_conversation(auth, to_user_id: int, **kwargs):
        """
        创建私信对话.
        :param auth: DouyinAuth object.
        :param to_user_id: 私信对话接收者ID.
        :return: 私信对话ID.
        """
        # 私信建会话走抖音 IM 私有网关 imapi.douyin.com（与 send_msg 同域），
        # 该接口靠 cookie + protobuf 内签名（web_protect 注入的 ticket/ts_sign/sdk_cert）鉴权，
        # 不需要 www.douyin.com 那套 msToken/a_bogus query（那是网页版 IM 接口参数，私有网关无此路径，
        # 强行拼 www.douyin.com 路径会返回 404 Unsupported path(Janus)）。
        url = "https://imapi.douyin.com/v2/conversation/create"
        requestProto = ProtoBuilder.build_create_conversation_request(auth, to_user_id, auth.get_uid())
        # IM 私有网关 imapi.douyin.com 靠 protobuf body 内签名(ticket/ts_sign/sdk_cert)鉴权，
        # 不叠加 www.douyin.com 的 bd-ticket-guard-* HTTP 头（叠加反而 INVALID_REQUEST）。
        headers = HeaderBuilder().build(HeaderType.PROTOBUF)
        headers.set_header('referer', 'https://www.douyin.com/')
        resp = requests.post(
            url,
            headers=headers.get(),
            cookies=auth.cookie,
            data=requestProto.SerializeToString(),
            verify=False
        )
        # 解析前先判定 HTTP 状态与非 protobuf 响应（抖音常返回 HTML/JSON 错误页）
        if resp.status_code != 200:
            raise RuntimeError(
                f"create_conversation HTTP {resp.status_code}: {resp.text[:200]}")
        ctype = resp.headers.get("Content-Type", "")
        if "application/x-protobuf" not in ctype and "octet-stream" not in ctype \
                and resp.content[:1] not in (b'\x08', b'\x12', b'\x1a', b'\x22'):
            # 看起来像 JSON/HTML 错误响应
            raise RuntimeError(
                f"create_conversation 返回非 protobuf 响应(Content-Type={ctype}): {resp.text[:200]}")
        responseProto = ResponseProto.Response()
        try:
            responseProto.ParseFromString(resp.content)
        except Exception as e:
            raise RuntimeError(
                f"create_conversation 响应 protobuf 解析失败(Wire corrupt?): {e} | "
                f"raw[:120]={resp.content[:120]!r}")
        resp_json = protobuf_to_dict(responseProto)
        # 暴露抖音返回的业务错误（message/error_desc/status_code），而不是裸 KeyError
        biz_msg = resp_json.get("message") or resp_json.get("error_desc") or ""
        body = resp_json.get("body") or {}
        conv = body.get("create_conversation_v2_body")
        if not conv or not conv.get("conversation_info_list"):
            detail = ""
            if biz_msg:
                detail = f" message={biz_msg!r}"
            raise RuntimeError(
                f"create_conversation 响应缺少 create_conversation_v2_body{detail} | "
                f"resp_json={resp_json}")
        conversation = conv["conversation_info_list"][0]
        conversation_id = conversation['conversation_id']
        conversation_short_id, ticket = int(conversation['conversation_short_id']), conversation['ticket']
        return conversation_id, conversation_short_id, ticket

    @staticmethod
    def get_conversation_list(auth, conversation_short_id: int = 0, **kwargs) -> list:
        """拉取一页私信会话列表（cmd 610），用 conversation_short_id 作分页游标。

        conversation_short_id=0 返回第一页；传入上一页最后一条的 short_id 返回下一页。
        每页条数由服务端决定（通常 20~50）。
        """
        my_id = auth.get_uid()
        url = "https://imapi.douyin.com/v2/conversation/get_info_list"
        requestProto = ProtoBuilder.build_get_conversation_list_info_request(
            auth, int(my_id), int(my_id), conversation_short_id)
        headers = HeaderBuilder().build(HeaderType.PROTOBUF)
        headers.set_header('referer', 'https://www.douyin.com/')

        resp = requests.post(
            url,
            headers=headers.get(),
            cookies=auth.cookie,
            data=requestProto.SerializeToString(),
            verify=False
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"get_conversation_list HTTP {resp.status_code}: {resp.text[:200]}")
        ctype = resp.headers.get("Content-Type", "")
        if "application/x-protobuf" not in ctype and "octet-stream" not in ctype \
                and resp.content[:1] not in (b'\x08', b'\x12', b'\x1a', b'\x22'):
            raise RuntimeError(
                f"get_conversation_list 返回非 protobuf 响应(Content-Type={ctype}): {resp.text[:200]}")
        responseProto = ResponseProto.Response()
        try:
            responseProto.ParseFromString(resp.content)
        except Exception as e:
            raise RuntimeError(
                f"get_conversation_list 响应 protobuf 解析失败(Wire corrupt?): {e} | "
                f"raw[:120]={resp.content[:120]!r}")
        resp_json = protobuf_to_dict(responseProto)
        body = resp_json.get("body") or {}
        conv_body = body.get("get_conversation_info_list_v2_response_body") or {}
        conv_list = conv_body.get("conversation_info_list") or []
        return conv_list

    @staticmethod
    def get_conversation_list_all(auth, max_pages: int = 20) -> list:
        """分页拉取【全部】私信会话列表，直到无更多结果或达到 max_pages。

        conversation_id 格式 `0:1:<uid_a>:<uid_b>` 中提取非自身 uid 作为 peer_id。
        """
        my_uid = str(auth.get_uid())
        all_convs = []
        cursor = 0
        seen_ids = set()
        for _ in range(max_pages):
            try:
                page = DouyinAPI.get_conversation_list(auth, conversation_short_id=cursor)
            except Exception as e:
                logger.warning("AUTH-022", f"[im] 分页拉取会话列表第 {_+1} 页失败: {e}")
                break
            if not page:
                break
            new_count = 0
            for info in page:
                conv_id = info.get("conversation_id") or ""
                short_id = info.get("conversation_short_id")
                if not conv_id or conv_id in seen_ids:
                    continue
                seen_ids.add(conv_id)
                # 从 conversation_id 解析对方 uid：格式 0:1:<uid_a>:<uid_b>
                parts = conv_id.split(":")
                peer_uid = None
                if len(parts) >= 4:
                    uid_a, uid_b = parts[2], parts[3]
                    if uid_a != my_uid:
                        peer_uid = uid_a
                    elif uid_b != my_uid:
                        peer_uid = uid_b
                    # 两者相同（自身会话）→ peer_uid=None，跳过
                info["_peer_uid"] = peer_uid
                all_convs.append(info)
                new_count += 1
            # 用最后一条的 short_id 作为下一页游标
            last_short = page[-1].get("conversation_short_id")
            if not last_short or new_count == 0 or int(last_short) == cursor:
                break
            cursor = int(last_short)
        logger.info(f"[im] 分页拉取完成：共 {len(all_convs)} 个会话")
        return all_convs

    @staticmethod
    def get_message_by_init(auth) -> bytes:
        """调 imapi get_message_by_init（cmd 2043）获取全量会话初始化数据。

        返回原始 protobuf 响应字节（250KB+，含全部会话 ID + 消息 + peer uid）。
        后续用正则从原始字节中提取 conversation_id 和 peer uid（proto 未定义 cmd 2043）。

        实测来源：抖音网页 douyin.com/chat 首次加载时调用此 API。
        """
        from builder.proto import ProtoBuilder
        from builder.header import HeaderBuilder, HeaderType
        # 用通用 build_normal_request 构建 cmd=2043 请求（与网页请求结构一致）
        request = ProtoBuilder.build_normal_request(auth, 2043)
        # body 字段：proto 未定义 cmd 2043 的 oneof，手动追加最小 body 字节
        # 网页请求体 field 8 (body) = tag(da 7f) + len(02) + content(10 00) = field 2 varint 0
        body_bytes = request.SerializeToString()
        # 在 body field (field 8) 后追加 cmd 2043 body：field 2043 (varint tag da7f) + len 2 + field 2 varint 0
        # tag for field 2043 wire type 2: (2043 << 3) | 2 = 16346, varint = da 7f
        init_body = bytes([0xda, 0x7f, 0x02, 0x10, 0x00])
        # 在 Request 序列化结果中，field 8 (body) 当前为空字符串 (22 00)
        # 替换为含 init_body 的版本：tag 42 + len + init_body
        # 简单做法：直接在序列化末尾追加 body field
        body_bytes = body_bytes + bytes([0x42, len(init_body)]) + init_body

        url = "https://imapi.douyin.com/v1/message/get_message_by_init"
        headers = HeaderBuilder().build(HeaderType.PROTOBUF)
        headers.set_header('referer', 'https://www.douyin.com/')
        resp = requests.post(
            url, headers=headers.get(), cookies=auth.cookie,
            data=body_bytes, verify=False, timeout=15,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"get_message_by_init HTTP {resp.status_code}: {resp.text[:200]}")
        logger.info(f"[im] get_message_by_init 响应 {len(resp.content)} bytes")
        return resp.content

    @staticmethod
    def parse_init_conversations(raw: bytes, my_uid: str) -> list[dict]:
        """从 get_message_by_init 原始字节中提取全部会话 + peer uid + sec_uid（正则法）。

        conversation_id 格式 0:1:<uid_a>:<uid_b>，其中非自身 uid 即对方 uid。
        同时提取 sec_uid（MS4wLjAB...格式），就近匹配到会话用于后续昵称解析。
        """
        import re
        decoded = raw.decode("utf-8", errors="replace")
        # 1) 找所有 conversation_id 及位置
        seen = set()
        result = []
        for m in re.finditer(r'0:1:\d{5,20}:\d{5,20}', decoded):
            cid = m.group()
            if cid in seen:
                continue
            seen.add(cid)
            parts = cid.split(":")
            uid_a, uid_b = parts[2], parts[3]
            if uid_a == uid_b:
                continue  # 跳过自身会话
            peer_uid = uid_b if uid_a == my_uid else uid_a
            result.append({"pos": m.start(), "conversation_id": cid, "peer_uid": peer_uid})

        # 2) 找所有 sec_uid 及位置（MS4wLjAB 开头，10~60 字符）
        sec_positions = [(m.start(), m.group()) for m in re.finditer(r'MS4wLjAB[\w_]{10,60}', decoded)]
        sec_seen = set()
        unique_secs = [(p, s) for p, s in sec_positions if not (s in sec_seen or sec_seen.add(s))]

        # 3) 就近匹配 sec_uid 到会话（±800 字节内最近的）
        for c in result:
            best_sec = None
            best_dist = 800
            for spos, sec in unique_secs:
                dist = abs(spos - c["pos"])
                if dist < best_dist:
                    best_dist = dist
                    best_sec = sec
            c["sec_uid"] = best_sec

        logger.info(f"[im] 从 init 响应中提取 {len(result)} 个真实会话，"
                     f"{len(unique_secs)} 个 sec_uid，"
                     f"匹配到 sec_uid 的会话 {sum(1 for c in result if c.get('sec_uid'))}/{len(result)}")
        return result

    @staticmethod
    def send_msg(auth, conversation_id, conversation_short_id, ticket, content: str, **kwargs) -> tuple:
        """
        发送私信（V2：返回 (bool ok, str detail)）。
        :param auth: DouyinAuth object.
        :param conversation_id: 私信对话ID.
        :param conversation_short_id: 私信对话短ID.
        :param ticket: 私信对话票据.
        :param content: 私信内容.
        :return: (True, 'ok') 发送成功；否则 (False, 具体原因)
        """
        # 文案为空防护：抖音对空消息会返回 OK 但实际未发送（被截断的根因之一）
        if not content or not str(content).strip():
            logger.error("AUTH-024", "[私信] 文案为空，拒绝发送（避免日志显示成功但实际未发送）")
            return False, "文案为空，拒绝发送"
        # 私信文案长度受限：超长会被平台截断/静默丢弃，先本地拦截
        if len(str(content)) > 500:
            logger.warning("AUTH-025", f"[私信] 文案长度 {len(str(content))} 超过 500 字，可能被平台截断，仅前 500 字发送")
        url = 'https://imapi.douyin.com/v1/message/send'
        # IM 私有网关靠 protobuf body 内签名鉴权，不叠加 bd-ticket-guard-* HTTP 头
        headers = HeaderBuilder().build(HeaderType.PROTOBUF)
        headers.set_header('referer', 'https://www.douyin.com/')
        requestProto = ProtoBuilder.build_send_message_request(auth, conversation_id, conversation_short_id, ticket,
                                                               content)
        webid = auth.cookie.get('s_v_web_id', '')
        # 回归基座私信设计：msToken 必须随请求动态生成（随机 107 位），
        # 空串会被 IM 网关风控拒绝(KICK/INVALID_REQUEST)。优先用 cookie 中真实 msToken，
        # 缺失则动态生成（基座原版即 generate_msToken()）。
        _cookie_ms = auth.cookie.get('msToken') or ''
        params = {
            'verifyFp': webid,
            'fp': webid,
            'msToken': _cookie_ms if _cookie_ms else generate_msToken()
        }
        query = splice_url(params)
        abogus = generate_a_bogus(query)
        params['a_bogus'] = abogus
        resp = requests.post(url, params=params, headers=headers.get(), verify=False, cookies=auth.cookie,
                             data=requestProto.SerializeToString())
        if resp.status_code != 200:
            logger.error("AUTH-026", f'私信发送 HTTP {resp.status_code}: {resp.text[:200]}')
            return False, f'私信发送 HTTP {resp.status_code}: {resp.text[:200]}'
        responseProto = ResponseProto.Response()
        try:
            responseProto.ParseFromString(resp.content)
        except Exception as e:
            # 2026-09-08：抖音风控/异常时返回 JSON（如 {"decision":"KICK"}）
            # 而非 protobuf——原日志报"Wire format was corrupt"误导排查。
            # 先尝试 JSON 解析，给出真实风控原因。
            try:
                import json as _json
                _j = _json.loads(resp.content.decode("utf-8", "replace"))
                _dec = _j.get("decision") or _j.get("message") or str(_j)[:80]
                logger.error("AUTH-027", 
                    f"私信发送被抖音拒绝（JSON 响应）：decision={_dec} | "
                    f"full={str(_j)[:200]}")
                return False, f"抖音拒绝发送：{_dec}（风控/限流，建议冷却后重试）"
            except Exception:
                pass
            logger.error("AUTH-028", f'私信发送响应 protobuf 解析失败: {e} | raw[:120]={resp.content[:120]!r}')
            return False, f'响应用户解析失败: {e}'
        resp_json = protobuf_to_dict(responseProto)
        success = resp_json.get('message') == 'OK'
        if success:
            logger.info(f'私信发送成功 conversation_id={conversation_id}')
            return True, 'ok'
        detail = DouyinAPI._classify_send_fail(resp_json)
        logger.error("AUTH-029", f'私信发送失败 conversation_id={conversation_id} resp_json={resp_json}')
        return False, detail

    @staticmethod
    def _classify_send_fail(resp_json: dict) -> str:
        """把抖音 IM 发送失败响应解析成人类可读的原因（发送结果反馈）。

        覆盖：需互关、发送频繁/被频控、隐私权限、账号风控、用户不存在等。
        """
        raw_msg = str(resp_json.get("message") or "").upper()
        err = str(resp_json.get("error_desc") or "")
        status = str(resp_json.get("status_code") or "")
        combined = " ".join([raw_msg, err, status]).upper()
        # 顺序敏感：先命中具体场景，再兜底
        if raw_msg == "OK":
            return "发送被静默拦截（返回 OK 但未实际投递，疑似内容违规/被截断）"
        if any(k in combined for k in ("MUTUAL", "FOLLOW_EACH", "NEED_FOLLOW", "INTERACT")):
            return "对方需与你互关后才能收到私信"
        if any(k in combined for k in ("PRIVILEGE", "PERMISSION", "PRIVACY")):
            return "对方隐私/权限设置限制，无法主动私信"
        if any(k in combined for k in ("FREQUENT", "RATE", "TOO_", "LIMIT", "SPAM", "FREQUENCY")):
            return "发送过于频繁，被平台频控拦截，请降低频率或更换账号"
        if any(k in combined for k in ("KICK", "INVALID_REQUEST", "RISK")):
            return f"私信被风控(KICK/INVALID_REQUEST): {err or raw_msg}"
        if any(k in combined for k in ("NOT_FOUND", "NOT FOUND", "USER_NOT_EXIST")):
            return "对方不存在或已注销"
        if any(k in combined for k in ("DENY", "FORBIDDEN", "BLOCK")):
            return f"发送被拒绝: {err or raw_msg}"
        base = (err or raw_msg or status or str(resp_json.get("status", ""))).strip()
        if base and base != "OK":
            return f"发送失败: {base}"
        return "发送失败（未知原因，请查看运行日志）"

    @staticmethod
    def get_im_user_info(auth, uid: str) -> dict:
        """调 REST JSON API 解析用户昵称/头像（GET + to_user_id）。

        注：CDP 实测浏览器用 POST + sec_user_ids，但 a_bogus 签名无法复现
        （status=8）。GET + to_user_id 经验证可返回昵称/头像，作退路保留。
        """
        from builder.params import Params
        from utils.fingerprint import get_profile
        api = "/aweme/v1/web/im/user/info/"
        headers = HeaderBuilder().build(HeaderType.GET)
        headers.set_referer("https://www.douyin.com/")
        params = Params()
        (params
         .add_param("device_platform", "webapp")
         .add_param("aid", "6383")
         .add_param("channel", "channel_pc_web")
         .add_param("to_user_id", str(uid))
         .add_param("pc_client_type", "1")
         .add_param("update_version_code", "170400")
         .add_param("version_code", "170400")
         .add_param("cookie_enabled", "true")
         .add_param("browser_language", "zh-CN")
         .add_param("browser_platform", "Win32")
         .add_param("browser_name", get_profile()["browser_name"])
         .add_param("browser_version", get_profile()["browser_version"])
         .add_param("browser_online", "true")
         .add_param("engine_name", "Blink")
         .add_param("os_name", "Windows")
         .add_param("os_version", "10")
         .add_param("platform", "PC")
         .add_param("downlink", "10")
         .add_param("effective_type", "4g")
         .add_param("round_trip_time", "100"))
        params.with_web_id(auth, f"https://www.douyin.com/")
        params.add_param("msToken", auth.cookie.get("msToken", ""))
        params.add_param("verifyFp", auth.cookie.get("s_v_web_id", ""))
        params.add_param("fp", auth.cookie.get("s_v_web_id", ""))
        params.with_a_bogus()
        resp = requests.get(
            f'{DouyinAPI.douyin_url}{api}',
            headers=headers.get(), cookies=auth.cookie,
            params=params.get(), verify=False, timeout=8,
        )
        data = json.loads(resp.text)
        if data.get("status_code") != 0:
            logger.warning("AUTH-023", f"[im] get_im_user_info uid={uid} status={data.get('status_code')}")
            return {}
        items = data.get("data") or []
        if not items:
            return {}
        u = items[0]
        avatar_small = u.get("avatar_small") or u.get("avatar_thumb") or {}
        avatar_url = avatar_small.get("url_list", [""])[0] if avatar_small.get("url_list") else ""
        return {
            "uid": str(u.get("uid") or uid),
            "nickname": u.get("nickname") or uid,
            "avatar": avatar_url,
            "sec_uid": u.get("sec_uid") or "",
            "follow_status": u.get("follow_status"),
            "follower_status": u.get("follower_status"),
        }

