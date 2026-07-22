from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal, cast

from sqlalchemy import DateTime, LargeBinary, Table, delete, insert, select, text, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app import models
from app.core.config import get_settings
from app.database import Base
from app.migrations import STUDIO_V2_REVISION
from app.schemas.release import (
    BackupManifestRead,
    BackupPreviewRead,
    BackupRestoreRead,
    BackupTableCount,
    RestoreStrategy,
)
from app.services.context_retrieval import rebuild_fts_index


BACKUP_FORMAT: Literal["novel-agent-studio-backup"] = "novel-agent-studio-backup"
BACKUP_SCHEMA_VERSION: Literal[2] = 2
ARCHIVE_FILES = frozenset({"manifest.json", "data.json"})
MAX_ARCHIVE_ENTRIES = 8
MAX_COMPRESSION_RATIO = 250

_REFERENCE_COLUMNS = frozenset(
    {"credential_env_var", "credential_env_var_hint", "env_var_name", "credential_reference_id"}
)
_TRANSIENT_REDACTIONS: dict[str, dict[str, Any]] = {
    "generic_http_adapter_configurations": {
        "last_test_request_json": "{}",
        "last_test_result_json": "{}",
        "last_tested_at": None,
    },
}
_SECRET_VALUE_PATTERNS = (
    re.compile(r"(?i)^bearer\s+\S+"),
    re.compile(r"(?i)^basic\s+[A-Za-z0-9+/=]{8,}"),
    re.compile(r"^sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"^AIza[0-9A-Za-z_-]{20,}"),
    re.compile(r"^(?:ghp|github_pat|xox[baprs])[_-][A-Za-z0-9_-]{12,}"),
)


@dataclass(frozen=True)
class LoadedBackup:
    manifest: BackupManifestRead
    tables: dict[str, list[dict[str, Any]]]
    archive_sha256: str
    archive_bytes: int
    uncompressed_bytes: int
    secret_findings: list[str]


def backup_tables() -> tuple[Table, ...]:
    # Importing models above registers every mapped table on Base.metadata.
    return tuple(Base.metadata.sorted_tables)


def create_backup_archive(db: Session) -> bytes:
    settings = get_settings()
    tables: dict[str, list[dict[str, Any]]] = {}
    counts: list[BackupTableCount] = []
    for table in backup_tables():
        rows = [
            _serialize_row(table.name, row)
            for row in db.execute(select(table)).mappings().all()
        ]
        tables[table.name] = rows
        counts.append(BackupTableCount(table=table.name, records=len(rows)))

    data_payload = {
        "schema_version": BACKUP_SCHEMA_VERSION,
        "tables": tables,
    }
    data_bytes = _canonical_json(data_payload)
    findings = scan_backup_secrets(data_payload)
    findings.extend(_scan_bound_environment_values(data_bytes.decode("utf-8"), tables))
    if findings:
        locations = ", ".join(sorted(set(findings))[:12])
        raise ValueError(f"备份 Secret 扫描失败：{locations}")

    manifest = BackupManifestRead(
        format=BACKUP_FORMAT,
        schema_version=BACKUP_SCHEMA_VERSION,
        app_version=settings.app_version,
        migration_revision=STUDIO_V2_REVISION,
        created_at=datetime.now(timezone.utc),
        data_sha256=hashlib.sha256(data_bytes).hexdigest(),
        tables=counts,
        includes=[
            "novels_and_versions",
            "story_library_and_timeline",
            "context_memory_and_snapshots",
            "agents_workflows_and_history",
            "provider_model_route_budget_configuration",
            "approval_changesets_and_writeback_audits",
        ],
        excludes=[
            "credential_values",
            "authorization_and_cookie_headers",
            "unredacted_adapter_test_payloads",
            "hidden_reasoning",
            "temporary_caches",
            "log_files",
        ],
    )
    manifest_bytes = _canonical_json(manifest.model_dump(mode="json"))
    output = io.BytesIO()
    with zipfile.ZipFile(
        output, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
    ) as archive:
        _write_zip_member(archive, "manifest.json", manifest_bytes)
        _write_zip_member(archive, "data.json", data_bytes)
    result = output.getvalue()
    if len(result) > settings.max_backup_bytes:
        raise ValueError("备份超过允许的压缩文件大小")
    return result


def preview_backup_archive(db: Session, archive_bytes: bytes) -> BackupPreviewRead:
    loaded = load_backup_archive(archive_bytes)
    current = current_table_counts(db)
    current_total = sum(
        item.records for item in current if item.table != "provider_presets"
    )
    conflicts = (
        [f"当前数据库已有 {current_total} 条记录；覆盖恢复将完整替换现有数据。"]
        if current_total
        else []
    )
    warnings = [
        "完整备份包含本地小说正文和 Context 快照，请按敏感创作资料保管。",
        "恢复不会导入 API Key、Authorization、Cookie、隐藏推理或日志。",
    ]
    return BackupPreviewRead(
        archive_sha256=loaded.archive_sha256,
        archive_bytes=loaded.archive_bytes,
        uncompressed_bytes=loaded.uncompressed_bytes,
        manifest=loaded.manifest,
        current_tables=current,
        conflicts=conflicts,
        warnings=warnings,
        secret_findings=loaded.secret_findings,
        can_restore=not loaded.secret_findings,
    )


def restore_backup_archive(
    db: Session,
    archive_bytes: bytes,
    *,
    strategy: RestoreStrategy,
    expected_sha256: str,
) -> BackupRestoreRead:
    loaded = load_backup_archive(archive_bytes)
    if loaded.archive_sha256 != expected_sha256:
        raise ValueError("恢复文件与已预览文件的 SHA-256 不一致")
    if loaded.secret_findings:
        raise ValueError("恢复文件包含疑似凭据，已拒绝导入")
    if strategy == "empty_only" and _has_user_data(db):
        raise ValueError("当前数据库不是空库；请选择明确的覆盖恢复策略")
    active_run = db.scalar(
        select(models.WorkflowRun.id).where(
            models.WorkflowRun.status.in_({"pending", "running", "waiting_approval"})
        ).limit(1)
    )
    if active_run is not None:
        raise ValueError(f"工作流 #{active_run} 仍在运行或等待审批，不能恢复备份")

    tables_by_name = {table.name: table for table in backup_tables()}
    for table in reversed(backup_tables()):
        db.execute(delete(table))

    deferred_updates: list[tuple[Table, dict[str, Any], dict[str, Any]]] = []
    for table in backup_tables():
        for raw_row in loaded.tables[table.name]:
            values = _deserialize_row(table, raw_row)
            primary_key = {
                column.name: values[column.name] for column in table.primary_key.columns
            }
            deferred: dict[str, Any] = {}
            for column in table.columns:
                if (
                    column.foreign_keys
                    and column.nullable
                    and values.get(column.name) is not None
                ):
                    deferred[column.name] = values[column.name]
                    values[column.name] = None
            db.execute(insert(table).values(**values))
            if deferred:
                deferred_updates.append((table, primary_key, deferred))

    for table, primary_key, values in deferred_updates:
        condition = None
        for key, value in primary_key.items():
            expression = table.c[key] == value
            condition = expression if condition is None else condition & expression
        if condition is None:
            raise ValueError(f"表 {table.name} 缺少主键，无法恢复延迟引用")
        db.execute(update(table).where(condition).values(**values))

    missing_tables = set(tables_by_name) - set(loaded.tables)
    if missing_tables:
        raise ValueError(f"备份缺少数据表：{', '.join(sorted(missing_tables))}")

    integrity_rows = db.execute(text("PRAGMA foreign_key_check")).all()
    integrity_errors = [" | ".join(str(value) for value in row) for row in integrity_rows]
    if integrity_errors:
        raise ValueError(f"恢复后的引用完整性检查失败：{integrity_errors[0]}")

    fts_records = 0
    for project_id in db.scalars(select(models.Project.id)).all():
        fts_records += rebuild_fts_index(db, project_id)

    # Core DELETE/INSERT bypasses the ORM identity map; do not expose stale pre-restore rows.
    db.expire_all()

    return BackupRestoreRead(
        strategy=strategy,
        archive_sha256=loaded.archive_sha256,
        restored_tables=[
            BackupTableCount(table=name, records=len(rows))
            for name, rows in loaded.tables.items()
        ],
        fts_records=fts_records,
        integrity_errors=[],
        completed_at=datetime.now(timezone.utc),
    )


def load_backup_archive(archive_bytes: bytes) -> LoadedBackup:
    settings = get_settings()
    if not archive_bytes:
        raise ValueError("备份文件为空")
    if len(archive_bytes) > settings.max_backup_bytes:
        raise ValueError("备份文件超过大小限制")
    archive_sha256 = hashlib.sha256(archive_bytes).hexdigest()
    stream = io.BytesIO(archive_bytes)
    if not zipfile.is_zipfile(stream):
        raise ValueError("备份不是有效 ZIP 文件")
    stream.seek(0)
    with zipfile.ZipFile(stream) as archive:
        infos = archive.infolist()
        if len(infos) > MAX_ARCHIVE_ENTRIES:
            raise ValueError("备份 ZIP 条目过多")
        names = {info.filename for info in infos}
        if names != ARCHIVE_FILES:
            raise ValueError("备份 ZIP 只能包含 manifest.json 和 data.json")
        uncompressed_bytes = 0
        for info in infos:
            _validate_zip_member(info)
            uncompressed_bytes += info.file_size
            if uncompressed_bytes > settings.max_backup_uncompressed_bytes:
                raise ValueError("备份解压后超过大小限制")
        manifest_bytes = archive.read("manifest.json")
        data_bytes = archive.read("data.json")

    try:
        manifest_value = json.loads(manifest_bytes)
        data_value = json.loads(data_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("备份 JSON 无效") from exc
    manifest = BackupManifestRead.model_validate(manifest_value)
    if hashlib.sha256(data_bytes).hexdigest() != manifest.data_sha256:
        raise ValueError("备份 data.json 哈希校验失败")
    migrated = _migrate_backup_data(data_value, manifest.schema_version)
    tables = _validate_table_payload(migrated, manifest)
    findings = scan_backup_secrets(migrated)
    findings.extend(_scan_bound_environment_values(data_bytes.decode("utf-8"), tables))
    return LoadedBackup(
        manifest=manifest,
        tables=tables,
        archive_sha256=archive_sha256,
        archive_bytes=len(archive_bytes),
        uncompressed_bytes=uncompressed_bytes,
        secret_findings=sorted(set(findings)),
    )


def current_table_counts(db: Session) -> list[BackupTableCount]:
    result: list[BackupTableCount] = []
    for table in backup_tables():
        count = len(db.execute(select(table.c[next(iter(table.primary_key.columns)).name])).all())
        result.append(BackupTableCount(table=table.name, records=count))
    return result


def scan_backup_secrets(value: Any, path: str = "$") -> list[str]:
    findings: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            name = str(key).lower()
            child = f"{path}.{key}"
            if name not in _REFERENCE_COLUMNS and _secret_field_name(name):
                if item not in (None, "", "[REDACTED]") and item != {"$var": "credential"}:
                    findings.append(child)
            findings.extend(scan_backup_secrets(item, child))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            findings.extend(scan_backup_secrets(item, f"{path}[{index}]"))
    elif isinstance(value, str):
        stripped = value.strip()
        if any(pattern.match(stripped) for pattern in _SECRET_VALUE_PATTERNS):
            findings.append(path)
        if stripped.startswith(("{", "[")):
            try:
                nested = json.loads(stripped)
            except json.JSONDecodeError:
                nested = None
            if nested is not None and nested != value:
                findings.extend(scan_backup_secrets(nested, f"{path}<json>"))
    return sorted(set(findings))


def _validate_table_payload(
    value: Any, manifest: BackupManifestRead
) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(value, dict) or value.get("schema_version") != BACKUP_SCHEMA_VERSION:
        raise ValueError("备份数据 Schema 版本无效")
    raw_tables = value.get("tables")
    if not isinstance(raw_tables, dict):
        raise ValueError("备份缺少 tables 对象")
    expected = {table.name: table for table in backup_tables()}
    if set(raw_tables) != set(expected):
        unknown = set(raw_tables) - set(expected)
        missing = set(expected) - set(raw_tables)
        detail = "; ".join(
            part for part in (
                f"未知表 {sorted(unknown)}" if unknown else "",
                f"缺少表 {sorted(missing)}" if missing else "",
            ) if part
        )
        raise ValueError(f"备份表集合与当前 Schema 不一致：{detail}")
    manifest_counts = {item.table: item.records for item in manifest.tables}
    validated: dict[str, list[dict[str, Any]]] = {}
    for name, table in expected.items():
        rows = raw_tables[name]
        if not isinstance(rows, list):
            raise ValueError(f"备份表 {name} 不是数组")
        columns = {column.name for column in table.columns}
        parsed_rows: list[dict[str, Any]] = []
        for index, row in enumerate(rows):
            if not isinstance(row, dict) or set(row) != columns:
                raise ValueError(f"备份表 {name} 第 {index + 1} 行字段与 Schema 不一致")
            parsed_rows.append(cast(dict[str, Any], row))
        manifest_count = manifest_counts.get(name)
        if manifest_count is None and manifest.schema_version == BACKUP_SCHEMA_VERSION:
            raise ValueError(f"备份清单缺少表 {name} 的计数")
        if manifest_count is not None and manifest_count != len(parsed_rows):
            raise ValueError(f"备份表 {name} 的清单计数不一致")
        validated[name] = parsed_rows
    return validated


def _migrate_backup_data(value: Any, schema_version: int) -> Any:
    if not isinstance(value, dict) or value.get("schema_version") != schema_version:
        raise ValueError("备份清单与数据 Schema 版本不一致")
    if schema_version == BACKUP_SCHEMA_VERSION:
        return value
    if schema_version == 1:
        raw_tables = value.get("tables")
        if not isinstance(raw_tables, dict):
            raise ValueError("备份缺少 tables 对象")
        expected = {table.name for table in backup_tables()}
        unknown = set(raw_tables) - expected
        if unknown:
            raise ValueError(f"旧备份包含未知表：{sorted(unknown)}")
        tables = {
            name: [dict(row) for row in rows] if isinstance(rows, list) else rows
            for name, rows in raw_tables.items()
        }
        for name in expected:
            tables.setdefault(name, [])
        _migrate_v1_chapters(tables)
        for row in tables["generation_jobs"]:
            if not isinstance(row, dict):
                raise ValueError("旧备份 generation_jobs 行格式无效")
            row.setdefault("idempotency_key", None)
            row.setdefault("active_scope_key", None)
        return {"schema_version": BACKUP_SCHEMA_VERSION, "tables": tables}
    raise ValueError(f"不支持的备份 Schema 版本：{schema_version}")


def _migrate_v1_chapters(tables: dict[str, Any]) -> None:
    volumes = tables.get("volumes")
    chapters = tables.get("chapters")
    if not isinstance(volumes, list) or not isinstance(chapters, list):
        raise ValueError("旧备份卷章数据格式无效")
    volume_meta: dict[int, tuple[int, int]] = {}
    for row in volumes:
        if not isinstance(row, dict):
            raise ValueError("旧备份 volumes 行格式无效")
        volume_meta[int(row["id"])] = (int(row["project_id"]), int(row["position"]))
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in chapters:
        if not isinstance(row, dict):
            raise ValueError("旧备份 chapters 行格式无效")
        volume_id = int(row["volume_id"])
        if volume_id not in volume_meta:
            raise ValueError(f"旧备份章节引用不存在的卷：{volume_id}")
        project_id, _ = volume_meta[volume_id]
        row["project_id"] = project_id
        grouped.setdefault(project_id, []).append(row)
    for project_rows in grouped.values():
        project_rows.sort(
            key=lambda row: (
                volume_meta[int(row["volume_id"])][1],
                int(row["position"]),
                int(row["id"]),
            )
        )
        active_number = 0
        for row in project_rows:
            if row.get("deleted_at") is None:
                active_number += 1
                row["number"] = active_number
            else:
                row["number"] = None


def _serialize_row(table_name: str, row: RowMapping) -> dict[str, Any]:
    values = {key: _serialize_value(value) for key, value in row.items()}
    for key, replacement in _TRANSIENT_REDACTIONS.get(table_name, {}).items():
        if key in values:
            values[key] = replacement
    return values


def _serialize_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bytes):
        return {"$binary": base64.b64encode(value).decode("ascii")}
    return value


def _deserialize_row(table: Table, row: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for column in table.columns:
        value = row[column.name]
        if value is not None and isinstance(column.type, DateTime):
            if not isinstance(value, str):
                raise ValueError(f"{table.name}.{column.name} 必须是 ISO 日期字符串")
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        elif value is not None and isinstance(column.type, LargeBinary):
            if not isinstance(value, dict) or not isinstance(value.get("$binary"), str):
                raise ValueError(f"{table.name}.{column.name} 二进制格式无效")
            value = base64.b64decode(value["$binary"], validate=True)
        values[column.name] = value
    return values


def _scan_bound_environment_values(
    serialized: str, tables: dict[str, list[dict[str, Any]]]
) -> list[str]:
    names: set[str] = set()
    for table_name, column_name in (
        ("provider_accounts", "credential_env_var"),
        ("credential_references", "env_var_name"),
    ):
        for row in tables.get(table_name, []):
            name = row.get(column_name)
            if isinstance(name, str) and name:
                names.add(name)
    findings: list[str] = []
    for name in names:
        secret = os.getenv(name)
        if secret and len(secret) >= 8 and secret in serialized:
            findings.append(f"$.environment_value[{name}]")
    return findings


def _secret_field_name(name: str) -> bool:
    normalized = name.replace("-", "_").replace(" ", "_")
    parts = {part for part in normalized.split("_") if part}
    return normalized in {
        "api_key",
        "apikey",
        "password",
        "secret",
        "access_token",
        "refresh_token",
        "authorization",
        "cookie",
    } or bool(parts & {"authorization", "password", "cookie"})


def _has_user_data(db: Session) -> bool:
    for table in backup_tables():
        if table.name == "provider_presets":
            continue
        primary_key = next(iter(table.primary_key.columns))
        if db.execute(select(primary_key).limit(1)).first() is not None:
            return True
    return False


def _validate_zip_member(info: zipfile.ZipInfo) -> None:
    name = info.filename.replace("\\", "/")
    if name.startswith("/") or ".." in name.split("/") or ":" in name:
        raise ValueError(f"备份 ZIP 路径不安全：{info.filename}")
    if info.flag_bits & 0x1:
        raise ValueError("不支持加密 ZIP")
    compressed = max(1, info.compress_size)
    if info.file_size > 10 * 1024 * 1024 and info.file_size / compressed > MAX_COMPRESSION_RATIO:
        raise ValueError("备份 ZIP 压缩比异常")


def _write_zip_member(archive: zipfile.ZipFile, name: str, payload: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=datetime.now().timetuple()[:6])
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    archive.writestr(info, payload)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
