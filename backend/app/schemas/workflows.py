from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


AgentOutputMode = Literal["text", "json"]
WorkflowNodeType = Literal[
    "start",
    "input_mapping",
    "context_retrieval",
    "agent",
    "human_approval",
    "state_extraction",
    "proposed_changes",
    "database_writeback",
    "merge",
    "condition",
    "text_template",
    "data_transform",
    "output",
]
WorkflowRunStatus = Literal[
    "pending",
    "running",
    "waiting_approval",
    "completed",
    "failed",
    "cancelled",
    "interrupted",
]
NodeRunStatus = Literal[
    "pending",
    "ready",
    "running",
    "waiting_approval",
    "completed",
    "failed",
    "skipped",
    "cancelled",
]


class AgentParameters(BaseModel):
    model_config = ConfigDict(extra="forbid")

    temperature: float = Field(default=0.7, ge=0, le=2)
    top_p: float | None = Field(default=None, gt=0, le=1)
    max_tokens: int = Field(default=1024, ge=1, le=1_000_000)
    scenario: Literal["normal", "delay", "timeout", "rate_limit", "error"] = "normal"


class AgentBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_tokens: int | None = Field(default=None, ge=1)
    max_cost: float | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=12, pattern=r"^[A-Z0-9_-]+$")

    @model_validator(mode="after")
    def require_limit(self) -> Self:
        if self.max_tokens is None and self.max_cost is None:
            return self
        return self


class AgentDefinitionBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=160)
    agent_type: str = Field(default="custom", min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_-]*$")
    system_prompt: str = Field(default="", max_length=100_000)
    prompt_template: str = Field(min_length=1, max_length=200_000)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    output_mode: AgentOutputMode = "text"
    model_profile_id: int | None = Field(default=None, ge=1)
    route_id: int | None = Field(default=None, ge=1)
    parameters: AgentParameters = Field(default_factory=AgentParameters)
    required_capabilities: list[str] = Field(default_factory=list, max_length=32)
    allow_degradation: bool = True
    timeout_seconds: float = Field(default=120.0, ge=1, le=3600)
    retry_count: int = Field(default=1, ge=0, le=5)
    budget: AgentBudget = Field(default_factory=AgentBudget)
    enabled: bool = True

    @model_validator(mode="after")
    def require_exact_target(self) -> Self:
        if (self.model_profile_id is None) == (self.route_id is None):
            raise ValueError("Agent 必须且只能选择一个固定模型或 Route")
        normalized: list[str] = []
        for capability in self.required_capabilities:
            value = capability.strip().lower()
            if not value or not value.replace("_", "").isalnum():
                raise ValueError("能力标识只能包含字母、数字和下划线")
            if value not in normalized:
                normalized.append(value)
        self.required_capabilities = normalized
        return self


class AgentDefinitionCreate(AgentDefinitionBase):
    pass


class AgentDefinitionUpdate(AgentDefinitionBase):
    expected_revision: int = Field(ge=1)


class AgentDefinitionRead(AgentDefinitionBase):
    id: int
    version: int
    config_hash: str
    revision: int
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class WorkflowNodeWrite(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    key: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")
    type: WorkflowNodeType
    label: str = Field(min_length=1, max_length=160)
    position_x: float = Field(default=0, ge=-1_000_000, le=1_000_000)
    position_y: float = Field(default=0, ge=-1_000_000, le=1_000_000)
    config: dict[str, Any] = Field(default_factory=dict)


class WorkflowEdgeWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$")
    source: str = Field(min_length=1, max_length=64)
    target: str = Field(min_length=1, max_length=64)
    source_handle: str | None = Field(default=None, max_length=40)
    target_handle: str | None = Field(default=None, max_length=40)


class WorkflowBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=180)
    description: str = Field(default="", max_length=20_000)
    enabled: bool = True
    nodes: list[WorkflowNodeWrite] = Field(min_length=1, max_length=500)
    edges: list[WorkflowEdgeWrite] = Field(default_factory=list, max_length=2_000)


class WorkflowCreate(WorkflowBase):
    pass


class WorkflowUpdate(WorkflowBase):
    expected_revision: int = Field(ge=1)


class WorkflowRead(WorkflowBase):
    id: int
    revision: int
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class WorkflowSummaryRead(BaseModel):
    id: int
    project_id: int
    name: str
    description: str
    enabled: bool
    revision: int
    node_count: int
    edge_count: int
    updated_at: datetime


class WorkflowValidationIssue(BaseModel):
    severity: Literal["error", "warning"] = "error"
    code: str
    message: str
    node_keys: list[str] = Field(default_factory=list)
    path: list[str] = Field(default_factory=list)


class WorkflowValidationRead(BaseModel):
    valid: bool
    issues: list[WorkflowValidationIssue]
    plan_hash: str | None = None
    topological_order: list[str] = Field(default_factory=list)


class WorkflowRunCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input: dict[str, Any] = Field(default_factory=dict)


class WorkflowRunDerive(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["retry_node", "retry_descendants", "clone_from_node"]
    node_key: str = Field(min_length=1, max_length=64)


class NodeRunAttemptRead(BaseModel):
    id: int
    node_run_id: int
    attempt_number: int
    status: str
    input: Any
    output: Any
    partial_output: str
    error: Any
    model_invocation_ids: list[int]
    input_tokens: int
    output_tokens: int
    total_tokens: int
    cost: float | None
    cost_known: bool
    currency: str
    started_at: datetime
    completed_at: datetime | None


class NodeRunRead(BaseModel):
    id: int
    workflow_run_id: int
    node_key: str
    node_type: str
    status: NodeRunStatus
    activated: bool
    input: Any
    output: Any
    error: Any
    warnings: list[str]
    attempt_count: int
    started_at: datetime | None
    completed_at: datetime | None
    attempts: list[NodeRunAttemptRead]


class WorkflowRunRead(BaseModel):
    id: int
    workflow_id: int
    project_id: int
    parent_run_id: int | None
    workflow_revision: int
    status: WorkflowRunStatus
    source_mode: str
    resume_node_key: str | None
    input: dict[str, Any]
    output: Any
    plan_hash: str
    error: Any
    cancel_requested: bool
    event_sequence: int
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime
    nodes: list[NodeRunRead]


class WorkflowRunSummaryRead(BaseModel):
    id: int
    workflow_id: int
    project_id: int
    parent_run_id: int | None
    status: WorkflowRunStatus
    source_mode: str
    event_sequence: int
    started_at: datetime | None
    completed_at: datetime | None
    created_at: datetime


class WorkflowRunEventRead(BaseModel):
    sequence: int
    event: str
    node_key: str | None
    payload: dict[str, Any]
    created_at: datetime


class WorkflowRunSnapshotRead(BaseModel):
    run: WorkflowRunRead
    snapshot: dict[str, Any]
    plan: dict[str, Any]
    events: list[WorkflowRunEventRead]


class WorkflowManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["novel-agent-studio-workflow"] = "novel-agent-studio-workflow"
    version: Literal[1] = 1
    name: str = Field(min_length=1, max_length=180)
    description: str = Field(default="", max_length=20_000)
    agents: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    nodes: list[WorkflowNodeWrite] = Field(min_length=1, max_length=500)
    edges: list[WorkflowEdgeWrite] = Field(default_factory=list, max_length=2_000)


class WorkflowManifestImport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    project_id: int = Field(ge=1)
    manifest: WorkflowManifest
