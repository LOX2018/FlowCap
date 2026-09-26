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


class LiveSearchResult(list):
    """``search_some_live`` 的返回值：直播列表 + **传输层事实**。

    为什么不是裸 ``list``（2026-09-27 实测复现）：内置 ``list`` 没有 ``__dict__``，
    ``lst.last_transport = x`` 直接抛 ``AttributeError``。若用 ``try/except`` 吞掉
    这个异常，风控事实就**静默丢失** —— 上层只能收到空列表，前端显示「没搜到」，
    正是项目铁律禁止的**假成功**。

    本类是 ``list`` 的子类，故 ``for x in result`` / ``len(result)`` /
    ``result[:n]`` 等既有用法**零改动**（`search_some_live` 的返回契约不变）。

    ``last_transport`` 为 ``None`` 表示传输层正常（HTTP 200 且响应可解析）；
    非 ``None`` 时形如 ``{"status": 403, "bytes": 46}``。
    """

    def __init__(self, items=None, transport=None):
        super().__init__(items or [])
        self.last_transport = transport


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
        params.add_param("engine_name", get_profile()["engine_name"])
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
        # ★ 2026-09-27 修复（M-14 收口 / 接 ADR-018 D6 接线）:
        #   `/aweme/v1/web/general/search/single/` 原实现把 `params.get()` 交给
        #   requests 的 `params=` —— 缺 uifid 与 secsdk webSign ⇒ 被 Argus 网关
        #   拦下（HTTP 403 + 46B `Blocked by ArgusSecurityPlugin Uifid Not
        #   Found`，同族 M-2 / client_comments 实测结论）⇒ safe_json 降级 `{}`
        #   ⇒ 综合搜索恒空（假成功）。
        #   ⇒ 改走 `signed_url`（带 uifid + `x-secsdk-web-signature`），同
        #   client_comments 的已验写法。
        #   必须发 `signed_url` 的返回值本身（**不能再把 params 交给 requests**），
        #   否则 requests 二次编码 → 与签名输入不一致 → 依旧 403（M-2 S2 判据）。
        url = params.signed_url(f'{DouyinAPI.domain_for(api)}{api}', auth)
        resp = requests.get(url, headers=headers.get(), cookies=auth.cookie,
                            verify=tls_verify())
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
         .add_param("engine_name", get_profile()["engine_name"])
         .add_param("os_name", "Windows")
         .add_param("os_version", "10")
         .add_param("platform", "PC")
         .add_param("keyword", query)
         .add_param("offset", str(offset))
         .add_param("count", str(count))
         .add_param("search_channel", search_channel))
        params.with_web_id(auth, "https://www.douyin.com/")
        params.with_a_bogus()
        # ★ 2026-09-27 修复（M-14 收口 / 接 ADR-018 D6 接线）：
        #   `/aweme/v1/web/general/search/stream/` 原实现同样把 `params.get()`
        #   交给 requests 的 `params=`（缺 uifid + secsdk webSign）⇒ Argus
        #   **HTTP 403（46B 非 JSON）**。本端点响应是 chunked 流，403 时
        #   `buf` 只有 46 字节的 `Blocked by ArgusSecurityPlugin Uifid Not
        #   Found`，chunked 解析全部失败 ⇒ `aweme_list=[]` 且 `status_code=0`
        #   —— 上层看到「空列表」当作「没搜到」（项目铁律禁止的假成功）。
        #   ⇒ 改走 `signed_url`；**不能再把 params 交给 requests**（二次编码
        #   会让签名失效 → 依旧 403）。
        url = params.signed_url(f'{DouyinAPI.domain_for(api)}{api}', auth)
        resp = requests.get(url,
                            headers=headers.get(), cookies=auth.cookie,
                            verify=tls_verify(),
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
        params.add_param("engine_name", get_profile()["engine_name"])
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
        # ★ 2026-09-27 修复（ADR-018 D6 / F5 接线前置）：本端点原实现把
        #   `params.get()` 交给 requests 的 `params=` —— 缺 uifid 与 secsdk
        #   webSign ⇒ 被 Argus 网关拦下（HTTP 403 + 46B
        #   `Blocked by ArgusSecurityPlugin Uifid Not Found`，同族 M-2 实测结论）。
        #
        #   ⚠️ 本路径 `/aweme/v1/web/live/search/` **不在** PROTECTED_PATHS_GET，
        #   因此 `test_m2_secsdk_send_side.S1` 的保护清单门禁**抓不到**它；
        #   且该清单是 GET/POST 共用的白名单，不应为其而改动清单本身。
        #   ⇒ 按 D6 决策，此处**无条件**改走 `signed_url`（与 client_video /
        #   client_user / client_collection 里需要签名的 GET 同构）。
        #
        #   必须发 `signed_url` 的返回值本身（**不能再把 params 交给 requests**），
        #   否则 requests 会二次编码，与签名输入不一致 → 依旧 403。
        url = params.signed_url(f'{DouyinAPI.domain_for(api)}{api}', auth)
        resp = requests.get(url, headers=headers.get(), cookies=auth.cookie,
                            verify=tls_verify())
        resp_json = safe_json(resp)
        # 2026-09-27 修补（ADR-018 F5 · 「禁止假成功」）：`safe_json` 把
        # Argus 403（46B `Blocked by ArgusSecurityPlugin Uifid Not Found`）
        # 与非 JSON 错误页一律降级成 `{}` —— 上层因此**无法区分**
        # 「真的没有这个关键词的直播间」与「被风控拦截」，前端只能显示空列表
        # 假装没结果（项目铁律禁止）。此处把传输层事实**如实带出**。
        try:
            _status, _nbytes = resp.status_code, len(resp.content or b"")
        except Exception:  # noqa: BLE001
            _status, _nbytes = 0, 0
        if _status != 200 or not isinstance(resp_json, dict) or not resp_json:
            if isinstance(resp_json, dict):
                resp_json["_transport"] = {"status": _status, "bytes": _nbytes}
        return resp_json

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
        # 2026-09-27 修补（ADR-018 F5 接线）：原实现裸下标 `res_json["data"]` /
        # `res_json["has_more"]` —— safe_json 在限流/风控（Argus 403 空响应体）
        # 时降级返回 `{}`，直接下标抛 KeyError，被上层 features._safe 吞成
        # 「未知错误」，用户看不到「被风控拦截」。此处补与
        # `search_some_general_work` 同款的键守卫 + 轮数上限（平台恒返
        # has_more=1 且 data 为空时不得死循环）。
        MAX_ROUNDS = 20
        transport = None
        for _round in range(MAX_ROUNDS):
            res_json = DouyinAPI.search_live(auth, query, offset, count)
            if isinstance(res_json, dict) and res_json.get("_transport"):
                # 风控/限流：把传输层事实带出，供上层如实告知用户（禁止显示空列表）
                transport = res_json["_transport"]
            if not isinstance(res_json, dict) or not res_json.get("data"):
                logger.warning(f"[SEARCH-002] 直播搜索缺 data（疑限流/风控），停止翻页: "
                               f"keys={list(res_json)[:6] if isinstance(res_json, dict) else type(res_json).__name__}")
                break
            live_list.extend(res_json["data"])
            if res_json.get("has_more") != 1 or len(live_list) >= num:
                break
            offset = str(int(offset) + int(count))
        if len(live_list) > num:
            live_list = live_list[:num]
        # 2026-09-27：把传输层事实带出给上层（「禁止假成功」的载体）。
        # ⚠️ 必须用 `LiveSearchResult` 而非裸 `list`：内置 `list` **没有
        # __dict__**，直接 `lst.last_transport = ...` 会抛 AttributeError
        # （实测复现）。此处在返回前统一包装，且切片后的结果也仍用本类构造。
        return LiveSearchResult(live_list, transport)

    @staticmethod
    def take_live_transport(live_list) -> dict | None:
        """取回 ``search_some_live`` 结果上的传输层事实（无则 None）。"""
        return getattr(live_list, "last_transport", None) if live_list is not None else None

