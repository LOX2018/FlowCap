# -*- coding: utf-8 -*-
"""bd-ticket-guard"""

import base64
import json
import time

from ecdsa import SigningKey, VerifyingKey, NIST256p
from ecdsa.util import sigencode_der, sigdecode_der


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
    import hashlib
    sk = _load_signing_key(prv)
    signature = sk.sign(e.encode("utf-8"), hashfunc=hashlib.sha256, sigencode=sigencode_der)
    return base64.b64encode(signature).decode()


def verify_req_sign(e, sig_b64: str, pub_hex: str) -> bool:
    import hashlib
    if isinstance(e, (dict, list)):
        e = json.dumps(e, ensure_ascii=False, separators=(",", ":"))
    vk = VerifyingKey.from_string(bytes.fromhex(pub_hex[2:]), curve=NIST256p)
    try:
        return vk.verify(base64.b64decode(sig_b64), e.encode("utf-8"),
                        hashfunc=hashlib.sha256, sigdecode=sigdecode_der)
    except Exception:
        return False


def generate_bd_ticket_client_data(api: str, ticket: str, ts_sign: str, prv: str) -> str:
    timestamp = int(time.time())
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
    p = {
        "ts_sign": ts_sign,
        "req_content": "ticket,path,timestamp",
        "req_sign": get_req_sign(res_sign, prv),
        "timestamp": timestamp,
    }
    p = json.dumps(p, ensure_ascii=False, separators=(",", ":"))
    return base64.urlsafe_b64encode(p.encode("utf-8")).decode()


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
