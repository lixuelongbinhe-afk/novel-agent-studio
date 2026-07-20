import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { HomePage } from "./HomePage";

const apiMocks = vi.hoisted(() => ({
  dashboard: vi.fn(),
  createProject: vi.fn(),
  createContinuation: vi.fn(),
  createContinuationFile: vi.fn(),
  deleteProject: vi.fn()
}));

vi.mock("../api/studio", () => ({
  studioApi: apiMocks
}));

describe("HomePage", () => {
  beforeEach(() => {
    apiMocks.dashboard.mockReset().mockResolvedValue([]);
    apiMocks.createProject.mockReset();
    apiMocks.createContinuation.mockReset();
    apiMocks.createContinuationFile.mockReset();
    apiMocks.deleteProject.mockReset();
  });

  afterEach(() => vi.restoreAllMocks());

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

  it("deletes a confirmed project and refreshes the persisted dashboard", async () => {
    const project = {
      id: 9,
      title: "待删除小说",
      summary: "测试真实删除链路",
      stage: "idea",
      stage_label: "创意简报",
      completed_words: 0,
      target_words: 100000,
      pending_reviews: 0,
      updated_at: new Date().toISOString(),
      entry_mode: "creative"
    };
    apiMocks.dashboard.mockReset().mockResolvedValueOnce([project]).mockResolvedValue([]);
    apiMocks.deleteProject.mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <MemoryRouter>
        <QueryClientProvider client={queryClient}>
          <HomePage />
        </QueryClientProvider>
      </MemoryRouter>
    );

    expect(await screen.findByText("待删除小说")).toBeInTheDocument();
    fireEvent.click(screen.getByTitle("删除"));

    await waitFor(() => expect(apiMocks.deleteProject.mock.calls[0]?.[0]).toBe(9));
    expect(await screen.findByRole("button", { name: /新建第一本小说/ })).toBeInTheDocument();
    expect(screen.queryByText("待删除小说")).not.toBeInTheDocument();
  });

  it("shows the real API error when project deletion fails", async () => {
    apiMocks.dashboard.mockReset().mockResolvedValue([{
      id: 10,
      title: "保留项目",
      summary: "",
      stage: "idea",
      stage_label: "创意简报",
      completed_words: 0,
      target_words: 100000,
      pending_reviews: 0,
      updated_at: new Date().toISOString(),
      entry_mode: "creative"
    }]);
    apiMocks.deleteProject.mockRejectedValue(new Error("数据库写入失败"));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    render(
      <MemoryRouter>
        <QueryClientProvider client={queryClient}>
          <HomePage />
        </QueryClientProvider>
      </MemoryRouter>
    );

    await screen.findByText("保留项目");
    fireEvent.click(screen.getByTitle("删除"));

    expect(await screen.findByRole("alert")).toHaveTextContent("删除失败：数据库写入失败");
    expect(screen.getByText("保留项目")).toBeInTheDocument();
  });
});
