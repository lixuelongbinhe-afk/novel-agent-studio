from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.core.config import get_settings


PHASE_1_REVISION = "13da8433608a"
PHASE_2_REVISION = "9f43d2a6c1b8"
PHASE_3_REVISION = "c31e6d7b924f"
PHASE_4_REVISION = "e47a1d8f2c60"
PHASE_5_REVISION = "f8b2c4d6e810"
PHASE_6_REVISION = "a6c8e0f2b419"
PHASE_7_REVISION = "d7e9f1a3c520"
STUDIO_V2_REVISION = "f1a2b3c4d5e6"
PHASE_1_TABLES = {
    "projects",
    "provider_accounts",
    "model_profiles",
    "protocol_configurations",
    "story_entities",
    "style_guides",
    "volumes",
    "chapters",
    "entity_aliases",
    "entity_relations",
    "model_capabilities",
    "model_pricing",
    "chapter_versions",
    "entity_state_changes",
    "foreshadows",
    "scenes",
    "timeline_events",
}
PHASE_2_TABLES = {"provider_presets"}
PHASE_3_TABLES = {
    "credential_references",
    "generic_http_adapter_configurations",
}
PHASE_4_TABLES = {
    "budget_policies",
    "capability_probe_runs",
    "model_invocations",
    "model_route_entries",
    "model_routes",
    "provider_health",
    "rate_limit_policies",
}
PHASE_5_TABLES = {
    "agent_definitions",
    "node_run_attempts",
    "node_runs",
    "workflow_edges",
    "workflow_nodes",
    "workflow_run_events",
    "workflow_runs",
    "workflows",
}
PHASE_6_TABLES = {
    "chapter_summaries",
    "scene_states",
    "chapter_entity_links",
    "context_pins",
    "content_classifications",
    "context_policies",
    "provider_data_policies",
    "context_builds",
    "context_fts",
}
PHASE_7_TABLES = {
    "approval_requests",
    "proposed_change_sets",
    "writeback_audits",
}
STUDIO_V2_TABLES = {
    "studio_project_states",
    "creative_artifacts",
    "studio_messages",
    "generation_jobs",
    "project_snapshots",
}


def upgrade_database(database_url: str | None = None) -> None:
    backend_root = Path(__file__).resolve().parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    resolved_url = database_url or get_settings().database_url
    config.attributes["database_url"] = resolved_url
    config.set_main_option("sqlalchemy.url", resolved_url.replace("%", "%%"))
    migration_engine = create_engine(resolved_url)
    try:
        table_names = set(inspect(migration_engine).get_table_names())
        with migration_engine.connect() as connection:
            current_revision = (
                connection.execute(
                    text("SELECT version_num FROM alembic_version")
                ).scalar_one_or_none()
                if "alembic_version" in table_names
                else None
            )
    finally:
        migration_engine.dispose()

    database_path = _sqlite_database_path(resolved_url)
    needs_schema_upgrade = bool(table_names) and current_revision != STUDIO_V2_REVISION
    backup_path = (
        _prepare_migration_backup(database_path, current_revision)
        if needs_schema_upgrade and database_path is not None
        else None
    )
    journal_path = _migration_journal_path(database_path)
    if backup_path is not None:
        _write_migration_journal(
            journal_path,
            {
                "status": "running",
                "from_revision": current_revision or "legacy-unversioned",
                "to_revision": STUDIO_V2_REVISION,
                "backup": str(backup_path),
                "started_at": datetime.now(timezone.utc).isoformat(),
            },
        )
    try:
        if "alembic_version" not in table_names and table_names & (
            PHASE_1_TABLES
            | PHASE_2_TABLES
            | PHASE_3_TABLES
            | PHASE_4_TABLES
            | PHASE_5_TABLES
            | PHASE_6_TABLES
            | PHASE_7_TABLES
            | STUDIO_V2_TABLES
        ):
            baseline = _legacy_baseline(table_names)
            command.stamp(config, baseline)
        command.upgrade(config, "head")
        _ensure_provider_presets(resolved_url)
        _ensure_context_defaults(resolved_url)
        _validate_migrated_database(resolved_url)
    except BaseException as exc:
        restored = False
        if backup_path is not None and database_path is not None:
            _restore_migration_backup(database_path, backup_path)
            restored = True
        _write_migration_journal(
            journal_path,
            {
                "status": "failed_restored" if restored else "failed",
                "from_revision": current_revision or "legacy-unversioned",
                "to_revision": STUDIO_V2_REVISION,
                "backup": str(backup_path) if backup_path is not None else None,
                "error": f"{type(exc).__name__}: {exc}"[:2_000],
                "completed_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        raise
    else:
        if backup_path is not None:
            _write_migration_journal(
                journal_path,
                {
                    "status": "completed",
                    "from_revision": current_revision or "legacy-unversioned",
                    "to_revision": STUDIO_V2_REVISION,
                    "backup": str(backup_path),
                    "completed_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            _prune_migration_backups(backup_path.parent)


def _sqlite_database_path(database_url: str) -> Path | None:
    parsed = make_url(database_url)
    if parsed.get_backend_name() != "sqlite" or parsed.database in {None, ":memory:"}:
        return None
    return Path(parsed.database).expanduser().resolve()


def _migration_journal_path(database_path: Path | None) -> Path | None:
    return (
        database_path.with_suffix(f"{database_path.suffix}.migration.json")
        if database_path is not None
        else None
    )


def _prepare_migration_backup(
    database_path: Path, current_revision: str | None
) -> Path:
    database_size = database_path.stat().st_size
    required_bytes = max(database_size * 2, 16 * 1024 * 1024)
    free_bytes = shutil.disk_usage(database_path.parent).free
    if free_bytes < required_bytes:
        raise RuntimeError(
            "Insufficient disk space for a safe database migration: "
            f"need {required_bytes} bytes, have {free_bytes} bytes"
        )
    backup_dir = database_path.parent / "migration-backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    revision = (current_revision or "legacy").replace("/", "_")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup_path = backup_dir / f"{database_path.stem}-{timestamp}-{revision}.db"
    with closing(sqlite3.connect(database_path)) as source, closing(
        sqlite3.connect(backup_path)
    ) as target:
        source.backup(target)
    return backup_path


def _restore_migration_backup(database_path: Path, backup_path: Path) -> None:
    with closing(sqlite3.connect(backup_path)) as source, closing(
        sqlite3.connect(database_path)
    ) as target:
        source.backup(target)


def _validate_migrated_database(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        with engine.connect() as connection:
            integrity = connection.exec_driver_sql("PRAGMA integrity_check").scalar_one()
            if integrity != "ok":
                raise RuntimeError(f"Database integrity check failed: {integrity}")
            foreign_keys = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
            if foreign_keys:
                raise RuntimeError(
                    "Database foreign key check failed: "
                    + " | ".join(str(value) for value in foreign_keys[0])
                )
    finally:
        engine.dispose()


def _write_migration_journal(path: Path | None, payload: dict[str, Any]) -> None:
    if path is None:
        return
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temporary.replace(path)


def _prune_migration_backups(directory: Path, *, keep: int = 3) -> None:
    backups = sorted(
        directory.glob("*.db"),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    for path in backups[keep:]:
        path.unlink(missing_ok=True)


def _legacy_baseline(table_names: set[str]) -> str:
    missing_phase_1 = PHASE_1_TABLES - table_names
    if missing_phase_1:
        missing = ", ".join(sorted(missing_phase_1))
        raise RuntimeError(
            "Legacy database is incomplete and was not modified; missing tables: "
            f"{missing}"
        )
    phase_2_present = PHASE_2_TABLES <= table_names
    phase_3_present = PHASE_3_TABLES <= table_names
    phase_4_present = PHASE_4_TABLES <= table_names
    phase_5_present = PHASE_5_TABLES <= table_names
    phase_6_present = PHASE_6_TABLES <= table_names
    phase_7_present = PHASE_7_TABLES <= table_names
    studio_v2_present = STUDIO_V2_TABLES <= table_names
    if table_names & STUDIO_V2_TABLES and not studio_v2_present:
        raise RuntimeError("Legacy database has a partial Studio V2 schema and was not modified")
    if studio_v2_present and not phase_7_present:
        raise RuntimeError("Studio V2 tables require the Phase 7 schema")
    if table_names & PHASE_3_TABLES and not phase_3_present:
        raise RuntimeError(
            "Legacy database has a partial Phase 3 schema and was not modified"
        )
    if phase_3_present and not phase_2_present:
        raise RuntimeError(
            "Legacy database has Phase 3 tables but is missing the Phase 2 schema"
        )
    if table_names & PHASE_4_TABLES and not phase_4_present:
        raise RuntimeError(
            "Legacy database has a partial Phase 4 schema and was not modified"
        )
    if phase_4_present and not phase_3_present:
        raise RuntimeError(
            "Legacy database has Phase 4 tables but is missing the Phase 3 schema"
        )
    if table_names & PHASE_5_TABLES and not phase_5_present:
        raise RuntimeError(
            "Legacy database has a partial Phase 5 schema and was not modified"
        )
    if phase_5_present and not phase_4_present:
        raise RuntimeError(
            "Legacy database has Phase 5 tables but is missing the Phase 4 schema"
        )
    if table_names & PHASE_6_TABLES and not phase_6_present:
        raise RuntimeError(
            "Legacy database has a partial Phase 6 schema and was not modified"
        )
    if phase_6_present and not phase_5_present:
        raise RuntimeError(
            "Legacy database has Phase 6 tables but is missing the Phase 5 schema"
        )
    if table_names & PHASE_7_TABLES and not phase_7_present:
        raise RuntimeError(
            "Legacy database has a partial Phase 7 schema and was not modified"
        )
    if phase_7_present and not phase_6_present:
        raise RuntimeError(
            "Legacy database has Phase 7 tables but is missing the Phase 6 schema"
        )
    if studio_v2_present:
        return STUDIO_V2_REVISION
    if phase_7_present:
        return PHASE_7_REVISION
    if phase_6_present:
        return PHASE_6_REVISION
    if phase_5_present:
        return PHASE_5_REVISION
    if phase_4_present:
        return PHASE_4_REVISION
    if phase_3_present:
        return PHASE_3_REVISION
    if phase_2_present:
        return PHASE_2_REVISION
    return PHASE_1_REVISION


def _ensure_provider_presets(database_url: str) -> None:
    from app.services.models import ensure_provider_presets

    seed_engine = create_engine(database_url)
    try:
        with Session(seed_engine) as db, db.begin():
            ensure_provider_presets(db)
    finally:
        seed_engine.dispose()


def _ensure_context_defaults(database_url: str) -> None:
    from app.services.context_memory import ensure_all_context_defaults

    seed_engine = create_engine(database_url)
    try:
        with Session(seed_engine) as db, db.begin():
            ensure_all_context_defaults(db)
    finally:
        seed_engine.dispose()
