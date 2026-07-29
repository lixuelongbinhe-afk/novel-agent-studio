"""add indexes used by foreign-key checks and parent deletion

Revision ID: b7d9e1f3a524
Revises: d4f6a8c0e212
"""

from collections.abc import Sequence

from alembic import op


revision: str = "b7d9e1f3a524"
down_revision: str | None = "d4f6a8c0e212"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


INDEXES = (
    ("ix_entity_relations_source_entity_id", "entity_relations", "source_entity_id"),
    ("ix_entity_relations_target_entity_id", "entity_relations", "target_entity_id"),
    ("ix_entity_state_changes_chapter_id", "entity_state_changes", "chapter_id"),
    ("ix_timeline_events_chapter_id", "timeline_events", "chapter_id"),
    ("ix_foreshadows_chapter_id", "foreshadows", "chapter_id"),
    ("ix_model_capabilities_model_profile_id", "model_capabilities", "model_profile_id"),
    ("ix_model_pricing_model_profile_id", "model_pricing", "model_profile_id"),
    (
        "ix_generic_http_adapter_configurations_credential_reference_id",
        "generic_http_adapter_configurations",
        "credential_reference_id",
    ),
    ("ix_generation_jobs_result_artifact_id", "generation_jobs", "result_artifact_id"),
)


def upgrade() -> None:
    for name, table, column in INDEXES:
        op.create_index(name, table, [column], unique=False)


def downgrade() -> None:
    for name, table, _column in reversed(INDEXES):
        op.drop_index(name, table_name=table)
