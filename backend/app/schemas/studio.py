from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


EntryMode = Literal["creative", "outline"]
StageName = Literal[
    "idea",
    "world",
    "characters",
    "plot",
    "volumes",
    "chapters",
    "drafting",
    "review",
    "complete",
]


class StudioProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    idea: str = Field(min_length=1, max_length=50_000)
    entry_mode: EntryMode = "creative"
    target_words: int = Field(default=100_000, ge=1, le=100_000_000)
    genre: str = Field(default="", max_length=120)
    theme: str = Field(default="", max_length=500)
    era: str = Field(default="", max_length=200)
    audience: str = Field(default="", max_length=200)
    chapter_count: int | None = Field(default=None, ge=1, le=10_000)
    chapter_words: int | None = Field(default=None, ge=100, le=100_000)
    style_description: str = Field(default="", max_length=20_000)
    point_of_view: str = Field(default="", max_length=120)
    prohibited_content: str = Field(default="", max_length=20_000)


class ContinuationImportRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    text: str | None = Field(default=None, max_length=10_000_000)
    source_project_id: int | None = Field(default=None, ge=1)
    source_name: str = Field(default="粘贴正文", max_length=260)
    target_words: int | None = Field(default=None, ge=1, le=100_000_000)
    target_chapters: int | None = Field(default=None, ge=1, le=10_000)
    target_volumes: int | None = Field(default=None, ge=1, le=1_000)
    continuation_start: Literal["choose", "current", "next"] = "choose"
    direction_mode: Literal["user", "ai", "switchable"] = "switchable"
    user_outline: str = Field(default="", max_length=500_000)

    @model_validator(mode="after")
    def require_one_source(self) -> "ContinuationImportRequest":
        has_text = bool(self.text and self.text.strip())
        if has_text == (self.source_project_id is not None):
            raise ValueError("必须提供正文文本或选择一个已有项目，且只能选择一种来源")
        return self


class ContinuationSettingsUpdate(BaseModel):
    target_words: int | None = Field(default=None, ge=1, le=100_000_000)
    target_chapters: int | None = Field(default=None, ge=1, le=10_000)
    target_volumes: int | None = Field(default=None, ge=1, le=1_000)
    continuation_start: Literal["choose", "current", "next"] | None = None
    direction_mode: Literal["user", "ai", "switchable"] | None = None
    user_outline: str | None = Field(default=None, max_length=500_000)


class StudioStateUpdate(BaseModel):
    review_granularity: Literal["chapter", "scene"] | None = None
    routing_strategy: Literal["quality", "cost", "speed", "balanced"] | None = None
    generation_mode: Literal["manual", "automatic", "countdown"] | None = None
    countdown_seconds: int | None = Field(default=None, ge=0, le=3600)
    memory_mode: Literal["automatic", "confirm"] | None = None
    budget_limit: float | None = Field(default=None, gt=0)
    budget_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3,12}$")
    budget_paused: bool | None = None


class ArtifactUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=240)
    content: str | None = Field(default=None, max_length=2_000_000)
    notes: str | None = Field(default=None, max_length=100_000)
    expected_revision: int = Field(ge=1)


class ArtifactDecision(BaseModel):
    action: Literal["approve", "request_changes", "reject"]
    note: str = Field(default="", max_length=100_000)
    conflict_resolution: Literal[
        "preserve_prose", "preserve_canon", "manual_merge"
    ] | None = None
    expected_revision: int = Field(ge=1)


class GenerateRequest(BaseModel):
    instruction: str = Field(default="", max_length=100_000)
    agent_name: str | None = Field(default=None, min_length=1, max_length=120)
    chapter_id: int | None = Field(default=None, ge=1)
    selected_text: str = Field(default="", max_length=200_000)
    mode: Literal["new", "continue", "local_revision", "full_rewrite", "variants"] = "new"
    use_demo_model: bool = False


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=100_000)
    chapter_id: int | None = Field(default=None, ge=1)
    selected_text: str = Field(default="", max_length=200_000)
    stage: str | None = Field(default=None, max_length=40)
    use_demo_model: bool = False


class MessageProposalDecision(BaseModel):
    action: Literal["apply", "reject"]


class OutlinePreviewRead(BaseModel):
    title: str
    volumes: list[dict[str, Any]]
    volume_count: int
    chapter_count: int
    scene_count: int
    warnings: list[str] = Field(default_factory=list)


class OutlineImportRequest(BaseModel):
    text: str = Field(min_length=1, max_length=5_000_000)
    replace_existing: bool = True


class SnapshotCreate(BaseModel):
    label: str = Field(min_length=1, max_length=240)
    reason: str = Field(default="", max_length=20_000)
    special: bool = False


class ChapterTreeRepairRequest(BaseModel):
    confirm: bool = False


class ProviderSetup(BaseModel):
    preset: Literal[
        "deepseek",
        "openai",
        "anthropic",
        "gemini",
        "xai",
        "openrouter",
        "openai_compatible",
    ]
    name: str = Field(min_length=1, max_length=120)
    base_url: str = Field(min_length=1, max_length=500)
    model: str = Field(min_length=1, max_length=160)
    api_key: str | None = Field(default=None, min_length=1, max_length=10_000)
    env_var_name: str | None = Field(default=None, pattern=r"^[A-Z_][A-Z0-9_]{1,119}$")

    @field_validator("api_key")
    @classmethod
    def strip_secret(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None


class ProviderSecretUpdate(BaseModel):
    api_key: str = Field(min_length=1, max_length=10_000)


class StudioRecord(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
