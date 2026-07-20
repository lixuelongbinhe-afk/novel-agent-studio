import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useUiStore } from "../stores/ui";
import { AppShell } from "./AppShell";

const apiMocks = vi.hoisted(() => ({
  dashboard: vi.fn()
}));

vi.mock("../api/studio", () => ({
  studioApi: apiMocks
}));

function renderShell(initialPath = "/") {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <QueryClientProvider client={queryClient}>
        <Routes>
          <Route element={<AppShell />}>
            <Route path="/" element={<div>项目首页</div>} />
            <Route path="/studio/:projectId" element={<div>创作工作台</div>} />
            <Route path="/models" element={<div>模型中心</div>} />
            <Route path="/advanced-api" element={<div>自定义接口</div>} />
          </Route>
        </Routes>
      </QueryClientProvider>
    </MemoryRouter>
  );
}

describe("AppShell", () => {
  beforeEach(() => {
    localStorage.clear();
    useUiStore.setState({ selectedProjectId: null, sidebarCollapsed: false });
    apiMocks.dashboard.mockReset().mockResolvedValue([{
      id: 12,
      title: "侧栏测试小说",
      summary: "",
      stage: "drafting",
      stage_label: "正文创作",
      completed_words: 4321,
      target_words: 100000,
      pending_reviews: 2,
      updated_at: new Date().toISOString(),
      entry_mode: "creative"
    }]);
  });

  it("toggles the sidebar and keeps the control label aligned with its action", async () => {
    const { container } = renderShell();
    await screen.findAllByText("侧栏测试小说");

    fireEvent.click(screen.getAllByTitle("收起侧栏")[0]);
    expect(container.querySelector(".nas-shell")).toHaveClass("is-collapsed");
    expect(screen.getAllByTitle("展开侧栏")).toHaveLength(2);

    fireEvent.click(screen.getAllByTitle("展开侧栏")[0]);
    expect(container.querySelector(".nas-shell")).not.toHaveClass("is-collapsed");
    expect(screen.getAllByTitle("收起侧栏")).toHaveLength(2);
  });

  it("navigates every primary entry and persists the selected project", async () => {
    renderShell();
    const projectTitles = await screen.findAllByText("侧栏测试小说");

    const recentProjectLink = projectTitles.find((element) => element.closest(".project-nav-item"));
    expect(recentProjectLink).toBeDefined();
    fireEvent.click(recentProjectLink!);
    expect(await screen.findByText("创作工作台")).toBeInTheDocument();
    await waitFor(() => expect(useUiStore.getState().selectedProjectId).toBe(12));

    fireEvent.click(screen.getByTitle("项目"));
    expect(await screen.findByText("项目首页")).toBeInTheDocument();
    fireEvent.click(screen.getByTitle("模型与 API"));
    expect(await screen.findByText("模型中心")).toBeInTheDocument();
    fireEvent.click(screen.getByTitle("自定义 HTTP"));
    expect(await screen.findByText("自定义接口")).toBeInTheDocument();
    fireEvent.click(screen.getByTitle("创作流程"));
    expect(await screen.findByText("创作工作台")).toBeInTheDocument();
  });
});
