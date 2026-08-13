// app.js —— 根组件 App + 挂载入口
function App() {
  const [tab, setTabState] = useState(() => {
    try {
      return localStorage.getItem('dy:tab') || 'overview';
    } catch (_) {
      return 'overview';
    }
  });
  const [toasts, setToasts] = useState([]);
  const [goDm, setGoDm] = useState(null);
  const [overview, setOverview] = useState(null);
  const [ready, setReady] = useState(false);
  const setTab = t => {
    setTabState(t);
    try {
      localStorage.setItem('dy:tab', t);
    } catch (_) {}
  };
  const push = useCallback(msg => {
    const id = ++cid;
    setToasts(ts => [...ts.slice(-2), {
      id,
      msg
    }]);
    setTimeout(() => setToasts(ts => ts.filter(x => x.id !== id)), 2600);
  }, []);
  const goMsg = useCallback((name, text) => {
    setGoDm({
      name,
      text: text || ''
    });
    setTab('msg');
  }, []);
  const api = useMemo(() => ({
    ready: () => window.ApiBridge && window.ApiBridge.ready,
    getOverview: () => window.ApiBridge.getOverview ? window.ApiBridge.getOverview() : Promise.resolve(null),
    getStats: () => window.ApiBridge.getStats ? window.ApiBridge.getStats() : Promise.resolve(null),
    getConversations: a => window.ApiBridge.getConversations ? window.ApiBridge.getConversations(a) : Promise.resolve(null),
    getConversation: (a, c) => window.ApiBridge.getConversation ? window.ApiBridge.getConversation(a, c) : Promise.resolve(null),
    getAccounts: () => window.ApiBridge.getAccounts ? window.ApiBridge.getAccounts() : Promise.resolve(null),
    checkAccount: n => window.ApiBridge.checkAccount ? window.ApiBridge.checkAccount(n) : Promise.resolve({
      ok: false,
      error: '未连接后端'
    }),
    getTasks: () => window.ApiBridge.getTasks ? window.ApiBridge.getTasks() : Promise.resolve(null),
    getLiveStream: () => window.ApiBridge.getLiveStream ? window.ApiBridge.getLiveStream() : Promise.resolve(null),
    sendDanmaku: c => window.ApiBridge.sendDanmaku ? window.ApiBridge.sendDanmaku(c) : Promise.resolve({
      ok: false,
      error: '未连接后端'
    }),
    doLike: n => window.ApiBridge.doLike ? window.ApiBridge.doLike(n) : Promise.resolve({
      ok: false,
      error: '未连接后端'
    }),
    requestDm: (nm, c) => window.ApiBridge.requestDm ? window.ApiBridge.requestDm(nm, c) : Promise.resolve({
      ok: false,
      error: '未连接后端'
    }),
    resolveLive: u => window.ApiBridge.resolveLive ? window.ApiBridge.resolveLive(u) : Promise.resolve({
      ok: false,
      error: '未连接后端'
    }),
    search: (m, q, n, s, p) => window.ApiBridge.search ? window.ApiBridge.search(m, q, n, s, p) : Promise.resolve({
      ok: false,
      error: '未连接后端'
    }),
    diggVideo: id => window.ApiBridge.diggVideo ? window.ApiBridge.diggVideo(id) : Promise.resolve({
      ok: false,
      error: '未连接后端'
    }),
    favoriteVideo: id => window.ApiBridge.favoriteVideo ? window.ApiBridge.favoriteVideo(id) : Promise.resolve({
      ok: false,
      error: '未连接后端'
    }),
    start: cfg => window.ApiBridge.start ? window.ApiBridge.start(cfg) : Promise.resolve({
      ok: false
    }),
    pause: () => window.ApiBridge.pause ? window.ApiBridge.pause() : Promise.resolve({
      ok: false
    }),
    resume: () => window.ApiBridge.resume ? window.ApiBridge.resume() : Promise.resolve({
      ok: false
    }),
    stop: () => window.ApiBridge.stop ? window.ApiBridge.stop() : Promise.resolve({
      ok: false
    }),
    setMaxTarget: n => window.ApiBridge.setMaxTarget ? window.ApiBridge.setMaxTarget(n) : Promise.resolve({
      ok: false
    }),
    saveConfig: cfg => window.ApiBridge.saveConfig ? window.ApiBridge.saveConfig(cfg) : Promise.resolve({
      ok: false
    }),
    saveDmPool: rows => window.ApiBridge.saveDmPool ? window.ApiBridge.saveDmPool(rows) : Promise.resolve({
      ok: false
    }),
    sendDm: (a, c, t) => window.ApiBridge.sendDm ? window.ApiBridge.sendDm(a, c, t) : Promise.resolve({
      ok: false
    }),
    exportStats: () => window.ApiBridge.exportStats ? window.ApiBridge.exportStats() : Promise.resolve({
      ok: false
    }),
    setCurrent: n => window.ApiBridge.setCurrent ? window.ApiBridge.setCurrent(n) : Promise.resolve({
      ok: false
    }),
    setRoles: (m, s) => window.ApiBridge.setRoles ? window.ApiBridge.setRoles(m, s) : Promise.resolve({
      ok: false
    }),
    addAccount: n => window.ApiBridge.addAccount ? window.ApiBridge.addAccount(n) : Promise.resolve({
      ok: false
    }),
    scanLogin: n => window.ApiBridge.scanLogin ? window.ApiBridge.scanLogin(n) : Promise.resolve({
      ok: false,
      error: '未连接后端'
    }),
    removeAccount: n => window.ApiBridge.removeAccount ? window.ApiBridge.removeAccount(n) : Promise.resolve({
      ok: false
    }),
    startBrowserDaemon: a => window.ApiBridge.startBrowserDaemon ? window.ApiBridge.startBrowserDaemon(a) : Promise.resolve({
      ok: false
    }),
    stopBrowserDaemon: a => window.ApiBridge.stopBrowserDaemon ? window.ApiBridge.stopBrowserDaemon(a) : Promise.resolve({
      ok: false
    }),
    startRecvDaemon: a => window.ApiBridge.startRecvDaemon ? window.ApiBridge.startRecvDaemon(a) : Promise.resolve({
      ok: false
    }),
    stopRecvDaemon: a => window.ApiBridge.stopRecvDaemon ? window.ApiBridge.stopRecvDaemon(a) : Promise.resolve({
      ok: false
    }),
    refreshBrowserDaemon: a => window.ApiBridge.refreshBrowserDaemon ? window.ApiBridge.refreshBrowserDaemon(a) : Promise.resolve({
      ok: false
    })
  }), []);
  useEffect(() => {
    let alive = true;
    const tick = () => {
      (window.ApiBridge && window.ApiBridge.ready ? api.getOverview() : Promise.resolve(null)).then(d => {
        if (!alive) return;
        setReady(!!(window.ApiBridge && window.ApiBridge.ready));
        if (d) setOverview(d);
      }).catch(() => {});
    };
    tick();
    const iv = setInterval(tick, 3000);
    return () => {
      alive = false;
      clearInterval(iv);
    };
  }, [api]);
  const T = tab;
  return React.createElement("div", {
    className: "app"
  }, React.createElement(Header, {
    tab: T,
    setTab: setTab,
    overview: overview,
    ready: ready
  }), React.createElement("main", {
    className: "main"
  }, React.createElement(AnimatePresence, {
    mode: "wait"
  }, React.createElement(motion.div, {
    key: T,
    initial: {
      opacity: 0,
      y: 8
    },
    animate: {
      opacity: 1,
      y: 0
    },
    exit: {
      opacity: 0
    },
    transition: {
      duration: .16
    }
  }, T === 'overview' && React.createElement(Overview, {
    push: push,
    api: api,
    overview: overview,
    ready: ready
  }), T === 'crawl' && React.createElement(Crawl, {
    push: push,
    goMsg: goMsg,
    api: api,
    overview: overview
  }), T === 'live' && React.createElement(Live, {
    push: push,
    goMsg: goMsg,
    api: api,
    ready: ready
  }), T === 'msg' && React.createElement(Messages, {
    push: push,
    goDm: goDm,
    api: api
  }), T === 'tasks' && React.createElement(Tasks, {
    push: push,
    api: api,
    overview: overview
  }), T === 'accounts' && React.createElement(Accounts, {
    push: push,
    goMsg: goMsg,
    api: api
  }), T === 'settings' && React.createElement(Settings, {
    push: push,
    api: api
  })))), React.createElement("div", {
    className: "toasts"
  }, toasts.map(t => React.createElement("div", {
    className: "toast",
    key: t.id
  }, t.msg))));
}
ReactDOM.createRoot(document.getElementById('root')).render(React.createElement(App, null));
