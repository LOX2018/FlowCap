// framework.js —— 固定框架层（外壳/导航/公共组件/常量/桥接）
// 此文件内容稳定，不随业务页面改动而变更；由 index.html 优先加载并启用 HTTP 强缓存，
// 实现“框架类内容固定下来、不用每次都重新下载/解析”，页面数据全部走接口动态渲染。
const {
  useState,
  useEffect,
  useRef,
  useMemo,
  useCallback
} = React;
const {
  motion,
  AnimatePresence
} = window.Motion;
function tick(off) {
  const d = new Date(Date.now() + (off || 0));
  const p = n => (n < 10 ? '0' : '') + n;
  return p(d.getHours()) + ':' + p(d.getMinutes()) + ':' + p(d.getSeconds());
}
function nowHM() {
  const d = new Date();
  const p = n => (n < 10 ? '0' : '') + n;
  return p(d.getHours()) + ':' + p(d.getMinutes());
}
const pick = arr => arr[Math.floor(Math.random() * arr.length)];
const HUES = ['150', '210', '300', '20', '40', '80', '260', '180'];
const hue = i => HUES[i % HUES.length];
const SPEAKERS = [];
const COMMENTS = [];
const GIFTS = [];
const DM_TEXTS = [];
const FEEDKIND = [];
const KIND_NAME = {
  danmaku: '弹幕',
  gift: '礼物',
  enter: '进场',
  follow: '关注',
  like: '点赞'
};
const VIDEOS = [];
const USERS = [];
const LIVES = [];
const COMMENTS_DETAIL = [];
let cid = 0;
const TASKS_INIT = [];
const TASK_ST = {
  running: ['运行中', 'accent'],
  done: ['成功', 'ok'],
  queued: ['排队', 'mute'],
  fail: ['失败', 'danger']
};
const CONVS_INIT = [];
const LOGS = [];
const ACCOUNTS_INIT = [];
function Spark({
  pts,
  color = 'var(--accent)'
}) {
  const w = 104,
    h = 30;
  const max = Math.max(...pts),
    min = Math.min(...pts);
  const x = i => i / (pts.length - 1) * w;
  const y = v => h - 3 - (v - min) / (max - min || 1) * (h - 6);
  const d = pts.map((v, i) => (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1)).join('');
  return React.createElement("svg", {
    width: w,
    height: h,
    viewBox: '0 0 ' + w + ' ' + h,
    "aria-hidden": "true"
  }, React.createElement("path", {
    d: d + ' L' + w + ' ' + h + ' L0 ' + h + ' Z',
    fill: color,
    opacity: ".12"
  }), React.createElement("path", {
    d: d,
    fill: "none",
    stroke: color,
    strokeWidth: "1.6"
  }), React.createElement("circle", {
    cx: x(pts.length - 1),
    cy: y(pts[pts.length - 1]),
    r: "2.4",
    fill: color
  }));
}
function Pill({
  c,
  children
}) {
  const map = {
    ok: ['var(--ok)', 'var(--ok-bg)'],
    warn: ['var(--warn)', 'var(--warn-bg)'],
    danger: ['var(--danger)', 'var(--danger-bg)'],
    accent: ['var(--accent)', 'var(--accent-bg)'],
    mute: ['var(--muted)', 'var(--surface-2)']
  };
  const [color, bg] = map[c] || map.mute;
  return React.createElement("span", {
    className: "pill",
    style: {
      color,
      background: bg
    }
  }, React.createElement("span", {
    className: "bullet",
    style: {
      background: color
    }
  }), children);
}
function Avatar({
  name,
  h,
  sm,
  lg
}) {
  return React.createElement("span", {
    className: 'avatar' + (sm ? ' sm' : '') + (lg ? ' lg' : ''),
    style: {
      background: 'oklch(55% 0.14 ' + h + ')'
    }
  }, name.charAt(0));
}
function Dot({
  c,
  pulse
}) {
  return React.createElement("span", {
    className: 'dot ' + c + (pulse ? ' pulse' : '')
  });
}
function Num({
  v
}) {
  return React.createElement("span", {
    className: "mono"
  }, v);
}
const TABS = [['overview', '总览'], ['crawl', '采集'], ['live', '直播监听'], ['msg', '私信'], ['accounts', '账号管理'], ['tasks', '任务中心'], ['settings', '设置']];
function Header({
  tab,
  setTab,
  overview,
  ready
}) {
  const ov = overview || {};
  const running = !!ov.running;
  const paused = !!ov.paused;
  const statusColor = !ready ? 'mute' : running ? paused ? 'warn' : 'ok' : 'danger';
  const statusText = !ready ? '未连接' : running ? paused ? '已暂停' : '运行中' : '已停止';
  const bd = ov.browserDaemon || {};
  const rd = ov.recvDaemon || {};
  return React.createElement("header", {
    className: "nav"
  }, React.createElement("div", {
    className: "brand"
  }, React.createElement("span", {
    className: "mark",
    "aria-hidden": "true"
  }), React.createElement("h1", null, "\u6296\u97F3\u6570\u636E\u63A7\u5236\u53F0"), React.createElement("span", {
    className: "sub"
  }, "Douyin Console")), React.createElement("nav", {
    className: "tabs",
    "aria-label": "\u4E3B\u5BFC\u822A"
  }, TABS.map(([id, label]) => React.createElement("button", {
    key: id,
    "data-od-id": 'tab-' + id,
    className: 'tab' + (tab === id ? ' active' : ''),
    onClick: () => setTab(id)
  }, label))), React.createElement("div", {
    className: "nav-status"
  }, React.createElement("span", {
    className: "badge-conn"
  }, React.createElement(Dot, {
    c: statusColor,
    pulse: running && !paused
  }), " \u5F15\u64CE ", React.createElement("b", null, statusText)), React.createElement("span", {
    className: "badge-conn"
  }, React.createElement(Dot, {
    c: bd && bd.alive ? 'ok' : 'danger',
    pulse: bd && bd.alive
  }), " \u51ED\u8BC1\u5B88\u62A4 ", React.createElement("b", null, bd && bd.alive ? bd.signReady ? '已就绪' : '登录中' : '离线')), React.createElement("span", {
    className: "badge-conn"
  }, React.createElement(Dot, {
    c: rd && rd.alive ? 'ok' : 'danger',
    pulse: rd && rd.alive
  }), " \u79C1\u4FE1\u5B88\u62A4 ", React.createElement("b", null, rd && rd.alive ? '在线' : '离线')), ready && React.createElement("span", {
    className: "badge-conn"
  }, React.createElement("b", null, "\u5DF2\u53D1 ", ov.sent, "/", ov.limit, ov.queue ? ' · 待发 ' + ov.queue : '')), !ready && React.createElement("span", {
    className: "demo-tag"
  }, "\u672A\u8FDE\u63A5")));
}
// 默认 mock 桥（未连接后端时使用），真实后端通过 pywebviewready 事件覆盖 window.ApiBridge
window.ApiBridge = {
  ready: false,
  getOverview: function () {
    return Promise.resolve(null);
  },
  getStats: function () {
    return Promise.resolve(null);
  },
  getConversations: function () {
    return Promise.resolve(null);
  },
  getAccounts: function () {
    return Promise.resolve(null);
  },
  checkAccount: function () {
    return Promise.resolve({
      ok: false,
      error: '未连接后端'
    });
  },
  getTasks: function () {
    return Promise.resolve(null);
  },
  sendDm: function () {
    return Promise.resolve({
      ok: true
    });
  },
  exportStats: function () {
    return Promise.resolve({
      ok: true
    });
  }
};
window.addEventListener('pywebviewready', function () {
  if (window.pywebview && window.pywebview.api) {
    Object.assign(window.ApiBridge, window.pywebview.api);
    window.ApiBridge.ready = true;
    console.log('[bridge] pywebview api ready');
  }
});
