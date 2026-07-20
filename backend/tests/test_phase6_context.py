from __future__ import annotations

from collections.abc import Generator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app import models
from app.database import Base
from app.schemas import (
    AgentDefinitionCreate,
    ChapterEntityLinkCreate,
    ChapterSummaryCreate,
    ChapterSummaryUpdate,
    ContextBuildRequest,
    SceneStateCreate,
    WorkflowCreate,
    WorkflowEdgeWrite,
    WorkflowNodeWrite,
    WorkflowRunCreate,
)
from app.schemas.context import ALL_CLASSIFICATIONS
from app.services import agents, context_builder, context_memory, workflow_runtime, workflows
from app.services.context_retrieval import CompositeRetriever, RetrievalCandidate, RetrievalQuery


@pytest.fixture
def session_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[sessionmaker[Session], None, None]:
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'phase6.db').as_posix()}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    monkeypatch.setattr(workflow_runtime, "SessionLocal", factory)
    workflow_runtime.event_bus._locks.clear()
    yield factory
    engine.dispose()


@dataclass(frozen=True)
class ContextSeed:
    project_id: int
    previous_chapter_id: int
    chapter_id: int
    scene_id: int
    character_id: int
    location_id: int
    item_id: int
    timeline_id: int
    foreshadow_id: int
    world_rule_id: int
    provider_id: int
    model_id: int
    agent_id: int
    policy_id: int


def seed_context(db: Session) -> ContextSeed:
    project = models.Project(title="上下文验收小说", summary="离线检索测试")
    db.add(project)
    db.flush()
    volume = models.Volume(project_id=project.id, title="第一卷", position=1)
    db.add(volume)
    db.flush()
    previous = models.Chapter(
        volume_id=volume.id,
        title="第一章 雾中来信",
        content="林栀在档案馆收到一封没有署名的旧信。",
        position=1,
        word_count=20,
    )
    current = models.Chapter(
        volume_id=volume.id,
        title="第二章 返港",
        content="林栀回到旧码头，潮水正漫过第七码头柱。",
        position=2,
        word_count=22,
    )
    db.add_all([previous, current])
    db.flush()
    scene = models.Scene(
        chapter_id=current.id,
        title="钟楼下的会面",
        synopsis="林栀带着铜钥匙在旧码头等待线人。",
        content="<p>钟楼下的铜钥匙闪了一下。远处的<strong>旧无线电</strong>忽然响起。</p>",
        position=1,
    )
    db.add(scene)
    character = models.StoryEntity(
        project_id=project.id,
        name="林栀",
        kind="character",
        description="年轻档案员，右手有旧伤。",
        tags='["主角", "档案馆"]',
    )
    location = models.StoryEntity(
        project_id=project.id,
        name="旧码头",
        kind="location",
        description="退潮时会露出第七码头柱。",
        tags='["港区"]',
    )
    item = models.StoryEntity(
        project_id=project.id,
        name="铜钥匙",
        kind="item",
        description="刻着潮汐刻度。",
        tags='["关键物品"]',
    )
    db.add_all([character, location, item])
    db.flush()
    db.add(models.EntityAlias(entity_id=character.id, alias="小栀"))
    db.add(
        models.EntityRelation(
            project_id=project.id,
            source_entity_id=character.id,
            target_entity_id=location.id,
            relation_type="秘密调查",
            notes="她每次退潮后都会来这里。",
        )
    )
    db.add(
        models.EntityStateChange(
            entity_id=character.id,
            chapter_id=current.id,
            field_name="持有物",
            old_value="无",
            new_value="铜钥匙",
            reason="第一章末收到",
        )
    )
    timeline = models.TimelineEvent(
        project_id=project.id,
        chapter_id=current.id,
        label="返港当夜",
        event_time="雾历 12 月 3 日 23:40",
        description="林栀在退潮前抵达旧码头。",
        position=2,
    )
    foreshadow = models.Foreshadow(
        project_id=project.id,
        chapter_id=current.id,
        setup_text="旧无线电只在退潮前呼叫林栀的小名。",
        payoff_text="",
        status="open",
    )
    world_rule = models.StyleGuide(
        project_id=project.id,
        name="潮汐规则",
        rule_text="雾港每晚只有一次完整退潮，时间线不得出现第二次退潮。",
        category="world",
    )
    voice_rule = models.StyleGuide(
        project_id=project.id,
        name="叙述口吻",
        rule_text="保持克制，以机械噪声和海雾承载情绪。",
        category="voice",
    )
    db.add_all([timeline, foreshadow, world_rule, voice_rule])
    db.flush()
    context_memory.create_chapter_summary(
        db,
        ChapterSummaryCreate(
            chapter_id=previous.id,
            summary="林栀收到无署名旧信，并在信封夹层发现铜钥匙。",
            key_events=["收到旧信", "发现铜钥匙"],
            entity_ids=[character.id, item.id],
        ),
    )
    context_memory.create_scene_state(
        db,
        SceneStateCreate(
            scene_id=scene.id,
            viewpoint_entity_id=character.id,
            location_entity_id=location.id,
            item_entity_ids=[item.id],
            state={"weather": "浓雾", "time": "退潮前"},
            notes="视角不可切换。",
        ),
    )
    context_memory.create_chapter_entity_link(
        db,
        ChapterEntityLinkCreate(
            chapter_id=current.id,
            entity_id=character.id,
            link_type="viewpoint",
            relevance=1.0,
            notes="本章视角人物",
        ),
    )
    provider = models.ProviderAccount(
        name="Phase6 Mock", provider_type="mock", enabled=True
    )
    db.add(provider)
    db.flush()
    db.add(
        models.ProtocolConfiguration(
            provider_account_id=provider.id, protocol="mock", options_json="{}"
        )
    )
    profile = models.ModelProfile(
        provider_account_id=provider.id,
        name="phase6-mock",
        display_name="Phase 6 Mock",
        context_window=16_384,
        enabled=True,
    )
    db.add(profile)
    db.flush()
    for capability in ("basic_text", "streaming", "system_prompt", "max_output_tokens"):
        db.add(
            models.ModelCapability(
                model_profile_id=profile.id,
                capability=capability,
                status="supported",
                source="official_metadata",
            )
        )
    agent = agents.create_agent(
        db,
        _agent_payload(project.id, profile.id, name="连贯性 Agent", agent_type="continuity"),
    )
    policy = context_memory.ensure_default_context_policy(db, project.id)
    policy.token_budget = 8_000
    context_memory.ensure_provider_data_policy(db, provider.id)
    db.flush()
    return ContextSeed(
        project_id=project.id,
        previous_chapter_id=previous.id,
        chapter_id=current.id,
        scene_id=scene.id,
        character_id=character.id,
        location_id=location.id,
        item_id=item.id,
        timeline_id=timeline.id,
        foreshadow_id=foreshadow.id,
        world_rule_id=world_rule.id,
        provider_id=provider.id,
        model_id=profile.id,
        agent_id=agent.id,
        policy_id=policy.id,
    )


def _agent_payload(
    project_id: int,
    model_id: int,
    *,
    name: str,
    agent_type: str = "writer",
) -> AgentDefinitionCreate:
    return AgentDefinitionCreate.model_validate(
        {
            "project_id": project_id,
            "name": name,
            "agent_type": agent_type,
            "system_prompt": "严格遵守检索上下文。",
            "prompt_template": "处理：{value}",
            "input_schema": {},
            "output_schema": {},
            "output_mode": "text",
            "model_profile_id": model_id,
            "route_id": None,
            "parameters": {
                "temperature": 0.4,
                "top_p": None,
                "max_tokens": 256,
                "scenario": "normal",
            },
            "required_capabilities": ["basic_text"],
            "allow_degradation": True,
            "timeout_seconds": 30,
            "retry_count": 0,
            "budget": {"max_tokens": None, "max_cost": None, "currency": "USD"},
            "enabled": True,
        }
    )


def _build_request(seed: ContextSeed, *, persist: bool = False) -> ContextBuildRequest:
    return ContextBuildRequest(
        project_id=seed.project_id,
        chapter_id=seed.chapter_id,
        scene_id=seed.scene_id,
        agent_id=seed.agent_id,
        policy_id=seed.policy_id,
        query="小栀拿着铜钥匙回到旧码头，遵守潮汐规则并回收无线电伏笔。",
        workflow_input={"task": "续写钟楼会面"},
        upstream_outputs={"scene_plan": {"focus": "退潮前的无线电呼叫"}},
        reserved_output_tokens=512,
        persist_snapshot=persist,
    )


def test_composite_retrieval_is_explainable_and_snapshot_is_reproducible(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db, db.begin():
        seed = seed_context(db)
        chapter_revision = cast(models.Chapter, db.get(models.Chapter, seed.chapter_id)).revision
        entity_revision = cast(models.StoryEntity, db.get(models.StoryEntity, seed.character_id)).revision

    with session_factory() as db, db.begin():
        first = context_builder.build_context(db, _build_request(seed, persist=True))
        assert first.id is not None
        assert not first.blocked
        assert "<p>" not in first.context_text
        assert "<strong>" not in first.context_text
        assert first.included_tokens <= first.token_budget
        by_source = {(item.source_type, item.source_id): item for item in first.included}
        assert ("entity", seed.character_id) in by_source
        assert ("entity", seed.location_id) in by_source
        assert ("entity", seed.item_id) in by_source
        assert ("timeline", seed.timeline_id) in by_source
        assert ("foreshadow", seed.foreshadow_id) in by_source
        assert ("style_guide", seed.world_rule_id) in by_source
        character = by_source[("entity", seed.character_id)]
        assert any("别名" in reason or "视角人物" in reason for reason in character.reasons)
        assert all(item.reasons for item in first.included)
        assert all(item.token_estimate >= 1 for item in first.included)
        assert all(item.classification in ALL_CLASSIFICATIONS for item in first.included)
        repeated = context_builder.build_context(db, _build_request(seed))
        assert repeated.build_hash == first.build_hash
        assert repeated.context_text == first.context_text
        assert cast(models.Chapter, db.get(models.Chapter, seed.chapter_id)).revision == chapter_revision
        assert cast(models.StoryEntity, db.get(models.StoryEntity, seed.character_id)).revision == entity_revision
        stored_text = first.context_text
        stored_hash = first.build_hash
        build_id = first.id

    with session_factory() as db, db.begin():
        scene = cast(models.Scene, db.get(models.Scene, seed.scene_id))
        scene.content = "修改后的场景正文，不应改变旧 ContextBuild。"
        scene.revision += 1

    with session_factory() as db, db.begin():
        snapshot = context_builder.read_context_build(db, build_id)
        assert snapshot.context_text == stored_text
        assert snapshot.build_hash == stored_hash
        rebuilt = context_builder.build_context(db, _build_request(seed))
        assert rebuilt.build_hash != stored_hash


class StaticRetriever:
    def __init__(self, candidates: list[RetrievalCandidate]) -> None:
        self.candidates = candidates

    def retrieve(self, db: Session, query: RetrievalQuery) -> list[RetrievalCandidate]:
        del db, query
        return [candidate for candidate in self.candidates]


def test_token_budget_truncates_optional_and_blocks_oversized_required(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db, db.begin():
        project = models.Project(title="预算测试")
        db.add(project)
        db.flush()
        policy = context_memory.ensure_default_context_policy(db, project.id)
        optional = RetrievalCandidate(
            source_type="style_guide",
            source_id=99,
            section="style",
            title="超长可选资料",
            content="海雾机械噪声" * 300,
            relevance=0.8,
            reasons=["静态预算测试"],
        )
        request = ContextBuildRequest(
            project_id=project.id,
            policy_id=policy.id,
            query="写一句话",
            token_budget_override=180,
        )
        result = context_builder.build_context(
            db,
            request,
            retriever=cast(CompositeRetriever, StaticRetriever([optional])),
        )
        assert not result.blocked
        assert result.included_tokens <= 180
        assert result.truncations
        assert any(item.truncated for item in result.included)

        required = RetrievalCandidate(
            source_type="scene",
            source_id=100,
            section="current_scene",
            title="不可删除的当前场景",
            content="关键正文" * 400,
            relevance=1.0,
            reasons=["当前场景"],
            required=True,
        )
        blocked = context_builder.build_context(
            db,
            request,
            retriever=cast(CompositeRetriever, StaticRetriever([required])),
        )
        assert blocked.blocked
        assert any("超过" in conflict for conflict in blocked.conflicts)


def test_provider_boundary_intersection_blocks_critical_story_data(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db, db.begin():
        seed = seed_context(db)
        provider = models.ProviderAccount(
            name="远程 DeepSeek", provider_type="openai_chat", base_url="https://example.test/v1"
        )
        db.add(provider)
        db.flush()
        db.add(
            models.ProtocolConfiguration(
                provider_account_id=provider.id,
                protocol="openai_chat",
                options_json="{}",
            )
        )
        profile = models.ModelProfile(
            provider_account_id=provider.id,
            name="remote-model",
            display_name="Remote Model",
            context_window=16_384,
        )
        db.add(profile)
        db.flush()
        remote_agent = agents.create_agent(
            db,
            _agent_payload(
                seed.project_id,
                profile.id,
                name="远程写作 Agent",
            ),
        )
        request = _build_request(seed).model_copy(update={"agent_id": remote_agent.id})
        denied = context_builder.build_context(db, request)
        assert denied.blocked
        assert denied.boundary.required_excluded_count >= 1
        assert any(item.source_type == "scene" for item in denied.excluded)
        assert denied.target_providers[0].policy_source == "remote_default"

        route = models.ModelRoute(
            project_id=seed.project_id,
            name="本地到远程回退",
            strategy="ordered_fallback",
            required_capabilities_json="[]",
            allow_degradation=True,
            enabled=True,
        )
        db.add(route)
        db.flush()
        db.add_all(
            [
                models.ModelRouteEntry(
                    route_id=route.id,
                    model_profile_id=seed.model_id,
                    position=0,
                    enabled=True,
                ),
                models.ModelRouteEntry(
                    route_id=route.id,
                    model_profile_id=profile.id,
                    position=1,
                    enabled=True,
                ),
            ]
        )
        route_payload = _agent_payload(
            seed.project_id,
            seed.model_id,
            name="Route 写作 Agent",
        ).model_dump()
        route_payload["model_profile_id"] = None
        route_payload["route_id"] = route.id
        route_agent = agents.create_agent(
            db, AgentDefinitionCreate.model_validate(route_payload)
        )
        route_denied = context_builder.build_context(
            db, _build_request(seed).model_copy(update={"agent_id": route_agent.id})
        )
        assert route_denied.blocked
        assert len(route_denied.target_providers) == 2
        assert "unpublished manuscript" not in route_denied.boundary.provider_allowed

        db.add(
            models.ProviderDataPolicy(
                provider_account_id=provider.id,
                allowed_classifications_json=context_memory._dump(ALL_CLASSIFICATIONS[:-1]),
                block_on_required_exclusion=True,
                notes="测试显式放行未发布稿件",
                enabled=True,
            )
        )
        db.flush()
        allowed = context_builder.build_context(db, request)
        assert not allowed.blocked
        assert allowed.target_providers[0].policy_source == "stored"
        route_allowed = context_builder.build_context(
            db, _build_request(seed).model_copy(update={"agent_id": route_agent.id})
        )
        assert not route_allowed.blocked

        db.add(
            models.ContentClassification(
                project_id=seed.project_id,
                source_type="scene",
                source_id=seed.scene_id,
                classification="secret",
                reason="用户明确标记",
            )
        )
        db.flush()
        secret_denied = context_builder.build_context(db, request)
        assert secret_denied.blocked
        assert any(
            item.source_type == "scene"
            and item.excluded_reason == "provider_data_boundary"
            for item in secret_denied.excluded
        )


@pytest.mark.asyncio
async def test_context_node_runs_and_agent_consumes_package_once(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, str] = {}

    async def capture_agent_attempt(
        run_id: int,
        project_id: int,
        workflow_id: int,
        node_key: str,
        attempt_id: int,
        attempt_number: int,
        agent: dict[str, Any],
        system_prompt: str,
        prompt: str,
    ) -> str:
        del (
            run_id,
            project_id,
            workflow_id,
            node_key,
            attempt_id,
            attempt_number,
            agent,
        )
        captured["system"] = system_prompt
        captured["prompt"] = prompt
        return "上下文消费完成"

    monkeypatch.setattr(workflow_runtime, "_execute_agent_attempt", capture_agent_attempt)
    with session_factory() as db, db.begin():
        seed = seed_context(db)
        nodes = [
            WorkflowNodeWrite(key="start", type="start", label="Start"),
            WorkflowNodeWrite(
                key="context",
                type="context_retrieval",
                label="Context Retrieval",
                config={
                    "agent_id": seed.agent_id,
                    "policy_id": seed.policy_id,
                    "chapter_id_path": "input.chapter_id",
                    "scene_id_path": "input.scene_id",
                    "query_template": "续写：{input.task}",
                    "token_budget": 4_000,
                    "reserved_output_tokens": 256,
                },
            ),
            WorkflowNodeWrite(
                key="agent",
                type="agent",
                label="Agent",
                config={"agent_id": seed.agent_id},
            ),
            WorkflowNodeWrite(key="output", type="output", label="Output"),
        ]
        edges = [
            WorkflowEdgeWrite(key="e1", source="start", target="context"),
            WorkflowEdgeWrite(key="e2", source="context", target="agent"),
            WorkflowEdgeWrite(key="e3", source="agent", target="output"),
        ]
        workflow = workflows.create_workflow(
            db,
            WorkflowCreate(
                project_id=seed.project_id,
                name="上下文节点验收",
                nodes=nodes,
                edges=edges,
            ),
        )
        run = workflows.create_run(
            db,
            workflow.id,
            WorkflowRunCreate(
                input={
                    "task": "继续钟楼会面",
                    "chapter_id": seed.chapter_id,
                    "scene_id": seed.scene_id,
                }
            ),
        )
        run_id = run.id

    await workflow_runtime.execute_run(run_id)
    with session_factory() as db:
        result = workflows.read_run(db, run_id)
        assert result.status == "completed"
        assert result.output == "上下文消费完成"
        builds = db.scalars(
            select(models.ContextBuild).where(models.ContextBuild.workflow_run_id == run_id)
        ).all()
        assert len(builds) == 1
        assert captured["system"].count("钟楼下的铜钥匙闪了一下") == 1
        assert "钟楼下的铜钥匙闪了一下" not in captured["prompt"]
        assert "context_reference" in captured["prompt"]
        events = workflows.list_events(db, run_id)
        assert any(item.event == "context_built" for item in events)
        assert any(item.event == "context_attached" for item in events)


@pytest.mark.asyncio
async def test_agent_builtin_context_builder_persists_actual_injection(
    session_factory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, str] = {}

    async def capture_agent_attempt(
        run_id: int,
        project_id: int,
        workflow_id: int,
        node_key: str,
        attempt_id: int,
        attempt_number: int,
        agent: dict[str, Any],
        system_prompt: str,
        prompt: str,
    ) -> str:
        del (
            run_id,
            project_id,
            workflow_id,
            node_key,
            attempt_id,
            attempt_number,
            agent,
        )
        captured["system"] = system_prompt
        captured["prompt"] = prompt
        return "自动上下文完成"

    monkeypatch.setattr(workflow_runtime, "_execute_agent_attempt", capture_agent_attempt)
    with session_factory() as db, db.begin():
        seed = seed_context(db)
        workflow = workflows.create_workflow(
            db,
            WorkflowCreate(
                project_id=seed.project_id,
                name="Agent 自动上下文验收",
                nodes=[
                    WorkflowNodeWrite(key="start", type="start", label="Start"),
                    WorkflowNodeWrite(
                        key="agent",
                        type="agent",
                        label="Agent",
                        config={
                            "agent_id": seed.agent_id,
                            "automatic_context": True,
                            "context_policy_id": seed.policy_id,
                            "chapter_id_path": "input.chapter_id",
                            "scene_id_path": "input.scene_id",
                            "context_query_template": "续写：{input.task}",
                            "context_token_budget": 4_000,
                        },
                    ),
                    WorkflowNodeWrite(key="output", type="output", label="Output"),
                ],
                edges=[
                    WorkflowEdgeWrite(key="e1", source="start", target="agent"),
                    WorkflowEdgeWrite(key="e2", source="agent", target="output"),
                ],
            ),
        )
        run = workflows.create_run(
            db,
            workflow.id,
            WorkflowRunCreate(
                input={
                    "task": "续写会面",
                    "chapter_id": seed.chapter_id,
                    "scene_id": seed.scene_id,
                }
            ),
        )
        run_id = run.id

    await workflow_runtime.execute_run(run_id)
    with session_factory() as db:
        result = workflows.read_run(db, run_id)
        assert result.status == "completed"
        assert result.output == "自动上下文完成"
        assert captured["system"].count("钟楼下的铜钥匙闪了一下") == 1
        assert "钟楼下的铜钥匙闪了一下" not in captured["prompt"]
        assert db.scalar(
            select(models.ContextBuild.id).where(
                models.ContextBuild.workflow_run_id == run_id
            )
        ) is not None


def test_context_memory_rejects_stale_revision_and_cross_project_entity(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory() as db, db.begin():
        seed = seed_context(db)
        summary = context_memory.list_chapter_summaries(db, seed.project_id)[0]
        update = summary.model_dump(
            exclude={
                "id",
                "token_count",
                "revision",
                "deleted_at",
                "created_at",
                "updated_at",
            }
        )
        update["summary"] = "合法更新"
        update["expected_revision"] = summary.revision
        changed = context_memory.update_chapter_summary(
            db,
            summary.id,
            ChapterSummaryUpdate.model_validate(update),
        )
        assert changed.revision == summary.revision + 1
        with pytest.raises(HTTPException) as stale:
            context_memory.update_chapter_summary(
                db,
                summary.id,
                ChapterSummaryUpdate.model_validate(update),
            )
        assert stale.value.status_code == 409

        other = models.Project(title="另一个项目")
        db.add(other)
        db.flush()
        foreign_entity = models.StoryEntity(
            project_id=other.id,
            name="越界实体",
            kind="character",
            description="",
            tags="[]",
        )
        db.add(foreign_entity)
        db.flush()
        with pytest.raises(HTTPException) as boundary:
            context_memory.create_chapter_entity_link(
                db,
                ChapterEntityLinkCreate(
                    chapter_id=seed.chapter_id,
                    entity_id=foreign_entity.id,
                    link_type="manual",
                ),
            )
        assert boundary.value.status_code == 422
