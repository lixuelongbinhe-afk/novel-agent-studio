from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.context import (
    ChapterEntityLinkCreate,
    ChapterEntityLinkRead,
    ChapterEntityLinkUpdate,
    ChapterSummaryCreate,
    ChapterSummaryRead,
    ChapterSummaryUpdate,
    ContentClassificationCreate,
    ContentClassificationRead,
    ContentClassificationUpdate,
    ContextBuildRead,
    ContextBuildRequest,
    ContextFtsStatusRead,
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
from app.services import context_builder, context_memory
from app.services.context_retrieval import rebuild_fts_index


router = APIRouter(prefix="/context", tags=["context"])


@router.get("/chapter-summaries", response_model=list[ChapterSummaryRead])
def list_chapter_summaries(
    project_id: int = Query(..., ge=1), db: Session = Depends(get_db)
) -> list[ChapterSummaryRead]:
    return context_memory.list_chapter_summaries(db, project_id)


@router.post(
    "/chapter-summaries",
    response_model=ChapterSummaryRead,
    status_code=status.HTTP_201_CREATED,
)
def create_chapter_summary(
    payload: ChapterSummaryCreate, db: Session = Depends(get_db)
) -> ChapterSummaryRead:
    with db.begin():
        return context_memory.create_chapter_summary(db, payload)


@router.put("/chapter-summaries/{summary_id}", response_model=ChapterSummaryRead)
def update_chapter_summary(
    summary_id: int,
    payload: ChapterSummaryUpdate,
    db: Session = Depends(get_db),
) -> ChapterSummaryRead:
    with db.begin():
        return context_memory.update_chapter_summary(db, summary_id, payload)


@router.get("/scene-states", response_model=list[SceneStateRead])
def list_scene_states(
    project_id: int = Query(..., ge=1), db: Session = Depends(get_db)
) -> list[SceneStateRead]:
    return context_memory.list_scene_states(db, project_id)


@router.post(
    "/scene-states",
    response_model=SceneStateRead,
    status_code=status.HTTP_201_CREATED,
)
def create_scene_state(
    payload: SceneStateCreate, db: Session = Depends(get_db)
) -> SceneStateRead:
    with db.begin():
        return context_memory.create_scene_state(db, payload)


@router.put("/scene-states/{state_id}", response_model=SceneStateRead)
def update_scene_state(
    state_id: int,
    payload: SceneStateUpdate,
    db: Session = Depends(get_db),
) -> SceneStateRead:
    with db.begin():
        return context_memory.update_scene_state(db, state_id, payload)


@router.get("/chapter-entity-links", response_model=list[ChapterEntityLinkRead])
def list_chapter_entity_links(
    project_id: int = Query(..., ge=1), db: Session = Depends(get_db)
) -> list[ChapterEntityLinkRead]:
    return context_memory.list_chapter_entity_links(db, project_id)


@router.post(
    "/chapter-entity-links",
    response_model=ChapterEntityLinkRead,
    status_code=status.HTTP_201_CREATED,
)
def create_chapter_entity_link(
    payload: ChapterEntityLinkCreate, db: Session = Depends(get_db)
) -> ChapterEntityLinkRead:
    with db.begin():
        return context_memory.create_chapter_entity_link(db, payload)


@router.put("/chapter-entity-links/{link_id}", response_model=ChapterEntityLinkRead)
def update_chapter_entity_link(
    link_id: int,
    payload: ChapterEntityLinkUpdate,
    db: Session = Depends(get_db),
) -> ChapterEntityLinkRead:
    with db.begin():
        return context_memory.update_chapter_entity_link(db, link_id, payload)


@router.get("/pins", response_model=list[ContextPinRead])
def list_context_pins(
    project_id: int = Query(..., ge=1), db: Session = Depends(get_db)
) -> list[ContextPinRead]:
    return context_memory.list_context_pins(db, project_id)


@router.post(
    "/pins", response_model=ContextPinRead, status_code=status.HTTP_201_CREATED
)
def create_context_pin(
    payload: ContextPinCreate, db: Session = Depends(get_db)
) -> ContextPinRead:
    with db.begin():
        return context_memory.create_context_pin(db, payload)


@router.put("/pins/{pin_id}", response_model=ContextPinRead)
def update_context_pin(
    pin_id: int, payload: ContextPinUpdate, db: Session = Depends(get_db)
) -> ContextPinRead:
    with db.begin():
        return context_memory.update_context_pin(db, pin_id, payload)


@router.get("/classifications", response_model=list[ContentClassificationRead])
def list_classifications(
    project_id: int = Query(..., ge=1), db: Session = Depends(get_db)
) -> list[ContentClassificationRead]:
    return context_memory.list_classifications(db, project_id)


@router.post(
    "/classifications",
    response_model=ContentClassificationRead,
    status_code=status.HTTP_201_CREATED,
)
def create_classification(
    payload: ContentClassificationCreate, db: Session = Depends(get_db)
) -> ContentClassificationRead:
    with db.begin():
        return context_memory.create_classification(db, payload)


@router.put(
    "/classifications/{classification_id}",
    response_model=ContentClassificationRead,
)
def update_classification(
    classification_id: int,
    payload: ContentClassificationUpdate,
    db: Session = Depends(get_db),
) -> ContentClassificationRead:
    with db.begin():
        return context_memory.update_classification(db, classification_id, payload)


@router.get("/policies", response_model=list[ContextPolicyRead])
def list_context_policies(
    project_id: int = Query(..., ge=1), db: Session = Depends(get_db)
) -> list[ContextPolicyRead]:
    with db.begin():
        return context_memory.list_context_policies(db, project_id)


@router.post(
    "/policies",
    response_model=ContextPolicyRead,
    status_code=status.HTTP_201_CREATED,
)
def create_context_policy(
    payload: ContextPolicyCreate, db: Session = Depends(get_db)
) -> ContextPolicyRead:
    with db.begin():
        return context_memory.create_context_policy(db, payload)


@router.put("/policies/{policy_id}", response_model=ContextPolicyRead)
def update_context_policy(
    policy_id: int,
    payload: ContextPolicyUpdate,
    db: Session = Depends(get_db),
) -> ContextPolicyRead:
    with db.begin():
        return context_memory.update_context_policy(db, policy_id, payload)


@router.get("/provider-policies", response_model=list[ProviderDataPolicyRead])
def list_provider_data_policies(
    db: Session = Depends(get_db),
) -> list[ProviderDataPolicyRead]:
    with db.begin():
        return context_memory.list_provider_data_policies(db)


@router.put(
    "/provider-policies/{provider_id}", response_model=ProviderDataPolicyRead
)
def update_provider_data_policy(
    provider_id: int,
    payload: ProviderDataPolicyUpdate,
    db: Session = Depends(get_db),
) -> ProviderDataPolicyRead:
    with db.begin():
        return context_memory.update_provider_data_policy(db, provider_id, payload)


@router.delete("/records/{resource}/{record_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_context_record(
    resource: str,
    record_id: int,
    expected_revision: int = Query(..., ge=1),
    db: Session = Depends(get_db),
) -> Response:
    with db.begin():
        context_memory.delete_record(db, resource, record_id, expected_revision)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/builds", response_model=ContextBuildRead)
def create_context_build(
    payload: ContextBuildRequest, db: Session = Depends(get_db)
) -> ContextBuildRead:
    with db.begin():
        return context_builder.build_context(db, payload)


@router.get("/builds", response_model=list[ContextBuildRead])
def list_context_builds(
    project_id: int = Query(..., ge=1),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[ContextBuildRead]:
    return context_builder.list_context_builds(db, project_id, limit=limit)


@router.get("/builds/{build_id}", response_model=ContextBuildRead)
def read_context_build(
    build_id: int, db: Session = Depends(get_db)
) -> ContextBuildRead:
    return context_builder.read_context_build(db, build_id)


@router.post("/reindex/{project_id}", response_model=ContextFtsStatusRead)
def reindex_context(
    project_id: int, db: Session = Depends(get_db)
) -> ContextFtsStatusRead:
    with db.begin():
        count = rebuild_fts_index(db, project_id)
    return ContextFtsStatusRead(project_id=project_id, indexed_records=count, rebuilt=True)
