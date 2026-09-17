# -*- coding: utf-8 -*-
"""私信图片发送（后端直发，不依赖浏览器点击）。

2026-09-05 实现依据：
  1. 本机实机抓包（docs/upload_flow.md ①-⑤）；
  2. 开源项目 Rockedw/douyin-web-api-sdk（Java）交叉验证，其 AWS4 签名、
     Commit 请求体、图片 content 结构与本机抓包完全一致；
  3. 真实图片消息 protobuf 帧解码 ground truth（见模块底部注释）。

链路（6 步）：
  ① GET  www.douyin.com/aweme/v1/web/im/upload/config/v2   → STS 临时凭证(~2h)
  ② GET  vod.bytedanceapi.com?Action=ApplyUploadInner      → StoreUri/Auth/UploadHost/SessionKey
  ③ POST https://{UploadHost}/upload/v1/{StoreUri}          → 二进制直传(crc32 校验)
  ④ POST vod.bytedanceapi.com?Action=CommitUploadInner      → Encryption.Uri/SecretKey/SourceMd5
  ⑤ POST www.douyin.com/aweme/v1/web/privacy/batch_build_image/ → 签名 CDN URL（仅自家前端渲染用,可选）
  ⑥ POST imapi.douyin.com/v1/message/send（protobuf, message_type=27, aweType=2702）

⚠️ 风控约束：仅供单条/低频发送（复用现有 send_msg 的节奏），严禁循环群发。
"""
from utils.tls_policy import tls_verify  # noqa: E402
import base64  # noqa: F401 (保留给调试用)
import hashlib
import hmac
import io
import json
import random
import string
import time
import uuid

import requests
from loguru import logger

import static.Request_pb2 as RequestProto
from builder.header import HeaderBuilder
from builder.proto import ProtoBuilder
from utils.dy_util import (
    generate_a_bogus,
    generate_millisecond,
    generate_msToken,
    generate_req_sign,
    splice_url,
)

_VOD_HOST = "https://vod.bytedanceapi.com"
_REGION = "cn-north-1"
_SERVICE_VOD = "vod"
# SpaceName 以 ① 返回为准，历史实测值为 zhenzhen
_DEFAULT_SPACE = "zhenzhen"
_UA = HeaderBuilder.ua


# ----------------------------------------------------------------------------
# AWS4 SigV4 签名（翻译自开源 SDK AWS4SignatureUtils，行为一致）
# ----------------------------------------------------------------------------
def _sha256_hex(data) -> str:
    if isinstance(data, str):
        data = data.encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _hmac_sha256_hex(key: bytes, msg: str) -> str:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).hexdigest()


def _hmac_sha256(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _uri_encode(s: str) -> str:
    """AWS4 要求的 URI 编码：空格→%20，*→%2A，~ 不编码。"""
    import urllib.parse

    return urllib.parse.quote(str(s), safe="~")


def _utc_times() -> tuple:
    """返回 (amzDate 'YYYYMMDDTHHMMSSZ', dateStamp 'YYYYMMDD')（UTC）。"""
    amz = time.gmtime()
    amz_date = time.strftime("%Y%m%dT%H%M%SZ", amz)
    date_stamp = time.strftime("%Y%m%d", amz)
    return amz_date, date_stamp


def _random_s(n: int = 11) -> str:
    """开源 SDK 同款随机串：11 位 [0-9a-z]。"""
    chars = string.digits + "abcdefghigklmnopqrstuvwxyz"  # 注意原 SDK 就拼错了 gkl
    return "".join(random.choice(chars) for _ in range(n))


def _signature_key(secret: str, date_stamp: str, region: str, service: str) -> bytes:
    k = _hmac_sha256(("AWS4" + secret).encode("utf-8"), date_stamp)
    k = _hmac_sha256(k, region)
    k = _hmac_sha256(k, service)
    return _hmac_sha256(k, "aws4_request")


def _aws4_get_authorization(secret_key, canonical_query, amz_date, session_token,
                            date_stamp, access_key_id, region=_REGION,
                            service=_SERVICE_VOD) -> str:
    """GET 请求 SigV4：signed headers = x-amz-date;x-amz-security-token，空 payload。"""
    sk = _signature_key(secret_key, date_stamp, region, service)
    canonical_headers = f"x-amz-date:{amz_date}\nx-amz-security-token:{session_token}\n"
    canonical_request = "\n".join([
        "GET", "/", canonical_query, canonical_headers,
        "x-amz-date;x-amz-security-token", _sha256_hex(""),
    ])
    scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256", amz_date, scope, _sha256_hex(canonical_request),
    ])
    sig = _hmac_sha256_hex(sk, string_to_sign)
    return (f"AWS4-HMAC-SHA256 Credential={access_key_id}/{scope}, "
            f"SignedHeaders=x-amz-date;x-amz-security-token, Signature={sig}")


def _aws4_post_authorization(secret_key, canonical_query, amz_date, session_token,
                             date_stamp, access_key_id, body_bytes: bytes,
                             region=_REGION, service=_SERVICE_VOD) -> tuple:
    """POST 请求 SigV4：signed headers = x-amz-content-sha256;x-amz-date;x-amz-security-token。

    返回 (authorization, content_sha256)。
    """
    content_sha = _sha256_hex(body_bytes)
    sk = _signature_key(secret_key, date_stamp, region, service)
    # 字典序：x-amz-content-sha256 < x-amz-date < x-amz-security-token
    canonical_headers = (
        f"x-amz-content-sha256:{content_sha}\n"
        f"x-amz-date:{amz_date}\n"
        f"x-amz-security-token:{session_token}\n"
    )
    canonical_request = "\n".join([
        "POST", "/", canonical_query, canonical_headers,
        "x-amz-content-sha256;x-amz-date;x-amz-security-token", content_sha,
    ])
    scope = f"{date_stamp}/{region}/{service}/aws4_request"
    string_to_sign = "\n".join([
        "AWS4-HMAC-SHA256", amz_date, scope, _sha256_hex(canonical_request),
    ])
    sig = _hmac_sha256_hex(sk, string_to_sign)
    auth = (f"AWS4-HMAC-SHA256 Credential={access_key_id}/{scope}, "
            f"SignedHeaders=x-amz-content-sha256;x-amz-date;x-amz-security-token, "
            f"Signature={sig}")
    return auth, content_sha


# ----------------------------------------------------------------------------
# ① STS 上传凭证
# ----------------------------------------------------------------------------
def get_upload_config(auth) -> dict:
    """① 拉取 IM 上传 STS 凭证。返回 {ak, sk, st, space_name, expire_at}。"""
    params = {
        "device_platform": "webapp",
        "aid": "6383",
        "channel": "channel_pc_web",
        "update_version_code": "170400",
        "pc_client_type": "1",
        "pc_libra_divert": "Windows",
        "support_h265": "1",
        "support_dash": "1",
        "cpu_core_num": "8",
        "version_code": "170400",
        "version_name": "17.4.0",
        "cookie_enabled": "true",
        "screen_width": "1440",
        "screen_height": "900",
        "browser_language": "zh-CN",
        "browser_platform": "Win32",
        "browser_name": "Chrome",
        "browser_version": "139.0.0.0",
        "browser_online": "true",
        "engine_name": "Blink",
        "engine_version": "139.0.0.0",
        "os_name": "Windows",
        "os_version": "10.0.0",
        "device_memory": "8",
        "platform": "PC",
        "downlink": "10",
        "effective_type": "4g",
        "round_trip_time": "150",
    }
    cookie = auth.cookie or {}
    webid = cookie.get("webid") or cookie.get("s_v_web_id", "")
    if webid:
        params["webid"] = webid
    uifid = cookie.get("uifid", "")
    if uifid:
        params["uifid"] = uifid
    _cookie_ms = cookie.get("msToken") or ""
    params["msToken"] = _cookie_ms if _cookie_ms else generate_msToken()
    if webid:
        params["verifyFp"] = webid
        params["fp"] = webid
    # 2026-09-05 实测：该接口必须带 a_bogus，缺失报 status_code=8 用户未登录
    query = splice_url(params)
    params["a_bogus"] = generate_a_bogus(query)
    url = "https://www.douyin.com/aweme/v1/web/im/upload/config/v2"
    headers = {
        "accept": "application/json, text/plain, */*",
        "accept-language": "zh-CN,zh;q=0.9",
        "cache-control": "no-cache",
        "pragma": "no-cache",
        "priority": "u=1, i",
        "referer": "https://www.douyin.com/",
        "sec-ch-ua": '"Not;A=Brand";v="99", "Google Chrome";v="139", "Chromium";v="139"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
        "user-agent": _UA,
    }
    resp = requests.get(url, params=params, headers=headers, cookies=auth.cookie,
                        verify=tls_verify(), timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"upload/config HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    if data.get("status_code") not in (0, None):
        raise RuntimeError(f"upload/config 业务错误: {data}")
    cfg = (data.get("public_image_config")
           or data.get("public_image_config_v2") or {})
    if not cfg.get("access_key_id"):
        raise RuntimeError(f"upload/config 响应缺少凭证字段: {list(data.keys())}")
    return {
        "ak": cfg["access_key_id"],
        "sk": cfg["secret_access_key"],
        "st": cfg["session_token"],
        "space_name": cfg.get("space_name") or _DEFAULT_SPACE,
        "expire_at": cfg.get("expire_at"),
    }


# ----------------------------------------------------------------------------
# ② ApplyUploadInner
# ----------------------------------------------------------------------------
def apply_upload(auth, cfg: dict, file_size: int) -> dict:
    """② 申请上传地址。返回 {store_uri, auth_token, upload_host, session_key, user_id}。"""
    amz_date, date_stamp = _utc_times()
    s = _random_s()
    params = {
        "Action": "ApplyUploadInner",
        "FileSize": str(file_size),
        "FileType": "image",
        "IsInner": "1",
        "s": s,
        "SpaceName": cfg["space_name"],
        "Version": "2020-11-19",
        "NeedFallback": "true",
    }
    # 签名用字典序 canonical query；实际 URL 必须与签名 query 完全一致（SigV4 要求）
    canonical_query = "&".join(
        f"{_uri_encode(k)}={_uri_encode(v)}" for k, v in sorted(params.items()))
    authorization = _aws4_get_authorization(
        cfg["sk"], canonical_query, amz_date, cfg["st"], date_stamp, cfg["ak"])
    url = _VOD_HOST + "/?" + canonical_query
    headers = {
        "Accept": "*/*",
        "Authorization": authorization,
        "User-Agent": _UA,
        "X-Amz-Date": amz_date,
        "x-amz-security-token": cfg["st"],
        "referer": "https://www.douyin.com/",
    }
    resp = requests.get(url, headers=headers, cookies=auth.cookie,
                        verify=tls_verify(), timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"ApplyUploadInner HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    err = (data.get("ResponseMetadata") or {}).get("Error")
    if err:
        raise RuntimeError(f"ApplyUploadInner 业务错误: {err.get('Code')} {err.get('Message')}")
    result = data.get("Result") or {}
    inner = result.get("InnerUploadAddress") or {}
    nodes = inner.get("UploadNodes") or []
    node = nodes[0] if nodes else {}
    store_infos = node.get("StoreInfos") or []
    si = store_infos[0] if store_infos else {}
    hosts = node.get("UploadHosts") or []
    outer = result.get("UploadAddress") or {}
    session_key = node.get("SessionKey") or outer.get("SessionKey") or ""
    store_uri = si.get("StoreUri") or ""
    if not store_uri or not session_key:
        raise RuntimeError(f"ApplyUploadInner 响应缺 StoreUri/SessionKey: {str(data)[:300]}")
    user_id = ((si.get("StorageHeader") or {}).get("USER_ID")
               or (outer.get("StoreInfos") or [{}])[0].get("StorageHeader", {}).get("USER_ID", "")
               or "")
    return {
        "store_uri": store_uri,
        "auth_token": si.get("Auth", ""),
        "upload_host": (hosts[0] if hosts else outer.get("UploadHosts", [""])[0]) or "",
        "session_key": session_key,
        "user_id": str(user_id),
    }


# ----------------------------------------------------------------------------
# ③ TOS 直传
# ----------------------------------------------------------------------------
def upload_to_tos(addr: dict, data: bytes) -> None:
    """③ 二进制直传 TOS。成功码 2000，失败抛异常。"""
    host = addr["upload_host"]
    if not host:
        raise RuntimeError("TOS 直传缺 UploadHost")
    url = f"https://{host}/upload/v1/{addr['store_uri']}"
    import zlib
    crc32 = f"{zlib.crc32(data) & 0xFFFFFFFF:08x}"
    headers = {
        "accept": "*/*",
        "authorization": addr["auth_token"],
        "content-crc32": crc32,
        "content-disposition": 'attachment; filename="image"',
        "content-type": "application/octet-stream",
        "origin": "https://www.douyin.com",
        "referer": "https://www.douyin.com/",
        "user-agent": _UA,
    }
    if addr.get("user_id"):
        headers["x-storage-u"] = addr["user_id"]
    resp = requests.post(url, headers=headers, data=data, verify=tls_verify(), timeout=60)
    if resp.status_code != 200:
        raise RuntimeError(f"TOS 直传 HTTP {resp.status_code}: {resp.text[:200]}")
    try:
        rj = resp.json()
    except Exception as e:
        raise RuntimeError(f"TOS 直传响应非 JSON: {resp.text[:200]} ({e})")
    if rj.get("code") != 2000:
        raise RuntimeError(f"TOS 直传失败: {rj}")
    logger.info(f"[img-send] ③ TOS 直传成功 crc32={crc32}")


# ----------------------------------------------------------------------------
# ④ CommitUploadInner → 加密素材要素
# ----------------------------------------------------------------------------
def commit_upload(auth, cfg: dict, session_key: str) -> dict:
    """④ 提交上传，返回 {uri, secret_key, source_md5, algorithm, extra}。"""
    amz_date, date_stamp = _utc_times()
    body_obj = {
        "SessionKey": session_key,
        "Functions": [{
            "name": "Encryption",
            "input": {
                "Config": {"copies": "cipher_v2"},
                "PolicyParams": {"policy-set": "check,thumb,medium,large"},
            },
        }],
    }
    body = json.dumps(body_obj, separators=(",", ":"), ensure_ascii=False)
    body_bytes = body.encode("utf-8")
    q = {"Action": "CommitUploadInner", "Version": "2020-11-19",
         "SpaceName": cfg["space_name"]}
    canonical_query = "&".join(
        f"{_uri_encode(k)}={_uri_encode(v)}" for k, v in sorted(q.items()))
    authorization, content_sha = _aws4_post_authorization(
        cfg["sk"], canonical_query, amz_date, cfg["st"], date_stamp, cfg["ak"], body_bytes)
    url = _VOD_HOST + "/?" + "&".join(f"{k}={v}" for k, v in q.items())
    headers = {
        "accept": "*/*",
        "authorization": authorization,
        "content-type": "text/plain;charset=UTF-8",
        "origin": "https://www.douyin.com",
        "referer": "https://www.douyin.com/",
        "user-agent": _UA,
        "x-amz-content-sha256": content_sha,
        "x-amz-date": amz_date,
        "x-amz-security-token": cfg["st"],
    }
    resp = requests.post(url, headers=headers, data=body_bytes, cookies=auth.cookie,
                         verify=tls_verify(), timeout=30)
    if resp.status_code != 200:
        raise RuntimeError(f"CommitUploadInner HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    err = (data.get("ResponseMetadata") or {}).get("Error")
    if err:
        raise RuntimeError(f"CommitUploadInner 业务错误: {err.get('Code')} {err.get('Message')}")
    results = ((data.get("Result") or {}).get("Results") or [])
    if not results:
        raise RuntimeError(f"CommitUploadInner 响应缺 Results: {str(data)[:300]}")
    r0 = results[0]
    enc = r0.get("Encryption") or {}
    if not enc.get("Uri") or not enc.get("SecretKey"):
        raise RuntimeError(f"CommitUploadInner 响应缺 Encryption.Uri/SecretKey: {str(r0)[:300]}")
    logger.info(f"[img-send] ④ Commit 成功 uri={enc['Uri'][:40]}… alg={enc.get('Algorithm')}")
    return {
        "uri": enc["Uri"],
        "secret_key": enc["SecretKey"],
        "source_md5": enc.get("SourceMd5", ""),
        "algorithm": enc.get("Algorithm", ""),
        "extra": enc.get("Extra") or {},
    }


# ----------------------------------------------------------------------------
# ⑤ batch_build_image（可选：给自家前端渲染用的签名 URL）
# ----------------------------------------------------------------------------
def build_signed_url(auth, oid: str):
    """⑤ 换取可访问的 p*-sign.douyinpic.com 签名 URL。失败返回 None（不影响发送）。"""
    params = {
        "device_platform": "webapp",
        "aid": "6383",
        "channel": "channel_pc_web",
        "pc_client_type": "1",
    }
    webid = (auth.cookie or {}).get("s_v_web_id", "")
    _cookie_ms = (auth.cookie or {}).get("msToken") or ""
    params["msToken"] = _cookie_ms if _cookie_ms else generate_msToken()
    if webid:
        params["verifyFp"] = webid
        params["fp"] = webid
    body_obj = {"convert_params": [
        {"uri": oid, "format": "tplv-x-get:large.image", "tpl": "%s://%v/%v~%v"}]}
    headers = {
        "accept": "application/json, text/plain, */*",
        "content-type": "application/json",
        "origin": "https://www.douyin.com",
        "referer": "https://www.douyin.com/",
        "user-agent": _UA,
    }
    try:
        resp = requests.post(
            "https://www.douyin.com/aweme/v1/web/privacy/batch_build_image/",
            params=params, headers=headers, cookies=auth.cookie,
            data=json.dumps(body_obj, separators=(",", ":")), verify=tls_verify(), timeout=30)
        if resp.status_code != 200:
            logger.warning(f"[SEND-019] " + f"[img-send] ⑤ batch_build_image HTTP {resp.status_code}")
            return None
        packs = ((resp.json().get("data") or {}).get("pack_results") or [])
        urls = (packs[0].get("UrlList") or []) if packs else []
        url = urls[0] if urls else None
        if url:
            logger.info(f"[img-send] ⑤ 拿到签名 URL: {url[:80]}…")
        return url
    except Exception as e:
        logger.warning(f"[SEND-020] " + f"[img-send] ⑤ batch_build_image 失败（忽略）: {e}")
        return None


# ----------------------------------------------------------------------------
# ⑥ message/send（protobuf, message_type=27, aweType=2702）
# ----------------------------------------------------------------------------
def build_image_content_json(md5: str, skey: str, oid: str, data_size: int,
                             width: int, height: int) -> str:
    """图片消息 content JSON（与真实抓包帧逐字段一致，紧凑分隔符）。"""
    content = {
        "resource_url": {
            "oid": oid,
            "skey": skey,
            "data_size": int(data_size),
            "md5": md5,
        },
        "cover_height": int(height),
        "cover_width": int(width),
        "check_pics": [],
        "md5": md5,
        "from_gallery": 1,
        "aweType": 2702,
    }
    return json.dumps(content, ensure_ascii=False, separators=(",", ":"))


def build_image_send_request(auth, conversation_id: str, conversation_short_id: int,
                             ticket: str, content_json: str,
                             client_message_id: str | None = None):
    """构建 cmd=100 发图 protobuf（message_type=27，与文本 7 不同）。

    签名公式与文本一致：content={json}&conversation_id=..&conversation_short_id=..
    """
    client_message_id = client_message_id or str(uuid.uuid4())
    request = ProtoBuilder.build_normal_request(auth, 100)
    body = request.body.send_message_body
    body.conversation_id = conversation_id
    body.conversation_type = 1
    body.conversation_short_id = conversation_short_id
    body.content = content_json
    body.ext.append(RequestProto.ExtValue(
        key="s:client_message_id", value=client_message_id))
    body.ext.append(RequestProto.ExtValue(
        key="s:stime", value=str(generate_millisecond())))
    body.ext.append(RequestProto.ExtValue(
        key="s:mentioned_users", value=""))
    body.message_type = 27
    body.ticket = ticket
    body.client_message_id = client_message_id
    request.reuqest_sign = generate_req_sign({
        "sign_data": (f"content={content_json}"
                      f"&conversation_id={conversation_id}"
                      f"&conversation_short_id={conversation_short_id}"),
        "certType": "cookie",
        "scene": "web_protect",
    }, auth.private_key)
    return request


def send_image_message(auth, conversation_id: str, conversation_short_id: int,
                       ticket: str, *, oid: str, skey: str, md5: str,
                       data_size: int, width: int, height: int) -> tuple:
    """⑥ 发送图片消息。返回 (ok, detail)，与 DouyinAPI.send_msg 同风格。"""
    from google.protobuf.json_format import MessageToDict

    import static.Response_pb2 as ResponseProto
    content_json = build_image_content_json(md5, skey, oid, data_size, width, height)
    url = "https://imapi.douyin.com/v1/message/send"
    from builder.header import HeaderType
    headers = HeaderBuilder().build(HeaderType.PROTOBUF)
    headers.set_header("referer", "https://www.douyin.com/")
    request_proto = build_image_send_request(
        auth, conversation_id, conversation_short_id, ticket, content_json)
    webid = (auth.cookie or {}).get("s_v_web_id", "")
    _cookie_ms = (auth.cookie or {}).get("msToken") or ""
    params = {
        "verifyFp": webid,
        "fp": webid,
        "msToken": _cookie_ms if _cookie_ms else generate_msToken(),
    }
    query = splice_url(params)
    params["a_bogus"] = generate_a_bogus(query)
    resp = requests.post(url, params=params, headers=headers.get(), verify=tls_verify(),
                         cookies=auth.cookie,
                         data=request_proto.SerializeToString(), timeout=30)
    if resp.status_code != 200:
        return False, f"message/send HTTP {resp.status_code}: {resp.text[:200]}"
    response_proto = ResponseProto.Response()
    try:
        response_proto.ParseFromString(resp.content)
    except Exception as e:
        return False, f"message/send 响应解析失败: {e} | raw[:120]={resp.content[:120]!r}"
    resp_json = MessageToDict(response_proto, preserving_proto_field_name=True)
    if resp_json.get("message") == "OK":
        logger.info(f"[img-send] ⑥ 图片消息发送成功 conversation_id={conversation_id}")
        return True, "ok"
    from dy_apis.douyin_api import DouyinAPI
    detail = DouyinAPI._classify_send_fail(resp_json)
    logger.error("SEND-021", f"[img-send] ⑥ 发送失败 conversation_id={conversation_id} "
                 f"resp_json={resp_json}")
    return False, detail


# ----------------------------------------------------------------------------
# 图片尺寸
# ----------------------------------------------------------------------------
def _image_size(data: bytes, filename: str = "") -> tuple:
    """读取图片宽高。HEIC 走 pillow_heif；失败退化 (800, 600)。"""
    try:
        from PIL import Image as PILImage
        try:
            import pillow_heif
            pillow_heif.register_heif_opener()
        except Exception:
            pass
        with PILImage.open(io.BytesIO(data)) as im:
            return im.width, im.height
    except Exception as e:
        logger.warning(f"[SEND-022] " + f"[img-send] 读取图片尺寸失败（退化 800x600）: {e}")
        return 800, 600


# ----------------------------------------------------------------------------
# 编排：①-⑥ 全链路
# ----------------------------------------------------------------------------
def send_image(auth, peer_id: int, image_data: bytes,
               filename: str = "image.jpg") -> tuple:
    """给 peer_id 发一张图片（全链路）。

    返回 (ok, detail, info)。
    info: {oid, skey, md5, data_size, width, height, origin_url,
           conversation_id, conversation_short_id, ticket}
    """
    from dy_apis.douyin_api import DouyinAPI

    if not image_data:
        return False, "图片内容为空", {}
    if len(image_data) > 20 * 1024 * 1024:
        return False, "图片超过 20MB 限制", {}

    md5 = hashlib.md5(image_data).hexdigest()
    width, height = _image_size(image_data, filename)
    logger.info(f"[img-send] 开始发图 peer={peer_id} size={len(image_data)}B "
                f"md5={md5} {width}x{height}")

    # 会话要素（与文本发送同源：create_conversation 幂等，返回既有会话）
    conversation_id, short_id, ticket = DouyinAPI.create_conversation(auth, int(peer_id))
    logger.info(f"[img-send] 会话就绪 conv={conversation_id} short={short_id}")

    cfg = get_upload_config(auth)                       # ①
    logger.info(f"[img-send] ① STS 就绪 space={cfg['space_name']} "
                f"expire={cfg['expire_at']}")
    addr = apply_upload(auth, cfg, len(image_data))     # ②
    logger.info(f"[img-send] ② Apply 就绪 host={addr['upload_host']} "
                f"store={addr['store_uri'][:40]}…")
    upload_to_tos(addr, image_data)                     # ③
    enc = commit_upload(auth, cfg, addr["session_key"])  # ④
    if enc["source_md5"] and enc["source_md5"] != md5:
        logger.warning("SEND-023", f"[img-send] ④ SourceMd5({enc['source_md5']}) 与本地 md5({md5}) "
                       f"不一致，以本地为准")
    ok, detail = send_image_message(                    # ⑥
        auth, conversation_id, short_id, ticket,
        oid=enc["uri"], skey=enc["secret_key"], md5=md5,
        data_size=len(image_data), width=width, height=height)
    if not ok:
        return False, detail, {}
    origin_url = build_signed_url(auth, enc["uri"])     # ⑤（渲染用，可失败）
    info = {
        "oid": enc["uri"],
        "skey": enc["secret_key"],
        "md5": md5,
        "data_size": len(image_data),
        "width": width,
        "height": height,
        "origin_url": origin_url,
        "conversation_id": conversation_id,
        "conversation_short_id": short_id,
        "ticket": ticket,
    }
    return True, "ok", info


# ============================================================================
# 【ground truth】真实抓包图片消息帧（base64 模板解码，blackboxprotobuf 反解）：
#   send_message_body:
#     conversation_type  = 1
#     message_type       = 27          ← 图片 27，文本是 7
#     content            = {"resource_url":{"oid":"tos-cn-o-00061/f1420b…",
#                          "skey":"f1ff5195…64hex","data_size":1856228,
#                          "md5":"fd249f3a…"},
#                          "cover_height":2120,"cover_width":1190,
#                          "check_pics":[],"md5":"fd249f3a…",
#                          "from_gallery":1,"aweType":2702}
#     ext                = s:client_message_id / s:stime / s:mentioned_users
#   oid = ④ Encryption.Uri（≠ ③ 上传的 StoreUri，服务端重新加密的对象）
#   skey = ④ Encryption.SecretKey（AES-256-GCM，与接收侧 origin_image_resolver 同源）
#   md5 = ④ Encryption.SourceMd5（= 原文件 MD5）
# ============================================================================
