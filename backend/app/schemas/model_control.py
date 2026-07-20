from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


CapabilityStatus = Literal["supported", "unsupported", "unknown", "degraded", "emulated"]
CapabilitySource = Literal[
    "provider_default",
    "imported_manifest",
    "model_list_api",
    "official_metadata",
    "automatic_probe",
    "manual_override",
]
ProbeLevel = Literal["basic", "standard", "advanced"]
RouteStrategy = Literal[
    "ordered_fallback",
    "lowest_cost",
    "lowest_latency",
    "healthiest",
    "manual_only",
]
LimitScope = Literal["global", "project", "provider", "model", "route", "workflow"]
BudgetScope = Literal["per_request", "project_daily", "route_per_run"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CapabilityOverrideWrite(BaseModel):
    status: CapabilityStatus


class EffectiveCapabilityRead(BaseModel):
    capability: str
    status: CapabilityStatus
    source: CapabilitySource
    reason: str


class EffectiveCapabilitiesRead(BaseModel):
    model_profile_id: int
    provider_account_id: int
    capabilities: list[EffectiveCapabilityRead]
    warnings: list[str] = Field(default_factory=list)
    generated_at: datetime


class CapabilityProbeRequest(BaseModel):
    level: ProbeLevel = "basic"
    confirm_advanced: bool = False
    max_estimated_cost: float = Field(default=0.05, gt=0, le=1.0)

    @model_validator(mode="after")
    def advanced_requires_confirmation(self) -> CapabilityProbeRequest:
        if self.level == "advanced" and not self.confirm_advanced:
            raise ValueError("advanced capability probing requires explicit confirmation")
        return self


class CapabilityProbeRead(ORMModel):
    id: int
    model_profile_id: int
    level: ProbeLevel
    status: str
    request_count: int
    max_output_tokens: int
    estimated_cost: float | None
    results: dict[str, CapabilityStatus]
    error_code: str | None
    completed_at: datetime | None
    created_at: datetime


class ModelPricingWrite(BaseModel):
    input_per_million: float | None = Field(default=None, ge=0)
    cached_input_per_million: float | None = Field(default=None, ge=0)
    output_per_million: float | None = Field(default=None, ge=0)
    reasoning_per_million: float | None = Field(default=None, ge=0)
    request_fee: float | None = Field(default=None, ge=0)
    tool_call_fee: float | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3,12}$")
    effective_from: datetime
    effective_to: datetime | None = None

    @model_validator(mode="after")
    def valid_interval(self) -> ModelPricingWrite:
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must be after effective_from")
        return self


class ModelPricingRead(ORMModel):
    id: int
    model_profile_id: int
    input_per_million: float | None
    cached_input_per_million: float | None
    output_per_million: float | None
    reasoning_per_million: float | None
    request_fee: float | None
    tool_call_fee: float | None
    currency: str
    effective_from: datetime
    effective_to: datetime | None
    revision: int


class RouteEntryWrite(BaseModel):
    model_profile_id: int = Field(ge=1)
    position: int = Field(default=0, ge=0)
    enabled: bool = True


class RouteEntryRead(ORMModel):
    id: int
    route_id: int
    model_profile_id: int
    position: int
    enabled: bool
    revision: int


class ModelRouteWrite(BaseModel):
    project_id: int | None = Field(default=None, ge=1)
    name: str = Field(min_length=1, max_length=160)
    strategy: RouteStrategy = "ordered_fallback"
    required_capabilities: list[str] = Field(default_factory=list, max_length=32)
    allow_degradation: bool = True
    enabled: bool = True
    entries: list[RouteEntryWrite] = Field(min_length=1, max_length=64)

    @field_validator("required_capabilities")
    @classmethod
    def normalize_capabilities(cls, value: list[str]) -> list[str]:
        normalized = [item.strip().lower() for item in value if item.strip()]
        if len(set(normalized)) != len(normalized):
            raise ValueError("required capabilities must be unique")
        return normalized

    @field_validator("entries")
    @classmethod
    def unique_models(cls, value: list[RouteEntryWrite]) -> list[RouteEntryWrite]:
        if len({item.model_profile_id for item in value}) != len(value):
            raise ValueError("route model entries must be unique")
        return value


class ModelRouteUpdate(ModelRouteWrite):
    expected_revision: int = Field(ge=1)


class ModelRouteRead(ORMModel):
    id: int
    project_id: int | None
    name: str
    strategy: RouteStrategy
    required_capabilities: list[str]
    allow_degradation: bool
    enabled: bool
    revision: int
    entries: list[RouteEntryRead]


class RateLimitPolicyWrite(BaseModel):
    scope_type: LimitScope
    scope_key: str = Field(default="*", min_length=1, max_length=120)
    max_concurrency: int | None = Field(default=None, ge=1, le=1000)
    requests_per_minute: int | None = Field(default=None, ge=1, le=1_000_000)
    tokens_per_minute: int | None = Field(default=None, ge=1, le=1_000_000_000)
    queue_timeout_seconds: float = Field(default=30, gt=0, le=3600)
    enabled: bool = True

    @model_validator(mode="after")
    def at_least_one_limit(self) -> RateLimitPolicyWrite:
        if (
            self.max_concurrency is None
            and self.requests_per_minute is None
            and self.tokens_per_minute is None
        ):
            raise ValueError("at least one rate limit must be configured")
        if self.scope_type == "global" and self.scope_key != "*":
            raise ValueError("global scope_key must be *")
        return self


class RateLimitPolicyUpdate(RateLimitPolicyWrite):
    expected_revision: int = Field(ge=1)


class RateLimitPolicyRead(ORMModel):
    id: int
    scope_type: LimitScope
    scope_key: str
    max_concurrency: int | None
    requests_per_minute: int | None
    tokens_per_minute: int | None
    queue_timeout_seconds: float
    enabled: bool
    revision: int


class BudgetPolicyWrite(BaseModel):
    scope_type: BudgetScope
    scope_key: str = Field(default="*", min_length=1, max_length=120)
    max_cost: float | None = Field(default=None, ge=0)
    max_tokens: int | None = Field(default=None, ge=1)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3,12}$")
    enabled: bool = True

    @model_validator(mode="after")
    def at_least_one_budget(self) -> BudgetPolicyWrite:
        if self.max_cost is None and self.max_tokens is None:
            raise ValueError("at least one budget must be configured")
        if self.scope_type == "per_request" and self.scope_key != "*":
            raise ValueError("per_request scope_key must be *")
        return self


class BudgetPolicyUpdate(BudgetPolicyWrite):
    expected_revision: int = Field(ge=1)


class BudgetPolicyRead(ORMModel):
    id: int
    scope_type: BudgetScope
    scope_key: str
    max_cost: float | None
    max_tokens: int | None
    currency: str
    enabled: bool
    revision: int


class ProviderHealthRead(ORMModel):
    id: int
    provider_account_id: int
    state: Literal["closed", "open", "half_open"]
    consecutive_failures: int
    failure_threshold: int
    recovery_timeout_seconds: float
    half_open_in_flight: bool
    opened_at: datetime | None
    last_success_at: datetime | None
    last_failure_at: datetime | None
    last_latency_ms: int | None
    last_error_code: str | None


class TokenEstimateRead(BaseModel):
    tokens: int
    estimated: bool
    source: Literal[
        "provider_actual",
        "provider_estimate",
        "official_tokenizer",
        "compatible_tokenizer",
        "local_approximation",
    ]


class ContextPreflightRead(BaseModel):
    input: TokenEstimateRead
    reserved_output_tokens: int
    total_tokens: int
    context_window: int
    remaining_tokens: int
    utilization: float
    level: Literal["ok", "warning", "strong_warning", "blocked"]
    blocked: bool
    warnings: list[str] = Field(default_factory=list)


class CostEstimateRead(BaseModel):
    known: bool
    amount: float | None
    currency: str
    breakdown: dict[str, float | None]
    pricing_id: int | None
    reason: str | None = None


class ExecutionPreflightRead(BaseModel):
    model_profile_id: int
    provider_account_id: int
    model_name: str
    context: ContextPreflightRead
    estimated_cost: CostEstimateRead
    capabilities: EffectiveCapabilitiesRead
    warnings: list[str] = Field(default_factory=list)


class ModelInvocationRead(ORMModel):
    id: int
    request_id: str
    project_id: int | None
    provider_account_id: int
    model_profile_id: int
    route_id: int | None
    route_run_id: str | None
    workflow_id: str | None
    status: str
    input_tokens: int
    output_tokens: int
    total_tokens: int
    usage_estimated: bool
    token_source: str
    cost: float | None
    cost_known: bool
    currency: str
    queue_ms: int
    latency_ms: int | None
    fallback_count: int
    error_code: str | None
    started_at: datetime
    completed_at: datetime | None
