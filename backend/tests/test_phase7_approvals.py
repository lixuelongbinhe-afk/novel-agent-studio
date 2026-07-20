from __future__ import annotations

from collections.abc import Generator
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app import models
from app.database import Base
from app.schemas import ApprovalCreate, ApprovalDecisionRequest, ApprovalSnapshot
from app.services import approvals


@pytest.fixture
def db(tmp_path: Path) -> Generator[Session, None, None]:
    engine = create_engine(f"sqlite:///{(tmp_path / 'phase7.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def seed_run(
    db: Session,
    *,
    project: models.Project | None = None,
    node_key: str = "prose_approval",
) -> tuple[models.Project, models.WorkflowRun, models.NodeRun]:
    project = project or models.Project(title="Phase 7 小说")
    db.add(project)
    db.flush()
    workflow = models.Workflow(
        project_id=project.id, name=f"审批流-{project.id}-{node_key}"
    )
    db.add(workflow)
    db.flush()
    run = models.WorkflowRun(
        workflow_id=workflow.id,
        project_id=project.id,
        workflow_revision=1,
        status="running",
        plan_json="{}",
        snapshot_json="{}",
    )
    db.add(run)
    db.flush()
    node_run = models.NodeRun(
        workflow_run_id=run.id,
        node_key=node_key,
        node_type="human_approval",
        status="running",
        activated=True,
    )
    db.add(node_run)
    db.flush()
    return project, run, node_run


def create_prose_approval(
    db: Session,
    *,
    project: models.Project,
    run: models.WorkflowRun,
    node_run: models.NodeRun,
    value: str = "第一版正文",
    revision: int = 1,
    round_number: int = 1,
    expires_at: datetime | None = None,
) -> models.ApprovalRequest:
    return approvals.create_approval(
        db,
        ApprovalCreate(
            project_id=project.id,
            workflow_run_id=run.id,
            node_run_id=node_run.id,
            node_key=node_run.node_key,
            approval_type="prose",
            title="正文审批",
            snapshot=ApprovalSnapshot(approval_type="prose", value=value),
            snapshot_revision=revision,
            round_number=round_number,
            expires_at=expires_at,
        ),
    )


def decision(
    action: str,
    *,
    expected_revision: int = 1,
    key: str = "decision-0001",
    note: str = "",
    edited_value: object | None = None,
) -> ApprovalDecisionRequest:
    return ApprovalDecisionRequest.model_validate(
        {
            "action": action,
            "expected_revision": expected_revision,
            "idempotency_key": key,
            "note": note,
            "edited_value": edited_value,
        }
    )


def test_edit_supersedes_without_mutating_snapshot_and_replays_idempotently(
    db: Session,
) -> None:
    project, run, node_run = seed_run(db)
    row = create_prose_approval(db, project=project, run=run, node_run=node_run)
    original_json = row.snapshot_json
    original_hash = row.snapshot_hash

    result = approvals.decide_approval(
        db,
        row.id,
        decision("edit", edited_value="人工编辑后的正文"),
    )

    assert result.approval.status == "superseded"
    assert result.replacement is not None
    assert result.replacement.status == "pending"
    assert result.replacement.snapshot_revision == 2
    assert result.replacement.snapshot.value == "人工编辑后的正文"
    assert row.snapshot_json == original_json
    assert row.snapshot_hash == original_hash

    replay = approvals.decide_approval(
        db,
        row.id,
        decision("edit", edited_value="人工编辑后的正文"),
    )
    assert replay.idempotent_replay is True
    assert replay.replacement is not None
    assert replay.replacement.id == result.replacement.id

    with pytest.raises(HTTPException, match="幂等键") as conflict:
        approvals.decide_approval(
            db,
            row.id,
            decision("edit", edited_value="同一个键的另一份正文"),
        )
    assert conflict.value.status_code == 409


def test_stale_revision_and_project_boundary_are_rejected(db: Session) -> None:
    project, run, node_run = seed_run(db)
    row = create_prose_approval(db, project=project, run=run, node_run=node_run)

    with pytest.raises(HTTPException) as stale:
        approvals.decide_approval(
            db,
            row.id,
            decision("approve", expected_revision=99),
        )
    assert stale.value.status_code == 409

    other_project = models.Project(title="另一个项目")
    db.add(other_project)
    db.flush()
    with pytest.raises(HTTPException, match="不属于所选项目") as boundary:
        create_prose_approval(
            db,
            project=other_project,
            run=run,
            node_run=node_run,
            revision=2,
        )
    assert boundary.value.status_code == 422


def test_expiry_and_run_cancellation_are_terminal(db: Session) -> None:
    project, run, node_run = seed_run(db)
    expired = create_prose_approval(
        db,
        project=project,
        run=run,
        node_run=node_run,
        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
    )
    expired_read = approvals.read_approval(db, expired.id)
    assert expired_read.status == "expired"
    assert expired_read.decision_action == "expire"
    with pytest.raises(HTTPException) as expired_decision:
        approvals.decide_approval(db, expired.id, decision("approve"))
    assert expired_decision.value.status_code == 409

    _, second_run, second_node = seed_run(db, project=project, node_key="metadata_approval")
    pending = create_prose_approval(
        db,
        project=project,
        run=second_run,
        node_run=second_node,
    )
    assert approvals.cancel_pending_for_run(db, second_run.id) == [pending.id]
    cancelled_read = approvals.read_approval(db, pending.id)
    assert cancelled_read.status == "cancelled"
    assert cancelled_read.decision_action == "cancel"


def test_request_changes_allows_at_most_three_rounds(db: Session) -> None:
    project, run, node_run = seed_run(db)
    first = create_prose_approval(db, project=project, run=run, node_run=node_run)
    approvals.decide_approval(
        db,
        first.id,
        decision("request_changes", note="补足冲突动机", key="round-one-request"),
    )
    second = approvals.create_revision_approval(
        db, first, "第二版正文", note="补足冲突动机"
    )
    assert second.round_number == 2

    approvals.decide_approval(
        db,
        second.id,
        decision("request_changes", note="收紧结尾", key="round-two-request"),
    )
    third = approvals.create_revision_approval(db, second, "第三版正文", note="收紧结尾")
    assert third.round_number == 3

    approvals.decide_approval(
        db,
        third.id,
        decision("request_changes", note="仍需修改", key="round-three-request"),
    )
    with pytest.raises(HTTPException, match="最多 3 轮") as maximum:
        approvals.create_revision_approval(db, third, "第四版正文", note="不应创建")
    assert maximum.value.status_code == 409


def test_snapshot_revision_creation_is_idempotent_but_hash_conflicts(db: Session) -> None:
    project, run, node_run = seed_run(db)
    first = create_prose_approval(db, project=project, run=run, node_run=node_run)
    same = create_prose_approval(db, project=project, run=run, node_run=node_run)
    assert same.id == first.id

    with pytest.raises(HTTPException, match="revision") as conflict:
        create_prose_approval(
            db,
            project=project,
            run=run,
            node_run=node_run,
            value="不同内容",
        )
    assert conflict.value.status_code == 409
