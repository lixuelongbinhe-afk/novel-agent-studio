import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { HomePage } from "./HomePage";

vi.mock("../api/studio", () => ({
  studioApi: {
    dashboard: async () => [],
    createProject: vi.fn(),
    createContinuation: vi.fn(),
    createContinuationFile: vi.fn(),
    deleteProject: vi.fn()
  }
}));

describe("HomePage", () => {
  it("renders the V2 project dashboard empty state", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <MemoryRouter>
        <QueryClientProvider client={queryClient}>
          <HomePage />
        </QueryClientProvider>
      </MemoryRouter>
    );

    expect(await screen.findByRole("heading", { name: "项目" })).toBeInTheDocument();
    expect(screen.getByText("创作阶段")).toBeInTheDocument();
    expect(screen.getByText("完成字数")).toBeInTheDocument();
    expect(screen.getByText("待审核")).toBeInTheDocument();
    expect(screen.getByText("最后编辑")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /导入半成品续写/ })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: /新建第一本小说/ })).toBeInTheDocument();
  });

  it("opens the continuation import workflow with every source mode", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <MemoryRouter>
        <QueryClientProvider client={queryClient}>
          <HomePage />
        </QueryClientProvider>
      </MemoryRouter>
    );

    fireEvent.click(await screen.findByRole("button", { name: /导入半成品续写/ }));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /上传文件/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /粘贴正文/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /已有项目/ })).toBeInTheDocument();
    expect(screen.getByText(/TXT · Markdown · Word · PDF/)).toBeInTheDocument();
  });
});
