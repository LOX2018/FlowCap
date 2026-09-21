> 性质: **现役知识（从 05 台账按主题拆出，v0.44.26 P0 收口）**
> 实测日期: 2026-09-05（实测）
> 判据: 实机探针/真机日志取证；详见各节内标注（本节为**分册**，索引见 `05_端点与协议台账.md`）
> 有效期/失效条件: 涉及**具体接口/字段/版本/内核/选择器**的内容随环境变化，**用前须按
>   `00_爬虫项目说明.md` §3.3 重验**；涉及**爬虫原理/风控红线**的部分长期有效。
> 状态: 有效（文内就地标注的「已作废」条目以 `01_铁律与红线.md` 为准）
>
> **本分册主题**：前端显示修复（表情包/base64/脏数据/时间）、UI 修复（对齐/留白/输入区）、WP 通道接入与图片上传逆向
> ⚠️ 本文是**按时间顺序的修复实录**（含现场描述）。按库纪律，**可复用结论**才算现役知识，
>   **同日期的现场过程**仅作溯源；与 `00_爬虫项目说明.md` / `01_铁律与红线.md` 冲突时以那两份为准。

---

## 22. 2026-09-05 前端显示修复实录（表情包/base64/脏数据/时间）

### 22.1 表情包显示异常（绿色边框 + 溢出）三连修

**现象**：表情包消息有绿色边框包裹、图片溢出容器边框、部分显示为黄色 3D 笑脸占位图。

**根因（三个独立问题叠加）**：
1. **绿边**：`.msg.out .bubble` 是绿色背景，`.mediathumb` 原为透明 → 绿底从图片四周漏出。
2. **溢出**：CSS 存在**两个** `.sticker` 相关规则冲突——`.mediathumb.sticker .thumbimg`（自动尺寸）
   与独立 `.sticker` 类（`width:76px;height:76px` 固定 + `display:grid`）。固定容器限制了图片，
   而图片本身超出 76px → 溢出边框。
3. **占位图**：贴纸 URL 在 Tauri webview 中加载失败（跨域/签名过期）→ 前端 onError 未兜底 → 显示默认占位。

**修复**（`frontend/src/styles/global.css`）：
```css
/* 删掉独立 .sticker 固定尺寸类,只保留 mediathumb.sticker 自动尺寸 */
.mediathumb { padding: 4px; background: var(--surface-2); border-radius: 6px; }
.msg.out .bubble.mediathumb { background: var(--surface-2); border-color: transparent; }
.mediathumb.sticker .thumbimg { max-width: 100px; max-height: 130px; }
```
即：**图片/表情消息统一走深色实底容器，方向用时间戳位置区分，不再套绿色气泡**。

### 22.2 base64 图片渲染失败（灰圈占位）根因：URL 编码污染

**现象**：私信图片消息显示为**灰色圆圈占位**，图片完全无法渲染。

**根因**：数据库 text 列里 `[图片] data:image/webp;base64,...` 的 base64 段**混入 URL 编码**
（实测尾部粘着 `%2F`→`/`、`%2B`→`+`、`%3D`→`=` 等编码字符）。前端 `sanitizeDataUri()`
原来直接 `replace(/[^A-Za-z0-9+/=_-]/g, "")` 剔除非法字符 → **`%` 被删掉，残留 `2F`/`2B`**
不是合法 base64 → 图片解码失败 → 灰圈。

**修复**（`frontend/src/pages/messages.tsx` `sanitizeDataUri`）：
```ts
let rest = u.slice(comma + 1);
try { rest = decodeURIComponent(rest); } catch { /* 保持原样 */ }
rest = rest.replace(/[^A-Za-z0-9+/=_-]/g, "");
return head + rest;
```
**铁律**：base64 清洗前必须先 `decodeURIComponent`，否则删 `%` 留下十六进制残渣。

### 22.3 脏数据过滤（三级防线）

| 脏数据类型 | 特征 | 过滤位置 |
|---|---|---|
| `[未知媒体] {...JSON...}` | aweType=110186 空媒体 / 图片文件元数据 | 后端 SQL `text NOT LIKE '[未知媒体]%'` + 前端 filter |
| `[分享视频] 视频ID xxx` | WS 错误解析噪音 | 后端 SQL + 前端 filter |
| `[系统提示]` | 打招呼卡片/推荐表情 | 前端 filter |
| `@url:https://www.iesdouyin.com/share/...` | 群聊分享链接 | 后端 SQL `text NOT LIKE 'https://www.iesdouyin.com/share/%'` |
| `text='1'` | 测试期残留（msg_id=NULL） | 库内清除（33 条） |

前端 filter（`messages.tsx`）：
```ts
.filter((m) => {
  const t = (m.text || "").trim();
  return !/^\[(未知媒体|分享视频|系统提示)\]/.test(t);
})
```

### 22.4 时间显示：日期分割线

**需求**：消息旁保留时分（`13:36`），跨天插入日期分割线（`2026-08-28`）。

**后端**（`backend/api/messages.py`）：**注意有两个时间函数**——
- `_fmt_ts()`：`%Y-%m-%d %H:%M:%S`（完整时间）
- `_fmt_hm()`：`%H:%M`（仅时分）

`get_conversation`（L346）原来用的是 `_fmt_hm` → 只返回时分，分割线无源可取。
**已改为 `_fmt_ts`**，前端拿完整时间自己切分。

**前端**（`messages.tsx` 渲染循环）：不再用 `.map()`，改 `forEach` 累积 nodes：
```tsx
let lastDate = "";
convMsgs.forEach((m) => {
  const fullDate = (m.mt || "").slice(0, 10);   // YYYY-MM-DD
  if (fullDate && fullDate !== lastDate) {
    lastDate = fullDate;
    nodes.push(<div key={`date-${fullDate}`} className="date-sep"><span>{fullDate}</span></div>);
  }
  nodes.push(<div className={"msg " + ...}>
    <MsgBubble ... />
    <span className="mtm">{(m.mt || "").slice(11, 16)}</span>  // 只取 HH:MM
  </div>);
});
```
CSS：
```css
.date-sep { display: flex; align-items: center; margin: 10px 0; }
.date-sep::before, .date-sep::after { content: ""; flex: 1; height: 1px; background: var(--border); }
.date-sep span { padding: 2px 10px; font-size: 11px; color: var(--muted); background: var(--surface); border: 1px solid var(--border); border-radius: 10px; }
```

### 22.5 部署排障教训：后端改动必须重打包主 exe + 同步 sidecar

**踩坑**：改 `backend/api/messages.py` 后只跑 `build_sidecar.py` 是不够的——
主程序 `DYAutoDM_v2_0.33.2.exe`（Tauri）会把 sidecar **内嵌**进主 exe，
启动时释放到临时目录运行。**必须**：
1. `build_sidecar.py build_one main.py dyautodm-backend`（产物到 `src-tauri/binaries/`）
2. `npx tauri build --no-bundle`（把新 sidecar 嵌进主 exe）
3. 覆盖部署 `DYAutoDM_v2_<版本>.exe` **并同步** `dyautodm-backend-*.exe`

**排障验证**：手动 curl backend 时若返回旧格式，先查测试目录的 sidecar exe 是否同步
（`ls -la` 对比时间戳），再查是否还有残留旧进程占着 8000 端口。

### 22.6 前端 React Query 缓存陷阱

`@tanstack/react-query` 默认内存缓存 + `staleTime: 3000`——后端过滤修好后，
**已打开的会话不会自动更新**。用户反馈"未看见变化"时先怀疑缓存：
- 切换会话再切回（触发 refetch）
- 或重启应用（清内存缓存）

---

## 23. 2026-09-05 前端 UI 修复全记录(对齐/留白/输入区)

### 23.1 表情包绿边/溢出三连修

**现象**：表情包消息有绿色边框包裹、图片溢出容器边框、部分显示黄色 3D 笑脸占位图。

**根因(三个独立问题)**：
1. **绿边**：`.msg.out .bubble` 绿色背景 + `.mediathumb` 透明 → 绿底漏出。
2. **溢出**：CSS 有两个 `.sticker` 相关规则冲突——`.mediathumb.sticker .thumbimg`(自动尺寸)
   与独立 `.sticker`(固定 `76x76` + `display:grid`)。
3. **占位图**：贴纸 URL 在 Tauri webview 加载失败 → onError 未兜底。

**修复**（`frontend/src/styles/global.css`）：
```css
.mediathumb { padding: 4px; background: var(--surface-2); border-radius: 6px; }
.msg.out .bubble.mediathumb { background: var(--surface-2); border-color: transparent; }
.mediathumb.sticker .thumbimg { max-width: 100px; max-height: 130px; }
/* 删掉独立 .sticker 固定尺寸类 */
```

### 23.2 base64 图片灰圈渲染失败

**现象**：私信图片消息显示灰色圆圈占位。

**根因**：数据库 text 列 base64 混入 URL 编码(%2F→/,%2B→+),前端 `sanitizeDataUri()`
直接删 `%` → 残留 `2F` 非法 base64。

**修复**：`sanitizeDataUri` 先 `decodeURIComponent` 再清洗非法字符。

### 23.3 脏数据过滤三层防线

| 位置 | 过滤内容 |
|---|---|
| 后端 SQL | `[未知媒体]%`、`[分享视频]%`、`https://www.iesdouyin.com/share/%` |
| 前端 filter | `[未知媒体]`、`[分享视频]`、`[系统提示]` |
| 库内清除 | `text='1'` 测试消息(33 条已删) |

### 23.4 时间格式 + 日期分割线

**后端**：`get_conversation` 改用 `_fmt_ts`（`YYYY-MM-DD HH:MM:SS`）替代 `_fmt_hm`（`HH:MM`）。

**前端**：渲染循环改 `forEach` 累积 nodes,每条日期变更插入分割线：
```tsx
let lastDate = "";
convMsgs.forEach((m) => {
  const fullDate = (m.mt || "").slice(0, 10);
  if (fullDate && fullDate !== lastDate) {
    lastDate = fullDate;
    nodes.push(<div className="date-sep"><span>{fullDate}</span></div>);
  }
  nodes.push(<div>...<span className="mtm">{(m.mt||"").slice(11,16)}</span></div>);
});
```

**CSS**：`.date-sep` 水平分割线 + 圆角标签。

### 23.5 输入框 2 行 + 发送按钮居中

```tsx
<textarea rows={2} ... />
<button className="btn primary">发送</button>
```
```css
.composer { align-items: center; }  /* 按钮垂直居中 */
```

### 23.6 删除账号信息卡片头像

```tsx
/* 删除前 */
<Avatar name={curAcct.name} ... />
<div>当前私信账号 · {curAcct.name}</div>
<div className="mono">UID {curAcct.uid} · 会话按账号隔离</div>

/* 删除后 */
<div>当前私信账号 · {curAcct.name}</div>
```

### 23.7 附件按钮 + 文件选择

输入框右侧加"＋"按钮 → 展开附件菜单(图片/视频/文件) → 弹出系统文件选择器。

```tsx
<button className="btn attach-btn" onClick={() => setShowAttach(v=>!v)}>＋</button>
{showAttach && (
  <div className="attach-menu">
    <div onClick={() => handlePickFile("image/*")}>🖼️ 图片</div>
    <div onClick={() => handlePickFile("video/*")}>🎬 视频</div>
    <div onClick={() => handlePickFile("*")}>📎 文件</div>
  </div>
)}
```

### 23.8 聊天区高度自适应视口(留白/对齐修复链)

**问题链**：
1. `.thread` 固定 600px + `grid.cols-3-7` 默认 `stretch` → 左侧卡片撑高 → 右侧卡片底部留白。
2. `.thread` 改 `calc(100vh - 136px)` → 聊天区自适应视口,输入框贴底。
3. 左侧 `.conv-list` 高度差 44px → 底部不对齐。

**修复**：
```css
.cols-3-7 { grid-template-columns: 3fr 7fr; align-items: start; }  /* 不拉伸 */
.thread { height: calc(100vh - 136px); min-height: 480px; }         /* 聊天区 */
.conv-list { max-height: calc(100vh - 186px); }                    /* 会话列表对齐 */
```

**铁律**：Tauri 桌面应用高度布局必须用 `calc(100vh - 偏移量)`,不能用固定 px,否则窗口缩放时必出留白。

### 23.9 部署排障教训

1. **PyInstaller 缓存**：改 backend 源码后 `build_sidecar.py` 可能读缓存 → 必须清 `backend/build/dyautodm-backend` 和 `backend/dist` 目录强制重建。
2. **Tauri 嵌 sidecar**：改 backend 必须同时 `build_sidecar.py` + `tauri build --no-bundle`,否则主 exe 里仍是旧 sidecar。
3. **React Query 缓存**：后端过滤修好后已打开的会话不会自动更新 → 需切换会话或重启应用。
4. **进程残留**：PyInstaller onefile 双进程(parent+child),`taskkill` 主进程后 child 仍持端口 → 需精确 taskkill 子进程。
5. **端口验证**：curl backend 前确认是新进程占端口(看时间戳),不是旧进程。

---

## 24. 2026-09-05 WP 通道接入 + 图片上传链路逆向（实测）

### 24.1 重大教训：所有浏览器侧侦察必须先确认登录态

**踩坑**：连续 4 轮侦察得出「抖音封得严 / WS 截不到 / IM SDK 不存在 / chat 页不发 HTTP」
的错误结论，**真实原因是 BCC 无头浏览器根本没登录**，打开的
是扫码登录页（页面文本：*登录后免费畅享高清视频 / 扫码登录 / 二维码失效*，
cookie 无 `sessionid`）。

**登录成功的判据（缺一不可，先看这个再侦察）**：
| 判据 | 未登录 | 已登录 |
|---|---|---|
| `document.cookie` 含 `sessionid` | ❌ | ✅ |
| `.conversationConversationItemwrapper` 数量 | 0 | 13（实测） |
| `input[type=file]` 数量 | 0 | **2** |
| 页面文本含「扫码登录」 | ✅ | ❌ |

**铁律**：凡涉及 chat 页的 hook/探测，**第一步必查 `convItemCount`**，
为 0 就先重新登录，否则所有结论都是垃圾。

### 24.2 图片/视频发送的真实上传链路（⭐ 核心成果）

登录后在 chat 页塞图 → 点发送，抓到的完整链路（5 步）：

```
① GET  https://www.douyin.com/aweme/v1/web/im/upload/config/v2
       获取上传配置/凭证（TOS 上传地址、鉴权参数）

② POST https://tos-d-x-hl.douyin.com/upload/v1/tos-cn-o-00061/oYqANX4JJCFIAAF1pAYQuJNAAnDdgUfEfZDDEa
       ⭐ 真正的字节 TOS 对象存储上传（图片/视频二进制在此上传）
       注意 tos-cn-o-00061 与加密图链 p*-sign.douyinpic.com 同源

③ POST https://vod.bytedanceapi.com
       视频转码/注册（视频专用；图片可跳过）

④ POST https://www.douyin.com/aweme/v1/web/privacy/batch_build_image/
       图片构建（图片专用）

⑤ POST https://imapi.douyin.com/v1/message/send
       最终发送，body 内带 resource_url（aweType=2702/2703/2704）
```

**file input 的 accept**：`.jpg,.jpeg,.png,.gif,.mp4`
（两个 input，class 分别为 `semi-upload-hidden-input` 与
`semi-upload-hidden-input-replace`）

**选择文件后的 DOM 流程**：
```
塞 File 到 input + dispatch change
  → 弹窗 MsgInputSendFileModalbox（「发送给 XXX」+ 文件列表 + 取消/发送）
  → 点 button.MsgInputSendFileModalbtnSure（发送）
  → 才真正触发 ① 上传链路
```
⚠️ **只塞文件不点发送 → 只有预览，不会有上传请求**（实测 90 个请求全是
get_read_index/get_min_index 轮询，0 个上传）。

### 24.3 发现的 imapi 新接口（知识库此前未记载 v3 系列）

登录态下 chat 页高频轮询（占全部请求 90%+）：
```
POST imapi.douyin.com/v3/conversation/get_read_index    (72 次)
POST imapi.douyin.com/v3/conversation/get_min_index     (3 次)
POST imapi.douyin.com/v1/message/get_user_message       (4 次)
POST imapi.douyin.com/v1/stranger/get_conversation_list (7 次)
POST imapi.douyin.com/v3/conversation/mark_read         (1 次)
POST /aweme/v1/web/im/user/active/status/               (2 次)
POST /aweme/v1/web/im/user/active/update/               (心跳)
```

### 24.4 WP 通道结论修正

此前判定「抖音封得严、WP 通道走不通」**是在未登录状态下的误判**，
**需在登录态下重测**。已明确的技术事实：
- chat 页私信**不走 www.douyin.com/aweme/v1/web/... 路径**（实测 404
  `Unsupported path(Janus)`），真实接口在 **`imapi.douyin.com`**。
- WP hook 正则已修正为按路径特征匹配（不绑死域名）。
- 页面内**无全局 IM SDK**（扫 window 全部属性含不可枚举，16 个候选名
  `webImService`/`DY_IM`/`imSdk` 等**全部不存在**，`send*` 只有
  浏览器原生 `postMessage`/`sendBeacon` 与 Slardar 埋点 `sendEvent`）
  → **WP 通道发送不能走 SDK，只能走 ①-⑤ 的 HTTP 链路**。

### 24.5 WP hook 的两个真实 bug（已修）

1. **JS 正则字面量跨行 → init script 静默失败**
   正则 `/.../` 里插换行是语法错误，整个 `add_init_script` 不执行，
   表现为 `typeof window.__CAP_WP_MESSAGE__ === 'undefined'`。
   **修法**：改用 `new RegExp('字符串')` 构造，可安全跨行拼接。
2. **hook URL 正则域名写错**
   原匹配 `www.douyin.com/aweme/v1/web/im/...`（实测 404 Janus），
   真实接口在 `imapi.douyin.com`。改为按**路径特征**匹配，不绑域名。

### 24.6 WS 通道发送：凭证失效症状

`recv_daemon /send` 用 .env 陈旧 cookie 会返回：
- `响应用户解析失败: Error parsing message with type 'Response': Wire format was corrupt`
- 或 `create_conversation 响应缺少 create_conversation_v2_body message='INVALID_REQUEST' | resp_json={'cmd': 609, ..., 'body': {}}`

**已修**：`recv_daemon /send` 补 `DYLoginApi.refresh_cookie_from_profile`
（`_pull_conversations_api` 早有此逻辑，`/send` 漏了）。**待验证**。

### 24.7 风控警告：自动发图会触发安全验证

实测点「发送」后页面立即出现：
```
POST /passport/web/challenge/
GET  /passport/token/beat/web/
POST login.douyin.com/passport/web/check_qrconnect/
GET  login.douyin.com/passport/web/get_qrcode/
```
→ **会话被踢回登录页**。批量自动发图有明确风控风险，

### 24.8 方案A：后端直发图片全链路（2026-09-05 实现完成，待实测）

开源项目 `Rockedw/douyin-web-api-sdk`（Java）交叉验证 + 真实抓包帧解码，
`backend/dy_apis/image_sender.py` 已实现完整 ①-⑥（**未实测**）：

**图片消息 ground truth**（真实 protobuf 帧反解，黑盒确认）：
```
message/send send_message_body:
  conversation_type = 1
  message_type      = 27        ← 图片 27！文本是 7
  content           = {"resource_url":{"oid","skey","data_size","md5"},
                       "cover_height","cover_width","check_pics":[],
                       "md5","from_gallery":1,"aweType":2702}
  ext               = s:client_message_id / s:stime / s:mentioned_users（同文本）
```
字段来源（④ CommitUploadInner 响应 `Result.Results[0]`）：
- `resource_url.oid`  = `Encryption.Uri`（**≠③上传的 StoreUri**，服务端重加密对象）
- `resource_url.skey` = `Encryption.SecretKey`（AES-256-GCM，与接收侧解密同源闭环）
- `resource_url.md5` = `Encryption.SourceMd5`（= 原文件 MD5）
- `data_size` = 原文件字节数
⚠️ `batch_build_image`（⑤）返回的 p3-sign URL **非发送必需**，仅供自家前端渲染。

**AWS4 SigV4 签名要点**（火山引擎 vod 域）：
- region=`cn-north-1`，service=`vod`，STS 凭证来自①（~2h 有效）
- GET（ApplyUploadInner）：signed headers=`x-amz-date;x-amz-security-token`，空 payload
- POST（CommitUploadInner）：多签 `x-amz-content-sha256`，body=`{"SessionKey":..,
  "Functions":[{"name":"Encryption","input":{"Config":{"copies":"cipher_v2"},
  "PolicyParams":{"policy-set":"check,thumb,medium,large"}}}]}`
- Commit 请求头 `content-type: text/plain;charset=UTF-8`（不是 application/json！）
- ③ TOS 直传头：`authorization`=②的 Auth、`content-crc32`=8位hex、`x-storage-u`=UID

**接入点**：`recv_daemon /send_image`（image_b64 入参）→ `send_image(auth, peer_id, data)`
→ `api/messages.py /send_image` 转发。发送成功落库 `role=me/msg_type=image/extra含skey`，
前端复用图片渲染逻辑。

**⚠️ 实测发现（重要）**：`im/upload/config/v2` 的鉴权**强绑定 BCC 页面会话**——
页面掉线后，即使 cookie 本身有效（`query/user` 能通、profile cookie 65 项新鲜），
探针直连该接口仍返回 `status_code=8 用户未登录`。
→ **发图前提 = BCC chat 页处于登录态**（先过 §24.1 铁律检查）。
→ 跨服务发图时 recv_daemon 的 `refresh_cookie_from_profile` 必须成功。

**实测结果（2026-09-06 00:30，BCC 登录态下）**：
- ① STS✓ —— **必须用 `public_image_config`（v1）凭证**；v2 的 session_token 报
  `Invalid session token, sequence is broken`（v1/v2 token 结构不同，v2 另有签名要求）
- ② Apply✓ —— 实际 URL query 必须与签名 canonical query **逐字节一致**（SigV4 铁律）
- ③ TOS 直传✓（crc32=0795a426；crc32 用 `zlib.crc32` 非 hashlib）
- ④ Commit✓ —— 拿到 `Encryption.Uri + SecretKey(aes-256-gcm)`，**发送侧要素全齐**
- ⑥ message/send → `{"decision":"KICK"}` —— **账号级私信风控**
- 对照：文本 `send_msg` 同样 KICK（09-04 11:16 起日志多次记录），
  `create_conversation` 连续调用也会 INVALID_REQUEST（频控）。
  DB 里 09-03 17:05 后再无 role=me 真实业务消息 → **imapi 直发路径 09-04 起被风控**，
  文本图片一视同仁，与图片实现无关（00 §四.7 早有记载：KICK=账号级风控需降频/换号）。
- **方案A 端到端实测通过（2026-09-06 00:42-00:54，账号：尚进工伤小助理）**：
  - 直调 `send_image()`：①-⑥ 全链路成功，对方（张老师）**抖音实收确认**；
  - `recv_daemon /send_image` 端点：发送 ok + DB 落库验证通过
    （`dm_messages #35026 role=me msg_type=image extra={skey,oid,…}`）；
  - 旧号（张老师）同代码 KICK → **证实 KICK 为账号级风控**，与实现无关；
  - 环境注意：BCC 占用 profile 时 `refresh_cookie_from_profile` 自动跳过
    （vbrowser exitCode=21），`.env` 凭证直接可用，不阻塞发送；
    sidecar 修改后必须重打包并覆盖 `<测试目录>/binaries/`（老铁律再证）。

### 24.9 WP 通道发图（备用通道，2026-09-06 01:14-01:18 实测通过）

WP 发图 = 在 BCC chat 页 DOM 上复刻用户手动发图动作（**与 WS 后端直发完全独立**，
不碰 imapi 直发签名，KICK 账号风控时仍可用）。

**DOM 流程（有头/无头一致，全部实测）**：
```
① BCC 拉起（vbrowser exe 内核, 账号 profile, 有头 headless=False / 无头 True）
   → goto https://www.douyin.com/chat?isPopup=1
② 铁律检查：.conversationConversationItemwrapper > 0 且无「扫码登录」
③ 搜索会话：找 placeholder 含「搜索」的 input → React 原生 setter 填值
   + dispatch input 事件 → 等 2.5s
④ 点开会话：.conversationConversationItemwrapper 中 innerText 含目标昵称的项
   → 依次 dispatch mousedown/mouseup/click（只 click 不触发 React）→ 等 3s
⑤ 塞图：input[type=file][0] ← DataTransfer 塞 File(1x1 PNG) → dispatch change → 等 6s
⑥ 弹窗校验（**发送前铁律**）：[class*=SendFileModal] 的 innerText 必须含
   目标昵称 + 文件名，否则不点发送（曾因弹窗指向别的会话险些发错人）
⑦ 点发送：innerText.trim()==='发送' 且 offsetParent!==null 的 button → click → 等 9s
⑧ 判定：弹窗消失 = 发送动作完成（**无 fetch hook 可抓**：页面内部走 XHR/WS 加密帧）
```

**实测结果**：
- 有头（01:14）+ 无头（01:17）**行为完全一致**，对方（小助理）**两次均实收确认**；
- 生产形态（无头容器）可直接用，无需可见窗口；
- 局限：无法从页面 hook 抓到发送请求细节（加密帧），只能靠对方实收判定成败；
  发送结果无 protobuf ACK，`{"ok":true}` 仅代表"点了发送且弹窗消失"。

**代码位置**：无固化到 daemon —— 实测脚本为独立 Playwright 流程
（`launch_async(vb_mode, cfg, headless, user_data_dir=profile)`），
WP 发图如需产品化应把上述 8 步封装进 `browser_daemon.py`
的 `wp_send_image()`（与 `wp_send_text` 并列，走 `/wp_send_image` 端点）。

**两通道对比（选型依据）**：

| | WS 通道（方案A后端直发） | WP 通道（BCC 页内 DOM） |
|---|---|---|
| 依赖 | .env 凭证 + imapi 签名 | BCC 容器 + profile 登录态 |
| 结果判定 | protobuf ACK（OK/KICK 明确） | 无 ACK，仅"弹窗消失"+对方实收 |
| KICK 风控 | **会被拦**（账号级） | **不受影响**（复用页面会话） |
| 落库 | 发送端直接落库（含 skey） | 靠 WS push 回声/对方收到后落库 |
| 适用 | 默认通道 | imapi 被风控时的兜底 |

**uid 轮换事故（2026-09-06 01:28，重要教训）**：
小助理账号登录态失效（疑与重新授权张老师凭证+应用重启后 chat 页重新握手有关）后，
`query/user` 返回**新的 user_uid（2609595357858448，≠真身 316276709526638）**，
Web 接口认新 uid、imapi 会话体系仍挂老 uid → 更新会话/建会话/发文本图全部
INVALID_REQUEST，而探活机制（keepalive/verify_account）见"能拿到 uid"即判正常，
**全程零告警**。暴露四处结构性缺陷（修复待办）：
1. `refresh_cookie_to_env` 无条件覆盖 .env cookie，不验证新 cookie 登录态/uid 一致性
   （`browser_daemon.py:858`）——好凭证会被坏凭证冲掉；
2. keepalive 探活只查"能拿到 uid"，不查 uid 是否与上次一致（`browser_daemon.py:880`）；
3. `verify_account` 拿到 uid 后不与 `dm_conversations.conv_id` 历史 uid 交叉验证
   （`accounts.py:250`）；
4. BCC `/cookie` 无账号一致性校验，`_bcc_alive` 只查端口 alive；手动
   `--port` 启动可绕过端口哈希制造双 BCC 并存（`login_api.py:31-41`）。
   → **1/2/3 已修复（2026-09-06 03:xx 提交，三 sidecar 已重打包）**：
   ① `refresh_cookie_to_env` 写前双门禁（新 cookie 探活 + uid 与 _last_uid
   一致），失败/漂移拒绝写入保住好凭证；② keepalive uid 漂移即 error +
   触发 scan_login；③ `verify_account` 探活 uid 与历史 conv_id uid 段
   交叉验证，漂移判 fail「uid 漂移（身份存疑）」。
   → **4 已修复（2026-09-06 同批提交）**：④a `_bcc_alive` 除 alive 外校验
   /status 的 account 与请求账号一致，不匹配按未运行处理并告警（拒绝跨账号
   取 cookie）；④b `browser_daemon.main` 启动时校验 `--port` 与
   `browser_daemon_port(account)` 哈希一致，不一致 SystemExit(2) 拒启
   （调试逃生口 `--allow-any-port`）。应用侧 daemon_launcher/main.py
   本就走哈希无需改。至此四处缺陷全部闭环。
   → **4c 补充修复（2026-09-06）**：WP 通道失效实锤 = BCC 页面「半登录态」
   （显示"一键登录"待激活），query/user 仍返回 uid → keepalive 误报正常。
   新增 `_page_login_state_sync()`（exec_js 查 convItems>0 且无一键登录/扫码），
   keepalive 探活通过后再做页面级检查，失效即触发 scan_login 自动重激活。
   **WS 发送延迟**：方案A 发图 6 次串行网络请求属正常（2-4s）；修复1 门禁
   探活已加 60s 类级缓存消除额外 0.5-1s/条。
   **日期分割线只剩一条**：首包 message 对象 field 10 = create_time 毫秒
   （黑盒实证，与 DB 历史吻合），此前 ts 全 fallback 到入库时刻。
   `_parse_message_create_time()` 已修复并验证恢复真实历史时间。

### 24.10 BCC 风控对齐：「无头」改真有头+窗口移屏外（2026-09-06 实测闭环）

**根因实证**：Chromium headless 模式被抖音风控识别。同一 profile 下，
有头（双击打开的指纹浏览器）登录态正常，BCC headless 容器却触发
step-up 重验证降级为半登录态（页面显示「一键登录」）——环境跳变是诱因。

**对照实验**（小助理 profile）：伪装模式与纯有头均半登录 → 确认 session
服务端失效与伪装无关；用户重新扫码后，伪装模式复测 **完全正常**
（conv=12，rel=False，14:31）。

**实现**（vbrowser.py，launch_async/launch_sync 双路径统一）：
```
headless=True 请求 → headless=False（恒有头）
  + --window-position=-32000,-32000   ← 窗口移出屏幕
  + --window-size=1440,900            ← 常规桌面尺寸
```
对抖音 100% 有头特征（与用户双击打开完全一致），对用户等效无头。
**BCC = 双击打开的指纹浏览器（无头状）**：同内核/同 profile/同链路，
仅窗口不可见。日志标记「无头请求已转为 真有头+窗口移屏外」。

⚠️ 注意：`--window-position` 在 Windows 下部分环境可能被显示器布局
覆盖；若窗口意外可见，可在 BCC 启动后用 CDP `Browser.setWindowBounds`
二次纠偏。

**副作用修复（2026-09-06 用户报告：扫码窗口跳到桌面外，无法重新扫码）**：
持久化 profile 会把窗口位置写进 Preferences——伪装模式跑过一次后，
下一次【可见启动】（扫码登录 / 查看模式）窗口从 -32000 屏外位置恢复，
二维码落在桌面外根本扫不了。修复（vbrowser.py）：
- 新增 `_ensure_window_visible()` / `_ensure_window_visible_sync()`：
  CDP `Browser.getWindowForTarget` 检测窗口坐标，|coord| > 30000（只命中
  ±32000 伪装量级，不误伤多显示器负坐标如 -1920）即 setWindowBounds
  归位到 (80,80)，失败仅告警不阻塞。
- 接入点：`launch_async`（headless=False 且非伪装）、`launch_sync`
  （非伪装）、`open_douyin_home`（恒调用）。
- **关键区分**：用 `_disguise` 标志位区分「伪装启动」与「真可见启动」
  ——伪装分支里 headless 被置 False，若只判 `if not headless` 会把
  伪装窗口也归位，伪装彻底失效（第一版补丁就犯过，已修）。
- 伪装模式行为不变：窗口仍留在屏外，风控对齐不受影响。

### 24.11 WP 通道固化进 BCC + 进程管理铁律（2026-09-06）

**WP 发送恒失败根因**：BCC `/wp_send` 是「探测式 IM SDK 空壳」——在
window 上找 webImService 等候选对象，全部落空即报错（实测 candidates=[]
恒失败）。上午实测成功的 DOM 流程从未固化进 daemon。
**修复**：`wp_send_text` 重写为 DOM 流程（DB 查 peer_name → 搜索会话 →
点开 → 编辑器 `execCommand('insertText')` → Enter），发送判定 = 编辑器
清空（**必须剔除零宽空格 \u200b 再判**，否则误报未清空）；`/wp_send`
路由补账号一致性校验。live 验证：DOM 流程走通、消息实际发出。
`wp_send_image` 同理待固化（8 步流程见 §24.9）。

**⚠️ 进程管理铁律（本次事故教训）**：**绝不 Stop-Process 强杀
BCC/浏览器进程**——session 会被服务端降级要求重认证，连「一键登录」
都失效（本机免扫协议 token 一并作废），只能重新扫码。部署覆盖二进制
前必须走 BCC `/quit` 端点（graceful close：context.close() 正常释放）。
若已降级：页面显示二维码失效+一键登录无效 → 唯一出路是重新扫码。

### 24.12 启动性能：daemon 并行拉起（2026-09-06）

**现象**：前端启动 ~40s。实测启动日志：recv_daemon 串行拉起 +
逐个 `_wait_for_port`（每账号 ~15s），2 账号 = 31s 纯阻塞。
**修复**：main.py `_auto_start_daemons` 改为 BCC + 全部 recv_daemon
**并行 spawn** 后统一轮询等端口——总耗时 ≈ 最慢一个（~15s），
N 账号不再线性叠加；等待超时不阻塞启动。
**「多次调用 BCC」排查**：browser-daemon/recv-daemon 各出现两个同名
进程是 PyInstaller onefile 的 launcher+worker 正常结构；
当前 1 BCC + 2 recv 实例数正确，无重复拉起。

### 24.13 keepalive 熔断：防「每 5 分钟重启浏览器」风控恶性循环（2026-09-06 P0）

**实测事故**（16:03-16:13 日志）：页面半登录 → keepalive 触发 scan_login
→ **整个浏览器重启** → 仍半登录（session 服务端已死，自动登录救不回）
→ 5 分钟后又来，连续 3 轮。每 5 分钟一次完整浏览器重启 = 极强风控信号。
**修复**：scan_login 连续失败 **2 次即熔断 30 分钟**——期间页面级检查
失败只记 warning 告警（提示"请在指纹浏览器重新扫码"），**绝不自动重启
浏览器**；页面恢复（conv>0）自动清零。三处触发点（uid 漂移/页面失效/
uid 失效）统一受熔断保护。

### 24.14 BCC 懒加载架构（2026-09-06 用户架构决策落地）

**决策**：启动**不再拉起 BCC**。历史理由（昵称自动捕获）08-29 已废，
BCC 消费者（WP 发送/昵称捕获/回声轮询）全部按需或可延后——启动时不该
为「可能不用」的功能预付 15s 成本与风控暴露。

**实现**：
- `main.py`：BCC 默认不随启动拉起（`DY_BCC_ON_START=1` 可恢复）；
  recv_daemon（WS 主通道，不依赖浏览器）照旧并行拉起
- `accounts.ensure_bcc(name)`：懒加载辅助（端口双检锁/防重复拉起/等就绪）
- `api/messages.py _bcc_url`：调 BCC 前自动 ensure_bcc（WP 发送即触发）；
  更新会话路径本就走 `ensure_daemons_for`（自带拉起），无需改
- `wp_recv`：BCC 未运行时空转等待（debug 静默），懒加载后自动恢复

**headless 模式配置**（复盘修正归因）：半登录态根因是 **session 生命周期**
（强杀进程/握手时序）而非 headless 模式本身——00:42-00:54 headless 一直
正常即为反例。`DY_BCC_HEADLESS_MODE=disguise(默认)|native`：
- `disguise` = 真有头+窗口移屏外（特征与双击打开一致，保险策略）
- `native` = 纯 Playwright headless（省资源；保留供回退）

### 24.15 启动冷静期 + 系统占位提示过滤（2026-09-06）

**BCC 启动冷静期**（`accounts.ensure_bcc`）：进程启动后 **30s 内禁止懒加载
BCC**（`DY_BCC_LAZY_DELAY` 可调秒数）。防御「启动时 BCC 快闪唤醒」——启动
期所有调用链（getAccounts/凭证校验/autoRecapture/任何 ensure_daemons_for
路径）若走到 ensure_bcc，冷静期内只读不拉进程，30s 后才接受懒加载。设计
依据：启动期不该让任何自动重捕打断启动节奏，重捕始终应该由用户显式触发。

**系统占位提示过滤**（`recv_daemon._extract` msg_type=7）：抖音在「未发过
消息的陌生会话」首次被打开时自动塞入占位提示「对方回复你或互关之前，可
发送一条文字消息。请礼貌发言，自觉遵守{{0}}」。sender 来自陌生人，role=them
被当真实消息入库污染聊天记录。识别：文本含"对方回复你或互关之前"/"可发送
一条文字消息"/"请礼貌发言"/"自觉遵守"四选一即中，直接返回 None 不入库。
同步 `messages.get_conversation` 查询加 3 条 NOT LIKE 过滤已入库历史脏数据。

### 24.16 全局并发治理：DB 写竞争 + 双通道重复落库（2026-09-06）

**问题 A — 多进程写竞争**：`database.get_db` 只有 WAL（解决读写不互斥），
**没有 `busy_timeout`**。而 backend + N×recv_daemon + BCC 会并发写同一个
db 文件 → 并发写直接抛 `database is locked`（默认 5s 且不重试）。
**修复**：`connect(timeout=30)` + `PRAGMA busy_timeout=30000`。

**问题 B — 双通道重复落库（唯一索引建了却没生效）**：`dm_messages` 早有
`uniq_dmmsg(account,conv_id,msg_id)` 与 `uniq_dmmsg_fallback(...)` 两个
唯一索引，但：
- `recv_daemon.add_message` / `wp_recv` / database JSON 迁移 用的是**裸
  INSERT**（绕过去重，冲突时抛 IntegrityError 而非静默去重）
- WP 通道只把 `client_msg_id` 塞进 extra JSON，**不写 msg_id 列**
  → `uniq_dmmsg` 完全管不到它；而 WS 回声与 WP 轮询的 ts 有毫秒级
  差异 → fallback 索引也失效 → **同一条消息必然重复入库**

**修复**：三处裸 INSERT 全改 `INSERT OR IGNORE` 且写入 msg_id 列
（WS 侧 `add_message` 新增 msg_id 参数，取 `msg.msg_id`；WP 侧取
`client_msg_id`）。现在 WS / WP 双通道同一条消息命中同一唯一索引真正去重。


---

