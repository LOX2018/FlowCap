// pages/live.js —— 直播监听页（含 DM_META/seedRows/ReviewMode 子组件，数据走接口）
const DM_META = {
  un: ['未私信', 'mute'],
  wait: ['待发送', 'warn'],
  sent: ['已发送', 'ok'],
  fail: ['发送失败', 'danger']
};
function seedRows() {
  return [];
}
function Live({
  push,
  goMsg,
  api,
  ready
}) {
  const [viewMode, setViewMode] = useState('single');
  const [activeAcct, setActiveAcct] = useState(null);
  const [room, setRoom] = useState('');
  const [listening, setListening] = useState(false);
  const [reconnect, setReconnect] = useState(false);
  const [feed, setFeed] = useState([]);
  const [rows, setRows] = useState(seedRows);
  const [heat, setHeat] = useState([]);
  const [review, setReview] = useState(false);
  const [dmDraft, setDmDraft] = useState('');
  const [roomLikes, setRoomLikes] = useState(0);
  const [myLikes, setMyLikes] = useState(0);
  const [burst, setBurst] = useState(0);
  const likeT = useRef();
  const [batchN, setBatchN] = useState('10');
  const [dmLimit, setDmLimit] = useState('3');
  const [dmInterval, setDmInterval] = useState('60.0');
  const [dmJitter, setDmJitter] = useState('50,120');
  const [dmTemplates, setDmTemplates] = useState([{
    text: '你好，欢迎留言咨询唐律师工伤，留个方式，唐律下播后帮你分析',
    enabled: true
  }, {
    text: '老乡 看到你在唐律师直播间咨询工伤问题，我是他的助理，你可以留个📞方式，我们帮你看下等级和赔偿 [握手]',
    enabled: true
  }, {
    text: '唐律还在直播，我是助理，可以留个联系方式，唐律下播后帮你分析',
    enabled: true
  }]);
  const [tasksCfg, setTasksCfg] = useState(null);
  const [forceRescan, setForceRescan] = useState(false);
  const [realAccts, setRealAccts] = useState([]);
  const [liveStream, setLiveStream] = useState(null);
  const streamRef = useRef(null);
  const feedRef = useRef(null);
  const heatRef = useRef(null);
  const likesRef = useRef(0);
  const curAcct = realAccts.find(a => a.name === activeAcct) || realAccts[0] || null;
  const running = !!(liveStream && liveStream.running);
  useEffect(() => {
    if (!ready) return;
    let alive = true;
    const load = () => {
      api.getTasks().then(t => {
        if (alive && t && t.ok) setTasksCfg(t);
      }).catch(() => {});
      api.getStats().then(d => {
        if (!alive || !d || !d.ok) return;
        const realRows = d.list.map((r, i) => ({
          id: 200000 + i,
          time: (r.captureTs || '').slice(-8) || '—',
          name: r.nickname || '未知',
          lv: 0,
          content: r.comment || '',
          dmStatus: r.status === '已发送' ? 'sent' : r.status === '已捕获' ? 'wait' : r.status === '发送失败' ? 'fail' : 'un',
          dmText: r.content || '',
          dmTime: r.sendTs || '',
          ts: Date.now() - i * 1000
        }));
        if (realRows.length) setRows(realRows);
      }).catch(() => {});
    };
    load();
    const iv = setInterval(load, 2500);
    return () => {
      alive = false;
      clearInterval(iv);
    };
  }, [api, ready]);
  useEffect(() => {
    if (!ready || !tasksCfg) return;
    if (tasksCfg.liveUrl) setRoom(tasksCfg.liveUrl);
    if (tasksCfg.maxTarget) setDmLimit(String(tasksCfg.maxTarget));
    if (tasksCfg.interval) setDmInterval(String(tasksCfg.interval));
    if (tasksCfg.delay) setDmJitter(String(tasksCfg.delay));
    if (typeof tasksCfg.forceRescan === 'boolean') setForceRescan(tasksCfg.forceRescan);
    if (tasksCfg.dmPool && tasksCfg.dmPool.length) setDmTemplates(tasksCfg.dmPool.map(p => ({
      text: p.text,
      enabled: p.enabled
    })));
  }, [ready, tasksCfg]);
  useEffect(() => {
    const h = e => {
      if (e.key === 'Escape') setReview(false);
    };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, []);
  useEffect(() => {
    if (!ready) return;
    let alive = true;
    const loadAccts = () => {
      api.getAccounts().then(d => {
        if (alive && d && d.ok && d.accounts && d.accounts.length) setRealAccts(d.accounts);
      }).catch(() => {});
    };
    const loadStream = () => {
      api.getLiveStream().then(d => {
        if (!alive || !d) return;
        const prev = streamRef.current || {};
        if (d.running !== prev.running || d.listening !== prev.listening || d.online !== prev.online || d.likes !== prev.likes || d.totalUser !== prev.totalUser || d.roomDisplay !== prev.roomDisplay || d.liveId !== prev.liveId) {
          streamRef.current = d;
          setLiveStream(d);
        }
        const rl = d.likes || 0;
        if (rl !== likesRef.current) {
          likesRef.current = rl;
          setRoomLikes(rl);
        }
        setReconnect(Boolean(d.listening && !d.running));
        const h = d.heat ? d.heat.map(p => p[1]) : null;
        if (h && h.length) {
          const ph = heatRef.current;
          if (!ph || ph.length !== h.length || h[h.length - 1] !== ph[ph.length - 1]) {
            heatRef.current = h;
            setHeat(h);
          }
        } else if (heatRef.current) {
          heatRef.current = null;
          setHeat([]);
        }
        if (d.feed && d.feed.length) {
          const fe = d.feed.map(f => f.epoch || 0);
          const pf = feedRef.current;
          if (!pf || pf.length !== fe.length || fe[0] !== pf[0] || fe[fe.length - 1] !== pf[pf.length - 1]) {
            feedRef.current = fe;
            setFeed(d.feed.map(f => ({
              id: f.epoch || 400000 + Math.random(),
              t: f.ts || '—',
              k: f.type || 'danmaku',
              n: f.nickname || '未知',
              l: 0,
              x: f.content || ''
            })));
          }
        } else if (feedRef.current) {
          feedRef.current = null;
          setFeed([]);
        }
      }).catch(() => {});
    };
    loadAccts();
    loadStream();
    const iv1 = setInterval(loadAccts, 5000);
    const iv2 = setInterval(loadStream, 2000);
    return () => {
      alive = false;
      clearInterval(iv1);
      clearInterval(iv2);
    };
  }, [api, ready]);
  const heatChart = (data, w = 640, h = 120) => {
    if (!data || !data.length) return React.createElement("div", {
      style: {
        height: h,
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        color: 'var(--muted)',
        fontSize: 12
      }
    }, "\u7B49\u5F85\u623F\u95F4\u70ED\u5EA6\u6570\u636E \xB7 \u5F15\u64CE\u8FD0\u884C\u540E\u81EA\u52A8\u751F\u6210");
    const max = Math.max(...data) * 1.18;
    const x = i => i / (data.length - 1) * w;
    const y = v => h - 10 - v / max * (h - 16);
    const d = data.map((v, i) => (i ? 'L' : 'M') + x(i).toFixed(1) + ' ' + y(v).toFixed(1)).join('');
    return React.createElement("svg", {
      width: w,
      height: h,
      viewBox: '0 0 ' + w + ' ' + h,
      style: {
        width: '100%',
        height: 'auto'
      },
      role: "img",
      "aria-label": "\u623F\u95F4\u70ED\u5EA6\u66F2\u7EBF"
    }, [0.25, 0.5, 0.75].map(g => React.createElement("line", {
      key: g,
      className: "chart-grid",
      x1: "0",
      x2: w,
      y1: h * g,
      y2: h * g
    })), React.createElement("path", {
      className: "chart-area",
      d: d + ' L' + w + ' ' + h + ' L0 ' + h + ' Z'
    }), React.createElement("path", {
      className: "chart-line",
      d: d
    }), React.createElement("circle", {
      className: "chart-last",
      cx: x(data.length - 1),
      cy: y(data[data.length - 1]),
      r: "3.4"
    }));
  };
  const sendDanmaku = () => {
    const text = dmDraft.trim();
    if (!text) return;
    if (ready && window.ApiBridge && window.ApiBridge.ready) {
      api.sendDanmaku(text).then(r => push(r && r.ok ? '弹幕已发送 · ' + text : '发送失败: ' + (r && r.error || ''))).catch(e => push('发送异常: ' + e));
    } else {
      push('未连接后端，无法发送弹幕');
    }
    setDmDraft('');
  };
  const doLike = () => {
    if (!(ready && window.ApiBridge && window.ApiBridge.ready)) {
      push('未连接后端，无法点赞');
      return;
    }
    api.doLike(1).then(r => {
      if (r && r.ok) {
        setMyLikes(m => m + 1);
        setBurst(b => b + 1);
      } else push('点赞失败: ' + (r && r.error || ''));
    }).catch(e => push('点赞异常: ' + e));
    clearTimeout(likeT.current);
    likeT.current = setTimeout(() => setBurst(0), 900);
  };
  const doBatch = () => {
    const n = Math.min(1000, Math.max(1, parseInt(batchN, 10) || 1));
    if (!(ready && window.ApiBridge && window.ApiBridge.ready)) {
      push('未连接后端，无法批量点赞');
      return;
    }
    api.doLike(n).then(r => {
      if (r && r.ok) {
        setMyLikes(m => m + n);
        setBurst(b => b + n);
      } else push('点赞失败: ' + (r && r.error || ''));
    }).catch(e => push('点赞异常: ' + e));
    clearTimeout(likeT.current);
    likeT.current = setTimeout(() => setBurst(0), 1600);
  };
  const dedup = useMemo(() => new Set(rows.map(r => r.name)).size, [rows]);
  const dedupCount = useMemo(() => new Set(rows.map(r => r.name + '||' + r.content)).size, [rows]);
  const waitCount = useMemo(() => rows.filter(r => r.dmStatus === 'wait').length, [rows]);
  const sentCount = useMemo(() => rows.filter(r => r.dmStatus === 'sent').length, [rows]);
  const sendDm = r => {
    if (ready && window.ApiBridge && window.ApiBridge.ready) {
      api.requestDm(r.name, r.content).then(res => push(res && res.ok ? (res.msg || '已加入发送队列') + ' · ' + r.name : '加入失败: ' + (res && res.error || ''))).catch(e => push('异常: ' + e));
      return;
    }
    const t = tick();
    setRows(rs => rs.map(x => x.id === r.id ? {
      ...x,
      dmStatus: 'sent',
      dmText: r.dmText || '（离线模拟）',
      dmTime: t
    } : x));
    push('私信已发送 → ' + r.name);
  };
  return React.createElement("div", null, React.createElement("div", {
    className: "section-head"
  }, React.createElement("div", null, React.createElement("h2", null, "\u76F4\u64AD\u76D1\u542C"), React.createElement("div", {
    className: "desc"
  }, "\u5B9E\u65F6\u5F39\u5E55 / \u793C\u7269 / \u8BC4\u8BBA\u91C7\u96C6\u4E0E\u79C1\u4FE1\u81EA\u52A8\u5316")), React.createElement("div", {
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
    c: liveStream && liveStream.running ? 'ok' : 'warn',
    pulse: liveStream && liveStream.running
  }), " ", liveStream ? liveStream.listening ? '直播引擎监听中' : liveStream.running ? '直播引擎运行中' : '直播引擎未运行' : '未连接'), React.createElement("span", {
    className: "demo-tag"
  }, ready ? liveStream && liveStream.running ? '实时数据' : '等待运行' : '未连接')), React.createElement("span", {
    className: "badge-conn",
    style: { marginLeft: 8 }
  }, React.createElement(Dot, {
    c: liveStream && liveStream.dmRunning ? 'ok' : 'warn',
    pulse: liveStream && liveStream.dmRunning
  }), " ", liveStream ? liveStream.dmRunning ? (liveStream.dmPaused ? '私信引擎已暂停' : '私信引擎发送中') : '私信引擎待命' : '未连接')), viewMode === 'grid' ? React.createElement("div", {
    className: "grid cols-2",
    "data-od-id": "live-grid"
  }, realAccts.slice(0, 2).map(acct => React.createElement("div", {
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
    h: hue(acct.name.length)
  }), React.createElement("div", {
    style: {
      flex: 1
    }
  }, React.createElement("div", {
    style: {
      fontWeight: 600,
      fontSize: 14
    }
  }, acct.name), React.createElement("div", {
    className: "mono",
    style: {
      fontSize: 11.5,
      color: 'var(--muted)'
    }
  }, "UID: ", acct.uid || '—')), React.createElement(Pill, {
    c: acct.loggedIn ? 'ok' : 'danger'
  }, acct.loggedIn ? '在线' : '离线')), React.createElement("div", {
    style: {
      padding: '10px 16px',
      borderBottom: '1px solid var(--border)'
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
  }, "\u76F4\u64AD\u95F4"), React.createElement("span", {
    className: "mono",
    style: {
      fontSize: 12,
      fontWeight: 600
    }
  }, liveStream && (liveStream.roomTitle || liveStream.liveUrl) || '未解析')), React.createElement("div", {
    className: "head-row"
  }, React.createElement("span", {
    style: {
      fontSize: 12,
      color: 'var(--muted)'
    }
  }, "\u5728\u7EBF\u4EBA\u6570"), React.createElement("span", {
    className: "mono",
    style: {
      fontSize: 12
    }
  }, liveStream ? liveStream.online.toLocaleString() : '—'))), React.createElement("div", {
    className: "grid cols-2",
    style: {
      padding: '10px 16px',
      gap: 8
    }
  }, React.createElement("div", {
    className: "card stat",
    style: {
      padding: '8px 10px'
    }
  }, React.createElement("span", {
    className: "label",
    style: {
      fontSize: 11
    }
  }, "\u5F39\u5E55"), React.createElement("span", {
    className: "num",
    style: {
      fontSize: 18
    }
  }, rows.length.toLocaleString())), React.createElement("div", {
    className: "card stat",
    style: {
      padding: '8px 10px'
    }
  }, React.createElement("span", {
    className: "label",
    style: {
      fontSize: 11
    }
  }, "\u5DF2\u79C1\u4FE1"), React.createElement("span", {
    className: "num",
    style: {
      fontSize: 18
    }
  }, rows.filter(r => r.dmStatus === 'sent').length))), React.createElement("div", {
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
      fontWeight: 600
    }
  }, "\u623F\u95F4\u70ED\u5EA6"), React.createElement("span", {
    className: "mono",
    style: {
      fontSize: 12,
      color: 'var(--accent)'
    }
  }, liveStream ? liveStream.online.toLocaleString() : 0, " \u4EBA")), React.createElement("div", {
    style: {
      height: 60
    }
  }, heatChart(heat.length ? heat : [0], 400, 60))), React.createElement("div", {
    style: {
      padding: '10px 16px',
      borderTop: '1px solid var(--border)',
      maxHeight: 120,
      overflow: 'auto'
    }
  }, feed.slice(0, 5).map(f => React.createElement("div", {
    className: "feed-item",
    key: f.id,
    style: {
      padding: '3px 0'
    }
  }, React.createElement("span", {
    className: "tm",
    style: {
      fontSize: 10
    }
  }, f.t), React.createElement("span", {
    className: 'kind k-' + f.k,
    style: {
      fontSize: 10
    }
  }, KIND_NAME[f.k] || f.k), React.createElement("span", {
    className: "txt",
    style: {
      fontSize: 11
    }
  }, React.createElement("b", null, f.n), " ", f.x))), feed.length === 0 && React.createElement("div", {
    style: {
      padding: '10px 0',
      color: 'var(--muted)',
      fontSize: 11,
      textAlign: 'center'
    }
  }, "\u6682\u65E0\u5B9E\u65F6\u4FE1\u606F")), React.createElement("div", {
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
  }, "\u8FDB\u5165\u76D1\u542C"), React.createElement("button", {
    className: "btn sm ghost",
    onClick: () => push('已导出 ' + acct.name + ' 数据')
  }, "\u5BFC\u51FA")))), realAccts.length === 0 && React.createElement("div", {
    className: "card",
    style: {
      padding: 40,
      textAlign: 'center',
      color: 'var(--muted)'
    }
  }, React.createElement("div", {
    style: {
      fontSize: 26,
      marginBottom: 8
    }
  }, "\uD83D\uDC65"), "\u6682\u65E0\u5DF2\u6388\u6743\u8D26\u53F7 \xB7 \u8BF7\u5230\u300C\u8D26\u53F7\u7BA1\u7406\u300D\u6DFB\u52A0\u5E76\u5B8C\u6210\u626B\u7801")) : React.createElement(React.Fragment, null, React.createElement("div", {
    className: "card",
    style: {
      marginBottom: 14
    },
    "data-od-id": "live-acct-select"
  }, React.createElement("div", {
    className: "head-row"
  }, React.createElement("span", {
    style: {
      fontSize: 13,
      fontWeight: 600
    }
  }, "\u5F53\u524D\u76D1\u542C\u8D26\u53F7"), React.createElement("div", {
    style: {
      flex: 1
    }
  }), React.createElement("div", {
    className: "seg"
  }, realAccts.map(a => React.createElement("button", {
    key: a.name,
    className: activeAcct === a.name ? 'active' : '',
    onClick: () => {
      setActiveAcct(a.name);
      push('已切换到 ' + a.name);
    }
  }, React.createElement(Avatar, {
    name: a.name,
    h: hue(a.name.length),
    sm: true
  }), " ", a.name)), realAccts.length === 0 && React.createElement("span", {
    className: "mono",
    style: {
      fontSize: 12,
      color: 'var(--muted)'
    }
  }, "\u65E0\u5DF2\u6388\u6743\u8D26\u53F7")))), React.createElement("div", {
    className: "card",
    style: {
      marginBottom: 14
    },
    "data-od-id": "live-input"
  }, React.createElement("div", {
    className: "searchbar",
    style: {
      marginBottom: 0
    }
  }, React.createElement("input", {
    className: "input",
    style: {
      flex: 1,
      fontFamily: 'var(--font-mono)'
    },
    value: room,
    onChange: e => setRoom(e.target.value),
    placeholder: "\u76F4\u64AD\u95F4 URL \u6216 room_id",
    "aria-label": "\u76F4\u64AD\u95F4\u5730\u5740"
  }), React.createElement("button", {
    className: "btn ghost",
    "data-od-id": "live-parse",
    onClick: () => {
      const u = room.trim();
      if (!u) return;
      if (ready && window.ApiBridge && window.ApiBridge.ready) {
        api.resolveLive(u).then(r => push(r && r.ok ? '已解析房间号 · ' + r.liveId : '解析失败: ' + (r && r.error || ''))).catch(e => push('解析异常: ' + e));
      } else push('未连接后端，无法解析');
    }
  }, "\u89E3\u6790\u623F\u95F4\u53F7"), ready && tasksCfg && tasksCfg.liveUrl && room !== tasksCfg.liveUrl && React.createElement("button", {
    className: "btn ghost",
    onClick: () => setRoom(tasksCfg.liveUrl)
  }, "\u586B\u5165\u5DF2\u914D\u7F6E"), !ready && React.createElement("button", {
    className: "btn primary",
    "data-od-id": "live-start",
    onClick: () => {
      setListening(s => !s);
      push(listening ? '已停止监听' : '开始监听 ' + room);
    }
  }, listening ? '停止监听' : '开始监听'), ready && React.createElement(React.Fragment, null, React.createElement("button", {
    className: "btn primary",
    "data-od-id": "live-start",
    disabled: running,
    onClick: () => {
      const cfg = {
        liveUrl: room,
        maxTarget: parseInt(dmLimit, 10) || 9999,
        interval: parseFloat(dmInterval) || 60,
        delay: dmJitter,
        dmPool: dmTemplates.filter(t => t.text && t.text.trim()).map(t => ({
          text: t.text.trim(),
          enabled: t.enabled
        })),
        enableSend: enableSend,
        forceRescan: forceRescan
      };
      api.start(cfg).then(r => push(r && r.ok ? '引擎已启动 · ' + room : '启动失败: ' + (r && r.error || ''))).catch(e => push('启动异常: ' + e));
    }
  }, "\u5F00\u59CB\u81EA\u52A8\u79C1\u4FE1"), React.createElement("button", {
    className: "btn ghost",
    "data-od-id": "live-pause",
    disabled: !running,
    onClick: () => api.pause().then(r => push(r && r.ok ? '已暂停' : '暂停失败')).catch(e => push('暂停异常: ' + e))
  }, "\u6682\u505C"), React.createElement("button", {
    className: "btn ghost",
    "data-od-id": "live-resume",
    disabled: !running,
    onClick: () => api.resume().then(r => push(r && r.ok ? '已继续' : '继续失败')).catch(e => push('继续异常: ' + e))
  }, "\u7EE7\u7EED"), React.createElement("button", {
    className: "btn ghost danger",
    "data-od-id": "live-stop",
    disabled: !running,
    onClick: () => api.stop().then(r => push(r && r.ok ? '已停止' : '停止失败')).catch(e => push('停止异常: ' + e))
  }, "\u505C\u6B62"))), React.createElement("div", {
    style: {
      fontSize: 11.5,
      color: 'var(--muted)',
      marginTop: 6
    }
  }, ready ? running ? '引擎运行中（真实监听）' : '引擎未运行 · 配置后点「开始自动私信」' : '粘贴直播页/分享短链/用户主页链接，自动识别')), React.createElement("div", {
    className: "card",
    style: {
      marginBottom: 14
    },
    "data-od-id": "live-auto-dm"
  }, React.createElement("h3", null, "\u81EA\u52A8\u79C1\u4FE1\u914D\u7F6E"), React.createElement("div", {
    className: "grid cols-3",
    style: {
      marginBottom: 14
    }
  }, React.createElement("div", {
    className: "field"
  }, React.createElement("label", null, "\u53D1\u9001\u4E0A\u9650"), React.createElement("input", {
    className: "input",
    type: "number",
    min: "1",
    max: "100",
    value: dmLimit,
    onChange: e => setDmLimit(e.target.value),
    style: {
      width: '100%',
      fontFamily: 'var(--font-mono)'
    },
    "aria-label": "\u6BCF\u573A\u6700\u591A\u53D1\u9001\u79C1\u4FE1\u6761\u6570"
  }), React.createElement("span", {
    className: "hint"
  }, "\u6BCF\u573A\u76F4\u64AD\u6700\u591A\u53D1\u9001\u79C1\u4FE1\u6761\u6570")), React.createElement("div", {
    className: "field"
  }, React.createElement("label", null, "\u95F4\u9694\uFF08\u79D2\uFF09"), React.createElement("input", {
    className: "input",
    type: "number",
    min: "1",
    step: "0.1",
    value: dmInterval,
    onChange: e => setDmInterval(e.target.value),
    style: {
      width: '100%',
      fontFamily: 'var(--font-mono)'
    },
    "aria-label": "\u4E24\u6761\u79C1\u4FE1\u4E4B\u95F4\u7684\u95F4\u9694\u79D2\u6570"
  }), React.createElement("span", {
    className: "hint"
  }, "\u4E24\u6761\u79C1\u4FE1\u4E4B\u95F4\u7684\u7B49\u5F85\u65F6\u95F4")), React.createElement("div", {
    className: "field"
  }, React.createElement("label", null, "\u5EF6\u8FDF\u6296\u52A8\uFF08\u79D2\uFF09"), React.createElement("input", {
    className: "input",
    value: dmJitter,
    onChange: e => setDmJitter(e.target.value),
    style: {
      width: '100%',
      fontFamily: 'var(--font-mono)'
    },
    "aria-label": "\u5EF6\u8FDF\u6296\u52A8\u533A\u95F4"
  }), React.createElement("span", {
    className: "hint"
  }, "\u683C\u5F0F\uFF1A50,120 = \u968F\u673A\u533A\u95F4\uFF1B60 = \u56FA\u5B9A\u5EF6\u8FDF"))), React.createElement("div", null, React.createElement("div", {
    className: "head-row",
    style: {
      marginBottom: 10
    }
  }, React.createElement("h3", {
    style: {
      marginBottom: 0
    }
  }, "\u79C1\u4FE1\u8BCD\u5E93"), React.createElement("span", {
    style: {
      fontSize: 12,
      color: 'var(--muted)'
    }
  }, "\u6BCF\u884C\u4E00\u6761\uFF0C\u52FE\u9009 = \u542F\u7528\uFF0C\u53D1\u9001\u65F6\u968F\u673A\u62BD\u5DF2\u542F\u7528\u7684\u4E00\u6761")), React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 8
    },
    "data-od-id": "dm-templates"
  }, dmTemplates.map((t, i) => React.createElement("div", {
    key: i,
    className: "head-row",
    style: {
      gap: 10
    }
  }, React.createElement("span", {
    className: "switch",
    style: {
      flex: 'none'
    }
  }, React.createElement("input", {
    type: "checkbox",
    checked: t.enabled,
    onChange: e => {
      const next = [...dmTemplates];
      next[i] = {
        ...next[i],
        enabled: e.target.checked
      };
      setDmTemplates(next);
    },
    "aria-label": '启用模板 ' + (i + 1)
  }), React.createElement("i", null)), React.createElement("input", {
    className: "input",
    style: {
      flex: 1
    },
    value: t.text,
    onChange: e => {
      const next = [...dmTemplates];
      next[i] = {
        ...next[i],
        text: e.target.value
      };
      setDmTemplates(next);
    },
    placeholder: "\u8F93\u5165\u79C1\u4FE1\u6587\u6848\u2026"
  })))), React.createElement("div", {
    className: "head-row",
    style: {
      marginTop: 10,
      gap: 8
    }
  }, React.createElement("button", {
    className: "btn sm ghost",
    onClick: () => setDmTemplates(dmTemplates.map(t => ({
      ...t,
      enabled: true
    })))
  }, "\u5168\u9009"), React.createElement("button", {
    className: "btn sm ghost",
    onClick: () => setDmTemplates(dmTemplates.map(t => ({
      ...t,
      enabled: false
    })))
  }, "\u5168\u4E0D\u9009"), React.createElement("button", {
    className: "btn sm ghost",
    onClick: () => setDmTemplates([...dmTemplates, {
      text: '',
      enabled: true
    }])
  }, "\u6DFB\u52A0\u4E00\u6761"), React.createElement("button", {
    className: "btn sm ghost",
    onClick: () => setDmTemplates(dmTemplates.filter(t => t.enabled))
  }, "\u5220\u9664\u9009\u4E2D"), React.createElement("span", {
    className: "mono",
    style: {
      fontSize: 11.5,
      color: 'var(--muted)'
    }
  }, "\u5DF2\u542F\u7528 ", dmTemplates.filter(t => t.enabled).length, " / ", dmTemplates.length, " \u6761"), ready && React.createElement(React.Fragment, null, React.createElement("button", {
    className: "btn sm primary",
    onClick: () => api.saveDmPool(dmTemplates).then(r => push(r && r.ok ? '词库已保存 · ' + (r.count || 0) + ' 条' : '保存失败: ' + (r && r.error || ''))).catch(e => push('保存异常: ' + e))
  }, "\u4FDD\u5B58\u8BCD\u5E93"), React.createElement("button", {
    className: "btn sm ghost",
    onClick: () => api.saveConfig({
      liveUrl: room,
      maxTarget: parseInt(dmLimit, 10),
      interval: parseFloat(dmInterval),
      delay: dmJitter,
      dmPool: dmTemplates
    }).then(r => push(r && r.ok ? '配置已写回 config.py' : '写回失败')).catch(e => push('写回异常: ' + e))
  }, "\u4FDD\u5B58\u914D\u7F6E"))))), React.createElement("div", {
    className: "live-layout",
    style: {
      marginBottom: 14
    }
  }, React.createElement("div", {
    className: "card",
    "data-od-id": "live-feed"
  }, React.createElement("h3", null, React.createElement("span", null, "\u5B9E\u65F6\u4FE1\u606F\u6D41 ", React.createElement("span", {
    className: "mono",
    style: {
      color: 'var(--accent)',
      fontWeight: 400
    }
  }, KIND_NAME.danmaku, " / ", KIND_NAME.gift, " / ", KIND_NAME.enter)), React.createElement("span", {
    className: "mono like-total"
  }, "\u2665 ", roomLikes.toLocaleString())), React.createElement("div", {
    className: "feed",
    style: {
      maxHeight: 236
    }
  }, feed.map(f => React.createElement("div", {
    className: "feed-item",
    key: f.id
  }, React.createElement("span", {
    className: "tm"
  }, f.t), React.createElement("span", {
    className: 'kind k-' + f.k
  }, KIND_NAME[f.k] || f.k), React.createElement("span", {
    className: "txt"
  }, React.createElement("b", null, f.n), "\u3000", f.x), f.l > 0 && React.createElement("span", {
    className: "lv"
  }, "Lv.", f.l))), feed.length === 0 && React.createElement("div", {
    style: {
      padding: '28px 12px',
      textAlign: 'center',
      color: 'var(--muted)',
      fontSize: 12
    }
  }, "\u6682\u65E0\u5B9E\u65F6\u4FE1\u606F \xB7 \u5F15\u64CE\u8FD0\u884C\u540E\u81EA\u52A8\u5C55\u793A\u5F39\u5E55 / \u793C\u7269 / \u8FDB\u573A / \u70B9\u8D5E / \u5173\u6CE8")), React.createElement("div", {
    className: "head-row",
    style: {
      marginTop: 12,
      paddingTop: 12,
      borderTop: '1px solid var(--border)'
    }
  }, React.createElement("input", {
    className: "input",
    style: {
      flex: 1
    },
    placeholder: "\u53D1\u9001\u5F39\u5E55\u5230\u76F4\u64AD\u95F4\u2026",
    value: dmDraft,
    onChange: e => setDmDraft(e.target.value),
    onKeyDown: e => e.key === 'Enter' && sendDanmaku()
  }), React.createElement("button", {
    className: "btn ghost",
    "data-od-id": "live-send-danmaku",
    onClick: sendDanmaku
  }, "\u53D1\u9001"), React.createElement("button", {
    className: "btn ghost like-btn",
    "data-od-id": "live-like",
    onClick: doLike
  }, "\u2665 \u70B9\u8D5E", myLikes > 0 && React.createElement("span", {
    className: "like-cnt"
  }, "\xD7", myLikes), burst > 0 && React.createElement("span", {
    className: "burst",
    key: myLikes
  }, "+", burst))), React.createElement("div", {
    className: "head-row batch-row",
    "data-od-id": "live-batch-like"
  }, React.createElement("span", {
    className: "mono",
    style: {
      fontSize: 12,
      color: 'var(--muted)'
    }
  }, "\u6279\u91CF\u70B9\u8D5E"), React.createElement("input", {
    className: "input batch-input",
    type: "number",
    min: "1",
    max: "1000",
    "aria-label": "\u6279\u91CF\u70B9\u8D5E\u6570\u91CF",
    value: batchN,
    onChange: e => setBatchN(e.target.value),
    onKeyDown: e => e.key === 'Enter' && doBatch()
  }), React.createElement("button", {
    className: "btn ghost",
    onClick: doBatch
  }, "\u6279\u91CF\u70B9\u8D5E"), React.createElement("span", {
    className: "mono",
    style: {
      fontSize: 11.5,
      color: 'var(--muted)'
    }
  }, "\u6BCF\u6B21\u6309\u8F93\u5165\u6570\u91CF\u6267\u884C\uFF0C\u4E0A\u9650 1000"))), React.createElement("div", {
    className: "card",
    "data-od-id": "heat-chart"
  }, React.createElement("h3", null, "\u623F\u95F4\u70ED\u5EA6 ", React.createElement("span", {
    className: "mono",
    style: {
      color: 'var(--accent)',
      fontWeight: 400
    }
  }, heat.length ? heat[heat.length - 1].toLocaleString() : liveStream && liveStream.online ? liveStream.online.toLocaleString() : 0, " \u4EBA\u5728\u7EBF")), React.createElement("div", {
    className: "chart-wrap"
  }, heatChart(heat)))), React.createElement("div", {
    className: "card",
    "data-od-id": "comment-stats"
  }, React.createElement("div", {
    className: "table-tools"
  }, React.createElement("div", {
    style: {
      flex: 1,
      minWidth: 240
    }
  }, React.createElement("h3", {
    style: {
      marginBottom: 0
    }
  }, "\u5B9E\u65F6\u8BC4\u8BBA\u7EDF\u8BA1\u5217\u8868")), React.createElement("button", {
    className: "btn ghost",
    "data-od-id": "review-open",
    onClick: () => setReview(true)
  }, "\u8FDB\u5165\u67E5\u9605\u6A21\u5F0F")), React.createElement("div", {
    className: "count-line"
  }, "\u5171 ", React.createElement("b", null, rows.length), " \u6761\u5F39\u5E55\u8BB0\u5F55 \xB7 \u53BB\u91CD ", React.createElement("b", null, dedupCount), " \u6761 \xB7 \u5B9E\u9645\u53D1\u8A00 ", React.createElement("b", null, dedup), " \u4EBA \xB7 \u5F85\u53D1\u9001\u79C1\u4FE1 ", React.createElement("b", null, waitCount), " \u6761 \xB7 \u5DF2\u53D1\u9001\u79C1\u4FE1 ", React.createElement("b", null, sentCount), " \u6761"), React.createElement("div", {
    className: "table-scroll"
  }, React.createElement("table", {
    className: "comment-table"
  }, React.createElement("thead", null, React.createElement("tr", null, React.createElement("th", null, "\u53D1\u9001\u65F6\u95F4"), React.createElement("th", null, "\u53D1\u8A00\u4EBA"), React.createElement("th", null, "\u8BC4\u8BBA\u5185\u5BB9"), React.createElement("th", null, "\u79C1\u4FE1\u72B6\u6001"), React.createElement("th", null, "\u79C1\u4FE1\u6587\u6848"), React.createElement("th", null, "\u79C1\u4FE1\u65F6\u95F4"))), React.createElement("tbody", null, rows.length === 0 && React.createElement("tr", null, React.createElement("td", {
    colSpan: "6",
    style: {
      padding: '30px 12px',
      textAlign: 'center',
      color: 'var(--muted)',
      fontSize: 13
    }
  }, React.createElement("div", {
    style: {
      fontSize: 24,
      marginBottom: 6
    }
  }, "\uD83D\uDCED"), "\u6682\u65E0\u8BC4\u8BBA\u8BB0\u5F55 \xB7 \u5F15\u64CE\u8FD0\u884C\u540E\u81EA\u52A8\u6355\u83B7")), rows.slice(0, 12).map(r => React.createElement("tr", {
    key: r.id
  }, React.createElement("td", {
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
    className: "content-cell",
    title: r.content
  }, React.createElement("span", {
    className: "cmt-text"
  }, r.content)), React.createElement("td", null, r.dmStatus === 'un' ? React.createElement("span", {
    className: "blank"
  }, "\u2014") : React.createElement(Pill, {
    c: DM_META[r.dmStatus][1]
  }, DM_META[r.dmStatus][0])), React.createElement("td", {
    className: "dm-cell"
  }, React.createElement("span", {
    className: 'dm-text' + (r.dmText ? ' has' : ''),
    title: r.dmText
  }, r.dmText || React.createElement("span", {
    className: "blank"
  }, "\u672A\u53D1\u9001"))), React.createElement("td", {
    className: "mono"
  }, r.dmTime || React.createElement("span", {
    className: "blank"
  }, "\u2014"))))))))), React.createElement(AnimatePresence, null, review && React.createElement(ReviewMode, {
    key: "review-mode",
    rows: rows,
    onClose: () => setReview(false),
    push: push,
    sendDm: sendDm,
    goMsg: goMsg
  })));
}
function ReviewMode({
  rows,
  onClose,
  push,
  sendDm,
  goMsg
}) {
  const [q, setQ] = useState('');
  const [st, setSt] = useState('all');
  const [asc, setAsc] = useState(false);
  const [exp, setExp] = useState(null);
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
  const exportCSV = () => {
    const head = ['发送时间', '发言人', '用户等级', '评论内容', '私信状态', '私信文案', '私信时间'];
    const body = filtered.map(r => [r.time, r.name, r.lv, r.content, DM_META[r.dmStatus][0], r.dmText || '', r.dmTime || '']);
    const csv = [head, ...body].map(l => l.map(c => '"' + String(c).replace(/"/g, '""') + '"').join(',')).join('\n');
    const blob = new Blob(['\ufeff' + csv], {
      type: 'text/csv;charset=utf-8'
    });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([JSON.stringify(exportData, null, 2)], {
      type: 'application/json'
    }));
    a.download = 'live_comments_' + tick().replace(/:/g, '') + '.csv';
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
    push('已导出 CSV · ' + a.download);
  };
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
    "data-od-id": "live-review"
  }, React.createElement("div", {
    className: "overlay-head"
  }, React.createElement("button", {
    className: "btn ghost",
    "data-od-id": "review-back",
    onClick: onClose
  }, "\u2039 \u8FD4\u56DE\u5B9E\u65F6\u6D41"), React.createElement("h2", null, "\u8BC4\u8BBA\u67E5\u9605\u6A21\u5F0F"), React.createElement("div", {
    style: {
      flex: 1
    }
  }), React.createElement("span", {
    className: "demo-tag"
  }, "\u53EA\u8BFB \xB7 \u5B9E\u65F6\u5165\u5E93")), React.createElement("div", {
    className: "overlay-body"
  }, React.createElement("div", {
    className: "table-tools"
  }, React.createElement("input", {
    className: "input",
    placeholder: "\u641C\u7D22\u6635\u79F0 / \u8BC4\u8BBA\u5185\u5BB9 / \u79C1\u4FE1\u6587\u6848\u2026",
    value: q,
    onChange: e => setQ(e.target.value)
  }), React.createElement("select", {
    className: "select",
    value: st,
    onChange: e => setSt(e.target.value),
    "aria-label": "\u79C1\u4FE1\u72B6\u6001\u7B5B\u9009"
  }, React.createElement("option", {
    value: "all"
  }, "\u5168\u90E8\u72B6\u6001"), React.createElement("option", {
    value: "un"
  }, "\u672A\u79C1\u4FE1"), React.createElement("option", {
    value: "wait"
  }, "\u5F85\u53D1\u9001"), React.createElement("option", {
    value: "sent"
  }, "\u5DF2\u53D1\u9001"), React.createElement("option", {
    value: "fail"
  }, "\u53D1\u9001\u5931\u8D25")), React.createElement("button", {
    className: "btn ghost",
    onClick: () => setAsc(s => !s)
  }, asc ? '时间 ↑' : '时间 ↓'), React.createElement("button", {
    className: "btn ghost",
    onClick: () => {
      setSt('all');
      setQ('');
    }
  }, "\u91CD\u7F6E"), React.createElement("button", {
    className: "btn primary",
    "data-od-id": "review-export",
    onClick: exportCSV
  }, "\u5BFC\u51FA CSV")), React.createElement("div", {
    className: "count-line"
  }, "\u5171 ", React.createElement("b", null, filtered.length), " \u6761 \xB7 ", React.createElement("span", {
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
  }), React.createElement("th", null, "\u53D1\u9001\u65F6\u95F4"), React.createElement("th", null, "\u53D1\u8A00\u4EBA"), React.createElement("th", null, "\u8BC4\u8BBA\u5185\u5BB9"), React.createElement("th", null, "\u79C1\u4FE1\u72B6\u6001"), React.createElement("th", null, "\u79C1\u4FE1\u6587\u6848"), React.createElement("th", null, "\u79C1\u4FE1\u65F6\u95F4"), React.createElement("th", {
    style: {
      width: 150
    }
  }, "\u64CD\u4F5C"))), React.createElement("tbody", null, filtered.map(r => React.createElement(React.Fragment, {
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
  }, "\u2014") : React.createElement(Pill, {
    c: DM_META[r.dmStatus][1]
  }, DM_META[r.dmStatus][0])), React.createElement("td", {
    className: "dm-cell"
  }, React.createElement("span", {
    className: 'dm-text' + (r.dmText ? ' has' : '')
  }, r.dmText || React.createElement("span", {
    className: "blank"
  }, "\u672A\u53D1\u9001"))), React.createElement("td", {
    className: "mono"
  }, r.dmTime || React.createElement("span", {
    className: "blank"
  }, "\u2014")), React.createElement("td", null, React.createElement("div", {
    className: "head-row",
    style: {
      gap: 6
    }
  }, React.createElement("button", {
    className: "btn text sm",
    onClick: () => {
      setExp(exp === r.id ? null : r.id);
    }
  }, "\u8BE6\u60C5"), React.createElement("button", {
    className: "btn text sm",
    onClick: () => sendDm(r)
  }, "\u53D1\u79C1\u4FE1"), React.createElement("button", {
    className: "btn text sm",
    onClick: () => goMsg(r.name, r.dmText || '')
  }, "\u53BB\u79C1\u4FE1\u4E2D\u5FC3")))), exp === r.id && React.createElement("tr", {
    key: r.id + '-d'
  }, React.createElement("td", {
    colSpan: "8",
    style: {
      padding: '6px 10px 14px',
      background: 'var(--surface-2)'
    }
  }, React.createElement("div", {
    className: "detail-panel"
  }, React.createElement("h4", null, "\u53D1\u8A00\u5386\u53F2 \xB7 ", r.name), React.createElement("div", {
    className: "history-list"
  }, rows.filter(x => x.name === r.name).slice(0, 5).map((x, i) => React.createElement("div", {
    className: "history-item",
    key: i
  }, React.createElement("span", {
    className: "tm"
  }, x.time), React.createElement("span", null, x.content)))), React.createElement("h4", null, "\u79C1\u4FE1\u5185\u5BB9"), React.createElement("div", {
    style: {
      fontSize: 13
    }
  }, r.dmStatus === 'un' ? React.createElement("span", {
    className: "blank"
  }, "\u5C1A\u672A\u5BF9\u8BE5\u53D1\u8A00\u4EBA\u53D1\u9001\u79C1\u4FE1") : React.createElement("span", null, React.createElement(Pill, {
    c: DM_META[r.dmStatus][1]
  }, DM_META[r.dmStatus][0]), "\u3000", r.dmText || '（文案未填写）', r.dmTime ? '　·　' + r.dmTime : '')))))))))))));
}
