from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import (
    ApprovalDecisionRead,
    ApprovalDecisionRequest,
    ApprovalRequestRead,
    ProposedChangeSetCreate,
    ProposedChangeSetEdit,
    ProposedChangeSetEditRead,
    ProposedChangeSetRead,
    ProposedChangeSetRebase,
    WritebackAuditRead,
    WritebackRequest,
    WritebackResultRead,
)
from app.services import approvals, change_sets, writeback
from app.services.approval_runtime import approval_signals


router = APIRouter(prefix="/approvals", tags=["approvals"])


@router.get("/requests", response_model=list[ApprovalRequestRead])
def list_approval_requests(
    project_id: int | None = Query(default=None, ge=1),
    workflow_run_id: int | None = Query(default=None, ge=1),
    approval_status: str | None = Query(default=None, alias="status", max_length=32),
    db: Session = Depends(get_db),
) -> list[ApprovalRequestRead]:
    with db.begin():
        return approvals.list_approvals(
            db,
            project_id=project_id,
            workflow_run_id=workflow_run_id,
            status=approval_status,
        )


@router.get("/requests/{approval_id}", response_model=ApprovalRequestRead)
def get_approval_request(
    approval_id: int, db: Session = Depends(get_db)
) -> ApprovalRequestRead:
    with db.begin():
        return approvals.read_approval(db, approval_id)


@router.post(
    "/requests/{approval_id}/decision", response_model=ApprovalDecisionRead
)
def decide_approval_request(
    approval_id: int,
    payload: ApprovalDecisionRequest,
    db: Session = Depends(get_db),
) -> ApprovalDecisionRead:
    with db.begin():
        result = approvals.decide_approval(db, approval_id, payload)
    approval_signals.notify(result.approval.id)
    if result.replacement is not None:
        approval_signals.notify(result.replacement.id)
    return result


@router.get("/change-sets", response_model=list[ProposedChangeSetRead])
def list_proposed_change_sets(
    project_id: int | None = Query(default=None, ge=1),
    workflow_run_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
) -> list[ProposedChangeSetRead]:
    return change_sets.list_change_sets(
        db,
        project_id=project_id,
        workflow_run_id=workflow_run_id,
    )


@router.post(
    "/change-sets",
    response_model=ProposedChangeSetRead,
    status_code=status.HTTP_201_CREATED,
)
def create_proposed_change_set(
    payload: ProposedChangeSetCreate,
    db: Session = Depends(get_db),
) -> ProposedChangeSetRead:
    with db.begin():
        row = change_sets.create_change_set(db, payload)
        return change_sets.change_set_read(db, row)


@router.get("/change-sets/{change_set_id}", response_model=ProposedChangeSetRead)
def get_proposed_change_set(
    change_set_id: int, db: Session = Depends(get_db)
) -> ProposedChangeSetRead:
    return change_sets.read_change_set(db, change_set_id)


@router.put(
    "/change-sets/{change_set_id}/items",
    response_model=ProposedChangeSetEditRead,
)
def edit_proposed_change_set(
    change_set_id: int,
    payload: ProposedChangeSetEdit,
    db: Session = Depends(get_db),
) -> ProposedChangeSetEditRead:
    with db.begin():
        result = change_sets.edit_change_set(db, change_set_id, payload)
    if result.replacement_approval is not None:
        approval_signals.notify(result.replacement_approval.id)
        if result.replacement_approval.parent_approval_id is not None:
            approval_signals.notify(result.replacement_approval.parent_approval_id)
    return result


@router.post(
    "/change-sets/{change_set_id}/resolve-conflict",
    response_model=ProposedChangeSetEditRead,
)
def resolve_change_set_conflict(
    change_set_id: int,
    payload: ProposedChangeSetRebase,
    db: Session = Depends(get_db),
) -> ProposedChangeSetEditRead:
    with db.begin():
        result = change_sets.rebase_change_set(db, change_set_id, payload)
    if result.replacement_approval is not None:
        approval_signals.notify(result.replacement_approval.id)
        if result.replacement_approval.parent_approval_id is not None:
            approval_signals.notify(result.replacement_approval.parent_approval_id)
    return result


@router.post(
    "/change-sets/{change_set_id}/writeback",
    response_model=WritebackResultRead,
)
def writeback_change_set(
    change_set_id: int,
    payload: WritebackRequest,
    db: Session = Depends(get_db),
) -> WritebackResultRead:
    with db.begin():
        return writeback.apply_change_set(db, change_set_id, payload)


@router.get("/audits", response_model=list[WritebackAuditRead])
def list_writeback_audits(
    project_id: int | None = Query(default=None, ge=1),
    workflow_run_id: int | None = Query(default=None, ge=1),
    change_set_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
) -> list[WritebackAuditRead]:
    return change_sets.list_audits(
        db,
        project_id=project_id,
        workflow_run_id=workflow_run_id,
        change_set_id=change_set_id,
    )


@router.get("/audits/{audit_id}", response_model=WritebackAuditRead)
def get_writeback_audit(
    audit_id: int, db: Session = Depends(get_db)
) -> WritebackAuditRead:
    return change_sets.read_audit(db, audit_id)
