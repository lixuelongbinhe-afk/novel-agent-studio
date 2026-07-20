from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, cast

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.repositories import get_or_404, require_revision
from app.schemas import (
    ApprovalCreate,
    ApprovalDecisionRead,
    ApprovalDecisionRequest,
    ApprovalRequestRead,
    ApprovalSnapshot,
)


TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled", "interrupted"}
TERMINAL_APPROVAL_STATUSES = {
    "approved",
    "changes_requested",
    "rejected",
    "expired",
    "cancelled",
    "superseded",
}


def create_approval(db: Session, payload: ApprovalCreate) -> models.ApprovalRequest:
    project = get_or_404(db, models.Project, payload.project_id)
    run = cast(models.WorkflowRun, get_or_404(db, models.WorkflowRun, payload.workflow_run_id))
    if run.project_id != project.id:
        raise HTTPException(status_code=422, detail="审批运行不属于所选项目")
    if run.status in TERMINAL_RUN_STATUSES or run.cancel_requested:
        raise HTTPException(status_code=409, detail="终态或已取消运行不能创建审批")
    node_run = cast(models.NodeRun, get_or_404(db, models.NodeRun, payload.node_run_id))
    if node_run.workflow_run_id != run.id or node_run.node_key != payload.node_key:
        raise HTTPException(status_code=422, detail="审批节点与运行节点不匹配")
    if payload.snapshot.approval_type != payload.approval_type:
        raise HTTPException(status_code=422, detail="审批快照类型不匹配")
    if payload.parent_approval_id is not None:
        parent = cast(
            models.ApprovalRequest,
            get_or_404(db, models.ApprovalRequest, payload.parent_approval_id),
        )
        if parent.workflow_run_id != run.id or parent.node_key != payload.node_key:
            raise HTTPException(status_code=422, detail="父审批不属于同一运行节点")
    snapshot_json = _dump(payload.snapshot.model_dump(mode="json"))
    snapshot_hash = _hash_json(payload.snapshot.model_dump(mode="json"))
    existing = db.scalar(
        select(models.ApprovalRequest).where(
            models.ApprovalRequest.workflow_run_id == run.id,
            models.ApprovalRequest.node_key == payload.node_key,
            models.ApprovalRequest.snapshot_revision == payload.snapshot_revision,
        )
    )
    if existing is not None:
        if existing.snapshot_hash == snapshot_hash:
            return existing
        raise HTTPException(status_code=409, detail="审批快照 revision 已被其他内容占用")
    row = models.ApprovalRequest(
        project_id=project.id,
        workflow_run_id=run.id,
        node_run_id=node_run.id,
        node_key=payload.node_key,
        approval_type=payload.approval_type,
        status="pending",
        title=payload.title,
        instructions=payload.instructions,
        snapshot_json=snapshot_json,
        snapshot_hash=snapshot_hash,
        snapshot_revision=payload.snapshot_revision,
        round_number=payload.round_number,
        parent_approval_id=payload.parent_approval_id,
        expires_at=payload.expires_at,
    )
    db.add(row)
    db.flush()
    return row


def list_approvals(
    db: Session,
    *,
    project_id: int | None = None,
    workflow_run_id: int | None = None,
    status: str | None = None,
) -> list[ApprovalRequestRead]:
    expire_pending(db, project_id=project_id)
    statement = select(models.ApprovalRequest).where(
        models.ApprovalRequest.deleted_at.is_(None)
    )
    if project_id is not None:
        statement = statement.where(models.ApprovalRequest.project_id == project_id)
    if workflow_run_id is not None:
        statement = statement.where(
            models.ApprovalRequest.workflow_run_id == workflow_run_id
        )
    if status is not None:
        statement = statement.where(models.ApprovalRequest.status == status)
    rows = db.scalars(
        statement.order_by(models.ApprovalRequest.created_at.desc(), models.ApprovalRequest.id.desc())
    ).all()
    return [approval_read(row) for row in rows]


def read_approval(db: Session, approval_id: int) -> ApprovalRequestRead:
    row = cast(models.ApprovalRequest, get_or_404(db, models.ApprovalRequest, approval_id))
    _expire_row(row)
    return approval_read(row)


def decide_approval(
    db: Session,
    approval_id: int,
    payload: ApprovalDecisionRequest,
) -> ApprovalDecisionRead:
    row = cast(models.ApprovalRequest, get_or_404(db, models.ApprovalRequest, approval_id))
    decision_value = payload.model_dump(mode="json", exclude={"expected_revision"})
    decision_hash = _hash_json(decision_value)
    if row.decision_idempotency_key is not None:
        if (
            row.decision_idempotency_key == payload.idempotency_key
            and row.decision_hash == decision_hash
        ):
            replay_replacement = (
                db.get(models.ApprovalRequest, row.superseded_by_id)
                if row.superseded_by_id is not None
                else None
            )
            return ApprovalDecisionRead(
                approval=approval_read(row),
                replacement=(
                    approval_read(replay_replacement)
                    if replay_replacement is not None
                    else None
                ),
                idempotent_replay=True,
            )
        if row.decision_idempotency_key == payload.idempotency_key:
            raise HTTPException(status_code=409, detail="幂等键已用于不同审批决定")
    _expire_row(row)
    if row.status != "pending" or row.superseded_by_id is not None:
        raise HTTPException(status_code=409, detail=f"审批当前状态为 {row.status}，不能再次处理")
    require_revision(row, payload.expected_revision)
    run = cast(models.WorkflowRun, get_or_404(db, models.WorkflowRun, row.workflow_run_id))
    if run.cancel_requested or run.status in {"cancelled", "interrupted"}:
        _resolve(row, "cancelled", "cancel", "运行已取消", None, None)
        raise HTTPException(status_code=409, detail="运行已取消，审批不可继续")

    replacement: models.ApprovalRequest | None = None
    if payload.action == "edit":
        if row.approval_type == "change_set":
            raise HTTPException(status_code=422, detail="ChangeSet 请使用逐项编辑接口")
        replacement = supersede_with_value(
            db,
            row,
            payload.edited_value,
            note=payload.note,
            idempotency_key=payload.idempotency_key,
            decision_hash=decision_hash,
        )
    else:
        status = {
            "approve": "approved",
            "request_changes": "changes_requested",
            "reject": "rejected",
        }[payload.action]
        _resolve(
            row,
            status,
            payload.action,
            payload.note,
            decision_value,
            (payload.idempotency_key, decision_hash),
        )
    return ApprovalDecisionRead(
        approval=approval_read(row),
        replacement=approval_read(replacement) if replacement is not None else None,
    )


def supersede_with_value(
    db: Session,
    row: models.ApprovalRequest,
    value: Any,
    *,
    note: str,
    idempotency_key: str | None = None,
    decision_hash: str | None = None,
    round_number: int | None = None,
) -> models.ApprovalRequest:
    if row.status != "pending" or row.superseded_by_id is not None:
        raise HTTPException(status_code=409, detail="只有当前 pending 审批可以被替代")
    snapshot = approval_snapshot(row).model_copy(
        update={
            "value": value,
            "source": {
                **approval_snapshot(row).source,
                "supersedes_approval_id": row.id,
                "edit_note": note,
            },
        }
    )
    next_revision = _next_snapshot_revision(db, row.workflow_run_id, row.node_key)
    replacement = create_approval(
        db,
        ApprovalCreate(
            project_id=row.project_id,
            workflow_run_id=row.workflow_run_id,
            node_run_id=row.node_run_id,
            node_key=row.node_key,
            approval_type=cast(Any, row.approval_type),
            title=row.title,
            instructions=row.instructions,
            snapshot=snapshot,
            snapshot_revision=next_revision,
            round_number=round_number or row.round_number,
            parent_approval_id=row.id,
            expires_at=row.expires_at,
        ),
    )
    row.status = "superseded"
    row.superseded_by_id = replacement.id
    row.decision_action = "edit"
    row.decision_note = note
    row.decision_payload_json = _dump({"replacement_id": replacement.id})
    row.decision_idempotency_key = idempotency_key
    row.decision_hash = decision_hash
    row.resolved_at = models.utcnow()
    row.revision += 1
    db.flush()
    return replacement


def create_revision_approval(
    db: Session,
    previous: models.ApprovalRequest,
    value: Any,
    *,
    note: str,
) -> models.ApprovalRequest:
    if previous.status != "changes_requested":
        raise HTTPException(status_code=409, detail="只有 changes_requested 审批可创建修订")
    if previous.round_number >= 3:
        raise HTTPException(status_code=409, detail="审批修订已达到最多 3 轮")
    snapshot = approval_snapshot(previous).model_copy(
        update={
            "value": value,
            "source": {
                **approval_snapshot(previous).source,
                "revision_of_approval_id": previous.id,
                "requested_changes": note,
            },
        }
    )
    replacement = create_approval(
        db,
        ApprovalCreate(
            project_id=previous.project_id,
            workflow_run_id=previous.workflow_run_id,
            node_run_id=previous.node_run_id,
            node_key=previous.node_key,
            approval_type=cast(Any, previous.approval_type),
            title=previous.title,
            instructions=previous.instructions,
            snapshot=snapshot,
            snapshot_revision=_next_snapshot_revision(
                db, previous.workflow_run_id, previous.node_key
            ),
            round_number=previous.round_number + 1,
            parent_approval_id=previous.id,
            expires_at=previous.expires_at,
        ),
    )
    previous.superseded_by_id = replacement.id
    previous.revision += 1
    db.flush()
    return replacement


def cancel_pending_for_run(db: Session, run_id: int) -> list[int]:
    rows = db.scalars(
        select(models.ApprovalRequest).where(
            models.ApprovalRequest.workflow_run_id == run_id,
            models.ApprovalRequest.status == "pending",
            models.ApprovalRequest.deleted_at.is_(None),
        )
    ).all()
    for row in rows:
        _resolve(row, "cancelled", "cancel", "运行取消", None, None)
    return [row.id for row in rows]


def expire_pending(db: Session, *, project_id: int | None = None) -> list[int]:
    statement = select(models.ApprovalRequest).where(
        models.ApprovalRequest.status == "pending",
        models.ApprovalRequest.expires_at.is_not(None),
        models.ApprovalRequest.deleted_at.is_(None),
    )
    if project_id is not None:
        statement = statement.where(models.ApprovalRequest.project_id == project_id)
    rows = db.scalars(statement).all()
    expired: list[int] = []
    for row in rows:
        if _expire_row(row):
            expired.append(row.id)
    return expired


def approval_snapshot(row: models.ApprovalRequest) -> ApprovalSnapshot:
    return ApprovalSnapshot.model_validate_json(row.snapshot_json)


def approval_read(row: models.ApprovalRequest) -> ApprovalRequestRead:
    return ApprovalRequestRead(
        id=row.id,
        project_id=row.project_id,
        workflow_run_id=row.workflow_run_id,
        node_run_id=row.node_run_id,
        node_key=row.node_key,
        approval_type=cast(Any, row.approval_type),
        status=cast(Any, row.status),
        title=row.title,
        instructions=row.instructions,
        snapshot=approval_snapshot(row),
        snapshot_hash=row.snapshot_hash,
        snapshot_revision=row.snapshot_revision,
        round_number=row.round_number,
        parent_approval_id=row.parent_approval_id,
        superseded_by_id=row.superseded_by_id,
        decision_action=cast(Any, row.decision_action),
        decision_note=row.decision_note,
        decision_payload=_json_value(row.decision_payload_json),
        expires_at=row.expires_at,
        resolved_at=row.resolved_at,
        revision=row.revision,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _expire_row(row: models.ApprovalRequest) -> bool:
    if row.status != "pending" or row.expires_at is None:
        return False
    if _utc(row.expires_at) > datetime.now(timezone.utc):
        return False
    _resolve(row, "expired", "expire", "审批已过期", None, None)
    return True


def _resolve(
    row: models.ApprovalRequest,
    status: str,
    action: str,
    note: str,
    payload: Any,
    idempotency: tuple[str, str] | None,
) -> None:
    row.status = status
    row.decision_action = action
    row.decision_note = note
    row.decision_payload_json = _dump(payload)
    if idempotency is not None:
        row.decision_idempotency_key, row.decision_hash = idempotency
    row.resolved_at = models.utcnow()
    row.revision += 1


def _next_snapshot_revision(db: Session, run_id: int, node_key: str) -> int:
    rows = db.scalars(
        select(models.ApprovalRequest.snapshot_revision).where(
            models.ApprovalRequest.workflow_run_id == run_id,
            models.ApprovalRequest.node_key == node_key,
        )
    ).all()
    return max(rows, default=0) + 1


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _hash_json(value: Any) -> str:
    return hashlib.sha256(_dump(value).encode("utf-8")).hexdigest()


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_value(value: str) -> Any:
    return json.loads(value)
