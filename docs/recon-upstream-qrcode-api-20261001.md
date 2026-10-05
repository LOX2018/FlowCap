# 上游情报报告：抖音「扫码登录」纯 HTTP（接口级）可行性

- **侦察日期**：2026-10-01
- **范围**：只读侦察，未修改任何文件、未触发任何真实登录请求
- **结论一句话**：**上游有完整且成熟的纯 HTTP 扫码登录实现，不需要浏览器 UI**；但存在 3 项硬约束（wp 匿名素材来源、dtrait 设备素材、身份一致性），且本项目 `FlowCap` 已经把这条路 vendor 进来并落地成**子进程 runner**（只是默认未启用）。

---

## 0. 上游副本筛选（取哪份为准）

| 副本 | 路径 | `dy_apis/login_api.py` 行数 | 判定 |
|---|---|---|---|
| `DouYin_Spider-master` | `C:\Users\LOX\Desktop\DYchajian\_ext_repos\DouYin_Spider-master\dy_apis\login_api.py` | 419 | 旧版（sso 域 `1.0.26` SDK，**已过期**；验证=粗) |
| `DouYin_Spider_git` | `C:\Users\LOX\Desktop\DYchajian\_ext_repos\DouYin_Spider_git\dy_apis\login_api.py` | **3410** | ✅ **最新最全，本报告主基准**（git HEAD `4479ea7`，2026-09-20） |
| `DouYin_Spider_latest` | `C:\Users\LOX\Desktop\DYchajian\_ext_repos\DouYin_Spider_latest\` | — | 只有 2 个零散文件，**无 login_api.py** |
| vendor 副本 | `C:\Users\LOX\Desktop\DYchajian\FlowCap\vendor\douyin_spider_upstream\dy_apis\login_api.py` | 3410 | 与 `DouYin_Spider_git` **逐字节相同**（`diff -q` 输出 IDENTICAL）|

验证命令与输出：

```
$ diff -q "FlowCap/vendor/douyin_spider_upstream/dy_apis/login_api.py" \
         "_ext_repos/DouYin_Spider_git/dy_apis/login_api.py"
IDENTICAL to git copy
```

> 下文所有行号以 `_ext_repos\DouYin_Spider_git\dy_apis\login_api.py` 为准（vendor 副本同号）。

---

## 1. 是否有【纯 HTTP】获取登录二维码的实现？

**答：有。** 类 `DYLoginApi`，文件自述即写明"不依赖浏览器自动化"。

### 证据 1-1（事实）· 模块自述

`C:\Users\LOX\Desktop\DYchajian\_ext_repos\DouYin_Spider_git\dy_apis\login_api.py:1-14`

```python
# -*- coding: utf-8 -*-
"""抖音登录（纯算，不依赖浏览器自动化）。

两种方式：
- **扫码登录**：`qrcode_login()`。取二维码 -> 展示 -> 轮询状态 -> 成功后跟随
  重定向落 Cookie。
- **短信验证码登录**：`phone_login()`。手机号与验证码按 passport 的
  `XOR 5 + hex` 加密（见 `utils/passport.py`）。
"""
```

`login_api.py:838-839`

```python
class DYLoginApi:
    """登录入口。所有方法都是纯 HTTP，不启动浏览器。"""
```

**佐证（无浏览器依赖）**：全仓 grep `playwright|selenium|camoufox|puppeteer|webdriver` 于 `login_api.py` 的返回值仅为字符串 `"webdriver": False`（它是 `account_sdk_source_info` 指纹 JSON 的一个叶子字段，不是自动化库），无任何浏览器驱动 import。`requirements.txt` 中无 playwright/selenium。

### 证据 1-2（事实）· 请求 URL / 域（这是最容易踩错的一条）

`login_api.py:773-780`

```python
SSO_URL = "https://sso.douyin.com"
HOME_URL = "https://www.douyin.com"
# 登录接口在 login.douyin.com 下，不是主站。
# 主站的 www.douyin.com/passport/web/get_qrcode/ 一律返回 error_code=4031
# 「网站存在安全风险」——在真实浏览器里发同样的请求也是 4031，跟客户端无关，
# 纯粹是走错了域（2026-08-16 用独立 Chrome profile 抓登出态实录确认）。
LOGIN_URL = "https://login.douyin.com"
PASSPORT_API = LOGIN_URL + "/passport/web/"
```

**⇒ 纯 HTTP 取码：`GET https://login.douyin.com/passport/web/get_qrcode/`**（不是 `www`，也不是 `sso`）。

`login_api.py:860-861`

```python
def __init__(self):
    self.base_url = PASSPORT_API
```

### 证据 1-3（事实）· `get_qrcode()` 函数本体（含必需参数）

`login_api.py:2626-2658`

```python
def get_qrcode(self, auth) -> dict:
    """取登录二维码，返回 {token, qrcode_index_url, ...}。"""
    cookie_profile = _passport_profile("DY_PASSPORT_COOKIE_PROFILE")
    refreshed = (cookie_profile in ("chrome_current", "chrome_current_early")
                 and getattr(auth, "_qr_refresh_ready", False))
    result = self._passport_get(
        auth, "get_qrcode/",
        lambda: self._sdk_params(auth, {
            "next": HOME_URL,
            "need_short_url": "true",
            "need_logo": "false",
            "is_new_login": "1",
            "is_from_iesaccountsaas": "1",
        }, device_fp=refreshed),
    )
```

方法为 **GET**（经 `_passport_get`，`login_api.py:2529-2535`）：

`login_api.py:2524-2535`

```python
def _passport_get(self, auth, api, params_builder):
    """passport GET 请求，遇到 gfkadpd 拦截页自动补 Cookie 重试一次。"""
    for attempt in range(2):
        params = params_builder()
        params.with_a_bogus(host="login.douyin.com")
        resp = auth.request(
                            "GET", self.base_url + api,
                            headers=headers_with_cookie(
                                self._passport_headers(
                                    auth, api="/passport/web/" + api).get(),
                                scoped_cookies(auth, "qr" if api == "get_qrcode/" else "login")),
                            params=params.get(), verify=False, timeout=20)
```

### 证据 1-4（事实）· 公共 query 参数（`_sdk_params`，含 aid/device_platform/mstoken/a_bogus）

`login_api.py:2319-2374`（节选关键段）

```python
params = Params()
for key, value in PASSPORT_SDK.items():          # passport_jssdk_version 等
    params.add_param(key, value)
params.add_param("aid", "6383")
params.add_param("language", "zh")
params.add_param("account_app_language", "zh-CN")
params.add_param("ts", DYLoginApi._sdk_ts())     # 当天 UTC 12:00，不是当前秒
for key, value in (extra or {}).items():
    params.add_param(key, value)
if with_p_ui:
    params.add_param("p_ui", PASSPORT_SDK_TAIL["p_ui"])
if device_fp:
    params.add_param("p_ca", PASSPORT_SDK_TAIL["p_ca"])
    params.add_param("p_ca_real", PASSPORT_SDK_TAIL["p_ca_real"])
    fp = auth.cookie.get("s_v_web_id") or generate_s_v_web_id()
    auth.cookie.setdefault("s_v_web_id", fp)
    params.add_param("fp", fp)
    params.add_param("verifyFp", fp)
params.add_param("account_sdk_source", PASSPORT_SDK_TAIL["account_sdk_source"])
params.add_param("account_sdk_source_info", DYLoginApi._sdk_source_info(auth))
...
params.add_param("device_platform", "web_app")
sign, qs = DYLoginApi._passport_sign(params.get(), data)
params.add_param("sign", sign)
params.add_param("qs", qs)
if with_ms_token:
    params.add_param("msToken", auth.msToken)
```

**注意 `get_qrcode` 调用时 `device_fp=False`**（`login_api.py:2642` 传的是 `device_fp=refreshed`，首次为 False）。这条有原文明确说明：

`login_api.py:2306-2317`

```python
:param device_fp: 是否带 `p_ca` / `p_ca_real` 以及当前 passport
    版本要求的 `fp` / `verifyFp`。**按接口取值，不是公共字段**：

    | 接口 | 带 |
    | --- | --- |
    | `challenge` / `check_qrconnect` | 是 |
    | `get_qrcode` / `web_record_status` / `token/beat` | 否 |

    2026-08-29 Chrome 151 / passport 3.4.4
    重新抓到的真值是：`challenge` 与 `check_qrconnect` 都带
    `p_ca,p_ca_real,fp,verifyFp`（顺序固定），`get_qrcode` 不带。
```

**SDK 版本常量（硬性，版本错即 4031）**：

`login_api.py:801-835`

```python
# 登录页 SDK 的版本标识，取自实录；版本对不上同样会被判 4031。
PASSPORT_SDK = {
    "passport_jssdk_version": "3.4.4",
    "passport_jssdk_type": "normal",
    "is_from_ttaccountsdk": "1",
}
PASSPORT_SDK_TAIL = {
    "p_ui": "2.4.4", "p_ca": "4.0.26", "p_ca_real": "1.0.0.892",
    "account_sdk_source": "web", "p_js_v": "3.4.4", "p_js_t": "pro",
    "p_zt": "3.3.17", "p_ver": "1.1.3", "p_ver_real": "0",
    "p_bd": "1.0.1.19-fix.01",
}
PASSPORT_APP_KEY = "163e7ce78d58971a41f5b969996d85c2"
SIGN_EXCLUDE = ("sign", "qs", "msToken", "a_bogus")
SIGN_KEY_LIMIT = 10
```

### 证据 1-5（事实）· 签名要求（`sign`/`qs` 是 **passport 自己的** sha256+XOR5，**不是** a_bogus）

`login_api.py:2271-2296`（docstring 附 JS 原始还原）

```python
def _passport_sign(query: dict, data: dict = None):
    """算 passport 的 `sign` / `qs`，还原自 async/28872.*.js。
    ...
    早先版本没实现这两个字段，理由是「不带也能拿到二维码」—— 那是
    `get_qrcode` 宽松，`check_qrconnect` 会因此返回 `error_code=7`
    「访问太频繁」，看起来像限频，实际是签名缺失被兜底话术挡回来。
    """
    signable = {k: v for k, v in query.items() if k not in SIGN_EXCLUDE}
    keys = sorted(signable)[:SIGN_KEY_LIMIT]
    qstr = "&".join("%s=%s" % (k, signable[k]) for k in keys)
    body = data or {}
    bstr = "&".join("%s=%s" % (k, body[k]) for k in sorted(body))
    plain = "%s&%s&app_key=%s" % (qstr, bstr, PASSPORT_APP_KEY)
    sign = hashlib.sha256(plain.encode("utf-8")).hexdigest()
    return sign, passport_encrypt(",".join(keys))
```

**注意 `sign` 的窗口是动态的**：

`login_api.py:832-834`

```python
# 只签按键名排序后的前 N 个 —— SDK 里是 `Object.keys(e).sort().splice(10)`。
# 注意这个窗口是**动态**的：请求少带一个字段，第 10 名就换人。
SIGN_KEY_LIMIT = 10
```

**`a_bogus` 也要发**（双签名，二者叠加）：`login_api.py:2528` `params.with_a_bogus(host="login.douyin.com")`；纯 Python 实现在 `utils/ab_pure.py`。

`utils/ab_pure.py:16-25`（page_id / aid 必须按子域取值）

```python
#   L[67..70]   = le(page_id, 4)   页面 ID，**每个子域不同**
#   L[71..74]   = le(aid, 4)       站点 aid，也随子域变
# 用错子域的这两个值，强校验接口（如 aweme/detail、discover/search）会判人机验证。
```

**`msToken` 也要发**（`with_ms_token=True`），来源 `utils/mstoken.py` 纯算：

`utils/mstoken.py:22-26`

```python
# Chrome's login lifecycle uses both endpoints:
# - /web/r/token seeds the initial 164-byte token;
# - /web/common sends one full msgType=1 report, then a 783-byte msgType=2
#   behavior heartbeat every 300 seconds.
_TOKEN_URL = "https://mssdk.bytedance.com/web/r/token?ms_appid=6383"
_COMMON_URL = "https://mssdk.bytedance.com/web/common"
```

### 证据 1-6（事实）· 必需请求头

`login_api.py:2424-2490`（节选）

```python
headers.set_header("web-sdk-version", "1")
if qr_headers:
    headers.set_header("sec-ch-ua-platform", profile["sec_ch_ua_platform"])
dtrait = (auth.session_dtrait_header(
    api, aid=6383, origin=HOME_URL, strict=strict_dtrait)
    if api else None)
if dtrait:
    headers.set_header("x-tt-session-dtrait", dtrait)
headers.set_header("referer", HOME_URL + "/")
if qr_headers:
    headers.set_header("sec-ch-ua", profile["sec_ch_ua"])
if api:
    headers.set_header("x-tt-passport-aid-sign",
                       DYLoginApi._aid_sign(api, DYLoginApi._sdk_ts()))
csrf = auth.cookie.get("passport_csrf_token") or auth.cookie.get("passport_csrf_token_default")
if qr_headers:
    headers.set_header("sec-ch-ua-mobile", "?0")
headers.set_header("x-tt-passport-csrf-token", csrf or "")
trace = auth.cookie.get("biz_trace_id")
if trace:
    headers.set_header("x-tt-passport-trace-id", trace)
headers.set_header("user-agent", profile["ua"])
headers.set_header("accept", "application/json, text/javascript")
...
headers.set_header("accept-language", "zh-CN,zh;q=0.9")
headers.set_header("origin", HOME_URL)
headers.set_header("priority", "u=1, i")
headers.set_header("sec-fetch-dest", "empty")
headers.set_header("sec-fetch-mode", "cors")
headers.set_header("sec-fetch-site", "same-site")
```

`qr_headers` 的判定（`login_api.py:2430-2435`）：命中 `/passport/web/challenge/`、`/passport/web/get_qrcode/`、`/passport/web/check_qrconnect/` 才带 `sec-ch-ua*`。

**头顺序也是指纹**（`login_api.py:2491-2503`），注释见 `login_api.py:61-63`：

```python
# login.douyin.com 上的 Cookie 顺序，逐项照实录。
# 顺序本身也是指纹：浏览器按「写入时间」排；请求发送时必须走
# ``headers_with_cookie``，不能把这个 mapping 交给 curl_cffi 的 Cookie Jar。
```

### 证据 1-7（事实）· **`ttwid` / `dtrait` 的获取**

`login_api.py:861-878`（`ttwid` 走注册接口，纯 POST）

```python
@staticmethod
def register_ttwid() -> str:
    """向 ttwid 注册接口换一个匿名设备标识。
    抖音首页 HTML 返回的是 acrawler 挑战页（`__ac_nonce` + `_$jsvmprt`），
    直接抓首页拿不到 ttwid，必须走这个接口。
    """
    body = {
        "region": "cn", "aid": 1768, "needFid": False,
        "service": "www.douyin.com",
        "migrate_info": {"ticket": "", "source": "node"},
        "cbUrlProtocol": "https", "union": True,
    }
    resp = requests.post("https://ttwid.bytedance.com/ttwid/union/register/", ...)
    return resp.cookies.get_dict().get("ttwid", "")
```

⇒ **"取码前的引导链路"是完全 HTTP 的**，`bootstrap_auth()`（`login_api.py:1286-1552`）顺序如下（`login_api.py:1480`）：

```
/bootstrap（www/jingxuan）→ register_ttwid → check_ttwid_www
  → login_guiding_strategy → get_sec_ts → check_ttwid
  → challenge → [ticket_guard/get_client_cert] → get_qrcode
```

其中 `challenge` 也是纯算（AES-256-CBC + SHA256(UA)），见 `login_api.py:1872-1885`：

```python
2026-08-22 实录时序：get_sec_ts -> ttwid/check -> **challenge** -> get_qrcode。
我们以前整个跳过了它，直接去取二维码。它有两个副作用是后面要用的：
1. 响应 Set-Cookie 下发 `passport_csrf_token` / `_default`
2. 服务端据此把这台设备标记成「已通过 challenge」
body 是 `sign=<AES-CBC 密文>&sk=<XOR5+hex 的 JS 调用栈>`，共 4445 字节。
—— key = SHA256(userAgent)，IV = key 后 16 字节；AES-256-CBC + PKCS#7
```

**⇒ `service` 参数：上游当前版本没有**。旧的 `_ext_repos\DouYin_Spider-master\dy_apis\login_api.py:144` 仍有 `params.add_param("service", 'https://www.douyin.com')`，但**新版已改为 `next`**（`login_api.py:2637`）。这是本份情报里最容易误导人的差异点 —— 旧版脚本直接照抄会失败。

---

## 2. 如何【轮询扫码状态】

### 证据 2-1（事实）· POST + token 在 body

`login_api.py:2660-2690`

```python
def check_qrcode(self, auth, token: str) -> dict:
    """轮询二维码状态。status: new / scanned / confirmed / expired。

    实录里这个是 **POST**，token 在表单 body 里，不在 query。
    """
    data = {
        "need_logo": "false",
        "is_frontier": "true",
        "token": token,
        "is_new_login": "1",
        "next": HOME_URL,
        "need_short_url": "true",
    }
    # data 要先于 params 构造：sign 把 query 和 body 一起签
    params = self._sdk_params(auth, {"is_from_iesaccountsaas": "1"},
                              data=data, device_fp=True,
                              with_ms_token=True)
    params.with_a_bogus(data, host="login.douyin.com")
    resp = auth.request("POST", self.base_url + "check_qrconnect/",
                         headers=headers_with_cookie(
                             self._passport_headers(
                                 auth, form=True,
                                 api="/passport/web/check_qrconnect/").get(),
                             scoped_cookies(auth, "qr")),
                             params=params.get(), data=data,
                             verify=False, timeout=20)
```

⇒ **`POST https://login.douyin.com/passport/web/check_qrconnect/`**，body 是 form-urlencoded，`token` 在 body 里（`device_fp=True`，与 get_qrcode 的关键差异）。

### 证据 2-2（事实）· status 取值与各自语义

四个取值散落在 `qrcode_login` 状态机里，全部有实测注释：

`login_api.py:2906-2923`

```python
status = info.get("status")
fresh_read = bool(status)
...
if status == "confirmed":
    self._follow_login_redirect(auth, info.get("redirect_url"))
    auth._ms_pinned = False
    logger.info("扫码登录成功")
    return auth
if status == "expired":
    logger.info("二维码已过期，换一张继续等")
    token, scanned, fresh_read = None, False, False
    ...
    continue
if status == "scanned":
    scanned = True
    logger.info("已扫码，请在手机上确认（该码不再轮换，安心确认）")
```

`login_api.py:2661` docstring：`"""轮询二维码状态。status: new / scanned / confirmed / expired。` ⇒ **`new` 为默认初始态**（`qrcode_login` 里 `fresh_read=True` 且不落上面三个分支即 new）。

**本项目侧的确认**（同一组取值）：`C:\Users\LOX\Desktop\DYchajian\FlowCap\backend\auto_dm\login_api_vendor.py:282-283`

```python
def check_qrcode(self, auth, token: str) -> dict:
    """轮询二维码状态。上游 status: new / scanned / confirmed / expired。"""
```

### 证据 2-3（事实）· 轮询节奏（有实测数值）

`login_api.py:845-854`

```python
# 二维码实测约 60 秒失效，留点余量按 55 秒主动换
QR_TTL = 55
# check_qrconnect 持续限频超过这个秒数就别硬撑了，报错让用户等冷却
THROTTLE_GIVEUP = 120
# 轮询间隔照浏览器实测（2026-08-16，独立 profile + CDP 记 wallTime）：
# 浏览器每个周期发 1 个 OPTIONS 预检 + 1 个 POST，POST 之间实测
# 5.12 / 5.13 / 5.12 / 5.12 / 5.22 秒，平均 5.14。取 5.2 略慢于浏览器，
# 保证不会比真人更激进。我们不发预检，所以请求量本来就只有浏览器一半。
POLL_INTERVAL = 5.2
```

### 证据 2-4（事实）· `error_code=7` 是限频兜底话术，不能当致命错

`login_api.py:2749-2752`

```python
# error_code=7「访问太频繁」是轮询限频，属于可重试的瞬时状态，
# 不能当成致命错误——浏览器遇到它也只是退避后接着轮询
if ((res.get("data") or {}).get("error_code")) != 7:
    self._raise_if_blocked("check_qrconnect/", res)
```

### 证据 2-5（事实）· 空响应 / 非 JSON 的兜底

`login_api.py:2699-2718`

```python
raw = (resp.text or '').strip()
if not raw:
    logger.warning(
        'check_qrconnect 返回空响应，暂按临时限频重试 '
        '(HTTP %s, logid=%s)', resp.status_code, resp.headers.get('X-Tt-Logid', ''))
    return {'data': {'error_code': 7, 'description': 'empty response from QR poll edge'},
            'message': 'retry'}
if not raw.startswith('{'):
    raise RuntimeError(
        'check_qrconnect 返回非 JSON（可能命中登录风控页）：' ...)
```

### 证据 2-6（事实）· **换码的硬约束**（踩过的坑，务必照做）

`login_api.py:2836-2847`

```python
# 二维码只活 60 秒左右，到期要换一张。但换码有个硬前提：
# **必须先成功读到状态**。
#
# 2026-08-17 实测踩过：被限频（error_code=7）期间状态读不到，代码仍按
# 55 秒兜底换了码，而用户正好扫的是那张 —— 扫码直接作废，界面上表现为
# 「我扫过了它还一直弹新码」。限频时我们对这张码是 new 还是 scanned
# 一无所知，此时换码是纯粹的有害操作。
#
# 所以换码条件收紧成：本轮成功读到了状态（`fresh_read`）、且读到的是
# 还没人扫的 `new`、且已超过 TTL。读到 `scanned` 永不换（用户正停在
# 手机确认页上），读到 `expired` 立刻换（见下面的分支）。
```

以及并发坑（`login_api.py:2862-2867`）：

```python
# ⚠️ 这里必须把结果带下去。以前是查完就丢，下面 888 行紧接着
# 又查一遍 —— 同一秒发两个 check_qrconnect，而浏览器是稳定
# 5.14 秒一发，从不并发。2026-08-22 的日志里
# 「轮询 72 / 73 同为 16:59:04」就是这个，紧跟着就开始返
# error_code=7。把这一发的结果复用掉，节奏才和浏览器一致。
```

---

## 3. 如何从接口拿到【登录凭证 cookie】

### 证据 3-1（事实）· **不是直返 cookie，是「跟随 redirect_url 逐跳收 Set-Cookie」**

`login_api.py:2910-2914`

```python
if status == "confirmed":
    self._follow_login_redirect(auth, info.get("redirect_url"))
    auth._ms_pinned = False
    logger.info("扫码登录成功")
    return auth
```

`login_api.py:3351-3373`（`_follow_login_redirect` 本体）

```python
def _follow_login_redirect(self, auth, redirect_url, max_hops=5):
    """跟随登录重定向，把各跳的 Set-Cookie 都收进来。"""
    if not redirect_url:
        return
    headers = HeaderBuilder().build(HeaderType.DOC)
    url = redirect_url
    for _ in range(max_hops):
        resp = auth.request("GET", url,
                            headers=headers_with_cookie(headers.get(), scoped_cookies(auth)),
                            verify=False, timeout=20, allow_redirects=False)
        set_cookies = resp.cookies.get_dict()
        merge_set_cookies(auth, set_cookies)
        apply_ticket_guard(auth, resp.headers, set_cookies)
        if resp.status_code not in (301, 302, 303, 307, 308):
            break
        url = resp.headers.get("Location")
        if not url:
            break
    auth.cookie.pop("msToken", None)
    auth.cookie_str = "; ".join(f"{k}={v}" for k, v in auth.cookie.items())
    auth._ttwid = auth.cookie.get("ttwid", "")
```

⇒ **`sessionid` / `sid_tt` 等登录 cookie 来自 `check_qrconnect` 返回 `status=confirmed` 时给的 `redirect_url`，逐跳 GET（不自动跟随）手工收 `Set-Cookie`。**

（轮询阶段 `check_qrcode` 自身也会 `merge_set_cookies`，见 `login_api.py:2691-2692`。）

### 证据 3-2（事实）· 登录 cookie 之外，还要拿 ticket-guard 四件套 —— **这是最容易漏的一步**

`login_api.py:11-13`（模块 docstring）

```python
登录过程中会自己生成一对 P-256 密钥并在请求头带上公钥，登录响应里服务端会通过
`bd-ticket-guard-server-data` 下发 `ticket` / `ts_sign` / `client_cert`，
解出来写回 auth 即可，无需读浏览器 localStorage。
```

`utils\passport.py:181-188`

```python
def apply_ticket_guard(auth, headers, cookies=None) -> bool:
    """若响应里带了新签发的 ticket，就写回 auth。返回是否更新成功。"""
    info = parse_ticket_guard_server_data(headers, cookies)
    if not info:
        return False
    auth.ticket = info["ticket"]
    auth.ts_sign = info["ts_sign"]
    auth.client_cert = info["client_cert"]
    return True
```

**漏了这一步会怎样**（`login_api.py:1313-1319`，这是硬约束原文）：

```python
# 登录用的 EC 密钥对由我们自己生成，登录响应会据此签发 ticket
# 公钥必须在**取二维码之前**就通过 Cookie 交给服务端：zero.js 把它写进
# bd_ticket_guard_client_data，登录接口据此把 ticket 签给这把公钥。
# 缺了这个 Cookie，扫码能成功、Cookie 能落，但响应里永远没有
# bd-ticket-guard-server-data，于是拿不到 ticket/ts_sign——
# 后续 create_v2、发评论这类强校验接口就全都做不了。
```

### 证据 3-3（事实）· 落盘字段

`login_api.py:3385-3396`

```python
def save_credential(self, auth) -> str:
    """把登录凭证写入 .env。"""
    values = {
        "DY_COOKIES": "; ".join(f"{k}={v}" for k, v in auth.cookie.items()),
        "DY_TICKET": auth.ticket or "",
        "DY_TS_SIGN": auth.ts_sign or "",
        "DY_CLIENT_CERT": auth.client_cert or "",
        "DY_PRIVATE_KEY": auth.private_key or "",
        # 设备绑定的，跟着一起落盘，免得下次 bootstrap 时丢了
        "DY_DTRAIT_BLOB": auth.dtrait_blob or "",
    }
```

### 证据 3-4（事实）· 本项目已实现的产物契约

`C:\Users\LOX\Desktop\DYchajian\FlowCap\backend\login_qr_api_runner.py:180-200`

```python
payload = {
    "ok": True,
    "stage": "confirmed",
    "cookie_str": cookie_str,
    "cookie_count": len(ck),
    "ticket": getattr(auth, "ticket", "") or "",
    "ts_sign": getattr(auth, "ts_sign", "") or "",
    "client_cert": getattr(auth, "client_cert", "") or "",
    "private_key": getattr(auth, "private_key", "") or "",
    "has_sessionid": bool(ck.get("sessionid") or ck.get("sid_tt")),
}
_write_json(result_path, payload)
```

⇒ **`sid_tt` 在 runner 里被当作 `sessionid` 的等价判据**（`bool(ck.get("sessionid") or ck.get("sid_tt"))`）。

---

## 4. 对【身份一致性】的要求

### 证据 4-1（事实）· TLS 指纹：必须 curl_cffi impersonate Chrome，**只用 Python requests 不行**

`utils\http_client.py:1-9`

```python
"""统一 HTTP 出口：用 curl_cffi 冒充 Chrome 的 TLS / HTTP2 指纹。

requests 走 Python 自己的 TLS 栈和 HTTP/1.1，JA3/JA4 指纹、HTTP/2 SETTINGS 帧、
ALPN 协商结果跟 Chrome 完全不是一回事。风控看这一层比看 header 字典顺序重得多，
header 对得再齐，TLS 握手一开口还是 Python。curl_cffi 底下是 curl-impersonate，
能把这层补上。
"""
```

### 证据 4-2（事实）· impersonate 目标与其与 UA 的错位（**这是硬约束**）

`utils\http_client.py:36-71`

```python
def _resolve_impersonate():
    """Pick the newest profile actually shipped by the installed curl_cffi.
    Chrome 151 is the browser-side UA in the current capture, but curl_cffi
    0.16.x only ships transport profiles through chrome150.  Passing the
    unsupported name makes every request fail before a socket is opened.
    """
    requested = (os.getenv("DY_HTTP_IMPERSONATE") or "chrome151").strip().lower()
    ...
# This is the transport profile, not the HTTP User-Agent string.  With the
# currently installed curl_cffi it resolves chrome151 -> chrome150; upgrading
# curl_cffi to a build that contains chrome151 makes the same code exact.
IMPERSONATE = _resolve_impersonate()
```

⇒ **UA 声称 Chrome 151，TLS 实际是 Chrome 150**（降级）。已有残缺，需注意。

HTTP/2 也被锁死（`utils\http_client.py:76-90`）：`DY_HTTP_VERSION` 默认 `v2`。

### 证据 4-3（事实）· UA 由 `get_profile()` 单点提供，必须全链路同源

`utils\fingerprint.py:21-26, 61-75`

```python
"ua": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       ...)
"screen_width": "2560",
"screen_height": "1440",
...
def get_profile():
    ...
    return {
        "ua": ua,
        ...
        "sec_ch_ua_platform": '"Windows"',
    }
```

**同源要求有明确注释**（`login_api.py:2091-2110` 的 `_sdk_source_info`）：

```python
另修掉一处**自相矛盾**：窗口尺寸原来写死 `h-635` / `w-1280`
（2560x1440 的屏算出 1280x805 的窗口），而同一个 profile 里
`geo` 早就按实录算好了 1215 / 1392。两边必须同源，否则
query 报 2560 宽的屏、这里报 1280 宽的窗，一比就露。
```

以及 cookie 与 query 同源（`login_api.py:1626-1628`）：

```python
# —— 设备指纹，必须与 query 的 screen_* / cpu_core_num / device_memory 同源 ——
# goal.md「指纹必须与 cookie 自洽」记的就是这个 Cookie：
# 它写死了屏幕尺寸 / 核数 / 内存，和 query 对不上一比就露。
```

### 证据 4-4（事实）· **是否与后续业务请求的浏览器身份必须一致？上游没有直接写这条，但本项目判定了必须一致**

上游 `login_api.py` **未出现** Camoufox/Firefox 字样，也未出现"与日常会话身份须一致"的规则 —— 它是纯 Chrome 151 单身份实现。**⇒ 「跨身份矛盾」是 FlowCap 自己导出的结论**，不是上游注释。

本项目判定原文（`C:\Users\LOX\Desktop\DYchajian\FlowCap\backend\api\accounts.py:206-212`）：

```python
## 为什么默认 RPA 而不是 API（2026-09-30 身份审查 + 实机实测）
API 路径用 **Chrome 151 UA + curl_cffi(chrome150)**，与本机 **Camoufox(Firefox 152)**
的日常会话构成**跨信号身份矛盾**（H-27「凭证 3 小时失效」同族根因）。
而 RPA 在本机 Camoufox 内完成，**TLS/JS/指纹天然同一身份**，且
**无头即可出码**（实测 headless=True：8.5s 启动，容器栅格化锚点解出
`https://v.douyin.com/...`）⇒ 兼顾「不开窗口」与「身份一致」。
```

同族根因 ADR：`docs\adr\ADR-016-fingerprint-profile-follow-kernel.md:27, 86`

```
| **浏览器品牌** | **Chrome 148** | **Firefox 152.0** |
  ⇒ 档案声称 Chrome 148，浏览器实际 Firefox 152      ← 矛盾诞生机制
```

**⇒ 这条对用户的问题最关键**：换成纯 API 出码，出码身份是 Chrome；若后续日常请求走 Camoufox/Firefox，会重蹈「凭证短时间失效」的覆辙。**除非 FlowCap 全链路改为 Chrome 身份**，否则换轨道只是把 UI 坐标脆弱性换成身份脆弱性。

### 证据 4-5（事实）· dtrait：发还是不发

`login_api.py:2438-2447`，非 strict 时 `strict_dtrait=False` ⇒ **拿不到素材就整个头不发**（`builder/auth.py:1083` `return None`），QR 路径仍继续。

`builder/auth.py:1063-1083`

```python
def session_dtrait_header(self, path, aid=6383, origin="https://www.douyin.com",
                          timestamp=None, randbytes=None, strict=False,
                          allow_static=True):
    """按请求 path 生成 x-tt-session-dtrait；拿不到素材时返回 None。"""
    ...
    if not blob:
        if strict:
            raise RuntimeError(
                "缺少可按 path 重算的 dtrait 设备素材；请配置 DY_DTRAIT_BLOB，"
                "发布接口禁止省略 x-tt-session-dtrait"
            )
        return None
```

风险提示原文（`login_api.py:1412-1414`）：

```python
logger.warning("没有 dtrait 素材（DY_DTRAIT_BLOB / DY_SESSION_DTRAIT 都为空），"
               "passport 请求会缺 x-tt-session-dtrait，可能被判需二次验证")
```

---

## 5. 已知失败 / 限制（原文摘录）

| # | 限制 | 原文摘录 | 位置 |
|---|---|---|---|
| L1 | **走错域 ⇒ 4031** | 「主站的 `www.douyin.com/passport/web/get_qrcode/` 一律返回 error_code=4031 「网站存在安全风险」——在真实浏览器里发同样的请求也是 4031，跟客户端无关，纯粹是走错了域（2026-08-16 用独立 Chrome profile 抓登出态实录确认）」 | `login_api.py:776-778` |
| L2 | **SDK 版本过期 ⇒ 4031** | 「登录页 SDK 的版本标识，取自实录；版本对不上同样会被判 4031。…… 上一版停留在 3.4.2，服务端已经滚过版本。这批号会随抖音发版变，出问题先来核这里」<br>「2026-08-29 Chrome 151 / passport 3.4.4 重新抓到的真值是…… 旧的 `passport_capture.json` 是 3.1.3 实录，不能继续作为当前版本基线」 | `login_api.py:801-803, 2314-2317` |
| L3 | **缺 sign/qs ⇒ 伪装成限频的 error_code=7** | 「早先版本没实现这两个字段，理由是「不带也能拿到二维码」—— 那是 `get_qrcode` 宽松，`check_qrconnect` 会因此返回 `error_code=7`「访问太频繁」，看起来像限频，实际是签名缺失被兜底话术挡回来」 | `login_api.py:2285-2287` |
| L4 | **缺 fp/verifyFp ⇒ 凭证校验被兜底成 error_code=7** | 「缺这两个字段时，扫码前仍可能返回 new/scanned，但扫码后的凭证校验会被兜底成 error_code=7」 | `login_api.py:2337-2338` |
| L5 | **没 bp public key ⇒ 拿不到 ticket 四件套** | 「缺了这个 Cookie，扫码能成功、Cookie 能落，但响应里永远没有 `bd-ticket-guard-server-data`，于是拿不到 ticket/ts_sign——后续 create_v2、发评论这类强校验接口就全都做不了」 | `login_api.py:1317-1319` |
| L6 | **`.env` 载入顺序 ⇒ 长期缺 dtrait** | 「之前把 load_dotenv 放在函数末尾，导致中间所有 `os.getenv` 全取空值 —— 这个坑让扫码登录长期缺 x-tt-session-dtrait，表现是 check_qrconnect 返回 `error_code=7 访问太频繁`（服务端兜底话术），看着像限频」 | `login_api.py:1304-1307` |
| L7 | **限频期间换码 = 用户扫码作废** | 「2026-08-17 实测踩过：被限频（error_code=7）期间状态读不到，代码仍按 55 秒兜底换了码，而用户正好扫的是那张 —— 扫码直接作废，界面上表现为「我扫过了它还一直弹新码」」 | `login_api.py:2839-2842` |
| L8 | **并发轮询触发限频** | 「以前是查完就丢……同一秒发两个 check_qrconnect，而浏览器是稳定 5.14 秒一发，从不并发。2026-08-22 的日志里「轮询 72 / 73 同为 16:59:04」就是这个，紧跟着就开始返 error_code=7」 | `login_api.py:2862-2866` |
| L9 | **gfkadpd 拦截页** | 「sso 域首次访问会返回一个只做 `document.cookie=gfkadpd=<e>,<t>` 再刷新的拦截页。脚本里两个数字是明文的，取出来直接补上 Cookie 即可，无需执行 JS」 | `login_api.py:2506-2511` |
| L10 | **匿名不透明 Cookie 无法从零生成** | 「`UIFID_TEMP` / `odin_tt` / `bit_env` / `passport_auth_mix_state` 则属于不透明会话材料。当前证据只支持"从真实页面/响应/浏览器运行时捕获后复用"，不支持从零确定性生成。……默认不生成，严格模式始终拒绝」 | `login_api.py:1560-1564` |
| L11 | **dtrait blob 无法纯算复现** | 「而生成它的 `@byted/uc-secure-dtrait-core` 是混淆 SDK，暂时没法纯算复现（见 `utils/dtrait.py` 开头）。所以从 .env 继承已有的那份」 | `login_api.py:1406-1410` |
| L12 | **本项目实测：单次取码拿到 HTML 挑战壳页** | 「项目无 ttwid 生成能力，且需 JS 执行环境 ⇒ **纯接口方案否决**」；「调 `get_qrcode` 接口 ❌ 返回 **9938 字节 JS 挑战壳页**（非 JSON）」 | `FlowCap\docs\adr\ADR-017-调研记录-20260926.md:12-21` |
| L13 | **本项目实测：ADR-017 §4 方案 B 否决** | 「B. 纯接口（无浏览器）| 用 `dyGenerateQRcode` 直接发 HTTP | ❌ **已实测不通**：缺 ttwid 生成能力 + 返回 JS 挑战壳页 | ❌ 否决」 | `FlowCap\docs\adr\ADR-017-im-remote-login-credential-update.md:305` |
| L14 | **本项目实测：转向 camera：的身份改判** | 「前台：λ：已实测 `get_qrcode` 返回 `error_code=0`，`bootstrap` 2.2s / 33 项 cookie 成功」 | `ADR-017:397` |

> **L12/L13 与 L14 的表面矛盾已解决**：L12/L13 是被**旧桥甩点** `dyGenerateQRcode`（`sso.douyin.com` + SDK 1.0.26）踩到的；L14 是**上游新版** `bootstrap_auth + get_qrcode` 的实测。两者对应的不是同一个实现。见 §7。

---

## 6. 其它登录方式的接口实现

| 方式 | 入口 | 接口 | 状态 |
|---|---|---|---|
| **扫码登录** | `qrcode_login()` `login_api.py:2770` | `get_qrcode/` + `check_qrconnect/` | ✅ 主推 |
| **短信验证码（passport-web）** | `send_sms_code()` `:3212` / `phone_login()` `:3284` | `POST /passport/web/send_code/`、`/passport/web/sms_login/` | 上游实现了，但 `strict_dtrait=True` |
| **短信验证码（SSO 备用链）** | `_send_sms_code_sso()` `:3169` / `_phone_login_sso()` `:3189` | `POST /send_activation_code/v2/`、`/quick_login/v2/` | 上游实现了（`login_api.py:787-795`） |
| **一键登录（one-click）** | `builder/auth.py:833` `one_click_read_paths` | 仅有读取路径，**无登录实现** | ❌ **上游无** |

短信手机号/验证码加密方式（`:3216-3226` 注释）：

```python
 overrides:mobile = passport_encrypt(self._format_sms_phone(phone))
 data = {
     "is6Digits": "1",
     "mix_mode": "1",
     "mobile": mobile,
     "type": "3731",
     "fixed_mix_mode": "1",
 }
```

`type=3731` 是浏览器已加密表单值（解码后明文 `24`），不可改（`:3225-3227`）。

**短信路径为何在本项目被禁**（`FlowCap\backend\auto_dm\login_api_vendor.py:200-215`）：

```python
def send_sms_code(self, auth, phone: str):
    """❌ 不可用（缺 820 字节 dtrait 素材）。短信登录请走 RPA 路径。"""
    raise NotImplementedError(self.SMS_UNSUPPORTED_REASON)
```

`login_api_vendor.py:122-125`

```python
SMS_UNSUPPORTED_REASON = (
    "[AUTH-072] API 路径的短信登录不可用：上游 strict_dtrait=True 要求 "
    "x-tt-session-dtrait 恰好 820 字节，需真机捕获的设备指纹素材（本项目暂无）。"
    "短信登录请改用 RPA 路径 auto_dm.login_remote（真浏览器无需 dtrait）。")
```

以及 `login_api.py:2441-2445` 的硬断言：

```python
if strict_dtrait and len(dtrait or "") != 820:
    raise RuntimeError(
        "x-tt-session-dtrait 未达到 Chrome 短信实录的 820 字节，"
        f"当前 {len(dtrait or '')} 字节，已拒绝发送"
    )
```

⇒ **QR 路径不触发这个 820 断言**（`strict=False`），这是 QR 能跑而短信跑不了的直接原因。

---

## 7. 这条路径在 FlowCap 里已经落地到什么程度（重要上下文）

**答案：纯 HTTP 扫码登录已经 vendor 进来、已经封装、已经做成可直接跑的子进程 runner，但默认未启用。**

| 层 | 文件 | 行 | 内容 |
|---|---|---|---|
| vendor 上游 | `FlowCap\vendor\douyin_spider_upstream\dy_apis\login_api.py` | 3410 行 | 与 `DouYin_Spider_git` 完全相同 |
| 适配层 | `FlowCap\backend\auto_dm\login_api_vendor.py` | `:250` `get_qrcode` / `:281` `check_qrcode` | 薄包装 + 能力边界表 |
| 子进程 runner | `FlowCap\backend\login_qr_api_runner.py` | 258 行 | `bootstrap → get_qrcode → 轮询 → _follow_login_redirect → result.json` |
| 前置分发 | `FlowCap\backend\main.py` | `:22-33` | `--qr-api-runner` 分发，在任何项目 import 之前 |
| API 编排 | `FlowCap\backend\api\accounts.py` | `:495-556` `_api_scan_login` | 含 sessionid+四件套**硬门禁** |
| 路由决策 | `FlowCap\backend\api\accounts.py` | `:223-226` | **默认关闭**，需 `DY_LOGIN_QR_BACKEND=api` |

**为什么已经实现却默认关闭**（`accounts.py:206-212`，即 §4 证据 4-4）：身份矛盾。

**且当前默认走的是「接口桥」而不是纯 API**（`login_remote.py:1044-1062`）：

```python
# 【设计来源】用户 2026-09-30 指定：
#   「先起个无头浏览器，然后把 API 使用的方法在无头浏览器上面进行监测……
#     相当于一个桥梁，把浏览器上面的接口暴露给前端」。
# 【为什么优于 DOM 截图】二维码数据来自**网络响应**（原始 URL），
#   不依赖元素锚点/像素解码 ⇒ 对改版免疫；且**同一 Firefox 身份**，
#   无 API 纯协议路径的「Chrome ⊗ Firefox」跨信号矛盾。
```

**⇒ 用户当前的质疑（依赖 UI 坐标）实际上已被 ADR-034 部分解决**：接口桥不点 tab、不截图、不碰坐标，二维码取自 `get_qrcode` 的响应体，并对 DOM 改版免疫。**真正还依赖 UI 的，只有 fallback 层 `_lr.prepare_qr_login`（DOM 截图 + cv2 解码）**。

---

## 8. 结论与建议

### 8.1 直接回答六个问题

1. **纯 HTTP 取码**：**有**。`DYLoginApi.get_qrcode()`（`login_api.py:2626`），`GET https://login.douyin.com/passport/web/get_qrcode/`。需 `aid=6383`、passport SDK 3.4.4 全套版本参数、`sign`+`qs`（passport 自有 sha256+XOR5）、`a_bogus`、`msToken`、`device_platform=web_app`。

2. **轮询**：**有**。`POST https://login.douyin.com/passport/web/check_qrconnect/`，`token` 在 **body**（非 query），`device_fp=True`。`status ∈ {new, scanned, confirmed, expired}`；`error_code=7` = 限频兜底话术。

3. **拿凭证**：不是响应直返，`confirmed` 时取 `redirect_url` 逐跳 GET 收 `Set-Cookie`（`login_api.py:3351`）；另外必须从 `bd-ticket-guard-server-data` 响应头解出 `ticket`/`ts_sign`/`client_cert`（`utils/passport.py:181`）。

4. **身份一致性**：必须 curl_cffi 冒充 Chrome（`utils/http_client.py:2-7`）；当前 UA=Chrome151 而 TLS=chrome150（残缺，`http_client.py:68-70`）。**上游未写"必须与日常业务请求身份一致"这条规则 ⇒ 「跨身份矛盾」是本项目自己的判定**（`accounts.py:206-212`），但耦合证据链（H-27 / ADR-016）很强。

5. **已知失败/限制**：14 条，见 §5。最硬的三条：**走错域 4031**、**SDK 版本过期 4031**、**缺 sign/qs → 伪装成 error_code=7**。

6. **其它登录方式**：短信两条链（passport-web `send_code`/`sms_login`，SSO `send_activation_code/v2`/`quick_login/v2`）上游都有实现，但**均需 820 字节 dtrait ⇒ 本项目已显式禁用**。**一键登录：上游无接口实现**（只有 `one_click_read_paths` 读取路径）。

### 8.2 给 FlowCap 的判断

| 方案 | 是否可行 | 关键代价 |
|---|---|---|
| **纯 HTTP（启用 `DY_LOGIN_QR_BACKEND=api`）** | ✅ 代码与能力已就位 | **身份切换**：出码身份变 Chrome，与账号日常 Camoufox/Firefox 会话构成跨信号矛盾 ⇒ 可能重蹈「凭证 3h 失效」 |
| **接口桥（当前默认）** | ✅ 已在跑 | 仍需起 Camoufox 进程，但**不依赖 UI 坐标**、对 DOM 改版免疫 |
| **纯 HTTP + 全链路改 Chrome 身份** | ✅ 理论最优 | 需要把整个 vbrowser 从 Camoufox 换成 Chrome 系，是大改，且丢掉 Camoufox 的反检测优势 |
| 继续用 | ❌ | 不碰 UI，但**没有解决用户提的坐标问题**（那是桥已经解决的） |

> **推断（标注）**：用户感知到的「依赖 UI 坐标」问题，**是 fallback 层的遗留印象，而非当前默认路径**。当前默认已经是接口桥（`login_remote.bridge_qr_login`），它取的是网络响应不是像素/坐标。建议先核实用户报障是否来自 `prepare_qr_login` fallback 分支；若是，修法应是**提高桥成功率**而不是改走纯 API。此为推断，需实测日志确认。

### 8.3 若要切纯 API，必须同时满足的前置条件

1. 安装 `curl_cffi`（缺则由 `login_api_vendor.py` 抛 `AUTH-071`）；
2. `.env` 提供 `DY_DTRAIT_BLOB` 或 `DY_SESSION_DTRAIT`（无则缺 `x-tt-session-dtrait`，`login_api.py:1412` 警告：可能被判需二次验证）；
3. 接受并复核身份矛盾问题 —— **这是唯一真正的决策点，不是技术问题**；
4. 必须走子进程（`login_api_vendor.py:_import_upstream` 的 AUTH-073 命名空间碰撞；同进程会导致静默半坏）。

---

## 附：本次侦察未做的事（诚实声明）

- **未跑任何会触发真实登录的脚本**（按任务约束）。
- **未实測 `get_qrcode` 当前是否仍返回 `error_code=0`** —— 本文对该能力的可用性判定，**引用的是本项目 2026-09-26 的既有实测结论**（`ADR-017:397`、`login_api_vendor.py:190-193`），不是本次新做的实測。抖音服务端在 2026-09-26 至 2026-10-01 之间是否滚动了 passport SDK 版本（当前锁定 3.4.4），**未验证**。
- 未阅读 `utils/acrawler.py` 的 Node runner 内部实现（VMP 採样），仅确认其定位为「把页面 acrawler bundle 搬到 Node 里跑以产出 `__ac_signature`」。
