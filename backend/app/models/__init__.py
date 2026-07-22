from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)


class Project(TimestampMixin, Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    title: Mapped[str] = mapped_column(String(200), index=True)
    summary: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str] = mapped_column(String(32), default="zh-CN")
    target_words: Mapped[int] = mapped_column(Integer, default=100000)
    volumes: Mapped[list["Volume"]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Volume(TimestampMixin, Base):
    __tablename__ = "volumes"
    __table_args__ = (
        Index(
            "uq_active_volume_position",
            "project_id",
            "position",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    position: Mapped[int] = mapped_column(Integer, default=0)
    project: Mapped[Project] = relationship(back_populates="volumes")
    chapters: Mapped[list["Chapter"]] = relationship(back_populates="volume", cascade="all, delete-orphan")


class Chapter(TimestampMixin, Base):
    __tablename__ = "chapters"
    __table_args__ = (
        Index(
            "uq_active_chapter_position",
            "volume_id",
            "position",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
        ),
        Index(
            "uq_active_project_chapter_number",
            "project_id",
            "number",
            unique=True,
            sqlite_where=text("deleted_at IS NULL AND number IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    volume_id: Mapped[int] = mapped_column(ForeignKey("volumes.id", ondelete="CASCADE"), index=True)
    number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    volume: Mapped[Volume] = relationship(back_populates="chapters")
    scenes: Mapped[list["Scene"]] = relationship(back_populates="chapter", cascade="all, delete-orphan")
    versions: Mapped[list["ChapterVersion"]] = relationship(
        back_populates="chapter", cascade="all, delete-orphan"
    )


class Scene(TimestampMixin, Base):
    __tablename__ = "scenes"
    __table_args__ = (
        Index(
            "uq_active_scene_position",
            "chapter_id",
            "position",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chapter_id: Mapped[int] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    synopsis: Mapped[str] = mapped_column(Text, default="")
    content: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer, default=0)
    chapter: Mapped[Chapter] = relationship(back_populates="scenes")


class ChapterVersion(Base):
    __tablename__ = "chapter_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    chapter_id: Mapped[int] = mapped_column(ForeignKey("chapters.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[str] = mapped_column(Text, default="")
    word_count: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(40), default="manual")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    chapter: Mapped[Chapter] = relationship(back_populates="versions")


class StoryEntity(TimestampMixin, Base):
    __tablename__ = "story_entities"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(40), default="character")
    description: Mapped[str] = mapped_column(Text, default="")
    tags: Mapped[str] = mapped_column(Text, default="[]")


class EntityAlias(TimestampMixin, Base):
    __tablename__ = "entity_aliases"
    __table_args__ = (UniqueConstraint("entity_id", "alias", name="uq_entity_alias"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("story_entities.id", ondelete="CASCADE"), index=True)
    alias: Mapped[str] = mapped_column(String(200))


class EntityRelation(TimestampMixin, Base):
    __tablename__ = "entity_relations"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    source_entity_id: Mapped[int] = mapped_column(ForeignKey("story_entities.id", ondelete="CASCADE"))
    target_entity_id: Mapped[int] = mapped_column(ForeignKey("story_entities.id", ondelete="CASCADE"))
    relation_type: Mapped[str] = mapped_column(String(80))
    notes: Mapped[str] = mapped_column(Text, default="")


class EntityStateChange(TimestampMixin, Base):
    __tablename__ = "entity_state_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    entity_id: Mapped[int] = mapped_column(ForeignKey("story_entities.id", ondelete="CASCADE"), index=True)
    chapter_id: Mapped[int | None] = mapped_column(ForeignKey("chapters.id", ondelete="SET NULL"))
    field_name: Mapped[str] = mapped_column(String(100))
    old_value: Mapped[str] = mapped_column(Text, default="")
    new_value: Mapped[str] = mapped_column(Text, default="")
    reason: Mapped[str] = mapped_column(Text, default="")


class TimelineEvent(TimestampMixin, Base):
    __tablename__ = "timeline_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    chapter_id: Mapped[int | None] = mapped_column(ForeignKey("chapters.id", ondelete="SET NULL"))
    label: Mapped[str] = mapped_column(String(200))
    event_time: Mapped[str] = mapped_column(String(100), default="")
    description: Mapped[str] = mapped_column(Text, default="")
    position: Mapped[int] = mapped_column(Integer, default=0)


class Foreshadow(TimestampMixin, Base):
    __tablename__ = "foreshadows"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    setup_text: Mapped[str] = mapped_column(Text)
    payoff_text: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(40), default="open")
    chapter_id: Mapped[int | None] = mapped_column(ForeignKey("chapters.id", ondelete="SET NULL"))


class StyleGuide(TimestampMixin, Base):
    __tablename__ = "style_guides"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    rule_text: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(80), default="voice")


class ProviderAccount(TimestampMixin, Base):
    __tablename__ = "provider_accounts"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    provider_type: Mapped[str] = mapped_column(String(80), default="mock")
    credential_env_var: Mapped[str | None] = mapped_column(String(120), nullable=True)
    base_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ProtocolConfiguration(TimestampMixin, Base):
    __tablename__ = "protocol_configurations"
    __table_args__ = (UniqueConstraint("provider_account_id", name="uq_protocol_provider"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_account_id: Mapped[int] = mapped_column(ForeignKey("provider_accounts.id", ondelete="CASCADE"))
    protocol: Mapped[str] = mapped_column(String(80), default="mock")
    options_json: Mapped[str] = mapped_column(Text, default="{}")


class ProviderPreset(TimestampMixin, Base):
    __tablename__ = "provider_presets"

    id: Mapped[int] = mapped_column(primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    protocol: Mapped[str] = mapped_column(String(80))
    base_url: Mapped[str] = mapped_column(String(500), default="")
    default_model: Mapped[str] = mapped_column(String(160), default="")
    credential_env_var_hint: Mapped[str] = mapped_column(String(120), default="")
    options_json: Mapped[str] = mapped_column(Text, default="{}")

    @property
    def options(self) -> dict[str, object]:
        import json

        value = json.loads(self.options_json)
        return value if isinstance(value, dict) else {}


class ModelProfile(TimestampMixin, Base):
    __tablename__ = "model_profiles"
    __table_args__ = (
        UniqueConstraint("provider_account_id", "name", name="uq_provider_model_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_account_id: Mapped[int] = mapped_column(ForeignKey("provider_accounts.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(160))
    display_name: Mapped[str] = mapped_column(String(200))
    context_window: Mapped[int] = mapped_column(Integer, default=8192)
    tokenizer_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    tokenizer_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ModelCapability(TimestampMixin, Base):
    __tablename__ = "model_capabilities"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_profile_id: Mapped[int] = mapped_column(ForeignKey("model_profiles.id", ondelete="CASCADE"))
    capability: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40), default="supported")
    source: Mapped[str] = mapped_column(String(80), default="provider_default")


class ModelPricing(TimestampMixin, Base):
    __tablename__ = "model_pricing"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_profile_id: Mapped[int] = mapped_column(ForeignKey("model_profiles.id", ondelete="CASCADE"))
    input_per_million: Mapped[float | None] = mapped_column(Float, nullable=True)
    cached_input_per_million: Mapped[float | None] = mapped_column(Float, nullable=True)
    output_per_million: Mapped[float | None] = mapped_column(Float, nullable=True)
    reasoning_per_million: Mapped[float | None] = mapped_column(Float, nullable=True)
    request_fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    tool_call_fee: Mapped[float | None] = mapped_column(Float, nullable=True)
    currency: Mapped[str] = mapped_column(String(12), default="USD")
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class CapabilityProbeRun(TimestampMixin, Base):
    __tablename__ = "capability_probe_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    model_profile_id: Mapped[int] = mapped_column(
        ForeignKey("model_profiles.id", ondelete="CASCADE"), index=True
    )
    level: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(32), default="running")
    request_count: Mapped[int] = mapped_column(Integer, default=0)
    max_output_tokens: Mapped[int] = mapped_column(Integer, default=64)
    estimated_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ModelRoute(TimestampMixin, Base):
    __tablename__ = "model_routes"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    name: Mapped[str] = mapped_column(String(160))
    strategy: Mapped[str] = mapped_column(String(40), default="ordered_fallback")
    required_capabilities_json: Mapped[str] = mapped_column(Text, default="[]")
    allow_degradation: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ModelRouteEntry(TimestampMixin, Base):
    __tablename__ = "model_route_entries"
    __table_args__ = (
        UniqueConstraint("route_id", "model_profile_id", name="uq_route_model"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    route_id: Mapped[int] = mapped_column(
        ForeignKey("model_routes.id", ondelete="CASCADE"), index=True
    )
    model_profile_id: Mapped[int] = mapped_column(
        ForeignKey("model_profiles.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class RateLimitPolicy(TimestampMixin, Base):
    __tablename__ = "rate_limit_policies"
    __table_args__ = (
        UniqueConstraint("scope_type", "scope_key", name="uq_rate_limit_scope"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(32))
    scope_key: Mapped[str] = mapped_column(String(120), default="*")
    max_concurrency: Mapped[int | None] = mapped_column(Integer, nullable=True)
    requests_per_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_per_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    queue_timeout_seconds: Mapped[float] = mapped_column(Float, default=30.0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class BudgetPolicy(TimestampMixin, Base):
    __tablename__ = "budget_policies"
    __table_args__ = (
        UniqueConstraint("scope_type", "scope_key", name="uq_budget_scope"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(32))
    scope_key: Mapped[str] = mapped_column(String(120), default="*")
    max_cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    currency: Mapped[str] = mapped_column(String(12), default="USD")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ProviderHealth(TimestampMixin, Base):
    __tablename__ = "provider_health"
    __table_args__ = (
        UniqueConstraint("provider_account_id", name="uq_provider_health_account"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_account_id: Mapped[int] = mapped_column(
        ForeignKey("provider_accounts.id", ondelete="CASCADE"), index=True
    )
    state: Mapped[str] = mapped_column(String(20), default="closed")
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)
    failure_threshold: Mapped[int] = mapped_column(Integer, default=3)
    recovery_timeout_seconds: Mapped[float] = mapped_column(Float, default=30.0)
    half_open_in_flight: Mapped[bool] = mapped_column(Boolean, default=False)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)


class ModelInvocation(Base):
    __tablename__ = "model_invocations"

    id: Mapped[int] = mapped_column(primary_key=True)
    request_id: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider_account_id: Mapped[int] = mapped_column(
        ForeignKey("provider_accounts.id", ondelete="CASCADE"), index=True
    )
    model_profile_id: Mapped[int] = mapped_column(
        ForeignKey("model_profiles.id", ondelete="CASCADE"), index=True
    )
    route_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_routes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    route_run_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    workflow_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cached_input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    reasoning_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    usage_estimated: Mapped[bool] = mapped_column(Boolean, default=True)
    token_source: Mapped[str] = mapped_column(String(40), default="local_approximation")
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_known: Mapped[bool] = mapped_column(Boolean, default=False)
    currency: Mapped[str] = mapped_column(String(12), default="USD")
    queue_ms: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fallback_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AgentDefinition(TimestampMixin, Base):
    __tablename__ = "agent_definitions"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_agent_project_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(160))
    agent_type: Mapped[str] = mapped_column(String(80), default="custom")
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    prompt_template: Mapped[str] = mapped_column(Text)
    input_schema_json: Mapped[str] = mapped_column(Text, default="{}")
    output_schema_json: Mapped[str] = mapped_column(Text, default="{}")
    output_mode: Mapped[str] = mapped_column(String(20), default="text")
    model_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_profiles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    route_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_routes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    parameters_json: Mapped[str] = mapped_column(Text, default="{}")
    required_capabilities_json: Mapped[str] = mapped_column(Text, default="[]")
    allow_degradation: Mapped[bool] = mapped_column(Boolean, default=True)
    timeout_seconds: Mapped[float] = mapped_column(Float, default=120.0)
    retry_count: Mapped[int] = mapped_column(Integer, default=1)
    budget_json: Mapped[str] = mapped_column(Text, default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    config_hash: Mapped[str] = mapped_column(String(64), index=True)


class Workflow(TimestampMixin, Base):
    __tablename__ = "workflows"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_workflow_project_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(180))
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class WorkflowNode(TimestampMixin, Base):
    __tablename__ = "workflow_nodes"
    __table_args__ = (
        UniqueConstraint("workflow_id", "node_key", name="uq_workflow_node_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[int] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), index=True
    )
    node_key: Mapped[str] = mapped_column(String(64))
    node_type: Mapped[str] = mapped_column(String(40))
    label: Mapped[str] = mapped_column(String(160))
    position_x: Mapped[float] = mapped_column(Float, default=0.0)
    position_y: Mapped[float] = mapped_column(Float, default=0.0)
    config_json: Mapped[str] = mapped_column(Text, default="{}")


class WorkflowEdge(TimestampMixin, Base):
    __tablename__ = "workflow_edges"
    __table_args__ = (
        UniqueConstraint("workflow_id", "edge_key", name="uq_workflow_edge_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[int] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), index=True
    )
    edge_key: Mapped[str] = mapped_column(String(100))
    source_node_key: Mapped[str] = mapped_column(String(64))
    target_node_key: Mapped[str] = mapped_column(String(64))
    source_handle: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_handle: Mapped[str | None] = mapped_column(String(40), nullable=True)


class WorkflowRun(TimestampMixin, Base):
    __tablename__ = "workflow_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[int] = mapped_column(
        ForeignKey("workflows.id", ondelete="CASCADE"), index=True
    )
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    parent_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    workflow_revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    source_mode: Mapped[str] = mapped_column(String(32), default="fresh")
    resume_node_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_json: Mapped[str] = mapped_column(Text, default="{}")
    output_json: Mapped[str] = mapped_column(Text, default="null")
    plan_json: Mapped[str] = mapped_column(Text)
    snapshot_json: Mapped[str] = mapped_column(Text)
    error_json: Mapped[str] = mapped_column(Text, default="null")
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    event_sequence: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NodeRun(TimestampMixin, Base):
    __tablename__ = "node_runs"
    __table_args__ = (
        UniqueConstraint("workflow_run_id", "node_key", name="uq_run_node_key"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_run_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True
    )
    node_key: Mapped[str] = mapped_column(String(64))
    node_type: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    activated: Mapped[bool] = mapped_column(Boolean, default=False)
    input_json: Mapped[str] = mapped_column(Text, default="null")
    output_json: Mapped[str] = mapped_column(Text, default="null")
    error_json: Mapped[str] = mapped_column(Text, default="null")
    warnings_json: Mapped[str] = mapped_column(Text, default="[]")
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class NodeRunAttempt(Base):
    __tablename__ = "node_run_attempts"
    __table_args__ = (
        UniqueConstraint("node_run_id", "attempt_number", name="uq_node_attempt_number"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    node_run_id: Mapped[int] = mapped_column(
        ForeignKey("node_runs.id", ondelete="CASCADE"), index=True
    )
    attempt_number: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32), default="running", index=True)
    input_json: Mapped[str] = mapped_column(Text, default="null")
    output_json: Mapped[str] = mapped_column(Text, default="null")
    partial_output: Mapped[str] = mapped_column(Text, default="")
    error_json: Mapped[str] = mapped_column(Text, default="null")
    model_invocation_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float | None] = mapped_column(Float, nullable=True)
    cost_known: Mapped[bool] = mapped_column(Boolean, default=False)
    currency: Mapped[str] = mapped_column(String(12), default="USD")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class WorkflowRunEvent(Base):
    __tablename__ = "workflow_run_events"
    __table_args__ = (
        UniqueConstraint("workflow_run_id", "sequence", name="uq_run_event_sequence"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_run_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True
    )
    sequence: Mapped[int] = mapped_column(Integer)
    event_type: Mapped[str] = mapped_column(String(60), index=True)
    node_key: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChapterSummary(TimestampMixin, Base):
    __tablename__ = "chapter_summaries"
    __table_args__ = (UniqueConstraint("chapter_id", name="uq_chapter_summary_chapter"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    chapter_id: Mapped[int] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE"), index=True
    )
    summary: Mapped[str] = mapped_column(Text, default="")
    key_events_json: Mapped[str] = mapped_column(Text, default="[]")
    entity_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(40), default="manual")


class SceneState(TimestampMixin, Base):
    __tablename__ = "scene_states"
    __table_args__ = (UniqueConstraint("scene_id", name="uq_scene_state_scene"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    scene_id: Mapped[int] = mapped_column(
        ForeignKey("scenes.id", ondelete="CASCADE"), index=True
    )
    viewpoint_entity_id: Mapped[int | None] = mapped_column(
        ForeignKey("story_entities.id", ondelete="SET NULL"), nullable=True, index=True
    )
    location_entity_id: Mapped[int | None] = mapped_column(
        ForeignKey("story_entities.id", ondelete="SET NULL"), nullable=True, index=True
    )
    item_entity_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    state_json: Mapped[str] = mapped_column(Text, default="{}")
    notes: Mapped[str] = mapped_column(Text, default="")


class ChapterEntityLink(TimestampMixin, Base):
    __tablename__ = "chapter_entity_links"
    __table_args__ = (
        UniqueConstraint(
            "chapter_id", "entity_id", "link_type", name="uq_chapter_entity_link"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    chapter_id: Mapped[int] = mapped_column(
        ForeignKey("chapters.id", ondelete="CASCADE"), index=True
    )
    entity_id: Mapped[int] = mapped_column(
        ForeignKey("story_entities.id", ondelete="CASCADE"), index=True
    )
    link_type: Mapped[str] = mapped_column(String(60), default="manual")
    relevance: Mapped[float] = mapped_column(Float, default=1.0)
    notes: Mapped[str] = mapped_column(Text, default="")


class ContextPin(TimestampMixin, Base):
    __tablename__ = "context_pins"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "source_type", "source_id", name="uq_context_pin_source"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    source_type: Mapped[str] = mapped_column(String(60))
    source_id: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(200), default="")
    content_override: Mapped[str] = mapped_column(Text, default="")
    priority: Mapped[int] = mapped_column(Integer, default=100)
    required: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ContentClassification(TimestampMixin, Base):
    __tablename__ = "content_classifications"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "source_type", "source_id", name="uq_content_classification_source"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    source_type: Mapped[str] = mapped_column(String(60))
    source_id: Mapped[int] = mapped_column(Integer)
    classification: Mapped[str] = mapped_column(
        String(60), default="unpublished manuscript"
    )
    reason: Mapped[str] = mapped_column(Text, default="")


class ContextPolicy(TimestampMixin, Base):
    __tablename__ = "context_policies"
    __table_args__ = (
        UniqueConstraint("project_id", "name", name="uq_context_policy_project_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(160))
    token_budget: Mapped[int] = mapped_column(Integer, default=6000)
    recent_chapter_count: Mapped[int] = mapped_column(Integer, default=3)
    max_results: Mapped[int] = mapped_column(Integer, default=80)
    min_relevance: Mapped[float] = mapped_column(Float, default=0.2)
    section_priorities_json: Mapped[str] = mapped_column(Text, default="{}")
    required_sections_json: Mapped[str] = mapped_column(Text, default="[]")
    allowed_classifications_json: Mapped[str] = mapped_column(Text, default="[]")
    use_summaries: Mapped[bool] = mapped_column(Boolean, default=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ProviderDataPolicy(TimestampMixin, Base):
    __tablename__ = "provider_data_policies"
    __table_args__ = (
        UniqueConstraint("provider_account_id", name="uq_provider_data_policy_account"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_account_id: Mapped[int] = mapped_column(
        ForeignKey("provider_accounts.id", ondelete="CASCADE"), index=True
    )
    allowed_classifications_json: Mapped[str] = mapped_column(Text, default="[]")
    block_on_required_exclusion: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)


class ContextBuild(Base):
    __tablename__ = "context_builds"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    chapter_id: Mapped[int | None] = mapped_column(
        ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    scene_id: Mapped[int | None] = mapped_column(
        ForeignKey("scenes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    agent_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_definitions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    workflow_run_id: Mapped[int | None] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    model_profile_id: Mapped[int | None] = mapped_column(
        ForeignKey("model_profiles.id", ondelete="SET NULL"), nullable=True, index=True
    )
    policy_id: Mapped[int | None] = mapped_column(
        ForeignKey("context_policies.id", ondelete="SET NULL"), nullable=True, index=True
    )
    provider_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    request_json: Mapped[str] = mapped_column(Text)
    result_json: Mapped[str] = mapped_column(Text)
    context_text: Mapped[str] = mapped_column(Text, default="")
    build_hash: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ApprovalRequest(TimestampMixin, Base):
    __tablename__ = "approval_requests"
    __table_args__ = (
        UniqueConstraint(
            "workflow_run_id",
            "node_key",
            "snapshot_revision",
            name="uq_approval_run_node_snapshot_revision",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    workflow_run_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True
    )
    node_run_id: Mapped[int] = mapped_column(
        ForeignKey("node_runs.id", ondelete="CASCADE"), index=True
    )
    node_key: Mapped[str] = mapped_column(String(64), index=True)
    approval_type: Mapped[str] = mapped_column(String(40), index=True)
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    title: Mapped[str] = mapped_column(String(240))
    instructions: Mapped[str] = mapped_column(Text, default="")
    snapshot_json: Mapped[str] = mapped_column(Text)
    snapshot_hash: Mapped[str] = mapped_column(String(64), index=True)
    snapshot_revision: Mapped[int] = mapped_column(Integer, default=1)
    round_number: Mapped[int] = mapped_column(Integer, default=1)
    parent_approval_id: Mapped[int | None] = mapped_column(
        ForeignKey("approval_requests.id", ondelete="SET NULL"), nullable=True, index=True
    )
    superseded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("approval_requests.id", ondelete="SET NULL"), nullable=True, index=True
    )
    decision_action: Mapped[str | None] = mapped_column(String(32), nullable=True)
    decision_note: Mapped[str] = mapped_column(Text, default="")
    decision_payload_json: Mapped[str] = mapped_column(Text, default="null")
    decision_idempotency_key: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )
    decision_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ProposedChangeSet(TimestampMixin, Base):
    __tablename__ = "proposed_change_sets"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    workflow_run_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True
    )
    node_run_id: Mapped[int] = mapped_column(
        ForeignKey("node_runs.id", ondelete="CASCADE"), index=True
    )
    node_key: Mapped[str] = mapped_column(String(64), index=True)
    source_approval_id: Mapped[int | None] = mapped_column(
        ForeignKey("approval_requests.id", ondelete="SET NULL"), nullable=True, index=True
    )
    chapter_id: Mapped[int | None] = mapped_column(
        ForeignKey("chapters.id", ondelete="SET NULL"), nullable=True, index=True
    )
    scene_id: Mapped[int | None] = mapped_column(
        ForeignKey("scenes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    extraction_json: Mapped[str] = mapped_column(Text, default="{}")
    base_revisions_json: Mapped[str] = mapped_column(Text, default="{}")
    items_json: Mapped[str] = mapped_column(Text, default="[]")
    conflicts_json: Mapped[str] = mapped_column(Text, default="[]")
    changes_hash: Mapped[str] = mapped_column(String(64), index=True)
    superseded_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("proposed_change_sets.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    applied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class WritebackAudit(Base):
    __tablename__ = "writeback_audits"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    workflow_run_id: Mapped[int] = mapped_column(
        ForeignKey("workflow_runs.id", ondelete="CASCADE"), index=True
    )
    change_set_id: Mapped[int] = mapped_column(
        ForeignKey("proposed_change_sets.id", ondelete="RESTRICT"), index=True
    )
    approval_request_id: Mapped[int] = mapped_column(
        ForeignKey("approval_requests.id", ondelete="RESTRICT"), index=True
    )
    change_set_hash: Mapped[str] = mapped_column(String(64), index=True)
    entries_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class CredentialReference(TimestampMixin, Base):
    __tablename__ = "credential_references"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    env_var_name: Mapped[str] = mapped_column(String(120))


class GenericHttpAdapterConfiguration(TimestampMixin, Base):
    __tablename__ = "generic_http_adapter_configurations"
    __table_args__ = (
        UniqueConstraint("provider_account_id", name="uq_generic_http_provider"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    provider_account_id: Mapped[int] = mapped_column(
        ForeignKey("provider_accounts.id", ondelete="CASCADE"), index=True
    )
    credential_reference_id: Mapped[int | None] = mapped_column(
        ForeignKey("credential_references.id", ondelete="SET NULL"), nullable=True
    )
    method: Mapped[str] = mapped_column(String(8), default="POST")
    endpoint: Mapped[str] = mapped_column(String(500), default="/")
    content_type: Mapped[str] = mapped_column(String(120), default="application/json")
    response_mode: Mapped[str] = mapped_column(String(40), default="json")
    stream_format: Mapped[str] = mapped_column(String(40), default="sse")
    security_mode: Mapped[str] = mapped_column(String(40), default="public_only")
    query_json: Mapped[str] = mapped_column(Text, default="{}")
    headers_json: Mapped[str] = mapped_column(Text, default="{}")
    request_template_json: Mapped[str] = mapped_column(Text, default="{}")
    parameter_mapping_json: Mapped[str] = mapped_column(Text, default="{}")
    response_mapping_json: Mapped[str] = mapped_column(Text, default="{}")
    stream_mapping_json: Mapped[str] = mapped_column(Text, default="{}")
    error_mapping_json: Mapped[str] = mapped_column(Text, default="{}")
    auth_json: Mapped[str] = mapped_column(Text, default='{"type":"none"}')
    capability_defaults_json: Mapped[str] = mapped_column(Text, default="{}")
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_origin: Mapped[str | None] = mapped_column(String(500), nullable=True)
    approval_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    tested_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    last_tested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class StudioProjectState(TimestampMixin, Base):
    __tablename__ = "studio_project_states"
    __table_args__ = (UniqueConstraint("project_id", name="uq_studio_project_state"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    entry_mode: Mapped[str] = mapped_column(String(24), default="creative")
    stage: Mapped[str] = mapped_column(String(40), default="idea", index=True)
    review_granularity: Mapped[str] = mapped_column(String(24), default="chapter")
    routing_strategy: Mapped[str] = mapped_column(String(24), default="balanced")
    generation_mode: Mapped[str] = mapped_column(String(24), default="countdown")
    countdown_seconds: Mapped[int] = mapped_column(Integer, default=10)
    memory_mode: Mapped[str] = mapped_column(String(24), default="automatic")
    budget_limit: Mapped[float | None] = mapped_column(Float, nullable=True)
    budget_spent: Mapped[float] = mapped_column(Float, default=0.0)
    budget_currency: Mapped[str] = mapped_column(String(12), default="USD")
    budget_warning_percent: Mapped[int] = mapped_column(Integer, default=70)
    budget_pause_percent: Mapped[int] = mapped_column(Integer, default=110)
    budget_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    config_json: Mapped[str] = mapped_column(Text, default="{}")


class CreativeArtifact(TimestampMixin, Base):
    __tablename__ = "creative_artifacts"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(48), index=True)
    title: Mapped[str] = mapped_column(String(240))
    content: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="pending", index=True)
    source: Mapped[str] = mapped_column(String(24), default="ai")
    position: Mapped[int] = mapped_column(Integer, default=0)
    version_number: Mapped[int] = mapped_column(Integer, default=1)
    notes: Mapped[str] = mapped_column(Text, default="")
    metadata_json: Mapped[str] = mapped_column(Text, default="{}")


class StudioMessage(Base):
    __tablename__ = "studio_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(20))
    content: Mapped[str] = mapped_column(Text)
    context_scope: Mapped[str] = mapped_column(String(80), default="project")
    proposal_json: Mapped[str] = mapped_column(Text, default="null")
    proposal_status: Mapped[str] = mapped_column(String(24), default="none", index=True)
    model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    model_reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class GenerationJob(TimestampMixin, Base):
    __tablename__ = "generation_jobs"
    __table_args__ = (
        UniqueConstraint(
            "project_id", "idempotency_key", name="uq_generation_job_idempotency"
        ),
        UniqueConstraint("active_scope_key", name="uq_generation_job_active_scope"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(48), index=True)
    label: Mapped[str] = mapped_column(String(240))
    status: Mapped[str] = mapped_column(String(24), default="queued", index=True)
    progress: Mapped[int] = mapped_column(Integer, default=0)
    model_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    model_reason: Mapped[str] = mapped_column(Text, default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    active_scope_key: Mapped[str | None] = mapped_column(String(320), nullable=True)
    result_artifact_id: Mapped[int | None] = mapped_column(
        ForeignKey("creative_artifacts.id", ondelete="SET NULL"), nullable=True
    )


class ProjectSnapshot(Base):
    __tablename__ = "project_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(24), default="automatic", index=True)
    label: Mapped[str] = mapped_column(String(240))
    reason: Mapped[str] = mapped_column(Text, default="")
    payload_json: Mapped[str] = mapped_column(Text)
    permanent: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
