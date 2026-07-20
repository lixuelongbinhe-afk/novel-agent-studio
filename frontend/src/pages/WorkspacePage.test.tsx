import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useUiStore } from "../stores/ui";
import { WorkspacePage } from "./WorkspacePage";

const { autosaveChapter } = vi.hoisted(() => ({
  autosaveChapter: vi.fn(async (_chapter: unknown, title: string, content: string) => ({
    id: 11,
    volume_id: 3,
    title,
    content,
    position: 1,
    word_count: content.length,
    revision: 2,
    updated_at: new Date().toISOString(),
    deleted_at: null
  }))
}));

vi.mock("../components/ManuscriptEditor", () => ({
  ManuscriptEditor: ({
    value,
    placeholder,
    onChange
  }: {
    value: string;
    placeholder: string;
    onChange: (value: string) => void;
  }) => <textarea value={value} placeholder={placeholder} onChange={(event) => onChange(event.target.value)} />
}));

vi.mock("../api/client", () => ({
  api: {
    listProjects: async () => [{ id: 1, title: "雾港回声", summary: "", language: "zh-CN", target_words: 100000, revision: 1, deleted_at: null, created_at: "", updated_at: "" }],
    tree: async () => ({
      project: { id: 1, title: "雾港回声", summary: "", language: "zh-CN", target_words: 100000, revision: 1, deleted_at: null, created_at: "", updated_at: "" },
      volumes: [{ id: 3, project_id: 1, title: "第一卷", position: 1, revision: 1, deleted_at: null }],
      chapters: [{ id: 11, volume_id: 3, title: "第一章", content: "旧正文", position: 1, word_count: 3, revision: 1, updated_at: "", deleted_at: null }],
      scenes: []
    }),
    listEntities: async () => [],
    listForeshadows: async () => [],
    listStyleGuides: async () => [],
    listChapterVersions: async () => [],
    autosaveChapter
  }
}));

describe("WorkspacePage", () => {
  beforeEach(() => {
    autosaveChapter.mockClear();
    useUiStore.setState({ selectedProjectId: 1, selectedChapterId: null, selectedSceneId: null, rightPanelOpen: true });
  });

  it("renders the three-pane writing workspace and saves edited manuscript", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <MemoryRouter>
        <QueryClientProvider client={queryClient}>
          <WorkspacePage />
        </QueryClientProvider>
      </MemoryRouter>
    );
    expect(await screen.findByDisplayValue("旧正文")).toBeInTheDocument();
    expect(screen.getByText("卷 / 章 / 场景")).toBeInTheDocument();
    expect(screen.getByText("资料侧栏")).toBeInTheDocument();

    fireEvent.change(screen.getByPlaceholderText("从这一章开始写..."), { target: { value: "新的正文内容" } });
    fireEvent.click(screen.getByTitle("保存 Ctrl+S"));
    await waitFor(() => expect(autosaveChapter).toHaveBeenCalledTimes(1));
    expect(autosaveChapter.mock.calls[0][1]).toBe("第一章");
    expect(autosaveChapter.mock.calls[0][2]).toBe("新的正文内容");
  });
});
