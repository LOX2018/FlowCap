/**
 * 运行日志页
 *
 * 轮询后端 /api/logs 读取落盘的日志文件（logs/run_*.log），
 * 替代 Tauri 下不可见的 CMD 窗口输出。
 *
 * 功能：
 * - 本次日志：只显示最新一次启动会话（run_*.log 最新文件），实时追加。
 * - 历史日志：列出全部历史启动会话，可切换查看 / 批量删除（批量管理）。
 * - 清空显示：仅清空前端视图，不触碰磁盘实质日志；清空后只显示新产生的行。
 */
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { PageProps } from "../api/client";

interface LogLine {
  ts: string;
  level: string;
  text: string;
}

interface Session {
  file: string;
  start: string;
  size: number;
  mtime: number;
}

const LEVEL_COLOR: Record<string, string> = {
  INFO: "var(--muted)",
  DEBUG: "var(--muted)",
  WARNING: "var(--warn)",
  ERROR: "var(--danger)",
  SUCCESS: "var(--ok)",
};

/** 将 20260815_123045 格式化为 2026-08-15 12:30:45 */
function fmtStart(start: string): string {
  if (start.length >= 15) {
    const d = start.slice(0, 8);
    const t = start.slice(9);
    return `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)} ${t.slice(0, 2)}:${t.slice(2, 4)}:${t.slice(4, 6)}`;
  }
  return start;
}

function fmtSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}

export default function LogsPage(props: PageProps) {
  const { api, ready } = props;
  const qc = useQueryClient();
  const [autoScroll, setAutoScroll] = useState(true);
  const [limit, setLimit] = useState(500);
  // 视图模式：current=本次日志，history=历史会话列表
  const [mode, setMode] = useState<"current" | "history">("current");
  // 当前查看的历史会话文件（点历史项进入查看）
  const [viewFile, setViewFile] = useState<string | null>(null);
  // 清空显示标记：持久化到 localStorage，切页重挂后继续隐藏旧行
  const [displayCleared, setDisplayCleared] = useState(
    () => localStorage.getItem("dy:logcleared") === "1",
  );
  // 清空时刻（HH:MM:SS），只显示晚于此刻之后的新行（按日志 ts 字符串比较）
  const [clearTs, setClearTs] = useState<string | null>(
    () => localStorage.getItem("dy:logclearts") || null,
  );
  // 历史会话多选
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const endRef = useRef<HTMLDivElement | null>(null);

  // 本次日志轮询（只读最新会话文件）
  const curQ = useQuery({
    queryKey: ["logs-current", limit],
    queryFn: async (): Promise<{ ok: boolean; file: string | null; lines: LogLine[] }> => {
      const s = await api.getSessions();
      const name = s.current;
      const d = await api.getLogs(limit, name || undefined);
      return d as unknown as { ok: boolean; file: string | null; lines: LogLine[] };
    },
    refetchInterval: 2000,
    enabled: !!ready && mode === "current",
  });

  // 历史会话列表轮询
  const sessQ = useQuery({
    queryKey: ["logs-sessions"],
    queryFn: async () => await api.getSessions(),
    refetchInterval: 5000,
    enabled: !!ready,
  });

  // 查看某历史会话时的日志轮询
  const histQ = useQuery({
    queryKey: ["logs-history", viewFile, limit],
    queryFn: async (): Promise<{ ok: boolean; file: string | null; lines: LogLine[] }> => {
      const d = await api.getLogs(limit, viewFile || undefined);
      return d as unknown as { ok: boolean; file: string | null; lines: LogLine[] };
    },
    refetchInterval: 2000,
    enabled: !!ready && mode === "history" && !!viewFile,
  });

  // 当前显示的数据源
  const activeQ = mode === "history" && viewFile ? histQ : curQ;
  const lines = activeQ.data?.lines || [];

  useEffect(() => {
    if (autoScroll && endRef.current) {
      endRef.current.scrollIntoView({ block: "end" });
    }
  }, [lines, autoScroll]);

  const sessions: Session[] = sessQ.data?.sessions || [];
  const currentFile = sessQ.data?.current || null;
  const historySessions = sessions.filter((s) => s.file !== currentFile);

  const toggleSelect = (file: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(file)) next.delete(file);
      else next.add(file);
      return next;
    });
  };

  const doDeleteSelected = async () => {
    const files = Array.from(selected);
    if (files.length === 0) {
      props.push("请先勾选要删除的历史会话");
      return;
    }
    // 乐观更新：删除前立即从列表隐藏选中项，消除删除后的可见延迟
    const delSet = new Set(files);
    qc.setQueryData(
      ["logs-sessions"],
      (old: { ok: boolean; current: string | null; sessions: Session[] } | undefined) => {
        if (!old) return old;
        return {
          ...old,
          sessions: old.sessions.filter((s) => !delSet.has(s.file)),
        };
      },
    );
    setSelected(new Set());
    const r = await api.deleteSessions(files);
    if (r.ok) {
      props.push(`已删除 ${r.deleted.length} 个历史会话${r.skipped.length ? `，跳过 ${r.skipped.length} 个` : ""}`);
    } else {
      props.push("删除失败");
      sessQ.refetch(); // 失败回滚：重新拉取真实状态
    }
  };

  // 清空显示：只显示晚于清空时刻(clearTs)的新行；旧行隐藏。
  // clearTs/displayCleared 持久化在 localStorage，切页重挂后仍生效，旧行不再回流。
  const shownLines = displayCleared
    ? lines.filter((l) => clearTs != null && l.ts > clearTs)
    : lines;

  return (
    <div>
      <div className="section-head">
        <div>
          <h2>运行日志</h2>
          <div className="desc">
            实时读取后端落盘的日志文件 · {activeQ.data?.file ? activeQ.data.file : "等待数据"}
          </div>
        </div>
        {/* 2026-09-10：页头连接状态删除，统一在顶栏会员徽章右侧显示 */}
      </div>

      {/* 模式切换 + 批量管理 */}
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          marginBottom: 10,
          fontSize: 13,
          color: "var(--muted)",
          flexWrap: "wrap",
        }}
      >
        <div style={{ display: "flex", gap: 6 }}>
          <button
            className={"btn sm" + (mode === "current" ? " accent" : " ghost")}
            onClick={() => {
              setMode("current");
              setViewFile(null);
            }}
          >
            本次日志
          </button>
          <button
            className={"btn sm" + (mode === "history" ? " accent" : " ghost")}
            onClick={() => setMode("history")}
          >
            历史日志（{historySessions.length}）
          </button>
        </div>

        {mode === "history" && (
          <>
            <div style={{ flex: 1 }} />
            <span style={{ fontSize: 12 }}>
              已选 {selected.size}
            </span>
            <button
              className="btn sm ghost"
              onClick={() => setSelected(new Set(historySessions.map((s) => s.file)))}
            >
              全选
            </button>
            <button
              className="btn sm ghost"
              onClick={() => setSelected(new Set())}
            >
              清除选择
            </button>
            <button
              className="btn sm danger"
              onClick={doDeleteSelected}
              disabled={selected.size === 0}
            >
              批量删除
            </button>
          </>
        )}

        {mode === "current" && (
          <>
            <div style={{ flex: 1 }} />
            <button
              className="btn sm ghost"
              onClick={() => {
                const now = new Date();
                const ts = [
                  String(now.getHours()).padStart(2, "0"),
                  String(now.getMinutes()).padStart(2, "0"),
                  String(now.getSeconds()).padStart(2, "0"),
                ].join(":");
                setDisplayCleared(true);
                setClearTs(ts);
                localStorage.setItem("dy:logcleared", "1");
                localStorage.setItem("dy:logclearts", ts);
                props.push("已清空显示（实质日志未删除）");
              }}
            >
              清空显示
            </button>
            {displayCleared && (
              <button
                className="btn sm ghost"
                onClick={() => {
                  setDisplayCleared(false);
                  setClearTs(null);
                  localStorage.removeItem("dy:logcleared");
                  localStorage.removeItem("dy:logclearts");
                }}
              >
                恢复显示
              </button>
            )}
          </>
        )}
      </div>

      {mode === "history" && !viewFile && (
        <div
          className="card"
          style={{ padding: 0, overflow: "hidden", border: "1px solid var(--line)", marginBottom: 10 }}
        >
          {historySessions.length === 0 && (
            <div style={{ color: "var(--muted)", padding: "20px 14px" }}>暂无历史会话（只有本次启动）</div>
          )}
          {historySessions.map((s) => (
            <div
              key={s.file}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                padding: "10px 14px",
                borderBottom: "1px solid var(--line)",
                fontSize: 13,
              }}
            >
              <input
                type="checkbox"
                checked={selected.has(s.file)}
                onChange={() => toggleSelect(s.file)}
              />
              <div style={{ flex: 1, cursor: "pointer" }} onClick={() => setViewFile(s.file)}>
                <div style={{ color: "var(--text)", fontWeight: 600 }}>{fmtStart(s.start)}</div>
                <div className="mono" style={{ color: "var(--muted)", fontSize: 11 }}>
                  {s.file} · {fmtSize(s.size)}
                </div>
              </div>
              <button
                className="btn sm ghost"
                onClick={() => setViewFile(s.file)}
              >
                查看
              </button>
              <button
                className="btn sm danger"
                onClick={async () => {
                  const r = await api.deleteSessions([s.file]);
                  if (r.ok && r.deleted.length) props.push(`已删除 ${s.file}`);
                  else props.push("删除失败（本次会话不可删）");
                }}
              >
                删除
              </button>
            </div>
          ))}
        </div>
      )}

      {mode === "history" && viewFile && (
        <div style={{ marginBottom: 10 }}>
          <button className="btn sm ghost" onClick={() => setViewFile(null)}>
            ← 返回历史列表
          </button>
          <span className="mono" style={{ marginLeft: 10, fontSize: 12, color: "var(--muted)" }}>
            {viewFile}
          </span>
        </div>
      )}

      {/* 行数上限 + 自动滚动（仅日志视图显示） */}
      {(mode === "current" || !!viewFile) && (
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
                ?.writeText(shownLines.map((l) => `${l.ts} | ${l.level} | ${l.text}`).join("\n"))
                .then(() => props.push("日志已复制到剪贴板"))
                .catch(() => props.push("复制失败"));
            }}
          >
            复制全部
          </button>
          <span className="mono" style={{ fontSize: 12 }}>
            共 {shownLines.length} 行
          </span>
        </div>
      )}

      <div
        className="card"
        style={{
          padding: 0,
          overflow: "hidden",
          border: "1px solid var(--line)",
          display: mode === "history" && !viewFile ? "none" : "block",
        }}
      >
        <div
          className="mono"
          style={{
            height: "calc(100vh - 300px)",
            minHeight: 320,
            overflowY: "auto",
            background: "var(--bg)",
            padding: "10px 14px",
            fontSize: 12.5,
            lineHeight: 1.7,
          }}
        >
          {displayCleared && shownLines.length === 0 && (
            <div style={{ color: "var(--muted)", padding: "20px 4px" }}>
              显示已清空（实质日志保留）· 新日志将从这里开始显示
            </div>
          )}
          {!displayCleared && lines.length === 0 && (
            <div style={{ color: "var(--muted)", padding: "20px 4px" }}>
              {activeQ.isLoading ? "加载中…" : "暂无日志（后端尚未产生运行记录）"}
            </div>
          )}
          {shownLines.map((l, i) => (
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
