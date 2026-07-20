import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  Archive,
  Bot,
  CheckCircle2,
  Clock3,
  Database,
  DatabaseBackup,
  Download,
  FileArchive,
  FileJson2,
  FileSpreadsheet,
  FileText,
  HardDrive,
  ListTree,
  LockKeyhole,
  PackageCheck,
  RefreshCw,
  ShieldCheck,
  Trash2,
  Upload,
  Workflow,
  type LucideIcon
} from "lucide-react";
import {
  ApiError,
  api,
  saveDownloadedFile,
  type ReleaseExportKind
} from "../api/client";
import { Dialog } from "../components/Dialog";
import { ErrorNotice } from "../components/ErrorNotice";
import { useUiStore } from "../stores/ui";

type RestoreStrategy = "empty_only" | "replace_all";
type ConfirmAction = "restore" | "delete_logs" | null;

type ExportSpec = {
  kind: ReleaseExportKind;
  label: string;
  description: string;
  icon: LucideIcon;
  project: boolean;
  chapter?: boolean;
};

const exportSpecs: ExportSpec[] = [
  { kind: "book_markdown", label: "全书 Markdown", description: "按卷章顺序合并正文", icon: FileText, project: true },
  { kind: "chapter_markdown", label: "单章 Markdown", description: "当前选择章节的独立正文", icon: FileText, project: true, chapter: true },
  { kind: "library_json", label: "资料库 JSON", description: "实体、别名、关系、状态与风格规则", icon: FileJson2, project: true },
  { kind: "timeline_csv", label: "时间线 CSV", description: "按顺序导出全部时间线事件", icon: FileSpreadsheet, project: true },
  { kind: "foreshadows_json", label: "伏笔 JSON", description: "埋设、发展、回收状态与章节引用", icon: ListTree, project: true },
  { kind: "agents_json", label: "智能体 JSON", description: "智能体版本、Schema、模型目标与预算", icon: Bot, project: true },
  { kind: "workflows_json", label: "工作流 Manifest", description: "可重新导入的节点、边与智能体快照", icon: Workflow, project: true },
  { kind: "adapters_json", label: "Adapter Manifest", description: "禁用且不含凭据的自定义适配器", icon: LockKeyhole, project: false },
  { kind: "diagnostics_zip", label: "脱敏诊断包", description: "运行环境、依赖、表计数与完整性状态", icon: Activity, project: false }
];

export function ReleasePage() {
  const queryClient = useQueryClient();
  const selectedProjectId = useUiStore((state) => state.selectedProjectId);
  const [backupFile, setBackupFile] = useState<File | null>(null);
  const [restoreStrategy, setRestoreStrategy] = useState<RestoreStrategy>("empty_only");
  const [replaceConfirmed, setReplaceConfirmed] = useState(false);
  const [selectedChapterId, setSelectedChapterId] = useState<number | null>(null);
  const [confirmAction, setConfirmAction] = useState<ConfirmAction>(null);
  const [notice, setNotice] = useState("");

  const statusQuery = useQuery({ queryKey: ["release-status"], queryFn: () => api.releaseStatus() });
  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: () => api.listProjects() });
  const projectId = selectedProjectId && projectsQuery.data?.some((item) => item.id === selectedProjectId)
    ? selectedProjectId
    : projectsQuery.data?.[0]?.id;
  const project = projectsQuery.data?.find((item) => item.id === projectId);
  const treeQuery = useQuery({
    queryKey: ["tree", projectId],
    queryFn: () => api.tree(projectId!),
    enabled: Boolean(projectId)
  });

  useEffect(() => {
    const chapters = treeQuery.data?.chapters ?? [];
    if (!chapters.some((item) => item.id === selectedChapterId)) {
      setSelectedChapterId(chapters[0]?.id ?? null);
    }
  }, [selectedChapterId, treeQuery.data]);

  const backupDownload = useMutation({
    mutationFn: () => api.downloadBackup(),
    onSuccess: (file) => {
      saveDownloadedFile(file);
      setNotice("完整备份已生成。文件未包含凭据值、日志或未脱敏调试响应。");
    }
  });
  const preview = useMutation({
    mutationFn: (file: File) => api.previewBackup(file),
    onSuccess: (value) => {
      setRestoreStrategy(value.conflicts.length ? "replace_all" : "empty_only");
      setReplaceConfirmed(false);
      setNotice(value.can_restore ? "备份校验通过，可以进入恢复确认。" : "备份未通过安全校验。 ");
    }
  });
  const restore = useMutation({
    mutationFn: () => {
      if (!backupFile || !preview.data) throw new Error("请先校验备份文件");
      return api.restoreBackup(backupFile, restoreStrategy, preview.data.archive_sha256);
    },
    onSuccess: async (result) => {
      setConfirmAction(null);
      setReplaceConfirmed(false);
      setNotice(`恢复完成：${result.restored_tables.reduce((sum, item) => sum + item.records, 0)} 条记录，FTS ${result.fts_records} 条。`);
      await queryClient.invalidateQueries();
    }
  });
  const exportFile = useMutation({
    mutationFn: (kind: ReleaseExportKind) =>
      api.downloadReleaseExport(kind, projectId, kind === "chapter_markdown" ? selectedChapterId ?? undefined : undefined),
    onSuccess: (file) => {
      saveDownloadedFile(file);
      setNotice(`${file.filename} 已生成。`);
    }
  });
  const logCleanup = useMutation({
    mutationFn: (all: boolean) => all ? api.deleteLogs() : api.cleanupLogs(),
    onSuccess: async (result) => {
      setConfirmAction(null);
      setNotice(`日志处理完成：删除 ${result.deleted_files} 个，保留 ${result.retained_files} 个。`);
      await statusQuery.refetch();
    }
  });

  const operationError = statusQuery.error ?? projectsQuery.error ?? treeQuery.error ??
    backupDownload.error ?? preview.error ?? restore.error ?? exportFile.error ?? logCleanup.error;
  const previewRecords = useMemo(
    () => preview.data?.manifest.tables.reduce((sum, item) => sum + item.records, 0) ?? 0,
    [preview.data]
  );
  const restoreReady = Boolean(
    backupFile && preview.data?.can_restore &&
    (restoreStrategy !== "replace_all" || replaceConfirmed)
  );

  return (
    <section className="page-stack release-page">
      <header className="page-header">
        <div>
          <span className="eyebrow">Backup, export & release</span>
          <h1>数据与发布</h1>
          <p>项目备份、事务恢复、内容导出和本地运行状态。</p>
        </div>
        <div className="release-head-state" aria-label="发布状态">
          <span className={statusQuery.data?.database_integrity === "ok" ? "ok" : "warn"}>
            <Database size={15} />数据库 {statusQuery.data?.database_integrity === "ok" ? "正常" : "待检查"}
          </span>
          <span><PackageCheck size={15} />v{statusQuery.data?.app_version ?? "..."}</span>
          <span><ShieldCheck size={15} />遥测关闭</span>
        </div>
      </header>

      {operationError ? <ErrorNotice message={errorMessage(operationError, "发布操作失败")} /> : null}
      {notice ? <div className="success-notice" role="status"><CheckCircle2 size={17} /><span>{notice}</span><button type="button" onClick={() => setNotice("")} aria-label="关闭状态">×</button></div> : null}

      <div className="release-primary-grid">
        <section className="release-panel backup-panel">
          <header>
            <div className="release-panel-title"><DatabaseBackup size={19} /><div><h2>完整备份与恢复</h2><span>格式 v1 · SHA-256 · 事务导入</span></div></div>
            <button className="primary-button" type="button" onClick={() => backupDownload.mutate()} disabled={backupDownload.isPending}>
              <Download size={16} />{backupDownload.isPending ? "正在生成" : "生成完整备份"}
            </button>
          </header>

          <div className="backup-input-row">
            <label className="secondary-button file-picker">
              <Upload size={16} />选择备份文件
              <input
                aria-label="选择备份文件"
                type="file"
                accept=".zip,.nasbackup.zip,application/zip"
                onChange={(event) => {
                  const file = event.target.files?.[0] ?? null;
                  setBackupFile(file);
                  preview.reset();
                  restore.reset();
                  setReplaceConfirmed(false);
                }}
              />
            </label>
            <span className="backup-file-name">
              {backupFile ? <><FileArchive size={17} /><strong>{backupFile.name}</strong><small>{formatBytes(backupFile.size)}</small></> : <><Archive size={17} /><strong>尚未选择文件</strong><small>最大 {formatBytes(statusQuery.data?.max_backup_bytes ?? 0)}</small></>}
            </span>
            <button className="secondary-button" type="button" disabled={!backupFile || preview.isPending} onClick={() => backupFile && preview.mutate(backupFile)}>
              <ShieldCheck size={16} />{preview.isPending ? "正在校验" : "校验并预览"}
            </button>
          </div>

          {preview.data ? (
            <div className={`backup-preview ${preview.data.can_restore ? "valid" : "invalid"}`}>
              <div className="backup-preview-summary">
                {preview.data.can_restore ? <CheckCircle2 size={22} /> : <AlertTriangle size={22} />}
                <span><strong>{preview.data.can_restore ? "备份完整且未发现凭据" : "备份不允许恢复"}</strong><small>{previewRecords.toLocaleString()} 条记录 · 解压后 {formatBytes(preview.data.uncompressed_bytes)}</small></span>
                <code title={preview.data.archive_sha256}>{preview.data.archive_sha256.slice(0, 16)}…</code>
              </div>
              <div className="backup-preview-details">
                <dl>
                  <div><dt>应用版本</dt><dd>{preview.data.manifest.app_version}</dd></div>
                  <div><dt>迁移版本</dt><dd>{preview.data.manifest.migration_revision}</dd></div>
                  <div><dt>创建时间</dt><dd>{new Date(preview.data.manifest.created_at).toLocaleString()}</dd></div>
                  <div><dt>压缩大小</dt><dd>{formatBytes(preview.data.archive_bytes)}</dd></div>
                </dl>
                {[...preview.data.conflicts, ...preview.data.warnings, ...preview.data.secret_findings].length ? (
                  <ul>
                    {preview.data.conflicts.map((item) => <li className="conflict" key={item}>{item}</li>)}
                    {preview.data.warnings.map((item) => <li key={item}>{item}</li>)}
                    {preview.data.secret_findings.map((item) => <li className="conflict" key={item}>疑似凭据：{item}</li>)}
                  </ul>
                ) : null}
              </div>
              <footer>
                <div className="restore-strategy" role="radiogroup" aria-label="恢复策略">
                  <label><input type="radio" name="restore-strategy" checked={restoreStrategy === "empty_only"} onChange={() => { setRestoreStrategy("empty_only"); setReplaceConfirmed(false); }} />仅恢复到空库</label>
                  <label><input type="radio" name="restore-strategy" checked={restoreStrategy === "replace_all"} onChange={() => setRestoreStrategy("replace_all")} />覆盖当前全部数据</label>
                </div>
                {restoreStrategy === "replace_all" ? <label className="replace-confirm"><input type="checkbox" checked={replaceConfirmed} onChange={(event) => setReplaceConfirmed(event.target.checked)} />我确认用此备份替换当前数据库</label> : null}
                <button className={restoreStrategy === "replace_all" ? "danger-button" : "primary-button"} type="button" disabled={!restoreReady || restore.isPending} onClick={() => setConfirmAction("restore")}>
                  <RefreshCw size={16} />开始恢复
                </button>
              </footer>
            </div>
          ) : (
            <div className="backup-empty"><HardDrive size={25} /><span>备份文件将在本机校验格式、大小、哈希、Schema 和 Secret。</span></div>
          )}
        </section>

        <aside className="release-side-stack">
          <section className="release-panel status-panel">
            <header><div className="release-panel-title"><Activity size={19} /><div><h2>本地运行状态</h2><span>{statusQuery.data?.environment ?? "正在读取"}</span></div></div></header>
            <dl className="release-status-list">
              <div><dt><Database size={15} />数据库文件</dt><dd>{formatBytes(statusQuery.data?.database_bytes ?? 0)}</dd></div>
              <div><dt><Clock3 size={15} />日志保留</dt><dd>{statusQuery.data?.log_retention_days ?? "—"} 天</dd></div>
              <div><dt><FileText size={15} />日志文件</dt><dd>{statusQuery.data?.log_files ?? "—"}</dd></div>
              <div><dt><PackageCheck size={15} />前端构建</dt><dd>{statusQuery.data?.frontend_bundled ? "已内置" : "开发服务"}</dd></div>
              <div><dt><LockKeyhole size={15} />Schema</dt><dd>{statusQuery.data?.migration_revision ?? "—"}</dd></div>
            </dl>
          </section>
          <section className="release-panel log-panel">
            <header><div className="release-panel-title"><FileText size={19} /><div><h2>本地日志</h2><span>正文与凭据不写入日志</span></div></div></header>
            <div className="log-actions">
              <button className="secondary-button" type="button" disabled={logCleanup.isPending} onClick={() => logCleanup.mutate(false)}><Clock3 size={16} />清理过期日志</button>
              <button className="secondary-button danger-ink" type="button" disabled={logCleanup.isPending} onClick={() => setConfirmAction("delete_logs")}><Trash2 size={16} />删除全部日志</button>
            </div>
          </section>
        </aside>
      </div>

      <section className="release-panel export-panel">
        <header>
          <div className="release-panel-title"><Download size={19} /><div><h2>数据导出</h2><span>{project ? `当前项目：${project.title}` : "当前没有项目"}</span></div></div>
          <label className="chapter-export-select">
            <span>单章</span>
            <select aria-label="导出章节" value={selectedChapterId ?? ""} disabled={!treeQuery.data?.chapters.length} onChange={(event) => setSelectedChapterId(Number(event.target.value))}>
              {!treeQuery.data?.chapters.length ? <option value="">暂无章节</option> : null}
              {treeQuery.data?.chapters.map((chapter) => <option key={chapter.id} value={chapter.id}>{chapter.title}</option>)}
            </select>
          </label>
        </header>
        <div className="export-list">
          {exportSpecs.map((spec) => {
            const Icon = spec.icon;
            const disabled = exportFile.isPending || (spec.project && !projectId) || (spec.chapter && !selectedChapterId);
            return (
              <article key={spec.kind}>
                <Icon size={18} />
                <span><strong>{spec.label}</strong><small>{spec.description}</small></span>
                <button className="secondary-button compact" type="button" disabled={disabled} onClick={() => exportFile.mutate(spec.kind)}><Download size={15} />导出</button>
              </article>
            );
          })}
        </div>
      </section>

      <Dialog
        open={confirmAction !== null}
        title={confirmAction === "restore" ? "确认恢复备份" : "确认删除全部日志"}
        description={confirmAction === "restore" ? (restoreStrategy === "replace_all" ? "当前数据库将在同一事务中被完整替换。" : "仅当当前数据库为空时才会导入。") : "当前日志文件内容将被立即删除。"}
        width="small"
        onClose={() => setConfirmAction(null)}
        footer={<><button className="secondary-button" type="button" onClick={() => setConfirmAction(null)}>取消</button><button className={confirmAction === "restore" && restoreStrategy !== "replace_all" ? "primary-button" : "danger-button"} type="button" disabled={restore.isPending || logCleanup.isPending} onClick={() => confirmAction === "restore" ? restore.mutate() : logCleanup.mutate(true)}>{confirmAction === "restore" ? "确认恢复" : "确认删除"}</button></>}
      >
        {confirmAction === "restore" ? <div className="confirmation-summary"><FileArchive size={22} /><span><strong>{backupFile?.name}</strong><small>SHA-256 {preview.data?.archive_sha256.slice(0, 24)}…</small></span></div> : <div className="confirmation-summary"><Trash2 size={22} /><span><strong>{statusQuery.data?.log_files ?? 0} 个日志文件</strong><small>此操作不删除数据库、备份或导出文件。</small></span></div>}
      </Dialog>
    </section>
  );
}

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) return error.message.replace(/^"|"$/g, "") || fallback;
  if (error instanceof Error) return error.message || fallback;
  return fallback;
}

function formatBytes(value: number): string {
  if (!value) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  const index = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  const amount = value / 1024 ** index;
  return `${amount >= 10 || index === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[index]}`;
}
