from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.database import SessionLocal, get_db
from app.schemas import (
    AgentDefinitionCreate,
    AgentDefinitionRead,
    AgentDefinitionUpdate,
    WorkflowCreate,
    WorkflowManifest,
    WorkflowManifestImport,
    WorkflowRead,
    WorkflowRunCreate,
    WorkflowRunDerive,
    WorkflowRunRead,
    WorkflowRunSnapshotRead,
    WorkflowRunSummaryRead,
    WorkflowSummaryRead,
    WorkflowUpdate,
    WorkflowValidationRead,
)
from app.services import agents, workflows
from app.services.workflow_runtime import workflow_run_manager


router = APIRouter(tags=["agents-workflows"])


@router.get("/agents", response_model=list[AgentDefinitionRead])
def list_agent_definitions(
    project_id: int = Query(..., ge=1), db: Session = Depends(get_db)
) -> list[AgentDefinitionRead]:
    return agents.list_agents(db, project_id)


@router.post(
    "/agents", response_model=AgentDefinitionRead, status_code=status.HTTP_201_CREATED
)
def create_agent_definition(
    payload: AgentDefinitionCreate, db: Session = Depends(get_db)
) -> AgentDefinitionRead:
    with db.begin():
        return agents.create_agent(db, payload)


@router.get("/agents/{agent_id}", response_model=AgentDefinitionRead)
def read_agent_definition(
    agent_id: int, db: Session = Depends(get_db)
) -> AgentDefinitionRead:
    return agents.read_agent(db, agent_id)


@router.put("/agents/{agent_id}", response_model=AgentDefinitionRead)
def update_agent_definition(
    agent_id: int,
    payload: AgentDefinitionUpdate,
    db: Session = Depends(get_db),
) -> AgentDefinitionRead:
    with db.begin():
        return agents.update_agent(db, agent_id, payload)


@router.delete("/agents/{agent_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_agent_definition(
    agent_id: int,
    expected_revision: int = Query(..., ge=1),
    db: Session = Depends(get_db),
) -> Response:
    with db.begin():
        agents.delete_agent(db, agent_id, expected_revision)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/workflows", response_model=list[WorkflowSummaryRead])
def list_workflow_definitions(
    project_id: int = Query(..., ge=1), db: Session = Depends(get_db)
) -> list[WorkflowSummaryRead]:
    return workflows.list_workflows(db, project_id)


@router.post(
    "/workflows", response_model=WorkflowRead, status_code=status.HTTP_201_CREATED
)
def create_workflow_definition(
    payload: WorkflowCreate, db: Session = Depends(get_db)
) -> WorkflowRead:
    with db.begin():
        return workflows.create_workflow(db, payload)


@router.post(
    "/workflows/import", response_model=WorkflowRead, status_code=status.HTTP_201_CREATED
)
def import_workflow_manifest(
    payload: WorkflowManifestImport, db: Session = Depends(get_db)
) -> WorkflowRead:
    with db.begin():
        return workflows.import_manifest(db, payload)


@router.get("/workflows/{workflow_id}", response_model=WorkflowRead)
def read_workflow_definition(
    workflow_id: int, db: Session = Depends(get_db)
) -> WorkflowRead:
    return workflows.read_workflow(db, workflow_id)


@router.put("/workflows/{workflow_id}", response_model=WorkflowRead)
def update_workflow_definition(
    workflow_id: int,
    payload: WorkflowUpdate,
    db: Session = Depends(get_db),
) -> WorkflowRead:
    with db.begin():
        return workflows.update_workflow(db, workflow_id, payload)


@router.delete("/workflows/{workflow_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workflow_definition(
    workflow_id: int,
    expected_revision: int = Query(..., ge=1),
    db: Session = Depends(get_db),
) -> Response:
    with db.begin():
        workflows.delete_workflow(db, workflow_id, expected_revision)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/workflows/{workflow_id}/validate", response_model=WorkflowValidationRead)
def validate_workflow_definition(
    workflow_id: int, db: Session = Depends(get_db)
) -> WorkflowValidationRead:
    return workflows.validate_workflow(db, workflow_id)


@router.get("/workflows/{workflow_id}/manifest", response_model=WorkflowManifest)
def export_workflow_manifest(
    workflow_id: int, db: Session = Depends(get_db)
) -> WorkflowManifest:
    return workflows.export_manifest(db, workflow_id)


@router.post(
    "/workflows/{workflow_id}/runs",
    response_model=WorkflowRunRead,
    status_code=status.HTTP_201_CREATED,
)
async def start_workflow_run(
    workflow_id: int,
    payload: WorkflowRunCreate,
    db: Session = Depends(get_db),
) -> WorkflowRunRead:
    with db.begin():
        result = workflows.create_run(db, workflow_id, payload)
    workflow_run_manager.start(result.id)
    return result


@router.get("/workflow-runs", response_model=list[WorkflowRunSummaryRead])
def list_workflow_runs(
    project_id: int | None = Query(default=None, ge=1),
    workflow_id: int | None = Query(default=None, ge=1),
    limit: int = Query(default=100, ge=1, le=500),
    before_id: int | None = Query(default=None, ge=1),
    db: Session = Depends(get_db),
) -> list[WorkflowRunSummaryRead]:
    return workflows.list_runs(
        db,
        project_id=project_id,
        workflow_id=workflow_id,
        limit=limit,
        before_id=before_id,
    )


@router.get("/workflow-runs/{run_id}", response_model=WorkflowRunRead)
def read_workflow_run(run_id: int, db: Session = Depends(get_db)) -> WorkflowRunRead:
    return workflows.read_run(db, run_id)


@router.get(
    "/workflow-runs/{run_id}/snapshot", response_model=WorkflowRunSnapshotRead
)
def read_workflow_run_snapshot(
    run_id: int, db: Session = Depends(get_db)
) -> WorkflowRunSnapshotRead:
    return workflows.read_run_snapshot(db, run_id)


@router.post("/workflow-runs/{run_id}/cancel", response_model=WorkflowRunRead)
async def cancel_workflow_run(
    run_id: int, db: Session = Depends(get_db)
) -> WorkflowRunRead:
    with db.begin():
        result = workflows.request_cancel(db, run_id)
    workflow_run_manager.signal_cancel(run_id)
    return result


@router.post(
    "/workflow-runs/{run_id}/derive",
    response_model=WorkflowRunRead,
    status_code=status.HTTP_201_CREATED,
)
async def derive_workflow_run(
    run_id: int,
    payload: WorkflowRunDerive,
    db: Session = Depends(get_db),
) -> WorkflowRunRead:
    with db.begin():
        result = workflows.derive_run(db, run_id, payload)
    workflow_run_manager.start(result.id)
    return result


@router.get("/workflow-runs/{run_id}/events/history")
def read_workflow_event_history(
    run_id: int,
    after: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> list[dict[str, object]]:
    return [item.model_dump(mode="json") for item in workflows.list_events(db, run_id, after=after)]


@router.get("/workflow-runs/{run_id}/events")
async def stream_workflow_events(
    run_id: int,
    request: Request,
    after: int = Query(default=0, ge=0),
    snapshot: bool = Query(default=False),
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    workflows.read_run(db, run_id)
    cursor = after
    if last_event_id:
        try:
            cursor = max(cursor, int(last_event_id))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail="Last-Event-ID 必须是整数") from exc

    async def generate() -> AsyncIterator[str]:
        current = cursor
        if snapshot:
            with SessionLocal() as snapshot_db:
                value = workflows.read_run_snapshot(snapshot_db, run_id)
            current = value.run.event_sequence
            yield _sse(
                current,
                "snapshot",
                value.model_dump(mode="json"),
            )
        while True:
            if await request.is_disconnected():
                return
            with SessionLocal() as event_db:
                events = workflows.list_events(event_db, run_id, after=current)
                run = workflows.read_run(event_db, run_id)
            for event in events:
                current = event.sequence
                yield _sse(current, event.event, event.model_dump(mode="json"))
            if run.status in {"completed", "failed", "cancelled", "interrupted"} and current >= run.event_sequence:
                return
            await asyncio.sleep(0.15)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


def _sse(sequence: int, event: str, payload: object) -> str:
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return f"id: {sequence}\nevent: {event}\ndata: {data}\n\n"
