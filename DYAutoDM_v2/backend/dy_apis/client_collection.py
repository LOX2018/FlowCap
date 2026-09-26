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

from utils.tls_policy import tls_verify  # noqa: E402
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
         .add_param("browser_online", "true").add_param("engine_name", get_profile()["engine_name"])
         .add_param("os_name", "Windows").add_param("os_version", "10")
         .add_param("cpu_core_num", get_profile()["cpu_core_num"])
         .add_param("device_memory", get_profile()["device_memory"])
         .add_param("platform", "PC").add_param("downlink", "10")
         .add_param("effective_type", "4g").add_param("round_trip_time", "100")
         .add_param("max_cursor", str(max_cursor)).add_param("count", str(num)))
        params.with_web_id(auth, "https://www.douyin.com/")
        params.with_a_bogus()
        # ★ 2026-09-26 修复（内容板块「收藏夹内作品」恒空 —— 根因：Argus 网关 403）
        #   `/aweme/v1/web/aweme/listcollection/` 在 secsdk webSign 保护清单内
        #   （utils/secsdk_web_sign.PROTECTED_PATHS_GET / _POST）。原实现把
        #   `params.get()` 交给 requests 的 `params=` —— 缺 uifid 与 secsdk 签名
        #   ⇒ 服务端恒返 **HTTP 403 "Blocked by ArgusSecurityPlugin Uifid Not
        #   Found"**（实测 46 字节非 JSON），safe_json 降级 `{}` ⇒ 恒空。
        #   ⇒ 改走 `signed_url`（带 uifid + secsdk 签名），实测 403 → 200。
        #   注意：必须发 signed_url 的返回值本身，不能再把 params 交给 requests
        #   （requests 会二次编码，与签名输入对不上 → 依旧 403）。
        url = params.signed_url(f'{DouyinAPI.domain_for(api)}{api}', auth)
        resp = requests.post(url,
                             headers=headers.get(), cookies=auth.cookie,
                             verify=tls_verify(), timeout=15)
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
         .add_param("browser_online", "true").add_param("engine_name", get_profile()["engine_name"])
         .add_param("os_name", "Windows").add_param("os_version", "10")
         .add_param("platform", "PC").add_param("count", str(count))
         .add_param("cursor", str(cursor)))
        params.with_web_id(auth, "https://www.douyin.com/")
        params.with_a_bogus()
        # ★ 2026-09-26 修复（内容板块「合集」恒空 —— 根因：Argus 网关 403）
        #   `/aweme/v1/web/mix/listcollection/` 在 secsdk webSign 保护清单内
        #   （utils/secsdk_web_sign.PROTECTED_PATHS_GET）。原实现把 `params.get()`
        #   交给 requests 的 `params=` —— 缺 uifid 与 secsdk 签名 ⇒ 服务端恒返
        #   **HTTP 403 "Blocked by ArgusSecurityPlugin Uifid Not Found"**，
        #   safe_json 降级 `{}` ⇒ 前端「合集」tab 永远空白，且拿不到 mix_id，
        #   连带的「合集内作品」也一并失效。
        #   ⇒ 改走 `signed_url`（带 uifid + secsdk 签名），实测 403 → 200
        #   （该账号 mix_infos 实测真实为 0，非拦截所致）。
        url = params.signed_url(f'{DouyinAPI.domain_for(api)}{api}', auth)
        resp = requests.get(url,
                            headers=headers.get(), cookies=auth.cookie,
                            verify=tls_verify(), timeout=15)
        return safe_json(resp)

    @staticmethod
    def get_mix_aweme(auth, mix_id: str, cursor: str = '0', count: str = '20', **kwargs):
        """**普通合集**内的作品（`/aweme/v1/web/mix/aweme/`，参数 `mix_id`）。

        ## 为什么新增（2026-09-21，用户反馈「合集内的视频一个都没获取到」）

        此前 `api/platform.py:/collection/series` 用的是 `get_series_aweme`
        —— 那是**短剧（series）专用**接口，参数叫 `series_id`。对普通合集
        （实测 `is_serial_mix = 0`、有 `mix_id` 但无 `series_id`）调用它会返回

            {"status_code": 5, "status_msg": "参数不合法", "aweme_list": null}

        ⇒ 前端因此永远看到空列表。正确接口是 `/aweme/v1/web/mix/aweme/`，
          参数 `mix_id`（上游 `mafqla/douyin-api` docs/user-api.md 与
          tikhub `fetch_video_mix_post_list` 一致：mix_id + cursor + count）。

        Args:
            mix_id: 合集 id（来自 `get_mix_list_collection` 的 `mix_infos[].mix_id`）

        🔴 **接线状态（2026-09-23，P1-7）**：本方法 2026-09-21 已实现，但当次**只加了
        方法没接路由** —— `api/platform.py:collection_series` 仍只调 `get_series_aweme`
        （文档声称的「已分流」是漂移）。现已真接线（`collection_series` 优先走本方法，
        `status_code != 0` 才回退短剧接口）。
        """
        api = "/aweme/v1/web/mix/aweme/"
        headers = HeaderBuilder().build(HeaderType.GET)
        # referer 必须是该合集的页面（实测 collection/<mix_id> 是正确来源页）
        headers.set_referer(f"https://www.douyin.com/collection/{mix_id}")
        params = Params()
        (params.add_param("device_platform", "webapp").add_param("aid", "6383")
         .add_param("channel", "channel_pc_web").add_param("pc_client_type", "1")
         .add_param("version_code", "170400").add_param("version_name", "17.4.0")
         .add_param("cookie_enabled", "true").add_param("browser_language", "zh-CN")
         .add_param("browser_platform", "Win32")
         .add_param("browser_name", get_profile()["browser_name"])
         .add_param("browser_version", get_profile()["browser_version"])
         .add_param("browser_online", "true").add_param("engine_name", get_profile()["engine_name"])
         .add_param("os_name", "Windows").add_param("os_version", "10")
         .add_param("platform", "PC")
         .add_param("mix_id", str(mix_id))
         .add_param("cursor", str(cursor)).add_param("count", str(count)))
        params.with_web_id(auth, f"https://www.douyin.com/collection/{mix_id}")
        params.add_param("msToken", auth.msToken)
        params.with_a_bogus()
        # ★ 必须走 secsdk 签名（2026-09-21 实测）：
        #   本接口在 PROTECTED_PATHS_GET 清单内，不加签服务端直接 403；
        #   且签名对规范化 query 算，**不能**再用 params= 传给 requests。
        url = params.signed_url(f'{DouyinAPI.domain_for(api)}{api}', auth)
        resp = requests.get(url, headers=headers.get(), cookies=auth.cookie,
                            verify=tls_verify(), timeout=15)
        return safe_json(resp)

    @staticmethod
    def get_series_aweme(auth, series_id: str, cursor: str = '0', count: str = '20', **kwargs):
        """**短剧**合集内的作品（`/aweme/v1/web/series/aweme/`，参数 `series_id`）。

        ⚠️ 2026-09-21 澄清：本接口**只适用于短剧**（`is_serial_mix = 1`）。
        普通合集请用 `get_mix_aweme`（参数 `mix_id`）—— 传错会得到
        `status_code: 5 / 参数不合法`。

        ⚠️ **文档漂移修正（2026-09-23，P1-7）**：本 docstring 原写
        「前端 `/collection/series` 端点已按 `is_serial_mix` 分流到两个接口」——
        那是**当时并不成立**的声明：`api/platform.py:collection_series` 实际只调
        本方法（用前端传来的 `mix_id` 打 `series_id`），分流从未落地。
        现已真的落地（见 `collection_series` 的 `_try`/回退逻辑），此句方可成立。
        """
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
         .add_param("browser_online", "true").add_param("engine_name", get_profile()["engine_name"])
         .add_param("os_name", "Windows").add_param("os_version", "10")
         .add_param("platform", "PC").add_param("series_id", str(series_id))
         .add_param("cursor", str(cursor)).add_param("count", str(count)))
        params.with_web_id(auth, "https://www.douyin.com/")
        params.with_a_bogus()
        resp = requests.get(f'{DouyinAPI.domain_for(api)}{api}',
                            headers=headers.get(), cookies=auth.cookie,
                            params=params.get(), verify=tls_verify(), timeout=15)
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
         .add_param("browser_online", "true").add_param("engine_name", get_profile()["engine_name"])
         .add_param("os_name", "Windows").add_param("os_version", "10")
         .add_param("platform", "PC").add_param("count", str(count))
         .add_param("cursor", str(cursor)))
        params.with_web_id(auth, "https://www.douyin.com/")
        params.with_a_bogus()
        # ★ 2026-09-26 修复（C-02 secsdk 签名接线）：`/aweme/v1/web/aweme/favorite/`
        #   在 secsdk webSign 保护清单内（PROTECTED_PATHS_GET）。原实现把 `params.get()`
        #   交给 requests 的 `params=` —— 缺 uifid 与 secsdk 签名 ⇒ 服务端恒返 403。
        #   改走 `signed_url`（带 uifid + secsdk 签名），与同文件其余 favorite 调用点一致。
        url = params.signed_url(f'{DouyinAPI.domain_for(api)}{api}', auth)
        resp = requests.get(url,
                            headers=headers.get(), cookies=auth.cookie,
                            verify=tls_verify(), timeout=15)
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
        params.add_param("engine_name", get_profile()["engine_name"])
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
        # ★ 2026-09-26 修复（内容板块「收藏夹」恒空 —— 根因：Argus 网关 403）：
        #   `/aweme/v1/web/collects/list/` 在 secsdk webSign 保护清单内
        #   （utils/secsdk_web_sign.PROTECTED_PATHS_GET）。原实现把 `params.get()`
        #   交给 requests 的 `params=` —— 缺 uifid 与 secsdk 签名 ⇒ 服务端恒返
        #   **HTTP 403 "Blocked by ArgusSecurityPlugin Uifid Not Found"**，
        #   safe_json 降级为 `{}` ⇒ 前端「收藏夹」永远 0 条。
        #   ⇒ 改走 `signed_url`（带 uifid + secsdk 签名），实测 403 → 200。
        #   注：实测该账号确实未建收藏夹文件夹（collects_list 真为 0），
        #   修复后返回的是**真实的 0** 而非被拦截的 0——区别在于 status_code
        #   已为 0 且响应体非空，前端不再误判为「接口不可用」。
        url = params.signed_url(f'{DouyinAPI.domain_for(api)}{api}', auth)
        res = requests.get(url, headers=headers.get(),
                           cookies=auth.cookie, verify=tls_verify())
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
                            cookies=auth.cookie, data=data, verify=tls_verify())
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
        params.add_param("engine_name", get_profile()["engine_name"])
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
                            cookies=auth.cookie, verify=tls_verify())
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
        params.add_param("engine_name", get_profile()["engine_name"])
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
                            cookies=auth.cookie, verify=tls_verify())
        return safe_json(res)

