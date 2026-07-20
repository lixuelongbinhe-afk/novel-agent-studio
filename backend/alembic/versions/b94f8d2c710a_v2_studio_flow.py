"""Add the V2 creation-flow tables.

Revision ID: b94f8d2c710a
Revises: d7e9f1a3c520
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "b94f8d2c710a"
down_revision: str | None = "d7e9f1a3c520"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "studio_project_states",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("entry_mode", sa.String(24), nullable=False, server_default="creative"),
        sa.Column("stage", sa.String(40), nullable=False, server_default="idea"),
        sa.Column("review_granularity", sa.String(24), nullable=False, server_default="chapter"),
        sa.Column("routing_strategy", sa.String(24), nullable=False, server_default="balanced"),
        sa.Column("generation_mode", sa.String(24), nullable=False, server_default="countdown"),
        sa.Column("countdown_seconds", sa.Integer(), nullable=False, server_default="10"),
        sa.Column("memory_mode", sa.String(24), nullable=False, server_default="automatic"),
        sa.Column("budget_limit", sa.Float(), nullable=True),
        sa.Column("budget_spent", sa.Float(), nullable=False, server_default="0"),
        sa.Column("budget_currency", sa.String(12), nullable=False, server_default="USD"),
        sa.Column("budget_warning_percent", sa.Integer(), nullable=False, server_default="70"),
        sa.Column("budget_pause_percent", sa.Integer(), nullable=False, server_default="110"),
        sa.Column("budget_paused", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("config_json", sa.Text(), nullable=False, server_default="{}"),
        *_timestamp_columns(),
        sa.UniqueConstraint("project_id", name="uq_studio_project_state"),
    )
    op.create_index("ix_studio_project_states_project_id", "studio_project_states", ["project_id"])
    op.create_index("ix_studio_project_states_stage", "studio_project_states", ["stage"])

    op.create_table(
        "creative_artifacts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("title", sa.String(240), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, server_default=""),
        sa.Column("status", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("source", sa.String(24), nullable=False, server_default="ai"),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("version_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("notes", sa.Text(), nullable=False, server_default=""),
        sa.Column("metadata_json", sa.Text(), nullable=False, server_default="{}"),
        *_timestamp_columns(),
    )
    op.create_index("ix_creative_artifacts_project_id", "creative_artifacts", ["project_id"])
    op.create_index("ix_creative_artifacts_kind", "creative_artifacts", ["kind"])
    op.create_index("ix_creative_artifacts_status", "creative_artifacts", ["status"])

    op.create_table(
        "studio_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("context_scope", sa.String(80), nullable=False, server_default="project"),
        sa.Column("proposal_json", sa.Text(), nullable=False, server_default="null"),
        sa.Column("proposal_status", sa.String(24), nullable=False, server_default="none"),
        sa.Column("model_name", sa.String(200), nullable=True),
        sa.Column("model_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_studio_messages_project_id", "studio_messages", ["project_id"])
    op.create_index("ix_studio_messages_proposal_status", "studio_messages", ["proposal_status"])

    op.create_table(
        "generation_jobs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(48), nullable=False),
        sa.Column("label", sa.String(240), nullable=False),
        sa.Column("status", sa.String(24), nullable=False, server_default="queued"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("model_name", sa.String(200), nullable=True),
        sa.Column("model_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("error_message", sa.Text(), nullable=False, server_default=""),
        sa.Column("result_artifact_id", sa.Integer(), sa.ForeignKey("creative_artifacts.id", ondelete="SET NULL"), nullable=True),
        *_timestamp_columns(),
    )
    op.create_index("ix_generation_jobs_project_id", "generation_jobs", ["project_id"])
    op.create_index("ix_generation_jobs_kind", "generation_jobs", ["kind"])
    op.create_index("ix_generation_jobs_status", "generation_jobs", ["status"])

    op.create_table(
        "project_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("project_id", sa.Integer(), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(24), nullable=False, server_default="automatic"),
        sa.Column("label", sa.String(240), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("permanent", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_project_snapshots_project_id", "project_snapshots", ["project_id"])
    op.create_index("ix_project_snapshots_kind", "project_snapshots", ["kind"])
    op.create_index("ix_project_snapshots_created_at", "project_snapshots", ["created_at"])


def downgrade() -> None:
    op.drop_table("project_snapshots")
    op.drop_table("generation_jobs")
    op.drop_table("studio_messages")
    op.drop_table("creative_artifacts")
    op.drop_table("studio_project_states")


def _timestamp_columns() -> tuple[sa.Column[object], ...]:
    return (
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
    )
