"""phase 4 model capability and execution control

Revision ID: e47a1d8f2c60
Revises: c31e6d7b924f
Create Date: 2026-07-18 09:30:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "e47a1d8f2c60"
down_revision: Union[str, Sequence[str], None] = "c31e6d7b924f"
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
    op.add_column(
        "model_profiles",
        sa.Column("tokenizer_name", sa.String(length=120), nullable=True),
    )
    op.add_column(
        "model_profiles",
        sa.Column("tokenizer_source", sa.String(length=40), nullable=True),
    )
    op.add_column("model_pricing", sa.Column("request_fee", sa.Float(), nullable=True))
    op.add_column("model_pricing", sa.Column("tool_call_fee", sa.Float(), nullable=True))
    op.add_column(
        "model_pricing",
        sa.Column("currency", sa.String(length=12), server_default="USD", nullable=False),
    )

    op.create_table(
        "capability_probe_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("model_profile_id", sa.Integer(), nullable=False),
        sa.Column("level", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("max_output_tokens", sa.Integer(), nullable=False),
        sa.Column("estimated_cost", sa.Float(), nullable=True),
        sa.Column("result_json", sa.Text(), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["model_profile_id"], ["model_profiles.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_capability_probe_runs_model_profile_id"),
        "capability_probe_runs",
        ["model_profile_id"],
        unique=False,
    )

    op.create_table(
        "model_routes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("strategy", sa.String(length=40), nullable=False),
        sa.Column("required_capabilities_json", sa.Text(), nullable=False),
        sa.Column("allow_degradation", sa.Boolean(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_model_routes_project_id"),
        "model_routes",
        ["project_id"],
        unique=False,
    )

    op.create_table(
        "model_route_entries",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("route_id", sa.Integer(), nullable=False),
        sa.Column("model_profile_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.ForeignKeyConstraint(["route_id"], ["model_routes.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["model_profile_id"], ["model_profiles.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("route_id", "model_profile_id", name="uq_route_model"),
    )
    op.create_index(
        op.f("ix_model_route_entries_route_id"),
        "model_route_entries",
        ["route_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_model_route_entries_model_profile_id"),
        "model_route_entries",
        ["model_profile_id"],
        unique=False,
    )

    op.create_table(
        "rate_limit_policies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scope_type", sa.String(length=32), nullable=False),
        sa.Column("scope_key", sa.String(length=120), nullable=False),
        sa.Column("max_concurrency", sa.Integer(), nullable=True),
        sa.Column("requests_per_minute", sa.Integer(), nullable=True),
        sa.Column("tokens_per_minute", sa.Integer(), nullable=True),
        sa.Column("queue_timeout_seconds", sa.Float(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope_type", "scope_key", name="uq_rate_limit_scope"),
    )

    op.create_table(
        "budget_policies",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scope_type", sa.String(length=32), nullable=False),
        sa.Column("scope_key", sa.String(length=120), nullable=False),
        sa.Column("max_cost", sa.Float(), nullable=True),
        sa.Column("max_tokens", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=12), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        *_timestamps(),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("scope_type", "scope_key", name="uq_budget_scope"),
    )

    op.create_table(
        "provider_health",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider_account_id", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("consecutive_failures", sa.Integer(), nullable=False),
        sa.Column("failure_threshold", sa.Integer(), nullable=False),
        sa.Column("recovery_timeout_seconds", sa.Float(), nullable=False),
        sa.Column("half_open_in_flight", sa.Boolean(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_latency_ms", sa.Integer(), nullable=True),
        sa.Column("last_error_code", sa.String(length=80), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["provider_account_id"], ["provider_accounts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_account_id", name="uq_provider_health_account"),
    )
    op.create_index(
        op.f("ix_provider_health_provider_account_id"),
        "provider_health",
        ["provider_account_id"],
        unique=False,
    )

    op.create_table(
        "model_invocations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=160), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("provider_account_id", sa.Integer(), nullable=False),
        sa.Column("model_profile_id", sa.Integer(), nullable=False),
        sa.Column("route_id", sa.Integer(), nullable=True),
        sa.Column("route_run_id", sa.String(length=120), nullable=True),
        sa.Column("workflow_id", sa.String(length=120), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cached_input_tokens", sa.Integer(), nullable=False),
        sa.Column("reasoning_tokens", sa.Integer(), nullable=False),
        sa.Column("total_tokens", sa.Integer(), nullable=False),
        sa.Column("usage_estimated", sa.Boolean(), nullable=False),
        sa.Column("token_source", sa.String(length=40), nullable=False),
        sa.Column("cost", sa.Float(), nullable=True),
        sa.Column("cost_known", sa.Boolean(), nullable=False),
        sa.Column("currency", sa.String(length=12), nullable=False),
        sa.Column("queue_ms", sa.Integer(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("fallback_count", sa.Integer(), nullable=False),
        sa.Column("error_code", sa.String(length=80), nullable=True),
        sa.Column("warnings_json", sa.Text(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["provider_account_id"], ["provider_accounts.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["model_profile_id"], ["model_profiles.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["route_id"], ["model_routes.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    for column in (
        "request_id",
        "project_id",
        "provider_account_id",
        "model_profile_id",
        "route_id",
        "route_run_id",
        "workflow_id",
        "status",
    ):
        op.create_index(
            op.f(f"ix_model_invocations_{column}"),
            "model_invocations",
            [column],
            unique=column == "request_id",
        )


def downgrade() -> None:
    for column in (
        "status",
        "workflow_id",
        "route_run_id",
        "route_id",
        "model_profile_id",
        "provider_account_id",
        "project_id",
        "request_id",
    ):
        op.drop_index(op.f(f"ix_model_invocations_{column}"), table_name="model_invocations")
    op.drop_table("model_invocations")
    op.drop_index(
        op.f("ix_provider_health_provider_account_id"), table_name="provider_health"
    )
    op.drop_table("provider_health")
    op.drop_table("budget_policies")
    op.drop_table("rate_limit_policies")
    op.drop_index(
        op.f("ix_model_route_entries_model_profile_id"),
        table_name="model_route_entries",
    )
    op.drop_index(
        op.f("ix_model_route_entries_route_id"), table_name="model_route_entries"
    )
    op.drop_table("model_route_entries")
    op.drop_index(op.f("ix_model_routes_project_id"), table_name="model_routes")
    op.drop_table("model_routes")
    op.drop_index(
        op.f("ix_capability_probe_runs_model_profile_id"),
        table_name="capability_probe_runs",
    )
    op.drop_table("capability_probe_runs")
    op.drop_column("model_pricing", "currency")
    op.drop_column("model_pricing", "tool_call_fee")
    op.drop_column("model_pricing", "request_fee")
    op.drop_column("model_profiles", "tokenizer_source")
    op.drop_column("model_profiles", "tokenizer_name")
