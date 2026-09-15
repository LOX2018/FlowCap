"""浏览器守护 —— 注入脚本（JS 常量）

## 为什么独立（2026-09-15 大单文件打散）

原 `daemon/browser_daemon.py`（2924 行）内联了约 195 行**注入用 JS 字符串**
（DOM 扫描/滚动、用户信息钩子、私信钩子）。JS 与 Python 逻辑混排会拖慢
阅读与检索，按「常量下沉」抽出本模块。

## 说明
**纯搬移**——每段 JS 逐字节不变（含转义与原样字符串前缀）。
"""

CAP_DOM_SWEEP_JS = """
(() => {
  const out = [];
  const items = document.querySelectorAll('[class*=conversationConversationItemwrapper]');
  for (const it of items) {
    try {
      const t = it.querySelector('[class*=ConversationItemtitle]');
      if (!t) continue;
      // 2026-09-14 v0.43.11：昵称取**完整**首行（原用 innerText 会把时间戳
      // 混进来，如「四川工伤-张老师昨天 03:00」）。title 元素 innerText 的
      // 第一行是纯昵称，这里保持 split 但只取首行且 trim。
      const nick = (t.innerText || '').split(String.fromCharCode(10))[0].trim();
      if (!nick) continue;
      const img = it.querySelector('img');
      // 2026-09-14 v0.43.11 A+B：新增 desc（会话最后一条消息预览）。
      // 这是 DOM 侧唯一能与「首包消息」做**精确文本匹配**的字段 ——
      // 用于把 DOM 的昵称/头像精确桥接到 peer_uid（见 conversation_capture）。
      const d = it.querySelector('[class*=ConversationItemDescleft]');
      const desc = d ? (d.innerText || '').replace(/\\s+/g, ' ').trim().slice(0, 120) : '';
      out.push({nickname: nick, avatar: img ? (img.src || '') : '', desc: desc});
    } catch (e) {}
  }
  return out;
})()
"""

CAP_DOM_SCROLL_JS = """
(y) => {
  const c = document.querySelector('.conversationConversationListwrapper');
  if (!c) return {ok: false, why: 'no-container'};
  c.scrollTop = y;
  return {ok: true, top: Math.round(c.scrollTop), sh: c.scrollHeight, ch: c.clientHeight};
}
"""

# 模块级 hook 脚本：截 im/user/info 响应（必须在 context 创建后、goto 前 add_init_script 注入）
CAP_USERINFO_HOOK_JS = r"""
(() => {
  if (window.__CAP_USERINFO__) return 'already';
  window.__CAP_USERINFO__ = { map: {} };
  const origFetch = window.fetch.bind(window);
  window.fetch = function(u, o) {
    const p = origFetch(u, o);
    try {
      const url = (typeof u === 'string') ? u : (u && u.url) || '';
      if (/im\/user\/info/.test(url)) {
        p.then(r => r.clone().json().catch(()=>null)).then(obj => {
          try {
            for (const it of (obj && obj.data) || []) {
              const su = it.sec_uid || it.sec_user_id;
              if (su) window.__CAP_USERINFO__.map[su] = {
                nickname: it.nickname || '',
                avatar: (it.avatar_thumb && it.avatar_thumb.url_list && it.avatar_thumb.url_list[0]) || '',
                uid: it.uid != null ? String(it.uid) : ''
              };
            }
          } catch(e) {}
        }).catch(()=>{});
      }
    } catch(e) {}
    return p;
  };
  const origXHR = window.XMLHttpRequest;
  window.XMLHttpRequest = function() {
    const x = new origXHR();
    const o = x.open; x.open = function(m,u,...r){ x.__u=u; x.__m=m; return o.call(x,m,u,...r); };
    const ob = x.send; x.send = function(d){ return ob.call(x,d); };
    x.addEventListener('load', function(){
      try {
        if (x.__u && /im\/user\/info/.test(x.__u)) {
          const obj = JSON.parse(x.responseText || '{}');
          for (const it of (obj.data) || []) {
            const su = it.sec_uid || it.sec_user_id;
            if (su) window.__CAP_USERINFO__.map[su] = {
              nickname: it.nickname || '',
              avatar: (it.avatar_thumb && it.avatar_thumb.url_list && it.avatar_thumb.url_list[0]) || '',
              uid: it.uid != null ? String(it.uid) : ''
            };
          }
        }
      } catch(e) {}
    });
    return x;
  };
  return 'captured';
})()
"""

# ---------------------------------------------------------------------------
# WP 通道私信消息 hook（2026-09-05 新增）
# 抖音网页版 douyin.com/chat 的私信收发会走两类请求：
#   - HTTP: imapi.douyin.com/v1/message/get_message_by_init（首包 250KB，全量会话）
#           imapi.douyin.com/v1/message/get_by_conversation（cmd 301，逐会话历史）
#           （2026-09-05 修正：原写 www.douyin.com/aweme/v1/web/... 是错的，
#             实测 404 Unsupported path(Janus)；真实接口在 imapi.douyin.com，
#             见知识库 08 §33.1 / §34.2）
#   - WebSocket: 实时推送新私信
# 这里被动 hook 这两类，把原始帧 raw 推入 window.__CAP_WP_MESSAGE__.events，
# 由后端 wp_recv 轮询读取后统一解析（不在页面内解析，保持 hook 极简、低侵入）。
# 风控边界：纯被动监听，绝不主动发请求、绝不遍历用户信息（昵称红线 08 §13）。
CAP_WP_MESSAGE_HOOK_JS = r"""(() => {
  if (window.__CAP_WP_MESSAGE__) return 'already';
  window.__CAP_WP_MESSAGE__ = { events: [] };
  const push = (kind, url, body) => {
    try {
      const ev = { kind, url, body, ts: Date.now() };
      const arr = window.__CAP_WP_MESSAGE__.events;
      arr.push(ev);
      if (arr.length > 500) arr.splice(0, arr.length - 500); // 上限防爆
    } catch(e) {}
  };
  // 2026-09-05 修正: 正则用 new RegExp(字符串) 构造。
  // 曾经的 bug: 直接写正则字面量 /.../ 且跨行 -> JS 语法错误 ->
  // 整个 init script 静默失败, window.__CAP_WP_MESSAGE__ 从未创建。
  // 匹配 imapi.douyin.com 的真实私信接口（知识库 08 §33.1 实证,
  // 不是 www.douyin.com/aweme/v1/web/... 那条, 后者实测 404 Janus）。
  const IMAPI_RE = new RegExp(
    '/(v1|v2)/.*(' +
    ['get_message_by_init', 'get_by_conversation', 'get_user_message',
     'get_info_list', 'mark_read', 'message/send', 'conversation/create',
     'conversation/info'].join('|') +
    ')'
  );
  // ---- fetch hook ----
  const origFetch = window.fetch.bind(window);
  window.fetch = function(u, o) {
    const p = origFetch(u, o);
    try {
      const url = (typeof u === 'string') ? u : (u && u.url) || '';
      if (IMAPI_RE.test(url)) {
        p.then(r => r.clone().text().catch(()=>null)).then(t => {
          if (t) push('http', url, t.slice(0, 400000));
        }).catch(()=>{});
      }
    } catch(e) {}
    return p;
  };
  // ---- XMLHttpRequest hook ----
  const origXHR = window.XMLHttpRequest;
  window.XMLHttpRequest = function() {
    const x = new origXHR();
    const o = x.open; x.open = function(m, u, ...r){ x.__u = u; x.__m = m; return o.call(x, m, u, ...r); };
    const ob = x.send; x.send = function(d){ return ob.call(x, d); };
    x.addEventListener('load', function(){
      try {
        if (x.__u && IMAPI_RE.test(x.__u)) {
          push('http', x.__u, (x.responseText || '').slice(0, 400000));
        }
      } catch(e) {}
    });
    return x;
  };
  // ---- WebSocket hook（双向：send 发出 + message 收进）----
  const OrigWS = window.WebSocket;
  window.WebSocket = function(u, p) {
    const ws = (typeof p === 'string') ? new OrigWS(u, p) : new OrigWS(u);
    const origAdd = ws.addEventListener.bind(ws);
    ws.addEventListener = function(ev, cb) {
      if (ev === 'message') {
        return origAdd(ev, (e) => {
          try {
            // 2026-09-06 全局治理：二进制帧不再丢弃为 '<binary>' 占位。
            // 抖音 IM 实时推送大量走 protobuf 二进制帧，之前 100% 丢失，
            // WP 通道只能靠 HTTP 轮询被动补消息。现在转 base64 上抛，
            // 后端侧按前缀 'B64:' 识别（wp_recv.parse_ws_frame 对非 JSON
            // 帧本来就只记 debug 跳过，不会误解析；需要实时性时再解码）。
            let d;
            if (typeof e.data === 'string') {
              d = e.data;
            } else if (e.data instanceof Blob) {
              d = 'B64:';  // Blob 异步读取复杂度高，先标记等待后续 FileReader 支持
            } else if (e.data instanceof ArrayBuffer) {
              const bytes = new Uint8Array(e.data);
              let bin = '';
              for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i]);
              d = 'B64:' + btoa(bin);
            } else {
              d = String(e.data);
            }
            if (/im|message|conversation/i.test(d) || d.startsWith('B64:')) push('ws', String(u), d.slice(0, 400000));
          } catch(e2) {}
          return cb(e);
        });
      }
      return origAdd(ev, cb);
    };
    return ws;
  };
  return 'captured';
})()
"""



