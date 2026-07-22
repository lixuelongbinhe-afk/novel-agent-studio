from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app import models
from app.database import Base
from app.schemas import (
    ApprovalCreate,
    ApprovalDecisionRequest,
    ApprovalSnapshot,
    ProposedChangeSetCreate,
    ProposedChangeSetEdit,
    StateExtractionResult,
    WritebackRequest,
)
from app.services import approvals, change_sets, writeback
from app.services.entity_resolution import EntityResolver


@pytest.fixture
def db(tmp_path: Path) -> Generator[Session, None, None]:
    engine = create_engine(f"sqlite:///{(tmp_path / 'phase7-changes.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def seed_story(db: Session) -> dict[str, Any]:
    project = models.Project(title="雾港回声")
    db.add(project)
    db.flush()
    volume = models.Volume(project_id=project.id, title="第一卷", position=1)
    db.add(volume)
    db.flush()
    chapter = models.Chapter(
        project_id=project.id,
        volume_id=volume.id,
        number=1,
        title="第一章",
        content="<p>旧港仍在下雨。</p>",
        word_count=7,
        position=1,
    )
    db.add(chapter)
    db.flush()
    scene = models.Scene(
        chapter_id=chapter.id,
        title="仓库对峙",
        synopsis="旧摘要",
        content="<p>门被推开。</p>",
        position=1,
    )
    db.add(scene)
    db.flush()
    entities = {
        "hero": models.StoryEntity(
            project_id=project.id,
            name="林岚",
            kind="character",
            description="调查员",
            tags='["主角"]',
        ),
        "location": models.StoryEntity(
            project_id=project.id,
            name="旧港",
            kind="location",
            description="潮湿的港区",
            tags="[]",
        ),
        "item": models.StoryEntity(
            project_id=project.id,
            name="铜钥匙",
            kind="item",
            description="仓库钥匙",
            tags="[]",
        ),
    }
    db.add_all(entities.values())
    db.flush()
    db.add(models.EntityAlias(entity_id=entities["hero"].id, alias="阿岚"))
    workflow = models.Workflow(project_id=project.id, name="审批写回流")
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
    node_runs: dict[str, models.NodeRun] = {}
    for key, node_type in (
        ("prose_approval", "human_approval"),
        ("proposed_changes", "proposed_changes"),
        ("metadata_approval", "human_approval"),
        ("writeback", "database_writeback"),
    ):
        node_run = models.NodeRun(
            workflow_run_id=run.id,
            node_key=key,
            node_type=node_type,
            status="running",
            activated=True,
        )
        db.add(node_run)
        node_runs[key] = node_run
    db.flush()
    return {
        "project": project,
        "volume": volume,
        "chapter": chapter,
        "scene": scene,
        "entities": entities,
        "run": run,
        "nodes": node_runs,
    }


def extraction(story: dict[str, Any]) -> StateExtractionResult:
    scene = story["scene"]
    return StateExtractionResult.model_validate(
        {
            "chapter_summary": {
                "summary": "林岚在仓库发现苏禾，并取得关键证词。",
                "key_events": ["仓库对峙", "苏禾交出证词"],
                "evidence": ["苏禾把录音笔放到桌上"],
                "confidence": 0.96,
            },
            "scene_summaries": [
                {
                    "scene_id": scene.id,
                    "scene_title": scene.title,
                    "summary": "林岚与苏禾在仓库完成第一次交涉。",
                    "evidence": ["仓库门被推开"],
                }
            ],
            "scene_states": [
                {
                    "scene_id": scene.id,
                    "scene_title": scene.title,
                    "viewpoint_name": "林岚",
                    "location_name": "旧港",
                    "item_names": ["铜钥匙"],
                    "state_updates": [
                        {
                            "field_name": "weather",
                            "old_value": "雨",
                            "new_value": "暴雨",
                            "reason": "场景末尾天气加剧",
                        }
                    ],
                    "notes": "所有人在仓库内。",
                    "evidence": ["雨声压过屋顶"],
                }
            ],
            "characters": [
                {
                    "name": "阿岚",
                    "aliases": ["岚姐"],
                    "description": "追查旧港失踪案的调查员",
                    "tags": ["主角", "调查员"],
                    "state_updates": [
                        {
                            "field_name": "trust_suhe",
                            "old_value": "unknown",
                            "new_value": "tentative",
                            "reason": "接受了证词",
                        }
                    ],
                    "evidence": ["林岚收起录音笔"],
                    "confidence": 0.98,
                },
                {
                    "name": "苏禾",
                    "aliases": ["小禾"],
                    "description": "旧港案目击者",
                    "tags": ["证人"],
                    "evidence": ["苏禾把录音笔放到桌上"],
                    "confidence": 0.95,
                },
            ],
            "locations": [{"name": "旧港", "confidence": 0.99}],
            "items": [{"name": "铜钥匙", "confidence": 0.99}],
            "organizations": [],
            "relationships": [
                {
                    "source_name": "林岚",
                    "target_name": "苏禾",
                    "relation_type": "初步合作",
                    "notes": "仍互相保留",
                    "evidence": ["双方交换线索"],
                    "confidence": 0.9,
                }
            ],
            "timeline_events": [
                {
                    "label": "仓库交涉",
                    "event_time": "雨夜 23:10",
                    "description": "林岚取得苏禾的证词。",
                    "evidence": ["墙上时钟指向 23:10"],
                }
            ],
            "foreshadows": [
                {
                    "action": "new",
                    "setup_text": "录音里有第三个人的咳嗽声。",
                    "evidence": ["录音末尾传来咳嗽"],
                }
            ],
            "conflicts": [],
            "continuity_warnings": [],
        }
    )


def approved_prose(db: Session, story: dict[str, Any], prose: str) -> models.ApprovalRequest:
    row = approvals.create_approval(
        db,
        ApprovalCreate(
            project_id=story["project"].id,
            workflow_run_id=story["run"].id,
            node_run_id=story["nodes"]["prose_approval"].id,
            node_key="prose_approval",
            approval_type="prose",
            title="正文审批",
            snapshot=ApprovalSnapshot(approval_type="prose", value=prose),
        ),
    )
    approvals.decide_approval(
        db,
        row.id,
        ApprovalDecisionRequest(
            action="approve",
            expected_revision=row.revision,
            idempotency_key="approve-prose-001",
        ),
    )
    return row


def build_change_set(
    db: Session,
    story: dict[str, Any],
    *,
    prose: str = "<p>旧港的雨更急了。林岚在仓库见到苏禾。</p>",
) -> models.ProposedChangeSet:
    prose_approval = approved_prose(db, story, prose)
    return change_sets.create_change_set(
        db,
        ProposedChangeSetCreate(
            project_id=story["project"].id,
            workflow_run_id=story["run"].id,
            node_run_id=story["nodes"]["proposed_changes"].id,
            node_key="proposed_changes",
            source_approval_id=prose_approval.id,
            chapter_id=story["chapter"].id,
            scene_id=story["scene"].id,
            approved_prose=prose,
            extraction=extraction(story),
        ),
    )


def approve_change_set(
    db: Session,
    story: dict[str, Any],
    row: models.ProposedChangeSet,
) -> models.ApprovalRequest:
    approval = change_sets.create_change_set_approval(
        db,
        row.id,
        node_run_id=story["nodes"]["metadata_approval"].id,
        node_key="metadata_approval",
    )
    approvals.decide_approval(
        db,
        approval.id,
        ApprovalDecisionRequest(
            action="approve",
            expected_revision=approval.revision,
            idempotency_key="approve-metadata-001",
        ),
    )
    return approval


def test_entity_resolution_order_and_ambiguity(db: Session) -> None:
    story = seed_story(db)
    project = story["project"]
    hero = story["entities"]["hero"]
    linked = models.StoryEntity(
        project_id=project.id, name="手工绑定者", kind="character", tags="[]"
    )
    fuzzy = models.StoryEntity(
        project_id=project.id, name="AlexanderHamilton", kind="character", tags="[]"
    )
    duplicate_a = models.StoryEntity(
        project_id=project.id, name="影", kind="character", tags="[]"
    )
    duplicate_b = models.StoryEntity(
        project_id=project.id, name="影", kind="character", tags="[]"
    )
    db.add_all([linked, fuzzy, duplicate_a, duplicate_b])
    db.flush()
    db.add(
        models.ChapterEntityLink(
            chapter_id=story["chapter"].id,
            entity_id=linked.id,
            link_type="manual-protagonist",
        )
    )
    db.flush()
    resolver = EntityResolver(db, project.id, chapter_id=story["chapter"].id)

    assert resolver.resolve(name="无关", kind="character", entity_id=hero.id).method == "id"
    assert resolver.resolve(name="林岚", kind="character").method == "exact_name"
    assert resolver.resolve(name="阿岚", kind="character").method == "alias"
    assert (
        resolver.resolve(
            name="不会命中名称",
            kind="character",
            manual_link_type="manual-protagonist",
        ).method
        == "manual_link"
    )
    assert (
        resolver.resolve(name="AlexanderHamilto", kind="character").method
        == "high_confidence"
    )
    assert resolver.resolve(name="影", kind="character").status == "ambiguous"
    assert resolver.resolve(name="不存在的人", kind="character").status == "unmatched"
    assert resolver.resolve(name="林岚", kind="location", entity_id=hero.id).status == "invalid"


def test_approved_changeset_writes_all_items_once_with_audit(db: Session) -> None:
    story = seed_story(db)
    row = build_change_set(db, story)
    items = change_sets.change_set_items(row)
    kinds = {item.kind for item in items}
    assert {
        "chapter_content",
        "chapter_summary",
        "scene_synopsis",
        "scene_state",
        "entity",
        "entity_alias",
        "entity_relation",
        "entity_state_change",
        "timeline_event",
        "foreshadow",
    } <= kinds
    new_entity = next(
        item
        for item in items
        if item.kind == "entity" and item.proposed["name"] == "苏禾"
    )
    assert new_entity.operation == "create"
    assert new_entity.resolution["status"] == "unmatched"
    relation = next(item for item in items if item.kind == "entity_relation")
    assert relation.proposed["target_entity_ref"] == new_entity.id
    approval = approve_change_set(db, story, row)
    change_set_revision = row.revision
    db.commit()

    with db.begin():
        result = writeback.apply_change_set(
            db,
            row.id,
            WritebackRequest(
                approval_request_id=approval.id,
                expected_change_set_revision=change_set_revision,
            ),
        )
    assert result.status == "applied"
    assert result.audit is not None
    assert len(result.applied_item_ids) == len(
        [item for item in items if item.decision == "accept"]
    )

    db.expire_all()
    chapter = db.get(models.Chapter, story["chapter"].id)
    assert chapter is not None
    assert "林岚在仓库见到苏禾" in chapter.content
    version = db.scalar(
        select(models.ChapterVersion).where(
            models.ChapterVersion.chapter_id == chapter.id
        )
    )
    assert version is not None
    assert version.content == "<p>旧港仍在下雨。</p>"
    summary = db.scalar(
        select(models.ChapterSummary).where(
            models.ChapterSummary.chapter_id == chapter.id
        )
    )
    assert summary is not None and "苏禾" in summary.summary
    scene_state = db.scalar(
        select(models.SceneState).where(
            models.SceneState.scene_id == story["scene"].id
        )
    )
    assert scene_state is not None
    assert json_value(scene_state.state_json)["weather"] == "暴雨"
    suhe = db.scalar(
        select(models.StoryEntity).where(
            models.StoryEntity.project_id == story["project"].id,
            models.StoryEntity.name == "苏禾",
        )
    )
    assert suhe is not None
    assert db.scalar(
        select(models.EntityAlias).where(
            models.EntityAlias.entity_id == suhe.id,
            models.EntityAlias.alias == "小禾",
        )
    ) is not None
    assert db.scalar(
        select(models.EntityRelation).where(
            models.EntityRelation.target_entity_id == suhe.id
        )
    ) is not None
    assert db.scalar(select(models.TimelineEvent).where(models.TimelineEvent.label == "仓库交涉"))
    assert db.scalar(
        select(models.Foreshadow).where(
            models.Foreshadow.setup_text == "录音里有第三个人的咳嗽声。"
        )
    )
    assert db.scalar(select(models.WritebackAudit).where(models.WritebackAudit.change_set_id == row.id))
    assert db.scalar(
        text("SELECT COUNT(*) FROM context_fts WHERE source_type='entity' AND title='苏禾'")
    ) == 1

    db.commit()
    with db.begin():
        replay = writeback.apply_change_set(
            db,
            row.id,
            WritebackRequest(
                approval_request_id=approval.id,
                expected_change_set_revision=change_set_revision,
            ),
        )
    assert replay.status == "applied"
    assert db.scalar(
        select(models.WritebackAudit).where(models.WritebackAudit.change_set_id == row.id)
    ) is not None
    assert len(
        db.scalars(
            select(models.WritebackAudit).where(
                models.WritebackAudit.change_set_id == row.id
            )
        ).all()
    ) == 1


def test_no_write_without_approval_and_revision_conflict_is_visible(db: Session) -> None:
    story = seed_story(db)
    row = build_change_set(db, story)
    pending = change_sets.create_change_set_approval(
        db,
        row.id,
        node_run_id=story["nodes"]["metadata_approval"].id,
        node_key="metadata_approval",
    )
    revision = row.revision
    db.commit()
    with pytest.raises(HTTPException, match="未批准"):
        with db.begin():
            writeback.apply_change_set(
                db,
                row.id,
                WritebackRequest(
                    approval_request_id=pending.id,
                    expected_change_set_revision=revision,
                ),
            )
    db.expire_all()
    untouched_chapter = db.get(models.Chapter, story["chapter"].id)
    assert untouched_chapter is not None
    assert untouched_chapter.content == "<p>旧港仍在下雨。</p>"

    approvals.decide_approval(
        db,
        pending.id,
        ApprovalDecisionRequest(
            action="approve",
            expected_revision=pending.revision,
            idempotency_key="approve-after-pending-check",
        ),
    )
    chapter = db.get(models.Chapter, story["chapter"].id)
    assert chapter is not None
    chapter.content = "<p>人工同时改过的正文。</p>"
    chapter.revision += 1
    db.commit()
    with db.begin():
        result = writeback.apply_change_set(
            db,
            row.id,
            WritebackRequest(
                approval_request_id=pending.id,
                expected_change_set_revision=revision,
            ),
        )
    assert result.status == "conflicted"
    assert any("当前 revision" in conflict for conflict in result.conflicts)
    db.expire_all()
    persisted_chapter = db.get(models.Chapter, chapter.id)
    assert persisted_chapter is not None
    assert persisted_chapter.content == "<p>人工同时改过的正文。</p>"
    assert db.scalar(
        select(models.ChapterVersion).where(models.ChapterVersion.chapter_id == chapter.id)
    ) is None
    assert db.scalar(
        select(models.WritebackAudit).where(models.WritebackAudit.change_set_id == row.id)
    ) is None


def test_edit_supersedes_approval_and_rejects_non_whitelisted_field(db: Session) -> None:
    story = seed_story(db)
    row = build_change_set(db, story)
    pending = change_sets.create_change_set_approval(
        db,
        row.id,
        node_run_id=story["nodes"]["metadata_approval"].id,
        node_key="metadata_approval",
    )
    items = change_sets.change_set_items(row)
    edited_items = [
        item.model_copy(update={"decision": "reject"}) if item.kind == "foreshadow" else item
        for item in items
    ]
    result = change_sets.edit_change_set(
        db,
        row.id,
        ProposedChangeSetEdit(expected_revision=row.revision, items=edited_items),
    )
    assert result.replacement_approval is not None
    assert pending.status == "superseded"
    assert result.replacement_approval.snapshot.value["changes_hash"] == result.change_set.changes_hash

    latest = change_sets.change_set_items(row)
    malicious = latest[0].model_copy(
        update={"proposed": {**latest[0].proposed, "raw_sql": "DROP TABLE chapters"}}
    )
    with pytest.raises(HTTPException, match="非白名单字段"):
        change_sets.edit_change_set(
            db,
            row.id,
            ProposedChangeSetEdit(
                expected_revision=row.revision,
                items=[malicious, *latest[1:]],
            ),
        )


def test_writeback_failure_rolls_back_every_table(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    story = seed_story(db)
    row = build_change_set(db, story)
    approval = approve_change_set(db, story, row)
    revision = row.revision
    initial_entity_count = len(db.scalars(select(models.StoryEntity)).all())
    db.commit()

    def fail_index(session: Session, project_id: int) -> int:
        raise RuntimeError(f"forced FTS failure for {project_id}")

    monkeypatch.setattr(writeback, "rebuild_fts_index", fail_index)
    with pytest.raises(RuntimeError, match="forced FTS failure"):
        with db.begin():
            writeback.apply_change_set(
                db,
                row.id,
                WritebackRequest(
                    approval_request_id=approval.id,
                    expected_change_set_revision=revision,
                ),
            )
    db.expire_all()
    chapter = db.get(models.Chapter, story["chapter"].id)
    assert chapter is not None and chapter.content == "<p>旧港仍在下雨。</p>"
    assert len(db.scalars(select(models.StoryEntity)).all()) == initial_entity_count
    assert db.scalar(select(models.ChapterVersion)) is None
    assert db.scalar(select(models.WritebackAudit)) is None
    persisted_change_set = db.get(models.ProposedChangeSet, row.id)
    assert persisted_change_set is not None
    assert persisted_change_set.status == "pending"


def json_value(value: str) -> dict[str, Any]:
    import json

    parsed = json.loads(value)
    return parsed if isinstance(parsed, dict) else {}
