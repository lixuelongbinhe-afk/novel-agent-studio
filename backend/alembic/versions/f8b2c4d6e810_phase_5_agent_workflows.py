"""phase 5 agent definitions and workflow runtime

Revision ID: f8b2c4d6e810
Revises: e47a1d8f2c60
Create Date: 2026-07-18 14:20:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "f8b2c4d6e810"
down_revision: Union[str, Sequence[str], None] = "e47a1d8f2c60"
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
        "agent_definitions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("agent_type", sa.String(length=80), nullable=False),
        sa.Column("system_prompt", sa.Text(), nullable=False),
        sa.Column("prompt_template", sa.Text(), nullable=False),
        sa.Column("input_schema_json", sa.Text(), nullable=False),
        sa.Column("output_schema_json", sa.Text(), nullable=False),
        sa.Column("output_mode", sa.String(length=20), nullable=False),
        sa.Column("model_profile_id", sa.Integer(), nullable=True),
        sa.Column("route_id", sa.Integer(), nullable=True),
        sa.Column("parameters_json", sa.Text(), nullable=False),
        sa.Column("required_capabilities_json", sa.Text(), nullable=False),
        sa.Column("allow_degradation", sa.Boolean(), nullable=False),
        sa.Column("timeout_seconds", sa.Float(), nullable=False),
        sa.Column("retry_count", sa.Integer(), nullable=False),
        sa.Column("budget_json", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("config_hash", sa.String(length=64), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["model_profile_id"], ["model_profiles.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(["route_id"], ["model_routes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "name", name="uq_agent_project_name"),
    )
    for column in ("project_id", "model_profile_id", "route_id", "config_hash"):
        op.create_index(
            op.f(f"ix_agent_definitions_{column}"),
            "agent_definitions",
            [column],
            unique=False,
        )

    op.create_table(
        "workflows",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=180), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "name", name="uq_workflow_project_name"),
    )
    op.create_index(op.f("ix_workflows_project_id"), "workflows", ["project_id"])

    op.create_table(
        "workflow_nodes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workflow_id", sa.Integer(), nullable=False),
        sa.Column("node_key", sa.String(length=64), nullable=False),
        sa.Column("node_type", sa.String(length=40), nullable=False),
        sa.Column("label", sa.String(length=160), nullable=False),
        sa.Column("position_x", sa.Float(), nullable=False),
        sa.Column("position_y", sa.Float(), nullable=False),
        sa.Column("config_json", sa.Text(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_id", "node_key", name="uq_workflow_node_key"),
    )
    op.create_index(
        op.f("ix_workflow_nodes_workflow_id"), "workflow_nodes", ["workflow_id"]
    )

    op.create_table(
        "workflow_edges",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workflow_id", sa.Integer(), nullable=False),
        sa.Column("edge_key", sa.String(length=100), nullable=False),
        sa.Column("source_node_key", sa.String(length=64), nullable=False),
        sa.Column("target_node_key", sa.String(length=64), nullable=False),
        sa.Column("source_handle", sa.String(length=40), nullable=True),
        sa.Column("target_handle", sa.String(length=40), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_id", "edge_key", name="uq_workflow_edge_key"),
    )
    op.create_index(
        op.f("ix_workflow_edges_workflow_id"), "workflow_edges", ["workflow_id"]
    )

    op.create_table(
        "workflow_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workflow_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("parent_run_id", sa.Integer(), nullable=True),
        sa.Column("workflow_revision", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("source_mode", sa.String(length=32), nullable=False),
        sa.Column("resume_node_key", sa.String(length=64), nullable=True),
        sa.Column("input_json", sa.Text(), nullable=False),
        sa.Column("output_json", sa.Text(), nullable=False),
        sa.Column("plan_json", sa.Text(), nullable=False),
        sa.Column("snapshot_json", sa.Text(), nullable=False),
        sa.Column("error_json", sa.Text(), nullable=False),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("event_sequence", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(["workflow_id"], ["workflows.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["parent_run_id"], ["workflow_runs.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in ("workflow_id", "project_id", "parent_run_id", "status"):
        op.create_index(
            op.f(f"ix_workflow_runs_{column}"),
            "workflow_runs",
            [column],
            unique=False,
        )

    op.create_table(
        "node_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workflow_run_id", sa.Integer(), nullable=False),
        sa.Column("node_key", sa.String(length=64), nullable=False),
        sa.Column("node_type", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("activated", sa.Boolean(), nullable=False),
        sa.Column("input_json", sa.Text(), nullable=False),
        sa.Column("output_json", sa.Text(), nullable=False),
        sa.Column("error_json", sa.Text(), nullable=False),
        sa.Column("warnings_json", sa.Text(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_run_id", "node_key", name="uq_run_node_key"),
    )
    op.create_index(
        op.f("ix_node_runs_workflow_run_id"), "node_runs", ["workflow_run_id"]
    )
    op.create_index(op.f("ix_node_runs_status"), "node_runs", ["status"])

    op.create_table(
        "node_run_attempts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("node_run_id", sa.Integer(), nullable=False),
        sa.Column("attempt_number", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input_json", sa.Text(), nullable=False),
        sa.Column("output_json", sa.Text(), nullable=False),
        sa.Column("partial_output", sa.Text(), nullable=False),
        sa.Column("error_json", sa.Text(), nullable=False),
        sa.Column("model_invocation_ids_json", sa.Text(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("cost", sa.Float(), nullable=True),
        sa.Column("cost_known", sa.Boolean(), nullable=False),
        sa.Column("currency", sa.String(length=12), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["node_run_id"], ["node_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("node_run_id", "attempt_number", name="uq_node_attempt_number"),
    )
    op.create_index(
        op.f("ix_node_run_attempts_node_run_id"),
        "node_run_attempts",
        ["node_run_id"],
    )
    op.create_index(
        op.f("ix_node_run_attempts_status"), "node_run_attempts", ["status"]
    )

    op.create_table(
        "workflow_run_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("workflow_run_id", sa.Integer(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(length=60), nullable=False),
        sa.Column("node_key", sa.String(length=64), nullable=True),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"], ["workflow_runs.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("workflow_run_id", "sequence", name="uq_run_event_sequence"),
    )
    for column in ("workflow_run_id", "event_type", "node_key"):
        op.create_index(
            op.f(f"ix_workflow_run_events_{column}"),
            "workflow_run_events",
            [column],
            unique=False,
        )


def downgrade() -> None:
    for column in ("node_key", "event_type", "workflow_run_id"):
        op.drop_index(
            op.f(f"ix_workflow_run_events_{column}"),
            table_name="workflow_run_events",
        )
    op.drop_table("workflow_run_events")
    op.drop_index(op.f("ix_node_run_attempts_status"), table_name="node_run_attempts")
    op.drop_index(
        op.f("ix_node_run_attempts_node_run_id"), table_name="node_run_attempts"
    )
    op.drop_table("node_run_attempts")
    op.drop_index(op.f("ix_node_runs_status"), table_name="node_runs")
    op.drop_index(op.f("ix_node_runs_workflow_run_id"), table_name="node_runs")
    op.drop_table("node_runs")
    for column in ("status", "parent_run_id", "project_id", "workflow_id"):
        op.drop_index(op.f(f"ix_workflow_runs_{column}"), table_name="workflow_runs")
    op.drop_table("workflow_runs")
    op.drop_index(op.f("ix_workflow_edges_workflow_id"), table_name="workflow_edges")
    op.drop_table("workflow_edges")
    op.drop_index(op.f("ix_workflow_nodes_workflow_id"), table_name="workflow_nodes")
    op.drop_table("workflow_nodes")
    op.drop_index(op.f("ix_workflows_project_id"), table_name="workflows")
    op.drop_table("workflows")
    for column in ("config_hash", "route_id", "model_profile_id", "project_id"):
        op.drop_index(
            op.f(f"ix_agent_definitions_{column}"), table_name="agent_definitions"
        )
    op.drop_table("agent_definitions")
