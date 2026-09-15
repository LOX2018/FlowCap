# -*- coding: utf-8 -*-
"""平台接口层 —— 收藏与合集域（mixin）

## 设计来源（照源项目 better-douyin）

对标 `src-tauri/src/api/client_collection.rs`（方案 B 逆向情报）。

## 迁移说明（本分支 design/better-douyin，阶段1）

本模块由 `douyin_api.py` 的 `DouyinAPI` **机械拆分**而来（ast 精确取体，
非重写）：方法体逐字节保持原样，仅换宿主。

**内部调用保持 `DouyinAPI.xxx` 形式不变**——最终类仍是 `DouyinAPI`
（由 `dy_apis/douyin_api.py` 组装全部 mixin），故 109 处内部引用与
300+ 处外部调用**零改动**。

## 本域方法

```
  get_aweme_list_collection
  get_mix_list_collection
  get_series_aweme
  get_aweme_favorite
  get_collect_list
  collect_aweme
  move_collect_aweme
  remove_collect_aweme
```
"""
from __future__ import annotations

# 本域所需的导入（与原 `douyin_api.py` 头部一致，避免循环依赖）
# 公共导入头（json/re/uuid/requests/BeautifulSoup/logger/protobuf/builder/utils）
# 见 dy_apis/_common.py —— 2026-09-15 共享提取，替代各域文件重复的 17 行导入头。
from dy_apis._common import *  # noqa: F401,F403



#: 组装后的最终类（由 `dy_apis/_bindings.py: bind_all()` 注入本模块全局）。
#: 域模块内保留原文件的 `DouyinAPI.xxx(...)` 内部调用形式（共 109 处），
#: 靠此全局名解析到最终类 —— 从而**无需改动任何内部调用**（门面模式的关键）。
DouyinAPI = None  # type: ignore[assignment]


class CollectionMixin:
    """收藏与合集域接口（来自 DouyinAPI）。"""

    @staticmethod
    def get_aweme_list_collection(auth, max_cursor: str = '0', num: str = '18', **kwargs):
        """收藏夹内的作品列表（源项目 `/aweme/v1/web/aweme/listcollection/`）。"""
        api = "/aweme/v1/web/aweme/listcollection/"
        headers = HeaderBuilder().build(HeaderType.POST)
        headers.set_referer("https://www.douyin.com/user/self?from_tab_name=main&showTab=favorite_collection")
        params = Params()
        (params.add_param("device_platform", "webapp")
         .add_param("aid", "6383").add_param("channel", "channel_pc_web")
         .add_param("pc_client_type", "1").add_param("version_code", "170400")
         .add_param("version_name", "17.4.0").add_param("cookie_enabled", "true")
         .add_param("screen_width", get_profile()["screen_width"])
         .add_param("screen_height", get_profile()["screen_height"])
         .add_param("browser_language", "zh-CN").add_param("browser_platform", "Win32")
         .add_param("browser_name", get_profile()["browser_name"])
         .add_param("browser_version", get_profile()["browser_version"])
         .add_param("browser_online", "true").add_param("engine_name", "Blink")
         .add_param("os_name", "Windows").add_param("os_version", "10")
         .add_param("cpu_core_num", get_profile()["cpu_core_num"])
         .add_param("device_memory", get_profile()["device_memory"])
         .add_param("platform", "PC").add_param("downlink", "10")
         .add_param("effective_type", "4g").add_param("round_trip_time", "100")
         .add_param("max_cursor", str(max_cursor)).add_param("count", str(num)))
        params.with_web_id(auth, "https://www.douyin.com/")
        params.with_a_bogus()
        resp = requests.post(f'{DouyinAPI.domain_for(api)}{api}',
                             headers=headers.get(), cookies=auth.cookie,
                             params=params.get(), verify=False, timeout=15)
        return safe_json(resp)

    @staticmethod
    def get_mix_list_collection(auth, count: str = '20', cursor: str = '0', **kwargs):
        """收藏的**合集**列表（源项目 `/aweme/v1/web/mix/listcollection/`）。"""
        api = "/aweme/v1/web/mix/listcollection/"
        headers = HeaderBuilder().build(HeaderType.GET)
        headers.set_referer("https://www.douyin.com/user/self?from_tab_name=main&showTab=favorite_collection")
        params = Params()
        (params.add_param("device_platform", "webapp").add_param("aid", "6383")
         .add_param("channel", "channel_pc_web").add_param("pc_client_type", "1")
         .add_param("version_code", "170400").add_param("version_name", "17.4.0")
         .add_param("cookie_enabled", "true").add_param("browser_language", "zh-CN")
         .add_param("browser_platform", "Win32")
         .add_param("browser_name", get_profile()["browser_name"])
         .add_param("browser_version", get_profile()["browser_version"])
         .add_param("browser_online", "true").add_param("engine_name", "Blink")
         .add_param("os_name", "Windows").add_param("os_version", "10")
         .add_param("platform", "PC").add_param("count", str(count))
         .add_param("cursor", str(cursor)))
        params.with_web_id(auth, "https://www.douyin.com/")
        params.with_a_bogus()
        resp = requests.get(f'{DouyinAPI.domain_for(api)}{api}',
                            headers=headers.get(), cookies=auth.cookie,
                            params=params.get(), verify=False, timeout=15)
        return safe_json(resp)

    @staticmethod
    def get_series_aweme(auth, series_id: str, cursor: str = '0', count: str = '20', **kwargs):
        """合集内的作品（源项目 `/aweme/v1/web/series/aweme/`）。"""
        api = "/aweme/v1/web/series/aweme/"
        headers = HeaderBuilder().build(HeaderType.GET)
        headers.set_referer("https://www.douyin.com/")
        params = Params()
        (params.add_param("device_platform", "webapp").add_param("aid", "6383")
         .add_param("channel", "channel_pc_web").add_param("pc_client_type", "1")
         .add_param("version_code", "170400").add_param("version_name", "17.4.0")
         .add_param("cookie_enabled", "true").add_param("browser_language", "zh-CN")
         .add_param("browser_platform", "Win32")
         .add_param("browser_name", get_profile()["browser_name"])
         .add_param("browser_version", get_profile()["browser_version"])
         .add_param("browser_online", "true").add_param("engine_name", "Blink")
         .add_param("os_name", "Windows").add_param("os_version", "10")
         .add_param("platform", "PC").add_param("series_id", str(series_id))
         .add_param("cursor", str(cursor)).add_param("count", str(count)))
        params.with_web_id(auth, "https://www.douyin.com/")
        params.with_a_bogus()
        resp = requests.get(f'{DouyinAPI.domain_for(api)}{api}',
                            headers=headers.get(), cookies=auth.cookie,
                            params=params.get(), verify=False, timeout=15)
        return safe_json(resp)

    @staticmethod
    def get_aweme_favorite(auth, count: str = '18', cursor: str = '0', **kwargs):
        """我的收藏（作品维度，源项目 `/aweme/v1/web/aweme/favorite/`）。

        ⚠️ **实测状态（2026-09-14）**：真实账号下四种组合（GET/POST × 主域名/备用域名）
        均返回 **HTTP 200 + 空响应体**（len=0）⇒ 该路径对当前账号无数据或平台已下线。

        **替代**：`get_user_favorite(auth, sec_id, ...)`（基座既有，走
        `/aweme/v1/web/aweme/favorite/` 的 user 维度变体）实测可用。
        本方法保留以对齐源项目接口面，但调用方**应优先用 `get_user_favorite`**。
        """
        api = "/aweme/v1/web/aweme/favorite/"
        headers = HeaderBuilder().build(HeaderType.GET)
        headers.set_referer("https://www.douyin.com/")
        params = Params()
        (params.add_param("device_platform", "webapp").add_param("aid", "6383")
         .add_param("channel", "channel_pc_web").add_param("pc_client_type", "1")
         .add_param("version_code", "170400").add_param("version_name", "17.4.0")
         .add_param("cookie_enabled", "true").add_param("browser_language", "zh-CN")
         .add_param("browser_platform", "Win32")
         .add_param("browser_name", get_profile()["browser_name"])
         .add_param("browser_version", get_profile()["browser_version"])
         .add_param("browser_online", "true").add_param("engine_name", "Blink")
         .add_param("os_name", "Windows").add_param("os_version", "10")
         .add_param("platform", "PC").add_param("count", str(count))
         .add_param("cursor", str(cursor)))
        params.with_web_id(auth, "https://www.douyin.com/")
        params.with_a_bogus()
        resp = requests.get(f'{DouyinAPI.domain_for(api)}{api}',
                            headers=headers.get(), cookies=auth.cookie,
                            params=params.get(), verify=False, timeout=15)
        return safe_json(resp)

    @staticmethod
    def get_collect_list(auth, max_cursor: str = "0", num: str = "20", **kwargs):
        """
        获取我的收藏夹列表
        :param auth: DouyinAuth object.
        :param max_cursor: 翻页游标（首次 '0'，后续取上次响应的 cursor）。
        :param num: 每页数量。
        :return: JSON.

        ## 2026-09-15 修复（原硬编码 cursor='0' / count='20' 无法翻页）
        实测响应为 {collects_list, cursor, has_more, total_number, status_code}；
        原实现把 cursor/count 写死，翻页参数无法传入 —— 现按签名开放。
        """
        api = "/aweme/v1/web/collects/list/"
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = "https://www.douyin.com/?recommend=1"
        headers.set_referer(refer)
        params = Params()
        params.add_param("device_platform", "webapp")
        params.add_param("aid", "6383")
        params.add_param("channel", "channel_pc_web")
        params.add_param("cursor", str(max_cursor))
        params.add_param("count", str(num))
        params.add_param("update_version_code", "170400")
        params.add_param("pc_client_type", "1")
        params.add_param("version_code", "170400")
        params.add_param("version_name", "17.4.0")
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
        return safe_json(res)

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
        return safe_json(res)

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
        params.add_param("browser_platform", "Win32")
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
        return safe_json(res)

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
        params.add_param("browser_platform", "Win32")
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
        return safe_json(res)

