"""phase 6 context memory and retrieval

Revision ID: a6c8e0f2b419
Revises: f8b2c4d6e810
Create Date: 2026-07-18 17:10:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "a6c8e0f2b419"
down_revision: Union[str, Sequence[str], None] = "f8b2c4d6e810"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _timestamps() -> list[sa.Column[object]]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "chapter_summaries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chapter_id", sa.Integer(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("key_events_json", sa.Text(), nullable=False),
        sa.Column("entity_ids_json", sa.Text(), nullable=False),
        sa.Column("token_count", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(length=40), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("chapter_id", name="uq_chapter_summary_chapter"),
    )
    op.create_index(
        op.f("ix_chapter_summaries_chapter_id"),
        "chapter_summaries",
        ["chapter_id"],
    )

    op.create_table(
        "scene_states",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scene_id", sa.Integer(), nullable=False),
        sa.Column("viewpoint_entity_id", sa.Integer(), nullable=True),
        sa.Column("location_entity_id", sa.Integer(), nullable=True),
        sa.Column("item_entity_ids_json", sa.Text(), nullable=False),
        sa.Column("state_json", sa.Text(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["scene_id"], ["scenes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["viewpoint_entity_id"], ["story_entities.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["location_entity_id"], ["story_entities.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scene_id", name="uq_scene_state_scene"),
    )
    for column in ("scene_id", "viewpoint_entity_id", "location_entity_id"):
        op.create_index(op.f(f"ix_scene_states_{column}"), "scene_states", [column])

    op.create_table(
        "chapter_entity_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("chapter_id", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("link_type", sa.String(length=60), nullable=False),
        sa.Column("relevance", sa.Float(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["entity_id"], ["story_entities.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "chapter_id", "entity_id", "link_type", name="uq_chapter_entity_link"
        ),
    )
    op.create_index(
        op.f("ix_chapter_entity_links_chapter_id"),
        "chapter_entity_links",
        ["chapter_id"],
    )
    op.create_index(
        op.f("ix_chapter_entity_links_entity_id"),
        "chapter_entity_links",
        ["entity_id"],
    )

    op.create_table(
        "context_pins",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(length=60), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("content_override", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id", "source_type", "source_id", name="uq_context_pin_source"
        ),
    )
    op.create_index(op.f("ix_context_pins_project_id"), "context_pins", ["project_id"])

    op.create_table(
        "content_classifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(length=60), nullable=False),
        sa.Column("source_id", sa.Integer(), nullable=False),
        sa.Column("classification", sa.String(length=60), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "project_id",
            "source_type",
            "source_id",
            name="uq_content_classification_source",
        ),
    )
    op.create_index(
        op.f("ix_content_classifications_project_id"),
        "content_classifications",
        ["project_id"],
    )

    op.create_table(
        "context_policies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("token_budget", sa.Integer(), nullable=False),
        sa.Column("recent_chapter_count", sa.Integer(), nullable=False),
        sa.Column("max_results", sa.Integer(), nullable=False),
        sa.Column("min_relevance", sa.Float(), nullable=False),
        sa.Column("section_priorities_json", sa.Text(), nullable=False),
        sa.Column("required_sections_json", sa.Text(), nullable=False),
        sa.Column("allowed_classifications_json", sa.Text(), nullable=False),
        sa.Column("use_summaries", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "name", name="uq_context_policy_project_name"),
    )
    op.create_index(
        op.f("ix_context_policies_project_id"), "context_policies", ["project_id"]
    )

    op.create_table(
        "provider_data_policies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider_account_id", sa.Integer(), nullable=False),
        sa.Column("allowed_classifications_json", sa.Text(), nullable=False),
        sa.Column("block_on_required_exclusion", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["provider_account_id"], ["provider_accounts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider_account_id", name="uq_provider_data_policy_account"
        ),
    )
    op.create_index(
        op.f("ix_provider_data_policies_provider_account_id"),
        "provider_data_policies",
        ["provider_account_id"],
    )

    op.create_table(
        "context_builds",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("chapter_id", sa.Integer(), nullable=True),
        sa.Column("scene_id", sa.Integer(), nullable=True),
        sa.Column("agent_id", sa.Integer(), nullable=True),
        sa.Column("workflow_run_id", sa.Integer(), nullable=True),
        sa.Column("model_profile_id", sa.Integer(), nullable=True),
        sa.Column("policy_id", sa.Integer(), nullable=True),
        sa.Column("provider_ids_json", sa.Text(), nullable=False),
        sa.Column("request_json", sa.Text(), nullable=False),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("context_text", sa.Text(), nullable=False),
        sa.Column("build_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["scene_id"], ["scenes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["agent_id"], ["agent_definitions.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_runs.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["model_profile_id"], ["model_profiles.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["policy_id"], ["context_policies.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "project_id",
        "chapter_id",
        "scene_id",
        "agent_id",
        "workflow_run_id",
        "model_profile_id",
        "policy_id",
        "build_hash",
    ):
        op.create_index(op.f(f"ix_context_builds_{column}"), "context_builds", [column])

    op.execute(
        "CREATE VIRTUAL TABLE context_fts USING fts5("
        "project_id UNINDEXED, source_type UNINDEXED, source_id UNINDEXED, "
        "title, content, tags, tokenize='unicode61')"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS context_fts")
    for column in (
        "build_hash",
        "policy_id",
        "model_profile_id",
        "workflow_run_id",
        "agent_id",
        "scene_id",
        "chapter_id",
        "project_id",
    ):
        op.drop_index(op.f(f"ix_context_builds_{column}"), table_name="context_builds")
    op.drop_table("context_builds")
    op.drop_index(
        op.f("ix_provider_data_policies_provider_account_id"),
        table_name="provider_data_policies",
    )
    op.drop_table("provider_data_policies")
    op.drop_index(op.f("ix_context_policies_project_id"), table_name="context_policies")
    op.drop_table("context_policies")
    op.drop_index(
        op.f("ix_content_classifications_project_id"),
        table_name="content_classifications",
    )
    op.drop_table("content_classifications")
    op.drop_index(op.f("ix_context_pins_project_id"), table_name="context_pins")
    op.drop_table("context_pins")
    op.drop_index(
        op.f("ix_chapter_entity_links_entity_id"), table_name="chapter_entity_links"
    )
    op.drop_index(
        op.f("ix_chapter_entity_links_chapter_id"), table_name="chapter_entity_links"
    )
    op.drop_table("chapter_entity_links")
    for column in ("location_entity_id", "viewpoint_entity_id", "scene_id"):
        op.drop_index(op.f(f"ix_scene_states_{column}"), table_name="scene_states")
    op.drop_table("scene_states")
    op.drop_index(
        op.f("ix_chapter_summaries_chapter_id"), table_name="chapter_summaries"
    )
    op.drop_table("chapter_summaries")
