from typing import Any

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import (
    ChapterAutosave,
    ChapterCreate,
    ChapterRead,
    ChapterVersionRead,
    EntityAliasCreate,
    EntityAliasRead,
    EntityAliasUpdate,
    EntityRelationCreate,
    EntityRelationRead,
    EntityRelationUpdate,
    EntityStateChangeCreate,
    EntityStateChangeRead,
    EntityStateChangeUpdate,
    ForeshadowCreate,
    ForeshadowRead,
    ForeshadowUpdate,
    ProjectCreate,
    ProjectRead,
    ProjectTreeRead,
    ProjectUpdate,
    ReorderItemRead,
    ReorderRequest,
    SceneCreate,
    SceneRead,
    SceneUpdate,
    StoryEntityCreate,
    StoryEntityRead,
    StoryEntityUpdate,
    StyleGuideCreate,
    StyleGuideRead,
    StyleGuideUpdate,
    TimelineEventCreate,
    TimelineEventRead,
    TimelineEventUpdate,
    VolumeCreate,
    VolumeRead,
    VolumeUpdate,
)
from app.services import novels

router = APIRouter(prefix="/projects", tags=["projects"])


@router.get("", response_model=list[ProjectRead])
def list_projects(
    deleted: bool = Query(False), db: Session = Depends(get_db)
) -> list[Any]:
    return novels.list_projects(db, deleted=deleted)


@router.post("", response_model=ProjectRead, status_code=status.HTTP_201_CREATED)
def create_project(payload: ProjectCreate, db: Session = Depends(get_db)) -> Any:
    with db.begin():
        return novels.create_project(db, payload)


@router.put("/{project_id}", response_model=ProjectRead)
def update_project(project_id: int, payload: ProjectUpdate, db: Session = Depends(get_db)) -> Any:
    with db.begin():
        return novels.update_project(db, project_id, payload)


@router.get("/{project_id}/tree", response_model=ProjectTreeRead)
def read_project_tree(project_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    return novels.project_tree(db, project_id)


@router.post("/{project_id}/volumes", response_model=VolumeRead, status_code=status.HTTP_201_CREATED)
def create_volume(project_id: int, payload: VolumeCreate, db: Session = Depends(get_db)) -> Any:
    with db.begin():
        return novels.create_volume(db, project_id, payload)


@router.put("/volumes/{volume_id}", response_model=VolumeRead)
def update_volume(volume_id: int, payload: VolumeUpdate, db: Session = Depends(get_db)) -> Any:
    with db.begin():
        return novels.update_volume(db, volume_id, payload)


@router.post(
    "/volumes/{volume_id}/chapters",
    response_model=ChapterRead,
    status_code=status.HTTP_201_CREATED,
)
def create_chapter(volume_id: int, payload: ChapterCreate, db: Session = Depends(get_db)) -> Any:
    with db.begin():
        return novels.create_chapter(db, volume_id, payload)


@router.put("/chapters/{chapter_id}/autosave", response_model=ChapterRead)
def autosave_chapter(
    chapter_id: int, payload: ChapterAutosave, db: Session = Depends(get_db)
) -> Any:
    with db.begin():
        return novels.autosave_chapter(db, chapter_id, payload)


@router.get("/chapters/{chapter_id}/versions", response_model=list[ChapterVersionRead])
def list_versions(chapter_id: int, db: Session = Depends(get_db)) -> list[Any]:
    return novels.list_chapter_versions(db, chapter_id)


@router.post("/chapters/{chapter_id}/versions/{version_id}/restore", response_model=ChapterRead)
def restore_version(
    chapter_id: int,
    version_id: int,
    expected_revision: int | None = Query(None, ge=1),
    db: Session = Depends(get_db),
) -> Any:
    with db.begin():
        return novels.restore_chapter_version(db, chapter_id, version_id, expected_revision)


@router.post(
    "/chapters/{chapter_id}/scenes",
    response_model=SceneRead,
    status_code=status.HTTP_201_CREATED,
)
def create_scene(chapter_id: int, payload: SceneCreate, db: Session = Depends(get_db)) -> Any:
    with db.begin():
        return novels.create_scene(db, chapter_id, payload)


@router.put("/scenes/{scene_id}", response_model=SceneRead)
def update_scene(scene_id: int, payload: SceneUpdate, db: Session = Depends(get_db)) -> Any:
    with db.begin():
        return novels.update_scene(db, scene_id, payload)


@router.post("/reorder/{resource}", response_model=list[ReorderItemRead])
def reorder(resource: str, payload: ReorderRequest, db: Session = Depends(get_db)) -> list[Any]:
    with db.begin():
        return novels.reorder_records(db, resource, payload)


@router.post(
    "/{project_id}/entities",
    response_model=StoryEntityRead,
    status_code=status.HTTP_201_CREATED,
)
def create_entity(
    project_id: int, payload: StoryEntityCreate, db: Session = Depends(get_db)
) -> Any:
    with db.begin():
        return novels.create_entity(db, project_id, payload)


@router.get("/{project_id}/entities", response_model=list[StoryEntityRead])
def list_entities(
    project_id: int, deleted: bool = Query(False), db: Session = Depends(get_db)
) -> list[Any]:
    return novels.list_entities(db, project_id, deleted=deleted)


@router.put("/entities/{entity_id}", response_model=StoryEntityRead)
def update_entity(
    entity_id: int, payload: StoryEntityUpdate, db: Session = Depends(get_db)
) -> Any:
    with db.begin():
        return novels.update_entity(db, entity_id, payload)


@router.post(
    "/entities/{entity_id}/aliases",
    response_model=EntityAliasRead,
    status_code=status.HTTP_201_CREATED,
)
def create_alias(
    entity_id: int, payload: EntityAliasCreate, db: Session = Depends(get_db)
) -> Any:
    with db.begin():
        return novels.create_alias(db, entity_id, payload)


@router.get("/{project_id}/aliases", response_model=list[EntityAliasRead])
def list_aliases(project_id: int, db: Session = Depends(get_db)) -> list[Any]:
    return novels.list_aliases(db, project_id)


@router.put("/aliases/{alias_id}", response_model=EntityAliasRead)
def update_alias(alias_id: int, payload: EntityAliasUpdate, db: Session = Depends(get_db)) -> Any:
    with db.begin():
        return novels.update_alias(db, alias_id, payload)


@router.post(
    "/{project_id}/relations",
    response_model=EntityRelationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_relation(
    project_id: int, payload: EntityRelationCreate, db: Session = Depends(get_db)
) -> Any:
    with db.begin():
        return novels.create_relation(db, project_id, payload)


@router.get("/{project_id}/relations", response_model=list[EntityRelationRead])
def list_relations(project_id: int, db: Session = Depends(get_db)) -> list[Any]:
    return novels.list_relations(db, project_id)


@router.put("/relations/{relation_id}", response_model=EntityRelationRead)
def update_relation(
    relation_id: int, payload: EntityRelationUpdate, db: Session = Depends(get_db)
) -> Any:
    with db.begin():
        return novels.update_relation(db, relation_id, payload)


@router.post(
    "/{project_id}/state-changes",
    response_model=EntityStateChangeRead,
    status_code=status.HTTP_201_CREATED,
)
def create_state_change(
    project_id: int, payload: EntityStateChangeCreate, db: Session = Depends(get_db)
) -> Any:
    with db.begin():
        return novels.create_state_change(db, project_id, payload)


@router.get("/{project_id}/state-changes", response_model=list[EntityStateChangeRead])
def list_state_changes(project_id: int, db: Session = Depends(get_db)) -> list[Any]:
    return novels.list_state_changes(db, project_id)


@router.put("/state-changes/{state_change_id}", response_model=EntityStateChangeRead)
def update_state_change(
    state_change_id: int,
    payload: EntityStateChangeUpdate,
    db: Session = Depends(get_db),
) -> Any:
    with db.begin():
        return novels.update_state_change(db, state_change_id, payload)


@router.post(
    "/{project_id}/timeline",
    response_model=TimelineEventRead,
    status_code=status.HTTP_201_CREATED,
)
def create_timeline_event(
    project_id: int, payload: TimelineEventCreate, db: Session = Depends(get_db)
) -> Any:
    with db.begin():
        return novels.create_timeline_event(db, project_id, payload)


@router.get("/{project_id}/timeline", response_model=list[TimelineEventRead])
def list_timeline_events(project_id: int, db: Session = Depends(get_db)) -> list[Any]:
    return novels.list_timeline_events(db, project_id)


@router.put("/timeline/{event_id}", response_model=TimelineEventRead)
def update_timeline_event(
    event_id: int, payload: TimelineEventUpdate, db: Session = Depends(get_db)
) -> Any:
    with db.begin():
        return novels.update_timeline_event(db, event_id, payload)


@router.post(
    "/{project_id}/foreshadows",
    response_model=ForeshadowRead,
    status_code=status.HTTP_201_CREATED,
)
def create_foreshadow(
    project_id: int, payload: ForeshadowCreate, db: Session = Depends(get_db)
) -> Any:
    with db.begin():
        return novels.create_foreshadow(db, project_id, payload)


@router.get("/{project_id}/foreshadows", response_model=list[ForeshadowRead])
def list_foreshadows(project_id: int, db: Session = Depends(get_db)) -> list[Any]:
    return novels.list_foreshadows(db, project_id)


@router.put("/foreshadows/{foreshadow_id}", response_model=ForeshadowRead)
def update_foreshadow(
    foreshadow_id: int, payload: ForeshadowUpdate, db: Session = Depends(get_db)
) -> Any:
    with db.begin():
        return novels.update_foreshadow(db, foreshadow_id, payload)


@router.post(
    "/{project_id}/style-guides",
    response_model=StyleGuideRead,
    status_code=status.HTTP_201_CREATED,
)
def create_style_guide(
    project_id: int, payload: StyleGuideCreate, db: Session = Depends(get_db)
) -> Any:
    with db.begin():
        return novels.create_style_guide(db, project_id, payload)


@router.get("/{project_id}/style-guides", response_model=list[StyleGuideRead])
def list_style_guides(project_id: int, db: Session = Depends(get_db)) -> list[Any]:
    return novels.list_style_guides(db, project_id)


@router.put("/style-guides/{style_guide_id}", response_model=StyleGuideRead)
def update_style_guide(
    style_guide_id: int, payload: StyleGuideUpdate, db: Session = Depends(get_db)
) -> Any:
    with db.begin():
        return novels.update_style_guide(db, style_guide_id, payload)


@router.get("/{project_id}/trash")
def project_trash(project_id: int, db: Session = Depends(get_db)) -> dict[str, list[Any]]:
    return novels.project_trash(db, project_id)


@router.delete("/records/{resource}/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def soft_delete_record(
    resource: str,
    item_id: int,
    expected_revision: int = Query(..., ge=1),
    db: Session = Depends(get_db),
) -> Response:
    with db.begin():
        novels.delete_record(db, resource, item_id, expected_revision)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/records/{resource}/{item_id}/restore", status_code=status.HTTP_204_NO_CONTENT)
def restore_record(
    resource: str,
    item_id: int,
    expected_revision: int = Query(..., ge=1),
    db: Session = Depends(get_db),
) -> Response:
    with db.begin():
        novels.restore_record(db, resource, item_id, expected_revision)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
