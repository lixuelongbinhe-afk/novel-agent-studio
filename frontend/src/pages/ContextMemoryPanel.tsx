import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  BookMarked,
  FileText,
  Link2,
  MapPin,
  Plus,
  Save,
  Shield,
  Trash2
} from "lucide-react";
import {
  api,
  type ChapterEntityLink,
  type ChapterEntityLinkInput,
  type ChapterSummary,
  type ChapterSummaryInput,
  type ContentClassification,
  type ContentClassificationValue,
  type ContextPin,
  type ProjectTree,
  type SceneState,
  type SceneStateInput,
  type StoryEntity
} from "../api/client";
import { EmptyState } from "../components/EmptyState";
import { FormField } from "../components/FormField";
import { CLASSIFICATION_LABELS, contextErrorMessage } from "./contextUi";

type MemoryTab = "summaries" | "states" | "links" | "pins" | "classifications";

export function ContextMemoryPanel({
  projectId,
  tree,
  onError
}: {
  projectId: number;
  tree: ProjectTree | undefined;
  onError: (message: string) => void;
}) {
  const [activeTab, setActiveTab] = useState<MemoryTab>("summaries");
  const entitiesQuery = useQuery({
    queryKey: ["entities", projectId],
    queryFn: () => api.listEntities(projectId)
  });
  const entities = entitiesQuery.data ?? [];
  const tabs: Array<{ id: MemoryTab; label: string; icon: typeof FileText }> = [
    { id: "summaries", label: "章节摘要", icon: FileText },
    { id: "states", label: "场景状态", icon: MapPin },
    { id: "links", label: "人工链接", icon: Link2 },
    { id: "pins", label: "Context Pins", icon: BookMarked },
    { id: "classifications", label: "数据分类", icon: Shield }
  ];
  return (
    <section className="context-memory-workbench">
      <nav className="memory-resource-tabs" aria-label="小说记忆资源">
        {tabs.map(({ id, label, icon: Icon }) => (
          <button key={id} className={activeTab === id ? "active" : ""} type="button" onClick={() => setActiveTab(id)}>
            <Icon size={15} />{label}
          </button>
        ))}
      </nav>
      {activeTab === "summaries" ? <SummaryEditor projectId={projectId} tree={tree} entities={entities} onError={onError} /> : null}
      {activeTab === "states" ? <SceneStateEditor projectId={projectId} tree={tree} entities={entities} onError={onError} /> : null}
      {activeTab === "links" ? <LinkEditor projectId={projectId} tree={tree} entities={entities} onError={onError} /> : null}
      {activeTab === "pins" ? <PinEditor projectId={projectId} onError={onError} /> : null}
      {activeTab === "classifications" ? <ClassificationEditor projectId={projectId} onError={onError} /> : null}
    </section>
  );
}

function SummaryEditor({ projectId, tree, entities, onError }: EditorProps) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: ["chapter-summaries", projectId],
    queryFn: () => api.listChapterSummaries(projectId)
  });
  const items = query.data ?? [];
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [form, setForm] = useState<ChapterSummaryInput>(() => emptySummary(tree));
  const selected = items.find((item) => item.id === selectedId) ?? null;
  useEffect(() => {
    if (!selected) return;
    setForm({
      chapter_id: selected.chapter_id,
      summary: selected.summary,
      key_events: selected.key_events,
      entity_ids: selected.entity_ids,
      source: selected.source
    });
    setIsNew(false);
  }, [selected?.id, selected?.revision]);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["chapter-summaries", projectId] });
  const save = useMutation({
    mutationFn: () => selected && !isNew
      ? api.updateChapterSummary(selected, form)
      : api.createChapterSummary(form),
    onSuccess: async (value) => {
      await refresh();
      setSelectedId(value.id);
      setIsNew(false);
      onError("");
    },
    onError: (error) => onError(contextErrorMessage(error))
  });
  const remove = useMutation({
    mutationFn: (item: ChapterSummary) => api.deleteContextRecord("chapter-summary", item),
    onSuccess: async () => {
      setSelectedId(null);
      await refresh();
    },
    onError: (error) => onError(contextErrorMessage(error))
  });
  return (
    <MemoryEditorLayout
      title="章节摘要"
      count={items.length}
      onAdd={() => { setIsNew(true); setSelectedId(null); setForm(emptySummary(tree)); }}
      list={items.map((item) => (
        <button key={item.id} className={item.id === selectedId ? "selected" : ""} type="button" onClick={() => setSelectedId(item.id)}>
          <FileText size={15} /><span><strong>{chapterName(tree, item.chapter_id)}</strong><small>{item.token_count} Token · {item.key_events.length} 个事件</small></span>
        </button>
      ))}
    >
      {selected || isNew ? (
        <form className="context-memory-form" onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>
          <MemoryFormHeader title={isNew ? "新建章节摘要" : chapterName(tree, form.chapter_id)} onDelete={selected ? () => remove.mutate(selected) : undefined} />
          <FormField label="章节">
            <select value={form.chapter_id} onChange={(event) => setForm({ ...form, chapter_id: Number(event.target.value) })}>
              {tree?.chapters.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
            </select>
          </FormField>
          <FormField label="摘要"><textarea rows={12} value={form.summary} onChange={(event) => setForm({ ...form, summary: event.target.value })} /></FormField>
          <FormField label="关键事件（每行一条)"><textarea rows={6} value={form.key_events.join("\n")} onChange={(event) => setForm({ ...form, key_events: lines(event.target.value) })} /></FormField>
          <EntityMultiSelect label="涉及实体" entities={entities} value={form.entity_ids} onChange={(entity_ids) => setForm({ ...form, entity_ids })} />
          <FormField label="来源">
            <select value={form.source} onChange={(event) => setForm({ ...form, source: event.target.value as ChapterSummaryInput["source"] })}>
              <option value="manual">手工</option><option value="approved_extraction">已审批提取</option><option value="import">导入</option>
            </select>
          </FormField>
          <SaveButton disabled={!form.chapter_id || !form.summary.trim() || save.isPending} />
        </form>
      ) : <MemoryPlaceholder icon={FileText} />}
    </MemoryEditorLayout>
  );
}

function SceneStateEditor({ projectId, tree, entities, onError }: EditorProps) {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["scene-states", projectId], queryFn: () => api.listSceneStates(projectId) });
  const items = query.data ?? [];
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [form, setForm] = useState<SceneStateInput>(() => emptySceneState(tree));
  const [stateJson, setStateJson] = useState("{}");
  const selected = items.find((item) => item.id === selectedId) ?? null;
  useEffect(() => {
    if (!selected) return;
    const value = sceneStateInput(selected);
    setForm(value);
    setStateJson(JSON.stringify(value.state, null, 2));
    setIsNew(false);
  }, [selected?.id, selected?.revision]);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["scene-states", projectId] });
  const save = useMutation({
    mutationFn: () => {
      const state = parseObject(stateJson, "场景状态 JSON");
      const payload = { ...form, state };
      return selected && !isNew ? api.updateSceneState(selected, payload) : api.createSceneState(payload);
    },
    onSuccess: async (value) => {
      await refresh(); setSelectedId(value.id); setIsNew(false); onError("");
    },
    onError: (error) => onError(contextErrorMessage(error))
  });
  const remove = useMutation({
    mutationFn: (item: SceneState) => api.deleteContextRecord("scene-state", item),
    onSuccess: async () => { setSelectedId(null); await refresh(); },
    onError: (error) => onError(contextErrorMessage(error))
  });
  return (
    <MemoryEditorLayout
      title="场景状态"
      count={items.length}
      onAdd={() => { const value = emptySceneState(tree); setIsNew(true); setSelectedId(null); setForm(value); setStateJson("{}"); }}
      list={items.map((item) => (
        <button key={item.id} className={item.id === selectedId ? "selected" : ""} type="button" onClick={() => setSelectedId(item.id)}>
          <MapPin size={15} /><span><strong>{sceneName(tree, item.scene_id)}</strong><small>{entityName(entities, item.viewpoint_entity_id)} · {entityName(entities, item.location_entity_id)}</small></span>
        </button>
      ))}
    >
      {selected || isNew ? (
        <form className="context-memory-form" onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>
          <MemoryFormHeader title={isNew ? "新建场景状态" : sceneName(tree, form.scene_id)} onDelete={selected ? () => remove.mutate(selected) : undefined} />
          <FormField label="场景"><select value={form.scene_id} onChange={(event) => setForm({ ...form, scene_id: Number(event.target.value) })}>{tree?.scenes.map((item) => <option key={item.id} value={item.id}>{chapterName(tree, item.chapter_id)} · {item.title}</option>)}</select></FormField>
          <div className="form-row">
            <EntitySelect label="视角人物" entities={entities} value={form.viewpoint_entity_id} onChange={(viewpoint_entity_id) => setForm({ ...form, viewpoint_entity_id })} />
            <EntitySelect label="当前地点" entities={entities} value={form.location_entity_id} onChange={(location_entity_id) => setForm({ ...form, location_entity_id })} />
          </div>
          <EntityMultiSelect label="当前物品" entities={entities} value={form.item_entity_ids} onChange={(item_entity_ids) => setForm({ ...form, item_entity_ids })} />
          <FormField label="结构化状态 JSON"><textarea rows={8} value={stateJson} onChange={(event) => setStateJson(event.target.value)} /></FormField>
          <FormField label="说明"><textarea rows={6} value={form.notes} onChange={(event) => setForm({ ...form, notes: event.target.value })} /></FormField>
          <SaveButton disabled={!form.scene_id || save.isPending} />
        </form>
      ) : <MemoryPlaceholder icon={MapPin} />}
    </MemoryEditorLayout>
  );
}

function LinkEditor({ projectId, tree, entities, onError }: EditorProps) {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["chapter-entity-links", projectId], queryFn: () => api.listChapterEntityLinks(projectId) });
  const items = query.data ?? [];
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [form, setForm] = useState<ChapterEntityLinkInput>(() => emptyLink(tree, entities));
  const selected = items.find((item) => item.id === selectedId) ?? null;
  useEffect(() => {
    if (!selected) return;
    setForm({ chapter_id: selected.chapter_id, entity_id: selected.entity_id, link_type: selected.link_type, relevance: selected.relevance, notes: selected.notes });
    setIsNew(false);
  }, [selected?.id, selected?.revision]);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["chapter-entity-links", projectId] });
  const save = useMutation({
    mutationFn: () => selected && !isNew ? api.updateChapterEntityLink(selected, form) : api.createChapterEntityLink(form),
    onSuccess: async (value) => { await refresh(); setSelectedId(value.id); setIsNew(false); onError(""); },
    onError: (error) => onError(contextErrorMessage(error))
  });
  const remove = useMutation({
    mutationFn: (item: ChapterEntityLink) => api.deleteContextRecord("chapter-entity-link", item),
    onSuccess: async () => { setSelectedId(null); await refresh(); },
    onError: (error) => onError(contextErrorMessage(error))
  });
  return (
    <MemoryEditorLayout
      title="人工链接"
      count={items.length}
      onAdd={() => { setIsNew(true); setSelectedId(null); setForm(emptyLink(tree, entities)); }}
      list={items.map((item) => (
        <button key={item.id} className={item.id === selectedId ? "selected" : ""} type="button" onClick={() => setSelectedId(item.id)}>
          <Link2 size={15} /><span><strong>{chapterName(tree, item.chapter_id)} → {entityName(entities, item.entity_id)}</strong><small>{item.link_type} · {Math.round(item.relevance * 100)}%</small></span>
        </button>
      ))}
    >
      {selected || isNew ? (
        <form className="context-memory-form" onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>
          <MemoryFormHeader title={isNew ? "新建人工链接" : form.link_type} onDelete={selected ? () => remove.mutate(selected) : undefined} />
          <FormField label="章节"><select value={form.chapter_id} onChange={(event) => setForm({ ...form, chapter_id: Number(event.target.value) })}>{tree?.chapters.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select></FormField>
          <EntitySelect label="实体" entities={entities} value={form.entity_id} required onChange={(entity_id) => setForm({ ...form, entity_id: entity_id ?? 0 })} />
          <FormField label="链接类型"><input value={form.link_type} onChange={(event) => setForm({ ...form, link_type: event.target.value })} placeholder="viewpoint / appears / referenced" /></FormField>
          <FormField label={`相关性 ${Math.round(form.relevance * 100)}%`}><input type="range" min={0} max={1} step={0.01} value={form.relevance} onChange={(event) => setForm({ ...form, relevance: Number(event.target.value) })} /></FormField>
          <FormField label="说明"><textarea rows={8} value={form.notes} onChange={(event) => setForm({ ...form, notes: event.target.value })} /></FormField>
          <SaveButton disabled={!form.chapter_id || !form.entity_id || !form.link_type.trim() || save.isPending} />
        </form>
      ) : <MemoryPlaceholder icon={Link2} />}
    </MemoryEditorLayout>
  );
}

function PinEditor({ projectId, onError }: { projectId: number; onError: (message: string) => void }) {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["context-pins", projectId], queryFn: () => api.listContextPins(projectId) });
  const items = query.data ?? [];
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const selected = items.find((item) => item.id === selectedId) ?? null;
  const [form, setForm] = useState<ContextPin | null>(null);
  useEffect(() => { if (selected) setForm(selected); }, [selected?.id, selected?.revision]);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["context-pins", projectId] });
  const save = useMutation({
    mutationFn: () => api.updateContextPin(selected!, {
      project_id: projectId,
      source_type: form!.source_type,
      source_id: form!.source_id,
      label: form!.label,
      content_override: form!.content_override,
      priority: form!.priority,
      required: form!.required,
      enabled: form!.enabled
    }),
    onSuccess: async () => { await refresh(); onError(""); },
    onError: (error) => onError(contextErrorMessage(error))
  });
  const remove = useMutation({
    mutationFn: (item: ContextPin) => api.deleteContextRecord("context-pin", item),
    onSuccess: async () => { setSelectedId(null); setForm(null); await refresh(); },
    onError: (error) => onError(contextErrorMessage(error))
  });
  return (
    <MemoryEditorLayout title="Context Pins" count={items.length} list={items.map((item) => (
      <button key={item.id} className={item.id === selectedId ? "selected" : ""} type="button" onClick={() => setSelectedId(item.id)}><BookMarked size={15} /><span><strong>{item.label || `${item.source_type} #${item.source_id}`}</strong><small>优先级 {item.priority} · {item.required ? "强制" : "可选"}</small></span></button>
    ))}>
      {selected && form ? <form className="context-memory-form" onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>
        <MemoryFormHeader title={form.label || "Context Pin"} onDelete={() => remove.mutate(selected)} />
        <div className="form-row"><FormField label="来源类型"><input value={form.source_type} disabled /></FormField><FormField label="来源 ID"><input value={form.source_id} disabled /></FormField></div>
        <FormField label="标签"><input value={form.label} onChange={(event) => setForm({ ...form, label: event.target.value })} /></FormField>
        <FormField label="内容覆盖"><textarea rows={10} value={form.content_override} onChange={(event) => setForm({ ...form, content_override: event.target.value })} /></FormField>
        <FormField label="优先级"><input type="number" min={0} max={1000} value={form.priority} onChange={(event) => setForm({ ...form, priority: Number(event.target.value) })} /></FormField>
        <div className="form-row"><label className="checkbox-row"><input type="checkbox" checked={form.required} onChange={(event) => setForm({ ...form, required: event.target.checked })} />强制包含</label><label className="checkbox-row"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} />启用</label></div>
        <SaveButton disabled={save.isPending} />
      </form> : <MemoryPlaceholder icon={BookMarked} />}
    </MemoryEditorLayout>
  );
}

function ClassificationEditor({ projectId, onError }: { projectId: number; onError: (message: string) => void }) {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["content-classifications", projectId], queryFn: () => api.listContentClassifications(projectId) });
  const items = query.data ?? [];
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const selected = items.find((item) => item.id === selectedId) ?? null;
  const [classification, setClassification] = useState<ContentClassificationValue>("unpublished manuscript");
  const [reason, setReason] = useState("");
  useEffect(() => { if (selected) { setClassification(selected.classification); setReason(selected.reason); } }, [selected?.id, selected?.revision]);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["content-classifications", projectId] });
  const save = useMutation({
    mutationFn: () => api.updateContentClassification(selected!, { project_id: projectId, source_type: selected!.source_type, source_id: selected!.source_id, classification, reason }),
    onSuccess: async () => { await refresh(); onError(""); },
    onError: (error) => onError(contextErrorMessage(error))
  });
  const remove = useMutation({
    mutationFn: (item: ContentClassification) => api.deleteContextRecord("classification", item),
    onSuccess: async () => { setSelectedId(null); await refresh(); },
    onError: (error) => onError(contextErrorMessage(error))
  });
  return (
    <MemoryEditorLayout title="数据分类" count={items.length} list={items.map((item) => (
      <button key={item.id} className={item.id === selectedId ? "selected" : ""} type="button" onClick={() => setSelectedId(item.id)}><Shield size={15} /><span><strong>{item.source_type} #{item.source_id}</strong><small>{CLASSIFICATION_LABELS[item.classification]}</small></span></button>
    ))}>
      {selected ? <form className="context-memory-form" onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>
        <MemoryFormHeader title={`${selected.source_type} #${selected.source_id}`} onDelete={() => remove.mutate(selected)} />
        <FormField label="分类"><select value={classification} onChange={(event) => setClassification(event.target.value as ContentClassificationValue)}>{(Object.keys(CLASSIFICATION_LABELS) as ContentClassificationValue[]).map((value) => <option key={value} value={value}>{CLASSIFICATION_LABELS[value]}</option>)}</select></FormField>
        <FormField label="原因"><textarea rows={8} value={reason} onChange={(event) => setReason(event.target.value)} /></FormField>
        <SaveButton disabled={save.isPending} />
      </form> : <MemoryPlaceholder icon={Shield} />}
    </MemoryEditorLayout>
  );
}

type EditorProps = {
  projectId: number;
  tree: ProjectTree | undefined;
  entities: StoryEntity[];
  onError: (message: string) => void;
};

function MemoryEditorLayout({ title, count, onAdd, list, children }: { title: string; count: number; onAdd?: () => void; list: React.ReactNode[]; children: React.ReactNode }) {
  return <div className="context-memory-layout"><aside className="context-memory-list"><header><div><strong>{title}</strong><span>{count} 条</span></div>{onAdd ? <button className="icon-button" type="button" title="新建" onClick={onAdd}><Plus size={16} /></button> : null}</header><div>{list.length ? list : <EmptyState icon={FileText} title="暂无记录" description="" />}</div></aside><section className="context-memory-detail">{children}</section></div>;
}

function MemoryFormHeader({ title, onDelete }: { title: string; onDelete?: () => void }) {
  return <header><div><span>记忆记录</span><h2>{title}</h2></div>{onDelete ? <button className="icon-button ghost danger-ink" type="button" title="删除" onClick={onDelete}><Trash2 size={16} /></button> : null}</header>;
}

function SaveButton({ disabled }: { disabled: boolean }) {
  return <div className="context-memory-save"><button className="primary-button" type="submit" disabled={disabled}><Save size={16} />保存</button></div>;
}

function MemoryPlaceholder({ icon }: { icon: typeof FileText }) {
  return <EmptyState icon={icon} title="选择一条记录" description="" />;
}

function EntitySelect({ label, entities, value, onChange, required = false }: { label: string; entities: StoryEntity[]; value: number | null; onChange: (value: number | null) => void; required?: boolean }) {
  return <FormField label={label}><select value={value ?? 0} onChange={(event) => onChange(Number(event.target.value) || null)}>{!required ? <option value={0}>未指定</option> : null}{entities.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.kind}</option>)}</select></FormField>;
}

function EntityMultiSelect({ label, entities, value, onChange }: { label: string; entities: StoryEntity[]; value: number[]; onChange: (value: number[]) => void }) {
  return <FormField label={label}><select className="context-multi-select" multiple value={value.map(String)} onChange={(event) => onChange(Array.from(event.currentTarget.selectedOptions, (option) => Number(option.value)))}>{entities.map((item) => <option key={item.id} value={item.id}>{item.name} · {item.kind}</option>)}</select></FormField>;
}

function emptySummary(tree: ProjectTree | undefined): ChapterSummaryInput {
  return { chapter_id: tree?.chapters[0]?.id ?? 0, summary: "", key_events: [], entity_ids: [], source: "manual" };
}

function emptySceneState(tree: ProjectTree | undefined): SceneStateInput {
  return { scene_id: tree?.scenes[0]?.id ?? 0, viewpoint_entity_id: null, location_entity_id: null, item_entity_ids: [], state: {}, notes: "" };
}

function sceneStateInput(item: SceneState): SceneStateInput {
  return { scene_id: item.scene_id, viewpoint_entity_id: item.viewpoint_entity_id, location_entity_id: item.location_entity_id, item_entity_ids: item.item_entity_ids, state: item.state, notes: item.notes };
}

function emptyLink(tree: ProjectTree | undefined, entities: StoryEntity[]): ChapterEntityLinkInput {
  return { chapter_id: tree?.chapters[0]?.id ?? 0, entity_id: entities[0]?.id ?? 0, link_type: "manual", relevance: 1, notes: "" };
}

function chapterName(tree: ProjectTree | undefined, id: number): string {
  return tree?.chapters.find((item) => item.id === id)?.title ?? `章节 #${id}`;
}

function sceneName(tree: ProjectTree | undefined, id: number): string {
  return tree?.scenes.find((item) => item.id === id)?.title ?? `场景 #${id}`;
}

function entityName(entities: StoryEntity[], id: number | null): string {
  return id ? entities.find((item) => item.id === id)?.name ?? `实体 #${id}` : "未指定";
}

function lines(value: string): string[] {
  return value.split(/\r?\n/).map((item) => item.trim()).filter(Boolean);
}

function parseObject(value: string, label: string): Record<string, unknown> {
  const parsed = JSON.parse(value) as unknown;
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error(`${label} 必须是对象`);
  return parsed as Record<string, unknown>;
}
