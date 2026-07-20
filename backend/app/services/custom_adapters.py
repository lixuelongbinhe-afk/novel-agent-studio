from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from typing import Any, cast

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.repositories import get_or_404, require_revision, soft_delete
from app.schemas import (
    CredentialReferenceCreate,
    CredentialReferenceUpdate,
    GenericAdapterManifest,
    GenericAdapterTestRead,
    GenericHttpAdapterCreate,
    GenericHttpAdapterRead,
    GenericHttpAdapterSetupCreate,
    GenericHttpAdapterUpdate,
    ManifestImportRead,
    NormalizedModelRequest,
    ProviderAccountCreate,
)
from app.services import model_gateway
from app.services.gateway_http import ProviderRequestError, ProviderRuntime
from app.services.safe_json import find_secret_material
from app.services.ssrf import TargetGuard, TargetSecurityError, canonical_origin


JSON_FIELDS = {
    "query": "query_json",
    "headers": "headers_json",
    "request_template": "request_template_json",
    "parameter_mapping": "parameter_mapping_json",
    "response_mapping": "response_mapping_json",
    "stream_mapping": "stream_mapping_json",
    "error_mapping": "error_mapping_json",
    "auth": "auth_json",
    "capability_defaults": "capability_defaults_json",
}
SCALAR_FIELDS = (
    "credential_reference_id",
    "method",
    "endpoint",
    "content_type",
    "response_mode",
    "stream_format",
    "security_mode",
    "enabled",
)


def list_credentials(db: Session) -> list[models.CredentialReference]:
    return list(
        db.scalars(
            select(models.CredentialReference)
            .where(models.CredentialReference.deleted_at.is_(None))
            .order_by(models.CredentialReference.id)
        ).all()
    )


def create_credential(
    db: Session, payload: CredentialReferenceCreate
) -> models.CredentialReference:
    duplicate = db.scalar(
        select(models.CredentialReference).where(
            models.CredentialReference.name == payload.name,
            models.CredentialReference.deleted_at.is_(None),
        )
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="Credential reference name already exists")
    item = models.CredentialReference(**payload.model_dump())
    db.add(item)
    db.flush()
    return item


def update_credential(
    db: Session, credential_id: int, payload: CredentialReferenceUpdate
) -> models.CredentialReference:
    item = cast(
        models.CredentialReference,
        get_or_404(db, models.CredentialReference, credential_id),
    )
    require_revision(item, payload.expected_revision)
    item.name = payload.name
    item.env_var_name = payload.env_var_name
    item.revision += 1
    for config in db.scalars(
        select(models.GenericHttpAdapterConfiguration).where(
            models.GenericHttpAdapterConfiguration.credential_reference_id == item.id,
            models.GenericHttpAdapterConfiguration.deleted_at.is_(None),
        )
    ):
        _revoke_security_state(config)
    db.flush()
    return item


def delete_credential(db: Session, credential_id: int, expected_revision: int) -> None:
    item = cast(
        models.CredentialReference,
        get_or_404(db, models.CredentialReference, credential_id),
    )
    require_revision(item, expected_revision)
    in_use = db.scalar(
        select(models.GenericHttpAdapterConfiguration.id).where(
            models.GenericHttpAdapterConfiguration.credential_reference_id == item.id,
            models.GenericHttpAdapterConfiguration.deleted_at.is_(None),
        )
    )
    if in_use is not None:
        raise HTTPException(status_code=409, detail="Credential reference is bound to an adapter")
    soft_delete(item)
    db.flush()


def list_configs(db: Session) -> list[GenericHttpAdapterRead]:
    rows = db.scalars(
        select(models.GenericHttpAdapterConfiguration)
        .where(models.GenericHttpAdapterConfiguration.deleted_at.is_(None))
        .order_by(models.GenericHttpAdapterConfiguration.id)
    ).all()
    return [config_read(db, row) for row in rows]


def get_config(
    db: Session, config_id: int
) -> models.GenericHttpAdapterConfiguration:
    return cast(
        models.GenericHttpAdapterConfiguration,
        get_or_404(db, models.GenericHttpAdapterConfiguration, config_id),
    )


def create_config(
    db: Session, payload: GenericHttpAdapterCreate
) -> GenericHttpAdapterRead:
    provider = cast(
        models.ProviderAccount,
        get_or_404(db, models.ProviderAccount, payload.provider_account_id),
    )
    if provider.provider_type != "generic_json_http":
        raise HTTPException(status_code=422, detail="Provider protocol must be generic_json_http")
    duplicate = db.scalar(
        select(models.GenericHttpAdapterConfiguration).where(
            models.GenericHttpAdapterConfiguration.provider_account_id == provider.id,
            models.GenericHttpAdapterConfiguration.deleted_at.is_(None),
        )
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="Provider already has a custom adapter")
    _validate_credential_reference(db, payload.credential_reference_id)
    data = payload.model_dump()
    config = models.GenericHttpAdapterConfiguration(
        provider_account_id=payload.provider_account_id,
        enabled=False,
    )
    _apply_fields(config, data, allow_enable=False)
    db.add(config)
    db.flush()
    return config_read(db, config)


def create_config_with_provider(
    db: Session, payload: GenericHttpAdapterSetupCreate
) -> GenericHttpAdapterRead:
    from app.services import models as model_service

    provider = model_service.create_provider(
        db,
        ProviderAccountCreate(
            name=payload.provider_name,
            provider_type="generic_json_http",
            credential_env_var=None,
            base_url=payload.base_url,
            enabled=True,
        ),
    )
    config_data = payload.model_dump(exclude={"provider_name", "base_url"})
    return create_config(
        db,
        GenericHttpAdapterCreate(
            provider_account_id=provider.id,
            **config_data,
        ),
    )


def update_config(
    db: Session, config_id: int, payload: GenericHttpAdapterUpdate
) -> GenericHttpAdapterRead:
    config = get_config(db, config_id)
    require_revision(config, payload.expected_revision)
    _validate_credential_reference(db, payload.credential_reference_id)
    provider = cast(
        models.ProviderAccount,
        get_or_404(db, models.ProviderAccount, config.provider_account_id),
    )
    previous_approval = _approval_fingerprint(provider, config)
    previous_test = _test_fingerprint(provider, config)
    _apply_fields(config, payload.model_dump(exclude={"expected_revision"}), allow_enable=False)
    current_approval = _approval_fingerprint(provider, config)
    current_test = _test_fingerprint(provider, config)
    if previous_approval != current_approval:
        config.approved_origin = None
        config.approval_fingerprint = None
    if previous_test != current_test:
        config.tested_fingerprint = None
        config.last_tested_at = None
    if payload.enabled:
        _require_enable_ready(provider, config)
        config.enabled = True
    else:
        config.enabled = False
    config.revision += 1
    db.flush()
    return config_read(db, config)


def delete_config(db: Session, config_id: int, expected_revision: int) -> None:
    config = get_config(db, config_id)
    require_revision(config, expected_revision)
    soft_delete(config)
    db.flush()


async def approve_origin(
    db: Session,
    config_id: int,
    expected_revision: int,
    guard: TargetGuard | None = None,
) -> GenericHttpAdapterRead:
    config = get_config(db, config_id)
    require_revision(config, expected_revision)
    provider = cast(
        models.ProviderAccount,
        get_or_404(db, models.ProviderAccount, config.provider_account_id),
    )
    if config.security_mode != "local_private":
        raise HTTPException(status_code=422, detail="Origin approval is only used in local_private mode")
    try:
        origin, _ = await (guard or TargetGuard()).validate_for_approval(
            join_provider_url(provider, config)
        )
    except TargetSecurityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    config.approved_origin = origin
    config.approval_fingerprint = _approval_fingerprint(provider, config)
    config.tested_fingerprint = None
    config.last_tested_at = None
    config.enabled = False
    config.revision += 1
    db.flush()
    return config_read(db, config)


async def test_config(
    db: Session,
    config_id: int,
    request: NormalizedModelRequest,
) -> GenericAdapterTestRead:
    config = get_config(db, config_id)
    provider = cast(
        models.ProviderAccount,
        get_or_404(db, models.ProviderAccount, config.provider_account_id),
    )
    runtime = runtime_for_config(db, provider, config, require_enabled=False)
    adapter = model_gateway.registry.get("generic_json_http")
    preview: dict[str, Any] = {}
    try:
        if hasattr(adapter, "prepare_request"):
            prepared = await adapter.prepare_request(request, runtime, stream=False)
            preview = prepared.redacted_preview
        response = await adapter.complete(request, runtime)
    except ProviderRequestError as exc:
        return GenericAdapterTestRead(
            ok=False,
            redacted_request=preview,
            response={},
            error=exc.error.model_dump(),
        )
    if response.error is not None:
        return GenericAdapterTestRead(
            ok=False,
            redacted_request=preview,
            response=response.model_dump(mode="json"),
            error=response.error.model_dump(mode="json"),
        )
    config.tested_fingerprint = _test_fingerprint(provider, config)
    config.last_tested_at = datetime.now(timezone.utc)
    config.revision += 1
    db.flush()
    return GenericAdapterTestRead(
        ok=True,
        redacted_request=preview,
        response=response.model_dump(mode="json"),
    )


def runtime_for_provider(db: Session, provider_id: int) -> ProviderRuntime:
    provider = cast(
        models.ProviderAccount, get_or_404(db, models.ProviderAccount, provider_id)
    )
    config = db.scalar(
        select(models.GenericHttpAdapterConfiguration).where(
            models.GenericHttpAdapterConfiguration.provider_account_id == provider.id,
            models.GenericHttpAdapterConfiguration.deleted_at.is_(None),
        )
    )
    if config is None:
        raise HTTPException(status_code=422, detail="Custom adapter configuration is missing")
    return runtime_for_config(db, provider, config, require_enabled=True)


def runtime_for_config(
    db: Session,
    provider: models.ProviderAccount,
    config: models.GenericHttpAdapterConfiguration,
    *,
    require_enabled: bool,
) -> ProviderRuntime:
    if require_enabled and (not provider.enabled or not config.enabled):
        raise ProviderRequestError(
            _provider_error("capability_unsupported", "Custom adapter is disabled")
        )
    credential = None
    if config.credential_reference_id is not None:
        reference = cast(
            models.CredentialReference,
            get_or_404(db, models.CredentialReference, config.credential_reference_id),
        )
        credential = os.getenv(reference.env_var_name)
        if not credential:
            raise ProviderRequestError(
                _provider_error(
                    "authentication",
                    f"Environment variable {reference.env_var_name} is not set",
                )
            )
    return ProviderRuntime(
        protocol="generic_json_http",
        base_url=provider.base_url or "",
        api_key=credential,
        options={"generic_config": _config_dict(config)},
        provider_account_id=provider.id,
    )


def export_manifest(db: Session, config_id: int) -> GenericAdapterManifest:
    config = get_config(db, config_id)
    provider = cast(
        models.ProviderAccount,
        get_or_404(db, models.ProviderAccount, config.provider_account_id),
    )
    fields = _config_dict(config)
    fields["credential_reference_id"] = None
    fields["enabled"] = False
    fields.pop("approved_origin", None)
    manifest = GenericAdapterManifest.model_validate(
        {
            "name": f"{provider.name} adapter",
            "provider_name": provider.name,
            "base_url": provider.base_url or "",
            "config": fields,
        }
    )
    findings = find_secret_material(manifest.model_dump(mode="json"))
    if findings:
        raise HTTPException(status_code=500, detail="Manifest secret scan failed")
    return manifest


def import_manifest(
    db: Session, manifest: GenericAdapterManifest
) -> ManifestImportRead:
    findings = find_secret_material(manifest.model_dump(mode="json"))
    if findings:
        raise HTTPException(
            status_code=422,
            detail={"message": "Manifest appears to contain credentials", "paths": findings},
        )
    try:
        canonical_origin(manifest.base_url)
    except TargetSecurityError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    provider_name = _available_provider_name(db, manifest.provider_name)
    provider = models.ProviderAccount(
        name=provider_name,
        provider_type="generic_json_http",
        base_url=manifest.base_url,
        credential_env_var=None,
        enabled=False,
    )
    db.add(provider)
    db.flush()
    db.add(
        models.ProtocolConfiguration(
            provider_account_id=provider.id,
            protocol="generic_json_http",
            options_json="{}",
        )
    )
    config_data = manifest.config.model_dump()
    config_data["credential_reference_id"] = None
    config_data["enabled"] = False
    config = models.GenericHttpAdapterConfiguration(provider_account_id=provider.id)
    _apply_fields(config, config_data, allow_enable=False)
    db.add(config)
    db.flush()
    return ManifestImportRead(provider_id=provider.id, adapter=config_read(db, config))


def config_read(
    db: Session, config: models.GenericHttpAdapterConfiguration
) -> GenericHttpAdapterRead:
    provider = cast(
        models.ProviderAccount,
        get_or_404(db, models.ProviderAccount, config.provider_account_id),
    )
    credential_name = None
    if config.credential_reference_id is not None:
        credential = db.get(models.CredentialReference, config.credential_reference_id)
        credential_name = credential.name if credential and credential.deleted_at is None else None
    data = _config_dict(config)
    data.update(
        {
            "id": config.id,
            "provider_account_id": config.provider_account_id,
            "credential_reference_name": credential_name,
            "approved_origin": config.approved_origin,
            "approval_current": config.approval_fingerprint
            == _approval_fingerprint(provider, config),
            "test_current": config.tested_fingerprint == _test_fingerprint(provider, config),
            "last_tested_at": config.last_tested_at,
            "revision": config.revision,
            "deleted_at": config.deleted_at,
        }
    )
    return GenericHttpAdapterRead.model_validate(data)


def join_provider_url(
    provider: models.ProviderAccount, config: models.GenericHttpAdapterConfiguration
) -> str:
    if not provider.base_url:
        raise HTTPException(status_code=422, detail="Provider Base URL is required")
    return f"{provider.base_url.rstrip('/')}/{config.endpoint.lstrip('/')}"


def _apply_fields(
    config: models.GenericHttpAdapterConfiguration,
    data: dict[str, Any],
    *,
    allow_enable: bool,
) -> None:
    for field in SCALAR_FIELDS:
        if field == "enabled" and not allow_enable:
            continue
        if field in data:
            setattr(config, field, data[field])
    config.enabled = bool(data.get("enabled")) if allow_enable else False
    for public_name, column_name in JSON_FIELDS.items():
        value = data.get(public_name, {})
        if hasattr(value, "model_dump"):
            value = value.model_dump()
        setattr(
            config,
            column_name,
            json.dumps(value, ensure_ascii=False, separators=(",", ":")),
        )


def _config_dict(config: models.GenericHttpAdapterConfiguration) -> dict[str, Any]:
    result = {field: getattr(config, field) for field in SCALAR_FIELDS}
    for public_name, column_name in JSON_FIELDS.items():
        result[public_name] = _load_json(getattr(config, column_name))
    result["approved_origin"] = config.approved_origin
    return result


def _load_json(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {}


def _approval_fingerprint(
    provider: models.ProviderAccount,
    config: models.GenericHttpAdapterConfiguration,
) -> str:
    origin = ""
    if provider.base_url:
        try:
            origin = canonical_origin(provider.base_url)
        except TargetSecurityError:
            origin = provider.base_url
    return _hash(
        {
            "origin": origin,
            "auth": _load_json(config.auth_json),
            "credential_reference_id": config.credential_reference_id,
        }
    )


def _test_fingerprint(
    provider: models.ProviderAccount,
    config: models.GenericHttpAdapterConfiguration,
) -> str:
    config_data = _config_dict(config)
    config_data.pop("enabled", None)
    return _hash(
        {
            "base_url": provider.base_url,
            **config_data,
            "approved_origin": config.approved_origin,
        }
    )


def _hash(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def _require_enable_ready(
    provider: models.ProviderAccount,
    config: models.GenericHttpAdapterConfiguration,
) -> None:
    if config.tested_fingerprint != _test_fingerprint(provider, config):
        raise HTTPException(status_code=409, detail="Adapter must be tested after its last change")
    if config.security_mode == "local_private":
        if config.approval_fingerprint != _approval_fingerprint(provider, config):
            raise HTTPException(status_code=409, detail="The exact local Origin must be approved")


def _validate_credential_reference(db: Session, credential_id: int | None) -> None:
    if credential_id is not None:
        get_or_404(db, models.CredentialReference, credential_id)


def _revoke_security_state(config: models.GenericHttpAdapterConfiguration) -> None:
    config.approved_origin = None
    config.approval_fingerprint = None
    config.tested_fingerprint = None
    config.last_tested_at = None
    config.enabled = False
    config.revision += 1


def revoke_provider_security_state(db: Session, provider_id: int) -> None:
    config = db.scalar(
        select(models.GenericHttpAdapterConfiguration).where(
            models.GenericHttpAdapterConfiguration.provider_account_id == provider_id,
            models.GenericHttpAdapterConfiguration.deleted_at.is_(None),
        )
    )
    if config is not None:
        _revoke_security_state(config)


def _available_provider_name(db: Session, requested: str) -> str:
    candidate = requested
    index = 2
    while db.scalar(select(models.ProviderAccount.id).where(models.ProviderAccount.name == candidate)):
        candidate = f"{requested} ({index})"
        index += 1
    return candidate


def _provider_error(code: str, message: str):  # type: ignore[no-untyped-def]
    from app.schemas import NormalizedProviderError

    return NormalizedProviderError(code=code, message=message, retryable=False)
