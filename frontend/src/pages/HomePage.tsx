import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRight,
  BookOpenText,
  CheckSquare2,
  Clock3,
  FileInput,
  Lightbulb,
  MoreHorizontal,
  Plus,
  Trash2,
  X
} from "lucide-react";
import { useNavigate } from "react-router-dom";
import { studioApi } from "../api/studio";
import { useUiStore } from "../stores/ui";

const emptyForm = {
  title: "",
  idea: "",
  entry_mode: "creative" as "creative" | "outline",
  target_words: 100000,
  genre: "",
  theme: "",
  era: "",
  audience: "",
  chapter_count: 80,
  chapter_words: 2500,
  style_description: "",
  point_of_view: "第三人称限知",
  prohibited_content: ""
};

export function HomePage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const setProject = useUiStore((state) => state.setProject);
  const { data: projects = [], isLoading } = useQuery({
    queryKey: ["studio-projects"],
    queryFn: studioApi.dashboard
  });
  const [dialogOpen, setDialogOpen] = useState(false);
  const [details, setDetails] = useState(false);
  const [form, setForm] = useState(emptyForm);
  const [error, setError] = useState("");

  const create = useMutation({
    mutationFn: () => studioApi.createProject(form),
    onSuccess: async (overview) => {
      setProject(overview.project.id);
      setDialogOpen(false);
      setForm(emptyForm);
      await queryClient.invalidateQueries({ queryKey: ["studio-projects"] });
      navigate(`/studio/${overview.project.id}`);
    },
    onError: (reason: Error) => setError(reason.message)
  });
  const remove = useMutation({
    mutationFn: studioApi.deleteProject,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["studio-projects"] })
  });

  function openProject(id: number) {
    setProject(id);
    navigate(`/studio/${id}`);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    create.mutate();
  }

  return (
    <section className="projects-page">
      <header className="page-toolbar">
        <div>
          <h1>项目</h1>
          <span>{projects.length} 本小说</span>
        </div>
        <button type="button" className="primary-button" onClick={() => setDialogOpen(true)}>
          <Plus size={16} /> 新建项目
        </button>
      </header>

      <div className="project-list-head" aria-hidden="true">
        <span>书名</span><span>创作阶段</span><span>完成字数</span><span>待审核</span><span>最后编辑</span><span />
      </div>
      <div className="project-list">
        {isLoading ? <div className="loading-line">读取项目中...</div> : null}
        {!isLoading && projects.length === 0 ? (
          <button type="button" className="empty-projects" onClick={() => setDialogOpen(true)}>
            <BookOpenText size={28} />
            <strong>新建第一本小说</strong>
          </button>
        ) : null}
        {projects.map((project) => (
          <article key={project.id} className="project-row" onDoubleClick={() => openProject(project.id)}>
            <button type="button" className="project-name" onClick={() => openProject(project.id)}>
              <span className="book-glyph"><BookOpenText size={17} /></span>
              <span><strong>{project.title}</strong><small>{project.summary}</small></span>
            </button>
            <span className="stage-cell">{project.stage_label}</span>
            <span className="metric-cell">{project.completed_words.toLocaleString()} <small>/ {project.target_words.toLocaleString()}</small></span>
            <span className={project.pending_reviews ? "review-count active" : "review-count"}>
              <CheckSquare2 size={14} /> {project.pending_reviews}
            </span>
            <span className="time-cell"><Clock3 size={13} /> {formatTime(project.updated_at)}</span>
            <div className="row-actions">
              <button type="button" className="icon-button subtle" title="打开" onClick={() => openProject(project.id)}><ArrowRight size={16} /></button>
              <button type="button" className="icon-button subtle danger" title="删除" onClick={() => {
                if (window.confirm(`删除“${project.title}”？`)) remove.mutate(project.id);
              }}><Trash2 size={15} /></button>
            </div>
          </article>
        ))}
      </div>

      {dialogOpen ? (
        <div className="modal-backdrop" role="presentation" onMouseDown={() => setDialogOpen(false)}>
          <section className="modal create-project-modal" role="dialog" aria-modal="true" onMouseDown={(event) => event.stopPropagation()}>
            <header>
              <div><h2>新建小说</h2><span>{details ? "详细创建" : "快速创建"}</span></div>
              <button type="button" className="icon-button subtle" onClick={() => setDialogOpen(false)} title="关闭"><X size={17} /></button>
            </header>
            <form onSubmit={submit}>
              <div className="mode-switch">
                <button type="button" className={form.entry_mode === "creative" ? "active" : ""} onClick={() => setForm({ ...form, entry_mode: "creative" })}>
                  <Lightbulb size={15} /> 从创意开始
                </button>
                <button type="button" className={form.entry_mode === "outline" ? "active" : ""} onClick={() => setForm({ ...form, entry_mode: "outline" })}>
                  <FileInput size={15} /> 导入大纲
                </button>
              </div>
              <label><span>书名</span><input autoFocus value={form.title} onChange={(event) => setForm({ ...form, title: event.target.value })} /></label>
              <label><span>{form.entry_mode === "creative" ? "题材与创意" : "大纲说明"}</span><textarea rows={5} value={form.idea} onChange={(event) => setForm({ ...form, idea: event.target.value })} /></label>
              {details ? (
                <div className="detail-form-grid">
                  <label><span>题材</span><input value={form.genre} onChange={(event) => setForm({ ...form, genre: event.target.value })} /></label>
                  <label><span>主题</span><input value={form.theme} onChange={(event) => setForm({ ...form, theme: event.target.value })} /></label>
                  <label><span>时代</span><input value={form.era} onChange={(event) => setForm({ ...form, era: event.target.value })} /></label>
                  <label><span>读者</span><input value={form.audience} onChange={(event) => setForm({ ...form, audience: event.target.value })} /></label>
                  <label><span>目标字数</span><input type="number" value={form.target_words} onChange={(event) => setForm({ ...form, target_words: Number(event.target.value) })} /></label>
                  <label><span>章节数量</span><input type="number" value={form.chapter_count} onChange={(event) => setForm({ ...form, chapter_count: Number(event.target.value) })} /></label>
                  <label><span>每章字数</span><input type="number" value={form.chapter_words} onChange={(event) => setForm({ ...form, chapter_words: Number(event.target.value) })} /></label>
                  <label><span>叙事视角</span><input value={form.point_of_view} onChange={(event) => setForm({ ...form, point_of_view: event.target.value })} /></label>
                  <label className="span-2"><span>文风</span><textarea rows={3} value={form.style_description} onChange={(event) => setForm({ ...form, style_description: event.target.value })} /></label>
                  <label className="span-2"><span>禁用内容</span><textarea rows={2} value={form.prohibited_content} onChange={(event) => setForm({ ...form, prohibited_content: event.target.value })} /></label>
                </div>
              ) : null}
              {error ? <div className="form-error">{error}</div> : null}
              <footer>
                <button type="button" className="text-button" onClick={() => setDetails(!details)}>{details ? "使用快速创建" : "填写详细设置"}</button>
                <button type="button" className="secondary-button" onClick={() => setDialogOpen(false)}>取消</button>
                <button type="submit" className="primary-button" disabled={!form.title.trim() || !form.idea.trim() || create.isPending}>
                  {create.isPending ? "创建中..." : "创建项目"}
                </button>
              </footer>
            </form>
          </section>
        </div>
      ) : null}
    </section>
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
