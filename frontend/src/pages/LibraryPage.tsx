import { FormEvent, ReactNode, useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ArrowRightLeft,
  BookMarked,
  Clock3,
  History,
  Lightbulb,
  Plus,
  Save,
  Search,
  Sparkles,
  Trash2,
  UsersRound
} from "lucide-react";
import {
  api,
  EntityKind,
  EntityRelation,
  EntityStateChange,
  Foreshadow,
  ProjectTree,
  StoryEntity,
  StyleGuide,
  TimelineEvent
} from "../api/client";
import { EmptyState } from "../components/EmptyState";
import { ErrorNotice } from "../components/ErrorNotice";
import { FormField } from "../components/FormField";
import { useUiStore } from "../stores/ui";

type LibraryTab = "entities" | "relations" | "states" | "timeline" | "foreshadows" | "styles";

const tabs: Array<{ key: LibraryTab; label: string; icon: typeof UsersRound }> = [
  { key: "entities", label: "人物与设定", icon: UsersRound },
  { key: "relations", label: "关系", icon: ArrowRightLeft },
  { key: "states", label: "状态变化", icon: History },
  { key: "timeline", label: "时间线", icon: Clock3 },
  { key: "foreshadows", label: "伏笔", icon: Lightbulb },
  { key: "styles", label: "风格指南", icon: Sparkles }
];

export function LibraryPage() {
  const [activeTab, setActiveTab] = useState<LibraryTab>("entities");
  const { data: projects = [] } = useQuery({ queryKey: ["projects"], queryFn: () => api.listProjects() });
  const selectedProjectId = useUiStore((state) => state.selectedProjectId);
  const projectId = selectedProjectId ?? projects[0]?.id;
  const { data: tree } = useQuery({
    queryKey: ["tree", projectId],
    queryFn: () => api.tree(projectId!),
    enabled: Boolean(projectId)
  });

  if (!projectId) {
    return <EmptyState icon={BookMarked} title="资料库需要一个项目" description="请先在项目首页创建小说。" />;
  }

  return (
    <section className="page-stack library-page">
      <header className="page-header">
        <div>
          <span className="eyebrow">世界观与连续性</span>
          <h1>资料库</h1>
          <p>集中管理人物、地点、物品、关系、状态变化、时间线、伏笔和写作规则。</p>
        </div>
      </header>
      <nav className="segmented-tabs library-tabs" aria-label="资料分类">
        {tabs.map((tab) => {
          const Icon = tab.icon;
          return (
            <button key={tab.key} className={activeTab === tab.key ? "active" : ""} type="button" onClick={() => setActiveTab(tab.key)}>
              <Icon size={16} />
              {tab.label}
            </button>
          );
        })}
      </nav>
      {activeTab === "entities" ? <EntitiesSection projectId={projectId} /> : null}
      {activeTab === "relations" ? <RelationsSection projectId={projectId} /> : null}
      {activeTab === "states" ? <StatesSection projectId={projectId} tree={tree} /> : null}
      {activeTab === "timeline" ? <TimelineSection projectId={projectId} tree={tree} /> : null}
      {activeTab === "foreshadows" ? <ForeshadowsSection projectId={projectId} tree={tree} /> : null}
      {activeTab === "styles" ? <StylesSection projectId={projectId} /> : null}
    </section>
  );
}

function EntitiesSection({ projectId }: { projectId: number }) {
  const queryClient = useQueryClient();
  const { data: entities = [], error } = useQuery({ queryKey: ["entities", projectId], queryFn: () => api.listEntities(projectId) });
  const { data: aliases = [] } = useQuery({ queryKey: ["aliases", projectId], queryFn: () => api.listAliases(projectId) });
  const [kind, setKind] = useState<EntityKind | "all">("all");
  const [search, setSearch] = useState("");
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [form, setForm] = useState({ name: "", kind: "character" as EntityKind, description: "", tags: "" });
  const [newAlias, setNewAlias] = useState("");
  const selected = entities.find((item) => item.id === selectedId) ?? null;
  const filtered = entities.filter((item) => {
    const matchesKind = kind === "all" || item.kind === kind;
    const needle = search.trim().toLowerCase();
    return matchesKind && (!needle || `${item.name} ${item.description} ${item.tags.join(" ")}`.toLowerCase().includes(needle));
  });

  useEffect(() => {
    if (selected) {
      setForm({ name: selected.name, kind: selected.kind, description: selected.description, tags: selected.tags.join("，") });
      setIsNew(false);
    }
  }, [selected?.id, selected?.revision]);

  const refresh = () => queryClient.invalidateQueries({ queryKey: ["entities", projectId] });
  const save = useMutation({
    mutationFn: () => {
      const payload = { ...form, tags: form.tags.split(/[，,]/).map((item) => item.trim()).filter(Boolean) };
      return isNew ? api.createEntity(projectId, payload) : api.updateEntity(selected!, payload);
    },
    onSuccess: async (entity) => {
      await refresh();
      setSelectedId(entity.id);
      setIsNew(false);
    }
  });
  const remove = useMutation({
    mutationFn: () => api.deleteRecord("entity", selected!),
    onSuccess: async () => {
      setSelectedId(null);
      await refresh();
    }
  });
  const addAlias = useMutation({
    mutationFn: () => api.createAlias(selected!.id, newAlias.trim()),
    onSuccess: async () => {
      setNewAlias("");
      await queryClient.invalidateQueries({ queryKey: ["aliases", projectId] });
    }
  });
  const removeAlias = useMutation({
    mutationFn: (aliasId: number) => {
      const alias = aliases.find((item) => item.id === aliasId)!;
      return api.deleteRecord("alias", alias);
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["aliases", projectId] })
  });

  function startNew() {
    setSelectedId(null);
    setIsNew(true);
    setForm({ name: "", kind: kind === "all" ? "character" : kind, description: "", tags: "" });
  }

  return (
    <LibraryLayout
      title="设定条目"
      count={filtered.length}
      toolbar={
        <>
          <label className="search-box"><Search size={16} /><input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="搜索名称、标签或描述" /></label>
          <select value={kind} onChange={(event) => setKind(event.target.value as EntityKind | "all")} aria-label="实体类型">
            <option value="all">全部类型</option><option value="character">人物</option><option value="location">地点</option><option value="item">物品</option><option value="organization">组织</option><option value="concept">概念</option>
          </select>
        </>
      }
      onAdd={startNew}
      list={
        error ? <ErrorNotice message="无法读取资料条目。" /> : filtered.length === 0 ? <EmptyState icon={UsersRound} title="暂无匹配条目" description="新建人物、地点、物品或组织。" /> : (
          <div className="library-list">
            {filtered.map((item) => (
              <button key={item.id} type="button" className={item.id === selectedId ? "selected" : ""} onClick={() => setSelectedId(item.id)}>
                <span className={`entity-kind kind-${item.kind}`}>{kindLabel(item.kind)}</span>
                <strong>{item.name}</strong>
                <small>{item.description || "暂无描述"}</small>
              </button>
            ))}
          </div>
        )
      }
      detail={selected || isNew ? (
        <form className="detail-form" onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>
          <DetailHeader title={isNew ? "新建设定" : selected!.name} onDelete={!isNew ? () => remove.mutate() : undefined} />
          {save.error ? <ErrorNotice message="保存失败，条目可能已在其他窗口修改。" /> : null}
          <FormField label="名称"><input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} autoFocus={isNew} /></FormField>
          <FormField label="类型"><select value={form.kind} onChange={(event) => setForm({ ...form, kind: event.target.value as EntityKind })}><option value="character">人物</option><option value="location">地点</option><option value="item">物品</option><option value="organization">组织</option><option value="concept">概念</option></select></FormField>
          <FormField label="描述"><textarea rows={9} value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} /></FormField>
          <FormField label="标签" hint="使用逗号分隔"><input value={form.tags} onChange={(event) => setForm({ ...form, tags: event.target.value })} /></FormField>
          {!isNew && selected ? (
            <section className="inline-section">
              <h3>别名</h3>
              <div className="alias-list">
                {aliases.filter((item) => item.entity_id === selected.id).map((alias) => (
                  <span key={alias.id}>{alias.alias}<button type="button" onClick={() => removeAlias.mutate(alias.id)} title="删除别名">×</button></span>
                ))}
              </div>
              <div className="inline-input"><input value={newAlias} onChange={(event) => setNewAlias(event.target.value)} placeholder="添加别名" /><button className="secondary-button" type="button" disabled={!newAlias.trim()} onClick={() => addAlias.mutate()}>添加</button></div>
            </section>
          ) : null}
          <SaveBar disabled={!form.name.trim() || save.isPending} />
        </form>
      ) : <DetailPlaceholder />}
    />
  );
}

function RelationsSection({ projectId }: { projectId: number }) {
  const queryClient = useQueryClient();
  const { data: entities = [] } = useQuery({ queryKey: ["entities", projectId], queryFn: () => api.listEntities(projectId) });
  const { data: items = [] } = useQuery({ queryKey: ["relations", projectId], queryFn: () => api.listRelations(projectId) });
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [form, setForm] = useState({ source_entity_id: 0, target_entity_id: 0, relation_type: "", notes: "" });
  const selected = items.find((item) => item.id === selectedId) ?? null;
  useEffect(() => { if (selected) { setForm(pickRelation(selected)); setIsNew(false); } }, [selected?.id, selected?.revision]);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["relations", projectId] });
  const save = useMutation({ mutationFn: () => isNew ? api.createRelation(projectId, form) : api.updateRelation(selected!, form), onSuccess: async (item) => { await refresh(); setSelectedId(item.id); setIsNew(false); } });
  const remove = useMutation({ mutationFn: () => api.deleteRecord("relation", selected!), onSuccess: async () => { setSelectedId(null); await refresh(); } });
  const nameOf = (id: number) => entities.find((item) => item.id === id)?.name ?? `#${id}`;
  const startNew = () => { setSelectedId(null); setIsNew(true); setForm({ source_entity_id: entities[0]?.id ?? 0, target_entity_id: entities[1]?.id ?? 0, relation_type: "", notes: "" }); };
  return (
    <LibraryLayout title="实体关系" count={items.length} onAdd={startNew} list={
      items.length === 0 ? <EmptyState icon={ArrowRightLeft} title="暂无关系" description="至少创建两个实体后，可以记录人物或设定之间的关系。" /> : <div className="library-list">{items.map((item) => <button key={item.id} className={item.id === selectedId ? "selected" : ""} onClick={() => setSelectedId(item.id)}><strong>{nameOf(item.source_entity_id)} → {nameOf(item.target_entity_id)}</strong><small>{item.relation_type}</small></button>)}</div>
    } detail={selected || isNew ? <form className="detail-form" onSubmit={(event) => { event.preventDefault(); save.mutate(); }}><DetailHeader title={isNew ? "新建关系" : form.relation_type || "关系"} onDelete={!isNew ? () => remove.mutate() : undefined} /><FormField label="起点"><EntitySelect entities={entities} value={form.source_entity_id} onChange={(value) => setForm({ ...form, source_entity_id: value })} /></FormField><FormField label="终点"><EntitySelect entities={entities} value={form.target_entity_id} onChange={(value) => setForm({ ...form, target_entity_id: value })} /></FormField><FormField label="关系类型"><input value={form.relation_type} onChange={(event) => setForm({ ...form, relation_type: event.target.value })} placeholder="例如：盟友、师生、敌对" /></FormField><FormField label="说明"><textarea rows={8} value={form.notes} onChange={(event) => setForm({ ...form, notes: event.target.value })} /></FormField><SaveBar disabled={!form.relation_type.trim() || form.source_entity_id === form.target_entity_id || save.isPending} /></form> : <DetailPlaceholder />} />
  );
}

function StatesSection({ projectId, tree }: { projectId: number; tree?: ProjectTree }) {
  const queryClient = useQueryClient();
  const { data: entities = [] } = useQuery({ queryKey: ["entities", projectId], queryFn: () => api.listEntities(projectId) });
  const { data: items = [] } = useQuery({ queryKey: ["state-changes", projectId], queryFn: () => api.listStateChanges(projectId) });
  const [selectedId, setSelectedId] = useState<number | null>(null); const [isNew, setIsNew] = useState(false);
  const [form, setForm] = useState({ entity_id: 0, chapter_id: null as number | null, field_name: "", old_value: "", new_value: "", reason: "" });
  const selected = items.find((item) => item.id === selectedId) ?? null;
  useEffect(() => { if (selected) { setForm(pickState(selected)); setIsNew(false); } }, [selected?.id, selected?.revision]);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["state-changes", projectId] });
  const save = useMutation({ mutationFn: () => isNew ? api.createStateChange(projectId, form) : api.updateStateChange(selected!, form), onSuccess: async (item) => { await refresh(); setSelectedId(item.id); setIsNew(false); } });
  const remove = useMutation({ mutationFn: () => api.deleteRecord("state-change", selected!), onSuccess: async () => { setSelectedId(null); await refresh(); } });
  const nameOf = (id: number) => entities.find((entity) => entity.id === id)?.name ?? `#${id}`;
  const startNew = () => { setSelectedId(null); setIsNew(true); setForm({ entity_id: entities[0]?.id ?? 0, chapter_id: null, field_name: "", old_value: "", new_value: "", reason: "" }); };
  return <LibraryLayout title="状态变化" count={items.length} onAdd={startNew} list={items.length === 0 ? <EmptyState icon={History} title="暂无状态变化" description="记录人物或物品随章节发生的变化。" /> : <div className="library-list">{items.map((item) => <button key={item.id} className={item.id === selectedId ? "selected" : ""} onClick={() => setSelectedId(item.id)}><strong>{nameOf(item.entity_id)} · {item.field_name}</strong><small>{item.old_value || "（空）"} → {item.new_value || "（空）"}</small></button>)}</div>} detail={selected || isNew ? <form className="detail-form" onSubmit={(event) => { event.preventDefault(); save.mutate(); }}><DetailHeader title={isNew ? "新建状态变化" : `${nameOf(form.entity_id)} · ${form.field_name}`} onDelete={!isNew ? () => remove.mutate() : undefined} /><FormField label="实体"><EntitySelect entities={entities} value={form.entity_id} onChange={(value) => setForm({ ...form, entity_id: value })} /></FormField><FormField label="发生章节"><ChapterSelect tree={tree} value={form.chapter_id} onChange={(value) => setForm({ ...form, chapter_id: value })} /></FormField><FormField label="变化字段"><input value={form.field_name} onChange={(event) => setForm({ ...form, field_name: event.target.value })} placeholder="例如：位置、伤势、持有物" /></FormField><div className="form-row"><FormField label="变化前"><textarea rows={4} value={form.old_value} onChange={(event) => setForm({ ...form, old_value: event.target.value })} /></FormField><FormField label="变化后"><textarea rows={4} value={form.new_value} onChange={(event) => setForm({ ...form, new_value: event.target.value })} /></FormField></div><FormField label="原因"><textarea rows={4} value={form.reason} onChange={(event) => setForm({ ...form, reason: event.target.value })} /></FormField><SaveBar disabled={!form.entity_id || !form.field_name.trim() || save.isPending} /></form> : <DetailPlaceholder />} />;
}

function TimelineSection({ projectId, tree }: { projectId: number; tree?: ProjectTree }) {
  const queryClient = useQueryClient(); const { data: items = [] } = useQuery({ queryKey: ["timeline", projectId], queryFn: () => api.listTimeline(projectId) });
  const [selectedId, setSelectedId] = useState<number | null>(null); const [isNew, setIsNew] = useState(false);
  const [form, setForm] = useState({ chapter_id: null as number | null, label: "", event_time: "", description: "", position: 0 });
  const selected = items.find((item) => item.id === selectedId) ?? null;
  useEffect(() => { if (selected) { setForm(pickTimeline(selected)); setIsNew(false); } }, [selected?.id, selected?.revision]);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["timeline", projectId] });
  const save = useMutation({ mutationFn: () => isNew ? api.createTimelineEvent(projectId, { ...form, position: items.length + 1 }) : api.updateTimelineEvent(selected!, form), onSuccess: async (item) => { await refresh(); setSelectedId(item.id); setIsNew(false); } });
  const remove = useMutation({ mutationFn: () => api.deleteRecord("timeline", selected!), onSuccess: async () => { setSelectedId(null); await refresh(); } });
  const startNew = () => { setSelectedId(null); setIsNew(true); setForm({ chapter_id: null, label: "", event_time: "", description: "", position: items.length + 1 }); };
  return <LibraryLayout title="故事时间线" count={items.length} onAdd={startNew} list={items.length === 0 ? <EmptyState icon={Clock3} title="时间线为空" description="记录故事内时间，而不是章节发布时间。" /> : <div className="timeline-list">{items.map((item) => <button key={item.id} className={item.id === selectedId ? "selected" : ""} onClick={() => setSelectedId(item.id)}><span>{item.event_time || "未定时间"}</span><strong>{item.label}</strong><small>{item.description}</small></button>)}</div>} detail={selected || isNew ? <form className="detail-form" onSubmit={(event) => { event.preventDefault(); save.mutate(); }}><DetailHeader title={isNew ? "新建时间事件" : form.label} onDelete={!isNew ? () => remove.mutate() : undefined} /><FormField label="事件名称"><input value={form.label} onChange={(event) => setForm({ ...form, label: event.target.value })} /></FormField><FormField label="故事内时间"><input value={form.event_time} onChange={(event) => setForm({ ...form, event_time: event.target.value })} placeholder="例如：冬至前夜 / 纪元 214 年" /></FormField><FormField label="关联章节"><ChapterSelect tree={tree} value={form.chapter_id} onChange={(value) => setForm({ ...form, chapter_id: value })} /></FormField><FormField label="事件说明"><textarea rows={10} value={form.description} onChange={(event) => setForm({ ...form, description: event.target.value })} /></FormField><SaveBar disabled={!form.label.trim() || save.isPending} /></form> : <DetailPlaceholder />} />;
}

function ForeshadowsSection({ projectId, tree }: { projectId: number; tree?: ProjectTree }) {
  const queryClient = useQueryClient(); const { data: items = [] } = useQuery({ queryKey: ["foreshadows", projectId], queryFn: () => api.listForeshadows(projectId) });
  const [selectedId, setSelectedId] = useState<number | null>(null); const [isNew, setIsNew] = useState(false);
  const [form, setForm] = useState({ setup_text: "", payoff_text: "", status: "open" as Foreshadow["status"], chapter_id: null as number | null });
  const selected = items.find((item) => item.id === selectedId) ?? null;
  useEffect(() => { if (selected) { setForm(pickForeshadow(selected)); setIsNew(false); } }, [selected?.id, selected?.revision]);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["foreshadows", projectId] });
  const save = useMutation({ mutationFn: () => isNew ? api.createForeshadow(projectId, form) : api.updateForeshadow(selected!, form), onSuccess: async (item) => { await refresh(); setSelectedId(item.id); setIsNew(false); } });
  const remove = useMutation({ mutationFn: () => api.deleteRecord("foreshadow", selected!), onSuccess: async () => { setSelectedId(null); await refresh(); } });
  const startNew = () => { setSelectedId(null); setIsNew(true); setForm({ setup_text: "", payoff_text: "", status: "open", chapter_id: null }); };
  return <LibraryLayout title="伏笔追踪" count={items.length} onAdd={startNew} list={items.length === 0 ? <EmptyState icon={Lightbulb} title="还没有伏笔" description="记录埋设内容、计划回收和当前状态。" /> : <div className="library-list">{items.map((item) => <button key={item.id} className={item.id === selectedId ? "selected" : ""} onClick={() => setSelectedId(item.id)}><span className={`status-chip ${item.status}`}>{foreshadowStatus(item.status)}</span><strong>{item.setup_text}</strong><small>{item.payoff_text || "尚未填写回收计划"}</small></button>)}</div>} detail={selected || isNew ? <form className="detail-form" onSubmit={(event) => { event.preventDefault(); save.mutate(); }}><DetailHeader title={isNew ? "新建伏笔" : "伏笔详情"} onDelete={!isNew ? () => remove.mutate() : undefined} /><FormField label="埋设内容"><textarea rows={6} value={form.setup_text} onChange={(event) => setForm({ ...form, setup_text: event.target.value })} /></FormField><FormField label="回收计划"><textarea rows={6} value={form.payoff_text} onChange={(event) => setForm({ ...form, payoff_text: event.target.value })} /></FormField><div className="form-row"><FormField label="状态"><select value={form.status} onChange={(event) => setForm({ ...form, status: event.target.value as Foreshadow["status"] })}><option value="open">待发展</option><option value="developing">发展中</option><option value="resolved">已回收</option><option value="abandoned">已放弃</option></select></FormField><FormField label="埋设章节"><ChapterSelect tree={tree} value={form.chapter_id} onChange={(value) => setForm({ ...form, chapter_id: value })} /></FormField></div><SaveBar disabled={!form.setup_text.trim() || save.isPending} /></form> : <DetailPlaceholder />} />;
}

function StylesSection({ projectId }: { projectId: number }) {
  const queryClient = useQueryClient(); const { data: items = [] } = useQuery({ queryKey: ["style-guides", projectId], queryFn: () => api.listStyleGuides(projectId) });
  const [selectedId, setSelectedId] = useState<number | null>(null); const [isNew, setIsNew] = useState(false);
  const [form, setForm] = useState({ name: "", rule_text: "", category: "voice" }); const selected = items.find((item) => item.id === selectedId) ?? null;
  useEffect(() => { if (selected) { setForm(pickStyle(selected)); setIsNew(false); } }, [selected?.id, selected?.revision]);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["style-guides", projectId] });
  const save = useMutation({ mutationFn: () => isNew ? api.createStyleGuide(projectId, form) : api.updateStyleGuide(selected!, form), onSuccess: async (item) => { await refresh(); setSelectedId(item.id); setIsNew(false); } });
  const remove = useMutation({ mutationFn: () => api.deleteRecord("style-guide", selected!), onSuccess: async () => { setSelectedId(null); await refresh(); } });
  const startNew = () => { setSelectedId(null); setIsNew(true); setForm({ name: "", rule_text: "", category: "voice" }); };
  return <LibraryLayout title="风格指南" count={items.length} onAdd={startNew} list={items.length === 0 ? <EmptyState icon={Sparkles} title="暂无写作规则" description="创建叙述口吻、用词禁忌或格式规则。" /> : <div className="library-list">{items.map((item) => <button key={item.id} className={item.id === selectedId ? "selected" : ""} onClick={() => setSelectedId(item.id)}><span className="category-chip">{item.category}</span><strong>{item.name}</strong><small>{item.rule_text}</small></button>)}</div>} detail={selected || isNew ? <form className="detail-form" onSubmit={(event) => { event.preventDefault(); save.mutate(); }}><DetailHeader title={isNew ? "新建风格规则" : form.name} onDelete={!isNew ? () => remove.mutate() : undefined} /><FormField label="规则名称"><input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></FormField><FormField label="分类"><select value={form.category} onChange={(event) => setForm({ ...form, category: event.target.value })}><option value="voice">叙述口吻</option><option value="dialogue">对白</option><option value="format">格式</option><option value="avoid">禁用项</option><option value="custom">自定义</option></select></FormField><FormField label="规则内容"><textarea rows={14} value={form.rule_text} onChange={(event) => setForm({ ...form, rule_text: event.target.value })} /></FormField><SaveBar disabled={!form.name.trim() || !form.rule_text.trim() || save.isPending} /></form> : <DetailPlaceholder />} />;
}

function LibraryLayout({ title, count, toolbar, onAdd, list, detail }: { title: string; count: number; toolbar?: ReactNode; onAdd: () => void; list: ReactNode; detail: ReactNode }) {
  return <section className="library-layout"><div className="library-browser"><header className="library-browser-header"><div><h2>{title}</h2><span>{count} 条</span></div><button className="primary-button compact" type="button" onClick={onAdd}><Plus size={16} />新建</button></header>{toolbar ? <div className="library-toolbar">{toolbar}</div> : null}<div className="library-list-scroll">{list}</div></div><aside className="library-detail">{detail}</aside></section>;
}

function DetailHeader({ title, onDelete }: { title: string; onDelete?: () => void }) { return <header className="detail-header"><div><span className="eyebrow">资料详情</span><h2>{title}</h2></div>{onDelete ? <button className="icon-button danger ghost" type="button" onClick={onDelete} title="移到回收站"><Trash2 size={17} /></button> : null}</header>; }
function SaveBar({ disabled }: { disabled: boolean }) { return <div className="detail-save-bar"><button className="primary-button" type="submit" disabled={disabled}><Save size={17} />保存</button></div>; }
function DetailPlaceholder() { return <EmptyState icon={BookMarked} title="选择一条资料" description="在左侧选择条目查看和编辑完整内容。" />; }
function EntitySelect({ entities, value, onChange }: { entities: StoryEntity[]; value: number; onChange: (value: number) => void }) { return <select value={value || ""} onChange={(event) => onChange(Number(event.target.value))}><option value="" disabled>请选择实体</option>{entities.map((item) => <option key={item.id} value={item.id}>{item.name} · {kindLabel(item.kind)}</option>)}</select>; }
function ChapterSelect({ tree, value, onChange }: { tree?: ProjectTree; value: number | null; onChange: (value: number | null) => void }) { return <select value={value ?? ""} onChange={(event) => onChange(event.target.value ? Number(event.target.value) : null)}><option value="">不关联章节</option>{tree?.chapters.map((chapter) => <option key={chapter.id} value={chapter.id}>{chapter.title}</option>)}</select>; }
function kindLabel(kind: string) { return ({ character: "人物", location: "地点", item: "物品", organization: "组织", concept: "概念" } as Record<string, string>)[kind] ?? kind; }
function foreshadowStatus(status: Foreshadow["status"]) { return ({ open: "待发展", developing: "发展中", resolved: "已回收", abandoned: "已放弃" } as const)[status]; }
function pickRelation(item: EntityRelation) { return { source_entity_id: item.source_entity_id, target_entity_id: item.target_entity_id, relation_type: item.relation_type, notes: item.notes }; }
function pickState(item: EntityStateChange) { return { entity_id: item.entity_id, chapter_id: item.chapter_id, field_name: item.field_name, old_value: item.old_value, new_value: item.new_value, reason: item.reason }; }
function pickTimeline(item: TimelineEvent) { return { chapter_id: item.chapter_id, label: item.label, event_time: item.event_time, description: item.description, position: item.position }; }
function pickForeshadow(item: Foreshadow) { return { setup_text: item.setup_text, payoff_text: item.payoff_text, status: item.status, chapter_id: item.chapter_id }; }
function pickStyle(item: StyleGuide) { return { name: item.name, rule_text: item.rule_text, category: item.category }; }
