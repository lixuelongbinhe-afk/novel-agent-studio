from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from app.core.config import get_settings


PHASE_1_REVISION = "13da8433608a"
PHASE_2_REVISION = "9f43d2a6c1b8"
PHASE_3_REVISION = "c31e6d7b924f"
PHASE_4_REVISION = "e47a1d8f2c60"
PHASE_5_REVISION = "f8b2c4d6e810"
PHASE_6_REVISION = "a6c8e0f2b419"
PHASE_7_REVISION = "d7e9f1a3c520"
STUDIO_V2_REVISION = "b94f8d2c710a"
PHASE_1_TABLES = {
    "projects",
    "provider_accounts",
    "model_profiles",
    "protocol_configurations",
    "story_entities",
    "style_guides",
    "volumes",
    "chapters",
    "entity_aliases",
    "entity_relations",
    "model_capabilities",
    "model_pricing",
    "chapter_versions",
    "entity_state_changes",
    "foreshadows",
    "scenes",
    "timeline_events",
}
PHASE_2_TABLES = {"provider_presets"}
PHASE_3_TABLES = {
    "credential_references",
    "generic_http_adapter_configurations",
}
PHASE_4_TABLES = {
    "budget_policies",
    "capability_probe_runs",
    "model_invocations",
    "model_route_entries",
    "model_routes",
    "provider_health",
    "rate_limit_policies",
}
PHASE_5_TABLES = {
    "agent_definitions",
    "node_run_attempts",
    "node_runs",
    "workflow_edges",
    "workflow_nodes",
    "workflow_run_events",
    "workflow_runs",
    "workflows",
}
PHASE_6_TABLES = {
    "chapter_summaries",
    "scene_states",
    "chapter_entity_links",
    "context_pins",
    "content_classifications",
    "context_policies",
    "provider_data_policies",
    "context_builds",
    "context_fts",
}
PHASE_7_TABLES = {
    "approval_requests",
    "proposed_change_sets",
    "writeback_audits",
}
STUDIO_V2_TABLES = {
    "studio_project_states",
    "creative_artifacts",
    "studio_messages",
    "generation_jobs",
    "project_snapshots",
}


def upgrade_database(database_url: str | None = None) -> None:
    backend_root = Path(__file__).resolve().parents[1]
    config = Config(str(backend_root / "alembic.ini"))
    config.set_main_option("script_location", str(backend_root / "alembic"))
    resolved_url = database_url or get_settings().database_url
    config.attributes["database_url"] = resolved_url
    config.set_main_option("sqlalchemy.url", resolved_url.replace("%", "%%"))
    migration_engine = create_engine(resolved_url)
    try:
        table_names = set(inspect(migration_engine).get_table_names())
    finally:
        migration_engine.dispose()

    if "alembic_version" not in table_names and table_names & (
        PHASE_1_TABLES
        | PHASE_2_TABLES
        | PHASE_3_TABLES
        | PHASE_4_TABLES
        | PHASE_5_TABLES
        | PHASE_6_TABLES
        | PHASE_7_TABLES
        | STUDIO_V2_TABLES
    ):
        baseline = _legacy_baseline(table_names)
        command.stamp(config, baseline)
    command.upgrade(config, "head")
    _ensure_provider_presets(resolved_url)
    _ensure_context_defaults(resolved_url)


def _legacy_baseline(table_names: set[str]) -> str:
    missing_phase_1 = PHASE_1_TABLES - table_names
    if missing_phase_1:
        missing = ", ".join(sorted(missing_phase_1))
        raise RuntimeError(
            "Legacy database is incomplete and was not modified; missing tables: "
            f"{missing}"
        )
    phase_2_present = PHASE_2_TABLES <= table_names
    phase_3_present = PHASE_3_TABLES <= table_names
    phase_4_present = PHASE_4_TABLES <= table_names
    phase_5_present = PHASE_5_TABLES <= table_names
    phase_6_present = PHASE_6_TABLES <= table_names
    phase_7_present = PHASE_7_TABLES <= table_names
    studio_v2_present = STUDIO_V2_TABLES <= table_names
    if table_names & STUDIO_V2_TABLES and not studio_v2_present:
        raise RuntimeError("Legacy database has a partial Studio V2 schema and was not modified")
    if studio_v2_present and not phase_7_present:
        raise RuntimeError("Studio V2 tables require the Phase 7 schema")
    if table_names & PHASE_3_TABLES and not phase_3_present:
        raise RuntimeError(
            "Legacy database has a partial Phase 3 schema and was not modified"
        )
    if phase_3_present and not phase_2_present:
        raise RuntimeError(
            "Legacy database has Phase 3 tables but is missing the Phase 2 schema"
        )
    if table_names & PHASE_4_TABLES and not phase_4_present:
        raise RuntimeError(
            "Legacy database has a partial Phase 4 schema and was not modified"
        )
    if phase_4_present and not phase_3_present:
        raise RuntimeError(
            "Legacy database has Phase 4 tables but is missing the Phase 3 schema"
        )
    if table_names & PHASE_5_TABLES and not phase_5_present:
        raise RuntimeError(
            "Legacy database has a partial Phase 5 schema and was not modified"
        )
    if phase_5_present and not phase_4_present:
        raise RuntimeError(
            "Legacy database has Phase 5 tables but is missing the Phase 4 schema"
        )
    if table_names & PHASE_6_TABLES and not phase_6_present:
        raise RuntimeError(
            "Legacy database has a partial Phase 6 schema and was not modified"
        )
    if phase_6_present and not phase_5_present:
        raise RuntimeError(
            "Legacy database has Phase 6 tables but is missing the Phase 5 schema"
        )
    if table_names & PHASE_7_TABLES and not phase_7_present:
        raise RuntimeError(
            "Legacy database has a partial Phase 7 schema and was not modified"
        )
    if phase_7_present and not phase_6_present:
        raise RuntimeError(
            "Legacy database has Phase 7 tables but is missing the Phase 6 schema"
        )
    if studio_v2_present:
        return STUDIO_V2_REVISION
    if phase_7_present:
        return PHASE_7_REVISION
    if phase_6_present:
        return PHASE_6_REVISION
    if phase_5_present:
        return PHASE_5_REVISION
    if phase_4_present:
        return PHASE_4_REVISION
    if phase_3_present:
        return PHASE_3_REVISION
    if phase_2_present:
        return PHASE_2_REVISION
    return PHASE_1_REVISION


def _ensure_provider_presets(database_url: str) -> None:
    from app.services.models import ensure_provider_presets

    seed_engine = create_engine(database_url)
    try:
        with Session(seed_engine) as db, db.begin():
            ensure_provider_presets(db)
    finally:
        seed_engine.dispose()


def _ensure_context_defaults(database_url: str) -> None:
    from app.services.context_memory import ensure_all_context_defaults

    seed_engine = create_engine(database_url)
    try:
        with Session(seed_engine) as db, db.begin():
            ensure_all_context_defaults(db)
    finally:
        seed_engine.dispose()
