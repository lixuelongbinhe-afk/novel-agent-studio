from __future__ import annotations

import asyncio
import time

import httpx
import pytest
from fastapi import FastAPI

from app.services import generation_jobs, workflow_runtime
from app.services.generation_jobs import GenerationLease
from app.services.studio_worker import StudioWorker


@pytest.mark.asyncio
async def test_health_remains_responsive_during_blocking_studio_work() -> None:
    worker = StudioWorker()
    app = FastAPI()

    @app.get("/health")
    async def health() -> dict[str, bool]:
        return {"ok": True}

    @app.get("/generate")
    async def generate() -> dict[str, bool]:
        async def blocking_work() -> dict[str, bool]:
            time.sleep(0.25)
            return {"done": True}

        return await worker.run(blocking_work)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        generation = asyncio.create_task(client.get("/generate"))
        await asyncio.sleep(0.03)
        started = time.perf_counter()
        health_response = await client.get("/health")
        elapsed = time.perf_counter() - started
        assert health_response.status_code == 200
        assert elapsed < 0.1
        assert (await generation).status_code == 200
    await worker.shutdown()


@pytest.mark.asyncio
async def test_generation_lease_wait_does_not_block_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sentinel = object()

    def slow_acquire(*_args: object, **_kwargs: object) -> GenerationLease:
        time.sleep(0.2)
        return sentinel  # type: ignore[return-value]

    monkeypatch.setattr(generation_jobs, "acquire", slow_acquire)
    task = asyncio.create_task(
        generation_jobs.acquire_async(
            object(),  # type: ignore[arg-type]
            project_id=1,
            phase="world",
            chapter_id=None,
            mode="new",
            idempotency_key="key",
            label="label",
            model_name="model",
            model_reason="reason",
        )
    )
    await asyncio.sleep(0.02)
    assert not task.done()
    started = time.perf_counter()
    await asyncio.sleep(0)
    assert time.perf_counter() - started < 0.05
    assert await task is sentinel


@pytest.mark.asyncio
async def test_cancel_polling_offloads_sync_database_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def slow_read(_run_id: int) -> bool:
        time.sleep(0.2)
        return False

    monkeypatch.setattr(workflow_runtime, "_cancel_requested_sync", slow_read)
    task = asyncio.create_task(workflow_runtime._cancel_requested(1))
    await asyncio.sleep(0.02)
    assert not task.done()
    await asyncio.sleep(0)
    assert await task is False
