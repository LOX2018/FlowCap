# -*- coding: utf-8 -*-
"""平台接口层 —— 用户域（mixin）

## 设计来源（照源项目 better-douyin）

对标 `src-tauri/src/api/client_user.rs`（方案 B 逆向情报）。

## 迁移说明（本分支 design/better-douyin，阶段1）

本模块由 `douyin_api.py` 的 `DouyinAPI` **机械拆分**而来（ast 精确取体，
非重写）：方法体逐字节保持原样，仅换宿主。

**内部调用保持 `DouyinAPI.xxx` 形式不变**——最终类仍是 `DouyinAPI`
（由 `dy_apis/douyin_api.py` 组装全部 mixin），故 109 处内部引用与
300+ 处外部调用**零改动**。

## 本域方法

```
  get_user_info
  get_user_favorite
  get_my_uid
  get_my_sec_uid
  get_user_all_work_info
  get_user_work_info
  get_device_id
  search_some_user
  search_user
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


class UserMixin:
    """用户域接口（来自 DouyinAPI）。"""

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

