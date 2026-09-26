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

from utils.tls_policy import tls_verify  # noqa: E402
# 本域所需的导入（与原 `douyin_api.py` 头部一致，避免循环依赖）
# 公共导入头（json/re/uuid/requests/BeautifulSoup/logger/protobuf/builder/utils）
# 见 dy_apis/_common.py —— 2026-09-15 共享提取，替代各域文件重复的 17 行导入头。
from dy_apis._common import *  # noqa: F401,F403



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
        # ★ 2026-09-26 修复（C-02 secsdk 签名接线 / M-2）：
        #   `/aweme/v1/web/aweme/detail/` 在 secsdk webSign 保护清单内
        #   （`utils.secsdk_web_sign.PROTECTED_PATHS_GET`）。原实现把 `params.get()`
        #   交给 requests 的 `params=` —— 缺 uifid 与 secsdk 签名 ⇒ 被 Argus 网关拦下。
        #   **A/B 实机实测**（真实作品 id=7680508646496750890，2026-09-26）：
        #     不带签名 → HTTP **403**（46B `Blocked by ArgusSecurityPlugin Uifid Not Found`）
        #     带签名   → HTTP **200** + **124,004 字节** + `aweme_detail` **非空**
        #   ⇒ 改走 `signed_url`（带 uifid + secsdk 签名）。
        #   注意：必须发 `signed_url` 的返回值本身，**不能再把 params 交给 requests**
        #   （否则 requests 会二次编码，与签名输入不一致 → 依旧 403）。
        url = params.signed_url(f'{DouyinAPI.domain_for(api)}{api}', auth)
        resp = requests.get(url, headers=headers.get(), cookies=auth.cookie,
                            verify=tls_verify())
        # 2026-09-17 修补（OCR 审查 HIGH）：裸 json.loads → safe_json。
        resp_json = safe_json(resp)
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
            # 2026-09-17 修补（OCR 审查 HIGH —— 无守卫下标 + 无轮数上限）：
            # 限流/空响应（safe_json → {}）时原 `res_json["data"]` 抛 KeyError；
            # 无上限的 while 在平台恒返 has_more=1 且 data 空时会死循环。
            if not isinstance(res_json, dict):
                break
            video_works = res_json.get("data")
            if not video_works:
                break
            video_work_list.extend(video_works)
            if res_json.get("has_more") != 1 or len(video_work_list) >= num:
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
                            params=params.get(), verify=tls_verify())
        search_id = resp.headers["X-Tt-Logid"]
        json_data = resp.json()
        return search_id, json_data["guide_search_words"], json_data

    @staticmethod
    def get_feed(auth, count='20', refresh_index='2', **kwargs):
        """获取首页推荐视频。

        ## 2026-09-17 修补（OCR 审查 CRITICAL）
        本方法由 douyin_api.py 机械拆分而来，**丢失了 `@staticmethod` 装饰器**
        —— 方法体首参是 auth 而非 self。缺少装饰器时定义在 class 体内会被当作
        普通实例方法，`DouyinAPI.get_feed(auth, ...)`（douyin_api.py:153 的调用
        形态）会因缺 self 实参抛 `TypeError: get_feed() missing 1 required
        positional argument`。同文件 get_work_info / search_video_work 均带
        @staticmethod，此处补齐以保持一致。

        ## ★ 2026-09-15 改用源项目接口（业务接口一律换成源项目方案）

        原用 `/aweme/v1/web/module/feed/`（**老接口**）：返回非标准的
        `cards[].aweme`，且 `aweme` 是 **JSON 字符串**（需二次 json.loads），
        解包脆弱（此前实测恒 0 条，靠补丁才取出 10 条）。

        **源项目实测方案**（`all_strings.txt` 逆向 + 直连验证）：
        ```
        /aweme/v1/web/tab/feed/   → HTTP 200 / 659465 字节
        返回标准结构：{aweme_list:[…], has_more, log_pb, status_code}
        实测 6 条；字段标准（desc / author.nickname / aweme_id 直接可读）
        ```
        ⇒ 改用该路径；下游**不再需要** cards + JSON 字符串的特殊解包。

        :param auth: DouyinAuth object.
        :param count: 数量.
        :param refresh_index: 刷新索引.
        :return: JSON（含 `aweme_list`）
        """
        api = "/aweme/v1/web/tab/feed/"
        headers = HeaderBuilder().build(HeaderType.GET)
        headers.set_referer("https://www.douyin.com/")
        params = Params()
        (params.add_param("device_platform", 'webapp')
         .add_param("aid", '6383')
         .add_param("channel", 'channel_pc_web')
         .add_param("pc_client_type", '1')
         .add_param("version_code", '170400')
         .add_param("version_name", '17.4.0')
         .add_param("cookie_enabled", 'true')
         .add_param("browser_language", 'zh-CN')
         .add_param("browser_platform", 'Win32')
         .add_param("browser_name", get_profile()["browser_name"])
         .add_param("browser_version", get_profile()["browser_version"])
         .add_param("browser_online", 'true')
         .add_param("engine_name", 'Blink')
         .add_param("os_name", 'Windows')
         .add_param("os_version", '10')
         .add_param("platform", 'PC')
         .add_param("count", str(count))
         .add_param("refresh_index", str(refresh_index)))
        params.with_web_id(auth, "https://www.douyin.com/")
        params.with_a_bogus()
        # ★ 2026-09-26（M-2 收尾）：`/aweme/v1/web/tab/feed/` 在 secsdk 保护清单内
        #   （`PROTECTED_PATHS_GET`）。**A/B 实机实测**（真实账号，2026-09-26）：
        #     不带签名 → HTTP **200** / 229,138 字节
        #     带签名   → HTTP **200** / 211,847 字节
        #   ⇒ 两者**均成功** —— 说明清单对本端点**过宽**（G2 门禁原告警
        #   与实测矛盾，已裁决）。但**签名严格占优**（两者皆 200，
        #   签名不引入新失败面、且对齐上游清单），按用户
        #   「保守策略为主」取保守侧 ⇒ 补签名。
        #   注意：必须发 `signed_url` 的返回值本身（不再传 params）。
        url = params.signed_url(f'{DouyinAPI.domain_for(api)}{api}', auth)
        resp = requests.get(url,
                            headers=headers.get(), cookies=auth.cookie,
                            verify=tls_verify(), timeout=15)
        return safe_json(resp)

