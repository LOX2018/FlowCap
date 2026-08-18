/**
 * 任务中心页
 *
 * 迁移自: DY_Spider_base/web/pages/tasks.js
 * 原版职责: 任务列表（直播监听私信引擎）、导出管理、结构化输出目录
 * 迁移要点:
 *   - 删除 TASKS_INIT / ACCOUNTS_INIT / TASK_ST 假数据与本地进度模拟轮询
 *   - 引擎状态改用 props.overview（App 已 3s 轮询）
 *   - React.createElement → JSX
 *   - loading 用 .sk 骨架屏，未运行显示空态
 *   - api.exportStats 在 client.ts 未声明，本地扩展类型
 */
import { useEffect, useState } from "react";
import { PageProps, Overview, TaskHistoryItem, ReusePayload } from "../api/client";
import { Avatar, Pill, Dot } from "../components/ui";

type OverviewExt = Overview & {
  liveUrl?: string;
  status?: string;
  engineState?: string;
  statusMsg?: string;
};

interface ExportStatsResp {
  ok: boolean;
  path?: string;
  error?: string;
}
type Api = PageProps["api"] & {
  exportStats: () => Promise<ExportStatsResp>;
};

const errMsg = (e: unknown): string => (e instanceof Error ? e.message : String(e));

export default function TasksPage(props: PageProps) {
  const { push, overview, ready, goReuse } = props;
  const api = props.api as Api;
  const ov = (overview || ({} as OverviewExt)) as OverviewExt;
  const setTab = props.setTab;

  // 历史任务列表（5s 轮询）
  const [history, setHistory] = useState<TaskHistoryItem[]>([]);
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const r = (await api.getTaskHistory()) as { ok: boolean; list?: TaskHistoryItem[] };
        if (alive && r && r.ok) setHistory(r.list || []);
      } catch {
        /* 忽略轮询错误 */
      }
    };
    load();
    const t = setInterval(load, 5000);
    return () => {
      alive = false;
      clearInterval(t);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready]);

  // 跳转：运行中任务 -> 直播监听页；历史任务 -> 直播监听页进入查阅模式查看结果
  const gotoTask = (item: TaskHistoryItem) => {
    if (!setTab) return;
    if (item.status === "running") {
      push("已跳转到直播监听页（该任务运行中）");
      setTab("live");
      return;
    }
    // 历史任务：跳转直播监听页并进入查阅模式
    push("已跳转到直播监听页查阅模式，查看任务运行结果");
    setTab("live");
    // 延迟让 live 页挂载后再进入查阅模式（传 records 快照）
    setTimeout(() => {
      if (props.goReview) {
        props.goReview({
          acct: item.acct || "",
          liveId: item.live_id || "",
          records: item.records || [],
          startTs: item.start_ts,
          endTs: item.end_ts,
        });
      }
    }, 150);
  };

  // 复用：用历史任务保存的配置快照预填直播监听页（重新运行同参数任务）
  const reuseTask = (item: TaskHistoryItem) => {
    const cfg = (item.config || {}) as ReusePayload & {
      live_url?: string;
      max_target?: number;
      live_id?: string;
      dm_pool?: { text: string; enabled: boolean }[];
    };
    if (!goReuse) {
      push("当前无法复用（缺少复用入口），请手动到直播监听页配置");
      return;
    }
    goReuse({
      room: cfg.live_url || cfg.live_id || "",
      maxTarget: cfg.max_target ?? cfg.maxTarget,
      interval: cfg.interval,
      delay: cfg.delay,
      dmPool: Array.isArray(cfg.dm_pool) ? cfg.dm_pool : cfg.dmPool,
      forceRescan: cfg.forceRescan,
      acct: cfg.acct,
    });
    push(`已复用任务「${item.acct || ""}」配置到直播监听页`);
  };

  return (
    <div>
      <div className="section-head">
        <div>
          <h2>任务中心</h2>
          <div className="desc">采集 / 监听 / 导出任务与断线重连状态</div>
        </div>
        <div className="head-row">
          {ready ? (
            <span className="badge-conn">
              <Dot c={ov.running ? (ov.paused ? "warn" : "ok") : "danger"} pulse={!!ov.running && !ov.paused} />{" "}
              已发 {ov.sent || 0}/{ov.limit || 0}
              {ov.queue ? " · 待发 " + ov.queue : ""}
            </span>
          ) : (
            <span className="demo-tag">未连接</span>
          )}
          {ready && (
            <button
              className="btn sm primary"
              onClick={() =>
                api
                  .exportStats()
                  .then((r) =>
                    push(
                      r && r.ok
                        ? "统计已导出 · " + (r.path || "")
                        : "导出失败: " + ((r && r.error) || ""),
                    ),
                  )
                  .catch((e: unknown) => push("导出异常: " + errMsg(e)))
              }
            >
              导出统计 xlsx
            </button>
          )}
        </div>
      </div>

      <div className="card" style={{ marginBottom: 14, padding: 0 }} data-od-id="task-list">
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th>创建时间</th>
                <th>账号</th>
                <th>任务类型</th>
                <th>目标</th>
                <th>状态</th>
                <th>耗时</th>
                <th>结果条数</th>
                <th>重试</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {ready && ov.running ? (
                <tr key="live-task">
                  <td className="mono" style={{ whiteSpace: "nowrap" }}>
                    {ov.status || "—"}
                  </td>
                  <td>
                    <div style={{ display: "inline-flex", alignItems: "center", gap: 7 }}>
                      <Avatar name="引擎" h="160" sm />
                      <span>自动私信引擎</span>
                    </div>
                  </td>
                  <td>直播监听私信</td>
                  <td className="mono" style={{ color: "var(--muted)" }}>
                    {ov.liveUrl || "—"}
                  </td>
                  <td>
                    {ov.engineState === "stopping" ? (
                      <Pill c="warn">私信收尾中</Pill>
                    ) : ov.engineState === "starting" ? (
                      <Pill c="warn">启动中…</Pill>
                    ) : (
                      <Pill c={ov.paused ? "warn" : "ok"}>
                        {ov.paused ? "已暂停" : "运行中"}
                      </Pill>
                    )}
                    {ov.statusMsg && ov.engineState === "stopping" && (
                      <div className="mono" style={{ fontSize: 11, color: "var(--muted)" }}>
                        {ov.statusMsg}
                      </div>
                    )}
                  </td>
                  <td className="mono">
                    {ov.sent || 0}/{ov.limit || 0}
                  </td>
                  <td className="mono">{ov.queue || 0}</td>
                  <td className="mono">0</td>
                  <td>
                    <button
                      className="btn text sm"
                      onClick={() => {
                        if (setTab) {
                          setTab("live");
                          push("已跳转到直播监听页（该任务运行中）");
                        }
                      }}
                    >
                      进入任务
                    </button>
                    {ov.engineState === "stopping" ? (
                      <button
                        className="btn text sm"
                        style={{ color: "var(--danger)" }}
                        title="立即终止仍在发送的存量私信"
                        onClick={() =>
                          api
                            .stop()
                            .then(() => push("已硬停止，存量私信终止发送"))
                            .catch((e: unknown) => push("异常: " + errMsg(e)))
                        }
                      >
                        停止存量
                      </button>
                    ) : ov.paused ? (
                      <button
                        className="btn text sm"
                        onClick={() =>
                          api
                            .resume()
                            .then(() => push("已继续"))
                            .catch((e: unknown) => push("异常: " + errMsg(e)))
                        }
                      >
                        继续
                      </button>
                    ) : (
                      <button
                        className="btn text sm"
                        onClick={() =>
                          api
                            .pause()
                            .then(() => push("已暂停"))
                            .catch((e: unknown) => push("异常: " + errMsg(e)))
                        }
                      >
                        暂停
                      </button>
                    )}
                    <button
                      className="btn text sm"
                      style={{ color: "var(--danger)" }}
                      onClick={() =>
                        api
                          .stop()
                          .then(() => push("已停止"))
                          .catch((e: unknown) => push("异常: " + errMsg(e)))
                      }
                    >
                      停止
                    </button>
                  </td>
                </tr>
              ) : !ready ? (
                [0, 1, 2].map((i) => (
                  <tr key={i}>
                    <td className="sk" style={{ height: 28 }} />
                    <td className="sk" style={{ height: 28 }} />
                    <td className="sk" style={{ height: 28 }} />
                    <td className="sk" style={{ height: 28 }} />
                    <td className="sk" style={{ height: 28 }} />
                    <td className="sk" style={{ height: 28 }} />
                    <td className="sk" style={{ height: 28 }} />
                    <td className="sk" style={{ height: 28 }} />
                    <td className="sk" style={{ height: 28 }} />
                  </tr>
                ))
              ) : (
                <tr>
                  <td
                    colSpan={9}
                    style={{ color: "var(--muted)", textAlign: "center", padding: "18px 10px" }}
                  >
                    暂无运行中任务
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* 历史任务 */}
      <div className="card" style={{ padding: 0 }}>
        <div className="section-head" style={{ padding: "12px 14px 4px", marginBottom: 0 }}>
          <div>
            <h2 style={{ margin: 0, fontSize: 15 }}>历史任务</h2>
<div className="desc" style={{ fontSize: 12 }}>
                每次启动的独立运行记录；运行中任务可跳转直播监听页，历史任务可跳转查阅模式查看结果（双击行同样进入查阅模式）
              </div>
          </div>
          {history.length > 0 && (
            <button
              className="btn text sm"
              style={{ color: "var(--danger)" }}
              onClick={() =>
                api
                  .clearTaskHistory()
                  .then(() => setHistory([]))
                  .catch(() => {})
              }
            >
              清空
            </button>
          )}
        </div>
        <div className="table-scroll">
          <table className="table">
            <thead>
              <tr>
                <th>开始时间</th>
                <th>账号</th>
                <th>直播间</th>
                <th>状态</th>
                <th>结果条数</th>
                <th>结束时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {!ready ? (
                <tr>
                  <td colSpan={7} style={{ color: "var(--muted)", textAlign: "center", padding: "16px 10px" }}>
                    未连接
                  </td>
                </tr>
              ) : history.length === 0 ? (
                <tr>
                  <td colSpan={7} style={{ color: "var(--muted)", textAlign: "center", padding: "16px 10px" }}>
                    暂无历史任务
                  </td>
                </tr>
              ) : (
                history.map((h) => (
                  <tr
                    key={h.id}
                    style={{ cursor: "pointer" }}
                    onDoubleClick={() => gotoTask(h)}
                    title="双击进入任务 / 查看结果查阅模式"
                  >
                    <td className="mono" style={{ whiteSpace: "nowrap" }}>
                      {h.start_ts || "—"}
                    </td>
                    <td>{h.acct || "—"}</td>
                    <td className="mono" style={{ color: "var(--muted)" }}>
                      {h.live_id || "—"}
                    </td>
                    <td>
                      <Pill c={h.status === "running" ? "ok" : h.status === "finished" ? "ok" : "warn"}>
                        {h.status === "running"
                          ? "运行中"
                          : h.status === "finished"
                            ? "已完成"
                            : "已停止"}
                      </Pill>
                    </td>
                    <td className="mono">{h.result_count || 0}</td>
                    <td className="mono" style={{ whiteSpace: "nowrap" }}>
                      {h.end_ts || "—"}
                    </td>
                    <td>
                      <button className="btn sm" onClick={() => gotoTask(h)}>
                        {h.status === "running" ? "进入任务" : "查看结果"}
                      </button>
                      <button
                        className="btn sm ghost"
                        style={{ marginLeft: 6 }}
                        onClick={() => reuseTask(h)}
                        title="复用该任务启动时的配置，重新运行"
                      >
                        复用
                      </button>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

    </div>
  );
}
