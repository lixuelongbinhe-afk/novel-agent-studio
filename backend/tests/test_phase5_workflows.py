from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Generator
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app import models
from app.api import workflows as workflow_api
from app.database import Base, get_db
from app.repositories import create_seed_data
from app.schemas import (
    AgentDefinitionCreate,
    AgentDefinitionUpdate,
    NormalizedModelRequest,
    NormalizedModelResponse,
    NormalizedProviderError,
    NormalizedStreamEvent,
    NormalizedUsage,
    WorkflowCreate,
    WorkflowEdgeWrite,
    WorkflowManifestImport,
    WorkflowNodeWrite,
    WorkflowRunCreate,
    WorkflowRunDerive,
)
from app.services import agents, model_gateway, workflow_runtime, workflow_validation, workflows


@pytest.fixture
def session_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[sessionmaker[Session], None, None]:
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'phase5.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    monkeypatch.setattr(workflow_runtime, "SessionLocal", factory)
    monkeypatch.setattr(workflow_api, "SessionLocal", factory)
    workflow_runtime.event_bus._locks.clear()
    yield factory
    engine.dispose()


def add_project_model(
    db: Session, *, protocol: str = "mock"
) -> tuple[models.Project, models.ProviderAccount, models.ModelProfile]:
    project = models.Project(title="Phase 5 小说", summary="workflow tests")
    db.add(project)
    db.flush()
    provider = models.ProviderAccount(
        name=f"Phase5 {protocol}", provider_type=protocol, enabled=True
    )
    db.add(provider)
    db.flush()
    db.add(
        models.ProtocolConfiguration(
            provider_account_id=provider.id, protocol=protocol, options_json="{}"
        )
    )
    profile = models.ModelProfile(
        provider_account_id=provider.id,
        name=f"{protocol}-model",
        display_name=f"{protocol} model",
        context_window=8192,
        enabled=True,
    )
    db.add(profile)
    db.flush()
    for capability in (
        "basic_text",
        "streaming",
        "system_prompt",
        "temperature",
        "top_p",
        "max_output_tokens",
        "json_object",
        "json_schema",
    ):
        db.add(
            models.ModelCapability(
                model_profile_id=profile.id,
                capability=capability,
                status="supported",
                source="official_metadata",
            )
        )
    return project, provider, profile


def agent_payload(
    project_id: int,
    model_id: int,
    *,
    name: str = "写作 Agent",
    prompt: str = "请处理 {input.topic}",
    scenario: str = "normal",
    retry_count: int = 1,
) -> AgentDefinitionCreate:
    return AgentDefinitionCreate.model_validate(
        {
            "project_id": project_id,
            "name": name,
            "agent_type": "writer",
            "system_prompt": "只输出工作流需要的结果。",
            "prompt_template": prompt,
            "input_schema": {},
            "output_schema": {},
            "output_mode": "text",
            "model_profile_id": model_id,
            "route_id": None,
            "parameters": {
                "temperature": 0.5,
                "top_p": None,
                "max_tokens": 128,
                "scenario": scenario,
            },
            "required_capabilities": ["basic_text"],
            "allow_degradation": True,
            "timeout_seconds": 10,
            "retry_count": retry_count,
            "budget": {"max_tokens": None, "max_cost": None, "currency": "USD"},
            "enabled": True,
        }
    )


def local_branch_graph(project_id: int) -> WorkflowCreate:
    nodes = [
        WorkflowNodeWrite(key="start", type="start", label="Start"),
        WorkflowNodeWrite(
            key="choose",
            type="condition",
            label="选择分支",
            config={"path": "input.use_true", "operator": "equals", "value": True},
        ),
        WorkflowNodeWrite(
            key="true_text",
            type="text_template",
            label="真分支",
            config={"template": "真:{input.name}"},
        ),
        WorkflowNodeWrite(
            key="false_text",
            type="text_template",
            label="假分支",
            config={"template": "假:{input.name}"},
        ),
        WorkflowNodeWrite(
            key="merge", type="merge", label="合并", config={"mode": "object"}
        ),
        WorkflowNodeWrite(key="output", type="output", label="Output"),
    ]
    edges = [
        WorkflowEdgeWrite(key="e1", source="start", target="choose"),
        WorkflowEdgeWrite(
            key="e2", source="choose", target="true_text", source_handle="true"
        ),
        WorkflowEdgeWrite(
            key="e3", source="choose", target="false_text", source_handle="false"
        ),
        WorkflowEdgeWrite(key="e4", source="true_text", target="merge"),
        WorkflowEdgeWrite(key="e5", source="false_text", target="merge"),
        WorkflowEdgeWrite(key="e6", source="merge", target="output"),
    ]
    return WorkflowCreate(
        project_id=project_id,
        name="分支与合并",
        description="local DAG",
        enabled=True,
        nodes=nodes,
        edges=edges,
    )


def parallel_agent_graph(
    project_id: int, first_agent: int, second_agent: int
) -> WorkflowCreate:
    nodes = [
        WorkflowNodeWrite(key="start", type="start", label="Start"),
        WorkflowNodeWrite(
            key="agent_a",
            type="agent",
            label="Agent A",
            config={"agent_id": first_agent},
        ),
        WorkflowNodeWrite(
            key="agent_b",
            type="agent",
            label="Agent B",
            config={"agent_id": second_agent},
        ),
        WorkflowNodeWrite(
            key="merge", type="merge", label="Merge", config={"mode": "object"}
        ),
        WorkflowNodeWrite(key="output", type="output", label="Output"),
    ]
    edges = [
        WorkflowEdgeWrite(key="s-a", source="start", target="agent_a"),
        WorkflowEdgeWrite(key="s-b", source="start", target="agent_b"),
        WorkflowEdgeWrite(key="a-m", source="agent_a", target="merge"),
        WorkflowEdgeWrite(key="b-m", source="agent_b", target="merge"),
        WorkflowEdgeWrite(key="m-o", source="merge", target="output"),
    ]
    return WorkflowCreate(
        project_id=project_id,
        name="真实并行",
        description="parallel agents",
        enabled=True,
        nodes=nodes,
        edges=edges,
    )


def test_agent_versioning_and_safe_templates(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db, db.begin():
        project, _provider, profile = add_project_model(db)
        created = agents.create_agent(db, agent_payload(project.id, profile.id))
        rename_data = created.model_dump(
            exclude={
                "id",
                "version",
                "config_hash",
                "revision",
                "deleted_at",
                "created_at",
                "updated_at",
            }
        )
        rename_data.update(name="改名 Agent", expected_revision=created.revision)
        renamed = agents.update_agent(
            db, created.id, AgentDefinitionUpdate.model_validate(rename_data)
        )
        assert renamed.version == 1
        prompt_data = renamed.model_dump(
            exclude={
                "id",
                "version",
                "config_hash",
                "revision",
                "deleted_at",
                "created_at",
                "updated_at",
            }
        )
        prompt_data.update(
            prompt_template="新版 {input.topic}", expected_revision=renamed.revision
        )
        updated = agents.update_agent(
            db, created.id, AgentDefinitionUpdate.model_validate(prompt_data)
        )
        assert updated.version == 2
        assert updated.config_hash != created.config_hash

        unsafe = agent_payload(
            project.id, profile.id, name="不安全", prompt="{input.__class__}"
        )
        with pytest.raises(HTTPException, match="无效"):
            agents.create_agent(db, unsafe)

        referenced = workflows.create_workflow(
            db,
            WorkflowCreate(
                project_id=project.id,
                name="引用删除保护",
                nodes=[
                    WorkflowNodeWrite(key="start", type="start", label="Start"),
                    WorkflowNodeWrite(
                        key="agent",
                        type="agent",
                        label="Agent",
                        config={"agent_id": updated.id},
                    ),
                    WorkflowNodeWrite(key="output", type="output", label="Output"),
                ],
                edges=[
                    WorkflowEdgeWrite(key="one", source="start", target="agent"),
                    WorkflowEdgeWrite(key="two", source="agent", target="output"),
                ],
            ),
        )
        with pytest.raises(HTTPException, match="正被工作流引用"):
            agents.delete_agent(db, updated.id, updated.revision)
        workflows.delete_workflow(db, referenced.id, referenced.revision)
        agents.delete_agent(db, updated.id, updated.revision)

    with pytest.raises(PydanticValidationError, match="只能选择一个"):
        AgentDefinitionCreate.model_validate(
            {
                **agent_payload(1, 1).model_dump(),
                "route_id": 1,
            }
        )


def test_validator_reports_cycle_path_and_invalid_agent(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db, db.begin():
        project, _provider, _profile = add_project_model(db)
        nodes = [
            WorkflowNodeWrite(key="start", type="start", label="Start"),
            WorkflowNodeWrite(
                key="missing", type="agent", label="Missing", config={"agent_id": 999}
            ),
            WorkflowNodeWrite(key="output", type="output", label="Output"),
        ]
        edges = [
            WorkflowEdgeWrite(key="a", source="start", target="missing"),
            WorkflowEdgeWrite(key="b", source="missing", target="output"),
            WorkflowEdgeWrite(key="c", source="output", target="missing"),
        ]
        result = workflow_validation.validate_graph(db, project.id, nodes, edges)
        assert result.valid is False
        cycle = next(issue for issue in result.issues if issue.code == "cycle")
        assert cycle.path[0] == cycle.path[-1]
        assert {"missing", "output"} <= set(cycle.path)
        assert any(issue.code == "agent_reference" for issue in result.issues)
        assert any(issue.code == "output_outgoing" for issue in result.issues)


@pytest.mark.asyncio
async def test_condition_merge_execution_events_and_derived_run(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db, db.begin():
        project, _provider, _profile = add_project_model(db)
        workflow = workflows.create_workflow(db, local_branch_graph(project.id))
        validation = workflows.validate_workflow(db, workflow.id)
        assert validation.valid is True
        run = workflows.create_run(
            db,
            workflow.id,
            WorkflowRunCreate(input={"use_true": True, "name": "雾港"}),
        )
        run_id = run.id

    await workflow_runtime.execute_run(run_id)
    with session_factory() as db, db.begin():
        completed = workflows.read_run(db, run_id)
        assert completed.status == "completed"
        assert completed.output == {"true_text": "真:雾港"}
        statuses = {item.node_key: item.status for item in completed.nodes}
        assert statuses["true_text"] == "completed"
        assert statuses["false_text"] == "skipped"
        events = workflows.list_events(db, run_id)
        assert [item.sequence for item in events] == list(range(1, len(events) + 1))
        assert events[-1].event == "run_completed"
        derived = workflows.derive_run(
            db,
            run_id,
            WorkflowRunDerive(mode="retry_descendants", node_key="true_text"),
        )
        derived_id = derived.id
        assert derived.parent_run_id == run_id

    await workflow_runtime.execute_run(derived_id)
    with session_factory() as db:
        result = workflows.read_run(db, derived_id)
        assert result.status == "completed"
        assert result.source_mode == "retry_descendants"
        assert result.output == {"true_text": "真:雾港"}


@pytest.mark.asyncio
async def test_independent_agent_nodes_really_overlap(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db, db.begin():
        project, _provider, profile = add_project_model(db)
        first = agents.create_agent(
            db,
            agent_payload(
                project.id, profile.id, name="人物 Agent", scenario="delay"
            ),
        )
        second = agents.create_agent(
            db,
            agent_payload(
                project.id, profile.id, name="世界 Agent", scenario="delay"
            ),
        )
        workflow = workflows.create_workflow(
            db, parallel_agent_graph(project.id, first.id, second.id)
        )
        run = workflows.create_run(
            db, workflow.id, WorkflowRunCreate(input={"topic": "失踪航线"})
        )
        run_id = run.id

    await workflow_runtime.execute_run(run_id)
    with session_factory() as db:
        result = workflows.read_run(db, run_id)
        assert result.status == "completed"
        agent_nodes = [item for item in result.nodes if item.node_key in {"agent_a", "agent_b"}]
        assert all(item.started_at and item.completed_at for item in agent_nodes)
        latest_start = max(cast(Any, item.started_at) for item in agent_nodes)
        earliest_finish = min(cast(Any, item.completed_at) for item in agent_nodes)
        assert latest_start < earliest_finish
        assert all(item.attempts[0].model_invocation_ids for item in agent_nodes)
        assert set(cast(dict[str, Any], result.output)) == {"agent_a", "agent_b"}


@pytest.mark.asyncio
async def test_cancellation_keeps_partial_output_and_releases_call(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db, db.begin():
        project, _provider, profile = add_project_model(db)
        agent = agents.create_agent(
            db,
            agent_payload(
                project.id, profile.id, name="可取消 Agent", scenario="delay"
            ),
        )
        graph = parallel_agent_graph(project.id, agent.id, agent.id)
        graph.name = "取消运行"
        workflow = workflows.create_workflow(db, graph)
        run = workflows.create_run(
            db, workflow.id, WorkflowRunCreate(input={"topic": "长输出"})
        )
        run_id = run.id

    cancellation = asyncio.Event()
    task = asyncio.create_task(
        workflow_runtime.execute_run(run_id, cancel_event=cancellation)
    )
    for _ in range(200):
        with session_factory() as db:
            events = workflows.list_events(db, run_id)
        if any(item.event == "node_output_delta" for item in events):
            cancellation.set()
            break
        await asyncio.sleep(0.01)
    await task
    with session_factory() as db:
        result = workflows.read_run(db, run_id)
        assert result.status == "cancelled"
        attempts = [attempt for node in result.nodes for attempt in node.attempts]
        assert any(attempt.partial_output for attempt in attempts)
        invocation_statuses = db.scalars(
            select(models.ModelInvocation.status).where(
                models.ModelInvocation.workflow_id == str(workflow.id)
            )
        ).all()
        assert "cancelled" in invocation_statuses


class FlakyAdapter:
    name = "phase5_flaky"

    def __init__(self) -> None:
        self.calls = 0

    async def complete(
        self, request: NormalizedModelRequest, runtime: Any = None
    ) -> NormalizedModelResponse:
        del runtime
        return NormalizedModelResponse(
            model=request.model,
            text="recovered",
            usage=NormalizedUsage(
                input_tokens=2,
                output_tokens=2,
                total_tokens=4,
                estimated=False,
                source="provider_actual",
            ),
            request_id="flaky-complete",
        )

    async def stream(
        self, request: NormalizedModelRequest, runtime: Any = None
    ) -> AsyncIterator[NormalizedStreamEvent]:
        del request, runtime
        self.calls += 1
        yield NormalizedStreamEvent(sequence=1, event="start", request_id=f"flaky-{self.calls}")
        if self.calls == 1:
            yield NormalizedStreamEvent(
                sequence=2,
                event="error",
                error=NormalizedProviderError(
                    code="provider_internal",
                    message="first call failed",
                    retryable=True,
                    status_code=500,
                ),
                request_id="flaky-1",
            )
            yield NormalizedStreamEvent(sequence=3, event="done", finish_reason="error")
            return
        yield NormalizedStreamEvent(
            sequence=2, event="delta", text_delta="recovered", request_id="flaky-2"
        )
        yield NormalizedStreamEvent(
            sequence=3,
            event="usage",
            usage=NormalizedUsage(
                input_tokens=2,
                output_tokens=2,
                total_tokens=4,
                estimated=False,
                source="provider_actual",
            ),
        )
        yield NormalizedStreamEvent(sequence=4, event="done", finish_reason="stop")

    async def list_models(self, runtime: Any) -> list[dict[str, Any]]:
        del runtime
        return []


@pytest.mark.asyncio
async def test_agent_retry_creates_independent_attempts(
    session_factory: sessionmaker[Session],
) -> None:
    adapter = FlakyAdapter()
    model_gateway.registry.register(adapter)
    with session_factory() as db, db.begin():
        project, _provider, profile = add_project_model(db, protocol=adapter.name)
        agent = agents.create_agent(
            db,
            agent_payload(
                project.id, profile.id, name="重试 Agent", retry_count=1
            ),
        )
        nodes = [
            WorkflowNodeWrite(key="start", type="start", label="Start"),
            WorkflowNodeWrite(
                key="agent", type="agent", label="Agent", config={"agent_id": agent.id}
            ),
            WorkflowNodeWrite(key="output", type="output", label="Output"),
        ]
        edges = [
            WorkflowEdgeWrite(key="one", source="start", target="agent"),
            WorkflowEdgeWrite(key="two", source="agent", target="output"),
        ]
        workflow = workflows.create_workflow(
            db,
            WorkflowCreate(
                project_id=project.id,
                name="Retry",
                description="",
                nodes=nodes,
                edges=edges,
            ),
        )
        run = workflows.create_run(
            db, workflow.id, WorkflowRunCreate(input={"topic": "retry"})
        )
        run_id = run.id

    await workflow_runtime.execute_run(run_id)
    with session_factory() as db:
        result = workflows.read_run(db, run_id)
        assert result.status == "completed"
        node = next(item for item in result.nodes if item.node_key == "agent")
        assert [item.status for item in node.attempts] == ["failed", "completed"]
        assert node.attempts[0].error["code"] == "provider_internal"
        assert node.output == "recovered"


@pytest.mark.asyncio
async def test_agent_budget_blocks_unknown_cost_before_provider(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db, db.begin():
        project, _provider, profile = add_project_model(db)
        payload = agent_payload(project.id, profile.id)
        value = payload.model_dump()
        value["budget"] = {"max_tokens": None, "max_cost": 0.1, "currency": "USD"}
        agent = agents.create_agent(db, AgentDefinitionCreate.model_validate(value))
        assert agent.budget.max_cost == 0.1
        workflow = workflows.create_workflow(
            db,
            WorkflowCreate(
                project_id=project.id,
                name="费用阻止",
                nodes=[
                    WorkflowNodeWrite(key="start", type="start", label="Start"),
                    WorkflowNodeWrite(
                        key="agent",
                        type="agent",
                        label="Agent",
                        config={"agent_id": agent.id},
                    ),
                    WorkflowNodeWrite(key="output", type="output", label="Output"),
                ],
                edges=[
                    WorkflowEdgeWrite(key="1", source="start", target="agent"),
                    WorkflowEdgeWrite(key="2", source="agent", target="output"),
                ],
            ),
        )
        run = workflows.create_run(
            db, workflow.id, WorkflowRunCreate(input={"topic": "budget"})
        )
        run_id = run.id

    await workflow_runtime.execute_run(run_id)
    with session_factory() as db:
        result = workflows.read_run(db, run_id)
        assert result.status == "failed"
        assert result.error["message"] == "价格未知，不能验证 Agent 费用预算"
        assert db.scalar(select(models.ModelInvocation.id)) is None


def test_manifest_round_trip_remaps_agents_and_imports_disabled(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db, db.begin():
        project, _provider, profile = add_project_model(db)
        first = agents.create_agent(
            db, agent_payload(project.id, profile.id, name="长" * 160)
        )
        nodes = [
            WorkflowNodeWrite(key="start", type="start", label="Start"),
            WorkflowNodeWrite(
                key="agent", type="agent", label="Agent", config={"agent_id": first.id}
            ),
            WorkflowNodeWrite(key="output", type="output", label="Output"),
        ]
        edges = [
            WorkflowEdgeWrite(key="1", source="start", target="agent"),
            WorkflowEdgeWrite(key="2", source="agent", target="output"),
        ]
        original = workflows.create_workflow(
            db,
            WorkflowCreate(
                project_id=project.id,
                name="可移植工作流",
                nodes=nodes,
                edges=edges,
            ),
        )
        manifest = workflows.export_manifest(db, original.id)
        imported = workflows.import_manifest(
            db,
            WorkflowManifestImport(project_id=project.id, manifest=manifest),
        )
        assert imported.enabled is False
        assert imported.name != original.name
        imported_agent_id = next(
            int(node.config["agent_id"])
            for node in imported.nodes
            if node.type == "agent"
        )
        assert imported_agent_id != first.id
        imported_agent = agents.read_agent(db, imported_agent_id)
        assert len(imported_agent.name) == 160
        assert imported_agent.name.endswith("(2)")


@pytest.mark.asyncio
async def test_api_reads_snapshot_and_replays_sse_from_last_event_id(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db, db.begin():
        project, _provider, _profile = add_project_model(db)
        workflow = workflows.create_workflow(db, local_branch_graph(project.id))
        run = workflows.create_run(
            db,
            workflow.id,
            WorkflowRunCreate(input={"use_true": False, "name": "重连"}),
        )
        run_id = run.id

    await workflow_runtime.execute_run(run_id)

    app = FastAPI()
    app.include_router(workflow_api.router, prefix="/api")

    def override_db() -> Generator[Session, None, None]:
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    with TestClient(app) as client:
        run_response = client.get(f"/api/workflow-runs/{run_id}")
        assert run_response.status_code == 200
        assert run_response.json()["status"] == "completed"

        snapshot_response = client.get(
            f"/api/workflow-runs/{run_id}/events?snapshot=true"
        )
        assert snapshot_response.status_code == 200
        assert "event: snapshot" in snapshot_response.text
        assert '"plan_hash"' in snapshot_response.text

        replay_response = client.get(
            f"/api/workflow-runs/{run_id}/events",
            headers={"Last-Event-ID": "3"},
        )
        assert replay_response.status_code == 200
        assert "id: 1\n" not in replay_response.text
        assert "event: run_completed" in replay_response.text


def test_seed_creates_valid_parallel_mock_workflow_and_secret_free_snapshot(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db, db.begin():
        project = create_seed_data(db)
        workflow = db.scalar(
            select(models.Workflow).where(
                models.Workflow.project_id == project.id,
                models.Workflow.name == "Mock 长篇创作总编工作流",
            )
        )
        assert workflow is not None
        validation = workflows.validate_workflow(db, workflow.id)
        assert validation.valid is True
        graph = workflows.read_workflow(db, workflow.id)
        goal_targets = {
            edge.target for edge in graph.edges if edge.source == "goal_analysis"
        }
        assert goal_targets == {"character", "worldbuilding", "foreshadow", "pacing"}
        run = workflows.create_run(
            db, workflow.id, WorkflowRunCreate(input={"goal": "追查失踪航线"})
        )
        snapshot = workflows.read_run_snapshot(db, run.id).snapshot
        assert len(snapshot["agents"]) == 11
        assert all("credential_env_var" not in item for item in snapshot["providers"])
