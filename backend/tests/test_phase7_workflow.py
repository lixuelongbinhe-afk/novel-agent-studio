from __future__ import annotations

import asyncio
from collections.abc import Generator
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app import models
from app.database import Base
from app.schemas import (
    AgentDefinitionCreate,
    AgentDefinitionRead,
    ApprovalDecisionRequest,
    ProposedChangeSetEdit,
    ProposedChangeSetRebase,
    WorkflowCreate,
    WorkflowEdgeWrite,
    WorkflowNodeWrite,
    WorkflowRunCreate,
)
from app.services import agents, approvals, change_sets, workflow_runtime, workflows
from app.services.approval_runtime import approval_signals


@pytest.fixture
def session_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[sessionmaker[Session], None, None]:
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'phase7-workflow.db').as_posix()}",
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
    workflow_runtime.event_bus._locks.clear()
    approval_signals._events.clear()
    yield factory
    engine.dispose()


def seed_runtime(
    db: Session,
) -> tuple[models.Project, models.Chapter, models.ModelProfile]:
    project = models.Project(title="Phase 7 工作流小说")
    db.add(project)
    db.flush()
    volume = models.Volume(project_id=project.id, title="第一卷", position=1)
    db.add(volume)
    db.flush()
    chapter = models.Chapter(
        volume_id=volume.id,
        title="第一章",
        content="<p>审批前正文。</p>",
        word_count=6,
        position=1,
    )
    db.add(chapter)
    provider = models.ProviderAccount(
        name=f"Phase7 Mock {project.id}", provider_type="mock", enabled=True
    )
    db.add(provider)
    db.flush()
    db.add(
        models.ProtocolConfiguration(
            provider_account_id=provider.id,
            protocol="mock",
            options_json="{}",
        )
    )
    profile = models.ModelProfile(
        provider_account_id=provider.id,
        name="mock-novel-v1",
        display_name="Mock Novel v1",
        context_window=32_768,
        enabled=True,
    )
    db.add(profile)
    db.flush()
    for capability in (
        "basic_text",
        "streaming",
        "system_prompt",
        "temperature",
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
    db.flush()
    return project, chapter, profile


def create_agent(
    db: Session,
    project_id: int,
    model_id: int,
    *,
    name: str,
    agent_type: str,
) -> AgentDefinitionRead:
    return agents.create_agent(
        db,
        AgentDefinitionCreate.model_validate(
            {
                "project_id": project_id,
                "name": name,
                "agent_type": agent_type,
                "system_prompt": "只完成当前节点任务。",
                "prompt_template": "请处理当前工作流输入。",
                "input_schema": {},
                "output_schema": {},
                "output_mode": "text",
                "model_profile_id": model_id,
                "route_id": None,
                "parameters": {
                    "temperature": 0.2,
                    "top_p": None,
                    "max_tokens": 512,
                    "scenario": "normal",
                },
                "required_capabilities": ["basic_text"],
                "allow_degradation": True,
                "timeout_seconds": 20,
                "retry_count": 0,
                "budget": {
                    "max_tokens": None,
                    "max_cost": None,
                    "currency": "USD",
                },
                "enabled": True,
            }
        ),
    )


def phase7_graph(
    project_id: int,
    editor_id: int,
    revision_id: int,
    extractor_id: int,
) -> WorkflowCreate:
    nodes = [
        WorkflowNodeWrite(key="start", type="start", label="Start"),
        WorkflowNodeWrite(
            key="editor",
            type="agent",
            label="编辑 Agent",
            config={"agent_id": editor_id},
        ),
        WorkflowNodeWrite(
            key="prose_approval",
            type="human_approval",
            label="正文审批",
            config={
                "approval_type": "prose",
                "title": "正文审批",
                "instructions": "确认正文或要求修改。",
                "revision_agent_id": revision_id,
            },
        ),
        WorkflowNodeWrite(
            key="extract",
            type="state_extraction",
            label="状态提取",
            config={
                "agent_id": extractor_id,
                "chapter_id_path": "input.chapter_id",
            },
        ),
        WorkflowNodeWrite(
            key="changes",
            type="proposed_changes",
            label="生成变更预览",
        ),
        WorkflowNodeWrite(
            key="metadata_approval",
            type="human_approval",
            label="元数据审批",
            config={
                "approval_type": "change_set",
                "title": "元数据审批",
                "instructions": "逐项确认后才可写回。",
            },
        ),
        WorkflowNodeWrite(
            key="writeback",
            type="database_writeback",
            label="事务写回",
            config={"poll_seconds": 0.1},
        ),
        WorkflowNodeWrite(key="output", type="output", label="Output"),
    ]
    order = [
        "start",
        "editor",
        "prose_approval",
        "extract",
        "changes",
        "metadata_approval",
        "writeback",
        "output",
    ]
    edges = [
        WorkflowEdgeWrite(
            key=f"edge-{index}", source=source, target=target
        )
        for index, (source, target) in enumerate(zip(order, order[1:]), start=1)
    ]
    return WorkflowCreate(
        project_id=project_id,
        name="双审批事务写回",
        description="Phase 7 runtime acceptance",
        nodes=nodes,
        edges=edges,
    )


async def wait_for_approval(
    factory: sessionmaker[Session],
    run_id: int,
    approval_type: str,
    *,
    minimum_id: int = 0,
    timeout: float = 12,
) -> models.ApprovalRequest:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        with factory() as db:
            row = db.scalar(
                select(models.ApprovalRequest)
                .where(
                    models.ApprovalRequest.workflow_run_id == run_id,
                    models.ApprovalRequest.approval_type == approval_type,
                    models.ApprovalRequest.status == "pending",
                    models.ApprovalRequest.id > minimum_id,
                )
                .order_by(models.ApprovalRequest.id.desc())
            )
            if row is not None:
                db.expunge(row)
                return row
        await asyncio.sleep(0.05)
    raise AssertionError(f"Timed out waiting for {approval_type} approval")


async def wait_for_run_status(
    factory: sessionmaker[Session],
    run_id: int,
    status: str,
    *,
    timeout: float = 15,
) -> models.WorkflowRun:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        with factory() as db:
            row = db.get(models.WorkflowRun, run_id)
            if row is not None and row.status == status:
                db.expunge(row)
                return row
            if row is not None and row.status == "failed" and status != "failed":
                raise AssertionError(f"Run failed: {row.error_json}")
        await asyncio.sleep(0.05)
    raise AssertionError(f"Timed out waiting for run status {status}")


async def wait_for_change_set_status(
    factory: sessionmaker[Session],
    run_id: int,
    status: str,
    *,
    timeout: float = 12,
) -> models.ProposedChangeSet:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        with factory() as db:
            row = db.scalar(
                select(models.ProposedChangeSet).where(
                    models.ProposedChangeSet.workflow_run_id == run_id
                )
            )
            if row is not None and row.status == status:
                db.expunge(row)
                return row
        await asyncio.sleep(0.05)
    raise AssertionError(f"Timed out waiting for change set status {status}")


def decide(
    factory: sessionmaker[Session],
    approval_id: int,
    *,
    action: str,
    key: str,
    note: str = "",
) -> None:
    with factory() as db, db.begin():
        row = db.get(models.ApprovalRequest, approval_id)
        assert row is not None
        approvals.decide_approval(
            db,
            row.id,
            ApprovalDecisionRequest.model_validate(
                {
                    "action": action,
                    "expected_revision": row.revision,
                    "idempotency_key": key,
                    "note": note,
                }
            ),
        )
    approval_signals.notify(approval_id)


def test_workflow_really_pauses_revises_twice_and_writes_once(
    session_factory: sessionmaker[Session],
) -> None:
    async def scenario() -> None:
        with session_factory() as db, db.begin():
            project, chapter, profile = seed_runtime(db)
            editor = create_agent(
                db, project.id, profile.id, name="编辑", agent_type="editor"
            )
            revision = create_agent(
                db, project.id, profile.id, name="修订", agent_type="revision"
            )
            extractor = create_agent(
                db, project.id, profile.id, name="状态提取", agent_type="extractor"
            )
            workflow = workflows.create_workflow(
                db,
                phase7_graph(project.id, editor.id, revision.id, extractor.id),
            )
            run = workflows.create_run(
                db, workflow.id, WorkflowRunCreate(input={"chapter_id": chapter.id})
            )
            run_id = run.id
            chapter_id = chapter.id

        task = asyncio.create_task(workflow_runtime.execute_run(run_id))
        first_prose = await wait_for_approval(
            session_factory, run_id, "prose"
        )
        await wait_for_run_status(session_factory, run_id, "waiting_approval")
        with session_factory() as db:
            untouched_chapter = db.get(models.Chapter, chapter_id)
            assert untouched_chapter is not None
            assert untouched_chapter.content == "<p>审批前正文。</p>"

        decide(
            session_factory,
            first_prose.id,
            action="request_changes",
            key="runtime-prose-changes-001",
            note="补足人物动机",
        )
        second_prose = await wait_for_approval(
            session_factory,
            run_id,
            "prose",
            minimum_id=first_prose.id,
        )
        assert second_prose.round_number == 2
        decide(
            session_factory,
            second_prose.id,
            action="approve",
            key="runtime-prose-approve-002",
        )

        first_metadata = await wait_for_approval(
            session_factory, run_id, "change_set"
        )
        with session_factory() as db:
            untouched_chapter = db.get(models.Chapter, chapter_id)
            assert untouched_chapter is not None
            assert untouched_chapter.content == "<p>审批前正文。</p>"
            change_set = db.scalar(
                select(models.ProposedChangeSet).where(
                    models.ProposedChangeSet.workflow_run_id == run_id
                )
            )
            assert change_set is not None
            change_set_id = change_set.id

        decide(
            session_factory,
            first_metadata.id,
            action="request_changes",
            key="runtime-metadata-changes-001",
            note="本轮不写摘要",
        )
        with session_factory() as db, db.begin():
            change_set = db.get(models.ProposedChangeSet, change_set_id)
            assert change_set is not None
            items = change_sets.change_set_items(change_set)
            edited = [
                item.model_copy(update={"decision": "reject"})
                if item.kind == "chapter_summary"
                else item
                for item in items
            ]
            edit_result = change_sets.edit_change_set(
                db,
                change_set.id,
                ProposedChangeSetEdit(
                    expected_revision=change_set.revision,
                    items=edited,
                ),
            )
        assert edit_result.replacement_approval is not None
        approval_signals.notify(
            first_metadata.id, edit_result.replacement_approval.id
        )
        second_metadata = await wait_for_approval(
            session_factory,
            run_id,
            "change_set",
            minimum_id=first_metadata.id,
        )
        assert second_metadata.round_number == 2
        decide(
            session_factory,
            second_metadata.id,
            action="approve",
            key="runtime-metadata-approve-002",
        )

        await asyncio.wait_for(task, timeout=15)
        completed = await wait_for_run_status(
            session_factory, run_id, "completed"
        )
        assert completed.completed_at is not None
        with session_factory() as db:
            written_chapter = db.get(models.Chapter, chapter_id)
            assert written_chapter is not None
            assert written_chapter.content != "<p>审批前正文。</p>"
            assert len(
                db.scalars(
                    select(models.ChapterVersion).where(
                        models.ChapterVersion.chapter_id == chapter_id
                    )
                ).all()
            ) == 1
            assert len(
                db.scalars(
                    select(models.WritebackAudit).where(
                        models.WritebackAudit.workflow_run_id == run_id
                    )
                ).all()
            ) == 1
            summary = db.scalar(
                select(models.ChapterSummary).where(
                    models.ChapterSummary.chapter_id == chapter_id
                )
            )
            assert summary is None
            attempts = db.scalars(
                select(models.NodeRunAttempt)
                .join(models.NodeRun, models.NodeRun.id == models.NodeRunAttempt.node_run_id)
                .where(
                    models.NodeRun.workflow_run_id == run_id,
                    models.NodeRun.node_key == "prose_approval",
                )
            ).all()
            assert len(attempts) >= 3
            events = db.scalars(
                select(models.WorkflowRunEvent).where(
                    models.WorkflowRunEvent.workflow_run_id == run_id,
                    models.WorkflowRunEvent.event_type == "run_waiting_approval",
                )
            ).all()
            assert len(events) >= 2

    asyncio.run(scenario())


def test_cancelling_waiting_run_cancels_approval_and_preserves_chapter(
    session_factory: sessionmaker[Session],
) -> None:
    async def scenario() -> None:
        with session_factory() as db, db.begin():
            project, chapter, profile = seed_runtime(db)
            editor = create_agent(
                db, project.id, profile.id, name="取消测试编辑", agent_type="editor"
            )
            revision = create_agent(
                db, project.id, profile.id, name="取消测试修订", agent_type="revision"
            )
            extractor = create_agent(
                db, project.id, profile.id, name="取消测试提取", agent_type="extractor"
            )
            workflow = workflows.create_workflow(
                db,
                phase7_graph(project.id, editor.id, revision.id, extractor.id),
            )
            run = workflows.create_run(
                db, workflow.id, WorkflowRunCreate(input={"chapter_id": chapter.id})
            )
            run_id = run.id
            chapter_id = chapter.id

        cancellation = asyncio.Event()
        task = asyncio.create_task(
            workflow_runtime.execute_run(run_id, cancel_event=cancellation)
        )
        approval = await wait_for_approval(session_factory, run_id, "prose")
        cancellation.set()
        await asyncio.wait_for(task, timeout=8)
        await wait_for_run_status(session_factory, run_id, "cancelled")
        with session_factory() as db:
            cancelled_approval = db.get(models.ApprovalRequest, approval.id)
            untouched_chapter = db.get(models.Chapter, chapter_id)
            assert cancelled_approval is not None
            assert untouched_chapter is not None
            assert cancelled_approval.status == "cancelled"
            assert untouched_chapter.content == "<p>审批前正文。</p>"
            assert db.scalar(select(models.WritebackAudit)) is None

    asyncio.run(scenario())


def test_writeback_conflict_rebases_reapproves_and_resumes_same_run(
    session_factory: sessionmaker[Session],
) -> None:
    async def scenario() -> None:
        with session_factory() as db, db.begin():
            project, chapter, profile = seed_runtime(db)
            editor = create_agent(
                db, project.id, profile.id, name="冲突编辑", agent_type="editor"
            )
            revision = create_agent(
                db, project.id, profile.id, name="冲突修订", agent_type="revision"
            )
            extractor = create_agent(
                db, project.id, profile.id, name="冲突提取", agent_type="extractor"
            )
            workflow = workflows.create_workflow(
                db,
                phase7_graph(project.id, editor.id, revision.id, extractor.id),
            )
            run = workflows.create_run(
                db, workflow.id, WorkflowRunCreate(input={"chapter_id": chapter.id})
            )
            run_id = run.id
            chapter_id = chapter.id

        task = asyncio.create_task(workflow_runtime.execute_run(run_id))
        prose = await wait_for_approval(session_factory, run_id, "prose")
        decide(
            session_factory,
            prose.id,
            action="approve",
            key="conflict-prose-approve",
        )
        metadata = await wait_for_approval(
            session_factory, run_id, "change_set"
        )
        with session_factory() as db, db.begin():
            concurrent_chapter = db.get(models.Chapter, chapter_id)
            assert concurrent_chapter is not None
            concurrent_chapter.content = "<p>审批期间人工并发修改。</p>"
            concurrent_chapter.word_count = 10
            concurrent_chapter.revision += 1
        decide(
            session_factory,
            metadata.id,
            action="approve",
            key="conflict-metadata-approve",
        )
        conflicted = await wait_for_change_set_status(
            session_factory, run_id, "conflicted"
        )
        await wait_for_run_status(session_factory, run_id, "waiting_approval")
        with session_factory() as db:
            concurrent_chapter = db.get(models.Chapter, chapter_id)
            assert concurrent_chapter is not None
            assert concurrent_chapter.content == "<p>审批期间人工并发修改。</p>"

        with session_factory() as db, db.begin():
            result = change_sets.rebase_change_set(
                db,
                conflicted.id,
                ProposedChangeSetRebase(
                    expected_revision=conflicted.revision,
                    action="rebase_current",
                ),
            )
            assert result.change_set.status == "pending"
        reapproval = await wait_for_approval(
            session_factory,
            run_id,
            "change_set",
            minimum_id=metadata.id,
        )
        decide(
            session_factory,
            reapproval.id,
            action="approve",
            key="conflict-reapproval-approve",
        )
        await asyncio.wait_for(task, timeout=15)
        await wait_for_run_status(session_factory, run_id, "completed")
        with session_factory() as db:
            versions = db.scalars(
                select(models.ChapterVersion).where(
                    models.ChapterVersion.chapter_id == chapter_id
                )
            ).all()
            assert len(versions) == 1
            assert versions[0].content == "<p>审批期间人工并发修改。</p>"
            assert len(db.scalars(select(models.WritebackAudit)).all()) == 1

    asyncio.run(scenario())
