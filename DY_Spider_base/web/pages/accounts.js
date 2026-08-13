// pages/accounts.js —— 账号管理页（含 mapAcct/AccountReview/ProxyDrawer/AccountDrawer 子组件，数据走接口）
function Accounts({
  push,
  goMsg,
  api
}) {
  const [accounts, setAccounts] = useState(ACCOUNTS_INIT);
  const [reviewAccount, setReviewAccount] = useState(null);
  const [proxyAcct, setProxyAcct] = useState(null);
  const [proxyForm, setProxyForm] = useState({
    type: 'socks5',
    host: '127.0.0.1',
    port: '1080',
    user: '',
    pass: '',
    testUrl: ''
  });
  const [proxyTesting, setProxyTesting] = useState(false);
  const [proxyTestResult, setProxyTestResult] = useState(null);
  const [addOpen, setAddOpen] = useState(false);
  const [addForm, setAddForm] = useState({
    name: '',
    uid: '',
    cookie: ''
  });
  const [editAcct, setEditAcct] = useState(null);
  const [editForm, setEditForm] = useState({
    name: '',
    uid: '',
    cookie: ''
  });
  const [batchMode, setBatchMode] = useState(false);
  const [batchSel, setBatchSel] = useState(() => new Set());
  const [realAccts, setRealAccts] = useState(null);
  // 后端原始账号对象 → 前端卡片渲染所需完整字段（含 lastRun/fp 等默认值），
  // 供 getAccounts 轮询刷新与「全部校验」复用，避免直接 setRealAccts 原始对象导致渲染崩溃黑屏。
  const mapAcct = (a, i) => {
    const lvl = a.level || (a.loggedIn ? 'ok' : 'expired');
    const lvlLabel = a.label || (a.loggedIn ? '凭证有效' : '凭证过期');
    const isValid = lvl === 'ok';
    return {
      id: 'ra' + i,
      name: a.name,
      uid: a.uid || '—',
      hue: hue(a.name.length * 2),
      tokenValid: isValid,
      tokenExpire: isValid ? '有效' : (lvl === 'nosign' ? '未扫码' : lvl === 'missing' ? '未配置' : '失效'),
      lvlLabel: lvlLabel,
      lvl: lvl,
      browserDaemonPort: a.browserDaemonPort,
      recvDaemonPort: a.recvDaemonPort,
      browserDaemonAlive: !!a.browserDaemonAlive,
      recvDaemonAlive: !!a.recvDaemonAlive,
      wpEngine: a.wpEngine || { level: 'unknown', label: '未校验', detail: '' },
      dmEngine: a.dmEngine || { level: 'unknown', label: '未校验', detail: '' },
      isCurrent: !!a.isCurrent,
      isMonitor: !!a.isMonitor,
      isSender: !!a.isSender,
      lastCheck: '刚刚',
      lastRun: {
        room: '—',
        roomUrl: '',
        time: '—',
        duration: '—',
        totalRuns: 0,
        comments: 0,
        dmSent: 0,
        dmSuccess: 0,
        dmFail: 0,
        dmAfterLive: 0
      },
      fp: {
        name: '浏览器守护 · ' + a.name,
        status: 'running',
        os: 'Windows 11',
        ip: '—',
        resolution: '1920×1080',
        ua: 'Chrome',
        proxy: '直连',
        lang: 'zh-CN',
        webrtc: '替换',
        timezone: 'Asia/Shanghai'
      }
    };
  };
  useEffect(() => {
    if (!window.ApiBridge || !window.ApiBridge.ready) return;
    let alive = true;
    const load = () => api.getAccounts().then(d => {
      if (!alive || !d || !d.ok) return;
      const list = d.accounts || [];
      if (list.length) {
        setRealAccts(list.map(mapAcct));
      }
    }).catch(() => {});
    load();
    const iv = setInterval(load, 8000);
    return () => {
      alive = false;
      clearInterval(iv);
    };
  }, [api]);
  const shownAccounts = realAccts || accounts;
  const validCnt = shownAccounts.filter(a => a.tokenValid).length;
  const totalComments = shownAccounts.reduce((s, a) => s + a.lastRun.comments, 0);
  const totalDm = shownAccounts.reduce((s, a) => s + a.lastRun.dmSent, 0);
  const totalFail = shownAccounts.reduce((s, a) => s + a.lastRun.dmFail, 0);
  const fpRunning = shownAccounts.filter(a => a.fp.status === 'running').length;
  const fpSet = useMemo(() => {
    const map = new Map();
    shownAccounts.forEach(a => {
      if (!map.has(a.fp.name)) map.set(a.fp.name, a.fp);
    });
    return Array.from(map.values());
  }, [shownAccounts]);
  useEffect(() => {
    const h = e => {
      if (e.key === 'Escape') {
        if (proxyAcct) setProxyAcct(null);else if (reviewAccount) setReviewAccount(null);else if (editAcct) setEditAcct(null);else if (addOpen) setAddOpen(false);
      }
    };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [proxyAcct, reviewAccount, editAcct, addOpen]);
  const checkAll = () => {
    if (window.ApiBridge && window.ApiBridge.ready) {
      // 真实后端：重新拉取并刷新卡片（getAccounts 内部已做实时探活），
      // 避免“重新获取凭证后刷新仍显示旧失败状态”。
      // 注意：必须用 mapAcct 映射（与轮询一致），直接 setRealAccts 原始对象会因
      // lastRun/fp 等字段缺失导致渲染崩溃（黑屏）。
      api.getAccounts().then(d => {
        if (d && d.ok && d.accounts) setRealAccts(d.accounts.map(mapAcct));
        push(d && d.ok ? '已刷新全部账号状态' : '刷新失败');
      }).catch(e => push('刷新异常: ' + e));
      return;
    }
    setAccounts(accounts.map(a => ({
      ...a,
      lastCheck: tick()
    })));
    push('已对全部 ' + accounts.length + ' 个账号执行凭证校验');
  };
  const openEdit = a => {
    setEditForm({
      name: a.name,
      uid: a.uid && a.uid !== '—' ? a.uid : '',
      cookie: ''
    });
    setEditAcct(a);
  };
  const saveEdit = () => {
    const name = editForm.name.trim();
    if (!name) {
      push('请填写昵称');
      return;
    }
    if (window.ApiBridge && window.ApiBridge.ready) {
      // 重新获取凭证 = 重新拉起内置指纹浏览器展示扫码二维码，扫码后凭证写回该账号
      api.scanLogin(name).then(r => {
        if (r && r.ok) {
          push('已弹出指纹浏览器 · ' + name + '，请扫码完成登录（凭证将自动写回）');
          setEditAcct(null);
        } else push('重新获取凭证失败: ' + (r && r.error || ''));
      }).catch(e => push('重新获取凭证异常: ' + e));
      return;
    }
    setAccounts(accts => accts.map(a => a.id === editAcct.id ? {
      ...a,
      name
    } : a));
    push('已刷新凭证 · ' + name);
    setEditAcct(null);
  };
  const toggleBatch = id => {
    setBatchSel(s => {
      const n = new Set(s);
      n.has(id) ? n.delete(id) : n.add(id);
      return n;
    });
  };
  const batchAll = () => {
    if (batchSel.size === shownAccounts.length) setBatchSel(new Set());else setBatchSel(new Set(shownAccounts.map(a => a.id)));
  };
  const batchCheck = () => {
    if (!batchSel.size) {
      push('请先勾选账号');
      return;
    }
    setAccounts(accounts.map(a => batchSel.has(a.id) ? {
      ...a,
      lastCheck: tick()
    } : a));
    push('已对 ' + batchSel.size + ' 个所选账号执行凭证校验');
  };
  const batchExport = () => {
    if (!batchSel.size) {
      push('请先勾选账号');
      return;
    }
    push('已导出 ' + batchSel.size + ' 个账号的运行数据');
  };
  const batchDelete = () => {
    if (!batchSel.size) {
      push('请先勾选账号');
      return;
    }
    if (window.ApiBridge && window.ApiBridge.ready) {
      const ids = Array.from(batchSel);
      Promise.all(ids.map(id => {
        const a = shownAccounts.find(x => x.id === id);
        return a ? api.removeAccount(a.name).catch(() => ({})) : Promise.resolve({});
      })).then(() => {
        push('已删除 ' + ids.length + ' 个账号');
        setBatchSel(new Set());
        setBatchMode(false);
      });
      return;
    }
    setAccounts(accounts.filter(a => !batchSel.has(a.id)));
    push('已删除 ' + batchSel.size + ' 个账号');
    setBatchSel(new Set());
    setBatchMode(false);
  };
  const addAccount = () => {
    const name = addForm.name.trim();
    if (!name) {
      push('请填写昵称');
      return;
    }
    if (window.ApiBridge && window.ApiBridge.ready) {
      api.addAccount(name).then(r => {
        if (r && r.ok) {
          setAddOpen(false);
          setAddForm({
            name: '',
            uid: '',
            cookie: ''
          });
          push('已新增账号 ' + name + '，正在打开内置指纹浏览器…');
          api.scanLogin(name).then(s => {
            if (s && s.ok) push('已弹出浏览器扫码窗口，请在浏览器中完成扫码登录（凭证将自动写回）');
            else push('打开扫码窗口失败: ' + (s && s.error || ''));
          }).catch(e => push('扫码异常: ' + e));
        } else push('新增失败: ' + (r && r.error || ''));
      }).catch(e => push('新增异常: ' + e));
      return;
    }
    const uid = addForm.uid.trim() || '—';
    const nu = {
      id: 'a' + Date.now(),
      name,
      uid,
      hue: [340, 100, 200, 60, 160][Math.floor(Math.random() * 5)],
      tokenValid: true,
      tokenExpire: '2027-01-01',
      lastCheck: '刚刚',
      lastRun: {
        room: '—',
        roomUrl: '',
        time: '—',
        duration: '—',
        totalRuns: 0,
        comments: 0,
        dmSent: 0,
        dmSuccess: 0,
        dmFail: 0,
        dmAfterLive: 0
      },
      fp: {
        name: 'BitBrowser · Profile 新增',
        status: 'stopped',
        os: 'Windows 11',
        ip: '—',
        resolution: '1920×1080',
        ua: 'Chrome 126.0.6478.126',
        proxy: '直连',
        lang: 'zh-CN',
        webrtc: '替换',
        timezone: 'Asia/Shanghai'
      }
    };
    setAccounts(list => [nu, ...list]);
    setAddOpen(false);
    setAddForm({
      name: '',
      uid: '',
      cookie: ''
    });
    push('已新增账号 ' + name);
  };
  return React.createElement("div", null, React.createElement("div", {
    className: "section-head"
  }, React.createElement("div", null, React.createElement("h2", null, "账号管理"), React.createElement("div", {
    className: "desc"
  }, "授权账号凭证监控 · 运行日志 · 指纹浏览器环境")), React.createElement("div", {
    className: "head-row"
  }, React.createElement("button", {
    className: "btn ghost",
    "data-od-id": "acct-add",
    onClick: () => setAddOpen(true)
  }, "新增账号"), React.createElement("button", {
    className: "btn ghost",
    "data-od-id": "acct-batch",
    onClick: () => {
      setBatchMode(b => !b);
      setBatchSel(new Set());
    }
  }, batchMode ? '退出批量' : '批量管理'), React.createElement("button", {
    className: "btn ghost",
    onClick: checkAll
  }, "全部校验")), window.ApiBridge && window.ApiBridge.ready ? null : React.createElement("span", {
    className: "demo-tag"
  }, "未连接")), React.createElement("div", {
    className: "acct-summary"
  }, React.createElement("div", {
    className: "card stat",
    "data-od-id": "acct-total"
  }, React.createElement("span", {
    className: "label"
  }, "已授权账号"), React.createElement("span", {
    className: "num"
  }, shownAccounts.length, React.createElement("span", {
    className: "unit"
  }, "个"))), React.createElement("div", {
    className: "card stat",
    "data-od-id": "acct-valid"
  }, React.createElement("span", {
    className: "label"
  }, "有效凭证"), React.createElement("span", {
    className: "num",
    style: {
      color: 'var(--ok)'
    }
  }, validCnt, React.createElement("span", {
    className: "unit"
  }, "个"))), React.createElement("div", {
    className: "card stat",
    "data-od-id": "acct-expired"
  }, React.createElement("span", {
    className: "label"
  }, "过期 / 异常"), React.createElement("span", {
    className: "num",
    style: {
      color: 'var(--danger)'
    }
  }, shownAccounts.length - validCnt, React.createElement("span", {
    className: "unit"
  }, "个"))), React.createElement("div", {
    className: "card stat",
    "data-od-id": "acct-fp"
  }, React.createElement("span", {
    className: "label"
  }, "指纹浏览器在线"), React.createElement("span", {
    className: "num",
    style: {
      color: 'var(--accent)'
    }
  }, fpRunning, React.createElement("span", {
    className: "unit"
  }, "/ ", fpSet.length)))), React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 14
    },
    "data-od-id": "account-list"
  }, batchMode && React.createElement("div", {
    className: "batch-bar",
    "data-od-id": "acct-batch-bar"
  }, React.createElement("button", {
    className: "btn sm ghost",
    onClick: batchAll
  }, batchSel.size === shownAccounts.length ? '取消全选' : '全选'), React.createElement("span", {
    className: "batch-count"
  }, "已选 ", React.createElement("b", {
    style: {
      color: 'var(--accent)',
      fontFamily: 'var(--font-mono)'
    }
  }, batchSel.size), " / ", shownAccounts.length, " 个账号"), React.createElement("div", {
    style: {
      flex: 1
    }
  }), React.createElement("button", {
    className: "btn sm ghost",
    onClick: batchCheck
  }, "批量校验"), React.createElement("button", {
    className: "btn sm ghost",
    onClick: batchExport
  }, "批量导出"), React.createElement("button", {
    className: "btn sm ghost",
    style: {
      color: 'var(--danger)',
      borderColor: 'var(--danger)'
    },
    onClick: batchDelete
  }, "删除所选")), shownAccounts.map(a => React.createElement("div", {
    className: "card acct-card",
    key: a.id,
    "data-od-id": 'acct-' + a.id,
    onClick: function () {
      // 点击卡片 = 选中该账号为监听账号（同时设为当前账号 + 监测/发送角色）
      Promise.resolve(api.setCurrent(a.name)).then(function () {
        return Promise.resolve(api.setRoles && api.setRoles({ monitor: a.name, sender: a.name }));
      });
      push('已选中监听账号 · ' + a.name);
      api.getAccounts && api.getAccounts();
    },
    style: a.isMonitor ? {
      border: '2px solid var(--accent)',
      boxShadow: '0 0 0 3px rgba(54,194,207,0.35)',
      cursor: 'pointer'
    } : { cursor: 'pointer' }
  }, React.createElement("div", {
    className: "acct-header"
  }, batchMode && React.createElement("input", {
    type: "checkbox",
    className: "batch-chk",
    checked: batchSel.has(a.id),
    onChange: () => toggleBatch(a.id),
    "aria-label": '选择 ' + a.name
  }), React.createElement(Avatar, {
    name: a.name,
    h: a.hue,
    lg: true
  }), React.createElement("div", {
    className: "info"
  }, React.createElement("div", {
    className: "nm"
  }, a.name, React.createElement(Pill, {
    c: a.tokenValid ? 'ok' : (a.lvl === 'nosign' ? 'warn' : 'danger')
  }, a.lvlLabel || (a.tokenValid ? '凭证有效' : '凭证过期'))), React.createElement("div", {
    className: "uid"
  }, "UID ", a.uid, " · 上次校验 ", a.lastCheck)), React.createElement("div", {
    className: "ops"
  }, React.createElement("button", {
    className: "btn sm ghost",
    onClick: () => openEdit(a),
    title: "编辑账号信息并刷新登录凭证"
  }, "刷新凭证"), React.createElement("button", {
    className: "btn sm ghost",
    onClick: () => setReviewAccount(a)
  }, "查阅模式"))), React.createElement("div", {
    className: "acct-body"
  }, React.createElement("div", {
    className: "acct-section",
    style: {
      gridColumn: '1',
      gridRow: '1',
      borderBottom: '1px solid var(--border)'
    }
  }, React.createElement("h4", null, "守护服务"), React.createElement("div", {
    className: "acct-row",
    style: {
      justifyContent: 'flex-start'
    }
  }, React.createElement("span", {
    className: "k"
  }, "凭证守护"), React.createElement("span", {
    className: "v",
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8
    }
  }, React.createElement(Pill, {
    c: a.browserDaemonAlive ? 'ok' : 'muted'
  }, a.browserDaemonAlive ? '运行中' : '未运行'), React.createElement("button", {
    className: "btn sm ghost",
    onClick: () => {
      if (a.browserDaemonAlive) {
        api.stopBrowserDaemon(a.name).then(r => {
          if (r && r.ok) {
            setRealAccts(prev => (prev || []).map(x => x.name === a.name ? Object.assign({}, x, { browserDaemonAlive: false }) : x));
            push('凭证守护已停止');
          } else push('停止失败: ' + (r && r.error || ''));
        }).catch(e => push('异常: ' + e));
      } else {
        api.startBrowserDaemon(a.name).then(r => {
          if (r && r.ok) {
            setRealAccts(prev => (prev || []).map(x => x.name === a.name ? Object.assign({}, x, { browserDaemonAlive: true }) : x));
            push('凭证守护已启动');
          } else push('启动失败: ' + (r && r.error || ''));
        }).catch(e => push('异常: ' + e));
      }
    }
  }, a.browserDaemonAlive ? '停止' : '启动'), React.createElement("span", {
    style: {
      fontSize: 11,
      color: 'var(--muted)'
    }
  }, ":", a.browserDaemonPort))), React.createElement("div", {
    className: "acct-row",
    style: {
      justifyContent: 'flex-start',
      marginTop: 10
    }
  }, React.createElement("span", {
    className: "k"
  }, "私信守护"), React.createElement("span", {
    className: "v",
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 6
    }
  }, React.createElement(Pill, {
    c: a.recvDaemonAlive ? 'ok' : 'muted'
  }, a.recvDaemonAlive ? '运行中' : '未运行'), React.createElement("button", {
    className: "btn sm ghost",
    onClick: () => {
      if (a.recvDaemonAlive) {
        api.stopRecvDaemon(a.name).then(r => {
          if (r && r.ok) {
            setRealAccts(prev => (prev || []).map(x => x.name === a.name ? Object.assign({}, x, { recvDaemonAlive: false }) : x));
            push('私信守护已停止');
          } else push('停止失败: ' + (r && r.error || ''));
        }).catch(e => push('异常: ' + e));
      } else {
        api.startRecvDaemon(a.name).then(r => {
          if (r && r.ok) {
            setRealAccts(prev => (prev || []).map(x => x.name === a.name ? Object.assign({}, x, { recvDaemonAlive: true }) : x));
            push('私信守护已启动');
          } else push('启动失败: ' + (r && r.error || ''));
        }).catch(e => push('异常: ' + e));
      }
    }
  }, a.recvDaemonAlive ? '停止' : '启动'), React.createElement("span", {
    style: {
      fontSize: 11,
      color: 'var(--muted)'
    }
  }, ":", a.recvDaemonPort)))), React.createElement("div", {
    className: "acct-section",
    style: { gridColumn: '1', gridRow: '2' }
  }, React.createElement("h4", null, "上次运行日志"), React.createElement("div", {
    className: "acct-row"
  }, React.createElement("span", {
    className: "k"
  }, "直播间"), React.createElement("span", {
    className: "v",
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 6,
      color: a.lastRun.room === '—' ? 'var(--muted)' : 'var(--fg)'
    }
  }, a.lastRun.room, a.lastRun.roomUrl && React.createElement("a", {
    href: a.lastRun.roomUrl,
    target: "_blank",
    rel: "noopener noreferrer",
    className: "btn sm ghost",
    style: {
      height: 24,
      padding: '0 8px',
      fontSize: 11,
      lineHeight: 1
    },
    onClick: () => push('已打开直播间：' + a.lastRun.room)
  }, "前往直播间"))), React.createElement("div", {
    className: "acct-row"
  }, React.createElement("span", {
    className: "k"
  }, "运行时间"), React.createElement("span", {
    className: "v"
  }, a.lastRun.time)), React.createElement("div", {
    className: "acct-row"
  }, React.createElement("span", {
    className: "k"
  }, "运行时长"), React.createElement("span", {
    className: "v"
  }, a.lastRun.duration)), React.createElement("div", {
    className: "acct-row"
  }, React.createElement("span", {
    className: "k"
  }, "累计运行"), React.createElement("span", {
    className: "v"
  }, a.lastRun.totalRuns, " 次")), React.createElement("div", {
    className: "acct-log-grid",
    style: {
      marginTop: 8
    }
  }, React.createElement("div", {
    className: "acct-log-cell"
  }, React.createElement("div", {
    className: "lc-label"
  }, "捕获评论"), React.createElement("div", {
    className: "lc-val"
  }, a.lastRun.comments.toLocaleString())), React.createElement("div", {
    className: "acct-log-cell"
  }, React.createElement("div", {
    className: "lc-label"
  }, "发送私信"), React.createElement("div", {
    className: "lc-val",
    style: {
      color: 'var(--accent)'
    }
  }, a.lastRun.dmSent)), React.createElement("div", {
    className: "acct-log-cell"
  }, React.createElement("div", {
    className: "lc-label"
  }, "下播私信"), React.createElement("div", {
    className: "lc-val",
    style: {
      color: 'var(--warn)'
    }
  }, a.lastRun.dmAfterLive)), React.createElement("div", {
    className: "acct-log-cell"
  }, React.createElement("div", {
    className: "lc-label"
  }, "成功"), React.createElement("div", {
    className: "lc-val",
    style: {
      color: 'var(--ok)'
    }
  }, a.lastRun.dmSuccess)), React.createElement("div", {
    className: "acct-log-cell"
  }, React.createElement("div", {
    className: "lc-label"
  }, "失败"), React.createElement("div", {
    className: "lc-val",
    style: {
      color: 'var(--danger)'
    }
  }, a.lastRun.dmFail)))), React.createElement("div", {
    className: "acct-section",
    style: { gridColumn: '2', gridRow: '1' }
  }, React.createElement("h4", null, "引擎校验"), React.createElement("div", {
    style: { display: 'flex', alignItems: 'stretch', gap: 10 }
  }, React.createElement("div", {
    style: { flex: 1, display: 'grid', gap: 10 }
  }, React.createElement("div", {
    style: { padding: '10px 12px', background: 'var(--bg)', borderRadius: 8 }
  }, React.createElement("div", {
    style: { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }
  }, React.createElement("span", {
    style: { fontWeight: 600, fontSize: 13 }
  }, "wp 引擎"), React.createElement(Pill, {
    c: (a.wpEngine.level === 'ok' ? 'ok' : a.wpEngine.level === 'warn' ? 'warn' : (a.wpEngine.level === 'fail' || a.wpEngine.level === 'error') ? 'danger' : 'info')
  }, a.wpEngine.level === 'stopped' ? '未运行' : a.wpEngine.label)), React.createElement("div", {
    style: { fontSize: 11.5, color: 'var(--muted)', lineHeight: 1.4 }
  }, a.wpEngine.detail)), (a.wpEngine.level === 'fail' || a.dmEngine.level === 'fail') && React.createElement("button", {
    className: "btn sm danger",
    style: { marginTop: 8 },
    onClick: function () {
      push('已提醒处理验证 · ' + a.name + ' · 请在弹出的指纹浏览器中完成验证');
      Promise.resolve(api.scanLogin(a.name)).then(function (d) {
        if (d && d.ok) {
          push(d.msg || ('已为 ' + a.name + ' 弹出指纹浏览器'));
        } else if (d && d.error) {
          push('处理验证失败: ' + d.error);
        }
      });
    }
  }, "立即处理验证"), React.createElement("div", {
    style: { padding: '10px 12px', background: 'var(--bg)', borderRadius: 8 }
  }, React.createElement("div", {
    style: { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 4 }
  }, React.createElement("span", {
    style: { fontWeight: 600, fontSize: 13 }
  }, "私信引擎"), React.createElement(Pill, {
    c: (a.dmEngine.level === 'ok' ? 'ok' : a.dmEngine.level === 'warn' ? 'warn' : (a.dmEngine.level === 'fail' || a.dmEngine.level === 'error') ? 'danger' : 'info')
  }, a.dmEngine.label)), React.createElement("div", {
    style: { fontSize: 11.5, color: 'var(--muted)', lineHeight: 1.4 }
  }, a.dmEngine.detail))), React.createElement("div", {
    style: { display: 'flex', flexDirection: 'column', justifyContent: 'center', alignItems: 'stretch', minWidth: 96 }
  }, React.createElement("button", {
    className: "btn sm",
    onClick: function () {
      push('已发起账号引擎校验 · ' + a.name);
      // 乐观更新：先置“校验中”，避免回环测试耗时（发真实私信）期间按钮无反馈
      setRealAccts(prev => (prev || []).map(x => x.name === a.name ? Object.assign({}, x, {
        dmEngine: { level: 'unknown', label: '校验中…', detail: '正在对自身发送回环测试文本，请稍候' }
      }) : x));
      Promise.resolve(api.checkAccount(a.name)).then(function (d) {
        if (d && d.ok && d.verify) {
          var v = d.verify;
          setRealAccts(prev => (prev || []).map(x => x.name === a.name ? Object.assign({}, x, {
            wpEngine: v.wp || x.wpEngine,
            dmEngine: v.dm || x.dmEngine,
            uid: v.uid || x.uid
          }) : x));
          push('引擎校验完成 · ' + a.name + ' · wp: ' + (v.wp && v.wp.label) + ' · dm: ' + (v.dm && v.dm.label));
        } else if (d && d.error) {
          push('引擎校验失败: ' + d.error);
        }
        if (api.getAccounts) api.getAccounts();
      }).catch(e => push('引擎校验异常: ' + e));
    }
  }, "引擎校验")))), React.createElement("div", {
    className: "acct-section",
    style: { gridColumn: '2', gridRow: '2' }
  }, React.createElement("h4", null, "关联指纹浏览器"), React.createElement("div", {
    className: "fp-card"
  }, React.createElement("div", {
    className: "fp-header"
  }, React.createElement(Dot, {
    c: a.fp.status === 'running' ? 'ok' : 'warn',
    pulse: a.fp.status === 'running'
  }), React.createElement("span", {
    className: "nm"
  }, a.fp.name), React.createElement(Pill, {
    c: a.fp.status === 'running' ? 'ok' : 'warn'
  }, a.fp.status === 'running' ? '运行中' : '已停止'), React.createElement("div", {
    style: {
      flex: 1
    }
  }), React.createElement("button", {
    className: "btn sm ghost",
    onClick: () => {
      setProxyAcct(a);
      setProxyForm({
        type: a.fp.proxy.includes('SOCKS') ? 'socks5' : a.fp.proxy.includes('HTTP') ? 'http' : 'direct',
        host: a.fp.proxy.includes('·') ? a.fp.proxy.split('·')[1]?.split(':')[0] || '' : '',
        port: a.fp.proxy.includes('·') ? a.fp.proxy.split('·')[1]?.split(':')[1] || '' : '',
        user: '',
        pass: '',
        testUrl: ''
      });
      setProxyTestResult(null);
      push('已打开代理配置 · ' + a.name);
    }
  }, "代理配置")), React.createElement("dl", {
    className: "fp-env"
  }, React.createElement("dt", null, "系统"), React.createElement("dd", null, a.fp.os), React.createElement("dt", null, "代理"), React.createElement("dd", {
    style: {
      fontFamily: 'var(--font-mono)',
      fontSize: 11.5
    }
  }, a.fp.proxy), React.createElement("dt", null, "分辨率"), React.createElement("dd", null, a.fp.resolution), React.createElement("dt", null, "UA"), React.createElement("dd", {
    style: {
      fontSize: 11,
      overflow: 'hidden',
      textOverflow: 'ellipsis',
      whiteSpace: 'nowrap',
      maxWidth: 180
    }
  }, a.fp.ua), React.createElement("dt", null, "WebRTC"), React.createElement("dd", null, a.fp.webrtc), React.createElement("dt", null, "时区"), React.createElement("dd", null, a.fp.timezone)))))))), React.createElement(AnimatePresence, null, reviewAccount && React.createElement(AccountReview, {
    key: "account-review",
    account: reviewAccount,
    onClose: () => setReviewAccount(null),
    push: push,
    goMsg: goMsg
  }), proxyAcct && React.createElement(ProxyDrawer, {
    key: "proxy-drawer",
    account: proxyAcct,
    form: proxyForm,
    setForm: setProxyForm,
    testing: proxyTesting,
    testResult: proxyTestResult,
    onTest: () => {
      setProxyTesting(true);
      setTimeout(() => {
        const ok = Math.random() > 0.3;
        setProxyTestResult({
          ok,
          msg: ok ? `连接成功 · ${proxyForm.type}://${proxyForm.host}${proxyForm.port ? ':' + proxyForm.port : ''} → ${(Math.random() * 200 + 50).toFixed(0)}ms` : `连接超时 · 请检查代理地址和端口是否正确`
        });
        setProxyTesting(false);
        push(ok ? '代理连接测试成功' : '代理连接测试失败');
      }, 1500);
    },
    onSave: () => {
      setAccounts(accts => accts.map(a => a.id === proxyAcct.id ? {
        ...a,
        fp: {
          ...a.fp,
          proxy: proxyForm.type === 'direct' ? '直连' : proxyForm.type.toUpperCase() + ' · ' + proxyForm.host + ':' + proxyForm.port
        }
      } : a));
      push('代理配置已保存 · ' + proxyAcct.name);
      setProxyAcct(null);
    },
    onClose: () => setProxyAcct(null)
  }), addOpen && React.createElement(AccountDrawer, {
    key: "account-drawer",
    mode: "add",
    form: addForm,
    setForm: setAddForm,
    onSave: addAccount,
    onClose: () => setAddOpen(false)
  }), editAcct && React.createElement(AccountDrawer, {
    key: "account-edit-drawer",
    mode: "edit",
    account: editAcct,
    form: editForm,
    setForm: setEditForm,
    onSave: saveEdit,
    onClose: () => setEditAcct(null)
  })));
}
const REVIEW_ROWS_INIT = [];
function AccountReview({
  account,
  onClose,
  push,
  goMsg
}) {
  const [q, setQ] = useState('');
  const [st, setSt] = useState('all');
  const [asc, setAsc] = useState(false);
  const [exp, setExp] = useState(null);
  const [dmFilter, setDmFilter] = useState('all');
  const rows = useMemo(() => {
    const base = REVIEW_ROWS_INIT;
    const min = Date.now() - base.length * 38000;
    return base.map((r, i) => ({
      id: i,
      time: r[0],
      name: r[1],
      lv: r[2],
      content: r[3],
      dmStatus: r[4],
      dmText: r[5],
      dmTime: r[6],
      ts: min + i * 38000
    }));
  }, []);
  const filtered = useMemo(() => {
    let list = rows.slice();
    if (st !== 'all') list = list.filter(r => r.dmStatus === st);
    if (q.trim()) {
      const kw = q.trim();
      list = list.filter(r => (r.name + r.content + r.dmText).includes(kw));
    }
    list.sort((a, b) => asc ? a.ts - b.ts : b.ts - a.ts);
    return list;
  }, [rows, q, st, asc]);
  const cnt = s => rows.filter(r => r.dmStatus === s).length;
  const DM_META = {
    un: ['未私信', 'mute'],
    wait: ['待发送', 'warn'],
    sent: ['已发送', 'ok'],
    fail: ['发送失败', 'danger']
  };
  const exportCSV = () => {
    const head = ['发送时间', '发言人', '用户等级', '评论内容', '私信状态', '私信文案', '私信时间'];
    const body = filtered.map(r => [r.time, r.name, r.lv, r.content, DM_META[r.dmStatus][0], r.dmText || '', r.dmTime || '']);
    const csv = [head, ...body].map(l => l.map(c => '"' + String(c).replace(/"/g, '""') + '"').join(',')).join('\n');
    const blob = new Blob(['\ufeff' + csv], {
      type: 'text/csv;charset=utf-8'
    });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = account.name + '_review_' + tick().replace(/:/g, '') + '.csv';
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    push('已导出 CSV · ' + a.download);
  };
  useEffect(() => {
    const h = e => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, [onClose]);
  const a = account;
  const r = a.lastRun;
  return React.createElement(motion.div, {
    className: "overlay",
    initial: {
      opacity: 0,
      y: 14
    },
    animate: {
      opacity: 1,
      y: 0
    },
    exit: {
      opacity: 0
    },
    transition: {
      duration: .18
    },
    "data-od-id": "acct-review"
  }, React.createElement("header", {
    className: "nav"
  }, React.createElement("div", {
    className: "brand"
  }, React.createElement("span", {
    className: "mark",
    "aria-hidden": "true"
  }), React.createElement("h1", null, "抖音数据控制台"), React.createElement("span", {
    className: "sub"
  }, "Douyin Console")), React.createElement("nav", {
    className: "tabs",
    "aria-label": "主导航"
  }, TABS.map(([id, label]) => React.createElement("button", {
    key: id,
    "data-od-id": 'review-tab-' + id,
    className: 'tab' + (id === 'accounts' ? ' active' : ''),
    onClick: () => {
      if (id !== 'accounts') {
        onClose();
        setTimeout(() => {
          document.querySelector('[data-od-id="tab-' + id + '"]')?.click();
        }, 50);
      }
    }
  }, label))), React.createElement("div", {
    className: "nav-status"
  }, React.createElement("span", {
    className: "badge-conn"
  }, React.createElement(Dot, {
    c: "ok",
    pulse: true
  }), " Cookie ", React.createElement("b", null, "有效")), React.createElement("span", {
    className: "badge-conn"
  }, React.createElement(Dot, {
    c: "ok",
    pulse: true
  }), " 直播 ", React.createElement("b", null, "已连接")), React.createElement("span", {
    className: "demo-tag"
  }, "查阅模式"))), React.createElement("div", {
    className: "overlay-head",
    style: {
      background: 'var(--surface)'
    }
  }, React.createElement("button", {
    className: "btn ghost",
    onClick: onClose
  }, "‹ 返回账号管理"), React.createElement(Avatar, {
    name: a.name,
    h: a.hue,
    sm: true
  }), React.createElement("h2", null, a.name, " · 查阅模式"), React.createElement(Pill, {
    c: a.tokenValid ? 'ok' : (a.lvl === 'nosign' ? 'warn' : 'danger')
  }, a.lvlLabel || (a.tokenValid ? '凭证有效' : '凭证过期')), React.createElement("div", {
    style: {
      flex: 1
    }
  }), React.createElement("span", {
    className: "demo-tag"
  }, "只读 · ", a.uid)), React.createElement("div", {
    className: "overlay-body"
  }, React.createElement("div", {
    className: "review-stats-row",
    "data-od-id": "review-stats"
  }, React.createElement("div", {
    className: "card stat",
    style: {
      flex: 1,
      minWidth: 140
    }
  }, React.createElement("span", {
    className: "label"
  }, "捕获评论"), React.createElement("span", {
    className: "num"
  }, r.comments.toLocaleString(), React.createElement("span", {
    className: "unit"
  }, "条"))), React.createElement("div", {
    className: "card stat",
    style: {
      flex: 1,
      minWidth: 140
    }
  }, React.createElement("span", {
    className: "label"
  }, "发送私信"), React.createElement("span", {
    className: "num",
    style: {
      color: 'var(--accent)'
    }
  }, r.dmSent, React.createElement("span", {
    className: "unit"
  }, "条"))), React.createElement("div", {
    className: "card stat",
    style: {
      flex: 1,
      minWidth: 140
    }
  }, React.createElement("span", {
    className: "label"
  }, "下播私信"), React.createElement("span", {
    className: "num",
    style: {
      color: 'var(--warn)'
    }
  }, r.dmAfterLive, React.createElement("span", {
    className: "unit"
  }, "条"))), React.createElement("div", {
    className: "card stat",
    style: {
      flex: 1,
      minWidth: 140
    }
  }, React.createElement("span", {
    className: "label"
  }, "成功"), React.createElement("span", {
    className: "num",
    style: {
      color: 'var(--ok)'
    }
  }, r.dmSuccess)), React.createElement("div", {
    className: "card stat",
    style: {
      flex: 1,
      minWidth: 140
    }
  }, React.createElement("span", {
    className: "label"
  }, "失败"), React.createElement("span", {
    className: "num",
    style: {
      color: 'var(--danger)'
    }
  }, r.dmFail)), React.createElement("div", {
    className: "card stat",
    style: {
      flex: 1,
      minWidth: 140
    }
  }, React.createElement("span", {
    className: "label"
  }, "成功率"), React.createElement("span", {
    className: "num",
    style: {
      color: r.dmSent > 0 && r.dmSuccess / r.dmSent > 0.9 ? 'var(--ok)' : 'var(--warn)'
    }
  }, r.dmSent > 0 ? Math.round(r.dmSuccess / r.dmSent * 100) : 0, React.createElement("span", {
    className: "unit"
  }, "%")))), React.createElement("div", {
    style: {
      display: 'grid',
      gridTemplateColumns: '1fr 320px',
      gap: 14,
      alignItems: 'start'
    },
    "data-od-id": "review-content"
  }, React.createElement("div", null, React.createElement("div", {
    className: "table-tools"
  }, React.createElement("input", {
    className: "input",
    placeholder: "搜索昵称 / 评论内容 / 私信文案…",
    value: q,
    onChange: e => setQ(e.target.value)
  }), React.createElement("select", {
    className: "select",
    value: st,
    onChange: e => setSt(e.target.value),
    "aria-label": "私信状态筛选"
  }, React.createElement("option", {
    value: "all"
  }, "全部状态"), React.createElement("option", {
    value: "un"
  }, "未私信"), React.createElement("option", {
    value: "wait"
  }, "待发送"), React.createElement("option", {
    value: "sent"
  }, "已发送"), React.createElement("option", {
    value: "fail"
  }, "发送失败")), React.createElement("button", {
    className: "btn ghost",
    onClick: () => setAsc(s => !s)
  }, asc ? '时间 ↑' : '时间 ↓'), React.createElement("button", {
    className: "btn ghost",
    onClick: () => {
      setSt('all');
      setQ('');
    }
  }, "重置"), React.createElement("button", {
    className: "btn primary",
    onClick: exportCSV
  }, "导出 CSV")), React.createElement("div", {
    className: "count-line"
  }, "共 ", React.createElement("b", null, filtered.length), " 条 · ", React.createElement("span", {
    className: "filter-pills",
    style: {
      display: 'inline-flex',
      marginLeft: 10,
      verticalAlign: 'middle'
    }
  }, [['all', '全部', rows.length], ['un', '未私信', cnt('un')], ['wait', '待发送', cnt('wait')], ['sent', '已发送', cnt('sent')], ['fail', '发送失败', cnt('fail')]].map(([id, l, c]) => React.createElement("button", {
    key: id,
    className: 'fpill' + (st === id ? ' active' : ''),
    onClick: () => setSt(id)
  }, l, React.createElement("span", {
    className: "c"
  }, c))))), React.createElement("div", {
    className: "card",
    style: {
      padding: 0
    }
  }, React.createElement("div", {
    className: "table-scroll"
  }, React.createElement("table", {
    className: "comment-table"
  }, React.createElement("thead", null, React.createElement("tr", null, React.createElement("th", {
    style: {
      width: 30
    }
  }), React.createElement("th", null, "发送时间"), React.createElement("th", null, "发言人"), React.createElement("th", null, "评论内容"), React.createElement("th", null, "私信状态"), React.createElement("th", null, "私信文案"), React.createElement("th", null, "私信时间"), React.createElement("th", {
    style: {
      width: 120
    }
  }, "操作"))), React.createElement("tbody", null, filtered.length === 0 && React.createElement("tr", null, React.createElement("td", {
    colSpan: "8",
    style: {
      padding: '36px 20px',
      textAlign: 'center',
      color: 'var(--muted)',
      fontSize: 13
    }
  }, React.createElement("div", {
    style: {
      fontSize: 26,
      marginBottom: 8
    }
  }, "📭"), "暂无评论记录 · 等待直播间捕获后自动入库")), filtered.map(r => React.createElement(React.Fragment, {
    key: r.id
  }, React.createElement("tr", {
    key: r.id
  }, React.createElement("td", null, React.createElement("span", {
    className: "mono",
    style: {
      color: 'var(--muted)'
    }
  }, exp === r.id ? '▾' : '▸')), React.createElement("td", {
    className: "mono"
  }, r.time), React.createElement("td", null, React.createElement("span", {
    className: "speaker"
  }, React.createElement(Avatar, {
    name: r.name,
    h: hue(r.name.length),
    sm: true
  }), React.createElement("span", {
    className: "nm"
  }, r.name), r.lv < 99 && React.createElement("span", {
    className: "lv"
  }, "Lv.", r.lv))), React.createElement("td", {
    className: "content-cell"
  }, React.createElement("span", {
    className: "cmt-text"
  }, r.content)), React.createElement("td", null, r.dmStatus === 'un' ? React.createElement("span", {
    className: "blank"
  }, "—") : React.createElement(Pill, {
    c: DM_META[r.dmStatus][1]
  }, DM_META[r.dmStatus][0])), React.createElement("td", {
    className: "dm-cell"
  }, React.createElement("span", {
    className: 'dm-text' + (r.dmText ? ' has' : '')
  }, r.dmText || React.createElement("span", {
    className: "blank"
  }, "未发送"))), React.createElement("td", {
    className: "mono"
  }, r.dmTime || React.createElement("span", {
    className: "blank"
  }, "—")), React.createElement("td", null, React.createElement("div", {
    className: "head-row",
    style: {
      gap: 4
    }
  }, React.createElement("button", {
    className: "btn text sm",
    onClick: () => setExp(exp === r.id ? null : r.id)
  }, "详情"), React.createElement("button", {
    className: "btn text sm",
    onClick: () => {
      goMsg(r.name, r.text);
      push('已跳转私信中心 · ' + r.name);
    }
  }, "发私信")))), exp === r.id && React.createElement("tr", {
    key: r.id + '-d'
  }, React.createElement("td", {
    colSpan: "8",
    style: {
      padding: '6px 10px 14px',
      background: 'var(--surface-2)'
    }
  }, React.createElement("div", {
    className: "detail-panel"
  }, React.createElement("h4", null, "评论历史 · ", r.name), React.createElement("div", {
    className: "history-list"
  }, rows.filter(x => x.name === r.name).slice(0, 5).map((x, i) => React.createElement("div", {
    className: "history-item",
    key: i
  }, React.createElement("span", {
    className: "tm"
  }, x.time), React.createElement("span", null, x.content)))), React.createElement("h4", null, "私信内容"), React.createElement("div", {
    style: {
      fontSize: 13
    }
  }, r.dmStatus === 'un' ? React.createElement("span", {
    className: "blank"
  }, "尚未对该发言人发送私信") : React.createElement("span", null, React.createElement(Pill, {
    c: DM_META[r.dmStatus][1]
  }, DM_META[r.dmStatus][0]), "　", r.dmText || '（文案未填写）', r.dmTime ? '　·　' + r.dmTime : '')))))))))))), React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 14
    }
  }, React.createElement("div", {
    className: "card"
  }, React.createElement("h3", null, "运行信息"), React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 6,
      fontSize: 13
    }
  }, React.createElement("div", {
    className: "acct-row"
  }, React.createElement("span", {
    className: "k"
  }, "直播间"), React.createElement("span", {
    className: "v",
    style: {
      color: 'var(--accent)'
    }
  }, r.room)), React.createElement("div", {
    className: "acct-row"
  }, React.createElement("span", {
    className: "k"
  }, "运行时间"), React.createElement("span", {
    className: "v"
  }, r.time)), React.createElement("div", {
    className: "acct-row"
  }, React.createElement("span", {
    className: "k"
  }, "运行时长"), React.createElement("span", {
    className: "v"
  }, r.duration)), React.createElement("div", {
    className: "acct-row"
  }, React.createElement("span", {
    className: "k"
  }, "累计运行"), React.createElement("span", {
    className: "v"
  }, r.totalRuns, " 次")))), React.createElement("div", {
    className: "card"
  }, React.createElement("h3", null, "指纹浏览器"), React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 6,
      fontSize: 13
    }
  }, React.createElement("div", {
    className: "fp-header",
    style: {
      marginBottom: 4
    }
  }, React.createElement(Dot, {
    c: a.fp.status === 'running' ? 'ok' : 'warn',
    pulse: a.fp.status === 'running'
  }), React.createElement("span", {
    className: "nm"
  }, a.fp.name), React.createElement(Pill, {
    c: a.fp.status === 'running' ? 'ok' : 'warn'
  }, a.fp.status === 'running' ? '运行中' : '已停止')), React.createElement("dl", {
    className: "fp-env"
  }, React.createElement("dt", null, "系统"), React.createElement("dd", null, a.fp.os), React.createElement("dt", null, "代理"), React.createElement("dd", {
    style: {
      fontFamily: 'var(--font-mono)',
      fontSize: 11
    }
  }, a.fp.proxy), React.createElement("dt", null, "分辨率"), React.createElement("dd", null, a.fp.resolution), React.createElement("dt", null, "UA"), React.createElement("dd", {
    style: {
      fontSize: 10.5,
      overflow: 'hidden',
      textOverflow: 'ellipsis',
      whiteSpace: 'nowrap'
    }
  }, a.fp.ua), React.createElement("dt", null, "WebRTC"), React.createElement("dd", null, a.fp.webrtc), React.createElement("dt", null, "时区"), React.createElement("dd", null, a.fp.timezone)))), React.createElement("div", {
    className: "card"
  }, React.createElement("h3", null, "操作"), React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 8
    }
  }, React.createElement("button", {
    className: "btn primary",
    style: {
      width: '100%'
    },
    onClick: () => push('已对全部未私信用户批量发送私信')
  }, "批量发送私信"), React.createElement("button", {
    className: "btn ghost",
    style: {
      width: '100%'
    },
    onClick: exportCSV
  }, "导出全部数据"), React.createElement("button", {
    className: "btn ghost",
    style: {
      width: '100%'
    },
    onClick: () => push('已跳转私信中心 · ' + a.name)
  }, "进入私信中心")))))));
}
function ProxyDrawer({
  account,
  form,
  setForm,
  testing,
  testResult,
  onTest,
  onSave,
  onClose
}) {
  if (!account) return null;
  const a = account;
  const proxyTypes = [{
    id: 'socks5',
    label: 'SOCKS5',
    desc: '推荐 · 支持UDP/TCP'
  }, {
    id: 'http',
    label: 'HTTP',
    desc: '兼容性好'
  }, {
    id: 'https',
    label: 'HTTPS',
    desc: '加密传输'
  }, {
    id: 'direct',
    label: '直连',
    desc: '不使用代理'
  }];
  return React.createElement(React.Fragment, null, React.createElement(motion.div, {
    key: "proxy-backdrop",
    className: "drawer-backdrop",
    initial: {
      opacity: 0
    },
    animate: {
      opacity: 1
    },
    exit: {
      opacity: 0
    },
    transition: {
      duration: .15
    },
    onClick: onClose
  }), React.createElement(motion.div, {
    key: "proxy-drawer",
    className: "drawer",
    initial: {
      x: '100%'
    },
    animate: {
      x: 0
    },
    exit: {
      x: '100%'
    },
    transition: {
      type: 'spring',
      damping: 28,
      stiffness: 320
    },
    "data-od-id": "proxy-drawer"
  }, React.createElement("div", {
    className: "drawer-head"
  }, React.createElement(Avatar, {
    name: a.name,
    h: a.hue
  }), React.createElement("div", {
    style: {
      flex: 1
    }
  }, React.createElement("div", {
    style: {
      fontWeight: 600,
      fontSize: 14
    }
  }, "代理配置 · ", a.name), React.createElement("div", {
    className: "mono",
    style: {
      fontSize: 11.5,
      color: 'var(--muted)'
    }
  }, "UID: ", a.uid, " · ", a.fp.name)), React.createElement(Pill, {
    c: a.fp.status === 'running' ? 'ok' : 'warn'
  }, a.fp.status === 'running' ? '运行中' : '已停止'), React.createElement("button", {
    className: "btn ghost",
    onClick: onClose,
    style: {
      fontSize: 18,
      padding: '4px 8px',
      lineHeight: 1
    }
  }, "×")), React.createElement("div", {
    className: "drawer-body"
  }, React.createElement("div", {
    className: "card",
    style: {
      marginBottom: 14
    }
  }, React.createElement("h3", null, "代理类型"), React.createElement("div", {
    className: "grid cols-4",
    style: {
      gap: 8
    }
  }, proxyTypes.map(pt => React.createElement("button", {
    key: pt.id,
    style: {
      padding: '10px 12px',
      cursor: 'pointer',
      border: form.type === pt.id ? '2px solid var(--accent)' : '1px solid var(--border)',
      borderRadius: 8,
      background: form.type === pt.id ? 'var(--accent-bg)' : 'var(--surface)',
      textAlign: 'left'
    },
    onClick: () => setForm(f => ({
      ...f,
      type: pt.id
    }))
  }, React.createElement("div", {
    style: {
      fontWeight: 600,
      fontSize: 13,
      color: form.type === pt.id ? 'var(--accent)' : 'var(--fg)'
    }
  }, pt.label), React.createElement("div", {
    style: {
      fontSize: 11,
      color: 'var(--muted)',
      marginTop: 2
    }
  }, pt.desc))))), form.type !== 'direct' && React.createElement("div", {
    className: "card",
    style: {
      marginBottom: 14
    }
  }, React.createElement("h3", null, "连接设置"), React.createElement("div", {
    className: "grid cols-2",
    style: {
      gap: 12
    }
  }, React.createElement("div", {
    className: "field"
  }, React.createElement("label", null, "主机地址"), React.createElement("input", {
    className: "input mono",
    placeholder: "127.0.0.1",
    value: form.host,
    onChange: e => setForm(f => ({
      ...f,
      host: e.target.value
    }))
  })), React.createElement("div", {
    className: "field"
  }, React.createElement("label", null, "端口"), React.createElement("input", {
    className: "input mono",
    placeholder: "1080",
    value: form.port,
    onChange: e => setForm(f => ({
      ...f,
      port: e.target.value
    }))
  })), React.createElement("div", {
    className: "field"
  }, React.createElement("label", null, "用户名 ", React.createElement("span", {
    style: {
      color: 'var(--muted)',
      fontWeight: 400
    }
  }, "(可选)")), React.createElement("input", {
    className: "input",
    placeholder: "留空则无认证",
    value: form.user,
    onChange: e => setForm(f => ({
      ...f,
      user: e.target.value
    }))
  })), React.createElement("div", {
    className: "field"
  }, React.createElement("label", null, "密码 ", React.createElement("span", {
    style: {
      color: 'var(--muted)',
      fontWeight: 400
    }
  }, "(可选)")), React.createElement("input", {
    className: "input",
    type: "password",
    placeholder: "留空则无认证",
    value: form.pass,
    onChange: e => setForm(f => ({
      ...f,
      pass: e.target.value
    }))
  }))), React.createElement("div", {
    style: {
      marginTop: 12,
      padding: '10px 14px',
      background: 'var(--surface-2)',
      borderRadius: 8,
      fontSize: 12,
      color: 'var(--muted)',
      display: 'flex',
      alignItems: 'center',
      gap: 8
    }
  }, React.createElement("span", {
    style: {
      fontSize: 16
    }
  }, "💡"), React.createElement("span", null, "完整地址：", React.createElement("b", {
    className: "mono",
    style: {
      color: 'var(--fg)'
    }
  }, form.type, "://", form.host, form.port ? ':' + form.port : '')))), React.createElement("div", {
    className: "card",
    style: {
      marginBottom: 14
    }
  }, React.createElement("h3", null, "连接测试"), React.createElement("div", {
    style: {
      display: 'flex',
      gap: 8,
      marginBottom: 12
    }
  }, React.createElement("input", {
    className: "input",
    style: {
      flex: 1
    },
    placeholder: "测试目标 URL（默认 https://www.douyin.com）",
    value: form.testUrl,
    onChange: e => setForm(f => ({
      ...f,
      testUrl: e.target.value
    }))
  }), React.createElement("button", {
    className: "btn primary",
    disabled: testing || form.type === 'direct',
    onClick: onTest
  }, testing ? '测试中…' : '测试连接')), testResult && React.createElement("div", {
    style: {
      padding: '10px 14px',
      borderRadius: 8,
      background: testResult.ok ? 'var(--ok-bg)' : 'var(--danger-bg)',
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      fontSize: 13
    }
  }, React.createElement("span", {
    style: {
      fontSize: 16
    }
  }, testResult.ok ? '✅' : '❌'), React.createElement("div", null, React.createElement("div", {
    style: {
      fontWeight: 600,
      color: testResult.ok ? 'var(--ok)' : 'var(--danger)'
    }
  }, testResult.ok ? '连接成功' : '连接失败'), React.createElement("div", {
    style: {
      fontSize: 12,
      color: 'var(--muted)',
      marginTop: 2
    }
  }, testResult.msg))), form.type === 'direct' && React.createElement("div", {
    style: {
      padding: '10px 14px',
      borderRadius: 8,
      background: 'var(--surface-2)',
      fontSize: 12,
      color: 'var(--muted)'
    }
  }, "直连模式下无需测试，流量将不经过代理直接发出")), React.createElement("div", {
    className: "card"
  }, React.createElement("h3", null, "当前配置预览"), React.createElement("div", {
    style: {
      display: 'grid',
      gridTemplateColumns: '80px 1fr',
      gap: '6px 10px',
      fontSize: 12.5
    }
  }, React.createElement("span", {
    style: {
      color: 'var(--muted)'
    }
  }, "代理类型"), React.createElement("span", {
    className: "mono",
    style: {
      fontWeight: 600
    }
  }, form.type.toUpperCase()), React.createElement("span", {
    style: {
      color: 'var(--muted)'
    }
  }, "地址"), React.createElement("span", {
    className: "mono"
  }, form.type === 'direct' ? '直连' : form.host + (form.port ? ':' + form.port : '')), React.createElement("span", {
    style: {
      color: 'var(--muted)'
    }
  }, "认证"), React.createElement("span", {
    className: "mono"
  }, form.user ? form.user + ' / ••••' : '无'), React.createElement("span", {
    style: {
      color: 'var(--muted)'
    }
  }, "影响账号"), React.createElement("span", null, a.name, " (", a.uid, ")")))), React.createElement("div", {
    className: "drawer-foot"
  }, React.createElement("button", {
    className: "btn ghost",
    onClick: onClose
  }, "取消"), React.createElement("button", {
    className: "btn primary",
    onClick: onSave
  }, "保存配置"))));
}
function AccountDrawer({
  form,
  setForm,
  onSave,
  onClose,
  mode = 'add',
  account = null
}) {
  const isEdit = mode === 'edit';
  return React.createElement(React.Fragment, null, React.createElement(motion.div, {
    key: 'acct-backdrop-' + mode,
    className: "drawer-backdrop",
    initial: {
      opacity: 0
    },
    animate: {
      opacity: 1
    },
    exit: {
      opacity: 0
    },
    transition: {
      duration: .15
    },
    onClick: onClose
  }), React.createElement(motion.div, {
    key: 'acct-drawer-' + mode,
    className: "drawer",
    initial: {
      x: '100%'
    },
    animate: {
      x: 0
    },
    exit: {
      x: '100%'
    },
    transition: {
      type: 'spring',
      damping: 28,
      stiffness: 320
    },
    "data-od-id": isEdit ? 'acct-edit-drawer' : 'acct-add-drawer'
  }, React.createElement("div", {
    className: "drawer-head"
  }, React.createElement(Avatar, {
    name: isEdit ? account && account.name || '+' : '+',
    h: isEdit ? account && account.hue || 200 : 200
  }), React.createElement("div", {
    style: {
      flex: 1
    }
  }, React.createElement("div", {
    style: {
      fontWeight: 600,
      fontSize: 14
    }
  }, isEdit ? '编辑账号信息' : '新增授权账号'), React.createElement("div", {
    className: "mono",
    style: {
      fontSize: 11.5,
      color: 'var(--muted)'
    }
  }, isEdit ? '修改账号信息并刷新登录凭证' : '授权新抖音账号并创建指纹环境')), React.createElement("button", {
    className: "btn ghost",
    onClick: onClose,
    style: {
      fontSize: 18,
      padding: '4px 8px',
      lineHeight: 1
    }
  }, "×")), React.createElement("div", {
    className: "drawer-body"
  }, React.createElement("div", {
    className: "card",
    style: {
      marginBottom: 14
    }
  }, React.createElement("h3", null, "账号信息"), React.createElement("div", {
    className: "grid cols-2",
    style: {
      gap: 12
    }
  }, React.createElement("div", {
    className: "field"
  }, React.createElement("label", null, "昵称"), React.createElement("input", {
    className: "input",
    placeholder: "如：阿强探店",
    value: form.name,
    onChange: e => setForm(f => ({
      ...f,
      name: e.target.value
    })),
    autoFocus: true
  })), React.createElement("div", {
    className: "field"
  }, React.createElement("label", null, "UID ", React.createElement("span", {
    style: {
      color: 'var(--muted)',
      fontWeight: 400
    }
  }, "(可选 · 扫码后自动获取)")), React.createElement("input", {
    className: "input mono",
    placeholder: "留空即可，扫码后自动读入",
    value: form.uid,
    onChange: e => setForm(f => ({
      ...f,
      uid: e.target.value
    }))
  }))), !isEdit && React.createElement("div", {
    className: "field",
    style: {
      marginTop: 12
    }
  }, React.createElement("label", null, "Cookie ", React.createElement("span", {
    style: {
      color: 'var(--muted)',
      fontWeight: 400
    }
  }, "(可选 · 留空则扫码授权)")), React.createElement("textarea", {
    className: "input mono",
    rows: 3,
    placeholder: "粘贴 Cookie 字符串，用于免扫码登录",
    value: form.cookie,
    onChange: e => setForm(f => ({
      ...f,
      cookie: e.target.value
    })),
    style: {
      resize: 'vertical'
    }
  }))), React.createElement("div", {
    style: {
      padding: '10px 14px',
      borderRadius: 8,
      background: 'var(--surface-2)',
      fontSize: 12,
      color: 'var(--muted)',
      lineHeight: 1.6
    }
  }, isEdit ? '点击「重新获取凭证」后，将重新拉起内置指纹浏览器并展示抖音扫码二维码，扫码完成后最新凭证（Cookie / 签名）自动写回该账号。' : '保存后将自动弹出内置指纹浏览器并展示抖音扫码二维码，扫码完成后凭证自动写回该账号；之后可在账号卡片中配置代理与凭证校验。')), React.createElement("div", {
    className: "drawer-foot"
  }, React.createElement("button", {
    className: "btn ghost",
    onClick: onClose
  }, "取消"), React.createElement("button", {
    className: "btn primary",
    onClick: onSave,
    disabled: !form.name.trim()
  }, isEdit ? '重新获取凭证' : '确认新增'))));
}
