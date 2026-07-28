from __future__ import annotations

import asyncio
import threading
import time
import weakref
from dataclasses import dataclass, field
from typing import Literal


NodeResourceClass = Literal["general", "context", "database", "waiting"]


class WorkflowSchedulingCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkflowSchedulerMetrics:
    queued_nodes: int
    active_nodes: int
    max_active_nodes: int
    completed_leases: int
    queue_wait_ms: float


@dataclass(eq=False)
class _Waiter:
    run_id: int
    provider_id: int | None
    resource_class: NodeResourceClass
    created_at: float = field(default_factory=time.perf_counter)


@dataclass
class _LoopState:
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    waiters: list[_Waiter] = field(default_factory=list)
    active_global: int = 0
    active_by_run: dict[int, int] = field(default_factory=dict)
    active_by_provider: dict[int, int] = field(default_factory=dict)
    active_context: int = 0
    active_database: int = 0


class WorkflowNodeLease:
    def __init__(
        self,
        controller: WorkflowConcurrencyController,
        state: _LoopState | None,
        waiter: _Waiter | None,
    ) -> None:
        self._controller = controller
        self._state = state
        self._waiter = waiter
        self._released = False

    async def release(self) -> None:
        if self._released:
            return
        self._released = True
        if self._state is not None and self._waiter is not None:
            await self._controller._release(self._state, self._waiter)


class WorkflowConcurrencyController:
    """Fair admission control that does not create tasks before capacity exists."""

    def __init__(
        self,
        *,
        max_global: int,
        max_per_run: int,
        max_per_provider: int,
        max_context_builds: int,
        max_database_tasks: int,
    ) -> None:
        limits = (
            max_global,
            max_per_run,
            max_per_provider,
            max_context_builds,
            max_database_tasks,
        )
        if any(value <= 0 for value in limits):
            raise ValueError("workflow concurrency limits must be positive")
        if max_per_run > max_global:
            raise ValueError("max_per_run must not exceed max_global")
        self.max_global = max_global
        self.max_per_run = max_per_run
        self.max_per_provider = max_per_provider
        self.max_context_builds = max_context_builds
        self.max_database_tasks = max_database_tasks
        self._states: weakref.WeakKeyDictionary[
            asyncio.AbstractEventLoop, _LoopState
        ] = weakref.WeakKeyDictionary()
        self._metrics_lock = threading.Lock()
        self._queued_nodes = 0
        self._active_nodes = 0
        self._max_active_nodes = 0
        self._completed_leases = 0
        self._queue_wait_seconds = 0.0

    async def acquire(
        self,
        *,
        run_id: int,
        provider_id: int | None,
        resource_class: NodeResourceClass,
        cancellation: asyncio.Event,
    ) -> WorkflowNodeLease:
        if resource_class == "waiting":
            return WorkflowNodeLease(self, None, None)
        state = self._state()
        waiter = _Waiter(run_id, provider_id, resource_class)
        async with state.condition:
            state.waiters.append(waiter)
            with self._metrics_lock:
                self._queued_nodes += 1
            try:
                while True:
                    if cancellation.is_set():
                        raise WorkflowSchedulingCancelled
                    first_runnable = next(
                        (item for item in state.waiters if self._fits(state, item)),
                        None,
                    )
                    if first_runnable is waiter:
                        state.waiters.remove(waiter)
                        self._reserve(state, waiter)
                        waited = time.perf_counter() - waiter.created_at
                        with self._metrics_lock:
                            self._queued_nodes -= 1
                            self._active_nodes += 1
                            self._max_active_nodes = max(
                                self._max_active_nodes, self._active_nodes
                            )
                            self._queue_wait_seconds += waited
                        return WorkflowNodeLease(self, state, waiter)
                    try:
                        await asyncio.wait_for(state.condition.wait(), timeout=0.1)
                    except TimeoutError:
                        continue
            except BaseException:
                if waiter in state.waiters:
                    state.waiters.remove(waiter)
                    with self._metrics_lock:
                        self._queued_nodes -= 1
                state.condition.notify_all()
                if self._idle(state):
                    self._states.pop(asyncio.get_running_loop(), None)
                raise

    def metrics(self) -> WorkflowSchedulerMetrics:
        with self._metrics_lock:
            return WorkflowSchedulerMetrics(
                queued_nodes=self._queued_nodes,
                active_nodes=self._active_nodes,
                max_active_nodes=self._max_active_nodes,
                completed_leases=self._completed_leases,
                queue_wait_ms=round(self._queue_wait_seconds * 1000, 3),
            )

    def reset_metrics(self) -> None:
        with self._metrics_lock:
            if self._active_nodes or self._queued_nodes:
                raise RuntimeError("cannot reset active workflow scheduler")
            self._max_active_nodes = 0
            self._completed_leases = 0
            self._queue_wait_seconds = 0.0

    def _state(self) -> _LoopState:
        loop = asyncio.get_running_loop()
        state = self._states.get(loop)
        if state is None:
            state = _LoopState()
            self._states[loop] = state
        return state

    def _fits(self, state: _LoopState, waiter: _Waiter) -> bool:
        if state.active_global >= self.max_global:
            return False
        if state.active_by_run.get(waiter.run_id, 0) >= self.max_per_run:
            return False
        if (
            waiter.provider_id is not None
            and state.active_by_provider.get(waiter.provider_id, 0)
            >= self.max_per_provider
        ):
            return False
        if (
            waiter.resource_class == "context"
            and state.active_context >= self.max_context_builds
        ):
            return False
        return not (
            waiter.resource_class == "database"
            and state.active_database >= self.max_database_tasks
        )

    def _reserve(self, state: _LoopState, waiter: _Waiter) -> None:
        state.active_global += 1
        state.active_by_run[waiter.run_id] = (
            state.active_by_run.get(waiter.run_id, 0) + 1
        )
        if waiter.provider_id is not None:
            state.active_by_provider[waiter.provider_id] = (
                state.active_by_provider.get(waiter.provider_id, 0) + 1
            )
        if waiter.resource_class == "context":
            state.active_context += 1
        elif waiter.resource_class == "database":
            state.active_database += 1

    async def _release(self, state: _LoopState, waiter: _Waiter) -> None:
        async with state.condition:
            state.active_global -= 1
            self._decrement(state.active_by_run, waiter.run_id)
            if waiter.provider_id is not None:
                self._decrement(state.active_by_provider, waiter.provider_id)
            if waiter.resource_class == "context":
                state.active_context -= 1
            elif waiter.resource_class == "database":
                state.active_database -= 1
            with self._metrics_lock:
                self._active_nodes -= 1
                self._completed_leases += 1
            state.condition.notify_all()
            if self._idle(state):
                self._states.pop(asyncio.get_running_loop(), None)

    @staticmethod
    def _idle(state: _LoopState) -> bool:
        return not state.waiters and state.active_global == 0

    @staticmethod
    def _decrement(values: dict[int, int], key: int) -> None:
        remaining = values[key] - 1
        if remaining:
            values[key] = remaining
        else:
            values.pop(key)
