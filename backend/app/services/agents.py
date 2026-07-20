from __future__ import annotations

import hashlib
import json
from typing import Any, cast

from fastapi import HTTPException
from jsonschema import Draft202012Validator, SchemaError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import models
from app.repositories import get_or_404, require_revision, soft_delete
from app.schemas import (
    AgentBudget,
    AgentDefinitionCreate,
    AgentDefinitionRead,
    AgentDefinitionUpdate,
    AgentParameters,
)
from app.services.safe_templates import SafeTemplateError, validate_template


def list_agents(db: Session, project_id: int) -> list[AgentDefinitionRead]:
    get_or_404(db, models.Project, project_id)
    rows = db.scalars(
        select(models.AgentDefinition)
        .where(
            models.AgentDefinition.project_id == project_id,
            models.AgentDefinition.deleted_at.is_(None),
        )
        .order_by(models.AgentDefinition.name, models.AgentDefinition.id)
    ).all()
    return [agent_read(row) for row in rows]


def read_agent(db: Session, agent_id: int) -> AgentDefinitionRead:
    row = cast(models.AgentDefinition, get_or_404(db, models.AgentDefinition, agent_id))
    return agent_read(row)


def create_agent(db: Session, payload: AgentDefinitionCreate) -> AgentDefinitionRead:
    _validate_agent(db, payload)
    duplicate = db.scalar(
        select(models.AgentDefinition).where(
            models.AgentDefinition.project_id == payload.project_id,
            models.AgentDefinition.name == payload.name,
            models.AgentDefinition.deleted_at.is_(None),
        )
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="同一项目中 Agent 名称不能重复")
    config_hash = agent_config_hash(payload)
    row = models.AgentDefinition(
        **_storage_values(payload), version=1, config_hash=config_hash
    )
    db.add(row)
    db.flush()
    return agent_read(row)


def update_agent(
    db: Session, agent_id: int, payload: AgentDefinitionUpdate
) -> AgentDefinitionRead:
    row = cast(models.AgentDefinition, get_or_404(db, models.AgentDefinition, agent_id))
    require_revision(row, payload.expected_revision)
    _validate_agent(db, payload)
    duplicate = db.scalar(
        select(models.AgentDefinition).where(
            models.AgentDefinition.project_id == payload.project_id,
            models.AgentDefinition.name == payload.name,
            models.AgentDefinition.id != row.id,
            models.AgentDefinition.deleted_at.is_(None),
        )
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail="同一项目中 Agent 名称不能重复")
    next_hash = agent_config_hash(payload)
    for key, value in _storage_values(payload).items():
        setattr(row, key, value)
    if next_hash != row.config_hash:
        row.version += 1
        row.config_hash = next_hash
    row.revision += 1
    db.flush()
    return agent_read(row)


def delete_agent(db: Session, agent_id: int, expected_revision: int) -> None:
    row = cast(models.AgentDefinition, get_or_404(db, models.AgentDefinition, agent_id))
    require_revision(row, expected_revision)
    reference_configs = db.scalars(
        select(models.WorkflowNode.config_json)
        .join(models.Workflow, models.Workflow.id == models.WorkflowNode.workflow_id)
        .where(
            models.WorkflowNode.deleted_at.is_(None),
            models.WorkflowNode.node_type == "agent",
            models.Workflow.deleted_at.is_(None),
        )
    ).all()
    if any(_json_object(value).get("agent_id") == agent_id for value in reference_configs):
        raise HTTPException(status_code=409, detail="Agent 正被工作流引用，不能删除")
    soft_delete(row)
    db.flush()


def agent_read(row: models.AgentDefinition) -> AgentDefinitionRead:
    return AgentDefinitionRead(
        id=row.id,
        project_id=row.project_id,
        name=row.name,
        agent_type=row.agent_type,
        system_prompt=row.system_prompt,
        prompt_template=row.prompt_template,
        input_schema=_json_object(row.input_schema_json),
        output_schema=_json_object(row.output_schema_json),
        output_mode=cast(Any, row.output_mode),
        model_profile_id=row.model_profile_id,
        route_id=row.route_id,
        parameters=AgentParameters.model_validate(_json_object(row.parameters_json)),
        required_capabilities=_json_list(row.required_capabilities_json),
        allow_degradation=row.allow_degradation,
        timeout_seconds=row.timeout_seconds,
        retry_count=row.retry_count,
        budget=AgentBudget.model_validate(_json_object(row.budget_json)),
        enabled=row.enabled,
        version=row.version,
        config_hash=row.config_hash,
        revision=row.revision,
        deleted_at=row.deleted_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def agent_snapshot(row: models.AgentDefinition) -> dict[str, Any]:
    return agent_read(row).model_dump(mode="json")


def agent_config_hash(payload: AgentDefinitionCreate | AgentDefinitionUpdate) -> str:
    critical = payload.model_dump(
        mode="json",
        exclude={"expected_revision", "name", "enabled"},
    )
    encoded = json.dumps(
        critical, ensure_ascii=True, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _storage_values(
    payload: AgentDefinitionCreate | AgentDefinitionUpdate,
) -> dict[str, Any]:
    return {
        "project_id": payload.project_id,
        "name": payload.name.strip(),
        "agent_type": payload.agent_type,
        "system_prompt": payload.system_prompt,
        "prompt_template": payload.prompt_template,
        "input_schema_json": _dump(payload.input_schema),
        "output_schema_json": _dump(payload.output_schema),
        "output_mode": payload.output_mode,
        "model_profile_id": payload.model_profile_id,
        "route_id": payload.route_id,
        "parameters_json": _dump(payload.parameters.model_dump(mode="json")),
        "required_capabilities_json": _dump(payload.required_capabilities),
        "allow_degradation": payload.allow_degradation,
        "timeout_seconds": payload.timeout_seconds,
        "retry_count": payload.retry_count,
        "budget_json": _dump(payload.budget.model_dump(mode="json")),
        "enabled": payload.enabled,
    }


def _validate_agent(
    db: Session, payload: AgentDefinitionCreate | AgentDefinitionUpdate
) -> None:
    get_or_404(db, models.Project, payload.project_id)
    if payload.model_profile_id is not None:
        get_or_404(db, models.ModelProfile, payload.model_profile_id)
    if payload.route_id is not None:
        route = cast(models.ModelRoute, get_or_404(db, models.ModelRoute, payload.route_id))
        if route.project_id is not None and route.project_id != payload.project_id:
            raise HTTPException(status_code=409, detail="Agent 不能引用其他项目的 Route")
    try:
        validate_template(payload.system_prompt)
        validate_template(payload.prompt_template)
        Draft202012Validator.check_schema(payload.input_schema)
        Draft202012Validator.check_schema(payload.output_schema)
    except (SafeTemplateError, SchemaError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    for value, label in (
        (payload.input_schema, "input_schema"),
        (payload.output_schema, "output_schema"),
    ):
        if len(_dump(value).encode("utf-8")) > 500_000:
            raise HTTPException(status_code=413, detail=f"{label} 超过 500 KB")


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_object(value: str) -> dict[str, Any]:
    parsed = json.loads(value)
    return parsed if isinstance(parsed, dict) else {}


def _json_list(value: str) -> list[str]:
    parsed = json.loads(value)
    return [str(item) for item in parsed] if isinstance(parsed, list) else []
