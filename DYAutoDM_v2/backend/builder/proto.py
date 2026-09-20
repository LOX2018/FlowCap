#!/usr/bin/env python
# -*- coding: utf-8 -*-
# @Time : 2024/6/8 下午6:57
# @Author : crush0
# @Description :
import json
import random

import uuid

from utils.fingerprint import get_profile
import static.Request_pb2 as RequestProto
from builder.header import HeaderBuilder
from utils.dy_util import generate_req_sign, generate_millisecond


class ProtoBuilder:
    # 2026 PC IM 信封常量（对齐源项目 cv-cat/DouYin_Spider）
    # 说明：PC IM 客户端的 protobuf 信封与主站版本独立演进，以下值要出现在
    # 每一个 imapi 请求上（会话读取与消息发送共通）。取值来源为源项目
    # builder/proto.py 的 2026 版实测常量，非本机推断。
    SDK_VERSION = "0.1.8"
    BUILD_NUMBER = "0d50935:feat/pc-im-groupB"
    VERSION_CODE = "360000"

    @staticmethod
    def build_normal_request(auth, cmd):
        request = RequestProto.Request()
        request.cmd = cmd
        request.sequence_id = random.randint(10000, 11000)
        request.sdk_version = ProtoBuilder.SDK_VERSION
        # 2026 信封**不再携带顶层 token** —— 鉴权由 bd-ticket-guard HTTP 头
        # 与（发送时的）会话内 ticket 承载。保留旧版 `token=auth.ticket`
        # 会让 body 与浏览器不一致，被抖音判 INVALID_REQUEST。
        request.refer = 3
        request.inbox_type = 0
        request.build_number = ProtoBuilder.BUILD_NUMBER
        request.device_id = '0'
        request.device_platform = 'douyin_pc'
        request.version_code = ProtoBuilder.VERSION_CODE
        request.headers['session_aid'] = '6383'
        request.headers['session_did'] = '0'
        request.headers['app_name'] = 'douyin_pc'
        request.headers['priority_region'] = 'cn'
        request.headers['user_agent'] = HeaderBuilder.ua
        request.headers['cookie_enabled'] = 'true'
        request.headers['browser_language'] = 'zh-CN'
        request.headers['browser_platform'] = 'Win32'
        # IM 的 protobuf 客户端用 navigator.appName/appVersion，与主站 REST
        # 的 Chrome 品牌/版本不同：browser_name 固定 "Mozilla"，
        # browser_version 为 UA 去掉前导 "Mozilla/"。
        request.headers['browser_name'] = 'Mozilla'
        request.headers['browser_version'] = get_profile()["ua"].replace(
            'Mozilla/', '', 1)
        request.headers['browser_online'] = 'true'
        request.headers['screen_width'] = get_profile()["screen_width"]
        request.headers['screen_height'] = get_profile()["screen_height"]
        # PC IM 从「精选」页进入；浏览器把该页 referrer 写进 protobuf map
        # （这不是 HTTP Referer）。
        request.headers['referer'] = 'https://www.douyin.com/jingxuan'
        request.headers['timezone_name'] = 'Asia/Shanghai'
        request.headers['deviceId'] = '0'
        request.headers['is-retry'] = '0'
        request.auth_type = 4
        request.biz = 'douyin_web'
        request.access = 'web_sdk'
        # 2026 信封的鉴权改由 bd-ticket-guard HTTP 头承担；旧 web_protect
        # 信封里的顶层 ts_sign/sdk_cert 字段**必须留空** —— 保留它们会让
        # body 变大且与 Chrome 抓包不符（抖音据此判 INVALID_REQUEST）。
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
    def build_send_message_request(auth, conversation_id, conversation_short_id, ticket,
                                   message=None, identity_security_token="",
                                   identity_security_device_id="", *,
                                   message_type=7, content=None, ext=None,
                                   mentioned_users=None, client_message_id=None):
        """构造 PC-IM ``message/send`` 信封（2026 版，对齐源项目）。

        ``message`` 保留为首个位置参数以兼容旧调用。富媒体消息通过
        ``content`` 传 JSON 对象并显式指定 ``message_type``。protobuf 字段
        始终是紧凑 JSON 字符串，与浏览器 SDK 一致。
        """
        if content is None:
            content = message
        if content is None:
            content = ""
        # 2026-09-21 回归（v0.44.16）：**文本消息必须包 aweType 信封**。
        #
        # 设计契约：protobuf 的 message_type=7（IM_TEXT）声明这是一条富媒体
        # 消息，其 content 不是裸文本，而是浏览器 SDK 的固定 JSON 信封
        #   {"aweType":700,"type":0,"richTextInfos":[],"text":"..."}
        # 服务端对裸文本会「收妥回 OK 但不投递」（GitHub cv-cat/DouYin_Spider
        # issue #44「返回 OK 对方收不到」、#64「10502」同一故障形态）。
        #
        # v0.43.97 重构本函数时为支持富媒体引入 content 参数，裸字符串落到
        # 下面的 else 分支直接 str() 发出，信封被静默丢弃 —— 09-19 23:44 起
        # 私信「显示成功实际未投递」即由此而来。
        #
        # 四来源一致（zhinjs/douyin-im buildDesktopTextContent、
        # cv-cat DouYin_Spider send_msg、xiaoxiaodafeng Douyin_messages、
        # 本项目 v0.43.97 前的实现）：aweType=700 / type=0 / richTextInfos=[]。
        if not isinstance(content, (dict, list, tuple)) and message_type == 7:
            content = {
                "aweType": 700,
                "type": 0,
                "richTextInfos": [],
                "text": str(content),
            }
        client_message_id = str(client_message_id or uuid.uuid4())
        request = ProtoBuilder.build_normal_request(auth, 100)
        if isinstance(content, (dict, list, tuple)):
            encoded_content = json.dumps(
                content, ensure_ascii=False, separators=(',', ':')
            )
        else:
            encoded_content = str(content)
        request.body.send_message_body.conversation_id = conversation_id
        request.body.send_message_body.conversation_type = 1
        request.body.send_message_body.conversation_short_id = conversation_short_id
        request.body.send_message_body.content = encoded_content
        # 浏览器每条消息都带这些扩展。保留调用方传入值，同时维持文本的默认。
        ext_map = dict(ext or {})
        defaults = {
            's:mentioned_users': ext_map.pop('s:mentioned_users', ''),
            's:client_message_id': ext_map.pop('s:client_message_id', client_message_id),
        }
        ext_map.pop('s:stime', None)
        for key, value in {**defaults, **ext_map}.items():
            request.body.send_message_body.ext.append(
                RequestProto.ExtValue(key=str(key), value=str(value))
            )
        request.body.send_message_body.ext.append(
            # PC IM 的 s:stime 是 ``<epoch-ms>.<5位随机数>``（如
            # ``1788015776891.21440``），不是旧客户端的裸毫秒整数。
            RequestProto.ExtValue(
                key='s:stime',
                value=f'{generate_millisecond()}.{random.randrange(100000):05d}',
            )
        )
        if mentioned_users:
            request.body.send_message_body.mentioned_users.extend(
                int(uid) for uid in mentioned_users
            )
        request.body.send_message_body.message_type = int(message_type)
        request.body.send_message_body.ticket = ticket
        request.body.send_message_body.client_message_id = client_message_id
        # 2026 版发送信封**没有顶层 reuqest_sign** —— 请求由 bd-ticket-guard
        # HTTP 头鉴权；会话 ticket 仍在 protobuf body 内。
        # 自 2026 PC IM rollout 起，message/send 通过 protobuf header map 携带
        # 短时有效的 identity-security 材料。保持可选，使只读/建会话请求
        # 在只需旧信封时仍可用。
        if identity_security_token:
            # 浏览器把 token 包成紧凑 JSON 对象，而非裸 token 字符串。
            request.headers['identity_security_token'] = json.dumps(
                {'token': str(identity_security_token)}, separators=(',', ':')
            )
        if identity_security_device_id:
            request.headers['identity_security_device_id'] = str(identity_security_device_id)
        request.headers['identity_security_aid'] = ''
        return request
