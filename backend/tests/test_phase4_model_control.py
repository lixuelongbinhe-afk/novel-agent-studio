from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Generator
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.api.model_control import router as model_control_router
from app.database import Base, get_db
from app.schemas import (
    CapabilityProbeRequest,
    CostEstimateRead,
    ModelDebugRequest,
    ModelPricingWrite,
    ModelRouteWrite,
    NormalizedContentPart,
    NormalizedMessage,
    NormalizedModelRequest,
    NormalizedModelResponse,
    NormalizedProviderError,
    NormalizedStreamEvent,
    NormalizedToolDefinition,
    NormalizedUsage,
    RouteEntryWrite,
)
from app.services import capabilities, model_control, model_execution, model_gateway
from app.services.control_errors import ModelControlError
from app.services.rate_limits import (
    LayeredRateLimiter,
    LimitContext,
    LimitDescriptor,
    matching_rate_limits,
    retry_delay,
)
from app.services.routing import (
    claim_provider,
    record_provider_result,
    resolve_candidates,
)
from app.services.structured_output import extract_json_value, prepare_request
from app.services.usage_control import (
    BudgetContext,
    BudgetManager,
    active_pricing,
    context_preflight,
    estimate_cost,
    estimate_input,
    normalize_usage,
)


engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
)


@pytest.fixture(autouse=True)
def clean_database() -> Generator[None, None, None]:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def add_model(
    db: Session,
    *,
    protocol: str = "mock",
    provider_name: str = "Phase4 Provider",
    model_name: str = "phase4-model",
    context_window: int = 8192,
) -> tuple[models.ProviderAccount, models.ModelProfile]:
    provider = models.ProviderAccount(
        name=provider_name,
        provider_type=protocol,
        enabled=True,
    )
    db.add(provider)
    db.flush()
    db.add(
        models.ProtocolConfiguration(
            provider_account_id=provider.id,
            protocol=protocol,
            options_json="{}",
        )
    )
    profile = models.ModelProfile(
        provider_account_id=provider.id,
        name=model_name,
        display_name=model_name,
        context_window=context_window,
        enabled=True,
    )
    db.add(profile)
    db.flush()
    return provider, profile


def request_for(model: str = "phase4-model", *, text: str = "synthetic input") -> NormalizedModelRequest:
    return NormalizedModelRequest(
        model=model,
        messages=[
            NormalizedMessage(
                role="user", content=[NormalizedContentPart(type="text", text=text)]
            )
        ],
        max_tokens=64,
    )


def response_for(
    request: NormalizedModelRequest,
    *,
    text: str = "ok",
    error: NormalizedProviderError | None = None,
) -> NormalizedModelResponse:
    return NormalizedModelResponse(
        model=request.model,
        text=text if error is None else "",
        content=(
            [NormalizedContentPart(type="text", text=text)] if error is None else []
        ),
        usage=NormalizedUsage(
            input_tokens=5,
            output_tokens=2 if error is None else 0,
            total_tokens=7 if error is None else 5,
            estimated=False,
            source="provider_actual",
        ),
        request_id=f"provider-{id(request)}",
        finish_reason="stop" if error is None else "error",
        error=error,
    )


class ScriptedAdapter:
    def __init__(
        self,
        name: str,
        complete_handler: Callable[
            [NormalizedModelRequest, int], NormalizedModelResponse
        ],
        stream_handler: Callable[
            [NormalizedModelRequest, int], AsyncIterator[NormalizedStreamEvent]
        ]
        | None = None,
    ) -> None:
        self.name = name
        self.complete_handler = complete_handler
        self.stream_handler = stream_handler
        self.complete_calls = 0
        self.stream_calls = 0

    async def complete(
        self, request: NormalizedModelRequest, runtime: Any = None
    ) -> NormalizedModelResponse:
        del runtime
        self.complete_calls += 1
        return self.complete_handler(request, self.complete_calls)

    async def stream(
        self, request: NormalizedModelRequest, runtime: Any = None
    ) -> AsyncIterator[NormalizedStreamEvent]:
        del runtime
        self.stream_calls += 1
        if self.stream_handler is None:
            response = self.complete_handler(request, self.stream_calls)
            yield NormalizedStreamEvent(sequence=1, event="start")
            if response.error is not None:
                yield NormalizedStreamEvent(sequence=2, event="error", error=response.error)
            else:
                yield NormalizedStreamEvent(
                    sequence=2, event="delta", text_delta=response.text
                )
                yield NormalizedStreamEvent(
                    sequence=3, event="usage", usage=response.usage
                )
            yield NormalizedStreamEvent(sequence=4, event="done", finish_reason="stop")
            return
        async for event in self.stream_handler(request, self.stream_calls):
            yield event

    async def list_models(self, runtime: Any) -> list[dict[str, Any]]:
        del runtime
        return []


def test_effective_capability_priority_and_current_configuration() -> None:
    with TestingSessionLocal() as db, db.begin():
        provider, profile = add_model(db, protocol="mock")
        db.add_all(
            [
                models.ModelCapability(
                    model_profile_id=profile.id,
                    capability="streaming",
                    status="unsupported",
                    source="automatic_probe",
                ),
                models.ModelCapability(
                    model_profile_id=profile.id,
                    capability="streaming",
                    status="supported",
                    source="official_metadata",
                ),
                models.ModelCapability(
                    model_profile_id=profile.id,
                    capability="streaming",
                    status="degraded",
                    source="manual_override",
                ),
            ]
        )

    with TestingSessionLocal() as db:
        effective = capabilities.effective_capabilities(db, profile.id)
        streaming = next(
            item for item in effective.capabilities if item.capability == "streaming"
        )
        assert (streaming.status, streaming.source) == ("degraded", "manual_override")
        reverted = capabilities.clear_manual_override(db, profile.id, "streaming")
        db.commit()
        streaming = next(
            item for item in reverted.capabilities if item.capability == "streaming"
        )
        assert (streaming.status, streaming.source) == (
            "unsupported",
            "automatic_probe",
        )
        stored_provider = db.get(models.ProviderAccount, provider.id)
        assert stored_provider is not None
        stored_provider.enabled = False
        db.commit()
        disabled = capabilities.effective_capabilities(db, profile.id)
        assert all(item.status == "unsupported" for item in disabled.capabilities)
        assert any("Provider 当前已停用" in item for item in disabled.warnings)


@pytest.mark.asyncio
async def test_capability_probe_is_bounded_and_cancellable() -> None:
    with TestingSessionLocal() as db, db.begin():
        _, profile = add_model(
            db, protocol="mock", model_name="mock-novel-v1"
        )
    with pytest.raises(PydanticValidationError):
        CapabilityProbeRequest(level="advanced", confirm_advanced=False)

    with TestingSessionLocal() as db:
        result = await capabilities.run_capability_probe(
            db, profile.id, CapabilityProbeRequest(level="standard")
        )
        db.commit()
        assert result.status == "completed"
        assert result.request_count == 3
        assert result.results["streaming"] == "supported"
        assert result.results["json_schema"] == "supported"

        advanced = await capabilities.run_capability_probe(
            db,
            profile.id,
            CapabilityProbeRequest(level="advanced", confirm_advanced=True),
        )
        db.commit()
        assert advanced.status == "completed"
        assert advanced.request_count == 4
        assert advanced.results["tool_calling"] == "unknown"

    async def cancelled() -> bool:
        return True

    with TestingSessionLocal() as db:
        with pytest.raises(asyncio.CancelledError):
            await capabilities.run_capability_probe(
                db,
                profile.id,
                CapabilityProbeRequest(level="basic"),
                is_cancelled=cancelled,
            )
        db.commit()
        cancelled_run = db.scalar(
            select(models.CapabilityProbeRun).order_by(
                models.CapabilityProbeRun.id.desc()
            )
        )
        assert cancelled_run is not None
        assert (cancelled_run.status, cancelled_run.error_code) == (
            "cancelled",
            "cancelled",
        )


@pytest.mark.asyncio
async def test_probe_records_missing_credentials_as_failed_without_calling_provider() -> None:
    adapter = ScriptedAdapter(
        "phase4_probe_auth_adapter",
        lambda request, _count: response_for(request),
    )
    model_gateway.registry.register(adapter)
    with TestingSessionLocal() as db, db.begin():
        provider, profile = add_model(
            db,
            protocol=adapter.name,
            provider_name="Probe Auth Provider",
            model_name="probe-auth-model",
        )
        provider.credential_env_var = "PHASE4_MISSING_PROBE_KEY"
        db.add(
            models.ModelPricing(
                model_profile_id=profile.id,
                input_per_million=0,
                output_per_million=0,
                request_fee=0,
                tool_call_fee=0,
                currency="USD",
                effective_from=datetime.now(timezone.utc) - timedelta(minutes=1),
            )
        )
    with TestingSessionLocal() as db:
        result = await capabilities.run_capability_probe(
            db, profile.id, CapabilityProbeRequest(level="basic")
        )
        assert result.status == "failed"
        assert result.error_code == "authentication"
        assert result.request_count == 0
        assert adapter.complete_calls == 0


def test_safe_degradation_and_json_extraction() -> None:
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
        "additionalProperties": False,
    }
    request = request_for()
    request.response_format = "json"
    request.json_schema = schema
    prepared = prepare_request(
        request,
        {"json_schema": "unsupported", "json_object": "supported"},
        allow_degradation=True,
    )
    assert prepared.structured_mode == "json_object"
    assert prepared.request.json_schema is None
    assert any("本地执行 JSON Schema" in item for item in prepared.warnings)
    assert extract_json_value('prefix {"answer":"ok"} suffix') == {"answer": "ok"}
    assert extract_json_value("not json") is None

    side_effect_request = request_for()
    side_effect_request.tools = [
        NormalizedToolDefinition(
            name="write_database",
            side_effect=True,
            input_schema={"type": "object"},
        )
    ]
    with pytest.raises(ModelControlError, match="副作用"):
        prepare_request(
            side_effect_request,
            {"tool_calling": "unsupported"},
            allow_degradation=True,
        )

    parameter_request = request_for()
    parameter_request.messages.insert(
        0,
        NormalizedMessage(
            role="system",
            content=[NormalizedContentPart(type="text", text="Synthetic system rule")],
        ),
    )
    parameter_request.top_p = 0.8
    parameter_request.temperature = 1.2
    downgraded = prepare_request(
        parameter_request,
        {
            "system_prompt": "emulated",
            "top_p": "unsupported",
            "temperature": "unsupported",
        },
        allow_degradation=True,
    )
    assert all(message.role != "system" for message in downgraded.request.messages)
    assert downgraded.request.top_p is None
    assert downgraded.request.temperature == 0.7
    assert any("System Prompt" in item for item in downgraded.warnings)
    assert any("top_p" in item for item in downgraded.warnings)
    assert any("temperature" in item for item in downgraded.warnings)


def test_context_preflight_thresholds_and_token_source() -> None:
    request = request_for(text="雾港" * 80)
    input_tokens = estimate_input(request).tokens
    total = input_tokens + request.max_tokens
    blocked = context_preflight(request, total)
    assert blocked.blocked is True
    assert blocked.level == "blocked"
    strong = context_preflight(request, int(total / 0.97))
    assert strong.level == "strong_warning"
    warning = context_preflight(request, int(total / 0.85))
    assert warning.level == "warning"
    ok = context_preflight(request, total * 2)
    assert ok.level == "ok"
    assert ok.input.estimated is True
    assert ok.input.source == "local_approximation"


def test_token_source_priority_uses_explicit_tokenizer_then_provider_usage() -> None:
    request = request_for(text="雾港 token source")
    official = estimate_input(
        request,
        tokenizer_name="cl100k_base",
        tokenizer_source="official_tokenizer",
    )
    compatible = estimate_input(
        request,
        tokenizer_name="cl100k_base",
        tokenizer_source="compatible_tokenizer",
    )
    assert official.tokens == compatible.tokens
    assert official.source == "official_tokenizer"
    assert compatible.source == "compatible_tokenizer"
    provider_estimate = normalize_usage(
        NormalizedUsage(
            input_tokens=9,
            output_tokens=3,
            total_tokens=12,
            estimated=True,
            source="provider_estimate",
        ),
        request,
        "output",
        tokenizer_name="cl100k_base",
        tokenizer_source="official_tokenizer",
    )
    assert provider_estimate.source == "provider_estimate"
    provider_actual = normalize_usage(
        provider_estimate.model_copy(update={"estimated": False}),
        request,
        "output",
        tokenizer_name="cl100k_base",
        tokenizer_source="official_tokenizer",
    )
    assert provider_actual.source == "provider_actual"
    with pytest.raises(ModelControlError) as unavailable:
        estimate_input(
            request,
            tokenizer_name="not-a-real-tokenizer",
            tokenizer_source="official_tokenizer",
        )
    assert unavailable.value.error.code == "tokenizer_unavailable"


def test_pricing_history_cost_and_unknown_are_distinct() -> None:
    now = datetime.now(timezone.utc)
    with TestingSessionLocal() as db, db.begin():
        _, profile = add_model(db)
        created = model_control.create_pricing(
            db,
            profile.id,
            ModelPricingWrite(
                input_per_million=2,
                cached_input_per_million=1,
                output_per_million=4,
                reasoning_per_million=5,
                request_fee=0.01,
                tool_call_fee=0.02,
                currency="USD",
                effective_from=now - timedelta(hours=1),
            ),
        )
        assert created.currency == "USD"
        with pytest.raises(Exception, match="区间不能重叠"):
            model_control.create_pricing(
                db,
                profile.id,
                ModelPricingWrite(
                    effective_from=now,
                    request_fee=0,
                    input_per_million=0,
                    output_per_million=0,
                ),
            )

    with TestingSessionLocal() as db:
        pricing = active_pricing(db, profile.id, now)
        usage = NormalizedUsage(
            input_tokens=1_000_000,
            cached_input_tokens=100_000,
            output_tokens=500_000,
            reasoning_tokens=10_000,
            total_tokens=1_510_000,
            estimated=False,
            source="provider_actual",
        )
        cost = estimate_cost(pricing, usage, tool_calls=2)
        assert cost.known is True
        assert cost.amount == pytest.approx(4.0)
        assert cost.breakdown["request"] == 0.01
        assert cost.breakdown["tools"] == 0.04
        pricing.request_fee = None  # type: ignore[union-attr]
        unknown = estimate_cost(pricing, usage, tool_calls=0)
        assert unknown.known is False
        assert unknown.amount is None
        assert unknown.breakdown["request"] is None


@pytest.mark.asyncio
async def test_layered_limiter_queues_times_out_and_cancels() -> None:
    limiter = LayeredRateLimiter()
    descriptor = LimitDescriptor(
        id=1001,
        max_concurrency=1,
        requests_per_minute=None,
        tokens_per_minute=100,
        queue_timeout_seconds=0.05,
    )
    first = await limiter.acquire([descriptor], 10)
    with pytest.raises(ModelControlError) as timeout_error:
        await limiter.acquire([descriptor], 10)
    assert timeout_error.value.error.code == "queue_timeout"

    waiting = asyncio.create_task(
        limiter.acquire(
            [
                LimitDescriptor(
                    id=1001,
                    max_concurrency=1,
                    requests_per_minute=None,
                    tokens_per_minute=100,
                    queue_timeout_seconds=1,
                )
            ],
            10,
        )
    )
    await asyncio.sleep(0.01)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    await first.release(8)
    next_lease = await limiter.acquire([descriptor], 10)
    await next_lease.release(10)

    with pytest.raises(ModelControlError) as impossible:
        await limiter.acquire([descriptor], 101)
    assert impossible.value.error.code == "rate_limit_impossible"

    rpm_descriptor = LimitDescriptor(
        id=1002,
        max_concurrency=None,
        requests_per_minute=1,
        tokens_per_minute=None,
        queue_timeout_seconds=0.02,
    )
    rpm_first = await limiter.acquire([rpm_descriptor], 1)
    await rpm_first.release(1)
    with pytest.raises(ModelControlError) as rpm_timeout:
        await limiter.acquire([rpm_descriptor], 1)
    assert rpm_timeout.value.error.code == "queue_timeout"


def test_all_six_rate_limit_scopes_match_one_request() -> None:
    with TestingSessionLocal() as db, db.begin():
        provider, profile = add_model(db)
        project = models.Project(title="Scope Project")
        db.add(project)
        db.flush()
        route = models.ModelRoute(
            project_id=project.id,
            name="Scope Route",
            strategy="ordered_fallback",
            required_capabilities_json="[]",
        )
        db.add(route)
        db.flush()
        scopes = {
            "global": "*",
            "project": str(project.id),
            "provider": str(provider.id),
            "model": str(profile.id),
            "route": str(route.id),
            "workflow": "workflow-alpha",
        }
        for scope_type, scope_key in scopes.items():
            db.add(
                models.RateLimitPolicy(
                    scope_type=scope_type,
                    scope_key=scope_key,
                    max_concurrency=1,
                    queue_timeout_seconds=1,
                )
            )
    with TestingSessionLocal() as db:
        descriptors = matching_rate_limits(
            db,
            LimitContext(
                project_id=project.id,
                provider_id=provider.id,
                model_id=profile.id,
                route_id=route.id,
                workflow_id="workflow-alpha",
            ),
        )
        assert len(descriptors) == 6


def test_retry_after_and_exponential_backoff() -> None:
    retry_after = NormalizedProviderError(
        code="rate_limit",
        message="slow down",
        retryable=True,
        retry_after_seconds=3.5,
    )
    assert retry_delay(0, retry_after) == 3.5
    transient = NormalizedProviderError(
        code="timeout", message="timeout", retryable=True
    )
    first = retry_delay(0, transient, jitter=0.5)
    second = retry_delay(1, transient, jitter=0.5)
    assert first == pytest.approx(0.25)
    assert second == pytest.approx(0.5)


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
async def test_project_daily_and_route_run_budgets_include_saved_usage() -> None:
    with TestingSessionLocal() as db, db.begin():
        provider, profile = add_model(db)
        project = models.Project(title="Budget Project")
        db.add(project)
        db.flush()
        route = models.ModelRoute(
            project_id=project.id,
            name="Budget Route",
            strategy="ordered_fallback",
            required_capabilities_json="[]",
        )
        db.add(route)
        db.flush()
        db.add_all(
            [
                models.BudgetPolicy(
                    scope_type="project_daily",
                    scope_key=str(project.id),
                    max_tokens=100,
                    currency="USD",
                ),
                models.BudgetPolicy(
                    scope_type="route_per_run",
                    scope_key=str(route.id),
                    max_tokens=150,
                    currency="USD",
                ),
                models.ModelInvocation(
                    request_id="saved-budget-usage",
                    project_id=project.id,
                    provider_account_id=provider.id,
                    model_profile_id=profile.id,
                    route_id=route.id,
                    route_run_id="run-1",
                    status="completed",
                    total_tokens=90,
                    cost=0,
                    cost_known=True,
                    started_at=datetime.now(timezone.utc),
                ),
            ]
        )
    known_cost = CostEstimateRead(
        known=True,
        amount=0,
        currency="USD",
        breakdown={},
        pricing_id=None,
    )
    with TestingSessionLocal() as db:
        manager = BudgetManager()
        with pytest.raises(ModelControlError) as daily:
            await manager.reserve(
                db,
                BudgetContext(project.id, route.id, "run-2"),
                tokens=20,
                cost=known_cost,
            )
        assert daily.value.error.code == "budget_exceeded"

    with TestingSessionLocal() as db, db.begin():
        daily_policy = db.scalar(
            select(models.BudgetPolicy).where(
                models.BudgetPolicy.scope_type == "project_daily"
            )
        )
        assert daily_policy is not None
        daily_policy.enabled = False
    with TestingSessionLocal() as db:
        manager = BudgetManager()
        with pytest.raises(ModelControlError) as route_run:
            await manager.reserve(
                db,
                BudgetContext(project.id, route.id, "run-1"),
                tokens=61,
                cost=known_cost,
            )
        assert route_run.value.error.code == "budget_exceeded"
        allowed = await manager.reserve(
            db,
            BudgetContext(project.id, route.id, "run-2"),
            tokens=61,
            cost=known_cost,
        )
        await allowed.release()


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
async def test_budget_blocks_tokens_and_unknown_cost_before_provider_call() -> None:
    adapter = ScriptedAdapter(
        "phase4_budget_adapter",
        lambda request, _count: response_for(request, text="should not execute"),
    )
    model_gateway.registry.register(adapter)
    with TestingSessionLocal() as db, db.begin():
        provider, profile = add_model(
            db,
            protocol=adapter.name,
            provider_name="Budget Provider",
            model_name="budget-model",
        )
        db.add(
            models.BudgetPolicy(
                scope_type="per_request",
                scope_key="*",
                max_tokens=10,
                max_cost=None,
                currency="USD",
                enabled=True,
            )
        )
    with TestingSessionLocal() as db:
        blocked = await model_execution.execute_model(
            db,
            ModelDebugRequest(
                provider_account_id=provider.id,
                model_profile_id=profile.id,
                model=profile.name,
                messages=request_for(profile.name).messages,
                max_tokens=64,
                max_retries=0,
            ),
        )
        assert blocked.error is not None
        assert blocked.error.code == "budget_exceeded"
        assert adapter.complete_calls == 0
        assert db.scalar(select(models.ModelInvocation)) is None

    with TestingSessionLocal() as db, db.begin():
        token_budget = db.scalar(select(models.BudgetPolicy))
        assert token_budget is not None
        token_budget.max_tokens = None
        token_budget.max_cost = 1
        token_budget.revision += 1
    with TestingSessionLocal() as db:
        unknown = await model_execution.execute_model(
            db,
            ModelDebugRequest(
                provider_account_id=provider.id,
                model_profile_id=profile.id,
                model=profile.name,
                messages=request_for(profile.name).messages,
                max_tokens=8,
                max_retries=0,
            ),
        )
        assert unknown.error is not None
        assert unknown.error.code == "budget_unknown_cost"
        assert adapter.complete_calls == 0


@pytest.mark.asyncio
async def test_structured_output_repairs_once_and_records_both_calls() -> None:
    def structured_handler(
        request: NormalizedModelRequest, count: int
    ) -> NormalizedModelResponse:
        return response_for(
            request,
            text="not json" if count == 1 else '{"answer":"repaired"}',
        )

    adapter = ScriptedAdapter("phase4_repair_adapter", structured_handler)
    model_gateway.registry.register(adapter)
    with TestingSessionLocal() as db, db.begin():
        provider, profile = add_model(
            db,
            protocol=adapter.name,
            provider_name="Repair Provider",
            model_name="repair-model",
        )
    with TestingSessionLocal() as db:
        result = await model_execution.execute_model(
            db,
            ModelDebugRequest(
                provider_account_id=provider.id,
                model_profile_id=profile.id,
                model=profile.name,
                messages=request_for(profile.name).messages,
                response_format="json",
                json_schema={
                    "type": "object",
                    "properties": {"answer": {"type": "string"}},
                    "required": ["answer"],
                    "additionalProperties": False,
                },
                max_tokens=64,
                max_retries=0,
            ),
        )
        assert result.error is None
        assert result.structured_data == {"answer": "repaired"}
        assert adapter.complete_calls == 2
        assert any("一次有限修复" in item for item in result.warnings)
        invocations = db.scalars(
            select(models.ModelInvocation).order_by(models.ModelInvocation.id)
        ).all()
        assert [item.status for item in invocations] == ["failed", "completed"]
        assert invocations[0].error_code == "schema_validation"


@pytest.mark.asyncio
async def test_structured_stream_buffers_validation_and_one_repair_before_output() -> None:
    adapter = ScriptedAdapter(
        "phase4_structured_stream_repair_adapter",
        lambda request, count: response_for(
            request,
            text="invalid" if count == 1 else '{"answer":"stream repaired"}',
        ),
    )
    model_gateway.registry.register(adapter)
    with TestingSessionLocal() as db, db.begin():
        provider, profile = add_model(
            db,
            protocol=adapter.name,
            provider_name="Structured Stream Provider",
            model_name="structured-stream-model",
        )
        db.add(
            models.ModelCapability(
                model_profile_id=profile.id,
                capability="streaming",
                status="supported",
                source="manual_override",
            )
        )
    with TestingSessionLocal() as db:
        events = [
            event
            async for event in model_execution.stream_model(
                db,
                ModelDebugRequest(
                    provider_account_id=provider.id,
                    model_profile_id=profile.id,
                    model=profile.name,
                    messages=request_for(profile.name).messages,
                    response_format="json",
                    json_schema={
                        "type": "object",
                        "properties": {"answer": {"type": "string"}},
                        "required": ["answer"],
                        "additionalProperties": False,
                    },
                    stream=True,
                    max_retries=0,
                ),
            )
        ]
        text = "".join(event.text_delta for event in events)
        assert text == '{"answer":"stream repaired"}'
        assert "invalid" not in text
        assert any("先缓冲" in (event.warning or "") for event in events)
        assert adapter.complete_calls == 2
        assert adapter.stream_calls == 0


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
