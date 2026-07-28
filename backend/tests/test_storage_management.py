from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app import models
from app.database import Base
from app.services.storage_management import cleanup_storage, storage_report


@pytest.fixture
def db(tmp_path: Path) -> Generator[Session, None, None]:
    engine = create_engine(f"sqlite:///{(tmp_path / 'storage.db').as_posix()}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session:
        yield session
    engine.dispose()


def test_storage_cleanup_is_safe_dry_runnable_and_integrity_checked(db: Session) -> None:
    now = datetime.now(timezone.utc)
    old = now - timedelta(days=120)
    project = models.Project(title="存储治理")
    db.add(project)
    db.flush()
    workflow = models.Workflow(project_id=project.id, name="长期工作流")
    db.add(workflow)
    db.flush()
    finished = _run(workflow.id, project.id, "completed", old)
    active = _run(workflow.id, project.id, "waiting_approval", old)
    db.add_all([finished, active])
    db.flush()
    db.add_all(
        [
            _event(finished.id, 1, "node_output_delta", old),
            _event(finished.id, 2, "debug_trace", old),
            _event(finished.id, 3, "run_completed", old),
            _event(active.id, 1, "node_output_delta", old),
        ]
    )
    db.add(_context(project.id, active.id, old, "active-protected"))
    for index in range(35):
        db.add(
            _context(
                project.id,
                finished.id,
                now - timedelta(minutes=index),
                f"finished-{index}",
            )
        )
    db.commit()

    dry_run = cleanup_storage(db, dry_run=True, project_id=project.id)
    assert dry_run.deleted_records == 0
    assert {item.key: item.records for item in dry_run.items} == {
        "workflow_deltas": 1,
        "workflow_events": 1,
        "context_builds": 5,
    }
    assert db.scalar(select(func.count(models.WorkflowRunEvent.id))) == 4
    db.rollback()

    with db.begin():
        result = cleanup_storage(db, dry_run=False, project_id=project.id)
    assert result.deleted_records == 7
    remaining_types = set(db.scalars(select(models.WorkflowRunEvent.event_type)).all())
    assert remaining_types == {"run_completed", "node_output_delta"}
    assert db.scalar(select(func.count(models.ContextBuild.id))) == 31
    assert db.scalar(
        select(func.count(models.ContextBuild.id)).where(
            models.ContextBuild.build_hash == "active-protected"
        )
    ) == 1

    report = storage_report(db, project_id=project.id)
    assert report.database_bytes > 0
    assert {item.key for item in report.categories} == {
        "workflow",
        "context",
        "snapshots",
        "usage",
        "other",
    }
    assert sum(item.records for item in report.cleanup) == 0


def _run(workflow_id: int, project_id: int, status: str, created_at: datetime) -> models.WorkflowRun:
    return models.WorkflowRun(
        workflow_id=workflow_id,
        project_id=project_id,
        workflow_revision=1,
        status=status,
        plan_json="{}",
        snapshot_json="{}",
        created_at=created_at,
        updated_at=created_at,
        completed_at=created_at if status == "completed" else None,
    )


def _event(
    run_id: int, sequence: int, event_type: str, created_at: datetime
) -> models.WorkflowRunEvent:
    return models.WorkflowRunEvent(
        workflow_run_id=run_id,
        sequence=sequence,
        event_type=event_type,
        payload_json='{"content":"1234567890"}',
        created_at=created_at,
    )


def _context(
    project_id: int, run_id: int, created_at: datetime, build_hash: str
) -> models.ContextBuild:
    return models.ContextBuild(
        project_id=project_id,
        workflow_run_id=run_id,
        request_json="{}",
        result_json="{}",
        context_text="上下文" * 20,
        build_hash=build_hash,
        created_at=created_at,
    )
