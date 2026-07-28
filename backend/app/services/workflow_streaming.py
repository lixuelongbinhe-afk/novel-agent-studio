from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass


PersistBatch = Callable[[str], Awaitable[object]]


@dataclass(frozen=True)
class StreamBufferStats:
    received_chunks: int
    received_bytes: int
    persisted_batches: int
    persisted_bytes: int


class StreamOutputBuffer:
    """Batch stream deltas before checkpoint and durable event persistence."""

    def __init__(
        self,
        persist: PersistBatch,
        *,
        flush_interval_seconds: float,
        flush_bytes: int,
    ) -> None:
        if flush_interval_seconds <= 0:
            raise ValueError("flush_interval_seconds must be positive")
        if flush_bytes <= 0:
            raise ValueError("flush_bytes must be positive")
        self._persist = persist
        self._flush_interval_seconds = flush_interval_seconds
        self._flush_bytes = flush_bytes
        self._pending: list[str] = []
        self._pending_bytes = 0
        self._received_chunks = 0
        self._received_bytes = 0
        self._persisted_batches = 0
        self._persisted_bytes = 0
        self._first_batch_persisted = False
        self._timer: asyncio.Task[None] | None = None
        self._background_error: BaseException | None = None
        self._closed = False
        self._lock = asyncio.Lock()

    @property
    def stats(self) -> StreamBufferStats:
        return StreamBufferStats(
            received_chunks=self._received_chunks,
            received_bytes=self._received_bytes,
            persisted_batches=self._persisted_batches,
            persisted_bytes=self._persisted_bytes,
        )

    async def append(self, delta: str) -> None:
        if not delta:
            return
        async with self._lock:
            self._raise_background_error()
            if self._closed:
                raise RuntimeError("stream output buffer is closed")
            delta_bytes = len(delta.encode("utf-8"))
            self._pending.append(delta)
            self._pending_bytes += delta_bytes
            self._received_chunks += 1
            self._received_bytes += delta_bytes
            if not self._first_batch_persisted:
                await self._flush_locked()
                self._first_batch_persisted = True
                return
            if self._timer is None:
                self._timer = asyncio.create_task(
                    self._flush_after_interval(),
                    name="workflow-stream-checkpoint-timer",
                )
            if self._pending_bytes >= self._flush_bytes:
                self._cancel_timer()
                await self._flush_locked()

    async def close(self) -> StreamBufferStats:
        async with self._lock:
            if self._closed:
                return self.stats
            self._closed = True
            self._cancel_timer()
            await self._flush_locked()
            self._raise_background_error()
            return self.stats

    async def _flush_after_interval(self) -> None:
        try:
            await asyncio.sleep(self._flush_interval_seconds)
            async with self._lock:
                self._timer = None
                await self._flush_locked()
        except asyncio.CancelledError:
            return
        except BaseException as exc:
            self._background_error = exc

    async def _flush_locked(self) -> None:
        if not self._pending:
            return
        batch = "".join(self._pending)
        batch_bytes = self._pending_bytes
        self._pending.clear()
        self._pending_bytes = 0
        try:
            await self._persist(batch)
        except BaseException:
            self._pending.insert(0, batch)
            self._pending_bytes += batch_bytes
            raise
        self._persisted_batches += 1
        self._persisted_bytes += batch_bytes

    def _cancel_timer(self) -> None:
        timer = self._timer
        self._timer = None
        if timer is not None and timer is not asyncio.current_task():
            timer.cancel()

    def _raise_background_error(self) -> None:
        if self._background_error is not None:
            error = self._background_error
            self._background_error = None
            raise error
