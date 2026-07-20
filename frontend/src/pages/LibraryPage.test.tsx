import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useUiStore } from "../stores/ui";
import { LibraryPage } from "./LibraryPage";

const { createEntity } = vi.hoisted(() => ({
  createEntity: vi.fn(async (_projectId: number, payload: Record<string, unknown>) => ({
    id: 7,
    project_id: 1,
    revision: 1,
    deleted_at: null,
    ...payload
  }))
}));

vi.mock("../api/client", () => ({
  api: {
    listProjects: async () => [{ id: 1, title: "雾港回声", summary: "", language: "zh-CN", target_words: 100000, revision: 1, deleted_at: null, created_at: "", updated_at: "" }],
    tree: async () => ({ project: {}, volumes: [], chapters: [], scenes: [] }),
    listEntities: async () => [],
    listAliases: async () => [],
    createEntity
  }
}));

describe("LibraryPage", () => {
  beforeEach(() => {
    createEntity.mockClear();
    useUiStore.setState({ selectedProjectId: 1 });
  });

  it("creates a real story entity from the detail form", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <MemoryRouter>
        <QueryClientProvider client={queryClient}>
          <LibraryPage />
        </QueryClientProvider>
      </MemoryRouter>
    );
    expect(await screen.findByText("暂无匹配条目")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "新建" }));
    fireEvent.change(screen.getByLabelText("名称"), { target: { value: "林栀" } });
    fireEvent.change(screen.getByLabelText("描述"), { target: { value: "年轻档案员" } });
    fireEvent.change(screen.getByLabelText("标签", { exact: false }), { target: { value: "主角，档案馆" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(createEntity).toHaveBeenCalledTimes(1));
    expect(createEntity.mock.calls[0][1]).toMatchObject({ name: "林栀", tags: ["主角", "档案馆"] });
  });
});
