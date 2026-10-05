# -*- coding: utf-8 -*-
"""平台接口层 —— 私信 IM域（mixin）

## 设计来源（照源项目 better-douyin）

对标 `src-tauri/src/api/client_im*.rs（4 个）`（方案 B 逆向情报）。

## 迁移说明（本分支 design/better-douyin，阶段1）

本模块由 `douyin_api.py` 的 `DouyinAPI` **机械拆分**而来（ast 精确取体，
非重写）：方法体逐字节保持原样，仅换宿主。

**内部调用保持 `DouyinAPI.xxx` 形式不变**——最终类仍是 `DouyinAPI`
（由 `dy_apis/douyin_api.py` 组装全部 mixin），故 109 处内部引用与
300+ 处外部调用**零改动**。

## 本域方法

```
  create_conversation
  get_conversation_list
  get_conversation_list_all
  get_message_by_init
  parse_init_conversations
  send_msg
  _classify_send_fail
  get_im_user_info
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


class ImMixin:
    """私信 IM域接口（来自 DouyinAPI）。"""

    @staticmethod
    def create_conversation(auth, to_user_id: int, **kwargs):
        """
        创建私信对话.
        :param auth: DouyinAuth object.
        :param to_user_id: 私信对话接收者ID.
        :return: 私信对话ID.
        """
        # 私信建会话走抖音 IM 私有网关 imapi.douyin.com（与 send_msg 同域），
        # 该接口靠 cookie + protobuf 内签名（web_protect 注入的 ticket/ts_sign/sdk_cert）鉴权，
        # 不需要 www.douyin.com 那套 msToken/a_bogus query（那是网页版 IM 接口参数，私有网关无此路径，
        # 强行拼 www.douyin.com 路径会返回 404 Unsupported path(Janus)）。
        url = "https://imapi.douyin.com/v2/conversation/create"
        requestProto = ProtoBuilder.build_create_conversation_request(auth, to_user_id, auth.get_uid())
        # IM 私有网关 imapi.douyin.com 靠 protobuf body 内签名(ticket/ts_sign/sdk_cert)鉴权，
        # 不叠加 www.douyin.com 的 bd-ticket-guard-* HTTP 头（叠加反而 INVALID_REQUEST）。
        headers = HeaderBuilder().build(HeaderType.PROTOBUF)
        headers.set_header('referer', 'https://www.douyin.com/')
        resp = requests.post(
            url,
            headers=headers.get(),
            cookies=auth.cookie,
            data=requestProto.SerializeToString(),
            verify=tls_verify()
        )
        # 解析前先判定 HTTP 状态与非 protobuf 响应（抖音常返回 HTML/JSON 错误页）
        if resp.status_code != 200:
            raise RuntimeError(
                f"create_conversation HTTP {resp.status_code}: {resp.text[:200]}")
        ctype = resp.headers.get("Content-Type", "")
        if "application/x-protobuf" not in ctype and "octet-stream" not in ctype \
                and resp.content[:1] not in (b'\x08', b'\x12', b'\x1a', b'\x22'):
            # 看起来像 JSON/HTML 错误响应
            raise RuntimeError(
                f"create_conversation 返回非 protobuf 响应(Content-Type={ctype}): {resp.text[:200]}")
        responseProto = ResponseProto.Response()
        try:
            responseProto.ParseFromString(resp.content)
        except Exception as e:
            raise RuntimeError(
                f"create_conversation 响应 protobuf 解析失败(Wire corrupt?): {e} | "
                f"raw[:120]={resp.content[:120]!r}")
        resp_json = protobuf_to_dict(responseProto)
        # 暴露抖音返回的业务错误（message/error_desc/status_code），而不是裸 KeyError
        biz_msg = resp_json.get("message") or resp_json.get("error_desc") or ""
        body = resp_json.get("body") or {}
        conv = body.get("create_conversation_v2_body")
        if not conv or not conv.get("conversation_info_list"):
            detail = ""
            if biz_msg:
                detail = f" message={biz_msg!r}"
            raise RuntimeError(
                f"create_conversation 响应缺少 create_conversation_v2_body{detail} | "
                f"resp_json={resp_json}")
        conversation = conv["conversation_info_list"][0]
        conversation_id = conversation['conversation_id']
        conversation_short_id, ticket = int(conversation['conversation_short_id']), conversation['ticket']
        return conversation_id, conversation_short_id, ticket

    @staticmethod
    def get_conversation_list(auth, conversation_short_id: int = 0, **kwargs) -> list:
        """拉取一页私信会话列表（cmd 610），用 conversation_short_id 作分页游标。

        conversation_short_id=0 返回第一页；传入上一页最后一条的 short_id 返回下一页。
        每页条数由服务端决定（通常 20~50）。
        """
        my_id = auth.get_uid()
        url = "https://imapi.douyin.com/v2/conversation/get_info_list"
        requestProto = ProtoBuilder.build_get_conversation_list_info_request(
            auth, int(my_id), int(my_id), conversation_short_id)
        headers = HeaderBuilder().build(HeaderType.PROTOBUF)
        headers.set_header('referer', 'https://www.douyin.com/')

        resp = requests.post(
            url,
            headers=headers.get(),
            cookies=auth.cookie,
            data=requestProto.SerializeToString(),
            verify=tls_verify()
        )
        if resp.status_code != 200:
            raise RuntimeError(
                f"get_conversation_list HTTP {resp.status_code}: {resp.text[:200]}")
        ctype = resp.headers.get("Content-Type", "")
        if "application/x-protobuf" not in ctype and "octet-stream" not in ctype \
                and resp.content[:1] not in (b'\x08', b'\x12', b'\x1a', b'\x22'):
            raise RuntimeError(
                f"get_conversation_list 返回非 protobuf 响应(Content-Type={ctype}): {resp.text[:200]}")
        responseProto = ResponseProto.Response()
        try:
            responseProto.ParseFromString(resp.content)
        except Exception as e:
            raise RuntimeError(
                f"get_conversation_list 响应 protobuf 解析失败(Wire corrupt?): {e} | "
                f"raw[:120]={resp.content[:120]!r}")
        resp_json = protobuf_to_dict(responseProto)
        body = resp_json.get("body") or {}
        conv_body = body.get("get_conversation_info_list_v2_response_body") or {}
        conv_list = conv_body.get("conversation_info_list") or []
        return conv_list

    @staticmethod
    def get_conversation_list_all(auth, max_pages: int = 20) -> list:
        """分页拉取【全部】私信会话列表，直到无更多结果或达到 max_pages。

        conversation_id 格式 `0:1:<uid_a>:<uid_b>` 中提取非自身 uid 作为 peer_id。
        """
        my_uid = str(auth.get_uid())
        all_convs = []
        cursor = 0
        seen_ids = set()
        for _ in range(max_pages):
            try:
                page = DouyinAPI.get_conversation_list(auth, conversation_short_id=cursor)
            except Exception as e:
                logger.warning(f"[AUTH-022] " + f"[im] 分页拉取会话列表第 {_+1} 页失败: {e}")
                break
            if not page:
                break
            new_count = 0
            for info in page:
                conv_id = info.get("conversation_id") or ""
                short_id = info.get("conversation_short_id")
                if not conv_id or conv_id in seen_ids:
                    continue
                seen_ids.add(conv_id)
                # 从 conversation_id 解析对方 uid：格式 0:1:<uid_a>:<uid_b>
                parts = conv_id.split(":")
                peer_uid = None
                if len(parts) >= 4:
                    uid_a, uid_b = parts[2], parts[3]
                    if uid_a != my_uid:
                        peer_uid = uid_a
                    elif uid_b != my_uid:
                        peer_uid = uid_b
                    # 两者相同（自身会话）→ peer_uid=None，跳过
                info["_peer_uid"] = peer_uid
                all_convs.append(info)
                new_count += 1
            # 用最后一条的 short_id 作为下一页游标
            last_short = page[-1].get("conversation_short_id")
            if not last_short or new_count == 0 or int(last_short) == cursor:
                break
            cursor = int(last_short)
        logger.info(f"[im] 分页拉取完成：共 {len(all_convs)} 个会话")
        return all_convs

    @staticmethod
    def get_message_by_init(auth) -> bytes:
        """调 imapi get_message_by_init（cmd 2043）获取全量会话初始化数据。

        返回原始 protobuf 响应字节（250KB+，含全部会话 ID + 消息 + peer uid）。
        后续用正则从原始字节中提取 conversation_id 和 peer uid（proto 未定义 cmd 2043）。

        实测来源：抖音网页 douyin.com/chat 首次加载时调用此 API。
        """
        from builder.proto import ProtoBuilder
        from builder.header import HeaderBuilder, HeaderType
        # 用通用 build_normal_request 构建 cmd=2043 请求（与网页请求结构一致）
        request = ProtoBuilder.build_normal_request(auth, 2043)
        # body 字段：proto 未定义 cmd 2043 的 oneof，手动追加最小 body 字节
        # 网页请求体 field 8 (body) = tag(da 7f) + len(02) + content(10 00) = field 2 varint 0
        body_bytes = request.SerializeToString()
        # 在 body field (field 8) 后追加 cmd 2043 body：field 2043 (varint tag da7f) + len 2 + field 2 varint 0
        # tag for field 2043 wire type 2: (2043 << 3) | 2 = 16346, varint = da 7f
        init_body = bytes([0xda, 0x7f, 0x02, 0x10, 0x00])
        # 在 Request 序列化结果中，field 8 (body) 当前为空字符串 (22 00)
        # 替换为含 init_body 的版本：tag 42 + len + init_body
        # 简单做法：直接在序列化末尾追加 body field
        body_bytes = body_bytes + bytes([0x42, len(init_body)]) + init_body

        url = "https://imapi.douyin.com/v1/message/get_message_by_init"
        headers = HeaderBuilder().build(HeaderType.PROTOBUF)
        headers.set_header('referer', 'https://www.douyin.com/')
        resp = requests.post(
            url, headers=headers.get(), cookies=auth.cookie,
            data=body_bytes, verify=tls_verify(), timeout=15,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"get_message_by_init HTTP {resp.status_code}: {resp.text[:200]}")
        logger.info(f"[im] get_message_by_init 响应 {len(resp.content)} bytes")
        return resp.content

    @staticmethod
    def parse_init_conversations(raw: bytes, my_uid: str) -> list[dict]:
        """从 get_message_by_init 原始字节中提取全部会话 + peer uid + sec_uid（正则法）。

        conversation_id 格式 0:1:<uid_a>:<uid_b>，其中非自身 uid 即对方 uid。
        同时提取 sec_uid（MS4wLjAB...格式），就近匹配到会话用于后续昵称解析。
        """
        import re
        decoded = raw.decode("utf-8", errors="replace")
        # 1) 找所有 conversation_id 及位置
        seen = set()
        result = []
        for m in re.finditer(r'0:1:\d{5,20}:\d{5,20}', decoded):
            cid = m.group()
            if cid in seen:
                continue
            seen.add(cid)
            parts = cid.split(":")
            uid_a, uid_b = parts[2], parts[3]
            if uid_a == uid_b:
                continue  # 跳过自身会话
            peer_uid = uid_b if uid_a == my_uid else uid_a
            result.append({"pos": m.start(), "conversation_id": cid, "peer_uid": peer_uid})

        # 2) 找所有 sec_uid 及位置（MS4wLjAB 开头，10~60 字符）
        sec_positions = [(m.start(), m.group()) for m in re.finditer(r'MS4wLjAB[\w_]{10,60}', decoded)]
        sec_seen = set()
        unique_secs = [(p, s) for p, s in sec_positions if not (s in sec_seen or sec_seen.add(s))]

        # 3) 就近匹配 sec_uid 到会话（±800 字节内最近的）
        for c in result:
            best_sec = None
            best_dist = 800
            for spos, sec in unique_secs:
                dist = abs(spos - c["pos"])
                if dist < best_dist:
                    best_dist = dist
                    best_sec = sec
            c["sec_uid"] = best_sec

        logger.info(f"[im] 从 init 响应中提取 {len(result)} 个真实会话，"
                     f"{len(unique_secs)} 个 sec_uid，"
                     f"匹配到 sec_uid 的会话 {sum(1 for c in result if c.get('sec_uid'))}/{len(result)}")
        return result

    @staticmethod
    def get_identity_security_token(auth, force=False, **kwargs) -> tuple:
        """取 PC IM 发送所需的短时身份令牌（2026 rollout 新增）。

        网页 IM bundle 自 2026 起在 ``imapi/v1/message/send`` 之前会调用
        ``/passport/safe/get_identity_security_token/``。返回的 token **不是
        Cookie**，而是与返回的 device_id 一起写进 protobuf header map。它绑定
        当前 ticket/dtrait 会话，**绝不可合成或跨 Auth 实例复用**。

        :return: (token, device_id)；失败抛 RuntimeError。
        """
        now = time.time()
        cached = str(getattr(auth, 'identity_security_token', '') or '')
        cached_ts = float(getattr(auth, 'identity_security_token_ts', 0) or 0)
        if cached and not force and now - cached_ts < 240:
            return cached, str(getattr(auth, 'identity_security_device_id', '') or '')

        api = '/passport/safe/get_identity_security_token/'
        referer = kwargs.get('referer') or 'https://www.douyin.com/chat?isPopup=1'
        trace_id = uuid.uuid4().hex[:8]
        params = {
            'passport_jssdk_version': '4.2.3',
            'passport_jssdk_type': 'lite',
            'is_from_ttaccountsdk': '1',
            'aid': '6383',
            'language': 'zh',
            'scene': 'web_im',
            'auto_retry_req': '0',
            'skip_verify': 'false',
            'identity_token_force_get_tag': '0',
            'biz_trace_id': trace_id,
            'id_token_version': '1.2.10',
            'msToken': getattr(auth, 'msToken', '') or (auth.cookie or {}).get('msToken', ''),
        }
        # 浏览器先签 query 再自行追加 a_bogus；该 passport 端点无 verifyFp/fp。
        params['a_bogus'] = generate_a_bogus(splice_url(params))

        headers = HeaderBuilder().build(HeaderType.GET)
        headers.set_referer(referer)
        headers.set_header('accept', 'application/json, text/javascript')
        _ck = auth.cookie or {}
        headers.set_header(
            'x-tt-passport-csrf-token',
            _ck.get('passport_csrf_token', '') or _ck.get('passport_csrf_token_default', ''))
        headers.set_header('x-tt-passport-trace-id', trace_id)
        # 与 send 同一安全会话：带同一套 bd-ticket-guard 客户端数据。
        headers.with_bd(api, auth)

        resp = requests.get(
            f'https://www.douyin.com{api}', params=params,
            headers=headers.get(), cookies=auth.cookie, verify=tls_verify())
        try:
            payload = resp.json()
        except Exception as exc:
            raise RuntimeError('身份安全 token 接口返回不可解析响应') from exc
        data = payload.get('data') or {}
        token = str(data.get('identity_security_token') or '')
        device_id = str(data.get('device_id') or '')
        if payload.get('message') not in (None, 'success') or not token:
            raise RuntimeError(f'身份安全 token 获取失败: {payload!r}')
        auth.identity_security_token = token
        auth.identity_security_device_id = device_id
        auth.identity_security_token_ts = now
        return token, device_id

    @staticmethod
    def send_msg(auth, conversation_id, conversation_short_id, ticket, content: str, **kwargs) -> tuple:
        """
        发送私信（V2：返回 (bool ok, str detail)）。
        :param auth: DouyinAuth object.
        :param conversation_id: 私信对话ID.
        :param conversation_short_id: 私信对话短ID.
        :param ticket: 私信对话票据.
        :param content: 私信内容.
        :return: (True, 'ok') 发送成功；否则 (False, 具体原因)
        """
        # 文案为空防护：抖音对空消息会返回 OK 但实际未发送（被截断的根因之一）
        if not content or not str(content).strip():
            logger.error(f"[AUTH-024] " + "[私信] 文案为空，拒绝发送（避免日志显示成功但实际未发送）")
            return False, "文案为空，拒绝发送"
        # 私信文案长度受限：超长会被平台截断/静默丢弃，先本地拦截
        if len(str(content)) > 500:
            logger.warning(f"[AUTH-025] " + f"[私信] 文案长度 {len(str(content))} 超过 500 字，可能被平台截断，仅前 500 字发送")
        url = 'https://imapi.douyin.com/v1/message/send'
        # 2026 版：请求由 bd-ticket-guard HTTP 头鉴权（with_bd），不再依赖 body 内
        # 顶层签名。同时 message/send 需携带短时 identity-security 材料。
        headers = HeaderBuilder().build(HeaderType.PROTOBUF)
        headers.set_header('referer', 'https://www.douyin.com/')
        headers.with_bd('/v1/message/send', auth)
        try:
            identity_token, identity_device_id = DouyinAPI.get_identity_security_token(auth)
        except Exception as e:  # noqa: BLE001
            logger.error(f"[AUTH-030] " + f"[私信] identity_security_token 获取失败: {e}")
            return False, f"身份令牌获取失败：{e}"
        requestProto = ProtoBuilder.build_send_message_request(
            auth, conversation_id, conversation_short_id, ticket, content,
            identity_security_token=identity_token,
            identity_security_device_id=identity_device_id)
        # 浏览器顺序：msToken -> a_bogus -> verifyFp -> fp。
        # 仅第一个字段参与本端点的 a_bogus 输入。
        params = {'msToken': getattr(auth, 'msToken', '') or (
            auth.cookie.get('msToken') if auth.cookie else '') or generate_msToken()}
        query = splice_url(params)
        params['a_bogus'] = generate_a_bogus(query)
        webid = auth.cookie.get('s_v_web_id', '') if auth.cookie else ''
        params['verifyFp'] = webid
        params['fp'] = webid
        resp = requests.post(url, params=params, headers=headers.get(), verify=tls_verify(), cookies=auth.cookie,
                             data=requestProto.SerializeToString())
        if resp.status_code != 200:
            logger.error(f"[AUTH-026] " + f'私信发送 HTTP {resp.status_code}: {resp.text[:200]}')
            return False, f'私信发送 HTTP {resp.status_code}: {resp.text[:200]}', \
                {"http_ok": False, "delivered": False, "state": "blocked",
                 "server_message_id": "", "status": None, "check_code": None,
                 "reason": f"HTTP {resp.status_code}", "error_kind": "http_error"}
        responseProto = ResponseProto.Response()
        try:
            responseProto.ParseFromString(resp.content)
        except Exception as e:
            # 2026-09-08：抖音风控/异常时返回 JSON（如 {"decision":"KICK"}）
            # 而非 protobuf——原日志报"Wire format was corrupt"误导排查。
            # 先尝试 JSON 解析，给出真实风控原因。
            try:
                import json as _json
                _j = _json.loads(resp.content.decode("utf-8", "replace"))
                _dec = _j.get("decision") or _j.get("message") or str(_j)[:80]
                logger.error(f"[AUTH-027] " + f"私信发送被抖音拒绝（JSON 响应）：decision={_dec} | "
                    f"full={str(_j)[:200]}")
                return False, f"抖音拒绝发送：{_dec}（风控/限流，建议冷却后重试）"
            except Exception:
                pass
            logger.error(f"[AUTH-028] " + f'私信发送响应 protobuf 解析失败: {e} | raw[:120]={resp.content[:120]!r}')
            return False, f'响应用户解析失败: {e}'
        resp_json = protobuf_to_dict(responseProto)
        # 2026-09-23（审计 P0-1）：投递判定改用**唯一解析器**（宽容字节解析）。
        # 旧实现 `message == 'OK'` 不足以证明投递 —— 本文件 _classify_send_fail
        # 自己就写着「发送被静默拦截（返回 OK 但未实际投递）」。真正的证据在
        # 响应体 body.field6→field100 的 server_message_id / check_code，
        # 而 static/Response_pb2.Response.body 未定义 field 100 ⇒ 必须走宽容解析。
        try:
            from services.send_response import delivery_verdict as _drv
            _v = _drv(resp.content, http_ok=True)
        except Exception as _e:  # noqa: BLE001
            logger.warning(f"[AUTH-031] " + f"[私信] 投递判定降级（解析器不可用）: {_e}")
            _v = {"delivered": bool(resp_json.get('message') == 'OK'),
                  "server_message_id": "", "status": None, "check_code": None,
                  "state": "unknown", "reason": "解析器不可用，退回 message==OK",
                  "error_kind": "unknown"}
        if _v.get("delivered"):
            logger.info(f"私信发送成功 conversation_id={conversation_id} "
                        f"server_message_id={_v.get('server_message_id')} "
                        f"check_code={_v.get('check_code')}")
            return True, "ok", _v
        # —— 无投递证据：给出**可归因**的失败原因（不再笼统「发送失败」） ——
        # 2026-09-28：把失败类型**结构化**（error_kind 枚举）挂到 verdict 上，
        # 全链路透传；消费方（配额/冷静期）只认枚举，不再猜文案。
        try:
            from services.send_response import failure_kind as _fk
            _kind = _fk(resp_json, _v)
        except Exception:  # noqa: BLE001
            _kind = "unknown"
        _v["error_kind"] = _kind
        detail = str(_v.get("reason") or "")
        if _v.get("state") == "review":
            logger.warning(f"[AUTH-029] " + f"[私信] {detail} conversation_id={conversation_id}")
        elif resp_json.get('message') == 'OK':
            # 只回 OK 却无消息号 —— 历史上被误当成功的那一类
            logger.error(f"[AUTH-032] " + f"[私信] 返回 OK 但服务端无 server_message_id "
                         f"⇒ 判定**未投递**（疑似内容违规/被截断）conversation_id={conversation_id} "
                         f"kind={_kind}")
        else:
            detail = DouyinAPI._classify_send_fail(resp_json)
            logger.error(f"[AUTH-029] " + f"私信发送失败 conversation_id={conversation_id} "
                         f"kind={_kind} resp_json={resp_json}")
        # ⚠️ 兼容契约：旧调用方按 (bool, str) 解包 ⇒ 第二项必须是**可读原因串**，
        #    结构化判定挂在第三项（旧调用方忽略）。
        return False, (detail or "发送未确认投递"), _v

    @staticmethod
    def _classify_send_fail(resp_json: dict) -> str:
        """把抖音 IM 发送失败响应解析成人类可读的原因（发送结果反馈）。

        覆盖：需互关、发送频繁/被频控、隐私权限、账号风控、用户不存在等。
        """
        raw_msg = str(resp_json.get("message") or "").upper()
        err = str(resp_json.get("error_desc") or "")
        status = str(resp_json.get("status_code") or "")
        combined = " ".join([raw_msg, err, status]).upper()
        # 顺序敏感：先命中具体场景，再兜底
        if raw_msg == "OK":
            return ("发送被静默拦截（返回 OK 但未实际投递，疑似内容违规/被截断）"
                    "—— 判定见 services/send_response.py delivery_verdict()")
        if any(k in combined for k in ("MUTUAL", "FOLLOW_EACH", "NEED_FOLLOW", "INTERACT")):
            return "对方需与你互关后才能收到私信"
        if any(k in combined for k in ("PRIVILEGE", "PERMISSION", "PRIVACY")):
            return "对方隐私/权限设置限制，无法主动私信"
        if any(k in combined for k in ("FREQUENT", "RATE", "TOO_", "LIMIT", "SPAM", "FREQUENCY")):
            return "发送过于频繁，被平台频控拦截，请降低频率或更换账号"
        if any(k in combined for k in ("KICK", "INVALID_REQUEST", "RISK")):
            return f"私信被风控(KICK/INVALID_REQUEST): {err or raw_msg}"
        if any(k in combined for k in ("NOT_FOUND", "NOT FOUND", "USER_NOT_EXIST")):
            return "对方不存在或已注销"
        if any(k in combined for k in ("DENY", "FORBIDDEN", "BLOCK")):
            return f"发送被拒绝: {err or raw_msg}"
        base = (err or raw_msg or status or str(resp_json.get("status", ""))).strip()
        if base and base != "OK":
            return f"发送失败: {base}"
        return "发送失败（未知原因，请查看运行日志）"

    @staticmethod
    def get_im_user_info(auth, uid: str) -> dict:
        """调 REST JSON API 解析用户昵称/头像（GET + to_user_id）。

        注：CDP 实测浏览器用 POST + sec_user_ids，但 a_bogus 签名无法复现
        （status=8）。GET + to_user_id 经验证可返回昵称/头像，作退路保留。
        """
        from builder.params import Params
        from utils.fingerprint import get_profile
        api = "/aweme/v1/web/im/user/info/"
        headers = HeaderBuilder().build(HeaderType.GET)
        headers.set_referer("https://www.douyin.com/")
        params = Params()
        (params
         .add_param("device_platform", "webapp")
         .add_param("aid", "6383")
         .add_param("channel", "channel_pc_web")
         .add_param("to_user_id", str(uid))
         .add_param("pc_client_type", "1")
         .add_param("update_version_code", "170400")
         .add_param("version_code", "170400")
         .add_param("cookie_enabled", "true")
         .add_param("browser_language", "zh-CN")
         .add_param("browser_platform", "Win32")
         .add_param("browser_name", get_profile()["browser_name"])
         .add_param("browser_version", get_profile()["browser_version"])
         .add_param("browser_online", "true")
         .add_param("engine_name", get_profile()["engine_name"])
         .add_param("os_name", "Windows")
         .add_param("os_version", "10")
         .add_param("platform", "PC")
         .add_param("downlink", "10")
         .add_param("effective_type", "4g")
         .add_param("round_trip_time", "100"))
        params.with_web_id(auth, f"https://www.douyin.com/")
        params.add_param("msToken", auth.cookie.get("msToken", ""))
        params.add_param("verifyFp", auth.cookie.get("s_v_web_id", ""))
        params.add_param("fp", auth.cookie.get("s_v_web_id", ""))
        params.with_a_bogus()
        resp = requests.get(
            f'{DouyinAPI.douyin_url}{api}',
            headers=headers.get(), cookies=auth.cookie,
            params=params.get(), verify=tls_verify(), timeout=8,
        )
        # 2026-09-17 修补（OCR 审查 HIGH）：裸 json.loads → safe_json
        # （后续已用 .get 访问，故只需统一降级策略）。
        data = safe_json(resp)
        if not isinstance(data, dict):
            return {}
        if data.get("status_code") != 0:
            logger.warning(f"[AUTH-023] " + f"[im] get_im_user_info uid={uid} status={data.get('status_code')}")
            return {}
        items = data.get("data") or []
        if not items:
            return {}
        u = items[0]
        avatar_small = u.get("avatar_small") or u.get("avatar_thumb") or {}
        avatar_url = avatar_small.get("url_list", [""])[0] if avatar_small.get("url_list") else ""
        return {
            "uid": str(u.get("uid") or uid),
            "nickname": u.get("nickname") or uid,
            "avatar": avatar_url,
            "sec_uid": u.get("sec_uid") or "",
            "follow_status": u.get("follow_status"),
            "follower_status": u.get("follower_status"),
        }

