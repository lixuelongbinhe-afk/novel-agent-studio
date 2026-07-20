from __future__ import annotations

from collections.abc import Generator
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from typing import Literal

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from reportlab.pdfgen import canvas

from app import models
from app.api import studio as studio_api
from app.database import Base
from app.repositories import word_count
from app.services.usage_control import estimate_text_tokens
from app.schemas.studio import (
    ArtifactDecision,
    ArtifactUpdate,
    ChapterTreeRepairRequest,
    ContinuationImportRequest,
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
    with Session(engine, expire_on_commit=False, autoflush=False) as session:
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


def test_delete_project_route_removes_project_from_dashboard(db: Session) -> None:
    overview = create_project(db)
    project_id = int(overview["project"]["id"])  # type: ignore[index]

    response = studio_api.delete_project(project_id, db)

    assert response.status_code == 204
    assert studio.dashboard(db) == []
    with pytest.raises(HTTPException, match="项目不存在"):
        studio.project_overview(db, project_id)


def test_continuation_import_builds_editable_tree_and_permanent_original(db: Session) -> None:
    manuscript = """# 第一卷 旧城
## 第1章 雨夜
雨落在旧城的石阶上。
## 第2章 来信
林舟拆开一封没有署名的信。"""
    with db.begin():
        overview = studio.create_continuation_project(
            db,
            ContinuationImportRequest(
                title="旧城来信",
                text=manuscript,
                source_name="draft.md",
                target_words=80_000,
                target_chapters=5,
                target_volumes=2,
            ),
        )
    assert overview["state"]["entry_mode"] == "continuation"
    assert overview["state"]["stage"] == "continuation_analysis"
    assert [item["key"] for item in overview["stages"]] == studio.CONTINUATION_STAGE_ORDER
    assert [chapter["title"] for chapter in overview["tree"]["chapters"]] == [
        "第1章 雨夜",
        "第2章 来信",
    ]
    original = next(
        item for item in overview["artifacts"] if item["kind"] == "continuation_original"
    )
    assert original["content"] == manuscript
    assert original["metadata"]["readonly"] is True
    assert overview["snapshots"][0]["permanent"] is True
    with pytest.raises(HTTPException, match="永久只读"):
        studio.update_artifact(
            db,
            int(original["id"]),
            ArtifactUpdate(content="不能覆盖", expected_revision=int(original["revision"])),
        )


def test_continuation_pdf_import_extracts_real_text() -> None:
    buffer = BytesIO()
    document = canvas.Canvas(buffer)
    document.drawString(72, 760, "Chapter 1 A half-finished novel")
    document.save()

    text = studio_api._extract_document_text(buffer.getvalue(), "draft.pdf")

    assert "half-finished novel" in text


def test_long_manuscript_chunks_stay_inside_token_budget() -> None:
    source = "开篇标记\n" + "矿井里的钟声与旧日契约。" * 5_000 + "\n终章标记"

    chunks = studio._chunk_text_by_tokens(source, 1_200)

    assert len(chunks) > 10
    assert all(estimate_text_tokens(chunk) <= 1_200 for chunk in chunks)
    assert "开篇标记" in chunks[0]
    assert "终章标记" in chunks[-1]


@pytest.mark.asyncio
async def test_studio_model_call_compacts_prompt_to_real_model_window(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[str] = []

    async def fake_execute(_db: Session, payload: object) -> SimpleNamespace:
        request = payload  # keep the test at the actual Studio -> model execution seam
        text = request.messages[0].content[0].text  # type: ignore[attr-defined]
        captured.append(text)
        return SimpleNamespace(error=None, text="完成", control=None, warnings=[])

    monkeypatch.setattr(studio.model_execution, "execute_model", fake_execute)
    profile = models.ModelProfile(
        id=88,
        provider_account_id=77,
        name="small-context-model",
        display_name="Small Context",
        context_window=4_096,
        enabled=True,
    )
    prompt = "任务开头必须保留\n" + "长篇小说上下文" * 8_000 + "\n作者要求结尾必须保留"

    result = await studio._model_call(
        db,
        1,
        prompt,
        profile,
        use_demo=False,
        max_tokens=1_200,
    )

    assert result.error is None
    assert len(captured) == 1
    assert estimate_text_tokens(captured[0]) <= 4_096 - 1_200 - 384
    assert "任务开头必须保留" in captured[0]
    assert "作者要求结尾必须保留" in captured[0]
    assert "上下文已自动压缩" in captured[0]


@pytest.mark.asyncio
async def test_studio_model_call_recompresses_after_provider_context_error(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[str] = []

    async def fake_execute(_db: Session, payload: object) -> SimpleNamespace:
        text = payload.messages[0].content[0].text  # type: ignore[attr-defined]
        captured.append(text)
        if len(captured) == 1:
            return SimpleNamespace(
                error=SimpleNamespace(code="context_too_long", message="provider limit"),
                text="",
                control=None,
                warnings=[],
            )
        return SimpleNamespace(error=None, text="压缩后成功", control=None, warnings=[])

    monkeypatch.setattr(studio.model_execution, "execute_model", fake_execute)
    profile = models.ModelProfile(
        id=89,
        provider_account_id=77,
        name="misreported-window-model",
        display_name="Misreported Window",
        context_window=8_192,
        enabled=True,
    )

    result = await studio._model_call(
        db,
        1,
        "任务开头\n" + "历史上下文" * 3_000 + "\n任务结尾",
        profile,
        use_demo=False,
        max_tokens=1_200,
    )

    assert result.error is None
    assert result.text == "压缩后成功"
    assert len(captured) == 2
    assert estimate_text_tokens(captured[1]) < estimate_text_tokens(captured[0])
    assert "任务开头" in captured[1]
    assert "任务结尾" in captured[1]


@pytest.mark.asyncio
async def test_continuation_review_gates_build_future_chapters(db: Session) -> None:
    with db.begin():
        overview = studio.create_continuation_project(
            db,
            ContinuationImportRequest(
                title="断章",
                text="## 第1章 起点\n开端。\n## 第2章 断点\n故事停在门前。",
                target_chapters=4,
                target_volumes=2,
            ),
        )
    project_id = int(overview["project"]["id"])

    for phase, next_stage in [
        ("continuation_analysis", "continuation_outline"),
        ("continuation_outline", "continuation_plan"),
        ("continuation_plan", "drafting"),
    ]:
        generated = await studio.generate(
            db, project_id, phase, GenerateRequest(use_demo_model=True)
        )
        assert all(item["status"] == "pending" for item in generated["artifacts"])
        assert studio.project_overview(db, project_id)["state"]["stage"] == phase
        for artifact in generated["artifacts"]:
            studio.decide_artifact(
                db,
                int(artifact["id"]),
                ArtifactDecision(
                    action="approve", expected_revision=int(artifact["revision"])
                ),
            )
        db.commit()
        assert studio.project_overview(db, project_id)["state"]["stage"] == next_stage

    result = studio.project_overview(db, project_id)
    assert len(result["tree"]["chapters"]) == 4
    assert len(result["tree"]["volumes"]) == 2
    assert result["state"]["config"]["plan_confirmed"] is True


def test_continuation_current_chapter_approval_appends_without_overwrite(db: Session) -> None:
    with db.begin():
        overview = studio.create_continuation_project(
            db,
            ContinuationImportRequest(
                title="未完之章",
                text="## 第1章 门后\n她推开门。",
                continuation_start="current",
            ),
        )
    project_id = int(overview["project"]["id"])
    chapter = db.get(models.Chapter, int(overview["tree"]["chapters"][0]["id"]))
    assert chapter is not None
    artifact = models.CreativeArtifact(
        project_id=project_id,
        kind="drafting",
        title="续写正文",
        content="门后站着失踪多年的父亲。",
        status="pending",
        metadata_json=f'{{"chapter_id":{chapter.id},"mode":"continue"}}',
    )
    db.add(artifact)
    db.commit()

    studio.decide_artifact(
        db,
        artifact.id,
        ArtifactDecision(action="approve", expected_revision=artifact.revision),
    )
    db.commit()
    db.refresh(chapter)
    assert chapter.content == "她推开门。\n\n门后站着失踪多年的父亲。"
    assert db.scalar(
        select(models.ProjectSnapshot).where(
            models.ProjectSnapshot.project_id == project_id,
            models.ProjectSnapshot.label == "AI 正文写入前",
        )
    ) is not None


def test_continuation_conflict_pause_requires_and_clears_author_decision(db: Session) -> None:
    with db.begin():
        overview = studio.create_continuation_project(
            db,
            ContinuationImportRequest(title="冲突续篇", text="## 第1章\n旧设定。"),
        )
    project_id = int(overview["project"]["id"])
    artifact = models.CreativeArtifact(
        project_id=project_id,
        kind="continuation_analysis",
        title="世界观提取",
        content="[重大冲突] 新旧身份不一致。",
        status="pending",
        metadata_json='{"agent_name":"世界观提取","series_key":"continuation:conflict"}',
    )
    studio._mark_conflicts(artifact)
    db.add(artifact)
    state = studio._state(db, project_id)
    config = studio._json_object(state.config_json)
    config["conflict_paused"] = True
    state.config_json = studio._dump(config)
    db.commit()

    result = studio.decide_artifact(
        db,
        artifact.id,
        ArtifactDecision(
            action="approve",
            conflict_resolution="preserve_canon",
            expected_revision=artifact.revision,
        ),
    )
    db.commit()

    assert result["status"] == "rejected"
    assert studio.project_overview(db, project_id)["state"]["config"]["conflict_paused"] is False


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


def test_chapter_plan_ignores_agent_heading_and_fills_requested_count(db: Session) -> None:
    overview = create_project(db)
    project_id = int(overview["project"]["id"])  # type: ignore[index]
    plan = """## 章节规划师
# 第一卷 矿井中的火种
## 第1章 深渊之下
## 第2章 血色的秘密
## 第3章 最初的觉醒
"""
    db.add(
        models.CreativeArtifact(
            project_id=project_id,
            kind="chapters",
            title="章节规划师",
            content=plan,
            status="approved",
            metadata_json='{"agent_name":"章节规划师"}',
        )
    )
    db.flush()

    studio._ensure_chapter_tree_from_plan(db, project_id)

    chapters = studio.project_overview(db, project_id)["tree"]["chapters"]
    assert [item["title"] for item in chapters] == [
        "第1章 深渊之下",
        "第2章 血色的秘密",
        "第3章 最初的觉醒",
        "第4章",
    ]
    assert studio._chapter_generation_ranges(80) == [
        (1, 10), (11, 20), (21, 30), (31, 40),
        (41, 50), (51, 60), (61, 70), (71, 80),
    ]


def test_chapter_tree_repair_is_confirmed_snapshotted_and_reversible(db: Session) -> None:
    overview = create_project(db)
    project_id = int(overview["project"]["id"])  # type: ignore[index]
    volume = models.Volume(project_id=project_id, title="第一卷", position=1)
    db.add(volume)
    db.flush()
    suspect = models.Chapter(
        volume_id=volume.id,
        title="章节规划师",
        content="误写入该占位章节的正文",
        position=1,
        word_count=10,
    )
    db.add(suspect)
    for position in range(1, 4):
        db.add(
            models.Chapter(
                volume_id=volume.id,
                title=f"第{position}章",
                content="已完成正文",
                position=position + 1,
                word_count=5,
            )
        )
    db.flush()

    preview = studio.chapter_tree_repair_preview(db, project_id)
    assert preview["can_repair"] is True
    assert preview["missing_numbers"] == [4]
    assert preview["suspect_chapters"][0]["id"] == suspect.id

    with pytest.raises(HTTPException, match="确认"):
        studio.repair_chapter_tree(
            db, project_id, ChapterTreeRepairRequest(confirm=False)
        )

    result = studio.repair_chapter_tree(
        db, project_id, ChapterTreeRepairRequest(confirm=True)
    )
    db.flush()

    assert result["repaired"] is True
    assert [item["title"] for item in result["overview"]["tree"]["chapters"]] == [
        "第1章",
        "第2章",
        "第3章",
        "第4章",
    ]
    db.refresh(suspect)
    assert suspect.deleted_at is not None
    snapshot = db.scalar(
        select(models.ProjectSnapshot).where(
            models.ProjectSnapshot.project_id == project_id,
            models.ProjectSnapshot.label == "修复章节结构前",
        )
    )
    assert snapshot is not None
    assert snapshot.permanent is True
    assert "章节规划师" in snapshot.payload_json


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


def test_approving_agent_draft_writes_chapter_and_creates_snapshot(db: Session) -> None:
    overview = create_project(db, "outline")
    project_id = int(overview["project"]["id"])  # type: ignore[index]
    with db.begin():
        studio.import_outline(
            db,
            project_id,
            OutlineImportRequest(text="# 第一卷\n## 第一章 深渊之下"),
        )
    chapter = db.scalar(
        select(models.Chapter)
        .join(models.Volume, models.Volume.id == models.Chapter.volume_id)
        .where(models.Volume.project_id == project_id)
    )
    assert chapter is not None
    original_revision = chapter.revision
    content = "雾从断桥下升起，林雾握紧了铜钥匙。"
    artifact = models.CreativeArtifact(
        project_id=project_id,
        kind="drafting",
        title="正文创作",
        content=content,
        status="pending",
        metadata_json=f'{{"chapter_id":{chapter.id},"mode":"new"}}',
    )
    db.add(artifact)
    db.commit()

    result = studio.decide_artifact(
        db,
        artifact.id,
        ArtifactDecision(action="approve", expected_revision=artifact.revision),
    )
    db.commit()

    db.refresh(chapter)
    assert result["status"] == "approved"
    assert chapter.content == content
    assert chapter.word_count == word_count(content)
    assert chapter.revision == original_revision + 1
    assert db.scalar(
        select(models.ProjectSnapshot).where(
            models.ProjectSnapshot.project_id == project_id,
            models.ProjectSnapshot.label == "AI 正文写入前",
        )
    ) is not None


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
