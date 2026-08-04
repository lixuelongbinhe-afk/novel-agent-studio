import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { OnboardingWizard } from "./OnboardingWizard";

const apiMocks = vi.hoisted(() => ({
  providers: vi.fn(),
  setupProvider: vi.fn(),
  createProject: vi.fn()
}));

vi.mock("../api/studio", () => ({
  studioApi: apiMocks
}));

const provider = {
  id: 7,
  name: "OpenAI",
  provider_type: "openai",
  base_url: "https://api.openai.com/v1",
  env_var_name: null,
  secret_stored: true,
  enabled: true,
  model: "gpt-5-mini",
  revision: 1
};

function renderWizard(onComplete = vi.fn()) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } }
  });
  render(
    <QueryClientProvider client={queryClient}>
      <OnboardingWizard onComplete={onComplete} />
    </QueryClientProvider>
  );
  return onComplete;
}

describe("OnboardingWizard", () => {
  beforeEach(() => {
    localStorage.clear();
    apiMocks.providers.mockReset().mockResolvedValueOnce([]).mockResolvedValue([provider]);
    apiMocks.setupProvider.mockReset().mockResolvedValue(provider);
    apiMocks.createProject.mockReset().mockResolvedValue({
      project: { id: 18, title: "星海回声" }
    });
  });

  it("creates a provider and user-authored project through all three steps", async () => {
    const onComplete = renderWizard();

    fireEvent.click(await screen.findByRole("button", { name: "开始配置" }));
    fireEvent.click(await screen.findByRole("button", { name: "OpenAI" }));
    fireEvent.change(screen.getByLabelText("API Key"), { target: { value: "test-secret" } });
    fireEvent.click(screen.getByRole("button", { name: /保存服务/ }));

    expect(await screen.findByRole("heading", { name: "创建第一本小说" })).toBeInTheDocument();
    expect(screen.getByLabelText("模型服务")).toHaveValue("7");
    fireEvent.change(screen.getByLabelText("书名"), { target: { value: "星海回声" } });
    fireEvent.change(screen.getByLabelText("题材与创意"), { target: { value: "一名领航员寻找失落的地球信标" } });
    fireEvent.click(screen.getByRole("button", { name: "创建项目" }));

    await waitFor(() => expect(onComplete).toHaveBeenCalledOnce());
    expect(localStorage.getItem("onboarding-done")).toBe("true");
    expect(apiMocks.setupProvider).toHaveBeenCalledWith(expect.objectContaining({
      preset: "openai",
      model: "gpt-5-mini",
      api_key: "test-secret"
    }));
    expect(apiMocks.createProject).toHaveBeenCalledWith(expect.objectContaining({
      title: "星海回声",
      idea: "一名领航员寻找失落的地球信标"
    }));
  });
});
