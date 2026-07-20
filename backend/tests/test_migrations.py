from __future__ import annotations

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from app.migrations import (
    PHASE_1_REVISION,
    STUDIO_V2_REVISION,
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
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == STUDIO_V2_REVISION
            assert connection.scalar(text("SELECT COUNT(*) FROM provider_presets")) == 9
    finally:
        engine.dispose()


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
    engine = create_engine(url)
    try:
        tables = set(inspect(engine).get_table_names())
        assert "generic_http_adapter_configurations" in tables
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT title FROM projects WHERE id = 1")) == "保留的旧项目"
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == STUDIO_V2_REVISION
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
