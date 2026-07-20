from __future__ import annotations

import asyncio
import random
import time
import uuid
import weakref
from collections import deque
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.schemas import NormalizedProviderError
from app.services.control_errors import ModelControlError


@dataclass(frozen=True)
class LimitContext:
    project_id: int | None
    provider_id: int
    model_id: int
    route_id: int | None
    workflow_id: str | None


@dataclass(frozen=True)
class LimitDescriptor:
    id: int
    max_concurrency: int | None
    requests_per_minute: int | None
    tokens_per_minute: int | None
    queue_timeout_seconds: float


@dataclass
class _LimitState:
    active: int = 0
    requests: deque[tuple[str, float]] = field(default_factory=deque)
    tokens: dict[str, tuple[float, int]] = field(default_factory=dict)


@dataclass
class RateLimitLease:
    manager: LayeredRateLimiter
    lease_id: str
    descriptors: list[LimitDescriptor]
    queue_ms: int
    released: bool = False

    async def release(self, consumed_tokens: int) -> None:
        if self.released:
            return
        self.released = True
        await self.manager.release(self, consumed_tokens)


class LayeredRateLimiter:
    def __init__(self) -> None:
        self._condition = asyncio.Condition()
        self._states: dict[int, _LimitState] = {}

    async def acquire(
        self, descriptors: list[LimitDescriptor], estimated_tokens: int
    ) -> RateLimitLease:
        started = time.monotonic()
        lease_id = uuid.uuid4().hex
        if not descriptors:
            return RateLimitLease(self, lease_id, [], 0)
        impossible = [
            item
            for item in descriptors
            if item.tokens_per_minute is not None
            and estimated_tokens > item.tokens_per_minute
        ]
        if impossible:
            raise ModelControlError(
                "rate_limit_impossible",
                "单次请求的预计 Token 已超过 TPM 上限",
                status_code=429,
            )
        timeout = min(item.queue_timeout_seconds for item in descriptors)
        deadline = started + timeout
        async with self._condition:
            while True:
                now = time.monotonic()
                for descriptor in descriptors:
                    self._prune(self._states.setdefault(descriptor.id, _LimitState()), now)
                if all(
                    self._available(descriptor, estimated_tokens)
                    for descriptor in descriptors
                ):
                    for descriptor in descriptors:
                        state = self._states.setdefault(descriptor.id, _LimitState())
                        state.active += 1
                        state.requests.append((lease_id, now))
                        state.tokens[lease_id] = (now, estimated_tokens)
                    return RateLimitLease(
                        self,
                        lease_id,
                        descriptors,
                        max(0, int((now - started) * 1000)),
                    )
                remaining = deadline - now
                if remaining <= 0:
                    raise ModelControlError(
                        "queue_timeout",
                        "请求在分层限流队列中等待超时",
                        retryable=True,
                        status_code=429,
                    )
                wake_after = min(remaining, self._next_wake(descriptors, now))
                try:
                    await asyncio.wait_for(self._condition.wait(), timeout=wake_after)
                except TimeoutError:
                    pass

    async def release(self, lease: RateLimitLease, consumed_tokens: int) -> None:
        now = time.monotonic()
        async with self._condition:
            for descriptor in lease.descriptors:
                state = self._states.get(descriptor.id)
                if state is None:
                    continue
                state.active = max(0, state.active - 1)
                if lease.lease_id in state.tokens:
                    started_at, _ = state.tokens[lease.lease_id]
                    state.tokens[lease.lease_id] = (started_at, max(0, consumed_tokens))
                self._prune(state, now)
            self._condition.notify_all()

    def _available(self, descriptor: LimitDescriptor, estimated_tokens: int) -> bool:
        state = self._states.setdefault(descriptor.id, _LimitState())
        if (
            descriptor.max_concurrency is not None
            and state.active >= descriptor.max_concurrency
        ):
            return False
        if (
            descriptor.requests_per_minute is not None
            and len(state.requests) >= descriptor.requests_per_minute
        ):
            return False
        if descriptor.tokens_per_minute is not None:
            used_tokens = sum(value for _, value in state.tokens.values())
            if used_tokens + estimated_tokens > descriptor.tokens_per_minute:
                return False
        return True

    @staticmethod
    def _prune(state: _LimitState, now: float) -> None:
        cutoff = now - 60
        while state.requests and state.requests[0][1] <= cutoff:
            state.requests.popleft()
        expired = [key for key, (created, _) in state.tokens.items() if created <= cutoff]
        for key in expired:
            state.tokens.pop(key, None)

    def _next_wake(self, descriptors: list[LimitDescriptor], now: float) -> float:
        waits = [1.0]
        for descriptor in descriptors:
            state = self._states.setdefault(descriptor.id, _LimitState())
            if state.requests:
                waits.append(max(0.01, state.requests[0][1] + 60 - now))
            if state.tokens:
                waits.append(
                    max(0.01, min(created for created, _ in state.tokens.values()) + 60 - now)
                )
        return min(waits)


_limiters: weakref.WeakKeyDictionary[
    asyncio.AbstractEventLoop, LayeredRateLimiter
] = weakref.WeakKeyDictionary()


def current_rate_limiter() -> LayeredRateLimiter:
    loop = asyncio.get_running_loop()
    limiter = _limiters.get(loop)
    if limiter is None:
        limiter = LayeredRateLimiter()
        _limiters[loop] = limiter
    return limiter


def matching_rate_limits(db: Session, context: LimitContext) -> list[LimitDescriptor]:
    rows = db.scalars(
        select(models.RateLimitPolicy).where(
            models.RateLimitPolicy.enabled.is_(True),
            models.RateLimitPolicy.deleted_at.is_(None),
        )
    ).all()
    keys = {
        "global": "*",
        "provider": str(context.provider_id),
        "model": str(context.model_id),
    }
    if context.project_id is not None:
        keys["project"] = str(context.project_id)
    if context.route_id is not None:
        keys["route"] = str(context.route_id)
    if context.workflow_id is not None:
        keys["workflow"] = context.workflow_id
    return [
        LimitDescriptor(
            id=row.id,
            max_concurrency=row.max_concurrency,
            requests_per_minute=row.requests_per_minute,
            tokens_per_minute=row.tokens_per_minute,
            queue_timeout_seconds=row.queue_timeout_seconds,
        )
        for row in rows
        if keys.get(row.scope_type) == row.scope_key
    ]


def retry_delay(
    attempt: int,
    error: NormalizedProviderError,
    *,
    base_seconds: float = 0.25,
    max_seconds: float = 8.0,
    jitter: float | None = None,
) -> float:
    if error.retry_after_seconds is not None:
        return min(max_seconds, max(0.0, error.retry_after_seconds))
    random_factor = float(
        random.random() if jitter is None else min(max(jitter, 0), 1)
    )
    exponential = base_seconds * float(2 ** max(0, attempt))
    return float(min(max_seconds, exponential * (0.75 + random_factor * 0.5)))
