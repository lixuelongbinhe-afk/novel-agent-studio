import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { studioApi, type StudioOverview } from "../api/studio";
import { StudioPage } from "./StudioPage";

const artifact = {
  id: 11,
  project_id: 1,
  kind: "world",
  title: "世界观架构师",
  content: "[重大冲突] 港口规则与旧设定冲突。",
  status: "pending" as const,
  source: "ai",
  position: 200,
  version_number: 2,
  notes: "核对潮汐规则",
  metadata: { agent_name: "世界观架构师", conflict_level: "major", series_key: "world:世界观架构师" },
  revision: 3,
  created_at: "2026-07-20T00:00:00Z",
  updated_at: "2026-07-20T00:00:00Z"
};

const overview: StudioOverview = {
  project: { id: 1, title: "雾港回声", summary: "雨季谜案", target_words: 100000, revision: 1, updated_at: "2026-07-20T00:00:00Z" },
  state: {
    id: 1, project_id: 1, entry_mode: "creative" as const, stage: "world", stage_label: "世界观与风格",
    review_granularity: "chapter" as const, routing_strategy: "balanced" as const,
    generation_mode: "countdown" as const, countdown_seconds: 10, memory_mode: "automatic" as const,
    budget_limit: null, budget_spent: 0, budget_currency: "USD", budget_warning_percent: 70,
    budget_pause_percent: 110, budget_paused: false, revision: 1, config: {}
  },
  stages: [
    { key: "idea", label: "创意简报" }, { key: "world", label: "世界观与风格" },
    { key: "characters", label: "人物与关系" }, { key: "plot", label: "剧情与伏笔" },
    { key: "volumes", label: "分卷大纲" }, { key: "chapters", label: "章节与场景" },
    { key: "drafting", label: "正文创作" }, { key: "review", label: "全文审阅" },
    { key: "complete", label: "完成" }
  ],
  artifacts: [artifact],
  tree: { volumes: [], chapters: [], scenes: [] },
  jobs: [], messages: [], snapshots: [],
  library_counts: { entities: 0, timeline: 0, foreshadows: 0, style_guides: 0 },
  usage: { invocations: 0, tokens: 0, spent: 0, limit: null, currency: "USD", percent: 0, warning: false, paused: false }
};

vi.mock("../api/studio", () => ({
  studioApi: {
    project: vi.fn(async () => overview),
    providers: async () => [{ id: 1, provider_type: "openai_chat", name: "DeepSeek" }],
    artifactVersions: async () => [artifact, { ...artifact, id: 10, version_number: 1, status: "superseded", content: "旧版规则" }],
    updateState: vi.fn(), updateArtifact: vi.fn(), decideArtifact: vi.fn(), generate: vi.fn(),
    chat: vi.fn(), decideProposal: vi.fn(), previewOutline: vi.fn(), previewOutlineFile: vi.fn(),
    importOutline: vi.fn(), extractStyleReference: vi.fn(), createSnapshot: vi.fn(),
    restoreSnapshot: vi.fn(), autosaveChapter: vi.fn(), exportUrl: vi.fn()
  }
}));

describe("StudioPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(studioApi.project).mockImplementation(async () => overview);
  });

  it("does not treat the WebView scroll result as an effect cleanup", async () => {
    const previousMessages = overview.messages;
    const previousScrollIntoView = HTMLElement.prototype.scrollIntoView;
    overview.messages = [{
      id: 1,
      project_id: 1,
      role: "assistant" as const,
      content: "已读取创作上下文",
      context_scope: "project",
      proposal: null,
      proposal_status: "none",
      model_name: null,
      model_reason: "",
      created_at: "2026-07-20T00:00:00Z"
    }];
    Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
      configurable: true,
      writable: true,
      value: vi.fn(() => ({ webview: true }))
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });

    try {
      const view = render(
        <MemoryRouter initialEntries={["/studio/1"]}>
          <QueryClientProvider client={client}>
            <Routes><Route path="/studio/:projectId" element={<StudioPage />} /></Routes>
          </QueryClientProvider>
        </MemoryRouter>
      );
      expect(await screen.findByText("已读取创作上下文")).toBeInTheDocument();
      expect(() => view.unmount()).not.toThrow();
    } finally {
      overview.messages = previousMessages;
      Object.defineProperty(HTMLElement.prototype, "scrollIntoView", {
        configurable: true,
        writable: true,
        value: previousScrollIntoView
      });
    }
  });

  it("exposes the confirmed review and continuation controls", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    render(
      <MemoryRouter initialEntries={["/studio/1"]}>
        <QueryClientProvider client={client}>
          <Routes><Route path="/studio/:projectId" element={<StudioPage />} /></Routes>
        </QueryClientProvider>
      </MemoryRouter>
    );

    expect(await screen.findByRole("heading", { name: "雾港回声" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "手动" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "自动" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "倒计时" })).toBeInTheDocument();
    expect(screen.getByText("提取参考文风")).toBeInTheDocument();
    expect(screen.getByTitle("只重新生成这一项")).toBeInTheDocument();

    fireEvent.click(screen.getByTitle("编辑"));
    expect(await screen.findByRole("heading", { name: "审核、批注与版本比较" })).toBeInTheDocument();
    expect(screen.getByText("审核批注")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "关闭审核编辑器" }));
  });

  it("requires an explicit resolution for a major conflict", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <MemoryRouter initialEntries={["/studio/1"]}>
        <QueryClientProvider client={client}>
          <Routes><Route path="/studio/:projectId" element={<StudioPage />} /></Routes>
        </QueryClientProvider>
      </MemoryRouter>
    );
    fireEvent.click(await screen.findByTitle("审核"));
    fireEvent.click(screen.getByTitle("通过"));
    expect(screen.getByRole("heading", { name: "发现重大设定冲突" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保留既有设定" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "手工合并" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "保留当前正文" })).toBeInTheDocument();
  });

  it("starts the next chapter after approval in automatic mode", async () => {
    artifact.kind = "drafting";
    artifact.metadata.conflict_level = "none";
    Object.assign(artifact.metadata, { chapter_id: 21 });
    Object.assign(overview.state, { stage: "drafting", stage_label: "正文创作", generation_mode: "automatic" });
    Object.assign(overview.tree, {
      volumes: [{ id: 1, project_id: 1, title: "第一卷", position: 1, revision: 1 }],
      chapters: [
        { id: 21, volume_id: 1, title: "第一章", content: "正文", position: 1, word_count: 2, revision: 1, updated_at: "" },
        { id: 22, volume_id: 1, title: "第二章", content: "", position: 2, word_count: 0, revision: 1, updated_at: "" }
      ]
    });
    vi.mocked(studioApi.decideArtifact).mockResolvedValue(artifact);
    vi.mocked(studioApi.generate).mockResolvedValue({ job: {} as never, artifact, artifacts: [artifact] });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <MemoryRouter initialEntries={["/studio/1"]}>
        <QueryClientProvider client={client}>
          <Routes><Route path="/studio/:projectId" element={<StudioPage />} /></Routes>
        </QueryClientProvider>
      </MemoryRouter>
    );

    fireEvent.click(await screen.findByTitle("审核"));
    fireEvent.click(screen.getByTitle("通过并写入正文"));
    await waitFor(() => expect(studioApi.generate).toHaveBeenCalledWith(
      1,
      "drafting",
      expect.objectContaining({ chapter_id: 22, use_demo_model: false })
    ));
  });

  it("writes an approved Agent draft into the open chapter and refreshes the editor", async () => {
    const draftContent = "雾从断桥下升起，林雾握紧了铜钥匙。";
    const draft = {
      ...artifact,
      id: 20,
      kind: "drafting",
      title: "正文创作",
      content: draftContent,
      status: "pending" as const,
      metadata: { chapter_id: 21, mode: "new", conflict_level: "none" },
      revision: 1
    };
    const chapter = { id: 21, volume_id: 1, title: "第一章 深渊之下", content: "", position: 1, word_count: 0, revision: 1, updated_at: "" };
    const initialOverview: StudioOverview = {
      ...overview,
      state: { ...overview.state, stage: "drafting", stage_label: "正文创作", generation_mode: "manual" },
      artifacts: [draft],
      tree: {
        volumes: [{ id: 1, project_id: 1, title: "第一卷", position: 1, revision: 1 }],
        chapters: [chapter],
        scenes: []
      }
    };
    const approvedOverview: StudioOverview = {
      ...initialOverview,
      artifacts: [{ ...draft, status: "approved", revision: 2 }],
      tree: {
        ...initialOverview.tree,
        chapters: [{ ...chapter, content: draftContent, word_count: 18, revision: 2 }]
      }
    };
    vi.mocked(studioApi.project)
      .mockResolvedValueOnce(initialOverview)
      .mockResolvedValue(approvedOverview);
    vi.mocked(studioApi.decideArtifact).mockResolvedValue({ ...draft, status: "approved", revision: 2 });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });

    render(
      <MemoryRouter initialEntries={["/studio/1"]}>
        <QueryClientProvider client={client}>
          <Routes><Route path="/studio/:projectId" element={<StudioPage />} /></Routes>
        </QueryClientProvider>
      </MemoryRouter>
    );

    const editor = await screen.findByPlaceholderText("正文");
    expect(editor).toHaveValue("");
    fireEvent.click(screen.getByTitle("审核"));
    fireEvent.click(screen.getByRole("button", { name: "通过并写入正文" }));

    await waitFor(() => expect(editor).toHaveValue(draftContent));
    expect(screen.getByText(/正文已通过并写入章节/)).toBeInTheDocument();
  });
});
