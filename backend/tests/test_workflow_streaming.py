from __future__ import annotations

import asyncio
from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app import models
from app.database import Base
from app.services import workflow_events, workflow_runtime
from app.services.workflow_streaming import StreamOutputBuffer


@pytest.fixture
def stream_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[tuple[sessionmaker[Session], Engine, int, int], None, None]:
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'workflow-streaming.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )
    monkeypatch.setattr(workflow_runtime, "SessionLocal", factory)
    monkeypatch.setattr(workflow_events, "SessionLocal", factory)
    workflow_runtime.event_bus._locks.clear()
    workflow_runtime.event_bus._lock_loops.clear()
    with factory() as db, db.begin():
        project = models.Project(title="流式持久化压力测试")
        db.add(project)
        db.flush()
        workflow = models.Workflow(project_id=project.id, name="批处理工作流")
        db.add(workflow)
        db.flush()
        run = models.WorkflowRun(
            workflow_id=workflow.id,
            project_id=project.id,
            workflow_revision=1,
            status="running",
            plan_json="{}",
            snapshot_json="{}",
        )
        db.add(run)
        db.flush()
        node = models.NodeRun(
            workflow_run_id=run.id,
            node_key="writer",
            node_type="agent",
            status="running",
            attempt_count=1,
        )
        db.add(node)
        db.flush()
        attempt = models.NodeRunAttempt(
            node_run_id=node.id,
            attempt_number=1,
            status="running",
        )
        db.add(attempt)
        db.flush()
        run_id = run.id
        attempt_id = attempt.id
    yield factory, engine, run_id, attempt_id
    engine.dispose()


@pytest.mark.asyncio
async def test_ten_thousand_deltas_use_bounded_checkpoint_transactions(
    stream_database: tuple[sessionmaker[Session], Engine, int, int],
) -> None:
    factory, engine, run_id, attempt_id = stream_database
    commits = 0

    def count_commit(_connection: object) -> None:
        nonlocal commits
        commits += 1

    event.listen(engine, "commit", count_commit)
    buffer = StreamOutputBuffer(
        lambda delta: workflow_runtime.event_bus.emit_stream_checkpoint(
            run_id, attempt_id, "writer", 1, delta
        ),
        flush_interval_seconds=60,
        flush_bytes=8 * 1024,
    )
    delta = "abcdefghij"
    for _ in range(10_000):
        await buffer.append(delta)
    stats = await buffer.close()
    event.remove(engine, "commit", count_commit)

    assert stats.received_chunks == 10_000
    assert stats.received_bytes == 100_000
    assert stats.persisted_bytes == 100_000
    assert stats.persisted_batches <= 14
    assert commits == stats.persisted_batches
    with factory() as db:
        attempt = db.get(models.NodeRunAttempt, attempt_id)
        assert attempt is not None
        assert attempt.partial_output == delta * 10_000
        events = db.scalars(
            select(models.WorkflowRunEvent)
            .where(models.WorkflowRunEvent.workflow_run_id == run_id)
            .order_by(models.WorkflowRunEvent.sequence)
        ).all()
        assert len(events) == stats.persisted_batches
        assert "".join(
            workflow_runtime._json_object(item.payload_json)["delta"]
            for item in events
        ) == delta * 10_000


@pytest.mark.asyncio
async def test_event_bus_releases_per_run_locks_explicitly(
    stream_database: tuple[sessionmaker[Session], Engine, int, int],
) -> None:
    _factory, _engine, run_id, _attempt_id = stream_database

    for _ in range(100):
        await workflow_runtime.event_bus.emit(run_id, "heartbeat")
    assert workflow_runtime.event_bus.active_lock_count() == 1
    workflow_runtime.event_bus.release(run_id)

    assert workflow_runtime.event_bus.active_lock_count() == 0


@pytest.mark.asyncio
async def test_event_bus_uses_one_lock_for_concurrent_emitters(
    stream_database: tuple[sessionmaker[Session], Engine, int, int],
) -> None:
    factory, _engine, run_id, _attempt_id = stream_database
    first = workflow_runtime.event_bus._lock_for(run_id)
    second = workflow_runtime.event_bus._lock_for(run_id)
    assert first is second

    sequences = await asyncio.gather(
        *(workflow_runtime.event_bus.emit(run_id, "heartbeat") for _ in range(20))
    )
    assert sorted(sequences) == list(range(1, 21))
    with factory() as db:
        persisted = db.scalars(
            select(models.WorkflowRunEvent.sequence)
            .where(models.WorkflowRunEvent.workflow_run_id == run_id)
            .order_by(models.WorkflowRunEvent.sequence)
        ).all()
    assert persisted == list(range(1, 21))


@pytest.mark.asyncio
async def test_terminal_status_and_event_are_committed_atomically(
    stream_database: tuple[sessionmaker[Session], Engine, int, int],
) -> None:
    factory, engine, run_id, _attempt_id = stream_database
    commits = 0

    def count_commit(_connection: object) -> None:
        nonlocal commits
        commits += 1

    def finish(db: Session) -> None:
        run = db.get(models.WorkflowRun, run_id)
        assert run is not None
        run.status = "completed"

    event.listen(engine, "commit", count_commit)
    try:
        _result, sequence = await workflow_runtime.event_bus.write_with_event(
            run_id,
            finish,
            "run_completed",
            payload={"output": {"chapter": 1}},
            priority=10,
            correlation_id=f"workflow:{run_id}:complete",
        )
    finally:
        event.remove(engine, "commit", count_commit)

    assert sequence == 1
    assert commits == 1
    with factory() as db:
        run = db.get(models.WorkflowRun, run_id)
        persisted = db.scalars(
            select(models.WorkflowRunEvent).where(
                models.WorkflowRunEvent.workflow_run_id == run_id
            )
        ).all()
    assert run is not None and run.status == "completed"
    assert [item.event_type for item in persisted] == ["run_completed"]


def test_event_bus_replaces_lock_after_event_loop_restart() -> None:
    bus = workflow_events.WorkflowEventBus()

    async def lock_for_current_loop() -> asyncio.Lock:
        return bus._lock_for(42)

    first = asyncio.run(lock_for_current_loop())
    second = asyncio.run(lock_for_current_loop())
    assert first is not second
    bus.release(42)


@pytest.mark.asyncio
async def test_event_bus_shutdown_clears_locks() -> None:
    bus = workflow_events.WorkflowEventBus()
    bus._lock_for(7)
    assert bus.active_lock_count() == 1
    await bus.shutdown()
    assert bus.active_lock_count() == 0


@pytest.mark.asyncio
async def test_stream_buffer_flushes_after_time_threshold() -> None:
    batches: list[str] = []
    persisted = asyncio.Event()

    async def persist(batch: str) -> None:
        batches.append(batch)
        persisted.set()

    buffer = StreamOutputBuffer(
        persist,
        flush_interval_seconds=0.02,
        flush_bytes=1024,
    )
    await buffer.append("尚未达到大小阈值")
    await asyncio.wait_for(persisted.wait(), timeout=0.5)
    stats = await buffer.close()

    assert batches == ["尚未达到大小阈值"]
    assert stats.persisted_batches == 1


@pytest.mark.asyncio
async def test_stream_buffer_close_forces_final_checkpoint() -> None:
    batches: list[str] = []

    async def persist(batch: str) -> None:
        batches.append(batch)

    buffer = StreamOutputBuffer(
        persist,
        flush_interval_seconds=60,
        flush_bytes=1024,
    )
    await buffer.append("取消或失败前必须保存")
    stats = await buffer.close()

    assert batches == ["取消或失败前必须保存"]
    assert stats.received_bytes == stats.persisted_bytes
