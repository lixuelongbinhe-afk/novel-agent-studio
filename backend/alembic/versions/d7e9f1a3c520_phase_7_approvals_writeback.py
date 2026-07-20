"""phase 7 approvals and safe writeback

Revision ID: d7e9f1a3c520
Revises: a6c8e0f2b419
Create Date: 2026-07-18 18:00:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "d7e9f1a3c520"
down_revision: Union[str, Sequence[str], None] = "a6c8e0f2b419"
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
        "approval_requests",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("workflow_run_id", sa.Integer(), nullable=False),
        sa.Column("node_run_id", sa.Integer(), nullable=False),
        sa.Column("node_key", sa.String(length=64), nullable=False),
        sa.Column("approval_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("title", sa.String(length=240), nullable=False),
        sa.Column("instructions", sa.Text(), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("snapshot_hash", sa.String(length=64), nullable=False),
        sa.Column("snapshot_revision", sa.Integer(), nullable=False),
        sa.Column("round_number", sa.Integer(), nullable=False),
        sa.Column("parent_approval_id", sa.Integer(), nullable=True),
        sa.Column("superseded_by_id", sa.Integer(), nullable=True),
        sa.Column("decision_action", sa.String(length=32), nullable=True),
        sa.Column("decision_note", sa.Text(), nullable=False),
        sa.Column("decision_payload_json", sa.Text(), nullable=False),
        sa.Column("decision_idempotency_key", sa.String(length=128), nullable=True),
        sa.Column("decision_hash", sa.String(length=64), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["node_run_id"], ["node_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["parent_approval_id"], ["approval_requests.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"], ["approval_requests.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workflow_run_id",
            "node_key",
            "snapshot_revision",
            name="uq_approval_run_node_snapshot_revision",
        ),
    )
    for column in (
        "project_id",
        "workflow_run_id",
        "node_run_id",
        "node_key",
        "approval_type",
        "status",
        "snapshot_hash",
        "parent_approval_id",
        "superseded_by_id",
        "expires_at",
    ):
        op.create_index(op.f(f"ix_approval_requests_{column}"), "approval_requests", [column])

    op.create_table(
        "proposed_change_sets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("workflow_run_id", sa.Integer(), nullable=False),
        sa.Column("node_run_id", sa.Integer(), nullable=False),
        sa.Column("node_key", sa.String(length=64), nullable=False),
        sa.Column("source_approval_id", sa.Integer(), nullable=True),
        sa.Column("chapter_id", sa.Integer(), nullable=True),
        sa.Column("scene_id", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("extraction_json", sa.Text(), nullable=False),
        sa.Column("base_revisions_json", sa.Text(), nullable=False),
        sa.Column("items_json", sa.Text(), nullable=False),
        sa.Column("conflicts_json", sa.Text(), nullable=False),
        sa.Column("changes_hash", sa.String(length=64), nullable=False),
        sa.Column("superseded_by_id", sa.Integer(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["node_run_id"], ["node_runs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["source_approval_id"], ["approval_requests.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["chapter_id"], ["chapters.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["scene_id"], ["scenes.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["superseded_by_id"], ["proposed_change_sets.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "project_id",
        "workflow_run_id",
        "node_run_id",
        "node_key",
        "source_approval_id",
        "chapter_id",
        "scene_id",
        "status",
        "changes_hash",
        "superseded_by_id",
    ):
        op.create_index(
            op.f(f"ix_proposed_change_sets_{column}"),
            "proposed_change_sets",
            [column],
        )

    op.create_table(
        "writeback_audits",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("workflow_run_id", sa.Integer(), nullable=False),
        sa.Column("change_set_id", sa.Integer(), nullable=False),
        sa.Column("approval_request_id", sa.Integer(), nullable=False),
        sa.Column("change_set_hash", sa.String(length=64), nullable=False),
        sa.Column("entries_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["change_set_id"], ["proposed_change_sets.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["approval_request_id"], ["approval_requests.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "project_id",
        "workflow_run_id",
        "change_set_id",
        "approval_request_id",
        "change_set_hash",
        "created_at",
    ):
        op.create_index(op.f(f"ix_writeback_audits_{column}"), "writeback_audits", [column])


def downgrade() -> None:
    op.drop_table("writeback_audits")
    op.drop_table("proposed_change_sets")
    op.drop_table("approval_requests")
