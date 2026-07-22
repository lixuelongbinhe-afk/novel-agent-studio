from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


RestoreStrategy = Literal["empty_only", "replace_all"]
ExportKind = Literal[
    "book_text",
    "book_markdown",
    "book_pdf",
    "chapter_markdown",
    "library_json",
    "timeline_csv",
    "foreshadows_json",
    "agents_json",
    "workflows_json",
    "adapters_json",
    "diagnostics_zip",
]


class BackupTableCount(BaseModel):
    model_config = ConfigDict(extra="forbid")

    table: str
    records: int = Field(ge=0)


class BackupManifestRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["novel-agent-studio-backup"]
    schema_version: Literal[1, 2]
    app_version: str
    migration_revision: str
    created_at: datetime
    data_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    tables: list[BackupTableCount]
    includes: list[str]
    excludes: list[str]


class BackupPreviewRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    archive_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    archive_bytes: int = Field(ge=0)
    uncompressed_bytes: int = Field(ge=0)
    manifest: BackupManifestRead
    current_tables: list[BackupTableCount]
    conflicts: list[str]
    warnings: list[str]
    secret_findings: list[str]
    can_restore: bool


class BackupRestoreRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strategy: RestoreStrategy
    archive_sha256: str
    restored_tables: list[BackupTableCount]
    fts_records: int = Field(ge=0)
    integrity_errors: list[str]
    completed_at: datetime


class ReleaseStatusRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    app_version: str
    environment: str
    migration_revision: str
    telemetry_enabled: Literal[False] = False
    frontend_bundled: bool
    database_integrity: Literal["ok", "failed"]
    database_bytes: int = Field(ge=0)
    log_retention_days: int = Field(ge=1)
    log_files: int = Field(ge=0)
    max_backup_bytes: int = Field(ge=1)


class LogCleanupRead(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deleted_files: int = Field(ge=0)
    retained_files: int = Field(ge=0)
    completed_at: datetime
