from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.approval_runtime import ApprovalSignalBus
from app.services.runtime_maintenance import RuntimeMaintenance
from app.core import logging_config


@pytest.mark.asyncio
async def test_runtime_maintenance_repeats_and_stops_cleanly() -> None:
    calls = 0
    called = asyncio.Event()

    async def cycle() -> None:
        nonlocal calls
        calls += 1
        called.set()

    maintenance = RuntimeMaintenance(interval_seconds=0.01, cycle=cycle)
    maintenance.start()
    maintenance.start()
    await asyncio.wait_for(called.wait(), timeout=0.5)
    await maintenance.shutdown()
    calls_after_shutdown = calls
    await asyncio.sleep(0.03)

    assert calls_after_shutdown >= 1
    assert calls == calls_after_shutdown


@pytest.mark.asyncio
async def test_runtime_maintenance_survives_failed_cycle() -> None:
    calls = 0
    recovered = asyncio.Event()

    async def cycle() -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("temporary maintenance failure")
        recovered.set()

    maintenance = RuntimeMaintenance(interval_seconds=0.01, cycle=cycle)
    maintenance.start()
    await asyncio.wait_for(recovered.wait(), timeout=0.5)
    await maintenance.shutdown()

    assert calls >= 2


@pytest.mark.asyncio
async def test_approval_signal_bus_does_not_retain_orphan_events() -> None:
    signals = ApprovalSignalBus()
    signals.notify(100)
    assert signals._events == {}

    waiting = asyncio.create_task(signals.wait(101, 0.5))
    await asyncio.sleep(0)
    signals.notify(101)
    assert await waiting is True
    assert signals._events == {}

    assert await signals.wait(102, 0.01) is False
    assert signals._events == {}


def test_periodic_log_cleanup_keeps_the_active_log(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    active = tmp_path / "studio.log"
    rotated = tmp_path / "studio.log.2026-01-01"
    active.write_text("active", encoding="utf-8")
    rotated.write_text("rotated", encoding="utf-8")
    old = (datetime.now(timezone.utc) - timedelta(days=30)).timestamp()
    os.utime(active, (old, old))
    os.utime(rotated, (old, old))
    monkeypatch.setattr(
        logging_config,
        "get_settings",
        lambda: SimpleNamespace(log_path=tmp_path, log_retention_days=14),
    )

    result = logging_config.cleanup_log_files(delete_all=False)

    assert active.read_text(encoding="utf-8") == "active"
    assert not rotated.exists()
    assert result.deleted_files == 1
    assert result.retained_files == 1
