# DYAutoDM 图片/视频发送 —— 上传链路逆向成果（2026-09-05 实测）

> 来源：登录态 chat 页真实发图，浏览器内 hook 抓包。
> 完整请求/响应原文见知识库 08 §24。本文件抽出**复现所需的参数结构**。

## 链路总览

```
① GET  https://www.douyin.com/aweme/v1/web/im/upload/config/v2
       ?device_platform=webapp&aid=6383&channel=channel_pc_web
       &update_version_code=170400&pc_client_type=1
   → STS 临时凭证（有效期 ~2h）

② GET  https://vod.bytedanceapi.com?Action=ApplyUploadInner&Version=2020-11-19
       &SpaceName=zhenzhen&FileType=image&IsInner=1&NeedFallback=t
   → StoreUri / Auth / UploadHosts / SessionKey

③ POST https://{UploadHost}/upload/v1/{StoreUri}
   → 二进制直传，返回 {"code":2000,"data":{"crc32":"..."}}

④ POST https://vod.bytedanceapi.com?Action=CommitUploadInner&Version=2020-11-19
       &SpaceName=zhenzhen      body: {"SessionKey": "..."}
   → ⭐ Uri + SecretKey + Algorithm + 图片元数据

⑤ POST https://www.douyin.com/aweme/v1/web/privacy/batch_build_image/
       ?device_platform=webapp&aid=6383&channel=channel_pc_web&pc_client_type=1
   → ⭐ p*-sign.douyinpic.com 可访问 URL（带 x-expires / x-signature）

⑥ POST https://imapi.douyin.com/v1/message/send
   → 带 aweType=2702 + resource_url 发送
```

## 各步关键参数

### ① upload/config/v2
- **必带参数**：`device_platform=webapp&aid=6383&channel=channel_pc_web`
  （不带返回 `{"status_code":5,"status_msg":"参数不合法"}`）
- 返回两套 config：`public_image_config_v2` / `inner_image_config`
- 关键字段：
  - `access_key_id`（AKTP 开头）
  - `secret_access_key`
  - `session_token`（STS2 开头，base64，内含 Policy + Signature）
  - `space_name`（实测 `zhenzhen` / `maya_review`）
  - `expire_at`（Unix 秒）
- Policy 声明的 Action 白名单：
  `vod:ApplyUpload` / `vod:CommitUpload` / `ImageX:ApplyImageUpload` /
  `ImageX:CommitImageUpload` / `vod:GetUploadCandidates` 等
- PSM：`toutiao.aweme_im.aweme_im_api_security`，UserId 绑定当前账号

### ② ApplyUploadInner
- Query：`Action=ApplyUploadInner&Version=2020-11-19&SpaceName={space_name}&FileType=image&IsInner=1&NeedFallback=t`
- 用 ① 的 STS 凭证签名（火山引擎 SDK 签名方式）
- 返回 `Result.UploadAddress`：
  - `StoreInfos[0].StoreUri`  = `tos-cn-o-00061/os8VafrEHHMOv4xQeKWsvIAyBKA6QAETC1AGfA`
  - `StoreInfos[0].Auth`      = `SpaceKey/{space}/1/:version:v2:{JWT}`
  - `StoreInfos[0].UploadID`
  - `StorageHeader.USER_ID`   = 当前账号 UID
  - `UploadHosts[0]`          = `tos-ali-hbzjk-ct-hl.douyin.com`（**与主域不同，注意**）
  - `SessionKey`              = base64（含 StoreUri + Auth，④ 要原样回传）

### ③ TOS 直传
- URL：`https://{UploadHosts[0]}/upload/v1/{StoreUri}`
- Method：POST
- Body：文件二进制（ArrayBuffer）
- 成功：`{"code":2000,"apiversion":"v1","message":"Success","data":{"crc32":"0795a426"}}`

### ④ CommitUploadInner ⭐ 最关键
- Query：`Action=CommitUploadInner&Version=2020-11-19&SpaceName={space_name}`
- Body：`{"SessionKey": "<② 返回的 SessionKey>"}`
- 返回 `Result.Results[0]`：
  - `Uri`          = `tos-cn-o-00061/7d46d9b09e16482a8ffda8c774bbf96f`（**加密后的 Uri**）
  - `UriStatus`    = 2000
  - `Encryption.SecretKey` = 64 hex（= **resource_url.skey**，AES-256-GCM 密钥）
  - `Encryption.Algorithm` = `aes-256-gcm`
  - `Encryption.Version`   = `v1`
  - `Encryption.SourceMd5`
  - `Encryption.Extra`：
    `img_format` / `img_width` / `img_height` / `img_size` / `img_frameCnt` /
    `mime_type` / `content_type` / `encryption_md5` / `encryption_size`

> ⚠️ 注意：③ 上传的 StoreUri 与 ④ 返回的 Uri **不同** ——
> ③ 用 `os8VafrEHHMO...`，④ 返回 `7d46d9b09e16...`（服务端重新加密后的对象）。
> **发消息要用 ④ 的 Uri，不是 ③ 的。**

### ⑤ batch_build_image
- Body：
  ```json
  {"convert_params":[{"uri":"<④的Uri>","format":"tplv-x-get:large.image","tpl":"%s://%v/%v~%v"}]}
  ```
- 返回 `data.pack_results[0].UrlList`：
  `https://p3-sign.douyinpic.com/tos-cn-o-00061/{Uri}~tplv-x-get:large.image?lk3s=...&x-expires=...&x-signature=...&from=...`
  （另有 p11 / p9 备用域名，以及 `.jpeg` 变体）

### ⑥ message/send
- 与文本同一个 `imapi.douyin.com/v1/message/send`
- 差异：`aweType` 改 **2702**（图片），content 内带 `resource_url`
  （含 `uri` / `skey` / `url_list` / 宽高 / size）
- 视频走 2703/2704，需额外 `duration`

## 与既有知识库的闭环验证

知识库 08 §四十 记载「图片 AES-256-GCM 加密，skey 在 `resource_url.skey`，
key=bytes.fromhex(skey)，iv=密文前 12 字节」——
**本次 ④ 的 `Encryption.SecretKey` 正是该 skey 的来源**，
发送侧与接收侧（origin_image_resolver 解密）至此完全闭环。

## 风控警告（实测）

点「发送」后页面立即出现：
```
POST /passport/web/challenge/
GET  /passport/token/beat/web/
POST login.douyin.com/passport/web/check_qrconnect/
GET  login.douyin.com/passport/web/get_qrcode/
```
→ 会话被踢回登录页。**批量自动发图明确触发风控**，
实现必须限速 + 单条 + 失败即停，禁止无人值守群发。

## 待补（下一步抓）

- ⑥ send 的**完整 content JSON 结构**（resource_url 各字段精确命名）
  本轮 ⑤ 之后未捕获到 send 的响应体，需再抓一次。
- 视频（FileType=video）的 ApplyUploadInner 参数差异
