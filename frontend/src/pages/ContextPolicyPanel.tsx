import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Plus,
  Save,
  Server,
  ShieldCheck,
  SlidersHorizontal,
  Trash2
} from "lucide-react";
import {
  api,
  type ContentClassificationValue,
  type ContextPolicy,
  type ContextPolicyInput,
  type ProviderDataPolicy,
  type ProviderDataPolicyInput
} from "../api/client";
import { EmptyState } from "../components/EmptyState";
import { FormField } from "../components/FormField";
import { CLASSIFICATION_LABELS, contextErrorMessage } from "./contextUi";

type PolicyMode = "context" | "provider";

const ALL_CLASSIFICATIONS = Object.keys(CLASSIFICATION_LABELS) as ContentClassificationValue[];
const DEFAULT_PRIORITIES: Record<string, number> = {
  user_task: 100,
  current_scene: 95,
  character_state: 90,
  upstream: 88,
  world_rules: 85,
  location_item_relation: 80,
  style: 75,
  timeline: 70,
  foreshadow: 68,
  neighbor_summaries: 55,
  history: 45
};

export function ContextPolicyPanel({
  projectId,
  onError
}: {
  projectId: number;
  onError: (message: string) => void;
}) {
  const [mode, setMode] = useState<PolicyMode>("context");
  return (
    <section className="context-policy-workbench">
      <nav className="policy-mode-tabs" aria-label="策略类型">
        <button className={mode === "context" ? "active" : ""} type="button" onClick={() => setMode("context")}><SlidersHorizontal size={15} />Context Policy</button>
        <button className={mode === "provider" ? "active" : ""} type="button" onClick={() => setMode("provider")}><ShieldCheck size={15} />Provider 数据边界</button>
      </nav>
      {mode === "context" ? <ProjectPolicyEditor projectId={projectId} onError={onError} /> : <ProviderPolicyEditor onError={onError} />}
    </section>
  );
}

function ProjectPolicyEditor({ projectId, onError }: { projectId: number; onError: (message: string) => void }) {
  const queryClient = useQueryClient();
  const query = useQuery({ queryKey: ["context-policies", projectId], queryFn: () => api.listContextPolicies(projectId) });
  const items = query.data ?? [];
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [isNew, setIsNew] = useState(false);
  const [form, setForm] = useState<ContextPolicyInput>(() => emptyPolicy(projectId));
  const [prioritiesJson, setPrioritiesJson] = useState(JSON.stringify(DEFAULT_PRIORITIES, null, 2));
  const selected = items.find((item) => item.id === selectedId) ?? null;
  useEffect(() => {
    if (!items.length || selectedId) return;
    setSelectedId(items[0].id);
  }, [items, selectedId]);
  useEffect(() => {
    if (!selected) return;
    const value = policyInput(selected);
    setForm(value);
    setPrioritiesJson(JSON.stringify(value.section_priorities, null, 2));
    setIsNew(false);
  }, [selected?.id, selected?.revision]);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["context-policies", projectId] });
  const save = useMutation({
    mutationFn: () => {
      const section_priorities = parsePriorityObject(prioritiesJson);
      const payload = { ...form, section_priorities };
      return selected && !isNew ? api.updateContextPolicy(selected, payload) : api.createContextPolicy(payload);
    },
    onSuccess: async (value) => { await refresh(); setSelectedId(value.id); setIsNew(false); onError(""); },
    onError: (error) => onError(contextErrorMessage(error))
  });
  const remove = useMutation({
    mutationFn: (item: ContextPolicy) => api.deleteContextRecord("context-policy", item),
    onSuccess: async () => { setSelectedId(null); await refresh(); },
    onError: (error) => onError(contextErrorMessage(error))
  });
  const startNew = () => {
    const value = emptyPolicy(projectId);
    setSelectedId(null);
    setIsNew(true);
    setForm(value);
    setPrioritiesJson(JSON.stringify(value.section_priorities, null, 2));
  };
  return (
    <div className="policy-editor-layout">
      <aside className="policy-list">
        <header><div><strong>项目策略</strong><span>{items.length} 条</span></div><button className="icon-button" type="button" title="新建策略" onClick={startNew}><Plus size={16} /></button></header>
        <div>{items.map((item) => <button key={item.id} className={item.id === selectedId ? "selected" : ""} type="button" onClick={() => setSelectedId(item.id)}><SlidersHorizontal size={15} /><span><strong>{item.name}</strong><small>{item.token_budget.toLocaleString()} Token · 最近 {item.recent_chapter_count} 章</small></span><i className={item.enabled ? "enabled" : ""} /></button>)}</div>
      </aside>
      <section className="policy-editor-detail">
        {selected || isNew ? <form className="policy-form" onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>
          <header><div><span>Context Policy</span><h2>{isNew ? "新建策略" : form.name}</h2></div>{selected ? <button className="icon-button ghost danger-ink" type="button" title="删除策略" onClick={() => remove.mutate(selected)}><Trash2 size={16} /></button> : null}</header>
          <FormField label="名称"><input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} /></FormField>
          <div className="policy-number-grid">
            <FormField label="Token 预算"><input type="number" min={128} value={form.token_budget} onChange={(event) => setForm({ ...form, token_budget: Number(event.target.value) })} /></FormField>
            <FormField label="最近章节数"><input type="number" min={0} max={100} value={form.recent_chapter_count} onChange={(event) => setForm({ ...form, recent_chapter_count: Number(event.target.value) })} /></FormField>
            <FormField label="结果上限"><input type="number" min={1} max={2000} value={form.max_results} onChange={(event) => setForm({ ...form, max_results: Number(event.target.value) })} /></FormField>
            <FormField label={`最低相关性 ${Math.round(form.min_relevance * 100)}%`}><input type="range" min={0} max={1} step={0.01} value={form.min_relevance} onChange={(event) => setForm({ ...form, min_relevance: Number(event.target.value) })} /></FormField>
          </div>
          <FormField label="强制区块（逗号分隔)"><input value={form.required_sections.join(", ")} onChange={(event) => setForm({ ...form, required_sections: commaValues(event.target.value) })} /></FormField>
          <ClassificationChecks value={form.allowed_classifications} onChange={(allowed_classifications) => setForm({ ...form, allowed_classifications })} />
          <FormField label="区块优先级 JSON"><textarea rows={13} value={prioritiesJson} onChange={(event) => setPrioritiesJson(event.target.value)} /></FormField>
          <div className="policy-switches"><label className="checkbox-row"><input type="checkbox" checked={form.use_summaries} onChange={(event) => setForm({ ...form, use_summaries: event.target.checked })} />优先使用摘要</label><label className="checkbox-row"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} />启用</label></div>
          <div className="policy-save"><button className="primary-button" type="submit" disabled={!form.name.trim() || !form.allowed_classifications.length || save.isPending}><Save size={16} />保存策略</button></div>
        </form> : <EmptyState icon={SlidersHorizontal} title="选择一个策略" description="" />}
      </section>
    </div>
  );
}

function ProviderPolicyEditor({ onError }: { onError: (message: string) => void }) {
  const queryClient = useQueryClient();
  const policiesQuery = useQuery({ queryKey: ["provider-data-policies"], queryFn: api.listProviderDataPolicies });
  const providersQuery = useQuery({ queryKey: ["providers"], queryFn: api.listProviders });
  const items = policiesQuery.data ?? [];
  const providers = providersQuery.data ?? [];
  const [selectedProviderId, setSelectedProviderId] = useState(0);
  const selected = items.find((item) => item.provider_account_id === selectedProviderId) ?? null;
  const [form, setForm] = useState<ProviderDataPolicyInput | null>(null);
  useEffect(() => {
    if (!selectedProviderId && items.length) setSelectedProviderId(items[0].provider_account_id);
  }, [items, selectedProviderId]);
  useEffect(() => { if (selected) setForm(providerPolicyInput(selected)); }, [selected?.id, selected?.revision]);
  const save = useMutation({
    mutationFn: () => api.updateProviderDataPolicy(selected!, form!),
    onSuccess: async () => { await queryClient.invalidateQueries({ queryKey: ["provider-data-policies"] }); onError(""); },
    onError: (error) => onError(contextErrorMessage(error))
  });
  const providerName = (id: number) => providers.find((item) => item.id === id)?.name ?? `Provider #${id}`;
  const providerType = (id: number) => providers.find((item) => item.id === id)?.provider_type ?? "unknown";
  return (
    <div className="policy-editor-layout">
      <aside className="policy-list provider-policy-list">
        <header><div><strong>Provider</strong><span>{items.length} 条</span></div></header>
        <div>{items.map((item) => <button key={item.id} className={item.provider_account_id === selectedProviderId ? "selected" : ""} type="button" onClick={() => setSelectedProviderId(item.provider_account_id)}><Server size={15} /><span><strong>{providerName(item.provider_account_id)}</strong><small>{providerType(item.provider_account_id)} · 允许 {item.allowed_classifications.length} 类</small></span><i className={item.enabled ? "enabled" : ""} /></button>)}</div>
      </aside>
      <section className="policy-editor-detail">
        {selected && form ? <form className="policy-form" onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>
          <header><div><span>Provider Data Policy</span><h2>{providerName(selected.provider_account_id)}</h2></div><span className="provider-type-chip">{providerType(selected.provider_account_id)}</span></header>
          <ClassificationChecks value={form.allowed_classifications} onChange={(allowed_classifications) => setForm({ ...form, allowed_classifications })} />
          <FormField label="策略说明"><textarea rows={10} value={form.notes} onChange={(event) => setForm({ ...form, notes: event.target.value })} /></FormField>
          <div className="policy-switches"><label className="checkbox-row"><input type="checkbox" checked={form.block_on_required_exclusion} onChange={(event) => setForm({ ...form, block_on_required_exclusion: event.target.checked })} />关键内容被排除时阻止</label><label className="checkbox-row"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} />启用</label></div>
          <div className="provider-boundary-preview"><ShieldCheck size={17} /><div><strong>当前允许范围</strong><span>{form.allowed_classifications.map((item) => CLASSIFICATION_LABELS[item]).join("、") || "无"}</span></div></div>
          <div className="policy-save"><button className="primary-button" type="submit" disabled={!form.allowed_classifications.length || save.isPending}><Save size={16} />保存边界</button></div>
        </form> : <EmptyState icon={Server} title="选择一个 Provider" description="" />}
      </section>
    </div>
  );
}

function ClassificationChecks({ value, onChange }: { value: ContentClassificationValue[]; onChange: (value: ContentClassificationValue[]) => void }) {
  return <fieldset className="classification-checks"><legend>允许的数据分类</legend>{ALL_CLASSIFICATIONS.map((item) => <label key={item}><input type="checkbox" checked={value.includes(item)} onChange={(event) => onChange(event.target.checked ? [...value, item] : value.filter((valueItem) => valueItem !== item))} /><span>{CLASSIFICATION_LABELS[item]}</span><small>{item}</small></label>)}</fieldset>;
}

function emptyPolicy(projectId: number): ContextPolicyInput {
  return {
    project_id: projectId,
    name: "新 Context Policy",
    token_budget: 6000,
    recent_chapter_count: 3,
    max_results: 80,
    min_relevance: 0.2,
    section_priorities: { ...DEFAULT_PRIORITIES },
    required_sections: ["user_task"],
    allowed_classifications: ALL_CLASSIFICATIONS.filter((item) => item !== "secret"),
    use_summaries: true,
    enabled: true
  };
}

function policyInput(item: ContextPolicy): ContextPolicyInput {
  return {
    project_id: item.project_id,
    name: item.name,
    token_budget: item.token_budget,
    recent_chapter_count: item.recent_chapter_count,
    max_results: item.max_results,
    min_relevance: item.min_relevance,
    section_priorities: item.section_priorities,
    required_sections: item.required_sections,
    allowed_classifications: item.allowed_classifications,
    use_summaries: item.use_summaries,
    enabled: item.enabled
  };
}

function providerPolicyInput(item: ProviderDataPolicy): ProviderDataPolicyInput {
  return {
    provider_account_id: item.provider_account_id,
    allowed_classifications: item.allowed_classifications,
    block_on_required_exclusion: item.block_on_required_exclusion,
    notes: item.notes,
    enabled: item.enabled
  };
}

function parsePriorityObject(value: string): Record<string, number> {
  const parsed = JSON.parse(value) as unknown;
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") throw new Error("区块优先级必须是 JSON 对象");
  const result: Record<string, number> = {};
  for (const [key, item] of Object.entries(parsed)) {
    if (typeof item !== "number" || item < 0 || item > 1000) throw new Error(`区块 ${key} 的优先级无效`);
    result[key] = item;
  }
  return result;
}

function commaValues(value: string): string[] {
  return value.split(/[,，]/).map((item) => item.trim()).filter(Boolean);
}
