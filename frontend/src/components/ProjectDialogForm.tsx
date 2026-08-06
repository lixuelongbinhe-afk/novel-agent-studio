import { FormEvent, useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileInput, Lightbulb } from "lucide-react";
import { StudioOverview, studioApi } from "../api/studio";

const emptyProjectForm = {
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

export function ProjectDialogForm({
  onSuccess,
  onCancel
}: {
  onSuccess: (overview: StudioOverview) => void;
  onCancel: (() => void) | null;
}) {
  const queryClient = useQueryClient();
  const { data: providers = [] } = useQuery({
    queryKey: ["studio-providers"],
    queryFn: studioApi.providers
  });
  const [form, setForm] = useState(emptyProjectForm);
  const [details, setDetails] = useState(false);
  const [selectedProviderId, setSelectedProviderId] = useState<number | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    if (selectedProviderId === null && providers[0]) {
      setSelectedProviderId(providers[0].id);
    }
  }, [providers, selectedProviderId]);

  const create = useMutation({
    mutationFn: () => studioApi.createProject(form),
    onSuccess: async (overview) => {
      await queryClient.invalidateQueries({ queryKey: ["studio-projects"] });
      onSuccess(overview);
    },
    onError: (reason: Error) => setError(reason.message)
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    create.mutate();
  }

  return (
    <form className="project-dialog-form" onSubmit={submit}>
      <div className="mode-switch">
        <button
          type="button"
          className={form.entry_mode === "creative" ? "active" : ""}
          onClick={() => setForm({ ...form, entry_mode: "creative" })}
        >
          <Lightbulb size={15} /> 从创意开始
        </button>
        <button
          type="button"
          className={form.entry_mode === "outline" ? "active" : ""}
          onClick={() => setForm({ ...form, entry_mode: "outline" })}
        >
          <FileInput size={15} /> 导入大纲
        </button>
      </div>
      <label>
        <span>书名</span>
        <input
          autoFocus
          value={form.title}
          onChange={(event) => setForm({ ...form, title: event.target.value })}
        />
      </label>
      <label>
        <span>{form.entry_mode === "creative" ? "题材与创意" : "大纲说明"}</span>
        <textarea
          rows={5}
          value={form.idea}
          onChange={(event) => setForm({ ...form, idea: event.target.value })}
        />
      </label>
      <label>
        <span>模型服务</span>
        <select
          value={selectedProviderId ?? ""}
          onChange={(event) => setSelectedProviderId(Number(event.target.value))}
          disabled={providers.length === 0}
        >
          {providers.length === 0 ? <option value="">尚未配置模型服务</option> : null}
          {providers.map((provider) => (
            <option key={provider.id} value={provider.id}>
              {provider.name} · {provider.model ?? provider.models?.[0]?.name ?? "默认模型"}
            </option>
          ))}
        </select>
      </label>
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
      {error ? <div className="form-error" role="alert">{error}</div> : null}
      <footer>
        <button type="button" className="text-button" onClick={() => setDetails(!details)}>
          {details ? "使用快速创建" : "填写详细设置"}
        </button>
        {onCancel ? (
          <button type="button" className="secondary-button" onClick={onCancel}>
            取消
          </button>
        ) : null}
        <button
          type="submit"
          className="primary-button"
          disabled={!form.title.trim() || !form.idea.trim() || create.isPending}
        >
          {create.isPending ? "创建中..." : "创建项目"}
        </button>
      </footer>
    </form>
  );
}
