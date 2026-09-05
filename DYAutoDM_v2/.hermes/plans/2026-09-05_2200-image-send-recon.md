# 图片/视频私信发送 — 重新侦察方案（plan 模式 · 不执行）

> **For Hermes:** plan-only，先侦察后汇报，等用户确认再实施。
> 上一轮误报"发图触发风控"——根因是 send 走 HTTPS POST + protobuf body，hook 截不到完整请求。
> 本轮基于基座项目 `DouYin_Spider-master.zip` 已找到完整结构。

## 1. 背景

### 1.1 上一轮失败原因（事实核查）

| 结论 | 实际情况 |
|---|---|
| "图片发出去了" | ❌ 数据库 `dm_messages` role=me 339 条**全文本**，0 条图片。**根本没发出去**。 |
| "发图触发风控" | ❌ 错误归因。点发送后 `passport/web/challenge/` 更可能是 sendMessage 失败或 exec_js 高频操作触发反爬。 |
| "喵喵爱之家是真实用户" | ❌ 该会话不在数据库也不在浏览器会话列表（12 vs 278）。 |

### 1.2 根因（侦察发现）

抖音 IM 消息**发送**和**接收**走两条不同的通道：

- **接收（push）**：WebSocket（基座 `dy_apis/douyin_recv_msg.py:36-79` on_message）
- **发送（send）**：**HTTPS POST `https://imapi.douyin.com/v1/message/send`**
  （基座 `dy_apis/douyin_api.py:1760-1793` `send_msg`）

我上一轮 hook `window.fetch` 抓到了 ①-⑤ **上传链路**（upload/config/apply/TOS/commit/build），
但**从未在日志里看到 `/v1/message/send` 出现**——这意味着发送按钮**根本没被触发**，
或触发后 fetch 被 sendMessage 前置逻辑阻断，没有真正发出。

而 `batch_build_image` 之后我**没有再调 ⑥**（脚本里就停在"点发送"那一步），
也没有验证对话框里**是否真的填好了 resource_url**。这是双失。

### 1.3 关键发现：基座项目已有 IM 发送实现（仅文本）

基座 `DouYin_Spider-master.zip` 里有完整的 IM 协议：

- `static/Request.proto` 88 行 — `SendMessageRequestBody` 完整字段
- `static/Response.proto` 45 行 — 响应解析
- `static/Request_pb2.py` / `Response_pb2.py` — Python 编译产物
- `dy_apis/douyin_api.py:1760-1793` — `send_msg()` 真实发送函数
  - URL: `https://imapi.douyin.com/v1/message/send`
  - Body: `requestProto.SerializeToString()`（protobuf 二进制）
  - Params: `verifyFp/fp/msToken/a_bogus` 四件套
  - Query: 需要 `a_bogus` 签名
  - 已有文本实现：`aweType=700` + `message_type=7`
- `builder/proto.py:79-111` — `build_send_message_request` 构建函数

**基座没有图片发送实现**（proto 里没定义 `aweType=2702` 的 content 结构）。
图片 content 字段是**自由 JSON 字符串**（Request.proto line 46 `string content`），
需要从真实抓包反推。

## 2. 目标

1. **真发一张图**到指定会话，**DB 可见 role=me + type=image + extra.skey**
2. **抓全 ⑥ message/send 的完整请求**（含 protobuf body 反解、aweType=2702、resource_url JSON 结构）
3. **验证：a_bogus 签名、protobuf 序列化、消息接收 ACK 全部一致**

## 3. 当前证据（不需要重抓）

| 步骤 | 来源 | 状态 |
|---|---|---|
| ① upload/config/v2 | 上一轮 BCC hook，已存 `docs/upload_flow.md` | ✅ 参数确定 |
| ② ApplyUploadInner (vod.bytedanceapi.com) | 同上 | ✅ 完整字段 |
| ③ TOS 直传 (UploadHosts[0]) | 同上 | ✅ 二进制 200 |
| ④ CommitUploadInner | 同上 | ✅ **Encryption.SecretKey=skey 来源** |
| ⑤ batch_build_image | 同上 | ✅ p3-sign.douyinpic.com URL |
| ⑥ message/send | **从未抓到** | ❌ 待本轮抓取 |

## 4. 重新抓包方案（核心改进）

### 4.1 改用 protobuf 反解

之前 hook `String(o.body).slice(0,3000)` 对 ArrayBuffer 无效（显示 `<binary>`）。
**新方案**：
```javascript
// 把 ArrayBuffer 转成 base64 推到 log（不在浏览器里反解，太慢）
const ab2b64 = (buf) => {
  const u8 = new Uint8Array(buf);
  let s = '';
  for (let i = 0; i < u8.length; i++) s += String.fromCharCode(u8[i]);
  return btoa(s);
};
// fetch 拦截
window.fetch = new Proxy(window.fetch, {
  apply(target, thisArg, args) {
    const [url, opts] = args;
    const body = opts && opts.body;
    let bodyB64 = '';
    if (body instanceof ArrayBuffer) bodyB64 = ab2b64(body);
    else if (body instanceof Uint8Array) bodyB64 = ab2b64(body);
    // 推到 window.__BODIES__
    (window.__BODIES__ ||= []).push({ url: String(url).slice(0,200), bodyB64: bodyB64.slice(0, 8000) });
    return Reflect.apply(target, thisArg, args);
  }
});
```
Python 端取回后用 `Request_pb2.Request.ParseFromString(b64decode(...))` 反解。

### 4.2 同时 hook WebSocket.send

发送理论上不走 WS，但保险起见也 hook 一下，免得漏：
```javascript
const OS = WebSocket.prototype.send;
WebSocket.prototype.send = function(data) {
  let b64 = '';
  if (data instanceof ArrayBuffer) {
    const u8 = new Uint8Array(data); let s = '';
    for (let i=0;i<u8.length;i++) s += String.fromCharCode(u8[i]);
    b64 = btoa(s);
  }
  (window.__WSMSGS__ ||= []).push({ dir:'send', b64: b64.slice(0, 4000) });
  return OS.call(this, data);
};
```

### 4.3 完整流程（待用户确认登录后执行）

```
Step 1: 确认登录态（拿 user_id）
Step 2: 滚动定位「尚进工伤小助理」会话（数据库里的 conv_id=0:1:316276709526638:3887506227210423）
        注意：当前浏览器登录的是"另一个账号"，需先让用户重新扫码登「四川工伤张老师」
Step 3: 点开会话 → 装 fetch+WS hook
Step 4: 在 input file 上传 1×1 红色 PNG
Step 5: 弹窗出来后**不立即点发送**——先抓配置 ①-⑤（已抓过，但本次确认）
Step 6: 等弹窗"发送"按钮变可用 → 点击
Step 7: 读 window.__BODIES__ 找 ⑥ message/send 的 ArrayBuffer → base64 → Python 反解
Step 8: DB 查询 dm_messages WHERE extra LIKE '%skey%' AND role='me' LIMIT 1
        → 找到 1 条则成功，否则失败
```

### 4.4 失败判定（铁硬标准）

| 验证项 | 通过条件 |
|---|---|
| ①-⑤ 抓到 | BCC hook log 包含 config/apply/TOS/commit/build 5 个 URL |
| ⑥ 抓到 | `__BODIES__` 里能找到 `imapi.douyin.com/v1/message/send` 条目 |
| DB 写入 | `dm_messages` 出现 `role=me AND extra LIKE '%skey%' AND conv_id LIKE '%316276709526638%'` 至少 1 条 |
| 接收方收到 | BCC 自己 recv 端 push 到该会话（轮询 `get_conversation_info_list_v2`） |
| 业务流跑通 | 对方回复"1"或新消息入库（不强制） |

**任何一项不过 → 失败，重新侦察，**绝不再"乐观汇报"**。**

## 5. 风控缓解

| 风险 | 缓解 |
|---|---|
| 账号再被踢 | **1×1 红色 PNG（70B）只发 1 张**，且发完立即停止；不批量 |
| exec_js 高频触发反爬 | 整个流程控制在 5 分钟内，JS hook 装一次不重装 |
| Session 过期 | 发图前 30s 内重新 verify 登录态 |

## 6. 风险评估

- **可行性**：基于基座已有 100% 完整 proto + 已有 send_msg 函数（仅 aweType=700），
  补图片发版（aweType=2702 + resource_url JSON）**技术可行**。
- **风控**：单次 1 张图**大概率**不触发（之前 3 轮"自动发图"实机 0 次成功发图——
  触发的不是图片，是高频 exec_js / 自动点击）。
- **时间成本**：从登录到拿到 DB 证据，预计 15-30 分钟（含 1 次扫码登录）。

## 7. 待用户确认

1. **是否切换到「四川工伤张老师」账号**（数据库里 278 会话、含「尚进工伤小助理」）？
2. **是否允许本次只发 1 张 1×1 PNG**作为唯一一次发送验证（不发第二次）？
3. **侦察期间允许 exec_js 30 次以内**（装 hook、读状态、点按钮、抓 body，不循环轮询）？

## 8. 不做的事（YAGNI）

- ❌ 不重写 send_msg 函数（基座版本能跑就用基座版本）
- ❌ 不实现视频发送（仅 image 路径）
- ❌ 不做批量发送验证（单次 1 张就够）
- ❌ 不动 base64/a_bogus 签名逻辑（基座已工作）
- ❌ 不重新逆向 ①-⑤（参数已确定）

## 9. 输出物（成功后）

1. `docs/upload_flow.md` 补 ⑥ message/send 完整字段（含反解截图）
2. 知识库 08 §24 补"aweType=2702 content JSON 结构"
3. `backend/api/messages.py` 加 `POST /send_image` 端点（直接复刻基座 send_msg + aweType=2702）
4. 不重打包（用户要求测试阶段不打后端）

## 10. 待定决策点

- **Go/No-Go** —— 等待用户确认（账号切换 + 单次允许 + exec_js 限额）
- 如果不切换账号，则只能向当前 12 个会话之一发图验证（不再是「尚进工伤小助理」）

---

**计划状态**: 草案 v1
**保存路径**: `DYAutoDM_v2/.hermes/plans/2026-09-05_2200-image-send-recon.md`
**实施前提**: 用户确认 go + 完成账号切换 + 接受单次发送限额
