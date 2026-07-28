from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import Select, delete, func, select, text
from sqlalchemy.orm import Session

from app import models
from app.core.config import get_settings
from app.database import Base
from app.schemas.release import (
    StorageCategoryRead,
    StorageCleanupItemRead,
    StorageCleanupRead,
    StorageReportRead,
)


_TERMINAL_RUN_STATUSES = frozenset({"completed", "failed", "cancelled", "interrupted"})
_IMPORTANT_EVENTS = frozenset(
    {
        "run_started",
        "run_completed",
        "run_failed",
        "run_cancelled",
        "run_interrupted",
        "node_started",
        "node_completed",
        "node_failed",
        "node_cancelled",
        "approval_required",
        "approval_resolved",
        "writeback_completed",
    }
)
_CATEGORY_TABLES = {
    "workflow": frozenset(
        {
            "workflow_runs",
            "node_runs",
            "node_run_attempts",
            "workflow_run_events",
            "approval_requests",
            "proposed_change_sets",
            "writeback_audits",
        }
    ),
    "context": frozenset({"context_builds", "context_fts", "context_fts_data"}),
    "snapshots": frozenset({"project_snapshots", "chapter_versions"}),
    "usage": frozenset({"model_invocations", "budget_reservations", "usage_ledger_entries"}),
}
_CATEGORY_LABELS = {
    "workflow": "工作流历史",
    "context": "上下文缓存",
    "snapshots": "快照与版本",
    "usage": "模型用量记录",
    "other": "项目与配置",
}


@dataclass(frozen=True)
class CleanupSelection:
    delta_events: Select[tuple[int]]
    ordinary_events: Select[tuple[int]]
    context_builds: Select[tuple[int]]


def storage_report(db: Session, *, project_id: int | None = None) -> StorageReportRead:
    page_size = int(db.scalar(text("PRAGMA page_size")) or 0)
    freelist = int(db.scalar(text("PRAGMA freelist_count")) or 0)
    database_path = _database_path(db)
    database_bytes = database_path.stat().st_size if database_path and database_path.exists() else 0
    wal_path = Path(f"{database_path}-wal") if database_path else None
    wal_bytes = wal_path.stat().st_size if wal_path and wal_path.exists() else 0
    table_bytes = _table_bytes(db)
    table_records = _table_records(db)
    categories = []
    for key, label in _CATEGORY_LABELS.items():
        names = _category_names(key)
        categories.append(
            StorageCategoryRead(
                key=key,
                label=label,
                bytes=sum(table_bytes.get(name, 0) for name in names),
                records=sum(table_records.get(name, 0) for name in names),
            )
        )
    return StorageReportRead(
        database_bytes=database_bytes,
        wal_bytes=wal_bytes,
        reusable_bytes=page_size * freelist,
        categories=categories,
        cleanup=_cleanup_items(db, _cleanup_selection(project_id)),
        generated_at=datetime.now(timezone.utc),
    )


def cleanup_storage(
    db: Session, *, dry_run: bool, project_id: int | None = None
) -> StorageCleanupRead:
    selection = _cleanup_selection(project_id)
    items = _cleanup_items(db, selection)
    deleted_records = 0 if dry_run else sum(item.records for item in items)
    if not dry_run:
        for model, ids in (
            (models.WorkflowRunEvent, selection.delta_events),
            (models.WorkflowRunEvent, selection.ordinary_events),
            (models.ContextBuild, selection.context_builds),
        ):
            db.execute(delete(model).where(model.id.in_(ids)))
        violations = db.execute(text("PRAGMA foreign_key_check")).first()
        if violations is not None:
            raise ValueError(f"清理后的外键完整性检查失败：{tuple(violations)}")
    return StorageCleanupRead(
        dry_run=dry_run,
        project_id=project_id,
        items=items,
        deleted_records=deleted_records,
        integrity="ok",
        completed_at=datetime.now(timezone.utc),
    )


def _cleanup_selection(project_id: int | None) -> CleanupSelection:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    terminal_run_ids = select(models.WorkflowRun.id).where(
        models.WorkflowRun.status.in_(_TERMINAL_RUN_STATUSES)
    )
    if project_id is not None:
        terminal_run_ids = terminal_run_ids.where(models.WorkflowRun.project_id == project_id)

    delta_events = select(models.WorkflowRunEvent.id).where(
        models.WorkflowRunEvent.workflow_run_id.in_(terminal_run_ids),
        models.WorkflowRunEvent.event_type == "node_output_delta",
        models.WorkflowRunEvent.created_at
        < now - timedelta(days=settings.workflow_delta_retention_days),
    )
    ordinary_events = select(models.WorkflowRunEvent.id).where(
        models.WorkflowRunEvent.workflow_run_id.in_(terminal_run_ids),
        models.WorkflowRunEvent.event_type != "node_output_delta",
        models.WorkflowRunEvent.event_type.not_in(_IMPORTANT_EVENTS),
        models.WorkflowRunEvent.created_at
        < now - timedelta(days=settings.workflow_event_retention_days),
    )

    ranked_context = select(
        models.ContextBuild.id.label("id"),
        models.ContextBuild.workflow_run_id.label("workflow_run_id"),
        func.row_number()
        .over(
            partition_by=models.ContextBuild.project_id,
            order_by=(models.ContextBuild.created_at.desc(), models.ContextBuild.id.desc()),
        )
        .label("retention_rank"),
    )
    if project_id is not None:
        ranked_context = ranked_context.where(models.ContextBuild.project_id == project_id)
    ranked = ranked_context.subquery()
    active_run_ids = select(models.WorkflowRun.id).where(
        models.WorkflowRun.status.not_in(_TERMINAL_RUN_STATUSES)
    )
    context_builds = select(ranked.c.id).where(
        ranked.c.retention_rank > settings.context_builds_per_project,
        (ranked.c.workflow_run_id.is_(None) | ranked.c.workflow_run_id.not_in(active_run_ids)),
    )
    return CleanupSelection(
        delta_events=delta_events,
        ordinary_events=ordinary_events,
        context_builds=context_builds,
    )


def _cleanup_items(db: Session, selection: CleanupSelection) -> list[StorageCleanupItemRead]:
    specifications = (
        (
            "workflow_deltas",
            "过期流式增量",
            models.WorkflowRunEvent,
            selection.delta_events,
            func.length(models.WorkflowRunEvent.payload_json),
        ),
        (
            "workflow_events",
            "过期非关键事件",
            models.WorkflowRunEvent,
            selection.ordinary_events,
            func.length(models.WorkflowRunEvent.payload_json),
        ),
        (
            "context_builds",
            "超额上下文快照",
            models.ContextBuild,
            selection.context_builds,
            func.length(models.ContextBuild.request_json)
            + func.length(models.ContextBuild.result_json)
            + func.length(models.ContextBuild.context_text),
        ),
    )
    items: list[StorageCleanupItemRead] = []
    for key, label, model, ids, size_expression in specifications:
        count, estimated = db.execute(
            select(func.count(model.id), func.coalesce(func.sum(size_expression), 0)).where(
                model.id.in_(ids)
            )
        ).one()
        items.append(
            StorageCleanupItemRead(
                key=key,
                label=label,
                records=int(count),
                estimated_bytes=int(estimated),
            )
        )
    return items


def _database_path(db: Session) -> Path | None:
    rows = db.execute(text("PRAGMA database_list")).all()
    for _, name, raw_path in rows:
        if name == "main" and raw_path:
            return Path(str(raw_path)).resolve()
    return None


def _table_bytes(db: Session) -> dict[str, int]:
    try:
        rows = db.execute(text("SELECT name, SUM(pgsize) FROM dbstat GROUP BY name")).all()
    except Exception:
        return {}
    owners = _storage_owners()
    result: dict[str, int] = {}
    for name, size in rows:
        owner = owners.get(str(name), str(name))
        result[owner] = result.get(owner, 0) + int(size or 0)
    return result


def _storage_owners() -> dict[str, str]:
    result: dict[str, str] = {}
    for table in Base.metadata.tables.values():
        result[table.name] = table.name
        for index in table.indexes:
            if index.name:
                result[index.name] = table.name
    return result


def _table_records(db: Session) -> dict[str, int]:
    return {
        table.name: int(db.scalar(select(func.count()).select_from(table)) or 0)
        for table in Base.metadata.sorted_tables
    }


def _category_names(key: str) -> frozenset[str]:
    if key != "other":
        return _CATEGORY_TABLES[key]
    assigned = frozenset().union(*_CATEGORY_TABLES.values())
    return frozenset(Base.metadata.tables) - assigned
