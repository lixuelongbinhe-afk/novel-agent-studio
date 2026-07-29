from __future__ import annotations

import json
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from app.migrations import (
    PHASE_1_REVISION,
    current_schema_revision,
    upgrade_database,
)


BACKEND_ROOT = Path(__file__).resolve().parents[1]


def database_url(path: Path) -> str:
    return f"sqlite:///{path.as_posix()}"


def alembic_config(url: str) -> Config:
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    config.attributes["database_url"] = url
    config.set_main_option("sqlalchemy.url", url)
    return config


def test_empty_database_reaches_studio_v2_with_presets(tmp_path: Path) -> None:
    url = database_url(tmp_path / "empty.db")
    upgrade_database(url)
    engine = create_engine(url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert "credential_references" in tables
        assert "generic_http_adapter_configurations" in tables
        assert "model_routes" in tables
        assert "budget_policies" in tables
        assert "model_invocations" in tables
        assert "agent_definitions" in tables
        assert "workflows" in tables
        assert "workflow_runs" in tables
        assert "workflow_run_events" in tables
        assert "node_run_attempts" in tables
        assert "chapter_summaries" in tables
        assert "scene_states" in tables
        assert "context_policies" in tables
        assert "provider_data_policies" in tables
        assert "context_builds" in tables
        assert "context_fts" in tables
        assert "approval_requests" in tables
        assert "proposed_change_sets" in tables
        assert "writeback_audits" in tables
        assert "studio_project_states" in tables
        assert "creative_artifacts" in tables
        assert "studio_messages" in tables
        assert "generation_jobs" in tables
        assert "project_snapshots" in tables
        pricing_columns = {
            item["name"] for item in inspect(engine).get_columns("model_pricing")
        }
        assert {"request_fee", "tool_call_fee", "currency"} <= pricing_columns
        model_columns = {
            item["name"] for item in inspect(engine).get_columns("model_profiles")
        }
        assert {"tokenizer_name", "tokenizer_source"} <= model_columns
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == current_schema_revision()
            active_scope_index = connection.scalar(
                text(
                    "SELECT sql FROM sqlite_master "
                    "WHERE type = 'index' AND name = 'uq_generation_job_active_scope'"
                )
            )
            assert "deleted_at IS NULL" in str(active_scope_index)
            assert "status IN ('queued','running')" in str(active_scope_index)
            assert connection.scalar(text("SELECT COUNT(*) FROM provider_presets")) == 9
            assert connection.scalar(
                text("SELECT default_model FROM provider_presets WHERE slug = 'deepseek'")
            ) == "deepseek-v4-flash"
    finally:
        engine.dispose()


def test_failed_migration_restores_online_backup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "failed-upgrade.db"
    url = database_url(database)
    command.upgrade(alembic_config(url), PHASE_1_REVISION)
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO projects "
                    "(id, title, summary, language, target_words, created_at, updated_at, revision) "
                    "VALUES (1, 'must survive', '', 'zh-CN', 1000, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1)"
                )
            )
    finally:
        engine.dispose()

    def fail_after_write(_config: Config, _revision: str) -> None:
        damaged = create_engine(url)
        try:
            with damaged.begin() as connection:
                connection.execute(text("DELETE FROM projects"))
        finally:
            damaged.dispose()
        raise RuntimeError("simulated migration failure")

    monkeypatch.setattr("app.migrations.command.upgrade", fail_after_write)
    with pytest.raises(RuntimeError, match="simulated migration failure"):
        upgrade_database(url)

    restored = create_engine(url)
    try:
        with restored.connect() as connection:
            assert connection.scalar(text("SELECT title FROM projects WHERE id = 1")) == (
                "must survive"
            )
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                PHASE_1_REVISION
            )
    finally:
        restored.dispose()
    journal = json.loads(
        database.with_suffix(".db.migration.json").read_text(encoding="utf-8")
    )
    assert journal["status"] == "failed_restored"


def test_migration_refuses_insufficient_backup_space(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "no-space.db"
    url = database_url(database)
    command.upgrade(alembic_config(url), PHASE_1_REVISION)

    class DiskUsage:
        total = 1
        used = 1
        free = 0

    monkeypatch.setattr("app.migrations.shutil.disk_usage", lambda _path: DiskUsage())
    with pytest.raises(RuntimeError, match="Insufficient disk space"):
        upgrade_database(url)

    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                PHASE_1_REVISION
            )
    finally:
        engine.dispose()


def test_new_dynamic_head_triggers_backup_without_constant_update(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "dynamic-head.db"
    url = database_url(database)
    upgrade_database(url)

    monkeypatch.setattr(
        "app.migrations.current_schema_revision",
        lambda _config=None: "future_dynamic_head",
    )
    upgrade_database(url)

    journal = json.loads(
        database.with_suffix(".db.migration.json").read_text(encoding="utf-8")
    )
    assert journal["status"] == "completed"
    assert journal["to_revision"] == "future_dynamic_head"
    assert Path(journal["backup"]).is_file()


def test_unversioned_phase_1_database_is_upgraded_without_data_loss(
    tmp_path: Path,
) -> None:
    url = database_url(tmp_path / "legacy-phase-1.db")
    command.upgrade(alembic_config(url), PHASE_1_REVISION)
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO projects "
                    "(id, title, summary, language, target_words, created_at, updated_at, revision) "
                    "VALUES (1, '保留的旧项目', '', 'zh-CN', 100000, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, 1)"
                )
            )
            connection.execute(text("DROP TABLE alembic_version"))
    finally:
        engine.dispose()

    upgrade_database(url)
    journal = json.loads(
        (tmp_path / "legacy-phase-1.db.migration.json").read_text(encoding="utf-8")
    )
    assert journal["status"] == "completed"
    assert journal["from_revision"] == "legacy-unversioned"
    assert Path(journal["backup"]).is_file()
    engine = create_engine(url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert "generic_http_adapter_configurations" in tables
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT title FROM projects WHERE id = 1")) == "保留的旧项目"
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == current_schema_revision()
            assert connection.scalar(text("SELECT COUNT(*) FROM provider_presets")) == 9
            assert connection.scalar(text("SELECT COUNT(*) FROM context_policies")) == 1
    finally:
        engine.dispose()


def test_partial_legacy_database_fails_without_claiming_success(tmp_path: Path) -> None:
    url = database_url(tmp_path / "partial.db")
    engine = create_engine(url)
    try:
        with engine.begin() as connection:
            connection.execute(text("CREATE TABLE projects (id INTEGER PRIMARY KEY)"))
    finally:
        engine.dispose()

    with pytest.raises(RuntimeError, match="incomplete"):
        upgrade_database(url)

    engine = create_engine(url)
    try:
        assert "alembic_version" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_story_order_migration_normalizes_legacy_rows_and_enforces_uniqueness(
    tmp_path: Path,
) -> None:
    url = database_url(tmp_path / "legacy-story-order.db")
    config = alembic_config(url)
    command.upgrade(config, "c5e7a9b1d320")
    engine = create_engine(url)
    timestamp = "2026-01-01 00:00:00"
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO projects "
                    "(id, title, summary, language, target_words, created_at, updated_at, revision) "
                    "VALUES (1, '旧项目', '', 'zh-CN', 100000, :ts, :ts, 1)"
                ),
                {"ts": timestamp},
            )
            connection.execute(
                text(
                    "INSERT INTO volumes "
                    "(id, project_id, title, position, created_at, updated_at, revision) VALUES "
                    "(1, 1, '第一卷', 7, :ts, :ts, 1), "
                    "(2, 1, '第二卷', 7, :ts, :ts, 1)"
                ),
                {"ts": timestamp},
            )
            connection.execute(
                text(
                    "INSERT INTO chapters "
                    "(id, volume_id, title, content, position, word_count, created_at, updated_at, revision) VALUES "
                    "(1, 1, '第一章', '', 5, 0, :ts, :ts, 1), "
                    "(2, 1, '第二章', '', 5, 0, :ts, :ts, 1), "
                    "(3, 2, '第三章', '', 9, 0, :ts, :ts, 1)"
                ),
                {"ts": timestamp},
            )
            connection.execute(
                text(
                    "INSERT INTO scenes "
                    "(id, chapter_id, title, synopsis, content, position, created_at, updated_at, revision) VALUES "
                    "(1, 1, '场景一', '', '', 4, :ts, :ts, 1), "
                    "(2, 1, '场景二', '', '', 4, :ts, :ts, 1)"
                ),
                {"ts": timestamp},
            )
    finally:
        engine.dispose()
    command.upgrade(config, "head")
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            assert connection.execute(
                text("SELECT position FROM volumes ORDER BY id")
            ).scalars().all() == [1, 2]
            assert connection.execute(
                text("SELECT position FROM chapters WHERE volume_id = 1 ORDER BY id")
            ).scalars().all() == [1, 2]
            assert [
                tuple(row)
                for row in connection.execute(
                    text("SELECT project_id, number FROM chapters ORDER BY id")
                ).all()
            ] == [(1, 1), (1, 2), (1, 3)]
            assert connection.execute(
                text("SELECT position FROM scenes ORDER BY id")
            ).scalars().all() == [1, 2]

        with pytest.raises(IntegrityError):
            with engine.begin() as connection:
                connection.execute(
                    text(
                        "INSERT INTO chapters "
                        "(project_id, volume_id, number, title, content, position, word_count, "
                        "created_at, updated_at, revision) "
                        "VALUES (1, 2, 2, '重复第二章', '', 2, 0, :ts, :ts, 1)"
                    ),
                    {"ts": timestamp},
                )
    finally:
        engine.dispose()


def test_deepseek_v4_migration_updates_only_official_deepseek_profiles(
    tmp_path: Path,
) -> None:
    url = database_url(tmp_path / "deepseek-v4.db")
    config = alembic_config(url)
    command.upgrade(config, "e8f1c3d5a740")
    engine = create_engine(url)
    timestamp = "2026-07-25 00:00:00"
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO provider_accounts "
                    "(id, name, provider_type, base_url, enabled, created_at, updated_at, revision) VALUES "
                    "(1, 'DeepSeek official', 'openai_chat', 'https://api.deepseek.com/v1', 1, :ts, :ts, 1), "
                    "(2, 'Compatible gateway', 'openai_chat', 'https://gateway.example/v1', 1, :ts, :ts, 1), "
                    "(3, 'DeepSeek with V4', 'openai_chat', 'https://api.deepseek.com', 1, :ts, :ts, 1), "
                    "(4, 'DeepSeek reasoner only', 'openai_chat', 'https://api.deepseek.com/v1/', 1, :ts, :ts, 1)"
                ),
                {"ts": timestamp},
            )
            connection.execute(
                text(
                    "INSERT INTO model_profiles "
                    "(id, provider_account_id, name, display_name, context_window, enabled, "
                    "created_at, updated_at, revision) VALUES "
                    "(1, 1, 'deepseek-chat', 'deepseek-chat', 128000, 1, :ts, :ts, 1), "
                    "(2, 1, 'deepseek-reasoner', 'My reasoning model', 128000, 1, :ts, :ts, 1), "
                    "(3, 2, 'deepseek-chat', 'Gateway alias', 64000, 1, :ts, :ts, 1), "
                    "(4, 3, 'deepseek-chat', 'Legacy routed model', 128000, 1, :ts, :ts, 1), "
                    "(5, 3, 'deepseek-v4-flash', 'Existing V4 model', 128000, 1, :ts, :ts, 1), "
                    "(6, 4, 'deepseek-reasoner', 'deepseek-reasoner', 128000, 1, :ts, :ts, 1), "
                    "(7, 3, 'deepseek-v4-pro', 'Existing V4 Pro', 128000, 1, :ts, :ts, 1)"
                ),
                {"ts": timestamp},
            )
            connection.execute(
                text(
                    "INSERT INTO model_routes "
                    "(id, name, strategy, required_capabilities_json, allow_degradation, enabled, "
                    "created_at, updated_at, revision) "
                    "VALUES (1, 'DeepSeek route', 'ordered_fallback', '[]', 1, 1, :ts, :ts, 1)"
                ),
                {"ts": timestamp},
            )
            connection.execute(
                text(
                    "INSERT INTO model_route_entries "
                    "(id, route_id, model_profile_id, position, enabled, created_at, updated_at, revision) "
                    "VALUES (1, 1, 4, 1, 1, :ts, :ts, 1)"
                ),
                {"ts": timestamp},
            )
    finally:
        engine.dispose()

    command.upgrade(config, "head")
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT provider_account_id, name, display_name, context_window "
                    "FROM model_profiles ORDER BY id"
                )
            ).all()
            assert [tuple(row) for row in rows] == [
                (1, "deepseek-v4-flash", "deepseek-v4-flash", 1_000_000),
                (2, "deepseek-chat", "Gateway alias", 64_000),
                (3, "deepseek-v4-flash", "Existing V4 model", 1_000_000),
                (4, "deepseek-v4-flash", "deepseek-v4-flash", 1_000_000),
                (3, "deepseek-v4-pro", "Existing V4 Pro", 1_000_000),
            ]
            assert connection.scalar(
                text("SELECT model_profile_id FROM model_route_entries WHERE id = 1")
            ) == 5
    finally:
        engine.dispose()
