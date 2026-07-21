export type DashboardProject = {
  id: number;
  title: string;
  summary: string;
  stage: string;
  stage_label: string;
  completed_words: number;
  target_words: number;
  pending_reviews: number;
  updated_at: string;
  entry_mode: "creative" | "outline" | "continuation";
};

export type StudioState = {
  id: number;
  project_id: number;
  entry_mode: "creative" | "outline" | "continuation";
  stage: string;
  stage_label: string;
  review_granularity: "chapter" | "scene";
  routing_strategy: "quality" | "cost" | "speed" | "balanced";
  generation_mode: "manual" | "automatic" | "countdown";
  countdown_seconds: number;
  memory_mode: "automatic" | "confirm";
  budget_limit: number | null;
  budget_spent: number;
  budget_currency: string;
  budget_warning_percent: number;
  budget_pause_percent: number;
  budget_paused: boolean;
  revision: number;
  config: Record<string, unknown>;
};

export type Artifact = {
  id: number;
  project_id: number;
  kind: string;
  title: string;
  content: string;
  status: "pending" | "approved" | "changes_requested" | "rejected" | "superseded";
  source: string;
  position: number;
  version_number: number;
  notes: string;
  metadata: Record<string, unknown>;
  revision: number;
  created_at: string;
  updated_at: string;
};

export type StudioMessage = {
  id: number;
  project_id: number;
  role: "user" | "assistant";
  content: string;
  context_scope: string;
  proposal: {
    target_type: string;
    target_id?: number;
    content?: string;
    phase?: string;
    chapter_id?: number;
    label?: string;
    use_demo_model?: boolean;
  } | null;
  proposal_status: "none" | "pending" | "applied" | "rejected";
  model_name: string | null;
  model_reason: string;
  created_at: string;
};

export type GenerationJob = {
  id: number;
  project_id: number;
  kind: string;
  label: string;
  status: "queued" | "running" | "completed" | "failed";
  progress: number;
  model_name: string | null;
  model_reason: string;
  error_message: string;
  created_at: string;
};

export type Snapshot = {
  id: number;
  project_id: number;
  kind: "automatic" | "special";
  label: string;
  reason: string;
  permanent: boolean;
  created_at: string;
};

export type Volume = { id: number; project_id: number; title: string; position: number; revision: number };
export type Chapter = {
  id: number;
  volume_id: number;
  title: string;
  content: string;
  position: number;
  word_count: number;
  revision: number;
  updated_at: string;
};
export type Scene = {
  id: number;
  chapter_id: number;
  title: string;
  synopsis: string;
  content: string;
  position: number;
  revision: number;
};

export type StudioOverview = {
  project: {
    id: number;
    title: string;
    summary: string;
    target_words: number;
    revision: number;
    updated_at: string;
  };
  state: StudioState;
  stages: Array<{ key: string; label: string }>;
  artifacts: Artifact[];
  tree: { volumes: Volume[]; chapters: Chapter[]; scenes: Scene[] };
  jobs: GenerationJob[];
  messages: StudioMessage[];
  snapshots: Snapshot[];
  chapter_tree_repair: {
    requested_count: number;
    active_count: number;
    suspect_chapters: Array<{ id: number; title: string; word_count: number; revision: number }>;
    missing_numbers: number[];
    out_of_order: boolean;
    duplicate_volumes: string[];
    position_errors: boolean;
    can_repair: boolean;
  };
  library_counts: { entities: number; timeline: number; foreshadows: number; style_guides: number };
  usage: {
    invocations: number;
    tokens: number;
    spent: number;
    limit: number | null;
    currency: string;
    percent: number;
    warning: boolean;
    paused: boolean;
  };
};

export type OutlinePreview = {
  title: string;
  volumes: Array<{
    title: string;
    chapters: Array<{ title: string; synopsis: string; scenes: Array<{ title: string; synopsis: string }> }>;
  }>;
  volume_count: number;
  chapter_count: number;
  scene_count: number;
  warnings: string[];
  source_text?: string;
};

export type StudioProvider = {
  id: number;
  name: string;
  provider_type: string;
  base_url: string;
  env_var_name: string | null;
  secret_stored: boolean;
  enabled: boolean;
  model: string | null;
  revision: number;
  models?: Array<{ id: number; name: string; display_name: string }>;
};

const JSON_HEADERS = { "Content-Type": "application/json" };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    let message = `请求失败 (${response.status})`;
    try {
      const payload = await response.json();
      message = typeof payload.detail === "string" ? payload.detail : JSON.stringify(payload.detail);
    } catch {
      // Keep the status message when a provider returns a non-JSON error.
    }
    throw new Error(message);
  }
  return response.status === 204 ? (undefined as T) : response.json();
}

function json(method: string, body: unknown): RequestInit {
  return { method, headers: JSON_HEADERS, body: JSON.stringify(body) };
}

export const studioApi = {
  dashboard: () => request<DashboardProject[]>("/api/studio/projects"),
  createProject: (payload: Record<string, unknown>) =>
    request<StudioOverview>("/api/studio/projects", json("POST", payload)),
  createContinuation: (payload: Record<string, unknown>) =>
    request<StudioOverview>("/api/studio/continuations", json("POST", payload)),
  createContinuationFile: async (file: File, payload: Record<string, unknown>) => {
    const body = new FormData();
    body.append("file", file);
    Object.entries(payload).forEach(([key, value]) => {
      if (value !== null && value !== undefined && value !== "") body.append(key, String(value));
    });
    return request<StudioOverview>("/api/studio/continuations/file", { method: "POST", body });
  },
  project: (id: number) => request<StudioOverview>(`/api/studio/projects/${id}`),
  deleteProject: (id: number) => request<void>(`/api/studio/projects/${id}`, { method: "DELETE" }),
  updateState: (id: number, payload: Partial<StudioState>) =>
    request<StudioState>(`/api/studio/projects/${id}/state`, json("PATCH", payload)),
  updateContinuationSettings: (id: number, payload: Record<string, unknown>) =>
    request<StudioState>(
      `/api/studio/projects/${id}/continuation/settings`,
      json("PATCH", payload)
    ),
  updateArtifact: (artifact: Artifact, patch: Partial<Artifact>) =>
    request<Artifact>(
      `/api/studio/artifacts/${artifact.id}`,
      json("PUT", {
        title: patch.title,
        content: patch.content,
        notes: patch.notes,
        expected_revision: artifact.revision
      })
    ),
  artifactVersions: (artifactId: number) =>
    request<Artifact[]>(`/api/studio/artifacts/${artifactId}/versions`),
  decideArtifact: (
    artifact: Artifact,
    action: "approve" | "request_changes" | "reject",
    note = "",
    conflictResolution?: "preserve_prose" | "preserve_canon" | "manual_merge"
  ) =>
    request<Artifact>(
      `/api/studio/artifacts/${artifact.id}/decision`,
      json("POST", {
        action,
        note,
        conflict_resolution: conflictResolution,
        expected_revision: artifact.revision
      })
    ),
  generate: (
    projectId: number,
    phase: string,
    payload: { instruction?: string; agent_name?: string; chapter_id?: number; selected_text?: string; mode?: string; use_demo_model?: boolean }
  ) => request<{ job: GenerationJob; artifact: Artifact; artifacts: Artifact[] }>(
    `/api/studio/projects/${projectId}/generate/${phase}`,
    json("POST", payload)
  ),
  chat: (projectId: number, payload: Record<string, unknown>) =>
    request<StudioMessage>(`/api/studio/projects/${projectId}/chat`, json("POST", payload)),
  decideProposal: (projectId: number, messageId: number, action: "apply" | "reject") =>
    request<StudioMessage>(
      `/api/studio/projects/${projectId}/messages/${messageId}/proposal`,
      json("POST", { action })
    ),
  previewOutline: (projectId: number, text: string) =>
    request<OutlinePreview>(
      `/api/studio/projects/${projectId}/outline/preview`,
      json("POST", { text, replace_existing: true })
    ),
  previewOutlineFile: async (projectId: number, file: File) => {
    const body = new FormData();
    body.append("file", file);
    return request<OutlinePreview>(`/api/studio/projects/${projectId}/outline/preview-file`, {
      method: "POST",
      body
    });
  },
  extractStyleReference: async (projectId: number, file: File, useDemoModel: boolean) => {
    const body = new FormData();
    body.append("file", file);
    body.append("use_demo_model", String(useDemoModel));
    return request<Artifact>(`/api/studio/projects/${projectId}/style-reference`, {
      method: "POST",
      body
    });
  },
  importOutline: (projectId: number, text: string) =>
    request<OutlinePreview>(
      `/api/studio/projects/${projectId}/outline/import`,
      json("POST", { text, replace_existing: true })
    ),
  createSnapshot: (projectId: number, label: string, reason: string, special: boolean) =>
    request<Snapshot>(
      `/api/studio/projects/${projectId}/snapshots`,
      json("POST", { label, reason, special })
    ),
  restoreSnapshot: (projectId: number, snapshotId: number) =>
    request<StudioOverview>(
      `/api/studio/projects/${projectId}/snapshots/${snapshotId}/restore`,
      { method: "POST" }
    ),
  repairChapterTree: (projectId: number) =>
    request<{ repaired: boolean; overview: StudioOverview }>(
      `/api/studio/projects/${projectId}/chapter-tree/repair`,
      json("POST", { confirm: true })
    ),
  providers: () => request<StudioProvider[]>("/api/studio/providers"),
  setupProvider: (payload: Record<string, unknown>) =>
    request<StudioProvider>("/api/studio/providers", json("POST", payload)),
  updateProviderSecret: (providerId: number, apiKey: string) =>
    request<StudioProvider>(
      `/api/studio/providers/${providerId}/secret`,
      json("PUT", { api_key: apiKey })
    ),
  deleteProvider: (providerId: number) =>
    request<void>(`/api/studio/providers/${providerId}`, { method: "DELETE" }),
  testProvider: (providerId: number) =>
    request<{ ok: boolean; latency_ms: number; model_count: number; error?: { message: string } }>(
      `/api/model-center/providers/${providerId}/test`,
      { method: "POST" }
    ),
  autosaveChapter: (chapter: Chapter, title: string, content: string) =>
    request<Chapter>(
      `/api/projects/chapters/${chapter.id}/autosave`,
      json("PUT", { title, content, expected_revision: chapter.revision })
    ),
  createChapter: (volumeId: number, title: string, position: number) =>
    request<Chapter>(
      `/api/projects/volumes/${volumeId}/chapters`,
      json("POST", { title, content: "", position })
    ),
  deleteChapter: (chapter: Chapter) =>
    request<void>(
      `/api/projects/records/chapter/${chapter.id}?expected_revision=${chapter.revision}`,
      { method: "DELETE" }
    ),
  exportUrl: (projectId: number, kind: "book_text" | "book_markdown" | "book_pdf") =>
    `/api/release/exports/${kind}?project_id=${projectId}`
};
