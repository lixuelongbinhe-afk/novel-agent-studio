import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ModelCenterPage } from "./ModelCenterPage";

const mocks = vi.hoisted(() => ({
  createProvider: vi.fn(async (payload: Record<string, unknown>) => ({ id: 2, revision: 1, deleted_at: null, ...payload })),
  updatePreset: vi.fn(async (preset: Record<string, unknown>, patch: Record<string, unknown>) => ({ ...preset, ...patch, revision: 2 })),
  testProvider: vi.fn(async () => ({ ok: true, protocol: "mock", latency_ms: 12, request_id: "connection-test", model_count: 1, error: null })),
  syncProviderModels: vi.fn(async () => ({ provider_account_id: 1, discovered: 1, created: 0, updated: 1, models: [] })),
  preflightModel: vi.fn(async (_payload: unknown) => ({
    model_profile_id: 1,
    provider_account_id: 1,
    model_name: "mock-novel-v1",
    context: {
      input: { tokens: 128, estimated: true, source: "local_approximation" },
      reserved_output_tokens: 512,
      total_tokens: 2048,
      context_window: 8192,
      remaining_tokens: 6144,
      utilization: 0.25,
      level: "ok",
      blocked: false,
      warnings: []
    },
    estimated_cost: {
      known: false,
      amount: null,
      currency: "USD",
      breakdown: {},
      pricing_id: null,
      reason: "pricing_unknown"
    },
    capabilities: { model_profile_id: 1, provider_account_id: 1, capabilities: [], warnings: [], generated_at: "2026-07-18T00:00:00Z" },
    warnings: []
  })),
  debugModel: vi.fn(async (_payload: unknown) => ({
    model: "mock-novel-v1",
    text: "Mock 调试输出",
    content: [{ type: "text", text: "Mock 调试输出" }],
    structured_data: null,
    tool_calls: [],
    finish_reason: "stop",
    usage: { input_tokens: 4, output_tokens: 6, total_tokens: 10, estimated: true },
    request_id: "mock-test",
    error: null,
    warnings: []
  })),
  streamModel: vi.fn(async (_payload: unknown, onEvent: (event: Record<string, unknown>) => void) => {
    onEvent({ sequence: 1, event: "start", text_delta: "", request_id: "stream-test", tool_call: null, usage: null, error: null, finish_reason: null, warning: null });
    onEvent({ sequence: 2, event: "delta", text_delta: "流式输出", request_id: "stream-test", tool_call: null, usage: null, error: null, finish_reason: null, warning: null });
    onEvent({ sequence: 3, event: "usage", text_delta: "", request_id: "stream-test", tool_call: null, usage: { input_tokens: 2, output_tokens: 3, total_tokens: 5, estimated: true }, error: null, finish_reason: null, warning: null });
    onEvent({ sequence: 4, event: "done", text_delta: "", request_id: "stream-test", tool_call: null, usage: null, error: null, finish_reason: "stop", warning: null });
  })
}));

vi.mock("../api/client", () => ({
  api: {
    listProviders: async () => [{ id: 1, name: "Mock Provider", provider_type: "mock", credential_env_var: null, base_url: null, enabled: true, revision: 1, deleted_at: null }],
    listModels: async () => [{ id: 1, provider_account_id: 1, name: "mock-novel-v1", display_name: "Mock Novel", context_window: 8192, enabled: true, revision: 1, deleted_at: null }],
    listPresets: async () => [{ id: 1, slug: "deepseek", name: "DeepSeek", protocol: "openai_chat", base_url: "https://api.deepseek.com/v1", default_model: "deepseek-chat", credential_env_var_hint: "DEEPSEEK_API_KEY", options: {}, revision: 1 }],
    listRoutes: async () => [],
    createProvider: mocks.createProvider,
    updateProvider: vi.fn(),
    deleteProvider: vi.fn(),
    testProvider: mocks.testProvider,
    syncProviderModels: mocks.syncProviderModels,
    createModel: vi.fn(),
    updateModel: vi.fn(),
    deleteModel: vi.fn(),
    createPreset: vi.fn(),
    updatePreset: mocks.updatePreset,
    preflightModel: mocks.preflightModel,
    debugModel: mocks.debugModel,
    streamModel: mocks.streamModel
  }
}));

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={queryClient}><ModelCenterPage /></QueryClientProvider>);
}

describe("ModelCenterPage", () => {
  it("manages a provider, tests and syncs it, then debugs the selected model", async () => {
    renderPage();
    expect(await screen.findByText("Mock Provider")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "添加 Provider" }));
    fireEvent.change(screen.getByLabelText("Provider 预设"), { target: { value: "deepseek" } });
    expect(screen.getByLabelText("Base URL")).toHaveValue("https://api.deepseek.com/v1");
    fireEvent.change(screen.getByLabelText("显示名称"), { target: { value: "本地测试" } });
    fireEvent.change(screen.getByLabelText(/API Key 环境变量名/), { target: { value: "test_key" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(mocks.createProvider).toHaveBeenCalledTimes(1));
    expect(mocks.createProvider.mock.calls[0][0]).toMatchObject({
      provider_type: "openai_chat",
      credential_env_var: "TEST_KEY"
    });

    fireEvent.click(screen.getByRole("button", { name: "测试 Mock Provider" }));
    expect(await screen.findByText("12 ms")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "同步 Mock Provider 的模型" }));
    expect(await screen.findByText("发现 1，新增 0，更新 1")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /调试台/ }));
    await waitFor(() => expect(screen.getByLabelText("模型")).toHaveValue("mock-novel-v1"));
    fireEvent.click(screen.getByRole("button", { name: "调用预检" }));
    expect(await screen.findByText("25%")).toBeInTheDocument();
    expect(screen.getByText("本地近似")).toBeInTheDocument();
    expect(mocks.preflightModel.mock.calls[0][0]).toMatchObject({ model_profile_id: 1, model: "mock-novel-v1" });

    fireEvent.click(screen.getByRole("button", { name: "普通响应" }));
    expect(await screen.findByText("Mock 调试输出")).toBeInTheDocument();
    expect(mocks.debugModel.mock.calls[0][0]).toMatchObject({ provider_account_id: 1, model: "mock-novel-v1" });

    fireEvent.click(screen.getByRole("button", { name: "流式响应" }));
    expect(await screen.findByText("流式输出")).toBeInTheDocument();
    expect(screen.getByText(/request_id: stream-test/)).toBeInTheDocument();
  });

  it("edits an existing Provider preset", async () => {
    renderPage();
    await screen.findByText("Mock Provider");
    const manageButton = screen.getByRole("button", { name: "管理预设" });
    await waitFor(() => expect(manageButton).toBeEnabled());
    fireEvent.click(manageButton);
    fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "https://gateway.example/v1" } });
    fireEvent.click(screen.getByRole("button", { name: "保存预设" }));
    await waitFor(() => expect(mocks.updatePreset).toHaveBeenCalledTimes(1));
    expect(mocks.updatePreset.mock.calls[0][1]).toMatchObject({ base_url: "https://gateway.example/v1" });
  });
});
