import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AnimatePresence, motion } from "framer-motion";
import {
  ArrowRight,
  BookOpenText,
  CheckSquare2,
  Clock3,
  ClipboardPaste,
  FileUp,
  FolderOpen,
  MoreHorizontal,
  Plus,
  Search,
  Trash2,
  X
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import { studioApi } from "../api/studio";
import { ProjectDialogForm } from "../components/ProjectDialogForm";
import { useUiStore } from "../stores/ui";
import {
  dialogBackdrop,
  dialogCard,
  fadeInUp,
  MAX_STAGGER_ITEMS,
  staggerContainer
} from "../utils/motion";

export function HomePage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const selectedProjectId = useUiStore((state) => state.selectedProjectId);
  const setProject = useUiStore((state) => state.setProject);
  const { data: projects = [], isLoading } = useQuery({
    queryKey: ["studio-projects"],
    queryFn: studioApi.dashboard
  });
  const [dialogOpen, setDialogOpen] = useState(false);
  const [continuationOpen, setContinuationOpen] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const [query, setQuery] = useState("");
  const [view, setView] = useState<"recent" | "all">("recent");

  const remove = useMutation({
    mutationFn: studioApi.deleteProject,
    onSuccess: async (_, deletedProjectId) => {
      setDeleteError("");
      if (selectedProjectId === deletedProjectId) {
        setProject(projects.find((project) => project.id !== deletedProjectId)?.id ?? null);
      }
      await queryClient.invalidateQueries({ queryKey: ["studio-projects"] });
    },
    onError: (reason: Error) => setDeleteError(`删除失败：${reason.message}`)
  });
  const createContinuation = useMutation({
    mutationFn: ({ file, payload }: { file: File | null; payload: Record<string, unknown> }) =>
      file ? studioApi.createContinuationFile(file, payload) : studioApi.createContinuation(payload),
    onSuccess: async (overview) => {
      setProject(overview.project.id);
      setContinuationOpen(false);
      await queryClient.invalidateQueries({ queryKey: ["studio-projects"] });
      navigate(`/studio/${overview.project.id}`);
    }
  });

  function openProject(id: number) {
    setProject(id);
    navigate(`/studio/${id}`);
  }

  const visibleProjects = projects
    .filter((project) => `${project.title}${project.summary}${project.stage_label}`.toLowerCase().includes(query.trim().toLowerCase()))
    .slice(0, view === "recent" ? 8 : projects.length);

  return (
    <section className="projects-page">
      <header className="page-toolbar">
        <div>
          <h1>项目</h1>
          <span>{projects.length} 本小说</span>
        </div>
        <div className="page-toolbar-actions">
          <label className="toolbar-search">
            <Search size={14} />
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索项目" aria-label="搜索项目" />
          </label>
          <button type="button" className="secondary-button" onClick={() => setContinuationOpen(true)}>
            <FileUp size={16} /> 导入半成品续写
          </button>
          <button type="button" className="primary-button" onClick={() => setDialogOpen(true)}>
            <Plus size={16} /> 新建项目
          </button>
        </div>
      </header>

      <nav className="project-view-tabs" aria-label="项目视图">
        <button type="button" className={view === "recent" ? "active" : ""} onClick={() => setView("recent")}>最近打开</button>
        <button type="button" className={view === "all" ? "active" : ""} onClick={() => setView("all")}>全部项目</button>
      </nav>

      <div className="project-list-head" aria-hidden="true">
        <span>项目名称</span><span>创作阶段</span><span>章节进度</span><span>完成字数<i className="sr-only">待审核</i></span><span>最后编辑</span><span>操作</span>
      </div>
      {deleteError ? <div className="form-error project-delete-error" role="alert">{deleteError}</div> : null}
      <motion.div className="project-list" variants={staggerContainer} initial="initial" animate="animate">
        {isLoading ? <div className="loading-line">读取项目中...</div> : null}
        {!isLoading && projects.length === 0 ? (
          <button type="button" className="empty-projects" onClick={() => setDialogOpen(true)}>
            <BookOpenText size={28} />
            <strong>新建第一本小说</strong>
          </button>
        ) : null}
        {!isLoading && projects.length > 0 && visibleProjects.length === 0 ? <div className="search-empty">没有匹配“{query}”的项目</div> : null}
        {visibleProjects.map((project, index) => {
          const progress = project.target_words > 0 ? Math.min(100, Math.round((project.completed_words / project.target_words) * 100)) : 0;
          return (
          <motion.article
            key={project.id}
            className="project-row"
            variants={index < MAX_STAGGER_ITEMS ? fadeInUp : undefined}
            onDoubleClick={() => openProject(project.id)}
          >
            <button type="button" className="project-name" onClick={() => openProject(project.id)}>
              <span className="book-glyph"><BookOpenText size={17} /></span>
              <span><strong>{project.title}</strong><small>{project.summary}</small></span>
            </button>
            <span className="stage-cell">{project.stage_label}</span>
            <span className="project-progress"><b>{progress}%</b><i><span style={{ width: `${progress}%` }} /></i></span>
            <span className="metric-cell">{project.completed_words.toLocaleString()} <small>字</small>{project.pending_reviews ? <em><CheckSquare2 size={12} />{project.pending_reviews} 待审核</em> : null}</span>
            <span className="time-cell"><Clock3 size={13} /> {formatTime(project.updated_at)}</span>
            <div className="row-actions">
              <button type="button" className="text-row-action" title="继续写作" onClick={() => openProject(project.id)}>继续写作<ArrowRight size={14} /></button>
              <button type="button" className="icon-button subtle danger" title="删除" onClick={() => {
                if (window.confirm(`删除“${project.title}”？`)) {
                  setDeleteError("");
                  remove.mutate(project.id);
                }
              }}><Trash2 size={15} /></button>
            </div>
          </motion.article>
        );})}
      </motion.div>

      <AnimatePresence>
        {dialogOpen ? (
          <motion.div className="modal-backdrop" role="presentation" onMouseDown={() => setDialogOpen(false)} {...dialogBackdrop}>
            <motion.section className="modal create-project-modal" role="dialog" aria-modal="true" onMouseDown={(event) => event.stopPropagation()} {...dialogCard}>
              <header>
                <div><h2>新建小说</h2><span>快速创建或填写详细设置</span></div>
                <button type="button" className="icon-button subtle" onClick={() => setDialogOpen(false)} title="关闭"><X size={17} /></button>
              </header>
              <ProjectDialogForm
                onCancel={() => setDialogOpen(false)}
                onSuccess={(overview) => {
                  setProject(overview.project.id);
                  setDialogOpen(false);
                  navigate(`/studio/${overview.project.id}`);
                }}
              />
            </motion.section>
          </motion.div>
        ) : null}
      </AnimatePresence>
      <AnimatePresence>
        {continuationOpen ? (
          <ContinuationImportDialog
            projects={projects}
            pending={createContinuation.isPending}
            error={createContinuation.error instanceof Error ? createContinuation.error.message : ""}
            onClose={() => setContinuationOpen(false)}
            onSubmit={(file, payload) => createContinuation.mutate({ file, payload })}
          />
        ) : null}
      </AnimatePresence>
    </section>
  );
}

type ContinuationSource = "file" | "paste" | "project";

function ContinuationImportDialog({
  projects,
  pending,
  error,
  onClose,
  onSubmit
}: {
  projects: Array<{ id: number; title: string }>;
  pending: boolean;
  error: string;
  onClose: () => void;
  onSubmit: (file: File | null, payload: Record<string, unknown>) => void;
}) {
  const [source, setSource] = useState<ContinuationSource>("file");
  const [file, setFile] = useState<File | null>(null);
  const [title, setTitle] = useState("");
  const [text, setText] = useState("");
  const [sourceProjectId, setSourceProjectId] = useState<number | null>(projects[0]?.id ?? null);
  const [targetWords, setTargetWords] = useState("");
  const [targetChapters, setTargetChapters] = useState("");
  const [targetVolumes, setTargetVolumes] = useState("");
  const [userOutline, setUserOutline] = useState("");

  const sourceReady = source === "file" ? Boolean(file) : source === "paste" ? Boolean(text.trim()) : sourceProjectId !== null;

  function submit(event: FormEvent) {
    event.preventDefault();
    const payload: Record<string, unknown> = {
      title: title.trim(),
      target_words: targetWords ? Number(targetWords) : null,
      target_chapters: targetChapters ? Number(targetChapters) : null,
      target_volumes: targetVolumes ? Number(targetVolumes) : null,
      continuation_start: "choose",
      direction_mode: "switchable",
      user_outline: userOutline
    };
    if (source === "paste") Object.assign(payload, { text, source_name: "粘贴正文" });
    if (source === "project") Object.assign(payload, { source_project_id: sourceProjectId });
    onSubmit(source === "file" ? file : null, payload);
  }

  return (
    <motion.div className="modal-backdrop" role="presentation" onMouseDown={onClose} {...dialogBackdrop}>
      <motion.section className="modal continuation-import-modal" role="dialog" aria-modal="true" onMouseDown={(event) => event.stopPropagation()} {...dialogCard}>
        <header>
          <div><h2>导入半成品续写</h2><span>原文永久保留，解析结果逐项审核</span></div>
          <button type="button" className="icon-button subtle" onClick={onClose} title="关闭"><X size={17} /></button>
        </header>
        <form onSubmit={submit}>
          <div className="continuation-source-tabs" role="tablist">
            <button type="button" className={source === "file" ? "active" : ""} onClick={() => setSource("file")}><FileUp size={15} />上传文件</button>
            <button type="button" className={source === "paste" ? "active" : ""} onClick={() => setSource("paste")}><ClipboardPaste size={15} />粘贴正文</button>
            <button type="button" className={source === "project" ? "active" : ""} onClick={() => setSource("project")}><FolderOpen size={15} />已有项目</button>
          </div>
          <label><span>新项目书名</span><input autoFocus value={title} onChange={(event) => setTitle(event.target.value)} /></label>
          {source === "file" ? (
            <label className="continuation-file-drop"><input type="file" accept=".txt,.md,.markdown,.docx,.pdf" onChange={(event) => setFile(event.target.files?.[0] ?? null)} /><FileUp size={22} /><strong>{file?.name ?? "选择小说文件"}</strong><span>TXT · Markdown · Word · PDF，最大 10 MB</span></label>
          ) : null}
          {source === "paste" ? <label><span>小说正文</span><textarea rows={9} value={text} onChange={(event) => setText(event.target.value)} placeholder="粘贴包含卷章标题的半成品正文" /></label> : null}
          {source === "project" ? <label><span>来源项目</span><select value={sourceProjectId ?? ""} onChange={(event) => setSourceProjectId(Number(event.target.value))}><option value="" disabled>选择项目</option>{projects.map((project) => <option key={project.id} value={project.id}>{project.title}</option>)}</select></label> : null}
          <div className="continuation-targets">
            <label><span>目标总字数</span><input type="number" min="1" placeholder="由 AI 建议" value={targetWords} onChange={(event) => setTargetWords(event.target.value)} /></label>
            <label><span>目标总章节</span><input type="number" min="1" placeholder="由 AI 建议" value={targetChapters} onChange={(event) => setTargetChapters(event.target.value)} /></label>
            <label><span>目标总卷数</span><input type="number" min="1" placeholder="由 AI 建议" value={targetVolumes} onChange={(event) => setTargetVolumes(event.target.value)} /></label>
          </div>
          <label><span>后续方向或大纲（可选）</span><textarea rows={3} value={userOutline} onChange={(event) => setUserOutline(event.target.value)} placeholder="留空时由 AI 提议；导入后仍可修改和切换" /></label>
          <div className="continuation-rules"><span>续写起点：进入正文阶段时选择</span><span>冲突处理：完成当前任务后暂停</span></div>
          {error ? <div className="form-error">{error}</div> : null}
          <footer><button type="button" className="secondary-button" onClick={onClose}>取消</button><button type="submit" className="primary-button" disabled={!title.trim() || !sourceReady || pending}>{pending ? "导入解析中..." : "导入并创建项目"}</button></footer>
        </form>
      </motion.section>
    </motion.div>
  );
}

function formatTime(value: string) {
  const date = new Date(value);
  const now = new Date();
  if (date.toDateString() === now.toDateString()) {
    return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
  }
  return date.toLocaleDateString("zh-CN", { month: "2-digit", day: "2-digit" });
}
