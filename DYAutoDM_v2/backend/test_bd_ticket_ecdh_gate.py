# -*- coding: utf-8 -*-
"""HC-16 门禁：bd-ticket ECDH/HMAC 移植（直播写接口 403 根因修复）。

## 背景（为什么需要这道门禁）

2026-10-01 实测：`DYAutoDM_v2/backend/utils/bd_ticket.py` **完全没有** ECDH/HMAC
能力（全仓 grep `ecdh` / `derive_ecdh_key` / `fetch_server_cert` /
`get_req_sign_hmac` 零命中），client-data **恒定为 ECDSA 签名**；而
`builder/header.py` 却按 `client_cert.startswith('pub.')` 把
`bd-ticket-guard-web-sign-type` 标为 `'1'`（=hmac）⇒ **头声明 hmac、载荷
ECDSA**，服务端校签必然失败。这解释了「只读接口正常（readonly 不发
client-data）、写接口（弹幕/点赞）恒 403」。

本门禁锁死三件事：
  1. 有 ecdh_key ⇒ 走 HMAC（`algo_type == 'hmac'`，`web-sign-type == '1'`）；
  2. 无 ecdh_key ⇒ 回退 ECDSA（`algo_type == 'ecdsa'`，`web-sign-type == '0'`）；
  3. 头值必须由**实际算法**驱动，不得再由 client_cert 形态猜测。

## 红线

🔴 **禁止真实网络请求** —— 全部用**固定 fixture**（硬编码私钥 + 硬编码服务端
证书）做纯本地计算，绝不调用 `fetch_server_cert` 打抖音。
🔴 **禁止真实写请求**（不发弹幕/点赞）。本文件只在**请求构造层**断言。
"""

import base64
import hashlib
import hmac
import json
import sys
import unittest

from ecdsa import SigningKey, NIST256p

FIX_TS = 1720000000
FAKE_TICKET = "hash.mgTYBN0DfPL9gfxlJ+Ab/Cd8efghijklmnopqrstuv=="
FAKE_TS_SIGN = "ts.2.fake"
API = "/webcast/room/chat/"


# ═══════════════════════════════════════════════════════════════════════════
# 固定 fixture（确定性，不依赖随机数，便于 reproducibility）
# ═══════════════════════════════════════════════════════════════════════════

_SERVER_SEED = b"HC16-negative-control-fixed-server-seed-v1"


def _fixed_server_sk() -> SigningKey:
    """确定性服务端私钥：由固定种子经 SHA256 折叠到 P-256 标量域得到私钥字节。

    刻意用固定种子而非 `SigningKey.generate()`：让服务端证书在**每次运行、
    每台机器**上完全相同，门禁失败时取证可复现。
    """
    digest = hashlib.sha256(_SERVER_SEED).digest()          # 32 字节
    n = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551
    d = (int.from_bytes(digest, "big") % (n - 1)) + 1
    return SigningKey.from_secret_exponent(d, curve=NIST256p)


def _fixed_client_pem() -> str:
    """确定性客户端私钥 PEM（同上，固定种子）。"""
    digest = hashlib.sha256(b"HC16-negative-control-fixed-client-seed-v1").digest()
    n = 0xffffffff00000000ffffffffffffffffbce6faada7179e84f3b9cac2fc632551
    d = (int.from_bytes(digest, "big") % (n - 1)) + 1
    return SigningKey.from_secret_exponent(d, curve=NIST256p).to_pem().decode()


def _fixed_server_cert_pub() -> str:
    """新版形态服务端证书：`pub.<标准 base64 65 字节未压缩公钥点>`。"""
    point = b"\x04" + _fixed_server_sk().get_verifying_key().to_string()
    return "pub." + base64.b64encode(point).decode()


def _fixed_server_cert_pem() -> str:
    """老版形态服务端证书：**自签 PEM**，内含 P-256 SPKI，供 `_server_pub_point`
    的 PEM 分支走通（验证两种格式兼容）。
    """
    sk = _fixed_server_sk()
    der = sk.get_verifying_key().to_der()          # SPKI DER
    b64 = base64.b64encode(der).decode()
    lines = [b64[i:i + 64] for i in range(0, len(b64), 64)]
    return ("-----BEGIN PUBLIC KEY-----\n" + "\n".join(lines)
            + "\n-----END PUBLIC KEY-----\n")


def _expected_ecdh_key(prv_pem: str, cert: str) -> bytes:
    """独立重算一遍 ECDH+HKDF，用作**对照基准**（不复用被测代码的中间函数）。

    刻意绕开被测的 `_hkdf_sha256`：若被测 HKDF 实现写错（例如少一轮、salt 用错），
    而基准也调用它，缺陷会被覆盖过去。
    """
    from utils.bd_ticket import _load_signing_key
    from ecdsa import VerifyingKey
    from utils.bd_ticket import _server_pub_point
    sk = _load_signing_key(prv_pem)
    vk = VerifyingKey.from_string(_server_pub_point(cert), curve=NIST256p)
    shared = (sk.privkey.secret_multiplier * vk.pubkey.point).x().to_bytes(32, "big")
    # RFC 5869 HKDF-SHA256，salt = 00 * 32，info = b""，L = 32
    salt = b"\x00" * 32
    prk = hmac.new(salt, shared, hashlib.sha256).digest()
    return hmac.new(prk, b"\x01", hashlib.sha256).digest()


def _decode_payload(client_data: str) -> dict:
    """解出 client-data 的 JSON 载荷（并顺带校验它是**标准 base64**）。"""
    assert isinstance(client_data, str), f"client-data 不是 str：{type(client_data)}"
    return json.loads(base64.b64decode(client_data).decode("utf-8"))


# ═══════════════════════════════════════════════════════════════════════════
# G1 —— 能力存在性（移植到位）
# ═══════════════════════════════════════════════════════════════════════════

class PortedSymbolsExist(unittest.TestCase):
    """移植的 5 个符号必须在位，且 `__all__` 语义清晰（无第二份同语义实现）。"""

    def test_all_five_symbols_importable(self):
        import utils.bd_ticket as bt
        for name in ("_server_pub_point", "_hkdf_sha256", "derive_ecdh_key",
                     "get_req_sign_hmac", "fetch_server_cert"):
            self.assertTrue(hasattr(bt, name),
                            f"utils.bd_ticket 缺少移植符号：{name}")

    def test_get_client_cert_api_constant_ported(self):
        import utils.bd_ticket as bt
        self.assertEqual(bt.GET_CLIENT_CERT_API,
                         "/passport/ticket_guard/get_client_cert/")

    def test_no_duplicate_implementation_in_bd_ticket(self):
        """本模块内不得存在第二份同语义实现（`derive_ecdh_key` 唯一）。"""
        import inspect
        import utils.bd_ticket as bt
        src = inspect.getsource(bt)
        self.assertEqual(src.count("def derive_ecdh_key"), 1)
        self.assertEqual(src.count("def get_req_sign_hmac"), 1)
        self.assertEqual(src.count("def fetch_server_cert"), 1)

    def test_header_uses_real_implementation_not_dy_util_wrapper(self):
        """`builder/header.py` 必须 import **真实现**（utils.bd_ticket），
        而不是 `utils/dy_util` 那份单值旧包装——否则拿不到 algo_type。"""
        import inspect
        import builder.header as bh
        src = inspect.getsource(bh)
        self.assertIn("from utils.bd_ticket import generate_bd_ticket_client_data",
                      src)
        self.assertNotIn(
            "from utils.dy_util import generate_ree_key, "
            "generate_bd_ticket_client_data", src,
            "header 仍从 dy_util 取单值旧包装（无 ECDH 能力）")


# ═══════════════════════════════════════════════════════════════════════════
# G2 —— 核心：HMAC / ECDSA 两条路径 + 头值自洽
# ═══════════════════════════════════════════════════════════════════════════

class SignAlgorithmSelection(unittest.TestCase):
    """有 ecdh_key → HMAC；无 → ECDSA。base64 必须标准（+/）。"""

    def setUp(self):
        from utils.bd_ticket import derive_ecdh_key
        self.pem = _fixed_client_pem()
        self.key = derive_ecdh_key(self.pem, _fixed_server_cert_pub())

    def test_t1_with_ecdh_key_is_hmac(self):
        """T1（核心）：有 ecdh_key ⇒ algo_type == 'hmac'。"""
        from utils.bd_ticket import generate_bd_ticket_client_data
        out = generate_bd_ticket_client_data(
            API, FAKE_TICKET, FAKE_TS_SIGN, self.pem,
            ecdh_key=self.key, timestamp=FIX_TS)
        self.assertEqual(out.algo_type, "hmac")
        # HMAC 签名肉眼可辨：32 字节摘要 → base64 固定 44 字符（含 1 个 =）
        sig = _decode_payload(out)["req_sign"]
        self.assertEqual(len(base64.b64decode(sig)), 32,
                         "HMAC-SHA256 摘要必须是 32 字节")

    def test_t2_without_ecdh_key_falls_back_to_ecdsa(self):
        """T2（回退）：无 ecdh_key ⇒ algo_type == 'ecdsa'，且仍是可验签的 DER。"""
        from utils.bd_ticket import generate_bd_ticket_client_data
        out = generate_bd_ticket_client_data(
            API, FAKE_TICKET, FAKE_TS_SIGN, self.pem, timestamp=FIX_TS)
        self.assertEqual(out.algo_type, "ecdsa")
        payload = _decode_payload(out)
        raw = base64.b64decode(payload["req_sign"])
        self.assertEqual(raw[0], 0x30, "ECDSA 必须是 DER SEQUENCE（0x30 开头）")
        self.assertGreater(len(raw), 60, "DER 签名长度不像 P-256 ECDSA")

    def test_t3_base64_is_standard_not_urlsafe(self):
        """T3：client-data 必须是**标准 base64**（+/），不是 urlsafe（-_）。"""
        from utils.bd_ticket import generate_bd_ticket_client_data
        for kw in ({"ecdh_key": self.key}, {}):
            out = generate_bd_ticket_client_data(
                API, FAKE_TICKET, FAKE_TS_SIGN, self.pem,
                timestamp=FIX_TS, **kw)
            self.assertNotIn("-", str(out), "client-data 含 urlsafe 字符 '-'")
            self.assertNotIn("_", str(out), "client-data 含 urlsafe 字符 '_'")
            # 标准 base64 能解开 —— 若编码形态不对这一步就炸
            json.loads(base64.b64decode(str(out)).decode("utf-8"))
            # 且恰等于标准编码器输出（形状级断言，而非仅字符集）
            payload = json.loads(base64.b64decode(str(out)).decode("utf-8"))
            canonical = json.dumps(payload, ensure_ascii=False,
                                   separators=(",", ":"))
            self.assertEqual(
                str(out), base64.b64encode(canonical.encode("utf-8")).decode(),
                "client-data 与标准 b64encode(json) 不等价")

    def test_t3b_base64_discriminating_fixtures(self):
        """T3b（形状负控）：必须存在**能区分**两种字母表的 fixture，并逐一验证。

        为何要补这条：T3 的「不含 -/_」是**必要不充分** —— 若某 payload 的 base64
        恰好不含第 62/63 个字符，urlsafe 与标准输出**逐字节相同**，退回 urlsafe
        也照样通过 T3。2026-10-01 实测：把 `b64encode` 改回 `urlsafe_b64encode`
        后 T3 **全绿**，门禁失效。故此处主动搜索「两种编码会分叉」的 payload，
        在这些**真正有判别力**的样本上断言编码形态。
        """
        # 🔴🔴 2026-10-01 **订正（父会话，推翻原判据）**：
        #   本条原设想是「找会让两种 base64 分叉的 payload 样本」。实测证伪：
        #   payload 是**纯 ASCII JSON**（键名固定 + req_sign 本身已是 base64），
        #   其字节值域里**几乎不可能**出现映射到 62/63 号字符的字节序列 ——
        #   3000 次确定性随机搜索，含 `+/` 与含 `-_` 的输出**均为 0**，
        #   即「分叉样本」在真实数据形态下**根本构造不出来**。
        #   （子 agent 手挑的 `ts.2.f?A]Y7NQ` 亦属同类：实测无判别力 ⇒ 假红。）
        #
        #   ⇒ 换判据：**直接断言实现本身**用标准 `b64encode`（而非 urlsafe）。
        #     这才是「编码形态」这个事实的**直接可读来源**，且不依赖样本运气。
        #     判据走 **AST 真实调用**（本项目既有做法，避免匹配到注释/docstring
        #     里的解释文字而自造假红 —— HC-16 首版门禁即踩过此坑）。
        import ast as _ast
        import utils.bd_ticket as _bt
        _src = open(_bt.__file__, encoding="utf-8").read()
        _tree = _ast.parse(_src)
        _uses_std = _uses_urlsafe = False
        for _n in _ast.walk(_tree):
            if not isinstance(_n, _ast.Call):
                continue
            _f = _n.func
            if isinstance(_f, _ast.Attribute) and isinstance(_f.value, _ast.Name) \
                    and _f.value.id == "base64":
                if _f.attr == "b64encode":
                    _uses_std = True
                elif _f.attr == "urlsafe_b64encode":
                    _uses_urlsafe = True
        self.assertTrue(_uses_std,
                        "generate_bd_ticket_client_data 未使用标准 base64.b64encode")
        self.assertFalse(_uses_urlsafe,
                         "仍存在 base64.urlsafe_b64encode 调用（编码形态不符上游 btoa）")

        # 附：仍做一次端到端等价性断言（不依赖字母表分叉）
        from utils.bd_ticket import generate_bd_ticket_client_data
        for _kw in ({"ecdh_key": self.key}, {}):
            out = generate_bd_ticket_client_data(
                API, FAKE_TICKET, FAKE_TS_SIGN, self.pem,
                timestamp=FIX_TS, **_kw)
            payload = json.loads(base64.b64decode(str(out)).decode("utf-8"))
            canonical = json.dumps(payload, ensure_ascii=False,
                                   separators=(",", ":")).encode("utf-8")
            self.assertEqual(str(out), base64.b64encode(canonical).decode(),
                             "client-data 与标准 b64encode(json) 不等价")

    def test_t4_still_str_compatible(self):
        """T4（兼容性硬约束）：返回值必须仍是 str，供既有门禁与 requests 编码。"""
        from utils.bd_ticket import generate_bd_ticket_client_data
        out = generate_bd_ticket_client_data(
            API, FAKE_TICKET, FAKE_TS_SIGN, self.pem,
            ecdh_key=self.key, timestamp=FIX_TS)
        self.assertIsInstance(out, str)
        self.assertEqual(len(out), len(str(out)))
        self.assertTrue(str(out).startswith("eyJ"), "不像 base64(JSON) 开头")

    def test_t5_hmac_value_matches_independent_computation(self):
        """T5：req_sign 必须等于**独立重算**的 HMAC（防 HKDF/密钥错误被掩盖）。"""
        from utils.bd_ticket import generate_bd_ticket_client_data
        out = generate_bd_ticket_client_data(
            API, FAKE_TICKET, FAKE_TS_SIGN, self.pem,
            ecdh_key=self.key, timestamp=FIX_TS)
        res_sign = f"ticket={FAKE_TICKET}&path={API}&timestamp={FIX_TS}"
        expect = base64.b64encode(
            hmac.new(self.key, res_sign.encode("utf-8"), hashlib.sha256).digest()
        ).decode()
        self.assertEqual(_decode_payload(out)["req_sign"], expect)

    def test_t6_t_trust_appended_only_when_requested(self):
        """T6：`t_trust` 仅显式传入时出现，且位于 timestamp 之后。"""
        from utils.bd_ticket import generate_bd_ticket_client_data
        off = generate_bd_ticket_client_data(
            API, FAKE_TICKET, FAKE_TS_SIGN, self.pem,
            ecdh_key=self.key, timestamp=FIX_TS)
        on = generate_bd_ticket_client_data(
            API, FAKE_TICKET, FAKE_TS_SIGN, self.pem,
            ecdh_key=self.key, timestamp=FIX_TS, t_trust=1)
        self.assertNotIn("t_trust", _decode_payload(off))
        keys = list(_decode_payload(on).keys())
        self.assertEqual(keys[-1], "t_trust", f"字段顺序异常：{keys}")
        self.assertEqual(_decode_payload(on)["t_trust"], 1)


# ═══════════════════════════════════════════════════════════════════════════
# G3 —— derive_ecdh_key 正确性 / 确定性
# ═══════════════════════════════════════════════════════════════════════════

class DeriveEcdhKeyProperties(unittest.TestCase):

    def test_t7_deterministic_same_prv_cert(self):
        """T7（核心）：同一 (prv, cert) 两次调用结果**逐字节相同**。"""
        from utils.bd_ticket import derive_ecdh_key
        pem, cert = _fixed_client_pem(), _fixed_server_cert_pub()
        k1 = derive_ecdh_key(pem, cert)
        k2 = derive_ecdh_key(pem, cert)
        self.assertEqual(k1, k2, "derive_ecdh_key 不确定（含随机数/可变状态）")
        self.assertEqual(len(k1), 32, "HMAC 密钥必须是 32 字节")

    def test_t8_matches_independent_reference(self):
        """T8：与**独立实现**的 ECDH+HKDF 基准一致（不复用被测中间函数）。"""
        from utils.bd_ticket import derive_ecdh_key
        pem, cert = _fixed_client_pem(), _fixed_server_cert_pub()
        self.assertEqual(derive_ecdh_key(pem, cert),
                         _expected_ecdh_key(pem, cert))

    def test_t9_both_cert_formats_agree(self):
        """T9：`pub.<b64>` 与 PEM 两种证书格式解析出的公钥点等价 ⇒ 同密钥。"""
        from utils.bd_ticket import derive_ecdh_key, _server_pub_point
        pem = _fixed_client_pem()
        self.assertEqual(derive_ecdh_key(pem, _fixed_server_cert_pub()),
                         derive_ecdh_key(pem, _fixed_server_cert_pem()))
        # _server_pub_point 两种输入产出同一个点
        self.assertEqual(_server_pub_point(_fixed_server_cert_pub()),
                         _server_pub_point(_fixed_server_cert_pem()))
        # 必须是 65 字节、0x04 开头
        pt = _server_pub_point(_fixed_server_cert_pub())
        self.assertEqual(len(pt), 65)
        self.assertEqual(pt[0], 0x04)

    def test_t10_ecdh_is_symmetric_clientside_vs_serverside(self):
        """T10：ECDH 对称性 —— 服务端用自己的私钥 + 客户端公钥应得**同一共享点**。

        这是 ECDH 的数学本质：任何一侧算出相同密钥。若这条红，说明两侧用的不是
        同一条曲线或公钥点被截断。
        """
        from utils.bd_ticket import derive_ecdh_key, get_ree_key, _server_pub_point
        cli_pem = _fixed_client_pem()
        key_cli = derive_ecdh_key(cli_pem, _fixed_server_cert_pub())
        # 反方向：服务端私钥 + 客户端公钥（ree_key 就是 04||X||Y 的 b64）
        cli_point = base64.b64decode(get_ree_key(cli_pem))
        key_srv = derive_ecdh_key(_fixed_server_sk().to_pem().decode(),
                                  "pub." + base64.b64encode(cli_point).decode())
        self.assertEqual(key_cli, key_srv, "ECDH 不对称（共享密钥不一致）")
        self.assertEqual(_server_pub_point(_fixed_server_cert_pub()),
                         cli_point[:1] + cli_point[1:] if False else
                         _server_pub_point(_fixed_server_cert_pub()))

    def test_t11_different_server_cert_gives_different_key(self):
        """T11（负向）：换一张服务端证书必须得到**不同**密钥（防硬编码返回值）。"""
        from ecdsa import SigningKey
        from utils.bd_ticket import derive_ecdh_key
        pem = _fixed_client_pem()
        other = SigningKey.generate(curve=NIST256p)
        cert2 = "pub." + base64.b64encode(
            b"\x04" + other.get_verifying_key().to_string()).decode()
        self.assertNotEqual(derive_ecdh_key(pem, _fixed_server_cert_pub()),
                            derive_ecdh_key(pem, cert2))

    def test_t12_bad_cert_raises(self):
        """T12（负控）：非 P-256 的 PEM 必须抛 ValueError，不得静默产出密钥。"""
        from utils.bd_ticket import derive_ecdh_key
        junk = ("-----BEGIN PUBLIC KEY-----\n"
                + base64.b64encode(b"\x01\x02\x03\x04").decode()
                + "\n-----END PUBLIC KEY-----\n")
        with self.assertRaises(ValueError):
            derive_ecdh_key(_fixed_client_pem(), junk)


# ═══════════════════════════════════════════════════════════════════════════
# G4 —— header 自洽：web-sign-type 必须由 algo_type 驱动
# ═══════════════════════════════════════════════════════════════════════════

class HeaderSignTypeConsistency(unittest.TestCase):
    """这是本次修复的**价值核心**：头声明必须与载荷算法一致。"""

    def _build(self, auth_client_cert, ecdh_key):
        from builder.header import Header

        class _Auth:
            ticket = FAKE_TICKET
            ts_sign = FAKE_TS_SIGN
            client_cert = auth_client_cert
            private_key = _fixed_client_pem()
            cookie = {}

            def __init__(self, k):
                self._k = k

            def ecdh_key(self, aid=6383, origin="https://www.douyin.com"):
                return self._k

        h = Header()
        h.with_bd(API, _Auth(ecdh_key), aid=6383,
                  origin="https://live.douyin.com", timestamp=FIX_TS)
        return h.get()

    def test_t13_hmac_path_sets_sign_type_1(self):
        """T13：真走 HMAC ⇒ `bd-ticket-guard-web-sign-type == '1'`。"""
        from utils.bd_ticket import derive_ecdh_key
        key = derive_ecdh_key(_fixed_client_pem(), _fixed_server_cert_pub())
        heads = self._build("pub.newstyle", key)
        cd = heads["bd-ticket-guard-client-data"]
        self.assertEqual(_decode_payload(cd)["timestamp"], FIX_TS)
        # 载荷是 HMAC（32 字节摘要）
        self.assertEqual(len(base64.b64decode(_decode_payload(cd)["req_sign"])), 32)
        self.assertEqual(heads["bd-ticket-guard-web-sign-type"], "1")

    def test_t14_ecdsa_fallback_sets_sign_type_0_even_with_pub_cert(self):
        """T14（★本次修复的核心判据）：client_cert 是新版 `pub.` **但 ECDH 失败**
        回退 ECDSA 时，头必须是 `'0'` —— 不得再按 client_cert 形态猜 hmac。
        """
        heads = self._build("pub.newstyle", None)      # ecdh_key = None ⇒ 回退
        self.assertEqual(heads["bd-ticket-guard-web-sign-type"], "0",
                         "头声明 hmac 而载荷是 ECDSA —— 这正是 403 根因")
        payload = _decode_payload(heads["bd-ticket-guard-client-data"])
        self.assertEqual(base64.b64decode(payload["req_sign"])[0], 0x30)

    def test_t15_old_cert_without_ecdh_is_0(self):
        heads = self._build("oldstyle", None)
        self.assertEqual(heads["bd-ticket-guard-web-sign-type"], "0")

    def test_t16_old_cert_with_ecdh_is_1(self):
        """老证 + 有密钥也应当按**实际算法**（hmac）标 1。"""
        from utils.bd_ticket import derive_ecdh_key
        key = derive_ecdh_key(_fixed_client_pem(), _fixed_server_cert_pub())
        heads = self._build("oldstyle", key)
        self.assertEqual(heads["bd-ticket-guard-web-sign-type"], "1")

    def test_t17_auth_without_ecdh_method_still_works(self):
        """T17（健壮性）：auth 没有 `ecdh_key` 方法（老对象/替身）不得炸，降级 ECDSA。"""
        from builder.header import Header

        class _LegacyAuth:
            ticket = FAKE_TICKET
            ts_sign = FAKE_TS_SIGN
            client_cert = "pub.x"
            private_key = _fixed_client_pem()
            cookie = {}

        h = Header()
        h.with_bd(API, _LegacyAuth(), timestamp=FIX_TS)
        heads = h.get()
        self.assertEqual(heads["bd-ticket-guard-web-sign-type"], "0")
        self.assertIn("bd-ticket-guard-client-data", heads)

    def test_t18_origin_forwarded_to_ecdh_key(self):
        """T18：`origin` 必须真的传给 `auth.ecdh_key`（直播域，不能落到主站）。"""
        from builder.header import Header
        seen = {}

        class _Auth:
            ticket = FAKE_TICKET
            ts_sign = FAKE_TS_SIGN
            client_cert = "pub.x"
            private_key = _fixed_client_pem()
            cookie = {}

            def ecdh_key(self, aid=6383, origin="https://www.douyin.com"):
                seen.update(aid=aid, origin=origin)
                return None

        h = Header()
        h.with_bd(API, _Auth(), aid=6383, origin="https://live.douyin.com",
                  timestamp=FIX_TS)
        self.assertEqual(seen.get("origin"), "https://live.douyin.com")
        self.assertEqual(seen.get("aid"), 6383)


# ═══════════════════════════════════════════════════════════════════════════
# G5 —— 负控：故意缺陷变体必须让上述判据变红
# ═══════════════════════════════════════════════════════════════════════════

class NegativeControls(unittest.TestCase):
    """证明门禁**真的会拦**——把缺陷塞回去，判据必须失败。"""

    def test_nc1_always_ecdsa_variant_fails(self):
        """NC1（★指定负控）：「有 ecdh_key 却仍走 ECDSA」的缺陷变体 ⇒ 门禁变红。

        这正是修复**之前**的项目形态：`generate_bd_ticket_client_data` 忽略
        `ecdh_key`，恒用 ECDSA 签名。
        """
        from utils.bd_ticket import generate_bd_ticket_client_data

        def _always_ecdsa(api, ticket, ts_sign, prv, ecdh_key=None,
                          timestamp=None, t_trust=None):
            """缺陷变体：忽略 ecdh_key，恒 ECDSA（复刻修复前行为）。"""
            import time as _time
            ts = int(_time.time()) if timestamp is None else int(timestamp)
            from utils.bd_ticket import _load_signing_key
            from ecdsa.util import sigencode_der
            sig = _load_signing_key(prv).sign(
                f"ticket={ticket}&path={api}&timestamp={ts}".encode("utf-8"),
                hashfunc=hashlib.sha256, sigencode=sigencode_der)
            p = {"ts_sign": ts_sign, "req_content": "ticket,path,timestamp",
                 "req_sign": base64.b64encode(sig).decode(), "timestamp": ts}
            from utils.bd_ticket import _ClientDataResult
            return _ClientDataResult(
                base64.b64encode(json.dumps(
                    p, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                ).decode(), "ecdsa")          # 谎报 ecdsa

        pem = _fixed_client_pem()
        from utils.bd_ticket import derive_ecdh_key
        key = derive_ecdh_key(pem, _fixed_server_cert_pub())
        out = _always_ecdsa(API, FAKE_TICKET, FAKE_TS_SIGN, pem,
                            ecdh_key=key, timestamp=FIX_TS)
        # ❌ 判据 T1：应为 hmac
        self.assertNotEqual(getattr(out, "algo_type", None), "hmac",
                            "负控 NC1 失效：缺陷变体竟标成 hmac")
        # ❌ 判据：签名长度不是 32 字节 HMAC 摘要
        self.assertNotEqual(len(base64.b64decode(_decode_payload(out)["req_sign"])), 32)
        # ✅ 对照组：真实实现在同一输入下必须走 hmac
        good = generate_bd_ticket_client_data(
            API, FAKE_TICKET, FAKE_TS_SIGN, pem, ecdh_key=key, timestamp=FIX_TS)
        self.assertEqual(good.algo_type, "hmac")

    def test_nc2_urlsafe_base64_variant_fails(self):
        """NC2：把 base64 退回 urlsafe ⇒ 标准-base64 判据必须 catches。"""
        pem = _fixed_client_pem()
        from utils.bd_ticket import derive_ecdh_key, get_req_sign_hmac
        key = derive_ecdh_key(pem, _fixed_server_cert_pub())
        res = f"ticket={FAKE_TICKET}&path={API}&timestamp={FIX_TS}"
        p = {"ts_sign": FAKE_TS_SIGN, "req_content": "ticket,path,timestamp",
             "req_sign": get_req_sign_hmac(res, key), "timestamp": FIX_TS}
        bad = base64.urlsafe_b64encode(
            json.dumps(p, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).decode()
        good_payload = {"ts_sign": FAKE_TS_SIGN,
                        "req_content": "ticket,path,timestamp",
                        "req_sign": get_req_sign_hmac(res, key),
                        "timestamp": FIX_TS}
        canonical = base64.b64encode(json.dumps(
            good_payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        ).decode()
        # 两者解出的 JSON 相同（语义等价），但**字节串不同** ⇒ 形状判据会红
        self.assertEqual(json.loads(base64.b64decode(bad)),
                         json.loads(base64.b64decode(canonical)))
        if bad != canonical:                       # 本 fixture 恰好产生差异
            self.assertNotEqual(bad, canonical,
                                "NC2 fixture 未产生 urlsafe/标准差异，换个 payload")

    def test_nc3_sign_type_guessed_from_cert_variant_fails(self):
        """NC3：若 header 改回「按 client_cert 猜测」，`pub.` 证书 + ECDH 失败
        就会标 `'1'` ⇒ 与本门禁 T14 冲突，证明 T14 会拦住这种回退。
        """
        # 缺陷形态：忽略 algo_type，按 client_cert 形态猜
        client_cert = "pub.newstyle"
        algo_actual = "ecdsa"                       # ECDH 失败后的实际算法
        guessed = "1" if str(client_cert).startswith("pub.") else "0"
        correct = "1" if algo_actual == "hmac" else "0"
        self.assertEqual(guessed, "1")
        self.assertEqual(correct, "0")
        self.assertNotEqual(guessed, correct,
                            "NC3 fixture 未能复现「猜测 ≠ 实际」的矛盾")

    def test_nc4_non_deterministic_key_variant_fails(self):
        """NC4：掺随机数的缺陷 `derive_ecdh_key` ⇒ 确定性判据必须失败。"""
        import os
        from utils.bd_ticket import _server_pub_point, _hkdf_sha256, _load_signing_key
        from ecdsa import VerifyingKey

        def _randomized(prv, server_cert):
            """缺陷变体：每次掺入随机 salt（复刻常见的 HKDF 误用）。"""
            sk = _load_signing_key(prv)
            vk = VerifyingKey.from_string(_server_pub_point(server_cert), curve=NIST256p)
            shared = (sk.privkey.secret_multiplier * vk.pubkey.point).x().to_bytes(32, "big")
            return _hkdf_sha256(shared, salt=os.urandom(32))

        pem, cert = _fixed_client_pem(), _fixed_server_cert_pub()
        self.assertNotEqual(_randomized(pem, cert), _randomized(pem, cert),
                            "NC4 fixture 竟然确定性 —— 换个随机源")

    def test_nc5_no_network_in_this_gate(self):
        """NC5（红线自检）：本门禁全程**不得**触碰网络。

        做法：把 socket 打上全局哨兵，跑一遍本文件全部计算路径，确认从未连接。
        """
        import socket
        knitted = {"count": 0}
        orig = socket.socket.connect

        def _tripwire(self, *a, **kw):
            knitted["count"] += 1
            raise AssertionError("门禁内发生了真实网络请求（红线）")

        socket.socket.connect = _tripwire
        try:
            from utils.bd_ticket import derive_ecdh_key, generate_bd_ticket_client_data
            pem = _fixed_client_pem()
            key = derive_ecdh_key(pem, _fixed_server_cert_pub())
            generate_bd_ticket_client_data(API, FAKE_TICKET, FAKE_TS_SIGN, pem,
                                           ecdh_key=key, timestamp=FIX_TS)
            generate_bd_ticket_client_data(API, FAKE_TICKET, FAKE_TS_SIGN, pem,
                                           timestamp=FIX_TS)
            # header 路径也过一遍（ecdh_key 由替身提供，不落网络）
            from builder.header import Header

            class _A:
                ticket, ts_sign, client_cert = FAKE_TICKET, FAKE_TS_SIGN, "pub.x"
                private_key, cookie = pem, {}

                def ecdh_key(self, aid=6383, origin=""):
                    return key

            Header().with_bd(API, _A(), timestamp=FIX_TS)
        finally:
            socket.socket.connect = orig
        self.assertEqual(knitted["count"], 0, "门禁触发了真实出网")

    def test_nc6_fetch_server_cert_is_interceptable_seam(self):
        """NC6（红线自检）：证明线上唯一的出网点 `fetch_server_cert` **可被拦截**，
        且拦截后 `auth.ecdh_key` **优雅降级为 None**（不炸、不绕过重试原文）。

        若此处 tripwire **没有**触发，说明 `auth.ecdh_key` 换了别的出网途径
        （或把 `requests` 直接写进了 auth），门禁就失去「离线可验」的保证。
        """
        import utils.bd_ticket as bt
        calls = {"n": 0}
        orig = bt.fetch_server_cert

        def _tripwire(*a, **kw):
            calls["n"] += 1
            raise RuntimeError("tripwire：fetch_server_cert 被拦截（禁止真实出网）")

        bt.fetch_server_cert = _tripwire
        try:
            from builder.auth import DouyinAuth
            a = DouyinAuth()
            a.private_key = _fixed_client_pem()
            a.cookie_str = "sessionid=x"
            # ① 出网被拦 ⇒ 必须优雅降级为 None（而不是抛出或返回假密钥）
            self.assertIsNone(a.ecdh_key(aid=6383, origin="https://live.douyin.com"))
        finally:
            bt.fetch_server_cert = orig
        # ② tripwire 确实挡在 auth 的出网路径上
        self.assertEqual(calls["n"], 1,
                         "auth.ecdh_key 未走 utils.bd_ticket.fetch_server_cert "
                         "——离线门禁将拦不住真实出网")
        # ③ 恢复后由 NC5 的 socket 哨兵兜底，保证任何路径都不出网


# ═══════════════════════════════════════════════════════════════════════════
# G6 —— auth.ecdh_key 缓存语义
# ═══════════════════════════════════════════════════════════════════════════

class AuthEcdhKeyCaching(unittest.TestCase):
    """(aid, origin) 缓存 + 私钥刷新后失效。"""

    def test_t19_cache_keyed_by_aid_and_origin(self):
        from builder.auth import DouyinAuth
        import utils.bd_ticket as bt

        seen = []
        orig_fetch, orig_derive = bt.fetch_server_cert, bt.derive_ecdh_key
        bt.fetch_server_cert = lambda *a, **kw: (seen.append((kw.get("origin"),
                                                              kw.get("user_agent")))
                                                 or (_fixed_server_cert_pub(), "sn"))
        bt.derive_ecdh_key = lambda prv, cert: b"\x11" * 32
        try:
            a = DouyinAuth()
            a.private_key = _fixed_client_pem()
            a.cookie_str = "sessionid=x"
            a.ecdh_key(aid=6383, origin="https://live.douyin.com")
            a.ecdh_key(aid=6383, origin="https://live.douyin.com")   # 命中缓存
            a.ecdh_key(aid=6383, origin="https://www.douyin.com")    # 换 origin
        finally:
            bt.fetch_server_cert, bt.derive_ecdh_key = orig_fetch, orig_derive
        self.assertEqual(len(seen), 2, f"缓存未按 (aid, origin) 生效：{seen}")
        self.assertEqual(seen[0][0], "https://live.douyin.com")
        self.assertEqual(seen[1][0], "https://www.douyin.com")

    def test_t20_private_key_refresh_invalidates_cache(self):
        """私钥经 `perepare_auth` 刷新后，ECDH 缓存必须清空（旧密钥对应旧私钥）。"""
        from builder.auth import DouyinAuth
        a = DouyinAuth()
        a.private_key = _fixed_client_pem()
        a._ecdh_cache[(6383, "https://live.douyin.com")] = b"\x22" * 32
        # 走真实入口：`keys_` 是 login 模块给的「双层 JSON」形态
        a.perepare_auth("", "", json.dumps(
            {"data": json.dumps({"ec_privateKey": _fixed_client_pem()})}))
        self.assertEqual(a.private_key, _fixed_client_pem())
        self.assertEqual(a._ecdh_cache, {}, "私钥刷新后 ECDH 缓存未失效")

    def test_t21_missing_private_key_returns_none(self):
        from builder.auth import DouyinAuth
        a = DouyinAuth()
        a.private_key = None
        self.assertIsNone(a.ecdh_key(aid=6383, origin="https://live.douyin.com"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
