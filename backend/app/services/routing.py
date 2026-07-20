from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.repositories import get_or_404
from app.schemas import NormalizedModelRequest, NormalizedProviderError
from app.services.capabilities import capability_status_map, effective_capabilities
from app.services.control_errors import ModelControlError
from app.services.model_control import ensure_provider_health
from app.services.usage_control import context_preflight, preflight_cost


FALLBACK_ERROR_CODES = {
    "rate_limit",
    "timeout",
    "connection",
    "provider_internal",
    "model_not_found",
    "capability_unsupported",
    "circuit_open",
}

HEALTH_FAILURE_CODES = {
    "rate_limit",
    "timeout",
    "connection",
    "provider_internal",
    "stream_interrupted",
    "malformed_response",
}

HEALTH_NEUTRAL_CODES = {
    "authentication",
    "permission",
    "invalid_request",
    "quota",
    "content_refusal",
    "context_too_long",
    "schema_validation",
    "cancelled",
    "data_boundary",
}


@dataclass(frozen=True)
class RouteCandidate:
    profile: models.ModelProfile
    provider: models.ProviderAccount
    position: int
    capability_warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class RouteResolution:
    route: models.ModelRoute | None
    route_run_id: str | None
    candidates: tuple[RouteCandidate, ...]
    required_capabilities: tuple[str, ...]
    allow_degradation: bool


def resolve_candidates(
    db: Session,
    request: NormalizedModelRequest,
    *,
    provider_account_id: int | None,
    model_profile_id: int | None,
    route_id: int | None,
    manual_model_profile_id: int | None,
    project_id: int | None,
    route_run_id: str | None,
    required_capabilities: list[str],
    allow_degradation: bool,
) -> RouteResolution:
    if route_id is None:
        profile = _resolve_direct_profile(
            db, request.model, provider_account_id, model_profile_id
        )
        candidate = _candidate_if_usable(
            db,
            profile,
            0,
            required_capabilities,
            allow_degradation,
        )
        if candidate is None:
            raise ModelControlError(
                "capability_unsupported",
                "所选模型不满足必需能力或当前已停用",
                status_code=409,
            )
        return RouteResolution(
            route=None,
            route_run_id=route_run_id,
            candidates=(candidate,),
            required_capabilities=tuple(required_capabilities),
            allow_degradation=allow_degradation,
        )

    route = cast(models.ModelRoute, get_or_404(db, models.ModelRoute, route_id))
    if not route.enabled:
        raise ModelControlError("route_disabled", "模型 Route 当前已停用")
    if route.project_id is not None and route.project_id != project_id:
        raise ModelControlError(
            "data_boundary",
            "项目专属 Route 不能用于其他项目",
            status_code=409,
        )
    route_required = _route_required_capabilities(route)
    combined_required = list(dict.fromkeys([*route_required, *required_capabilities]))
    effective_degradation = route.allow_degradation and allow_degradation
    entries = db.scalars(
        select(models.ModelRouteEntry)
        .where(
            models.ModelRouteEntry.route_id == route.id,
            models.ModelRouteEntry.enabled.is_(True),
            models.ModelRouteEntry.deleted_at.is_(None),
        )
        .order_by(models.ModelRouteEntry.position, models.ModelRouteEntry.id)
    ).all()
    if route.strategy == "manual_only":
        if manual_model_profile_id is None:
            raise ModelControlError(
                "manual_model_required",
                "manual only Route 必须明确选择模型",
                status_code=422,
            )
        entries = [
            item for item in entries if item.model_profile_id == manual_model_profile_id
        ]
        if not entries:
            raise ModelControlError(
                "manual_model_invalid", "手动选择的模型不属于该 Route", status_code=422
            )

    candidates: list[RouteCandidate] = []
    for entry in entries:
        entry_profile = db.get(models.ModelProfile, entry.model_profile_id)
        if entry_profile is None:
            continue
        candidate = _candidate_if_usable(
            db,
            entry_profile,
            entry.position,
            combined_required,
            effective_degradation,
        )
        if candidate is not None:
            candidates.append(candidate)
    if not candidates:
        raise ModelControlError(
            "capability_unsupported",
            "Route 中没有满足能力、启用状态和健康要求的模型",
            status_code=409,
        )
    candidates = _sort_candidates(db, route.strategy, candidates, request)
    return RouteResolution(
        route=route,
        route_run_id=route_run_id or f"request-{datetime.now(timezone.utc).timestamp():.6f}",
        candidates=tuple(candidates),
        required_capabilities=tuple(combined_required),
        allow_degradation=effective_degradation,
    )


def claim_provider(db: Session, provider_id: int) -> models.ProviderHealth:
    health = ensure_provider_health(db, provider_id)
    now = datetime.now(timezone.utc)
    if health.state == "open":
        opened_at = _aware(health.opened_at or now)
        elapsed = (now - opened_at).total_seconds()
        if elapsed < health.recovery_timeout_seconds:
            retry_after = health.recovery_timeout_seconds - elapsed
            raise ModelControlError(
                "circuit_open",
                "Provider 熔断器处于 open 状态",
                retryable=True,
                status_code=503,
                retry_after_seconds=retry_after,
            )
        health.state = "half_open"
        health.half_open_in_flight = False
        health.revision += 1
    if health.state == "half_open":
        if health.half_open_in_flight:
            raise ModelControlError(
                "circuit_open",
                "Provider 熔断器正在执行 half-open 试探请求",
                retryable=True,
                status_code=503,
            )
        health.half_open_in_flight = True
        health.revision += 1
    db.flush()
    return health


def record_provider_result(
    health: models.ProviderHealth,
    *,
    error: NormalizedProviderError | None,
    latency_ms: int,
) -> None:
    now = datetime.now(timezone.utc)
    health.last_latency_ms = max(0, latency_ms)
    if error is None:
        health.state = "closed"
        health.consecutive_failures = 0
        health.half_open_in_flight = False
        health.opened_at = None
        health.last_success_at = now
        health.last_error_code = None
        health.revision += 1
        return

    health.last_error_code = error.code
    if error.code in HEALTH_NEUTRAL_CODES:
        health.half_open_in_flight = False
        health.revision += 1
        return
    if error.code not in HEALTH_FAILURE_CODES:
        health.half_open_in_flight = False
        health.revision += 1
        return
    health.last_failure_at = now
    health.consecutive_failures += 1
    if health.state == "half_open" or health.consecutive_failures >= health.failure_threshold:
        health.state = "open"
        health.opened_at = now
    health.half_open_in_flight = False
    health.revision += 1


def fallback_allowed(error: NormalizedProviderError, *, emitted_text: bool) -> bool:
    return not emitted_text and error.code in FALLBACK_ERROR_CODES


def _resolve_direct_profile(
    db: Session,
    model_name: str,
    provider_account_id: int | None,
    model_profile_id: int | None,
) -> models.ModelProfile:
    if model_profile_id is not None:
        profile = cast(
            models.ModelProfile, get_or_404(db, models.ModelProfile, model_profile_id)
        )
        if provider_account_id is not None and profile.provider_account_id != provider_account_id:
            raise ModelControlError(
                "invalid_request", "模型不属于所选 Provider", status_code=422
            )
        return profile
    stmt = select(models.ModelProfile).where(
        models.ModelProfile.name == model_name,
        models.ModelProfile.deleted_at.is_(None),
    )
    if provider_account_id is not None:
        stmt = stmt.where(models.ModelProfile.provider_account_id == provider_account_id)
    resolved_profile = db.scalar(stmt.order_by(models.ModelProfile.id))
    if resolved_profile is not None:
        return resolved_profile
    if provider_account_id is None:
        return _ensure_builtin_mock(db, model_name)
    raise ModelControlError("model_not_found", "找不到所选模型", status_code=404)


def _ensure_builtin_mock(db: Session, model_name: str) -> models.ModelProfile:
    if model_name != "mock-novel-v1":
        raise ModelControlError("model_not_found", "找不到所选模型", status_code=404)
    provider = db.scalar(
        select(models.ProviderAccount).where(
            models.ProviderAccount.provider_type == "mock",
            models.ProviderAccount.deleted_at.is_(None),
        )
    )
    if provider is None:
        name = "内置 Mock"
        suffix = 1
        while db.scalar(
            select(models.ProviderAccount).where(models.ProviderAccount.name == name)
        ) is not None:
            suffix += 1
            name = f"内置 Mock {suffix}"
        provider = models.ProviderAccount(name=name, provider_type="mock", enabled=True)
        db.add(provider)
        db.flush()
        db.add(
            models.ProtocolConfiguration(
                provider_account_id=provider.id, protocol="mock", options_json="{}"
            )
        )
    profile = models.ModelProfile(
        provider_account_id=provider.id,
        name="mock-novel-v1",
        display_name="Mock Novel v1",
        context_window=8192,
        enabled=True,
    )
    db.add(profile)
    db.flush()
    return profile


def _candidate_if_usable(
    db: Session,
    profile: models.ModelProfile,
    position: int,
    required: list[str],
    allow_degradation: bool,
) -> RouteCandidate | None:
    if profile.deleted_at is not None or not profile.enabled:
        return None
    provider = db.get(models.ProviderAccount, profile.provider_account_id)
    if provider is None or provider.deleted_at is not None or not provider.enabled:
        return None
    capabilities = effective_capabilities(db, profile.id)
    status_map = capability_status_map(capabilities)
    warnings: list[str] = []
    for capability in required:
        status = status_map.get(capability, "unknown")
        if status == "supported":
            continue
        if allow_degradation and status in {"degraded", "emulated"}:
            warnings.append(f"能力 {capability} 将以 {status} 模式执行")
            continue
        return None
    health = ensure_provider_health(db, provider.id)
    if health.state == "open" and not _recovery_due(health):
        return None
    return RouteCandidate(profile, provider, position, tuple(warnings))


def _sort_candidates(
    db: Session,
    strategy: str,
    candidates: list[RouteCandidate],
    request: NormalizedModelRequest,
) -> list[RouteCandidate]:
    if strategy in {"ordered_fallback", "manual_only"}:
        return sorted(candidates, key=lambda item: item.position)
    if strategy == "lowest_latency":
        return sorted(
            candidates,
            key=lambda item: (
                _health(db, item.provider.id).last_latency_ms is None,
                _health(db, item.provider.id).last_latency_ms or 0,
                item.position,
            ),
        )
    if strategy == "healthiest":
        return sorted(candidates, key=lambda item: _health_sort_key(db, item))
    if strategy == "lowest_cost":
        return sorted(candidates, key=lambda item: _cost_sort_key(db, item, request))
    raise ModelControlError("invalid_route_strategy", "Route 策略无效", status_code=422)


def _cost_sort_key(
    db: Session, candidate: RouteCandidate, request: NormalizedModelRequest
) -> tuple[bool, float, int]:
    candidate_request = request.model_copy(update={"model": candidate.profile.name})
    context = context_preflight(
        candidate_request,
        candidate.profile.context_window,
        tokenizer_name=candidate.profile.tokenizer_name,
        tokenizer_source=candidate.profile.tokenizer_source,
    )
    cost = preflight_cost(db, candidate.profile.id, candidate_request, context)
    return (not cost.known, cost.amount or 0, candidate.position)


def _health_sort_key(
    db: Session, candidate: RouteCandidate
) -> tuple[int, int, bool, int, int]:
    health = _health(db, candidate.provider.id)
    state_rank = {"closed": 0, "half_open": 1, "open": 2}.get(health.state, 3)
    return (
        state_rank,
        health.consecutive_failures,
        health.last_latency_ms is None,
        health.last_latency_ms or 0,
        candidate.position,
    )


def _health(db: Session, provider_id: int) -> models.ProviderHealth:
    return ensure_provider_health(db, provider_id)


def _recovery_due(health: models.ProviderHealth) -> bool:
    if health.opened_at is None:
        return True
    return (
        datetime.now(timezone.utc) - _aware(health.opened_at)
    ).total_seconds() >= health.recovery_timeout_seconds


def _route_required_capabilities(route: models.ModelRoute) -> list[str]:
    try:
        value = json.loads(route.required_capabilities_json)
    except json.JSONDecodeError:
        return []
    return [str(item) for item in value] if isinstance(value, list) else []


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)
