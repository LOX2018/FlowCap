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
# 公共导入头（json/re/uuid/requests/BeautifulSoup/logger/protobuf/builder/utils）
# 见 dy_apis/_common.py —— 2026-09-15 共享提取，替代各域文件重复的 17 行导入头。
from dy_apis._common import *  # noqa: F401,F403



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
        """获取自己的 sec_uid。

        ## 2026-09-14 修复（实测驱动）

        **原缺陷**：原实现用**单一正则**从 `/user/self` 的 HTML 里抠 `secUid`，
        再直接 `[0]` 索引：
        ```python
        sec_uid = re.findall(r'\\"secUid\\":\\"(.*?)\\"', response.text)[0]
        ```
        页面未渲染 / 结构变化 / 命中风控页时 `findall` 返回空列表 →
        **`IndexError: list index out of range`**，且该异常在调用链上游被
        笼统 catch 成 502「获取自身 sec_uid 失败」——**前端只看到"请求失败"，
        真因被吞掉**（与本项目已修的几处"静默失败"同族）。

        **本次修法**（不新增请求，仅把"单点脆弱"改为"多点降级 + 显式失败"）：
          1. 多种常见写法逐一尝试（转义引号 / 未转义 / 单引号 / `sec_user_id` 键）；
          2. 校验形状（抖音 sec_uid 以 `MS4wLjABAAAA` 开头）——**避免抓到别的值**；
          3. 全部失败 → 抛**带诊断信息**的 RuntimeError（含 HTTP 码与响应长度），
             而非 `IndexError`，便于定位是"未登录"还是"页面结构变了"。
          4. 可选：调用方传入 `fallback`（如账号表已存的 sec_uid）时优先用它，
             完全避免请求（少一次请求也更安全）。

        :param auth: DouyinAuth object.
        :keyword fallback: 已知的 sec_uid（优先返回，不发请求）
        :return: sec_uid
        :raises RuntimeError: 所有途径均失败（**显式**，不再 IndexError）
        """
        fb = kwargs.get("fallback")
        if fb:
            return str(fb)

        # ══ 主路径（★ 2026-09-15 实测确定，照源项目接口）══
        # `/aweme/v1/web/user/profile/self/`（源项目逆向情报中确有此接口）
        # 实测：HTTP 200 / 17954 字节 / 返回 `user.sec_uid` + `user.uid` + `user.nickname`，
        #       uid 与 `get_my_uid`（query/user）实测值一致（316276709526638）⇒ 可信。
        # 相比 HTML 正则：结构化、稳定、一次请求即可。
        try:
            api_path = "/aweme/v1/web/user/profile/self/"
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
             .add_param("browser_name", get_profile()["browser_name"])
             .add_param("browser_version", get_profile()["browser_version"])
             .add_param("browser_online", "true")
             .add_param("engine_name", "Blink")
             .add_param("os_name", "Windows")
             .add_param("os_version", "10")
             .add_param("platform", "PC")
             .add_param("publish_video_strategy_type", "2"))
            params.with_web_id(auth, "https://www.douyin.com/user/self")
            params.with_a_bogus()
            hh = HeaderBuilder().build(HeaderType.GET)
            hh.set_referer("https://www.douyin.com/user/self")
            rr = requests.get(f"{DouyinAPI.domain_for(api_path)}{api_path}",
                              headers=hh.get(), cookies=auth.cookie,
                              params=params.get(), verify=False,
                              timeout=kwargs.get("timeout", 15))
            if rr.status_code == 200:
                js = rr.json()
                u = (js or {}).get("user") or {}
                sec = u.get("sec_uid") or ""
                if sec and sec.startswith("MS4wLjABAAAA"):
                    # 顺带把 uid/nickname 回写 auth（消费方可能依赖）
                    try:
                        if u.get("uid") and not getattr(auth, "uid", None):
                            auth.uid = int(u["uid"])
                        if u.get("nickname"):
                            auth.nickname = u["nickname"]
                    except Exception:  # noqa: BLE001
                        pass
                    return str(sec)
                logger.warning("SEC-UID-001", f"profile/self 返回异常：status_code={js.get('status_code')}")
            else:
                logger.warning("SEC-UID-002", f"profile/self HTTP {rr.status_code}")
        except Exception as e:  # noqa: BLE001
            logger.warning("SEC-UID-003", f"profile/self 取 sec_uid 失败，回落到 HTML：{type(e).__name__}")

        # ══ 回落：旧 HTML 正则路径（保留兼容，但不再直接 [0] 索引）══
        headers = HeaderBuilder().build(HeaderType.GET)
        url = "https://www.douyin.com/user/self"
        params_raw = {"from_tab_name": "main"}
        response = requests.get(url, headers=headers.get(),
                                cookies=auth.cookie, params=params_raw,
                                timeout=kwargs.get("timeout", 15), verify=False)
        text = response.text or ""

        # 多种常见写法逐一尝试（按命中概率排序）
        patterns = (
            r'\\"secUid\\":\\"(.*?)\\"',          # 转义双引号（旧版常见）
            r'"secUid":"(.*?)"',                      # 普通双引号
            r'secUid\\":\\"(.*?)\\"',
            r"'secUid':'(.*?)'",                      # 单引号
            r'sec_user_id\\":\\"(.*?)\\"',
            r'"sec_user_id":"(.*?)"',
        )
        for pat in patterns:
            for m in re.findall(pat, text):
                if m and m.startswith("MS4wLjABAAAA"):
                    return m

        # 兜底：任何形如 sec_uid 的串（放宽形状要求，但仍需合理长度）
        for pat in patterns:
            for m in re.findall(pat, text):
                if m and len(m) > 20:
                    return m

        raise RuntimeError(
            "sec_uid 提取失败：HTTP {} / 响应 {} 字节 / 命中风控页={}"
            .format(response.status_code, len(text),
                    "是" if ("verify" in text[:2000].lower() or "滑块" in text) else "否")
        )

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
        return safe_json(resp)

