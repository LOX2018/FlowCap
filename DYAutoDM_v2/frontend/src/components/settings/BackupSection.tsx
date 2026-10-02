/**
 * 备份（配置中心 · 系统 分区）
 *
 * ## 为什么存在（2026-10-02 用户要求）
 *
 * 用户要求系统页提供「备份」子板块，含**导出与导入**，且
 * **不以套餐形式、按自定义选择范围**（原话：「全部都提供，但是呢，
 * 不以套餐的形式，以自定义选择范围的形式」）。
 *
 * ## 契约
 *
 * - 范围清单来自后端 `GET /api/backup/scopes`（SSOT，前端不硬编码）。
 * - 导出 = 纯读 + 落盘一个新文件；导入 = 按范围覆盖（后端写前快照、失败回滚）。
 * - **不含**账号凭证 / 浏览器 profile / 日志（后端已排除，前端如实提示）。
 * - 导出目录由配置中心 `system.export_dir` 决定（本页「系统」分区可改）。
 */
import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Download, Upload, FolderOpen, AlertTriangle } from "lucide-react";
import { PageProps, BackupScope } from "../../api/client";
import { Button } from "@/components/ui/button";
import { SetCard, SetCardHead, SetCardBody, SetCardFoot } from "@/components/page/set-card";
import { errMsg } from "@/lib/utils";

export default function BackupSection(props: PageProps) {
  const { api, ready, push } = props;

  const scopesQ = useQuery({
    queryKey: ["backup-scopes"],
    queryFn: () => api.getBackupScopes(),
    enabled: !!ready,
    staleTime: 60_000,
  });
  const dirQ = useQuery({
    queryKey: ["backup-export-dir"],
    queryFn: () => api.getExportDir(),
    enabled: !!ready,
    staleTime: 30_000,
  });

  const scopes: BackupScope[] = scopesQ.data?.scopes || [];

  // 导出范围（默认全选，用户可自行取消）
  const [sel, setSel] = useState<Set<string>>(new Set());
  useEffect(() => {
    if (scopes.length && sel.size === 0) {
      setSel(new Set(scopes.map((s) => s.key)));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopes.length]);

  const [busy, setBusy] = useState(false);
  const [exported, setExported] = useState<{ filename: string; path: string; bytes: number } | null>(null);
  const [importFile, setImportFile] = useState<File | null>(null);

  const allOn = scopes.length > 0 && sel.size === scopes.length;
  const toggle = (k: string) => {
    setSel((prev) => {
      const n = new Set(prev);
      n.has(k) ? n.delete(k) : n.add(k);
      return n;
    });
  };
  const toggleAll = () => {
    setSel(allOn ? new Set() : new Set(scopes.map((s) => s.key)));
  };

  const fmtBytes = (n: number) =>
    n > 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`;

  const doExport = async () => {
    if (!sel.size) return push("请至少勾选一个范围");
    setBusy(true);
    try {
      const r = await api.exportBackup([...sel]);
      setExported({ filename: r.filename, path: r.path, bytes: r.bytes });
      push(`已导出 ${r.scopes.length} 个范围（${fmtBytes(r.bytes)}）`);
    } catch (e) {
      push(`导出失败：${errMsg(e)}`, 6000);
    } finally {
      setBusy(false);
    }
  };

  const doDownload = async () => {
    if (!exported) return;
    try {
      const blob = await api.downloadBackup(exported.filename);
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = exported.filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) {
      push(`下载失败：${errMsg(e)}`, 6000);
    }
  };

  const doImport = async () => {
    if (!importFile) return push("请先选择备份文件");
    setBusy(true);
    try {
      // scopes 空 = 按备份包内声明的全部范围导入
      const r = await api.importBackup(importFile, []);
      push(`已导入 ${r.imported_scopes.length} 个范围（键 ${r.kv_keys.length} · 表 ${r.tables.length}）`);
      setImportFile(null);
    } catch (e) {
      push(`导入失败：${errMsg(e)}`, 8000);
    } finally {
      setBusy(false);
    }
  };

  const dir = dirQ.data?.dir || "";
  const grouped = useMemo(() => {
    const kv = scopes.filter((s) => s.kind === "kv");
    const tb = scopes.filter((s) => s.kind !== "kv");
    return { kv, tb };
  }, [scopes]);

  return (
    <SetCard data-od-id="settings-backup-section">
      <SetCardHead
        title="备份"
        description="按自定义范围导出 / 导入（配置与业务数据）"
        right={
          <span className="flex items-center gap-1.5 text-[0.7rem] text-[var(--color-text-muted)]">
            <FolderOpen className="h-3.5 w-3.5" />
            <span className="max-w-[280px] truncate" title={dir}>{dir || "（默认目录）"}</span>
          </span>
        }
      />
      <SetCardBody>
        {/* ---- 范围勾选 ---- */}
        <div className="mb-2 flex items-center gap-2">
          <span className="text-[0.72rem] text-[var(--color-text-muted)]">导出范围</span>
          <button
            type="button"
            onClick={toggleAll}
            className="text-[0.72rem] text-[var(--color-accent)] hover:underline"
          >
            {allOn ? "全不选" : "全选"}
          </button>
          <span className="text-[0.72rem] text-[var(--color-text-muted)]">
            （已选 {sel.size}/{scopes.length}）
          </span>
        </div>

        <div className="mb-3 grid grid-cols-[repeat(auto-fill,minmax(200px,1fr))] gap-1.5">
          {[...grouped.kv, ...grouped.tb].map((s) => (
            <label
              key={s.key}
              title={s.desc}
              className="flex cursor-pointer items-center gap-2 rounded-[8px] border
                         border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 py-1.5
                         text-[0.74rem] hover:border-[var(--color-border-strong)]"
            >
              <input
                type="checkbox"
                checked={sel.has(s.key)}
                onChange={() => toggle(s.key)}
              />
              <span className="min-w-0 flex-1 truncate">{s.label}</span>
              {s.kind !== "kv" && (
                <span className="shrink-0 text-[0.66rem] text-[var(--color-text-muted)]">数据表</span>
              )}
            </label>
          ))}
        </div>

        {/* ---- 导出 ---- */}
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={doExport} disabled={busy || !sel.size} data-od-id="backup-export">
            <Download className="h-3.5 w-3.5" />导出
          </Button>
          {exported && (
            <>
              <span className="text-[0.72rem] text-[var(--color-text-muted)]">
                {exported.filename} · {fmtBytes(exported.bytes)}
              </span>
              <Button size="sm" variant="secondary" onClick={doDownload}>
                下载
              </Button>
            </>
          )}
        </div>

        {/* ---- 导入 ---- */}
        <div className="mt-4 border-t border-[var(--color-border)] pt-3">
          <div className="mb-1.5 text-[0.72rem] text-[var(--color-text-muted)]">导入备份</div>
          <div className="flex flex-wrap items-center gap-2">
            <input
              type="file"
              accept="application/json,.json"
              onChange={(e) => setImportFile(e.target.files?.[0] || null)}
              className="text-[0.72rem] text-[var(--color-text-muted)] file:mr-2 file:rounded-[8px]
                         file:border file:border-[var(--color-border)] file:bg-[var(--color-surface)]
                         file:px-2.5 file:py-1 file:text-[0.72rem] file:text-[var(--color-text)]"
            />
            <Button size="sm" variant="secondary" onClick={doImport} disabled={busy || !importFile} data-od-id="backup-import">
              <Upload className="h-3.5 w-3.5" />导入
            </Button>
          </div>
          <div className="mt-2 flex items-start gap-1.5 text-[0.68rem] leading-relaxed text-[var(--color-text-muted)]">
            <AlertTriangle className="mt-[1px] h-3 w-3 shrink-0 text-[var(--color-warning)]" />
            <span>
              导入按范围**覆盖**现有数据（后端写前快照、失败自动回滚）。
              备份包不含账号凭证 / 浏览器 profile / 日志。
            </span>
          </div>
        </div>
      </SetCardBody>
      <SetCardFoot>
        <span className="text-[0.68rem] text-[var(--color-text-muted)]">
          导出目录可在上方「系统」分区修改
        </span>
      </SetCardFoot>
    </SetCard>
  );
}
