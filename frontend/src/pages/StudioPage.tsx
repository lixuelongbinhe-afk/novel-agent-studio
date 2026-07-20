import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  AlertTriangle,
  ArchiveRestore,
  BookOpenText,
  Bot,
  Check,
  CheckCircle2,
  ChevronDown,
  CircleDollarSign,
  Clock3,
  Download,
  FileCheck2,
  FileInput,
  FileUp,
  FileText,
  History,
  GitCompareArrows,
  LibraryBig,
  LoaderCircle,
  MessageSquareText,
  MoreHorizontal,
  Pause,
  Pencil,
  Plus,
  Play,
  RefreshCw,
  RotateCcw,
  Save,
  Send,
  Settings2,
  Sparkles,
  SplitSquareVertical,
  Trash2,
  Undo2,
  WandSparkles,
  X
} from "lucide-react";
import { useNavigate, useParams } from "react-router-dom";
import {
  Artifact,
  Chapter,
  OutlinePreview,
  StudioOverview,
  studioApi
} from "../api/studio";
import { useUiStore } from "../stores/ui";

type RightTab = "chat" | "reviews" | "progress" | "library" | "cost";

const phaseDescriptions: Record<string, string> = {
  idea: "创意简报",
  world: "世界观、规则、文风与边界",
  characters: "人物档案、关系与成长弧",
  plot: "主支线、时间线、伏笔与转折",
  volumes: "分卷目标、节奏与结尾钩子",
  chapters: "章节目标、冲突与场景拆分",
  continuation_import: "导入原文并识别卷章结构",
  continuation_analysis: "逐项审核原文结构、设定、人物、时间线、伏笔与文风",
  continuation_outline: "从已有正文反向补建分卷、章节和场景大纲",
  continuation_plan: "确认续写方向、目标规模、未来卷章和结局",
  drafting: "按已批准资料创作正文",
  review: "终稿与一致性审阅",
  complete: "小说已完成"
};

export function StudioPage() {
  const params = useParams();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const projectId = Number(params.projectId);
  const setProject = useUiStore((state) => state.setProject);
  const [selectedPhase, setSelectedPhase] = useState<string>("");
  const [rightTab, setRightTab] = useState<RightTab>("chat");
  const [instruction, setInstruction] = useState("");
  const [chatText, setChatText] = useState("");
  const [editing, setEditing] = useState<Artifact | null>(null);
  const [editText, setEditText] = useState("");
  const [editTitle, setEditTitle] = useState("");
  const [editNotes, setEditNotes] = useState("");
  const [versions, setVersions] = useState<Artifact[]>([]);
  const [compareVersionId, setCompareVersionId] = useState<number | null>(null);
  const [conflictArtifact, setConflictArtifact] = useState<Artifact | null>(null);
  const [continuation, setContinuation] = useState<{ chapterId: number; seconds: number } | null>(null);
  const [demoApproved, setDemoApproved] = useState(false);
  const [outlineText, setOutlineText] = useState("");
  const [outlinePreview, setOutlinePreview] = useState<OutlinePreview | null>(null);
  const [snapshotsOpen, setSnapshotsOpen] = useState(false);
  const [selectedChapterId, setSelectedChapterId] = useState<number | null>(null);
  const [selectedText, setSelectedText] = useState("");
  const [notice, setNotice] = useState("");

  const { data: overview, isLoading, error } = useQuery({
    queryKey: ["studio-project", projectId],
    queryFn: () => studioApi.project(projectId),
    enabled: Number.isFinite(projectId),
    refetchInterval: 5_000
  });
  const { data: providers = [] } = useQuery({ queryKey: ["studio-providers"], queryFn: studioApi.providers });

  useEffect(() => {
    if (projectId) setProject(projectId);
  }, [projectId, setProject]);
  useEffect(() => {
    if (overview && !selectedPhase) setSelectedPhase(overview.state.stage);
  }, [overview, selectedPhase]);
  useEffect(() => {
    if (overview?.tree.chapters.length && !selectedChapterId) {
      setSelectedChapterId(overview.tree.chapters[0].id);
    }
  }, [overview, selectedChapterId]);

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["studio-project", projectId] });
  const realProviders = providers.filter((item) => item.provider_type !== "mock");
  const generate = useMutation({
    mutationFn: (payload: { phase: string; mode?: string; chapterId?: number; selection?: string; agentName?: string }) =>
      studioApi.generate(projectId, payload.phase, {
        instruction,
        agent_name: payload.agentName,
        chapter_id: payload.chapterId,
        selected_text: payload.selection,
        mode: payload.mode ?? "new",
        use_demo_model: realProviders.length === 0
      }),
    onSuccess: async () => {
      setInstruction("");
      setNotice("生成完成，已进入待审核列表");
      setRightTab("reviews");
      await refresh();
    },
    onError: (reason: Error) => setNotice(reason.message)
  });
  const decide = useMutation({
    mutationFn: ({ artifact, action, resolution }: { artifact: Artifact; action: "approve" | "request_changes" | "reject"; resolution?: "preserve_prose" | "preserve_canon" | "manual_merge" }) =>
      studioApi.decideArtifact(artifact, action, artifact.notes, resolution),
    onSuccess: async (_result, variables) => {
      const writesManuscript = ["drafting", "revision_proposal", "scene_draft"].includes(variables.artifact.kind);
      setEditing((current) => current?.id === variables.artifact.id ? null : current);
      setNotice(variables.action === "approve"
        ? writesManuscript ? "正文已通过并写入章节" : "审核已通过"
        : variables.action === "request_changes" ? "已标记为需要修改" : "已拒绝该候选");
      await queryClient.invalidateQueries({ queryKey: ["studio-project", projectId] });
      const latest = await queryClient.fetchQuery({
        queryKey: ["studio-project", projectId],
        queryFn: () => studioApi.project(projectId)
      });
      if (variables.action === "approve") scheduleNextChapter(latest, variables.artifact);
    },
    onError: (reason: Error) => setNotice(reason.message)
  });
  const saveArtifact = useMutation({
    mutationFn: () => studioApi.updateArtifact(editing!, { title: editTitle, content: editText, notes: editNotes }),
    onSuccess: async (replacement) => {
      setEditing(replacement);
      setEditTitle(replacement.title);
      setEditText(replacement.content);
      setEditNotes(replacement.notes);
      const history = await studioApi.artifactVersions(replacement.id);
      setVersions(history);
      setCompareVersionId(history.find((item) => item.id !== replacement.id)?.id ?? history[0]?.id ?? null);
      setNotice("新版本已保存，请确认后通过并写入");
      await refresh();
    },
    onError: (reason: Error) => setNotice(reason.message)
  });
  const sendChat = useMutation({
    mutationFn: () => studioApi.chat(projectId, {
      message: chatText,
      chapter_id: selectedChapterId,
      selected_text: selectedText,
      stage: selectedPhase,
      use_demo_model: realProviders.length === 0
    }),
    onSuccess: async () => {
      setChatText("");
      await refresh();
    },
    onError: (reason: Error) => setNotice(reason.message)
  });
  const proposal = useMutation({
    mutationFn: ({ messageId, action }: { messageId: number; action: "apply" | "reject" }) =>
      studioApi.decideProposal(projectId, messageId, action),
    onSuccess: refresh,
    onError: (reason: Error) => setNotice(reason.message)
  });
  const updateState = useMutation({
    mutationFn: (payload: Record<string, unknown>) => studioApi.updateState(projectId, payload),
    onSuccess: refresh
  });
  const updateContinuation = useMutation({
    mutationFn: (payload: Record<string, unknown>) => studioApi.updateContinuationSettings(projectId, payload),
    onSuccess: async () => {
      await refresh();
      setNotice("续写设置已保存");
    },
    onError: (reason: Error) => setNotice(reason.message)
  });
  const importOutline = useMutation({
    mutationFn: () => studioApi.importOutline(projectId, outlinePreview?.source_text ?? outlineText),
    onSuccess: async () => {
      setOutlinePreview(null);
      setOutlineText("");
      setSelectedPhase("drafting");
      await refresh();
    },
    onError: (reason: Error) => setNotice(reason.message)
  });
  const styleReference = useMutation({
    mutationFn: (file: File) => studioApi.extractStyleReference(projectId, file, realProviders.length === 0),
    onSuccess: async () => {
      setNotice("参考文风已提取，等待你审核");
      setSelectedPhase("world");
      setRightTab("reviews");
      await refresh();
    },
    onError: (reason: Error) => setNotice(reason.message)
  });
  const repairChapterTree = useMutation({
    mutationFn: () => studioApi.repairChapterTree(projectId),
    onSuccess: async (result) => {
      const added = result.overview.tree.chapters.find((chapter) => chapter.word_count === 0);
      if (added) setSelectedChapterId(added.id);
      setSelectedPhase(result.overview.state.stage);
      setNotice("章节结构已修复；原内容已永久快照，缺失章节已补回");
      await refresh();
    },
    onError: (reason: Error) => setNotice(reason.message)
  });

  useEffect(() => {
    if (!continuation) return;
    const timer = window.setTimeout(() => {
      if (continuation.seconds <= 1) {
        setContinuation(null);
        setSelectedChapterId(continuation.chapterId);
        generate.mutate({ phase: "drafting", chapterId: continuation.chapterId });
      } else {
        setContinuation({ ...continuation, seconds: continuation.seconds - 1 });
      }
    }, 1000);
    return () => window.clearTimeout(timer);
  }, [continuation]);

  if (isLoading) return <div className="route-loading">正在读取项目...</div>;
  if (error || !overview) {
    return <div className="fatal-state"><strong>项目无法打开</strong><span>{(error as Error)?.message}</span><button onClick={() => navigate("/")}>返回项目</button></div>;
  }

  const phase = selectedPhase || overview.state.stage;
  const artifacts = overview.artifacts.filter((item) =>
    item.status !== "superseded" && (
      item.kind === phase ||
      (phase === "continuation_import" && item.kind === "continuation_original") ||
      (phase === "drafting" && ["revision_proposal", "scene_draft"].includes(item.kind))
    )
  );
  const pending = overview.artifacts.filter((item) => ["pending", "changes_requested"].includes(item.status));
  const missingChapterSummary = formatMissingChapters(overview.chapter_tree_repair.missing_numbers);
  const hasUnsavedArtifactEdits = editing !== null && (
    editTitle !== editing.title || editText !== editing.content || editNotes !== editing.notes
  );
  const continuationConfig = overview.state.config;
  const isContinuationProject = overview.state.entry_mode === "continuation";
  const importedChapterCount = Number(continuationConfig.imported_chapter_count ?? 0);
  const importedChapters = overview.tree.chapters.slice(0, importedChapterCount);
  const lastImportedChapter = importedChapters[importedChapters.length - 1];
  const firstFutureChapter = overview.tree.chapters[importedChapterCount];

  function ensureModelPermission() {
    if (realProviders.length === 0 && !demoApproved) {
      if (!window.confirm("尚未配置正式 API。使用明确标记的内置演示模型生成本次内容？")) {
        navigate("/models");
        return false;
      }
      setDemoApproved(true);
    }
    return true;
  }

  function startGeneration(targetPhase = phase, options?: { mode?: string; chapterId?: number; selection?: string; agentName?: string }) {
    if (!ensureModelPermission()) return;
    generate.mutate({ phase: targetPhase, ...options });
  }

  async function openEdit(artifact: Artifact) {
    setEditing(artifact);
    setEditTitle(artifact.title);
    setEditText(artifact.content);
    setEditNotes(artifact.notes);
    const history = await studioApi.artifactVersions(artifact.id);
    setVersions(history);
    setCompareVersionId(history.find((item) => item.id !== artifact.id)?.id ?? history[0]?.id ?? null);
  }

  function approveArtifact(artifact: Artifact) {
    if (artifact.metadata.conflict_level === "major") {
      setConflictArtifact(artifact);
      return;
    }
    decide.mutate({ artifact, action: "approve" });
  }

  function scheduleNextChapter(latest: StudioOverview, artifact: Artifact) {
    if (!["drafting", "scene_draft"].includes(artifact.kind)) return;
    const chapterId = Number(artifact.metadata.chapter_id ?? 0);
    if (!chapterId) return;
    if (artifact.kind === "scene_draft") {
      const hasRemainingScenes = latest.artifacts.some((item) =>
        item.kind === "scene_draft" &&
        Number(item.metadata.chapter_id ?? 0) === chapterId &&
        ["pending", "changes_requested"].includes(item.status)
      );
      if (hasRemainingScenes) return;
    }
    const index = latest.tree.chapters.findIndex((item) => item.id === chapterId);
    const next = latest.tree.chapters[index + 1];
    if (!next) {
      setNotice("正文已通过并写入章节；全部章节正文已完成，已进入全文审阅");
      return;
    }
    if (latest.state.generation_mode === "manual") {
      setNotice(`正文已通过并写入章节；可手动开始“${next.title}”`);
      return;
    }
    if (!ensureModelPermission()) return;
    setSelectedChapterId(next.id);
    if (latest.state.generation_mode === "automatic") {
      generate.mutate({ phase: "drafting", chapterId: next.id });
      setNotice(`正文已通过并写入章节；正在自动开始“${next.title}”`);
      return;
    }
    setContinuation({ chapterId: next.id, seconds: latest.state.countdown_seconds });
  }

  function uploadStyleReference(file: File) {
    if (!ensureModelPermission()) return;
    styleReference.mutate(file);
  }

  return (
    <section className="studio-page">
      {notice ? <button className="toast" type="button" onClick={() => setNotice("")}><span>{notice}</span><X size={14} /></button> : null}
      <header className="studio-toolbar">
        <div className="project-heading">
          <h1>{overview.project.title}</h1>
          <span>{overview.state.stage_label}</span>
        </div>
        <div className="toolbar-controls">
          <label className="compact-select"><span>路由</span><select value={overview.state.routing_strategy} onChange={(event) => updateState.mutate({ routing_strategy: event.target.value })}>
            <option value="balanced">均衡</option><option value="quality">质量</option><option value="cost">成本</option><option value="speed">速度</option>
          </select></label>
          <label className="compact-select"><span>审核</span><select value={overview.state.review_granularity} onChange={(event) => updateState.mutate({ review_granularity: event.target.value })}>
            <option value="chapter">章级</option><option value="scene">场景级</option>
          </select></label>
          <label className="compact-select"><span>续写</span><select value={overview.state.generation_mode} onChange={(event) => updateState.mutate({ generation_mode: event.target.value })}>
            <option value="manual">手动</option><option value="automatic">自动</option><option value="countdown">倒计时</option>
          </select></label>
          {overview.state.generation_mode === "countdown" ? <label className="countdown-setting" title="批准当前章后，等待多少秒开始下一章"><Clock3 size={13} /><input type="number" min="0" max="3600" value={overview.state.countdown_seconds} onChange={(event) => updateState.mutate({ countdown_seconds: Number(event.target.value) })} /><span>秒</span></label> : null}
          <button className="icon-button" type="button" title="快照与导出" onClick={() => setSnapshotsOpen(true)}><History size={16} /></button>
        </div>
      </header>

      {continuation ? <div className="continuation-banner"><Clock3 size={15} /><span><strong>{continuation.seconds} 秒</strong>后开始下一章</span><button type="button" onClick={() => setContinuation(null)}><Pause size={14} />暂停</button><button type="button" onClick={() => { const chapterId = continuation.chapterId; setContinuation(null); generate.mutate({ phase: "drafting", chapterId }); }}><Play size={14} />立即开始</button></div> : null}
      {isContinuationProject && Boolean(continuationConfig.conflict_paused) ? <div className="continuation-conflict-banner"><AlertTriangle size={16} /><span><strong>续写已暂停</strong>存在重大设定或时间线冲突，请在“待审核项目”中选择处理方案。</span><button type="button" onClick={() => setRightTab("reviews")}>前往审核</button></div> : null}

      <div className="phase-strip" aria-label="创作阶段">
        {overview.stages.map((item, index) => {
          const currentIndex = overview.stages.findIndex((entry) => entry.key === overview.state.stage);
          const complete = index < currentIndex || overview.artifacts.some((artifact) => artifact.kind === item.key && artifact.status === "approved");
          return (
            <button key={item.key} type="button" className={`${phase === item.key ? "active" : ""} ${complete ? "complete" : ""}`} onClick={() => setSelectedPhase(item.key)}>
              <span>{complete ? <Check size={12} /> : index + 1}</span><b>{item.label}</b>
            </button>
          );
        })}
      </div>

      <div className="studio-workarea">
        <main className="stage-workspace">
          {["drafting", "review"].includes(phase) ? (
            <div className="writing-stage-shell">
              {isContinuationProject && phase === "drafting" && continuationConfig.continuation_start === "choose" ? (
                <div className="continuation-start-banner">
                  <div><BookOpenText size={17} /><span><strong>选择续写起点</strong>原文副本不会被修改；正文编辑区使用可恢复的工作副本。</span></div>
                  <button type="button" disabled={!lastImportedChapter || updateContinuation.isPending} onClick={() => { if (lastImportedChapter) setSelectedChapterId(lastImportedChapter.id); updateContinuation.mutate({ continuation_start: "current" }); }}>接着写当前章</button>
                  <button type="button" disabled={!firstFutureChapter || updateContinuation.isPending} onClick={() => { if (firstFutureChapter) setSelectedChapterId(firstFutureChapter.id); updateContinuation.mutate({ continuation_start: "next" }); }}>从下一章开始</button>
                </div>
              ) : null}
              {overview.chapter_tree_repair.can_repair ? (
                <div className="chapter-repair-banner">
                  <AlertTriangle size={17} />
                  <div><strong>检测到章节结构异常</strong><span>“{overview.chapter_tree_repair.suspect_chapters.map((item) => item.title).join("、")}”被误当成正文，将补齐 {missingChapterSummary}。原正文会永久保留。</span></div>
                  <button type="button" disabled={repairChapterTree.isPending} onClick={() => {
                    if (window.confirm(`修复章节结构？\n\n异常章节将移入回收状态，并补齐 ${missingChapterSummary}。操作前会创建永久特殊快照。`)) repairChapterTree.mutate();
                  }}>{repairChapterTree.isPending ? "修复中..." : "修复章节结构"}</button>
                </div>
              ) : null}
              {phase === "review" ? (
                <div className="review-edit-toolbar">
                  <div><FileCheck2 size={16} /><span><strong>全文审阅</strong>可继续逐章修改、保存或让 Agent 重写</span></div>
                  <button className="primary-button" type="button" disabled={generate.isPending} onClick={() => startGeneration("review")}><Sparkles size={15} />生成全文审阅</button>
                </div>
              ) : null}
              <WritingStage
                overview={overview}
                selectedChapterId={selectedChapterId}
                onSelectChapter={setSelectedChapterId}
                onSelection={setSelectedText}
                onGenerate={(mode, chapterId, selection) => {
                  const continuationMode = isContinuationProject && mode === "new" && continuationConfig.continuation_start === "current" && chapterId === lastImportedChapter?.id ? "continue" : mode;
                  startGeneration("drafting", { mode: continuationMode, chapterId, selection });
                }}
                onOpenArtifact={openEdit}
                generating={generate.isPending}
                onRefresh={refresh}
                onNotice={setNotice}
              />
            </div>
          ) : (
            <>
              <header className="stage-header">
                <div><span className="section-kicker">当前工作区</span><h2>{overview.stages.find((item) => item.key === phase)?.label}</h2><p>{phaseDescriptions[phase]}</p></div>
                <div className="stage-primary-actions">
                  {phase === "world" ? <label className="secondary-button file-action"><input type="file" accept=".txt,.md,.markdown,.docx" onChange={(event) => event.target.files?.[0] && uploadStyleReference(event.target.files[0])} /><FileUp size={15} />提取参考文风</label> : null}
                  {phase !== "idea" && phase !== "complete" && phase !== "continuation_import" ? (
                    <button className="primary-button" type="button" disabled={generate.isPending || styleReference.isPending} onClick={() => startGeneration()}>
                      {generate.isPending ? <LoaderCircle className="spin" size={16} /> : <Sparkles size={16} />}
                      {generate.isPending ? "Agent 正在工作" : artifacts.length ? "生成整阶段新版本" : "开始生成"}
                    </button>
                  ) : null}
                </div>
              </header>

              {overview.state.entry_mode === "outline" && phase === "chapters" ? (
                <OutlineImportPanel
                  text={outlineText}
                  onText={setOutlineText}
                  onPreview={async () => setOutlinePreview(await studioApi.previewOutline(projectId, outlineText))}
                  onFile={async (file) => setOutlinePreview(await studioApi.previewOutlineFile(projectId, file))}
                />
              ) : null}

              {isContinuationProject && phase === "continuation_plan" ? <ContinuationSettingsPanel overview={overview} onSave={(payload) => updateContinuation.mutate(payload)} saving={updateContinuation.isPending} /> : null}

              {phase !== "idea" && phase !== "complete" && phase !== "continuation_import" ? (
                <label className="generation-note"><WandSparkles size={15} /><input value={instruction} onChange={(event) => setInstruction(event.target.value)} placeholder="补充本阶段要求" /></label>
              ) : null}

              <div className="artifact-list">
                {artifacts.length === 0 ? <div className="empty-stage"><FileText size={24} /><span>暂无内容</span></div> : null}
                {artifacts.map((artifact) => (
                  <article key={artifact.id} className={`artifact-card status-${artifact.status}`}>
                    <header>
                      <div><span>{statusLabel(artifact.status)}</span><h3>{artifact.title}</h3><small>版本 {artifact.version_number} · {sourceLabel(artifact.source)}</small></div>
                      <div className="artifact-actions">
                        {!artifact.metadata.readonly ? <button type="button" className="icon-button subtle" title="编辑" onClick={() => openEdit(artifact)}><Pencil size={15} /></button> : null}
                        {artifact.metadata.agent_name && ["world", "characters", "plot", "volumes", "chapters", "continuation_analysis", "continuation_outline", "continuation_plan"].includes(artifact.kind) ? <button type="button" className="icon-button subtle" title="只重新生成这一项" disabled={generate.isPending} onClick={() => startGeneration(artifact.kind, { agentName: String(artifact.metadata.agent_name) })}><RefreshCw size={15} /></button> : null}
                        {artifact.status !== "approved" && artifact.status !== "rejected" ? (
                          <>
                            <button type="button" className="secondary-button" disabled={decide.isPending} onClick={() => decide.mutate({ artifact, action: "request_changes" })}>要求修改</button>
                            <button type="button" className="approve-button" disabled={decide.isPending} onClick={() => approveArtifact(artifact)}><Check size={15} />通过</button>
                          </>
                        ) : null}
                      </div>
                    </header>
                    {artifact.metadata.conflict_level === "major" ? <div className="conflict-badge major"><AlertTriangle size={14} />重大设定冲突，需要你决定</div> : null}
                    {artifact.metadata.conflict_level === "minor" ? <div className="conflict-badge minor"><CheckCircle2 size={14} />轻微冲突已自动校正并标记</div> : null}
                    <div className="artifact-content">{artifact.content}</div>
                    {artifact.notes ? <footer>{artifact.notes}</footer> : null}
                  </article>
                ))}
              </div>
            </>
          )}
        </main>

        <aside className="context-rail">
          <div className="rail-tabs">
            <RailTab icon={MessageSquareText} label="对话" active={rightTab === "chat"} onClick={() => setRightTab("chat")} />
            <RailTab icon={FileCheck2} label="审核" count={pending.length} active={rightTab === "reviews"} onClick={() => setRightTab("reviews")} />
            <RailTab icon={Activity} label="进度" active={rightTab === "progress"} onClick={() => setRightTab("progress")} />
            <RailTab icon={LibraryBig} label="资料" active={rightTab === "library"} onClick={() => setRightTab("library")} />
            <RailTab icon={CircleDollarSign} label="费用" active={rightTab === "cost"} onClick={() => setRightTab("cost")} />
          </div>
          {rightTab === "chat" ? <ChatPanel overview={overview} value={chatText} onChange={setChatText} onSend={() => sendChat.mutate()} sending={sendChat.isPending} onProposal={(messageId, action) => proposal.mutate({ messageId, action })} /> : null}
          {rightTab === "reviews" ? <ReviewPanel items={pending} approving={decide.isPending} onOpen={(artifact) => { setSelectedPhase(["revision_proposal", "scene_draft"].includes(artifact.kind) ? "drafting" : artifact.kind); openEdit(artifact); }} onApprove={approveArtifact} /> : null}
          {rightTab === "progress" ? <ProgressPanel overview={overview} /> : null}
          {rightTab === "library" ? <LibraryPanel overview={overview} /> : null}
          {rightTab === "cost" ? <CostPanel overview={overview} onUpdate={(value) => updateState.mutate(value)} /> : null}
        </aside>
      </div>

      {editing ? (
        <div className="modal-backdrop" onMouseDown={() => setEditing(null)}>
          <section className="modal artifact-editor" onMouseDown={(event) => event.stopPropagation()}>
            <header><div><h2>审核、批注与版本比较</h2><span>保存后生成版本 {Math.max(...versions.map((item) => item.version_number), editing.version_number) + 1}</span></div><button className="icon-button subtle" aria-label="关闭审核编辑器" onClick={() => setEditing(null)}><X size={17} /></button></header>
            <div className="version-toolbar"><GitCompareArrows size={15} /><span>对比版本</span><select value={compareVersionId ?? ""} onChange={(event) => setCompareVersionId(Number(event.target.value))}>{versions.map((item) => <option key={item.id} value={item.id}>版本 {item.version_number} · {statusLabel(item.status)}</option>)}</select>{compareVersionId ? <button type="button" className="secondary-button" onClick={() => { const old = versions.find((item) => item.id === compareVersionId); if (old) { setEditTitle(old.title); setEditText(old.content); setEditNotes(old.notes); } }}><RotateCcw size={14} />恢复到编辑区</button> : null}</div>
            <div className="version-compare-grid">
              <section><header><strong>当前编辑</strong><span>{editText.replace(/\s/g, "").length} 字</span></header><label><span>标题</span><input value={editTitle} onChange={(event) => setEditTitle(event.target.value)} /></label><textarea value={editText} onChange={(event) => setEditText(event.target.value)} /></section>
              <section><header><strong>历史版本</strong><span>只读</span></header><label><span>标题</span><input readOnly value={versions.find((item) => item.id === compareVersionId)?.title ?? ""} /></label><textarea readOnly value={versions.find((item) => item.id === compareVersionId)?.content ?? ""} /></section>
            </div>
            <label className="artifact-notes"><span>审核批注</span><textarea rows={3} value={editNotes} onChange={(event) => setEditNotes(event.target.value)} placeholder="记录修改理由、待核问题或给下一轮 Agent 的意见" /></label>
            <footer><button className="secondary-button" onClick={() => setEditing(null)}>取消</button><button className="secondary-button" disabled={decide.isPending || hasUnsavedArtifactEdits} onClick={() => decide.mutate({ artifact: editing, action: "request_changes" })}>要求修改</button><button className="primary-button" disabled={saveArtifact.isPending || !hasUnsavedArtifactEdits} onClick={() => saveArtifact.mutate()}><Save size={15} />保存新版本</button><button className="approve-button" title={hasUnsavedArtifactEdits ? "请先保存当前编辑" : "通过并写入"} disabled={decide.isPending || hasUnsavedArtifactEdits} onClick={() => approveArtifact(editing)}><Check size={15} />{["drafting", "revision_proposal", "scene_draft"].includes(editing.kind) ? "通过并写入正文" : "通过审核"}</button></footer>
          </section>
        </div>
      ) : null}

      {conflictArtifact ? <div className="modal-backdrop" onMouseDown={() => setConflictArtifact(null)}><section className="modal conflict-dialog" onMouseDown={(event) => event.stopPropagation()}><header><div><h2>发现重大设定冲突</h2><span>{conflictArtifact.title}</span></div><button className="icon-button subtle" onClick={() => setConflictArtifact(null)}><X size={17} /></button></header><div><AlertTriangle size={22} /><p>系统不会替你决定。请选择本次写回采用哪一边，或先进入编辑器手工合并。</p></div><footer><button className="secondary-button" onClick={() => { const artifact = conflictArtifact; setConflictArtifact(null); decide.mutate({ artifact, action: "approve", resolution: "preserve_canon" }); }}>保留既有设定</button><button className="secondary-button" onClick={() => { const artifact = conflictArtifact; setConflictArtifact(null); openEdit(artifact); }}>手工合并</button><button className="primary-button" onClick={() => { const artifact = conflictArtifact; setConflictArtifact(null); decide.mutate({ artifact, action: "approve", resolution: "preserve_prose" }); }}>保留当前正文</button></footer></section></div> : null}

      {outlinePreview ? (
        <OutlinePreviewDialog preview={outlinePreview} onClose={() => setOutlinePreview(null)} onImport={() => importOutline.mutate()} importing={importOutline.isPending} />
      ) : null}
      {snapshotsOpen ? <SnapshotDialog overview={overview} onClose={() => setSnapshotsOpen(false)} onRefresh={refresh} onNotice={setNotice} /> : null}
    </section>
  );
}

function RailTab({ icon: Icon, label, count, active, onClick }: { icon: typeof Bot; label: string; count?: number; active: boolean; onClick: () => void }) {
  return <button type="button" className={active ? "active" : ""} onClick={onClick} title={label}><Icon size={15} /><span>{label}</span>{count ? <b>{count}</b> : null}</button>;
}

function ChatPanel({ overview, value, onChange, onSend, sending, onProposal }: { overview: StudioOverview; value: string; onChange: (value: string) => void; onSend: () => void; sending: boolean; onProposal: (messageId: number, action: "apply" | "reject") => void }) {
  const streamRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const stream = streamRef.current;
    if (stream) stream.scrollTop = stream.scrollHeight;
  }, [overview.messages.length]);
  return <div className="rail-panel chat-panel">
    <header><div><Bot size={16} /><strong>总编对话</strong></div><span>自动上下文</span></header>
    <div ref={streamRef} className="chat-stream" role="log" aria-label="对话消息" aria-live="polite">
      {overview.messages.length === 0 ? <div className="chat-empty"><MessageSquareText size={22} /><span>开始对话</span></div> : null}
      {overview.messages.map((message) => <div key={message.id} className={`chat-message ${message.role}`}>
        <div>{message.content}</div>
        {message.role === "assistant" ? <small>{message.model_name} · {message.context_scope}</small> : null}
        {message.proposal_status === "pending" ? <div className="proposal-actions"><span>修改提案待确认</span><button onClick={() => onProposal(message.id, "reject")}>拒绝</button><button className="approve" onClick={() => onProposal(message.id, "apply")}>应用</button></div> : null}
      </div>)}
    </div>
    <form className="chat-composer" onSubmit={(event) => { event.preventDefault(); if (value.trim()) onSend(); }}>
      <textarea rows={3} value={value} onChange={(event) => onChange(event.target.value)} placeholder="询问、分析或提出修改要求" />
      <div><span>项目 · 阶段 · 章节 · 选区</span><button type="submit" className="send-button" disabled={sending || !value.trim()} title="发送">{sending ? <LoaderCircle className="spin" size={16} /> : <Send size={16} />}</button></div>
    </form>
  </div>;
}

function ReviewPanel({ items, approving, onOpen, onApprove }: { items: Artifact[]; approving: boolean; onOpen: (item: Artifact) => void; onApprove: (item: Artifact) => void }) {
  return <div className="rail-panel"><header><div><FileCheck2 size={16} /><strong>待审核</strong></div><span>{items.length} 项</span></header><div className="rail-list">
    {items.length === 0 ? <div className="rail-empty"><CheckCircle2 size={22} /><span>没有待审核内容</span></div> : null}
    {items.map((item) => { const writesManuscript = ["drafting", "revision_proposal", "scene_draft"].includes(item.kind); const approveLabel = writesManuscript ? "通过并写入正文" : "通过"; return <article key={item.id} className="review-item"><button type="button" onClick={() => onOpen(item)}><span>{artifactKindLabel(item.kind)}</span><strong>{item.title}</strong><small>版本 {item.version_number} · 点击查看和编辑</small></button><button className="approve-button review-approve" title={approveLabel} aria-label={approveLabel} disabled={approving} onClick={() => onApprove(item)}><Check size={14} /><span>{approveLabel}</span></button></article>; })}
  </div></div>;
}

function ProgressPanel({ overview }: { overview: StudioOverview }) {
  return <div className="rail-panel"><header><div><Activity size={16} /><strong>执行进度</strong></div><span>{overview.jobs.length} 条</span></header><div className="rail-list">
    {overview.jobs.length === 0 ? <div className="rail-empty"><Activity size={22} /><span>暂无任务</span></div> : null}
    {overview.jobs.map((job) => <article key={job.id} className="job-item"><div><span className={`job-dot ${job.status}`} /><strong>{job.label}</strong><small>{job.model_name}</small></div><div className="progress-track"><i style={{ width: `${job.progress}%` }} /></div><footer><span>{job.status === "completed" ? "已完成" : job.status === "failed" ? "失败" : `${job.progress}%`}</span><small title={job.model_reason}>{job.model_reason}</small></footer></article>)}
  </div></div>;
}

function LibraryPanel({ overview }: { overview: StudioOverview }) {
  const rows = [["人物与实体", overview.library_counts.entities], ["时间线事件", overview.library_counts.timeline], ["伏笔", overview.library_counts.foreshadows], ["文风规则", overview.library_counts.style_guides]];
  return <div className="rail-panel"><header><div><LibraryBig size={16} /><strong>资料库</strong></div><span>自动更新</span></header><div className="library-metrics">{rows.map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}</div><div className="memory-state"><CheckCircle2 size={15} /><span>记忆模式：{overview.state.memory_mode === "automatic" ? "自动更新" : "确认后更新"}</span></div></div>;
}

function CostPanel({ overview, onUpdate }: { overview: StudioOverview; onUpdate: (value: Record<string, unknown>) => void }) {
  const [budget, setBudget] = useState(overview.state.budget_limit?.toString() ?? "");
  return <div className="rail-panel"><header><div><CircleDollarSign size={16} /><strong>费用</strong></div><span>{overview.usage.currency}</span></header><div className="cost-summary"><strong>{overview.usage.spent.toFixed(4)}</strong><span>/ {overview.usage.limit?.toFixed(2) ?? "未设置"}</span><div className={overview.usage.warning ? "budget-bar warning" : "budget-bar"}><i style={{ width: `${Math.min(100, overview.usage.percent)}%` }} /></div><small>{overview.usage.tokens.toLocaleString()} tokens · {overview.usage.invocations} 次调用</small></div><label className="budget-input"><span>项目预算</span><div><input type="number" min="0.01" step="0.01" value={budget} onChange={(event) => setBudget(event.target.value)} /><button onClick={() => onUpdate({ budget_limit: budget ? Number(budget) : null, budget_paused: false })}>保存</button></div></label><div className="budget-rules"><span>70% 提醒</span><span>110% 任务结束后暂停</span></div>{overview.usage.paused ? <button className="primary-button full" onClick={() => onUpdate({ budget_paused: false })}>确认继续生成</button> : null}</div>;
}

function WritingStage({ overview, selectedChapterId, onSelectChapter, onSelection, onGenerate, onOpenArtifact, generating, onRefresh, onNotice }: { overview: StudioOverview; selectedChapterId: number | null; onSelectChapter: (id: number) => void; onSelection: (text: string) => void; onGenerate: (mode: string, chapterId: number, selection?: string) => void; onOpenArtifact: (artifact: Artifact) => void; generating: boolean; onRefresh: () => Promise<unknown>; onNotice: (value: string) => void }) {
  const selected = overview.tree.chapters.find((item) => item.id === selectedChapterId) ?? overview.tree.chapters[0];
  const [title, setTitle] = useState(selected?.title ?? "");
  const [content, setContent] = useState(selected?.content ?? "");
  const [dirty, setDirty] = useState(false);
  const [selection, setSelection] = useState("");
  const hydratedChapter = useRef({ id: selected?.id ?? null, revision: selected?.revision ?? null });
  const queryClient = useQueryClient();
  const save = useMutation({
    mutationFn: () => studioApi.autosaveChapter(selected!, title, content),
    onSuccess: async () => {
      setDirty(false);
      await queryClient.invalidateQueries({ queryKey: ["studio-project", overview.project.id] });
    },
    onError: (reason: Error) => onNotice(reason.message)
  });
  const createChapter = useMutation({
    mutationFn: () => {
      const volume = overview.tree.volumes[overview.tree.volumes.length - 1];
      if (!volume) throw new Error("请先建立分卷");
      const nextPosition = Math.max(0, ...overview.tree.chapters.filter((item) => item.volume_id === volume.id).map((item) => item.position)) + 1;
      return studioApi.createChapter(volume.id, `第${overview.tree.chapters.length + 1}章`, nextPosition);
    },
    onSuccess: async (chapter) => {
      onSelectChapter(chapter.id);
      onNotice(`已新增“${chapter.title}”，可直接修改标题和正文`);
      await onRefresh();
    },
    onError: (reason: Error) => onNotice(reason.message)
  });
  const deleteChapter = useMutation({
    mutationFn: () => studioApi.deleteChapter(selected!),
    onSuccess: async () => {
      const replacement = overview.tree.chapters.find((item) => item.id !== selected?.id);
      if (replacement) onSelectChapter(replacement.id);
      onNotice("章节已移入回收站");
      await onRefresh();
    },
    onError: (reason: Error) => onNotice(reason.message)
  });
  useEffect(() => {
    if (!selected) return;
    const chapterChanged = hydratedChapter.current.id !== selected.id;
    const serverChanged = hydratedChapter.current.revision !== selected.revision;
    if (!chapterChanged && (!serverChanged || dirty)) return;
    setTitle(selected.title);
    setContent(selected.content);
    setDirty(false);
    hydratedChapter.current = { id: selected.id, revision: selected.revision };
  }, [selected?.id, selected?.revision, dirty]);
  useEffect(() => {
    if (!dirty || !selected) return;
    const timer = window.setTimeout(() => save.mutate(), 1600);
    return () => window.clearTimeout(timer);
  }, [dirty, title, content, selected?.id]);
  if (!selected) return <div className="empty-stage"><BookOpenText size={24} /><span>卷章大纲审核通过后，正文工作区会自动建立。</span></div>;
  const selectedScenes = overview.tree.scenes.filter((scene) => scene.chapter_id === selected.id);
  return <div className="writing-workspace">
    <aside className="chapter-tree"><header><span>卷章</span><div><b>{overview.tree.chapters.length}</b><button type="button" title="新增章节" aria-label="新增章节" disabled={createChapter.isPending} onClick={() => createChapter.mutate()}><Plus size={14} /></button><button type="button" title="删除当前章节" aria-label="删除当前章节" disabled={overview.tree.chapters.length <= 1 || deleteChapter.isPending} onClick={() => { if (window.confirm(`将“${selected.title}”移入回收站？`)) deleteChapter.mutate(); }}><Trash2 size={14} /></button></div></header>{overview.tree.volumes.map((volume) => <section key={volume.id}><strong>{volume.title}</strong>{overview.tree.chapters.filter((chapter) => chapter.volume_id === volume.id).map((chapter) => <button key={chapter.id} className={chapter.id === selected.id ? "active" : ""} onClick={() => onSelectChapter(chapter.id)}><FileText size={13} /><span>{chapter.title}</span><small>{chapter.word_count}</small></button>)}</section>)}</aside>
    <section className="manuscript-pane">{overview.state.review_granularity === "scene" ? <div className="scene-review-strip"><span>场景审核</span>{selectedScenes.map((scene) => { const artifact = overview.artifacts.find((item) => item.kind === "scene_draft" && Number(item.metadata.scene_id) === scene.id && item.status !== "superseded"); return <button key={scene.id} type="button" className={artifact?.status === "approved" ? "approved" : artifact ? "pending" : "empty"} disabled={!artifact} onClick={() => artifact && onOpenArtifact(artifact)}><span>{scene.title}</span><small>{artifact ? statusLabel(artifact.status) : "未生成"}</small></button>; })}</div> : null}<header><input value={title} onChange={(event) => { setTitle(event.target.value); setDirty(true); }} /><div><span className={dirty ? "save-state dirty" : "save-state"}>{save.isPending ? "保存中" : dirty ? "未保存" : "已保存"}</span><button className="icon-button subtle" title="保存" onClick={() => save.mutate()}><Save size={15} /></button><button className="secondary-button" disabled={generating} onClick={() => onGenerate("full_rewrite", selected.id)}>全文重写</button><button className="primary-button" disabled={generating} onClick={() => onGenerate("new", selected.id)}>{generating ? <LoaderCircle className="spin" size={15} /> : <Sparkles size={15} />}续写正文</button></div></header><textarea className="manuscript-editor" value={content} onChange={(event) => { setContent(event.target.value); setDirty(true); }} onSelect={(event) => { const target = event.currentTarget; const value = target.value.slice(target.selectionStart, target.selectionEnd); setSelection(value); onSelection(value); }} placeholder="正文" /><footer><span>{content.replace(/\s/g, "").length.toLocaleString()} 字</span><div><button disabled={!selection || generating} onClick={() => onGenerate("local_revision", selected.id, selection)}><WandSparkles size={14} />局部修改</button><button disabled={generating} onClick={() => onGenerate("variants", selected.id, selection)}><SplitSquareVertical size={14} />多个方案</button></div></footer></section>
  </div>;
}

function OutlineImportPanel({ text, onText, onPreview, onFile }: { text: string; onText: (value: string) => void; onPreview: () => void; onFile: (file: File) => void }) {
  return <section className="outline-import"><header><FileInput size={17} /><strong>导入大纲</strong><label className="file-button"><input type="file" accept=".txt,.md,.markdown,.docx" onChange={(event) => event.target.files?.[0] && onFile(event.target.files[0])} />选择文件</label></header><textarea rows={12} value={text} onChange={(event) => onText(event.target.value)} placeholder="粘贴卷、章、场景大纲" /><footer><span>TXT · Markdown · Word</span><button className="primary-button" disabled={!text.trim()} onClick={onPreview}>解析并预览</button></footer></section>;
}

function ContinuationSettingsPanel({ overview, onSave, saving }: { overview: StudioOverview; onSave: (payload: Record<string, unknown>) => void; saving: boolean }) {
  const config = overview.state.config;
  const [targetWords, setTargetWords] = useState(String(config.target_words ?? overview.project.target_words ?? ""));
  const [targetChapters, setTargetChapters] = useState(String(config.target_chapters ?? ""));
  const [targetVolumes, setTargetVolumes] = useState(String(config.target_volumes ?? ""));
  const [directionMode, setDirectionMode] = useState(String(config.direction_mode ?? "switchable"));
  const [userOutline, setUserOutline] = useState(String(config.user_outline ?? ""));

  return <section className="continuation-settings-panel"><header><div><Settings2 size={16} /><strong>续写目标与方向</strong></div><span>{config.target_mode === "ai" ? "等待 AI 建议，可随时改为手动值" : "作者设定"}</span></header><div className="continuation-settings-grid"><label><span>目标总字数</span><input type="number" min="1" value={targetWords} onChange={(event) => setTargetWords(event.target.value)} /></label><label><span>目标总章节</span><input type="number" min="1" placeholder="由 AI 建议" value={targetChapters} onChange={(event) => setTargetChapters(event.target.value)} /></label><label><span>目标总卷数</span><input type="number" min="1" placeholder="由 AI 建议" value={targetVolumes} onChange={(event) => setTargetVolumes(event.target.value)} /></label><label><span>剧情方向</span><select value={directionMode} onChange={(event) => setDirectionMode(event.target.value)}><option value="switchable">作者与 AI 可切换</option><option value="user">以作者大纲为准</option><option value="ai">由 AI 提议</option></select></label><label className="wide"><span>作者后续大纲</span><textarea rows={3} value={userOutline} onChange={(event) => setUserOutline(event.target.value)} placeholder="可留空并审核 AI 的方向提案" /></label></div><footer><span>已导入 {Number(config.imported_volume_count ?? 0)} 卷 · {Number(config.imported_chapter_count ?? 0)} 章 · {Number(config.imported_words ?? 0).toLocaleString()} 字</span><button type="button" className="secondary-button" disabled={saving || !targetWords} onClick={() => onSave({ target_words: Number(targetWords), target_chapters: targetChapters ? Number(targetChapters) : undefined, target_volumes: targetVolumes ? Number(targetVolumes) : undefined, direction_mode: directionMode, user_outline: userOutline })}>{saving ? "保存中..." : "保存目标"}</button></footer></section>;
}

function OutlinePreviewDialog({ preview, onClose, onImport, importing }: { preview: OutlinePreview; onClose: () => void; onImport: () => void; importing: boolean }) {
  return <div className="modal-backdrop" onMouseDown={onClose}><section className="modal outline-preview-modal" onMouseDown={(event) => event.stopPropagation()}><header><div><h2>大纲结构预览</h2><span>{preview.volume_count} 卷 · {preview.chapter_count} 章 · {preview.scene_count} 场景</span></div><button className="icon-button subtle" onClick={onClose}><X size={17} /></button></header><div className="outline-tree-preview">{preview.volumes.map((volume, index) => <section key={`${volume.title}-${index}`}><strong>{volume.title}</strong>{volume.chapters.map((chapter, chapterIndex) => <div key={`${chapter.title}-${chapterIndex}`}><span>{chapter.title}</span><small>{chapter.scenes.length} 场景</small></div>)}</section>)}</div>{preview.warnings.map((warning) => <div className="preview-warning" key={warning}>{warning}</div>)}<footer><button className="secondary-button" onClick={onClose}>返回修改</button><button className="primary-button" disabled={importing} onClick={onImport}>{importing ? "导入中..." : "确认导入"}</button></footer></section></div>;
}

function SnapshotDialog({ overview, onClose, onRefresh, onNotice }: { overview: StudioOverview; onClose: () => void; onRefresh: () => Promise<unknown>; onNotice: (value: string) => void }) {
  const [label, setLabel] = useState("");
  const create = useMutation({ mutationFn: () => studioApi.createSnapshot(overview.project.id, label || "手动快照", "作者手动创建", true), onSuccess: async () => { setLabel(""); await onRefresh(); }, onError: (reason: Error) => onNotice(reason.message) });
  const restore = useMutation({ mutationFn: (id: number) => studioApi.restoreSnapshot(overview.project.id, id), onSuccess: onRefresh, onError: (reason: Error) => onNotice(reason.message) });
  return <div className="modal-backdrop" onMouseDown={onClose}><section className="modal snapshot-modal" onMouseDown={(event) => event.stopPropagation()}><header><div><h2>快照与导出</h2><span>普通快照 {overview.snapshots.filter((item) => !item.permanent).length}/3</span></div><button className="icon-button subtle" onClick={onClose}><X size={17} /></button></header><div className="export-strip"><a href={studioApi.exportUrl(overview.project.id, "book_text")} download><FileText size={16} />TXT</a><a href={studioApi.exportUrl(overview.project.id, "book_markdown")} download><Download size={16} />Markdown</a><a href={studioApi.exportUrl(overview.project.id, "book_pdf")} download><FileCheck2 size={16} />PDF</a></div><div className="snapshot-create"><input value={label} onChange={(event) => setLabel(event.target.value)} placeholder="特殊快照名称" /><button className="primary-button" onClick={() => create.mutate()}><Save size={15} />保存特殊快照</button></div><div className="snapshot-list">{overview.snapshots.map((item) => <article key={item.id}><span className={item.permanent ? "special" : "automatic"}>{item.permanent ? "特殊" : "自动"}</span><div><strong>{item.label}</strong><small>{item.reason || new Date(item.created_at).toLocaleString("zh-CN")}</small></div><button className="icon-button subtle" title="恢复" onClick={() => { if (window.confirm(`恢复到“${item.label}”？`)) restore.mutate(item.id); }}><Undo2 size={15} /></button></article>)}</div></section></div>;
}

function statusLabel(status: Artifact["status"]) { return ({ pending: "待审核", approved: "已通过", changes_requested: "需修改", rejected: "已拒绝", superseded: "旧版本" })[status]; }
function artifactKindLabel(kind: string) { return ({ drafting: "章节正文", revision_proposal: "正文修改方案", scene_draft: "场景正文", world: "世界观", characters: "人物关系", plot: "剧情伏笔", volumes: "分卷大纲", chapters: "章节大纲", continuation_original: "原始只读副本", continuation_analysis: "原文资料分析", continuation_outline: "反向补建大纲", continuation_plan: "续写规划", continuation_direction: "作者续写方向", review: "全文审阅" } as Record<string, string>)[kind] ?? kind; }
function sourceLabel(source: string) { return ({ ai: "AI 生成", user: "人工修改", import: "导入", ai_chat: "对话提案" } as Record<string, string>)[source] ?? source; }
function formatMissingChapters(numbers: number[]) {
  if (numbers.length === 0) return "缺失章节";
  if (numbers.length === 1) return `第 ${numbers[0]} 章`;
  const consecutive = numbers.every((number, index) => index === 0 || number === numbers[index - 1] + 1);
  if (consecutive) return `第 ${numbers[0]} 至 ${numbers[numbers.length - 1]} 章（共 ${numbers.length} 章）`;
  return `缺失的 ${numbers.length} 个章节`;
}
