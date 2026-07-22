from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app import models
from app.database import Base
from app.services import generation_jobs


def test_concurrent_generation_requests_share_one_active_lease(tmp_path: Path) -> None:
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'generation-race.db').as_posix()}",
        connect_args={"timeout": 10},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False, autoflush=False)
    with factory.begin() as db:
        project = models.Project(title="并发测试")
        db.add(project)
        db.flush()
        project_id = project.id

    barrier = Barrier(2)

    def acquire(key: str) -> tuple[int, bool]:
        with factory() as db:
            barrier.wait(timeout=5)
            lease = generation_jobs.acquire(
                db,
                project_id=project_id,
                phase="world",
                chapter_id=None,
                mode="new",
                idempotency_key=key,
                label="世界观",
                model_name="Mock",
                model_reason="test",
            )
            return lease.job.id, lease.replayed

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(acquire, ("request-a", "request-b")))
        assert results[0][0] == results[1][0]
        assert sorted(replayed for _, replayed in results) == [False, True]
        with factory() as db:
            assert db.scalar(select(func.count(models.GenerationJob.id))) == 1
    finally:
        engine.dispose()


def test_failed_lease_releases_scope_for_next_request(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{(tmp_path / 'generation-state.db').as_posix()}")
    Base.metadata.create_all(engine)
    try:
        with Session(engine, expire_on_commit=False) as db:
            project = models.Project(title="状态测试")
            db.add(project)
            db.commit()
            first = generation_jobs.acquire(
                db,
                project_id=project.id,
                phase="world",
                chapter_id=None,
                mode="new",
                idempotency_key="first",
                label="世界观",
                model_name="Mock",
                model_reason="test",
            )
            generation_jobs.fail(db, first.job.id, "provider timeout")
            second = generation_jobs.acquire(
                db,
                project_id=project.id,
                phase="world",
                chapter_id=None,
                mode="new",
                idempotency_key="second",
                label="世界观",
                model_name="Mock",
                model_reason="retry",
            )
            assert second.replayed is False
            assert second.job.id != first.job.id
            assert db.get(models.GenerationJob, first.job.id).status == "failed"  # type: ignore[union-attr]
    finally:
        engine.dispose()
