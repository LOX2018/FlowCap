from builder.header import HeaderBuilder
from utils.fingerprint import get_profile
from utils.dy_util import generate_webid, generate_msToken, splice_url, generate_a_bogus, generate_fake_webid


class Params:
    def __init__(self):
        self.params = {}

    def with_platform(self):
        params = {
            'device_platform': 'webapp',
            'aid': '6383',
            'channel': 'channel_pc_web',
            'pc_client_type': '1',
            'update_version_code': '170400',
            'version_code': '170400',
            'version_name': '17.4.0',
            'cookie_enabled': 'true',
            'screen_width': get_profile()["screen_width"],
            'screen_height': get_profile()["screen_height"],
            'browser_language': 'zh-CN',
            'browser_platform': 'Win32',
            'browser_name': get_profile()["browser_name"],
            'browser_version': get_profile()["browser_version"],
            'browser_online': 'true',
            'engine_name': 'Blink',
            'engine_version': get_profile()["engine_version"],
            'os_name': 'Windows',
            'os_version': '10',
            'cpu_core_num': get_profile()["cpu_core_num"],
            'device_memory': get_profile()["device_memory"],
            'platform': 'PC',
            'downlink': '10',
            'effective_type': '4g',
            'round_trip_time': '100',
        }
        self.params.update(params)
        return self

    def update_params(self, params):
        self.params.update(params)
        return self

    def with_web_id(self, auth=None, url="", fake=False):
        webid = generate_fake_webid() if fake else generate_webid(auth, url)
        self.params['webid'] = webid
        return self

    def with_a_bogus(self, data=None, host='www.douyin.com'):
        """算 a_bogus。host 必须是本次请求的子域：签名里内嵌 (aid, page_id)，
        www / live / creator 三套值不同，用错了强校验接口会判人机验证。
        """
        query = splice_url(self.get())
        if data is not None:
            data = splice_url(data)
        else:
            data = ''
        abogus = generate_a_bogus(query, data, host=host)
        self.add_param('a_bogus', abogus)
        return self

    def with_ms_token(self):
        msToken = generate_msToken()
        self.params['msToken'] = msToken
        return self

    def signed_url(self, base_url, auth=None, ts=None):
        """拼出带 `timestamp` + `x-secsdk-web-signature` 的完整 URL。

        ## 何时需要（2026-09-21 实测）

        secsdk 的 webSign 策略只覆盖部分接口，清单见
        `utils.secsdk_web_sign.PROTECTED_PATHS_GET`。命中清单却**没签**时，
        服务端直接返回 **HTTP 403（46 字节非 JSON）** —— 这就是
        `/aweme/v1/web/mix/aweme/`（合集作品）此前恒取不到数据的原因。

        ## 必须发本方法的返回值（不能把 get() 传给 requests 的 params）

        签名对**规范化后的 query** 计算，服务端也按收到的 query 校验。
        若把 `params=self.get()` 交给 requests，requests 会**再编码一遍**，
        与签名输入对不上 → 依旧 403。

        用法::

            url = params.signed_url(f'{DouyinAPI.domain_for(api)}{api}', auth)
            requests.get(url, headers=..., cookies=...)   # 注意：不传 params
        """
        from utils.secsdk_web_sign import sign_url
        uifid = (auth.cookie or {}).get('UIFID', '') if auth else ''
        return sign_url(base_url + '?' + self.toString(), ts=ts, uifid=uifid)

    def needs_secsdk_sign(self, path, method='GET'):
        """该 path 是否在 secsdk webSign 保护清单内（决定用哪种发送方式）。"""
        try:
            from utils.secsdk_web_sign import is_protected
            return is_protected(path, method)
        except Exception:  # noqa: BLE001 —— 模块缺失时保守返回 False（不阻断既有链路）
            return False

    def add_param(self, key, value):
        self.params[key] = value
        return self

    def get(self):
        return self.params

    def sort(self):
        order = ['device_platform', 'aid', 'channel', 'publish_video_strategy_type', 'source', 'sec_user_id',
                 'personal_center_strategy', 'update_version_code', 'pc_client_type', 'version_code', 'version_name',
                 'cookie_enabled', 'screen_width', 'screen_height', 'browser_language', 'browser_platform',
                 'browser_name', 'browser_version', 'browser_online', 'engine_name', 'engine_version', 'os_name',
                 'os_version', 'cpu_core_num', 'device_memory', 'platform', 'downlink', 'effective_type',
                 'round_trip_time', 'webid', 'verifyFp', 'fp', 'msToken', 'a_bogus']
        # 按照 order 排序的字段
        sorted_params = {key: self.params[key] for key in order if key in self.params}
        # 不在 order 中的字段
        remaining_params = {key: self.params[key] for key in self.params if key not in order}
        # 合并两个字典
        sorted_params.update(remaining_params)
        self.params = sorted_params

    def toString(self):
        # 按url参数格式拼接参数
        return "&".join([f"{k}={v}" for k, v in self.params.items()])
