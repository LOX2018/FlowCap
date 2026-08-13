// pages/tasks.js —— 任务中心页（数据走接口）
function Tasks({
  push,
  api,
  overview
}) {
  const [tasks, setTasks] = useState([]);
  const ready = window.ApiBridge && window.ApiBridge.ready;
  useEffect(() => {
    if (ready) return;
    const iv = setInterval(() => setTasks(ts => ts.map(t => t.status === 'running' ? {
      ...t,
      prog: Math.min(100, t.prog + Math.round(Math.random() * 6))
    } : t)), 1800);
    return () => clearInterval(iv);
  }, [ready]);
  const done = tasks.filter(t => t.status === 'done').length;
  const ov = overview || {};
  return React.createElement("div", null, React.createElement("div", {
    className: "section-head"
  }, React.createElement("div", null, React.createElement("h2", null, "任务中心"), React.createElement("div", {
    className: "desc"
  }, "采集 / 监听 / 导出任务与断线重连状态")), React.createElement("div", {
    className: "head-row"
  }, ready ? React.createElement("span", {
    className: "badge-conn"
  }, React.createElement(Dot, {
    c: ov.running ? ov.paused ? 'warn' : 'ok' : 'danger',
    pulse: ov.running && !ov.paused
  }), " 已发 ", ov.sent || 0, "/", ov.limit || 0, ov.queue ? ' · 待发 ' + ov.queue : '') : React.createElement(React.Fragment, null, React.createElement("span", {
    className: "badge-conn"
  }, React.createElement(Dot, {
    c: "ok",
    pulse: true
  }), " ", done, "/", tasks.length, " 已完成"), React.createElement("span", {
    className: "demo-tag"
  }, "未连接")), ready && React.createElement("button", {
    className: "btn sm primary",
    onClick: () => api.exportStats().then(r => push(r && r.ok ? '统计已导出 · ' + r.path : '导出失败: ' + (r && r.error || ''))).catch(e => push('导出异常: ' + e))
  }, "导出统计 xlsx"))), React.createElement("div", {
    className: "card",
    style: {
      marginBottom: 14,
      padding: 0
    },
    "data-od-id": "task-list"
  }, React.createElement("div", {
    className: "table-scroll"
  }, React.createElement("table", {
    className: "table"
  }, React.createElement("thead", null, React.createElement("tr", null, React.createElement("th", null, "创建时间"), React.createElement("th", null, "账号"), React.createElement("th", null, "任务类型"), React.createElement("th", null, "目标"), React.createElement("th", null, "状态"), React.createElement("th", null, "耗时"), React.createElement("th", null, "结果条数"), React.createElement("th", null, "重试"), React.createElement("th", null, "操作"))), React.createElement("tbody", null, ready && ov.running ? React.createElement("tr", {
    key: "live-task"
  }, React.createElement("td", {
    className: "mono",
    style: {
      whiteSpace: 'nowrap'
    }
  }, ov.status || '—'), React.createElement("td", null, React.createElement("div", {
    style: {
      display: 'inline-flex',
      alignItems: 'center',
      gap: 7
    }
  }, React.createElement(Avatar, {
    name: "引擎",
    h: 160,
    sm: true
  }), React.createElement("span", null, "自动私信引擎"))), React.createElement("td", null, "直播监听私信"), React.createElement("td", {
    className: "mono",
    style: {
      color: 'var(--muted)'
    }
  }, ov.liveUrl || '—'), React.createElement("td", null, React.createElement(Pill, {
    c: ov.paused ? 'warn' : 'ok'
  }, ov.paused ? '已暂停' : '运行中')), React.createElement("td", {
    className: "mono"
  }, ov.sent || 0, "/", ov.limit || 0), React.createElement("td", {
    className: "mono"
  }, ov.queue || 0), React.createElement("td", {
    className: "mono"
  }, "0"), React.createElement("td", null, ov.paused ? React.createElement("button", {
    className: "btn text sm",
    onClick: () => api.resume().then(() => push('已继续')).catch(e => push('异常: ' + e))
  }, "继续") : React.createElement("button", {
    className: "btn text sm",
    onClick: () => api.pause().then(() => push('已暂停')).catch(e => push('异常: ' + e))
  }, "暂停"), React.createElement("button", {
    className: "btn text sm",
    style: {
      color: 'var(--danger)'
    },
    onClick: () => api.stop().then(() => push('已停止')).catch(e => push('异常: ' + e))
  }, "停止"))) : tasks.map(t => {
    const ta = ACCOUNTS_INIT.find(a => a.id === t.acct) || null;
    return React.createElement("tr", {
      key: t.id
    }, React.createElement("td", {
      className: "mono",
      style: {
        whiteSpace: 'nowrap'
      }
    }, t.ct), React.createElement("td", null, React.createElement("div", {
      style: {
        display: 'inline-flex',
        alignItems: 'center',
        gap: 7
      }
    }, React.createElement(Avatar, {
      name: ta ? ta.name : '?',
      h: ta ? ta.hue : 0,
      sm: true
    }), React.createElement("span", null, ta ? ta.name : '—'))), React.createElement("td", null, t.type), React.createElement("td", {
      className: "mono",
      style: {
        color: 'var(--muted)'
      }
    }, t.target), React.createElement("td", null, React.createElement(Pill, {
      c: TASK_ST[t.status][1]
    }, TASK_ST[t.status][0])), React.createElement("td", {
      className: "mono"
    }, t.dur), React.createElement("td", {
      className: "mono"
    }, t.res), React.createElement("td", {
      className: "mono"
    }, t.retry), React.createElement("td", null, t.status === 'done' ? React.createElement("button", {
      className: "btn text sm",
      onClick: () => push('已导出结果 · ' + t.target + '.json')
    }, "导出结果") : t.status === 'running' ? React.createElement("button", {
      className: "btn text sm",
      onClick: () => push('任务已暂停')
    }, "暂停") : React.createElement("button", {
      className: "btn text sm",
      onClick: () => push('任务已重新入队')
    }, "重试")));
  }))))), React.createElement("div", {
    className: "export-grid"
  }, React.createElement("div", {
    className: "card",
    "data-od-id": "export-card"
  }, React.createElement("h3", null, "导出管理"), React.createElement("div", {
    className: "field-row",
    style: {
      marginBottom: 10
    }
  }, React.createElement("div", {
    style: {
      flex: 1
    }
  }, React.createElement("div", {
    style: {
      fontWeight: 600
    }
  }, "评论数据.xlsx"), React.createElement("div", {
    className: "mono",
    style: {
      fontSize: 11.5,
      color: 'var(--muted)'
    }
  }, "8,432 行 · 2026-08-11 10:12")), ready ? React.createElement("button", {
    className: "btn sm ghost",
    onClick: () => api.exportStats().then(r => push(r && r.ok ? '已导出 · ' + r.path : '导出失败')).catch(e => push('异常: ' + e))
  }, "导出") : React.createElement("button", {
    className: "btn sm ghost",
    onClick: () => push('已开始导出 Excel')
  }, "导出")), React.createElement("div", {
    className: "field-row",
    style: {
      marginBottom: 10
    }
  }, React.createElement("div", {
    style: {
      flex: 1
    }
  }, React.createElement("div", {
    style: {
      fontWeight: 600
    }
  }, "用户主页数据.json"), React.createElement("div", {
    className: "mono",
    style: {
      fontSize: 11.5,
      color: 'var(--muted)'
    }
  }, "96 条 · 2026-08-11 09:40")), React.createElement("button", {
    className: "btn sm ghost",
    onClick: () => push('已开始导出 JSON')
  }, "导出")), React.createElement("div", {
    className: "field-row"
  }, React.createElement("div", {
    style: {
      flex: 1
    }
  }, React.createElement("div", {
    style: {
      fontWeight: 600
    }
  }, "私信会话.json"), React.createElement("div", {
    className: "mono",
    style: {
      fontSize: 11.5,
      color: 'var(--muted)'
    }
  }, "342 条 · 2026-08-11 10:01")), React.createElement("button", {
    className: "btn sm ghost",
    onClick: () => push('已开始导出 JSON')
  }, "导出"))), React.createElement("div", {
    className: "card",
    "data-od-id": "output-tree"
  }, React.createElement("h3", null, "结构化输出目录"), React.createElement("div", {
    className: "tree"
  }, React.createElement("div", null, React.createElement("span", {
    className: "dir"
  }, "output/")), React.createElement("div", null, "　├─ ", React.createElement("span", {
    className: "dir"
  }, "2026-08-11/")), React.createElement("div", null, "　│　├─ ", React.createElement("span", {
    className: "cur"
  }, "users.json")), React.createElement("div", null, "　│　├─ ", React.createElement("span", {
    className: "cur"
  }, "videos/")), React.createElement("div", null, "　│　│　└─ ", React.createElement("span", {
    className: "cur"
  }, "v1/")), React.createElement("div", null, "　│　│　　├─ info.json"), React.createElement("div", null, "　│　│　　└─ comments.xlsx"), React.createElement("div", null, "　│　├─ ", React.createElement("span", {
    className: "cur"
  }, "live_7462001/")), React.createElement("div", null, "　│　│　└─ ", React.createElement("span", {
    className: "cur"
  }, "danmaku.csv")), React.createElement("div", null, "　│　└─ ", React.createElement("span", {
    className: "cur"
  }, "media/"))))));
}
