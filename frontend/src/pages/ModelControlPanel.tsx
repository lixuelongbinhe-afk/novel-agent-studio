import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Activity,
  ArrowDown,
  ArrowUp,
  BrainCircuit,
  Gauge,
  Pencil,
  Plus,
  RefreshCw,
  Route as RouteIcon,
  Save,
  ShieldCheck,
  Trash2,
  X
} from "lucide-react";
import {
  api,
  type BudgetInput,
  type BudgetPolicy,
  type BudgetScope,
  type CapabilityProbe,
  type CapabilityStatus,
  type LimitScope,
  type ModelPricing,
  type ModelProfile,
  type ModelRoute,
  type ModelRouteInput,
  type Project,
  type Provider,
  type RateLimitInput,
  type RateLimitPolicy,
  type RouteStrategy
} from "../api/client";
import { Dialog } from "../components/Dialog";
import { EmptyState } from "../components/EmptyState";
import { ErrorNotice } from "../components/ErrorNotice";
import { FormField } from "../components/FormField";

type ControlTab = "capabilities" | "routes" | "limits" | "activity";

const CAPABILITY_STATUS: Array<[CapabilityStatus, string]> = [
  ["supported", "支持"],
  ["unsupported", "不支持"],
  ["unknown", "未知"],
  ["degraded", "降级"],
  ["emulated", "模拟"]
];

const ROUTE_STRATEGIES: Array<[RouteStrategy, string]> = [
  ["ordered_fallback", "有序回退"],
  ["lowest_cost", "最低费用"],
  ["lowest_latency", "最低延迟"],
  ["healthiest", "最健康"],
  ["manual_only", "仅手动选择"]
];

type PricingForm = {
  input: string;
  cached: string;
  output: string;
  reasoning: string;
  request: string;
  tool: string;
  currency: string;
  from: string;
  to: string;
};

type RouteForm = {
  projectId: string;
  name: string;
  strategy: RouteStrategy;
  requiredCapabilities: string;
  allowDegradation: boolean;
  enabled: boolean;
  modelIds: number[];
};

type LimitForm = {
  scopeType: LimitScope;
  scopeKey: string;
  concurrency: string;
  rpm: string;
  tpm: string;
  queueTimeout: string;
  enabled: boolean;
};

type BudgetForm = {
  scopeType: BudgetScope;
  scopeKey: string;
  maxCost: string;
  maxTokens: string;
  currency: string;
  enabled: boolean;
};

const nowLocal = () => {
  const date = new Date();
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
};

const emptyPricing = (): PricingForm => ({
  input: "",
  cached: "",
  output: "",
  reasoning: "",
  request: "",
  tool: "",
  currency: "USD",
  from: nowLocal(),
  to: ""
});

const emptyRoute = (): RouteForm => ({
  projectId: "",
  name: "",
  strategy: "ordered_fallback",
  requiredCapabilities: "",
  allowDegradation: true,
  enabled: true,
  modelIds: []
});

const emptyLimit = (): LimitForm => ({
  scopeType: "global",
  scopeKey: "*",
  concurrency: "",
  rpm: "",
  tpm: "",
  queueTimeout: "30",
  enabled: true
});

const emptyBudget = (): BudgetForm => ({
  scopeType: "per_request",
  scopeKey: "*",
  maxCost: "",
  maxTokens: "",
  currency: "USD",
  enabled: true
});

export function ModelControlPanel({ providers, models }: { providers: Provider[]; models: ModelProfile[] }) {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<ControlTab>("capabilities");
  const [selectedModelId, setSelectedModelId] = useState(models[0]?.id ?? 0);
  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: () => api.listProjects() });
  const routesQuery = useQuery({ queryKey: ["model-routes"], queryFn: () => api.listRoutes() });
  const limitsQuery = useQuery({ queryKey: ["rate-limits"], queryFn: api.listRateLimits });
  const budgetsQuery = useQuery({ queryKey: ["budgets"], queryFn: api.listBudgets });
  const healthQuery = useQuery({ queryKey: ["provider-health"], queryFn: api.listProviderHealth });
  const invocationsQuery = useQuery({ queryKey: ["model-invocations"], queryFn: () => api.listInvocations(100) });
  const projects = projectsQuery.data ?? [];
  const routes = routesQuery.data ?? [];

  useEffect(() => {
    if (models.some((model) => model.id === selectedModelId)) return;
    setSelectedModelId(models[0]?.id ?? 0);
  }, [models, selectedModelId]);

  return (
    <div className="control-shell">
      <nav className="control-subtabs" aria-label="模型控制视图">
        <button type="button" className={tab === "capabilities" ? "active" : ""} onClick={() => setTab("capabilities")}>
          <BrainCircuit size={16} />能力与价格
        </button>
        <button type="button" className={tab === "routes" ? "active" : ""} onClick={() => setTab("routes")}>
          <RouteIcon size={16} />Route
        </button>
        <button type="button" className={tab === "limits" ? "active" : ""} onClick={() => setTab("limits")}>
          <Gauge size={16} />限流与预算
        </button>
        <button type="button" className={tab === "activity" ? "active" : ""} onClick={() => setTab("activity")}>
          <Activity size={16} />健康与调用
        </button>
      </nav>

      {tab === "capabilities" ? (
        <CapabilitiesView
          models={models}
          selectedModelId={selectedModelId}
          onSelectModel={setSelectedModelId}
        />
      ) : null}
      {tab === "routes" ? (
        <RoutesView
          routes={routes}
          models={models}
          projects={projects}
          loading={routesQuery.isLoading}
          error={routesQuery.error ? "无法读取模型 Route。" : null}
        />
      ) : null}
      {tab === "limits" ? (
        <LimitsView
          limits={limitsQuery.data ?? []}
          budgets={budgetsQuery.data ?? []}
          providers={providers}
          models={models}
          projects={projects}
          routes={routes}
          loading={limitsQuery.isLoading || budgetsQuery.isLoading}
        />
      ) : null}
      {tab === "activity" ? (
        <ActivityView
          providers={providers}
          models={models}
          health={healthQuery.data ?? []}
          invocations={invocationsQuery.data ?? []}
          loading={healthQuery.isLoading || invocationsQuery.isLoading}
          onRefresh={async () => {
            await Promise.all([
              queryClient.invalidateQueries({ queryKey: ["provider-health"] }),
              queryClient.invalidateQueries({ queryKey: ["model-invocations"] })
            ]);
          }}
        />
      ) : null}
    </div>
  );
}

function CapabilitiesView({
  models,
  selectedModelId,
  onSelectModel
}: {
  models: ModelProfile[];
  selectedModelId: number;
  onSelectModel: (id: number) => void;
}) {
  const queryClient = useQueryClient();
  const capabilitiesQuery = useQuery({
    queryKey: ["model-capabilities", selectedModelId],
    queryFn: () => api.modelCapabilities(selectedModelId),
    enabled: selectedModelId > 0
  });
  const probesQuery = useQuery({
    queryKey: ["capability-probes", selectedModelId],
    queryFn: () => api.listCapabilityProbes(selectedModelId),
    enabled: selectedModelId > 0
  });
  const pricingQuery = useQuery({
    queryKey: ["model-pricing", selectedModelId],
    queryFn: () => api.listModelPricing(selectedModelId),
    enabled: selectedModelId > 0
  });
  const [pricingOpen, setPricingOpen] = useState(false);
  const [pricingForm, setPricingForm] = useState<PricingForm>(emptyPricing);

  const overrideCapability = useMutation({
    mutationFn: ({ capability, status }: { capability: string; status: CapabilityStatus }) =>
      api.setCapabilityOverride(selectedModelId, capability, status),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["model-capabilities", selectedModelId] })
  });
  const clearOverride = useMutation({
    mutationFn: (capability: string) => api.clearCapabilityOverride(selectedModelId, capability),
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["model-capabilities", selectedModelId] })
  });
  const runProbe = useMutation({
    mutationFn: ({ level, confirmed }: { level: CapabilityProbe["level"]; confirmed: boolean }) =>
      api.runCapabilityProbe(selectedModelId, level, confirmed),
    onSuccess: async () => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["model-capabilities", selectedModelId] }),
        queryClient.invalidateQueries({ queryKey: ["capability-probes", selectedModelId] })
      ]);
    }
  });
  const savePricing = useMutation({
    mutationFn: () => api.createModelPricing(selectedModelId, pricingPayload(pricingForm)),
    onSuccess: async () => {
      setPricingOpen(false);
      setPricingForm(emptyPricing());
      await queryClient.invalidateQueries({ queryKey: ["model-pricing", selectedModelId] });
    }
  });
  const deletePricing = useMutation({
    mutationFn: api.deleteModelPricing,
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["model-pricing", selectedModelId] })
  });

  if (models.length === 0) return <EmptyState icon={BrainCircuit} title="没有可配置的模型" description="请先在 Provider 与模型中添加模型。" />;
  const capabilities = capabilitiesQuery.data?.capabilities ?? [];
  const latestProbe = probesQuery.data?.[0];
  return (
    <div className="control-view">
      <header className="control-toolbar">
        <FormField label="模型">
          <select value={selectedModelId} onChange={(event) => onSelectModel(Number(event.target.value))}>
            {models.map((model) => <option key={model.id} value={model.id}>{model.display_name}</option>)}
          </select>
        </FormField>
        <div className="control-toolbar-actions">
          <button className="secondary-button compact" type="button" disabled={runProbe.isPending} onClick={() => runProbe.mutate({ level: "basic", confirmed: false })}>基础探测</button>
          <button className="secondary-button compact" type="button" disabled={runProbe.isPending} onClick={() => runProbe.mutate({ level: "standard", confirmed: false })}>标准探测</button>
          <button
            className="secondary-button compact"
            type="button"
            disabled={runProbe.isPending}
            onClick={() => {
              if (window.confirm("高级探测最多发出 4 个合成请求，确认继续？")) runProbe.mutate({ level: "advanced", confirmed: true });
            }}
          >高级探测</button>
          <button className="primary-button compact" type="button" onClick={() => { setPricingForm(emptyPricing()); setPricingOpen(true); }}><Plus size={15} />价格</button>
        </div>
      </header>
      {capabilitiesQuery.error ? <ErrorNotice message="能力配置读取失败。" /> : null}
      {runProbe.error ? <ErrorNotice message="能力探测失败，请检查价格、连接和高级确认。" /> : null}
      {latestProbe ? (
        <div className="control-summary-line">
          <span>最近探测</span><strong>{probeLevelLabel(latestProbe.level)} · {latestProbe.status}</strong>
          <span>{latestProbe.request_count} 请求</span><span>{latestProbe.error_code ?? "无错误"}</span>
        </div>
      ) : null}
      <div className="control-table-wrap">
        <table className="control-table">
          <thead><tr><th>能力</th><th>有效状态</th><th>来源</th><th>判定</th><th aria-label="操作" /></tr></thead>
          <tbody>
            {capabilities.map((item) => (
              <tr key={item.capability}>
                <td><strong>{capabilityLabel(item.capability)}</strong><small>{item.capability}</small></td>
                <td>
                  <select className={`status-select status-${item.status}`} value={item.status} aria-label={`${item.capability} 状态`} onChange={(event) => overrideCapability.mutate({ capability: item.capability, status: event.target.value as CapabilityStatus })}>
                    {CAPABILITY_STATUS.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
                  </select>
                </td>
                <td>{sourceLabel(item.source)}</td>
                <td className="reason-cell">{item.reason}</td>
                <td>{item.source === "manual_override" ? <button className="icon-button ghost" type="button" title="清除手动覆盖" aria-label={`清除 ${item.capability} 手动覆盖`} onClick={() => clearOverride.mutate(item.capability)}><X size={15} /></button> : null}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <section className="control-section">
        <header><div><span className="eyebrow">Pricing History</span><h2>价格历史</h2></div></header>
        <div className="control-table-wrap">
          <table className="control-table compact-table">
            <thead><tr><th>生效时间</th><th>输入 / 百万</th><th>缓存 / 百万</th><th>输出 / 百万</th><th>推理 / 百万</th><th>请求</th><th>工具</th><th /></tr></thead>
            <tbody>
              {(pricingQuery.data ?? []).map((item) => (
                <tr key={item.id}>
                  <td>{formatDate(item.effective_from)}<small>{item.effective_to ? `至 ${formatDate(item.effective_to)}` : "当前"}</small></td>
                  <td>{moneyOrUnknown(item.input_per_million, item.currency)}</td>
                  <td>{moneyOrUnknown(item.cached_input_per_million, item.currency)}</td>
                  <td>{moneyOrUnknown(item.output_per_million, item.currency)}</td>
                  <td>{moneyOrUnknown(item.reasoning_per_million, item.currency)}</td>
                  <td>{moneyOrUnknown(item.request_fee, item.currency)}</td>
                  <td>{moneyOrUnknown(item.tool_call_fee, item.currency)}</td>
                  <td><button className="icon-button ghost danger-ink" type="button" title="删除价格记录" aria-label={`删除价格 ${item.id}`} onClick={() => { if (window.confirm("删除这条价格记录？")) deletePricing.mutate(item); }}><Trash2 size={15} /></button></td>
                </tr>
              ))}
              {!pricingQuery.isLoading && (pricingQuery.data ?? []).length === 0 ? <tr><td colSpan={8} className="empty-cell">尚无价格记录</td></tr> : null}
            </tbody>
          </table>
        </div>
      </section>

      <Dialog
        open={pricingOpen}
        title="新增价格区间"
        onClose={() => setPricingOpen(false)}
        footer={<><button className="secondary-button" type="button" onClick={() => setPricingOpen(false)}>取消</button><button className="primary-button" type="submit" form="pricing-form" disabled={savePricing.isPending}><Save size={16} />保存</button></>}
      >
        <form id="pricing-form" className="form-grid" onSubmit={(event) => { event.preventDefault(); savePricing.mutate(); }}>
          {savePricing.error ? <ErrorNotice message="价格保存失败，请检查生效区间是否重叠。" /> : null}
          <div className="form-row"><FormField label="币种"><input value={pricingForm.currency} maxLength={12} onChange={(event) => setPricingForm({ ...pricingForm, currency: event.target.value.toUpperCase() })} /></FormField><FormField label="每次请求"><input type="number" min="0" step="any" value={pricingForm.request} onChange={(event) => setPricingForm({ ...pricingForm, request: event.target.value })} placeholder="未知" /></FormField></div>
          <div className="form-row"><FormField label="输入 / 百万"><input type="number" min="0" step="any" value={pricingForm.input} onChange={(event) => setPricingForm({ ...pricingForm, input: event.target.value })} placeholder="未知" /></FormField><FormField label="缓存输入 / 百万"><input type="number" min="0" step="any" value={pricingForm.cached} onChange={(event) => setPricingForm({ ...pricingForm, cached: event.target.value })} placeholder="未知" /></FormField></div>
          <div className="form-row"><FormField label="输出 / 百万"><input type="number" min="0" step="any" value={pricingForm.output} onChange={(event) => setPricingForm({ ...pricingForm, output: event.target.value })} placeholder="未知" /></FormField><FormField label="推理 / 百万"><input type="number" min="0" step="any" value={pricingForm.reasoning} onChange={(event) => setPricingForm({ ...pricingForm, reasoning: event.target.value })} placeholder="未知" /></FormField></div>
          <FormField label="每次工具调用"><input type="number" min="0" step="any" value={pricingForm.tool} onChange={(event) => setPricingForm({ ...pricingForm, tool: event.target.value })} placeholder="未知" /></FormField>
          <div className="form-row"><FormField label="生效时间"><input type="datetime-local" value={pricingForm.from} onChange={(event) => setPricingForm({ ...pricingForm, from: event.target.value })} /></FormField><FormField label="失效时间"><input type="datetime-local" value={pricingForm.to} onChange={(event) => setPricingForm({ ...pricingForm, to: event.target.value })} /></FormField></div>
        </form>
      </Dialog>
    </div>
  );
}

function RoutesView({ routes, models, projects, loading, error }: { routes: ModelRoute[]; models: ModelProfile[]; projects: Project[]; loading: boolean; error: string | null }) {
  const queryClient = useQueryClient();
  const [open, setOpen] = useState(false);
  const [editing, setEditing] = useState<ModelRoute | null>(null);
  const [form, setForm] = useState<RouteForm>(emptyRoute);
  const save = useMutation({
    mutationFn: () => editing ? api.updateRoute(editing, routePayload(form)) : api.createRoute(routePayload(form)),
    onSuccess: async () => { setOpen(false); await queryClient.invalidateQueries({ queryKey: ["model-routes"] }); }
  });
  const remove = useMutation({ mutationFn: api.deleteRoute, onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["model-routes"] }) });
  function openCreate() { setEditing(null); setForm(emptyRoute()); save.reset(); setOpen(true); }
  function openEdit(route: ModelRoute) {
    setEditing(route);
    setForm({ projectId: route.project_id?.toString() ?? "", name: route.name, strategy: route.strategy, requiredCapabilities: route.required_capabilities.join(", "), allowDegradation: route.allow_degradation, enabled: route.enabled, modelIds: [...route.entries].sort((a, b) => a.position - b.position).map((item) => item.model_profile_id) });
    save.reset(); setOpen(true);
  }
  function toggleModel(id: number) { setForm((current) => ({ ...current, modelIds: current.modelIds.includes(id) ? current.modelIds.filter((item) => item !== id) : [...current.modelIds, id] })); }
  function moveModel(id: number, direction: -1 | 1) {
    setForm((current) => {
      const index = current.modelIds.indexOf(id); const target = index + direction;
      if (index < 0 || target < 0 || target >= current.modelIds.length) return current;
      const next = [...current.modelIds]; [next[index], next[target]] = [next[target], next[index]];
      return { ...current, modelIds: next };
    });
  }
  if (error) return <ErrorNotice message={error} />;
  return (
    <div className="control-view">
      <header className="control-toolbar"><div><span className="eyebrow">Model Route</span><h2>模型路由</h2></div><button className="primary-button compact" type="button" onClick={openCreate}><Plus size={15} />新建 Route</button></header>
      {loading ? <p className="muted">正在读取 Route…</p> : null}
      <div className="control-table-wrap">
        <table className="control-table">
          <thead><tr><th>名称</th><th>策略</th><th>范围</th><th>模型顺序</th><th>必需能力</th><th>状态</th><th /></tr></thead>
          <tbody>
            {routes.map((route) => (
              <tr key={route.id}>
                <td><strong>{route.name}</strong><small>rev {route.revision}</small></td>
                <td>{routeStrategyLabel(route.strategy)}</td>
                <td>{route.project_id ? projects.find((item) => item.id === route.project_id)?.title ?? `项目 #${route.project_id}` : "全局"}</td>
                <td className="route-models-cell">{[...route.entries].sort((a, b) => a.position - b.position).map((entry) => models.find((model) => model.id === entry.model_profile_id)?.display_name ?? `模型 #${entry.model_profile_id}`).join(" → ")}</td>
                <td>{route.required_capabilities.length ? route.required_capabilities.join(", ") : "无"}</td>
                <td><span className={`status-chip ${route.enabled ? "enabled" : "disabled"}`}>{route.enabled ? (route.allow_degradation ? "启用 · 可降级" : "启用 · 严格") : "停用"}</span></td>
                <td><div className="row-actions"><button className="icon-button ghost" type="button" title="编辑 Route" aria-label={`编辑 Route ${route.name}`} onClick={() => openEdit(route)}><Pencil size={15} /></button><button className="icon-button ghost danger-ink" type="button" title="删除 Route" aria-label={`删除 Route ${route.name}`} onClick={() => { if (window.confirm(`删除 Route“${route.name}”？`)) remove.mutate(route); }}><Trash2 size={15} /></button></div></td>
              </tr>
            ))}
            {!loading && routes.length === 0 ? <tr><td colSpan={7} className="empty-cell">尚无 Route</td></tr> : null}
          </tbody>
        </table>
      </div>
      <Dialog open={open} title={editing ? "编辑 Route" : "新建 Route"} width="large" onClose={() => setOpen(false)} footer={<><button className="secondary-button" type="button" onClick={() => setOpen(false)}>取消</button><button className="primary-button" type="submit" form="route-form" disabled={!form.name.trim() || form.modelIds.length === 0 || save.isPending}><Save size={16} />保存</button></>}>
        <form id="route-form" className="form-grid" onSubmit={(event) => { event.preventDefault(); save.mutate(); }}>
          {save.error ? <ErrorNotice message="Route 保存失败，请检查项目、模型和 revision。" /> : null}
          <div className="form-row"><FormField label="名称"><input value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} autoFocus /></FormField><FormField label="策略"><select value={form.strategy} onChange={(event) => setForm({ ...form, strategy: event.target.value as RouteStrategy })}>{ROUTE_STRATEGIES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></FormField></div>
          <div className="form-row"><FormField label="项目范围"><select value={form.projectId} onChange={(event) => setForm({ ...form, projectId: event.target.value })}><option value="">全局</option>{projects.map((project) => <option key={project.id} value={project.id}>{project.title}</option>)}</select></FormField><FormField label="必需能力"><input value={form.requiredCapabilities} onChange={(event) => setForm({ ...form, requiredCapabilities: event.target.value })} placeholder="json_schema, streaming" /></FormField></div>
          <div className="form-row"><label className="checkbox-row"><input type="checkbox" checked={form.allowDegradation} onChange={(event) => setForm({ ...form, allowDegradation: event.target.checked })} /><span>允许安全降级</span></label><label className="checkbox-row"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} /><span>启用 Route</span></label></div>
          <fieldset className="model-order-fieldset"><legend>模型顺序</legend>{models.map((model) => { const selected = form.modelIds.includes(model.id); const index = form.modelIds.indexOf(model.id); return <div key={model.id} className={selected ? "selected" : ""}><label><input type="checkbox" checked={selected} onChange={() => toggleModel(model.id)} /><span><strong>{model.display_name}</strong><small>{model.name}</small></span></label>{selected ? <div><span>{index + 1}</span><button className="icon-button ghost" type="button" title="上移" aria-label={`上移 ${model.display_name}`} disabled={index === 0} onClick={() => moveModel(model.id, -1)}><ArrowUp size={14} /></button><button className="icon-button ghost" type="button" title="下移" aria-label={`下移 ${model.display_name}`} disabled={index === form.modelIds.length - 1} onClick={() => moveModel(model.id, 1)}><ArrowDown size={14} /></button></div> : null}</div>; })}</fieldset>
        </form>
      </Dialog>
    </div>
  );
}

function LimitsView({ limits, budgets, providers, models, projects, routes, loading }: { limits: RateLimitPolicy[]; budgets: BudgetPolicy[]; providers: Provider[]; models: ModelProfile[]; projects: Project[]; routes: ModelRoute[]; loading: boolean }) {
  const queryClient = useQueryClient();
  const [limitOpen, setLimitOpen] = useState(false); const [editingLimit, setEditingLimit] = useState<RateLimitPolicy | null>(null); const [limitForm, setLimitForm] = useState<LimitForm>(emptyLimit);
  const [budgetOpen, setBudgetOpen] = useState(false); const [editingBudget, setEditingBudget] = useState<BudgetPolicy | null>(null); const [budgetForm, setBudgetForm] = useState<BudgetForm>(emptyBudget);
  const saveLimit = useMutation({ mutationFn: () => editingLimit ? api.updateRateLimit(editingLimit, limitPayload(limitForm)) : api.createRateLimit(limitPayload(limitForm)), onSuccess: async () => { setLimitOpen(false); await queryClient.invalidateQueries({ queryKey: ["rate-limits"] }); } });
  const removeLimit = useMutation({ mutationFn: api.deleteRateLimit, onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["rate-limits"] }) });
  const saveBudget = useMutation({ mutationFn: () => editingBudget ? api.updateBudget(editingBudget, budgetPayload(budgetForm)) : api.createBudget(budgetPayload(budgetForm)), onSuccess: async () => { setBudgetOpen(false); await queryClient.invalidateQueries({ queryKey: ["budgets"] }); } });
  const removeBudget = useMutation({ mutationFn: api.deleteBudget, onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["budgets"] }) });
  function openLimit(item?: RateLimitPolicy) { setEditingLimit(item ?? null); setLimitForm(item ? { scopeType: item.scope_type, scopeKey: item.scope_key, concurrency: numberText(item.max_concurrency), rpm: numberText(item.requests_per_minute), tpm: numberText(item.tokens_per_minute), queueTimeout: String(item.queue_timeout_seconds), enabled: item.enabled } : emptyLimit()); saveLimit.reset(); setLimitOpen(true); }
  function openBudget(item?: BudgetPolicy) { setEditingBudget(item ?? null); setBudgetForm(item ? { scopeType: item.scope_type, scopeKey: item.scope_key, maxCost: numberText(item.max_cost), maxTokens: numberText(item.max_tokens), currency: item.currency, enabled: item.enabled } : emptyBudget()); saveBudget.reset(); setBudgetOpen(true); }
  return (
    <div className="control-view">
      {loading ? <p className="muted">正在读取控制策略…</p> : null}
      <section className="control-section">
        <header><div><span className="eyebrow">Concurrency · RPM · TPM</span><h2>分层限流</h2></div><button className="primary-button compact" type="button" onClick={() => openLimit()}><Plus size={15} />限流</button></header>
        <div className="control-table-wrap"><table className="control-table"><thead><tr><th>范围</th><th>并发</th><th>RPM</th><th>TPM</th><th>队列超时</th><th>状态</th><th /></tr></thead><tbody>{limits.map((item) => <tr key={item.id}><td><strong>{limitScopeLabel(item.scope_type)}</strong><small>{scopeKeyLabel(item.scope_type, item.scope_key, { providers, models, projects, routes })}</small></td><td>{item.max_concurrency ?? "—"}</td><td>{item.requests_per_minute ?? "—"}</td><td>{item.tokens_per_minute?.toLocaleString() ?? "—"}</td><td>{item.queue_timeout_seconds}s</td><td><span className={`status-chip ${item.enabled ? "enabled" : "disabled"}`}>{item.enabled ? "启用" : "停用"}</span></td><td><div className="row-actions"><button className="icon-button ghost" type="button" title="编辑限流" aria-label={`编辑限流 ${item.id}`} onClick={() => openLimit(item)}><Pencil size={15} /></button><button className="icon-button ghost danger-ink" type="button" title="删除限流" aria-label={`删除限流 ${item.id}`} onClick={() => { if (window.confirm("删除这条限流策略？")) removeLimit.mutate(item); }}><Trash2 size={15} /></button></div></td></tr>)}{!loading && limits.length === 0 ? <tr><td colSpan={7} className="empty-cell">尚无限流策略</td></tr> : null}</tbody></table></div>
      </section>
      <section className="control-section">
        <header><div><span className="eyebrow">Request · Daily · Route Run</span><h2>预算</h2></div><button className="primary-button compact" type="button" onClick={() => openBudget()}><Plus size={15} />预算</button></header>
        <div className="control-table-wrap"><table className="control-table"><thead><tr><th>范围</th><th>Token 上限</th><th>费用上限</th><th>币种</th><th>状态</th><th /></tr></thead><tbody>{budgets.map((item) => <tr key={item.id}><td><strong>{budgetScopeLabel(item.scope_type)}</strong><small>{budgetScopeKeyLabel(item, projects, routes)}</small></td><td>{item.max_tokens?.toLocaleString() ?? "—"}</td><td>{item.max_cost === null ? "—" : item.max_cost.toLocaleString()}</td><td>{item.currency}</td><td><span className={`status-chip ${item.enabled ? "enabled" : "disabled"}`}>{item.enabled ? "启用" : "停用"}</span></td><td><div className="row-actions"><button className="icon-button ghost" type="button" title="编辑预算" aria-label={`编辑预算 ${item.id}`} onClick={() => openBudget(item)}><Pencil size={15} /></button><button className="icon-button ghost danger-ink" type="button" title="删除预算" aria-label={`删除预算 ${item.id}`} onClick={() => { if (window.confirm("删除这条预算策略？")) removeBudget.mutate(item); }}><Trash2 size={15} /></button></div></td></tr>)}{!loading && budgets.length === 0 ? <tr><td colSpan={6} className="empty-cell">尚无预算策略</td></tr> : null}</tbody></table></div>
      </section>
      <Dialog open={limitOpen} title={editingLimit ? "编辑限流" : "新增限流"} onClose={() => setLimitOpen(false)} footer={<><button className="secondary-button" type="button" onClick={() => setLimitOpen(false)}>取消</button><button className="primary-button" type="submit" form="limit-form" disabled={saveLimit.isPending || (!limitForm.concurrency && !limitForm.rpm && !limitForm.tpm)}><Save size={16} />保存</button></>}>
        <form id="limit-form" className="form-grid" onSubmit={(event) => { event.preventDefault(); saveLimit.mutate(); }}>{saveLimit.error ? <ErrorNotice message="限流保存失败，请检查范围是否重复。" /> : null}<div className="form-row"><FormField label="范围"><select value={limitForm.scopeType} onChange={(event) => { const scope = event.target.value as LimitScope; setLimitForm({ ...limitForm, scopeType: scope, scopeKey: defaultScopeKey(scope, { providers, models, projects, routes }) }); }}>{(["global", "project", "provider", "model", "route", "workflow"] as LimitScope[]).map((value) => <option key={value} value={value}>{limitScopeLabel(value)}</option>)}</select></FormField><ScopeKeyField scope={limitForm.scopeType} value={limitForm.scopeKey} onChange={(scopeKey) => setLimitForm({ ...limitForm, scopeKey })} providers={providers} models={models} projects={projects} routes={routes} /></div><div className="form-row"><FormField label="最大并发"><input type="number" min="1" value={limitForm.concurrency} onChange={(event) => setLimitForm({ ...limitForm, concurrency: event.target.value })} /></FormField><FormField label="队列超时（秒）"><input type="number" min="0.1" step="0.1" value={limitForm.queueTimeout} onChange={(event) => setLimitForm({ ...limitForm, queueTimeout: event.target.value })} /></FormField></div><div className="form-row"><FormField label="RPM"><input type="number" min="1" value={limitForm.rpm} onChange={(event) => setLimitForm({ ...limitForm, rpm: event.target.value })} /></FormField><FormField label="TPM"><input type="number" min="1" value={limitForm.tpm} onChange={(event) => setLimitForm({ ...limitForm, tpm: event.target.value })} /></FormField></div><label className="checkbox-row"><input type="checkbox" checked={limitForm.enabled} onChange={(event) => setLimitForm({ ...limitForm, enabled: event.target.checked })} /><span>启用限流</span></label></form>
      </Dialog>
      <Dialog open={budgetOpen} title={editingBudget ? "编辑预算" : "新增预算"} onClose={() => setBudgetOpen(false)} footer={<><button className="secondary-button" type="button" onClick={() => setBudgetOpen(false)}>取消</button><button className="primary-button" type="submit" form="budget-form" disabled={saveBudget.isPending || (!budgetForm.maxCost && !budgetForm.maxTokens)}><Save size={16} />保存</button></>}>
        <form id="budget-form" className="form-grid" onSubmit={(event) => { event.preventDefault(); saveBudget.mutate(); }}>{saveBudget.error ? <ErrorNotice message="预算保存失败，请检查范围、币种和上限。" /> : null}<div className="form-row"><FormField label="范围"><select value={budgetForm.scopeType} onChange={(event) => { const scope = event.target.value as BudgetScope; setBudgetForm({ ...budgetForm, scopeType: scope, scopeKey: scope === "per_request" ? "*" : scope === "project_daily" ? projects[0]?.id.toString() ?? "" : routes[0]?.id.toString() ?? "" }); }}><option value="per_request">单次请求</option><option value="project_daily">项目每日</option><option value="route_per_run">Route 单次运行</option></select></FormField><BudgetScopeKeyField scope={budgetForm.scopeType} value={budgetForm.scopeKey} onChange={(scopeKey) => setBudgetForm({ ...budgetForm, scopeKey })} projects={projects} routes={routes} /></div><div className="form-row"><FormField label="Token 上限"><input type="number" min="1" value={budgetForm.maxTokens} onChange={(event) => setBudgetForm({ ...budgetForm, maxTokens: event.target.value })} /></FormField><FormField label="费用上限"><input type="number" min="0" step="any" value={budgetForm.maxCost} onChange={(event) => setBudgetForm({ ...budgetForm, maxCost: event.target.value })} /></FormField></div><FormField label="币种"><input value={budgetForm.currency} onChange={(event) => setBudgetForm({ ...budgetForm, currency: event.target.value.toUpperCase() })} /></FormField><label className="checkbox-row"><input type="checkbox" checked={budgetForm.enabled} onChange={(event) => setBudgetForm({ ...budgetForm, enabled: event.target.checked })} /><span>启用预算</span></label></form>
      </Dialog>
    </div>
  );
}

function ActivityView({ providers, models, health, invocations, loading, onRefresh }: { providers: Provider[]; models: ModelProfile[]; health: Awaited<ReturnType<typeof api.listProviderHealth>>; invocations: Awaited<ReturnType<typeof api.listInvocations>>; loading: boolean; onRefresh: () => Promise<void> }) {
  const queryClient = useQueryClient();
  const reset = useMutation({ mutationFn: api.resetProviderHealth, onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["provider-health"] }) });
  return <div className="control-view"><header className="control-toolbar"><div><span className="eyebrow">Circuit Breaker</span><h2>Provider 健康</h2></div><button className="secondary-button compact" type="button" disabled={loading} onClick={() => void onRefresh()}><RefreshCw size={15} className={loading ? "spin" : ""} />刷新</button></header><div className="health-grid">{health.map((item) => { const provider = providers.find((value) => value.id === item.provider_account_id); return <section key={item.id} className="health-row"><div><ShieldCheck size={18} /><span><strong>{provider?.name ?? `Provider #${item.provider_account_id}`}</strong><small>{item.last_error_code ?? "无错误"}</small></span></div><span className={`circuit-state circuit-${item.state}`}>{circuitLabel(item.state)}</span><dl><div><dt>连续失败</dt><dd>{item.consecutive_failures} / {item.failure_threshold}</dd></div><div><dt>最近延迟</dt><dd>{item.last_latency_ms === null ? "—" : `${item.last_latency_ms} ms`}</dd></div><div><dt>最近成功</dt><dd>{formatDate(item.last_success_at)}</dd></div></dl><button className="secondary-button compact" type="button" onClick={() => reset.mutate(item.provider_account_id)}>重置</button></section>; })}</div>{!loading && health.length === 0 ? <p className="muted">尚无 Provider 健康记录。</p> : null}<section className="control-section"><header><div><span className="eyebrow">Usage Ledger</span><h2>调用记录</h2></div></header><div className="control-table-wrap"><table className="control-table compact-table"><thead><tr><th>时间</th><th>模型</th><th>状态</th><th>Tokens</th><th>Token 来源</th><th>费用</th><th>排队 / 延迟</th><th>回退</th></tr></thead><tbody>{invocations.map((item) => <tr key={item.id}><td>{formatDate(item.started_at)}<small>{item.request_id.slice(0, 18)}</small></td><td>{models.find((model) => model.id === item.model_profile_id)?.display_name ?? `模型 #${item.model_profile_id}`}</td><td><span className={`status-chip ${item.status === "completed" ? "enabled" : "disabled"}`}>{item.status}</span>{item.error_code ? <small>{item.error_code}</small> : null}</td><td>{item.total_tokens.toLocaleString()}<small>{item.usage_estimated ? "估算" : "实际"}</small></td><td>{tokenSourceLabel(item.token_source)}</td><td>{item.cost_known && item.cost !== null ? `${item.currency} ${item.cost.toFixed(6)}` : "未知"}</td><td>{item.queue_ms} / {item.latency_ms ?? "—"} ms</td><td>{item.fallback_count}</td></tr>)}{!loading && invocations.length === 0 ? <tr><td colSpan={8} className="empty-cell">尚无调用记录</td></tr> : null}</tbody></table></div></section></div>;
}

function ScopeKeyField({ scope, value, onChange, providers, models, projects, routes }: { scope: LimitScope; value: string; onChange: (value: string) => void; providers: Provider[]; models: ModelProfile[]; projects: Project[]; routes: ModelRoute[] }) {
  if (scope === "global") return <FormField label="标识"><input value="*" disabled /></FormField>;
  if (scope === "workflow") return <FormField label="Workflow 标识"><input value={value} onChange={(event) => onChange(event.target.value)} /></FormField>;
  const values = scope === "project" ? projects.map((item) => [item.id, item.title] as const) : scope === "provider" ? providers.map((item) => [item.id, item.name] as const) : scope === "model" ? models.map((item) => [item.id, item.display_name] as const) : routes.map((item) => [item.id, item.name] as const);
  return <FormField label="标识"><select value={value} onChange={(event) => onChange(event.target.value)}>{values.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></FormField>;
}

function BudgetScopeKeyField({ scope, value, onChange, projects, routes }: { scope: BudgetScope; value: string; onChange: (value: string) => void; projects: Project[]; routes: ModelRoute[] }) {
  if (scope === "per_request") return <FormField label="标识"><input value="*" disabled /></FormField>;
  const values = scope === "project_daily" ? projects.map((item) => [item.id, item.title] as const) : routes.map((item) => [item.id, item.name] as const);
  return <FormField label="标识"><select value={value} onChange={(event) => onChange(event.target.value)}>{values.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></FormField>;
}

function pricingPayload(form: PricingForm) {
  return { input_per_million: nullableNumber(form.input), cached_input_per_million: nullableNumber(form.cached), output_per_million: nullableNumber(form.output), reasoning_per_million: nullableNumber(form.reasoning), request_fee: nullableNumber(form.request), tool_call_fee: nullableNumber(form.tool), currency: form.currency, effective_from: new Date(form.from).toISOString(), effective_to: form.to ? new Date(form.to).toISOString() : null };
}

function routePayload(form: RouteForm): ModelRouteInput {
  return { project_id: form.projectId ? Number(form.projectId) : null, name: form.name.trim(), strategy: form.strategy, required_capabilities: form.requiredCapabilities.split(",").map((item) => item.trim().toLowerCase()).filter(Boolean), allow_degradation: form.allowDegradation, enabled: form.enabled, entries: form.modelIds.map((model_profile_id, position) => ({ model_profile_id, position, enabled: true })) };
}

function limitPayload(form: LimitForm): RateLimitInput { return { scope_type: form.scopeType, scope_key: form.scopeType === "global" ? "*" : form.scopeKey, max_concurrency: nullableNumber(form.concurrency), requests_per_minute: nullableNumber(form.rpm), tokens_per_minute: nullableNumber(form.tpm), queue_timeout_seconds: Number(form.queueTimeout), enabled: form.enabled }; }
function budgetPayload(form: BudgetForm): BudgetInput { return { scope_type: form.scopeType, scope_key: form.scopeType === "per_request" ? "*" : form.scopeKey, max_cost: nullableNumber(form.maxCost), max_tokens: nullableNumber(form.maxTokens), currency: form.currency, enabled: form.enabled }; }
function nullableNumber(value: string): number | null { return value.trim() === "" ? null : Number(value); }
function numberText(value: number | null): string { return value === null ? "" : String(value); }
function formatDate(value: string | null): string { return value ? new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" }).format(new Date(value)) : "—"; }
function moneyOrUnknown(value: number | null, currency: string): string { return value === null ? "未知" : `${currency} ${value.toLocaleString()}`; }
function probeLevelLabel(value: CapabilityProbe["level"]): string { return value === "basic" ? "基础" : value === "standard" ? "标准" : "高级"; }
function routeStrategyLabel(value: RouteStrategy): string { return ROUTE_STRATEGIES.find(([key]) => key === value)?.[1] ?? value; }
function capabilityLabel(value: string): string { return ({ basic_text: "文本生成", system_prompt: "System Prompt", streaming: "流式输出", json_object: "JSON Object", json_schema: "严格 JSON Schema", tool_calling: "工具调用", temperature: "Temperature", top_p: "Top P", max_output_tokens: "最大输出" } as Record<string, string>)[value] ?? value; }
function sourceLabel(value: string): string { return ({ manual_override: "手动覆盖", automatic_probe: "自动探测", official_metadata: "官方元数据", model_list_api: "模型列表", imported_manifest: "Manifest", provider_default: "Provider 默认" } as Record<string, string>)[value] ?? value; }
function limitScopeLabel(value: LimitScope): string { return ({ global: "全局", project: "项目", provider: "Provider", model: "模型", route: "Route", workflow: "Workflow" } as Record<LimitScope, string>)[value]; }
function budgetScopeLabel(value: BudgetScope): string { return ({ per_request: "单次请求", project_daily: "项目每日", route_per_run: "Route 单次运行" } as Record<BudgetScope, string>)[value]; }
function circuitLabel(value: "closed" | "open" | "half_open"): string { return value === "closed" ? "Closed" : value === "open" ? "Open" : "Half-open"; }
function tokenSourceLabel(value: string): string { return ({ provider_actual: "供应商实际", provider_estimate: "供应商估算", official_tokenizer: "官方 tokenizer", compatible_tokenizer: "兼容 tokenizer", local_approximation: "本地近似" } as Record<string, string>)[value] ?? value; }
function defaultScopeKey(scope: LimitScope, values: { providers: Provider[]; models: ModelProfile[]; projects: Project[]; routes: ModelRoute[] }): string { if (scope === "global") return "*"; if (scope === "workflow") return ""; if (scope === "provider") return values.providers[0]?.id.toString() ?? ""; if (scope === "model") return values.models[0]?.id.toString() ?? ""; if (scope === "project") return values.projects[0]?.id.toString() ?? ""; return values.routes[0]?.id.toString() ?? ""; }
function scopeKeyLabel(scope: LimitScope, key: string, values: { providers: Provider[]; models: ModelProfile[]; projects: Project[]; routes: ModelRoute[] }): string { if (scope === "global") return "*"; if (scope === "workflow") return key; if (scope === "provider") return values.providers.find((item) => String(item.id) === key)?.name ?? `#${key}`; if (scope === "model") return values.models.find((item) => String(item.id) === key)?.display_name ?? `#${key}`; if (scope === "project") return values.projects.find((item) => String(item.id) === key)?.title ?? `#${key}`; return values.routes.find((item) => String(item.id) === key)?.name ?? `#${key}`; }
function budgetScopeKeyLabel(item: BudgetPolicy, projects: Project[], routes: ModelRoute[]): string { if (item.scope_type === "per_request") return "*"; if (item.scope_type === "project_daily") return projects.find((project) => String(project.id) === item.scope_key)?.title ?? `项目 #${item.scope_key}`; return routes.find((route) => String(route.id) === item.scope_key)?.name ?? `Route #${item.scope_key}`; }
