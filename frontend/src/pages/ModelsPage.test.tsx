import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ModelsPage } from "./ModelsPage";

const mocks = vi.hoisted(() => ({
  providers: vi.fn(),
  setupProvider: vi.fn(),
  updateProviderSecret: vi.fn(),
  deleteProvider: vi.fn(),
  testProvider: vi.fn()
}));

vi.mock("../api/studio", () => ({
  studioApi: mocks
}));

const provider = {
  id: 7,
  name: "DeepSeek 主账号",
  provider_type: "deepseek",
  base_url: "https://api.deepseek.com/v1",
  env_var_name: null,
  secret_stored: true,
  enabled: true,
  model: "deepseek-chat",
  revision: 1
};

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(
    <MemoryRouter>
      <QueryClientProvider client={queryClient}>
        <ModelsPage />
      </QueryClientProvider>
    </MemoryRouter>
  );
}

describe("ModelsPage", () => {
  beforeEach(() => {
    mocks.providers.mockReset().mockResolvedValue([]);
    mocks.setupProvider.mockReset();
    mocks.updateProviderSecret.mockReset();
    mocks.deleteProvider.mockReset();
    mocks.testProvider.mockReset();
  });

  afterEach(() => vi.restoreAllMocks());

  it("offers secure provider setup and custom HTTP entry", async () => {
    renderPage();

    expect(await screen.findByRole("heading", { name: "模型与 API" })).toBeInTheDocument();
    expect(screen.getByText(/Windows 凭据管理器/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /自定义 HTTP/ })).toHaveAttribute("href", "/advanced-api");
    expect(await screen.findByRole("button", { name: /连接第一个模型服务/ })).toBeInTheDocument();
  });

  it("creates a provider with the selected preset and secure key mode", async () => {
    mocks.setupProvider.mockResolvedValue(provider);
    renderPage();
    fireEvent.click(await screen.findByRole("button", { name: /连接第一个模型服务/ }));
    fireEvent.click(screen.getByRole("button", { name: /^OpenAI$/ }));
    fireEvent.change(screen.getByLabelText("API Key"), { target: { value: "test-secret" } });
    fireEvent.click(screen.getByRole("button", { name: /保存服务/ }));

    await waitFor(() => expect(mocks.setupProvider).toHaveBeenCalledWith(expect.objectContaining({
      preset: "openai",
      base_url: "https://api.openai.com/v1",
      model: "gpt-5-mini",
      api_key: "test-secret",
      env_var_name: null
    })));
  });

  it("tests, updates, and deletes an existing provider through real mutations", async () => {
    mocks.providers.mockReset().mockResolvedValue([provider]);
    mocks.testProvider.mockResolvedValue({ ok: true, latency_ms: 83, model_count: 2 });
    mocks.updateProviderSecret.mockResolvedValue(provider);
    mocks.deleteProvider.mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    renderPage();
    await screen.findByText("DeepSeek 主账号");

    fireEvent.click(screen.getByTitle("测试连接"));
    expect(await screen.findByText("连接正常 · 83 ms · 2 个模型")).toBeInTheDocument();
    expect(mocks.testProvider).toHaveBeenCalledWith(7);

    fireEvent.click(screen.getByTitle("更新 API Key"));
    fireEvent.change(screen.getByLabelText("新的 API Key"), { target: { value: "replacement-secret" } });
    fireEvent.click(screen.getByRole("button", { name: /^更新$/ }));
    await waitFor(() => expect(mocks.updateProviderSecret).toHaveBeenCalledWith(7, "replacement-secret"));

    fireEvent.click(screen.getByTitle("删除服务"));
    await waitFor(() => expect(mocks.deleteProvider.mock.calls[0]?.[0]).toBe(7));
  });
});
