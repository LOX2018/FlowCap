﻿/**
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
import { PageProps, Overview } from "../api/client";
import { Avatar, Pill, Dot } from "../components/ui";

type OverviewExt = Overview & { liveUrl?: string; status?: string };

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
  const { push, overview, ready } = props;
  const api = props.api as Api;
  const ov = (overview || ({} as OverviewExt)) as OverviewExt;

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
                    <Pill c={ov.paused ? "warn" : "ok"}>{ov.paused ? "已暂停" : "运行中"}</Pill>
                  </td>
                  <td className="mono">
                    {ov.sent || 0}/{ov.limit || 0}
                  </td>
                  <td className="mono">{ov.queue || 0}</td>
                  <td className="mono">0</td>
                  <td>
                    {ov.paused ? (
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

      <div className="export-grid">
        <div className="card" data-od-id="export-card">
          <h3>导出管理</h3>
          <div className="field-row" style={{ marginBottom: 10 }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 600 }}>评论数据.xlsx</div>
              <div className="mono" style={{ fontSize: 11.5, color: "var(--muted)" }}>
                8,432 行 · 2026-08-11 10:12
              </div>
            </div>
            {ready ? (
              <button
                className="btn sm ghost"
                onClick={() =>
                  api
                    .exportStats()
                    .then((r) => push(r && r.ok ? "已导出 · " + (r.path || "") : "导出失败"))
                    .catch((e: unknown) => push("异常: " + errMsg(e)))
                }
              >
                导出
              </button>
            ) : (
              <button className="btn sm ghost" onClick={() => push("已开始导出 Excel")}>
                导出
              </button>
            )}
          </div>
          <div className="field-row" style={{ marginBottom: 10 }}>
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 600 }}>用户主页数据.json</div>
              <div className="mono" style={{ fontSize: 11.5, color: "var(--muted)" }}>
                96 条 · 2026-08-11 09:40
              </div>
            </div>
            <button className="btn sm ghost" onClick={() => push("已开始导出 JSON")}>
              导出
            </button>
          </div>
          <div className="field-row">
            <div style={{ flex: 1 }}>
              <div style={{ fontWeight: 600 }}>私信会话.json</div>
              <div className="mono" style={{ fontSize: 11.5, color: "var(--muted)" }}>
                342 条 · 2026-08-11 10:01
              </div>
            </div>
            <button className="btn sm ghost" onClick={() => push("已开始导出 JSON")}>
              导出
            </button>
          </div>
        </div>
        <div className="card" data-od-id="output-tree">
          <h3>结构化输出目录</h3>
          <div className="tree">
            <div>
              <span className="dir">output/</span>
            </div>
            <div>
              {"\u3000\u251C\u2500 "}
              <span className="dir">2026-08-11/</span>
            </div>
            <div>
              {"\u3000\u2502\u3000\u251C\u2500 "}
              <span className="cur">users.json</span>
            </div>
            <div>
              {"\u3000\u2502\u3000\u251C\u2500 "}
              <span className="cur">videos/</span>
            </div>
            <div>
              {"\u3000\u2502\u3000\u2502\u3000\u2514\u2500 "}
              <span className="cur">v1/</span>
            </div>
            <div>{"\u3000\u2502\u3000\u2502\u3000\u3000\u251C\u2500 info.json"}</div>
            <div>{"\u3000\u2502\u3000\u2502\u3000\u3000\u2514\u2500 comments.xlsx"}</div>
            <div>
              {"\u3000\u2502\u3000\u251C\u2500 "}
              <span className="cur">live_7462001/</span>
            </div>
            <div>
              {"\u3000\u2502\u3000\u2502\u3000\u2514\u2500 "}
              <span className="cur">danmaku.csv</span>
            </div>
            <div>
              {"\u3000\u2502\u3000\u2514\u2500 "}
              <span className="cur">media/</span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
