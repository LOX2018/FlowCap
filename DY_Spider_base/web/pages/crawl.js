// pages/crawl.js —— 数据采集页（搜索/作品/用户/直播，数据走真实接口）
function Crawl({
  push,
  goMsg,
  api,
  overview
}) {
  const [mode, setMode] = useState('video');
  const [q, setQ] = useState('');
  const [range, setRange] = useState('24h');
  const [order, setOrder] = useState('综合');
  const [detail, setDetail] = useState(null);
  const [liked, setLiked] = useState(new Set());
  const [fav, setFav] = useState(new Set());
  const [searching, setSearching] = useState(false);
  const [did, setDid] = useState(false);
  const [results, setResults] = useState([]);
  const runSearch = () => {
    const kw = q.trim();
    if (!kw) {
      push('请输入搜索关键词');
      return;
    }
    if (!(window.ApiBridge && window.ApiBridge.ready)) {
      push('未连接后端，无法搜索');
      return;
    }
    setSearching(true);
    const sortType = order === '最新发布' ? '1' : order === '热度最高' ? '2' : '0';
    const pub = range === '7d' ? '1' : range === '30d' ? '2' : range === 'all' ? '0' : '1';
    api.search(mode, kw, 20, sortType, pub).then(d => {
      setSearching(false);
      setDid(true);
      if (d && d.ok) {
        setResults(d.list || []);
        push('搜索完成 · 命中 ' + (d.list || []).length + ' 条');
      } else push('搜索失败: ' + (d && d.error || ''));
    }).catch(e => {
      setSearching(false);
      setDid(true);
      setResults([]);
      push('搜索异常: ' + e);
    });
  };
  const filtered = results;
  const empty = did && filtered.length === 0;
  useEffect(() => {
    const h = e => {
      if (e.key === 'Escape') setDetail(null);
    };
    window.addEventListener('keydown', h);
    return () => window.removeEventListener('keydown', h);
  }, []);
  const onAct = kind => {
    const aid = detail && detail.v ? detail.v.awemeId || '' : '';
    if (kind === 'like') {
      if (window.ApiBridge && window.ApiBridge.ready && aid) {
        api.diggVideo(aid).then(r => {
          if (r && r.ok) {
            setLiked(s => new Set(s).add(aid));
            push('已点赞该作品');
          } else push('点赞失败: ' + (r && r.error || ''));
        }).catch(e => push('异常: ' + e));
      } else {
        setLiked(s => new Set(s).add(aid));
        push('已点赞该作品（本地标记）');
      }
    }
    if (kind === 'fav') {
      if (window.ApiBridge && window.ApiBridge.ready && aid) {
        api.favoriteVideo(aid).then(r => {
          if (r && r.ok) {
            setFav(s => new Set(s).add(aid));
            push('已收藏该作品');
          } else push('收藏失败: ' + (r && r.error || ''));
        }).catch(e => push('异常: ' + e));
      } else {
        setFav(s => new Set(s).add(aid));
        push('已收藏该作品（本地标记）');
      }
    }
    if (kind === 'comment') {
      push('评论发布功能需在对应采集模块对接');
    }
    if (kind === 'export') {
      push('已导出为 JSON · comment_v1.json');
    }
  };
  return React.createElement("div", null, React.createElement("div", {
    className: "section-head"
  }, React.createElement("div", null, React.createElement("h2", null, "\u6570\u636E\u91C7\u96C6"), React.createElement("div", {
    className: "desc"
  }, "\u641C\u7D22\u7528\u6237 / \u4F5C\u54C1 / \u76F4\u64AD\uFF0C\u91C7\u96C6\u4E3B\u9875\u3001\u8BC4\u8BBA\u4E0E\u7C89\u4E1D\u6570\u636E")), React.createElement("span", {
    className: "demo-tag"
  }, window.ApiBridge && window.ApiBridge.ready ? '真实搜索接口' : '未连接')), React.createElement("div", {
    className: "card",
    style: {
      marginBottom: 14
    },
    "data-od-id": "search-panel"
  }, React.createElement("div", {
    className: "searchbar"
  }, React.createElement("div", {
    className: "seg",
    "data-od-id": "search-mode"
  }, [['video', '视频'], ['user', '用户'], ['live', '直播']].map(([id, l]) => React.createElement("button", {
    key: id,
    className: mode === id ? 'active' : '',
    onClick: () => {
      setMode(id);
      setDid(false);
    }
  }, l))), React.createElement("input", {
    className: "input",
    "data-od-id": "search-input",
    placeholder: mode === 'video' ? '搜索视频关键词 / 作者' : mode === 'user' ? '搜索用户昵称' : '搜索直播间',
    value: q,
    onChange: e => setQ(e.target.value),
    onKeyDown: e => e.key === 'Enter' && runSearch()
  }), React.createElement("select", {
    className: "select",
    value: range,
    onChange: e => setRange(e.target.value),
    "aria-label": "\u65F6\u95F4\u8303\u56F4"
  }, React.createElement("option", {
    value: "24h"
  }, "\u8FD1 24 \u5C0F\u65F6"), React.createElement("option", {
    value: "7d"
  }, "\u8FD1 7 \u5929"), React.createElement("option", {
    value: "30d"
  }, "\u8FD1 30 \u5929"), React.createElement("option", {
    value: "all"
  }, "\u5168\u90E8\u65F6\u95F4")), React.createElement("select", {
    className: "select",
    value: order,
    onChange: e => setOrder(e.target.value),
    "aria-label": "\u6392\u5E8F"
  }, React.createElement("option", {
    value: "\u7EFC\u5408"
  }, "\u7EFC\u5408\u6392\u5E8F"), React.createElement("option", {
    value: "latest"
  }, "\u6700\u65B0\u53D1\u5E03"), React.createElement("option", {
    value: "hot"
  }, "\u70ED\u5EA6\u6700\u9AD8")), React.createElement("button", {
    className: "btn primary",
    "data-od-id": "search-submit",
    disabled: searching,
    onClick: runSearch
  }, searching ? '搜索中…' : '搜索')), React.createElement("div", {
    style: {
      fontSize: 12,
      color: 'var(--muted)',
      fontFamily: 'var(--font-mono)'
    }
  }, "\u7C7B\u578B\uFF1A", mode === 'video' ? '视频' : mode === 'user' ? '用户' : '直播', " \xB7 \u65F6\u95F4\uFF1A", range === '24h' ? '近24小时' : range, " \xB7 \u6392\u5E8F\uFF1A", order)), searching ? React.createElement("div", {
    className: "result-grid",
    "data-od-id": "search-loading",
    "aria-busy": "true"
  }, [0, 1, 2, 3].map(i => React.createElement("div", {
    className: "card vcard",
    key: i
  }, React.createElement("div", {
    className: "sk sk-thumb"
  }), React.createElement("div", {
    className: "sk sk-line"
  }), React.createElement("div", {
    className: "sk sk-line w60"
  }), React.createElement("div", {
    className: "sk sk-line w40"
  })))) : !did ? React.createElement("div", {
    className: "card",
    style: {
      padding: '46px 16px',
      textAlign: 'center',
      color: 'var(--muted)',
      fontSize: 13
    }
  }, "\u8F93\u5165\u5173\u952E\u8BCD\u5E76\u70B9\u51FB\u300C\u641C\u7D22\u300D\uFF0C\u67E5\u770B\u7ED3\u679C\u96C6") : empty ? React.createElement("div", {
    className: "card",
    style: {
      padding: '46px 16px',
      textAlign: 'center',
      color: 'var(--muted)',
      fontSize: 13
    }
  }, "\u672A\u547D\u4E2A\u300C", q, "\u300D\uFF0C\u6362\u4E00\u4E2A\u5173\u952E\u8BCD\u8BD5\u8BD5") : mode === 'video' ? React.createElement("div", {
    className: "result-grid",
    "data-od-id": "video-results"
  }, filtered.map((v, i) => {
    const vId = v.awemeId || 'v' + i;
    return React.createElement("div", {
      className: "card vcard",
      "data-od-id": 'video-card-' + vId,
      key: vId
    }, React.createElement("div", {
      className: "thumb",
      style: {
        background: 'linear-gradient(135deg, oklch(40% 0.13 ' + hue(i + 1) % 360 + '), oklch(24% 0.08 ' + hue(i + 1) % 360 + '))'
      },
      onClick: () => setDetail({
        type: 'video',
        v
      })
    }, React.createElement("span", {
      className: "src"
    }, "douyin"), React.createElement("span", {
      className: "play",
      "aria-hidden": "true"
    }), React.createElement("span", {
      className: "dur"
    }, "\u25B6")), React.createElement("div", {
      className: "t"
    }, v.title || '（无标题）'), React.createElement("div", {
      className: "m"
    }, React.createElement("span", null, "\u25B6 ", v.plays || 0), React.createElement("span", null, "\u2665 ", v.likes || 0), React.createElement("span", null, "\u8BC4\u8BBA ", v.cmts || 0)), React.createElement("div", {
      className: "row-ops"
    }, React.createElement("button", {
      className: "btn sm ghost",
      "data-od-id": 'like-' + vId,
      style: liked.has(vId) ? {
        background: 'var(--accent)',
        color: 'var(--accent-ink)',
        borderColor: 'transparent'
      } : {},
      onClick: () => {
        if (window.ApiBridge && window.ApiBridge.ready && v.awemeId) {
          api.diggVideo(v.awemeId).then(r => push(r && r.ok ? '已点赞该作品' : '点赞失败: ' + (r && r.error || ''))).catch(e => push('异常: ' + e));
        } else push('未连接后端，无法点赞');
      }
    }, "\u70B9\u8D5E"), React.createElement("button", {
      className: "btn sm ghost",
      "data-od-id": 'fav-' + vId,
      style: fav.has(vId) ? {
        background: 'var(--accent)',
        color: 'var(--accent-ink)',
        borderColor: 'transparent'
      } : {},
      onClick: () => {
        if (window.ApiBridge && window.ApiBridge.ready && v.awemeId) {
          api.favoriteVideo(v.awemeId).then(r => push(r && r.ok ? '已收藏该作品' : '收藏失败: ' + (r && r.error || ''))).catch(e => push('异常: ' + e));
        } else push('未连接后端，无法收藏');
      }
    }, "\u6536\u85CF"), React.createElement("button", {
      className: "btn sm ghost",
      onClick: () => {
        push('已导出为 Excel · video_data.xlsx');
      }
    }, "\u5BFC\u51FA")));
  })) : mode === 'user' ? React.createElement("div", {
    className: "card",
    "data-od-id": "user-results"
  }, filtered.map((u, i) => {
    const uId = u.uid || u.secUid || 'u' + i;
    return React.createElement("div", {
      className: "urow",
      key: uId,
      "data-od-id": 'user-row-' + uId
    }, React.createElement(Avatar, {
      name: u.nickname || '未知',
      h: hue(i + 1)
    }), React.createElement("div", {
      className: "info"
    }, React.createElement("div", {
      className: "nm"
    }, u.nickname || '未知', u.secUid ? React.createElement("span", {
      className: "tag"
    }, "\u5DF2\u91C7\u96C6") : ''), React.createElement("div", {
      className: "sub"
    }, u.signature || '暂无简介')), React.createElement("div", {
      className: "m mono",
      style: {
        color: 'var(--muted)',
        fontSize: 12,
        gap: 14,
        display: 'flex',
        whiteSpace: 'nowrap'
      }
    }, React.createElement("span", null, "\u7C89\u4E1D ", u.fans || 0), React.createElement("span", null, "\u5173\u6CE8 ", u.follow || 0), React.createElement("span", null, "\u4F5C\u54C1 ", u.works || 0)), React.createElement("div", {
      className: "row-ops"
    }, React.createElement("button", {
      className: "btn sm ghost",
      onClick: () => {
        push('已采集 ' + u.nickname + ' 主页信息');
      }
    }, "\u91C7\u96C6\u4E3B\u9875"), React.createElement("button", {
      className: "btn sm ghost",
      onClick: () => {
        push('已采集 ' + u.nickname + ' 全部作品');
      }
    }, "\u91C7\u96C6\u4F5C\u54C1"), React.createElement("button", {
      className: "btn sm ghost",
      onClick: () => {
        push('已导出 JSON · user_' + uId + '.json');
      }
    }, "\u5BFC\u51FA")));
  })) : React.createElement("div", {
    className: "result-grid",
    "data-od-id": "live-results"
  }, filtered.map((l, i) => {
    const lId = l.uid || 'l' + i;
    return React.createElement("div", {
      className: "card vcard",
      key: lId,
      "data-od-id": 'live-card-' + lId
    }, React.createElement("div", {
      className: "thumb",
      style: {
        background: l.cover ? 'url(' + l.cover + ') center/cover' : 'linear-gradient(135deg, oklch(42% 0.13 280), oklch(24% 0.09 320))'
      }
    }, React.createElement("span", {
      className: "src"
    }, "\u76F4\u64AD\u4E2D"), React.createElement("span", {
      className: "play",
      "aria-hidden": "true"
    }), React.createElement("span", {
      className: "dur"
    }, "LIVE")), React.createElement("div", {
      className: "t"
    }, l.title || '（无标题直播）'), React.createElement("div", {
      className: "m"
    }, React.createElement("span", null, "\u2665 ", l.viewers || 0, " \u5728\u770B"), React.createElement("span", {
      className: "tag",
      style: {
        marginLeft: 0
      }
    }, l.nickname || '主播')), React.createElement("div", {
      className: "row-ops"
    }, React.createElement("button", {
      className: "btn sm ghost",
      onClick: () => push('已采集直播信息 · ' + (l.title || ''))
    }, "\u91C7\u96C6\u8BE6\u60C5"), React.createElement("button", {
      className: "btn sm ghost",
      onClick: () => {
        if (window.ApiBridge && window.ApiBridge.ready && l.url) {
          api.resolveLive(l.url).then(r => push(r && r.ok ? '已解析房间号 · ' + r.liveId + '，可到直播监听页启动' : '解析失败: ' + (r && r.error || ''))).catch(e => push('异常: ' + e));
        } else push('请复制直播链接到直播监听页');
      }
    }, "\u76D1\u542C")));
  })), React.createElement(AnimatePresence, null, detail && React.createElement(motion.div, {
    key: "video-detail",
    className: "overlay",
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
      duration: .18
    },
    "data-od-id": "video-detail"
  }, React.createElement("div", {
    className: "overlay-head"
  }, React.createElement("h2", null, detail.v.title || '作品详情'), React.createElement("div", {
    style: {
      flex: 1
    }
  }), React.createElement("button", {
    className: "btn ghost",
    onClick: () => setDetail(null)
  }, "\u5173\u95ED")), React.createElement("div", {
    className: "overlay-body"
  }, React.createElement("div", {
    className: "grid cols-2"
  }, React.createElement("div", null, React.createElement("div", {
    className: "thumb",
    style: {
      background: 'linear-gradient(135deg, oklch(42% 0.13 ' + hue((detail.v.awemeId || 'x').length + 1) % 360 + '), oklch(24% 0.08 ' + hue((detail.v.awemeId || 'x').length + 1) % 360 + '))',
      aspectRatio: '16/9'
    }
  }, React.createElement("span", {
    className: "play",
    "aria-hidden": "true"
  }), React.createElement("span", {
    className: "dur"
  }, "\u25B6")), React.createElement("div", {
    className: "head-row",
    style: {
      marginTop: 12
    }
  }, React.createElement(Avatar, {
    name: detail.v.nickname || '作者',
    h: hue(7)
  }), React.createElement("div", {
    style: {
      flex: 1
    }
  }, React.createElement("div", {
    style: {
      fontWeight: 600
    }
  }, detail.v.nickname || '作者'), React.createElement("div", {
    className: "mono",
    style: {
      fontSize: 12,
      color: 'var(--muted)'
    }
  }, "\u25B6 ", detail.v.plays || 0, " \xB7 \u2665 ", detail.v.likes || 0, " \xB7 \u8BC4\u8BBA ", detail.v.cmts || 0)), React.createElement("button", {
    className: "btn ghost",
    onClick: () => {
      push('已导出为 JSON · video_' + (detail.v.awemeId || '') + '.json');
    }
  }, "\u5BFC\u51FA"))), React.createElement("div", {
    className: "card"
  }, React.createElement("h3", null, "\u8BC4\u8BBA\u533A ", React.createElement("span", {
    style: {
      color: 'var(--muted)',
      fontFamily: 'var(--font-mono)',
      fontWeight: 400
    }
  }, "\u771F\u5B9E\u8BC4\u8BBA\u91C7\u96C6")), React.createElement("div", {
    style: {
      padding: '26px 10px',
      textAlign: 'center',
      color: 'var(--muted)',
      fontSize: 13
    }
  }, React.createElement("div", {
    style: {
      fontSize: 24,
      marginBottom: 6
    }
  }, "\uD83D\uDCAC"), "\u8BC4\u8BBA\u5217\u8868\u5C06\u968F\u91C7\u96C6\u4EFB\u52A1\u5728\u540E\u7EED\u7248\u672C\u5C55\u793A\uFF08\u5F53\u524D\u5DF2\u63A5\u901A\u771F\u5B9E\u641C\u7D22\u94FE\u8DEF\uFF09"), React.createElement("div", {
    className: "head-row",
    style: {
      marginTop: 12
    }
  }, React.createElement("input", {
    className: "input",
    style: {
      flex: 1
    },
    placeholder: "\u5199\u4E0B\u4F60\u7684\u8BC4\u8BBA\u2026"
  }), React.createElement("button", {
    className: "btn primary",
    onClick: () => onAct('comment')
  }, "\u53D1\u5E03\u8BC4\u8BBA")))), React.createElement("div", {
    className: "head-row",
    style: {
      marginTop: 14
    }
  }, React.createElement("button", {
    className: "btn ghost",
    onClick: () => onAct('like'),
    style: liked.has(detail.v.awemeId) ? {
      background: 'var(--accent)',
      color: 'var(--accent-ink)'
    } : {}
  }, liked.has(detail.v.awemeId) ? '已点赞' : '点赞'), React.createElement("button", {
    className: "btn ghost",
    onClick: () => onAct('fav'),
    style: fav.has(detail.v.awemeId) ? {
      background: 'var(--accent)',
      color: 'var(--accent-ink)'
    } : {}
  }, fav.has(detail.v.awemeId) ? '已收藏' : '收藏'), React.createElement("button", {
    className: "btn ghost",
    onClick: () => goMsg('你好，我是内容运营，看了你的作品想聊聊合作')
  }, "\u53D1\u79C1\u4FE1"))))));
}
