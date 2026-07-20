export type RecordBase = {
  id: number;
  revision: number;
  deleted_at: string | null;
};

export type Project = RecordBase & {
  title: string;
  summary: string;
  language: string;
  target_words: number;
  created_at: string;
  updated_at: string;
};

export type Volume = RecordBase & {
  project_id: number;
  title: string;
  position: number;
};

export type Chapter = RecordBase & {
  volume_id: number;
  title: string;
  content: string;
  position: number;
  word_count: number;
  updated_at: string;
};

export type Scene = RecordBase & {
  chapter_id: number;
  title: string;
  synopsis: string;
  content: string;
  position: number;
};

export type ChapterVersion = {
  id: number;
  chapter_id: number;
  title: string;
  content: string;
  word_count: number;
  source: string;
  created_at: string;
};

export type EntityKind = "character" | "location" | "item" | "organization" | "concept";

export type StoryEntity = RecordBase & {
  project_id: number;
  name: string;
  kind: EntityKind;
  description: string;
  tags: string[];
};

export type EntityAlias = RecordBase & { entity_id: number; alias: string };

export type EntityRelation = RecordBase & {
  project_id: number;
  source_entity_id: number;
  target_entity_id: number;
  relation_type: string;
  notes: string;
};

export type EntityStateChange = RecordBase & {
  entity_id: number;
  chapter_id: number | null;
  field_name: string;
  old_value: string;
  new_value: string;
  reason: string;
};

export type TimelineEvent = RecordBase & {
  project_id: number;
  chapter_id: number | null;
  label: string;
  event_time: string;
  description: string;
  position: number;
};

export type Foreshadow = RecordBase & {
  project_id: number;
  setup_text: string;
  payoff_text: string;
  status: "open" | "developing" | "resolved" | "abandoned";
  chapter_id: number | null;
};

export type StyleGuide = RecordBase & {
  project_id: number;
  name: string;
  rule_text: string;
  category: string;
};

export type Provider = RecordBase & {
  name: string;
  provider_type: string;
  credential_env_var: string | null;
  base_url: string | null;
  enabled: boolean;
};

export type ProviderPreset = {
  id: number;
  slug: string;
  name: string;
  protocol: string;
  base_url: string;
  default_model: string;
  credential_env_var_hint: string;
  options: Record<string, unknown>;
  revision: number;
};

export type ModelProfile = RecordBase & {
  provider_account_id: number;
  name: string;
  display_name: string;
  context_window: number;
  tokenizer_name: string | null;
  tokenizer_source: "official_tokenizer" | "compatible_tokenizer" | null;
  enabled: boolean;
};

export type CapabilityStatus = "supported" | "unsupported" | "unknown" | "degraded" | "emulated";

export type EffectiveCapability = {
  capability: string;
  status: CapabilityStatus;
  source: "provider_default" | "imported_manifest" | "model_list_api" | "official_metadata" | "automatic_probe" | "manual_override";
  reason: string;
};

export type EffectiveCapabilities = {
  model_profile_id: number;
  provider_account_id: number;
  capabilities: EffectiveCapability[];
  warnings: string[];
  generated_at: string;
};

export type CapabilityProbe = {
  id: number;
  model_profile_id: number;
  level: "basic" | "standard" | "advanced";
  status: string;
  request_count: number;
  max_output_tokens: number;
  estimated_cost: number | null;
  results: Record<string, CapabilityStatus>;
  error_code: string | null;
  completed_at: string | null;
  created_at: string;
};

export type ModelPricing = {
  id: number;
  model_profile_id: number;
  input_per_million: number | null;
  cached_input_per_million: number | null;
  output_per_million: number | null;
  reasoning_per_million: number | null;
  request_fee: number | null;
  tool_call_fee: number | null;
  currency: string;
  effective_from: string;
  effective_to: string | null;
  revision: number;
};

export type ModelPricingInput = Omit<ModelPricing, "id" | "model_profile_id" | "revision">;

export type RouteStrategy = "ordered_fallback" | "lowest_cost" | "lowest_latency" | "healthiest" | "manual_only";

export type ModelRouteEntry = {
  id: number;
  revision: number;
  route_id: number;
  model_profile_id: number;
  position: number;
  enabled: boolean;
};

export type ModelRoute = {
  id: number;
  project_id: number | null;
  name: string;
  strategy: RouteStrategy;
  required_capabilities: string[];
  allow_degradation: boolean;
  enabled: boolean;
  revision: number;
  entries: ModelRouteEntry[];
};

export type ModelRouteInput = Omit<ModelRoute, "id" | "revision" | "entries"> & {
  entries: Array<Pick<ModelRouteEntry, "model_profile_id" | "position" | "enabled">>;
};

export type LimitScope = "global" | "project" | "provider" | "model" | "route" | "workflow";

export type RateLimitPolicy = {
  id: number;
  revision: number;
  scope_type: LimitScope;
  scope_key: string;
  max_concurrency: number | null;
  requests_per_minute: number | null;
  tokens_per_minute: number | null;
  queue_timeout_seconds: number;
  enabled: boolean;
};

export type RateLimitInput = Omit<RateLimitPolicy, "id" | "revision">;

export type BudgetScope = "per_request" | "project_daily" | "route_per_run";

export type BudgetPolicy = {
  id: number;
  revision: number;
  scope_type: BudgetScope;
  scope_key: string;
  max_cost: number | null;
  max_tokens: number | null;
  currency: string;
  enabled: boolean;
};

export type BudgetInput = Omit<BudgetPolicy, "id" | "revision">;

export type ProviderHealth = {
  id: number;
  provider_account_id: number;
  state: "closed" | "open" | "half_open";
  consecutive_failures: number;
  failure_threshold: number;
  recovery_timeout_seconds: number;
  half_open_in_flight: boolean;
  opened_at: string | null;
  last_success_at: string | null;
  last_failure_at: string | null;
  last_latency_ms: number | null;
  last_error_code: string | null;
};

export type ModelInvocation = {
  id: number;
  request_id: string;
  project_id: number | null;
  provider_account_id: number;
  model_profile_id: number;
  route_id: number | null;
  route_run_id: string | null;
  workflow_id: string | null;
  status: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  usage_estimated: boolean;
  token_source: string;
  cost: number | null;
  cost_known: boolean;
  currency: string;
  queue_ms: number;
  latency_ms: number | null;
  fallback_count: number;
  error_code: string | null;
  started_at: string;
  completed_at: string | null;
};

export type ExecutionPreflight = {
  model_profile_id: number;
  provider_account_id: number;
  model_name: string;
  context: {
    input: { tokens: number; estimated: boolean; source: string };
    reserved_output_tokens: number;
    total_tokens: number;
    context_window: number;
    remaining_tokens: number;
    utilization: number;
    level: "ok" | "warning" | "strong_warning" | "blocked";
    blocked: boolean;
    warnings: string[];
  };
  estimated_cost: {
    known: boolean;
    amount: number | null;
    currency: string;
    breakdown: Record<string, number | null>;
    pricing_id: number | null;
    reason: string | null;
  };
  capabilities: EffectiveCapabilities;
  warnings: string[];
};

export type AgentParameters = {
  temperature: number;
  top_p: number | null;
  max_tokens: number;
  scenario: "normal" | "delay" | "timeout" | "rate_limit" | "error";
};

export type AgentBudget = {
  max_tokens: number | null;
  max_cost: number | null;
  currency: string;
};

export type AgentDefinition = RecordBase & {
  project_id: number;
  name: string;
  agent_type: string;
  system_prompt: string;
  prompt_template: string;
  input_schema: Record<string, unknown>;
  output_schema: Record<string, unknown>;
  output_mode: "text" | "json";
  model_profile_id: number | null;
  route_id: number | null;
  parameters: AgentParameters;
  required_capabilities: string[];
  allow_degradation: boolean;
  timeout_seconds: number;
  retry_count: number;
  budget: AgentBudget;
  enabled: boolean;
  version: number;
  config_hash: string;
  created_at: string;
  updated_at: string;
};

export type AgentDefinitionInput = Omit<
  AgentDefinition,
  keyof RecordBase | "version" | "config_hash" | "created_at" | "updated_at"
>;

export type WorkflowNodeType =
  | "start"
  | "input_mapping"
  | "context_retrieval"
  | "agent"
  | "human_approval"
  | "state_extraction"
  | "proposed_changes"
  | "database_writeback"
  | "merge"
  | "condition"
  | "text_template"
  | "data_transform"
  | "output";

export type WorkflowNode = {
  key: string;
  type: WorkflowNodeType;
  label: string;
  position_x: number;
  position_y: number;
  config: Record<string, unknown>;
};

export type WorkflowEdge = {
  key: string;
  source: string;
  target: string;
  source_handle: string | null;
  target_handle: string | null;
};

export type Workflow = RecordBase & {
  project_id: number;
  name: string;
  description: string;
  enabled: boolean;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
  created_at: string;
  updated_at: string;
};

export type WorkflowInput = Omit<
  Workflow,
  keyof RecordBase | "created_at" | "updated_at"
>;

export type WorkflowSummary = {
  id: number;
  project_id: number;
  name: string;
  description: string;
  enabled: boolean;
  revision: number;
  node_count: number;
  edge_count: number;
  updated_at: string;
};

export type WorkflowValidationIssue = {
  severity: "error" | "warning";
  code: string;
  message: string;
  node_keys: string[];
  path: string[];
};

export type WorkflowValidation = {
  valid: boolean;
  issues: WorkflowValidationIssue[];
  plan_hash: string | null;
  topological_order: string[];
};

export type NodeRunAttempt = {
  id: number;
  node_run_id: number;
  attempt_number: number;
  status: string;
  input: unknown;
  output: unknown;
  partial_output: string;
  error: unknown;
  model_invocation_ids: number[];
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost: number | null;
  cost_known: boolean;
  currency: string;
  started_at: string;
  completed_at: string | null;
};

export type NodeRunStatus =
  | "pending"
  | "ready"
  | "running"
  | "waiting_approval"
  | "completed"
  | "failed"
  | "skipped"
  | "cancelled";

export type NodeRun = {
  id: number;
  workflow_run_id: number;
  node_key: string;
  node_type: WorkflowNodeType;
  status: NodeRunStatus;
  activated: boolean;
  input: unknown;
  output: unknown;
  error: unknown;
  warnings: string[];
  attempt_count: number;
  started_at: string | null;
  completed_at: string | null;
  attempts: NodeRunAttempt[];
};

export type WorkflowRunStatus =
  | "pending"
  | "running"
  | "waiting_approval"
  | "completed"
  | "failed"
  | "cancelled"
  | "interrupted";

export type WorkflowRun = {
  id: number;
  workflow_id: number;
  project_id: number;
  parent_run_id: number | null;
  workflow_revision: number;
  status: WorkflowRunStatus;
  source_mode: string;
  resume_node_key: string | null;
  input: Record<string, unknown>;
  output: unknown;
  plan_hash: string;
  error: unknown;
  cancel_requested: boolean;
  event_sequence: number;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  nodes: NodeRun[];
};

export type WorkflowRunSummary = Pick<
  WorkflowRun,
  | "id"
  | "workflow_id"
  | "project_id"
  | "parent_run_id"
  | "status"
  | "source_mode"
  | "event_sequence"
  | "started_at"
  | "completed_at"
  | "created_at"
>;

export type WorkflowRunEvent = {
  sequence: number;
  event: string;
  node_key: string | null;
  payload: Record<string, unknown>;
  created_at: string;
};

export type WorkflowRunSnapshot = {
  run: WorkflowRun;
  snapshot: Record<string, unknown>;
  plan: Record<string, unknown>;
  events: WorkflowRunEvent[];
};

export type ApprovalStatus =
  | "pending"
  | "approved"
  | "changes_requested"
  | "rejected"
  | "expired"
  | "cancelled"
  | "superseded";

export type ApprovalType = "prose" | "change_set" | "generic";

export type ApprovalSnapshot = {
  kind: "approval_snapshot";
  approval_type: ApprovalType;
  value: unknown;
  source: Record<string, unknown>;
};

export type ApprovalRequest = {
  id: number;
  project_id: number;
  workflow_run_id: number;
  node_run_id: number;
  node_key: string;
  approval_type: ApprovalType;
  status: ApprovalStatus;
  title: string;
  instructions: string;
  snapshot: ApprovalSnapshot;
  snapshot_hash: string;
  snapshot_revision: number;
  round_number: number;
  parent_approval_id: number | null;
  superseded_by_id: number | null;
  decision_action: "approve" | "request_changes" | "reject" | "edit" | "cancel" | "expire" | null;
  decision_note: string;
  decision_payload: unknown;
  expires_at: string | null;
  resolved_at: string | null;
  revision: number;
  created_at: string;
  updated_at: string;
};

export type ApprovalDecisionResult = {
  approval: ApprovalRequest;
  replacement: ApprovalRequest | null;
  idempotent_replay: boolean;
};

export type ChangeDecision = "accept" | "reject" | "later";
export type ChangeKind =
  | "chapter_content"
  | "chapter_summary"
  | "scene_synopsis"
  | "scene_state"
  | "entity"
  | "entity_alias"
  | "entity_relation"
  | "entity_state_change"
  | "timeline_event"
  | "foreshadow";

export type ProposedChangeItem = {
  id: string;
  kind: ChangeKind;
  operation: "create" | "update" | "upsert";
  target_id: number | null;
  target_label: string;
  base_revision: number | null;
  before: Record<string, unknown>;
  proposed: Record<string, unknown>;
  evidence: string[];
  confidence: number;
  resolution: Record<string, unknown>;
  conflicts: string[];
  decision: ChangeDecision;
};

export type ProposedChangeSet = {
  id: number;
  project_id: number;
  workflow_run_id: number;
  node_run_id: number;
  node_key: string;
  source_approval_id: number | null;
  chapter_id: number | null;
  scene_id: number | null;
  status: "pending" | "approved" | "applied" | "conflicted" | "cancelled" | "superseded";
  extraction: Record<string, unknown>;
  base_revisions: Record<string, number>;
  items: ProposedChangeItem[];
  conflicts: string[];
  live_conflicts: string[];
  changes_hash: string;
  superseded_by_id: number | null;
  applied_at: string | null;
  revision: number;
  created_at: string;
  updated_at: string;
};

export type ChangeSetEditResult = {
  change_set: ProposedChangeSet;
  replacement_approval: ApprovalRequest | null;
};

export type WritebackAudit = {
  id: number;
  project_id: number;
  workflow_run_id: number;
  change_set_id: number;
  approval_request_id: number;
  change_set_hash: string;
  entries: Array<Record<string, unknown>>;
  created_at: string;
};

export type WorkflowStreamEvent = {
  id: number;
  event: string;
  data: WorkflowRunEvent | WorkflowRunSnapshot;
};

export type WorkflowManifest = {
  format: "novel-agent-studio-workflow";
  version: 1;
  name: string;
  description: string;
  agents: Array<Record<string, unknown>>;
  nodes: WorkflowNode[];
  edges: WorkflowEdge[];
};

export type ContentClassificationValue =
  | "public"
  | "internal"
  | "confidential"
  | "personal information"
  | "sensitive personal information"
  | "unpublished manuscript"
  | "secret";

export type ContextRecordBase = RecordBase & {
  created_at: string;
  updated_at: string;
};

export type ChapterSummary = ContextRecordBase & {
  chapter_id: number;
  summary: string;
  key_events: string[];
  entity_ids: number[];
  token_count: number;
  source: "manual" | "approved_extraction" | "import";
};

export type SceneState = ContextRecordBase & {
  scene_id: number;
  viewpoint_entity_id: number | null;
  location_entity_id: number | null;
  item_entity_ids: number[];
  state: Record<string, unknown>;
  notes: string;
};

export type ChapterEntityLink = ContextRecordBase & {
  chapter_id: number;
  entity_id: number;
  link_type: string;
  relevance: number;
  notes: string;
};

export type ContextPin = ContextRecordBase & {
  project_id: number;
  source_type: string;
  source_id: number;
  label: string;
  content_override: string;
  priority: number;
  required: boolean;
  enabled: boolean;
};

export type ContentClassification = ContextRecordBase & {
  project_id: number;
  source_type: string;
  source_id: number;
  classification: ContentClassificationValue;
  reason: string;
};

export type ContextPolicy = ContextRecordBase & {
  project_id: number;
  name: string;
  token_budget: number;
  recent_chapter_count: number;
  max_results: number;
  min_relevance: number;
  section_priorities: Record<string, number>;
  required_sections: string[];
  allowed_classifications: ContentClassificationValue[];
  use_summaries: boolean;
  enabled: boolean;
};

export type ProviderDataPolicy = ContextRecordBase & {
  provider_account_id: number;
  allowed_classifications: ContentClassificationValue[];
  block_on_required_exclusion: boolean;
  notes: string;
  enabled: boolean;
  inherited_default: boolean;
};

export type ContextTargetProvider = {
  provider_account_id: number;
  provider_name: string;
  provider_type: string;
  model_profile_ids: number[];
  allowed_classifications: ContentClassificationValue[];
  policy_source: "stored" | "local_default" | "remote_default";
};

export type ContextItem = {
  key: string;
  source_type: string;
  source_id: number;
  section: string;
  title: string;
  content: string;
  relevance: number;
  reasons: string[];
  token_estimate: number;
  original_token_estimate: number;
  classification: ContentClassificationValue;
  pinned: boolean;
  priority: number;
  required: boolean;
  locked: boolean;
  included: boolean;
  excluded_reason: string | null;
  truncated: boolean;
  metadata: Record<string, unknown>;
};

export type ContextBuildRequest = {
  project_id: number;
  chapter_id: number | null;
  scene_id: number | null;
  agent_id: number | null;
  model_profile_id: number | null;
  policy_id: number | null;
  workflow_run_id: number | null;
  query: string;
  workflow_input: Record<string, unknown>;
  upstream_outputs: Record<string, unknown>;
  model_context_window: number | null;
  reserved_output_tokens: number;
  token_budget_override: number | null;
  excluded_keys: string[];
  locked_keys: string[];
  priority_overrides: Record<string, number>;
  persist_snapshot: boolean;
};

export type ContextBuild = {
  id: number | null;
  kind: "context_package";
  build_hash: string;
  project_id: number;
  chapter_id: number | null;
  scene_id: number | null;
  agent_id: number | null;
  model_profile_id: number | null;
  policy_id: number | null;
  target_providers: ContextTargetProvider[];
  token_budget: number;
  reserved_output_tokens: number;
  included_tokens: number;
  context_text: string;
  included: ContextItem[];
  excluded: ContextItem[];
  truncations: Array<{
    key: string;
    original_tokens: number;
    final_tokens: number;
    strategy: "summary" | "truncate" | "omit_neighbor";
    reason: string;
  }>;
  boundary: {
    policy_allowed: ContentClassificationValue[];
    provider_allowed: ContentClassificationValue[];
    effective_allowed: ContentClassificationValue[];
    excluded_count: number;
    required_excluded_count: number;
  };
  blocked: boolean;
  conflicts: string[];
  created_at: string | null;
};

export type ChapterSummaryInput = Omit<
  ChapterSummary,
  keyof ContextRecordBase | "token_count"
>;
export type SceneStateInput = Omit<SceneState, keyof ContextRecordBase>;
export type ChapterEntityLinkInput = Omit<ChapterEntityLink, keyof ContextRecordBase>;
export type ContextPinInput = Omit<ContextPin, keyof ContextRecordBase>;
export type ContentClassificationInput = Omit<
  ContentClassification,
  keyof ContextRecordBase
>;
export type ContextPolicyInput = Omit<ContextPolicy, keyof ContextRecordBase>;
export type ProviderDataPolicyInput = Omit<
  ProviderDataPolicy,
  keyof ContextRecordBase | "inherited_default"
>;

export type NormalizedProviderError = {
  code: string;
  message: string;
  retryable: boolean;
  status_code: number | null;
  request_id: string | null;
};

export type ProviderConnection = {
  ok: boolean;
  protocol: string;
  latency_ms: number;
  request_id: string;
  model_count: number;
  error: NormalizedProviderError | null;
};

export type ModelSync = {
  provider_account_id: number;
  discovered: number;
  created: number;
  updated: number;
  models: ModelProfile[];
};

export type ProjectTree = {
  project: Project;
  volumes: Volume[];
  chapters: Chapter[];
  scenes: Scene[];
};

export type TrashItem = RecordBase & {
  label: string;
};

export type ProjectTrash = Record<
  | "projects"
  | "volumes"
  | "chapters"
  | "scenes"
  | "entities"
  | "aliases"
  | "relations"
  | "state_changes"
  | "timeline"
  | "foreshadows"
  | "style_guides",
  TrashItem[]
>;

export type ModelResponse = {
  model: string;
  text: string;
  content: Array<Record<string, unknown>>;
  structured_data: Record<string, unknown> | null;
  tool_calls: Array<{ id: string; name: string; arguments: Record<string, unknown> | string }>;
  finish_reason: string;
  usage: {
    input_tokens: number;
    output_tokens: number;
    total_tokens: number;
    cached_input_tokens?: number;
    reasoning_tokens?: number;
    estimated: boolean;
    source?: string;
  };
  request_id: string;
  error: NormalizedProviderError | null;
  warnings: string[];
  control: Record<string, unknown> | null;
};

export type ModelDebugRequest = {
  provider_account_id: number | null;
  model: string;
  messages: Array<{
    role: "system" | "user" | "assistant" | "tool";
    content: Array<{ type: "text"; text: string }>;
  }>;
  response_format: "text" | "json";
  stream?: boolean;
  temperature?: number;
  max_tokens?: number;
  model_profile_id?: number | null;
  route_id?: number | null;
  manual_model_profile_id?: number | null;
  project_id?: number | null;
  workflow_id?: string | null;
  route_run_id?: string | null;
  required_capabilities?: string[];
  allow_degradation?: boolean;
  max_retries?: number;
};

export type NormalizedStreamEvent = {
  sequence: number;
  event: "start" | "delta" | "tool_call_delta" | "usage" | "warning" | "error" | "done";
  text_delta: string;
  tool_call: { id: string; name: string; arguments: Record<string, unknown> | string } | null;
  usage: ModelResponse["usage"] | null;
  error: NormalizedProviderError | null;
  finish_reason: string | null;
  request_id: string | null;
  warning: string | null;
};

export type CredentialReference = RecordBase & {
  name: string;
  env_var_name: string;
};

export type GenericAuth = {
  type: "none" | "bearer" | "api_key_header" | "custom_header" | "query" | "basic";
  header_name?: string | null;
  query_name?: string | null;
  username?: string | null;
  prefix?: string;
};

export type GenericAdapter = RecordBase & {
  provider_account_id: number;
  credential_reference_id: number | null;
  credential_reference_name: string | null;
  method: "GET" | "POST";
  endpoint: string;
  content_type: string;
  response_mode: "json" | "raw_text";
  stream_format: "sse" | "ndjson" | "chunked_json" | "raw_text";
  security_mode: "public_only" | "local_private";
  query: Record<string, unknown>;
  headers: Record<string, string>;
  request_template: unknown;
  parameter_mapping: Record<string, string>;
  response_mapping: Record<string, unknown>;
  stream_mapping: Record<string, unknown>;
  error_mapping: Record<string, unknown>;
  auth: GenericAuth;
  capability_defaults: Record<string, string>;
  enabled: boolean;
  approved_origin: string | null;
  approval_current: boolean;
  test_current: boolean;
  last_tested_at: string | null;
};

export type GenericAdapterInput = Pick<
  GenericAdapter,
  | "provider_account_id"
  | "credential_reference_id"
  | "method"
  | "endpoint"
  | "content_type"
  | "response_mode"
  | "stream_format"
  | "security_mode"
  | "query"
  | "headers"
  | "request_template"
  | "parameter_mapping"
  | "response_mapping"
  | "stream_mapping"
  | "error_mapping"
  | "auth"
  | "capability_defaults"
  | "enabled"
>;

export type GenericAdapterSetupInput = Omit<GenericAdapterInput, "provider_account_id"> & {
  provider_name: string;
  base_url: string;
};

export type GenericAdapterTest = {
  ok: boolean;
  redacted_request: Record<string, unknown>;
  response: ModelResponse | Record<string, never>;
  error: NormalizedProviderError | null;
};

export type GenericAdapterManifest = {
  schema_version: "1.0";
  kind: "novel-agent-studio.generic-json-http";
  name: string;
  provider_name: string;
  base_url: string;
  config: Omit<GenericAdapterInput, "provider_account_id"> & {
    credential_reference_id: null;
    enabled: false;
  };
};

export type BackupTableCount = { table: string; records: number };

export type BackupManifest = {
  format: "novel-agent-studio-backup";
  schema_version: 1;
  app_version: string;
  migration_revision: string;
  created_at: string;
  data_sha256: string;
  tables: BackupTableCount[];
  includes: string[];
  excludes: string[];
};

export type BackupPreview = {
  archive_sha256: string;
  archive_bytes: number;
  uncompressed_bytes: number;
  manifest: BackupManifest;
  current_tables: BackupTableCount[];
  conflicts: string[];
  warnings: string[];
  secret_findings: string[];
  can_restore: boolean;
};

export type BackupRestoreResult = {
  strategy: "empty_only" | "replace_all";
  archive_sha256: string;
  restored_tables: BackupTableCount[];
  fts_records: number;
  integrity_errors: string[];
  completed_at: string;
};

export type ReleaseStatus = {
  app_version: string;
  environment: string;
  migration_revision: string;
  telemetry_enabled: false;
  frontend_bundled: boolean;
  database_integrity: "ok" | "failed";
  database_bytes: number;
  log_retention_days: number;
  log_files: number;
  max_backup_bytes: number;
};

export type LogCleanupResult = {
  deleted_files: number;
  retained_files: number;
  completed_at: string;
};

export type ReleaseExportKind =
  | "book_markdown"
  | "chapter_markdown"
  | "library_json"
  | "timeline_csv"
  | "foreshadows_json"
  | "agents_json"
  | "workflows_json"
  | "adapters_json"
  | "diagnostics_zip";

export type DownloadedFile = { blob: Blob; filename: string };

export class ApiError extends Error {
  status: number;
  detail: unknown;

  constructor(status: number, message: string, detail: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

const jsonHeaders = { "Content-Type": "application/json" };

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init);
  if (!response.ok) {
    throw await responseError(response);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

async function downloadRequest(url: string, init?: RequestInit): Promise<DownloadedFile> {
  const response = await fetch(url, init);
  if (!response.ok) throw await responseError(response);
  const disposition = response.headers.get("content-disposition") ?? "";
  const encoded = disposition.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
  const quoted = disposition.match(/filename="([^"]+)"/i)?.[1];
  let filename = quoted ?? "NovelAgentStudio-export";
  if (encoded) {
    try {
      filename = decodeURIComponent(encoded);
    } catch {
      filename = encoded;
    }
  }
  return { blob: await response.blob(), filename };
}

export function saveDownloadedFile(file: DownloadedFile): void {
  const url = URL.createObjectURL(file.blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = file.filename;
  anchor.style.display = "none";
  document.body.append(anchor);
  anchor.click();
  anchor.remove();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

async function responseError(response: Response): Promise<ApiError> {
  const body = await response.text();
  let detail: unknown = body;
  try {
    detail = JSON.parse(body);
  } catch {
    // Keep the server text when it is not JSON.
  }
  const message =
    typeof detail === "object" && detail && "detail" in detail
      ? JSON.stringify((detail as { detail: unknown }).detail)
      : body || response.statusText;
  return new ApiError(response.status, message, detail);
}

async function streamRequest(
  url: string,
  payload: unknown,
  onEvent: (event: NormalizedStreamEvent) => void,
  signal?: AbortSignal
): Promise<void> {
  const response = await fetch(url, { ...json("POST", payload), signal });
  if (!response.ok) throw await responseError(response);
  if (!response.body) throw new ApiError(502, "流式响应没有正文", null);

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let buffer = "";

  const consume = (final: boolean) => {
    while (true) {
      const boundary = findSseBoundary(buffer);
      if (!boundary) break;
      const block = buffer.slice(0, boundary.index);
      buffer = buffer.slice(boundary.index + boundary.width);
      emitSseBlock(block, onEvent);
    }
    if (final && buffer.trim()) {
      emitSseBlock(buffer, onEvent);
      buffer = "";
    }
  };

  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      consume(false);
    }
    buffer += decoder.decode();
    consume(true);
  } finally {
    reader.releaseLock();
  }
}

async function streamWorkflowRequest(
  url: string,
  onEvent: (event: WorkflowStreamEvent) => void,
  signal?: AbortSignal,
  lastEventId?: number
): Promise<void> {
  const headers = lastEventId === undefined ? undefined : { "Last-Event-ID": String(lastEventId) };
  const response = await fetch(url, { method: "GET", headers, signal });
  if (!response.ok) throw await responseError(response);
  if (!response.body) throw new ApiError(502, "工作流事件流没有正文", null);
  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8", { fatal: true });
  let buffer = "";
  const consume = (final: boolean) => {
    while (true) {
      const boundary = findSseBoundary(buffer);
      if (!boundary) break;
      const block = buffer.slice(0, boundary.index);
      buffer = buffer.slice(boundary.index + boundary.width);
      emitWorkflowSseBlock(block, onEvent);
    }
    if (final && buffer.trim()) emitWorkflowSseBlock(buffer, onEvent);
  };
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      consume(false);
    }
    buffer += decoder.decode();
    consume(true);
  } finally {
    reader.releaseLock();
  }
}

function emitWorkflowSseBlock(
  block: string,
  onEvent: (event: WorkflowStreamEvent) => void
): void {
  let id = 0;
  let event = "message";
  const data: string[] = [];
  for (const line of block.split(/\r\n|\n|\r/)) {
    if (line.startsWith("id:")) id = Number(line.slice(3).trim());
    else if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
  }
  if (!data.length) return;
  onEvent({ id, event, data: JSON.parse(data.join("\n")) as WorkflowRunEvent | WorkflowRunSnapshot });
}

function findSseBoundary(value: string): { index: number; width: number } | null {
  const candidates = ["\r\n\r\n", "\n\n", "\r\r"]
    .map((separator) => ({ index: value.indexOf(separator), width: separator.length }))
    .filter((item) => item.index >= 0)
    .sort((left, right) => left.index - right.index);
  return candidates[0] ?? null;
}

function emitSseBlock(
  block: string,
  onEvent: (event: NormalizedStreamEvent) => void
): void {
  const data = block
    .split(/\r\n|\n|\r/)
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).replace(/^ /, ""))
    .join("\n");
  if (!data || data === "[DONE]") return;
  onEvent(JSON.parse(data) as NormalizedStreamEvent);
}

function json(method: string, body: unknown): RequestInit {
  return { method, headers: jsonHeaders, body: JSON.stringify(body) };
}

export const api = {
  listProjects: (deleted = false) => request<Project[]>(`/api/projects?deleted=${deleted}`),
  createProject: (payload: Pick<Project, "title" | "summary" | "language" | "target_words">) =>
    request<Project>("/api/projects", json("POST", payload)),
  updateProject: (project: Project, patch: Partial<Project>) =>
    request<Project>(
      `/api/projects/${project.id}`,
      json("PUT", {
        title: patch.title ?? project.title,
        summary: patch.summary ?? project.summary,
        language: patch.language ?? project.language,
        target_words: patch.target_words ?? project.target_words,
        expected_revision: project.revision
      })
    ),
  tree: (projectId: number) => request<ProjectTree>(`/api/projects/${projectId}/tree`),
  projectTrash: (projectId: number) => request<ProjectTrash>(`/api/projects/${projectId}/trash`),
  createVolume: (projectId: number, title: string, position: number) =>
    request<Volume>(`/api/projects/${projectId}/volumes`, json("POST", { title, position })),
  updateVolume: (volume: Volume, patch: Partial<Volume>) =>
    request<Volume>(
      `/api/projects/volumes/${volume.id}`,
      json("PUT", {
        title: patch.title ?? volume.title,
        position: patch.position ?? volume.position,
        expected_revision: volume.revision
      })
    ),
  createChapter: (volumeId: number, title: string, position: number) =>
    request<Chapter>(
      `/api/projects/volumes/${volumeId}/chapters`,
      json("POST", { title, content: "", position })
    ),
  autosaveChapter: (chapter: Chapter, title: string, content: string) =>
    request<Chapter>(
      `/api/projects/chapters/${chapter.id}/autosave`,
      json("PUT", { title, content, expected_revision: chapter.revision })
    ),
  listChapterVersions: (chapterId: number) =>
    request<ChapterVersion[]>(`/api/projects/chapters/${chapterId}/versions`),
  restoreChapterVersion: (chapter: Chapter, versionId: number) =>
    request<Chapter>(
      `/api/projects/chapters/${chapter.id}/versions/${versionId}/restore?expected_revision=${chapter.revision}`,
      { method: "POST" }
    ),
  createScene: (chapterId: number, title: string, position: number) =>
    request<Scene>(
      `/api/projects/chapters/${chapterId}/scenes`,
      json("POST", { title, synopsis: "", content: "", position })
    ),
  updateScene: (scene: Scene, patch: Partial<Scene>) =>
    request<Scene>(
      `/api/projects/scenes/${scene.id}`,
      json("PUT", {
        title: patch.title ?? scene.title,
        synopsis: patch.synopsis ?? scene.synopsis,
        content: patch.content ?? scene.content,
        position: patch.position ?? scene.position,
        expected_revision: scene.revision
      })
    ),
  reorder: <T extends RecordBase & { position: number }>(resource: string, items: T[]) =>
    request<Array<{ id: number; position: number; revision: number }>>(
      `/api/projects/reorder/${resource}`,
      json("POST", {
        items: items.map((item, position) => ({ id: item.id, position, expected_revision: item.revision }))
      })
    ),
  listEntities: (projectId: number) => request<StoryEntity[]>(`/api/projects/${projectId}/entities`),
  createEntity: (projectId: number, payload: Pick<StoryEntity, "name" | "kind" | "description" | "tags">) =>
    request<StoryEntity>(`/api/projects/${projectId}/entities`, json("POST", payload)),
  updateEntity: (entity: StoryEntity, patch: Partial<StoryEntity>) =>
    request<StoryEntity>(
      `/api/projects/entities/${entity.id}`,
      json("PUT", {
        name: patch.name ?? entity.name,
        kind: patch.kind ?? entity.kind,
        description: patch.description ?? entity.description,
        tags: patch.tags ?? entity.tags,
        expected_revision: entity.revision
      })
    ),
  listAliases: (projectId: number) => request<EntityAlias[]>(`/api/projects/${projectId}/aliases`),
  createAlias: (entityId: number, alias: string) =>
    request<EntityAlias>(`/api/projects/entities/${entityId}/aliases`, json("POST", { alias })),
  updateAlias: (item: EntityAlias, alias: string) =>
    request<EntityAlias>(
      `/api/projects/aliases/${item.id}`,
      json("PUT", { alias, expected_revision: item.revision })
    ),
  listRelations: (projectId: number) => request<EntityRelation[]>(`/api/projects/${projectId}/relations`),
  createRelation: (
    projectId: number,
    payload: Pick<EntityRelation, "source_entity_id" | "target_entity_id" | "relation_type" | "notes">
  ) => request<EntityRelation>(`/api/projects/${projectId}/relations`, json("POST", payload)),
  updateRelation: (relation: EntityRelation, patch: Partial<EntityRelation>) =>
    request<EntityRelation>(
      `/api/projects/relations/${relation.id}`,
      json("PUT", { ...relation, ...patch, expected_revision: relation.revision })
    ),
  listStateChanges: (projectId: number) =>
    request<EntityStateChange[]>(`/api/projects/${projectId}/state-changes`),
  createStateChange: (
    projectId: number,
    payload: Pick<EntityStateChange, "entity_id" | "chapter_id" | "field_name" | "old_value" | "new_value" | "reason">
  ) => request<EntityStateChange>(`/api/projects/${projectId}/state-changes`, json("POST", payload)),
  updateStateChange: (item: EntityStateChange, patch: Partial<EntityStateChange>) =>
    request<EntityStateChange>(
      `/api/projects/state-changes/${item.id}`,
      json("PUT", { ...item, ...patch, expected_revision: item.revision })
    ),
  listTimeline: (projectId: number) => request<TimelineEvent[]>(`/api/projects/${projectId}/timeline`),
  createTimelineEvent: (
    projectId: number,
    payload: Pick<TimelineEvent, "chapter_id" | "label" | "event_time" | "description" | "position">
  ) => request<TimelineEvent>(`/api/projects/${projectId}/timeline`, json("POST", payload)),
  updateTimelineEvent: (event: TimelineEvent, patch: Partial<TimelineEvent>) =>
    request<TimelineEvent>(
      `/api/projects/timeline/${event.id}`,
      json("PUT", { ...event, ...patch, expected_revision: event.revision })
    ),
  listForeshadows: (projectId: number) =>
    request<Foreshadow[]>(`/api/projects/${projectId}/foreshadows`),
  createForeshadow: (
    projectId: number,
    payload: Pick<Foreshadow, "setup_text" | "payoff_text" | "status" | "chapter_id">
  ) => request<Foreshadow>(`/api/projects/${projectId}/foreshadows`, json("POST", payload)),
  updateForeshadow: (item: Foreshadow, patch: Partial<Foreshadow>) =>
    request<Foreshadow>(
      `/api/projects/foreshadows/${item.id}`,
      json("PUT", { ...item, ...patch, expected_revision: item.revision })
    ),
  listStyleGuides: (projectId: number) =>
    request<StyleGuide[]>(`/api/projects/${projectId}/style-guides`),
  createStyleGuide: (
    projectId: number,
    payload: Pick<StyleGuide, "name" | "rule_text" | "category">
  ) => request<StyleGuide>(`/api/projects/${projectId}/style-guides`, json("POST", payload)),
  updateStyleGuide: (item: StyleGuide, patch: Partial<StyleGuide>) =>
    request<StyleGuide>(
      `/api/projects/style-guides/${item.id}`,
      json("PUT", { ...item, ...patch, expected_revision: item.revision })
    ),
  deleteRecord: (resource: string, record: RecordBase) =>
    request<void>(
      `/api/projects/records/${resource}/${record.id}?expected_revision=${record.revision}`,
      { method: "DELETE" }
    ),
  restoreRecord: (resource: string, record: RecordBase) =>
    request<void>(
      `/api/projects/records/${resource}/${record.id}/restore?expected_revision=${record.revision}`,
      { method: "POST" }
    ),
  listProviders: () => request<Provider[]>("/api/model-center/providers"),
  listPresets: () => request<ProviderPreset[]>("/api/model-center/presets"),
  createPreset: (payload: Omit<ProviderPreset, "id" | "revision">) =>
    request<ProviderPreset>("/api/model-center/presets", json("POST", payload)),
  updatePreset: (preset: ProviderPreset, patch: Partial<ProviderPreset>) =>
    request<ProviderPreset>(
      `/api/model-center/presets/${preset.id}`,
      json("PUT", {
        slug: patch.slug ?? preset.slug,
        name: patch.name ?? preset.name,
        protocol: patch.protocol ?? preset.protocol,
        base_url: patch.base_url ?? preset.base_url,
        default_model: patch.default_model ?? preset.default_model,
        credential_env_var_hint:
          patch.credential_env_var_hint ?? preset.credential_env_var_hint,
        options: patch.options ?? preset.options,
        expected_revision: preset.revision
      })
    ),
  createProvider: (payload: Omit<Provider, keyof RecordBase | "enabled"> & { enabled?: boolean }) =>
    request<Provider>("/api/model-center/providers", json("POST", payload)),
  updateProvider: (provider: Provider, patch: Partial<Provider>) =>
    request<Provider>(
      `/api/model-center/providers/${provider.id}`,
      json("PUT", {
        name: patch.name ?? provider.name,
        provider_type: patch.provider_type ?? provider.provider_type,
        credential_env_var: patch.credential_env_var ?? provider.credential_env_var,
        base_url: patch.base_url ?? provider.base_url,
        enabled: patch.enabled ?? provider.enabled,
        expected_revision: provider.revision
      })
    ),
  deleteProvider: (provider: Provider) =>
    request<void>(
      `/api/model-center/providers/${provider.id}?expected_revision=${provider.revision}`,
      { method: "DELETE" }
    ),
  testProvider: (providerId: number) =>
    request<ProviderConnection>(`/api/model-center/providers/${providerId}/test`, {
      method: "POST"
    }),
  syncProviderModels: (providerId: number) =>
    request<ModelSync>(`/api/model-center/providers/${providerId}/sync`, { method: "POST" }),
  listModels: () => request<ModelProfile[]>("/api/model-center/models"),
  createModel: (payload: Omit<ModelProfile, keyof RecordBase | "enabled"> & { enabled?: boolean }) =>
    request<ModelProfile>("/api/model-center/models", json("POST", payload)),
  updateModel: (model: ModelProfile, patch: Partial<ModelProfile>) =>
    request<ModelProfile>(
      `/api/model-center/models/${model.id}`,
      json("PUT", {
        display_name: patch.display_name ?? model.display_name,
        context_window: patch.context_window ?? model.context_window,
        tokenizer_name:
          "tokenizer_name" in patch ? patch.tokenizer_name ?? null : model.tokenizer_name,
        tokenizer_source:
          "tokenizer_source" in patch ? patch.tokenizer_source ?? null : model.tokenizer_source,
        enabled: patch.enabled ?? model.enabled,
        expected_revision: model.revision
      })
    ),
  deleteModel: (model: ModelProfile) =>
    request<void>(
      `/api/model-center/models/${model.id}?expected_revision=${model.revision}`,
      { method: "DELETE" }
    ),
  modelCapabilities: (modelId: number) =>
    request<EffectiveCapabilities>(`/api/model-center/models/${modelId}/capabilities`),
  setCapabilityOverride: (modelId: number, capability: string, status: CapabilityStatus) =>
    request<EffectiveCapabilities>(
      `/api/model-center/models/${modelId}/capabilities/${encodeURIComponent(capability)}`,
      json("PUT", { status })
    ),
  clearCapabilityOverride: (modelId: number, capability: string) =>
    request<EffectiveCapabilities>(
      `/api/model-center/models/${modelId}/capabilities/${encodeURIComponent(capability)}`,
      { method: "DELETE" }
    ),
  listCapabilityProbes: (modelId: number) =>
    request<CapabilityProbe[]>(`/api/model-center/models/${modelId}/probes`),
  runCapabilityProbe: (
    modelId: number,
    level: CapabilityProbe["level"],
    confirmAdvanced = false
  ) =>
    request<CapabilityProbe>(
      `/api/model-center/models/${modelId}/probes`,
      json("POST", {
        level,
        confirm_advanced: confirmAdvanced,
        max_estimated_cost: 0.05
      })
    ),
  listModelPricing: (modelId: number) =>
    request<ModelPricing[]>(`/api/model-center/models/${modelId}/pricing`),
  createModelPricing: (modelId: number, payload: ModelPricingInput) =>
    request<ModelPricing>(
      `/api/model-center/models/${modelId}/pricing`,
      json("POST", payload)
    ),
  deleteModelPricing: (pricing: ModelPricing) =>
    request<void>(
      `/api/model-center/pricing/${pricing.id}?expected_revision=${pricing.revision}`,
      { method: "DELETE" }
    ),
  listRoutes: (projectId?: number) =>
    request<ModelRoute[]>(
      `/api/model-center/routes${projectId ? `?project_id=${projectId}` : ""}`
    ),
  createRoute: (payload: ModelRouteInput) =>
    request<ModelRoute>("/api/model-center/routes", json("POST", payload)),
  updateRoute: (route: ModelRoute, payload: ModelRouteInput) =>
    request<ModelRoute>(
      `/api/model-center/routes/${route.id}`,
      json("PUT", { ...payload, expected_revision: route.revision })
    ),
  deleteRoute: (route: ModelRoute) =>
    request<void>(
      `/api/model-center/routes/${route.id}?expected_revision=${route.revision}`,
      { method: "DELETE" }
    ),
  listRateLimits: () => request<RateLimitPolicy[]>("/api/model-center/rate-limits"),
  createRateLimit: (payload: RateLimitInput) =>
    request<RateLimitPolicy>("/api/model-center/rate-limits", json("POST", payload)),
  updateRateLimit: (policy: RateLimitPolicy, payload: RateLimitInput) =>
    request<RateLimitPolicy>(
      `/api/model-center/rate-limits/${policy.id}`,
      json("PUT", { ...payload, expected_revision: policy.revision })
    ),
  deleteRateLimit: (policy: RateLimitPolicy) =>
    request<void>(
      `/api/model-center/rate-limits/${policy.id}?expected_revision=${policy.revision}`,
      { method: "DELETE" }
    ),
  listBudgets: () => request<BudgetPolicy[]>("/api/model-center/budgets"),
  createBudget: (payload: BudgetInput) =>
    request<BudgetPolicy>("/api/model-center/budgets", json("POST", payload)),
  updateBudget: (policy: BudgetPolicy, payload: BudgetInput) =>
    request<BudgetPolicy>(
      `/api/model-center/budgets/${policy.id}`,
      json("PUT", { ...payload, expected_revision: policy.revision })
    ),
  deleteBudget: (policy: BudgetPolicy) =>
    request<void>(
      `/api/model-center/budgets/${policy.id}?expected_revision=${policy.revision}`,
      { method: "DELETE" }
    ),
  listProviderHealth: () => request<ProviderHealth[]>("/api/model-center/health"),
  resetProviderHealth: (providerId: number) =>
    request<ProviderHealth>(`/api/model-center/health/${providerId}/reset`, {
      method: "POST"
    }),
  listInvocations: (limit = 100) =>
    request<ModelInvocation[]>(`/api/model-center/invocations?limit=${limit}`),
  preflightModel: (payload: ModelDebugRequest) =>
    request<ExecutionPreflight>("/api/model-center/preflight", json("POST", payload)),
  debugModel: (payload: ModelDebugRequest) =>
    request<ModelResponse>("/api/model-center/debug", json("POST", payload)),
  streamModel: (
    payload: ModelDebugRequest,
    onEvent: (event: NormalizedStreamEvent) => void,
    signal?: AbortSignal
  ) => streamRequest("/api/model-center/debug/stream", { ...payload, stream: true }, onEvent, signal),
  listAgents: (projectId: number) => request<AgentDefinition[]>(`/api/agents?project_id=${projectId}`),
  createAgent: (payload: AgentDefinitionInput) =>
    request<AgentDefinition>("/api/agents", json("POST", payload)),
  updateAgent: (agent: AgentDefinition, payload: AgentDefinitionInput) =>
    request<AgentDefinition>(
      `/api/agents/${agent.id}`,
      json("PUT", { ...payload, expected_revision: agent.revision })
    ),
  deleteAgent: (agent: AgentDefinition) =>
    request<void>(`/api/agents/${agent.id}?expected_revision=${agent.revision}`, { method: "DELETE" }),
  listWorkflows: (projectId: number) =>
    request<WorkflowSummary[]>(`/api/workflows?project_id=${projectId}`),
  readWorkflow: (workflowId: number) => request<Workflow>(`/api/workflows/${workflowId}`),
  createWorkflow: (payload: WorkflowInput) =>
    request<Workflow>("/api/workflows", json("POST", payload)),
  updateWorkflow: (workflow: Workflow, payload: WorkflowInput) =>
    request<Workflow>(
      `/api/workflows/${workflow.id}`,
      json("PUT", { ...payload, expected_revision: workflow.revision })
    ),
  deleteWorkflow: (workflow: WorkflowSummary | Workflow) =>
    request<void>(
      `/api/workflows/${workflow.id}?expected_revision=${workflow.revision}`,
      { method: "DELETE" }
    ),
  validateWorkflow: (workflowId: number) =>
    request<WorkflowValidation>(`/api/workflows/${workflowId}/validate`, { method: "POST" }),
  exportWorkflow: (workflowId: number) =>
    request<WorkflowManifest>(`/api/workflows/${workflowId}/manifest`),
  importWorkflow: (projectId: number, manifest: WorkflowManifest) =>
    request<Workflow>("/api/workflows/import", json("POST", { project_id: projectId, manifest })),
  startWorkflowRun: (workflowId: number, input: Record<string, unknown>) =>
    request<WorkflowRun>(`/api/workflows/${workflowId}/runs`, json("POST", { input })),
  listWorkflowRuns: (
    projectId: number,
    workflowId?: number,
    limit = 50,
    beforeId?: number
  ) => {
    const params = new URLSearchParams({
      project_id: String(projectId),
      limit: String(limit)
    });
    if (workflowId) params.set("workflow_id", String(workflowId));
    if (beforeId) params.set("before_id", String(beforeId));
    return request<WorkflowRunSummary[]>(`/api/workflow-runs?${params.toString()}`);
  },
  readWorkflowRun: (runId: number) => request<WorkflowRun>(`/api/workflow-runs/${runId}`),
  readWorkflowSnapshot: (runId: number) =>
    request<WorkflowRunSnapshot>(`/api/workflow-runs/${runId}/snapshot`),
  cancelWorkflowRun: (runId: number) =>
    request<WorkflowRun>(`/api/workflow-runs/${runId}/cancel`, { method: "POST" }),
  deriveWorkflowRun: (
    runId: number,
    mode: "retry_node" | "retry_descendants" | "clone_from_node",
    nodeKey: string
  ) => request<WorkflowRun>(`/api/workflow-runs/${runId}/derive`, json("POST", { mode, node_key: nodeKey })),
  streamWorkflowEvents: (
    runId: number,
    onEvent: (event: WorkflowStreamEvent) => void,
    options?: { signal?: AbortSignal; lastEventId?: number; snapshot?: boolean }
  ) => streamWorkflowRequest(
    `/api/workflow-runs/${runId}/events${options?.snapshot ? "?snapshot=true" : ""}`,
    onEvent,
    options?.signal,
    options?.lastEventId
  ),
  listApprovalRequests: (projectId: number, approvalStatus?: ApprovalStatus) =>
    request<ApprovalRequest[]>(
      `/api/approvals/requests?project_id=${projectId}${approvalStatus ? `&status=${approvalStatus}` : ""}`
    ),
  readApprovalRequest: (approvalId: number) =>
    request<ApprovalRequest>(`/api/approvals/requests/${approvalId}`),
  decideApprovalRequest: (
    approval: ApprovalRequest,
    payload: {
      action: "approve" | "request_changes" | "reject" | "edit";
      idempotency_key: string;
      note?: string;
      edited_value?: unknown;
    }
  ) => request<ApprovalDecisionResult>(
    `/api/approvals/requests/${approval.id}/decision`,
    json("POST", { ...payload, expected_revision: approval.revision })
  ),
  listChangeSets: (projectId: number) =>
    request<ProposedChangeSet[]>(`/api/approvals/change-sets?project_id=${projectId}`),
  readChangeSet: (changeSetId: number) =>
    request<ProposedChangeSet>(`/api/approvals/change-sets/${changeSetId}`),
  editChangeSet: (changeSet: ProposedChangeSet, items: ProposedChangeItem[]) =>
    request<ChangeSetEditResult>(
      `/api/approvals/change-sets/${changeSet.id}/items`,
      json("PUT", { expected_revision: changeSet.revision, items })
    ),
  resolveChangeSet: (
    changeSet: ProposedChangeSet,
    action: "rebase_current" | "manual_merge" | "abandon" | "reextract",
    items?: ProposedChangeItem[]
  ) => request<ChangeSetEditResult>(
    `/api/approvals/change-sets/${changeSet.id}/resolve-conflict`,
    json("POST", {
      expected_revision: changeSet.revision,
      action,
      ...(action === "manual_merge" ? { items: items ?? changeSet.items } : {})
    })
  ),
  listWritebackAudits: (projectId: number) =>
    request<WritebackAudit[]>(`/api/approvals/audits?project_id=${projectId}`),
  listChapterSummaries: (projectId: number) =>
    request<ChapterSummary[]>(`/api/context/chapter-summaries?project_id=${projectId}`),
  createChapterSummary: (payload: ChapterSummaryInput) =>
    request<ChapterSummary>("/api/context/chapter-summaries", json("POST", payload)),
  updateChapterSummary: (item: ChapterSummary, payload: ChapterSummaryInput) =>
    request<ChapterSummary>(
      `/api/context/chapter-summaries/${item.id}`,
      json("PUT", { ...payload, expected_revision: item.revision })
    ),
  listSceneStates: (projectId: number) =>
    request<SceneState[]>(`/api/context/scene-states?project_id=${projectId}`),
  createSceneState: (payload: SceneStateInput) =>
    request<SceneState>("/api/context/scene-states", json("POST", payload)),
  updateSceneState: (item: SceneState, payload: SceneStateInput) =>
    request<SceneState>(
      `/api/context/scene-states/${item.id}`,
      json("PUT", { ...payload, expected_revision: item.revision })
    ),
  listChapterEntityLinks: (projectId: number) =>
    request<ChapterEntityLink[]>(`/api/context/chapter-entity-links?project_id=${projectId}`),
  createChapterEntityLink: (payload: ChapterEntityLinkInput) =>
    request<ChapterEntityLink>("/api/context/chapter-entity-links", json("POST", payload)),
  updateChapterEntityLink: (item: ChapterEntityLink, payload: ChapterEntityLinkInput) =>
    request<ChapterEntityLink>(
      `/api/context/chapter-entity-links/${item.id}`,
      json("PUT", { ...payload, expected_revision: item.revision })
    ),
  listContextPins: (projectId: number) =>
    request<ContextPin[]>(`/api/context/pins?project_id=${projectId}`),
  createContextPin: (payload: ContextPinInput) =>
    request<ContextPin>("/api/context/pins", json("POST", payload)),
  updateContextPin: (item: ContextPin, payload: ContextPinInput) =>
    request<ContextPin>(
      `/api/context/pins/${item.id}`,
      json("PUT", { ...payload, expected_revision: item.revision })
    ),
  listContentClassifications: (projectId: number) =>
    request<ContentClassification[]>(`/api/context/classifications?project_id=${projectId}`),
  createContentClassification: (payload: ContentClassificationInput) =>
    request<ContentClassification>("/api/context/classifications", json("POST", payload)),
  updateContentClassification: (
    item: ContentClassification,
    payload: ContentClassificationInput
  ) =>
    request<ContentClassification>(
      `/api/context/classifications/${item.id}`,
      json("PUT", { ...payload, expected_revision: item.revision })
    ),
  listContextPolicies: (projectId: number) =>
    request<ContextPolicy[]>(`/api/context/policies?project_id=${projectId}`),
  createContextPolicy: (payload: ContextPolicyInput) =>
    request<ContextPolicy>("/api/context/policies", json("POST", payload)),
  updateContextPolicy: (item: ContextPolicy, payload: ContextPolicyInput) =>
    request<ContextPolicy>(
      `/api/context/policies/${item.id}`,
      json("PUT", { ...payload, expected_revision: item.revision })
    ),
  listProviderDataPolicies: () =>
    request<ProviderDataPolicy[]>("/api/context/provider-policies"),
  updateProviderDataPolicy: (
    item: ProviderDataPolicy,
    payload: ProviderDataPolicyInput
  ) =>
    request<ProviderDataPolicy>(
      `/api/context/provider-policies/${item.provider_account_id}`,
      json("PUT", { ...payload, expected_revision: item.revision })
    ),
  deleteContextRecord: (resource: string, item: RecordBase) =>
    request<void>(
      `/api/context/records/${encodeURIComponent(resource)}/${item.id}?expected_revision=${item.revision}`,
      { method: "DELETE" }
    ),
  buildContext: (payload: ContextBuildRequest) =>
    request<ContextBuild>("/api/context/builds", json("POST", payload)),
  listContextBuilds: (projectId: number, limit = 100) =>
    request<ContextBuild[]>(`/api/context/builds?project_id=${projectId}&limit=${limit}`),
  readContextBuild: (buildId: number) =>
    request<ContextBuild>(`/api/context/builds/${buildId}`),
  rebuildContextIndex: (projectId: number) =>
    request<{ project_id: number; indexed_records: number; rebuilt: boolean }>(
      `/api/context/reindex/${projectId}`,
      { method: "POST" }
    ),
  listCredentials: () => request<CredentialReference[]>("/api/custom-api/credentials"),
  createCredential: (payload: Pick<CredentialReference, "name" | "env_var_name">) =>
    request<CredentialReference>("/api/custom-api/credentials", json("POST", payload)),
  updateCredential: (item: CredentialReference, patch: Partial<CredentialReference>) =>
    request<CredentialReference>(
      `/api/custom-api/credentials/${item.id}`,
      json("PUT", {
        name: patch.name ?? item.name,
        env_var_name: patch.env_var_name ?? item.env_var_name,
        expected_revision: item.revision
      })
    ),
  deleteCredential: (item: CredentialReference) =>
    request<void>(
      `/api/custom-api/credentials/${item.id}?expected_revision=${item.revision}`,
      { method: "DELETE" }
    ),
  listCustomAdapters: () => request<GenericAdapter[]>("/api/custom-api/adapters"),
  createCustomAdapter: (payload: GenericAdapterInput) =>
    request<GenericAdapter>("/api/custom-api/adapters", json("POST", payload)),
  setupCustomAdapter: (payload: GenericAdapterSetupInput) =>
    request<GenericAdapter>("/api/custom-api/adapters/setup", json("POST", payload)),
  updateCustomAdapter: (adapter: GenericAdapter, patch: Partial<GenericAdapterInput>) => {
    const payload: Omit<GenericAdapterInput, "provider_account_id"> & { expected_revision: number } = {
      credential_reference_id:
        "credential_reference_id" in patch
          ? patch.credential_reference_id ?? null
          : adapter.credential_reference_id,
      method: patch.method ?? adapter.method,
      endpoint: patch.endpoint ?? adapter.endpoint,
      content_type: patch.content_type ?? adapter.content_type,
      response_mode: patch.response_mode ?? adapter.response_mode,
      stream_format: patch.stream_format ?? adapter.stream_format,
      security_mode: patch.security_mode ?? adapter.security_mode,
      query: patch.query ?? adapter.query,
      headers: patch.headers ?? adapter.headers,
      request_template: patch.request_template ?? adapter.request_template,
      parameter_mapping: patch.parameter_mapping ?? adapter.parameter_mapping,
      response_mapping: patch.response_mapping ?? adapter.response_mapping,
      stream_mapping: patch.stream_mapping ?? adapter.stream_mapping,
      error_mapping: patch.error_mapping ?? adapter.error_mapping,
      auth: patch.auth ?? adapter.auth,
      capability_defaults: patch.capability_defaults ?? adapter.capability_defaults,
      enabled: patch.enabled ?? adapter.enabled,
      expected_revision: adapter.revision
    };
    return request<GenericAdapter>(`/api/custom-api/adapters/${adapter.id}`, json("PUT", payload));
  },
  deleteCustomAdapter: (adapter: GenericAdapter) =>
    request<void>(
      `/api/custom-api/adapters/${adapter.id}?expected_revision=${adapter.revision}`,
      { method: "DELETE" }
    ),
  approveCustomOrigin: (adapter: GenericAdapter) =>
    request<GenericAdapter>(
      `/api/custom-api/adapters/${adapter.id}/approve-origin`,
      json("POST", { expected_revision: adapter.revision })
    ),
  testCustomAdapter: (adapterId: number, payload: ModelDebugRequest) =>
    request<GenericAdapterTest>(
      `/api/custom-api/adapters/${adapterId}/test`,
      json("POST", { request: payload })
    ),
  streamCustomAdapter: (
    adapterId: number,
    payload: ModelDebugRequest,
    onEvent: (event: NormalizedStreamEvent) => void,
    signal?: AbortSignal
  ) => streamRequest(`/api/custom-api/adapters/${adapterId}/debug/stream`, { request: { ...payload, stream: true } }, onEvent, signal),
  exportCustomManifest: (adapterId: number) =>
    request<GenericAdapterManifest>(`/api/custom-api/adapters/${adapterId}/manifest`),
  importCustomManifest: (manifest: GenericAdapterManifest) =>
    request<{ provider_id: number; adapter: GenericAdapter }>(
      "/api/custom-api/manifests/import",
      json("POST", manifest)
    ),
  releaseStatus: () => request<ReleaseStatus>("/api/release/status"),
  downloadBackup: () => downloadRequest("/api/release/backup"),
  previewBackup: (file: File) =>
    request<BackupPreview>("/api/release/backup/preview", {
      method: "POST",
      headers: { "Content-Type": "application/zip" },
      body: file
    }),
  restoreBackup: (
    file: File,
    strategy: "empty_only" | "replace_all",
    expectedSha256: string
  ) =>
    request<BackupRestoreResult>(
      `/api/release/backup/restore?strategy=${strategy}&expected_sha256=${encodeURIComponent(expectedSha256)}`,
      {
        method: "POST",
        headers: { "Content-Type": "application/zip" },
        body: file
      }
    ),
  downloadReleaseExport: (
    kind: ReleaseExportKind,
    projectId?: number,
    chapterId?: number
  ) => {
    const query = new URLSearchParams();
    if (projectId) query.set("project_id", String(projectId));
    if (chapterId) query.set("chapter_id", String(chapterId));
    return downloadRequest(`/api/release/exports/${kind}${query.size ? `?${query}` : ""}`);
  },
  cleanupLogs: () => request<LogCleanupResult>("/api/release/logs/cleanup", { method: "POST" }),
  deleteLogs: () => request<LogCleanupResult>("/api/release/logs", { method: "DELETE" })
};
