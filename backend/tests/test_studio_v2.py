from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Literal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app import models
from app.database import Base
from app.schemas.studio import (
    ArtifactDecision,
    ArtifactUpdate,
    GenerateRequest,
    OutlineImportRequest,
    SnapshotCreate,
    StudioProjectCreate,
)
from app.services import studio


@pytest.fixture
def db(tmp_path: Path) -> Generator[Session, None, None]:
    engine = create_engine(f"sqlite:///{(tmp_path / 'studio-v2.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def create_project(
    db: Session, entry_mode: Literal["creative", "outline"] = "creative"
) -> dict[str, object]:
    with db.begin():
        return studio.create_project(
            db,
            StudioProjectCreate(
                title="雾港回声",
                idea="档案员在雨季调查一艘失踪渡轮。",
                entry_mode=entry_mode,
                genre="悬疑",
                target_words=120_000,
                chapter_count=4,
                chapter_words=2500,
            ),
        )


def test_project_flow_has_expected_defaults_and_dashboard(db: Session) -> None:
    overview = create_project(db)
    project_id = int(overview["project"]["id"])  # type: ignore[index]

    assert overview["state"]["stage"] == "idea"  # type: ignore[index]
    assert overview["state"]["review_granularity"] == "chapter"  # type: ignore[index]
    assert overview["state"]["generation_mode"] == "countdown"  # type: ignore[index]
    assert overview["state"]["countdown_seconds"] == 10  # type: ignore[index]
    assert overview["state"]["budget_warning_percent"] == 70  # type: ignore[index]
    assert overview["state"]["budget_pause_percent"] == 110  # type: ignore[index]
    assert studio.dashboard(db)[0]["id"] == project_id


def test_outline_import_builds_volume_chapter_scene_tree(db: Session) -> None:
    overview = create_project(db, "outline")
    project_id = int(overview["project"]["id"])  # type: ignore[index]
    text = "# 第一卷 潮声\n## 第一章 夜航\n场景一 防波堤\n雾中传来旧钟声。\n## 第二章 档案\n场景一 地库"

    preview = studio.parse_outline(text, "雾港回声")
    assert (preview["volume_count"], preview["chapter_count"], preview["scene_count"]) == (1, 2, 2)
    with db.begin():
        studio.import_outline(db, project_id, OutlineImportRequest(text=text))

    result = studio.project_overview(db, project_id)
    assert result["state"]["stage"] == "drafting"
    assert len(result["tree"]["volumes"]) == 1
    assert len(result["tree"]["chapters"]) == 2
    assert len(result["tree"]["scenes"]) == 2


def test_snapshot_retention_keeps_three_ordinary_and_all_special(db: Session) -> None:
    overview = create_project(db)
    project_id = int(overview["project"]["id"])  # type: ignore[index]
    with db.begin():
        for index in range(5):
            studio.create_snapshot(db, project_id, SnapshotCreate(label=f"自动 {index}"))
        for index in range(4):
            studio.create_snapshot(
                db,
                project_id,
                SnapshotCreate(label=f"转折 {index}", special=True),
            )

    snapshots = db.scalars(
        select(models.ProjectSnapshot).where(models.ProjectSnapshot.project_id == project_id)
    ).all()
    assert len([item for item in snapshots if not item.permanent]) == 3
    assert len([item for item in snapshots if item.permanent]) == 4


def test_startup_marks_unfinished_generation_jobs_interrupted(db: Session) -> None:
    overview = create_project(db)
    project_id = int(overview["project"]["id"])  # type: ignore[index]
    job = models.GenerationJob(
        project_id=project_id,
        kind="drafting",
        label="未完成任务",
        status="running",
        progress=42,
    )
    db.add(job)
    db.commit()

    assert studio.mark_interrupted_generation_jobs(db) == 1
    assert job.status == "failed"
    assert job.progress == 100
    assert "退出" in job.error_message


@pytest.mark.asyncio
async def test_multi_agent_generation_requires_review_before_advancing(db: Session) -> None:
    overview = create_project(db)
    project_id = int(overview["project"]["id"])  # type: ignore[index]

    generated = await studio.generate(
        db,
        project_id,
        "world",
        GenerateRequest(use_demo_model=True),
    )
    artifacts = generated["artifacts"]
    assert generated["job"]["status"] == "completed"
    assert len(artifacts) == len(studio.PHASE_AGENTS["world"])
    assert {item["metadata"]["agent_name"] for item in artifacts} == {
        name for name, _ in studio.PHASE_AGENTS["world"]
    }
    assert all(item["status"] == "pending" for item in artifacts)
    assert studio.project_overview(db, project_id)["state"]["stage"] == "idea"

    for artifact in artifacts[:-1]:
        studio.decide_artifact(
            db,
            int(artifact["id"]),
            ArtifactDecision(action="approve", expected_revision=int(artifact["revision"])),
        )
    assert studio.project_overview(db, project_id)["state"]["stage"] == "idea"
    artifact = artifacts[-1]
    studio.decide_artifact(
        db,
        int(artifact["id"]),
        ArtifactDecision(action="approve", expected_revision=int(artifact["revision"])),
    )
    db.commit()
    assert studio.project_overview(db, project_id)["state"]["stage"] == "characters"


@pytest.mark.asyncio
async def test_regenerate_one_planning_item_supersedes_old_version(db: Session) -> None:
    overview = create_project(db)
    project_id = int(overview["project"]["id"])  # type: ignore[index]
    first = await studio.generate(db, project_id, "world", GenerateRequest(use_demo_model=True))
    agent_name = str(first["artifacts"][0]["metadata"]["agent_name"])

    replacement = await studio.generate(
        db,
        project_id,
        "world",
        GenerateRequest(use_demo_model=True, agent_name=agent_name, instruction="加强主题冲突"),
    )

    current = studio.project_overview(db, project_id)["artifacts"]
    series = [item for item in current if item["metadata"].get("agent_name") == agent_name]
    assert len(replacement["artifacts"]) == 1
    assert {item["status"] for item in series} == {"pending", "superseded"}


@pytest.mark.asyncio
async def test_scene_review_creates_independent_pending_artifacts(db: Session) -> None:
    overview = create_project(db, "outline")
    project_id = int(overview["project"]["id"])  # type: ignore[index]
    text = "# 第一卷\n## 第一章 夜航\n### 场景一 码头\n登船。\n### 场景二 船舱\n发现线索。"
    with db.begin():
        studio.import_outline(db, project_id, OutlineImportRequest(text=text))
        state = studio._state(db, project_id)
        state.review_granularity = "scene"
    chapter_id = int(studio.project_overview(db, project_id)["tree"]["chapters"][0]["id"])

    generated = await studio.generate(
        db,
        project_id,
        "drafting",
        GenerateRequest(use_demo_model=True, chapter_id=chapter_id),
    )

    assert len(generated["artifacts"]) == 2
    assert {item["kind"] for item in generated["artifacts"]} == {"scene_draft"}
    assert len({item["metadata"]["scene_id"] for item in generated["artifacts"]}) == 2
    assert all(item["status"] == "pending" for item in generated["artifacts"])


def test_major_conflict_requires_author_resolution(db: Session) -> None:
    overview = create_project(db)
    project_id = int(overview["project"]["id"])  # type: ignore[index]
    artifact = models.CreativeArtifact(
        project_id=project_id,
        kind="world",
        title="冲突候选",
        content="[重大冲突] 主角身份与已批准设定不一致。",
        status="pending",
        metadata_json='{"series_key":"world:conflict"}',
    )
    studio._mark_conflicts(artifact)
    db.add(artifact)
    db.commit()

    with pytest.raises(HTTPException, match="重大冲突必须由作者选择处理方式"):
        studio.decide_artifact(
            db,
            artifact.id,
            ArtifactDecision(action="approve", expected_revision=artifact.revision),
        )
    db.rollback()
    result = studio.decide_artifact(
        db,
        artifact.id,
        ArtifactDecision(
            action="approve",
            conflict_resolution="preserve_canon",
            expected_revision=artifact.revision,
        ),
    )
    assert result["status"] == "rejected"


@pytest.mark.asyncio
async def test_style_reference_is_reviewable_and_versioned(db: Session) -> None:
    overview = create_project(db)
    project_id = int(overview["project"]["id"])  # type: ignore[index]
    extracted = await studio.extract_style_reference(
        db,
        project_id,
        "短句推进。对白克制。环境描写偏冷。",
        "sample.md",
        True,
    )
    assert extracted["status"] == "pending"
    assert extracted["metadata"]["filename"] == "sample.md"
    updated = studio.update_artifact(
        db,
        int(extracted["id"]),
        ArtifactUpdate(
            content=str(extracted["content"]) + "\n作者批注后的规则。",
            notes="保留短句规则",
            expected_revision=int(extracted["revision"]),
        ),
    )
    versions = studio.artifact_versions(db, int(updated["id"]))
    assert [item["version_number"] for item in versions] == [2, 1]
    assert versions[0]["notes"] == "保留短句规则"


@pytest.mark.asyncio
async def test_full_planning_approval_gate_builds_writing_tree(db: Session) -> None:
    overview = create_project(db)
    project_id = int(overview["project"]["id"])  # type: ignore[index]
    phases = ["world", "characters", "plot", "volumes", "chapters"]

    for index, phase in enumerate(phases):
        generated = await studio.generate(
            db,
            project_id,
            phase,
            GenerateRequest(use_demo_model=True),
        )
        if index == 0:
            with pytest.raises(HTTPException, match="请先完成并批准"):
                await studio.generate(
                    db,
                    project_id,
                    "drafting",
                    GenerateRequest(use_demo_model=True, chapter_id=1),
                )
        for artifact in generated["artifacts"]:
            studio.decide_artifact(
                db,
                int(artifact["id"]),
                ArtifactDecision(
                    action="approve",
                    expected_revision=int(artifact["revision"]),
                ),
            )
        db.commit()

    result = studio.project_overview(db, project_id)
    assert result["state"]["stage"] == "drafting"
    assert len(result["tree"]["chapters"]) == 4
    assert len(result["tree"]["scenes"]) == 12
