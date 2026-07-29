from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from app.core.config import get_settings
from app.core.logging_config import cleanup_log_files
from app.database import checkpoint_sqlite
from app.services.storage_management import cleanup_storage
from app.services.workflow_events import event_bus


logger = logging.getLogger(__name__)
MaintenanceCycle = Callable[[], Awaitable[None]]


class RuntimeMaintenance:
    """Run bounded housekeeping while the desktop process stays open."""

    def __init__(self, *, interval_seconds: float, cycle: MaintenanceCycle) -> None:
        if interval_seconds <= 0:
            raise ValueError("interval_seconds must be positive")
        self._interval_seconds = interval_seconds
        self._cycle = cycle
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop = asyncio.Event()
        self._task = asyncio.create_task(
            self._run(), name="nas-runtime-maintenance"
        )

    async def shutdown(self) -> None:
        task = self._task
        if task is None:
            return
        self._stop.set()
        await task
        self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(
                    self._stop.wait(), timeout=self._interval_seconds
                )
            except TimeoutError:
                try:
                    await self._cycle()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Periodic runtime maintenance failed")


async def run_maintenance_cycle() -> None:
    settings = get_settings()
    if settings.storage_auto_gc:
        await event_bus.write(
            lambda db: cleanup_storage(db, dry_run=False),
            priority=90,
            correlation_id="runtime-maintenance:storage-cleanup",
        )
    await asyncio.to_thread(cleanup_log_files, delete_all=False)
    await asyncio.to_thread(checkpoint_sqlite, truncate=False)


_settings = get_settings()
runtime_maintenance = RuntimeMaintenance(
    interval_seconds=_settings.runtime_maintenance_interval_seconds,
    cycle=run_maintenance_cycle,
)
