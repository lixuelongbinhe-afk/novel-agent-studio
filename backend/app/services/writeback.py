from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, TypeGuard, cast

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.repositories import get_or_404, require_revision, word_count
from app.schemas.approvals import (
    ProposedChangeItem,
    WritebackRequest,
    WritebackResultRead,
)
from app.services import approvals, change_sets
from app.services.context_retrieval import rebuild_fts_index


TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled", "interrupted"}
APPLY_PRIORITY = {
    "entity": 10,
    "chapter_content": 20,
    "chapter_summary": 30,
    "entity_alias": 40,
    "entity_state_change": 40,
    "scene_synopsis": 50,
    "scene_state": 50,
    "entity_relation": 60,
    "timeline_event": 70,
    "foreshadow": 80,
}


def apply_change_set(
    db: Session,
    change_set_id: int,
    payload: WritebackRequest,
) -> WritebackResultRead:
    row = cast(
        models.ProposedChangeSet,
        get_or_404(db, models.ProposedChangeSet, change_set_id),
    )
    existing_audit = db.scalar(
        select(models.WritebackAudit).where(
            models.WritebackAudit.change_set_id == row.id
        )
    )
    if row.status == "applied":
        if (
            existing_audit is not None
            and existing_audit.approval_request_id == payload.approval_request_id
            and existing_audit.change_set_hash == row.changes_hash
        ):
            return WritebackResultRead(
                status="applied",
                change_set=change_sets.change_set_read(db, row),
                audit=change_sets.audit_read(existing_audit),
                applied_item_ids=[
                    str(entry.get("item_id", ""))
                    for entry in _json_entries(existing_audit.entries_json)
                ],
            )
        raise HTTPException(status_code=409, detail="变更集已经由另一审批写回")
    if row.status in {"cancelled", "superseded"}:
        raise HTTPException(status_code=409, detail=f"变更集状态 {row.status} 不可写回")
    require_revision(row, payload.expected_change_set_revision)
    run = cast(models.WorkflowRun, get_or_404(db, models.WorkflowRun, row.workflow_run_id))
    if run.status in TERMINAL_RUN_STATUSES or run.cancel_requested:
        raise HTTPException(status_code=409, detail="运行已取消或结束，拒绝写回")
    approval = _validated_approval(db, row, payload.approval_request_id)
    items = change_sets.change_set_items(row)
    accepted = [item for item in items if item.decision == "accept"]
    for item in accepted:
        change_sets.validate_proposed_fields(item)
        _validate_item_values(db, row, item)
    conflicts = change_sets.live_change_set_conflicts(db, row)
    if conflicts:
        row.status = "conflicted"
        row.revision += 1
        db.flush()
        return WritebackResultRead(
            status="conflicted",
            change_set=change_sets.change_set_read(db, row),
            conflicts=conflicts,
        )

    _version_chapter_if_needed(db, row, accepted)
    created_entity_refs: dict[str, int] = {}
    entries: list[dict[str, Any]] = []
    sorted_items = sorted(
        accepted,
        key=lambda item: (APPLY_PRIORITY.get(item.kind, 999), item.id),
    )
    for item in sorted_items:
        result_id = _apply_item(db, row, item, created_entity_refs)
        entries.append(
            {
                "item_id": item.id,
                "kind": item.kind,
                "operation": item.operation,
                "target_id": result_id,
                "before": item.before,
                "applied": item.proposed,
                "evidence": item.evidence,
                "confidence": item.confidence,
            }
        )
    rebuild_fts_index(db, row.project_id)
    row.status = "applied"
    row.applied_at = models.utcnow()
    row.revision += 1
    audit = models.WritebackAudit(
        project_id=row.project_id,
        workflow_run_id=row.workflow_run_id,
        change_set_id=row.id,
        approval_request_id=approval.id,
        change_set_hash=row.changes_hash,
        entries_json=_dump(entries),
    )
    db.add(audit)
    db.flush()
    return WritebackResultRead(
        status="applied",
        change_set=change_sets.change_set_read(db, row),
        audit=change_sets.audit_read(audit),
        applied_item_ids=[item.id for item in sorted_items],
    )


def _validated_approval(
    db: Session,
    row: models.ProposedChangeSet,
    approval_id: int,
) -> models.ApprovalRequest:
    approval = cast(
        models.ApprovalRequest,
        get_or_404(db, models.ApprovalRequest, approval_id),
    )
    approvals.read_approval(db, approval.id)
    if (
        approval.project_id != row.project_id
        or approval.workflow_run_id != row.workflow_run_id
        or approval.approval_type != "change_set"
        or approval.status != "approved"
        or approval.superseded_by_id is not None
    ):
        raise HTTPException(status_code=409, detail="元数据审批无效、未批准或已被替代")
    value = approvals.approval_snapshot(approval).value
    if not isinstance(value, dict):
        raise HTTPException(status_code=422, detail="元数据审批快照格式无效")
    if (
        value.get("change_set_id") != row.id
        or value.get("changes_hash") != row.changes_hash
        or value.get("change_set_revision") != row.revision
    ):
        raise HTTPException(status_code=409, detail="审批快照与当前变更集不一致")
    return approval


def _version_chapter_if_needed(
    db: Session,
    row: models.ProposedChangeSet,
    accepted: list[ProposedChangeItem],
) -> None:
    if not any(item.kind == "chapter_content" for item in accepted):
        return
    if row.chapter_id is None:
        raise HTTPException(status_code=422, detail="正文写回缺少章节")
    chapter = cast(models.Chapter, get_or_404(db, models.Chapter, row.chapter_id))
    db.add(
        models.ChapterVersion(
            chapter_id=chapter.id,
            title=chapter.title,
            content=chapter.content,
            word_count=chapter.word_count,
            source=f"workflow_writeback:{row.workflow_run_id}",
        )
    )
    db.flush()


def _apply_item(
    db: Session,
    change_set: models.ProposedChangeSet,
    item: ProposedChangeItem,
    created_entity_refs: dict[str, int],
) -> int:
    handlers: dict[str, Callable[[], int]] = {
        "chapter_content": lambda: _apply_chapter_content(db, change_set, item),
        "chapter_summary": lambda: _apply_chapter_summary(db, change_set, item),
        "scene_synopsis": lambda: _apply_scene_synopsis(db, item),
        "scene_state": lambda: _apply_scene_state(
            db, change_set, item, created_entity_refs
        ),
        "entity": lambda: _apply_entity(db, change_set, item, created_entity_refs),
        "entity_alias": lambda: _apply_entity_alias(
            db, change_set, item, created_entity_refs
        ),
        "entity_relation": lambda: _apply_entity_relation(
            db, change_set, item, created_entity_refs
        ),
        "entity_state_change": lambda: _apply_entity_state_change(
            db, change_set, item, created_entity_refs
        ),
        "timeline_event": lambda: _apply_timeline(db, change_set, item),
        "foreshadow": lambda: _apply_foreshadow(db, change_set, item),
    }
    handler = handlers.get(item.kind)
    if handler is None:
        raise HTTPException(status_code=422, detail=f"不支持写回 {item.kind}")
    return handler()


def _apply_chapter_content(
    db: Session, change_set: models.ProposedChangeSet, item: ProposedChangeItem
) -> int:
    chapter = cast(models.Chapter, get_or_404(db, models.Chapter, _target_id(item)))
    if chapter.id != change_set.chapter_id:
        raise HTTPException(status_code=422, detail="正文目标不是变更集章节")
    content = _string(item, "content", max_length=2_000_000)
    chapter.content = content
    chapter.word_count = word_count(content)
    chapter.revision += 1
    db.flush()
    return chapter.id


def _apply_chapter_summary(
    db: Session, change_set: models.ProposedChangeSet, item: ProposedChangeItem
) -> int:
    chapter_id = _integer(item, "chapter_id")
    if chapter_id != change_set.chapter_id:
        raise HTTPException(status_code=422, detail="摘要目标不是变更集章节")
    summary = (
        db.get(models.ChapterSummary, item.target_id)
        if item.target_id is not None
        else db.scalar(
            select(models.ChapterSummary).where(
                models.ChapterSummary.chapter_id == chapter_id,
                models.ChapterSummary.deleted_at.is_(None),
            )
        )
    )
    if summary is None:
        summary = models.ChapterSummary(chapter_id=chapter_id)
        db.add(summary)
    summary.summary = _string(item, "summary", max_length=100_000)
    summary.key_events_json = _dump(_string_list(item, "key_events"))
    summary.entity_ids_json = _dump(_integer_list(item, "entity_ids"))
    summary.token_count = _integer(item, "token_count", minimum=0)
    summary.source = _string(item, "source", max_length=40)
    if summary.id is not None:
        summary.revision += 1
    db.flush()
    return summary.id


def _apply_scene_synopsis(db: Session, item: ProposedChangeItem) -> int:
    scene = cast(models.Scene, get_or_404(db, models.Scene, _target_id(item)))
    scene.synopsis = _string(item, "synopsis", max_length=50_000)
    scene.revision += 1
    db.flush()
    return scene.id


def _apply_scene_state(
    db: Session,
    change_set: models.ProposedChangeSet,
    item: ProposedChangeItem,
    entity_refs: dict[str, int],
) -> int:
    scene_id = _integer(item, "scene_id")
    scene = cast(models.Scene, get_or_404(db, models.Scene, scene_id))
    _require_scene_project(db, scene, change_set.project_id)
    state = (
        db.get(models.SceneState, item.target_id)
        if item.target_id is not None
        else db.scalar(
            select(models.SceneState).where(
                models.SceneState.scene_id == scene_id,
                models.SceneState.deleted_at.is_(None),
            )
        )
    )
    if state is None:
        state = models.SceneState(scene_id=scene_id)
        db.add(state)
    state.viewpoint_entity_id = _optional_entity_reference(
        db,
        change_set.project_id,
        item,
        "viewpoint_entity_id",
        "viewpoint_entity_ref",
        entity_refs,
    )
    state.location_entity_id = _optional_entity_reference(
        db,
        change_set.project_id,
        item,
        "location_entity_id",
        "location_entity_ref",
        entity_refs,
    )
    item_ids = _integer_list(item, "item_entity_ids")
    for reference in _string_list(item, "item_entity_refs"):
        item_ids.append(_resolved_ref(reference, entity_refs))
    for entity_id in item_ids:
        _entity_in_project(db, entity_id, change_set.project_id)
    state.item_entity_ids_json = _dump(list(dict.fromkeys(item_ids)))
    raw_state = item.proposed.get("state")
    if not isinstance(raw_state, dict):
        raise HTTPException(status_code=422, detail=f"{item.id}.state 必须是对象")
    state.state_json = _dump(raw_state)
    state.notes = _string(item, "notes", max_length=50_000)
    if state.id is not None:
        state.revision += 1
    db.flush()
    return state.id


def _apply_entity(
    db: Session,
    change_set: models.ProposedChangeSet,
    item: ProposedChangeItem,
    entity_refs: dict[str, int],
) -> int:
    entity = (
        db.get(models.StoryEntity, item.target_id)
        if item.target_id is not None
        else None
    )
    is_new = entity is None
    if entity is None:
        entity = models.StoryEntity(project_id=change_set.project_id)
        db.add(entity)
    elif entity.project_id != change_set.project_id:
        raise HTTPException(status_code=422, detail="实体目标不属于当前项目")
    entity.name = _string(item, "name", max_length=200, minimum_length=1)
    entity.kind = _string(item, "kind", max_length=40, minimum_length=1)
    if entity.kind not in {"character", "location", "item", "organization"}:
        raise HTTPException(status_code=422, detail=f"{item.id}.kind 不在白名单")
    entity.description = _string(item, "description", max_length=100_000)
    entity.tags = _dump(_string_list(item, "tags", maximum=100))
    if not is_new:
        entity.revision += 1
    db.flush()
    entity_refs[item.id] = entity.id
    return entity.id


def _apply_entity_alias(
    db: Session,
    change_set: models.ProposedChangeSet,
    item: ProposedChangeItem,
    entity_refs: dict[str, int],
) -> int:
    entity_id = _required_entity_reference(
        db,
        change_set.project_id,
        item,
        "entity_id",
        "entity_ref",
        entity_refs,
    )
    alias_value = _string(item, "alias", max_length=200, minimum_length=1)
    existing = db.scalar(
        select(models.EntityAlias).where(
            models.EntityAlias.entity_id == entity_id,
            models.EntityAlias.alias == alias_value,
            models.EntityAlias.deleted_at.is_(None),
        )
    )
    if existing is not None:
        return existing.id
    alias = models.EntityAlias(entity_id=entity_id, alias=alias_value)
    db.add(alias)
    db.flush()
    return alias.id


def _apply_entity_relation(
    db: Session,
    change_set: models.ProposedChangeSet,
    item: ProposedChangeItem,
    entity_refs: dict[str, int],
) -> int:
    source_id = _required_entity_reference(
        db,
        change_set.project_id,
        item,
        "source_entity_id",
        "source_entity_ref",
        entity_refs,
    )
    target_id = _required_entity_reference(
        db,
        change_set.project_id,
        item,
        "target_entity_id",
        "target_entity_ref",
        entity_refs,
    )
    relation = (
        db.get(models.EntityRelation, item.target_id)
        if item.target_id is not None
        else None
    )
    is_new = relation is None
    if relation is None:
        relation = models.EntityRelation(project_id=change_set.project_id)
        db.add(relation)
    elif relation.project_id != change_set.project_id:
        raise HTTPException(status_code=422, detail="关系目标不属于当前项目")
    relation.source_entity_id = source_id
    relation.target_entity_id = target_id
    relation.relation_type = _string(
        item, "relation_type", max_length=80, minimum_length=1
    )
    relation.notes = _string(item, "notes", max_length=20_000)
    if not is_new:
        relation.revision += 1
    db.flush()
    return relation.id


def _apply_entity_state_change(
    db: Session,
    change_set: models.ProposedChangeSet,
    item: ProposedChangeItem,
    entity_refs: dict[str, int],
) -> int:
    entity_id = _required_entity_reference(
        db,
        change_set.project_id,
        item,
        "entity_id",
        "entity_ref",
        entity_refs,
    )
    chapter_id = _optional_integer(item, "chapter_id")
    if chapter_id is not None:
        _require_chapter_project(db, chapter_id, change_set.project_id)
    state = models.EntityStateChange(
        entity_id=entity_id,
        chapter_id=chapter_id,
        field_name=_string(item, "field_name", max_length=100, minimum_length=1),
        old_value=_string(item, "old_value", max_length=20_000),
        new_value=_string(item, "new_value", max_length=20_000),
        reason=_string(item, "reason", max_length=20_000),
    )
    db.add(state)
    db.flush()
    return state.id


def _apply_timeline(
    db: Session, change_set: models.ProposedChangeSet, item: ProposedChangeItem
) -> int:
    event = (
        db.get(models.TimelineEvent, item.target_id)
        if item.target_id is not None
        else None
    )
    is_new = event is None
    if event is None:
        event = models.TimelineEvent(project_id=change_set.project_id)
        db.add(event)
    elif event.project_id != change_set.project_id:
        raise HTTPException(status_code=422, detail="时间线目标不属于当前项目")
    chapter_id = _optional_integer(item, "chapter_id")
    if chapter_id is not None:
        _require_chapter_project(db, chapter_id, change_set.project_id)
    event.chapter_id = chapter_id
    event.label = _string(item, "label", max_length=200, minimum_length=1)
    event.event_time = _string(item, "event_time", max_length=100)
    event.description = _string(item, "description", max_length=50_000)
    event.position = _integer(item, "position", minimum=0)
    if not is_new:
        event.revision += 1
    db.flush()
    return event.id


def _apply_foreshadow(
    db: Session, change_set: models.ProposedChangeSet, item: ProposedChangeItem
) -> int:
    foreshadow = (
        db.get(models.Foreshadow, item.target_id)
        if item.target_id is not None
        else None
    )
    is_new = foreshadow is None
    if foreshadow is None:
        foreshadow = models.Foreshadow(project_id=change_set.project_id)
        db.add(foreshadow)
    elif foreshadow.project_id != change_set.project_id:
        raise HTTPException(status_code=422, detail="伏笔目标不属于当前项目")
    chapter_id = _optional_integer(item, "chapter_id")
    if chapter_id is not None:
        _require_chapter_project(db, chapter_id, change_set.project_id)
    foreshadow.setup_text = _string(
        item, "setup_text", max_length=100_000, minimum_length=1
    )
    foreshadow.payoff_text = _string(item, "payoff_text", max_length=100_000)
    status_value = _string(item, "status", max_length=40, minimum_length=1)
    if status_value not in {"open", "resolved"}:
        raise HTTPException(status_code=422, detail=f"{item.id}.status 不在白名单")
    foreshadow.status = status_value
    foreshadow.chapter_id = chapter_id
    if not is_new:
        foreshadow.revision += 1
    db.flush()
    return foreshadow.id


def _validate_item_values(
    db: Session, row: models.ProposedChangeSet, item: ProposedChangeItem
) -> None:
    if item.target_id is not None:
        model_entry = change_sets.TARGET_MODELS.get(item.kind)
        if model_entry is None:
            raise HTTPException(status_code=422, detail=f"{item.id} 不允许目标 ID")
        _, model = model_entry
        target = db.get(model, item.target_id)
        if target is None or not change_sets._target_in_project(db, target, row.project_id):
            raise HTTPException(status_code=422, detail=f"{item.id} 的目标不属于当前项目")
    project_value = item.proposed.get("project_id")
    if project_value is not None and project_value != row.project_id:
        raise HTTPException(status_code=422, detail=f"{item.id}.project_id 不匹配")
    chapter_value = item.proposed.get("chapter_id")
    if chapter_value is not None:
        if not _is_integer(chapter_value):
            raise HTTPException(status_code=422, detail=f"{item.id}.chapter_id 必须是整数")
        _require_chapter_project(db, int(chapter_value), row.project_id)


def _required_entity_reference(
    db: Session,
    project_id: int,
    item: ProposedChangeItem,
    id_key: str,
    ref_key: str,
    refs: dict[str, int],
) -> int:
    value = _optional_entity_reference(
        db, project_id, item, id_key, ref_key, refs
    )
    if value is None:
        raise HTTPException(status_code=422, detail=f"{item.id} 缺少实体引用")
    return value


def _optional_entity_reference(
    db: Session,
    project_id: int,
    item: ProposedChangeItem,
    id_key: str,
    ref_key: str,
    refs: dict[str, int],
) -> int | None:
    direct = item.proposed.get(id_key)
    reference = item.proposed.get(ref_key)
    if direct is not None and reference is not None:
        raise HTTPException(status_code=422, detail=f"{item.id} 同时提供 ID 和临时引用")
    if direct is not None:
        if not _is_integer(direct):
            raise HTTPException(status_code=422, detail=f"{item.id}.{id_key} 必须是整数")
        entity_id = int(direct)
        _entity_in_project(db, entity_id, project_id)
        return entity_id
    if reference is not None:
        if not isinstance(reference, str):
            raise HTTPException(status_code=422, detail=f"{item.id}.{ref_key} 必须是字符串")
        return _resolved_ref(reference, refs)
    return None


def _resolved_ref(reference: str, refs: dict[str, int]) -> int:
    try:
        return refs[reference]
    except KeyError as error:
        raise HTTPException(status_code=409, detail=f"临时实体引用 {reference} 未解析") from error


def _entity_in_project(
    db: Session, entity_id: int, project_id: int
) -> models.StoryEntity:
    entity = cast(models.StoryEntity, get_or_404(db, models.StoryEntity, entity_id))
    if entity.project_id != project_id:
        raise HTTPException(status_code=422, detail="实体不属于当前项目")
    return entity


def _require_chapter_project(db: Session, chapter_id: int, project_id: int) -> None:
    chapter = cast(models.Chapter, get_or_404(db, models.Chapter, chapter_id))
    volume = cast(models.Volume, get_or_404(db, models.Volume, chapter.volume_id))
    if volume.project_id != project_id:
        raise HTTPException(status_code=422, detail="章节不属于当前项目")


def _require_scene_project(
    db: Session, scene: models.Scene, project_id: int
) -> None:
    _require_chapter_project(db, scene.chapter_id, project_id)


def _target_id(item: ProposedChangeItem) -> int:
    if item.target_id is None:
        raise HTTPException(status_code=422, detail=f"{item.id} 缺少写回目标")
    return item.target_id


def _string(
    item: ProposedChangeItem,
    key: str,
    *,
    max_length: int,
    minimum_length: int = 0,
) -> str:
    value = item.proposed.get(key)
    if not isinstance(value, str) or not minimum_length <= len(value) <= max_length:
        raise HTTPException(status_code=422, detail=f"{item.id}.{key} 文本长度无效")
    return value


def _integer(
    item: ProposedChangeItem,
    key: str,
    *,
    minimum: int = 1,
) -> int:
    value = item.proposed.get(key)
    if not _is_integer(value) or value < minimum:
        raise HTTPException(status_code=422, detail=f"{item.id}.{key} 必须是有效整数")
    return value


def _optional_integer(item: ProposedChangeItem, key: str) -> int | None:
    value = item.proposed.get(key)
    if value is None:
        return None
    if not _is_integer(value) or value < 1:
        raise HTTPException(status_code=422, detail=f"{item.id}.{key} 必须是有效整数")
    return value


def _string_list(
    item: ProposedChangeItem, key: str, *, maximum: int = 5_000
) -> list[str]:
    value = item.proposed.get(key)
    if not isinstance(value, list) or len(value) > maximum or not all(
        isinstance(entry, str) for entry in value
    ):
        raise HTTPException(status_code=422, detail=f"{item.id}.{key} 必须是字符串列表")
    return cast(list[str], value)


def _integer_list(item: ProposedChangeItem, key: str) -> list[int]:
    value = item.proposed.get(key)
    if not isinstance(value, list) or len(value) > 5_000 or not all(
        _is_integer(entry) and int(entry) >= 1 for entry in value
    ):
        raise HTTPException(status_code=422, detail=f"{item.id}.{key} 必须是正整数列表")
    return [int(entry) for entry in value]


def _is_integer(value: Any) -> TypeGuard[int]:
    return isinstance(value, int) and not isinstance(value, bool)


def _json_entries(value: str) -> list[dict[str, Any]]:
    parsed = json.loads(value)
    if not isinstance(parsed, list):
        return []
    return [entry for entry in parsed if isinstance(entry, dict)]


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
