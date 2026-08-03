from __future__ import annotations

from collections.abc import AsyncIterator, Generator
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.api.model_control import router as model_control_router
from app.database import get_db
from app.schemas import (
    ModelDebugRequest,
    ModelRouteWrite,
    NormalizedModelRequest,
    NormalizedProviderError,
    NormalizedStreamEvent,
    RouteEntryWrite,
)
from app.services import model_control, model_execution, model_gateway
from app.services.control_errors import ModelControlError
from app.services.routing import (
    claim_provider,
    record_provider_result,
    resolve_candidates,
)


from tests.model_control_helpers import (
    ScriptedAdapter,
    TestingSessionLocal,
    add_model,
    clean_database as clean_database,
    request_for,
    response_for,
)


def test_circuit_breaker_closed_open_half_open_and_neutral_errors() -> None:
    with TestingSessionLocal() as db, db.begin():
        provider, _ = add_model(db)
        health = model_control.ensure_provider_health(db, provider.id)
        for _ in range(3):
            record_provider_result(
                health,
                error=NormalizedProviderError(
                    code="timeout", message="timeout", retryable=True
                ),
                latency_ms=10,
            )
        assert health.state == "open"
        assert health.consecutive_failures == 3
        with pytest.raises(ModelControlError) as opened:
            claim_provider(db, provider.id)
        assert opened.value.error.code == "circuit_open"

        health.opened_at = datetime.now(timezone.utc) - timedelta(seconds=60)
        health.recovery_timeout_seconds = 1
        claimed = claim_provider(db, provider.id)
        assert claimed.state == "half_open"
        assert claimed.half_open_in_flight is True
        record_provider_result(
            claimed,
            error=NormalizedProviderError(code="cancelled", message="cancelled"),
            latency_ms=1,
        )
        assert claimed.state == "half_open"
        assert claimed.consecutive_failures == 3
        assert claimed.half_open_in_flight is False
        claim_provider(db, provider.id)
        record_provider_result(claimed, error=None, latency_ms=8)
        assert claimed.state == "closed"
        assert claimed.consecutive_failures == 0


def test_route_strategies_use_saved_pricing_not_model_names() -> None:
    now = datetime.now(timezone.utc)
    with TestingSessionLocal() as db, db.begin():
        _, expensive = add_model(
            db,
            provider_name="Expensive Provider",
            model_name="definitely-cheap-by-name",
        )
        _, cheap = add_model(
            db,
            provider_name="Cheap Provider",
            model_name="definitely-expensive-by-name",
        )
        for profile, input_price in ((expensive, 10.0), (cheap, 1.0)):
            db.add(
                models.ModelPricing(
                    model_profile_id=profile.id,
                    input_per_million=input_price,
                    output_per_million=input_price,
                    cached_input_per_million=0,
                    reasoning_per_million=0,
                    request_fee=0,
                    tool_call_fee=0,
                    currency="USD",
                    effective_from=now - timedelta(minutes=1),
                )
            )
        route = model_control.create_route(
            db,
            ModelRouteWrite(
                name="最低费用",
                strategy="lowest_cost",
                entries=[
                    RouteEntryWrite(model_profile_id=expensive.id, position=0),
                    RouteEntryWrite(model_profile_id=cheap.id, position=1),
                ],
            ),
        )

    with TestingSessionLocal() as db:
        resolution = resolve_candidates(
            db,
            request_for(),
            provider_account_id=None,
            model_profile_id=None,
            route_id=route.id,
            manual_model_profile_id=None,
            project_id=None,
            route_run_id="pricing-test",
            required_capabilities=[],
            allow_degradation=True,
        )
        assert resolution.candidates[0].profile.id == cheap.id


def test_latency_health_and_manual_route_strategies() -> None:
    with TestingSessionLocal() as db, db.begin():
        first_provider, first = add_model(
            db, provider_name="Latency One", model_name="latency-one"
        )
        second_provider, second = add_model(
            db, provider_name="Latency Two", model_name="latency-two"
        )
        first_health = model_control.ensure_provider_health(db, first_provider.id)
        first_health.last_latency_ms = 400
        first_health.consecutive_failures = 2
        second_health = model_control.ensure_provider_health(db, second_provider.id)
        second_health.last_latency_ms = 40
        second_health.consecutive_failures = 0
        latency_route = model_control.create_route(
            db,
            ModelRouteWrite(
                name="最低延迟",
                strategy="lowest_latency",
                entries=[
                    RouteEntryWrite(model_profile_id=first.id, position=0),
                    RouteEntryWrite(model_profile_id=second.id, position=1),
                ],
            ),
        )
        health_route = model_control.create_route(
            db,
            ModelRouteWrite(
                name="最健康",
                strategy="healthiest",
                entries=[
                    RouteEntryWrite(model_profile_id=first.id, position=0),
                    RouteEntryWrite(model_profile_id=second.id, position=1),
                ],
            ),
        )
        manual_route = model_control.create_route(
            db,
            ModelRouteWrite(
                name="手动",
                strategy="manual_only",
                entries=[
                    RouteEntryWrite(model_profile_id=first.id, position=0),
                    RouteEntryWrite(model_profile_id=second.id, position=1),
                ],
            ),
        )

    def resolve(route_id: int, manual_id: int | None = None) -> Any:
        with TestingSessionLocal() as db:
            return resolve_candidates(
                db,
                request_for(),
                provider_account_id=None,
                model_profile_id=None,
                route_id=route_id,
                manual_model_profile_id=manual_id,
                project_id=None,
                route_run_id="strategy-test",
                required_capabilities=[],
                allow_degradation=True,
            )

    assert resolve(latency_route.id).candidates[0].profile.id == second.id
    assert resolve(health_route.id).candidates[0].profile.id == second.id
    assert resolve(manual_route.id, first.id).candidates[0].profile.id == first.id
    with pytest.raises(ModelControlError) as manual_missing:
        resolve(manual_route.id)
    assert manual_missing.value.error.code == "manual_model_required"


@pytest.mark.asyncio
async def test_route_fallback_only_for_allowed_errors() -> None:
    transient_adapter = ScriptedAdapter(
        "phase4_transient_adapter",
        lambda request, _count: response_for(
            request,
            error=NormalizedProviderError(
                code="rate_limit", message="limited", retryable=True, status_code=429
            ),
        ),
    )
    success_adapter = ScriptedAdapter(
        "phase4_success_adapter",
        lambda request, _count: response_for(request, text="fallback success"),
    )
    model_gateway.registry.register(transient_adapter)
    model_gateway.registry.register(success_adapter)
    with TestingSessionLocal() as db, db.begin():
        _, first = add_model(
            db,
            protocol=transient_adapter.name,
            provider_name="Transient Provider",
            model_name="first-model",
        )
        _, second = add_model(
            db,
            protocol=success_adapter.name,
            provider_name="Success Provider",
            model_name="second-model",
        )
        route = model_control.create_route(
            db,
            ModelRouteWrite(
                name="有序回退",
                strategy="ordered_fallback",
                entries=[
                    RouteEntryWrite(model_profile_id=first.id, position=0),
                    RouteEntryWrite(model_profile_id=second.id, position=1),
                ],
            ),
        )

    with TestingSessionLocal() as db:
        result = await model_execution.execute_model(
            db,
            ModelDebugRequest(
                route_id=route.id,
                model="route-placeholder",
                messages=request_for().messages,
                max_tokens=64,
                max_retries=0,
            ),
        )
        assert result.error is None
        assert result.text == "fallback success"
        assert transient_adapter.complete_calls == 1
        assert success_adapter.complete_calls == 1
        assert any("已按 Route 规则切换" in item for item in result.warnings)
        invocations = db.scalars(
            select(models.ModelInvocation).order_by(models.ModelInvocation.id)
        ).all()
        assert [item.status for item in invocations] == ["failed", "completed"]

    authentication_adapter = ScriptedAdapter(
        "phase4_auth_adapter",
        lambda request, _count: response_for(
            request,
            error=NormalizedProviderError(
                code="authentication", message="bad key", status_code=401
            ),
        ),
    )
    untouched_adapter = ScriptedAdapter(
        "phase4_untouched_adapter",
        lambda request, _count: response_for(request, text="must not run"),
    )
    model_gateway.registry.register(authentication_adapter)
    model_gateway.registry.register(untouched_adapter)
    with TestingSessionLocal() as db, db.begin():
        _, auth_model = add_model(
            db,
            protocol=authentication_adapter.name,
            provider_name="Auth Provider",
            model_name="auth-model",
        )
        _, untouched_model = add_model(
            db,
            protocol=untouched_adapter.name,
            provider_name="Untouched Provider",
            model_name="untouched-model",
        )
        auth_route = model_control.create_route(
            db,
            ModelRouteWrite(
                name="认证不回退",
                strategy="ordered_fallback",
                entries=[
                    RouteEntryWrite(model_profile_id=auth_model.id, position=0),
                    RouteEntryWrite(model_profile_id=untouched_model.id, position=1),
                ],
            ),
        )
    with TestingSessionLocal() as db:
        blocked = await model_execution.execute_model(
            db,
            ModelDebugRequest(
                route_id=auth_route.id,
                model="route-placeholder",
                messages=request_for().messages,
                max_retries=0,
            ),
        )
        assert blocked.error is not None
        assert blocked.error.code == "authentication"
        assert untouched_adapter.complete_calls == 0


@pytest.mark.asyncio
async def test_stream_never_falls_back_after_partial_text() -> None:
    async def partial_stream(
        _request: NormalizedModelRequest, _count: int
    ) -> AsyncIterator[NormalizedStreamEvent]:
        yield NormalizedStreamEvent(sequence=1, event="start")
        yield NormalizedStreamEvent(sequence=2, event="delta", text_delta="partial")
        yield NormalizedStreamEvent(
            sequence=3,
            event="error",
            error=NormalizedProviderError(
                code="timeout", message="interrupted", retryable=True
            ),
        )
        yield NormalizedStreamEvent(sequence=4, event="done", finish_reason="error")

    first_adapter = ScriptedAdapter(
        "phase4_partial_stream_adapter",
        lambda request, _count: response_for(request),
        partial_stream,
    )
    second_adapter = ScriptedAdapter(
        "phase4_second_stream_adapter",
        lambda request, _count: response_for(request, text="second"),
    )
    model_gateway.registry.register(first_adapter)
    model_gateway.registry.register(second_adapter)
    with TestingSessionLocal() as db, db.begin():
        _, first = add_model(
            db,
            protocol=first_adapter.name,
            provider_name="Partial Stream Provider",
            model_name="partial-stream-model",
        )
        _, second = add_model(
            db,
            protocol=second_adapter.name,
            provider_name="Second Stream Provider",
            model_name="second-stream-model",
        )
        for profile in (first, second):
            db.add(
                models.ModelCapability(
                    model_profile_id=profile.id,
                    capability="streaming",
                    status="supported",
                    source="manual_override",
                )
            )
        route = model_control.create_route(
            db,
            ModelRouteWrite(
                name="流式不得拼接",
                strategy="ordered_fallback",
                entries=[
                    RouteEntryWrite(model_profile_id=first.id, position=0),
                    RouteEntryWrite(model_profile_id=second.id, position=1),
                ],
            ),
        )
    with TestingSessionLocal() as db:
        events = [
            event
            async for event in model_execution.stream_model(
                db,
                ModelDebugRequest(
                    route_id=route.id,
                    model="route-placeholder",
                    messages=request_for().messages,
                    stream=True,
                    max_retries=0,
                ),
            )
        ]
        assert "".join(event.text_delta for event in events) == "partial"
        assert any(
            event.error is not None and event.error.code == "timeout"
            for event in events
        )
        assert second_adapter.stream_calls == 0
        assert not any("切换" in (event.warning or "") for event in events)


@pytest.mark.asyncio
async def test_stream_may_fallback_before_any_text_with_explicit_warning() -> None:
    async def early_error(
        _request: NormalizedModelRequest, _count: int
    ) -> AsyncIterator[NormalizedStreamEvent]:
        yield NormalizedStreamEvent(sequence=1, event="start")
        yield NormalizedStreamEvent(
            sequence=2,
            event="error",
            error=NormalizedProviderError(
                code="connection", message="offline", retryable=True
            ),
        )

    first_adapter = ScriptedAdapter(
        "phase4_early_error_stream_adapter",
        lambda request, _count: response_for(request),
        early_error,
    )
    second_adapter = ScriptedAdapter(
        "phase4_fallback_stream_adapter",
        lambda request, _count: response_for(request, text="fallback stream"),
    )
    model_gateway.registry.register(first_adapter)
    model_gateway.registry.register(second_adapter)
    with TestingSessionLocal() as db, db.begin():
        _, first = add_model(
            db,
            protocol=first_adapter.name,
            provider_name="Early Error Stream",
            model_name="early-error-stream",
        )
        _, second = add_model(
            db,
            protocol=second_adapter.name,
            provider_name="Fallback Stream",
            model_name="fallback-stream",
        )
        for profile in (first, second):
            db.add(
                models.ModelCapability(
                    model_profile_id=profile.id,
                    capability="streaming",
                    status="supported",
                    source="manual_override",
                )
            )
        route = model_control.create_route(
            db,
            ModelRouteWrite(
                name="流前可回退",
                strategy="ordered_fallback",
                entries=[
                    RouteEntryWrite(model_profile_id=first.id, position=0),
                    RouteEntryWrite(model_profile_id=second.id, position=1),
                ],
            ),
        )
    with TestingSessionLocal() as db:
        events = [
            event
            async for event in model_execution.stream_model(
                db,
                ModelDebugRequest(
                    route_id=route.id,
                    model="route-placeholder",
                    messages=request_for().messages,
                    stream=True,
                    max_retries=0,
                ),
            )
        ]
        assert "".join(event.text_delta for event in events) == "fallback stream"
        assert any("切换到" in (event.warning or "") for event in events)
        assert first_adapter.stream_calls == 1
        assert second_adapter.stream_calls == 1


def test_model_control_api_persists_real_routes_limits_and_budgets() -> None:
    with TestingSessionLocal() as db, db.begin():
        provider, profile = add_model(
            db,
            provider_name="API Provider",
            model_name="api-model",
        )

    api_app = FastAPI()
    api_app.include_router(model_control_router, prefix="/api")

    def override_db() -> Generator[Session, None, None]:
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    api_app.dependency_overrides[get_db] = override_db
    client = TestClient(api_app)
    capability = client.put(
        f"/api/model-center/models/{profile.id}/capabilities/streaming",
        json={"status": "supported"},
    )
    assert capability.status_code == 200
    assert next(
        item
        for item in capability.json()["capabilities"]
        if item["capability"] == "streaming"
    )["source"] == "manual_override"

    route = client.post(
        "/api/model-center/routes",
        json={
            "name": "API Route",
            "strategy": "manual_only",
            "entries": [{"model_profile_id": profile.id, "position": 0}],
        },
    )
    assert route.status_code == 201
    rate_limit = client.post(
        "/api/model-center/rate-limits",
        json={
            "scope_type": "provider",
            "scope_key": str(provider.id),
            "max_concurrency": 2,
            "queue_timeout_seconds": 5,
        },
    )
    assert rate_limit.status_code == 201
    budget = client.post(
        "/api/model-center/budgets",
        json={
            "scope_type": "per_request",
            "scope_key": "*",
            "max_tokens": 4096,
            "currency": "USD",
        },
    )
    assert budget.status_code == 201
    health = client.get("/api/model-center/health")
    assert health.status_code == 200
    assert health.json()[0]["state"] == "closed"
    assert client.get("/api/model-center/routes").json()[0]["name"] == "API Route"
    assert client.get("/api/model-center/rate-limits").json()[0]["max_concurrency"] == 2
    assert client.get("/api/model-center/budgets").json()[0]["max_tokens"] == 4096
