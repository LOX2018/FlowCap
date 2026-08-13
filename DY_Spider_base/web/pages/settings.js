// pages/settings.js —— 设置页（数据走接口）
function Settings({
  push,
  api
}) {
  const [expandedAcct, setExpandedAcct] = useState(null);
  const [tasksCfg, setTasksCfg] = useState(null);
  const [forceRescan, setForceRescan] = useState(false);
  const [realAccts, setRealAccts] = useState([]);
  useEffect(() => {
    if (!(window.ApiBridge && window.ApiBridge.ready)) return;
    let alive = true;
    const loadTasks = () => api.getTasks().then(d => {
      if (alive && d && d.ok) {
        setTasksCfg(d);
        if (typeof d.forceRescan === 'boolean') setForceRescan(d.forceRescan);
      }
    }).catch(() => {});
    const loadAccts = () => api.getAccounts().then(d => {
      if (alive && d && d.ok && d.accounts) setRealAccts(d.accounts);
    }).catch(() => {});
    loadTasks();
    loadAccts();
    const iv1 = setInterval(loadTasks, 8000);
    const iv2 = setInterval(loadAccts, 8000);
    return () => {
      alive = false;
      clearInterval(iv1);
      clearInterval(iv2);
    };
  }, [api]);
  const tc = tasksCfg || {};
  const DEFAULTS = {
    rateLimit: '—',
    searchCap: '—',
    exportFmt: 'xlsx',
    dmPerLive: tc.maxTarget != null ? String(tc.maxTarget) : '—',
    dmInterval: tc.interval != null ? String(tc.interval) : '—',
    likeInterval: '—',
    wsReconnect: '—',
    syncLimit: '—',
    recvMedia: true,
    maxTasks: '—',
    retry: '—',
    timeout: '—',
    outDir: 'stats_export/ · logs/',
    autoExport: true,
    logFile: true,
    cookieWww: '',
    cookieLive: '',
    cookieWwwOk: false,
    cookieLiveOk: false,
    proxyEnabled: false,
    proxyAddr: ''
  };
  const saveAll = () => {
    if (window.ApiBridge && window.ApiBridge.ready) {
      api.saveConfig({
        forceRescan: forceRescan
      }).then(r => push(r && r.ok ? '配置已写回 config.py · 写入 ' + (r.written || 0) + ' 项' : '保存失败: ' + (r && r.error || ''))).catch(e => push('保存异常: ' + e));
    } else {
      push('未连接后端，无法保存');
    }
  };
  return React.createElement("div", null, React.createElement("div", {
    className: "section-head"
  }, React.createElement("div", null, React.createElement("h2", null, "设置"), React.createElement("div", {
    className: "desc"
  }, "默认配置适用于全部账号，可为单个账号开启独立配置覆盖默认值")), React.createElement("span", {
    className: "demo-tag"
  }, "未连接")), React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      marginBottom: 10
    }
  }, React.createElement("span", {
    style: {
      fontSize: 14,
      fontWeight: 700,
      color: 'var(--accent)'
    }
  }, "●"), React.createElement("span", {
    style: {
      fontSize: 14,
      fontWeight: 700
    }
  }, "默认配置"), React.createElement("span", {
    style: {
      fontSize: 12,
      color: 'var(--muted)'
    }
  }, "所有账号共用，未独立配置的账号自动使用此处参数")), React.createElement("div", {
    className: "grid cols-3",
    style: {
      marginBottom: 14
    }
  }, React.createElement("div", {
    className: "card"
  }, React.createElement("h3", null, "采集参数"), React.createElement("div", {
    className: "form"
  }, React.createElement("div", {
    className: "field"
  }, React.createElement("label", null, "请求频率限制（ms）"), React.createElement("input", {
    className: "input",
    style: {
      width: '100%',
      fontFamily: 'var(--font-mono)'
    },
    value: DEFAULTS.rateLimit,
    readOnly: true
  }), React.createElement("span", {
    className: "hint"
  }, "两次请求之间的最小间隔")), React.createElement("div", {
    className: "field"
  }, React.createElement("label", null, "搜索结果上限"), React.createElement("input", {
    className: "input",
    style: {
      width: '100%',
      fontFamily: 'var(--font-mono)'
    },
    value: DEFAULTS.searchCap,
    readOnly: true
  })), React.createElement("div", {
    className: "field"
  }, React.createElement("label", null, "导出格式"), React.createElement("div", {
    className: "seg",
    style: {
      width: '100%'
    }
  }, ['xlsx', 'csv', 'json'].map(f => React.createElement("button", {
    key: f,
    className: DEFAULTS.exportFmt === f ? 'active' : '',
    style: {
      flex: 1
    }
  }, f.toUpperCase())))))), React.createElement("div", {
    className: "card"
  }, React.createElement("h3", null, "直播监听策略"), React.createElement("div", {
    className: "form"
  }, React.createElement("div", {
    className: "field"
  }, React.createElement("label", null, "每场私信上限"), React.createElement("input", {
    className: "input",
    style: {
      width: '100%',
      fontFamily: 'var(--font-mono)'
    },
    value: DEFAULTS.dmPerLive,
    readOnly: true
  })), React.createElement("div", {
    className: "field"
  }, React.createElement("label", null, "私信间隔（秒）"), React.createElement("input", {
    className: "input",
    style: {
      width: '100%',
      fontFamily: 'var(--font-mono)'
    },
    value: DEFAULTS.dmInterval,
    readOnly: true
  })), React.createElement("div", {
    className: "field"
  }, React.createElement("label", null, "点赞间隔（秒）"), React.createElement("input", {
    className: "input",
    style: {
      width: '100%',
      fontFamily: 'var(--font-mono)'
    },
    value: DEFAULTS.likeInterval,
    readOnly: true
  })))), React.createElement("div", {
    className: "card"
  }, React.createElement("h3", null, "私信同步"), React.createElement("div", {
    className: "form"
  }, React.createElement("div", {
    className: "field"
  }, React.createElement("label", null, "断线重连（秒）"), React.createElement("input", {
    className: "input",
    style: {
      width: '100%',
      fontFamily: 'var(--font-mono)'
    },
    value: DEFAULTS.wsReconnect,
    readOnly: true
  })), React.createElement("div", {
    className: "field"
  }, React.createElement("label", null, "单次同步上限"), React.createElement("input", {
    className: "input",
    style: {
      width: '100%',
      fontFamily: 'var(--font-mono)'
    },
    value: DEFAULTS.syncLimit,
    readOnly: true
  })), React.createElement("div", {
    className: "field-row"
  }, React.createElement("label", {
    style: {
      flex: 1
    }
  }, "接收图片/视频"), React.createElement("span", {
    className: "switch"
  }, React.createElement("input", {
    type: "checkbox",
    checked: DEFAULTS.recvMedia,
    readOnly: true
  }), React.createElement("i", null)))))), React.createElement("div", {
    className: "grid cols-2",
    style: {
      marginBottom: 14
    }
  }, React.createElement("div", {
    className: "card"
  }, React.createElement("h3", null, "任务管理"), React.createElement("div", {
    className: "form",
    style: {
      display: 'flex',
      flexDirection: 'row',
      flexWrap: 'wrap',
      gap: 16,
      alignItems: 'center'
    }
  }, React.createElement("div", {
    className: "field",
    style: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 8
    }
  }, React.createElement("label", {
    style: {
      whiteSpace: 'nowrap'
    }
  }, "最大并发"), React.createElement("input", {
    className: "input",
    style: {
      width: 60,
      fontFamily: 'var(--font-mono)'
    },
    value: DEFAULTS.maxTasks,
    readOnly: true
  })), React.createElement("div", {
    className: "field",
    style: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 8
    }
  }, React.createElement("label", {
    style: {
      whiteSpace: 'nowrap'
    }
  }, "重试次数"), React.createElement("input", {
    className: "input",
    style: {
      width: 60,
      fontFamily: 'var(--font-mono)'
    },
    value: DEFAULTS.retry,
    readOnly: true
  })), React.createElement("div", {
    className: "field",
    style: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 8
    }
  }, React.createElement("label", {
    style: {
      whiteSpace: 'nowrap'
    }
  }, "超时（秒）"), React.createElement("input", {
    className: "input",
    style: {
      width: 60,
      fontFamily: 'var(--font-mono)'
    },
    value: DEFAULTS.timeout,
    readOnly: true
  })))), React.createElement("div", {
    className: "card"
  }, React.createElement("h3", null, "输出与日志"), React.createElement("div", {
    className: "form",
    style: {
      display: 'flex',
      flexDirection: 'row',
      flexWrap: 'wrap',
      gap: 16,
      alignItems: 'center'
    }
  }, React.createElement("div", {
    className: "field",
    style: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 8,
      flex: 1
    }
  }, React.createElement("label", {
    style: {
      whiteSpace: 'nowrap'
    }
  }, "输出目录"), React.createElement("input", {
    className: "input mono",
    style: {
      flex: 1
    },
    value: DEFAULTS.outDir,
    readOnly: true
  }), React.createElement("button", {
    className: "btn sm ghost",
    "data-od-id": "settings-pick-path",
    onClick: () => push('已选择输出目录 · ' + DEFAULTS.outDir)
  }, "选择路径")), React.createElement("div", {
    className: "field-row"
  }, React.createElement("label", null, "自动导出"), React.createElement("span", {
    className: "switch"
  }, React.createElement("input", {
    type: "checkbox",
    checked: DEFAULTS.autoExport,
    readOnly: true
  }), React.createElement("i", null))), React.createElement("div", {
    className: "field-row"
  }, React.createElement("label", null, "日志写文件"), React.createElement("span", {
    className: "switch"
  }, React.createElement("input", {
    type: "checkbox",
    checked: DEFAULTS.logFile,
    readOnly: true
  }), React.createElement("i", null)))))), React.createElement("div", {
    style: {
      background: 'var(--panel)',
      border: '1px solid var(--line)',
      borderRadius: 12,
      padding: '14px 16px',
      marginBottom: 14
    }
  }, React.createElement("div", {
    style: {
      fontSize: 13,
      fontWeight: 700,
      marginBottom: 8
    }
  }, "启动策略"), React.createElement("label", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      fontSize: 13,
      color: 'var(--muted)',
      cursor: 'pointer'
    }
  }, React.createElement("input", {
    type: "checkbox",
    checked: forceRescan,
    onChange: e => setForceRescan(e.target.checked)
  }), React.createElement("span", null, "启动前强制重新扫码"), React.createElement("i", {
    style: {
      fontSize: 12,
      color: 'var(--muted)'
    }
  }, "（勾选则每次启动自动私信都强制重扫忽略磁盘凭证；不勾选则复用守护进程保活的凭证快速启动）"))), React.createElement("div", {
    style: {
      display: 'flex',
      alignItems: 'center',
      gap: 8,
      marginTop: 20,
      marginBottom: 10
    }
  }, React.createElement("span", {
    style: {
      fontSize: 14,
      fontWeight: 700,
      color: 'var(--warn)'
    }
  }, "●"), React.createElement("span", {
    style: {
      fontSize: 14,
      fontWeight: 700
    }
  }, "独立配置"), React.createElement("span", {
    style: {
      fontSize: 12,
      color: 'var(--muted)'
    }
  }, "开启后该账号使用独立参数，未填写的字段仍使用默认值")), React.createElement("div", {
    style: {
      display: 'flex',
      flexDirection: 'column',
      gap: 10
    }
  }, realAccts.map(a => {
    const isExpanded = expandedAcct === a.name;
    const r = a.roles || {};
    const roleTxt = [r.monitor ? '监测' : '', r.sender ? '发送' : ''].filter(Boolean).join(' / ') || '未分配';
    return React.createElement("div", {
      className: "card",
      key: a.name,
      style: {
        padding: 0,
        overflow: 'hidden',
        borderLeft: a.loggedIn ? '3px solid var(--ok)' : '3px solid var(--danger)'
      }
    }, React.createElement("div", {
      style: {
        display: 'flex',
        alignItems: 'center',
        gap: 10,
        padding: '10px 16px',
        cursor: 'pointer'
      },
      onClick: () => setExpandedAcct(isExpanded ? null : a.name)
    }, React.createElement(Avatar, {
      name: a.name,
      h: hue(a.name.length),
      sm: true
    }), React.createElement("span", {
      style: {
        fontWeight: 600,
        fontSize: 13
      }
    }, a.name), React.createElement("span", {
      className: "mono",
      style: {
        fontSize: 11.5,
        color: 'var(--muted)'
      }
    }, a.uid || '未读入 UID'), React.createElement("div", {
      style: {
        flex: 1
      }
    }), a.isCurrent && React.createElement(Pill, {
      c: "ok"
    }, "当前"), r.monitor && React.createElement(Pill, {
      c: "ok"
    }, "监测"), r.sender && React.createElement(Pill, {
      c: "ok"
    }, "发送"), React.createElement(Pill, {
      c: a.loggedIn ? 'ok' : 'danger'
    }, a.loggedIn ? '已登录' : '未登录'), React.createElement("span", {
      style: {
        fontSize: 12,
        color: 'var(--muted)',
        transition: 'transform .15s',
        transform: isExpanded ? 'rotate(90deg)' : ''
      }
    }, "▶")), isExpanded && React.createElement("div", {
      style: {
        padding: '12px 16px',
        borderTop: '1px solid var(--border)',
        background: 'var(--surface-2)'
      }
    }, React.createElement("div", {
      style: {
        fontSize: 12,
        display: 'grid',
        gridTemplateColumns: '80px 1fr',
        gap: '4px 8px'
      }
    }, React.createElement("span", {
      style: {
        color: 'var(--muted)'
      }
    }, "UID"), React.createElement("span", {
      className: "mono",
      style: {
        fontSize: 11
      }
    }, a.uid || '未读入（扫码后自动获取）'), React.createElement("span", {
      style: {
        color: 'var(--muted)'
      }
    }, "角色"), React.createElement("span", {
      className: "mono",
      style: {
        fontSize: 11
      }
    }, roleTxt), React.createElement("span", {
      style: {
        color: 'var(--muted)'
      }
    }, "浏览器守护"), React.createElement("span", {
      className: "mono",
      style: {
        fontSize: 11
      }
    }, a.browserDaemonAlive ? '运行中' : '未运行'), React.createElement("span", {
      style: {
        color: 'var(--muted)'
      }
    }, "接收守护"), React.createElement("span", {
      className: "mono",
      style: {
        fontSize: 11
      }
    }, a.recvDaemonAlive ? '运行中' : '未运行'))));
  }), realAccts.length === 0 && React.createElement("div", {
    className: "card",
    style: {
      padding: 36,
      textAlign: 'center',
      color: 'var(--muted)',
      fontSize: 13
    }
  }, React.createElement("div", {
    style: {
      fontSize: 24,
      marginBottom: 6
    }
  }, "🔑"), "暂无账号 · 请到「账号管理」添加并扫码授权")), React.createElement("div", {
    style: {
      marginTop: 14,
      display: 'flex',
      gap: 8
    }
  }, React.createElement("button", {
    className: "btn primary",
    onClick: saveAll
  }, "保存全部配置"), React.createElement("button", {
    className: "btn ghost",
    onClick: () => push('重置仅作用于展示值；任务参数请到「任务中心」修改并保存')
  }, "重置全部为默认")));
}
