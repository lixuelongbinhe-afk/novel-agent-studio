import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CustomApiPage } from "./CustomApiPage";

const mocks = vi.hoisted(() => ({
  adapter: {
    id: 1,
    provider_account_id: 1,
    credential_reference_id: 1,
    credential_reference_name: "测试凭据",
    method: "POST" as const,
    endpoint: "/chat",
    content_type: "application/json",
    response_mode: "json" as const,
    stream_format: "sse" as const,
    security_mode: "local_private" as const,
    query: {},
    headers: {},
    request_template: { model: { $var: "model" } },
    parameter_mapping: {},
    response_mapping: { text: "$.text" },
    stream_mapping: { text_delta: "$.delta", done: "$.done" },
    error_mapping: { message: "$.error.message" },
    auth: { type: "bearer" as const },
    capability_defaults: {},
    enabled: false,
    approved_origin: null,
    approval_current: false,
    test_current: false,
    last_tested_at: null,
    revision: 1,
    deleted_at: null
  },
  approve: vi.fn(async (item: Record<string, unknown>) => ({ ...item, approved_origin: "http://127.0.0.1:8020", approval_current: true, revision: 2 })),
  test: vi.fn(async () => ({
    ok: true,
    redacted_request: { method: "POST", headers: { Authorization: "[REDACTED]" }, body: { prompt: "测试" } },
    response: { model: "custom-model", text: "自定义 API 输出", usage: { total_tokens: 8 } },
    error: null
  })),
  stream: vi.fn(async (_id: number, _payload: unknown, onEvent: (event: Record<string, unknown>) => void) => {
    onEvent({ event: "delta", text_delta: "流式自定义输出" });
    onEvent({ event: "done", text_delta: "" });
  }),
  setupAdapter: vi.fn(async (payload: Record<string, unknown>) => ({ id: 2, provider_account_id: 2, ...payload })),
  createCredential: vi.fn(async (payload: Record<string, unknown>) => ({ id: 2, revision: 1, deleted_at: null, ...payload })),
  updateCredential: vi.fn(async (item: Record<string, unknown>, payload: Record<string, unknown>) => ({ ...item, ...payload, revision: 2 })),
  updateAdapter: vi.fn(async (item: Record<string, unknown>, patch: Record<string, unknown>) => ({ ...item, ...patch, revision: 2 })),
  deleteAdapter: vi.fn(async (_item: Record<string, unknown>) => undefined),
  exportManifest: vi.fn(async (_id: number) => ({ manifest_version: 1, provider: { name: "本地自定义 API" }, adapter: {} })),
  importManifest: vi.fn(async (_manifest: Record<string, unknown>) => ({ adapter: { id: 1 }, provider: { id: 1 } })),
  deleteCredential: vi.fn(async (_item: Record<string, unknown>) => undefined)
}));

vi.mock("../api/client", () => ({
  api: {
    listProviders: async () => [{ id: 1, name: "本地自定义 API", provider_type: "generic_json_http", credential_env_var: null, base_url: "http://127.0.0.1:8020", enabled: true, revision: 1, deleted_at: null }],
    listCustomAdapters: async () => [mocks.adapter],
    listCredentials: async () => [{ id: 1, name: "测试凭据", env_var_name: "CUSTOM_TEST_KEY", revision: 1, deleted_at: null }],
    approveCustomOrigin: mocks.approve,
    testCustomAdapter: mocks.test,
    streamCustomAdapter: mocks.stream,
    updateCustomAdapter: mocks.updateAdapter,
    deleteCustomAdapter: mocks.deleteAdapter,
    exportCustomManifest: mocks.exportManifest,
    importCustomManifest: mocks.importManifest,
    createProvider: vi.fn(),
    updateProvider: vi.fn(),
    createCustomAdapter: vi.fn(),
    setupCustomAdapter: mocks.setupAdapter,
    createCredential: mocks.createCredential,
    updateCredential: mocks.updateCredential,
    deleteCredential: mocks.deleteCredential
  }
}));

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={queryClient}><CustomApiPage /></QueryClientProvider>);
}

describe("CustomApiPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.adapter.enabled = false;
    mocks.adapter.test_current = false;
  });

  afterEach(() => vi.restoreAllMocks());

  it("approves the exact Origin and shows a redacted real test plus stream output", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { name: "本地自定义 API" })).toBeInTheDocument();
    expect(screen.getByText("http://127.0.0.1:8020", { exact: true })).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "审批 Origin" }));
    await waitFor(() => expect(mocks.approve).toHaveBeenCalledTimes(1));
    fireEvent.click(screen.getByRole("button", { name: "测试普通响应" }));
    expect(await screen.findByText(/REDACTED/)).toBeInTheDocument();
    expect(screen.getByText(/自定义 API 输出/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("sk-");
    fireEvent.click(screen.getByRole("button", { name: "测试流式" }));
    expect(await screen.findByText("流式自定义输出")).toBeInTheDocument();
  });

  it("creates a Provider and disabled custom adapter from the real configuration form", async () => {
    renderPage();
    await screen.findByRole("heading", { name: "本地自定义 API" });
    fireEvent.click(screen.getByRole("button", { name: "新建适配器" }));
    fireEvent.change(screen.getByLabelText("Provider 名称"), { target: { value: "新 API" } });
    fireEvent.change(screen.getByLabelText("Base URL"), { target: { value: "https://api.example.com/v1" } });
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));
    await waitFor(() => expect(mocks.setupAdapter).toHaveBeenCalledTimes(1));
    expect(mocks.setupAdapter.mock.calls[0][0]).toMatchObject({ provider_name: "新 API", base_url: "https://api.example.com/v1", endpoint: "/chat", enabled: false });
  });

  it("creates a credential reference using an environment variable name only", async () => {
    renderPage();
    await screen.findByRole("heading", { name: "本地自定义 API" });
    fireEvent.click(screen.getByRole("button", { name: "凭据引用" }));
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "新凭据" } });
    fireEvent.change(screen.getByLabelText("环境变量名"), { target: { value: "custom_new_key" } });
    fireEvent.click(screen.getByRole("button", { name: "添加" }));
    await waitFor(() => expect(mocks.createCredential).toHaveBeenCalledWith({ name: "新凭据", env_var_name: "CUSTOM_NEW_KEY" }));
  });

  it("edits an existing credential reference and still stores only the environment variable name", async () => {
    renderPage();
    await screen.findByRole("heading", { name: "本地自定义 API" });
    fireEvent.click(screen.getByRole("button", { name: "凭据引用" }));
    fireEvent.click(screen.getByTitle("编辑凭据引用"));
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "更新凭据" } });
    fireEvent.change(screen.getByLabelText("环境变量名"), { target: { value: "updated_key" } });
    fireEvent.click(screen.getByRole("button", { name: "更新" }));
    await waitFor(() => expect(mocks.updateCredential).toHaveBeenCalled());
    expect(mocks.updateCredential.mock.calls[0][1]).toEqual({ name: "更新凭据", env_var_name: "UPDATED_KEY" });
  });

  it("enables, exports, and deletes a tested adapter", async () => {
    mocks.adapter.test_current = true;
    vi.spyOn(window, "confirm").mockReturnValue(true);
    Object.defineProperty(URL, "createObjectURL", { configurable: true, value: vi.fn(() => "blob:test-manifest") });
    Object.defineProperty(URL, "revokeObjectURL", { configurable: true, value: vi.fn() });
    vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    renderPage();
    await screen.findByRole("heading", { name: "本地自定义 API" });

    fireEvent.click(screen.getByRole("button", { name: "启用" }));
    await waitFor(() => expect(mocks.updateAdapter).toHaveBeenCalledWith(mocks.adapter, { enabled: true }));

    fireEvent.click(screen.getByTitle("导出 Manifest"));
    await waitFor(() => expect(mocks.exportManifest).toHaveBeenCalledWith(1));

    fireEvent.click(screen.getByTitle("删除适配器"));
    await waitFor(() => expect(mocks.deleteAdapter.mock.calls[0]?.[0]).toMatchObject({ id: 1 }));
  });

  it("imports a manifest and deletes a credential reference", async () => {
    renderPage();
    await screen.findByRole("heading", { name: "本地自定义 API" });
    const file = {
      name: "adapter.json",
      text: vi.fn(async () => JSON.stringify({ manifest_version: 1 }))
    } as unknown as File;
    const input = document.querySelector<HTMLInputElement>('input[type="file"]');
    expect(input).not.toBeNull();
    fireEvent.change(input!, { target: { files: [file] } });
    await waitFor(() => expect(mocks.importManifest.mock.calls[0]?.[0]).toEqual({ manifest_version: 1 }));

    fireEvent.click(screen.getByRole("button", { name: "凭据引用" }));
    fireEvent.click(screen.getByTitle("删除凭据引用"));
    await waitFor(() => expect(mocks.deleteCredential.mock.calls[0]?.[0]).toMatchObject({ id: 1 }));
  });
});
