import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { ModelsPage } from "./ModelsPage";

vi.mock("../api/studio", () => ({
  studioApi: {
    providers: async () => [],
    setupProvider: vi.fn(),
    updateProviderSecret: vi.fn(),
    deleteProvider: vi.fn(),
    testProvider: vi.fn()
  }
}));

describe("ModelsPage", () => {
  it("offers secure provider setup and custom HTTP entry", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <MemoryRouter>
        <QueryClientProvider client={queryClient}>
          <ModelsPage />
        </QueryClientProvider>
      </MemoryRouter>
    );

    expect(await screen.findByRole("heading", { name: "模型与 API" })).toBeInTheDocument();
    expect(screen.getByText(/Windows 凭据管理器/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /自定义 HTTP/ })).toHaveAttribute("href", "/advanced-api");
    expect(await screen.findByRole("button", { name: /连接第一个模型服务/ })).toBeInTheDocument();
  });
});
