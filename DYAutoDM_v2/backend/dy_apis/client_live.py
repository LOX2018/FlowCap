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

from utils.tls_policy import tls_verify  # noqa: E402
# 本域所需的导入（与原 `douyin_api.py` 头部一致，避免循环依赖）
# 公共导入头（json/re/uuid/requests/BeautifulSoup/logger/protobuf/builder/utils）
# 见 dy_apis/_common.py —— 2026-09-15 共享提取，替代各域文件重复的 17 行导入头。
from dy_apis._common import *  # noqa: F401,F403



#: 组装后的最终类（由 `dy_apis/_bindings.py: bind_all()` 注入本模块全局）。
#: 域模块内保留原文件的 `DouyinAPI.xxx(...)` 内部调用形式（共 109 处），
#: 靠此全局名解析到最终类 —— 从而**无需改动任何内部调用**（门面模式的关键）。
DouyinAPI = None  # type: ignore[assignment]

#: 直播域主机名。**a_bogus 签名必须按子域取 (aid, page_id)**：直播域是
#: (6383, 7571)，与 www 主站的 (6383, 11881) 不同。用主站值签 live 请求会被
#: 服务端判人机验证/静默拒绝（表现为「直播流数据全部拉不到」且无报错）。
#: 对齐上游 cv-cat/DouYin_Spider `dy_apis/douyin_api.py: LIVE_HOST`。
LIVE_HOST = 'live.douyin.com'


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
        res = requests.get(url, headers=headers, cookies=auth_.cookie, verify=tls_verify())
        # 2026-09-22（T-01 / ENG-022）：原实现 `res.cookies.get_dict()['ttwid']`
        # 是**硬 KeyError** —— 服务端未下发 ttwid 时（直播间不存在 / 连接被拦 /
        # 域名或路径拼错，实测 `live.douyin.com/https://…` 即此）异常在解析前抛出，
        # 被调用方 `_room_info_with_credential` 捕获成「带凭证进房异常」→ 回落匿名
        # → 也失败 → 进房全断。ttwid 只是建连用的设备级 cookie，缺失不该让整条
        # 进房链崩掉：缺失时置空由服务端裁决（与 _anon_live_info 的 `or ""` 同构）。
        ttwid = (res.cookies.get_dict() or {}).get('ttwid') or ""
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
                    # 2026-09-17 修补：删除残留调试 print（污染 stdout，
                    # daemon 环境下会混入日志；且会打印直播间全量信息）。
                    logger.debug(f"[LIVE] get_live_info 解析成功 room={room_id}")
                    return res
                except Exception as e:
                    pass
        # 2026-09-17 修补（OCR 审查 HIGH —— 返回形状与调用方约定不符）：
        # 原返回 `(None, None, None)` 三元组，但调用方一律按「dict 或 None」
        # 处理（`if not room_info` / `room_info["room_id"]`）→ 元组为真值
        # 会通过 falsy 检查，随后下标抛 TypeError。统一返回 None。
        return None

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
        params.add_param("browser_version", get_profile()["browser_version"])
        params.add_param("browser_online", "true")
        params.add_param("engine_name", get_profile()["engine_name"])
        params.add_param("engine_version", get_profile()["engine_version"])
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
        params.with_a_bogus(host=LIVE_HOST)
        res = requests.post(f'{DouyinAPI.live_url}{api}', headers=headers.get(), cookies=auth.cookie,
                           params=params.get(), verify=tls_verify())
        return safe_json(res)

    @staticmethod
    def get_all_live_production(auth, url: str, **kwargs):
        """
        获取直播间的所有商品信息.
        :param auth: DouyinAuth object.
        :param url: 直播间链接.
        :return:
        """
        room_info = DouyinAPI.get_live_info(auth, url.split("/")[-1].split("?")[0])
        # 2026-09-17 修补（OCR 审查 HIGH —— None 下标）：
        # get_live_info 解析不到 roomId 时返回 None → 原 `room_info["room_id"]`
        # 抛 TypeError 打断整批。现跳过该条（与"房间不可用"语义一致）。
        if not isinstance(room_info, dict):
            logger.warning(f"[LIVE-020] get_live_info 无有效房间信息，"
                           f"返回空商品列表: {url}")
            return []
        room_id = room_info["room_id"]
        author_id = room_info["author_id"]
        offset = "0"
        production_list = []
        while True:
            res_json = DouyinAPI.get_live_production(auth, url, room_id, author_id, offset)
            # 2026-09-17 修补（OCR 审查 HIGH —— 无守卫下标 + 无轮数上限）：
            # safe_json 可能降级为 {} → 原 `res_json["promotions"]` 抛 KeyError；
            # 且 `while True` 无上限，平台若恒返非 -1 的 next_offset 会死循环。
            if not isinstance(res_json, dict):
                break
            productions = res_json.get("promotions") or []
            if not productions:
                break
            production_list.extend(productions)
            offset = str(res_json.get("next_offset") or "-1")
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
        params.add_param("engine_name", get_profile()["engine_name"])
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
        params.with_a_bogus(data, host=LIVE_HOST)
        res = requests.post(f'{DouyinAPI.live_url}{api}', headers=headers.get(), params=params.get(),
                            cookies=auth.cookie, data=data, verify=tls_verify())
        return safe_json(res)

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
        params.add_param("browser_name", get_profile()["browser_name"])
        params.add_param("browser_version", get_profile()["browser_version"])
        params.add_param("webcast_sdk_version", "2450")
        params.add_param("room_id", room_id)
        params.add_param("anchor_id", anchor_id)
        params.add_param("sec_anchor_id", sec_anchor_id)
        params.add_param("ignoreToast", "true")
        params.add_param("rank_type", "30")
        params.add_param("update_scene", "rank_message")
        params.add_param("msToken", auth.msToken)
        params.with_a_bogus(host=LIVE_HOST)
        response = requests.get(url, headers=headers.get(), params=params.get(),
                           cookies=auth.cookie, verify=tls_verify())

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
        params.add_param("screen_width", get_profile()["screen_width"])
        params.add_param("screen_height", get_profile()["screen_height"])
        params.add_param("browser_language", "en")
        params.add_param("browser_platform", "Win32")
        params.add_param("browser_name", "Mozilla")
        # ★ 2026-09-26（ADR-016 D4 / H-22 审计 idx10 订正）：
        #   本字段名是 `browser_version`，语义为**短版本号**（如 "152.0"）——
        #   全仓 REST 参数 30+ 处一律取 profile["browser_version"]。
        #   上一轮误把「硬编码旧 Chrome UA」换成 profile["ua"]（**完整 UA 串**），
        #   与同文件上方 250 行及全仓取值形态不一致，反成新的自相矛盾。
        #   注：`builder/proto.py` 的 IM protobuf 分支确以「UA 去掉 Mozilla/」充当
        #   browser_version（客户端 navigator.appVersion 语义），那是**独立约定**，
        #   REST 参数不适用。此处按 REST 约定回短版本号。
        params.add_param("browser_version", get_profile()["browser_version"])
        params.add_param("browser_online", "true")
        params.add_param("tz_name", "Asia/Shanghai")
        params.add_param("msToken", auth.msToken)
        params.with_a_bogus(host=LIVE_HOST)
        res = requests.get(f'{DouyinAPI.live_url}{api}', headers=headers.get(), params=params.get(),
                           cookies=auth.cookie, verify=tls_verify())
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
        params.add_param("browser_name", get_profile()["browser_name"])
        params.add_param("browser_version", get_profile()["browser_version"])
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
        params.with_a_bogus(data, host=LIVE_HOST)
        res = requests.post(f'{DouyinAPI.live_url}{api}', headers=headers.get(),
                            params=params.get(), cookies=auth.cookie, data=data, verify=tls_verify())
        return safe_json(res)

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
        params.add_param("browser_name", get_profile()["browser_name"])
        params.add_param("browser_version", get_profile()["browser_version"])
        params.add_param("os_name", 'Windows')
        params.add_param("os_version", '10')
        params.add_param("room_id", str(room_id))
        params.add_param("msToken", auth.msToken)
        params.with_a_bogus(host=LIVE_HOST)
        res = requests.get(f'{DouyinAPI.live_url}{api}', headers=headers.get(),
                           params=params.get(), cookies=auth.cookie, verify=tls_verify())
        return safe_json(res)

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
        params.add_param("browser_name", get_profile()["browser_name"])
        params.add_param("browser_version", get_profile()["browser_version"])
        params.add_param("os_name", 'Windows')
        params.add_param("os_version", '10')
        params.add_param("room_id", str(room_id))
        params.add_param("msToken", auth.msToken)
        params.with_a_bogus(host=LIVE_HOST)
        res = requests.get(f'{DouyinAPI.live_url}{api}', headers=headers.get(),
                           params=params.get(), cookies=auth.cookie, verify=tls_verify())
        return safe_json(res)

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
        params.add_param("browser_name", get_profile()["browser_name"])
        params.add_param("browser_version", get_profile()["browser_version"])
        params.add_param("os_name", 'Windows')
        params.add_param("os_version", '10')
        params.add_param("room_id", str(room_id))
        params.add_param("msToken", auth.msToken)
        data = {"room_id": str(room_id)}
        params.with_a_bogus(data, host=LIVE_HOST)
        res = requests.post(f'{DouyinAPI.live_url}{api}', headers=headers.get(),
                            params=params.get(), cookies=auth.cookie, data=data, verify=tls_verify())
        return safe_json(res)

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
        params.add_param("browser_name", get_profile()["browser_name"])
        params.add_param("browser_version", get_profile()["browser_version"])
        params.add_param("os_name", 'Windows')
        params.add_param("os_version", '10')
        params.add_param("room_id", str(room_id))
        params.add_param("msToken", auth.msToken)
        data = {"room_id": str(room_id)}
        params.with_a_bogus(data, host=LIVE_HOST)
        res = requests.post(f'{DouyinAPI.live_url}{api}', headers=headers.get(),
                            params=params.get(), cookies=auth.cookie, data=data, verify=tls_verify())
        return safe_json(res)

    @staticmethod
    def _live_chat_room_id(auth, room_id):
        """把「直播间号(web_rid)」升级为**真实 room_id** —— 直播域写接口只认后者。

        ## 为什么必须有这一层（2026-09-30，直播发送链路根因）

        本项目「直播间号」有两套标识，此前链路把二者混用了：

        | 标识 | 取值来源 | 谁在用 |
        |---|---|---|
        | `web_rid`（URL 短号，如 `992931212705`） | `link_resolve.resolve_live_id` → kv `config.live_id` | 取房间信息 / 进房 |
        | `room_id`（真实房间号，如 `7688251038101556006`） | 进房页 `roomId`、`get_live_info()["room_id"]` | 直播域**写**接口 |

        `link_resolve.resolve_live_id` 的契约只保证返回 **web_rid**（其文档原文：
        「返回的 live_id 即为 web_rid（真实直播间号）」），而 `/webcast/room/chat/`
        与 `/webcast/room/like/` 要的 `room_id` 是**另一套值** —— 实测
        `follow/live/992931212705` 进房 `room_id=7688251038101556006`，二者逐字不同。
        ⇒ 直接拿 web_rid 调写接口 ⇒ 上游业务失败，且响应里没有可读错误码（静默失败）。

        ⇒ 本函数是**唯一**的写接口房间号归一化入口：能取到真实 `room_id` 就用，
        取不到**原样回退**（绝不编造、绝不抛错打断）。只读、幂等。
        """
        rid = str(room_id or "").strip()
        if not rid:
            return rid
        try:
            info = DouyinAPI.get_live_info(auth, rid)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[LIVE-038] [live-chat] 真实 room_id 探测失败（沿用原值 {rid}）: {e}")
            return rid
        real = ""
        if isinstance(info, dict):
            real = str(info.get("room_id") or "").strip()
        if real and real != rid:
            logger.debug(f"[live-chat] room_id 归一化：web_rid={rid} -> room_id={real}")
            return real
        if not real:
            logger.warning(f"[LIVE-039] [live-chat] 未取到真实 room_id（沿用 {rid}）——"
                           "直播域写接口可能因房间号形态不符而失败")
        return rid

    @staticmethod
    def diggLiveRoom(auth, room_id: str, count: str = '1'):
        """直播间点赞（`/webcast/room/like/`，POST form）。

        ## 对齐上游（2026-09-30）

        逐字段核对上游 `cv-cat/DouYin_Spider` **最新 head** `b17b12ee` 的
        `dy_apis/douyin_api.py:1897-1928`（与本地 vendor 快照逐字节相同 ⇒
        该接口自 09-19 起上游未再改动）。

        🔴 **差异（本项目修复）**：上游仍写 `headers.set_header("origin",
        DouyinAPI.douyin_url)`（= **主站** www.douyin.com）。而这个接口打的是
        `live.douyin.com` —— 同一份上游自己的 `sendMsgInRoom` 已把这一点写明：
        「直播域的 Origin 和 bd-ticket 证书也必须按 live.douyin.com 生成；
        沿用主站 Origin 会得到空响应或业务失败」。点赞与发弹幕**同属直播域写接口**，
        同一约束成立（ADR-004 §4「E 线同族残留」即登记本条）。

        ⇒ 本项目按直播域写接口的**统一形态**落地（与 `sendMsgInRoom` 一致）：
          ① `Origin` 用直播域；
          ② 补 `with_bd(api, auth, origin=live_url)` —— 写接口需要 bd-ticket 证书，
             此前完全缺失（发弹幕有、点赞没有 = 同族不一致）。
        真实投递验证 pending（写接口触风控红线，不在本批真发）。
        """
        # 2026-09-30：写接口房间号必须是**真实 room_id**，不是 URL 短号(web_rid)。
        room_id = DouyinAPI._live_chat_room_id(auth, room_id)
        api = "/webcast/room/like/"
        headers = HeaderBuilder().build(HeaderType.FORM)
        refer = f"{DouyinAPI.live_url}/{room_id}"
        headers.set_header("Origin", DouyinAPI.live_url)
        headers.with_bd(api, auth, origin=DouyinAPI.live_url)
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
        params.add_param("browser_name", get_profile()["browser_name"])
        params.add_param("browser_version", get_profile()["browser_version"])
        params.add_param("room_id", room_id)
        params.add_param("count", count)
        params.add_param("msToken", auth.msToken)
        data = {
        }
        params.with_a_bogus(data, host=LIVE_HOST)
        res = requests.post(f'{DouyinAPI.live_url}{api}', headers=headers.get(), params=params.get(),
                            cookies=auth.cookie, data=data, verify=tls_verify())
        return safe_json(res)

    @staticmethod
    def sendMsgInRoom(auth, room_id: str, content: str = '', **kwargs):
        """发送直播间弹幕（`/webcast/room/chat/`，GET）。

        ## 对齐上游（2026-09-23，T1/A1）

        逐字段对齐上游 `cv-cat/DouYin_Spider` commit `251075e`
        (`feat: align live room comment sending`, 2026-09-20 01:26)：

        - `Origin` 由**主站** `douyin_url` 改为**直播域** `live_url`。直播前端
          的 `Origin` 与 bd-ticket 证书都必须按 `live.douyin.com` 生成；沿用
          主站 `Origin` 会得到空响应或业务失败（上游注释原文）。
        - 新增 `**kwargs`：`referer` / `web_rid` / `enter_from` / `type`，以及
          7 个可选 query（`episode_info_str`/`flow_time`/`team_id`/`camera_id`/
          `emoji_id`/`rtf_content`/`paste_edit_method`，为空则不发送）。
        - `enter_from` 默认由 `'web_others_homepage'` 改为 `'link_share'`
          （上游取值，PC Web 直播实录）。
        - `room_id` / `type` 一律 `str()`。
        - `with_bd(api, auth, origin=DouyinAPI.live_url)`：⚠️ 本项目
          `builder/header.py: with_bd` **签名收 `origin` 但函数体从不使用它**
          （无 `ecdh_key(aid, origin)`），故此参数在当前实现下是**空操作**——
          照抄以保持上游形态一致，**不得**据此认为「证书已按直播域生成」
          （真正的差异在未移植的 `ecdh_key`，见 UP-L1 A2-6）。

        房间参数名仍是 `room_id`（值来自前端 `room_id_str`）；`web_rid` kwarg
        **只用于拼 referer**，不落进 query。

        ⚠️ 写接口（触风控红线）：本项目当前无生产调用方；真实投递验证 pending。
        """
        api = "/webcast/room/chat/"
        # 2026-09-30：写接口房间号必须是**真实 room_id**（URL 短号 web_rid 会被上游判无效）。
        real_room_id = DouyinAPI._live_chat_room_id(auth, room_id)
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = kwargs.get('referer') or f"{DouyinAPI.live_url}/{kwargs.get('web_rid', room_id)}"
        headers.set_header("Origin", DouyinAPI.live_url)
        headers.with_bd(api, auth, origin=DouyinAPI.live_url)
        headers.with_csrf(auth.cookie_str)
        headers.set_referer(refer)
        params = Params()
        params.add_param("aid", '6383')
        params.add_param("app_name", 'douyin_web')
        params.add_param("live_id", '1')
        params.add_param("device_platform", 'web')
        params.add_param("language", 'zh-CN')
        params.add_param("enter_from", kwargs.get('enter_from', 'link_share'))
        params.add_param("cookie_enabled", 'true')
        params.add_param("screen_width", '2560')
        params.add_param("screen_height", '1440')
        params.add_param("browser_language", 'zh-CN')
        params.add_param("browser_platform", 'Win32')
        params.add_param("browser_name", get_profile()["browser_name"])
        params.add_param("browser_version", get_profile()["browser_version"])
        params.add_param("room_id", str(real_room_id))
        params.add_param("content", content)
        params.add_param("type", str(kwargs.get('type', '0')))
        for key in ('episode_info_str', 'flow_time', 'team_id', 'camera_id',
                    'emoji_id', 'rtf_content', 'paste_edit_method'):
            value = kwargs.get(key)
            if value not in (None, ''):
                params.add_param(key, value)
        params.add_param("msToken", auth.msToken)
        params.with_a_bogus(host=LIVE_HOST)
        res = requests.get(f'{DouyinAPI.live_url}{api}', headers=headers.get(), params=params.get(),
                           cookies=auth.cookie, verify=tls_verify())
        return safe_json(res)

