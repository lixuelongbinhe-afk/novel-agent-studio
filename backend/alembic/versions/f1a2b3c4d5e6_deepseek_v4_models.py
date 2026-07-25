"""migrate official DeepSeek profiles to V4 models

Revision ID: f1a2b3c4d5e6
Revises: e8f1c3d5a740
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "e8f1c3d5a740"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


DEEPSEEK_URLS = (
    "https://api.deepseek.com",
    "https://api.deepseek.com/",
    "https://api.deepseek.com/v1",
    "https://api.deepseek.com/v1/",
)

MODEL_REFERENCE_TABLES = (
    "model_capabilities",
    "model_pricing",
    "capability_probe_runs",
    "model_invocations",
    "agent_definitions",
    "context_builds",
)


def _merge_existing_target(
    connection: sa.Connection,
    *,
    legacy_model: str,
    v4_model: str,
) -> None:
    collisions = connection.execute(
        sa.text(
            "SELECT legacy.id, current.id FROM model_profiles AS legacy "
            "JOIN provider_accounts ON provider_accounts.id = legacy.provider_account_id "
            "JOIN model_profiles AS current "
            "ON current.provider_account_id = legacy.provider_account_id "
            "AND current.name = :v4_model "
            "WHERE legacy.name = :legacy_model "
            "AND provider_accounts.deleted_at IS NULL "
            "AND provider_accounts.base_url IN :deepseek_urls"
        ).bindparams(sa.bindparam("deepseek_urls", expanding=True)),
        {
            "legacy_model": legacy_model,
            "v4_model": v4_model,
            "deepseek_urls": DEEPSEEK_URLS,
        },
    ).all()
    for legacy_id, current_id in collisions:
        connection.execute(
            sa.text(
                "DELETE FROM model_route_entries WHERE model_profile_id = :legacy_id "
                "AND EXISTS (SELECT 1 FROM model_route_entries AS current_entry "
                "WHERE current_entry.route_id = model_route_entries.route_id "
                "AND current_entry.model_profile_id = :current_id)"
            ),
            {"legacy_id": legacy_id, "current_id": current_id},
        )
        connection.execute(
            sa.text(
                "UPDATE model_route_entries SET model_profile_id = :current_id "
                "WHERE model_profile_id = :legacy_id"
            ),
            {"legacy_id": legacy_id, "current_id": current_id},
        )
        for table in MODEL_REFERENCE_TABLES:
            connection.execute(
                sa.text(
                    f"UPDATE {table} SET model_profile_id = :current_id "
                    "WHERE model_profile_id = :legacy_id"
                ),
                {"legacy_id": legacy_id, "current_id": current_id},
            )
        connection.execute(
            sa.text("DELETE FROM model_profiles WHERE id = :legacy_id"),
            {"legacy_id": legacy_id},
        )


def upgrade() -> None:
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "UPDATE provider_presets SET default_model = 'deepseek-v4-flash', "
            "revision = revision + 1, updated_at = CURRENT_TIMESTAMP "
            "WHERE slug = 'deepseek' "
            "AND default_model IN ('deepseek-chat', 'deepseek-reasoner')"
        )
    )
    for legacy_model, v4_model in (
        ("deepseek-chat", "deepseek-v4-flash"),
        ("deepseek-reasoner", "deepseek-v4-flash"),
    ):
        _merge_existing_target(
            connection,
            legacy_model=legacy_model,
            v4_model=v4_model,
        )
        connection.execute(
            sa.text(
                "UPDATE model_profiles SET name = :v4_model, "
                "display_name = CASE WHEN display_name = :legacy_model "
                "THEN :v4_model ELSE display_name END, "
                "context_window = 1000000, revision = revision + 1, "
                "updated_at = CURRENT_TIMESTAMP "
                "WHERE name = :legacy_model AND provider_account_id IN ("
                "SELECT id FROM provider_accounts WHERE deleted_at IS NULL "
                "AND base_url IN :deepseek_urls) "
                "AND NOT EXISTS (SELECT 1 FROM model_profiles AS current "
                "WHERE current.provider_account_id = model_profiles.provider_account_id "
                "AND current.name = :v4_model)"
            ).bindparams(sa.bindparam("deepseek_urls", expanding=True)),
            {
                "legacy_model": legacy_model,
                "v4_model": v4_model,
                "deepseek_urls": DEEPSEEK_URLS,
            },
        )
    connection.execute(
        sa.text(
            "UPDATE model_profiles SET context_window = 1000000, "
            "revision = revision + 1, updated_at = CURRENT_TIMESTAMP "
            "WHERE name IN ('deepseek-v4-flash', 'deepseek-v4-pro') "
            "AND context_window != 1000000 AND provider_account_id IN ("
            "SELECT id FROM provider_accounts WHERE deleted_at IS NULL "
            "AND base_url IN :deepseek_urls)"
        ).bindparams(sa.bindparam("deepseek_urls", expanding=True)),
        {"deepseek_urls": DEEPSEEK_URLS},
    )


def downgrade() -> None:
    # The retired aliases are intentionally not restored because doing so would
    # leave upgraded installations unable to call the official DeepSeek API.
    pass
