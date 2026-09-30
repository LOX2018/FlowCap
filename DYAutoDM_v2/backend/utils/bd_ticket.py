# -*- coding: utf-8 -*-
"""bd-ticket-guard

签名分两种（对齐 passport 的 zero.js `n2` / `deriveEcdhKey`）：
- ``hmac``：客户端 EC 私钥与服务端 ecies 证书公钥做 ECDH，经 HKDF-SHA256 得到 32 字节
  会话密钥，再对 sign_data 做 HMAC-SHA256。新版证书（``client_cert`` 形如 ``pub.<b64>``）
  走这条，请求头 ``bd-ticket-guard-web-sign-type: 1``。
- ``ecdsa``：直接用 EC 私钥对 sign_data 做 ECDSA-SHA256（DER），
  ``bd-ticket-guard-web-sign-type: 0``，仅在拿不到服务端证书时兜底。

2026-10-01（HC-16，ECDH/HMAC 移植）：
  本模块此前**只有 ECDSA 一条路径**，而 `builder/header.py` 已按
  `client_cert.startswith('pub.')` 把 `bd-ticket-guard-web-sign-type` 标成
  `'1'`（=hmac）⇒ **头声明 hmac、实际 ECDSA 签名**，服务端校验必失败。
  这是「只读接口通（readonly 不发 client-data）、写接口（弹幕/点赞）恒 403」
  的机理。本次把上游 cv-cat/DouYin_Spider 的 ECDH/HMAC 能力整段移植过来（保留
  上游注释与归因），并把 base64 改为标准 `b64encode`（上游注释：浏览器用
  btoa()，是 `+/` 不是 `-_`）。
"""

import base64
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from ecdsa import SigningKey, VerifyingKey, NIST256p
from ecdsa.util import sigencode_der, sigdecode_der

# P-256 的 SubjectPublicKeyInfo 前缀，其后紧跟 64 字节裸公钥点（X||Y）。
# 服务端 ecies 证书固定是 P-256，按此定位可免引入完整 X.509 解析依赖。
_P256_SPKI_PREFIX = bytes.fromhex(
    "3059301306072a8648ce3d020106082a8648ce3d03010703420004"
)

GET_CLIENT_CERT_API = "/passport/ticket_guard/get_client_cert/"


def _load_signing_key(prv) -> SigningKey:
    if "-----BEGIN" in prv:
        return SigningKey.from_pem(prv)
    return SigningKey.from_string(bytes.fromhex(prv), curve=NIST256p)


def get_ree_key(prv) -> str:
    sk = _load_signing_key(prv)
    vk = sk.get_verifying_key()
    return base64.b64encode(b"\x04" + vk.to_string()).decode()


def get_req_sign(e, prv) -> str:
    if isinstance(e, (dict, list)):
        e = json.dumps(e, ensure_ascii=False, separators=(",", ":"))
    sk = _load_signing_key(prv)
    signature = sk.sign(e.encode("utf-8"), hashfunc=hashlib.sha256, sigencode=sigencode_der)
    return base64.b64encode(signature).decode()


# ═══════════════════════════════════════════════════════════════════════════
# ECDH / HMAC（2026-10-01 从上游 cv-cat/DouYin_Spider utils/bd_ticket.py 移植）
# ═══════════════════════════════════════════════════════════════════════════

def _pem_body(pem: str) -> bytes:
    body = "".join(
        line for line in pem.strip().splitlines() if "-----" not in line
    )
    return base64.b64decode(body)


def _server_pub_point(server_cert: str) -> bytes:
    """从服务端证书里取出 65 字节未压缩公钥点（含 0x04 前缀）。

    兼容两种下发格式：完整 PEM 证书，或新版的 ``pub.<base64 裸公钥点>``。
    """
    if server_cert.startswith("pub."):
        return base64.b64decode(server_cert[4:])
    der = _pem_body(server_cert)
    idx = der.find(_P256_SPKI_PREFIX)
    if idx < 0:
        raise ValueError("服务端证书中未找到 P-256 公钥")
    start = idx + len(_P256_SPKI_PREFIX)
    return b"\x04" + der[start:start + 64]


def _hkdf_sha256(ikm: bytes, length: int = 32, salt: bytes = b"", info: bytes = b"") -> bytes:
    if not salt:
        salt = b"\x00" * hashlib.sha256().digest_size
    prk = hmac.new(salt, ikm, hashlib.sha256).digest()
    okm, block, counter = b"", b"", 1
    while len(okm) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        okm += block
        counter += 1
    return okm[:length]


def derive_ecdh_key(prv: str, server_cert: str) -> bytes:
    """ECDH(客户端私钥, 服务端证书公钥) -> HKDF-SHA256 -> 32 字节 HMAC 密钥。

    纯本地计算（**无网络**）。对同一 (prv, cert) **确定性**——ECDH 共享点由
    「私钥标量 × 服务端公钥点」唯一确定，HKDF 亦为确定性 KDF。
    """
    sk = _load_signing_key(prv)
    vk = VerifyingKey.from_string(_server_pub_point(server_cert), curve=NIST256p)
    shared_point = sk.privkey.secret_multiplier * vk.pubkey.point
    shared = shared_point.x().to_bytes(32, "big")
    return _hkdf_sha256(shared)


def get_req_sign_hmac(e, ecdh_key: bytes) -> str:
    """对 sign_data 做 HMAC-SHA256（ECDH 会话密钥），返回标准 base64。

    与 `get_req_sign`（ECDSA-DER）互斥：同一份 client-data 只能走其一，
    由 `generate_bd_ticket_client_data` 按 `ecdh_key` 是否存在二选一。
    """
    if isinstance(e, (dict, list)):
        e = json.dumps(e, ensure_ascii=False, separators=(",", ":"))
    return base64.b64encode(
        hmac.new(ecdh_key, e.encode("utf-8"), hashlib.sha256).digest()
    ).decode()


def _http():
    """延迟取 http 客户端。

    上游在模块级 `from utils import http_client as requests`；本项目**无**
    `utils/http_client` 模块（全仓唯一命中落在 vendor 目录），故改为**延迟导入**：
    优先 `utils.http_client`，回退本项目实际使用的 `requests`。保持「只有
    fetch_server_cert 出网时才需要」的惰性语义（离线门禁导入本模块不触发网络栈）。
    """
    try:
        from utils import http_client as requests  # type: ignore
        return requests
    except Exception:  # noqa: BLE001
        import requests  # type: ignore
        return requests


def fetch_server_cert(aid, cookie_str, origin="https://www.douyin.com",
                      user_agent=None, proxies=None, session_dtrait=None,
                      csrf_token=None, ms_token=None, referer=None):
    """取服务端 ecies 证书（浏览器的 get_client_cert）。

    🔴 **本函数会真实出网**打到抖音 `GET_CLIENT_CERT_API`。离线门禁/测试
    **严禁调用**（风控红线）——纯本地验证请用 `derive_ecdh_key` + fixture 证书。

    对齐 SDK：body 的键值对用 ``,`` 而非 ``&`` 连接（见 zero.js ``tu``）。

    :param session_dtrait: `x-tt-session-dtrait` 头，实录里这个请求是带的。
    :param csrf_token: `x-secsdk-csrf-token`，由 secsdk 的取 token 流程下发；
        没有就不发（实录里有，但缺它是否被拒尚未实测）。
    :param ms_token: optional session mssdk token. Chrome reuses the token
        obtained immediately before this request; generating a short random
        fallback changes the query shape.
    :param referer: page URL used as the HTTP Referer; callers can freeze it
        for a browser fixture replay.
    :return: (server_cert, server_sn)
    """
    # 延迟导入：utils.dy_util 会反向用到本模块，模块级导入会成环
    from utils.dy_util import generate_a_bogus, generate_msToken
    from utils.fingerprint import get_profile

    profile = get_profile()
    query = {
        "aid": str(aid),
        "is_from_ttaccountsdk": "1",
        "msToken": ms_token or generate_msToken(),
    }
    query["a_bogus"] = generate_a_bogus(urlencode(query))
    url = f"{origin}{GET_CLIENT_CERT_API}?{urlencode(query)}"
    # JS-set header order follows the ticket-guard capture. Chrome's network
    # layer additionally emits origin/fetch metadata and Cookie; curl_cffi's
    # default headers are disabled, so include those explicitly.
    # Chrome 151 reqid=494's JS-controlled header order is exactly:
    # x-tt-session-dtrait, referer, user-agent, accept,
    # x-secsdk-csrf-token, content-type.  The client-hint/cache headers that
    # older captures contained are not sent by this current request.
    headers = {
        "x-tt-session-dtrait": session_dtrait or "",
        "referer": referer or f"{origin}/",
        "user-agent": user_agent or profile["ua"],
        "accept": "application/json",
    }
    if csrf_token is None:
        # 实录里这个请求是带 x-secsdk-csrf-token 的，没传就自己去换一个
        try:
            from utils.dy_util import generate_csrf_token
            csrf_token, _ = generate_csrf_token(cookie_str or "")
        except Exception:
            csrf_token = ""
    if csrf_token:
        headers["x-secsdk-csrf-token"] = csrf_token
    # Keep the JS-set order from Chrome's ticket-guard capture:
    # Accept -> x-secsdk-csrf-token -> Content-Type.  The remaining fetch
    # metadata is added after this block; curl_cffi keeps the mapping order
    # and the cookie is inserted immediately after content-type below.
    headers.update({
        "content-type": "application/x-www-form-urlencoded",
        # These are network-layer additions observed after the controlled
        # headers; keep them explicit because curl_cffi default headers are
        # disabled, but do not add cache-control/pragma/sec-ch-ua*.
        "accept-language": "zh-CN,zh;q=0.9",
        "origin": origin,
        "priority": "u=1, i",
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    })
    # Put Cookie after content-type, matching Chrome's copied-cURL/wire
    # position. Never pass it through curl_cffi's cookie jar: that jar sorts
    # by its own insertion rules and loses browser order.
    with_cookie = {}
    for name, value in headers.items():
        with_cookie[name] = value
        if name == "content-type":
            with_cookie["cookie"] = cookie_str or ""
    requests = _http()
    resp = requests.post(url, headers=with_cookie,
                         data=f"server_data=1,aid={aid}",
                         verify=False, proxies=proxies, timeout=15)
    res_json = resp.json()
    if res_json.get("message") != "success":
        raise RuntimeError(f"获取服务端证书失败: {res_json}")
    data = res_json.get("data") or {}
    cert, sn = data.get("server_cert", ""), data.get("server_sn", "")
    if not cert:
        raise RuntimeError(f"服务端证书为空: {res_json}")
    return cert, sn


class _ClientDataResult(str):
    """client-data 载荷：本体是 str（向后兼容），额外携带本次实际用的签名算法。

    2026-10-01（HC-16）——这是「返回值方案 A（tuple）」的**向后兼容变体 A′**：
      上游返回裸 tuple `(client_data, algo_type)`。本项目不能照抄，因为既有门禁
      `test_live_write_credential_loader.py:121` 断言 `isinstance(out, str)`
      （该门禁本次**不在授权改动范围**，且被要求「必须继续绿」）。
      故用一个 **str 子类**承载两者：`str(...)` 语义完全不变（len/切片/json/
      requests 编码逐字节一致），算法真相挂在 `.algo_type` 上由调用方读取。

    ⚠️ **刻意不**覆写 `__iter__` / `__getitem__`：那样会让 `for ch in s` 之类
      的字符串迭代语义被偷偷改成「按 tuple 迭代」，属于隐性副作用。
      若日后允许同步改上述门禁，应退化为**纯 tuple**（上游形态），
      改动清单：`return _ClientDataResult(...)` → `return data, algo_type`
      + `builder/header.py` 两行解包 + loader 门禁一行 isinstance。
    """

    __slots__ = ("algo_type",)

    def __new__(cls, data: str, algo_type: str):
        obj = super().__new__(cls, data)
        obj.algo_type = algo_type
        return obj


def verify_req_sign(e, sig_b64: str, pub_hex: str) -> bool:
    if isinstance(e, (dict, list)):
        e = json.dumps(e, ensure_ascii=False, separators=(",", ":"))
    vk = VerifyingKey.from_string(bytes.fromhex(pub_hex[2:]), curve=NIST256p)
    try:
        return vk.verify(base64.b64decode(sig_b64), e.encode("utf-8"),
                        hashfunc=hashlib.sha256, sigdecode=sigdecode_der)
    except Exception:
        return False


def generate_bd_ticket_client_data(api: str, ticket: str, ts_sign: str, prv: str,
                                   ecdh_key: bytes = None, timestamp: int = None,
                                   t_trust: int = None):
    """生成 bd-ticket-guard-client-data。

    :param api: 请求 pathname（不含 query）。
    :param ecdh_key: 有则走 HMAC，无则回退 ECDSA。
    :param timestamp: 可注入（测试/回放用）；缺省取当前时间。
    :param t_trust: `_bd_ticket_crypt_cookie` 建立后置 1，否则不发该字段。
    :return: **`_ClientDataResult`（str 子类）**：本体即 client-data base64 串，
             另带 `.algo_type`（'hmac' / 'ecdsa'）。详见该类 docstring。

    🔴 2026-10-01（HC-16）**返回值方案：A′（str 子类携带 algo_type）**。
      原始诉求要求 A（tuple）/ B（单值）二选一，真实约束下两者都不可用：
        · **A 不可行**：既有门禁 `test_live_write_credential_loader.py:121`
          断言 `isinstance(out, str)`，该门禁本次**不在授权改动范围**且被要求
          「必须继续绿」。返回裸 tuple 会立刻让它变红。
        · **B 不可行**：B 的语义是「内部自己决定算法」，意味着 `builder/header.py`
          拿不到算法真相，只能继续靠 `client_cert.startswith('pub.')` **猜测**
          ——而「头声明 hmac、实际 ECDSA」正是本次要消灭的 403 根因。
      ⇒ 取 A′：`str` 子类本体（对 requests/json/isinstance 逐字节等价），
      算法真相由 `.algo_type` 显式外泄给 header **驱动** web-sign-type。
      这是在不侵入他人门禁的前提下，唯一能同时满足「契约不变」与
      「头-载荷一致」的形态。代价与退化路径见 `_ClientDataResult` docstring。

    ⚠️ `utils/dy_util.py` 的同名**包装**函数（返回裸 urlsafe str，无 ECDH 能力）
      不在本次改动范围（他人工作区），且**已被 header 绕开**——header 自
      2026-10-01 起直接 import 本模块的真实现。见报告「诚实标注」。
    """
    timestamp = int(time.time()) if timestamp is None else int(timestamp)
    # 2026-09-17 修补（OCR 审查 MEDIUM —— 待签串分隔符注入）
    # 2026-10-01 修正（**实测驱动的回归修复**）：原实现对 `ticket` 与 `api` **一律**
    #   拒绝 `&` 与 `=`，但真实 `ticket` 是 **base64**（实测
    #   `hash.mgTYBN0DfPL9gfxlJ…==`，49 字符，**必含 `=` padding**）⇒ 守卫**恒误拦**，
    #   把合法凭证判为非法，写接口（弹幕/点赞）全线抛
    #   `ValueError: ticket 含非法分隔符（&/=）`。上游 `cv-cat/DouYin_Spider`
    #   的对应函数**没有**该校验（同一待签串格式），即以 `=` 参与签名是**正常形态**。
    #
    #   按**注入可行性**分别判定（这才是守卫的本意）：
    #     · `&` —— 能**伪造额外键值对**（`ticket=a&path=/evil`）⇒ **必须拦**；
    #     · `=` —— 落在第一个 `=` 之后，只是「值的一部分」，**不产生新键**
    #       ⇒ 对 base64 的 `ticket` **必须放行**；对 `api`（契约是纯路径）仍拦。
    if "&" in str(ticket):
        raise ValueError("ticket 含非法分隔符（&），拒绝生成签名材料")
    if "&" in str(api) or "=" in str(api):
        raise ValueError("api 含非法字符（&/=），拒绝生成签名材料")
    res_sign = f"ticket={ticket}&path={api}&timestamp={timestamp}"
    if ecdh_key:
        req_sign, algo_type = get_req_sign_hmac(res_sign, ecdh_key), "hmac"
    else:
        req_sign, algo_type = get_req_sign(res_sign, prv), "ecdsa"
    p = {
        "ts_sign": ts_sign,
        "req_content": "ticket,path,timestamp",
        "req_sign": req_sign,
        "timestamp": timestamp,
    }
    # 新版 ticket-guard 在 `_bd_ticket_crypt_cookie` 已建立后追加这一项。
    # 它位于 timestamp 之后；无 trust cookie 的早期阶段仍保持旧四字段结构。
    if t_trust is not None:
        p["t_trust"] = int(t_trust)
    p = json.dumps(p, ensure_ascii=False, separators=(",", ":"))
    # 浏览器用 btoa()，是标准 base64（+/），不是 urlsafe（-_）
    return _ClientDataResult(base64.b64encode(p.encode("utf-8")).decode(), algo_type)


def ticket_guard_version(ts_sign: str) -> int:
    """bd-ticket-guard 的 web-version 由 ts_sign 前缀决定（源项目 zero.js `nQ`）。

    `ts.1` 前缀 -> 1，其余 -> 2。2026 起主流会话为 `ts.2`（web-version=2）。
    """
    return 1 if (ts_sign or "").startswith("ts.1") else 2


if __name__ == "__main__":
    sk = SigningKey.generate(curve=NIST256p)
    pem = sk.to_pem().decode()
    pub_hex = ("04" + sk.get_verifying_key().to_string().hex())
    msg = "ticket=abc&path=/aweme/v1/web/aweme/post/&timestamp=1720000000"
    sig = get_req_sign(msg, pem)
    print("ree_key :", get_ree_key(pem)[:40], "...")
    print("req_sign:", sig[:40], "...")
    print("verify  :", "PASS" if verify_req_sign(msg, sig, pub_hex) else "FAIL")
    # ECDH/HMAC 自检（**纯本地**，不出网）
    srv = SigningKey.generate(curve=NIST256p)
    cert = "pub." + base64.b64encode(
        b"\x04" + srv.get_verifying_key().to_string()).decode()
    k1 = derive_ecdh_key(pem, cert)
    k2 = derive_ecdh_key(pem, cert)
    print("ecdh    :", "PASS" if (len(k1) == 32 and k1 == k2) else "FAIL")
    _cd, algo = generate_bd_ticket_client_data(
        "/webcast/room/chat/", "hash.abc==", "ts.2.x", pem,
        ecdh_key=k1, timestamp=1720000000)
    print("algo    :", algo)
    _cd0, algo0 = generate_bd_ticket_client_data(
        "/webcast/room/chat/", "hash.abc==", "ts.2.x", pem, timestamp=1720000000)
    print("fallback:", algo0)
