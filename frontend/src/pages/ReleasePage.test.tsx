import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useUiStore } from "../stores/ui";
import { ReleasePage } from "./ReleasePage";

const mocks = vi.hoisted(() => ({
  saveDownloadedFile: vi.fn(),
  downloadBackup: vi.fn(),
  previewBackup: vi.fn(),
  restoreBackup: vi.fn(),
  downloadReleaseExport: vi.fn(),
  cleanupLogs: vi.fn(),
  deleteLogs: vi.fn()
}));

const timestamp = "2026-07-18T12:00:00Z";
const status = {
  app_version: "1.0.0",
  environment: "production",
  migration_revision: "d7e9f1a3c520",
  telemetry_enabled: false as const,
  frontend_bundled: true,
  database_integrity: "ok" as const,
  database_bytes: 1024 * 1024,
  log_retention_days: 14,
  log_files: 2,
  max_backup_bytes: 256 * 1024 * 1024
};
const preview = {
  archive_sha256: "a".repeat(64),
  archive_bytes: 1200,
  uncompressed_bytes: 3400,
  manifest: {
    format: "novel-agent-studio-backup" as const,
    schema_version: 1 as const,
    app_version: "1.0.0",
    migration_revision: "d7e9f1a3c520",
    created_at: timestamp,
    data_sha256: "b".repeat(64),
    tables: [{ table: "projects", records: 1 }, { table: "chapters", records: 2 }],
    includes: ["novels_and_versions"],
    excludes: ["credential_values"]
  },
  current_tables: [{ table: "projects", records: 1 }],
  conflicts: ["当前数据库已有 1 条记录；覆盖恢复将完整替换现有数据。"],
  warnings: ["完整备份包含本地小说正文。"],
  secret_findings: [],
  can_restore: true
};

vi.mock("../api/client", () => ({
  ApiError: class ApiError extends Error {},
  saveDownloadedFile: mocks.saveDownloadedFile,
  api: {
    releaseStatus: async () => status,
    listProjects: async () => [{ id: 1, title: "雾港回声", summary: "", language: "zh-CN", target_words: 100000, revision: 1, deleted_at: null, created_at: timestamp, updated_at: timestamp }],
    tree: async () => ({ project: { id: 1 }, volumes: [], chapters: [{ id: 7, title: "夜航", content: "", volume_id: 3, position: 1, word_count: 0, revision: 1, deleted_at: null, updated_at: timestamp }], scenes: [] }),
    downloadBackup: mocks.downloadBackup,
    previewBackup: mocks.previewBackup,
    restoreBackup: mocks.restoreBackup,
    downloadReleaseExport: mocks.downloadReleaseExport,
    cleanupLogs: mocks.cleanupLogs,
    deleteLogs: mocks.deleteLogs
  }
}));

function renderPage() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } }
  });
  render(<QueryClientProvider client={queryClient}><ReleasePage /></QueryClientProvider>);
}

describe("ReleasePage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useUiStore.setState({ selectedProjectId: 1 });
    mocks.downloadBackup.mockResolvedValue({ blob: new Blob(["backup"]), filename: "complete.zip" });
    mocks.previewBackup.mockResolvedValue(preview);
    mocks.restoreBackup.mockResolvedValue({
      strategy: "replace_all",
      archive_sha256: preview.archive_sha256,
      restored_tables: preview.manifest.tables,
      fts_records: 4,
      integrity_errors: [],
      completed_at: timestamp
    });
    mocks.downloadReleaseExport.mockResolvedValue({ blob: new Blob(["export"]), filename: "雾港回声.md" });
    mocks.cleanupLogs.mockResolvedValue({ deleted_files: 1, retained_files: 1, completed_at: timestamp });
    mocks.deleteLogs.mockResolvedValue({ deleted_files: 2, retained_files: 0, completed_at: timestamp });
  });

  it("downloads, validates and restores the exact previewed backup", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { name: "数据与发布" })).toBeInTheDocument();
    expect(await screen.findByText((_, element) => element?.textContent === "数据库 正常")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "生成完整备份" }));
    await waitFor(() => expect(mocks.saveDownloadedFile).toHaveBeenCalledWith(expect.objectContaining({ filename: "complete.zip" })));

    const file = new File(["PK backup"], "project.nasbackup.zip", { type: "application/zip" });
    fireEvent.change(screen.getByLabelText("选择备份文件"), { target: { files: [file] } });
    fireEvent.click(screen.getByRole("button", { name: "校验并预览" }));
    expect(await screen.findByText("备份完整且未发现凭据")).toBeInTheDocument();
    expect(mocks.previewBackup).toHaveBeenCalledWith(file);

    fireEvent.click(screen.getByLabelText("我确认用此备份替换当前数据库"));
    fireEvent.click(screen.getByRole("button", { name: "开始恢复" }));
    expect(await screen.findByRole("dialog", { name: "确认恢复备份" })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "确认恢复" }));
    await waitFor(() => expect(mocks.restoreBackup).toHaveBeenCalledWith(file, "replace_all", "a".repeat(64)));
    expect(await screen.findByText(/恢复完成：3 条记录/)).toBeInTheDocument();
  });

  it("exports the selected chapter and requires confirmation before deleting logs", async () => {
    renderPage();
    await screen.findByRole("heading", { name: "数据与发布" });
    await waitFor(() => expect(screen.getByLabelText("导出章节")).toHaveValue("7"));
    const chapterRow = screen.getByText("单章 Markdown").closest("article");
    expect(chapterRow).not.toBeNull();
    fireEvent.click(within(chapterRow!).getByRole("button", { name: "导出" }));
    await waitFor(() => expect(mocks.downloadReleaseExport).toHaveBeenCalledWith("chapter_markdown", 1, 7));
    expect(mocks.saveDownloadedFile).toHaveBeenCalledWith(expect.objectContaining({ filename: "雾港回声.md" }));

    fireEvent.click(screen.getByRole("button", { name: "删除全部日志" }));
    expect(await screen.findByRole("dialog", { name: "确认删除全部日志" })).toBeInTheDocument();
    expect(mocks.deleteLogs).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(mocks.deleteLogs).toHaveBeenCalledTimes(1));
  });
});
