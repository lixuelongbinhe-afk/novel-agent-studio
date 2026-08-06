from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app import models
from app.schemas import (
    CostEstimateRead,
    ModelDebugRequest,
    ModelPricingWrite,
    NormalizedUsage,
)
from app.services import model_control, model_execution, model_gateway
from app.services.control_errors import ModelControlError
from app.services.usage_control import (
    BudgetContext,
    BudgetManager,
    active_pricing,
    context_preflight,
    estimate_cost,
    estimate_input,
    normalize_usage,
)


from tests.model_control_helpers import (
    ScriptedAdapter,
    TestingSessionLocal,
    add_model,
    clean_database as clean_database,
    request_for,
    response_for,
)


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
