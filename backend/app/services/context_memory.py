from __future__ import annotations

import json
from typing import Any, cast
from urllib.parse import urlsplit

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.repositories import get_or_404, require_revision, soft_delete
from app.schemas.context import (
    ALL_CLASSIFICATIONS,
    DEFAULT_SECTION_PRIORITIES,
    ChapterEntityLinkCreate,
    ChapterEntityLinkRead,
    ChapterEntityLinkUpdate,
    ChapterSummaryCreate,
    ChapterSummaryRead,
    ChapterSummaryUpdate,
    ContentClassificationCreate,
    ContentClassificationRead,
    ContentClassificationUpdate,
    ContextPinCreate,
    ContextPinRead,
    ContextPinUpdate,
    ContextPolicyCreate,
    ContextPolicyRead,
    ContextPolicyUpdate,
    ProviderDataPolicyRead,
    ProviderDataPolicyUpdate,
    SceneStateCreate,
    SceneStateRead,
    SceneStateUpdate,
)
from app.services.usage_control import estimate_text_tokens


DEFAULT_POLICY_NAME = "默认长篇写作"
LOCAL_PROVIDER_TYPES = {"mock", "ollama", "local"}


def create_chapter_summary(
    db: Session, payload: ChapterSummaryCreate
) -> ChapterSummaryRead:
    _chapter_project_id(db, payload.chapter_id)
    existing = db.scalar(
        select(models.ChapterSummary).where(
            models.ChapterSummary.chapter_id == payload.chapter_id,
            models.ChapterSummary.deleted_at.is_(None),
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="该章节已有摘要")
    row = models.ChapterSummary(
        chapter_id=payload.chapter_id,
        summary=payload.summary,
        key_events_json=_dump(payload.key_events),
        entity_ids_json=_dump(_unique_ids(payload.entity_ids)),
        token_count=estimate_text_tokens(payload.summary),
        source=payload.source,
    )
    _validate_entities_for_chapter(db, payload.chapter_id, payload.entity_ids)
    db.add(row)
    db.flush()
    return chapter_summary_read(row)


def update_chapter_summary(
    db: Session, summary_id: int, payload: ChapterSummaryUpdate
) -> ChapterSummaryRead:
    row = cast(models.ChapterSummary, get_or_404(db, models.ChapterSummary, summary_id))
    require_revision(row, payload.expected_revision)
    _chapter_project_id(db, payload.chapter_id)
    _validate_entities_for_chapter(db, payload.chapter_id, payload.entity_ids)
    duplicate = db.scalar(
        select(models.ChapterSummary).where(
            models.ChapterSummary.chapter_id == payload.chapter_id,
            models.ChapterSummary.id != row.id,
            models.ChapterSummary.deleted_at.is_(None),
        )
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="该章节已有摘要")
    row.chapter_id = payload.chapter_id
    row.summary = payload.summary
    row.key_events_json = _dump(payload.key_events)
    row.entity_ids_json = _dump(_unique_ids(payload.entity_ids))
    row.token_count = estimate_text_tokens(payload.summary)
    row.source = payload.source
    row.revision += 1
    db.flush()
    return chapter_summary_read(row)


def list_chapter_summaries(db: Session, project_id: int) -> list[ChapterSummaryRead]:
    _project(db, project_id)
    rows = db.scalars(
        select(models.ChapterSummary)
        .join(models.Chapter, models.Chapter.id == models.ChapterSummary.chapter_id)
        .join(models.Volume, models.Volume.id == models.Chapter.volume_id)
        .where(
            models.Volume.project_id == project_id,
            models.ChapterSummary.deleted_at.is_(None),
        )
        .order_by(models.Chapter.position, models.ChapterSummary.id)
    ).all()
    return [chapter_summary_read(row) for row in rows]


def create_scene_state(db: Session, payload: SceneStateCreate) -> SceneStateRead:
    project_id = _scene_project_id(db, payload.scene_id)
    _validate_scene_state_entities(db, project_id, payload)
    existing = db.scalar(
        select(models.SceneState).where(
            models.SceneState.scene_id == payload.scene_id,
            models.SceneState.deleted_at.is_(None),
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="该场景已有状态记录")
    row = models.SceneState(
        scene_id=payload.scene_id,
        viewpoint_entity_id=payload.viewpoint_entity_id,
        location_entity_id=payload.location_entity_id,
        item_entity_ids_json=_dump(_unique_ids(payload.item_entity_ids)),
        state_json=_dump(payload.state),
        notes=payload.notes,
    )
    db.add(row)
    db.flush()
    return scene_state_read(row)


def update_scene_state(
    db: Session, state_id: int, payload: SceneStateUpdate
) -> SceneStateRead:
    row = cast(models.SceneState, get_or_404(db, models.SceneState, state_id))
    require_revision(row, payload.expected_revision)
    project_id = _scene_project_id(db, payload.scene_id)
    _validate_scene_state_entities(db, project_id, payload)
    duplicate = db.scalar(
        select(models.SceneState).where(
            models.SceneState.scene_id == payload.scene_id,
            models.SceneState.id != row.id,
            models.SceneState.deleted_at.is_(None),
        )
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="该场景已有状态记录")
    row.scene_id = payload.scene_id
    row.viewpoint_entity_id = payload.viewpoint_entity_id
    row.location_entity_id = payload.location_entity_id
    row.item_entity_ids_json = _dump(_unique_ids(payload.item_entity_ids))
    row.state_json = _dump(payload.state)
    row.notes = payload.notes
    row.revision += 1
    db.flush()
    return scene_state_read(row)


def list_scene_states(db: Session, project_id: int) -> list[SceneStateRead]:
    _project(db, project_id)
    rows = db.scalars(
        select(models.SceneState)
        .join(models.Scene, models.Scene.id == models.SceneState.scene_id)
        .join(models.Chapter, models.Chapter.id == models.Scene.chapter_id)
        .join(models.Volume, models.Volume.id == models.Chapter.volume_id)
        .where(
            models.Volume.project_id == project_id,
            models.SceneState.deleted_at.is_(None),
        )
        .order_by(models.Chapter.position, models.Scene.position, models.SceneState.id)
    ).all()
    return [scene_state_read(row) for row in rows]


def create_chapter_entity_link(
    db: Session, payload: ChapterEntityLinkCreate
) -> ChapterEntityLinkRead:
    project_id = _chapter_project_id(db, payload.chapter_id)
    _entity_in_project(db, payload.entity_id, project_id)
    existing = db.scalar(
        select(models.ChapterEntityLink).where(
            models.ChapterEntityLink.chapter_id == payload.chapter_id,
            models.ChapterEntityLink.entity_id == payload.entity_id,
            models.ChapterEntityLink.link_type == payload.link_type,
            models.ChapterEntityLink.deleted_at.is_(None),
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="章节与实体的同类链接已存在")
    row = models.ChapterEntityLink(**payload.model_dump())
    db.add(row)
    db.flush()
    return chapter_entity_link_read(row)


def update_chapter_entity_link(
    db: Session, link_id: int, payload: ChapterEntityLinkUpdate
) -> ChapterEntityLinkRead:
    row = cast(
        models.ChapterEntityLink, get_or_404(db, models.ChapterEntityLink, link_id)
    )
    require_revision(row, payload.expected_revision)
    project_id = _chapter_project_id(db, payload.chapter_id)
    _entity_in_project(db, payload.entity_id, project_id)
    duplicate = db.scalar(
        select(models.ChapterEntityLink).where(
            models.ChapterEntityLink.chapter_id == payload.chapter_id,
            models.ChapterEntityLink.entity_id == payload.entity_id,
            models.ChapterEntityLink.link_type == payload.link_type,
            models.ChapterEntityLink.id != row.id,
            models.ChapterEntityLink.deleted_at.is_(None),
        )
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="章节与实体的同类链接已存在")
    for key, value in payload.model_dump(exclude={"expected_revision"}).items():
        setattr(row, key, value)
    row.revision += 1
    db.flush()
    return chapter_entity_link_read(row)


def list_chapter_entity_links(
    db: Session, project_id: int
) -> list[ChapterEntityLinkRead]:
    _project(db, project_id)
    rows = db.scalars(
        select(models.ChapterEntityLink)
        .join(models.Chapter, models.Chapter.id == models.ChapterEntityLink.chapter_id)
        .join(models.Volume, models.Volume.id == models.Chapter.volume_id)
        .where(
            models.Volume.project_id == project_id,
            models.ChapterEntityLink.deleted_at.is_(None),
        )
        .order_by(models.Chapter.position, models.ChapterEntityLink.id)
    ).all()
    return [chapter_entity_link_read(row) for row in rows]


def create_context_pin(db: Session, payload: ContextPinCreate) -> ContextPinRead:
    _project(db, payload.project_id)
    _require_source_project(db, payload.source_type, payload.source_id, payload.project_id)
    existing = db.scalar(
        select(models.ContextPin).where(
            models.ContextPin.project_id == payload.project_id,
            models.ContextPin.source_type == payload.source_type,
            models.ContextPin.source_id == payload.source_id,
            models.ContextPin.deleted_at.is_(None),
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="该来源已经 Pin")
    row = models.ContextPin(**payload.model_dump())
    db.add(row)
    db.flush()
    return context_pin_read(row)


def update_context_pin(
    db: Session, pin_id: int, payload: ContextPinUpdate
) -> ContextPinRead:
    row = cast(models.ContextPin, get_or_404(db, models.ContextPin, pin_id))
    require_revision(row, payload.expected_revision)
    _require_source_project(db, payload.source_type, payload.source_id, payload.project_id)
    duplicate = db.scalar(
        select(models.ContextPin).where(
            models.ContextPin.project_id == payload.project_id,
            models.ContextPin.source_type == payload.source_type,
            models.ContextPin.source_id == payload.source_id,
            models.ContextPin.id != row.id,
            models.ContextPin.deleted_at.is_(None),
        )
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="该来源已经 Pin")
    for key, value in payload.model_dump(exclude={"expected_revision"}).items():
        setattr(row, key, value)
    row.revision += 1
    db.flush()
    return context_pin_read(row)


def list_context_pins(db: Session, project_id: int) -> list[ContextPinRead]:
    _project(db, project_id)
    rows = db.scalars(
        select(models.ContextPin)
        .where(
            models.ContextPin.project_id == project_id,
            models.ContextPin.deleted_at.is_(None),
        )
        .order_by(models.ContextPin.priority.desc(), models.ContextPin.id)
    ).all()
    return [context_pin_read(row) for row in rows]


def create_classification(
    db: Session, payload: ContentClassificationCreate
) -> ContentClassificationRead:
    _project(db, payload.project_id)
    _require_source_project(db, payload.source_type, payload.source_id, payload.project_id)
    existing = db.scalar(
        select(models.ContentClassification).where(
            models.ContentClassification.project_id == payload.project_id,
            models.ContentClassification.source_type == payload.source_type,
            models.ContentClassification.source_id == payload.source_id,
            models.ContentClassification.deleted_at.is_(None),
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="该来源已有数据分类")
    row = models.ContentClassification(**payload.model_dump())
    db.add(row)
    db.flush()
    return classification_read(row)


def update_classification(
    db: Session, classification_id: int, payload: ContentClassificationUpdate
) -> ContentClassificationRead:
    row = cast(
        models.ContentClassification,
        get_or_404(db, models.ContentClassification, classification_id),
    )
    require_revision(row, payload.expected_revision)
    _require_source_project(db, payload.source_type, payload.source_id, payload.project_id)
    duplicate = db.scalar(
        select(models.ContentClassification).where(
            models.ContentClassification.project_id == payload.project_id,
            models.ContentClassification.source_type == payload.source_type,
            models.ContentClassification.source_id == payload.source_id,
            models.ContentClassification.id != row.id,
            models.ContentClassification.deleted_at.is_(None),
        )
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="该来源已有数据分类")
    for key, value in payload.model_dump(exclude={"expected_revision"}).items():
        setattr(row, key, value)
    row.revision += 1
    db.flush()
    return classification_read(row)


def list_classifications(
    db: Session, project_id: int
) -> list[ContentClassificationRead]:
    _project(db, project_id)
    rows = db.scalars(
        select(models.ContentClassification)
        .where(
            models.ContentClassification.project_id == project_id,
            models.ContentClassification.deleted_at.is_(None),
        )
        .order_by(models.ContentClassification.source_type, models.ContentClassification.source_id)
    ).all()
    return [classification_read(row) for row in rows]


def ensure_default_context_policy(db: Session, project_id: int) -> models.ContextPolicy:
    _project(db, project_id)
    row = db.scalar(
        select(models.ContextPolicy).where(
            models.ContextPolicy.project_id == project_id,
            models.ContextPolicy.name == DEFAULT_POLICY_NAME,
            models.ContextPolicy.deleted_at.is_(None),
        )
    )
    if row is not None:
        return row
    row = models.ContextPolicy(
        project_id=project_id,
        name=DEFAULT_POLICY_NAME,
        token_budget=6_000,
        recent_chapter_count=3,
        max_results=80,
        min_relevance=0.2,
        section_priorities_json=_dump(DEFAULT_SECTION_PRIORITIES),
        required_sections_json=_dump(["user_task"]),
        allowed_classifications_json=_dump(ALL_CLASSIFICATIONS[:-1]),
        use_summaries=True,
        enabled=True,
    )
    db.add(row)
    db.flush()
    return row


def create_context_policy(
    db: Session, payload: ContextPolicyCreate
) -> ContextPolicyRead:
    _project(db, payload.project_id)
    _require_policy_name_available(db, payload.project_id, payload.name)
    row = models.ContextPolicy(**_policy_values(payload))
    db.add(row)
    db.flush()
    return context_policy_read(row)


def update_context_policy(
    db: Session, policy_id: int, payload: ContextPolicyUpdate
) -> ContextPolicyRead:
    row = cast(models.ContextPolicy, get_or_404(db, models.ContextPolicy, policy_id))
    require_revision(row, payload.expected_revision)
    _project(db, payload.project_id)
    _require_policy_name_available(db, payload.project_id, payload.name, exclude_id=row.id)
    for key, value in _policy_values(payload).items():
        setattr(row, key, value)
    row.revision += 1
    db.flush()
    return context_policy_read(row)


def list_context_policies(db: Session, project_id: int) -> list[ContextPolicyRead]:
    ensure_default_context_policy(db, project_id)
    rows = db.scalars(
        select(models.ContextPolicy)
        .where(
            models.ContextPolicy.project_id == project_id,
            models.ContextPolicy.deleted_at.is_(None),
        )
        .order_by(models.ContextPolicy.id)
    ).all()
    return [context_policy_read(row) for row in rows]


def ensure_provider_data_policy(
    db: Session, provider_id: int
) -> models.ProviderDataPolicy:
    provider = cast(
        models.ProviderAccount, get_or_404(db, models.ProviderAccount, provider_id)
    )
    row = db.scalar(
        select(models.ProviderDataPolicy).where(
            models.ProviderDataPolicy.provider_account_id == provider_id,
            models.ProviderDataPolicy.deleted_at.is_(None),
        )
    )
    if row is not None:
        return row
    allowed = default_provider_classifications(provider)
    row = models.ProviderDataPolicy(
        provider_account_id=provider.id,
        allowed_classifications_json=_dump(allowed),
        block_on_required_exclusion=True,
        notes=(
            "本机 Provider 默认允许未发布稿件；secret 仍需显式放行。"
            if is_local_provider(provider)
            else "远程 Provider 默认仅允许 public 与 internal，请按实际合同和合规要求调整。"
        ),
        enabled=True,
    )
    db.add(row)
    db.flush()
    return row


def list_provider_data_policies(db: Session) -> list[ProviderDataPolicyRead]:
    providers = db.scalars(
        select(models.ProviderAccount)
        .where(models.ProviderAccount.deleted_at.is_(None))
        .order_by(models.ProviderAccount.id)
    ).all()
    rows = [ensure_provider_data_policy(db, provider.id) for provider in providers]
    return [provider_policy_read(row) for row in rows]


def update_provider_data_policy(
    db: Session, provider_id: int, payload: ProviderDataPolicyUpdate
) -> ProviderDataPolicyRead:
    if payload.provider_account_id != provider_id:
        raise HTTPException(status_code=422, detail="Provider id 与路径不一致")
    row = ensure_provider_data_policy(db, provider_id)
    require_revision(row, payload.expected_revision)
    row.allowed_classifications_json = _dump(payload.allowed_classifications)
    row.block_on_required_exclusion = payload.block_on_required_exclusion
    row.notes = payload.notes
    row.enabled = payload.enabled
    row.revision += 1
    db.flush()
    return provider_policy_read(row)


def delete_record(
    db: Session, resource: str, record_id: int, expected_revision: int
) -> None:
    mapping: dict[str, type[Any]] = {
        "chapter-summary": models.ChapterSummary,
        "scene-state": models.SceneState,
        "chapter-entity-link": models.ChapterEntityLink,
        "context-pin": models.ContextPin,
        "classification": models.ContentClassification,
        "context-policy": models.ContextPolicy,
    }
    model = mapping.get(resource)
    if model is None:
        raise HTTPException(status_code=404, detail="不支持的上下文资源")
    row = get_or_404(db, model, record_id)
    require_revision(row, expected_revision)
    soft_delete(row)
    db.flush()


def default_provider_classifications(
    provider: models.ProviderAccount,
) -> list[str]:
    if is_local_provider(provider):
        return list(ALL_CLASSIFICATIONS[:-1])
    return ["public", "internal"]


def is_local_provider(provider: models.ProviderAccount) -> bool:
    if provider.provider_type.lower() in LOCAL_PROVIDER_TYPES:
        return True
    if not provider.base_url:
        return False
    host = (urlsplit(provider.base_url).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}


def chapter_summary_read(row: models.ChapterSummary) -> ChapterSummaryRead:
    return ChapterSummaryRead(
        id=row.id,
        chapter_id=row.chapter_id,
        summary=row.summary,
        key_events=_json_string_list(row.key_events_json),
        entity_ids=_json_int_list(row.entity_ids_json),
        token_count=row.token_count,
        source=cast(Any, row.source),
        revision=row.revision,
        deleted_at=row.deleted_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def scene_state_read(row: models.SceneState) -> SceneStateRead:
    return SceneStateRead(
        id=row.id,
        scene_id=row.scene_id,
        viewpoint_entity_id=row.viewpoint_entity_id,
        location_entity_id=row.location_entity_id,
        item_entity_ids=_json_int_list(row.item_entity_ids_json),
        state=_json_object(row.state_json),
        notes=row.notes,
        revision=row.revision,
        deleted_at=row.deleted_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def chapter_entity_link_read(
    row: models.ChapterEntityLink,
) -> ChapterEntityLinkRead:
    return ChapterEntityLinkRead(
        id=row.id,
        chapter_id=row.chapter_id,
        entity_id=row.entity_id,
        link_type=row.link_type,
        relevance=row.relevance,
        notes=row.notes,
        revision=row.revision,
        deleted_at=row.deleted_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def context_pin_read(row: models.ContextPin) -> ContextPinRead:
    return ContextPinRead(
        id=row.id,
        project_id=row.project_id,
        source_type=row.source_type,
        source_id=row.source_id,
        label=row.label,
        content_override=row.content_override,
        priority=row.priority,
        required=row.required,
        enabled=row.enabled,
        revision=row.revision,
        deleted_at=row.deleted_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def classification_read(
    row: models.ContentClassification,
) -> ContentClassificationRead:
    return ContentClassificationRead(
        id=row.id,
        project_id=row.project_id,
        source_type=row.source_type,
        source_id=row.source_id,
        classification=cast(Any, row.classification),
        reason=row.reason,
        revision=row.revision,
        deleted_at=row.deleted_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def context_policy_read(row: models.ContextPolicy) -> ContextPolicyRead:
    return ContextPolicyRead(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        token_budget=row.token_budget,
        recent_chapter_count=row.recent_chapter_count,
        max_results=row.max_results,
        min_relevance=row.min_relevance,
        section_priorities=_json_int_object(row.section_priorities_json),
        required_sections=_json_string_list(row.required_sections_json),
        allowed_classifications=cast(Any, _json_string_list(row.allowed_classifications_json)),
        use_summaries=row.use_summaries,
        enabled=row.enabled,
        revision=row.revision,
        deleted_at=row.deleted_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def provider_policy_read(row: models.ProviderDataPolicy) -> ProviderDataPolicyRead:
    return ProviderDataPolicyRead(
        id=row.id,
        provider_account_id=row.provider_account_id,
        allowed_classifications=cast(
            Any, _json_string_list(row.allowed_classifications_json)
        ),
        block_on_required_exclusion=row.block_on_required_exclusion,
        notes=row.notes,
        enabled=row.enabled,
        revision=row.revision,
        deleted_at=row.deleted_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        inherited_default=False,
    )


def ensure_all_context_defaults(db: Session) -> None:
    for project_id in db.scalars(
        select(models.Project.id).where(models.Project.deleted_at.is_(None))
    ).all():
        ensure_default_context_policy(db, project_id)
    for provider_id in db.scalars(
        select(models.ProviderAccount.id).where(models.ProviderAccount.deleted_at.is_(None))
    ).all():
        ensure_provider_data_policy(db, provider_id)


def _policy_values(payload: ContextPolicyCreate | ContextPolicyUpdate) -> dict[str, Any]:
    return {
        "project_id": payload.project_id,
        "name": payload.name,
        "token_budget": payload.token_budget,
        "recent_chapter_count": payload.recent_chapter_count,
        "max_results": payload.max_results,
        "min_relevance": payload.min_relevance,
        "section_priorities_json": _dump(payload.section_priorities),
        "required_sections_json": _dump(payload.required_sections),
        "allowed_classifications_json": _dump(payload.allowed_classifications),
        "use_summaries": payload.use_summaries,
        "enabled": payload.enabled,
    }


def _require_policy_name_available(
    db: Session, project_id: int, name: str, *, exclude_id: int | None = None
) -> None:
    statement = select(models.ContextPolicy.id).where(
        models.ContextPolicy.project_id == project_id,
        models.ContextPolicy.name == name,
        models.ContextPolicy.deleted_at.is_(None),
    )
    if exclude_id is not None:
        statement = statement.where(models.ContextPolicy.id != exclude_id)
    if db.scalar(statement) is not None:
        raise HTTPException(status_code=409, detail="上下文策略名称已存在")


def _validate_scene_state_entities(
    db: Session, project_id: int, payload: SceneStateCreate | SceneStateUpdate
) -> None:
    ids = [
        item
        for item in [
            payload.viewpoint_entity_id,
            payload.location_entity_id,
            *payload.item_entity_ids,
        ]
        if item is not None
    ]
    for entity_id in _unique_ids(ids):
        _entity_in_project(db, entity_id, project_id)


def _validate_entities_for_chapter(
    db: Session, chapter_id: int, entity_ids: list[int]
) -> None:
    project_id = _chapter_project_id(db, chapter_id)
    for entity_id in _unique_ids(entity_ids):
        _entity_in_project(db, entity_id, project_id)


def _require_source_project(
    db: Session, source_type: str, source_id: int, expected_project_id: int
) -> None:
    if source_type == "chapter":
        project_id = _chapter_project_id(db, source_id)
    elif source_type == "scene":
        project_id = _scene_project_id(db, source_id)
    elif source_type == "chapter_summary":
        summary = cast(
            models.ChapterSummary, get_or_404(db, models.ChapterSummary, source_id)
        )
        project_id = _chapter_project_id(db, summary.chapter_id)
    elif source_type == "scene_state":
        scene_state = cast(models.SceneState, get_or_404(db, models.SceneState, source_id))
        project_id = _scene_project_id(db, scene_state.scene_id)
    elif source_type == "chapter_entity_link":
        chapter_link = cast(
            models.ChapterEntityLink,
            get_or_404(db, models.ChapterEntityLink, source_id),
        )
        project_id = _chapter_project_id(db, chapter_link.chapter_id)
    elif source_type == "entity":
        entity = cast(models.StoryEntity, get_or_404(db, models.StoryEntity, source_id))
        project_id = entity.project_id
    elif source_type == "relation":
        relation = cast(models.EntityRelation, get_or_404(db, models.EntityRelation, source_id))
        project_id = relation.project_id
    elif source_type == "timeline":
        timeline = cast(models.TimelineEvent, get_or_404(db, models.TimelineEvent, source_id))
        project_id = timeline.project_id
    elif source_type == "foreshadow":
        foreshadow = cast(models.Foreshadow, get_or_404(db, models.Foreshadow, source_id))
        project_id = foreshadow.project_id
    elif source_type == "style_guide":
        style_guide = cast(models.StyleGuide, get_or_404(db, models.StyleGuide, source_id))
        project_id = style_guide.project_id
    else:
        raise HTTPException(status_code=422, detail=f"不支持的上下文来源：{source_type}")
    if project_id != expected_project_id:
        raise HTTPException(status_code=422, detail="上下文来源不属于当前项目")


def _entity_in_project(
    db: Session, entity_id: int, project_id: int
) -> models.StoryEntity:
    entity = cast(models.StoryEntity, get_or_404(db, models.StoryEntity, entity_id))
    if entity.project_id != project_id:
        raise HTTPException(status_code=422, detail="实体不属于当前项目")
    return entity


def _project(db: Session, project_id: int) -> models.Project:
    return cast(models.Project, get_or_404(db, models.Project, project_id))


def _chapter_project_id(db: Session, chapter_id: int) -> int:
    row = db.execute(
        select(models.Volume.project_id)
        .join(models.Chapter, models.Chapter.volume_id == models.Volume.id)
        .where(models.Chapter.id == chapter_id, models.Chapter.deleted_at.is_(None))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Chapter not found")
    return int(row)


def _scene_project_id(db: Session, scene_id: int) -> int:
    row = db.execute(
        select(models.Volume.project_id)
        .join(models.Chapter, models.Chapter.volume_id == models.Volume.id)
        .join(models.Scene, models.Scene.chapter_id == models.Chapter.id)
        .where(models.Scene.id == scene_id, models.Scene.deleted_at.is_(None))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Scene not found")
    return int(row)


def _unique_ids(values: list[int]) -> list[int]:
    return list(dict.fromkeys(values))


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_value(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _json_object(value: str) -> dict[str, Any]:
    result = _json_value(value)
    return result if isinstance(result, dict) else {}


def _json_int_object(value: str) -> dict[str, int]:
    result = _json_object(value)
    return {
        str(key): int(item)
        for key, item in result.items()
        if isinstance(item, int) and not isinstance(item, bool)
    }


def _json_string_list(value: str) -> list[str]:
    result = _json_value(value)
    return [str(item) for item in result] if isinstance(result, list) else []


def _json_int_list(value: str) -> list[int]:
    result = _json_value(value)
    if not isinstance(result, list):
        return []
    return [int(item) for item in result if isinstance(item, int) and not isinstance(item, bool)]
