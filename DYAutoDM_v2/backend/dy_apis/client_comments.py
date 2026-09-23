# -*- coding: utf-8 -*-
"""平台接口层 —— 评论域（mixin）

## 设计来源（照源项目 better-douyin）

对标 `src-tauri/src/api/client_comments.rs`（方案 B 逆向情报）。

## 迁移说明（本分支 design/better-douyin，阶段1）

本模块由 `douyin_api.py` 的 `DouyinAPI` **机械拆分**而来（ast 精确取体，
非重写）：方法体逐字节保持原样，仅换宿主。

**内部调用保持 `DouyinAPI.xxx` 形式不变**——最终类仍是 `DouyinAPI`
（由 `dy_apis/douyin_api.py` 组装全部 mixin），故 109 处内部引用与
300+ 处外部调用**零改动**。

## 本域方法

```
  get_work_out_comment
  get_work_all_out_comment
  get_work_inner_comment
  get_work_all_inner_comment
  get_work_all_comment
  publish_comment
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


class CommentsMixin:
    """评论域接口（来自 DouyinAPI）。"""

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
        params.add_param("round_trip_time", "0")
        params.with_web_id(auth, url)
        params.add_param("verifyFp", auth.cookie['s_v_web_id'])
        params.add_param("fp", auth.cookie['s_v_web_id'])
        params.add_param("msToken", auth.msToken)
        params.with_a_bogus()
        resp = requests.get(f'{DouyinAPI.douyin_url}{api}', headers=headers.get(), cookies=auth.cookie,
                            params=params.get(), verify=tls_verify())
        # 2026-09-17 修补（OCR 审查 HIGH —— 裸 json.loads 未走 safe_json）：
        # 抖音限流/风控时返回**空响应体**，`json.loads(resp.text)` 抛
        # JSONDecodeError 并被上层当 502。项目约定用 safe_json 优雅降级。
        return safe_json(resp)

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
            # 2026-09-17 修补（OCR 审查 HIGH —— 无守卫的下标访问）：
            # safe_json 在限流/空响应时降级为 `{}` → 原 `res_json["comments"]`
            # 会抛 KeyError（比 JSONDecodeError 更隐蔽）。改用 .get 并在
            # 拿不到批次时终止翻页（等价于"平台无更多数据"）。
            if not isinstance(res_json, dict):
                break
            comments = res_json.get("comments")
            if comments is None or len(comments) == 0:
                break
            cursor = str(res_json.get("cursor") or "")
            comment_list.extend(comments)
            if res_json.get("has_more") != 1:
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
        params.add_param("round_trip_time", "0")
        params.with_web_id(auth, refer)
        params.add_param("verifyFp", auth.cookie['s_v_web_id'])
        params.add_param("fp", auth.cookie['s_v_web_id'])
        params.add_param("msToken", auth.msToken)
        params.with_a_bogus()
        resp = requests.get(f'{DouyinAPI.douyin_url}{api}', headers=headers.get(), cookies=auth.cookie,
                            params=params.get(), verify=tls_verify())
        # 2026-09-17 修补（OCR 审查 HIGH）：裸 json.loads → safe_json。
        resp_json = safe_json(resp)
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
            # 2026-09-17 修补（OCR 审查 HIGH —— 无守卫的下标访问）：
            # 同 get_work_all_out_comment，safe_json 可能返回 `{}`。
            if not isinstance(res_json, dict):
                break
            comments = res_json.get("comments")
            cursor = str(res_json.get("cursor") or "")
            if type(comments) is list and len(comments) > 0:
                comment_list.extend(comments)
            if res_json.get("has_more") != 1:
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
    def publish_comment(auth, aweme_id: str, content: str = '', reply_id="", **kwargs):
        """发布作品评论 / 回复（`/aweme/v1/web/comment/publish`，POST form）。

        ## 对齐上游（2026-09-23，T2/A2）

        逐字段对齐上游 `cv-cat/DouYin_Spider` commit `df52357`
        (`fix: align work comment publishing`, 2026-09-20 01:33)：

        - `text_extra`：由裸 list `[]` 改为 **`JSON.stringify` 语义**（字符串）。
          本项目 `requests.post(..., data=data)` 走
          `urllib.parse.urlencode`，**空 list 会被整键丢弃**（[实测]），而
          `params.with_a_bogus(data)` 里的 `splice_url` 会把它拼成
          `text_extra=%5B%5D` ⇒ **签名输入 ≠ 上线字节**，是确定性缺陷。
          上游做法：`kwargs.get('text_extra', [])`，已是 str 则原样，否则
          `json.dumps(..., ensure_ascii=False, separators=(',', ':'))`。
          不传时默认 `[]` → `json.dumps` 得 `'[]'`，**与改造前语义等价**（零回归）。
        - `comment_send_celltime` / `comment_video_celltime`：**去随机、默认 0**。
          旧版 `random.randint(1000, 20000)` 是旧脚本遗留，服务端会把评论当成
          「播放器内操作」→ 偶发业务失败（上游注释原文）。
        - 新增 `reply_to_reply_id`（二级回复的回复对象）。
        - 新增 `one_level_comment_rank`(默认 -1) / `paste_edit_method`
          (默认 `'non_paste'`)，与上游逐字一致。

        ## 明确**不**照抄的上游半句（本项目能力缺口，硬约束）

        上游 `df52357` 同时新增了 `ticket_matches_session()` 与 `dtrait_*`
        两处**硬门禁**（不满足即 `raise RuntimeError`）。本项目 `DouyinAuth`
        这两组能力**均不存在**（[实测] `hasattr` 全 False，`.env.enc` 亦无
        `DY_DTRAIT_*` 键）⇒ 照抄会让本方法**每次必抛**（静态可证）。
        本项目既定定调是「能力缺失时降级」（见 `builder/header.py:34-38`），
        故此处维持**不硬抛**：若将来移植该门禁，须先补 auth 能力来源。

        ⚠️ 写接口（触风控红线）：本项目当前无生产调用方；真实投递验证 pending。
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
        }
        if reply_id != "":
            data["reply_id"] = reply_id
        reply_to_reply_id = kwargs.get('reply_to_reply_id', '')
        if reply_to_reply_id != "":
            data["reply_to_reply_id"] = reply_to_reply_id
        # 上游 df52357：PC Web 发送函数默认传 0；随机值会让服务端把评论当成
        # 播放器内操作，导致发布接口偶发业务失败。
        data["comment_send_celltime"] = kwargs.get('comment_send_celltime', 0)
        data["comment_video_celltime"] = kwargs.get('comment_video_celltime', 0)
        data["one_level_comment_rank"] = kwargs.get('one_level_comment_rank', -1)
        data["paste_edit_method"] = kwargs.get('paste_edit_method', "non_paste")
        data["text"] = content
        # 上游 df52357：前端发送 JSON.stringify(textExtra)。不能把 list 直接交给
        # requests —— data= 走 urlencode 时空 list 会被整键丢弃，非空 list 的 dict
        # 会被拆成子项，两种都与参与 a_bogus 的 body 对不上。
        text_extra = kwargs.get('text_extra', [])
        data["text_extra"] = (text_extra if isinstance(text_extra, str) else
                              json.dumps(text_extra, ensure_ascii=False,
                                         separators=(',', ':')))
        params.with_a_bogus(data)
        params.add_param("verifyFp", auth.cookie['s_v_web_id'])
        params.add_param("fp", auth.cookie['s_v_web_id'])
        res = requests.post(f'{DouyinAPI.douyin_url}{api}', headers=headers.get(), params=params.get(),
                            cookies=auth.cookie, data=data, verify=tls_verify())
        return safe_json(res)

