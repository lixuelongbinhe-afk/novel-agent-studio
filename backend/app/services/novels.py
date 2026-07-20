import json
from collections.abc import Iterable
from typing import Any, cast

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.repositories import (
    get_including_deleted_or_404,
    get_or_404,
    list_active,
    list_deleted,
    require_revision,
    restore,
    soft_delete,
    word_count,
)
from app.schemas import (
    ChapterAutosave,
    ChapterCreate,
    EntityAliasCreate,
    EntityAliasUpdate,
    EntityRelationCreate,
    EntityRelationUpdate,
    EntityStateChangeCreate,
    EntityStateChangeUpdate,
    ForeshadowCreate,
    ForeshadowUpdate,
    ProjectCreate,
    ProjectUpdate,
    ReorderRequest,
    SceneCreate,
    SceneUpdate,
    StoryEntityCreate,
    StoryEntityUpdate,
    StyleGuideCreate,
    StyleGuideUpdate,
    TimelineEventCreate,
    TimelineEventUpdate,
    VolumeCreate,
    VolumeUpdate,
)


RESOURCE_MODELS: dict[str, type[Any]] = {
    "project": models.Project,
    "volume": models.Volume,
    "chapter": models.Chapter,
    "scene": models.Scene,
    "entity": models.StoryEntity,
    "alias": models.EntityAlias,
    "relation": models.EntityRelation,
    "state-change": models.EntityStateChange,
    "timeline": models.TimelineEvent,
    "foreshadow": models.Foreshadow,
    "style-guide": models.StyleGuide,
}


def create_project(db: Session, payload: ProjectCreate) -> models.Project:
    project = models.Project(**payload.model_dump())
    db.add(project)
    db.flush()
    volume = models.Volume(project_id=project.id, title="第一卷", position=1)
    db.add(volume)
    db.flush()
    chapter = models.Chapter(volume_id=volume.id, title="第一章", content="", position=1)
    db.add(chapter)
    db.flush()
    from app.services.context_memory import ensure_default_context_policy

    ensure_default_context_policy(db, project.id)
    return project


def list_projects(db: Session, *, deleted: bool = False) -> list[models.Project]:
    loader = list_deleted if deleted else list_active
    return loader(db, models.Project)


def update_project(db: Session, project_id: int, payload: ProjectUpdate) -> models.Project:
    project = cast(models.Project, get_or_404(db, models.Project, project_id))
    require_revision(project, payload.expected_revision)
    for key, value in payload.model_dump(exclude={"expected_revision"}).items():
        setattr(project, key, value)
    project.revision += 1
    db.flush()
    return project


def project_tree(db: Session, project_id: int) -> dict[str, object]:
    project = get_or_404(db, models.Project, project_id)
    volumes = list_active(db, models.Volume, models.Volume.project_id == project.id)
    chapters = list_active(
        db,
        models.Chapter,
        models.Chapter.volume_id.in_([volume.id for volume in volumes] or [-1]),
    )
    scenes = list_active(
        db,
        models.Scene,
        models.Scene.chapter_id.in_([chapter.id for chapter in chapters] or [-1]),
    )
    return {"project": project, "volumes": volumes, "chapters": chapters, "scenes": scenes}


def create_volume(db: Session, project_id: int, payload: VolumeCreate) -> models.Volume:
    get_or_404(db, models.Project, project_id)
    volume = models.Volume(project_id=project_id, **payload.model_dump())
    db.add(volume)
    db.flush()
    return volume


def update_volume(db: Session, volume_id: int, payload: VolumeUpdate) -> models.Volume:
    volume = cast(models.Volume, get_or_404(db, models.Volume, volume_id))
    _apply_revision_update(volume, payload.model_dump())
    db.flush()
    return volume


def create_chapter(db: Session, volume_id: int, payload: ChapterCreate) -> models.Chapter:
    get_or_404(db, models.Volume, volume_id)
    chapter = models.Chapter(**payload.model_dump(), volume_id=volume_id)
    chapter.word_count = word_count(chapter.content)
    db.add(chapter)
    db.flush()
    return chapter


def autosave_chapter(db: Session, chapter_id: int, payload: ChapterAutosave) -> models.Chapter:
    chapter = cast(models.Chapter, get_or_404(db, models.Chapter, chapter_id))
    require_revision(chapter, payload.expected_revision)
    _snapshot_chapter(db, chapter, "autosave_before_update")
    chapter.title = payload.title
    chapter.content = payload.content
    chapter.word_count = word_count(payload.content)
    chapter.revision += 1
    db.flush()
    return chapter


def list_chapter_versions(db: Session, chapter_id: int) -> list[models.ChapterVersion]:
    get_or_404(db, models.Chapter, chapter_id)
    stmt = (
        select(models.ChapterVersion)
        .where(models.ChapterVersion.chapter_id == chapter_id)
        .order_by(models.ChapterVersion.created_at.desc(), models.ChapterVersion.id.desc())
    )
    return list(db.scalars(stmt).all())


def restore_chapter_version(
    db: Session, chapter_id: int, version_id: int, expected_revision: int | None = None
) -> models.Chapter:
    chapter = cast(models.Chapter, get_or_404(db, models.Chapter, chapter_id))
    if expected_revision is not None:
        require_revision(chapter, expected_revision)
    version = db.get(models.ChapterVersion, version_id)
    if version is None or version.chapter_id != chapter.id:
        raise HTTPException(status_code=404, detail="Chapter version not found")
    _snapshot_chapter(db, chapter, "before_restore")
    chapter.title = version.title
    chapter.content = version.content
    chapter.word_count = version.word_count
    chapter.revision += 1
    db.flush()
    return chapter


def create_scene(db: Session, chapter_id: int, payload: SceneCreate) -> models.Scene:
    get_or_404(db, models.Chapter, chapter_id)
    scene = models.Scene(chapter_id=chapter_id, **payload.model_dump())
    db.add(scene)
    db.flush()
    return scene


def update_scene(db: Session, scene_id: int, payload: SceneUpdate) -> models.Scene:
    scene = cast(models.Scene, get_or_404(db, models.Scene, scene_id))
    _apply_revision_update(scene, payload.model_dump())
    db.flush()
    return scene


def reorder_records(db: Session, resource: str, payload: ReorderRequest) -> list[Any]:
    model = {"volume": models.Volume, "chapter": models.Chapter, "scene": models.Scene, "timeline": models.TimelineEvent}.get(resource)
    if model is None:
        raise HTTPException(status_code=400, detail="Unsupported sortable resource")
    records = []
    parent_values: set[tuple[str, int]] = set()
    for requested in payload.items:
        record = get_or_404(db, model, requested.id)
        require_revision(record, requested.expected_revision)
        for field in ("project_id", "volume_id", "chapter_id"):
            if hasattr(record, field):
                parent_values.add((field, int(getattr(record, field))))
                break
        records.append((record, requested.position))
    if len(parent_values) != 1:
        raise HTTPException(status_code=400, detail="All reordered records must share one parent")
    for record, position in records:
        record.position = position
        record.revision += 1
    db.flush()
    return [record for record, _ in records]


def create_entity(db: Session, project_id: int, payload: StoryEntityCreate) -> models.StoryEntity:
    get_or_404(db, models.Project, project_id)
    data = payload.model_dump()
    data["tags"] = json.dumps(data["tags"], ensure_ascii=False)
    entity = models.StoryEntity(project_id=project_id, **data)
    db.add(entity)
    db.flush()
    return entity


def list_entities(db: Session, project_id: int, *, deleted: bool = False) -> list[models.StoryEntity]:
    get_or_404(db, models.Project, project_id)
    loader = list_deleted if deleted else list_active
    return loader(db, models.StoryEntity, models.StoryEntity.project_id == project_id)


def update_entity(db: Session, entity_id: int, payload: StoryEntityUpdate) -> models.StoryEntity:
    entity = cast(models.StoryEntity, get_or_404(db, models.StoryEntity, entity_id))
    data = payload.model_dump()
    data["tags"] = json.dumps(data["tags"], ensure_ascii=False)
    _apply_revision_update(entity, data)
    db.flush()
    return entity


def create_alias(db: Session, entity_id: int, payload: EntityAliasCreate) -> models.EntityAlias:
    get_or_404(db, models.StoryEntity, entity_id)
    alias = models.EntityAlias(entity_id=entity_id, **payload.model_dump())
    db.add(alias)
    try:
        db.flush()
    except Exception as exc:
        raise HTTPException(status_code=409, detail="Alias already exists for this entity") from exc
    return alias


def list_aliases(db: Session, project_id: int) -> list[models.EntityAlias]:
    entity_ids = _project_entity_ids(db, project_id)
    return list_active(db, models.EntityAlias, models.EntityAlias.entity_id.in_(entity_ids or [-1]))


def update_alias(db: Session, alias_id: int, payload: EntityAliasUpdate) -> models.EntityAlias:
    alias = cast(models.EntityAlias, get_or_404(db, models.EntityAlias, alias_id))
    _apply_revision_update(alias, payload.model_dump())
    db.flush()
    return alias


def create_relation(db: Session, project_id: int, payload: EntityRelationCreate) -> models.EntityRelation:
    _require_project_entities(db, project_id, [payload.source_entity_id, payload.target_entity_id])
    relation = models.EntityRelation(project_id=project_id, **payload.model_dump())
    db.add(relation)
    db.flush()
    return relation


def list_relations(db: Session, project_id: int) -> list[models.EntityRelation]:
    get_or_404(db, models.Project, project_id)
    return list_active(db, models.EntityRelation, models.EntityRelation.project_id == project_id)


def update_relation(db: Session, relation_id: int, payload: EntityRelationUpdate) -> models.EntityRelation:
    relation = cast(models.EntityRelation, get_or_404(db, models.EntityRelation, relation_id))
    _require_project_entities(db, relation.project_id, [payload.source_entity_id, payload.target_entity_id])
    _apply_revision_update(relation, payload.model_dump())
    db.flush()
    return relation


def create_state_change(
    db: Session, project_id: int, payload: EntityStateChangeCreate
) -> models.EntityStateChange:
    _require_project_entities(db, project_id, [payload.entity_id])
    _require_project_chapter(db, project_id, payload.chapter_id)
    change = models.EntityStateChange(**payload.model_dump())
    db.add(change)
    db.flush()
    return change


def list_state_changes(db: Session, project_id: int) -> list[models.EntityStateChange]:
    entity_ids = _project_entity_ids(db, project_id)
    return list_active(
        db, models.EntityStateChange, models.EntityStateChange.entity_id.in_(entity_ids or [-1])
    )


def update_state_change(
    db: Session, state_change_id: int, payload: EntityStateChangeUpdate
) -> models.EntityStateChange:
    change = cast(
        models.EntityStateChange,
        get_or_404(db, models.EntityStateChange, state_change_id),
    )
    entity = cast(models.StoryEntity, get_or_404(db, models.StoryEntity, payload.entity_id))
    _require_project_chapter(db, entity.project_id, payload.chapter_id)
    _apply_revision_update(change, payload.model_dump())
    db.flush()
    return change


def create_timeline_event(
    db: Session, project_id: int, payload: TimelineEventCreate
) -> models.TimelineEvent:
    get_or_404(db, models.Project, project_id)
    _require_project_chapter(db, project_id, payload.chapter_id)
    event = models.TimelineEvent(project_id=project_id, **payload.model_dump())
    db.add(event)
    db.flush()
    return event


def list_timeline_events(db: Session, project_id: int) -> list[models.TimelineEvent]:
    get_or_404(db, models.Project, project_id)
    return list_active(db, models.TimelineEvent, models.TimelineEvent.project_id == project_id)


def update_timeline_event(
    db: Session, event_id: int, payload: TimelineEventUpdate
) -> models.TimelineEvent:
    event = cast(models.TimelineEvent, get_or_404(db, models.TimelineEvent, event_id))
    _require_project_chapter(db, event.project_id, payload.chapter_id)
    _apply_revision_update(event, payload.model_dump())
    db.flush()
    return event


def create_foreshadow(db: Session, project_id: int, payload: ForeshadowCreate) -> models.Foreshadow:
    get_or_404(db, models.Project, project_id)
    _require_project_chapter(db, project_id, payload.chapter_id)
    item = models.Foreshadow(project_id=project_id, **payload.model_dump())
    db.add(item)
    db.flush()
    return item


def list_foreshadows(db: Session, project_id: int) -> list[models.Foreshadow]:
    get_or_404(db, models.Project, project_id)
    return list_active(db, models.Foreshadow, models.Foreshadow.project_id == project_id)


def update_foreshadow(
    db: Session, foreshadow_id: int, payload: ForeshadowUpdate
) -> models.Foreshadow:
    item = cast(models.Foreshadow, get_or_404(db, models.Foreshadow, foreshadow_id))
    _require_project_chapter(db, item.project_id, payload.chapter_id)
    _apply_revision_update(item, payload.model_dump())
    db.flush()
    return item


def create_style_guide(db: Session, project_id: int, payload: StyleGuideCreate) -> models.StyleGuide:
    get_or_404(db, models.Project, project_id)
    item = models.StyleGuide(project_id=project_id, **payload.model_dump())
    db.add(item)
    db.flush()
    return item


def list_style_guides(db: Session, project_id: int) -> list[models.StyleGuide]:
    get_or_404(db, models.Project, project_id)
    return list_active(db, models.StyleGuide, models.StyleGuide.project_id == project_id)


def update_style_guide(
    db: Session, style_guide_id: int, payload: StyleGuideUpdate
) -> models.StyleGuide:
    item = cast(models.StyleGuide, get_or_404(db, models.StyleGuide, style_guide_id))
    _apply_revision_update(item, payload.model_dump())
    db.flush()
    return item


def project_trash(db: Session, project_id: int) -> dict[str, list[Any]]:
    project = cast(models.Project, get_including_deleted_or_404(db, models.Project, project_id))
    volumes = list_deleted(db, models.Volume, models.Volume.project_id == project.id)
    active_volumes = list_active(db, models.Volume, models.Volume.project_id == project.id)
    volume_ids = [item.id for item in volumes + active_volumes]
    chapters = list_deleted(db, models.Chapter, models.Chapter.volume_id.in_(volume_ids or [-1]))
    active_chapters = list_active(db, models.Chapter, models.Chapter.volume_id.in_(volume_ids or [-1]))
    chapter_ids = [item.id for item in chapters + active_chapters]
    entities = list_deleted(db, models.StoryEntity, models.StoryEntity.project_id == project_id)
    active_entities = list_active(db, models.StoryEntity, models.StoryEntity.project_id == project_id)
    entity_ids = [item.id for item in entities + active_entities]
    records = {
        "projects": [project] if project.deleted_at is not None else [],
        "volumes": volumes,
        "chapters": chapters,
        "scenes": list_deleted(db, models.Scene, models.Scene.chapter_id.in_(chapter_ids or [-1])),
        "entities": entities,
        "aliases": list_deleted(
            db, models.EntityAlias, models.EntityAlias.entity_id.in_(entity_ids or [-1])
        ),
        "relations": list_deleted(db, models.EntityRelation, models.EntityRelation.project_id == project_id),
        "state_changes": list_deleted(
            db,
            models.EntityStateChange,
            models.EntityStateChange.entity_id.in_(entity_ids or [-1]),
        ),
        "timeline": list_deleted(db, models.TimelineEvent, models.TimelineEvent.project_id == project_id),
        "foreshadows": list_deleted(db, models.Foreshadow, models.Foreshadow.project_id == project_id),
        "style_guides": list_deleted(db, models.StyleGuide, models.StyleGuide.project_id == project_id),
    }
    return {
        resource: [
            {
                "id": item.id,
                "revision": item.revision,
                "deleted_at": item.deleted_at.isoformat() if item.deleted_at else None,
                "label": str(
                    getattr(
                        item,
                        "title",
                        getattr(item, "name", getattr(item, "label", getattr(item, "setup_text", item.id))),
                    )
                ),
            }
            for item in items
        ]
        for resource, items in records.items()
    }


def delete_record(db: Session, resource: str, item_id: int, expected_revision: int) -> None:
    model = RESOURCE_MODELS.get(resource)
    if model is None:
        raise HTTPException(status_code=400, detail="Unsupported resource")
    item = get_or_404(db, model, item_id)
    require_revision(item, expected_revision)
    soft_delete(item)
    db.flush()


def restore_record(db: Session, resource: str, item_id: int, expected_revision: int) -> None:
    model = RESOURCE_MODELS.get(resource)
    if model is None:
        raise HTTPException(status_code=400, detail="Unsupported resource")
    item = get_including_deleted_or_404(db, model, item_id)
    if item.deleted_at is None:
        raise HTTPException(status_code=409, detail="Record is not deleted")
    require_revision(item, expected_revision)
    restore(item)
    db.flush()


def _snapshot_chapter(db: Session, chapter: models.Chapter, source: str) -> None:
    db.add(
        models.ChapterVersion(
            chapter_id=chapter.id,
            title=chapter.title,
            content=chapter.content,
            word_count=chapter.word_count,
            source=source,
        )
    )


def _apply_revision_update(item: Any, data: dict[str, Any]) -> None:
    expected_revision = int(data.pop("expected_revision"))
    require_revision(item, expected_revision)
    for key, value in data.items():
        setattr(item, key, value)
    item.revision += 1


def _project_entity_ids(db: Session, project_id: int) -> list[int]:
    get_or_404(db, models.Project, project_id)
    stmt = select(models.StoryEntity.id).where(models.StoryEntity.project_id == project_id)
    return list(db.scalars(stmt).all())


def _require_project_entities(db: Session, project_id: int, entity_ids: Iterable[int]) -> None:
    expected = set(entity_ids)
    if not expected:
        return
    stmt = select(models.StoryEntity.id).where(
        models.StoryEntity.project_id == project_id,
        models.StoryEntity.deleted_at.is_(None),
        models.StoryEntity.id.in_(expected),
    )
    actual = set(db.scalars(stmt).all())
    if actual != expected:
        raise HTTPException(status_code=400, detail="Entity does not belong to this project")


def _require_project_chapter(db: Session, project_id: int, chapter_id: int | None) -> None:
    if chapter_id is None:
        return
    stmt = (
        select(models.Chapter.id)
        .join(models.Volume, models.Chapter.volume_id == models.Volume.id)
        .where(
            models.Chapter.id == chapter_id,
            models.Chapter.deleted_at.is_(None),
            models.Volume.project_id == project_id,
            models.Volume.deleted_at.is_(None),
        )
    )
    if db.scalar(stmt) is None:
        raise HTTPException(status_code=400, detail="Chapter does not belong to this project")
