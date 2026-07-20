from __future__ import annotations

import hashlib
import io
import json
import zipfile
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from app import models
from app.api.release import router as release_router
from app.core.security import LocalOriginMiddleware, SecurityHeadersMiddleware
from app.database import Base, get_db
from app.services import release_backup
from app.services.release_backup import (
    create_backup_archive,
    load_backup_archive,
    preview_backup_archive,
    restore_backup_archive,
)
from app.services.release_exports import build_export


@pytest.fixture
def db(tmp_path: Path) -> Generator[Session, None, None]:
    engine = create_engine(f"sqlite:///{(tmp_path / 'phase8.db').as_posix()}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as session:
        yield session
    engine.dispose()


def seed_release_data(db: Session) -> dict[str, Any]:
    project = models.Project(title="雾港回声", summary="一座沿海旧城的悬疑故事")
    db.add(project)
    db.flush()
    volume = models.Volume(project_id=project.id, title="潮汐卷", position=1)
    db.add(volume)
    db.flush()
    chapter = models.Chapter(
        volume_id=volume.id,
        title="夜航",
        content="雾从防波堤漫上来，林栀听见旧钟响了三次。",
        position=1,
        word_count=22,
    )
    db.add(chapter)
    entity = models.StoryEntity(
        project_id=project.id,
        name="林栀",
        kind="character",
        description="港口档案员",
        tags='["主角", "调查者"]',
    )
    db.add(entity)
    db.flush()
    db.add(models.EntityAlias(entity_id=entity.id, alias="小栀"))
    db.add(
        models.TimelineEvent(
            project_id=project.id,
            chapter_id=chapter.id,
            label="旧钟三响",
            event_time="雨季第三夜",
            description="林栀开始调查失踪船只。",
            position=1,
        )
    )
    db.add(
        models.Foreshadow(
            project_id=project.id,
            chapter_id=chapter.id,
            setup_text="锈蚀船铃内刻着日期",
            payoff_text="",
            status="open",
        )
    )
    db.add(models.StyleGuide(project_id=project.id, name="叙事视角", rule_text="限知第三人称"))

    provider = models.ProviderAccount(
        name="Release Mock Provider",
        provider_type="mock",
        credential_env_var="PHASE8_TEST_KEY",
    )
    db.add(provider)
    db.flush()
    model = models.ModelProfile(
        provider_account_id=provider.id,
        name="mock-release",
        display_name="Mock Release",
        context_window=8192,
        enabled=True,
    )
    db.add(model)
    db.flush()
    agent = models.AgentDefinition(
        project_id=project.id,
        name="总编",
        agent_type="editor",
        system_prompt="检查连贯性",
        prompt_template="请审阅 {{ input }}",
        input_schema_json="{}",
        output_schema_json="{}",
        output_mode="text",
        model_profile_id=model.id,
        parameters_json="{}",
        required_capabilities_json="[]",
        budget_json="{}",
        config_hash="0" * 64,
    )
    db.add(agent)
    workflow = models.Workflow(project_id=project.id, name="发布检查工作流")
    db.add(workflow)
    db.flush()
    db.add_all(
        [
            models.WorkflowNode(
                workflow_id=workflow.id,
                node_key="start",
                node_type="start",
                label="开始",
                config_json="{}",
            ),
            models.WorkflowNode(
                workflow_id=workflow.id,
                node_key="output",
                node_type="output",
                label="输出",
                config_json="{}",
            ),
            models.WorkflowEdge(
                workflow_id=workflow.id,
                edge_key="start-output",
                source_node_key="start",
                target_node_key="output",
            ),
        ]
    )
    adapter_provider = models.ProviderAccount(
        name="Release Generic Provider",
        provider_type="generic_json_http",
        base_url="https://example.com",
    )
    db.add(adapter_provider)
    db.flush()
    db.add(
        models.GenericHttpAdapterConfiguration(
            provider_account_id=adapter_provider.id,
            endpoint="/v1/generate",
            request_template_json='{"prompt":{"$var":"prompt"}}',
            response_mapping_json='{"text":"$.text"}',
            auth_json='{"type":"none"}',
            enabled=False,
        )
    )
    db.commit()
    return {
        "project_id": project.id,
        "chapter_id": chapter.id,
        "chapter_content": chapter.content,
    }


def test_complete_backup_round_trip_and_fts_rebuild(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = seed_release_data(db)
    monkeypatch.setenv("PHASE8_TEST_KEY", "sk-live-value-must-never-leak")
    archive = create_backup_archive(db)
    assert b"sk-live-value-must-never-leak" not in archive
    loaded = load_backup_archive(archive)
    assert loaded.manifest.format == "novel-agent-studio-backup"
    assert loaded.secret_findings == []
    db.commit()

    chapter = db.get(models.Chapter, seeded["chapter_id"])
    assert chapter is not None
    chapter.content = "这段内容必须被覆盖恢复替换"
    db.add(models.Project(title="恢复时应消失的临时项目"))
    db.commit()

    with db.begin():
        result = restore_backup_archive(
            db,
            archive,
            strategy="replace_all",
            expected_sha256=hashlib.sha256(archive).hexdigest(),
        )
    restored = db.get(models.Chapter, seeded["chapter_id"])
    assert restored is not None
    assert restored.content == seeded["chapter_content"]
    assert db.scalar(select(func.count(models.Project.id))) == 1
    assert result.fts_records > 0
    assert db.scalar(text("SELECT count(*) FROM context_fts")) == result.fts_records


def test_restore_failure_rolls_back_original_database(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = seed_release_data(db)
    archive = create_backup_archive(db)
    db.commit()
    chapter = db.get(models.Chapter, seeded["chapter_id"])
    assert chapter is not None
    chapter.content = "恢复失败后必须保留的当前正文"
    db.commit()

    def fail_fts(_db: Session, _project_id: int) -> int:
        raise OSError("simulated disk full")

    monkeypatch.setattr(release_backup, "rebuild_fts_index", fail_fts)
    with pytest.raises(OSError, match="disk full"):
        with db.begin():
            restore_backup_archive(
                db,
                archive,
                strategy="replace_all",
                expected_sha256=hashlib.sha256(archive).hexdigest(),
            )
    db.expire_all()
    current = db.get(models.Chapter, seeded["chapter_id"])
    assert current is not None
    assert current.content == "恢复失败后必须保留的当前正文"


def test_backup_rejects_traversal_schema_tampering_and_secret_material(db: Session) -> None:
    seed_release_data(db)
    archive = create_backup_archive(db)
    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        manifest = json.loads(source.read("manifest.json"))
        data = json.loads(source.read("data.json"))

    traversal = io.BytesIO()
    with zipfile.ZipFile(traversal, "w") as target:
        target.writestr("manifest.json", json.dumps(manifest))
        target.writestr("data.json", json.dumps(data))
        target.writestr("../outside.txt", "blocked")
    with pytest.raises(ValueError, match="只能包含"):
        load_backup_archive(traversal.getvalue())

    data["tables"].pop("chapters")
    tampered = _repack(manifest, data)
    with pytest.raises(ValueError, match="Schema"):
        load_backup_archive(tampered)

    with zipfile.ZipFile(io.BytesIO(archive)) as source:
        manifest = json.loads(source.read("manifest.json"))
        data = json.loads(source.read("data.json"))
    data["tables"]["chapters"][0]["content"] = "Bearer abcdefghijklmnop-secret"
    secret_archive = _repack(manifest, data)
    preview = preview_backup_archive(db, secret_archive)
    assert preview.can_restore is False
    assert preview.secret_findings


def test_all_release_exports_are_real_and_redacted(db: Session) -> None:
    seeded = seed_release_data(db)
    project_id = seeded["project_id"]
    chapter_id = seeded["chapter_id"]
    kinds = {
        "book_markdown",
        "chapter_markdown",
        "library_json",
        "timeline_csv",
        "foreshadows_json",
        "agents_json",
        "workflows_json",
        "adapters_json",
        "diagnostics_zip",
    }
    artifacts = {
        kind: build_export(
            db,
            kind,  # type: ignore[arg-type]
            project_id=project_id,
            chapter_id=chapter_id,
            frontend_bundled=True,
        )
        for kind in kinds
    }
    assert "雾从防波堤" in artifacts["book_markdown"].content.decode("utf-8")
    assert "雾从防波堤" in artifacts["chapter_markdown"].content.decode("utf-8")
    library = json.loads(artifacts["library_json"].content)
    assert library["entities"][0]["tags"] == ["主角", "调查者"]
    assert artifacts["timeline_csv"].content.startswith(b"\xef\xbb\xbf")
    assert json.loads(artifacts["foreshadows_json"].content)["foreshadows"]
    assert json.loads(artifacts["agents_json"].content)["agents"][0]["name"] == "总编"
    assert json.loads(artifacts["workflows_json"].content)["workflows"][0]["nodes"]
    adapter = json.loads(artifacts["adapters_json"].content)["adapters"][0]
    assert adapter["config"]["credential_reference_id"] is None
    assert adapter["config"]["enabled"] is False
    with zipfile.ZipFile(io.BytesIO(artifacts["diagnostics_zip"].content)) as diagnostics:
        payload = json.loads(diagnostics.read("diagnostics.json"))
    assert payload["privacy"]["credentials_included"] is False
    assert payload["privacy"]["manuscript_content_included"] is False
    assert "雾从防波堤" not in json.dumps(payload, ensure_ascii=False)


def test_release_api_stream_limits_mime_and_security_headers(tmp_path: Path) -> None:
    database = tmp_path / "api.db"
    engine = create_engine(f"sqlite:///{database.as_posix()}")
    Base.metadata.create_all(engine)
    local_session = sessionmaker(engine, expire_on_commit=False)
    with local_session() as session:
        seed_release_data(session)

    api_app = FastAPI()
    api_app.state.frontend_bundled = False
    api_app.add_middleware(SecurityHeadersMiddleware)
    api_app.add_middleware(
        LocalOriginMiddleware, allowed_origins=["http://127.0.0.1:5173"]
    )
    api_app.include_router(release_router, prefix="/api")

    @api_app.post("/same-origin")
    def same_origin_write() -> dict[str, bool]:
        return {"ok": True}

    def override_db() -> Generator[Session, None, None]:
        with local_session() as session:
            yield session

    api_app.dependency_overrides[get_db] = override_db
    client = TestClient(api_app)
    backup = client.get("/api/release/backup")
    assert backup.status_code == 200
    assert backup.content.startswith(b"PK")
    assert backup.headers["x-content-type-options"] == "nosniff"
    assert backup.headers["content-security-policy"].startswith("default-src")

    bad_mime = client.post(
        "/api/release/backup/preview",
        content=backup.content,
        headers={"Content-Type": "text/plain"},
    )
    assert bad_mime.status_code == 415
    preview = client.post(
        "/api/release/backup/preview",
        content=backup.content,
        headers={"Content-Type": "application/zip"},
    )
    assert preview.status_code == 200
    assert preview.json()["can_restore"] is True
    same_origin = client.post(
        "/same-origin", headers={"Origin": "http://testserver"}
    )
    assert same_origin.status_code == 200
    blocked = client.delete(
        "/api/release/logs", headers={"Origin": "https://malicious.example"}
    )
    assert blocked.status_code == 403
    engine.dispose()


def _repack(manifest: dict[str, Any], data: dict[str, Any]) -> bytes:
    data_bytes = json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    next_manifest = dict(manifest)
    next_manifest["data_sha256"] = hashlib.sha256(data_bytes).hexdigest()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(next_manifest, ensure_ascii=False))
        archive.writestr("data.json", data_bytes)
    return output.getvalue()
