/**
 * 运行日志页
 *
 * 轮询后端 /api/logs 读取落盘的日志文件（logs/run_*.log），
 * 替代 Tauri 下不可见的 CMD 窗口输出。
 */
import { useEffect, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { PageProps } from "../api/client";

interface LogLine {
  ts: string;
  level: string;
  text: string;
}

const LEVEL_COLOR: Record<string, string> = {
  INFO: "var(--muted)",
  DEBUG: "var(--muted)",
  WARNING: "var(--warn)",
  ERROR: "var(--danger)",
  SUCCESS: "var(--ok)",
};

export default function LogsPage(props: PageProps) {
  const { api, ready } = props;
  const [autoScroll, setAutoScroll] = useState(true);
  const [limit, setLimit] = useState(500);
  const endRef = useRef<HTMLDivElement | null>(null);

  const q = useQuery({
    queryKey: ["logs", limit],
    queryFn: async (): Promise<{ ok: boolean; file: string | null; lines: LogLine[] }> => {
      const d = await api.getLogs(limit);
      return d as unknown as { ok: boolean; file: string | null; lines: LogLine[] };
    },
    refetchInterval: 2000,
    enabled: !!ready,
  });

  useEffect(() => {
    if (autoScroll && endRef.current) {
      endRef.current.scrollIntoView({ block: "end" });
    }
  }, [q.data, autoScroll]);

  const lines = q.data?.lines || [];

  return (
    <div>
      <div className="section-head">
        <div>
          <h2>运行日志</h2>
          <div className="desc">
            实时读取后端落盘的日志文件 · {q.data?.file ? q.data.file : "等待数据"}
          </div>
        </div>
        <span className="demo-tag">{ready ? "已连接" : "未连接"}</span>
      </div>

      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          marginBottom: 10,
          fontSize: 13,
          color: "var(--muted)",
        }}
      >
        <span>行数上限：</span>
        <select
          className="input"
          style={{ width: 90 }}
          value={limit}
          onChange={(e) => setLimit(Number(e.target.value))}
        >
          {[200, 500, 1000, 2000].map((n) => (
            <option key={n} value={n}>
              {n}
            </option>
          ))}
        </select>
        <label style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
          <input
            type="checkbox"
            checked={autoScroll}
            onChange={(e) => setAutoScroll(e.target.checked)}
          />
          自动滚动到底部
        </label>
        <div style={{ flex: 1 }} />
        <button
          className="btn sm ghost"
          onClick={() => {
            navigator.clipboard
              ?.writeText(lines.map((l) => `${l.ts} | ${l.level} | ${l.text}`).join("\n"))
              .then(() => props.push("日志已复制到剪贴板"))
              .catch(() => props.push("复制失败"));
          }}
        >
          复制全部
        </button>
        <span className="mono" style={{ fontSize: 12 }}>
          共 {lines.length} 行
        </span>
      </div>

      <div
        className="card"
        style={{
          padding: 0,
          overflow: "hidden",
          border: "1px solid var(--line)",
        }}
      >
        <div
          className="mono"
          style={{
            height: "calc(100vh - 280px)",
            minHeight: 320,
            overflowY: "auto",
            background: "var(--bg)",
            padding: "10px 14px",
            fontSize: 12.5,
            lineHeight: 1.7,
          }}
        >
          {lines.length === 0 && (
            <div style={{ color: "var(--muted)", padding: "20px 4px" }}>
              {q.isLoading ? "加载中…" : "暂无日志（后端尚未产生运行记录）"}
            </div>
          )}
          {lines.map((l, i) => (
            <div
              key={i}
              style={{
                display: "flex",
                gap: 10,
                whiteSpace: "pre-wrap",
                wordBreak: "break-all",
              }}
            >
              <span className="mono" style={{ color: "var(--muted)", flexShrink: 0 }}>
                {l.ts}
              </span>
              <span
                className="mono"
                style={{
                  color: LEVEL_COLOR[l.level] || "var(--muted)",
                  flexShrink: 0,
                  width: 64,
                }}
              >
                {l.level}
              </span>
              <span style={{ flex: 1 }}>{l.text}</span>
            </div>
          ))}
          <div ref={endRef} />
        </div>
      </div>
    </div>
  );
}
