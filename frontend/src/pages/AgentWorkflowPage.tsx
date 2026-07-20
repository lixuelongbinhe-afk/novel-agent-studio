import { ChangeEvent, FormEvent, useEffect, useMemo, useRef, useState } from "react";
import {
  type InfiniteData,
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient
} from "@tanstack/react-query";
import {
  Activity,
  Ban,
  Bot,
  Braces,
  CheckCircle2,
  CircleAlert,
  Clock3,
  Coins,
  Copy,
  Download,
  FileJson,
  GitFork,
  Hash,
  ListTree,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Square,
  Trash2,
  Upload,
  Workflow as WorkflowIcon
} from "lucide-react";
import {
  ApiError,
  api,
  type AgentDefinition,
  type AgentDefinitionInput,
  type ModelProfile,
  type ModelRoute,
  type NodeRun,
  type NodeRunStatus,
  type Workflow,
  type WorkflowEdge,
  type WorkflowManifest,
  type WorkflowNode,
  type WorkflowRun,
  type WorkflowRunEvent,
  type WorkflowRunSummary,
  type WorkflowRunStatus,
  type WorkflowSummary,
  type WorkflowValidation
} from "../api/client";
import { Dialog } from "../components/Dialog";
import { EmptyState } from "../components/EmptyState";
import { ErrorNotice } from "../components/ErrorNotice";
import { FormField } from "../components/FormField";
import { useUiStore } from "../stores/ui";
import { WorkflowCanvas } from "./WorkflowCanvas";

type PageTab = "agents" | "workflows" | "runs";
type TargetMode = "model" | "route";
type AgentForm = {
  name: string;
  agentType: string;
  systemPrompt: string;
  promptTemplate: string;
  inputSchema: string;
  outputSchema: string;
  outputMode: "text" | "json";
  targetMode: TargetMode;
  targetId: string;
  temperature: string;
  topP: string;
  maxTokens: string;
  scenario: "normal" | "delay" | "timeout" | "rate_limit" | "error";
  capabilities: string;
  allowDegradation: boolean;
  timeoutSeconds: string;
  retryCount: string;
  budgetTokens: string;
  budgetCost: string;
  currency: string;
  enabled: boolean;
};
type WorkflowDraft = Pick<Workflow, "id" | "project_id" | "name" | "description" | "enabled" | "nodes" | "edges" | "revision">;
const RUN_HISTORY_PAGE_SIZE = 50;

export function AgentWorkflowPage() {
  const queryClient = useQueryClient();
  const selectedProjectId = useUiStore((state) => state.selectedProjectId);
  const [activeTab, setActiveTab] = useState<PageTab>("agents");
  const [pageError, setPageError] = useState("");

  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: () => api.listProjects() });
  const projectId = selectedProjectId ?? projectsQuery.data?.[0]?.id;
  const agentsQuery = useQuery({
    queryKey: ["agents", projectId],
    queryFn: () => api.listAgents(projectId!),
    enabled: Boolean(projectId)
  });
  const modelsQuery = useQuery({ queryKey: ["models"], queryFn: api.listModels });
  const routesQuery = useQuery({
    queryKey: ["routes", projectId],
    queryFn: () => api.listRoutes(projectId),
    enabled: Boolean(projectId)
  });
  const workflowsQuery = useQuery({
    queryKey: ["workflows", projectId],
    queryFn: () => api.listWorkflows(projectId!),
    enabled: Boolean(projectId)
  });
  const runsQuery = useInfiniteQuery({
    queryKey: ["workflow-runs", projectId],
    queryFn: ({ pageParam }) => api.listWorkflowRuns(
      projectId!,
      undefined,
      RUN_HISTORY_PAGE_SIZE,
      pageParam
    ),
    initialPageParam: undefined as number | undefined,
    getNextPageParam: (lastPage) => lastPage.length === RUN_HISTORY_PAGE_SIZE
      ? lastPage[lastPage.length - 1]?.id
      : undefined,
    enabled: Boolean(projectId),
    refetchInterval: activeTab === "runs" ? 1_500 : false
  });

  const agents = agentsQuery.data ?? [];
  const models = modelsQuery.data ?? [];
  const routes = routesQuery.data ?? [];
  const workflows = workflowsQuery.data ?? [];
  const runs = useMemo(
    () => runsQuery.data?.pages.flatMap((page) => page) ?? [],
    [runsQuery.data]
  );

  if (!projectId && !projectsQuery.isLoading) {
    return (
      <section className="page-stack agent-workflow-page">
        <EmptyState icon={WorkflowIcon} title="还没有项目" description="先在项目首页创建小说项目，再配置 Agent 和工作流。" />
      </section>
    );
  }

  return (
    <section className="page-stack agent-workflow-page">
      <header className="page-header">
        <div>
          <span className="eyebrow">Agent orchestration</span>
          <h1>智能体工作流</h1>
          <p>配置可复用 Agent，在 DAG 画布中编排并查看每一步的真实运行记录。</p>
        </div>
        <div className="workflow-head-stats" aria-label="工作流概览">
          <span><Bot size={15} /><strong>{agents.length}</strong> Agent</span>
          <span><WorkflowIcon size={15} /><strong>{workflows.length}</strong> 工作流</span>
          <span><Activity size={15} /><strong>{runs.filter((run) => isActiveStatus(run.status)).length}</strong> 运行中</span>
        </div>
      </header>

      <nav className="segmented-tabs" aria-label="智能体工作流视图">
        <button className={activeTab === "agents" ? "active" : ""} type="button" onClick={() => setActiveTab("agents")}>
          <Bot size={16} />Agent
        </button>
        <button className={activeTab === "workflows" ? "active" : ""} type="button" onClick={() => setActiveTab("workflows")}>
          <WorkflowIcon size={16} />工作流
        </button>
        <button className={activeTab === "runs" ? "active" : ""} type="button" onClick={() => setActiveTab("runs")}>
          <Activity size={16} />运行记录
        </button>
      </nav>

      {pageError ? <ErrorNotice message={pageError} onDismiss={() => setPageError("")} /> : null}
      {activeTab === "agents" ? (
        <AgentView
          projectId={projectId!}
          agents={agents}
          models={models}
          routes={routes}
          loading={agentsQuery.isLoading || modelsQuery.isLoading || routesQuery.isLoading}
          onError={setPageError}
        />
      ) : activeTab === "workflows" ? (
        <WorkflowView
          projectId={projectId!}
          agents={agents}
          workflows={workflows}
          loading={workflowsQuery.isLoading}
          onRunStarted={(run) => {
            queryClient.setQueryData(["workflow-run", run.id], run);
            void queryClient.invalidateQueries({ queryKey: ["workflow-runs", projectId] });
            setActiveTab("runs");
          }}
          onError={setPageError}
        />
      ) : (
        <RunView
          projectId={projectId!}
          agents={agents}
          workflows={workflows}
          runs={runs}
          loading={runsQuery.isLoading}
          hasMore={Boolean(runsQuery.hasNextPage)}
          loadingMore={runsQuery.isFetchingNextPage}
          onLoadMore={() => void runsQuery.fetchNextPage()}
          onError={setPageError}
        />
      )}
    </section>
  );
}

function AgentView({
  projectId,
  agents,
  models,
  routes,
  loading,
  onError
}: {
  projectId: number;
  agents: AgentDefinition[];
  models: ModelProfile[];
  routes: ModelRoute[];
  loading: boolean;
  onError: (message: string) => void;
}) {
  const queryClient = useQueryClient();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<AgentDefinition | null>(null);
  const [form, setForm] = useState<AgentForm>(() => emptyAgentForm(models, routes));
  const [formError, setFormError] = useState("");

  const saveAgent = useMutation({
    mutationFn: () => {
      const payload = agentPayload(form, projectId);
      return editing ? api.updateAgent(editing, payload) : api.createAgent(payload);
    },
    onSuccess: async () => {
      setDialogOpen(false);
      setEditing(null);
      setFormError("");
      await queryClient.invalidateQueries({ queryKey: ["agents", projectId] });
    },
    onError: (error) => setFormError(errorMessage(error, "Agent 保存失败"))
  });
  const deleteAgent = useMutation({
    mutationFn: api.deleteAgent,
    onSuccess: async () => queryClient.invalidateQueries({ queryKey: ["agents", projectId] }),
    onError: (error) => onError(errorMessage(error, "Agent 删除失败"))
  });

  function openCreate() {
    setEditing(null);
    setForm(emptyAgentForm(models, routes));
    setFormError("");
    saveAgent.reset();
    setDialogOpen(true);
  }

  function openEdit(agent: AgentDefinition) {
    setEditing(agent);
    setForm(agentForm(agent));
    setFormError("");
    saveAgent.reset();
    setDialogOpen(true);
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    setFormError("");
    saveAgent.mutate();
  }

  const targetAvailable = form.targetMode === "model" ? models.length > 0 : routes.length > 0;
  return (
    <div className="agent-management">
      <div className="section-commandbar">
        <div><strong>Agent 定义</strong><span>版本化提示词、模型目标、重试与单次预算</span></div>
        <button className="primary-button" type="button" onClick={openCreate} disabled={!models.length && !routes.length}>
          <Plus size={17} />新建 Agent
        </button>
      </div>
      {!models.length && !routes.length ? <ErrorNotice message="请先在模型中心添加模型，或创建至少一条可用 Route。" /> : null}
      {loading ? <div className="route-loading">正在读取 Agent 配置...</div> : agents.length ? (
        <div className="agent-table" role="table" aria-label="Agent 列表">
          <div className="agent-table-head" role="row">
            <span>名称</span><span>类型</span><span>目标</span><span>输出</span><span>限制</span><span>状态</span><span>操作</span>
          </div>
          {agents.map((agent) => {
            const target = agent.model_profile_id
              ? models.find((model) => model.id === agent.model_profile_id)?.display_name ?? `模型 #${agent.model_profile_id}`
              : routes.find((route) => route.id === agent.route_id)?.name ?? `Route #${agent.route_id}`;
            return (
              <div className="agent-table-row" role="row" key={agent.id}>
                <div><Bot size={16} /><span><strong>{agent.name}</strong><small>v{agent.version} · rev {agent.revision}</small></span></div>
                <span>{agent.agent_type}</span>
                <span title={target}>{target}</span>
                <span>{agent.output_mode === "json" ? "JSON" : "文本"}</span>
                <span>{agent.parameters.max_tokens.toLocaleString()} tokens · {agent.retry_count} 次重试</span>
                <span className={`status-chip ${agent.enabled ? "enabled" : "disabled"}`}>{agent.enabled ? "启用" : "停用"}</span>
                <div className="row-actions">
                  <button className="icon-button ghost" type="button" title="编辑 Agent" onClick={() => openEdit(agent)}><Pencil size={15} /></button>
                  <button
                    className="icon-button ghost danger-ink"
                    type="button"
                    title="删除 Agent"
                    disabled={deleteAgent.isPending}
                    onClick={() => { if (window.confirm(`删除 Agent“${agent.name}”？`)) deleteAgent.mutate(agent); }}
                  ><Trash2 size={15} /></button>
                </div>
              </div>
            );
          })}
        </div>
      ) : (
        <EmptyState icon={Bot} title="还没有 Agent" description="创建一个 Agent，选择固定模型或 Route，并配置真实提示词与运行限制。" />
      )}

      <Dialog
        open={dialogOpen}
        title={editing ? `编辑 ${editing.name}` : "新建 Agent"}
        description={editing ? `当前版本 v${editing.version}；关键配置改变后自动升版。` : "Agent 必须且只能绑定固定模型或 Route。"}
        width="large"
        onClose={() => setDialogOpen(false)}
        footer={<><button className="secondary-button" type="button" onClick={() => setDialogOpen(false)}>取消</button><button className="primary-button" type="submit" form="agent-form" disabled={saveAgent.isPending || !targetAvailable || !form.targetId || !form.name.trim() || !form.promptTemplate.trim()}><Save size={17} />保存 Agent</button></>}
      >
        <form id="agent-form" className="agent-form" onSubmit={submit}>
          {formError ? <ErrorNotice message={formError} onDismiss={() => setFormError("")} /> : null}
          <div className="form-row">
            <FormField label="名称"><input autoFocus maxLength={160} value={form.name} onChange={(event) => setForm({ ...form, name: event.target.value })} placeholder="例如：章节初稿" /></FormField>
            <FormField label="类型" hint="小写字母开头，可含数字、下划线和连字符"><input list="agent-types" value={form.agentType} onChange={(event) => setForm({ ...form, agentType: event.target.value.toLowerCase() })} /><datalist id="agent-types">{["goal_analysis", "character", "worldbuilding", "foreshadow", "pacing", "scene_plan", "draft", "continuity", "dialogue", "style", "editor", "custom"].map((value) => <option value={value} key={value} />)}</datalist></FormField>
          </div>
          <FormField label="系统提示词" hint="支持安全模板变量，如 {input.task}、{nodes.draft_1}"><textarea rows={4} value={form.systemPrompt} onChange={(event) => setForm({ ...form, systemPrompt: event.target.value })} placeholder="你是一名严谨的中文小说创作助手。" /></FormField>
          <FormField label="任务提示词模板"><textarea rows={7} required value={form.promptTemplate} onChange={(event) => setForm({ ...form, promptTemplate: event.target.value })} placeholder="请完成任务：{input.task}" /></FormField>

          <div className="agent-target-panel">
            <div className="segmented-control compact-control" aria-label="目标类型">
              <button className={form.targetMode === "model" ? "active" : ""} type="button" onClick={() => setForm({ ...form, targetMode: "model", targetId: models[0] ? String(models[0].id) : "" })}>固定模型</button>
              <button className={form.targetMode === "route" ? "active" : ""} type="button" onClick={() => setForm({ ...form, targetMode: "route", targetId: routes[0] ? String(routes[0].id) : "" })}>Route</button>
            </div>
            <FormField label={form.targetMode === "model" ? "模型" : "Route"}>
              <select value={form.targetId} onChange={(event) => setForm({ ...form, targetId: event.target.value })}>
                <option value="" disabled>请选择{form.targetMode === "model" ? "模型" : " Route"}</option>
                {(form.targetMode === "model" ? models : routes).map((item) => <option key={item.id} value={item.id}>{"display_name" in item ? item.display_name : item.name}{!item.enabled ? "（已停用）" : ""}</option>)}
              </select>
            </FormField>
          </div>

          <details className="agent-form-section" open>
            <summary><Braces size={16} />输入与输出契约</summary>
            <div className="form-grid">
              <div className="form-row">
                <FormField label="输出模式"><select value={form.outputMode} onChange={(event) => setForm({ ...form, outputMode: event.target.value as "text" | "json" })}><option value="text">文本</option><option value="json">JSON</option></select></FormField>
                <FormField label="必需能力" hint="逗号分隔，例如 vision,json_mode"><input value={form.capabilities} onChange={(event) => setForm({ ...form, capabilities: event.target.value })} /></FormField>
              </div>
              <div className="form-row">
                <FormField label="输入 JSON Schema"><textarea rows={9} spellCheck={false} value={form.inputSchema} onChange={(event) => setForm({ ...form, inputSchema: event.target.value })} /></FormField>
                <FormField label="输出 JSON Schema"><textarea rows={9} spellCheck={false} value={form.outputSchema} onChange={(event) => setForm({ ...form, outputSchema: event.target.value })} /></FormField>
              </div>
            </div>
          </details>

          <details className="agent-form-section" open>
            <summary><Hash size={16} />采样、超时与重试</summary>
            <div className="agent-number-grid">
              <FormField label="Temperature"><input type="number" min="0" max="2" step="0.1" value={form.temperature} onChange={(event) => setForm({ ...form, temperature: event.target.value })} /></FormField>
              <FormField label="Top P" hint="留空使用 Provider 默认"><input type="number" min="0.01" max="1" step="0.01" value={form.topP} onChange={(event) => setForm({ ...form, topP: event.target.value })} /></FormField>
              <FormField label="最大输出 tokens"><input type="number" min="1" step="1" value={form.maxTokens} onChange={(event) => setForm({ ...form, maxTokens: event.target.value })} /></FormField>
              <FormField label="超时（秒）"><input type="number" min="1" max="3600" step="1" value={form.timeoutSeconds} onChange={(event) => setForm({ ...form, timeoutSeconds: event.target.value })} /></FormField>
              <FormField label="重试次数"><input type="number" min="0" max="5" step="1" value={form.retryCount} onChange={(event) => setForm({ ...form, retryCount: event.target.value })} /></FormField>
              <FormField label="Mock 场景"><select value={form.scenario} onChange={(event) => setForm({ ...form, scenario: event.target.value as AgentForm["scenario"] })}><option value="normal">正常</option><option value="delay">延迟</option><option value="timeout">超时</option><option value="rate_limit">限流</option><option value="error">错误</option></select></FormField>
            </div>
          </details>

          <details className="agent-form-section">
            <summary><Coins size={16} />Agent 单次预算</summary>
            <div className="agent-number-grid">
              <FormField label="最大 tokens" hint="留空不限"><input type="number" min="1" value={form.budgetTokens} onChange={(event) => setForm({ ...form, budgetTokens: event.target.value })} /></FormField>
              <FormField label="最大成本" hint="留空不限"><input type="number" min="0" step="0.0001" value={form.budgetCost} onChange={(event) => setForm({ ...form, budgetCost: event.target.value })} /></FormField>
              <FormField label="币种"><input maxLength={12} value={form.currency} onChange={(event) => setForm({ ...form, currency: event.target.value.toUpperCase() })} /></FormField>
            </div>
          </details>
          <div className="agent-toggle-row">
            <label className="checkbox-row"><input type="checkbox" checked={form.allowDegradation} onChange={(event) => setForm({ ...form, allowDegradation: event.target.checked })} /><span>能力不足时允许降级</span></label>
            <label className="checkbox-row"><input type="checkbox" checked={form.enabled} onChange={(event) => setForm({ ...form, enabled: event.target.checked })} /><span>启用 Agent</span></label>
          </div>
        </form>
      </Dialog>
    </div>
  );
}

function WorkflowView({
  projectId,
  agents,
  workflows,
  loading,
  onRunStarted,
  onError
}: {
  projectId: number;
  agents: AgentDefinition[];
  workflows: WorkflowSummary[];
  loading: boolean;
  onRunStarted: (run: WorkflowRun) => void;
  onError: (message: string) => void;
}) {
  const queryClient = useQueryClient();
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [draft, setDraft] = useState<WorkflowDraft | null>(null);
  const [dirty, setDirty] = useState(false);
  const [canvasEpoch, setCanvasEpoch] = useState(0);
  const [validation, setValidation] = useState<WorkflowValidation | null>(null);
  const [action, setAction] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [runOpen, setRunOpen] = useState(false);
  const [runInput, setRunInput] = useState('{\n  "task": "根据输入完成本次小说创作任务",\n  "topic": "雾港回声"\n}');
  const [dialogError, setDialogError] = useState("");
  const importRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!workflows.length) {
      setSelectedId(null);
      setDraft(null);
      return;
    }
    if (!selectedId || !workflows.some((workflow) => workflow.id === selectedId)) setSelectedId(workflows[0].id);
  }, [selectedId, workflows]);

  const workflowQuery = useQuery({
    queryKey: ["workflow", selectedId],
    queryFn: () => api.readWorkflow(selectedId!),
    enabled: Boolean(selectedId)
  });

  useEffect(() => {
    const workflow = workflowQuery.data;
    if (!workflow) return;
    if (draft?.id === workflow.id && dirty) return;
    setDraft(workflow);
    setDirty(false);
    setValidation(null);
    setCanvasEpoch((value) => value + 1);
  }, [workflowQuery.data?.id, workflowQuery.data?.revision]);

  const createWorkflow = useMutation({
    mutationFn: () => api.createWorkflow({
      project_id: projectId,
      name: newName.trim(),
      description: newDescription.trim(),
      enabled: true,
      ...initialWorkflowGraph(agents.find((agent) => agent.enabled) ?? agents[0])
    }),
    onSuccess: async (workflow) => {
      setCreateOpen(false);
      setNewName("");
      setNewDescription("");
      setSelectedId(workflow.id);
      setDraft(workflow);
      setDirty(false);
      setCanvasEpoch((value) => value + 1);
      await queryClient.invalidateQueries({ queryKey: ["workflows", projectId] });
    },
    onError: (error) => setDialogError(errorMessage(error, "工作流创建失败"))
  });
  const deleteWorkflow = useMutation({
    mutationFn: api.deleteWorkflow,
    onSuccess: async () => {
      setDraft(null);
      setSelectedId(null);
      await queryClient.invalidateQueries({ queryKey: ["workflows", projectId] });
    },
    onError: (error) => onError(errorMessage(error, "工作流删除失败"))
  });
  const startRun = useMutation({
    mutationFn: async () => {
      if (!draft) throw new Error("未选择工作流");
      const parsed = parseObject(runInput, "运行输入");
      return api.startWorkflowRun(draft.id, parsed);
    },
    onSuccess: (run) => {
      setRunOpen(false);
      setDialogError("");
      onRunStarted(run);
    },
    onError: (error) => setDialogError(errorMessage(error, "工作流启动失败"))
  });

  function updateDraft(patch: Partial<WorkflowDraft>) {
    if (!draft) return;
    setDraft({ ...draft, ...patch });
    setDirty(true);
    setValidation(null);
  }

  async function persistDraft(): Promise<Workflow> {
    if (!draft) throw new Error("未选择工作流");
    if (!dirty) return workflowQuery.data ?? api.readWorkflow(draft.id);
    const saved = await api.updateWorkflow(draft as Workflow, {
      project_id: projectId,
      name: draft.name.trim(),
      description: draft.description.trim(),
      enabled: draft.enabled,
      nodes: draft.nodes,
      edges: draft.edges
    });
    setDraft(saved);
    setDirty(false);
    setCanvasEpoch((value) => value + 1);
    queryClient.setQueryData(["workflow", saved.id], saved);
    await queryClient.invalidateQueries({ queryKey: ["workflows", projectId] });
    return saved;
  }

  async function save() {
    setAction("save");
    try {
      await persistDraft();
      setValidation(null);
    } catch (error) {
      onError(errorMessage(error, "工作流保存失败"));
    } finally {
      setAction("");
    }
  }

  async function validate(openRunAfter = false) {
    setAction(openRunAfter ? "prepare-run" : "validate");
    try {
      const saved = await persistDraft();
      const result = await api.validateWorkflow(saved.id);
      setValidation(result);
      if (result.valid && openRunAfter) {
        setDialogError("");
        setRunOpen(true);
      }
    } catch (error) {
      onError(errorMessage(error, "工作流校验失败"));
    } finally {
      setAction("");
    }
  }

  async function exportManifest() {
    if (!draft) return;
    setAction("export");
    try {
      const saved = await persistDraft();
      const manifest = await api.exportWorkflow(saved.id);
      downloadJson(`${safeFilename(saved.name)}.nas-workflow.json`, manifest);
    } catch (error) {
      onError(errorMessage(error, "工作流导出失败"));
    } finally {
      setAction("");
    }
  }

  async function importManifest(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    event.target.value = "";
    if (!file) return;
    setAction("import");
    try {
      const parsed = JSON.parse(await file.text()) as WorkflowManifest;
      const imported = await api.importWorkflow(projectId, parsed);
      setSelectedId(imported.id);
      setDraft(imported);
      setDirty(false);
      setCanvasEpoch((value) => value + 1);
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ["workflows", projectId] }),
        queryClient.invalidateQueries({ queryKey: ["agents", projectId] })
      ]);
    } catch (error) {
      onError(errorMessage(error, "工作流清单导入失败"));
    } finally {
      setAction("");
    }
  }

  return (
    <div className="workflow-editor-layout">
      <aside className="workflow-browser">
        <header><div><strong>工作流</strong><span>{workflows.length} 个定义</span></div><button className="icon-button ghost" type="button" title="新建工作流" onClick={() => { setDialogError(""); setCreateOpen(true); }}><Plus size={17} /></button></header>
        <input ref={importRef} className="visually-hidden" type="file" accept="application/json,.json" onChange={importManifest} />
        {loading ? <div className="browser-loading">正在读取...</div> : workflows.length ? workflows.map((workflow) => (
          <button className={selectedId === workflow.id ? "selected" : ""} type="button" key={workflow.id} onClick={() => { setSelectedId(workflow.id); setDirty(false); }}>
            <WorkflowIcon size={16} /><span><strong>{workflow.name}</strong><small>{workflow.node_count} 节点 · rev {workflow.revision}</small></span><i className={workflow.enabled ? "enabled" : "disabled"} title={workflow.enabled ? "已启用" : "已停用"} />
          </button>
        )) : <EmptyState icon={WorkflowIcon} title="还没有工作流" description="创建后即可在画布中编排节点。" />}
        <footer><button className="secondary-button compact" type="button" disabled={action === "import"} onClick={() => importRef.current?.click()}><Upload size={15} />导入清单</button></footer>
      </aside>

      <section className="workflow-workbench">
        {draft ? (
          <>
            <header className="workflow-editor-toolbar">
              <div className="workflow-name-fields">
                <input aria-label="工作流名称" maxLength={180} value={draft.name} onChange={(event) => updateDraft({ name: event.target.value })} />
                <input aria-label="工作流描述" maxLength={20000} value={draft.description} onChange={(event) => updateDraft({ description: event.target.value })} placeholder="工作流说明" />
              </div>
              <div className="workflow-toolbar-actions">
                <label className="checkbox-row workflow-enabled"><input type="checkbox" checked={draft.enabled} onChange={(event) => updateDraft({ enabled: event.target.checked })} /><span>启用</span></label>
                <button className="icon-button ghost" type="button" title="导出清单" disabled={Boolean(action)} onClick={exportManifest}><Download size={16} /></button>
                <button className="icon-button ghost danger-ink" type="button" title="删除工作流" disabled={deleteWorkflow.isPending} onClick={() => { if (window.confirm(`删除工作流“${draft.name}”？`)) deleteWorkflow.mutate(draft as Workflow); }}><Trash2 size={16} /></button>
                <button className="secondary-button" type="button" disabled={Boolean(action)} onClick={() => validate(false)}><CheckCircle2 size={16} />校验</button>
                <button className="secondary-button" type="button" disabled={!dirty || Boolean(action) || !draft.name.trim()} onClick={save}><Save size={16} />{dirty ? "保存" : "已保存"}</button>
                <button className="primary-button" type="button" disabled={Boolean(action) || !draft.enabled || !draft.name.trim()} onClick={() => validate(true)}><Play size={16} />运行</button>
              </div>
            </header>
            {validation ? <ValidationStrip validation={validation} /> : null}
            <WorkflowCanvas
              workflowKey={`${draft.id}:${draft.revision}:${canvasEpoch}`}
              value={{ nodes: draft.nodes, edges: draft.edges }}
              agents={agents}
              onChange={(graph) => updateDraft(graph)}
            />
          </>
        ) : <EmptyState icon={ListTree} title="选择或创建工作流" description="左侧选择定义，画布会显示可编辑的真实 DAG。" action={<button className="primary-button" type="button" onClick={() => setCreateOpen(true)}><Plus size={17} />新建工作流</button>} />}
      </section>

      <Dialog open={createOpen} title="新建工作流" description={agents.length ? "将自动创建 Start → Agent → Output，可直接校验运行。" : "将创建 Start → Output；添加 Agent 后可继续编排。"} width="small" onClose={() => setCreateOpen(false)} footer={<><button className="secondary-button" type="button" onClick={() => setCreateOpen(false)}>取消</button><button className="primary-button" type="submit" form="create-workflow-form" disabled={!newName.trim() || createWorkflow.isPending}><Plus size={16} />创建</button></>}>
        <form id="create-workflow-form" className="form-grid" onSubmit={(event) => { event.preventDefault(); setDialogError(""); createWorkflow.mutate(); }}>
          {dialogError ? <ErrorNotice message={dialogError} /> : null}
          <FormField label="名称"><input autoFocus value={newName} maxLength={180} onChange={(event) => setNewName(event.target.value)} placeholder="例如：章节生成流水线" /></FormField>
          <FormField label="说明"><textarea rows={4} value={newDescription} onChange={(event) => setNewDescription(event.target.value)} /></FormField>
        </form>
      </Dialog>

      <Dialog open={runOpen} title={`运行 ${draft?.name ?? "工作流"}`} description="输入会与本次快照一起持久化；运行后修改定义不会改变本次结果。" width="medium" onClose={() => setRunOpen(false)} footer={<><button className="secondary-button" type="button" onClick={() => setRunOpen(false)}>取消</button><button className="primary-button" type="submit" form="run-workflow-form" disabled={startRun.isPending}><Play size={16} />开始运行</button></>}>
        <form id="run-workflow-form" className="form-grid" onSubmit={(event) => { event.preventDefault(); setDialogError(""); startRun.mutate(); }}>
          {dialogError ? <ErrorNotice message={dialogError} /> : null}
          <FormField label="运行输入 JSON"><textarea rows={14} spellCheck={false} value={runInput} onChange={(event) => setRunInput(event.target.value)} /></FormField>
        </form>
      </Dialog>
    </div>
  );
}

function RunView({
  projectId,
  agents,
  workflows,
  runs,
  loading,
  hasMore,
  loadingMore,
  onLoadMore,
  onError
}: {
  projectId: number;
  agents: AgentDefinition[];
  workflows: WorkflowSummary[];
  runs: WorkflowRunSummary[];
  loading: boolean;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
  onError: (message: string) => void;
}) {
  const queryClient = useQueryClient();
  const [selectedRunId, setSelectedRunId] = useState<number | null>(null);
  const [selectedNodeKey, setSelectedNodeKey] = useState<string | null>(null);
  const [liveEvents, setLiveEvents] = useState<WorkflowRunEvent[]>([]);
  const lastEventId = useRef(0);

  useEffect(() => {
    if (!runs.length) {
      setSelectedRunId(null);
      return;
    }
    if (!selectedRunId || !runs.some((run) => run.id === selectedRunId)) setSelectedRunId(runs[0].id);
  }, [runs, selectedRunId]);

  const runQuery = useQuery({
    queryKey: ["workflow-run", selectedRunId],
    queryFn: () => api.readWorkflowRun(selectedRunId!),
    enabled: Boolean(selectedRunId),
    refetchInterval: (query) => isActiveStatus((query.state.data as WorkflowRun | undefined)?.status) ? 700 : false
  });
  const snapshotQuery = useQuery({
    queryKey: ["workflow-run-snapshot", selectedRunId],
    queryFn: () => api.readWorkflowSnapshot(selectedRunId!),
    enabled: Boolean(selectedRunId),
    staleTime: Infinity
  });
  const run = runQuery.data;
  const snapshot = snapshotQuery.data;

  useEffect(() => {
    if (!run) return;
    queryClient.setQueryData<InfiniteData<WorkflowRunSummary[], number | undefined>>(
      ["workflow-runs", projectId],
      (current) => current ? {
        ...current,
        pages: current.pages.map((page) => page.map((item) => item.id === run.id ? {
          ...item,
          status: run.status,
          event_sequence: run.event_sequence,
          started_at: run.started_at,
          completed_at: run.completed_at
        } : item))
      } : current
    );
  }, [run?.id, run?.status, run?.event_sequence, run?.started_at, run?.completed_at, projectId, queryClient]);

  useEffect(() => {
    setLiveEvents([]);
    setSelectedNodeKey(null);
    lastEventId.current = 0;
  }, [selectedRunId]);

  useEffect(() => {
    const max = Math.max(0, ...(snapshot?.events.map((event) => event.sequence) ?? []));
    lastEventId.current = Math.max(lastEventId.current, max);
  }, [snapshot?.events.length]);

  useEffect(() => {
    if (!selectedRunId || !isActiveStatus(run?.status)) return;
    const controller = new AbortController();
    let stopped = false;
    let frameId: number | null = null;
    let bufferedEvents: WorkflowRunEvent[] = [];
    const enqueueEvents = (values: WorkflowRunEvent[]) => {
      if (!values.length) return;
      bufferedEvents.push(...values);
      lastEventId.current = Math.max(
        lastEventId.current,
        ...values.map((event) => event.sequence)
      );
      if (frameId !== null) return;
      frameId = window.requestAnimationFrame(() => {
        frameId = null;
        const next = bufferedEvents;
        bufferedEvents = [];
        setLiveEvents((current) => mergeEvents(current, next));
      });
    };
    const listen = async () => {
      while (!stopped && !controller.signal.aborted) {
        try {
          await api.streamWorkflowEvents(selectedRunId, (message) => {
            if ("events" in message.data && "run" in message.data) {
              const values = message.data.events;
              enqueueEvents(values);
            } else if ("sequence" in message.data) {
              const event = message.data;
              enqueueEvents([event]);
              if (event.event !== "node_output_delta") {
                void queryClient.invalidateQueries({ queryKey: ["workflow-run", selectedRunId] });
              }
            }
          }, { signal: controller.signal, lastEventId: lastEventId.current });
          await Promise.all([
            queryClient.invalidateQueries({ queryKey: ["workflow-run", selectedRunId] }),
            queryClient.invalidateQueries({ queryKey: ["workflow-runs", projectId] })
          ]);
          break;
        } catch (error) {
          if (controller.signal.aborted || stopped) break;
          await delay(500);
        }
      }
    };
    void listen();
    return () => {
      stopped = true;
      controller.abort();
      if (frameId !== null) window.cancelAnimationFrame(frameId);
    };
  }, [selectedRunId, run?.status, projectId, queryClient]);

  useEffect(() => {
    if (!run?.nodes.length) return;
    if (!selectedNodeKey || !run.nodes.some((node) => node.node_key === selectedNodeKey)) setSelectedNodeKey(run.nodes[0].node_key);
  }, [run?.id, run?.nodes, selectedNodeKey]);

  const cancelRun = useMutation({
    mutationFn: api.cancelWorkflowRun,
    onSuccess: async (value) => {
      queryClient.setQueryData(["workflow-run", value.id], value);
      await queryClient.invalidateQueries({ queryKey: ["workflow-runs", projectId] });
    },
    onError: (error) => onError(errorMessage(error, "取消运行失败"))
  });
  const deriveRun = useMutation({
    mutationFn: ({ mode, nodeKey }: { mode: "retry_node" | "retry_descendants" | "clone_from_node"; nodeKey: string }) => api.deriveWorkflowRun(run!.id, mode, nodeKey),
    onSuccess: async (value) => {
      queryClient.setQueryData(["workflow-run", value.id], value);
      setSelectedRunId(value.id);
      await queryClient.invalidateQueries({ queryKey: ["workflow-runs", projectId] });
    },
    onError: (error) => onError(errorMessage(error, "派生运行失败"))
  });

  const events = useMemo(() => mergeEvents(snapshot?.events ?? [], liveEvents), [snapshot?.events, liveEvents]);
  const selectedNode = run?.nodes.find((node) => node.node_key === selectedNodeKey) ?? null;
  const statuses = useMemo(() => Object.fromEntries((run?.nodes ?? []).map((node) => [node.node_key, node.status])) as Record<string, NodeRunStatus>, [run?.nodes]);
  const frozenWorkflow = workflowFromSnapshot(snapshot?.snapshot);

  return (
    <div className="run-monitor-layout">
      <aside className="run-browser">
        <header><div><strong>运行记录</strong><span>已加载 {runs.length} 次</span></div><button className="icon-button ghost" type="button" title="刷新运行记录" onClick={() => void queryClient.invalidateQueries({ queryKey: ["workflow-runs", projectId] })}><RefreshCw size={16} /></button></header>
        {loading ? <div className="browser-loading">正在读取...</div> : runs.length ? <div className="run-history-list">{runs.map((item) => {
          const workflowName = workflows.find((workflow) => workflow.id === item.workflow_id)?.name ?? `工作流 #${item.workflow_id}`;
          return <button className={selectedRunId === item.id ? "selected" : ""} type="button" key={item.id} onClick={() => setSelectedRunId(item.id)}><RunStatusIcon status={item.status} /><span><strong>{workflowName}</strong><small>#{item.id} · {formatDate(item.created_at)}</small></span><span className={`run-status status-${item.status}`}>{runStatusLabel(item.status)}</span></button>;
        })}</div> : <EmptyState icon={Activity} title="还没有运行记录" description="从工作流页校验并启动一次运行。" />}
        {runs.length && hasMore ? <footer className="run-history-pagination"><button className="secondary-button compact" type="button" disabled={loadingMore} onClick={onLoadMore}>{loadingMore ? "正在加载..." : "加载更早记录"}</button></footer> : null}
      </aside>

      <section className="run-workbench">
        {run ? (
          <>
            <header className="run-header">
              <div><span className={`run-status status-${run.status}`}>{runStatusLabel(run.status)}</span><h2>{workflows.find((workflow) => workflow.id === run.workflow_id)?.name ?? `工作流 #${run.workflow_id}`} · 运行 #{run.id}</h2><p>{run.parent_run_id ? `派生自 #${run.parent_run_id} · ${sourceModeLabel(run.source_mode)}` : "原始运行"} · 快照 rev {run.workflow_revision}</p></div>
              <div className="run-header-actions">
                {isActiveStatus(run.status) ? <button className="danger-button" type="button" disabled={cancelRun.isPending} onClick={() => cancelRun.mutate(run.id)}><Square size={15} />取消运行</button> : null}
              </div>
            </header>
            <div className="run-metric-strip">
              <div><Clock3 size={16} /><span>耗时</span><strong>{durationText(run.started_at, run.completed_at)}</strong></div>
              <div><Hash size={16} /><span>事件</span><strong>{Math.max(run.event_sequence, events[events.length - 1]?.sequence ?? 0)}</strong></div>
              <div><Coins size={16} /><span>已知成本</span><strong>{runCost(run.nodes)}</strong></div>
              <div><Activity size={16} /><span>节点</span><strong>{run.nodes.filter((node) => node.status === "completed").length}/{run.nodes.length}</strong></div>
            </div>

            {frozenWorkflow ? <div className="run-canvas-band"><WorkflowCanvas workflowKey={`run-${run.id}`} value={{ nodes: frozenWorkflow.nodes, edges: frozenWorkflow.edges }} agents={agents} statuses={statuses} readOnly /></div> : snapshotQuery.isLoading ? <div className="route-loading">正在读取不可变运行快照...</div> : null}

            <div className="run-detail-grid">
              <section className="node-run-list">
                <header><strong>节点执行</strong><span>选择节点查看 Attempt、输入和输出</span></header>
                <div>{run.nodes.map((node) => <button className={selectedNodeKey === node.node_key ? "selected" : ""} type="button" key={node.id} onClick={() => setSelectedNodeKey(node.node_key)}><span className={`node-status-mark status-${node.status}`} /><span><strong>{frozenWorkflow?.nodes.find((item) => item.key === node.node_key)?.label ?? node.node_key}</strong><small>{node.node_type} · {node.attempt_count} attempt</small></span><span className={`run-status status-${node.status}`}>{nodeStatusLabel(node.status)}</span></button>)}</div>
              </section>
              <NodeRunDetail node={selectedNode} run={run} deriving={deriveRun.isPending} onDerive={(mode) => selectedNode && deriveRun.mutate({ mode, nodeKey: selectedNode.node_key })} />
            </div>

            <div className="run-output-events">
              <section><header><strong>最终输出</strong><span>{run.status === "completed" ? "Output 节点结果" : "运行尚未完成时保留当前状态"}</span></header><pre>{jsonText(run.output)}</pre>{run.error ? <ErrorNotice message={jsonText(run.error)} /> : null}</section>
              <section><header><strong>事件流</strong><span>SSE 序号单调递增 · 显示最近 250 条</span></header><div className="event-log">{events.slice(-250).map((event) => <div key={event.sequence}><time>{event.sequence}</time><strong>{event.event}</strong><span>{event.node_key ?? "run"}</span><code>{compactJson(event.payload)}</code></div>)}</div></section>
            </div>
          </>
        ) : runQuery.isLoading ? <div className="route-loading">正在读取运行快照...</div> : <EmptyState icon={Activity} title="选择一次运行" description="左侧可查看历史、实时状态和派生关系。" />}
      </section>
    </div>
  );
}

function NodeRunDetail({ node, run, deriving, onDerive }: { node: NodeRun | null; run: WorkflowRun; deriving: boolean; onDerive: (mode: "retry_node" | "retry_descendants" | "clone_from_node") => void }) {
  if (!node) return <section className="node-run-detail"><EmptyState icon={ListTree} title="选择节点" description="查看节点的每次尝试、局部输出和用量。" /></section>;
  const partial = node.attempts[node.attempts.length - 1]?.partial_output ?? "";
  return (
    <section className="node-run-detail">
      <header><div><strong>{node.node_key}</strong><span>{node.node_type} · {nodeStatusLabel(node.status)}</span></div><div className="node-derive-actions"><button className="icon-button ghost" type="button" title="仅重试此节点" disabled={deriving || isActiveStatus(run.status)} onClick={() => onDerive("retry_node")}><RotateCcw size={15} /></button><button className="icon-button ghost" type="button" title="重试此节点及下游" disabled={deriving || isActiveStatus(run.status)} onClick={() => onDerive("retry_descendants")}><GitFork size={15} /></button><button className="icon-button ghost" type="button" title="从此节点克隆新运行" disabled={deriving || isActiveStatus(run.status)} onClick={() => onDerive("clone_from_node")}><Copy size={15} /></button></div></header>
      {node.error ? <ErrorNotice message={jsonText(node.error)} /> : null}
      {node.warnings.length ? <div className="node-warnings">{node.warnings.map((warning) => <span key={warning}><CircleAlert size={14} />{warning}</span>)}</div> : null}
      <div className="node-io-grid"><div><strong>输入</strong><pre>{jsonText(node.input)}</pre></div><div><strong>{partial && node.status === "running" ? "流式局部输出" : "输出"}</strong><pre>{partial && node.status === "running" ? partial : jsonText(node.output)}</pre></div></div>
      <div className="attempt-list"><header><strong>Attempts</strong><span>{node.attempts.length} 次</span></header>{node.attempts.length ? node.attempts.map((attempt) => <div key={attempt.id}><span className={`node-status-mark status-${attempt.status}`} /><strong>#{attempt.attempt_number}</strong><span>{attempt.status}</span><span>{attempt.total_tokens.toLocaleString()} tokens</span><span>{attempt.cost_known ? `${attempt.currency} ${(attempt.cost ?? 0).toFixed(6)}` : "成本未知"}</span><time>{durationText(attempt.started_at, attempt.completed_at)}</time></div>) : <p className="muted">该节点尚未开始。</p>}</div>
    </section>
  );
}

function ValidationStrip({ validation }: { validation: WorkflowValidation }) {
  return (
    <div className={`workflow-validation ${validation.valid ? "valid" : "invalid"}`}>
      <div>{validation.valid ? <CheckCircle2 size={17} /> : <CircleAlert size={17} />}<strong>{validation.valid ? "校验通过" : `发现 ${validation.issues.length} 个问题`}</strong>{validation.plan_hash ? <code>{validation.plan_hash.slice(0, 12)}</code> : null}</div>
      {validation.issues.length ? <ul>{validation.issues.map((issue, index) => <li key={`${issue.code}-${index}`}><span>{issue.severity === "error" ? "错误" : "警告"}</span><strong>{issue.message}</strong>{issue.path.length ? <code>{issue.path.join(" → ")}</code> : null}</li>)}</ul> : <span>拓扑顺序：{validation.topological_order.join(" → ")}</span>}
    </div>
  );
}

function emptyAgentForm(models: ModelProfile[], routes: ModelRoute[]): AgentForm {
  const targetMode: TargetMode = models.length ? "model" : "route";
  return {
    name: "",
    agentType: "custom",
    systemPrompt: "你是一名严谨的中文小说创作助手。",
    promptTemplate: "请完成以下任务：{input.task}",
    inputSchema: "{}",
    outputSchema: "{}",
    outputMode: "text",
    targetMode,
    targetId: String(targetMode === "model" ? models[0]?.id ?? "" : routes[0]?.id ?? ""),
    temperature: "0.7",
    topP: "",
    maxTokens: "1024",
    scenario: "normal",
    capabilities: "",
    allowDegradation: true,
    timeoutSeconds: "120",
    retryCount: "1",
    budgetTokens: "",
    budgetCost: "",
    currency: "USD",
    enabled: true
  };
}

function agentForm(agent: AgentDefinition): AgentForm {
  return {
    name: agent.name,
    agentType: agent.agent_type,
    systemPrompt: agent.system_prompt,
    promptTemplate: agent.prompt_template,
    inputSchema: JSON.stringify(agent.input_schema, null, 2),
    outputSchema: JSON.stringify(agent.output_schema, null, 2),
    outputMode: agent.output_mode,
    targetMode: agent.model_profile_id ? "model" : "route",
    targetId: String(agent.model_profile_id ?? agent.route_id ?? ""),
    temperature: String(agent.parameters.temperature),
    topP: agent.parameters.top_p === null ? "" : String(agent.parameters.top_p),
    maxTokens: String(agent.parameters.max_tokens),
    scenario: agent.parameters.scenario,
    capabilities: agent.required_capabilities.join(", "),
    allowDegradation: agent.allow_degradation,
    timeoutSeconds: String(agent.timeout_seconds),
    retryCount: String(agent.retry_count),
    budgetTokens: agent.budget.max_tokens === null ? "" : String(agent.budget.max_tokens),
    budgetCost: agent.budget.max_cost === null ? "" : String(agent.budget.max_cost),
    currency: agent.budget.currency,
    enabled: agent.enabled
  };
}

function agentPayload(form: AgentForm, projectId: number): AgentDefinitionInput {
  const target = positiveInteger(form.targetId, "模型或 Route");
  return {
    project_id: projectId,
    name: required(form.name, "名称"),
    agent_type: required(form.agentType, "类型"),
    system_prompt: form.systemPrompt,
    prompt_template: required(form.promptTemplate, "任务提示词模板"),
    input_schema: parseObject(form.inputSchema, "输入 JSON Schema"),
    output_schema: parseObject(form.outputSchema, "输出 JSON Schema"),
    output_mode: form.outputMode,
    model_profile_id: form.targetMode === "model" ? target : null,
    route_id: form.targetMode === "route" ? target : null,
    parameters: {
      temperature: boundedNumber(form.temperature, "Temperature", 0, 2),
      top_p: optionalBoundedNumber(form.topP, "Top P", 0, 1, false),
      max_tokens: positiveInteger(form.maxTokens, "最大输出 tokens"),
      scenario: form.scenario
    },
    required_capabilities: form.capabilities.split(",").map((value) => value.trim().toLowerCase()).filter(Boolean),
    allow_degradation: form.allowDegradation,
    timeout_seconds: boundedNumber(form.timeoutSeconds, "超时", 1, 3600),
    retry_count: integerInRange(form.retryCount, "重试次数", 0, 5),
    budget: {
      max_tokens: optionalPositiveInteger(form.budgetTokens, "预算 tokens"),
      max_cost: optionalBoundedNumber(form.budgetCost, "预算成本", 0, Number.MAX_SAFE_INTEGER, true),
      currency: required(form.currency.toUpperCase(), "币种")
    },
    enabled: form.enabled
  };
}

function initialWorkflowGraph(agent?: AgentDefinition): { nodes: WorkflowNode[]; edges: WorkflowEdge[] } {
  const nodes: WorkflowNode[] = [
    { key: "start", type: "start", label: "Start", position_x: 80, position_y: 170, config: {} },
    ...(agent ? [{ key: "agent_1", type: "agent" as const, label: agent.name, position_x: 340, position_y: 170, config: { agent_id: agent.id } }] : []),
    { key: "output", type: "output", label: "Output", position_x: agent ? 600 : 340, position_y: 170, config: {} }
  ];
  const edges: WorkflowEdge[] = agent
    ? [
      { key: "e_start_agent", source: "start", target: "agent_1", source_handle: null, target_handle: null },
      { key: "e_agent_output", source: "agent_1", target: "output", source_handle: null, target_handle: null }
    ]
    : [{ key: "e_start_output", source: "start", target: "output", source_handle: null, target_handle: null }];
  return { nodes, edges };
}

function workflowFromSnapshot(value: Record<string, unknown> | undefined): Pick<Workflow, "nodes" | "edges"> | null {
  const workflow = value?.workflow;
  if (!workflow || typeof workflow !== "object" || Array.isArray(workflow)) return null;
  const record = workflow as Record<string, unknown>;
  if (!Array.isArray(record.nodes) || !Array.isArray(record.edges)) return null;
  return { nodes: record.nodes as WorkflowNode[], edges: record.edges as WorkflowEdge[] };
}

function RunStatusIcon({ status }: { status: WorkflowRunStatus }) {
  if (status === "completed") return <CheckCircle2 size={16} />;
  if (status === "failed") return <CircleAlert size={16} />;
  if (status === "cancelled" || status === "interrupted") return <Ban size={16} />;
  if (status === "waiting_approval") return <Clock3 size={16} />;
  return <Activity size={16} />;
}

function mergeEvents(first: WorkflowRunEvent[], second: WorkflowRunEvent[]): WorkflowRunEvent[] {
  const values = new Map<number, WorkflowRunEvent>();
  for (const event of [...first, ...second]) values.set(event.sequence, event);
  return [...values.values()].sort((left, right) => left.sequence - right.sequence);
}

function errorMessage(error: unknown, fallback: string): string {
  if (error instanceof ApiError) {
    const message = error.message.replace(/^"|"$/g, "");
    return message || fallback;
  }
  return error instanceof Error ? error.message : fallback;
}

function parseObject(text: string, label: string): Record<string, unknown> {
  let value: unknown;
  try { value = JSON.parse(text); } catch { throw new Error(`${label} 不是有效 JSON`); }
  if (!value || typeof value !== "object" || Array.isArray(value)) throw new Error(`${label} 必须是 JSON 对象`);
  return value as Record<string, unknown>;
}

function required(value: string, label: string): string {
  const result = value.trim();
  if (!result) throw new Error(`${label}不能为空`);
  return result;
}

function boundedNumber(value: string, label: string, min: number, max: number): number {
  const number = Number(value);
  if (!Number.isFinite(number) || number < min || number > max) throw new Error(`${label}必须在 ${min} 到 ${max} 之间`);
  return number;
}

function optionalBoundedNumber(value: string, label: string, min: number, max: number, inclusiveMin: boolean): number | null {
  if (!value.trim()) return null;
  const number = Number(value);
  const below = inclusiveMin ? number < min : number <= min;
  if (!Number.isFinite(number) || below || number > max) throw new Error(`${label}超出允许范围`);
  return number;
}

function positiveInteger(value: string, label: string): number {
  return integerInRange(value, label, 1, Number.MAX_SAFE_INTEGER);
}

function optionalPositiveInteger(value: string, label: string): number | null {
  return value.trim() ? positiveInteger(value, label) : null;
}

function integerInRange(value: string, label: string, min: number, max: number): number {
  const number = Number(value);
  if (!Number.isInteger(number) || number < min || number > max) throw new Error(`${label}必须是 ${min} 到 ${max} 之间的整数`);
  return number;
}

function isActiveStatus(status: WorkflowRunStatus | undefined): boolean {
  return status === "pending" || status === "running" || status === "waiting_approval";
}

function runStatusLabel(status: WorkflowRunStatus): string {
  return ({ pending: "等待", running: "运行中", waiting_approval: "等待审批", completed: "完成", failed: "失败", cancelled: "已取消", interrupted: "已中断" } as Record<WorkflowRunStatus, string>)[status];
}

function nodeStatusLabel(status: NodeRunStatus): string {
  return ({ pending: "等待", ready: "就绪", running: "运行中", waiting_approval: "等待审批", completed: "完成", failed: "失败", skipped: "跳过", cancelled: "已取消" } as Record<NodeRunStatus, string>)[status];
}

function sourceModeLabel(value: string): string {
  return ({ retry_node: "仅重试节点", retry_descendants: "重试节点及下游", clone_from_node: "从节点克隆" } as Record<string, string>)[value] ?? value;
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat("zh-CN", { month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }).format(new Date(value));
}

function durationText(start: string | null, end: string | null): string {
  if (!start) return "未开始";
  const milliseconds = Math.max(0, new Date(end ?? Date.now()).getTime() - new Date(start).getTime());
  if (milliseconds < 1_000) return `${milliseconds} ms`;
  if (milliseconds < 60_000) return `${(milliseconds / 1_000).toFixed(1)} s`;
  return `${Math.floor(milliseconds / 60_000)}m ${Math.floor((milliseconds % 60_000) / 1_000)}s`;
}

function runCost(nodes: NodeRun[]): string {
  const attempts = nodes.flatMap((node) => node.attempts);
  if (!attempts.length || attempts.some((attempt) => !attempt.cost_known)) return "未知";
  const currencies = new Set(attempts.map((attempt) => attempt.currency));
  if (currencies.size !== 1) return "多币种";
  return `${attempts[0].currency} ${attempts.reduce((sum, attempt) => sum + (attempt.cost ?? 0), 0).toFixed(6)}`;
}

function jsonText(value: unknown): string {
  if (value === null || value === undefined) return "—";
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

function compactJson(value: unknown): string {
  const text = typeof value === "string" ? value : JSON.stringify(value);
  return text.length > 280 ? `${text.slice(0, 277)}...` : text;
}

function downloadJson(filename: string, value: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: "application/json" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}

function safeFilename(value: string): string {
  return value.replace(/[<>:"/\\|?*\u0000-\u001f]/g, "_").trim() || "workflow";
}

function delay(milliseconds: number): Promise<void> {
  return new Promise((resolve) => window.setTimeout(resolve, milliseconds));
}
