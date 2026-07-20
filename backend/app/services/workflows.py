from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, cast

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import models
from app.repositories import get_or_404, require_revision, soft_delete
from app.schemas import (
    AgentDefinitionCreate,
    NodeRunAttemptRead,
    NodeRunRead,
    WorkflowCreate,
    WorkflowEdgeWrite,
    WorkflowManifest,
    WorkflowManifestImport,
    WorkflowNodeWrite,
    WorkflowRead,
    WorkflowRunCreate,
    WorkflowRunDerive,
    WorkflowRunEventRead,
    WorkflowRunRead,
    WorkflowRunSnapshotRead,
    WorkflowRunSummaryRead,
    WorkflowSummaryRead,
    WorkflowUpdate,
    WorkflowValidationRead,
)
from app.services import agents, capabilities
from app.services.model_control import ensure_provider_health
from app.services.usage_control import active_pricing
from app.services.workflow_validation import compile_graph, validate_graph


TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled", "interrupted"}


def list_workflows(db: Session, project_id: int) -> list[WorkflowSummaryRead]:
    get_or_404(db, models.Project, project_id)
    rows = db.scalars(
        select(models.Workflow)
        .where(
            models.Workflow.project_id == project_id,
            models.Workflow.deleted_at.is_(None),
        )
        .order_by(models.Workflow.updated_at.desc(), models.Workflow.id.desc())
    ).all()
    workflow_ids = [row.id for row in rows]
    node_count_rows = db.execute(
        select(models.WorkflowNode.workflow_id, func.count(models.WorkflowNode.id))
        .where(
            models.WorkflowNode.workflow_id.in_(workflow_ids or [-1]),
            models.WorkflowNode.deleted_at.is_(None),
        )
        .group_by(models.WorkflowNode.workflow_id)
    ).all()
    node_counts: dict[int, int] = {int(item[0]): int(item[1]) for item in node_count_rows}
    edge_count_rows = db.execute(
        select(models.WorkflowEdge.workflow_id, func.count(models.WorkflowEdge.id))
        .where(
            models.WorkflowEdge.workflow_id.in_(workflow_ids or [-1]),
            models.WorkflowEdge.deleted_at.is_(None),
        )
        .group_by(models.WorkflowEdge.workflow_id)
    ).all()
    edge_counts: dict[int, int] = {int(item[0]): int(item[1]) for item in edge_count_rows}
    result: list[WorkflowSummaryRead] = []
    for row in rows:
        result.append(
            WorkflowSummaryRead(
                id=row.id,
                project_id=row.project_id,
                name=row.name,
                description=row.description,
                enabled=row.enabled,
                revision=row.revision,
                node_count=node_counts.get(row.id, 0),
                edge_count=edge_counts.get(row.id, 0),
                updated_at=row.updated_at,
            )
        )
    return result


def read_workflow(db: Session, workflow_id: int) -> WorkflowRead:
    row = cast(models.Workflow, get_or_404(db, models.Workflow, workflow_id))
    return workflow_read(db, row)


def create_workflow(db: Session, payload: WorkflowCreate) -> WorkflowRead:
    get_or_404(db, models.Project, payload.project_id)
    _validate_graph_storage(payload.nodes, payload.edges)
    duplicate = db.scalar(
        select(models.Workflow).where(
            models.Workflow.project_id == payload.project_id,
            models.Workflow.name == payload.name,
            models.Workflow.deleted_at.is_(None),
        )
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="同一项目中工作流名称不能重复")
    row = models.Workflow(
        project_id=payload.project_id,
        name=payload.name.strip(),
        description=payload.description,
        enabled=payload.enabled,
    )
    db.add(row)
    db.flush()
    _replace_graph(db, row.id, payload.nodes, payload.edges)
    db.flush()
    return workflow_read(db, row)


def update_workflow(db: Session, workflow_id: int, payload: WorkflowUpdate) -> WorkflowRead:
    row = cast(models.Workflow, get_or_404(db, models.Workflow, workflow_id))
    require_revision(row, payload.expected_revision)
    get_or_404(db, models.Project, payload.project_id)
    _validate_graph_storage(payload.nodes, payload.edges)
    duplicate = db.scalar(
        select(models.Workflow).where(
            models.Workflow.project_id == payload.project_id,
            models.Workflow.name == payload.name,
            models.Workflow.id != row.id,
            models.Workflow.deleted_at.is_(None),
        )
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="同一项目中工作流名称不能重复")
    row.project_id = payload.project_id
    row.name = payload.name.strip()
    row.description = payload.description
    row.enabled = payload.enabled
    row.revision += 1
    _replace_graph(db, row.id, payload.nodes, payload.edges)
    db.flush()
    return workflow_read(db, row)


def delete_workflow(db: Session, workflow_id: int, expected_revision: int) -> None:
    row = cast(models.Workflow, get_or_404(db, models.Workflow, workflow_id))
    require_revision(row, expected_revision)
    active_run = db.scalar(
        select(models.WorkflowRun).where(
            models.WorkflowRun.workflow_id == workflow_id,
            models.WorkflowRun.status.in_(["pending", "running"]),
        )
    )
    if active_run is not None:
        raise HTTPException(status_code=409, detail="工作流有运行中的任务，不能删除")
    soft_delete(row)
    db.flush()


def validate_workflow(db: Session, workflow_id: int) -> WorkflowValidationRead:
    workflow = read_workflow(db, workflow_id)
    return validate_graph(db, workflow.project_id, workflow.nodes, workflow.edges)


def create_run(db: Session, workflow_id: int, payload: WorkflowRunCreate) -> WorkflowRunRead:
    workflow = cast(models.Workflow, get_or_404(db, models.Workflow, workflow_id))
    if not workflow.enabled:
        raise HTTPException(status_code=409, detail="工作流已停用")
    graph = workflow_read(db, workflow)
    plan = compile_graph(db, workflow.project_id, graph.nodes, graph.edges)
    snapshot = _build_snapshot(db, workflow, graph, plan, payload.input)
    row = models.WorkflowRun(
        workflow_id=workflow.id,
        project_id=workflow.project_id,
        workflow_revision=workflow.revision,
        status="pending",
        source_mode="fresh",
        input_json=_dump(payload.input),
        output_json="null",
        plan_json=_dump(plan),
        snapshot_json=_dump(snapshot),
        error_json="null",
    )
    db.add(row)
    db.flush()
    _create_node_runs(db, row.id, plan)
    _append_event_sync(db, row, "run_created", payload={"plan_hash": plan["hash"]})
    db.flush()
    return run_read(db, row)


def derive_run(db: Session, source_run_id: int, payload: WorkflowRunDerive) -> WorkflowRunRead:
    source = cast(models.WorkflowRun, get_or_404(db, models.WorkflowRun, source_run_id))
    if source.status not in TERMINAL_RUN_STATUSES:
        raise HTTPException(status_code=409, detail="只能从已结束的运行派生")
    plan = _json_object(source.plan_json)
    node_map = cast(dict[str, Any], plan.get("nodes", {}))
    if payload.node_key not in node_map:
        raise HTTPException(status_code=404, detail="派生起点不存在")
    rerun = {payload.node_key}
    if payload.mode in {"retry_descendants", "clone_from_node"}:
        descendants = cast(dict[str, list[str]], plan.get("descendants", {}))
        rerun.update(descendants.get(payload.node_key, []))
    row = models.WorkflowRun(
        workflow_id=source.workflow_id,
        project_id=source.project_id,
        parent_run_id=source.id,
        workflow_revision=source.workflow_revision,
        status="pending",
        source_mode=payload.mode,
        resume_node_key=payload.node_key,
        input_json=source.input_json,
        output_json="null",
        plan_json=source.plan_json,
        snapshot_json=source.snapshot_json,
        error_json="null",
    )
    db.add(row)
    db.flush()
    source_nodes = {
        item.node_key: item
        for item in db.scalars(
            select(models.NodeRun).where(models.NodeRun.workflow_run_id == source.id)
        ).all()
    }
    for key in cast(list[str], plan.get("topological_order", [])):
        node = cast(dict[str, Any], node_map[key])
        previous = source_nodes.get(key)
        copy_previous = (
            key not in rerun
            and previous is not None
            and previous.status in {"completed", "skipped"}
        )
        if copy_previous:
            assert previous is not None
            status_value = previous.status
            activated = previous.activated
            input_json = previous.input_json
            output_json = previous.output_json
            warnings_json = previous.warnings_json
            started_at = previous.started_at
            completed_at = previous.completed_at
        else:
            status_value = "pending"
            activated = False
            input_json = "null"
            output_json = "null"
            warnings_json = "[]"
            started_at = None
            completed_at = None
        db.add(
            models.NodeRun(
                workflow_run_id=row.id,
                node_key=key,
                node_type=str(node["type"]),
                status=status_value,
                activated=activated,
                input_json=input_json,
                output_json=output_json,
                error_json="null",
                warnings_json=warnings_json,
                attempt_count=0,
                started_at=started_at,
                completed_at=completed_at,
            )
        )
    _append_event_sync(
        db,
        row,
        "run_derived",
        payload={
            "parent_run_id": source.id,
            "mode": payload.mode,
            "node_key": payload.node_key,
            "rerun_nodes": sorted(rerun),
        },
    )
    db.flush()
    return run_read(db, row)


def request_cancel(db: Session, run_id: int) -> WorkflowRunRead:
    row = cast(models.WorkflowRun, get_or_404(db, models.WorkflowRun, run_id))
    if row.status in TERMINAL_RUN_STATUSES:
        return run_read(db, row)
    if not row.cancel_requested:
        row.cancel_requested = True
        row.revision += 1
        _append_event_sync(db, row, "cancel_requested")
        db.flush()
    return run_read(db, row)


def list_runs(
    db: Session,
    *,
    project_id: int | None = None,
    workflow_id: int | None = None,
    limit: int = 100,
    before_id: int | None = None,
) -> list[WorkflowRunSummaryRead]:
    stmt = select(models.WorkflowRun)
    if project_id is not None:
        stmt = stmt.where(models.WorkflowRun.project_id == project_id)
    if workflow_id is not None:
        stmt = stmt.where(models.WorkflowRun.workflow_id == workflow_id)
    if before_id is not None:
        stmt = stmt.where(models.WorkflowRun.id < before_id)
    rows = db.scalars(stmt.order_by(models.WorkflowRun.id.desc()).limit(limit)).all()
    return [run_summary(row) for row in rows]


def read_run(db: Session, run_id: int) -> WorkflowRunRead:
    row = cast(models.WorkflowRun, get_or_404(db, models.WorkflowRun, run_id))
    return run_read(db, row)


def read_run_snapshot(db: Session, run_id: int) -> WorkflowRunSnapshotRead:
    row = cast(models.WorkflowRun, get_or_404(db, models.WorkflowRun, run_id))
    return WorkflowRunSnapshotRead(
        run=run_read(db, row),
        snapshot=_json_object(row.snapshot_json),
        plan=_json_object(row.plan_json),
        events=list_events(db, run_id),
    )


def list_events(
    db: Session, run_id: int, *, after: int = 0, limit: int = 2_000
) -> list[WorkflowRunEventRead]:
    get_or_404(db, models.WorkflowRun, run_id)
    rows = db.scalars(
        select(models.WorkflowRunEvent)
        .where(
            models.WorkflowRunEvent.workflow_run_id == run_id,
            models.WorkflowRunEvent.sequence > after,
        )
        .order_by(models.WorkflowRunEvent.sequence)
        .limit(limit)
    ).all()
    return [event_read(row) for row in rows]


def export_manifest(db: Session, workflow_id: int) -> WorkflowManifest:
    workflow = read_workflow(db, workflow_id)
    agent_ids = sorted(
        {
            int(node.config[key])
            for node in workflow.nodes
            for key in ("agent_id", "revision_agent_id")
            if node.type in {"agent", "context_retrieval", "state_extraction", "human_approval"}
            and isinstance(node.config.get(key), int)
        }
    )
    agent_values: list[dict[str, Any]] = []
    for agent_id in agent_ids:
        row = cast(models.AgentDefinition, get_or_404(db, models.AgentDefinition, agent_id))
        value = agents.agent_snapshot(row)
        for key in (
            "id",
            "revision",
            "deleted_at",
            "created_at",
            "updated_at",
            "version",
            "config_hash",
        ):
            value.pop(key, None)
        value["source_id"] = agent_id
        agent_values.append(value)
    return WorkflowManifest(
        name=workflow.name,
        description=workflow.description,
        agents=agent_values,
        nodes=workflow.nodes,
        edges=workflow.edges,
    )


def import_manifest(db: Session, payload: WorkflowManifestImport) -> WorkflowRead:
    get_or_404(db, models.Project, payload.project_id)
    agent_map: dict[int, int] = {}
    for raw in payload.manifest.agents:
        source_id = raw.get("source_id")
        if not isinstance(source_id, int):
            raise HTTPException(status_code=422, detail="Manifest Agent 缺少 source_id")
        value = dict(raw)
        value.pop("source_id", None)
        value["project_id"] = payload.project_id
        value["name"] = _unique_agent_name(
            db, payload.project_id, str(value.get("name", "Imported Agent"))
        )
        created = agents.create_agent(db, AgentDefinitionCreate.model_validate(value))
        agent_map[source_id] = created.id
    nodes: list[WorkflowNodeWrite] = []
    for node in payload.manifest.nodes:
        config = dict(node.config)
        for key in ("agent_id", "revision_agent_id"):
            source_id = config.get(key)
            if source_id is None:
                continue
            if source_id not in agent_map:
                raise HTTPException(
                    status_code=422,
                    detail=f"Manifest 节点 {node.key} 的 Agent 不存在",
                )
            config[key] = agent_map[cast(int, source_id)]
        nodes.append(node.model_copy(update={"config": config}))
    name = _unique_workflow_name(db, payload.project_id, payload.manifest.name)
    return create_workflow(
        db,
        WorkflowCreate(
            project_id=payload.project_id,
            name=name,
            description=payload.manifest.description,
            enabled=False,
            nodes=nodes,
            edges=payload.manifest.edges,
        ),
    )


def mark_interrupted_runs(db: Session) -> int:
    rows = db.scalars(
        select(models.WorkflowRun).where(
            models.WorkflowRun.status.in_(["pending", "running", "waiting_approval"])
        )
    ).all()
    for row in rows:
        row.status = "interrupted"
        row.completed_at = models.utcnow()
        row.error_json = _dump({"code": "process_interrupted", "message": "应用关闭时运行尚未完成"})
        active_nodes = db.scalars(
            select(models.NodeRun).where(
                models.NodeRun.workflow_run_id == row.id,
                models.NodeRun.status.in_(["ready", "running", "waiting_approval"]),
            )
        ).all()
        for node in active_nodes:
            node.status = "cancelled"
            node.completed_at = models.utcnow()
        _append_event_sync(db, row, "run_interrupted")
    db.flush()
    return len(rows)


def ensure_default_mock_workflow(db: Session, project_id: int) -> models.Workflow:
    existing = db.scalar(
        select(models.Workflow).where(
            models.Workflow.project_id == project_id,
            models.Workflow.name == "Mock 长篇创作总编工作流",
            models.Workflow.deleted_at.is_(None),
        )
    )
    if existing is not None:
        return existing
    profile = db.scalar(
        select(models.ModelProfile)
        .join(
            models.ProviderAccount,
            models.ProviderAccount.id == models.ModelProfile.provider_account_id,
        )
        .where(
            models.ProviderAccount.name == "Mock Provider",
            models.ModelProfile.name == "mock-novel-v1",
            models.ModelProfile.deleted_at.is_(None),
        )
    )
    if profile is None:
        raise RuntimeError("Mock model must exist before seeding the default workflow")
    specifications = [
        ("目标分析", "planner", "分析创作目标并列出核心冲突：{input.goal}"),
        ("人物设计", "character", "根据上游目标设计人物动机与关系：{value}"),
        ("世界观设计", "worldbuilding", "根据上游目标整理世界规则与限制：{value}"),
        ("伏笔设计", "foreshadow", "根据上游目标提出可回收伏笔：{value}"),
        ("节奏设计", "pacing", "根据上游目标规划章节节奏：{value}"),
        ("场景规划", "scene_planner", "整合人物、世界、伏笔与节奏，输出场景计划：{value}"),
        ("正文生成", "writer", "依据场景计划撰写正文草稿：{value}"),
        ("连贯性检查", "continuity", "检查草稿的时间、人物与因果连贯性：{value}"),
        ("对白检查", "dialogue", "检查草稿对白的角色区分与潜台词：{value}"),
        ("文风检查", "style", "检查草稿文风、视角与感官细节：{value}"),
        ("总编审校", "editor", "整合三项审校意见，给出最终编辑稿：{value}"),
    ]
    agent_ids: dict[str, int] = {}
    for name, agent_type, prompt in specifications:
        found = db.scalar(
            select(models.AgentDefinition).where(
                models.AgentDefinition.project_id == project_id,
                models.AgentDefinition.name == name,
                models.AgentDefinition.deleted_at.is_(None),
            )
        )
        if found is None:
            created = agents.create_agent(
                db,
                AgentDefinitionCreate.model_validate(
                    {
                        "project_id": project_id,
                        "name": name,
                        "agent_type": agent_type,
                        "system_prompt": "你是小说智能体工作室中的专业协作 Agent。",
                        "prompt_template": prompt,
                        "input_schema": {},
                        "output_schema": {},
                        "output_mode": "text",
                        "model_profile_id": profile.id,
                        "route_id": None,
                        "parameters": {
                            "temperature": 0.7,
                            "top_p": None,
                            "max_tokens": 512,
                            "scenario": "normal",
                        },
                        "required_capabilities": ["basic_text"],
                        "allow_degradation": True,
                        "timeout_seconds": 120,
                        "retry_count": 1,
                        "budget": {
                            "max_tokens": 4096,
                            "max_cost": None,
                            "currency": "USD",
                        },
                        "enabled": True,
                    }
                ),
            )
            agent_ids[name] = created.id
        else:
            agent_ids[name] = found.id
    positions = {
        "start": (0, 280),
        "目标分析": (220, 280),
        "人物设计": (480, 40),
        "世界观设计": (480, 200),
        "伏笔设计": (480, 360),
        "节奏设计": (480, 520),
        "场景规划": (760, 280),
        "正文生成": (1000, 280),
        "连贯性检查": (1240, 100),
        "对白检查": (1240, 280),
        "文风检查": (1240, 460),
        "总编审校": (1500, 280),
        "output": (1740, 280),
    }
    keys = {
        "目标分析": "goal_analysis",
        "人物设计": "character",
        "世界观设计": "worldbuilding",
        "伏笔设计": "foreshadow",
        "节奏设计": "pacing",
        "场景规划": "scene_plan",
        "正文生成": "draft",
        "连贯性检查": "continuity",
        "对白检查": "dialogue",
        "文风检查": "style",
        "总编审校": "editor",
    }
    node_values = [
        WorkflowNodeWrite(
            key="start",
            type="start",
            label="Start",
            position_x=positions["start"][0],
            position_y=positions["start"][1],
        ),
        *[
            WorkflowNodeWrite(
                key=keys[name],
                type="agent",
                label=name,
                position_x=positions[name][0],
                position_y=positions[name][1],
                config={"agent_id": agent_ids[name]},
            )
            for name, _agent_type, _prompt in specifications
        ],
        WorkflowNodeWrite(
            key="output",
            type="output",
            label="Output",
            position_x=positions["output"][0],
            position_y=positions["output"][1],
        ),
    ]
    edge_pairs = [
        ("start", "goal_analysis"),
        ("goal_analysis", "character"),
        ("goal_analysis", "worldbuilding"),
        ("goal_analysis", "foreshadow"),
        ("goal_analysis", "pacing"),
        ("character", "scene_plan"),
        ("worldbuilding", "scene_plan"),
        ("foreshadow", "scene_plan"),
        ("pacing", "scene_plan"),
        ("scene_plan", "draft"),
        ("draft", "continuity"),
        ("draft", "dialogue"),
        ("draft", "style"),
        ("continuity", "editor"),
        ("dialogue", "editor"),
        ("style", "editor"),
        ("editor", "output"),
    ]
    created_workflow = create_workflow(
        db,
        WorkflowCreate(
            project_id=project_id,
            name="Mock 长篇创作总编工作流",
            description="目标分析后并行完成人物、世界观、伏笔和节奏，再经正文与三路审校汇总。",
            enabled=True,
            nodes=node_values,
            edges=[
                WorkflowEdgeWrite(key=f"e{index}", source=source, target=target)
                for index, (source, target) in enumerate(edge_pairs, start=1)
            ],
        ),
    )
    return cast(models.Workflow, get_or_404(db, models.Workflow, created_workflow.id))


def workflow_read(db: Session, row: models.Workflow) -> WorkflowRead:
    node_rows = db.scalars(
        select(models.WorkflowNode)
        .where(
            models.WorkflowNode.workflow_id == row.id,
            models.WorkflowNode.deleted_at.is_(None),
        )
        .order_by(models.WorkflowNode.id)
    ).all()
    edge_rows = db.scalars(
        select(models.WorkflowEdge)
        .where(
            models.WorkflowEdge.workflow_id == row.id,
            models.WorkflowEdge.deleted_at.is_(None),
        )
        .order_by(models.WorkflowEdge.id)
    ).all()
    return WorkflowRead(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        description=row.description,
        enabled=row.enabled,
        nodes=[
            WorkflowNodeWrite(
                key=item.node_key,
                type=cast(Any, item.node_type),
                label=item.label,
                position_x=item.position_x,
                position_y=item.position_y,
                config=_json_object(item.config_json),
            )
            for item in node_rows
        ],
        edges=[
            WorkflowEdgeWrite(
                key=item.edge_key,
                source=item.source_node_key,
                target=item.target_node_key,
                source_handle=item.source_handle,
                target_handle=item.target_handle,
            )
            for item in edge_rows
        ],
        revision=row.revision,
        deleted_at=row.deleted_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def run_read(db: Session, row: models.WorkflowRun) -> WorkflowRunRead:
    nodes = db.scalars(
        select(models.NodeRun)
        .where(models.NodeRun.workflow_run_id == row.id)
        .order_by(models.NodeRun.id)
    ).all()
    plan = _json_object(row.plan_json)
    return WorkflowRunRead(
        id=row.id,
        workflow_id=row.workflow_id,
        project_id=row.project_id,
        parent_run_id=row.parent_run_id,
        workflow_revision=row.workflow_revision,
        status=cast(Any, row.status),
        source_mode=row.source_mode,
        resume_node_key=row.resume_node_key,
        input=_json_object(row.input_json),
        output=_json_value(row.output_json),
        plan_hash=str(plan.get("hash", "")),
        error=_json_value(row.error_json),
        cancel_requested=row.cancel_requested,
        event_sequence=row.event_sequence,
        started_at=row.started_at,
        completed_at=row.completed_at,
        created_at=row.created_at,
        nodes=[node_read(db, node) for node in nodes],
    )


def node_read(db: Session, row: models.NodeRun) -> NodeRunRead:
    attempts = db.scalars(
        select(models.NodeRunAttempt)
        .where(models.NodeRunAttempt.node_run_id == row.id)
        .order_by(models.NodeRunAttempt.attempt_number)
    ).all()
    return NodeRunRead(
        id=row.id,
        workflow_run_id=row.workflow_run_id,
        node_key=row.node_key,
        node_type=row.node_type,
        status=cast(Any, row.status),
        activated=row.activated,
        input=_json_value(row.input_json),
        output=_json_value(row.output_json),
        error=_json_value(row.error_json),
        warnings=_json_string_list(row.warnings_json),
        attempt_count=row.attempt_count,
        started_at=row.started_at,
        completed_at=row.completed_at,
        attempts=[attempt_read(item) for item in attempts],
    )


def attempt_read(row: models.NodeRunAttempt) -> NodeRunAttemptRead:
    return NodeRunAttemptRead(
        id=row.id,
        node_run_id=row.node_run_id,
        attempt_number=row.attempt_number,
        status=row.status,
        input=_json_value(row.input_json),
        output=_json_value(row.output_json),
        partial_output=row.partial_output,
        error=_json_value(row.error_json),
        model_invocation_ids=_json_int_list(row.model_invocation_ids_json),
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        total_tokens=row.total_tokens,
        cost=row.cost,
        cost_known=row.cost_known,
        currency=row.currency,
        started_at=row.started_at,
        completed_at=row.completed_at,
    )


def run_summary(row: models.WorkflowRun) -> WorkflowRunSummaryRead:
    return WorkflowRunSummaryRead(
        id=row.id,
        workflow_id=row.workflow_id,
        project_id=row.project_id,
        parent_run_id=row.parent_run_id,
        status=cast(Any, row.status),
        source_mode=row.source_mode,
        event_sequence=row.event_sequence,
        started_at=row.started_at,
        completed_at=row.completed_at,
        created_at=row.created_at,
    )


def event_read(row: models.WorkflowRunEvent) -> WorkflowRunEventRead:
    return WorkflowRunEventRead(
        sequence=row.sequence,
        event=row.event_type,
        node_key=row.node_key,
        payload=_json_object(row.payload_json),
        created_at=row.created_at,
    )


def _replace_graph(
    db: Session,
    workflow_id: int,
    nodes: list[WorkflowNodeWrite],
    edges: list[WorkflowEdgeWrite],
) -> None:
    for edge_row in db.scalars(
        select(models.WorkflowEdge).where(models.WorkflowEdge.workflow_id == workflow_id)
    ).all():
        db.delete(edge_row)
    for node_row in db.scalars(
        select(models.WorkflowNode).where(models.WorkflowNode.workflow_id == workflow_id)
    ).all():
        db.delete(node_row)
    db.flush()
    db.add_all(
        [
            models.WorkflowNode(
                workflow_id=workflow_id,
                node_key=node.key,
                node_type=node.type,
                label=node.label,
                position_x=node.position_x,
                position_y=node.position_y,
                config_json=_dump(node.config),
            )
            for node in nodes
        ]
    )
    db.add_all(
        [
            models.WorkflowEdge(
                workflow_id=workflow_id,
                edge_key=edge.key,
                source_node_key=edge.source,
                target_node_key=edge.target,
                source_handle=edge.source_handle,
                target_handle=edge.target_handle,
            )
            for edge in edges
        ]
    )


def _validate_graph_storage(nodes: list[WorkflowNodeWrite], edges: list[WorkflowEdgeWrite]) -> None:
    node_keys = [node.key for node in nodes]
    edge_keys = [edge.key for edge in edges]
    if len(node_keys) != len(set(node_keys)):
        raise HTTPException(status_code=422, detail="节点 key 不能重复")
    if len(edge_keys) != len(set(edge_keys)):
        raise HTTPException(status_code=422, detail="边 key 不能重复")
    known = set(node_keys)
    for edge in edges:
        if edge.source not in known or edge.target not in known:
            raise HTTPException(status_code=422, detail=f"边 {edge.key} 引用了不存在的节点")


def _create_node_runs(db: Session, run_id: int, plan: dict[str, Any]) -> None:
    nodes = cast(dict[str, dict[str, Any]], plan["nodes"])
    for key in cast(list[str], plan["topological_order"]):
        db.add(
            models.NodeRun(
                workflow_run_id=run_id,
                node_key=key,
                node_type=str(nodes[key]["type"]),
                status="pending",
            )
        )


def _build_snapshot(
    db: Session,
    workflow: models.Workflow,
    graph: WorkflowRead,
    plan: dict[str, Any],
    run_input: dict[str, Any],
) -> dict[str, Any]:
    agent_ids = {
        int(node.config[key])
        for node in graph.nodes
        for key in ("agent_id", "revision_agent_id")
        if node.type in {"agent", "context_retrieval", "state_extraction", "human_approval"}
        and isinstance(node.config.get(key), int)
    }
    agent_rows = [
        cast(models.AgentDefinition, get_or_404(db, models.AgentDefinition, item))
        for item in sorted(agent_ids)
    ]
    route_ids = {row.route_id for row in agent_rows if row.route_id is not None}
    model_ids = {row.model_profile_id for row in agent_rows if row.model_profile_id is not None}
    model_ids.update(
        int(node.config["model_profile_id"])
        for node in graph.nodes
        if node.type == "context_retrieval" and isinstance(node.config.get("model_profile_id"), int)
    )
    route_values: list[dict[str, Any]] = []
    for route_id in sorted(route_ids):
        route = cast(models.ModelRoute, get_or_404(db, models.ModelRoute, route_id))
        entries = db.scalars(
            select(models.ModelRouteEntry)
            .where(
                models.ModelRouteEntry.route_id == route.id,
                models.ModelRouteEntry.deleted_at.is_(None),
            )
            .order_by(models.ModelRouteEntry.position)
        ).all()
        model_ids.update(entry.model_profile_id for entry in entries if entry.enabled)
        route_values.append(
            {
                "id": route.id,
                "project_id": route.project_id,
                "name": route.name,
                "strategy": route.strategy,
                "required_capabilities": _json_string_list(route.required_capabilities_json),
                "allow_degradation": route.allow_degradation,
                "enabled": route.enabled,
                "revision": route.revision,
                "entries": [
                    {
                        "model_profile_id": entry.model_profile_id,
                        "position": entry.position,
                        "enabled": entry.enabled,
                        "revision": entry.revision,
                    }
                    for entry in entries
                ],
            }
        )
    profiles: list[dict[str, Any]] = []
    provider_ids: set[int] = set()
    capability_values: dict[str, Any] = {}
    pricing_values: dict[str, Any] = {}
    for model_id in sorted(model_ids):
        profile = cast(models.ModelProfile, get_or_404(db, models.ModelProfile, model_id))
        provider_ids.add(profile.provider_account_id)
        profiles.append(
            {
                "id": profile.id,
                "provider_account_id": profile.provider_account_id,
                "name": profile.name,
                "display_name": profile.display_name,
                "context_window": profile.context_window,
                "tokenizer_name": profile.tokenizer_name,
                "tokenizer_source": profile.tokenizer_source,
                "enabled": profile.enabled,
                "revision": profile.revision,
            }
        )
        capability_values[str(profile.id)] = capabilities.effective_capabilities(
            db, profile.id
        ).model_dump(mode="json")
        pricing = active_pricing(db, profile.id)
        pricing_values[str(profile.id)] = (
            {
                "id": pricing.id,
                "input_per_million": pricing.input_per_million,
                "cached_input_per_million": pricing.cached_input_per_million,
                "output_per_million": pricing.output_per_million,
                "reasoning_per_million": pricing.reasoning_per_million,
                "request_fee": pricing.request_fee,
                "tool_call_fee": pricing.tool_call_fee,
                "currency": pricing.currency,
                "effective_from": pricing.effective_from.isoformat(),
                "effective_to": pricing.effective_to.isoformat() if pricing.effective_to else None,
                "revision": pricing.revision,
            }
            if pricing is not None
            else None
        )
    providers: list[dict[str, Any]] = []
    protocols: list[dict[str, Any]] = []
    for provider_id in sorted(provider_ids):
        provider = cast(models.ProviderAccount, get_or_404(db, models.ProviderAccount, provider_id))
        ensure_provider_health(db, provider.id)
        providers.append(
            {
                "id": provider.id,
                "name": provider.name,
                "provider_type": provider.provider_type,
                "base_url": provider.base_url,
                "enabled": provider.enabled,
                "revision": provider.revision,
            }
        )
        protocol = db.scalar(
            select(models.ProtocolConfiguration).where(
                models.ProtocolConfiguration.provider_account_id == provider.id,
                models.ProtocolConfiguration.deleted_at.is_(None),
            )
        )
        if protocol is not None:
            protocols.append(
                {
                    "provider_account_id": provider.id,
                    "protocol": protocol.protocol,
                    "options": _json_object(protocol.options_json),
                    "revision": protocol.revision,
                }
            )
    budget_rows = db.scalars(
        select(models.BudgetPolicy).where(
            models.BudgetPolicy.deleted_at.is_(None),
            models.BudgetPolicy.enabled.is_(True),
        )
    ).all()
    limit_rows = db.scalars(
        select(models.RateLimitPolicy).where(
            models.RateLimitPolicy.deleted_at.is_(None),
            models.RateLimitPolicy.enabled.is_(True),
        )
    ).all()
    context_policy_rows = db.scalars(
        select(models.ContextPolicy).where(
            models.ContextPolicy.project_id == workflow.project_id,
            models.ContextPolicy.deleted_at.is_(None),
            models.ContextPolicy.enabled.is_(True),
        )
    ).all()
    provider_policy_rows = db.scalars(
        select(models.ProviderDataPolicy).where(
            models.ProviderDataPolicy.provider_account_id.in_(provider_ids or {-1}),
            models.ProviderDataPolicy.deleted_at.is_(None),
            models.ProviderDataPolicy.enabled.is_(True),
        )
    ).all()
    return {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": run_input,
        "workflow": graph.model_dump(mode="json"),
        "plan_hash": plan["hash"],
        "agents": [agents.agent_snapshot(row) for row in agent_rows],
        "models": profiles,
        "providers": providers,
        "protocols": protocols,
        "routes": route_values,
        "capabilities": capability_values,
        "pricing": pricing_values,
        "budgets": [_record_dict(row) for row in budget_rows],
        "rate_limits": [_record_dict(row) for row in limit_rows],
        "context_policies": [_record_dict(row) for row in context_policy_rows],
        "provider_data_policies": [_record_dict(row) for row in provider_policy_rows],
    }


def _record_dict(row: Any) -> dict[str, Any]:
    return {
        column.name: _json_compatible(getattr(row, column.name))
        for column in row.__table__.columns
        if column.name not in {"deleted_at"}
    }


def _append_event_sync(
    db: Session,
    run: models.WorkflowRun,
    event_type: str,
    *,
    node_key: str | None = None,
    payload: dict[str, Any] | None = None,
) -> models.WorkflowRunEvent:
    run.event_sequence += 1
    event = models.WorkflowRunEvent(
        workflow_run_id=run.id,
        sequence=run.event_sequence,
        event_type=event_type,
        node_key=node_key,
        payload_json=_dump(payload or {}),
    )
    db.add(event)
    return event


def _unique_agent_name(db: Session, project_id: int, base: str) -> str:
    return _unique_name(db, models.AgentDefinition, project_id, base, "Agent", 160)


def _unique_workflow_name(db: Session, project_id: int, base: str) -> str:
    return _unique_name(db, models.Workflow, project_id, base, "工作流", 180)


def _unique_name(
    db: Session,
    model: type[Any],
    project_id: int,
    base: str,
    fallback: str,
    max_length: int,
) -> str:
    root = (base.strip() or fallback)[:max_length]
    candidate = root
    number = 2
    while (
        db.scalar(
            select(model).where(
                model.project_id == project_id,
                model.name == candidate,
                model.deleted_at.is_(None),
            )
        )
        is not None
    ):
        suffix = f" ({number})"
        candidate = f"{root[: max_length - len(suffix)]}{suffix}"
        number += 1
    return candidate


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_value(value: str) -> Any:
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return None


def _json_object(value: str) -> dict[str, Any]:
    parsed = _json_value(value)
    return parsed if isinstance(parsed, dict) else {}


def _json_string_list(value: str) -> list[str]:
    parsed = _json_value(value)
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _json_int_list(value: str) -> list[int]:
    parsed = _json_value(value)
    return (
        [int(item) for item in parsed if isinstance(item, int)] if isinstance(parsed, list) else []
    )


def _json_compatible(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    return value
