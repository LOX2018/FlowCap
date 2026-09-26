import base64
import json
import time

from loguru import logger

from dy_apis.douyin_api import DouyinAPI
from utils.dy_util import trans_cookies, generate_msToken, generate_dynamic_msToken

# msToken 缓存有效期（秒）：过期后下次访问自动重新换取
_MS_TTL = 600


class DouyinAuth:
    def __init__(self):
        self.cookie = None
        self.cookie_str = None
        self.private_key = None
        self.ticket = None
        self.ts_sign = None
        self.client_cert = None
        self.ree_public_key = None
        self.uid = None
        self._ttwid = ""
        self._ms_cache = ""      # 动态 msToken 缓存
        self._ms_ts = 0          # 缓存时间戳

    def perepare_auth(self, cookieStr: str, web_protect_: str = "", keys_: str = ""):
        # 2026-09-17 修补（OCR 审查 CRITICAL）：原实现无条件执行
        # `self.cookie = trans_cookies(cookieStr)`。而调用方存在
        # `auth.perepare_auth("", web_protect, keys)` 的用法
        # （browser_daemon 页面签名刷新后仅需重算 ticket/ts_sign/私钥），
        # 此时 trans_cookies("") 返回 {}，会把**已有效的 cookie 清空**
        # → 后续请求全部 401/风控。现改为：空串不覆盖既有 cookie。
        if cookieStr:
            self.cookie = trans_cookies(cookieStr)
            self._ttwid = self.cookie.get("ttwid", "")
            # 真实请求 msToken 只在 query 携带；cookie 里若有旧 msToken 去掉，避免冲突
            self.cookie.pop("msToken", None)
            self.cookie_str = "; ".join([f"{k}={v}" for k, v in self.cookie.items()])
        else:
            logger.debug("[auth] perepare_auth 收到空 cookie，保留现有 cookie 不变"
                         "（仅刷新签名四件套）")
        # 2026-09-26 修补（ADR-017 M1 实测）：原守卫为 `!= ""`，**挡不住 None**。
        # 实测触发链：未登录账号下 security-sdk 尚未生成 web_protect 时，
        #   login_api.dyGenerateInitData 读到 localStorage 返回 None →
        #   perepare_auth('', None, None) → `if None != ""` 为真 → json.loads(None)
        #   → TypeError: the JSON object must be str, bytes or bytearray, not NoneType
        # 修法：守卫改为「非空字符串」语义（同时挡 None/空串），保持原有效路径行为不变。
        #   注：仅当传入为**非空字符串**时才解析；None/"" 一律跳过（保留既有 ticket 等字段）。
        if web_protect_ and isinstance(web_protect_, str):
            web_protect_ = json.loads(json.loads(web_protect_)['data'])
            self.ticket = web_protect_['ticket']
            self.ts_sign = web_protect_['ts_sign']
            self.client_cert = web_protect_['client_cert']
        elif web_protect_ is None:
            logger.debug("[auth] perepare_auth 收到 web_protect_=None（SDK 未就绪/未登录），"
                         "跳过签名四件套刷新（保留既有值）")
        if keys_ and isinstance(keys_, str):
            keys_ = json.loads(json.loads(keys_)['data'])
            self.private_key = keys_['ec_privateKey']
            self.ree_public_key = base64.b64encode(self.private_key.encode()).decode()
        elif keys_ is None:
            logger.debug("[auth] perepare_auth 收到 keys_=None（SDK 未就绪/未登录），跳过私钥刷新")


    @property
    def msToken(self):
        """惰性获取 msToken：首次/过期时自动纯算换取真 token（绑定本会话 ttwid），失败回退随机。
        这样 search 等需要 msToken 的调用无需手动准备，缺失/过期会自动补上。"""
        now = time.time()
        if self._ms_cache and (now - self._ms_ts < _MS_TTL):
            return self._ms_cache
        tok = ""
        try:
            tok = generate_dynamic_msToken(ttwid=self._ttwid)
        except Exception:
            tok = ""
        if tok:
            self._ms_cache = tok
            self._ms_ts = now
            return tok
        return self._ms_cache or generate_msToken()

    @msToken.setter
    def msToken(self, value):
        if value:
            self._ms_cache = value
            self._ms_ts = time.time()

    def refresh_mstoken(self):
        """强制刷新 msToken（如遇接口因 token 过期报错时可调用）。"""
        self._ms_cache = ""
        self._ms_ts = 0
        return self.msToken

    def get_uid(self):
        if self.uid is None:
            self.uid = DouyinAPI.get_my_uid(self)
        return self.uid
