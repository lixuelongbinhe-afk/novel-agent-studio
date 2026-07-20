from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas import (
    CredentialReferenceCreate,
    CredentialReferenceRead,
    CredentialReferenceUpdate,
    GenericAdapterManifest,
    GenericAdapterTestRead,
    GenericAdapterTestRequest,
    GenericHttpAdapterCreate,
    GenericHttpAdapterRead,
    GenericHttpAdapterSetupCreate,
    GenericHttpAdapterUpdate,
    ManifestImportRead,
    NormalizedModelRequest,
    OriginApprovalRequest,
)
from app.services import custom_adapters, model_gateway


router = APIRouter(prefix="/custom-api", tags=["custom-api"])


@router.get("/credentials", response_model=list[CredentialReferenceRead])
def list_credentials(db: Session = Depends(get_db)) -> list[Any]:
    return custom_adapters.list_credentials(db)


@router.post(
    "/credentials",
    response_model=CredentialReferenceRead,
    status_code=status.HTTP_201_CREATED,
)
def create_credential(
    payload: CredentialReferenceCreate, db: Session = Depends(get_db)
) -> Any:
    with db.begin():
        return custom_adapters.create_credential(db, payload)


@router.put("/credentials/{credential_id}", response_model=CredentialReferenceRead)
def update_credential(
    credential_id: int,
    payload: CredentialReferenceUpdate,
    db: Session = Depends(get_db),
) -> Any:
    with db.begin():
        return custom_adapters.update_credential(db, credential_id, payload)


@router.delete("/credentials/{credential_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_credential(
    credential_id: int,
    expected_revision: int = Query(..., ge=1),
    db: Session = Depends(get_db),
) -> Response:
    with db.begin():
        custom_adapters.delete_credential(db, credential_id, expected_revision)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/adapters", response_model=list[GenericHttpAdapterRead])
def list_adapters(db: Session = Depends(get_db)) -> list[GenericHttpAdapterRead]:
    return custom_adapters.list_configs(db)


@router.post(
    "/adapters",
    response_model=GenericHttpAdapterRead,
    status_code=status.HTTP_201_CREATED,
)
def create_adapter(
    payload: GenericHttpAdapterCreate, db: Session = Depends(get_db)
) -> GenericHttpAdapterRead:
    with db.begin():
        return custom_adapters.create_config(db, payload)


@router.post(
    "/adapters/setup",
    response_model=GenericHttpAdapterRead,
    status_code=status.HTTP_201_CREATED,
)
def setup_adapter(
    payload: GenericHttpAdapterSetupCreate, db: Session = Depends(get_db)
) -> GenericHttpAdapterRead:
    with db.begin():
        return custom_adapters.create_config_with_provider(db, payload)


@router.put("/adapters/{adapter_id}", response_model=GenericHttpAdapterRead)
def update_adapter(
    adapter_id: int,
    payload: GenericHttpAdapterUpdate,
    db: Session = Depends(get_db),
) -> GenericHttpAdapterRead:
    with db.begin():
        return custom_adapters.update_config(db, adapter_id, payload)


@router.delete("/adapters/{adapter_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_adapter(
    adapter_id: int,
    expected_revision: int = Query(..., ge=1),
    db: Session = Depends(get_db),
) -> Response:
    with db.begin():
        custom_adapters.delete_config(db, adapter_id, expected_revision)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/adapters/{adapter_id}/approve-origin",
    response_model=GenericHttpAdapterRead,
)
async def approve_origin(
    adapter_id: int,
    payload: OriginApprovalRequest,
    db: Session = Depends(get_db),
) -> GenericHttpAdapterRead:
    with db.begin():
        return await custom_adapters.approve_origin(
            db, adapter_id, payload.expected_revision
        )


@router.post(
    "/adapters/{adapter_id}/test",
    response_model=GenericAdapterTestRead,
)
async def test_adapter(
    adapter_id: int,
    payload: GenericAdapterTestRequest,
    db: Session = Depends(get_db),
) -> GenericAdapterTestRead:
    request = NormalizedModelRequest.model_validate(payload.request)
    with db.begin():
        return await custom_adapters.test_config(db, adapter_id, request)


@router.post("/adapters/{adapter_id}/debug/stream")
async def stream_adapter(
    adapter_id: int,
    payload: GenericAdapterTestRequest,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    request = NormalizedModelRequest.model_validate(payload.request)
    config = custom_adapters.get_config(db, adapter_id)
    from app import models
    from app.repositories import get_or_404

    provider = get_or_404(db, models.ProviderAccount, config.provider_account_id)
    runtime = custom_adapters.runtime_for_config(
        db, provider, config, require_enabled=False
    )
    adapter = model_gateway.registry.get("generic_json_http")

    async def event_source() -> AsyncIterator[str]:
        async for event in adapter.stream(request, runtime):
            yield (
                f"id: {event.sequence}\nevent: {event.event}\n"
                f"data: {event.model_dump_json()}\n\n"
            )

    return StreamingResponse(
        event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get(
    "/adapters/{adapter_id}/manifest", response_model=GenericAdapterManifest
)
def export_manifest(
    adapter_id: int, db: Session = Depends(get_db)
) -> GenericAdapterManifest:
    return custom_adapters.export_manifest(db, adapter_id)


@router.post(
    "/manifests/import",
    response_model=ManifestImportRead,
    status_code=status.HTTP_201_CREATED,
)
def import_manifest(
    manifest: GenericAdapterManifest, db: Session = Depends(get_db)
) -> ManifestImportRead:
    with db.begin():
        return custom_adapters.import_manifest(db, manifest)
