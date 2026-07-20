import json
import re
from datetime import datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class RevisionUpdate(BaseModel):
    expected_revision: int = Field(ge=1)


class ReorderItem(BaseModel):
    id: int = Field(ge=1)
    position: int = Field(ge=0)
    expected_revision: int = Field(ge=1)


class ReorderRequest(BaseModel):
    items: list[ReorderItem] = Field(min_length=1, max_length=500)

    @field_validator("items")
    @classmethod
    def unique_ids(cls, value: list[ReorderItem]) -> list[ReorderItem]:
        if len({item.id for item in value}) != len(value):
            raise ValueError("reorder item ids must be unique")
        return value


class ReorderItemRead(ORMModel):
    id: int
    position: int
    revision: int


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=10000)
    language: str = Field(default="zh-CN", min_length=2, max_length=32)
    target_words: int = Field(default=100000, ge=1, le=100_000_000)


class ProjectUpdate(ProjectCreate, RevisionUpdate):
    pass


class ProjectRead(ORMModel):
    id: int
    title: str
    summary: str
    language: str
    target_words: int
    revision: int
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class VolumeCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    position: int = Field(default=0, ge=0)


class VolumeUpdate(VolumeCreate, RevisionUpdate):
    pass


class VolumeRead(ORMModel):
    id: int
    project_id: int
    title: str
    position: int
    revision: int
    deleted_at: datetime | None


class ChapterCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(default="", max_length=2_000_000)
    position: int = Field(default=0, ge=0)


class ChapterRead(ORMModel):
    id: int
    volume_id: int
    title: str
    content: str
    position: int
    word_count: int
    revision: int
    deleted_at: datetime | None
    updated_at: datetime


class ChapterAutosave(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    content: str = Field(max_length=2_000_000)
    expected_revision: int = Field(ge=1)


class ChapterVersionRead(ORMModel):
    id: int
    chapter_id: int
    title: str
    content: str
    word_count: int
    source: str
    created_at: datetime


class SceneCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    synopsis: str = Field(default="", max_length=20000)
    content: str = Field(default="", max_length=500000)
    position: int = Field(default=0, ge=0)


class SceneUpdate(SceneCreate, RevisionUpdate):
    pass


class SceneRead(ORMModel):
    id: int
    chapter_id: int
    title: str
    synopsis: str
    content: str
    position: int
    revision: int
    deleted_at: datetime | None


class ProjectTreeRead(BaseModel):
    project: ProjectRead
    volumes: list[VolumeRead]
    chapters: list[ChapterRead]
    scenes: list[SceneRead]


class StoryEntityCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    kind: Literal["character", "location", "item", "organization", "concept"] = "character"
    description: str = Field(default="", max_length=100000)
    tags: list[str] = Field(default_factory=list, max_length=100)

    @field_validator("tags")
    @classmethod
    def clean_tags(cls, value: list[str]) -> list[str]:
        tags = [tag.strip() for tag in value if tag.strip()]
        if any(len(tag) > 80 for tag in tags):
            raise ValueError("tag is too long")
        return list(dict.fromkeys(tags))


class StoryEntityUpdate(StoryEntityCreate, RevisionUpdate):
    pass


class StoryEntityRead(ORMModel):
    id: int
    project_id: int
    name: str
    kind: str
    description: str
    tags: list[str]
    revision: int
    deleted_at: datetime | None

    @field_validator("tags", mode="before")
    @classmethod
    def parse_tags(cls, value: object) -> list[str]:
        if isinstance(value, str):
            parsed = json.loads(value or "[]")
            return [str(item) for item in parsed] if isinstance(parsed, list) else []
        return list(value) if isinstance(value, list) else []


class EntityAliasCreate(BaseModel):
    alias: str = Field(min_length=1, max_length=200)


class EntityAliasUpdate(EntityAliasCreate, RevisionUpdate):
    pass


class EntityAliasRead(ORMModel):
    id: int
    entity_id: int
    alias: str
    revision: int
    deleted_at: datetime | None


class EntityRelationCreate(BaseModel):
    source_entity_id: int = Field(ge=1)
    target_entity_id: int = Field(ge=1)
    relation_type: str = Field(min_length=1, max_length=80)
    notes: str = Field(default="", max_length=20000)

    @model_validator(mode="after")
    def different_entities(self) -> "EntityRelationCreate":
        if self.source_entity_id == self.target_entity_id:
            raise ValueError("relation endpoints must be different")
        return self


class EntityRelationUpdate(EntityRelationCreate, RevisionUpdate):
    pass


class EntityRelationRead(ORMModel):
    id: int
    project_id: int
    source_entity_id: int
    target_entity_id: int
    relation_type: str
    notes: str
    revision: int
    deleted_at: datetime | None


class EntityStateChangeCreate(BaseModel):
    entity_id: int = Field(ge=1)
    chapter_id: int | None = Field(default=None, ge=1)
    field_name: str = Field(min_length=1, max_length=100)
    old_value: str = Field(default="", max_length=50000)
    new_value: str = Field(default="", max_length=50000)
    reason: str = Field(default="", max_length=20000)


class EntityStateChangeUpdate(EntityStateChangeCreate, RevisionUpdate):
    pass


class EntityStateChangeRead(ORMModel):
    id: int
    entity_id: int
    chapter_id: int | None
    field_name: str
    old_value: str
    new_value: str
    reason: str
    revision: int
    deleted_at: datetime | None


class TimelineEventCreate(BaseModel):
    chapter_id: int | None = Field(default=None, ge=1)
    label: str = Field(min_length=1, max_length=200)
    event_time: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=100000)
    position: int = Field(default=0, ge=0)


class TimelineEventUpdate(TimelineEventCreate, RevisionUpdate):
    pass


class TimelineEventRead(ORMModel):
    id: int
    project_id: int
    chapter_id: int | None
    label: str
    event_time: str
    description: str
    position: int
    revision: int
    deleted_at: datetime | None


class ForeshadowCreate(BaseModel):
    setup_text: str = Field(min_length=1, max_length=100000)
    payoff_text: str = Field(default="", max_length=100000)
    status: Literal["open", "developing", "resolved", "abandoned"] = "open"
    chapter_id: int | None = Field(default=None, ge=1)


class ForeshadowUpdate(ForeshadowCreate, RevisionUpdate):
    pass


class ForeshadowRead(ORMModel):
    id: int
    project_id: int
    setup_text: str
    payoff_text: str
    status: str
    chapter_id: int | None
    revision: int
    deleted_at: datetime | None


class StyleGuideCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    rule_text: str = Field(min_length=1, max_length=100000)
    category: str = Field(default="voice", min_length=1, max_length=80)


class StyleGuideUpdate(StyleGuideCreate, RevisionUpdate):
    pass


class StyleGuideRead(ORMModel):
    id: int
    project_id: int
    name: str
    rule_text: str
    category: str
    revision: int
    deleted_at: datetime | None


LibraryRead = (
    StoryEntityRead
    | EntityAliasRead
    | EntityRelationRead
    | EntityStateChangeRead
    | TimelineEventRead
    | ForeshadowRead
    | StyleGuideRead
)


class ProviderAccountCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    provider_type: str = Field(default="mock", min_length=1, max_length=80)
    credential_env_var: str | None = Field(default=None, max_length=120)
    base_url: str | None = Field(default=None, max_length=500)
    enabled: bool = True

    @field_validator("credential_env_var")
    @classmethod
    def validate_env_name(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{0,119}", value):
            raise ValueError("credential_env_var must be an environment variable name")
        return value

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return value
        try:
            parsed = urlsplit(value)
            port = parsed.port
        except ValueError as exc:
            raise ValueError("base_url is invalid") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url must be an http or https URL with a hostname")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("base_url must not contain credentials")
        if parsed.query or parsed.fragment:
            raise ValueError("base_url must not contain query parameters or fragments")
        if port is not None and not 1 <= port <= 65535:
            raise ValueError("base_url port is invalid")
        return value


class ProviderAccountUpdate(ProviderAccountCreate, RevisionUpdate):
    pass


class ProviderAccountRead(ORMModel):
    id: int
    name: str
    provider_type: str
    credential_env_var: str | None
    base_url: str | None
    enabled: bool
    revision: int
    deleted_at: datetime | None


class ProviderPresetCreate(BaseModel):
    slug: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,79}$")
    name: str = Field(min_length=1, max_length=120)
    protocol: str = Field(min_length=1, max_length=80)
    base_url: str = Field(default="", max_length=500)
    default_model: str = Field(default="", max_length=160)
    credential_env_var_hint: str = Field(default="", max_length=120)
    options: dict[str, Any] = Field(default_factory=dict)


class ProviderPresetUpdate(ProviderPresetCreate, RevisionUpdate):
    pass


class ProviderPresetRead(ORMModel):
    id: int
    slug: str
    name: str
    protocol: str
    base_url: str
    default_model: str
    credential_env_var_hint: str
    options: dict[str, Any]
    revision: int


class ModelProfileCreate(BaseModel):
    provider_account_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=160)
    display_name: str = Field(min_length=1, max_length=200)
    context_window: int = Field(default=8192, ge=512)
    tokenizer_name: str | None = Field(default=None, max_length=120)
    tokenizer_source: Literal["official_tokenizer", "compatible_tokenizer"] | None = None
    enabled: bool = True

    @model_validator(mode="after")
    def tokenizer_fields_match(self) -> "ModelProfileCreate":
        if bool(self.tokenizer_name) != bool(self.tokenizer_source):
            raise ValueError("tokenizer_name and tokenizer_source must be set together")
        return self


class ModelProfileUpdate(BaseModel):
    display_name: str = Field(min_length=1, max_length=200)
    context_window: int = Field(default=8192, ge=512)
    tokenizer_name: str | None = Field(default=None, max_length=120)
    tokenizer_source: Literal["official_tokenizer", "compatible_tokenizer"] | None = None
    enabled: bool = True
    expected_revision: int = Field(ge=1)

    @model_validator(mode="after")
    def tokenizer_fields_match(self) -> "ModelProfileUpdate":
        if bool(self.tokenizer_name) != bool(self.tokenizer_source):
            raise ValueError("tokenizer_name and tokenizer_source must be set together")
        return self


class ModelProfileRead(ORMModel):
    id: int
    provider_account_id: int
    name: str
    display_name: str
    context_window: int
    tokenizer_name: str | None
    tokenizer_source: Literal["official_tokenizer", "compatible_tokenizer"] | None
    enabled: bool
    revision: int
    deleted_at: datetime | None


class ProviderConnectionRead(BaseModel):
    ok: bool
    protocol: str
    latency_ms: int
    request_id: str
    model_count: int = 0
    error: "NormalizedProviderError | None" = None


class ModelSyncRead(BaseModel):
    provider_account_id: int
    discovered: int
    created: int
    updated: int
    models: list[ModelProfileRead]


class NormalizedToolDefinition(BaseModel):
    name: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_-]{0,127}$")
    description: str = Field(default="", max_length=10_000)
    input_schema: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    side_effect: bool = False


class NormalizedToolCall(BaseModel):
    id: str = Field(default="", max_length=200)
    name: str = Field(min_length=1, max_length=128)
    arguments: dict[str, Any] | str = Field(default_factory=dict)


class NormalizedContentPart(BaseModel):
    type: Literal["text", "json", "tool_call", "tool_result"] = "text"
    text: str | None = Field(default=None, max_length=2_000_000)
    data: dict[str, Any] | None = None
    tool_call_id: str | None = Field(default=None, max_length=200)
    name: str | None = Field(default=None, max_length=128)
    arguments: dict[str, Any] | str | None = None


class NormalizedMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: list[NormalizedContentPart] = Field(min_length=1, max_length=1000)


class NormalizedUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cached_input_tokens: int = 0
    reasoning_tokens: int = 0
    estimated: bool = True
    source: Literal[
        "provider_actual",
        "provider_estimate",
        "official_tokenizer",
        "compatible_tokenizer",
        "local_approximation",
    ] = "local_approximation"


class NormalizedModelRequest(BaseModel):
    model: str = Field(default="mock-novel-v1", min_length=1, max_length=200)
    messages: list[NormalizedMessage] = Field(min_length=1, max_length=1000)
    stream: bool = False
    temperature: float = Field(default=0.7, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    max_tokens: int = Field(default=1024, ge=1, le=1_000_000)
    response_format: Literal["text", "json"] = "text"
    json_schema: dict[str, Any] | None = None
    tools: list[NormalizedToolDefinition] = Field(default_factory=list, max_length=128)
    tool_choice: Literal["auto", "none", "required"] = "auto"
    metadata: dict[str, str] = Field(default_factory=dict)
    scenario: Literal["normal", "delay", "timeout", "rate_limit", "error"] = "normal"


class NormalizedProviderError(BaseModel):
    code: str
    message: str
    retryable: bool = False
    status_code: int | None = None
    request_id: str | None = None
    retry_after_seconds: float | None = Field(default=None, ge=0)


class ModelDebugRequest(NormalizedModelRequest):
    provider_account_id: int | None = Field(default=None, ge=1)
    model_profile_id: int | None = Field(default=None, ge=1)
    route_id: int | None = Field(default=None, ge=1)
    manual_model_profile_id: int | None = Field(default=None, ge=1)
    project_id: int | None = Field(default=None, ge=1)
    workflow_id: str | None = Field(default=None, max_length=120)
    route_run_id: str | None = Field(default=None, max_length=120)
    required_capabilities: list[str] = Field(default_factory=list, max_length=32)
    allow_degradation: bool = True
    max_retries: int = Field(default=1, ge=0, le=5)


class NormalizedModelResponse(BaseModel):
    model: str
    text: str
    content: list[NormalizedContentPart] = Field(default_factory=list)
    structured_data: dict[str, Any] | None = None
    tool_calls: list[NormalizedToolCall] = Field(default_factory=list)
    finish_reason: str = "stop"
    usage: NormalizedUsage
    request_id: str
    error: NormalizedProviderError | None = None
    warnings: list[str] = Field(default_factory=list)
    control: dict[str, Any] | None = None


class NormalizedStreamEvent(BaseModel):
    sequence: int
    event: Literal["start", "delta", "tool_call_delta", "usage", "warning", "error", "done"]
    text_delta: str = ""
    tool_call: NormalizedToolCall | None = None
    usage: NormalizedUsage | None = None
    error: NormalizedProviderError | None = None
    finish_reason: str | None = None
    request_id: str | None = None
    warning: str | None = None


from app.schemas.custom_api import (  # noqa: E402
    CredentialReferenceCreate as CredentialReferenceCreate,
    CredentialReferenceRead as CredentialReferenceRead,
    CredentialReferenceUpdate as CredentialReferenceUpdate,
    GenericAdapterManifest as GenericAdapterManifest,
    GenericAdapterTestRead as GenericAdapterTestRead,
    GenericAdapterTestRequest as GenericAdapterTestRequest,
    GenericHttpAdapterCreate as GenericHttpAdapterCreate,
    GenericHttpAdapterRead as GenericHttpAdapterRead,
    GenericHttpAdapterSetupCreate as GenericHttpAdapterSetupCreate,
    GenericHttpAdapterUpdate as GenericHttpAdapterUpdate,
    ManifestImportRead as ManifestImportRead,
    OriginApprovalRequest as OriginApprovalRequest,
)
from app.schemas.context import (  # noqa: E402
    ALL_CLASSIFICATIONS as ALL_CLASSIFICATIONS,
    ChapterEntityLinkCreate as ChapterEntityLinkCreate,
    ChapterEntityLinkRead as ChapterEntityLinkRead,
    ChapterEntityLinkUpdate as ChapterEntityLinkUpdate,
    ChapterSummaryCreate as ChapterSummaryCreate,
    ChapterSummaryRead as ChapterSummaryRead,
    ChapterSummaryUpdate as ChapterSummaryUpdate,
    ContentClassificationCreate as ContentClassificationCreate,
    ContentClassificationRead as ContentClassificationRead,
    ContentClassificationUpdate as ContentClassificationUpdate,
    ContextBoundaryRead as ContextBoundaryRead,
    ContextBuildRead as ContextBuildRead,
    ContextBuildRequest as ContextBuildRequest,
    ContextFtsStatusRead as ContextFtsStatusRead,
    ContextItemRead as ContextItemRead,
    ContextPinCreate as ContextPinCreate,
    ContextPinRead as ContextPinRead,
    ContextPinUpdate as ContextPinUpdate,
    ContextPolicyCreate as ContextPolicyCreate,
    ContextPolicyRead as ContextPolicyRead,
    ContextPolicyUpdate as ContextPolicyUpdate,
    ContextTargetProviderRead as ContextTargetProviderRead,
    ContextTruncationRead as ContextTruncationRead,
    ProviderDataPolicyRead as ProviderDataPolicyRead,
    ProviderDataPolicyUpdate as ProviderDataPolicyUpdate,
    SceneStateCreate as SceneStateCreate,
    SceneStateRead as SceneStateRead,
    SceneStateUpdate as SceneStateUpdate,
)
from app.schemas.approvals import (  # noqa: E402
    ApprovalCreate as ApprovalCreate,
    ApprovalDecisionRead as ApprovalDecisionRead,
    ApprovalDecisionRequest as ApprovalDecisionRequest,
    ApprovalRequestRead as ApprovalRequestRead,
    ApprovalSnapshot as ApprovalSnapshot,
    ChapterSummaryExtraction as ChapterSummaryExtraction,
    EntityExtraction as EntityExtraction,
    EntityStateExtraction as EntityStateExtraction,
    ExtractionIssue as ExtractionIssue,
    ForeshadowExtraction as ForeshadowExtraction,
    ProposedChangeItem as ProposedChangeItem,
    ProposedChangeSetCreate as ProposedChangeSetCreate,
    ProposedChangeSetEdit as ProposedChangeSetEdit,
    ProposedChangeSetEditRead as ProposedChangeSetEditRead,
    ProposedChangeSetRead as ProposedChangeSetRead,
    ProposedChangeSetRebase as ProposedChangeSetRebase,
    RelationshipExtraction as RelationshipExtraction,
    SceneStateExtraction as SceneStateExtraction,
    SceneSummaryExtraction as SceneSummaryExtraction,
    StateExtractionResult as StateExtractionResult,
    TimelineExtraction as TimelineExtraction,
    WritebackAuditRead as WritebackAuditRead,
    WritebackRequest as WritebackRequest,
    WritebackResultRead as WritebackResultRead,
)
from app.schemas.model_control import (  # noqa: E402
    BudgetPolicyRead as BudgetPolicyRead,
    BudgetPolicyUpdate as BudgetPolicyUpdate,
    BudgetPolicyWrite as BudgetPolicyWrite,
    CapabilityOverrideWrite as CapabilityOverrideWrite,
    CapabilityProbeRead as CapabilityProbeRead,
    CapabilityProbeRequest as CapabilityProbeRequest,
    ContextPreflightRead as ContextPreflightRead,
    CostEstimateRead as CostEstimateRead,
    EffectiveCapabilitiesRead as EffectiveCapabilitiesRead,
    EffectiveCapabilityRead as EffectiveCapabilityRead,
    ExecutionPreflightRead as ExecutionPreflightRead,
    ModelInvocationRead as ModelInvocationRead,
    ModelPricingRead as ModelPricingRead,
    ModelPricingWrite as ModelPricingWrite,
    ModelRouteRead as ModelRouteRead,
    ModelRouteUpdate as ModelRouteUpdate,
    ModelRouteWrite as ModelRouteWrite,
    ProviderHealthRead as ProviderHealthRead,
    RateLimitPolicyRead as RateLimitPolicyRead,
    RateLimitPolicyUpdate as RateLimitPolicyUpdate,
    RateLimitPolicyWrite as RateLimitPolicyWrite,
    RouteEntryRead as RouteEntryRead,
    RouteEntryWrite as RouteEntryWrite,
    TokenEstimateRead as TokenEstimateRead,
)
from app.schemas.workflows import (  # noqa: E402
    AgentBudget as AgentBudget,
    AgentDefinitionCreate as AgentDefinitionCreate,
    AgentDefinitionRead as AgentDefinitionRead,
    AgentDefinitionUpdate as AgentDefinitionUpdate,
    AgentParameters as AgentParameters,
    NodeRunAttemptRead as NodeRunAttemptRead,
    NodeRunRead as NodeRunRead,
    WorkflowCreate as WorkflowCreate,
    WorkflowEdgeWrite as WorkflowEdgeWrite,
    WorkflowManifest as WorkflowManifest,
    WorkflowManifestImport as WorkflowManifestImport,
    WorkflowNodeWrite as WorkflowNodeWrite,
    WorkflowRead as WorkflowRead,
    WorkflowRunCreate as WorkflowRunCreate,
    WorkflowRunDerive as WorkflowRunDerive,
    WorkflowRunEventRead as WorkflowRunEventRead,
    WorkflowRunRead as WorkflowRunRead,
    WorkflowRunSnapshotRead as WorkflowRunSnapshotRead,
    WorkflowRunSummaryRead as WorkflowRunSummaryRead,
    WorkflowSummaryRead as WorkflowSummaryRead,
    WorkflowUpdate as WorkflowUpdate,
    WorkflowValidationIssue as WorkflowValidationIssue,
    WorkflowValidationRead as WorkflowValidationRead,
)
