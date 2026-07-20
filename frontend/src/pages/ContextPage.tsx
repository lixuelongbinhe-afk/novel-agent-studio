import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  BookMarked,
  Braces,
  Check,
  ChevronRight,
  Database,
  FileText,
  Filter,
  Gauge,
  ListFilter,
  Lock,
  Pin,
  RefreshCw,
  Search,
  Server,
  Settings2,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Unlock,
  X
} from "lucide-react";
import {
  api,
  type ContentClassificationValue,
  type ContextBuild,
  type ContextBuildRequest,
  type ContextItem
} from "../api/client";
import { EmptyState } from "../components/EmptyState";
import { ErrorNotice } from "../components/ErrorNotice";
import { FormField } from "../components/FormField";
import { useUiStore } from "../stores/ui";
import { ContextMemoryPanel } from "./ContextMemoryPanel";
import { ContextPolicyPanel } from "./ContextPolicyPanel";
import { CLASSIFICATION_LABELS, contextErrorMessage, SECTION_LABELS } from "./contextUi";

type ContextTab = "preview" | "memory" | "policies";
type ItemFilter = "all" | "included" | "excluded";
type BuildControls = {
  excludedKeys: string[];
  lockedKeys: string[];
  priorities: Record<string, number>;
};

const EXCLUDED_LABELS: Record<string, string> = {
  temporary_exclusion: "临时排除",
  provider_data_boundary: "数据边界",
  below_relevance_threshold: "相关性不足",
  result_limit: "结果上限",
  budget_low_relevance: "Token 预算"
};

const CLASSIFIABLE_SOURCES = new Set([
  "chapter",
  "scene",
  "chapter_summary",
  "scene_state",
  "chapter_entity_link",
  "entity",
  "relation",
  "timeline",
  "foreshadow",
  "style_guide"
]);

export function ContextPage() {
  const selectedProjectId = useUiStore((state) => state.selectedProjectId);
  const selectedChapterId = useUiStore((state) => state.selectedChapterId);
  const selectedSceneId = useUiStore((state) => state.selectedSceneId);
  const [activeTab, setActiveTab] = useState<ContextTab>("preview");
  const [pageError, setPageError] = useState("");
  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: () => api.listProjects() });
  const projectId = selectedProjectId ?? projectsQuery.data?.[0]?.id;
  const treeQuery = useQuery({
    queryKey: ["project-tree", projectId],
    queryFn: () => api.tree(projectId!),
    enabled: Boolean(projectId)
  });
  const agentsQuery = useQuery({
    queryKey: ["agents", projectId],
    queryFn: () => api.listAgents(projectId!),
    enabled: Boolean(projectId)
  });
  const policiesQuery = useQuery({
    queryKey: ["context-policies", projectId],
    queryFn: () => api.listContextPolicies(projectId!),
    enabled: Boolean(projectId)
  });

  if (!projectId && projectsQuery.isLoading) {
    return <section className="page-stack context-page"><div className="context-loading">正在载入项目...</div></section>;
  }

  if (!projectId) {
    return (
      <section className="page-stack context-page">
        <EmptyState
          icon={Database}
          title="还没有项目"
          description="先创建小说项目，再建立上下文记忆与检索策略。"
        />
      </section>
    );
  }

  const tree = treeQuery.data;
  const agents = agentsQuery.data ?? [];
  const policies = policiesQuery.data ?? [];

  return (
    <section className="page-stack context-page">
      <header className="page-header context-page-header">
        <div>
          <span className="eyebrow">Context memory</span>
          <h1>上下文记忆</h1>
          <p>检索结果、Token 预算与 Provider 数据边界。</p>
        </div>
        <div className="context-head-stats" aria-label="上下文概览">
          <span><BookMarked size={15} /><strong>{tree?.chapters.length ?? 0}</strong> 章节</span>
          <span><Sparkles size={15} /><strong>{agents.length}</strong> Agent</span>
          <span><ShieldCheck size={15} /><strong>{policies.length}</strong> 策略</span>
        </div>
      </header>

      <nav className="segmented-tabs" aria-label="上下文记忆视图">
        <button className={activeTab === "preview" ? "active" : ""} type="button" onClick={() => setActiveTab("preview")}>
          <Search size={16} />上下文预览
        </button>
        <button className={activeTab === "memory" ? "active" : ""} type="button" onClick={() => setActiveTab("memory")}>
          <Database size={16} />小说记忆
        </button>
        <button className={activeTab === "policies" ? "active" : ""} type="button" onClick={() => setActiveTab("policies")}>
          <Settings2 size={16} />策略与边界
        </button>
      </nav>

      {pageError ? <ErrorNotice message={pageError} onDismiss={() => setPageError("")} /> : null}
      {activeTab === "preview" ? (
        <ContextPreview
          projectId={projectId!}
          tree={tree}
          agents={agents}
          policies={policies}
          initialChapterId={selectedChapterId}
          initialSceneId={selectedSceneId}
          loading={treeQuery.isLoading || agentsQuery.isLoading || policiesQuery.isLoading}
          onError={setPageError}
        />
      ) : activeTab === "memory" ? (
        <ContextMemoryPanel projectId={projectId!} tree={tree} onError={setPageError} />
      ) : (
        <ContextPolicyPanel projectId={projectId!} onError={setPageError} />
      )}
    </section>
  );
}

function ContextPreview({
  projectId,
  tree,
  agents,
  policies,
  initialChapterId,
  initialSceneId,
  loading,
  onError
}: {
  projectId: number;
  tree: Awaited<ReturnType<typeof api.tree>> | undefined;
  agents: Awaited<ReturnType<typeof api.listAgents>>;
  policies: Awaited<ReturnType<typeof api.listContextPolicies>>;
  initialChapterId: number | null;
  initialSceneId: number | null;
  loading: boolean;
  onError: (message: string) => void;
}) {
  const queryClient = useQueryClient();
  const [chapterId, setChapterId] = useState(0);
  const [sceneId, setSceneId] = useState(0);
  const [agentId, setAgentId] = useState(0);
  const [policyId, setPolicyId] = useState(0);
  const [query, setQuery] = useState("续写当前场景，保持人物状态、世界规则、时间线和伏笔一致。");
  const [budget, setBudget] = useState("6000");
  const [reservedOutput, setReservedOutput] = useState("1024");
  const [workflowInput, setWorkflowInput] = useState("{}");
  const [upstreamOutputs, setUpstreamOutputs] = useState("{}");
  const [controls, setControls] = useState<BuildControls>({
    excludedKeys: [],
    lockedKeys: [],
    priorities: {}
  });
  const [result, setResult] = useState<ContextBuild | null>(null);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [filter, setFilter] = useState<ItemFilter>("all");

  const pinsQuery = useQuery({
    queryKey: ["context-pins", projectId],
    queryFn: () => api.listContextPins(projectId)
  });
  const classificationsQuery = useQuery({
    queryKey: ["content-classifications", projectId],
    queryFn: () => api.listContentClassifications(projectId)
  });
  const scenes = useMemo(
    () => tree?.scenes.filter((scene) => !chapterId || scene.chapter_id === chapterId) ?? [],
    [chapterId, tree?.scenes]
  );

  useEffect(() => {
    if (!tree?.chapters.length) return;
    const next = tree.chapters.some((item) => item.id === initialChapterId)
      ? initialChapterId!
      : tree.chapters[0].id;
    setChapterId((current) => current || next);
  }, [initialChapterId, tree?.chapters]);

  useEffect(() => {
    if (!scenes.length) {
      setSceneId(0);
      return;
    }
    setSceneId((current) => {
      if (scenes.some((item) => item.id === current)) return current;
      if (scenes.some((item) => item.id === initialSceneId)) return initialSceneId!;
      return scenes[0].id;
    });
  }, [initialSceneId, scenes]);

  useEffect(() => {
    if (agents.length && !agents.some((item) => item.id === agentId)) setAgentId(agents[0].id);
  }, [agentId, agents]);

  useEffect(() => {
    if (!policies.length) return;
    const selected = policies.find((item) => item.id === policyId) ?? policies[0];
    if (selected.id !== policyId) setPolicyId(selected.id);
    setBudget(String(selected.token_budget));
  }, [policies, policyId]);

  const buildMutation = useMutation({
    mutationFn: (nextControls: BuildControls) => {
      let workflowValue: Record<string, unknown>;
      let upstreamValue: Record<string, unknown>;
      try {
        workflowValue = parseJsonObject(workflowInput);
        upstreamValue = parseJsonObject(upstreamOutputs);
      } catch (error) {
        throw new Error(error instanceof Error ? error.message : "运行输入 JSON 无效");
      }
      const payload: ContextBuildRequest = {
        project_id: projectId,
        chapter_id: chapterId || null,
        scene_id: sceneId || null,
        agent_id: agentId || null,
        model_profile_id: null,
        policy_id: policyId || null,
        workflow_run_id: null,
        query: query.trim(),
        workflow_input: { task: query.trim(), ...workflowValue },
        upstream_outputs: upstreamValue,
        model_context_window: null,
        reserved_output_tokens: Math.max(0, Number(reservedOutput) || 0),
        token_budget_override: Math.max(128, Number(budget) || 128),
        excluded_keys: nextControls.excludedKeys,
        locked_keys: nextControls.lockedKeys,
        priority_overrides: nextControls.priorities,
        persist_snapshot: true
      };
      return api.buildContext(payload);
    },
    onSuccess: (value) => {
      setResult(value);
      const keys = [...value.included, ...value.excluded].map((item) => item.key);
      setSelectedKey((current) => (current && keys.includes(current) ? current : keys[0] ?? null));
      onError("");
    },
    onError: (error) => onError(contextErrorMessage(error))
  });

  const run = (next = controls) => {
    setControls(next);
    buildMutation.mutate(next);
  };

  const canRetrieve =
    !loading && Boolean(query.trim()) && policyId > 0 && (agents.length === 0 || agentId > 0);

  const allItems = result ? [...result.included, ...result.excluded] : [];
  const visibleItems = allItems.filter((item) =>
    filter === "all" ? true : filter === "included" ? item.included : !item.included
  );
  const selected = allItems.find((item) => item.key === selectedKey) ?? null;
  const existingPin = selected
    ? pinsQuery.data?.find(
        (item) => item.source_type === selected.source_type && item.source_id === selected.source_id
      )
    : undefined;

  const pinMutation = useMutation({
    mutationFn: async (item: ContextItem) => {
      const payload = {
        project_id: projectId,
        source_type: item.source_type,
        source_id: item.source_id,
        label: item.title.replace(/^Pin · /, ""),
        content_override: existingPin?.content_override ?? "",
        priority: controls.priorities[item.key] ?? item.priority,
        required: item.required,
        enabled: true
      };
      return existingPin
        ? api.updateContextPin(existingPin, payload)
        : api.createContextPin(payload);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["context-pins", projectId] });
      run();
    },
    onError: (error) => onError(contextErrorMessage(error))
  });

  const classificationMutation = useMutation({
    mutationFn: async ({ item, classification }: { item: ContextItem; classification: ContentClassificationValue }) => {
      const existing = classificationsQuery.data?.find(
        (value) => value.source_type === item.source_type && value.source_id === item.source_id
      );
      const payload = {
        project_id: projectId,
        source_type: item.source_type,
        source_id: item.source_id,
        classification,
        reason: "从上下文预览调整"
      };
      return existing
        ? api.updateContentClassification(existing, payload)
        : api.createContentClassification(payload);
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["content-classifications", projectId] });
      run();
    },
    onError: (error) => onError(contextErrorMessage(error))
  });

  if (loading) return <div className="context-loading">正在载入上下文资源...</div>;

  return (
    <div className="context-preview-workbench">
      <section className="context-query-band" aria-label="上下文检索参数">
        <div className="context-query-main">
          <FormField label="任务">
            <textarea rows={3} value={query} onChange={(event) => setQuery(event.target.value)} />
          </FormField>
          <button
            className="primary-button context-retrieve-button"
            type="button"
            disabled={!canRetrieve || buildMutation.isPending}
            onClick={() => run()}
          >
            <Search size={17} />{buildMutation.isPending ? "检索中" : "检索上下文"}
          </button>
        </div>
        <div className="context-select-grid">
          <FormField label="章节">
            <select value={chapterId} onChange={(event) => setChapterId(Number(event.target.value))}>
              <option value={0}>不指定</option>
              {tree?.chapters.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
            </select>
          </FormField>
          <FormField label="场景">
            <select value={sceneId} onChange={(event) => setSceneId(Number(event.target.value))}>
              <option value={0}>不指定</option>
              {scenes.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}
            </select>
          </FormField>
          <FormField label="Agent">
            <select value={agentId} onChange={(event) => setAgentId(Number(event.target.value))}>
              <option value={0}>不指定</option>
              {agents.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </FormField>
          <FormField label="Context Policy">
            <select value={policyId} onChange={(event) => setPolicyId(Number(event.target.value))}>
              {policies.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
            </select>
          </FormField>
          <FormField label="输入 Token 预算">
            <input type="number" min={128} value={budget} onChange={(event) => setBudget(event.target.value)} />
          </FormField>
          <FormField label="预留输出 Token">
            <input type="number" min={0} value={reservedOutput} onChange={(event) => setReservedOutput(event.target.value)} />
          </FormField>
        </div>
        <details className="context-json-inputs">
          <summary><Braces size={15} />运行输入</summary>
          <div>
            <FormField label="工作流输入 JSON"><textarea rows={5} value={workflowInput} onChange={(event) => setWorkflowInput(event.target.value)} /></FormField>
            <FormField label="上游输出 JSON"><textarea rows={5} value={upstreamOutputs} onChange={(event) => setUpstreamOutputs(event.target.value)} /></FormField>
          </div>
        </details>
      </section>

      {result ? (
        <>
          {result.blocked ? (
            <div className="context-conflict-banner" role="alert">
              <AlertTriangle size={18} />
              <div><strong>上下文已阻止</strong>{result.conflicts.map((item) => <span key={item}>{item}</span>)}</div>
            </div>
          ) : result.conflicts.length ? (
            <div className="context-warning-banner">
              <AlertTriangle size={17} />
              <span>{result.conflicts.join("；")}</span>
            </div>
          ) : null}
          <section className="context-metric-strip" aria-label="上下文构建结果">
            <div><Gauge size={17} /><span>Token</span><strong>{result.included_tokens.toLocaleString()} / {result.token_budget.toLocaleString()}</strong></div>
            <div><Check size={17} /><span>包含</span><strong>{result.included.length}</strong></div>
            <div><Filter size={17} /><span>排除</span><strong>{result.excluded.length}</strong></div>
            <div><ShieldCheck size={17} /><span>边界排除</span><strong>{result.boundary.excluded_count}</strong></div>
            <div><Server size={17} /><span>目标 Provider</span><strong>{result.target_providers.length}</strong></div>
            <div><FileText size={17} /><span>快照</span><strong>{result.id ? `#${result.id}` : "未保存"}</strong></div>
          </section>
          <section className="context-provider-band">
            <strong>目标 Provider</strong>
            <div>
              {result.target_providers.length ? result.target_providers.map((provider) => (
                <span key={provider.provider_account_id} title={provider.allowed_classifications.join("、")}>
                  <Server size={13} />{provider.provider_name}
                  <small>{provider.policy_source === "stored" ? "显式策略" : provider.policy_source === "local_default" ? "本机默认" : "远程保守默认"}</small>
                </span>
              )) : <span className="muted">未解析目标 Provider</span>}
            </div>
            <code title={result.build_hash}>{result.build_hash.slice(0, 16)}</code>
          </section>
          <section className="context-result-grid">
            <aside className="context-source-panel">
              <header>
                <div><strong>来源</strong><span>{visibleItems.length} / {allItems.length}</span></div>
                <div className="compact-segments" aria-label="来源筛选">
                  <button className={filter === "all" ? "active" : ""} type="button" title="全部" onClick={() => setFilter("all")}><ListFilter size={14} /></button>
                  <button className={filter === "included" ? "active" : ""} type="button" title="仅包含" onClick={() => setFilter("included")}><Check size={14} /></button>
                  <button className={filter === "excluded" ? "active" : ""} type="button" title="仅排除" onClick={() => setFilter("excluded")}><X size={14} /></button>
                </div>
              </header>
              <div className="context-source-list">
                {visibleItems.map((item) => (
                  <button
                    key={item.key}
                    type="button"
                    className={`${selectedKey === item.key ? "selected" : ""} ${item.included ? "included" : "excluded"}`}
                    onClick={() => setSelectedKey(item.key)}
                  >
                    <span className="source-state">{item.included ? <Check size={13} /> : <X size={13} />}</span>
                    <span className="source-copy">
                      <strong>{item.title}</strong>
                      <small>{SECTION_LABELS[item.section] ?? item.section} · {item.token_estimate} Token</small>
                    </span>
                    <span className="source-flags">
                      {item.pinned ? <Pin size={12} /> : null}
                      {item.locked ? <Lock size={12} /> : null}
                      {item.truncated ? <span>截</span> : null}
                    </span>
                  </button>
                ))}
              </div>
            </aside>
            <article className="context-text-panel">
              <header>
                <div><strong>实际上下文</strong><span>{result.included_tokens.toLocaleString()} Token</span></div>
                <button className="icon-button ghost" type="button" title="按当前控制重新检索" onClick={() => run()} disabled={buildMutation.isPending}><RefreshCw size={16} /></button>
              </header>
              <pre>{result.context_text || "当前策略未包含任何上下文。"}</pre>
            </article>
            <aside className="context-item-inspector">
              {selected ? (
                <>
                  <header><div><span>{SECTION_LABELS[selected.section] ?? selected.section}</span><strong>{selected.title}</strong></div><ChevronRight size={16} /></header>
                  <div className="context-inspector-scroll">
                    <div className="context-item-status">
                      <span className={`classification classification-${classificationClass(selected.classification)}`}>{CLASSIFICATION_LABELS[selected.classification]}</span>
                      <span>{selected.included ? "已包含" : EXCLUDED_LABELS[selected.excluded_reason ?? ""] ?? "已排除"}</span>
                    </div>
                    <dl className="context-item-metrics">
                      <div><dt>相关性</dt><dd>{Math.round(selected.relevance * 100)}%</dd></div>
                      <div><dt>Token</dt><dd>{selected.token_estimate}</dd></div>
                      <div><dt>优先级</dt><dd>{controls.priorities[selected.key] ?? selected.priority}</dd></div>
                      <div><dt>来源</dt><dd>{selected.source_type} #{selected.source_id}</dd></div>
                    </dl>
                    <section className="context-reasons"><strong>选择原因</strong>{selected.reasons.map((reason) => <p key={reason}><ChevronRight size={12} />{reason}</p>)}</section>
                    <FormField label="临时优先级">
                      <input
                        type="number"
                        min={0}
                        max={1000}
                        value={controls.priorities[selected.key] ?? selected.priority}
                        onChange={(event) => setControls((current) => ({
                          ...current,
                          priorities: { ...current.priorities, [selected.key]: Number(event.target.value) }
                        }))}
                      />
                    </FormField>
                    {CLASSIFIABLE_SOURCES.has(selected.source_type) ? (
                      <FormField label="数据分类">
                        <select
                          value={selected.classification}
                          disabled={classificationMutation.isPending}
                          onChange={(event) => classificationMutation.mutate({
                            item: selected,
                            classification: event.target.value as ContentClassificationValue
                          })}
                        >
                          {(Object.keys(CLASSIFICATION_LABELS) as ContentClassificationValue[]).map((value) => <option key={value} value={value}>{CLASSIFICATION_LABELS[value]}</option>)}
                        </select>
                      </FormField>
                    ) : null}
                    <div className="context-item-actions">
                      <button
                        className="secondary-button compact"
                        type="button"
                        onClick={() => {
                          const locked = controls.lockedKeys.includes(selected.key)
                          run({
                            ...controls,
                            lockedKeys: locked
                              ? controls.lockedKeys.filter((key) => key !== selected.key)
                              : [...controls.lockedKeys, selected.key]
                          });
                        }}
                      >
                        {controls.lockedKeys.includes(selected.key) ? <Unlock size={14} /> : <Lock size={14} />}
                        {controls.lockedKeys.includes(selected.key) ? "解除锁定" : "锁定"}
                      </button>
                      <button
                        className="secondary-button compact"
                        type="button"
                        disabled={selected.required && selected.included}
                        onClick={() => {
                          const excluded = controls.excludedKeys.includes(selected.key);
                          run({
                            ...controls,
                            excludedKeys: excluded
                              ? controls.excludedKeys.filter((key) => key !== selected.key)
                              : [...controls.excludedKeys, selected.key]
                          });
                        }}
                      >
                        {controls.excludedKeys.includes(selected.key) ? <Check size={14} /> : <X size={14} />}
                        {controls.excludedKeys.includes(selected.key) ? "恢复" : "临时排除"}
                      </button>
                      {CLASSIFIABLE_SOURCES.has(selected.source_type) ? (
                        <button className="secondary-button compact" type="button" disabled={pinMutation.isPending} onClick={() => pinMutation.mutate(selected)}>
                          {existingPin ? <BookMarked size={14} /> : <Pin size={14} />}{existingPin ? "更新 Pin" : "Pin"}
                        </button>
                      ) : null}
                      <button className="primary-button compact" type="button" onClick={() => run()}><RefreshCw size={14} />应用并重检</button>
                    </div>
                    <section className="context-item-content"><strong>来源内容</strong><pre>{selected.content}</pre></section>
                  </div>
                </>
              ) : (
                <EmptyState icon={SlidersHorizontal} title="选择一个来源" description="" />
              )}
            </aside>
          </section>
        </>
      ) : (
        <EmptyState
          icon={Search}
          title="尚未检索"
          description=""
          action={<button className="primary-button" type="button" onClick={() => run()} disabled={!canRetrieve || buildMutation.isPending}><Search size={16} />检索上下文</button>}
        />
      )}
    </div>
  );
}

function parseJsonObject(value: string): Record<string, unknown> {
  const parsed = JSON.parse(value) as unknown;
  if (!parsed || Array.isArray(parsed) || typeof parsed !== "object") {
    throw new Error("运行输入必须是 JSON 对象");
  }
  return parsed as Record<string, unknown>;
}

function classificationClass(value: ContentClassificationValue): string {
  return value.replace(/ /g, "-");
}
