from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


ContentClassificationValue = Literal[
    "public",
    "internal",
    "confidential",
    "personal information",
    "sensitive personal information",
    "unpublished manuscript",
    "secret",
]

ALL_CLASSIFICATIONS: list[ContentClassificationValue] = [
    "public",
    "internal",
    "confidential",
    "personal information",
    "sensitive personal information",
    "unpublished manuscript",
    "secret",
]

DEFAULT_SECTION_PRIORITIES: dict[str, int] = {
    "user_task": 100,
    "current_scene": 95,
    "character_state": 90,
    "world_rules": 85,
    "location_item_relation": 80,
    "style": 75,
    "timeline": 70,
    "foreshadow": 68,
    "neighbor_summaries": 55,
    "history": 45,
    "upstream": 88,
}


class RevisionRead(BaseModel):
    id: int
    revision: int
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ChapterSummaryBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_id: int = Field(ge=1)
    summary: str = Field(min_length=1, max_length=200_000)
    key_events: list[str] = Field(default_factory=list, max_length=500)
    entity_ids: list[int] = Field(default_factory=list, max_length=2_000)
    source: Literal["manual", "approved_extraction", "import"] = "manual"


class ChapterSummaryCreate(ChapterSummaryBase):
    pass


class ChapterSummaryUpdate(ChapterSummaryBase):
    expected_revision: int = Field(ge=1)


class ChapterSummaryRead(ChapterSummaryBase, RevisionRead):
    token_count: int


class SceneStateBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_id: int = Field(ge=1)
    viewpoint_entity_id: int | None = Field(default=None, ge=1)
    location_entity_id: int | None = Field(default=None, ge=1)
    item_entity_ids: list[int] = Field(default_factory=list, max_length=2_000)
    state: dict[str, Any] = Field(default_factory=dict)
    notes: str = Field(default="", max_length=200_000)


class SceneStateCreate(SceneStateBase):
    pass


class SceneStateUpdate(SceneStateBase):
    expected_revision: int = Field(ge=1)


class SceneStateRead(SceneStateBase, RevisionRead):
    pass


class ChapterEntityLinkBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_id: int = Field(ge=1)
    entity_id: int = Field(ge=1)
    link_type: str = Field(default="manual", min_length=1, max_length=60)
    relevance: float = Field(default=1.0, ge=0, le=1)
    notes: str = Field(default="", max_length=20_000)


class ChapterEntityLinkCreate(ChapterEntityLinkBase):
    pass


class ChapterEntityLinkUpdate(ChapterEntityLinkBase):
    expected_revision: int = Field(ge=1)


class ChapterEntityLinkRead(ChapterEntityLinkBase, RevisionRead):
    pass


class ContextPinBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(ge=1)
    source_type: str = Field(min_length=1, max_length=60, pattern=r"^[a-z][a-z0-9_-]*$")
    source_id: int = Field(ge=1)
    label: str = Field(default="", max_length=200)
    content_override: str = Field(default="", max_length=200_000)
    priority: int = Field(default=100, ge=0, le=1_000)
    required: bool = False
    enabled: bool = True


class ContextPinCreate(ContextPinBase):
    pass


class ContextPinUpdate(ContextPinBase):
    expected_revision: int = Field(ge=1)


class ContextPinRead(ContextPinBase, RevisionRead):
    pass


class ContentClassificationBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(ge=1)
    source_type: str = Field(min_length=1, max_length=60, pattern=r"^[a-z][a-z0-9_-]*$")
    source_id: int = Field(ge=1)
    classification: ContentClassificationValue = "unpublished manuscript"
    reason: str = Field(default="", max_length=20_000)


class ContentClassificationCreate(ContentClassificationBase):
    pass


class ContentClassificationUpdate(ContentClassificationBase):
    expected_revision: int = Field(ge=1)


class ContentClassificationRead(ContentClassificationBase, RevisionRead):
    pass


class ContextPolicyBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=160)
    token_budget: int = Field(default=6_000, ge=128, le=2_000_000)
    recent_chapter_count: int = Field(default=3, ge=0, le=100)
    max_results: int = Field(default=80, ge=1, le=2_000)
    min_relevance: float = Field(default=0.2, ge=0, le=1)
    section_priorities: dict[str, int] = Field(
        default_factory=lambda: dict(DEFAULT_SECTION_PRIORITIES)
    )
    required_sections: list[str] = Field(
        default_factory=lambda: ["user_task"], max_length=32
    )
    allowed_classifications: list[ContentClassificationValue] = Field(
        default_factory=lambda: list(ALL_CLASSIFICATIONS[:-1]), max_length=7
    )
    use_summaries: bool = True
    enabled: bool = True

    @model_validator(mode="after")
    def normalize_sections(self) -> Self:
        priorities: dict[str, int] = {}
        for key, value in self.section_priorities.items():
            normalized = key.strip().lower()
            if not normalized or not normalized.replace("_", "").isalnum():
                raise ValueError("区块名称只能包含字母、数字和下划线")
            if not 0 <= value <= 1_000:
                raise ValueError("区块优先级必须在 0 到 1000 之间")
            priorities[normalized] = value
        self.section_priorities = priorities
        self.required_sections = list(
            dict.fromkeys(item.strip().lower() for item in self.required_sections if item.strip())
        )
        self.allowed_classifications = list(dict.fromkeys(self.allowed_classifications))
        return self


class ContextPolicyCreate(ContextPolicyBase):
    pass


class ContextPolicyUpdate(ContextPolicyBase):
    expected_revision: int = Field(ge=1)


class ContextPolicyRead(ContextPolicyBase, RevisionRead):
    pass


class ProviderDataPolicyBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider_account_id: int = Field(ge=1)
    allowed_classifications: list[ContentClassificationValue] = Field(max_length=7)
    block_on_required_exclusion: bool = True
    notes: str = Field(default="", max_length=20_000)
    enabled: bool = True

    @model_validator(mode="after")
    def normalize_classifications(self) -> Self:
        self.allowed_classifications = list(dict.fromkeys(self.allowed_classifications))
        if self.enabled and not self.allowed_classifications:
            raise ValueError("启用的数据策略至少允许一种数据分类")
        return self


class ProviderDataPolicyUpdate(ProviderDataPolicyBase):
    expected_revision: int = Field(ge=1)


class ProviderDataPolicyRead(ProviderDataPolicyBase, RevisionRead):
    inherited_default: bool = False


class ContextTargetProviderRead(BaseModel):
    provider_account_id: int
    provider_name: str
    provider_type: str
    model_profile_ids: list[int]
    allowed_classifications: list[ContentClassificationValue]
    policy_source: Literal["stored", "local_default", "remote_default"]


class ContextItemRead(BaseModel):
    key: str
    source_type: str
    source_id: int
    section: str
    title: str
    content: str
    relevance: float = Field(ge=0, le=1)
    reasons: list[str]
    token_estimate: int = Field(ge=0)
    original_token_estimate: int = Field(ge=0)
    classification: ContentClassificationValue
    pinned: bool
    priority: int
    required: bool
    locked: bool
    included: bool
    excluded_reason: str | None = None
    truncated: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextTruncationRead(BaseModel):
    key: str
    original_tokens: int
    final_tokens: int
    strategy: Literal["summary", "truncate", "omit_neighbor"]
    reason: str


class ContextBoundaryRead(BaseModel):
    policy_allowed: list[ContentClassificationValue]
    provider_allowed: list[ContentClassificationValue]
    effective_allowed: list[ContentClassificationValue]
    excluded_count: int
    required_excluded_count: int


class ContextBuildRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(ge=1)
    chapter_id: int | None = Field(default=None, ge=1)
    scene_id: int | None = Field(default=None, ge=1)
    agent_id: int | None = Field(default=None, ge=1)
    model_profile_id: int | None = Field(default=None, ge=1)
    policy_id: int | None = Field(default=None, ge=1)
    workflow_run_id: int | None = Field(default=None, ge=1)
    query: str = Field(default="", max_length=200_000)
    workflow_input: dict[str, Any] = Field(default_factory=dict)
    upstream_outputs: dict[str, Any] = Field(default_factory=dict)
    model_context_window: int | None = Field(default=None, ge=128, le=2_000_000)
    reserved_output_tokens: int = Field(default=1_024, ge=0, le=1_000_000)
    token_budget_override: int | None = Field(default=None, ge=128, le=2_000_000)
    excluded_keys: list[str] = Field(default_factory=list, max_length=2_000)
    locked_keys: list[str] = Field(default_factory=list, max_length=2_000)
    priority_overrides: dict[str, int] = Field(default_factory=dict)
    persist_snapshot: bool = False

    @model_validator(mode="after")
    def validate_transient_controls(self) -> Self:
        for key, priority in self.priority_overrides.items():
            if not key or len(key) > 240:
                raise ValueError("临时优先级的来源 key 无效")
            if not 0 <= priority <= 1_000:
                raise ValueError("临时优先级必须在 0 到 1000 之间")
        return self


class ContextBuildRead(BaseModel):
    id: int | None = None
    kind: Literal["context_package"] = "context_package"
    build_hash: str
    project_id: int
    chapter_id: int | None
    scene_id: int | None
    agent_id: int | None
    model_profile_id: int | None
    policy_id: int | None
    target_providers: list[ContextTargetProviderRead]
    token_budget: int
    reserved_output_tokens: int
    included_tokens: int
    context_text: str
    included: list[ContextItemRead]
    excluded: list[ContextItemRead]
    truncations: list[ContextTruncationRead]
    boundary: ContextBoundaryRead
    blocked: bool
    conflicts: list[str]
    created_at: datetime | None = None


class ContextFtsStatusRead(BaseModel):
    project_id: int
    indexed_records: int
    rebuilt: bool
