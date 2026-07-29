from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, cast

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from sqlalchemy import DateTime, LargeBinary, Table, delete, insert, select, text, update
from sqlalchemy.engine import RowMapping
from sqlalchemy.orm import Session

from app import models
from app.core.config import get_settings
from app.database import Base
from app.migrations import current_schema_revision
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
BackupArchiveSource = bytes | Path
ARCHIVE_FILES = frozenset({"manifest.json", "data.json"})
MAX_ARCHIVE_ENTRIES = 8
MAX_COMPRESSION_RATIO = 250
ENCRYPTED_BACKUP_MAGIC = b"NASBKP1\0"
ENCRYPTED_BACKUP_SALT_BYTES = 16
ENCRYPTED_BACKUP_NONCE_BYTES = 12
ENCRYPTED_BACKUP_TAG_BYTES = 16
ENCRYPTED_BACKUP_OVERHEAD = (
    len(ENCRYPTED_BACKUP_MAGIC)
    + ENCRYPTED_BACKUP_SALT_BYTES
    + ENCRYPTED_BACKUP_NONCE_BYTES
    + ENCRYPTED_BACKUP_TAG_BYTES
)

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
    archive_path = create_backup_archive_file(db)
    try:
        return archive_path.read_bytes()
    finally:
        archive_path.unlink(missing_ok=True)


def create_backup_archive_file(db: Session) -> Path:
    settings = get_settings()
    data_path = _temporary_path(".json")
    archive_path = _temporary_path(".nasbackup.zip")
    counts: list[BackupTableCount] = []
    findings: list[str] = []
    environment_secrets = _bound_environment_secrets(db)
    data_hash = hashlib.sha256()
    data_size = 0

    def write_data(target: Any, payload: bytes) -> None:
        nonlocal data_size
        data_size += len(payload)
        if data_size > settings.max_backup_uncompressed_bytes:
            raise ValueError("备份数据超过允许的解压后大小")
        data_hash.update(payload)
        target.write(payload)

    try:
        with data_path.open("wb") as data_file:
            write_data(data_file, b'{"schema_version":2,"tables":{')
            for table_index, table in enumerate(backup_tables()):
                if table_index:
                    write_data(data_file, b",")
                write_data(data_file, _canonical_json(table.name) + b":[")
                row_count = 0
                for row in db.execute(select(table)).mappings():
                    serialized = _serialize_row(table.name, row)
                    findings.extend(
                        scan_backup_secrets(
                            serialized, f"$.tables.{table.name}[{row_count}]"
                        )
                    )
                    row_bytes = _canonical_json(serialized)
                    findings.extend(
                        _scan_serialized_environment_values(row_bytes, environment_secrets)
                    )
                    if row_count:
                        write_data(data_file, b",")
                    write_data(data_file, row_bytes)
                    row_count += 1
                write_data(data_file, b"]")
                counts.append(BackupTableCount(table=table.name, records=row_count))
            write_data(data_file, b"}}")

        if findings:
            locations = ", ".join(sorted(set(findings))[:12])
            raise ValueError(f"备份 Secret 扫描失败：{locations}")

        manifest = BackupManifestRead(
            format=BACKUP_FORMAT,
            schema_version=BACKUP_SCHEMA_VERSION,
            app_version=settings.app_version,
            migration_revision=current_schema_revision(),
            created_at=datetime.now(timezone.utc),
            data_sha256=data_hash.hexdigest(),
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
        with zipfile.ZipFile(
            archive_path, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            _write_zip_member(archive, "manifest.json", manifest_bytes)
            _write_zip_file(archive, "data.json", data_path)
        if archive_path.stat().st_size > settings.max_backup_bytes:
            raise ValueError("备份超过允许的压缩文件大小")
        return archive_path
    except Exception:
        archive_path.unlink(missing_ok=True)
        raise
    finally:
        data_path.unlink(missing_ok=True)


def encrypt_backup_archive_file(archive_path: Path, password: str) -> Path:
    password_bytes = _validate_backup_password(password)
    salt = os.urandom(ENCRYPTED_BACKUP_SALT_BYTES)
    nonce = os.urandom(ENCRYPTED_BACKUP_NONCE_BYTES)
    header = ENCRYPTED_BACKUP_MAGIC + salt + nonce
    key = _derive_backup_key(password_bytes, salt)
    encrypted_path = _temporary_path(".nasbackup.enc")
    try:
        encryptor = Cipher(algorithms.AES(key), modes.GCM(nonce)).encryptor()
        encryptor.authenticate_additional_data(header)
        with archive_path.open("rb") as source, encrypted_path.open("wb") as target:
            target.write(header)
            while chunk := source.read(1024 * 1024):
                target.write(encryptor.update(chunk))
            target.write(encryptor.finalize())
            target.write(encryptor.tag)
        if encrypted_path.stat().st_size > get_settings().max_backup_bytes + ENCRYPTED_BACKUP_OVERHEAD:
            raise ValueError("加密备份超过允许的文件大小")
        return encrypted_path
    except Exception:
        encrypted_path.unlink(missing_ok=True)
        raise


def decrypt_backup_archive_file(archive_path: Path, password: str | None) -> tuple[Path, bool]:
    if not is_encrypted_backup(archive_path):
        return archive_path, False
    if password is None:
        raise ValueError("此备份已加密，请输入备份密码")
    password_bytes = _validate_backup_password(password)
    size = archive_path.stat().st_size
    if size <= ENCRYPTED_BACKUP_OVERHEAD:
        raise ValueError("加密备份文件不完整")
    with archive_path.open("rb") as source:
        header = source.read(
            len(ENCRYPTED_BACKUP_MAGIC)
            + ENCRYPTED_BACKUP_SALT_BYTES
            + ENCRYPTED_BACKUP_NONCE_BYTES
        )
        salt_start = len(ENCRYPTED_BACKUP_MAGIC)
        nonce_start = salt_start + ENCRYPTED_BACKUP_SALT_BYTES
        salt = header[salt_start:nonce_start]
        nonce = header[nonce_start:]
        source.seek(-ENCRYPTED_BACKUP_TAG_BYTES, os.SEEK_END)
        tag = source.read(ENCRYPTED_BACKUP_TAG_BYTES)
        ciphertext_bytes = size - len(header) - ENCRYPTED_BACKUP_TAG_BYTES
        source.seek(len(header))
        key = _derive_backup_key(password_bytes, salt)
        decrypted_path = _temporary_path(".nasbackup.zip")
        try:
            decryptor = Cipher(algorithms.AES(key), modes.GCM(nonce, tag)).decryptor()
            decryptor.authenticate_additional_data(header)
            remaining = ciphertext_bytes
            with decrypted_path.open("wb") as target:
                while remaining:
                    chunk = source.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("加密备份文件不完整")
                    remaining -= len(chunk)
                    target.write(decryptor.update(chunk))
                target.write(decryptor.finalize())
            if decrypted_path.stat().st_size > get_settings().max_backup_bytes:
                raise ValueError("解密后的备份超过允许的文件大小")
            return decrypted_path, True
        except InvalidTag as exc:
            decrypted_path.unlink(missing_ok=True)
            raise ValueError("备份密码错误或加密文件已损坏") from exc
        except Exception:
            decrypted_path.unlink(missing_ok=True)
            raise


def is_encrypted_backup(archive_path: Path) -> bool:
    with archive_path.open("rb") as source:
        return source.read(len(ENCRYPTED_BACKUP_MAGIC)) == ENCRYPTED_BACKUP_MAGIC


def preview_backup_archive(db: Session, archive_source: BackupArchiveSource) -> BackupPreviewRead:
    loaded = load_backup_archive(archive_source)
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
    archive_source: BackupArchiveSource,
    *,
    strategy: RestoreStrategy,
    expected_sha256: str,
) -> BackupRestoreRead:
    loaded = load_backup_archive(archive_source)
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


def load_backup_archive(archive_source: BackupArchiveSource) -> LoadedBackup:
    settings = get_settings()
    archive_bytes = (
        len(archive_source) if isinstance(archive_source, bytes) else archive_source.stat().st_size
    )
    if not archive_bytes:
        raise ValueError("备份文件为空")
    if archive_bytes > settings.max_backup_bytes:
        raise ValueError("备份文件超过大小限制")
    archive_sha256 = _source_sha256(archive_source)
    stream: Any = (
        io.BytesIO(archive_source) if isinstance(archive_source, bytes) else archive_source
    )
    if not zipfile.is_zipfile(stream):
        raise ValueError("备份不是有效 ZIP 文件")
    data_path = _temporary_path(".json")
    try:
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
            data_hash = hashlib.sha256()
            with archive.open("data.json") as source, data_path.open("wb") as target:
                while chunk := source.read(1024 * 1024):
                    data_hash.update(chunk)
                    target.write(chunk)

        try:
            manifest_value = json.loads(manifest_bytes)
            with data_path.open("r", encoding="utf-8") as data_file:
                data_value = json.load(data_file)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("备份 JSON 无效") from exc
        manifest = BackupManifestRead.model_validate(manifest_value)
        if data_hash.hexdigest() != manifest.data_sha256:
            raise ValueError("备份 data.json 哈希校验失败")
        migrated = _migrate_backup_data(data_value, manifest.schema_version)
        tables = _validate_table_payload(migrated, manifest)
        findings = scan_backup_secrets(migrated)
        findings.extend(_scan_bound_environment_file(data_path, tables))
        return LoadedBackup(
            manifest=manifest,
            tables=tables,
            archive_sha256=archive_sha256,
            archive_bytes=archive_bytes,
            uncompressed_bytes=uncompressed_bytes,
            secret_findings=sorted(set(findings)),
        )
    finally:
        data_path.unlink(missing_ok=True)


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


def _scan_bound_environment_file(
    data_path: Path, tables: dict[str, list[dict[str, Any]]]
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
        if secret and len(secret) >= 8 and _file_contains(data_path, secret.encode("utf-8")):
            findings.append(f"$.environment_value[{name}]")
    return findings


def _file_contains(path: Path, needle: bytes) -> bool:
    overlap = max(0, len(needle) - 1)
    previous = b""
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            window = previous + chunk
            if needle in window:
                return True
            previous = window[-overlap:] if overlap else b""
    return False


def _source_sha256(source: BackupArchiveSource) -> str:
    if isinstance(source, bytes):
        return hashlib.sha256(source).hexdigest()
    digest = hashlib.sha256()
    with source.open("rb") as archive_file:
        while chunk := archive_file.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _bound_environment_secrets(db: Session) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for table_name, column_name in (
        ("provider_accounts", "credential_env_var"),
        ("credential_references", "env_var_name"),
    ):
        table = Base.metadata.tables.get(table_name)
        if table is None or column_name not in table.c:
            continue
        for name in db.scalars(select(table.c[column_name])):
            if not isinstance(name, str) or not name:
                continue
            secret = os.getenv(name)
            if secret and len(secret) >= 8:
                result[name] = secret.encode("utf-8")
    return result


def _scan_serialized_environment_values(
    payload: bytes, environment_secrets: dict[str, bytes]
) -> list[str]:
    return [
        f"$.environment_value[{name}]"
        for name, secret in environment_secrets.items()
        if secret in payload
    ]


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


def _write_zip_file(archive: zipfile.ZipFile, name: str, source_path: Path) -> None:
    info = zipfile.ZipInfo(name, date_time=datetime.now().timetuple()[:6])
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o600 << 16
    with source_path.open("rb") as source, archive.open(info, "w") as target:
        shutil.copyfileobj(source, target, length=1024 * 1024)


def _temporary_path(suffix: str) -> Path:
    descriptor, raw_path = tempfile.mkstemp(prefix="novel-agent-studio-", suffix=suffix)
    os.close(descriptor)
    return Path(raw_path)


def _validate_backup_password(password: str) -> bytes:
    encoded = password.encode("utf-8")
    if len(encoded) < 8:
        raise ValueError("备份密码至少需要 8 个字节")
    if len(encoded) > 1024:
        raise ValueError("备份密码过长")
    return encoded


def _derive_backup_key(password: bytes, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(password)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
