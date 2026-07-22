"""story order constraints and canonical chapter numbers

Revision ID: e8f1c3d5a740
Revises: c5e7a9b1d320
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "e8f1c3d5a740"
down_revision: str | None = "c5e7a9b1d320"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _normalize_positions(table: str, parent_column: str) -> None:
    connection = op.get_bind()
    parents = connection.execute(
        sa.text(
            f"SELECT DISTINCT {parent_column} FROM {table} "
            "WHERE deleted_at IS NULL ORDER BY 1"
        )
    ).scalars()
    for parent_id in parents:
        ids = connection.execute(
            sa.text(
                f"SELECT id FROM {table} WHERE {parent_column} = :parent_id "
                "AND deleted_at IS NULL ORDER BY position, id"
            ),
            {"parent_id": parent_id},
        ).scalars()
        for position, row_id in enumerate(ids, 1):
            connection.execute(
                sa.text(f"UPDATE {table} SET position = :position WHERE id = :id"),
                {"position": position, "id": row_id},
            )


def upgrade() -> None:
    _normalize_positions("volumes", "project_id")
    _normalize_positions("chapters", "volume_id")
    _normalize_positions("scenes", "chapter_id")

    connection = op.get_bind()
    is_sqlite = connection.dialect.name == "sqlite"
    # Rebuilding chapters on SQLite triggers ON DELETE CASCADE for scenes,
    # versions and summaries. Native ADD COLUMN preserves every child row.
    op.add_column("chapters", sa.Column("project_id", sa.Integer(), nullable=True))
    op.add_column("chapters", sa.Column("number", sa.Integer(), nullable=True))
    connection.execute(
        sa.text(
            "UPDATE chapters SET project_id = ("
            "SELECT volumes.project_id FROM volumes WHERE volumes.id = chapters.volume_id)"
        )
    )
    project_ids = connection.execute(
        sa.text("SELECT DISTINCT project_id FROM chapters WHERE deleted_at IS NULL")
    ).scalars()
    for project_id in project_ids:
        chapter_ids = connection.execute(
            sa.text(
                "SELECT chapters.id FROM chapters JOIN volumes ON volumes.id = chapters.volume_id "
                "WHERE chapters.project_id = :project_id AND chapters.deleted_at IS NULL "
                "ORDER BY volumes.position, chapters.position, chapters.id"
            ),
            {"project_id": project_id},
        ).scalars()
        for number, chapter_id in enumerate(chapter_ids, 1):
            connection.execute(
                sa.text("UPDATE chapters SET number = :number WHERE id = :id"),
                {"number": number, "id": chapter_id},
            )
    if not is_sqlite:
        with op.batch_alter_table("chapters") as batch_op:
            batch_op.alter_column("project_id", existing_type=sa.Integer(), nullable=False)
            batch_op.create_foreign_key(
                "fk_chapters_project_id",
                "projects",
                ["project_id"],
                ["id"],
                ondelete="CASCADE",
            )
    op.create_index("ix_chapters_project_id", "chapters", ["project_id"], unique=False)

    op.create_index(
        "uq_active_volume_position",
        "volumes",
        ["project_id", "position"],
        unique=True,
        sqlite_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_active_chapter_position",
        "chapters",
        ["volume_id", "position"],
        unique=True,
        sqlite_where=sa.text("deleted_at IS NULL"),
    )
    op.create_index(
        "uq_active_project_chapter_number",
        "chapters",
        ["project_id", "number"],
        unique=True,
        sqlite_where=sa.text("deleted_at IS NULL AND number IS NOT NULL"),
    )
    op.create_index(
        "uq_active_scene_position",
        "scenes",
        ["chapter_id", "position"],
        unique=True,
        sqlite_where=sa.text("deleted_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_active_scene_position", table_name="scenes")
    op.drop_index("uq_active_project_chapter_number", table_name="chapters")
    op.drop_index("uq_active_chapter_position", table_name="chapters")
    op.drop_index("uq_active_volume_position", table_name="volumes")
    op.drop_index("ix_chapters_project_id", table_name="chapters")
    if op.get_bind().dialect.name != "sqlite":
        with op.batch_alter_table("chapters") as batch_op:
            batch_op.drop_constraint("fk_chapters_project_id", type_="foreignkey")
    op.drop_column("chapters", "number")
    op.drop_column("chapters", "project_id")
