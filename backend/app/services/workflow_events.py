from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any, TypeVar

from sqlalchemy import func, update
from sqlalchemy.orm import Session

from app import models
from app.core.config import get_settings
from app.database import SessionLocal
from app.services.database_writer import DatabaseWriter, DatabaseWriterMetrics


T = TypeVar("T")


class WorkflowEventBus:
    """Serialize workflow persistence and publish monotonically ordered events."""

    def __init__(self) -> None:
        self._locks: dict[int, asyncio.Lock] = {}
        settings = get_settings()
        self._writer = DatabaseWriter(
            lambda: SessionLocal(),
            max_queue_size=settings.database_write_queue_size,
            busy_retries=settings.database_busy_retries,
        )

    async def emit(
        self,
        run_id: int,
        event_type: str,
        *,
        node_key: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> int:
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        async with lock:

            def write(db: Session) -> int:
                run = db.get(models.WorkflowRun, run_id)
                if run is None:
                    return 0
                run.event_sequence += 1
                event = models.WorkflowRunEvent(
                    workflow_run_id=run_id,
                    sequence=run.event_sequence,
                    event_type=event_type,
                    node_key=node_key,
                    payload_json=_dump(payload or {}),
                )
                db.add(event)
                db.flush()
                return event.sequence

            return await self._writer.submit(
                write,
                priority=40,
                correlation_id=f"workflow:{run_id}:event:{event_type}",
            )

    async def write(
        self,
        operation: Callable[[Session], T],
        *,
        priority: int,
        correlation_id: str,
    ) -> T:
        return await self._writer.submit(
            operation,
            priority=priority,
            correlation_id=correlation_id,
        )

    async def emit_stream_checkpoint(
        self,
        run_id: int,
        attempt_id: int,
        node_key: str,
        attempt_number: int,
        delta: str,
    ) -> int:
        if not delta:
            return 0
        lock = self._locks.setdefault(run_id, asyncio.Lock())
        async with lock:

            def write(db: Session) -> int:
                updated = db.execute(
                    update(models.NodeRunAttempt)
                    .where(models.NodeRunAttempt.id == attempt_id)
                    .values(
                        partial_output=func.coalesce(
                            models.NodeRunAttempt.partial_output, ""
                        )
                        + delta
                    )
                    .returning(models.NodeRunAttempt.id)
                )
                if updated.scalar_one_or_none() is None:
                    raise RuntimeError("NodeRunAttempt not found")
                run = db.get(models.WorkflowRun, run_id)
                if run is None:
                    raise RuntimeError("WorkflowRun not found")
                run.event_sequence += 1
                event = models.WorkflowRunEvent(
                    workflow_run_id=run_id,
                    sequence=run.event_sequence,
                    event_type="node_output_delta",
                    node_key=node_key,
                    payload_json=_dump(
                        {"attempt": attempt_number, "delta": delta}
                    ),
                )
                db.add(event)
                db.flush()
                return event.sequence

            return await self._writer.submit(
                write,
                priority=30,
                correlation_id=f"workflow:{run_id}:checkpoint:{attempt_id}",
            )

    async def drain(self) -> None:
        await self._writer.drain()

    async def shutdown(self) -> None:
        await self._writer.shutdown()

    def writer_metrics(self) -> DatabaseWriterMetrics:
        return self._writer.metrics()


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


event_bus = WorkflowEventBus()
