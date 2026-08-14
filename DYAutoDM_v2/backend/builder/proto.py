#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time : 2024/6/8 下午6:57
# @Author : crush0
# @Description :
import base64
import json
import random

import uuid

from utils.fingerprint import get_profile
import static.Request_pb2 as RequestProto
from builder.header import HeaderBuilder
from utils.dy_util import generate_webid, generate_req_sign, generate_millisecond


class ProtoBuilder:
    @staticmethod
    def build_normal_request(auth, cmd):
        request = RequestProto.Request()
        request.cmd = cmd
        request.sequence_id = random.randint(10000, 11000)
        request.sdk_version = "1.1.3"
        request.token = auth.ticket
        request.refer = 3
        request.inbox_type = 0
        request.build_number = "5fa6ff1:Detached: 5fa6ff1111fd53aafc4c753505d3c93daad74d27"
        request.device_id = '0'
        request.device_platform = 'douyin_pc'
        request.headers['session_aid'] = '6383'
        request.headers['session_did'] = '0'
        request.headers['app_name'] = 'douyin_pc'
        request.headers['priority_region'] = 'cn'
        request.headers['user_agent'] = HeaderBuilder.ua
        request.headers['cookie_enabled'] = 'true'
        request.headers['browser_language'] = 'zh-CN'
        request.headers['browser_platform'] = 'Win32'
        request.headers['browser_name'] = get_profile()["browser_name"]
        request.headers['browser_version'] = get_profile()["browser_version"]
        request.headers['browser_online'] = 'true'
        request.headers['screen_width'] = get_profile()["screen_width"]
        request.headers['screen_height'] = get_profile()["screen_height"]
        request.headers['referer'] = ''
        request.headers['timezone_name'] = 'Etc/GMT-8'
        request.headers['deviceId'] = '0'
        request.headers['webid'] = generate_webid()
        # fp 字段原样写入 cookie 的 s_v_web_id。但抖音在风控验证态下会把 s_v_web_id
        # 写成占位值（如 "verify_msppk8gp_xxxx"），写入 fp 会污染 IM 签名导致 KICK/INVALID_REQUEST。
        # 识别占位值后清空 fp（不写脏值），由服务端按其它签名要素鉴权，避免签名被污染。
        _sv = auth.cookie.get('s_v_web_id') if getattr(auth, 'cookie', None) else None
        if _sv and not str(_sv).strip().startswith('verify_msppk8gp_') and str(_sv).strip().isdigit():
            request.headers['fp'] = _sv
        else:
            request.headers['fp'] = ''
        request.headers['is-retry'] = '0'
        request.auth_type = 4
        request.biz = 'douyin_web'
        request.access = 'web_sdk'
        request.ts_sign = auth.ts_sign
        request.sdk_cert = base64.b64encode(auth.client_cert.encode('utf-8')).decode('utf-8')
        return request

    @staticmethod
    def build_create_conversation_request(auth, toId, myId):
        request = ProtoBuilder.build_normal_request(auth, 609)
        request.body.create_conversation_v2_body.conversation_type = 1
        request.body.create_conversation_v2_body.participants.extend([int(toId), int(myId)])
        reuqest_sign = generate_req_sign({
            "sign_data": f"avatar_url=&idempotent_id=&name=&participants={toId},{myId}",
            "certType": "cookie",
            "scene": "web_protect"
        }, auth.private_key)
        request.reuqest_sign = reuqest_sign
        return request

    @staticmethod
    def build_get_conversation_list_info_request(auth, toId, myId, conversation_short_id):
        request = ProtoBuilder.build_normal_request(auth, 610)
        request.body.get_conversation_info_list_v2_body.data.conversation_id = f"0:1:{myId}:{toId}"
        request.body.get_conversation_info_list_v2_body.data.conversation_short_id = conversation_short_id
        request.body.get_conversation_info_list_v2_body.data.conversation_type = 1
        return request

    @staticmethod
    def build_send_message_request(auth, conversation_id, conversation_short_id, ticket, message):
        client_message_id = str(uuid.uuid4())
        request = ProtoBuilder.build_normal_request(auth, 100)
        msg_content = {
            "mention_users": [],
            "aweType": 700,
            "richTextInfos": [],
            "text": message
        }
        request.body.send_message_body.conversation_id = conversation_id
        request.body.send_message_body.conversation_type = 1
        request.body.send_message_body.conversation_short_id = conversation_short_id
        request.body.send_message_body.content = json.dumps(msg_content, ensure_ascii=False,
                                                            separators=(',', ':'))
        request.body.send_message_body.ext.append(
            RequestProto.ExtValue(key='s:client_message_id', value=client_message_id)
        )
        request.body.send_message_body.ext.append(
            RequestProto.ExtValue(key='s:stime', value=str(generate_millisecond()))
        )
        request.body.send_message_body.ext.append(
            RequestProto.ExtValue(key='s:mentioned_users', value='')
        )
        request.body.send_message_body.message_type = 7
        request.body.send_message_body.ticket = ticket
        request.body.send_message_body.client_message_id = client_message_id
        req_sign = generate_req_sign({
            "sign_data": f'content={json.dumps(msg_content, separators=(",", ":"), ensure_ascii=False)}' + f'&conversation_id={conversation_id}&conversation_short_id={conversation_short_id}',
            "certType": "cookie",
            "scene": "web_protect"
        }, auth.private_key)
        request.reuqest_sign = req_sign
        return request
