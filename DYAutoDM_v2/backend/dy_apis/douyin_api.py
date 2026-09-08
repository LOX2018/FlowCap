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



class DouyinAPI:
    douyin_url = 'https://www.douyin.com'
    live_url = 'https://live.douyin.com'
    creator = "https://creator.douyin.com"
    # 2026-09-06 全局治理：get_my_uid 缓存新鲜度 TTL（秒）。
    # auth.uid 缓存超过该时长后必须重探活，uid 轮换后自动自愈；
    # 300s = 5 分钟，风控安全频次（每账号每 5 分钟至多 1 次 query/user）。
    UID_CACHE_TTL_SEC = 300

    # 2026-09-06 第五轮治理 P1-C（09 台账 5.2B1）：进程级探活缓存。
    # 此前 verify_account / live_hook 心跳 / recv_daemon / accounts 校验
    # 4 处独立调 get_my_uid → 同账号同 IP 的 query/user 规律性外网探测
    # 叠加 = 风控信号。现按 sessionid 键控做进程级缓存：
    #   成功 → UID_PROBE_TTL_OK（默认 300s）内直接复用；
    #   失败 → UID_PROBE_TTL_FAIL（默认 60s）内不再重复打接口。
    # force_probe=True 跳过缓存（refresh_cookie_to_env 门禁等必须真探活的路径用）。
    UID_PROBE_TTL_OK = 300
    UID_PROBE_TTL_FAIL = 60
    _uid_probe_cache: dict = {}   # class attr: {sessionid_key: (ts, uid_or_None)}


    @staticmethod
    def get_user_all_work_info(auth, user_url: str, **kwargs) -> list:
        """
        获取用户全部作品信息.
        :param auth: DouyinAuth object.
        :param user_url: 用户主页URL.
        :return: 全部作品信息.
        """
        max_cursor = "0"
        work_list = []
        while True:
            res_json = DouyinAPI.get_user_work_info(auth, user_url, max_cursor)
            if "aweme_list" not in res_json.keys():
                break
            works = res_json["aweme_list"]
            max_cursor = str(res_json["max_cursor"])
            work_list.extend(works)
            if res_json["has_more"] != 1:
                break
        return work_list


    @staticmethod
    def get_user_work_info(auth, user_url: str, max_cursor, **kwargs) -> dict:
        """
        获取用户作品信息.
        :param auth: DouyinAuth object.
        :param user_url:  用户主页URL.
        :param max_cursor:  上一次请求的max_cursor.
        :return:
        """
        api = f"/aweme/v1/web/aweme/post/"
        user_id = user_url.split("/")[-1].split("?")[0]
        headers = HeaderBuilder().build(HeaderType.GET)
        headers.set_referer(user_url)
        params = Params()
        params.add_param("device_platform", 'webapp')
        params.add_param("aid", '6383')
        params.add_param("channel", 'channel_pc_web')
        params.add_param("sec_user_id", user_id)
        params.add_param("max_cursor", max_cursor)
        params.add_param("locate_query", 'false')
        params.add_param("show_live_replay_strategy", '1')
        params.add_param("need_time_list", '1' if max_cursor == '0' else '0')
        params.add_param("time_list_query", '0')
        params.add_param("whale_cut_token", '')
        params.add_param("cut_version", '1')
        params.add_param("count", '18')
        params.add_param("publish_video_strategy_type", '2')
        params.add_param("update_version_code", '170400')
        params.add_param("pc_client_type", '1')
        params.add_param("version_code", '290100')
        params.add_param("version_name", '29.1.0')
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
        params.with_web_id(auth, user_url)
        params.add_param("verifyFp", auth.cookie['s_v_web_id'])
        params.add_param("fp", auth.cookie['s_v_web_id'])
        params.add_param("msToken",
                         auth.msToken)
        params.with_a_bogus()
        resp = requests.get(f'{DouyinAPI.douyin_url}{api}', headers=headers.get(), cookies=auth.cookie,
                            params=params.get(), verify=False)
        return json.loads(resp.text)

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
        params.add_param("browser_platform", get_profile()["platform"])
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
    def get_work_out_comment(auth, url: str, cursor: str = '0', **kwargs) -> dict:
        """
        获取作品的全部一级评论.
        :param auth: DouyinAuth object.
        :param url: 作品URL.
        :param cursor: 评论游标.
        :return: JSON.
        """
        api = f"/aweme/v1/web/comment/list/"
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
        params.add_param("cursor", cursor)
        params.add_param("count", "5")
        params.add_param("item_type", "0")
        params.add_param("whale_cut_token", "")
        params.add_param("cut_version", "1")
        params.add_param("rcFT", "")
        params.add_param("update_version_code", "170400")
        params.add_param("pc_client_type", "1")
        params.add_param("version_code", "170400")
        params.add_param("version_name", "17.4.0")
        params.add_param("cookie_enabled", "true")
        params.add_param("screen_width", get_profile()["screen_width"])
        params.add_param("screen_height", get_profile()["screen_height"])
        params.add_param("browser_language", "zh-CN")
        params.add_param("browser_platform", get_profile()["platform"])
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
        params.add_param("downlink", "10")
        params.add_param("effective_type", "4g")
        params.add_param("round_trip_time", "0")
        params.with_web_id(auth, url)
        params.add_param("verifyFp", auth.cookie['s_v_web_id'])
        params.add_param("fp", auth.cookie['s_v_web_id'])
        params.add_param("msToken", auth.msToken)
        params.with_a_bogus()
        resp = requests.get(f'{DouyinAPI.douyin_url}{api}', headers=headers.get(), cookies=auth.cookie,
                            params=params.get(), verify=False)
        resp_json = json.loads(resp.text)
        return resp_json

    @staticmethod
    def get_work_all_out_comment(auth, url: str, **kwargs) -> list:
        """
        获取作品全部一级评论.
        :param auth: DouyinAuth object.
        :param url: 作品URL.
        :return:
        """
        cursor = "0"
        comment_list = []
        while True:
            res_json = DouyinAPI.get_work_out_comment(auth, url, cursor)
            comments = res_json["comments"]
            cursor = str(res_json["cursor"])
            if comments is None or len(comments) == 0:
                break
            comment_list.extend(comments)
            if res_json["has_more"] != 1:
                break
        return comment_list

    @staticmethod
    def get_work_inner_comment(auth, comment: dict, cursor: str, count: str = '3', **kwargs):
        """
        获取作品评论的二级评论.
        :param count: 要获取的二级评论数量.
        :param auth: DouyinAuth object.
        :param comment: 一级评论信息.
        :param cursor: 评论游标.
        :return:
        """
        api = f"/aweme/v1/web/comment/list/reply/"
        aweme_id = comment['aweme_id']
        comment_id = comment['cid']
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = f'https://www.douyin.com/video/{aweme_id}'
        headers.set_referer(refer)
        params = Params()
        params.add_param("device_platform", "webapp")
        params.add_param("aid", "6383")
        params.add_param("channel", "channel_pc_web")
        params.add_param("item_id", aweme_id)
        params.add_param("comment_id", comment_id)
        params.add_param("cut_version", "1")
        params.add_param("cursor", cursor)
        params.add_param("count", count)
        params.add_param("item_type", "0")
        params.add_param("update_version_code", "170400")
        params.add_param("pc_client_type", "1")
        params.add_param("version_code", "170400")
        params.add_param("version_name", "17.4.0")
        params.add_param("cookie_enabled", "true")
        params.add_param("screen_width", get_profile()["screen_width"])
        params.add_param("screen_height", get_profile()["screen_height"])
        params.add_param("browser_language", "zh-CN")
        params.add_param("browser_platform", get_profile()["platform"])
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
        params.add_param("downlink", "10")
        params.add_param("effective_type", "4g")
        params.add_param("round_trip_time", "0")
        params.with_web_id(auth, refer)
        params.add_param("verifyFp", auth.cookie['s_v_web_id'])
        params.add_param("fp", auth.cookie['s_v_web_id'])
        params.add_param("msToken", auth.msToken)
        params.with_a_bogus()
        resp = requests.get(f'{DouyinAPI.douyin_url}{api}', headers=headers.get(), cookies=auth.cookie,
                            params=params.get(), verify=False)
        resp_json = json.loads(resp.text)
        return resp_json

    @staticmethod
    def get_work_all_inner_comment(auth, comment: dict, **kwargs) -> list:
        """
        获取作品评论的全部二级评论.
        :param auth: DouyinAuth object.
        :param comment: 一级评论信息.
        :return: 二级评论列表.
        """
        cursor = "0"
        count = '5'
        comment_list = []
        while True:
            res_json = DouyinAPI.get_work_inner_comment(auth, comment, cursor, count)
            comments = res_json["comments"]
            cursor = str(res_json["cursor"])
            if type(comments) is list and len(comments) > 0:
                comment_list.extend(comments)
            if res_json["has_more"] != 1:
                break
        return comment_list

    @staticmethod
    def get_work_all_comment(auth, url: str, **kwargs):
        """
        获取作品全部评论.
        :param auth: DouyinAuth object.
        :param url: 作品URL.
        :return: 全部评论列表.
        """
        out_comment_list = DouyinAPI.get_work_all_out_comment(auth, url)
        for comment in out_comment_list:
            comment['reply_comment'] = []
            if comment['reply_comment_total'] > 0:
                inner_comment_list = DouyinAPI.get_work_all_inner_comment(auth, comment)
                comment['reply_comment'] = inner_comment_list
        return out_comment_list

    @staticmethod
    def get_user_info(auth, user_url: str, **kwargs) -> dict:
        """
        获取用户信息.
        :param auth: DouyinAuth object.
        :param user_url: 用户主页URL.
        :return: 用户信息.
        """
        api = f"/aweme/v1/web/user/profile/other/"
        user_id = user_url.split("/")[-1].split("?")[0]
        headers = HeaderBuilder().build(HeaderType.GET)
        headers.set_referer(user_url)
        params = Params()
        params.add_param("device_platform", 'webapp')
        params.add_param("aid", '6383')
        params.add_param("channel", 'channel_pc_web')
        params.add_param("publish_video_strategy_type", '2')
        params.add_param("source", 'channel_pc_web')
        params.add_param("sec_user_id", user_id)
        params.add_param("personal_center_strategy", '1')
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
        params.add_param("round_trip_time", '100')
        params.with_web_id(auth, user_url)
        params.add_param("msToken", auth.msToken)
        params.add_param('verifyFp', auth.cookie['s_v_web_id'])
        params.add_param('fp', auth.cookie['s_v_web_id'])
        params.with_a_bogus()
        resp = requests.get(f'{DouyinAPI.douyin_url}{api}', headers=headers.get(), cookies=auth.cookie,
                            params=params.get(), verify=False, timeout=kwargs.get("timeout", 10))
        return json.loads(resp.text)

    @staticmethod
    def search_general_work(auth, query: str, sort_type: str = '0', publish_time: str = '0', offset: str = '0',
                            filter_duration="", search_range="", content_type="", **kwargs):
        """
        搜索综合频道作品.
        :param auth: DouyinAuth object.
        :param query: 搜索关键字.
        :param sort_type: 排序方式 0 综合排序, 1 最多点赞, 2 最新发布.
        :param publish_time: 发布时间 0 不限, 1 一天内, 7 一周内, 180 半年内.
        :param offset: 搜索结果偏移量.
        :param filter_duration: 视频时长 空字符串 不限, 0-1 一分钟内, 1-5 1-5分钟内, 5-10000 5分钟以上
        :param search_range: 搜索范围 0 不限, 1 最近看过, 2 还未看过, 3 关注的人
        :param content_type: 内容形式 0 不限, 1 视频, 2 图文
        :return: JSON数据.
        """
        api = f"/aweme/v1/web/general/search/single/"
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = f'https://www.douyin.com/search/{urllib.parse.quote(query)}?aid={uuid.uuid4()}&type=general'
        headers.set_referer(refer)
        params = Params()
        params.add_param("device_platform", "webapp")
        params.add_param("aid", "6383")
        params.add_param("channel", "channel_pc_web")
        params.add_param("search_channel", "aweme_general")
        params.add_param("enable_history", "1")
        params.add_param("filter_selected", r'{"sort_type":"%s","publish_time":"%s","filter_duration":"%s",'
                                            r'"search_range":"%s","content_type":"%s"}' % (sort_type, publish_time,
                                                                                           filter_duration,
                                                                                           search_range, content_type))
        params.add_param("keyword", query)
        params.add_param("search_source", "tab_search")
        params.add_param("query_correct_type", "1")
        params.add_param("is_filter_search", "1")
        params.add_param("from_group_id", "")
        params.add_param("offset", offset)
        params.add_param("count", '25')
        params.add_param("need_filter_settings", '1' if offset == '0' else '0')
        params.add_param("list_type", "single")
        params.add_param("update_version_code", "170400")
        params.add_param("pc_client_type", "1")
        params.add_param("version_code", "190600")
        params.add_param("version_name", "19.6.0")
        params.add_param("cookie_enabled", "true")
        params.add_param("screen_width", get_profile()["screen_width"])
        params.add_param("screen_height", get_profile()["screen_height"])
        params.add_param("browser_language", "zh-CN")
        params.add_param("browser_platform", get_profile()["platform"])
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
        params.add_param("downlink", "10")
        params.add_param("effective_type", "4g")
        params.add_param("round_trip_time", "50")
        params.with_web_id(auth, refer)
        params.add_param("msToken", auth.msToken)
        # 综合搜索风控(antispam_check)只认新算法签名：纯算 a_bogus（Python 原生执行 bdms VMP）
        params.add_param('a_bogus', generate_a_bogus_pure(api, splice_url(params.get())))
        resp = requests.get(f'{DouyinAPI.douyin_url}{api}', headers=headers.get(), cookies=auth.cookie,
                            params=params.get(), verify=False)
        return json.loads(resp.text)

    @staticmethod
    def search_some_general_work(auth, query: str, num: int, sort_type: str, publish_time: str, filter_duration="", search_range="", content_type="", **kwargs) -> list:
        """
        搜索指定数量综合频道作品.
        :param auth: DouyinAuth object.
        :param query: 搜索关键字.
        :param num: 搜索结果数量.
        :param sort_type: 排序方式 0 综合排序, 1 最多点赞, 2 最新发布.
        :param publish_time: 发布时间 0 不限, 1 一天内, 7 一周内, 180 半年内.
        :param filter_duration: 视频时长 空字符串 不限, 0-1 一分钟内, 1-5 1-5分钟内, 5-10000 5分钟以上
        :param search_range: 搜索范围 0 不限, 1 最近看过, 2 还未看过, 3 关注的人
        :param content_type: 内容形式 0 不限, 1 视频, 2 图文
        :return: 作品列表.
        """
        offset = "0"
        work_list = []
        while True:
            res_json = DouyinAPI.search_general_work(auth, query, sort_type, publish_time, offset,
                                                     filter_duration, search_range, content_type)
            works = [w for w in res_json["data"] if w.get("aweme_info")]
            work_list.extend(works)
            if res_json["has_more"] != 1 or len(work_list) >= num:
                break
            offset = str(int(offset) + len(res_json["data"]))
        if len(work_list) > num:
            work_list = work_list[:num]
        return work_list

    @staticmethod
    def search_some_user(auth, query: str, num: int, **kwargs) -> list:
        """
        搜索指定数量用户.
        :param auth: DouyinAuth object.
        :param query: 搜索关键字.
        :param num: 搜索结果数量.
        :return: 用户列表.
        """
        offset = "0"
        count = "25"
        user_list = []
        while True:
            res_json = DouyinAPI.search_user(auth, query, offset, count)
            users = res_json["user_list"]
            user_list.extend(users)
            if res_json["has_more"] != 1 or len(user_list) >= num:
                break
            offset = str(int(offset) + int(count))
        if len(user_list) > num:
            user_list = user_list[:num]
        return user_list


    @staticmethod
    def search_user(auth, query: str, offset: str = '0', num: str = '25', douyin_user_fans="", douyin_user_type="", **kwargs):
        """
        搜索用户.
        :param auth: DouyinAuth object.
        :param query:  搜索关键字.
        :param offset:  搜索结果偏移量.
        :param num:  搜索结果数量.
        :param douyin_user_fans: 粉丝数量 空字符串 (0_1k 1000以下) (1k_1w 1000-10000) (1w_10w 10000-100000) (10w_100w 10w-100w粉丝) (100w_ 100w以上)
        :param douyin_user_type: 用户类型 空字符串 不限 common_user 普通用户 enterprise_user 企业用户 personal_user 个人认证用户
        :return: JSON数据.
        """
        api = "/aweme/v1/web/discover/search"
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = f'https://www.douyin.com/search/{urllib.parse.quote(query)}?aid={uuid.uuid4()}&type=general'
        headers.set_referer(refer)
        params = Params()
        params.add_param("device_platform", 'webapp')
        params.add_param("aid", '6383')
        params.add_param("channel", 'channel_pc_web')
        params.add_param("search_channel", 'aweme_user_web')
        params.add_param("search_filter_value", r'{"douyin_user_fans":["%s"],"douyin_user_type":["%s"]}' % (
            douyin_user_fans, douyin_user_type))
        params.add_param("keyword", query)
        params.add_param("search_source", 'switch_tab')
        params.add_param("query_correct_type", '1')
        params.add_param("is_filter_search", '1')
        # params.add_param("from_group_id", '7378456704385600820')
        params.add_param("offset", offset)
        params.add_param("count", num)
        params.add_param("need_filter_settings", '1' if offset == '0' else '0')
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
        params.add_param("round_trip_time", '150')
        params.with_web_id(auth, refer)
        params.add_param("msToken", auth.msToken)
        params.with_a_bogus()
        resp = requests.get(f'{DouyinAPI.douyin_url}{api}', headers=headers.get(), cookies=auth.cookie,
                            params=params.get(), verify=False)
        return resp.json()

    @staticmethod
    def search_live(auth, query: str, offset: str = '0', num: str = '25', **kwargs):
        """
        搜索直播.
        :param auth: DouyinAuth object.
        :param query:  搜索关键字.
        :param offset:  搜索结果偏移量.
        :param num:  搜索数量.
        :return: JSON数据.
        """
        api = "/aweme/v1/web/live/search/"
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = f'https://www.douyin.com/search/{urllib.parse.quote(query)}?aid={uuid.uuid4()}&type=live'
        headers.set_referer(refer)
        params = Params()
        params.add_param("device_platform", 'webapp')
        params.add_param("aid", '6383')
        params.add_param("channel", 'channel_pc_web')
        params.add_param("search_channel", 'aweme_live')
        params.add_param("keyword", query)
        params.add_param("search_source", 'normal_search')
        params.add_param("query_correct_type", '1')
        params.add_param("is_filter_search", '0')
        params.add_param("from_group_id", '')
        params.add_param("offset", offset)
        params.add_param("count", num)
        params.add_param("need_filter_settings", '1' if offset == '0' else '0')
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
        resp = requests.get(f'{DouyinAPI.douyin_url}{api}', headers=headers.get(), cookies=auth.cookie,
                            params=params.get(), verify=False)
        return resp.json()

    @staticmethod
    def search_some_live(auth, query: str, num: int, **kwargs) -> list:
        """
        搜索指定数量直播.
        :param auth: DouyinAuth object.
        :param query:  搜索关键字.
        :param num:  搜索数量.
        :return: 直播列表.
        """
        offset = "0"
        count = "25"
        live_list = []
        while True:
            res_json = DouyinAPI.search_live(auth, query, offset, count)
            lives = res_json["data"]
            live_list.extend(lives)
            if res_json["has_more"] != 1 or len(live_list) >= num:
                break
            offset = str(int(offset) + int(count))
        if len(live_list) > num:
            live_list = live_list[:num]
        return live_list

    @staticmethod
    def get_user_favorite(auth, sec_id: str, max_cursor: str = '0', num: str = '18', **kwargs):
        """
        获取用户收藏.
        :param auth: DouyinAuth object.
        :param sec_id:  用户SECID.
        :param max_cursor:  翻页游标.
        :param num: 要获取的收藏数量.
        :return: JSON.
        """
        headers = HeaderBuilder.build(HeaderType.GET)
        refer = f"https://www.douyin.com/user/{sec_id}?showTab=like"
        headers.set_referer(refer)
        params = Params()
        params.add_param("device_platform", 'webapp')
        params.add_param("aid", '6383')
        params.add_param("channel", 'channel_pc_web')
        params.add_param("sec_user_id", 'MS4wLjABAAAA99bTJ_GOw3odYmsXOe7i7xuEv0iQf2X_Kg_VUyVP0U8')
        params.add_param("max_cursor", max_cursor)
        params.add_param("min_cursor", '0')
        params.add_param("whale_cut_token", '')
        params.add_param("cut_version", '1')
        params.add_param("count", num)
        params.add_param("publish_video_strategy_type", '2')
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
        params.add_param("round_trip_time", '100')
        params.with_web_id(auth=auth, url=refer)
        params.add_param("verifyFp", auth.cookie['s_v_web_id'])
        params.add_param("fp", auth.cookie['s_v_web_id'])
        params.add_param("msToken",
                         auth.msToken)
        params.with_a_bogus()
        response = requests.get('https://www.douyin.com/aweme/v1/web/aweme/favorite/', params=params.get(),
                                headers=headers.get(), cookies=auth.cookie,
                                verify=False)
        return response.json()


    @staticmethod
    def get_my_uid(auth, **kwargs) -> int:
        """
        获取自己的用户ID.
        :param auth: DouyinAuth object.
        :return: 用户ID；无有效 cookie 情况下返回 None（探活失败）。

        【修复 2026-08-17 16:36 基座直测结论】
        抖音新版 cookie 格式已变化，旧代码的「占位/伪造」判断全部误伤正常登录态：
          - s_v_web_id 正常值就是 'verify_xxx' 开头（非旧版假设的 19 位纯数字）；
          - uid_tt 是 32 位 hex 字符串，转十进制超 19 位（非旧版假设的 int64 十进制）。
        旧代码因「s_v_web_id=verify_ 开头 → 判占位」而【提前 return None，拦截网络
        请求】，导致探活失败。基座实测证明：即便 s_v_web_id=verify_ 开头、uid_tt 是
        hex，网络接口 query/user 仍返回真实十进制 uid（如 3887506227210423）。
        故本函数【不再基于 cookie 格式提前拦截】，uid_tt 解析失败一律走网络
        query/user 拿真实 uid。

        【P1-C 2026-09-06】进程级探活缓存：按 sessionid 键控（同账号所有 auth
        副本共享同一份缓存），成功 300s / 失败 60s 内不重复发 query/user。
        kwargs 传 force_probe=True 时跳过进程缓存（凭证落盘门禁等必须真探活的路径）。
        """
        if not auth or not getattr(auth, "cookie", None):
            return None
        # ---- P1-C 进程级缓存查询（在 auth.uid 缓存之前：跨副本共享）----
        _force = bool(kwargs.get("force_probe"))
        _sess = (auth.cookie.get("sessionid") or auth.cookie.get("sessionid_ss") or "")
        _cache_key = _sess[:32] if _sess else ""
        if _cache_key and not _force:
            try:
                _ts, _cached_uid = DouyinAPI._uid_probe_cache.get(_cache_key, (0.0, None))
                _ttl = (DouyinAPI.UID_PROBE_TTL_OK if _cached_uid
                        else DouyinAPI.UID_PROBE_TTL_FAIL)
                if _cached_uid and (time.time() - _ts) < _ttl:
                    # 回写本副本 auth.uid（方向判定等消费方依赖）
                    try:
                        auth.uid = int(_cached_uid)
                        auth._uid_cached_at = _ts
                    except Exception:
                        pass
                    return int(_cached_uid)
            except Exception:
                pass
        # 优先返回已由 ensure_uid 设置的 auth.uid（避免重复解析不一致）
        # 2026-09-06 全局治理（uid 轮换自愈 + 风控平衡）：
        # 原实现一旦缓存 auth.uid 就永远返回缓存，**uid 轮换后无任何路径更新**。
        # 加上"缓存新鲜度"门：缓存超过 UID_CACHE_TTL_SEC（默认 300s = 5 分钟）
        # 视为陈旧，必须重探活。
        # 风控面：每 5 分钟 1 次 query/user，符合抖音探活频次安全范围；
        # uid 轮换后下个 5 分钟窗口自动恢复，无需重启进程。
        existing_uid = getattr(auth, "uid", None)
        existing_ts = getattr(auth, "_uid_cached_at", None)
        if existing_uid and existing_ts:
            try:
                _now = time.time()
                if (_now - float(existing_ts)) < DouyinAPI.UID_CACHE_TTL_SEC:
                    return existing_uid
            except Exception:
                pass
        cookie = auth.cookie
        # 真实 uid 优先：uid_tt 是登录态直接下发的数字 uid。
        # 仅当它是纯十进制数字才直接返回（旧版格式）；hex 或其它格式一律走网络接口。
        uid_tt = cookie.get("uid_tt") or cookie.get("uid_tt_ss")
        if uid_tt:
            sval = str(uid_tt).strip()
            if sval.isdigit():
                try:
                    _uid = int(sval)
                    # 写回缓存并打时间戳（即使直接 return，下次 uid 轮换也能感知）
                    try:
                        auth.uid = _uid
                        auth._uid_cached_at = time.time()
                    except Exception:
                        pass
                    # P1-C：uid_tt 短路成功同样写进程级缓存
                    if _cache_key:
                        try:
                            DouyinAPI._uid_probe_cache[_cache_key] = (time.time(), _uid)
                        except Exception:
                            pass
                    return _uid
                except (ValueError, TypeError):
                    pass
        # 走网络接口 query/user 拿真实十进制 uid（抖音新版唯一可靠来源）。
        # 不因 s_v_web_id 格式提前 return——verify_ 开头是抖音新版正常格式，
        # 且基座实测该接口对 verify_ 开头的 s_v_web_id 依然返回真实 uid。
        s_v_web_id = cookie.get("s_v_web_id")
        url = 'https://www.douyin.com/aweme/v1/web/query/user/'
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = 'https://www.douyin.com/'
        headers.set_header('referer', refer)
        params = Params()
        params.with_platform()
        params.with_web_id(auth, refer)
        params.with_ms_token()
        params.add_param('verifyFp', s_v_web_id)
        params.add_param('fp', s_v_web_id)
        params.with_a_bogus()
        resp = requests.get(url, params=params.get(), verify=False, headers=headers.get(), cookies=auth.cookie,
                            timeout=kwargs.get("timeout", 10))
        resp_json = json.loads(resp.text)
        # 写回 auth.uid + 时间戳（探活成功是最新鲜的 uid，确保轮换自愈链闭合）
        try:
            _fresh_uid = int(resp_json['user_uid'])
            auth.uid = _fresh_uid
            auth._uid_cached_at = time.time()
        except Exception:
            _fresh_uid = int(resp_json['user_uid'])
        # P1-C：写进程级缓存（成功）
        if _cache_key:
            try:
                DouyinAPI._uid_probe_cache[_cache_key] = (time.time(), _fresh_uid)
            except Exception:
                pass
        return _fresh_uid

    @staticmethod
    def get_my_sec_uid(auth, **kwargs) -> str:
        """
        获取自己的SECID.
        :param auth: DouyinAuth object.
        :return: SECID.
        """
        headers = HeaderBuilder().build(HeaderType.GET)
        url = "https://www.douyin.com/user/self"
        params = {
            "from_tab_name": "main"
        }
        response = requests.get(url, headers=headers.get(), cookies=auth.cookie, params=params)
        sec_uid = re.findall(r'\\"secUid\\":\\"(.*?)\\"', response.text)[0]
        return sec_uid


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
        params.add_param("browser_platform", get_profile()["platform"])
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
        params.add_param("browser_platform", get_profile()["platform"])
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
    def collect_aweme(auth, aweme_id: str, action: str = '1', **kwargs):
        """
        收藏或取消收藏视频.
        :param auth: DouyinAuth object.
        :param aweme_id: 视频ID.
        :param action: 1: 收藏, 0: 取消收藏.
        :return: 响应JSON.
        """
        api = '/aweme/v1/web/aweme/collect/'
        headers = HeaderBuilder().build(HeaderType.FORM)
        refer = "https://www.douyin.com/?recommend=1"
        headers.set_referer(refer)
        headers.with_bd(api, auth)
        headers.with_csrf(auth.cookie_str)
        headers.set_header("origin", DouyinAPI.douyin_url)
        params = Params()
        params.add_param("device_platform", "webapp")
        params.add_param("aid", "6383")
        params.add_param("channel", "channel_pc_web")
        params.add_param("pc_client_type", "1")
        params.add_param("update_version_code", "170400")
        params.add_param("version_code", "170400")
        params.add_param("version_name", "17.4.0")
        params.add_param("cookie_enabled", "true")
        params.add_param("screen_width", get_profile()["screen_width"])
        params.add_param("screen_height", get_profile()["screen_height"])
        params.add_param("browser_language", "zh-CN")
        params.add_param("browser_platform", get_profile()["platform"])
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
        params.add_param("downlink", "10")
        params.add_param("effective_type", "4g")
        params.add_param("round_trip_time", "50")
        params.with_web_id(auth, refer)
        params.add_param("verifyFp", auth.cookie['s_v_web_id'])
        params.add_param("fp", auth.cookie['s_v_web_id'])
        params.add_param("msToken", auth.msToken)
        data = {
            "action": action,
            "aweme_id": aweme_id,
            "aweme_type": "0",
        }
        params.with_a_bogus(data)
        res = requests.post(f'{DouyinAPI.douyin_url}{api}', headers=headers.get(), params=params.get(),
                            cookies=auth.cookie, data=data, verify=False)
        return res.json()

    @staticmethod
    def move_collect_aweme(auth, aweme_id: str, collect_name: str, collect_id: str, **kwargs):
        """
        移动视频到指定收藏夹（需要先收藏视频）
        :param collect_name: 收藏夹名称
        :param collect_id: 收藏夹ID
        :param auth: DouyinAuth object.
        :param aweme_id: 视频ID.
        :return: 响应JSON.
        """
        api = '/aweme/v1/web/collects/video/move/'
        headers = HeaderBuilder().build(HeaderType.FORM)
        refer = "https://www.douyin.com/?recommend=1"
        headers.set_referer(refer)
        headers.with_bd(api, auth)
        headers.with_csrf(auth.cookie_str)
        headers.set_header("origin", DouyinAPI.douyin_url)
        params = Params()
        params.add_param("aid", "6383")
        params.add_param("browser_language", "zh-CN")
        params.add_param("browser_name", get_profile()["browser_name"])
        params.add_param("browser_online", "true")
        params.add_param("browser_platform", get_profile()["platform"])
        params.add_param("browser_version", get_profile()["browser_version"])
        params.add_param("channel", "channel_pc_web")
        params.add_param("collects_name", collect_name)
        params.add_param("cookie_enabled", "true")
        params.add_param("cpu_core_num", get_profile()["cpu_core_num"])
        params.add_param("device_memory", get_profile()["device_memory"])
        params.add_param("device_platform", "webapp")
        params.add_param("downlink", "10")
        params.add_param("effective_type", "4g")
        params.add_param("engine_name", "Blink")
        params.add_param("engine_version", get_profile()["engine_version"])
        params.add_param("item_ids", aweme_id)
        params.add_param("item_type", "2")
        params.add_param("move_collects_list", collect_id)
        params.add_param("os_name", "Windows")
        params.add_param("os_version", get_profile()["os_version"])
        params.add_param("pc_client_type", "1")
        params.add_param("platform", "PC")
        params.add_param("round_trip_time", "50")
        params.add_param("screen_height", get_profile()["screen_height"])
        params.add_param("screen_width", get_profile()["screen_width"])
        params.add_param("to_collects_id", collect_id)
        params.add_param("update_collects_sort", "true")
        params.add_param("update_version_code", "170400")
        params.add_param("version_code", "170400")
        params.add_param("version_name", "17.4.0")
        params.with_web_id(auth, refer)
        params.add_param("verifyFp", auth.cookie['s_v_web_id'])
        params.add_param("fp", auth.cookie['s_v_web_id'])
        params.add_param("msToken", auth.msToken)
        params.with_a_bogus()
        res = requests.post(f'{DouyinAPI.douyin_url}{api}', headers=headers.get(), params=params.get(),
                            cookies=auth.cookie, verify=False)
        return res.json()

    @staticmethod
    def remove_collect_aweme(auth, aweme_id: str, collect_name: str, collect_id: str, **kwargs):
        """
        从指定收藏夹中移除视频（需要先收藏视频）
        :param collect_name: 收藏夹名称
        :param collect_id: 收藏夹ID
        :param auth: DouyinAuth object.
        :param aweme_id: 视频ID.
        :return: 响应JSON.
        """
        api = '/aweme/v1/web/collects/video/move/'
        headers = HeaderBuilder().build(HeaderType.FORM)
        refer = "https://www.douyin.com/user/self?showTab=favorite_collection"
        headers.set_referer(refer)
        headers.with_bd(api, auth)
        headers.with_csrf(auth.cookie_str)
        headers.set_header("origin", DouyinAPI.douyin_url)
        params = Params()
        params.add_param("aid", "6383")
        params.add_param("browser_language", "zh-CN")
        params.add_param("browser_name", get_profile()["browser_name"])
        params.add_param("browser_online", "true")
        params.add_param("browser_platform", get_profile()["platform"])
        params.add_param("browser_version", get_profile()["browser_version"])
        params.add_param("channel", "channel_pc_web")
        params.add_param("collects_name", collect_name)
        params.add_param("cookie_enabled", "true")
        params.add_param("cpu_core_num", get_profile()["cpu_core_num"])
        params.add_param("device_memory", get_profile()["device_memory"])
        params.add_param("device_platform", "webapp")
        params.add_param("downlink", "10")
        params.add_param("effective_type", "4g")
        params.add_param("engine_name", "Blink")
        params.add_param("engine_version", get_profile()["engine_version"])
        params.add_param("from_collects_id", collect_id)
        params.add_param("item_ids", aweme_id)
        params.add_param("item_type", "2")
        params.add_param("os_name", "Windows")
        params.add_param("os_version", get_profile()["os_version"])
        params.add_param("pc_client_type", "1")
        params.add_param("platform", "PC")
        params.add_param("round_trip_time", "50")
        params.add_param("screen_height", get_profile()["screen_height"])
        params.add_param("screen_width", get_profile()["screen_width"])
        params.add_param("update_version_code", "170400")
        params.add_param("version_code", "170400")
        params.add_param("version_name", "17.4.0")
        params.with_web_id(auth, refer)
        params.add_param("verifyFp", auth.cookie['s_v_web_id'])
        params.add_param("fp", auth.cookie['s_v_web_id'])
        params.add_param("msToken", auth.msToken)
        params.with_a_bogus()
        res = requests.post(f'{DouyinAPI.douyin_url}{api}', headers=headers.get(), params=params.get(),
                            cookies=auth.cookie, verify=False)
        return res.json()

    @staticmethod
    def get_collect_list(auth, **kwargs):
        """
        获取我的收藏夹列表
        :param auth: DouyinAuth object.
        :return: JSON.
        """
        api = "/aweme/v1/web/collects/list/"
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = "https://www.douyin.com/?recommend=1"
        headers.set_referer(refer)
        params = Params()
        params.add_param("device_platform", "webapp")
        params.add_param("aid", "6383")
        params.add_param("channel", "channel_pc_web")
        params.add_param("cursor", "0")
        params.add_param("count", "20")
        params.add_param("update_version_code", "170400")
        params.add_param("pc_client_type", "1")
        params.add_param("version_code", "170400")
        params.add_param("version_name", "17.4.0")
        params.add_param("cookie_enabled", "true")
        params.add_param("screen_width", get_profile()["screen_width"])
        params.add_param("screen_height", get_profile()["screen_height"])
        params.add_param("browser_language", "zh-CN")
        params.add_param("browser_platform", get_profile()["platform"])
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
        params.add_param("downlink", "5.95")
        params.add_param("effective_type", "4g")
        params.add_param("round_trip_time", "200")
        params.with_web_id(auth, refer)
        params.add_param("msToken", auth.msToken)
        params.with_a_bogus()
        params.add_param("verifyFp", auth.cookie['s_v_web_id'])
        params.add_param("fp", auth.cookie['s_v_web_id'])
        res = requests.get(f'{DouyinAPI.douyin_url}{api}', headers=headers.get(), params=params.get(),
                           cookies=auth.cookie, verify=False)
        return res.json()

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
    def get_notice_list(auth, min_time='0', max_time='0', count='10', notice_group='700', **kwargs):
        """
        获得通知
        :param auth: DouyinAuth object.
        :param min_time: 最小时间戳.
        :param max_time: 最大时间戳.
        :param count: 数量.
        :param notice_group: 消息类型 700 全部消息 401 粉丝 601 @我的 2 评论 3 点赞 520 弹幕
        :return: JSON.
        """
        api = "/aweme/v1/web/notice/"
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = "https://www.douyin.com/?recommend=1"
        headers.set_referer(refer)
        params = Params()
        params.add_param("device_platform", 'webapp')
        params.add_param("aid", '6383')
        params.add_param("channel", 'channel_pc_web')
        params.add_param("is_new_notice", '1')
        params.add_param("is_mark_read", '1')
        params.add_param("notice_group", notice_group)
        params.add_param("count", count)
        params.add_param("min_time", min_time)
        params.add_param("max_time", max_time)
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
        res = requests.get(f'{DouyinAPI.douyin_url}{api}', headers=headers.get(), params=params.get(),
                           cookies=auth.cookie, verify=False)
        return res.json()

    @staticmethod
    def get_some_notice_list(auth, num: int = 20, notice_group='700', **kwargs) -> list:
        """
        获得前num条通知
        :param auth: DouyinAuth object.
        :param num: 数量.
        :param notice_group: 消息类型 | 700 全部消息 401 粉丝 601 @我的 2 评论 3 点赞 520 弹幕
        :return:
        """
        min_time = "0"
        max_time = "0"
        count = "10"
        notice_list = []
        while True:
            res_json = DouyinAPI.get_notice_list(auth, min_time, max_time, count, notice_group)
            notices = res_json["notice_list_v2"]
            notice_list.extend(notices)
            if res_json["has_more"] != 1 or len(notice_list) >= num:
                break
            min_time = res_json["min_time"]
            max_time = res_json["max_time"]
        if len(notice_list) > num:
            notice_list = notice_list[:num]
        return notice_list

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
        params.add_param("browser_platform", get_profile()["platform"])
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
        params.add_param("browser_platform", get_profile()["platform"])
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

    @staticmethod
    def publish_comment(auth, aweme_id: str, content: str = '', reply_id="", **kwargs):
        """
        发布评论
        :param auth: DouyinAuth object.
        :param aweme_id: 视频ID.
        :param content: 评论内容.
        :param reply_id: 回复评论ID.
        :return: JSON.
        """
        api = "/aweme/v1/web/comment/publish"
        headers = HeaderBuilder().build(HeaderType.FORM)
        refer = f"https://www.douyin.com/discover?modal_id={aweme_id}"
        headers.set_header("Origin", DouyinAPI.douyin_url)
        headers.with_bd(api, auth)
        headers.with_csrf(auth.cookie_str)
        headers.set_referer(refer)
        params = Params()
        params.add_param("app_name", 'aweme')
        params.add_param("enter_from", 'discover')
        params.add_param("previous_page", 'discover')
        params.add_param("device_platform", 'webapp')
        params.add_param("aid", '6383')
        params.add_param("channel", 'channel_pc_web')
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
        data = {
            "aweme_id": aweme_id,
            "comment_send_celltime": random.randint(1000, 20000),
            "comment_video_celltime": random.randint(1000, 20000),
        }
        if reply_id != "":
            data["reply_id"] = reply_id
        data["text"] = content
        data["text_extra"] = []
        params.with_a_bogus(data)
        params.add_param("verifyFp", auth.cookie['s_v_web_id'])
        params.add_param("fp", auth.cookie['s_v_web_id'])
        res = requests.post(f'{DouyinAPI.douyin_url}{api}', headers=headers.get(), params=params.get(),
                            cookies=auth.cookie, data=data, verify=False)
        return res.json()

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
    def get_device_id(auth, **kwargs) -> str:
        """
        获取设备ID.
        :param auth: DouyinAuth object.
        :return: 设备ID.
        """
        url = "https://www.douyin.com/aweme/v1/web/query/user"
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = "https://www.douyin.com/discover"
        headers.set_header("referer", refer)
        params = Params()
        (params
         .with_platform()
         .add_param("publish_video_strategy_type", "2")
         .with_web_id(auth, refer)
         .with_ms_token()
         .add_param('verifyFp', auth.cookie['s_v_web_id'])
         .add_param('fp', auth.cookie['s_v_web_id'])
         .with_a_bogus()
         )
        resp = requests.get(url, params=params.get(), verify=False, headers=headers.get(), cookies=auth.cookie)
        resp_json = json.loads(resp.text)
        return resp_json['id']

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


if __name__ == '__main__':
    web_protect_str = r''
    keys_str = r''
    cookies_str = ''



    from builder.auth import DouyinAuth
    auth_ = DouyinAuth()
    auth_.perepare_auth(cookies_str, web_protect_str, keys_str)

    live_url = "https://live.douyin.com/852953608964"
    live_id = "852953608964"
    res = DouyinAPI.get_live_info(auth_, live_id)
    print(res)

    room_id = res['room_id']
    anchor_id = res['anchor_id']
    sec_anchor_id = res['sec_uid']
    DouyinAPI.get_rank_list(auth_, room_id, anchor_id, sec_anchor_id)



    # res = DouyinAPI.search_live(auth_, "三角洲")
    # # print(res)
    # for i in res['data']:
    #     print(i['lives']['author']['nickname'])
    #     live_id = re.findall(r'"web_rid":"(.*?)",', str(i['lives']))[0]
    #     live_url = f'https://live.douyin.com/{live_id}'
    #     print(live_url)

    # my_uid = DouyinAPI.get_my_uid(auth_)
    # print(my_uid)
    # my_sec_uid = DouyinAPI.get_my_sec_uid(auth_)
    # print(my_sec_uid)
    # work_url = r'https://www.douyin.com/video/7433523124836060416'
    # print(DouyinAPI.get_user_info(auth_, "https://www.douyin.com/user/MS4wLjABAAAA7BDbZk0LjnEMcDDsLag5mDrMc157hD3x0SMhH1HaCM8"))
    # print(DouyinAPI.digg(auth_, "7433523124836060416", "1"))
    # print(DouyinAPI.digg(auth_, "7212619184386182435", "1"))
    # user_info = DouyinAPI.get_user_info(auth_, "https://www.douyin.com/user/MS4wLjABAAAAHXtdycTLMSe5Ld_468-9HKR1HUUrk4ywq-xMCM-E9w_cDIrhmynrQUalv061ZSpn?from_tab_name=main")
    # to_user_id = user_info['user']['uid']
    # conversation_id, conversation_short_id, ticket = DouyinAPI.create_conversation(auth_, to_user_id)
    # content = r'有份长期通告寻求合作，你通过了前期筛选，我是项目负责人，期待你与我联系：ncyj12'
    # DouyinAPI.send_msg(auth_, conversation_id, conversation_short_id, ticket, content)
    # print(DouyinAPI.get_user_all_work_info(auth_,"https://www.douyin.com/user/MS4wLjABAAAA8nC7nKxMrRtBwEqFzRgRBSxhBcw89VL0ysN-IXvhlKU?vid=7378825215213718818"))
    # print(DouyinAPI.get_work_info(auth_, "https://www.douyin.com/video/7212619184386182435"))
    # print(DouyinAPI.get_work_all_out_comment(auth_, "https://www.douyin.com/video/7212619184386182435"))
    # print(DouyinAPI.get_work_inner_comment(auth_, {
    #     "aweme_id": "7212619184386182435",
    #     "cid": "7327990109411902208"
    # }, "0"))
    # print(DouyinAPI.get_work_all_inner_comment(auth_, {
    #     "aweme_id": "7212619184386182435",
    #     "cid": "7327990109411902208"
    # }))
    # print(DouyinAPI.get_work_all_comment(auth_, "https://www.douyin.com/video/7212619184386182435"))
    # print(DouyinAPI.search_general_work(auth_, "美女", sort_type='2'))
    # print(DouyinAPI.search_some_general_work(auth_, "美女", sort_type='2', publish_time='0', num=30))
    # print(DouyinAPI.get_all_live_production(auth_, "https://live.douyin.com/84255891276"))
    # 60503986163 289606013148 91819894158
    # room_info = DouyinAPI.get_live_info(auth_, '60503986163')
    # print(room_info)
    # print(DouyinAPI.get_live_production(auth_, "https://live.douyin.com/84255891276", room_id, author_id, '0'))
    # print(DouyinAPI.collect_aweme(auth_, "7377676120549772554", '1'))
    # print(DouyinAPI.move_collect_aweme(auth_, "7207861673711930656", "tt", "7379252593215919891"))
    # print(DouyinAPI.remove_collect_aweme(auth_, "7376244589235113250", "tt", "7379252593215919891"))
    # print(DouyinAPI.get_live_production_detail(auth_, "https://live.douyin.com/552370739330", "3622058069401408240", "MS4wLjABAAAATfhR-kvE-AWqZaNaomCLFqgDKzvBwMS87FUGVjS_u7Y", "7379220637308504843"))
    # print(DouyinAPI.get_collect_list(auth_))
    # print(DouyinAPI.search_user(auth_, "巴旦木公主"))
    # print(DouyinAPI.search_some_user(auth_, "巴旦木公主", 30))
    # print(DouyinAPI.search_live(auth_, "馨馨baby😐ᵇᵃᵇʸ"))
    # print(DouyinAPI.get_user_favorite(auth_, "MS4wLjABAAAA99bTJ_GOw3odYmsXOe7i7xuEv0iQf2X_Kg_VUyVP0U8"))
    # print(DouyinAPI.get_some_user_follower_list(auth_, "3074704605975950", "MS4wLjABAAAA0L4jpkJDeuFO9AM-dQK1B649tmr7GIw-sQtyPasP_Z45QnUjIQgUOLIs8Kw8Gp-u", 40))
    # print(DouyinAPI.get_some_user_following_list(auth_, "3074704605975950", "MS4wLjABAAAA0L4jpkJDeuFO9AM-dQK1B649tmr7GIw-sQtyPasP_Z45QnUjIQgUOLIs8Kw8Gp-u", 40))
    # print(DouyinAPI.search_some_video_work(auth_, "巴旦木公主", 32))
    # print(DouyinAPI.get_feed(auth_))
    # print(DouyinAPI.publish_comment(auth_, "7356193166732709139"))
    # print(DouyinAPI.get_upload_auth_key(auth_))

    # while True:
    #     print(DouyinAPI.sendMsgInRoom(auth_, room_id, "666"))
    #     time.sleep(3)
    # #
    # while True:
    #     print(DouyinAPI.diggLiveRoom(auth_, room_id, '10'))
    #     time.sleep(1)
