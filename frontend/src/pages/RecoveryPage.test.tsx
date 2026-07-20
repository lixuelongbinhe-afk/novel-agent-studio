import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useUiStore } from "../stores/ui";
import { RecoveryPage } from "./RecoveryPage";

const { restoreRecord } = vi.hoisted(() => ({
  restoreRecord: vi.fn(async (_resource: string, _record: unknown) => undefined)
}));

vi.mock("../api/client", () => ({
  api: {
    listProjects: async (deleted = false) => deleted
      ? [{ id: 9, title: "旧项目", summary: "", language: "zh-CN", target_words: 90000, revision: 2, deleted_at: new Date().toISOString(), created_at: "", updated_at: "" }]
      : [{ id: 1, title: "雾港回声", summary: "", language: "zh-CN", target_words: 100000, revision: 1, deleted_at: null, created_at: "", updated_at: "" }],
    projectTrash: async () => ({
      projects: [], volumes: [], chapters: [{ id: 11, revision: 3, deleted_at: new Date().toISOString(), label: "被删章节" }], scenes: [],
      entities: [], aliases: [], relations: [], state_changes: [], timeline: [], foreshadows: [], style_guides: []
    }),
    restoreRecord
  }
}));

describe("RecoveryPage", () => {
  beforeEach(() => {
    restoreRecord.mockClear();
    useUiStore.setState({ selectedProjectId: 1 });
  });

  it("restores a deleted chapter with its revision", async () => {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={queryClient}><RecoveryPage /></QueryClientProvider>);
    expect(await screen.findByText("被删章节")).toBeInTheDocument();
    const chapterGroup = screen.getByText("章节").closest("section")!;
    fireEvent.click(chapterGroup.querySelector("button")!);
    await waitFor(() => expect(restoreRecord).toHaveBeenCalledTimes(1));
    expect(restoreRecord.mock.calls[0][0]).toBe("chapter");
    expect(restoreRecord.mock.calls[0][1]).toMatchObject({ id: 11, revision: 3 });
  });
});
