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

# ── 域模块（照源项目 api/client_*.rs 切分，本分支 design/better-douyin 阶段1）──
from dy_apis.client_user import UserMixin
from dy_apis._bindings import bind_all
from dy_apis.client_video import VideoMixin
from dy_apis.client_comments import CommentsMixin
from dy_apis.client_collection import CollectionMixin
from dy_apis.client_relations import RelationsMixin
from dy_apis.client_notice import NoticeMixin
from dy_apis.client_search import SearchMixin
from dy_apis.client_live import LiveMixin
from dy_apis.client_im import ImMixin


# =============================================================================
# DouyinAPI —— 组装门面（兼容层）
# =============================================================================
# 本类由 9 个域 mixin 组装（照源项目 `api/client_*.rs` 的按域切分）：
#   UserMixin       ← client_user.rs          VideoMixin      ← client_video.rs
#   CommentsMixin   ← client_comments.rs      CollectionMixin ← client_collection.rs
#   RelationsMixin  ← client_relations.rs     NoticeMixin     ← client_notice.rs
#   SearchMixin     ← client_feed.rs(搜索/流)  LiveMixin       ← client_feed.rs(直播)
#   ImMixin         ← client_im*.rs(4 个)
#
# **为何用 mixin 而非直接搬走**：原文件 109 处内部调用写作 `DouyinAPI.xxx(...)`；
# 拆成 mixin 后 `DouyinAPI` 仍是最终类，这些引用自然解析到 mixin 提供的方法
# ⇒ 内部调用与 300+ 处外部调用**全部零改动**（门面模式）。
#
# 类属性（域名 / UID 缓存）留在本类；mixin 内通过 `DouyinAPI.` 或 `self.` 访问。
# =============================================================================
class DouyinAPI(UserMixin, VideoMixin, CommentsMixin, CollectionMixin, RelationsMixin, NoticeMixin, SearchMixin, LiveMixin, ImMixin):
    douyin_url = 'https://www.douyin.com'
    live_url = 'https://live.douyin.com'
    creator = "https://creator.douyin.com"
    douyin_url_hj = 'https://www-hj.douyin.com'
    _HJ_PREFIXES = (
        '/aweme/v1/web/comment/list',        # 评论列表（含 reply）
        '/aweme/v1/web/aweme/listcollection',
        '/aweme/v1/web/aweme/favorite/',
        '/aweme/v1/web/mix/listcollection',
        '/aweme/v1/web/series/aweme/',
        '/aweme/v1/web/im/user/info/',
        '/aweme/v1/web/im/spotlight/relation/',
        '/aweme/v1/web/im/user/active/status/',
        '/aweme/v1/web/commit/item/digg/',
        '/aweme/v1/web/commit/follow/user/',
        '/aweme/v1/web/aweme/collect/',
    )
    @classmethod
    def domain_for(cls, path: str) -> str:
        """按路径返回应使用的域名（照源项目双域名策略）。

        Args:
            path: 接口路径，如 `/aweme/v1/web/comment/list/`

        Returns:
            完整域名（不含尾斜杠）
        """
        for pre in cls._HJ_PREFIXES:
            if path.startswith(pre):
                return cls.douyin_url_hj
        return cls.douyin_url
    UID_CACHE_TTL_SEC = 300
    UID_PROBE_TTL_OK = 300
    UID_PROBE_TTL_FAIL = 60
    _uid_probe_cache: dict = {}   # class attr: {sessionid_key: (ts, uid_or_None)}


# ── 组装完成 → 把最终类注入各域模块的全局名（使 109 处 `DouyinAPI.xxx` 生效）──
# 域模块内保留原样写法 `DouyinAPI.xxx(...)`；本调用让这些名字解析到最终类，
# 因此内部调用与 300+ 处外部调用**无需任何改动**（门面模式的关键一步）。
bind_all(DouyinAPI)


if __name__ == '__main__':
    web_protect_str = r''
    keys_str = r''
    cookies_str = ''



    from builder.auth import DouyinAuth
    auth_ = DouyinAuth()
    auth_.perepare_auth(cookies_str, web_protect_str, keys_str)

    live_url = "https://live.douyin.com/852953608964"
    live_id = "852953608964"
    res = DouyinAPI.get_live_info(auth_, live_id)
    print(res)

    room_id = res['room_id']
    anchor_id = res['anchor_id']
    sec_anchor_id = res['sec_uid']
    DouyinAPI.get_rank_list(auth_, room_id, anchor_id, sec_anchor_id)



    # res = DouyinAPI.search_live(auth_, "三角洲")
    # # print(res)
    # for i in res['data']:
    #     print(i['lives']['author']['nickname'])
    #     live_id = re.findall(r'"web_rid":"(.*?)",', str(i['lives']))[0]
    #     live_url = f'https://live.douyin.com/{live_id}'
    #     print(live_url)

    # my_uid = DouyinAPI.get_my_uid(auth_)
    # print(my_uid)
    # my_sec_uid = DouyinAPI.get_my_sec_uid(auth_)
    # print(my_sec_uid)
    # work_url = r'https://www.douyin.com/video/7433523124836060416'
    # print(DouyinAPI.get_user_info(auth_, "https://www.douyin.com/user/MS4wLjABAAAA7BDbZk0LjnEMcDDsLag5mDrMc157hD3x0SMhH1HaCM8"))
    # print(DouyinAPI.digg(auth_, "7433523124836060416", "1"))
    # print(DouyinAPI.digg(auth_, "7212619184386182435", "1"))
    # user_info = DouyinAPI.get_user_info(auth_, "https://www.douyin.com/user/MS4wLjABAAAAHXtdycTLMSe5Ld_468-9HKR1HUUrk4ywq-xMCM-E9w_cDIrhmynrQUalv061ZSpn?from_tab_name=main")
    # to_user_id = user_info['user']['uid']
    # conversation_id, conversation_short_id, ticket = DouyinAPI.create_conversation(auth_, to_user_id)
    # content = r'有份长期通告寻求合作，你通过了前期筛选，我是项目负责人，期待你与我联系：ncyj12'
    # DouyinAPI.send_msg(auth_, conversation_id, conversation_short_id, ticket, content)
    # print(DouyinAPI.get_user_all_work_info(auth_,"https://www.douyin.com/user/MS4wLjABAAAA8nC7nKxMrRtBwEqFzRgRBSxhBcw89VL0ysN-IXvhlKU?vid=7378825215213718818"))
    # print(DouyinAPI.get_work_info(auth_, "https://www.douyin.com/video/7212619184386182435"))
    # print(DouyinAPI.get_work_all_out_comment(auth_, "https://www.douyin.com/video/7212619184386182435"))
    # print(DouyinAPI.get_work_inner_comment(auth_, {
    #     "aweme_id": "7212619184386182435",
    #     "cid": "7327990109411902208"
    # }, "0"))
    # print(DouyinAPI.get_work_all_inner_comment(auth_, {
    #     "aweme_id": "7212619184386182435",
    #     "cid": "7327990109411902208"
    # }))
    # print(DouyinAPI.get_work_all_comment(auth_, "https://www.douyin.com/video/7212619184386182435"))
    # print(DouyinAPI.search_general_work(auth_, "美女", sort_type='2'))
    # print(DouyinAPI.search_some_general_work(auth_, "美女", sort_type='2', publish_time='0', num=30))
    # print(DouyinAPI.get_all_live_production(auth_, "https://live.douyin.com/84255891276"))
    # 60503986163 289606013148 91819894158
    # room_info = DouyinAPI.get_live_info(auth_, '60503986163')
    # print(room_info)
    # print(DouyinAPI.get_live_production(auth_, "https://live.douyin.com/84255891276", room_id, author_id, '0'))
    # print(DouyinAPI.collect_aweme(auth_, "7377676120549772554", '1'))
    # print(DouyinAPI.move_collect_aweme(auth_, "7207861673711930656", "tt", "7379252593215919891"))
    # print(DouyinAPI.remove_collect_aweme(auth_, "7376244589235113250", "tt", "7379252593215919891"))
    # print(DouyinAPI.get_live_production_detail(auth_, "https://live.douyin.com/552370739330", "3622058069401408240", "MS4wLjABAAAATfhR-kvE-AWqZaNaomCLFqgDKzvBwMS87FUGVjS_u7Y", "7379220637308504843"))
    # print(DouyinAPI.get_collect_list(auth_))
    # print(DouyinAPI.search_user(auth_, "巴旦木公主"))
    # print(DouyinAPI.search_some_user(auth_, "巴旦木公主", 30))
    # print(DouyinAPI.search_live(auth_, "馨馨baby😐ᵇᵃᵇʸ"))
    # print(DouyinAPI.get_user_favorite(auth_, "MS4wLjABAAAA99bTJ_GOw3odYmsXOe7i7xuEv0iQf2X_Kg_VUyVP0U8"))
    # print(DouyinAPI.get_some_user_follower_list(auth_, "3074704605975950", "MS4wLjABAAAA0L4jpkJDeuFO9AM-dQK1B649tmr7GIw-sQtyPasP_Z45QnUjIQgUOLIs8Kw8Gp-u", 40))
    # print(DouyinAPI.get_some_user_following_list(auth_, "3074704605975950", "MS4wLjABAAAA0L4jpkJDeuFO9AM-dQK1B649tmr7GIw-sQtyPasP_Z45QnUjIQgUOLIs8Kw8Gp-u", 40))
    # print(DouyinAPI.search_some_video_work(auth_, "巴旦木公主", 32))
    # print(DouyinAPI.get_feed(auth_))
    # print(DouyinAPI.publish_comment(auth_, "7356193166732709139"))
    # print(DouyinAPI.get_upload_auth_key(auth_))

    # while True:
    #     print(DouyinAPI.sendMsgInRoom(auth_, room_id, "666"))
    #     time.sleep(3)
    # #
    # while True:
    #     print(DouyinAPI.diggLiveRoom(auth_, room_id, '10'))
    #     time.sleep(1)
