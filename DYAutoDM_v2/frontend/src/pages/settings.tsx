/**
 * 设置页
 *
 * 迁移自: DY_Spider_base/web/pages/settings.js
 * 原版职责: 配置保存、账号查看
 * 迁移要点:
 *   - 旧版 setInterval 轮询 tasks/accounts → 改用 useQuery + refetchInterval
 *   - 旧版 window.ApiBridge.ready → props.ready
 *   - React.createElement → JSX
 *   - 配置写回改为 FastAPI 后端统一处理（saveConfig），不再提及 config.py
 *   - .catch(() => {}) → .catch(e => push('失败:' + ...))
 */
import { useState, useEffect } from "react";
import { useQuery } from "@tanstack/react-query";
import { PageProps } from "../api/client";
import { Avatar, Pill, hue } from "../components/ui";

interface Account {
  name: string;
  uid?: string;
  isCurrent?: boolean;
  loggedIn?: boolean;
  roles?: { monitor?: boolean; sender?: boolean };
  browserDaemonAlive?: boolean;
  recvDaemonAlive?: boolean;
}
interface AccountsResp {
  ok?: boolean;
  accounts?: Account[];
}
interface TasksResp {
  ok?: boolean;
  forceRescan?: boolean;
  maxTarget?: number;
  interval?: number;
}

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export default function SettingsPage(props: PageProps) {
  const { push, api, ready } = props;
  const [expandedAcct, setExpandedAcct] = useState<string | null>(null);
  const [forceRescan, setForceRescan] = useState(false);

  const tasksQ = useQuery({
    queryKey: ["settings-tasks"],
    queryFn: async (): Promise<TasksResp | null> => {
      const d = (await api.getTasks()) as unknown as TasksResp;
      return d && d.ok ? d : null;
    },
    refetchInterval: 8000,
    enabled: !!ready,
  });

  const accountsQ = useQuery({
    queryKey: ["settings-accounts"],
    queryFn: async (): Promise<Account[]> => {
      const d = (await api.getAccounts()) as unknown as AccountsResp;
      return d && d.ok && d.accounts ? d.accounts : [];
    },
    refetchInterval: 8000,
    enabled: !!ready,
  });

  useEffect(() => {
    if (tasksQ.data && typeof tasksQ.data.forceRescan === "boolean") {
      setForceRescan(tasksQ.data.forceRescan);
    }
  }, [tasksQ.data]);

  const tc: TasksResp = tasksQ.data || {};
  const realAccts = accountsQ.data || [];

  const DEFAULTS = {
    rateLimit: "—",
    searchCap: "—",
    exportFmt: "xlsx",
    dmPerLive: tc.maxTarget != null ? String(tc.maxTarget) : "—",
    dmInterval: tc.interval != null ? String(tc.interval) : "—",
    likeInterval: "—",
    wsReconnect: "—",
    syncLimit: "—",
    recvMedia: true,
    maxTasks: "—",
    retry: "—",
    timeout: "—",
    outDir: "stats_export/ · logs/",
    autoExport: true,
    logFile: true,
    cookieWww: "",
    cookieLive: "",
    cookieWwwOk: false,
    cookieLiveOk: false,
    proxyEnabled: false,
    proxyAddr: "",
  };

  const saveAll = () => {
    api.saveConfig({ forceRescan })
      .then((r) => push(r && r.ok ? "配置已保存" : "保存失败"))
      .catch((e) => push("失败:保存异常 " + errMsg(e)));
  };

  return (
    <div>
      <div className="section-head">
        <div>
          <h2>设置</h2>
          <div className="desc">默认配置适用于全部账号，可为单个账号开启独立配置覆盖默认值</div>
        </div>
        <span className="demo-tag">{ready ? "已连接" : "未连接"}</span>
      </div>

      <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 10 }}>
        <span style={{ fontSize: 14, fontWeight: 700, color: "var(--accent)" }}>●</span>
        <span style={{ fontSize: 14, fontWeight: 700 }}>默认配置</span>
        <span style={{ fontSize: 12, color: "var(--muted)" }}>
          所有账号共用，未独立配置的账号自动使用此处参数
        </span>
      </div>

      <div className="grid cols-3" style={{ marginBottom: 14 }}>
        <div className="card">
          <h3>采集参数</h3>
          <div className="form">
            <div className="field">
              <label>请求频率限制（ms）</label>
              <input
                className="input"
                style={{ width: "100%", fontFamily: "var(--font-mono)" }}
                value={DEFAULTS.rateLimit}
                readOnly
              />
              <span className="hint">两次请求之间的最小间隔</span>
            </div>
            <div className="field">
              <label>搜索结果上限</label>
              <input
                className="input"
                style={{ width: "100%", fontFamily: "var(--font-mono)" }}
                value={DEFAULTS.searchCap}
                readOnly
              />
            </div>
            <div className="field">
              <label>导出格式</label>
              <div className="seg" style={{ width: "100%" }}>
                {["xlsx", "csv", "json"].map((f) => (
                  <button
                    key={f}
                    className={DEFAULTS.exportFmt === f ? "active" : ""}
                    style={{ flex: 1 }}
                  >
                    {f.toUpperCase()}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>

        <div className="card">
          <h3>直播监听策略</h3>
          <div className="form">
            <div className="field">
              <label>每场私信上限</label>
              <input
                className="input"
                style={{ width: "100%", fontFamily: "var(--font-mono)" }}
                value={DEFAULTS.dmPerLive}
                readOnly
              />
            </div>
            <div className="field">
              <label>私信间隔（秒）</label>
              <input
                className="input"
                style={{ width: "100%", fontFamily: "var(--font-mono)" }}
                value={DEFAULTS.dmInterval}
                readOnly
              />
            </div>
            <div className="field">
              <label>点赞间隔（秒）</label>
              <input
                className="input"
                style={{ width: "100%", fontFamily: "var(--font-mono)" }}
                value={DEFAULTS.likeInterval}
                readOnly
              />
            </div>
          </div>
        </div>

        <div className="card">
          <h3>私信同步</h3>
          <div className="form">
            <div className="field">
              <label>断线重连（秒）</label>
              <input
                className="input"
                style={{ width: "100%", fontFamily: "var(--font-mono)" }}
                value={DEFAULTS.wsReconnect}
                readOnly
              />
            </div>
            <div className="field">
              <label>单次同步上限</label>
              <input
                className="input"
                style={{ width: "100%", fontFamily: "var(--font-mono)" }}
                value={DEFAULTS.syncLimit}
                readOnly
              />
            </div>
            <div className="field-row">
              <label style={{ flex: 1 }}>接收图片/视频</label>
              <span className="switch">
                <input type="checkbox" checked={DEFAULTS.recvMedia} readOnly />
                <i />
              </span>
            </div>
          </div>
        </div>
      </div>

      <div className="grid cols-2" style={{ marginBottom: 14 }}>
        <div className="card">
          <h3>任务管理</h3>
          <div
            className="form"
            style={{
              display: "flex",
              flexDirection: "row",
              flexWrap: "wrap",
              gap: 16,
              alignItems: "center",
            }}
          >
            <div className="field" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
              <label style={{ whiteSpace: "nowrap" }}>最大并发</label>
              <input
                className="input"
                style={{ width: 60, fontFamily: "var(--font-mono)" }}
                value={DEFAULTS.maxTasks}
                readOnly
              />
            </div>
            <div className="field" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
              <label style={{ whiteSpace: "nowrap" }}>重试次数</label>
              <input
                className="input"
                style={{ width: 60, fontFamily: "var(--font-mono)" }}
                value={DEFAULTS.retry}
                readOnly
              />
            </div>
            <div className="field" style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
              <label style={{ whiteSpace: "nowrap" }}>超时（秒）</label>
              <input
                className="input"
                style={{ width: 60, fontFamily: "var(--font-mono)" }}
                value={DEFAULTS.timeout}
                readOnly
              />
            </div>
          </div>
        </div>

        <div className="card">
          <h3>输出与日志</h3>
          <div
            className="form"
            style={{
              display: "flex",
              flexDirection: "row",
              flexWrap: "wrap",
              gap: 16,
              alignItems: "center",
            }}
          >
            <div
              className="field"
              style={{ flexDirection: "row", alignItems: "center", gap: 8, flex: 1 }}
            >
              <label style={{ whiteSpace: "nowrap" }}>输出目录</label>
              <input
                className="input mono"
                style={{ flex: 1 }}
                value={DEFAULTS.outDir}
                readOnly
              />
              <button
                className="btn sm ghost"
                data-od-id="settings-pick-path"
                onClick={() => push("已选择输出目录 · " + DEFAULTS.outDir)}
              >
                选择路径
              </button>
            </div>
            <div className="field-row">
              <label>自动导出</label>
              <span className="switch">
                <input type="checkbox" checked={DEFAULTS.autoExport} readOnly />
                <i />
              </span>
            </div>
            <div className="field-row">
              <label>日志写文件</label>
              <span className="switch">
                <input type="checkbox" checked={DEFAULTS.logFile} readOnly />
                <i />
              </span>
            </div>
          </div>
        </div>
      </div>

      <div
        style={{
          background: "var(--panel)",
          border: "1px solid var(--line)",
          borderRadius: 12,
          padding: "14px 16px",
          marginBottom: 14,
        }}
      >
        <div style={{ fontSize: 13, fontWeight: 700, marginBottom: 8 }}>启动策略</div>
        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: 8,
            fontSize: 13,
            color: "var(--muted)",
            cursor: "pointer",
          }}
        >
          <input
            type="checkbox"
            checked={forceRescan}
            onChange={(e) => setForceRescan(e.target.checked)}
          />
          <span>启动前强制重新扫码</span>
          <i style={{ fontSize: 12, color: "var(--muted)" }}>
            （勾选则每次启动自动私信都强制重扫忽略磁盘凭证；不勾选则复用守护进程保活的凭证快速启动）
          </i>
        </label>
      </div>

      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginTop: 20,
          marginBottom: 10,
        }}
      >
        <span style={{ fontSize: 14, fontWeight: 700, color: "var(--warn)" }}>●</span>
        <span style={{ fontSize: 14, fontWeight: 700 }}>独立配置</span>
        <span style={{ fontSize: 12, color: "var(--muted)" }}>
          开启后该账号使用独立参数，未填写的字段仍使用默认值
        </span>
      </div>

      <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        {realAccts.map((acct) => {
          const isExpanded = expandedAcct === acct.name;
          const r = acct.roles || {};
          const roleTxt =
            [r.monitor ? "监测" : "", r.sender ? "发送" : ""].filter(Boolean).join(" / ") ||
            "未分配";
          return (
            <div
              className="card"
              key={acct.name}
              style={{
                padding: 0,
                overflow: "hidden",
                borderLeft: acct.loggedIn ? "3px solid var(--ok)" : "3px solid var(--danger)",
              }}
            >
              <div
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  padding: "10px 16px",
                  cursor: "pointer",
                }}
                onClick={() => setExpandedAcct(isExpanded ? null : acct.name)}
              >
                <Avatar name={acct.name} h={hue(acct.name.length)} sm />
                <span style={{ fontWeight: 600, fontSize: 13 }}>{acct.name}</span>
                <span className="mono" style={{ fontSize: 11.5, color: "var(--muted)" }}>
                  {acct.uid || "未读入 UID"}
                </span>
                <div style={{ flex: 1 }} />
                {acct.isCurrent && <Pill c="ok">当前</Pill>}
                {r.monitor && <Pill c="ok">监测</Pill>}
                {r.sender && <Pill c="ok">发送</Pill>}
                <Pill c={acct.loggedIn ? "ok" : "danger"}>
                  {acct.loggedIn ? "已登录" : "未登录"}
                </Pill>
                <span
                  style={{
                    fontSize: 12,
                    color: "var(--muted)",
                    transition: "transform .15s",
                    transform: isExpanded ? "rotate(90deg)" : "",
                  }}
                >
                  ▶
                </span>
              </div>
              {isExpanded && (
                <div
                  style={{
                    padding: "12px 16px",
                    borderTop: "1px solid var(--border)",
                    background: "var(--surface-2)",
                  }}
                >
                  <div
                    style={{
                      fontSize: 12,
                      display: "grid",
                      gridTemplateColumns: "80px 1fr",
                      gap: "4px 8px",
                    }}
                  >
                    <span style={{ color: "var(--muted)" }}>UID</span>
                    <span className="mono" style={{ fontSize: 11 }}>
                      {acct.uid || "未读入（扫码后自动获取）"}
                    </span>
                    <span style={{ color: "var(--muted)" }}>角色</span>
                    <span className="mono" style={{ fontSize: 11 }}>
                      {roleTxt}
                    </span>
                    <span style={{ color: "var(--muted)" }}>浏览器守护</span>
                    <span className="mono" style={{ fontSize: 11 }}>
                      {acct.browserDaemonAlive ? "运行中" : "未运行"}
                    </span>
                    <span style={{ color: "var(--muted)" }}>接收守护</span>
                    <span className="mono" style={{ fontSize: 11 }}>
                      {acct.recvDaemonAlive ? "运行中" : "未运行"}
                    </span>
                  </div>
                </div>
              )}
            </div>
          );
        })}
        {realAccts.length === 0 && (
          <div
            className="card"
            style={{ padding: 36, textAlign: "center", color: "var(--muted)", fontSize: 13 }}
          >
            <div style={{ fontSize: 24, marginBottom: 6 }}>🔑</div>
            暂无账号 · 请到「账号管理」添加并扫码授权
          </div>
        )}
      </div>

      <div style={{ marginTop: 14, display: "flex", gap: 8 }}>
        <button className="btn primary" onClick={saveAll}>
          保存全部配置
        </button>
        <button
          className="btn ghost"
          onClick={() => push("重置仅作用于展示值；任务参数请到「任务中心」修改并保存")}
        >
          重置全部为默认
        </button>
      </div>
    </div>
  );
}
