# -*- coding: utf-8 -*-
"""平台接口层 —— 搜索与流域（mixin）

## 设计来源（照源项目 better-douyin）

对标 `src-tauri/src/api/client_feed.rs（搜索/流）`（方案 B 逆向情报）。

## 迁移说明（本分支 design/better-douyin，阶段1）

本模块由 `douyin_api.py` 的 `DouyinAPI` **机械拆分**而来（ast 精确取体，
非重写）：方法体逐字节保持原样，仅换宿主。

**内部调用保持 `DouyinAPI.xxx` 形式不变**——最终类仍是 `DouyinAPI`
（由 `dy_apis/douyin_api.py` 组装全部 mixin），故 109 处内部引用与
300+ 处外部调用**零改动**。

## 本域方法

```
  search_general_work
  search_some_general_work
  search_live
  search_some_live
```
"""
from __future__ import annotations

from utils.tls_policy import tls_verify  # noqa: E402
# 本域所需的导入（与原 `douyin_api.py` 头部一致，避免循环依赖）
# 公共导入头（json/re/uuid/requests/BeautifulSoup/logger/protobuf/builder/utils）
# 见 dy_apis/_common.py —— 2026-09-15 共享提取，替代各域文件重复的 17 行导入头。
from dy_apis._common import *  # noqa: F401,F403



#: 组装后的最终类（由 `dy_apis/_bindings.py: bind_all()` 注入本模块全局）。
#: 域模块内保留原文件的 `DouyinAPI.xxx(...)` 内部调用形式（共 109 处），
#: 靠此全局名解析到最终类 —— 从而**无需改动任何内部调用**（门面模式的关键）。
DouyinAPI = None  # type: ignore[assignment]


class SearchMixin:
    """搜索与流域接口（来自 DouyinAPI）。"""

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
        params.add_param("downlink", "10")
        params.add_param("effective_type", "4g")
        params.add_param("round_trip_time", "50")
        params.with_web_id(auth, refer)
        params.add_param("msToken", auth.msToken)
        # 综合搜索风控(antispam_check)只认新算法签名：纯算 a_bogus（Python 原生执行 bdms VMP）
        params.add_param('a_bogus', generate_a_bogus_pure(api, splice_url(params.get())))
        resp = requests.get(f'{DouyinAPI.douyin_url}{api}', headers=headers.get(), cookies=auth.cookie,
                            params=params.get(), verify=tls_verify())
        # 2026-09-17 修补（OCR 审查 HIGH）：原为裸 `json.loads(resp.text)`。
        # 抖音限流/风控时返回**空响应体**（非 JSON）→ 直接解析抛 JSONDecodeError，
        # 被上层当作「接口不可用」。同族的 client_relations/client_comments
        # 均已用 safe_json 优雅降级，此处漏改（降级策略不一致）。
        return safe_json(resp)


    @staticmethod
    def search_stream(auth, query: str, offset: str = '0', count: str = '10',
                      search_channel: str = 'aweme_general', **kwargs) -> dict:
        """源项目方案的搜索（2026-09-15 实测落地）

        源项目用 /aweme/v1/web/general/search/stream/ （逆向实证），
        不是我方原用的 /general/search/single/ 。

        ## 实测响应特征（关键，勿按普通 JSON 解析）
        HTTP 200 / 792794 字节
        格式是 chunked 分块，每块为：十六进制长度前缀 + CRLF + JSON + CRLF
        实际样例首部： 1289d 后面跟 JSON 的 status_code/data 数组
        直接 resp.json() 会失败，因为首字节不是左花括号。

        JSON 结构： status_code 加 data 数组，data 每项含 type 与 aweme_info ，
        aweme_info 内含 aweme_id / desc / author 等标准字段。

        :return: 归一化 dict，含 status_code / aweme_list / raw
                 aweme_list 已把 data 项里的 aweme_info 提取出来，便于下游复用
        """
        api = "/aweme/v1/web/general/search/stream/"
        headers = HeaderBuilder().build(HeaderType.GET)
        headers.set_referer("https://www.douyin.com/")
        prof = get_profile()
        params = Params()
        (params.add_param("device_platform", "webapp")
         .add_param("aid", "6383")
         .add_param("channel", "channel_pc_web")
         .add_param("pc_client_type", "1")
         .add_param("version_code", "170400")
         .add_param("version_name", "17.4.0")
         .add_param("cookie_enabled", "true")
         .add_param("browser_language", "zh-CN")
         .add_param("browser_platform", "Win32")
         .add_param("browser_name", prof["browser_name"])
         .add_param("browser_version", prof["browser_version"])
         .add_param("browser_online", "true")
         .add_param("engine_name", "Blink")
         .add_param("os_name", "Windows")
         .add_param("os_version", "10")
         .add_param("platform", "PC")
         .add_param("keyword", query)
         .add_param("offset", str(offset))
         .add_param("count", str(count))
         .add_param("search_channel", search_channel))
        params.with_web_id(auth, "https://www.douyin.com/")
        params.with_a_bogus()
        resp = requests.get(f'{DouyinAPI.domain_for(api)}{api}',
                            headers=headers.get(), cookies=auth.cookie,
                            params=params.get(), verify=tls_verify(),
                            timeout=kwargs.get("timeout", 30))
        # 关键：用 **bytes** 解析。chunked 的长度前缀是**字节数**，
        # 而 len(str) 是字符数 —— 响应含中文时两者不等，用 str 会整体错位。
        buf = resp.content or b""

        # chunked 分块解析：十六进制长度前缀 + CRLF + JSON + CRLF
        aweme_list = []
        raw_objs = []
        status_code = 0
        idx = 0
        total = len(buf)
        while idx < total:
            crlf = buf.find(b"\r\n", idx)
            if crlf < 0:
                break
            hexlen = buf[idx:crlf].strip()
            start = crlf + 2
            try:
                size = int(hexlen, 16)
            except ValueError:
                # 不是长度前缀，可能是纯 JSON，尝试整体解析
                try:
                    raw_objs.append(json.loads(buf[idx:].decode("utf-8", "replace")))
                except Exception:
                    pass
                break
            blob = buf[start:start + size]
            try:
                raw_objs.append(json.loads(blob.decode("utf-8", "replace")))
            except Exception:
                pass
            idx = start + size + 2

        for obj in raw_objs:
            if not isinstance(obj, dict):
                continue
            status_code = obj.get("status_code", status_code)
            for item in (obj.get("data") or []):
                if not isinstance(item, dict):
                    continue
                ai = item.get("aweme_info")
                aweme_list.append(ai if isinstance(ai, dict) else item)

        return {"status_code": status_code, "aweme_list": aweme_list, "raw": raw_objs}

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
            # 2026-09-17 修补（OCR 审查 HIGH）：平台限流/风控时 `search_general_work`
            # 返回 {} 或缺少 "data"/"has_more"（safe_json 的降级结果）→ 直接下标
            # 会抛 KeyError。先做键守卫，缺失即停止翻页（而非崩溃）。
            if not isinstance(res_json, dict) or not res_json.get("data"):
                logger.warning(f"[SEARCH-001] 搜索结果缺 data 字段（疑限流/风控），"
                               f"停止翻页: keys={list(res_json)[:6] if isinstance(res_json, dict) else type(res_json).__name__}")
                break
            works = [w for w in res_json["data"] if w.get("aweme_info")]
            work_list.extend(works)
            if res_json.get("has_more") != 1 or len(work_list) >= num:
                break
            offset = str(int(offset) + len(res_json["data"]))
        if len(work_list) > num:
            work_list = work_list[:num]
        return work_list

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
                            params=params.get(), verify=tls_verify())
        return safe_json(resp)

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

