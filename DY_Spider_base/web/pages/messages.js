// pages/messages.js —— 私信中心页（含 MSG_STYLE/MsgBubble 子组件，数据走接口）
const MSG_STYLE = {
  sticker: ['oklch(70% 0.13 300)', '✨'],
  image: ['oklch(45% 0.13 210)', '图片'],
  video: ['oklch(42% 0.13 150)', '分享的视频']
};
function MsgBubble({
  m,
  h
}) {
  if (m.type === 'text') return React.createElement("div", {
    className: "bubble"
  }, m.text);
  if (m.type === 'voice') return React.createElement("div", {
    className: "bubble"
  }, React.createElement("div", {
    className: "voice"
  }, React.createElement("span", {
    className: "voice-bars"
  }, [10, 18, 14, 22, 12, 20, 8, 16].map((hgt, i) => React.createElement("i", {
    key: i,
    style: {
      height: hgt
    }
  }))), React.createElement("span", {
    className: "vdur"
  }, m.dur)));
  if (m.type === 'sticker') return React.createElement("div", {
    className: "sticker",
    style: {
      background: 'oklch(70% 0.13 300)'
    }
  }, "✨");
  if (m.type === 'image') return React.createElement("div", {
    className: "imgtile",
    style: {
      background: 'linear-gradient(135deg, oklch(48% 0.13 210), oklch(26% 0.09 250))'
    }
  }, "图片消息");
  return React.createElement("div", {
    className: "bubble"
  }, React.createElement("div", {
    className: "vshare"
  }, React.createElement("div", {
    className: "thumb",
    style: {
      background: 'linear-gradient(135deg, oklch(46% 0.13 150), oklch(26% 0.09 190))'
    }
  }, React.createElement("span", {
    className: "play",
    "aria-hidden": "true"
  })), React.createElement("div", null, React.createElement("div", {
    className: "ti"
  }, m.title), React.createElement("div", {
    className: "st"
  }, "分享的视频"))));
}
function Messages({
  push,
  goDm,
  api
}) {
  const [activeAcct, setActiveAcct] = useState(null);
  const [convs, setConvs] = useState([]);
  const [active, setActive] = useState('');
  const [draft, setDraft] = useState('');
  const [newBanner, setNewBanner] = useState(false);
  const [showNew, setShowNew] = useState(false);
  const [newName, setNewName] = useState('');
  const [realConvs, setRealConvs] = useState(null);
  const [realAccts, setRealAccts] = useState([]);
  const bannerT = useRef();
  const curAcct = realAccts.find(a => a.name === activeAcct) || realAccts[0] || null;
  const acctConvs = convs.filter(c => c.acct === activeAcct);
  useEffect(() => {
    if (!window.ApiBridge || !window.ApiBridge.ready) return;
    let alive = true;
    const load = () => api.getConversations(activeAcct).then(d => {
      if (!alive || !d || !d.ok) return;
      const list = d.conversations || [];
      if (list.length) {
        const mapped = list.map((c, i) => ({
          id: 'rc' + i,
          conv_id: c.conv_id,
          acct: activeAcct,
          hue: hue((c.name || 'x').length * 2),
          name: c.name || '会话' + i,
          unread: c.unread || 0,
          msgs: (c.messages || []).map((m, j) => ({
            id: 'rm' + i + '_' + j,
            dir: m.dir || 'in',
            type: m.type || 'text',
            text: m.text || '',
            mt: m.time || nowHM()
          }))
        }));
        setRealConvs(mapped);
      }
    }).catch(() => {});
    load();
    const iv = setInterval(load, 3000);
    return () => {
      alive = false;
      clearInterval(iv);
    };
  }, [api, activeAcct]);
  useEffect(() => {
    if (!window.ApiBridge || !window.ApiBridge.ready) return;
    let alive = true;
    const load = () => api.getAccounts().then(d => {
      if (!alive || !d || !d.ok || !d.accounts || !d.accounts.length) return;
      setRealAccts(d.accounts);
      setActiveAcct(p => p || d.accounts[0].name);
    }).catch(() => {});
    load();
    const iv = setInterval(load, 5000);
    return () => {
      alive = false;
      clearInterval(iv);
    };
  }, [api]);
  const shownConvs = realConvs || acctConvs;
  useEffect(() => {
    if (!goDm) return;
    const {
      name,
      text
    } = goDm;
    const existing = convs.find(c => c.name === name);
    if (existing) {
      setActiveAcct(existing.acct);
      setActive(existing.id);
      setDraft(text || '');
    } else {
      const nc = {
        id: 'c' + (convs.length + 1),
        acct: activeAcct,
        hue: hue(name.length * 2),
        name,
        unread: 0,
        msgs: [{
          id: ++cid,
          dir: 'in',
          type: 'text',
          text: '你好，很高兴认识你',
          mt: nowHM()
        }]
      };
      setConvs(c => [nc, ...c]);
      setActive(nc.id);
      setDraft(text || '');
    }
    push('已打开 ' + name + ' 的会话，文案已预填');
  }, [goDm]);
  const conv = shownConvs.find(c => c.id === active) || shownConvs[0] || convs[0] || {
    id: '',
    name: '暂无会话',
    hue: 0,
    msgs: []
  };
  const send = () => {
    if (!draft.trim()) return;
    if (window.ApiBridge && window.ApiBridge.ready && conv && conv.id && conv.id.indexOf('rc') === 0) {
      const convId = conv.realId != null ? conv.realId : conv.conv_id || conv.id;
      api.sendDm(activeAcct, convId, draft.trim()).then(r => {
        if (r && r.ok) {
          push('私信已发送');
          setDraft('');
        } else push('发送失败: ' + (r && r.error || ''));
      }).catch(e => push('发送异常: ' + e));
      return;
    }
    setConvs(cs => cs.map(c => c.id === conv.id ? {
      ...c,
      msgs: [...c.msgs, {
        id: ++cid,
        dir: 'out',
        type: 'text',
        text: draft.trim(),
        mt: nowHM()
      }]
    } : c));
    setDraft('');
    push('私信已发送');
  };
  const openConv = id => {
    setActive(id);
    setConvs(cs => cs.map(c => c.id === id ? {
      ...c,
      unread: 0
    } : c));
  };
  const createConv = () => {
    const nm = newName.trim();
    if (!nm) {
      push('请输入对方昵称');
      return;
    }
    if (window.ApiBridge && window.ApiBridge.ready) {
      api.requestDm(nm).then(r => {
        setShowNew(false);
        setNewName('');
        push(r && r.ok ? (r.msg || '已加入发送队列') + ' · ' + nm : '发送失败: ' + (r && r.error || ''));
      }).catch(e => push('异常: ' + e));
      return;
    }
    const existing = convs.find(c => c.name === nm);
    if (existing) {
      setActiveAcct(existing.acct);
      setActive(existing.id);
      setShowNew(false);
      setNewName('');
      setDraft('');
      push('已打开已有会话 ' + nm);
      return;
    }
    const nc = {
      id: 'c' + (convs.length + 1),
      acct: activeAcct,
      hue: hue(nm.length * 2),
      name: nm,
      unread: 0,
      msgs: [{
        id: ++cid,
        dir: 'in',
        type: 'text',
        text: '你好，很高兴认识你',
        mt: nowHM()
      }]
    };
    setConvs(c => [nc, ...c]);
    setActive(nc.id);
    setShowNew(false);
    setNewName('');
    push('已创建会话 ' + nm);
  };
  return React.createElement("div", null, React.createElement("div", {
    className: "section-head"
  }, React.createElement("div", null, React.createElement("h2", null, "私信中心"), React.createElement("div", {
    className: "desc"
  }, "WebSocket 实时收发 · 文本 / 表情 / 语音 / 图片 / 视频")), React.createElement("div", {
    className: "head-row"
  }, window.ApiBridge && window.ApiBridge.ready ? React.createElement("span", {
    className: "badge-conn"
  }, React.createElement(Dot, {
    c: "ok",
    pulse: true
  }), " 已连接（真实后端）") : React.createElement(React.Fragment, null, React.createElement("span", {
    className: "badge-conn"
  }, React.createElement(Dot, {
    c: "warn"
  }), " 未连接"), React.createElement("span", {
    className: "demo-tag"
  }, "未连接")))), React.createElement("div", {
    className: "card",
    style: {
      marginBottom: 12
    },
    "data-od-id": "msg-acct-select"
  }, React.createElement("div", {
    className: "head-row"
  }, curAcct ? React.createElement(React.Fragment, null, React.createElement(Avatar, {
    name: curAcct.name,
    h: hue(curAcct.name.length)
  }), React.createElement("div", {
    style: {
      flex: 1
    }
  }, React.createElement("div", {
    style: {
      fontWeight: 600,
      fontSize: 13.5
    }
  }, "当前私信账号 · ", curAcct.name), React.createElement("div", {
    className: "mono",
    style: {
      fontSize: 11.5,
      color: 'var(--muted)'
    }
  }, "UID ", curAcct.uid || '—', " · 会话按账号隔离")), React.createElement(Pill, {
    c: curAcct.loggedIn ? 'ok' : (curAcct.level === 'nosign' ? 'warn' : 'danger')
  }, curAcct.label || '凭证状态未知')) : React.createElement("div", {
    style: {
      flex: 1,
      fontSize: 13.5,
      color: 'var(--muted)'
    }
  }, "暂无账号 · 请在「账号」页添加并登录"), React.createElement("div", {
    className: "seg",
    style: {
      marginLeft: 6
    }
  }, realAccts.map(a => React.createElement("button", {
    key: a.name,
    className: activeAcct === a.name ? 'active' : '',
    onClick: () => {
      setActiveAcct(a.name);
      setActive('');
      push('已切换到 ' + a.name + ' 的私信通道');
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
  }, "无已授权账号")))), React.createElement("div", {
    className: "grid cols-3-7",
    "data-od-id": "messages-panel"
  }, React.createElement("div", {
    className: "card",
    style: {
      padding: 8
    }
  }, React.createElement("div", {
    className: "head-row",
    style: {
      padding: '4px 8px 10px'
    }
  }, React.createElement("h3", {
    style: {
      marginBottom: 0
    }
  }, "会话列表"), React.createElement("div", {
    style: {
      flex: 1
    }
  }), React.createElement("button", {
    className: "btn sm ghost",
    "data-od-id": "new-conv",
    onClick: () => setShowNew(s => !s)
  }, showNew ? '取消' : '＋ 新建会话')), React.createElement("div", {
    className: "conv-list"
  }, showNew && React.createElement("div", {
    className: "head-row",
    style: {
      padding: '2px 8px 8px',
      gap: 8
    },
    "data-od-id": "new-conv-form"
  }, React.createElement("input", {
    className: "input",
    style: {
      flex: 1,
      height: 34,
      fontSize: 12.5
    },
    autoFocus: true,
    placeholder: "输入对方昵称，回车创建…",
    value: newName,
    onChange: e => setNewName(e.target.value),
    onKeyDown: e => {
      if (e.key === 'Enter') createConv();
      if (e.key === 'Escape') setShowNew(false);
    }
  }), React.createElement("button", {
    className: "btn sm ghost",
    onClick: createConv
  }, "创建")), shownConvs.length === 0 && React.createElement("div", {
    style: {
      padding: '14px 10px',
      color: 'var(--muted)',
      fontSize: 12.5
    }
  }, "暂无会话（接收守护未收到消息）"), shownConvs.map(c => React.createElement("button", {
    className: 'conv' + (c.id === active ? ' active' : ''),
    "data-od-id": 'conv-' + c.id,
    key: c.id,
    onClick: () => openConv(c.id)
  }, React.createElement(Avatar, {
    name: c.name,
    h: c.hue
  }), React.createElement("span", {
    className: "info"
  }, React.createElement("span", {
    className: "nm"
  }, c.name, React.createElement("span", {
    className: "t"
  }, c.msgs && c.msgs.length ? c.msgs[c.msgs.length - 1].mt : '')), React.createElement("span", {
    className: "pre"
  }, c.msgs && c.msgs.length ? c.msgs[c.msgs.length - 1].type === 'text' ? c.msgs[c.msgs.length - 1].text : c.msgs[c.msgs.length - 1].dir === 'in' ? '收到一条新消息' : '已发送' : '')), c.unread > 0 && React.createElement("span", {
    className: "unread"
  }, c.unread)))), React.createElement("div", {
    className: "card thread-wrap",
    style: {
      padding: 0
    }
  }, React.createElement("div", {
    className: "thread"
  }, React.createElement("div", {
    className: "thread-head"
  }, React.createElement(Avatar, {
    name: conv.name,
    h: conv.hue,
    sm: true
  }), React.createElement("span", {
    className: "nm"
  }, conv.name), React.createElement("span", {
    className: "mono",
    style: {
      fontSize: 11,
      color: 'var(--muted)'
    }
  }, "会话 ID ", conv.id), React.createElement("div", {
    style: {
      flex: 1
    }
  }), React.createElement("button", {
    className: "btn sm ghost",
    onClick: () => {
      push('已导出该会话为 JSON');
    }
  }, "导出会话")), newBanner && React.createElement("div", {
    className: "newmsg-banner",
    onClick: () => {
      setNewBanner(false);
    }
  }, "有新私信进入，点击查看"), React.createElement("div", {
    className: "msgs"
  }, conv.msgs.map(m => React.createElement("div", {
    className: 'msg ' + m.dir,
    key: m.id
  }, React.createElement(MsgBubble, {
    m: m,
    h: conv.hue
  }), React.createElement("span", {
    className: "mtm"
  }, m.mt)))), React.createElement("div", {
    className: "composer",
    "data-od-id": "composer"
  }, React.createElement("textarea", {
    className: "textarea",
    placeholder: "输入私信内容…",
    value: draft,
    onChange: e => setDraft(e.target.value),
    onKeyDown: e => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        send();
      }
    }
  }), React.createElement("button", {
    className: "btn primary",
    "data-od-id": "send-msg",
    onClick: send
  }, "发送")))))));
}
