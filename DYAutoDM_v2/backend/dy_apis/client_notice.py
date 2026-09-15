# -*- coding: utf-8 -*-
"""平台接口层 —— 通知域（mixin）

## 设计来源（照源项目 better-douyin）

对标 `src-tauri/src/api/client_notice.rs`（方案 B 逆向情报）。

## 迁移说明（本分支 design/better-douyin，阶段1）

本模块由 `douyin_api.py` 的 `DouyinAPI` **机械拆分**而来（ast 精确取体，
非重写）：方法体逐字节保持原样，仅换宿主。

**内部调用保持 `DouyinAPI.xxx` 形式不变**——最终类仍是 `DouyinAPI`
（由 `dy_apis/douyin_api.py` 组装全部 mixin），故 109 处内部引用与
300+ 处外部调用**零改动**。

## 本域方法

```
  get_notice_list
  get_some_notice_list
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


class NoticeMixin:
    """通知域接口（来自 DouyinAPI）。"""

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

