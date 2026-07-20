from __future__ import annotations

import json
import os
import time
from typing import Any, cast

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.repositories import get_or_404, require_revision, soft_delete
from app.schemas import (
    ModelProfileCreate,
    ModelProfileRead,
    ModelProfileUpdate,
    ModelSyncRead,
    ProviderAccountCreate,
    ProviderAccountUpdate,
    ProviderConnectionRead,
    ProviderPresetCreate,
    ProviderPresetUpdate,
    NormalizedProviderError,
)
from app.services import model_gateway
from app.services.gateway_http import (
    ProviderRequestError,
    ProviderRuntime,
    new_request_id,
)


DEFAULT_PRESETS: tuple[dict[str, str], ...] = (
    {"slug": "openai", "name": "OpenAI", "protocol": "openai_responses", "base_url": "https://api.openai.com/v1", "default_model": "gpt-5-mini", "credential_env_var_hint": "OPENAI_API_KEY"},
    {"slug": "deepseek", "name": "DeepSeek", "protocol": "openai_chat", "base_url": "https://api.deepseek.com/v1", "default_model": "deepseek-chat", "credential_env_var_hint": "DEEPSEEK_API_KEY"},
    {"slug": "xai", "name": "xAI / Grok", "protocol": "openai_chat", "base_url": "https://api.x.ai/v1", "default_model": "grok-4", "credential_env_var_hint": "XAI_API_KEY"},
    {"slug": "anthropic", "name": "Anthropic", "protocol": "anthropic", "base_url": "https://api.anthropic.com", "default_model": "claude-sonnet-4-5", "credential_env_var_hint": "ANTHROPIC_API_KEY"},
    {"slug": "gemini", "name": "Gemini", "protocol": "gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta", "default_model": "gemini-2.5-flash", "credential_env_var_hint": "GEMINI_API_KEY"},
    {"slug": "openrouter", "name": "OpenRouter", "protocol": "openai_chat", "base_url": "https://openrouter.ai/api/v1", "default_model": "", "credential_env_var_hint": "OPENROUTER_API_KEY"},
    {"slug": "ollama", "name": "Ollama", "protocol": "ollama", "base_url": "http://127.0.0.1:11434", "default_model": "", "credential_env_var_hint": ""},
    {"slug": "openai-compatible", "name": "通用 OpenAI-compatible", "protocol": "openai_compatible", "base_url": "", "default_model": "", "credential_env_var_hint": "PROVIDER_API_KEY"},
    {"slug": "anthropic-compatible", "name": "通用 Anthropic-compatible", "protocol": "anthropic_compatible", "base_url": "", "default_model": "", "credential_env_var_hint": "PROVIDER_API_KEY"},
)


def create_provider(db: Session, payload: ProviderAccountCreate) -> models.ProviderAccount:
    existing = db.scalar(
        select(models.ProviderAccount).where(models.ProviderAccount.name == payload.name)
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="Provider name already exists")
    provider = models.ProviderAccount(**payload.model_dump())
    db.add(provider)
    db.flush()
    db.add(
        models.ProtocolConfiguration(
            provider_account_id=provider.id,
            protocol=provider.provider_type,
            options_json="{}",
        )
    )
    db.flush()
    from app.services.context_memory import ensure_provider_data_policy

    ensure_provider_data_policy(db, provider.id)
    return provider


def update_provider(
    db: Session, provider_id: int, payload: ProviderAccountUpdate
) -> models.ProviderAccount:
    provider = cast(
        models.ProviderAccount, get_or_404(db, models.ProviderAccount, provider_id)
    )
    require_revision(provider, payload.expected_revision)
    previous_base_url = provider.base_url
    previous_type = provider.provider_type
    duplicate = db.scalar(
        select(models.ProviderAccount).where(
            models.ProviderAccount.name == payload.name,
            models.ProviderAccount.id != provider.id,
            models.ProviderAccount.deleted_at.is_(None),
        )
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="Provider name already exists")
    for key, value in payload.model_dump(exclude={"expected_revision"}).items():
        setattr(provider, key, value)
    provider.revision += 1
    protocol = _protocol_configuration(db, provider)
    protocol.protocol = provider.provider_type
    protocol.revision += 1
    if (
        previous_base_url != provider.base_url
        or previous_type != provider.provider_type
    ):
        from app.services.custom_adapters import revoke_provider_security_state

        revoke_provider_security_state(db, provider.id)
    db.flush()
    return provider


def delete_provider(db: Session, provider_id: int, expected_revision: int) -> None:
    provider = cast(
        models.ProviderAccount, get_or_404(db, models.ProviderAccount, provider_id)
    )
    require_revision(provider, expected_revision)
    soft_delete(provider)
    db.flush()


def list_providers(db: Session) -> list[models.ProviderAccount]:
    stmt = (
        select(models.ProviderAccount)
        .where(models.ProviderAccount.deleted_at.is_(None))
        .order_by(models.ProviderAccount.id)
    )
    return list(db.scalars(stmt).all())


def create_model_profile(db: Session, payload: ModelProfileCreate) -> models.ModelProfile:
    get_or_404(db, models.ProviderAccount, payload.provider_account_id)
    existing = db.scalar(
        select(models.ModelProfile).where(
            models.ModelProfile.provider_account_id == payload.provider_account_id,
            models.ModelProfile.name == payload.name,
        )
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="Model already exists for this provider")
    profile = models.ModelProfile(**payload.model_dump())
    db.add(profile)
    db.flush()
    return profile


def update_model_profile(
    db: Session, model_id: int, payload: ModelProfileUpdate
) -> models.ModelProfile:
    profile = cast(models.ModelProfile, get_or_404(db, models.ModelProfile, model_id))
    require_revision(profile, payload.expected_revision)
    for key, value in payload.model_dump(exclude={"expected_revision"}).items():
        setattr(profile, key, value)
    profile.revision += 1
    db.flush()
    return profile


def delete_model_profile(db: Session, model_id: int, expected_revision: int) -> None:
    profile = cast(models.ModelProfile, get_or_404(db, models.ModelProfile, model_id))
    require_revision(profile, expected_revision)
    soft_delete(profile)
    db.flush()


def list_model_profiles(
    db: Session, provider_account_id: int | None = None
) -> list[models.ModelProfile]:
    stmt = select(models.ModelProfile).where(models.ModelProfile.deleted_at.is_(None))
    if provider_account_id is not None:
        stmt = stmt.where(models.ModelProfile.provider_account_id == provider_account_id)
    return list(db.scalars(stmt.order_by(models.ModelProfile.id)).all())


def list_presets(db: Session) -> list[models.ProviderPreset]:
    stmt = (
        select(models.ProviderPreset)
        .where(models.ProviderPreset.deleted_at.is_(None))
        .order_by(models.ProviderPreset.id)
    )
    return list(db.scalars(stmt).all())


def create_preset(db: Session, payload: ProviderPresetCreate) -> models.ProviderPreset:
    existing = db.scalar(
        select(models.ProviderPreset).where(models.ProviderPreset.slug == payload.slug)
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="Preset slug already exists")
    data = payload.model_dump(exclude={"options"})
    preset = models.ProviderPreset(
        **data, options_json=json.dumps(payload.options, ensure_ascii=False)
    )
    db.add(preset)
    db.flush()
    return preset


def update_preset(
    db: Session, preset_id: int, payload: ProviderPresetUpdate
) -> models.ProviderPreset:
    preset = cast(models.ProviderPreset, get_or_404(db, models.ProviderPreset, preset_id))
    require_revision(preset, payload.expected_revision)
    duplicate = db.scalar(
        select(models.ProviderPreset).where(
            models.ProviderPreset.slug == payload.slug,
            models.ProviderPreset.id != preset.id,
            models.ProviderPreset.deleted_at.is_(None),
        )
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="Preset slug already exists")
    for key, value in payload.model_dump(exclude={"expected_revision", "options"}).items():
        setattr(preset, key, value)
    preset.options_json = json.dumps(payload.options, ensure_ascii=False)
    preset.revision += 1
    db.flush()
    return preset


def ensure_provider_presets(db: Session) -> None:
    existing_slugs = set(db.scalars(select(models.ProviderPreset.slug)).all())
    for data in DEFAULT_PRESETS:
        if data["slug"] not in existing_slugs:
            db.add(models.ProviderPreset(**data, options_json="{}"))
    db.flush()


def provider_runtime(db: Session, provider_id: int) -> ProviderRuntime:
    provider = cast(
        models.ProviderAccount, get_or_404(db, models.ProviderAccount, provider_id)
    )
    protocol = _protocol_configuration(db, provider)
    if not provider.enabled:
        raise ProviderRequestError(
            _connection_error("capability_unsupported", "Provider is disabled")
        )
    if (protocol.protocol or provider.provider_type) == "generic_json_http":
        from app.services.custom_adapters import runtime_for_provider

        return runtime_for_provider(db, provider.id)
    options = _json_object(protocol.options_json)
    api_key = None
    if provider.credential_env_var:
        api_key = os.getenv(provider.credential_env_var)
    if not api_key:
        from app.services.credential_store import get_provider_secret

        api_key = get_provider_secret(provider.id)
    credential_protocols = {
        "openai_chat",
        "openai_responses",
        "openai_compatible",
        "anthropic",
        "anthropic_compatible",
        "gemini",
    }
    if not api_key and (
        provider.credential_env_var
        or (protocol.protocol or provider.provider_type) in credential_protocols
    ):
        source = (
            f"environment variable {provider.credential_env_var} or Windows Credential Manager"
            if provider.credential_env_var
            else "Windows Credential Manager"
        )
        raise ProviderRequestError(
            _connection_error("authentication", f"No API key found in {source}")
        )
    return ProviderRuntime(
        protocol=protocol.protocol or provider.provider_type,
        base_url=provider.base_url or "",
        api_key=api_key,
        options=options,
        provider_account_id=provider.id,
    )


async def test_provider_connection(
    db: Session, provider_id: int
) -> ProviderConnectionRead:
    started = time.perf_counter()
    request_id = new_request_id()
    protocol = "unknown"
    try:
        runtime = provider_runtime(db, provider_id)
        protocol = runtime.protocol
        adapter = model_gateway.registry.get(runtime.protocol)
        discovered = await adapter.list_models(runtime)
        return ProviderConnectionRead(
            ok=True,
            protocol=runtime.protocol,
            latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
            request_id=request_id,
            model_count=len(discovered),
        )
    except ProviderRequestError as exc:
        return ProviderConnectionRead(
            ok=False,
            protocol=protocol,
            latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
            request_id=exc.error.request_id or request_id,
            error=exc.error,
        )
    except KeyError:
        return ProviderConnectionRead(
            ok=False,
            protocol=protocol,
            latency_ms=max(0, int((time.perf_counter() - started) * 1000)),
            request_id=request_id,
            error=_connection_error("capability_unsupported", "Protocol adapter is not installed"),
        )


async def sync_provider_models(db: Session, provider_id: int) -> ModelSyncRead:
    runtime = provider_runtime(db, provider_id)
    try:
        adapter = model_gateway.registry.get(runtime.protocol)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail="Unsupported provider protocol") from exc
    discovered = await adapter.list_models(runtime)
    created = 0
    updated = 0
    for item in discovered:
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            continue
        profile = db.scalar(
            select(models.ModelProfile).where(
                models.ModelProfile.provider_account_id == provider_id,
                models.ModelProfile.name == model_id,
            )
        )
        display_name = str(item.get("display_name") or model_id)
        context_window = max(512, int(item.get("context_window") or 8192))
        if profile is None:
            profile = models.ModelProfile(
                provider_account_id=provider_id,
                name=model_id,
                display_name=display_name,
                context_window=context_window,
            )
            db.add(profile)
            created += 1
        else:
            changed = (
                profile.display_name != display_name
                or profile.context_window != context_window
                or profile.deleted_at is not None
            )
            if changed:
                profile.display_name = display_name
                profile.context_window = context_window
                profile.deleted_at = None
                profile.revision += 1
                updated += 1
    db.flush()
    profiles = list_model_profiles(db, provider_id)
    return ModelSyncRead(
        provider_account_id=provider_id,
        discovered=len(discovered),
        created=created,
        updated=updated,
        models=[ModelProfileRead.model_validate(profile) for profile in profiles],
    )


def _protocol_configuration(
    db: Session, provider: models.ProviderAccount
) -> models.ProtocolConfiguration:
    config = db.scalar(
        select(models.ProtocolConfiguration).where(
            models.ProtocolConfiguration.provider_account_id == provider.id,
            models.ProtocolConfiguration.deleted_at.is_(None),
        )
    )
    if config is None:
        config = models.ProtocolConfiguration(
            provider_account_id=provider.id,
            protocol=provider.provider_type,
            options_json="{}",
        )
        db.add(config)
        db.flush()
    return config


def _json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _connection_error(code: str, message: str) -> NormalizedProviderError:
    return NormalizedProviderError(code=code, message=message, retryable=False)
