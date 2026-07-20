import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useUiStore } from "../stores/ui";
import { ApprovalPage } from "./ApprovalPage";

const mocks = vi.hoisted(() => ({
  decideApprovalRequest: vi.fn(),
  editChangeSet: vi.fn(),
  resolveChangeSet: vi.fn()
}));

const timestamp = "2026-07-18T08:00:00Z";

const proseApproval = {
  id: 11,
  project_id: 1,
  workflow_run_id: 21,
  node_run_id: 31,
  node_key: "approve_prose",
  approval_type: "prose" as const,
  status: "pending" as const,
  title: "第二章正文确认",
  instructions: "确认叙事视角和关键线索。",
  snapshot: {
    kind: "approval_snapshot" as const,
    approval_type: "prose" as const,
    value: "潮声越过旧码头。新的候选正文。",
    source: { chapter_id: 2, base_revision: 4, base_content: "潮声越过旧码头。旧正文。" }
  },
  snapshot_hash: "a".repeat(64),
  snapshot_revision: 1,
  round_number: 1,
  parent_approval_id: null,
  superseded_by_id: null,
  decision_action: null,
  decision_note: "",
  decision_payload: null,
  expires_at: "2026-07-19T08:00:00Z",
  resolved_at: null,
  revision: 1,
  created_at: timestamp,
  updated_at: timestamp
};

const changeSet = {
  id: 41,
  project_id: 1,
  workflow_run_id: 21,
  node_run_id: 33,
  node_key: "propose_changes",
  source_approval_id: 11,
  chapter_id: 2,
  scene_id: 3,
  status: "conflicted" as const,
  extraction: { kind: "state_extraction_result" },
  base_revisions: { "entity:7": 2 },
  items: [
    {
      id: "entity:7:update",
      kind: "entity" as const,
      operation: "update" as const,
      target_id: 7,
      target_label: "林雾",
      base_revision: 2,
      before: { name: "林雾", description: "尚未返港" },
      proposed: { name: "林雾", description: "已经返港" },
      evidence: ["她踏上湿润的石阶。"],
      confidence: 0.96,
      resolution: { method: "exact_name" },
      conflicts: [],
      decision: "later" as const
    }
  ],
  conflicts: [],
  live_conflicts: ["实体 #7 已从 revision 2 更新到 revision 3"],
  changes_hash: "b".repeat(64),
  superseded_by_id: null,
  applied_at: null,
  revision: 2,
  created_at: timestamp,
  updated_at: timestamp
};

const changeApproval = {
  ...proseApproval,
  id: 12,
  node_run_id: 34,
  node_key: "approve_changes",
  approval_type: "change_set" as const,
  title: "小说状态变更确认",
  snapshot: {
    kind: "approval_snapshot" as const,
    approval_type: "change_set" as const,
    value: {
      change_set_id: 41,
      change_set_revision: 2,
      changes_hash: "b".repeat(64),
      items: changeSet.items
    },
    source: { chapter_id: 2, scene_id: 3 }
  }
};

vi.mock("../components/ManuscriptEditor", () => ({
  ManuscriptEditor: ({ value, onChange, onSave }: { value: string; onChange: (value: string) => void; onSave: () => void }) => (
    <div>
      <textarea aria-label="编辑候选正文" value={value} onChange={(event) => onChange(event.target.value)} />
      <button type="button" onClick={onSave}>编辑器保存</button>
    </div>
  )
}));

vi.mock("../api/client", () => ({
  ApiError: class ApiError extends Error {},
  api: {
    listProjects: async () => [{ id: 1, title: "雾港回声", summary: "", language: "zh-CN", target_words: 100000, revision: 1, deleted_at: null, created_at: timestamp, updated_at: timestamp }],
    listApprovalRequests: async () => [proseApproval, changeApproval],
    listChangeSets: async () => [changeSet],
    listWritebackAudits: async () => [],
    decideApprovalRequest: mocks.decideApprovalRequest,
    editChangeSet: mocks.editChangeSet,
    resolveChangeSet: mocks.resolveChangeSet
  }
}));

function renderPage() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  render(<QueryClientProvider client={queryClient}><ApprovalPage /></QueryClientProvider>);
}

describe("ApprovalPage", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useUiStore.setState({ selectedProjectId: null });
    mocks.decideApprovalRequest.mockResolvedValue({ approval: { ...proseApproval, status: "approved" }, replacement: null, idempotent_replay: false });
    mocks.editChangeSet.mockResolvedValue({ change_set: { ...changeSet, live_conflicts: [] }, replacement_approval: null });
    mocks.resolveChangeSet.mockResolvedValue({ change_set: { ...changeSet, status: "pending", live_conflicts: [] }, replacement_approval: null });
  });

  it("reviews the frozen prose and sends real approval decisions", async () => {
    renderPage();

    expect(await screen.findByRole("heading", { name: "审批与写回" })).toBeInTheDocument();
    expect(await screen.findByText("新的候选正文。")).toBeInTheDocument();
    expect(screen.getByText("旧正文。")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "批准" }));
    await waitFor(() => expect(mocks.decideApprovalRequest).toHaveBeenCalledWith(
      expect.objectContaining({ id: 11, revision: 1 }),
      expect.objectContaining({ action: "approve", idempotency_key: expect.stringMatching(/^approval-11-approve-/) })
    ));

    fireEvent.change(screen.getByLabelText("审批说明"), { target: { value: "请把码头气味写得更具体" } });
    fireEvent.click(screen.getByRole("button", { name: "要求修改" }));
    await waitFor(() => expect(mocks.decideApprovalRequest).toHaveBeenLastCalledWith(
      expect.objectContaining({ id: 11 }),
      expect.objectContaining({ action: "request_changes", note: "请把码头气味写得更具体" })
    ));
  });

  it("edits per-item decisions and invokes explicit conflict resolution", async () => {
    renderPage();
    await screen.findByRole("heading", { name: "审批与写回" });
    fireEvent.click(screen.getByRole("button", { name: /变更预览/ }));

    expect(await screen.findByText("实体 #7 已从 revision 2 更新到 revision 3")).toBeInTheDocument();
    const decisionGroup = screen.getByLabelText("林雾 决定");
    fireEvent.click(within(decisionGroup).getByRole("button", { name: "接受" }));
    fireEvent.click(screen.getByTitle("编辑变更值"));
    fireEvent.change(screen.getByLabelText("编辑 林雾"), { target: { value: JSON.stringify({ name: "林雾", description: "返港并拿到铜钥匙" }) } });
    fireEvent.click(screen.getByRole("button", { name: "保存逐项决定" }));

    await waitFor(() => expect(mocks.editChangeSet).toHaveBeenCalledWith(
      expect.objectContaining({ id: 41, revision: 2 }),
      [expect.objectContaining({ decision: "accept", proposed: { name: "林雾", description: "返港并拿到铜钥匙" } })]
    ));

    fireEvent.click(screen.getByRole("button", { name: "按当前版本重基" }));
    await waitFor(() => expect(mocks.resolveChangeSet).toHaveBeenCalledWith(
      expect.objectContaining({ id: 41 }),
      "rebase_current",
      expect.any(Array)
    ));
  });
});
