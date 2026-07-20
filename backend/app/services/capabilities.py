from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any, cast

from fastapi import HTTPException
from jsonschema import Draft202012Validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.repositories import get_or_404
from app.schemas import (
    CapabilityProbeRead,
    CapabilityProbeRequest,
    EffectiveCapabilitiesRead,
    EffectiveCapabilityRead,
    NormalizedContentPart,
    NormalizedMessage,
    NormalizedModelRequest,
    NormalizedToolDefinition,
)
from app.schemas.model_control import CapabilitySource, CapabilityStatus
from app.services import model_gateway
from app.services import models as model_service
from app.services.gateway_http import ProviderRequestError


SOURCE_PRIORITY: dict[str, int] = {
    "provider_default": 0,
    "imported_manifest": 1,
    "model_list_api": 2,
    "official_metadata": 3,
    "automatic_probe": 4,
    "manual_override": 5,
}

KNOWN_CAPABILITIES = (
    "basic_text",
    "system_prompt",
    "streaming",
    "json_object",
    "json_schema",
    "tool_calling",
    "temperature",
    "top_p",
    "max_output_tokens",
)

PROTOCOL_DEFAULTS: dict[str, dict[str, CapabilityStatus]] = {
    "mock": {
        "basic_text": "supported",
        "system_prompt": "emulated",
        "streaming": "supported",
        "json_object": "supported",
        "json_schema": "degraded",
        "tool_calling": "unsupported",
        "temperature": "supported",
        "top_p": "unknown",
        "max_output_tokens": "supported",
    },
    "openai_responses": {
        "basic_text": "supported",
        "system_prompt": "supported",
        "streaming": "supported",
        "json_object": "supported",
        "json_schema": "supported",
        "tool_calling": "supported",
        "temperature": "supported",
        "top_p": "supported",
        "max_output_tokens": "supported",
    },
    "openai_chat": {
        "basic_text": "supported",
        "system_prompt": "supported",
        "streaming": "supported",
        "json_object": "supported",
        "json_schema": "supported",
        "tool_calling": "supported",
        "temperature": "supported",
        "top_p": "supported",
        "max_output_tokens": "supported",
    },
    "anthropic": {
        "basic_text": "supported",
        "system_prompt": "supported",
        "streaming": "supported",
        "json_object": "emulated",
        "json_schema": "emulated",
        "tool_calling": "supported",
        "temperature": "supported",
        "top_p": "supported",
        "max_output_tokens": "supported",
    },
    "gemini": {
        "basic_text": "supported",
        "system_prompt": "supported",
        "streaming": "supported",
        "json_object": "supported",
        "json_schema": "degraded",
        "tool_calling": "supported",
        "temperature": "supported",
        "top_p": "supported",
        "max_output_tokens": "supported",
    },
    "ollama": {
        "basic_text": "supported",
        "system_prompt": "supported",
        "streaming": "supported",
        "json_object": "degraded",
        "json_schema": "unknown",
        "tool_calling": "unknown",
        "temperature": "supported",
        "top_p": "supported",
        "max_output_tokens": "supported",
    },
}

PROTOCOL_ALIASES = {
    "openai_compatible": "openai_chat",
    "anthropic_compatible": "anthropic",
}

ProbeCancelled = Callable[[], Awaitable[bool]]


def effective_capabilities(
    db: Session, model_profile_id: int
) -> EffectiveCapabilitiesRead:
    profile = cast(
        models.ModelProfile, get_or_404(db, models.ModelProfile, model_profile_id)
    )
    provider = cast(
        models.ProviderAccount,
        get_or_404(db, models.ProviderAccount, profile.provider_account_id),
    )
    protocol = db.scalar(
        select(models.ProtocolConfiguration).where(
            models.ProtocolConfiguration.provider_account_id == provider.id,
            models.ProtocolConfiguration.deleted_at.is_(None),
        )
    )
    protocol_name = protocol.protocol if protocol is not None else provider.provider_type
    protocol_name = PROTOCOL_ALIASES.get(protocol_name, protocol_name)
    defaults = dict(PROTOCOL_DEFAULTS.get(protocol_name, {}))
    defaults.update(_generic_adapter_defaults(db, provider.id))

    candidates: dict[str, list[tuple[int, CapabilityStatus, CapabilitySource, str]]] = {}
    for capability in KNOWN_CAPABILITIES:
        status = defaults.get(capability, "unknown")
        candidates.setdefault(capability, []).append(
            (
                SOURCE_PRIORITY["provider_default"],
                status,
                "provider_default",
                f"来自协议 {protocol_name} 的默认元数据",
            )
        )

    rows = db.scalars(
        select(models.ModelCapability).where(
            models.ModelCapability.model_profile_id == profile.id,
            models.ModelCapability.deleted_at.is_(None),
        )
    ).all()
    for row in rows:
        if row.source not in SOURCE_PRIORITY or row.status not in {
            "supported",
            "unsupported",
            "unknown",
            "degraded",
            "emulated",
        }:
            continue
        source = cast(CapabilitySource, row.source)
        status = cast(CapabilityStatus, row.status)
        candidates.setdefault(row.capability, []).append(
            (
                SOURCE_PRIORITY[source],
                status,
                source,
                f"来自 {source}，记录 #{row.id}",
            )
        )

    warnings: list[str] = []
    configured = provider.enabled and profile.enabled
    if not provider.enabled:
        warnings.append("Provider 当前已停用，所有能力暂不可调用。")
    if not profile.enabled:
        warnings.append("模型当前已停用，所有能力暂不可调用。")

    effective: list[EffectiveCapabilityRead] = []
    for capability in sorted(candidates):
        _, status, source, reason = max(candidates[capability], key=lambda item: item[0])
        if not configured:
            status = "unsupported"
            reason = f"当前配置已停用；基础判定为 {source}"
        effective.append(
            EffectiveCapabilityRead(
                capability=capability,
                status=status,
                source=source,
                reason=reason,
            )
        )
    return EffectiveCapabilitiesRead(
        model_profile_id=profile.id,
        provider_account_id=provider.id,
        capabilities=effective,
        warnings=warnings,
        generated_at=datetime.now(timezone.utc),
    )


def set_manual_override(
    db: Session, model_profile_id: int, capability: str, status: CapabilityStatus
) -> EffectiveCapabilitiesRead:
    get_or_404(db, models.ModelProfile, model_profile_id)
    normalized = _capability_name(capability)
    _upsert_capability(db, model_profile_id, normalized, status, "manual_override")
    db.flush()
    return effective_capabilities(db, model_profile_id)


def clear_manual_override(
    db: Session, model_profile_id: int, capability: str
) -> EffectiveCapabilitiesRead:
    get_or_404(db, models.ModelProfile, model_profile_id)
    normalized = _capability_name(capability)
    rows = db.scalars(
        select(models.ModelCapability).where(
            models.ModelCapability.model_profile_id == model_profile_id,
            models.ModelCapability.capability == normalized,
            models.ModelCapability.source == "manual_override",
            models.ModelCapability.deleted_at.is_(None),
        )
    ).all()
    for row in rows:
        row.deleted_at = datetime.now(timezone.utc)
        row.revision += 1
    db.flush()
    return effective_capabilities(db, model_profile_id)


async def run_capability_probe(
    db: Session,
    model_profile_id: int,
    payload: CapabilityProbeRequest,
    *,
    is_cancelled: ProbeCancelled | None = None,
) -> CapabilityProbeRead:
    profile = cast(
        models.ModelProfile, get_or_404(db, models.ModelProfile, model_profile_id)
    )
    provider = cast(
        models.ProviderAccount,
        get_or_404(db, models.ProviderAccount, profile.provider_account_id),
    )
    if payload.level == "advanced" and not payload.confirm_advanced:
        raise HTTPException(status_code=400, detail="高级探测需要明确确认")
    _ensure_probe_cost_is_bounded(db, profile, provider, payload)

    max_requests = {"basic": 1, "standard": 3, "advanced": 4}[payload.level]
    run = models.CapabilityProbeRun(
        model_profile_id=profile.id,
        level=payload.level,
        status="running",
        request_count=0,
        max_output_tokens=64,
        result_json="{}",
    )
    db.add(run)
    db.flush()

    try:
        runtime = model_service.provider_runtime(db, provider.id)
    except ProviderRequestError as exc:
        run.status = "failed"
        run.error_code = exc.error.code
        run.completed_at = datetime.now(timezone.utc)
        db.flush()
        return _probe_read(run)
    try:
        adapter = model_gateway.registry.get(runtime.protocol)
    except KeyError as exc:
        run.status = "failed"
        run.error_code = "capability_unsupported"
        run.completed_at = datetime.now(timezone.utc)
        db.flush()
        raise HTTPException(status_code=400, detail="当前协议没有可用适配器") from exc

    results: dict[str, CapabilityStatus] = {}
    try:
        await _check_cancelled(is_cancelled)
        basic = await adapter.complete(_probe_text_request(profile.name), runtime)
        run.request_count += 1
        if basic.error is not None:
            raise ProviderRequestError(basic.error)
        results["basic_text"] = "supported" if basic.text.strip() else "unknown"
        results["max_output_tokens"] = "supported"

        if payload.level in {"standard", "advanced"}:
            await _check_cancelled(is_cancelled)
            structured = await adapter.complete(_probe_json_request(profile.name), runtime)
            run.request_count += 1
            if structured.error is not None:
                results["json_object"] = _status_for_probe_error(structured.error.code)
                results["json_schema"] = _status_for_probe_error(structured.error.code)
            else:
                value = structured.structured_data or _safe_json_object(structured.text)
                results["json_object"] = "supported" if value is not None else "degraded"
                results["json_schema"] = (
                    "supported" if value is not None and _valid_probe_json(value) else "degraded"
                )

            await _check_cancelled(is_cancelled)
            saw_delta = False
            stream_error: str | None = None
            async for event in adapter.stream(_probe_stream_request(profile.name), runtime):
                if event.event == "delta" and event.text_delta:
                    saw_delta = True
                if event.event == "error" and event.error is not None:
                    stream_error = event.error.code
                await _check_cancelled(is_cancelled)
            run.request_count += 1
            results["streaming"] = (
                _status_for_probe_error(stream_error) if stream_error else (
                    "supported" if saw_delta else "unknown"
                )
            )

        if payload.level == "advanced":
            await _check_cancelled(is_cancelled)
            tool_response = await adapter.complete(_probe_tool_request(profile.name), runtime)
            run.request_count += 1
            if tool_response.error is not None:
                results["tool_calling"] = _status_for_probe_error(tool_response.error.code)
            else:
                results["tool_calling"] = (
                    "supported" if tool_response.tool_calls else "unknown"
                )

        if run.request_count > max_requests:
            raise RuntimeError("capability probe exceeded its request limit")
        for capability, status in results.items():
            _upsert_capability(
                db, profile.id, capability, status, "automatic_probe"
            )
        run.status = "completed"
        run.result_json = json.dumps(results, ensure_ascii=True, sort_keys=True)
        run.estimated_cost = 0.0 if provider.provider_type == "mock" else None
    except asyncio.CancelledError:
        run.status = "cancelled"
        run.error_code = "cancelled"
        raise
    except ProviderRequestError as exc:
        run.status = "failed"
        run.error_code = exc.error.code
    finally:
        run.completed_at = datetime.now(timezone.utc)
        db.flush()
    return _probe_read(run)


def list_probe_runs(db: Session, model_profile_id: int) -> list[CapabilityProbeRead]:
    get_or_404(db, models.ModelProfile, model_profile_id)
    rows = db.scalars(
        select(models.CapabilityProbeRun)
        .where(
            models.CapabilityProbeRun.model_profile_id == model_profile_id,
            models.CapabilityProbeRun.deleted_at.is_(None),
        )
        .order_by(models.CapabilityProbeRun.id.desc())
    ).all()
    return [_probe_read(row) for row in rows]


def capability_status_map(value: EffectiveCapabilitiesRead) -> dict[str, CapabilityStatus]:
    return {item.capability: item.status for item in value.capabilities}


def _generic_adapter_defaults(db: Session, provider_id: int) -> dict[str, CapabilityStatus]:
    config = db.scalar(
        select(models.GenericHttpAdapterConfiguration).where(
            models.GenericHttpAdapterConfiguration.provider_account_id == provider_id,
            models.GenericHttpAdapterConfiguration.deleted_at.is_(None),
        )
    )
    if config is None:
        return {}
    try:
        raw = json.loads(config.capability_defaults_json)
    except json.JSONDecodeError:
        return {}
    if not isinstance(raw, dict):
        return {}
    allowed = {"supported", "unsupported", "unknown", "degraded", "emulated"}
    return {
        str(key): cast(CapabilityStatus, value)
        for key, value in raw.items()
        if isinstance(value, str) and value in allowed
    }


def _upsert_capability(
    db: Session,
    model_profile_id: int,
    capability: str,
    status: CapabilityStatus,
    source: CapabilitySource,
) -> models.ModelCapability:
    row = db.scalar(
        select(models.ModelCapability).where(
            models.ModelCapability.model_profile_id == model_profile_id,
            models.ModelCapability.capability == capability,
            models.ModelCapability.source == source,
            models.ModelCapability.deleted_at.is_(None),
        )
    )
    if row is None:
        row = models.ModelCapability(
            model_profile_id=model_profile_id,
            capability=capability,
            status=status,
            source=source,
        )
        db.add(row)
    elif row.status != status:
        row.status = status
        row.revision += 1
    return row


def _ensure_probe_cost_is_bounded(
    db: Session,
    profile: models.ModelProfile,
    provider: models.ProviderAccount,
    payload: CapabilityProbeRequest,
) -> None:
    if provider.provider_type == "mock":
        return
    now = datetime.now(timezone.utc)
    pricing = db.scalar(
        select(models.ModelPricing)
        .where(
            models.ModelPricing.model_profile_id == profile.id,
            models.ModelPricing.deleted_at.is_(None),
            models.ModelPricing.effective_from <= now,
            (
                models.ModelPricing.effective_to.is_(None)
                | (models.ModelPricing.effective_to > now)
            ),
        )
        .order_by(models.ModelPricing.effective_from.desc())
    )
    if (
        pricing is None
        or pricing.input_per_million is None
        or pricing.output_per_million is None
        or pricing.request_fee is None
    ):
        raise HTTPException(
            status_code=409,
            detail="能力探测前必须配置当前价格，未知费用不能按 0 处理",
        )
    requests = {"basic": 1, "standard": 3, "advanced": 4}[payload.level]
    estimated = requests * (
        pricing.request_fee
        + 256 / 1_000_000 * pricing.input_per_million
        + 64 / 1_000_000 * pricing.output_per_million
    )
    if estimated > payload.max_estimated_cost:
        raise HTTPException(
            status_code=409,
            detail="能力探测的费用上限不足，请提高确认上限或调整探测级别",
        )


def _probe_text_request(model: str) -> NormalizedModelRequest:
    return NormalizedModelRequest(
        model=model,
        messages=[
            NormalizedMessage(
                role="user",
                content=[NormalizedContentPart(type="text", text="Reply only with PROBE_OK.")],
            )
        ],
        temperature=0,
        max_tokens=16,
    )


def _probe_json_request(model: str) -> NormalizedModelRequest:
    schema = {
        "type": "object",
        "properties": {"probe": {"type": "string", "const": "ok"}},
        "required": ["probe"],
        "additionalProperties": False,
    }
    return NormalizedModelRequest(
        model=model,
        messages=[
            NormalizedMessage(
                role="user",
                content=[
                    NormalizedContentPart(
                        type="text", text='Return exactly one JSON object: {"probe":"ok"}.'
                    )
                ],
            )
        ],
        temperature=0,
        max_tokens=32,
        response_format="json",
        json_schema=schema,
    )


def _probe_stream_request(model: str) -> NormalizedModelRequest:
    request = _probe_text_request(model)
    request.stream = True
    return request


def _probe_tool_request(model: str) -> NormalizedModelRequest:
    return NormalizedModelRequest(
        model=model,
        messages=[
            NormalizedMessage(
                role="user",
                content=[
                    NormalizedContentPart(
                        type="text", text="Call probe_echo once with value set to ok."
                    )
                ],
            )
        ],
        temperature=0,
        max_tokens=64,
        tools=[
            NormalizedToolDefinition(
                name="probe_echo",
                description="Synthetic no-side-effect capability probe",
                input_schema={
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
                side_effect=False,
            )
        ],
        tool_choice="required",
    )


async def _check_cancelled(callback: ProbeCancelled | None) -> None:
    if callback is not None and await callback():
        raise asyncio.CancelledError


def _safe_json_object(text: str) -> dict[str, Any] | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _valid_probe_json(value: dict[str, Any]) -> bool:
    schema = {
        "type": "object",
        "properties": {"probe": {"type": "string", "const": "ok"}},
        "required": ["probe"],
        "additionalProperties": False,
    }
    return not list(Draft202012Validator(schema).iter_errors(value))


def _status_for_probe_error(error_code: str | None) -> CapabilityStatus:
    if error_code == "capability_unsupported":
        return "unsupported"
    return "unknown"


def _probe_read(run: models.CapabilityProbeRun) -> CapabilityProbeRead:
    try:
        value = json.loads(run.result_json)
    except json.JSONDecodeError:
        value = {}
    results = value if isinstance(value, dict) else {}
    return CapabilityProbeRead(
        id=run.id,
        model_profile_id=run.model_profile_id,
        level=cast(Any, run.level),
        status=run.status,
        request_count=run.request_count,
        max_output_tokens=run.max_output_tokens,
        estimated_cost=run.estimated_cost,
        results=cast(dict[str, CapabilityStatus], results),
        error_code=run.error_code,
        completed_at=run.completed_at,
        created_at=run.created_at,
    )


def _capability_name(value: str) -> str:
    normalized = value.strip().lower()
    if not normalized or len(normalized) > 80:
        raise HTTPException(status_code=422, detail="能力名称无效")
    return normalized
