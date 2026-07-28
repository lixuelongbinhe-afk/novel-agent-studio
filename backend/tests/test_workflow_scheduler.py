from __future__ import annotations

import asyncio
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app import models
from app.database import Base
from app.schemas import (
    WorkflowCreate,
    WorkflowEdgeWrite,
    WorkflowNodeWrite,
    WorkflowRunCreate,
)
from app.services import workflow_events, workflow_runtime, workflows
from app.services.workflow_scheduler import (
    WorkflowConcurrencyController,
    WorkflowSchedulingCancelled,
)


def test_controller_does_not_reuse_conditions_across_event_loops() -> None:
    controller = WorkflowConcurrencyController(
        max_global=1,
        max_per_run=1,
        max_per_provider=1,
        max_context_builds=1,
        max_database_tasks=1,
    )

    async def exercise(run_id: int) -> None:
        lease = await controller.acquire(
            run_id=run_id,
            provider_id=None,
            resource_class="general",
            cancellation=asyncio.Event(),
        )
        await lease.release()

    for run_id in range(5):
        with asyncio.Runner() as runner:
            runner.run(exercise(run_id))

    metrics = controller.metrics()
    assert metrics.active_nodes == 0
    assert metrics.queued_nodes == 0
    assert metrics.completed_leases == 5


@pytest.fixture
def session_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[sessionmaker[Session], None, None]:
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'workflow-scheduler.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(workflow_runtime, "SessionLocal", factory)
    monkeypatch.setattr(workflow_events, "SessionLocal", factory)
    workflow_runtime.event_bus._locks.clear()
    yield factory
    engine.dispose()


@pytest.mark.asyncio
async def test_hundred_ready_nodes_never_exceed_per_run_limit(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    branch_count = 100
    with session_factory() as db, db.begin():
        project = models.Project(title="百节点并发调度")
        db.add(project)
        db.flush()
        nodes = [WorkflowNodeWrite(key="start", type="start", label="Start")]
        nodes.extend(
            WorkflowNodeWrite(
                key=f"branch_{index}",
                type="text_template",
                label=f"分支 {index}",
                config={"template": f"分支 {index}: {{input.topic}}"},
            )
            for index in range(branch_count)
        )
        nodes.extend(
            [
                WorkflowNodeWrite(
                    key="merge", type="merge", label="Merge", config={"mode": "object"}
                ),
                WorkflowNodeWrite(key="output", type="output", label="Output"),
            ]
        )
        edges = [
            WorkflowEdgeWrite(
                key=f"start_{index}", source="start", target=f"branch_{index}"
            )
            for index in range(branch_count)
        ]
        edges.extend(
            WorkflowEdgeWrite(
                key=f"merge_{index}", source=f"branch_{index}", target="merge"
            )
            for index in range(branch_count)
        )
        edges.append(WorkflowEdgeWrite(key="output", source="merge", target="output"))
        workflow = workflows.create_workflow(
            db,
            WorkflowCreate(
                project_id=project.id,
                name="百节点并行工作流",
                nodes=nodes,
                edges=edges,
            ),
        )
        run = workflows.create_run(
            db, workflow.id, WorkflowRunCreate(input={"topic": "受控并发"})
        )
        run_id = run.id

    original = workflow_runtime._execute_local_node
    active = 0
    max_active = 0

    async def tracked_local_node(
        run_id_value: int,
        node_key: str,
        node: dict[str, Any],
        context: dict[str, Any],
    ) -> Any:
        nonlocal active, max_active
        tracked = node_key.startswith("branch_")
        if tracked:
            active += 1
            max_active = max(max_active, active)
            await asyncio.sleep(0.003)
        try:
            return await original(run_id_value, node_key, node, context)
        finally:
            if tracked:
                active -= 1

    monkeypatch.setattr(workflow_runtime, "_execute_local_node", tracked_local_node)
    workflow_runtime.workflow_concurrency.reset_metrics()
    await workflow_runtime.execute_run(run_id)

    with session_factory() as db:
        result = workflows.read_run(db, run_id)
    metrics = workflow_runtime.workflow_concurrency.metrics()
    assert result.status == "completed", result.error
    assert max_active <= 4
    assert metrics.max_active_nodes <= 4
    assert metrics.completed_leases == branch_count + 3


@pytest.mark.asyncio
async def test_provider_waiter_does_not_block_other_provider() -> None:
    controller = WorkflowConcurrencyController(
        max_global=2,
        max_per_run=2,
        max_per_provider=1,
        max_context_builds=1,
        max_database_tasks=1,
    )
    cancellation = asyncio.Event()
    first = await controller.acquire(
        run_id=1,
        provider_id=10,
        resource_class="general",
        cancellation=cancellation,
    )
    started: list[str] = []

    async def wait_for_provider(label: str, provider_id: int) -> None:
        lease = await controller.acquire(
            run_id=2 if label == "blocked-a" else 3,
            provider_id=provider_id,
            resource_class="general",
            cancellation=cancellation,
        )
        started.append(label)
        await lease.release()

    blocked = asyncio.create_task(wait_for_provider("blocked-a", 10))
    available = asyncio.create_task(wait_for_provider("provider-b", 20))
    await asyncio.wait_for(available, timeout=0.5)
    assert started == ["provider-b"]
    await first.release()
    await asyncio.wait_for(blocked, timeout=0.5)
    assert started == ["provider-b", "blocked-a"]


@pytest.mark.asyncio
async def test_cancelled_admission_releases_queue_within_one_second() -> None:
    controller = WorkflowConcurrencyController(
        max_global=1,
        max_per_run=1,
        max_per_provider=1,
        max_context_builds=1,
        max_database_tasks=1,
    )
    first_cancel = asyncio.Event()
    first = await controller.acquire(
        run_id=1,
        provider_id=10,
        resource_class="general",
        cancellation=first_cancel,
    )
    waiting_cancel = asyncio.Event()
    waiting = asyncio.create_task(
        controller.acquire(
            run_id=2,
            provider_id=20,
            resource_class="general",
            cancellation=waiting_cancel,
        )
    )
    await asyncio.sleep(0.02)
    waiting_cancel.set()
    with pytest.raises(WorkflowSchedulingCancelled):
        await asyncio.wait_for(waiting, timeout=1.0)
    assert controller.metrics().queued_nodes == 0
    await first.release()
    assert controller.metrics().active_nodes == 0
