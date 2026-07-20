from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any, cast

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.repositories import get_or_404, require_revision
from app.schemas.approvals import (
    ApprovalCreate,
    ApprovalSnapshot,
    ProposedChangeItem,
    ProposedChangeSetCreate,
    ProposedChangeSetEdit,
    ProposedChangeSetEditRead,
    ProposedChangeSetRead,
    ProposedChangeSetRebase,
    StateExtractionResult,
    WritebackAuditRead,
)
from app.services import approvals
from app.services.change_set_builder import ChangeSetBuilder


TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled", "interrupted"}
IMMUTABLE_CHANGE_SET_STATUSES = {"applied", "cancelled", "superseded"}

ALLOWED_PROPOSED_FIELDS: dict[str, set[str]] = {
    "chapter_content": {"content"},
    "chapter_summary": {
        "chapter_id",
        "summary",
        "key_events",
        "entity_ids",
        "token_count",
        "source",
    },
    "scene_synopsis": {"synopsis"},
    "scene_state": {
        "scene_id",
        "viewpoint_entity_id",
        "viewpoint_entity_ref",
        "location_entity_id",
        "location_entity_ref",
        "item_entity_ids",
        "item_entity_refs",
        "state",
        "notes",
    },
    "entity": {"name", "kind", "description", "tags"},
    "entity_alias": {"entity_id", "entity_ref", "alias"},
    "entity_relation": {
        "project_id",
        "source_entity_id",
        "source_entity_ref",
        "target_entity_id",
        "target_entity_ref",
        "relation_type",
        "notes",
    },
    "entity_state_change": {
        "entity_id",
        "entity_ref",
        "chapter_id",
        "field_name",
        "old_value",
        "new_value",
        "reason",
    },
    "timeline_event": {
        "project_id",
        "chapter_id",
        "label",
        "event_time",
        "description",
        "position",
    },
    "foreshadow": {
        "project_id",
        "setup_text",
        "payoff_text",
        "status",
        "chapter_id",
    },
}

TARGET_MODELS: dict[str, tuple[str, type[Any]]] = {
    "chapter_content": ("chapter", models.Chapter),
    "chapter_summary": ("chapter_summary", models.ChapterSummary),
    "scene_synopsis": ("scene", models.Scene),
    "scene_state": ("scene_state", models.SceneState),
    "entity": ("entity", models.StoryEntity),
    "entity_relation": ("entity_relation", models.EntityRelation),
    "timeline_event": ("timeline_event", models.TimelineEvent),
    "foreshadow": ("foreshadow", models.Foreshadow),
}


def create_change_set(
    db: Session, payload: ProposedChangeSetCreate
) -> models.ProposedChangeSet:
    project = cast(models.Project, get_or_404(db, models.Project, payload.project_id))
    run = cast(models.WorkflowRun, get_or_404(db, models.WorkflowRun, payload.workflow_run_id))
    if run.project_id != project.id:
        raise HTTPException(status_code=422, detail="运行不属于当前项目")
    if run.status in TERMINAL_RUN_STATUSES or run.cancel_requested:
        raise HTTPException(status_code=409, detail="终态或已取消运行不能创建变更集")
    node_run = cast(models.NodeRun, get_or_404(db, models.NodeRun, payload.node_run_id))
    if node_run.workflow_run_id != run.id or node_run.node_key != payload.node_key:
        raise HTTPException(status_code=422, detail="变更集节点与运行节点不匹配")
    chapter = _chapter_in_project(db, payload.chapter_id, project.id)
    scene = _scene_in_project(db, payload.scene_id, project.id)
    if scene is not None and chapter is not None and scene.chapter_id != chapter.id:
        raise HTTPException(status_code=422, detail="目标场景不属于目标章节")
    approved_prose = _approved_prose(
        db,
        payload.source_approval_id,
        project_id=project.id,
        workflow_run_id=run.id,
        submitted_prose=payload.approved_prose,
    )
    build = ChangeSetBuilder(
        db,
        project_id=project.id,
        chapter=chapter,
        scene=scene,
    ).build(payload.extraction, approved_prose=approved_prose)
    for item in build.items:
        validate_proposed_fields(item)
    extraction_json = _dump(payload.extraction.model_dump(mode="json"))
    items_json = _dump([item.model_dump(mode="json") for item in build.items])
    base_json = _dump(build.base_revisions)
    changes_hash = calculate_changes_hash(
        extraction=payload.extraction.model_dump(mode="json"),
        base_revisions=build.base_revisions,
        items=build.items,
    )
    row = models.ProposedChangeSet(
        project_id=project.id,
        workflow_run_id=run.id,
        node_run_id=node_run.id,
        node_key=payload.node_key,
        source_approval_id=payload.source_approval_id,
        chapter_id=chapter.id if chapter is not None else None,
        scene_id=scene.id if scene is not None else None,
        status="pending",
        extraction_json=extraction_json,
        base_revisions_json=base_json,
        items_json=items_json,
        conflicts_json=_dump(build.conflicts),
        changes_hash=changes_hash,
    )
    db.add(row)
    db.flush()
    return row


def list_change_sets(
    db: Session,
    *,
    project_id: int | None = None,
    workflow_run_id: int | None = None,
) -> list[ProposedChangeSetRead]:
    statement = select(models.ProposedChangeSet).where(
        models.ProposedChangeSet.deleted_at.is_(None)
    )
    if project_id is not None:
        statement = statement.where(models.ProposedChangeSet.project_id == project_id)
    if workflow_run_id is not None:
        statement = statement.where(
            models.ProposedChangeSet.workflow_run_id == workflow_run_id
        )
    rows = db.scalars(
        statement.order_by(
            models.ProposedChangeSet.created_at.desc(),
            models.ProposedChangeSet.id.desc(),
        )
    ).all()
    return [change_set_read(db, row) for row in rows]


def read_change_set(db: Session, change_set_id: int) -> ProposedChangeSetRead:
    row = cast(
        models.ProposedChangeSet,
        get_or_404(db, models.ProposedChangeSet, change_set_id),
    )
    return change_set_read(db, row)


def edit_change_set(
    db: Session,
    change_set_id: int,
    payload: ProposedChangeSetEdit,
) -> ProposedChangeSetEditRead:
    row = cast(
        models.ProposedChangeSet,
        get_or_404(db, models.ProposedChangeSet, change_set_id),
    )
    _require_mutable(row)
    require_revision(row, payload.expected_revision)
    existing_items = change_set_items(row)
    existing_by_id = {item.id: item for item in existing_items}
    if len(payload.items) != len(existing_items) or {
        item.id for item in payload.items
    } != set(existing_by_id):
        raise HTTPException(status_code=422, detail="编辑必须完整保留原变更项 ID 集合")
    updated: list[ProposedChangeItem] = []
    for incoming in payload.items:
        current = existing_by_id[incoming.id]
        if (
            incoming.kind != current.kind
            or incoming.operation != current.operation
            or incoming.target_id != current.target_id
            or incoming.base_revision != current.base_revision
        ):
            raise HTTPException(status_code=422, detail=f"{incoming.id} 的目标或操作不可直接修改")
        candidate = current.model_copy(
            update={"proposed": incoming.proposed, "decision": incoming.decision}
        )
        validate_proposed_fields(candidate)
        updated.append(candidate)
    _store_items(row, updated)
    replacement = _refresh_pending_approval(db, row, note="变更集已逐项编辑，旧审批快照失效")
    db.flush()
    return ProposedChangeSetEditRead(
        change_set=change_set_read(db, row),
        replacement_approval=(
            approvals.approval_read(replacement) if replacement is not None else None
        ),
    )


def rebase_change_set(
    db: Session,
    change_set_id: int,
    payload: ProposedChangeSetRebase,
) -> ProposedChangeSetEditRead:
    row = cast(
        models.ProposedChangeSet,
        get_or_404(db, models.ProposedChangeSet, change_set_id),
    )
    _require_mutable(row)
    require_revision(row, payload.expected_revision)
    if payload.action in {"abandon", "reextract"}:
        row.status = "cancelled" if payload.action == "abandon" else "superseded"
        row.revision += 1
        _cancel_pending_approvals(
            db,
            row,
            "变更集已放弃" if payload.action == "abandon" else "变更集等待重新提取",
        )
        db.flush()
        return ProposedChangeSetEditRead(change_set=change_set_read(db, row))

    items = change_set_items(row)
    if payload.action == "manual_merge":
        assert payload.items is not None
        items = _manual_merge_items(db, row, items, payload.items)
    items = [_refresh_item_base(db, item) for item in items]
    _store_items(row, items)
    row.status = "pending"
    replacement = _refresh_pending_approval(
        db,
        row,
        note=(
            "变更集已按当前版本重基，旧审批快照失效"
            if payload.action == "rebase_current"
            else "变更集已手工合并，旧审批快照失效"
        ),
    )
    db.flush()
    return ProposedChangeSetEditRead(
        change_set=change_set_read(db, row),
        replacement_approval=(
            approvals.approval_read(replacement) if replacement is not None else None
        ),
    )


def create_change_set_approval(
    db: Session,
    change_set_id: int,
    *,
    node_run_id: int,
    node_key: str,
    title: str = "元数据变更审批",
    instructions: str = "逐项确认实体、关系、时间线、伏笔和摘要变更。",
    expires_at: datetime | None = None,
) -> models.ApprovalRequest:
    row = cast(
        models.ProposedChangeSet,
        get_or_404(db, models.ProposedChangeSet, change_set_id),
    )
    _require_mutable(row)
    node_run = cast(models.NodeRun, get_or_404(db, models.NodeRun, node_run_id))
    if node_run.workflow_run_id != row.workflow_run_id or node_run.node_key != node_key:
        raise HTTPException(status_code=422, detail="审批节点与变更集运行不匹配")
    existing = _pending_change_set_approval(db, row)
    if existing is not None:
        value = approvals.approval_snapshot(existing).value
        if isinstance(value, dict) and value.get("changes_hash") == row.changes_hash:
            return existing
        approvals.supersede_with_value(
            db,
            existing,
            change_set_snapshot_value(row),
            note="变更集内容已变化，替换旧审批快照",
        )
        replacement = db.get(models.ApprovalRequest, existing.superseded_by_id)
        if replacement is None:
            raise RuntimeError("Failed to create replacement approval")
        return replacement
    requested = _latest_change_set_approval(db, row, {"changes_requested"})
    if requested is not None:
        return approvals.create_revision_approval(
            db,
            requested,
            change_set_snapshot_value(row),
            note=requested.decision_note,
        )
    snapshot_revision = _next_approval_revision(db, row.workflow_run_id, node_key)
    return approvals.create_approval(
        db,
        ApprovalCreate(
            project_id=row.project_id,
            workflow_run_id=row.workflow_run_id,
            node_run_id=node_run.id,
            node_key=node_key,
            approval_type="change_set",
            title=title,
            instructions=instructions,
            snapshot=ApprovalSnapshot(
                approval_type="change_set",
                value=change_set_snapshot_value(row),
                source={"change_set_id": row.id, "changes_hash": row.changes_hash},
            ),
            snapshot_revision=snapshot_revision,
            expires_at=expires_at,
        ),
    )


def change_set_snapshot_value(row: models.ProposedChangeSet) -> dict[str, Any]:
    return {
        "change_set_id": row.id,
        "changes_hash": row.changes_hash,
        "change_set_revision": row.revision,
        "items": [item.model_dump(mode="json") for item in change_set_items(row)],
    }


def change_set_read(
    db: Session, row: models.ProposedChangeSet
) -> ProposedChangeSetRead:
    return ProposedChangeSetRead(
        id=row.id,
        project_id=row.project_id,
        workflow_run_id=row.workflow_run_id,
        node_run_id=row.node_run_id,
        node_key=row.node_key,
        source_approval_id=row.source_approval_id,
        chapter_id=row.chapter_id,
        scene_id=row.scene_id,
        status=cast(Any, row.status),
        extraction=StateExtractionResult.model_validate_json(row.extraction_json),
        base_revisions=_json_int_object(row.base_revisions_json),
        items=change_set_items(row),
        conflicts=_json_string_list(row.conflicts_json),
        live_conflicts=live_change_set_conflicts(db, row),
        changes_hash=row.changes_hash,
        superseded_by_id=row.superseded_by_id,
        applied_at=row.applied_at,
        revision=row.revision,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def change_set_items(row: models.ProposedChangeSet) -> list[ProposedChangeItem]:
    value = _json_list(row.items_json)
    return [ProposedChangeItem.model_validate(item) for item in value]


def live_change_set_conflicts(
    db: Session, row: models.ProposedChangeSet
) -> list[str]:
    if row.status == "applied":
        return []
    conflicts: list[str] = []
    items = change_set_items(row)
    accepted_ids = {item.id for item in items if item.decision == "accept"}
    for item in items:
        if item.decision != "accept":
            continue
        conflicts.extend(f"{item.id}: {message}" for message in item.conflicts)
        if item.target_id is not None and item.base_revision is not None:
            model_entry = TARGET_MODELS.get(item.kind)
            if model_entry is None:
                conflicts.append(f"{item.id}: 变更类型没有受控目标模型")
                continue
            _, model = model_entry
            target = db.get(model, item.target_id)
            if target is None or getattr(target, "deleted_at", None) is not None:
                conflicts.append(f"{item.id}: 目标记录已删除")
            elif int(target.revision) != item.base_revision:
                conflicts.append(
                    f"{item.id}: 基线 revision={item.base_revision}，"
                    f"当前 revision={target.revision}"
                )
        conflicts.extend(_reference_conflicts(item, accepted_ids))
    return list(dict.fromkeys(conflicts))


def calculate_changes_hash(
    *,
    extraction: dict[str, Any],
    base_revisions: dict[str, int],
    items: list[ProposedChangeItem],
) -> str:
    value = {
        "extraction": extraction,
        "base_revisions": base_revisions,
        "items": [item.model_dump(mode="json") for item in items],
    }
    return hashlib.sha256(_dump(value).encode("utf-8")).hexdigest()


def validate_proposed_fields(item: ProposedChangeItem) -> None:
    allowed = ALLOWED_PROPOSED_FIELDS.get(item.kind)
    if allowed is None:
        raise HTTPException(status_code=422, detail=f"不支持的变更类型：{item.kind}")
    extras = set(item.proposed) - allowed
    if extras:
        raise HTTPException(
            status_code=422,
            detail=f"{item.id} 包含非白名单字段：{', '.join(sorted(extras))}",
        )
    serialized = _dump(item.proposed)
    if len(serialized.encode("utf-8")) > 2_500_000:
        raise HTTPException(status_code=422, detail=f"{item.id} 的变更内容过大")
    _validate_json_shape(item.id, item.proposed)


def audit_read(row: models.WritebackAudit) -> WritebackAuditRead:
    return WritebackAuditRead(
        id=row.id,
        project_id=row.project_id,
        workflow_run_id=row.workflow_run_id,
        change_set_id=row.change_set_id,
        approval_request_id=row.approval_request_id,
        change_set_hash=row.change_set_hash,
        entries=cast(list[dict[str, Any]], _json_list(row.entries_json)),
        created_at=row.created_at,
    )


def list_audits(
    db: Session,
    *,
    project_id: int | None = None,
    workflow_run_id: int | None = None,
    change_set_id: int | None = None,
) -> list[WritebackAuditRead]:
    statement = select(models.WritebackAudit)
    if project_id is not None:
        statement = statement.where(models.WritebackAudit.project_id == project_id)
    if workflow_run_id is not None:
        statement = statement.where(
            models.WritebackAudit.workflow_run_id == workflow_run_id
        )
    if change_set_id is not None:
        statement = statement.where(models.WritebackAudit.change_set_id == change_set_id)
    rows = db.scalars(
        statement.order_by(
            models.WritebackAudit.created_at.desc(), models.WritebackAudit.id.desc()
        )
    ).all()
    return [audit_read(row) for row in rows]


def read_audit(db: Session, audit_id: int) -> WritebackAuditRead:
    row = cast(models.WritebackAudit, get_or_404(db, models.WritebackAudit, audit_id))
    return audit_read(row)


def _approved_prose(
    db: Session,
    approval_id: int | None,
    *,
    project_id: int,
    workflow_run_id: int,
    submitted_prose: str | None,
) -> str | None:
    if submitted_prose is None:
        return None
    if approval_id is None:
        raise HTTPException(status_code=422, detail="正文变更必须引用已批准的正文审批")
    approval = cast(
        models.ApprovalRequest,
        get_or_404(db, models.ApprovalRequest, approval_id),
    )
    if (
        approval.project_id != project_id
        or approval.workflow_run_id != workflow_run_id
        or approval.approval_type != "prose"
        or approval.status != "approved"
        or approval.superseded_by_id is not None
    ):
        raise HTTPException(status_code=409, detail="正文审批无效、未批准或已被替代")
    snapshot_value = approvals.approval_snapshot(approval).value
    if not isinstance(snapshot_value, str):
        raise HTTPException(status_code=422, detail="正文审批快照不是文本")
    if submitted_prose != snapshot_value:
        raise HTTPException(status_code=409, detail="提交正文与已批准快照不一致")
    return snapshot_value


def _chapter_in_project(
    db: Session, chapter_id: int | None, project_id: int
) -> models.Chapter | None:
    if chapter_id is None:
        return None
    chapter = cast(models.Chapter, get_or_404(db, models.Chapter, chapter_id))
    volume = cast(models.Volume, get_or_404(db, models.Volume, chapter.volume_id))
    if volume.project_id != project_id:
        raise HTTPException(status_code=422, detail="章节不属于当前项目")
    return chapter


def _scene_in_project(
    db: Session, scene_id: int | None, project_id: int
) -> models.Scene | None:
    if scene_id is None:
        return None
    scene = cast(models.Scene, get_or_404(db, models.Scene, scene_id))
    chapter = cast(models.Chapter, get_or_404(db, models.Chapter, scene.chapter_id))
    _chapter_in_project(db, chapter.id, project_id)
    return scene


def _store_items(
    row: models.ProposedChangeSet, items: list[ProposedChangeItem]
) -> None:
    row.items_json = _dump([item.model_dump(mode="json") for item in items])
    extraction = _json_object(row.extraction_json)
    base_revisions = _base_revisions_from_items(items)
    row.base_revisions_json = _dump(base_revisions)
    row.changes_hash = calculate_changes_hash(
        extraction=extraction,
        base_revisions=base_revisions,
        items=items,
    )
    row.status = "pending"
    row.revision += 1


def _base_revisions_from_items(items: list[ProposedChangeItem]) -> dict[str, int]:
    result: dict[str, int] = {}
    for item in items:
        if item.target_id is None or item.base_revision is None:
            continue
        model_entry = TARGET_MODELS.get(item.kind)
        if model_entry is not None:
            prefix, _ = model_entry
            result[f"{prefix}:{item.target_id}"] = item.base_revision
    return result


def _refresh_pending_approval(
    db: Session,
    row: models.ProposedChangeSet,
    *,
    note: str,
) -> models.ApprovalRequest | None:
    current = _latest_change_set_approval(
        db, row, {"pending", "changes_requested"}
    )
    if current is None:
        return None
    if current.status == "changes_requested":
        return approvals.create_revision_approval(
            db,
            current,
            change_set_snapshot_value(row),
            note=note,
        )
    return approvals.supersede_with_value(
        db, current, change_set_snapshot_value(row), note=note
    )


def _pending_change_set_approval(
    db: Session, row: models.ProposedChangeSet
) -> models.ApprovalRequest | None:
    return _latest_change_set_approval(db, row, {"pending"})


def _latest_change_set_approval(
    db: Session,
    row: models.ProposedChangeSet,
    statuses: set[str],
) -> models.ApprovalRequest | None:
    candidates = db.scalars(
        select(models.ApprovalRequest).where(
            models.ApprovalRequest.workflow_run_id == row.workflow_run_id,
            models.ApprovalRequest.approval_type == "change_set",
            models.ApprovalRequest.status.in_(statuses),
            models.ApprovalRequest.deleted_at.is_(None),
        ).order_by(models.ApprovalRequest.id.desc())
    ).all()
    for candidate in candidates:
        snapshot = approvals.approval_snapshot(candidate)
        value = snapshot.value
        source_id = snapshot.source.get("change_set_id")
        if source_id == row.id or (
            isinstance(value, dict) and value.get("change_set_id") == row.id
        ):
            return candidate
    return None


def _cancel_pending_approvals(
    db: Session, row: models.ProposedChangeSet, note: str
) -> None:
    pending = _pending_change_set_approval(db, row)
    if pending is None:
        return
    pending.status = "cancelled"
    pending.decision_action = "cancel"
    pending.decision_note = note
    pending.decision_payload_json = "null"
    pending.resolved_at = models.utcnow()
    pending.revision += 1


def _next_approval_revision(db: Session, run_id: int, node_key: str) -> int:
    revisions = db.scalars(
        select(models.ApprovalRequest.snapshot_revision).where(
            models.ApprovalRequest.workflow_run_id == run_id,
            models.ApprovalRequest.node_key == node_key,
        )
    ).all()
    return max(revisions, default=0) + 1


def _manual_merge_items(
    db: Session,
    row: models.ProposedChangeSet,
    current: list[ProposedChangeItem],
    incoming: list[ProposedChangeItem],
) -> list[ProposedChangeItem]:
    current_by_id = {item.id: item for item in current}
    if len(incoming) != len(current) or {item.id for item in incoming} != set(current_by_id):
        raise HTTPException(status_code=422, detail="手工合并必须完整保留变更项 ID 集合")
    merged: list[ProposedChangeItem] = []
    for item in incoming:
        original = current_by_id[item.id]
        if item.kind != original.kind:
            raise HTTPException(status_code=422, detail=f"{item.id} 的变更类型不可修改")
        validate_proposed_fields(item)
        _validate_manual_target(db, row, item)
        merged.append(
            item.model_copy(
                update={
                    "evidence": original.evidence,
                    "confidence": original.confidence,
                    "resolution": {
                        **original.resolution,
                        "status": "manual",
                        "manually_merged": True,
                    },
                    "conflicts": [],
                }
            )
        )
    return merged


def _validate_manual_target(
    db: Session, row: models.ProposedChangeSet, item: ProposedChangeItem
) -> None:
    if item.target_id is None:
        if item.operation == "update":
            raise HTTPException(status_code=422, detail=f"{item.id} 的 update 缺少目标")
        return
    model_entry = TARGET_MODELS.get(item.kind)
    if model_entry is None:
        raise HTTPException(status_code=422, detail=f"{item.id} 不允许指定目标")
    _, model = model_entry
    target = db.get(model, item.target_id)
    if target is None or getattr(target, "deleted_at", None) is not None:
        raise HTTPException(status_code=422, detail=f"{item.id} 的手工目标不存在")
    if not _target_in_project(db, target, row.project_id):
        raise HTTPException(status_code=422, detail=f"{item.id} 的手工目标不属于当前项目")


def _refresh_item_base(db: Session, item: ProposedChangeItem) -> ProposedChangeItem:
    if item.target_id is None:
        return item.model_copy(update={"base_revision": None, "before": {}})
    model_entry = TARGET_MODELS.get(item.kind)
    if model_entry is None:
        return item
    _, model = model_entry
    target = db.get(model, item.target_id)
    if target is None or getattr(target, "deleted_at", None) is not None:
        raise HTTPException(status_code=409, detail=f"{item.id} 的目标已删除，不能重基")
    return item.model_copy(
        update={
            "base_revision": int(target.revision),
            "before": _target_snapshot(item.kind, target),
        }
    )


def _target_snapshot(kind: str, target: Any) -> dict[str, Any]:
    if kind == "chapter_content":
        return {"content": target.content, "word_count": target.word_count}
    if kind == "chapter_summary":
        return {
            "summary": target.summary,
            "key_events": _json_list(target.key_events_json),
            "entity_ids": _json_list(target.entity_ids_json),
            "token_count": target.token_count,
            "source": target.source,
        }
    if kind == "scene_synopsis":
        return {"synopsis": target.synopsis}
    if kind == "scene_state":
        return {
            "scene_id": target.scene_id,
            "viewpoint_entity_id": target.viewpoint_entity_id,
            "location_entity_id": target.location_entity_id,
            "item_entity_ids": _json_list(target.item_entity_ids_json),
            "state": _json_object(target.state_json),
            "notes": target.notes,
        }
    if kind == "entity":
        return {
            "name": target.name,
            "kind": target.kind,
            "description": target.description,
            "tags": _json_list(target.tags),
        }
    if kind == "entity_relation":
        return {
            "source_entity_id": target.source_entity_id,
            "target_entity_id": target.target_entity_id,
            "relation_type": target.relation_type,
            "notes": target.notes,
        }
    if kind == "timeline_event":
        return {
            "label": target.label,
            "event_time": target.event_time,
            "description": target.description,
            "chapter_id": target.chapter_id,
            "position": target.position,
        }
    if kind == "foreshadow":
        return {
            "setup_text": target.setup_text,
            "payoff_text": target.payoff_text,
            "status": target.status,
            "chapter_id": target.chapter_id,
        }
    return {}


def _target_in_project(db: Session, target: Any, project_id: int) -> bool:
    if hasattr(target, "project_id"):
        return int(target.project_id) == project_id
    if isinstance(target, models.Chapter):
        volume = db.get(models.Volume, target.volume_id)
        return volume is not None and volume.project_id == project_id
    if isinstance(target, models.Scene):
        chapter = db.get(models.Chapter, target.chapter_id)
        return chapter is not None and _target_in_project(db, chapter, project_id)
    if isinstance(target, models.ChapterSummary):
        chapter = db.get(models.Chapter, target.chapter_id)
        return chapter is not None and _target_in_project(db, chapter, project_id)
    if isinstance(target, models.SceneState):
        scene = db.get(models.Scene, target.scene_id)
        return scene is not None and _target_in_project(db, scene, project_id)
    return False


def _reference_conflicts(
    item: ProposedChangeItem, accepted_ids: set[str]
) -> list[str]:
    conflicts: list[str] = []
    reference_keys = {
        "entity_ref",
        "source_entity_ref",
        "target_entity_ref",
        "viewpoint_entity_ref",
        "location_entity_ref",
    }
    for key in reference_keys:
        value = item.proposed.get(key)
        if value is not None and value not in accepted_ids:
            conflicts.append(f"{item.id}: 引用的变更项 {value} 未被接受")
    refs = item.proposed.get("item_entity_refs", [])
    if isinstance(refs, list):
        for value in refs:
            if value not in accepted_ids:
                conflicts.append(f"{item.id}: 引用的变更项 {value} 未被接受")
    return conflicts


def _validate_json_shape(item_id: str, value: Any, *, depth: int = 0) -> None:
    if depth > 8:
        raise HTTPException(status_code=422, detail=f"{item_id} 的嵌套层级过深")
    if value is None or isinstance(value, bool | int | float | str):
        return
    if isinstance(value, list):
        if len(value) > 5_000:
            raise HTTPException(status_code=422, detail=f"{item_id} 的列表过长")
        for child in value:
            _validate_json_shape(item_id, child, depth=depth + 1)
        return
    if isinstance(value, dict):
        if len(value) > 1_000:
            raise HTTPException(status_code=422, detail=f"{item_id} 的对象字段过多")
        for key, child in value.items():
            if not isinstance(key, str) or len(key) > 200:
                raise HTTPException(status_code=422, detail=f"{item_id} 含非法对象键")
            _validate_json_shape(item_id, child, depth=depth + 1)
        return
    raise HTTPException(status_code=422, detail=f"{item_id} 包含非 JSON 值")


def _require_mutable(row: models.ProposedChangeSet) -> None:
    if row.status in IMMUTABLE_CHANGE_SET_STATUSES:
        raise HTTPException(status_code=409, detail=f"变更集状态 {row.status} 不可修改")


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_list(value: str) -> list[Any]:
    parsed = json.loads(value)
    return parsed if isinstance(parsed, list) else []


def _json_object(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    return parsed if isinstance(parsed, dict) else {}


def _json_int_object(value: str) -> dict[str, int]:
    return {
        str(key): int(item)
        for key, item in _json_object(value).items()
        if isinstance(item, int)
    }


def _json_string_list(value: str) -> list[str]:
    return [str(item) for item in _json_list(value)]
