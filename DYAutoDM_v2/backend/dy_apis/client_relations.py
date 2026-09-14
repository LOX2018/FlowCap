# -*- coding: utf-8 -*-
"""平台接口层 —— 关系（粉丝/关注/点赞）域（mixin）

## 设计来源（照源项目 better-douyin）

对标 `src-tauri/src/api/client_relations.rs`（方案 B 逆向情报）。

## 迁移说明（本分支 design/better-douyin，阶段1）

本模块由 `douyin_api.py` 的 `DouyinAPI` **机械拆分**而来（ast 精确取体，
非重写）：方法体逐字节保持原样，仅换宿主。

**内部调用保持 `DouyinAPI.xxx` 形式不变**——最终类仍是 `DouyinAPI`
（由 `dy_apis/douyin_api.py` 组装全部 mixin），故 109 处内部引用与
300+ 处外部调用**零改动**。

## 本域方法

```
  get_user_follower_list
  get_some_user_follower_list
  get_user_following_list
  get_some_user_following_list
  digg
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


class RelationsMixin:
    """关系（粉丝/关注/点赞）域接口（来自 DouyinAPI）。"""

    @staticmethod
    def get_user_follower_list(auth, user_id: str, sec_id: str, max_time: str = '0', count: str = '20', **kwargs):
        """
        获取用户的粉丝列表
        :param auth: DouyinAuth object.
        :param user_id: 用户ID.
        :param sec_id: 用户sec_id.
        :param max_time: 最大时间戳.
        :param count: 数量.
        :return:  JSON.
        """
        api = "/aweme/v1/web/user/follower/list/"
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = f"https://www.douyin.com/user/{sec_id}"
        headers.set_referer(refer)
        params = Params()
        params.add_param("device_platform", 'webapp')
        params.add_param("aid", '6383')
        params.add_param("channel", 'channel_pc_web')
        params.add_param("user_id", user_id)
        params.add_param("sec_user_id", sec_id)
        params.add_param("offset", '0')
        params.add_param("min_time", '0')
        params.add_param("max_time", max_time)
        params.add_param("count", count)
        params.add_param("source_type", '2' if max_time == '0' else '1')
        params.add_param("gps_access", '0')
        params.add_param("address_book_access", '0')
        params.add_param("update_version_code", '170400')
        params.add_param("pc_client_type", '1')
        params.add_param("version_code", '170400')
        params.add_param("version_name", '17.4.0')
        params.add_param("cookie_enabled", 'true')
        params.add_param("screen_width", get_profile()["screen_width"])
        params.add_param("screen_height", get_profile()["screen_height"])
        params.add_param("browser_language", 'zh-CN')
        params.add_param("browser_platform", 'Win32')
        params.add_param("browser_name", get_profile()["browser_name"])
        params.add_param("browser_version", get_profile()["browser_version"])
        params.add_param("browser_online", 'true')
        params.add_param("engine_name", 'Blink')
        params.add_param("engine_version", get_profile()["engine_version"])
        params.add_param("os_name", 'Windows')
        params.add_param("os_version", '10')
        params.add_param("cpu_core_num", get_profile()["cpu_core_num"])
        params.add_param("device_memory", get_profile()["device_memory"])
        params.add_param("platform", 'PC')
        params.add_param("downlink", '10')
        params.add_param("effective_type", '4g')
        params.add_param("round_trip_time", '150')
        params.with_web_id(auth, refer)
        params.add_param("msToken", auth.msToken)
        params.with_a_bogus()
        params.add_param("verifyFp", auth.cookie['s_v_web_id'])
        params.add_param("fp", auth.cookie['s_v_web_id'])
        res = requests.get(f'{DouyinAPI.douyin_url}{api}', headers=headers.get(), params=params.get(),
                           cookies=auth.cookie, verify=False)
        return res.json()

    @staticmethod
    def get_some_user_follower_list(auth, user_id: str, sec_id: str, num: int, **kwargs) -> list:
        """
        获取用户的前num个粉丝列表
        :param auth: DouyinAuth object.
        :param user_id: 用户ID.
        :param sec_id: 用户sec_id.
        :param num: 要获取的数量
        :return: 粉丝列表.
        """
        max_time = "0"
        count = "20"
        follower_list = []
        while True:
            res_json = DouyinAPI.get_user_follower_list(auth, user_id, sec_id, max_time, count)
            followers = res_json["followers"]
            follower_list.extend(followers)
            if res_json["has_more"] != 1 or len(follower_list) >= num:
                break
            max_time = res_json["min_time"]
        if len(follower_list) > num:
            follower_list = follower_list[:num]
        return follower_list

    @staticmethod
    def get_user_following_list(auth, user_id: str, sec_id: str, max_time: str = '0', count: str = '20', **kwargs):
        """
        获取用户的关注列表
        :param auth: DouyinAuth object.
        :param user_id: 用户ID.
        :param sec_id: 用户sec_id.
        :param max_time: 最大时间戳.
        :param count: 数量.
        :return:
        """
        api = "/aweme/v1/web/user/following/list/"
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = f"https://www.douyin.com/user/{sec_id}"
        headers.set_referer(refer)
        params = Params()
        params.add_param("device_platform", 'webapp')
        params.add_param("aid", '6383')
        params.add_param("channel", 'channel_pc_web')
        params.add_param("user_id", user_id)
        params.add_param("sec_user_id", sec_id)
        params.add_param("offset", '0')
        params.add_param("min_time", '0')
        params.add_param("max_time", max_time)
        params.add_param("count", count)
        params.add_param("source_type", '2' if max_time == '0' else '1')
        params.add_param("gps_access", '0')
        params.add_param("address_book_access", '0')
        params.add_param("is_top", '1')
        params.add_param("update_version_code", '170400')
        params.add_param("pc_client_type", '1')
        params.add_param("version_code", '170400')
        params.add_param("version_name", '17.4.0')
        params.add_param("cookie_enabled", 'true')
        params.add_param("screen_width", get_profile()["screen_width"])
        params.add_param("screen_height", get_profile()["screen_height"])
        params.add_param("browser_language", 'zh-CN')
        params.add_param("browser_platform", 'Win32')
        params.add_param("browser_name", get_profile()["browser_name"])
        params.add_param("browser_version", get_profile()["browser_version"])
        params.add_param("browser_online", 'true')
        params.add_param("engine_name", 'Blink')
        params.add_param("engine_version", get_profile()["engine_version"])
        params.add_param("os_name", 'Windows')
        params.add_param("os_version", '10')
        params.add_param("cpu_core_num", get_profile()["cpu_core_num"])
        params.add_param("device_memory", get_profile()["device_memory"])
        params.add_param("platform", 'PC')
        params.add_param("downlink", '10')
        params.add_param("effective_type", '4g')
        params.add_param("round_trip_time", '150')
        params.with_web_id(auth, refer)
        params.add_param("msToken", auth.msToken)
        params.with_a_bogus()
        params.add_param("verifyFp", auth.cookie['s_v_web_id'])
        params.add_param("fp", auth.cookie['s_v_web_id'])
        res = requests.get(f'{DouyinAPI.douyin_url}{api}', headers=headers.get(), params=params.get(),
                           cookies=auth.cookie, verify=False)
        return res.json()

    @staticmethod
    def get_some_user_following_list(auth, user_id: str, sec_id: str, num: int, **kwargs) -> list:
        """
        获取用户的前num个关注列表
        :param auth: DouyinAuth object.
        :param user_id: 用户ID.
        :param sec_id: 用户sec_id.
        :param num: 要获取的数量
        :return: 关注列表.
        """
        max_time = "0"
        count = "20"
        following_list = []
        while True:
            res_json = DouyinAPI.get_user_following_list(auth, user_id, sec_id, max_time, count)
            followings = res_json["followings"]
            following_list.extend(followings)
            if res_json["has_more"] != 1 or len(following_list) >= num:
                break
            max_time = res_json["min_time"]
        if len(following_list) > num:
            following_list = following_list[:num]
        return following_list

    @staticmethod
    def digg(auth, aweme_id: str, digg_type: str = '1', **kwargs) -> bool:
        """
        点赞视频.
        :param auth: DouyinAuth object.
        :param aweme_id: 视频ID.
        :param digg_type: 点赞类型, 1: 点赞, 0: 取消点赞.
        :return: 0 点赞成功 1 取消点赞成功
        """
        api = '/aweme/v1/web/commit/item/digg/'
        url = f'{DouyinAPI.douyin_url}{api}'
        refer = f'{DouyinAPI.douyin_url}/discover?modal_id={aweme_id}'
        headers = HeaderBuilder.build(HeaderType.FORM)
        headers.set_header("Host", DouyinAPI.douyin_url.split("https://")[-1])
        headers.with_bd(api, auth)
        headers.with_csrf(auth.cookie_str)
        headers.set_header("origin", DouyinAPI.douyin_url)
        headers.set_header("referer", refer)
        params = Params()
        params.with_platform()
        params.with_ms_token()
        params.with_web_id(auth, refer)
        params.add_param('verifyFp', auth.cookie['s_v_web_id'])
        params.add_param('fp', auth.cookie['s_v_web_id'])
        params.with_a_bogus()
        data = {
            'aweme_id': aweme_id,
            'item_type': '0',
            'type': digg_type,
        }
        resp = requests.post(url, params=params.get(), headers=headers.get(), cookies=auth.cookie, data=data,
                             verify=False)
        print(resp.text)
        resp_json = json.loads(resp.text)
        return resp_json['is_digg'] == 0

