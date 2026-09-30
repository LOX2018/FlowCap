from enum import Enum

from loguru import logger

# 2026-10-01（HC-16）：client-data 的**唯一实现**在 utils/bd_ticket（含 ECDH/HMAC），
# `utils/dy_util.generate_bd_ticket_client_data` 只是一份**单值返回的旧包装**
# （不在本次改动范围）。header 必须用真实现才能拿到 `algo_type` 去驱动
# `bd-ticket-guard-web-sign-type`——否则「头声明 hmac、载荷 ECDSA」的矛盾无法消除。
from utils.bd_ticket import generate_bd_ticket_client_data
from utils.dy_util import generate_ree_key, generate_csrf_token


class HeaderType(Enum):
    DOC = 'DOC'
    POST = 'POST'
    FORM = 'FORM'
    GET = 'GET'
    PROTOBUF = 'PROTOBUF'


class Header:
    def __init__(self):
        self.headers = {}

    def with_bd(self, api, auth, aid=6383, origin='https://www.douyin.com',
                timestamp=None, dtrait_timestamp=None, dtrait_randbytes=None,
                require_dtrait=False):
        """bd-ticket-guard 请求头（2026 版，对齐源项目 passport zero.js 拦截器分支）。

        :param api: 请求 pathname（不含 query）。
        :param aid: 该子域 aid（www=6383，creator=2906）。
        :param origin: 换证书站点，需与业务请求同源。

        差异说明（2026-09-19 对齐源项目 cv-cat/DouYin_Spider）：
          - 补齐 `bd-ticket-guard-web-sign-type`（hmac=1 / ecdsa=0）；
          - `web-version` 由 ts_sign 前缀决定（ts.1 -> 1，其余 -> 2），
            不再是固定的 '1'；
          - 移除旧版多余的 `iteration-version` 头（2026 抓包无此头）；
          - ticket 与 cookie 会话一致性：本项目 auth 无 `ticket_matches_session`
            强校验方法，改为可判定时告警、不硬抛（避免因能力缺口直接不可用）；
          - `x-tt-session-dtrait` 设备特征头：本项目 auth 无
            `session_dtrait_header` 能力，优雅跳过（高风控接口需要它，
            IM 私信发送以 bd-ticket-guard 为鉴权主体，缺 dtrait 不阻断）。

        2026-10-01（HC-16）：`origin` 由空操作变为**真实生效** —— 它经
        `auth.ecdh_key(aid, origin)` 决定从哪个子域换取服务端 ecies 证书
        （直播域与主站证书/密钥不通用）。同时 `bd-ticket-guard-web-sign-type`
        改由 client-data 的**实际算法** `algo_type` 驱动，不再靠 client_cert 形态猜测。
        """
        from utils.bd_ticket import ticket_guard_version
        # 会话一致性门禁（能力存在时强校验；缺失时降级告警）
        try:
            if hasattr(auth, "ticket_matches_session") and not auth.ticket_matches_session():
                raise RuntimeError(
                    'bd-ticket-guard 的 ticket/ts_sign 与当前 cookie 不是同一次登录'
                    '（cookie 里的 bd_ticket_guard_ts_sign_id 对不上 DY_TS_SIGN）。'
                    '强校验接口会失败且报错无从判断，请重新抓取配套的凭证。')
        except RuntimeError:
            raise
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[header] ticket 会话一致性校验异常（放行）：{e}")
        # 2026-10-01（HC-16）：ECDH/HMAC 移植。`origin` 不再是空操作 —— 它决定
        # 从哪个子域取服务端 ecies 证书（直播域 aid 与主站不同，证书/密钥不通用）。
        ecdh_key = None
        try:
            ecdh_fn = getattr(auth, "ecdh_key", None)
            if callable(ecdh_fn):
                ecdh_key = ecdh_fn(aid=aid, origin=origin)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[header] ecdh_key 获取失败（回退 ECDSA）：{e}")
        trust_cookie = (getattr(auth, "cookie", None) or {}).get("_bd_ticket_crypt_cookie")
        # client_data 是 str 子类，`set_header` 走 requests 编码时就是它的 str 本体。
        client_data = generate_bd_ticket_client_data(
            api, auth.ticket, auth.ts_sign, auth.private_key, ecdh_key=ecdh_key,
            timestamp=timestamp, t_trust=1 if trust_cookie else None)
        # mock/替身返回裸值时兜底为 ecdsa（真实实现恒是新返回类型）
        algo_type = getattr(client_data, "algo_type", "ecdsa")
        self.set_header('bd-ticket-guard-client-data', client_data)
        self.set_header('bd-ticket-guard-ree-public-key', generate_ree_key(auth.private_key))
        self.set_header('bd-ticket-guard-version', '2')
        self.set_header('bd-ticket-guard-web-version',
                        str(ticket_guard_version(getattr(auth, 'ts_sign', '') or '')))
        # ★ web-sign-type 由**本次 client-data 实际使用的签名算法**驱动（algo_type），
        #   不再由 client_cert 形态猜测。2026-10-01（HC-16）：此前按
        #   `client_cert.startswith('pub.')` 标 hmac(=1)，而 client-data 恒为 ECDSA
        #   签名 ⇒ 头声明与载荷自相矛盾，服务端校签必失败（写接口恒 403 的根因）。
        self.set_header('bd-ticket-guard-web-sign-type',
                        '1' if algo_type == 'hmac' else '0')
        # 设备特征头（能力存在才发）
        try:
            if hasattr(auth, "session_dtrait_header"):
                dtrait = auth.session_dtrait_header(
                    api, aid=aid, origin=origin, timestamp=dtrait_timestamp,
                    randbytes=dtrait_randbytes, strict=require_dtrait,
                    allow_static=not require_dtrait)
                if dtrait:
                    self.set_header('x-tt-session-dtrait', dtrait)
                elif require_dtrait:
                    raise RuntimeError('发布接口必须携带 x-tt-session-dtrait，当前未能生成')
        except RuntimeError:
            raise
        except Exception as e:  # noqa: BLE001
            if require_dtrait:
                raise
            logger.debug(f"[header] dtrait 头生成跳过：{e}")
        return self

    def with_bd_readonly(self, auth):
        """只读接口的 bd-ticket-guard 头（4 个，**不含 client-data**）。

        2026-08-16 实录（comment/list 等）：只读接口浏览器只发
        ree-public-key / version:2 / web-sign-type / web-version:2，不发 client-data。
        """
        from utils.bd_ticket import ticket_guard_version
        try:
            if not getattr(auth, 'private_key', None):
                return self
            self.set_header('bd-ticket-guard-ree-public-key',
                            generate_ree_key(auth.private_key))
            self.set_header('bd-ticket-guard-version', '2')
            self.set_header('bd-ticket-guard-web-version',
                            str(ticket_guard_version(getattr(auth, 'ts_sign', '') or '')))
            algo = 'hmac' if str(getattr(auth, 'client_cert', '') or '').startswith('pub.') else 'ecdsa'
            self.set_header('bd-ticket-guard-web-sign-type', '1' if algo == 'hmac' else '0')
        except Exception:
            pass
        return self

    def set_header(self, key, value):
        self.headers[key] = value
        return self

    def with_csrf(self, cookie_str):
        """设置 CSRF 头。

        2026-09-17 修补（OCR 审查 HIGH）：
         1) `generate_csrf_token` 失败时返回 `(None, None)`，原实现无条件
            `[0]` 写入 → 请求头被设为字面量 `"None"`，下游得到难以定位的
            401/签名拒绝，而非快速失败。现取后判空，为空则不设该头。
         2) 补齐 `return self`（与 set_header/set_referer 一致），消除
            "链式调用会 TypeError" 的潜在隐患。
        """
        tok = None
        try:
            tok = generate_csrf_token(cookie_str)[0]
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[header] generate_csrf_token 异常: "
                           f"{type(e).__name__}: {e}")
        if tok:
            self.set_header('x-secsdk-csrf-token', tok)
        else:
            logger.warning("[header] CSRF token 获取失败（为空），"
                           "本次不设置 x-secsdk-csrf-token 头")
        return self

    def set_referer(self, url):
        self.set_header('referer', url)
        return self

    def remove_header(self, key):
        if key in self.headers:
            del self.headers[key]
        return self

    def get(self):
        return self.headers

    def __call__(self):
        return self.headers


class HeaderBuilder:
    from utils.fingerprint import get_profile
    ua = get_profile()["ua"]
    sec_ch_ua = get_profile()["sec_ch_ua"]
    sec_ch_ua_platform = get_profile()["sec_ch_ua_platform"]

    @staticmethod
    def build(header_type):
        header = Header()
        header.set_header('user-agent', HeaderBuilder.ua)
        header.set_header('cache-control', 'no-cache')
        header.set_header('pragma', 'no-cache')
        # ★ 2026-09-26（ADR-016 D2）：**Client Hints 按内核条件发送**。
        #
        # 判据：`sec_ch_ua` 为空 ⇒ 当前内核是 Gecko（Firefox/Camoufox），
        # 而 **Firefox 不实现 Client Hints**（`navigator.userAgentData === null`）。
        # 若此时仍发 sec-ch-ua，等于「头端声明 Chrome ⊗ 浏览器自报 Firefox」——
        # 比完全不伪装更易被交叉校验识别（本次凭证 3 小时失效的根因之一）。
        # ⇒ Gecko 内核下**不发**这一组头。
        if HeaderBuilder.sec_ch_ua:
            header.set_header('sec-ch-ua', HeaderBuilder.sec_ch_ua)
            header.set_header('sec-ch-ua-mobile', '?0')
            header.set_header('sec-ch-ua-platform', HeaderBuilder.sec_ch_ua_platform)
        header.set_header('sec-fetch-dest', 'empty')
        header.set_header('sec-fetch-mode', 'cors')
        header.set_header('sec-fetch-site', 'same-origin')
        header.set_header('priority', 'u=1, i')
        header.set_header('accept-language', 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6')
        if header_type == HeaderType.POST:
            header.set_header('accept', '*/*')
            header.set_header('content-type', 'application/json; charset=UTF-8')
        elif header_type == HeaderType.FORM:
            header.set_header('accept', 'application/json, text/plain, */*')
            header.set_header('content-type', 'application/x-www-form-urlencoded; charset=UTF-8')
        elif header_type == HeaderType.PROTOBUF:
            header.set_header('accept', 'application/x-protobuf')
            header.set_header('content-type', 'application/x-protobuf')
        elif header_type == HeaderType.GET:
            header.set_header('accept', 'application/json, text/plain, */*')
        elif header_type == HeaderType.DOC:
            header = Header()
            h = {
                'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                'accept-language': 'zh-CN,zh;q=0.9,en;q=0.8,en-GB;q=0.7,en-US;q=0.6',
                'cache-control': 'no-cache',
                'cookie': '',
                'pragma': 'no-cache',
                'priority': 'u=0, i',
                'sec-fetch-dest': 'document',
                'sec-fetch-mode': 'navigate',
                'sec-fetch-site': 'none',
                'sec-fetch-user': '?1',
                'upgrade-insecure-requests': '1',
                'user-agent': HeaderBuilder.ua
            }
            # ★ 2026-09-26（ADR-016 D2）：同 build() —— Gecko 内核不发 Client Hints
            if HeaderBuilder.sec_ch_ua:
                h['sec-ch-ua'] = HeaderBuilder.sec_ch_ua
                h['sec-ch-ua-mobile'] = '?0'
                h['sec-ch-ua-platform'] = HeaderBuilder.sec_ch_ua_platform
            header.headers.update(h)
        return header
