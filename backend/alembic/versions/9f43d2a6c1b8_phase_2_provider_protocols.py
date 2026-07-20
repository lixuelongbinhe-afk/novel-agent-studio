"""phase 2 provider protocols

Revision ID: 9f43d2a6c1b8
Revises: 13da8433608a
Create Date: 2026-07-18 03:25:00
"""

from datetime import datetime, timezone
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "9f43d2a6c1b8"
down_revision: Union[str, Sequence[str], None] = "13da8433608a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PRESETS = [
    ("openai", "OpenAI", "openai_responses", "https://api.openai.com/v1", "gpt-5-mini", "OPENAI_API_KEY"),
    ("deepseek", "DeepSeek", "openai_chat", "https://api.deepseek.com/v1", "deepseek-chat", "DEEPSEEK_API_KEY"),
    ("xai", "xAI / Grok", "openai_chat", "https://api.x.ai/v1", "grok-4", "XAI_API_KEY"),
    ("anthropic", "Anthropic", "anthropic", "https://api.anthropic.com", "claude-sonnet-4-5", "ANTHROPIC_API_KEY"),
    ("gemini", "Gemini", "gemini", "https://generativelanguage.googleapis.com/v1beta", "gemini-2.5-flash", "GEMINI_API_KEY"),
    ("openrouter", "OpenRouter", "openai_chat", "https://openrouter.ai/api/v1", "", "OPENROUTER_API_KEY"),
    ("ollama", "Ollama", "ollama", "http://127.0.0.1:11434", "", ""),
    ("openai-compatible", "通用 OpenAI-compatible", "openai_compatible", "", "", "PROVIDER_API_KEY"),
    ("anthropic-compatible", "通用 Anthropic-compatible", "anthropic_compatible", "", "", "PROVIDER_API_KEY"),
]


def upgrade() -> None:
    preset_table = op.create_table(
        "provider_presets",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("protocol", sa.String(length=80), nullable=False),
        sa.Column("base_url", sa.String(length=500), nullable=False),
        sa.Column("default_model", sa.String(length=160), nullable=False),
        sa.Column("credential_env_var_hint", sa.String(length=120), nullable=False),
        sa.Column("options_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug"),
    )
    op.create_index(op.f("ix_provider_presets_slug"), "provider_presets", ["slug"], unique=True)
    now = datetime.now(timezone.utc)
    op.bulk_insert(
        preset_table,
        [
            {
                "id": index,
                "slug": slug,
                "name": name,
                "protocol": protocol,
                "base_url": base_url,
                "default_model": default_model,
                "credential_env_var_hint": env_hint,
                "options_json": "{}",
                "created_at": now,
                "updated_at": now,
                "deleted_at": None,
                "revision": 1,
            }
            for index, (slug, name, protocol, base_url, default_model, env_hint) in enumerate(PRESETS, 1)
        ],
    )
    with op.batch_alter_table("protocol_configurations") as batch_op:
        batch_op.create_unique_constraint("uq_protocol_provider", ["provider_account_id"])
    with op.batch_alter_table("model_profiles") as batch_op:
        batch_op.create_unique_constraint("uq_provider_model_name", ["provider_account_id", "name"])


def downgrade() -> None:
    with op.batch_alter_table("model_profiles") as batch_op:
        batch_op.drop_constraint("uq_provider_model_name", type_="unique")
    with op.batch_alter_table("protocol_configurations") as batch_op:
        batch_op.drop_constraint("uq_protocol_provider", type_="unique")
    op.drop_index(op.f("ix_provider_presets_slug"), table_name="provider_presets")
    op.drop_table("provider_presets")
