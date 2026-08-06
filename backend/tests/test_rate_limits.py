from __future__ import annotations

import asyncio
import time

import pytest

from app import models
from app.schemas import (
    NormalizedProviderError,
)
from app.services.control_errors import ModelControlError
from app.services.rate_limits import (
    LayeredRateLimiter,
    LimitContext,
    LimitDescriptor,
    matching_rate_limits,
    retry_delay,
)


from tests.model_control_helpers import (
    TestingSessionLocal,
    add_model,
    clean_database as clean_database,
)


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


@pytest.mark.asyncio
async def test_layered_limiter_evicts_expired_policy_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    limiter = LayeredRateLimiter()
    started = time.monotonic()
    for policy_id in range(1_000, 1_200):
        descriptor = LimitDescriptor(
            id=policy_id,
            max_concurrency=None,
            requests_per_minute=10,
            tokens_per_minute=1_000,
            queue_timeout_seconds=1,
        )
        lease = await limiter.acquire([descriptor], 1)
        await lease.release(1)
    assert limiter.state_count() == 200

    monkeypatch.setattr("app.services.rate_limits.time.monotonic", lambda: started + 61)
    current = LimitDescriptor(
        id=2_000,
        max_concurrency=1,
        requests_per_minute=None,
        tokens_per_minute=None,
        queue_timeout_seconds=1,
    )
    lease = await limiter.acquire([current], 1)
    assert limiter.state_count() == 1
    await lease.release(1)
    assert limiter.state_count() == 0


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
