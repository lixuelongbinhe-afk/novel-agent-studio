"""add indexes for long-running retention maintenance

Revision ID: a2b4c6d8e010
Revises: f1a2b3c4d5e6
"""

from collections.abc import Sequence

from alembic import op


revision: str = "a2b4c6d8e010"
down_revision: str | None = "f1a2b3c4d5e6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_workflow_run_events_type_created_run",
        "workflow_run_events",
        ["event_type", "created_at", "workflow_run_id"],
    )
    op.create_index(
        "ix_workflow_run_events_created_run",
        "workflow_run_events",
        ["created_at", "workflow_run_id"],
    )
    op.create_index(
        "ix_context_builds_project_created_id",
        "context_builds",
        ["project_id", "created_at", "id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_context_builds_project_created_id", table_name="context_builds"
    )
    op.drop_index(
        "ix_workflow_run_events_created_run", table_name="workflow_run_events"
    )
    op.drop_index(
        "ix_workflow_run_events_type_created_run",
        table_name="workflow_run_events",
    )
