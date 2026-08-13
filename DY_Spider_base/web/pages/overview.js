// pages/overview.js —— 总览页（数据全部来自接口，框架层固定不变）
function Overview({
  push,
  api,
  overview,
  ready
}) {
  const [viewMode, setViewMode] = useState('single');
  const [activeAcct, setActiveAcct] = useState('');
  const [feed, setFeed] = useState([]);
  const [stats, setStats] = useState(null);
  const [accounts, setAccounts] = useState([]);
  const ov = overview || {};
  useEffect(() => {
    if (!ready) return;
    let alive = true;
    const load = () => {
      api.getAccounts().then(d => {
        if (alive && d && d.ok) setAccounts(d.accounts || []);
      }).catch(() => {});
      api.getStats().then(d => {
        if (alive && d && d.ok) setStats(d);
      }).catch(() => {});
    };
    load();
    const iv = setInterval(load, 3000);
    return () => {
      alive = false;
      clearInterval(iv);
    };
  }, [api, ready]);
  const realFeed = (stats && stats.list || []).slice(0, 24).map((r, i) => ({
    id: 100000 + i,
    t: (r.captureTs || '').slice(-8) || '—',
    k: r.status === '已发送' || r.status === '私信已发送' ? 'msg' : 'danmaku',
    n: r.nickname || '未知',
    l: 0,
    x: (r.comment || '') + (r.content ? ' → 私信: ' + r.content : '')
  }));
  const shownFeed = ready && realFeed.length ? realFeed : feed;
  const statCards = ready ? [{
    label: '已发私信',
    num: (ov.sent || 0).toLocaleString() + '/' + (ov.limit || 0),
    delta: '待发 ' + (ov.queue || 0),
    color: 'var(--accent)'
  }, {
    label: '捕获评论',
    num: (stats ? stats.total : 0).toLocaleString() + ' 条',
    delta: '已发 ' + (stats ? stats.sent : 0),
    color: 'var(--ok)'
  }, {
    label: '账号',
    num: ov.browserDaemon && ov.browserDaemon.alive ? '凭证就绪' : '凭证离线',
    delta: ov.recvDaemon && ov.recvDaemon.alive ? '私信守护在线' : '私信守护离线',
    color: 'var(--warn)'
  }, {
    label: '引擎状态',
    num: ov.running ? ov.paused ? '已暂停' : '运行中' : '已停止',
    delta: ov.status || '',
    color: 'var(--accent)'
  }] : null;
  const taskList = ready ? React.createElement("div", {
    className: "run-item"
  }, React.createElement("div", {
    className: "top"
  }, React.createElement("span", {
    className: "nm"
  }, "\u81EA\u52A8\u79C1\u4FE1\u5F15\u64CE \xB7 ", ov.liveUrl || '未配置直播间'), React.createElement("span", {
    className: "pct",
    style: {
      color: ov.running ? 'var(--ok)' : 'var(--muted)'
    }
  }, ov.running ? ov.paused ? '已暂停' : '运行中' : '未启动')), React.createElement("div", {
    className: "track"
  }, React.createElement("div", {
    className: "bar",
    style: {
      width: (ov.limit ? Math.min(100, Math.round((ov.sent || 0) / ov.limit * 100)) : 0) + '%'
    }
  }))) : TASKS_INIT.slice(0, 2).map(t => React.createElement("div", {
    className: "run-item",
    key: t.id
  }, React.createElement("div", {
    className: "top"
  }, React.createElement("span", {
    className: "nm"
  }, t.type, " \xB7 ", t.target), React.createElement("span", {
    className: "pct"
  }, t.status === 'running' ? t.prog + '%' : '完成')), React.createElement("div", {
    className: "track"
  }, React.createElement("div", {
    className: "bar",
    style: {
      width: (t.status === 'running' ? t.prog : 100) + '%'
    }
  }))));
  const curAcct = accounts.find(a => a.name === activeAcct) || accounts.find(a => a.isCurrent) || accounts[0] || null;
  const gridCards = accounts.map(acct => React.createElement("div", {
    className: "card",
    key: acct.name,
    style: {
      padding: 0,
      overflow: 'hidden'
    }
  }, React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 10,
      padding: '12px 16px',
      borderBottom: '1px solid var(--border)',
      background: 'var(--surface-2)'
    }
  }, React.createElement(Avatar, {
    name: acct.name,
    h: 20
  }), React.createElement("div", {
    style: {
      flex: 1
    }
  }, React.createElement("div", {
    style: {
      fontWeight: 600,
      fontSize: 14
    }
  }, acct.name, acct.isCurrent ? ' · 当前' : ''), React.createElement("div", {
    className: "mono",
    style: {
      fontSize: 11.5,
      color: 'var(--muted)'
    }
  }, "UID: ", acct.uid || '—')), React.createElement(Pill, {
    c: acct.loggedIn ? 'ok' : 'danger'
  }, acct.loggedIn ? acct.signReady ? '已登录·签名就绪' : '已登录' : '离线')), React.createElement("div", {
    className: "grid cols-2",
    style: {
      padding: '10px 16px',
      gap: 6
    }
  }, React.createElement("div", {
    className: "card stat",
    style: {
      padding: '6px 8px'
    }
  }, React.createElement("span", {
    className: "label",
    style: {
      fontSize: 10
    }
  }, "\u76D1\u6D4B\u89D2\u8272"), React.createElement("span", {
    className: "num",
    style: {
      fontSize: 14
    }
  }, acct.isMonitor ? '是' : '否')), React.createElement("div", {
    className: "card stat",
    style: {
      padding: '6px 8px'
    }
  }, React.createElement("span", {
    className: "label",
    style: {
      fontSize: 10
    }
  }, "\u53D1\u9001\u89D2\u8272"), React.createElement("span", {
    className: "num",
    style: {
      fontSize: 14
    }
  }, acct.isSender ? '是' : '否'))), React.createElement("div", {
    style: {
      padding: '10px 16px',
      borderTop: '1px solid var(--border)'
    }
  }, React.createElement("div", {
    className: "head-row",
    style: {
      marginBottom: 6
    }
  }, React.createElement("span", {
    style: {
      fontSize: 12,
      color: 'var(--muted)'
    }
  }, "\u5B88\u62A4\u72B6\u6001"), React.createElement("span", {
    className: "mono",
    style: {
      fontSize: 12,
      fontWeight: 600
    }
  }, ready && ov.browserDaemon && ov.browserDaemon.alive ? '凭证守护在线' : '守护离线')), React.createElement("div", {
    className: "head-row"
  }, React.createElement("span", {
    style: {
      fontSize: 12,
      color: 'var(--muted)'
    }
  }, "\u79C1\u4FE1\u5B88\u62A4"), React.createElement("span", {
    className: "mono",
    style: {
      fontSize: 12
    }
  }, ready && ov.recvDaemon && ov.recvDaemon.alive ? '在线' : '离线'))), React.createElement("div", {
    style: {
      padding: '8px 16px',
      borderTop: '1px solid var(--border)',
      display: 'flex',
      gap: 6
    }
  }, React.createElement("button", {
    className: "btn sm ghost",
    style: {
      flex: 1
    },
    onClick: () => {
      setViewMode('single');
      setActiveAcct(acct.name);
      push('已切换到 ' + acct.name);
    }
  }, "\u67E5\u770B\u8BE6\u60C5"))));
  return React.createElement("div", null, React.createElement("div", {
    className: "section-head"
  }, React.createElement("div", null, React.createElement("h2", null, "\u603B\u89C8"), React.createElement("div", {
    className: "desc"
  }, "\u7CFB\u7EDF\u8FD0\u884C\u72B6\u6001\u4E0E\u8D26\u53F7\u6982\u51B5")), React.createElement("div", {
    className: "head-row"
  }, React.createElement("div", {
    className: "seg"
  }, React.createElement("button", {
    className: viewMode === 'single' ? 'active' : '',
    onClick: () => setViewMode('single')
  }, "\u5355\u8D26\u6237"), React.createElement("button", {
    className: viewMode === 'grid' ? 'active' : '',
    onClick: () => setViewMode('grid')
  }, "\u591A\u8D26\u6237\u603B\u89C8")), React.createElement("span", {
    className: "badge-conn"
  }, React.createElement(Dot, {
    c: !ready ? 'mute' : ov.running ? ov.paused ? 'warn' : 'ok' : 'danger',
    pulse: ready && ov.running && !ov.paused
  }), " ", ready ? ov.running ? ov.paused ? '已暂停' : '引擎运行中' : '引擎已停止' : '未连接'), ready && React.createElement("span", {
    className: "badge-conn"
  }, React.createElement("b", null, "\u5DF2\u53D1 ", ov.sent, "/", ov.limit, ov.queue ? ' · 待发 ' + ov.queue : '')))), viewMode === 'grid' ? React.createElement("div", {
    className: "grid cols-2",
    "data-od-id": "overview-grid"
  }, gridCards.length ? gridCards : React.createElement("div", {
    className: "feed-item",
    style: {
      color: 'var(--muted)'
    }
  }, "\u6682\u65E0\u8D26\u53F7\u6570\u636E")) : React.createElement(React.Fragment, null, React.createElement("div", {
    className: "card",
    style: {
      marginBottom: 14
    },
    "data-od-id": "overview-acct-select"
  }, React.createElement("div", {
    className: "head-row"
  }, React.createElement("span", {
    style: {
      fontSize: 13,
      fontWeight: 600
    }
  }, "\u5F53\u524D\u67E5\u770B\u8D26\u53F7"), React.createElement("div", {
    style: {
      flex: 1
    }
  }), React.createElement("div", {
    className: "seg"
  }, accounts.map(a => React.createElement("button", {
    key: a.name,
    className: activeAcct === a.name ? 'active' : '',
    onClick: () => {
      setActiveAcct(a.name);
      push('已切换到 ' + a.name);
    }
  }, React.createElement(Avatar, {
    name: a.name,
    h: 20,
    sm: true
  }), " ", a.name)))), curAcct && React.createElement("div", {
    className: "head-row",
    style: {
      marginTop: 10,
      fontSize: 12,
      color: 'var(--muted)'
    }
  }, React.createElement("span", null, "UID: ", curAcct.uid || '—'), React.createElement("span", null, "\u767B\u5F55: ", curAcct.loggedIn ? '是' : '否'), React.createElement("span", null, "\u7B7E\u540D: ", curAcct.signReady ? '就绪' : '未就绪'), React.createElement("span", null, "\u76D1\u6D4B: ", curAcct.isMonitor ? '是' : '否'), React.createElement("span", null, "\u53D1\u9001: ", curAcct.isSender ? '是' : '否'))), React.createElement("div", {
    className: "grid cols-4",
    style: {
      marginBottom: 14
    }
  }, (statCards || []).map((c, i) => React.createElement("div", {
    className: "card stat",
    key: i,
    "data-od-id": 'stat-' + i
  }, React.createElement("span", {
    className: "label"
  }, c.label), React.createElement("span", {
    className: "num"
  }, c.num, React.createElement("span", {
    className: "unit"
  }, " ")), React.createElement("div", {
    className: "spark"
  }, React.createElement("span", {
    className: "delta",
    style: {
      color: c.color
    }
  }, c.delta))))), React.createElement("div", {
    className: "grid cols-2"
  }, React.createElement("div", {
    className: "card",
    "data-od-id": "overview-feed"
  }, React.createElement("h3", null, "\u5B9E\u65F6\u52A8\u6001 ", React.createElement("span", {
    className: "demo-tag",
    style: {
      textTransform: 'none'
    }
  }, ready ? '实时' : '未连接')), React.createElement("div", {
    className: "feed"
  }, shownFeed.map(f => React.createElement("div", {
    className: "feed-item",
    key: f.id
  }, React.createElement("span", {
    className: "tm"
  }, f.t), React.createElement("span", {
    className: 'kind k-' + f.k
  }, KIND_NAME[f.k] || f.k), React.createElement("span", {
    className: "txt"
  }, React.createElement("b", null, f.n), "\u3000", f.x), f.l ? React.createElement("span", {
    className: "lv"
  }, "Lv.", f.l) : null)), !shownFeed.length && React.createElement("div", {
    className: "feed-item",
    style: {
      color: 'var(--muted)'
    }
  }, "\u6682\u65E0\u6570\u636E"))), React.createElement("div", {
    className: "card",
    "data-od-id": "overview-tasks"
  }, React.createElement("h3", null, "\u8FD0\u884C\u4E2D\u4EFB\u52A1"), React.createElement("div", {
    className: "run-list"
  }, taskList)))));
}
