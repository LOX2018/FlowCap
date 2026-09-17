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
    # 2026-09-17 修补（OCR 审查 MEDIUM —— 待签串分隔符注入）：
    # `ticket` / `api` 直接插进 `k=v&k=v` 形式的待签串，若其中含 `&` 或 `=`
    # 就能**伪造额外键值对**（改变被签名内容的语义）。这两者的取值本应是
    # 不含分隔符的 token / 路径，此处按契约显式校验（fail-fast），
    # 而不是静默产出被篡改的签名材料。
    for _n, _v in (("ticket", ticket), ("api", api)):
        if "&" in str(_v) or "=" in str(_v):
            raise ValueError(f"{_n} 含非法分隔符（&/=），拒绝生成签名材料")
    res_sign = f"ticket={ticket}&path={api}&timestamp={timestamp}"
    p = {
        "ts_sign": ts_sign,
        "req_content": "ticket,path,timestamp",
        "req_sign": get_req_sign(res_sign, prv),
        "timestamp": timestamp,
    }
    p = json.dumps(p, ensure_ascii=False, separators=(",", ":"))
    return base64.urlsafe_b64encode(p.encode("utf-8")).decode()


if __name__ == "__main__":
    sk = SigningKey.generate(curve=NIST256p)
    pem = sk.to_pem().decode()
    pub_hex = ("04" + sk.get_verifying_key().to_string().hex())
    msg = "ticket=abc&path=/aweme/v1/web/aweme/post/&timestamp=1720000000"
    sig = get_req_sign(msg, pem)
    print("ree_key :", get_ree_key(pem)[:40], "...")
    print("req_sign:", sig[:40], "...")
    print("verify  :", "PASS" if verify_req_sign(msg, sig, pub_hex) else "FAIL")
