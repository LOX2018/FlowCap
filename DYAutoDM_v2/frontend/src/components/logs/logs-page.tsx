/**
 * 运行日志页（重设计版 · 对标 better-douyin 设计体系）
 *
 * 轮询后端 /api/logs 读取落盘日志（logs/run_*.log），替代 Tauri 下不可见的 CMD 窗口。
 *
 * ## 本次改动（重设计）
 * - 页头/工具条/历史列表/日志视图全部改走设计令牌与新组件
 * - 旧 `.btn sm accent|ghost|danger` 手搓 → `<Button variant>` + `<SegmentedTabs>`
 * - 旧内联 `style={{color:"var(--muted)"}}` 遍布 → 令牌类
 * - 日志行级别色：`--warn/--danger/--ok` → 新语义令牌
 * - **业务逻辑零改动**（2000ms 轮询、清空显示 localStorage 持久化、
 *   乐观删除 + 失败回滚、自动滚动、行数上限全部保持）
 */
import { useEffect, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Copy, Trash2, Eye, ArrowLeft, Eraser, RotateCcw, FileText,
} from "lucide-react";
import { PageProps } from "../../api/client";
import { PageContainer, PageHeader } from "@/components/layout/app-shell";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import {
  Select, SelectTrigger, SelectValue, SelectContent, SelectItem,
} from "@/components/ui/select";
import { Row, RowText, SegmentedTabs, Blank, Toolbar } from "@/components/page/kit";
import { confirmDialog } from "@/components/ui/modal";
import { cn } from "@/lib/utils";
import { type LogLine, type Session, fmtStart, fmtSize, LEVEL_CLASS } from "./logs-shared";

export default function LogsPage(props: PageProps) {
  const { api, ready } = props;
  const qc = useQueryClient();
  const [autoScroll, setAutoScroll] = useState(true);
  const [limit, setLimit] = useState(500);
  const [mode, setMode] = useState<"current" | "history">("current");
  const [viewFile, setViewFile] = useState<string | null>(null);
  const [displayCleared, setDisplayCleared] = useState(
    () => localStorage.getItem("dy:logcleared") === "1",
  );
  const [clearTs, setClearTs] = useState<string | null>(
    () => localStorage.getItem("dy:logclearts") || null,
  );
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
    // 危险操作确认：必须在乐观更新之前 —— 否则点「取消」也已触发 setQueryData，
    // 列表先隐藏后回滚，用户看到的是「消失了又跳回来」。
    if (!(await confirmDialog({
      title: "批量删除",
      message: `删除选中的 ${files.length} 个历史会话？文件将被永久删除。`,
      danger: true,
    }))) return;
    // 乐观更新：删除前立即从列表隐藏选中项，消除删除后的可见延迟
    const delSet = new Set(files);
    qc.setQueryData(
      ["logs-sessions"],
      (old: { ok: boolean; current: string | null; sessions: Session[] } | undefined) => {
        if (!old) return old;
        return { ...old, sessions: old.sessions.filter((s) => !delSet.has(s.file)) };
      },
    );
    setSelected(new Set());
    const r = await api.deleteSessions(files);
    if (r.ok) {
      props.push(
        `已删除 ${r.deleted.length} 个历史会话${r.skipped.length ? `，跳过 ${r.skipped.length} 个` : ""}`,
      );
    } else {
      props.push("删除失败");
      sessQ.refetch(); // 失败回滚：重新拉取真实状态
    }
  };

  // 清空显示：只显示晚于清空时刻(clearTs)的新行；旧行隐藏。
  const shownLines = displayCleared
    ? lines.filter((l) => clearTs != null && l.ts > clearTs)
    : lines;

  const clearDisplay = () => {
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
  };

  const restoreDisplay = () => {
    setDisplayCleared(false);
    setClearTs(null);
    localStorage.removeItem("dy:logcleared");
    localStorage.removeItem("dy:logclearts");
  };

  const showLogView = mode === "current" || !!viewFile;

  return (
    <PageContainer>
      <PageHeader
        title="运行日志"
        description={`实时读取后端落盘的日志文件 · ${activeQ.data?.file || "等待数据"}`}
      />

      {/* 模式切换 + 批量管理 */}
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <SegmentedTabs
          value={mode}
          onChange={(m) => {
            setMode(m);
            if (m === "current") setViewFile(null);
          }}
          items={[
            { value: "current", label: "本次日志" },
            { value: "history", label: `历史日志（${historySessions.length}）` },
          ]}
        />

        {mode === "history" && (
          <>
            <div className="flex-1" />
            <Badge variant="outline">已选 {selected.size}</Badge>
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setSelected(new Set(historySessions.map((s) => s.file)))}
            >
              全选
            </Button>
            <Button variant="ghost" size="sm" onClick={() => setSelected(new Set())}>
              清除选择
            </Button>
            <Button
              variant="danger-outline"
              size="sm"
              onClick={doDeleteSelected}
              disabled={selected.size === 0}
            >
              <Trash2 className="h-3.5 w-3.5" />批量删除
            </Button>
          </>
        )}

        {mode === "current" && (
          <>
            <div className="flex-1" />
            <Button variant="ghost" size="sm" onClick={clearDisplay}>
              <Eraser className="h-3.5 w-3.5" />清空显示
            </Button>
            {displayCleared && (
              <Button variant="ghost" size="sm" onClick={restoreDisplay}>
                <RotateCcw className="h-3.5 w-3.5" />恢复显示
              </Button>
            )}
          </>
        )}
      </div>

      {/* 历史会话列表 */}
      {mode === "history" && !viewFile && (
        <Card className="mb-3 overflow-hidden">
          {historySessions.length === 0 && (
            <Blank>暂无历史会话（只有本次启动）</Blank>
          )}
          <div className="divide-y divide-[var(--color-border)]">
            {historySessions.map((s) => (
              <Row key={s.file} className="!px-3.5 !py-2.5">
                <input
                  type="checkbox"
                  className="h-4 w-4 shrink-0 accent-[var(--color-accent)]"
                  checked={selected.has(s.file)}
                  onChange={() => toggleSelect(s.file)}
                />
                <button
                  type="button"
                  className="min-w-0 flex-1 cursor-pointer text-left"
                  onClick={() => setViewFile(s.file)}
                >
                  <RowText
                    primary={fmtStart(s.start)}
                    secondary={`${s.file} · ${fmtSize(s.size)}`}
                    mono
                  />
                </button>
                <Button variant="ghost" size="sm" onClick={() => setViewFile(s.file)}>
                  <Eye className="h-3.5 w-3.5" />查看
                </Button>
                <Button
                  variant="danger-outline"
                  size="sm"
                  onClick={async () => {
                    // 危险操作确认：await 在 mutate 之前（否则取消也删了）
                    if (!(await confirmDialog({
                      title: "删除历史会话",
                      message: `删除 ${s.file}？文件将被永久删除。`,
                      danger: true,
                    }))) return;
                    const r = await api.deleteSessions([s.file]);
                    if (r.ok && r.deleted.length) props.push(`已删除 ${s.file}`);
                    else props.push("删除失败（本次会话不可删）");
                  }}
                >
                  <Trash2 className="h-3.5 w-3.5" />删除
                </Button>
              </Row>
            ))}
          </div>
        </Card>
      )}

      {mode === "history" && viewFile && (
        <div className="mb-3 flex items-center gap-3">
          <Button variant="ghost" size="sm" onClick={() => setViewFile(null)}>
            <ArrowLeft className="h-3.5 w-3.5" />返回历史列表
          </Button>
          <span className="font-mono text-[0.72rem] text-[var(--color-text-muted)]">
            {viewFile}
          </span>
        </div>
      )}

      {/* 行数上限 + 自动滚动（仅日志视图显示） */}
      {showLogView && (
        <div className="mb-3 flex flex-wrap items-center gap-3">
          <span className="text-[0.74rem] text-[var(--color-text-secondary)]">行数上限：</span>
          <Select value={String(limit)} onValueChange={(v) => setLimit(Number(v))}>
            <SelectTrigger className="w-[90px]"><SelectValue /></SelectTrigger>
            <SelectContent>
              {[200, 500, 1000, 2000].map((n) => (
                <SelectItem key={n} value={String(n)}>{n}</SelectItem>
              ))}
            </SelectContent>
          </Select>
          <label className="flex cursor-pointer items-center gap-2 text-[0.74rem]
                            text-[var(--color-text-secondary)]">
            <Switch checked={autoScroll} onCheckedChange={setAutoScroll} />
            自动滚动到底部
          </label>
          <div className="flex-1" />
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              navigator.clipboard
                ?.writeText(shownLines.map((l) => `${l.ts} | ${l.level} | ${l.text}`).join("\n"))
                .then(() => props.push("日志已复制到剪贴板"))
                .catch(() => props.push("复制失败"));
            }}
          >
            <Copy className="h-3.5 w-3.5" />复制全部
          </Button>
          <span className="font-mono text-[0.72rem] text-[var(--color-text-muted)]">
            共 {shownLines.length} 行
          </span>
        </div>
      )}

      {/* 日志视图 */}
      {showLogView && (
        <Card className="overflow-hidden">
          <div className="h-[calc(100vh-330px)] min-h-[320px] overflow-y-auto
                          bg-[var(--color-background-soft)] p-2.5 font-mono text-[0.78rem]
                          leading-[1.7]">
            {displayCleared && shownLines.length === 0 && (
              <Blank>显示已清空（实质日志保留）· 新日志将从这里开始显示</Blank>
            )}
            {!displayCleared && lines.length === 0 && (
              <Blank>
                {activeQ.isLoading ? "加载中…" : "暂无日志（后端尚未产生运行记录）"}
              </Blank>
            )}
            {shownLines.map((l, i) => (
              <div key={i} className="flex gap-2.5 whitespace-pre-wrap break-all">
                <span className="shrink-0 text-[var(--color-text-muted)]">{l.ts}</span>
                <span
                  className={cn(
                    "w-16 shrink-0",
                    LEVEL_CLASS[l.level] || "text-[var(--color-text-muted)]"
                  )}
                >
                  {l.level}
                </span>
                <span className="flex-1 text-[var(--color-text)]">{l.text}</span>
              </div>
            ))}
            <div ref={endRef} />
          </div>
        </Card>
      )}

      {/* 空态提示（历史模式下未选文件时不显示日志视图） */}
      {mode === "history" && !viewFile && historySessions.length > 0 && (
        <Toolbar className="text-[0.72rem] text-[var(--color-text-muted)]">
          <FileText className="h-3.5 w-3.5" />
          点某条会话的「查看」进入日志内容
        </Toolbar>
      )}
    </PageContainer>
  );
}
