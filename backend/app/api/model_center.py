from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import (
    ModelDebugRequest,
    ModelProfileCreate,
    ModelProfileRead,
    ModelProfileUpdate,
    ModelSyncRead,
    NormalizedModelResponse,
    ProviderAccountCreate,
    ProviderAccountRead,
    ProviderAccountUpdate,
    ProviderConnectionRead,
    ProviderPresetCreate,
    ProviderPresetRead,
    ProviderPresetUpdate,
)
from app.services import model_execution
from app.services import models as model_service
from app.services.gateway_http import ProviderRequestError

router = APIRouter(prefix="/model-center", tags=["model-center"])


@router.get("/presets", response_model=list[ProviderPresetRead])
def list_presets(db: Session = Depends(get_db)) -> list[Any]:
    return model_service.list_presets(db)


@router.post(
    "/presets", response_model=ProviderPresetRead, status_code=status.HTTP_201_CREATED
)
def create_preset(payload: ProviderPresetCreate, db: Session = Depends(get_db)) -> Any:
    with db.begin():
        return model_service.create_preset(db, payload)


@router.put("/presets/{preset_id}", response_model=ProviderPresetRead)
def update_preset(
    preset_id: int, payload: ProviderPresetUpdate, db: Session = Depends(get_db)
) -> Any:
    with db.begin():
        return model_service.update_preset(db, preset_id, payload)


@router.get("/providers", response_model=list[ProviderAccountRead])
def list_providers(db: Session = Depends(get_db)) -> list[Any]:
    return model_service.list_providers(db)


@router.post(
    "/providers", response_model=ProviderAccountRead, status_code=status.HTTP_201_CREATED
)
def create_provider(payload: ProviderAccountCreate, db: Session = Depends(get_db)) -> Any:
    with db.begin():
        return model_service.create_provider(db, payload)


@router.put("/providers/{provider_id}", response_model=ProviderAccountRead)
def update_provider(
    provider_id: int,
    payload: ProviderAccountUpdate,
    db: Session = Depends(get_db),
) -> Any:
    with db.begin():
        return model_service.update_provider(db, provider_id, payload)


@router.delete("/providers/{provider_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_provider(
    provider_id: int,
    expected_revision: int = Query(..., ge=1),
    db: Session = Depends(get_db),
) -> Response:
    with db.begin():
        model_service.delete_provider(db, provider_id, expected_revision)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/providers/{provider_id}/test", response_model=ProviderConnectionRead)
async def test_provider(
    provider_id: int, db: Session = Depends(get_db)
) -> ProviderConnectionRead:
    with db.begin():
        return await model_service.test_provider_connection(db, provider_id)


@router.post("/providers/{provider_id}/sync", response_model=ModelSyncRead)
async def sync_models(provider_id: int, db: Session = Depends(get_db)) -> ModelSyncRead:
    try:
        with db.begin():
            return await model_service.sync_provider_models(db, provider_id)
    except ProviderRequestError as exc:
        raise HTTPException(status_code=502, detail=exc.error.model_dump()) from exc


@router.get("/models", response_model=list[ModelProfileRead])
def list_models(
    provider_account_id: int | None = Query(None, ge=1),
    db: Session = Depends(get_db),
) -> list[Any]:
    return model_service.list_model_profiles(db, provider_account_id)


@router.post("/models", response_model=ModelProfileRead, status_code=status.HTTP_201_CREATED)
def create_model(payload: ModelProfileCreate, db: Session = Depends(get_db)) -> Any:
    with db.begin():
        return model_service.create_model_profile(db, payload)


@router.put("/models/{model_id}", response_model=ModelProfileRead)
def update_model(
    model_id: int, payload: ModelProfileUpdate, db: Session = Depends(get_db)
) -> Any:
    with db.begin():
        return model_service.update_model_profile(db, model_id, payload)


@router.delete("/models/{model_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_model(
    model_id: int,
    expected_revision: int = Query(..., ge=1),
    db: Session = Depends(get_db),
) -> Response:
    with db.begin():
        model_service.delete_model_profile(db, model_id, expected_revision)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/debug", response_model=NormalizedModelResponse)
async def debug_model(
    payload: ModelDebugRequest, db: Session = Depends(get_db)
) -> NormalizedModelResponse:
    return await model_execution.execute_model(db, payload)


@router.post("/debug/stream")
async def stream_model(
    payload: ModelDebugRequest, db: Session = Depends(get_db)
) -> StreamingResponse:
    async def event_source() -> AsyncIterator[str]:
        async for event in model_execution.stream_model(db, payload):
            yield f"id: {event.sequence}\nevent: {event.event}\ndata: {event.model_dump_json()}\n\n"

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
