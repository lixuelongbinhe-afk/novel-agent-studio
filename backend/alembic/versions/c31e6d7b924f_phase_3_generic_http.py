"""phase 3 generic http adapter

Revision ID: c31e6d7b924f
Revises: 9f43d2a6c1b8
Create Date: 2026-07-18 04:05:00
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c31e6d7b924f"
down_revision: Union[str, Sequence[str], None] = "9f43d2a6c1b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "credential_references",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("env_var_name", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_table(
        "generic_http_adapter_configurations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider_account_id", sa.Integer(), nullable=False),
        sa.Column("credential_reference_id", sa.Integer(), nullable=True),
        sa.Column("method", sa.String(length=8), nullable=False),
        sa.Column("endpoint", sa.String(length=500), nullable=False),
        sa.Column("content_type", sa.String(length=120), nullable=False),
        sa.Column("response_mode", sa.String(length=40), nullable=False),
        sa.Column("stream_format", sa.String(length=40), nullable=False),
        sa.Column("security_mode", sa.String(length=40), nullable=False),
        sa.Column("query_json", sa.Text(), nullable=False),
        sa.Column("headers_json", sa.Text(), nullable=False),
        sa.Column("request_template_json", sa.Text(), nullable=False),
        sa.Column("parameter_mapping_json", sa.Text(), nullable=False),
        sa.Column("response_mapping_json", sa.Text(), nullable=False),
        sa.Column("stream_mapping_json", sa.Text(), nullable=False),
        sa.Column("error_mapping_json", sa.Text(), nullable=False),
        sa.Column("auth_json", sa.Text(), nullable=False),
        sa.Column("capability_defaults_json", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("approved_origin", sa.String(length=500), nullable=True),
        sa.Column("approval_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("tested_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["credential_reference_id"], ["credential_references.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["provider_account_id"], ["provider_accounts.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_account_id", name="uq_generic_http_provider"),
    )
    op.create_index(
        op.f("ix_generic_http_adapter_configurations_provider_account_id"),
        "generic_http_adapter_configurations",
        ["provider_account_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_generic_http_adapter_configurations_provider_account_id"),
        table_name="generic_http_adapter_configurations",
    )
    op.drop_table("generic_http_adapter_configurations")
    op.drop_table("credential_references")
