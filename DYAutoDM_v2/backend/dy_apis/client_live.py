# -*- coding: utf-8 -*-
"""平台接口层 —— 直播域（mixin）

## 设计来源（照源项目 better-douyin）

对标 `src-tauri/src/api/client_feed.rs（直播）`（方案 B 逆向情报）。

## 迁移说明（本分支 design/better-douyin，阶段1）

本模块由 `douyin_api.py` 的 `DouyinAPI` **机械拆分**而来（ast 精确取体，
非重写）：方法体逐字节保持原样，仅换宿主。

**内部调用保持 `DouyinAPI.xxx` 形式不变**——最终类仍是 `DouyinAPI`
（由 `dy_apis/douyin_api.py` 组装全部 mixin），故 109 处内部引用与
300+ 处外部调用**零改动**。

## 本域方法

```
  get_live_info
  get_live_production
  get_all_live_production
  get_live_production_detail
  get_rank_list
  get_webcast_detail
  linkmicApply
  linkmicWaitingList
  linkmicList
  linkmicCheck
  linkmicLeave
  diggLiveRoom
  sendMsgInRoom
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


class LiveMixin:
    """直播域接口（来自 DouyinAPI）。"""

    @staticmethod
    def get_live_info(auth_, live_id, **kwargs):
        """
        获取直播间信息.
        :param live_id: 直播间ID
        :return: 直播间ID, 用户ID, ttwid
        """
        url = "https://live.douyin.com/" + live_id
        headers = {
            "accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
            "accept-language": "zh-CN,zh;q=0.9,zh-TW;q=0.8,en;q=0.7,ja;q=0.6",
            "cache-control": "no-cache",
            "pragma": "no-cache",
            "priority": "u=0, i",
            "referer": "https://live.douyin.com/?from_nav=1",
            "sec-ch-ua": "\"Not)A;Brand\";v=\"8\", \"Chromium\";v=\"138\", \"Google Chrome\";v=\"138\"",
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": "\"Windows\"",
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "navigate",
            "sec-fetch-site": "same-origin",
            "upgrade-insecure-requests": "1",
            "user-agent": get_profile()["ua"]
        }
        res = requests.get(url, headers=headers, cookies=auth_.cookie, verify=False)
        ttwid = res.cookies.get_dict()['ttwid']
        soup = BeautifulSoup(res.text, 'html.parser')
        scripts = soup.select('script[nonce]')
        # print(res.text)
        for script in scripts:
            if script.string is not None and 'roomId' in script.string:
                try:
                    user_id = re.findall(r'\\"user_unique_id\\":\\"(\d+)\\"', script.string)[0]
                    room_id = re.findall(r'\\"roomId\\":\\"(\d+)\\"', script.string)[0]
                    user_unique_id = re.findall(r'\\"user_unique_id\\":\\"(\d+)\\"', script.string)[0]
                    room_info = re.findall(r'\\"roomInfo\\":\{\\"room\\":\{\\"id_str\\":\\".*?\\",\\"status\\":(.*?),\\"status_str\\":\\".*?\\",\\"title\\":\\"(.*?)\\"', script.string)[0]
                    # "anchor\":{\"id_str\":\"3998258005032616\",\
                    anchor_id = re.findall(r'\\"anchor\\":\{\\"id_str\\":\\"(\d+)\\"', script.string)[0]
                    # , \"sec_uid\":\"M
                    sec_uid = re.findall(r'\\"sec_uid\\":\\"(.*?)\\"', script.string)[0]
                    room_status = room_info[0]
                    room_title = room_info[1]
                    res = {
                        "room_id": room_id,
                        "user_id": user_id,
                        "user_unique_id": user_unique_id,
                        "anchor_id": anchor_id,
                        "sec_uid": sec_uid,
                        "ttwid": ttwid,
                        # 2 是直播中 4 是未开播
                        "room_status": room_status,
                        "room_title": room_title
                    }
                    print(res)
                    return res
                except Exception as e:
                    pass
        return None, None, None

    @staticmethod
    def get_live_production(auth, url: str, room_id: str, author_id: str, offset: str, **kwargs):
        """
        获取直播间的商品信息.
        :param auth: DouyinAuth object.
        :param url: 直播间链接.
        :param room_id: 直播间ID
        :param author_id: 主播ID
        :param offset: 翻页游标.
        :return: JSON 商品列表.
        """
        api = f"/live/promotions/page/"
        headers = HeaderBuilder().build(HeaderType.GET)
        headers.set_header("origin", DouyinAPI.live_url)
        headers.set_referer(url)
        params = Params()
        params.add_param("device_platform", "webapp")
        params.add_param("aid", "6383")
        params.add_param("channel", "channel_pc_web")
        params.add_param("room_id", room_id)
        params.add_param("author_id", author_id)
        params.add_param("offset", offset)
        params.add_param("limit", "20")
        params.add_param("pc_client_type", "1")
        params.add_param("version_code", "210800")
        params.add_param("version_name", "21.8.0")
        params.add_param("cookie_enabled", "true")
        params.add_param("screen_width", "2560")
        params.add_param("screen_height", "1440")
        params.add_param("browser_language", "zh-CN")
        params.add_param("browser_platform", "Win32")
        params.add_param("browser_name", get_profile()["browser_name"])
        params.add_param("browser_version", "121.0.0.0")
        params.add_param("browser_online", "true")
        params.add_param("engine_name", "Blink")
        params.add_param("engine_version", "121.0.0.0")
        params.add_param("os_name", "Windows")
        params.add_param("os_version", get_profile()["os_version"])
        params.add_param("cpu_core_num", "20")
        params.add_param("device_memory", get_profile()["device_memory"])
        params.add_param("platform", "PC")
        params.add_param("downlink", "10")
        params.add_param("effective_type", "4g")
        params.add_param("round_trip_time", "50")
        params.with_web_id(auth, url)
        params.add_param("msToken", auth.msToken)
        params.with_a_bogus()
        res = requests.post(f'{DouyinAPI.live_url}{api}', headers=headers.get(), cookies=auth.cookie,
                           params=params.get(), verify=False)
        return res.json()

    @staticmethod
    def get_all_live_production(auth, url: str, **kwargs):
        """
        获取直播间的所有商品信息.
        :param auth: DouyinAuth object.
        :param url: 直播间链接.
        :return:
        """
        room_info = DouyinAPI.get_live_info(auth, url.split("/")[-1].split("?")[0])
        room_id = room_info["room_id"]
        author_id = room_info["author_id"]
        offset = "0"
        production_list = []
        while True:
            res_json = DouyinAPI.get_live_production(auth, url, room_id, author_id, offset)
            productions = res_json["promotions"]
            production_list.extend(productions)
            offset = str(res_json["next_offset"])
            if offset == "-1":
                break
        return production_list

    @staticmethod
    def get_live_production_detail(auth, url, ec_promotion_id, sec_author_id, live_room_id, **kwargs):
        """
        获取直播间商品详情.
        :param auth: DouyinAuth object.
        :param url: 直播间链接.
        :param ec_promotion_id: 商品ID.
        :param sec_author_id: 主播ID
        :param live_room_id: 直播间ID
        :return: JSON 商品详情.
        """
        api = f"/ecom/product/detail/saas/pc/"
        headers = HeaderBuilder().build(HeaderType.FORM)
        headers.set_header("origin", DouyinAPI.live_url)
        headers.set_referer(url)
        headers.with_csrf(auth.cookie_str)
        params = Params()
        params.add_param("is_h5", "1")
        params.add_param("origin_type", "638301")
        params.add_param("device_platform", "webapp")
        params.add_param("aid", "6383")
        params.add_param("channel", "channel_pc_web")
        params.add_param("pc_client_type", "1")
        params.add_param("update_version_code", "170400")
        params.add_param("version_code", "")
        params.add_param("version_name", "")
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
        params.add_param("downlink", "1.7")
        params.add_param("effective_type", "4g")
        params.add_param("round_trip_time", "200")
        params.with_web_id(auth, url)
        params.add_param("msToken", auth.msToken)
        data = {
            "bff_type": "2",
            "ec_promotion_id": ec_promotion_id,
            "is_h5": "1",
            "item_id": "0",
            "live_room_id": live_room_id,
            "origin_type": "638301",
            "promotion_ids": ec_promotion_id,
            "room_id": live_room_id,
            "sec_author_id": sec_author_id,
            "use_new_price": "1"
        }
        params.with_a_bogus(data)
        res = requests.post(f'{DouyinAPI.live_url}{api}', headers=headers.get(), params=params.get(),
                            cookies=auth.cookie, data=data, verify=False)
        return res.json()

    @staticmethod
    def get_rank_list(auth, room_id: str, anchor_id: str, sec_anchor_id: str):
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = "https://live.douyin.com"
        headers.set_referer(refer)
        url = "https://live.douyin.com/webcast/ranklist/audience/"
        params = Params()

        # params = {
        #     "aid": "6383",
        #     "app_name": "douyin_web",
        #     "live_id": "1",
        #     "device_platform": "web",
        #     "language": "zh-CN",
        #     "enter_from": "web_live",
        #     "cookie_enabled": "true",
        #     "screen_width": "2560",
        #     "screen_height": "1600",
        #     "browser_language": "zh-CN",
        #     "browser_platform": "Win32",
        #     "browser_name": "Chrome",
        #     "browser_version": "138.0.0.0",
        #     "webcast_sdk_version": "2450",
        #     "room_id": "7527483067720583955",
        #     "anchor_id": "3998258005032616",
        #     "sec_anchor_id": "MS4wLjABAAAA2F3NX6RiboGdfcX98Hpp3JESCY-Z8Tw8jQD8aqs25qhdnQSvMyyAbVvnLq5NT_rN",
        #     "ignoreToast": "true",
        #     "rank_type": "30",
        #     "update_scene": "rank_message",
        #     "msToken": "-HpOqCxjx1MRFQP00onCIVOe7UekYXQKcayCMuaffyovdtusmV13ZavT6mmX24sWMlGVdZza4F-MWiGt6iddfmElCqbOu59e-RiUXuBfYxqkbM-OZRHlLQn6dcDCagr8olEfvFxMvSye3lYz4-_pvuAkUQjA-a8oShkGqRiUXlrD",
        #     "a_bogus": "OXsfhHXEd2WbedKSYCY5t53lU8DlNsuyFBiQbinue5Cuch0bDmPtknebJxow1Mjo5SpziCl77EUMbxxb0VXi11HpqmkvS8JWbTICVh8LgqqRTFisEHRTewgEHJebWOJEm5ojJ1k3ItmP2EA4L1riUQAjCAaj4Qkp/rrRda4aNItggzs9FNqxuxSDOXFNBRI4YE=="
        # }
        params.add_param("aid", "6383")
        params.add_param("app_name", "douyin_web")
        params.add_param("live_id", "1")
        params.add_param("device_platform", "web")
        params.add_param("language", "zh-CN")
        params.add_param("enter_from", "web_live")
        params.add_param("cookie_enabled", "true")
        params.add_param("screen_width", "2560")
        params.add_param("screen_height", "1600")
        params.add_param("browser_language", "zh-CN")
        params.add_param("browser_platform", "Win32")
        params.add_param("browser_name", "Chrome")
        params.add_param("browser_version", "138.0.0.0")
        params.add_param("webcast_sdk_version", "2450")
        params.add_param("room_id", room_id)
        params.add_param("anchor_id", anchor_id)
        params.add_param("sec_anchor_id", sec_anchor_id)
        params.add_param("ignoreToast", "true")
        params.add_param("rank_type", "30")
        params.add_param("update_scene", "rank_message")
        params.add_param("msToken", auth.msToken)
        params.with_a_bogus()
        response = requests.get(url, headers=headers.get(), params=params.get(),
                           cookies=auth.cookie, verify=False)

        print(response.text)
        print(response)
        return response.json()

    @staticmethod
    def get_webcast_detail(auth, user_id, room_id, url: str):
        api = f"/webcast/im/fetch/"
        headers = HeaderBuilder().build(HeaderType.FORM)
        headers.set_header("origin", DouyinAPI.live_url)
        headers.set_referer(url)
        headers.with_csrf(auth.cookie_str)
        params = Params()
        params.add_param("resp_content_type", "protobuf")
        params.add_param("did_rule", "3")
        params.add_param("device_id", "")
        params.add_param("app_name", "douyin_web")
        params.add_param("endpoint", "live_pc")
        params.add_param("support_wrds", "1")
        params.add_param("user_unique_id", str(user_id))
        params.add_param("identity", "audience")
        params.add_param("need_persist_msg_count", "15")
        params.add_param("insert_task_id", "")
        params.add_param("live_reason", "")
        params.add_param("room_id", room_id)
        params.add_param("version_code", "180800")
        params.add_param("last_rtt", "0")
        params.add_param("live_id", "1")
        params.add_param("aid", "6383")
        params.add_param("fetch_rule", "1")
        params.add_param("cursor", "")
        params.add_param("internal_ext", "")
        params.add_param("device_platform", "web")
        params.add_param("cookie_enabled", "true")
        params.add_param("screen_width", "2560")
        params.add_param("screen_height", "1440")
        params.add_param("browser_language", "en")
        params.add_param("browser_platform", "Win32")
        params.add_param("browser_name", "Mozilla")
        params.add_param("browser_version",
                         "5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36")
        params.add_param("browser_online", "true")
        params.add_param("tz_name", "Asia/Shanghai")
        params.add_param("msToken", auth.msToken)
        params.with_a_bogus()
        res = requests.get(f'{DouyinAPI.live_url}{api}', headers=headers.get(), params=params.get(),
                           cookies=auth.cookie, verify=False)
        return res.content

    @staticmethod
    # ================= 直播间连麦（2026-09-10，实测逆向：知识库 05 §5.8.1） =================
    # 实测抓包（v23~v30 探针）：申请 POST body 为
    #   anchor_id=<主播uid>&apply_type=0&guest_supported_vendor=4&link_type=2&room_id=<真实room_id>
    # 响应：{"data":{"linkmic_id_str":..., "auto_join":..., "waiting_list_offset":1,...},"status_code":0}
    # 查询类：waiting_list（排队人数 total_count）/ list/v2（连线者）/ check_audience_linkers（action）
    # 签名：与 diggLiveRoom 同范式（msToken + with_a_bogus），FORM 头 + csrf

    @staticmethod
    def linkmicApply(auth, room_id: str, anchor_id: str, apply_type: str = '0',
                     link_type: str = '2'):
        api = "/webcast/linkmic_audience/apply/"
        headers = HeaderBuilder().build(HeaderType.FORM)
        refer = f"https://live.douyin.com/{room_id}"
        headers.set_header("origin", DouyinAPI.live_url)
        headers.with_csrf(auth.cookie_str)
        headers.set_referer(refer)
        params = Params()
        params.add_param("aid", '6383')
        params.add_param("app_name", 'douyin_web')
        params.add_param("live_id", '1')
        params.add_param("device_platform", 'web')
        params.add_param("language", 'zh-CN')
        params.add_param("enter_from", 'link_share')
        params.add_param("cookie_enabled", 'true')
        params.add_param("screen_width", '1280')
        params.add_param("screen_height", '720')
        params.add_param("browser_language", 'zh-CN')
        params.add_param("browser_platform", 'Win32')
        params.add_param("browser_name", 'Chrome')
        params.add_param("browser_version", '148.0.0.0')
        params.add_param("os_name", 'Windows')
        params.add_param("os_version", '10')
        params.add_param("msToken", auth.msToken)
        data = {
            "anchor_id": str(anchor_id),
            "apply_type": str(apply_type),
            "guest_supported_vendor": '4',
            "link_type": str(link_type),
            "room_id": str(room_id),
        }
        params.with_a_bogus(data)
        res = requests.post(f'{DouyinAPI.live_url}{api}', headers=headers.get(),
                            params=params.get(), cookies=auth.cookie, data=data, verify=False)
        return res.json()

    @staticmethod
    def linkmicWaitingList(auth, room_id: str):
        """排队列表：total_count 即「排队人数」（申请成功的体现）。"""
        api = "/webcast/linkmic_audience/waiting_list/"
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = f"https://live.douyin.com/{room_id}"
        headers.set_header("origin", DouyinAPI.live_url)
        headers.with_csrf(auth.cookie_str)
        headers.set_referer(refer)
        params = Params()
        params.add_param("aid", '6383')
        params.add_param("app_name", 'douyin_web')
        params.add_param("live_id", '1')
        params.add_param("device_platform", 'web')
        params.add_param("language", 'zh-CN')
        params.add_param("enter_from", 'link_share')
        params.add_param("cookie_enabled", 'true')
        params.add_param("screen_width", '1280')
        params.add_param("screen_height", '720')
        params.add_param("browser_language", 'zh-CN')
        params.add_param("browser_platform", 'Win32')
        params.add_param("browser_name", 'Chrome')
        params.add_param("browser_version", '148.0.0.0')
        params.add_param("os_name", 'Windows')
        params.add_param("os_version", '10')
        params.add_param("room_id", str(room_id))
        params.add_param("msToken", auth.msToken)
        params.with_a_bogus()
        res = requests.get(f'{DouyinAPI.live_url}{api}', headers=headers.get(),
                           params=params.get(), cookies=auth.cookie, verify=False)
        return res.json()

    @staticmethod
    def linkmicList(auth, room_id: str):
        """连线者列表：审批通过/连线建立后含主播与自己 uid。"""
        api = "/webcast/linkmic_audience/list/v2/"
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = f"https://live.douyin.com/{room_id}"
        headers.set_header("origin", DouyinAPI.live_url)
        headers.with_csrf(auth.cookie_str)
        headers.set_referer(refer)
        params = Params()
        params.add_param("aid", '6383')
        params.add_param("app_name", 'douyin_web')
        params.add_param("live_id", '1')
        params.add_param("device_platform", 'web')
        params.add_param("language", 'zh-CN')
        params.add_param("enter_from", 'link_share')
        params.add_param("cookie_enabled", 'true')
        params.add_param("screen_width", '1280')
        params.add_param("screen_height", '720')
        params.add_param("browser_language", 'zh-CN')
        params.add_param("browser_platform", 'Win32')
        params.add_param("browser_name", 'Chrome')
        params.add_param("browser_version", '148.0.0.0')
        params.add_param("os_name", 'Windows')
        params.add_param("os_version", '10')
        params.add_param("room_id", str(room_id))
        params.add_param("msToken", auth.msToken)
        params.with_a_bogus()
        res = requests.get(f'{DouyinAPI.live_url}{api}', headers=headers.get(),
                           params=params.get(), cookies=auth.cookie, verify=False)
        return res.json()

    @staticmethod
    def linkmicCheck(auth, room_id: str):
        """状态轮询（页面原生 20s 一次）：action / sleep_second / silence_status。"""
        api = "/webcast/linkmic_audience/check_audience_linkers/"
        headers = HeaderBuilder().build(HeaderType.FORM)
        refer = f"https://live.douyin.com/{room_id}"
        headers.set_header("origin", DouyinAPI.live_url)
        headers.with_csrf(auth.cookie_str)
        headers.set_referer(refer)
        params = Params()
        params.add_param("aid", '6383')
        params.add_param("app_name", 'douyin_web')
        params.add_param("live_id", '1')
        params.add_param("device_platform", 'web')
        params.add_param("language", 'zh-CN')
        params.add_param("enter_from", 'link_share')
        params.add_param("cookie_enabled", 'true')
        params.add_param("screen_width", '1280')
        params.add_param("screen_height", '720')
        params.add_param("browser_language", 'zh-CN')
        params.add_param("browser_platform", 'Win32')
        params.add_param("browser_name", 'Chrome')
        params.add_param("browser_version", '148.0.0.0')
        params.add_param("os_name", 'Windows')
        params.add_param("os_version", '10')
        params.add_param("room_id", str(room_id))
        params.add_param("msToken", auth.msToken)
        data = {"room_id": str(room_id)}
        params.with_a_bogus(data)
        res = requests.post(f'{DouyinAPI.live_url}{api}', headers=headers.get(),
                            params=params.get(), cookies=auth.cookie, data=data, verify=False)
        return res.json()

    @staticmethod
    def linkmicLeave(auth, room_id: str):
        api = "/webcast/linkmic_audience/leave/"
        headers = HeaderBuilder().build(HeaderType.FORM)
        refer = f"https://live.douyin.com/{room_id}"
        headers.set_header("origin", DouyinAPI.live_url)
        headers.with_csrf(auth.cookie_str)
        headers.set_referer(refer)
        params = Params()
        params.add_param("aid", '6383')
        params.add_param("app_name", 'douyin_web')
        params.add_param("live_id", '1')
        params.add_param("device_platform", 'web')
        params.add_param("language", 'zh-CN')
        params.add_param("enter_from", 'link_share')
        params.add_param("cookie_enabled", 'true')
        params.add_param("screen_width", '1280')
        params.add_param("screen_height", '720')
        params.add_param("browser_language", 'zh-CN')
        params.add_param("browser_platform", 'Win32')
        params.add_param("browser_name", 'Chrome')
        params.add_param("browser_version", '148.0.0.0')
        params.add_param("os_name", 'Windows')
        params.add_param("os_version", '10')
        params.add_param("room_id", str(room_id))
        params.add_param("msToken", auth.msToken)
        data = {"room_id": str(room_id)}
        params.with_a_bogus(data)
        res = requests.post(f'{DouyinAPI.live_url}{api}', headers=headers.get(),
                            params=params.get(), cookies=auth.cookie, data=data, verify=False)
        return res.json()

    def diggLiveRoom(auth, room_id: str, count: str = '1'):
        api = "/webcast/room/like/"
        headers = HeaderBuilder().build(HeaderType.FORM)
        refer = f"https://live.douyin.com/{room_id}"
        headers.set_header("origin", DouyinAPI.douyin_url)
        headers.with_csrf(auth.cookie_str)
        headers.set_referer(refer)
        params = Params()
        params.add_param("aid", '6383')
        params.add_param("app_name", 'douyin_web')
        params.add_param("live_id", '1')
        params.add_param("device_platform", 'web')
        params.add_param("language", 'zh-CN')
        params.add_param("enter_from", 'web_live')
        params.add_param("cookie_enabled", 'true')
        params.add_param("screen_width", '2560')
        params.add_param("screen_height", '1440')
        params.add_param("browser_language", 'zh-CN')
        params.add_param("browser_platform", 'Win32')
        params.add_param("browser_name", 'Edge')
        params.add_param("browser_version", '130.0.0.0')
        params.add_param("room_id", room_id)
        params.add_param("count", count)
        params.add_param("msToken", auth.msToken)
        data = {
        }
        params.with_a_bogus(data)
        res = requests.post(f'{DouyinAPI.live_url}{api}', headers=headers.get(), params=params.get(),
                            cookies=auth.cookie, data=data, verify=False)
        return res.json()

    @staticmethod
    def sendMsgInRoom(auth, room_id: str, content: str = ''):
        api = "/webcast/room/chat/"
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = f"https://live.douyin.com/{room_id}"
        headers.set_header("Origin", DouyinAPI.douyin_url)
        headers.with_bd(api, auth)
        headers.with_csrf(auth.cookie_str)
        headers.set_referer(refer)
        params = Params()
        params.add_param("aid", '6383')
        params.add_param("app_name", 'douyin_web')
        params.add_param("live_id", '1')
        params.add_param("device_platform", 'web')
        params.add_param("language", 'zh-CN')
        params.add_param("enter_from", 'web_others_homepage')
        params.add_param("cookie_enabled", 'true')
        params.add_param("screen_width", '2560')
        params.add_param("screen_height", '1440')
        params.add_param("browser_language", 'zh-CN')
        params.add_param("browser_platform", 'Win32')
        params.add_param("browser_name", 'Edge')
        params.add_param("browser_version", '130.0.0.0')
        params.add_param("room_id", room_id)
        params.add_param("content", content)
        params.add_param("type", '0')
        params.add_param("msToken", auth.msToken)
        params.with_a_bogus()
        res = requests.get(f'{DouyinAPI.live_url}{api}', headers=headers.get(), params=params.get(),
                           cookies=auth.cookie, verify=False)
        return res.json()

