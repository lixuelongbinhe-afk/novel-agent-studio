from __future__ import annotations

import asyncio
import itertools
import queue
import threading
import time
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass, field
from typing import Any, TypeVar, cast

from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session


T = TypeVar("T")
SessionFactory = Callable[[], Session]
WriteOperation = Callable[[Session], T]


class DatabaseWriteQueueClosed(RuntimeError):
    pass


@dataclass(frozen=True)
class DatabaseWriterMetrics:
    queue_length: int
    max_queue_length: int
    submitted: int
    completed: int
    failed: int
    busy_retries: int
    busy_wait_ms: float
    queue_wait_ms: float
    transaction_ms: float


@dataclass(order=True)
class _QueuedWrite:
    priority: int
    sequence: int
    operation: WriteOperation[Any] = field(compare=False)
    future: Future[Any] = field(compare=False)
    created_at: float = field(compare=False)
    correlation_id: str = field(compare=False)


class DatabaseWriter:
    """Execute bounded, prioritized SQLite writes on one dedicated thread."""

    def __init__(
        self,
        session_factory: SessionFactory,
        *,
        max_queue_size: int,
        busy_retries: int,
        busy_retry_base_seconds: float = 0.02,
    ) -> None:
        if max_queue_size <= 0:
            raise ValueError("max_queue_size must be positive")
        if busy_retries < 0:
            raise ValueError("busy_retries must not be negative")
        self._session_factory = session_factory
        self._queue: queue.PriorityQueue[_QueuedWrite] = queue.PriorityQueue(
            maxsize=max_queue_size
        )
        self._busy_retries = busy_retries
        self._busy_retry_base_seconds = busy_retry_base_seconds
        self._sequence = itertools.count()
        self._thread: threading.Thread | None = None
        self._lifecycle_lock = threading.Lock()
        self._metrics_lock = threading.Lock()
        self._accepting = True
        self._stop = threading.Event()
        self._max_queue_length = 0
        self._submitted = 0
        self._completed = 0
        self._failed = 0
        self._busy_retry_count = 0
        self._busy_wait_seconds = 0.0
        self._queue_wait_seconds = 0.0
        self._transaction_seconds = 0.0

    async def submit(
        self,
        operation: WriteOperation[T],
        *,
        priority: int = 50,
        correlation_id: str = "",
    ) -> T:
        self._ensure_started()
        future: Future[T] = Future()
        item = _QueuedWrite(
            priority=priority,
            sequence=next(self._sequence),
            operation=operation,
            future=cast(Future[Any], future),
            created_at=time.perf_counter(),
            correlation_id=correlation_id,
        )
        while True:
            if not self._accepting:
                raise DatabaseWriteQueueClosed("database writer is shutting down")
            try:
                self._queue.put_nowait(item)
                break
            except queue.Full:
                await asyncio.sleep(0.005)
        with self._metrics_lock:
            self._submitted += 1
            self._max_queue_length = max(
                self._max_queue_length, self._queue.qsize()
            )
        # The transaction has been accepted by the writer and may already be
        # running.  Keep cancellation of the caller from cancelling the
        # concurrent future; otherwise the worker could commit successfully
        # and then fail while publishing the result.
        return await asyncio.shield(asyncio.wrap_future(future))

    async def drain(self) -> None:
        await asyncio.to_thread(self._queue.join)

    async def shutdown(self) -> None:
        with self._lifecycle_lock:
            thread = self._thread
            if thread is None:
                return
            self._accepting = False
        await self.drain()
        self._stop.set()
        await asyncio.to_thread(thread.join, 10.0)
        if thread.is_alive():
            raise RuntimeError("database writer did not stop within 10 seconds")
        with self._lifecycle_lock:
            self._thread = None
            self._stop.clear()
            self._accepting = True

    def metrics(self) -> DatabaseWriterMetrics:
        with self._metrics_lock:
            return DatabaseWriterMetrics(
                queue_length=self._queue.qsize(),
                max_queue_length=self._max_queue_length,
                submitted=self._submitted,
                completed=self._completed,
                failed=self._failed,
                busy_retries=self._busy_retry_count,
                busy_wait_ms=round(self._busy_wait_seconds * 1000, 3),
                queue_wait_ms=round(self._queue_wait_seconds * 1000, 3),
                transaction_ms=round(self._transaction_seconds * 1000, 3),
            )

    def _ensure_started(self) -> None:
        with self._lifecycle_lock:
            if not self._accepting:
                raise DatabaseWriteQueueClosed("database writer is shutting down")
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(
                target=self._worker,
                name="nas-database-writer",
                daemon=True,
            )
            self._thread.start()

    def _worker(self) -> None:
        while not self._stop.is_set() or not self._queue.empty():
            try:
                item = self._queue.get(timeout=0.05)
            except queue.Empty:
                continue
            queue_wait = time.perf_counter() - item.created_at
            started = time.perf_counter()
            try:
                result = self._execute(item.operation)
            except BaseException as exc:
                with self._metrics_lock:
                    self._failed += 1
                self._complete_future(item.future, exception=exc)
            else:
                with self._metrics_lock:
                    self._completed += 1
                self._complete_future(item.future, result=result)
            finally:
                elapsed = time.perf_counter() - started
                with self._metrics_lock:
                    self._queue_wait_seconds += queue_wait
                    self._transaction_seconds += elapsed
                self._queue.task_done()

    @staticmethod
    def _complete_future(
        future: Future[Any],
        *,
        result: Any = None,
        exception: BaseException | None = None,
    ) -> None:
        """Publish a write result without allowing a consumer race to kill the worker."""
        if future.cancelled() or future.done():
            return
        try:
            if exception is not None:
                future.set_exception(exception)
            else:
                future.set_result(result)
        except BaseException:
            # A consumer can cancel between the state check and completion.
            # The write thread must remain alive for later queued operations.
            return

    def _execute(self, operation: WriteOperation[T]) -> T:
        for attempt in range(self._busy_retries + 1):
            try:
                with self._session_factory() as session, session.begin():
                    return operation(session)
            except OperationalError as exc:
                message = str(exc).lower()
                if (
                    attempt >= self._busy_retries
                    or "locked" not in message
                    and "busy" not in message
                ):
                    raise
                with self._metrics_lock:
                    self._busy_retry_count += 1
                delay = self._busy_retry_base_seconds * (2**attempt)
                with self._metrics_lock:
                    self._busy_wait_seconds += delay
                time.sleep(delay)
        raise RuntimeError("database writer retry loop exhausted")
