/**
 * 设置页
 *
 * 迁移自: DY_Spider_base/web/pages/settings.js
 * 原版职责: 配置保存、账号查看
 * 迁移要点:
 *   - 旧版 setInterval 轮询 tasks/accounts → 改用 useQuery + refetchInterval
 *   - 旧版 window.ApiBridge.ready → props.ready
 *   - React.createElement → JSX
 *   - 配置写回改为 FastAPI 后端统一处理（saveTaskConfig），不再提及 config.py
 *
 * 布局改版（本版）：
 *   - 取消宫格（grid cols-5），改为「子导航栏 + 可收缩列表」结构
 *   - 子导航栏：默认配置 / 启动策略 / 独立账号，点击切换区块
 *   - 默认配置下的每个分类均为可收缩列表项，点击标题展开查看/编辑
 *   - 每个字段为可编辑输入框，实时修改后点击「保存全部配置」生效
 */
import { useState, useEffect, useCallback } from "react";
import { useQuery } from "@tanstack/react-query";
import { PageProps } from "../api/client";
import { Avatar, Pill, hue } from "../components/ui";
import UnifiedConfigSection from "../components/UnifiedConfigSection";
import AgentSection from "../components/AgentSection";

interface Account {
  name: string;
  uid?: string;
  isCurrent?: boolean;
  loggedIn?: boolean;
  roles?: { monitor?: boolean; sender?: boolean };
  browserDaemonAlive?: boolean;
  recvDaemonAlive?: boolean;
}

interface TasksData {
  ok?: boolean;
  maxTarget?: number;
  interval?: number;
  delay?: string;
  forceRescan?: boolean;
  enableDanmaku?: boolean;
  enableConsole?: boolean;
  enableSend?: boolean;
}

function errMsg(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

/** 可收缩列表项：标题栏（点击展开/收缩）+ 内容 + 可选底部操作区 */
function Collapsible(props: {
  title: string;
  subtitle?: string;
  defaultOpen?: boolean;
  footer?: React.ReactNode;
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
            padding: "12px 14px",
            borderTop: "1px solid var(--border)",
            background: "var(--surface-2)",
          }}
        >
          {/* 参数项目自动换行：每个气泡 flex:1 0 220px 独立包裹，不固定宫格 */}
          <div style={{ display: "flex", flexWrap: "wrap", gap: 12 }}>{props.children}</div>
          {props.footer && (
            <div
              style={{
                display: "flex",
                justifyContent: "flex-end",
                marginTop: 12,
                paddingTop: 10,
                borderTop: "1px dashed var(--border)",
              }}
            >
              {props.footer}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** 收缩内容区的参数气泡包装：自适应宽度，上下排列 label/输入/hint */
function FieldWrap(props: { children: React.ReactNode }) {
  return (
    <div
      style={{
        flex: "1 0 220px",
        minWidth: 0,
        maxWidth: "100%",
      }}
    >
      {props.children}
    </div>
  );
}

/** 可编辑字段卡片 */
function EditField(props: {
  label: string;
  value: string;
  onChange: (val: string) => void;
  type?: "text" | "number";
  hint?: string;
}) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 2,
        padding: "8px 10px",
        background: "var(--surface)",
        borderRadius: 8,
        border: "1px solid var(--border)",
        fontSize: 12.5,
      }}
    >
      <span style={{ color: "var(--muted)", fontSize: 11.5 }}>{props.label}</span>
      <input
        className="mono"
        type={props.type || "text"}
        value={props.value}
        onChange={(e) => props.onChange(e.target.value)}
        style={{
          width: "100%",
          background: "var(--surface-2)",
          border: "1px solid var(--border)",
          borderRadius: 4,
          padding: "4px 6px",
          fontSize: 12,
          color: "var(--text)",
          outline: "none",
        }}
      />
      {props.hint && (
        <span style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>{props.hint}</span>
      )}
    </div>
  );
}

/** 开关字段卡片 */
function SwitchField(props: {
  label: string;
  checked: boolean;
  onChange: (val: boolean) => void;
  hint?: string;
}) {
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        gap: 2,
        padding: "8px 10px",
        background: "var(--surface)",
        borderRadius: 8,
        border: "1px solid var(--border)",
        fontSize: 12.5,
      }}
    >
      <span style={{ color: "var(--muted)", fontSize: 11.5 }}>{props.label}</span>
      <label className="switch" style={{ marginTop: 2 }}>
        <input
          type="checkbox"
          checked={props.checked}
          onChange={(e) => props.onChange(e.target.checked)}
        />
        <i />
      </label>
      {props.hint && (
        <span style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>{props.hint}</span>
      )}
    </div>
  );
}

export default function SettingsPage(props: PageProps) {
  const { push, api, ready } = props;
  const [expandedAcct, setExpandedAcct] = useState<string | null>(null);

  // 子导航：默认配置 / 启动策略 / 独立账号 / 通用配置
  const [section, setSection] = useState<
    "default" | "strategy" | "accounts" | "unified" | "agent"
  >("default");

  // 从 tasks API 加载运行时配置
  const tasksQ = useQuery({
    queryKey: ["settings-tasks"],
    queryFn: async (): Promise<TasksData | null> => {
      const d = (await api.getTasks()) as unknown as TasksData;
      return d && d.ok ? d : null;
    },
    refetchInterval: 8000,
    enabled: !!ready,
  });
  const tc = tasksQ.data || {};

  // 本地编辑态state（带默认值，避免输入框为空需手动填写）
  const [maxTarget, setMaxTarget] = useState("3");
  const [dmInterval, setDmInterval] = useState("60");
  const [delay, setDelay] = useState("40,65");
  const [forceRescan, setForceRescan] = useState(false);
  const [enableDanmaku, setEnableDanmaku] = useState(true);
  const [enableConsole, setEnableConsole] = useState(true);
  const [enableSend, setEnableSend] = useState(true);
  // 标记是否已做首次初始化
  const [initDone, setInitDone] = useState(false);

  // 只要 tasks API 数据变化就同步到本地编辑态（仅首次及数据真变时）
  useEffect(() => {
    if (!tc) return;
    setMaxTarget((prev) => (initDone || prev !== "3" ? prev : tc.maxTarget != null ? String(tc.maxTarget) : "3"));
    setDmInterval((prev) => (initDone || prev !== "60" ? prev : tc.interval != null ? String(tc.interval) : "60"));
    setDelay((prev) => (initDone || prev !== "40,65" ? prev : tc.delay != null ? tc.delay : "40,65"));
    setForceRescan((prev) => (initDone ? prev : tc.forceRescan ?? false));
    setEnableDanmaku((prev) => (initDone ? prev : tc.enableDanmaku ?? true));
    setEnableConsole((prev) => (initDone ? prev : tc.enableConsole ?? true));
    setEnableSend((prev) => (initDone ? prev : tc.enableSend ?? true));
    if (!initDone) setInitDone(true);
  }, [tc, initDone]);

  // 账号列表（读取 App 常驻轮询的共享缓存，切页不再重拉）
  const accountsQ = useQuery({
    queryKey: ["accounts"],
    queryFn: async (): Promise<Account[]> => {
      const d = await api.getAccounts();
      return (d as Account[]) || [];
    },
    enabled: !!ready,
  });
  const realAccts = accountsQ.data || [];

  // 保存「直播监听策略」分组（仅发送节奏相关字段）
  const saveStrategyGroup = useCallback(() => {
    api
      .saveTaskConfig({
        maxTarget: parseInt(maxTarget) || 3,
        interval: parseFloat(dmInterval) || 60,
        delay: delay || "40,65",
      })
      .then((r) => push(r && r.ok ? "直播监听策略已保存" : "保存失败"))
      .catch((e) => push("失败:保存异常 " + errMsg(e)));
  }, [maxTarget, dmInterval, delay, api, push]);

  // 保存「触发开关」分组（仅运行时特性开关）
  const saveSwitchesGroup = useCallback(() => {
    api
      .saveTaskConfig({ enableDanmaku, enableConsole, enableSend })
      .then((r) => push(r && r.ok ? "触发开关已保存" : "保存失败"))
      .catch((e) => push("失败:保存异常 " + errMsg(e)));
  }, [enableDanmaku, enableConsole, enableSend, api, push]);

  const saveStrategySectionGroup = useCallback(() => {
    api
      .saveTaskConfig({ forceRescan })
      .then((r) => push(r && r.ok ? "启动策略已保存" : "保存失败"))
      .catch((e) => push("失败:保存异常 " + errMsg(e)));
  }, [forceRescan, api, push]);

  // 保存全部配置（兼容原底部按钮）
  const saveAll = useCallback(() => {
    api
      .saveTaskConfig({
        maxTarget: parseInt(maxTarget) || 3,
        interval: parseFloat(dmInterval) || 60,
        delay: delay || "40,65",
        forceRescan,
        enableDanmaku,
        enableConsole,
        enableSend,
      })
      .then((r) => push(r && r.ok ? "配置已保存至 data/config.json" : "保存失败"))
      .catch((e) => push("失败:保存异常 " + errMsg(e)));
  }, [maxTarget, dmInterval, delay, forceRescan, enableDanmaku, enableConsole, enableSend, api, push]);

  const navBtn = (
    key: "default" | "strategy" | "accounts" | "unified" | "agent",
    label: string,
    count?: number,
  ) => (
    <button
      className={"btn sm" + (section === key ? " accent" : " ghost")}
      onClick={() => setSection(key)}
      style={{ width: "100%", justifyContent: "flex-start", textAlign: "left" }}
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

      {/* 主体：左侧竖式子导航栏 + 右侧内容 */}
      <div style={{ display: "flex", gap: 16, alignItems: "flex-start" }}>
        {/* 左侧竖式子导航栏 */}
        <div
          style={{
            display: "flex",
            flexDirection: "column",
            gap: 2,
            width: 130,
            flex: "none",
            position: "sticky",
            top: 0,
            border: "1px solid var(--line)",
            borderRadius: 10,
            padding: 6,
            background: "var(--panel)",
          }}
        >
          {navBtn("default", "默认配置")}
          {navBtn("strategy", "启动策略")}
          {navBtn("accounts", "独立账号", realAccts.length)}
          {navBtn("unified", "通用配置")}
          {navBtn("agent", "AI 与 Agent")}
        </div>

        {/* 右侧内容区 */}
        <div style={{ flex: 1, minWidth: 0 }}>
          {section === "default" && (
            <div>
              <div style={{ fontSize: 12, color: "var(--muted)", marginBottom: 8 }}>
                以下为各分类参数，点击标题展开后可直接编辑
              </div>

              <Collapsible
                title="直播监听策略"
                subtitle="自动私信节奏参数"
                footer={
                  <button className="btn accent sm" onClick={saveStrategyGroup}>
                    保存此分组
                  </button>
                }
              >
                <FieldWrap>
                  <EditField
                    label="每场私信上限"
                    type="number"
                    value={maxTarget}
                    onChange={setMaxTarget}
                    hint="每场直播最多发送条数"
                  />
                </FieldWrap>
                <FieldWrap>
                  <EditField
                    label="私信间隔（秒）"
                    type="number"
                    value={dmInterval}
                    onChange={setDmInterval}
                    hint="两条私信之间的最小间隔"
                  />
                </FieldWrap>
                <FieldWrap>
                  <EditField
                    label="延迟抖动范围"
                    value={delay}
                    onChange={setDelay}
                    hint="格式: min,max（如 40,65）"
                  />
                </FieldWrap>
              </Collapsible>

              <Collapsible
                title="触发开关"
                subtitle="运行时特性开关"
                footer={
                  <button className="btn accent sm" onClick={saveSwitchesGroup}>
                    保存此分组
                  </button>
                }
              >
                <FieldWrap>
                  <SwitchField
                    label="接收弹幕"
                    checked={enableDanmaku}
                    onChange={setEnableDanmaku}
                    hint="启动监听时自动接收弹幕评论"
                  />
                </FieldWrap>
                <FieldWrap>
                  <SwitchField
                    label="控制台输出"
                    checked={enableConsole}
                    onChange={setEnableConsole}
                    hint="运行日志输出到控制台"
                  />
                </FieldWrap>
                <FieldWrap>
                  <SwitchField
                    label="启用发送"
                    checked={enableSend}
                    onChange={setEnableSend}
                    hint="是否实际发送私信"
                  />
                </FieldWrap>
              </Collapsible>
            </div>
          )}

          {section === "strategy" && (
            <div>
              <Collapsible
                title="启动策略"
                subtitle="启动自动私信时的凭证行为"
                defaultOpen
                footer={
                  <button className="btn accent sm" onClick={saveStrategySectionGroup}>
                    保存此分组
                  </button>
                }
              >
                <FieldWrap>
                  <div
                    style={{
                      display: "flex",
                      alignItems: "center",
                      gap: 8,
                      fontSize: 13,
                      color: "var(--muted)",
                      cursor: "pointer",
                      padding: "8px 10px",
                      background: "var(--surface)",
                      borderRadius: 8,
                      border: "1px solid var(--border)",
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
                </FieldWrap>
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
                            display: "flex",
                            gap: 12,
                            flexWrap: "wrap",
                          }}
                        >
                          {[
                            { label: "UID", value: acct.uid || "未读入（扫码后自动获取）" },
                            { label: "角色", value: roleTxt },
                            { label: "浏览器守护", value: acct.browserDaemonAlive ? "运行中" : "未运行" },
                            { label: "接收守护", value: acct.recvDaemonAlive ? "运行中" : "未运行" },
                          ].map((item) => (
                            <div
                              key={item.label}
                              style={{
                                flex: "1 0 140px",
                                display: "flex",
                                flexDirection: "column",
                                gap: 2,
                                padding: "8px 10px",
                                background: "var(--surface)",
                                borderRadius: 8,
                                border: "1px solid var(--border)",
                              }}
                            >
                              <span style={{ color: "var(--muted)", fontSize: 11.5 }}>
                                {item.label}
                              </span>
                              <span className="mono" style={{ fontSize: 11 }}>
                                {item.value}
                              </span>
                            </div>
                          ))}
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
          {section === "unified" && <UnifiedConfigSection {...props} />}
          {section === "agent" && <AgentSection {...props} />}
        </div>
      </div>

      <div style={{ marginTop: 14, display: "flex", gap: 8 }}>
        <button className="btn primary" onClick={saveAll}>
          保存全部配置
        </button>
      </div>
    </div>
  );
}