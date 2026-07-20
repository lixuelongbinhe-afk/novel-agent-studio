import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ModelControlPanel } from "./ModelControlPanel";

const mocks = vi.hoisted(() => ({
  setCapabilityOverride: vi.fn(async () => ({ capabilities: [] })),
  runCapabilityProbe: vi.fn(async () => ({ id: 1, status: "completed" })),
  createModelPricing: vi.fn(async () => ({ id: 1 })),
  createRoute: vi.fn(async (payload: Record<string, unknown>) => ({ id: 2, revision: 1, entries: [], ...payload })),
  createRateLimit: vi.fn(async (payload: Record<string, unknown>) => ({ id: 2, revision: 1, ...payload })),
  createBudget: vi.fn(async (payload: Record<string, unknown>) => ({ id: 2, revision: 1, ...payload })),
  resetProviderHealth: vi.fn(async (_providerId: number) => ({ id: 1, state: "closed" }))
}));

vi.mock("../api/client", () => ({
  api: {
    listProjects: async () => [{ id: 1, title: "雾港回声", summary: "", language: "zh-CN", target_words: 100000, revision: 1, deleted_at: null, created_at: "2026-07-18", updated_at: "2026-07-18" }],
    listRoutes: async () => [],
    listRateLimits: async () => [],
    listBudgets: async () => [],
    listProviderHealth: async () => [{ id: 1, provider_account_id: 1, state: "closed", consecutive_failures: 0, failure_threshold: 3, recovery_timeout_seconds: 30, half_open_in_flight: false, opened_at: null, last_success_at: null, last_failure_at: null, last_latency_ms: 12, last_error_code: null }],
    listInvocations: async () => [],
    modelCapabilities: async () => ({
      model_profile_id: 1,
      provider_account_id: 1,
      generated_at: "2026-07-18T00:00:00Z",
      warnings: [],
      capabilities: [
        { capability: "basic_text", status: "supported", source: "provider_default", reason: "Mock 默认" },
        { capability: "streaming", status: "supported", source: "provider_default", reason: "Mock 默认" }
      ]
    }),
    listCapabilityProbes: async () => [],
    listModelPricing: async () => [],
    setCapabilityOverride: mocks.setCapabilityOverride,
    clearCapabilityOverride: vi.fn(),
    runCapabilityProbe: mocks.runCapabilityProbe,
    createModelPricing: mocks.createModelPricing,
    deleteModelPricing: vi.fn(),
    createRoute: mocks.createRoute,
    updateRoute: vi.fn(),
    deleteRoute: vi.fn(),
    createRateLimit: mocks.createRateLimit,
    updateRateLimit: vi.fn(),
    deleteRateLimit: vi.fn(),
    createBudget: mocks.createBudget,
    updateBudget: vi.fn(),
    deleteBudget: vi.fn(),
    resetProviderHealth: mocks.resetProviderHealth
  }
}));

const providers = [{ id: 1, name: "Mock Provider", provider_type: "mock", credential_env_var: null, base_url: null, enabled: true, revision: 1, deleted_at: null }];
const models = [{ id: 1, provider_account_id: 1, name: "mock-novel-v1", display_name: "Mock Novel", context_window: 8192, tokenizer_name: null, tokenizer_source: null, enabled: true, revision: 1, deleted_at: null }];

function renderPanel() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={queryClient}><ModelControlPanel providers={providers} models={models} /></QueryClientProvider>);
}

describe("ModelControlPanel", () => {
  beforeEach(() => vi.clearAllMocks());

  it("overrides capabilities, runs a probe, saves pricing and a real route", async () => {
    renderPanel();
    expect(await screen.findByText("文本生成")).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("streaming 状态"), { target: { value: "degraded" } });
    await waitFor(() => expect(mocks.setCapabilityOverride).toHaveBeenCalledWith(1, "streaming", "degraded"));

    fireEvent.click(screen.getByRole("button", { name: "基础探测" }));
    await waitFor(() => expect(mocks.runCapabilityProbe).toHaveBeenCalledWith(1, "basic", false));

    fireEvent.click(screen.getByRole("button", { name: "价格" }));
    fireEvent.change(screen.getByLabelText("每次请求"), { target: { value: "0" } });
    fireEvent.change(screen.getByLabelText("输入 / 百万"), { target: { value: "1" } });
    fireEvent.change(screen.getByLabelText("输出 / 百万"), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(mocks.createModelPricing).toHaveBeenCalledTimes(1));

    fireEvent.click(screen.getByRole("button", { name: "Route" }));
    fireEvent.click(screen.getByRole("button", { name: "新建 Route" }));
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "主创作路由" } });
    fireEvent.click(screen.getByRole("checkbox", { name: /Mock Novel/ }));
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(mocks.createRoute).toHaveBeenCalledWith(expect.objectContaining({
      name: "主创作路由",
      strategy: "ordered_fallback",
      entries: [{ model_profile_id: 1, position: 0, enabled: true }]
    })));
  });

  it("persists limit and budget policies and resets provider health", async () => {
    renderPanel();
    await screen.findByText("文本生成");
    fireEvent.click(screen.getByRole("button", { name: "限流与预算" }));
    fireEvent.click(screen.getByRole("button", { name: "限流" }));
    fireEvent.change(screen.getByLabelText("最大并发"), { target: { value: "2" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(mocks.createRateLimit).toHaveBeenCalledWith(expect.objectContaining({ scope_type: "global", scope_key: "*", max_concurrency: 2 })));

    fireEvent.click(screen.getByRole("button", { name: "预算" }));
    fireEvent.change(screen.getByLabelText("Token 上限"), { target: { value: "4096" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(mocks.createBudget).toHaveBeenCalledWith(expect.objectContaining({ scope_type: "per_request", max_tokens: 4096 })));

    fireEvent.click(screen.getByRole("button", { name: "健康与调用" }));
    expect(await screen.findByText("Mock Provider")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "重置" }));
    await waitFor(() => expect(mocks.resetProviderHealth.mock.calls[0]?.[0]).toBe(1));
  });
});
