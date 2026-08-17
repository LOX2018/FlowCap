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
 *
 * 布局改版（本版）：
 *   - 取消宫格（grid cols-5），改为「子导航栏 + 可收缩列表」结构
 *   - 子导航栏：默认配置 / 启动策略 / 独立账号，点击切换区块
 *   - 默认配置下的每个分类（采集参数 / 直播监听策略 / 私信同步 / 任务管理 / 输出与日志）
 *     均为可收缩列表项，默认收缩，点击标题单词展开
 *   - 启动策略 / 独立账号 区块同样默认收缩
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

/** 可收缩列表项：标题栏（点击展开/收缩）+ 内容 */
function Collapsible(props: {
  title: string;
  subtitle?: string;
  defaultOpen?: boolean;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(!!props.defaultOpen);
  return (
    <div
      style={{
        background: "var(--panel)",
        border: "1px solid var(--line)",
        borderRadius: 10,
        marginBottom: 10,
        overflow: "hidden",
      }}
    >
      <div
        onClick={() => setOpen(!open)}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "10px 14px",
          cursor: "pointer",
          userSelect: "none",
        }}
      >
        <span
          style={{
            fontSize: 12,
            color: "var(--muted)",
            transition: "transform .15s",
            transform: open ? "rotate(90deg)" : "",
          }}
        >
          ▶
        </span>
        <span style={{ fontWeight: 700, fontSize: 13.5 }}>{props.title}</span>
        {props.subtitle && (
          <span style={{ fontSize: 12, color: "var(--muted)" }}>{props.subtitle}</span>
        )}
        <div style={{ flex: 1 }} />
        <span style={{ fontSize: 11, color: "var(--muted)" }}>{open ? "收起" : "展开"}</span>
      </div>
      {open && (
        <div
          style={{
            padding: "4px 14px 12px",
            borderTop: "1px solid var(--border)",
            background: "var(--surface-2)",
          }}
        >
          {props.children}
        </div>
      )}
    </div>
  );
}

/** 单字段行（列表展示，非输入框宫格） */
function Row(props: { label: string; value: string; hint?: string }) {
  return (
    <div
      style={{
        display: "flex",
        alignItems: "baseline",
        gap: 10,
        padding: "6px 0",
        borderBottom: "1px dashed var(--border)",
        fontSize: 13,
      }}
    >
      <span style={{ width: 140, color: "var(--muted)", flex: "none" }}>{props.label}</span>
      <span className="mono" style={{ flex: 1, wordBreak: "break-all" }}>
        {props.value}
      </span>
      {props.hint && (
        <span style={{ fontSize: 11, color: "var(--muted)", flex: "none", maxWidth: 220 }}>
          {props.hint}
        </span>
      )}
    </div>
  );
}

export default function SettingsPage(props: PageProps) {
  const { push, api, ready } = props;
  const [expandedAcct, setExpandedAcct] = useState<string | null>(null);
  const [forceRescan, setForceRescan] = useState(false);
  // 子导航：默认配置 / 启动策略 / 独立账号
  const [section, setSection] = useState<"default" | "strategy" | "accounts">("default");

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
    exportNote: "统计导出为 xlsx，运行日志落盘 logs/",
  };

  const saveAll = () => {
    api.saveConfig({ forceRescan })
      .then((r) => push(r && r.ok ? "配置已保存" : "保存失败"))
      .catch((e) => push("失败:保存异常 " + errMsg(e)));
  };

  const navBtn = (key: "default" | "strategy" | "accounts", label: string, count?: number) => (
    <button
      className={"btn sm" + (section === key ? " accent" : " ghost")}
      onClick={() => setSection(key)}
    >
      {label}
      {count != null ? `（${count}）` : ""}
    </button>
  );

  return (
    <div>
      <div className="section-head">
        <div>
          <h2>设置</h2>
          <div className="desc">默认配置适用于全部账号，可为单个账号开启独立配置覆盖默认值</div>
        </div>
        <span className="demo-tag">{ready ? "已连接" : "未连接"}</span>
      </div>

      {/* 子导航栏 */}
      <div
        style={{
          display: "flex",
          gap: 6,
          marginBottom: 12,
          borderBottom: "1px solid var(--line)",
          paddingBottom: 8,
        }}
      >
        {navBtn("default", "默认配置")}
        {navBtn("strategy", "启动策略")}
        {navBtn("accounts", "独立账号", realAccts.length)}
      </div>

      {section === "default" && (
        <div>
          <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 8 }}>
            以下为各分类参数的当前默认值，点击标题展开查看详情（默认收缩）
          </div>

          <Collapsible title="采集参数" subtitle="直播间评论 / 弹幕捕获相关">
            <Row label="请求频率限制" value={DEFAULTS.rateLimit} hint="两次请求最小间隔（ms）" />
            <Row label="搜索结果上限" value={DEFAULTS.searchCap} />
            <Row label="导出格式" value={String(DEFAULTS.exportFmt).toUpperCase()} />
          </Collapsible>

          <Collapsible title="直播监听策略" subtitle="自动私信节奏">
            <Row label="每场私信上限" value={DEFAULTS.dmPerLive} hint="每场直播最多发送条数" />
            <Row label="私信间隔（秒）" value={DEFAULTS.dmInterval} />
            <Row label="点赞间隔（秒）" value={DEFAULTS.likeInterval} />
          </Collapsible>

          <Collapsible title="私信同步" subtitle="IM 长连接接收">
            <Row label="断线重连（秒）" value={DEFAULTS.wsReconnect} />
            <Row label="单次同步上限" value={DEFAULTS.syncLimit} />
            <Row label="接收图片/视频" value={DEFAULTS.recvMedia ? "开启" : "关闭"} />
          </Collapsible>

          <Collapsible title="任务管理" subtitle="并发 / 重试 / 超时">
            <Row label="最大并发" value={DEFAULTS.maxTasks} />
            <Row label="重试次数" value={DEFAULTS.retry} />
            <Row label="超时（秒）" value={DEFAULTS.timeout} />
          </Collapsible>

          <Collapsible title="输出与日志" subtitle="落盘位置">
            <Row label="输出目录" value={DEFAULTS.outDir} />
            <Row label="说明" value={DEFAULTS.exportNote} />
          </Collapsible>
        </div>
      )}

      {section === "strategy" && (
        <div>
          <Collapsible title="启动策略" subtitle="启动自动私信时的凭证行为" defaultOpen>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                fontSize: 13,
                color: "var(--muted)",
                cursor: "pointer",
                padding: "4px 0",
              }}
            >
              <span className="switch" style={{ flex: "none" }}>
                <input
                  type="checkbox"
                  checked={forceRescan}
                  onChange={(e) => setForceRescan(e.target.checked)}
                />
                <i />
              </span>
              <span style={{ color: "var(--text)" }}>启动前强制重新扫码</span>
              <i style={{ fontSize: 12, color: "var(--muted)" }}>
                （勾选则每次启动自动私信都强制重扫忽略磁盘凭证；不勾选则复用守护进程保活的凭证快速启动）
              </i>
            </div>
          </Collapsible>
        </div>
      )}

      {section === "accounts" && (
        <div>
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 8,
              marginBottom: 10,
              fontSize: 13,
              color: "var(--muted)",
            }}
          >
            <span>开启后该账号使用独立参数覆盖默认值</span>
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
        </div>
      )}

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
