# -*- coding: utf-8 -*-
"""平台接口层 —— 作品域（mixin）

## 设计来源（照源项目 better-douyin）

对标 `src-tauri/src/api/client_video.rs`（方案 B 逆向情报）。

## 迁移说明（本分支 design/better-douyin，阶段1）

本模块由 `douyin_api.py` 的 `DouyinAPI` **机械拆分**而来（ast 精确取体，
非重写）：方法体逐字节保持原样，仅换宿主。

**内部调用保持 `DouyinAPI.xxx` 形式不变**——最终类仍是 `DouyinAPI`
（由 `dy_apis/douyin_api.py` 组装全部 mixin），故 109 处内部引用与
300+ 处外部调用**零改动**。

## 本域方法

```
  get_work_info
  search_some_video_work
  search_video_work
  get_feed
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


class VideoMixin:
    """作品域接口（来自 DouyinAPI）。"""

    @staticmethod
    def get_work_info(auth, url: str) -> dict:
        """
        获取作品信息.
        :param auth: DouyinAuth object.
        :param url: 作品URL.
        :return: JSON.
        """
        api = f"/aweme/v1/web/aweme/detail/"
        if 'video' in url:
            aweme_id = url.split("/")[-1].split("?")[0]
        else:
            aweme_id = re.findall(r'modal_id=(\d+)', url)[0]
            url = f'https://www.douyin.com/video/{aweme_id}'
        headers = HeaderBuilder().build(HeaderType.GET)
        headers.set_referer(url)
        params = Params()
        params.add_param("device_platform", "webapp")
        params.add_param("aid", "6383")
        params.add_param("channel", "channel_pc_web")
        params.add_param("aweme_id", aweme_id)
        params.add_param("update_version_code", "170400")
        params.add_param("pc_client_type", "1")
        params.add_param("version_code", "190500")
        params.add_param("version_name", "19.5.0")
        params.add_param("cookie_enabled", "true")
        params.add_param("screen_width", get_profile()["screen_width"])
        params.add_param("screen_height", get_profile()["screen_height"])
        params.add_param("browser_language", "zh-CN")
        params.add_param("browser_platform", "Win32")
        params.add_param("browser_name", get_profile()["browser_name"])
        params.add_param("browser_version", get_profile()["browser_version"])
        params.add_param("browser_online", "true")
        params.add_param("engine_name", "Blink")
        params.add_param("engine_version", get_profile()["engine_version"])
        params.add_param("os_name", "Windows")
        params.add_param("os_version", get_profile()["os_version"])
        params.add_param("cpu_core_num", get_profile()["cpu_core_num"])
        params.add_param("device_memory", get_profile()["device_memory"])
        params.add_param("platform", "PC")
        params.add_param("downlink", "4.75")
        params.add_param("effective_type", "4g")
        params.add_param("round_trip_time", "150")
        params.with_web_id(auth, url)
        params.add_param("msToken", auth.msToken)
        params.with_a_bogus()
        params.add_param("verifyFp", auth.cookie['s_v_web_id'])
        params.add_param("fp", auth.cookie['s_v_web_id'])
        resp = requests.get(f'{DouyinAPI.douyin_url}{api}', headers=headers.get(), cookies=auth.cookie,
                            params=params.get(), verify=False)
        resp_json = json.loads(resp.text)
        return resp_json

    @staticmethod
    def search_some_video_work(auth, query: str, num: int = 16, sort_type: str = '0', publish_time: str = '0',
                               filter_duration="", search_range="0", **kwargs) -> tuple:
        """
        搜索视频频道作品.
        :param auth: DouyinAuth object.
        :param query: 搜索关键字.
        :param num: 搜索结果数量.
        :param sort_type: 排序方式 0 综合排序 1 最多点赞 2 最新发布.
        :param publish_time: 发布时间 0 不限 1 一天内 7 一周内 180 半年内.
        :param filter_duration: 视频时长 空字符串 不限 0-1 一分钟内 1-5 1-5分钟内 5-10000 5分钟以上
        :param search_range: 搜索范围 0 不限 3 关注的人 1 最近看过 2 还未看过
        :return: 作品列表, 引导词.
        """
        offset = "0"
        count = "25"
        search_id = ""
        video_work_list = []
        while True:
            search_id, guide_search_words, res_json = DouyinAPI.search_video_work(auth, query, offset, count, sort_type,
                                                                                  publish_time, filter_duration,
                                                                                  search_range, search_id)
            video_works = res_json["data"]
            video_work_list.extend(video_works)
            if res_json["has_more"] != 1 or len(video_work_list) >= num:
                break
            offset = str(int(offset) + int(count))
        if len(video_work_list) > num:
            video_work_list = video_work_list[:num]
        return video_work_list, guide_search_words

    @staticmethod
    def search_video_work(auth, query: str, offset: str = '0', count: str = '16', sort_type: str = '0',
                          publish_time: str = '0', filter_duration="", search_range="0", search_id="", **kwargs):
        """
        搜索视频频道作品.
        :param auth: DouyinAuth object.
        :param query: 搜索关键字.
        :param offset: 搜索结果偏移量.
        :param count: 搜索结果数量.
        :param sort_type: 排序方式 0 综合排序 1 最多点赞 2 最新发布.
        :param publish_time: 发布时间 0 不限 1 一天内 7 一周内 180 半年内.
        :param filter_duration: 视频时长 空字符串 不限 0-1 一分钟内 1-5 1-5分钟内 5-10000 5分钟以上
        :param search_range: 搜索范围 0 不限 3 关注的人 1 最近看过 2 还未看过
        :return: 下个搜索ID, 引导词, JSON数据.
        """
        api = "/aweme/v1/web/search/item/"
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = f'https://www.douyin.com/search/{urllib.parse.quote(query)}?aid={uuid.uuid4()}&type=video'
        headers.set_referer(refer)
        params = Params()
        params.add_param("device_platform", 'webapp')
        params.add_param("aid", '6383')
        params.add_param("channel", 'channel_pc_web')
        params.add_param("search_channel", 'aweme_video_web')
        params.add_param("enable_history", '1')
        params.add_param("sort_type", sort_type)
        params.add_param("publish_time", publish_time)
        params.add_param("filter_duration", filter_duration)
        params.add_param("search_range", search_range)
        params.add_param("keyword", query)
        params.add_param("search_source", 'normal_search')
        params.add_param("query_correct_type", '1')
        params.add_param("is_filter_search", '1')
        params.add_param("from_group_id", '')
        params.add_param("offset", offset)
        params.add_param("count", count)
        params.add_param("need_filter_settings", '1' if offset == '0' else '0')
        if search_id != "":
            params.add_param("search_id", search_id)
        params.add_param("list_type", 'single')
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
        params.add_param("round_trip_time", '50')
        params.with_web_id(auth, refer)
        params.add_param("msToken", auth.msToken)
        params.with_a_bogus()
        params.add_param("verifyFp", auth.cookie['s_v_web_id'])
        params.add_param("fp", auth.cookie['s_v_web_id'])
        resp = requests.get(f'{DouyinAPI.douyin_url}{api}', headers=headers.get(), cookies=auth.cookie,
                            params=params.get(), verify=False)
        search_id = resp.headers["X-Tt-Logid"]
        json_data = resp.json()
        return search_id, json_data["guide_search_words"], json_data

    @staticmethod
    def get_feed(auth, count='20', refresh_index='2', **kwargs):
        """
        获取首页推荐视频
        :param auth: DouyinAuth object.
        :param count: 数量.
        :param refresh_index: 刷新索引.
        :return: JSON.
        """
        api = "/aweme/v1/web/module/feed/"
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = "https://www.douyin.com/"
        headers.set_referer(refer)
        params = Params()
        params.add_param("device_platform", 'webapp')
        params.add_param("aid", '6383')
        params.add_param("channel", 'channel_pc_web')
        params.add_param("module_id", '3003101')
        params.add_param("count", count)
        params.add_param("filterGids", '')
        params.add_param("presented_ids", '')
        params.add_param("refresh_index", refresh_index)
        params.add_param("refer_id", '')
        params.add_param("refer_type", '10')
        params.add_param("awemePcRecRawData", '{"is_client":false}')
        params.add_param("Seo-Flag", '0')
        params.add_param("install_time", '1715480185')
        params.add_param("pc_client_type", '1')
        params.add_param("update_version_code", '170400')
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
        params.add_param("round_trip_time", '100')
        params.with_web_id(auth, refer)
        params.add_param("msToken", auth.msToken)
        params.with_a_bogus()
        params.add_param("verifyFp", auth.cookie['s_v_web_id'])
        params.add_param("fp", auth.cookie['s_v_web_id'])

        res = requests.get(f'{DouyinAPI.douyin_url}{api}', headers=headers.get(), params=params.get(),
                           cookies=auth.cookie, verify=False)
        return res.json()

