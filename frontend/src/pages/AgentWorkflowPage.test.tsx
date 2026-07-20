import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AgentWorkflowPage } from "./AgentWorkflowPage";

const mocks = vi.hoisted(() => {
  const state = { runStatus: "completed" };
  return {
    state,
    createAgent: vi.fn(async (payload: Record<string, unknown>) => ({ id: 2, revision: 1, version: 1, config_hash: "agent-hash", deleted_at: null, created_at: "2026-07-18T00:00:00Z", updated_at: "2026-07-18T00:00:00Z", ...payload })),
    updateWorkflow: vi.fn(async (workflow: Record<string, unknown>, payload: Record<string, unknown>) => ({ ...workflow, ...payload, revision: Number(workflow.revision) + 1 })),
    validateWorkflow: vi.fn(async () => ({ valid: true, issues: [], plan_hash: "plan-hash", topological_order: ["start", "agent_1", "output"] })),
    startWorkflowRun: vi.fn(),
    cancelWorkflowRun: vi.fn(),
    deriveWorkflowRun: vi.fn(),
    streamWorkflowEvents: vi.fn(async () => undefined)
  };
});

const nodes = [
  { key: "start", type: "start", label: "Start", position_x: 50, position_y: 100, config: {} },
  { key: "agent_1", type: "agent", label: "章节初稿", position_x: 300, position_y: 100, config: { agent_id: 1 } },
  { key: "output", type: "output", label: "Output", position_x: 550, position_y: 100, config: {} }
];
const edges = [
  { key: "e1", source: "start", target: "agent_1", source_handle: null, target_handle: null },
  { key: "e2", source: "agent_1", target: "output", source_handle: null, target_handle: null }
];
const workflow = {
  id: 1,
  project_id: 1,
  name: "章节流水线",
  description: "原始说明",
  enabled: true,
  nodes,
  edges,
  revision: 1,
  deleted_at: null,
  created_at: "2026-07-18T00:00:00Z",
  updated_at: "2026-07-18T00:00:00Z"
};
const agent = {
  id: 1,
  project_id: 1,
  name: "章节初稿",
  agent_type: "draft",
  system_prompt: "你是小说作者",
  prompt_template: "任务：{input.task}",
  input_schema: {},
  output_schema: {},
  output_mode: "text",
  model_profile_id: 1,
  route_id: null,
  parameters: { temperature: 0.7, top_p: null, max_tokens: 1024, scenario: "normal" },
  required_capabilities: [],
  allow_degradation: true,
  timeout_seconds: 120,
  retry_count: 1,
  budget: { max_tokens: null, max_cost: null, currency: "USD" },
  enabled: true,
  version: 1,
  config_hash: "agent-hash",
  revision: 1,
  deleted_at: null,
  created_at: "2026-07-18T00:00:00Z",
  updated_at: "2026-07-18T00:00:00Z"
};

function runRecord(status = mocks.state.runStatus) {
  return {
    id: 9,
    workflow_id: 1,
    project_id: 1,
    parent_run_id: null,
    workflow_revision: 1,
    status,
    source_mode: "original",
    resume_node_key: null,
    input: { task: "写一段" },
    output: status === "completed" ? "完成文本" : null,
    plan_hash: "plan-hash",
    error: null,
    cancel_requested: false,
    event_sequence: 4,
    started_at: "2026-07-18T00:00:00Z",
    completed_at: status === "running" ? null : "2026-07-18T00:00:01Z",
    created_at: "2026-07-18T00:00:00Z",
    nodes: [
      { id: 1, workflow_run_id: 9, node_key: "start", node_type: "start", status: "completed", activated: true, input: { task: "写一段" }, output: { task: "写一段" }, error: null, warnings: [], attempt_count: 1, started_at: "2026-07-18T00:00:00Z", completed_at: "2026-07-18T00:00:00Z", attempts: [] },
      { id: 2, workflow_run_id: 9, node_key: "agent_1", node_type: "agent", status: status === "running" ? "running" : "completed", activated: true, input: { workflow_input: { task: "写一段" } }, output: status === "completed" ? "完成文本" : null, error: null, warnings: [], attempt_count: 1, started_at: "2026-07-18T00:00:00Z", completed_at: status === "running" ? null : "2026-07-18T00:00:01Z", attempts: [{ id: 3, node_run_id: 2, attempt_number: 1, status, input: {}, output: status === "completed" ? "完成文本" : null, partial_output: status === "running" ? "流式片段" : "完成文本", error: null, model_invocation_ids: [5], input_tokens: 5, output_tokens: 8, total_tokens: 13, cost: 0, cost_known: true, currency: "USD", started_at: "2026-07-18T00:00:00Z", completed_at: status === "running" ? null : "2026-07-18T00:00:01Z" }] },
      { id: 3, workflow_run_id: 9, node_key: "output", node_type: "output", status: status === "completed" ? "completed" : "pending", activated: status === "completed", input: null, output: status === "completed" ? "完成文本" : null, error: null, warnings: [], attempt_count: status === "completed" ? 1 : 0, started_at: null, completed_at: null, attempts: [] }
    ]
  };
}

vi.mock("./WorkflowCanvas", () => ({
  WorkflowCanvas: ({ value, readOnly }: { value: { nodes: Array<{ key: string }> }; readOnly?: boolean }) => <div data-testid={readOnly ? "run-canvas" : "workflow-canvas"}>{value.nodes.map((node) => node.key).join(",")}</div>
}));

vi.mock("../api/client", () => ({
  ApiError: class ApiError extends Error {},
  api: {
    listProjects: async () => [{ id: 1, title: "雾港回声", summary: "", language: "zh-CN", target_words: 100000, revision: 1, deleted_at: null, created_at: "2026-07-18", updated_at: "2026-07-18" }],
    listAgents: async () => [agent],
    listModels: async () => [{ id: 1, provider_account_id: 1, name: "mock-novel-v1", display_name: "Mock Novel", context_window: 8192, tokenizer_name: null, tokenizer_source: null, enabled: true, revision: 1, deleted_at: null }],
    listRoutes: async () => [],
    listWorkflows: async () => [{ id: 1, project_id: 1, name: "章节流水线", description: "原始说明", enabled: true, revision: 1, node_count: 3, edge_count: 2, updated_at: "2026-07-18T00:00:00Z" }],
    readWorkflow: async () => workflow,
    createAgent: mocks.createAgent,
    updateAgent: vi.fn(),
    deleteAgent: vi.fn(),
    createWorkflow: vi.fn(),
    updateWorkflow: mocks.updateWorkflow,
    deleteWorkflow: vi.fn(),
    validateWorkflow: mocks.validateWorkflow,
    exportWorkflow: vi.fn(),
    importWorkflow: vi.fn(),
    startWorkflowRun: mocks.startWorkflowRun,
    listWorkflowRuns: async () => [{ id: 9, workflow_id: 1, project_id: 1, parent_run_id: null, status: mocks.state.runStatus, source_mode: "original", event_sequence: 4, started_at: "2026-07-18T00:00:00Z", completed_at: mocks.state.runStatus === "running" ? null : "2026-07-18T00:00:01Z", created_at: "2026-07-18T00:00:00Z" }],
    readWorkflowRun: async () => runRecord(),
    readWorkflowSnapshot: async () => ({ run: runRecord(), snapshot: { workflow }, plan: {}, events: [{ sequence: 1, event: "run_started", node_key: null, payload: {}, created_at: "2026-07-18T00:00:00Z" }] }),
    cancelWorkflowRun: mocks.cancelWorkflowRun,
    deriveWorkflowRun: mocks.deriveWorkflowRun,
    streamWorkflowEvents: mocks.streamWorkflowEvents
  }
}));

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={queryClient}><AgentWorkflowPage /></QueryClientProvider>);
}

describe("AgentWorkflowPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.state.runStatus = "completed";
    mocks.startWorkflowRun.mockImplementation(async () => runRecord("completed"));
    mocks.cancelWorkflowRun.mockImplementation(async () => {
      mocks.state.runStatus = "cancelled";
      return runRecord("cancelled");
    });
    mocks.deriveWorkflowRun.mockImplementation(async () => ({ ...runRecord("completed"), id: 10, parent_run_id: 9, source_mode: "retry_node" }));
  });

  it("creates a complete Agent, saves and validates a workflow, then starts a real run", async () => {
    renderPage();
    expect(await screen.findByText("章节初稿")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "新建 Agent" }));
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "对白润色" } });
    fireEvent.change(screen.getByLabelText(/^类型/), { target: { value: "dialogue" } });
    fireEvent.change(screen.getByLabelText("任务提示词模板"), { target: { value: "润色：{input.task}" } });
    fireEvent.change(screen.getByLabelText(/必需能力/), { target: { value: "basic_text, streaming" } });
    fireEvent.click(screen.getByRole("button", { name: "保存 Agent" }));
    await waitFor(() => expect(mocks.createAgent).toHaveBeenCalledTimes(1));
    expect(mocks.createAgent.mock.calls[0][0]).toMatchObject({
      name: "对白润色",
      agent_type: "dialogue",
      model_profile_id: 1,
      route_id: null,
      required_capabilities: ["basic_text", "streaming"],
      parameters: { max_tokens: 1024 },
      budget: { max_tokens: null, max_cost: null, currency: "USD" }
    });

    fireEvent.click(screen.getByRole("button", { name: "工作流" }));
    expect(await screen.findByTestId("workflow-canvas")).toHaveTextContent("start,agent_1,output");
    fireEvent.change(screen.getByLabelText("工作流描述"), { target: { value: "已修改说明" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(mocks.updateWorkflow).toHaveBeenCalledTimes(1));
    expect(mocks.updateWorkflow.mock.calls[0][1]).toMatchObject({ description: "已修改说明" });

    fireEvent.click(screen.getByRole("button", { name: "校验" }));
    expect(await screen.findByText("校验通过")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "运行" }));
    expect(await screen.findByRole("dialog")).toHaveTextContent("运行 章节流水线");
    fireEvent.click(screen.getByRole("button", { name: "开始运行" }));
    await waitFor(() => expect(mocks.startWorkflowRun).toHaveBeenCalledWith(1, expect.objectContaining({ task: expect.any(String) })));
    expect(await screen.findByText(/运行 #9/)).toBeInTheDocument();
  });

  it("cancels an active run and derives a retry from the selected node", async () => {
    mocks.state.runStatus = "running";
    renderPage();
    await screen.findByText("章节初稿");
    fireEvent.click(screen.getByRole("button", { name: "运行记录" }));
    expect(await screen.findByText(/运行 #9/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "取消运行" }));
    await waitFor(() => expect(mocks.cancelWorkflowRun.mock.calls[0]?.[0]).toBe(9));
    await waitFor(() => expect(screen.getByRole("button", { name: "仅重试此节点" })).toBeEnabled());
    fireEvent.click(screen.getByRole("button", { name: "仅重试此节点" }));
    await waitFor(() => expect(mocks.deriveWorkflowRun).toHaveBeenCalledWith(9, "retry_node", "start"));
  });
});
