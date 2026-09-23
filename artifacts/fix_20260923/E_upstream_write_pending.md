# E · 上游写接口对齐（T1 `sendMsgInRoom` / T2 `publish_comment`）

> **本轮性质**：**代码对齐 + 离线可验证证据**。用户本轮**明确不真发**，故
> **端到端实机验证一律标注 `pending`，未执行、未通过**。本文件不声称任何「已跑通」。
>
> 任务：按上游源码把 DYAutoDM_v2 的 T1/T2 两个**写接口**（发弹幕 / 发评论）对齐上游。
> 本仓：`C:\Users\LOX\Desktop\DYchajian`，分支 `design/better-douyin`，HEAD `b455192`，版本 `0.44.53`。
> 落盘：2026-09-23。

---

## 〇、先回答「依据哪个上游文件 / 哪个 sha」

### 三个候选目录的实测比对（**先比版本，再动代码**）

| 候选目录 | 内容形态 | `dy_apis/douyin_api.py` sha256(12) | 行数 | `reply_to_reply_id` | `web_rid` | `check_risk_response` | 判定 |
|---|---|---|---|---|---|---|---|
| `_ext_repos/DouYin_Spider_git` | **完整 git 仓库**（有 `.git`） | `5b92eff58818` | 2772 | 5 | 45 | 31 | ✅ **唯一最新 → 本轮依据** |
| `_ext_repos/DouYin_Spider-master` | 无 `.git` 的旧快照（mtime 2026-09-10 19:19） | `788701051ff9` | 2047 | 0 | 1 | 0 | ❌ 早于 09-20 两个对齐 commit |
| `_ext_repos/DouYin_Spider_latest` | 仅 2 个散落文件（`builder_proto.py`、`dy_apis_douyin_im_media.py`），**无 `dy_apis/`** | — | — | — | — | — | ❌ 与这两个函数无关 |

**判定依据（机械可复核）**：`DouYin_Spider_git` 的 `dy_apis/douyin_api.py` 拥有
「最后一次改动 = 2026-09-20 01:33「fix: align work comment publishing」」的提交历史；
`DouYin_Spider-master` 的同名函数体与 `DouYin_Spider_git` 里 **`251075e` 之前**的版本
逐字相同（即 `Origin = douyin_url`、`enter_from='web_others_homepage'`、`random.randint(1000,20000)`、
`text_extra = "[]"`）—— 属**过时快照**，故排除。

```bash
# 复核命令（只读）
cd C:/Users/LOX/Desktop/DYchajian/_ext_repos/DouYin_Spider_git
git log --oneline -3 -- dy_apis/douyin_api.py
#   df52357 fix: align work comment publishing          ← T2
#   251075e feat: align live room comment sending       ← T1
#   41ed6c5 feat: add live PK rank APIs and WebSocket events
git log -1 --format='%H %ci' 251075e   # 251075e…  2026-09-20 01:26:19 +0800
git log -1 --format='%H %ci' df52357   # df52357…  2026-09-20 01:33:06 +0800
git log -1 --format='%H %ci' 4479ea7   # 4479ea7…  2026-09-20 01:36:53 +0800  (HEAD, merge PR #91)
sha256sum dy_apis/douyin_api.py        # 5b92eff588184a60bf7b4d313559dd08bcaacb3320bcb30710ca782da39a62d7
git status --porcelain                 # (clean)
```

### 本轮依据的精确坐标

```
文件：_ext_repos/DouYin_Spider_git/dy_apis/douyin_api.py
sha256：5b92eff588184a60bf7b4d313559dd08bcaacb3320bcb30710ca782da39a62d7
仓库 HEAD：4479ea784bf3e63e75fcbe4ca985f84678d46b27（Merge PR #91，2026-09-20 01:36:53 +0800）
  T1 来源 commit：251075ec31af4eeba86d74dc34bbd61c669a9c94（2026-09-20 01:26:19）
  T2 来源 commit：df52357d569d3c9a7d691dc895c13fc1aecd2620（2026-09-20 01:33:06）
```

---

## 一、T1 `sendMsgInRoom`（发弹幕）

### ① 上游溯源（源码片段，逐字）

来源：`_ext_repos/DouYin_Spider_git/dy_apis/douyin_api.py`，第 **1930–1973** 行，
commit `251075e`（`feat: align live room comment sending`，2026-09-20 01:26）：

```python
    @staticmethod
    def sendMsgInRoom(auth, room_id: str, content: str = '', **kwargs):
        """发送直播间评论。

        直播前端调用 ``/webcast/room/chat/`` 的 GET 接口，房间参数名仍是
        ``room_id``（值来自前端的 ``room_id_str``）。直播域的 Origin 和
        bd-ticket 证书也必须按 ``live.douyin.com`` 生成；沿用主站 Origin
        会得到空响应或业务失败。
        """
        api = "/webcast/room/chat/"
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = kwargs.get('referer') or f"{DouyinAPI.live_url}/{kwargs.get('web_rid', room_id)}"
        headers.set_header("Origin", DouyinAPI.live_url)                     # ← 核心：直播域
        headers.with_bd(api, auth, origin=DouyinAPI.live_url)                # ← 带 origin
        headers.with_csrf(auth.cookie_str)
        headers.set_referer(refer)
        params = Params()
        params.add_param("aid", '6383')
        params.add_param("app_name", 'douyin_web')
        params.add_param("live_id", '1')
        params.add_param("device_platform", 'web')
        params.add_param("language", 'zh-CN')
        params.add_param("enter_from", kwargs.get('enter_from', 'link_share'))   # ← 旧: 'web_others_homepage'
        params.add_param("cookie_enabled", 'true')
        params.add_param("screen_width", get_profile()["screen_width"])
        params.add_param("screen_height", get_profile()["screen_height"])
        params.add_param("browser_language", 'zh-CN')
        params.add_param("browser_platform", 'Win32')
        params.add_param("browser_name", get_profile()["browser_name"])
        params.add_param("browser_version", get_profile()["browser_version"])
        params.add_param("room_id", str(room_id))                            # ← 旧: 裸 room_id
        params.add_param("content", content)
        params.add_param("type", str(kwargs.get('type', '0')))               # ← 旧: '0' 常量
        for key in ('episode_info_str', 'flow_time', 'team_id', 'camera_id',  # ← 纯新增 7 项
                    'emoji_id', 'rtf_content', 'paste_edit_method'):
            value = kwargs.get(key)
            if value not in (None, ''):
                params.add_param(key, value)
        params.add_param("msToken", auth.msToken)
        params.with_a_bogus(host=LIVE_HOST)
        res = requests.get(f'{DouyinAPI.live_url}{api}', headers=headers.get(), params=params.get(),
                           cookies=auth.cookie, verify=False)
        check_risk_response(res)                                             # 本项目无此助手（见 §五）
        return res.json()
```

**上游给出的「为什么是真 bug」（原文）**：
> 直播域的 Origin 和 bd-ticket 证书也必须按 `live.douyin.com` 生成；沿用主站 Origin 会得到空响应或业务失败。

**上游改动前的同一函数（`251075e^`，用于确认旧形态确实存在）**：与 §一·② 本项目现状**逐字段一致**
（`Origin = douyin_url`、`with_bd(api, auth)`、`enter_from='web_others_homepage'`、裸 `room_id`、`type='0'`、无 7 项）。

### ② 现状（改动前，实测原文）

`backend/dy_apis/client_live.py:598-628`（HEAD `b455192`）：

```python
    @staticmethod
    def sendMsgInRoom(auth, room_id: str, content: str = ''):        # ❌ 无 **kwargs
        api = "/webcast/room/chat/"
        headers = HeaderBuilder().build(HeaderType.GET)
        refer = f"https://live.douyin.com/{room_id}"                 # ❌ 不可覆盖 / 无 web_rid
        headers.set_header("Origin", DouyinAPI.douyin_url)           # ❌ 主站 Origin
        headers.with_bd(api, auth)                                   # ❌ 无 origin
        headers.with_csrf(auth.cookie_str)
        headers.set_referer(refer)
        params = Params()
        params.add_param("aid", '6383')
        params.add_param("app_name", 'douyin_web')
        params.add_param("live_id", '1')
        params.add_param("device_platform", 'web')
        params.add_param("language", 'zh-CN')
        params.add_param("enter_from", 'web_others_homepage')        # ❌ 与上游不一致
        params.add_param("cookie_enabled", 'true')
        params.add_param("screen_width", '2560')
        params.add_param("screen_height", '1440')
        params.add_param("browser_language", 'zh-CN')
        params.add_param("browser_platform", 'Win32')
        params.add_param("browser_name", 'Edge')
        params.add_param("browser_version", '130.0.0.0')
        params.add_param("room_id", room_id)                         # ❌ 未 str()
        params.add_param("content", content)
        params.add_param("type", '0')                                # ❌ 不可覆盖
        params.add_param("msToken", auth.msToken)
        params.with_a_bogus(host=LIVE_HOST)
        res = requests.get(f'{DouyinAPI.live_url}{api}', headers=headers.get(), params=params.get(),
                           cookies=auth.cookie, verify=tls_verify())
        return safe_json(res)
```

> 旁证（本项目自身口径不一致）：同文件其余 8 个直播接口
> （`:144 / :231 / :346 / :400 / :438 / :470 / :502 / :534`）**全部**写 `DouyinAPI.live_url`，
> 只有 `sendMsgInRoom`（`:603` pre-fix）与 `diggLiveRoom`（`:571`）落在主站。

### ③ 修法（diff）

```diff
--- a/backend/dy_apis/client_live.py
+++ b/backend/dy_apis/client_live.py
@@ -598,10 +598,10 @@
-    def sendMsgInRoom(auth, room_id: str, content: str = ''):
+    def sendMsgInRoom(auth, room_id: str, content: str = '', **kwargs):
         api = "/webcast/room/chat/"
         headers = HeaderBuilder().build(HeaderType.GET)
-        refer = f"https://live.douyin.com/{room_id}"
-        headers.set_header("Origin", DouyinAPI.douyin_url)
-        headers.with_bd(api, auth)
+        refer = kwargs.get('referer') or f"{DouyinAPI.live_url}/{kwargs.get('web_rid', room_id)}"
+        headers.set_header("Origin", DouyinAPI.live_url)
+        headers.with_bd(api, auth, origin=DouyinAPI.live_url)
@@
-        params.add_param("enter_from", 'web_others_homepage')
+        params.add_param("enter_from", kwargs.get('enter_from', 'link_share'))
@@
-        params.add_param("room_id", room_id)
+        params.add_param("room_id", str(room_id))
         params.add_param("content", content)
-        params.add_param("type", '0')
+        params.add_param("type", str(kwargs.get('type', '0')))
+        for key in ('episode_info_str', 'flow_time', 'team_id', 'camera_id',
+                    'emoji_id', 'rtf_content', 'paste_edit_method'):
+            value = kwargs.get(key)
+            if value not in (None, ''):
+                params.add_param(key, value)
```

**与本项目的两处**有意**偏离（必须显式上报，不静默落地）：**

1. **保留 `verify=tls_verify()`**（上游是 `verify=False`）。本项目有 TLS 策略中心
   `utils/tls_policy.py`，全链路统一走它；照抄 `verify=False` 是**倒退**且违反项目约定。
2. **未移植 `check_risk_response(res)`**（上游 `dy_apis/douyin_api.py:58`）。该助手在本仓
   **全仓 0 命中**；移植它属「写接口失败显式化」的独立工作线（UP-L1 A1-8），且会引入新的
   公共助手文件 —— **超出本任务「只碰两个文件」的边界**。已如实登记为未做项（§五·5.3）。

**`with_bd(..., origin=live_url)` 的真实作用：空操作（不夸大）**
本项目 `builder/header.py:20-22` 的 `with_bd` **签名收 `origin`，但函数体从不使用它**
（没有上游那种 `ecdh_key(aid=..., origin=...)`）。本轮用测试实测
（`test_with_bd_receives_live_origin`）：`generate_bd_ticket_client_data` 只收到
`(api, ticket, ts_sign, private_key)`，**没有 origin**。⇒ 这句是「形态对齐」，
**不得**据此宣称「证书已按直播域生成」。真正的差异是未移植的 `ecdh_key`（UP-L1 A2-6），属另一工作线。

### ④ 离线测试输出

见 §三（整模块 40 用例全绿；T1 相关用例分布见 §三 分组表：`TestT1SendMsgInRoom` 8 条 +
`TestSourceGates`/`TestUpstreamParity` 中的 T1 条款）。

### ⑤ 负控结果

见 §四（回退成旧写法 → **RED**，T1 相关 6 条明确变红）。

### ⑥ 实机端到端验证 —— **未执行（pending）**

> **本轮未发任何弹幕。** 不声称「已投递」，也不声称「已修复线上行为」。

---

## 二、T2 `publish_comment`（发作品评论）

### ① 上游溯源（源码片段，逐字）

来源：`_ext_repos/DouYin_Spider_git/dy_apis/douyin_api.py`，commit `df52357`
（`fix: align work comment publishing`，2026-09-20 01:33）；HEAD 状态行 **1976–2098**。

**改动部分（HEAD 逐字）：**

```python
    @staticmethod
    def publish_comment(auth, aweme_id: str, content: str = '', reply_id="", **kwargs):
        """发布评论 ..."""
        api = "/aweme/v1/web/comment/publish"
        # 评论发布是 bd-ticket-guard 的强校验写接口。调用方从同一个
        # 浏览器会话抓到的短时凭据可以通过 kwargs 显式传入 ...
        for name in (
                'ticket', 'ts_sign', 'client_cert', 'private_key',
                'dtrait_blob', 'dtrait_profile', 'session_dtrait'):
            value = kwargs.get(name)
            if value is not None:
                setattr(auth, name, value)
        if not auth.ticket_matches_session():                      # ← 硬门禁①（本项目不移植）
            raise RuntimeError('评论发布需要与当前 Cookie 同会话的 ticket/ts_sign；...')
        if not (getattr(auth, 'dtrait_blob', None)
                or getattr(auth, 'dtrait_profile', None)
                or getattr(auth, 'session_dtrait', None)):          # ← 硬门禁②（本项目不移植）
            raise RuntimeError('评论发布需要同一浏览器会话的 dtrait_blob/profile 或 ...')
        ...
        data = {
            "aweme_id": aweme_id,
        }
        if reply_id != "":
            data["reply_id"] = reply_id
        reply_to_reply_id = kwargs.get('reply_to_reply_id', '')      # ← 纯新增
        if reply_to_reply_id != "":
            data["reply_to_reply_id"] = reply_to_reply_id
        # PC Web 的发送函数默认传 0；随机值是旧版脚本遗留，会让服务端
        # 把评论当成播放器内操作，导致发布接口偶发业务失败。
        data["comment_send_celltime"] = kwargs.get('comment_send_celltime', 0)    # ← 去随机
        data["comment_video_celltime"] = kwargs.get('comment_video_celltime', 0)  # ← 去随机
        data["one_level_comment_rank"] = kwargs.get('one_level_comment_rank', -1)
        data["paste_edit_method"] = kwargs.get('paste_edit_method', "non_paste")
        data["text"] = content
        # 前端发送 JSON.stringify(textExtra)，不能把 list 直接交给
        # requests，否则签名 body 与实际表单编码会不一致。
        text_extra = kwargs.get('text_extra', [])
        data["text_extra"] = (text_extra if isinstance(text_extra, str) else
                               json.dumps(text_extra, ensure_ascii=False,
                                          separators=(',', ':')))        # ← JSON.stringify 语义
        params.with_a_bogus(data)
        ...
        res = requests.post(f'{DouyinAPI.douyin_url}{api}', headers=headers.get(), params=params.get(),
                            cookies=auth.cookie, data=data, verify=False)
        check_risk_response(res)
        return res.json()
```

**上游改动前（`df52357^`）的同一段 —— 确认旧写法确切形态，并揭示调用约定：**

```python
        data["comment_send_celltime"] = random.randint(1000, 20000)
        data["comment_video_celltime"] = random.randint(1000, 20000)
        data["one_level_comment_rank"] = -1
        data["paste_edit_method"] = "non_paste"
        data["text"] = content
        # 必须是字符串 "[]"：空 list 会被 requests 直接从表单里丢掉，
        # 与参与 a_bogus 计算的 body 对不上
        data["text_extra"] = "[]"
```

> ⚠️ **关键判读**：上游注释「空 list 会被 requests 直接从表单里丢掉」直接证明上游
> `publish_comment` 是 `requests.post(..., **data=data**)`（form-urlencoded），**不是 `json=`**。
> 本轮已**实测本项目同样是 `data=data`**（pre-fix `client_comments.py:296-297`），
> 故改法口径与上游**同构**，可直接照抄 —— 本文件 §二·③ 给出实测依据。

### ② 现状（改动前，实测原文）

`backend/dy_apis/client_comments.py:236-298`（HEAD `b455192`）：

```python
    @staticmethod
    def publish_comment(auth, aweme_id: str, content: str = '', reply_id="", **kwargs):
        ...
        data = {
            "aweme_id": aweme_id,
            "comment_send_celltime": random.randint(1000, 20000),       # ❌ 随机
            "comment_video_celltime": random.randint(1000, 20000),      # ❌ 随机
        }
        if reply_id != "":
            data["reply_id"] = reply_id
        data["text"] = content
        data["text_extra"] = []                                          # ❌ 裸 list
        params.with_a_bogus(data)
        ...
        res = requests.post(f'{DouyinAPI.douyin_url}{api}', headers=headers.get(), params=params.get(),
                            cookies=auth.cookie, data=data, verify=tls_verify())   # ← data=，不是 json=
        return safe_json(res)
```

### ③ 「`text_extra = []` 到底怎么进请求体」——判定依据（读代码 + 实测，不凭印象）

| 证据 | 读数 | 结论 |
|---|---|---|
| pre-fix `client_comments.py:296-297` | `requests.post(..., **data=data**, verify=tls_verify())` | 走 **form-urlencoded（`data=`）**，不是 `json=` |
| [实测] `RequestEncodingMixin._encode_params({'text_extra': []})` | `''`（**整键被丢弃**） | wire 上**没有** `text_extra` |
| [实测] `utils.dy_util.splice_url({'text_extra': []})` | `'text_extra=%5B%5D'` | 参与 a_bogus 的签名输入**有** `text_extra` |
| ⇒ 结论 | `splice_url(data) != _encode_params(data)` | **签名 body ≠ 上线字节**（确定性缺陷） |
| [实测] `_encode_params({'text_extra': [{'type':1}]})` | `'text_extra=type'`（dict 被拆成子项） | 非空 list 形态同样错误 |
| [实测]（改动后）`text_extra='[]'` | `splice_url == _encode_params` → `True` | 修复后一致 |

⇒ **决定：照抄上游 `isinstance(str)` 分支 + `json.dumps(..., ensure_ascii=False, separators=(',',':'))`。**
不传 `text_extra` 时默认 `[]` → 序列化得 `'[]'`，**与改动前语义等价**（零回归）；
且此后 `splice_url(data) == _encode_params(data)` 恒成立（本轮有测试钉死）。

> 顺带说明（**不影响本决定**）：`urlencode` 对空 list 的行为与 `requests` 版本有关
> （本项目 `requests 2.33.0`；旧版行为是 `str([]) == '[]'` → 反而一致）。无论哪个版本，
> 「**传 `str`**」都天然满足「签名输入 == 上线字节」，故这是**版本无关的正确形态**。

### ④ 修法（diff）

```diff
--- a/backend/dy_apis/client_comments.py
+++ b/backend/dy_apis/client_comments.py
@@ -284,13 +284,25 @@
         data = {
             "aweme_id": aweme_id,
-            "comment_send_celltime": random.randint(1000, 20000),
-            "comment_video_celltime": random.randint(1000, 20000),
         }
         if reply_id != "":
             data["reply_id"] = reply_id
+        reply_to_reply_id = kwargs.get('reply_to_reply_id', '')
+        if reply_to_reply_id != "":
+            data["reply_to_reply_id"] = reply_to_reply_id
+        # 上游 df52357：PC Web 发送函数默认传 0；随机值会让服务端把评论当成
+        # 播放器内操作，导致发布接口偶发业务失败。
+        data["comment_send_celltime"] = kwargs.get('comment_send_celltime', 0)
+        data["comment_video_celltime"] = kwargs.get('comment_video_celltime', 0)
+        data["one_level_comment_rank"] = kwargs.get('one_level_comment_rank', -1)
+        data["paste_edit_method"] = kwargs.get('paste_edit_method', "non_paste")
         data["text"] = content
-        data["text_extra"] = []
+        # 上游 df52357：前端发送 JSON.stringify(textExtra)。不能把 list 直接交给
+        # requests —— data= 走 urlencode 时空 list 会被整键丢弃，非空 list 的 dict
+        # 会被拆成子项，两种都与参与 a_bogus 的 body 对不上。
+        text_extra = kwargs.get('text_extra', [])
+        data["text_extra"] = (text_extra if isinstance(text_extra, str) else
+                              json.dumps(text_extra, ensure_ascii=False,
+                                         separators=(',', ':')))
```

### ⑤ 明确**不**照抄的上游半句（能力缺口，硬约束）

上游 `df52357` 同时新增了 `ticket_matches_session()` 与 `dtrait_*` 两处**硬门禁**
（不满足即 `raise RuntimeError`）。本项目：

- `DouyinAuth`（`builder/auth.py:14-26`）**无** `ticket_matches_session` / `dtrait_blob` /
  `dtrait_profile` / `session_dtrait` / `session_dtrait_header`（`hasattr` 全 `False`）；
- `.env.enc` **无** `DY_DTRAIT_BLOB` / `DY_SESSION_DTRAIT` 键；
- 项目既定定调是「能力缺失时**降级**」（`builder/header.py:34-38` 明文）。

⇒ **照抄会让 `publish_comment` 每次必抛**（静态可证），故**不移植**；
本轮用测试把它**钉死为缺席**（`test_no_unintended_hard_gates`），防止将来被误加。

### ⑥ 实机端到端验证 —— **未执行（pending）**

> **本轮未发任何评论。** 不声称「已投递」。

---

## 三、离线测试输出（隔离运行）

**隔离根**：`DY_APP_ROOT=$LOCALAPPDATA/Temp/fixE`（本轮实测展开为 `C:\Users\LOX\AppData\Local\Temp\fixE`）。
**解释器**：`C:\Users\LOX\AppData\Local\Programs\Python\Python314\python.exe`（Python 3.14.6）。
**被测模块**：新增 `backend/test_upstream_write_align_t1_t2.py`（只跑这一个模块，40 用例）。

```bash
cd C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend
DY_APP_ROOT="$LOCALAPPDATA/Temp/fixE" \
  "C:/Users/LOX/AppData/Local/Programs/Python/Python314/python.exe" \
  -m unittest test_upstream_write_align_t1_t2 -v
```

**实测输出（尾部，逐字）：**

```
Ran 40 tests in 2.048s

OK
```

**用例清单（40 条）**

| 组 | 条数 | 断言要点 |
|---|---|---|
| `TestT1SendMsgInRoom` | 8 | Origin == `https://live.douyin.com`（**且 != 主站**）、referer 默认/覆盖、`web_rid` 只影响 referer、`enter_from` 默认 `link_share`、`type` 可覆盖且为 str、7 个可选参数「非空才发」、`with_bd` 收到 live origin（并证明其为空操作）、a_bogus `host=live.douyin.com` |
| `TestT2PublishComment` | 9 | celltime 默认 `0` / 可覆盖、`text_extra` 默认 `'[]'`（str）、非空 list → 紧凑 JSON（`ensure_ascii=False`）、已是 str 则透传、`reply_to_reply_id` 可传/空串不发、`reply_id` 仍可用、rank/paste 默认与可覆盖 |
| `TestT2SignatureConsistency` | 4 | 旧形态签名 ≠ wire、新形态签名 == wire（空 list 与非空 list 两例）、celltime 取值与一致性无关 |
| `TestNegativeControl` | 5 | 见 §四 |
| `TestSourceGates` | 7 | 源码级机械门禁（Origin / 可选参数 / 无 random / text_extra JSON / 无硬门禁 / 实机标志仍为 False 等） |
| `TestUpstreamParity` | 5 | **从上游快照逐字提取**期望值再与本项目比对（`enter_from`、7 项可选参数、celltime/text_extra 六个字面量、referer 表达式） |
| **合计** | **40** | |

**隔离性证据（零出站请求）**：测试全程 `mock.patch.object(<宿主模块>, "requests", MagicMock)`，
`requests.get/post` 的 `side_effect` 只做「记录 + 返回 MagicMock」；并对
`generate_a_bogus` / `generate_webid` / `generate_csrf_token` / `generate_bd_ticket_client_data` /
`generate_ree_key` 打桩。`with_bd` 仍用**真实 EC P-256 私钥**跑通（证明真实链路可组装）。
每个用例还校验被测函数的 `__globals__` **确为宿主模块**（防「测错对象」）。

**无回归旁证**：同一隔离根下复跑既有守卫
`test_abogus_host_guards test_no_dup_dict_keys test_no_loguru_printf_style test_upstream_p1 test_upstream_p5`
→ `Ran 69 tests … OK`。

---

## 四、负控结果（把改动回退成旧写法 → 必须变红）

> **纪律**：不 `commit` / 不 `checkout` / 不 `stash` / 不 `clean`。
> 做法：用 Python 读 `git show b455192:<path>` 把两个文件**临时**写回改动前内容
> （先备份工作区文本），跑同一测试模块，再**原样还原**并校验 sha256 一致。

**结果（实测）：**

| 步骤 | 退出码 | 结果 |
|---|---|---|
| 回退成旧写法 → 跑测试 | `1` | **RED — `FAILED (failures=15, errors=8)`** |
| 还原 → 再跑测试 | `0` | **GREEN — `OK`（40 tests）** |
| 还原前后 sha256 一致 | — | ✅ `client_live.py` `f8b1459015d5…`、`client_comments.py` `af72aebff78e…` |

**变红清单（逐条，证明测试真的有鉴别力）**

T1（5 条 FAIL + 5 条 ERROR）：

```
FAIL  test_origin_is_live_domain_not_main_site            ← Origin 仍是主站
FAIL  test_full_contract                                  ← 整组契约（Origin 为第一条失败）
FAIL  test_t1_live_origin_in_source                       ← 源码机械门禁
FAIL  test_t1_optional_key_tuple_matches_upstream
FAIL  test_upstream_enter_from_default_matches_ours
ERROR test_referer_default_and_override / test_web_rid_only_affects_referer /
      test_referer_kwarg_wins_over_web_rid / test_enter_from_and_type_overrides /
      test_optional_params_emitted_only_when_nonempty      ← 旧签名无 **kwargs
```

T2（10 条 FAIL + 3 条 ERROR）：

```
FAIL  test_celltime_default_zero / test_celltime_overridable
FAIL  test_text_extra_default_is_json_string / test_text_extra_list_is_json_encoded /
      test_text_extra_str_passthrough
FAIL  test_full_contract
FAIL  test_t2_no_random_celltime_in_source / test_t2_text_extra_json_in_source /
      test_t2_reply_to_reply_id_in_source
FAIL  test_upstream_celltime_and_text_extra_literals_match_ours
ERROR test_reply_to_reply_id / test_rank_and_paste_defaults / test_rank_and_paste_overridable
```

**「不靠第一条兜底」的额外证据**（`TestNegativeControl`，绿态下 5 条全过）：

- **T1**：旧实现原样 → 变红，且失败原因含 `Origin`；**只把 Origin 手工改成 `live_url`** → **仍变红**
  （暴露 `referer` / `enter_from` 等后续缺陷）⇒ 不是单条断言兜底。
- **T2**：旧实现原样 → 变红，第一条失败是 `celltime`；**只把 celltime 手工修成 0** → **仍变红**
  （暴露 `text_extra`）⇒ 两条缺陷各有独立鉴别力。
- **T2 签名一致性**：直接对旧 `data` 用**真实** `RequestEncodingMixin._encode_params` 与 `splice_url`
  计算 ⇒ `assertNotEqual` 成立；并断言 wire **丢键** `text_extra`、签名**有键** `text_extra=%5B%5D`。

---

## 五、诚实标注：端到端实机验证 **未验证（pending）**

### 5.1 状态表

| 项 | 状态 |
|---|---|
| 代码对齐（T1 / T2） | ✅ 已落盘（§六 文件清单） |
| 离线请求构造层测试（含负控、上游逐字对账） | ✅ 40/40 绿，隔离根运行 |
| **真实发送弹幕（T1）** | ❌ **未执行 —— pending 用户下次真实场景** |
| **真实发布评论（T2）** | ❌ **未执行 —— pending 用户下次真实场景** |
| 服务端是否接受新参数（`enter_from=link_share`、`celltime=0`、`text_extra='[]'`） | ❌ **未知**（本轮无实测数据，**不得**当作已验证） |
| `dtrait` 能力缺口（评论发布可能被风控拦成 200+空 body） | ⚠️ **未解决**（本项目无 dtrait 来源；`safe_json` 会把空响应降级为 `{}` ⇒ 上层可能**假成功**） |

> **不得声称「已端到端验证」。** 本轮结论上限是：
> 「**请求构造与上游逐字段一致，且离线可证明旧写法确实不一致**」。

### 5.2 给用户的实机验证步骤（本轮未执行）

#### 前置（照 `scripts/diag/verify_text_send_delivery.py` 的协议精神）

```bash
# ① 环境门禁：必须隔离根（脚本会拒绝主分支根 C:\temp\dyautodm_test）
export DY_APP_ROOT="C:\\temp\\dyautodm_design"

# ② 清残留进程（铁律：走 /quit 优雅退出，禁 taskkill /F）；确认只剩 1 个 BCC
powershell -NoProfile -Command "Get-CimInstance Win32_Process | ? {$_.Name -like 'python*'} | Select ProcessId,CreationDate"
```

**③ 进程启动时间必须晚于源码 mtime**（否则跑的是旧代码 —— 这条在 UP-L1 §三·L1-a 有明文）。

**加载凭证的方式（复用既有诊断脚本的 bootstrap，不新写一套）**：
`scripts/diag/*.py` 的 `_bootstrap()` 会 读 `$DY_APP_ROOT/members/.session.json`
→ 设 `DY_MEMBER` / `DY_MEMBER_KEY` → 返回 `.../accounts`；
再用 `DYLoginApi._load_auth_from_env(<accounts>/<账号>/.env)` 取 `auth`。
把自己的弹幕/评论探针放到 `scripts/diag/` 下（**本任务不新增该文件**，避免越界）。

#### T1（弹幕）判据 —— 需要**正在开播**的、你**自己的**直播间

| # | 判据 | 通过标准 |
|---|---|---|
| ① | **回包是 JSON 且 `status_code == 0`** | 不是空 body、不是 HTML 挑战页 |
| ② | **在随后拉到的弹幕流里看到该条 + 你自己的昵称** | 用 `LiveChatHook.feed_snapshot` 或 BCC `/exec_js` 读直播间弹幕 DOM；内容用带时间戳的固定串（如 `[UP-L1-验证-<ts>]`） |
| ③ | ①+② **同时**成立 | 才算「已投递」 |
| ④ | **失败分类** | 若回包空/非 JSON：**看响应头** —— `X-Vc-Bdturing-Parameters`（人机验证）/ `X-Tt-Verify-Passport-Decision`（二次验证），并记 `X-Tt-Logid`。**「0 字节 200」既不算成功、也不算「接口坏了」** |
| ⑤ | **对照（关键，唯一可信方式）** | 同内容各跑一次「主站 Origin」（把 `client_live.py` 那 1 行回退）与「直播域 Origin」，比较回包形态差异 —— 这是验证 A1-1 是否真有效的**唯一可信方式** |

**调用参数提醒（上游语义，已实测确认）**：`room_id` 是 query 参数本身（值来自前端
`room_id_str`）；`web_rid` 只用于拼 referer。你项目里 `web_rid`
（`core/auto_dm.py:487-508` 从 live_url 解析）与 `room_id` 是否等价，**这一条仍需你实机
确认后再写死**（UP-L1 已标 `[待验]`）。

#### T2（评论）判据 —— 需要**你自己的作品**

| # | 判据 | 通过标准 |
|---|---|---|
| ① | 回包 `status_code == 0` | — |
| ② | **用 `comment/list` 回读到该评论**（`DouyinAPI.get_work_out_comment`） | 这是 `verify_text_send_delivery.py` 的既定口径：**只信服务端回包 + 二次回读**，不信日志、不信 `{"ok": true}` |
| ③ | 只有 ① 没有 ② | 判为**未投递** |
| ④ | 空 body | 按响应头分类；若命中 `X-Tt-Verify-Passport-Decision` ⇒ **如实报告「本项目无 dtrait 能力」**，**不得**宣称已修好 |
| ⑤ | 频控 | 同账号同作品两次评论间隔 ≥ 发送闸门最小间隔；不得连发探测 |
| ⑥ | 回复场景 | 若要验 `reply_to_reply_id`：先发一条一级评论取 `cid`，再以 `reply_id=<cid>` 发二级，断言可回读 |

**直接可用的调用形态**（**本轮未执行**，仅示意签名）：

```python
DouyinAPI.sendMsgInRoom(auth, room_id="<你的房间id>", content="[UP-L1-验证-<ts>]",
                        web_rid="<直播间web_rid>", enter_from="link_share")

DouyinAPI.publish_comment(auth, aweme_id="<你的作品id>", content="[UP-L2-验证-<ts>]",
                          text_extra=[], reply_id="", reply_to_reply_id="")
```

### 5.3 顺带上报的**同链路既有缺陷**（范围外，本次**未改**，但必须知道）

1. **`splice_url` 对 `/` 未按上游编码**：本项目 `utils/dy_util.py:182-188` 用
   `urllib.parse.quote(str(value))`（保留 `/`）；上游基线 `63ef397` 起已是 `quote(v, safe='')`。
   ⇒ 正文含 `/`（如带链接）时**签名输入 ≠ 上线字节**。**本次未改**（跨链路改动，
   `utils/dy_util.py` 不在本任务独占文件内）。
2. **评论发布缺 4 项上游基线前置**（UP-L1 A2-10）：`parse_aweme_id`（收链接）、`uifid`（头+query）、
   `_comment_uid` → query `uid`、`round_trip_time=50`。本项目**全无**。若将来真启用评论发布，
   这 4 项是**前置条件**（本轮未做，属独立工作线）。
3. **`check_risk_response` 未移植**：写接口被风控拦成「200 + 空 body」时，`safe_json` 会
   降级为 `{}`，上层可能读到**假成功**。本轮未做（理由见 §一·③）。

---

## 六、改动文件清单 + 新增测试文件

### 修改（2 个，均为本任务独占文件）

| 文件 | 改动 |
|---|---|
| `DYAutoDM_v2/backend/dy_apis/client_live.py` | `sendMsgInRoom` 对齐上游 `251075e`：Origin → `live_url`、`+**kwargs`、referer/`web_rid`/`enter_from`/`type`、7 个可选 query、`str()` 化；补注释说明 `with_bd(origin=…)` 为空操作 |
| `DYAutoDM_v2/backend/dy_apis/client_comments.py` | `publish_comment` 对齐上游 `df52357`：celltime 去随机默认 0、`text_extra` → JSON.stringify 语义、`+reply_to_reply_id`、`+one_level_comment_rank`/`paste_edit_method` kwargs 版；补注释说明**不**移植 dtrait/ticket 硬门禁及理由 |

**sha256（改动后，实测）**

```
client_live.py      f8b1459015d535a048f410fa1beeb616cdabb2532ad48c45f51ad072db0e4b82
client_comments.py  af72aebff78e912c09bf1c2e7936e6e5d97320deecb6386efa8a07629f279365
```

### 新增（1 个）

| 文件 | 说明 |
|---|---|
| `DYAutoDM_v2/backend/test_upstream_write_align_t1_t2.py` | 40 用例：T1/T2 请求构造层契约 + 签名一致性 + **负控** + 源码机械门禁 + 上游逐字对账。sha256 `38a14291785aa226c2896b910f79e1e06b8bdf1782a196f660720208c0ccab8a` |

### 明确**未**触碰

- **未改版本源**（`_build_version.py` / `package.json` / `frontend/package.json` /
  `src-tauri/Cargo.toml` / `Cargo.lock` / `tauri.conf.json`）。升版 `0.44.54` 属任务包 §一·3 的
  **独立步骤**（六处同步 + `scripts/check_version_sync.py` 门禁），**不在本任务授权内**。
- 未改 `工作记忆/` 任何文件；未改 `artifacts/UP_*`。
- 未 `git add` / `commit` / `checkout` / `stash` / `clean`。负控的临时回退是**文件级写 + 原样还原**，
  已校验 sha256 一致（§四）。

---

## 七、环境读数（可复核）

```bash
git -C C:/Users/LOX/Desktop/DYchajian rev-parse --short HEAD          # b455192
git -C C:/Users/LOX/Desktop/DYchajian branch --show-current           # design/better-douyin
"C:/Users/LOX/AppData/Local/Programs/Python/Python314/python.exe" -V  # Python 3.14.6
cd C:/Users/LOX/Desktop/DYchajian/DYAutoDM_v2/backend
DY_APP_ROOT="$LOCALAPPDATA/Temp/fixE" \
  "C:/Users/LOX/AppData/Local/Programs/Python/Python314/python.exe" \
  -m unittest test_upstream_write_align_t1_t2 -v                      # Ran 40 tests … OK
```
