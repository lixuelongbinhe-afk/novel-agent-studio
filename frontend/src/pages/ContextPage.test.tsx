import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ContextPage } from "./ContextPage";

const mocks = vi.hoisted(() => ({
  buildContext: vi.fn(),
  createContextPin: vi.fn(),
  createContentClassification: vi.fn()
}));

const timestamp = "2026-07-18T00:00:00Z";
const agent = {
  id: 1,
  project_id: 1,
  name: "连贯性 Agent",
  agent_type: "continuity",
  system_prompt: "",
  prompt_template: "{value}",
  input_schema: {},
  output_schema: {},
  output_mode: "text",
  model_profile_id: 1,
  route_id: null,
  parameters: { temperature: 0.7, top_p: null, max_tokens: 512, scenario: "normal" },
  required_capabilities: [],
  allow_degradation: true,
  timeout_seconds: 120,
  retry_count: 1,
  budget: { max_tokens: null, max_cost: null, currency: "USD" },
  enabled: true,
  version: 1,
  config_hash: "hash",
  revision: 1,
  deleted_at: null,
  created_at: timestamp,
  updated_at: timestamp
};

function contextItem(overrides: Record<string, unknown>) {
  return {
    key: "scene:1:current_scene",
    source_type: "scene",
    source_id: 1,
    section: "current_scene",
    title: "当前场景 · 钟楼会面",
    content: "钟楼下的铜钥匙闪了一下。",
    relevance: 1,
    reasons: ["用户选择的当前场景"],
    token_estimate: 18,
    original_token_estimate: 18,
    classification: "unpublished manuscript",
    pinned: false,
    priority: 95,
    required: true,
    locked: false,
    included: true,
    excluded_reason: null,
    truncated: false,
    metadata: {},
    ...overrides
  };
}

function buildResult(payload?: { excluded_keys?: string[] }) {
  const scene = contextItem({});
  const characterKey = "entity:2:character_state";
  const excludedByUser = payload?.excluded_keys?.includes(characterKey);
  const character = contextItem({
    key: characterKey,
    source_type: "entity",
    source_id: 2,
    section: "character_state",
    title: "林雾 · 当前状态",
    content: "林雾携带铜钥匙。",
    relevance: 0.9,
    token_estimate: 12,
    original_token_estimate: 12,
    priority: 80,
    required: false,
    included: !excludedByUser,
    excluded_reason: excludedByUser ? "temporary_exclusion" : null
  });
  return {
    id: 7,
    kind: "context_package",
    build_hash: "1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
    project_id: 1,
    chapter_id: 1,
    scene_id: 1,
    agent_id: 1,
    model_profile_id: 1,
    policy_id: 1,
    target_providers: [{ provider_account_id: 1, provider_name: "Mock Provider", provider_type: "mock", model_profile_ids: [1], allowed_classifications: ["public", "internal", "unpublished manuscript"], policy_source: "stored" }],
    token_budget: 6000,
    reserved_output_tokens: 1024,
    included_tokens: excludedByUser ? 18 : 30,
    context_text: excludedByUser
      ? "## 当前场景 · 钟楼会面\n钟楼下的铜钥匙闪了一下。"
      : "## 当前场景 · 钟楼会面\n钟楼下的铜钥匙闪了一下。\n\n## 林雾 · 当前状态\n林雾携带铜钥匙。",
    included: excludedByUser ? [scene] : [scene, character],
    excluded: excludedByUser
      ? [character]
      : [contextItem({ key: "foreshadow:2:foreshadow", source_type: "foreshadow", source_id: 2, section: "foreshadow", title: "秘密伏笔", content: "保密", classification: "secret", included: false, required: false, excluded_reason: "provider_data_boundary" })],
    truncations: [],
    boundary: { policy_allowed: ["public", "internal", "unpublished manuscript"], provider_allowed: ["public", "internal", "unpublished manuscript"], effective_allowed: ["public", "internal", "unpublished manuscript"], excluded_count: excludedByUser ? 0 : 1, required_excluded_count: 0 },
    blocked: false,
    conflicts: [],
    created_at: timestamp
  };
}

vi.mock("./ContextMemoryPanel", () => ({ ContextMemoryPanel: () => <div data-testid="memory-panel">记忆面板</div> }));
vi.mock("./ContextPolicyPanel", () => ({ ContextPolicyPanel: () => <div data-testid="policy-panel">策略面板</div> }));

vi.mock("../api/client", () => ({
  api: {
    listProjects: async () => [{ id: 1, title: "雾港回声", summary: "", language: "zh-CN", target_words: 100000, revision: 1, deleted_at: null, created_at: timestamp, updated_at: timestamp }],
    tree: async () => ({
      project: { id: 1, title: "雾港回声", summary: "", language: "zh-CN", target_words: 100000, revision: 1, deleted_at: null, created_at: timestamp, updated_at: timestamp },
      volumes: [{ id: 1, project_id: 1, title: "第一卷", position: 1, revision: 1, deleted_at: null }],
      chapters: [{ id: 1, volume_id: 1, title: "第二章 返港", content: "", position: 1, word_count: 0, revision: 1, deleted_at: null, updated_at: timestamp }],
      scenes: [{ id: 1, chapter_id: 1, title: "钟楼会面", synopsis: "", content: "", position: 1, revision: 1, deleted_at: null }]
    }),
    listAgents: async () => [agent],
    listContextPolicies: async () => [{ id: 1, project_id: 1, name: "默认长篇写作", token_budget: 6000, recent_chapter_count: 3, max_results: 80, min_relevance: 0.2, section_priorities: {}, required_sections: ["user_task"], allowed_classifications: ["public", "internal", "unpublished manuscript"], use_summaries: true, enabled: true, revision: 1, deleted_at: null, created_at: timestamp, updated_at: timestamp }],
    listContextPins: async () => [],
    listContentClassifications: async () => [],
    buildContext: mocks.buildContext,
    createContextPin: mocks.createContextPin,
    updateContextPin: vi.fn(),
    createContentClassification: mocks.createContentClassification,
    updateContentClassification: vi.fn()
  }
}));

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={queryClient}><ContextPage /></QueryClientProvider>);
}

describe("ContextPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.buildContext.mockImplementation(async (payload: { excluded_keys?: string[] }) => buildResult(payload));
    mocks.createContextPin.mockImplementation(async (payload: Record<string, unknown>) => ({ id: 1, revision: 1, deleted_at: null, created_at: timestamp, updated_at: timestamp, ...payload }));
    mocks.createContentClassification.mockImplementation(async (payload: Record<string, unknown>) => ({ id: 1, revision: 1, deleted_at: null, created_at: timestamp, updated_at: timestamp, ...payload }));
  });

  it("builds a real preview and applies transient exclusion, pin and classification controls", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { name: "上下文记忆" })).toBeInTheDocument();
    const retrieve = (await screen.findAllByRole("button", { name: "检索上下文" }))[0];
    await waitFor(() => expect(retrieve).toBeEnabled());
    fireEvent.click(retrieve);
    await waitFor(() => expect(mocks.buildContext).toHaveBeenCalledTimes(1));
    expect(mocks.buildContext.mock.calls[0][0]).toMatchObject({
      project_id: 1,
      chapter_id: 1,
      scene_id: 1,
      agent_id: 1,
      policy_id: 1,
      token_budget_override: 6000,
      persist_snapshot: true
    });
    expect(await screen.findByText("实际上下文")).toBeInTheDocument();
    expect(screen.getByText("钟楼下的铜钥匙闪了一下。", { selector: "pre" })).toBeInTheDocument();
    expect(screen.getByText("Mock Provider")).toBeInTheDocument();
    expect(screen.getByText("未发布稿件", { selector: "span.classification" })).toBeInTheDocument();

    expect(screen.getByRole("button", { name: "临时排除" })).toBeDisabled();
    fireEvent.click(screen.getByRole("button", { name: /林雾 · 当前状态/ }));
    expect(screen.getByRole("button", { name: "临时排除" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "临时排除" }));
    await waitFor(() => expect(mocks.buildContext).toHaveBeenCalledTimes(2));
    expect(mocks.buildContext.mock.calls[1][0].excluded_keys).toContain("entity:2:character_state");

    fireEvent.click(screen.getByRole("button", { name: "恢复" }));
    await waitFor(() => expect(mocks.buildContext).toHaveBeenCalledTimes(3));
    fireEvent.click(screen.getByRole("button", { name: "Pin" }));
    await waitFor(() => expect(mocks.createContextPin).toHaveBeenCalledWith(expect.objectContaining({ source_type: "entity", source_id: 2, priority: 80 })));

    fireEvent.change(screen.getByLabelText("数据分类"), { target: { value: "confidential" } });
    await waitFor(() => expect(mocks.createContentClassification).toHaveBeenCalledWith(expect.objectContaining({ classification: "confidential", source_type: "entity" })));
  });

  it("opens the memory and policy workspaces", async () => {
    renderPage();
    await screen.findByRole("heading", { name: "上下文记忆" });
    fireEvent.click(screen.getByRole("button", { name: "小说记忆" }));
    expect(screen.getByTestId("memory-panel")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "策略与边界" }));
    expect(screen.getByTestId("policy-panel")).toBeInTheDocument();
  });
});
