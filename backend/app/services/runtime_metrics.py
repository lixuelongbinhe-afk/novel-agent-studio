from __future__ import annotations

import threading
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

from app.database import sqlite_runtime_metrics
from app.services.database_writer import DatabaseWriterMetrics
from app.services.workflow_scheduler import WorkflowSchedulerMetrics


@dataclass(frozen=True)
class SSEMetrics:
    active_connections: int
    total_connections: int
    disconnect_count: int
    reconnect_count: int
    events_sent: int
    event_delay_ms: float


class SSEMetricsCollector:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active_connections = 0
        self._total_connections = 0
        self._disconnect_count = 0
        self._reconnect_count = 0
        self._events_sent = 0
        self._event_delay_seconds = 0.0

    def connected(self, *, reconnect: bool) -> None:
        with self._lock:
            self._active_connections += 1
            self._total_connections += 1
            if reconnect:
                self._reconnect_count += 1

    def disconnected(self) -> None:
        with self._lock:
            self._active_connections = max(0, self._active_connections - 1)
            self._disconnect_count += 1

    def event_sent(self, created_at: datetime | None) -> None:
        delay = 0.0
        if created_at is not None:
            value = created_at
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            delay = max(0.0, (datetime.now(timezone.utc) - value).total_seconds())
        with self._lock:
            self._events_sent += 1
            self._event_delay_seconds += delay

    def snapshot(self) -> SSEMetrics:
        with self._lock:
            average_delay = (
                self._event_delay_seconds / self._events_sent
                if self._events_sent
                else 0.0
            )
            return SSEMetrics(
                active_connections=self._active_connections,
                total_connections=self._total_connections,
                disconnect_count=self._disconnect_count,
                reconnect_count=self._reconnect_count,
                events_sent=self._events_sent,
                event_delay_ms=round(average_delay * 1000, 3),
            )


sse_metrics = SSEMetricsCollector()


def performance_snapshot(
    writer: DatabaseWriterMetrics,
    scheduler: WorkflowSchedulerMetrics,
) -> dict[str, Any]:
    database = sqlite_runtime_metrics()
    return {
        "workflow": {
            "workflow_queue_wait_time_ms": scheduler.queue_wait_ms,
            "active_node_count": scheduler.active_nodes,
            "queued_node_count": scheduler.queued_nodes,
            "max_active_node_count": scheduler.max_active_nodes,
            "completed_node_leases": scheduler.completed_leases,
        },
        "sqlite": {
            **database,
            "database_transaction_duration_ms": writer.transaction_ms,
            "database_write_queue_length": writer.queue_length,
            "database_write_queue_max_length": writer.max_queue_length,
            "database_lock_wait_time_ms": writer.busy_wait_ms,
            "database_busy_error_count": writer.busy_retries,
            "database_write_failed_count": writer.failed,
        },
        "sse": asdict(sse_metrics.snapshot()),
    }
