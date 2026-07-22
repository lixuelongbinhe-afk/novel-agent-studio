"""generation job leases and idempotency

Revision ID: c5e7a9b1d320
Revises: b94f8d2c710a
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "c5e7a9b1d320"
down_revision: str | None = "b94f8d2c710a"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("generation_jobs") as batch_op:
        batch_op.add_column(sa.Column("idempotency_key", sa.String(length=128), nullable=True))
        batch_op.add_column(sa.Column("active_scope_key", sa.String(length=320), nullable=True))
        batch_op.create_unique_constraint(
            "uq_generation_job_idempotency", ["project_id", "idempotency_key"]
        )
        batch_op.create_unique_constraint(
            "uq_generation_job_active_scope", ["active_scope_key"]
        )


def downgrade() -> None:
    with op.batch_alter_table("generation_jobs") as batch_op:
        batch_op.drop_constraint("uq_generation_job_active_scope", type_="unique")
        batch_op.drop_constraint("uq_generation_job_idempotency", type_="unique")
        batch_op.drop_column("active_scope_key")
        batch_op.drop_column("idempotency_key")
