from __future__ import annotations

import asyncio
import multiprocessing
from pathlib import Path
import sqlite3
import time

import pytest
from sqlalchemy import create_engine, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app import models
from app.database import Base
from app.services.database_writer import DatabaseWriter


def _write_wal_until_terminated(database_path: str, ready_path: str) -> None:
    connection = sqlite3.connect(database_path, timeout=5)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA synchronous=NORMAL")
    connection.execute("CREATE TABLE IF NOT EXISTS durable_rows (value INTEGER)")
    connection.commit()
    for value in range(100_000):
        connection.execute("INSERT INTO durable_rows(value) VALUES (?)", (value,))
        connection.commit()
        if value == 20:
            Path(ready_path).write_text("ready", encoding="ascii")
        time.sleep(0.001)


@pytest.mark.asyncio
async def test_eight_producers_share_one_bounded_sqlite_writer(tmp_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'writer-queue.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db, db.begin():
        project = models.Project(title="Writer Queue", summary="")
        db.add(project)
        db.flush()
        project_id = project.id

    writer = DatabaseWriter(
        lambda: factory(),
        max_queue_size=32,
        busy_retries=3,
    )

    def append_character(db: Session) -> None:
        db.execute(
            update(models.Project)
            .where(models.Project.id == project_id)
            .values(summary=func.coalesce(models.Project.summary, "") + "x")
        )

    async def producer(index: int) -> None:
        for item in range(100):
            await writer.submit(
                append_character,
                priority=20 + index,
                correlation_id=f"producer:{index}:write:{item}",
            )

    await asyncio.gather(*(producer(index) for index in range(8)))
    await writer.shutdown()

    with factory() as db:
        summary = db.scalar(
            select(models.Project.summary).where(models.Project.id == project_id)
        )
    metrics = writer.metrics()
    assert summary == "x" * 800
    assert metrics.submitted == 800
    assert metrics.completed == 800
    assert metrics.failed == 0
    assert metrics.queue_length == 0
    assert metrics.max_queue_length <= 32
    engine.dispose()


@pytest.mark.asyncio
async def test_shutdown_drains_all_accepted_writes(tmp_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'shutdown-flush.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db, db.begin():
        project = models.Project(title="Shutdown Flush", summary="")
        db.add(project)
        db.flush()
        project_id = project.id

    writer = DatabaseWriter(lambda: factory(), max_queue_size=64, busy_retries=3)

    def append_character(db: Session) -> None:
        time.sleep(0.001)
        db.execute(
            update(models.Project)
            .where(models.Project.id == project_id)
            .values(summary=func.coalesce(models.Project.summary, "") + "x")
        )

    writes = [
        asyncio.create_task(
            writer.submit(
                append_character,
                priority=20,
                correlation_id=f"shutdown:{index}",
            )
        )
        for index in range(50)
    ]
    while writer.metrics().submitted < len(writes):
        await asyncio.sleep(0.001)
    await writer.shutdown()
    await asyncio.gather(*writes)

    with factory() as db:
        summary = db.scalar(
            select(models.Project.summary).where(models.Project.id == project_id)
        )
    assert summary == "x" * len(writes)
    assert writer.metrics().queue_length == 0
    engine.dispose()


@pytest.mark.asyncio
async def test_cancelled_submit_does_not_kill_writer_thread(tmp_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'cancelled-submit.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory() as db, db.begin():
        project = models.Project(title="Cancelled Submit", summary="")
        db.add(project)
        db.flush()
        project_id = project.id

    writer = DatabaseWriter(lambda: factory(), max_queue_size=8, busy_retries=3)

    def slow_write(db: Session) -> None:
        time.sleep(0.05)
        db.execute(
            update(models.Project)
            .where(models.Project.id == project_id)
            .values(summary="first")
        )

    first = asyncio.create_task(
        writer.submit(slow_write, correlation_id="cancel:first")
    )
    while writer.metrics().submitted < 1:
        await asyncio.sleep(0.001)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first

    await writer.submit(
        lambda db: db.execute(
            update(models.Project)
            .where(models.Project.id == project_id)
            .values(summary="second")
        ),
        correlation_id="cancel:second",
    )
    await writer.shutdown()

    with factory() as db:
        summary = db.scalar(
            select(models.Project.summary).where(models.Project.id == project_id)
        )
    assert summary == "second"
    assert writer.metrics().completed == 2
    engine.dispose()


def test_forced_process_exit_leaves_wal_database_integral(tmp_path: Path) -> None:
    database_path = tmp_path / "forced-exit.db"
    ready_path = tmp_path / "writer-ready"
    context = multiprocessing.get_context("spawn")
    process = context.Process(
        target=_write_wal_until_terminated,
        args=(str(database_path), str(ready_path)),
    )
    process.start()
    deadline = time.monotonic() + 10
    while not ready_path.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ready_path.exists(), "child writer did not reach the committed checkpoint"
    process.terminate()
    process.join(timeout=10)
    assert not process.is_alive()

    with sqlite3.connect(database_path, timeout=5) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        committed = connection.execute("SELECT COUNT(*) FROM durable_rows").fetchone()
    assert integrity == ("ok",)
    assert committed is not None and committed[0] >= 21


def test_sqlite_connections_enable_concurrency_pragmas(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'pragmas.db').as_posix()}")
    with engine.connect() as connection:
        journal_mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar_one()
        busy_timeout = connection.exec_driver_sql("PRAGMA busy_timeout").scalar_one()
        foreign_keys = connection.exec_driver_sql("PRAGMA foreign_keys").scalar_one()
        synchronous = connection.exec_driver_sql("PRAGMA synchronous").scalar_one()
    assert str(journal_mode).lower() == "wal"
    assert int(busy_timeout) >= 5_000
    assert int(foreign_keys) == 1
    assert int(synchronous) == 1
    engine.dispose()
