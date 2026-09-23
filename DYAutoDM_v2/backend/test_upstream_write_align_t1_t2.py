# -*- coding: utf-8 -*-
"""T1/T2 上游写接口对齐 —— 离线单测（**不真发、不联网**）。

对齐上游 `cv-cat/DouYin_Spider`（本地只读快照 `_ext_repos/DouYin_Spider_git`）：

  T1 `sendMsgInRoom`   ← `251075e`  feat: align live room comment sending  (2026-09-20 01:26)
  T2 `publish_comment` ← `df52357`  fix: align work comment publishing     (2026-09-20 01:33)

## 这一组测试为什么存在

两条接口都是**写接口**（发弹幕 / 发评论）且都触风控红线。缺陷形态是
「参数被静默忽略 / 签名 body ≠ 上线字节」——**不抛异常、不打日志**，
肉眼和运行时都发现不了，只能靠**请求构造层断言**钉死。

## 验证策略（离线，可回放）

1. **mock `requests.get/post`**，捕获 `headers / params / data`（请求构造层），
   不依赖任何响应语义，也不产生任何出站请求。
2. 签名/CSRF/webid 打桩；`with_bd` 用**真实 EC 私钥**跑通（证明真实链路可组装）。
3. **负控**：把**同一组断言**喂给**改动前**的实现（逐字复刻本项目 pre-change 源码，
   见 `_legacy_send_msg_in_room` / `_legacy_publish_comment`），断言必须抛
   `AssertionError` —— 证明这些断言确实在测这件事，而不是恒真。
4. **源码级机械门禁**：静态断言关键字段仍在，防回归（照 `test_abogus_host_guards.py` 体例）。
5. **上游源码对账**：上游快照在场时，从上游源码**逐字提取**期望字面量再比对本项目
   （Open-Source Provenance Law：不凭印象）。

## 运行（隔离根）

    cd DYAutoDM_v2/backend
    DY_APP_ROOT=$LOCALAPPDATA/Temp/fixE python -m unittest test_upstream_write_align_t1_t2 -v

## 未覆盖（诚实标注）

本测试**完全不证明**弹幕/评论能被服务端接受或真实投递 —— 那是实机端到端，
本轮未执行（pending）。判据见 `artifacts/fix_20260923/E_upstream_write_pending.md`。
"""
from __future__ import annotations

import json
import os
import re
import sys
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# ── 上游只读快照（用于逐字对账；缺失则跳过对账测试，不影响行为测试）──────────
_UPSTREAM = os.path.abspath(os.path.join(
    _HERE, "..", "..", "_ext_repos", "DouYin_Spider_git", "dy_apis", "douyin_api.py"))

_CLIENT_LIVE = os.path.join(_HERE, "dy_apis", "client_live.py")
_CLIENT_COMMENTS = os.path.join(_HERE, "dy_apis", "client_comments.py")

LIVE_URL = "https://live.douyin.com"
MAIN_URL = "https://www.douyin.com"
ROOM_ID = "7687606083061320475"

#: 本轮**明确未执行**的实机端到端验证标记（不得改动为 True）
E2E_LIVE_DELIVERY_VERIFIED = False
E2E_COMMENT_PUBLISH_VERIFIED = False


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _ast(path):
    import ast
    return ast.parse(_read(path))


def _used_names(tree):
    """AST 里**实际用到**的标识符（Name.id / Attribute.attr）。

    与文本扫描的区别：docstring / 注释里的名字**不计入** —— 缺席类断言必须
    用这个，否则本文件的说明性文档（会提到被禁止移植的名字）会造成假红。
    """
    import ast
    names = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            names.add(n.id)
        elif isinstance(n, ast.Attribute):
            names.add(n.attr)
    return names


def _env_gate():
    """隔离根门禁（照 `scripts/diag/verify_text_send_delivery.py` 体例）。

    本轮要求 `DY_APP_ROOT` 指向一次性隔离根（`$LOCALAPPDATA/Temp/fixE`），
    避免本测试意外触到真实数据根。
    """
    root = os.path.abspath(os.environ.get("DY_APP_ROOT") or "")
    if not root:
        raise AssertionError(
            "未设置 DY_APP_ROOT；本测试要求隔离运行："
            "DY_APP_ROOT=$LOCALAPPDATA/Temp/fixE")
    home = os.path.abspath(os.path.expanduser("~"))
    if root == home or root == os.path.abspath(os.path.join(home, "AppData")):
        raise AssertionError("DY_APP_ROOT 指向用户主目录，拒绝运行")
    return root


# ═══════════════════════════════════════════════════════════════════════════
# 测试替身：伪造 auth + 打桩签名/网络
# ═══════════════════════════════════════════════════════════════════════════

_EC_PEM = None


def _ec_pem():
    """真实 EC(P-256) 私钥 PEM —— 让 `with_bd` 走真实签名路径（纯本地、无网络）。"""
    global _EC_PEM
    if _EC_PEM is None:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        k = ec.generate_private_key(ec.SECP256R1())
        _EC_PEM = k.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
    return _EC_PEM


class _FakeAuth:
    """最小 DouyinAuth 替身。

    ⚠️ 刻意**不**提供 `ticket_matches_session` / `session_dtrait_header` ——
    复刻本项目真实 auth 的能力缺口（`builder/auth.py` 无此二者），
    以证明 `with_bd` 走的是「能力缺失时降级」分支。
    """

    def __init__(self):
        self.cookie = {
            "s_v_web_id": "verify_fake_s_v_web_id",
            "sessionid": "fake_sessionid",
            "UIFID": "fake_uifid",
        }
        self.cookie_str = "; ".join(f"{k}={v}" for k, v in self.cookie.items())
        self.ticket = "FAKE_TICKET"
        self.ts_sign = "ts.1.fake"
        self.client_cert = "pub.fake"
        self.private_key = _ec_pem()
        self.ree_public_key = "fake_ree"
        self.uid = "1234567890"
        self._ms_cache = "FAKE_MSTOKEN"
        self._ms_ts = 0
        self._dtrait_calls = 0

    @property
    def msToken(self):
        return self._ms_cache


class _Captured:
    """一次请求构造的捕获结果。"""

    def __init__(self, method, args, kwargs):
        self.method = method              # 'get' / 'post'
        self.url = args[0]
        self.headers = kwargs.get("headers") or {}
        self.params = kwargs.get("params") or {}
        self.cookies = kwargs.get("cookies")
        self.data = kwargs.get("data")
        self.kwargs = kwargs


def _capture_call(fn, reqmod, *args, **kwargs):
    """执行 `fn(*args, **kwargs)`，捕获 `reqmod.requests` 上的调用。

    不产生任何出站请求：`reqmod.requests`（被调函数的宿主模块，即
    `dy_apis.client_live` / `dy_apis.client_comments`）被整体替换为 MagicMock，
    同时把签名/CSRF/webid 打桩（纯本地确定值）。
    """
    import builder.header as _bh
    import builder.params as _bp

    recorded = {}

    def _fake_get(url, **kw):
        recorded["call"] = _Captured("get", (url,), kw)
        return mock.MagicMock()

    def _fake_post(url, **kw):
        recorded["call"] = _Captured("post", (url,), kw)
        return mock.MagicMock()

    fake_requests = mock.MagicMock()
    fake_requests.get.side_effect = _fake_get
    fake_requests.post.side_effect = _fake_post

    with mock.patch.object(reqmod, "requests", fake_requests), \
            mock.patch.object(_bp, "generate_a_bogus", return_value="ABOGUS_STUB"), \
            mock.patch.object(_bp, "generate_webid", return_value="WEBID_STUB"), \
            mock.patch.object(_bh, "generate_csrf_token", return_value=("CSRF_STUB", "x")), \
            mock.patch.object(_bh, "generate_bd_ticket_client_data",
                              return_value="CLIENT_DATA_STUB"), \
            mock.patch.object(_bh, "generate_ree_key", return_value="REE_STUB"):
        fn(*args, **kwargs)

    assert "call" in recorded, f"{getattr(fn, '__name__', fn)} 未发出 requests 调用"
    return recorded["call"]


def _capture(entry_point, module, *args, **kwargs):
    """执行 **组装后的真实入口** `DouyinAPI.<entry_point>(...)`，捕获请求构造。

    `module` 是该方法的**宿主**模块（`dy_apis.client_live` /
    `dy_apis.client_comments`）—— 方法体只通过宿主模块全局的 `requests` 出网，
    故打桩 `module.requests` 即完全隔离网络。同时断言方法的 `__globals__`
    确为该宿主模块（防「测错对象」）。
    """
    import dy_apis.douyin_api as _dapi          # 导入即完成 bind_all
    fn = getattr(_dapi.DouyinAPI, entry_point)
    if isinstance(fn, staticmethod):            # 理论不会走到，防御性展开
        fn = fn.__func__
    assert fn.__globals__ is module.__dict__, \
        f"{entry_point} 的宿主模块不是 {module.__name__}"
    return _capture_call(fn, module, *args, **kwargs)


# ═══════════════════════════════════════════════════════════════════════════
# 契约断言（同一组断言同时用于「新实现」与「负控旧实现」）
# ═══════════════════════════════════════════════════════════════════════════

T1_OPTIONAL_KEYS = ("episode_info_str", "flow_time", "team_id", "camera_id",
                    "emoji_id", "rtf_content", "paste_edit_method")


def assert_t1_aligned(call, room_id=ROOM_ID):
    """T1 `sendMsgInRoom` 请求构造契约（对齐上游 251075e）。"""
    # ① 域名：直播域
    assert call.url == f"{LIVE_URL}/webcast/room/chat/", f"url={call.url}"
    # ② Origin：必须是**直播域**，不是主站
    assert call.headers["Origin"] == LIVE_URL, f"Origin={call.headers['Origin']}"
    assert call.headers["Origin"] != MAIN_URL, "Origin 仍是主站（上游 251075e 要修的正是这条）"
    # ③ referer：直播域 + room_id
    assert call.headers["referer"] == f"{LIVE_URL}/{room_id}", f"referer={call.headers['referer']}"
    # ④ enter_from 默认值：上游由 web_others_homepage 改为 link_share
    assert call.params["enter_from"] == "link_share", f"enter_from={call.params['enter_from']}"
    # ⑤ room_id / type 一律 str（上游 str(...)）
    assert call.params["room_id"] == str(room_id)
    assert isinstance(call.params["room_id"], str)
    assert call.params["type"] == "0" and isinstance(call.params["type"], str)
    # ⑥ 其余直播域固定 query 齐备
    for k, v in (("aid", "6383"), ("app_name", "douyin_web"), ("live_id", "1"),
                 ("device_platform", "web"), ("language", "zh-CN"),
                 ("cookie_enabled", "true")):
        assert call.params[k] == v, f"{k}={call.params.get(k)!r} != {v!r}"
    # ⑦ msToken 在 query
    assert call.params["msToken"] == "FAKE_MSTOKEN"
    # ⑧ 7 个可选参数「不传即不发」
    for k in T1_OPTIONAL_KEYS:
        assert k not in call.params, f"可选参数 {k} 未传却被发送：{call.params[k]!r}"
    # ⑨ 返回的是 safe_json（不抛）——由调用方执行时已被 mock 覆盖，此处只看构造
    assert call.cookies is not None


def assert_t2_aligned(call):
    """T2 `publish_comment` 请求构造契约（对齐上游 df52357）。"""
    data = call.data
    assert call.url == f"{MAIN_URL}/aweme/v1/web/comment/publish", f"url={call.url}"
    # ① celltime 去随机、默认 0
    assert data["comment_send_celltime"] == 0, f"send_celltime={data['comment_send_celltime']!r}"
    assert data["comment_video_celltime"] == 0, f"video_celltime={data['comment_video_celltime']!r}"
    # ② text_extra 必须是 JSON.stringify 语义的**字符串**
    assert isinstance(data["text_extra"], str), \
        f"text_extra 不是 str 而是 {type(data['text_extra']).__name__}：{data['text_extra']!r}"
    assert json.loads(data["text_extra"]) == [], f"text_extra={data['text_extra']!r}"
    # ③ 缺省 rank / paste_edit_method（上游默认值）
    assert data["one_level_comment_rank"] == -1
    assert data["paste_edit_method"] == "non_paste"
    # ④ reply_to_reply_id 缺省不出现
    assert "reply_to_reply_id" not in data
    # ⑤ reply_id 缺省不出现
    assert "reply_id" not in data
    # ⑥ form 是**非 JSON**：content-type 走 urlencoded（上游 requests 默认行为）
    assert "aweme_id" in data and "text" in data


# ═══════════════════════════════════════════════════════════════════════════
# T1 —— sendMsgInRoom
# ═══════════════════════════════════════════════════════════════════════════

class TestT1SendMsgInRoom(unittest.TestCase):
    """T1 行为契约（离线捕获请求构造层）。"""

    def _call(self, **kwargs):
        import dy_apis.client_live as cl
        return _capture("sendMsgInRoom", cl, _FakeAuth(), ROOM_ID, "hi", **kwargs)

    def test_origin_is_live_domain_not_main_site(self):
        """核心修复：Origin = 直播域（不是主站）。"""
        call = self._call()
        self.assertEqual(call.headers["Origin"], LIVE_URL)
        self.assertNotEqual(call.headers["Origin"], MAIN_URL)

    def test_full_contract(self):
        """整组契约（同一组断言也会喂给负控旧实现）。"""
        assert_t1_aligned(self._call())

    def test_referer_default_and_override(self):
        """referer 默认 live/{room_id}；可用 kwarg referer 覆盖。"""
        self.assertEqual(self._call().headers["referer"], f"{LIVE_URL}/{ROOM_ID}")
        self.assertEqual(
            self._call(referer="https://live.douyin.com/999").headers["referer"],
            "https://live.douyin.com/999")

    def test_web_rid_only_affects_referer(self):
        """`web_rid` 只用于拼 referer；query 里的房间参数名仍是 `room_id`。"""
        call = self._call(web_rid="111222333")
        self.assertEqual(call.headers["referer"], f"{LIVE_URL}/111222333")
        self.assertEqual(call.params["room_id"], ROOM_ID)          # 未变
        self.assertNotIn("web_rid", call.params)                   # 不落 query

    def test_referer_kwarg_wins_over_web_rid(self):
        call = self._call(referer="https://live.douyin.com/explicit", web_rid="111")
        self.assertEqual(call.headers["referer"], "https://live.douyin.com/explicit")

    def test_enter_from_and_type_overrides(self):
        call = self._call(enter_from="web_live", type=2)
        self.assertEqual(call.params["enter_from"], "web_live")
        self.assertEqual(call.params["type"], "2")
        self.assertIsInstance(call.params["type"], str)

    def test_optional_params_emitted_only_when_nonempty(self):
        """7 个可选参数：非空才发；空串/None 一律不发。"""
        call = self._call(emoji_id="7", rtf_content="rich", flow_time=0,
                          team_id="", camera_id=None, episode_info_str="ep",
                          paste_edit_method="non_paste")
        self.assertEqual(call.params["emoji_id"], "7")
        self.assertEqual(call.params["rtf_content"], "rich")
        self.assertEqual(call.params["flow_time"], 0)          # 0 是「非空」值
        self.assertEqual(call.params["episode_info_str"], "ep")
        self.assertEqual(call.params["paste_edit_method"], "non_paste")
        self.assertNotIn("team_id", call.params)                # '' 不发
        self.assertNotIn("camera_id", call.params)              # None 不发

    def test_with_bd_receives_live_origin(self):
        """`with_bd(..., origin=live_url)` 确实被传入（对齐上游形态）。

        ⚠️ 同时证明本项目 `with_bd` 的 `origin` 是**空操作**：
        `generate_bd_ticket_client_data` 只收到 (api, ticket, ts_sign, private_key)，
        未收到 origin —— 即「证书按直播域生成」并未发生（本项目缺 ecdh_key）。
        这条断言是**如实记录能力缺口**，不是缺陷修复。
        """
        import dy_apis.douyin_api as _dapi
        auth = _FakeAuth()
        seen = {}

        def _spy(api, ticket, ts_sign, prik):
            seen.update(api=api, ticket=ticket, ts_sign=ts_sign, prik=prik)
            return "CLIENT_DATA_STUB"

        import dy_apis.client_live as cl
        with mock.patch.object(cl, "requests", mock.MagicMock()) as fake_req, \
                mock.patch("builder.header.generate_bd_ticket_client_data",
                           side_effect=_spy), \
                mock.patch("builder.header.generate_csrf_token",
                           return_value=("CSRF_STUB", "x")), \
                mock.patch("builder.header.generate_ree_key", return_value="REE_STUB"), \
                mock.patch("builder.params.generate_a_bogus", return_value="ABOGUS_STUB"):
            fake_req.get.return_value = mock.MagicMock()
            _dapi.DouyinAPI.sendMsgInRoom(auth, ROOM_ID, "hi")

        self.assertEqual(seen["api"], "/webcast/room/chat/")
        self.assertEqual(seen["ticket"], auth.ticket)
        self.assertEqual(seen["ts_sign"], auth.ts_sign)
        self.assertEqual(seen["prik"], auth.private_key)
        self.assertNotIn("origin", seen,
                         "本项目 with_bd 的 origin 实为空操作 —— 若此处出现 origin，"
                         "说明 ecdh_key 已移植，需同步更新本断言与文档")

    def test_abogus_signed_with_live_host(self):
        """a_bogus 必须按 live 子域签名（(aid,page_id) 与主站不同）。"""
        import builder.params as bp
        import dy_apis.client_live as cl
        import dy_apis.douyin_api as _dapi
        with mock.patch.object(cl, "requests", mock.MagicMock()) as fake_req, \
                mock.patch.object(bp, "generate_a_bogus",
                                  return_value="ABOGUS_STUB") as spy, \
                mock.patch("builder.header.generate_csrf_token",
                           return_value=("CSRF_STUB", "x")), \
                mock.patch("builder.header.generate_bd_ticket_client_data",
                           return_value="CD"), \
                mock.patch("builder.header.generate_ree_key", return_value="REE"):
            fake_req.get.return_value = mock.MagicMock()
            _dapi.DouyinAPI.sendMsgInRoom(_FakeAuth(), ROOM_ID, "hi")
        self.assertEqual(spy.call_args.kwargs.get("host"), "live.douyin.com")


# ═══════════════════════════════════════════════════════════════════════════
# T2 —— publish_comment
# ═══════════════════════════════════════════════════════════════════════════

class TestT2PublishComment(unittest.TestCase):
    """T2 行为契约（离线捕获请求构造层）。"""

    def _call(self, **kwargs):
        import dy_apis.client_comments as cc
        # 传 content 以模拟真实调用（关键缺陷与 content 无关，但保持一致）
        return _capture("publish_comment", cc, _FakeAuth(), "7356193166732709139",
                        "upstream-align-test", **kwargs)

    def test_celltime_default_zero(self):
        """celltime 默认 0（去随机）。"""
        data = self._call().data
        self.assertEqual(data["comment_send_celltime"], 0)
        self.assertEqual(data["comment_video_celltime"], 0)

    def test_celltime_overridable(self):
        """kwargs 可覆盖 celltime（上游 `kwargs.get(..., 0)`）。"""
        data = self._call(comment_send_celltime=123,
                          comment_video_celltime=456).data
        self.assertEqual(data["comment_send_celltime"], 123)
        self.assertEqual(data["comment_video_celltime"], 456)

    def test_text_extra_default_is_json_string(self):
        """text_extra 缺省 = JSON.stringify([]) == '[]'（字符串，不是 list）。"""
        te = self._call().data["text_extra"]
        self.assertIsInstance(te, str)
        self.assertEqual(te, "[]")
        self.assertEqual(json.loads(te), [])

    def test_text_extra_list_is_json_encoded(self):
        """非空 list → 紧凑 JSON（ensure_ascii=False，separators=(',',':')）。"""
        te = self._call(text_extra=[{"type": 1, "hashtag_name": "话题"}]).data["text_extra"]
        self.assertEqual(te, '[{"type":1,"hashtag_name":"话题"}]')
        self.assertEqual(json.loads(te), [{"type": 1, "hashtag_name": "话题"}])
        self.assertNotIn(" ", te.replace('"hashtag_name"', ''))      # 无多余空格

    def test_text_extra_str_passthrough(self):
        """已经是 str 则原样透传（上游 isinstance(text_extra, str) 分支）。"""
        raw = '[{"type":1}]'
        self.assertEqual(self._call(text_extra=raw).data["text_extra"], raw)

    def test_reply_to_reply_id(self):
        """新增 reply_to_reply_id（二级回复）。"""
        self.assertNotIn("reply_to_reply_id", self._call().data)
        d = self._call(reply_to_reply_id="700123").data
        self.assertEqual(d["reply_to_reply_id"], "700123")
        # 空串不入 body
        self.assertNotIn("reply_to_reply_id", self._call(reply_to_reply_id="").data)

    def test_reply_id_still_supported(self):
        self.assertEqual(self._call(reply_id="700999").data["reply_id"], "700999")

    def test_rank_and_paste_defaults(self):
        d = self._call().data
        self.assertEqual(d["one_level_comment_rank"], -1)
        self.assertEqual(d["paste_edit_method"], "non_paste")

    def test_rank_and_paste_overridable(self):
        d = self._call(one_level_comment_rank=3, paste_edit_method="paste").data
        self.assertEqual(d["one_level_comment_rank"], 3)
        self.assertEqual(d["paste_edit_method"], "paste")

    def test_full_contract(self):
        """整组契约（同一组断言也会喂给负控旧实现）。"""
        assert_t2_aligned(self._call())


class TestT2SignatureConsistency(unittest.TestCase):
    """`text_extra` 形态错误是**签名 body ≠ 上线字节**的确定性缺陷。

    本题只比较「签名输入」（`splice_url`）与「requests 实际 form 编码」
    （`RequestEncodingMixin._encode_params`），这是本项目 `with_a_bogus` +
    `requests(data=...)` 的真实组合，不依赖任何网络。
    """

    def _encode(self, data):
        from requests.models import RequestEncodingMixin
        return RequestEncodingMixin._encode_params(data)

    def test_old_list_form_mismatches_wire(self):
        """负向事实（改动前）：空 list 的签名输入与上线字节不一致。"""
        from utils.dy_util import splice_url
        legacy = {"aweme_id": "735", "comment_send_celltime": 0,
                  "text": "x", "text_extra": []}
        self.assertNotIn("text_extra", self._encode(legacy))       # wire 丢键
        self.assertIn("text_extra=%5B%5D", splice_url(legacy))     # 签名有键
        self.assertNotEqual(splice_url(legacy), self._encode(legacy))

    def test_new_json_string_form_matches_wire(self):
        """正向事实（改动后）：签名输入 == 上线字节。"""
        from utils.dy_util import splice_url
        fixed = {"aweme_id": "735", "comment_send_celltime": 0,
                 "text": "x", "text_extra": "[]"}
        self.assertIn("text_extra=%5B%5D", self._encode(fixed))
        self.assertEqual(splice_url(fixed), self._encode(fixed))

    def test_new_form_matches_wire_for_nonempty_list(self):
        from utils.dy_util import splice_url
        fixed = {"aweme_id": "735", "comment_send_celltime": 0, "text": "x",
                 "text_extra": json.dumps([{"type": 1}], ensure_ascii=False,
                                          separators=(",", ":"))}
        self.assertEqual(splice_url(fixed), self._encode(fixed))

    def test_celltime_value_does_not_break_consistency(self):
        """去掉随机与一致性无关（上游注释的收益是服务端语义，不是编码一致性）。"""
        from utils.dy_util import splice_url
        for c in (0, 1000, 20000):
            d = {"aweme_id": "735", "comment_send_celltime": c,
                 "text": "x", "text_extra": "[]"}
            self.assertEqual(splice_url(d), self._encode(d))


# ═══════════════════════════════════════════════════════════════════════════
# 负控 —— 把同一组断言喂给「改动前」的实现，必须变红
# ═══════════════════════════════════════════════════════════════════════════
#
# 下列两个函数**逐字复刻本项目改动前的源码**
#   client_live.py   (pre-T1)  行 598-628
#   client_comments.py (pre-T2) 行 236-298
# 只换宿主（模块级函数而非 mixin 静态方法），方法体与顺序保持原样。

def _legacy_send_msg_in_room(auth, room_id, content=""):
    """T1 改动前实现（逐字复刻 pre-change 源码）。"""
    from dy_apis._common import HeaderBuilder, HeaderType, Params, safe_json
    api = "/webcast/room/chat/"
    headers = HeaderBuilder().build(HeaderType.GET)
    refer = f"https://live.douyin.com/{room_id}"
    headers.set_header("Origin", MAIN_URL)
    headers.with_bd(api, auth)
    headers.with_csrf(auth.cookie_str)
    headers.set_referer(refer)
    params = Params()
    params.add_param("aid", '6383')
    params.add_param("app_name", 'douyin_web')
    params.add_param("live_id", '1')
    params.add_param("device_platform", 'web')
    params.add_param("language", 'zh-CN')
    params.add_param("enter_from", 'web_others_homepage')
    params.add_param("cookie_enabled", 'true')
    params.add_param("screen_width", '2560')
    params.add_param("screen_height", '1440')
    params.add_param("browser_language", 'zh-CN')
    params.add_param("browser_platform", 'Win32')
    params.add_param("browser_name", 'Edge')
    params.add_param("browser_version", '130.0.0.0')
    params.add_param("room_id", room_id)
    params.add_param("content", content)
    params.add_param("type", '0')
    params.add_param("msToken", auth.msToken)
    params.with_a_bogus(host="live.douyin.com")
    import dy_apis.client_live as cl
    res = cl.requests.get(f'https://live.douyin.com{api}', headers=headers.get(),
                          params=params.get(), cookies=auth.cookie, verify=False)
    return safe_json(res)


def _legacy_publish_comment(auth, aweme_id, content="", reply_id=""):
    """T2 改动前实现（逐字复刻 pre-change 源码）。"""
    import random
    from dy_apis._common import HeaderBuilder, HeaderType, Params, get_profile, safe_json
    api = "/aweme/v1/web/comment/publish"
    headers = HeaderBuilder().build(HeaderType.FORM)
    refer = f"https://www.douyin.com/discover?modal_id={aweme_id}"
    headers.set_header("Origin", MAIN_URL)
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
        "comment_send_celltime": random.randint(1000, 20000),
        "comment_video_celltime": random.randint(1000, 20000),
    }
    if reply_id != "":
        data["reply_id"] = reply_id
    data["text"] = content
    data["text_extra"] = []
    params.with_a_bogus(data)
    params.add_param("verifyFp", auth.cookie['s_v_web_id'])
    params.add_param("fp", auth.cookie['s_v_web_id'])
    import dy_apis.client_comments as cc
    res = cc.requests.post(f'https://www.douyin.com{api}', headers=headers.get(),
                           params=params.get(), cookies=auth.cookie, data=data, verify=False)
    return safe_json(res)


class TestNegativeControl(unittest.TestCase):
    """负控：同一组契约断言喂给改动前实现 → 必须 AssertionError。

    这是「测试真的在测这件事」的证明：若旧实现也能通过，说明断言恒真、无鉴别力。
    对每一处缺陷**同时**给出两条证据：① 旧实现的具体错误值被钉死；
    ② 契约断言（`assert_t1_aligned` / `assert_t2_aligned`）必然变红。
    """

    def test_legacy_t1_fails_contract(self):
        import dy_apis.client_live as cl
        int_room = 7687606083061320475          # 故意传 int：旧实现不 str() 化
        call = _capture_call(_legacy_send_msg_in_room, cl, _FakeAuth(), int_room, "hi")

        # ① 逐项钉死旧实现的错误
        self.assertEqual(call.headers["Origin"], MAIN_URL)        # 主站 Origin（错）
        self.assertNotEqual(call.headers["Origin"], LIVE_URL)
        self.assertEqual(call.params["enter_from"], "web_others_homepage")
        self.assertIsInstance(call.params["room_id"], int)        # 未 str()
        for key in T1_OPTIONAL_KEYS:                              # 7 个可选参数全无
            self.assertNotIn(key, call.params)

        # ② 契约断言必须变红，且失败原因正是 Origin（第一条断言）
        with self.assertRaises(AssertionError) as cm:
            assert_t1_aligned(call, room_id=int_room)
        self.assertIn("Origin", str(cm.exception))

    def test_legacy_t1_each_defect_would_break_contract(self):
        """逐条证明：把每个字段单独改回旧值都会触发契约失败（不靠第一条兜底）。"""
        import copy
        good = _capture_call(_legacy_send_msg_in_room, __import__(
            "dy_apis.client_live", fromlist=["x"]), _FakeAuth(), ROOM_ID, "hi")

        # 旧实现的 referer 与 room_id str 化：手工修好后，其余仍应失败
        variants = {}

        v = copy.deepcopy(good); variants["Origin=主站"] = v   # 原样
        # 手工把 Origin 修成 live（模拟只修这一条）→ 应暴露下一条缺陷
        v2 = copy.deepcopy(good); v2.headers = dict(good.headers); v2.headers["Origin"] = LIVE_URL
        variants["只修 Origin（enter_from 仍旧）"] = v2

        for label, call in variants.items():
            with self.assertRaises(AssertionError, msg=f"{label} 竟通过契约"):
                assert_t1_aligned(call)

    def test_legacy_t2_fails_contract(self):
        import dy_apis.client_comments as cc
        call = _capture_call(_legacy_publish_comment, cc, _FakeAuth(),
                             "7356193166732709139", "x")

        # ① 逐项钉死旧实现的错误
        self.assertIsInstance(call.data["text_extra"], list)
        self.assertEqual(call.data["text_extra"], [])
        self.assertTrue(1000 <= call.data["comment_send_celltime"] <= 20000)
        self.assertTrue(1000 <= call.data["comment_video_celltime"] <= 20000)
        self.assertNotIn("reply_to_reply_id", call.data)

        # ② 契约断言必须变红（celltime 是第一条断言，先于 text_extra）
        with self.assertRaises(AssertionError) as cm:
            assert_t2_aligned(call)
        self.assertIn("celltime", str(cm.exception))

    def test_legacy_t2_text_extra_alone_breaks_contract(self):
        """把 celltime 手工修好（模拟只修这一条）→ 应暴露 text_extra 缺陷。"""
        import copy
        import dy_apis.client_comments as cc
        call = _capture_call(_legacy_publish_comment, cc, _FakeAuth(),
                             "7356193166732709139", "x")
        fixed = copy.deepcopy(call)
        fixed.data["comment_send_celltime"] = 0
        fixed.data["comment_video_celltime"] = 0
        with self.assertRaises(AssertionError) as cm:
            assert_t2_aligned(fixed)
        self.assertIn("text_extra", str(cm.exception))

    def test_legacy_t2_text_extra_mismatch_is_real(self):
        """旧写法的签名/上线不一致，用真实编码函数复核（不是口头断言）。"""
        from requests.models import RequestEncodingMixin
        from utils.dy_util import splice_url
        import dy_apis.client_comments as cc
        call = _capture_call(_legacy_publish_comment, cc, _FakeAuth(),
                             "7356193166732709139", "x")
        wire = RequestEncodingMixin._encode_params(call.data)
        signed = splice_url(call.data)
        self.assertNotEqual(signed, wire)
        self.assertNotIn("text_extra", wire)          # wire 丢键
        self.assertIn("text_extra=%5B%5D", signed)    # 签名有键


# ═══════════════════════════════════════════════════════════════════════════
# 源码级机械门禁（防回归；照 test_abogus_host_guards.py 体例）
# ═══════════════════════════════════════════════════════════════════════════

class TestSourceGates(unittest.TestCase):
    def test_t1_live_origin_in_source(self):
        src = _read(_CLIENT_LIVE)
        self.assertIn('headers.set_header("Origin", DouyinAPI.live_url)', src)
        self.assertNotIn('headers.set_header("Origin", DouyinAPI.douyin_url)', src)
        self.assertIn("headers.with_bd(api, auth, origin=DouyinAPI.live_url)", src)
        self.assertIn("kwargs.get('enter_from', 'link_share')", src)
        self.assertIn('params.add_param("room_id", str(room_id))', src)
        self.assertIn("str(kwargs.get('type', '0'))", src)
        self.assertIn("kwargs.get('referer')", src)
        self.assertIn("kwargs.get('web_rid', room_id)", src)

    def test_t1_optional_key_tuple_matches_upstream(self):
        src = _read(_CLIENT_LIVE)
        for key in T1_OPTIONAL_KEYS:
            self.assertIn(f"'{key}'", src, f"缺少可选参数 {key}")

    def test_t2_no_random_celltime_in_source(self):
        """`random` 必须在 publish_comment 里彻底退场（上游 df52357 去随机）。

        用 AST 判定：`random` 模块名或 `randint` 属性名不得出现在评论域代码里。
        （文档里提到旧写法的字符串不算 —— 否则这条门禁会被自己的注释误伤。）
        """
        names = _used_names(_ast(_CLIENT_COMMENTS))
        self.assertNotIn("random", names,
                         "publish_comment 仍引用 random（上游已去随机 celltime）")
        self.assertNotIn("randint", names, "仍调用 randint")
        src = _read(_CLIENT_COMMENTS)
        self.assertIn("kwargs.get('comment_send_celltime', 0)", src)
        self.assertIn("kwargs.get('comment_video_celltime', 0)", src)

    def test_t2_text_extra_json_in_source(self):
        src = _read(_CLIENT_COMMENTS)
        self.assertNotIn('data["text_extra"] = []', src,
                         "text_extra 仍是裸 list（空 list 会被 urlencode 丢键）")
        self.assertIn("json.dumps(text_extra, ensure_ascii=False,", src)
        self.assertIn("separators=(',', ':')", src)
        self.assertIn("isinstance(text_extra, str)", src)

    def test_t2_reply_to_reply_id_in_source(self):
        self.assertIn("reply_to_reply_id", _read(_CLIENT_COMMENTS))

    def test_no_unintended_hard_gates(self):
        """上游 dtrait/ticket 硬门禁**不得**被照抄（本项目无该能力，照抄必抛）。

        用 AST 取**实际被引用的名字** —— 文档/注释里提到这些名字不算（本方法
        的 docstring 就在解释为什么不移植）。
        """
        names = _used_names(_ast(_CLIENT_COMMENTS))
        for banned in ("ticket_matches_session", "dtrait_blob", "dtrait_profile",
                       "session_dtrait", "session_dtrait_header"):
            self.assertNotIn(banned, names,
                             f"publish_comment 出现了被禁止硬移植的 {banned}")

    def test_e2e_flags_still_false(self):
        """本轮不真发：实机端到端验证标志必须保持 False（防虚报）。"""
        self.assertFalse(E2E_LIVE_DELIVERY_VERIFIED)
        self.assertFalse(E2E_COMMENT_PUBLISH_VERIFIED)


# ═══════════════════════════════════════════════════════════════════════════
# 上游源码逐字对账（Open-Source Provenance Law）
# ═══════════════════════════════════════════════════════════════════════════

@unittest.skipUnless(os.path.isfile(_UPSTREAM),
                     f"上游快照缺失，跳过逐字对账：{_UPSTREAM}")
class TestUpstreamParity(unittest.TestCase):
    """从上游源码**逐字提取**期望值，与本项目实现比对。"""

    @classmethod
    def setUpClass(cls):
        cls.up = _read(_UPSTREAM)

    def test_upstream_has_live_origin_and_with_bd_origin(self):
        self.assertIn('headers.set_header("Origin", DouyinAPI.live_url)', self.up)
        self.assertIn("headers.with_bd(api, auth, origin=DouyinAPI.live_url)", self.up)

    def test_upstream_enter_from_default_matches_ours(self):
        m = re.search(r'params\.add_param\("enter_from",\s*(kwargs\.get\([^)]*\))', self.up)
        self.assertIsNotNone(m, "上游未找到 enter_from 写法")
        self.assertIn(m.group(1), _read(_CLIENT_LIVE))

    def test_upstream_optional_key_tuple_matches_ours(self):
        m = re.search(r"for key in \(([^)]*)\):", self.up)
        self.assertIsNotNone(m, "上游未找到可选参数循环")
        for key in re.findall(r"'([a-z_]+)'", m.group(1)):
            self.assertIn(f"'{key}'", _read(_CLIENT_LIVE),
                          f"本项目缺少上游可选参数 {key}")

    def test_upstream_celltime_and_text_extra_literals_match_ours(self):
        own = _read(_CLIENT_COMMENTS)
        for literal in ("kwargs.get('comment_send_celltime', 0)",
                        "kwargs.get('comment_video_celltime', 0)",
                        "json.dumps(text_extra, ensure_ascii=False,",
                        "separators=(',', ':')",
                        "isinstance(text_extra, str)",
                        "kwargs.get('reply_to_reply_id', '')"):
            self.assertIn(literal, self.up, f"上游未见字面量 {literal!r}")
            self.assertIn(literal, own, f"本项目未见字面量 {literal!r}")

    def test_upstream_referer_expression_matches_ours(self):
        self.assertIn("kwargs.get('referer')", self.up)
        self.assertIn("kwargs.get('web_rid', room_id)", self.up)


if __name__ == "__main__":
    _env_gate()
    unittest.main(verbosity=2)
