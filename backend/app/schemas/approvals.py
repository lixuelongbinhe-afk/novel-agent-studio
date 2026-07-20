from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


ApprovalStatus = Literal[
    "pending",
    "approved",
    "changes_requested",
    "rejected",
    "expired",
    "cancelled",
    "superseded",
]
ApprovalType = Literal["prose", "change_set", "generic"]
ApprovalAction = Literal["approve", "request_changes", "reject", "edit"]
ApprovalResolutionAction = Literal[
    "approve",
    "request_changes",
    "reject",
    "edit",
    "cancel",
    "expire",
]
ChangeDecision = Literal["accept", "reject", "later"]
ChangeSetStatus = Literal[
    "pending",
    "approved",
    "applied",
    "conflicted",
    "cancelled",
    "superseded",
]
ChangeKind = Literal[
    "chapter_content",
    "chapter_summary",
    "scene_synopsis",
    "scene_state",
    "entity",
    "entity_alias",
    "entity_relation",
    "entity_state_change",
    "timeline_event",
    "foreshadow",
]
ChangeOperation = Literal["create", "update", "upsert"]


class ApprovalSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["approval_snapshot"] = "approval_snapshot"
    approval_type: ApprovalType
    value: Any
    source: dict[str, Any] = Field(default_factory=dict)


class ApprovalCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(ge=1)
    workflow_run_id: int = Field(ge=1)
    node_run_id: int = Field(ge=1)
    node_key: str = Field(min_length=1, max_length=64)
    approval_type: ApprovalType
    title: str = Field(min_length=1, max_length=240)
    instructions: str = Field(default="", max_length=20_000)
    snapshot: ApprovalSnapshot
    snapshot_revision: int = Field(default=1, ge=1)
    round_number: int = Field(default=1, ge=1, le=3)
    parent_approval_id: int | None = Field(default=None, ge=1)
    expires_at: datetime | None = None


class ApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: ApprovalAction
    expected_revision: int = Field(ge=1)
    idempotency_key: str = Field(
        min_length=8,
        max_length=128,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    note: str = Field(default="", max_length=20_000)
    edited_value: Any = None

    @model_validator(mode="after")
    def validate_action_payload(self) -> Self:
        if self.action == "request_changes" and not self.note.strip():
            raise ValueError("要求修改必须填写说明")
        if self.action == "edit" and self.edited_value is None:
            raise ValueError("编辑审批必须提供 edited_value")
        if self.action != "edit" and self.edited_value is not None:
            raise ValueError("只有 edit 操作可以提交 edited_value")
        return self


class ApprovalRequestRead(BaseModel):
    id: int
    project_id: int
    workflow_run_id: int
    node_run_id: int
    node_key: str
    approval_type: ApprovalType
    status: ApprovalStatus
    title: str
    instructions: str
    snapshot: ApprovalSnapshot
    snapshot_hash: str
    snapshot_revision: int
    round_number: int
    parent_approval_id: int | None
    superseded_by_id: int | None
    decision_action: ApprovalResolutionAction | None
    decision_note: str
    decision_payload: Any
    expires_at: datetime | None
    resolved_at: datetime | None
    revision: int
    created_at: datetime
    updated_at: datetime


class ApprovalDecisionRead(BaseModel):
    approval: ApprovalRequestRead
    replacement: ApprovalRequestRead | None = None
    idempotent_replay: bool = False


class EntityStateExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field_name: str = Field(min_length=1, max_length=100)
    old_value: str | None = Field(default=None, max_length=20_000)
    new_value: str = Field(max_length=20_000)
    reason: str = Field(default="", max_length=20_000)


class ChapterSummaryExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(max_length=100_000)
    key_events: list[str] = Field(default_factory=list, max_length=200)
    evidence: list[str] = Field(default_factory=list, max_length=100)
    confidence: float = Field(default=1.0, ge=0, le=1)


class SceneSummaryExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_id: int | None = Field(default=None, ge=1)
    scene_title: str = Field(default="", max_length=200)
    summary: str = Field(max_length=50_000)
    evidence: list[str] = Field(default_factory=list, max_length=100)
    confidence: float = Field(default=1.0, ge=0, le=1)


class SceneStateExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scene_id: int | None = Field(default=None, ge=1)
    scene_title: str = Field(default="", max_length=200)
    viewpoint_entity_id: int | None = Field(default=None, ge=1)
    viewpoint_name: str = Field(default="", max_length=200)
    location_entity_id: int | None = Field(default=None, ge=1)
    location_name: str = Field(default="", max_length=200)
    item_entity_ids: list[int] = Field(default_factory=list, max_length=500)
    item_names: list[str] = Field(default_factory=list, max_length=500)
    state_updates: list[EntityStateExtraction] = Field(default_factory=list, max_length=500)
    notes: str = Field(default="", max_length=50_000)
    evidence: list[str] = Field(default_factory=list, max_length=100)
    confidence: float = Field(default=1.0, ge=0, le=1)


class EntityExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: int | None = Field(default=None, ge=1)
    name: str = Field(min_length=1, max_length=200)
    aliases: list[str] = Field(default_factory=list, max_length=100)
    description: str | None = Field(default=None, max_length=100_000)
    tags: list[str] = Field(default_factory=list, max_length=100)
    state_updates: list[EntityStateExtraction] = Field(default_factory=list, max_length=200)
    manual_link_type: str | None = Field(default=None, max_length=60)
    evidence: list[str] = Field(default_factory=list, max_length=100)
    confidence: float = Field(default=1.0, ge=0, le=1)


class RelationshipExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    relation_id: int | None = Field(default=None, ge=1)
    source_entity_id: int | None = Field(default=None, ge=1)
    source_name: str = Field(min_length=1, max_length=200)
    target_entity_id: int | None = Field(default=None, ge=1)
    target_name: str = Field(min_length=1, max_length=200)
    relation_type: str = Field(min_length=1, max_length=80)
    notes: str = Field(default="", max_length=20_000)
    evidence: list[str] = Field(default_factory=list, max_length=100)
    confidence: float = Field(default=1.0, ge=0, le=1)


class TimelineExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeline_event_id: int | None = Field(default=None, ge=1)
    label: str = Field(min_length=1, max_length=200)
    event_time: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=50_000)
    evidence: list[str] = Field(default_factory=list, max_length=100)
    confidence: float = Field(default=1.0, ge=0, le=1)


class ForeshadowExtraction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    foreshadow_id: int | None = Field(default=None, ge=1)
    action: Literal["new", "develop", "resolve"]
    setup_text: str = Field(min_length=1, max_length=100_000)
    payoff_text: str = Field(default="", max_length=100_000)
    evidence: list[str] = Field(default_factory=list, max_length=100)
    confidence: float = Field(default=1.0, ge=0, le=1)


class ExtractionIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str = Field(min_length=1, max_length=80)
    message: str = Field(min_length=1, max_length=20_000)
    evidence: list[str] = Field(default_factory=list, max_length=100)


class StateExtractionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chapter_summary: ChapterSummaryExtraction
    scene_summaries: list[SceneSummaryExtraction] = Field(default_factory=list, max_length=500)
    scene_states: list[SceneStateExtraction] = Field(default_factory=list, max_length=500)
    characters: list[EntityExtraction] = Field(default_factory=list, max_length=1_000)
    locations: list[EntityExtraction] = Field(default_factory=list, max_length=1_000)
    items: list[EntityExtraction] = Field(default_factory=list, max_length=1_000)
    organizations: list[EntityExtraction] = Field(default_factory=list, max_length=1_000)
    relationships: list[RelationshipExtraction] = Field(default_factory=list, max_length=2_000)
    timeline_events: list[TimelineExtraction] = Field(default_factory=list, max_length=2_000)
    foreshadows: list[ForeshadowExtraction] = Field(default_factory=list, max_length=2_000)
    conflicts: list[ExtractionIssue] = Field(default_factory=list, max_length=500)
    continuity_warnings: list[ExtractionIssue] = Field(default_factory=list, max_length=500)


class ProposedChangeItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    kind: ChangeKind
    operation: ChangeOperation
    target_id: int | None = Field(default=None, ge=1)
    target_label: str = Field(min_length=1, max_length=240)
    base_revision: int | None = Field(default=None, ge=1)
    before: dict[str, Any] = Field(default_factory=dict)
    proposed: dict[str, Any] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list, max_length=100)
    confidence: float = Field(default=1.0, ge=0, le=1)
    resolution: dict[str, Any] = Field(default_factory=dict)
    conflicts: list[str] = Field(default_factory=list, max_length=100)
    decision: ChangeDecision = "accept"

    @model_validator(mode="after")
    def target_matches_operation(self) -> Self:
        if self.operation == "update" and self.target_id is None:
            raise ValueError("update 变更必须指定 target_id")
        if self.operation == "create" and self.target_id is not None:
            raise ValueError("create 变更不能指定 target_id")
        return self


class ProposedChangeSetCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(ge=1)
    workflow_run_id: int = Field(ge=1)
    node_run_id: int = Field(ge=1)
    node_key: str = Field(min_length=1, max_length=64)
    source_approval_id: int | None = Field(default=None, ge=1)
    chapter_id: int | None = Field(default=None, ge=1)
    scene_id: int | None = Field(default=None, ge=1)
    approved_prose: str | None = Field(default=None, max_length=2_000_000)
    extraction: StateExtractionResult


class ProposedChangeSetEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    items: list[ProposedChangeItem] = Field(max_length=5_000)


class ProposedChangeSetRebase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(ge=1)
    action: Literal["rebase_current", "manual_merge", "abandon", "reextract"]
    items: list[ProposedChangeItem] | None = Field(default=None, max_length=5_000)

    @model_validator(mode="after")
    def require_manual_items(self) -> Self:
        if self.action == "manual_merge" and self.items is None:
            raise ValueError("手工合并必须提交 items")
        if self.action != "manual_merge" and self.items is not None:
            raise ValueError("只有手工合并可以提交 items")
        return self


class ProposedChangeSetRead(BaseModel):
    id: int
    project_id: int
    workflow_run_id: int
    node_run_id: int
    node_key: str
    source_approval_id: int | None
    chapter_id: int | None
    scene_id: int | None
    status: ChangeSetStatus
    extraction: StateExtractionResult
    base_revisions: dict[str, int]
    items: list[ProposedChangeItem]
    conflicts: list[str]
    live_conflicts: list[str]
    changes_hash: str
    superseded_by_id: int | None
    applied_at: datetime | None
    revision: int
    created_at: datetime
    updated_at: datetime


class ProposedChangeSetEditRead(BaseModel):
    change_set: ProposedChangeSetRead
    replacement_approval: ApprovalRequestRead | None = None


class WritebackAuditRead(BaseModel):
    id: int
    project_id: int
    workflow_run_id: int
    change_set_id: int
    approval_request_id: int
    change_set_hash: str
    entries: list[dict[str, Any]]
    created_at: datetime


class WritebackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approval_request_id: int = Field(ge=1)
    expected_change_set_revision: int = Field(ge=1)


class WritebackResultRead(BaseModel):
    status: Literal["applied", "conflicted"]
    change_set: ProposedChangeSetRead
    audit: WritebackAuditRead | None = None
    conflicts: list[str] = Field(default_factory=list)
    applied_item_ids: list[str] = Field(default_factory=list)
