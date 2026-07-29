"""align active generation lease uniqueness with business state

Revision ID: d4f6a8c0e212
Revises: a2b4c6d8e010
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "d4f6a8c0e212"
down_revision: str | None = "a2b4c6d8e010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


ACTIVE_PREDICATE = "deleted_at IS NULL AND status IN ('queued','running')"


def upgrade() -> None:
    with op.batch_alter_table("generation_jobs") as batch_op:
        batch_op.drop_constraint(
            "uq_generation_job_active_scope", type_="unique"
        )
    op.create_index(
        "uq_generation_job_active_scope",
        "generation_jobs",
        ["active_scope_key"],
        unique=True,
        sqlite_where=sa.text(ACTIVE_PREDICATE),
        postgresql_where=sa.text(ACTIVE_PREDICATE),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_generation_job_active_scope", table_name="generation_jobs"
    )
    with op.batch_alter_table("generation_jobs") as batch_op:
        batch_op.create_unique_constraint(
            "uq_generation_job_active_scope", ["active_scope_key"]
        )
