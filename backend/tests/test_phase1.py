import os
from collections.abc import Generator
from typing import cast

import pytest

os.environ["NAS_DATABASE_URL"] = "sqlite:///:memory:"

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app import models
from app.database import Base, get_db
from app.main import app
from app.repositories import create_seed_data, word_count
from app.services import models as model_service


engine = create_engine(
    "sqlite://",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
TestingSessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def override_get_db() -> Generator[Session, None, None]:
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db
client = TestClient(app)


@pytest.fixture(autouse=True)
def clean_database() -> Generator[None, None, None]:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def create_project(title: str = "测试长篇") -> dict[str, object]:
    response = client.post("/api/projects", json={"title": title})
    assert response.status_code == 201
    return cast(dict[str, object], response.json())


def test_project_chapter_autosave_version_and_conflict() -> None:
    created = create_project()
    tree = client.get(f"/api/projects/{created['id']}/tree").json()
    chapter = tree["chapters"][0]

    saved = client.put(
        f"/api/projects/chapters/{chapter['id']}/autosave",
        json={
            "title": "第一章 修订",
            "content": "她在雨夜推开档案馆的门。",
            "expected_revision": chapter["revision"],
        },
    )
    assert saved.status_code == 200
    assert saved.json()["revision"] == chapter["revision"] + 1
    assert saved.json()["word_count"] == 11

    conflict = client.put(
        f"/api/projects/chapters/{chapter['id']}/autosave",
        json={"title": "旧修订", "content": "旧内容", "expected_revision": chapter["revision"]},
    )
    assert conflict.status_code == 409

    versions = client.get(f"/api/projects/chapters/{chapter['id']}/versions").json()
    assert len(versions) == 1
    restored = client.post(
        f"/api/projects/chapters/{chapter['id']}/versions/{versions[0]['id']}/restore",
        params={"expected_revision": saved.json()["revision"]},
    )
    assert restored.status_code == 200
    assert restored.json()["content"] == ""
    assert len(client.get(f"/api/projects/chapters/{chapter['id']}/versions").json()) == 2


def test_project_volume_scene_update_reorder_delete_and_restore() -> None:
    project = create_project()
    updated = client.put(
        f"/api/projects/{project['id']}",
        json={
            "title": "修改后的长篇",
            "summary": "简介",
            "language": "zh-CN",
            "target_words": 160000,
            "expected_revision": project["revision"],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["target_words"] == 160000
    stale = client.put(
        f"/api/projects/{project['id']}",
        json={
            "title": "过期写入",
            "summary": "",
            "language": "zh-CN",
            "target_words": 1,
            "expected_revision": project["revision"],
        },
    )
    assert stale.status_code == 409

    tree = client.get(f"/api/projects/{project['id']}/tree").json()
    volume = tree["volumes"][0]
    second = client.post(
        f"/api/projects/volumes/{volume['id']}/chapters",
        json={"title": "第二章", "content": "", "position": 2},
    ).json()
    first = tree["chapters"][0]
    reordered = client.post(
        "/api/projects/reorder/chapter",
        json={
            "items": [
                {"id": second["id"], "position": 0, "expected_revision": second["revision"]},
                {"id": first["id"], "position": 1, "expected_revision": first["revision"]},
            ]
        },
    )
    assert reordered.status_code == 200
    assert client.get(f"/api/projects/{project['id']}/tree").json()["chapters"][0]["id"] == second["id"]

    scene = client.post(
        f"/api/projects/chapters/{second['id']}/scenes",
        json={"title": "码头争执", "synopsis": "", "content": "", "position": 1},
    ).json()
    changed_scene = client.put(
        f"/api/projects/scenes/{scene['id']}",
        json={
            "title": "码头对峙",
            "synopsis": "双方第一次正面冲突",
            "content": "雾里传来脚步声。",
            "position": 1,
            "expected_revision": scene["revision"],
        },
    )
    assert changed_scene.status_code == 200

    deleted = client.delete(
        f"/api/projects/records/scene/{scene['id']}",
        params={"expected_revision": changed_scene.json()["revision"]},
    )
    assert deleted.status_code == 204
    trash = client.get(f"/api/projects/{project['id']}/trash").json()
    assert [item["id"] for item in trash["scenes"]] == [scene["id"]]
    restored = client.post(
        f"/api/projects/records/scene/{scene['id']}/restore",
        params={"expected_revision": changed_scene.json()["revision"] + 1},
    )
    assert restored.status_code == 204


def test_library_records_are_real_crud_resources() -> None:
    project = create_project()
    project_id = cast(int, project["id"])
    tree = client.get(f"/api/projects/{project_id}/tree").json()
    chapter_id = tree["chapters"][0]["id"]

    person = client.post(
        f"/api/projects/{project_id}/entities",
        json={"name": "林栀", "kind": "character", "description": "档案员", "tags": ["主角"]},
    ).json()
    location = client.post(
        f"/api/projects/{project_id}/entities",
        json={"name": "旧码头", "kind": "location", "description": "", "tags": []},
    ).json()
    assert person["tags"] == ["主角"]
    alias = client.post(
        f"/api/projects/entities/{person['id']}/aliases", json={"alias": "小栀"}
    )
    assert alias.status_code == 201

    relation = client.post(
        f"/api/projects/{project_id}/relations",
        json={
            "source_entity_id": person["id"],
            "target_entity_id": location["id"],
            "relation_type": "常去地点",
            "notes": "",
        },
    )
    assert relation.status_code == 201
    assert client.post(
        f"/api/projects/{project_id}/relations",
        json={
            "source_entity_id": person["id"],
            "target_entity_id": person["id"],
            "relation_type": "无效",
            "notes": "",
        },
    ).status_code == 422

    state = client.post(
        f"/api/projects/{project_id}/state-changes",
        json={
            "entity_id": person["id"],
            "chapter_id": chapter_id,
            "field_name": "位置",
            "old_value": "档案馆",
            "new_value": "旧码头",
            "reason": "调查线索",
        },
    )
    assert state.status_code == 201
    timeline = client.post(
        f"/api/projects/{project_id}/timeline",
        json={
            "chapter_id": chapter_id,
            "label": "收到失踪电报",
            "event_time": "冬至前夜",
            "description": "主线开始",
            "position": 1,
        },
    )
    assert timeline.status_code == 201
    foreshadow = client.post(
        f"/api/projects/{project_id}/foreshadows",
        json={
            "setup_text": "无线电呼出林栀的名字",
            "payoff_text": "",
            "status": "open",
            "chapter_id": chapter_id,
        },
    )
    assert foreshadow.status_code == 201
    style = client.post(
        f"/api/projects/{project_id}/style-guides",
        json={"name": "叙述口吻", "rule_text": "克制，避免滥用感叹号。", "category": "voice"},
    )
    assert style.status_code == 201

    assert len(client.get(f"/api/projects/{project_id}/aliases").json()) == 1
    assert len(client.get(f"/api/projects/{project_id}/relations").json()) == 1
    assert len(client.get(f"/api/projects/{project_id}/state-changes").json()) == 1
    assert len(client.get(f"/api/projects/{project_id}/timeline").json()) == 1
    assert len(client.get(f"/api/projects/{project_id}/foreshadows").json()) == 1
    assert len(client.get(f"/api/projects/{project_id}/style-guides").json()) == 1


def test_provider_stores_env_var_name_only_and_mock_gateway_streams() -> None:
    provider = client.post(
        "/api/model-center/providers",
        json={
            "name": "DeepSeek",
            "provider_type": "openai_compatible",
            "credential_env_var": "DEEPSEEK_API_KEY",
        },
    )
    assert provider.status_code == 201
    assert provider.json()["credential_env_var"] == "DEEPSEEK_API_KEY"
    assert "sk-" not in str(provider.json())

    payload = {
        "model": "mock-novel-v1",
        "messages": [{"role": "user", "content": [{"type": "text", "text": "写一段开场"}]}],
    }
    response = client.post("/api/model-center/debug", json=payload)
    assert response.status_code == 200
    assert response.json()["usage"]["total_tokens"] > 0
    with client.stream("POST", "/api/model-center/debug/stream", json={**payload, "stream": True}) as stream:
        body = "".join(stream.iter_text())
    assert "event: delta" in body
    assert "event: usage" in body
    assert "event: done" in body


def test_seed_is_idempotent_and_word_count_handles_mixed_text() -> None:
    with TestingSessionLocal() as db:
        with db.begin():
            first = create_seed_data(db)
        first_id = first.id
        with db.begin():
            second = create_seed_data(db)
        assert second.id == first_id
        assert len(db.scalars(select(models.Project)).all()) == 1
        assert len(db.scalars(select(models.ProviderAccount)).all()) == 1
    assert word_count("雾港 echo returns 2026!") == 5
    assert word_count("<p>雾港 <strong>echo</strong></p>") == 3


def test_model_center_presets_connection_sync_and_selected_debug() -> None:
    with TestingSessionLocal() as db:
        with db.begin():
            model_service.ensure_provider_presets(db)

    presets = client.get("/api/model-center/presets")
    assert presets.status_code == 200
    assert {item["slug"] for item in presets.json()} == {
        "openai",
        "deepseek",
        "xai",
        "anthropic",
        "gemini",
        "openrouter",
        "ollama",
        "openai-compatible",
        "anthropic-compatible",
    }
    deepseek = next(item for item in presets.json() if item["slug"] == "deepseek")
    changed = client.put(
        f"/api/model-center/presets/{deepseek['id']}",
        json={
            **deepseek,
            "base_url": "https://gateway.example/v1",
            "expected_revision": deepseek["revision"],
        },
    )
    assert changed.status_code == 200
    assert changed.json()["base_url"] == "https://gateway.example/v1"

    provider = client.post(
        "/api/model-center/providers",
        json={"name": "本地 Mock", "provider_type": "mock", "enabled": True},
    )
    assert provider.status_code == 201
    provider_id = provider.json()["id"]
    connection = client.post(f"/api/model-center/providers/{provider_id}/test")
    assert connection.status_code == 200
    assert connection.json()["ok"] is True
    assert connection.json()["model_count"] == 1

    synced = client.post(f"/api/model-center/providers/{provider_id}/sync")
    assert synced.status_code == 200
    assert synced.json()["created"] == 1
    model = synced.json()["models"][0]
    assert model["name"] == "mock-novel-v1"
    updated_model = client.put(
        f"/api/model-center/models/{model['id']}",
        json={
            "display_name": "Mock 小说模型",
            "context_window": 16384,
            "enabled": True,
            "expected_revision": model["revision"],
        },
    )
    assert updated_model.status_code == 200
    assert updated_model.json()["context_window"] == 16384

    response = client.post(
        "/api/model-center/debug",
        json={
            "provider_account_id": provider_id,
            "model": model["name"],
            "messages": [
                {"role": "user", "content": [{"type": "text", "text": "写一段雾港开场"}]}
            ],
        },
    )
    assert response.status_code == 200
    assert response.json()["model"] == "mock-novel-v1"
    assert response.json()["error"] is None


def test_provider_connection_reports_missing_env_without_leaking_secret() -> None:
    os.environ.pop("PHASE2_MISSING_API_KEY", None)
    provider = client.post(
        "/api/model-center/providers",
        json={
            "name": "隔离凭据测试",
            "provider_type": "openai_chat",
            "credential_env_var": "PHASE2_MISSING_API_KEY",
            "base_url": "https://invalid.example/v1",
        },
    )
    assert provider.status_code == 201
    result = client.post(f"/api/model-center/providers/{provider.json()['id']}/test")
    assert result.status_code == 200
    payload = result.json()
    assert payload["ok"] is False
    assert payload["error"]["code"] == "authentication"
    assert "PHASE2_MISSING_API_KEY" in payload["error"]["message"]
    assert "sk-" not in result.text


def test_custom_adapter_api_crud_and_secret_free_manifest() -> None:
    provider = client.post(
        "/api/model-center/providers",
        json={
            "name": "自定义 JSON API",
            "provider_type": "generic_json_http",
            "base_url": "https://api.example.com/v1",
            "enabled": True,
        },
    )
    assert provider.status_code == 201
    credential = client.post(
        "/api/custom-api/credentials",
        json={"name": "自定义凭据", "env_var_name": "CUSTOM_JSON_API_KEY"},
    )
    assert credential.status_code == 201
    assert "CUSTOM_JSON_API_KEY" in credential.text
    assert "sk-" not in credential.text

    adapter = client.post(
        "/api/custom-api/adapters",
        json={
            "provider_account_id": provider.json()["id"],
            "credential_reference_id": credential.json()["id"],
            "method": "POST",
            "endpoint": "/chat",
            "request_template": {
                "model": {"$var": "model"},
                "messages": {"$var": "messages"},
            },
            "response_mapping": {
                "text": "$.choices[0].message.content",
                "model": "$.model",
            },
            "auth": {"type": "api_key_header", "header_name": "X-Provider-Key"},
        },
    )
    assert adapter.status_code == 201
    assert adapter.json()["enabled"] is False
    assert adapter.json()["credential_reference_name"] == "自定义凭据"
    listed = client.get("/api/custom-api/adapters")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [adapter.json()["id"]]

    manifest = client.get(
        f"/api/custom-api/adapters/{adapter.json()['id']}/manifest"
    )
    assert manifest.status_code == 200
    assert manifest.json()["config"]["credential_reference_id"] is None
    assert manifest.json()["config"]["enabled"] is False
    serialized = manifest.text
    assert "CUSTOM_JSON_API_KEY" not in serialized
    assert "X-Provider-Key" in serialized

    imported = client.post("/api/custom-api/manifests/import", json=manifest.json())
    assert imported.status_code == 201
    assert imported.json()["adapter"]["enabled"] is False
    assert imported.json()["adapter"]["credential_reference_id"] is None

    rejected = client.post(
        "/api/custom-api/adapters",
        json={
            "provider_account_id": imported.json()["provider_id"],
            "endpoint": "https://attacker.example/collect",
            "headers": {"Authorization": "Bearer sk-leaked"},
        },
    )
    assert rejected.status_code == 422


def test_custom_adapter_atomic_setup_rolls_back_provider_on_failure() -> None:
    failed = client.post(
        "/api/custom-api/adapters/setup",
        json={
            "provider_name": "必须回滚的 Provider",
            "base_url": "https://rollback.example/v1",
            "credential_reference_id": 999_999,
            "endpoint": "/chat",
        },
    )
    assert failed.status_code == 404
    providers = client.get("/api/model-center/providers")
    assert providers.status_code == 200
    assert all(
        provider["name"] != "必须回滚的 Provider"
        for provider in providers.json()
    )

    created = client.post(
        "/api/custom-api/adapters/setup",
        json={
            "provider_name": "事务式自定义 API",
            "base_url": "https://atomic.example/v1",
            "endpoint": "/chat",
        },
    )
    assert created.status_code == 201
    assert created.json()["enabled"] is False
    providers = client.get("/api/model-center/providers").json()
    provider = next(item for item in providers if item["name"] == "事务式自定义 API")
    assert provider["id"] == created.json()["provider_account_id"]
